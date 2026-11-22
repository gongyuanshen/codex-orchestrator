"""The installed package and MCP handshake must identify the same release."""
import json
from pathlib import Path
import tomllib

from orchestrator import __version__
from orchestrator.config import Settings
from orchestrator.mcp_shim import create_mcp_server


def test_mode_enhancement_release_identity():
    assert __version__ == '0.6.9'


def test_package_and_mcp_release_versions_match(tmp_path):
    root = Path(__file__).parents[1]
    plugin = root / 'plugin'
    assert create_mcp_server(Settings(data_dir=tmp_path)).create_initialization_options().server_version == __version__
    assert not (plugin / 'plugin.json').exists(), 'Legacy root plugin/plugin.json must be removed for single-manifest convergence'
    manifest_path = plugin / '.codex-plugin' / 'plugin.json'
    assert manifest_path.is_file()
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    assert manifest['name'] == 'tri-orchestrator'
    assert manifest['version'] == __version__
    assert manifest['hooks'] == './hooks/hooks.json'
    assert manifest['skills'] == './skills/'
    assert manifest['mcpServers'] == './.mcp.json'
    assert isinstance(manifest.get('interface'), dict) and manifest['interface'].get('displayName')
    hooks_path = plugin / 'hooks' / 'hooks.json'
    assert hooks_path.is_file()
    hooks_cfg = json.loads(hooks_path.read_text(encoding='utf-8'))
    assert set(hooks_cfg.get('hooks', {}).keys()) == {'SessionStart', 'PostCompact', 'UserPromptSubmit', 'Stop'}
    assert tomllib.loads((plugin / 'pyproject.toml').read_text(encoding='utf-8'))['project']['version'] == __version__
    for lock in (plugin / 'uv.lock', root / 'uv.lock'):
        packages = tomllib.loads(lock.read_text(encoding='utf-8'))['package']
        assert next(p for p in packages if p['name'] == 'tri-orchestrator')['version'] == __version__


def test_local_verification_collector_console_entry():
    plugin = Path(__file__).parents[1] / 'plugin'
    scripts = tomllib.loads((plugin / 'pyproject.toml').read_text(encoding='utf-8'))['project']['scripts']
    assert scripts == {
        'tri-orchestrator-mcp': 'orchestrator.mcp_shim:main',
        'tri-orchestrator-daemon': 'orchestrator.daemon:main',
        'tri-orchestrator-verify': 'orchestrator.verify_runner:main',
        'tri-orchestrator-hook': 'orchestrator.hook_runner:main',
        'tri-orchestrator-challenge': 'orchestrator.challenge_runner:main',
    }
