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


def test_creates_receipt_parent_but_never_reuses_final_directory(tmp_path):
    m, p = frozen(tmp_path)
    output_dir = tmp_path / 'verification-receipts' / m['artifact_id']
    collect(p, output_dir)
    validate_receipt(m, output_dir / 'receipt.json', require_success=True)
    original = (output_dir / 'receipt.json').read_bytes()
    with pytest.raises(FileExistsError):
        collect(p, output_dir)
    assert (output_dir / 'receipt.json').read_bytes() == original


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


@pytest.mark.skipif(sys.platform != 'win32', reason='Win32 console inheritance')
def test_windows_native_ctrl_break_cancels_owned_commands_before_next_launch(tmp_path):
    import subprocess

    code = 'from pathlib import Path;import sys,time;Path(sys.argv[1]).touch();time.sleep(6)'
    m, p = frozen(tmp_path, code, timeout=10)
    m['max_parallel'] = 2
    markers = [tmp_path / f'launched-{i}' for i in range(3)]
    m['commands'] = [
        dict(m['commands'][0], id=f'job-{i}', parallel_group='checks', argv=[sys.executable, '-c', code, str(marker)])
        for i, marker in enumerate(markers)
    ]
    p.write_text(json.dumps(m), encoding='utf-8')
    output_dir = tmp_path / 'output'
    controller = tmp_path / 'console_controller.py'
    controller.write_text('''
import ctypes, subprocess, sys, time
from pathlib import Path
proc = subprocess.Popen(
    [sys.executable, '-m', 'orchestrator.verify_runner', '--manifest', sys.argv[1], '--output-dir', sys.argv[2]],
    creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
)
try:
    deadline = time.monotonic() + 5
    while not all(Path(marker).is_file() for marker in sys.argv[3:]):
        if proc.poll() is not None or time.monotonic() >= deadline:
            raise RuntimeError('Collector did not start both owned processes')
        time.sleep(0.01)
    if not ctypes.windll.kernel32.GenerateConsoleCtrlEvent(1, proc.pid):
        raise ctypes.WinError()
    raise SystemExit(proc.wait(timeout=5))
finally:
    if proc.poll() is None:
        proc.kill()
        proc.wait(timeout=5)
''', encoding='utf-8')
    startup = subprocess.STARTUPINFO(dwFlags=subprocess.STARTF_USESHOWWINDOW, wShowWindow=subprocess.SW_HIDE)
    child = subprocess.run(
        [sys.executable, str(controller), str(p), str(output_dir), str(markers[0]), str(markers[1])],
        startupinfo=startup, creationflags=subprocess.CREATE_NEW_CONSOLE,
        capture_output=True, timeout=16, check=False,
    )
    assert child.returncode == 1, (child.stdout, child.stderr)
    assert not markers[2].exists()
    receipt = json.loads((output_dir / 'receipt.json').read_text(encoding='utf-8'))
    assert receipt['status'] == 'cancelled'
    assert [command['status'] for command in receipt['commands']] == ['cancelled', 'cancelled']
    validate_receipt(m, output_dir / 'receipt.json', require_success=False)


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


