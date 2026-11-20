"""Tests for Multi-Agent & Session Model Visibility in the web dashboard UI."""
import pathlib
import re
import subprocess

from test_dashboard_behavior import APP, run_dashboard

INDEX_HTML = APP.with_name('index.html')
STYLES_CSS = APP.with_name('styles.css')


def test_html_and_css_expose_model_visibility_controls_and_tokens():
    html = INDEX_HTML.read_text(encoding='utf-8')
    css = STYLES_CSS.read_text(encoding='utf-8')

    assert 'id="runModelBadge"' in html
    assert html.index('id="runMetaPill"') < html.index('id="runModelBadge"') < html.index('class="topbar-right"')
    assert 'id="graphShowHelpersToggle"' in html
    assert html.index('id="graphExpandAllToggle"') < html.index('id="graphShowHelpersToggle"')

    required_classes = [
        '.model-badge',
        '.model-badge-effort',
        '.model-badge-mismatch',
        '.model-badge-empty',
        '.model-badge-compact',
        '.session-model-summary',
        '.member-subagents-tree',
        '.subagent-tree-item',
        '.subagent-depth-tag',
        '.unregistered-subagents-wrap',
    ]
    for cls in required_classes:
        assert cls in css, f'Missing CSS rule for {cls}'
    assert '[data-theme="dark"] .model-badge' in css
    assert '[data-theme="dark"] .member-subagents-tree' in css
    assert '[data-theme="dark"] .subagent-depth-tag' in css


def test_minimal_dom_shell_guards_missing_optional_model_elements():
    script = r'''
const fs = require('fs'), vm = require('vm'), assert = require('assert');
const source = fs.readFileSync(process.argv[1], 'utf8');
function node() {
  const classes = new Set(), attrs = {}, events = {};
  return {
    innerHTML: '', textContent: '', value: '', style: {}, dataset: {}, disabled: false,
    isConnected: true, inert: false, scrollTop: 0, scrollHeight: 0, clientHeight: 100,
    classList: { add: (x) => classes.add(x), remove: (x) => classes.delete(x), contains: (x) => classes.has(x), toggle: (x, v) => v ? classes.add(x) : classes.delete(x) },
    setAttribute(k, v) { attrs[k] = String(v); }, removeAttribute(k) { delete attrs[k]; }, getAttribute(k) { return attrs[k] ?? null; },
    addEventListener(type, listener) { (events[type] ??= []).push(listener); },
    dispatchEvent(evt) { for (const l of events[evt.type] || []) l(evt); },
    querySelectorAll() { return []; }, querySelector() { return null; }, appendChild() {},
    getBoundingClientRect() { return { width: 1000, height: 600 }; }, getClientRects() { return [{}]; },
    closest() { return null; }, focus() { document.activeElement = this; }, contains(t) { return this === t; },
  };
}
const nodes = new Map();
const omitted = new Set(['runModelBadge', 'graphShowHelpersToggle']);
const document = Object.assign(node(), {
  activeElement: null,
  getElementById(id) {
    if (omitted.has(id)) return null;
    if (!nodes.has(id)) nodes.set(id, node());
    return nodes.get(id);
  },
  querySelectorAll() { return []; },
  createElement: node,
  documentElement: node(),
});
class EventSource { constructor() {} addEventListener() {} close() {} }
const ctx = {
  document, URLSearchParams, EventSource, console, Map, Set, Date, encodeURIComponent,
  setImmediate, requestAnimationFrame: (f) => f(), setTimeout: () => 1, clearTimeout() {},
  setInterval: () => 1, clearInterval() {}, CSS: { escape: String },
  window: Object.assign(node(), { location: { search: '', pathname: '/swarm' }, history: { replaceState() {} }, matchMedia: () => ({ matches: false }), innerWidth: 1200 }),
  fetch: async () => ({ ok: true, headers: { get: () => 'application/json' }, json: async () => ({}) }),
};
vm.createContext(ctx);
vm.runInContext(source.replace('  init();', '  globalThis.runTest = (code) => eval(code);'), ctx);
ctx.assert = assert;
ctx.runTest(`(() => {
  assert.equal(el.runModelBadge, null);
  assert.equal(el.graphShowHelpersToggle, null);
  bindEvents();
  state.run = { id: 'r1', project_id: 'p1', mode: 'swarm', status: 'active', codex_model: 'gpt-6.2-sol', codex_reasoning_effort: 'high' };
  state.loadState = 'ready';
  updateTopRunControls();
  clearRunState();
  assert.equal(state.helperSubagents.length, 0);
  assert.equal(state.sessionSubagents.length, 0);
  assert.equal(state.modelSummary.length, 0);
})()`);
'''
    result = subprocess.run(['node', '-e', script, str(APP)], capture_output=True, text=True, encoding='utf-8')
    assert result.returncode == 0, result.stderr


