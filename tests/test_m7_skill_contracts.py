"""M7 & M12 static contract tests: Codex V2 native capability unlock, macro turns, role TOML & SKILL streamlining."""
from __future__ import annotations

from pathlib import Path
import re
import tomllib

import pytest

from skill_docs import mode_corpus, reference_paths, reference_text, section, shared_corpus, shared_text, skill_text

ROOT = Path(__file__).resolve().parents[1] / "plugin"
MODES = ("boost", "teamwork", "swarm")
ALL_SKILLS = ("boost", "teamwork", "swarm")
STRICT_NO_SPAWN_ROLES = ("interviewer", "investigator")
DEPTH1_HELPER_ROLES = ("planner", "coder", "swarm_worker", "verifier")
EXPECTED_AGENT_ROLES = frozenset((*STRICT_NO_SPAWN_ROLES, *DEPTH1_HELPER_ROLES))


def _skill_text(mode: str) -> str:
    return (ROOT / "skills" / mode / "SKILL.md").read_text(encoding="utf-8")


def _inline_role_map(text: str) -> dict[str, str]:
    roles: dict[str, str] = {}
    for line in text.splitlines():
        match = re.match(r"^- \*\*`(\w+)` \(", line)
        if match:
            roles[match.group(1)] = line
    return roles


@pytest.mark.parametrize("mode", MODES)
def test_zero_script_agent_type_omission_and_host_qualified_model_selection(mode: str) -> None:
    text = mode_corpus(mode)
    assert 'agent_type: "default"' not in text
    assert 'agent_type="default"' not in text
    assert "xhigh" not in text
    rules = section(shared_text(), "## Subagent rules")
    assert "omit `agent_type`" in rules
    assert "`CODEX_HOME`" in rules
    assert "live `spawn_agent` schema" in rules
    _assert_native_model_selection(rules)
    assert "100%" not in text
    for line in text.splitlines():
        if 'fork_turns="all"' in line:
            assert "choose `reasoning_effort`" not in line


def _assert_native_model_selection(text: str) -> None:
    assert "omit `model`" in text
    assert "Codex role and session settings" in text
    assert 'omit both `model` and `reasoning_effort` with `fork_turns="all"`' in text
    assert 'Pass `reasoning_effort` only with `fork_turns="none"`' in text
    assert "host schema and model support the requested tier" in text


def test_zero_legacy_v1_or_stale_version_artifacts_across_all_skills_and_roles() -> None:
    all_docs = [ROOT / "skills/route-a-workflow.md"]
    all_docs += [ROOT / "skills" / name / "SKILL.md" for name in ALL_SKILLS]
    all_docs += sorted((ROOT / "agents").glob("*.toml"))
    all_docs += reference_paths()
    for path in all_docs:
        text = path.read_text(encoding="utf-8")
        assert "functions.exec" not in text, f"{path} still references removed functions.exec"
        assert 'agent_type="default"' not in text, f"{path} still instructs agent_type=\"default\""
        assert 'agent_type: "default"' not in text, f"{path} still instructs agent_type: \"default\""
        assert "xhigh" not in text, f"{path} still hardcodes xhigh reasoning effort"
        assert "send_input" not in text, f"{path} still references obsolete Codex V1 send_input"
        assert "Codex V1" not in text, f"{path} still references obsolete Codex V1"
        assert "software 0.5.0/schema7" not in text, f"{path} still carries stale 0.5.0/schema7 claim"
        assert "Software 0.4.0/schema6" not in text, f"{path} still carries stale 0.4.0/schema6 claim"
        assert "New runs use workflow4" not in text, f"{path} still carries stale workflow4 default claim"


@pytest.mark.parametrize("path", sorted((ROOT / "agents").glob("*.toml")), ids=lambda p: p.stem)
def test_agent_tomls_omit_model_and_reasoning_effort_overrides(path: Path) -> None:
    assert path.stem in EXPECTED_AGENT_ROLES
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    assert "model" not in data
    assert "model_reasoning_effort" not in data
    if path.stem in STRICT_NO_SPAWN_ROLES:
        assert data["sandbox_mode"] == "read-only"
    else:
        assert "sandbox_mode" not in data


