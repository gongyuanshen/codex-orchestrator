# 三重编排器 (Tri Orchestrator)

面向 Codex 的自包含多代理编排插件，提供 **`$boost`**（极速模式）、**`$teamwork`**（协同模式）、**`$swarm`**（蜂窝模式）三种显式工作流，底层由本地单例 HTTP/SSE 守护进程、SQLite WAL `#seq` 消息板、`git clone --local` / 三方目录快照隔离工作区（含非破坏性冲突预检）以及极简中文 Web 控制台（`http://127.0.0.1:18790`）提供支撑。

## Route A 开发版（0.2.0，尚未切换生产安装）

正式服务新增可靠收件箱、版本绑定的候选／集成验收、步骤检查点和最多3轮自动返工。原生代理仍由根协调者投递、唤醒和中断；消息回执不是独立验收。

- 新建任务采用结构化流程1；迁移前任务保留流程0及历史语义，不补造PASS。
- 27个统一MCP工具中，新增 `inbox_send/get/ack/wait`、`workflow_step_save/advance`、`artifact_capture`、`verification_record`。严格执行顺序见 [共享工作流合同](plugin/skills/route-a-workflow.md)。
- 文件或任务包变化会使旧验收失效。工作区必须通过候选门禁后合入，并通过集成门禁后清理；中途失败必须显式核对，不自动重放。
- 所有模式仍共用18790和各自入口。登记状态、实际宿主观察及观察过期明确分开；心跳不会自动证明模型仍在运行。
- `scripts/upgrade.py` 提供 `prepare/apply/verify/rollback` 的受控离线包辅助流程：所有模式无活动任务、服务停止和备份核验后才允许写入。它不会修改Codex注册表配置、停止服务、重命名版本化缓存或假装完成宿主安装激活。旧版缓存不得直接伪装成0.2.0。
- 一旦新版产生真实新数据，拒绝用旧备份覆盖回滚，保留数据进行前向修复。需要刷新桌面MCP时由用户手动完成。

开发和隔离验证记录：[执行台账](docs/superpowers/plans/2026-11-16-route-a-execution.md)。安装版仍保持0.1.2，待所有模式空闲后另行切换；第二批模式专项增强尚未声明交付。

仓库保留开发计划和验证摘要；本地运行数据库、配置快照、原始验证日志和备份不纳入仓库。

## 前置依赖

