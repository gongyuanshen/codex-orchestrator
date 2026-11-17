"""M0 runner contract; source baseline must be independently fixed before case execution."""
import pytest
import hashlib
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / 'docs/verification/native-gap-next/m0/run_baseline.py'


def load_runner():
    spec = importlib.util.spec_from_file_location('native_gap_baseline', RUNNER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_six_cases_and_honest_unavailable_metrics():
    assert RUNNER.is_file(), 'M0 executable baseline runner is missing'
    runner = load_runner()
    cases = runner.load_cases()
    assert len(cases) == 6
    assert len({case['id'] for case in cases}) == 6
    assert {case['mode'] for case in cases} == {'boost', 'teamwork', 'swarm'}
    for case in cases:
        assert case['failure_condition'] and case['success_condition']
        assert case['contracts'] and case['fixtures'] and case['task']
        assert all(hashlib.sha256(f['test_source'].encode('utf-8')).hexdigest() == f['provenance']['sha256'] for f in case['fixtures'])
    metrics = runner.unavailable_metrics()
    assert metrics['tokens'] is None
    assert metrics['duplicate_native_side_effects'] is None
    assert metrics['empty_model_turns'] is None


def test_junit_uses_actual_counts(tmp_path):
    assert RUNNER.is_file(), 'M0 executable baseline runner is missing'
    runner = load_runner()
    path = tmp_path / 'results.xml'
    path.write_text('<testsuites><testsuite tests="2" failures="1" errors="0" skipped="0"><testcase name="one"><failure message="wrong"/></testcase><testcase name="two"/></testsuite></testsuites>', encoding='utf-8')
    assert runner.junit_counts(path) == {'tests': 2, 'failures': 1, 'errors': 0, 'skipped': 0}

def archived_contract(name):
    import orchestrator
    baseline = Path(orchestrator.__file__).resolve().parents[3]
    spec = importlib.util.spec_from_file_location('m0_reused_' + name, baseline / 'tests' / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_teamwork_disconnect_reopen_before_handoff_ack(tmp_path):
    import json
    from orchestrator.db import Database
    from orchestrator.workflow import WorkflowService
    contract = archived_contract('test_mode_workflows')
    fixture = contract.mode_flow.__wrapped__(tmp_path)
    create = next(fixture)
    try:
        db, flow, run, first, root, evidence, settings = create('teamwork')
        second = flow.step_save(run['id'], 'next', 'author', 'verifier', kind='milestone', dependencies=[first['id']])
        first = contract.finish(flow, first, evidence)
        first = contract.advance(flow, first, 'handoff', agent_id='/root/old', recipient_agent_id='/root/new', evidence='sent before disconnect')
        handoff = first['checkpoint']['handoff']
        db.close()
        reopened = Database(settings=settings)
        try:
            resumed = WorkflowService(reopened)
            first = resumed.get_step(first['id'])
            assert first['checkpoint']['handoff'] == handoff
            with pytest.raises(contract.WorkflowConflictError, match='recipient'):
                contract.advance(resumed, first, 'ack_handoff', agent_id='/root/wrong', evidence='wrong after reconnect')
            original = (root / 'source.txt').read_bytes()
            (root / 'source.txt').write_text('stale after reconnect', encoding='utf-8')
            with pytest.raises(contract.WorkflowConflictError, match='stale'):
                contract.advance(resumed, first, 'ack_handoff', agent_id='/root/new', evidence='stale after reconnect')
            (root / 'source.txt').write_bytes(original)
            first = contract.advance(resumed, first, 'ack_handoff', agent_id='/root/new', evidence='read durable exact checkpoint after reopen')
            second = contract.advance(resumed, resumed.get_step(second['id']), 'dispatch')
            assert second['state'] == 'dispatched'
            assert first['attempt'] == second['attempt'] == 0
            result = dict(database_reopened=True, handoff_preserved=True, dependent_state=second['state'], original_verifier=first['verifier_agent_id'])
            (tmp_path / 'handoff-reopen-evidence.json').write_text(json.dumps(result), encoding='utf-8')
            print(json.dumps(result))
        finally:
            reopened.close()
    finally:
        fixture.close()


def test_swarm_effect_then_disconnect_reconcile_without_replay(tmp_path):
    import json
    from orchestrator.board import BoardService
    from orchestrator.db import Database
    from orchestrator.inbox import InboxStore
    contract = archived_contract('test_mode_inbox')
    fixture = contract.linked.__wrapped__(tmp_path)
    db, flow, store, run, step, root = next(fixture)
    try:
        step = contract.advance(flow, step, 'dispatch')
        sent = store.send(**contract.args(run, step))['message']
        store.ack(sent['id'], 'author', 'accepted', 'about to execute once')
        effect = tmp_path / 'actual-side-effects.txt'
        with effect.open('a', encoding='utf-8') as target:
            target.write(sent['id'] + '\n')
        # Drop the connection after the real effect, before its terminal receipt.
        settings = db.settings
        db.close()
        reopened = Database(settings=settings)
        try:
            recovered = InboxStore(reopened, BoardService(reopened))
            observed = recovered.poll(run['id'], 'author')['messages']
            assert len(observed) == 1 and observed[0]['requires_reconciliation']
            assert observed[0]['id'] == sent['id']
            reopened.update_worker_status(run_id=run['id'], worker_name='author', status='stopped')
            recovered.ack(sent['id'], 'author', 'cancelled', 'effect file confirms action occurred; no replay after stop')
            assert recovered.get(sent['id'])['status'] == 'cancelled'
            duplicate = recovered.send(**contract.args(run, step))
            assert duplicate['duplicate'] and duplicate['message']['status'] == 'cancelled'
            assert recovered.poll(run['id'], 'author')['messages'] == []
            lines = effect.read_text(encoding='utf-8').splitlines()
            assert lines == [sent['id']]
            result = dict(database_reopened=True, accepted_required_reconciliation=True, actual_fixture_side_effects=len(lines), duplicate_fixture_side_effects=len(lines)-1, terminal_status='cancelled', terminal_resend_duplicate=True, stopped_worker_preserved=reopened.get_worker_by_name(run['id'], 'author')['status'])
            assert result['stopped_worker_preserved'] == 'stopped'
            (tmp_path / 'effect-reopen-evidence.json').write_text(json.dumps(result), encoding='utf-8')
            print(json.dumps(result))
        finally:
            reopened.close()
    finally:
        fixture.close()
