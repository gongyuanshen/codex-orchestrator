"""Host-side deterministic read-only challenge fan-out CLI (M10).

Executes bounded read-only ``codex exec`` slices concurrently from the host
terminal without adding any command-execution RPC endpoint to the 18790 daemon.
"""

from __future__ import annotations

import argparse
import asyncio
import ctypes as C
from ctypes import wintypes as W
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from typing import Any
import uuid

from orchestrator.artifacts import capture_content, digest, recapture

SCHEMA_VERSION = 1
EVIDENCE_LEVEL = "challenge_fanout_v1"
MAX_WORKERS_CAP = 4
BUILTIN_SCHEMA_FILENAME = "builtin_challenge_schema.json"
CODEX_WORKER_ISOLATION_ARGS: tuple[str, ...] = (
    "--disable",
    "plugins",
    "--disable",
    "hooks",
    "--disable",
    "multi_agent",
    "-c",
    "mcp_servers={}",
    "-c",
    'web_search="disabled"',
)
ALLOWED_SEVERITIES = ("critical", "high", "medium", "low", "info")
ALLOWED_VERDICTS = ("PASS", "FAIL")
_FINDING_FIELDS = (
    "severity",
    "category",
    "file",
    "line",
    "criterion_id",
    "title",
    "evidence",
    "counterexample",
)
_SLICE_OUTPUT_FIELDS = ("findings", "summary", "verdict")
_OPTIONAL_STR_BINDINGS = ("run_id", "step_id", "artifact_id")
_OPTIONAL_INT_BINDINGS = ("packet_version", "attempt", "contract_revision")
_ALLOWED_SLICE_KEYS = frozenset(
    {"slice_id", "paths", "criteria_ids", "prompt", "focus", "title", "timeout_sec"}
)
_ALLOWED_MANIFEST_KEYS = frozenset(
    {
        "schema_version",
        "target_dir",
        "slices",
        "max_workers",
        "timeout_sec",
        "title",
        "description",
        "mode",
        *_OPTIONAL_STR_BINDINGS,
        *_OPTIONAL_INT_BINDINGS,
    }
)

BUILTIN_CHALLENGE_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "TriOrchestratorChallengeSliceOutput",
    "type": "object",
    "additionalProperties": False,
    "required": list(_SLICE_OUTPUT_FIELDS),
    "properties": {
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": list(_FINDING_FIELDS),
                "properties": {
                    "severity": {
                        "type": "string",
                        "enum": list(ALLOWED_SEVERITIES),
                    },
                    "category": {"type": "string", "minLength": 1},
                    "file": {"type": "string", "minLength": 1},
                    "line": {"type": ["integer", "null"], "minimum": 1},
                    "criterion_id": {"type": "string", "minLength": 1},
                    "title": {"type": "string", "minLength": 1},
                    "evidence": {"type": "string", "minLength": 1},
                    "counterexample": {"type": ["string", "null"]},
                },
            },
        },
        "summary": {"type": "string", "minLength": 1},
        "verdict": {"type": "string", "enum": list(ALLOWED_VERDICTS)},
    },
}


