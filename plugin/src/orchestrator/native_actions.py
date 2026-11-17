"""Durable root-coordinator handoff ledger. This module never invokes host tools."""
from __future__ import annotations
import hashlib
import json
import time
import uuid
from orchestrator.db import Database, NotFoundError
from orchestrator.inbox import InboxStore, InboxConflictError

class NativeActionConflictError(InboxConflictError):
    """The frozen source or native claim no longer permits this operation."""

def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()

def install_schema(conn):
    conn.execute('CREATE TABLE IF NOT EXISTS native_actions (\n        action_id TEXT PRIMARY KEY, stable_key TEXT NOT NULL UNIQUE,\n        run_id TEXT NOT NULL REFERENCES runs(id), source_type TEXT NOT NULL, source_id TEXT NOT NULL,\n        recipient TEXT NOT NULL, action_type TEXT NOT NULL, source_digest TEXT NOT NULL,\n        content_digest TEXT NOT NULL, snapshot_json TEXT NOT NULL, status TEXT NOT NULL,\n        revision INTEGER NOT NULL, claim_token TEXT, lease_expires_at REAL,\n        outcome TEXT, evidence_ref TEXT, evidence_digest TEXT, result TEXT,\n        history_json TEXT NOT NULL, updated_at REAL NOT NULL)')

