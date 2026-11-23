"""Reserved host-wake switch (v0.6.11).

Codex ``0.166.0-alpha.5.1`` still ships ``agent_message_board`` and ``wake_on_agent_messages`` as
"under development / false", so a child cannot wake a peer: every ``coder -> verifier -> coder``
hand-off is relayed by the root coordinator.  This module keeps that relay as the only behaviour
today and reserves the switch for when the host opens the features:

* ``TRI_ORCH_HOST_WAKE=off`` (default): never probe; the route is always ``coordinator_relay``.
* ``TRI_ORCH_HOST_WAKE=auto``: probe ``codex features list`` (cached) and report
  ``host_message_board`` only when *every* wake feature is enabled on this host.

The route is advisory metadata on ``board_post`` results and ``/api/health``; the durable ``#seq``
board stays the record either way, and no gate depends on it.  Nothing here invokes host tools.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import threading
import time
from typing import Any

HOST_WAKE_ENV = "TRI_ORCH_HOST_WAKE"
VALID_HOST_WAKE_MODES = ("off", "auto")
WAKE_FEATURES = ("agent_message_board", "wake_on_agent_messages")
ROUTE_RELAY = "coordinator_relay"
ROUTE_HOST = "host_message_board"
_CACHE_TTL_SEC = 300.0

_cache_lock = threading.Lock()
_cache: dict[str, Any] = {"at": 0.0, "features": None, "error": None}


def resolve_host_wake_mode(value: str | None = None) -> str:
    raw = os.environ.get(HOST_WAKE_ENV, "") if value is None else value
    mode = raw.strip().lower() or "off"
    return mode if mode in VALID_HOST_WAKE_MODES else "off"


def parse_feature_list(text: str) -> dict[str, dict[str, Any]]:
    """Parse ``codex features list`` rows: ``<name>  <stage words...>  <true|false>``."""
    features: dict[str, dict[str, Any]] = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 3 or parts[-1].lower() not in {"true", "false"}:
            continue
        features[parts[0]] = {"stage": " ".join(parts[1:-1]), "enabled": parts[-1].lower() == "true"}
    return features


def _codex_bin() -> str | None:
    explicit = os.environ.get("CODEX_BIN", "").strip()
    if explicit:
        return explicit
    return shutil.which("codex.exe") if os.name == "nt" and shutil.which("codex.exe") else shutil.which("codex")


def probe_features(*, timeout_sec: float = 5.0, now: float | None = None, refresh: bool = False) -> dict[str, Any]:
    """Return ``{"features": {...}|None, "error": str|None, "probed_at": float}`` (cached)."""
    ts = time.time() if now is None else now
    with _cache_lock:
        if not refresh and _cache["at"] and ts - _cache["at"] < _CACHE_TTL_SEC:
            return {"features": _cache["features"], "error": _cache["error"], "probed_at": _cache["at"]}
    features: dict[str, dict[str, Any]] | None = None
    error: str | None = None
    binary = _codex_bin()
    if binary is None:
        error = "codex executable not found (set CODEX_BIN)"
    else:
        flags = int(getattr(subprocess, "CREATE_NO_WINDOW", 0)) if os.name == "nt" else 0
        try:
            proc = subprocess.run([binary, "features", "list"], capture_output=True, text=True, encoding="utf-8",
                                  errors="replace", timeout=timeout_sec, stdin=subprocess.DEVNULL, creationflags=flags)
            if proc.returncode == 0:
                features = parse_feature_list(proc.stdout)
            else:
                error = f"codex features list exited {proc.returncode}"
        except (OSError, subprocess.TimeoutExpired) as exc:
            error = f"codex features list failed: {type(exc).__name__}"
    with _cache_lock:
        _cache.update(at=ts, features=features, error=error)
    return {"features": features, "error": error, "probed_at": ts}


def host_wake_status(mode: str | None = None, *, probe: Any = None) -> dict[str, Any]:
    """Report the wake route; ``probe`` may be injected (tests) and defaults to :func:`probe_features`."""
    resolved = resolve_host_wake_mode(mode)
    if resolved == "off":
        return {"mode": "off", "route": ROUTE_RELAY, "features": None,
                "reason": f"{HOST_WAKE_ENV}=off; @mentions are relayed by the coordinator"}
    result = (probe or probe_features)()
    features = result.get("features")
    states = {name: bool(features and features.get(name, {}).get("enabled")) for name in WAKE_FEATURES}
    if features is None:
        return {"mode": "auto", "route": ROUTE_RELAY, "features": states,
                "reason": f"host features unknown ({result.get('error')}); relaying"}
    if all(states.values()):
        return {"mode": "auto", "route": ROUTE_HOST, "features": states,
                "reason": "host wake features enabled; deliver @mentions with the native message board"}
    missing = sorted(name for name, on in states.items() if not on)
    return {"mode": "auto", "route": ROUTE_RELAY, "features": states,
            "reason": f"host features not enabled: {', '.join(missing)}; relaying"}


def cached_probe() -> dict[str, Any]:
    """Return the last probe result without running a subprocess (for latency-sensitive callers)."""
    with _cache_lock:
        if _cache["at"]:
            return {"features": _cache["features"], "error": _cache["error"], "probed_at": _cache["at"]}
    return {"features": None, "error": "not probed yet", "probed_at": None}


def reset_cache() -> None:
    with _cache_lock:
        _cache.update(at=0.0, features=None, error=None)
