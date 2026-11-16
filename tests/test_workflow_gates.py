"""Strict Route A contracts: genuine records, immutable artifacts and explicit rework."""
import json
from pathlib import Path

import pytest

from orchestrator.config import Settings
from orchestrator.db import Database
from orchestrator.workflow import WorkflowConflictError, WorkflowService


@pytest.fixture
def flow(tmp_path):
    project = tmp_path / 'project'
    project.mkdir()
    (project / 'source.txt').write_text('first', encoding='utf-8')
    evidence = tmp_path / 'test-output.txt'
    evidence.write_text('1 passed', encoding='utf-8')
    db = Database(settings=Settings(data_dir=tmp_path / 'state'))
    run = db.create_run(mode='boost', title='Strict flow', project_root=project)
    db.save_task_packet(run['id'], title='Frozen', goal='Actual feature',
                        file_ownership={'author': ['source.txt']},
                        acceptance_criteria=['exact output'])
    db.upsert_worker(run['id'], 'author', role='coder', agent_id='/root/author')
    db.upsert_worker(run['id'], 'verifier', role='verifier', agent_id='/root/verifier')
    service = WorkflowService(db)
    step = service.step_save(run['id'], 'main', 'author', 'verifier')
    yield db, service, run, step, project, evidence
    db.close()


def advance(service, step, action, **kw):
    return service.step_advance(step['id'], action, step['revision'], **kw)


def submit(service, step):
    step = advance(service, step, 'dispatch')
    step = advance(service, step, 'accept')
    step = advance(service, step, 'submit')
    return advance(service, step, 'challenge', evidence='Independent counterexample check', agent_id='/root/verifier')


def record(service, step, evidence, scope='candidate', verdict='PASS', key=None):
    artifact = service.capture(step['id'], scope, paths=['source.txt'])
    return service.record(step['id'], artifact['id'], 'verifier', verdict,
                          [{'criterion': 'exact output', 'status': verdict}],
                          [str(evidence)], key or f'{scope}-{step["attempt"]}-{verdict}')


def test_fresh_run_cannot_be_created_completed_or_closed_without_gate(flow):
    db, _, run, _, project, _ = flow
    with pytest.raises(ValueError, match='active'):
        db.create_run(mode='boost', title='Bypass', project_root=project, status='completed')
    with pytest.raises(WorkflowConflictError):
        db.update_run(run['id'], status='completed', metadata={'passed': True})
    assert db.get_run(run['id'])['workflow_version'] == 1


def test_cas_independence_and_dependency_checks(flow):
    db, service, run, step, _, _ = flow
    with pytest.raises(WorkflowConflictError, match='revision'):
        service.step_advance(step['id'], 'dispatch', 99)
    with pytest.raises(ValueError, match='different'):
        service.step_save(run['id'], 'self', 'author', 'author')
    dependent = service.step_save(run['id'], 'later', 'author', 'verifier', dependencies=[step['id']])
    with pytest.raises(WorkflowConflictError, match='depend'):
        advance(service, dependent, 'dispatch')
    with pytest.raises(ValueError, match='cycle'):
        service.step_save(run['id'], 'main', 'author', 'verifier', dependencies=[dependent['id']],
                          step_id=step['id'], expected_revision=step['revision'])


def test_current_artifact_candidate_and_integrated_verification_close(flow):
    db, service, run, step, _, evidence = flow
    step = submit(service, step)
    record(service, step, evidence)
    step = service.get_step(step['id'])
    assert step['state'] == 'ready_to_merge'
    with pytest.raises(WorkflowConflictError):
        advance(service, step, 'complete')
    step = advance(service, step, 'begin_integrated_review')
    record(service, step, evidence, 'integrated')
    step = advance(service, service.get_step(step['id']), 'complete')
    assert step['state'] == 'done'
    assert db.update_run(run['id'], status='completed')['status'] == 'completed'
    with pytest.raises(WorkflowConflictError):
        db.save_task_packet(run['id'], title='late', goal='mutate terminal', acceptance_criteria=['x'])


