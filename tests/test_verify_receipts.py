from pathlib import Path
import pytest
from test_workflow_gates import flow

def test_new_runs_default_receipts_workflow(flow):
    db, _, _, _, project, _ = flow
    assert db.create_run(mode="boost", title="new", project_root=project)["workflow_version"] == 5

def test_workflow3_requires_commands(flow):
    db, service, run, step, _, _ = flow
    db.connect().execute("UPDATE runs SET workflow_version=3 WHERE id=?", (run["id"],))
    with pytest.raises(ValueError, match="verification_commands"):
        service.capture(step["id"], paths=["source.txt"])

import json
import sys
from orchestrator.artifacts import digest
from test_workflow_gates import advance


def prepare(flow):
    db, service, run, step, project, evidence = flow
    db.connect().execute("UPDATE runs SET workflow_version=3 WHERE id=?", (run["id"],))
    for action in ('dispatch', 'accept', 'submit'):
        step = advance(service, step, action)
    return db, service, run, step, project, evidence


def commands(project, code="print('actual verification')"):
    return [{'id': 'actual-test', 'argv': [sys.executable, '-c', code],
             'cwd': str(project.parent), 'timeout_sec': 10, 'env_keys': [], 'criteria': ['exact output']}]


def capture(flow, code="print('actual verification')", scope='candidate'):
    db, service, run, step, project, evidence = flow
    artifact = service.capture(step['id'], scope, paths=['source.txt'], verification_commands=commands(project, code))
    if scope == 'candidate':
        step = advance(service, service.get_step(step['id']), 'challenge', artifact_id=artifact['id'],
                       evidence='Independent actual challenge', agent_id='/root/verifier')
    return artifact


def collect(artifact, output):
    from orchestrator.verify_runner import collect as execute
    return execute(Path(artifact['manifest_path']), output)


def record_receipt(service, step, artifact, evidence, output, verdict='PASS', status=None, key='actual'):
    return service.record(step['id'], artifact['id'], 'verifier', verdict,
        [{'criterion': 'exact output', 'status': status or verdict}], [str(evidence)], key,
        receipt_files=[str(output / 'receipt.json')])


def test_capture_freezes_noncircular_manifest_and_bounded_summary(flow):
    state = prepare(flow)
    db, service, run, step, project, evidence = state
    artifact = capture(state)
    manifest = artifact['descriptor']['verification_manifest']
    assert json.loads(Path(artifact['manifest_path']).read_text(encoding='utf-8')) == manifest
    assert digest(manifest) == artifact['manifest_digest']
    assert 'verification_manifest' not in manifest['artifact']
    assert 'required_paths' not in manifest['artifact']
    assert manifest['artifact_id'] == artifact['id']
    assert service._artifact_current(artifact)
    summary = service.summary(run['id'])['artifacts'][0]
    assert summary['evidence_level'] == 'local_collector_v1'
    assert summary['verification_command_count'] == 1
    assert 'descriptor' not in summary and 'descriptor_json' not in summary
    Path(artifact['manifest_path']).unlink()
    assert not service._artifact_current(artifact)
    assert not Path(artifact['manifest_path']).exists()


@pytest.mark.parametrize('mutation', ['receipt', 'stdout', 'stderr', 'source', 'manifest'])
def test_gate_rechecks_receipt_and_all_raw_outputs(flow, mutation):
    state = prepare(flow)
    db, service, run, step, project, evidence = state
    artifact = capture(state)
    output = project.parent / 'collector'
    receipt = collect(artifact, output)
    result = record_receipt(service, step, artifact, evidence, output)
    registered = {item['path'] for item in result['evidence_manifest']}
    assert str((output / 'receipt.json').resolve()) in registered
    for stream in ('stdout', 'stderr'):
        assert receipt['commands'][0][stream]['path'] in registered
    if mutation == 'receipt':
        path = output / 'receipt.json'
    elif mutation in ('stdout', 'stderr'):
        path = Path(receipt['commands'][0][mutation]['path'])
    elif mutation == 'source':
        path = project / 'source.txt'
    else:
        path = Path(artifact['manifest_path'])
    path.write_bytes(path.read_bytes() + b'changed')
    with pytest.raises(ValueError):
        advance(service, service.get_step(step['id']), 'begin_integrated_review')


@pytest.mark.parametrize('status', ['FAIL', 'NOT_RUN'])
def test_exit_zero_does_not_replace_criterion_judgment(flow, status):
    state = prepare(flow)
    _, service, _, step, project, evidence = state
    artifact = capture(state)
    output = project.parent / 'collector'
    collect(artifact, output)
    with pytest.raises(ValueError, match='every criterion'):
        record_receipt(service, step, artifact, evidence, output, status=status)


