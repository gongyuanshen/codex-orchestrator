"""Contract regressions only; these assertions do not prove host execution."""
from pathlib import Path
import re
import tomllib

import pytest

ROOT = Path(__file__).resolve().parents[1]
SHARED = ROOT / "plugin/skills/route-a-workflow.md"
MODES = ("boost", "teamwork", "swarm")


def test_shared_transfer_sequence_persists_before_native_execution():
    text = SHARED.read_text(encoding="utf-8")
    section = text.split("## M1 native transfer ledger", 1)[1].split("\n## ", 1)[0]
    steps = re.findall(r"^\d+\. (.+)", section, re.MULTILINE)
    required = ("inbox_send", "board_status", 'action="claim"', 'action="begin"', "actual native", 'action="record"')
    positions = [next(i for i, step in enumerate(steps) if term in step) for term in required]
    assert positions == sorted(set(positions)), "persist, project, claim, begin, invoke, record must remain ordered"
    for term in ("source_digest", "claim_token", "expected_revision", "evidence_ref", "evidence_digest", "source_id", "source_type", "recipient", "action_type"):
        assert term in section
    assert "needs_reconciliation" in section and "never automatically replay" in section
    assert "not exactly-once" in section
    assert "workflow0/1/2 semantics remain unchanged" in section
    assert "workflow3" in section and "M2" in section
    assert "inbox ACK, business PASS and control ACK are separate" in section
    assert "ambiguous recipients" in section and "original seq" in section


@pytest.mark.parametrize("mode", MODES)
def test_modes_and_inline_roles_require_same_transfer_protocol(mode):
    text = (ROOT / f"plugin/skills/{mode}/SKILL.md").read_text(encoding="utf-8")
    assert "## M1 transfer protocol" in text
    section = text.split("## M1 transfer protocol", 1)[1].split("\n## ", 1)[0]
    for term in ("inbox_send", "board_status", "native_action_update", "claim", "begin", "record", "reconcile", "original author", "original verifier", "3 automatic rework", "bootstrap", "waiting-only", "control ACK", "business PASS"):
        assert term in section
    roles = [line for line in text.splitlines() if re.match(r"- \*\*`\w+` \(", line)]
    assert roles
    assert all("M1 transfer protocol" in line for line in roles)
    assert 'then use the actual native followup_task/send_message' not in text


@pytest.mark.parametrize("path", sorted((ROOT / "plugin/agents").glob("*.toml")), ids=lambda path: path.stem)
def test_packaged_roles_preserve_native_boundaries(path):
    role = tomllib.loads(path.read_text(encoding="utf-8"))
    contract = role["developer_instructions"]
    for term in ("inbox_send", "native_action_update", "claim", "begin", "record", "reconcile", "waiting-only", "business PASS", "control ACK", "original author", "original verifier"):
        assert term in contract
    assert "model" not in role, "M1 must not introduce a model override"