@pytest.mark.parametrize('change', ['source', 'evidence', 'packet', 'binding'])
def test_stale_verification_cannot_finish_current_work(flow, change):
    db, service, run, step, project, evidence = flow
    step = submit(service, step)
    record(service, step, evidence)
    step = advance(service, service.get_step(step['id']), 'begin_integrated_review')
    record(service, step, evidence, 'integrated')
    if change == 'source':
        (project / 'source.txt').write_text('changed', encoding='utf-8')
    elif change == 'evidence':
        evidence.write_text('different output', encoding='utf-8')
    elif change == 'packet':
        db.save_task_packet(run['id'], title='Changed', goal='new criterion', acceptance_criteria=['other'])
    else:
        with db.transaction() as conn:
            conn.execute("UPDATE workers SET agent_id='/root/new-verifier' WHERE run_id=? AND name='verifier'", (run['id'],))
    with pytest.raises(WorkflowConflictError):
        advance(service, service.get_step(step['id']), 'complete')


def test_three_reworks_then_needs_decision_and_duplicate_fail_not_counted(flow):
    _, service, _, step, _, evidence = flow
    step = submit(service, step)
    for attempt in range(4):
        result = record(service, step, evidence, verdict='FAIL', key=f'failure-{attempt}')
        same = service.record(step['id'], result['artifact_id'], 'verifier', 'FAIL',
                              [{'criterion': 'exact output', 'status': 'FAIL'}], [str(evidence)], f'failure-{attempt}')
        assert same['id'] == result['id']
        step = service.get_step(step['id'])
        assert step['attempt'] == attempt
        if attempt < 3:
            assert step['state'] == 'rework'
            step = advance(service, step, 'start_rework')
            step = advance(service, step, 'submit')
            step = advance(service, step, 'challenge', evidence='Retest challenge', agent_id='/root/verifier')
        else:
            assert step['state'] == 'needs_decision'
            with pytest.raises(WorkflowConflictError):
                advance(service, step, 'start_rework')


def test_missing_criteria_fabricated_text_and_changed_after_capture_fail(flow):
    _, service, _, step, project, evidence = flow
    step = submit(service, step)
    artifact = service.capture(step['id'], 'candidate', paths=['source.txt'])
    with pytest.raises(ValueError, match='criteria'):
        service.record(step['id'], artifact['id'], 'verifier', 'PASS', [], [str(evidence)], 'missing')
    with pytest.raises((ValueError, FileNotFoundError)):
        service.record(step['id'], artifact['id'], 'verifier', 'PASS',
                       [{'criterion': 'exact output', 'status': 'PASS'}], ['PASS'], 'text')
    (project / 'source.txt').write_text('after capture', encoding='utf-8')
    with pytest.raises(WorkflowConflictError, match='artifact'):
        service.record(step['id'], artifact['id'], 'verifier', 'PASS',
                       [{'criterion': 'exact output', 'status': 'PASS'}], [str(evidence)], 'changed')


def test_pause_and_control_block_dispatch(flow):
    db, service, _, step, _, _ = flow
    db.update_worker_status(worker_id=db.get_worker_by_name(step['run_id'], 'author')['id'], status='stopped')
    with pytest.raises(WorkflowConflictError, match='paused'):
        advance(service, step, 'dispatch')


def test_paths_cannot_escape_project_and_capture_includes_missing_file(flow):
    _, service, _, step, _, _ = flow
    with pytest.raises(ValueError, match='scope'):
        service.capture(step['id'], 'candidate', paths=['../outside'])
    artifact = service.capture(step['id'], 'candidate', paths=['source.txt', 'deleted.txt'])
    assert artifact['descriptor']['manifest']['deleted.txt'] is None


def test_candidate_requires_boost_challenge_and_distinct_binding(flow):
    db, service, _, step, _, evidence = flow
    step = advance(service, advance(service, advance(service, step, 'dispatch'), 'accept'), 'submit')
    with pytest.raises(WorkflowConflictError, match='challenge'):
        record(service, step, evidence)
    with pytest.raises(WorkflowConflictError, match='verifier'):
        advance(service, step, 'challenge', evidence='pretend', agent_id='/root/author')


