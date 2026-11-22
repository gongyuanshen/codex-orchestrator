"""Workflow5 role/skill instructions agree with executable backend interfaces."""
from pathlib import Path
import re
import tomllib

ROOT = Path(__file__).resolve().parents[1] / "plugin"


def _parse_yaml_frontmatter_strict(markdown_text: str) -> dict[str, object]:
    """Parse SKILL.md YAML frontmatter enforcing YAML's unquoted ` #` comment truncation rule."""
    assert markdown_text.startswith("---\n"), "SKILL.md must begin with YAML frontmatter"
    _, rest = markdown_text.split("---\n", 1)
    fm_raw, _ = rest.split("\n---\n", 1)

    def _parse_scalar(raw_val: str) -> str:
        val = raw_val.strip()
        if not val:
            return ""
        if val[0] == '"':
            assert val.endswith('"') and len(val) >= 2, f"Unterminated double-quoted YAML value: {raw_val!r}"
            return val[1:-1].replace('\\"', '"')
        if val[0] == "'":
            assert val.endswith("'") and len(val) >= 2, f"Unterminated single-quoted YAML value: {raw_val!r}"
            return val[1:-1].replace("''", "'")
        # In standard YAML, `#` preceded by whitespace (or at start of scalar) starts a comment!
        val = re.sub(r"(?:^|\s)#.*$", "", val).rstrip()
        return val

    result: dict[str, object] = {}
    current_map: dict[str, str] | None = None
    for line in fm_raw.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line.startswith("  ") and current_map is not None:
            k, v = line.strip().split(":", 1)
            current_map[k.strip()] = _parse_scalar(v)
        else:
            k, v = line.split(":", 1)
            key = k.strip()
            if not v.strip():
                nested: dict[str, str] = {}
                result[key] = nested
                current_map = nested
            else:
                result[key] = _parse_scalar(v)
                current_map = None
    return result


def test_all_skills_yaml_frontmatter_never_truncates_on_hash_comments():
    for path in sorted((ROOT / "skills").glob("*/SKILL.md")):
        fm = _parse_yaml_frontmatter_strict(path.read_text(encoding="utf-8"))
        assert fm["name"] == path.parent.name, path
        desc = str(fm["description"])
        assert len(desc) >= 80, (path, desc)
        metadata = fm["metadata"]
        assert isinstance(metadata, dict)
        short_desc = str(metadata.get("short-description", ""))
        assert len(short_desc) >= 15, (path, short_desc)

    swarm_fm = _parse_yaml_frontmatter_strict((ROOT / "skills/swarm/SKILL.md").read_text(encoding="utf-8"))
    swarm_desc = str(swarm_fm["description"])
    for preserved in ("#seq message board", "#backend/#ui/#verify", "board_read(since_seq)", "live dashboard observability"):
        assert preserved in swarm_desc, f"swarm/SKILL.md YAML description truncated {preserved!r}: {swarm_desc!r}"
    swarm_meta = swarm_fm["metadata"]
    assert isinstance(swarm_meta, dict)
    assert "#seq" in swarm_meta["short-description"] and "stall recovery" in swarm_meta["short-description"]


def test_all_roles_and_modes_load_all_supported_strict_contracts():
    skill_paths = sorted((ROOT / "skills").glob("*/SKILL.md"))
    assert {path.parent.name for path in skill_paths} == {"boost", "teamwork", "swarm"}
    for path in list((ROOT / "agents").glob("*.toml")) + skill_paths:
        text = path.read_text(encoding="utf-8")
        assert "workflow5" in text and "0.6.10/schema10" in text, path
        assert "workflow0..4" in text, path
        assert "board_status.workflow.workflow_version" in text, path
    for path in skill_paths:
        text = path.read_text(encoding="utf-8")
        assert text.startswith("---\n"), path
        assert f"name: {path.parent.name}" in text, path
        assert "../route-a-workflow.md" in text, path
    shared = (ROOT / "skills/route-a-workflow.md").read_text(encoding="utf-8")
    assert "workflow5" in shared and "0.6.10/schema10" in shared
    assert 'artifact_id=candidate["id"]' in shared
    assert 'recipient_agent_id=next_coordinator_agent_id' in shared
    assert "admission precedes native implementation dispatch" in shared
    assert "workflow1..4" in shared and "workflow0..4" in shared


