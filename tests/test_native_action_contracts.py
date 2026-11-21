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
    assert "workflow0..4" in text and "waiting-only" in text
    dispatch_section = text.split("## 4. Dispatch, native transfer ledger & reconciliation", 1)[1].split("\n## ", 1)[0]
    manual_section = dispatch_section.split("### Manual primitive transfer ledger & grouped transfers", 1)[1]
    steps = re.findall(r"^\d+\. (.+)", manual_section, re.MULTILINE)
    required = ("inbox_send", "board_status", 'action="claim"', 'action="begin"', "actual native", 'action="record"')
    positions = [next(i for i, step in enumerate(steps) if term in step) for term in required]
    assert positions == sorted(set(positions)), "persist, project, claim, begin, invoke, record must remain ordered"
    for term in (
        "source_digest",
        "claim_token",
        "expected_revision",
        "evidence_ref",
        "evidence_digest",
        "source_id",
        "source_type",
        "recipient",
        "action_type",
    ):
        assert term in manual_section
    assert "needs_reconciliation" in dispatch_section and "not exactly-once" in dispatch_section
    assert "Never re-claim or automatically replay" in dispatch_section
    assert "inbox ACK, business PASS, and control ACK are separate" in manual_section
    assert "ambiguous recipients" in manual_section and "original seq" in manual_section


@pytest.mark.parametrize("mode", MODES)
def test_modes_reference_shared_transfer_and_reconciliation_protocol(mode):
    text = (ROOT / f"plugin/skills/{mode}/SKILL.md").read_text(encoding="utf-8")
    assert "../route-a-workflow.md" in text
    for term in (
        "workflow_prepare_dispatch",
        "native_action_record_call",
        "needs_reconciliation",
        "worker_control_ack",
        "original author",
        "original verifier",
        "3 automatic rework",
        "waiting-only",
        "workflow1..4",
    ):
        assert term in text, (mode, term)
    assert "then use the actual native followup_task/send_message" not in text
    assert "send_input" not in text


@pytest.mark.parametrize("path", sorted((ROOT / "plugin/agents").glob("*.toml")), ids=lambda path: path.stem)
def test_packaged_roles_preserve_native_boundaries(path):
    role = tomllib.loads(path.read_text(encoding="utf-8"))
    contract = role["developer_instructions"]
    assert "not independent acceptance" in contract
    assert "worker_control_ack" in contract
    assert "original author" in contract and "original verifier" in contract
    assert "model" not in role, "Must not introduce a model override"
    if path.stem in {"coder", "swarm_worker"}:
        assert "workflow_author_turn" in contract and "business PASS" in contract
    elif path.stem == "verifier":
        assert "workflow_verifier_turn" in contract and "business PASS" in contract
