"""S2 regression scenarios for run attribution and lifecycle isolation."""

from concurrent.futures import ThreadPoolExecutor
import json
import os
import subprocess
import sys
from pathlib import Path
from threading import Barrier

import pytest
from starlette.testclient import TestClient

from orchestrator import codex_bridge, db as db_module
from orchestrator.board import BoardService
from orchestrator.config import Settings
from orchestrator.daemon import create_app
from orchestrator.db import Database
from orchestrator.rpc_handlers import INTERNAL_RPC_DEFINITIONS, RpcDispatcher, TOOL_DEFINITIONS
from orchestrator.verify_runner import collect
from orchestrator.workspace_mgr import WORKSPACES_DIRNAME, WorkspaceManager


@pytest.fixture
def env(tmp_path, monkeypatch):
    # The live Codex home must not supply metadata to an isolated fixture.
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "unused-codex-home"))
    project = tmp_path / "root-project"
    project.mkdir()
    settings = Settings(data_dir=tmp_path / "data", port_range=(29100, 29130))
    with Database(settings=settings) as db:
        dispatcher = RpcDispatcher(db, BoardService(db, settings), WorkspaceManager(db, settings))

        def rpc(tool, **kwargs):
            return dispatcher.dispatch(tool, kwargs)

        yield project, db, rpc


def _git(path, *args):
    return subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True)


def _turn(rpc, session, turn, cwd, *, invoked=False):
    return rpc(
        "session_hook_check", session_id=session, event_name="UserPromptSubmit",
        cwd=str(cwd), turn_id=turn, prompt_preview=f"preview {turn}", has_mode_invocation=invoked,
    )


def _run(rpc, root, session, **extra):
    return rpc("run_create", mode="swarm", title=session, project_root=str(root), session_id=session, **extra)


def _codex_project(monkeypatch, root):
    project = {"project_id": "codex-main", "name": "Codex Main", "root_path": str(root)}
    monkeypatch.setattr(codex_bridge, "list_codex_projects", lambda: [project])
    return project


def test_s2_wire_schemas_retained_strict_and_extended():
    fields_by_tool = [
        (TOOL_DEFINITIONS, "run_create", {"turn_id": "string", "trigger_prompt": "string", "execution_root": "string"}),
        (INTERNAL_RPC_DEFINITIONS, "session_hook_check", {"turn_id": "string", "prompt_preview": "string", "has_mode_invocation": "boolean"}),
    ]
    for definitions, name, fields in fields_by_tool:
        schema = next(tool["inputSchema"] for tool in definitions if tool["name"] == name)
        assert schema["additionalProperties"] is False
        assert not set(fields).intersection(schema["required"])
        assert {key: schema["properties"][key]["type"] for key in fields if key in schema["properties"]} == fields


def test_t3_tracks_three_runs_and_continuation_under_one_project(env, monkeypatch):
    root, db, rpc = env
    _git(root, "init")
    parent = db.ensure_project(root, is_git=True)
    codex = _codex_project(monkeypatch, root)
    session = "thread"
    slices = [root / "artifacts" / f"iteration-{i}" / "slice" for i in range(1, 4)]
    for path in slices:
        path.mkdir(parents=True)

    assert not _turn(rpc, "ordinary", "turn-1", root)["bound"]
    assert db.list_session_turns("ordinary") == []
    runs = []
    for i, path in enumerate(slices, 1):
        turn_id = f"turn-{i}"
        previous_run = runs[-1]["id"] if runs else None
        check = _turn(rpc, session, turn_id, path, invoked=True)
        assert check["run_id"] == previous_run
        latest = db.list_session_turns(session)[0]
        assert (latest["turn_id"], latest["cwd"], latest["active_run_id"]) == (turn_id, str(path), previous_run)
        run = _run(rpc, path, session, project_name=path.name)
        runs.append(run)
        assert run["turn_id"] == turn_id
        assert run["trigger_prompt"] == f"preview {turn_id}"
        assert run["execution_root"] == str(path)
        assert run["project_id"] == parent["id"]
    assert [run["session_seq"] for run in runs] == [1, 2, 3]
    assert len(db.list_projects()) == 1
    project = db.get_project(parent["id"])
    assert project["is_git"]
    assert (project["codex_project_id"], project["codex_project_name"], project["name"]) == (
        codex["project_id"], codex["name"], codex["name"],
    )

    _turn(rpc, session, "turn-4-continuation", root)
    assert len(db.list_runs()) == 3
    turns = db.list_session_turns(session)
    assert [turn["turn_id"] for turn in turns] == ["turn-4-continuation", "turn-3", "turn-2", "turn-1"]
    assert turns[0]["active_run_id"] == runs[-1]["id"]
    assert turns[0]["prompt_preview"] == "preview turn-4-continuation"
    _turn(rpc, session, "return-to-first-slice", slices[0])
    assert db.list_session_turns(session)[0]["active_run_id"] == runs[0]["id"]

    _turn(rpc, "history-only", "invoked", root, invoked=True)
    _turn(rpc, "history-only", "passive", root)
    assert [row["turn_id"] for row in db.list_session_turns("history-only")] == ["passive", "invoked"]
    assert db.list_session_bindings("history-only") == []


