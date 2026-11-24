"""Offline package upgrade helper; Codex owns registry activation and MCP reload.

Run with the repository Python. This command never stops/starts a service or
edits Codex configuration. A versioned managed cache is not renamed or relabeled.
"""
from __future__ import annotations

import argparse
import ast
from contextlib import closing, contextmanager
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import socket
import sqlite3
import stat
import subprocess
import sys
import time
import urllib.request
import uuid


class UpgradeError(RuntimeError):
    """The requested upgrade cannot proceed without losing its proof boundary."""


EXCLUDED = {".venv", "__pycache__", ".pytest_cache", ".git"}


def write_json(path: Path, value: dict) -> None:
    with path.open("x", encoding="utf-8") as output:
        json.dump(value, output, ensure_ascii=False, indent=2)
        output.flush()
        os.fsync(output.fileno())


def _write_json_replace(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".upgrade-" + uuid.uuid4().hex + ".tmp")
    try:
        with temp.open("x", encoding="utf-8") as output:
            json.dump(value, output, ensure_ascii=False, indent=2)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def _is_reparse_or_symlink(path: Path) -> bool:
    try:
        if path.is_symlink():
            return True
        if hasattr(path, "is_junction") and path.is_junction():
            return True
        st = os.lstat(path)
        attrs = getattr(st, "st_file_attributes", 0)
        return bool(attrs & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))
    except OSError:
        return False


def _dir_bytes(root: Path) -> int:
    if _is_reparse_or_symlink(root):
        return 0
    if not root.is_dir():
        try:
            return root.lstat().st_size
        except OSError:
            return 0
    total = 0
    with os.scandir(root) as it:
        for entry in it:
            ep = Path(entry.path)
            if _is_reparse_or_symlink(ep):
                continue
            if entry.is_dir(follow_symlinks=False):
                total += _dir_bytes(ep)
            else:
                try:
                    total += entry.stat(follow_symlinks=False).st_size
                except OSError:
                    pass
    return total


def _rmtree_safe(root: Path) -> int:
    """Recursively remove a directory tree without following symlinks or Windows junctions."""
    if _is_reparse_or_symlink(root):
        try:
            os.rmdir(root)
        except OSError:
            root.unlink(missing_ok=True)
        return 0
    if not root.exists():
        return 0
    if not root.is_dir():
        size = 0
        try:
            size = root.lstat().st_size
        except OSError:
            pass
        try:
            root.unlink()
        except PermissionError:
            os.chmod(root, stat.S_IWRITE | stat.S_IREAD)
            root.unlink()
        return size

    freed = 0
    with os.scandir(root) as it:
        for entry in it:
            ep = Path(entry.path)
            if _is_reparse_or_symlink(ep):
                try:
                    os.rmdir(ep)
                except OSError:
                    ep.unlink(missing_ok=True)
            elif entry.is_dir(follow_symlinks=False):
                freed += _rmtree_safe(ep)
            else:
                try:
                    freed += entry.stat(follow_symlinks=False).st_size
                except OSError:
                    pass
                try:
                    ep.unlink()
                except PermissionError:
                    os.chmod(ep, stat.S_IWRITE | stat.S_IREAD)
                    ep.unlink()
    try:
        os.rmdir(root)
    except PermissionError:
        os.chmod(root, stat.S_IWRITE | stat.S_IREAD | stat.S_IEXEC)
        os.rmdir(root)
    return freed


def _is_valid_plugin_dir(path: Path) -> bool:
    if _is_reparse_or_symlink(path) or not path.is_dir():
        return False
    if (path / ".codex-plugin" / "plugin.json").is_file():
        return True
    try:
        for child in path.iterdir():
            if not _is_reparse_or_symlink(child) and child.is_dir():
                if (child / ".codex-plugin" / "plugin.json").is_file():
                    return True
    except OSError:
        return False
    return False


