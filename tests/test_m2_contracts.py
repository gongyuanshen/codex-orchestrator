"""Local verification collector (`local_collector_v1`) instruction contracts."""
from pathlib import Path
import re
import tomllib
import pytest

from skill_docs import mode_corpus, reference_text, section, shared_corpus, skill_text

ROOT = Path(__file__).resolve().parents[1]


def test_verification_collector_requires_frozen_commands_then_real_receipts():
    assert "workflow0..4" in shared_corpus() and "workflow3" in shared_corpus()
    section_text = section(reference_text("verification"), "## Local verification collector")
    ordered = (
        "artifact_capture",
        "verification_commands",
        "tri-orchestrator-verify --manifest",
        "verification_record",
        "receipt_files",
    )
    positions = [section_text.index(word) for word in ordered]
    assert positions == sorted(positions)
    for term in (
        "argv",
        "cwd",
        "timeout_sec",
        "env_keys",
        "criteria",
        "manifest_path",
        "manifest_digest",
        "local_collector_v1",
        "not host authentication",
        "raw stdout/stderr",
        "environment values",
        "temporary modification",
        "candidate",
        "integrated",
        "original verifier",
        "each criterion",
        "RPC",
    ):
        assert term in section_text, term


@pytest.mark.parametrize("mode", ("boost", "teamwork", "swarm"))
def test_modes_and_inline_roles_inherit_verification_collector(mode):
    skill = skill_text(mode)
    text = mode_corpus(mode)
    assert "New runs use workflow4" not in text
    assert "software 0.5.0/schema7" not in text
    assert "../route-a-workflow.md" in skill
    assert "workflow5" in skill and "workflow_verifier_turn" in skill
    for term in (
        "workflow5",
        "0.6.12/schema10",
        "workflow_verifier_turn",
        "tri-orchestrator-verify",
        "tri-orchestrator-challenge",
        "env_profile",
    ):
        assert term in text, (mode, term)
    roles = [line for line in reference_text("inline-roles").splitlines() if re.match(r"- \*\*`\w+` \(", line)]
    assert roles
    verifier_line = next(line for line in roles if line.startswith("- **`verifier`"))
    for term in ("workflow_verifier_turn", "tri-orchestrator-verify", "receipt_files"):
        assert term in verifier_line, (mode, term)


def test_verifier_role_loads_collector_contract_and_all_roles_omit_models():
    for path in sorted((ROOT / "plugin/agents").glob("*.toml")):
        role = tomllib.loads(path.read_text(encoding="utf-8"))
        assert "model" not in role, path
        assert "model_reasoning_effort" not in role, path

    verifier = tomllib.loads((ROOT / "plugin/agents/verifier.toml").read_text(encoding="utf-8"))
    v_text = verifier["developer_instructions"]
    for term in (
        "workflow5",
        "verification_commands",
        "receipt_files",
        "local_collector_v1",
        "original verifier",
        "tri-orchestrator-verify --manifest",
    ):
        assert term in v_text, term


def test_env_keys_execution_contract_is_explicit_in_shared_modes_inline_and_verifier():
    shared = reference_text("verification")
    verifier = (ROOT / "plugin/agents/verifier.toml").read_text(encoding="utf-8")
    for path, text in ((ROOT / "plugin/skills/reference/verification.md", shared), (ROOT / "plugin/agents/verifier.toml", verifier)):
        for term in (
            "exact child environment allowlist",
            "verifier terminal",
            "daemon validates only key syntax/uniqueness",
            "SystemRoot/PATH/TEMP",
        ):
            assert term in text, (path, term)

    inline_section = reference_text("inline-roles")
    assert "env_keys" in inline_section and "env_profile" in inline_section
    assert "local_collector_v1" in inline_section
