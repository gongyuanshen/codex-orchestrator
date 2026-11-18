"""SQLite (WAL) state engine, schema migrations, and CRUD helpers for tri-orchestrator."""

from __future__ import annotations

import json
import os
import posixpath
import re
import socket
import sqlite3
import tempfile
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from orchestrator.config import Settings, get_settings

SCHEMA_VERSION = 9

TASK_NAME_RE = re.compile(r"^[a-z0-9_-]{1,48}$")
PENDING_AGENT_PATH_RE = re.compile(r"^/root/[a-z0-9_-]{1,48}$")

VALID_RUN_MODES = frozenset({"boost", "teamwork", "swarm"})
VALID_RUN_STATUSES = frozenset({"active", "completed", "failed", "archived"})
VALID_WORKER_ROLES = frozenset(
    {"interviewer", "planner", "investigator", "coder", "verifier", "swarm_worker"}
)
VALID_WORKER_STATUSES = frozenset({"waiting", "working", "stopped", "killed"})
VALID_MSG_TYPES = frozenset({"info", "handoff", "diff", "test", "alert", "human"})
VALID_WORKSPACE_MODES = frozenset({"git_clone", "snapshot"})
VALID_WORKSPACE_STATUSES = frozenset(
    {"active", "verified", "merged", "cleaned", "conflict"}
)


class NotFoundError(LookupError):
    """Raised when a referenced project, run, worker, workspace, or packet does not exist."""


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _json_dumps(value: Any, default_empty: str) -> str:
    if value is None:
        return default_empty
    if isinstance(value, str):
        # Validate JSON if caller already serialized it.
        json.loads(value)
        return value
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _json_loads(raw: str | None, fallback: Any) -> Any:
    if not raw:
        return fallback
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return fallback


def _is_port_bindable(port: int, host: str = "127.0.0.1") -> bool:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        # On Windows SO_EXCLUSIVEADDRUSE prevents binding over another listener.
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        sock.bind((host, port))
        return True
    except OSError:
        return False
    finally:
        sock.close()


def classify_project_kind(root_path: str | Path) -> str:
    """Classify project paths without changing the stored path."""
    path = posixpath.normpath(str(root_path).replace("\\", "/")).casefold()
    parts = tuple(path.split("/"))
    if any(parts[i:i + 2] == (".codex", "backups") for i in range(len(parts))):
        return "temp"
    if any(parts[i:i + 2] == ("docs", "verification") and "fixture" in parts[i + 2:]
           for i in range(len(parts))):
        return "fixture"
    if any(parts[i:i + 3] == ("appdata", "local", "temp")
           or parts[i:i + 2] == ("windows", "temp") for i in range(len(parts))):
        return "temp"
    temp_root = posixpath.normpath(tempfile.gettempdir().replace("\\", "/")).casefold()
    return "temp" if path == temp_root or path.startswith(temp_root.rstrip("/") + "/") else "workspace"


def _refresh_canonical_projects(conn: sqlite3.Connection, subtree: Path | None = None) -> None:
    projects = [(Path(os.path.abspath(r["root_path"])), r) for r in conn.execute(
        "SELECT id, root_path, is_git, canonical_project_id FROM projects ORDER BY rowid ASC"
    )]
    roots: dict[Path, str] = {}
    for path, project in sorted(projects, key=lambda entry: len(entry[0].parts)):
        canonical_id = project["canonical_project_id"]
        if subtree is None or path.is_relative_to(subtree):
            canonical_id = next((roots[parent] for parent in path.parents if parent in roots), None)
            if canonical_id and (project["is_git"] or (path / ".git").exists()):
                canonical_id = None
            if project["canonical_project_id"] != canonical_id:
                conn.execute("UPDATE projects SET canonical_project_id = ? WHERE id = ?",
                             (canonical_id, project["id"]))
        roots.setdefault(path, canonical_id or project["id"])


def _project_from_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "root_path": row["root_path"],
        "name": row["name"],
        "is_git": bool(row["is_git"]),
        "created_at": row["created_at"],
        "codex_project_id": row["codex_project_id"],
        "codex_project_name": row["codex_project_name"],
        "project_kind": row["project_kind"],
        "canonical_project_id": row["canonical_project_id"],
    }


def _run_from_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "project_id": row["project_id"],
        "mode": row["mode"],
        "title": row["title"],
        "status": row["status"],
        "metadata": _json_loads(row["metadata_json"], {}),
        "metadata_json": row["metadata_json"],
        "workflow_version": row["workflow_version"],
        "session_id": row["session_id"],
        "turn_id": row["turn_id"],
        "session_seq": row["session_seq"],
        "trigger_prompt": row["trigger_prompt"],
        "execution_root": row["execution_root"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _task_packet_from_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "run_id": row["run_id"],
        "version": row["version"],
        "title": row["title"],
        "goal": row["goal"],
        "file_ownership": _json_loads(row["file_ownership_json"], {}),
        "file_ownership_json": row["file_ownership_json"],
        "acceptance_criteria": _json_loads(row["acceptance_criteria_json"], []),
        "acceptance_criteria_json": row["acceptance_criteria_json"],
        "status": row["status"],
        "created_at": row["created_at"],
    }


