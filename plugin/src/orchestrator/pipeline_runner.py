"""Deterministic host-side auto-closed-loop DAG runner CLI (``tri-orchestrator-pipeline``, M11).

Drives persistent ``codex app-server --listen stdio://`` author and blind-verifier
threads across isolated workspaces while preserving 100% of ``18790`` daemon SQLite
workflow gates, ``tri-orchestrator-verify`` command receipts, ``<= 3`` rework budget,
and ``<= 1.5s`` Web pause/stop ``turn/interrupt`` responsiveness.
"""

from __future__ import annotations

import argparse
import asyncio
from collections import deque
from collections.abc import Mapping, Sequence
import copy
from datetime import datetime, timezone
import hashlib
import json
import locale
import math
import os
from pathlib import Path
import re
import sys
import time
from typing import Any
from urllib.parse import urlparse
import uuid

import httpx

from orchestrator import __version__
from orchestrator.appserver_client import (
    AppServerError,
    AppServerRpcError,
    AppServerStdioClient,
    format_appserver_agent_id,
    normalize_thread_id,
)
from orchestrator.config import Settings, get_settings
from orchestrator.mcp_shim import ensure_daemon_running
from orchestrator import verify_runner
from orchestrator.workflow import ENV_PROFILES, MAX_REWORKS
from orchestrator.workspace_mgr import (
    WorkspaceManager,
    _append_git_exclude_entries,
    _run_git,
)

SCHEMA_VERSION = 1
RUNNER_NAME = "tri-orchestrator-pipeline"
EXECUTION_KIND = "app_server_pipeline"
MAX_WORKERS_CAP = 4
DEFAULT_POLL_INTERVAL_SEC = 0.4
MAX_POLL_INTERVAL_SEC = 1.5
ALLOWED_MODES = frozenset({"boost", "teamwork", "swarm"})
ALLOWED_COMPLEXITIES = frozenset({"normal", "high"})
ALLOWED_STEP_KINDS = frozenset({"task", "milestone"})
ALLOWED_SPEC_DEP_MODES = frozenset(
    {
        "auto",
        "none",
        "junction",
        "uv_sync",
        "full_copy",
        "link_ro_cache",
        "fresh_venv_uv",
        "copy",
    }
)
TURN_TRANSIENT_MAX_RETRIES = 2
_SAFE_STEP_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


class PipelineError(RuntimeError):
    """Base error for pipeline specification, RPC, or execution failures."""


class PipelineSpecError(ValueError):
    """Raised when ``pipeline_spec.json`` or CLI flags fail schema validation."""


class PipelineRpcError(PipelineError):
    """Raised when a daemon ``/api/rpc`` call returns an error response."""

    def __init__(self, tool: str, detail: str, *, status_code: int = 400) -> None:
        self.tool = tool
        self.detail = detail
        self.status_code = status_code
        super().__init__(f"Daemon RPC {tool} failed (HTTP {status_code}): {detail}")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical_json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")


def _pretty_json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def _atomic_write_json(path: Path, payload: Mapping[str, Any]) -> str:
    """Atomically write ``payload`` with embedded ``sha256`` over canonical payload fields."""
    path.parent.mkdir(parents=True, exist_ok=True)
    base_body = {k: v for k, v in payload.items() if k != "sha256"}
    body_sha = hashlib.sha256(_canonical_json_bytes(base_body)).hexdigest()
    final_payload = {**base_body, "sha256": body_sha}
    raw_bytes = _pretty_json_bytes(final_payload)
    tmp_path = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with tmp_path.open("wb") as fh:
            fh.write(raw_bytes)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_path, path)
    except BaseException:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    return body_sha


def _default_env_profile() -> str:
    return "windows_minimal_py" if os.name == "nt" else "posix_minimal_py"