class ChallengeValidationError(ValueError):
    """Raised when a challenge manifest, CLI argument, or slice output violates schema."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical_json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")


def _pretty_json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with tmp_path.open("wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp_path, path)
    except BaseException:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def write_builtin_schema(directory: Path) -> Path:
    """Atomically write deterministic ``builtin_challenge_schema.json`` into ``directory``."""
    directory.mkdir(parents=True, exist_ok=True)
    schema_path = directory / BUILTIN_SCHEMA_FILENAME
    _atomic_write_bytes(schema_path, _canonical_json_bytes(BUILTIN_CHALLENGE_SCHEMA))
    return schema_path


def _validate_nonempty_str(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or "\0" in value:
        raise ChallengeValidationError(f"{label} must be a non-empty string without NUL")
    return value.strip()


def _validate_positive_timeout(value: Any, label: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value <= 0
    ):
        raise ChallengeValidationError(f"{label} must be a finite positive number")
    return float(value)


def _validate_output_outside_target(path: Path, target_dir: Path, label: str) -> None:
    resolved = path.resolve(strict=False)
    if resolved == target_dir or resolved.is_relative_to(target_dir) or target_dir.is_relative_to(resolved):
        raise ChallengeValidationError(
            f"{label} must not overlap target_dir ({target_dir}): {resolved}"
        )


def validate_challenge_manifest(manifest: Any) -> dict[str, Any]:
    """Validate and normalize a frozen challenge manifest dictionary."""
    if not isinstance(manifest, dict):
        raise ChallengeValidationError("Challenge manifest must be a JSON object")
    unknown_top = set(manifest.keys()) - _ALLOWED_MANIFEST_KEYS
    if unknown_top:
        raise ChallengeValidationError(
            f"Challenge manifest contains unsupported fields: {sorted(unknown_top)}"
        )

    schema_ver = manifest.get("schema_version", SCHEMA_VERSION)
    if isinstance(schema_ver, bool) or schema_ver != SCHEMA_VERSION:
        raise ChallengeValidationError(
            f"Unsupported challenge manifest schema_version: {schema_ver!r}"
        )

    raw_target = _validate_nonempty_str(manifest.get("target_dir"), "target_dir")
    target_candidate = Path(raw_target)
    if target_candidate.is_symlink():
        raise ChallengeValidationError(f"target_dir must not be a symbolic link: {raw_target}")
    try:
        target_dir = target_candidate.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ChallengeValidationError(f"target_dir does not exist: {raw_target}") from exc
    if not target_dir.is_dir():
        raise ChallengeValidationError(f"target_dir is not a directory: {target_dir}")

    raw_slices = manifest.get("slices")
    if not isinstance(raw_slices, list) or not raw_slices:
        raise ChallengeValidationError("slices must be a non-empty list")

    if "max_workers" in manifest:
        max_workers = manifest["max_workers"]
        if (
            isinstance(max_workers, bool)
            or not isinstance(max_workers, int)
            or max_workers < 1
            or max_workers > MAX_WORKERS_CAP
        ):
            raise ChallengeValidationError(
                f"max_workers must be an integer between 1 and {MAX_WORKERS_CAP} (got {max_workers!r})"
            )
    else:
        max_workers = min(MAX_WORKERS_CAP, len(raw_slices))

    if "timeout_sec" in manifest:
        timeout_sec = _validate_positive_timeout(manifest["timeout_sec"], "timeout_sec")
    else:
        timeout_sec = 60.0

    normalized_slices: list[dict[str, Any]] = []
    seen_slice_ids: set[str] = set()
    all_scoped_paths: list[str] = []

    for index, raw_slice in enumerate(raw_slices):
        label = f"slices[{index}]"
        if not isinstance(raw_slice, dict):
            raise ChallengeValidationError(f"{label} must be an object")
        unknown_keys = set(raw_slice.keys()) - _ALLOWED_SLICE_KEYS
        if unknown_keys:
            raise ChallengeValidationError(
                f"{label} contains unsupported fields: {sorted(unknown_keys)}"
            )

        slice_id = _validate_nonempty_str(raw_slice.get("slice_id"), f"{label}.slice_id")
        if slice_id in seen_slice_ids:
            raise ChallengeValidationError(f"Duplicate slice_id in manifest: {slice_id!r}")
        seen_slice_ids.add(slice_id)

        raw_paths = raw_slice.get("paths")
        if not isinstance(raw_paths, list) or not raw_paths:
            raise ChallengeValidationError(f"{label} ({slice_id}): paths must be a non-empty list")
        cleaned_paths = [
            _validate_nonempty_str(item, f"{label}.paths[{p_idx}]")
            for p_idx, item in enumerate(raw_paths)
        ]
        try:
            slice_snapshot = capture_content(target_dir, paths=cleaned_paths)
        except ValueError as exc:
            raise ChallengeValidationError(f"{label} ({slice_id}): {exc}") from exc

        for rel in cleaned_paths:
            target_entry = target_dir / Path(rel)
            if not target_entry.exists():
                raise ChallengeValidationError(
                    f"{label} ({slice_id}): scoped path does not exist inside target_dir: {rel}"
                )

        raw_criteria = raw_slice.get("criteria_ids")
        if not isinstance(raw_criteria, list) or not raw_criteria:
            raise ChallengeValidationError(
                f"{label} ({slice_id}): criteria_ids must be a non-empty list"
            )
        criteria_ids: list[str] = []
        seen_criteria: set[str] = set()
        for c_idx, c_raw in enumerate(raw_criteria):
            cid = _validate_nonempty_str(c_raw, f"{label}.criteria_ids[{c_idx}]")
            if cid in seen_criteria:
                raise ChallengeValidationError(
                    f"{label} ({slice_id}): duplicate criterion_id {cid!r}"
                )
            seen_criteria.add(cid)
            criteria_ids.append(cid)

        has_prompt = "prompt" in raw_slice and raw_slice["prompt"] is not None
        has_focus = "focus" in raw_slice and raw_slice["focus"] is not None
        if not has_prompt and not has_focus:
            raise ChallengeValidationError(
                f"{label} ({slice_id}): must specify non-empty 'prompt' and/or 'focus'"
            )
        prompt_text = (
            _validate_nonempty_str(raw_slice["prompt"], f"{label}.prompt")
            if has_prompt
            else None
        )
        focus_text = (
            _validate_nonempty_str(raw_slice["focus"], f"{label}.focus")
            if has_focus
            else None
        )
        title_text = (
            _validate_nonempty_str(raw_slice["title"], f"{label}.title")
            if raw_slice.get("title") is not None
            else None
        )
        slice_timeout = (
            _validate_positive_timeout(raw_slice["timeout_sec"], f"{label}.timeout_sec")
            if raw_slice.get("timeout_sec") is not None
            else timeout_sec
        )

        canonical_paths = list(slice_snapshot["paths"] or cleaned_paths)
        all_scoped_paths.extend(canonical_paths)
        normalized_slices.append(
            {
                "slice_id": slice_id,
                "paths": canonical_paths,
                "criteria_ids": criteria_ids,
                "prompt": prompt_text,
                "focus": focus_text,
                "title": title_text,
                "timeout_sec": slice_timeout,
            }
        )

    bindings: dict[str, Any] = {}
    for key in _OPTIONAL_STR_BINDINGS:
        if key in manifest and manifest[key] is not None:
            bindings[key] = _validate_nonempty_str(manifest[key], key)
    for key in _OPTIONAL_INT_BINDINGS:
        if key in manifest and manifest[key] is not None:
            val = manifest[key]
            if isinstance(val, bool) or not isinstance(val, int) or val < 0:
                raise ChallengeValidationError(f"{key} must be a non-negative integer")
            bindings[key] = val

    return {
        "schema_version": SCHEMA_VERSION,
        "target_dir": target_dir,
        "slices": normalized_slices,
        "scoped_paths": sorted(set(all_scoped_paths)),
        "max_workers": max_workers,
        "timeout_sec": timeout_sec,
        "bindings": bindings,
    }


def validate_slice_output(raw_stdout: str) -> dict[str, Any]:
    """Strictly parse and validate one slice's JSON output against BUILTIN_CHALLENGE_SCHEMA."""
    if not isinstance(raw_stdout, str) or not raw_stdout.strip():
        raise ChallengeValidationError("Slice stdout is empty; expected JSON object")
    try:
        payload = json.loads(raw_stdout)
    except json.JSONDecodeError as exc:
        raise ChallengeValidationError(f"Malformed JSON output: {exc}") from exc

    if not isinstance(payload, dict):
        raise ChallengeValidationError("Slice output root must be a JSON object")

    missing = [k for k in _SLICE_OUTPUT_FIELDS if k not in payload]
    extra = sorted(set(payload.keys()) - set(_SLICE_OUTPUT_FIELDS))
    if missing:
        raise ChallengeValidationError(f"Slice output missing required fields: {missing}")
    if extra:
        raise ChallengeValidationError(f"Slice output contains unexpected fields: {extra}")

    summary = _validate_nonempty_str(payload["summary"], "summary")
    verdict = payload["verdict"]
    if not isinstance(verdict, str) or verdict not in ALLOWED_VERDICTS:
        raise ChallengeValidationError(
            f"verdict must be one of {list(ALLOWED_VERDICTS)} (got {verdict!r})"
        )

    raw_findings = payload["findings"]
    if not isinstance(raw_findings, list):
        raise ChallengeValidationError("findings must be a JSON array")

    validated_findings: list[dict[str, Any]] = []
    for idx, item in enumerate(raw_findings):
        prefix = f"findings[{idx}]"
        if not isinstance(item, dict):
            raise ChallengeValidationError(f"{prefix} must be a JSON object")
        f_missing = [k for k in _FINDING_FIELDS if k not in item]
        f_extra = sorted(set(item.keys()) - set(_FINDING_FIELDS))
        if f_missing:
            raise ChallengeValidationError(f"{prefix} missing required fields: {f_missing}")
        if f_extra:
            raise ChallengeValidationError(f"{prefix} contains unexpected fields: {f_extra}")

        severity = item["severity"]
        if not isinstance(severity, str) or severity not in ALLOWED_SEVERITIES:
            raise ChallengeValidationError(
                f"{prefix}.severity must be one of {list(ALLOWED_SEVERITIES)} (got {severity!r})"
            )
        category = _validate_nonempty_str(item["category"], f"{prefix}.category")
        file_ref = _validate_nonempty_str(item["file"], f"{prefix}.file")
        line = item["line"]
        if line is not None and (isinstance(line, bool) or not isinstance(line, int) or line < 1):
            raise ChallengeValidationError(
                f"{prefix}.line must be a positive integer or null (got {line!r})"
            )
        criterion_id = _validate_nonempty_str(item["criterion_id"], f"{prefix}.criterion_id")
        title = _validate_nonempty_str(item["title"], f"{prefix}.title")
        evidence = _validate_nonempty_str(item["evidence"], f"{prefix}.evidence")
        counterexample = item["counterexample"]
        if counterexample is not None and (
            not isinstance(counterexample, str) or "\0" in counterexample
        ):
            raise ChallengeValidationError(
                f"{prefix}.counterexample must be a string or null"
            )

        validated_findings.append(
            {
                "severity": severity,
                "category": category,
                "file": file_ref,
                "line": line,
                "criterion_id": criterion_id,
                "title": title,
                "evidence": evidence,
                "counterexample": counterexample,
            }
        )

    return {
        "findings": validated_findings,
        "summary": summary,
        "verdict": verdict,
    }