def test_changed_record_retry_is_conflict(flow):
    _, service, _, step, _, evidence = flow
    step = submit(service, step)
    r = record(service, step, evidence, verdict='FAIL', key='same-key')
    with pytest.raises(WorkflowConflictError, match='key'):
        service.record(step['id'], r['artifact_id'], 'verifier', 'PASS',
                       [{'criterion': 'exact output', 'status': 'PASS'}], [str(evidence)], 'same-key')


def test_revision_of_contract_invalidates_older_pass_even_same_content(flow):
    _, service, run, step, _, evidence = flow
    step = submit(service, step)
    record(service, step, evidence)
    current = service.get_step(step['id'])
    revised = service.step_save(run['id'], 'main', 'author', 'verifier',
                                step_id=step['id'], expected_revision=current['revision'])
    revised = submit(service, revised)
    # A contract revision cannot borrow the previous contract's candidate PASS.
    with pytest.raises(WorkflowConflictError, match='contract'):
        service._valid_verification(revised, 'candidate')


def test_completion_cannot_omit_packet_acceptance_criteria(flow):
    db, service, run, _, _, evidence = flow
    db.save_task_packet(run['id'], title='Two', goal='Both required', file_ownership={'author':['source.txt']}, acceptance_criteria=['exact output', 'omitted'])
    current = service.summary(run['id'])['steps'][0]
    step = service.step_save(run['id'], 'main', 'author', 'verifier', criteria=['exact output'],
                              step_id=current['id'], expected_revision=current['revision'])
    step = submit(service, step)
    record(service, step, evidence)
    step = advance(service, service.get_step(step['id']), 'begin_integrated_review')
    record(service, step, evidence, 'integrated')
    advance(service, service.get_step(step['id']), 'complete')
    with pytest.raises(WorkflowConflictError, match='cover'):
        db.update_run(run['id'], status='completed')


def test_reverify_refreshes_done_step_after_later_intentional_change(flow):
    db, service, run, step, project, evidence = flow
    step = submit(service, step)
    record(service, step, evidence)
    step = advance(service, service.get_step(step['id']), 'begin_integrated_review')
    record(service, step, evidence, 'integrated')
    step = advance(service, service.get_step(step['id']), 'complete')
    (project / 'source.txt').write_text('later intentional integration', encoding='utf-8')
    with pytest.raises(WorkflowConflictError):
        db.update_run(run['id'], status='completed')
    step = advance(service, step, 'reverify')
    assert step['attempt'] == 0
    record(service, step, evidence, 'integrated', key='refresh')
    advance(service, service.get_step(step['id']), 'complete')
    assert db.update_run(run['id'], status='completed')['status'] == 'completed'


def test_native_observation_is_explicit_bounded_and_not_inferred_from_heartbeat(flow):
    _, service, run, step, _, _ = flow
    assert 'native_observation' not in service.summary(run['id'])['steps'][0]['checkpoint']
    import time
    observation = {'agent_id': '/root/author', 'state': 'idle', 'observed_at': time.time(),
                    'evidence': 'Actual native list result reported by coordinator'}
    step = advance(service, step, 'observe', observation=observation)
    assert step['state'] == 'planned'
    assert step['checkpoint']['native_observation'] == observation
    with pytest.raises(WorkflowConflictError):
        advance(service, step, 'observe', observation={**observation, 'agent_id': '/other'})
    with pytest.raises(ValueError):
        advance(service, step, 'observe', observation={**observation, 'observed_at': float('inf')})