def test_t3_infers_project_honors_explicit_fields_and_keeps_nested_git_independent(env, monkeypatch):
    root, db, rpc = env
    codex = _codex_project(monkeypatch, root)
    nested = root / "artifacts" / "local-repo"
    nested.mkdir(parents=True)
    _git(nested, "init")
    outside = root.parent / "root-project-sibling"
    outside.mkdir()
    threads = {
        "inferred": {
            "cwd": str(nested), "codex_project_root": str(root),
            "codex_project_id": codex["project_id"], "codex_project_name": codex["name"],
        },
        "cwd-only": {
            "cwd": str(outside), "codex_project_root": None,
            "codex_project_id": None, "codex_project_name": None,
        },
    }
    monkeypatch.setattr(codex_bridge, "get_thread_meta", threads.get)
    db.upsert_session_turn("inferred", "seen", cwd=str(root), prompt_preview="stored preview")
    inferred = rpc(
        "run_create", mode="swarm", title="inferred", session_id="inferred",
        turn_id="explicit-turn", trigger_prompt="full prompt", execution_root=str(nested),
    )
    project = db.get_project(inferred["project_id"])
    assert (project["root_path"], project["codex_project_id"], project["name"]) == (
        str(root), codex["project_id"], codex["name"],
    )
    assert (inferred["turn_id"], inferred["trigger_prompt"], inferred["execution_root"]) == (
        "explicit-turn", "full prompt", str(nested),
    )
    by_id = rpc("run_create", mode="swarm", title="by id", project_id=project["id"], session_id="inferred")
    assert (by_id["project_id"], by_id["turn_id"], by_id["trigger_prompt"]) == (project["id"], "seen", "stored preview")

    nested_run = _run(rpc, nested, "independent", is_git=False, project_name="Local Git")
    local = db.get_project(nested_run["project_id"])
    assert nested_run["execution_root"] == ""
    assert local["id"] != project["id"]
    assert (local["root_path"], local["name"], local["codex_project_id"]) == (
        str(nested), "Local Git", codex["project_id"],
    )
    outside_run = rpc("run_create", mode="swarm", title="cwd only", session_id="cwd-only")
    assert db.get_project(outside_run["project_id"])["root_path"] == str(outside)


def test_t3_uses_deepest_registered_workspace_without_codex_metadata(env, monkeypatch):
    root, db, rpc = env
    parent = root / "nested-parent"
    trial = parent / "slice"
    trial.mkdir(parents=True)
    original_classify = db_module.classify_project_kind

    # Pytest stores this example in temp; model the project roots' persisted workspace classification.
    roots = {str(root), str(parent)}
    monkeypatch.setattr(db_module, "classify_project_kind",
                        lambda path: "workspace" if str(path) in roots else original_classify(path))
    db.ensure_project(root)
    project = db.ensure_project(parent, name="Registered parent", codex_project_id="external-id",
                                codex_project_name="Previously linked")
    run = _run(rpc, trial, "real-project", project_name=trial.name)
    assert run["project_id"] == project["id"]
    assert run["execution_root"] == str(trial)
    assert db.get_project(project["id"])["name"] == "Previously linked"


