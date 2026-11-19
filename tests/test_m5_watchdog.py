"""Factual workflow4 watchdog classifications, without synthetic host control."""
import json
import time
from types import SimpleNamespace

import pytest

from orchestrator.board import BoardService
from orchestrator.config import Settings
from orchestrator.db import Database
import orchestrator.watchdog as watchdog
import orchestrator.workflow as workflow_module
from orchestrator.workflow import WorkflowService


@pytest.fixture
def case(tmp_path):
    project = tmp_path / 'project'
    project.mkdir()
    (project / 'source.txt').write_text('original', encoding='utf-8')
    db = Database(settings=Settings(data_dir=tmp_path / 'state'))
    board = BoardService(db)
    run = db.create_run(mode='swarm', title='factual', project_root=project)
    with db.transaction() as conn:
        conn.execute('UPDATE runs SET workflow_version=3 WHERE id=?', (run['id'],))
    for name, role in [('author', 'coder'), ('reviewer', 'verifier')]:
        board.worker_register(run['id'], name, role, agent_id='/root/' + name)
    db.save_task_packet(run['id'], title='one', goal='one', file_ownership={'author': ['source.txt']},
                        acceptance_criteria=['one'])
    flow = WorkflowService(db)
    step = flow.step_save(run['id'], 'one', 'author', 'reviewer')
    step = flow.step_advance(step['id'], 'dispatch', step['revision'])
    with db.transaction() as conn:
        conn.execute('UPDATE runs SET workflow_version=4 WHERE id=?', (run['id'],))
    yield db, board, run, step
    db.close()


def set_step(case, state=None, checkpoint=None):
    db, _, _, step = case
    with db.transaction() as conn:
        if state:
            conn.execute('UPDATE workflow_steps SET state=? WHERE id=?', (state, step['id']))
        if checkpoint is not None:
            conn.execute('UPDATE workflow_steps SET checkpoint_json=? WHERE id=?',
                         (json.dumps(checkpoint), step['id']))


def test_heartbeat_expiry_only_requires_observation_and_never_death(case):
    db, board, run, _ = case
    worker = db.get_worker_by_name(run['id'], 'author')
    db.update_worker_status(run_id=run['id'], worker_name='author', status='working')
    with db.transaction() as conn:
        conn.execute('UPDATE workers SET last_heartbeat_at=1 WHERE id=?', (worker['id'],))
    facts = watchdog.classify_run(db, run['id'])
    assert facts['classification'] == 'host_unknown'
    assert facts['incomplete'] is True
    assert any(b['kind'] == 'observe_host' for b in facts['blockers'])
    assert db.get_worker(worker['id'])['status'] == 'working'
    alerts = watchdog.StallWatchdog(db, board).scan_once()
    assert len(alerts) == 1
    assert 'dead' not in alerts[0]['message']['body_md'].lower()
    assert watchdog.StallWatchdog(db, board).scan_once() == []


def test_normal_wait_does_not_alert_and_read_is_read_only(case):
    db, board, run, _ = case
    before = db.connect().total_changes
    db.connect().execute('PRAGMA query_only=ON')
    try:
        facts = watchdog.classify_run(db, run['id'])
    finally:
        db.connect().execute('PRAGMA query_only=OFF')
    assert facts['classification'] == 'normal_wait'
    assert not facts['alert_required']
    assert db.connect().total_changes == before
    assert watchdog.StallWatchdog(db, board).scan_once() == []


def test_stopped_original_worker_is_user_decision_not_wakeup(case):
    db, board, run, _ = case
    db.update_worker_status(run_id=run['id'], worker_name='author', status='stopped')
    facts = watchdog.classify_run(db, run['id'])
    assert facts['classification'] == 'user_decision'
    assert facts['executable_candidates'] == []
    assert facts['coordinator_required'] is True
    assert facts['alert_required'] is True
    watchdog.StallWatchdog(db, board).scan_once()
    assert db.get_worker_by_name(run['id'], 'author')['status'] == 'stopped'


def test_reconciliation_and_exhausted_rework_are_distinct(case):
    db, _, run, _ = case
    set_step(case, state='reconciliation_required')
    assert watchdog.classify_run(db, run['id'])['classification'] == 'needs_reconciliation'
    set_step(case, state='needs_decision')
    assert watchdog.classify_run(db, run['id'])['classification'] == 'user_decision'


def test_current_idle_observation_reports_true_no_progress(case):
    db, _, run, _ = case
    observed = time.time()
    set_step(case, state='implementing', checkpoint={'owner_last_dispatch_at': observed - 1,
        'native_observations': {'/root/author': {'agent_id': '/root/author', 'state': 'idle',
            'observed_at': observed, 'evidence': 'Actual native status output'}}})
    facts = watchdog.classify_run(db, run['id'])
    assert facts['classification'] == 'no_progress'
    assert facts['alert_required'] is True
    assert any(b['kind'] == 'idle_incomplete' for b in facts['blockers'])