def _infer_plugin_cache_dir(target_plugin: Path) -> tuple[Path | None, Path | None]:
    resolved = target_plugin.resolve()
    parent = resolved.parent
    grandparent = parent.parent
    if parent.name == "tri-orchestrator" and grandparent.name == "tri-orchestrator" and grandparent.parent.name == "cache":
        return grandparent, parent
    if parent.name == "tri-orchestrator" and grandparent.name == "cache":
        return parent, resolved
    return None, resolved


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def package_files(root: Path) -> dict[str, str]:
    files = {}
    for current, directories, names in os.walk(root, followlinks=False):
        directories[:] = sorted(d for d in directories if d not in EXCLUDED)
        for name in directories + sorted(names):
            p = Path(current) / name
            if p.is_symlink():
                raise UpgradeError(f"Package symlink requires explicit handling: {p}")
        for name in sorted(names):
            p = Path(current) / name
            if p.suffix != ".pyc":
                files[p.relative_to(root).as_posix()] = digest(p.read_bytes())
    return files


def package_identity(root: Path) -> dict:
    value = json.loads((root / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("name") != "tri-orchestrator" or not isinstance(value.get("version"), str):
        raise UpgradeError(f"Unexpected plugin identity: {root}")
    return {"name": value["name"], "version": value["version"]}


def supported_schema(root: Path) -> int:
    tree = ast.parse((root / "src" / "orchestrator" / "db.py").read_text(encoding="utf-8"))
    for statement in tree.body:
        if isinstance(statement, ast.Assign) and any(isinstance(n, ast.Name) and n.id == "SCHEMA_VERSION" for n in statement.targets):
            value = ast.literal_eval(statement.value)
            if type(value) is int and value > 0:
                return value
    raise UpgradeError("Package has no literal supported SCHEMA_VERSION")


def database_snapshot(path: Path) -> dict:
    if not path.is_file():
        raise UpgradeError(f"Existing database required: {path}")
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
        db.execute("BEGIN")
        check = db.execute("PRAGMA quick_check").fetchone()[0]
        foreign = db.execute("PRAGMA foreign_key_check").fetchall()
        if check != "ok" or foreign:
            raise UpgradeError(f"Database integrity failed: {check}; foreign keys={foreign}")
        version = db.execute("SELECT COALESCE(MAX(version),0) FROM schema_migrations").fetchone()[0]
        active = [list(r) for r in db.execute("SELECT id,mode FROM runs WHERE status='active' ORDER BY id")]
        tables = {}
        for (name,) in db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"):
            quoted = '"' + name.replace('"', '""') + '"'
            rows = db.execute(f"SELECT * FROM {quoted} ORDER BY rowid").fetchall()
            encoded = json.dumps(rows, ensure_ascii=False, separators=(",", ":"), default=lambda x: {"blob": x.hex()}).encode()
            columns = [list(r) for r in db.execute(f"PRAGMA table_info({quoted})")]
            tables[name] = {"count": len(rows), "sha256": digest(encoded), "columns": columns}
        return {"schema_version": version, "active_runs": active, "tables": tables}


def no_active(snapshot: dict) -> None:
    if snapshot["active_runs"]:
        raise UpgradeError(f"Finish all active tasks before upgrade: {snapshot['active_runs']}")


def require_old_rows(data: Path, before: dict) -> None:
    with closing(sqlite3.connect(data.resolve().as_uri() + "?mode=ro", uri=True)) as db:
        for name, table in before["tables"].items():
            quoted = '"' + name.replace('"', '""') + '"'
            columns = ",".join('"' + c[1].replace('"', '""') + '"' for c in table["columns"])
            rows = db.execute(f"SELECT {columns} FROM {quoted} ORDER BY rowid LIMIT ?", (table["count"],)).fetchall()
            encoded = json.dumps(rows, ensure_ascii=False, separators=(",", ":"), default=lambda x: {"blob": x.hex()}).encode()
            if digest(encoded) != table["sha256"]:
                raise UpgradeError(f"Migration changed preexisting rows in {name}")


def checked_path(root: Path, relative: str) -> Path:
    p = root / relative
    if Path(relative).is_absolute() or ".." in Path(relative).parts or not p.resolve().is_relative_to(root.resolve()):
        raise UpgradeError(f"Managed file escapes its package: {relative}")
    for ancestor in [p, *p.parents]:
        if ancestor == root.parent:
            break
        if ancestor.is_symlink():
            raise UpgradeError(f"Managed file follows a symlink: {p}")
    return p


def atomic_copy(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(target.name + ".upgrade-" + uuid.uuid4().hex + ".tmp")
    try:
        with source.open("rb") as src, temp.open("xb") as dst:
            shutil.copyfileobj(src, dst)
            dst.flush()
            os.fsync(dst.fileno())
        os.replace(temp, target)
    finally:
        temp.unlink(missing_ok=True)


def copy_package(source: Path, target: Path, inventory: dict) -> None:
    for name in inventory:
        atomic_copy(checked_path(source, name), checked_path(target, name))


def process_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes
        kernel = ctypes.windll.kernel32
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            if not kernel.GetExitCodeProcess(handle, ctypes.byref(code)):
                raise UpgradeError(f"Cannot inspect daemon PID {pid}")
            return code.value == 259
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


@contextmanager
def offline(data: Path, port: int):
    # Same mutex as the normal shim; reserve the port so startup cannot race apply.
    from orchestrator.mcp_shim import _acquire_daemon_lock
    with _acquire_daemon_lock(data / "daemon.lock", timeout_sec=2):
        pid_file = data / "daemon.pid.json"
        if pid_file.exists():
            owner = json.loads(pid_file.read_text(encoding="utf-8"))
            if process_alive(int(owner["pid"])):
                raise UpgradeError("Daemon must be stopped manually before package apply")
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as reservation:
            if os.name == "nt":
                reservation.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            try:
                reservation.bind(("127.0.0.1", port))
            except OSError as exc:
                raise UpgradeError(f"Daemon/listener must be stopped before apply: port {port}") from exc
            yield


def prepare(source_plugin: Path, target_plugin: Path, data_dir: Path, backup_dir: Path, *, port: int = 18790, keep_backups: int | None = None) -> dict:
    source, target, data, backup = (Path(p).resolve() for p in (source_plugin, target_plugin, data_dir, backup_dir))
    paths = [source, target, data, backup]
    if any(a == b or a.is_relative_to(b) or b.is_relative_to(a) for i, a in enumerate(paths) for b in paths[i + 1:]):
        raise UpgradeError("Source, target, runtime data and backup directories must not overlap")
    if type(port) is not int or not 1 <= port <= 65535:
        raise UpgradeError("Invalid loopback port")
    if keep_backups is not None and (type(keep_backups) is not int or keep_backups < 0):
        raise UpgradeError("keep_backups must be a non-negative integer")
    source_id, target_id = package_identity(source), package_identity(target)
    snapshot = database_snapshot(data / "state.db")
    no_active(snapshot)
    schema = supported_schema(source)
    if snapshot["schema_version"] > schema:
        raise UpgradeError("Database schema is newer than the staged package supports")
    if backup.exists():
        raise UpgradeError(f"Backup must be a new directory: {backup}")
    source_files, target_files = package_files(source), package_files(target)
    backup.mkdir(parents=True)
    copy_package(source, backup / "staged", source_files)
    copy_package(target, backup / "package-before", target_files)
    with closing(sqlite3.connect((data / "state.db").as_uri() + "?mode=ro", uri=True)) as src, closing(sqlite3.connect(backup / "state-before.db")) as dst:
        src.backup(dst)
    if (database_snapshot(backup / "state-before.db") != snapshot
            or database_snapshot(data / "state.db") != snapshot
            or package_files(backup / "staged") != source_files
            or package_files(backup / "package-before") != target_files):
        raise UpgradeError("Preparation inputs changed while taking backups; retain this failed backup and prepare again")
    manifest = {"status": "prepared", "created_at": time.time(), "target_plugin": str(target), "data_dir": str(data), "port": port,
                "source_identity": source_id, "target_identity": target_id, "supported_schema": schema,
                "source_files": source_files, "target_files": target_files, "database_before": snapshot,
                "activation": "Package operation only; Codex owns registry activation and manual MCP reload"}
    write_json(backup / "prepared.json", manifest)
    if keep_backups is not None:
        prune_report = prune(
            backup_root=backup.parent,
            keep_backups=keep_backups,
            active_plugin_dir=target,
            current_backup_dir=backup,
            protect_current=True,
        )
        return {**manifest, "pruned": prune_report}
    return manifest


def load_prepared(backup: Path) -> dict:
    return json.loads((backup / "prepared.json").read_text(encoding="utf-8"))


def apply(backup_dir: Path, *, database_only: bool = False) -> dict:
    backup = Path(backup_dir).resolve()
    manifest = load_prepared(backup)
    target, data = Path(manifest["target_plugin"]), Path(manifest["data_dir"])
    if (backup / "apply-started.json").exists():
        raise UpgradeError("Upgrade already started; inspect its checkpoint instead of replaying it")
    if (not database_only and re.fullmatch(r"\d+\.\d+\.\d+(?:[-+].+)?", target.name)
            and target.name != manifest["source_identity"]["version"]):
        raise UpgradeError("Versioned managed cache belongs to the old release; use apply --database-only, then install the new version through its host")
    staged = backup / "staged"
    state_before = backup / "state-before.db"
    candidate = backup / "state-candidate.db"
    before = manifest["database_before"]
    if package_files(staged) != manifest["source_files"]:
        raise UpgradeError("The staged package changed after preparation")
    if database_snapshot(state_before) != before:
        raise UpgradeError("The database backup changed after preparation")
    with offline(data, manifest["port"]):
        if package_files(target) != manifest["target_files"] or database_snapshot(data / "state.db") != before:
            raise UpgradeError("Package or database changed since preparation; prepare a new backup")
        no_active(before)
        write_json(backup / "apply-started.json", {"started_at": time.time(), "status": "applying_service_stopped"})
        try:
            # Prove the migration preserves every old row before touching the runtime database.
            atomic_copy(state_before, candidate)
            code = "import sys; sys.path.insert(0,sys.argv[1]); from orchestrator.db import Database; d=Database(sys.argv[2]); d.close()"
            result = subprocess.run([sys.executable, "-B", "-c", code, str(staged / "src"), str(candidate)],
                                    capture_output=True, text=True, encoding="utf-8", timeout=60,
                                    env=dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8"),
                                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            write_json(backup / "migration.json", {"returncode": result.returncode, "stdout": result.stdout, "stderr": result.stderr})
            if result.returncode:
                raise UpgradeError(f"Staged package migration failed: {result.stderr}")
            after = database_snapshot(candidate)
            if after["schema_version"] != manifest["supported_schema"]:
                raise UpgradeError("Migration did not reach the staged schema version")
            no_active(after)
            require_old_rows(candidate, before)
            if database_snapshot(data / "state.db") != before or package_files(target) != manifest["target_files"]:
                raise UpgradeError("Package or database changed during migration; retain the candidate and prepare a new backup")
            if not database_only:
                copy_package(staged, target, manifest["source_files"])
                for name, expected in manifest["source_files"].items():
                    if digest(checked_path(target, name).read_bytes()) != expected:
                        raise UpgradeError(f"Package readback mismatch: {name}")
            with closing(sqlite3.connect(candidate.as_uri() + "?mode=ro", uri=True)) as src, closing(sqlite3.connect(data / "state.db")) as dst:
                src.backup(dst)
            if database_snapshot(data / "state.db") != after:
                raise UpgradeError("Migrated database readback mismatch")
            candidate.unlink()
            report = {"status": "applied_service_stopped", "database_after": after, "package_after": package_files(target),
                      "database_only": database_only,
                      "next": "Activate/refresh this package through its owning host, then run verify; no service was started"}
            write_json(backup / "applied.json", report)
            return report
        except BaseException as exc:
            write_json(backup / "apply-failed.json", {"status": "reconciliation_required", "error": f"{type(exc).__name__}: {exc}"})
            raise


def rollback(backup_dir: Path) -> dict:
    backup = Path(backup_dir).resolve()
    manifest = load_prepared(backup)
    applied = json.loads((backup / "applied.json").read_text(encoding="utf-8"))
    target, data = Path(manifest["target_plugin"]), Path(manifest["data_dir"])
    # Older applied.json records always installed the staged package in place.
    restore_package = not applied.get("database_only", False)
    if (backup / "rolled-back.json").exists():
        raise UpgradeError("Rollback already completed")
    with offline(data, manifest["port"]):
        if database_snapshot(data / "state.db") != applied["database_after"]:
            raise UpgradeError("Database has new writes; preserve them and repair forward instead of restoring an old snapshot")
        if restore_package and package_files(target) != applied["package_after"]:
            raise UpgradeError("Package changed after apply; inspect before rollback")
        if (database_snapshot(backup / "state-before.db") != manifest["database_before"]
                or (restore_package and package_files(backup / "package-before") != manifest["target_files"])):
            raise UpgradeError("Rollback backup changed")
        if restore_package:
            copy_package(backup / "package-before", target, manifest["target_files"])
            for name in manifest["source_files"].keys() - manifest["target_files"].keys():
                checked_path(target, name).unlink()
        with closing(sqlite3.connect(backup / "state-before.db")) as src, closing(sqlite3.connect(data / "state.db")) as dst:
            src.backup(dst)
        if (database_snapshot(data / "state.db") != manifest["database_before"]
                or (restore_package and package_files(target) != manifest["target_files"])):
            raise UpgradeError("Rollback readback failed; retain checkpoint and repair explicitly")
        report = {"status": "rolled_back_service_stopped", "next": "Restore host activation only after checking the restored package"}
        write_json(backup / "rolled-back.json", report)
        return report


def verify(
    backup_dir: Path,
    *,
    keep_backups: int = 3,
    prune_backups: bool = True,
    plugin_cache_dir: Path | None = None,
) -> dict:
    if type(keep_backups) is not int or keep_backups < 0:
        raise UpgradeError("keep_backups must be a non-negative integer")
    backup = Path(backup_dir).resolve()
    manifest = load_prepared(backup)
    data = Path(manifest["data_dir"])
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(f"http://127.0.0.1:{manifest['port']}/api/health", timeout=5) as response:
        health = json.load(response)
    required = {"durable_inbox_v1", "workflow_gates_v1", "async_inbox_wait_v1"}
    if manifest["supported_schema"] >= 5:
        required.add("workflow_modes_v2")
    if manifest["supported_schema"] >= 6:
        required.add("native_actions_v1")
        required.add("verification_receipts_v1")
    if manifest["supported_schema"] >= 7:
        required.update({"plan_admission_v1", "compact_handoff_v1", "recipient_groups_v1", "progress_classification_v1", "workflow_reviews_v1"})
    if manifest["supported_schema"] >= 8:
        required.update({"macro_rpc_v1", "compact_board_v1", "workflow5_direct_spawn_v1", "session_hooks_v1"})
    if manifest["supported_schema"] >= 10:
        required.add("agent_model_tracking_v1")
    if (health.get("status") != "ok" or health.get("ok") is not True
            or type(health.get("pid")) is not int or not process_alive(health["pid"])
            or health.get("software_version") != manifest["source_identity"]["version"]
            or health.get("schema_version") != manifest["supported_schema"]
            or Path(health.get("data_dir", "")).resolve() != data
            or health.get("port") != manifest["port"]
            or not required.issubset(health.get("capabilities", []))):
        raise UpgradeError(f"Host activation health mismatch: {health}")
    _write_json_replace(
        backup / "verified.json",
        {
            "status": "backend_verified",
            "verified_at": time.time(),
            "software_version": health.get("software_version"),
            "schema_version": health.get("schema_version"),
        },
    )
    report = {
        "status": "backend_verified",
        "health": health,
        "remaining": "Fresh native MCP catalog/tool invocation and three entry acceptance are separate host checks",
    }
    if prune_backups:
        target_raw = manifest.get("target_plugin")
        target_plugin = Path(target_raw) if target_raw else None
        if plugin_cache_dir is None and target_plugin is not None:
            inferred_cache, inferred_active = _infer_plugin_cache_dir(target_plugin)
        else:
            inferred_cache = plugin_cache_dir
            inferred_active = target_plugin
        report["pruned"] = prune(
            backup_root=backup.parent,
            plugin_cache_dir=inferred_cache,
            keep_backups=keep_backups,
            active_plugin_dir=inferred_active,
            current_backup_dir=backup,
            protect_current=(keep_backups > 0),
        )
    return report


def _rotation_created_at(bundle: Path) -> float:
    """Only prepare()'s own package pair and database backup establish ownership."""
    manifest_path = bundle / "prepared.json"
    state_path = bundle / "state-before.db"
    for path in (bundle, manifest_path, state_path):
        if _is_reparse_or_symlink(path):
            raise UpgradeError(f"symlink_or_junction: {path}")

    if not manifest_path.is_file() or not state_path.is_file():
        raise UpgradeError("missing prepared.json or state-before.db")
    manifest = load_prepared(bundle)
    if not isinstance(manifest, dict) or manifest.get("status") != "prepared":
        raise UpgradeError("invalid prepared.json status")
    created_at = manifest.get("created_at")
    if type(created_at) not in (int, float) or not math.isfinite(created_at) or created_at <= 0:
        raise UpgradeError("invalid prepared.json created_at")

    for directory, identity, files in (("staged", "source_identity", "source_files"),
                                       ("package-before", "target_identity", "target_files")):
        package = bundle / directory
        metadata = package / ".codex-plugin" / "plugin.json"
        if any(_is_reparse_or_symlink(path) for path in (package, metadata.parent, metadata)):
            raise UpgradeError(f"symlink_or_junction: {metadata}")
        if not metadata.is_file():
            raise UpgradeError(f"missing plugin metadata: {metadata}")
        inventory = manifest.get(files)
        if (manifest.get(identity) != package_identity(package) or not isinstance(inventory, dict)
                or inventory.get(".codex-plugin/plugin.json") != digest(metadata.read_bytes())):
            raise UpgradeError(f"prepared.json does not identify its {directory} package")
    return created_at


def prune(
    backup_root: Path | None = None,
    *,
    plugin_cache_dir: Path | None = None,
    keep_backups: int = 3,
    keep_plugin_backups: int = 0,
    active_plugin_dir: Path | None = None,
    current_backup_dir: Path | None = None,
    protect_current: bool = True,
    verified_only: bool = False,
    dry_run: bool = False,
) -> dict:
    """Rotate old completed upgrade backups and prune stale plugin-backup-* staging dirs."""
    if type(keep_backups) is not int or keep_backups < 0:
        raise UpgradeError("keep_backups must be a non-negative integer")
    if type(keep_plugin_backups) is not int or keep_plugin_backups < 0:
        raise UpgradeError("keep_plugin_backups must be a non-negative integer")

    active_resolved = active_plugin_dir.resolve() if active_plugin_dir is not None else None
    current_resolved = current_backup_dir.resolve() if current_backup_dir is not None else None

    pruned_backups: list[str] = []
    kept_backups: list[str] = []
    skipped_backups: list[dict[str, str]] = []
    pruned_plugin_backups: list[str] = []
    kept_plugin_backups: list[str] = []
    skipped_plugin_backups: list[dict[str, str]] = []
    freed_bytes = 0

    def _overlaps_active(candidate: Path) -> bool:
        if active_resolved is None:
            return False
        cand_res = candidate.resolve()
        return (
            cand_res == active_resolved
            or active_resolved.is_relative_to(cand_res)
            or cand_res.is_relative_to(active_resolved)
        )

    if backup_root is not None:
        root_path = Path(backup_root).resolve()
        if root_path.is_dir():
            eligible: list[tuple[tuple[float, float, str], Path]] = []
            for entry in sorted(root_path.iterdir(), key=lambda p: p.name):
                if _is_reparse_or_symlink(entry):
                    skipped_backups.append({"path": str(entry), "reason": "symlink_or_junction"})
                    continue
                if not entry.is_dir():
                    continue
                bundle_dir = entry
                marker = entry / "prepared.json"
                if not marker.exists() and not _is_reparse_or_symlink(marker):
                    bundle_dir = entry / "upgrade-bundle"
                    if _is_reparse_or_symlink(bundle_dir):
                        skipped_backups.append({"path": str(entry), "reason": "symlink_or_junction"})
                        continue
                    if not bundle_dir.is_dir():
                        skipped_backups.append({"path": str(entry), "reason": "no_upgrade_bundle"})
                        continue

                if _overlaps_active(bundle_dir):
                    skipped_backups.append({"path": str(bundle_dir), "reason": "active_plugin_overlap"})
                    continue

                try:
                    created_at = _rotation_created_at(bundle_dir)
                    mtime = bundle_dir.lstat().st_mtime
                except (OSError, ValueError, OverflowError, RecursionError, UpgradeError) as exc:
                    skipped_backups.append({"path": str(bundle_dir), "reason": f"invalid_upgrade_backup: {exc}"})
                    continue

                if any(_is_reparse_or_symlink(bundle_dir / name) for name in
                       ("verified.json", "applied.json", "apply-failed.json", "apply-started.json", "rolled-back.json")):
                    skipped_backups.append({"path": str(bundle_dir), "reason": "symlink_or_junction"})
                    continue
                is_verified = (bundle_dir / "verified.json").is_file()
                is_applied = (bundle_dir / "applied.json").is_file()
                is_failed = (bundle_dir / "apply-failed.json").is_file()
                is_started = (bundle_dir / "apply-started.json").is_file()
                is_rolled_back = (bundle_dir / "rolled-back.json").is_file()

                if is_failed and not is_verified:
                    skipped_backups.append({"path": str(bundle_dir), "reason": "apply_failed"})
                    continue
                if is_started and not is_applied and not is_rolled_back and not is_verified:
                    skipped_backups.append({"path": str(bundle_dir), "reason": "apply_in_progress"})
                    continue
                if verified_only and not is_verified:
                    skipped_backups.append({"path": str(bundle_dir), "reason": "not_verified"})
                    continue

                # A wrapper may also contain backups owned by other tools.
                eligible.append(((created_at, mtime, entry.name), bundle_dir))

            eligible.sort(key=lambda item: item[0], reverse=True)
            retained: list[Path] = []
            for _, cand in eligible:
                if protect_current and current_resolved is not None and cand.resolve().is_relative_to(current_resolved):
                    retained.append(cand)
            for _, cand in eligible:
                if cand in retained:
                    continue
                if len(retained) < keep_backups:
                    retained.append(cand)

            retained_set = {p.resolve() for p in retained}
            for _, cand in eligible:
                if cand.resolve() in retained_set:
                    kept_backups.append(str(cand))
                else:
                    freed_bytes += _dir_bytes(cand) if dry_run else _rmtree_safe(cand)
                    pruned_backups.append(str(cand))

    if plugin_cache_dir is not None:
        cache_path = Path(plugin_cache_dir).resolve()
        if cache_path.is_dir():
            has_active_plugin = bool(active_resolved is not None and active_resolved.exists())
            if not has_active_plugin:
                for child in cache_path.iterdir():
                    if not child.name.startswith("plugin-backup-") and _is_valid_plugin_dir(child):
                        has_active_plugin = True
                        break

            staging_candidates: list[tuple[tuple[float, str], Path]] = []
            for entry in sorted(cache_path.iterdir(), key=lambda p: p.name):
                if not entry.name.startswith("plugin-backup-"):
                    continue
                if _is_reparse_or_symlink(entry):
                    skipped_plugin_backups.append({"path": str(entry), "reason": "symlink_or_junction"})
                    continue
                if not entry.is_dir():
                    continue
                if _overlaps_active(entry):
                    skipped_plugin_backups.append({"path": str(entry), "reason": "active_plugin_overlap"})
                    continue
                if not has_active_plugin:
                    skipped_plugin_backups.append({"path": str(entry), "reason": "no_active_plugin_detected"})
                    continue
                try:
                    mtime = float(entry.lstat().st_mtime)
                except OSError:
                    mtime = 0.0
                staging_candidates.append(((mtime, entry.name), entry))

            staging_candidates.sort(key=lambda item: item[0], reverse=True)
            for idx, (_, cand) in enumerate(staging_candidates):
                if idx < keep_plugin_backups:
                    kept_plugin_backups.append(str(cand))
                else:
                    freed_bytes += _dir_bytes(cand) if dry_run else _rmtree_safe(cand)
                    pruned_plugin_backups.append(str(cand))

    return {
        "status": "dry_run" if dry_run else "pruned",
        "dry_run": dry_run,
        "keep_backups": keep_backups,
        "keep_plugin_backups": keep_plugin_backups,
        "freed_bytes": freed_bytes,
        "pruned_backups": pruned_backups,
        "kept_backups": kept_backups,
        "skipped_backups": skipped_backups,
        "pruned_plugin_backups": pruned_plugin_backups,
        "kept_plugin_backups": kept_plugin_backups,
        "skipped_plugin_backups": skipped_plugin_backups,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    prep = sub.add_parser("prepare", help="Stage package and backup without touching installation")
    prep.add_argument("--source-plugin", type=Path, required=True)
    prep.add_argument("--target-plugin", type=Path, required=True)
    prep.add_argument("--data-dir", type=Path, required=True)
    prep.add_argument("--backup-dir", type=Path, required=True)
    prep.add_argument("--port", type=int, default=18790)
    prep.add_argument("--keep-backups", type=int, default=None)
    for action in ("apply", "rollback"):
        command = sub.add_parser(action)
        command.add_argument("--backup-dir", type=Path, required=True)
        if action == "apply":
            command.add_argument("--database-only", action="store_true", help="Migrate state.db without replacing files in the installed plugin cache")
    ver = sub.add_parser("verify", help="Verify running upgraded daemon and prune rotated backups")
    ver.add_argument("--backup-dir", type=Path, required=True)
    ver.add_argument("--keep-backups", type=int, default=3)
    ver.add_argument("--no-prune", action="store_false", dest="prune_backups")
    ver.add_argument("--plugin-cache-dir", type=Path, default=None)
    prn = sub.add_parser("prune", help="Rotate old upgrade backups and expired plugin-backup-* staging dirs")
    prn.add_argument("--backup-root", type=Path, default=None)
    prn.add_argument("--plugin-cache-dir", type=Path, default=None)
    prn.add_argument("--active-plugin-dir", type=Path, default=None)
    prn.add_argument("--keep-backups", type=int, default=3)
    prn.add_argument("--keep-plugin-backups", type=int, default=0)
    prn.add_argument("--verified-only", action="store_true")
    prn.add_argument("--dry-run", action="store_true")
    args = vars(parser.parse_args())
    action = args.pop("action")
    print(json.dumps(globals()[action](**args), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
