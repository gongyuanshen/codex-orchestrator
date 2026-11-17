"""M5 durable grouped native handoffs: actual SQLite transactions, no host calls."""
from concurrent.futures import ThreadPoolExecutor

import pytest

from orchestrator.board import BoardService
from orchestrator.config import Settings
from orchestrator.db import Database
from orchestrator.inbox import InboxConflictError, InboxStore
from orchestrator.native_actions import NativeActionConflictError, NativeActionStore
from orchestrator.workflow import WorkflowService


@pytest.fixture
def grouped(tmp_path):
    db = Database(settings=Settings(data_dir=tmp_path / "state"))
    board = BoardService(db)
    run = db.create_run(mode="swarm", title="grouped", project_root=tmp_path)
    # Set up the existing admitted contract before selecting the new protocol.
    with db.transaction() as conn:
        conn.execute("UPDATE runs SET workflow_version=3 WHERE id=?", (run['id'],))
    for name, role in [('author', 'coder'), ('reviewer', 'verifier')]:
        board.worker_register(run['id'], name, role, agent_id='/root/' + name)
    db.save_task_packet(run['id'], title='work', goal='work',
                        file_ownership={'author': ['source.txt']}, acceptance_criteria=['one'])
    flow = WorkflowService(db)
    step = flow.step_save(run['id'], 'one', 'author', 'reviewer')
    step = flow.step_advance(step['id'], 'dispatch', step['revision'])
    with db.transaction() as conn:
        conn.execute("UPDATE runs SET workflow_version=4 WHERE id=?", (run['id'],))
    inbox = InboxStore(db, board)

    def send(key, kind='task'):
        return inbox.send(run['id'], 'root', 'author', key, kind, key, 'body ' + key,
                          step_id=step['id'] if kind != 'notice' else None)['message']

    send('one'); send('two')
    yield db, board, run, inbox, NativeActionStore(db), send
    db.close()


def claim_group(grouped, members=None):
    _, _, run, _, store, _ = grouped
    group = store.project(run['id'])['next_action_groups'][0]
    return store.update(action='claim_group', run_id=run['id'], recipient=group['recipient'],
                        action_type=group['action_type'], members=members or [
                            {k: m[k] for k in ('source_type', 'source_id', 'source_digest')}
                            for m in group['members']])


def advance_group(store, group, operation, **kwargs):
    return store.update(action=operation, group_id=group['group_id'], members=[
        dict(action_id=m['action_id'], expected_revision=m['revision'], claim_token=m['claim_token'])
        for m in group['members']], **kwargs)


def evidence(outcome='confirmed'):
    return dict(outcome=outcome, evidence_ref='native:actual-result', evidence_digest='a' * 64,
                result='one native wake covered both frozen message IDs')


def test_group_preserves_individual_sources_and_acks(grouped):
    db, board, run, inbox, store, _ = grouped
    before = db.connect().total_changes
    projection = store.project(run['id'])
    assert db.connect().total_changes == before
    assert len(projection['next_action_groups']) == 1
    assert len(projection['next_action_groups'][0]['members']) == 2
    group = claim_group(grouped)
    assert len({m['action_id'] for m in group['members']}) == 2
    begun = advance_group(store, group, 'begin_group')
    result = advance_group(store, begun, 'record_group', **evidence())
    assert all(m['status'] == 'confirmed' for m in result['members'])
    assert all(m['evidence_ref'] == 'native:actual-result' for m in result['members'])
    ids = [m['source_id'] for m in result['members']]
    assert all(inbox.get(mid)['status'] == 'pending' for mid in ids)
    inbox.ack(ids[0], 'author', 'accepted', 'received first ID')
    assert inbox.get(ids[1])['status'] == 'pending'


def test_concurrent_groups_allow_only_one_wake(grouped):
    _, _, run, _, store, _ = grouped
    projected = store.project(run['id'])['next_action_groups'][0]
    members = [{k: m[k] for k in ('source_type', 'source_id', 'source_digest')}
               for m in projected['members']]
    def claim():
        try:
            return store.update(action='claim_group', run_id=run['id'], recipient='author',
                action_type='followup_task', members=members)['group_id']
        except NativeActionConflictError:
            return 'conflict'
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda _: claim(), range(2)))
    assert results.count('conflict') == 1


