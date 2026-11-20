"""Configuration loader and runtime settings for tri-orchestrator."""

from __future__ import annotations

import math
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 18790
DEFAULT_STALL_SEC = 300
DEFAULT_WATCHDOG_INTERVAL_SEC = 15
DEFAULT_PORT_RANGE = (18800, 18999)
DEFAULT_MAX_SNAPSHOT_MB = 500
DEFAULT_GC_INTERVAL_SEC = 3600.0
DEFAULT_RESULT_TTL_SEC = 86400
DEFAULT_RESULT_MAX_FILES = 50
DEFAULT_WORKSPACE_RETENTION_SEC = 86400

_PORT_RANGE_RE = re.compile(r"^\s*(\d+)\s*(?:\.\.|-|:|,)\s*(\d+)\s*$")


def default_data_dir() -> Path:
    """Return the default per-user runtime data directory (%LOCALAPPDATA%\\tri-orchestrator)."""
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(os.path.expandvars(local_app_data)).expanduser() / "tri-orchestrator"
    if os.name == "nt":
        return Path.home() / "AppData" / "Local" / "tri-orchestrator"
    return Path.home() / ".local" / "share" / "tri-orchestrator"


def parse_port_range(raw: str | tuple[int, int] | list[int]) -> tuple[int, int]:
    """Parse a port range such as '18800..18999' or (18800, 18999) into a validated pair."""
    if isinstance(raw, str):
        match = _PORT_RANGE_RE.match(raw)
        if not match:
            raise ValueError(f"Invalid TRI_ORCH_PORT_RANGE format: {raw!r} (expected e.g. '18800..18999')")
        start, end = int(match.group(1)), int(match.group(2))
    elif isinstance(raw, (tuple, list)) and len(raw) == 2:
        start, end = int(raw[0]), int(raw[1])
    else:
        raise ValueError(f"Invalid port_range value: {raw!r}")

    if not (1 <= start <= 65535 and 1 <= end <= 65535):
        raise ValueError(f"Port range bounds must be within 1..65535, got {(start, end)}")
    if start > end:
        raise ValueError(f"Port range start must be <= end, got {(start, end)}")
    return (start, end)


def _parse_positive_number(raw: str, env_name: str) -> int | float:
    text = raw.strip()
    try:
        value: int | float = float(text) if ("." in text or "e" in text.lower()) else int(text)
    except ValueError as exc:
        raise ValueError(f"Invalid numeric value for {env_name}: {raw!r}") from exc
    if not math.isfinite(float(value)) or value <= 0:
        raise ValueError(f"{env_name} must be > 0, got {value}")
    return value


def _parse_non_negative_int(raw: str, env_name: str) -> int:
    text = raw.strip()
    try:
        value = int(text)
    except ValueError as exc:
        raise ValueError(f"Invalid integer value for {env_name}: {raw!r}") from exc
    if value < 0:
        raise ValueError(f"{env_name} must be >= 0, got {value}")
    return value


def _normalize_path(raw: str | Path) -> Path:
    text = os.path.expandvars(str(raw))
    return Path(text).expanduser()


