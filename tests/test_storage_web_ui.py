"""Tests for the Storage & Cleanup web console UI and SSE auto-refresh contract."""

from __future__ import annotations

import pathlib
import subprocess

from test_dashboard_behavior import APP, run_dashboard

WEB_DIR = APP.parent
INDEX_HTML = WEB_DIR / "index.html"
STYLES_CSS = WEB_DIR / "styles.css"


def test_storage_html_css_and_js_syntax() -> None:
    html = INDEX_HTML.read_text(encoding="utf-8")
    css = STYLES_CSS.read_text(encoding="utf-8")
    js = APP.read_text(encoding="utf-8")

    # Zero-CDN constraint preserved.
    assert "http://" not in html and "https://" not in html

    # Topbar button inside topbar-right before themeToggleBtn.
    topbar_right_start = html.index('class="topbar-right"')
    theme_btn_pos = html.index('id="themeToggleBtn"')
    storage_btn_pos = html.index('id="storageManageBtn"')
    assert topbar_right_start < storage_btn_pos < theme_btn_pos
    assert 'id="storageManageBadge"' in html
    assert "存储与清理" in html

    # Modal elements and action controls.
    for elem_id in (
        "storageModalBackdrop",
        "storageModalDialog",
        "storageModalTitle",
        "storageTotalPill",
        "storageRefreshBtn",
        "storageCloseBtn",
        "storageModalBody",
        "storageGcBtn",
    ):
        assert f'id="{elem_id}"' in html, f"Missing element #{elem_id} in index.html"
    assert "立即清理安全缓存与已合入工作区" in html

    # Required CSS rules.
    for selector in (
        ".storage-manage-btn.has-cleanable",
        ".storage-manage-btn.has-dirty",
        ".storage-btn-badge",
        ".storage-dialog",
        ".storage-metrics-grid",
        ".storage-ws-item.is-dirty-review",
        ".storage-dirty-note",
    ):
        assert selector in css, f"Missing CSS rule {selector}"

    # JS syntax & API/SSE symbols.
    assert "/api/storage" in js
    assert "/api/storage/gc" in js
    assert "storage_updated" in js
    assert "workspace_updated" in js
    syntax = subprocess.run(["node", "--check", str(APP)], capture_output=True, text=True, encoding="utf-8")
    assert syntax.returncode == 0, syntax.stderr


def test_storage_topbar_badge_states_and_bytes_formatting() -> None:
    run_dashboard(r'''(() => {
      assert.equal(formatBytes(0), '0 B');
      assert.equal(formatBytes(512), '512 B');
      assert.equal(formatBytes(1536), '1.5 KB');
      assert.equal(formatBytes(5 * 1024 * 1024), '5.00 MB');
      assert.equal(formatBytes(2 * 1024 * 1024 * 1024), '2.00 GB');

      // Initial empty report hides badge.
      state.storageReport = null;
      renderStorageBadge();
      assert.equal(el.storageManageBadge.classList.contains('hidden'), true);
      assert.equal(el.storageManageBtn.classList.contains('is-highlighted'), false);

      // Cleanable space highlights button & badge.
      state.storageReport = {
        total_bytes: 6 * 1024 * 1024,
        runtime: { total_bytes: 1024 * 1024 },
        workspaces: { total_bytes: 5 * 1024 * 1024, cleanable_bytes: 5 * 1024 * 1024, cleanable_count: 2, dirty_review_count: 0, active_count: 0, items: [] },
      };
      renderStorageBadge();
      assert.equal(el.storageManageBadge.classList.contains('hidden'), false);
      assert.equal(el.storageManageBadge.textContent, '可清 5.00 MB');
      assert.equal(el.storageManageBtn.classList.contains('has-cleanable'), true);
      assert.equal(el.storageManageBtn.classList.contains('has-dirty'), false);
      assert.equal(el.storageManageBtn.classList.contains('is-highlighted'), true);

      // Dirty review items raise warning highlight.
      state.storageReport = {
        total_bytes: 3 * 1024 * 1024,
        runtime: { total_bytes: 1024 * 1024 },
        workspaces: { total_bytes: 2 * 1024 * 1024, cleanable_bytes: 0, cleanable_count: 0, dirty_review_count: 2, active_count: 1, items: [] },
      };
      renderStorageBadge();
      assert.equal(el.storageManageBtn.classList.contains('has-dirty'), true);
      assert.equal(el.storageManageBtn.classList.contains('is-highlighted'), true);
      assert.ok(el.storageManageBadge.textContent.includes('2 待核查'));

      // Clean state shows total footprint without highlight.
      state.storageReport = {
        total_bytes: 2048,
        runtime: { total_bytes: 2048 },
        workspaces: { total_bytes: 0, cleanable_bytes: 0, cleanable_count: 0, dirty_review_count: 0, active_count: 0, items: [] },
      };
      renderStorageBadge();
      assert.equal(el.storageManageBtn.classList.contains('is-highlighted'), false);
      assert.equal(el.storageManageBtn.classList.contains('has-cleanable'), false);
      assert.equal(el.storageManageBtn.classList.contains('has-dirty'), false);
      assert.equal(el.storageManageBadge.textContent, '2.0 KB');
    })()''')


