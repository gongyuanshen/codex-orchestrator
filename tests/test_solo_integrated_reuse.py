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
        {'summary': 'impl: core: saved', 'created_at': 176.0},          # later revision save: ignored
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
    assert first['stages_sec'] == {'dispatch': 15.0, 'author': 60.0, 'candidate_review': 60.0,
                                   'merge': 5.0, 'integrated_review': 10.0}
    assert first['done_at'] == 250.0 and first['current_stage'] is None
    assert second['stages_sec'] == {'dispatch': 10.0}
    assert second['current_stage'] == 'dispatch' and second['current_since'] == 130.0
    assert result['totals_sec']['dispatch'] == 25.0
    assert result['logged_span_sec'] == 150.0
    assert stage_timings(steps, log) == result