@pytest.mark.parametrize("mode", MODES)
def test_nuanced_fork_turns_replaces_blanket_none(mode: str) -> None:
    text = mode_corpus(mode)
    assert 'Always pass `fork_turns: "none"`' not in text
    assert 'forces parent model and `reasoning_effort` when `fork_turns="all"`' not in text
    rules = section(shared_text(), "## Subagent rules")
    assert "custom `agent_type` applies regardless of `fork_turns`" in rules
    assert 'fork_turns: "none"' in rules
    assert 'fork_turns: "all"' in rules
    for blind_role in ("verifier", "challenger"):
        assert blind_role in rules
    assert "unbiased blind review" in rules
    assert "warm KV cache" in rules


def test_shared_workflow_documents_nuanced_fork_turns_and_reasoning_effort() -> None:
    shared = shared_text()
    assert "omit `agent_type`" in shared
    assert "`CODEX_HOME`" in shared and "live `spawn_agent` schema" in shared
    assert "custom `agent_type` applies regardless of `fork_turns`" in shared
    assert 'fork_turns: "none"' in shared
    assert 'fork_turns: "all"' in shared
    _assert_native_model_selection(shared)


def test_swarm_reasoning_effort_records_per_task_plan_and_observation() -> None:
    _, heading, rest = reference_text("reasoning-effort").partition("# Per-task reasoning effort and evidence")
    assert heading, "Swarm must define per-task reasoning evidence"
    rules = rest.split("\n## ", 1)[0]
    for expected in (
        "before first spawn", "`medium`", "`high`", "`ultra`", 'fork_turns="none"', 'fork_turns="all"',
        "`target_effort`", "`selection_reason`", "`planned_source`", "`override_unavailable_reason`",
        "`explicit`", "`role_override`", "`agents_default`", "`session_inheritance`", "`unknown`",
        "`body_md`", "`board_post`", "`codex_thread_id`", "first turn", "`observed_effort`",
        "`observed_source`", "`evidence_ref`", "`unverified`", "does not change verification gates",
    ):
        assert expected in rules


def test_shared_verification_contract_explains_group_boundaries_and_keepalive() -> None:
    shared = shared_corpus()
    assert "consecutive commands with the same `parallel_group`" in shared
    assert "ungrouped commands run serially" in shared
    assert "pass a positive `max_parallel`" in shared
    assert "`verify_argv_template`" in shared
    assert "--manifest ABS --output-dir NEW_ABS --heartbeat-url URL" in shared


@pytest.mark.parametrize("role_name", STRICT_NO_SPAWN_ROLES)
def test_strict_no_spawn_roles_forbid_subagents_in_toml(role_name: str) -> None:
    data = tomllib.loads((ROOT / "agents" / f"{role_name}.toml").read_text(encoding="utf-8"))
    instructions = data["developer_instructions"]
    assert "Strictly forbidden from spawning subagents" in instructions
    assert "challenger" in instructions
    assert "MAY spawn pure read-only `investigator` helpers" not in instructions


@pytest.mark.parametrize("role_name", DEPTH1_HELPER_ROLES)
def test_depth1_roles_allow_only_readonly_investigator_helpers_in_toml(role_name: str) -> None:
    data = tomllib.loads((ROOT / "agents" / f"{role_name}.toml").read_text(encoding="utf-8"))
    instructions = data["developer_instructions"]
    assert "Never spawn nested subagents" not in instructions
    assert "max_depth = 2" in instructions
    assert "MAY spawn pure read-only `investigator` helpers" in instructions
    assert "cross-module call-chain lookup" in instructions
    assert "NEVER edit files" in instructions
    assert "NEVER register as author/verifier on `18790`" in instructions
    assert "NEVER submit verification receipts" in instructions
    assert "solely accountable" in instructions or "sole original verifier accountable" in instructions