def discover_local_windows_codex_exe() -> str | None:
    """Discover Codex Desktop bundled ``codex.exe`` under ``%LOCALAPPDATA%\\OpenAI\\Codex\\bin``."""
    if os.name != "nt":
        return None
    local_appdata = os.environ.get("LOCALAPPDATA")
    if not local_appdata:
        return None
    bin_root = Path(local_appdata) / "OpenAI" / "Codex" / "bin"
    if not bin_root.is_dir():
        return None
    candidates = [p for p in bin_root.rglob("codex.exe") if p.is_file()]
    if not candidates:
        return None
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return str(candidates[0].resolve())


def resolve_codex_executable(codex_bin: str | Path | None = None) -> tuple[str, list[str], bool]:
    """Resolve ``codex_bin`` cleanly across Windows ``.exe``/``.cmd``/``.py`` and POSIX.

    When ``codex_bin`` and ``CODEX_BIN`` are unset on Windows, prefers the direct
    ``%LOCALAPPDATA%\\OpenAI\\Codex\\bin\\...\\codex.exe`` binary over ``codex.cmd`` wrappers
    to eliminate the ~1.8s PowerShell cold-start tax and ``cmd.exe`` quoting hazards.

    Returns ``(resolved_display_path, exec_prefix, is_windows_batch)``.
    """
    explicit_bin = str(codex_bin) if codex_bin is not None else os.environ.get("CODEX_BIN")
    if explicit_bin is None:
        direct_win_exe = discover_local_windows_codex_exe()
        if direct_win_exe:
            return direct_win_exe, [direct_win_exe], False
        raw = "codex"
    else:
        raw = _validate_nonempty_str(explicit_bin, "codex_bin")

    resolved_str: str | None = None
    candidate = Path(raw)
    has_sep = os.sep in raw or (os.altsep is not None and os.altsep in raw)

    if has_sep or candidate.is_file():
        if candidate.is_file():
            resolved_str = str(candidate.resolve())
        elif os.name == "nt" and not candidate.suffix:
            which_hit = shutil.which(str(candidate))
            if which_hit:
                resolved_str = str(Path(which_hit).resolve())
            else:
                for ext in (".exe", ".cmd", ".bat", ".py"):
                    with_ext = candidate.with_suffix(ext)
                    if with_ext.is_file():
                        resolved_str = str(with_ext.resolve())
                        break
    else:
        which_hit = shutil.which(raw)
        if which_hit:
            resolved_str = str(Path(which_hit).resolve())
        elif explicit_bin is None or raw.lower() in {"codex", "codex.exe"}:
            resolved_str = discover_local_windows_codex_exe()

    if not resolved_str:
        raise ChallengeValidationError(f"Codex executable not found: {raw!r}")

    suffix = Path(resolved_str).suffix.lower()
    if suffix == ".py":
        if os.name == "nt" or not os.access(resolved_str, os.X_OK):
            return resolved_str, [sys.executable, resolved_str], False
    is_windows_batch = os.name == "nt" and suffix in {".cmd", ".bat"}
    if is_windows_batch:
        comspec = os.environ.get("COMSPEC") or "cmd.exe"
        return resolved_str, [comspec, "/d", "/c", resolved_str], True
    return resolved_str, [resolved_str], False


