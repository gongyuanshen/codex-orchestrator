"""Packaged instructions match strict backend semantics, not native execution proof."""
from pathlib import Path

ROOT = Path(__file__).parents[1]


def test_all_modes_load_shared_strict_workflow_contract():
    for mode in ('boost', 'teamwork', 'swarm'):
        text = (ROOT / 'plugin/skills' / mode / 'SKILL.md').read_text(encoding='utf-8')
        assert '../route-a-workflow.md' in text
        assert 'at most 3 automatic rework rounds' in text
    shared = (ROOT / 'plugin/skills/route-a-workflow.md').read_text(encoding='utf-8')
    for name in ('workflow_step_save', 'workflow_step_advance', 'artifact_capture',
                 'verification_record', 'inbox_send', 'inbox_ack', 'inbox_wait'):
        assert name in shared
    assert 'expected_revision' in shared
    assert 'before running' in shared
    assert 'different native agent' in shared
    assert 'not independent acceptance' in shared
    assert 'needs_decision' in shared
    assert 'reconciliation_required' in shared


def test_roles_preserve_receipt_and_acceptance_separation():
    for role in ('coder', 'swarm_worker', 'verifier', 'planner', 'interviewer', 'investigator'):
        text = (ROOT / 'plugin/agents' / f'{role}.toml').read_text(encoding='utf-8')
        assert 'Route A structured workflow' in text
        assert 'not independent acceptance' in text
