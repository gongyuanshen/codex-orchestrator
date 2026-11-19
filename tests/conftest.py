import socket

import pytest


@pytest.fixture(autouse=True)
def isolated_runtime_environment(tmp_path, monkeypatch):
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    assert port != 18790
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "isolated-codex"))
    monkeypatch.setenv("TRI_ORCH_HOST", "127.0.0.1")
    monkeypatch.setenv("TRI_ORCH_DATA_DIR", str(tmp_path / "isolated-runtime"))
    monkeypatch.setenv("TRI_ORCH_PORT", str(port))
