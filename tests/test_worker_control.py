import pytest
from orchestrator.db import Database, SCHEMA_VERSION

def test_control(tmp_path):
    db = Database(tmp_path / 'db')
    run = db.create_run(mode='boost', title='test', project_root=tmp_path)
    w = db.upsert_worker(run['id'], 'coder', role='coder', status='working', agent_id='native-1')
    req = db.request_worker_control(w['id'], 'stopped')
    assert db.get_worker(w['id'])['status'] == 'working'
    assert db.request_worker_control(w['id'], 'stopped') == req
    with pytest.raises(ValueError): db.request_worker_control(w['id'], 'killed')
    with pytest.raises(ValueError): db.update_worker_status(worker_id=w['id'], status='waiting')
    with pytest.raises(ValueError): db.upsert_worker(run['id'], 'coder', role='coder', agent_id='other')
    db.close()
    db = Database(tmp_path / 'db')
    assert db.list_worker_controls(run['id']) == [req]
    with pytest.raises(ValueError): db.worker_control_ack(req['id'], 'applied', '')
    ack = db.worker_control_ack(req['id'], 'applied', 'interrupt_agent native-1 returned interrupted')
    assert db.get_worker(w['id'])['status'] == 'stopped'
    assert db.worker_control_ack(req['id'], 'applied', ack['result']) == ack
    with pytest.raises(ValueError): db.worker_control_ack(req['id'], 'failed', 'error')
    with pytest.raises(ValueError): db.update_worker_heartbeat(worker_id=w['id'], status='working')
    db.close()

def test_failed_and_terminal(tmp_path):
    from orchestrator.board import BoardService
    db = Database(tmp_path / 'db')
    r = db.create_run(mode='boost', title='test', project_root=tmp_path)
    w = db.upsert_worker(r['id'], 'coder', role='coder', status='working', heartbeat_at=1)
    with pytest.raises(ValueError): db.request_worker_control(w['id'], 'stopped')
    w = db.upsert_worker(r['id'], 'coder', role='coder', status='working', agent_id='native', heartbeat_at=1)
    req = db.request_worker_control(w['id'], 'killed')
    with pytest.raises(ValueError): db.update_run(r['id'], status='completed')
    failed = db.worker_control_ack(req['id'], 'failed', 'native target unavailable')
    assert failed['status'] == 'failed'
    assert db.list_worker_controls(r['id']) == [failed]
    assert db.get_worker(w['id'])['status'] == 'working'
    # This test isolates legacy control semantics, not strict workflow acceptance.
    db.connect().execute("UPDATE runs SET workflow_version=0 WHERE id=?", (r["id"],))
    db.update_run(r['id'], status='completed')
    assert not db.find_stalled_workers(now=10000)
    assert not BoardService(db).board_status(r['id'], now=10000)['stalled_workers']
    with pytest.raises(ValueError): db.request_worker_control(w['id'], 'stopped')
    db.close()

def test_legacy_migration(tmp_path):
    import sqlite3
    from contextlib import closing
    path = tmp_path / 'legacy'
    # Pin the pre-control tables rather than seeding an already-upgraded database.
    with closing(sqlite3.connect(path)) as conn:
        conn.executescript("""
            CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at REAL NOT NULL);
            INSERT INTO schema_migrations VALUES (1, 0);
            CREATE TABLE projects (
                id TEXT PRIMARY KEY, root_path TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL, is_git INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL
            );
            INSERT INTO projects VALUES ('old', 'old-root', 'preserved', 0, 0);
            CREATE TABLE workers (
                id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
                name TEXT NOT NULL, role TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'waiting',
                workspace_id TEXT, current_task TEXT NOT NULL DEFAULT '', last_heartbeat_at REAL NOT NULL,
                stall_threshold_sec INTEGER NOT NULL DEFAULT 300, stalled_alerted INTEGER NOT NULL DEFAULT 0,
                created_at REAL NOT NULL, updated_at REAL NOT NULL, UNIQUE (run_id, name)
            );
        """)
    with Database(path) as db:
        assert db.migrate() == SCHEMA_VERSION
        assert db.get_project('old')['name'] == 'preserved'
    with Database(path) as db:
        assert db.get_project('old')['name'] == 'preserved'
        assert 'agent_id' in {r['name'] for r in db.connect().execute('PRAGMA table_info(workers)')}

def test_binding_and_control_boundaries(tmp_path):
    db = Database(tmp_path / 'db')
    r = db.create_run(mode='boost', title='test', project_root=tmp_path)
    w = db.upsert_worker(r['id'], 'coder', role='coder', agent_id='native')
    with pytest.raises(ValueError): db.request_worker_control(w['id'], 'working')
    with pytest.raises(ValueError): db.upsert_worker(r['id'], 'coder', role='coder', agent_id='other')
    req = db.request_worker_control(w['id'], 'killed')
    for result in (123, {}, [], None, ' '):
        with pytest.raises(ValueError): db.worker_control_ack(req['id'], 'applied', result)
    ack = db.worker_control_ack(req['id'], 'applied', 'native interrupt confirmed')
    with pytest.raises(ValueError): db.upsert_worker(r['id'], 'coder', role='coder', status='working')
    # This test isolates legacy control semantics, not strict workflow acceptance.
    db.connect().execute("UPDATE runs SET workflow_version=0 WHERE id=?", (r["id"],))
    db.update_run(r['id'], status='completed')
    assert db.worker_control_ack(req['id'], 'applied', ack['result']) == ack
    db.close()

@pytest.mark.parametrize('outcome', ['applied', 'failed'])
def test_ack_publishes_persisted_worker(tmp_path, outcome):
    import asyncio
    from orchestrator.board import BoardService
    from orchestrator.rpc_handlers import EventBus, RpcDispatcher
    from orchestrator.workspace_mgr import WorkspaceManager

    async def exercise():
        db = Database(tmp_path / 'events.db')
        try:
            run = db.create_run(mode='boost', title='events', project_root=tmp_path)
            worker = db.upsert_worker(run['id'], 'coder', role='coder', status='working', agent_id='native')
            request = db.request_worker_control(worker['id'], 'stopped')
            bus = EventBus()
            _, queue = bus.subscribe(run['id'])
            dispatcher = RpcDispatcher(db, BoardService(db), WorkspaceManager(db), bus)
            args = dict(request_id=request['id'], outcome=outcome, result='native tool evidence')
            for _ in range(2):
                ack = dispatcher.dispatch('worker_control_ack', args)
                await asyncio.sleep(0)
                events = []
                while not queue.empty():
                    events.append(queue.get_nowait())
                assert events[0]['event'] == 'worker_control_updated'
                assert events[0]['data']['control_request'] == ack
                persisted = db.get_worker(worker['id'])
                assert persisted['status'] == ('stopped' if outcome == 'applied' else 'working')
                assert [e['event'] for e in events] == (['worker_control_updated', 'worker_updated'] if outcome == 'applied' else ['worker_control_updated'])
                if outcome == 'applied':
                    assert events[1]['data'] == {'run_id': run['id'], 'worker': persisted}
        finally:
            db.close()

    asyncio.run(exercise())