def test_real_fail_same_author_repair_same_verifier_integrated_gate(flow):
    state = prepare(flow)
    db, service, run, step, project, evidence = state
    artifact = capture(state, "import sys; print('real fail'); sys.exit(7)")
    output = project.parent / 'failure'
    receipt = collect(artifact, output)
    assert receipt['commands'][0]['exit_code'] == 7
    with pytest.raises(ValueError):
        record_receipt(service, step, artifact, evidence, output)
    record_receipt(service, step, artifact, evidence, output, verdict='FAIL', key='failure')
    step = advance(service, service.get_step(step['id']), 'start_rework')
    (project / 'source.txt').write_text('repaired', encoding='utf-8')
    step = advance(service, step, 'submit')
    state = db, service, run, step, project, evidence
    fixed = capture(state)
    repaired = project.parent / 'repaired'
    collect(fixed, repaired)
    with pytest.raises(ValueError, match='another delivery'):
        record_receipt(service, step, artifact, evidence, output, key='stale-attempt')
    record_receipt(service, step, fixed, evidence, repaired, key='retest')
    step = advance(service, service.get_step(step['id']), 'begin_integrated_review')
    state = db, service, run, step, project, evidence
    integrated = capture(state, scope='integrated')
    final = project.parent / 'integrated'
    collect(integrated, final)
    record_receipt(service, step, integrated, evidence, final, key='integrated')
    step = advance(service, service.get_step(step['id']), 'complete')
    assert step['owner_agent_id'] == '/root/author'
    assert step['verifier_agent_id'] == '/root/verifier'
    assert step['attempt'] == 1
    assert db.update_run(run['id'], status='completed')['status'] == 'completed'


def test_legacy_rejects_new_command_input(flow):
    _, service, _, step, project, _ = flow
    with pytest.raises(ValueError, match='requires workflow3'):
        service.capture(step['id'], paths=['source.txt'], verification_commands=commands(project))



@pytest.mark.parametrize('mutation', ['manifest_digest', 'command_order', 'stdout', 'stderr'])
def test_record_rejects_changed_receipt_before_registration(flow, mutation):
    state = prepare(flow)
    _, service, _, step, project, evidence = state
    artifact = capture(state)
    output = project.parent / 'collector'
    receipt = collect(artifact, output)
    if mutation == 'manifest_digest':
        receipt['manifest_digest'] = '0' * 64
    elif mutation == 'command_order':
        receipt['commands'][0]['command_id'] = 'different command'
    else:
        Path(receipt['commands'][0][mutation]['path']).write_bytes(b'changed raw output')
    if mutation in ('manifest_digest', 'command_order'):
        (output / 'receipt.json').write_text(json.dumps(receipt), encoding='utf-8')
    with pytest.raises(ValueError):
        record_receipt(service, step, artifact, evidence, output)


def test_capture_rejects_unmapped_criteria_and_freezes_no_env_values(flow, monkeypatch):
    state = prepare(flow)
    _, service, _, step, project, _ = state
    command = commands(project)
    command[0]['criteria'] = ['not in frozen step']
    with pytest.raises(ValueError, match='cover exactly'):
        service.capture(step['id'], paths=['source.txt'], verification_commands=command)
    command = commands(project)
    command[0]['env_keys'] = ['M2_ONLY_IN_VERIFIER_TERMINAL']
    monkeypatch.delenv('M2_ONLY_IN_VERIFIER_TERMINAL', raising=False)
    artifact = service.capture(step['id'], paths=['source.txt'], verification_commands=command)
    assert artifact['descriptor']['verification_manifest']['commands'][0]['env_keys'] == ['M2_ONLY_IN_VERIFIER_TERMINAL']
    assert service._artifact_current(artifact)


def test_manifest_whitespace_change_is_not_recreated_or_accepted(flow):
    state = prepare(flow)
    artifact = capture(state)
    path = Path(artifact['manifest_path'])
    path.write_bytes(path.read_bytes() + b' ')
    assert not state[1]._artifact_current(artifact)
    assert path.read_bytes().endswith(b' ')


def test_incomplete_fail_registers_all_present_raw_bytes(flow):
    state = prepare(flow)
    _, service, _, step, project, evidence = state
    artifact = capture(state, "import sys,time; print('partial', flush=True); time.sleep(2)")
    manifest = artifact['descriptor']['verification_manifest']
    # A real timeout produces truthful partial outputs and incomplete evidence.
    # Freeze a short timeout in a second capture; no receipt state is fabricated.
    command = commands(project, "import time; print('partial', flush=True); time.sleep(2)")
    command[0]['timeout_sec'] = .2
    artifact = service.capture(step['id'], paths=['source.txt'], verification_commands=command)
    advance(service, service.get_step(step['id']), 'challenge', artifact_id=artifact['id'],
            evidence='Independent timeout case', agent_id='/root/verifier')
    output = project.parent / 'timeout'
    receipt = collect(artifact, output)
    assert receipt['status'] != 'finished'
    assert receipt['ended_at'] is None
    with pytest.raises(ValueError):
        record_receipt(service, step, artifact, evidence, output)
    result = record_receipt(service, step, artifact, evidence, output, verdict='FAIL')
    registered = {item['path']: item['sha256'] for item in result['evidence_manifest']}
    import hashlib
    for name in ('stdout', 'stderr'):
        log = Path(receipt['commands'][0][name]['path'])
        assert registered[str(log)] == hashlib.sha256(log.read_bytes()).hexdigest()



