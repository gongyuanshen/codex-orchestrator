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
from time import monotonic, sleep
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
    model: str | None
    reasoning_effort: str | None
    model_provider: str | None


class _SubagentThread(TypedDict):
    thread_id: str
    parent_thread_id: str
    depth: int
    edge_status: str | None
    agent_path: str
    agent_role: str | None
    agent_nickname: str | None
    model: str | None
    reasoning_effort: str | None
    model_provider: str | None
    created_at: float | None
    updated_at: float | None


class _CodexCatalogThread(TypedDict):
    session_id: str
    title: str
    cwd: str
    codex_project_id: str | None
    codex_project_name: str | None
    codex_project_root: str | None
    is_pinned: bool
    created_at: float | None
    updated_at: float | None
    model: str | None
    reasoning_effort: str | None
    model_provider: str | None
    codex_source: str | None
    client_kind: str


class _CodexCatalogProject(TypedDict):
    codex_project_id: str
    name: str
    root_path: str
    root_paths: list[str]
    position: int
    is_git: bool
    threads: list[_CodexCatalogThread]
    project_origin: str
    client_kinds: list[str]


class _CodexCatalog(TypedDict):
    projects: list[_CodexCatalogProject]
    unmatched_cli_threads: list[_CodexCatalogThread]
    missing_session_ids: list[str]


_FileState = tuple[int, int, int]


@dataclass
class _Snapshot:
    state: tuple[Path, tuple[_FileState | None, ...]]
    expires_at: float
    projects: list[_Project]
    names: dict[str, str] | None = None
    threads: dict[str, _Thread | None] = field(default_factory=dict)
    session_subagents: dict[str, list[_SubagentThread]] = field(default_factory=dict)


@dataclass
class _CatalogSnapshot:
    state: tuple[Path, tuple[_FileState | None, ...]]
    bound_ids: frozenset[str]
    expires_at: float
    catalog: _CodexCatalog


_CACHE_TTL = 1.5
_cache: _Snapshot | None = None
_catalog_cache: _CatalogSnapshot | None = None
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


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ? LIMIT 1",
        (table,),
    ).fetchone()
    return row is not None


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    quoted = '"' + table.replace('"', '""') + '"'
    return {row[1] for row in conn.execute(f"PRAGMA table_info({quoted})")}


def _clean_opt_str(value: object) -> str | None:
    if isinstance(value, str):
        return value.strip() or None
    return None


def _read_threads(
    conn: sqlite3.Connection, snapshot: _Snapshot, session_ids: list[str],
) -> dict[str, _Thread | None]:
    assert snapshot.names is not None
    found: dict[str, _Thread | None] = dict.fromkeys(session_ids)
    columns = _table_columns(conn, "threads")
    fields = ", ".join(col if col in columns else "NULL" for col in (
        "cwd", "project_id", "name", "title", "first_user_message", "model", "reasoning_effort", "model_provider",
    ))
    tsource_expr = "thread_source" if "thread_source" in columns else "NULL"
    source_expr = "source" if "source" in columns else "NULL"
    batch = conn.getlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER)
    for start in range(0, len(session_ids), batch):
        ids = session_ids[start:start + batch]
        params = ",".join("?" for _ in ids)
        for (
            session_id,
            raw_cwd,
            project_id,
            name,
            title,
            first_message,
            model,
            reasoning_effort,
            model_provider,
        ) in conn.execute(
            f"""SELECT id, {fields}
                FROM threads
                WHERE id IN ({params})
                AND lower(coalesce({tsource_expr}, '')) NOT IN ('subagent', 'guardian_review')
                AND instr(lower(coalesce({source_expr}, '')), 'subagent') = 0""",
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
                "model": _clean_opt_str(model),
                "reasoning_effort": _clean_opt_str(reasoning_effort),
                "model_provider": _clean_opt_str(model_provider),
            }
    return found