def test_arrivals_are_not_lost_or_claimed_while_wake_inflight(grouped):
    _, _, run, _, store, send = grouped
    group = claim_group(grouped)
    later = send('later')
    assert store.project(run['id'])['next_action_groups'] == []
    item = next(a for a in store.project(run['id'])['next_actions'] if a['source_id'] == later['id'])
    with pytest.raises(NativeActionConflictError, match='in.flight'):
        store.update(action='claim', **{k: item[k] for k in
                     ('run_id', 'source_type', 'source_id', 'recipient', 'action_type', 'source_digest')})
    begun = advance_group(store, group, 'begin_group')
    advance_group(store, begun, 'record_group', **evidence())
    pending = store.project(run['id'])['next_action_groups']
    assert [m['source_id'] for m in pending[0]['members']] == [later['id']]


def test_group_claim_rolls_back_if_one_digest_changed(grouped):
    db, _, run, _, store, _ = grouped
    projected = store.project(run['id'])['next_action_groups'][0]
    members = [{k: m[k] for k in ('source_type', 'source_id', 'source_digest')}
               for m in projected['members']]
    members[-1]['source_digest'] = '0' * 64
    with pytest.raises(NativeActionConflictError, match='digest'):
        claim_group(grouped, members)
    with db.read_connection() as conn:
        assert conn.execute('SELECT count(*) FROM native_actions').fetchone()[0] == 0


def test_frozen_members_cannot_be_omitted_or_individually_begun(grouped):
    _, _, _, _, store, _ = grouped
    group = claim_group(grouped)
    with pytest.raises(NativeActionConflictError, match='member'):
        advance_group(store, group | {'members': group['members'][:1]}, 'begin_group')
    m = group['members'][0]
    with pytest.raises(NativeActionConflictError, match='group'):
        store.update(action='begin', action_id=m['action_id'], expected_revision=m['revision'],
                     claim_token=m['claim_token'])
    assert all(store.get(m['action_id'])['status'] == 'claimed' for m in group['members'])


@pytest.mark.parametrize('race', ['control', 'stopped', 'rebind', 'source'])
def test_group_begin_revalidates_every_source_atomically(grouped, race):
    db, _, run, _, store, _ = grouped
    group = claim_group(grouped)
    worker = db.get_worker_by_name(run['id'], 'author')
    if race == 'control':
        db.request_worker_control(worker['id'], 'stopped')
    else:
        with db.transaction() as conn:
            if race == 'stopped':
                conn.execute("UPDATE workers SET status='stopped' WHERE id=?", (worker['id'],))
            elif race == 'rebind':
                conn.execute("UPDATE workers SET agent_id='different' WHERE id=?", (worker['id'],))
            else:
                conn.execute("UPDATE inbox_messages SET body_md='different' WHERE id=?",
                             (group['members'][-1]['source_id'],))
    with pytest.raises(InboxConflictError):
        advance_group(store, group, 'begin_group')
    assert all(store.get(m['action_id'])['status'] == 'claimed' for m in group['members'])


@pytest.mark.parametrize('cause', ['restart', 'unknown', 'expired'])
def test_uncertain_wake_fences_new_arrivals_without_replay(grouped, cause):
    db, _, run, _, store, send = grouped
    group = advance_group(store, claim_group(grouped), 'begin_group')
    if cause == 'restart':
        store.recover()
    elif cause == 'unknown':
        advance_group(store, group, 'record_group', **evidence('unknown'))
    else:
        with db.transaction() as conn:
            conn.execute('UPDATE native_actions SET lease_expires_at=0')
        with pytest.raises(NativeActionConflictError):
            advance_group(store, group, 'record_group', **evidence())
    send('later')
    assert store.project(run['id'])['next_action_groups'] == []
    assert all(store.get(m['action_id'])['status'] == 'needs_reconciliation' for m in group['members'])


def test_notice_only_does_not_wake_and_old_workflow_is_unchanged(grouped):
    db, _, run, _, store, send = grouped
    group = advance_group(store, claim_group(grouped), 'begin_group')
    advance_group(store, group, 'record_group', **evidence())
    send('FYI', kind='notice')
    assert store.project(run['id'])['next_action_groups'] == []
    with db.transaction() as conn:
        conn.execute('UPDATE runs SET workflow_version=3 WHERE id=?', (run['id'],))
    assert any(m['source']['kind'] == 'notice' for m in store.project(run['id'])['next_actions'])
    with pytest.raises(NativeActionConflictError, match='workflow4'):
        store.update(action='claim_group', run_id=run['id'], recipient='author',
                     action_type='followup_task', members=[dict(source_type='inbox',
                         source_id='old', source_digest='0' * 64)])


