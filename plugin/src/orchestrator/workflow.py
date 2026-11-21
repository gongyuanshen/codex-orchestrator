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
from orchestrator.admission import ownership_scopes, overlap, contains

EXECUTION_KINDS = frozenset({'native', 'coordinator_local'})

MAX_REWORKS = 3
STEP_ACTIONS = frozenset({'dispatch', 'accept', 'submit', 'challenge', 'start_rework',
                          'begin_integrated_review', 'complete', 'handoff', 'ack_handoff',
                          'reconcile', 'observe', 'reverify', 'local_revision_boundary'})

ENV_PROFILES: dict[str, tuple[str, ...]] = {
    'none': (),
    'windows_minimal_py': (
        'SYSTEMROOT',
        'SystemRoot',
        'COMSPEC',
        'PATHEXT',
        'PATH',
        'TEMP',
        'TMP',
        'USERPROFILE',
        'PYTHONUTF8',
        'PYTHONIOENCODING',
    ),
    'posix_minimal_py': (
        'PATH',
        'HOME',
        'TMPDIR',
        'LANG',
        'LC_ALL',
        'PYTHONUTF8',
        'PYTHONIOENCODING',
    ),
}


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
 step_id TEXT REFERENCES workflow_steps(id),
 step_packet_version INTEGER, step_attempt INTEGER, step_contract_revision INTEGER,
 UNIQUE(run_id,sender,key),
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
    if 'step_key' in result and isinstance(result.get('checkpoint'), dict):
        result['execution_kind'] = result['checkpoint'].get('execution_kind', 'native')
    return result


