"""Workflow2 mode contracts on isolated databases and real artifact bytes."""
import sqlite3
from pathlib import Path

import pytest

from orchestrator.board import BoardService
from orchestrator.config import Settings
from orchestrator.db import Database, SCHEMA_VERSION
from orchestrator.rpc_handlers import RpcDispatcher, get_tool_definitions
from orchestrator.workflow import WorkflowConflictError, WorkflowService
from orchestrator.workspace_mgr import WorkspaceManager


@pytest.fixture
def mode_flow(tmp_path):
    opened = []
    def create(mode='boost', version=2):
        root = tmp_path / f'project-{len(opened)}'
        root.mkdir()
        (root / 'source.txt').write_text('initial', encoding='utf-8')
        evidence = tmp_path / f'evidence-{len(opened)}.txt'
        evidence.write_text('fixture evidence for state-machine assertions', encoding='utf-8')
        settings = Settings(data_dir=tmp_path / f'state-{len(opened)}')
        db = Database(settings=settings)
        opened.append(db)
        run = db.create_run(mode=mode, title='mode contract', project_root=root)
        db.connect().execute('UPDATE runs SET workflow_version=? WHERE id=?', (1 if version == 99 else version, run['id']))
        db.save_task_packet(run['id'], title='Frozen', goal='contract',
                            file_ownership={'author': ['source.txt'], 'other': ['source.txt']},
                            acceptance_criteria=['output'])
        for name, role in [('author', 'coder'), ('verifier', 'verifier'), ('other', 'coder'), ('reviewer2', 'verifier')]:
            db.upsert_worker(run['id'], name, role=role, agent_id=f'/root/{name}')
        service = WorkflowService(db)
        step = service.step_save(run['id'], 'main', 'author', 'verifier',
                                 kind='milestone' if mode == 'teamwork' else 'task')
        if version == 99:
            db.connect().execute('UPDATE runs SET workflow_version=99 WHERE id=?', (run['id'],))
        return db, service, run, step, root, evidence, settings
    yield create
    for db in opened:
        db.close()


def advance(service, step, action, **kwargs):
    return service.step_advance(step['id'], action, step['revision'], **kwargs)


def deliver(service, step):
    for action in ('dispatch', 'accept', 'submit'):
        step = advance(service, step, action)
    return step


def challenge(service, step, artifact):
    return advance(service, step, 'challenge', agent_id='/root/verifier',
                   evidence='actual counterexample review receipt', artifact_id=artifact['id'])


def record(service, step, artifact, evidence, verdict='PASS'):
    return service.record(step['id'], artifact['id'], 'verifier', verdict,
                          [{'criterion': 'output', 'status': verdict}], [str(evidence)], artifact['id'])


def finish(service, step, evidence):
    step = deliver(service, step)
    artifact = service.capture(step['id'], 'candidate', ['source.txt'])
    if service.db.get_run(step['run_id'])['mode'] == 'boost':
        step = challenge(service, step, artifact)
    record(service, step, artifact, evidence)
    step = advance(service, service.get_step(step['id']), 'begin_integrated_review')
    artifact = service.capture(step['id'], 'integrated', ['source.txt'])
    record(service, step, artifact, evidence)
    return advance(service, service.get_step(step['id']), 'complete')


def test_new_runs_use_workflow4_schema7_and_rpc_cannot_choose_version(tmp_path):
    settings = Settings(data_dir=tmp_path / 'data')
    db = Database(settings=settings)
    try:
        assert SCHEMA_VERSION == db.migrate() == 8
        assert db.create_run(mode='boost', title='new', project_root=tmp_path)['workflow_version'] == 5
        dispatcher = RpcDispatcher(db, BoardService(db), WorkspaceManager(db, settings))
        with pytest.raises(ValueError, match='Unknown'):
            dispatcher.dispatch('run_create', {'mode': 'boost', 'title': 'bypass',
                                  'project_root': str(tmp_path), 'workflow_version': 0})
        properties = next(t['inputSchema']['properties'] for t in get_tool_definitions()
                          if t['name'] == 'workflow_step_advance')
        assert {'artifact_id', 'recipient_agent_id'} <= properties.keys()
    finally:
        db.close()