def build_scoped_prompt(slice_spec: dict[str, Any], *, sanitize_for_cmd: bool = False) -> str:
    """Build a deterministic read-only counterexample challenge prompt for a slice."""
    parts = [
        "Perform a strict read-only counterexample challenge review.",
        "Do NOT modify, create, or delete any files.",
        f"Slice ID: {slice_spec['slice_id']}",
        f"Scoped Paths: {', '.join(slice_spec['paths'])}",
        f"Acceptance Criteria IDs: {', '.join(slice_spec['criteria_ids'])}",
    ]
    if slice_spec.get("title"):
        parts.append(f"Slice Title: {slice_spec['title']}")
    if slice_spec.get("focus"):
        parts.append(f"Focus: {slice_spec['focus']}")
    if slice_spec.get("prompt"):
        parts.append(f"Instructions: {slice_spec['prompt']}")
    parts.append(
        "Return ONLY valid JSON conforming to --output-schema with keys: findings, summary, verdict."
    )

    if sanitize_for_cmd:
        # Windows cmd.exe truncates arguments at CR/LF and breaks quoting on inner '"'.
        joined = " | ".join(parts)
        sanitized = (
            joined.replace("\r\n", " ")
            .replace("\n", " ")
            .replace("\r", " ")
            .replace('"', "'")
            .replace("%", "%%")
        )
        return sanitized
    return "\n".join(parts)