def test_model_helpers_and_switch_resolution():
    run_dashboard(r'''(() => {
      assert.equal(formatModelText('gpt-6.2-sol', 'ultra'), 'gpt-6.2-sol · ultra');
      assert.equal(formatModelText('gpt-6.2-sol', ''), 'gpt-6.2-sol');
      assert.equal(formatModelText('', 'ultra'), '');

      assert.equal(modelSourceLabel('live'), 'Codex 实时');
      assert.equal(modelSourceLabel('trigger_snapshot'), '触发快照');
      assert.equal(modelSourceLabel('snapshot'), '历史快照');
      assert.equal(modelSourceLabel(null), '未观测');

      const matched = resolveRunModels({ trigger_model: 'gpt-6.1-sol', trigger_reasoning_effort: 'high', codex_model: 'gpt-6.1-sol', codex_reasoning_effort: 'high' });
      assert.equal(matched.primaryModel, 'gpt-6.1-sol');
      assert.equal(matched.primarySource, 'trigger_snapshot');
      assert.equal(matched.switched, false);

      const switched = resolveRunModels({ trigger_model: 'gpt-6.1-sol', trigger_reasoning_effort: 'high', codex_model: 'gpt-6.2-sol', codex_reasoning_effort: 'ultra' });
      assert.equal(switched.primaryModel, 'gpt-6.1-sol');
      assert.equal(switched.liveModel, 'gpt-6.2-sol');
      assert.equal(switched.liveEffort, 'ultra');
      assert.equal(switched.switched, true);

      const effortSwitched = resolveRunModels({ trigger_model: 'gpt-6.2-sol', trigger_reasoning_effort: 'medium', codex_model: 'gpt-6.2-sol', codex_reasoning_effort: 'ultra' });
      assert.equal(effortSwitched.switched, true);

      const liveOnly = resolveRunModels({ codex_model: 'gpt-6.2-sol', codex_reasoning_effort: 'high' });
      assert.equal(liveOnly.primaryModel, 'gpt-6.2-sol');
      assert.equal(liveOnly.primarySource, 'live');
      assert.equal(liveOnly.switched, false);

      const empty = resolveRunModels({});
      assert.equal(empty.primaryModel, null);
      assert.equal(empty.switched, false);

      state.run = { id: 'r1', codex_model: 'gpt-6.2-sol', codex_reasoning_effort: 'ultra', codex_model_provider: 'openai', session_id: 'sess-1' };
      state.workers = [{ id: 'w1', name: 'coder-1', model: 'gpt-6.1-mini', reasoning_effort: 'medium', model_provider: 'openai', codex_thread_id: 'th-w1', model_source: 'live' }];
      assert.equal(resolveSenderModel({ sender: 'human' }), null);
      assert.equal(resolveSenderModel({ sender: 'system' }), null);
      assert.equal(resolveSenderModel({ sender: 'watchdog' }), null);
      assert.equal(resolveSenderModel({ sender: 'workflow' }), null);
      assert.equal(resolveSenderModel({ sender: 'coordinator' }).model, 'gpt-6.2-sol');
      assert.equal(resolveSenderModel({ sender: 'coder-1' }).model, 'gpt-6.1-mini');
      assert.equal(resolveSenderModel({ sender: 'unknown-bot' }), null);
    })()''')


