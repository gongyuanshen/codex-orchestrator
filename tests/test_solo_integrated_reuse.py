"""v0.6.11: workspace-less integrated reuse and stage timings, on real files and real receipts.

Without an isolated workspace the candidate and integrated captures read the same tree, so the
current candidate PASS stands in for the integrated PASS.  The reuse basis is re-validated at
every gate use; any edit after the candidate PASS forces a real integrated verification.
"""
import pytest

from orchestrator.board import BoardService
from orchestrator.workflow import STAGE_ORDER, WorkflowConflictError, stage_timings
from test_plan_admission import advance, flow, verify  # noqa: F401  (fixture re-export)


def _integrated_records(db, step_id):
    with db.read_connection() as conn:
        return conn.execute('''SELECT v.id FROM verification_records v JOIN workflow_artifacts a
            ON a.id=v.artifact_id WHERE v.step_id=? AND a.scope='integrated' ''', (step_id,)).fetchall()


def _solo_candidate_pass(flow):
    db, s, run, root, _ = flow
    db.connect().execute("UPDATE runs SET mode='boost' WHERE id=?", (run['id'],))
    step = s.step_save(run['id'], 'a', 'a', 'va', execution_kind='coordinator_local')
    step = advance(s, step, 'dispatch', agent_id='/root/a', evidence='Accepted own synchronous local phase')
    for action in ('accept', 'submit'):
        step = advance(s, step, action)
    step = verify(s, step, 'candidate')
    assert step['state'] == 'ready_to_merge'
    return step


def test_solo_reuses_current_candidate_pass_and_completes_without_second_round(flow):
    db, s, run, *_ = flow
    step = _solo_candidate_pass(flow)
    step = advance(s, step, 'begin_integrated_review')
    reuse = step['checkpoint']['integrated_reuse']
    assert step['state'] == 'integrated_review'
    assert reuse['attempt'] == step['attempt'] and reuse['verifier_agent_id'] == '/root/va'
    assert reuse['candidate_verification_id'] and reuse['candidate_artifact_id']
    assert step['checkpoint']['integrated_reuse_history'] == [reuse]
    assert s.integrated_reuse_current(step)
    step = advance(s, step, 'complete')
    assert step['state'] == 'done'
    s.require_completion(run['id'])
    assert _integrated_records(db, step['id']) == []
    assert db.update_run(run['id'], status='completed')['status'] == 'completed'


def test_edit_after_candidate_pass_invalidates_reuse_and_requires_real_integrated_gate(flow):
    db, s, run, root, _ = flow
    step = _solo_candidate_pass(flow)
    step = advance(s, step, 'begin_integrated_review')
    (root / 'source.txt').write_text('edited after the candidate PASS')
    assert not s.integrated_reuse_current(s.get_step(step['id']))
    with pytest.raises(WorkflowConflictError, match='no longer current'):
        advance(s, step, 'complete')
    with pytest.raises(WorkflowConflictError):
        s.require_completion(run['id'])
    step = verify(s, s.get_step(step['id']), 'integrated')
    step = advance(s, step, 'complete')
    assert step['state'] == 'done'
    assert len(_integrated_records(db, step['id'])) == 1


def test_explicit_integrated_fail_overrides_reuse(flow):
    db, s, run, *_ = flow
    step = _solo_candidate_pass(flow)
    step = advance(s, step, 'begin_integrated_review')
    step = verify(s, step, 'integrated', 'FAIL')
    assert step['state'] == 'rework'
    with pytest.raises(WorkflowConflictError):
        s._valid_verification(s.get_step(step['id']), 'integrated')


def test_reuse_basis_is_bound_to_the_attempt(flow):
    db, s, run, *_ = flow
    step = _solo_candidate_pass(flow)
    step = advance(s, step, 'begin_integrated_review')
    stale = s.get_step(step['id'])
    stale['checkpoint']['integrated_reuse']['attempt'] = stale['attempt'] + 1
    with pytest.raises(WorkflowConflictError, match='another delivery'):
        s._valid_verification(stale, 'integrated')


def test_native_workspace_less_step_also_reuses(flow):
    db, s, run, *_ = flow
    step = s.step_save(run['id'], 'a', 'a', 'va')
    for action in ('dispatch', 'accept', 'submit'):
        step = advance(s, step, action)
    step = verify(s, step, 'candidate')
    step = advance(s, step, 'begin_integrated_review')
    assert 'integrated_reuse' in step['checkpoint']
    assert advance(s, step, 'complete')['state'] == 'done'


def test_older_workflow_versions_keep_the_separate_integrated_gate(flow):
    db, s, run, *_ = flow
    db.connect().execute('UPDATE runs SET workflow_version=4 WHERE id=?', (run['id'],))
    step = _solo_candidate_pass(flow)
    step = advance(s, step, 'begin_integrated_review')
    assert 'integrated_reuse' not in step['checkpoint']
    with pytest.raises(WorkflowConflictError, match='No current integrated'):
        advance(s, step, 'complete')