class _WindowsJobGuard:
    """Win32 Job Object + Toolhelp32 process-tree guard for async subprocesses."""

    def __init__(self) -> None:
        self._k = C.WinDLL("kernel32", use_last_error=True)
        self._setup_signatures()
        self.job: int | None = None
        self.root_pid: int | None = None
        self.root_create_time: int | None = None
        self.root_assigned: bool = False

    def _setup_signatures(self) -> None:
        k = self._k
        H, P, S = W.HANDLE, C.c_void_p, C.c_size_t

        class IO(C.Structure):
            _fields_ = [
                (n, C.c_ulonglong)
                for n in (
                    "read_ops",
                    "write_ops",
                    "other_ops",
                    "read_bytes",
                    "write_bytes",
                    "other_bytes",
                )
            ]

        class LIMIT(C.Structure):
            _fields_ = [
                ("process_time", C.c_longlong),
                ("job_time", C.c_longlong),
                ("flags", W.DWORD),
                ("min_ws", S),
                ("max_ws", S),
                ("active_limit", W.DWORD),
                ("affinity", S),
                ("priority", W.DWORD),
                ("scheduling", W.DWORD),
            ]

        class EXTENDED(C.Structure):
            _fields_ = [
                ("basic", LIMIT),
                ("io", IO),
                ("process_memory", S),
                ("job_memory", S),
                ("peak_process", S),
                ("peak_job", S),
            ]

        class ACCOUNT(C.Structure):
            _fields_ = [
                ("user", C.c_longlong),
                ("kernel", C.c_longlong),
                ("period_user", C.c_longlong),
                ("period_kernel", C.c_longlong),
                ("faults", W.DWORD),
                ("total", W.DWORD),
                ("active", W.DWORD),
                ("terminated", W.DWORD),
            ]

        class PROCESSENTRY32W(C.Structure):
            _fields_ = [
                ("dwSize", W.DWORD),
                ("cntUsage", W.DWORD),
                ("th32ProcessID", W.DWORD),
                ("th32DefaultHeapID", C.c_size_t),
                ("th32ModuleID", W.DWORD),
                ("cntThreads", W.DWORD),
                ("th32ParentProcessID", W.DWORD),
                ("pcPriClassBase", C.c_long),
                ("dwFlags", W.DWORD),
                ("szExeFile", W.WCHAR * 260),
            ]

        self._EXTENDED = EXTENDED
        self._ACCOUNT = ACCOUNT
        self._PROCESSENTRY32W = PROCESSENTRY32W

        k.CreateJobObjectW.argtypes, k.CreateJobObjectW.restype = [P, W.LPCWSTR], H
        k.SetInformationJobObject.argtypes, k.SetInformationJobObject.restype = (
            [H, C.c_int, P, W.DWORD],
            W.BOOL,
        )
        k.QueryInformationJobObject.argtypes, k.QueryInformationJobObject.restype = (
            [H, C.c_int, P, W.DWORD, P],
            W.BOOL,
        )
        k.AssignProcessToJobObject.argtypes, k.AssignProcessToJobObject.restype = [H, H], W.BOOL
        k.TerminateJobObject.argtypes, k.TerminateJobObject.restype = [H, W.UINT], W.BOOL
        k.OpenProcess.argtypes, k.OpenProcess.restype = [W.DWORD, W.BOOL, W.DWORD], H
        k.TerminateProcess.argtypes, k.TerminateProcess.restype = [H, W.UINT], W.BOOL
        k.GetProcessTimes.argtypes, k.GetProcessTimes.restype = (
            [
                H,
                C.POINTER(W.FILETIME),
                C.POINTER(W.FILETIME),
                C.POINTER(W.FILETIME),
                C.POINTER(W.FILETIME),
            ],
            W.BOOL,
        )
        k.CreateToolhelp32Snapshot.argtypes, k.CreateToolhelp32Snapshot.restype = (
            [W.DWORD, W.DWORD],
            H,
        )
        k.Process32FirstW.argtypes, k.Process32FirstW.restype = [H, P], W.BOOL
        k.Process32NextW.argtypes, k.Process32NextW.restype = [H, P], W.BOOL
        k.CloseHandle.argtypes, k.CloseHandle.restype = [H], W.BOOL

    def open_job(self) -> None:
        job = self._k.CreateJobObjectW(None, None)
        if not job:
            return
        self.job = job
        limits = self._EXTENDED()
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        self._k.SetInformationJobObject(job, 9, C.byref(limits), C.sizeof(limits))

    def _creation_time_for_handle(self, handle: int) -> int | None:
        created = W.FILETIME()
        exited = W.FILETIME()
        kernel = W.FILETIME()
        user = W.FILETIME()
        if not self._k.GetProcessTimes(
            handle, C.byref(created), C.byref(exited), C.byref(kernel), C.byref(user)
        ):
            return None
        return (int(created.dwHighDateTime) << 32) | int(created.dwLowDateTime)

    def _descendant_pids(self, root_pid: int, min_create_time: int | None) -> list[int]:
        snap = self._k.CreateToolhelp32Snapshot(0x00000002, 0)  # TH32CS_SNAPPROCESS
        invalid = C.c_void_p(-1).value
        if not snap or snap == invalid:
            return []
        parent_map: dict[int, list[int]] = {}
        try:
            entry = self._PROCESSENTRY32W()
            entry.dwSize = C.sizeof(entry)
            if self._k.Process32FirstW(snap, C.byref(entry)):
                while True:
                    pid = int(entry.th32ProcessID)
                    ppid = int(entry.th32ParentProcessID)
                    if pid != ppid:
                        parent_map.setdefault(ppid, []).append(pid)
                    if not self._k.Process32NextW(snap, C.byref(entry)):
                        break
        finally:
            self._k.CloseHandle(snap)

        ordered: list[int] = []
        queue = list(parent_map.get(root_pid, ()))
        visited = {root_pid}
        while queue:
            cid = queue.pop(0)
            if cid in visited:
                continue
            visited.add(cid)
            if min_create_time is not None:
                h = self._k.OpenProcess(0x1000, False, cid)  # PROCESS_QUERY_LIMITED_INFORMATION
                if h:
                    try:
                        ctime = self._creation_time_for_handle(h)
                    finally:
                        self._k.CloseHandle(h)
                    if ctime is not None and ctime < min_create_time:
                        continue
            ordered.append(cid)
            queue.extend(parent_map.get(cid, ()))
        return ordered

    def attach_tree(self, root_pid: int) -> None:
        self.root_pid = root_pid
        access = 0x0100 | 0x0001 | 0x1000  # SET_QUOTA | TERMINATE | QUERY_LIMITED_INFORMATION
        root_handle = self._k.OpenProcess(access, False, root_pid)
        if root_handle:
            try:
                self.root_create_time = self._creation_time_for_handle(root_handle)
                if self.job and self._k.AssignProcessToJobObject(self.job, root_handle):
                    self.root_assigned = True
            finally:
                self._k.CloseHandle(root_handle)

        if not self.job:
            return
        for cid in self._descendant_pids(root_pid, self.root_create_time):
            ch = self._k.OpenProcess(access, False, cid)
            if ch:
                try:
                    self._k.AssignProcessToJobObject(self.job, ch)
                finally:
                    self._k.CloseHandle(ch)

    def _job_active_count(self) -> int:
        if not self.job:
            return 0
        account = self._ACCOUNT()
        if not self._k.QueryInformationJobObject(
            self.job, 1, C.byref(account), C.sizeof(account), None
        ):
            return 0
        return int(account.active)

    def terminate_all(self, *, force_sweep: bool = False) -> None:
        if not force_sweep and self.job and self.root_assigned and self._job_active_count() == 0:
            return

        if self.root_pid is not None:
            descendants = self._descendant_pids(self.root_pid, self.root_create_time)
        else:
            descendants = []

        if self.job:
            self._k.TerminateJobObject(self.job, 1)

        # Fallback sweep for any process created in the microsecond race before job assignment.
        targets = list(reversed(descendants))
        if self.root_pid is not None:
            targets.append(self.root_pid)
        for pid in targets:
            h = self._k.OpenProcess(0x0001 | 0x1000, False, pid)  # TERMINATE | QUERY_LIMITED
            if h:
                try:
                    ctime = self._creation_time_for_handle(h)
                    if (
                        self.root_create_time is None
                        or ctime is None
                        or ctime >= self.root_create_time
                    ):
                        self._k.TerminateProcess(h, 1)
                finally:
                    self._k.CloseHandle(h)

        if self.job:
            deadline = time.monotonic() + 3.0
            while self._job_active_count() > 0 and time.monotonic() < deadline:
                time.sleep(0.01)

    def close(self) -> None:
        if self.job:
            self._k.CloseHandle(self.job)
            self.job = None


def _terminate_posix_group(pid: int) -> None:
    try:
        os.killpg(pid, signal.SIGKILL)
    except ProcessLookupError:
        return
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        try:
            os.killpg(pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.01)


