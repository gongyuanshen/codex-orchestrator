"""Storage footprint reporting and conservative garbage collection for tri-orchestrator."""

from __future__ import annotations

import argparse
import json
import logging
import os
import stat
import time
from pathlib import Path
from typing import Any

from orchestrator.config import Settings, get_settings
from orchestrator.db import Database, require_runtime_schema
from orchestrator.workspace_mgr import (
    WorkspaceCleanupGuardError,
    WorkspaceManager,
    _is_reparse_or_symlink,
)

logger = logging.getLogger(__name__)

_RECEIPT_SUBDIRS = ("receipts", "verification-receipts", "verification-manifests")


def _is_reparse_entry(entry: os.DirEntry[str]) -> bool:
    """Return True if a DirEntry is a symlink, Windows junction, or other reparse point."""
    try:
        if entry.is_symlink():
            return True
        if hasattr(entry, "is_junction") and entry.is_junction():
            return True
        reparse_attr = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
        if reparse_attr:
            st = entry.stat(follow_symlinks=False)
            if getattr(st, "st_file_attributes", 0) & reparse_attr:
                return True
    except OSError:
        return True
    return False


def _file_size_no_follow(path: Path) -> int:
    """Return file byte size using lstat without following symlinks/junctions."""
    if _is_reparse_or_symlink(path):
        return 0
    try:
        st = path.lstat()
    except OSError:
        return 0
    if stat.S_ISREG(st.st_mode):
        return int(st.st_size)
    return 0


def _dir_size_bytes(root: Path) -> int:
    """Compute total regular-file bytes inside root without following symlinks or junctions."""
    if _is_reparse_or_symlink(root):
        return 0
    try:
        st = root.lstat()
    except OSError:
        return 0
    if stat.S_ISREG(st.st_mode):
        return int(st.st_size)
    if not stat.S_ISDIR(st.st_mode):
        return 0

    total = 0
    stack: list[str] = [str(root)]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as it:
                for entry in it:
                    if _is_reparse_entry(entry):
                        continue
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(entry.path)
                        elif entry.is_file(follow_symlinks=False):
                            total += int(entry.stat(follow_symlinks=False).st_size)
                    except OSError:
                        continue
        except OSError:
            continue
    return total


def _is_runtime_log_name(name: str) -> bool:
    return (
        name == "daemon.log"
        or name.startswith("daemon.log.")
        or name == "shim.log"
        or name.startswith("shim.log.")
        or name.endswith(".log")
    )


def _collect_runtime_report(settings: Settings) -> dict[str, int]:
    db_path = settings.db_path
    state_db_bytes = sum(
        _file_size_no_follow(candidate)
        for candidate in (
            db_path,
            Path(f"{db_path}-wal"),
            Path(f"{db_path}-shm"),
            Path(f"{db_path}-journal"),
        )
    )

    logs_bytes = 0
    data_dir = settings.data_dir
    if data_dir.is_dir() and not _is_reparse_or_symlink(data_dir):
        try:
            with os.scandir(data_dir) as it:
                for entry in it:
                    if _is_reparse_entry(entry):
                        continue
                    try:
                        if entry.is_file(follow_symlinks=False) and _is_runtime_log_name(entry.name):
                            logs_bytes += int(entry.stat(follow_symlinks=False).st_size)
                    except OSError:
                        continue
        except OSError:
            pass
        logs_subdir = data_dir / "logs"
        if logs_subdir.is_dir() and not _is_reparse_or_symlink(logs_subdir):
            logs_bytes += _dir_size_bytes(logs_subdir)

    results_dir = settings.results_dir
    results_bytes = 0
    results_count = 0
    if results_dir.is_dir() and not _is_reparse_or_symlink(results_dir):
        results_bytes = _dir_size_bytes(results_dir)
        try:
            with os.scandir(results_dir) as it:
                for entry in it:
                    if _is_reparse_entry(entry):
                        continue
                    try:
                        if entry.is_file(follow_symlinks=False) and entry.name.endswith(".json"):
                            results_count += 1
                    except OSError:
                        continue
        except OSError:
            pass

    receipts_bytes = sum(
        _dir_size_bytes(data_dir / subdir)
        for subdir in _RECEIPT_SUBDIRS
    )

    runtime_total = state_db_bytes + logs_bytes + results_bytes + receipts_bytes
    return {
        "state_db_bytes": int(state_db_bytes),
        "logs_bytes": int(logs_bytes),
        "results_bytes": int(results_bytes),
        "results_count": int(results_count),
        "receipts_bytes": int(receipts_bytes),
        "total_bytes": int(runtime_total),
    }


