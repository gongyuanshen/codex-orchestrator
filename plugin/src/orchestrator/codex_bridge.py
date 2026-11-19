"""Read-only bridge to the local Codex project and thread metadata stores."""

from __future__ import annotations

import json
import ntpath
import os
import posixpath
import shutil
import sqlite3
import tempfile
from collections.abc import Iterable, Iterator
from contextlib import closing, contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock
from time import monotonic
from typing import TypedDict


class _Project(TypedDict):
    project_id: str
    name: str
    root_path: str


class _Thread(TypedDict):
    session_id: str
    title: str
    cwd: str
    codex_project_id: str | None
    codex_project_name: str | None
    codex_project_root: str | None


_FileState = tuple[int, int, int]


@dataclass
class _Snapshot:
    state: tuple[Path, tuple[_FileState | None, ...]]
    expires_at: float
    projects: list[_Project]
    names: dict[str, str] | None = None
    threads: dict[str, _Thread | None] = field(default_factory=dict)


_CACHE_TTL = 1.5
_cache: _Snapshot | None = None
_cache_lock = Lock()


def _strip_win_extended_prefix(path: str) -> str:
    if path[:8].upper() == "\\\\?\\UNC\\":
        return "\\\\" + path[8:]
    return path.removeprefix("\\\\?\\")


def _path_key(path: str) -> str:
    clean = _strip_win_extended_prefix(path)
    if ntpath.splitdrive(clean)[0]:
        return ntpath.normcase(ntpath.normpath(clean)).replace("\\", "/")
    return posixpath.normpath(clean)


def _match_project(path: str, projects: Iterable[_Project]) -> _Project | None:
    if not path:
        return None
    key = _path_key(path)
    best = None
    best_length = 0
    for project in projects:
        root = _path_key(project["root_path"])
        if len(root) > best_length and (key == root or key.startswith(root.rstrip("/") + "/")):
            best, best_length = project, len(root)
    return best


def _file_state(path: Path) -> _FileState | None:
    try:
        info = path.stat()
    except FileNotFoundError:
        return None
    return info.st_mtime_ns, info.st_size, info.st_ino


def _scratch_parent(home: Path) -> Path:
    # Discovering tempfile's default directory can itself write a probe file there.
    home = home.resolve()
    local_app_data = os.getenv("LOCALAPPDATA")
    default = Path(local_app_data) / "Temp" if os.name == "nt" and local_app_data else Path("/tmp")
    for candidate in (os.getenv("TMPDIR"), os.getenv("TEMP"), os.getenv("TMP"), default):
        if candidate:
            parent = Path(candidate).resolve()
            if not parent.is_relative_to(home) and parent.is_dir():
                return parent
    raise OSError("No scratch directory outside CODEX_HOME")


@contextmanager
def _connect(db: Path, wal: Path, files: tuple[_FileState | None, ...]) -> Iterator[sqlite3.Connection]:
    with db.open("rb") as source:
        wal_header = 2 in source.read(20)[18:20]
    if not wal_header and files[1] is None:
        with closing(sqlite3.connect(db.as_uri() + "?mode=ro", uri=True, timeout=0.5)) as conn:
            # A writer may switch to WAL after the header read; exclusive mode avoids live SHM.
            conn.execute("PRAGMA locking_mode=EXCLUSIVE")
            conn.execute("PRAGMA temp_store=MEMORY")
            yield conn
        return

    # SQLite's live WAL reader may modify -shm or create sidecars even with mode=ro.
    # Let SQLite reconstruct the WAL index on an isolated copy, never on the live store.
    with tempfile.TemporaryDirectory(prefix="codex-meta-", dir=_scratch_parent(db.parent)) as directory:
        snapshot = Path(directory) / db.name
        shutil.copyfile(db, snapshot)
        if files[1] is not None:
            shutil.copyfile(wal, snapshot.with_name(wal.name))
        if (_file_state(db), _file_state(wal)) != files[:2]:
            raise OSError("Codex state changed while copying")
        with closing(sqlite3.connect(snapshot.as_uri() + "?mode=ro", uri=True, timeout=0.5)) as conn:
            yield conn


@contextmanager
def readonly_snapshot(path: Path) -> Iterator[sqlite3.Connection]:
    """Inspect an existing SQLite store without opening its live WAL index."""
    path = path.resolve()
    wal = path.with_name(path.name + "-wal")
    with _connect(path, wal, (_file_state(path), _file_state(wal))) as conn:
        yield conn