def _read_session_subagents(
    conn: sqlite3.Connection, session_ids: Iterable[str],
) -> dict[str, list[_SubagentThread]]:
    ids = list(dict.fromkeys(session_ids))
    result: dict[str, list[_SubagentThread]] = {sid: [] for sid in ids}
    if not ids or not _table_exists(conn, "thread_spawn_edges"):
        return result

    edge_cols = _table_columns(conn, "thread_spawn_edges")
    edge_status_expr = "e.status" if "status" in edge_cols else "NULL"

    thread_cols = _table_columns(conn, "threads")
    optional_cols = (
        "agent_path",
        "agent_role",
        "agent_nickname",
        "model",
        "reasoning_effort",
        "model_provider",
        "created_at",
        "updated_at",
    )
    select_exprs = [
        f"t.{col}" if col in thread_cols else f"NULL AS {col}"
        for col in optional_cols
    ]
    order_created = "coalesce(t.created_at, 0) ASC, " if "created_at" in thread_cols else ""

    query = f"""
        WITH RECURSIVE tree(parent_thread_id, child_thread_id, edge_status, depth, path_trace) AS (
            SELECT e.parent_thread_id,
                   e.child_thread_id,
                   {edge_status_expr},
                   1,
                   char(31) || e.parent_thread_id || char(31) || e.child_thread_id || char(31)
            FROM thread_spawn_edges AS e
            WHERE e.parent_thread_id = ?
              AND e.child_thread_id != e.parent_thread_id
            UNION ALL
            SELECT e.parent_thread_id,
                   e.child_thread_id,
                   {edge_status_expr},
                   tree.depth + 1,
                   tree.path_trace || e.child_thread_id || char(31)
            FROM thread_spawn_edges AS e
            JOIN tree ON e.parent_thread_id = tree.child_thread_id
            WHERE tree.depth < 8
              AND e.child_thread_id != e.parent_thread_id
              AND instr(tree.path_trace, char(31) || e.child_thread_id || char(31)) = 0
        )
        SELECT tree.child_thread_id,
               tree.parent_thread_id,
               tree.depth,
               tree.edge_status,
               {", ".join(select_exprs)}
        FROM tree
        JOIN threads AS t ON t.id = tree.child_thread_id
        WHERE lower(coalesce(t.thread_source, '')) != 'guardian_review'
        ORDER BY tree.depth ASC, {order_created}t.id ASC
    """

    for sid in ids:
        if not sid:
            continue
        seen_threads: set[str] = set()
        items: list[_SubagentThread] = []
        for (
            thread_id,
            parent_thread_id,
            depth,
            edge_status,
            agent_path,
            agent_role,
            agent_nickname,
            model,
            reasoning_effort,
            model_provider,
            created_at,
            updated_at,
        ) in conn.execute(query, (sid,)):
            if thread_id in seen_threads:
                continue
            seen_threads.add(thread_id)
            items.append({
                "thread_id": thread_id,
                "parent_thread_id": parent_thread_id,
                "depth": int(depth),
                "edge_status": _clean_opt_str(edge_status),
                "agent_path": _clean_opt_str(agent_path) or "",
                "agent_role": _clean_opt_str(agent_role),
                "agent_nickname": _clean_opt_str(agent_nickname),
                "model": _clean_opt_str(model),
                "reasoning_effort": _clean_opt_str(reasoning_effort),
                "model_provider": _clean_opt_str(model_provider),
                "created_at": float(created_at) if created_at is not None else None,
                "updated_at": float(updated_at) if updated_at is not None else None,
            })
        result[sid] = items
    return result


def _resolve_codex_home() -> Path | None:
    try:
        configured_home = os.getenv("CODEX_HOME")
        return Path(configured_home).expanduser() if configured_home else Path.home() / ".codex"
    except RuntimeError:  # No home directory is configured.
        return None


def _is_internal_chatgpt_project_root(root_path: str) -> bool:
    return ".chatgpt-projects" in _path_key(root_path).split("/")


