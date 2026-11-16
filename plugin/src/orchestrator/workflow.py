"""Persisted Route A contracts; host dispatch remains the native coordinator's job."""
from __future__ import annotations

import json
import math
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any

from orchestrator.artifacts import capture_content, capture_evidence, digest, evidence_current, recapture
from orchestrator.db import Database, NotFoundError

MAX_REWORKS = 3
STEP_ACTIONS = frozenset({'dispatch', 'accept', 'submit', 'challenge', 'start_rework',
                          'begin_integrated_review', 'complete', 'handoff', 'ack_handoff',
                          'reconcile', 'observe', 'reverify'})


class WorkflowConflictError(ValueError):
    """The persisted revision, approved artifact or required gate is not current."""


_SQL = '''
CREATE TABLE IF NOT EXISTS workflow_steps (
 id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(id), step_key TEXT NOT NULL,
 kind TEXT NOT NULL, owner TEXT NOT NULL, verifier TEXT NOT NULL,
 owner_agent_id TEXT NOT NULL, verifier_agent_id TEXT NOT NULL,
 workspace_id TEXT REFERENCES workspaces(id), packet_version INTEGER NOT NULL,
 dependencies_json TEXT NOT NULL, criteria_json TEXT NOT NULL,
 state TEXT NOT NULL, attempt INTEGER NOT NULL DEFAULT 0, revision INTEGER NOT NULL DEFAULT 0,
 contract_revision INTEGER NOT NULL DEFAULT 0,
 checkpoint_json TEXT NOT NULL DEFAULT '{}', handoff_required INTEGER NOT NULL DEFAULT 0,
 created_at REAL NOT NULL, updated_at REAL NOT NULL, UNIQUE(run_id,step_key)
);
CREATE TABLE IF NOT EXISTS workflow_artifacts (
 id TEXT PRIMARY KEY, step_id TEXT NOT NULL REFERENCES workflow_steps(id),
 scope TEXT NOT NULL, packet_version INTEGER NOT NULL, attempt INTEGER NOT NULL,
 descriptor_json TEXT NOT NULL, digest TEXT NOT NULL, created_at REAL NOT NULL,
 contract_revision INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS verification_records (
 id TEXT PRIMARY KEY, step_id TEXT NOT NULL REFERENCES workflow_steps(id),
 artifact_id TEXT NOT NULL REFERENCES workflow_artifacts(id), key TEXT NOT NULL,
 packet_version INTEGER NOT NULL, attempt INTEGER NOT NULL,
 verifier TEXT NOT NULL, verifier_agent_id TEXT NOT NULL, verdict TEXT NOT NULL,
 criteria_results_json TEXT NOT NULL, evidence_manifest_json TEXT NOT NULL,
 request_digest TEXT NOT NULL, created_at REAL NOT NULL, UNIQUE(step_id,key)
);
CREATE TABLE IF NOT EXISTS inbox_messages (
 id TEXT PRIMARY KEY, board_seq INTEGER NOT NULL UNIQUE REFERENCES board_messages(seq),
 run_id TEXT NOT NULL REFERENCES runs(id), sender TEXT NOT NULL, recipient TEXT NOT NULL,
 key TEXT NOT NULL, kind TEXT NOT NULL CHECK(kind IN ('task','rework','review','result','notice')),
 summary TEXT NOT NULL, body_md TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','accepted','completed','failed','cancelled')),
 receipts_json TEXT NOT NULL DEFAULT '[]', recipient_agent_id TEXT,
 step_id TEXT REFERENCES workflow_steps(id), UNIQUE(run_id,sender,key),
 FOREIGN KEY(run_id,recipient) REFERENCES workers(run_id,name)
);
CREATE INDEX IF NOT EXISTS idx_workflow_run ON workflow_steps(run_id);
CREATE INDEX IF NOT EXISTS idx_verification_step ON verification_records(step_id,created_at);
CREATE INDEX IF NOT EXISTS idx_inbox_pending ON inbox_messages(run_id,recipient,board_seq)
 WHERE status IN ('pending','accepted');
'''


def install_schema(conn: sqlite3.Connection) -> None:
    for statement in _SQL.split(';'):
        if statement.strip():
            conn.execute(statement)


