"""Windows creation-time Job ownership; never launches an unowned fallback."""
from __future__ import annotations

import ctypes as C
from ctypes import wintypes as W
import os
import subprocess
import time


class ProcessFailure(RuntimeError):
    def __init__(self, status: str, message: str, exit_code: int | None = None):
        super().__init__(f'{status}: {message}')
        self.status = status
        self.exit_code = exit_code


def run_owned(argv, cwd, env, timeout, stdout, stderr, *, cancel=None):
    if os.name != 'nt':
        return _run_posix(argv, cwd, env, timeout, stdout, stderr, cancel=cancel)
    if cancel is not None and cancel.is_set():
        raise ProcessFailure('cancelled', 'cancelled before creation')
    executable = None
    program = argv[0]
    raw_pathext = next((value for key, value in env.items() if key.upper() == 'PATHEXT'), '')
    pathext_list: list[str] = []
    seen_exts: set[str] = set()
    for ext in (raw_pathext.split(os.pathsep) if raw_pathext else ['.COM', '.EXE', '.BAT', '.CMD']):
        clean_ext = ext.strip().strip('"').strip()
        if not clean_ext:
            continue
        if not clean_ext.startswith('.'):
            clean_ext = '.' + clean_ext
        lower_ext = clean_ext.lower()
        if lower_ext not in seen_exts:
            seen_exts.add(lower_ext)
            pathext_list.append(clean_ext)
    if '.exe' not in seen_exts:
        seen_exts.add('.exe')
        pathext_list.insert(0, '.exe')

    if os.path.dirname(program) or os.path.splitdrive(program)[0]:
        candidates = [os.path.join(cwd, program)]
    else:
        search = next((value for key, value in env.items() if key.upper() == 'PATH'), '')
        candidates = []
        if search:
            for raw_dir in search.split(os.pathsep):
                clean_dir = raw_dir.strip().strip('"').strip()
                if not clean_dir:
                    continue
                candidates.append(os.path.join(cwd, clean_dir, program))

    for candidate in candidates:
        cand_ext = os.path.splitext(candidate)[1].lower()
        if cand_ext and cand_ext in seen_exts:
            probe_list = [candidate]
        else:
            probe_list = [*(candidate + ext for ext in pathext_list), candidate]
        for expanded in probe_list:
            if os.path.isfile(expanded):
                executable = os.path.abspath(expanded)
                break
        if executable is not None:
            break
    if executable is None:
        raise ProcessFailure('startup_failed', f'executable not found: {argv[0]}')
    if os.path.splitext(executable)[1].lower() in ('.bat', '.cmd'):
        raise ProcessFailure('startup_failed', 'batch scripts require a shell and are unsupported')
    import msvcrt
    k = C.WinDLL('kernel32', use_last_error=True)
    H, P, S = W.HANDLE, C.c_void_p, C.c_size_t

    class IO(C.Structure):
        _fields_ = [(n, C.c_ulonglong) for n in ('read_ops', 'write_ops', 'other_ops', 'read_bytes', 'write_bytes', 'other_bytes')]

    class LIMIT(C.Structure):
        _fields_ = [('process_time', C.c_longlong), ('job_time', C.c_longlong), ('flags', W.DWORD), ('min_ws', S), ('max_ws', S), ('active_limit', W.DWORD), ('affinity', S), ('priority', W.DWORD), ('scheduling', W.DWORD)]

    class EXTENDED(C.Structure):
        _fields_ = [('basic', LIMIT), ('io', IO), ('process_memory', S), ('job_memory', S), ('peak_process', S), ('peak_job', S)]

    class ACCOUNT(C.Structure):
        _fields_ = [('user', C.c_longlong), ('kernel', C.c_longlong), ('period_user', C.c_longlong), ('period_kernel', C.c_longlong), ('faults', W.DWORD), ('total', W.DWORD), ('active', W.DWORD), ('terminated', W.DWORD)]

    class STARTUP(C.Structure):
        _fields_ = [('cb', W.DWORD), ('reserved', W.LPWSTR), ('desktop', W.LPWSTR), ('title', W.LPWSTR), ('x', W.DWORD), ('y', W.DWORD), ('width', W.DWORD), ('height', W.DWORD), ('chars_x', W.DWORD), ('chars_y', W.DWORD), ('fill', W.DWORD), ('flags', W.DWORD), ('show', W.WORD), ('reserved_bytes', W.WORD), ('reserved_ptr', P), ('stdin', H), ('stdout', H), ('stderr', H)]

    class STARTUPEX(C.Structure):
        _fields_ = [('startup', STARTUP), ('attributes', P)]

    class PROCESS(C.Structure):
        _fields_ = [('process', H), ('thread', H), ('pid', W.DWORD), ('tid', W.DWORD)]

    signatures = {
        'CreateJobObjectW': ([P, W.LPCWSTR], H),
        'SetInformationJobObject': ([H, C.c_int, P, W.DWORD], W.BOOL),
        'QueryInformationJobObject': ([H, C.c_int, P, W.DWORD, P], W.BOOL),
        'TerminateJobObject': ([H, W.UINT], W.BOOL),
        'InitializeProcThreadAttributeList': ([P, W.DWORD, W.DWORD, C.POINTER(S)], W.BOOL),
        'UpdateProcThreadAttribute': ([P, W.DWORD, S, P, S, P, P], W.BOOL),
        'DeleteProcThreadAttributeList': ([P], None),
        'CreateProcessW': ([W.LPCWSTR, W.LPWSTR, P, P, W.BOOL, W.DWORD, P, W.LPCWSTR, P, P], W.BOOL),
        'CloseHandle': ([H], W.BOOL),
        'GetCurrentProcess': ([], H),
        'DuplicateHandle': ([H, H, H, C.POINTER(H), W.DWORD, W.BOOL, W.DWORD], W.BOOL),
        'WaitForSingleObject': ([H, W.DWORD], W.DWORD),
        'GetExitCodeProcess': ([H, C.POINTER(W.DWORD)], W.BOOL),
    }
    for name, (args, result) in signatures.items():
        function = getattr(k, name)
        function.argtypes, function.restype = args, result

    def checked(result, operation):
        if not result:
            raise OSError(C.get_last_error(), f'{operation}: {C.FormatError(C.get_last_error())}')
        return result

    handles = []
    attributes = None
    initialized = False
    job = None
    created = False
    exit_code = None
    primary = None

    def active():
        account = ACCOUNT()
        checked(k.QueryInformationJobObject(job, 1, C.byref(account), C.sizeof(account), None), 'QueryInformationJobObject')
        return account.active

    def terminate():
        checked(k.TerminateJobObject(job, 1), 'TerminateJobObject')
        deadline = time.monotonic() + 5
        while active():
            if time.monotonic() >= deadline:
                raise ProcessFailure('cleanup_failed', 'owned Job still contains active processes', exit_code)
            time.sleep(.01)

    try:
        job = checked(k.CreateJobObjectW(None, None), 'CreateJobObjectW')
        handles.append(job)
        limits = EXTENDED()
        limits.basic.flags = 0x2000
        checked(k.SetInformationJobObject(job, 9, C.byref(limits), C.sizeof(limits)), 'SetInformationJobObject')
        size = S()
        k.InitializeProcThreadAttributeList(None, 2, 0, C.byref(size))
        if not size.value:
            checked(False, 'InitializeProcThreadAttributeList size')
        attributes = C.create_string_buffer(size.value)
        checked(k.InitializeProcThreadAttributeList(attributes, 2, 0, C.byref(size)), 'InitializeProcThreadAttributeList')
        initialized = True
        jobs = (H * 1)(job)
        checked(k.UpdateProcThreadAttribute(attributes, 0, 0x2000D, jobs, C.sizeof(jobs), None, None), 'JOB_LIST')
        duplicates = []
        with open(os.devnull, 'rb') as stdin:
            for stream in (stdin, stdout, stderr):
                handle = H()
                checked(k.DuplicateHandle(k.GetCurrentProcess(), msvcrt.get_osfhandle(stream.fileno()), k.GetCurrentProcess(), C.byref(handle), 0, True, 2), 'DuplicateHandle')
                duplicates.append(handle.value)
                handles.append(handle.value)
            stdio = (H * 3)(*duplicates)
            checked(k.UpdateProcThreadAttribute(attributes, 0, 0x20002, stdio, C.sizeof(stdio), None, None), 'HANDLE_LIST')
            startup = STARTUPEX()
            startup.startup.cb = C.sizeof(startup)
            startup.startup.flags = 0x100
            startup.startup.stdin, startup.startup.stdout, startup.startup.stderr = duplicates
            startup.attributes = C.cast(attributes, P)
            process = PROCESS()
            commandline = C.create_unicode_buffer(subprocess.list2cmdline(argv))
            environment = C.create_unicode_buffer('\0'.join(f'{key}={value}' for key, value in sorted(env.items(), key=lambda item: item[0].upper())) + '\0\0')
            if cancel is not None and cancel.is_set():
                raise ProcessFailure('cancelled', 'cancelled before creation')
            checked(k.CreateProcessW(executable, commandline, None, None, True, 0x80000 | 0x400, environment, cwd, C.byref(startup), C.byref(process)), 'CreateProcessW')
            created = True
            handles.extend([process.process, process.thread])
        for handle in duplicates + [process.thread]:
            checked(k.CloseHandle(handle), 'CloseHandle startup')
            handles.remove(handle)
        deadline = time.monotonic() + timeout
        while True:
            wait = k.WaitForSingleObject(process.process, 20)
            if wait == 0:
                code = W.DWORD()
                checked(k.GetExitCodeProcess(process.process, C.byref(code)), 'GetExitCodeProcess')
                exit_code = code.value
                break
            if wait != 258:
                checked(False, 'WaitForSingleObject')
            if cancel is not None and cancel.is_set():
                raise ProcessFailure('cancelled', 'cancellation requested')
            if time.monotonic() >= deadline:
                raise ProcessFailure('timeout', 'command deadline exceeded')
        if active():
            raise ProcessFailure('descendants_alive', 'root exited with active descendants', exit_code)
    except BaseException as exc:
        primary = exc
        if created:
            try:
                terminate()
            except BaseException as cleanup:
                primary = ProcessFailure('cleanup_failed', f'primary={exc!r}; cleanup={cleanup!r}', exit_code)
    finally:
        if initialized:
            k.DeleteProcThreadAttributeList(attributes)
        for handle in reversed(handles):
            if not k.CloseHandle(handle):
                error = OSError(C.get_last_error(), 'CloseHandle')
                primary = ProcessFailure('cleanup_failed', f'primary={primary!r}; cleanup={error!r}', exit_code)
    if primary is not None:
        if isinstance(primary, (ProcessFailure, KeyboardInterrupt)):
            raise primary
        raise ProcessFailure('startup_failed' if not created else 'collection_failed', str(primary), exit_code) from primary
    return exit_code