def test_stage_timings_cover_the_real_flow_and_stay_out_of_compact_status(flow):
    db, s, run, *_ = flow
    step = _solo_candidate_pass(flow)
    step = advance(s, step, 'begin_integrated_review')
    advance(s, step, 'complete')
    timings = s.summary(run['id'])['stage_timings']
    assert timings['stage_order'] == list(STAGE_ORDER)
    (entry,) = timings['steps']
    assert entry['step_key'] == 'a' and entry['done_at'] is not None and entry['current_stage'] is None
    assert entry['transitions'] >= 6
    assert set(entry['stages_sec']) <= set(STAGE_ORDER)
    assert timings['logged_span_sec'] >= 0
    board = BoardService(db)
    assert 'stage_timings' in board.board_status(run['id'])['workflow']
    assert 'stage_timings' not in board.board_status(run['id'], compact=True)['workflow']


def test_stage_timings_are_deterministic_from_the_log():
    steps = [{'id': 's1', 'step_key': 'impl: core'}, {'id': 's2', 'step_key': 'docs'}]
    log = [
        {'summary': 'impl: core: saved', 'created_at': 100.0},
        {'summary': 'impl: core: dispatched', 'created_at': 110.0},
        {'summary': 'impl: core: implementing', 'created_at': 115.0},
        {'summary': 'impl: core: candidate_review', 'created_at': 175.0},
        {'summary': 'impl: core: saved', 'created_at': 176.0},          # revised contract returns to planning
        {'summary': 'impl: core: candidate_review', 'created_at': 180.0},
        {'summary': 'impl: core: ready_to_merge', 'created_at': 235.0},
        {'summary': 'impl: core: integrated_review', 'created_at': 240.0},
        {'summary': 'impl: core: done', 'created_at': 250.0},
        {'summary': 'docs: saved', 'created_at': 120.0},
        {'summary': 'docs: dispatched', 'created_at': 130.0},
        {'summary': 'unknown-step: done', 'created_at': 999.0},
        {'summary': 'docs: not-a-state', 'created_at': 140.0},
    ]
    result = stage_timings(steps, log)
    first, second = result['steps']
    assert first['stages_sec'] == {'dispatch': 19.0, 'author': 60.0, 'candidate_review': 56.0,
                                   'merge': 5.0, 'integrated_review': 10.0}
    assert first['done_at'] == 250.0 and first['current_stage'] is None
    assert second['stages_sec'] == {'dispatch': 10.0}
    assert second['current_stage'] == 'dispatch' and second['current_since'] == 130.0
    assert result['totals_sec']['dispatch'] == 29.0
    assert result['logged_span_sec'] == 150.0
    assert stage_timings(steps, log) == result

def test_stale_before_integration_requires_real_review(flow):
    db, s, run, root, _ = flow
    step = _solo_candidate_pass(flow)
    (root / 'source.txt').write_text('format changed')
    step = advance(s, step, 'begin_integrated_review')
    assert step['state'] == 'integrated_review' and 'integrated_reuse' not in step['checkpoint']
    with pytest.raises(WorkflowConflictError, match='No current integrated'):
        advance(s, step, 'complete')
    step = verify(s, step, 'integrated')
    assert advance(s, step, 'complete')['state'] == 'done'


@pytest.mark.parametrize('explicit', [True, False])
def test_reverify_invalidates_reuse_and_previous_explicit_receipts(flow, explicit):
    db, s, run, *_ = flow
    step = advance(s, _solo_candidate_pass(flow), 'begin_integrated_review')
    if explicit:
        step = verify(s, step, 'integrated')
        old = s.summary(run['id'])['verifications'][-1]
    step = advance(s, advance(s, step, 'complete'), 'reverify')
    assert 'integrated_reuse' not in step['checkpoint']
    assert s.recorded_integrated_gate(step, step['checkpoint']) is None
    if explicit:
        with pytest.raises(WorkflowConflictError, match='fresh integrated capture'):
            s.record(step['id'], old['artifact_id'], step['verifier'], 'PASS', old['criteria_results'], [], 'try-old-receipt')
    with pytest.raises(WorkflowConflictError, match='No current integrated'):
        advance(s, step, 'complete')
    step = verify(s, step, 'integrated')
    assert s.recorded_integrated_gate(step, step['checkpoint']) == 'verified'
    assert advance(s, step, 'complete')['state'] == 'done'


def test_real_stop_response_round_trip(flow):
    from orchestrator.hook_runner import evaluate_rpc_hook_response_for_stop
    from orchestrator.rpc_handlers import RpcDispatcher
    db, s, run, *_ = flow
    rpc = RpcDispatcher(db, BoardService(db), s.workspaces)
    def hook():
        return rpc._session_run_hook_result({}, {'project_root': str(flow[3])}, run, check_stop=True)
    step = _solo_candidate_pass(flow)
    assert 'begin_integrated_review' in hook()['stop_reason']
    step = advance(s, step, 'begin_integrated_review')
    assert '复用' in hook()['stop_reason']
    step = verify(s, step, 'integrated')
    assert '集成验收已通过' in hook()['stop_reason']
    advance(s, step, 'complete')
    res = hook()
    assert res['progress_classification'] == 'completed' and evaluate_rpc_hook_response_for_stop(res)[0]
    assert not evaluate_rpc_hook_response_for_stop(dict(res, awaiting_closeout=False))[0]