def _read_global_state(
    path: Path,
    home: Path,
) -> tuple[set[str], dict[str, str], dict[str, int], dict[str, int], dict[str, str], set[str]]:
    empty: tuple[set[str], dict[str, str], dict[str, int], dict[str, int], dict[str, str], set[str]] = (
        set(), {}, {}, {}, {}, set()
    )
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError):
        return empty
    if not isinstance(data, dict):
        return empty

    pending_deleted_ids: set[str] = set()
    raw_deletions = data.get("app-server-pending-project-deletions-by-host")
    if isinstance(raw_deletions, dict):
        for host_entries in raw_deletions.values():
            if not isinstance(host_entries, dict):
                continue
            for legacy_id, entry in host_entries.items():
                if isinstance(legacy_id, str) and legacy_id.strip():
                    pending_deleted_ids.add(legacy_id.strip())
                if isinstance(entry, dict):
                    app_id = entry.get("projectId")
                    if isinstance(app_id, str) and app_id.strip():
                        pending_deleted_ids.add(app_id.strip())

    legacy_to_app_id: dict[str, str] = {}
    raw_id_map = data.get("app-server-project-id-by-legacy-project-id-by-host")
    if isinstance(raw_id_map, dict):
        home_key = _path_key(str(home))
        prioritized_hosts: list[tuple[int, dict[object, object]]] = []
        for host_key, mapping in raw_id_map.items():
            if not isinstance(host_key, str) or not host_key.startswith("local:") or not isinstance(mapping, dict):
                continue
            is_exact = _path_key(host_key.removeprefix("local:")) == home_key
            prioritized_hosts.append((1 if is_exact else 0, mapping))
        prioritized_hosts.sort(key=lambda item: item[0])
        for _, mapping in prioritized_hosts:
            for legacy_id, app_id in mapping.items():
                if isinstance(legacy_id, str) and isinstance(app_id, str):
                    clean_legacy = legacy_id.strip()
                    clean_app = app_id.strip()
                    if clean_legacy and clean_app:
                        legacy_to_app_id[clean_legacy] = clean_app

    local_projects = data.get("local-projects")
    if not isinstance(local_projects, dict):
        local_projects = {}

    order_by_app_id: dict[str, int] = {}
    order_by_root: dict[str, int] = {}
    raw_order = data.get("project-order")
    if isinstance(raw_order, list):
        for idx, raw_legacy_id in enumerate(raw_order):
            if not isinstance(raw_legacy_id, str) or not raw_legacy_id.strip():
                continue
            legacy_id = raw_legacy_id.strip()
            mapped_app_id = legacy_to_app_id.get(legacy_id, legacy_id)
            order_by_app_id.setdefault(mapped_app_id, idx)
            lp = local_projects.get(legacy_id)
            if isinstance(lp, dict) and isinstance(lp.get("rootPaths"), list):
                for raw_root in lp["rootPaths"]:
                    if isinstance(raw_root, str) and raw_root.strip():
                        order_by_root.setdefault(_path_key(_strip_win_extended_prefix(raw_root.strip())), idx)

    thread_assignments: dict[str, str] = {}
    excluded_assigned_threads: set[str] = set()
    raw_assignments = data.get("thread-project-assignments")
    if isinstance(raw_assignments, dict):
        for tid, entry in raw_assignments.items():
            if not isinstance(tid, str) or not tid.strip():
                continue
            clean_tid = tid.strip()
            if not isinstance(entry, dict):
                continue
            if entry.get("projectKind") != "local":
                excluded_assigned_threads.add(clean_tid)
                continue
            raw_pid = entry.get("projectId")
            if isinstance(raw_pid, str) and raw_pid.strip():
                clean_raw_pid = raw_pid.strip()
                resolved_app_id = legacy_to_app_id.get(clean_raw_pid, clean_raw_pid)
                if clean_raw_pid in pending_deleted_ids or resolved_app_id in pending_deleted_ids:
                    excluded_assigned_threads.add(clean_tid)
                else:
                    thread_assignments[clean_tid] = resolved_app_id

    return (
        pending_deleted_ids,
        legacy_to_app_id,
        order_by_app_id,
        order_by_root,
        thread_assignments,
        excluded_assigned_threads,
    )