def test_workflow3_http_catalog_freeze_collect_record_and_gate(tmp_path):
    import asyncio
    import subprocess
    import httpx
    from orchestrator.config import Settings
    from orchestrator.daemon import create_app
    from orchestrator.rpc_handlers import get_tool_definitions

    project = tmp_path / 'project'
    project.mkdir()
    (project / 'source.txt').write_text('HTTP source', encoding='utf-8')
    evidence = tmp_path / 'judgment.txt'
    evidence.write_text('Independent criterion judgment', encoding='utf-8')

    async def scenario():
        app = create_app(Settings(data_dir=tmp_path / 'state'), start_watchdog=False, manage_pid_file=False)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://test') as client:
                catalog = (await client.get('/api/tools')).json()['tools']
                assert catalog == get_tool_definitions()
                definitions = {tool['name']: tool['inputSchema'] for tool in catalog}
                assert 'verification_commands' in definitions['artifact_capture']['properties']
                assert 'receipt_files' in definitions['verification_record']['properties']
                assert 'workflow_version' not in definitions['run_create']['properties']
                assert not any('execute' in name or 'command' in name for name in definitions)

                async def rpc(tool, **arguments):
                    response = await client.post('/api/rpc', json={'tool': tool, 'arguments': arguments})
                    assert response.status_code == 200, response.text
                    return response.json()['result']

                run = await rpc('run_create', mode='swarm', title='Workflow3 actual HTTP', project_root=str(project))
                assert run['workflow_version'] == 5
                await rpc('task_packet_save', run_id=run['id'], title='Frozen HTTP', goal='actual collection',
                          file_ownership={'author': ['source.txt']}, acceptance_criteria=['exact output'])
                for name, role in [('author', 'coder'), ('verifier', 'verifier')]:
                    await rpc('worker_register', run_id=run['id'], name=name, role=role, agent_id='/root/' + name)
                step = await rpc('workflow_step_save', run_id=run['id'], step_key='http', owner='author', verifier='verifier')
                for action in ('dispatch', 'accept', 'submit'):
                    step = await rpc('workflow_step_advance', step_id=step['id'], action=action, expected_revision=step['revision'])
                for scope in ('candidate', 'integrated'):
                    artifact = await rpc('artifact_capture', step_id=step['id'], scope=scope,
                                         paths=['source.txt'], verification_commands=commands(project))
                    output = tmp_path / scope
                    result = subprocess.run([sys.executable, '-m', 'orchestrator.verify_runner', '--manifest',
                        artifact['manifest_path'], '--output-dir', str(output)], capture_output=True, check=True)
                    summary = json.loads(result.stdout)
                    assert summary['manifest_digest'] == artifact['manifest_digest']
                    assert len(result.stdout) < 2000
                    await rpc('verification_record', step_id=step['id'], artifact_id=artifact['id'],
                        verifier='verifier', verdict='PASS', criteria_results=[{'criterion': 'exact output', 'status': 'PASS'}],
                        evidence_files=[str(evidence)], receipt_files=[summary['receipt_path']], key=scope)
                    step = app.state.workflow.get_step(step['id'])
                    action = 'begin_integrated_review' if scope == 'candidate' else 'complete'
                    step = await rpc('workflow_step_advance', step_id=step['id'], action=action, expected_revision=step['revision'])
                assert (await rpc('run_update', run_id=run['id'], status='completed'))['status'] == 'completed'
    asyncio.run(scenario())



def test_backend_manifest_allows_location_outside_partial_captured_scope(tmp_path):
    from orchestrator.config import Settings
    from orchestrator.db import Database
    from orchestrator.workflow import WorkflowService
    project = tmp_path / 'project'
    project.mkdir()
    (project / 'source.txt').write_text('actual source', encoding='utf-8')
    db = Database(settings=Settings(data_dir=project / 'local-state'))
    try:
        run = db.create_run(mode='swarm', title='partial source', project_root=project)
        db.save_task_packet(run['id'], title='Frozen', goal='scope', file_ownership={'author': ['source.txt']},
                            acceptance_criteria=['exact output'])
        for name, role in [('author', 'coder'), ('verifier', 'verifier')]:
            db.upsert_worker(run['id'], name, role=role, agent_id='/root/' + name)
        service = WorkflowService(db)
        step = service.step_save(run['id'], 'partial', 'author', 'verifier')
        artifact = service.capture(step['id'], paths=['source.txt'], verification_commands=commands(project))
        assert Path(artifact['manifest_path']).is_relative_to(project)
        assert service._artifact_current(artifact)
        with pytest.raises(ValueError, match='overlaps captured artifact scope'):
            service.capture(step['id'], verification_commands=commands(project))
    finally:
        db.close()