def collect_storage_report(
    db: Database,
    settings: Settings,
    *,
    workspace_mgr: WorkspaceManager | None = None,
) -> dict[str, Any]:
    """Inspect runtime data_dir footprint and categorize all tracked workspaces."""
    runtime = _collect_runtime_report(settings)
    mgr = workspace_mgr or WorkspaceManager(db, settings)
    runs_by_id = {run["id"]: run for run in db.list_runs(limit=None)}

    items: list[dict[str, Any]] = []
    for ws in db.list_workspaces():
        ws_path = Path(ws["path"])
        is_reparse = _is_reparse_or_symlink(ws_path)
        exists = ws_path.exists() or is_reparse
        if ws["status"] == "cleaned" and not exists:
            continue

        ws_bytes = _dir_size_bytes(ws_path) if (exists and not is_reparse) else 0
        run = runs_by_id.get(ws["run_id"]) or db.get_run(ws["run_id"])
        run_status = run["status"] if run is not None else "unknown"

        if ws["status"] == "cleaned":
            category = "dirty_review"
            reason = "Workspace status is 'cleaned' in DB, but directory still exists on disk"
            added_count = 0
            modified_count = 0
        elif run_status == "active":
            category = "active"
            reason = f"Run is active (workspace status: {ws['status']})"
            added_count = 0
            modified_count = 0
        else:
            safety = mgr.workspace_gc_safety(ws["id"])
            added_count = int(safety["added_count"])
            modified_count = int(safety["modified_count"])
            discard = (
                mgr.workspace_discard_check(ws["id"])
                if ws["status"] != "merged" and not safety["safe_to_clean"]
                else None
            )
            if run_status in {"completed", "archived"} and safety["safe_to_clean"]:
                category = "cleanable"
                reason = str(safety["reason"])
            elif discard is not None and discard["discardable"]:
                # Never merged and identical to its baseline: safe to discard on explicit request.
                category = "discardable"
                reason = str(discard["reason"])
            else:
                category = "dirty_review"
                if run_status not in {"completed", "archived"}:
                    reason = (
                        f"Run status is {run_status!r} (must be 'completed' or 'archived'); "
                        f"{safety['reason']}"
                    )
                else:
                    reason = str(safety["reason"])

        items.append(
            {
                "workspace_id": ws["id"],
                "run_id": ws["run_id"],
                "run_status": run_status,
                "worker_name": ws["worker_name"],
                "mode": ws["mode"],
                "status": ws["status"],
                "path": ws["path"],
                "exists": bool(exists),
                "bytes": int(ws_bytes),
                "category": category,
                "reason": reason,
                "added_count": int(added_count),
                "modified_count": int(modified_count),
            }
        )

    ws_total_bytes = sum(item["bytes"] for item in items)
    cleanable_bytes = sum(item["bytes"] for item in items if item["category"] == "cleanable")
    cleanable_count = sum(1 for item in items if item["category"] == "cleanable")
    dirty_review_count = sum(1 for item in items if item["category"] == "dirty_review")
    discardable_count = sum(1 for item in items if item["category"] == "discardable")
    discardable_bytes = sum(item["bytes"] for item in items if item["category"] == "discardable")
    active_count = sum(1 for item in items if item["category"] == "active")

    return {
        "data_dir": str(settings.data_dir),
        "runtime": runtime,
        "workspaces": {
            "total_bytes": int(ws_total_bytes),
            "cleanable_bytes": int(cleanable_bytes),
            "cleanable_count": int(cleanable_count),
            "dirty_review_count": int(dirty_review_count),
            "discardable_count": int(discardable_count),
            "discardable_bytes": int(discardable_bytes),
            "active_count": int(active_count),
            "items": items,
        },
        "total_bytes": int(runtime["total_bytes"] + ws_total_bytes),
    }