def _copy_catalog(catalog: _CodexCatalog) -> _CodexCatalog:
    return {
        "projects": [
            {
                **project,
                "root_paths": project["root_paths"].copy(),
                "client_kinds": project["client_kinds"].copy(),
                "threads": [thread.copy() for thread in project["threads"]],
            }
            for project in catalog["projects"]
        ],
        "unmatched_cli_threads": [thread.copy() for thread in catalog["unmatched_cli_threads"]],
        "missing_session_ids": catalog["missing_session_ids"].copy(),
    }


def _catalog_thread_sort_key(thread: _CodexCatalogThread) -> tuple[int, float, float, str]:
    return (
        -int(thread["is_pinned"]),
        -(thread["updated_at"] or 0.0),
        -(thread["created_at"] or 0.0),
        thread["session_id"],
    )


def get_codex_state_fingerprint() -> tuple[str, tuple[_FileState | None, ...]] | None:
    """Return a pure stat()-based fingerprint of Codex's local project/thread state files."""
    home = _resolve_codex_home()
    if home is None:
        return None
    db = home / "state_5.sqlite"
    db_state = _file_state(db)
    if db_state is None:
        return None
    wal_state = _file_state(home / "state_5.sqlite-wal")
    index_state = _file_state(home / "session_index.jsonl")
    gs_state = _file_state(home / ".codex-global-state.json")
    return (str(db.absolute()), (db_state, wal_state, index_state, gs_state))


def invalidate_catalog_cache() -> None:
    """Clear in-memory metadata and catalog caches so the next read re-inspects disk."""
    global _cache, _catalog_cache
    with _cache_lock:
        _cache = None
        _catalog_cache = None


