"""Exclusive health-port probes must share the daemon startup mutex."""
from contextlib import contextmanager

from orchestrator.config import Settings
import orchestrator.mcp_shim as shim

from test_shim_concurrency import _terminate_pid


def test_ensure_health_port_probe_is_inside_singleton_lock(tmp_path, free_tcp_port, monkeypatch):
    settings = Settings(data_dir=tmp_path, port=free_tcp_port)
    original_lock = shim._acquire_daemon_lock
    original_health = shim.fetch_health
    held = False

    @contextmanager
    def tracked_lock(*args, **kwargs):
        nonlocal held
        with original_lock(*args, **kwargs):
            held = True
            try:
                yield
            finally:
                held = False

    def checked_health(*args, **kwargs):
        assert held, 'Exclusive port probe ran outside singleton lock'
        return original_health(*args, **kwargs)

    monkeypatch.setattr(shim, '_acquire_daemon_lock', tracked_lock)
    monkeypatch.setattr(shim, 'fetch_health', checked_health)
    pid = None
    try:
        first = shim.ensure_daemon_running(settings)
        pid = first['pid']
        assert first['spawned'] is True
        second = shim.ensure_daemon_running(settings)
        assert second == {'spawned': False, 'pid': pid, 'url': settings.base_url}
    finally:
        if pid is not None:
            _terminate_pid(pid)