def _collect_results_candidates(
    settings: Settings,
    *,
    current_now: float,
    dry_run: bool,
) -> tuple[list[str], int]:
    """Prune expired or excess results/*.json files without following symlinks."""
    results_dir = settings.results_dir
    if not results_dir.is_dir() or _is_reparse_or_symlink(results_dir):
        return [], 0

    entries: list[tuple[Path, float, int]] = []
    try:
        with os.scandir(results_dir) as it:
            for entry in it:
                if _is_reparse_entry(entry):
                    continue
                try:
                    if entry.is_file(follow_symlinks=False) and entry.name.endswith(".json"):
                        st = entry.stat(follow_symlinks=False)
                        entries.append((Path(entry.path), float(st.st_mtime), int(st.st_size)))
                except OSError:
                    continue
    except OSError:
        return [], 0

    entries.sort(key=lambda item: (item[1], item[0].name))
    ttl_sec = int(settings.result_ttl_sec)
    max_files = int(settings.result_max_files)

    cleaned: list[str] = []
    freed_bytes = 0
    kept = 0
    for path, mtime, size in reversed(entries):
        expired = (current_now - mtime) >= ttl_sec
        over_limit = kept >= max_files
        if expired or over_limit:
            if dry_run:
                cleaned.append(str(path))
                freed_bytes += size
            else:
                try:
                    path.unlink(missing_ok=True)
                    cleaned.append(str(path))
                    freed_bytes += size
                except OSError as exc:
                    logger.warning("Failed to remove overflow result file %s: %s", path, exc)
        else:
            kept += 1
    return cleaned, freed_bytes