def get_codex_catalog(include_exec_session_ids: Iterable[str] | None = None) -> _CodexCatalog | None:
    """Read Desktop and CLI threads, exposing headless exec only for run-bound IDs."""
    global _catalog_cache
    bound_ids = frozenset(include_exec_session_ids or ())
    home = _resolve_codex_home()
    if home is None:
        return None
    db = home / "state_5.sqlite"
    wal = home / "state_5.sqlite-wal"
    index = home / "session_index.jsonl"
    gs_path = home / ".codex-global-state.json"

    with _cache_lock:
        db_state = _file_state(db)
        if db_state is None:
            return None
        state = (db.absolute(), (db_state, _file_state(wal), _file_state(index), _file_state(gs_path)))
        now = monotonic()
        if (_catalog_cache is not None and _catalog_cache.state == state
                and _catalog_cache.bound_ids == bound_ids and now < _catalog_cache.expires_at):
            return _copy_catalog(_catalog_cache.catalog)

        for attempt in range(3):
            db_state = _file_state(db)
            if db_state is None:
                return None
            wal_state = _file_state(wal)
            index_state = _file_state(index)
            gs_state = _file_state(gs_path)
            state = (db.absolute(), (db_state, wal_state, index_state, gs_state))
            try:
                with _connect(state[0], wal, state[1][:3]) as conn:
                    conn.execute("BEGIN")
                    if not _table_exists(conn, "threads"):
                        return None
                    has_desktop = _table_exists(conn, "projects") and _table_exists(conn, "project_roots")
                    (
                        pending_deleted_ids,
                        _legacy_to_app_id,
                        order_by_app_id,
                        order_by_root,
                        thread_assignments,
                        excluded_assigned_threads,
                    ) = _read_global_state(gs_path, home)

                    proj_cols = _table_columns(conn, "projects")
                    root_cols = _table_columns(conn, "project_roots")
                    pos_expr = "coalesce(p.position, 0)" if "position" in proj_cols else "0"
                    root_pos_expr = "coalesce(r.position, 0)" if "position" in root_cols else "NULL"

                    grouped_roots: dict[str, tuple[str, int, list[str]]] = {}
                    project_rows = conn.execute(
                        f"""
                        SELECT p.id, p.name, {pos_expr} AS project_position, r.path
                        FROM projects AS p
                        JOIN project_roots AS r ON p.id = r.project_id
                        ORDER BY project_position ASC, p.id ASC, {root_pos_expr} ASC, r.path ASC
                        """
                    ) if has_desktop else ()
                    for pid, pname, db_pos, raw_root in project_rows:
                        clean_pid = _clean_opt_str(pid)
                        clean_root = _strip_win_extended_prefix(_clean_opt_str(raw_root) or "")
                        if not clean_pid or not clean_root:
                            continue
                        entry = grouped_roots.get(clean_pid)
                        if entry is None:
                            grouped_roots[clean_pid] = (
                                str(pname or Path(clean_root).name or clean_root),
                                int(db_pos or 0),
                                [clean_root],
                            )
                        elif clean_root not in entry[2]:
                            entry[2].append(clean_root)

                    candidates: list[tuple[tuple[int, int, str, str], _CodexCatalogProject, list[str]]] = []
                    for pid, (pname, db_pos, roots) in grouped_roots.items():
                        primary_root = roots[0]
                        if pid in pending_deleted_ids or _is_internal_chatgpt_project_root(primary_root):
                            continue
                        rank = order_by_app_id.get(pid)
                        if rank is None:
                            rank = order_by_root.get(_path_key(primary_root))
                        if rank is None:
                            rank = 10_000 + db_pos
                        try:
                            is_git = (Path(primary_root) / ".git").exists()
                        except OSError:
                            is_git = False
                        proj_obj: _CodexCatalogProject = {
                            "codex_project_id": pid,
                            "name": pname,
                            "root_path": primary_root,
                            "root_paths": roots,
                            "position": db_pos,
                            "is_git": is_git,
                            "threads": [],
                            "project_origin": "desktop",
                            "client_kinds": [],
                        }
                        candidates.append(((rank, db_pos, pname.casefold(), pid), proj_obj, roots))

                    candidates.sort(key=lambda item: item[0])
                    visible_projects: list[_CodexCatalogProject] = []
                    visible_by_id: dict[str, _CodexCatalogProject] = {}
                    visible_root_entries: list[_Project] = []
                    for idx, (_, proj_obj, roots) in enumerate(candidates):
                        proj_obj["position"] = idx
                        visible_projects.append(proj_obj)
                        visible_by_id[proj_obj["codex_project_id"]] = proj_obj
                        for root_path in roots:
                            visible_root_entries.append({
                                "project_id": proj_obj["codex_project_id"],
                                "name": proj_obj["name"],
                                "root_path": root_path,
                            })

                    tcols = _table_columns(conn, "threads")
                    archived_cond = "coalesce(archived, 0) = 0" if "archived" in tcols else "1 = 1"
                    cwd_expr = "cwd" if "cwd" in tcols else "NULL"
                    pid_expr = "project_id" if "project_id" in tcols else "NULL"
                    name_expr = "name" if "name" in tcols else "NULL"
                    title_expr = "title" if "title" in tcols else "NULL"
                    msg_expr = "first_user_message" if "first_user_message" in tcols else "NULL"
                    tsource_expr = "thread_source" if "thread_source" in tcols else "NULL"
                    source_expr = "source" if "source" in tcols else "NULL"
                    pinned_expr = "coalesce(is_pinned, 0)" if "is_pinned" in tcols else "0"
                    created_expr = "created_at" if "created_at" in tcols else "NULL"
                    updated_expr = "updated_at" if "updated_at" in tcols else "NULL"
                    model_expr = "model" if "model" in tcols else "NULL"
                    effort_expr = "reasoning_effort" if "reasoning_effort" in tcols else "NULL"
                    provider_expr = "model_provider" if "model_provider" in tcols else "NULL"

                    names = _read_names(index)
                    thread_sql = f"""
                        SELECT id, {cwd_expr}, {pid_expr}, {name_expr}, {title_expr}, {msg_expr},
                               {tsource_expr}, {source_expr}, {pinned_expr}, {created_expr}, {updated_expr},
                               {model_expr}, {effort_expr}, {provider_expr}
                        FROM threads
                        WHERE {archived_cond}
                          AND lower(coalesce({tsource_expr}, '')) NOT IN ('subagent', 'guardian_review')
                          AND instr(lower(coalesce({source_expr}, '')), 'subagent') = 0
                    """
                    unmatched: list[_CodexCatalogThread] = []
                    missing_ids = set(bound_ids)
                    has_bound_cli = False
                    for (
                        tid,
                        raw_cwd,
                        raw_pid,
                        tname,
                        ttitle,
                        tmsg,
                        tsource,
                        raw_source,
                        is_pinned,
                        created_at,
                        updated_at,
                        model,
                        reasoning_effort,
                        model_provider,
                    ) in conn.execute(thread_sql):
                        if not isinstance(tid, str) or not tid:
                            continue
                        missing_ids.discard(tid)
                        source = (_clean_opt_str(raw_source) or "").lower() or None
                        if source == "exec" and tid not in bound_ids:
                            continue
                        is_cli = source in {"cli", "exec"}
                        has_bound_cli |= is_cli and tid in bound_ids
                        cwd = _strip_win_extended_prefix(raw_cwd or "")
                        resolved_title = (
                            names.get(tid)
                            or (tname or "").strip()
                            or _first_line(ttitle)
                            or _first_line(tmsg)
                        )
                        if not resolved_title and not _clean_opt_str(tsource) and not is_cli:
                            continue

                        clean_pid = _clean_opt_str(raw_pid)
                        if is_cli and (
                            tid in excluded_assigned_threads
                            or clean_pid in pending_deleted_ids
                            or (clean_pid in grouped_roots and clean_pid not in visible_by_id)
                            or _is_internal_chatgpt_project_root(cwd)
                        ):
                            continue
                        assigned_pid = clean_pid or thread_assignments.get(tid)
                        target_proj = visible_by_id.get(assigned_pid) if assigned_pid else None
                        if target_proj is None and (is_cli or (
                            assigned_pid is None and tid not in excluded_assigned_threads
                        )):
                            matched = _match_project(cwd, visible_root_entries)
                            if matched is not None:
                                target_proj = visible_by_id.get(matched["project_id"])
                        if target_proj is None and not is_cli:
                            continue

                        thread: _CodexCatalogThread = {
                            "session_id": tid,
                            "title": resolved_title or tid,
                            "cwd": cwd,
                            "codex_project_id": target_proj["codex_project_id"] if target_proj else clean_pid,
                            "codex_project_name": target_proj["name"] if target_proj else None,
                            "codex_project_root": target_proj["root_path"] if target_proj else None,
                            "is_pinned": bool(is_pinned),
                            "created_at": float(created_at) if created_at is not None else None,
                            "updated_at": float(updated_at) if updated_at is not None else None,
                            "model": _clean_opt_str(model),
                            "reasoning_effort": _clean_opt_str(reasoning_effort),
                            "model_provider": _clean_opt_str(model_provider),
                            "codex_source": source,
                            "client_kind": "cli" if is_cli else "desktop",
                        }
                        (target_proj["threads"] if target_proj else unmatched).append(thread)

                    for proj in visible_projects:
                        proj["threads"].sort(key=_catalog_thread_sort_key)
                        proj["client_kinds"] = sorted({t["client_kind"] for t in proj["threads"]}) or ["desktop"]
                    unmatched.sort(key=_catalog_thread_sort_key)
                    # A deliberately hidden row is not missing and must not be synthesized from a run.
                    candidate_ids = tuple(missing_ids)
                    batch_size = conn.getlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER)
                    for start in range(0, len(candidate_ids), batch_size):
                        batch = candidate_ids[start:start + batch_size]
                        placeholders = ",".join("?" for _ in batch)
                        for tid, raw_source in conn.execute(
                            f"SELECT id, {source_expr} FROM threads WHERE id IN ({placeholders})", batch,
                        ):
                            missing_ids.discard(tid)
                            has_bound_cli |= (_clean_opt_str(raw_source) or "").lower() in {"cli", "exec"}
                    # Falling back to the legacy run list would expose filtered CLI sessions.
                    if not (has_desktop or unmatched or missing_ids or has_bound_cli):
                        return None
                    catalog: _CodexCatalog = {
                        "projects": visible_projects,
                        "unmatched_cli_threads": unmatched,
                        "missing_session_ids": sorted(missing_ids),
                    }
                    _catalog_cache = _CatalogSnapshot(state, bound_ids, monotonic() + _CACHE_TTL, catalog)
                    return _copy_catalog(catalog)
            except OSError as exc:
                if attempt < 2 and "changed while copying" in str(exc):
                    sleep(0.025)
                    continue
                return None
            except sqlite3.Error:
                return None
        return None


