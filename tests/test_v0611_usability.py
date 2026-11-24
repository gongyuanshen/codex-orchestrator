"""v0.6.11 usability: layered skills, fixed-load budgets, MCP tool tiers and folded options.

These checks guard the *cost* of a session (what is always loaded) while the contract tests in
test_m*_contracts.py guard the *semantics* that moved into on-demand reference files.
"""
from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

import pytest

from orchestrator.board import BoardService
from orchestrator.config import Settings
from orchestrator.db import Database
from orchestrator.rpc_handlers import (
    ADVANCED_TOOL_NAMES,
    PREPARE_DISPATCH_OPTION_KEYS,
    TOOL_TIER_ENV,
    RpcDispatcher,
    get_mcp_tool_definitions,
    get_tool_definitions,
    resolve_tool_tier,
)
from orchestrator.workspace_mgr import WorkspaceManager
from skill_docs import (
    MODES,
    PLUGIN,
    REFERENCE_DIR,
    body_without_frontmatter,
    reference_paths,
    reference_text,
    shared_text,
    skill_text,
)

# Budgets in UTF-8 bytes.  v0.6.10 loaded ~39.7KB of rules for one $boost (route-a 21.2KB +
# boost 18.5KB) and advertised a 24.5KB tool catalog.
SKILL_BODY_BUDGET = 4096
SHARED_BUDGET = 9600
BOOST_FIXED_TEXT_BUDGET = 14_500
CORE_CATALOG_BUDGET = 18_000


def _size(text: str) -> int:
    return len(text.encode("utf-8"))


def _compact(definitions: list[dict]) -> int:
    return len(json.dumps(definitions, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))


# ---------------------------------------------------------------------------
# Layered skills & budgets
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("mode", MODES)
def test_skill_body_fits_budget(mode: str) -> None:
    body = body_without_frontmatter(skill_text(mode))
    assert _size(body) <= SKILL_BODY_BUDGET, (mode, _size(body))


def test_shared_contract_and_boost_fixed_load_fit_budget() -> None:
    assert _size(shared_text()) <= SHARED_BUDGET, _size(shared_text())
    fixed = _size(skill_text("boost")) + _size(shared_text())
    assert fixed <= BOOST_FIXED_TEXT_BUDGET, fixed


def test_core_tool_catalog_fits_budget_and_is_smaller_than_full() -> None:
    core, full = get_mcp_tool_definitions("core"), get_mcp_tool_definitions("full")
    assert _compact(core) <= CORE_CATALOG_BUDGET, _compact(core)
    assert _compact(core) < _compact(full)
    assert _compact(core) + _size(shared_text()) + _size(skill_text("boost")) <= 22_000


def test_reference_table_links_exist_and_every_reference_is_routed() -> None:
    shared = shared_text()
    linked = set(re.findall(r"\(reference/([a-z0-9-]+)\.md\)", shared))
    existing = {path.stem for path in reference_paths()}
    assert linked, "route-a must route to on-demand references"
    assert linked <= existing, linked - existing
    assert existing <= linked, f"unrouted reference files: {existing - linked}"
    for mode in MODES:
        for target in re.findall(r"\(\.\./reference/([a-z0-9-]+)\.md\)", skill_text(mode)):
            assert target in existing, (mode, target)
    for path in reference_paths():
        for target in re.findall(r"\]\(([a-z0-9-]+)\.md\)", path.read_text(encoding="utf-8")):
            assert target in existing, (path.name, target)
        assert (REFERENCE_DIR / "../route-a-workflow.md").resolve().exists()


def test_reference_directory_is_not_mistaken_for_a_skill() -> None:
    assert not (REFERENCE_DIR / "SKILL.md").exists()
    assert {path.parent.name for path in (PLUGIN / "skills").glob("*/SKILL.md")} == set(MODES)


def test_no_long_lines_duplicated_across_always_loaded_files() -> None:
    """Shared rules live once in route-a; modes reference them instead of restating them."""
    seen: dict[str, str] = {}
    docs = {"route-a": shared_text(), **{mode: body_without_frontmatter(skill_text(mode)) for mode in MODES}}
    for name, text in docs.items():
        for line in text.splitlines():
            normalized = line.strip()
            if len(normalized) < 120:
                continue
            assert normalized not in seen, f"line duplicated in {seen.get(normalized)} and {name}: {normalized[:80]}"
            seen[normalized] = name