async def _run_slice_subprocess(
    *,
    slice_spec: dict[str, Any],
    exec_prefix: list[str],
    is_windows_batch: bool,
    schema_path: Path,
    target_dir: Path,
    semaphore: asyncio.Semaphore,
    concurrency_tracker: dict[str, int],
) -> dict[str, Any]:
    del is_windows_batch  # Prompt is always piped cleanly via stdin ("-").
    slice_id = slice_spec["slice_id"]
    timeout_sec = slice_spec["timeout_sec"]
    scoped_prompt = build_scoped_prompt(slice_spec, sanitize_for_cmd=False)
    prompt_bytes = scoped_prompt.encode("utf-8")
    argv = [
        *exec_prefix,
        "exec",
        *CODEX_WORKER_ISOLATION_ARGS,
        "-s",
        "read-only",
        "--ephemeral",
        "--output-schema",
        str(schema_path),
        "-C",
        str(target_dir),
        "-",
    ]
    child_env = os.environ.copy()
    child_env.setdefault("PYTHONUTF8", "1")
    child_env.setdefault("PYTHONIOENCODING", "utf-8")

    async with semaphore:
        concurrency_tracker["active"] += 1
        if concurrency_tracker["active"] > concurrency_tracker["peak"]:
            concurrency_tracker["peak"] = concurrency_tracker["active"]

        started_at = _now_iso()
        t0 = time.monotonic()
        win_guard = _WindowsJobGuard() if os.name == "nt" else None
        proc: asyncio.subprocess.Process | None = None
        stdout_bytes = b""
        stderr_bytes = b""
        timed_out = False

        try:
            if win_guard is not None:
                win_guard.open_job()
                creationflags = int(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)) | int(
                    getattr(subprocess, "CREATE_NO_WINDOW", 0)
                )
                proc = await asyncio.create_subprocess_exec(
                    *argv,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env=child_env,
                    creationflags=creationflags,
                )
                win_guard.attach_tree(proc.pid)
            else:
                proc = await asyncio.create_subprocess_exec(
                    *argv,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env=child_env,
                    start_new_session=True,
                )

            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    proc.communicate(input=prompt_bytes), timeout=timeout_sec
                )
            except TimeoutError:
                timed_out = True
                if win_guard is not None:
                    await asyncio.to_thread(win_guard.terminate_all, force_sweep=True)
                else:
                    await asyncio.to_thread(_terminate_posix_group, proc.pid)
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
                try:
                    await asyncio.wait_for(proc.wait(), timeout=3.0)
                except TimeoutError:
                    pass
            except asyncio.CancelledError:
                if win_guard is not None:
                    await asyncio.to_thread(win_guard.terminate_all, force_sweep=True)
                else:
                    await asyncio.to_thread(_terminate_posix_group, proc.pid)
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
                raise
            else:
                # Ensure zero stray background grandchildren survive after root exit.
                if win_guard is not None:
                    await asyncio.to_thread(win_guard.terminate_all)
                else:
                    await asyncio.to_thread(_terminate_posix_group, proc.pid)
        except OSError as exc:
            duration_ms = int((time.monotonic() - t0) * 1000)
            return {
                "slice_id": slice_id,
                "paths": slice_spec["paths"],
                "criteria_ids": slice_spec["criteria_ids"],
                "focus": slice_spec["focus"],
                "prompt": slice_spec["prompt"],
                "status": "startup_failed",
                "exit_code": None,
                "started_at": started_at,
                "ended_at": _now_iso(),
                "duration_ms": duration_ms,
                "verdict": None,
                "summary": None,
                "findings": [],
                "error": f"Failed to launch codex subprocess: {exc}",
                "stderr_snippet": "",
            }
        finally:
            concurrency_tracker["active"] -= 1
            if proc is not None:
                transport = getattr(proc, "_transport", None)
                if transport is not None:
                    transport.close()
                await asyncio.sleep(0)
            if win_guard is not None:
                win_guard.close()

        ended_at = _now_iso()
        duration_ms = int((time.monotonic() - t0) * 1000)
        raw_stdout = stdout_bytes.decode("utf-8", errors="replace")
        raw_stderr = stderr_bytes.decode("utf-8", errors="replace").strip()[:2000]

        if timed_out:
            return {
                "slice_id": slice_id,
                "paths": slice_spec["paths"],
                "criteria_ids": slice_spec["criteria_ids"],
                "focus": slice_spec["focus"],
                "prompt": slice_spec["prompt"],
                "status": "timeout",
                "exit_code": proc.returncode if proc is not None else None,
                "started_at": started_at,
                "ended_at": ended_at,
                "duration_ms": duration_ms,
                "verdict": None,
                "summary": None,
                "findings": [],
                "error": f"Slice exceeded timeout_sec={timeout_sec}s; process tree terminated",
                "stderr_snippet": raw_stderr,
            }

        exit_code = proc.returncode
        if exit_code != 0:
            detail = raw_stderr or raw_stdout.strip()[:1000] or "no diagnostic output"
            return {
                "slice_id": slice_id,
                "paths": slice_spec["paths"],
                "criteria_ids": slice_spec["criteria_ids"],
                "focus": slice_spec["focus"],
                "prompt": slice_spec["prompt"],
                "status": "nonzero_exit",
                "exit_code": exit_code,
                "started_at": started_at,
                "ended_at": ended_at,
                "duration_ms": duration_ms,
                "verdict": None,
                "summary": None,
                "findings": [],
                "error": f"codex exec exited with code {exit_code}: {detail}",
                "stderr_snippet": raw_stderr,
            }

        try:
            parsed = validate_slice_output(raw_stdout)
        except ChallengeValidationError as exc:
            status_code = (
                "invalid_json" if str(exc).startswith("Malformed JSON") or "empty" in str(exc)
                else "schema_violation"
            )
            return {
                "slice_id": slice_id,
                "paths": slice_spec["paths"],
                "criteria_ids": slice_spec["criteria_ids"],
                "focus": slice_spec["focus"],
                "prompt": slice_spec["prompt"],
                "status": status_code,
                "exit_code": exit_code,
                "started_at": started_at,
                "ended_at": ended_at,
                "duration_ms": duration_ms,
                "verdict": None,
                "summary": None,
                "findings": [],
                "error": str(exc),
                "stderr_snippet": raw_stderr,
            }

        return {
            "slice_id": slice_id,
            "paths": slice_spec["paths"],
            "criteria_ids": slice_spec["criteria_ids"],
            "focus": slice_spec["focus"],
            "prompt": slice_spec["prompt"],
            "status": "ok",
            "exit_code": 0,
            "started_at": started_at,
            "ended_at": ended_at,
            "duration_ms": duration_ms,
            "verdict": parsed["verdict"],
            "summary": parsed["summary"],
            "findings": parsed["findings"],
            "error": None,
            "stderr_snippet": raw_stderr,
        }


