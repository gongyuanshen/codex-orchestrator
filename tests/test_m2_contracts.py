"""Workflow3 instruction contracts, not proof of actual collector execution."""
from pathlib import Path
import re
import tomllib
import pytest
ROOT = Path(__file__).resolve().parents[1]


def test_workflow3_collector_requires_frozen_commands_then_real_receipts():
    text = (ROOT/'plugin/skills/route-a-workflow.md').read_text(encoding='utf-8')
    section = text.split('## M2 workflow3 verification collector', 1)[1].split('\n## ', 1)[0]
    ordered = ('artifact_capture', 'verification_commands', 'tri-orchestrator-verify --manifest', 'verification_record', 'receipt_files')
    positions = [section.index(word) for word in ordered]
    assert positions == sorted(positions)
    for term in ('argv', 'cwd', 'timeout_sec', 'env_keys', 'criteria', 'manifest_path', 'manifest_digest', 'local_collector_v1', 'not host authentication', 'raw stdout/stderr', 'environment values', 'temporary modification', 'candidate', 'integrated', 'original verifier', 'each criterion', 'RPC', 'workflow0/1/2'):
        assert term in section


@pytest.mark.parametrize('mode', ('boost','teamwork','swarm'))
def test_modes_and_inline_roles_inherit_workflow3_collector(mode):
    text = (ROOT/f'plugin/skills/{mode}/SKILL.md').read_text(encoding='utf-8')
    for term in ('New runs use workflow4', 'verification_commands', 'tri-orchestrator-verify --manifest', 'receipt_files', 'local_collector_v1', 'M1 transfer protocol'):
        assert term in text
    roles = [line for line in text.splitlines() if re.match(r'- \*\*`\w+` \(',line)]
    assert roles and all('M2 workflow3 collector' in line for line in roles)


@pytest.mark.parametrize('path', sorted((ROOT/'plugin/agents').glob('*.toml')), ids=lambda path:path.stem)
def test_roles_load_m2_contract_without_changing_models(path):
    role = tomllib.loads(path.read_text(encoding='utf-8'))
    text = role['developer_instructions']
    for term in ('workflow3', 'verification_commands', 'receipt_files', 'local_collector_v1', 'original verifier', 'M1'):
        assert term in text
    assert 'model' not in role


def test_env_keys_execution_contract_is_explicit_in_shared_modes_inline_and_roles():
    paths = [ROOT/'plugin/skills/route-a-workflow.md']
    paths += [ROOT/f'plugin/skills/{mode}/SKILL.md' for mode in ('boost','teamwork','swarm')]
    paths += sorted((ROOT/'plugin/agents').glob('*.toml'))
    for path in paths:
        text = path.read_text(encoding='utf-8')
        for term in ('exact child environment allowlist', 'verifier terminal', 'daemon validates only key syntax/uniqueness', 'SystemRoot/PATH/TEMP'):
            assert term in text, (path, term)
        if path.name == 'SKILL.md':
            roles = [line for line in text.splitlines() if re.match(r'- \*\*`\w+` \(', line)]
            assert all('env_keys allowlist' in line for line in roles)