def test_sidebar_topbar_and_command_palette_model_visibility():
    run_dashboard(r'''(() => {
      state.projects = [{ id: 'p1', name: 'Repo', root_path: 'G:/repo', project_kind: 'workspace', is_git: true }];
      state.selectedProjectId = 'p1';
      const switchedRun = {
        id: 'r1', project_id: 'p1', mode: 'swarm', status: 'active', title: 'Switched Run',
        session_id: 'sess-1', session_seq: 1, codex_thread_name: 'Main Thread',
        trigger_model: 'gpt-6.1-sol', trigger_reasoning_effort: 'high',
        codex_model: 'gpt-6.2-sol', codex_reasoning_effort: 'ultra', codex_model_provider: 'openai',
        updated_at: 2000,
      };
      const cleanRun = {
        id: 'r2', project_id: 'p1', mode: 'swarm', status: 'completed', title: 'Clean Run',
        session_id: 'sess-2', session_seq: 1, codex_thread_name: 'Second Thread',
        codex_model: 'gpt-6.0-mini', codex_reasoning_effort: 'low',
        updated_at: 1000,
      };
      state.runs = [switchedRun, cleanRun];
      state.selectedRunId = 'r1';
      state.run = switchedRun;
      state.loadState = 'ready';

      renderRunSidebar();
      const sidebarHtml = el.runSidebarList.innerHTML;
      assert.ok(sidebarHtml.includes('gpt-6.2-sol'), 'Session summary shows live session model');
      assert.ok(sidebarHtml.includes('gpt-6.1-sol'), 'Run item shows trigger snapshot model');
      assert.ok(sidebarHtml.includes('model-badge-mismatch'), 'Switched run shows mismatch indicator');
      assert.ok(sidebarHtml.includes('gpt-6.0-mini'), 'Second run shows live model');

      state.runSearchQuery = 'ultra';
      renderRunSidebar();
      assert.ok(el.runSidebarList.innerHTML.includes('Switched Run'));
      assert.ok(!el.runSidebarList.innerHTML.includes('Clean Run'));

      state.runSearchQuery = 'gpt-6.0-mini';
      renderRunSidebar();
      assert.ok(!el.runSidebarList.innerHTML.includes('Switched Run'));
      assert.ok(el.runSidebarList.innerHTML.includes('Clean Run'));
      state.runSearchQuery = '';

      updateTopRunControls();
      assert.equal(el.runModelBadge.classList.contains('hidden'), false);
      assert.equal(el.runModelBadge.classList.contains('model-badge-mismatch'), true);
      assert.ok(el.runModelBadge.innerHTML.includes('gpt-6.1-sol'));
      assert.ok(el.runModelBadge.innerHTML.includes('high'));
      assert.ok(el.runModelBadge.title.includes('gpt-6.2-sol · ultra'));

      state.cmdPaletteQuery = 'gpt-6.1-sol';
      let entries = getCommandPaletteEntries();
      assert.equal(entries.length, 1);
      assert.equal(entries[0].id, 'r1');
      assert.ok(entries[0].sub.includes('gpt-6.1-sol · high'));
      assert.ok(entries[0].sub.includes('已切换→gpt-6.2-sol · ultra'));

      state.cmdPaletteQuery = 'low';
      entries = getCommandPaletteEntries();
      assert.equal(entries.length, 1);
      assert.equal(entries[0].id, 'r2');

      state.run = { id: 'r3', project_id: 'p1', mode: 'swarm', status: 'active' };
      updateTopRunControls();
      assert.equal(el.runModelBadge.classList.contains('hidden'), true);
    })()''')


