import json
import sys
from pathlib import Path

import pytest
from orchestrator.artifacts import capture_content, digest
from orchestrator.verify_runner import collect, validate_receipt


def frozen(tmp_path, code="print('ok')", args=(), timeout=5):
    source = tmp_path / 'source'
    source.mkdir()
    (source / 'a.txt').write_text('original')
    manifest = dict(schema_version=1, evidence_level='local_collector_v1', run_id='r', step_id='s', artifact_id='a', scope='candidate', packet_version=1, attempt=1, contract_revision=1, verifier='v', verifier_agent_id='va', artifact=capture_content(source), commands=[dict(id='test', argv=[sys.executable, '-c', code, *args], cwd=str(tmp_path), timeout_sec=timeout, env_keys=[], criteria=['works'])])
    path = tmp_path / 'manifest.json'
    path.write_text(json.dumps(manifest), encoding='utf-8')
    return manifest, path


def test_real_nonzero_separate_bytes(tmp_path):
    m, p = frozen(tmp_path, "import os;os.write(1,b'out');os.write(2,b'err');raise SystemExit(7)")
    receipt = collect(p, tmp_path / 'output')
    assert receipt['status'] == 'finished'
    assert receipt['commands'][0]['exit_code'] == 7
    assert Path(receipt['commands'][0]['stdout']['path']).read_bytes() == b'out'
    assert Path(receipt['commands'][0]['stderr']['path']).read_bytes() == b'err'
    validate_receipt(m, tmp_path / 'output/receipt.json', require_success=False)
    with pytest.raises(ValueError):
        validate_receipt(m, tmp_path / 'output/receipt.json', require_success=True)


def test_exact_argv_large_output_and_tamper(tmp_path):
    args = ['中文 空格', '', 'a"b', 'x\\', '&|>']
    m, p = frozen(tmp_path, "import sys,json,os;os.write(1,json.dumps(sys.argv[1:],ensure_ascii=False).encode('utf-8'));os.write(2,b'e'*3000000)", args)
    r = collect(p, tmp_path / 'output')
    c = r['commands'][0]
    assert json.loads(Path(c['stdout']['path']).read_text('utf-8')) == args
    assert c['stderr']['bytes'] == 3000000
    validate_receipt(m, tmp_path / 'output/receipt.json', require_success=True)
    Path(c['stderr']['path']).write_bytes(b'changed')
    with pytest.raises(ValueError):
        validate_receipt(m, tmp_path / 'output/receipt.json', require_success=True)


def test_missing_program_and_timeout(tmp_path):
    m, p = frozen(tmp_path)
    m['commands'][0]['argv'][0] = str(tmp_path / 'missing.exe')
    p.write_text(json.dumps(m))
    r = collect(p, tmp_path / 'missing-output')
    assert r['status'] != 'finished'
    assert r['commands'][0]['exit_code'] is None
    m['commands'][0]['argv'] = [sys.executable, '-c', 'import time;time.sleep(30)']
    m['commands'][0]['timeout_sec'] = .2
    p.write_text(json.dumps(m))
    r = collect(p, tmp_path / 'timeout-output')
    assert r['status'] == 'timeout'
    validate_receipt(m, tmp_path / 'timeout-output/receipt.json', require_success=False)


def test_checkpoint_and_restoration_boundary(tmp_path):
    m, p = frozen(tmp_path, "from pathlib import Path;Path('source/a.txt').write_text('changed')")
    r = collect(p, tmp_path / 'output')
    assert r['status'] != 'finished'
    with pytest.raises(ValueError):
        validate_receipt(m, tmp_path / 'output/receipt.json', require_success=True)
    (tmp_path / 'source/a.txt').write_text('original')
    m['commands'][0]['argv'] = [sys.executable, '-c', "from pathlib import Path;p=Path('source/a.txt');b=p.read_bytes();p.write_text('temporary');assert p.read_text()=='temporary';p.write_bytes(b)"]
    p.write_text(json.dumps(m))
    r = collect(p, tmp_path / 'restored-output')
    assert r['status'] == 'finished'  # Checkpoints intentionally cannot detect restored mutations.
    validate_receipt(m, tmp_path / 'restored-output/receipt.json', require_success=True)
    changed = dict(m, attempt=2)
    with pytest.raises(ValueError):
        validate_receipt(changed, tmp_path / 'restored-output/receipt.json', require_success=True)



def test_environment_is_explicit_and_never_serialized(tmp_path, monkeypatch):
    monkeypatch.setenv('M2_DECLARED_SECRET', 'secret-value-do-not-serialize')
    monkeypatch.setenv('M2_UNDECLARED', 'must-not-inherit')
    m, p = frozen(tmp_path, "import os;assert os.environ['M2_DECLARED_SECRET']=='secret-value-do-not-serialize';assert 'M2_UNDECLARED' not in os.environ;print('checked')")
    m['commands'][0]['env_keys'] = ['M2_DECLARED_SECRET']
    p.write_text(json.dumps(m))
    r = collect(p, tmp_path / 'output')
    assert r['status'] == 'finished' and r['commands'][0]['exit_code'] == 0
    assert 'secret-value-do-not-serialize' not in (tmp_path / 'output/receipt.json').read_text()


def test_capture_fault_is_incomplete(tmp_path, monkeypatch):
    import orchestrator.verify_runner as runner
    m, p = frozen(tmp_path)
    def fail_hash(path):
        raise OSError('injected output hash failure')
    monkeypatch.setattr(runner, '_log', fail_hash)
    r = collect(p, tmp_path / 'output')
    assert r['status'] == 'collection_failed'
    assert 'injected output hash failure' in r['error']
    assert r['ended_at'] is None
    assert (tmp_path / 'output/0000.stdout').read_bytes()


