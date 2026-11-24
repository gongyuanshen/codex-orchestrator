"""Compact handoffs and mapped challenger findings use existing workflow gates."""
import json

import pytest

from orchestrator.board import BoardService
from orchestrator.inbox import InboxStore
from orchestrator.native_actions import NativeActionStore
from orchestrator.workflow import WorkflowConflictError
from test_plan_admission import flow, advance, finish, observe, verify


def handoff(s, step, recipient='/new/root'):
    return advance(s, step, 'handoff', agent_id='/old/root', evidence='board decision reference',
        recipient_agent_id=recipient, next_responsibility='Deliver second milestone with original bindings',
        forbidden_actions=['replace_original_bindings'], decision_seqs=[])


def test_exact_handoff_digest_recipient_and_notice_stability(flow):
    db, s, run, *_ = flow
    step = finish(s, s.step_save(run['id'], 'a', 'a', 'va', handoff_required=True))
    step = handoff(s, step)
    pack = step['checkpoint']['handoff']
    with pytest.raises(WorkflowConflictError, match='Handoff'):
        advance(s, step, 'ack_handoff', agent_id='/wrong', evidence='wrong', handoff_digest=pack['digest'])
    with pytest.raises(WorkflowConflictError, match='Handoff'):
        advance(s, step, 'ack_handoff', agent_id='/new/root', evidence='wrong digest', handoff_digest='old')
    InboxStore(db, BoardService(db)).send(run['id'], 'a', 'b', 'notice1', 'notice', 'ordinary notification', 'notification body')
    BoardService(db).board_read(run['id'], reader_name='new-root', advance_cursor=True)
    step = advance(s, step, 'ack_handoff', agent_id='/new/root', evidence='found original references', handoff_digest=pack['digest'])
    assert step['checkpoint']['handoff_history'] == [pack]
    assert s.resume_context(run['id'])['handoffs'][0]['acknowledged']


def test_handoff_current_facts_and_read_queries_do_not_ack_work(flow):
    db, s, run, *_ = flow
    step = finish(s, s.step_save(run['id'], 'a', 'a', 'va', handoff_required=True))
    step = handoff(s, step)
    old = step['checkpoint']['handoff']
    # A task without context is allowed only in old protocols; use an admitted B task here.
    b = s.step_save(run['id'], 'b', 'b', 'vb', dependencies=[step['id']])
    with pytest.raises(WorkflowConflictError, match='Handoff'):
        advance(s, step, 'ack_handoff', agent_id='/new/root', evidence='stale', handoff_digest=old['digest'])
    step = handoff(s, step)
    before = db.connect().total_changes
    result = BoardService(db).board_status(run['id'])
    assert db.connect().total_changes == before
    assert result['resume_context']['milestones'][1]['step_id'] == b['id']
    assert len(step['checkpoint']['handoff_history']) == 2
    assert step['checkpoint']['handoff_history'][0] == old


def test_high_complexity_findings_require_original_verifier_and_budgeted_repair(flow):
    db, s, run, *_ = flow
    step = s.step_save(run['id'], 'a', 'a', 'va', complexity='high')
    for action in ('dispatch', 'accept', 'submit'):
        step = advance(s, step, action)
    finding = s.review(step['id'], step['revision'], '/root/c', 'output', 'counterexample evidence')
    with pytest.raises(WorkflowConflictError, match='original-verifier FAIL'):
        verify(s, step, 'candidate', resolved_finding_ids=[finding['id']],
            review_dimensions=dict.fromkeys(('specification', 'code', 'stress', 'evidence'), 'PASS'))
    step = verify(s, step, 'candidate', 'FAIL',
        review_dimensions=dict.fromkeys(('specification', 'code', 'stress', 'evidence'), 'FAIL'))
    observe(s, step)
    step = advance(s, step, 'start_rework')
    step = advance(s, step, 'submit')
    step = verify(s, step, 'candidate', resolved_finding_ids=[finding['id']],
        review_dimensions=dict.fromkeys(('specification', 'code', 'stress', 'evidence'), 'PASS'))
    assert step['attempt'] == 1
    assert step['checkpoint']['findings'][0]['status'] == 'resolved'
    assert len(step['checkpoint']['review_dimensions']) == 2


