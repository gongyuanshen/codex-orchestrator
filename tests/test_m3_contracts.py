"""Workflow5 (0.6.13/schema10) static instruction agreement and public schema parity."""
from pathlib import Path
import re
import tomllib
import pytest

from skill_docs import mode_corpus, reference_text, shared_corpus, skill_text

ROOT = Path(__file__).resolve().parents[1] / "plugin"


def test_shared_workflow5_preserves_legacy_and_admission_proof_boundaries():
    text = shared_corpus()
    assert "New runs default to `workflow5` (`0.6.13/schema10`" in text
    assert "New runs use workflow4" not in text
    assert "software 0.5.0/schema7" not in text
    for term in (
        "plan_readiness",
        "resume_context",
        "read-only",
        "draft",
        "atomic",
        "Windows",
        "glob",
        "path-alias",
        "native quiescence",
        "first admission",
        "merge-chain",
        "original verifier",
        "final PASS",
        "workflow0..4",
        "handoff_history",
        "handoff_digest",
        "intended recipient",
        "next_responsibility",
        "forbidden_actions",
        "decision_seqs",
        "ordinary notices",
        "needs_decision",
    ):
        assert term.lower() in text.lower(), term


def test_workflow5_challenger_and_independent_review_dimensions_preserve_single_gate():
    text = shared_corpus()
    for term in (
        'complexity="high"',
        "workflow_review(step_id, expected_revision, agent_id, criterion, evidence)",
        "review_dimensions",
        "specification",
        "code",
        "stress",
        "evidence",
        "resolved_finding_ids",
        "PASS|FAIL|NOT_RUN",
        "all four `PASS`",
        "optional on normal complexity",
        "dismissed_finding_ids",
    ):
        assert term in text, term


@pytest.mark.parametrize("mode", ("boost", "teamwork", "swarm"))
def test_modes_and_inline_roles_use_current_workflow5(mode):
    text = mode_corpus(mode)
    assert "../route-a-workflow.md" in skill_text(mode)
    assert "workflow5" in skill_text(mode)
    for term in (
        "workflow5",
        "0.6.13/schema10",
        "workflow0..4",
        "plan_readiness",
        "resume_context",
        "handoff_digest",
        "workflow_review",
        "review_dimensions",
        "native_action_update",
        "claim_group",
        "begin_group",
        "record_group",
        "reconcile_group",
    ):
        assert term in text, (mode, term)
    assert "New runs use workflow4" not in text
    assert "software 0.5.0/schema7" not in text
    assert "New runs use workflow3" not in text
    inline_section = reference_text("inline-roles")
    assert "../route-a-workflow.md" in inline_section
    assert re.search(r"- \*\*`\w+` \(", inline_section)


@pytest.mark.parametrize("path", sorted((ROOT / "agents").glob("*.toml")), ids=lambda p: p.stem)
def test_all_installed_roles_load_workflow5_without_permission_or_model_expansion(path):
    role = tomllib.loads(path.read_text(encoding="utf-8"))
    text = role["developer_instructions"]
    assert "software 0.5.0/schema7" not in text
    assert "New runs use workflow4" not in text
    for term in ("workflow5", "0.6.13/schema10", "workflow0..4", "board_status.workflow.workflow_version"):
        assert term in text, (path, term)
    assert "model" not in role
    assert "model_reasoning_effort" not in role
    if path.stem in {"investigator", "interviewer"}:
        assert role["sandbox_mode"] == "read-only"
    else:
        assert "sandbox_mode" not in role

    if path.stem == "planner":
        for term in ("plan_readiness", "resume_context", "review_dimensions", "handoff_digest"):
            assert term in text, (path, term)
    elif path.stem == "verifier":
        for term in ("review_dimensions", "resolved_finding_ids", "dismissed_finding_ids", "handoff_digest", "resume_context"):
            assert term in text, (path, term)
    elif path.stem == "swarm_worker":
        for term in ("resolved_finding_ids", "handoff_digest", "resume_context"):
            assert term in text, (path, term)
    elif path.stem == "coder":
        assert "resolved_finding_ids" in text, path


def test_swarm_group_wakeup_keeps_per_message_receipts_and_honest_stall_states():
    text = reference_text("transfers-and-recovery")
    section = text
    for term in (
        "next_action_groups",
        "source_type",
        "source_id",
        "source_digest",
        "action_id",
        "expected_revision",
        "claim_token",
        "group_id",
        "frozen membership",
        "inbox_ack",
        "notice-only",
        "controls first",
        "one in-flight",
        "new arrivals",
        "waiting for coordinator",
        "normal waiting",
        "dependency not ready",
        "user decision",
        "needs_reconciliation",
        "host state unknown",
        "unfinished task",
        "no executable progress",
        "actionable blocker",
    ):
        assert term.lower() in section.lower(), term


def test_planner_only_forbids_concurrent_overlap_not_approved_sequential_reuse():
    role = tomllib.loads((ROOT / "agents/planner.toml").read_text(encoding="utf-8"))
    text = role["developer_instructions"]
    assert "AT MOST ONE concurrent writer" in text
    assert "sequential same-file milestones" in text
    assert "native-quiescence" in text


