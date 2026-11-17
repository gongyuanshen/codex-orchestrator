"""The installed package and MCP handshake must identify the same release."""
import json
from pathlib import Path
import tomllib

from orchestrator import __version__
from orchestrator.config import Settings
from orchestrator.mcp_shim import create_mcp_server


def test_mode_enhancement_release_identity():
    assert __version__ == '0.5.0'


def test_package_and_mcp_release_versions_match(tmp_path):
    root = Path(__file__).parents[1]
    plugin = root / 'plugin'
    assert create_mcp_server(Settings(data_dir=tmp_path)).create_initialization_options().server_version == __version__
    for name in ('.codex-plugin/plugin.json', 'plugin.json'):
        assert json.loads((plugin / name).read_text(encoding='utf-8'))['version'] == __version__
    assert tomllib.loads((plugin / 'pyproject.toml').read_text(encoding='utf-8'))['project']['version'] == __version__
    for lock in (plugin / 'uv.lock', root / 'uv.lock'):
        packages = tomllib.loads(lock.read_text(encoding='utf-8'))['package']
        assert next(p for p in packages if p['name'] == 'tri-orchestrator')['version'] == __version__


def test_local_verification_collector_console_entry():
    plugin = Path(__file__).parents[1] / 'plugin'
    scripts = tomllib.loads((plugin / 'pyproject.toml').read_text(encoding='utf-8'))['project']['scripts']
    assert scripts['tri-orchestrator-verify'] == 'orchestrator.verify_runner:main'
