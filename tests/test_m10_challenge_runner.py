"""M10 tests for host-side deterministic read-only challenge fan-out CLI."""

from __future__ import annotations

import ctypes as C
from ctypes import wintypes as W
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import textwrap
import time

import pytest

from orchestrator.artifacts import capture_evidence, digest
from orchestrator.challenge_runner import (
    BUILTIN_CHALLENGE_SCHEMA,
    CODEX_WORKER_ISOLATION_ARGS,
    ChallengeValidationError,
    main,
    resolve_codex_executable,
    run_challenge,
    validate_challenge_manifest,
    validate_slice_output,
    write_builtin_schema,
)


def _pid_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        k = C.WinDLL("kernel32", use_last_error=True)
        k.OpenProcess.argtypes = [W.DWORD, W.BOOL, W.DWORD]
        k.OpenProcess.restype = W.HANDLE
        k.GetExitCodeProcess.argtypes = [W.HANDLE, C.POINTER(W.DWORD)]
        k.GetExitCodeProcess.restype = W.BOOL
        k.CloseHandle.argtypes = [W.HANDLE]
        k.CloseHandle.restype = W.BOOL
        handle = k.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        try:
            code = W.DWORD()
            if not k.GetExitCodeProcess(handle, C.byref(code)):
                return False
            return int(code.value) == 259
        finally:
            k.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def _make_fake_codex(bin_dir: Path, python_body: str, *, base_name: str = "codex") -> Path:
    """Create a real platform executable wrapper (.cmd on Windows, +x script on POSIX)."""
    bin_dir.mkdir(parents=True, exist_ok=True)
    impl_py = bin_dir / f"_{base_name}_impl.py"
    impl_py.write_text(textwrap.dedent(python_body).lstrip(), encoding="utf-8")

    if os.name == "nt":
        wrapper = bin_dir / f"{base_name}.cmd"
        wrapper.write_text(
            f'@echo off\r\nchcp 65001 >nul\r\n"{sys.executable}" "{impl_py}" %*\r\nexit /b %errorlevel%\r\n',
            encoding="utf-8",
        )
        return wrapper

    wrapper = bin_dir / base_name
    wrapper.write_text(
        f'#!/bin/sh\nexec "{sys.executable}" "{impl_py}" "$@"\n',
        encoding="utf-8",
    )
    wrapper.chmod(wrapper.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return wrapper


def _make_sample_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "target_repo"
    (repo / "pkg").mkdir(parents=True)
    (repo / "pkg" / "auth.py").write_text("def verify_token(tok: str) -> bool:\n    return bool(tok)\n", encoding="utf-8")
    (repo / "pkg" / "billing.py").write_text("def charge(cents: int) -> int:\n    return cents\n", encoding="utf-8")
    (repo / "pkg" / "router.py").write_text("def route(path: str) -> str:\n    return path\n", encoding="utf-8")
    return repo


def test_cli_help_and_module_entrypoint(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(["--help"])
    captured = capsys.readouterr()
    assert rc == 0
    assert "tri-orchestrator-challenge" in captured.out
    assert "--manifest" in captured.out
    assert "--output" in captured.out
    assert "--codex-bin" in captured.out
    assert "--digest-out" in captured.out

    proc = subprocess.run(
        [sys.executable, "-m", "orchestrator.challenge_runner", "--help"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert proc.returncode == 0
    assert "tri-orchestrator-challenge" in proc.stdout
    assert proc.stderr == ""

    # Missing manifest returns validation error exit code != 0
    missing_rc = main(["--output", str(tmp_path / "out.json")])
    missing_out = json.loads(capsys.readouterr().out)
    assert missing_rc != 0
    assert missing_out["status"] == "validation_failed"


def test_manifest_validation_and_max_workers_cap(tmp_path: Path) -> None:
    repo = _make_sample_repo(tmp_path)

    def valid_base() -> dict:
        return {
            "target_dir": str(repo),
            "max_workers": 4,
            "timeout_sec": 10.0,
            "slices": [
                {
                    "slice_id": "s1",
                    "paths": ["pkg/auth.py"],
                    "criteria_ids": ["AC-1"],
                    "focus": "Token boundary validation",
                }
            ],
        }

    normalized = validate_challenge_manifest(valid_base())
    assert normalized["max_workers"] == 4
    assert normalized["target_dir"] == repo.resolve()

    # Enforce max_workers in 1..4 strictly
    for bad_workers in (0, -1, 5, 8, True, False, "4", 2.5, None):
        bad = valid_base()
        bad["max_workers"] = bad_workers
        with pytest.raises(ChallengeValidationError, match="max_workers"):
            validate_challenge_manifest(bad)

    # Reject invalid timeout_sec
    for bad_timeout in (0, -3.5, float("inf"), float("nan"), True, "10", None):
        bad = valid_base()
        bad["timeout_sec"] = bad_timeout
        with pytest.raises(ChallengeValidationError, match="timeout_sec"):
            validate_challenge_manifest(bad)

    # Reject missing/non-dir target_dir
    bad_dir = valid_base()
    bad_dir["target_dir"] = str(tmp_path / "does_not_exist")
    with pytest.raises(ChallengeValidationError, match="target_dir"):
        validate_challenge_manifest(bad_dir)

    file_target = tmp_path / "plain.txt"
    file_target.write_text("hi", encoding="utf-8")
    bad_file_dir = valid_base()
    bad_file_dir["target_dir"] = str(file_target)
    with pytest.raises(ChallengeValidationError, match="directory"):
        validate_challenge_manifest(bad_file_dir)

    # Reject empty slices, duplicate slice_id, missing prompt/focus, empty criteria
    empty_slices = valid_base()
    empty_slices["slices"] = []
    with pytest.raises(ChallengeValidationError, match="slices"):
        validate_challenge_manifest(empty_slices)

    dup_slices = valid_base()
    dup_slices["slices"] = [dup_slices["slices"][0], dict(dup_slices["slices"][0])]
    with pytest.raises(ChallengeValidationError, match="Duplicate slice_id"):
        validate_challenge_manifest(dup_slices)

    no_prompt = valid_base()
    del no_prompt["slices"][0]["focus"]
    with pytest.raises(ChallengeValidationError, match="prompt.*focus"):
        validate_challenge_manifest(no_prompt)

    empty_criteria = valid_base()
    empty_criteria["slices"][0]["criteria_ids"] = []
    with pytest.raises(ChallengeValidationError, match="criteria_ids"):
        validate_challenge_manifest(empty_criteria)

    # Reject path traversal, excluded env file, and missing file inside target_dir
    traversal = valid_base()
    traversal["slices"][0]["paths"] = ["../outside.py"]
    with pytest.raises(ChallengeValidationError, match="escapes project"):
        validate_challenge_manifest(traversal)

    (repo / ".env").write_text("SECRET=1\n", encoding="utf-8")
    env_scope = valid_base()
    env_scope["slices"][0]["paths"] = [".env"]
    with pytest.raises(ChallengeValidationError, match="excluded"):
        validate_challenge_manifest(env_scope)
    (repo / ".env").unlink()

    missing_rel = valid_base()
    missing_rel["slices"][0]["paths"] = ["pkg/nonexistent.py"]
    with pytest.raises(ChallengeValidationError, match="does not exist"):
        validate_challenge_manifest(missing_rel)

    # Reject symlink / junction escape inside target_dir via artifacts.capture_content
    outside_dir = tmp_path / "outside_secret"
    outside_dir.mkdir()
    (outside_dir / "secret.py").write_text("x = 1\n", encoding="utf-8")
    link_path = repo / "pkg" / "linked_dir"
    created_link = False
    try:
        os.symlink(outside_dir, link_path, target_is_directory=True)
        created_link = True
    except OSError:
        if os.name == "nt":
            res = subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(link_path), str(outside_dir)],
                capture_output=True,
                check=False,
            )
            created_link = res.returncode == 0
    if created_link:
        try:
            symlink_manifest = valid_base()
            symlink_manifest["slices"][0]["paths"] = ["pkg/linked_dir"]
            with pytest.raises(ChallengeValidationError, match="symbolic link|escapes"):
                validate_challenge_manifest(symlink_manifest)
        finally:
            if os.name == "nt" and link_path.exists() and not link_path.is_symlink():
                os.rmdir(link_path)
            else:
                link_path.unlink(missing_ok=True)


def test_valid_multi_slice_execution_cli_and_sha256_digest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _make_sample_repo(tmp_path)
    invocations_dir = tmp_path / "invocations"
    invocations_dir.mkdir()

    fake_script = f"""
import json, os, pathlib, re, sys

argv = sys.argv[1:]
assert argv[0] == "exec", f"unexpected subcommand: {{argv}}"
assert "--disable" in argv and "plugins" in argv and "hooks" in argv and "multi_agent" in argv
assert "mcp_servers={{}}" in argv
assert 'web_search="disabled"' in argv
assert "-s" in argv and argv[argv.index("-s") + 1] == "read-only"
assert "--ephemeral" in argv
schema_file = pathlib.Path(argv[argv.index("--output-schema") + 1])
assert schema_file.is_file()
schema_obj = json.loads(schema_file.read_text(encoding="utf-8"))
assert schema_obj["required"] == ["findings", "summary", "verdict"]
cd_dir = pathlib.Path(argv[argv.index("-C") + 1])
assert cd_dir.is_dir()
assert argv[-1] == "-", f"expected stdin prompt marker '-', got {{argv[-1]!r}}"
prompt = sys.stdin.read()

m = re.search(r"Slice ID:\\s*([A-Za-z0-9_-]+)", prompt)
slice_id = m.group(1) if m else "unknown"
rec = pathlib.Path({str(invocations_dir)!r}) / f"{{slice_id}}.json"
rec.write_text(json.dumps({{"argv": argv, "prompt": prompt}}, ensure_ascii=False), encoding="utf-8")

if slice_id == "slice_auth":
    out = {{
        "findings": [
            {{
                "severity": "high",
                "category": "authentication",
                "file": "pkg/auth.py",
                "line": 2,
                "criterion_id": "AC-AUTH-1",
                "title": "Whitespace-only token passes truthiness check",
                "evidence": "bool('   ') evaluates to True without signature decode",
                "counterexample": "verify_token('   ') -> True",
            }}
        ],
        "summary": "Found 1 authentication bypass counterexample in auth.py",
        "verdict": "FAIL",
    }}
else:
    out = {{
        "findings": [],
        "summary": "No counterexamples found in billing/router slice",
        "verdict": "PASS",
    }}
sys.stdout.write(json.dumps(out))
"""
    fake_bin = _make_fake_codex(tmp_path / "fake_bin", fake_script)

    manifest = {
        "schema_version": 1,
        "run_id": "run_m10_demo",
        "step_id": "step_review_1",
        "artifact_id": "art_cand_1",
        "packet_version": 1,
        "attempt": 1,
        "contract_revision": 1,
        "target_dir": str(repo),
        "max_workers": 2,
        "timeout_sec": 15,
        "slices": [
            {
                "slice_id": "slice_auth",
                "paths": ["pkg/auth.py"],
                "criteria_ids": ["AC-AUTH-1", "AC-AUTH-2"],
                "focus": 'Verify "quoted" & special | %TEMP%\nmultiline safety',
                "prompt": "Try whitespace and empty tokens.",
            },
            {
                "slice_id": "slice_billing",
                "paths": ["pkg/billing.py", "pkg/router.py"],
                "criteria_ids": ["AC-BILL-1"],
                "prompt": "Check negative integer handling.",
            },
        ],
    }
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    report_path = tmp_path / "out" / "report.json"
    schema_export = tmp_path / "out" / "exported_schema.json"

    # Exercise CODEX_BIN env fallback + positional manifest CLI invocation
    monkeypatch.setenv("CODEX_BIN", str(fake_bin))
    exit_code = main(
        [
            str(manifest_path),
            "--output",
            str(report_path),
            "--schema-out",
            str(schema_export),
        ]
    )
    cli_stdout = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert cli_stdout["status"] == "completed"
    assert cli_stdout["verdict"] == "FAIL"
    assert cli_stdout["valid"] is True
    assert cli_stdout["source_tree_mutated"] is False
    assert cli_stdout["findings_total"] == 1

    default_digest_path = Path(f"{report_path}.sha256")
    assert report_path.is_file()
    assert default_digest_path.is_file()
    assert schema_export.is_file()
    assert json.loads(schema_export.read_text(encoding="utf-8")) == BUILTIN_CHALLENGE_SCHEMA

    raw_bytes = report_path.read_bytes()
    expected_sha = hashlib.sha256(raw_bytes).hexdigest()
    recorded_sha = default_digest_path.read_text(encoding="ascii")
    assert len(recorded_sha) == 64
    assert re.fullmatch(r"[0-9a-f]{64}", recorded_sha)
    assert recorded_sha == expected_sha
    assert cli_stdout["report_sha256"] == expected_sha

    report = json.loads(raw_bytes.decode("utf-8"))
    assert report["status"] == "completed"
    assert report["valid"] is True
    assert report["verdict"] == "FAIL"
    assert report["source_tree_mutated"] is False
    assert report["pre_scan_digest"] == report["post_scan_digest"]
    assert report["scoped_pre_digest"] == report["scoped_post_digest"]
    assert report["bindings"]["run_id"] == "run_m10_demo"
    assert report["counts"]["slices_total"] == 2
    assert report["counts"]["slices_ok"] == 2
    assert report["counts"]["findings_total"] == 1
    assert report["counts"]["by_severity"]["high"] == 1
    assert report["findings"][0]["slice_id"] == "slice_auth"
    assert report["findings"][0]["criterion_id"] == "AC-AUTH-1"

    # Verify prompt survived Windows .cmd quoting without command injection or truncation
    auth_inv = json.loads((invocations_dir / "slice_auth.json").read_text(encoding="utf-8"))
    assert "Try whitespace and empty tokens." in auth_inv["prompt"]
    assert "multiline safety" in auth_inv["prompt"]

    # Ready for artifact_capture / workflow_review evidence registration
    evidence_records = capture_evidence([str(report_path), str(default_digest_path)])
    assert evidence_records[0]["sha256"] == expected_sha

    # Also verify custom --digest-out and --manifest flag with max_workers=5 CLI rejection
    bad_manifest = dict(manifest, max_workers=5)
    bad_manifest_path = tmp_path / "bad_manifest.json"
    bad_manifest_path.write_text(json.dumps(bad_manifest), encoding="utf-8")
    bad_rc = main(
        [
            "--manifest",
            str(bad_manifest_path),
            "--output",
            str(tmp_path / "out" / "bad_report.json"),
            "--digest-out",
            str(tmp_path / "out" / "bad.sha"),
        ]
    )
    bad_summary = json.loads(capsys.readouterr().out)
    assert bad_rc != 0
    assert bad_summary["status"] == "validation_failed"
    assert "max_workers" in bad_summary["error"]


def test_concurrency_cap_enforced_at_runtime(tmp_path: Path) -> None:
    repo = _make_sample_repo(tmp_path)
    events_dir = tmp_path / "concurrency_events"
    events_dir.mkdir()

    fake_script = f"""
import json, os, pathlib, re, sys, time, uuid

prompt = sys.stdin.read() if sys.argv[-1] == "-" else sys.argv[-1]
m = re.search(r"Slice ID:\\s*([A-Za-z0-9_-]+)", prompt)
sid = m.group(1) if m else uuid.uuid4().hex
edir = pathlib.Path({str(events_dir)!r})
t_enter = time.monotonic()
time.sleep(0.25)
t_exit = time.monotonic()
(edir / f"{{sid}}.json").write_text(
    json.dumps({{"enter": t_enter, "exit": t_exit}}), encoding="utf-8"
)
sys.stdout.write(json.dumps({{"findings": [], "summary": "ok", "verdict": "PASS"}}))
"""
    fake_bin = _make_fake_codex(tmp_path / "conc_bin", fake_script)

    slices = [
        {
            "slice_id": f"s_{i}",
            "paths": ["pkg/auth.py"],
            "criteria_ids": ["AC-1"],
            "focus": f"Slice {i}",
        }
        for i in range(6)
    ]
    manifest = {
        "target_dir": str(repo),
        "max_workers": 2,
        "timeout_sec": 10.0,
        "slices": slices,
    }
    manifest_path = tmp_path / "conc_manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    report_path = tmp_path / "conc_out" / "report.json"

    report = run_challenge(manifest_path, report_path, codex_bin=str(fake_bin))
    assert report["status"] == "completed"
    assert report["verdict"] == "PASS"
    assert 1 <= report["peak_concurrency"] <= 2
    assert json.loads(report_path.read_text(encoding="utf-8")) == report

    # Compute external overlap concurrency from recorded intervals (with 10ms interior margin)
    intervals = [
        json.loads(p.read_text(encoding="utf-8")) for p in sorted(events_dir.glob("*.json"))
    ]
    assert len(intervals) == 6
    sample_points = [iv["enter"] + 0.05 for iv in intervals]
    max_external_overlap = max(
        sum(1 for iv in intervals if iv["enter"] <= pt <= iv["exit"])
        for pt in sample_points
    )
    assert max_external_overlap <= 2


def test_per_slice_timeout_terminates_grandchild_process_tree(tmp_path: Path) -> None:
    repo = _make_sample_repo(tmp_path)
    grandchild_pid_file = tmp_path / "grandchild.pid"
    great_grandchild_pid_file = tmp_path / "great_grandchild.pid"

    fake_script = f"""
import pathlib, subprocess, sys, time

ggc_code = (
    "import pathlib, os, time; "
    f"pathlib.Path({str(great_grandchild_pid_file)!r}).write_text(str(os.getpid()), encoding='utf-8'); "
    "time.sleep(60)"
)
# Grandchild spawns a great-grandchild and both inherit stdout/stderr pipes!
proc = subprocess.Popen([sys.executable, "-c", ggc_code])
pathlib.Path({str(grandchild_pid_file)!r}).write_text(str(proc.pid), encoding="utf-8")
time.sleep(60)
"""
    fake_bin = _make_fake_codex(tmp_path / "timeout_bin", fake_script)
    manifest = {
        "target_dir": str(repo),
        "max_workers": 1,
        "timeout_sec": 1.2,
        "slices": [
            {
                "slice_id": "hang_slice",
                "paths": ["pkg/auth.py"],
                "criteria_ids": ["AC-TIMEOUT"],
                "prompt": "Simulate hanging grandchild tree holding stdio pipes",
            }
        ],
    }
    manifest_path = tmp_path / "timeout_manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    report_path = tmp_path / "timeout_out" / "report.json"
    custom_digest = tmp_path / "timeout_out" / "custom.sha256"

    t0 = time.monotonic()
    rc = main(
        [
            "--manifest",
            str(manifest_path),
            "--output",
            str(report_path),
            "--digest-out",
            str(custom_digest),
            "--codex-bin",
            str(fake_bin),
        ]
    )
    elapsed = time.monotonic() - t0

    assert rc == 1
    assert elapsed < 10.0, f"Timeout cleanup hung for {elapsed:.2f}s"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["status"] == "failed"
    assert report["valid"] is False
    assert report["slices"][0]["status"] == "timeout"
    assert custom_digest.read_text(encoding="ascii") == hashlib.sha256(
        report_path.read_bytes()
    ).hexdigest()

    assert grandchild_pid_file.is_file(), "Grandchild process did not start before timeout"
    grandchild_pid = int(grandchild_pid_file.read_text(encoding="utf-8").strip())
    pids_to_check = [grandchild_pid]
    if great_grandchild_pid_file.is_file():
        pids_to_check.append(int(great_grandchild_pid_file.read_text(encoding="utf-8").strip()))

    deadline = time.monotonic() + 2.0
    while any(_pid_is_alive(p) for p in pids_to_check) and time.monotonic() < deadline:
        time.sleep(0.05)
    for pid in pids_to_check:
        assert not _pid_is_alive(pid), f"Descendant PID {pid} leaked alive!"


def test_nonzero_exit_malformed_json_and_schema_violation_rejected(tmp_path: Path) -> None:
    repo = _make_sample_repo(tmp_path)

    fake_script = """
import json, re, sys

prompt = sys.stdin.read() if sys.argv[-1] == "-" else sys.argv[-1]
m = re.search(r"Slice ID:\\s*([A-Za-z0-9_-]+)", prompt)
sid = m.group(1) if m else ""

if sid == "s_exit":
    sys.stderr.write("sandbox backend error: boom\\n")
    raise SystemExit(7)
elif sid == "s_bad_json":
    sys.stdout.write("{not valid json")
elif sid == "s_bad_schema":
    # Missing required 'counterexample' and invalid severity
    sys.stdout.write(json.dumps({
        "findings": [{
            "severity": "extreme",
            "category": "bug",
            "file": "pkg/auth.py",
            "line": 1,
            "criterion_id": "AC-1",
            "title": "Bad finding",
            "evidence": "line 1"
        }],
        "summary": "bad schema output",
        "verdict": "FAIL"
    }))
else:
    sys.stdout.write(json.dumps({"findings": [], "summary": "clean", "verdict": "PASS"}))
"""
    fake_bin = _make_fake_codex(tmp_path / "err_bin", fake_script)
    manifest = {
        "target_dir": str(repo),
        "max_workers": 3,
        "timeout_sec": 10.0,
        "slices": [
            {
                "slice_id": "s_exit",
                "paths": ["pkg/auth.py"],
                "criteria_ids": ["AC-1"],
                "focus": "nonzero exit case",
            },
            {
                "slice_id": "s_bad_json",
                "paths": ["pkg/billing.py"],
                "criteria_ids": ["AC-2"],
                "focus": "malformed json case",
            },
            {
                "slice_id": "s_bad_schema",
                "paths": ["pkg/router.py"],
                "criteria_ids": ["AC-3"],
                "focus": "schema violation case",
            },
            {
                "slice_id": "s_ok",
                "paths": ["pkg/auth.py"],
                "criteria_ids": ["AC-4"],
                "focus": "ok case",
            },
        ],
    }
    manifest_path = tmp_path / "err_manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    report_path = tmp_path / "err_out" / "report.json"

    rc = main(
        [
            "--manifest",
            str(manifest_path),
            "--output",
            str(report_path),
            "--codex-bin",
            str(fake_bin),
        ]
    )
    assert rc == 1
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["status"] == "failed"
    assert report["valid"] is False
    assert report["counts"]["slices_ok"] == 1
    assert report["counts"]["slices_failed"] == 3

    by_id = {s["slice_id"]: s for s in report["slices"]}
    assert by_id["s_exit"]["status"] == "nonzero_exit"
    assert by_id["s_exit"]["exit_code"] == 7
    assert "boom" in by_id["s_exit"]["error"]

    assert by_id["s_bad_json"]["status"] == "invalid_json"
    assert "Malformed JSON" in by_id["s_bad_json"]["error"]

    assert by_id["s_bad_schema"]["status"] == "schema_violation"
    assert "missing required fields" in by_id["s_bad_schema"]["error"]

    assert by_id["s_ok"]["status"] == "ok"

    # Unit-level edge checks on validate_slice_output (bool line, extra keys)
    with pytest.raises(ChallengeValidationError, match="unexpected fields"):
        validate_slice_output(
            json.dumps({"findings": [], "summary": "ok", "verdict": "PASS", "extra": 1})
        )
    with pytest.raises(ChallengeValidationError, match="positive integer or null"):
        validate_slice_output(
            json.dumps(
                {
                    "findings": [
                        {
                            "severity": "low",
                            "category": "style",
                            "file": "a.py",
                            "line": True,
                            "criterion_id": "C1",
                            "title": "t",
                            "evidence": "e",
                            "counterexample": None,
                        }
                    ],
                    "summary": "ok",
                    "verdict": "PASS",
                }
            )
        )


def test_mid_scan_source_tree_mutation_invalidates_report(tmp_path: Path) -> None:
    repo = _make_sample_repo(tmp_path)

    # Simulate external/rogue modification to target_dir while scan is in flight
    fake_script = """
import json, pathlib, sys

cd_dir = pathlib.Path(sys.argv[sys.argv.index("-C") + 1])
target_file = cd_dir / "pkg" / "billing.py"
target_file.write_text("# mutated mid-scan!\\n", encoding="utf-8")
sys.stdout.write(json.dumps({"findings": [], "summary": "looked clean", "verdict": "PASS"}))
"""
    fake_bin = _make_fake_codex(tmp_path / "dirty_bin", fake_script)
    manifest = {
        "target_dir": str(repo),
        "max_workers": 1,
        "timeout_sec": 10.0,
        "slices": [
            {
                "slice_id": "s_clean_scope",
                "paths": ["pkg/auth.py"],
                "criteria_ids": ["AC-1"],
                "focus": "Even mutation outside slice scope inside target_dir must invalidate",
            }
        ],
    }
    manifest_path = tmp_path / "dirty_manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    report_path = tmp_path / "dirty_out" / "report.json"

    rc = main(
        [
            "--manifest",
            str(manifest_path),
            "--output",
            str(report_path),
            "--codex-bin",
            str(fake_bin),
        ]
    )
    assert rc == 1

    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["source_tree_mutated"] is True
    assert report["status"] == "invalidated_dirty_tree"
    assert report["valid"] is False
    assert report["verdict"] == "INVALIDATED"
    assert report["pre_scan_digest"] != report["post_scan_digest"]
    digest_file = Path(f"{report_path}.sha256")
    assert digest_file.read_text(encoding="ascii") == hashlib.sha256(
        report_path.read_bytes()
    ).hexdigest()


def test_output_inside_target_dir_rejected_and_deterministic_schema(tmp_path: Path) -> None:
    repo = _make_sample_repo(tmp_path)
    s1 = write_builtin_schema(tmp_path / "d1")
    s2 = write_builtin_schema(tmp_path / "d2")
    assert s1.read_bytes() == s2.read_bytes()
    assert digest(json.loads(s1.read_text(encoding="utf-8"))) == digest(BUILTIN_CHALLENGE_SCHEMA)

    manifest = {
        "target_dir": str(repo),
        "max_workers": 1,
        "timeout_sec": 5.0,
        "slices": [
            {
                "slice_id": "s1",
                "paths": ["pkg/auth.py"],
                "criteria_ids": ["AC-1"],
                "focus": "check overlap guard",
            }
        ],
    }
    manifest_path = tmp_path / "overlap_manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    # Attempting to write report inside target_dir is rejected before execution
    rc = main(
        [
            "--manifest",
            str(manifest_path),
            "--output",
            str(repo / "report.json"),
        ]
    )
    assert rc != 0
    assert not (repo / "report.json").exists()


def test_path_resolution_via_which_subprocess_cli_and_no_daemon_coupling(
    tmp_path: Path,
) -> None:
    repo = _make_sample_repo(tmp_path)
    fake_script = """
import json, sys
sys.stdout.write(json.dumps({"findings": [], "summary": "all clear", "verdict": "PASS"}))
"""
    bin_dir = tmp_path / "path_bin"
    _make_fake_codex(bin_dir, fake_script, base_name="mock-codex-cli")

    manifest = {
        "target_dir": str(repo),
        "max_workers": 4,
        "timeout_sec": 10.0,
        "slices": [
            {
                "slice_id": "s_path",
                "paths": ["pkg"],
                "criteria_ids": ["AC-DIR-1"],
                "focus": "Directory slice resolution via PATH shutil.which",
            }
        ],
    }
    manifest_path = tmp_path / "path_manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    report_path = tmp_path / "path_out" / "report.json"

    env = dict(os.environ)
    env["PATH"] = str(bin_dir) + os.pathsep + env.get("PATH", "")
    env.pop("CODEX_BIN", None)

    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "orchestrator.challenge_runner",
            "--manifest",
            str(manifest_path),
            "--output",
            str(report_path),
            "--codex-bin",
            "mock-codex-cli",
        ],
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert proc.returncode == 0, f"stderr={proc.stderr} stdout={proc.stdout}"
    assert proc.stderr == ""
    summary = json.loads(proc.stdout)
    assert summary["status"] == "completed"
    assert summary["verdict"] == "PASS"
    assert summary["valid"] is True

    # Red-line check: challenge_runner is strictly host-side and never imports daemon/rpc_handlers
    import inspect
    import orchestrator.challenge_runner as cr_mod

    src = inspect.getsource(cr_mod)
    assert "orchestrator.daemon" not in src
    assert "orchestrator.rpc_handlers" not in src
    assert "httpx" not in src
    assert "--disable" in CODEX_WORKER_ISOLATION_ARGS


@pytest.mark.skipif(os.name != "nt", reason="Windows Codex Desktop binary resolution")
def test_resolve_codex_executable_prefers_localappdata_exe_and_wraps_batch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_localappdata = tmp_path / "LocalAppData"
    bundled_exe = fake_localappdata / "OpenAI" / "Codex" / "bin" / "abc123" / "codex.exe"
    bundled_exe.parent.mkdir(parents=True)
    bundled_exe.write_bytes(b"MZ")

    fake_path_dir = tmp_path / "choco_bin"
    fake_path_dir.mkdir()
    choco_cmd = fake_path_dir / "codex.cmd"
    choco_cmd.write_text("@echo off\r\necho slow wrapper\r\n", encoding="utf-8")

    monkeypatch.delenv("CODEX_BIN", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(fake_localappdata))
    monkeypatch.setenv("PATH", str(fake_path_dir) + os.pathsep + os.environ.get("PATH", ""))

    resolved_str, exec_prefix, is_batch = resolve_codex_executable(None)
    assert Path(resolved_str) == bundled_exe.resolve()
    assert exec_prefix == [str(bundled_exe.resolve())]
    assert is_batch is False

    # When a .cmd wrapper is explicitly passed, wrap via [comspec, "/d", "/c", ...]
    cmd_str, cmd_prefix, cmd_is_batch = resolve_codex_executable(str(choco_cmd))
    assert Path(cmd_str) == choco_cmd.resolve()
    assert cmd_is_batch is True
    assert cmd_prefix[1:3] == ["/d", "/c"]
    assert Path(cmd_prefix[3]) == choco_cmd.resolve()