@pytest.mark.parametrize("tool", ["workspace_create", "workflow_prepare_dispatch"])
def test_workspace_rpc_rejects_non_boolean_copy_env_before_creating_files(env, tool):
    root, db, rpc = env
    (root / ".env").write_text("DEMO=example\n", encoding="utf-8")
    run_id = _run(rpc, root, "copy-env-type")["id"]
    kwargs = {"worker_name": "author"}
    if tool == "workflow_prepare_dispatch":
        rpc(
            "task_packet_save", run_id=run_id, title="Fixture", goal="Verify argument rejection",
            file_ownership={"author": ["app.py"]}, acceptance_criteria=["reject invalid flag"],
        )
        kwargs = dict(create_workspace=True, owner="author", task_name="author", verifier="verifier",
                      step_key="dispatch", instruction="Verify argument rejection")

    with pytest.raises(ValueError, match="copy_env must be a boolean"):
        rpc(tool, run_id=run_id, copy_env="false", **kwargs)

    assert db.list_workspaces(run_id=run_id) == []
    assert not (root / WORKSPACES_DIRNAME).exists()


def test_prepare_dispatch_rolls_back_registered_git_worktree(env):
    root, db, rpc = env
    _git(root, "init")
    (root / "seed.txt").write_text("seed\n", encoding="utf-8")
    _git(root, "add", "seed.txt")
    _git(root, "-c", "user.email=test@example.test", "-c", "user.name=Test", "commit", "-m", "seed")
    run_id = _run(rpc, root, "worktree-rollback")["id"]

    with pytest.raises(ValueError, match="workspace_base_mode"):
        rpc(
            "workflow_prepare_dispatch", run_id=run_id, create_workspace=True,
            workspace_base_mode="snapshot", git_backend="worktree", copy_env=False,
            owner="rollback", task_name="rollback", verifier="verifier",
            step_key="rollback", instruction="Verify rollback",
        )

    assert db.list_workspaces(run_id=run_id) == []
    assert not (root / WORKSPACES_DIRNAME).exists()
    assert _git(root, "branch", "--list", "swarm/*").stdout == b""
    assert len(_git(root, "worktree", "list").stdout.splitlines()) == 1


