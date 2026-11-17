"""Mode2 role/skill instructions agree with executable backend interfaces."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "plugin"


def test_all_roles_and_modes_load_all_supported_strict_contracts():
    for path in list((ROOT / "agents").glob("*.toml")) + list((ROOT / "skills").glob("*/SKILL.md")):
        text = path.read_text(encoding="utf-8")
        assert "workflow_version=1/2/3/4" in text, path
    shared = (ROOT / "skills/route-a-workflow.md").read_text(encoding="utf-8")
    assert 'artifact_id=candidate["id"]' in shared
    assert 'recipient_agent_id=next_coordinator_agent_id' in shared
    assert "before native implementation dispatch" in shared
    assert "workflow1" in shared and "workflow2" in shared


def test_mode_specific_stage_and_bounded_native_wakeup_contracts():
    boost = (ROOT / "skills/boost/SKILL.md").read_text(encoding="utf-8")
    teamwork = (ROOT / "skills/teamwork/SKILL.md").read_text(encoding="utf-8")
    swarm = (ROOT / "skills/swarm/SKILL.md").read_text(encoding="utf-8")
    assert "Execute → Independent challenge → Original-author repair → Final re-verification" in boost
    assert "milestone's existing attempt counter" in teamwork
    assert "intended recipient" in teamwork
    assert 'step_id=step["id"]' in swarm
    assert "one inbox_wait per worker turn" in swarm
    assert "heartbeat timeout is not evidence of death" in swarm
    recovery = swarm.split("**Stall & Silent-Exit Recovery**", 1)[1].split("### 5.", 1)[0]
    assert "actual native observation" in recovery
    assert "spawn a replacement worker" not in recovery


def test_implementation_sequences_prebind_verifier_and_admit_before_work():
    for mode, start, end in (("boost", "### 2.", "### 3."),
                             ("teamwork", "### Step 3:", "### Step 4:"),
                             ("swarm", "### 2.", "### 3.")):
        text = (ROOT / "skills" / mode / "SKILL.md").read_text(encoding="utf-8")
        implementation = text.split(start, 1)[1].split(end, 1)[0]
        assert "bootstrap-only" in implementation, mode
        assert "workflow_step_advance" in implementation, mode
        assert "followup_task" in implementation, mode
        assert "prebound verifier" in implementation, mode


def test_teamwork_integrates_and_confirms_each_milestone_before_next():
    text = (ROOT / "skills/teamwork/SKILL.md").read_text(encoding="utf-8")
    merge = text.split("### Step 6:", 1)[1].split("### Large MCP", 1)[0]
    assert "For each admitted milestone" in merge
    assert 'scope="integrated"' in merge
    assert 'action="ack_handoff"' in merge
    assert "Only then admit the next dependent milestone" in merge
    assert merge.index("After all milestones") < merge.index("workspace_cleanup")


def test_boost_retains_original_workspaces_until_all_integrated_gates():
    text = (ROOT / "skills/boost/SKILL.md").read_text(encoding="utf-8")
    merge = text.split("### 5. Merge, Cleanup & Close", 1)[1].split("### Large MCP", 1)[0]
    assert "After all" in merge
    assert merge.index("After all") < merge.index("workspace_cleanup")
    assert 'scope="integrated"' in merge
    assert 'action="reverify"' in merge
    assert "original author workspaces" in merge