def _lookup(
    session_ids: tuple[str, ...] = (),
    subagent_session_ids: tuple[str, ...] = (),
) -> tuple[list[_Project], dict[str, _Thread], dict[str, list[_SubagentThread]]]:
    global _cache
    with _cache_lock:
        home = _resolve_codex_home()
        if home is None:
            return [], {}, {}
        db, wal = home / "state_5.sqlite", home / "state_5.sqlite-wal"
        index = home / "session_index.jsonl"
        try:
            state = (db.absolute(), (_file_state(db), _file_state(wal), _file_state(index)))
            now = monotonic()
            snapshot = _cache
            if snapshot is not None and (snapshot.state != state or now >= snapshot.expires_at):
                snapshot = None
            missing_threads = [
                sid for sid in session_ids if snapshot is None or sid not in snapshot.threads
            ]
            missing_subagents = [
                sid for sid in subagent_session_ids if snapshot is None or sid not in snapshot.session_subagents
            ]
            if snapshot is None or missing_threads or missing_subagents:
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
                        ] if _table_exists(conn, "projects") and _table_exists(conn, "project_roots") else []
                        snapshot = _Snapshot(state, now + _CACHE_TTL, projects)
                    if missing_threads:
                        if snapshot.names is None:
                            snapshot.names = _read_names(index)
                        snapshot.threads.update(_read_threads(conn, snapshot, missing_threads))
                    if missing_subagents:
                        snapshot.session_subagents.update(_read_session_subagents(conn, missing_subagents))
                _cache = snapshot
            return (
                [project.copy() for project in snapshot.projects],
                {sid: meta.copy() for sid in session_ids if (meta := snapshot.threads[sid]) is not None},
                {
                    sid: [item.copy() for item in snapshot.session_subagents[sid]]
                    for sid in subagent_session_ids
                },
            )
        except (OSError, sqlite3.Error):
            return [], {}, {}


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


def get_session_subagents(session_id: str) -> list[_SubagentThread]:
    """Return subagent tree metadata spawned from session_id."""
    if not session_id:
        return []
    return get_sessions_subagents((session_id,)).get(session_id, [])


def get_sessions_subagents(session_ids: Iterable[str]) -> dict[str, list[_SubagentThread]]:
    """Return subagent trees keyed by parent session ID, or {} when unavailable."""
    ids = tuple(dict.fromkeys(session_ids))
    return _lookup(subagent_session_ids=ids)[2] if ids else {}
