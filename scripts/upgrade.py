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
import os
from pathlib import Path
import re
import shutil
import socket
import sqlite3
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
    if value.get("name") != "tri-orchestrator" or not isinstance(value.get("version"), str):
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


def prepare(source_plugin: Path, target_plugin: Path, data_dir: Path, backup_dir: Path, *, port: int = 18790) -> dict:
    source, target, data, backup = (Path(p).resolve() for p in (source_plugin, target_plugin, data_dir, backup_dir))
    paths = [source, target, data, backup]
    if any(a == b or a.is_relative_to(b) or b.is_relative_to(a) for i, a in enumerate(paths) for b in paths[i + 1:]):
        raise UpgradeError("Source, target, runtime data and backup directories must not overlap")
    if type(port) is not int or not 1 <= port <= 65535:
        raise UpgradeError("Invalid loopback port")
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
    return manifest


def load_prepared(backup: Path) -> dict:
    return json.loads((backup / "prepared.json").read_text(encoding="utf-8"))


def apply(backup_dir: Path) -> dict:
    backup = Path(backup_dir).resolve()
    manifest = load_prepared(backup)
    target, data = Path(manifest["target_plugin"]), Path(manifest["data_dir"])
    if (backup / "apply-started.json").exists():
        raise UpgradeError("Upgrade already started; inspect its checkpoint instead of replaying it")
    if re.fullmatch(r"\d+\.\d+\.\d+(?:[-+].+)?", target.name) and target.name != manifest["source_identity"]["version"]:
        raise UpgradeError("Versioned managed cache target belongs to the old release; use supported host installation to activate the staged new version")
    if package_files(backup / "staged") != manifest["source_files"]:
        raise UpgradeError("The staged package changed after preparation")
    with offline(data, manifest["port"]):
        if package_files(target) != manifest["target_files"] or database_snapshot(data / "state.db") != manifest["database_before"]:
            raise UpgradeError("Package or database changed since preparation; prepare a new backup")
        no_active(database_snapshot(data / "state.db"))
        write_json(backup / "apply-started.json", {"started_at": time.time(), "status": "applying_service_stopped"})
        try:
            copy_package(backup / "staged", target, manifest["source_files"])
            for name, expected in manifest["source_files"].items():
                if digest(checked_path(target, name).read_bytes()) != expected:
                    raise UpgradeError(f"Package readback mismatch: {name}")
            code = "import sys; sys.path.insert(0,sys.argv[1]); from orchestrator.db import Database; d=Database(sys.argv[2]); d.close()"
            result = subprocess.run([sys.executable, "-B", "-c", code, str(target / "src"), str(data / "state.db")],
                                    capture_output=True, text=True, encoding="utf-8", timeout=60,
                                    env=dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8"),
                                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            write_json(backup / "migration.json", {"returncode": result.returncode, "stdout": result.stdout, "stderr": result.stderr})
            if result.returncode:
                raise UpgradeError(f"Staged package migration failed: {result.stderr}")
            after = database_snapshot(data / "state.db")
            if after["schema_version"] != manifest["supported_schema"]:
                raise UpgradeError("Migration did not reach the staged schema version")
            no_active(after)
            require_old_rows(data / "state.db", manifest["database_before"])
            report = {"status": "applied_service_stopped", "database_after": after, "package_after": package_files(target),
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
    if (backup / "rolled-back.json").exists():
        raise UpgradeError("Rollback already completed")
    with offline(data, manifest["port"]):
        if database_snapshot(data / "state.db") != applied["database_after"]:
            raise UpgradeError("Database has new writes; preserve them and repair forward instead of restoring an old snapshot")
        if package_files(target) != applied["package_after"]:
            raise UpgradeError("Package changed after apply; inspect before rollback")
        if package_files(backup / "package-before") != manifest["target_files"] or database_snapshot(backup / "state-before.db") != manifest["database_before"]:
            raise UpgradeError("Rollback backup changed")
        copy_package(backup / "package-before", target, manifest["target_files"])
        for name in manifest["source_files"].keys() - manifest["target_files"].keys():
            checked_path(target, name).unlink()
        with closing(sqlite3.connect(backup / "state-before.db")) as src, closing(sqlite3.connect(data / "state.db")) as dst:
            src.backup(dst)
        if database_snapshot(data / "state.db") != manifest["database_before"] or package_files(target) != manifest["target_files"]:
            raise UpgradeError("Rollback readback failed; retain checkpoint and repair explicitly")
        report = {"status": "rolled_back_service_stopped", "next": "Restore host activation only after checking the restored package"}
        write_json(backup / "rolled-back.json", report)
        return report


def verify(backup_dir: Path) -> dict:
    backup = Path(backup_dir).resolve()
    manifest = load_prepared(backup)
    data = Path(manifest["data_dir"])
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(f"http://127.0.0.1:{manifest['port']}/api/health", timeout=5) as response:
        health = json.load(response)
    required = {"durable_inbox_v1", "workflow_gates_v1", "async_inbox_wait_v1"}
    if manifest["supported_schema"] == 5:
        required.add("workflow_modes_v2")
    if (health.get("status") != "ok" or health.get("ok") is not True
            or type(health.get("pid")) is not int or not process_alive(health["pid"])
            or health.get("software_version") != manifest["source_identity"]["version"]
            or health.get("schema_version") != manifest["supported_schema"]
            or Path(health.get("data_dir", "")).resolve() != data
            or health.get("port") != manifest["port"]
            or not required.issubset(health.get("capabilities", []))):
        raise UpgradeError(f"Host activation health mismatch: {health}")
    return {"status": "backend_verified", "health": health,
            "remaining": "Fresh native MCP catalog/tool invocation and three entry acceptance are separate host checks"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    prep = sub.add_parser("prepare", help="Stage package and backup without touching installation")
    prep.add_argument("--source-plugin", type=Path, required=True)
    prep.add_argument("--target-plugin", type=Path, required=True)
    prep.add_argument("--data-dir", type=Path, required=True)
    prep.add_argument("--backup-dir", type=Path, required=True)
    prep.add_argument("--port", type=int, default=18790)
    for action in ("apply", "rollback", "verify"):
        command = sub.add_parser(action)
        command.add_argument("--backup-dir", type=Path, required=True)
    args = vars(parser.parse_args())
    action = args.pop("action")
    print(json.dumps(globals()[action](**args), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