def test_running_observation_is_wait_not_death(case):
    db, board, run, _ = case
    observed = time.time()
    set_step(case, checkpoint={'native_observations': {'/root/author': {
        'agent_id': '/root/author', 'state': 'running', 'observed_at': observed,
        'evidence': 'Native running observation'}}})
    db.update_worker_status(run_id=run['id'], worker_name='author', status='working')
    with db.transaction() as conn:
        conn.execute('UPDATE workers SET last_heartbeat_at=1 WHERE run_id=?', (run['id'],))
    assert watchdog.classify_run(db, run['id'])['classification'] == 'normal_wait'
    assert watchdog.StallWatchdog(db, board).scan_once() == []


def test_done_and_inactive_runs_never_raise_persistent_alerts(case):
    db, board, run, _ = case
    set_step(case, state='done')
    facts = watchdog.classify_run(db, run['id'])
    assert facts['incomplete'] is False
    assert not facts['alert_required']
    db.update_run(run['id'], status='failed')
    assert watchdog.classify_run(db, run['id'])['classification'] == 'inactive'
    assert watchdog.StallWatchdog(db, board).scan_once() == []


def test_done_steps_with_unfinished_business_receipt_are_not_completed(case):
    from orchestrator.inbox import InboxStore
    db, board, run, step = case
    inbox = InboxStore(db, board)
    sent = inbox.send(run['id'], 'root', 'author', 'one', 'task', 'work', 'work',
                       step_id=step['id'])['message']
    inbox.ack(sent['id'], 'author', 'accepted', 'received before completion')
    set_step(case, state='done')
    facts = watchdog.classify_run(db, run['id'])
    assert facts['incomplete'] is True
    assert facts['classification'] == 'needs_reconciliation'
    assert any(b['kind'] == 'reconcile_inbox' for b in facts['blockers'])


def test_stale_running_observation_does_not_mask_unknown_host(case):
    db, _, run, step = case
    set_step(case, checkpoint={'native_observations': {'/root/author': {
        'agent_id': '/root/author', 'state': 'running', 'observed_at': step['created_at'],
        'evidence': 'Old observation'}}})
    db.update_worker_status(run_id=run['id'], worker_name='author', status='working')
    facts = watchdog.classify_run(db, run['id'], now=time.time() + 1000)
    assert facts['classification'] == 'host_unknown'


@pytest.mark.parametrize('mentions', [['reviewer'], []])
def test_human_direction_after_done_steps_still_requires_coordinator(case, mentions):
    db, board, run, _ = case
    set_step(case, state='done')
    board.board_post(run['id'], sender='human', mentions=mentions,
                     msg_type='human', body_md='Further direction remains unresolved')
    facts = watchdog.classify_run(db, run['id'])
    assert facts['incomplete'] is True
    assert facts['coordinator_required'] is True
    assert facts['classification'] == ('ready' if mentions else 'needs_reconciliation')


def test_repeated_same_idle_observation_does_not_create_duplicate_persistent_alert(case):
    db, board, run, step = case
    flow = WorkflowService(db)
    start = time.time()
    for index in range(2):
        current = flow.get_step(step['id'])
        observed_at = start + index
        flow.step_advance(current['id'], 'observe', current['revision'], observation={
            'agent_id': current['owner_agent_id'], 'state': 'idle',
            'observed_at': observed_at, 'evidence': 'Actual native observation ' + str(index)})
        alerts = watchdog.StallWatchdog(db, board).scan_once(now=observed_at)
        assert len(alerts) == (1 if index == 0 else 0)


def test_verifier_observation_before_submit_is_not_current_review_state(case, monkeypatch):
    db, _, run, step = case
    set_step(case, state='implementing')
    flow = WorkflowService(db)
    current = flow.get_step(step['id'])
    now = time.time()
    monkeypatch.setattr(workflow_module, 'time', SimpleNamespace(time=lambda: now))
    current = flow.step_advance(current['id'], 'observe', current['revision'], observation={
        'agent_id': current['verifier_agent_id'], 'state': 'idle',
        'observed_at': now, 'evidence': 'Reviewer was idle before work was submitted'})
    now += 1
    current = flow.step_advance(current['id'], 'submit', current['revision'])
    facts = watchdog.classify_run(db, run['id'], now=now)
    assert facts['classification'] == 'normal_wait'
    assert facts['alert_required'] is False
    now += 1
    flow.step_advance(current['id'], 'observe', current['revision'], observation={
        'agent_id': current['verifier_agent_id'], 'state': 'idle',
        'observed_at': now, 'evidence': 'Current reviewer state after submit'})
    assert watchdog.classify_run(db, run['id'], now=now)['classification'] == 'no_progress'


