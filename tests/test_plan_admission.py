"""Workflow4 admission; real files and serialized SQLite transactions."""
import concurrent.futures
import sys
import time
import json
import os
from pathlib import Path

import pytest

from orchestrator.config import Settings
from orchestrator.db import Database, SCHEMA_VERSION
from orchestrator.workflow import WorkflowService, WorkflowConflictError
from orchestrator.workspace_mgr import WorkspaceManager
from orchestrator.verify_runner import collect


@pytest.fixture
def flow(tmp_path):
    root = tmp_path / 'project'
    root.mkdir()
    (root / 'source.txt').write_text('initial')
    settings = Settings(data_dir=tmp_path / 'data')
    db = Database(settings=settings)
    run = db.create_run(mode='swarm', title='M3', project_root=root)
    db.save_task_packet(run['id'], title='frozen', goal='M3',
        file_ownership={'a': ['source.txt'], 'b': ['source.txt']}, acceptance_criteria=['output'])
    for name, role in [('a', 'coder'), ('b', 'coder'), ('va', 'verifier'), ('vb', 'verifier'), ('c', 'coder')]:
        db.upsert_worker(run['id'], name, role=role, agent_id='/root/' + name)
    yield db, WorkflowService(db), run, root, settings
    db.close()


def advance(s, step, action, **kwargs):
    step = s.get_step(step['id'])
    return s.step_advance(step['id'], action, step['revision'], **kwargs)


def observe(s, step, state='idle'):
    return advance(s, step, 'observe', observation=dict(agent_id=step['owner_agent_id'],
        state=state, observed_at=time.time(), evidence='native tool observation fixture'))


def verify(s, step, scope, verdict='PASS', *, code=None, **kwargs):
    # Most callers test gates; hard-path callers supply actual content assertions.
    code = code if code is not None else 'raise SystemExit(' + ('0' if verdict == 'PASS' else '1') + ')'
    criteria = s.get_step(step['id'])['criteria']
    commands = [dict(id='check', argv=[sys.executable, '-c', code],
        cwd=str(s.db.db_path.parent), timeout_sec=5, env_keys=[], criteria=criteria)]
    artifact = s.capture(step['id'], scope, verification_commands=commands)
    if scope == 'candidate' and s.db.get_run(step['run_id'])['mode'] == 'boost':
        advance(s, step, 'challenge', agent_id=step['verifier_agent_id'], evidence='independent counterexample review', artifact_id=artifact['id'])
    directory = s.db.db_path.parent / artifact['id']
    collect(artifact['manifest_path'], directory)
    s.record(step['id'], artifact['id'], step['verifier'], verdict,
        [{'criterion': criterion, 'status': verdict} for criterion in criteria], [str(directory / '0000.stdout')], artifact['id'],
        receipt_files=[str(directory / 'receipt.json')], **kwargs)
    return s.get_step(step['id'])


def finish(s, step):
    for action in ('dispatch', 'accept', 'submit'):
        step = advance(s, step, action)
    step = verify(s, step, 'candidate')
    step = advance(s, step, 'begin_integrated_review')
    step = verify(s, step, 'integrated')
    return advance(s, step, 'complete')


def test_new_contract(flow):
    db, s, run, *_ = flow
    assert SCHEMA_VERSION == 8
    assert run['workflow_version'] == 5


@pytest.mark.parametrize('left,right', [('source.txt', 'SOURCE.TXT'), ('src', 'src/a.py'),
    ('src/**/*.py', 'src/a.js'), ('./source.txt', 'source.txt'), ('src\\a.py', 'src/a.py')])
def test_overlap_rejected_atomically(flow, left, right):
    db, s, run, *_ = flow
    db.save_task_packet(run['id'], title='frozen', goal='M3',
        file_ownership={'a': [left], 'b': [right]}, acceptance_criteria=['output'])
    a = s.step_save(run['id'], 'a', 'a', 'va')
    b = s.step_save(run['id'], 'b', 'b', 'vb')
    advance(s, a, 'dispatch')
    with pytest.raises(WorkflowConflictError, match='ownership'):
        advance(s, b, 'dispatch')
    assert s.get_step(b['id'])['state'] == 'planned'