def test_overview_and_members_show_models_l2_trees_and_unregistered_subagents():
    run_dashboard(r'''(() => {
      state.projects = [{ id: 'p1', name: 'Repo', root_path: 'G:/repo', project_kind: 'workspace' }];
      state.selectedProjectId = 'p1';
      state.selectedRunId = 'r1';
      state.loadState = 'ready';

      const l2Helper = {
        thread_id: 'th-l2', parent_thread_id: 'th-w1', parent_worker_id: 'w1', parent_worker_name: 'coder-1',
        depth: 2, edge_status: 'completed', agent_path: '/root/coder-1/explorer-1',
        agent_role: 'explorer', agent_nickname: 'fast-scout',
        model: 'gpt-6.1-mini', reasoning_effort: 'low', model_provider: 'openai', model_source: 'live',
      };
      const l3Helper = {
        thread_id: 'th-l3', parent_thread_id: 'th-l2', parent_worker_id: 'w1', parent_worker_name: 'coder-1',
        depth: 3, edge_status: 'running', agent_path: '/root/coder-1/explorer-1/grep-2',
        agent_role: 'investigator', agent_nickname: 'deep-grep',
        model: 'gpt-6.1-nano', reasoning_effort: 'minimal', model_provider: 'openai', model_source: 'snapshot',
      };
      const unregistered = {
        thread_id: 'th-unreg', parent_thread_id: 'sess-1', registered: false,
        parent_worker_id: null, parent_worker_name: 'coordinator', depth: 1,
        edge_status: 'completed', status: 'completed', agent_path: '/root/ad-hoc-explorer',
        agent_role: 'explorer', agent_nickname: 'ad-hoc',
        model: 'gpt-6.2-mini', reasoning_effort: 'medium', model_provider: 'openai', model_source: 'live',
      };

      applySnapshot({
        run: {
          id: 'r1', project_id: 'p1', mode: 'swarm', status: 'active', title: 'Model Tree Run',
          session_id: 'sess-1', session_seq: 2, codex_thread_name: 'Session X',
          trigger_model: 'gpt-6.1-sol', trigger_reasoning_effort: 'high',
          codex_model: 'gpt-6.2-sol', codex_reasoning_effort: 'ultra', codex_model_provider: 'openai',
        },
        workers: [
          {
            id: 'w1', name: 'coder-1', role: 'coder', status: 'working', current_task: 'Build UI',
            agent_id: '/root/coder-1', codex_thread_id: 'th-w1',
            model: 'gpt-6.2-sol', reasoning_effort: 'high', model_provider: 'openai',
            agent_nickname: 'ui-builder', model_source: 'live',
            subagents: [l2Helper, l3Helper],
          },
          {
            id: 'w2', name: 'ver-1', role: 'verifier', status: 'stopped', current_task: 'Verify',
            model: 'gpt-6.1-sol', reasoning_effort: 'medium', model_source: 'snapshot', subagents: [],
          },
        ],
        helper_subagents: [l2Helper, l3Helper],
        session_subagents: [unregistered],
        model_summary: [
          { model: 'gpt-6.2-sol', reasoning_effort: 'ultra', count: 1, roles: ['coordinator'] },
          { model: 'gpt-6.2-sol', reasoning_effort: 'high', count: 1, roles: ['coder'] },
        ],
        messages: [],
      });
      renderAll();

      const ov = el.overviewContent.innerHTML;
      assert.ok(ov.includes('主对话模型'), 'Overview renders primary session model row');
      assert.ok(ov.includes('gpt-6.2-sol'), 'Overview renders live model');
      assert.ok(ov.includes('触发时模型'), 'Overview renders trigger snapshot when switched');
      assert.ok(ov.includes('gpt-6.1-sol'), 'Overview renders trigger model value');
      assert.ok(ov.includes('session-model-summary'), 'Overview renders model summary chips');
      assert.ok(ov.includes('×1（主协调者）'));
      assert.ok(ov.includes('member-subagents-tree is-compact'), 'Overview nests compact L2+ subtree');
      assert.ok(ov.includes('L2 辅助') && ov.includes('L3 辅助'));
      assert.ok(ov.includes('/root/coder-1/explorer-1'));
      assert.ok(ov.includes('主会话未登记原生子代理（1）'), 'Overview shows collapsible unregistered subagents');
      assert.ok(ov.includes('/root/ad-hoc-explorer'));

      const summaryMeta = el.workersSummaryMeta.innerHTML;
      assert.ok(summaryMeta.includes('共 2 名'), 'Preserve exact worker count substring');
      assert.ok(summaryMeta.includes('辅助子代理 3 名'), 'Show total L2+ + unregistered helper count');

      const membersHtml = el.workersList.innerHTML;
      assert.ok(membersHtml.includes('member-subagents-tree'));
      assert.ok(membersHtml.includes('fast-scout'));
      assert.ok(membersHtml.includes('主会话未登记原生子代理 · 1'));
      assert.ok(membersHtml.includes('/root/ad-hoc-explorer'));
      assert.ok(renderMemberRow(state.workers[0]).match(/<div[^>]*role="listitem"[^>]*>\s*<button/));
    })()''')


def test_board_feed_renders_turn_and_sender_model_badges():
    run_dashboard(r'''(() => {
      state.run = {
        id: 'r1', project_id: 'p1', mode: 'swarm', status: 'active',
        session_id: 'sess-1', codex_model: 'gpt-6.2-sol', codex_reasoning_effort: 'ultra', codex_model_provider: 'openai',
      };
      state.workers = [
        { id: 'w1', name: 'coder-1', role: 'coder', status: 'working', model: 'gpt-6.1-mini', reasoning_effort: 'high', codex_thread_id: 'th-w1', model_source: 'live' },
      ];
      state.sessionTurns = [
        { session_id: 'sess-1', turn_id: 't1', prompt_preview: 'Turn one', model: 'gpt-6.1-sol', reasoning_effort: 'medium', created_at: 10 },
      ];
      state.messages = [
        { seq: 1, sender: 'coordinator', sender_role: 'coordinator', body_md: 'go', created_at: 20, channel: 'main' },
        { seq: 2, sender: 'coder-1', sender_role: 'coder', body_md: 'done', created_at: 30, channel: 'main' },
        { seq: 3, sender: 'human', sender_role: 'human', body_md: 'ok', created_at: 40, channel: 'human' },
        { seq: 4, sender: 'system', sender_role: 'system', body_md: 'alert', created_at: 50, channel: 'main' },
      ];
      renderBoardFeed();
      const html = el.boardFeed.innerHTML;
      assert.ok(html.includes('对话轮次 1') && html.includes('gpt-6.1-sol') && html.includes('medium'));
      const rows = html.split('<article class="feed-row').slice(1);
      assert.equal(rows.length, 4);
      assert.ok(rows[0].includes('gpt-6.2-sol') && rows[0].includes('ultra'), 'Coordinator message displays run model');
      assert.ok(rows[1].includes('gpt-6.1-mini') && rows[1].includes('high'), 'Worker message displays worker model');
      assert.ok(!rows[2].includes('model-badge'), 'Human message must not display model badge');
      assert.ok(!rows[3].includes('model-badge'), 'System message must not display model badge');
    })()''')