def test_legacy_and_exception_flows_stay_out_of_the_always_loaded_layer() -> None:
    always = [shared_text(), *(skill_text(mode) for mode in MODES)]
    for text in always:
        assert "`workflow1..4` fallback" not in text
        assert "claim_group" not in text
        assert "## Manual primitive transfer ledger" not in text
        assert "## Inline Fallback Role Contracts" not in text
    assert "`workflow1..4` fallback" in reference_text("legacy-resume")
    assert "claim_group" in reference_text("transfers-and-recovery")


@pytest.mark.parametrize("mode", MODES)
def test_mode_files_keep_their_own_normal_path_macros(mode: str) -> None:
    text = skill_text(mode)
    for macro in ("workflow_prepare_dispatch", "native_action_record_call", "workflow_closeout_run", "../route-a-workflow.md"):
        assert macro in text, (mode, macro)


def test_reasoning_effort_policy_is_shared_by_all_modes() -> None:
    shared = shared_text()
    assert "`medium`" in shared and "`high`" in shared and "`ultra`" in shared
    assert "reference/reasoning-effort.md" in shared
    assert "choose it for the task or omit it" not in shared
    for mode in MODES:
        assert "choose it for the task or omit it" not in skill_text(mode)


def test_teamwork_clarifies_inline_and_keeps_interviewer_optional() -> None:
    text = skill_text("teamwork")
    step1 = text.split("### Step 1:", 1)[1].split("### Step 2:", 1)[0]
    assert "main conversation" in step1 and "ONE high-leverage question" in step1
    assert "spawn_agent" not in step1
    assert "explicitly asks" in step1
    assert (PLUGIN / "agents" / "interviewer.toml").exists()


# ---------------------------------------------------------------------------
# MCP tool tiers
# ---------------------------------------------------------------------------

def test_core_tier_hides_only_advanced_tools_and_adds_proxy() -> None:
    full_names = {tool["name"] for tool in get_tool_definitions()}
    core_names = {tool["name"] for tool in get_mcp_tool_definitions("core")}
    assert core_names == full_names - ADVANCED_TOOL_NAMES
    assert "tool_advanced" in core_names
    assert ADVANCED_TOOL_NAMES <= full_names
    assert {tool["name"] for tool in get_mcp_tool_definitions("full")} == full_names