def test_concurrent_admission_has_one_winner(flow):
    db, s, run, *_ = flow
    steps = [s.step_save(run['id'], name, name, 'v' + name) for name in ('a', 'b')]
    def dispatch(step):
        try:
            return advance(s, step, 'dispatch')['state']
        except WorkflowConflictError:
            return 'conflict'
    with concurrent.futures.ThreadPoolExecutor(2) as pool:
        assert sorted(pool.map(dispatch, steps)) == ['conflict', 'dispatched']


def test_disjoint_and_partial_plan_allowed(flow):
    db, s, run, *_ = flow
    db.save_task_packet(run['id'], title='frozen', goal='M3',
        file_ownership={'a': ['a.txt'], 'b': ['b.txt']}, acceptance_criteria=['output', 'e2e'])
    steps = [s.step_save(run['id'], name, name, 'v' + name, criteria=['output']) for name in ('a', 'b')]
    for step in steps:
        advance(s, step, 'dispatch')
    assert s.plan_readiness(run['id'])['uncovered_criteria'] == ['e2e']


def test_sequential_dependency_requires_native_quiescence(flow):
    db, s, run, *_ = flow
    a = finish(s, s.step_save(run['id'], 'a', 'a', 'va'))
    b = s.step_save(run['id'], 'b', 'b', 'vb', dependencies=[a['id']])
    with pytest.raises(WorkflowConflictError, match='observation'):
        advance(s, b, 'dispatch')
    observe(s, a)
    admitted = advance(s, b, 'dispatch')
    assert admitted['checkpoint']['admission']['dependencies'][a['id']]['artifact_id']


@pytest.mark.parametrize('revision', ['step', 'packet'])
def test_contract_revision_cannot_hide_a_live_original_author(flow, revision):
    db, s, run, *_ = flow
    a = s.step_save(run['id'], 'a', 'a', 'va')
    a = advance(s, a, 'dispatch')
    a = advance(s, a, 'accept')
    a = observe(s, a, 'running')
    with pytest.raises(WorkflowConflictError, match='observation'):
        if revision == 'step':
            s.step_save(run['id'], 'a', 'a', 'va', step_id=a['id'], expected_revision=a['revision'])
        else:
            db.save_task_packet(run['id'], title='revised', goal='same',
                file_ownership={'a': ['source.txt'], 'b': ['source.txt']}, acceptance_criteria=['output'])
    assert s.get_step(a['id'])['state'] == 'implementing'


def test_revised_planned_original_author_must_still_be_quiescent_at_admission(flow):
    db, s, run, *_ = flow
    a = s.step_save(run['id'], 'a', 'a', 'va')
    a = advance(s, a, 'dispatch')
    a = advance(s, a, 'accept')
    a = observe(s, a)
    a = s.step_save(run['id'], 'a', 'a', 'va', step_id=a['id'], expected_revision=a['revision'])
    observe(s, a, 'running')
    b = s.step_save(run['id'], 'b', 'b', 'vb')
    with pytest.raises(WorkflowConflictError, match='observation'):
        advance(s, b, 'dispatch')
    observe(s, a)
    assert advance(s, b, 'dispatch')['state'] == 'dispatched'


def test_old_idle_observation_cannot_replace_new_running_observation(flow):
    db, s, run, *_ = flow
    a = s.step_save(run['id'], 'a', 'a', 'va')
    a = observe(s, a)
    old = a['checkpoint']['native_observation']
    a = advance(s, a, 'dispatch')
    a = observe(s, a, 'running')
    with pytest.raises(WorkflowConflictError, match='observation'):
        advance(s, a, 'observe', observation=old)


def test_hard_link_ownership_alias_is_rejected(flow):
    db, s, run, root, _ = flow
    os.link(root / 'source.txt', root / 'alias.txt')
    with pytest.raises(ValueError, match='Hard-linked'):
        s.step_save(run['id'], 'a', 'a', 'va')


def test_explicit_packet_revision_allows_original_author_budgeted_rework(flow):
    db, s, run, *_ = flow
    a = s.step_save(run['id'], 'a', 'a', 'va')
    for action in ('dispatch', 'accept', 'submit'):
        a = advance(s, a, action)
    a = verify(s, a, 'candidate', 'FAIL')
    observe(s, a)
    db.save_task_packet(run['id'], title='explicit revision', goal='same original obligation',
        file_ownership={'a': ['source.txt']}, acceptance_criteria=['output'])
    a = s.get_step(a['id'])
    a = s.step_save(run['id'], 'a', 'a', 'va', step_id=a['id'], expected_revision=a['revision'])
    a = advance(s, a, 'start_rework')
    assert a['state'] == 'implementing' and a['attempt'] == 1
    assert a['owner_agent_id'] == '/root/a'