def _run_posix(argv, cwd, env, timeout, stdout, stderr, *, cancel=None):
    """POSIX groups clean ordinary cancellation, not forced collector SIGKILL."""
    import signal
    if cancel is not None and cancel.is_set():
        raise ProcessFailure('cancelled', 'cancelled before creation')
    process = None
    primary = None
    code = None
    try:
        process = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                                   stdout=stdout, stderr=stderr, start_new_session=True)
        deadline = time.monotonic() + timeout
        while process.poll() is None:
            if cancel is not None and cancel.is_set():
                raise ProcessFailure('cancelled', 'cancellation requested')
            if time.monotonic() >= deadline:
                raise ProcessFailure('timeout', 'command deadline exceeded')
            time.sleep(.02)
        code = process.returncode
        try:
            os.killpg(process.pid, 0)
        except ProcessLookupError:
            return code
        raise ProcessFailure('descendants_alive', 'root exited with active descendants', code)
    except BaseException as exc:
        primary = exc
        if process is not None:
            try:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass  # ESRCH is explicit proof that the owned group no longer exists.
                process.wait(timeout=5)
                deadline = time.monotonic() + 5
                while True:
                    try:
                        os.killpg(process.pid, 0)
                    except ProcessLookupError:
                        break
                    if time.monotonic() >= deadline:
                        raise ProcessFailure('cleanup_failed', 'owned process group still exists', code)
                    time.sleep(.01)
            except BaseException as cleanup:
                primary = ProcessFailure('cleanup_failed', f'primary={exc!r}; cleanup={cleanup!r}', code)
        if isinstance(primary, (ProcessFailure, KeyboardInterrupt)):
            raise primary
        raise ProcessFailure('startup_failed' if process is None else 'collection_failed', str(primary), code) from primary