def test_stopped_notice_only_recipient_does_not_reopen_done_work(case):
    from orchestrator.inbox import InboxStore
    from orchestrator.native_actions import NativeActionStore
    db, board, run, _ = case
    set_step(case, state='done')
    InboxStore(db, board).send(run['id'], 'root', 'author', 'FYI', 'notice', 'FYI', 'Informational')
    db.update_worker_status(run_id=run['id'], worker_name='author', status='stopped')
    projection = NativeActionStore(db).project(run['id'])
    assert projection['needs_clarification'] == []
    facts = watchdog.classify_run(db, run['id'])
    assert facts['incomplete'] is False
    assert facts['alert_required'] is False


def test_definitive_native_failure_remains_coordinator_blocker_after_done_steps(case):
    from orchestrator.native_actions import NativeActionStore
    db, board, run, _ = case
    set_step(case, state='done')
    pointer = board.board_post(run['id'], sender='human', mentions=['reviewer'],
                               msg_type='human', body_md='Further review direction')
    store = NativeActionStore(db)
    item = store.project(run['id'])['next_actions'][0]
    row = store.update(action='claim', **{k: item[k] for k in
        ('run_id', 'source_type', 'source_id', 'recipient', 'action_type', 'source_digest')})
    row = store.update(action='begin', action_id=row['action_id'],
                       expected_revision=row['revision'], claim_token=row['claim_token'])
    store.update(action='record', action_id=row['action_id'], expected_revision=row['revision'],
                 claim_token=row['claim_token'], outcome='failed', evidence_ref='native:actual-failure',
                 evidence_digest='f' * 64, result='Actual host invocation failed definitively')
    assert store.project(run['id'])['next_action_groups'] == []
    facts = watchdog.classify_run(db, run['id'])
    assert facts['incomplete'] is True
    assert facts['classification'] == 'user_decision'
    assert facts['coordinator_required'] is True
    assert facts['alert_required'] is True
    assert any(b['kind'] == 'native_failure' and b['source_id'] == str(pointer['seq'])
               for b in facts['blockers'])


@pytest.mark.parametrize('native_status', ['invoking', 'confirmed'])
def test_observation_before_same_stage_native_wake_is_not_current(case, native_status):
    from orchestrator.inbox import InboxStore
    from orchestrator.native_actions import NativeActionStore
    db, board, run, step = case
    flow = WorkflowService(db)
    current = flow.get_step(step['id'])
    current = flow.step_advance(current['id'], 'observe', current['revision'], observation={
        'agent_id': current['owner_agent_id'], 'state': 'idle',
        'observed_at': time.time(), 'evidence': 'Author idle before new wake'})
    InboxStore(db, board).send(run['id'], 'root', 'author', 'new-wake', 'task', 'task', 'work',
                               step_id=step['id'])
    store = NativeActionStore(db)
    item = store.project(run['id'])['next_actions'][0]
    row = store.update(action='claim', **{k: item[k] for k in
        ('run_id', 'source_type', 'source_id', 'recipient', 'action_type', 'source_digest')})
    row = store.update(action='begin', action_id=row['action_id'],
                       expected_revision=row['revision'], claim_token=row['claim_token'])
    if native_status == 'confirmed':
        store.update(action='record', action_id=row['action_id'], expected_revision=row['revision'],
                     claim_token=row['claim_token'], outcome='confirmed', evidence_ref='native:wake',
                     evidence_digest='e' * 64, result='Actual native followup returned')
    facts = watchdog.classify_run(db, run['id'])
    assert facts['classification'] == 'normal_wait'
    assert facts['alert_required'] is False
    flow.step_advance(current['id'], 'observe', current['revision'], observation={
        'agent_id': current['owner_agent_id'], 'state': 'idle',
        'observed_at': time.time(), 'evidence': 'Author idle after native wake'})
    assert watchdog.classify_run(db, run['id'])['classification'] == 'no_progress'


def test_alert_predicate_revalidated_with_atomic_persistent_write(case, monkeypatch):
    db, board, run, _ = case
    db.update_worker_status(run_id=run['id'], worker_name='author', status='stopped')
    classify = watchdog.classify_run
    changed = False

    def close_between_read_and_alert(*args, **kwargs):
        nonlocal changed
        facts = classify(*args, **kwargs)
        if not changed:
            changed = True
            db.update_run(run['id'], status='failed')
        return facts

    monkeypatch.setattr(watchdog, 'classify_run', close_between_read_and_alert)
    assert watchdog.StallWatchdog(db, board).scan_once() == []
    with db.read_connection() as conn:
        assert conn.execute("SELECT count(*) FROM board_messages WHERE run_id=? AND sender='watchdog'",
                             (run['id'],)).fetchone()[0] == 0