def test_disjoint_durable_merge_remains_in_admitted_rework_chain(flow):
    db, s, run, root, _ = flow
    (root / 'c.txt').write_text('before')
    db.save_task_packet(run['id'], title='parallel', goal='M3', acceptance_criteria=['output'],
        file_ownership={'a': ['source.txt'], 'b': ['source.txt'], 'c': ['c.txt']})
    db.upsert_worker(run['id'], 'vc', role='verifier', agent_id='/root/vc')
    a, b, ws, manager = serial_merge(flow)
    cws = manager.workspace_create(run['id'], 'c', copy_env=False)
    c = s.step_save(run['id'], 'c', 'c', 'vc', workspace_id=cws['id'])
    for action in ('dispatch', 'accept'):
        c = advance(s, c, action)
    (Path(cws['path']) / 'c.txt').write_text('approved disjoint change')
    c = advance(s, c, 'submit')
    verify(s, c, 'candidate')
    manager.workspace_merge(cws['id'])
    assert advance(s, b, 'start_rework')['attempt'] == 1


def test_explicit_serial_requirement_revision_revalidates_parent_before_rework(flow):
    db, s, run, root, _ = flow
    a, b, ws, manager = serial_merge(flow)
    db.save_task_packet(run['id'], title='explicit requirements correction', goal='revalidate originals',
        file_ownership={'a': ['source.txt'], 'b': ['source.txt']}, acceptance_criteria=['output'])
    a = finish(s, s.get_step(a['id']))
    observe(s, a)
    b = advance(s, b, 'start_rework')
    assert b['attempt'] == 1 and b['owner_agent_id'] == '/root/b'
    assert b['verifier_agent_id'] == '/root/vb' and a['verifier_agent_id'] == '/root/va'


@pytest.mark.parametrize('execution_kind,explicit_boundary,allowed', [('coordinator_local', True, True), ('coordinator_local', False, False), ('native', True, False)])
def test_boost_solo_rework_requires_explicit_frozen_local_boundary(flow, execution_kind, explicit_boundary, allowed):
    db, s, run, *_ = flow
    db.connect().execute("UPDATE runs SET mode='boost' WHERE id=?", (run['id'],))
    a = s.step_save(run['id'], 'a', 'a', 'va', execution_kind=execution_kind)
    boundary = dict(agent_id='/root/a', evidence='Prior synchronous local edit and verification phase finished; beginning own local phase')
    a = advance(s, a, 'dispatch', **boundary)
    for action in ('accept', 'submit'):
        a = advance(s, a, action)
    a = verify(s, a, 'candidate', 'FAIL')
    observe(s, a, 'running')
    args = boundary if explicit_boundary else {}
    if allowed:
        a = advance(s, a, 'start_rework', **args)
        assert a['attempt'] == 1 and a['checkpoint']['coordinator_local_boundaries'][-1]['agent_id'] == '/root/a'
        assert a['checkpoint']['native_observations']['/root/a']['state'] == 'running'
    else:
        with pytest.raises(WorkflowConflictError):
            advance(s, a, 'start_rework', **args)


@pytest.mark.parametrize('obstruction', ['wrong_identity', 'empty_evidence', 'ended', 'stopped', 'control', 'exhausted'])
def test_coordinator_local_boundary_preserves_existing_guards(flow, obstruction):
    db, s, run, *_ = flow
    db.connect().execute("UPDATE runs SET mode='boost' WHERE id=?", (run['id'],))
    a = s.step_save(run['id'], 'a', 'a', 'va', execution_kind='coordinator_local')
    boundary = dict(agent_id='/root/a', evidence='Synchronous local phase completed')
    a = advance(s, a, 'dispatch', **boundary)
    for action in ('accept', 'submit'):
        a = advance(s, a, action)
    a = verify(s, a, 'candidate', 'FAIL')
    if obstruction == 'wrong_identity':
        boundary['agent_id'] = '/root/b'
    elif obstruction == 'empty_evidence':
        boundary['evidence'] = ''
    elif obstruction == 'ended':
        observe(s, a, 'ended')
    elif obstruction == 'stopped':
        db.update_worker_status(status='stopped', worker_id=db.get_worker_by_name(run['id'], 'a')['id'])
    elif obstruction == 'control':
        db.request_worker_control(db.get_worker_by_name(run['id'], 'a')['id'], 'stopped')
    else:
        db.connect().execute("UPDATE workflow_steps SET attempt=3,state='needs_decision' WHERE id=?", (a['id'],))
    with pytest.raises(WorkflowConflictError):
        advance(s, a, 'start_rework', **boundary)