def test_storage_modal_renders_runtime_workspaces_and_escapes_xss() -> None:
    run_dashboard(r'''(async () => {
      bindEvents();
      el.storageManageBtn.focus();
      el.storageManageBtn.dispatchEvent({ type: 'click' });

      assert.equal(state.storageModalOpen, true);
      assert.equal(el.storageModalBackdrop.classList.contains('hidden'), false);
      assert.equal(el.storageModalBackdrop.getAttribute('aria-hidden'), 'false');
      assert.equal(el.storageManageBtn.getAttribute('aria-expanded'), 'true');
      assert.equal(document.activeElement, el.storageModalTitle);
      assert.equal(requests.length, 1);
      assert.equal(requests[0].url, '/api/storage');

      requests[0].resolve({
        data_dir: 'C:\\data\\<script>alert(1)</script>\\tri-orchestrator',
        total_bytes: 15 * 1024 * 1024,
        runtime: {
          state_db_bytes: 512 * 1024,
          logs_bytes: 256 * 1024,
          results_bytes: 2 * 1024 * 1024,
          results_count: 17,
          receipts_bytes: 64 * 1024,
          total_bytes: 2880 * 1024,
        },
        workspaces: {
          total_bytes: 12 * 1024 * 1024,
          cleanable_bytes: 4 * 1024 * 1024,
          cleanable_count: 1,
          dirty_review_count: 1,
          active_count: 1,
          items: [
            {
              workspace_id: 'ws-clean',
              run_id: 'run_clean_1234',
              run_status: 'completed',
              worker_name: 'coder-clean',
              mode: 'snapshot',
              status: 'merged',
              path: 'G:\\proj\\.swarm-workspaces\\clean',
              exists: true,
              bytes: 4 * 1024 * 1024,
              category: 'cleanable',
              reason: '已合入且无未合入改动',
              added_count: 0,
              modified_count: 0,
            },
            {
              workspace_id: 'ws-dirty',
              run_id: 'run_dirty_5678',
              run_status: 'completed',
              worker_name: '<img src=x onerror=alert(2)>',
              mode: 'git_clone',
              status: 'active',
              path: 'G:\\proj\\.swarm-workspaces\\<script>bad</script>',
              exists: true,
              bytes: 6 * 1024 * 1024,
              category: 'dirty_review',
              reason: '存在未合入文件 <img src=y>',
              added_count: 3,
              modified_count: 2,
            },
            {
              workspace_id: 'ws-active',
              run_id: 'run_active_9012',
              run_status: 'active',
              worker_name: 'coder-live',
              mode: 'snapshot',
              status: 'active',
              path: 'G:\\proj\\.swarm-workspaces\\live',
              exists: true,
              bytes: 2 * 1024 * 1024,
              category: 'active',
              reason: '所属任务正在执行',
              added_count: 1,
              modified_count: 0,
            },
          ],
        },
      });
      await new Promise((resolve) => setImmediate(resolve));

      const html = el.storageModalBody.innerHTML;
      // Runtime breakdown
      assert.ok(html.includes('SQLite 状态库（state.db）'));
      assert.ok(html.includes('512.0 KB'));
      assert.ok(html.includes('大结果缓存（results/）'));
      assert.ok(html.includes('2.00 MB'));
      assert.ok(html.includes('17 个缓存文件'));
      assert.ok(html.includes('运行日志（logs/）'));
      assert.ok(html.includes('256.0 KB'));
      assert.ok(html.includes('验收回执（receipts/）'));
      assert.ok(html.includes('64.0 KB'));

      // Workspace 3 categories, counts, and paths
      assert.ok(html.includes('可安全清理'));
      assert.ok(html.includes('含未合入变更待核查'));
      assert.ok(html.includes('进行中'));
      assert.ok(html.includes('新增 3 · 修改 2'));
      assert.ok(html.includes('不自动删除'));
      assert.ok(html.includes('G:\\proj\\.swarm-workspaces\\clean'));
      assert.ok(html.includes('G:\\proj\\.swarm-workspaces\\live'));
      // dirty_review is sorted first before cleanable and active
      assert.ok(html.indexOf('ws-dirty') < html.indexOf('ws-clean'));
      assert.ok(html.indexOf('ws-clean') < html.indexOf('ws-active'));

      // XSS protection
      assert.ok(!html.includes('<script>'));
      assert.ok(!html.includes('<img '));
      assert.ok(html.includes('&lt;script&gt;alert(1)&lt;/script&gt;'));
      assert.ok(html.includes('&lt;img src=x onerror=alert(2)&gt;'));
      assert.ok(html.includes('&lt;script&gt;bad&lt;/script&gt;'));

      // Escape closes modal and restores focus to topbar button
      document.dispatchEvent({ type: 'keydown', key: 'Escape', preventDefault() {} });
      assert.equal(state.storageModalOpen, false);
      assert.equal(el.storageModalBackdrop.classList.contains('hidden'), true);
      assert.equal(el.storageManageBtn.getAttribute('aria-expanded'), 'false');
      assert.equal(document.activeElement, el.storageManageBtn);

      // Re-open -> Tab wraps inside dialog -> backdrop click closes modal
      el.storageManageBtn.dispatchEvent({ type: 'click' });
      assert.equal(state.storageModalOpen, true);
      requests[1].resolve({ data_dir: 'C:\\data', total_bytes: 0, runtime: { total_bytes: 0 }, workspaces: { total_bytes: 0, items: [] } });
      await new Promise((resolve) => setImmediate(resolve));
      el.storageModalDialog.querySelectorAll = () => [el.storageRefreshBtn, el.storageGcBtn];
      el.storageGcBtn.focus();
      let tabPrevented = false;
      document.dispatchEvent({ type: 'keydown', key: 'Tab', shiftKey: false, preventDefault() { tabPrevented = true; } });
      assert.equal(tabPrevented, true);
      assert.equal(document.activeElement, el.storageRefreshBtn);
      el.storageModalBackdrop.dispatchEvent({ type: 'click', target: el.storageModalBackdrop });
      assert.equal(state.storageModalOpen, false);
    })()''')