def test_schema4_upgrade_keeps_existing_run_contracts_and_adds_nullable_context(tmp_path, monkeypatch):
    path = tmp_path / 'old4.db'
    db = Database(path)
    old = []
    for version in (0, 1):
        run = db.create_run(mode='swarm', title=f'old{version}', project_root=tmp_path)
        db.connect().execute('UPDATE runs SET workflow_version=? WHERE id=?', (version, run['id']))
        old.append(db.get_run(run['id']))
    db.connect().execute('DELETE FROM schema_migrations WHERE version>4')
    db.connect().execute('INSERT OR IGNORE INTO schema_migrations VALUES(4,0)')
    columns = {r['name'] for r in db.connect().execute('PRAGMA table_info(inbox_messages)')}
    for column in ('step_packet_version', 'step_attempt', 'step_contract_revision'):
        if column in columns:
            db.connect().execute(f'ALTER TABLE inbox_messages DROP COLUMN {column}')
    db.close()
    db = Database(path)
    try:
        assert [db.get_run(r['id']) for r in old] == old
        columns = {r['name']: r for r in db.connect().execute('PRAGMA table_info(inbox_messages)')}
        for column in ('step_packet_version', 'step_attempt', 'step_contract_revision'):
            assert column in columns and columns[column]['notnull'] == 0
        assert db.migrate() == 8
    finally:
        db.close()
    before = path.read_bytes()
    monkeypatch.setattr('orchestrator.db.SCHEMA_VERSION', 4)
    with pytest.raises(ValueError, match='newer'):
        Database(path)
    assert path.read_bytes() == before


@pytest.mark.parametrize('version', [1, 2, 3, 99])
def test_shared_completion_and_terminal_reopening_are_not_bypassable(mode_flow, version):
    db, service, run, step, *_ = mode_flow(version=version)
    with pytest.raises(WorkflowConflictError):
        db.update_run(run['id'], status='completed')
    if version in (1, 2, 3):
        db.update_run(run['id'], status='archived')
        with pytest.raises(WorkflowConflictError):
            db.update_run(run['id'], status='active')
    else:
        with pytest.raises(WorkflowConflictError):
            db.save_task_packet(run['id'], title='unknown', goal='reject')


def test_boost_challenge_requires_exact_current_candidate_binding(mode_flow):
    db, service, run, step, root, evidence, _ = mode_flow()
    step = deliver(service, step)
    first = service.capture(step['id'], 'candidate', ['source.txt'])
    with pytest.raises(WorkflowConflictError, match='artifact'):
        advance(service, step, 'challenge', agent_id='/root/verifier', evidence='not bound')
    with pytest.raises(WorkflowConflictError, match='verifier'):
        advance(service, step, 'challenge', agent_id='/root/author', evidence='self', artifact_id=first['id'])
    step = challenge(service, step, first)
    assert step['checkpoint']['challenge'] == {
        'agent_id': '/root/verifier', 'evidence': 'actual counterexample review receipt',
        'artifact_id': first['id'], 'packet_version': step['packet_version'],
        'attempt': step['attempt'], 'contract_revision': step['contract_revision']}
    second = service.capture(step['id'], 'candidate', ['source.txt'])
    with pytest.raises(WorkflowConflictError, match='challenge'):
        record(service, step, second, evidence)
    step = challenge(service, step, second)
    record(service, step, second, evidence)
    assert service.get_step(step['id'])['state'] == 'ready_to_merge'