def test_graph_shows_models_helper_spawn_edges_detail_and_toggle():
    run_dashboard(r'''(() => {
      const l2Helper = {
        thread_id: 'th-l2', parent_thread_id: 'th-w1', parent_worker_id: 'w1', parent_worker_name: 'coder-1',
        depth: 2, edge_status: 'running', agent_path: '/root/coder-1/scout',
        agent_role: 'explorer', agent_nickname: 'scout-nick',
        model: 'gpt-6.1-mini', reasoning_effort: 'low', model_provider: 'openai', model_source: 'live',
      };
      const unregHelper = {
        thread_id: 'th-unreg', parent_thread_id: 'sess-1', registered: false,
        parent_worker_id: null, parent_worker_name: 'coordinator',
        depth: 1, edge_status: 'completed', agent_path: '/root/ad-hoc',
        agent_role: 'explorer', agent_nickname: 'ad-hoc-nick',
        model: 'gpt-6.2-mini', reasoning_effort: 'medium', model_provider: 'openai', model_source: 'live',
      };

      applySnapshot({
        run: {
          id: 'r1', project_id: 'p1', mode: 'swarm', status: 'active',
          session_id: 'sess-1', codex_model: 'gpt-6.2-sol', codex_reasoning_effort: 'ultra', codex_model_provider: 'openai',
        },
        workers: [
          { id: 'w1', name: 'coder-1', role: 'coder', status: 'working', codex_thread_id: 'th-w1', model: 'gpt-6.2-sol', reasoning_effort: 'high', model_provider: 'openai', model_source: 'live' },
        ],
        helper_subagents: [l2Helper],
        session_subagents: [unregHelper],
        messages: [
          { seq: 1, sender: 'coordinator', sender_role: 'coordinator', mentions: ['coder-1'], created_at: 10 },
        ],
      });

      renderGraph();
      const svgHtml = el.graphCanvasHost.innerHTML;
      assert.ok(svgHtml.includes('height="62"'), 'Graph node height bumped to 62');
      assert.ok(svgHtml.includes('gpt-6.2-sol · ultra'), 'Coordinator node shows run model');
      assert.ok(svgHtml.includes('gpt-6.2-sol · high'), 'Worker node shows worker model');
      assert.ok(svgHtml.includes('gpt-6.1-mini · low'), 'L2 helper node shows helper model');
      assert.ok(svgHtml.includes('gpt-6.2-mini · medium'), 'Unregistered subagent node shows model');
      assert.ok(svgHtml.includes('data-from="coder-1" data-to="/root/coder-1/scout"'), 'L2 spawn edge from parent worker');
      assert.ok(svgHtml.includes('data-from="coordinator" data-to="/root/ad-hoc"'), 'Unregistered edge from coordinator');
      assert.ok(svgHtml.includes('data-spawn="1"') && svgHtml.includes('stroke-dasharray="4 3"') && svgHtml.includes('>派生<'));

      state.graphSelectedNode = '/root/coder-1/scout';
      renderGraph();
      const l2Detail = el.graphDetailBar.innerHTML;
      assert.ok(l2Detail.includes('/root/coder-1/scout'));
      assert.ok(l2Detail.includes('gpt-6.1-mini'));
      assert.ok(l2Detail.includes('Provider: openai'));
      assert.ok(l2Detail.includes('线程: th-l2'));
      assert.ok(l2Detail.includes('昵称: scout-nick'));
      assert.ok(!l2Detail.includes('inspect-worker'), 'L2 helper must not have workspace inspector button');

      state.graphSelectedNode = 'coder-1';
      renderGraph();
      const wDetail = el.graphDetailBar.innerHTML;
      assert.ok(wDetail.includes('inspect-worker'), 'L1 worker keeps workspace inspector button');
      assert.ok(wDetail.includes('gpt-6.2-sol') && wDetail.includes('th-w1'));

      bindEvents();
      el.graphShowHelpersToggle.checked = false;
      el.graphShowHelpersToggle.dispatchEvent({ type: 'change' });
      assert.equal(state.graphShowHelpers, false);
      assert.ok(!el.graphCanvasHost.innerHTML.includes('/root/coder-1/scout'));
      assert.ok(!el.graphCanvasHost.innerHTML.includes('/root/ad-hoc'));
    })()''')