@pytest.mark.parametrize('field,value', [('scope', []), ('attempt', True), ('verifier', ''), ('commands', [])])
def test_manifest_rejects_invalid_frozen_binding(tmp_path, field, value):
    from orchestrator.verify_runner import validate_manifest
    m, _ = frozen(tmp_path)
    m[field] = value
    with pytest.raises(ValueError):
        validate_manifest(m)


def test_capture_fault_preserves_primary_failure(tmp_path, monkeypatch):
    import orchestrator.verify_runner as runner
    m, p = frozen(tmp_path)
    m['commands'][0]['argv'][0] = str(tmp_path / 'missing.exe')
    p.write_text(json.dumps(m))
    def fail_hash(path):
        raise OSError('injected output hash failure')
    monkeypatch.setattr(runner, '_log', fail_hash)
    r = collect(p, tmp_path / 'output')
    assert 'executable not found' in r['error']
    assert 'injected output hash failure' in r['error']


@pytest.mark.parametrize('mutation', ['early_receipt_end', 'overlapping_commands'])
def test_receipt_rejects_impossible_timeline(tmp_path, mutation):
    m, p = frozen(tmp_path)
    if mutation == 'overlapping_commands':
        m['commands'].append(dict(m['commands'][0], id='second'))
        p.write_text(json.dumps(m))
    collect(p, tmp_path / 'output')
    receipt_path = tmp_path / 'output/receipt.json'
    receipt = json.loads(receipt_path.read_text())
    if mutation == 'early_receipt_end':
        receipt['ended_at'] = receipt['started_at']
    else:
        receipt['commands'][1]['started_at'] = receipt['commands'][0]['started_at']
    receipt_path.write_text(json.dumps(receipt))
    with pytest.raises(ValueError):
        validate_receipt(m, receipt_path, require_success=True)


def test_receipt_rejects_non_file_partial_output(tmp_path):
    m, p = frozen(tmp_path)
    collect(p, tmp_path / 'output')
    path = tmp_path / 'output/receipt.json'
    r = json.loads(path.read_text())
    r['status'] = 'running'
    r['ended_at'] = None
    c = r['commands'][0]
    c['status'] = 'running'
    c['ended_at'] = None
    log = Path(c['stdout']['path'])
    log.unlink()
    log.mkdir()
    c['stdout']['bytes'] = c['stdout']['sha256'] = None
    path.write_text(json.dumps(r))
    with pytest.raises(ValueError):
        validate_receipt(m, path, require_success=False)


@pytest.mark.parametrize('exit_code', [0, 7])
def test_cli_bounded_real_summary(tmp_path, exit_code):
    import subprocess
    m, p = frozen(tmp_path, f"import os;os.write(1,b'x'*3000000);raise SystemExit({exit_code})")
    child = subprocess.run([sys.executable, '-m', 'orchestrator.verify_runner', '--manifest', str(p), '--output-dir', str(tmp_path / 'output')], capture_output=True, check=False)
    assert child.returncode == (0 if exit_code == 0 else 1)
    assert len(child.stdout) < 1000 and child.stderr == b''
    summary = json.loads(child.stdout)
    assert summary['status'] == 'finished'
    assert summary['manifest_digest'] == digest(m)
    assert Path(summary['receipt_path']).is_file()


def test_case_insensitive_systemroot_and_env_profile_never_leak_or_serialize(tmp_path, monkeypatch):
    import os
    from orchestrator.verify_runner import ENV_PROFILE_KEYS, build_isolated_env, lookup_env_value

    found, val = lookup_env_value('systemroot', {'SystemRoot': r'C:\Windows'}, case_insensitive=True)
    assert found is True and val == r'C:\Windows'
    found_rev, val_rev = lookup_env_value('SystemRoot', {'SYSTEMROOT': r'C:\Windows'}, case_insensitive=True)
    assert found_rev is True and val_rev == r'C:\Windows'

    isolated = build_isolated_env(
        ENV_PROFILE_KEYS['windows_minimal_py'],
        {'SystemRoot': r'C:\Windows', 'Path': r'C:\Windows\System32', 'UNDECLARED_KEY': 'hidden-val'},
        case_insensitive=True,
        command_id='env_test',
    )
    assert 'UNDECLARED_KEY' not in isolated
    assert [k for k in isolated if k.upper() == 'SYSTEMROOT'] == ['SYSTEMROOT']
    assert isolated['SYSTEMROOT'] == r'C:\Windows'

    monkeypatch.setenv('M6_RUNNER_SECRET', 'never-inherit-or-write')
    monkeypatch.delenv('PYTHONIOENCODING', raising=False)
    m, p = frozen(
        tmp_path,
        "import os, socket; assert 'M6_RUNNER_SECRET' not in os.environ; s = socket.socket(); s.close(); print('win-socket-ok')",
    )
    m['commands'][0]['env_keys'] = (
        ['systemroot', 'SystemRoot', 'PATH', 'PYTHONIOENCODING']
        if os.name == 'nt'
        else list(ENV_PROFILE_KEYS['posix_minimal_py'])
    )
    p.write_text(json.dumps(m), encoding='utf-8')
    r = collect(p, tmp_path / 'env-profile-out')
    assert r['status'] == 'finished' and r['commands'][0]['exit_code'] == 0
    assert Path(r['commands'][0]['stdout']['path']).read_text('utf-8').strip() == 'win-socket-ok'
    validate_receipt(m, tmp_path / 'env-profile-out/receipt.json', require_success=True)
    receipt_raw = (tmp_path / 'env-profile-out/receipt.json').read_text('utf-8')
    assert 'never-inherit-or-write' not in receipt_raw
    if os.name == 'nt' and os.environ.get('SYSTEMROOT'):
        assert os.environ['SYSTEMROOT'] not in receipt_raw