def test_interactive_modes_and_agents_enforce_native_and_local_execution_kinds():
    for native_mode in ("boost", "teamwork", "swarm"):
        native_text = (ROOT / "skills" / native_mode / "SKILL.md").read_text(encoding="utf-8")
        assert 'execution_kind="native"' in native_text
        assert "app_server_pipeline" not in native_text
        assert "$pipeline" not in native_text
        assert "send_input" not in native_text

    for toml_path in sorted((ROOT / "agents").glob("*.toml")):
        instructions = tomllib.loads(toml_path.read_text(encoding="utf-8"))["developer_instructions"]
        assert "app_server_pipeline" not in instructions, toml_path
        assert "$pipeline" not in instructions, toml_path
        assert "send_input" not in instructions, toml_path


def test_mode_specific_stage_and_bounded_native_wakeup_contracts():
    boost = (ROOT / "skills/boost/SKILL.md").read_text(encoding="utf-8")
    teamwork = (ROOT / "skills/teamwork/SKILL.md").read_text(encoding="utf-8")
    swarm = (ROOT / "skills/swarm/SKILL.md").read_text(encoding="utf-8")
    assert "Execute → Independent challenge → Original-author repair → Final re-verification" in boost
    assert "milestone's existing attempt counter" in teamwork
    assert "intended recipient" in teamwork
    assert 'step_id=step["id"]' in swarm
    assert "one `inbox_wait` per worker turn" in swarm
    assert "heartbeat timeout is not evidence of death" in swarm
    recovery = swarm.split("**Stall & Silent-Exit Recovery**", 1)[1].split("### 5.", 1)[0]
    assert "actual native observation" in recovery
    assert "spawn a replacement worker" not in recovery


def test_implementation_sequences_support_workflow5_direct_and_legacy_fallback():
    for mode, start, end in (("boost", "### 2.", "### 3."),
                             ("teamwork", "### Step 3:", "### Step 4:"),
                             ("swarm", "### 2.", "### 3.")):
        text = (ROOT / "skills" / mode / "SKILL.md").read_text(encoding="utf-8")
        implementation = text.split(start, 1)[1].split(end, 1)[0]
        # Primary workflow5 path: 1-turn direct dispatch + lazy verifier frozen & reused via followup_task
        assert "workflow_prepare_dispatch" in implementation, mode
        assert "native_action_record_call" in implementation, mode
        assert "followup_task" in implementation, mode
        # Stored workflow1..4 fallback preserved via board_status.workflow.workflow_version
        assert "`workflow1..4` fallback" in implementation, mode
        assert "workflow_step_advance" in implementation, mode
        assert "prebound verifier" in implementation, mode


def test_teamwork_integrates_and_confirms_each_milestone_before_next():
    text = (ROOT / "skills/teamwork/SKILL.md").read_text(encoding="utf-8")
    merge = text.split("### Step 6:", 1)[1].split("### Large MCP", 1)[0]
    assert "For each admitted milestone" in merge
    assert 'scope="integrated"' in merge
    assert 'action="ack_handoff"' in merge
    assert "Only then admit the next dependent milestone" in merge
    assert "workflow_closeout_run" in merge
    assert merge.index("After all milestones") < merge.index("workspace_cleanup")


def test_boost_retains_original_workspaces_until_all_integrated_gates():
    text = (ROOT / "skills/boost/SKILL.md").read_text(encoding="utf-8")
    merge = text.split("### 5. Merge, Cleanup & Close", 1)[1].split("### Large MCP", 1)[0]
    assert "After all" in merge
    assert merge.index("After all") < merge.index("workspace_cleanup")
    assert "workflow_closeout_run" in merge
    assert 'scope="integrated"' in merge
    assert 'action="reverify"' in merge
    assert "original author workspaces" in merge
