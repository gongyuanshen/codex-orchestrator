import sys
import threading
import time
from pathlib import Path

import pytest
from orchestrator.verify_process import run_owned, ProcessFailure


def test_real_process_and_timeout(tmp_path):
    with (tmp_path / 'out').open('wb') as out, (tmp_path / 'err').open('wb') as err:
        assert run_owned([sys.executable, '-c', 'raise SystemExit(7)'], str(tmp_path), dict(__import__('os').environ), 5, out, err) == 7
        with pytest.raises(ProcessFailure, match='timeout'):
            run_owned([sys.executable, '-c', 'import time;time.sleep(30)'], str(tmp_path), dict(__import__('os').environ), .1, out, err)


def test_pre_cancel_creates_no_process(tmp_path):
    cancel = threading.Event()
    cancel.set()
    with (tmp_path / 'out').open('wb') as out, (tmp_path / 'err').open('wb') as err:
        with pytest.raises(ProcessFailure, match='cancelled'):
            run_owned([sys.executable, '-c', "open('created','w').close()"], str(tmp_path), dict(__import__('os').environ), 5, out, err, cancel=cancel)
    assert not (tmp_path / 'created').exists()


def test_root_exit_with_child_is_incomplete(tmp_path):
    with (tmp_path / 'out').open('wb') as out, (tmp_path / 'err').open('wb') as err:
        with pytest.raises(ProcessFailure, match='descendants') as failure:
            run_owned([sys.executable, '-c', 'import subprocess,sys;subprocess.Popen([sys.executable,"-c","import time;time.sleep(30)"])'], str(tmp_path), dict(__import__('os').environ), 5, out, err)
        assert failure.value.exit_code == 0


def _tree_code():
    grandchild = "import os,time;open('grandchild.pid','w').write(str(os.getpid()));time.sleep(60)"
    child = "import subprocess,sys,os,time;open('child.pid','w').write(str(os.getpid()));subprocess.Popen([sys.executable,'-c'," + repr(grandchild) + "]);time.sleep(60)"
    return "import subprocess,sys,os,time;open('root.pid','w').write(str(os.getpid()));subprocess.Popen([sys.executable,'-c'," + repr(child) + "]);time.sleep(60)"


def _wait_ready(tmp_path):
    deadline = time.monotonic() + 10
    paths = [tmp_path / (name + '.pid') for name in ('root', 'child', 'grandchild')]
    while not all(p.is_file() and p.stat().st_size for p in paths):
        assert time.monotonic() < deadline, 'tree failed to become ready'
        time.sleep(.01)
    return [int(p.read_text()) for p in paths]


def _retain(pids):
    import ctypes
    from ctypes import wintypes
    k = ctypes.WinDLL('kernel32', use_last_error=True)
    k.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k.OpenProcess.restype = wintypes.HANDLE
    k.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    k.WaitForSingleObject.restype = wintypes.DWORD
    k.CloseHandle.argtypes = [wintypes.HANDLE]
    k.CloseHandle.restype = wintypes.BOOL
    handles = [k.OpenProcess(0x100000, False, pid) for pid in pids]
    assert all(handles), 'cannot retain actual process identity'
    return k, handles


@pytest.mark.skipif(sys.platform != 'win32', reason='Windows Job guarantee')
@pytest.mark.parametrize('mode', ['timeout', 'cancel'])
def test_owned_tree_cleanup_preserves_unrelated(tmp_path, mode):
    import os
    import subprocess
    sentinel = subprocess.Popen([sys.executable, '-c', 'import time;time.sleep(60)'])
    cancel = threading.Event()
    result = []
    def execute():
        try:
            with (tmp_path / 'out').open('wb') as out, (tmp_path / 'err').open('wb') as err:
                run_owned([sys.executable, '-c', _tree_code()], str(tmp_path), dict(os.environ), 1 if mode == 'timeout' else 30, out, err, cancel=cancel)
        except BaseException as exc:
            result.append(exc)
    thread = threading.Thread(target=execute)
    thread.start()
    k = None
    handles = []
    try:
        k, handles = _retain(_wait_ready(tmp_path))
        if mode == 'cancel':
            cancel.set()
        thread.join(10)
        assert not thread.is_alive()
        assert len(result) == 1 and isinstance(result[0], ProcessFailure)
        assert result[0].status == ('timeout' if mode == 'timeout' else 'cancelled')
        assert all(k.WaitForSingleObject(h, 5000) == 0 for h in handles)
        assert sentinel.poll() is None
    finally:
        cancel.set()
        thread.join(10)
        for h in handles:
            assert k.CloseHandle(h)
        sentinel.terminate()
        sentinel.wait(5)