def test_documented_m3_m5_fields_exist_in_the_shared_public_tool_catalog():
    from orchestrator.rpc_handlers import get_tool_definitions

    schemas = {tool["name"]: tool["inputSchema"] for tool in get_tool_definitions()}
    assert set(schemas["workflow_review"]["required"]) == {
        "step_id", "expected_revision", "agent_id", "criterion", "evidence"
    }
    assert schemas["workflow_step_save"]["properties"]["complexity"]["enum"] == ["normal", "high"]
    assert {"handoff_digest", "next_responsibility", "forbidden_actions", "decision_seqs"} <= set(
        schemas["workflow_step_advance"]["properties"]
    )
    review = schemas["verification_record"]["properties"]
    assert {"resolved_finding_ids", "dismissed_finding_ids"} <= set(review)
    assert set(review["review_dimensions"]["required"]) == {"specification", "code", "stress", "evidence"}
    native = schemas["native_action_update"]["properties"]
    assert {"claim_group", "begin_group", "record_group", "reconcile_group"} <= set(native["action"]["enum"])
    assert {"group_id", "members"} <= set(native)


def test_original_verifier_can_dismiss_only_unsubstantiated_findings_without_fake_rework():
    text = shared_corpus()
    for term in ("dismissed_finding_ids", "unsubstantiated", "without increasing attempt", "substantiated `FAIL` cannot be dismissed"):
        assert term in text, term
    for mode in ("boost", "teamwork", "swarm"):
        assert "dismissed_finding_ids" in mode_corpus(mode)
    verifier = tomllib.loads((ROOT / "agents/verifier.toml").read_text(encoding="utf-8"))["developer_instructions"]
    assert "dismissed_finding_ids" in verifier and "unsubstantiated" in verifier


def test_m5_recovery_uses_exact_frozen_group_projection_and_classifier_states():
    text = reference_text("transfers-and-recovery")
    for term in (
        "native_action_groups",
        "frozen_member_ids",
        "complete group membership",
        "potential_candidates",
        "normal_wait",
        "dependency_wait",
        "host_unknown",
        "no_progress",
    ):
        assert term in text, term


def test_solo_local_boundary_is_frozen_and_never_claims_host_idle():
    shared = shared_corpus()
    for term in (
        'execution_kind="coordinator_local"',
        'execution_kind="native"',
        "Boost without a workspace",
        "before first dispatch",
        "immutable after `original_bindings`",
        "coordinator_local_boundaries",
        "completed synchronous local edit/verification phase",
        "never claim root host idle",
        "stopped",
        "ended",
        "packet",
        "binding",
        "budget",
    ):
        assert term in shared, term
    boost = (ROOT / "skills/boost/SKILL.md").read_text(encoding="utf-8")
    route = boost.split("### 2. Route Complexity: Solo vs Delegation", 1)[1].split("### 3.", 1)[0]
    assert 'workflow_step_save(execution_kind="coordinator_local")' in route
    assert 'agent_id=step["owner_agent_id"]' in route
    assert "Pure investigation" in route and "report also uses `coordinator_local`" in route
    rework = boost.split("### 4. Same-Thread Rework Loop", 1)[1].split("### 5.", 1)[0]
    assert "coordinator_local_boundaries" in rework
    assert "nonblank evidence" in rework


def test_installed_and_inline_roles_retain_native_default_and_local_exception_scope():
    for name in ("planner", "coder", "swarm_worker", "verifier", "investigator"):
        text = tomllib.loads((ROOT / f"agents/{name}.toml").read_text(encoding="utf-8"))["developer_instructions"]
        assert "execution_kind" in text and "coordinator_local" in text, name
        assert "Boost" in text, name
    for mode in ("boost", "teamwork", "swarm"):
        text = mode_corpus(mode)
        assert "execution_kind" in text and "coordinator_local" in text
        inline_section = reference_text("inline-roles")
        assert "execution_kind" in inline_section, mode


def test_coordinator_local_enum_and_default_match_the_actual_public_catalog():
    from orchestrator.rpc_handlers import get_tool_definitions

    schema = next(tool["inputSchema"] for tool in get_tool_definitions() if tool["name"] == "workflow_step_save")
    execution = schema["properties"]["execution_kind"]
    assert execution["enum"] == ["native", "coordinator_local"]
    assert execution["default"] == "native"


def test_local_revision_proof_is_single_use_and_packet_then_step_needs_fresh_proof():
    shared = shared_corpus()
    for term in (
        'action="local_revision_boundary"',
        "single-use",
        "revision/packet/contract/attempt",
        "content_digest",
        "project content",
        "intervening observation",
        "filesystem edit",
        "`task_packet_save` consumes",
        "fresh `local_revision_boundary` before `workflow_step_save`",
        "each executed local step",
        "old criterion",
        "local_revision_boundaries",
    ):
        assert term in shared, term
    boost = (ROOT / "skills/boost/SKILL.md").read_text(encoding="utf-8")
    assert "local_revision_boundary → task_packet_save → fresh local_revision_boundary → workflow_step_save" in boost


def test_modes_and_planner_include_local_revision_protocol():
    planner = tomllib.loads((ROOT / "agents/planner.toml").read_text(encoding="utf-8"))["developer_instructions"]
    for term in ("local_revision_boundary", "single-use", "content_digest", "task_packet_save", "fresh", "workflow_step_save"):
        assert term in planner, term
    for mode in ("boost", "teamwork", "swarm"):
        text = mode_corpus(mode)
        assert "local_revision_boundary" in text, mode


def test_workflow_advance_public_action_enum_is_exact_including_local_revision_boundary():
    from orchestrator.rpc_handlers import get_tool_definitions

    schema = next(tool["inputSchema"] for tool in get_tool_definitions() if tool["name"] == "workflow_step_advance")
    actions = schema["properties"]["action"]["enum"]
    assert len(actions) == len(set(actions))
    assert set(actions) == {
        "dispatch",
        "accept",
        "submit",
        "challenge",
        "start_rework",
        "begin_integrated_review",
        "complete",
        "handoff",
        "ack_handoff",
        "reconcile",
        "observe",
        "reverify",
        "local_revision_boundary",
    }
