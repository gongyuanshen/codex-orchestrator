"""Static M4 instruction checks; not evidence that model routing works on real tasks."""
from pathlib import Path
import re
import tomllib

ROOT = Path(__file__).resolve().parents[1] / "plugin"


def test_boost_routes_unknowns_from_evidence_not_file_count():
    text = (ROOT / "skills/boost/SKILL.md").read_text(encoding="utf-8")
    route = text.split("### 2. Route Complexity: Solo vs Delegation", 1)[1].split("### 3.", 1)[0]
    for term in ("substantive unknowns", "investigator", "actual callers", "shared state", "reproduction", "independent work", "Known local", "no extra author", "route_decision", 'msg_type="info"', "packet version", "grounds", "boundary"):
        assert term in route, term
    assert "1–2 tightly coupled files" not in route
    assert route.index("investigator") < route.index("route_decision") < route.index('action="dispatch"')


def test_shared_challenger_returns_findings_to_original_gate_and_budget():
    text = (ROOT / "skills/route-a-workflow.md").read_text(encoding="utf-8")
    section = text.split("## M4 evidence-based routing and counterexamples", 1)[1].split("\n## ", 1)[0]
    for term in ("clean-context", "read-only challenger", "original author", "original verifier", "workflow_review", "acceptance criterion", "FAIL", "remaining", "3 automatic rework", "genuinely new requirement", "explicit packet revision", "new independent scope", "same failed step", "does not prove routing quality"):
        assert term in section, term


def test_investigation_is_a_solo_report_not_an_invented_code_merge():
    text = (ROOT / "skills/boost/SKILL.md").read_text(encoding="utf-8")
    for term in ("Pure investigation", "Solo report contract", "investigator stays read-only", "approved report scope", "independent verifier", "no invented code edits, workspace or merge"):
        assert term in text, term
    role = tomllib.loads((ROOT / "agents/investigator.toml").read_text(encoding="utf-8"))
    assert "Pure investigation" in role["developer_instructions"]
    assert role["sandbox_mode"] == "read-only"


def test_all_roles_and_inline_roles_share_m4_boundaries_without_model_override():
    for path in sorted((ROOT / "agents").glob("*.toml")):
        role = tomllib.loads(path.read_text(encoding="utf-8"))
        text = role["developer_instructions"]
        for term in ("M4", "original author", "original verifier", "read-only challenger", "3 automatic rework"):
            assert term in text, (path, term)
        assert "model" not in role
    for mode in ("boost", "teamwork", "swarm"):
        text = (ROOT / f"skills/{mode}/SKILL.md").read_text(encoding="utf-8")
        inline = [line for line in text.splitlines() if re.match(r"- \*\*`\w+` \(", line)]
        assert inline and all("shared M3/M4/M5 workflow4" in line for line in inline), mode


def test_boost_routing_benchmark_requires_actual_defects_not_role_counts():
    text = (ROOT / "skills/boost/SKILL.md").read_text(encoding="utf-8")
    for term in ("M0", "actual defects", "unplanned human rescue", "empty waits", "static instruction checks", "routing quality"):
        assert term in text, term