def test_inspector_renders_model_thread_and_subagents_for_ws_and_non_ws():
    run_dashboard(r'''(() => {
      const l2Helper = {
        thread_id: 'th-l2', parent_thread_id: 'th-w1', depth: 2,
        edge_status: 'running', agent_path: '/root/coder-1/sub-a',
        agent_role: 'explorer', agent_nickname: 'sub-nick',
        model: 'gpt-6.1-mini', reasoning_effort: 'low', model_provider: 'openai', model_source: 'live',
      };
      state.run = { id: 'r1', status: 'active' };
      state.workers = [
        {
          id: 'w-nows', name: 'scout-1', role: 'investigator', status: 'waiting', current_task: 'Trace',
          agent_id: '/root/scout-1', codex_thread_id: 'th-nows', model: null,
        },
        {
          id: 'w-ws', name: 'coder-1', role: 'coder', status: 'working', current_task: 'Implement',
          workspace_id: 'ws-1', agent_id: '/root/coder-1', codex_thread_id: 'th-w1',
          agent_nickname: 'coder-nick', model: 'gpt-6.2-sol', reasoning_effort: 'ultra',
          model_provider: 'openai', model_source: 'live', subagents: [l2Helper],
        },
      ];
      state.workspaces = [{ id: 'ws-1', mode: 'git_clone', status: 'active', path: 'G:/ws1', branch_name: 'feat/x' }];

      state.selectedWorkerId = 'w-nows';
      renderInspector();
      const noWsHtml = el.inspectorBody.innerHTML;
      assert.ok(noWsHtml.includes('使用模型'));
      assert.ok(noWsHtml.includes('模型未观测'));
      assert.ok(noWsHtml.includes('Codex 线程'));
      assert.ok(noWsHtml.includes('th-nows'));

      state.selectedWorkerId = 'w-ws';
      renderInspector();
      const wsHtml = el.inspectorBody.innerHTML;
      assert.ok(wsHtml.includes('使用模型'));
      assert.ok(wsHtml.includes('gpt-6.2-sol') && wsHtml.includes('ultra'));
      assert.ok(wsHtml.includes('Codex 实时'));
      assert.ok(wsHtml.includes('th-w1') && wsHtml.includes('coder-nick') && wsHtml.includes('openai'));
      assert.ok(wsHtml.includes('下属辅助子代理（L2+）'));
      assert.ok(wsHtml.includes('/root/coder-1/sub-a') && wsHtml.includes('sub-nick') && wsHtml.includes('gpt-6.1-mini'));
    })()''')