def test_coordinator_local_is_boost_only_workspace_free_and_cannot_convert_executed_native(flow):
    db, s, run, root, settings = flow
    with pytest.raises(WorkflowConflictError):
        s.step_save(run['id'], 'a', 'a', 'va', execution_kind='coordinator_local')
    db.connect().execute("UPDATE runs SET mode='boost' WHERE id=?", (run['id'],))
    ws = WorkspaceManager(db, settings).workspace_create(run['id'], 'a', copy_env=False)
    with pytest.raises(WorkflowConflictError):
        s.step_save(run['id'], 'a', 'a', 'va', workspace_id=ws['id'], execution_kind='coordinator_local')
    a = s.step_save(run['id'], 'a', 'a', 'va')
    a = advance(s, a, 'dispatch')
    a = observe(s, a)
    with pytest.raises(WorkflowConflictError, match='execution_kind'):
        s.step_save(run['id'], 'a', 'a', 'va', step_id=a['id'], expected_revision=a['revision'], execution_kind='coordinator_local')


def failed_local(flow):
    db, s, run, *_ = flow
    db.connect().execute("UPDATE runs SET mode='boost' WHERE id=?", (run['id'],))
    step = s.step_save(run['id'], 'a', 'a', 'va', execution_kind='coordinator_local')
    step = advance(s, step, 'dispatch', agent_id='/root/a', evidence='Accepted own synchronous local phase')
    for action in ('accept', 'submit'):
        step = advance(s, step, action)
    step = verify(s, step, 'candidate', 'FAIL')
    return observe(s, step, 'running')


def test_local_packet_then_step_revision_then_original_repair_with_changed_criteria(flow):
    db, s, run, root, _ = flow
    step = failed_local(flow)
    boundary = dict(agent_id='/root/a', evidence='Finished current synchronous edit/verification phase before explicit revision')
    step = advance(s, step, 'local_revision_boundary', **boundary)
    db.save_task_packet(run['id'], title='corrected requirements', goal='explicit correction',
        file_ownership={'a': ['source.txt']}, acceptance_criteria=['corrected'])
    # The old criterion no longer exists: obtaining a new revision boundary must remain possible.
    step = advance(s, step, 'local_revision_boundary', **boundary)
    step = s.step_save(run['id'], 'a', 'a', 'va', step_id=step['id'], expected_revision=step['revision'],
        execution_kind='coordinator_local', criteria=['corrected'])
    assert len(step['checkpoint']['local_revision_boundaries']) == 2
    assert 'local_revision_boundary' not in step['checkpoint'] and step['attempt'] == 0
    step = advance(s, step, 'start_rework', **boundary)
    (root / 'source.txt').write_text('corrected')
    step = advance(s, step, 'submit')
    assertion = f'from pathlib import Path; assert Path({str(root / "source.txt")!r}).read_text() == "corrected"'
    step = verify(s, step, 'candidate', code=assertion)
    step = advance(s, step, 'begin_integrated_review')
    step = verify(s, step, 'integrated', code=assertion)
    step = advance(s, step, 'complete')
    s.require_completion(run['id'])
    assert step['attempt'] == 1 and step['owner_agent_id'] == '/root/a' and step['verifier_agent_id'] == '/root/va'


