(() => {
  'use strict';

  const HEALTH_POLL_MS = 15000;
  const HEARTBEAT_TICK_MS = 1000;
  const GRAPH_PLAY_INTERVAL_MS = 700;
  const THEME_STORAGE_KEY = 'tri-orch-theme';
  const BASE_CHANNELS = ['all', 'main', 'human'];

  const MODE_LABELS = {
    boost: '极速模式',
    teamwork: '协同模式',
    swarm: '蜂窝模式',
  };
  const MODE_NAMES = { boost: 'Boost · 专注执行', teamwork: 'Teamwork · 分工协作', swarm: 'Swarm · 并行协作' };
  const MODE_DESCRIPTIONS = {
    boost: '围绕一个目标推进，按需委派，完成后独立验收。',
    teamwork: '明确需求和文件分工，再开发、验收与合并。',
    swarm: '多名执行者并行推进，通过消息板持续协调。',
  };
  const PAGE_MODE = window.location.pathname.replace(/^\/|\/$/g, '') || null;

  const RUN_STATUS_LABELS = {
    active: '进行中',
    completed: '已完成',
    failed: '已失败',
    archived: '已归档',
  };

  const WORKER_STATUS_LABELS = {
    waiting: '待命',
    working: '执行中',
    stopped: '已暂停',
    killed: '已终止',
  };

  const ROLE_LABELS = {
    coordinator: '主协调者',
    interviewer: '需求访谈',
    planner: '架构规划',
    investigator: '代码勘探',
    coder: '开发实现',
    verifier: '独立验收',
    swarm_worker: '蜂窝执行',
    human: '人工干预',
    watchdog: '监控看门狗',
    system: '系统看门狗',
    workflow: '工作流引擎',
  };

  const CHANNEL_LABELS = {
    all: '全部',
    main: '主频道',
    human: '人工',
  };

  const MSG_TYPE_LABELS = {
    info: '通知',
    handoff: '交接',
    diff: '差异',
    test: '测试',
    alert: '告警',
    human: '人工',
  };

  const WS_MODE_LABELS = {
    git_clone: 'Git 本地克隆',
    snapshot: '目录快照',
  };

  const WS_STATUS_LABELS = {
    active: '活跃',
    verified: '已验证',
    merged: '已合入',
    cleaned: '已清理',
    conflict: '有冲突',
  };

  const TP_STATUS_LABELS = {
    active: '已冻结',
    archived: '已归档',
    superseded: '已更新',
    draft: '草稿',
  };

  const state = {
    theme: 'light',
    activeTab: 'overview',
    inspectorOpen: false,
    inspectorReturnFocus: null,

    contextVersion: 0,
    snapshotVersion: 0,
    loadState: 'idle',
    loadError: '',
    expandedMessages: new Set(),
    overviewExpanded: new Set(),
    diffErrors: new Map(),
    sending: false,
    projects: [],
    selectedProjectId: null,
    runs: [],
    selectedRunId: null,
    run: null,
    taskPacket: null,
    workflow: null,
    inbox: null,
    nativeActions: null,
    progressStatus: null,
    resumeContext: null,
    planReadiness: null,
    workers: [],
    controlRequests: [],
    workspaces: [],
    messages: [],
    channels: new Set(['main', 'human']),

    activeChannel: 'all',
    mentionFilter: '',
    searchQuery: '',
    autoScroll: true,

    selectedWorkerId: null,
    diffByWorkspace: new Map(),
    diffRequests: new Map(),
    mergePreviewByWorkspace: new Map(),
    inspectorBannerByWorkspace: new Map(),
    diffWrap: false,
    actionBusy: false,

    healthOk: null,
    healthPort: null,
    healthPid: null,
    sseState: 'idle',
    eventSource: null,
    sseHadError: false,
    sseRunId: null,

    graphStep: null,
    graphPlaying: false,
    graphPlayTimer: null,
    graphHideInactive: false,
    graphExpandAll: true,
    graphSelectedNode: null,
    graphHoverNode: null,
  };

  const el = {
    modeChooser: document.getElementById('modeChooser'),
    modeChooserStatus: document.getElementById('modeChooserStatus'),
    dashboardShell: document.getElementById('dashboardShell'),
    modeDescription: document.getElementById('modeDescription'),
    modeLinks: document.querySelectorAll('[data-mode-link]'),
    projectSelect: document.getElementById('projectSelect'),
    runSelect: document.getElementById('runSelect'),
    runMetaPill: document.getElementById('runMetaPill'),
    modeBadge: document.getElementById('modeBadge'),
    runStatusBadge: document.getElementById('runStatusBadge'),
    healthBadge: document.getElementById('healthBadge'),
    toggleInspectorBtn: document.getElementById('toggleInspectorBtn'),
    themeToggleBtn: document.getElementById('themeToggleBtn'),
    themeIconSun: document.getElementById('themeIconSun'),
    themeIconMoon: document.getElementById('themeIconMoon'),
    refreshSnapshotBtn: document.getElementById('refreshSnapshotBtn'),
    exportDropdown: document.getElementById('exportDropdown'),
    exportMenuBtn: document.getElementById('exportMenuBtn'),
    exportMenu: document.getElementById('exportMenu'),
    exportMdBtn: document.getElementById('exportMdBtn'),
    exportJsonBtn: document.getElementById('exportJsonBtn'),

    overviewContent: document.getElementById('overviewContent'),
    contextStatus: document.getElementById('contextStatus'),
    runTitleHeading: document.getElementById('runTitleHeading'),
    projectPathSubtitle: document.getElementById('projectPathSubtitle'),
    sseBadge: document.getElementById('sseBadge'),
    tabButtons: Array.from(document.querySelectorAll('.tab-item')),
    tabBoardCount: document.getElementById('tabBoardCount'),
    tabMembersCount: document.getElementById('tabMembersCount'),
    tabPanels: {
      overview: document.getElementById('tab-overview'),
      board: document.getElementById('tab-board'),
      members: document.getElementById('tab-members'),
      graph: document.getElementById('tab-graph'),
    },

    channelTabs: document.getElementById('channelTabs'),
    boardStatsSubtitle: document.getElementById('boardStatsSubtitle'),
    mentionFilterSelect: document.getElementById('mentionFilterSelect'),
    boardSearchInput: document.getElementById('boardSearchInput'),
    jumpLatestBtn: document.getElementById('jumpLatestBtn'),
    autoScrollToggle: document.getElementById('autoScrollToggle'),
    boardFeed: document.getElementById('boardFeed'),

    humanMessageForm: document.getElementById('humanMessageForm'),
    humanMessageInput: document.getElementById('humanMessageInput'),
    humanChannelSelect: document.getElementById('humanChannelSelect'),
    humanChannelCustom: document.getElementById('humanChannelCustom'),
    humanMentionSelect: document.getElementById('humanMentionSelect'),
    humanMentionCustom: document.getElementById('humanMentionCustom'),
    humanSendBtn: document.getElementById('humanSendBtn'),

    taskPacketHeading: document.getElementById('taskPacketHeading'),
    taskPacketMeta: document.getElementById('taskPacketMeta'),
    taskPacketCriteriaCount: document.getElementById('taskPacketCriteriaCount'),
    taskPacketBody: document.getElementById('taskPacketBody'),
    workersSummaryMeta: document.getElementById('workersSummaryMeta'),
    workersList: document.getElementById('workersList'),

    graphPlayBtn: document.getElementById('graphPlayBtn'),
    graphPlayIcon: document.getElementById('graphPlayIcon'),
    graphPauseIcon: document.getElementById('graphPauseIcon'),
    graphSlider: document.getElementById('graphSlider'),
    graphReadout: document.getElementById('graphReadout'),
    graphHideInactiveToggle: document.getElementById('graphHideInactiveToggle'),
    graphExpandAllToggle: document.getElementById('graphExpandAllToggle'),
    graphCanvasHost: document.getElementById('graphCanvasHost'),
    graphDetailBar: document.getElementById('graphDetailBar'),

    drawerBackdrop: document.getElementById('drawerBackdrop'),
    inspectorDrawer: document.getElementById('inspectorDrawer'),
    inspectorBackground: Array.from(document.querySelectorAll('.mode-bar, .topbar, .main-pane')),
    inspectorTitle: document.getElementById('inspectorTitle'),
    inspectorSubtitle: document.getElementById('inspectorSubtitle'),
    refreshDiffBtn: document.getElementById('refreshDiffBtn'),
    closeDrawerBtn: document.getElementById('closeDrawerBtn'),
    inspectorBody: document.getElementById('inspectorBody'),

    toastContainer: document.getElementById('toastContainer'),
  };

  // ---------- 中文枚举与图标辅助 ----------

  function labelMode(mode) {
    const key = String(mode || '').toLowerCase();
    return MODE_LABELS[key] || (mode ? String(mode) : '—');
  }

  function labelRunStatus(status) {
    const key = String(status || '').toLowerCase();
    return RUN_STATUS_LABELS[key] || (status ? String(status) : '—');
  }

  function labelWorkerStatus(status) {
    const key = String(status || '').toLowerCase();
    return WORKER_STATUS_LABELS[key] || (status ? String(status) : '状态未记录');
  }

  function labelRole(role) {
    const key = String(role || '').toLowerCase();
    return ROLE_LABELS[key] || (role && key !== 'unknown' ? `职责未知（${role}）` : '职责未知');
  }

  function labelChannel(ch) {
    const norm = normalizeChannel(ch);
    const key = norm.toLowerCase();
    return CHANNEL_LABELS[key] || norm;
  }

  function labelMsgType(msgType) {
    const key = String(msgType || 'info').toLowerCase();
    return MSG_TYPE_LABELS[key] || String(msgType || '通知');
  }

  function labelWsMode(mode) {
    const key = String(mode || '').toLowerCase();
    return WS_MODE_LABELS[key] || (mode ? String(mode) : '—');
  }

  function labelWsStatus(status) {
    const key = String(status || '').toLowerCase();
    return WS_STATUS_LABELS[key] || (status ? String(status) : '未知');
  }

  function labelTaskPacketStatus(status) {
    const key = String(status || 'active').toLowerCase();
    return TP_STATUS_LABELS[key] || String(status || '已冻结');
  }

  function getRoleTone(role) {
    const key = String(role || '').toLowerCase();
    switch (key) {
      case 'coordinator':
      case 'human':
        return 'tint-primary';
      case 'verifier':
        return 'tint-success';
      case 'coder':
        return 'tint-warning';
      case 'interviewer':
        return 'tint-violet';
      case 'investigator':
        return 'tint-cyan';
      case 'watchdog':
      case 'system':
        return 'tint-danger';
      case 'workflow':
        return 'tint-cyan';
      default:
        return 'tint-muted';
    }
  }

  function getRoleIconSvg(role) {
    const key = String(role || '').toLowerCase();
    const base = 'viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"';
    switch (key) {
      case 'coordinator':
        return `<svg ${base}><circle cx="12" cy="12" r="3"></circle><path d="M12 2v4M12 18v4M4.93 4.93l2.83 2.83M16.24 16.24l2.83 2.83M2 12h4M18 12h4M4.93 19.07l2.83-2.83M16.24 7.76l2.83-2.83"></path></svg>`;
      case 'interviewer':
        return `<svg ${base}><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"></path><path d="M8 9h8M8 13h5"></path></svg>`;
      case 'planner':
        return `<svg ${base}><rect x="4" y="3" width="16" height="18" rx="2"></rect><path d="M8 8h8M8 12h8M8 16h5"></path></svg>`;
      case 'investigator':
        return `<svg ${base}><circle cx="11" cy="11" r="7"></circle><path d="m20 20-3.5-3.5"></path></svg>`;
      case 'coder':
        return `<svg ${base}><polyline points="16 18 22 12 16 6"></polyline><polyline points="8 6 2 12 8 18"></polyline></svg>`;
      case 'verifier':
        return `<svg ${base}><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"></path><path d="m9 12 2 2 4-4"></path></svg>`;
      case 'swarm_worker':
        return `<svg ${base}><polygon points="12 2 20 7 20 17 12 22 4 17 4 7 12 2"></polygon><circle cx="12" cy="12" r="2.5"></circle></svg>`;
      case 'human':
        return `<svg ${base}><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"></path><circle cx="12" cy="7" r="4"></circle></svg>`;
      case 'watchdog':
      case 'system':
        return `<svg ${base}><polyline points="22 12 18 12 15 21 9 3 6 12 2 12"></polyline></svg>`;
      case 'workflow':
        return `<svg ${base}><path d="M6 3v12"></path><circle cx="18" cy="6" r="3"></circle><circle cx="6" cy="18" r="3"></circle><path d="M18 9a9 9 0 0 1-9 9"></path></svg>`;
      default:
        return `<svg ${base}><circle cx="12" cy="12" r="8"></circle><path d="M12 8v4l3 2"></path></svg>`;
    }
  }

  function renderRoleBadge(role, large = false) {
    const tone = getRoleTone(role);
    const sizeCls = large ? 'role-icon-badge role-icon-badge-lg' : 'role-icon-badge';
    return `<span class="${sizeCls} ${tone}" aria-hidden="true">${getRoleIconSvg(role)}</span>`;
  }

  function getWorkerStatusTone(status, stalled) {
    if (stalled) return 'tint-danger';
    const key = String(status || 'waiting').toLowerCase();
    if (key === 'working') return 'tint-warning';
    if (key === 'waiting') return 'tint-primary';
    if (key === 'killed') return 'tint-danger';
    return 'tint-muted';
  }

  function getWsStatusTone(status) {
    const key = String(status || 'active').toLowerCase();
    if (key === 'verified') return 'tint-success';
    if (key === 'merged') return 'tint-cyan';
    if (key === 'conflict') return 'tint-danger';
    if (key === 'active') return 'tint-primary';
    return 'tint-muted';
  }

  function getMsgTypeTone(msgType) {
    const key = String(msgType || 'info').toLowerCase();
    if (key === 'alert') return 'tint-danger';
    if (key === 'test') return 'tint-success';
    if (key === 'diff') return 'tint-violet';
    if (key === 'handoff') return 'tint-warning';
    if (key === 'human') return 'tint-primary';
    return 'tint-muted';
  }

  // ---------- 工具函数 ----------

  function escapeHtml(value) {
    if (value === null || value === undefined) return '';
    return String(value)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  function safeJsonParse(value, fallback) {
    if (value === null || value === undefined || value === '') return fallback;
    if (typeof value !== 'string') return value;
    try {
      return JSON.parse(value);
    } catch {
      return fallback;
    }
  }

  function parseTimestampMs(raw) {
    if (raw === null || raw === undefined || raw === '') return null;
    if (typeof raw === 'number' && Number.isFinite(raw)) {
      return raw < 1e12 ? raw * 1000 : raw;
    }
    const str = String(raw).trim();
    if (!str) return null;
    if (/^\d+(\.\d+)?$/.test(str)) {
      const num = Number(str);
      return num < 1e12 ? num * 1000 : num;
    }
    if (/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(\.\d+)?$/.test(str)) {
      const parsed = Date.parse(str.replace(' ', 'T') + 'Z');
      return Number.isNaN(parsed) ? null : parsed;
    }
    if (/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?$/.test(str)) {
      const parsed = Date.parse(str + 'Z');
      return Number.isNaN(parsed) ? null : parsed;
    }
    const parsed = Date.parse(str);
    return Number.isNaN(parsed) ? null : parsed;
  }

  function getHeartbeatAgeSec(rawTimestamp) {
    const ms = parseTimestampMs(rawTimestamp);
    if (ms === null) return null;
    return Math.max(0, Math.floor((Date.now() - ms) / 1000));
  }

  function formatAge(ageSec) {
    if (ageSec === null || ageSec === undefined) return '无心跳';
    if (ageSec < 3) return '刚刚';
    if (ageSec < 60) return `${ageSec}秒前`;
    if (ageSec < 3600) {
      const m = Math.floor(ageSec / 60);
      return `${m}分钟前`;
    }
    const h = Math.floor(ageSec / 3600);
    const m = Math.floor((ageSec % 3600) / 60);
    return m > 0 ? `${h}小时${m}分前` : `${h}小时前`;
  }

  function formatLocalTime(raw) {
    const ms = parseTimestampMs(raw);
    if (ms === null) return String(raw || '');
    const d = new Date(ms);
    return d.toLocaleTimeString('zh-CN', {
      hour12: false,
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
    });
  }

  function shortId(id) {
    if (!id) return '';
    const s = String(id);
    return s.length > 12 ? s.slice(0, 8) : s;
  }

  function normalizeChannel(ch) {
    const val = String(ch || 'main').trim();
    return val || 'main';
  }

  function normalizeHandle(name) {
    return String(name || '').trim().replace(/^@+/, '');
  }

  function normalizeMentions(msg) {
    const raw = msg.mentions !== undefined ? msg.mentions : msg.mentions_json;
    const parsed = safeJsonParse(raw, []);
    if (!Array.isArray(parsed)) return [];
    return parsed.map((m) => normalizeHandle(m)).filter(Boolean);
  }

  function inferSenderRole(msg) {
    if (msg.sender_role) return String(msg.sender_role).toLowerCase();
    const sender = normalizeHandle(msg.sender).toLowerCase();
    const worker = state.workers.find((w) => w.name && w.name.toLowerCase() === sender);
    return worker?.role ? String(worker.role).toLowerCase() : 'unknown';
  }


  function isAppServerAgentId(agentId) {
    return typeof agentId === 'string' && agentId.startsWith('appserver://');
  }

  function isAppServerStep(step) {
    if (!step || typeof step !== 'object') return false;
    const execKind = step.execution_kind || step.checkpoint?.execution_kind || '';
    if (execKind === 'app_server_pipeline') return true;
    if (execKind === 'native' || execKind === 'coordinator_local') {
      return isAppServerAgentId(step.owner_agent_id) || isAppServerAgentId(step.verifier_agent_id);
    }
    return isAppServerAgentId(step.owner_agent_id) || isAppServerAgentId(step.verifier_agent_id) ||
      isAppServerAgentId(step.checkpoint?.original_bindings?.owner_agent_id) ||
      isAppServerAgentId(step.checkpoint?.original_bindings?.verifier_agent_id);
  }

  function isAppServerWorker(worker) {
    return Boolean(worker && isAppServerAgentId(worker.agent_id));
  }

  function renderAppServerBadge() {
    return '<span class="pill-badge badge-appserver" title="由宿主终端 tri-orchestrator-pipeline 驱动的独立 App-Server 管线线程（appserver://），非聊天内原生 V2 子代理（/root/...）">App-Server 管线</span>';
  }

  function isWorkerStalled(worker) {
    if (!worker || worker.status !== 'working' || state.run?.status !== 'active') return false;
    const threshold = Number(worker.stall_threshold_sec || 300);
    const age = getHeartbeatAgeSec(worker.last_heartbeat_at);
    return age !== null && age > threshold;
  }

  function getWorkerWorkspace(worker) {
    if (!worker) return null;
    if (worker.workspace_id) {
      const byId = state.workspaces.find((ws) => String(ws.id) === String(worker.workspace_id));
      if (byId) return byId;
    }
    return state.workspaces.find((ws) => ws.worker_name && ws.worker_name === worker.name) || null;
  }

  function getWorkspaceUnmergedCount(ws, worker) {
    if (!ws) return null;
    const cachedDiff = state.diffByWorkspace.get(ws.id);
    if (cachedDiff) {
      const files = extractStatusFiles(cachedDiff);
      if (typeof cachedDiff.unmerged_count === 'number') return cachedDiff.unmerged_count;
      if (typeof cachedDiff.dirty_count === 'number') return cachedDiff.dirty_count;
      return files.length;
    }
    if (worker && typeof worker.unmerged_files_count === 'number') {
      return worker.unmerged_files_count;
    }
    if (typeof ws.unmerged_files_count === 'number') return ws.unmerged_files_count;
    if (typeof ws.unmerged_count === 'number') return ws.unmerged_count;
    if (typeof ws.changed_files_count === 'number') return ws.changed_files_count;
    return null;
  }

  function extractStatusFiles(source) {
    if (!source) return [];
    const raw =
      source.status_files ??
      source.git_status ??
      source.changed_files ??
      source.files ??
      [];
    const list = safeJsonParse(raw, []);
    if (!Array.isArray(list)) return [];
    return list
      .map((item) => {
        if (typeof item === 'string') {
          const trimmed = item.trimEnd();
          const m = trimmed.match(/^([ MADRCU?!]{1,2})\s+(.+)$/);
          if (m) return { code: m[1].trim() || 'M', path: m[2] };
          return { code: 'M', path: trimmed };
        }
        if (item && typeof item === 'object') {
          return {
            code: String(item.code || item.status || 'M').trim(),
            path: String(item.path || item.file || item.filename || ''),
          };
        }
        return null;
      })
      .filter((entry) => entry && entry.path);
  }

  function extractDiffText(diffPayload) {
    if (!diffPayload) return '';
    if (typeof diffPayload === 'string') return diffPayload;
    return (
      diffPayload.diff ??
      diffPayload.diff_text ??
      diffPayload.unified_diff ??
      diffPayload.patch ??
      ''
    );
  }

  // ---------- 主题切换 ----------

  function applyTheme(theme) {
    const normalized = theme === 'dark' ? 'dark' : 'light';
    state.theme = normalized;
    document.documentElement.setAttribute('data-theme', normalized);
    try {
      localStorage.setItem(THEME_STORAGE_KEY, normalized);
    } catch {
      // 忽略存储限制
    }
    const isDark = normalized === 'dark';
    el.themeIconSun.classList.toggle('hidden', !isDark);
    el.themeIconMoon.classList.toggle('hidden', isDark);
    el.themeToggleBtn.title = isDark ? '切换浅色主题' : '切换深色主题';
    el.themeToggleBtn.setAttribute('aria-pressed', isDark ? 'true' : 'false');
  }

  function initTheme() {
    let initial = 'light';
    try {
      if (localStorage.getItem(THEME_STORAGE_KEY) === 'dark') {
        initial = 'dark';
      }
    } catch {
      initial = 'light';
    }
    applyTheme(initial);
  }

  // ---------- XSS 安全 Markdown 渲染 ----------

  function renderInlineMarkdown(escapedLine) {
    const codeTokens = [];
    let text = escapedLine.replace(/`([^`\n]+)`/g, (_, code) => {
      const idx = codeTokens.length;
      codeTokens.push(`<code class="inline-code">${code}</code>`);
      return `\u0000ICODE${idx}\u0000`;
    });

    text = text.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, (_, label, url) => {
      return `<a href="${url}" target="_blank" rel="noopener noreferrer">${label}</a>`;
    });

    text = text.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
    text = text.replace(/__(.+?)__/g, '<strong>$1</strong>');
    text = text.replace(/\*([^*\n]+)\*/g, '<em>$1</em>');
    text = text.replace(/~~([^~]+)~~/g, '<del>$1</del>');

    text = text.replace(/(?<![A-Za-z0-9_:/])@([A-Za-z0-9_](?:[A-Za-z0-9_.-]*[A-Za-z0-9_-])?)/g, '<span class="inline-mention">@$1</span>');
    text = text.replace(/(^|[\s(])#(\d+)\b/g, '$1<span class="inline-seq-ref" data-jump-seq="$2" title="跳转至消息 #$2">#$2</span>');

    return text.replace(/\u0000ICODE(\d+)\u0000/g, (_, idx) => codeTokens[Number(idx)] || '');
  }

  function renderMarkdown(rawMarkdown) {
    const source = String(rawMarkdown || '').replace(/\u0000/g, '');
    if (!source.trim()) return '<p class="muted-caption">（无正文内容）</p>';

    const codeBlocks = [];
    const normalized = source.replace(/\r\n?/g, '\n');
    const withPlaceholders = normalized.replace(/```([a-zA-Z0-9_+-]*)[ \t]*\n([\s\S]*?)```/g, (_, lang, code) => {
      const idx = codeBlocks.length;
      const safeLang = lang ? `<span class="code-lang">${escapeHtml(lang)}</span>` : '';
      const safeCode = escapeHtml(code.replace(/\n$/, ''));
      codeBlocks.push(`<pre class="code-block">${safeLang}<code>${safeCode}</code></pre>`);
      return `\n\u0000CBLOCK${idx}\u0000\n`;
    });

    const lines = escapeHtml(withPlaceholders).split('\n');
    const out = [];
    let i = 0;

    while (i < lines.length) {
      const line = lines[i];
      const trimmed = line.trim();

      if (!trimmed) {
        i += 1;
        continue;
      }

      const blockMatch = trimmed.match(/^\u0000CBLOCK(\d+)\u0000$/);
      if (blockMatch) {
        out.push(codeBlocks[Number(blockMatch[1])] || '');
        i += 1;
        continue;
      }

      const headingMatch = trimmed.match(/^(#{1,4})\s+(.+)$/);
      if (headingMatch) {
        const level = Math.min(4, Math.max(1, headingMatch[1].length));
        const tag = level <= 2 ? 'h3' : 'h4';
        out.push(`<${tag}>${renderInlineMarkdown(headingMatch[2])}</${tag}>`);
        i += 1;
        continue;
      }

      if (/^&gt;\s?/.test(trimmed)) {
        const quoteLines = [];
        while (i < lines.length && /^&gt;\s?/.test(lines[i].trim())) {
          quoteLines.push(renderInlineMarkdown(lines[i].trim().replace(/^&gt;\s?/, '')));
          i += 1;
        }
        out.push(`<blockquote>${quoteLines.join('<br>')}</blockquote>`);
        continue;
      }

      if (/^[-*+]\s+/.test(trimmed)) {
        const items = [];
        while (i < lines.length && /^[-*+]\s+/.test(lines[i].trim())) {
          items.push(`<li>${renderInlineMarkdown(lines[i].trim().replace(/^[-*+]\s+/, ''))}</li>`);
          i += 1;
        }
        out.push(`<ul>${items.join('')}</ul>`);
        continue;
      }

      if (/^\d+\.\s+/.test(trimmed)) {
        const items = [];
        while (i < lines.length && /^\d+\.\s+/.test(lines[i].trim())) {
          items.push(`<li>${renderInlineMarkdown(lines[i].trim().replace(/^\d+\.\s+/, ''))}</li>`);
          i += 1;
        }
        out.push(`<ol>${items.join('')}</ol>`);
        continue;
      }

      const paraLines = [];
      while (
        i < lines.length &&
        lines[i].trim() !== '' &&
        !/^\u0000CBLOCK\d+\u0000$/.test(lines[i].trim()) &&
        !/^#{1,4}\s+/.test(lines[i].trim()) &&
        !/^&gt;\s?/.test(lines[i].trim()) &&
        !/^[-*+]\s+/.test(lines[i].trim()) &&
        !/^\d+\.\s+/.test(lines[i].trim())
      ) {
        paraLines.push(renderInlineMarkdown(lines[i]));
        i += 1;
      }
      out.push(`<p>${paraLines.join('<br>')}</p>`);
    }

    return out.join('');
  }

  // ---------- 消息提示 ----------

  function showToast(title, message, variant = 'info', durationMs = 5000) {
    const toast = document.createElement('div');
    toast.className = `toast toast-${variant}`;
    toast.innerHTML = `
      <div class="toast-head">
        <span>${escapeHtml(title)}</span>
        <button type="button" class="toast-close" aria-label="关闭通知">&times;</button>
      </div>
      ${message ? `<div class="toast-body">${escapeHtml(message)}</div>` : ''}
    `;

    const closeBtn = toast.querySelector('.toast-close');
    const removeToast = () => {
      if (toast.parentNode) toast.parentNode.removeChild(toast);
    };
    closeBtn.addEventListener('click', removeToast);
    el.toastContainer.appendChild(toast);

    if (durationMs > 0) {
      setTimeout(removeToast, durationMs);
    }
  }

  // ---------- API 客户端 ----------

  async function apiRequest(url, options = {}) {
    const fetchOpts = {
      headers: {
        'Accept': 'application/json',
        ...(options.body ? { 'Content-Type': 'application/json' } : {}),
        ...(options.headers || {}),
      },
      ...options,
    };

    const res = await fetch(url, fetchOpts);
    const contentType = res.headers.get('content-type') || '';
    let payload = null;
    if (contentType.includes('application/json')) {
      payload = await res.json();
    } else {
      const text = await res.text();
      payload = safeJsonParse(text, { message: text });
    }

    if (!res.ok) {
      const detail =
        (payload && (payload.detail || payload.error || payload.message)) ||
        `HTTP ${res.status} ${res.statusText}`;
      const err = new Error(typeof detail === 'string' ? detail : JSON.stringify(detail));
      err.status = res.status;
      err.payload = payload;
      throw err;
    }

    return payload;
  }

  // ---------- 健康状态与 SSE 指示器 ----------

  function renderHealthPill() {
    const portPart = state.healthPort ? ` :${state.healthPort}` : '';
    const sseTextMap = {
      connected: 'SSE 实时连接中',
      connecting: 'SSE 连接中',
      reconnecting: 'SSE 重连中',
      error: 'SSE 连接断开',
      idle: 'SSE 空闲',
    };
    const sseLabel = sseTextMap[state.sseState] || 'SSE 空闲';

    if (state.healthOk === true) {
      const prefix = state.sseState === 'connected' ? '实时' : '在线';
      const dotClass = state.sseState === 'error' ? 'dot-pending' : 'dot-ok';
      const content = `
        <span class="status-dot ${dotClass}"></span>
        <span class="status-text tabular">${escapeHtml(`${prefix}${portPart}`)}</span>
      `;
      if (el.healthBadge.innerHTML !== content) el.healthBadge.innerHTML = content;
      const pidInfo = state.healthPid ? ` · PID ${state.healthPid}` : '';
      el.healthBadge.title = `守护进程运行中${portPart}${pidInfo} · ${sseLabel}`;
    } else if (state.healthOk === false) {
      const content = `
        <span class="status-dot dot-error"></span>
        <span class="status-text">守护进程离线</span>
      `;
      if (el.healthBadge.innerHTML !== content) el.healthBadge.innerHTML = content;
      el.healthBadge.title = '无法连接守护进程 (/api/health)';
    } else {
      const content = `
        <span class="status-dot dot-pending"></span>
        <span class="status-text">检测中</span>
      `;
      if (el.healthBadge.innerHTML !== content) el.healthBadge.innerHTML = content;
      el.healthBadge.title = '正在检测守护进程状态...';
    }
  }

  function setSseBadge(mode, label) {
    state.sseState = mode;
    const dotMap = {
      connected: 'dot-ok',
      connecting: 'dot-pending',
      reconnecting: 'dot-pending',
      error: 'dot-error',
      idle: 'dot-muted',
    };
    const clsMap = {
      connected: 'sse-live',
      connecting: 'sse-pending',
      reconnecting: 'sse-pending',
      error: 'sse-error',
      idle: '',
    };
    const dotClass = dotMap[mode] || 'dot-muted';
    el.sseBadge.className = `sse-indicator ${clsMap[mode] || ''}`.trim();
    const content = `
      <span class="status-dot ${dotClass}"></span>
      <span class="status-text">${escapeHtml(label)}</span>
    `;
    if (el.sseBadge.innerHTML !== content) el.sseBadge.innerHTML = content;
    renderHealthPill();
  }

  async function checkHealth() {
    try {
      const data = await apiRequest('/api/health');
      const ok = data?.status ? data.status === 'ok' || data.ok === true : true;
      state.healthOk = ok;
      state.healthPort = data?.port || null;
      state.healthPid = data?.pid || null;
      renderHealthPill();
    } catch {
      state.healthOk = false;
      renderHealthPill();
    }
  }

  // ---------- 数据加载与水合 ----------

  function syncUrlParams() {
    const params = new URLSearchParams(window.location.search);
    if (state.selectedProjectId) params.set('project_id', state.selectedProjectId);
    else params.delete('project_id');
    if (state.selectedRunId) params.set('run_id', state.selectedRunId);
    else params.delete('run_id');
    const qs = params.toString();
    const target = qs ? `${window.location.pathname}?${qs}` : window.location.pathname;
    window.history.replaceState(null, '', target);
    renderModeLinks();
  }

  function renderModeLinks() {
    const projectId = state.selectedProjectId || new URLSearchParams(window.location.search).get('project_id');
    const query = projectId ? `?${new URLSearchParams({ project_id: projectId })}` : '';
    for (const link of el.modeLinks) {
      const mode = link.dataset.modeLink;
      link.setAttribute('href', `/${mode}${query}`);
      if (mode === PAGE_MODE) link.setAttribute('aria-current', 'page');
      else link.removeAttribute('aria-current');
    }
  }

  function validateRunContext(run, runId = null, projectId = null, mode = PAGE_MODE) {
    if (!run?.id || !run.project_id || !Object.hasOwn(MODE_LABELS, run.mode)) {
      throw new Error('任务元数据不完整或模式无效');
    }
    if (runId && String(run.id) !== String(runId)) throw new Error('返回的任务与所选任务不匹配');
    if (mode && run.mode !== mode) throw new Error(`任务属于 ${MODE_NAMES[run.mode]}，请从对应入口打开`);
    if (projectId && String(run.project_id) !== String(projectId)) throw new Error('所选任务不属于该项目');
  }

  async function resolveRun(runId, projectId = null, mode = PAGE_MODE) {
    const version = state.contextVersion;
    const data = await apiRequest(`/api/runs/${encodeURIComponent(runId)}`);
    if (version !== state.contextVersion) return null;
    validateRunContext(data.run, runId, projectId, mode);
    if (String(data.project?.id) !== String(data.run.project_id)) throw new Error('任务与项目元数据不匹配');
    return data.run;
  }

  async function loadProjects() {
    const params = new URLSearchParams(window.location.search);
    const requestedProject = state.selectedProjectId || params.get('project_id');
    const requestedRun = state.selectedRunId || params.get('run_id');
    state.selectedProjectId = requestedProject || null;
    state.selectedRunId = requestedRun || null;
    state.runs = [];
    clearRunState();
    const version = state.contextVersion;
    state.loadState = 'loading';
    renderRunSelector();
    renderAll();
    try {
      const data = await apiRequest('/api/projects');
      if (version !== state.contextVersion) return;
      state.projects = Array.isArray(data) ? data : data.projects;
      renderProjectSelector();
      let projectId = requestedProject;
      let runId = requestedRun;
      let exactRun = null;
      if (requestedRun) {
        exactRun = await resolveRun(requestedRun, requestedProject);
        if (!exactRun) return;
        projectId = exactRun.project_id;
      } else if (!projectId) {
        const result = await apiRequest(`/api/runs?status=active&mode=${PAGE_MODE}`);
        if (version !== state.contextVersion) return;
        const runs = Array.isArray(result) ? result : result.runs;
        runs.forEach((run) => validateRunContext(run));
        let preferred = runs[0];
        if (!preferred) {
          const recent = await apiRequest(`/api/runs?mode=${PAGE_MODE}`);
          if (version !== state.contextVersion) return;
          const recentRuns = Array.isArray(recent) ? recent : recent.runs;
          recentRuns.forEach((run) => validateRunContext(run));
          preferred = recentRuns[0];
        }
        projectId = preferred?.project_id || state.projects[0]?.id || null;
        runId = preferred?.id || null;
      }
      if (projectId && !state.projects.some((p) => String(p.id) === String(projectId))) {
        throw new Error('所选项目不存在，请重新选择项目');
      }
      await loadRuns(projectId, runId, exactRun);
    } catch (err) {
      if (version !== state.contextVersion) return;
      state.loadState = 'error';
      state.loadError = err.message;
      renderProjectSelector();
      renderRunSelector();
      renderAll();
    }
  }


  async function loadRuns(projectId, preferredRunId = null, exactRun = null) {
    state.selectedProjectId = projectId;
    state.selectedRunId = preferredRunId;
    state.runs = [];
    clearRunState();
    const version = state.contextVersion;
    state.loadState = projectId ? 'loading' : 'idle';
    renderProjectSelector();
    renderRunSelector();
    renderAll();
    syncUrlParams();
    if (!projectId) return;
    try {
      const data = await apiRequest(`/api/runs?project_id=${encodeURIComponent(projectId)}&mode=${PAGE_MODE}`);
      if (version !== state.contextVersion) return;
      const runs = Array.isArray(data) ? data : data.runs;
      runs.forEach((run) => validateRunContext(run, null, projectId));
      if (preferredRunId && !runs.some((r) => String(r.id) === String(preferredRunId))) {
        const selectedRun = exactRun || await resolveRun(preferredRunId, projectId);
        if (!selectedRun) return;
        runs.push(selectedRun);
      }
      runs.sort((a, b) => {
        const activeOrder = Number(b.status === 'active') - Number(a.status === 'active');
        return activeOrder || (parseTimestampMs(b.updated_at || b.created_at) || 0) - (parseTimestampMs(a.updated_at || a.created_at) || 0);
      });
      state.runs = runs;
      renderRunSelector();
      const runId = preferredRunId || runs[0]?.id;
      if (runId) await selectRun(runId);
      else { state.loadState = 'idle'; syncUrlParams(); renderRunSelector(); renderAll(); }
    } catch (err) {
      if (version !== state.contextVersion) return;
      state.loadState = 'error';
      state.loadError = err.message;
      renderRunSelector();
      renderAll();
    }
  }


  function clearRunState() {
    state.contextVersion += 1;
    state.loadState = 'idle';
    state.loadError = '';
    state.sending = false;
    state.actionBusy = false;
    state.diffRequests.clear();
    state.diffByWorkspace.clear();
    state.diffErrors.clear();
    state.mergePreviewByWorkspace.clear();
    state.inspectorBannerByWorkspace.clear();
    closeEventSource();
    stopGraphReplay();
    state.run = null;
    state.taskPacket = null;
    state.workflow = null;
    state.inbox = null;
    state.nativeActions = null;
    state.progressStatus = null;
    state.resumeContext = null;
    state.planReadiness = null;
    state.workers = [];
    state.controlRequests = [];
    state.workspaces = [];
    state.messages = [];
    state.channels = new Set(['main', 'human']);
    state.selectedWorkerId = null;
    state.graphStep = null;
    state.graphSelectedNode = null;
    updateTopRunControls();
    renderAll();
  }

  async function selectRun(runId) {
    if (String(state.selectedRunId) !== String(runId)) {
      state.expandedMessages.clear();
      state.overviewExpanded.clear();
    }
    state.selectedRunId = runId;
    clearRunState();
    const version = state.contextVersion;
    state.loadState = 'loading';
    syncUrlParams();
    renderRunSelector();
    renderAll();
    if (await refreshSnapshot(runId)) {
      if (version === state.contextVersion) connectEventSource(runId);
    }
  }


  async function refreshSnapshot(runId, opts = {}) {
    if (!runId) return false;
    const version = state.contextVersion;
    const request = ++state.snapshotVersion;
    try {
      const snap = await apiRequest(`/api/runs/${encodeURIComponent(runId)}/snapshot`);
      if (version !== state.contextVersion || request !== state.snapshotVersion) return false;
      validateRunContext(snap.run, runId, state.selectedProjectId);
      applySnapshot(snap);
      state.loadState = 'ready';
      state.loadError = '';
      renderAll();
      return true;
    } catch (err) {
      if (version !== state.contextVersion || request !== state.snapshotVersion) return false;
      clearRunState();
      state.loadState = 'error';
      state.loadError = err.message;
      renderRunSelector();
      renderAll();
      return false;
    }
  }


  function applySnapshot(snap) {
    state.run = snap.run;
    state.workflow = snap.workflow ?? null;
    state.inbox = snap.inbox ?? null;
    state.nativeActions = snap.native_actions ?? null;
    state.progressStatus = snap.progress_status ?? null;
    state.resumeContext = snap.resume_context ?? null;
    state.planReadiness = snap.plan_readiness ?? null;

    if (Array.isArray(snap.task_packets) && snap.task_packets.length > 0) {
      const sorted = [...snap.task_packets].sort((a, b) => Number(b.version || 0) - Number(a.version || 0));
      state.taskPacket = sorted[0];
    } else {
      state.taskPacket = snap.task_packet || null;
    }

    state.controlRequests = Array.isArray(snap.control_requests) ? snap.control_requests : [];
    state.workers = Array.isArray(snap.workers) ? snap.workers : [];
    state.workspaces = Array.isArray(snap.workspaces) ? snap.workspaces : [];

    const rawMessages = Array.isArray(snap.messages)
      ? snap.messages
      : (Array.isArray(snap.board_messages) ? snap.board_messages : []);
    const bySeq = new Map();
    for (const msg of rawMessages) {
      if (msg && msg.seq !== undefined) {
        bySeq.set(Number(msg.seq), msg);
      }
    }
    state.messages = Array.from(bySeq.values()).sort((a, b) => Number(a.seq) - Number(b.seq));

    const chSet = new Set(['main', 'human']);
    if (Array.isArray(snap.channels)) {
      snap.channels.forEach((c) => chSet.add(normalizeChannel(c)));
    }
    for (const msg of state.messages) {
      chSet.add(normalizeChannel(msg.channel));
    }
    state.channels = chSet;

    if ((state.selectedWorkerId === null || state.selectedWorkerId === undefined) && state.workers.length > 0) {
      state.selectedWorkerId = state.workers[0].id;
    } else if (state.selectedWorkerId !== null && state.selectedWorkerId !== undefined) {
      const stillExists = state.workers.some((w) => String(w.id) === String(state.selectedWorkerId));
      if (!stillExists) {
        state.selectedWorkerId = state.workers.length > 0 ? state.workers[0].id : null;
      }
    }
  }

  // ---------- SSE 实时事件流 ----------

  function closeEventSource() {
    if (state.eventSource) {
      state.eventSource.close();
      state.eventSource = null;
    }
    state.sseRunId = null;
    state.sseHadError = false;
    setSseBadge('idle', 'SSE 空闲');
  }

  function connectEventSource(runId) {
    closeEventSource();
    if (!runId) return;

    state.sseRunId = runId;
    state.sseHadError = false;
    setSseBadge('connecting', '连接实时流...');

    const version = state.contextVersion;
    const es = new EventSource(`/api/events?run_id=${encodeURIComponent(runId)}`);
    state.eventSource = es;

    const ownsStream = () => state.eventSource === es && version === state.contextVersion;
    es.onopen = () => {
      if (!ownsStream()) return;
      const wasReconnect = state.sseHadError;
      state.sseHadError = false;
      setSseBadge('connected', '实时流');
      if (wasReconnect) {
        refreshSnapshot(runId, { silent: true });
      }
    };

    es.onerror = () => {
      if (!ownsStream()) return;
      state.sseHadError = true;
      if (es.readyState === EventSource.CONNECTING) {
        setSseBadge('reconnecting', '重连中...');
      } else {
        setSseBadge('error', '连接断开');
      }
    };

    const handleEvent = (eventType, rawData) => {
      if (!ownsStream()) return;
      const payload = safeJsonParse(rawData, null);
      if (!payload || typeof payload !== 'object') return;
      const data = payload.data && typeof payload.data === 'object' ? payload.data : payload;
      if (data.run_id && String(data.run_id) !== String(state.selectedRunId)) return;

      switch (eventType) {
        case 'board_message':
          upsertBoardMessage(data.message || data);
          break;
        case 'worker_control_updated':
          upsertControlRequest(data.control_request);
          break;
        case 'worker_updated':
          upsertWorker(data.worker || data);
          break;
        case 'workspace_updated':
          upsertWorkspace(data.workspace || data);
          break;
        case 'run_updated':
          applyRunUpdate(data.run || data);
          break;
        case 'task_packet_updated':
          state.taskPacket = data.task_packet || data;
          renderTaskPacket();
          renderOverview();
          renderGraph();
          break;
        case 'workflow_updated':
        case 'inbox_updated':
        case 'native_action_updated':
          refreshSnapshot(runId, { silent: true });
          break;
        case 'stall_alert':
          handleStallAlert(data);
          break;
        default:
          break;
      }
    };

    const namedEvents = [
      'board_message',
      'worker_updated',
      'worker_control_updated',
      'workspace_updated',
      'run_updated',
      'task_packet_updated',
      'stall_alert',
      'workflow_updated',
      'inbox_updated',
      'native_action_updated',
    ];
    for (const evtName of namedEvents) {
      es.addEventListener(evtName, (evt) => handleEvent(evtName, evt.data));
    }

    es.onmessage = (evt) => {
      const parsed = safeJsonParse(evt.data, null);
      if (!parsed || typeof parsed !== 'object') return;
      const evtType = parsed.event || parsed.type;
      if (evtType) {
        handleEvent(evtType, parsed.data !== undefined ? parsed.data : parsed);
      }
    };
  }

  function upsertBoardMessage(msg) {
    if (!msg || msg.seq === undefined) return;
    const seqNum = Number(msg.seq);
    const idx = state.messages.findIndex((m) => Number(m.seq) === seqNum);
    if (idx >= 0) {
      state.messages[idx] = msg;
    } else {
      state.messages.push(msg);
      state.messages.sort((a, b) => Number(a.seq) - Number(b.seq));
    }

    const ch = normalizeChannel(msg.channel);
    if (!state.channels.has(ch)) {
      state.channels.add(ch);
      renderChannelControls();
    } else {
      renderChannelTabsOnly();
    }
    renderMentionFilterOptions();
    renderBoardFeed({ isLiveAppend: idx < 0 });
    renderTabCounts();
    renderOverview();
    renderGraph();
  }

  function upsertWorker(worker) {
    if (!worker) return;
    const idx = state.workers.findIndex(
      (w) => (worker.id && String(w.id) === String(worker.id)) || (worker.name && w.name === worker.name)
    );
    if (idx >= 0) {
      state.workers[idx] = { ...state.workers[idx], ...worker };
    } else {
      state.workers.push(worker);
    }
    renderWorkers();
    renderTaskPacket();
    renderMentionFilterOptions();
    renderTabCounts();
    renderOverview();
    renderGraph();
    if (String(state.selectedWorkerId) === String(worker.id)) {
      renderInspector();
    }
  }

  function upsertWorkspace(ws) {
    if (!ws || !ws.id) return;
    const idx = state.workspaces.findIndex((item) => String(item.id) === String(ws.id));
    if (idx >= 0) {
      state.workspaces[idx] = { ...state.workspaces[idx], ...ws };
    } else {
      state.workspaces.push(ws);
    }
    renderWorkers();

    const selectedWorker = state.workers.find((w) => String(w.id) === String(state.selectedWorkerId));
    const selectedWs = getWorkerWorkspace(selectedWorker);
    if (selectedWs && String(selectedWs.id) === String(ws.id)) {
      renderInspector();
      if (state.inspectorOpen && ws.status !== 'cleaned') {
        loadWorkspaceDiff(ws.id, { silent: true });
      }
    }
  }

  function applyRunUpdate(updatedRun) {
    try {
      validateRunContext(updatedRun, state.selectedRunId, state.selectedProjectId);
    } catch (err) {
      state.runs = [];
      clearRunState();
      state.loadState = 'error';
      state.loadError = err.message;
      renderRunSelector();
      renderAll();
      return;
    }
    state.run = { ...(state.run || {}), ...updatedRun };
    const idx = state.runs.findIndex((r) => String(r.id) === String(updatedRun.id));
    if (idx >= 0) {
      state.runs[idx] = { ...state.runs[idx], ...updatedRun };
    }
    renderRunSelector();
    updateTopRunControls();
    renderOverview();
  }

  function handleStallAlert(data) {
    const worker = data.worker || null;
    if (worker) {
      upsertWorker({ ...worker, stalled_alerted: 1 });
    } else if (data.worker_id || data.worker_name) {
      const target = state.workers.find(
        (w) =>
          (data.worker_id && String(w.id) === String(data.worker_id)) ||
          (data.worker_name && w.name === data.worker_name)
      );
      if (target) {
        upsertWorker({ ...target, stalled_alerted: 1 });
      }
    }
    if (data.message) {
      upsertBoardMessage(data.message);
    }
    const label = data.worker_name || worker?.name || '成员';
    showToast('成员心跳告警', `@${label} 心跳已超过停滞阈值，请检查工作状态。`, 'warning', 7000);
  }

  // ---------- 顶部栏与页签渲染 ----------

  function renderProjectSelector() {
    el.projectSelect.disabled = state.projects.length === 0;
    if (state.projects.length === 0) {
      el.projectSelect.innerHTML = `<option value="">${state.loadState === 'error' ? '项目未加载' : '暂无已注册项目'}</option>`;
      el.projectPathSubtitle.textContent = '未选择项目';
      el.projectPathSubtitle.title = '';
      return;
    }

    const current = state.projects.find((p) => String(p.id) === String(state.selectedProjectId));
    el.projectSelect.innerHTML = (current ? '' : '<option value="">请选择项目</option>') + state.projects
      .map((p) => {
        const selected = String(p.id) === String(state.selectedProjectId) ? 'selected' : '';
        const kind = p.is_git ? 'Git' : '目录';
        const label = `${p.name || p.root_path} [${kind}]`;
        return `<option value="${escapeHtml(p.id)}" ${selected} title="${escapeHtml(p.root_path)}">${escapeHtml(label)}</option>`;
      })
      .join('');

    el.projectPathSubtitle.textContent = current ? current.root_path : '未选择项目';
    el.projectPathSubtitle.title = current ? current.root_path : '';
  }

  function renderRunSelector() {
    el.runSelect.disabled = state.runs.length === 0;
    if (state.runs.length === 0) {
      const label = state.loadState === 'loading' ? '正在加载任务…' : state.loadState === 'error' ? '任务未加载' : `该项目暂无 ${MODE_NAMES[PAGE_MODE]} 任务`;
      el.runSelect.innerHTML = `<option value="">${escapeHtml(label)}</option>`;
      return;
    }

    el.runSelect.innerHTML = state.runs
      .map((r) => {
        const selected = String(r.id) === String(state.selectedRunId) ? 'selected' : '';
        const statusLabel = labelRunStatus(r.status);
        const title = r.title || `任务 ${shortId(r.id)}`;
        return `<option value="${escapeHtml(r.id)}" ${selected}>[${escapeHtml(statusLabel)}] ${escapeHtml(title)}</option>`;
      })
      .join('');
  }

  function updateTopRunControls() {
    const run = state.run || state.runs.find((r) => String(r.id) === String(state.selectedRunId));
    el.contextStatus.textContent = state.loadState === 'error' ? `加载失败：${state.loadError}。可切换项目或入口，也可点击「立即同步」重试。` : state.loadState === 'loading' ? '正在加载所选任务…' : '';
    el.contextStatus.classList.toggle('hidden', !el.contextStatus.textContent);
    el.contextStatus.classList.toggle('is-error', state.loadState === 'error');
    if (!run) {
      el.modeBadge.className = 'run-pill-mode badge-mode-none';
      el.modeBadge.textContent = '—';
      el.runStatusBadge.className = 'run-pill-status';
      el.runStatusBadge.textContent = '—';
      el.runTitleHeading.textContent = '未选择运行任务';
      el.runTitleHeading.title = '';
      el.exportMenuBtn.disabled = true;
      el.exportMdBtn.disabled = true;
      el.exportJsonBtn.disabled = true;
      el.humanSendBtn.disabled = true;
      closeExportMenu();
      return;
    }

    const mode = String(run.mode || 'unknown').toLowerCase();
    const status = String(run.status || 'unknown').toLowerCase();
    el.modeBadge.className = `run-pill-mode mode-${escapeHtml(mode)}`;
    el.modeBadge.textContent = labelMode(mode);

    el.runStatusBadge.className = `run-pill-status status-${escapeHtml(status)}`;
    el.runStatusBadge.textContent = labelRunStatus(status);

    const runTitle = run.title || `任务 ${shortId(run.id)}`;
    el.runTitleHeading.textContent = runTitle;
    el.runTitleHeading.title = `${runTitle} (${run.id})`;

    const ready = state.loadState === 'ready';
    el.exportMenuBtn.disabled = !ready;
    el.exportMdBtn.disabled = !ready;
    el.exportJsonBtn.disabled = !ready;
    el.humanSendBtn.disabled = !ready || state.sending;
  }

  function renderTabCounts() {
    el.tabBoardCount.textContent = String(state.messages.length);
    el.tabMembersCount.textContent = String(state.workers.length);
  }

  function switchTab(tabName) {
    if (!el.tabPanels[tabName]) return;
    state.activeTab = tabName;
    for (const btn of el.tabButtons) {
      const isActive = btn.getAttribute('data-tab') === tabName;
      btn.classList.toggle('active', isActive);
      btn.setAttribute('aria-selected', isActive ? 'true' : 'false');
      btn.setAttribute('tabindex', isActive ? '0' : '-1');
    }
    for (const [key, panel] of Object.entries(el.tabPanels)) {
      panel.classList.toggle('hidden', key !== tabName);
    }

    if (tabName !== 'graph') stopGraphReplay();
    if (tabName === 'board' && state.autoScroll) {
      scrollFeedToBottom();
    } else if (tabName === 'graph') {
      renderGraph();
    }
    el.tabButtons.find((btn) => btn.getAttribute('data-tab') === tabName)?.focus();
  }

  function syncInspectorMode() {
    const modal = state.inspectorOpen && window.innerWidth <= 1080;
    for (const background of el.inspectorBackground) background.inert = modal;
    if (modal) {
      el.inspectorDrawer.setAttribute('role', 'dialog');
      el.inspectorDrawer.setAttribute('aria-modal', 'true');
      if (!el.inspectorDrawer.contains(document.activeElement)) el.inspectorTitle.focus();
    } else {
      el.inspectorDrawer.setAttribute('role', 'complementary');
      el.inspectorDrawer.removeAttribute('aria-modal');
    }
    el.drawerBackdrop.classList.toggle('hidden', !modal);
  }

  function setInspectorOpen(open) {
    const wasOpen = state.inspectorOpen;
    if (open && !wasOpen) {
      const trigger = document.activeElement;
      state.inspectorReturnFocus = { trigger, workerId: trigger?.closest('[data-worker-id]')?.getAttribute('data-worker-id') };
    }
    state.inspectorOpen = Boolean(open);
    el.inspectorDrawer.classList.toggle('hidden', !state.inspectorOpen);
    el.toggleInspectorBtn.classList.toggle('active', state.inspectorOpen);
    el.toggleInspectorBtn.setAttribute('aria-expanded', state.inspectorOpen ? 'true' : 'false');
    syncInspectorMode();
    if (open && !wasOpen) el.inspectorTitle.focus();
    if (!open && wasOpen) {
      const { trigger, workerId } = state.inspectorReturnFocus;
      let target = trigger?.isConnected ? trigger : workerId ? el.workersList.querySelector(`[data-worker-id="${CSS.escape(workerId)}"]`) : null;
      if (!target || target.closest('.hidden')) target = el.toggleInspectorBtn;
      target.focus();
      state.inspectorReturnFocus = null;
    }
  }

  // ---------- 页签 2：任务包、文件所有权矩阵与成员列表 ----------

  function lookupOwnerRoleLabel(ownerName) {
    const clean = normalizeHandle(ownerName);
    const lower = clean.toLowerCase();
    if (!clean) return '—';
    if (lower === 'shared') return '共享范围';
    if (lower === 'unassigned') return '未分配';
    const worker = state.workers.find((w) => w.name && w.name.toLowerCase() === lower);
    if (worker && worker.role) return labelRole(worker.role);
    return '职责未知';
  }

  function renderTaskPacket() {
    const tp = state.taskPacket;
    if (!tp) {
      el.taskPacketHeading.textContent = '任务包';
      el.taskPacketMeta.innerHTML = '';
      el.taskPacketCriteriaCount.textContent = '';
      el.taskPacketBody.innerHTML = '<div class="empty-state">当前运行任务尚未冻结任务包，选择已有任务包或等待主协调者下发。</div>';
      return;
    }

    const version = tp.version ? `v${tp.version}` : 'v1';
    const status = String(tp.status || 'active').toLowerCase();
    const statusTone = status === 'active' ? 'tint-success' : 'tint-muted';

    el.taskPacketHeading.textContent = tp.title ? `任务包 · ${tp.title}` : '任务包';
    el.taskPacketMeta.innerHTML = `
      <span class="pill-badge tint-muted mono">${escapeHtml(version)}</span>
      <span class="pill-badge ${statusTone}">${escapeHtml(labelTaskPacketStatus(status))}</span>
    `;

    const ownershipRaw = tp.file_ownership !== undefined ? tp.file_ownership : tp.file_ownership_json;
    const ownership = safeJsonParse(ownershipRaw, null);
    const criteriaRaw = tp.acceptance_criteria !== undefined ? tp.acceptance_criteria : tp.acceptance_criteria_json;
    const criteria = safeJsonParse(criteriaRaw, []);

    const criteriaLen = Array.isArray(criteria) ? criteria.length : (criteria ? 1 : 0);
    el.taskPacketCriteriaCount.textContent = criteriaLen > 0 ? `验收标准 ${criteriaLen} 项` : '';

    const ownershipHtml = renderFileOwnershipMatrix(ownership);
    const criteriaHtml = renderAcceptanceCriteria(criteria);

    el.taskPacketBody.innerHTML = `
      <div class="tp-two-col">
        <div>
          <div class="tp-field-label">核心目标</div>
          <p class="tp-goal-text">${escapeHtml(tp.goal || '（暂未填写核心目标）')}</p>
        </div>
        <div>
          <div class="tp-field-label">验收标准</div>
          ${criteriaHtml}
        </div>
      </div>
      <div>
        <div class="tp-field-label">文件所有权矩阵</div>
        ${ownershipHtml}
      </div>
    `;
  }

  function renderFileOwnershipMatrix(ownership) {
    if (!ownership) {
      return '<div class="muted-caption">暂未登记文件所有权矩阵。</div>';
    }

    if (typeof ownership === 'string') {
      return `<p class="tp-goal-text">${escapeHtml(ownership)}</p>`;
    }

    const rows = [];
    if (Array.isArray(ownership)) {
      for (const item of ownership) {
        if (typeof item === 'string') {
          rows.push({ owner: 'shared', roleLabel: '共享范围', files: [item] });
        } else if (item && typeof item === 'object') {
          const owner = item.owner || item.worker || item.agent || item.role || 'unassigned';
          const roleLabel = item.role && item.role !== owner ? labelRole(item.role) : lookupOwnerRoleLabel(owner);
          const filesVal = item.files || item.paths || item.patterns || (item.path ? [item.path] : []);
          const files = Array.isArray(filesVal) ? filesVal : [filesVal];
          rows.push({ owner, roleLabel, files });
        }
      }
    } else if (typeof ownership === 'object') {
      for (const [key, val] of Object.entries(ownership)) {
        if (val && typeof val === 'object' && !Array.isArray(val)) {
          const filesVal = val.files || val.paths || val.patterns || (val.path ? [val.path] : []);
          const files = Array.isArray(filesVal) ? filesVal : [filesVal];
          const roleLabel = val.role ? labelRole(val.role) : lookupOwnerRoleLabel(key);
          rows.push({ owner: key, roleLabel, files });
        } else {
          const files = Array.isArray(val) ? val : [val];
          rows.push({ owner: key, roleLabel: lookupOwnerRoleLabel(key), files });
        }
      }
    }

    if (rows.length === 0) {
      return '<div class="muted-caption">文件所有权矩阵为空。</div>';
    }

    return `
      <div class="ownership-table-wrap">
        <table class="ownership-table">
          <thead>
            <tr>
              <th class="ownership-col-member">成员</th>
              <th class="ownership-col-role">职责</th>
              <th>独占路径范围</th>
            </tr>
          </thead>
          <tbody>
            ${rows
              .map((r) => {
                const cleanOwner = normalizeHandle(r.owner);
                const ownerDisplay = cleanOwner && !['shared', 'unassigned'].includes(cleanOwner.toLowerCase())
                  ? `@${cleanOwner}`
                  : (cleanOwner.toLowerCase() === 'shared' ? '共享' : '未分配');
                return `
                  <tr>
                    <td class="ownership-col-member mono">${escapeHtml(ownerDisplay)}</td>
                    <td class="ownership-col-role">${escapeHtml(r.roleLabel)}</td>
                    <td>
                      <div class="ownership-paths">
                        ${r.files
                          .map((f) => `<span class="path-chip">${escapeHtml(typeof f === 'object' ? JSON.stringify(f) : f)}</span>`)
                          .join('')}
                      </div>
                    </td>
                  </tr>
                `;
              })
              .join('')}
          </tbody>
        </table>
      </div>
    `;
  }

  function renderAcceptanceCriteria(criteria) {
    if (!criteria || (Array.isArray(criteria) && criteria.length === 0)) {
      return '<div class="muted-caption">暂未设置验收标准。</div>';
    }
    if (typeof criteria === 'string') {
      return `<p class="tp-goal-text">${escapeHtml(criteria)}</p>`;
    }
    if (!Array.isArray(criteria)) {
      return `<pre class="code-block">${escapeHtml(JSON.stringify(criteria, null, 2))}</pre>`;
    }

    return `
      <ul class="criteria-list">
        ${criteria
          .map((item) => {
            if (typeof item === 'string') {
              return `<li class="criteria-item"><span class="criteria-mark">✓</span><span>${escapeHtml(item)}</span></li>`;
            }
            if (item && typeof item === 'object') {
              const passed = Boolean(item.done || item.passed);
              const text = item.criterion || item.description || item.text || item.title || JSON.stringify(item);
              return `<li class="criteria-item"><span class="criteria-mark ${passed ? '' : 'criteria-mark-pending'}">${passed ? '✓' : '○'}</span><span>${escapeHtml(text)}</span></li>`;
            }
            return `<li class="criteria-item"><span class="criteria-mark">○</span><span>${escapeHtml(String(item))}</span></li>`;
          })
          .join('')}
      </ul>
    `;
  }

  function renderMemberRow(w) {
    const isSelected = String(w.id) === String(state.selectedWorkerId);
    const stalled = isWorkerStalled(w);
    const ws = getWorkerWorkspace(w);
    const port = w.assigned_port ?? ws?.assigned_port ?? null;
    const ageSec = getHeartbeatAgeSec(w.last_heartbeat_at);
    const unmergedCount = getWorkspaceUnmergedCount(ws, w);
    const status = String(w.status || 'unknown').toLowerCase();
    const role = String(w.role || 'unknown').toLowerCase();

    return `
      <div class="member-list-item" role="listitem">
      <button
        type="button"
        class="member-row ${isSelected ? 'selected' : ''} ${stalled ? 'stalled' : ''}"
        data-worker-id="${escapeHtml(w.id)}"
      >
        <div class="member-row-left">
          ${renderRoleBadge(role, true)}
          <div class="member-info">
            <div class="member-top-line">
              <span class="member-name">@${escapeHtml(normalizeHandle(w.name))}</span>
              <span class="member-role">${escapeHtml(labelRole(role))}</span>
              ${isAppServerWorker(w) ? renderAppServerBadge() : ''}
              <span class="pill-badge ${getWorkerStatusTone(status, false)}">登记：${escapeHtml(labelWorkerStatus(status))}</span>
              ${stalled ? '<span class="pill-badge tint-danger">心跳超时告警</span>' : ''}
            </div>
            <p class="member-responsibility">${escapeHtml(roleResponsibility(role))}</p>
            <p class="member-task">最近登记工作：${escapeHtml(w.current_task || '尚未记录')}</p>
            ${renderHostObservation(w)}
          </div>
        </div>
        <div class="member-row-right tabular">
          ${port ? `<span class="member-port" title="独立验证端口">:${escapeHtml(port)}</span>` : '<span class="member-port">未分配端口</span>'}
          <span
            class="pill-badge heartbeat-age ${stalled ? 'tint-danger' : 'tint-muted'}"
            data-heartbeat="${escapeHtml(w.last_heartbeat_at || '')}"
            data-threshold="${escapeHtml(w.stall_threshold_sec || 300)}"
            data-status="${escapeHtml(status)}"
            data-alerted="${w.stalled_alerted ? '1' : '0'}"
          >${escapeHtml(formatAge(ageSec))}</span>
          ${
            ws
              ? `<span class="pill-badge ${unmergedCount > 0 ? 'tint-warning' : 'tint-muted'}" title="未合入或待提交变更文件数">${unmergedCount === null ? '尚未检查变更' : unmergedCount > 0 ? `${unmergedCount} 处变更` : '上次检查无变更'}</span>`
              : '<span class="pill-badge tint-muted">无工作区</span>'
          }
          <span class="member-open-cue">检查 →</span>
        </div>
      </button>
      </div>
    `;
  }

  function renderWorkers() {
    const focusedWorker = el.workersList.contains(document.activeElement)
      ? document.activeElement.closest('.member-row')?.getAttribute('data-worker-id') : null;
    const workers = state.workers;
    const stalledCount = workers.filter(isWorkerStalled).length;
    el.workersSummaryMeta.innerHTML = `
      <span class="pill-badge tint-muted tabular">共 ${workers.length} 名</span>
      ${stalledCount > 0 ? `<span class="pill-badge tint-danger tabular">${stalledCount} 名心跳异常</span>` : ''}
    `;

    if (workers.length === 0) {
      el.workersList.innerHTML = '<div class="empty-state">当前运行任务尚未注册成员。</div>';
      if (focusedWorker) el.tabButtons.find((btn) => btn.getAttribute('data-tab') === 'members').focus();
      return;
    }

    const activeWorkers = workers.filter((w) => ['waiting', 'working'].includes(String(w.status || 'unknown').toLowerCase()));
    const retiredWorkers = workers.filter((w) => ['stopped', 'killed'].includes(String(w.status || '').toLowerCase()));

    const activeSort = (a, b) => {
      const aStall = isWorkerStalled(a) ? 0 : 1;
      const bStall = isWorkerStalled(b) ? 0 : 1;
      if (aStall !== bStall) return aStall - bStall;
      const aWork = a.status === 'working' ? 0 : 1;
      const bWork = b.status === 'working' ? 0 : 1;
      if (aWork !== bWork) return aWork - bWork;
      return String(a.name || '').localeCompare(String(b.name || ''));
    };

    activeWorkers.sort(activeSort);

    const blocks = [];
    blocks.push(`
      <div class="member-group-block">
        <div class="member-group-label tabular">登记为执行中 / 待命 · ${activeWorkers.length}</div>
        <div class="member-list-card" ${activeWorkers.length > 0 ? 'role="list"' : ''}>
          ${
            activeWorkers.length > 0
              ? activeWorkers.map(renderMemberRow).join('')
              : '<div class="empty-state">当前无正在执行或待命的活跃成员。</div>'
          }
        </div>
      </div>
    `);

    if (retiredWorkers.length > 0) {
      blocks.push(`
        <div class="member-group-block">
          <div class="member-group-label tabular">登记为暂停 / 终止 · ${retiredWorkers.length}</div>
          <div class="member-list-card is-retired" role="list">
            ${retiredWorkers.map(renderMemberRow).join('')}
          </div>
        </div>
      `);
    }

    const unknownWorkers = workers.filter((w) => !['waiting', 'working', 'stopped', 'killed'].includes(w.status));
    if (unknownWorkers.length) blocks.push(`<div class="member-group-block"><div class="member-group-label">其他 / 未记录状态</div><div class="member-list-card" role="list">${unknownWorkers.map(renderMemberRow).join('')}</div></div>`);
    el.workersList.innerHTML = blocks.join('');
    if (focusedWorker) {
      const row = el.workersList.querySelector(`[data-worker-id="${CSS.escape(focusedWorker)}"]`);
      if (row) row.focus();
      else el.tabButtons.find((btn) => btn.getAttribute('data-tab') === 'members').focus();
    }
  }

  function tickHeartbeats() {
    const nodes = el.workersList.querySelectorAll('.heartbeat-age');
    for (const node of nodes) {
      const rawTs = node.getAttribute('data-heartbeat');
      const threshold = Number(node.getAttribute('data-threshold') || 300);
      const status = node.getAttribute('data-status');
      const age = getHeartbeatAgeSec(rawTs);
      node.textContent = formatAge(age);

      const overdue = state.run?.status === 'active' && status === 'working' && age !== null && age > threshold;
      node.classList.toggle('tint-danger', overdue);
      node.classList.toggle('tint-muted', !overdue);
      const row = node.closest('.member-row');
      if (row) {
        row.classList.toggle('stalled', overdue);
      }
    }
  }

  // ---------- 页签 1：消息板 ----------

  function getOrderedChannels() {
    const dynamic = Array.from(state.channels).filter((c) => !BASE_CHANNELS.includes(c));
    dynamic.sort((a, b) => a.localeCompare(b));
    return [...BASE_CHANNELS, ...dynamic];
  }

  function renderChannelTabsOnly() {
    const focusedChannel = el.channelTabs.contains(document.activeElement) ? document.activeElement.getAttribute('data-channel') : null;
    const ordered = getOrderedChannels();
    if (!ordered.includes(state.activeChannel)) {
      state.activeChannel = 'all';
    }

    const retired = state.workers.filter((w) => ['stopped', 'killed'].includes(w.status));
    const current = state.workers.filter((w) => !['stopped', 'killed'].includes(w.status));
    const counts = new Map();
    counts.set('all', state.messages.length);
    for (const ch of ordered) {
      if (ch !== 'all') counts.set(ch, 0);
    }
    for (const m of state.messages) {
      const ch = normalizeChannel(m.channel);
      counts.set(ch, (counts.get(ch) || 0) + 1);
    }

    el.channelTabs.innerHTML = ordered
      .map((ch) => {
        const active = state.activeChannel === ch;
        const count = counts.get(ch) || 0;
        const displayName = labelChannel(ch);
        const isCustom = !BASE_CHANNELS.includes(ch);
        return `
          <button
            type="button"
            class="ch-pill ${active ? 'active' : ''} ${isCustom ? 'mono' : ''}"
            data-channel="${escapeHtml(ch)}"
            aria-pressed="${active ? 'true' : 'false'}"
          >
            <span>${escapeHtml(displayName)}</span>
            <span class="ch-pill-count tabular">${count}</span>
          </button>
        `;
      })
      .join('');
    if (focusedChannel) {
      const channel = ordered.includes(focusedChannel) ? focusedChannel : state.activeChannel;
      el.channelTabs.querySelector(`[data-channel="${CSS.escape(channel)}"]`).focus();
    }
  }

  function renderChannelControls() {
    renderChannelTabsOnly();

    const currentSendChannel = el.humanChannelSelect.value;
    const writableChannels = [
      'human',
      'main',
      ...Array.from(state.channels).filter((c) => c !== 'human' && c !== 'main').sort(),
    ];
    el.humanChannelSelect.innerHTML =
      writableChannels
        .map((c) => `<option value="${escapeHtml(c)}">频道：${escapeHtml(labelChannel(c))}</option>`)
        .join('') + '<option value="__custom__">+ 自定义频道...</option>';

    if (currentSendChannel && (writableChannels.includes(currentSendChannel) || currentSendChannel === '__custom__')) {
      el.humanChannelSelect.value = currentSendChannel;
    } else {
      el.humanChannelSelect.value = 'human';
    }
  }

  function renderMentionFilterOptions() {
    const names = new Set();
    for (const w of state.workers) {
      if (w.name) names.add(normalizeHandle(w.name));
    }
    for (const m of state.messages) {
      for (const mention of normalizeMentions(m)) {
        names.add(mention);
      }
    }

    const sortedNames = Array.from(names).sort((a, b) => a.localeCompare(b));

    const prevFilter = el.mentionFilterSelect.value || state.mentionFilter;
    el.mentionFilterSelect.innerHTML =
      '<option value="">@提及：全部</option>' +
      sortedNames.map((n) => `<option value="${escapeHtml(n)}">@${escapeHtml(n)}</option>`).join('');
    if (prevFilter && sortedNames.includes(prevFilter)) {
      el.mentionFilterSelect.value = prevFilter;
    }

    const prevHumanTag = el.humanMentionSelect.value;
    el.humanMentionSelect.innerHTML =
      '<option value="">@提醒：可选</option>' +
      '<option value="all">@all（全体）</option>' +
      sortedNames
        .filter((n) => n !== 'all')
        .map((n) => `<option value="${escapeHtml(n)}">@${escapeHtml(n)}</option>`)
        .join('');
    if (prevHumanTag && (prevHumanTag === 'all' || sortedNames.includes(prevHumanTag))) {
      el.humanMentionSelect.value = prevHumanTag;
    }
  }

  function getFilteredMessages() {
    const chFilter = state.activeChannel;
    const mentionFilter = state.mentionFilter.trim().toLowerCase();
    const q = state.searchQuery.trim().toLowerCase();

    return state.messages.filter((msg) => {
      if (chFilter !== 'all' && normalizeChannel(msg.channel) !== chFilter) {
        return false;
      }

      const mentions = normalizeMentions(msg);
      if (mentionFilter) {
        const hasMention =
          mentions.some((m) => m.toLowerCase() === mentionFilter) ||
          String(msg.body_md || '').toLowerCase().includes(`@${mentionFilter}`);
        if (!hasMention) return false;
      }

      if (q) {
        const haystack = [
          `#${msg.seq}`,
          msg.sender,
          msg.sender_role,
          labelRole(msg.sender_role),
          msg.channel,
          labelChannel(msg.channel),
          msg.msg_type,
          labelMsgType(msg.msg_type),
          msg.summary,
          msg.body_md,
          mentions.join(' '),
        ]
          .filter(Boolean)
          .join('\n')
          .toLowerCase();
        if (!haystack.includes(q)) return false;
      }

      return true;
    });
  }

  function isFeedNearBottom() {
    const feed = el.boardFeed;
    return feed.scrollHeight - feed.scrollTop - feed.clientHeight < 64;
  }

  function scrollFeedToBottom() {
    el.boardFeed.scrollTop = el.boardFeed.scrollHeight;
    el.jumpLatestBtn.classList.add('hidden');
  }

  function roleResponsibility(role) {
    // These descriptions follow plugin/agents/*.toml; handles are not roles.
    const descriptions = {
      interviewer: '澄清需求，冻结可验收的标准；不修改代码。',
      investigator: '追踪调用链与故障根因，收集证据；不修改代码。',
      planner: '拆分实现任务，明确文件所有权并编写任务包。',
      coder: '在分配范围内实现代码并编写单元测试。',
      verifier: '独立检查规格与代码质量，运行验证；不修改代码。',
      swarm_worker: '在隔离工作区实现分配任务，通过消息板交付证据。',
      system: '监控运行心跳与异常停滞，发布系统看门狗告警。',
      workflow: '驱动结构化门禁流转与验收回执归档。',
    };
    return descriptions[role] || '职责说明未记录';
  }

  function messageSummary(msg) {
    const text = String(msg.summary || msg.body_md || '').replace(/\s+/g, ' ').trim();
    return text ? (text.length > 160 ? text.slice(0, 160) + '…' : text) : '未填写摘要或正文';
  }

  function labelWorkflowState(value) {
    const labels = {
      planned: '待实施', dispatched: '已分派', implementing: '实施中',
      candidate_review: '候选验收', ready_to_merge: '候选通过，待合入',
      integrating: '合入中', integrated_review: '集成验收', rework: '待原作者返工',
      needs_decision: '待用户决策', reconciliation_required: '需要核对',
      handoff: '等待交接确认', done: '已完成',
    };
    return labels[value] || `未识别阶段：${value}`;
  }

  function renderWorkflowStep(step) {
    const attention = ['needs_decision', 'reconciliation_required', 'rework', 'handoff'].includes(step.state);
    const stateBadge = `<span class="pill-badge ${attention ? 'tint-warning' : step.state === 'done' ? 'tint-success' : 'tint-muted'}">${escapeHtml(labelWorkflowState(step.state))}</span>`;
    const badges = isAppServerStep(step)
      ? `<span class="workflow-step-badges">${renderAppServerBadge()}${stateBadge}</span>`
      : stateBadge;
    return `<li class="workflow-step"><span class="workflow-step-name" title="${escapeHtml(step.step_key)}">${escapeHtml(step.step_key)}</span>${badges}<span class="muted-caption tabular">返工 ${escapeHtml(step.attempt)} / 3</span></li>`;
  }

  function renderProgressStatus() {
    if (![4, 5].includes(state.run.workflow_version)) return '';
    const progress = state.progressStatus;
    const heading = '<div class="overview-section-heading"><h2>当前推进</h2></div>';
    if (progress === null) return `<section class="overview-section">${heading}<p class="muted-caption">推进状态未加载，请同步后查看。</p></section>`;
    const labels = {ready: '有可推进事项', completed: '步骤已完成', inactive: '任务非活动', normal_wait: '正常等待', dependency_wait: '等待前序步骤', user_decision: '等待用户决策', needs_reconciliation: '需要核对', host_unknown: '需要观察宿主状态', no_progress: '无可执行推进'};
    const label = labels[progress.classification] || `未识别推进状态：${progress.classification}`;
    const blockers = progress.blockers.map((item) => `<li>${escapeHtml(item.reason)}</li>`).join('');
    return `<section class="overview-section" aria-label="当前推进与协调待办">${heading}<p>${escapeHtml(label)}${progress.coordinator_required ? ' · 等待协调者处理' : ''}</p><p class="muted-caption">依据登记事实判断，不代表宿主实时状态；心跳过期只要求观察，不会自动唤醒成员。</p>${blockers ? `<details class="workflow-blockers" data-overview-detail="progress-blockers" ${state.overviewExpanded.has('progress-blockers') ? 'open' : ''}><summary>查看 ${progress.blockers.length} 项阻塞依据</summary><ul>${blockers}</ul></details>` : ''}</section>`;
  }

  function renderResumeContext() {
    if (![4, 5].includes(state.run.workflow_version)) return '';
    const context = state.resumeContext;
    const plan = state.planReadiness;
    const heading = '<div class="overview-section-heading"><h2>计划与交接</h2></div>';
    if (context === null || plan === null) return `<section class="overview-section">${heading}<p class="muted-caption">交接投影未加载，请同步后查看。</p></section>`;
    const handoffs = context.handoffs.map((item) => `<li class="workflow-step"><span class="workflow-step-name" title="${escapeHtml(item.digest)}">${escapeHtml(item.digest)}</span><span class="pill-badge ${item.current ? 'tint-muted' : 'tint-warning'}">${item.current ? (item.acknowledged ? '交接包已确认' : '交接包待确认') : '交接包已过期'}</span><span class="muted-caption">${escapeHtml(item.recipient_agent_id)}</span></li>`).join('');
    const references = [...context.artifact_refs, ...context.verification_refs].map((item) => `<li>${escapeHtml(item.id)}</li>`).join('');
    return `<section class="overview-section" aria-label="计划准备度和交接包">${heading}<div class="workflow-delivery tabular"><span>未覆盖验收项 ${plan.uncovered_criteria.length}</span><span>范围冲突 ${plan.ownership_conflicts.length}</span><span>未处置业务消息 ${context.unresolved.inbox.length}</span><span>待处理控制 ${context.unresolved.controls.length}</span><span>未决转交 ${context.unresolved.native_actions.length}</span></div><p class="muted-caption">准备度提示不阻止创建合法草案；原协调者仍掌握旧子代理生命周期，接手不等于继承原生身份。</p><details class="workflow-more" data-overview-detail="resume-context" ${state.overviewExpanded.has('resume-context') ? 'open' : ''}><summary>交接包、证据引用与不得执行的操作</summary><p class="muted-caption">冻结任务包 ${escapeHtml(context.frozen_packet_id)} · 当前事实摘要 ${escapeHtml(context.facts_digest)}</p>${handoffs ? `<ul class="workflow-steps">${handoffs}</ul>` : '<p class="muted-caption">尚未建立交接包。</p>'}<ul>${plan.uncovered_criteria.map((item) => `<li>未覆盖：${escapeHtml(item)}</li>`).join('')}${plan.ownership_conflicts.map((item) => `<li>范围冲突：${escapeHtml(item.step_id)} / ${escapeHtml(item.other_step_id)} · ${escapeHtml(item.paths.join(' / '))}</li>`).join('')}${references}${context.forbidden_actions.map((item) => `<li>${escapeHtml(item)}</li>`).join('')}</ul></details></section>`;
  }

  function renderLocalReceipts() {
    if (![3, 4, 5].includes(state.run.workflow_version)) return '';
    const artifacts = state.workflow.artifacts.filter((item) => item.evidence_level === 'local_collector_v1');
    const rows = artifacts.slice(0, 3).map((artifact) => {
      const records = state.workflow.verifications.filter((record) => record.artifact_id === artifact.id);
      const evidenceCount = records.reduce((total, record) => total + record.evidence_manifest.length, 0);
      return `<li class="workflow-step"><span class="workflow-step-name" title="${escapeHtml(artifact.id)}">${escapeHtml(artifact.id)}</span><span class="pill-badge tint-muted">${escapeHtml(artifact.scope)} · ${escapeHtml(artifact.verification_command_count)} 条冻结命令</span><span class="muted-caption">${records.length} 次验收登记 · 登记证据文件 ${evidenceCount}</span></li>`;
    }).join('');
    return `<p class="muted-caption">本地采集 · local_collector_v1：不是宿主身份认证；冻结清单不表示采集已完成，登记证据也不表示当前有效。门禁使用时重新核对。</p>${artifacts.length ? `<details class="workflow-more" data-overview-detail="local-receipts" ${state.overviewExpanded.has('local-receipts') ? 'open' : ''}><summary>已冻结 ${artifacts.length} 份采集清单（展开查看登记摘要）</summary><ul class="workflow-steps">${rows}</ul>${artifacts.length > 3 ? `<p class="muted-caption">另有 ${artifacts.length - 3} 份清单；概况仅展示前 3 份，不加载原始日志。</p>` : ''}</details>` : '<p class="muted-caption">尚未登记本地采集清单。</p>'}`;
  }

  function renderReviewDimensions() {
    if (![4, 5].includes(state.run.workflow_version)) return '';
    const reviews = state.workflow.steps.flatMap((step) => Object.entries(step.checkpoint?.review_dimensions || {}).map(([id, dimensions]) => ({id, dimensions})));
    const rows = reviews.map(({id, dimensions}) => `<li>${escapeHtml(id)} · 规格检查：${escapeHtml(dimensions.specification)} · 代码正确性：${escapeHtml(dimensions.code)} · 压力与反例：${escapeHtml(dimensions.stress)} · 证据检查：${escapeHtml(dimensions.evidence)}</li>`).join('');
    return `<p class="muted-caption">分项审查由原验收者记录；项目目标达成仍需最终集成门禁，不等于多角色独立审查。</p>${reviews.length ? `<details class="workflow-more" data-overview-detail="review-dimensions" ${state.overviewExpanded.has('review-dimensions') ? 'open' : ''}><summary>规格、代码、反例与证据检查（${reviews.length} 份）</summary><ul>${rows}</ul></details>` : '<p class="muted-caption">尚未记录分项审查。</p>'}`;
  }

  function renderWorkflowSummary() {
    const version = state.run.workflow_version;
    const versionLabel = version === 0 ? '旧流程，未启用结构化门禁' : [1, 2, 3, 4, 5].includes(version) ? `结构化门禁 · v${version}` : '流程版本未记录';
    const heading = `<div class="overview-section-heading"><h2>流程与验收</h2><span class="pill-badge tint-muted">${versionLabel}</span></div>`;
    if (![1, 2, 3, 4, 5].includes(version)) return `<section class="overview-section workflow-overview">${heading}</section>`;
    if (state.workflow === null) return `<section class="overview-section workflow-overview">${heading}<p class="workflow-warning" role="status">结构化流程数据未加载，请同步后查看门禁状态。</p></section>`;
    const { steps, verifications, blockers } = state.workflow;
    // Keep urgent/current steps ahead of completed history without hiding any step.
    const ordered = [...steps].sort((a, b) => Number(a.state === 'done') - Number(b.state === 'done'));
    const counts = state.inbox?.counts;
    const delivery = counts ? `<div class="workflow-delivery tabular"><span>待接收 ${escapeHtml(counts.pending)}</span><span>已接收待处置 ${escapeHtml(counts.accepted)}</span><span>执行完成回执 ${escapeHtml(counts.completed)}</span><span>未处置任务 ${escapeHtml(state.inbox.blocking_count)}</span></div>` : '<p class="muted-caption">收件箱摘要未加载。</p>';
    const more = ordered.length > 3 ? `<details class="workflow-more" data-overview-detail="workflow-more" ${state.overviewExpanded.has('workflow-more') ? 'open' : ''}><summary>其余 ${ordered.length - 3} 个步骤</summary><ul class="workflow-steps">${ordered.slice(3).map(renderWorkflowStep).join('')}</ul></details>` : '';
    const blockerLabels = blockers.map((blocker) => {
      const stepKey = steps.find((step) => step.id === blocker.step_id)?.step_key || blocker.step_key || blocker.step_id || '未知步骤';
      return `${stepKey}：${labelWorkflowState(blocker.state)}`;
    });
    const blocking = blockerLabels.length ? `<details class="workflow-blockers" data-overview-detail="workflow-blockers" ${state.overviewExpanded.has('workflow-blockers') ? 'open' : ''}><summary class="workflow-warning">${escapeHtml(blockerLabels[0])}${blockers.length > 1 ? `（共 ${blockers.length} 项阻塞，展开查看）` : '（展开查看）'}</summary><ul>${blockerLabels.map((reason) => `<li>${escapeHtml(reason)}</li>`).join('')}</ul></details>` : '<p class="muted-caption">流程记录暂无阻塞；最终完成仍由后端核对当前产物与验收证据。</p>';
    return `<section class="overview-section workflow-overview" aria-label="结构化流程与收件状态">${heading}${delivery}<p class="muted-caption">回执不等于独立验收 · 已记录 ${verifications.length} 次验收，是否有效由当前门禁核对。</p>${renderLocalReceipts()}${renderReviewDimensions()}${blocking}${ordered.length ? `<ul class="workflow-steps">${ordered.slice(0, 3).map(renderWorkflowStep).join('')}</ul>${more}` : '<p class="muted-caption">尚未建立工作步骤，不能据此认定任务已验收。</p>'}</section>`;
  }

  function hostObservation(worker) {
    if (!worker.agent_id || state.workflow === null) return null;
    const observations = state.workflow.steps.map((step) => step.checkpoint?.native_observation)
      .filter((observation) => observation?.agent_id === worker.agent_id);
    observations.sort((a, b) => parseTimestampMs(b.observed_at) - parseTimestampMs(a.observed_at));
    return observations[0] || null;
  }

  function renderHostObservationContents(worker) {
    const observation = hostObservation(worker);
    if (!observation) return '宿主：未观察';
    const timestamp = parseTimestampMs(observation.observed_at);
    if (timestamp === null || timestamp > Date.now()) return '宿主：观察时间异常';
    const labels = { running: '执行中', active: '执行中', idle: '空闲', completed: '本轮结束', interrupted: '已中断', stopped: '已暂停', killed: '已终止', errored: '错误' };
    const stale = Date.now() - timestamp > 30000;
    const iso = new Date(timestamp).toISOString();
    return `最近观察：${escapeHtml(labels[observation.state] || observation.state)}${stale ? ' · 已过期' : ''}<time datetime="${escapeHtml(iso)}">${escapeHtml(new Date(timestamp).toLocaleString('zh-CN', { hour12: false }))}</time>`;
  }

  function renderHostObservation(worker) {
    const observation = hostObservation(worker);
    return `<span class="host-observation" data-host-worker="${escapeHtml(worker.id)}" data-observed-at="${escapeHtml(observation?.observed_at ?? '')}">${renderHostObservationContents(worker)}</span>`;
  }

  function tickHostObservations() {
    for (const node of document.querySelectorAll('.host-observation')) {
      const worker = state.workers.find((item) => String(item.id) === node.dataset.hostWorker);
      if (worker) node.innerHTML = renderHostObservationContents(worker);
    }
  }

  function renderOverviewMember(w) {
    return `          <button type="button" class="overview-member" data-worker-id="${escapeHtml(w.id)}">
            <span class="overview-member-role">${escapeHtml(labelRole(w.role))}<small>@${escapeHtml(w.name)}</small></span>
            <span class="overview-member-work"><span>${escapeHtml(roleResponsibility(w.role))}</span><span class="overview-current-task">最近登记工作：${escapeHtml(w.current_task || '尚未记录')}</span></span>
            <span class="overview-member-state">${isAppServerWorker(w) ? renderAppServerBadge() : ''}<span class="pill-badge tint-muted">登记：${escapeHtml(labelWorkerStatus(w.status))}</span>${renderHostObservation(w)}</span>
          </button>`;
  }

  function renderNativeActions() {
    const projection = state.nativeActions;
    const heading = '<h2>原生转交</h2>';
    if (projection === null) return `<section class="overview-section workflow-overview">${heading}<p class="muted-caption">转交投影未加载，请同步后查看。</p></section>`;
    const { counts, next_actions, needs_reconciliation, needs_clarification } = projection;
    const lowerBound = projection.counts_complete ? '' : '≥';
    const rows = [
      ...next_actions.map((item) => ({ item, label: '等待转交' })),
      ...needs_reconciliation.map((item) => ({ item, label: '需核对' })),
      ...needs_clarification.map((item) => ({ item, label: '待澄清' })),
    ];
    const ledger = rows.map(({ item, label }) => {
      const description = item.reason || item.source?.summary || item.action_type || item.status || '';
      const identity = `${item.source_type} · ${item.source_id}${item.action_id ? ` · ${item.action_id}` : ''}${item.seq != null ? ` · #${item.seq}` : ''}`;
      return `<li class="workflow-step"><span class="workflow-step-name" title="${escapeHtml(description)}">${escapeHtml(item.recipient || '收件人待澄清')} · ${escapeHtml(description)}</span><span class="pill-badge ${label === '等待转交' ? 'tint-muted' : 'tint-warning'}">${label}</span><span class="muted-caption">${escapeHtml(identity)}</span></li>`;
    }).join('');
    return `<section class="overview-section workflow-overview" aria-label="原生转交登记与待核对项">
      <div class="overview-section-heading">${heading}</div>
      <div class="workflow-delivery tabular"><span>等待转交 ${lowerBound}${escapeHtml(projection.next_actions_count)}</span><span>已转交 ${escapeHtml(counts.confirmed)}</span><span>需核对 ${lowerBound}${escapeHtml(projection.needs_reconciliation_count)}</span><span>待澄清 ${lowerBound}${escapeHtml(projection.needs_clarification_count)}</span></div>
      <p class="muted-caption">领取中 ${escapeHtml(counts.claimed)} · 调用中 ${escapeHtml(counts.invoking)} · 转交失败 ${escapeHtml(counts.failed)} · 已取消 ${escapeHtml(counts.cancelled)}</p>
      <p class="muted-caption">转交登记不代表宿主实时状态；已转交不等于业务完成或独立验收。需核对项不会自动重放。</p>
      <details class="workflow-more" data-overview-detail="native-ledger" ${state.overviewExpanded.has('native-ledger') ? 'open' : ''}><summary>转交账本与待办明细（本次 ${rows.length} 项${projection.native_actions_has_more ? '，有更多记录' : ''}）</summary><p class="muted-caption">每类最多 ${escapeHtml(projection.limit)} 项；${projection.counts_complete ? '待办计数完整。' : '待办计数为当前查询下界，不代表完整总数。'}转交状态计数来自账本。</p>${rows.length ? `<ul class="workflow-steps">${ledger}</ul>` : '<p class="muted-caption">当前没有等待转交、需核对或待澄清项。</p>'}</details>
    </section>`;
  }

  function renderOverview() {
    const focused = el.overviewContent.contains(document.activeElement) ? document.activeElement : null;
    const focusKey = ['id', 'data-action', 'data-view', 'data-open-seq', 'data-worker-id'].find((key) => focused?.getAttribute(key) != null);
    const detail = focused?.tagName === 'SUMMARY' ? focused.closest('[data-overview-detail]') : null;
    const focusSelector = focusKey ? `[${focusKey}="${CSS.escape(focused.getAttribute(focusKey))}"]`
      : detail ? `[data-overview-detail="${CSS.escape(detail.getAttribute('data-overview-detail'))}"] > summary` : null;
    if (state.loadState !== 'ready') {
      el.overviewContent.innerHTML = `<div class="empty-state">${state.loadState === 'loading' ? '正在加载任务概况…' : state.loadState === 'error' ? '任务数据未加载，请查看上方错误并重试。' : `当前项目暂无 ${escapeHtml(MODE_NAMES[PAGE_MODE])} 任务，可切换项目或点击「立即同步」。`}</div>`;
      if (focused) el.tabButtons.find((btn) => btn.getAttribute('data-tab') === 'overview').focus();
      return;
    }
    const run = state.run;
    const recent = state.messages.slice(-6).reverse();
    const retired = state.workers.filter((w) => ['stopped', 'killed'].includes(w.status));
    const current = state.workers.filter((w) => !['stopped', 'killed'].includes(w.status));
    const counts = new Map();
    for (const worker of state.workers) {
      const label = labelWorkerStatus(worker.status);
      counts.set(label, (counts.get(label) || 0) + 1);
    }
    el.overviewContent.innerHTML = `
      <section class="overview-section overview-task">
        <h2>任务概况 <span class="pill-badge tint-muted">${escapeHtml(labelRunStatus(run.status))}</span></h2>
        <details class="overview-goal-details" data-overview-detail="goal" ${state.overviewExpanded.has('goal') ? 'open' : ''}>
          <summary><span class="overview-goal-preview">${escapeHtml(state.taskPacket?.goal || '任务包尚未记录核心目标。')}</span><span class="goal-expand-label">展开 / 收起完整目标</span></summary>
          <p class="overview-goal">${escapeHtml(state.taskPacket?.goal || '任务包尚未记录核心目标。')}</p>
        </details>
        <p class="overview-meta">${escapeHtml(labelMode(run.mode))} · ${state.workers.length} 名已登记成员 · ${escapeHtml(Array.from(counts, ([label, n]) => `${n} 名${label}`).join('，') || '尚无成员状态')}</p>
        <p class="muted-caption">状态与工作内容来自登记记录；不代表代理当前在线或执行进度。</p>
      </section>
      ${renderWorkflowSummary()}
      ${renderProgressStatus()}
      ${renderResumeContext()}
      ${renderNativeActions()}
      <div class="overview-columns">
      <section class="overview-section">
        <div class="overview-section-heading"><h2>成员分工</h2><button type="button" class="btn btn-ghost btn-xs" data-view="members">查看任务包与全部成员</button></div>
        <div class="overview-members">${current.length ? current.map(renderOverviewMember).join('') : '<p class="muted-caption">没有登记为执行中或待命的成员。</p>'}</div>
        ${retired.length ? `<details class="retired-members" data-overview-detail="retired" ${state.overviewExpanded.has('retired') ? 'open' : ''}><summary>已暂停 / 终止成员（${retired.length}）</summary>${retired.map(renderOverviewMember).join('')}</details>` : ''}
      
      </section>
      <section class="overview-section">
        <div class="overview-section-heading"><h2>最近动态</h2><button type="button" class="btn btn-ghost btn-xs" data-view="board">查看全部 ${state.messages.length} 条日志</button></div>
        <p class="muted-caption">最近 ${recent.length} 条公告，按发布时间倒序。点击查看全文。</p>
        <div class="overview-updates">${recent.length ? recent.map((msg) => `<button type="button" class="overview-update" data-open-seq="${escapeHtml(msg.seq)}"><span>${escapeHtml(messageSummary(msg))}</span><small>${escapeHtml(labelRole(inferSenderRole(msg)))} · @${escapeHtml(msg.sender)} · #${escapeHtml(msg.seq)} · ${escapeHtml(formatLocalTime(msg.created_at))}</small></button>`).join('') : '<p class="muted-caption">尚无协作动态。</p>'}</div>
      </section>
      </div>`;
    if (focused) {
      const target = focusSelector ? el.overviewContent.querySelector(focusSelector) : null;
      let closedDetails = target?.closest('details:not([open])');
      while (closedDetails && closedDetails.querySelector(':scope > summary') === target) {
        closedDetails = closedDetails.parentElement.closest('details:not([open])');
      }
      if (target && !closedDetails && target.getClientRects().length > 0) target.focus();
      else el.tabButtons.find((btn) => btn.getAttribute('data-tab') === 'overview').focus();
    }
  }


  function renderBoardFeed(opts = {}) {
    const wasNearBottom = isFeedNearBottom();
    const filtered = getFilteredMessages();

    if (filtered.length !== state.messages.length) {
      el.boardStatsSubtitle.textContent = `显示 ${filtered.length} / ${state.messages.length} 条`;
      el.boardStatsSubtitle.classList.remove('hidden');
    } else {
      el.boardStatsSubtitle.classList.add('hidden');
    }

    if (filtered.length === 0) {
      el.boardFeed.innerHTML =
        state.messages.length === 0
          ? '<div class="empty-state empty-board">暂无消息，等待运行任务产生协作动态...</div>'
          : '<div class="empty-state empty-board">没有匹配当前频道、@提及或关键词筛选条件的消息。</div>';
      return;
    }

    el.boardFeed.innerHTML = filtered
      .map((msg) => {
        const msgType = String(msg.msg_type || 'info').toLowerCase();
        const role = inferSenderRole(msg);
        const channel = normalizeChannel(msg.channel);
        const mentions = normalizeMentions(msg);
        const rowClass =
          msgType === 'alert' ? 'msg-alert' : msgType === 'human' || msg.sender === 'human' ? 'msg-human' : '';
        const summaryText = messageSummary(msg);
        const bodyMd = msg.body_md ? String(msg.body_md) : '';
        const expanded = state.expandedMessages.has(String(msg.seq));
        const senderHandle = normalizeHandle(msg.sender || 'unknown');
        const showChannelChip = channel !== 'main';
        const showTypeChip = msgType !== 'info';

        return `
          <article class="feed-row ${rowClass}" data-seq="${escapeHtml(msg.seq)}">
            <div class="feed-row-head">
              <div class="feed-row-identity">
                ${renderRoleBadge(role, false)}
                <div class="feed-meta-line">
                  <span class="feed-sender">@${escapeHtml(senderHandle)}</span>
                  <span class="feed-role-name">${escapeHtml(labelRole(role))}</span>
                  ${
                    mentions.length > 0
                      ? `<span class="feed-arrow" aria-hidden="true">→</span>
                         <span class="feed-recipients">${mentions.map((m) => `<span>@${escapeHtml(m)}</span>`).join(' ')}</span>`
                      : ''
                  }
                  ${
                    showChannelChip
                      ? `<span class="chip-tag tint-muted ${channel !== 'human' ? 'mono' : ''}">${escapeHtml(labelChannel(channel))}</span>`
                      : ''
                  }
                  ${
                    showTypeChip
                      ? `<span class="chip-tag ${getMsgTypeTone(msgType)}">${escapeHtml(labelMsgType(msgType))}</span>`
                      : ''
                  }
                </div>
              </div>
              <span class="feed-seq-time mono tabular" title="${escapeHtml(msg.created_at || '')}">#${escapeHtml(msg.seq)} · ${escapeHtml(formatLocalTime(msg.created_at))}</span>
            </div>
            <div class="feed-body-wrap">
              <details class="message-details" data-message-seq="${escapeHtml(msg.seq)}" ${expanded ? 'open' : ''}>
                <summary><span>${escapeHtml(summaryText)}</span><span class="message-expand-label">全文</span></summary>
                <div class="md-body">${expanded ? renderMarkdown(bodyMd || msg.summary || '（正文为空）') : ''}</div>
              </details>
            </div>
          </article>
        `;
      })
      .join('');

    if (state.autoScroll && (wasNearBottom || !opts.isLiveAppend)) {
      scrollFeedToBottom();
    } else if (opts.isLiveAppend && !wasNearBottom) {
      el.jumpLatestBtn.classList.remove('hidden');
    }
  }

  function jumpToSeqRow(seq) {
    const targetSeq = String(seq);
    switchTab('board');
    state.expandedMessages.add(targetSeq);
    renderBoardFeed();
    let row = el.boardFeed.querySelector(`[data-seq="${CSS.escape(targetSeq)}"]`);
    if (!row) {
      state.activeChannel = 'all';
      state.mentionFilter = '';
      state.searchQuery = '';
      el.mentionFilterSelect.value = '';
      el.boardSearchInput.value = '';
      renderChannelTabsOnly();
      renderBoardFeed();
      row = el.boardFeed.querySelector(`[data-seq="${CSS.escape(targetSeq)}"]`);
    }
    if (row) {
      const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
      row.setAttribute('tabindex', '-1');
      row.focus({ preventScroll: true });
      row.scrollIntoView({ behavior: reducedMotion ? 'auto' : 'smooth', block: 'center' });
      row.classList.remove('msg-highlight');
      if (!reducedMotion) {
        void row.offsetWidth;
        row.classList.add('msg-highlight');
      }
    }
  }

  // ---------- 页签 3：实时协作图与时间轴回放 ----------

  function stopGraphReplay() {
    if (state.graphPlayTimer) {
      clearInterval(state.graphPlayTimer);
      state.graphPlayTimer = null;
    }
    state.graphPlaying = false;
    el.graphPlayIcon.classList.remove('hidden');
    el.graphPauseIcon.classList.add('hidden');
    el.graphPlayBtn.title = '回放协作流';
    el.graphPlayBtn.setAttribute('aria-label', '回放协作流');
  }

  function startGraphReplay() {
    const total = state.messages.length;
    if (total === 0) return;

    stopGraphReplay();
    state.graphPlaying = true;
    el.graphPlayIcon.classList.add('hidden');
    el.graphPauseIcon.classList.remove('hidden');
    el.graphPlayBtn.title = '暂停回放';
    el.graphPlayBtn.setAttribute('aria-label', '暂停回放');

    if (state.graphStep === null || state.graphStep >= total) {
      state.graphStep = 1;
    }
    renderGraph();

    state.graphPlayTimer = setInterval(() => {
      if (state.graphStep === null || state.graphStep >= state.messages.length) {
        state.graphStep = null;
        stopGraphReplay();
        renderGraph();
        return;
      }
      state.graphStep += 1;
      if (state.graphStep >= state.messages.length) {
        state.graphStep = null;
        stopGraphReplay();
      }
      renderGraph();
    }, GRAPH_PLAY_INTERVAL_MS);
  }

  function buildGraphModel(stepLimit) {
    const slice = state.messages.slice(0, stepLimit);
    const nodesMap = new Map();

    function ensureNode(handle, defaults = {}) {
      const clean = normalizeHandle(handle);
      if (!clean || clean.toLowerCase() === 'all') return null;
      const existing = nodesMap.get(clean);
      if (existing) {
        if (defaults.role && existing.role === 'unknown') existing.role = defaults.role;
        if (defaults.status) existing.status = defaults.status;
        if (defaults.stalled !== undefined) existing.stalled = defaults.stalled;
        if (defaults.worker) existing.worker = defaults.worker;
        return existing;
      }
      const role = defaults.role || 'unknown';
      const node = {
        id: clean,
        role,
        status: defaults.status || 'active',
        stalled: Boolean(defaults.stalled),
        worker: defaults.worker || null,
        sentCount: 0,
        recvCount: 0,
        broadcastCount: 0,
        lastSeq: 0,
      };
      nodesMap.set(clean, node);
      return node;
    }

    for (const w of state.workers) {
      if (!w.name) continue;
      ensureNode(w.name, {
        role: String(w.role || 'unknown').toLowerCase(),
        status: String(w.status || 'unknown').toLowerCase(),
        stalled: isWorkerStalled(w),
        worker: w,
      });
    }

    for (const m of slice) {
      const sender = normalizeHandle(m.sender || 'unknown');
      const role = inferSenderRole(m);
      const sNode = ensureNode(sender, { role });
      if (sNode) {
        sNode.sentCount += 1;
        sNode.lastSeq = Math.max(sNode.lastSeq, Number(m.seq || 0));
      }
      for (const ment of normalizeMentions(m)) {
        if (ment.toLowerCase() === 'all') continue;
        const rNode = ensureNode(ment);
        if (rNode) {
          rNode.recvCount += 1;
          rNode.lastSeq = Math.max(rNode.lastSeq, Number(m.seq || 0));
        }
      }
    }

    const edgeMap = new Map();
    const lastSliceMsg = slice.length > 0 ? slice[slice.length - 1] : null;
    const allIds = Array.from(nodesMap.keys());

    function addEdge(from, to, isBroadcast, seq) {
      if (!from || !to || from === to) return;
      const key = `${from}:::${to}:::${isBroadcast ? 'all' : 'direct'}`;
      const prev = edgeMap.get(key);
      if (prev) {
        prev.count += 1;
        prev.lastSeq = Math.max(prev.lastSeq, seq);
      } else {
        edgeMap.set(key, {
          from,
          to,
          isBroadcast,
          count: 1,
          lastSeq: seq,
        });
      }
    }

    for (const m of slice) {
      const sender = normalizeHandle(m.sender || 'unknown');
      const seq = Number(m.seq || 0);
      const mentions = normalizeMentions(m);
      const hasAll = mentions.some((x) => x.toLowerCase() === 'all');

      if (hasAll) {
        const sNode = nodesMap.get(sender);
        if (sNode) sNode.broadcastCount += 1;
        if (state.graphExpandAll) {
          for (const targetId of allIds) {
            if (targetId !== sender) {
              addEdge(sender, targetId, true, seq);
            }
          }
        }
      }

      for (const ment of mentions) {
        if (ment.toLowerCase() === 'all') continue;
        addEdge(sender, ment, false, seq);
      }
    }

    let nodes = Array.from(nodesMap.values());
    let edges = Array.from(edgeMap.values());

    if (state.graphHideInactive) {
      const connectedIds = new Set();
      for (const e of edges) {
        connectedIds.add(e.from);
        connectedIds.add(e.to);
      }
      nodes = nodes.filter((n) => {
        if (n.role === 'coordinator' || n.role === 'human') {
          return connectedIds.has(n.id) || n.sentCount > 0 || n.recvCount > 0;
        }
        const isEnded = n.status === 'stopped' || n.status === 'killed';
        if (isEnded && !connectedIds.has(n.id)) return false;
        if (!n.worker && !connectedIds.has(n.id)) return false;
        return !isEnded || connectedIds.has(n.id);
      });
      const visibleIds = new Set(nodes.map((n) => n.id));
      edges = edges.filter((e) => visibleIds.has(e.from) && visibleIds.has(e.to));
    }

    return { nodes, edges, slice, lastSliceMsg };
  }

  function layoutGraphNodes(nodes) {
    const width = 760;
    const topNodes = [];
    const midNodes = [];
    const bottomNodes = [];

    for (const n of nodes) {
      if (n.role === 'coordinator' || n.role === 'human' || n.role === 'watchdog') {
        topNodes.push(n);
      } else if (n.role === 'verifier') {
        bottomNodes.push(n);
      } else {
        midNodes.push(n);
      }
    }

    const pos = new Map();
    const boxW = 148;
    const boxH = 44;

    function placeRow(list, y) {
      if (list.length === 0) return;
      const padX = 86;
      const span = width - padX * 2;
      if (list.length === 1) {
        pos.set(list[0].id, { x: width / 2, y, w: boxW, h: boxH });
        return;
      }
      const step = span / (list.length - 1);
      list.forEach((item, idx) => {
        pos.set(item.id, { x: padX + step * idx, y, w: boxW, h: boxH });
      });
    }

    const useCircularFallback = (topNodes.length === 0 && bottomNodes.length === 0) && nodes.length > 1;
    let totalHeight = 380;

    if (useCircularFallback) {
      const cx = width / 2;
      const cy = 190;
      const rx = Math.min(260, 110 + nodes.length * 22);
      const ry = 120;
      nodes.forEach((n, idx) => {
        const angle = (Math.PI * 2 * idx) / nodes.length - Math.PI / 2;
        pos.set(n.id, {
          x: cx + rx * Math.cos(angle),
          y: cy + ry * Math.sin(angle),
          w: boxW,
          h: boxH,
        });
      });
      totalHeight = 380;
    } else {
      placeRow(topNodes, 56);
      if (midNodes.length <= 4) {
        placeRow(midNodes, topNodes.length > 0 ? 190 : 80);
        const bottomY = midNodes.length > 0 ? 324 : 210;
        placeRow(bottomNodes, bottomY);
        totalHeight = bottomNodes.length > 0 ? 390 : (midNodes.length > 0 ? 265 : 140);
      } else {
        const splitIdx = Math.ceil(midNodes.length / 2);
        const rowA = midNodes.slice(0, splitIdx);
        const rowB = midNodes.slice(splitIdx);
        placeRow(rowA, 165);
        placeRow(rowB, 265);
        placeRow(bottomNodes, 368);
        totalHeight = bottomNodes.length > 0 ? 430 : 340;
      }
    }

    return { pos, width, height: totalHeight };
  }

  function computeEdgeGeometry(fromBox, toBox, pairHasReverse, isBroadcast) {
    const dx = toBox.x - fromBox.x;
    const dy = toBox.y - fromBox.y;
    const dist = Math.hypot(dx, dy) || 1;
    const ux = dx / dist;
    const uy = dy / dist;

    // 算出矩形边界交点，避免箭头被节点遮挡
    const rxFrom = fromBox.w / 2 + 4;
    const ryFrom = fromBox.h / 2 + 4;
    const scaleFrom = 1 / Math.max(Math.abs(ux) / rxFrom, Math.abs(uy) / ryFrom);
    const sx = fromBox.x + ux * scaleFrom;
    const sy = fromBox.y + uy * scaleFrom;

    const rxTo = toBox.w / 2 + 6;
    const ryTo = toBox.h / 2 + 6;
    const scaleTo = 1 / Math.max(Math.abs(ux) / rxTo, Math.abs(uy) / ryTo);
    const ex = toBox.x - ux * scaleTo;
    const ey = toBox.y - uy * scaleTo;

    const nx = -uy;
    const ny = ux;
    const curveOffset = pairHasReverse ? (isBroadcast ? 34 : 22) : (isBroadcast ? 24 : 12);

    const cx = (sx + ex) / 2 + nx * curveOffset;
    const cy = (sy + ey) / 2 + ny * curveOffset;

    const lx = 0.25 * sx + 0.5 * cx + 0.25 * ex;
    const ly = 0.25 * sy + 0.5 * cy + 0.25 * ey;

    return {
      d: `M ${sx.toFixed(1)} ${sy.toFixed(1)} Q ${cx.toFixed(1)} ${cy.toFixed(1)} ${ex.toFixed(1)} ${ey.toFixed(1)}`,
      lx: lx.toFixed(1),
      ly: ly.toFixed(1),
    };
  }

  function getGraphNodeStrokeColor(node) {
    if (node.stalled) return 'var(--danger-dot)';
    if (node.role === 'coordinator' || node.role === 'human') return 'var(--primary)';
    if (node.role === 'verifier') return 'var(--success-dot)';
    if (node.status === 'working') return 'var(--warning-dot)';
    if (node.status === 'killed') return 'var(--danger-dot)';
    if (node.status === 'stopped') return 'var(--border-strong)';
    return 'var(--border-strong)';
  }

  function renderGraphDetail(model, stepLimit) {
    const focused = el.graphDetailBar.contains(document.activeElement) ? document.activeElement : null;
    const action = focused?.getAttribute('data-graph-action');
    const targetKey = focused?.getAttribute('data-node-id') !== null ? 'data-node-id' : 'data-worker-id';
    const focusSelector = action ? `[data-graph-action="${CSS.escape(action)}"][${targetKey}="${CSS.escape(focused.getAttribute(targetKey))}"]` : null;
    const focusId = state.graphHoverNode || state.graphSelectedNode;
    if (focusId) {
      const node = model.nodes.find((n) => n.id === focusId);
      if (node) {
        const displayName = node.id === 'coordinator' ? '主协调者' : `@${node.id}`;
        const statusStr = node.worker ? ` · ${labelWorkerStatus(node.status)}` : '';
        const broadcastStr = node.broadcastCount > 0 ? ` · @all 广播 ${node.broadcastCount} 次` : '';
        el.graphDetailBar.classList.remove('hidden');
        el.graphDetailBar.innerHTML = `
          <div class="graph-detail-main">
            <span class="mono" style="font-weight:600;">${escapeHtml(displayName)}</span>
            ${isAppServerWorker(node.worker) ? renderAppServerBadge() : ''}
            <span class="pill-badge tint-muted">${escapeHtml(labelRole(node.role))}${escapeHtml(statusStr)}</span>
            <span class="muted-caption tabular">发出 ${node.sentCount} 条 · 被 @提及 ${node.recvCount} 次${escapeHtml(broadcastStr)}</span>
          </div>
          <div class="graph-detail-actions">
            <button type="button" class="btn btn-xs" data-graph-action="filter-board" data-node-id="${escapeHtml(node.id)}">在消息板筛选</button>
            ${
              node.worker
                ? `<button type="button" class="btn btn-xs btn-accent" data-graph-action="inspect-worker" data-worker-id="${escapeHtml(node.worker.id)}">打开工作区检查器</button>`
                : ''
            }
          </div>
        `;
        if (focused) {
          const target = focusSelector ? el.graphDetailBar.querySelector(focusSelector) : null;
          if (target) target.focus();
          else el.tabButtons.find((btn) => btn.getAttribute('data-tab') === 'graph').focus();
        }
        return;
      }
    }

    if (model.lastSliceMsg) {
      const m = model.lastSliceMsg;
      const senderName = normalizeHandle(m.sender || 'unknown');
      const mentions = normalizeMentions(m);
      const targetStr = mentions.length > 0 ? ` → ${mentions.map((x) => `@${x}`).join(' ')}` : '';
      const summary = m.summary || m.body_md || '';
      el.graphDetailBar.classList.remove('hidden');
      el.graphDetailBar.innerHTML = `
        <div class="graph-detail-main">
          <span class="pill-badge tint-primary mono tabular">#${escapeHtml(m.seq)}</span>
          <span class="mono" style="font-weight:600;">@${escapeHtml(senderName)}</span>
          ${targetStr ? `<span class="mono" style="color:var(--link);">${escapeHtml(targetStr)}</span>` : ''}
          <span class="muted-caption">${escapeHtml(summary.slice(0, 90))}${summary.length > 90 ? '...' : ''}</span>
        </div>
        <div class="graph-detail-actions">
          <span class="muted-caption tabular">时间点 ${stepLimit} / ${state.messages.length}</span>
        </div>
      `;
    } else {
      el.graphDetailBar.classList.add('hidden');
      el.graphDetailBar.innerHTML = '';
    }
    if (focused) el.tabButtons.find((btn) => btn.getAttribute('data-tab') === 'graph').focus();
  }

  function renderGraph() {
    const focusedNode = el.graphCanvasHost.contains(document.activeElement) ? document.activeElement.closest('.graph-node') : null;
    const focusedId = focusedNode?.getAttribute('data-node-id');
    const total = state.messages.length;
    const stepLimit = total === 0
      ? 0
      : (state.graphStep === null ? total : Math.max(1, Math.min(total, state.graphStep)));

    el.graphSlider.min = total > 0 ? '1' : '0';
    el.graphSlider.max = String(total);
    el.graphSlider.value = String(stepLimit);
    el.graphSlider.disabled = total === 0;
    el.graphPlayBtn.disabled = total === 0;
    el.graphReadout.textContent = `${stepLimit} / ${total} 条消息`;

    const model = buildGraphModel(stepLimit);
    if (model.nodes.length === 0) {
      el.graphCanvasHost.innerHTML =
        '<div class="empty-state">暂无协作消息，收到 #seq 消息或注册成员后将实时生成拓扑图。</div>';
      const detailHadFocus = el.graphDetailBar.contains(document.activeElement);
      el.graphDetailBar.classList.add('hidden');
      if (focusedNode || detailHadFocus) el.tabButtons.find((btn) => btn.getAttribute('data-tab') === 'graph').focus();
      return;
    }

    const { pos, width, height } = layoutGraphNodes(model.nodes);
    const activeFocus = state.graphHoverNode || state.graphSelectedNode;
    const latestSeq = model.lastSliceMsg ? Number(model.lastSliceMsg.seq) : -1;

    const edgeSetLookup = new Set(model.edges.map((e) => `${e.from}:::${e.to}`));

    const edgesSvg = model.edges
      .map((edge) => {
        const fromBox = pos.get(edge.from);
        const toBox = pos.get(edge.to);
        if (!fromBox || !toBox) return '';

        const hasReverse = edgeSetLookup.has(`${edge.to}:::${edge.from}`);
        const geom = computeEdgeGeometry(fromBox, toBox, hasReverse, edge.isBroadcast);
        const isConnectedToFocus = activeFocus && (edge.from === activeFocus || edge.to === activeFocus);
        const isLatest = edge.lastSeq === latestSeq && state.graphPlaying;
        const dimmed = activeFocus && !isConnectedToFocus;

        const strokeColor = isLatest || isConnectedToFocus ? 'var(--primary)' : 'var(--text-muted)';
        const markerId = isLatest || isConnectedToFocus ? 'url(#graphArrowActive)' : 'url(#graphArrow)';
        const strokeWidth = (1.5 + Math.min(2.8, (edge.count - 1) * 0.55)).toFixed(2);
        const opacity = dimmed ? '0.16' : (isConnectedToFocus || isLatest ? '0.95' : (edge.isBroadcast ? '0.48' : '0.68'));
        const dash = edge.isBroadcast ? 'stroke-dasharray="5 4"' : '';

        return `
          <g class="graph-edge" data-from="${escapeHtml(edge.from)}" data-to="${escapeHtml(edge.to)}" data-broadcast="${edge.isBroadcast ? '1' : '0'}" data-latest="${isLatest ? '1' : '0'}">
            <path d="${geom.d}" stroke="${strokeColor}" stroke-width="${strokeWidth}" ${dash} fill="none" marker-end="${markerId}" style="opacity:${opacity}"></path>
            <rect class="graph-edge-label-bg" x="${Number(geom.lx) - Math.max(10, (String(edge.count).length * 7 + 10) / 2)}" y="${Number(geom.ly) - 9}" width="${Math.max(20, String(edge.count).length * 7 + 10)}" height="17" rx="8"></rect>
            <text x="${geom.lx}" y="${Number(geom.ly) + 3.5}" text-anchor="middle" font-size="10.5" font-weight="600" class="mono" fill="${isConnectedToFocus ? 'var(--link)' : 'var(--text-secondary)'}">${edge.count}</text>
          </g>
        `;
      })
      .join('');

    const connectedToFocus = new Set();
    if (activeFocus) {
      connectedToFocus.add(activeFocus);
      for (const e of model.edges) {
        if (e.from === activeFocus) connectedToFocus.add(e.to);
        if (e.to === activeFocus) connectedToFocus.add(e.from);
      }
    }

    const nodesSvg = model.nodes
      .map((node) => {
        const box = pos.get(node.id);
        if (!box) return '';
        const x = (box.x - box.w / 2).toFixed(1);
        const y = (box.y - box.h / 2).toFixed(1);
        const strokeColor = getGraphNodeStrokeColor(node);
        const isSelected = activeFocus === node.id;
        const dimmed = activeFocus && !connectedToFocus.has(node.id);
        const displayTitle = `@${node.id}`;
        const truncatedTitle = displayTitle.length > 15 ? `${displayTitle.slice(0, 14)}…` : displayTitle;
        const subText = node.worker ? `${labelRole(node.role)} · ${labelWorkerStatus(node.status)}` : labelRole(node.role);

        return `
          <g class="graph-node" data-node-id="${escapeHtml(node.id)}" data-default-stroke="${escapeHtml(strokeColor)}" tabindex="0" role="button" aria-label="${escapeHtml(`${displayTitle} ${subText}`)}" aria-pressed="${state.graphSelectedNode === node.id ? 'true' : 'false'}">
            <rect
              style="opacity:${dimmed ? '0.28' : '1'}"
              x="${x}"
              y="${y}"
              width="${box.w}"
              height="${box.h}"
              rx="12"
              fill="${isSelected ? 'var(--bg-subtle)' : 'var(--bg-card)'}"
              stroke="${isSelected ? 'var(--primary)' : strokeColor}"
              stroke-width="${isSelected ? '2.2' : '1.5'}"
            ></rect>
            <text x="${box.x.toFixed(1)}" y="${(box.y - 3).toFixed(1)}" text-anchor="middle" font-size="12" font-weight="600" fill="var(--text-primary)" class="${node.id === 'coordinator' ? '' : 'mono'}">${escapeHtml(truncatedTitle)}</text>
            <text x="${box.x.toFixed(1)}" y="${(box.y + 13).toFixed(1)}" text-anchor="middle" font-size="10.5" fill="var(--text-muted)">${escapeHtml(subText)}</text>
          </g>
        `;
      })
      .join('');

    el.graphCanvasHost.innerHTML = `
      <svg class="graph-svg" viewBox="0 0 ${width} ${height}" role="group" aria-label="成员消息协作拓扑图">
        <defs>
          <marker id="graphArrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
            <path d="M 0 1 L 10 5 L 0 9 z" fill="var(--text-muted)"></path>
          </marker>
          <marker id="graphArrowActive" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
            <path d="M 0 1 L 10 5 L 0 9 z" fill="var(--primary)"></path>
          </marker>
        </defs>
        <g>${edgesSvg}</g>
        <g>${nodesSvg}</g>
      </svg>
    `;

    renderGraphDetail(model, stepLimit);
    if (focusedNode) {
      const target = el.graphCanvasHost.querySelector(`.graph-node[data-node-id="${CSS.escape(focusedId)}"]`);
      if (target) target.focus();
      else el.tabButtons.find((btn) => btn.getAttribute('data-tab') === 'graph').focus();
    }
  }

  function applyGraphHighlightInPlace() {
    const svg = el.graphCanvasHost.querySelector('.graph-svg');
    if (!svg) return;
    const total = state.messages.length;
    const stepLimit = total === 0
      ? 0
      : (state.graphStep === null ? total : Math.max(1, Math.min(total, state.graphStep)));
    const model = buildGraphModel(stepLimit);
    const activeFocus = state.graphHoverNode || state.graphSelectedNode;

    const connectedToFocus = new Set();
    if (activeFocus) {
      connectedToFocus.add(activeFocus);
      for (const e of model.edges) {
        if (e.from === activeFocus) connectedToFocus.add(e.to);
        if (e.to === activeFocus) connectedToFocus.add(e.from);
      }
    }

    for (const edgeEl of svg.querySelectorAll('.graph-edge')) {
      const from = edgeEl.getAttribute('data-from');
      const to = edgeEl.getAttribute('data-to');
      const isBroadcast = edgeEl.getAttribute('data-broadcast') === '1';
      const isLatest = edgeEl.getAttribute('data-latest') === '1';
      const isConnected = Boolean(activeFocus && (from === activeFocus || to === activeFocus));
      const dimmed = Boolean(activeFocus && !isConnected);

      const pathEl = edgeEl.querySelector('path');
      const textEl = edgeEl.querySelector('text');
      if (pathEl) {
        pathEl.style.opacity = dimmed ? '0.16' : (isConnected || isLatest ? '0.95' : (isBroadcast ? '0.48' : '0.68'));
        const active = isConnected || isLatest;
        pathEl.setAttribute('stroke', active ? 'var(--primary)' : 'var(--text-muted)');
        pathEl.setAttribute('marker-end', active ? 'url(#graphArrowActive)' : 'url(#graphArrow)');
      }
      if (textEl) {
        textEl.setAttribute('fill', isConnected ? 'var(--link)' : 'var(--text-secondary)');
      }
    }

    for (const nodeEl of svg.querySelectorAll('.graph-node')) {
      const nodeId = nodeEl.getAttribute('data-node-id');
      nodeEl.setAttribute('aria-pressed', state.graphSelectedNode === nodeId ? 'true' : 'false');
      const defaultStroke = nodeEl.getAttribute('data-default-stroke') || 'var(--border-strong)';
      const isSelected = activeFocus === nodeId;
      const dimmed = Boolean(activeFocus && !connectedToFocus.has(nodeId));
      const rectEl = nodeEl.querySelector('rect');
      if (rectEl) {
        rectEl.style.opacity = dimmed ? '0.28' : '1';
        rectEl.setAttribute('fill', isSelected ? 'var(--bg-subtle)' : 'var(--bg-card)');
        rectEl.setAttribute('stroke', isSelected ? 'var(--primary)' : defaultStroke);
        rectEl.setAttribute('stroke-width', isSelected ? '2.2' : '1.5');
      }
    }

    renderGraphDetail(model, stepLimit);
  }

  // ---------- 侧滑工作区检查器与安全操作 ----------

  async function loadWorkspaceDiff(workspaceId, opts = {}) {
    if (!workspaceId) return;
    const version = state.contextVersion;
    const request = {};
    state.diffRequests.set(workspaceId, request);
    state.diffErrors.delete(workspaceId);
    if (!opts.silent) {
      renderInspector();
    }
    let payload;
    try {
      payload = await apiRequest(`/api/workspaces/${encodeURIComponent(workspaceId)}/diff`);
    } catch (err) {
      if (version !== state.contextVersion || state.diffRequests.get(workspaceId) !== request) return;
      state.diffByWorkspace.delete(workspaceId);
      state.diffErrors.set(workspaceId, err.message);
      state.diffRequests.delete(workspaceId);
      if (!opts.silent) {
        showToast('加载工作区差异失败', err.message, 'error');
      }
      renderInspector();
      return;
    }
    if (version !== state.contextVersion || state.diffRequests.get(workspaceId) !== request) return;
    state.diffByWorkspace.set(workspaceId, payload);
    state.diffRequests.delete(workspaceId);
    renderWorkers();
    renderInspector();
  }

  function summarizeDiffLines(diffText) {
    let added = 0;
    let deleted = 0;
    const lines = String(diffText || '').replace(/\r\n?/g, '\n').split('\n');
    for (const line of lines) {
      if (line.startsWith('+++') || line.startsWith('---')) continue;
      if (line.startsWith('+')) added += 1;
      else if (line.startsWith('-')) deleted += 1;
    }
    return { added, deleted };
  }

  function renderUnifiedDiff(diffText) {
    const text = String(diffText || '');
    if (!text.trim()) {
      return '<div class="empty-state">暂无未提交或未合入的代码变更。</div>';
    }

    const lines = text.replace(/\r\n?/g, '\n').split('\n');
    const htmlLines = lines.map((line, idx) => {
      let cls = 'diff-line-ctx';
      if (
        line.startsWith('diff --git ') ||
        line.startsWith('index ') ||
        line.startsWith('--- ') ||
        line.startsWith('+++ ') ||
        line.startsWith('new file mode') ||
        line.startsWith('deleted file mode')
      ) {
        cls = 'diff-line-header';
      } else if (line.startsWith('@@')) {
        cls = 'diff-line-hunk';
      } else if (line.startsWith('+')) {
        cls = 'diff-line-add';
      } else if (line.startsWith('-')) {
        cls = 'diff-line-del';
      }

      return `<div class="diff-line ${cls}"><span class="diff-line-num tabular">${idx + 1}</span><span class="diff-line-text">${escapeHtml(line)}</span></div>`;
    });

    return `<div class="diff-container ${state.diffWrap ? 'diff-wrap' : ''}">${htmlLines.join('')}</div>`;
  }

  function renderMergePreviewBanner(preview) {
    if (!preview) return '';
    const p = preview.preview || preview.result || preview;
    const hasConflicts = Boolean(p.has_conflicts || (Array.isArray(p.conflict_files) && p.conflict_files.length > 0));
    const conflictFiles = Array.isArray(p.conflict_files) ? p.conflict_files : [];
    const filesChanged = Array.isArray(p.files_changed) ? p.files_changed : [];
    const summary = p.summary || (hasConflicts ? '干跑合并预检发现文件冲突，请先人工核对。' : '干跑合并预检通过，无冲突。');

    return `
      <div class="banner ${hasConflicts ? 'banner-danger' : 'banner-success'}">
        <div class="banner-header">
          <span>${hasConflicts ? '合并冲突预检：发现冲突' : '合并冲突预检：无冲突'}</span>
          <span class="pill-badge ${hasConflicts ? 'tint-danger' : 'tint-success'} tabular">${hasConflicts ? `${conflictFiles.length} 个冲突` : '可合入'}</span>
        </div>
        <div>${escapeHtml(summary)}</div>
        ${
          conflictFiles.length > 0
            ? `<div class="ownership-paths">${conflictFiles.map((f) => `<span class="path-chip">${escapeHtml(f)}</span>`).join('')}</div>`
            : ''
        }
        ${
          filesChanged.length > 0
            ? `<div class="muted-caption">涉及变更文件（${filesChanged.length}）：${escapeHtml(filesChanged.join(', '))}</div>`
            : ''
        }
      </div>
    `;
  }

  function upsertControlRequest(request) {
    const index = state.controlRequests.findIndex((item) => item.id === request.id);
    if (index < 0) state.controlRequests.push(request);
    else state.controlRequests[index] = request;
    renderInspector();
  }

  function acceptControlResponse(response, workerId, requestedStatus) {
    const request = response?.control_request;
    if (response?.ok !== true || response.workspace_preserved !== true || !request?.id ||
        String(request.worker_id) !== String(workerId) || request.requested_status !== requestedStatus ||
        request.status !== 'pending') {
      throw new Error('控制请求响应格式错误：未确认请求已进入待处理状态');
    }
    upsertControlRequest(request);
  }

  function renderWorkerControl(worker) {
    const isAppServer = typeof worker?.agent_id === 'string' && worker.agent_id.startsWith('appserver://');
    const labels = {
      pending: isAppServer ? '待处理（等待管线运行器 turn/interrupt 中断回执）' : '待处理（等待协调者原生中断回执）',
      applied: '已执行',
      failed: '执行失败',
    };
    const requests = state.controlRequests.filter((item) => String(item.worker_id) === String(worker.id));
    const bindingBadge = isAppServer
      ? ' <span class="pill-badge badge-appserver" title="由宿主终端 tri-orchestrator-pipeline 驱动的独立 App-Server 管线线程（appserver://），非聊天内原生 V2 子代理（/root/...）">App-Server 管线</span>'
      : '';
    const bindingNote = isAppServer
      ? '请求暂停 / 结束任务：tri-orchestrator-pipeline 在 <=2s 内发送 turn/interrupt 中断当前回合，工作区文件完整保留。'
      : '请求结束任务：协调者中断代理并停止分配任务，不终止操作系统进程；工作区文件保留。';
    return `<div class="inspector-section"><div class="kv-key">${isAppServer ? '执行线程绑定' : '原生代理绑定'}</div>
      <div class="kv-val mono">${escapeHtml(worker.agent_id || '未绑定（不可控制）')}${bindingBadge}</div>
      <div class="actions-note">${bindingNote}</div>
      ${requests.map((item) => `<div class="actions-note">${escapeHtml(item.requested_status === 'stopped' ? '请求暂停' : '请求结束任务')} · ${escapeHtml(labels[item.status])}<div>${escapeHtml(item.result || '')}</div></div>`).join('')}</div>`;
  }

  function restoreInspectorFocus(selector) {
    if (!selector) return;
    const target = el.inspectorDrawer.querySelector(selector);
    if (target && !target.disabled && !target.closest('.hidden')) target.focus();
    else el.inspectorTitle.focus();
  }

  function renderInspector() {
    const focused = el.inspectorDrawer.contains(document.activeElement) ? document.activeElement : null;
    const focusedId = focused?.getAttribute('id');
    const focusedAction = focused?.getAttribute('data-action');
    const focusedSelector = focusedId ? `#${CSS.escape(focusedId)}` : focusedAction ? `[data-action="${CSS.escape(focusedAction)}"]` : null;
    const worker = state.workers.find((w) => String(w.id) === String(state.selectedWorkerId));
    if (!worker) {
      el.inspectorTitle.textContent = '工作区检查器';
      el.inspectorSubtitle.textContent = '请选择成员以检查工作区';
      el.refreshDiffBtn.classList.add('hidden');
      el.inspectorBody.innerHTML =
        '<div class="empty-state">在“成员”页签点击任意成员行，即可在右侧检查其隔离工作区、变更文件状态、代码差异与安全操作。</div>';
      restoreInspectorFocus(focusedSelector);
      return;
    }

    const ws = getWorkerWorkspace(worker);
    const workerStatus = String(worker.status || 'waiting').toLowerCase();
    const workerHandle = `@${normalizeHandle(worker.name)}`;
    const roleLabel = labelRole(worker.role);
    const controlDisabled = state.actionBusy || !worker.agent_id || state.run?.status !== 'active' ||
      !['working', 'waiting'].includes(workerStatus) ||
      state.controlRequests.some((item) => String(item.worker_id) === String(worker.id) && item.status === 'pending');

    el.inspectorTitle.innerHTML = `工作区检查器 · <span class="mono">${escapeHtml(workerHandle)}</span>`;

    if (!ws) {
      el.inspectorSubtitle.textContent = `${roleLabel} · 未绑定工作区`;
      el.refreshDiffBtn.classList.add('hidden');
      el.inspectorBody.innerHTML = `
        ${renderWorkerControl(worker)}
        <div class="inspector-section">
          <div class="inspector-kv-list">
            <div class="kv-key">成员名称</div>
            <div class="kv-val mono">${escapeHtml(workerHandle)} ${isAppServerWorker(worker) ? renderAppServerBadge() : ''}<span class="pill-badge ${getWorkerStatusTone(workerStatus, isWorkerStalled(worker))}">${escapeHtml(labelWorkerStatus(workerStatus))}</span></div>
            <div class="kv-key">成员职责</div>
            <div class="kv-val">${escapeHtml(roleLabel)}</div>
            <div class="kv-key">当前任务</div>
            <div class="kv-val">${escapeHtml(worker.current_task || '—')}</div>
          </div>
        </div>
        <div class="inspector-section">
          <div class="inspector-section-head">
            <span class="inspector-section-title">安全状态控制（保文件）</span>
          </div>
          <div class="actions-grid">
            <button type="button" class="btn btn-warning" data-action="status-stopped" data-worker-id="${escapeHtml(worker.id)}" ${controlDisabled ? 'disabled' : ''}>
              请求暂停
            </button>
            <button type="button" class="btn btn-danger-outline" data-action="status-killed" data-worker-id="${escapeHtml(worker.id)}" ${controlDisabled ? 'disabled' : ''}>
              请求结束任务
            </button>
          </div>
          <div class="actions-note">该成员未绑定隔离工作区；控制请求须等待协调者执行并回执。</div>
        </div>
      `;
      restoreInspectorFocus(focusedSelector);
      return;
    }

    const portText = ws.assigned_port ? ` · 端口 ${ws.assigned_port}` : '';
    el.inspectorSubtitle.textContent = `${ws.branch_name || '目录快照'}${portText}`;
    el.refreshDiffBtn.classList.remove('hidden');

    const diffPayload = state.diffByWorkspace.get(ws.id);
    const isLoadingDiff = state.diffRequests.has(ws.id);
    const statusFiles = extractStatusFiles(diffPayload || ws);
    const diffText = extractDiffText(diffPayload);
    const diffStats = summarizeDiffLines(diffText);
    const aheadCommits = diffPayload?.commits_ahead ?? diffPayload?.ahead_commits ?? null;
    const wsStatus = String(ws.status || 'active').toLowerCase();
    const wsMode = String(ws.mode || 'git_clone').toLowerCase();
    const isCleaned = wsStatus === 'cleaned';
    const preview = state.mergePreviewByWorkspace.get(ws.id);
    const banner = state.inspectorBannerByWorkspace.get(ws.id);

    el.inspectorBody.innerHTML = `
      ${renderWorkerControl(worker)}
      <!-- 工作区元数据 -->
      <div class="inspector-section">
        <div class="inspector-kv-list">
          <div class="kv-key">成员状态</div>
          <div class="kv-val">
            <span class="mono">${escapeHtml(workerHandle)}</span>
            ${isAppServerWorker(worker) ? renderAppServerBadge() : ''}
            <span class="pill-badge ${getWorkerStatusTone(workerStatus, isWorkerStalled(worker))}">${escapeHtml(labelWorkerStatus(workerStatus))}</span>
          </div>
          <div class="kv-key">工作区</div>
          <div class="kv-val">
            <span class="pill-badge tint-primary">${escapeHtml(labelWsMode(wsMode))}</span>
            <span class="pill-badge ${getWsStatusTone(wsStatus)}">${escapeHtml(labelWsStatus(wsStatus))}</span>
          </div>
          <div class="kv-key">本地路径</div>
          <div class="kv-val mono">
            <span>${escapeHtml(ws.path || '—')}</span>
            ${ws.path ? `<button type="button" class="btn btn-ghost btn-xs" data-action="copy-path" data-path="${escapeHtml(ws.path)}">复制</button>` : ''}
          </div>
          <div class="kv-key">Git 分支</div>
          <div class="kv-val mono">${escapeHtml(ws.branch_name || '（目录快照）')}</div>
          <div class="kv-key">验证端口</div>
          <div class="kv-val mono tabular">${ws.assigned_port ? escapeHtml(ws.assigned_port) : '—'}</div>
          <div class="kv-key">基线提交</div>
          <div class="kv-val mono">${escapeHtml(ws.base_commit ? shortId(ws.base_commit) : '—')}</div>
        </div>
      </div>

      <!-- 仅保留 4 类安全操作 -->
      <div class="inspector-section">
        <div class="inspector-section-head">
          <span class="inspector-section-title">安全操作</span>
          <span class="pill-badge tint-muted">只读 / 受守卫保护</span>
        </div>
        <div class="actions-grid">
          <button type="button" class="btn btn-accent" data-action="merge-preview" data-workspace-id="${escapeHtml(ws.id)}" ${state.actionBusy || isCleaned ? 'disabled' : ''}>
            合并冲突预检
          </button>
          <button type="button" class="btn btn-secondary" data-action="open-explorer" data-workspace-id="${escapeHtml(ws.id)}" ${state.actionBusy || isCleaned ? 'disabled' : ''}>
            打开本地目录
          </button>
          <button type="button" class="btn btn-warning" data-action="status-stopped" data-worker-id="${escapeHtml(worker.id)}" ${controlDisabled ? 'disabled' : ''}>
            请求暂停
          </button>
          <button type="button" class="btn btn-danger-outline" data-action="status-killed" data-worker-id="${escapeHtml(worker.id)}" ${controlDisabled ? 'disabled' : ''}>
            请求结束任务
          </button>
        </div>
        <button
          type="button"
          class="btn ${wsStatus === 'merged' ? 'btn-success-outline' : 'btn-danger-outline'}"
          data-action="cleanup-workspace"
          data-workspace-id="${escapeHtml(ws.id)}"
          ${state.actionBusy || isCleaned ? 'disabled' : ''}
          title="受服务端安全守卫保护：仅在工作区状态为已合入且目录干净时允许清理"
        >
          ${isCleaned ? '工作区已清理' : (wsStatus === 'merged' ? '清理已合入工作区' : '清理已合入工作区（安全守卫：需先合入）')}
        </button>
        <div class="actions-note">
          手动状态标记仅更新调度状态并完整保留磁盘文件；工作区清理受服务端守卫保护，未合入或仍有未提交改动时将返回 HTTP 409 拦截。
        </div>
      </div>

      ${
        banner
          ? `<div class="banner banner-${escapeHtml(banner.variant || 'danger')}">
               <div class="banner-header">
                 <span>${escapeHtml(banner.title)}</span>
                 <button type="button" class="btn btn-ghost btn-xs" data-action="dismiss-banner" data-workspace-id="${escapeHtml(ws.id)}">关闭</button>
               </div>
               <div>${escapeHtml(banner.message)}</div>
             </div>`
          : ''
      }

      ${renderMergePreviewBanner(preview)}

      <!-- 变更文件列表 -->
      <div class="inspector-section">
        <div class="inspector-section-head">
          <span class="inspector-section-title">变更文件状态</span>
          <div class="tp-meta">
            ${aheadCommits !== null ? `<span class="pill-badge tint-muted tabular">领先 ${escapeHtml(aheadCommits)} 个提交</span>` : ''}
            <span class="pill-badge ${statusFiles.length > 0 ? 'tint-warning' : 'tint-muted'} tabular">${diffPayload ? `${statusFiles.length} 个文件` : '尚未检查'}</span>
          </div>
        </div>
        ${
          isLoadingDiff && !diffPayload
            ? '<div class="empty-state">正在加载工作区文件状态...</div>'
            : !diffPayload
              ? `<div class="empty-state">${escapeHtml(state.diffErrors.get(ws.id) ? `检查失败：${state.diffErrors.get(ws.id)}。请重新加载。` : '尚未检查工作区变更。')}</div>`
            : statusFiles.length === 0
              ? '<div class="empty-state">工作树干净（无已修改或未跟踪文件）。</div>'
              : `<div class="status-files-list">
                  ${statusFiles
                    .map((f) => {
                      const code = f.code || 'M';
                      const codeCls = code === '??' ? 'git-code-untracked' : `git-code-${escapeHtml(code[0])}`;
                      return `
                        <div class="status-file-row">
                          <span class="git-code ${codeCls}">${escapeHtml(code)}</span>
                          <span>${escapeHtml(f.path)}</span>
                        </div>
                      `;
                    })
                    .join('')}
                </div>`
        }
      </div>

      <!-- 代码变更差异（Diff） -->
      <div class="inspector-section">
        <div class="inspector-section-head">
          <span class="inspector-section-title">代码变更差异（Diff）</span>
          <div class="tp-meta">
            ${diffText.trim() ? `<span class="muted-caption mono tabular">+${diffStats.added} / -${diffStats.deleted}</span>` : ''}
            <button type="button" class="btn btn-ghost btn-xs" data-action="toggle-diff-wrap">
              ${state.diffWrap ? '取消换行' : '自动换行'}
            </button>
            <button type="button" class="btn btn-ghost btn-xs" data-action="reload-diff" data-workspace-id="${escapeHtml(ws.id)}">
              重新加载
            </button>
          </div>
        </div>
        <div class="diff-box">
          ${
            isLoadingDiff && !diffPayload
              ? '<div class="empty-state">正在加载 Diff...</div>'
              : !diffPayload ? '<div class="empty-state">尚未取得 Diff。</div>' : renderUnifiedDiff(diffText)
          }
        </div>
      </div>
    `;
    restoreInspectorFocus(focusedSelector);
  }

  function renderAll() {
    updateTopRunControls();
    renderTabCounts();
    renderTaskPacket();
    renderOverview();
    renderWorkers();
    renderChannelControls();
    renderMentionFilterOptions();
    renderBoardFeed();
    renderGraph();
    renderInspector();
  }

  // ---------- 交互处理 ----------

  function openExportMenu(last = false) {
    el.exportMenu.classList.remove('hidden');
    el.exportMenuBtn.setAttribute('aria-expanded', 'true');
    const items = Array.from(el.exportMenu.querySelectorAll('[role="menuitem"]:not(:disabled)'));
    items[last ? items.length - 1 : 0].focus();
  }

  function closeExportMenu(returnFocus = false) {
    el.exportMenu.classList.add('hidden');
    el.exportMenuBtn.setAttribute('aria-expanded', 'false');
    if (returnFocus) el.exportMenuBtn.focus();
  }

  async function handleHumanSubmit(evt) {
    evt.preventDefault();
    if (!state.selectedRunId || state.loadState !== 'ready' || state.sending) return;
    const version = state.contextVersion;
    const runId = state.selectedRunId;
    const draft = el.humanMessageInput.value;
    const mentionDraft = el.humanMentionCustom.value;

    const rawChannel =
      el.humanChannelSelect.value === '__custom__'
        ? el.humanChannelCustom.value.trim()
        : el.humanChannelSelect.value;
    const channel = normalizeChannel(rawChannel || 'human');
    let bodyMd = el.humanMessageInput.value.trim();
    if (!bodyMd) {
      showToast('内容为空', '请先输入需要发送的人工干预指令或消息。', 'warning', 3000);
      return;
    }

    const mentions = [];
    const selectMention = normalizeHandle(el.humanMentionSelect.value);
    const customMention = normalizeHandle(el.humanMentionCustom.value);
    if (selectMention) mentions.push(selectMention);
    if (customMention && !mentions.includes(customMention)) mentions.push(customMention);

    for (const m of mentions) {
      const token = `@${m}`;
      if (!bodyMd.includes(token)) {
        bodyMd = `${token} ${bodyMd}`;
      }
    }

    state.sending = true;
    el.humanSendBtn.disabled = true;
    try {
      const resp = await apiRequest(`/api/runs/${encodeURIComponent(runId)}/human-message`, {
        method: 'POST',
        body: JSON.stringify({
          channel,
          body_md: bodyMd,
          mentions,
        }),
      });
      if (version !== state.contextVersion) return;
      if (el.humanMessageInput.value === draft) el.humanMessageInput.value = '';
      if (el.humanMentionCustom.value === mentionDraft) el.humanMentionCustom.value = '';
      const postedMsg = resp?.message || (resp?.seq ? resp : null);
      if (postedMsg && postedMsg.seq !== undefined) {
        upsertBoardMessage(postedMsg);
      } else {
        await refreshSnapshot(state.selectedRunId, { silent: true });
      }
      if (version !== state.contextVersion) return;
      showToast('人工消息已发送', `已投递至「${labelChannel(channel)}」频道`, 'success', 2500);
    } catch (err) {
      if (version === state.contextVersion) showToast('发送人工消息失败', err.message, 'error');
    } finally {
      if (version === state.contextVersion) { state.sending = false; updateTopRunControls(); }
    }
  }

  async function handleExport(format) {
    if (!state.selectedRunId) return;
    closeExportMenu(true);
    const url = `/api/runs/${encodeURIComponent(state.selectedRunId)}/export?format=${encodeURIComponent(format)}`;
    try {
      const res = await fetch(url);
      if (!res.ok) {
        const text = await res.text().catch(() => res.statusText);
        throw new Error(text || `HTTP ${res.status}`);
      }
      const blob = await res.blob();
      const cd = res.headers.get('content-disposition') || '';
      const filenameMatch = cd.match(/filename="?([^"]+)"?/i);
      const ext = format === 'json' ? 'json' : 'md';
      const filename = filenameMatch ? filenameMatch[1] : `tri-orchestrator-run-${shortId(state.selectedRunId)}.${ext}`;

      const objectUrl = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = objectUrl;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(objectUrl);

      showToast('报告导出成功', filename, 'success', 3000);
    } catch (err) {
      showToast(`导出 ${extLabel(format)} 报告失败`, err.message, 'error');
    }
  }

  function extLabel(fmt) {
    return fmt === 'json' ? 'JSON' : 'Markdown';
  }

  async function runInspectorAction(action, targetEl) {
    const version = state.contextVersion;
    const workspaceId = targetEl.getAttribute('data-workspace-id');
    const workerId = targetEl.getAttribute('data-worker-id');

    if (action === 'copy-path') {
      const p = targetEl.getAttribute('data-path') || '';
      try {
        await navigator.clipboard.writeText(p);
        showToast('路径已复制', p, 'success', 2000);
      } catch {
        showToast('复制失败', p, 'warning', 3000);
      }
      return;
    }

    if (action === 'toggle-diff-wrap') {
      state.diffWrap = !state.diffWrap;
      renderInspector();
      return;
    }

    if (action === 'reload-diff' && workspaceId) {
      await loadWorkspaceDiff(workspaceId);
      return;
    }

    if (action === 'dismiss-banner' && workspaceId) {
      state.inspectorBannerByWorkspace.delete(workspaceId);
      renderInspector();
      return;
    }

    state.actionBusy = true;
    renderInspector();

    try {
      if (action === 'merge-preview' && workspaceId) {
        const preview = await apiRequest(`/api/workspaces/${encodeURIComponent(workspaceId)}/merge-preview`, {
          method: 'POST',
          body: JSON.stringify({}),
        });
        if (version !== state.contextVersion) return;
        state.mergePreviewByWorkspace.set(workspaceId, preview);
        state.inspectorBannerByWorkspace.delete(workspaceId);
        const p = preview.preview || preview.result || preview;
        const hasConflicts = Boolean(p.has_conflicts || (Array.isArray(p.conflict_files) && p.conflict_files.length > 0));
        showToast(
          hasConflicts ? '合并预检发现冲突' : '合并预检无冲突',
          hasConflicts ? '请在右侧工作区检查器查看冲突文件列表。' : '当前分支可安全合入目标分支。',
          hasConflicts ? 'warning' : 'success'
        );
      } else if ((action === 'status-stopped' || action === 'status-killed') && workerId) {
        const nextStatus = action === 'status-stopped' ? 'stopped' : 'killed';
        const resp = await apiRequest(`/api/workers/${encodeURIComponent(workerId)}/status`, {
          method: 'POST',
          body: JSON.stringify({ status: nextStatus }),
        });
        if (version !== state.contextVersion) return;
        acceptControlResponse(resp, workerId, nextStatus);
        const targetWorker = state.workers.find((w) => String(w.id) === String(workerId));
        const pendingHint = isAppServerWorker(targetWorker)
          ? '等待 tri-orchestrator-pipeline 在 <=2s 内触发 turn/interrupt 并返回回执；成员状态尚未改变。'
          : '等待协调者调用原生中断工具并返回执行回执；成员状态尚未改变。';
        showToast('控制请求待处理', pendingHint, 'warning');
      } else if (action === 'cleanup-workspace' && workspaceId) {
        try {
          const resp = await apiRequest(`/api/workspaces/${encodeURIComponent(workspaceId)}/cleanup`, {
            method: 'POST',
            body: JSON.stringify({}),
          });
          if (version !== state.contextVersion) return;
          state.inspectorBannerByWorkspace.set(workspaceId, {
            variant: 'success',
            title: '工作区已清理',
            message: resp?.message || '已安全清理合入且干净的隔离工作区目录。',
          });
          if (resp?.workspace) {
            upsertWorkspace(resp.workspace);
          } else {
            const ws = state.workspaces.find((item) => String(item.id) === String(workspaceId));
            if (ws) upsertWorkspace({ ...ws, status: 'cleaned' });
          }
          showToast('工作区清理成功', '已安全移除已合入且无残留修改的工作区目录。', 'success');
        } catch (err) {
          const isConflict = err.status === 409;
          if (version !== state.contextVersion) return;
          state.inspectorBannerByWorkspace.set(workspaceId, {
            variant: isConflict ? 'warning' : 'danger',
            title: isConflict ? '清理被安全守卫拦截（HTTP 409）' : `清理失败（HTTP ${err.status || '错误'}）`,
            message: isConflict
              ? `工作区尚未完成合入或存在未提交变更，服务端拒绝清理以保护代码：${err.message}`
              : err.message,
          });
          showToast(
            isConflict ? '安全守卫拦截清理（HTTP 409）' : '清理工作区失败',
            err.message,
            isConflict ? 'warning' : 'error',
            7000
          );
        }
      } else if (action === 'open-explorer' && workspaceId) {
        const resp = await apiRequest(`/api/workspaces/${encodeURIComponent(workspaceId)}/open-explorer`, {
          method: 'POST',
          body: JSON.stringify({}),
        });
        if (version !== state.contextVersion) return;
        showToast('已打开本地目录', resp?.path || '已在资源管理器中打开隔离工作区目录。', 'success', 3000);
      }
    } catch (err) {
      if (version !== state.contextVersion) return;
      showToast('操作执行失败', err.message, 'error');
    } finally {
      if (version !== state.contextVersion) return;
      state.actionBusy = false;
      renderWorkers();
      renderInspector();
    }
  }

  async function selectWorkerAndOpenInspector(workerId) {
    if (!workerId) return;
    state.selectedWorkerId = workerId;
    setInspectorOpen(true);
    renderWorkers();
    renderInspector();
    el.inspectorTitle.focus();

    const worker = state.workers.find((w) => String(w.id) === String(workerId));
    const ws = getWorkerWorkspace(worker);
    if (ws && ws.status !== 'cleaned') {
      await loadWorkspaceDiff(ws.id);
    }
  }

  // ---------- 事件绑定 ----------

  function bindEvents() {
    el.overviewContent.addEventListener('toggle', (evt) => {
      const detail = evt.target;
      if (!detail.dataset.overviewDetail || !detail.isConnected) return;
      if (detail.open) state.overviewExpanded.add(detail.dataset.overviewDetail);
      else state.overviewExpanded.delete(detail.dataset.overviewDetail);
    }, true);
    el.overviewContent.addEventListener('click', (evt) => {
      const target = evt.target.closest('button');
      if (!target) return;
      if (target.dataset.view) switchTab(target.dataset.view);
      else if (target.dataset.openSeq) jumpToSeqRow(target.dataset.openSeq);
      else if (target.dataset.workerId) selectWorkerAndOpenInspector(target.dataset.workerId);
    });
    el.boardFeed.addEventListener('toggle', (evt) => {
      const details = evt.target;
      if (!details.matches('.message-details') || !details.isConnected) return;
      const seq = details.dataset.messageSeq;
      if (details.open) {
        state.expandedMessages.add(seq);
        const msg = state.messages.find((m) => String(m.seq) === seq);
        details.querySelector('.md-body').innerHTML = renderMarkdown(msg.body_md || msg.summary || '（正文为空）');
      } else {
        state.expandedMessages.delete(seq);
        details.querySelector('.md-body').innerHTML = '';
      }
    }, true);
    el.themeToggleBtn.addEventListener('click', () => {
      applyTheme(state.theme === 'dark' ? 'light' : 'dark');
    });

    el.toggleInspectorBtn.addEventListener('click', () => {
      const nextOpen = !state.inspectorOpen;
      setInspectorOpen(nextOpen);
      if (nextOpen) {
        renderInspector();
        const worker = state.workers.find((w) => String(w.id) === String(state.selectedWorkerId));
        const ws = getWorkerWorkspace(worker);
        if (ws && ws.status !== 'cleaned' && !state.diffByWorkspace.has(ws.id)) {
          loadWorkspaceDiff(ws.id, { silent: true });
        }
      }
    });

    el.closeDrawerBtn.addEventListener('click', () => setInspectorOpen(false));
    el.drawerBackdrop.addEventListener('click', () => setInspectorOpen(false));

    el.tabButtons.forEach((btn) => {
      btn.addEventListener('click', () => {
        const tab = btn.getAttribute('data-tab');
        if (tab) switchTab(tab);
      });
      btn.addEventListener('keydown', (evt) => {
        const index = el.tabButtons.indexOf(btn);
        const next = { ArrowRight: (index + 1) % el.tabButtons.length, ArrowLeft: (index + el.tabButtons.length - 1) % el.tabButtons.length, Home: 0, End: el.tabButtons.length - 1 }[evt.key];
        if (next === undefined) return;
        evt.preventDefault();
        switchTab(el.tabButtons[next].getAttribute('data-tab'));
      });
    });

    el.projectSelect.addEventListener('change', async () => {
      await loadRuns(el.projectSelect.value || null);
    });

    el.runSelect.addEventListener('change', async () => {
      const nextRunId = el.runSelect.value || null;
      if (nextRunId) {
        await selectRun(nextRunId);
      } else {
        state.selectedRunId = null;
        clearRunState();
      }
    });

    el.refreshSnapshotBtn.addEventListener('click', async () => {
      checkHealth();
      await loadProjects();
    });

    el.exportMenuBtn.addEventListener('click', (evt) => {
      evt.stopPropagation();
      if (el.exportMenu.classList.contains('hidden')) openExportMenu();
      else closeExportMenu(true);
    });
    el.exportMenuBtn.addEventListener('keydown', (evt) => {
      if (!['Enter', ' ', 'ArrowDown', 'ArrowUp'].includes(evt.key)) return;
      evt.preventDefault();
      openExportMenu(evt.key === 'ArrowUp');
    });
    el.exportMenu.addEventListener('keydown', (evt) => {
      if (evt.key === 'Tab') {
        closeExportMenu(true);
        return;
      }
      const items = Array.from(el.exportMenu.querySelectorAll('[role="menuitem"]:not(:disabled)'));
      const index = items.indexOf(document.activeElement);
      const next = { ArrowDown: (index + 1) % items.length, ArrowUp: (index + items.length - 1) % items.length, Home: 0, End: items.length - 1 }[evt.key];
      if (next === undefined) return;
      evt.preventDefault();
      items[next].focus();
    });

    el.exportMdBtn.addEventListener('click', () => handleExport('md'));
    el.exportJsonBtn.addEventListener('click', () => handleExport('json'));

    document.addEventListener('click', (evt) => {
      if (!el.exportDropdown.contains(evt.target)) {
        closeExportMenu();
      }
    });

    document.addEventListener('keydown', (evt) => {
      if (evt.key === 'Escape') {
        if (!el.exportMenu.classList.contains('hidden')) {
          evt.preventDefault();
          closeExportMenu(true);
        } else if (state.inspectorOpen) {
          evt.preventDefault();
          setInspectorOpen(false);
        }
      } else if (evt.key === 'Tab' && state.inspectorOpen && window.innerWidth <= 1080) {
        const items = Array.from(el.inspectorDrawer.querySelectorAll('button:not(:disabled), [href], input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [tabindex]:not([tabindex="-1"])'))
          .filter((item) => !item.closest('.hidden') && item.getClientRects().length > 0);
        const index = items.indexOf(document.activeElement);
        evt.preventDefault();
        items[index < 0 ? (evt.shiftKey ? items.length - 1 : 0) : (index + (evt.shiftKey ? -1 : 1) + items.length) % items.length].focus();
      }
    });
    window.addEventListener('resize', syncInspectorMode);

    el.workersList.addEventListener('click', async (evt) => {
      const row = evt.target.closest('.member-row');
      if (!row) return;
      const workerId = row.getAttribute('data-worker-id');
      if (workerId) {
        await selectWorkerAndOpenInspector(workerId);
      }
    });

    el.channelTabs.addEventListener('click', (evt) => {
      const tab = evt.target.closest('.ch-pill');
      if (!tab) return;
      state.activeChannel = tab.getAttribute('data-channel') || 'all';
      renderChannelTabsOnly();
      renderBoardFeed();
    });

    el.mentionFilterSelect.addEventListener('change', () => {
      state.mentionFilter = el.mentionFilterSelect.value || '';
      renderBoardFeed();
    });

    el.boardSearchInput.addEventListener('input', () => {
      state.searchQuery = el.boardSearchInput.value || '';
      renderBoardFeed();
    });

    el.autoScrollToggle.addEventListener('change', () => {
      state.autoScroll = el.autoScrollToggle.checked;
      if (state.autoScroll) {
        scrollFeedToBottom();
      }
    });

    el.jumpLatestBtn.addEventListener('click', () => {
      scrollFeedToBottom();
    });

    el.boardFeed.addEventListener('scroll', () => {
      if (isFeedNearBottom()) {
        el.jumpLatestBtn.classList.add('hidden');
      }
    });

    el.boardFeed.addEventListener('click', (evt) => {
      const seqRef = evt.target.closest('[data-jump-seq]');
      if (!seqRef) return;
      const seq = seqRef.getAttribute('data-jump-seq');
      if (seq) jumpToSeqRow(seq);
    });

    el.humanChannelSelect.addEventListener('change', () => {
      const isCustom = el.humanChannelSelect.value === '__custom__';
      el.humanChannelCustom.classList.toggle('hidden', !isCustom);
      if (isCustom) {
        el.humanChannelCustom.focus();
      }
    });

    el.humanMessageInput.addEventListener('keydown', (evt) => {
      if ((evt.ctrlKey || evt.metaKey) && evt.key === 'Enter') {
        evt.preventDefault();
        el.humanMessageForm.requestSubmit();
      }
    });

    el.humanMessageForm.addEventListener('submit', handleHumanSubmit);

    el.refreshDiffBtn.addEventListener('click', async () => {
      const worker = state.workers.find((w) => String(w.id) === String(state.selectedWorkerId));
      const ws = getWorkerWorkspace(worker);
      if (ws) {
        await loadWorkspaceDiff(ws.id);
      }
    });

    el.inspectorBody.addEventListener('click', (evt) => {
      const btn = evt.target.closest('[data-action]');
      if (!btn || btn.disabled) return;
      const action = btn.getAttribute('data-action');
      runInspectorAction(action, btn);
    });

    el.graphPlayBtn.addEventListener('click', () => {
      if (state.graphPlaying) {
        stopGraphReplay();
      } else {
        startGraphReplay();
      }
    });

    el.graphSlider.addEventListener('input', () => {
      stopGraphReplay();
      const val = Number(el.graphSlider.value);
      state.graphStep = val >= state.messages.length ? null : val;
      renderGraph();
    });

    el.graphHideInactiveToggle.addEventListener('change', () => {
      state.graphHideInactive = el.graphHideInactiveToggle.checked;
      renderGraph();
    });

    el.graphExpandAllToggle.addEventListener('change', () => {
      state.graphExpandAll = el.graphExpandAllToggle.checked;
      renderGraph();
    });

    const selectGraphNode = (evt) => {
      const nodeEl = evt.target.closest('.graph-node');
      if (!nodeEl) {
        if (state.graphSelectedNode !== null) {
          state.graphSelectedNode = null;
          applyGraphHighlightInPlace();
        }
        return;
      }
      const nodeId = nodeEl.getAttribute('data-node-id');
      state.graphSelectedNode = state.graphSelectedNode === nodeId ? null : nodeId;
      applyGraphHighlightInPlace();
    };
    el.graphCanvasHost.addEventListener('click', selectGraphNode);
    el.graphCanvasHost.addEventListener('keydown', (evt) => {
      if ((evt.key === 'Enter' || evt.key === ' ') && evt.target.closest('.graph-node')) {
        evt.preventDefault();
        evt.stopPropagation();
        selectGraphNode(evt);
      }
    });

    el.graphCanvasHost.addEventListener('mouseover', (evt) => {
      const nodeEl = evt.target.closest('.graph-node');
      const nextHover = nodeEl ? nodeEl.getAttribute('data-node-id') : null;
      if (nextHover !== state.graphHoverNode) {
        state.graphHoverNode = nextHover;
        applyGraphHighlightInPlace();
      }
    });

    el.graphCanvasHost.addEventListener('mouseleave', () => {
      if (state.graphHoverNode !== null) {
        state.graphHoverNode = null;
        applyGraphHighlightInPlace();
      }
    });

    el.graphDetailBar.addEventListener('click', async (evt) => {
      const btn = evt.target.closest('[data-graph-action]');
      if (!btn) return;
      const action = btn.getAttribute('data-graph-action');
      if (action === 'filter-board') {
        const nodeId = btn.getAttribute('data-node-id') || '';
        state.mentionFilter = nodeId;
        el.mentionFilterSelect.value = nodeId;
        switchTab('board');
        renderBoardFeed();
      } else if (action === 'inspect-worker') {
        const workerId = btn.getAttribute('data-worker-id');
        if (workerId) {
          await selectWorkerAndOpenInspector(workerId);
        }
      }
    });

    window.addEventListener('beforeunload', () => {
      closeEventSource();
      stopGraphReplay();
    });
  }

  // ---------- 启动入口 ----------

  async function init() {
    initTheme();
    renderModeLinks();
    el.modeChooser.classList.toggle('hidden', Boolean(PAGE_MODE));
    el.dashboardShell.classList.toggle('hidden', !PAGE_MODE);
    if (!PAGE_MODE) {
      const params = new URLSearchParams(window.location.search);
      const runId = params.get('run_id');
      if (!runId) return;
      el.modeChooserStatus.textContent = '正在打开任务所属的模式入口…';
      try {
        const run = await resolveRun(runId, params.get('project_id'), null);
        if (!run) return;
        params.set('project_id', run.project_id);
        params.set('run_id', run.id);
        window.location.replace(`/${run.mode}?${params}`);
      } catch (err) {
        el.modeChooserStatus.textContent = `打开失败：${err.message}。请检查链接，或选择下方入口。`;
        el.modeChooserStatus.classList.add('is-error');
      }
      return;
    }
    document.title = `${MODE_NAMES[PAGE_MODE]} — 三重编排器`;
    el.modeDescription.textContent = MODE_DESCRIPTIONS[PAGE_MODE];
    bindEvents();
    checkHealth();
    try {
      await loadProjects();
    } catch (err) {
      showToast('加载项目列表失败', err.message, 'error');
    }

    setInterval(checkHealth, HEALTH_POLL_MS);
    setInterval(tickHeartbeats, HEARTBEAT_TICK_MS);
    setInterval(tickHostObservations, HEARTBEAT_TICK_MS);
  }

  init();
})();