@pytest.mark.parametrize('stale', [False, True])
def test_solo_closeout_checks_candidate_before_mutation(flow, stale):
    from orchestrator.rpc_handlers import RpcDispatcher
    db, s, run, root, _ = flow
    step = _solo_candidate_pass(flow)
    rpc = RpcDispatcher(db, BoardService(db), s.workspaces)
    if stale:
        (root / 'source.txt').write_text('changed')
        with pytest.raises(WorkflowConflictError, match='request_verify'):
            rpc.dispatch('workflow_closeout_run', {'run_id': run['id']})
        assert s.get_step(step['id'])['state'] == 'ready_to_merge'
    else:
        rpc.dispatch('workflow_closeout_run', {'run_id': run['id']})
        assert db.get_run(run['id'])['status'] == 'completed'
        assert _integrated_records(db, step['id']) == []

def test_open_findings_and_idle_observation_do_not_hide_real_gate_owner(flow):
    import time
    from orchestrator.watchdog import classify_run
    db, s, run, *_ = flow
    step = advance(s, _solo_candidate_pass(flow), 'begin_integrated_review')
    advance(s, step, 'observe', observation=dict(agent_id=step['verifier_agent_id'], state='idle',
            observed_at=time.time(), evidence='idle after recording PASS'))
    status = classify_run(db, run['id'])
    assert status['classification'] == 'normal_wait' and not status['alert_required']
    assert any(g.get('gate') == 'reused' for g in status['potential_candidates'])
    step = verify(s, step, 'integrated')
    assert s.recorded_integrated_gate(step, {**step['checkpoint'], 'findings': [{'status': 'open'}]}) is None
    assert s.recorded_integrated_gate(dict(step, verifier_agent_id='other'), step['checkpoint']) is None


def test_old_explicit_integrated_pass_does_not_block_revised_candidate(flow):
    db, s, run, *_ = flow
    local = dict(agent_id='/root/a', evidence='Synchronous phase completed before contract revision')
    old = verify(s, advance(s, _solo_candidate_pass(flow), 'begin_integrated_review'), 'integrated')
    step = advance(s, old, 'local_revision_boundary', **local)
    step = s.step_save(run['id'], 'a', 'a', 'va', execution_kind='coordinator_local',
                       step_id=step['id'], expected_revision=step['revision'])
    assert step['contract_revision'] == old['contract_revision'] + 1
    step = advance(s, step, 'dispatch', **local)
    step = advance(s, advance(s, step, 'accept'), 'submit')
    step = advance(s, verify(s, step, 'candidate'), 'begin_integrated_review')
    assert s.recorded_integrated_gate(step, step['checkpoint']) == 'reused'
    assert advance(s, step, 'complete')['state'] == 'done'
    s.require_completion(run['id'])
    assert len(_integrated_records(db, step['id'])) == 1


def test_unsettled_native_action_blocks_closeout_and_final_commit_until_reconciled(flow):
    from orchestrator.native_actions import NativeActionStore
    from orchestrator.rpc_handlers import RpcDispatcher

    db, s, run, *_ = flow
    run_id = run['id']
    board = BoardService(db)
    rpc = RpcDispatcher(db, board, s.workspaces)
    store = NativeActionStore(db)
    step = advance(s, advance(s, _solo_candidate_pass(flow), 'begin_integrated_review'), 'complete')
    board.board_post(run_id, 'human', mentions=['va'], msg_type='human', body_md='Check this follow-up')
    item = next(i for i in store.project(run_id)['next_actions'] if i['source_type'] == 'human')
    row = store.update(action='claim', **{k: item[k] for k in
                       ('run_id', 'source_type', 'source_id', 'recipient', 'action_type', 'source_digest')})

    def worker_states():
        return {w['name']: w['status'] for w in db.list_workers(run_id=run_id)}

    initial_states = worker_states()
    def reject(status):
        assert row['status'] == status
        for close in (lambda: rpc.dispatch('workflow_closeout_run', {'run_id': run_id}),
                      lambda: db.update_run(run_id, status='completed')):
            with pytest.raises(WorkflowConflictError, match=row['action_id']) as exc:
                close()
            assert status in str(exc.value)
            assert db.get_run(run_id)['status'] == 'active'
            assert worker_states() == initial_states
            assert s.get_step(step['id']) == step
            assert store.get(row['action_id']) == row

    reject('claimed')
    row = store.update(action='begin', action_id=row['action_id'], expected_revision=row['revision'],
                       claim_token=row['claim_token'])
    reject('invoking')
    store.recover()
    row = store.get(row['action_id'])
    reject('needs_reconciliation')
    result = store.update(action='reconcile', action_id=row['action_id'], expected_revision=row['revision'],
                          outcome='confirmed', evidence_ref='host:inspection', evidence_digest='a'*64,
                          result='Original human follow-up was delivered')
    assert result['status'] == 'confirmed'
    assert rpc.dispatch('workflow_closeout_run', {'run_id': run_id})['run']['status'] == 'completed'