def _worker_from_row(row: sqlite3.Row) -> dict[str, Any]:
    keys = row.keys()
    return {
        "id": row["id"],
        "run_id": row["run_id"],
        "name": row["name"],
        "role": row["role"],
        "agent_id": row["agent_id"],
        "pending_agent_path": row["pending_agent_path"] if "pending_agent_path" in keys else None,
        "status": row["status"],
        "workspace_id": row["workspace_id"],
        "current_task": row["current_task"],
        "last_heartbeat_at": row["last_heartbeat_at"],
        "stall_threshold_sec": row["stall_threshold_sec"],
        "stalled_alerted": bool(row["stalled_alerted"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _board_message_from_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "seq": row["seq"],
        "run_id": row["run_id"],
        "channel": row["channel"],
        "sender": row["sender"],
        "sender_role": row["sender_role"],
        "mentions": _json_loads(row["mentions_json"], []),
        "mentions_json": row["mentions_json"],
        "msg_type": row["msg_type"],
        "summary": row["summary"],
        "body_md": row["body_md"],
        "created_at": row["created_at"],
    }


def _workspace_from_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "run_id": row["run_id"],
        "worker_name": row["worker_name"],
        "path": row["path"],
        "branch_name": row["branch_name"],
        "base_commit": row["base_commit"],
        "merged_commit": row["merged_commit"],
        "merged_target_commit": row["merged_target_commit"],
        "mode": row["mode"],
        "assigned_port": row["assigned_port"],
        "status": row["status"],
        "snapshot_manifest": _json_loads(row["snapshot_manifest_json"], {}),
        "snapshot_manifest_json": row["snapshot_manifest_json"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


_SESSION_BINDINGS_SQL = """
CREATE TABLE IF NOT EXISTS session_run_bindings (
    session_id TEXT,
    run_id TEXT NOT NULL,
    project_root TEXT NOT NULL,
    last_stop_blocked_key TEXT,
    updated_at REAL NOT NULL,
    is_primary INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (session_id, run_id)
)
"""
_SESSION_BINDINGS_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_session_run_bindings_run
    ON session_run_bindings (run_id, updated_at DESC)
"""

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    root_path TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    is_git INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    codex_project_id TEXT,
    codex_project_name TEXT,
    project_kind TEXT NOT NULL DEFAULT 'workspace' CHECK (project_kind IN ('workspace', 'fixture', 'temp')),
    canonical_project_id TEXT
);

CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    mode TEXT NOT NULL CHECK (mode IN ('boost', 'teamwork', 'swarm')),
    title TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'completed', 'failed', 'archived')),
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    session_id TEXT,
    turn_id TEXT,
    session_seq INTEGER NOT NULL DEFAULT 0,
    trigger_prompt TEXT NOT NULL DEFAULT '',
    execution_root TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS task_packets (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    version INTEGER NOT NULL,
    title TEXT NOT NULL,
    goal TEXT NOT NULL,
    file_ownership_json TEXT NOT NULL DEFAULT '{}',
    acceptance_criteria_json TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'active',
    created_at REAL NOT NULL,
    UNIQUE (run_id, version)
);

CREATE TABLE IF NOT EXISTS workspaces (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    worker_name TEXT NOT NULL,
    path TEXT NOT NULL,
    branch_name TEXT,
    base_commit TEXT,
    merged_commit TEXT,
    merged_target_commit TEXT,
    mode TEXT NOT NULL CHECK (mode IN ('git_clone', 'snapshot')),
    assigned_port INTEGER,
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'verified', 'merged', 'cleaned', 'conflict')),
    snapshot_manifest_json TEXT NOT NULL DEFAULT '{}',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS workers (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('interviewer', 'planner', 'investigator', 'coder', 'verifier', 'swarm_worker')),
    agent_id TEXT,
    pending_agent_path TEXT,
    status TEXT NOT NULL DEFAULT 'waiting' CHECK (status IN ('waiting', 'working', 'stopped', 'killed')),
    workspace_id TEXT,
    current_task TEXT NOT NULL DEFAULT '',
    last_heartbeat_at REAL NOT NULL,
    stall_threshold_sec INTEGER NOT NULL DEFAULT 300,
    stalled_alerted INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    UNIQUE (run_id, name)
);

""" + _SESSION_BINDINGS_SQL + """;

CREATE TABLE IF NOT EXISTS session_turns (
    session_id TEXT NOT NULL,
    turn_id TEXT NOT NULL,
    cwd TEXT NOT NULL,
    prompt_preview TEXT NOT NULL,
    active_run_id TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    PRIMARY KEY (session_id, turn_id)
);
CREATE INDEX IF NOT EXISTS idx_session_turns_session_updated
    ON session_turns (session_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS board_messages (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    channel TEXT NOT NULL DEFAULT 'main',
    sender TEXT NOT NULL,
    sender_role TEXT NOT NULL DEFAULT '',
    mentions_json TEXT NOT NULL DEFAULT '[]',
    msg_type TEXT NOT NULL DEFAULT 'info' CHECK (msg_type IN ('info', 'handoff', 'diff', 'test', 'alert', 'human')),
    summary TEXT NOT NULL DEFAULT '',
    body_md TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS read_cursors (
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    reader_name TEXT NOT NULL,
    channel TEXT NOT NULL DEFAULT 'main',
    last_seq INTEGER NOT NULL DEFAULT 0,
    updated_at REAL NOT NULL,
    PRIMARY KEY (run_id, reader_name, channel)
);

CREATE INDEX IF NOT EXISTS idx_runs_project_updated
    ON runs (project_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_task_packets_run_version
    ON task_packets (run_id, version DESC);
CREATE INDEX IF NOT EXISTS idx_workspaces_run_worker
    ON workspaces (run_id, worker_name);
CREATE INDEX IF NOT EXISTS idx_workspaces_port_status
    ON workspaces (assigned_port, status);
CREATE INDEX IF NOT EXISTS idx_workers_run_status
    ON workers (run_id, status);
CREATE INDEX IF NOT EXISTS idx_workers_heartbeat
    ON workers (status, stalled_alerted, last_heartbeat_at);
CREATE INDEX IF NOT EXISTS idx_board_messages_run_seq
    ON board_messages (run_id, seq);
CREATE INDEX IF NOT EXISTS idx_board_messages_run_channel_seq
    ON board_messages (run_id, channel, seq);
""" + _SESSION_BINDINGS_INDEX_SQL + ";"


def _migrate_session_metadata(conn: sqlite3.Connection, version: int) -> None:
    for table, additions in (
        ("projects", {
            "codex_project_id": "TEXT",
            "codex_project_name": "TEXT",
            "project_kind": "TEXT NOT NULL DEFAULT 'workspace' CHECK (project_kind IN ('workspace', 'fixture', 'temp'))",
            "canonical_project_id": "TEXT",
        }),
        ("runs", {
            "session_id": "TEXT",
            "turn_id": "TEXT",
            "session_seq": "INTEGER NOT NULL DEFAULT 0",
            "trigger_prompt": "TEXT NOT NULL DEFAULT ''",
            "execution_root": "TEXT NOT NULL DEFAULT ''",
        }),
    ):
        columns = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        for name, declaration in additions.items():
            if name not in columns:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {declaration}")

    bindings = {r["name"]: r["pk"] for r in conn.execute("PRAGMA table_info(session_run_bindings)")}
    if bindings["run_id"] != 2:
        conn.execute("ALTER TABLE session_run_bindings RENAME TO session_run_bindings_v8")
        conn.execute(_SESSION_BINDINGS_SQL)
        # The offline upgrade gate hashes the exact old column values in rowid order.
        conn.execute("""
            INSERT INTO session_run_bindings
                (rowid, session_id, run_id, project_root, last_stop_blocked_key, updated_at)
            SELECT rowid, session_id, run_id, project_root, last_stop_blocked_key, updated_at
            FROM session_run_bindings_v8 ORDER BY rowid ASC
        """)
        conn.execute("DROP TABLE session_run_bindings_v8")
        conn.execute(_SESSION_BINDINGS_INDEX_SQL)
    elif "is_primary" not in bindings:
        conn.execute("ALTER TABLE session_run_bindings ADD COLUMN is_primary INTEGER NOT NULL DEFAULT 1")

    if version < 9:
        conn.executemany(
            "UPDATE projects SET project_kind = ? WHERE id = ?",
            [(classify_project_kind(r["root_path"]), r["id"])
             for r in conn.execute("SELECT id, root_path FROM projects")],
        )
        _refresh_canonical_projects(conn)
        conn.execute("""
            UPDATE runs SET session_id = (
                SELECT b.session_id FROM session_run_bindings AS b
                WHERE b.run_id = runs.id AND b.session_id <> ''
                ORDER BY b.updated_at DESC, b.rowid DESC LIMIT 1
            )
            WHERE (session_id IS NULL OR session_id = '') AND EXISTS (
                SELECT 1 FROM session_run_bindings AS b WHERE b.run_id = runs.id AND b.session_id <> ''
            )
        """)
        conn.execute("UPDATE runs SET session_seq = 1 WHERE session_id <> '' AND session_seq = 0")


class Database:
    """Thread-safe SQLite WAL state store with explicit BEGIN IMMEDIATE write locking."""

    def __init__(
        self,
        db_path: str | Path | None = None,
        *,
        settings: Settings | None = None,
        init_schema: bool = True,
    ) -> None:
        resolved_settings = settings or get_settings()
        target_path = db_path if db_path is not None else resolved_settings.db_path
        self.db_path: Path = Path(target_path)
        self.settings: Settings = resolved_settings
        self._is_memory = str(target_path) == ":memory:"
        if not self._is_memory:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)

        # In-process RLock serializes write transactions and avoids SQLite read->write lock upgrades.
        self._write_lock = threading.RLock()
        self._pool_lock = threading.Lock()
        self._local = threading.local()
        self._conns_by_tid: dict[int, sqlite3.Connection] = {}
        self._closed = False

        if init_schema:
            try:
                self.migrate()
            except BaseException:
                self.close()
                raise

    @classmethod
    def from_settings(cls, settings: Settings | None = None) -> Database:
        cfg = settings or get_settings()
        return cls(cfg.db_path, settings=cfg)

    def _open_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(
            ":memory:" if self._is_memory else str(self.db_path),
            timeout=5.0,
            isolation_level=None,
            check_same_thread=False,
        )
        conn.row_factory = sqlite3.Row
        # Always configure busy_timeout before other pragmas so concurrent opens wait cleanly.
        conn.execute("PRAGMA busy_timeout=5000;")
        if not self._is_memory:
            for attempt in range(5):
                try:
                    mode = str(
                        conn.execute("PRAGMA journal_mode;").fetchone()[0]
                    ).lower()
                    if mode != "wal":
                        conn.execute("PRAGMA journal_mode=WAL;")
                    break
                except sqlite3.OperationalError:
                    if attempt == 4:
                        raise
                    time.sleep(0.05 * (attempt + 1))
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
        return conn

    def connect(self) -> sqlite3.Connection:
        """Return the calling thread's configured SQLite connection."""
        if self._closed:
            raise RuntimeError("Database has been closed")
        conn: sqlite3.Connection | None = getattr(self._local, "conn", None)
        if conn is not None:
            return conn
        tid = threading.get_ident()
        conn = self._open_connection()
        with self._pool_lock:
            if self._closed:
                conn.close()
                raise RuntimeError("Database has been closed")
            self._conns_by_tid[tid] = conn
            self._local.conn = conn
            self._local.tx_depth = 0
        return conn

    @contextmanager
    def read_connection(self) -> Iterator[sqlite3.Connection]:
        """Yield a thread-local WAL connection for read queries."""
        yield self.connect()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Serialize write transactions using an RLock + BEGIN IMMEDIATE (savepoints when nested)."""
        with self._write_lock:
            conn = self.connect()
            depth = int(getattr(self._local, "tx_depth", 0))
            if depth == 0:
                conn.execute("BEGIN IMMEDIATE;")
            else:
                conn.execute(f"SAVEPOINT _sp_{depth};")
            self._local.tx_depth = depth + 1
            try:
                yield conn
            except BaseException:
                if depth == 0:
                    if conn.in_transaction:
                        conn.execute("ROLLBACK;")
                else:
                    conn.execute(f"ROLLBACK TO SAVEPOINT _sp_{depth};")
                    conn.execute(f"RELEASE SAVEPOINT _sp_{depth};")
                raise
            else:
                if depth == 0:
                    if conn.in_transaction:
                        conn.execute("COMMIT;")
                else:
                    conn.execute(f"RELEASE SAVEPOINT _sp_{depth};")
            finally:
                self._local.tx_depth = depth

    def migrate(self) -> int:
        """Create/upgrade SQLite schema idempotently and return the current schema version."""
        with self.transaction() as conn:
            version = 0
            if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_migrations'").fetchone():
                version = conn.execute("SELECT COALESCE(MAX(version),0) FROM schema_migrations").fetchone()[0]
                if version > SCHEMA_VERSION:
                    raise ValueError(f"Database schema {version} is newer than supported {SCHEMA_VERSION}")
            # Execute DDL statements individually so BEGIN IMMEDIATE stays active (unlike executescript).
            for stmt in _SCHEMA_SQL.split(";"):
                sql = stmt.strip()
                if sql:
                    conn.execute(sql + ";")
            worker_cols = {r["name"] for r in conn.execute("PRAGMA table_info(workers)")}
            if "agent_id" not in worker_cols:
                conn.execute("ALTER TABLE workers ADD COLUMN agent_id TEXT")
            if "pending_agent_path" not in worker_cols:
                conn.execute("ALTER TABLE workers ADD COLUMN pending_agent_path TEXT")
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_workers_pending_path "
                "ON workers(run_id, pending_agent_path) "
                "WHERE pending_agent_path IS NOT NULL AND pending_agent_path != ''"
            )
            conn.execute("""CREATE TABLE IF NOT EXISTS worker_controls (
                id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(id),
                worker_id TEXT NOT NULL REFERENCES workers(id), agent_id TEXT NOT NULL,
                requested_status TEXT NOT NULL, status TEXT NOT NULL,
                result TEXT, created_at REAL NOT NULL, resolved_at REAL)""")
            conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_control_pending ON worker_controls(worker_id) WHERE status = 'pending'")
            # Upgrade guard if an existing table missed snapshot_manifest_json.
            ws_cols = {
                row["name"]
                for row in conn.execute("PRAGMA table_info(workspaces);").fetchall()
            }
            if "snapshot_manifest_json" not in ws_cols:
                conn.execute(
                    "ALTER TABLE workspaces ADD COLUMN snapshot_manifest_json TEXT NOT NULL DEFAULT '{}';"
                )
            for column in ("merged_commit", "merged_target_commit"):
                if column not in ws_cols:
                    conn.execute(f"ALTER TABLE workspaces ADD COLUMN {column} TEXT;")
            if "workflow_version" not in {r["name"] for r in conn.execute("PRAGMA table_info(runs)")}:
                conn.execute("ALTER TABLE runs ADD COLUMN workflow_version INTEGER NOT NULL DEFAULT 0")
            _migrate_session_metadata(conn, version)
            from orchestrator.workflow import install_schema
            install_schema(conn)
            from orchestrator.native_actions import install_schema as install_native_schema
            install_native_schema(conn)
            inbox_columns = {r["name"] for r in conn.execute("PRAGMA table_info(inbox_messages)")}
            for column in ("step_packet_version", "step_attempt", "step_contract_revision"):
                if column not in inbox_columns:
                    conn.execute(f"ALTER TABLE inbox_messages ADD COLUMN {column} INTEGER")
            conn.execute(
                "INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (?, ?);",
                (SCHEMA_VERSION, time.time()),
            )
            row = conn.execute(
                "SELECT COALESCE(MAX(version), 0) AS v FROM schema_migrations;"
            ).fetchone()
            return int(row["v"])

    def close(self) -> None:
        """Close all thread-local connections held by this Database instance."""
        with self._write_lock:
            with self._pool_lock:
                self._closed = True
                conns = list(self._conns_by_tid.values())
                self._conns_by_tid.clear()
            for conn in conns:
                try:
                    conn.close()
                except sqlite3.Error:
                    pass
            if hasattr(self._local, "conn"):
                delattr(self._local, "conn")
            if hasattr(self._local, "tx_depth"):
                delattr(self._local, "tx_depth")

    def __enter__(self) -> Database:
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.close()

    # ------------------------------------------------------------------
    # Projects
    # ------------------------------------------------------------------

    @staticmethod
    def _normalize_root(root_path: str | Path) -> str:
        return str(Path(root_path).resolve(strict=False))

    def ensure_project(
        self,
        root_path: str | Path,
        *,
        name: str | None = None,
        is_git: bool | None = None,
        codex_project_id: str | None = None,
        codex_project_name: str | None = None,
    ) -> dict[str, Any]:
        """Insert or update a project keyed by its root path."""
        norm_root = self._normalize_root(root_path)
        resolved_name = (name or Path(norm_root).name or norm_root).strip()
        now = time.time()
        with self.transaction() as conn:
            existing = conn.execute(
                "SELECT * FROM projects WHERE root_path = ?;",
                (norm_root,),
            ).fetchone()
            if existing is not None:
                if is_git is not None and bool(existing["is_git"]) != bool(is_git):
                    conn.execute(
                        "UPDATE projects SET is_git = ? WHERE id = ?;",
                        (1 if is_git else 0, existing["id"]),
                    )
                if name is not None and resolved_name and existing["name"] != resolved_name:
                    conn.execute(
                        "UPDATE projects SET name = ? WHERE id = ?;",
                        (resolved_name, existing["id"]),
                    )
                if codex_project_id is not None or codex_project_name is not None:
                    conn.execute("""
                        UPDATE projects SET codex_project_id = COALESCE(?, codex_project_id),
                            codex_project_name = COALESCE(?, codex_project_name) WHERE id = ?
                    """, (codex_project_id, codex_project_name, existing["id"]))
                project_id = existing["id"]
            else:
                project_id = _new_id("proj")
                conn.execute("""
                    INSERT INTO projects
                        (id, root_path, name, is_git, created_at, codex_project_id, codex_project_name, project_kind)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (project_id, norm_root, resolved_name, int(bool(is_git)), now,
                      codex_project_id, codex_project_name, classify_project_kind(norm_root)))
            _refresh_canonical_projects(conn, Path(norm_root))
            row = conn.execute(
                "SELECT * FROM projects WHERE id = ?;", (project_id,)
            ).fetchone()
            assert row is not None
            return _project_from_row(row)

    def get_project(self, project_id: str) -> dict[str, Any] | None:
        with self.read_connection() as conn:
            row = conn.execute(
                "SELECT * FROM projects WHERE id = ?;", (project_id,)
            ).fetchone()
            return _project_from_row(row) if row is not None else None

    def get_project_by_root(self, root_path: str | Path) -> dict[str, Any] | None:
        norm_root = self._normalize_root(root_path)
        with self.read_connection() as conn:
            row = conn.execute(
                "SELECT * FROM projects WHERE root_path = ?;", (norm_root,)
            ).fetchone()
            return _project_from_row(row) if row is not None else None

    def list_projects(self) -> list[dict[str, Any]]:
        with self.read_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM projects ORDER BY created_at DESC, name ASC;"
            ).fetchall()
            return [_project_from_row(r) for r in rows]

    # ------------------------------------------------------------------
    # Runs
    # ------------------------------------------------------------------

    def create_run(
        self,
        *,
        mode: str,
        title: str,
        project_id: str | None = None,
        project_root: str | Path | None = None,
        project_name: str | None = None,
        is_git: bool | None = None,
        status: str = "active",
        metadata: dict[str, Any] | str | None = None,
        run_id: str | None = None,
        session_id: str | None = None,
        turn_id: str | None = None,
        trigger_prompt: str = "",
        execution_root: str = "",
    ) -> dict[str, Any]:
        """Create a new orchestration run, auto-registering the project when project_root is given."""
        if mode not in VALID_RUN_MODES:
            raise ValueError(f"Invalid run mode {mode!r}; expected one of {sorted(VALID_RUN_MODES)}")
        if status not in VALID_RUN_STATUSES:
            raise ValueError(
                f"Invalid run status {status!r}; expected one of {sorted(VALID_RUN_STATUSES)}"
            )
        if status != "active":
            raise ValueError("New workflow runs must start active")
        if project_id is None and project_root is None:
            raise ValueError("Either project_id or project_root must be provided")
        if session_id is not None and (not isinstance(session_id, str) or not session_id.strip()):
            raise ValueError("session_id must be a non-empty string when provided")

        clean_session = session_id.strip() if session_id is not None else None
        meta_json = _json_dumps(metadata, "{}")
        rid = run_id or _new_id("run")

        with self.transaction() as conn:
            now = time.time()
            resolved_project_id = project_id
            resolved_root_path: str
            if resolved_project_id is None:
                assert project_root is not None
                proj = self.ensure_project(
                    project_root, name=project_name, is_git=is_git
                )
                resolved_project_id = proj["id"]
                resolved_root_path = proj["root_path"]
            else:
                proj_row = conn.execute(
                    "SELECT root_path FROM projects WHERE id = ?;", (resolved_project_id,)
                ).fetchone()
                if proj_row is None:
                    raise NotFoundError(f"Project not found: {resolved_project_id}")
                resolved_root_path = str(proj_row["root_path"])

            session_seq = self._next_session_seq(conn, clean_session) if clean_session is not None else 0
            conn.execute("""
                INSERT INTO runs
                    (id, project_id, mode, title, status, metadata_json, created_at, updated_at, workflow_version,
                     session_id, turn_id, session_seq, trigger_prompt, execution_root)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 5, ?, ?, ?, ?, ?)
            """, (rid, resolved_project_id, mode, title, status, meta_json, now, now,
                  clean_session, turn_id, session_seq, trigger_prompt, execution_root))
            if clean_session is not None:
                self._upsert_session_binding_in_tx(
                    conn,
                    session_id=clean_session,
                    run_id=rid,
                    project_root=resolved_root_path,
                    now=now,
                )
            row = conn.execute("SELECT * FROM runs WHERE id = ?;", (rid,)).fetchone()
            assert row is not None
            return _run_from_row(row)

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with self.read_connection() as conn:
            row = conn.execute("SELECT * FROM runs WHERE id = ?;", (run_id,)).fetchone()
            return _run_from_row(row) if row is not None else None

    def update_run(
        self,
        run_id: str,
        *,
        status: str | None = None,
        title: str | None = None,
        metadata: dict[str, Any] | str | None = None,
    ) -> dict[str, Any]:
        if status is not None and status not in VALID_RUN_STATUSES:
            raise ValueError(
                f"Invalid run status {status!r}; expected one of {sorted(VALID_RUN_STATUSES)}"
            )
        now = time.time()
        with self.transaction() as conn:
            existing = conn.execute(
                "SELECT * FROM runs WHERE id = ?;", (run_id,)
            ).fetchone()
            if existing is None:
                raise NotFoundError(f"Run not found: {run_id}")

            new_status = status if status is not None else existing["status"]
            if existing["workflow_version"] not in {0, 1, 2, 3, 4, 5}:
                from orchestrator.workflow import WorkflowConflictError
                raise WorkflowConflictError("Unsupported workflow version")
            if existing["workflow_version"] in {1, 2, 3, 4, 5}:
                from orchestrator.workflow import WorkflowConflictError, WorkflowService
                if existing["status"] != "active" and new_status == "active":
                    raise WorkflowConflictError("Create a new continuation run instead of reopening a terminal workflow")
                if new_status == "completed":
                    WorkflowService(self).require_completion(run_id)
            new_title = title if title is not None else existing["title"]
            new_meta = (
                _json_dumps(metadata, "{}")
                if metadata is not None
                else existing["metadata_json"]
            )
            if new_status != "active" and conn.execute("SELECT 1 FROM worker_controls WHERE run_id = ? AND status = 'pending'", (run_id,)).fetchone():
                raise ValueError("Acknowledge pending controls before closing run")
            conn.execute(
                """
                UPDATE runs
                SET status = ?, title = ?, metadata_json = ?, updated_at = ?
                WHERE id = ?;
                """,
                (new_status, new_title, new_meta, now, run_id),
            )
            row = conn.execute("SELECT * FROM runs WHERE id = ?;", (run_id,)).fetchone()
            assert row is not None
            return _run_from_row(row)

    def list_runs(
        self,
        *,
        project_id: str | None = None,
        status: str | None = None,
        mode: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if project_id is not None:
            clauses.append("project_id = ?")
            params.append(project_id)
        if status is not None:
            clauses.append("status = ?")
            params.append(status)
        if mode is not None:
            clauses.append("mode = ?")
            params.append(mode)
        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(max(1, int(limit)))
        with self.read_connection() as conn:
            rows = conn.execute(
                f"SELECT * FROM runs {where_sql} ORDER BY updated_at DESC, created_at DESC LIMIT ?;",
                params,
            ).fetchall()
            return [_run_from_row(r) for r in rows]

    # ------------------------------------------------------------------
    # Task Packets (versioned per run)
    # ------------------------------------------------------------------

    def save_task_packet(
        self,
        run_id: str,
        *,
        title: str,
        goal: str,
        file_ownership: dict[str, Any] | list[Any] | str | None = None,
        acceptance_criteria: list[Any] | dict[str, Any] | str | None = None,
        status: str = "active",
    ) -> dict[str, Any]:
        """Persist a new monotonically versioned task packet for run_id."""
        ownership_json = _json_dumps(file_ownership, "{}")
        criteria_json = _json_dumps(acceptance_criteria, "[]")
        now = time.time()
        pkt_id = _new_id("pkt")
        with self.transaction() as conn:
            run_row = conn.execute(
                "SELECT * FROM runs WHERE id = ?;", (run_id,)
            ).fetchone()
            if run_row is None:
                raise NotFoundError(f"Run not found: {run_id}")
            if run_row["workflow_version"] not in {0, 1, 2, 3, 4, 5}:
                from orchestrator.workflow import WorkflowConflictError
                raise WorkflowConflictError("Unsupported workflow version")
            if run_row["workflow_version"] in {1, 2, 3, 4, 5} and run_row["status"] != "active":
                from orchestrator.workflow import WorkflowConflictError
                raise WorkflowConflictError("Cannot change a terminal workflow task packet")
            if run_row["workflow_version"] in {1, 2, 3, 4, 5} and conn.execute(
                    "SELECT 1 FROM workflow_steps WHERE run_id=? AND state IN ('integrating','reconciliation_required')", (run_id,)).fetchone():
                from orchestrator.workflow import WorkflowConflictError
                raise WorkflowConflictError("Reconcile the in-flight merge before changing its task packet")
            if run_row["workflow_version"] == 5 and conn.execute(
                    "SELECT 1 FROM native_actions WHERE run_id=? AND action_type='spawn_agent' AND status IN ('claimed','invoking','needs_reconciliation')",
                    (run_id,)).fetchone():
                from orchestrator.workflow import WorkflowConflictError
                raise WorkflowConflictError("Reconcile the in-flight spawn_agent before changing its task packet")
            if run_row["workflow_version"] in {4, 5}:
                from orchestrator.workflow import WorkflowService
                service = WorkflowService(self)
                for step in service._steps(run_id):
                    if 'original_bindings' in step['checkpoint']:
                        service._revision_boundary(step)

            ver_row = conn.execute(
                "SELECT COALESCE(MAX(version), 0) AS max_ver FROM task_packets WHERE run_id = ?;",
                (run_id,),
            ).fetchone()
            next_version = int(ver_row["max_ver"]) + 1
            conn.execute(
                """
                INSERT INTO task_packets (
                    id, run_id, version, title, goal,
                    file_ownership_json, acceptance_criteria_json, status, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    pkt_id,
                    run_id,
                    next_version,
                    title,
                    goal,
                    ownership_json,
                    criteria_json,
                    status,
                    now,
                ),
            )
            if run_row["workflow_version"] == 1:
                conn.execute("UPDATE workflow_steps SET state='planned', packet_version=?, checkpoint_json='{}', revision=revision+1, contract_revision=contract_revision+1 WHERE run_id=?", (next_version, run_id))
            elif run_row["workflow_version"] in {2, 3, 4, 5}:
                for step in conn.execute("SELECT id,state,checkpoint_json FROM workflow_steps WHERE run_id=?", (run_id,)).fetchall():
                    checkpoint = json.loads(step["checkpoint_json"])
                    retained = {"original_bindings": checkpoint["original_bindings"]} if "original_bindings" in checkpoint else {}
                    if run_row["workflow_version"] in {4, 5}:
                        retained.update({key: checkpoint[key] for key in (
                            "merge_history", "handoff_history", "handoff_ack_history", "findings",
                            "review_dimensions", "complexity", "execution_kind",
                            "coordinator_local_boundaries", "local_revision_boundaries",
                            "native_observations", "owner_last_dispatch_at", "last_failure_at",
                            "pending_owner_path", "pending_verifier_path", "frozen_verifier_binding",
                        ) if key in checkpoint})
                        if step['state'] == 'rework':
                            retained['admission_revision_required'] = True
                    state = step["state"] if step["state"] in {"rework", "needs_decision"} else "planned"
                    conn.execute("UPDATE workflow_steps SET state=?,packet_version=?,checkpoint_json=?,revision=revision+1,contract_revision=contract_revision+1 WHERE id=?",
                                 (state, next_version, _json_dumps(retained, "{}"), step["id"]))
            conn.execute(
                "UPDATE runs SET updated_at = ? WHERE id = ?;", (now, run_id)
            )
            row = conn.execute(
                "SELECT * FROM task_packets WHERE id = ?;", (pkt_id,)
            ).fetchone()
            assert row is not None
            return _task_packet_from_row(row)

    def get_task_packet(
        self, run_id: str, *, version: int | None = None
    ) -> dict[str, Any] | None:
        with self.read_connection() as conn:
            if version is None:
                row = conn.execute(
                    "SELECT * FROM task_packets WHERE run_id = ? ORDER BY version DESC LIMIT 1;",
                    (run_id,),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT * FROM task_packets WHERE run_id = ? AND version = ?;",
                    (run_id, int(version)),
                ).fetchone()
            return _task_packet_from_row(row) if row is not None else None

    def list_task_packets(self, run_id: str) -> list[dict[str, Any]]:
        with self.read_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM task_packets WHERE run_id = ? ORDER BY version ASC;",
                (run_id,),
            ).fetchall()
            return [_task_packet_from_row(r) for r in rows]

    # ------------------------------------------------------------------
    # Workspaces & Port Pool
    # ------------------------------------------------------------------

    def list_used_ports(
        self, *, inactive_statuses: tuple[str, ...] = ("cleaned",)
    ) -> set[int]:
        placeholders = ",".join("?" for _ in inactive_statuses)
        with self.read_connection() as conn:
            if inactive_statuses:
                rows = conn.execute(
                    f"""
                    SELECT assigned_port FROM workspaces
                    WHERE assigned_port IS NOT NULL AND status NOT IN ({placeholders});
                    """,
                    inactive_statuses,
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT assigned_port FROM workspaces WHERE assigned_port IS NOT NULL;"
                ).fetchall()
            return {int(r["assigned_port"]) for r in rows}

    def _allocate_port_in_tx(
        self,
        conn: sqlite3.Connection,
        port_range: tuple[int, int],
        exclude_ports: set[int] | None = None,
    ) -> int:
        start, end = port_range
        rows = conn.execute(
            "SELECT assigned_port FROM workspaces WHERE assigned_port IS NOT NULL AND status != 'cleaned';"
        ).fetchall()
        reserved = {int(r["assigned_port"]) for r in rows}
        if exclude_ports:
            reserved.update(exclude_ports)

        for port in range(start, end + 1):
            if port in reserved:
                continue
            if _is_port_bindable(port, self.settings.host):
                return port
        raise RuntimeError(f"No free TCP ports available in range {start}..{end}")

    def allocate_port(
        self,
        port_range: tuple[int, int] | None = None,
        *,
        exclude_ports: set[int] | None = None,
    ) -> int:
        """Select a port in port_range unused by active workspaces and bindable on localhost."""
        bounds = port_range or self.settings.port_range
        with self.transaction() as conn:
            return self._allocate_port_in_tx(conn, bounds, exclude_ports)

    def create_workspace(
        self,
        run_id: str,
        worker_name: str,
        *,
        path: str | Path,
        mode: str,
        branch_name: str | None = None,
        base_commit: str | None = None,
        assigned_port: int | None = None,
        auto_allocate_port: bool = True,
        port_range: tuple[int, int] | None = None,
        status: str = "active",
        snapshot_manifest: dict[str, Any] | list[Any] | str | None = None,
        workspace_id: str | None = None,
    ) -> dict[str, Any]:
        """Persist a workspace record and optionally allocate an unused localhost port atomically."""
        if mode not in VALID_WORKSPACE_MODES:
            raise ValueError(
                f"Invalid workspace mode {mode!r}; expected one of {sorted(VALID_WORKSPACE_MODES)}"
            )
        if status not in VALID_WORKSPACE_STATUSES:
            raise ValueError(
                f"Invalid workspace status {status!r}; expected one of {sorted(VALID_WORKSPACE_STATUSES)}"
            )

        manifest_json = _json_dumps(snapshot_manifest, "{}")
        norm_path = str(Path(path).resolve(strict=False))
        now = time.time()
        ws_id = workspace_id or _new_id("ws")
        bounds = port_range or self.settings.port_range

        with self.transaction() as conn:
            run_row = conn.execute(
                "SELECT 1 FROM runs WHERE id = ?;", (run_id,)
            ).fetchone()
            if run_row is None:
                raise NotFoundError(f"Run not found: {run_id}")

            resolved_port = assigned_port
            if resolved_port is None and auto_allocate_port:
                resolved_port = self._allocate_port_in_tx(conn, bounds)

            conn.execute(
                """
                INSERT INTO workspaces (
                    id, run_id, worker_name, path, branch_name, base_commit,
                    mode, assigned_port, status, snapshot_manifest_json, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    ws_id,
                    run_id,
                    worker_name,
                    norm_path,
                    branch_name,
                    base_commit,
                    mode,
                    resolved_port,
                    status,
                    manifest_json,
                    now,
                    now,
                ),
            )
            # Link matching registered worker if it does not yet have a workspace_id.
            conn.execute(
                """
                UPDATE workers
                SET workspace_id = COALESCE(workspace_id, ?), updated_at = ?
                WHERE run_id = ? AND name = ?;
                """,
                (ws_id, now, run_id, worker_name),
            )
            row = conn.execute(
                "SELECT * FROM workspaces WHERE id = ?;", (ws_id,)
            ).fetchone()
            assert row is not None
            return _workspace_from_row(row)

    def get_workspace(self, workspace_id: str) -> dict[str, Any] | None:
        with self.read_connection() as conn:
            row = conn.execute(
                "SELECT * FROM workspaces WHERE id = ?;", (workspace_id,)
            ).fetchone()
            return _workspace_from_row(row) if row is not None else None

    def get_workspace_by_worker(
        self,
        run_id: str,
        worker_name: str,
        *,
        include_cleaned: bool = False,
    ) -> dict[str, Any] | None:
        with self.read_connection() as conn:
            if include_cleaned:
                row = conn.execute(
                    """
                    SELECT * FROM workspaces
                    WHERE run_id = ? AND worker_name = ?
                    ORDER BY created_at DESC LIMIT 1;
                    """,
                    (run_id, worker_name),
                ).fetchone()
            else:
                row = conn.execute(
                    """
                    SELECT * FROM workspaces
                    WHERE run_id = ? AND worker_name = ? AND status != 'cleaned'
                    ORDER BY created_at DESC LIMIT 1;
                    """,
                    (run_id, worker_name),
                ).fetchone()
            return _workspace_from_row(row) if row is not None else None

    def list_workspaces(
        self,
        *,
        run_id: str | None = None,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if run_id is not None:
            clauses.append("run_id = ?")
            params.append(run_id)
        if status is not None:
            clauses.append("status = ?")
            params.append(status)
        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self.read_connection() as conn:
            rows = conn.execute(
                f"SELECT * FROM workspaces {where_sql} ORDER BY created_at ASC;",
                params,
            ).fetchall()
            return [_workspace_from_row(r) for r in rows]

    def update_workspace(
        self,
        workspace_id: str,
        *,
        status: str | None = None,
        branch_name: str | None = None,
        base_commit: str | None = None,
        merged_commit: str | None = None,
        merged_target_commit: str | None = None,
        assigned_port: int | None = None,
        path: str | Path | None = None,
        snapshot_manifest: dict[str, Any] | list[Any] | str | None = None,
    ) -> dict[str, Any]:
        if status is not None and status not in VALID_WORKSPACE_STATUSES:
            raise ValueError(
                f"Invalid workspace status {status!r}; expected one of {sorted(VALID_WORKSPACE_STATUSES)}"
            )
        now = time.time()
        with self.transaction() as conn:
            existing = conn.execute(
                "SELECT * FROM workspaces WHERE id = ?;", (workspace_id,)
            ).fetchone()
            if existing is None:
                raise NotFoundError(f"Workspace not found: {workspace_id}")

            new_status = status if status is not None else existing["status"]
            new_branch = branch_name if branch_name is not None else existing["branch_name"]
            new_commit = base_commit if base_commit is not None else existing["base_commit"]
            new_merged_commit = merged_commit if merged_commit is not None else existing["merged_commit"]
            new_merged_target = merged_target_commit if merged_target_commit is not None else existing["merged_target_commit"]
            new_port = assigned_port if assigned_port is not None else existing["assigned_port"]
            new_path = (
                str(Path(path).resolve(strict=False))
                if path is not None
                else existing["path"]
            )
            new_manifest = (
                _json_dumps(snapshot_manifest, "{}")
                if snapshot_manifest is not None
                else existing["snapshot_manifest_json"]
            )
            conn.execute(
                """
                UPDATE workspaces
                SET status = ?, branch_name = ?, base_commit = ?, merged_commit = ?, merged_target_commit = ?, assigned_port = ?,
                    path = ?, snapshot_manifest_json = ?, updated_at = ?
                WHERE id = ?;
                """,
                (
                    new_status,
                    new_branch,
                    new_commit,
                    new_merged_commit,
                    new_merged_target,
                    new_port,
                    new_path,
                    new_manifest,
                    now,
                    workspace_id,
                ),
            )
            row = conn.execute(
                "SELECT * FROM workspaces WHERE id = ?;", (workspace_id,)
            ).fetchone()
            assert row is not None
            return _workspace_from_row(row)

    # ------------------------------------------------------------------
    # Workers
    # ------------------------------------------------------------------

    @staticmethod
    def resolve_pending_agent_path(
        *,
        task_name: str | None = None,
        pending_agent_path: str | None = None,
    ) -> str | None:
        resolved: str | None = None
        if task_name is not None:
            if not isinstance(task_name, str) or not TASK_NAME_RE.fullmatch(task_name):
                raise ValueError(
                    f"Invalid task_name {task_name!r}; must match ^[a-z0-9_-]{{1,48}}$"
                )
            resolved = f"/root/{task_name}"
        if pending_agent_path is not None:
            if not isinstance(pending_agent_path, str) or not PENDING_AGENT_PATH_RE.fullmatch(pending_agent_path):
                raise ValueError(
                    f"Invalid pending_agent_path {pending_agent_path!r}; must match ^/root/[a-z0-9_-]{{1,48}}$"
                )
            if resolved is not None and pending_agent_path != resolved:
                raise ValueError("task_name and pending_agent_path do not match")
            resolved = pending_agent_path
        return resolved

    def upsert_worker(
        self,
        run_id: str,
        name: str,
        *,
        role: str,
        agent_id: str | None = None,
        task_name: str | None = None,
        pending_agent_path: str | None = None,
        status: str = "waiting",
        workspace_id: str | None = None,
        current_task: str = "",
        stall_threshold_sec: int | None = None,
        heartbeat_at: float | None = None,
        worker_id: str | None = None,
    ) -> dict[str, Any]:
        if agent_id is not None:
            if not isinstance(agent_id, str) or not agent_id.strip():
                raise ValueError("agent_id must be non-empty")
        resolved_pending_path = self.resolve_pending_agent_path(
            task_name=task_name, pending_agent_path=pending_agent_path
        )
        if agent_id is not None and resolved_pending_path is not None and agent_id != resolved_pending_path:
            raise ValueError("agent_id does not match task_name / pending_agent_path")
        if role not in VALID_WORKER_ROLES:
            raise ValueError(
                f"Invalid worker role {role!r}; expected one of {sorted(VALID_WORKER_ROLES)}"
            )
        if status not in VALID_WORKER_STATUSES:
            raise ValueError(
                f"Invalid worker status {status!r}; expected one of {sorted(VALID_WORKER_STATUSES)}"
            )
        now = float(heartbeat_at) if heartbeat_at is not None else time.time()
        threshold = (
            int(stall_threshold_sec)
            if stall_threshold_sec is not None
            else int(self.settings.stall_sec)
        )
        if threshold <= 0:
            raise ValueError(f"stall_threshold_sec must be > 0, got {threshold}")

        with self.transaction() as conn:
            run_row = conn.execute(
                "SELECT workflow_version, mode FROM runs WHERE id = ?;", (run_id,)
            ).fetchone()
            if run_row is None:
                raise NotFoundError(f"Run not found: {run_id}")
            if resolved_pending_path is not None and run_row["workflow_version"] != 5:
                raise ValueError("Pre-spawn task_name/pending_agent_path requires workflow5")
            if resolved_pending_path is not None:
                dup = conn.execute(
                    """
                    SELECT name FROM workers
                    WHERE run_id = ? AND name != ?
                      AND (pending_agent_path = ? OR agent_id = ?)
                    LIMIT 1;
                    """,
                    (run_id, name, resolved_pending_path, resolved_pending_path),
                ).fetchone()
                if dup is not None:
                    raise ValueError(
                        f"Duplicate task_name / agent path {resolved_pending_path!r} in run {run_id} (already used by {dup['name']!r})"
                    )

            existing = conn.execute(
                "SELECT * FROM workers WHERE run_id = ? AND name = ?;",
                (run_id, name),
            ).fetchone()

            if existing is not None:
                if existing["agent_id"] is not None:
                    if agent_id is not None and agent_id != existing["agent_id"]:
                        raise ValueError("Native agent binding is immutable; register a new worker name")
                    if resolved_pending_path is not None and resolved_pending_path != existing["agent_id"]:
                        raise ValueError("Native agent binding is immutable; register a new worker name")
                    final_agent_id: str | None = existing["agent_id"]
                    final_pending_path: str | None = None
                else:
                    unsettled = conn.execute(
                        "SELECT 1 FROM native_actions WHERE run_id = ? AND recipient = ? AND status IN ('claimed','invoking','needs_reconciliation') LIMIT 1;",
                        (run_id, name),
                    ).fetchone()
                    if unsettled is not None:
                        if agent_id is not None or (
                            resolved_pending_path is not None
                            and resolved_pending_path != existing["pending_agent_path"]
                        ):
                            from orchestrator.native_actions import NativeActionConflictError
                            raise NativeActionConflictError(
                                f"Worker {name!r} has an unsettled native action requiring completion or reconciliation"
                            )
                    if agent_id is not None:
                        if (
                            existing["pending_agent_path"] is not None
                            and agent_id != existing["pending_agent_path"]
                        ):
                            raise ValueError(
                                f"agent_id {agent_id!r} does not match pre-frozen path {existing['pending_agent_path']!r}"
                            )
                        final_agent_id = agent_id
                        final_pending_path = None
                    else:
                        if (
                            existing["pending_agent_path"] is not None
                            and resolved_pending_path is not None
                            and resolved_pending_path != existing["pending_agent_path"]
                        ):
                            active_step = conn.execute(
                                "SELECT 1 FROM workflow_steps WHERE run_id = ? AND (owner = ? OR verifier = ?) AND state != 'planned' LIMIT 1;",
                                (run_id, name, name),
                            ).fetchone()
                            if active_step is not None:
                                raise ValueError(
                                    f"Cannot change pre-frozen path for worker {name!r} on an active workflow step"
                                )
                        final_agent_id = None
                        final_pending_path = (
                            resolved_pending_path
                            if resolved_pending_path is not None
                            else existing["pending_agent_path"]
                        )

                self._reject_pending(conn, existing["id"])
                if existing["status"] == "killed" and status != "killed":
                    raise ValueError(
                        f"Worker {name!r} is killed and cannot transition to {status!r}"
                    )
                resolved_ws = workspace_id if workspace_id is not None else existing["workspace_id"]
                conn.execute(
                    """
                    UPDATE workers
                    SET agent_id = ?, pending_agent_path = ?, role = ?, status = ?, workspace_id = ?, current_task = ?,
                        last_heartbeat_at = ?, stall_threshold_sec = ?,
                        stalled_alerted = 0, updated_at = ?
                    WHERE id = ?;
                    """,
                    (
                        final_agent_id,
                        final_pending_path,
                        role,
                        status,
                        resolved_ws,
                        current_task if current_task else existing["current_task"],
                        now,
                        threshold,
                        now,
                        existing["id"],
                    ),
                )
                row = conn.execute(
                    "SELECT * FROM workers WHERE id = ?;", (existing["id"],)
                ).fetchone()
                assert row is not None
                return _worker_from_row(row)

            resolved_ws = workspace_id
            if resolved_ws is None:
                ws_row = conn.execute(
                    """
                    SELECT id FROM workspaces
                    WHERE run_id = ? AND worker_name = ? AND status != 'cleaned'
                    ORDER BY created_at DESC LIMIT 1;
                    """,
                    (run_id, name),
                ).fetchone()
                if ws_row is not None:
                    resolved_ws = ws_row["id"]

            wid = worker_id or _new_id("wrk")
            final_pending = None if agent_id is not None else resolved_pending_path
            conn.execute(
                """
                INSERT INTO workers (
                    id, run_id, name, role, agent_id, pending_agent_path, status, workspace_id, current_task,
                    last_heartbeat_at, stall_threshold_sec, stalled_alerted,
                    created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?);
                """,
                (
                    wid,
                    run_id,
                    name,
                    role,
                    agent_id,
                    final_pending,
                    status,
                    resolved_ws,
                    current_task,
                    now,
                    threshold,
                    now,
                    now,
                ),
            )
            row = conn.execute("SELECT * FROM workers WHERE id = ?;", (wid,)).fetchone()
            assert row is not None
            return _worker_from_row(row)

    @staticmethod
    def _reject_pending(conn, worker_id):
        if conn.execute("SELECT 1 FROM worker_controls WHERE worker_id = ? AND status = 'pending'", (worker_id,)).fetchone():
            raise ValueError("Worker has a pending control request; acknowledge it first")

    def request_worker_control(self, worker_id: str, status: str) -> dict[str, Any]:
        if status not in {"stopped", "killed"}:
            raise ValueError("Control status must be stopped or killed")
        with self.transaction() as conn:
            worker = self._resolve_worker_in_tx(conn, worker_id=worker_id)
            if not worker["agent_id"]:
                raise ValueError("Worker has no native agent_id binding")
            if conn.execute("SELECT status FROM runs WHERE id = ?", (worker["run_id"],)).fetchone()[0] != "active":
                raise ValueError("Run is not active")
            pending = conn.execute("SELECT * FROM worker_controls WHERE worker_id = ? AND status = 'pending'", (worker_id,)).fetchone()
            if pending:
                if pending["requested_status"] == status:
                    return dict(pending)
                raise ValueError("Conflicting pending control request")
            if worker["status"] == "killed":
                raise ValueError("Killed worker is terminal")
            request_id = _new_id("ctl")
            conn.execute("INSERT INTO worker_controls VALUES (?, ?, ?, ?, ?, 'pending', NULL, ?, NULL)",
                         (request_id, worker["run_id"], worker_id, worker["agent_id"], status, time.time()))
            return dict(conn.execute("SELECT * FROM worker_controls WHERE id = ?", (request_id,)).fetchone())

    def list_worker_controls(self, run_id: str) -> list[dict[str, Any]]:
        with self.read_connection() as conn:
            return [dict(r) for r in conn.execute("SELECT * FROM worker_controls WHERE run_id = ? ORDER BY created_at, id", (run_id,))]

    def worker_control_ack(self, request_id: str, outcome: str, result: str) -> dict[str, Any]:
        if outcome not in {"applied", "failed"} or not isinstance(result, str) or not result.strip():
            raise ValueError("Acknowledgement requires applied/failed and non-empty native evidence")
        with self.transaction() as conn:
            request = conn.execute("SELECT * FROM worker_controls WHERE id = ?", (request_id,)).fetchone()
            if request is None:
                raise NotFoundError(f"Control request not found: {request_id}")
            if request["status"] != "pending":
                if request["status"] == outcome and request["result"] == result:
                    return dict(request)
                raise ValueError("Contradictory control acknowledgement")
            worker = self._resolve_worker_in_tx(conn, worker_id=request["worker_id"])
            if worker["agent_id"] != request["agent_id"]:
                raise ValueError("Native agent binding changed")
            now = time.time()
            conn.execute("UPDATE worker_controls SET status = ?, result = ?, resolved_at = ? WHERE id = ?", (outcome, result, now, request_id))
            if outcome == "applied":
                self.update_worker_status(worker_id=worker["id"], status=request["requested_status"])
            return dict(conn.execute("SELECT * FROM worker_controls WHERE id = ?", (request_id,)).fetchone())

    def get_worker(self, worker_id: str) -> dict[str, Any] | None:
        with self.read_connection() as conn:
            row = conn.execute(
                "SELECT * FROM workers WHERE id = ?;", (worker_id,)
            ).fetchone()
            return _worker_from_row(row) if row is not None else None

    def get_worker_by_name(self, run_id: str, name: str) -> dict[str, Any] | None:
        with self.read_connection() as conn:
            row = conn.execute(
                "SELECT * FROM workers WHERE run_id = ? AND name = ?;",
                (run_id, name),
            ).fetchone()
            return _worker_from_row(row) if row is not None else None

    def list_workers(
        self, *, run_id: str | None = None, status: str | None = None
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if run_id is not None:
            clauses.append("run_id = ?")
            params.append(run_id)
        if status is not None:
            clauses.append("status = ?")
            params.append(status)
        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self.read_connection() as conn:
            rows = conn.execute(
                f"SELECT * FROM workers {where_sql} ORDER BY created_at ASC, name ASC;",
                params,
            ).fetchall()
            return [_worker_from_row(r) for r in rows]

    def _resolve_worker_in_tx(
        self,
        conn: sqlite3.Connection,
        *,
        worker_id: str | None = None,
        run_id: str | None = None,
        worker_name: str | None = None,
    ) -> sqlite3.Row:
        if worker_id is not None:
            row = conn.execute(
                "SELECT * FROM workers WHERE id = ?;", (worker_id,)
            ).fetchone()
            if row is None:
                raise NotFoundError(f"Worker not found: {worker_id}")
            return row
        if run_id is not None and worker_name is not None:
            row = conn.execute(
                "SELECT * FROM workers WHERE run_id = ? AND name = ?;",
                (run_id, worker_name),
            ).fetchone()
            if row is None:
                raise NotFoundError(
                    f"Worker not found in run {run_id}: {worker_name}"
                )
            return row
        raise ValueError("Specify either worker_id or both run_id and worker_name")

    def update_worker_heartbeat(
        self,
        *,
        worker_id: str | None = None,
        run_id: str | None = None,
        worker_name: str | None = None,
        current_task: str | None = None,
        status: str | None = None,
        heartbeat_at: float | None = None,
    ) -> dict[str, Any]:
        if status is not None and status not in VALID_WORKER_STATUSES:
            raise ValueError(
                f"Invalid worker status {status!r}; expected one of {sorted(VALID_WORKER_STATUSES)}"
            )
        now = float(heartbeat_at) if heartbeat_at is not None else time.time()
        with self.transaction() as conn:
            existing = self._resolve_worker_in_tx(
                conn, worker_id=worker_id, run_id=run_id, worker_name=worker_name
            )
            self._reject_pending(conn, existing["id"])
            if existing["status"] in {"stopped", "killed"}:
                raise ValueError(
                    f"Cannot record heartbeat for killed worker {existing['name']!r}"
                )
            new_status = status if status is not None else existing["status"]
            new_task = current_task if current_task is not None else existing["current_task"]
            conn.execute(
                """
                UPDATE workers
                SET status = ?, current_task = ?, last_heartbeat_at = ?,
                    stalled_alerted = 0, updated_at = ?
                WHERE id = ?;
                """,
                (new_status, new_task, now, now, existing["id"]),
            )
            row = conn.execute(
                "SELECT * FROM workers WHERE id = ?;", (existing["id"],)
            ).fetchone()
            assert row is not None
            return _worker_from_row(row)

    def update_worker_status(
        self,
        *,
        status: str,
        worker_id: str | None = None,
        run_id: str | None = None,
        worker_name: str | None = None,
        current_task: str | None = None,
        workspace_id: str | None = None,
    ) -> dict[str, Any]:
        if status not in VALID_WORKER_STATUSES:
            raise ValueError(
                f"Invalid worker status {status!r}; expected one of {sorted(VALID_WORKER_STATUSES)}"
            )
        now = time.time()
        with self.transaction() as conn:
            existing = self._resolve_worker_in_tx(
                conn, worker_id=worker_id, run_id=run_id, worker_name=worker_name
            )
            self._reject_pending(conn, existing["id"])
            if existing["status"] == "killed" and status != "killed":
                raise ValueError(
                    f"Worker {existing['name']!r} is killed and cannot transition to {status!r}"
                )
            new_task = current_task if current_task is not None else existing["current_task"]
            new_ws = workspace_id if workspace_id is not None else existing["workspace_id"]
            stalled_flag = 0
            # Transitioning to working refreshes heartbeat so a freshly resumed worker isn't immediately stalled.
            new_hb = now if status == "working" else existing["last_heartbeat_at"]
            conn.execute(
                """
                UPDATE workers
                SET status = ?, current_task = ?, workspace_id = ?,
                    last_heartbeat_at = ?, stalled_alerted = ?, updated_at = ?
                WHERE id = ?;
                """,
                (status, new_task, new_ws, new_hb, stalled_flag, now, existing["id"]),
            )
            row = conn.execute(
                "SELECT * FROM workers WHERE id = ?;", (existing["id"],)
            ).fetchone()
            assert row is not None
            return _worker_from_row(row)

    def find_stalled_workers(
        self, *, now: float | None = None, run_id: str | None = None
    ) -> list[dict[str, Any]]:
        """Return active 'working' workers whose heartbeat exceeded stall_threshold_sec and haven't alerted."""
        ts = float(now) if now is not None else time.time()
        clauses = [
            "status = 'working'",
            "run_id IN (SELECT id FROM runs WHERE status = 'active')",
            "stalled_alerted = 0",
            "(? - last_heartbeat_at) > stall_threshold_sec",
        ]
        params: list[Any] = [ts]
        if run_id is not None:
            clauses.append("run_id = ?")
            params.append(run_id)
        where_sql = " AND ".join(clauses)
        with self.read_connection() as conn:
            rows = conn.execute(
                f"SELECT * FROM workers WHERE {where_sql} ORDER BY last_heartbeat_at ASC;",
                params,
            ).fetchall()
            return [_worker_from_row(r) for r in rows]

    def mark_worker_stalled_alerted(
        self, worker_id: str, *, alerted: bool = True
    ) -> dict[str, Any]:
        now = time.time()
        with self.transaction() as conn:
            existing = conn.execute(
                "SELECT * FROM workers WHERE id = ?;", (worker_id,)
            ).fetchone()
            if existing is None:
                raise NotFoundError(f"Worker not found: {worker_id}")
            conn.execute(
                "UPDATE workers SET stalled_alerted = ?, updated_at = ? WHERE id = ?;",
                (1 if alerted else 0, now, worker_id),
            )
            row = conn.execute(
                "SELECT * FROM workers WHERE id = ?;", (worker_id,)
            ).fetchone()
            assert row is not None
            return _worker_from_row(row)

    # ------------------------------------------------------------------
    # Board Messages & Read Cursors
    # ------------------------------------------------------------------

    def insert_board_message(
        self,
        run_id: str,
        *,
        sender: str,
        sender_role: str = "",
        channel: str = "main",
        mentions: list[str] | str | None = None,
        msg_type: str = "info",
        summary: str = "",
        body_md: str = "",
        created_at: float | None = None,
    ) -> dict[str, Any]:
        if msg_type not in VALID_MSG_TYPES:
            raise ValueError(
                f"Invalid msg_type {msg_type!r}; expected one of {sorted(VALID_MSG_TYPES)}"
            )
        mentions_json = _json_dumps(mentions, "[]")
        ts = float(created_at) if created_at is not None else time.time()
        resolved_channel = channel.strip() or "main"

        with self.transaction() as conn:
            run_row = conn.execute(
                "SELECT 1 FROM runs WHERE id = ?;", (run_id,)
            ).fetchone()
            if run_row is None:
                raise NotFoundError(f"Run not found: {run_id}")

            cur = conn.execute(
                """
                INSERT INTO board_messages (
                    run_id, channel, sender, sender_role,
                    mentions_json, msg_type, summary, body_md, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    run_id,
                    resolved_channel,
                    sender,
                    sender_role,
                    mentions_json,
                    msg_type,
                    summary,
                    body_md,
                    ts,
                ),
            )
            seq = int(cur.lastrowid)
            conn.execute(
                "UPDATE runs SET updated_at = ? WHERE id = ?;", (ts, run_id)
            )
            row = conn.execute(
                "SELECT * FROM board_messages WHERE seq = ?;", (seq,)
            ).fetchone()
            assert row is not None
            return _board_message_from_row(row)

    def get_board_message(self, seq: int) -> dict[str, Any] | None:
        with self.read_connection() as conn:
            row = conn.execute(
                "SELECT * FROM board_messages WHERE seq = ?;", (int(seq),)
            ).fetchone()
            return _board_message_from_row(row) if row is not None else None

    def query_board_messages(
        self,
        run_id: str,
        *,
        since_seq: int = 0,
        channel: str | None = None,
        mention: str | None = None,
        msg_type: str | None = None,
        limit: int | None = 100,
    ) -> list[dict[str, Any]]:
        clauses = ["run_id = ?", "seq > ?"]
        params: list[Any] = [run_id, max(0, int(since_seq))]

        if channel is not None and channel.strip() not in ("", "all"):
            clauses.append("channel = ?")
            params.append(channel.strip())
        if msg_type is not None and msg_type.strip():
            clauses.append("msg_type = ?")
            params.append(msg_type.strip())
        if mention is not None and mention.lstrip("@").strip():
            target = mention.lstrip("@").strip()
            clauses.append(
                "EXISTS (SELECT 1 FROM json_each(mentions_json) WHERE value = ?)"
            )
            params.append(target)

        where_sql = " AND ".join(clauses)
        limit_sql = ""
        if limit is not None:
            limit_sql = " LIMIT ?"
            params.append(max(1, int(limit)))
        with self.read_connection() as conn:
            rows = conn.execute(
                f"SELECT * FROM board_messages WHERE {where_sql} ORDER BY seq ASC{limit_sql};",
                params,
            ).fetchall()
            return [_board_message_from_row(r) for r in rows]

    def get_latest_seq(self, run_id: str) -> int:
        with self.read_connection() as conn:
            row = conn.execute(
                "SELECT COALESCE(MAX(seq), 0) AS latest_seq FROM board_messages WHERE run_id = ?;",
                (run_id,),
            ).fetchone()
            return int(row["latest_seq"]) if row is not None else 0

    def get_read_cursor(
        self, run_id: str, reader_name: str, channel: str = "main"
    ) -> int:
        with self.read_connection() as conn:
            row = conn.execute(
                """
                SELECT last_seq FROM read_cursors
                WHERE run_id = ? AND reader_name = ? AND channel = ?;
                """,
                (run_id, reader_name, channel),
            ).fetchone()
            return int(row["last_seq"]) if row is not None else 0

    def list_read_cursors(self, run_id: str) -> dict[tuple[str, str], int]:
        with self.read_connection() as conn:
            rows = conn.execute(
                "SELECT reader_name, channel, last_seq FROM read_cursors WHERE run_id = ?;",
                (run_id,),
            ).fetchall()
            return {(r["reader_name"], r["channel"]): int(r["last_seq"]) for r in rows}

    def set_read_cursor(
        self,
        run_id: str,
        reader_name: str,
        channel: str = "main",
        last_seq: int = 0,
    ) -> int:
        now = time.time()
        seq_val = max(0, int(last_seq))
        with self.transaction() as conn:
            conn.execute(
                """
                INSERT INTO read_cursors (run_id, reader_name, channel, last_seq, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT (run_id, reader_name, channel)
                DO UPDATE SET
                    last_seq = MAX(read_cursors.last_seq, excluded.last_seq),
                    updated_at = excluded.updated_at;
                """,
                (run_id, reader_name, channel, seq_val, now),
            )
            row = conn.execute(
                """
                SELECT last_seq FROM read_cursors
                WHERE run_id = ? AND reader_name = ? AND channel = ?;
                """,
                (run_id, reader_name, channel),
            ).fetchone()
            return int(row["last_seq"]) if row is not None else seq_val

    def list_channels(self, run_id: str) -> list[dict[str, Any]]:
        with self.read_connection() as conn:
            rows = conn.execute(
                """
                SELECT
                    channel,
                    COUNT(*) AS message_count,
                    MAX(seq) AS latest_seq,
                    MAX(created_at) AS last_message_at
                FROM board_messages
                WHERE run_id = ?
                GROUP BY channel
                ORDER BY MIN(seq) ASC;
                """,
                (run_id,),
            ).fetchall()
            return [
                {
                    "channel": r["channel"],
                    "message_count": int(r["message_count"]),
                    "latest_seq": int(r["latest_seq"]),
                    "last_message_at": r["last_message_at"],
                }
                for r in rows
            ]

    # ------------------------------------------------------------------
    # Session Run Bindings (M9 Lifecycle Hooks)
    # ------------------------------------------------------------------

    @staticmethod
    def _session_binding_from_row(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "session_id": row["session_id"],
            "run_id": row["run_id"],
            "project_root": row["project_root"],
            "last_stop_blocked_key": row["last_stop_blocked_key"],
            "updated_at": row["updated_at"],
            "is_primary": bool(row["is_primary"]),
        }

    @staticmethod
    def _next_session_seq(conn: sqlite3.Connection, session_id: str) -> int:
        return int(conn.execute(
            "SELECT COALESCE(MAX(session_seq), 0) + 1 FROM runs WHERE session_id = ?", (session_id,)
        ).fetchone()[0])

    _SESSION_BINDING_ORDER = """
        ORDER BY b.is_primary DESC, (r.status = 'active') DESC, b.updated_at DESC, b.rowid DESC
    """

    @staticmethod
    def _session_binding_row(
        conn: sqlite3.Connection, session_id: str, run_id: str | None
    ) -> sqlite3.Row | None:
        if run_id is not None:
            return conn.execute(
                "SELECT * FROM session_run_bindings WHERE session_id = ? AND run_id = ?",
                (session_id, run_id),
            ).fetchone()
        return conn.execute("""
            SELECT b.* FROM session_run_bindings AS b LEFT JOIN runs AS r ON r.id = b.run_id
            WHERE b.session_id = ? AND b.is_primary = 1
        """ + Database._SESSION_BINDING_ORDER + " LIMIT 1", (session_id,)).fetchone()

    def _upsert_session_binding_in_tx(
        self,
        conn: sqlite3.Connection,
        *,
        session_id: str,
        run_id: str,
        project_root: str,
        now: float | None = None,
        is_primary: bool = True,
    ) -> dict[str, Any]:
        ts = float(now) if now is not None else time.time()
        conn.execute("""
            INSERT INTO session_run_bindings (session_id, run_id, project_root, updated_at, is_primary)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT (session_id, run_id) DO UPDATE SET
                project_root = excluded.project_root, updated_at = excluded.updated_at,
                is_primary = excluded.is_primary
        """, (session_id, run_id, self._normalize_root(project_root), ts, int(is_primary)))
        row = self._session_binding_row(conn, session_id, run_id)
        assert row is not None
        return self._session_binding_from_row(row)

    def upsert_session_binding(
        self,
        session_id: str,
        run_id: str,
        project_root: str | Path | None = None,
        *,
        is_primary: bool = True,
    ) -> dict[str, Any]:
        if not isinstance(session_id, str) or not session_id.strip():
            raise ValueError("session_id must be a non-empty string")
        if not isinstance(run_id, str) or not run_id.strip():
            raise ValueError("run_id must be a non-empty string")
        clean_session = session_id.strip()
        clean_run = run_id.strip()
        with self.transaction() as conn:
            run_row = conn.execute("""
                SELECT r.session_id, p.root_path FROM runs AS r JOIN projects AS p ON p.id = r.project_id
                WHERE r.id = ?
            """, (clean_run,)).fetchone()
            if run_row is None:
                raise NotFoundError(f"Run not found: {clean_run}")
            if not run_row["session_id"]:
                conn.execute(
                    "UPDATE runs SET session_id = ?, session_seq = ? WHERE id = ?",
                    (clean_session, self._next_session_seq(conn, clean_session), clean_run),
                )
            resolved_root = run_row["root_path"]
            if project_root is not None and str(project_root).strip():
                resolved_root = str(project_root)
            return self._upsert_session_binding_in_tx(
                conn, session_id=clean_session, run_id=clean_run,
                project_root=resolved_root, is_primary=is_primary,
            )

    def get_session_binding(self, session_id: str, run_id: str | None = None) -> dict[str, Any] | None:
        if not isinstance(session_id, str) or not session_id.strip():
            return None
        with self.read_connection() as conn:
            row = self._session_binding_row(conn, session_id.strip(), run_id)
            return self._session_binding_from_row(row) if row is not None else None

    def list_session_bindings(self, session_id: str) -> list[dict[str, Any]]:
        with self.read_connection() as conn:
            rows = conn.execute("""
                SELECT b.* FROM session_run_bindings AS b LEFT JOIN runs AS r ON r.id = b.run_id
                WHERE b.session_id = ?
            """ + self._SESSION_BINDING_ORDER, (session_id.strip(),))
            return [self._session_binding_from_row(row) for row in rows]

    def update_session_stop_blocked_key(
        self, session_id: str, blocked_key: str | None, *, run_id: str | None = None
    ) -> dict[str, Any]:
        if not isinstance(session_id, str) or not session_id.strip():
            raise ValueError("session_id must be a non-empty string")
        clean_session = session_id.strip()
        with self.transaction() as conn:
            existing = self._session_binding_row(conn, clean_session, run_id)
            if existing is None:
                raise NotFoundError(f"Session binding not found: {clean_session}")
            bound_run_id = existing["run_id"]
            conn.execute("""
                UPDATE session_run_bindings SET last_stop_blocked_key = ?, updated_at = ?
                WHERE session_id = ? AND run_id = ?
            """, (blocked_key, time.time(), clean_session, bound_run_id))
            row = self._session_binding_row(conn, clean_session, bound_run_id)
            assert row is not None
            return self._session_binding_from_row(row)

    def upsert_session_turn(
        self,
        session_id: str,
        turn_id: str,
        *,
        cwd: str,
        prompt_preview: str,
        active_run_id: str | None = None,
    ) -> dict[str, Any]:
        if not isinstance(session_id, str) or not session_id.strip():
            raise ValueError("session_id must be a non-empty string")
        if not isinstance(turn_id, str) or not turn_id.strip():
            raise ValueError("turn_id must be a non-empty string")
        clean_session, clean_turn = session_id.strip(), turn_id.strip()
        with self.transaction() as conn:
            now = time.time()
            conn.execute("""
                INSERT INTO session_turns
                    (session_id, turn_id, cwd, prompt_preview, active_run_id, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (session_id, turn_id) DO UPDATE SET
                    cwd = excluded.cwd, prompt_preview = excluded.prompt_preview,
                    active_run_id = excluded.active_run_id, updated_at = excluded.updated_at
            """, (clean_session, clean_turn, cwd, prompt_preview, active_run_id, now, now))
            row = conn.execute(
                "SELECT * FROM session_turns WHERE session_id = ? AND turn_id = ?",
                (clean_session, clean_turn),
            ).fetchone()
            assert row is not None
            return dict(row)

    def list_session_turns(self, session_id: str) -> list[dict[str, Any]]:
        with self.read_connection() as conn:
            rows = conn.execute("""
                SELECT * FROM session_turns WHERE session_id = ?
                ORDER BY updated_at DESC, rowid DESC
            """, (session_id.strip(),))
            return [dict(row) for row in rows]