def test_restart_projection_recovers_complete_frozen_group_without_events(grouped):
    _, _, run, _, store, send = grouped
    group = advance_group(store, claim_group(grouped), 'begin_group')
    send('arrived-during-call')
    store.recover()
    projected = NativeActionStore(store.db).project(run['id'])
    restored = projected['native_action_groups'][0]
    assert restored['group_id'] == group['group_id']
    assert {m['action_id'] for m in restored['members']} == {m['action_id'] for m in group['members']}
    assert all(m['status'] == 'needs_reconciliation' for m in restored['members'])
    reconciled = advance_group(store, restored, 'reconcile_group', **evidence('not_called'))
    assert all(m['status'] == 'failed' for m in reconciled['members'])
    remaining = store.project(run['id'])['next_action_groups'][0]
    assert len(remaining['members']) == 3


def test_partial_record_race_marks_every_group_member_uncertain(grouped):
    db, _, _, _, store, _ = grouped
    group = advance_group(store, claim_group(grouped), 'begin_group')
    with db.transaction() as conn:
        conn.execute("UPDATE inbox_messages SET body_md='changed after native call' WHERE id=?",
                     (group['members'][-1]['source_id'],))
    result = advance_group(store, group, 'record_group', **evidence())
    assert all(m['status'] == 'needs_reconciliation' for m in result['members'])
    assert all(m['result'] == evidence()['result'] for m in result['members'])


def test_stale_group_member_cas_does_not_partially_write_receipts(grouped):
    _, _, _, _, store, _ = grouped
    group = advance_group(store, claim_group(grouped), 'begin_group')
    stale = group | {'members': [dict(m) for m in group['members']]}
    stale['members'][-1]['revision'] += 1
    with pytest.raises(NativeActionConflictError, match='revision'):
        advance_group(store, stale, 'record_group', **evidence())
    assert all(store.get(m['action_id'])['status'] == 'invoking' for m in group['members'])
    assert all(store.get(m['action_id'])['evidence_ref'] is None for m in group['members'])


def test_one_expired_member_fences_the_entire_external_call(grouped):
    db, _, _, _, store, _ = grouped
    group = claim_group(grouped)
    with db.transaction() as conn:
        conn.execute('UPDATE native_actions SET lease_expires_at=0 WHERE action_id=?',
                     (group['members'][0]['action_id'],))
    store.expire()
    assert all(store.get(m['action_id'])['status'] == 'needs_reconciliation' for m in group['members'])


def test_http_group_claim_begin_record_and_missing_token_rejection(grouped):
    import asyncio
    import httpx
    from orchestrator.daemon import create_app

    async def scenario():
        db, _, run, _, _, _ = grouped
        app = create_app(db.settings, db=db, start_watchdog=False)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                         base_url='http://test') as client:
                async def rpc(**args):
                    response = await client.post('/api/rpc', json={
                        'tool': 'native_action_update', 'arguments': args})
                    assert response.status_code == 200, response.text
                    return response.json()['result']

                response = await client.post('/api/rpc', json={'tool': 'board_status',
                                                               'arguments': {'run_id': run['id']}})
                projected = response.json()['result']['next_action_groups'][0]
                group = await rpc(action='claim_group', run_id=run['id'], recipient='author',
                                  action_type='followup_task', members=[{k: m[k] for k in
                                  ('source_type', 'source_id', 'source_digest')} for m in projected['members']])
                args = dict(group_id=group['group_id'], members=[dict(action_id=m['action_id'],
                    expected_revision=m['revision']) for m in group['members']])
                rejected = await client.post('/api/rpc', json={'tool': 'native_action_update',
                    'arguments': dict(action='begin_group', **args)})
                assert rejected.status_code == 400
                for member, original in zip(args['members'], group['members']):
                    member['claim_token'] = original['claim_token']
                group = await rpc(action='begin_group', **args)
                for member, begun in zip(args['members'], group['members']):
                    member['expected_revision'] = begun['revision']
                result = await rpc(action='record_group', **args, **evidence())
                assert all(m['status'] == 'confirmed' for m in result['members'])
    asyncio.run(scenario())


def test_busy_recipient_sources_cannot_hide_other_recipient_group(grouped):
    from orchestrator.watchdog import classify_run
    _, board, run, _, store, send = grouped
    claim_group(grouped)
    for index in range(51):
        send('later-' + str(index))
    human = board.board_post(run['id'], sender='human', mentions=['reviewer'],
                             msg_type='human', body_md='Read the independent review direction')
    projected = store.project(run['id'])
    assert projected['next_action_groups_count'] == 1
    assert len(projected['next_action_groups']) == 1
    reviewer = projected['next_action_groups'][0]
    assert reviewer['recipient'] == 'reviewer'
    assert reviewer['members'][0]['source_id'] == str(human['seq'])
    facts = classify_run(store.db, run['id'])
    assert facts['classification'] == 'ready'
    assert any(c['recipient'] == 'reviewer' for c in facts['executable_candidates'])