def _validate_nonempty_str(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or "\0" in value:
        raise PipelineSpecError(f"{label} must be a non-empty string without NUL")
    return value.strip()


def _validate_rel_ownership_paths(raw_paths: Any, label: str) -> list[str]:
    if not isinstance(raw_paths, list) or not raw_paths:
        raise PipelineSpecError(f"{label} must be a non-empty list of relative paths")
    cleaned: list[str] = []
    seen: set[str] = set()
    for idx, raw in enumerate(raw_paths):
        p_str = _validate_nonempty_str(raw, f"{label}[{idx}]")
        p_obj = Path(p_str)
        if p_obj.is_absolute() or ".." in p_obj.parts:
            raise PipelineSpecError(f"{label}[{idx}] must be a relative path inside project_root: {p_str!r}")
        norm = p_obj.as_posix()
        if norm not in seen:
            seen.add(norm)
            cleaned.append(norm)
    return cleaned


def _validate_command_list(
    raw_commands: Any,
    *,
    label: str,
    step_criteria: Sequence[str] | None = None,
    project_root: Path | None = None,
) -> list[dict[str, Any]]:
    if not isinstance(raw_commands, list) or not raw_commands:
        raise PipelineSpecError(f"{label} must be a non-empty list of command specs")
    seen_ids: set[str] = set()
    normalized: list[dict[str, Any]] = []
    covered_criteria: set[str] = set()

    for idx, raw_cmd in enumerate(raw_commands):
        cmd_label = f"{label}[{idx}]"
        if not isinstance(raw_cmd, Mapping):
            raise PipelineSpecError(f"{cmd_label} must be a JSON object")
        cmd_id = _validate_nonempty_str(raw_cmd.get("id"), f"{cmd_label}.id")
        if cmd_id in seen_ids:
            raise PipelineSpecError(f"Duplicate command id in {label}: {cmd_id!r}")
        seen_ids.add(cmd_id)

        raw_argv = raw_cmd.get("argv")
        if (
            not isinstance(raw_argv, list)
            or not raw_argv
            or any(not isinstance(a, str) or not a or "\0" in a for a in raw_argv)
        ):
            raise PipelineSpecError(f"{cmd_label}.argv must be a non-empty string list")

        raw_cwd = raw_cmd.get("cwd", ".")
        cwd_str = _validate_nonempty_str(raw_cwd, f"{cmd_label}.cwd")
        cwd_path = Path(cwd_str)
        if cwd_path.is_absolute():
            if project_root is not None:
                norm_cwd = cwd_path.resolve(strict=False)
                norm_proj = project_root.resolve(strict=False)
                if not (norm_cwd == norm_proj or norm_cwd.is_relative_to(norm_proj)):
                    raise PipelineSpecError(
                        f"{cmd_label}.cwd must stay inside project_root ({norm_proj}): {cwd_str!r}"
                    )
        else:
            if ".." in cwd_path.parts:
                raise PipelineSpecError(
                    f"{cmd_label}.cwd must not contain '..' path traversal: {cwd_str!r}"
                )

        raw_timeout = raw_cmd.get("timeout_sec", 60.0)
        if (
            isinstance(raw_timeout, bool)
            or not isinstance(raw_timeout, (int, float))
            or not math.isfinite(raw_timeout)
            or raw_timeout <= 0
        ):
            raise PipelineSpecError(f"{cmd_label}.timeout_sec must be a positive number")

        raw_env_keys = raw_cmd.get("env_keys", [])
        if (
            not isinstance(raw_env_keys, list)
            or any(not isinstance(k, str) or not k.strip() or "=" in k or "\0" in k for k in raw_env_keys)
        ):
            raise PipelineSpecError(f"{cmd_label}.env_keys must be a list of valid environment variable names")
        dedup_env_keys = list(dict.fromkeys(k.strip() for k in raw_env_keys))

        raw_crit = raw_cmd.get("criteria")
        if not isinstance(raw_crit, list) or not raw_crit:
            raise PipelineSpecError(f"{cmd_label}.criteria must be a non-empty list of strings")
        cmd_criteria: list[str] = []
        seen_c: set[str] = set()
        for c_idx, c_item in enumerate(raw_crit):
            c_text = _validate_nonempty_str(c_item, f"{cmd_label}.criteria[{c_idx}]")
            if c_text not in seen_c:
                seen_c.add(c_text)
                cmd_criteria.append(c_text)
        covered_criteria.update(cmd_criteria)

        normalized.append(
            {
                "id": cmd_id,
                "argv": list(raw_argv),
                "cwd": cwd_str,
                "timeout_sec": float(raw_timeout),
                "env_keys": dedup_env_keys,
                "criteria": cmd_criteria,
            }
        )

    if step_criteria is not None:
        expected_set = set(step_criteria)
        if covered_criteria != expected_set:
            missing = sorted(expected_set - covered_criteria)
            extra = sorted(covered_criteria - expected_set)
            raise PipelineSpecError(
                f"{label} criteria coverage mismatch (missing={missing}, extra={extra})"
            )
    return normalized


def _topological_sort_steps(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_key = {s["step_key"]: s for s in steps}
    in_degree: dict[str, int] = {s["step_key"]: 0 for s in steps}
    dependents: dict[str, list[str]] = {s["step_key"]: [] for s in steps}

    for step in steps:
        key = step["step_key"]
        for dep in step["dependencies"]:
            if dep not in by_key:
                raise PipelineSpecError(
                    f"Step {key!r} depends on unknown step_key {dep!r}"
                )
            if dep == key:
                raise PipelineSpecError(f"Step {key!r} cannot depend on itself")
            in_degree[key] += 1
            dependents[dep].append(key)

    queue: deque[str] = deque(s["step_key"] for s in steps if in_degree[s["step_key"]] == 0)
    ordered: list[dict[str, Any]] = []
    while queue:
        cur = queue.popleft()
        ordered.append(by_key[cur])
        for nxt in dependents[cur]:
            in_degree[nxt] -= 1
            if in_degree[nxt] == 0:
                queue.append(nxt)

    if len(ordered) != len(steps):
        cycle_nodes = [s["step_key"] for s in steps if in_degree[s["step_key"]] > 0]
        raise PipelineSpecError(f"Dependency cycle detected among steps: {cycle_nodes}")
    return ordered


def validate_pipeline_spec(raw_spec: Any) -> dict[str, Any]:
    """Strictly validate and normalize a ``pipeline_spec.json`` dictionary."""
    if not isinstance(raw_spec, Mapping):
        raise PipelineSpecError("Pipeline spec must be a JSON object")

    raw_root = _validate_nonempty_str(raw_spec.get("project_root"), "project_root")
    root_candidate = Path(raw_root)
    try:
        project_root = root_candidate.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise PipelineSpecError(f"project_root does not exist: {raw_root!r}") from exc
    if not project_root.is_dir():
        raise PipelineSpecError(f"project_root is not a directory: {project_root}")

    mode = str(raw_spec.get("mode", "swarm")).strip()
    if mode not in ALLOWED_MODES:
        raise PipelineSpecError(f"mode must be one of {sorted(ALLOWED_MODES)} (got {mode!r})")

    raw_title = raw_spec.get("title") or raw_spec.get("objective")
    title = _validate_nonempty_str(raw_title, "title")
    raw_objective = raw_spec.get("objective") or raw_spec.get("goal") or title
    objective = _validate_nonempty_str(raw_objective, "objective")

    raw_steps = raw_spec.get("steps")
    if not isinstance(raw_steps, list) or not raw_steps:
        raise PipelineSpecError("steps must be a non-empty list")

    if "max_workers" in raw_spec and raw_spec["max_workers"] is not None:
        max_workers = raw_spec["max_workers"]
        if (
            isinstance(max_workers, bool)
            or not isinstance(max_workers, int)
            or max_workers < 1
            or max_workers > MAX_WORKERS_CAP
        ):
            raise PipelineSpecError(
                f"max_workers must be an integer between 1 and {MAX_WORKERS_CAP} (got {max_workers!r})"
            )
    else:
        max_workers = min(MAX_WORKERS_CAP, len(raw_steps))

    allow_network = raw_spec.get("allow_network", False)
    if not isinstance(allow_network, bool):
        raise PipelineSpecError("allow_network must be a boolean")

    model: str | None = None
    if raw_spec.get("model") is not None:
        model = _validate_nonempty_str(raw_spec["model"], "model")

    default_profile = _default_env_profile()
    if raw_spec.get("env_profile") is not None:
        top_env_profile = _validate_nonempty_str(raw_spec["env_profile"], "env_profile")
        if top_env_profile not in ENV_PROFILES:
            raise PipelineSpecError(
                f"env_profile must be one of {sorted(ENV_PROFILES)} (got {top_env_profile!r})"
            )
    else:
        top_env_profile = default_profile

    if raw_spec.get("dep_mode") is not None:
        top_dep_mode = _validate_nonempty_str(raw_spec["dep_mode"], "dep_mode")
        if top_dep_mode not in ALLOWED_SPEC_DEP_MODES:
            raise PipelineSpecError(
                f"dep_mode must be one of {sorted(ALLOWED_SPEC_DEP_MODES)} (got {top_dep_mode!r})"
            )
    else:
        top_dep_mode = "auto"

    seen_step_keys: set[str] = set()
    normalized_steps: list[dict[str, Any]] = []

    for idx, item in enumerate(raw_steps):
        s_label = f"steps[{idx}]"
        if not isinstance(item, Mapping):
            raise PipelineSpecError(f"{s_label} must be a JSON object")
        step_key = _validate_nonempty_str(item.get("step_key"), f"{s_label}.step_key")
        if not _SAFE_STEP_KEY_RE.fullmatch(step_key):
            raise PipelineSpecError(
                f"{s_label}.step_key {step_key!r} must match {_SAFE_STEP_KEY_RE.pattern}"
            )
        if step_key in seen_step_keys:
            raise PipelineSpecError(f"Duplicate step_key: {step_key!r}")
        seen_step_keys.add(step_key)

        step_title = _validate_nonempty_str(
            item.get("title") or step_key, f"{s_label}.title"
        )
        instruction = _validate_nonempty_str(
            item.get("instruction") or item.get("prompt"), f"{s_label}.instruction"
        )
        ownership = _validate_rel_ownership_paths(
            item.get("file_ownership"), f"{s_label}.file_ownership"
        )

        raw_criteria = item.get("acceptance_criteria")
        if not isinstance(raw_criteria, list) or not raw_criteria:
            raise PipelineSpecError(f"{s_label}.acceptance_criteria must be a non-empty list")
        criteria: list[str] = []
        seen_crit: set[str] = set()
        for c_idx, raw_c in enumerate(raw_criteria):
            c_str = _validate_nonempty_str(raw_c, f"{s_label}.acceptance_criteria[{c_idx}]")
            if c_str in seen_crit:
                raise PipelineSpecError(
                    f"{s_label}.acceptance_criteria contains duplicate criterion: {c_str!r}"
                )
            seen_crit.add(c_str)
            criteria.append(c_str)

        step_env_profile = (
            _validate_nonempty_str(item["env_profile"], f"{s_label}.env_profile")
            if item.get("env_profile") is not None
            else top_env_profile
        )
        if step_env_profile not in ENV_PROFILES:
            raise PipelineSpecError(
                f"{s_label}.env_profile must be one of {sorted(ENV_PROFILES)} (got {step_env_profile!r})"
            )

        step_dep_mode = (
            _validate_nonempty_str(item["dep_mode"], f"{s_label}.dep_mode")
            if item.get("dep_mode") is not None
            else top_dep_mode
        )
        if step_dep_mode not in ALLOWED_SPEC_DEP_MODES:
            raise PipelineSpecError(
                f"{s_label}.dep_mode must be one of {sorted(ALLOWED_SPEC_DEP_MODES)} (got {step_dep_mode!r})"
            )

        commands = _validate_command_list(
            item.get("commands"),
            label=f"{s_label}.commands",
            step_criteria=criteria,
            project_root=project_root,
        )

        raw_deps = item.get("dependencies", [])
        if not isinstance(raw_deps, list):
            raise PipelineSpecError(f"{s_label}.dependencies must be a list of step_keys")
        deps: list[str] = []
        seen_deps: set[str] = set()
        for d_idx, raw_d in enumerate(raw_deps):
            d_str = _validate_nonempty_str(raw_d, f"{s_label}.dependencies[{d_idx}]")
            if d_str not in seen_deps:
                seen_deps.add(d_str)
                deps.append(d_str)

        complexity = str(item.get("complexity", "normal")).strip()
        if complexity not in ALLOWED_COMPLEXITIES:
            raise PipelineSpecError(
                f"{s_label}.complexity must be one of {sorted(ALLOWED_COMPLEXITIES)} (got {complexity!r})"
            )

        kind = str(item.get("kind", "task")).strip()
        if kind not in ALLOWED_STEP_KINDS:
            raise PipelineSpecError(
                f"{s_label}.kind must be one of {sorted(ALLOWED_STEP_KINDS)} (got {kind!r})"
            )

        safe_worker_suffix = re.sub(r"[^a-z0-9_-]+", "-", step_key.lower()).strip("-") or f"s{idx}"
        owner = _validate_nonempty_str(
            item.get("owner") or f"author-{safe_worker_suffix}",
            f"{s_label}.owner",
        )
        verifier = _validate_nonempty_str(
            item.get("verifier") or f"verifier-{safe_worker_suffix}",
            f"{s_label}.verifier",
        )
        if owner == verifier:
            raise PipelineSpecError(
                f"{s_label}: owner and verifier must be distinct workers (got {owner!r})"
            )

        normalized_steps.append(
            {
                "step_key": step_key,
                "title": step_title,
                "instruction": instruction,
                "owner": owner,
                "verifier": verifier,
                "file_ownership": ownership,
                "acceptance_criteria": criteria,
                "commands": commands,
                "env_profile": step_env_profile,
                "dep_mode": step_dep_mode,
                "dependencies": deps,
                "complexity": complexity,
                "kind": kind,
            }
        )

    topo_steps = _topological_sort_steps(normalized_steps)

    # Reject role mixing across steps and duplicate owner/verifier across concurrent steps.
    owner_scopes: dict[str, list[str]] = {}
    all_owners = {s["owner"] for s in topo_steps}
    all_verifiers = {s["verifier"] for s in topo_steps}
    mixed_roles = sorted(all_owners & all_verifiers)
    if mixed_roles:
        raise PipelineSpecError(
            f"Worker(s) {mixed_roles} cannot act as both owner and verifier in the same pipeline"
        )

    ancestors_by_key: dict[str, set[str]] = {}
    for step in topo_steps:
        anc: set[str] = set(step["dependencies"])
        for dep in step["dependencies"]:
            anc.update(ancestors_by_key.get(dep, ()))
        ancestors_by_key[step["step_key"]] = anc

        prev_scope = owner_scopes.get(step["owner"])
        if prev_scope is not None and prev_scope != step["file_ownership"]:
            raise PipelineSpecError(
                f"Worker {step['owner']!r} is assigned conflicting file_ownership across steps"
            )
        owner_scopes[step["owner"]] = step["file_ownership"]

    for i_idx in range(len(topo_steps)):
        for j_idx in range(i_idx + 1, len(topo_steps)):
            s_a = topo_steps[i_idx]
            s_b = topo_steps[j_idx]
            k_a, k_b = s_a["step_key"], s_b["step_key"]
            is_ordered = (k_a in ancestors_by_key[k_b]) or (k_b in ancestors_by_key[k_a])
            if not is_ordered:
                if s_a["verifier"] == s_b["verifier"]:
                    raise PipelineSpecError(
                        f"Concurrent steps {k_a!r} and {k_b!r} cannot share verifier {s_a['verifier']!r}; "
                        "assign distinct verifier names or add an explicit dependency"
                    )
                if s_a["owner"] == s_b["owner"]:
                    raise PipelineSpecError(
                        f"Concurrent steps {k_a!r} and {k_b!r} cannot share owner {s_a['owner']!r}; "
                        "assign distinct owner names or add an explicit dependency"
                    )

    integration_commands: list[dict[str, Any]] = []
    if raw_spec.get("integration_commands") is not None:
        raw_int = raw_spec["integration_commands"]
        if not isinstance(raw_int, list):
            raise PipelineSpecError("integration_commands must be a list")
        if raw_int:
            integration_commands = _validate_command_list(
                raw_int,
                label="integration_commands",
                step_criteria=None,
                project_root=project_root,
            )

    return {
        "schema_version": SCHEMA_VERSION,
        "project_root": str(project_root),
        "mode": mode,
        "title": title,
        "objective": objective,
        "max_workers": max_workers,
        "allow_network": allow_network,
        "model": model,
        "env_profile": top_env_profile,
        "dep_mode": top_dep_mode,
        "steps": topo_steps,
        "integration_commands": integration_commands,
    }


def _resolve_workspace_dep_mode(spec_dep_mode: str, project_root: Path) -> str:
    """Translate spec ``dep_mode`` into ``WorkspaceManager.workspace_create`` ``dep_mode``."""
    mode = (spec_dep_mode or "auto").strip()
    if mode == "none":
        return "none"
    if mode in {"junction", "link_ro_cache"}:
        return "link_ro_cache"
    if mode in {"uv_sync", "fresh_venv_uv"}:
        return "fresh_venv_uv"
    if mode in {"full_copy", "copy"}:
        return "copy"

    # auto: link existing .venv / node_modules via Windows Junction / POSIX symlink when present
    search_roots = [project_root]
    is_git, repo_root, _, has_head = WorkspaceManager._detect_git_repo(project_root)
    if is_git and has_head and repo_root != project_root:
        search_roots.append(repo_root)
    for root in search_roots:
        for dep_name in (".venv", "node_modules"):
            if (root / dep_name).is_dir():
                return "link_ro_cache"
    return "none"


def _compute_git_subproject_offset(project_root: Path) -> Path:
    """Return relative offset from Git toplevel to ``project_root`` when inside a monorepo subdirectory."""
    resolved_proj = project_root.resolve(strict=False)
    is_git, repo_root, _, has_head = WorkspaceManager._detect_git_repo(resolved_proj)
    if is_git and has_head:
        resolved_repo = repo_root.resolve(strict=False)
        if resolved_proj != resolved_repo and resolved_proj.is_relative_to(resolved_repo):
            return resolved_proj.relative_to(resolved_repo)
    return Path(".")


def _effective_workspace_project_dir(ws_path: Path, project_root: Path) -> Path:
    """Resolve the directory inside ``ws_path`` corresponding to ``project_root``."""
    resolved_ws = ws_path.resolve(strict=False)
    if (resolved_ws / ".git").exists():
        offset = _compute_git_subproject_offset(project_root)
        if offset != Path("."):
            candidate = (resolved_ws / offset).resolve(strict=False)
            if candidate.is_dir():
                return candidate
    return resolved_ws


def _is_pytest_token(token: str) -> bool:
    name_low = Path(token).name.lower()
    return name_low in {"pytest", "pytest.exe", "py.test", "py.test.exe"}


def _sanitize_pytest_cache_argv(argv: Sequence[str]) -> list[str]:
    """Prevent pytest verification commands from dirtying captured trees with ``.pytest_cache``."""
    out = list(argv)
    if not out or "no:cacheprovider" in out or any("cache_dir" in part for part in out):
        return out
    if _is_pytest_token(out[0]):
        return [out[0], "-p", "no:cacheprovider", *out[1:]]
    for idx in range(len(out) - 1):
        if out[idx] == "-m" and out[idx + 1] in {"pytest", "py.test"}:
            return [*out[: idx + 2], "-p", "no:cacheprovider", *out[idx + 2 :]]
    if Path(out[0]).stem.lower() == "uv" and len(out) >= 3 and out[1] == "run":
        for idx in range(2, len(out)):
            tok = out[idx]
            if tok == "--":
                if idx + 1 < len(out) and _is_pytest_token(out[idx + 1]):
                    return [*out[: idx + 2], "-p", "no:cacheprovider", *out[idx + 2 :]]
                break
            if tok.startswith("-"):
                continue
            if _is_pytest_token(tok):
                return [*out[: idx + 1], "-p", "no:cacheprovider", *out[idx + 1 :]]
            break
    return out


def _resolve_commands_for_root(
    commands: Sequence[Mapping[str, Any]],
    *,
    target_root: Path,
    project_root: Path,
) -> list[dict[str, Any]]:
    """Map command ``cwd`` entries strictly inside ``target_root`` (accounting for Git subdirectory offset)."""
    resolved_target = target_root.resolve(strict=False)
    resolved_proj = project_root.resolve(strict=False)
    if resolved_target != resolved_proj and (resolved_target / ".git").exists():
        offset = _compute_git_subproject_offset(resolved_proj)
        base_dir = (resolved_target / offset).resolve(strict=False) if offset != Path(".") else resolved_target
    else:
        base_dir = resolved_target

    resolved: list[dict[str, Any]] = []
    for cmd in commands:
        raw_cwd_str = str(cmd["cwd"])
        raw_cwd = Path(raw_cwd_str)
        if raw_cwd.is_absolute():
            norm_raw = raw_cwd.resolve(strict=False)
            if not (norm_raw == resolved_proj or norm_raw.is_relative_to(resolved_proj)):
                raise PipelineSpecError(
                    f"Command {cmd.get('id')!r} cwd escapes project_root ({resolved_proj}): {raw_cwd_str!r}"
                )
            rel = norm_raw.relative_to(resolved_proj)
            mapped_cwd = (base_dir / rel).resolve(strict=False)
        else:
            if ".." in raw_cwd.parts:
                raise PipelineSpecError(
                    f"Command {cmd.get('id')!r} cwd must not contain '..': {raw_cwd_str!r}"
                )
            mapped_cwd = (base_dir / raw_cwd).resolve(strict=False)

        if not (mapped_cwd == resolved_target or mapped_cwd.is_relative_to(resolved_target)):
            raise PipelineSpecError(
                f"Command {cmd.get('id')!r} resolved cwd escapes target_root ({resolved_target}): {mapped_cwd}"
            )
        mapped_cwd.mkdir(parents=True, exist_ok=True)
        resolved.append(
            {
                "id": str(cmd["id"]),
                "argv": _sanitize_pytest_cache_argv(cmd["argv"]),
                "cwd": str(mapped_cwd),
                "timeout_sec": float(cmd["timeout_sec"]),
                "env_keys": list(cmd.get("env_keys", [])),
                "criteria": list(cmd["criteria"]),
            }
        )
    return resolved


def _ownership_prefix_paths(ownership: Sequence[str]) -> list[str]:
    result: list[str] = []
    for raw in ownership:
        parts: list[str] = []
        for part in Path(raw).parts:
            if any(ch in part for ch in "*?["):
                break
            parts.append(part)
        result.append(Path(*parts).as_posix() if parts else ".")
    return sorted(set(result))


def _decode_command_bytes(raw: bytes) -> str:
    """Decode command log bytes UTF-8 strict first, falling back to GB18030/locale on Windows."""
    if not raw:
        return ""
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        pass
    pref_enc = locale.getpreferredencoding(False)
    fallback_encodings: list[str] = []
    if pref_enc and pref_enc.lower() not in {"utf-8", "utf8", "ascii"}:
        fallback_encodings.append(pref_enc)
    if "gb18030" not in {e.lower() for e in fallback_encodings}:
        fallback_encodings.append("gb18030")
    for enc in fallback_encodings:
        try:
            return raw.decode(enc)
        except (LookupError, UnicodeDecodeError):
            continue
    return raw.decode("utf-8", errors="replace")


def _read_tail(path_str: str | None, max_chars: int = 1600) -> str:
    if not path_str:
        return ""
    p = Path(path_str)
    if not p.is_file():
        return ""
    try:
        raw = p.read_bytes()
    except OSError:
        return ""
    text = _decode_command_bytes(raw)
    if len(text) <= max_chars:
        return text
    return "...[truncated]...\n" + text[-max_chars:]


def _format_turn_failure_detail(turn: Mapping[str, Any]) -> str:
    """Extract concise human-actionable API/transport error diagnostics from a failed turn."""
    parts: list[str] = []
    errors = turn.get("errors")
    if isinstance(errors, list):
        for item in errors:
            if isinstance(item, Mapping):
                msg = item.get("message") or item.get("detail") or item.get("error")
                if isinstance(msg, Mapping):
                    msg = msg.get("message") or json.dumps(dict(msg), ensure_ascii=False)
                if msg:
                    parts.append(str(msg))
                else:
                    parts.append(json.dumps(dict(item), ensure_ascii=False))
            elif item:
                parts.append(str(item))
    completed = turn.get("turn_completed")
    if isinstance(completed, Mapping):
        turn_obj = completed.get("turn")
        if isinstance(turn_obj, Mapping) and turn_obj.get("error"):
            err_val = turn_obj["error"]
            err_str = (
                str(err_val.get("message") or json.dumps(dict(err_val), ensure_ascii=False))
                if isinstance(err_val, Mapping)
                else str(err_val)
            )
            if err_str:
                parts.append(err_str)
    dedup = list(dict.fromkeys(p.strip() for p in parts if p and p.strip()))
    if not dedup:
        return f"turn ended with status={turn.get('status')!r}"
    return "; ".join(dedup)[:1000]


def _is_transient_turn_failure(turn: Mapping[str, Any]) -> bool:
    """Return True for transient 429/5xx/network transport failures eligible for short retry."""
    detail_lower = _format_turn_failure_detail(turn).lower()
    permanent_markers = (
        "401",
        "403",
        "unauthorized",
        "invalid_api_key",
        "invalid api key",
        "incorrect api key",
        "forbidden",
        "authentication",
        "insufficient_quota",
        "billing",
        "invalid_request_error",
        "context_length_exceeded",
    )
    if any(marker in detail_lower for marker in permanent_markers):
        return False
    transient_markers = (
        "429",
        "rate limit",
        "rate_limit",
        "too many requests",
        "500",
        "502",
        "503",
        "504",
        "internal server error",
        "bad gateway",
        "service unavailable",
        "gateway timeout",
        "server_error",
        "overloaded",
        "timeout",
        "timed out",
        "connection",
        "disconnected",
        "transport",
        "reset by peer",
        "eof",
        "temporarily unavailable",
    )
    return any(marker in detail_lower for marker in transient_markers)


def _is_zero_turn_rollout_rpc_error(exc: BaseException) -> bool:
    msg_lower = str(exc).lower()
    if isinstance(exc, AppServerRpcError) and exc.code == -32600:
        return "thread not loaded" in msg_lower or "no rollout found" in msg_lower
    return "thread not loaded" in msg_lower or "no rollout found" in msg_lower


def _can_rehydrate_zero_turn_binding(
    step_row: Mapping[str, Any],
    role_tag: str,
    agent_id: str,
) -> bool:
    """Allow replacing a 0-turn lost rollout ONLY before any delivery or verification materialized."""
    if int(step_row.get("attempt") or 0) > 0:
        return False
    cp = step_row.get("checkpoint") or {}
    if cp.get("last_failure_at") or cp.get("review_dimensions"):
        return False
    obs = (cp.get("native_observations") or {}).get(agent_id)
    obs_ev = str(obs.get("evidence") or "") if isinstance(obs, Mapping) else ""
    if role_tag == "author":
        if step_row.get("state") not in {"dispatched", "implementing"}:
            return False
        if obs_ev.startswith("Finished author turn"):
            return False
        return True
    if role_tag == "verifier":
        if step_row.get("state") != "candidate_review":
            return False
        if obs_ev.startswith("Completed "):
            return False
        return True
    return False


def _summarize_receipt_for_prompt(
    receipt: Mapping[str, Any],
    manifest_commands: Sequence[Mapping[str, Any]],
) -> tuple[bool, set[str], str]:
    """Return ``(all_passed, failed_criteria, formatted_digest_text)`` from ``verify_runner`` receipt."""
    cmd_by_id = {str(c["id"]): c for c in manifest_commands}
    all_passed = receipt.get("status") == "finished"
    failed_criteria: set[str] = set()
    lines: list[str] = [f"Collector status: {receipt.get('status')}"]
    if receipt.get("error"):
        lines.append(f"Collector error: {receipt['error']}")
        all_passed = False

    rec_commands = receipt.get("commands")
    if not isinstance(rec_commands, list) or len(rec_commands) != len(manifest_commands):
        all_passed = False
        for c in manifest_commands:
            failed_criteria.update(c.get("criteria", ()))

    for rec in rec_commands if isinstance(rec_commands, list) else []:
        cid = str(rec.get("command_id") or "")
        exit_code = rec.get("exit_code")
        status = rec.get("status")
        ok = status == "finished" and exit_code == 0
        lines.append(f"- Command `{cid}`: status={status}, exit_code={exit_code}")
        if not ok:
            all_passed = False
            spec_cmd = cmd_by_id.get(cid, {})
            failed_criteria.update(spec_cmd.get("criteria", ()))
            out_tail = _read_tail((rec.get("stdout") or {}).get("path"))
            err_tail = _read_tail((rec.get("stderr") or {}).get("path"))
            if out_tail.strip():
                lines.append(f"  stdout:\n{out_tail.strip()}")
            if err_tail.strip():
                lines.append(f"  stderr:\n{err_tail.strip()}")

    return all_passed, failed_criteria, "\n".join(lines)


def _normalize_verifier_decision(
    *,
    structured: Mapping[str, Any] | None,
    raw_text: str,
    step_criteria: Sequence[str],
    complexity: str,
    commands_all_passed: bool,
    failed_command_criteria: set[str],
    receipt_summary_text: str,
) -> dict[str, Any]:
    """Validate and enforce hard-command invariants over verifier structured JSON output."""
    verdict = "FAIL"
    summary = ""
    findings: list[str] = []
    raw_dims: Mapping[str, Any] = {}
    raw_crit_map: dict[str, str] = {}

    if isinstance(structured, Mapping):
        if structured.get("verdict") in {"PASS", "FAIL"}:
            verdict = str(structured["verdict"])
        if isinstance(structured.get("summary"), str) and structured["summary"].strip():
            summary = structured["summary"].strip()
        if isinstance(structured.get("review_dimensions"), Mapping):
            raw_dims = structured["review_dimensions"]
        if isinstance(structured.get("criteria_results"), list):
            for entry in structured["criteria_results"]:
                if (
                    isinstance(entry, Mapping)
                    and isinstance(entry.get("criterion"), str)
                    and entry.get("status") in {"PASS", "FAIL", "NOT_RUN"}
                ):
                    raw_crit_map[entry["criterion"]] = str(entry["status"])
        if isinstance(structured.get("findings"), list):
            for f_item in structured["findings"]:
                if isinstance(f_item, str) and f_item.strip():
                    findings.append(f_item.strip())
                elif isinstance(f_item, Mapping):
                    findings.append(json.dumps(dict(f_item), ensure_ascii=False, sort_keys=True))

    if not isinstance(structured, Mapping):
        verdict = "FAIL"
        findings.append("Verifier did not produce valid structured output conforming to outputSchema.")
        if raw_text.strip():
            summary = raw_text.strip()[:400]

    # Hard command Layer 1 gate: non-zero exit code ALWAYS forces FAIL regardless of model text.
    if not commands_all_passed:
        verdict = "FAIL"
        findings.append(f"Hard verification command failure:\n{receipt_summary_text}")

    default_dim = "PASS" if verdict == "PASS" else ("FAIL" if complexity == "high" else "NOT_RUN")
    review_dimensions: dict[str, str] = {}
    for dim_key in ("specification", "code", "stress", "evidence"):
        val = raw_dims.get(dim_key)
        review_dimensions[dim_key] = str(val) if val in {"PASS", "FAIL", "NOT_RUN"} else default_dim
    if not commands_all_passed:
        review_dimensions["evidence"] = "FAIL"

    if "FAIL" in review_dimensions.values():
        verdict = "FAIL"
    if complexity == "high" and verdict == "PASS":
        if any(v != "PASS" for v in review_dimensions.values()):
            verdict = "FAIL"

    criteria_results: list[dict[str, str]] = []
    for crit in step_criteria:
        if crit in failed_command_criteria:
            c_status = "FAIL"
        elif crit in raw_crit_map:
            c_status = raw_crit_map[crit]
        else:
            c_status = "PASS" if verdict == "PASS" else "FAIL"
        if verdict == "PASS" and c_status != "PASS":
            verdict = "FAIL"
        criteria_results.append({"criterion": crit, "status": c_status})

    if verdict == "FAIL" and all(item["status"] == "PASS" for item in criteria_results):
        # At least one criterion must reflect the failure so rework prompt is actionable.
        criteria_results[0]["status"] = "FAIL"

    if not summary:
        summary = (
            "All verification commands and blind review checks passed."
            if verdict == "PASS"
            else (findings[0][:300] if findings else "Verification failed.")
        )

    return {
        "verdict": verdict,
        "summary": summary,
        "review_dimensions": review_dimensions,
        "criteria_results": criteria_results,
        "findings": findings,
    }


_RUNTIME_GIT_EXCLUDES = [
    "__pycache__/",
    "*.py[cod]",
    ".pytest_cache/",
    ".ruff_cache/",
    ".mypy_cache/",
]


def _ensure_git_runtime_excludes(repo_path: Path) -> None:
    """Idempotently exclude Python bytecode/tool caches in ``.git/info/exclude`` without touching ``.gitignore``."""
    is_git, _, git_common_dir, _ = WorkspaceManager._detect_git_repo(repo_path)
    if is_git and git_common_dir is not None and git_common_dir.exists():
        _append_git_exclude_entries(git_common_dir, _RUNTIME_GIT_EXCLUDES)


def _commit_workspace_changes_if_dirty(ws_path: Path, step_key: str, attempt: int) -> dict[str, Any]:
    """Stage and commit author file changes cleanly inside an isolated git workspace."""
    git_dir = ws_path / ".git"
    if not git_dir.exists():
        return {"committed": False, "mode": "snapshot"}

    _ensure_git_runtime_excludes(ws_path)

    unmerged = _run_git(ws_path, "-c", "core.quotepath=false", "diff", "--name-only", "--diff-filter=U")
    if unmerged.returncode == 0 and unmerged.stdout.strip():
        files = [ln.strip() for ln in unmerged.stdout.splitlines() if ln.strip()]
        raise PipelineError(f"Workspace {ws_path} contains unresolved git merge conflicts: {files}")

    porcelain = _run_git(ws_path, "-c", "core.quotepath=false", "status", "--porcelain", "-uall")
    if porcelain.returncode != 0:
        raise PipelineError(f"git status failed in {ws_path}: {porcelain.stderr}")
    if not porcelain.stdout.strip():
        head = _run_git(ws_path, "rev-parse", "HEAD")
        return {"committed": False, "mode": "git_clone", "commit": head.stdout.strip()}

    _run_git(ws_path, "add", "-A", check=True)
    staged_check = _run_git(ws_path, "-c", "core.quotepath=false", "diff", "--cached", "--name-only")
    if not staged_check.stdout.strip():
        head = _run_git(ws_path, "rev-parse", "HEAD")
        return {"committed": False, "mode": "git_clone", "commit": head.stdout.strip()}

    id_args = WorkspaceManager._git_identity_args(ws_path)
    commit_msg = f"pipeline({step_key}): candidate attempt {attempt}"
    _run_git(ws_path, *id_args, "commit", "-m", commit_msg, check=True)
    head = _run_git(ws_path, "rev-parse", "HEAD", check=True)
    return {
        "committed": True,
        "mode": "git_clone",
        "commit": head.stdout.strip(),
        "files": [ln.strip() for ln in staged_check.stdout.splitlines() if ln.strip()],
    }


def _build_settings(
    *,
    daemon_url: str | None = None,
    data_dir: str | Path | None = None,
) -> tuple[Settings, bool]:
    """Return ``(settings, has_explicit_daemon_url)``."""
    base = get_settings(data_dir=data_dir)
    if not daemon_url:
        return base, False
    parsed = urlparse(daemon_url.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise PipelineSpecError(f"Invalid --daemon-url {daemon_url!r}; expected http://<host>:<port>")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    return (
        Settings(
            host=parsed.hostname,
            port=port,
            data_dir=base.data_dir,
            stall_sec=base.stall_sec,
            watchdog_interval_sec=base.watchdog_interval_sec,
            port_range=base.port_range,
        ),
        True,
    )


class _DaemonRpcClient:
    """Thin async HTTP client talking strictly to ``127.0.0.1:<port>/api/rpc``."""

    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")
        self._http = httpx.AsyncClient(
            base_url=self.base_url,
            trust_env=False,
            timeout=httpx.Timeout(60.0, connect=5.0),
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def check_health(self) -> dict[str, Any]:
        resp = await self._http.get("/api/health")
        resp.raise_for_status()
        body = resp.json()
        if not isinstance(body, dict) or body.get("status") != "ok":
            raise PipelineError(f"Unhealthy daemon response at {self.base_url}: {body!r}")
        return body

    async def get_run_snapshot(self, run_id: str) -> dict[str, Any]:
        resp = await self._http.get(f"/api/runs/{run_id}/snapshot")
        if resp.status_code >= 400:
            try:
                body = resp.json()
                detail = body.get("detail") or body.get("error") or resp.text
            except ValueError:
                detail = resp.text
            raise PipelineRpcError("get_run_snapshot", str(detail), status_code=resp.status_code)
        return resp.json()

    async def call(self, tool: str, arguments: Mapping[str, Any]) -> Any:
        resp = await self._http.post(
            "/api/rpc",
            json={"tool": tool, "arguments": dict(arguments)},
        )
        try:
            body = resp.json()
        except ValueError:
            body = {"ok": False, "error": resp.text}
        if resp.status_code >= 400 or not isinstance(body, Mapping) or not body.get("ok", False):
            detail = (
                (body.get("detail") or body.get("error"))
                if isinstance(body, Mapping)
                else str(body)
            ) or f"HTTP {resp.status_code}"
            raise PipelineRpcError(tool, str(detail), status_code=resp.status_code)
        return body.get("result")


class PipelineRunner:
    """Bounded async DAG runner coordinating ``AppServerStdioClient`` and daemon gates."""

    def __init__(
        self,
        *,
        settings: Settings,
        explicit_daemon_url: bool = False,
        codex_bin: str | Path | None = None,
        appserver_argv: Sequence[str] | None = None,
        appserver_env: Mapping[str, str] | None = None,
        poll_interval_sec: float = DEFAULT_POLL_INTERVAL_SEC,
        output_path: str | Path | None = None,
    ) -> None:
        self.settings = settings
        self.explicit_daemon_url = explicit_daemon_url
        self.codex_bin = codex_bin
        self.appserver_argv = list(appserver_argv) if appserver_argv is not None else None
        self.appserver_env = dict(appserver_env) if appserver_env is not None else None
        self.poll_interval_sec = min(MAX_POLL_INTERVAL_SEC, max(0.05, float(poll_interval_sec)))
        self.output_path = Path(output_path).resolve() if output_path is not None else None

        self._rpc: _DaemonRpcClient | None = None
        self._appserver: AppServerStdioClient | None = None
        self._merge_lock = asyncio.Lock()
        self._obs_lock = asyncio.Lock()
        self._active_turns_lock = asyncio.Lock()
        self._control_action_lock = asyncio.Lock()
        self._active_turns: dict[str, dict[str, Any]] = {}
        self._central_poller_task: asyncio.Task[None] | None = None
        self._controls_only_supported = True
        self._worker_threads: dict[str, str] = {}
        self._worker_thread_locks: dict[str, asyncio.Lock] = {}
        self._thread_aliases: dict[str, str] = {}
        self._last_obs_ts = 0.0
        self._abort_event = asyncio.Event()
        self._halted_reason: str | None = None
        self._halt_detail: str | None = None
        self._active_workers = 0
        self._peak_workers = 0

        self._step_metrics: dict[str, dict[str, Any]] = {}
        self._all_receipt_paths: list[str] = []
        self._acknowledged_controls: list[dict[str, Any]] = []
        self._saved_interceptions: list[dict[str, Any]] = []
        self._saved_interception_counts: dict[str, int] = {}

    def _get_worker_lock(self, worker_name: str) -> asyncio.Lock:
        lock = self._worker_thread_locks.get(worker_name)
        if lock is None:
            lock = asyncio.Lock()
            self._worker_thread_locks[worker_name] = lock
        return lock

    def _effective_appserver_env(self) -> dict[str, str]:
        merged = {
            "PYTHONUTF8": os.environ.get("PYTHONUTF8") or "1",
            "PYTHONIOENCODING": os.environ.get("PYTHONIOENCODING") or "utf-8",
        }
        if self.appserver_env:
            merged.update(self.appserver_env)
        return merged

    async def _next_obs_ts(self, min_after: float = 0.0) -> float:
        async with self._obs_lock:
            now = time.time()
            target = max(now, self._last_obs_ts + 0.002, float(min_after) + 0.002)
            if target > now:
                await asyncio.sleep(min(0.05, target - now + 0.001))
                target = max(target, time.time())
            self._last_obs_ts = target
            return target

    def _resolve_appserver_argv_override(self) -> list[str] | None:
        if self.appserver_argv is not None:
            return self.appserver_argv
        raw_env_argv = os.environ.get("TRI_ORCH_APPSERVER_ARGV_JSON")
        if raw_env_argv and raw_env_argv.strip():
            parsed = json.loads(raw_env_argv)
            if isinstance(parsed, list) and all(isinstance(x, str) for x in parsed):
                return parsed
        return None

    async def _ensure_daemon_and_clients(self, project_root: Path) -> None:
        os.environ.setdefault("PYTHONUTF8", "1")
        os.environ.setdefault("PYTHONIOENCODING", "utf-8")
        if not self.explicit_daemon_url:
            await asyncio.to_thread(ensure_daemon_running, self.settings)
        self._rpc = _DaemonRpcClient(self.settings.base_url)
        health = await self._rpc.check_health()
        caps = set(health.get("capabilities") or [])
        if "pipeline_runner_v1" not in caps:
            raise PipelineError(
                f"Daemon at {self.settings.base_url} lacks 'pipeline_runner_v1' capability (got {sorted(caps)})"
            )

        self._appserver = AppServerStdioClient(
            codex_bin=self.codex_bin,
            argv=self._resolve_appserver_argv_override(),
            env=self._effective_appserver_env(),
            cwd=project_root,
        )
        await self._appserver.start()

    async def _close_appserver(self) -> None:
        if self._central_poller_task is not None and not self._central_poller_task.done():
            self._central_poller_task.cancel()
            try:
                await self._central_poller_task
            except asyncio.CancelledError:
                pass
        self._central_poller_task = None

        if self._appserver is not None:
            for ev in self._appserver.interceptions:
                self._saved_interceptions.append(ev)
                m = str(ev.get("method") or "unknown")
                self._saved_interception_counts[m] = self._saved_interception_counts.get(m, 0) + 1
            try:
                await self._appserver.close()
            finally:
                self._appserver = None

    async def _close_clients(self) -> None:
        await self._close_appserver()
        if self._rpc is not None:
            try:
                await self._rpc.aclose()
            finally:
                self._rpc = None

    def _init_step_metric(self, step_cfg: Mapping[str, Any], step_id: str) -> dict[str, Any]:
        metric = self._step_metrics.setdefault(
            step_cfg["step_key"],
            {
                "step_key": step_cfg["step_key"],
                "step_id": step_id,
                "owner": step_cfg["owner"],
                "verifier": step_cfg["verifier"],
                "state": "planned",
                "attempts": 0,
                "reworks": 0,
                "author_thread_id": None,
                "verifier_thread_id": None,
                "workspace_id": None,
                "receipt_paths": [],
                "turns": 0,
            },
        )
        metric["step_id"] = step_id
        return metric

    async def _fetch_step(self, run_id: str, step_id: str) -> dict[str, Any]:
        assert self._rpc is not None
        status = await self._rpc.call("board_status", {"run_id": run_id, "compact": True})
        for step in status["workflow"]["steps"]:
            if step["id"] == step_id:
                return step
        raise PipelineError(f"Workflow step {step_id} not found in run {run_id}")

    async def _record_observation(
        self,
        *,
        run_id: str,
        step_id: str,
        agent_id: str,
        state: str,
        evidence: str,
        min_after: float = 0.0,
    ) -> dict[str, Any]:
        assert self._rpc is not None
        step = await self._fetch_step(run_id, step_id)
        prev_obs = step.get("checkpoint", {}).get("native_observations", {}).get(agent_id)
        prev_ts = float(prev_obs["observed_at"]) if isinstance(prev_obs, Mapping) and "observed_at" in prev_obs else 0.0
        obs_ts = await self._next_obs_ts(max(min_after, prev_ts))
        return await self._rpc.call(
            "workflow_step_advance",
            {
                "step_id": step_id,
                "action": "observe",
                "expected_revision": step["revision"],
                "observation": {
                    "agent_id": agent_id,
                    "state": state,
                    "observed_at": obs_ts,
                    "evidence": evidence,
                },
            },
        )

    async def _poll_pending_controls(self, run_id: str) -> list[dict[str, Any]]:
        assert self._rpc is not None
        b_stat: Mapping[str, Any] | None = None
        if self._controls_only_supported:
            try:
                b_stat = await self._rpc.call(
                    "board_status",
                    {"run_id": run_id, "compact": True, "controls_only": True},
                )
            except PipelineRpcError as exc:
                if "controls_only" in exc.detail or "unexpected" in exc.detail.lower():
                    self._controls_only_supported = False
                else:
                    raise
        if b_stat is None:
            b_stat = await self._rpc.call(
                "board_status", {"run_id": run_id, "compact": True}
            )
        return [
            dict(c)
            for c in b_stat.get("control_requests", [])
            if isinstance(c, Mapping) and c.get("status") == "pending"
        ]

    async def _interrupt_single_active_entry(
        self, entry: Mapping[str, Any]
    ) -> tuple[str, dict[str, Any]]:
        assert self._appserver is not None
        tid = str(entry["thread_id"])
        holder = entry["turn_id_holder"]
        deadline = time.monotonic() + 0.45
        while holder.get("turn_id") is None and time.monotonic() < deadline:
            await asyncio.sleep(0.015)

        receipt: dict[str, Any] = {
            "interrupted": False,
            "thread_id": tid,
            "worker_name": entry["worker_name"],
            "step_id": entry["step_id"],
        }
        turn_id = holder.get("turn_id")
        if turn_id:
            try:
                int_res = await self._appserver.interrupt_turn(tid, str(turn_id))
                receipt = dict(int_res)
                receipt["interrupted"] = True
                receipt["worker_name"] = entry["worker_name"]
                receipt["step_id"] = entry["step_id"]
            except AppServerError as exc:
                receipt["error"] = str(exc)
        return tid, receipt

    async def _acknowledge_and_interrupt_all_controls(
        self,
        *,
        run_id: str,
        pending_controls: Sequence[Mapping[str, Any]],
    ) -> None:
        async with self._control_action_lock:
            if self._halted_reason == "interrupted_by_control":
                return
            assert self._rpc is not None and self._appserver is not None
            self._halted_reason = "interrupted_by_control"
            self._abort_event.set()

            async with self._active_turns_lock:
                active_entries = list(self._active_turns.values())

            receipts_by_worker: dict[str, dict[str, Any]] = {}
            all_receipts: list[dict[str, Any]] = []
            if active_entries:
                pairs = await asyncio.gather(
                    *(self._interrupt_single_active_entry(ent) for ent in active_entries),
                    return_exceptions=True,
                )
                for ent, res in zip(active_entries, pairs):
                    if isinstance(res, tuple):
                        _, rec = res
                    else:
                        rec = {
                            "interrupted": False,
                            "thread_id": ent["thread_id"],
                            "worker_name": ent["worker_name"],
                            "error": str(res),
                        }
                    receipts_by_worker[str(ent["worker_name"])] = rec
                    all_receipts.append(rec)

            status_data = await self._rpc.call("board_status", {"run_id": run_id, "compact": True})
            next_actions = status_data.get("native_actions", {}).get("next_actions", [])
            control_action_map = {
                str(item["source_id"]): item
                for item in next_actions
                if item.get("source_type") == "control"
            }

            for ctl in pending_controls:
                req_id = str(ctl["id"])
                target_wname = str(ctl.get("worker_name") or "")
                matched_rec = receipts_by_worker.get(target_wname) or (
                    all_receipts[0]
                    if all_receipts
                    else {"interrupted": False, "worker_name": target_wname}
                )
                if req_id in control_action_map:
                    act_item = control_action_map[req_id]
                    try:
                        claimed = await self._rpc.call(
                            "native_action_update",
                            {
                                "action": "claim",
                                "run_id": run_id,
                                "source_type": "control",
                                "source_id": req_id,
                                "recipient": act_item["recipient"],
                                "action_type": "interrupt_agent",
                                "source_digest": act_item["source_digest"],
                            },
                        )
                        begun = await self._rpc.call(
                            "native_action_update",
                            {
                                "action": "begin",
                                "action_id": claimed["action_id"],
                                "claim_token": claimed["claim_token"],
                                "expected_revision": claimed["revision"],
                            },
                        )
                        await self._rpc.call(
                            "native_action_record_call",
                            {
                                "run_id": run_id,
                                "action_id": begun["action_id"],
                                "claim_token": begun["claim_token"],
                                "expected_revision": begun["revision"],
                                "outcome": "confirmed",
                                "raw_response": {
                                    "target_receipt": matched_rec,
                                    "interrupted_turns": all_receipts,
                                },
                            },
                        )
                    except PipelineRpcError:
                        pass

                ack_msg = (
                    f"Interrupted {len(all_receipts)} active pipeline turn(s) via app-server turn/interrupt "
                    f"(target={target_wname or 'run'}, requested_status={ctl.get('requested_status')})"
                )
                try:
                    acked = await self._rpc.call(
                        "worker_control_ack",
                        {
                            "request_id": req_id,
                            "outcome": "applied",
                            "result": ack_msg,
                        },
                    )
                    self._acknowledged_controls.append(acked)
                except PipelineRpcError:
                    pass

            seen_obs: set[tuple[str, str]] = set()
            for ent in active_entries:
                obs_pair = (str(ent["step_id"]), str(ent["agent_id"]))
                if obs_pair in seen_obs:
                    continue
                seen_obs.add(obs_pair)
                try:
                    await self._record_observation(
                        run_id=run_id,
                        step_id=obs_pair[0],
                        agent_id=obs_pair[1],
                        state="interrupted",
                        evidence=f"Interrupted by user control ({[c['id'] for c in pending_controls]})",
                    )
                except PipelineRpcError:
                    pass

            self._halt_detail = f"Interrupted by worker_control: {[c['id'] for c in pending_controls]}"

    async def _central_control_poller_loop(self, run_id: str) -> None:
        while not self._abort_event.is_set():
            async with self._active_turns_lock:
                has_active = bool(self._active_turns)
            if not has_active:
                return
            try:
                pending = await self._poll_pending_controls(run_id)
                if pending:
                    await self._acknowledge_and_interrupt_all_controls(
                        run_id=run_id,
                        pending_controls=pending,
                    )
                    return
            except asyncio.CancelledError:
                raise
            except Exception:
                pass
            await asyncio.sleep(self.poll_interval_sec)

    async def _ensure_central_poller(self, run_id: str) -> None:
        if self._abort_event.is_set():
            return
        if self._central_poller_task is None or self._central_poller_task.done():
            self._central_poller_task = asyncio.create_task(
                self._central_control_poller_loop(run_id),
                name=f"pipeline-central-ctl-{run_id}",
            )

    async def _run_turn_with_control_watch(
        self,
        *,
        run_id: str,
        step_id: str,
        worker_name: str,
        thread_id: str,
        agent_id: str,
        prompt: str,
        cwd: Path,
        role: str,
        model: str | None,
        effort: str,
        allow_network: bool,
    ) -> dict[str, Any] | None:
        assert self._rpc is not None and self._appserver is not None

        async def _on_progress(prog: dict[str, Any]) -> None:
            if self._abort_event.is_set():
                return
            try:
                await self._rpc.call(
                    "worker_heartbeat",
                    {
                        "run_id": run_id,
                        "worker_name": worker_name,
                        "status": "working",
                        "current_task": f"{role} turn ({prog.get('summary', 'active')})"[:120],
                    },
                )
            except Exception:
                pass

        worker_lock = self._get_worker_lock(worker_name)
        async with worker_lock:
            for retry_idx in range(TURN_TRANSIENT_MAX_RETRIES + 1):
                if self._abort_event.is_set():
                    return None

                turn_id_holder: dict[str, str | None] = {"turn_id": None}
                entry = {
                    "thread_id": thread_id,
                    "agent_id": agent_id,
                    "worker_name": worker_name,
                    "step_id": step_id,
                    "role": role,
                    "turn_id_holder": turn_id_holder,
                }

                def _on_started(tid_val: str, _holder: dict[str, str | None] = turn_id_holder) -> None:
                    _holder["turn_id"] = tid_val

                async with self._active_turns_lock:
                    self._active_turns[thread_id] = entry
                await self._ensure_central_poller(run_id)

                turn_res_dict: dict[str, Any] | None = None
                try:
                    turn_res = await self._appserver.run_turn(
                        thread_id,
                        prompt,
                        cwd=cwd,
                        role=role,
                        model=model,
                        effort=effort,
                        allow_network=allow_network,
                        on_progress=_on_progress,
                        progress_interval_sec=1.0,
                        on_turn_started=_on_started,
                    )
                    turn_res_dict = dict(turn_res)
                except AppServerError:
                    if not self._abort_event.is_set():
                        raise
                finally:
                    async with self._active_turns_lock:
                        if self._active_turns.get(thread_id) is entry:
                            self._active_turns.pop(thread_id, None)

                if self._halted_reason == "interrupted_by_control":
                    if self._central_poller_task is not None and not self._central_poller_task.done():
                        try:
                            await asyncio.wait_for(self._central_poller_task, timeout=2.0)
                        except Exception:
                            pass
                    return None

                if turn_res_dict is None or self._abort_event.is_set():
                    return None

                turn_status = str(turn_res_dict.get("status") or "completed")
                if turn_status == "completed":
                    return turn_res_dict

                if turn_status == "interrupted":
                    # Check if a pending control caused or coincided with the interruption
                    try:
                        pending = await self._poll_pending_controls(run_id)
                        if pending:
                            await self._acknowledge_and_interrupt_all_controls(
                                run_id=run_id,
                                pending_controls=pending,
                            )
                            return None
                    except Exception:
                        pass

                err_diag = _format_turn_failure_detail(turn_res_dict)
                if (
                    retry_idx < TURN_TRANSIENT_MAX_RETRIES
                    and turn_status == "failed"
                    and _is_transient_turn_failure(turn_res_dict)
                    and not self._abort_event.is_set()
                ):
                    await asyncio.sleep(0.25 * (retry_idx + 1))
                    continue

                # Permanent API/auth/turn failure or exhausted transient retries:
                # Halt cleanly WITHOUT submitting candidate or burning rework attempt budget!
                try:
                    await self._record_observation(
                        run_id=run_id,
                        step_id=step_id,
                        agent_id=agent_id,
                        state="idle",
                        evidence=f"{role} turn failed without consuming attempt budget: {err_diag[:180]}",
                    )
                    await self._rpc.call(
                        "worker_status_update",
                        {
                            "run_id": run_id,
                            "worker_name": worker_name,
                            "status": "waiting",
                            "current_task": f"Halted on API/turn error: {err_diag[:100]}",
                        },
                    )
                except Exception:
                    pass

                self._halted_reason = "turn_failed"
                self._halt_detail = (
                    f"{role.capitalize()} turn failed on {worker_name} ({step_id}): {err_diag}"
                )
                self._abort_event.set()
                return None

        return None

    @staticmethod
    def _build_author_prompt(
        *,
        spec: Mapping[str, Any],
        step_cfg: Mapping[str, Any],
        ws_path: Path,
        rework_feedback: str | None = None,
        attempt: int = 0,
    ) -> str:
        sections = [
            f"# Pipeline Step: {step_cfg['step_key']} — {step_cfg['title']}",
            f"Run Objective: {spec['objective']}",
            f"Isolated Workspace Directory: {ws_path}",
            f"Exclusive File Ownership: {', '.join(step_cfg['file_ownership'])}",
            "Acceptance Criteria:",
            *[f"- {c}" for c in step_cfg["acceptance_criteria"]],
            "",
            "## Task Instructions",
            step_cfg["instruction"],
            "",
            "Stay strictly within your Exclusive File Ownership inside the isolated workspace.",
            "Do not request out-of-sandbox approvals or interactive user input.",
        ]
        if rework_feedback:
            sections.extend(
                [
                    "",
                    f"## Rework Required (Attempt {attempt} of {MAX_REWORKS})",
                    "Previous candidate verification FAILED. Fix the root cause of all failures below:",
                    rework_feedback,
                ]
            )
        return "\n".join(sections)

    @staticmethod
    def _build_verifier_prompt(
        *,
        step_cfg: Mapping[str, Any],
        scope: str,
        target_root: Path,
        diff_summary: str,
        receipt_summary: str,
        commands_all_passed: bool,
    ) -> str:
        return "\n".join(
            [
                f"# Independent Blind Verification ({scope.upper()}) — Step {step_cfg['step_key']}",
                "Perform a strict read-only audit against the frozen acceptance criteria and command receipt.",
                "Do NOT modify, create, or delete any files.",
                f"Target Root: {target_root}",
                f"Step Complexity: {step_cfg['complexity']}",
                f"Exclusive File Ownership: {', '.join(step_cfg['file_ownership'])}",
                "Acceptance Criteria (criteria_results must include every item verbatim):",
                *[f"- {c}" for c in step_cfg["acceptance_criteria"]],
                "",
                f"Hard Command Collector All Passed: {commands_all_passed}",
                "## Hard Command Evidence Summary",
                receipt_summary,
                "",
                "## Diff Summary",
                diff_summary[:6000] if diff_summary else "(no diff text)",
                "",
                "Return ONLY valid JSON matching outputSchema with keys: "
                "verdict, summary, review_dimensions, criteria_results, findings.",
            ]
        )

    def _collect_verification_evidence_Sync(
        self,
        *,
        run_id: str,
        step_key: str,
        scope: str,
        attempt: int,
        manifest_path: Path,
    ) -> tuple[Path, dict[str, Any]]:
        os.environ.setdefault("PYTHONUTF8", "1")
        os.environ.setdefault("PYTHONIOENCODING", "utf-8")
        safe_run = re.sub(r"[^A-Za-z0-9._-]+", "_", run_id).strip("._-") or "run"
        out_dir = (
            self.settings.data_dir
            / "verification-runs"
            / safe_run
            / f"{step_key}-{scope}-a{attempt}-{uuid.uuid4().hex[:8]}"
        ).resolve()
        out_dir.parent.mkdir(parents=True, exist_ok=True)
        receipt = verify_runner.collect(manifest_path, out_dir)
        receipt_path = out_dir / "receipt.json"
        return receipt_path, receipt

    async def _find_open_verifier_message(
        self, run_id: str, verifier_name: str, step_id: str
    ) -> dict[str, Any] | None:
        assert self._rpc is not None
        try:
            res = await self._rpc.call(
                "inbox_wait",
                {
                    "run_id": run_id,
                    "recipient": verifier_name,
                    "timeout_ms": 0,
                },
            )
        except PipelineRpcError as exc:
            if exc.status_code == 409 and "binding" in exc.detail.lower():
                return None
            raise
        for msg in res.get("messages", []):
            if msg.get("step_id") == step_id and msg.get("kind") == "review":
                return msg
        return None

    async def _ensure_verifier_thread(
        self,
        *,
        verifier_name: str,
        cwd: Path,
        model: str | None,
        existing_tid: str | None = None,
    ) -> str:
        assert self._appserver is not None
        async with self._get_worker_lock(verifier_name):
            cached = existing_tid or self._worker_threads.get(verifier_name)
            if cached:
                self._worker_threads[verifier_name] = cached
                return cached
            started_ver = await self._appserver.start_verifier_thread(
                cwd,
                model=model,
                effort="high",
            )
            tid = str(started_ver["thread_id"])
            self._worker_threads[verifier_name] = tid
            return tid

    async def _run_verification_gate(
        self,
        *,
        run_id: str,
        mode: str,
        spec: Mapping[str, Any],
        step_cfg: Mapping[str, Any],
        step_id: str,
        scope: str,
        target_root: Path,
        project_root: Path,
        verifier_tid: str | None,
        verifier_agent_id: str | None,
        capture_paths: list[str] | None,
        diff_text: str,
        include_integration_commands: bool = False,
        author_tid: str | None = None,
    ) -> dict[str, Any] | None:
        """Execute ``request_verify -> artifact_capture -> verify_runner -> blind verifier -> verification_record``."""
        assert self._rpc is not None and self._appserver is not None
        verifier_name = step_cfg["verifier"]
        metric = self._step_metrics[step_cfg["step_key"]]

        # Layer 1 preparation: validate & resolve command cwds and runtime excludes BEFORE binding/capturing
        raw_cmds = list(step_cfg["commands"])
        if scope == "integrated" and include_integration_commands and spec.get("integration_commands"):
            for idx, extra_cmd in enumerate(spec["integration_commands"]):
                extra_copy = dict(extra_cmd)
                extra_copy["id"] = f"int_{idx}_{extra_cmd['id']}"
                # Map integration command criteria onto valid step criteria subset
                valid_crit = [c for c in extra_cmd["criteria"] if c in step_cfg["acceptance_criteria"]]
                extra_copy["criteria"] = valid_crit or [step_cfg["acceptance_criteria"][0]]
                raw_cmds.append(extra_copy)

        resolved_cmds = _resolve_commands_for_root(
            raw_cmds,
            target_root=target_root,
            project_root=project_root,
        )
        await asyncio.to_thread(_ensure_git_runtime_excludes, target_root)

        verifier_cwd = _effective_workspace_project_dir(target_root, project_root)
        resolved_v_tid = await self._ensure_verifier_thread(
            verifier_name=verifier_name,
            cwd=verifier_cwd,
            model=spec["model"],
            existing_tid=verifier_tid,
        )
        if author_tid and resolved_v_tid == author_tid:
            raise PipelineError(
                f"Blind review violation: verifier_tid == author_tid ({resolved_v_tid})"
            )
        resolved_v_agent_id = verifier_agent_id or format_appserver_agent_id(resolved_v_tid)
        metric["verifier_thread_id"] = resolved_v_tid

        open_review_msg = await self._find_open_verifier_message(run_id, verifier_name, step_id)
        if open_review_msg is not None:
            review_msg_id = str(open_review_msg["id"])
            if open_review_msg.get("status") == "pending":
                await self._rpc.call(
                    "inbox_ack",
                    {
                        "message_id": review_msg_id,
                        "recipient": verifier_name,
                        "status": "accepted",
                        "evidence": f"Resumed {scope} verification on {resolved_v_agent_id}",
                    },
                )
            await self._rpc.call(
                "worker_status_update",
                {
                    "run_id": run_id,
                    "worker_name": verifier_name,
                    "status": "working",
                    "current_task": f"Blind {scope} verification for {step_cfg['step_key']}",
                },
            )
        else:
            prep_verify = await self._rpc.call(
                "workflow_prepare_dispatch",
                {
                    "run_id": run_id,
                    "dispatch_action": "request_verify",
                    "step_id": step_id,
                    "verifier": verifier_name,
                    "agent_id": resolved_v_agent_id,
                    "worker_status": "working",
                    "instruction": f"Blind {scope} verification for {step_cfg['step_key']}",
                },
            )
            await self._rpc.call(
                "native_action_record_call",
                {
                    "run_id": run_id,
                    "action_id": prep_verify["action_id"],
                    "claim_token": prep_verify["claim_token"],
                    "expected_revision": prep_verify["expected_revision"],
                    "outcome": "confirmed",
                    "agent_id": resolved_v_agent_id,
                    "raw_response": {
                        "verifier_thread_id": resolved_v_tid,
                        "scope": scope,
                        "step_id": step_id,
                    },
                },
            )
            review_msg_id = prep_verify["message_id"]
            await self._rpc.call(
                "inbox_ack",
                {
                    "message_id": review_msg_id,
                    "recipient": verifier_name,
                    "status": "accepted",
                    "evidence": f"Accepted {scope} verification on {resolved_v_agent_id}",
                },
            )

        capture_args: dict[str, Any] = {
            "step_id": step_id,
            "scope": scope,
            "verification_commands": resolved_cmds,
            "env_profile": step_cfg["env_profile"],
        }
        if capture_paths is not None:
            capture_args["paths"] = capture_paths

        artifact = await self._rpc.call("artifact_capture", capture_args)
        step_now = await self._fetch_step(run_id, step_id)
        manifest_path = Path(artifact["descriptor"]["verification_manifest_path"])

        receipt_path, receipt = await asyncio.to_thread(
            self._collect_verification_evidence_Sync,
            run_id=run_id,
            step_key=step_cfg["step_key"],
            scope=scope,
            attempt=step_now["attempt"],
            manifest_path=manifest_path,
        )
        receipt_str = str(receipt_path)
        metric["receipt_paths"].append(receipt_str)
        self._all_receipt_paths.append(receipt_str)

        if self._abort_event.is_set():
            return None

        commands_passed, failed_criteria, receipt_summary = _summarize_receipt_for_prompt(
            receipt, resolved_cmds
        )

        # Layer 2: Independent read-only blind verifier turn
        v_prompt = self._build_verifier_prompt(
            step_cfg=step_cfg,
            scope=scope,
            target_root=verifier_cwd,
            diff_summary=diff_text,
            receipt_summary=receipt_summary,
            commands_all_passed=commands_passed,
        )
        v_turn = await self._run_turn_with_control_watch(
            run_id=run_id,
            step_id=step_id,
            worker_name=verifier_name,
            thread_id=resolved_v_tid,
            agent_id=resolved_v_agent_id,
            prompt=v_prompt,
            cwd=verifier_cwd,
            role="verifier",
            model=spec["model"],
            effort="high",
            allow_network=False,
        )
        if v_turn is None:
            return None
        metric["turns"] += 1

        if v_turn.get("needs_clarification"):
            await self._record_observation(
                run_id=run_id,
                step_id=step_id,
                agent_id=resolved_v_agent_id,
                state="idle",
                evidence="Verifier paused due to requestUserInput interception",
            )
            self._halted_reason = "needs_clarification"
            self._halt_detail = f"Verifier {verifier_name} requested interactive user input on {step_cfg['step_key']}"
            self._abort_event.set()
            return None

        decision = _normalize_verifier_decision(
            structured=v_turn.get("structured_output"),
            raw_text=str(v_turn.get("final_message") or ""),
            step_criteria=step_cfg["acceptance_criteria"],
            complexity=step_cfg["complexity"],
            commands_all_passed=commands_passed,
            failed_command_criteria=failed_criteria,
            receipt_summary_text=receipt_summary,
        )

        # Boost mode requires independent verifier challenge before candidate acceptance.
        if mode == "boost" and scope == "candidate":
            step_before_chal = await self._fetch_step(run_id, step_id)
            await self._rpc.call(
                "workflow_step_advance",
                {
                    "step_id": step_id,
                    "action": "challenge",
                    "expected_revision": step_before_chal["revision"],
                    "agent_id": resolved_v_agent_id,
                    "artifact_id": artifact["id"],
                    "evidence": f"Challenge review ({decision['verdict']}): {decision['summary']}",
                },
            )

        evidence_files = [receipt_str]
        for cmd_rec in receipt.get("commands", []):
            for stream_key in ("stdout", "stderr"):
                log_p = (cmd_rec.get(stream_key) or {}).get("path")
                if isinstance(log_p, str) and Path(log_p).is_file():
                    evidence_files.append(log_p)

        step_for_key = await self._fetch_step(run_id, step_id)
        rec_key = (
            f"{step_id}:{scope}:v{step_for_key['packet_version']}"
            f":c{step_for_key['contract_revision']}:a{step_for_key['attempt']}:{artifact['id']}"
        )
        ver_record = await self._rpc.call(
            "verification_record",
            {
                "step_id": step_id,
                "artifact_id": artifact["id"],
                "verifier": verifier_name,
                "verdict": decision["verdict"],
                "criteria_results": decision["criteria_results"],
                "evidence_files": list(dict.fromkeys(evidence_files)),
                "receipt_files": [receipt_str],
                "review_dimensions": decision["review_dimensions"],
                "key": rec_key,
            },
        )

        await self._rpc.call(
            "inbox_ack",
            {
                "message_id": review_msg_id,
                "recipient": verifier_name,
                "status": "completed",
                "evidence": f"{scope} {decision['verdict']}: {decision['summary'][:240]}",
            },
        )
        await self._record_observation(
            run_id=run_id,
            step_id=step_id,
            agent_id=resolved_v_agent_id,
            state="idle",
            evidence=f"Completed {scope} review ({decision['verdict']})",
        )
        await self._rpc.call(
            "worker_status_update",
            {
                "run_id": run_id,
                "worker_name": verifier_name,
                "status": "waiting",
                "current_task": f"Last {scope} verdict: {decision['verdict']}",
            },
        )

        step_after = await self._fetch_step(run_id, step_id)
        metric["state"] = step_after["state"]
        return {
            "verdict": decision["verdict"],
            "summary": decision["summary"],
            "findings": decision["findings"],
            "criteria_results": decision["criteria_results"],
            "receipt_summary": receipt_summary,
            "receipt_path": receipt_str,
            "verification_record": ver_record,
            "step": step_after,
            "verifier_tid": resolved_v_tid,
        }

    async def _find_open_author_message(
        self, run_id: str, owner_name: str, step_id: str
    ) -> dict[str, Any] | None:
        assert self._rpc is not None
        res = await self._rpc.call(
            "inbox_wait",
            {
                "run_id": run_id,
                "recipient": owner_name,
                "timeout_ms": 0,
            },
        )
        for msg in res.get("messages", []):
            if msg.get("step_id") == step_id and msg.get("kind") in {"task", "rework"}:
                return msg
        return None

    async def _execute_single_step(
        self,
        *,
        run_id: str,
        spec: Mapping[str, Any],
        step_cfg: Mapping[str, Any],
        step_id: str,
        project_root: Path,
        semaphore: asyncio.Semaphore,
        resumed_threads: dict[str, str],
    ) -> bool:
        """Drive a single DAG step from its current state through ``done``."""
        assert self._rpc is not None and self._appserver is not None
        async with semaphore:
            if self._abort_event.is_set():
                return False

            self._active_workers += 1
            if self._active_workers > self._peak_workers:
                self._peak_workers = self._active_workers

            try:
                owner_name = step_cfg["owner"]
                verifier_name = step_cfg["verifier"]
                metric = self._init_step_metric(step_cfg, step_id)
                step = await self._fetch_step(run_id, step_id)
                metric["state"] = step["state"]

                if step["state"] == "done":
                    return True
                if step["state"] in {"needs_decision", "reconciliation_required"}:
                    self._halted_reason = step["state"]
                    self._halt_detail = f"Step {step_cfg['step_key']} is in {step['state']}"
                    self._abort_event.set()
                    return False

                ws_id: str | None = step.get("workspace_id")
                ws_path: Path | None = None
                if ws_id:
                    ws_info = await self._rpc.call("workspace_status", {"workspace_id": ws_id})
                    ws_path = Path(ws_info["path"]).resolve()
                    metric["workspace_id"] = ws_id

                author_tid = resumed_threads.get(owner_name) or self._worker_threads.get(owner_name)
                if not author_tid and step.get("owner_agent_id"):
                    author_tid = normalize_thread_id(step["owner_agent_id"])
                if author_tid:
                    self._worker_threads[owner_name] = author_tid

                verifier_tid = resumed_threads.get(verifier_name) or self._worker_threads.get(verifier_name)
                if not verifier_tid and step.get("verifier_agent_id"):
                    verifier_tid = normalize_thread_id(step["verifier_agent_id"])
                if verifier_tid:
                    self._worker_threads[verifier_name] = verifier_tid

                current_msg_id: str | None = None
                rework_feedback: str | None = None

                # Initial dispatch if step is still planned
                if step["state"] == "planned":
                    if ws_id is None:
                        resolved_dep_mode = _resolve_workspace_dep_mode(
                            str(step_cfg.get("dep_mode") or spec.get("dep_mode") or "auto"),
                            project_root,
                        )
                        ws_created = await self._rpc.call(
                            "workspace_create",
                            {
                                "run_id": run_id,
                                "worker_name": owner_name,
                                "copy_env": True,
                                "dep_mode": resolved_dep_mode,
                            },
                        )
                        ws_id = ws_created["id"]
                        ws_path = Path(ws_created["path"]).resolve()
                        metric["workspace_id"] = ws_id

                    assert ws_path is not None
                    author_cwd = _effective_workspace_project_dir(ws_path, project_root)
                    if not author_tid:
                        async with self._get_worker_lock(owner_name):
                            author_tid = self._worker_threads.get(owner_name)
                            if not author_tid:
                                started_author = await self._appserver.start_author_thread(
                                    author_cwd,
                                    model=spec["model"],
                                    effort="medium",
                                    allow_network=spec["allow_network"],
                                )
                                author_tid = str(started_author["thread_id"])
                                self._worker_threads[owner_name] = author_tid
                                author_raw = started_author["raw_response"]
                            else:
                                author_raw = {"reused_thread_id": author_tid}
                    else:
                        author_raw = {"resumed_thread_id": author_tid}

                    author_agent_id = format_appserver_agent_id(author_tid)
                    metric["author_thread_id"] = author_tid

                    init_prompt = self._build_author_prompt(
                        spec=spec,
                        step_cfg=step_cfg,
                        ws_path=author_cwd,
                        attempt=0,
                    )
                    prep = await self._rpc.call(
                        "workflow_prepare_dispatch",
                        {
                            "run_id": run_id,
                            "dispatch_action": "dispatch",
                            "step_id": step_id,
                            "workspace_id": ws_id,
                            "owner": owner_name,
                            "verifier": verifier_name,
                            "agent_id": author_agent_id,
                            "worker_status": "working",
                            "execution_kind": EXECUTION_KIND,
                            "title": step_cfg["title"],
                            "instruction": init_prompt,
                        },
                    )
                    await self._rpc.call(
                        "native_action_record_call",
                        {
                            "run_id": run_id,
                            "action_id": prep["action_id"],
                            "claim_token": prep["claim_token"],
                            "expected_revision": prep["expected_revision"],
                            "outcome": "confirmed",
                            "agent_id": author_agent_id,
                            "raw_response": author_raw,
                        },
                    )
                    current_msg_id = prep["message_id"]
                    step = prep["step"]
                    metric["state"] = step["state"]

                # Candidate implementation + auto-rework loop (initial + up to MAX_REWORKS=3)
                while step["state"] in {"dispatched", "implementing", "rework", "candidate_review"}:
                    if self._abort_event.is_set():
                        return False

                    if step["state"] == "rework":
                        if step["attempt"] >= MAX_REWORKS:
                            self._halted_reason = "needs_decision"
                            self._halt_detail = (
                                f"Step {step_cfg['step_key']} exhausted rework cap ({MAX_REWORKS})"
                            )
                            self._abort_event.set()
                            return False
                        assert author_tid is not None and ws_path is not None
                        author_agent_id = format_appserver_agent_id(author_tid)
                        last_fail_at = float(step.get("checkpoint", {}).get("last_failure_at") or 0.0)
                        await self._record_observation(
                            run_id=run_id,
                            step_id=step_id,
                            agent_id=author_agent_id,
                            state="idle",
                            evidence=f"Author idle before rework attempt {step['attempt'] + 1}",
                            min_after=last_fail_at,
                        )
                        next_attempt = step["attempt"] + 1
                        author_cwd = _effective_workspace_project_dir(ws_path, project_root)
                        rework_prompt = self._build_author_prompt(
                            spec=spec,
                            step_cfg=step_cfg,
                            ws_path=author_cwd,
                            rework_feedback=rework_feedback or "Inspect failing verification receipts and fix defects.",
                            attempt=next_attempt,
                        )
                        rework_prep = await self._rpc.call(
                            "workflow_prepare_dispatch",
                            {
                                "run_id": run_id,
                                "dispatch_action": "start_rework",
                                "step_id": step_id,
                                "owner": owner_name,
                                "agent_id": author_agent_id,
                                "worker_status": "working",
                                "instruction": rework_prompt,
                            },
                        )
                        await self._rpc.call(
                            "native_action_record_call",
                            {
                                "run_id": run_id,
                                "action_id": rework_prep["action_id"],
                                "claim_token": rework_prep["claim_token"],
                                "expected_revision": rework_prep["expected_revision"],
                                "outcome": "confirmed",
                                "agent_id": author_agent_id,
                                "raw_response": {
                                    "rework_dispatched": True,
                                    "thread_id": author_tid,
                                    "attempt": next_attempt,
                                },
                            },
                        )
                        current_msg_id = rework_prep["message_id"]
                        step = rework_prep["step"]
                        metric["reworks"] = step["attempt"]
                        metric["state"] = step["state"]

                    if step["state"] in {"dispatched", "implementing"}:
                        assert author_tid is not None and ws_path is not None
                        author_agent_id = format_appserver_agent_id(author_tid)
                        metric["author_thread_id"] = author_tid

                        if current_msg_id is None:
                            open_msg = await self._find_open_author_message(
                                run_id, owner_name, step_id
                            )
                            if open_msg is None:
                                raise PipelineError(
                                    f"No open task/rework inbox message found for active step {step_id}"
                                )
                            current_msg_id = open_msg["id"]

                        if step["state"] == "dispatched" or (
                            step["state"] == "implementing" and step["attempt"] >= 1
                        ):
                            open_msg_obj = await self._rpc.call(
                                "inbox_get",
                                {"message_id": current_msg_id},
                            )
                            if open_msg_obj is not None and open_msg_obj.get("status") == "pending":
                                accepted = await self._rpc.call(
                                    "workflow_author_turn",
                                    {
                                        "run_id": run_id,
                                        "step_id": step_id,
                                        "message_id": current_msg_id,
                                        "phase": "accept",
                                        "author": owner_name,
                                        "summary": f"Accepted attempt {step['attempt']} on {author_agent_id}",
                                    },
                                )
                                step = accepted["step"]
                                metric["state"] = step["state"]

                        await self._rpc.call(
                            "worker_status_update",
                            {
                                "run_id": run_id,
                                "worker_name": owner_name,
                                "status": "working",
                                "current_task": f"{step_cfg['title']} (attempt {step['attempt']})",
                            },
                        )

                        author_cwd = _effective_workspace_project_dir(ws_path, project_root)
                        turn_prompt = self._build_author_prompt(
                            spec=spec,
                            step_cfg=step_cfg,
                            ws_path=author_cwd,
                            rework_feedback=rework_feedback if step["attempt"] > 0 else None,
                            attempt=step["attempt"],
                        )
                        author_turn = await self._run_turn_with_control_watch(
                            run_id=run_id,
                            step_id=step_id,
                            worker_name=owner_name,
                            thread_id=author_tid,
                            agent_id=author_agent_id,
                            prompt=turn_prompt,
                            cwd=author_cwd,
                            role="author",
                            model=spec["model"],
                            effort="medium",
                            allow_network=spec["allow_network"],
                        )
                        if author_turn is None:
                            return False
                        metric["turns"] += 1
                        metric["attempts"] = step["attempt"] + 1

                        if author_turn.get("needs_clarification"):
                            await self._record_observation(
                                run_id=run_id,
                                step_id=step_id,
                                agent_id=author_agent_id,
                                state="idle",
                                evidence="Author paused due to requestUserInput interception",
                            )
                            await self._rpc.call(
                                "worker_status_update",
                                {
                                    "run_id": run_id,
                                    "worker_name": owner_name,
                                    "status": "waiting",
                                    "current_task": "Waiting for human clarification (requestUserInput intercepted)",
                                },
                            )
                            await self._rpc.call(
                                "board_post",
                                {
                                    "run_id": run_id,
                                    "sender": owner_name,
                                    "sender_role": "coder",
                                    "msg_type": "alert",
                                    "mentions": ["coordinator"],
                                    "summary": f"{step_cfg['step_key']} paused: clarification required",
                                    "body_md": json.dumps(
                                        author_turn.get("clarification_requests", []),
                                        ensure_ascii=False,
                                        indent=2,
                                    ),
                                },
                            )
                            self._halted_reason = "needs_clarification"
                            self._halt_detail = (
                                f"Step {step_cfg['step_key']} requested user input; paused for human clarification"
                            )
                            self._abort_event.set()
                            return False

                        await asyncio.to_thread(
                            _commit_workspace_changes_if_dirty,
                            ws_path,
                            step_cfg["step_key"],
                            step["attempt"],
                        )

                        await self._record_observation(
                            run_id=run_id,
                            step_id=step_id,
                            agent_id=author_agent_id,
                            state="idle",
                            evidence=f"Finished author turn {author_turn.get('turn_id')} (attempt {step['attempt']})",
                        )
                        await self._rpc.call(
                            "worker_status_update",
                            {
                                "run_id": run_id,
                                "worker_name": owner_name,
                                "status": "waiting",
                                "current_task": f"Submitted attempt {step['attempt']} for candidate review",
                            },
                        )

                        submitted = await self._rpc.call(
                            "workflow_author_turn",
                            {
                                "run_id": run_id,
                                "step_id": step_id,
                                "message_id": current_msg_id,
                                "phase": "submit",
                                "author": owner_name,
                                "summary": (author_turn.get("final_message") or "Candidate submitted")[:240],
                                "post_board_note": True,
                            },
                        )
                        current_msg_id = None
                        step = submitted["step"]
                        metric["state"] = step["state"]

                    if step["state"] == "candidate_review":
                        assert ws_id is not None and ws_path is not None
                        ws_diff = await self._rpc.call("workspace_diff", {"workspace_id": ws_id})
                        cand_gate = await self._run_verification_gate(
                            run_id=run_id,
                            mode=spec["mode"],
                            spec=spec,
                            step_cfg=step_cfg,
                            step_id=step_id,
                            scope="candidate",
                            target_root=ws_path,
                            project_root=project_root,
                            verifier_tid=verifier_tid,
                            verifier_agent_id=(
                                format_appserver_agent_id(verifier_tid) if verifier_tid else None
                            ),
                            capture_paths=None,
                            diff_text=str(ws_diff.get("diff") or ""),
                            include_integration_commands=False,
                            author_tid=author_tid,
                        )
                        if cand_gate is None:
                            return False

                        verifier_tid = str(cand_gate["verifier_tid"])
                        step = cand_gate["step"]
                        metric["state"] = step["state"]

                        if cand_gate["verdict"] == "FAIL":
                            failed_items = [
                                c["criterion"]
                                for c in cand_gate["criteria_results"]
                                if c["status"] != "PASS"
                            ]
                            rework_feedback = "\n".join(
                                [
                                    f"Verifier Summary: {cand_gate['summary']}",
                                    f"Failed Criteria: {failed_items}",
                                    "Verifier Findings:",
                                    *[f"- {f}" for f in cand_gate["findings"]],
                                    "Command Receipt Excerpt:",
                                    cand_gate["receipt_summary"],
                                ]
                            )
                            if step["state"] == "needs_decision":
                                self._halted_reason = "needs_decision"
                                self._halt_detail = (
                                    f"Step {step_cfg['step_key']} reached needs_decision after "
                                    f"{step['attempt'] + 1} failed candidate deliveries: {cand_gate['summary']}"
                                )
                                self._abort_event.set()
                                return False
                            continue

                return step["state"] in {"ready_to_merge", "integrated_review", "done"}
            finally:
                self._active_workers -= 1

    async def _integrate_wave(
        self,
        *,
        run_id: str,
        spec: Mapping[str, Any],
        wave_keys: Sequence[str],
        prior_done_keys: set[str],
        step_id_by_key: Mapping[str, str],
        project_root: Path,
        resumed_threads: dict[str, str],
        is_final_wave: bool = False,
    ) -> bool:
        """Merge all wave candidates into ``project_root``, then run stationary integrated gates."""
        assert self._rpc is not None and self._appserver is not None
        by_key = {s["step_key"]: s for s in spec["steps"]}
        merged_any = False
        merged_files_by_key: dict[str, list[str]] = {}

        async with self._merge_lock:
            for s_key in wave_keys:
                if self._abort_event.is_set():
                    return False
                step_cfg = by_key[s_key]
                step_id = step_id_by_key[s_key]
                metric = self._init_step_metric(step_cfg, step_id)
                step = await self._fetch_step(run_id, step_id)
                ws_id = step.get("workspace_id")

                if step["state"] == "ready_to_merge":
                    if ws_id:
                        preview = await self._rpc.call(
                            "workspace_merge_preview", {"workspace_id": ws_id}
                        )
                        if preview.get("has_conflicts"):
                            self._halted_reason = "merge_conflict"
                            self._halt_detail = (
                                f"Merge preview conflict on {s_key}: {preview.get('summary')}"
                            )
                            self._abort_event.set()
                            return False
                        merge_res = await self._rpc.call(
                            "workspace_merge", {"workspace_id": ws_id}
                        )
                        merged_files_by_key[s_key] = list(merge_res.get("files_changed") or [])
                        merged_any = True
                        step = await self._fetch_step(run_id, step_id)
                    else:
                        step = await self._rpc.call(
                            "workflow_step_advance",
                            {
                                "step_id": step_id,
                                "action": "begin_integrated_review",
                                "expected_revision": step["revision"],
                            },
                        )
                    metric["state"] = step["state"]

            # If new merges landed on top of previously completed waves, transition prior done steps
            # to integrated_review so every step's integrated snapshot reflects the stationary tree.
            ordered_to_verify: list[str] = []
            if merged_any and prior_done_keys:
                for s_cfg in spec["steps"]:
                    p_key = s_cfg["step_key"]
                    if p_key not in prior_done_keys:
                        continue
                    p_id = step_id_by_key[p_key]
                    p_step = await self._fetch_step(run_id, p_id)
                    if p_step["state"] == "done":
                        p_step = await self._rpc.call(
                            "workflow_step_advance",
                            {
                                "step_id": p_id,
                                "action": "reverify",
                                "expected_revision": p_step["revision"],
                            },
                        )
                        self._step_metrics[p_key]["state"] = p_step["state"]
                    if p_step["state"] == "integrated_review":
                        ordered_to_verify.append(p_key)

            for s_key in wave_keys:
                if s_key not in ordered_to_verify:
                    ordered_to_verify.append(s_key)

            for s_key in ordered_to_verify:
                if self._abort_event.is_set():
                    return False
                step_cfg = by_key[s_key]
                step_id = step_id_by_key[s_key]
                metric = self._init_step_metric(step_cfg, step_id)
                step = await self._fetch_step(run_id, step_id)
                if step["state"] == "done":
                    continue
                if step["state"] != "integrated_review":
                    self._halted_reason = step["state"]
                    self._halt_detail = f"Unexpected step state {step['state']!r} before integrated review on {s_key}"
                    self._abort_event.set()
                    return False

                verifier_name = step_cfg["verifier"]
                verifier_tid = (
                    resumed_threads.get(verifier_name)
                    or self._worker_threads.get(verifier_name)
                    or metric.get("verifier_thread_id")
                )
                if not verifier_tid and step.get("verifier_agent_id"):
                    verifier_tid = normalize_thread_id(step["verifier_agent_id"])

                merged_files = merged_files_by_key.get(s_key, [])
                run_top_int_cmds = bool(is_final_wave and s_key in wave_keys)
                int_gate = await self._run_verification_gate(
                    run_id=run_id,
                    mode=spec["mode"],
                    spec=spec,
                    step_cfg=step_cfg,
                    step_id=step_id,
                    scope="integrated",
                    target_root=project_root,
                    project_root=project_root,
                    verifier_tid=verifier_tid,
                    verifier_agent_id=(
                        format_appserver_agent_id(verifier_tid) if verifier_tid else None
                    ),
                    capture_paths=None,
                    diff_text=f"Merged files into main repository: {merged_files}",
                    include_integration_commands=run_top_int_cmds,
                )
                if int_gate is None:
                    return False
                step = int_gate["step"]
                metric["state"] = step["state"]

                if int_gate["verdict"] != "PASS":
                    self._halted_reason = step["state"]
                    self._halt_detail = (
                        f"Integrated verification failed on {s_key}: {int_gate['summary']}"
                    )
                    self._abort_event.set()
                    return False

                completed_step = await self._rpc.call(
                    "workflow_step_advance",
                    {
                        "step_id": step_id,
                        "action": "complete",
                        "expected_revision": step["revision"],
                    },
                )
                step = completed_step
                metric["state"] = step["state"]

                owner_aid = step.get("owner_agent_id") or ""
                if owner_aid:
                    completion_at = float(
                        step.get("checkpoint", {}).get("completion_at") or time.time()
                    )
                    await self._record_observation(
                        run_id=run_id,
                        step_id=step_id,
                        agent_id=owner_aid,
                        state="idle",
                        evidence=f"Step {s_key} integrated and completed",
                        min_after=completion_at,
                    )

        return True

    async def _cleanup_merged_workspaces(self, run_id: str) -> int:
        if self._rpc is None:
            return 0
        b_stat = await self._rpc.call(
            "board_status",
            {"run_id": run_id, "compact": True, "include_workspaces": True},
        )
        remaining_merged = 0
        for ws in b_stat.get("workspaces", []):
            if ws.get("status") == "merged":
                try:
                    await self._rpc.call("workspace_cleanup", {"workspace_id": ws["id"]})
                except PipelineRpcError:
                    remaining_merged += 1
        return remaining_merged

    async def _reverify_stale_steps_if_needed(
        self,
        *,
        run_id: str,
        spec: Mapping[str, Any],
        step_id_by_key: Mapping[str, str],
        project_root: Path,
    ) -> bool:
        """Finalize workspaces, workers, and run status='completed', reverifying stale steps if needed."""
        assert self._rpc is not None
        by_key = {s["step_key"]: s for s in spec["steps"]}

        for _ in range(len(spec["steps"]) + 1):
            try:
                remaining = await self._cleanup_merged_workspaces(run_id)
                if remaining > 0 and self._appserver is not None:
                    # Windows app-server child may still hold a handle on the last active workspace cwd;
                    # close it while run is still 'active' so workspace_cleanup succeeds cleanly.
                    await self._close_appserver()
                    await self._cleanup_merged_workspaces(run_id)
                await self._stop_idle_pipeline_workers(run_id)
                await self._rpc.call(
                    "run_update", {"run_id": run_id, "status": "completed"}
                )
                return True
            except PipelineRpcError as exc:
                if "stale" not in exc.detail.lower():
                    raise

                if self._appserver is None:
                    self._appserver = AppServerStdioClient(
                        codex_bin=self.codex_bin,
                        argv=self._resolve_appserver_argv_override(),
                        env=self._effective_appserver_env(),
                        cwd=project_root,
                    )
                    await self._appserver.start()
                    for l_tid, w_tid in self._thread_aliases.items():
                        self._appserver.register_thread_alias(l_tid, w_tid)

                reverified_any = False
                for step_cfg in spec["steps"]:
                    s_key = step_cfg["step_key"]
                    s_id = step_id_by_key[s_key]
                    st = await self._fetch_step(run_id, s_id)
                    if st["state"] != "done":
                        continue
                    v_aid = st.get("verifier_agent_id") or ""
                    if not v_aid:
                        continue
                    v_tid = normalize_thread_id(v_aid)
                    if v_tid not in self._appserver._threads:
                        try:
                            await self._appserver.resume_thread(
                                v_tid,
                                cwd=project_root,
                                role="verifier",
                                model=spec["model"],
                                effort="high",
                                allow_network=False,
                            )
                        except AppServerError:
                            pass
                    st = await self._rpc.call(
                        "workflow_step_advance",
                        {
                            "step_id": s_id,
                            "action": "reverify",
                            "expected_revision": st["revision"],
                        },
                    )
                    gate = await self._run_verification_gate(
                        run_id=run_id,
                        mode=spec["mode"],
                        spec=spec,
                        step_cfg=by_key[s_key],
                        step_id=s_id,
                        scope="integrated",
                        target_root=project_root,
                        project_root=project_root,
                        verifier_tid=v_tid,
                        verifier_agent_id=v_aid,
                        capture_paths=None,
                        diff_text="Final main-tree consistency reverification",
                        include_integration_commands=(s_key == spec["steps"][-1]["step_key"]),
                    )
                    if gate is None or gate["verdict"] != "PASS":
                        self._halted_reason = "reverify_failed"
                        self._halt_detail = f"Final reverification failed on {s_key}"
                        return False
                    st_after = gate["step"]
                    done_st = await self._rpc.call(
                        "workflow_step_advance",
                        {
                            "step_id": s_id,
                            "action": "complete",
                            "expected_revision": st_after["revision"],
                        },
                    )
                    owner_aid = done_st.get("owner_agent_id") or ""
                    if owner_aid:
                        await self._record_observation(
                            run_id=run_id,
                            step_id=s_id,
                            agent_id=owner_aid,
                            state="idle",
                            evidence=f"Step {s_key} reverified and completed",
                            min_after=float(done_st.get("checkpoint", {}).get("completion_at") or time.time()),
                        )
                    reverified_any = True
                if not reverified_any:
                    raise
        return False

    async def _run_dag(
        self,
        *,
        run_id: str,
        spec: Mapping[str, Any],
        step_id_by_key: Mapping[str, str],
        project_root: Path,
        resumed_threads: dict[str, str],
    ) -> bool:
        semaphore = asyncio.Semaphore(int(spec["max_workers"]))
        step_by_key = {s["step_key"]: s for s in spec["steps"]}
        done_keys: set[str] = set()

        # Pre-populate steps already in 'done' (critical for --resume-run)
        assert self._rpc is not None
        status_now = await self._rpc.call("board_status", {"run_id": run_id, "compact": True})
        for st in status_now["workflow"]["steps"]:
            s_key = st["step_key"]
            if s_key in step_by_key:
                m = self._init_step_metric(step_by_key[s_key], st["id"])
                m["state"] = st["state"]
                m["reworks"] = st["attempt"]
                if st.get("owner_agent_id"):
                    m["author_thread_id"] = normalize_thread_id(st["owner_agent_id"])
                if st.get("verifier_agent_id"):
                    m["verifier_thread_id"] = normalize_thread_id(st["verifier_agent_id"])
                if st["state"] == "done":
                    done_keys.add(s_key)

        remaining_keys = [
            s["step_key"] for s in spec["steps"] if s["step_key"] not in done_keys
        ]

        while remaining_keys and not self._abort_event.is_set():
            wave_keys = [
                s_key
                for s_key in remaining_keys
                if all(dep in done_keys for dep in step_by_key[s_key]["dependencies"])
            ]
            if not wave_keys:
                self._halted_reason = self._halted_reason or "dependency_blocked"
                return False

            candidate_tasks = {
                s_key: asyncio.create_task(
                    self._execute_single_step(
                        run_id=run_id,
                        spec=spec,
                        step_cfg=step_by_key[s_key],
                        step_id=step_id_by_key[s_key],
                        project_root=project_root,
                        semaphore=semaphore,
                        resumed_threads=resumed_threads,
                    ),
                    name=f"pipeline-cand-{s_key}",
                )
                for s_key in wave_keys
            }
            results = await asyncio.gather(*candidate_tasks.values())
            if not all(results) or self._abort_event.is_set():
                return False

            is_final_wave = set(wave_keys) == set(remaining_keys)
            integrated_ok = await self._integrate_wave(
                run_id=run_id,
                spec=spec,
                wave_keys=wave_keys,
                prior_done_keys=set(done_keys),
                step_id_by_key=step_id_by_key,
                project_root=project_root,
                resumed_threads=resumed_threads,
                is_final_wave=is_final_wave,
            )
            if not integrated_ok or self._abort_event.is_set():
                return False

            for s_key in wave_keys:
                done_keys.add(s_key)
                remaining_keys.remove(s_key)

        if self._abort_event.is_set() or len(done_keys) != len(spec["steps"]):
            return False
        return True

    async def _create_fresh_run(self, spec: Mapping[str, Any]) -> tuple[str, dict[str, str]]:
        assert self._rpc is not None
        project_root = Path(spec["project_root"]).resolve()

        run = await self._rpc.call(
            "run_create",
            {
                "mode": spec["mode"],
                "title": spec["title"],
                "project_root": str(project_root),
                "metadata": {
                    "runner": RUNNER_NAME,
                    "software_version": __version__,
                    "pipeline_spec": dict(spec),
                },
            },
        )
        run_id = str(run["id"])

        ownership_map: dict[str, list[str]] = {}
        all_criteria: list[str] = []
        seen_crit: set[str] = set()
        for step_cfg in spec["steps"]:
            ownership_map[step_cfg["owner"]] = list(step_cfg["file_ownership"])
            for crit in step_cfg["acceptance_criteria"]:
                if crit not in seen_crit:
                    seen_crit.add(crit)
                    all_criteria.append(crit)

        await self._rpc.call(
            "task_packet_save",
            {
                "run_id": run_id,
                "title": spec["title"],
                "goal": spec["objective"],
                "file_ownership": ownership_map,
                "acceptance_criteria": all_criteria,
            },
        )

        registered_workers: set[str] = set()
        for step_cfg in spec["steps"]:
            if step_cfg["owner"] not in registered_workers:
                await self._rpc.call(
                    "worker_register",
                    {
                        "run_id": run_id,
                        "name": step_cfg["owner"],
                        "role": "swarm_worker" if spec["mode"] == "swarm" else "coder",
                        "status": "waiting",
                        "current_task": step_cfg["title"],
                    },
                )
                registered_workers.add(step_cfg["owner"])
            if step_cfg["verifier"] not in registered_workers:
                await self._rpc.call(
                    "worker_register",
                    {
                        "run_id": run_id,
                        "name": step_cfg["verifier"],
                        "role": "verifier",
                        "status": "waiting",
                        "current_task": f"Verify {step_cfg['title']}",
                    },
                )
                registered_workers.add(step_cfg["verifier"])

        step_id_by_key: dict[str, str] = {}
        for step_cfg in spec["steps"]:
            dep_ids = [step_id_by_key[d] for d in step_cfg["dependencies"]]
            saved = await self._rpc.call(
                "workflow_step_save",
                {
                    "run_id": run_id,
                    "step_key": step_cfg["step_key"],
                    "owner": step_cfg["owner"],
                    "verifier": step_cfg["verifier"],
                    "dependencies": dep_ids,
                    "kind": step_cfg["kind"],
                    "criteria": list(step_cfg["acceptance_criteria"]),
                    "complexity": step_cfg["complexity"],
                    "execution_kind": EXECUTION_KIND,
                },
            )
            step_id_by_key[step_cfg["step_key"]] = str(saved["id"])
            self._init_step_metric(step_cfg, str(saved["id"]))

        b_status = await self._rpc.call("board_status", {"run_id": run_id, "compact": True})
        readiness = b_status.get("plan_readiness") or {}
        if not readiness.get("ready"):
            raise PipelineSpecError(f"Plan readiness gate rejected spec: {readiness}")

        return run_id, step_id_by_key

    async def _prepare_resume_run(
        self,
        run_id: str,
        explicit_spec: Mapping[str, Any] | None,
    ) -> tuple[dict[str, Any], dict[str, str], dict[str, str], bool]:
        """Inspect persisted run, validate thread health via ``thread/read``, and ``thread/resume``."""
        os.environ.setdefault("PYTHONUTF8", "1")
        os.environ.setdefault("PYTHONIOENCODING", "utf-8")
        if not self.explicit_daemon_url:
            await asyncio.to_thread(ensure_daemon_running, self.settings)
        self._rpc = _DaemonRpcClient(self.settings.base_url)
        await self._rpc.check_health()

        snapshot = await self._rpc.get_run_snapshot(run_id)
        run = snapshot["run"]
        run_metadata = dict(run.get("metadata") or {})
        stored_spec = run_metadata.get("pipeline_spec")
        raw_spec = stored_spec if stored_spec is not None else explicit_spec
        if raw_spec is None:
            raise PipelineSpecError(
                f"Run {run_id} has no stored metadata.pipeline_spec; supply --spec explicitly"
            )
        spec = validate_pipeline_spec(raw_spec)
        project_root = Path(spec["project_root"]).resolve()

        if run["status"] == "completed":
            step_map = {
                s["step_key"]: str(s["id"])
                for s in snapshot["workflow"]["steps"]
            }
            return spec, step_map, {}, True

        if run["status"] != "active":
            raise PipelineError(f"Cannot resume non-active run {run_id} (status={run['status']!r})")

        pending_ctls = [
            c for c in snapshot.get("control_requests", []) if c.get("status") == "pending"
        ]
        if pending_ctls:
            raise PipelineError(
                f"Run {run_id} has unacknowledged pending control_requests: {[c['id'] for c in pending_ctls]}"
            )
        native_proj = snapshot.get("native_actions") or {}
        reconcile_count = int(
            native_proj.get(
                "needs_reconciliation_count",
                native_proj.get("reconciliation_count", 0),
            )
            or 0
        )
        if reconcile_count:
            raise PipelineError(
                f"Run {run_id} has {reconcile_count} native action(s) requiring reconciliation before resume"
            )

        self._appserver = AppServerStdioClient(
            codex_bin=self.codex_bin,
            argv=self._resolve_appserver_argv_override(),
            env=self._effective_appserver_env(),
            cwd=project_root,
        )
        await self._appserver.start()

        persisted_aliases = run_metadata.get("pipeline_thread_aliases")
        aliases_dirty = False
        if isinstance(persisted_aliases, Mapping):
            for l_tid, w_tid in persisted_aliases.items():
                if isinstance(l_tid, str) and isinstance(w_tid, str) and l_tid.strip() and w_tid.strip():
                    norm_l = normalize_thread_id(l_tid)
                    norm_w = normalize_thread_id(w_tid)
                    self._thread_aliases[norm_l] = norm_w
                    self._appserver.register_thread_alias(norm_l, norm_w)

        workers_by_name = {w["name"]: w for w in snapshot.get("workers", [])}
        ws_by_id = {ws["id"]: ws for ws in snapshot.get("workspaces", [])}
        steps_by_key = {s["step_key"]: s for s in snapshot["workflow"]["steps"]}

        step_id_by_key: dict[str, str] = {}
        resumed_threads: dict[str, str] = {}

        async def _rehydrate_zero_turn(
            logical_tid: str, role_tag: str, role_cwd: Path
        ) -> None:
            nonlocal aliases_dirty
            assert self._appserver is not None
            if role_tag == "verifier":
                fresh = await self._appserver.start_verifier_thread(
                    role_cwd,
                    model=spec["model"],
                    effort="high",
                )
            else:
                fresh = await self._appserver.start_author_thread(
                    role_cwd,
                    model=spec["model"],
                    effort="medium",
                    allow_network=spec["allow_network"],
                )
            new_wire_tid = str(fresh["thread_id"])
            self._thread_aliases[logical_tid] = new_wire_tid
            self._appserver.register_thread_alias(logical_tid, new_wire_tid)
            aliases_dirty = True

        for step_cfg in spec["steps"]:
            s_key = step_cfg["step_key"]
            st = steps_by_key.get(s_key)
            if st is None:
                raise PipelineError(f"Persisted run {run_id} is missing workflow step {s_key!r}")
            step_id_by_key[s_key] = str(st["id"])
            metric = self._init_step_metric(step_cfg, str(st["id"]))
            metric["state"] = st["state"]
            metric["reworks"] = st["attempt"]
            metric["workspace_id"] = st.get("workspace_id")

            if st["state"] == "done":
                if st.get("owner_agent_id"):
                    o_tid = normalize_thread_id(st["owner_agent_id"])
                    metric["author_thread_id"] = o_tid
                    self._worker_threads.setdefault(step_cfg["owner"], o_tid)
                if st.get("verifier_agent_id"):
                    v_tid = normalize_thread_id(st["verifier_agent_id"])
                    metric["verifier_thread_id"] = v_tid
                    self._worker_threads.setdefault(step_cfg["verifier"], v_tid)
                continue

            if st["state"] in {"needs_decision", "reconciliation_required"}:
                self._halted_reason = st["state"]
                self._halt_detail = f"Step {s_key} is halted in {st['state']}"
                self._abort_event.set()
                return spec, step_id_by_key, resumed_threads, False

            ws_obj = ws_by_id.get(st["workspace_id"]) if st.get("workspace_id") else None
            raw_ws_cwd = (
                Path(ws_obj["path"]).resolve()
                if ws_obj and Path(ws_obj["path"]).exists()
                else project_root
            )
            effective_step_cwd = _effective_workspace_project_dir(raw_ws_cwd, project_root)
            verifier_cwd = (
                project_root
                if st["state"] == "integrated_review"
                else effective_step_cwd
            )

            for role_tag, worker_name, raw_aid, role_cwd in (
                ("author", step_cfg["owner"], st.get("owner_agent_id") or "", effective_step_cwd),
                ("verifier", step_cfg["verifier"], st.get("verifier_agent_id") or "", verifier_cwd),
            ):
                w_row = workers_by_name.get(worker_name)
                if w_row and w_row.get("status") in {"stopped", "killed"}:
                    self._halted_reason = "needs_decision"
                    self._halt_detail = (
                        f"Worker {worker_name} is {w_row['status']}; refusing to resume stopped/killed member"
                    )
                    self._abort_event.set()
                    return spec, step_id_by_key, resumed_threads, False

                if not raw_aid or worker_name in resumed_threads:
                    continue
                tid = normalize_thread_id(raw_aid)
                rehydrated_zero_turn = False
                try:
                    read_info = await self._appserver.read_thread(tid)
                except AppServerError as exc:
                    if _is_zero_turn_rollout_rpc_error(exc) and _can_rehydrate_zero_turn_binding(
                        st, role_tag, raw_aid
                    ):
                        await _rehydrate_zero_turn(tid, role_tag, role_cwd)
                        rehydrated_zero_turn = True
                        read_info = None
                    else:
                        self._halted_reason = "needs_decision"
                        self._halt_detail = (
                            f"Bound {role_tag} thread {raw_aid} failed thread/read ({exc}); "
                            "refusing to replace original binding"
                        )
                        self._abort_event.set()
                        return spec, step_id_by_key, resumed_threads, False

                if not rehydrated_zero_turn:
                    assert read_info is not None
                    if read_info.get("status") == "systemError":
                        try:
                            await self._record_observation(
                                run_id=run_id,
                                step_id=str(st["id"]),
                                agent_id=raw_aid,
                                state="ended",
                                evidence=f"thread/read reported systemError for {raw_aid}",
                            )
                        except PipelineRpcError:
                            pass
                        self._halted_reason = "needs_decision"
                        self._halt_detail = (
                            f"Bound {role_tag} thread {raw_aid} is in systemError; "
                            "halted at needs_decision without replacing original binding"
                        )
                        self._abort_event.set()
                        return spec, step_id_by_key, resumed_threads, False

                    try:
                        await self._appserver.resume_thread(
                            tid,
                            cwd=role_cwd,
                            role=role_tag,
                            model=spec["model"],
                            effort="high" if role_tag == "verifier" else "medium",
                            allow_network=False if role_tag == "verifier" else spec["allow_network"],
                        )
                    except AppServerError as exc:
                        if _is_zero_turn_rollout_rpc_error(exc) and _can_rehydrate_zero_turn_binding(
                            st, role_tag, raw_aid
                        ):
                            await _rehydrate_zero_turn(tid, role_tag, role_cwd)
                        else:
                            self._halted_reason = "needs_decision"
                            self._halt_detail = (
                                f"Bound {role_tag} thread {raw_aid} failed thread/resume ({exc}); "
                                "refusing to replace original binding"
                            )
                            self._abort_event.set()
                            return spec, step_id_by_key, resumed_threads, False

                resumed_threads[worker_name] = tid
                self._worker_threads[worker_name] = tid
                if role_tag == "author":
                    metric["author_thread_id"] = tid
                else:
                    metric["verifier_thread_id"] = tid

        if aliases_dirty:
            updated_meta = {
                **run_metadata,
                "pipeline_thread_aliases": dict(self._thread_aliases),
            }
            await self._rpc.call(
                "run_update",
                {"run_id": run_id, "metadata": updated_meta},
            )

        return spec, step_id_by_key, resumed_threads, False

    async def execute(
        self,
        *,
        spec: Mapping[str, Any] | None = None,
        resume_run_id: str | None = None,
    ) -> tuple[int, dict[str, Any]]:
        started_wall = _now_iso()
        t0 = time.monotonic()
        run_id: str | None = resume_run_id
        validated_spec: dict[str, Any] | None = (
            validate_pipeline_spec(spec) if spec is not None else None
        )
        exit_code = 1
        final_status = "failed"
        interceptions_list: list[dict[str, Any]] = []
        interception_counts: dict[str, int] = {}

        try:
            if resume_run_id:
                (
                    validated_spec,
                    step_id_by_key,
                    resumed_threads,
                    already_completed,
                ) = await self._prepare_resume_run(resume_run_id, validated_spec)
                project_root = Path(validated_spec["project_root"]).resolve()
                if already_completed:
                    exit_code = 0
                    final_status = "completed"
                elif not self._abort_event.is_set():
                    ok = await self._run_dag(
                        run_id=resume_run_id,
                        spec=validated_spec,
                        step_id_by_key=step_id_by_key,
                        project_root=project_root,
                        resumed_threads=resumed_threads,
                    )
                    if ok:
                        done_ok = await self._reverify_stale_steps_if_needed(
                            run_id=resume_run_id,
                            spec=validated_spec,
                            step_id_by_key=step_id_by_key,
                            project_root=project_root,
                        )
                        if done_ok:
                            await self._stop_idle_pipeline_workers(resume_run_id)
                            exit_code = 0
                            final_status = "completed"
                        else:
                            final_status = self._halted_reason or "failed"
                    else:
                        final_status = self._halted_reason or "failed"
                else:
                    final_status = self._halted_reason or "needs_decision"
            else:
                if validated_spec is None:
                    raise PipelineSpecError("Either --spec or --resume-run must be provided")
                project_root = Path(validated_spec["project_root"]).resolve()
                await self._ensure_daemon_and_clients(project_root)
                run_id, step_id_by_key = await self._create_fresh_run(validated_spec)

                ok = await self._run_dag(
                    run_id=run_id,
                    spec=validated_spec,
                    step_id_by_key=step_id_by_key,
                    project_root=project_root,
                    resumed_threads={},
                )
                if ok:
                    done_ok = await self._reverify_stale_steps_if_needed(
                        run_id=run_id,
                        spec=validated_spec,
                        step_id_by_key=step_id_by_key,
                        project_root=project_root,
                    )
                    if done_ok:
                        await self._stop_idle_pipeline_workers(run_id)
                        exit_code = 0
                        final_status = "completed"
                    else:
                        final_status = self._halted_reason or "failed"
                else:
                    final_status = self._halted_reason or "failed"
        except BaseException as exc:
            if not self._halt_detail:
                self._halt_detail = f"{type(exc).__name__}: {exc}"
            if isinstance(exc, PipelineSpecError):
                final_status = "invalid_spec"
            else:
                final_status = self._halted_reason or "failed"
            exit_code = 2 if isinstance(exc, PipelineSpecError) else 1
        finally:
            # Close app-server FIRST so any Windows directory handles on step workspaces are released
            # before the final merged-workspace cleanup pass.
            await self._close_appserver()
            interceptions_list = list(self._saved_interceptions)
            interception_counts = dict(self._saved_interception_counts)

            if self._rpc is not None and run_id is not None:
                try:
                    await self._cleanup_merged_workspaces(run_id)
                except Exception:
                    pass
                try:
                    b_final = await self._rpc.call(
                        "board_status", {"run_id": run_id, "compact": True}
                    )
                    for st in b_final.get("workflow", {}).get("steps", []):
                        s_key = st["step_key"]
                        if s_key in self._step_metrics:
                            self._step_metrics[s_key]["state"] = st["state"]
                            self._step_metrics[s_key]["reworks"] = st["attempt"]
                except Exception:
                    pass

            await self._close_clients()

        elapsed_sec = round(max(0.0, time.monotonic() - t0), 4)
        summary: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "runner": RUNNER_NAME,
            "software_version": __version__,
            "execution_kind": EXECUTION_KIND,
            "run_id": run_id,
            "resumed": bool(resume_run_id),
            "status": final_status,
            "exit_code": exit_code,
            "halted_reason": self._halted_reason,
            "error": self._halt_detail if exit_code != 0 else None,
            "mode": validated_spec["mode"] if validated_spec else None,
            "max_workers": validated_spec["max_workers"] if validated_spec else None,
            "peak_concurrency": self._peak_workers,
            "started_at": started_wall,
            "ended_at": _now_iso(),
            "elapsed_sec": elapsed_sec,
            "steps": [
                copy.deepcopy(self._step_metrics[s["step_key"]])
                for s in (validated_spec["steps"] if validated_spec else [])
                if s["step_key"] in self._step_metrics
            ],
            "receipt_paths": list(dict.fromkeys(self._all_receipt_paths)),
            "interceptions": {
                "total": len(interceptions_list),
                "by_method": interception_counts,
                "events": interceptions_list,
            },
            "acknowledged_controls": [c["id"] for c in self._acknowledged_controls],
        }

        target_out = self.output_path
        if target_out is None and run_id:
            target_out = (
                self.settings.data_dir / "pipeline-summaries" / f"{run_id}.json"
            ).resolve()
        if target_out is not None:
            sha_hex = _atomic_write_json(target_out, summary)
            summary["sha256"] = sha_hex
            summary["summary_path"] = str(target_out)
        else:
            summary["sha256"] = hashlib.sha256(_canonical_json_bytes(summary)).hexdigest()

        return exit_code, summary

    async def _stop_idle_pipeline_workers(self, run_id: str) -> None:
        if self._rpc is None:
            return
        try:
            b_stat = await self._rpc.call("board_status", {"run_id": run_id, "compact": True})
            for w in b_stat.get("workers", []):
                if w.get("status") in {"waiting", "working"}:
                    await self._rpc.call(
                        "worker_status_update",
                        {
                            "run_id": run_id,
                            "worker_name": w["name"],
                            "status": "stopped",
                            "current_task": "Pipeline completed",
                        },
                    )
        except Exception:
            pass


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tri-orchestrator-pipeline",
        description="Deterministic host-side codex app-server DAG pipeline runner (M11).",
    )
    parser.add_argument(
        "--spec",
        type=Path,
        default=None,
        help="Path to frozen pipeline_spec.json",
    )
    parser.add_argument(
        "--resume-run",
        dest="resume_run",
        default=None,
        help="Existing active run_id to resume using persisted metadata.pipeline_spec",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Path to atomically write pipeline_summary.json",
    )
    parser.add_argument(
        "--daemon-url",
        dest="daemon_url",
        default=None,
        help="Optional daemon URL override (e.g. http://127.0.0.1:18790)",
    )
    parser.add_argument(
        "--data-dir",
        dest="data_dir",
        type=Path,
        default=None,
        help="Optional TRI_ORCH_DATA_DIR override",
    )
    parser.add_argument(
        "--codex-bin",
        dest="codex_bin",
        default=None,
        help="Optional codex executable (or mock app-server script) path override",
    )
    parser.add_argument(
        "--poll-interval",
        dest="poll_interval",
        type=float,
        default=DEFAULT_POLL_INTERVAL_SEC,
        help=f"Control-request polling interval in seconds (<= {MAX_POLL_INTERVAL_SEC}s)",
    )
    return parser


async def async_run_cli(argv: Sequence[str] | None = None) -> tuple[int, dict[str, Any]]:
    parser = build_arg_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.spec is None and not args.resume_run:
        parser.error("one of --spec or --resume-run is required")

    raw_spec: dict[str, Any] | None = None
    if args.spec is not None:
        spec_path = args.spec.resolve()
        if not spec_path.is_file():
            summary_err = {
                "status": "invalid_spec",
                "exit_code": 2,
                "error": f"Spec file not found: {spec_path}",
            }
            if args.output is not None:
                _atomic_write_json(args.output.resolve(), summary_err)
            return 2, summary_err
        try:
            raw_spec = json.loads(spec_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            summary_err = {
                "status": "invalid_spec",
                "exit_code": 2,
                "error": f"Failed to parse spec JSON: {exc}",
            }
            if args.output is not None:
                _atomic_write_json(args.output.resolve(), summary_err)
            return 2, summary_err

    try:
        settings, explicit_url = _build_settings(
            daemon_url=args.daemon_url,
            data_dir=args.data_dir,
        )
    except PipelineSpecError as exc:
        summary_err = {"status": "invalid_spec", "exit_code": 2, "error": str(exc)}
        if args.output is not None:
            _atomic_write_json(args.output.resolve(), summary_err)
        return 2, summary_err

    runner = PipelineRunner(
        settings=settings,
        explicit_daemon_url=explicit_url,
        codex_bin=args.codex_bin,
        poll_interval_sec=args.poll_interval,
        output_path=args.output,
    )
    return await runner.execute(spec=raw_spec, resume_run_id=args.resume_run)


def main(argv: Sequence[str] | None = None) -> int:
    try:
        exit_code, summary = asyncio.run(async_run_cli(argv))
    except KeyboardInterrupt:
        print(json.dumps({"status": "cancelled", "exit_code": 130}, ensure_ascii=True))
        return 130
    compact_stdout = {
        "run_id": summary.get("run_id"),
        "status": summary.get("status"),
        "exit_code": exit_code,
        "halted_reason": summary.get("halted_reason"),
        "error": summary.get("error"),
        "elapsed_sec": summary.get("elapsed_sec"),
        "summary_path": summary.get("summary_path"),
        "sha256": summary.get("sha256"),
    }
    print(json.dumps(compact_stdout, ensure_ascii=False))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