def test_parallel_group_batches_barriers_and_heartbeat_keepalive(tmp_path):
    from http.server import BaseHTTPRequestHandler, HTTPServer
    import threading
    import time

    heartbeats = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get('Content-Length') or 0)
            body = json.loads(self.rfile.read(length).decode('utf-8'))
            heartbeats.append((self.path, body))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"ok":true}')

        def log_message(self, format, *args):
            return

    server = HTTPServer(('127.0.0.1', 0), Handler)
    port = server.server_port
    srv_thread = threading.Thread(target=server.serve_forever, daemon=True)
    srv_thread.start()
    try:
        source = tmp_path / 'source'
        source.mkdir()
        (source / 'a.txt').write_text('original')
        manifest = dict(
            schema_version=1,
            evidence_level='local_collector_v1',
            run_id='r',
            step_id='s',
            artifact_id='a',
            scope='candidate',
            packet_version=1,
            attempt=1,
            contract_revision=1,
            verifier='v',
            verifier_agent_id='va',
            max_parallel=2,
            artifact=capture_content(source),
            commands=[
                dict(
                    id='barrier-build',
                    argv=[sys.executable, '-c', "print('built')"],
                    cwd=str(tmp_path),
                    timeout_sec=5,
                    env_keys=[],
                    criteria=['c1'],
                ),
                dict(
                    id='par-1',
                    argv=[sys.executable, '-c', "import time; time.sleep(0.25); print('p1')"],
                    cwd=str(tmp_path),
                    timeout_sec=5,
                    env_keys=[],
                    criteria=['c2'],
                    parallel_group='checks',
                ),
                dict(
                    id='par-2',
                    argv=[sys.executable, '-c', "import time; time.sleep(0.25); print('p2')"],
                    cwd=str(tmp_path),
                    timeout_sec=5,
                    env_keys=[],
                    criteria=['c3'],
                    parallel_group='checks',
                ),
                dict(
                    id='barrier-post',
                    argv=[sys.executable, '-c', "print('done')"],
                    cwd=str(tmp_path),
                    timeout_sec=5,
                    env_keys=[],
                    criteria=['c4'],
                ),
            ],
        )
        m_path = tmp_path / 'manifest.json'
        m_path.write_text(json.dumps(manifest), encoding='utf-8')
        out_dir = tmp_path / 'par-out'
        receipt = collect(
            m_path,
            out_dir,
            heartbeat_url=f'http://127.0.0.1:{port}/api/runs/r/workers/v/heartbeat',
            heartbeat_interval_sec=0.08,
        )
        assert receipt['status'] == 'finished'
        assert [c['command_id'] for c in receipt['commands']] == [
            'barrier-build',
            'par-1',
            'par-2',
            'barrier-post',
        ]
        assert all(c['exit_code'] == 0 for c in receipt['commands'])
        validate_receipt(manifest, out_dir / 'receipt.json', require_success=True)
        assert len(heartbeats) >= 1
        assert heartbeats[0][0] == '/api/runs/r/workers/v/heartbeat'
        assert 'active_commands' in heartbeats[0][1]

        # Tampering barrier-post to start before par-2 ended must be rejected by validate_receipt.
        tampered = json.loads((out_dir / 'receipt.json').read_text('utf-8'))
        tampered['commands'][3]['started_at'] = tampered['commands'][0]['started_at']
        (out_dir / 'receipt.json').write_text(json.dumps(tampered), encoding='utf-8')
        with pytest.raises(ValueError, match='invalid command timing'):
            validate_receipt(manifest, out_dir / 'receipt.json', require_success=True)
    finally:
        server.shutdown()
        server.server_close()


def test_parallel_receipt_times_start_after_a_slot_opens(tmp_path):
    m, p = frozen(tmp_path, "import time; time.sleep(0.25)")
    m['max_parallel'] = 2
    m['commands'] = [dict(m['commands'][0], id=f'job-{i}', parallel_group='checks') for i in range(3)]
    p.write_text(json.dumps(m), encoding='utf-8')
    output_dir = tmp_path / 'output'
    receipt = collect(p, output_dir)
    first, second, queued = receipt['commands']
    assert second['started_at'] < first['ended_at']
    assert queued['started_at'] >= min(first['ended_at'], second['ended_at'])
    validate_receipt(m, output_dir / 'receipt.json', require_success=True)


@pytest.mark.parametrize('use_ctrl_c', [True, False], ids=['ctrl-c', 'platform-stop'])
def test_parallel_interrupt_cancels_running_processes_and_skips_queued_commands(tmp_path, monkeypatch, capsys, use_ctrl_c):
    import _thread
    import signal
    import threading
    from orchestrator.verify_runner import main

    code = "from pathlib import Path;import sys,time;Path(sys.argv[1]).touch();time.sleep(1.25)"
    m, p = frozen(tmp_path, code)
    m['max_parallel'] = 2
    markers = [tmp_path / f'launched-{i}' for i in range(3)]
    m['commands'] = [
        dict(m['commands'][0], id=f'job-{i}', parallel_group='checks', argv=[sys.executable, '-c', code, str(marker)])
        for i, marker in enumerate(markers)
    ]
    p.write_text(json.dumps(m), encoding='utf-8')
    output_dir = tmp_path / 'output'
    monkeypatch.setattr(sys, 'argv', ['tri-orchestrator-verify', '--manifest', str(p), '--output-dir', str(output_dir)])
    platform_stop = signal.SIGBREAK if sys.platform == 'win32' else signal.SIGTERM
    signum = signal.SIGINT if use_ctrl_c else platform_stop
    original_stop_handler = signal.getsignal(platform_stop)
    stop = threading.Event()

    def interrupt_running_children():
        while not stop.wait(0.01):
            if all(marker.exists() for marker in markers[:2]):
                _thread.interrupt_main(signum)
                return

    interrupter = threading.Thread(target=interrupt_running_children)
    interrupter.start()
    try:
        exit_code = main()
    finally:
        stop.set()
        interrupter.join()
        signal.signal(platform_stop, original_stop_handler)
    assert exit_code == 1
    assert json.loads(capsys.readouterr().out)['status'] == 'cancelled'
    receipt = json.loads((output_dir / 'receipt.json').read_text(encoding='utf-8'))
    assert receipt['status'] == 'cancelled'
    assert not markers[2].exists()
    assert [(c['command_id'], c['status']) for c in receipt['commands']] == [('job-0', 'cancelled'), ('job-1', 'cancelled')]
    validate_receipt(m, output_dir / 'receipt.json', require_success=False)