def test_storage_gc_and_refresh_actions_and_error_visibility() -> None:
    run_dashboard(r'''(async () => {
      const calls = [];
      const queue = [];
      fetch = (url, options = {}) => new Promise((resolve, reject) => {
        calls.push({ url, options });
        queue.push({
          resolve: (data, ok = true, status = 200) => resolve({
            ok,
            status,
            statusText: ok ? 'OK' : 'Error',
            headers: { get: () => 'application/json' },
            json: async () => data,
          }),
          reject,
        });
      });

      bindEvents();
      openStorageModal();
      assert.equal(calls.length, 1);
      assert.equal(calls[0].url, '/api/storage');

      queue[0].resolve({
        data_dir: 'C:\\data\\tri-orch',
        total_bytes: 10 * 1024 * 1024,
        runtime: { state_db_bytes: 1024, logs_bytes: 0, results_bytes: 1024 * 1024, results_count: 3, receipts_bytes: 0, total_bytes: 1025 * 1024 },
        workspaces: {
          total_bytes: 9 * 1024 * 1024,
          cleanable_bytes: 5 * 1024 * 1024,
          cleanable_count: 1,
          dirty_review_count: 1,
          active_count: 0,
          items: [
            { workspace_id: 'ws-1', run_id: 'r1', run_status: 'completed', worker_name: 'coder-1', mode: 'snapshot', status: 'merged', path: 'G:\\ws-1', exists: true, bytes: 5 * 1024 * 1024, category: 'cleanable', reason: 'safe', added_count: 0, modified_count: 0 },
            { workspace_id: 'ws-2', run_id: 'r2', run_status: 'completed', worker_name: 'coder-2', mode: 'snapshot', status: 'active', path: 'G:\\ws-2', exists: true, bytes: 4 * 1024 * 1024, category: 'dirty_review', reason: 'dirty', added_count: 2, modified_count: 1 },
          ],
        },
      });
      await new Promise((resolve) => setImmediate(resolve));

      // Click GC button -> POST /api/storage/gc with exact payload
      el.storageGcBtn.dispatchEvent({ type: 'click' });
      assert.equal(calls.length, 2);
      assert.equal(calls[1].url, '/api/storage/gc');
      assert.equal(calls[1].options.method, 'POST');
      assert.deepEqual(JSON.parse(calls[1].options.body), {
        clean_results: true,
        clean_workspaces: true,
        include_all_completed: true,
        dry_run: false,
      });

      const reportAfter = {
        data_dir: 'C:\\data\\tri-orch',
        total_bytes: 4 * 1024 * 1024 + 1024,
        runtime: { state_db_bytes: 1024, logs_bytes: 0, results_bytes: 0, results_count: 0, receipts_bytes: 0, total_bytes: 1024 },
        workspaces: {
          total_bytes: 4 * 1024 * 1024,
          cleanable_bytes: 0,
          cleanable_count: 0,
          dirty_review_count: 1,
          active_count: 0,
          items: [
            { workspace_id: 'ws-2', run_id: 'r2', run_status: 'completed', worker_name: 'coder-2', mode: 'snapshot', status: 'active', path: 'G:\\ws-2', exists: true, bytes: 4 * 1024 * 1024, category: 'dirty_review', reason: 'dirty', added_count: 2, modified_count: 1 },
          ],
        },
      };
      queue[1].resolve({
        ok: true,
        dry_run: false,
        freed_bytes: 6 * 1024 * 1024,
        cleaned_results: ['res1.json', 'res2.json', 'res3.json'],
        cleaned_workspaces: ['ws-1'],
        skipped_dirty_workspaces: [{ workspace_id: 'ws-2', path: 'G:\\ws-2' }],
        report_after: reportAfter,
      });
      await new Promise((resolve) => setImmediate(resolve));

      const afterHtml = el.storageModalBody.innerHTML;
      assert.ok(afterHtml.includes('本次清理已释放 6.00 MB'));
      assert.ok(afterHtml.includes('已清理结果缓存 3 项'));
      assert.ok(afterHtml.includes('已清理安全工作区 1 个'));
      assert.ok(afterHtml.includes('已跳过含未合入变更工作区 1 个'));
      assert.ok(!afterHtml.includes('ws-1'));
      assert.ok(afterHtml.includes('ws-2'));

      // Click Refresh button -> GET /api/storage
      el.storageRefreshBtn.dispatchEvent({ type: 'click' });
      assert.equal(calls.length, 3);
      assert.equal(calls[2].url, '/api/storage');
      queue[2].reject(new Error('disk scan timeout'));
      await new Promise((resolve) => setImmediate(resolve));
      assert.ok(el.storageModalBody.innerHTML.includes('获取存储占用失败'));
      assert.ok(el.storageModalBody.innerHTML.includes('disk scan timeout'));

      // GC failure surfaces explicit error banner, never fake success
      el.storageGcBtn.dispatchEvent({ type: 'click' });
      assert.equal(calls.length, 4);
      queue[3].resolve({ detail: 'permission denied on gc' }, false, 500);
      await new Promise((resolve) => setImmediate(resolve));
      assert.ok(el.storageModalBody.innerHTML.includes('执行安全清理失败'));
      assert.ok(el.storageModalBody.innerHTML.includes('permission denied on gc'));
    })()''')