- **Codex CLI**（支持插件机制的 `codex`）
- **[`uv`](https://docs.astral.sh/uv/)**（在插件缓存目录中自动管理 Python `>=3.12` 独立运行时）
- **`git`**（用于本地克隆工作区隔离；非 Git 目录使用三方目录快照）

---

## 安装指南

### 方式 A — 零脚本 GitHub 开箱即用安装

在 Windows、macOS 或 Linux 上直接通过 Codex CLI 从 GitHub 安装，无需运行任何本地脚本：

```bash
codex plugin marketplace add gongyuanshen/codex-orchestrator
codex plugin add tri-orchestrator@tri-orchestrator
```

安装后将自动完成以下过程：
- Codex 会将当前 `0.2.0` 开发版的 `./plugin/` 缓存至 `~/.codex/plugins/cache/tri-orchestrator/tri-orchestrator/0.2.0/`。
- 首次触发 MCP 时，Codex 在插件缓存目录内自动执行 `uv run --quiet --project . python -m orchestrator.mcp_shim`，安装运行时依赖（`mcp`、`starlette`、`uvicorn`、`httpx`）并拉起后台单例守护进程。
- 若 `~/.codex/agents/*.toml` 中未安装专用角色配置，`$boost`、`$teamwork` 与 `$swarm` 会自动回退为 `default` 子代理并在提示词中内联角色契约。

### 方式 B — 可选完整角色配置与缓存预热

克隆本仓库后运行平台安装脚本，可额外获得：
1. 将全部 6 个专用角色配置（`plugin/agents/*.toml`，预设了调优后的 `model_reasoning_effort` 与 `sandbox_mode`）安装至 `~/.codex/agents/`（始终继承当前 Codex 会话模型，不硬编码任何模型名）；
2. 创建带时间戳备份与基线备份后，将 `[agents]` 并发限制（`max_threads = 6`、`max_depth = 2`）安全合并至 `~/.codex/config.toml`，并通过 `codex --strict-config` 校验；
3. 在 Codex 插件缓存目录（`~/.codex/plugins/cache/...`）中预热 `uv sync --no-dev`，同时不修改仓库内任何受追踪文件。

**Windows（PowerShell）：**

```powershell
powershell -ExecutionPolicy Bypass -File scripts/install.ps1
```

可选 Windows 端到端验证：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/verify_e2e.ps1
```

**macOS / Linux（Bash）：**

```bash
bash scripts/install.sh
```

---

## 工作流（`$boost`、`$teamwork`、`$swarm`）

| 技能指令 | 编排定位 | 适用场景 |
| :--- | :--- | :--- |
| **`$boost`** | 自适应单目标极速执行：智能路由单兵直达或按需委派、逐字锁定需求、独立 `verifier` 验收与主协调者抽检，以及同线程返工闭环。 | 聚焦型修复、小中型功能或局部重构（1–2 文件单兵或定向调度 `investigator` / `coder` / `verifier`）。 |
| **`$teamwork`** | 结构化端到端协同交付流水线：需求访谈澄清（`interviewer`，支持 `[skip-interview]` 跳过）、`xhigh` 架构规划与互斥 `file_ownership` 文件所有权矩阵（`planner`）、隔离工作区并行开发（`coder`）、两阶段验收（`verifier`）、同线程返工与顺序合入。 | 需严格冻结需求规格且零文件写冲突的中大型多模块研发任务。 |
| **`$swarm`** | 基于 SQLite `#seq` 频道（`main`、`#backend`、`#ui`、`#verify` 等）的多成员蜂窝并发协同、增量游标 `board_read(since_seq)`、防上下文膨胀的一行 `#seq` 回执、看门狗心跳卡死检测与 Web 实时人工干预。 | 需要成员间点对点交接与实时可观测性的大型并行工作流。 |

---

### 工作区模式与依赖边界

- `mode="git_clone"` 才要求在分配的分支提交、报告真实 commit SHA，并在验收/合并前保持 Git 工作树干净。`mode="snapshot"` 的 `branch_name=None` 正常；合并前 `dirty=true` 表示相对基线有改动，应检查 `workspace_diff`、文件所有权和真实测试输出，不要求提交或初始化 Git。
- 两种模式都先 `workspace_merge_preview` 再 `workspace_merge(strategy="merge")`；snapshot 执行三方目录比较与应用。合入后完成主目录集成验证，再由 `workspace_cleanup` 按对应模式核验已合入且工作区未新增改动，不能用 Git 的干净工作树要求阻断快照验收。
- `dep_mode="none"` 不配置依赖目录。需要独立依赖文件时使用 `copy`；`fresh_venv_uv` 创建新的 `.venv`，依赖包需在其中安装，不负责创建 `node_modules`。
- `link_ro_cache` 的名称保留兼容，实际使用目录链接/硬链接（无法链接时复制），是**共享可写**依赖，不是只读或写入隔离。经链接写入可能修改源项目依赖；需隔离依赖写入时使用 `copy` 或 `fresh_venv_uv`。
- `$boost` Solo 验收失败由协调者本人修复，再交同一个 verifier；Delegation 则交原 coder 返工，再交原 verifier。静态技能契约测试只检查文档约束，不证明原生代理已执行完整闭环。

## 架构概览

```text
Codex CLI / Subagents
   │  stdio JSON-RPC（纯净 stdout）
   ▼
uv run --quiet --project . python -m orchestrator.mcp_shim
   │  daemon.lock 互斥锁（msvcrt / fcntl）-> 自动拉起唯一后台守护进程
   ▼
单例 HTTP/SSE 守护进程（127.0.0.1:18790）
   ├── /api/rpc & /api/tools   ──► RpcDispatcher（27 个 MCP 工具）
   ├── /api/events             ──► EventBus（SSE 实时事件流）
   ├── /                     ──► Boost / Teamwork / Swarm 入口选择
   ├── /boost, /teamwork, /swarm ──► 各模式控制台（共用本地静态资源）
   ├── Watchdog Loop           ──► 成员心跳超时卡死告警
   ├── SQLite WAL Engine       ──► projects、runs、task_packets、workers、
   │                               board_messages（#seq）、read_cursors、workspaces
   └── WorkspaceManager        ──► .swarm-workspaces/<run>-<worker>
                                   （git clone --local / 三方快照 + 端口池）
```

### 仓库目录结构

```text
.
├── .agents/plugins/marketplace.json   # Codex 插件市场目录（指向 ./plugin）
├── plugin/                            # 自包含可分发插件包
│   ├── .codex-plugin/plugin.json      # Codex 插件元数据、技能与 MCP 覆盖声明
│   ├── .mcp.json                      # Codex MCP 覆盖配置（env_vars、超时设置）
│   ├── plugin.json                    # Agent Plugins 清单（displayName: 三重编排器）
│   ├── mcp.json                       # Agent Plugins MCP 配置（${PLUGIN_ROOT}）
│   ├── pyproject.toml & uv.lock       # 仅含运行时依赖的 Python 包配置（>=3.12）
│   ├── agents/*.toml                  # 6 个角色配置（interviewer, planner, investigator, coder, verifier, swarm_worker）
│   ├── assets/{icon.svg,logo.svg}     # 插件图标资源
│   ├── skills/{boost,teamwork,swarm}/ # 显式工作流技能（含 default 子代理回退契约）
│   └── src/orchestrator/              # 守护进程、MCP shim、SQLite/消息板、工作区管理、看门狗与 Web 控制台
├── scripts/                           # 可选角色安装脚本、Windows 卸载与 E2E 验证脚本
├── tests/                             # 单元与集成测试套件
└── pyproject.toml & uv.lock           # 仓库根开发/测试配置（可编辑安装 plugin + pytest）
```

---

## MCP 工具（27 个）

| 分类 | 工具名 | 功能说明 |
| :--- | :--- | :--- |
| **运行管理** | `run_create` | 创建 `boost` / `teamwork` / `swarm` 运行实例并自动注册 `project_root`。 |
| | `run_update` | 更新运行状态（`active`、`completed`、`failed`、`archived`）、`title` 或 `metadata`。 |
| | `run_list` | 按 `project_id`、`status` 或 `mode` 筛选并列出运行记录。 |
| **任务契约** | `task_packet_save` | 持久化单调递增版本的任务契约（`goal`、`file_ownership`、`acceptance_criteria`）。 |
| | `task_packet_get` | 获取指定运行最新（或指定 `version`）的任务契约。 |
| **消息板 (`#seq`)** | `board_post` | 发布消息、差异或测试报告至指定频道（`main`、`#verify` 等），返回紧凑指针 `{seq, channel, mentions, summary}`。 |
| | `board_read` | 按 `since_seq` 或 `reader_name` 游标增量读取消息（支持 `advance_cursor=true`）。 |
| | `board_status` | 返回全员状态、心跳延迟、卡死标记、频道未读计数及最新任务契约。 |
| | `board_channel_list` | 列出当前运行的活跃频道及消息计数。 |
| **成员管理** | `worker_register` | 绑定真实 Codex `agent_id` 并注册或更新成员节点（`interviewer`、`planner`、`investigator`、`coder`、`verifier`、`swarm_worker`）。 |
| | `worker_heartbeat` | 刷新成员心跳时间戳并清除 `stalled_alerted` 告警标记。 |
| | `worker_status_update` | 变更成员状态（`waiting`、`working`、`stopped`、`killed`）。 |
| | `worker_control_ack` | 协调者执行原生中断后，提交带真实结果的 applied/failed 回执；只有 applied 原子更新成员状态。 |
| **隔离工作区** | `workspace_create` | 在 `.swarm-workspaces/` 下创建隔离的 `git clone --local`（或快照）工作区并分配测试端口（`18800..18999`）。 |
| | `workspace_status` | 返回工作区脏标记、领先提交数及变更文件列表。 |
| | `workspace_diff` | 返回文件变更状态及统一 Diff 文本。 |
| | `workspace_merge_preview` | 非破坏性合并冲突预检（`git merge-tree --write-tree` 或三方快照），不改动主仓库。 |
| | `workspace_merge` | Git 模式合并/cherry-pick 已提交的干净分支；snapshot 模式按基线执行三方目录合并，冲突不写入。 |
| | `workspace_cleanup` | 核验已成功合入且工作区无合入后的新增改动；按 Git 提交证据或 snapshot 基线检查，再删除工作区目录。 |

---

## 中文 Web 控制台（`http://127.0.0.1:18790`）

### 任务概览与按需详情

首页只提供三个独立入口，共用一个服务和数据库，不额外占用端口：

- [`/boost`](http://127.0.0.1:18790/boost)：聚焦单个目标，直接执行或按需委派，再独立验收。
- [`/teamwork`](http://127.0.0.1:18790/teamwork)：先澄清与规划，再按模块分工开发和验收。
- [`/swarm`](http://127.0.0.1:18790/swarm)：多成员并行，通过频道交接与汇总进展。

每个入口只列出对应模式的任务，默认先展示任务概览：任务状态、成员职责与已登记工作、最近动态。顶部“项目 / 任务”用于选择工作目录及其中的一次编排记录；无指定上下文时优先选择本模式进行中的任务。日志全文按需展开，协作图、工作区检查和人工消息入口仍保留。

旧版 `/?project_id=...&run_id=...` 或仅带 `run_id` 的链接会按任务实际模式跳转；超过最近 100 条的历史任务也可通过链接打开。指定错误的模式、项目或不存在的任务会明确报错，不会自动替换成另一任务。切换模式使用独立页面导航，项目内无本模式任务时显示空状态。当前任务的实时更新仍由 SSE 接收；新建任务通过“立即同步”刷新列表。模式入口是展示分区，不是权限、进程或存储隔离。

2026-11-16 已在 Swarm 全部结束后通过官方插件安装流程更新到 **0.1.2**，合并此前工作流、工作区与概览修复及三个独立入口。安装版、数据迁移与真实浏览器验收见 [安装验证记录](docs/verification/installed-20261116/report.md)；此前源码隔离证据保留在 `docs/verification/mode-entries-20261116/report.md`。

成员状态是协调者登记的状态，不是对原生代理进程的实时探测。恢复旧任务时，应显式重新登记成员及当前工作；发布消息本身不恢复成员状态。

摘要快照与 `board_status` 不返回内部文件哈希清单，摘要快照不扫描工作目录。未执行详细检查的文件变更数显示为“未检查”，不等于零变更；需要时打开工作区详情读取差异。完整 manifest 仍保存在数据库中供差异与合并使用。

源码验证、运行审计及安装生效边界见 `docs/verification/dashboard-20261116/audit.md`。

MCP shim 拉起守护进程后，可在浏览器打开 **`http://127.0.0.1:18790`** 使用 Antigravity 极简风格中文控制台：

- **项目/任务选择与浅/深色主题**：切换仓库项目与运行实例，查看编排模式、健康状态与 SSE 连接状态；支持浅/深色主题、一键同步与导出 `.md` / `.json` 运行报告。
- **`消息板` 视图**：支持按频道（`全部` / `主频道` / `人工` / 自定义频道）、`@提及` 与关键词检索过滤连续消息流（含转义安全的 Markdown 渲染、跳至最新与自动滚动开关），并在底部提供悬浮人工干预输入框向 `human` 频道实时发送指令（`POST /api/runs/{run_id}/human-message`）。
- **`成员` 视图**：顶部全宽展示冻结的任务契约目标、验收标准与互斥文件所有权矩阵；下方按活跃与已结束分组显示成员图标、职责角色、1 秒级心跳延迟、卡死告警、分配端口与未合入文件数。
- **`协作图` 视图**：基于 `#seq` 消息与成员动态生成的交互式 SVG 拓扑协作图，支持时间轴滑块与自动回放（`▶` / `⏸`）、隐藏未活跃成员及 `@all` 广播边展开，点击或悬停节点可高亮关联协作路径。
- **侧滑工作区检查器（`工作区检查`）**：点击成员行或顶栏开关即可滑出右侧检查器，查看工作区元数据、文件变更列表及统一 Diff，并严格限定 4 类安全操作（合并冲突预检、打开本地目录、请求暂停/结束任务，等待协调者执行并回执，保留文件、仅针对已合入且干净工作区的清理与 HTTP `409` 拦截）。危险操作端点（`git push`、`reset`、未合入强制删除）均被显式禁止并返回 `HTTP 403`。

### 运行时环境变量（`TRI_ORCH_*`）

| 变量名 | 默认值 | 说明 |
| :--- | :--- | :--- |
| `TRI_ORCH_HOST` | `127.0.0.1` | 守护进程监听主机。 |
| `TRI_ORCH_PORT` | `18790` | 守护进程 HTTP/SSE 端口。 |
| `TRI_ORCH_DATA_DIR` | `%LOCALAPPDATA%\tri-orchestrator`（Windows）<br>`~/.local/share/tri-orchestrator`（POSIX） | 运行时数据目录，存放 `state.db`、`daemon.lock`、`daemon.pid.json`、`shim.log` 与 `daemon.log`。 |
| `TRI_ORCH_STALL_SEC` | `300` | 默认成员心跳卡死告警阈值（秒）。 |
| `TRI_ORCH_WATCHDOG_INTERVAL_SEC` | `15` | 看门狗后台巡检间隔（秒）。 |
| `TRI_ORCH_PORT_RANGE` | `18800..18999` | 分配给隔离工作区的测试端口池范围。 |

---

## 开发与测试

```bash
uv run pytest -v
```

---

## 卸载指南

### 方式 A — 零脚本卸载

```bash
codex plugin remove tri-orchestrator@tri-orchestrator
codex plugin marketplace remove tri-orchestrator
```

### 方式 B — Windows 脚本卸载

自动停止正在运行的守护进程、移除插件与市场注册信息、清理 `~/.codex/agents/` 中的 6 个角色 TOML（若存在安装前基线文件则自动还原），并支持按需恢复 `config.toml` 备份或清理运行时数据目录：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/uninstall.ps1
# 可选参数：
# powershell -ExecutionPolicy Bypass -File scripts/uninstall.ps1 -RestoreConfigBackup -RemoveDataDir
```

### macOS / Linux 清理（若曾使用 `scripts/install.sh`）

先执行上方两条 `codex plugin ...` 移除命令，随后按需删除 `~/.codex/agents/` 下的 6 个角色文件（`interviewer.toml`、`planner.toml`、`investigator.toml`、`coder.toml`、`verifier.toml`、`swarm_worker.toml`），并在停止 `daemon.pid.json` 对应进程后清理 `~/.local/share/tri-orchestrator`。

## 许可证

MIT


## 控制与连接语义

- 控制台的暂停/结束任务是**请求**，HTTP 202 仅表示已入队。界面显示“待协调者执行”，不提前显示停止成功。未绑定真实 Codex 代理的历史成员不能请求控制。
- 协调者从 `board_status.control_requests` 读取请求，使用原生中断工具后再 `worker_control_ack`。失败保留原因，成员状态不变；重启不会丢失请求。结束任务表示中断并停止继续派发该成员，不表示杀死 Codex 或操作系统进程。
- 协调者在工具边界和最长 30 秒的等待前后检查请求；协调者离线、模型断网或执行长工具时仍可能延迟，界面会保持待处理，不自动伪造回执。
- 人工文字消息只写入消息板，不会自动投递给原生代理。`$boost` 协调者须在派发/返工前、等待前后和收尾前，通过 `board_read(reader_name="coordinator", advance_cursor=False)` 读取，分别记录原始 `#seq` 的收到、拟采取行动、实际转发与后续完成证据；批次处置已记载后，再按相同 `since_seq` 和非空批次数量以 `advance_cursor=True` 推进持久游标。中断恢复时先核对收据和原生状态，不保证 exactly-once；游标前进或转发成功不等于任务完成。
- 原生派发前的成员登记为待命；成功绑定并实际派发后才记为工作中。原生线程完成后，仍待审查/返工的成员记为待命；整个工作流收尾才记为已停止。状态来自协调者明确报告，不是后台直接读取 Codex 内部数据库。
- 已停止/已终止成员和已结束运行不显示当前卡住告警；历史告警留在消息板。迟到心跳不能恢复已暂停或已终止的成员。
- MCP 转发不重放结果未知的写请求。读超时后先核对原操作是否落地，不盲目重试。健康检查异常或已有存活但不健康实例时明确报错，不再据此拉起第二个 daemon。失败启动不得覆盖正常实例的 PID 文件。
- 插件连接错误不会被伪装成模型连接错误，也不会自动绕过 MCP 直写数据库。Codex 模型网络、代理配置不由此插件修改。

本轮实现与验证记录见 `docs/superpowers/plans/2026-11-15-reliable-control.md`。


## Large result delivery

Codex stdio is a bounded message transport, not a bulk-file channel. Small MCP results keep their original JSON schema. When the final encoded tool response exceeds the inline budget, the shim atomically stores the **complete original JSON result** under the runtime data directory's `results/` folder and returns an explicit `delivery: "file"` receipt with the absolute result path, byte count and SHA-256. This is not silent truncation. Read the local result file; do not replay a completed write tool call. File creation errors are reported as tool errors, not success.

The browser HTTP API keeps its existing payload contract. No model/proxy settings or workspace files are changed by result delivery. Result files are retained for audit and can consume disk space; this release does not automatically delete them. Binary diff detection scans in bounded chunks before text decoding, and existing binary files remain visible in the diff listing.