@pytest.mark.parametrize("mode", MODES)
def test_inline_role_contracts_enforce_tiered_max_depth_2_helper_rules(mode: str) -> None:
    text = reference_text("inline-roles")
    roles = _inline_role_map(text)
    assert roles
    for name, line in roles.items():
        assert "Never spawn subagents." not in line, (mode, name)
        if name in STRICT_NO_SPAWN_ROLES:
            assert "Strictly forbidden from spawning subagents" in line, (mode, name)
            assert "challenger" in line, (mode, name)
            assert "may spawn pure read-only `investigator` helpers" not in line, (mode, name)
        else:
            assert name in DEPTH1_HELPER_ROLES, (mode, name)
            assert "may spawn pure read-only `investigator` helpers" in line, (mode, name)
            assert "cross-module call-chain lookup ONLY" in line, (mode, name)
            assert "never edit files" in line, (mode, name)
            assert "never register as author/verifier on `18790`" in line, (mode, name)
            assert "never submit verification receipts" in line, (mode, name)
            assert "caller remains solely accountable" in line, (mode, name)


@pytest.mark.parametrize("mode", MODES)
def test_wait_agent_30s_compact_board_and_parallel_direct_mcp_calls_contract(mode: str) -> None:
    text = mode_corpus(mode)
    assert "wait_agent(timeout_ms=30000)" in text
    assert "compact=True" in text
    assert "<=30s" in text
    assert "parallel direct MCP tool calls" in text
    for readonly_tool in ("workspace_status", "workspace_diff", "workspace_merge_preview"):
        assert readonly_tool in text
    assert "collaboration.*" in text
    assert "top-level direct calls" in text
    assert "isError" in text
    assert "automatic retries" in text


@pytest.mark.parametrize("mode", MODES)
def test_workflow5_macro_rpc_primary_path_and_reconciliation_fence(mode: str) -> None:
    text = mode_corpus(mode)
    for required in (
        "workflow5",
        "0.6.11/schema10",
        "workflow_prepare_dispatch",
        "task_name",
        "/root/<task_name>",
        "zero waiting-only bootstrap empty turns",
        "native_action_record_call",
        "workflow_author_turn",
        'phase="accept"|"submit"',
        "workflow_verifier_turn",
        'phase="prepare"',
        'phase="record"',
        "workflow_closeout_run",
        "cli_executables",
        "tri-orchestrator-challenge",
        "env_profile",
        "windows_minimal_py",
        "posix_minimal_py",
        'list_agents(path_prefix="/root/<task_name>")',
        "needs_reconciliation",
        "session_id",
        "board_status.workflow.workflow_version",
    ):
        assert required in text, (mode, required)


