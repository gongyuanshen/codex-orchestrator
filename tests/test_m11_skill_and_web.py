"""M11 & M12 $pipeline skill contract, Web Console App-Server badge, CJK @mentions, role labels, and live worker_control tests."""

from __future__ import annotations

import os
import re
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path

import httpx
import pytest
import uvicorn

from orchestrator.config import Settings
from orchestrator.daemon import create_app
from test_dashboard_behavior import run_dashboard

ROOT = Path(__file__).resolve().parents[1]
PLUGIN_DIR = ROOT / "plugin"
PIPELINE_SKILL = PLUGIN_DIR / "skills" / "pipeline" / "SKILL.md"
WEB_DIR = PLUGIN_DIR / "src" / "orchestrator" / "web"
APP_JS = WEB_DIR / "app.js"
STYLES_CSS = WEB_DIR / "styles.css"


def _strip_yaml_scalar(raw_val: str) -> str:
    val = raw_val.strip()
    if not val:
        return ""
    if val[0] == '"' and val.endswith('"') and len(val) >= 2:
        return val[1:-1].replace('\\"', '"')
    if val[0] == "'" and val.endswith("'") and len(val) >= 2:
        return val[1:-1].replace("''", "'")
    return re.sub(r"(?:^|\s)#.*$", "", val).rstrip()


def _parse_frontmatter(markdown_text: str) -> tuple[dict[str, object], str]:
    assert markdown_text.startswith("---\n"), "SKILL.md must begin with YAML frontmatter"
    _, rest = markdown_text.split("---\n", 1)
    fm_raw, body = rest.split("\n---\n", 1)
    result: dict[str, object] = {}
    current_map: dict[str, str] | None = None
    for line in fm_raw.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line.startswith("  ") and current_map is not None:
            k, v = line.strip().split(":", 1)
            current_map[k.strip()] = _strip_yaml_scalar(v)
        else:
            k, v = line.split(":", 1)
            key = k.strip()
            if v.strip() == "":
                nested: dict[str, str] = {}
                result[key] = nested
                current_map = nested
            else:
                result[key] = _strip_yaml_scalar(v)
                current_map = None
    return result, body


def test_pipeline_skill_frontmatter_sections_and_guardrails() -> None:
    assert PIPELINE_SKILL.is_file()
    raw = PIPELINE_SKILL.read_text(encoding="utf-8")
    fm, body = _parse_frontmatter(raw)

    assert fm["name"] == "pipeline"
    desc = str(fm["description"])
    assert "$pipeline" in desc
    for interactive in ("$boost", "$teamwork", "$swarm"):
        assert interactive in desc
    metadata = fm["metadata"]
    assert isinstance(metadata, dict)
    assert metadata.get("short-description")

    # Routing boundary: batch/unattended/zero-relay-tax/window-close resilience vs interactive V2 subagents.
    for phrase in (
        "多模块批量重构",
        "长时无人值守",
        "零主助手中继税",
        "关闭当前聊天窗口",
        "$boost",
        "$teamwork",
        "$swarm",
        "/root/<task_name>",
    ):
        assert phrase in body, phrase

    # Deterministic 3-step host workflow in strict order.
    step1_idx = body.index("### 步骤 1：")
    step2_idx = body.index("### 步骤 2：")
    step3_idx = body.index("### 步骤 3：")
    assert step1_idx < step2_idx < step3_idx

    step1 = body[step1_idx:step2_idx]
    for spec_field in (
        "最多进行 1 轮简明澄清",
        "pipeline_spec.json",
        '"project_root"',
        '"mode"',
        '"title"',
        '"max_workers"',
        "1..4",
        '"file_ownership"',
        '"acceptance_criteria"',
        '"commands"',
        '"env_profile"',
        '"dependencies"',
        '"complexity"',
    ):
        assert spec_field in step1, spec_field

    step2 = body[step2_idx:step3_idx]
    assert "plugins/cache/tri-orchestrator/tri-orchestrator/0.6.0/.venv" not in step2
    for exec_term in (
        "tri-orchestrator-pipeline --spec",
        "--output",
        "cli_executables.pipeline",
        "tri-orchestrator-challenge",
        "PATH",
        "plugins/cache/tri-orchestrator/tri-orchestrator/*/.venv",
        "Scripts/tri-orchestrator-pipeline.exe",
        "bin/tri-orchestrator-pipeline",
        "pipeline_summary.json",
        "http://127.0.0.1:18790",
        "App-Server 管线",
        "turn/interrupt",
        "<=2s",
    ):
        assert exec_term in step2, exec_term

    step3 = body[step3_idx:]
    for guardrail in (
        "严禁主助手双写",
        "appserver://<thread_id>",
        'execution_kind="app_server_pipeline"',
        "/root/...",
        "3 轮",
        "needs_decision",
        "needs_clarification",
        "needs_reconciliation",
        "tri-orchestrator-pipeline --resume-run",
        "thread/read",
        "thread/resume",
    ):
        assert guardrail in step3, guardrail


