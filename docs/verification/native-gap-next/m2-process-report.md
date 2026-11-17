# M2 Windows process ownership research

Date: 2026-11-17. Scope: read-only product research; this report is the only repository change owned by this research task.

## Decision

Use an unnamed Windows Job Object per collected command, `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`, and **creation-time** `PROC_THREAD_ATTRIBUTE_JOB_LIST` through `ctypes` / `CreateProcessW`. Do not launch normally and assign afterward. Do not implement the collector with `Popen(CREATE_SUSPENDED)` followed by assignment: that leaves an orphan window if the collector dies between those calls.

Microsoft explicitly describes the suspended-create/assign crash window and states that JOB_LIST assignment happens before the initial thread runs. JOB_LIST requires Windows 10 / Windows Server 2016 or newer. Unsupported OS or rejected nested-job configuration must be a startup failure, not an unowned-process fallback. [Microsoft explanation](https://devblogs.microsoft.com/oldnewthing/20230209-00/?p=107812/), [attribute API](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-updateprocthreadattribute).

This is local process ownership, not a hostile-code sandbox. Children created by an external service, scheduled task broker, or other process outside this job are not automatically owned descendants. Same-user hostile handle duplication or mutation is outside M2's evidence guarantee.

## Local findings (actually inspected)

- Repository runtime: `G:\工具\codex-orchestrator\.venv\Scripts\python.exe`, CPython **3.14.4**, 64-bit AMD64; standard library `E:\Python\Lib\subprocess.py`.
- OS reports Windows major 10, minor 0, build 22631.
- `Popen._execute_child` serializes argument sequences with `list2cmdline`, invokes `_winapi.CreateProcess`, retains the process handle, and immediately closes the returned initial-thread handle. Therefore a suspended `Popen` cannot simply be resumed through a documented public Popen API.
- Public `STARTUPINFO.lpAttributeList` is not a generic raw Win32 attribute bridge. CPython's versioned `_winapi.c` has no `job_list` support. Passing an invented dictionary key does not install JOB_LIST. [CPython source](https://raw.githubusercontent.com/python/cpython/v3.14.4/Modules/_winapi.c), [Python subprocess documentation](https://docs.python.org/3.14/library/subprocess.html).
- Existing production Popen sites were found in `plugin/src/orchestrator/daemon.py` and `plugin/src/orchestrator/mcp_shim.py`; they are service startup sites, not the new collector, and should not be changed to solve M2.
- `plugin/pyproject.toml` currently declares Python >=3.12 and has no process-management dependency. `ctypes`, `msvcrt`, `subprocess`, `time`, and file APIs cover this design without adding one.

## Minimum implementation sequence

1. At the collector boundary validate frozen argv as a nonempty sequence of strings, no embedded NUL; validate cwd, timeout, environment keys/values, and evidence destinations. Freeze the resolved executable path alongside the original argv. Resolve an unqualified executable using the explicitly selected environment PATH, rather than silently changing which executable runs.
2. Create the running/incomplete record and open separate stdout/stderr files outside the accepted artifact scope. Use binary file handles directly; this avoids two pipe-reader threads, pipe EOF retention by descendants, and large-output memory buffering. Open stdin as `NUL` so an unattended command cannot hang awaiting input. An interactive-input requirement would need a separate explicit contract.
3. `CreateJobObjectW(NULL, NULL)` with noninheritable handle. Set extended-limit information (`JobObjectExtendedLimitInformation = 9`) with `LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE (0x2000)`. Do not enable either breakaway flag. Failure aborts before creating a process.
4. Build STARTUPINFOEX with TWO attributes: JOB_LIST (`0x0002000D`, array containing only this job) and HANDLE_LIST (`0x00020002`, stdin/stdout/stderr handles only). Duplicate those three handles as inheritable; never mark the job inheritable or put it in HANDLE_LIST. Set `STARTF_USESTDHANDLES`, `cb = sizeof(STARTUPINFOEXW)`, and `bInheritHandles=TRUE`. All `ctypes` API signatures must specify pointer-sized HANDLE/SIZE_T arguments and return types; default `c_int` return truncates handles on x64.
5. Call `CreateProcessW` with absolute `lpApplicationName`, writable Unicode command-line buffer from `subprocess.list2cmdline(argv)`, exact cwd, an explicitly constructed Unicode environment block, and `EXTENDED_STARTUPINFO_PRESENT | CREATE_UNICODE_ENVIRONMENT`. Keep attribute buffers and both arrays alive until after DeleteProcThreadAttributeList. No shell, no .bat/.cmd implicit interpreter. Validate and reject batch files at the boundary if the contract forbids shell execution. Windows provides one command-line string, not native argv: `list2cmdline` is appropriate for CRT/Python-style parsing; arbitrary custom parsers cannot be promised identical argv semantics. [CreateProcessW](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-createprocessw), [attribute lifetime/handle inheritance](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-updateprocthreadattribute).
6. Retain process handle for exit-code/wait identity; close initial-thread handle after successful launch. Close the parent's inheritable stdio duplicates promptly. The collector alone retains the job handle; do not let daemon/test observer retain an extra handle in the collector-death test. Inherited/duplicated job handles prevent last-close cleanup. [Job handle lifetime](https://learn.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-createjobobjecta).
7. Wait in bounded intervals, using `time.monotonic()` for deadline and wall-clock UTC only for receipt timestamps. Distinguish timeout, requested cancellation, startup failure, I/O failure, cleanup failure, and genuine process exit. If an exit code equals STILL_ACTIVE, a signalled process handle still proves it exited: do not infer liveness from numeric code alone.
8. On timeout/cancellation/error call `TerminateJobObject` and check the BOOL result. Then poll `QueryInformationJobObject(JobObjectBasicAccountingInformation=1)` until `ActiveProcesses == 0` or a distinct bounded cleanup deadline expires. Retain the job handle until this observation. Root exit alone does not prove child-tree completion. TerminateJobObject is not a complete-cleanup receipt by itself. [Termination API](https://learn.microsoft.com/en-us/windows/win32/api/jobapi2/nf-jobapi2-terminatejobobject), [accounting structure](https://learn.microsoft.com/en-us/windows/win32/api/winnt/ns-winnt-jobobject_basic_accounting_information).
9. Apply the same tree policy to normal root exit: if descendants remain, they are still part of this command. The smallest finite policy is terminate remaining descendants and mark the run incomplete/failed, not a clean command success. Record the root's actual exit code separately; do not replace it with the cleanup termination code. A command intentionally spawning a durable daemon needs an explicit different contract, not an implicit exception.
10. Only after tree-zero confirmation, close/flush output files, hash complete bytes, and write a finished receipt. A cleanup deadline failure retains incomplete status and includes Win32 error context. Preserve the primary exception AND cleanup errors, for example via ExceptionGroup; no empty catches or cleanup exception masking. Finally close all owned handles. If the collector itself is killed, Windows closes its sole job handle and KILL_ON_JOB_CLOSE handles owned members; no success receipt can be written by the dead collector.

The job's no-breakaway setting prevents ordinary children from escaping by requesting CREATE_BREAKAWAY_FROM_JOB. Nested parent host jobs can impose additional limits or reject association; surface the exact CreateProcess/Win32 failure and preserve running/incomplete evidence. Do not request breakaway to evade host restrictions. [Job Objects](https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects), [AssignProcessToJobObject](https://learn.microsoft.com/en-us/windows/win32/api/jobapi2/nf-jobapi2-assignprocesstojobobject).

Do not use taskkill by image name, enumerate/kill all python.exe, or reconstruct ownership from parent PID snapshots. PID reuse, root exit, and concurrent unrelated processes make those approaches incorrect. Job membership defines the owned set.

## Probe actually executed

A dependency-free ctypes probe was executed through the repository Python with source on stdin (no probe file added). It created an unnamed kill-on-close job, initialized a one-attribute JOB_LIST, created a Python child sleeping 60 seconds using CreateProcessW/EXTENDED_STARTUPINFO_PRESENT, queried active membership, terminated the job, and polled accounting up to five seconds. All Win32 calls checked failure results. Stdout/stderr redirection and forced collector death were NOT included in this first probe.

Actual output, exit code 0:

```text
JOB_LIST create=ok; active_before=1; terminate=ok; active_after=0; pid=35180
```

This proves the basic API/ABI path on this machine only; it is not full M2 acceptance or an installed-package claim.

## Required runnable acceptance cases for the implementation

Use `sys.executable -c` fixtures, never a shell wrapper. An outer Python test owns the collector process handle and a same-image unrelated sentinel. The command fixture creates a child and grandchild immediately and writes each PID plus readiness token to a private temporary directory. The outer test opens SYNCHRONIZE process handles while members are alive; retained process handles, rather than later PID lookups, prevent PID-reuse ambiguity.

| Case | Trigger and required observation |
| --- | --- |
| Exact argv | Python fixture JSON-prints `sys.argv[1:]`; assert byte-decoded JSON equality for Chinese, spaces, empty string, quotes, trailing backslashes, and shell metacharacters. |
| Real output/exit | Write distinct binary stdout/stderr, then `sys.exit(7)`; assert exact file bytes and real exit 7, not PASS. |
| Large output | Write multiple MiB to each stream; assert full byte counts/hashes and bounded returned summary. |
| Missing executable | Frozen absolute path does not exist; startup failure, no finished receipt, preserved running/partial evidence. |
| Timeout tree | Root/child/grandchild sleep; deadline must terminate all; active count reaches zero; all retained member handles signal; unrelated sentinel remains unsignalled. |
| Cancellation tree | Cancel only after descendant readiness; same assertions, reason=cancelled. |
| Collector forced death | Outer test kills collector using its retained handle after readiness, without owning a duplicate job handle; each member handle signals within deadline, sentinel survives, receipt remains incomplete. |
| Root exits before child | Root spawns sleeping child and returns 0; collector terminates surviving descendants, reports incomplete/failed rather than fabricated clean success. |
| Pre-creation cancellation | No CreateProcess call and no child readiness artifact. |
| Host nested job | Run collector inside a restrictive test-owned parent job; either valid nested ownership works or startup fails explicitly; never launch outside host ownership. |
| Capture/cleanup fault | Inject output creation/hash failure and a query/termination API failure at the test seam; retain error context and partial output; never finished-success. |
| Startup kill stress | Repeatedly terminate collector around CreateProcess; assert no fixture process retained alive; an empirical stress result is not a formal OS transactional guarantee. |

For observer enumeration, JobObjectBasicProcessIdList buffers can be incomplete and require resize/retry; polling accounting is smaller for the live collector's own zero-member check. Do not give the observer an extra job handle while trying to test last-handle-close termination.

## Unproven boundary and scope

Microsoft documents creation-time assignment before the initial thread runs, but the cited API contract does not explicitly specify every rollback outcome if the creating thread/process is killed *inside* CreateProcessW. Do not present the basic probe or a finite kill stress test as a mathematical guarantee for every interrupted kernel-call interleaving. JOB_LIST removes the explicitly documented userspace create-then-assign gap and is the smallest supported design for the requested collector; stronger claims would need separately established OS guarantees or a different supervised execution contract.

Skipped: no product code, package changes, new dependency, unrelated service-startup refactor, forced-death tree test, installation validation, or complete M2 test run.
Risk: command-line parsers and externally brokered processes are outside arbitrary-executable argv/tree guarantees; all job cleanup failures must remain visible and invalidate completion.