def test_upsert_worker_anti_clobber_and_single_debounced_retry():
    script = r'''
const fs = require('fs'), vm = require('vm'), assert = require('assert');
const source = fs.readFileSync(process.argv[1], 'utf8');
function node() {
  const classes = new Set(), attrs = {}, events = {};
  return {
    innerHTML: '', textContent: '', value: '', style: {}, dataset: {}, disabled: false,
    isConnected: true, inert: false, scrollTop: 0, scrollHeight: 0, clientHeight: 100,
    classList: { add: (x) => classes.add(x), remove: (x) => classes.delete(x), contains: (x) => classes.has(x), toggle: (x, v) => v ? classes.add(x) : classes.delete(x) },
    setAttribute(k, v) { attrs[k] = String(v); }, removeAttribute(k) { delete attrs[k]; }, getAttribute(k) { return attrs[k] ?? null; },
    addEventListener(type, listener) { (events[type] ??= []).push(listener); },
    dispatchEvent(evt) { for (const l of events[evt.type] || []) l(evt); },
    querySelectorAll() { return []; }, querySelector() { return null; }, appendChild() {},
    getBoundingClientRect() { return { width: 1000, height: 600 }; }, getClientRects() { return [{}]; },
    closest() { return null; }, focus() { document.activeElement = this; }, contains(t) { return this === t; },
  };
}
const nodes = new Map(), timers = new Map(), cleared = [], fetches = [];
let nextTimer = 1;
const document = Object.assign(node(), {
  activeElement: null,
  getElementById(id) { if (!nodes.has(id)) nodes.set(id, node()); return nodes.get(id); },
  querySelectorAll() { return []; },
  createElement: node,
  documentElement: node(),
});
class EventSource { constructor() {} addEventListener() {} close() {} }
const ctx = {
  document, URLSearchParams, EventSource, console, Map, Set, Date, encodeURIComponent,
  setImmediate, requestAnimationFrame: (f) => f(),
  setTimeout: (fn, ms) => { const id = nextTimer++; timers.set(id, { fn, ms }); return id; },
  clearTimeout: (id) => { cleared.push(id); timers.delete(id); },
  setInterval: () => 1, clearInterval() {}, CSS: { escape: String },
  window: Object.assign(node(), { location: { search: '', pathname: '/swarm' }, history: { replaceState() {} }, matchMedia: () => ({ matches: false }), innerWidth: 1200 }),
  fetch: async (url) => {
    fetches.push(url);
    return {
      ok: true,
      headers: { get: () => 'application/json' },
      json: async () => ({
        run: { id: 'r1', project_id: 'p1', mode: 'swarm', status: 'active', session_id: 'sess-1' },
        workers: [{ id: 'w2', name: 'coder-2', agent_id: '/root/coder-2', model: 'gpt-6.2-sol', reasoning_effort: 'high', status: 'working' }],
      }),
    };
  },
};
vm.createContext(ctx);
vm.runInContext(source.replace('  init();', '  globalThis.runTest = (code) => eval(code);'), ctx);
ctx.assert = assert; ctx.timers = timers; ctx.cleared = cleared; ctx.fetches = fetches;
ctx.runTest(`(async () => {
  state.selectedProjectId = 'p1';
  state.selectedRunId = 'r1';
  state.run = { id: 'r1', project_id: 'p1', mode: 'swarm', status: 'active', session_id: 'sess-1' };
  state.loadState = 'ready';
  state.workers = [{
    id: 'w1', name: 'coder-1', role: 'coder', status: 'working', agent_id: '/root/coder-1',
    model: 'gpt-6.2-sol', reasoning_effort: 'ultra', model_provider: 'openai',
    codex_thread_id: 'th-w1', agent_nickname: 'nick-1', model_source: 'live',
    subagents: [{ thread_id: 'th-l2', depth: 2, agent_path: '/root/coder-1/sub', model: 'gpt-6.1-mini' }],
  }];

  // Incoming partial/null SSE worker_updated must not clobber existing model metadata or subagents.
  upsertWorker({
    id: 'w1', name: 'coder-1', status: 'waiting', agent_id: '/root/coder-1',
    model: null, reasoning_effort: null, model_provider: null, codex_thread_id: null, agent_nickname: null, model_source: null,
  });
  assert.equal(state.workers[0].status, 'waiting');
  assert.equal(state.workers[0].model, 'gpt-6.2-sol');
  assert.equal(state.workers[0].reasoning_effort, 'ultra');
  assert.equal(state.workers[0].model_provider, 'openai');
  assert.equal(state.workers[0].codex_thread_id, 'th-w1');
  assert.equal(state.workers[0].agent_nickname, 'nick-1');
  assert.equal(state.workers[0].model_source, 'live');
  assert.equal(state.workers[0].subagents.length, 1);
  assert.equal(timers.size, 0, 'No retry needed when model is preserved');

  // Incoming worker with agent_id and missing model schedules one debounced ~1000ms snapshot retry.
  upsertWorker({ id: 'w2', name: 'coder-2', agent_id: '/root/coder-2', status: 'working', model: null });
  upsertWorker({ id: 'w2', name: 'coder-2', agent_id: '/root/coder-2', status: 'working' });
  assert.equal(cleared.length, 1, 'Second rapid event debounces the first timer');
  assert.equal(timers.size, 1);
  const [[timerId, timer]] = Array.from(timers.entries());
  assert.equal(timer.ms, 1000);
  timers.delete(timerId);
  timer.fn();
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(fetches.length, 1);
  assert.equal(fetches[0], '/api/runs/r1/snapshot');
  assert.equal(state.workers.length, 1);
  assert.equal(state.workers[0].id, 'w2');
  assert.equal(state.workers[0].model, 'gpt-6.2-sol');

  // clearRunState cancels pending timer.
  upsertWorker({ id: 'w3', name: 'coder-3', agent_id: '/root/coder-3', status: 'working', model: null });
  assert.equal(timers.size, 1);
  clearRunState();
  assert.equal(timers.size, 0);
  assert.equal(state.workerModelRetryTimer, null);
})()`);
'''
    result = subprocess.run(['node', '-e', script, str(APP)], capture_output=True, text=True, encoding='utf-8')
    assert result.returncode == 0, result.stderr