def test_pipeline_skill_windows_lookup_runs_as_documented(tmp_path: Path) -> None:
    shell = shutil.which("powershell.exe") or shutil.which("pwsh.exe")
    if os.name != "nt" or not shell:
        pytest.skip("Windows PowerShell is required")

    command = next(
        line.split("`")[1]
        for line in PIPELINE_SKILL.read_text(encoding="utf-8").splitlines()
        if "**Windows (PowerShell)**" in line
    )
    codex_home = tmp_path / "Codex Home"
    executable = (
        codex_home / "plugins/cache/tri-orchestrator/tri-orchestrator"
        / "99.0.0/.venv/Scripts/tri-orchestrator-pipeline.exe"
    )
    executable.parent.mkdir(parents=True)
    executable.touch()
    proc = subprocess.run(
        [shell, "-NoProfile", "-NonInteractive", "-Command", "$ErrorActionPreference='Stop'; " + command],
        env={**os.environ, "CODEX_HOME": str(codex_home)},
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert proc.returncode == 0, proc.stderr
    assert Path(proc.stdout.strip()) == executable


def test_web_app_js_passes_node_syntax_check() -> None:
    proc = subprocess.run(
        ["node", "--check", str(APP_JS)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stderr.strip() == ""


def test_appserver_badge_css_contrast_meets_wcag_aa_in_both_themes() -> None:
    css = STYLES_CSS.read_text(encoding="utf-8")
    assert ".badge-appserver" in css
    assert "border: 1px solid var(--pipeline-border);" in css

    def _rgb(hex_color: str) -> tuple[int, int, int]:
        cleaned = hex_color.strip()
        return (int(cleaned[1:3], 16), int(cleaned[3:5], 16), int(cleaned[5:7], 16))

    def _luminance(color: tuple[int, int, int]) -> float:
        channels = [c / 255.0 for c in color]
        linear = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
        return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]

    for theme in ("light", "dark"):
        match = re.search(r'\[data-theme="' + theme + r'"\]\s*\{([^}]+)\}', css)
        assert match is not None, theme
        tokens = dict(re.findall(r"(--[\w-]+):\s*([^;]+);", match.group(1)))
        fg = _rgb(tokens["--pipeline-fg"])
        rgba_match = re.fullmatch(r"rgba\(([^)]+)\)", tokens["--pipeline-bg"].strip())
        assert rgba_match is not None
        r, g, b, alpha = (float(x.strip()) for x in rgba_match.group(1).split(","))
        for surface_token in ("--bg-surface", "--bg-card"):
            base = _rgb(tokens[surface_token])
            blended = (
                round(r * alpha + base[0] * (1.0 - alpha)),
                round(g * alpha + base[1] * (1.0 - alpha)),
                round(b * alpha + base[2] * (1.0 - alpha)),
            )
            hi, lo = sorted((_luminance(fg), _luminance(blended)), reverse=True)
            contrast = (hi + 0.05) / (lo + 0.05)
            assert contrast >= 4.5, f"{theme} .badge-appserver on {surface_token}: {contrast:.2f}:1 < 4.5:1"


def test_dashboard_chinese_mentions_system_roles_and_null_safe_blocker_step_key() -> None:
    run_dashboard(
        r"""(() => {
      // 1. Chinese before/after @handle highlights strictly the ASCII handle without swallowing CJK chars.
      const cjkMentionHtml = renderMarkdown('移交@verifier请复核，同时请@coder-1与@swarm_worker协助（邮箱 user@example.com 不高亮）。');
      assert.ok(
        cjkMentionHtml.includes('移交<span class="inline-mention">@verifier</span>请复核'),
        'Expected @verifier bounded cleanly between Chinese characters: ' + cjkMentionHtml
      );
      assert.ok(cjkMentionHtml.includes('请<span class="inline-mention">@coder-1</span>与<span class="inline-mention">@swarm_worker</span>协助'));
      assert.ok(!cjkMentionHtml.includes('@example.com</span>'), 'Email addresses must not highlight as @mentions');

      // 2. ROLE_LABELS covers system and workflow so watchdog/workflow posts never render 职责未知（system）.
      assert.equal(labelRole('system'), '系统看门狗');
      assert.equal(labelRole('workflow'), '工作流引擎');

      applySnapshot({
        run: {id: 'run-polish', project_id: 'p1', mode: 'swarm', status: 'active', workflow_version: 5, title: 'Web Polish Run'},
        workflow: {
          steps: [{id: 'step-known', step_key: 'known-step', state: 'rework', attempt: 1, checkpoint: {}}],
          artifacts: [],
          verifications: [],
          blockers: [
            {step_id: 'orphan-step-id', state: 'needs_decision'},
            {step_id: 'step-known', state: 'rework'}
          ]
        },
        inbox: {counts: {pending: 0, accepted: 0, completed: 0, failed: 0, cancelled: 0}, blocking_count: 0},
        workers: [],
        workspaces: [],
        messages: [
          {seq: 1, sender: 'watchdog', sender_role: 'system', channel: 'main', msg_type: 'alert', summary: '心跳超时告警', body_md: '移交@coordinator检查'},
          {seq: 2, sender: 'workflow', sender_role: 'workflow', channel: '#verify', msg_type: 'info', summary: '门禁流转记录', body_md: '通知@verifier请复核'}
        ]
      });
      state.loadState = 'ready';
      state.expandedMessages.add('1');
      state.expandedMessages.add('2');
      renderAll();

      // 3. Null-safe step_key lookup in renderWorkflowSummary prevents crashes on orphan blocker step_ids.
      assert.ok(el.overviewContent.innerHTML.includes('orphan-step-id：待用户决策'));
      assert.ok(el.overviewContent.innerHTML.includes('known-step：待原作者返工'));

      assert.ok(el.boardFeed.innerHTML.includes('系统看门狗'));
      assert.ok(el.boardFeed.innerHTML.includes('工作流引擎'));
      assert.ok(!el.boardFeed.innerHTML.includes('职责未知（system）'));
      assert.ok(!el.boardFeed.innerHTML.includes('职责未知（workflow）'));
      assert.ok(el.boardFeed.innerHTML.includes('移交<span class="inline-mention">@coordinator</span>检查'));
      assert.ok(el.boardFeed.innerHTML.includes('通知<span class="inline-mention">@verifier</span>请复核'));
    })()"""
    )


def test_dashboard_honest_attribution_badge_across_steps_members_and_inspector() -> None:
    run_dashboard(
        r"""(() => {
      const steps = [
        {id: 's-pipe-top', step_key: 'pipe-planned', state: 'planned', attempt: 0, execution_kind: 'app_server_pipeline', owner_agent_id: '', verifier_agent_id: '', checkpoint: {}},
        {id: 's-pipe-ckpt', step_key: 'pipe-ckpt', state: 'implementing', attempt: 1, owner_agent_id: '', verifier_agent_id: '', checkpoint: {execution_kind: 'app_server_pipeline'}},
        {id: 's-pipe-agent', step_key: 'pipe-bound', state: 'candidate_review', attempt: 0, owner_agent_id: 'appserver://thr_author_1', verifier_agent_id: 'appserver://thr_ver_1', checkpoint: {}},
        {id: 's-native', step_key: 'native-v2-step', state: 'implementing', attempt: 0, execution_kind: 'native', owner_agent_id: '/root/coder_1', verifier_agent_id: '/root/verifier_1', checkpoint: {execution_kind: 'native'}},
        {id: 's-local', step_key: 'boost-local-step', state: 'implementing', attempt: 0, execution_kind: 'coordinator_local', owner_agent_id: '/root/coord', verifier_agent_id: '/root/verifier_1', checkpoint: {execution_kind: 'coordinator_local'}}
      ];
      const workers = [
        {id: 'w-pipe', name: 'author-pipe', role: 'coder', status: 'working', agent_id: 'appserver://thr_author_1', workspace_id: 'ws-pipe', current_task: 'Refactor auth'},
        {id: 'w-native', name: 'coder-native', role: 'coder', status: 'working', agent_id: '/root/coder_1', workspace_id: 'ws-native', current_task: 'Interactive task'},
        {id: 'w-unbound', name: 'planned-ver', role: 'verifier', status: 'waiting', agent_id: null, current_task: 'Await candidate'}
      ];
      const workspaces = [
        {id: 'ws-pipe', worker_name: 'author-pipe', mode: 'git_clone', status: 'active', path: 'G:/tmp/ws-pipe', branch_name: 'swarm/pipe'},
        {id: 'ws-native', worker_name: 'coder-native', mode: 'git_clone', status: 'active', path: 'G:/tmp/ws-native', branch_name: 'swarm/native'}
      ];

      // Direct unit checks on step rendering so each execution_kind is verified in isolation.
      assert.ok(renderWorkflowStep(steps[0]).includes('badge-appserver'), 'execution_kind=app_server_pipeline must show badge');
      assert.ok(renderWorkflowStep(steps[0]).includes('App-Server 管线'));
      assert.ok(renderWorkflowStep(steps[1]).includes('badge-appserver'), 'checkpoint.execution_kind=app_server_pipeline must show badge');
      assert.ok(renderWorkflowStep(steps[2]).includes('badge-appserver'), 'appserver:// agent_id must show badge');
      assert.ok(!renderWorkflowStep(steps[3]).includes('badge-appserver'), 'native /root/... step must NEVER show App-Server badge');
      assert.ok(!renderWorkflowStep(steps[4]).includes('badge-appserver'), 'coordinator_local step must NEVER show App-Server badge');

      // Hydrate workflow5 snapshot and verify Overview, Members, Graph detail, and Inspector.
      applySnapshot({
        run: {id: 'run-m11', project_id: 'p1', mode: 'swarm', status: 'active', workflow_version: 5, title: 'M11 Pipeline Run'},
        workflow: {steps, artifacts: [], verifications: [], blockers: []},
        inbox: {counts: {pending: 0, accepted: 1, completed: 0, failed: 0, cancelled: 0}, blocking_count: 1},
        progress_status: {classification: 'normal_wait', incomplete: true, executable_candidates: [], blockers: [], coordinator_required: false},
        resume_context: {facts_digest: 'd1', frozen_packet_id: 'tp1', artifact_refs: [], verification_refs: [], unresolved: {inbox: [], controls: [], native_actions: []}, handoffs: [], forbidden_actions: []},
        plan_readiness: {ready: true, uncovered_criteria: [], ownership_conflicts: []},
        workers,
        workspaces,
        messages: [{seq: 1, sender: 'author-pipe', sender_role: 'coder', channel: 'main', summary: 'turn started', mentions: ['planned-ver']}]
      });
      state.loadState = 'ready';
      renderAll();

      assert.ok(el.overviewContent.innerHTML.includes('结构化门禁 · v5'));
      assert.ok(el.overviewContent.innerHTML.includes('App-Server 管线'));

      const pipeMemberHtml = renderMemberRow(workers[0]);
      const nativeMemberHtml = renderMemberRow(workers[1]);
      const unboundMemberHtml = renderMemberRow(workers[2]);
      assert.ok(pipeMemberHtml.includes('badge-appserver') && pipeMemberHtml.includes('App-Server 管线'));
      assert.ok(!nativeMemberHtml.includes('badge-appserver'));
      assert.ok(!unboundMemberHtml.includes('badge-appserver'));

      state.graphSelectedNode = 'author-pipe';
      renderGraph();
      assert.ok(el.graphDetailBar.innerHTML.includes('App-Server 管线'));
      state.graphSelectedNode = 'coder-native';
      renderGraph();
      assert.ok(!el.graphDetailBar.innerHTML.includes('App-Server 管线'));

      state.selectedWorkerId = 'w-pipe';
      renderInspector();
      assert.ok(el.inspectorBody.innerHTML.includes('执行线程绑定'));
      assert.ok(el.inspectorBody.innerHTML.includes('appserver://thr_author_1'));
      assert.ok(el.inspectorBody.innerHTML.includes('App-Server 管线'));
      assert.ok(!el.inspectorBody.innerHTML.includes('原生代理绑定'));

      state.selectedWorkerId = 'w-native';
      renderInspector();
      assert.ok(el.inspectorBody.innerHTML.includes('原生代理绑定'));
      assert.ok(!el.inspectorBody.innerHTML.includes('App-Server 管线'));
    })()"""
    )


def test_dashboard_pause_and_stop_controls_enabled_for_appserver_workers() -> None:
    run_dashboard(
        r"""(async () => {
      const pipeWithWs = {id: 'w-pipe-ws', name: 'author-1', role: 'coder', status: 'working', agent_id: 'appserver://thr_active_01', workspace_id: 'ws-1'};
      const pipeNoWs = {id: 'w-pipe-nows', name: 'verifier-1', role: 'verifier', status: 'waiting', agent_id: 'appserver://thr_ver_01'};
      applySnapshot({
        run: {id: 'run-ctrl', project_id: 'p1', mode: 'swarm', status: 'active', workflow_version: 5},
        workers: [pipeWithWs, pipeNoWs],
        workspaces: [{id: 'ws-1', worker_name: 'author-1', mode: 'git_clone', status: 'active', path: 'G:/tmp/ws-1'}],
        control_requests: []
      });
      state.loadState = 'ready';

      // 1. Bound appserver:// worker WITH workspace: both pause (status-stopped) and stop (status-killed) buttons enabled.
      state.selectedWorkerId = 'w-pipe-ws';
      renderInspector();
      const pauseBtnTag = el.inspectorBody.innerHTML.match(/<button[^>]*data-action="status-stopped"[^>]*>/)[0];
      const stopBtnTag = el.inspectorBody.innerHTML.match(/<button[^>]*data-action="status-killed"[^>]*>/)[0];
      assert.ok(!pauseBtnTag.includes('disabled'), 'Pause button must be enabled for active appserver:// worker');
      assert.ok(!stopBtnTag.includes('disabled'), 'Stop button must be enabled for active appserver:// worker');

      const btnEl = testNode();
      btnEl.setAttribute('data-worker-id', 'w-pipe-ws');
      const actionPromise = runInspectorAction('status-stopped', btnEl);
      assert.equal(requests.length, 1);
      assert.equal(requests[0].url, '/api/workers/w-pipe-ws/status');
      requests[0].resolve({
        ok: true,
        workspace_preserved: true,
        control_request: {
          id: 'ctrl-1',
          run_id: 'run-ctrl',
          worker_id: 'w-pipe-ws',
          agent_id: 'appserver://thr_active_01',
          requested_status: 'stopped',
          status: 'pending',
          result: null
        }
      });
      await actionPromise;
      assert.ok(el.inspectorBody.innerHTML.includes('turn/interrupt'));
      const pauseAfterPending = el.inspectorBody.innerHTML.match(/<button[^>]*data-action="status-stopped"[^>]*>/)[0];
      assert.ok(pauseAfterPending.includes('disabled'), 'Duplicate control must be disabled while pending');

      // 2. Bound appserver:// worker WITHOUT workspace: both pause and stop buttons also enabled.
      state.selectedWorkerId = 'w-pipe-nows';
      renderInspector();
      const pauseNoWsTag = el.inspectorBody.innerHTML.match(/<button[^>]*data-action="status-stopped"[^>]*>/)[0];
      const stopNoWsTag = el.inspectorBody.innerHTML.match(/<button[^>]*data-action="status-killed"[^>]*>/)[0];
      assert.ok(!pauseNoWsTag.includes('disabled'));
      assert.ok(!stopNoWsTag.includes('disabled'));

      const killBtnEl = testNode();
      killBtnEl.setAttribute('data-worker-id', 'w-pipe-nows');
      const killPromise = runInspectorAction('status-killed', killBtnEl);
      assert.equal(requests.length, 2);
      assert.equal(requests[1].url, '/api/workers/w-pipe-nows/status');
      requests[1].resolve({
        ok: true,
        workspace_preserved: true,
        control_request: {
          id: 'ctrl-2',
          run_id: 'run-ctrl',
          worker_id: 'w-pipe-nows',
          agent_id: 'appserver://thr_ver_01',
          requested_status: 'killed',
          status: 'pending',
          result: null
        }
      });
      await killPromise;
      assert.equal(state.controlRequests.length, 2);
    })()"""
    )


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def test_live_http_static_assets_and_worker_control_pause_stop_against_appserver_worker(
    tmp_path: Path,
) -> None:
    project_dir = tmp_path / "demo_repo"
    project_dir.mkdir()
    (project_dir / "mod.py").write_text("X = 1\n", encoding="utf-8")
    port = _free_port()
    assert port != 18790
    settings = Settings(
        host="127.0.0.1",
        port=port,
        data_dir=tmp_path / "isolated_data",
        port_range=(29100, 29150),
    )

    app = create_app(settings, start_watchdog=False, manage_pid_file=True)
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host=settings.host,
            port=settings.port,
            log_config=None,
            access_log=False,
            log_level="warning",
        )
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline:
            try:
                with httpx.Client(trust_env=False, timeout=0.5) as probe:
                    if probe.get(f"{settings.base_url}/api/health").status_code == 200:
                        break
            except Exception:
                pass
            time.sleep(0.05)
        else:
            raise RuntimeError("Isolated Uvicorn test server failed to start within 10s")

        with httpx.Client(base_url=settings.base_url, trust_env=False, timeout=10.0) as client:
            # 1. Verify live HTTP static assets serve App-Server badge hooks & CSS rules.
            js_resp = client.get("/app.js")
            assert js_resp.status_code == 200
            assert "App-Server 管线" in js_resp.text
            assert "app_server_pipeline" in js_resp.text
            assert "appserver://" in js_resp.text
            assert "系统看门狗" in js_resp.text
            assert "工作流引擎" in js_resp.text

            css_resp = client.get("/styles.css")
            assert css_resp.status_code == 200
            assert ".badge-appserver" in css_resp.text
            assert "--pipeline-fg" in css_resp.text

            for entry in ("/", "/boost", "/teamwork", "/swarm"):
                html_resp = client.get(entry)
                assert html_resp.status_code == 200

            # 2. Create workflow5 run and register active appserver://thr_... workers.
            run_resp = client.post(
                "/api/rpc",
                json={
                    "tool": "run_create",
                    "arguments": {
                        "mode": "swarm",
                        "title": "Live HTTP AppServer Control Test",
                        "project_root": str(project_dir),
                    },
                },
            )
            assert run_resp.status_code == 200, run_resp.text
            run = run_resp.json()["result"]
            run_id = run["id"]
            assert run["workflow_version"] == 5

            w_pause = client.post(
                "/api/rpc",
                json={
                    "tool": "worker_register",
                    "arguments": {
                        "run_id": run_id,
                        "name": "author-step1",
                        "role": "coder",
                        "status": "working",
                        "agent_id": "appserver://thr_pause_target_01",
                        "current_task": "Executing step 1 via app-server",
                    },
                },
            ).json()["result"]
            assert w_pause["agent_id"] == "appserver://thr_pause_target_01"

            w_stop = client.post(
                "/api/rpc",
                json={
                    "tool": "worker_register",
                    "arguments": {
                        "run_id": run_id,
                        "name": "author-step2",
                        "role": "coder",
                        "status": "working",
                        "agent_id": "appserver://thr_stop_target_02",
                        "current_task": "Executing step 2 via app-server",
                    },
                },
            ).json()["result"]
            assert w_stop["agent_id"] == "appserver://thr_stop_target_02"

            # 3. Live HTTP Pause (`status="stopped"`) + pipeline turn/interrupt ACK.
            pause_http = client.post(
                f"/api/workers/{w_pause['id']}/status",
                json={"status": "stopped"},
            )
            assert pause_http.status_code == 202, pause_http.text
            pause_payload = pause_http.json()
            assert pause_payload["ok"] is True
            assert pause_payload["workspace_preserved"] is True
            pause_req = pause_payload["control_request"]
            assert pause_req["status"] == "pending"
            assert pause_req["requested_status"] == "stopped"
            assert pause_req["agent_id"] == "appserver://thr_pause_target_01"

            snap_before_ack = client.get(f"/api/runs/{run_id}/snapshot").json()
            workers_by_id = {w["id"]: w for w in snap_before_ack["workers"]}
            assert workers_by_id[w_pause["id"]]["status"] == "working"
            assert any(
                c["id"] == pause_req["id"] and c["status"] == "pending"
                for c in snap_before_ack["control_requests"]
            )

            ack_pause = client.post(
                "/api/rpc",
                json={
                    "tool": "worker_control_ack",
                    "arguments": {
                        "request_id": pause_req["id"],
                        "outcome": "applied",
                        "result": "turn/interrupt completed on appserver://thr_pause_target_01 within 120ms",
                    },
                },
            )
            assert ack_pause.status_code == 200, ack_pause.text
            assert ack_pause.json()["result"]["status"] == "applied"

            # 4. Live HTTP Stop (`status="killed"`) + pipeline turn/interrupt ACK.
            stop_http = client.post(
                f"/api/workers/{w_stop['id']}/status",
                json={"status": "killed"},
            )
            assert stop_http.status_code == 202, stop_http.text
            stop_payload = stop_http.json()
            assert stop_payload["ok"] is True
            assert stop_payload["workspace_preserved"] is True
            stop_req = stop_payload["control_request"]
            assert stop_req["status"] == "pending"
            assert stop_req["requested_status"] == "killed"
            assert stop_req["agent_id"] == "appserver://thr_stop_target_02"

            ack_stop = client.post(
                "/api/rpc",
                json={
                    "tool": "worker_control_ack",
                    "arguments": {
                        "request_id": stop_req["id"],
                        "outcome": "applied",
                        "result": "turn/interrupt + Win32 Job Object closed for appserver://thr_stop_target_02",
                    },
                },
            )
            assert ack_stop.status_code == 200, ack_stop.text
            assert ack_stop.json()["result"]["status"] == "applied"

            snap_after_ack = client.get(f"/api/runs/{run_id}/snapshot").json()
            final_workers = {w["id"]: w for w in snap_after_ack["workers"]}
            assert final_workers[w_pause["id"]]["status"] == "stopped"
            assert final_workers[w_stop["id"]]["status"] == "killed"
    finally:
        server.should_exit = True
        thread.join(timeout=5.0)