def _read_names(path: Path) -> dict[str, str]:
    names: dict[str, str] = {}
    try:
        with path.open("rb") as index:
            for line in index:
                try:
                    entry = json.loads(line)
                except (ValueError, RecursionError):
                    continue  # An interrupted append must not hide earlier complete entries.
                if isinstance(entry, dict):
                    session_id, name = entry.get("id"), entry.get("thread_name")
                    if isinstance(session_id, str) and isinstance(name, str):
                        names[session_id] = name.strip()
    except FileNotFoundError:
        return {}
    return names


def _first_line(value: str | None) -> str:
    return value.split("\n", 1)[0].partition("\r")[0].strip() if value else ""


def _read_threads(
    conn: sqlite3.Connection, snapshot: _Snapshot, session_ids: list[str],
) -> dict[str, _Thread | None]:
    assert snapshot.names is not None
    found: dict[str, _Thread | None] = dict.fromkeys(session_ids)
    batch = conn.getlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER)
    for start in range(0, len(session_ids), batch):
        ids = session_ids[start:start + batch]
        params = ",".join("?" for _ in ids)
        for session_id, raw_cwd, project_id, name, title, first_message in conn.execute(
            f"""SELECT id, cwd, project_id, name, title, first_user_message FROM threads
                WHERE id IN ({params})
                AND lower(coalesce(thread_source, '')) NOT IN ('subagent', 'guardian_review')
                AND instr(lower(coalesce(source, '')), 'subagent') = 0""",
            ids,
        ):
            cwd = _strip_win_extended_prefix(raw_cwd or "")
            registered = [p for p in snapshot.projects if p["project_id"] == project_id]
            if registered:
                project = _match_project(cwd, registered) or registered[0]
            else:
                project = _match_project(cwd, snapshot.projects)
            found[session_id] = {
                "session_id": session_id,
                "title": (snapshot.names.get(session_id) or (name or "").strip()
                          or _first_line(title) or _first_line(first_message)),
                "cwd": cwd,
                "codex_project_id": project["project_id"] if project else None,
                "codex_project_name": project["name"] if project else None,
                "codex_project_root": project["root_path"] if project else None,
            }
    return found


def _lookup(session_ids: tuple[str, ...] = ()) -> tuple[list[_Project], dict[str, _Thread]]:
    global _cache
    with _cache_lock:
        try:
            configured_home = os.getenv("CODEX_HOME")
            home = Path(configured_home).expanduser() if configured_home else Path.home() / ".codex"
        except RuntimeError:  # No home directory is configured.
            return [], {}
        db, wal = home / "state_5.sqlite", home / "state_5.sqlite-wal"
        index = home / "session_index.jsonl"
        try:
            state = (db.absolute(), (_file_state(db), _file_state(wal), _file_state(index)))
            now = monotonic()
            snapshot = _cache
            if snapshot is not None and (snapshot.state != state or now >= snapshot.expires_at):
                snapshot = None
            missing = [sid for sid in session_ids if snapshot is None or sid not in snapshot.threads]
            if snapshot is None or missing:
                with _connect(state[0], wal, state[1]) as conn:
                    conn.execute("BEGIN")
                    if snapshot is None:
                        projects: list[_Project] = [
                            {"project_id": pid, "name": name, "root_path": _strip_win_extended_prefix(root)}
                            for pid, name, root in conn.execute("""
                                SELECT p.id, p.name, r.path FROM projects AS p
                                JOIN project_roots AS r ON p.id = r.project_id
                                ORDER BY p.id, r.path
                            """)
                        ]
                        snapshot = _Snapshot(state, now + _CACHE_TTL, projects)
                    if missing:
                        if snapshot.names is None:
                            snapshot.names = _read_names(index)
                        snapshot.threads.update(_read_threads(conn, snapshot, missing))
                _cache = snapshot
            return (
                [project.copy() for project in snapshot.projects],
                {sid: meta.copy() for sid in session_ids if (meta := snapshot.threads[sid]) is not None},
            )
        except (OSError, sqlite3.Error):
            return [], {}


def list_codex_projects() -> list[_Project]:
    """Return one project record per registered Codex root."""
    return _lookup()[0]


def resolve_codex_project_for_path(path: str | os.PathLike[str]) -> _Project | None:
    """Find the deepest registered project root containing the path."""
    return _match_project(os.fspath(path), list_codex_projects())


def get_thread_meta(session_id: str) -> _Thread | None:
    """Return user-thread metadata, or None for absent and internal threads."""
    return get_threads_meta((session_id,)).get(session_id)


def get_threads_meta(session_ids: Iterable[str]) -> dict[str, _Thread]:
    """Return metadata keyed by ID, omitting absent and internal threads."""
    ids = tuple(dict.fromkeys(session_ids))
    return _lookup(ids)[1] if ids else {}