async def run_challenge_async(
    raw_manifest: dict[str, Any],
    *,
    output_path: Path,
    digest_path: Path | None = None,
    codex_bin: str | None = None,
    schema_out: Path | None = None,
    manifest_path: Path | None = None,
) -> dict[str, Any]:
    """Validate manifest, run bounded read-only fan-out, verify tree integrity, and write report."""
    validated = validate_challenge_manifest(raw_manifest)
    target_dir: Path = validated["target_dir"]

    resolved_output = Path(output_path).resolve()
    resolved_digest = (
        Path(digest_path).resolve()
        if digest_path is not None
        else Path(f"{resolved_output}.sha256")
    )
    _validate_output_outside_target(resolved_output, target_dir, "output_path")
    _validate_output_outside_target(resolved_digest, target_dir, "digest_path")
    if schema_out is not None:
        _validate_output_outside_target(Path(schema_out).resolve(), target_dir, "schema_out")

    resolved_codex, exec_prefix, is_windows_batch = resolve_codex_executable(codex_bin)

    # Pre-scan snapshots BEFORE running any slice subprocesses.
    pre_tree_snapshot = capture_content(target_dir)
    pre_scoped_snapshot = capture_content(target_dir, paths=validated["scoped_paths"])
    pre_scan_digest = digest(pre_tree_snapshot)
    scoped_pre_digest = digest(pre_scoped_snapshot)

    started_at = _now_iso()
    concurrency_tracker = {"active": 0, "peak": 0}
    semaphore = asyncio.Semaphore(validated["max_workers"])

    with tempfile.TemporaryDirectory(prefix="tri-challenge-schema-") as temp_dir_str:
        schema_dir = Path(temp_dir_str)
        schema_path = write_builtin_schema(schema_dir)
        if schema_out is not None:
            resolved_schema_out = Path(schema_out).resolve()
            _atomic_write_bytes(
                resolved_schema_out, _canonical_json_bytes(BUILTIN_CHALLENGE_SCHEMA)
            )

        tasks = [
            _run_slice_subprocess(
                slice_spec=slice_spec,
                exec_prefix=exec_prefix,
                is_windows_batch=is_windows_batch,
                schema_path=schema_path,
                target_dir=target_dir,
                semaphore=semaphore,
                concurrency_tracker=concurrency_tracker,
            )
            for slice_spec in validated["slices"]
        ]
        slice_results = await asyncio.gather(*tasks)

    # Post-scan snapshots AFTER all slices have finished and exited.
    source_tree_mutated = False
    post_scan_digest: str | None = None
    scoped_post_digest: str | None = None
    source_mutation_detail: str | None = None

    try:
        post_tree_snapshot = recapture(pre_tree_snapshot)
        post_scoped_snapshot = recapture(pre_scoped_snapshot)
        post_scan_digest = digest(post_tree_snapshot)
        scoped_post_digest = digest(post_scoped_snapshot)
        if post_scan_digest != pre_scan_digest or scoped_post_digest != scoped_pre_digest:
            source_tree_mutated = True
            source_mutation_detail = (
                f"Working tree changed during challenge scan "
                f"(pre={pre_scan_digest[:12]}.. vs post={post_scan_digest[:12]}..)"
            )
    except (OSError, ValueError) as exc:
        source_tree_mutated = True
        source_mutation_detail = f"Post-scan source snapshot failed validation: {exc}"

    ended_at = _now_iso()
    slices_ok = sum(1 for item in slice_results if item["status"] == "ok")
    slices_failed = len(slice_results) - slices_ok

    consolidated_findings: list[dict[str, Any]] = []
    by_severity = {sev: 0 for sev in ALLOWED_SEVERITIES}
    for item in slice_results:
        for finding in item["findings"]:
            by_severity[finding["severity"]] += 1
            consolidated_findings.append({"slice_id": item["slice_id"], **finding})

    if source_tree_mutated:
        overall_status = "invalidated_dirty_tree"
        valid = False
        overall_verdict = "INVALIDATED"
        summary_text = source_mutation_detail or "Challenge invalidated due to dirty working tree"
    elif slices_failed > 0:
        overall_status = "failed"
        valid = False
        overall_verdict = "ERROR"
        failed_desc = ", ".join(
            f"{s['slice_id']}:{s['status']}" for s in slice_results if s["status"] != "ok"
        )
        summary_text = f"{slices_failed}/{len(slice_results)} slice(s) failed ({failed_desc})"
    else:
        overall_status = "completed"
        valid = True
        any_slice_fail = any(s["verdict"] == "FAIL" for s in slice_results)
        any_blocking_finding = any(
            f["severity"] in {"critical", "high", "medium", "low"}
            for f in consolidated_findings
        )
        overall_verdict = "FAIL" if (any_slice_fail or any_blocking_finding) else "PASS"
        summary_text = (
            f"Completed {len(slice_results)} slice(s): verdict={overall_verdict}, "
            f"findings={len(consolidated_findings)}"
        )

    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "evidence_level": EVIDENCE_LEVEL,
        "status": overall_status,
        "valid": valid,
        "verdict": overall_verdict,
        "summary": summary_text,
        "target_dir": str(target_dir),
        "manifest_path": str(manifest_path.resolve()) if manifest_path is not None else None,
        "manifest_digest": digest(raw_manifest),
        "report_path": str(resolved_output),
        "digest_path": str(resolved_digest),
        "builtin_schema_digest": digest(BUILTIN_CHALLENGE_SCHEMA),
        "codex_bin": resolved_codex,
        "max_workers": validated["max_workers"],
        "peak_concurrency": concurrency_tracker["peak"],
        "timeout_sec": validated["timeout_sec"],
        "started_at": started_at,
        "ended_at": ended_at,
        "source_tree_mutated": source_tree_mutated,
        "pre_scan_digest": pre_scan_digest,
        "post_scan_digest": post_scan_digest,
        "scoped_pre_digest": scoped_pre_digest,
        "scoped_post_digest": scoped_post_digest,
        "source_mutation_detail": source_mutation_detail,
        "bindings": validated["bindings"],
        "counts": {
            "slices_total": len(slice_results),
            "slices_ok": slices_ok,
            "slices_failed": slices_failed,
            "findings_total": len(consolidated_findings),
            "by_severity": by_severity,
        },
        "slices": slice_results,
        "findings": consolidated_findings,
    }

    report_bytes = _pretty_json_bytes(report)
    report_sha256 = hashlib.sha256(report_bytes).hexdigest()
    _atomic_write_bytes(resolved_output, report_bytes)
    _atomic_write_bytes(resolved_digest, report_sha256.encode("ascii"))
    return report