def test_xss_escaping_across_all_model_and_subagent_views():
    run_dashboard(r'''(() => {
      const xssModel = '<img src=x onerror=alert(1)>';
      const xssEffort = '" onmouseover="alert(2)';
      const xssProvider = '<script>badProvider()</script>';
      const xssThread = '<img src=t>';
      const xssNick = '<svg onload=alert(3)>';
      const xssPath = '/root/<img src=p>';

      const helper = {
        thread_id: xssThread, parent_thread_id: 'th-w1', parent_worker_id: 'w1', parent_worker_name: 'coder-1',
        depth: 2, edge_status: '<bad>', agent_path: xssPath,
        agent_role: '<script>role</script>', agent_nickname: xssNick,
        model: xssModel, reasoning_effort: xssEffort, model_provider: xssProvider, model_source: 'live',
      };
      const unreg = {
        thread_id: 'th-u', parent_thread_id: 'sess-1', registered: false,
        parent_worker_id: null, parent_worker_name: 'coordinator', depth: 1,
        edge_status: 'running', agent_path: xssPath + '-u',
        agent_role: 'explorer', agent_nickname: xssNick,
        model: xssModel, reasoning_effort: xssEffort, model_provider: xssProvider, model_source: 'live',
      };
      const run = {
        id: 'r1', project_id: 'p1', mode: 'swarm', status: 'active', title: 'XSS Run',
        session_id: 'sess-1', session_seq: 1, codex_thread_name: 'Thread',
        trigger_model: xssModel, trigger_reasoning_effort: xssEffort,
        codex_model: xssModel + '2', codex_reasoning_effort: 'ultra', codex_model_provider: xssProvider,
      };
      state.projects = [{ id: 'p1', name: 'Repo', root_path: 'G:/repo', project_kind: 'workspace' }];
      state.selectedProjectId = 'p1';
      state.selectedRunId = 'r1';
      state.runs = [run];
      state.loadState = 'ready';

      applySnapshot({
        run,
        session_turns: [{ session_id: 'sess-1', turn_id: 't1', model: xssModel, reasoning_effort: xssEffort, created_at: 10 }],
        workers: [{
          id: 'w1', name: 'coder-1', role: 'coder', status: 'working', current_task: 'Work',
          agent_id: '/root/coder-1', codex_thread_id: xssThread,
          agent_nickname: xssNick, model: xssModel, reasoning_effort: xssEffort,
          model_provider: xssProvider, model_source: 'live', subagents: [helper],
        }],
        helper_subagents: [helper],
        session_subagents: [unreg],
        model_summary: [{ model: xssModel, reasoning_effort: xssEffort, count: 1, roles: ['coder'] }],
        messages: [{ seq: 1, sender: 'coder-1', sender_role: 'coder', body_md: 'msg', created_at: 20, channel: 'main' }],
      });
      state.selectedWorkerId = 'w1';
      state.graphSelectedNode = xssPath;
      renderRunSidebar();
      renderAll();
      renderCommandPalette();

      const outputs = [
        el.runModelBadge.innerHTML,
        el.runSidebarList.innerHTML,
        el.cmdPaletteResults.innerHTML,
        el.overviewContent.innerHTML,
        el.workersList.innerHTML,
        el.boardFeed.innerHTML,
        el.graphCanvasHost.innerHTML,
        el.graphDetailBar.innerHTML,
        el.inspectorBody.innerHTML,
      ];
      for (const html of outputs) {
        assert.ok(!html.includes('<img'), 'Unescaped <img found');
        assert.ok(!html.includes('<script'), 'Unescaped <script found');
        assert.ok(!html.includes('<svg onload'), 'Unescaped <svg onload found');
        assert.ok(!html.includes('" onmouseover="'), 'Unescaped attribute quote breakout found');
      }
      assert.ok(el.overviewContent.innerHTML.includes('&lt;img'));
      assert.ok(el.inspectorBody.innerHTML.includes('&lt;script&gt;'));
      assert.ok(el.graphCanvasHost.innerHTML.includes('&lt;img'));
      assert.ok(el.graphDetailBar.innerHTML.includes('&lt;svg'));
    })()''')
