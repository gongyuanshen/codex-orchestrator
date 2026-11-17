"""Launch the shared daemon through local WMI, outside a client's short-lived Job."""
from __future__ import annotations

import os
import subprocess

import pythoncom
import win32api
import win32con
import win32event
import win32process
from win32com.client import GetObject


class WindowsDaemonProcess:
    """Owned process handle; PID reuse cannot change poll/wait/termination targets."""

    def __init__(self, pid: int, args: list[str]):
        self.pid = pid
        self.args = args
        # PyHANDLE closes on release; retaining it also preserves the process identity after exit.
        self._handle = win32api.OpenProcess(
            win32con.SYNCHRONIZE | win32con.PROCESS_QUERY_LIMITED_INFORMATION | win32con.PROCESS_TERMINATE,
            False, pid,
        )

    def poll(self) -> int | None:
        if win32event.WaitForSingleObject(self._handle, 0) == win32con.WAIT_TIMEOUT:
            return None
        return win32process.GetExitCodeProcess(self._handle)

    def wait(self, timeout: float | None = None) -> int:
        milliseconds = win32event.INFINITE if timeout is None else max(0, int(timeout * 1000))
        if win32event.WaitForSingleObject(self._handle, milliseconds) == win32con.WAIT_TIMEOUT:
            raise subprocess.TimeoutExpired(self.args, timeout)
        return win32process.GetExitCodeProcess(self._handle)

    def terminate(self) -> None:
        win32api.TerminateProcess(self._handle, 1)

    kill = terminate


def spawn_daemon(command: list[str], environment: dict[str, str]) -> WindowsDaemonProcess:
    # Open logs before importing the application so bootstrap/import errors remain observable.
    bootstrap = (
        "import os,sys,runpy; "
        "sys.stdin=open(os.devnull); "
        "sys.stdout=sys.stderr=open(os.path.join(os.environ['TRI_ORCH_DATA_DIR'],'daemon.log'),'a',encoding='utf-8'); "
        "runpy.run_module('orchestrator.daemon',run_name='__main__')"
    )
    args = [command[0], "-c", bootstrap, *command[3:]]
    pythoncom.CoInitializeEx(pythoncom.COINIT_APARTMENTTHREADED)
    service = startup = process = inputs = output = None
    try:
        service = GetObject(r"winmgmts:{impersonationLevel=impersonate}!\\.\root\cimv2")
        startup = service.Get("Win32_ProcessStartup").SpawnInstance_()
        startup.ShowWindow = 0
        # BREAKAWAY releases the WMI provider's quota job, not the calling SDK's job.
        startup.CreateFlags = (subprocess.CREATE_NO_WINDOW | subprocess.CREATE_BREAKAWAY_FROM_JOB
                               | 0x00000400)  # CREATE_UNICODE_ENVIRONMENT
        startup.EnvironmentVariables = [f"{key}={value}" for key, value in environment.items()]
        process = service.Get("Win32_Process")
        inputs = process.Methods_("Create").InParameters.SpawnInstance_()
        inputs.CommandLine = subprocess.list2cmdline(args)
        inputs.CurrentDirectory = os.getcwd()
        inputs.ProcessStartupInformation = startup
        output = process.ExecMethod_("Create", inputs)
        if output.ReturnValue != 0:
            raise OSError(f"Local WMI daemon creation failed: Win32_Process.Create return={output.ReturnValue}")
        return WindowsDaemonProcess(int(output.ProcessId), args)
    finally:
        # Release all apartment-bound proxies before tearing down that apartment.
        output = inputs = process = startup = service = None
        pythoncom.CoUninitialize()
