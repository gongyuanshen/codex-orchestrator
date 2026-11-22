"""Local verification collector (`local_collector_v1`) instruction contracts."""
from pathlib import Path
import re
import tomllib
import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_verification_collector_requires_frozen_commands_then_real_receipts():
    text = (ROOT / "plugin/skills/route-a-workflow.md").read_text(encoding="utf-8")
    assert "workflow0..4" in text and "workflow3" in text
    section = text.split("## 6. Routing, verification collector, challenge & review gates", 1)[1].split("\n## ", 1)[0]
    ordered = (
        "artifact_capture",
        "verification_commands",
        "tri-orchestrator-verify --manifest",
        "verification_record",
        "receipt_files",
    )
    positions = [section.index(word) for word in ordered]
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
        assert term in section, term


@pytest.mark.parametrize("mode", ("boost", "teamwork", "swarm"))
def test_modes_and_inline_roles_inherit_verification_collector(mode):
    text = (ROOT / f"plugin/skills/{mode}/SKILL.md").read_text(encoding="utf-8")
    assert "New runs use workflow4" not in text
    assert "software 0.5.0/schema7" not in text
    for term in (
        "workflow5",
        "0.6.9/schema10",
        "../route-a-workflow.md",
        "workflow_verifier_turn",
        "tri-orchestrator-verify",
        "tri-orchestrator-challenge",
        "env_profile",
    ):
        assert term in text, (mode, term)
    roles = [line for line in text.splitlines() if re.match(r"- \*\*`\w+` \(", line)]
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
    shared = (ROOT / "plugin/skills/route-a-workflow.md").read_text(encoding="utf-8")
    verifier = (ROOT / "plugin/agents/verifier.toml").read_text(encoding="utf-8")
    for path, text in ((ROOT / "plugin/skills/route-a-workflow.md", shared), (ROOT / "plugin/agents/verifier.toml", verifier)):
        for term in (
            "exact child environment allowlist",
            "verifier terminal",
            "daemon validates only key syntax/uniqueness",
            "SystemRoot/PATH/TEMP",
        ):
            assert term in text, (path, term)

    for mode in ("boost", "teamwork", "swarm"):
        text = (ROOT / f"plugin/skills/{mode}/SKILL.md").read_text(encoding="utf-8")
        inline_section = text.split("## Inline Fallback Role Contracts", 1)[1].split("\n## ", 1)[0]
        assert "env_keys" in inline_section and "env_profile" in inline_section, mode
        assert "local_collector_v1" in inline_section, mode