def _id(prefix: str) -> str:
    return prefix + '_' + uuid.uuid4().hex[:16]


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def _decode(row: sqlite3.Row) -> dict[str, Any]:
    result = dict(row)
    for key in tuple(result):
        if key.endswith('_json'):
            result[key[:-5]] = json.loads(result.pop(key))
    return result


class WorkflowService:
    def __init__(self, db: Database):
        self.db = db

    def _run(self, run_id: str, *, active: bool = True) -> dict:
        run = self.db.get_run(run_id)
        if run is None:
            raise NotFoundError(f'Run not found: {run_id}')
        if run['workflow_version'] != 1:
            raise WorkflowConflictError('Legacy run has no structured workflow; create a new continuation run')
        if active and run['status'] != 'active':
            raise WorkflowConflictError('Workflow run is not active')
        return run

    def get_step(self, step_id: str) -> dict:
        with self.db.read_connection() as conn:
            row = conn.execute('SELECT * FROM workflow_steps WHERE id=?', (step_id,)).fetchone()
        if row is None:
            raise NotFoundError(f'Workflow step not found: {step_id}')
        return _decode(row)

    def _worker(self, run_id: str, name: str) -> dict:
        worker = self.db.get_worker_by_name(run_id, name)
        if worker is None:
            raise NotFoundError(f'Worker not found: {name}')
        if not worker['agent_id']:
            raise WorkflowConflictError(f'Worker {name} has no actual native agent binding')
        return worker

    def _bindings(self, step: dict, *, dispatch: bool = False) -> None:
        owner = self._worker(step['run_id'], step['owner'])
        verifier = self._worker(step['run_id'], step['verifier'])
        if owner['agent_id'] != step['owner_agent_id'] or verifier['agent_id'] != step['verifier_agent_id']:
            raise WorkflowConflictError('Workflow owner/verifier binding changed; explicitly revise the step')
        if owner['agent_id'] == verifier['agent_id'] or owner['name'] == verifier['name']:
            raise WorkflowConflictError('Author and verifier must be different native agents')
        if dispatch and (owner['status'] in {'stopped', 'killed'} or verifier['status'] in {'stopped', 'killed'}):
            raise WorkflowConflictError('A paused or terminated worker cannot be dispatched automatically')
        if any(x['status'] == 'pending' for x in self.db.list_worker_controls(step['run_id'])):
            raise WorkflowConflictError('Acknowledge pending controls before workflow progress')

    def _packet(self, step: dict) -> dict:
        packet = self.db.get_task_packet(step['run_id'])
        if packet is None or packet['version'] != step['packet_version']:
            raise WorkflowConflictError('Task packet changed; revise and reverify the step')
        if not set(step['criteria']) <= set(self._criteria(packet['acceptance_criteria'])):
            raise WorkflowConflictError('Step criteria no longer match the task packet')
        return packet

    @staticmethod
    def _criteria(criteria: Any) -> list[str]:
        if not isinstance(criteria, list) or not criteria or any(not isinstance(x, str) or not x.strip() for x in criteria):
            raise ValueError('Acceptance criteria must be a nonempty list of strings')
        if len(set(criteria)) != len(criteria):
            raise ValueError('Acceptance criteria must be unique')
        return criteria

    @staticmethod
    def _owned_paths(packet: dict, owner: str) -> list[str]:
        ownership = packet['file_ownership']
        paths = ownership.get(owner) if isinstance(ownership, dict) else None
        if not isinstance(paths, list) or not paths or any(not isinstance(p, str) or not p.strip() for p in paths):
            raise ValueError('Freeze a nonempty file ownership scope for the author before capturing a delivery')
        result = []
        for raw in paths:
            path = Path(raw)
            if path.is_absolute() or '..' in path.parts:
                raise ValueError('File ownership scope must stay inside the project')
            # Glob ownership requires its containing directory, including later additions.
            prefix = []
            for part in path.parts:
                if any(c in part for c in '*?['):
                    break
                prefix.append(part)
            result.append(Path(*prefix).as_posix() if prefix else '.')
        return sorted(set(result))

    @staticmethod
    def _covers(required: list[str], paths: list[str] | None) -> bool:
        if paths is None:
            return True
        scopes = [Path(p).as_posix() for p in paths]
        return all(any(need == scope or need.startswith(scope + '/') for scope in scopes) for need in required)

    def _candidate_for_integration(self, step: dict) -> dict:
        with self.db.read_connection() as conn:
            row = conn.execute('''SELECT a.* FROM workflow_artifacts a JOIN verification_records v ON a.id=v.artifact_id
                WHERE a.step_id=? AND a.scope='candidate' AND a.packet_version=? AND a.attempt=?
                AND a.contract_revision=? AND v.verdict='PASS' AND v.verifier_agent_id=? ORDER BY v.created_at DESC LIMIT 1''',
                (step['id'], step['packet_version'], step['attempt'], step['contract_revision'], step['verifier_agent_id'])).fetchone()
        if row is None:
            raise WorkflowConflictError('Integrated verification requires a matching approved candidate')
        return _decode(row)

    def _required_capture_scope(self, step: dict, scope: str) -> list[str]:
        packet = self._packet(step)
        required = self._owned_paths(packet, step['owner'])
        if scope == 'integrated':
            candidate = self._candidate_for_integration(step)['descriptor']
            if candidate['paths'] is None:
                return ['.']
            required.extend(candidate['paths'])
            required.extend(candidate['manifest'])
        return sorted(set(required))

    def _log(self, step: dict, action: str, detail: str = '') -> None:
        self.db.insert_board_message(step['run_id'], sender='workflow', channel='workflow',
                                     summary=f'{step["step_key"]}: {action}',
                                     body_md=detail or f'State {step["state"]}; attempt {step["attempt"]}; revision {step["revision"]}')

    def step_save(self, run_id: str, step_key: str, owner: str, verifier: str,
                  workspace_id: str | None = None, dependencies: list[str] | None = None,
                  kind: str = 'task', step_id: str | None = None, expected_revision: int | None = None,
                  handoff_required: bool = False, criteria: list[str] | None = None) -> dict:
        if not all(isinstance(v, str) and v.strip() for v in (run_id, step_key, owner, verifier)):
            raise ValueError('Workflow identifiers must be nonempty strings')
        if owner == verifier:
            raise ValueError('Author and verifier must be different workers')
        if kind not in {'task', 'milestone'} or type(handoff_required) is not bool:
            raise ValueError('Invalid workflow kind or handoff flag')
        dependencies = [] if dependencies is None else dependencies
        if not isinstance(dependencies, list) or any(not isinstance(x, str) for x in dependencies) or len(set(dependencies)) != len(dependencies):
            raise ValueError('Dependencies must be unique step IDs')
        with self.db.transaction() as conn:
            run = self._run(run_id)
            packet = self.db.get_task_packet(run_id)
            if packet is None:
                raise WorkflowConflictError('Freeze a task packet before creating workflow steps')
            packet_criteria = self._criteria(packet['acceptance_criteria'])
            criteria = self._criteria(criteria if criteria is not None else packet_criteria)
            if not set(criteria) <= set(packet_criteria):
                raise ValueError('Step criteria must come from the frozen task packet')
            self._owned_paths(packet, owner)
            author = self._worker(run_id, owner)
            gate = self._worker(run_id, verifier)
            if author['agent_id'] == gate['agent_id']:
                raise ValueError('Author and verifier must be different native agents')
            if workspace_id:
                workspace = self.db.get_workspace(workspace_id)
                if workspace is None or workspace['run_id'] != run_id or workspace['worker_name'] != owner:
                    raise ValueError('Workspace must belong to this run and author')
            old = self.get_step(step_id) if step_id else None
            if old and (old['run_id'] != run_id or old['step_key'] != step_key):
                raise ValueError('Cannot move or rename an existing workflow step')
            if old and old['revision'] != expected_revision:
                raise WorkflowConflictError('Workflow revision conflict')
            if old and old['state'] in {'integrating', 'reconciliation_required', 'needs_decision', 'rework'}:
                raise WorkflowConflictError('Resolve the current operation or rework before revising its contract')
            sid = step_id or _id('step')
            for dependency in dependencies:
                parent = self.get_step(dependency)
                if parent['run_id'] != run_id:
                    raise ValueError('Dependencies must belong to the same run')
                seen = set()
                pending = [dependency]
                while pending:
                    current = pending.pop()
                    if current == sid:
                        raise ValueError('Workflow dependency cycle')
                    if current not in seen:
                        seen.add(current)
                        pending.extend(self.get_step(current)['dependencies'])
            now = time.time()
            # Revise explicitly, retaining retry budget and old immutable verification records.
            handoff_required = handoff_required or (run['mode'] == 'teamwork' and kind == 'milestone')
            values = (kind, owner, verifier, author['agent_id'], gate['agent_id'], workspace_id,
                      packet['version'], _json(dependencies), _json(criteria), int(handoff_required), now)
            if old:
                checkpoint = old['checkpoint']
                checkpoint.pop('challenge', None)
                conn.execute('''UPDATE workflow_steps SET kind=?,owner=?,verifier=?,owner_agent_id=?,verifier_agent_id=?,
                                workspace_id=?,packet_version=?,dependencies_json=?,criteria_json=?,handoff_required=?,updated_at=?,
                                state='planned',checkpoint_json=?,revision=revision+1,contract_revision=contract_revision+1 WHERE id=?''',
                             (*values, _json(checkpoint), sid))
            else:
                conn.execute('''INSERT INTO workflow_steps
                    (id,run_id,step_key,kind,owner,verifier,owner_agent_id,verifier_agent_id,workspace_id,packet_version,
                     dependencies_json,criteria_json,handoff_required,updated_at,created_at,state)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'planned')''', (sid, run_id, step_key, *values, now))
            step = self.get_step(sid)
            self._log(step, 'saved')
            return step

    def _set(self, step: dict, state: str, checkpoint: dict | None = None, attempt: int | None = None) -> dict:
        with self.db.transaction() as conn:
            changed = conn.execute('''UPDATE workflow_steps SET state=?,checkpoint_json=?,attempt=?,
                revision=revision+1,updated_at=? WHERE id=? AND revision=?''',
                (state, _json(step['checkpoint'] if checkpoint is None else checkpoint),
                 step['attempt'] if attempt is None else attempt, time.time(), step['id'], step['revision']))
            if changed.rowcount != 1:
                raise WorkflowConflictError('Workflow revision conflict')
            updated = self.get_step(step['id'])
            self._log(updated, state)
            return updated

    def step_advance(self, step_id: str, action: str, expected_revision: int,
                     evidence: str | None = None, agent_id: str | None = None,
                     observation: dict | None = None) -> dict:
        if action not in STEP_ACTIONS or type(expected_revision) is not int:
            raise ValueError('Invalid workflow action or expected_revision')
        if action in {'handoff', 'ack_handoff'} and any(
                not isinstance(value, str) or not value.strip() for value in (agent_id, evidence)):
            raise ValueError('Handoff sender/recipient binding and evidence must be nonblank strings')
        with self.db.transaction():
            step = self.get_step(step_id)
            run = self._run(step['run_id'])
            if step['revision'] != expected_revision:
                raise WorkflowConflictError('Workflow revision conflict')
            self._packet(step)
            self._bindings(step, dispatch=action in {'dispatch', 'start_rework', 'accept'})
            state = step['state']
            checkpoint = step['checkpoint']
            if action == 'observe':
                if not isinstance(observation, dict) or set(observation) != {'agent_id', 'state', 'observed_at', 'evidence'}:
                    raise ValueError('Observation requires agent_id/state/observed_at/evidence')
                if observation['agent_id'] not in {step['owner_agent_id'], step['verifier_agent_id']}:
                    raise WorkflowConflictError('Observation agent binding does not match this step')
                if observation['state'] not in {'running', 'idle', 'interrupted', 'ended'} or not isinstance(observation['evidence'], str) or not observation['evidence'].strip():
                    raise ValueError('Invalid native observation state/evidence')
                at = observation['observed_at']
                if type(at) not in (float, int) or not math.isfinite(at) or at <= 0 or at > time.time() + 5:
                    raise ValueError('Invalid native observation timestamp')
                checkpoint['native_observation'] = observation
                return self._set(step, state, checkpoint)
            if action == 'dispatch' and state == 'planned':
                for dependency in step['dependencies']:
                    parent = self.get_step(dependency)
                    if parent['state'] != 'done':
                        raise WorkflowConflictError('A dependency is not complete')
                    self._valid_verification(parent, 'integrated')
                return self._set(step, 'dispatched')
            if action == 'accept' and state == 'dispatched':
                return self._set(step, 'implementing')
            if action == 'submit' and state == 'implementing':
                checkpoint.pop('challenge', None)
                return self._set(step, 'candidate_review', checkpoint)
            if action == 'challenge' and state == 'candidate_review':
                if agent_id != step['verifier_agent_id'] or not isinstance(evidence, str) or not evidence.strip():
                    raise WorkflowConflictError('Independent verifier challenge evidence and binding required')
                checkpoint['challenge'] = {'agent_id': agent_id, 'evidence': evidence, 'attempt': step['attempt']}
                return self._set(step, state, checkpoint)
            if action == 'start_rework' and state == 'rework' and step['attempt'] < MAX_REWORKS:
                checkpoint.pop('challenge', None)
                checkpoint.pop('handoff', None)
                return self._set(step, 'implementing', checkpoint, step['attempt'] + 1)
            if action == 'begin_integrated_review' and state == 'ready_to_merge' and not step['workspace_id']:
                self._valid_verification(step, 'candidate')
                return self._set(step, 'integrated_review')
            if action == 'complete' and state == 'integrated_review':
                self._valid_verification(step, 'integrated')
                return self._set(step, 'handoff' if step['handoff_required'] else 'done')
            if action == 'reverify' and state == 'done':
                # A later intentional merge can stale an earlier milestone's content gate.
                return self._set(step, 'integrated_review')
            if action == 'handoff' and state == 'handoff':
                checkpoint['handoff'] = {'sender_agent_id': agent_id, 'evidence': evidence,
                                          'packet_version': step['packet_version']}
                return self._set(step, state, checkpoint)
            if action == 'ack_handoff' and state == 'handoff':
                handoff = checkpoint.get('handoff')
                if not handoff or agent_id == handoff['sender_agent_id']:
                    raise WorkflowConflictError('Handoff requires confirmation by a different actual coordinator')
                self._valid_verification(step, 'integrated')
                checkpoint['handoff']['recipient_agent_id'] = agent_id
                checkpoint['handoff']['receipt'] = evidence
                return self._set(step, 'done', checkpoint)
            if action == 'reconcile':
                raise WorkflowConflictError('Use explicit workspace merge reconciliation against actual target content; no state-only success')
            raise WorkflowConflictError(f'Action {action} is not permitted from {state}')

    def capture(self, step_id: str, scope: str = 'candidate', paths: list[str] | None = None) -> dict:
        if scope not in {'candidate', 'integrated'}:
            raise ValueError('Artifact scope must be candidate or integrated')
        with self.db.transaction() as conn:
            step = self.get_step(step_id)
            run = self._run(step['run_id'])
            self._packet(step)
            self._bindings(step)
            project = self.db.get_project(run['project_id'])
            root, mode, baseline = Path(project['root_path']), 'snapshot', {}
            if scope == 'candidate' and step['workspace_id']:
                ws = self.db.get_workspace(step['workspace_id'])
                root, mode, baseline = Path(ws['path']), ws['mode'], ws['snapshot_manifest'].get('files', {})
                # A merge may affect any source path, so its candidate gate covers the full source scan.
                if paths is not None:
                    raise ValueError('Workspace candidates capture the complete workspace, not a partial scope')
            descriptor = capture_content(root, paths=paths, mode=mode, baseline=baseline)
            required = self._required_capture_scope(step, scope)
            if not self._covers(required, descriptor['paths']):
                raise WorkflowConflictError('Artifact scope does not cover the frozen author/candidate scope')
            descriptor['required_paths'] = required
            descriptor['workspace_id'] = step['workspace_id'] if scope == 'candidate' else None
            if scope == 'integrated' and step['workspace_id']:
                merge = step['checkpoint'].get('merge', {})
                if not merge.get('proof'):
                    raise WorkflowConflictError('Integrated capture requires actual merge proof')
                descriptor['merge_proof_digest'] = digest(merge['proof'])
            identity = _id('artifact')
            conn.execute('''INSERT INTO workflow_artifacts VALUES(?,?,?,?,?,?,?,?,?)''',
                         (identity, step_id, scope, step['packet_version'], step['attempt'],
                          _json(descriptor), digest(descriptor), time.time(), step['contract_revision']))
            return self._artifact(identity)

    def _artifact(self, artifact_id: str) -> dict:
        with self.db.read_connection() as conn:
            row = conn.execute('SELECT * FROM workflow_artifacts WHERE id=?', (artifact_id,)).fetchone()
        if row is None:
            raise NotFoundError(f'Artifact not found: {artifact_id}')
        return _decode(row)

    def _artifact_current(self, artifact: dict) -> bool:
        descriptor = artifact['descriptor']
        try:
            current = recapture(descriptor)
        except (FileNotFoundError, ValueError):
            return False
        current['workspace_id'] = descriptor['workspace_id']
        current['required_paths'] = descriptor['required_paths']
        if 'merge_proof_digest' in descriptor:
            current['merge_proof_digest'] = descriptor['merge_proof_digest']
        return digest(current) == artifact['digest']

    def record(self, step_id: str, artifact_id: str, verifier: str, verdict: str,
                criteria_results: list[dict], evidence_files: list[str], key: str) -> dict:
        if verdict not in {'PASS', 'FAIL'} or not isinstance(key, str) or not key.strip():
            raise ValueError('Verification requires PASS/FAIL and a nonempty stable key')
        request = {'artifact_id': artifact_id, 'verifier': verifier, 'verdict': verdict,
                   'criteria_results': criteria_results, 'evidence_files': evidence_files}
        request_digest = digest(request)
        with self.db.transaction() as conn:
            existing = conn.execute('SELECT * FROM verification_records WHERE step_id=? AND key=?', (step_id, key)).fetchone()
            if existing:
                if existing['request_digest'] != request_digest:
                    raise WorkflowConflictError('Verification key already has different content')
                return _decode(existing)
            step = self.get_step(step_id)
            run = self._run(step['run_id'])
            self._packet(step)
            self._bindings(step)
            if verifier != step['verifier']:
                raise WorkflowConflictError('Only the bound independent verifier can record this gate')
            artifact = self._artifact(artifact_id)
            if (artifact['step_id'] != step_id or artifact['packet_version'] != step['packet_version']
                    or artifact['attempt'] != step['attempt'] or artifact['contract_revision'] != step['contract_revision']):
                raise WorkflowConflictError('Verification artifact belongs to another delivery')
            required_state = 'candidate_review' if artifact['scope'] == 'candidate' else 'integrated_review'
            if step['state'] != required_state:
                raise WorkflowConflictError(f'Verification is not allowed from {step["state"]}')
            if run['mode'] == 'boost' and artifact['scope'] == 'candidate' and not step['checkpoint'].get('challenge'):
                raise WorkflowConflictError('Independent challenge must be recorded before Boost candidate acceptance')
            if not isinstance(criteria_results, list) or any(not isinstance(x, dict) or set(x) != {'criterion', 'status'} or x['status'] not in {'PASS', 'FAIL', 'NOT_RUN'} for x in criteria_results):
                raise ValueError('Invalid criteria results')
            if len(criteria_results) != len(step['criteria']) or {x['criterion'] for x in criteria_results} != set(step['criteria']):
                raise ValueError('Verification criteria are missing or duplicated')
            if verdict == 'PASS' and any(x['status'] != 'PASS' for x in criteria_results):
                raise ValueError('A PASS requires every criterion to pass')
            manifest = capture_evidence(evidence_files)
            if not self._artifact_current(artifact):
                raise WorkflowConflictError('Verification artifact changed after capture')
            identity = _id('verification')
            conn.execute('''INSERT INTO verification_records VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                         (identity, step_id, artifact_id, key, step['packet_version'], step['attempt'],
                          verifier, step['verifier_agent_id'], verdict, _json(criteria_results),
                          _json(manifest), request_digest, time.time()))
            if verdict == 'FAIL':
                self._set(step, 'rework' if step['attempt'] < MAX_REWORKS else 'needs_decision')
            elif artifact['scope'] == 'candidate':
                self._set(step, 'ready_to_merge')
            else:
                self._set(step, 'integrated_review')
            return self._verification(identity)

    def _verification(self, identity: str) -> dict:
        with self.db.read_connection() as conn:
            row = conn.execute('SELECT * FROM verification_records WHERE id=?', (identity,)).fetchone()
        if row is None:
            raise NotFoundError(f'Verification not found: {identity}')
        return _decode(row)

    def _valid_verification(self, step: dict, scope: str) -> dict:
        self._packet(step)
        self._bindings(step)
        with self.db.read_connection() as conn:
            rows = conn.execute('''SELECT v.* FROM verification_records v JOIN workflow_artifacts a ON a.id=v.artifact_id
              WHERE v.step_id=? AND a.scope=? ORDER BY v.created_at DESC''', (step['id'], scope)).fetchall()
        for row in rows:
            v = _decode(row)
            if v['packet_version'] != step['packet_version'] or v['attempt'] != step['attempt']:
                continue
            if v['verdict'] != 'PASS' or v['verifier_agent_id'] != step['verifier_agent_id']:
                raise WorkflowConflictError('The current independent verification is not PASS')
            artifact = self._artifact(v['artifact_id'])
            if artifact['contract_revision'] != step['contract_revision']:
                raise WorkflowConflictError('The workflow contract changed after verification')
            if not self._covers(self._required_capture_scope(step, scope), artifact['descriptor']['paths']):
                raise WorkflowConflictError('Approved artifact scope does not cover the current delivery')
            if {x['criterion'] for x in v['criteria_results']} != set(step['criteria']):
                raise WorkflowConflictError('The verified criteria do not match the current contract')
            if not evidence_current(v['evidence_manifest']) or not self._artifact_current(artifact):
                raise WorkflowConflictError('Approved artifact or evidence is stale')
            return artifact
        raise WorkflowConflictError(f'No current {scope} independent PASS')

    def find_step_for_workspace(self, workspace_id: str) -> dict | None:
        ws = self.db.get_workspace(workspace_id)
        if ws is None:
            raise NotFoundError(f'Workspace not found: {workspace_id}')
        run = self.db.get_run(ws['run_id'])
        if run['workflow_version'] == 0:
            return None
        with self.db.read_connection() as conn:
            rows = conn.execute('SELECT * FROM workflow_steps WHERE workspace_id=?', (workspace_id,)).fetchall()
        if len(rows) != 1:
            raise WorkflowConflictError('A strict workspace requires exactly one owning workflow step')
        return _decode(rows[0])

    def require_candidate(self, workspace_id: str, *, source_commit: str | None = None,
                           manifest: dict | None = None) -> dict | None:
        step = self.find_step_for_workspace(workspace_id)
        if step is None:
            return None
        self._run(step['run_id'])
        self._bindings(step, dispatch=True)
        if step['state'] != 'ready_to_merge':
            raise WorkflowConflictError('Candidate is not ready to merge')
        artifact = self._valid_verification(step, 'candidate')
        descriptor = artifact['descriptor']
        if source_commit is not None and descriptor.get('source_commit') != source_commit:
            raise WorkflowConflictError('Fetched source commit is not the approved candidate')
        if manifest is not None and descriptor['manifest'] != manifest:
            raise WorkflowConflictError('Source manifest is not the approved candidate')
        return step

    def begin_merge(self, step_id: str, checkpoint: dict) -> dict:
        with self.db.transaction():
            step = self.get_step(step_id)
            self.require_candidate(step['workspace_id'])
            if not isinstance(checkpoint, dict) or not checkpoint.get('merge_id'):
                raise ValueError('Merge checkpoint requires a unique merge_id')
            data = step['checkpoint']
            data['merge'] = checkpoint
            return self._set(step, 'integrating', data)

    def finish_merge(self, step_id: str, proof: dict) -> dict:
        with self.db.transaction():
            step = self.get_step(step_id)
            self._run(step['run_id'])
            self._packet(step)
            self._bindings(step)
            if step['state'] not in {'integrating', 'reconciliation_required'}:
                raise WorkflowConflictError('No pending merge checkpoint')
            checkpoint = step['checkpoint']
            if not proof.get('merge_id') or proof['merge_id'] != checkpoint['merge']['merge_id']:
                raise WorkflowConflictError('Merge proof does not match the pending operation')
            checkpoint['merge']['proof'] = proof
            checkpoint['merge'].pop('error', None)
            return self._set(step, 'integrated_review', checkpoint)

    def mark_merge_uncertain(self, step_id: str, error: str) -> dict:
        with self.db.transaction():
            step = self.get_step(step_id)
            checkpoint = step['checkpoint']
            if not checkpoint.get('merge'):
                raise WorkflowConflictError('Missing merge checkpoint')
            if step['state'] not in {'integrating', 'reconciliation_required'}:
                raise WorkflowConflictError('No in-flight merge to mark uncertain')
            checkpoint['merge']['error'] = error
            return self._set(step, 'reconciliation_required', checkpoint)

    def require_integrated(self, workspace_id: str) -> None:
        step = self.find_step_for_workspace(workspace_id)
        if step is not None:
            self._run(step['run_id'])
            if step['state'] not in {'integrated_review', 'handoff', 'done'}:
                raise WorkflowConflictError('Workspace requires final integrated acceptance before cleanup')
            self._valid_verification(step, 'integrated')

    require_cleanup = require_integrated

    def require_completion(self, run_id: str) -> None:
        self._run(run_id)
        with self.db.read_connection() as conn:
            rows = conn.execute('SELECT * FROM workflow_steps WHERE run_id=?', (run_id,)).fetchall()
            unresolved = conn.execute('''SELECT id FROM inbox_messages WHERE run_id=? AND
                (status='accepted' OR (status='pending' AND kind!='notice')) LIMIT 1''', (run_id,)).fetchone()
        if not rows or unresolved:
            raise WorkflowConflictError('Missing workflow steps or unresolved inbox work')
        criteria = set(self._criteria(self.db.get_task_packet(run_id)['acceptance_criteria']))
        covered = set()
        for row in rows:
            step = _decode(row)
            if step['state'] != 'done':
                raise WorkflowConflictError(f'Workflow step is not complete: {step["step_key"]}')
            self._valid_verification(step, 'integrated')
            covered.update(step['criteria'])
        if covered != criteria:
            raise WorkflowConflictError('Workflow steps do not cover all frozen acceptance criteria')
        if any(x['status'] == 'pending' for x in self.db.list_worker_controls(run_id)):
            raise WorkflowConflictError('Acknowledge pending controls before completion')

    def summary(self, run_id: str) -> dict:
        run = self.db.get_run(run_id)
        if run is None:
            raise NotFoundError(f'Run not found: {run_id}')
        with self.db.read_connection() as conn:
            steps = [_decode(r) for r in conn.execute('SELECT * FROM workflow_steps WHERE run_id=? ORDER BY created_at', (run_id,))]
            records = [_decode(r) for r in conn.execute('''SELECT v.* FROM verification_records v JOIN workflow_steps s
                ON s.id=v.step_id WHERE s.run_id=? ORDER BY v.created_at''', (run_id,))]
            artifacts = [dict(r) for r in conn.execute('''SELECT a.id,a.step_id,a.scope,a.digest,a.created_at FROM workflow_artifacts a
                JOIN workflow_steps s ON s.id=a.step_id WHERE s.run_id=? ORDER BY a.created_at''', (run_id,))]
        # Dashboard reads stored observations only; gate use rehashes actual artifacts and evidence.
        blockers = [{'step_id': s['id'], 'state': s['state']} for s in steps if s['state'] != 'done']
        return {'steps': steps, 'verifications': records, 'artifacts': artifacts,
                'blockers': blockers, 'max_reworks': MAX_REWORKS,
                'workflow_version': run['workflow_version'], 'artifact_validity': 'checked_at_gate_use'}