@pytest.mark.parametrize("git", [False, True], ids=["snapshot", "git-clone"])
def test_t3_workspace_creation_and_explorer_use_run_execution_root(env, monkeypatch, git):
    root, db, rpc = env
    execution = root / "artifacts" / "slice"
    execution.mkdir(parents=True)
    (execution / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    if git:
        _git(root, "init")
        _git(root, "add", "artifacts")
        _git(root, "-c", "user.email=test@example.test", "-c", "user.name=Test", "commit", "-m", "seed")
    (root / ".env").write_text("SOURCE=parent\n", encoding="utf-8")
    (execution / ".env").write_text("SOURCE=slice\n", encoding="utf-8")
    _codex_project(monkeypatch, root)
    run = _run(rpc, execution, "workspace")
    run_id = run["id"]
    assert run["execution_root"] == str(execution)
    assert run["project_root"] == str(root)
    assert run["source_root"] == str(root if git else execution)
    # A post-create admission failure must roll back its empty directory as well as DB rows.
    wrong_mode = "snapshot" if git else "git_clone"
    with pytest.raises(ValueError, match="workspace_base_mode"):
        rpc(
            "workflow_prepare_dispatch", run_id=run_id, create_workspace=True,
            workspace_base_mode=wrong_mode, dep_mode="none", copy_env=False,
            owner="rollback", task_name="rollback", verifier="verifier",
            step_key="rollback", instruction="Test rollback",
        )
    assert not (execution / WORKSPACES_DIRNAME).exists()
    assert db.list_workspaces(run_id=run_id) == []

    direct = rpc("workspace_create", run_id=run_id, worker_name="direct", dep_mode="none", copy_env=False)
    assert not list(Path(direct["path"]).rglob(".env"))
    relative = (execution.relative_to(root) if git else Path()) / "app.py"
    rpc(
        "task_packet_save", run_id=run_id, title="Fixture", goal="Verify dispatch location",
        file_ownership={"dispatched": [relative.as_posix()]}, acceptance_criteria=["location"],
    )
    prep = rpc(
        "workflow_prepare_dispatch", run_id=run_id, create_workspace=True, dep_mode="none",
        copy_env=True, owner="dispatched", task_name="dispatched", verifier="verifier",
        step_key="dispatch", instruction="Test location",
    )
    workspace = prep["workspace"]
    assert Path(direct["path"]).parent == Path(workspace["path"]).parent == execution / WORKSPACES_DIRNAME
    assert workspace["mode"] == ("git_clone" if git else "snapshot")
    assert workspace["source_root"] == run["source_root"]
    assert (Path(workspace["path"]) / relative).read_text(encoding="utf-8") == "VALUE = 1\n"
    assert (Path(workspace["path"]) / ".env").read_text(encoding="utf-8") == "SOURCE=slice\n"

    outside = root.parent / "elsewhere" / WORKSPACES_DIRNAME / "unrelated"
    outside.mkdir(parents=True)
    unrelated = db.create_workspace(run_id, "unrelated", path=outside, mode="snapshot")
    legacy_path = root / WORKSPACES_DIRNAME / "previous"
    legacy_path.mkdir(parents=True, exist_ok=True)
    legacy = db.create_workspace(run_id, "previous", path=legacy_path, mode="snapshot")
    # The legacy RPC also accepts an explicit workspace source override; its manifest
    # records the authorized base rather than trusting any path named .swarm-workspaces.
    explicit_source = root.parent / "separate-source"
    explicit_source.mkdir()
    (explicit_source / "only.txt").write_text("override", encoding="utf-8")
    explicit = rpc(
        "workspace_create", run_id=run_id, worker_name="explicit", project_root=str(explicit_source),
        dep_mode="none", copy_env=False,
    )
    assert explicit["snapshot_manifest"]["project_root"] == str(explicit_source)
    assert (Path(explicit["path"]) / "only.txt").read_text(encoding="utf-8") == "override"
    opens = []
    monkeypatch.setattr("orchestrator.daemon._launch_explorer", opens.append)
    app = create_app(db.settings, db=db, start_watchdog=False, manage_pid_file=False)
    with TestClient(app) as client:
        assert client.post(f"/api/workspaces/{workspace['id']}/open-explorer", json={}).status_code == 200
        assert client.post(f"/api/workspaces/{legacy['id']}/open-explorer", json={}).status_code == 200
        assert client.post(f"/api/workspaces/{explicit['id']}/open-explorer", json={}).status_code == 200
        assert client.post(f"/api/workspaces/{unrelated['id']}/open-explorer", json={}).status_code == 403
    assert opens == [Path(workspace["path"]), legacy_path, Path(explicit["path"])]



@pytest.mark.parametrize("create_workspace", [True, False], ids=["automatic", "explicit"])
def test_t3_nested_snapshot_admits_only_execution_root(env, monkeypatch, create_workspace):
    root, db, rpc = env
    monkeypatch.setattr(db.settings, "max_snapshot_mb", 1)
    _codex_project(monkeypatch, root)
    execution = root / "artifacts" / "slice"
    execution.mkdir(parents=True)
    (execution / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    (execution / ".triignore").write_text("slice-cache.bin\n", encoding="utf-8")
    (execution / "slice-cache.bin").write_bytes(b"x" * (2 << 20))
    (root / "parent-material.bin").write_bytes(b"x" * (2 << 20))
    run = rpc("run_create", mode="boost", title="nested", project_root=str(execution))
    assert db.get_project(run["project_id"])["root_path"] == str(root)
    assert run["execution_root"] == str(execution)
    existing = {} if create_workspace else {
        "workspace_id": rpc(
            "workspace_create", run_id=run["id"], worker_name="author", project_root=str(execution),
            copy_env=False,
        )["id"],
    }
    prep = rpc(
        "workflow_prepare_dispatch", run_id=run["id"], create_workspace=create_workspace,
        owner="author", task_name="author", verifier="verifier", step_key="edit-slice",
        instruction="Change the slice", file_ownership={"author": ["app.py"]},
        acceptance_criteria=["Change app.py"], copy_env=False, **existing,
    )

    workspace = db.get_workspace(prep["workspace"]["id"])
    assert workspace["snapshot_manifest"]["project_root"] == str(execution)
    assert set(workspace["snapshot_manifest"]["files"]) == {".triignore", "app.py"}
    assert {p.name for p in Path(workspace["path"]).iterdir()} == {".triignore", "app.py"}
    admission = db.connect().execute(
        "SELECT checkpoint_json FROM workflow_steps WHERE id = ?", (prep["step"]["id"],),
    ).fetchone()
    recorded = json.loads(admission["checkpoint_json"])["admission"]
    assert recorded["base_manifest"] == workspace["snapshot_manifest"]["files"]
    assert recorded["allowed_scopes"] == ["app.py"]
    assert run["source_root"] == prep["workspace"]["source_root"] == recorded["source_root"] == str(execution)


def test_t3_nested_snapshot_rejects_reusing_parent_source(env, monkeypatch):
    root, db, rpc = env
    _codex_project(monkeypatch, root)
    execution = root / "slice"
    execution.mkdir()
    run = rpc("run_create", mode="boost", title="wrong source", project_root=str(execution))
    workspace = rpc("workspace_create", run_id=run["id"], worker_name="author", project_root=str(root))
    with pytest.raises(ValueError, match="Workspace source root.*expected"):
        rpc(
            "workflow_prepare_dispatch", run_id=run["id"], workspace_id=workspace["id"],
            owner="author", task_name="author", verifier="verifier", step_key="edit-slice",
            instruction="Change the slice", file_ownership={"author": ["app.py"]},
            acceptance_criteria=["Change app.py"],
        )
    assert db.get_workspace(workspace["id"])["status"] == "active"
    assert db.get_worker_by_name(run["id"], "author") is None


def test_t3_nested_snapshot_uses_slice_paths_through_workflow5_closeout(env, monkeypatch, tmp_path):
    root, db, rpc = env
    _codex_project(monkeypatch, root)
    execution = root / "artifacts" / "slice"
    execution.mkdir(parents=True)
    edited = execution / "app.py"
    root_file = root / "app.py"
    sibling = execution.parent / "sibling.py"
    execution_env = execution / ".env"
    unchanged = {root_file: "root", sibling: "sibling", execution_env: "SCOPE=slice"}
    for path, text in {edited: "old", **unchanged}.items():
        path.write_text(text, encoding="utf-8")

    run = _run(rpc, execution, "merge-nested")
    run_id = run["id"]
    relative = edited.relative_to(execution).as_posix()
    criterion = "nested file updated without changing its project siblings"
    rpc(
        "task_packet_save", run_id=run_id, title="Nested snapshot", goal=criterion,
        file_ownership={"author": [relative]}, acceptance_criteria=[criterion],
    )

    def record_fixture_native(action):
        # This fixture injects the host reply only. Verification and merge are not mocked.
        raw = (
            {"task_name": action["target_agent_path"]}
            if action["action_type"] == "spawn_agent"
            else {"status": "queued"}
        )
        rpc(
            "native_action_record_call", run_id=run_id, action_id=action["action_id"],
            claim_token=action["claim_token"], expected_revision=action["expected_revision"],
            outcome="confirmed", raw_response=raw,
        )

    author = rpc(
        "workflow_prepare_dispatch", run_id=run_id, create_workspace=True, copy_env=False,
        owner="author", task_name="author", verifier="verifier", verifier_task_name="verifier",
        step_key="edit-nested", instruction=criterion,
    )
    record_fixture_native(author)
    step_id = author["step"]["id"]
    assert author["workspace"]["mode"] == "snapshot"
    workspace = Path(author["workspace"]["path"])
    assert workspace.parent == execution / WORKSPACES_DIRNAME
    rpc("workflow_author_turn", run_id=run_id, step_id=step_id, message_id=author["message_id"], phase="accept")

    candidate = workspace / relative
    candidate.write_text("new", encoding="utf-8")
    rpc(
        "workflow_author_turn", run_id=run_id, step_id=step_id, message_id=author["message_id"],
        phase="submit", summary="Changed nested app.py",
    )

    def verify(scope, cwd, target):
        delivery = rpc(
            "workflow_prepare_dispatch", run_id=run_id, step_id=step_id, dispatch_action="request_verify",
            task_name="verifier", instruction=f"Verify {scope}",
        )
        record_fixture_native(delivery)
        turn = rpc(
            "workflow_verifier_turn", run_id=run_id, step_id=step_id, message_id=delivery["message_id"],
            phase="prepare", scope=scope, env_profile="windows_minimal_py" if os.name == "nt" else "posix_minimal_py",
            verification_commands=[{
                "id": scope, "cwd": str(cwd), "timeout_sec": 15, "criteria": [criterion],
                "argv": [sys.executable, "-c",
                         "from pathlib import Path; import sys; assert Path(sys.argv[1]).read_text() == 'new'",
                         str(target)],
            }],
        )
        receipts = tmp_path / scope
        receipt = collect(Path(turn["artifact"]["manifest_path"]), receipts)
        assert receipt["status"] == "finished", receipt
        assert receipt["commands"][0]["exit_code"] == 0
        return rpc(
            "workflow_verifier_turn", run_id=run_id, step_id=step_id, phase="record",
            artifact_id=turn["artifact"]["id"], verdict="PASS", receipt_files=[str(receipts / "receipt.json")],
        )

    assert verify("candidate", workspace, candidate)["step"]["state"] == "ready_to_merge"
    merged = rpc("workspace_merge", workspace_id=author["workspace"]["id"])
    assert merged["files_changed"] == [relative]
    assert candidate == workspace / relative
    assert edited.read_text(encoding="utf-8") == "new"
    assert {path: path.read_text(encoding="utf-8") for path in unchanged} == unchanged
    verify("integrated", execution, edited)
    assert rpc("workflow_closeout_run", run_id=run_id)["run"]["status"] == "completed"
    assert db.get_workspace(author["workspace"]["id"])["status"] == "cleaned" and not workspace.exists()


def test_t4_concurrent_sessions_have_independent_runs_and_agents(env):
    root, db, rpc = env
    start = Barrier(2)

    def create(session):
        turn_id = f"turn-{session}"
        _turn(rpc, session, turn_id, root, invoked=True)
        start.wait(timeout=5)
        run = _run(rpc, root, session)
        rpc("worker_register", run_id=run["id"], name="coder", role="swarm_worker", agent_id=f"/root/{session}")
        return run

    with ThreadPoolExecutor(max_workers=2) as pool:
        run_a, run_b = list(pool.map(create, ["session_a", "session_b"]))
    assert run_a["id"] != run_b["id"]
    assert run_a["project_id"] == run_b["project_id"]
    for session, run in [("session_a", run_a), ("session_b", run_b)]:
        assert run["turn_id"] == f"turn-{session}"
        assert run["session_seq"] == 1
        assert [binding["run_id"] for binding in db.list_session_bindings(session)] == [run["id"]]
        assert db.get_worker_by_name(run["id"], "coder")["agent_id"] == f"/root/{session}"
        _turn(rpc, session, "continuation", root)
        assert db.list_session_turns(session)[0]["active_run_id"] == run["id"]


def test_t5_stop_walks_only_matching_active_runs_and_dedupes_per_binding(env, monkeypatch):
    root, db, rpc = env
    a1, a2, b1, b2 = [_run(rpc, root, session)["id"] for session in ("a", "a", "b", "b")]
    # Seed a completed legacy Run without constructing an unrelated workflow's verification gates.
    db.connect().execute("UPDATE runs SET workflow_version=0 WHERE id=?", (a1,))
    db.update_run(a1, status="completed")
    db.upsert_session_binding("a", a1)  # A later touch must not resurrect a completed Run.
    db.upsert_session_binding("b", b1, is_primary=False)
    for run_id in (a2, b1, b2):
        rpc("worker_register", run_id=run_id, name="coder", role="swarm_worker", agent_id=f"/root/{run_id}")
        rpc(
            "board_post", run_id=run_id, sender="human", sender_role="human", channel="human",
            msg_type="human", mentions=["coder"], summary="pending", body_md="@coder continue",
        )
    seen = []
    original = BoardService.board_status

    def record(self, run_id, *args, **kwargs):
        seen.append(run_id)
        return original(self, run_id, *args, **kwargs)

    monkeypatch.setattr(BoardService, "board_status", record)

    def stop(session, cwd=root):
        return rpc("session_hook_check", session_id=session, event_name="Stop", cwd=str(cwd), mark_stop_blocked=True)

    assert not stop("a", root.parent)["should_block_stop"]
    assert not stop("a", root / WORKSPACES_DIRNAME / "isolated")["should_block_stop"]
    seen.clear()
    a_stop = stop("a")
    assert a_stop["should_block_stop"] and a_stop["run_id"] == a2
    assert seen == [a2]
    assert not any(db.get_session_binding("b", run_id)["last_stop_blocked_key"] for run_id in (b1, b2))
    assert db.get_session_binding("a", a1)["last_stop_blocked_key"] is None
    assert not stop("a")["should_block_stop"]  # B's pending Runs are not candidates for A.

    answers = [stop("b"), stop("b")]
    assert [answer["run_id"] for answer in answers] == [b2, b1]
    assert all(answer["should_block_stop"] for answer in answers)
    assert answers[0]["blocked_key"] != answers[1]["blocked_key"]
    for answer in answers:
        assert db.get_session_binding("b", answer["run_id"])["last_stop_blocked_key"] == answer["blocked_key"]
    assert db.get_session_binding("a", a2)["last_stop_blocked_key"] == a_stop["blocked_key"]
    assert not stop("b")["should_block_stop"]


def test_t5_stop_skips_other_roots_in_one_session_and_marks_by_default(env):
    root, db, rpc = env
    other = root.parent / "other-project"
    other.mkdir()
    run_root, run_other = [_run(rpc, cwd, "same-session")["id"] for cwd in (root, other)]
    for run_id in (run_root, run_other):
        rpc("worker_register", run_id=run_id, name="coder", role="swarm_worker", agent_id=f"/root/{run_id}")
        rpc(
            "board_post", run_id=run_id, sender="human", sender_role="human", channel="human",
            msg_type="human", mentions=["coder"], summary="pending", body_md="@coder continue",
        )

    def stop():
        return rpc("session_hook_check", session_id="same-session", event_name="Stop", cwd=str(root))

    result = stop()
    assert result["should_block_stop"] and result["run_id"] == run_root
    assert db.get_session_binding("same-session", run_root)["last_stop_blocked_key"] == result["blocked_key"]
    assert db.get_session_binding("same-session", run_other)["last_stop_blocked_key"] is None
    assert not stop()["should_block_stop"]


def test_t3_custom_slice_name_cannot_rename_registered_parent(env, monkeypatch):
    root, db, rpc = env
    child = root / "artifacts" / "slice"
    child.mkdir(parents=True)
    _codex_project(monkeypatch, root)
    project = db.ensure_project(root, name="Codex Main")

    run = _run(rpc, child, "custom-slice", project_name="Unrelated scratch label")

    assert run["project_id"] == project["id"]
    assert run["execution_root"] == str(child)
    assert db.get_project(project["id"])["name"] == "Codex Main"


def test_list_runs_orders_equal_timestamps_by_newest_insertion(env, monkeypatch):
    root, db, _rpc = env
    project = db.ensure_project(root)
    monkeypatch.setattr(db_module.time, "time", lambda: 123.0)
    runs = [db.create_run(project_id=project["id"], mode="swarm", title=str(i)) for i in range(3)]

    assert db.list_runs(project_id=project["id"], limit=2) == [runs[2], runs[1]]


def test_execution_root_rejects_outside_paths_atomically(env):
    root, db, rpc = env
    outside = root.with_name(root.name + "-sibling")
    outside.mkdir()
    with pytest.raises(ValueError, match="execution_root"):
        _run(rpc, root, "outside", execution_root=str(outside))
    assert db.list_projects() == []
    project = db.ensure_project(root)
    with pytest.raises(ValueError, match="execution_root"):
        rpc("run_create", project_id=project["id"], mode="swarm", title="outside", execution_root=str(outside))
    assert db.list_runs() == []


def test_execution_root_rejects_historical_outside_workspace_placement(env):
    root, db, rpc = env
    outside = root.with_name(root.name + "-historical")
    outside.mkdir()
    run = _run(rpc, root, "historical")
    with db.transaction() as conn:
        conn.execute("UPDATE runs SET execution_root=? WHERE id=?", (str(outside), run["id"]))
    with pytest.raises(ValueError, match="execution_root"):
        rpc("workspace_create", run_id=run["id"], worker_name="worker")
    assert not (outside / ".swarm-workspaces").exists()


def test_execution_root_rejects_symlink_escape(env):
    root, db, rpc = env
    outside = root.with_name(root.name + "-outside")
    outside.mkdir()
    link = root / "link"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("Directory symlink unavailable")
    with pytest.raises(ValueError, match="execution_root"):
        _run(rpc, root, "symlink", execution_root=str(link))
    assert db.list_runs() == []