@pytest.mark.parametrize('stale', ['no_boundary', 'edited', 'transition', 'wrong_identity', 'ended'])
def test_local_revision_requires_fresh_current_phase_boundary(flow, stale):
    db, s, run, root, _ = flow
    step = failed_local(flow)
    args = dict(agent_id='/root/a', evidence='Current synchronous phase finished')
    if stale == 'wrong_identity':
        with pytest.raises(WorkflowConflictError):
            advance(s, step, 'local_revision_boundary', agent_id='/root/b', evidence=args['evidence'])
        return
    if stale == 'ended':
        observe(s, step, 'ended')
        with pytest.raises(WorkflowConflictError):
            advance(s, step, 'local_revision_boundary', **args)
        return
    if stale != 'no_boundary':
        step = advance(s, step, 'local_revision_boundary', **args)
    if stale == 'edited':
        (root / 'source.txt').write_text('edited after claimed phase completion')
    if stale == 'transition':
        step = observe(s, step, 'running')
    with pytest.raises(WorkflowConflictError):
        s.step_save(run['id'], 'a', 'a', 'va', step_id=step['id'], expected_revision=step['revision'], execution_kind='coordinator_local')


def serial_merge(flow):
    db, s, run, root, settings = flow
    a = finish(s, s.step_save(run['id'], 'a', 'a', 'va'))
    observe(s, a)
    manager = WorkspaceManager(db, settings)
    ws = manager.workspace_create(run['id'], 'b', copy_env=False)
    b = s.step_save(run['id'], 'b', 'b', 'vb', dependencies=[a['id']], workspace_id=ws['id'])
    for action in ('dispatch', 'accept'):
        b = advance(s, b, action)
    (Path(ws['path']) / 'source.txt').write_text('B first integration')
    b = advance(s, b, 'submit')
    b = verify(s, b, 'candidate')
    manager.workspace_merge(ws['id'])
    b = verify(s, b, 'integrated', 'FAIL', code=f'from pathlib import Path; assert Path({str(root / "source.txt")!r}).read_text() == "B repaired integration"')
    observe(s, b)
    return a, b, ws, manager


def test_real_serial_merge_fail_rework_and_original_final_reverification(flow):
    db, s, run, root, _ = flow
    a, b, ws, manager = serial_merge(flow)
    assert (root / 'source.txt').read_text() == 'B first integration'
    with pytest.raises(WorkflowConflictError, match='stale'):
        s._valid_verification(s.get_step(a['id']), 'integrated')
    b = advance(s, b, 'start_rework')
    assert b['attempt'] == 1 and b['owner_agent_id'] == '/root/b'
    (Path(ws['path']) / 'source.txt').write_text('B repaired integration')
    b = advance(s, b, 'submit')
    b = verify(s, b, 'candidate')
    manager.workspace_merge(ws['id'])
    b = verify(s, b, 'integrated', code=f'from pathlib import Path; assert Path({str(root / "source.txt")!r}).read_text() == "B repaired integration"')
    b = advance(s, b, 'complete')
    assert len(b['checkpoint']['merge_history']) == 2
    with pytest.raises(WorkflowConflictError, match='stale'):
        s.require_completion(run['id'])
    a = advance(s, a, 'reverify')
    a = verify(s, a, 'integrated', code=f'from pathlib import Path; assert Path({str(root / "source.txt")!r}).read_text() == "B repaired integration"')
    a = advance(s, a, 'complete')
    s.require_completion(run['id'])
    assert a['verifier_agent_id'] == '/root/va' and b['verifier_agent_id'] == '/root/vb'


@pytest.mark.parametrize('mutation', ['foreign', 'missing_proof', 'changed_contract', 'ended', 'budget'])
def test_serial_rework_rejects_unproven_or_changed_chain(flow, mutation):
    db, s, run, root, _ = flow
    a, b, ws, manager = serial_merge(flow)
    if mutation == 'foreign':
        (root / 'source.txt').write_text('foreign')
    elif mutation == 'missing_proof':
        step = s.get_step(b['id'])
        step['checkpoint']['merge_history'] = []
        db.connect().execute('UPDATE workflow_steps SET checkpoint_json=? WHERE id=?', (json.dumps(step['checkpoint']), b['id']))
    elif mutation == 'changed_contract':
        db.connect().execute('UPDATE workflow_steps SET contract_revision=contract_revision+1 WHERE id=?', (a['id'],))
    elif mutation == 'ended':
        observe(s, b, 'ended')
    else:
        db.connect().execute('UPDATE workflow_steps SET attempt=3,state=\'needs_decision\' WHERE id=?', (b['id'],))
    with pytest.raises(WorkflowConflictError):
        advance(s, b, 'start_rework')
    assert s.get_step(b['id'])['owner_agent_id'] == '/root/b'