def test_teamwork_milestone_handoff_and_dependency_confirmation(tmp_path):
    project = tmp_path / 'project'
    project.mkdir()
    (project / 'source.txt').write_text('one', encoding='utf-8')
    evidence = tmp_path / 'evidence.txt'
    evidence.write_text('actual evidence', encoding='utf-8')
    db = Database(settings=Settings(data_dir=tmp_path / 'state'))
    run = db.create_run(mode='teamwork', title='Milestones', project_root=project)
    db.save_task_packet(run['id'], title='Frozen', goal='Both stages', file_ownership={'author':['source.txt']}, acceptance_criteria=['exact output'])
    db.upsert_worker(run['id'], 'author', role='coder', agent_id='/root/author')
    db.upsert_worker(run['id'], 'verifier', role='verifier', agent_id='/root/verifier')
    service = WorkflowService(db)
    first = service.step_save(run['id'], 'milestone-1', 'author', 'verifier', kind='milestone')
    second = service.step_save(run['id'], 'milestone-2', 'author', 'verifier', dependencies=[first['id']], kind='milestone')
    first = submit(service, first)
    record(service, first, evidence)
    first = advance(service, service.get_step(first['id']), 'begin_integrated_review')
    record(service, first, evidence, 'integrated')
    first = advance(service, service.get_step(first['id']), 'complete')
    assert first['state'] == 'handoff'
    with pytest.raises(WorkflowConflictError, match='depend'):
        advance(service, second, 'dispatch')
    first = advance(service, first, 'handoff', agent_id='/root/project-old', evidence='Versioned checkpoint handed over')
    with pytest.raises(WorkflowConflictError):
        advance(service, first, 'ack_handoff', agent_id='/root/project-old', evidence='self ACK')
    first = advance(service, first, 'ack_handoff', agent_id='/root/project-new', evidence='Checkpoint read and confirmed')
    assert first['state'] == 'done'
    assert advance(service, second, 'dispatch')['state'] == 'dispatched'
    db.close()


def test_summary_never_recaptures_or_reads_evidence_files(flow, monkeypatch):
    _, service, run, _, _, _ = flow
    def fail(*args, **kw):
        raise AssertionError('Dashboard must not scan files')
    monkeypatch.setattr('orchestrator.workflow.recapture', fail)
    monkeypatch.setattr('orchestrator.workflow.evidence_current', fail)
    assert service.summary(run['id'])['steps']


def test_explicit_file_scope_does_not_scan_unrelated_project_data(flow, monkeypatch):
    _, service, _, step, _, _ = flow
    def fail(*args, **kw):
        raise PermissionError('Unrelated live database must not be scanned')
    monkeypatch.setattr('orchestrator.workspace_mgr._scan_snapshot_files', fail)
    assert service.capture(step['id'], 'candidate', ['source.txt'])['descriptor']['manifest']


def test_solo_candidate_cannot_approve_non_owned_nonexistent_file(flow):
    _, service, _, step, _, _ = flow
    with pytest.raises(WorkflowConflictError, match='scope'):
        service.capture(step['id'], 'candidate', paths=['nonexistent-unrelated.txt'])


def test_integrated_capture_cannot_shrink_approved_candidate_scope(flow):
    _, service, _, step, _, evidence = flow
    step = submit(service, step)
    record(service, step, evidence)
    step = advance(service, service.get_step(step['id']), 'begin_integrated_review')
    with pytest.raises(WorkflowConflictError, match='scope'):
        service.capture(step['id'], 'integrated', paths=['nonexistent-unrelated.txt'])


@pytest.mark.parametrize('action', ['handoff', 'ack_handoff'])
@pytest.mark.parametrize('field,value', [('agent_id', '   '), ('agent_id', 12),
                                        ('evidence', '\t '), ('evidence', 12)])
def test_handoff_rejects_blank_or_non_string_receipt_without_mutation(flow, action, field, value):
    _, service, run, step, _, evidence = flow
    step = service.step_save(run['id'], 'main', 'author', 'verifier',
                              step_id=step['id'], expected_revision=step['revision'], handoff_required=True)
    step = submit(service, step)
    record(service, step, evidence)
    step = advance(service, service.get_step(step['id']), 'begin_integrated_review')
    record(service, step, evidence, 'integrated')
    step = advance(service, service.get_step(step['id']), 'complete')
    if action == 'ack_handoff':
        step = advance(service, step, 'handoff', agent_id='/root/project-old', evidence='Checkpoint sent')
    before = service.get_step(step['id'])
    arguments = {'agent_id': '/root/project-new', 'evidence': 'Checkpoint received', field: value}
    with pytest.raises(ValueError):
        advance(service, step, action, **arguments)
    assert service.get_step(step['id']) == before