def test_shared_workflow_and_agent_tomls_streamline_legacy_and_adopt_role_focused_macros() -> None:
    shared = shared_corpus()
    for required in (
        "workflow_prepare_dispatch",
        "native_action_record_call",
        "workflow_author_turn",
        "workflow_verifier_turn",
        "workflow_closeout_run",
        "verify_executable",
        "verify_command_hint",
        "cli_executables",
        "tri-orchestrator-challenge",
        "windows_minimal_py",
        "posix_minimal_py",
        'list_agents(path_prefix="/root/<task_name>")',
        "board_status.workflow.workflow_version",
        "parallel direct MCP tool calls",
        "collaboration.*",
        "wait_agent(timeout_ms=30000)",
        "compact=True",
    ):
        assert required in shared, required

    for path in sorted((ROOT / "agents").glob("*.toml")):
        raw = path.read_text(encoding="utf-8")
        data = tomllib.loads(raw)
        instructions = data["developer_instructions"]
        assert "workflow5" in instructions, path
        assert "0.6.11/schema10" in instructions, path
        assert "board_status.workflow.workflow_version" in instructions, path
        assert "skills/route-a-workflow.md" not in instructions, f"{path} must not require broken relative path"
        assert "self-contained" in instructions, path
        # Streamlined Route A footer stays compact (<= 6 bullet lines under ## Route A structured workflow)
        footer = instructions.split("## Route A structured workflow", 1)[1].strip().splitlines()
        assert len(footer) <= 6, (path, len(footer))

    # Read-only interviewer/investigator stay free of coordinator dispatch/closeout boilerplate
    for readonly_role in ("interviewer", "investigator"):
        instructions = tomllib.loads((ROOT / f"agents/{readonly_role}.toml").read_text(encoding="utf-8"))["developer_instructions"]
        assert "workflow_prepare_dispatch" not in instructions, readonly_role
        assert "workflow_closeout_run" not in instructions, readonly_role

    investigator = tomllib.loads((ROOT / "agents/investigator.toml").read_text(encoding="utf-8"))["developer_instructions"]
    assert "tri-orchestrator-challenge" in investigator and "cli_executables" in investigator

    for role_name in ("coder", "swarm_worker"):
        instructions = tomllib.loads((ROOT / f"agents/{role_name}.toml").read_text(encoding="utf-8"))["developer_instructions"]
        assert "workflow_author_turn" in instructions and "execution_kind" in instructions, role_name

    planner = tomllib.loads((ROOT / "agents/planner.toml").read_text(encoding="utf-8"))["developer_instructions"]
    assert "execution_kind" in planner

    verifier = tomllib.loads((ROOT / "agents/verifier.toml").read_text(encoding="utf-8"))["developer_instructions"]
    for term in (
        "workflow_verifier_turn",
        'phase="prepare"',
        'phase="record"',
        'inbox_ack(status="accepted")',
        'inbox_ack(status="completed")',
        'action="challenge"',
        "verify_executable",
        "cli_executables",
        "tri-orchestrator-challenge",
        "execution_kind",
    ):
        assert term in verifier, term

    swarm_worker = tomllib.loads((ROOT / "agents/swarm_worker.toml").read_text(encoding="utf-8"))["developer_instructions"]
    assert "body_md=" in swarm_worker
    assert "post_board_note=True" in swarm_worker
    assert "do NOT duplicate with a separate `board_post`" in swarm_worker


def test_documented_m6_m8_fields_align_with_public_tool_catalog() -> None:
    from orchestrator.rpc_handlers import get_tool_definitions

    schemas = {tool["name"]: tool["inputSchema"] for tool in get_tool_definitions()}
    prep_props = set(schemas["workflow_prepare_dispatch"]["properties"])
    assert {"run_id", "task_name", "create_workspace", "dispatch_action", "session_id"} <= prep_props
    rec_props = set(schemas["native_action_record_call"]["properties"])
    assert {"run_id", "outcome", "raw_response", "claim_token", "expected_revision"} <= rec_props
    turn_props = schemas["workflow_author_turn"]["properties"]
    assert set(turn_props["phase"]["enum"]) == {"accept", "submit"}
    assert {"body_md", "post_board_note"} <= set(turn_props)
    ver_turn_props = schemas["workflow_verifier_turn"]["properties"]
    assert set(ver_turn_props["phase"]["enum"]) == {"prepare", "record"}
    assert {
        "run_id",
        "step_id",
        "message_id",
        "scope",
        "verification_commands",
        "env_profile",
        "artifact_id",
        "verdict",
        "receipt_files",
        "review_dimensions",
        "post_board_note",
    } <= set(ver_turn_props)
    closeout_schema = schemas["workflow_closeout_run"]
    assert "run_id" in closeout_schema["properties"]
    assert closeout_schema["required"] == ["run_id"]
    assert "session_hook_check" not in schemas
    env_enum = set(schemas["artifact_capture"]["properties"]["env_profile"]["enum"])
    assert {"none", "windows_minimal_py", "posix_minimal_py"} <= env_enum
    assert schemas["board_status"]["properties"]["compact"]["type"] == "boolean"
    assert "session_id" in schemas["run_create"]["properties"]
