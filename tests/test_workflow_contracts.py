"""Static workflow contracts; these checks do not prove native-agent execution."""

from pathlib import Path
import re

import pytest

from skill_docs import mode_corpus, reference_text, shared_text


ROOT = Path(__file__).resolve().parents[1]
SKILLS = ("boost", "teamwork", "swarm")


def skill_text(name: str) -> str:
    return (ROOT / "plugin" / "skills" / name / "SKILL.md").read_text(encoding="utf-8")


def test_boost_rework_retains_the_actual_author_and_verifier() -> None:
    text = skill_text("boost")
    rework = text.split("### 4. Same-Thread Rework Loop", 1)[1].split("### 5.", 1)[0]
    assert "**Solo Path**" in rework
    assert "coordinator fixes" in rework
    assert "no coder thread exists" in rework
    assert "**Delegation Path**" in rework
    assert "exact original `coder` thread" in rework
    assert "same original `verifier`" in rework
    assert "followup_task" in rework


def test_boost_reads_human_text_with_a_durable_cursor_and_honest_receipts() -> None:
    text = reference_text("human-control")
    assert 'board_read(run_id=run_id, reader_name="coordinator", advance_cursor=False)' in text
    assert 'since_seq=batch["since_seq"], limit=batch["count"], advance_cursor=True' in text
    assert "Only after recording the batch" in text
    assert "not exactly-once delivery" in text
    assert "before dispatching/reworking, before and after waits, and before run closure" in text
    assert 'channel="human"' in text and 'msg_type="human"' in text
    assert "without channel or mention filters" in text
    assert "durable cursor" in text
    assert "received" in text and "planned action" in text and "completion evidence" in text
    assert "does not automatically deliver" in text
    assert "advancing the cursor is not completion" in text
    assert "send_message" in text and "followup_task" in text


@pytest.mark.parametrize("name", SKILLS)
def test_workspace_contract_distinguishes_git_and_snapshot(name: str) -> None:
    text = shared_text() + "\n" + skill_text(name)
    assert "## Workspaces" in shared_text() and "../route-a-workflow.md" in skill_text(name)
    assert '`mode="git_clone"`' in text
    assert '`mode="snapshot"`' in text
    assert "`branch_name=None` is normal" in text
    assert "`dirty=true` before merge is expected" in text
    assert "three-way snapshot" in text
    assert "workspace_diff" in text
    assert "Do not require a Git commit or a clean Git worktree" in text
    role = "swarm_worker" if name == "swarm" else "coder"
    inline_role = next(line for line in reference_text("inline-roles").splitlines() if line.startswith(f"- **`{role}` ("))
    assert 'only for `git_clone`' in inline_role
    assert 'snapshot' in inline_role
    assert "to verify clean commits" not in mode_corpus(name)
    assert "to verify clean git status" not in mode_corpus(name)


@pytest.mark.parametrize("name", SKILLS)
def test_linked_dependencies_are_not_promised_to_be_isolated(name: str) -> None:
    text = shared_text()
    assert '`dep_mode="link_ro_cache"`' in text
    assert "shared writable dependencies" in text
    assert "not read-only or write-isolated" in text
    assert '`dep_mode="copy"`' in text and '`dep_mode="fresh_venv_uv"`' in text
    assert "(Use `dep_mode=\"link_ro_cache\"`" not in mode_corpus(name)


@pytest.mark.parametrize("name", SKILLS)
def test_pre_spawn_registration_is_not_reported_as_working(name: str) -> None:
    text = mode_corpus(name)
    registration_examples = re.findall(r"worker_register\([^\n`]*\)", text)
    assert registration_examples
    pre_spawn = [call for call in registration_examples if "agent_id=" not in call]
    assert all('status="working"' not in call for call in pre_spawn)
    assert 'status="waiting"' in text
    assert 'status="working"' in text  # Still reported after real dispatch.


def test_readme_explains_dependency_and_snapshot_boundaries() -> None:
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "link_ro_cache" in text
    assert "共享可写" in text
    assert "fresh_venv_uv" in text
    assert "branch_name=None" in text
    assert "dirty=true" in text
    assert "不会自动" in text