def test_storage_sse_auto_refresh_behavior() -> None:
    run_dashboard(r'''(async () => {
      state.selectedRunId = 'run-current';
      state.loadState = 'ready';
      connectGlobalEvents();
      const globalStream = streams.find((s) => s.url === '/api/events');
      assert.ok(globalStream);
      assert.equal(typeof globalStream.events.storage_updated, 'function');
      assert.equal(typeof globalStream.events.workspace_updated, 'function');

      // When modal is closed, workspace_updated for another run does not fetch /api/storage.
      globalStream.events.workspace_updated({ data: JSON.stringify({ run_id: 'other-run', workspace: { id: 'ws-other' } }) });
      assert.equal(requests.filter((r) => r.url === '/api/storage').length, 0);

      // When modal is open, storage_updated and workspace_updated (even for other runs) refresh /api/storage.
      state.storageModalOpen = true;
      globalStream.events.storage_updated({ data: JSON.stringify({ freed_bytes: 1024 }) });
      assert.equal(requests.filter((r) => r.url === '/api/storage').length, 1);
      requests[requests.length - 1].resolve({
        data_dir: 'C:\\data',
        total_bytes: 4096,
        runtime: { state_db_bytes: 4096, logs_bytes: 0, results_bytes: 0, results_count: 0, receipts_bytes: 0, total_bytes: 4096 },
        workspaces: { total_bytes: 0, cleanable_bytes: 0, cleanable_count: 0, dirty_review_count: 0, active_count: 0, items: [] },
      });
      await new Promise((resolve) => setImmediate(resolve));
      assert.ok(el.storageModalBody.innerHTML.includes('4.0 KB'));

      globalStream.events.workspace_updated({ data: JSON.stringify({ run_id: 'other-run', workspace: { id: 'ws-other', status: 'cleaned' } }) });
      assert.equal(requests.filter((r) => r.url === '/api/storage').length, 2);

      // daemon.py _publish_gc_events payload embeds report directly -> updates modal & badge immediately without extra fetch
      globalStream.events.storage_updated({
        data: JSON.stringify({
          freed_bytes: 2048,
          cleaned_results: [],
          cleaned_workspaces: ['ws-other'],
          report: {
            data_dir: 'C:\\data',
            total_bytes: 2048,
            runtime: { state_db_bytes: 2048, logs_bytes: 0, results_bytes: 0, results_count: 0, receipts_bytes: 0, total_bytes: 2048 },
            workspaces: { total_bytes: 0, cleanable_bytes: 0, cleanable_count: 0, dirty_review_count: 0, active_count: 0, items: [] },
          },
        }),
      });
      assert.equal(requests.filter((r) => r.url === '/api/storage').length, 2);
      assert.ok(el.storageModalBody.innerHTML.includes('2.0 KB'));
      assert.equal(el.storageManageBadge.textContent, '2.0 KB');
    })()''')


def test_minimal_dom_shell_guards_missing_storage_elements() -> None:
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
const omitted = new Set([
  'storageManageBtn', 'storageManageBadge', 'storageModalBackdrop',
  'storageModalDialog', 'storageModalTitle', 'storageTotalPill',
  'storageRefreshBtn', 'storageCloseBtn', 'storageModalBody', 'storageGcBtn',
]);
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
ctx.runTest(`(async () => {
  assert.equal(el.storageManageBtn, null);
  assert.equal(el.storageModalBody, null);
  bindEvents();
  openStorageModal();
  await loadStorageReport();
  renderStorageModal();
  closeStorageModal();
})()`);
'''
    result = subprocess.run(["node", "-e", script, str(APP)], capture_output=True, text=True, encoding="utf-8")
    assert result.returncode == 0, result.stderr