def test_unsubstantiated_challenger_finding_can_be_dismissed_by_original_verifier(flow):
    db, s, run, *_ = flow
    step = s.step_save(run['id'], 'a', 'a', 'va', complexity='high')
    for action in ('dispatch', 'accept', 'submit'):
        step = advance(s, step, action)
    from orchestrator.rpc_handlers import RpcDispatcher
    db.update_worker_heartbeat(run_id=run['id'], worker_name='c', status='working', heartbeat_at=1.0)
    rpc = RpcDispatcher(db, BoardService(db), s.workspaces)
    finding = rpc.dispatch('workflow_review', dict(step_id=step['id'], expected_revision=step['revision'],
        agent_id='/root/c', criterion='output', evidence='hypothesis disproven by real verification'))
    assert db.get_worker_by_name(run['id'], 'c')['last_heartbeat_at'] > 1.0
    step = verify(s, step, 'candidate', dismissed_finding_ids=[finding['id']],
        review_dimensions=dict.fromkeys(('specification', 'code', 'stress', 'evidence'), 'PASS'))
    assert step['attempt'] == 0
    assert step['checkpoint']['findings'][0]['status'] == 'dismissed'


@pytest.mark.parametrize('change', ['pending', 'accepted', 'control', 'native_action', 'ended'])
def test_handoff_changes_remain_visible_after_cursor_advance(flow, change):
    db, s, run, *_ = flow
    db.save_task_packet(run['id'], title='independent', goal='handoff',
        file_ownership={'a': ['source.txt'], 'b': ['other.txt']}, acceptance_criteria=['output'])
    a = finish(s, s.step_save(run['id'], 'a', 'a', 'va', handoff_required=True))
    b = advance(s, s.step_save(run['id'], 'b', 'b', 'vb'), 'dispatch')
    store = InboxStore(db, BoardService(db))
    if change == 'native_action':
        message = store.send(run['id'], 'root', 'b', 'work', 'task', 'actual work', 'execute assigned task', step_id=b['id'])
    a = handoff(s, a)
    pack = a['checkpoint']['handoff']
    if change in {'pending', 'accepted'}:
        message = store.send(run['id'], 'root', 'b', 'work', 'task', 'actual work', 'execute assigned task', step_id=b['id'])
        if change == 'accepted':
            store.ack(message['message']['id'], 'b', 'accepted', 'recipient accepted; not completed')
    elif change == 'control':
        db.request_worker_control(db.get_worker_by_name(run['id'], 'b')['id'], 'stopped')
    elif change == 'native_action':
        native = NativeActionStore(db)
        item = next(item for item in native.project(run['id'])['next_actions'] if item['source_id'] == message['message']['id'])
        native.update(action='claim', **{key: item[key] for key in ('run_id', 'source_type', 'source_id', 'recipient', 'action_type', 'source_digest')})
    else:
        observe(s, a, 'ended')
    BoardService(db).board_read(run['id'], reader_name='new-root', advance_cursor=True)
    with pytest.raises(WorkflowConflictError):
        advance(s, a, 'ack_handoff', agent_id='/new/root', evidence='stale ACK', handoff_digest=pack['digest'])
    context = s.resume_context(run['id'])
    assert context['handoffs'][0]['current'] is False
    if change == 'accepted':
        assert context['unresolved']['inbox'][0]['status'] == 'accepted'
    if change == 'ended':
        assert context['binding_issues'][0]['decision_required']


def test_unresolved_finding_exhausts_original_budget_without_false_completion(flow):
    db, s, run, *_ = flow
    step = s.step_save(run['id'], 'a', 'a', 'va', complexity='high')
    for action in ('dispatch', 'accept', 'submit'):
        step = advance(s, step, action)
    s.review(step['id'], step['revision'], '/root/c', 'output', 'persistent counterexample')
    for attempt in range(4):
        step = verify(s, step, 'candidate', 'FAIL', review_dimensions=dict.fromkeys(('specification', 'code', 'stress', 'evidence'), 'FAIL'))
        if attempt < 3:
            observe(s, step)
            step = advance(s, step, 'start_rework')
            step = advance(s, step, 'submit')
    assert step['state'] == 'needs_decision' and step['attempt'] == 3
    assert step['checkpoint']['findings'][0]['status'] == 'open'
    with pytest.raises(WorkflowConflictError):
        s.require_completion(run['id'])