def run_challenge(
    manifest_path: Path,
    output_path: Path,
    *,
    digest_path: Path | None = None,
    codex_bin: str | None = None,
    schema_out: Path | None = None,
) -> dict[str, Any]:
    """Synchronous entry point loading ``manifest_path`` and writing consolidated report."""
    resolved_manifest = Path(manifest_path).resolve(strict=True)
    if not resolved_manifest.is_file():
        raise ChallengeValidationError(f"Manifest path is not a file: {resolved_manifest}")
    try:
        raw_manifest = json.loads(resolved_manifest.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ChallengeValidationError(
            f"Malformed challenge manifest JSON ({resolved_manifest}): {exc}"
        ) from exc

    return asyncio.run(
        run_challenge_async(
            raw_manifest,
            output_path=Path(output_path),
            digest_path=digest_path,
            codex_bin=codex_bin,
            schema_out=schema_out,
            manifest_path=resolved_manifest,
        )
    )


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tri-orchestrator-challenge",
        description=(
            "Host-side deterministic read-only Codex challenge fan-out CLI "
            "(bounded concurrency <= 4, source-tree integrity verified, SHA-256 evidenced)."
        ),
    )
    parser.add_argument(
        "manifest_pos",
        nargs="?",
        metavar="MANIFEST",
        help="Path to frozen challenge manifest JSON (positional alternative to --manifest).",
    )
    parser.add_argument(
        "-m",
        "--manifest",
        dest="manifest_opt",
        type=Path,
        help="Path to frozen challenge manifest JSON.",
    )
    parser.add_argument(
        "-o",
        "--output",
        dest="output",
        type=Path,
        required=True,
        help="Output path for consolidated JSON challenge report (outside target_dir).",
    )
    parser.add_argument(
        "--digest-out",
        dest="digest_out",
        type=Path,
        default=None,
        help="Optional companion 64-char SHA-256 file path (defaults to <output>.sha256).",
    )
    parser.add_argument(
        "--codex-bin",
        dest="codex_bin",
        default=None,
        help='Codex CLI executable path or name (defaults to CODEX_BIN env or "codex").',
    )
    parser.add_argument(
        "--schema-out",
        dest="schema_out",
        type=Path,
        default=None,
        help="Optional path to also export deterministic builtin_challenge_schema.json.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        code = exc.code
        if code is None or code == 0:
            return 0
        return int(code) if isinstance(code, int) else 2

    pos_path = Path(args.manifest_pos) if args.manifest_pos is not None else None
    opt_path: Path | None = args.manifest_opt
    if pos_path is not None and opt_path is not None:
        if pos_path.resolve(strict=False) != opt_path.resolve(strict=False):
            print(
                json.dumps(
                    {
                        "status": "validation_failed",
                        "error": "Conflicting positional MANIFEST and --manifest paths provided",
                    },
                    ensure_ascii=True,
                )
            )
            return 2
        manifest_path = opt_path
    elif opt_path is not None:
        manifest_path = opt_path
    elif pos_path is not None:
        manifest_path = pos_path
    else:
        print(
            json.dumps(
                {
                    "status": "validation_failed",
                    "error": "A challenge manifest path is required (--manifest <path> or positional)",
                },
                ensure_ascii=True,
            )
        )
        return 2

    try:
        result = run_challenge(
            manifest_path,
            args.output,
            digest_path=args.digest_out,
            codex_bin=args.codex_bin,
            schema_out=args.schema_out,
        )
    except (ChallengeValidationError, OSError, ValueError) as exc:
        print(
            json.dumps(
                {"status": "validation_failed", "error": str(exc)[:1000]},
                ensure_ascii=True,
            )
        )
        return 2

    digest_file = Path(result["digest_path"])
    report_sha256 = digest_file.read_text(encoding="ascii").strip()
    print(
        json.dumps(
            {
                "status": result["status"],
                "verdict": result["verdict"],
                "valid": result["valid"],
                "source_tree_mutated": result["source_tree_mutated"],
                "findings_total": result["counts"]["findings_total"],
                "peak_concurrency": result["peak_concurrency"],
                "report_path": result["report_path"],
                "digest_path": result["digest_path"],
                "report_sha256": report_sha256,
            },
            ensure_ascii=True,
        )
    )
    return 0 if result["status"] == "completed" and not result["source_tree_mutated"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