def test_boost_challenge_rejects_stale_foreign_and_previous_attempt_artifacts(mode_flow):
    db, service, run, step, root, evidence, _ = mode_flow()
    step = deliver(service, step)
    artifact = service.capture(step['id'], 'candidate', ['source.txt'])
    (root / 'source.txt').write_text('modified', encoding='utf-8')
    with pytest.raises(WorkflowConflictError, match='artifact'):
        challenge(service, step, artifact)
    other = service.step_save(run['id'], 'other-step', 'author', 'verifier')
    foreign = service.capture(other['id'], 'candidate', ['source.txt'])
    with pytest.raises(WorkflowConflictError, match='artifact'):
        challenge(service, step, foreign)
    artifact = service.capture(step['id'], 'candidate', ['source.txt'])
    step = challenge(service, step, artifact)
    record(service, step, artifact, evidence, 'FAIL')
    step = advance(service, service.get_step(step['id']), 'start_rework')
    step = advance(service, step, 'submit')
    with pytest.raises(WorkflowConflictError, match='artifact'):
        challenge(service, step, artifact)
    current = service.capture(step['id'], 'candidate', ['source.txt'])
    with pytest.raises(WorkflowConflictError, match='challenge'):
        record(service, step, current, evidence)


@pytest.mark.parametrize('version', [2, 3])
@pytest.mark.parametrize('replacement', ['owner', 'verifier', 'owner_binding', 'verifier_binding'])
def test_executed_work_keeps_original_author_and_verifier(mode_flow, replacement, version):
    db, service, run, step, *_ = mode_flow(version=version)
    step = advance(service, step, 'dispatch')
    owner, verifier = 'author', 'verifier'
    if replacement == 'owner':
        owner = 'other'
    elif replacement == 'verifier':
        verifier = 'reviewer2'
    else:
        name = 'author' if replacement == 'owner_binding' else 'verifier'
        db.connect().execute('UPDATE workers SET agent_id=? WHERE run_id=? AND name=?',
                             ('/root/replacement', run['id'], name))
    with pytest.raises(WorkflowConflictError, match='original'):
        service.step_save(run['id'], 'main', owner, verifier, step_id=step['id'],
                          expected_revision=step['revision'])


@pytest.mark.parametrize('version', [2, 3])
@pytest.mark.parametrize('state', ['rework', 'needs_decision'])
def test_packet_changes_do_not_revive_failed_steps_or_retry_budget(mode_flow, state, version):
    db, service, run, step, *_ = mode_flow(version=version)
    step = advance(service, step, 'dispatch')
    db.connect().execute('UPDATE workflow_steps SET state=?,attempt=3 WHERE id=?', (state, step['id']))
    before = service.get_step(step['id'])
    db.save_task_packet(run['id'], title='Changed', goal='same work',
                        file_ownership={'author': ['source.txt']}, acceptance_criteria=['output'])
    current = service.get_step(step['id'])
    assert current['state'] == state
    assert current['attempt'] == 3
    assert current['checkpoint']['original_bindings'] == before['checkpoint']['original_bindings']
    with pytest.raises(WorkflowConflictError):
        advance(service, current, 'dispatch')


@pytest.mark.parametrize('version', [2, 3])
def test_packet_update_cannot_erase_original_bindings(mode_flow, version):
    db, service, run, step, *_ = mode_flow(version=version)
    step = advance(service, step, 'dispatch')
    db.save_task_packet(run['id'], title='Changed', goal='same work',
                        file_ownership={'author': ['source.txt'], 'other': ['source.txt']}, acceptance_criteria=['output'])
    step = service.get_step(step['id'])
    with pytest.raises(WorkflowConflictError, match='original'):
        service.step_save(run['id'], 'main', 'other', 'verifier', step_id=step['id'], expected_revision=step['revision'])