def test_parallel_interrupt_preserves_owned_process_cleanup_failure(tmp_path, monkeypatch):
    import _thread
    import threading
    import time

    from orchestrator import verify_runner as runner
    from orchestrator.verify_process import ProcessFailure

    active = threading.Event()
    finish_cleanup = threading.Event()
    m, p = frozen(tmp_path)
    m['max_parallel'] = 2
    m['commands'] = [dict(m['commands'][0], id=f'job-{i}', parallel_group='checks') for i in range(2)]
    p.write_text(json.dumps(m), encoding='utf-8')

    def fail_process_cleanup(*args, cancel=None):
        active.set()
        if cancel is not None:
            assert cancel.wait(3)
        assert finish_cleanup.wait(3)
        raise ProcessFailure('cleanup_failed', 'injected owned-process cleanup failure')

    def interrupt_twice():
        if active.wait(3):
            _thread.interrupt_main()
            time.sleep(0.1)
            _thread.interrupt_main()
        finish_cleanup.set()

    monkeypatch.setattr(runner, 'run_owned', fail_process_cleanup)
    interrupter = threading.Thread(target=interrupt_twice)
    interrupter.start()
    output_dir = tmp_path / 'output'
    receipt = collect(p, output_dir)
    interrupter.join()
    assert receipt['status'] == 'cleanup_failed'
    assert 'injected owned-process cleanup failure' in receipt['error']
    validate_receipt(m, output_dir / 'receipt.json', require_success=False)


@pytest.mark.parametrize('timeout', [False, True], ids=['completed-command', 'preserve-command-timeout'])
def test_interrupt_during_heartbeat_shutdown_keeps_terminal_receipt(tmp_path, monkeypatch, capsys, timeout):
    import _thread
    import signal
    import threading
    import time

    from orchestrator import verify_runner as runner

    m, path = frozen(tmp_path, 'import time;time.sleep(0.5)', timeout=0.25 if timeout else 5)
    output_dir = tmp_path / 'output'
    stop_requested = threading.Event()
    release_request = threading.Event()
    stopped = []
    original_stop = runner._HeartbeatKeepalive.stop

    def pending_request(self):
        if self._active_provider():
            assert release_request.wait(5)

    def note_stop(self, *args, **kwargs):
        stopped.append(self._thread)
        stop_requested.set()
        return original_stop(self, *args, **kwargs)

    def interrupt_stop():
        if stop_requested.wait(5):
            _thread.interrupt_main()
            time.sleep(0.1)
        release_request.set()

    monkeypatch.setattr(runner._HeartbeatKeepalive, '_send_once', pending_request)
    monkeypatch.setattr(runner._HeartbeatKeepalive, 'stop', note_stop)
    monkeypatch.setattr(sys, 'argv', [
        'tri-orchestrator-verify', '--manifest', str(path), '--output-dir', str(output_dir),
        '--heartbeat-url', 'http://127.0.0.1', '--heartbeat-interval-sec', '0.01',
    ])
    platform_stop = signal.SIGBREAK if sys.platform == 'win32' else signal.SIGTERM
    handler = signal.getsignal(platform_stop)
    interrupter = threading.Thread(target=interrupt_stop)
    interrupter.start()
    try:
        try:
            assert runner.main() == 1
        except KeyboardInterrupt:
            pytest.fail('heartbeat shutdown propagated Ctrl+C without a terminal CLI result')
        assert stopped and not stopped[-1].is_alive()
    finally:
        release_request.set()
        interrupter.join()
        signal.signal(platform_stop, handler)

    expected = 'timeout' if timeout else 'cancelled'
    assert json.loads(capsys.readouterr().out)['status'] == expected
    receipt_path = output_dir / 'receipt.json'
    receipt = json.loads(receipt_path.read_text(encoding='utf-8'))
    assert receipt['status'] == expected and receipt['ended_at'] is None
    validate_receipt(m, receipt_path, require_success=False)


def test_slow_heartbeat_cannot_delay_terminal_receipt_past_shutdown_bound(tmp_path, monkeypatch, caplog):
    import threading

    from orchestrator import verify_runner as runner

    m, path = frozen(tmp_path, 'import time;time.sleep(0.25)')
    release_request = threading.Event()
    request_finished = threading.Event()

    def pending_request(self):
        if self._active_provider():
            release_request.wait(1.5)
            request_finished.set()

    monkeypatch.setattr(runner, '_KEEPALIVE_STOP_TIMEOUT_SEC', 0.1, raising=False)
    monkeypatch.setattr(runner._HeartbeatKeepalive, '_send_once', pending_request)
    output_dir = tmp_path / 'output'
    try:
        receipt = collect(path, output_dir, heartbeat_url='http://127.0.0.1', heartbeat_interval_sec=0.01)
        assert receipt['status'] == 'finished'
        assert not request_finished.is_set()
        assert 'heartbeat request is still in flight' in caplog.text
        validate_receipt(m, output_dir / 'receipt.json', require_success=True)
    finally:
        release_request.set()
        assert request_finished.wait(2)