def run_gc(
    db: Database,
    workspace_mgr: WorkspaceManager,
    settings: Settings,
    *,
    now: float | None = None,
    clean_results: bool = True,
    clean_workspaces: bool = True,
    include_all_completed: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Clean expired overflow results and safe, retention-eligible merged workspaces."""
    current_now = time.time() if now is None else float(now)
    freed_bytes = 0
    cleaned_results: list[str] = []
    cleaned_workspaces: list[str] = []
    skipped_dirty_workspaces: list[dict[str, Any]] = []

    if clean_results:
        cleaned_results, result_freed = _collect_results_candidates(
            settings,
            current_now=current_now,
            dry_run=dry_run,
        )
        freed_bytes += result_freed

    if clean_workspaces:
        runs_by_id = {run["id"]: run for run in db.list_runs(limit=None)}
        retention_sec = int(settings.workspace_retention_sec)

        for ws in db.list_workspaces():
            ws_path = Path(ws["path"])
            is_reparse = _is_reparse_or_symlink(ws_path)
            exists = ws_path.exists() or is_reparse
            if ws["status"] == "cleaned" and not exists:
                continue

            run = runs_by_id.get(ws["run_id"]) or db.get_run(ws["run_id"])
            run_status = run["status"] if run is not None else "unknown"
            if run_status == "active":
                continue

            ws_bytes = _dir_size_bytes(ws_path) if (exists and not is_reparse) else 0
            if ws["status"] == "cleaned":
                skipped_dirty_workspaces.append(
                    {
                        "workspace_id": ws["id"],
                        "run_id": ws["run_id"],
                        "run_status": run_status,
                        "worker_name": ws["worker_name"],
                        "mode": ws["mode"],
                        "status": ws["status"],
                        "path": ws["path"],
                        "bytes": int(ws_bytes),
                        "reason": "Workspace status is 'cleaned' in DB, but directory still exists on disk",
                        "added_count": 0,
                        "modified_count": 0,
                        "added_files": [],
                        "modified_files": [],
                    }
                )
                continue

            safety = workspace_mgr.workspace_gc_safety(ws["id"])
            if run_status not in {"completed", "archived"} or not safety["safe_to_clean"]:
                reason = (
                    f"Run status is {run_status!r} (must be 'completed' or 'archived'); {safety['reason']}"
                    if run_status not in {"completed", "archived"}
                    else str(safety["reason"])
                )
                skipped_dirty_workspaces.append(
                    {
                        "workspace_id": ws["id"],
                        "run_id": ws["run_id"],
                        "run_status": run_status,
                        "worker_name": ws["worker_name"],
                        "mode": ws["mode"],
                        "status": ws["status"],
                        "path": ws["path"],
                        "bytes": int(ws_bytes),
                        "reason": reason,
                        "added_count": int(safety["added_count"]),
                        "modified_count": int(safety["modified_count"]),
                        "added_files": list(safety["added_files"]),
                        "modified_files": list(safety["modified_files"]),
                    }
                )
                continue

            ref_ts = max(
                float((run or {}).get("updated_at") or 0.0),
                float(ws.get("updated_at") or 0.0),
            )
            if ref_ts <= 0.0:
                ref_ts = float(ws.get("created_at") or 0.0)
            age = current_now - ref_ts
            if not include_all_completed and age < retention_sec:
                continue

            if dry_run:
                cleaned_workspaces.append(ws["id"])
                freed_bytes += ws_bytes
                continue

            try:
                workspace_mgr.workspace_gc_cleanup(ws["id"])
            except WorkspaceCleanupGuardError as exc:
                latest_safety = workspace_mgr.workspace_gc_safety(ws["id"])
                skipped_dirty_workspaces.append(
                    {
                        "workspace_id": ws["id"],
                        "run_id": ws["run_id"],
                        "run_status": run_status,
                        "worker_name": ws["worker_name"],
                        "mode": ws["mode"],
                        "status": ws["status"],
                        "path": ws["path"],
                        "bytes": int(ws_bytes),
                        "reason": str(exc),
                        "added_count": int(latest_safety["added_count"]),
                        "modified_count": int(latest_safety["modified_count"]),
                        "added_files": list(latest_safety["added_files"]),
                        "modified_files": list(latest_safety["modified_files"]),
                    }
                )
                continue

            cleaned_workspaces.append(ws["id"])
            freed_bytes += ws_bytes

    report_after = collect_storage_report(db, settings, workspace_mgr=workspace_mgr)
    return {
        "ok": True,
        "dry_run": bool(dry_run),
        "freed_bytes": int(freed_bytes),
        "cleaned_results": cleaned_results,
        "cleaned_workspaces": cleaned_workspaces,
        "skipped_dirty_workspaces": skipped_dirty_workspaces,
        "report_after": report_after,
    }


def _empty_dry_run_without_db(settings: Settings) -> dict[str, Any]:
    runtime = _collect_runtime_report(settings)
    cleaned_results, freed_bytes = _collect_results_candidates(
        settings,
        current_now=time.time(),
        dry_run=True,
    )
    return {
        "ok": True,
        "dry_run": True,
        "freed_bytes": int(freed_bytes),
        "cleaned_results": cleaned_results,
        "cleaned_workspaces": [],
        "skipped_dirty_workspaces": [],
        "report_after": {
            "data_dir": str(settings.data_dir),
            "runtime": runtime,
            "workspaces": {
                "total_bytes": 0,
                "cleanable_bytes": 0,
                "cleanable_count": 0,
                "dirty_review_count": 0,
                "active_count": 0,
                "items": [],
            },
            "total_bytes": int(runtime["total_bytes"]),
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="tri-orchestrator storage footprint inspector and safe garbage collector"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview reclaimable results and workspaces without deleting files or updating state.db",
    )
    parser.add_argument(
        "--all-completed",
        action="store_true",
        help="Include clean merged workspaces from completed/archived runs regardless of workspace_retention_sec",
    )
    parser.add_argument(
        "--data-dir",
        default=None,
        help="Runtime data directory override (defaults to TRI_ORCH_DATA_DIR or LOCALAPPDATA)",
    )
    args = parser.parse_args(argv)

    cfg = get_settings(data_dir=args.data_dir)
    if args.dry_run and not cfg.db_path.exists():
        result = _empty_dry_run_without_db(cfg)
    else:
        require_runtime_schema(cfg.db_path)
        with Database.from_settings(cfg) as db:
            ws_mgr = WorkspaceManager(db, cfg)
            result = run_gc(
                db,
                ws_mgr,
                cfg,
                include_all_completed=bool(args.all_completed),
                dry_run=bool(args.dry_run),
            )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