class WorkflowService:
    def __init__(self, db: Database):
        self.db = db

    def _steps(self, run_id: str) -> list[dict]:
        with self.db.read_connection() as conn:
            return [_decode(row) for row in conn.execute(
                'SELECT * FROM workflow_steps WHERE run_id=? ORDER BY created_at,id', (run_id,))]

    def _project_root(self, run_id: str) -> Path:
        return Path(self.db.get_project(self.db.get_run(run_id)['project_id'])['root_path'])

    def _scopes(self, step: dict) -> list[str]:
        packet = self._packet(step)
        return ownership_scopes(self._project_root(step['run_id']), packet['file_ownership'][step['owner']])

    def _ancestors(self, step: dict) -> set[str]:
        result, pending = set(), list(step['dependencies'])
        while pending:
            identity = pending.pop()
            if identity not in result:
                result.add(identity)
                pending.extend(self.get_step(identity)['dependencies'])
        return result

    def _quiescent(self, step: dict, after: float) -> None:
        observation = step['checkpoint'].get('native_observations', {}).get(step['owner_agent_id'])
        after = max(after, step['checkpoint'].get('owner_last_dispatch_at', 0))
        if (not observation or observation['state'] not in {'idle', 'interrupted'}
                or observation['observed_at'] < after):
            raise WorkflowConflictError('Current original-author native idle/interrupted observation required; ended identity needs decision')

    def _revision_boundary(self, step: dict) -> None:
        if step['checkpoint'].get('execution_kind', 'native') != 'coordinator_local':
            self._quiescent(step, step['checkpoint'].get('owner_last_dispatch_at', step['created_at']))
            return
        self._bindings(step, dispatch=True, allow_lazy_verifier=True)
        boundary = step['checkpoint'].get('local_revision_boundary')
        if (not boundary or boundary['agent_id'] != step['owner_agent_id']
                or any(boundary[key] != step[key] for key in ('revision', 'packet_version', 'contract_revision', 'attempt'))
                or boundary['content_digest'] != digest(capture_content(self._project_root(step['run_id']))['manifest'])):
            raise WorkflowConflictError('Fresh current local_revision_boundary required before revising coordinator-local work')

    def _admit(self, step: dict, *, first: bool) -> None:
        """Called only inside step_advance's BEGIN IMMEDIATE admission transaction."""
        scopes = self._scopes(step)
        ancestors = self._ancestors(step)
        for other in self._steps(step['run_id']):
            if other['id'] == step['id']:
                continue
            if other['state'] == 'planned':
                # A revised contract may no longer describe the old native writer's scope.
                if 'original_bindings' in other['checkpoint']:
                    self._quiescent(other, other['checkpoint'].get('owner_last_dispatch_at', other['created_at']))
                continue
            conflict = overlap(scopes, self._scopes(other))
            if not conflict:
                continue
            if other['id'] not in ancestors:
                # An explicitly revised chain must revalidate its predecessor before
                # the paused original successor consumes its remaining rework budget.
                if (other['state'] == 'rework' and other['checkpoint'].get('admission_revision_required')
                        and step['id'] in self._ancestors(other)):
                    self._quiescent(other, other['checkpoint']['last_failure_at'])
                    continue
                raise WorkflowConflictError(f'Concurrent ownership conflict with {other["step_key"]}: {conflict}')
            if first and other['state'] != 'done':
                raise WorkflowConflictError('Overlapping dependency is not complete')
            self._quiescent(other, other['checkpoint'].get('completion_at', other['created_at']))
        if first:
            dependencies = {}
            for identity in sorted(ancestors):
                parent = self.get_step(identity)
                if parent['state'] != 'done':
                    raise WorkflowConflictError('A dependency is not complete')
                artifact = self._valid_verification(parent, 'integrated')
                with self.db.read_connection() as conn:
                    record = conn.execute('SELECT id FROM verification_records WHERE artifact_id=? AND verdict=\'PASS\' ORDER BY created_at DESC LIMIT 1', (artifact['id'],)).fetchone()
                dependencies[identity] = {
                    **{key: parent[key] for key in ('packet_version', 'contract_revision', 'attempt', 'owner_agent_id', 'verifier_agent_id')},
                    'artifact_id': artifact['id'], 'verification_id': record['id'],
                    'handoff_digest': digest(parent['checkpoint'].get('handoff'))}
            step['checkpoint']['admission'] = {
                'packet_version': step['packet_version'], 'contract_revision': step['contract_revision'],
                'execution_kind': step['checkpoint'].get('execution_kind', 'native'),
                'dependencies': dependencies, 'allowed_scopes': scopes,
                'base_manifest': capture_content(self._project_root(step['run_id']))['manifest'],
                'prior_merge_ids': sorted(merge['merge_id'] for current in self._steps(step['run_id'])
                    for merge in current['checkpoint'].get('merge_history', []))}
        else:
            admission = step['checkpoint'].get('admission')
            if (not admission or any(admission[key] != step[key] for key in ('packet_version', 'contract_revision'))
                    or admission.get('execution_kind', 'native') != step['checkpoint'].get('execution_kind', 'native')
                    or admission['allowed_scopes'] != scopes or set(admission['dependencies']) != ancestors):
                raise WorkflowConflictError('Missing or changed dependency admission; reconciliation required')
            for identity, frozen in admission['dependencies'].items():
                parent = self.get_step(identity)
                self._packet(parent)
                self._bindings(parent)
                if any(parent[key] != frozen[key] for key in ('packet_version', 'contract_revision', 'attempt', 'owner_agent_id', 'verifier_agent_id')):
                    raise WorkflowConflictError('Dependency contract changed; reconciliation required')
                if digest(parent['checkpoint'].get('handoff')) != frozen['handoff_digest']:
                    raise WorkflowConflictError('Dependency handoff changed; reconciliation required')
                verification = self._verification(frozen['verification_id'])
                if verification['artifact_id'] != frozen['artifact_id'] or not evidence_current(verification['evidence_manifest']):
                    raise WorkflowConflictError('Dependency admission evidence changed; reconciliation required')
            if admission['dependencies']:
                self._admitted_manifest(step)

    def _admitted_manifest(self, step: dict) -> dict:
        admission = step['checkpoint']['admission']
        current = admission['base_manifest']
        history = [(owner, merge) for owner in self._steps(step['run_id'])
                   for merge in owner['checkpoint'].get('merge_history', [])
                   if merge['merge_id'] not in admission['prior_merge_ids']]
        for owner, merge in sorted(history, key=lambda item: item[1]['completed_at']):
            if (merge['before_manifest'] != current or not merge.get('proof')
                    or any(merge[key] != owner[key] for key in ('packet_version', 'contract_revision', 'owner_agent_id'))):
                raise WorkflowConflictError('Missing or changed merge chain proof; reconciliation required')
            self._bindings(owner)
            after = merge['after_manifest']
            changed = {p for p in set(current) | set(after) if current.get(p) != after.get(p)}
            if merge['allowed_scopes'] != self._scopes(owner) or any(not contains(merge['allowed_scopes'], p) for p in changed):
                raise WorkflowConflictError('Merge chain exceeds admitted ownership; reconciliation required')
            if merge['proof']['mode'] == 'snapshot' and merge['proof']['target_manifest'] != after:
                raise WorkflowConflictError('Merge proof does not certify its recorded target')
            current = after
        if capture_content(self._project_root(step['run_id']))['manifest'] != current:
            raise WorkflowConflictError('Foreign changes or missing merge chain proof; reconciliation required')
        return current

    def plan_readiness(self, run_id: str) -> dict:
        steps = self._steps(run_id)
        packet = self.db.get_task_packet(run_id)
        covered = {criterion for step in steps for criterion in step['criteria']}
        uncovered = sorted(set(packet['acceptance_criteria']) - covered) if packet else []
        conflicts = []
        if self.db.get_run(run_id)['workflow_version'] in {4, 5}:
            for index, step in enumerate(steps):
                for other in steps[index + 1:]:
                    if other['id'] in self._ancestors(step) or step['id'] in self._ancestors(other):
                        continue
                    conflict = overlap(self._scopes(step), self._scopes(other))
                    if conflict:
                        conflicts.append({'step_id': step['id'], 'other_step_id': other['id'], 'paths': list(conflict)})
        return {'ready': bool(steps) and not uncovered and not conflicts,
                'uncovered_criteria': uncovered, 'ownership_conflicts': conflicts}

    def _handoff_facts(self, run_id: str) -> dict:
        with self.db.read_connection() as conn:
            artifacts = [dict(row) for row in conn.execute('SELECT a.id,a.digest FROM workflow_artifacts a JOIN workflow_steps s ON a.step_id=s.id WHERE s.run_id=? ORDER BY a.id', (run_id,))]
            verifications = [dict(row) for row in conn.execute('SELECT v.id,v.artifact_id,v.request_digest FROM verification_records v JOIN workflow_steps s ON v.step_id=s.id WHERE s.run_id=? ORDER BY v.id', (run_id,))]
            inbox = [dict(row) for row in conn.execute("SELECT id,status FROM inbox_messages WHERE run_id=? AND kind!='notice' AND status IN ('pending','accepted') ORDER BY id", (run_id,))]
            actions = [dict(row) for row in conn.execute("""SELECT n.action_id,n.status,n.revision,n.source_digest FROM native_actions n
                LEFT JOIN inbox_messages m ON n.source_type='inbox' AND m.id=n.source_id
                WHERE n.run_id=? AND n.status NOT IN ('confirmed','cancelled') AND (m.kind IS NULL OR m.kind!='notice')
                ORDER BY n.action_id""", (run_id,))]
        packet = self.db.get_task_packet(run_id)
        return {'packet_id': packet['id'] if packet else None,
            'steps': [{**{key: s[key] for key in ('id', 'packet_version', 'contract_revision', 'attempt', 'owner_agent_id', 'verifier_agent_id')},
                'state': 'accepted' if s['state'] in {'handoff', 'done'} else s['state'],
                'native_states': {agent: observation['state'] for agent, observation in s['checkpoint'].get('native_observations', {}).items()}}
                for s in self._steps(run_id)],
            'bindings': [{'name': w['name'], 'agent_id': w['agent_id'], 'status': w['status']} for w in self.db.list_workers(run_id=run_id)],
            'artifacts': artifacts, 'verifications': verifications, 'inbox': inbox,
            'controls': [{'id': c['id'], 'status': c['status']} for c in self.db.list_worker_controls(run_id) if c['status'] == 'pending'],
            'native_actions': actions}

    def resume_context(self, run_id: str) -> dict:
        run = self.db.get_run(run_id)
        wf_ver = run['workflow_version'] if run else 0
        facts = self._handoff_facts(run_id)
        steps = self._steps(run_id)
        workers = {worker['name']: worker for worker in self.db.list_workers(run_id=run_id)}
        binding_issues = []
        for step in steps:
            for role in ('owner', 'verifier'):
                worker = workers.get(step[role])
                expected_aid = step[role + '_agent_id']
                if wf_ver == 5 and expected_aid == '':
                    if not worker or worker['agent_id'] is not None or worker['status'] in {'stopped', 'killed'}:
                        binding_issues.append({'step_id': step['id'], 'agent_id': expected_aid,
                            'role': role, 'decision_required': True})
                    continue
                observation = step['checkpoint'].get('native_observations', {}).get(expected_aid)
                if (not worker or worker['agent_id'] != expected_aid or worker['status'] in {'stopped', 'killed'}
                        or observation and observation['state'] == 'ended'):
                    binding_issues.append({'step_id': step['id'], 'agent_id': expected_aid,
                        'role': role, 'decision_required': True})
        return {'facts_digest': digest(facts), 'frozen_packet_id': facts['packet_id'],
            'binding_issues': binding_issues,
            'milestones': [{'step_id': s['id'], 'step_key': s['step_key'], 'state': s['state'],
                'owner_agent_id': s['owner_agent_id'], 'verifier_agent_id': s['verifier_agent_id'],
                'attempt': s['attempt']} for s in steps],
            'artifact_refs': facts['artifacts'], 'verification_refs': facts['verifications'],
            'unresolved': {key: facts[key] for key in ('inbox', 'controls', 'native_actions')},
            'handoffs': [{'step_id': s['id'], 'digest': s['checkpoint']['handoff']['digest'],
                'recipient_agent_id': s['checkpoint']['handoff']['pack']['recipient_agent_id'],
                'current': s['checkpoint']['handoff']['pack']['facts_digest'] == digest(facts),
                'acknowledged': s['checkpoint'].get('handoff_ack', {}).get('digest') == s['checkpoint']['handoff']['digest']}
                for s in steps if 'pack' in s['checkpoint'].get('handoff', {})],
            'lifecycle_authority': 'original_root_coordinator',
            'forbidden_actions': ['replace_original_bindings', 'replay_uncertain_native_actions', 'resume_stopped_members']}

    def _run(self, run_id: str, *, active: bool = True) -> dict:
        run = self.db.get_run(run_id)
        if run is None:
            raise NotFoundError(f'Run not found: {run_id}')
        if run['workflow_version'] == 0:
            raise WorkflowConflictError('Legacy run has no structured workflow; create a new continuation run')
        if run['workflow_version'] not in {1, 2, 3, 4, 5}:
            raise WorkflowConflictError('Unsupported workflow version')
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

    def _require_no_fenced_spawn(self, run_id: str, *worker_names: str) -> None:
        names = tuple({n for n in worker_names if n})
        if not names:
            return
        placeholders = ','.join('?' for _ in names)
        with self.db.read_connection() as conn:
            fenced = conn.execute(
                f"SELECT action_id, status FROM native_actions WHERE run_id=? AND recipient IN ({placeholders}) "
                "AND action_type='spawn_agent' AND status IN ('claimed','invoking','needs_reconciliation') LIMIT 1",
                (run_id, *names),
            ).fetchone()
        if fenced is not None:
            raise WorkflowConflictError(
                f"Step is fenced by in-flight/uncertain spawn_agent ({fenced['action_id']}: {fenced['status']}); reconcile first"
            )

    def _bindings(
        self,
        step: dict,
        *,
        dispatch: bool = False,
        allow_pre_spawn_owner: bool = False,
        allow_lazy_verifier: bool = False,
    ) -> None:
        run = self.db.get_run(step['run_id'])
        wf_ver = run['workflow_version'] if run else 0
        if wf_ver != 5:
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
            return

        owner = self.db.get_worker_by_name(step['run_id'], step['owner'])
        if owner is None:
            raise NotFoundError(f"Worker not found: {step['owner']}")
        verifier = self.db.get_worker_by_name(step['run_id'], step['verifier'])
        if verifier is None:
            raise NotFoundError(f"Worker not found: {step['verifier']}")
        if owner['name'] == verifier['name']:
            raise WorkflowConflictError('Author and verifier must be different native agents')
        with self.db.read_connection() as conn:
            fenced = conn.execute(
                "SELECT action_id FROM native_actions WHERE run_id=? AND recipient IN (?,?) AND status='needs_reconciliation' LIMIT 1",
                (step['run_id'], step['owner'], step['verifier']),
            ).fetchone()
        if fenced is not None:
            raise WorkflowConflictError(
                f"Step requires native reconciliation ({fenced['action_id']}) before workflow progress"
            )

        if allow_pre_spawn_owner and not step['owner_agent_id']:
            self._require_no_fenced_spawn(step['run_id'], step['owner'])
            if owner['agent_id'] is not None or not owner.get('pending_agent_path'):
                raise WorkflowConflictError(
                    f"Worker {step['owner']} requires a native agent binding or pre-spawn task_name"
                )
            expected_owner_path = step['checkpoint'].get('pending_owner_path')
            if expected_owner_path and expected_owner_path != owner['pending_agent_path']:
                raise WorkflowConflictError('Workflow owner pre-spawn path changed; explicitly revise the step')
        else:
            if not owner['agent_id'] or not step['owner_agent_id']:
                raise WorkflowConflictError(f"Worker {step['owner']} has no actual native agent binding")
            if owner['agent_id'] != step['owner_agent_id']:
                raise WorkflowConflictError('Workflow owner/verifier binding changed; explicitly revise the step')

        if allow_lazy_verifier and not step['verifier_agent_id']:
            if verifier['agent_id'] is not None:
                raise WorkflowConflictError('Workflow owner/verifier binding changed; explicitly revise the step')
        else:
            if not verifier['agent_id'] or not step['verifier_agent_id']:
                raise WorkflowConflictError(f"Worker {step['verifier']} has no actual native agent binding")
            if verifier['agent_id'] != step['verifier_agent_id']:
                raise WorkflowConflictError('Workflow owner/verifier binding changed; explicitly revise the step')

        owner_anchor = owner['agent_id'] or owner.get('pending_agent_path')
        verifier_anchor = verifier['agent_id'] or verifier.get('pending_agent_path')
        if owner_anchor and verifier_anchor and owner_anchor == verifier_anchor:
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
                  handoff_required: bool | None = None, criteria: list[str] | None = None,
                  complexity: str = 'normal', execution_kind: str = 'native') -> dict:
        if not all(isinstance(v, str) and v.strip() for v in (run_id, step_key, owner, verifier)):
            raise ValueError('Workflow identifiers must be nonempty strings')
        if owner == verifier:
            raise ValueError('Author and verifier must be different workers')
        if kind not in {'task', 'milestone'} or (handoff_required is not None and type(handoff_required) is not bool):
            raise ValueError('Invalid workflow kind or handoff flag')
        if complexity not in {'normal', 'high'}:
            raise ValueError('Complexity must be normal or high')
        if execution_kind not in EXECUTION_KINDS:
            raise ValueError('execution_kind must be native or coordinator_local')
        dependencies = [] if dependencies is None else dependencies
        if not isinstance(dependencies, list) or any(not isinstance(x, str) for x in dependencies) or len(set(dependencies)) != len(dependencies):
            raise ValueError('Dependencies must be unique step IDs')
        with self.db.transaction() as conn:
            run = self._run(run_id)
            if execution_kind == 'coordinator_local' and (run['workflow_version'] not in {4, 5} or run['mode'] != 'boost' or workspace_id is not None):
                raise WorkflowConflictError('Coordinator-local execution requires workflow4/5 Boost without a workspace')
            packet = self.db.get_task_packet(run_id)
            if packet is None:
                raise WorkflowConflictError('Freeze a task packet before creating workflow steps')
            packet_criteria = self._criteria(packet['acceptance_criteria'])
            criteria = self._criteria(criteria if criteria is not None else packet_criteria)
            if not set(criteria) <= set(packet_criteria):
                raise ValueError('Step criteria must come from the frozen task packet')
            self._owned_paths(packet, owner)
            if run['workflow_version'] in {4, 5}:
                ownership_scopes(self._project_root(run_id), packet['file_ownership'][owner])
            old = self.get_step(step_id) if step_id else None
            if run['workflow_version'] == 5:
                self._require_no_fenced_spawn(run_id, owner, verifier)
                author = self.db.get_worker_by_name(run_id, owner)
                if author is None:
                    raise NotFoundError(f'Worker not found: {owner}')
                gate = self.db.get_worker_by_name(run_id, verifier)
                if gate is None:
                    raise NotFoundError(f'Worker not found: {verifier}')
                if execution_kind == 'coordinator_local':
                    if not author['agent_id']:
                        raise WorkflowConflictError(f'Worker {owner} has no actual native agent binding')
                elif not author['agent_id'] and not author.get('pending_agent_path'):
                    raise WorkflowConflictError(f'Worker {owner} requires an agent_id or pre-spawn task_name')
                author_anchor = author['agent_id'] or author.get('pending_agent_path')
                gate_anchor = gate['agent_id'] or gate.get('pending_agent_path')
                if author_anchor and gate_anchor and author_anchor == gate_anchor:
                    raise ValueError('Author and verifier must be different native agents')
                author_aid = author['agent_id'] or ''
                gate_aid = gate['agent_id'] or ''
                for peer in self._steps(run_id):
                    if old and peer['id'] == old['id']:
                        continue
                    frozen_v = peer['checkpoint'].get('frozen_verifier_binding')
                    if frozen_v and frozen_v.get('verifier') == verifier and gate_aid != frozen_v.get('verifier_agent_id'):
                        raise WorkflowConflictError('Executed work must retain its original author and verifier bindings')
            else:
                author = self._worker(run_id, owner)
                gate = self._worker(run_id, verifier)
                if author['agent_id'] == gate['agent_id']:
                    raise ValueError('Author and verifier must be different native agents')
                author_aid = author['agent_id']
                gate_aid = gate['agent_id']
            if workspace_id:
                workspace = self.db.get_workspace(workspace_id)
                if workspace is None or workspace['run_id'] != run_id or workspace['worker_name'] != owner:
                    raise ValueError('Workspace must belong to this run and author')
            if old and (old['run_id'] != run_id or old['step_key'] != step_key):
                raise ValueError('Cannot move or rename an existing workflow step')
            if old and old['revision'] != expected_revision:
                raise WorkflowConflictError('Workflow revision conflict')
            if old and run['workflow_version'] == 5:
                frozen_v = old['checkpoint'].get('frozen_verifier_binding')
                if frozen_v and (verifier != frozen_v.get('verifier') or gate_aid != frozen_v.get('verifier_agent_id')):
                    raise WorkflowConflictError('Executed work must retain its original author and verifier bindings')
            if old and run['workflow_version'] in {2, 3, 4, 5} and 'original_bindings' in old['checkpoint']:
                orig = old['checkpoint']['original_bindings']
                if run['workflow_version'] == 5:
                    if owner != orig['owner'] or verifier != orig['verifier']:
                        raise WorkflowConflictError('Executed work must retain its original author and verifier bindings')
                    if orig['owner_agent_id'] and author_aid != orig['owner_agent_id']:
                        raise WorkflowConflictError('Executed work must retain its original author and verifier bindings')
                    if orig['verifier_agent_id'] and gate_aid != orig['verifier_agent_id']:
                        raise WorkflowConflictError('Executed work must retain its original author and verifier bindings')
                else:
                    requested = {'owner': owner, 'verifier': verifier,
                                 'owner_agent_id': author_aid, 'verifier_agent_id': gate_aid}
                    if requested != orig:
                        raise WorkflowConflictError('Executed work must retain its original author and verifier bindings')
                if run['workflow_version'] in {4, 5}:
                    if execution_kind != old['checkpoint'].get('execution_kind', 'native'):
                        raise WorkflowConflictError('Executed work must retain its frozen execution_kind')
                    self._revision_boundary(old)
            revising_rework = bool(old and old['state'] == 'rework' and run['workflow_version'] in {4, 5})
            if old and old['state'] in {'integrating', 'reconciliation_required', 'needs_decision', 'rework'} and not revising_rework:
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
            if handoff_required is None:
                handoff_required = bool(run['mode'] == 'teamwork' and kind == 'milestone')
            values = (kind, owner, verifier, author_aid, gate_aid, workspace_id,
                      packet['version'], _json(dependencies), _json(criteria), int(handoff_required), now)
            if old:
                checkpoint = old['checkpoint']
                checkpoint.pop('challenge', None)
                if run['workflow_version'] in {4, 5}:
                    checkpoint['complexity'] = complexity
                    checkpoint['execution_kind'] = execution_kind
                    checkpoint.pop('admission', None)
                    checkpoint.pop('local_revision_boundary', None)
                    if revising_rework:
                        checkpoint['admission_revision_required'] = True
                if run['workflow_version'] == 5:
                    if not author_aid and author.get('pending_agent_path'):
                        checkpoint['pending_owner_path'] = author['pending_agent_path']
                    else:
                        checkpoint.pop('pending_owner_path', None)
                    if not gate_aid and gate.get('pending_agent_path'):
                        checkpoint['pending_verifier_path'] = gate['pending_agent_path']
                    elif gate_aid:
                        checkpoint.pop('pending_verifier_path', None)
                conn.execute('''UPDATE workflow_steps SET kind=?,owner=?,verifier=?,owner_agent_id=?,verifier_agent_id=?,
                                workspace_id=?,packet_version=?,dependencies_json=?,criteria_json=?,handoff_required=?,updated_at=?,
                                state='planned',checkpoint_json=?,revision=revision+1,contract_revision=contract_revision+1 WHERE id=?''',
                             (*values, _json(checkpoint), sid))
                if revising_rework:
                    conn.execute("UPDATE workflow_steps SET state='rework' WHERE id=?", (sid,))
            else:
                conn.execute('''INSERT INTO workflow_steps
                    (id,run_id,step_key,kind,owner,verifier,owner_agent_id,verifier_agent_id,workspace_id,packet_version,
                     dependencies_json,criteria_json,handoff_required,updated_at,created_at,state)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'planned')''', (sid, run_id, step_key, *values, now))
                if run['workflow_version'] in {4, 5}:
                    init_cp: dict[str, Any] = {'complexity': complexity, 'execution_kind': execution_kind}
                    if run['workflow_version'] == 5:
                        if not author_aid and author.get('pending_agent_path'):
                            init_cp['pending_owner_path'] = author['pending_agent_path']
                        if not gate_aid and gate.get('pending_agent_path'):
                            init_cp['pending_verifier_path'] = gate['pending_agent_path']
                        if gate_aid:
                            init_cp['frozen_verifier_binding'] = {'verifier': verifier, 'verifier_agent_id': gate_aid}
                    conn.execute('UPDATE workflow_steps SET checkpoint_json=? WHERE id=?',
                                 (_json(init_cp), sid))
            step = self.get_step(sid)
            self._log(step, 'saved')
            return step

    def _set(self, step: dict, state: str, checkpoint: dict | None = None, attempt: int | None = None) -> dict:
        with self.db.transaction() as conn:
            checkpoint = step['checkpoint'] if checkpoint is None else checkpoint
            if state != step['state'] and self.db.get_run(step['run_id'])['workflow_version'] in {4, 5}:
                checkpoint['state_entered_at'] = time.time()
            changed = conn.execute('''UPDATE workflow_steps SET state=?,checkpoint_json=?,attempt=?,
                revision=revision+1,updated_at=? WHERE id=? AND revision=?''',
                (state, _json(checkpoint),
                 step['attempt'] if attempt is None else attempt, time.time(), step['id'], step['revision']))
            if changed.rowcount != 1:
                raise WorkflowConflictError('Workflow revision conflict')
            updated = self.get_step(step['id'])
            self._log(updated, state)
            return updated

    def step_advance(self, step_id: str, action: str, expected_revision: int,
                     evidence: str | None = None, agent_id: str | None = None,
                     observation: dict | None = None, artifact_id: str | None = None,
                     recipient_agent_id: str | None = None, handoff_digest: str | None = None,
                     next_responsibility: str | None = None, forbidden_actions: list[str] | None = None,
                     decision_seqs: list[int] | None = None) -> dict:
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
            if action != 'local_revision_boundary':
                self._packet(step)
            allow_pre_spawn = action == 'dispatch' and step['checkpoint'].get('execution_kind', 'native') == 'native'
            allow_lazy_ver = action in {'dispatch', 'accept', 'submit', 'local_revision_boundary', 'observe'}
            self._bindings(
                step,
                dispatch=action in {'dispatch', 'start_rework', 'accept', 'local_revision_boundary'},
                allow_pre_spawn_owner=allow_pre_spawn,
                allow_lazy_verifier=allow_lazy_ver,
            )
            state = step['state']
            checkpoint = step['checkpoint']
            if action == 'local_revision_boundary':
                observation = checkpoint.get('native_observations', {}).get(step['owner_agent_id'])
                packet = self.db.get_task_packet(step['run_id'])
                if (run['workflow_version'] not in {4, 5} or run['mode'] != 'boost' or step['workspace_id'] is not None
                        or checkpoint.get('execution_kind') != 'coordinator_local' or 'original_bindings' not in checkpoint
                        or not step['owner_agent_id'] or agent_id != step['owner_agent_id']
                        or not isinstance(evidence, str) or not evidence.strip()
                        or observation and observation['state'] == 'ended'
                        or not packet or packet['version'] != step['packet_version']
                        or state in {'integrating', 'reconciliation_required', 'needs_decision'}):
                    raise WorkflowConflictError('Local revision boundary requires current original local owner, completed-phase evidence and live binding')
                boundary = {key: step[key] for key in ('packet_version', 'contract_revision', 'attempt')}
                boundary.update(revision=step['revision'] + 1, agent_id=agent_id, evidence=evidence,
                    content_digest=digest(capture_content(self._project_root(step['run_id']))['manifest']), recorded_at=time.time())
                checkpoint['local_revision_boundary'] = boundary
                checkpoint.setdefault('local_revision_boundaries', []).append(boundary)
                return self._set(step, state, checkpoint)
            if action == 'observe':
                if not isinstance(observation, dict) or set(observation) != {'agent_id', 'state', 'observed_at', 'evidence'}:
                    raise ValueError('Observation requires agent_id/state/observed_at/evidence')
                bound_agents = {aid for aid in (step['owner_agent_id'], step['verifier_agent_id']) if aid}
                if observation['agent_id'] not in bound_agents:
                    raise WorkflowConflictError('Observation agent binding does not match this step')
                if observation['state'] not in {'running', 'idle', 'interrupted', 'ended'} or not isinstance(observation['evidence'], str) or not observation['evidence'].strip():
                    raise ValueError('Invalid native observation state/evidence')
                at = observation['observed_at']
                if type(at) not in (float, int) or not math.isfinite(at) or at <= 0 or at > time.time() + 5:
                    raise ValueError('Invalid native observation timestamp')
                checkpoint['native_observation'] = observation
                if run['workflow_version'] in {4, 5}:
                    previous = checkpoint.get('native_observations', {}).get(observation['agent_id'])
                    if previous and observation['observed_at'] <= previous['observed_at']:
                        raise WorkflowConflictError('Native observation must be newer than the previous observation')
                    checkpoint.setdefault('native_observations', {})[observation['agent_id']] = observation
                return self._set(step, state, checkpoint)
            if run['workflow_version'] in {4, 5} and action in {'dispatch', 'accept', 'start_rework'}:
                local = checkpoint.get('execution_kind', 'native') == 'coordinator_local'
                if local and action in {'dispatch', 'start_rework'}:
                    owner_observation = checkpoint.get('native_observations', {}).get(step['owner_agent_id'])
                    if (not step['owner_agent_id'] or agent_id != step['owner_agent_id']
                            or not isinstance(evidence, str) or not evidence.strip()
                            or owner_observation and owner_observation['state'] == 'ended'):
                        raise WorkflowConflictError('Coordinator-local boundary requires original identity and completed-phase evidence; ended identity needs decision')
                    checkpoint.setdefault('coordinator_local_boundaries', []).append({
                        'action': action, 'agent_id': agent_id, 'evidence': evidence,
                        'attempt': step['attempt'] + int(action == 'start_rework'), 'recorded_at': time.time()})
                self._admit(step, first=action == 'dispatch' or (action == 'start_rework' and checkpoint.get('admission_revision_required', False)))
                checkpoint.pop('admission_revision_required', None)
                if action == 'start_rework' and not local:
                    self._quiescent(step, checkpoint['last_failure_at'])
            elif action == 'dispatch' or (run['workflow_version'] in {2, 3} and action in {'accept', 'start_rework'}):
                for dependency in step['dependencies']:
                    parent = self.get_step(dependency)
                    if parent['state'] != 'done':
                        raise WorkflowConflictError('A dependency is not complete')
                    self._valid_verification(parent, 'integrated')
            if action == 'dispatch' and state == 'planned':
                if run['workflow_version'] in {2, 3, 4, 5}:
                    checkpoint.setdefault('original_bindings', {key: step[key] for key in
                        ('owner', 'verifier', 'owner_agent_id', 'verifier_agent_id')})
                if run['workflow_version'] in {4, 5}:
                    checkpoint['owner_last_dispatch_at'] = time.time()
                return self._set(step, 'dispatched', checkpoint)
            if action == 'accept' and state == 'dispatched':
                return self._set(step, 'implementing')
            if action == 'submit' and state == 'implementing':
                checkpoint.pop('challenge', None)
                return self._set(step, 'candidate_review', checkpoint)
            if action == 'challenge' and state == 'candidate_review':
                if not step['verifier_agent_id'] or agent_id != step['verifier_agent_id'] or not isinstance(evidence, str) or not evidence.strip():
                    raise WorkflowConflictError('Independent verifier challenge evidence and binding required')
                challenge = {'agent_id': agent_id, 'evidence': evidence, 'attempt': step['attempt']}
                if run['workflow_version'] in {2, 3, 4, 5}:
                    if not isinstance(artifact_id, str) or not artifact_id.strip():
                        raise WorkflowConflictError('Challenge requires an exact candidate artifact_id')
                    artifact = self._artifact(artifact_id)
                    if (artifact['step_id'] != step_id or artifact['scope'] != 'candidate'
                            or any(artifact[key] != step[key] for key in
                                   ('packet_version', 'attempt', 'contract_revision'))
                            or not self._artifact_current(artifact)):
                        raise WorkflowConflictError('Challenge artifact is not the current candidate delivery')
                    challenge.update({key: step[key] for key in ('packet_version', 'contract_revision')})
                    challenge['artifact_id'] = artifact_id
                checkpoint['challenge'] = challenge
                return self._set(step, state, checkpoint)
            if action == 'start_rework' and state == 'rework' and step['attempt'] < MAX_REWORKS:
                checkpoint.pop('challenge', None)
                checkpoint.pop('handoff', None)
                if run['workflow_version'] in {4, 5}:
                    checkpoint['owner_last_dispatch_at'] = time.time()
                return self._set(step, 'implementing', checkpoint, step['attempt'] + 1)
            if action == 'begin_integrated_review' and state == 'ready_to_merge' and not step['workspace_id']:
                self._valid_verification(step, 'candidate')
                return self._set(step, 'integrated_review')
            if action == 'complete' and state == 'integrated_review':
                self._valid_verification(step, 'integrated')
                if run['workflow_version'] in {4, 5}:
                    checkpoint['completion_at'] = time.time()
                return self._set(step, 'handoff' if step['handoff_required'] else 'done', checkpoint)
            if action == 'reverify' and state == 'done':
                # A later intentional merge can stale an earlier milestone's content gate.
                return self._set(step, 'integrated_review')
            if action == 'handoff' and state == 'handoff':
                if run['workflow_version'] in {2, 3, 4, 5}:
                    if (not isinstance(recipient_agent_id, str) or not recipient_agent_id.strip()
                            or recipient_agent_id == agent_id):
                        raise WorkflowConflictError('Handoff requires a distinct intended recipient_agent_id')
                    self._valid_verification(step, 'integrated')
                if run['workflow_version'] in {4, 5}:
                    if not isinstance(next_responsibility, str) or not next_responsibility.strip():
                        raise ValueError('Handoff requires next_responsibility')
                    if not isinstance(forbidden_actions, list) or any(not isinstance(x, str) or not x.strip() for x in forbidden_actions):
                        raise ValueError('Handoff requires forbidden_actions strings')
                    if not isinstance(decision_seqs, list) or any(type(x) is not int or x <= 0 for x in decision_seqs):
                        raise ValueError('Handoff requires decision_seqs')
                    for seq in decision_seqs:
                        message = self.db.get_board_message(seq)
                        if not message or message['run_id'] != step['run_id']:
                            raise ValueError('Handoff decision seq belongs to another run')
                    facts = self._handoff_facts(step['run_id'])
                    pack = {'step_id': step_id, 'sender_agent_id': agent_id,
                        'recipient_agent_id': recipient_agent_id, 'frozen_requirements': self._packet(step),
                        'milestone': step['step_key'], 'facts': facts, 'facts_digest': digest(facts),
                        'decision_seqs': decision_seqs, 'next_responsibility': next_responsibility,
                        'forbidden_actions': forbidden_actions, 'evidence_ref': evidence}
                    handoff = {'pack': pack, 'digest': digest(pack)}
                    checkpoint['handoff'] = handoff
                    checkpoint.setdefault('handoff_history', []).append(handoff)
                    checkpoint.pop('handoff_ack', None)
                    return self._set(step, state, checkpoint)
                checkpoint['handoff'] = {'sender_agent_id': agent_id, 'evidence': evidence,
                                          'packet_version': step['packet_version']}
                if run['workflow_version'] in {2, 3, 4, 5}:
                    checkpoint['handoff'].update({'recipient_agent_id': recipient_agent_id,
                        'attempt': step['attempt'], 'contract_revision': step['contract_revision']})
                return self._set(step, state, checkpoint)
            if action == 'ack_handoff' and state == 'handoff':
                handoff = checkpoint.get('handoff')
                if run['workflow_version'] in {4, 5}:
                    if (not handoff or handoff_digest != handoff['digest'] or digest(handoff['pack']) != handoff_digest
                            or agent_id != handoff['pack']['recipient_agent_id']
                            or handoff['pack']['facts_digest'] != digest(self._handoff_facts(step['run_id']))):
                        raise WorkflowConflictError('Handoff digest, intended recipient or current facts changed')
                    self._valid_verification(step, 'integrated')
                    checkpoint['handoff_ack'] = {'digest': handoff_digest, 'agent_id': agent_id, 'evidence': evidence}
                    checkpoint.setdefault('handoff_ack_history', []).append(checkpoint['handoff_ack'])
                    return self._set(step, 'done', checkpoint)
                if not handoff or agent_id == handoff['sender_agent_id']:
                    raise WorkflowConflictError('Handoff requires confirmation by a different actual coordinator')
                if run['workflow_version'] in {2, 3, 4, 5} and (
                        agent_id != handoff['recipient_agent_id'] or any(
                            handoff[key] != step[key] for key in ('packet_version', 'attempt', 'contract_revision'))):
                    raise WorkflowConflictError('Handoff ACK does not match the intended recipient/current delivery')
                self._valid_verification(step, 'integrated')
                checkpoint['handoff']['recipient_agent_id'] = agent_id
                checkpoint['handoff']['receipt'] = evidence
                return self._set(step, 'done', checkpoint)
            if action == 'reconcile':
                raise WorkflowConflictError('Use explicit workspace merge reconciliation against actual target content; no state-only success')
            raise WorkflowConflictError(f'Action {action} is not permitted from {state}')

    def capture(self, step_id: str, scope: str = 'candidate', paths: list[str] | None = None,
                verification_commands: list[dict] | None = None, env_profile: str = 'none',
                max_parallel: int | None = None) -> dict:
        if scope not in {'candidate', 'integrated'}:
            raise ValueError('Artifact scope must be candidate or integrated')
        if env_profile not in ENV_PROFILES:
            raise ValueError(f'Invalid env_profile {env_profile!r}; expected one of {sorted(ENV_PROFILES)}')
        if max_parallel is not None and (type(max_parallel) is not int or max_parallel < 1):
            raise ValueError('max_parallel must be a positive integer')
        resolved_commands: list[dict] | None = None
        if verification_commands is not None:
            if not isinstance(verification_commands, list):
                raise ValueError('commands must be nonempty')
            profile_keys = ENV_PROFILES[env_profile]
            resolved_commands = []
            for cmd in verification_commands:
                if not isinstance(cmd, dict):
                    resolved_commands.append(cmd)
                    continue
                cmd_copy = dict(cmd)
                if profile_keys:
                    raw_keys = cmd_copy.get('env_keys')
                    if raw_keys is None:
                        raw_keys = []
                    if isinstance(raw_keys, list):
                        cmd_copy['env_keys'] = list(dict.fromkeys([*raw_keys, *profile_keys]))
                resolved_commands.append(cmd_copy)
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
            identity = _id('artifact')
            if run['workflow_version'] in {3, 4, 5}:
                if resolved_commands is None:
                    raise ValueError('workflow3 requires verification_commands')
                from orchestrator.verify_runner import validate_manifest, validate_evidence_path
                if paths is not None and (not isinstance(paths, list) or not paths or any(
                        not isinstance(path, str) or not path.strip() for path in paths)):
                    raise ValueError('Artifact scope paths must be a nonempty list of relative paths')
                manifest_path = self.db.db_path.parent / 'verification-manifests' / (identity + '.json')
                validate_evidence_path(manifest_path.resolve(), {'root': str(root), 'paths': paths})
            elif resolved_commands is not None or env_profile != 'none' or max_parallel is not None:
                raise ValueError('verification_commands requires workflow3')
            descriptor = capture_content(root, paths=paths, mode=mode, baseline=baseline)
            required = self._required_capture_scope(step, scope)
            if not self._covers(required, descriptor['paths']):
                raise WorkflowConflictError('Artifact scope does not cover the frozen author/candidate scope')
            if run['workflow_version'] in {3, 4, 5}:
                manifest = {'schema_version': 1, 'evidence_level': 'local_collector_v1',
                    'run_id': run['id'], 'step_id': step_id, 'artifact_id': identity,
                    'scope': scope, 'packet_version': step['packet_version'],
                    'attempt': step['attempt'], 'contract_revision': step['contract_revision'],
                    'verifier': step['verifier'], 'verifier_agent_id': step['verifier_agent_id'],
                    'artifact': dict(descriptor), 'commands': resolved_commands}
                if max_parallel is not None:
                    manifest['max_parallel'] = max_parallel
                validate_manifest(manifest)
                if {criterion for cmd in resolved_commands for criterion in cmd['criteria']} != set(step['criteria']):
                    raise ValueError('verification_commands must cover exactly the step criteria')
                manifest_path.parent.mkdir(parents=True, exist_ok=True)
                with manifest_path.open('x', encoding='utf-8') as stream:
                    stream.write(_json(manifest))
                descriptor['verification_manifest'] = manifest
                descriptor['verification_manifest_path'] = str(manifest_path.resolve())
                descriptor['verification_manifest_digest'] = digest(manifest)
            descriptor['required_paths'] = required
            descriptor['workspace_id'] = step['workspace_id'] if scope == 'candidate' else None
            if scope == 'integrated' and step['workspace_id']:
                merge = step['checkpoint'].get('merge', {})
                if not merge.get('proof'):
                    raise WorkflowConflictError('Integrated capture requires actual merge proof')
                descriptor['merge_proof_digest'] = digest(merge['proof'])
            conn.execute('''INSERT INTO workflow_artifacts VALUES(?,?,?,?,?,?,?,?,?)''',
                         (identity, step_id, scope, step['packet_version'], step['attempt'],
                          _json(descriptor), digest(descriptor), time.time(), step['contract_revision']))
            return self._artifact(identity)

    def _artifact(self, artifact_id: str) -> dict:
        with self.db.read_connection() as conn:
            row = conn.execute('SELECT * FROM workflow_artifacts WHERE id=?', (artifact_id,)).fetchone()
        if row is None:
            raise NotFoundError(f'Artifact not found: {artifact_id}')
        result = _decode(row)
        if 'verification_manifest' in result['descriptor']:
            result['manifest_path'] = result['descriptor']['verification_manifest_path']
            result['manifest_digest'] = result['descriptor']['verification_manifest_digest']
        return result

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
        for key in ('verification_manifest', 'verification_manifest_path', 'verification_manifest_digest'):
            if key in descriptor:
                current[key] = descriptor[key]
        if 'verification_manifest' in descriptor:
            try:
                self._frozen_manifest(artifact)
            except (ValueError, OSError):
                return False
        return digest(current) == artifact['digest']

    @staticmethod
    def _frozen_manifest(artifact: dict) -> dict:
        descriptor = artifact['descriptor']
        manifest = descriptor['verification_manifest']
        if digest(manifest) != descriptor['verification_manifest_digest']:
            raise ValueError('Frozen verification manifest digest mismatch')
        path = Path(descriptor['verification_manifest_path'])
        if path.read_bytes() != _json(manifest).encode('utf-8'):
            raise ValueError('Frozen verification manifest file changed')
        for key in ('artifact_id', 'step_id', 'scope', 'packet_version', 'attempt', 'contract_revision'):
            expected = artifact['id'] if key == 'artifact_id' else artifact[key]
            if manifest[key] != expected:
                raise ValueError('Frozen verification manifest binding mismatch: ' + key)
        return manifest

    def _receipt_evidence(self, artifact: dict, receipt_files: list[str], *, require_success: bool) -> list[str]:
        from orchestrator.verify_runner import validate_receipt
        if not isinstance(receipt_files, list) or not receipt_files or any(not isinstance(p, str) for p in receipt_files):
            raise ValueError('workflow3 requires receipt_files')
        manifest = self._frozen_manifest(artifact)
        paths = [artifact['descriptor']['verification_manifest_path']]
        for receipt in receipt_files:
            paths.extend(str(p) for p in validate_receipt(manifest, Path(receipt), require_success=require_success))
        return list(dict.fromkeys(paths))

    def review(self, step_id: str, expected_revision: int, agent_id: str, criterion: str, evidence: str) -> dict:
        with self.db.transaction():
            step = self.get_step(step_id)
            run = self._run(step['run_id'])
            self._packet(step)
            self._bindings(step)
            if run['workflow_version'] not in {4, 5} or step['checkpoint'].get('complexity') != 'high':
                raise WorkflowConflictError('Challenger findings require frozen high complexity workflow4/5')
            if step['revision'] != expected_revision:
                raise WorkflowConflictError('Workflow revision conflict')
            if criterion not in step['criteria']:
                raise WorkflowConflictError('Finding must map to an existing criterion; revise new requirements explicitly')
            if not isinstance(evidence, str) or not evidence.strip():
                raise ValueError('Finding requires evidence')
            if agent_id in {step['owner_agent_id'], step['verifier_agent_id']} or not any(
                    w['agent_id'] == agent_id for w in self.db.list_workers(run_id=step['run_id'])):
                raise WorkflowConflictError('Challenger must be a registered independent native agent')
            if step['state'] not in {'candidate_review', 'integrated_review', 'done', 'handoff', 'ready_to_merge'}:
                raise WorkflowConflictError('Challenger finding requires a delivered candidate or integrated artifact')
            finding = {'id': _id('finding'), 'criterion': criterion, 'evidence': evidence,
                'agent_id': agent_id, 'attempt': step['attempt'], 'created_at': time.time(), 'status': 'open'}
            step['checkpoint'].setdefault('findings', []).append(finding)
            state = step['state']
            if state in {'done', 'handoff'}:
                state = 'integrated_review'
            elif state == 'ready_to_merge':
                state = 'candidate_review'
            self._set(step, state)
            return finding

    def record(self, step_id: str, artifact_id: str, verifier: str, verdict: str,
                criteria_results: list[dict], evidence_files: list[str], key: str, receipt_files: list[str] | None = None,
                review_dimensions: dict | None = None, resolved_finding_ids: list[str] | None = None,
                dismissed_finding_ids: list[str] | None = None) -> dict:
        if verdict not in {'PASS', 'FAIL'} or not isinstance(key, str) or not key.strip():
            raise ValueError('Verification requires PASS/FAIL and a nonempty stable key')
        request = {'artifact_id': artifact_id, 'verifier': verifier, 'verdict': verdict,
                   'criteria_results': criteria_results, 'evidence_files': evidence_files}
        if receipt_files is not None:
            request['receipt_files'] = receipt_files
        if review_dimensions is not None:
            request['review_dimensions'] = review_dimensions
        if resolved_finding_ids is not None:
            request['resolved_finding_ids'] = resolved_finding_ids
        if dismissed_finding_ids is not None:
            request['dismissed_finding_ids'] = dismissed_finding_ids
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
            if run['mode'] == 'boost' and artifact['scope'] == 'candidate':
                challenge = step['checkpoint'].get('challenge')
                if not challenge:
                    raise WorkflowConflictError('Independent challenge must be recorded before Boost candidate acceptance')
                if run['workflow_version'] in {2, 3, 4, 5} and (
                        challenge['agent_id'] != step['verifier_agent_id']
                        or challenge['artifact_id'] != artifact_id
                        or any(challenge[key] != step[key] for key in ('packet_version', 'attempt', 'contract_revision'))):
                    raise WorkflowConflictError('Independent challenge does not bind this exact candidate delivery')
            if not isinstance(criteria_results, list) or any(not isinstance(x, dict) or set(x) != {'criterion', 'status'} or x['status'] not in {'PASS', 'FAIL', 'NOT_RUN'} for x in criteria_results):
                raise ValueError('Invalid criteria results')
            if len(criteria_results) != len(step['criteria']) or {x['criterion'] for x in criteria_results} != set(step['criteria']):
                raise ValueError('Verification criteria are missing or duplicated')
            if verdict == 'PASS' and any(x['status'] != 'PASS' for x in criteria_results):
                raise ValueError('A PASS requires every criterion to pass')
            if review_dimensions is not None:
                if run['workflow_version'] not in {4, 5} or not isinstance(review_dimensions, dict) or set(review_dimensions) != {'specification', 'code', 'stress', 'evidence'} or any(
                        value not in {'PASS', 'FAIL', 'NOT_RUN'} for value in review_dimensions.values()):
                    raise ValueError('Review dimensions require workflow4/5 specification/code/stress/evidence statuses')
                if verdict == 'PASS' and 'FAIL' in review_dimensions.values():
                    raise WorkflowConflictError('Failed review dimension blocks PASS')
            if run['workflow_version'] in {4, 5} and step['checkpoint'].get('complexity') == 'high':
                if review_dimensions is None or (verdict == 'PASS' and any(value != 'PASS' for value in review_dimensions.values())):
                    raise WorkflowConflictError('High complexity requires all review dimensions and an actual PASS for each')
            resolutions = [] if resolved_finding_ids is None else resolved_finding_ids
            dismissals = [] if dismissed_finding_ids is None else dismissed_finding_ids
            for ids in (resolutions, dismissals):
                if not isinstance(ids, list) or any(not isinstance(value, str) for value in ids) or len(ids) != len(set(ids)):
                    raise ValueError('Finding IDs must be unique strings')
            if set(resolutions) & set(dismissals):
                raise ValueError('Finding cannot be both resolved and dismissed')
            findings = step['checkpoint'].get('findings', [])
            unresolved = [f for f in findings if f['status'] == 'open']
            closing = set(resolutions) | set(dismissals)
            if closing and (verdict != 'PASS' or not closing <= {f['id'] for f in unresolved}):
                raise WorkflowConflictError('Only current original-verifier PASS may resolve open mapped findings')
            if verdict == 'PASS' and {f['id'] for f in unresolved} != closing:
                raise WorkflowConflictError('Unresolved challenger findings block PASS')
            if any(f['id'] in dismissals and f.get('fail_verification_id') for f in findings):
                raise WorkflowConflictError('A substantiated FAIL cannot be dismissed or bypass its rework budget')
            if any(f['id'] in resolutions and (not f.get('fail_verification_id') or step['attempt'] <= f['attempt']) for f in findings):
                raise WorkflowConflictError('Confirmed finding requires original-verifier FAIL and same-author budgeted rework')
            if run['workflow_version'] in {3, 4, 5}:
                receipt_paths = self._receipt_evidence(artifact, receipt_files, require_success=verdict == 'PASS')
            elif receipt_files is not None:
                raise ValueError('receipt_files requires workflow3')
            else:
                receipt_paths = []
            manifest = capture_evidence(evidence_files + receipt_paths)
            receipts = {str(Path(p).resolve()) for p in (receipt_files or [])}
            for item in manifest:
                if item['path'] in receipts:
                    item['kind'] = 'verification_receipt'
            if not self._artifact_current(artifact):
                raise WorkflowConflictError('Verification artifact changed after capture')
            identity = _id('verification')
            conn.execute('''INSERT INTO verification_records VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                         (identity, step_id, artifact_id, key, step['packet_version'], step['attempt'],
                          verifier, step['verifier_agent_id'], verdict, _json(criteria_results),
                          _json(manifest), request_digest, time.time()))
            if review_dimensions is not None:
                step['checkpoint'].setdefault('review_dimensions', {})[identity] = review_dimensions
            for finding in unresolved:
                if verdict == 'FAIL' and any(x['criterion'] == finding['criterion'] and x['status'] == 'FAIL' for x in criteria_results):
                    finding['fail_verification_id'] = identity
                elif finding['id'] in resolutions:
                    finding.update(status='resolved', resolution_verification_id=identity)
                elif finding['id'] in dismissals:
                    finding.update(status='dismissed', resolution_verification_id=identity)
            if verdict == 'FAIL':
                if run['workflow_version'] in {4, 5}:
                    step['checkpoint']['last_failure_at'] = time.time()
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
        if any(f['status'] == 'open' for f in step['checkpoint'].get('findings', [])):
            raise WorkflowConflictError('Unresolved challenger findings block acceptance')
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
            if self.db.get_run(step['run_id'])['workflow_version'] in {3, 4, 5}:
                receipts = [item['path'] for item in v['evidence_manifest'] if item.get('kind') == 'verification_receipt']
                try:
                    paths = self._receipt_evidence(artifact, receipts, require_success=True)
                except (ValueError, OSError) as exc:
                    raise WorkflowConflictError('Approved receipt is stale: ' + str(exc)) from exc
                if not set(paths) <= {item['path'] for item in v['evidence_manifest']}:
                    raise WorkflowConflictError('Receipt outputs are missing from original evidence chain')
            return artifact
        raise WorkflowConflictError(f'No current {scope} independent PASS')

    def find_step_for_workspace(self, workspace_id: str) -> dict | None:
        ws = self.db.get_workspace(workspace_id)
        if ws is None:
            raise NotFoundError(f'Workspace not found: {workspace_id}')
        run = self.db.get_run(ws['run_id'])
        if run['workflow_version'] == 0:
            return None
        self._run(ws['run_id'], active=False)
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
            if self.db.get_run(step['run_id'])['workflow_version'] in {4, 5}:
                admission = data.get('admission')
                if admission and admission['dependencies']:
                    self._admitted_manifest(step)
                checkpoint['observed_before_manifest'] = capture_content(self._project_root(step['run_id']))['manifest']
                if checkpoint['mode'] == 'snapshot':
                    before, after = checkpoint['observed_before_manifest'], checkpoint['desired_manifest']
                    changes = {p for p in set(before) | set(after) if before.get(p) != after.get(p)}
                else:
                    from orchestrator.artifacts import git
                    changes = git(self._project_root(step['run_id']), '-c', 'core.quotePath=false',
                        'diff', '--name-only', checkpoint['target_before_commit'], checkpoint['desired_tree']).splitlines()
                if any(not contains(self._scopes(step), p) for p in changes):
                    raise WorkflowConflictError('Planned merge exceeds frozen ownership')
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
            if self.db.get_run(step['run_id'])['workflow_version'] in {4, 5}:
                before = checkpoint['merge']['observed_before_manifest']
                after = capture_content(self._project_root(step['run_id']))['manifest']
                if proof['mode'] == 'snapshot' and proof['target_manifest'] != after:
                    raise WorkflowConflictError('Actual target changed after merge proof capture; reconciliation required')
                if proof['mode'] == 'git_clone':
                    from orchestrator.artifacts import git
                    root = self._project_root(step['run_id'])
                    if git(root, 'status', '--porcelain') or git(root, 'rev-parse', 'HEAD') != proof['target_commit'] or git(root, 'rev-parse', 'HEAD^{tree}') != proof['target_tree']:
                        raise WorkflowConflictError('Actual Git target changed after merge proof capture; reconciliation required')
                changes = {p for p in set(before) | set(after) if before.get(p) != after.get(p)}
                if any(not contains(self._scopes(step), p) for p in changes):
                    raise WorkflowConflictError('Actual merge exceeds frozen ownership')
                checkpoint.setdefault('merge_history', []).append({
                    **{key: step[key] for key in ('packet_version', 'contract_revision', 'attempt', 'owner_agent_id')},
                    'merge_id': proof['merge_id'], 'before_manifest': before,
                    'after_manifest': after, 'proof': proof, 'allowed_scopes': self._scopes(step), 'completed_at': time.time()})
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
            artifacts = [dict(r) for r in conn.execute('''SELECT a.id,a.step_id,a.scope,a.digest,a.created_at,a.descriptor_json FROM workflow_artifacts a
                JOIN workflow_steps s ON s.id=a.step_id WHERE s.run_id=? ORDER BY a.created_at''', (run_id,))]
        for artifact in artifacts:
            descriptor = json.loads(artifact.pop('descriptor_json'))
            if run['workflow_version'] in {3, 4, 5} and 'verification_manifest' in descriptor:
                artifact['evidence_level'] = 'local_collector_v1'
                artifact['verification_command_count'] = len(descriptor['verification_manifest']['commands'])
        from orchestrator.board import _strip_step_manifests
        compact_steps = [_strip_step_manifests(s) for s in steps]
        # Dashboard reads stored observations only; gate use rehashes actual artifacts and evidence.
        blockers = [{'step_id': s['id'], 'state': s['state']} for s in compact_steps if s['state'] != 'done']
        return {'steps': compact_steps, 'verifications': records, 'artifacts': artifacts,
                'blockers': blockers, 'max_reworks': MAX_REWORKS,
                'workflow_version': run['workflow_version'], 'artifact_validity': 'checked_at_gate_use'}