@pytest.mark.skipif(sys.platform != 'win32', reason='Windows Job guarantee')
def test_forced_collector_death_keeps_incomplete_receipt(tmp_path):
    import json
    import os
    import subprocess
    from orchestrator.artifacts import capture_content
    source = tmp_path / 'source'
    source.mkdir()
    (source / 'a').write_text('source')
    m = dict(schema_version=1, evidence_level='local_collector_v1', run_id='r', step_id='s', artifact_id='a', scope='candidate', packet_version=1, attempt=1, contract_revision=1, verifier='v', verifier_agent_id='va', artifact=capture_content(source), commands=[dict(id='tree', argv=[sys.executable, '-c', _tree_code()], cwd=str(tmp_path), timeout_sec=30, env_keys=[], criteria=['tree'])])
    manifest = tmp_path / 'manifest.json'
    manifest.write_text(json.dumps(m))
    output = tmp_path / 'output'
    collector = subprocess.Popen([sys.executable, '-m', 'orchestrator.verify_runner', '--manifest', str(manifest), '--output-dir', str(output)], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    sentinel = subprocess.Popen([sys.executable, '-c', 'import time;time.sleep(60)'])
    k = None
    handles = []
    try:
        k, handles = _retain(_wait_ready(tmp_path))
        collector.kill()
        collector.communicate(timeout=5)
        assert all(k.WaitForSingleObject(h, 5000) == 0 for h in handles)
        assert sentinel.poll() is None
        receipt = json.loads((output / 'receipt.json').read_text())
        assert receipt['status'] == 'running' and receipt['ended_at'] is None
        from orchestrator.verify_runner import validate_receipt
        evidence = validate_receipt(m, output / 'receipt.json', require_success=False)
        assert len(evidence) == 3
        with pytest.raises(ValueError):
            validate_receipt(m, output / 'receipt.json', require_success=True)
    finally:
        if collector.poll() is None:
            collector.kill()
            collector.communicate(timeout=5)
        for h in handles:
            assert k.CloseHandle(h)
        sentinel.terminate()
        sentinel.wait(5)


@pytest.mark.skipif(sys.platform == 'win32', reason='POSIX process group contract')
def test_posix_timeout_and_cancel_real_group(tmp_path):
    import os
    with (tmp_path / 'out').open('wb') as out, (tmp_path / 'err').open('wb') as err:
        assert run_owned([sys.executable, '-c', 'raise SystemExit(7)'], str(tmp_path), {}, 5, out, err) == 7
        with pytest.raises(ProcessFailure, match='timeout'):
            run_owned([sys.executable, '-c', 'import time;time.sleep(30)'], str(tmp_path), {}, .1, out, err)


@pytest.mark.skipif(sys.platform != 'win32', reason='Windows Job fault injection')
@pytest.mark.parametrize('api', ['TerminateJobObject', 'QueryInformationJobObject'])
def test_cleanup_api_failure_is_visible(tmp_path, monkeypatch, api):
    import ctypes
    import os
    real_dll = ctypes.WinDLL
    class FaultDLL:
        def __init__(self):
            self.real = real_dll('kernel32', use_last_error=True)
            def fail(*args):
                ctypes.set_last_error(5)
                return 0
            self.fail = fail
        def __getattr__(self, name):
            return self.fail if name == api else getattr(self.real, name)
    monkeypatch.setattr(ctypes, 'WinDLL', lambda *a, **k: FaultDLL())
    with (tmp_path / 'out').open('wb') as out, (tmp_path / 'err').open('wb') as err:
        with pytest.raises(ProcessFailure) as failure:
            run_owned([sys.executable, '-c', 'import time;time.sleep(30)'], str(tmp_path), dict(os.environ), .05, out, err)
    assert failure.value.status == 'cleanup_failed'
    assert api in str(failure.value)
    assert 'primary=' in str(failure.value) and 'cleanup=' in str(failure.value)


def test_relative_executable_and_relative_path_use_frozen_cwd(tmp_path):
    import os
    executable = Path(sys.executable)
    for argv, env in [([f'.{os.sep}{executable.name}', '-c', 'print(42)'], {}), ([executable.name, '-c', 'print(42)'], {'PATH': '.'})]:
        with (tmp_path / 'out').open('wb') as out, (tmp_path / 'err').open('wb') as err:
            assert run_owned(argv, str(executable.parent), env, 5, out, err) == 0
        assert (tmp_path / 'out').read_bytes().strip() == b'42'