def test_tool_tier_env_selection(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(TOOL_TIER_ENV, raising=False)
    assert resolve_tool_tier() == "core"
    monkeypatch.setenv(TOOL_TIER_ENV, " FULL ")
    assert resolve_tool_tier() == "full"
    assert len(get_mcp_tool_definitions()) == len(get_tool_definitions())
    monkeypatch.setenv(TOOL_TIER_ENV, "everything")
    with pytest.raises(ValueError, match=TOOL_TIER_ENV):
        resolve_tool_tier()


def test_core_prepare_dispatch_schema_folds_low_frequency_fields() -> None:
    core = next(t for t in get_mcp_tool_definitions("core") if t["name"] == "workflow_prepare_dispatch")
    full = next(t for t in get_tool_definitions() if t["name"] == "workflow_prepare_dispatch")
    core_props, full_props = set(core["inputSchema"]["properties"]), set(full["inputSchema"]["properties"])
    assert not core_props & PREPARE_DISPATCH_OPTION_KEYS
    assert "options" in core_props and "options" in full_props
    assert PREPARE_DISPATCH_OPTION_KEYS <= full_props
    assert len(core_props) < len(full_props)
    for required in ("run_id", "task_name", "create_workspace", "dispatch_action", "session_id", "message_key", "criteria"):
        assert required in core_props, required
    for key in sorted(PREPARE_DISPATCH_OPTION_KEYS):
        assert key in core["inputSchema"]["properties"]["options"]["description"], key


def _mentioned_tools(text: str, catalog: set[str]) -> set[str]:
    return {name for name in catalog if re.search(rf"(?<![A-Za-z0-9_]){re.escape(name)}(?![A-Za-z0-9_])", text)}


def test_advanced_role_tools_have_gateway_guidance_and_normal_path_needs_no_proxy() -> None:
    catalog = {tool["name"] for tool in get_tool_definitions()}
    core = {tool["name"] for tool in get_mcp_tool_definitions("core")}
    advanced_doc = reference_text("advanced-tools")
    for name in ADVANCED_TOOL_NAMES:
        assert f"`{name}`" in advanced_doc, name
    for path in [*(PLUGIN / "agents").glob("*.toml"), REFERENCE_DIR / "inline-roles.md"]:
        role = path.read_text(encoding="utf-8")
        if _mentioned_tools(role, ADVANCED_TOOL_NAMES):
            assert "tool_advanced(tool=" in role, path.name
    # The always-loaded normal path itself needs no proxy hop.
    normal = shared_text().split("## Normal path", 1)[1].split("\n## ", 1)[0]
    for name in _mentioned_tools(normal, catalog):
        assert name in core, name


# ---------------------------------------------------------------------------
# Dispatcher behaviour: options folding & proxy
# ---------------------------------------------------------------------------

@pytest.fixture
def env(tmp_path: Path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "main.py").write_text("VALUE = 1\n", encoding="utf-8")
    settings = Settings(data_dir=tmp_path / "data", port_range=(29100, 29150))
    db = Database(settings=settings)
    board = BoardService(db, settings)
    dispatcher = RpcDispatcher(db, board, WorkspaceManager(db, settings))
    yield project, db, dispatcher
    db.close()


def _run_with_packet(dispatcher: RpcDispatcher, project: Path) -> str:
    run = dispatcher.dispatch("run_create", {"mode": "boost", "title": "Tier", "project_root": str(project)})
    dispatcher.dispatch("task_packet_save", {
        "run_id": run["id"], "title": "Tier", "goal": "Exercise folded options",
        "file_ownership": {"coder_1": ["main.py"]}, "acceptance_criteria": ["AC-1"],
    })
    return run["id"]


def test_options_are_folded_and_validated(env) -> None:
    _, _, dispatcher = env
    folded = dispatcher.validate_arguments(
        "workflow_prepare_dispatch", {"run_id": "r", "options": {"dep_mode": "copy", "step_kind": "milestone"}},
    )
    assert folded == {"run_id": "r", "dep_mode": "copy", "step_kind": "milestone"}
    assert dispatcher.validate_arguments("workflow_prepare_dispatch", {"run_id": "r", "options": None}) == {"run_id": "r"}
    with pytest.raises(ValueError, match="Unknown workflow_prepare_dispatch.options keys"):
        dispatcher.validate_arguments("workflow_prepare_dispatch", {"run_id": "r", "options": {"owner": "x"}})
    with pytest.raises(ValueError, match="both top-level and in options"):
        dispatcher.validate_arguments(
            "workflow_prepare_dispatch", {"run_id": "r", "dep_mode": "copy", "options": {"dep_mode": "none"}},
        )
    with pytest.raises(ValueError, match="must be a JSON object"):
        dispatcher.validate_arguments("workflow_prepare_dispatch", {"run_id": "r", "options": ["dep_mode"]})
    with pytest.raises(ValueError, match="must be a boolean"):
        dispatcher.validate_arguments("workflow_prepare_dispatch", {"run_id": "r", "options": {"copy_env": "yes"}})
    with pytest.raises(ValueError, match="Unknown arguments"):
        dispatcher.validate_arguments("workflow_step_save", {"run_id": "r", "options": {}})


def test_prepare_dispatch_accepts_folded_options_end_to_end(env) -> None:
    project, db, dispatcher = env
    run_id = _run_with_packet(dispatcher, project)
    prep = dispatcher.dispatch("workflow_prepare_dispatch", {
        "run_id": run_id, "owner": "coder_1", "task_name": "coder_1", "verifier": "verifier_1",
        "step_key": "slice-1", "criteria": ["AC-1"], "instruction": "Implement slice 1",
        "create_workspace": True,
        "options": {"dep_mode": "none", "copy_env": False, "stall_threshold_sec": 900},
    })
    assert prep["action_id"] and prep["claim_token"]
    worker = db.get_worker_by_name(run_id, "coder_1")
    assert worker is not None and worker["stall_threshold_sec"] == 900


def test_tool_advanced_describes_and_calls_hidden_tools(env) -> None:
    project, _, dispatcher = env
    run_id = _run_with_packet(dispatcher, project)
    described = dispatcher.dispatch("tool_advanced", {"tool": "board_channel_list"})
    assert described["tool"] == "board_channel_list"
    assert described["inputSchema"]["required"] == ["run_id"]
    via_proxy = dispatcher.dispatch("tool_advanced", {"tool": "board_channel_list", "arguments": {"run_id": run_id}})
    direct = dispatcher.dispatch("board_channel_list", {"run_id": run_id})
    assert via_proxy == direct
    with pytest.raises(ValueError, match="Unknown arguments"):
        dispatcher.dispatch("tool_advanced", {"tool": "board_channel_list", "arguments": {"run_id": run_id, "bogus": 1}})
    with pytest.raises(ValueError, match="only proxies advanced-tier tools"):
        dispatcher.dispatch("tool_advanced", {"tool": "run_create", "arguments": {}})
    with pytest.raises(ValueError, match="must be a JSON object"):
        dispatcher.dispatch("tool_advanced", {"tool": "board_channel_list", "arguments": "run"})

def test_solo_accept_and_submit_with_real_ticket_and_atomic_failure(env):
    from orchestrator.workflow import WorkflowConflictError
    project, db, rpc = env
    run = _run_with_packet(rpc, project)
    start = dict(run_id=run, dispatch_action="accept_local", step_key="one", owner="coder_1", verifier="verifier",
                 options=dict(agent_id="/root", advance_evidence="inspected frozen packet"))
    step = rpc.dispatch("workflow_prepare_dispatch", start)["step"]
    assert step["state"] == "implementing" and step["workspace_id"] is None
    assert not db.connect().execute("select 1 from inbox_messages").fetchone()
    with pytest.raises(WorkflowConflictError, match="already exists"):
        rpc.dispatch("workflow_prepare_dispatch", start)
    submit = dict(run_id=run, dispatch_action="submit_local_for_verify", step_id=step["id"], message_key="review-one",
                  options=dict(expected_step_revision=step["revision"], advance_agent_id="/root"))
    with pytest.raises(WorkflowConflictError, match="task_name"):
        rpc.dispatch("workflow_prepare_dispatch", submit)
    assert rpc.workflow.get_step(step['id'])['state'] == 'implementing'
    sent = rpc.dispatch("workflow_prepare_dispatch", dict(submit, verifier_task_name="verifier_1"))
    assert sent["step"]["state"] == "candidate_review" and sent["message_id"]
    snapshot = db.get_worker_by_name(run, "verifier")
    with pytest.raises(WorkflowConflictError, match="Cannot prepare integrated"):
        rpc.dispatch("workflow_verifier_turn", dict(run_id=run, step_id=step["id"], phase="prepare", scope="integrated"))
    assert db.get_worker_by_name(run, "verifier") == snapshot
    assert rpc.inbox.get(sent["message_id"])["status"] == "pending"

    # Exercise the same ticket through confirmation, a real subprocess receipt, and final closeout.
    import sys
    from orchestrator.verify_runner import collect
    rpc.dispatch("native_action_record_call", dict(run_id=run, action_id=sent["action_id"],
        claim_token=sent["claim_token"], expected_revision=sent["expected_revision"], outcome="confirmed",
        raw_response={"agent_id": "/root/verifier_1"}))
    artifact = rpc.dispatch("workflow_verifier_turn", dict(run_id=run, step_id=step["id"], phase="prepare",
        verification_commands=[dict(id="read", argv=[sys.executable, "-c",
            "from pathlib import Path; assert Path('main.py').read_text().strip() == 'VALUE = 1'"], cwd=str(project), timeout_sec=10,
            env_keys=[], criteria=["AC-1"])]))["artifact"]
    output = db.settings.data_dir / "smoke-receipt"
    collect(artifact["manifest_path"], output)
    rpc.dispatch("workflow_verifier_turn", dict(run_id=run, step_id=step["id"], phase="record",
        verdict="PASS", artifact_id=artifact["id"], receipt_files=[str(output / "receipt.json")]))
    rpc.dispatch("workflow_closeout_run", dict(run_id=run))
    assert db.get_run(run)["status"] == "completed"
    assert len(rpc.workflow.summary(run)["verifications"]) == 1
