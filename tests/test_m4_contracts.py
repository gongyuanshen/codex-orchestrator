"""Static evidence-based routing & challenger instruction contracts."""
from pathlib import Path
import tomllib
from skill_docs import reference_text

ROOT = Path(__file__).resolve().parents[1] / "plugin"


def test_boost_routes_unknowns_from_evidence_not_file_count():
    text = (ROOT / "skills/boost/SKILL.md").read_text(encoding="utf-8")
    route = text.split("### 2. Route Complexity: Solo vs Delegation", 1)[1].split("### 3.", 1)[0]
    for term in (
        "substantive unknowns",
        "investigator",
        "actual callers",
        "shared state",
        "reproduction",
        "independent work",
        "known local",
        "no extra author",
        "route_decision",
        'msg_type="info"',
        "packet version",
        "grounds",
        "boundary",
    ):
        assert term in route, term
    assert "1–2 tightly coupled files" not in route
    assert route.index("investigator") < route.index("route_decision") < route.index('action="dispatch"')


def test_shared_challenger_returns_findings_to_original_gate_and_budget():
    text = reference_text("verification")
    section = text
    for term in (
        "read-only challenger",
        "original author",
        "original verifier",
        "workflow_review",
        "acceptance criterion",
        "FAIL",
        "3 automatic rework",
        "genuinely new requirement",
        "explicit packet revision",
        "new independent scope",
        "same failed step",
    ):
        assert term in section, term


def test_investigation_is_a_solo_report_not_an_invented_code_merge():
    text = (ROOT / "skills/boost/SKILL.md").read_text(encoding="utf-8")
    for term in (
        "Pure investigation",
        "Solo report contract",
        "`investigator` stays read-only",
        "approved report scope",
        "independent `verifier`",
        "no invented code edits, workspace, or merge",
    ):
        assert term in text, term
    role = tomllib.loads((ROOT / "agents/investigator.toml").read_text(encoding="utf-8"))
    assert "Pure investigation" in role["developer_instructions"]
    assert role["sandbox_mode"] == "read-only"


def test_all_roles_and_modes_share_rework_and_challenger_boundaries_without_model_override():
    for path in sorted((ROOT / "agents").glob("*.toml")):
        role = tomllib.loads(path.read_text(encoding="utf-8"))
        text = role["developer_instructions"]
        assert "original author" in text and "original verifier" in text, path
        assert "3 automatic rework" in text or "3-round rework" in text, path
        assert "model" not in role
        assert "model_reasoning_effort" not in role
    for challenger_role in ("interviewer", "investigator", "planner", "verifier"):
        text = tomllib.loads((ROOT / f"agents/{challenger_role}.toml").read_text(encoding="utf-8"))["developer_instructions"]
        assert "challenger" in text, challenger_role
    for mode in ("boost", "teamwork", "swarm"):
        text = (ROOT / f"skills/{mode}/SKILL.md").read_text(encoding="utf-8")
        inline_section = text + reference_text("inline-roles")
        assert "../route-a-workflow.md" in inline_section, mode


def test_boost_routing_judged_by_actual_defects_not_role_counts():
    text = (ROOT / "skills/boost/SKILL.md").read_text(encoding="utf-8")
    for term in ("actual defects", "unplanned human rescue", "empty waits", "subagent count"):
        assert term in text, term
    assert "benchmark against M0" not in text
    assert "static instruction checks" not in text