@dataclass(slots=True)
class Settings:
    """Runtime settings for daemon, SQLite state store, watchdog, and workspace port pool."""

    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    data_dir: Path = field(default_factory=default_data_dir)
    stall_sec: int | float = DEFAULT_STALL_SEC
    watchdog_interval_sec: int | float = DEFAULT_WATCHDOG_INTERVAL_SEC
    port_range: tuple[int, int] = DEFAULT_PORT_RANGE
    max_snapshot_mb: int = DEFAULT_MAX_SNAPSHOT_MB
    gc_interval_sec: float = DEFAULT_GC_INTERVAL_SEC
    result_ttl_sec: int = DEFAULT_RESULT_TTL_SEC
    result_max_files: int = DEFAULT_RESULT_MAX_FILES
    workspace_retention_sec: int = DEFAULT_WORKSPACE_RETENTION_SEC

    def __post_init__(self) -> None:
        self.host = str(self.host).strip() or DEFAULT_HOST
        self.port = int(self.port)
        if not (1 <= self.port <= 65535):
            raise ValueError(f"port must be within 1..65535, got {self.port}")
        self.data_dir = _normalize_path(self.data_dir)
        if self.stall_sec <= 0:
            raise ValueError(f"stall_sec must be > 0, got {self.stall_sec}")
        if self.watchdog_interval_sec <= 0:
            raise ValueError(
                f"watchdog_interval_sec must be > 0, got {self.watchdog_interval_sec}"
            )
        self.port_range = parse_port_range(self.port_range)
        self.max_snapshot_mb = int(self.max_snapshot_mb)
        if self.max_snapshot_mb < 0:
            raise ValueError(f"max_snapshot_mb must be >= 0, got {self.max_snapshot_mb}")
        self.gc_interval_sec = float(self.gc_interval_sec)
        if not math.isfinite(self.gc_interval_sec) or self.gc_interval_sec <= 0:
            raise ValueError(f"gc_interval_sec must be > 0, got {self.gc_interval_sec}")
        self.result_ttl_sec = int(self.result_ttl_sec)
        if self.result_ttl_sec < 0:
            raise ValueError(f"result_ttl_sec must be >= 0, got {self.result_ttl_sec}")
        self.result_max_files = int(self.result_max_files)
        if self.result_max_files < 0:
            raise ValueError(f"result_max_files must be >= 0, got {self.result_max_files}")
        self.workspace_retention_sec = int(self.workspace_retention_sec)
        if self.workspace_retention_sec < 0:
            raise ValueError(
                f"workspace_retention_sec must be >= 0, got {self.workspace_retention_sec}"
            )

    @property
    def db_path(self) -> Path:
        return self.data_dir / "state.db"

    @property
    def results_dir(self) -> Path:
        return self.data_dir / "results"

    @property
    def lock_path(self) -> Path:
        return self.data_dir / "daemon.lock"

    @property
    def pid_path(self) -> Path:
        return self.data_dir / "daemon.pid.json"

    @property
    def shim_log_path(self) -> Path:
        return self.data_dir / "shim.log"

    @property
    def daemon_log_path(self) -> Path:
        return self.data_dir / "daemon.log"

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    @property
    def port_range_start(self) -> int:
        return self.port_range[0]

    @property
    def port_range_end(self) -> int:
        return self.port_range[1]

    def ensure_data_dir(self) -> Path:
        """Create data_dir on disk if it does not already exist and return it."""
        self.data_dir.mkdir(parents=True, exist_ok=True)
        return self.data_dir

    @classmethod
    def from_env(cls, data_dir: str | Path | None = None) -> Settings:
        """Build Settings from TRI_ORCH_* environment variables with optional data_dir override."""
        host = os.environ.get("TRI_ORCH_HOST", DEFAULT_HOST).strip() or DEFAULT_HOST

        raw_port = os.environ.get("TRI_ORCH_PORT", "").strip()
        port = int(raw_port) if raw_port else DEFAULT_PORT

        if data_dir is not None:
            resolved_data_dir = _normalize_path(data_dir)
        else:
            raw_data_dir = os.environ.get("TRI_ORCH_DATA_DIR", "").strip()
            resolved_data_dir = (
                _normalize_path(raw_data_dir) if raw_data_dir else default_data_dir()
            )

        raw_stall = os.environ.get("TRI_ORCH_STALL_SEC", "").strip()
        stall_sec: int | float = (
            _parse_positive_number(raw_stall, "TRI_ORCH_STALL_SEC")
            if raw_stall
            else DEFAULT_STALL_SEC
        )

        raw_watchdog = os.environ.get("TRI_ORCH_WATCHDOG_INTERVAL_SEC", "").strip()
        watchdog_interval_sec: int | float = (
            _parse_positive_number(raw_watchdog, "TRI_ORCH_WATCHDOG_INTERVAL_SEC")
            if raw_watchdog
            else DEFAULT_WATCHDOG_INTERVAL_SEC
        )

        raw_port_range = os.environ.get("TRI_ORCH_PORT_RANGE", "").strip()
        port_range = (
            parse_port_range(raw_port_range) if raw_port_range else DEFAULT_PORT_RANGE
        )

        raw_max_snapshot = os.environ.get("TRI_ORCH_MAX_SNAPSHOT_MB", "").strip()
        max_snapshot_mb = (
            _parse_non_negative_int(raw_max_snapshot, "TRI_ORCH_MAX_SNAPSHOT_MB")
            if raw_max_snapshot
            else DEFAULT_MAX_SNAPSHOT_MB
        )

        raw_gc_interval = os.environ.get("TRI_ORCH_GC_INTERVAL_SEC", "").strip()
        gc_interval_sec = (
            float(_parse_positive_number(raw_gc_interval, "TRI_ORCH_GC_INTERVAL_SEC"))
            if raw_gc_interval
            else DEFAULT_GC_INTERVAL_SEC
        )

        raw_result_ttl = os.environ.get("TRI_ORCH_RESULT_TTL_SEC", "").strip()
        result_ttl_sec = (
            _parse_non_negative_int(raw_result_ttl, "TRI_ORCH_RESULT_TTL_SEC")
            if raw_result_ttl
            else DEFAULT_RESULT_TTL_SEC
        )

        raw_result_max = os.environ.get("TRI_ORCH_RESULT_MAX_FILES", "").strip()
        result_max_files = (
            _parse_non_negative_int(raw_result_max, "TRI_ORCH_RESULT_MAX_FILES")
            if raw_result_max
            else DEFAULT_RESULT_MAX_FILES
        )

        raw_ws_retention = os.environ.get("TRI_ORCH_WORKSPACE_RETENTION_SEC", "").strip()
        workspace_retention_sec = (
            _parse_non_negative_int(raw_ws_retention, "TRI_ORCH_WORKSPACE_RETENTION_SEC")
            if raw_ws_retention
            else DEFAULT_WORKSPACE_RETENTION_SEC
        )

        return cls(
            host=host,
            port=port,
            data_dir=resolved_data_dir,
            stall_sec=stall_sec,
            watchdog_interval_sec=watchdog_interval_sec,
            port_range=port_range,
            max_snapshot_mb=max_snapshot_mb,
            gc_interval_sec=gc_interval_sec,
            result_ttl_sec=result_ttl_sec,
            result_max_files=result_max_files,
            workspace_retention_sec=workspace_retention_sec,
        )


def get_settings(data_dir: str | Path | None = None) -> Settings:
    """Load Settings from environment variables, optionally overriding data_dir."""
    return Settings.from_env(data_dir=data_dir)