def test_teamwork_targets_exact_handoff_recipient_and_rechecks_current_pass(mode_flow):
    db, service, run, first, root, evidence, _ = mode_flow('teamwork')
    second = service.step_save(run['id'], 'next', 'author', 'verifier', kind='milestone', dependencies=[first['id']])
    first = finish(service, first, evidence)
    with pytest.raises(WorkflowConflictError, match='recipient'):
        advance(service, first, 'handoff', agent_id='/root/old', evidence='sent')
    first = advance(service, first, 'handoff', agent_id='/root/old',
                    recipient_agent_id='/root/new', evidence='versioned checkpoint sent')
    with pytest.raises(WorkflowConflictError, match='depend'):
        advance(service, second, 'dispatch')
    with pytest.raises(WorkflowConflictError, match='recipient'):
        advance(service, first, 'ack_handoff', agent_id='/root/wrong', evidence='not target')
    (root / 'source.txt').write_text('stale content', encoding='utf-8')
    with pytest.raises(WorkflowConflictError, match='stale'):
        advance(service, first, 'ack_handoff', agent_id='/root/new', evidence='target read')
    (root / 'source.txt').write_text('initial', encoding='utf-8')
    first = advance(service, first, 'ack_handoff', agent_id='/root/new', evidence='target read')
    second = advance(service, second, 'dispatch')
    assert second['attempt'] == first['attempt'] == 0
    (root / 'source.txt').write_text('stale after dispatch', encoding='utf-8')
    with pytest.raises(WorkflowConflictError, match='stale'):
        advance(service, second, 'accept')


def test_start_rework_rechecks_dependencies_without_new_retry_counter(mode_flow):
    db, service, run, first, root, evidence, _ = mode_flow('teamwork')
    first = finish(service, first, evidence)
    first = advance(service, first, 'handoff', agent_id='/root/old', recipient_agent_id='/root/new', evidence='sent')
    first = advance(service, first, 'ack_handoff', agent_id='/root/new', evidence='read')
    second = service.step_save(run['id'], 'next', 'author', 'verifier', kind='milestone', dependencies=[first['id']])
    second = deliver(service, second)
    artifact = service.capture(second['id'], 'candidate', ['source.txt'])
    record(service, second, artifact, evidence, 'FAIL')
    second = service.get_step(second['id'])
    (root / 'source.txt').write_text('dependency stale', encoding='utf-8')
    with pytest.raises(WorkflowConflictError, match='stale'):
        advance(service, second, 'start_rework')
    assert service.get_step(second['id'])['attempt'] == 0
    (root / 'source.txt').write_text('initial', encoding='utf-8')
    assert advance(service, second, 'start_rework')['attempt'] == 1


@pytest.mark.parametrize('version', [1, 2, 3, 99])
def test_workspace_merge_and_cleanup_do_not_skip_strict_or_unknown_versions(mode_flow, version):
    db, service, run, step, root, evidence, settings = mode_flow('swarm', version)
    manager = WorkspaceManager(db, settings)
    ws = manager.workspace_create(run['id'], 'author', copy_env=False)
    with pytest.raises(WorkflowConflictError):
        manager.workspace_merge(ws['id'])
    db.connect().execute("UPDATE workspaces SET status='merged' WHERE id=?", (ws['id'],))
    with pytest.raises(WorkflowConflictError):
        manager.workspace_cleanup(ws['id'])
    assert Path(ws['path']).is_dir()


def test_boost_three_reworks_same_bindings_and_integrated_completion(mode_flow):
    db, service, run, step, root, evidence, _ = mode_flow()
    step = deliver(service, step)
    for attempt in range(4):
        artifact = service.capture(step['id'], 'candidate', ['source.txt'])
        step = challenge(service, step, artifact)
        result = record(service, step, artifact, evidence, 'FAIL')
        # An idempotent failure receipt never consumes an additional repair attempt.
        duplicate = record(service, step, artifact, evidence, 'FAIL')
        assert result['id'] == duplicate['id']
        step = service.get_step(step['id'])
        assert step['attempt'] == attempt
        assert step['checkpoint']['original_bindings']['owner_agent_id'] == '/root/author'
        assert step['verifier_agent_id'] == '/root/verifier'
        if attempt < 3:
            step = advance(service, step, 'start_rework')
            step = advance(service, step, 'submit')
        else:
            assert step['state'] == 'needs_decision'
            with pytest.raises(WorkflowConflictError):
                advance(service, step, 'start_rework')
    db2, service2, run2, step2, _, evidence2, _ = mode_flow()
    assert finish(service2, step2, evidence2)['state'] == 'done'
    assert db2.update_run(run2['id'], status='completed')['status'] == 'completed'