class NativeActionStore:

    def __init__(self, db: Database):
        self.db = db

    def get(self, action_id):
        with self.db.read_connection() as conn:
            row = conn.execute('SELECT * FROM native_actions WHERE action_id=?', (action_id,)).fetchone()
        if row is None:
            raise NotFoundError(f'Native action not found: {action_id}')
        value = dict(row)
        value['history'] = json.loads(value.pop('history_json'))
        value['snapshot'] = json.loads(value.pop('snapshot_json'))
        return value

    def _source(self, conn, run_id, source_type, source_id, recipient, action_type, *, stage=True):
        run = conn.execute('SELECT * FROM runs WHERE id=?', (run_id,)).fetchone()
        if run is None:
            raise NotFoundError(f'Run not found: {run_id}')
        if run['status'] != 'active':
            raise NativeActionConflictError('Run is not active')
        worker = conn.execute('SELECT * FROM workers WHERE run_id=? AND name=?', (run_id, recipient)).fetchone()
        if worker is None or not worker['agent_id']:
            raise NativeActionConflictError('Recipient requires native binding')
        step = None
        if source_type == 'inbox':
            row = conn.execute('SELECT * FROM inbox_messages WHERE id=? AND run_id=?', (source_id, run_id)).fetchone()
            if row is None:
                raise NotFoundError('Inbox source not found')
            source = dict(row)
            if source['recipient'] != recipient or (stage and source['status'] != 'pending'):
                raise NativeActionConflictError('Inbox source recipient/status changed')
            InboxStore._require_binding(source, worker['agent_id'])
            if source['kind'] != 'notice':
                if not source['step_id']:
                    raise NativeActionConflictError('Business handoff requires an admitted workflow step')
                step = dict(InboxStore._step_context(conn, source['step_id']))
                step['revision'] = conn.execute('SELECT revision FROM workflow_steps WHERE id=?', (source['step_id'],)).fetchone()[0]
                if any((source['step_' + name] is None for name in ('packet_version', 'attempt', 'contract_revision'))):
                    raise NativeActionConflictError('Legacy business source lacks frozen context; cancel and reissue original message')
                InboxStore._require_step_delivery(source, step, stage=stage)
            if action_type != 'followup_task':
                raise ValueError('Inbox action_type must be followup_task')
        elif source_type == 'control':
            row = conn.execute('SELECT * FROM worker_controls WHERE id=? AND run_id=?', (source_id, run_id)).fetchone()
            if row is None:
                raise NotFoundError('Control source not found')
            source = dict(row)
            if source['worker_id'] != worker['id'] or source['agent_id'] != worker['agent_id'] or (stage and source['status'] != 'pending'):
                raise NativeActionConflictError('Control source or binding changed')
            if action_type != 'interrupt_agent':
                raise ValueError('Control action_type must be interrupt_agent')
        elif source_type == 'human':
            if not isinstance(source_id, str) or not source_id.isascii() or (not source_id.isdecimal()) or source_id.startswith('0'):
                raise ValueError('Human source_id must be canonical positive decimal seq')
            row = conn.execute("SELECT * FROM board_messages WHERE seq=? AND run_id=? AND msg_type='human'", (source_id, run_id)).fetchone()
            if row is None:
                raise NotFoundError('Human source not found')
            source = dict(row)
            mentions = json.loads(source['mentions_json'])
            if mentions != [recipient]:
                raise NativeActionConflictError('Human recipient needs clarification; not broadcasting')
            if action_type != 'send_message':
                raise ValueError('Human action_type must be send_message')
        else:
            raise ValueError('source_type must be inbox/control/human')
        if source_type != 'control':
            if worker['status'] in {'stopped', 'killed'}:
                raise NativeActionConflictError('Recipient is stopped')
            if conn.execute("SELECT 1 FROM worker_controls WHERE worker_id=? AND status='pending'", (worker['id'],)).fetchone():
                raise NativeActionConflictError('Recipient has pending control')
        packet = conn.execute('SELECT MAX(version) FROM task_packets WHERE run_id=?', (run_id,)).fetchone()[0]
        snapshot = dict(source=source, step=step, agent_id=worker['agent_id'], packet_version=packet, worker_status=worker['status'], workflow_version=run['workflow_version'])
        version = [packet, source.get('step_attempt'), source.get('step_contract_revision')]
        key = digest([source_type, str(source_id), run_id, version, recipient, worker['agent_id'], action_type])
        return (snapshot, key, digest(snapshot), digest(source))

    def _fence(self, conn, where, args):
        rows = conn.execute("SELECT action_id,history_json,status,snapshot_json FROM native_actions WHERE status IN ('claimed','invoking') AND " + where, args).fetchall()
        fenced = {row['action_id']: row for row in rows}
        for row in rows:
            group = json.loads(row['snapshot_json']).get('native_group')
            if group:
                placeholders = ','.join('?' for _ in group['members'])
                for member in conn.execute("SELECT action_id,history_json,status,snapshot_json FROM native_actions WHERE status IN ('claimed','invoking') AND action_id IN (" + placeholders + ')', tuple(group['members'])):
                    fenced[member['action_id']] = member
        for row in fenced.values():
            history = json.loads(row['history_json']) + [dict(operation='fence', previous_status=row['status'], reason='restart' if where == '1=1' else 'lease_expired', at=time.time())]
            conn.execute("UPDATE native_actions SET status='needs_reconciliation',revision=revision+1,claim_token=NULL,lease_expires_at=NULL,history_json=?,updated_at=? WHERE action_id=?", (json.dumps(history), time.time(), row['action_id']))

    def recover(self):
        """Called once at daemon start, never by a read-only projection."""
        with self.db.transaction() as conn:
            self._fence(conn, '1=1', ())

    def expire(self):
        with self.db.transaction() as conn:
            self._fence(conn, 'lease_expires_at<=?', (time.time(),))

    def update(self, **arguments):
        if arguments.get('action') in {'claim_group', 'begin_group', 'record_group', 'reconcile_group'}:
            return self._update_group(**arguments)
        # Commit any lease fence before surfacing the rejected stale claim.
        result = self._update_transaction(**arguments)
        if result is None:
            raise NativeActionConflictError('Native lease expired; claim is fenced for reconciliation')
        return result

    def _update_transaction(self, action, run_id=None, source_type=None, source_id=None, recipient=None, action_type=None, source_digest=None, action_id=None, expected_revision=None, claim_token=None, outcome=None, evidence_ref=None, evidence_digest=None, result=None, cancel=False, _group_id=None):
        if action not in {'claim', 'begin', 'record', 'reconcile'}:
            raise ValueError('Unknown native action operation')
        if not isinstance(cancel, bool) or (cancel and (action != 'reconcile' or outcome not in {'not_called', 'rejected'})):
            raise ValueError('cancel requires reconcile with definite not_called/rejected evidence')
        # A later rejected CAS must not roll back the expired-token fence.
        if _group_id is None:
            self.expire()
        with self.db.transaction() as conn:
            if action == 'claim':
                expected = source_digest
                if not all((isinstance(x, str) and x.strip() for x in (run_id, source_type, source_id, recipient, action_type, expected))):
                    raise ValueError('claim requires source identity and source_digest')
                snapshot, key, current, content = self._source(conn, run_id, source_type, source_id, recipient, action_type)
                if snapshot['workflow_version'] == 4 and source_type != 'control':
                    if source_type == 'inbox' and snapshot['source']['kind'] == 'notice':
                        raise NativeActionConflictError('Notice-only delivery does not warrant a native wake')
                    if _group_id is None and self._busy(conn, run_id, recipient, snapshot['agent_id']):
                        raise NativeActionConflictError('Recipient has an in-flight wake requiring completion or reconciliation')
                if current != expected:
                    raise NativeActionConflictError('Native source digest changed')
                prior = conn.execute('SELECT stable_key,status,outcome FROM native_actions WHERE source_type=? AND source_id=? AND run_id=?', (source_type, source_id, run_id)).fetchall()
                if any((r['stable_key'] != key for r in prior)):
                    raise NativeActionConflictError('Native source already has a different frozen version or binding; reconcile original action')
                old = conn.execute('SELECT * FROM native_actions WHERE stable_key=?', (key,)).fetchone()
                if old:
                    if old['content_digest'] != content:
                        raise NativeActionConflictError('Same stable key has different source contents')
                    if old['status'] != 'failed' or old['outcome'] not in {'not_called', 'rejected'}:
                        raise NativeActionConflictError('Native action already claimed or requires reconciliation')
                    action_id = old['action_id']
                    revision = old['revision'] + 1
                    history = json.loads(old['history_json'])
                else:
                    action_id = 'nat_' + uuid.uuid4().hex
                    revision = 1
                    history = []
                token = uuid.uuid4().hex
                now = time.time()
                history.append(dict(operation='claim', revision=revision, at=now))
                conn.execute("INSERT INTO native_actions VALUES (?,?,?,?,?,?,?,?,?,?, 'claimed',?,?,?,NULL,NULL,NULL,NULL,?,?)\n                    ON CONFLICT(stable_key) DO UPDATE SET source_digest=excluded.source_digest,snapshot_json=excluded.snapshot_json,\n                    status='claimed',revision=excluded.revision,claim_token=excluded.claim_token,lease_expires_at=excluded.lease_expires_at,\n                    history_json=excluded.history_json,updated_at=excluded.updated_at", (action_id, key, run_id, source_type, source_id, recipient, action_type, current, content, json.dumps(snapshot, ensure_ascii=False), revision, token, now + 60, json.dumps(history), now))
            else:
                row = self.get(action_id)
                if row['snapshot'].get('native_group', {}).get('id') != _group_id:
                    raise NativeActionConflictError('Frozen group member requires the complete group operation')
                if _group_id is None and action != 'reconcile' and row['status'] in {'claimed', 'invoking'} and (row['lease_expires_at'] <= time.time()):
                    self._fence(conn, 'action_id=?', (action_id,))
                    return None
                if expected_revision != row['revision']:
                    raise NativeActionConflictError('Stale native action revision')
                if action != 'reconcile' and (not claim_token or row['claim_token'] != claim_token):
                    raise NativeActionConflictError('Stale native claim token')
                source_race = None
                if action == 'begin':
                    if row['status'] != 'claimed':
                        raise NativeActionConflictError('Native action is not claimed')
                    _, _, current, _ = self._source(conn, row['run_id'], row['source_type'], row['source_id'], row['recipient'], row['action_type'])
                    if current != row['source_digest']:
                        raise NativeActionConflictError('Native source changed before begin')
                    status = 'invoking'
                else:
                    allowed = {'invoking'} if action == 'record' else {'needs_reconciliation'}
                    if row['status'] not in allowed:
                        raise NativeActionConflictError('Native result requires invoking/reconciliation state')
                    if outcome not in {'confirmed', 'failed', 'not_called', 'rejected', 'unknown'}:
                        raise ValueError('Invalid native outcome')
                    if not all((isinstance(x, str) and x.strip() for x in (evidence_ref, result))):
                        raise ValueError('Native result requires evidence_ref and actual result')
                    if not isinstance(evidence_digest, str) or len(evidence_digest) != 64 or any((c not in '0123456789abcdef' for c in evidence_digest)):
                        raise ValueError('evidence_digest must be SHA256 hex')
                    status = 'needs_reconciliation' if outcome == 'unknown' else 'confirmed' if outcome == 'confirmed' else 'failed'
                    if cancel:
                        status = 'cancelled'
                    if action == 'record':
                        try:
                            current, _, _, _ = self._source(conn, row['run_id'], row['source_type'], row['source_id'], row['recipient'], row['action_type'], stage=False)
                            frozen = row['snapshot']
                            invariant = ('agent_id', 'packet_version', 'workflow_version')
                            if any((current[k] != frozen[k] for k in invariant)):
                                source_race = 'Source binding/version changed after begin'
                            mutable = {'status', 'receipts_json', 'result', 'resolved_at'} if row['source_type'] == 'control' else {'status', 'receipts_json'}
                            if {k: v for k, v in current['source'].items() if k not in mutable} != {k: v for k, v in frozen['source'].items() if k not in mutable}:
                                source_race = 'Source content changed after begin'
                            if current['step'] and any((current['step'][k] != frozen['step'][k] for k in ('ctx_packet_version', 'ctx_attempt', 'ctx_contract_revision', 'ctx_owner_agent_id', 'ctx_verifier_agent_id'))):
                                source_race = 'Step contract changed after begin'
                        except (InboxConflictError, ValueError, NotFoundError) as exc:
                            source_race = str(exc)
                        if source_race:
                            status = 'needs_reconciliation'
                history = row['history'] + [dict(operation=action, source_race=source_race, outcome=outcome, evidence_ref=evidence_ref, evidence_digest=evidence_digest, result=result, at=time.time())]
                conn.execute('UPDATE native_actions SET status=?,revision=revision+1,claim_token=?,lease_expires_at=?,outcome=?,evidence_ref=?,evidence_digest=?,result=?,history_json=?,updated_at=? WHERE action_id=?', (status, row['claim_token'] if action == 'begin' else None, row['lease_expires_at'] if action == 'begin' else None, outcome, evidence_ref, evidence_digest, result, json.dumps(history, ensure_ascii=False), time.time(), action_id))
            return self.get(action_id)

    @staticmethod
    def _busy(conn, run_id, recipient, agent_id):
        rows = conn.execute("SELECT snapshot_json FROM native_actions WHERE run_id=? AND recipient=? AND source_type!='control' AND status IN ('claimed','invoking','needs_reconciliation')", (run_id, recipient))
        return any(json.loads(row['snapshot_json'])['agent_id'] == agent_id for row in rows)

    def _update_group(self, action, members, run_id=None, recipient=None, action_type=None,
                      group_id=None, outcome=None, evidence_ref=None, evidence_digest=None,
                      result=None, cancel=False):
        """Freeze explicit member IDs, then CAS the entire native call in one transaction.

        Each source keeps its own ledger row and business ACK. No query or group
        transition advances an inbox cursor. Complexity is O(frozen members).
        """
        if not isinstance(members, list) or not members or len(members) > 50:
            raise ValueError('Group members must contain between 1 and 50 explicit entries')
        operation = action.removesuffix('_group')
        required = {'source_type', 'source_id', 'source_digest'} if operation == 'claim' else {'action_id', 'expected_revision'}
        if operation not in {'claim', 'reconcile'}:
            required |= {'claim_token'}
        allowed = required if operation == 'claim' else required | {'claim_token'}
        if any(not isinstance(m, dict) or not required <= set(m) or set(m) - allowed for m in members):
            raise ValueError('Invalid explicit group member fields')
        identities = [(m['source_type'], m['source_id']) if operation == 'claim' else m['action_id'] for m in members]
        if any(not isinstance(identity, (str, tuple)) for identity in identities):
            raise ValueError('Group member identities must be strings')
        if operation != 'claim' and any(type(m['expected_revision']) is not int for m in members):
            raise ValueError('Group member expected_revision must be an integer')
        if len(set(identities)) != len(identities):
            raise ValueError('Group members must be distinct')
        if operation == 'claim' and any(m['source_type'] not in {'inbox', 'human'} for m in members):
            raise ValueError('Only business/human handoffs may be grouped; controls remain separate')
        self.expire()
        expired = False
        with self.db.transaction() as conn:
            if operation == 'claim':
                run = conn.execute('SELECT workflow_version FROM runs WHERE id=?', (run_id,)).fetchone()
                if run is None:
                    raise NotFoundError(f'Run not found: {run_id}')
                if run['workflow_version'] != 4:
                    raise NativeActionConflictError('Grouped handoff requires workflow4')
                group_id = 'ng_' + uuid.uuid4().hex
                first = self._source(conn, run_id, members[0]['source_type'], members[0]['source_id'], recipient, action_type)[0]
                if self._busy(conn, run_id, recipient, first['agent_id']):
                    raise NativeActionConflictError('Recipient has an in-flight wake requiring completion or reconciliation')
                rows = [self._update_transaction(action='claim', run_id=run_id, recipient=recipient,
                            action_type=action_type, _group_id=group_id, **member) for member in members]
                frozen = dict(id=group_id, members=[row['action_id'] for row in rows])
                for row in rows:
                    snapshot = row['snapshot'] | {'native_group': frozen}
                    conn.execute('UPDATE native_actions SET snapshot_json=? WHERE action_id=?',
                                 (json.dumps(snapshot, ensure_ascii=False), row['action_id']))
            else:
                if not isinstance(group_id, str) or not group_id.strip():
                    raise ValueError('Group operation requires group_id')
                rows = [self.get(member['action_id']) for member in members]
                frozen = rows[0]['snapshot'].get('native_group')
                if not frozen or frozen['id'] != group_id or set(frozen['members']) != set(identities):
                    raise NativeActionConflictError('Explicit members do not match the frozen group membership')
                if any(row['snapshot'].get('native_group') != frozen for row in rows):
                    raise NativeActionConflictError('Frozen group membership changed')
                if operation != 'reconcile' and any(row['status'] in {'claimed', 'invoking'} and row['lease_expires_at'] <= time.time() for row in rows):
                    placeholders = ','.join('?' for _ in identities)
                    self._fence(conn, 'action_id IN (' + placeholders + ')', tuple(identities))
                    expired = True
                if not expired:
                    rows = [self._update_transaction(action=operation, _group_id=group_id,
                                outcome=outcome, evidence_ref=evidence_ref, evidence_digest=evidence_digest,
                                result=result, cancel=cancel, **member) for member in members]
                    # One external call covers every member; uncertainty belongs to all.
                    if any(row['status'] == 'needs_reconciliation' for row in rows):
                        for row in rows:
                            if row['status'] != 'needs_reconciliation':
                                history = row['history']
                                history[-1]['group_uncertainty'] = 'Another frozen member changed during the same native call'
                                conn.execute("UPDATE native_actions SET status='needs_reconciliation',history_json=? WHERE action_id=?", (json.dumps(history, ensure_ascii=False), row['action_id']))
            answer = dict(group_id=group_id, members=[self.get(m['action_id']) for m in rows])
        if expired:
            raise NativeActionConflictError('Native group lease expired; all members require reconciliation')
        return answer

    def project(self, run_id, limit=50):
        """Read-only, bounded output; scan sources without loading their full history.

        Filtering precedes output limiting so old ambiguous sources cannot hide newer work.
        Complexity is O(outstanding sources); each retained group has at most 50 members.
        """
        actions, clarification = ([], [])
        action_count = clarification_count = 0

        def clarify(value):
            nonlocal clarification_count
            clarification_count += 1
            if len(clarification) < limit:
                clarification.append(value)
        with self.db.read_connection() as conn:
            run = conn.execute('SELECT workflow_version FROM runs WHERE id=?', (run_id,)).fetchone()
            if run is None:
                raise NotFoundError(f'Run not found: {run_id}')
            group_keys, groups = set(), {}
            queries = (('control', "SELECT c.id AS source_id,w.name AS recipient FROM worker_controls c JOIN workers w ON w.id=c.worker_id WHERE c.run_id=? AND c.status='pending' ORDER BY c.created_at", 'interrupt_agent'), ('inbox', "SELECT id AS source_id,recipient,kind FROM inbox_messages WHERE run_id=? AND status='pending' ORDER BY board_seq", 'followup_task'), ('human', "SELECT seq AS source_id,mentions_json,summary,body_md FROM board_messages WHERE run_id=? AND msg_type='human' ORDER BY seq", 'send_message'))
            for typ, query, act in queries:
                for source_row in conn.execute(query, (run_id,)):
                    if run['workflow_version'] == 4 and typ == 'inbox' and source_row['kind'] == 'notice':
                        continue
                    sid = str(source_row['source_id'])
                    prior = conn.execute('SELECT stable_key,status,outcome,content_digest FROM native_actions WHERE run_id=? AND source_type=? AND source_id=?', (run_id, typ, sid)).fetchall()
                    if prior and any((not (r['status'] == 'failed' and r['outcome'] in {'not_called', 'rejected'}) for r in prior)):
                        continue
                    if typ == 'human':
                        mentions = json.loads(source_row['mentions_json'])
                        if len(mentions) != 1:
                            clarify(dict(source_type=typ, source_id=sid, seq=source_row['source_id'], summary=source_row['summary'], body_md=source_row['body_md'], reason='Recipient is ambiguous'))
                            continue
                        target = mentions[0]
                    else:
                        target = source_row['recipient']
                    try:
                        snapshot, key, current, content = self._source(conn, run_id, typ, sid, target, act)
                    except (InboxConflictError, ValueError, NotFoundError) as exc:
                        clarify(dict(source_type=typ, source_id=sid, recipient=target, reason=str(exc)))
                        continue
                    if prior and any(r['stable_key'] != key for r in prior):
                        clarify(dict(source_type=typ, source_id=sid, reason='Original native action has a different frozen version/binding; reconcile original source'))
                        continue
                    if prior and any((r['content_digest'] != content for r in prior)):
                        clarify(dict(source_type=typ, source_id=sid, reason='Same stable key has different source contents'))
                        continue
                    item = dict(run_id=run_id, source_type=typ, source_id=sid, recipient=target, action_type=act, source_digest=current, agent_id=snapshot['agent_id'], source=snapshot['source'])
                    if run['workflow_version'] == 4 and typ != 'control' and not self._busy(conn, run_id, target, snapshot['agent_id']):
                        key = (target, snapshot['agent_id'], act)
                        group_keys.add(key)
                        # Busy sources must not consume another recipient's bounded projection.
                        if key not in groups and len(groups) < limit:
                            groups[key] = dict(run_id=run_id, recipient=key[0], agent_id=key[1], action_type=key[2], members=[])
                        if key in groups and len(groups[key]['members']) < 50:
                            groups[key]['members'].append(item)
                    action_count += 1
                    if len(actions) < limit:
                        actions.append(item)
            reconcile_sql = "FROM native_actions WHERE run_id=? AND (status='needs_reconciliation' OR (status IN ('claimed','invoking') AND lease_expires_at<=?))"
            args = (run_id, time.time())
            reconciliation_count = conn.execute('SELECT count(*) ' + reconcile_sql, args).fetchone()[0]
            rows = conn.execute('SELECT action_id,status,revision,source_type,source_id,recipient,lease_expires_at ' + reconcile_sql + ' ORDER BY updated_at LIMIT ?', (*args, limit)).fetchall()
            counts = dict.fromkeys(('claimed', 'invoking', 'confirmed', 'failed', 'needs_reconciliation', 'cancelled'), 0)
            counts.update({r['status']: r['n'] for r in conn.execute('SELECT status,count(*) n FROM native_actions WHERE run_id=? GROUP BY status', (run_id,))})
            recovery_groups = {}
            recovery_group_ids = set()
            for row in conn.execute("SELECT snapshot_json FROM native_actions WHERE run_id=? AND status IN ('claimed','invoking','needs_reconciliation') ORDER BY updated_at", (run_id,)):
                frozen = json.loads(row['snapshot_json']).get('native_group')
                if not frozen:
                    continue
                recovery_group_ids.add(frozen['id'])
                if frozen['id'] not in recovery_groups and len(recovery_groups) < limit:
                    recovery_groups[frozen['id']] = dict(group_id=frozen['id'], members=[self.get(identity) for identity in frozen['members']])
            reconciliation = []
            for row in rows:
                item = dict(row)
                frozen = self.get(row['action_id'])['snapshot'].get('native_group')
                if frozen:
                    item.update(group_id=frozen['id'], frozen_member_ids=frozen['members'])
                reconciliation.append(item)
        return dict(native_action_groups=list(recovery_groups.values()), native_action_groups_count=len(recovery_group_ids), native_action_groups_has_more=len(recovery_group_ids) > limit, next_action_groups=list(groups.values()), next_action_groups_count=len(group_keys) if run['workflow_version'] == 4 else 0, limit=limit, counts=counts, counts_complete=True, next_actions_count=action_count, needs_reconciliation_count=reconciliation_count, needs_clarification_count=clarification_count, next_actions=actions, needs_reconciliation=reconciliation, needs_clarification=clarification, native_actions_has_more=any((n > limit for n in (action_count, reconciliation_count, clarification_count))))