def test_boost_challenge_and_teamwork_target_parameters_cross_real_rpc(mode_flow):
    db, service, run, step, _, evidence, settings = mode_flow()
    dispatcher = RpcDispatcher(db, BoardService(db), WorkspaceManager(db, settings))
    step = deliver(service, step)
    artifact = service.capture(step['id'], 'candidate', ['source.txt'])
    result = dispatcher.dispatch('workflow_step_advance', {
        'step_id': step['id'], 'action': 'challenge', 'expected_revision': step['revision'],
        'agent_id': '/root/verifier', 'artifact_id': artifact['id'], 'evidence': 'RPC challenge receipt'})
    assert result['checkpoint']['challenge']['artifact_id'] == artifact['id']
    record(service, result, artifact, evidence)
    db, service, run, step, _, evidence, settings = mode_flow('teamwork')
    dispatcher = RpcDispatcher(db, BoardService(db), WorkspaceManager(db, settings))
    step = finish(service, step, evidence)
    result = dispatcher.dispatch('workflow_step_advance', {
        'step_id': step['id'], 'action': 'handoff', 'expected_revision': step['revision'],
        'agent_id': '/root/old', 'recipient_agent_id': '/root/new', 'evidence': 'RPC target receipt'})
    assert result['checkpoint']['handoff']['recipient_agent_id'] == '/root/new'


@pytest.mark.parametrize('key', ['packet_version', 'contract_revision'])
def test_boost_challenge_context_cannot_borrow_old_contract(mode_flow, key):
    db, service, run, step, root, evidence, _ = mode_flow()
    step = deliver(service, step)
    artifact = service.capture(step['id'], 'candidate', ['source.txt'])
    step = challenge(service, step, artifact)
    import json
    checkpoint = step['checkpoint']
    checkpoint['challenge'][key] -= 1
    db.connect().execute('UPDATE workflow_steps SET checkpoint_json=? WHERE id=?', (json.dumps(checkpoint), step['id']))
    with pytest.raises(WorkflowConflictError, match='challenge'):
        record(service, step, artifact, evidence)


def test_teamwork_handoff_send_cannot_preserve_stale_verification(mode_flow):
    db, service, run, step, root, evidence, _ = mode_flow('teamwork')
    step = finish(service, step, evidence)
    (root / 'source.txt').write_text('changed before send', encoding='utf-8')
    with pytest.raises(WorkflowConflictError, match='stale'):
        advance(service, step, 'handoff', agent_id='/root/old', recipient_agent_id='/root/new', evidence='send stale')


@pytest.mark.parametrize('action', ['accept', 'start_rework'])
def test_workflow1_keeps_original_post_dispatch_dependency_contract(mode_flow, action):
    db, service, run, first, root, evidence, _ = mode_flow('teamwork', version=1)
    first = finish(service, first, evidence)
    first = advance(service, first, 'handoff', agent_id='/root/old', evidence='legacy checkpoint sent')
    first = advance(service, first, 'ack_handoff', agent_id='/root/new', evidence='legacy checkpoint read')
    second = service.step_save(run['id'], 'next', 'author', 'verifier', kind='milestone', dependencies=[first['id']])
    second = advance(service, second, 'dispatch')
    if action == 'start_rework':
        second = advance(service, second, 'accept')
        second = advance(service, second, 'submit')
        artifact = service.capture(second['id'], 'candidate', ['source.txt'])
        record(service, second, artifact, evidence, 'FAIL')
        second = service.get_step(second['id'])
    (root / 'source.txt').write_text('changed after valid legacy dispatch', encoding='utf-8')
    # Stored workflow1 checked dependencies only on dispatch, not on execution/rework admission.
    result = advance(service, second, action)
    assert result['state'] == 'implementing'
    assert result['attempt'] == (1 if action == 'start_rework' else 0)
