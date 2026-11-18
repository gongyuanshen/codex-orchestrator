# 三重编排器 (Tri Orchestrator)

面向 Codex 的自包含多代理编排插件，提供 **`$boost`**（极速模式）、**`$teamwork`**（协同模式）、**`$swarm`**（蜂窝模式）三种交互式原生 V2 子代理工作流，以及 **`$pipeline`**（零中继税 App-Server 自动闭环 DAG 管线工作流），配套生命周期钩子（`SessionStart`、`PostCompact`、`UserPromptSubmit`、`Stop`），底层由本地单例 HTTP/SSE 守护进程、SQLite WAL `#seq` 消息板、`git clone --local` / 三方目录快照隔离工作区（含非破坏性冲突预检）以及极简中文 Web 控制台（`http://127.0.0.1:18790`）提供支撑。

## 0.6.0 更新

- **原子宏工具与首轮直达（`workflow5` / `schema 8`）：** 新增 `workflow_prepare_dispatch`、`native_action_record_call`、`workflow_author_turn`、`workflow_verifier_turn`、`workflow_closeout_run` 与 `session_bind`，将工作区创建、派发登记、作者接收/提交、双层验收与全流程收尾压缩为原子事务，消除子代理首轮 `waiting-only` 空转，并将 `board_status(compact=True)` 轮询载荷缩减约 74%–82%。
- **确定性 DAG 管线（`$pipeline` / `tri-orchestrator-pipeline`）：** 新增宿主终端自动闭环管线运行器，通过独立隔离的 `codex app-server --listen stdio://` 驱动多模块并发/串行切片、双层真实验收、最多 3 轮同线程自动返工与 `--resume-run` 断点续跑，消除主助手逐轮中继税并支持关闭聊天窗口后继续后台推进。
- **生命周期钩子与单一清单收敛（`tri-orchestrator-hook`）：** 移除旧版根级 `plugin/plugin.json`，统一收敛至 `plugin/.codex-plugin/plugin.json` 并声明 `plugin/hooks/hooks.json`，支持 `SessionStart`、`PostCompact`、`UserPromptSubmit`、`Stop` 四类钩子，实现冷启动本地 `session_id` 注入、上下文压缩后自动恢复与未收尾工作流单次防早退拦截；守护进程离线时静默零副作用退出，绝不自动拉起后台服务。
- **高复杂并发盲审采集（`tri-orchestrator-challenge`）：** 新增宿主侧只读并发盲审采集器，配合 `tri-orchestrator-verify` 为四维高复杂审查（`specification` / `code` / `stress` / `evidence`）生成带 64 位 SHA-256 摘要的硬回执。
- **Codex V2 原生对齐与平滑升级：** 安装脚本自动幂等启用 `multi_agent = true` 与 `multi_agent_v2 = true`，执行 `hooks/list` 四钩子自检，并支持可选 `-TrustPluginHooks` / `--trust-plugin-hooks`；全套提供 **35 个公开 MCP 工具**与 **6 个 CLI 可执行程序**（`tri-orchestrator-{mcp,daemon,verify,hook,challenge,pipeline}`），`schema 7 -> 8` 升级 100% 原值保留全部历史任务与 `workflow0..4` 门禁规则。

发布与验证记录见 [0.6.0 发布记录](docs/verification/github-060-20261118/report.md)、[0.6.0 隔离验收报告](docs/verification/v0.6.0-isolated-acceptance.md)、[0.6.0 二阶段与深度审计验收](docs/verification/v0.6.0-phase2-supplement-acceptance.md)、[M9 生命周期钩子实测](docs/verification/m9-hooks-spike-0.6.0.md) 与 [0.6.0 正式升级回执](docs/verification/v0.6.0-production-upgrade-receipt.md)。

## 0.5.0 历史更新（当前正式版本已升级 0.6.0）

- **可靠派发：** 记录原生代理调用的领取、开始、结果和核对过程，区分“尚未调用”和“结果未知”。
- **真实验收：** 新增命令行采集器 `tri-orchestrator-verify`，保存真实退出码、输出和绑定当前产物的验收回执。
- **协同交接：** 计划准入检查文件冲突，紧凑交接包保留关键状态，结构化审查关联具体发现与返工。
- **更直接的工作流：** Boost 按任务难度选择执行路径；批量消息和停滞分类帮助协调者处理待办。
- **兼容升级：** 新增工作流版本 4，保留旧任务规则；统一提供 29 个 MCP 工具，数据库版本为 7，并修复 Windows 后台进程启动问题。

交互式日常任务仍由主助手通过 Codex 原生 V2 工具创建和控制代理；确定性批量 DAG 任务可由宿主终端 `tri-orchestrator-pipeline` 独立驱动 `codex app-server`。`18790` 守护进程仅提供持久状态、隔离工作区、门禁与观测，绝不暴露任何任意命令执行 RPC，也不会独立接管原生子代理。

历史 0.5.0 发布记录见 [0.5.0 发布记录](docs/verification/github-050-20261117/report.md)。`tri-orchestrator-verify`、`tri-orchestrator-challenge` 与 `tri-orchestrator-pipeline` 均由宿主侧或验收者在终端运行，不通过后台服务执行任意命令。验收命令和工作目录由产物清单冻结，仅白名单环境变量键名（`env_profile`），绝不序列化环境变量值。

## Route A 历史交付（0.3.0；当前正式版本已升级 0.6.0）

正式服务新增可靠收件箱、版本绑定的候选／集成验收、步骤检查点和最多3轮自动返工。原生代理仍由根协调者投递、唤醒和中断；消息回执不是独立验收。

- 新建任务采用结构化流程2；既有流程0/1保留各自合同，不补造PASS。Boost挑战绑定当前产物与原验收者；Teamwork逐里程碑集成及指定接手方确认；Swarm执行消息绑定步骤、任务包、返工轮次与合同。
- 27个统一MCP工具中，新增 `inbox_send/get/ack/wait`、`workflow_step_save/advance`、`artifact_capture`、`verification_record`。严格执行顺序见 [共享工作流合同](plugin/skills/route-a-workflow.md)。
- 文件或任务包变化会使旧验收失效。工作区必须通过候选门禁后合入，并通过集成门禁后清理；中途失败必须显式核对，不自动重放。
- 所有模式仍共用18790和各自入口。登记状态、实际宿主观察及观察过期明确分开；心跳不会自动证明模型仍在运行。
- `scripts/upgrade.py` 提供 `prepare/apply/verify/rollback` 的受控离线包辅助流程：所有模式无活动任务、服务停止和备份核验后才允许写入。它不会修改Codex注册表配置、停止服务、重命名版本化缓存或假装完成宿主安装激活。旧版缓存不得直接伪装成新版本。
- 一旦新版产生真实新数据，拒绝用旧备份覆盖回滚，保留数据进行前向修复。当前旧聊天已在新一轮加载工具并完成真实原生只读调用；这不承诺任意进行中的旧turn/旧shim即时刷新。需要手动刷新时仍由用户操作。

开发和隔离验证记录：[执行台账](docs/superpowers/plans/2026-11-16-route-a-execution.md)。2026-11-17 两批已完成并受控安装0.3.0/schema5；验收及故障恢复见 [第二批交付](docs/verification/modes-20261117/report.md)。三模式真实GUI、旧聊天新turn刷新及后续修复见 [GUI与旧聊天验收](docs/verification/gui-hot-refresh-20261117/report.md)。主分支集成、三模式键盘/实时刷新与工程无障碍收尾见 [最终收尾验收](docs/verification/main-closeout-20261117/report.md)。先前 [0.2.0安装](docs/verification/install-020-20261117/report.md) 与 [安装后收尾](docs/verification/postinstall-small-fixes-20261117/report.md) 作为历史检查点保留。

仓库保留开发计划和验证摘要；本地运行数据库、配置快照、原始验证日志和备份不纳入仓库。

## 前置依赖

- **Codex CLI**（支持插件机制、V2 子代理与生命周期钩子的 `codex`）
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
- Codex 按所选市场版本缓存 `./plugin/`；当前版本为 `0.6.0`，对应缓存 `~/.codex/plugins/cache/tri-orchestrator/tri-orchestrator/0.6.0/`。
- 首次触发 MCP 时，Codex 在插件缓存目录内自动执行 `uv run --quiet --project . python -m orchestrator.mcp_shim`，安装运行时依赖（`mcp`、`starlette`、`uvicorn`、`httpx`）并拉起后台单例守护进程。
- **零脚本角色回退契约：** 若 `~/.codex/agents/*.toml` 中未安装专用角色配置，`$boost`、`$teamwork` 与 `$swarm` 在调用 `spawn_agent` 时会**省略 `agent_type` 参数**（不要传 `agent_type="default"`），按角色档位显式传入 `reasoning_effort`（`planner="xhigh"`，`verifier` / `investigator` / `interviewer` / `challenger="high"`，`coder` / `swarm_worker="medium"`，且绝不硬编码 `model`），并在提示词中内联完整角色契约。

### 方式 B — 可选完整角色配置、V2 开启、钩子自检与缓存预热

克隆本仓库后运行平台安装脚本，可额外获得：
1. 将全部 6 个专用角色配置（`plugin/agents/*.toml`，预设调优后的 `model_reasoning_effort` 与只读角色 `sandbox_mode`）安装至 `~/.codex/agents/`（始终继承当前 Codex 会话模型，不硬编码任何模型名）；
2. 创建带时间戳备份与基线备份后，安全合并 `[agents]` 并发限制（`max_threads = 6`、`max_depth = 2`）与 `[features]` 多代理开关（`multi_agent = true`、`multi_agent_v2 = true`）至 `~/.codex/config.toml`，并通过 `codex --strict-config` 校验；
3. 在 Codex 插件缓存目录（`~/.codex/plugins/cache/tri-orchestrator/tri-orchestrator/0.6.0/`）中预热 `uv sync --no-dev`，生成全部 6 个 `tri-orchestrator-{mcp,daemon,verify,hook,challenge,pipeline}` 可执行入口，同时不修改仓库内任何受追踪文件；
4. 通过 `codex app-server --stdio` 执行 `hooks/list` 自检，核实 4 个生命周期钩子（`sessionStart`、`postCompact`、`userPromptSubmit`、`stop`）已被原生发现。默认保留 `untrusted` 状态供用户在 Codex 桌面端一键点击信任；自动化或纯 CLI 环境可显式附加 `-TrustPluginHooks`（PowerShell）或 `--trust-plugin-hooks`（Bash）将信任哈希写入 `[hooks.state]`。

**Windows（PowerShell）：**

```powershell
powershell -ExecutionPolicy Bypass -File scripts/install.ps1
# 可选：在 CLI 环境下直接信任 4 个插件生命周期钩子
# powershell -ExecutionPolicy Bypass -File scripts/install.ps1 -TrustPluginHooks
```

可选 Windows 端到端验证：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/verify_e2e.ps1
```

**macOS / Linux（Bash）：**

```bash
bash scripts/install.sh
# 可选：在 CLI 环境下直接信任 4 个插件生命周期钩子
# bash scripts/install.sh --trust-plugin-hooks
```

---

## 工作流（`$boost`、`$teamwork`、`$swarm`、`$pipeline`）

| 技能指令 | 编排定位 | 适用场景 |
| :--- | :--- | :--- |
| **`$boost`** | 自适应单目标极速执行：智能路由单兵直达或按需委派、逐字锁定需求、独立 `verifier` 挑战验收与主协调者抽检，以及同线程返工闭环。 | 聚焦型修复、小中型功能或局部重构（1–2 文件单兵或定向调度 `investigator` / `coder` / `verifier`）。 |
| **`$teamwork`** | 结构化端到端协同交付流水线：需求访谈澄清（`interviewer`，支持 `[skip-interview]` 跳过）、`xhigh` 架构规划与互斥 `file_ownership` 文件所有权矩阵（`planner`）、隔离工作区并行开发（`coder`）、双层验收（`verifier`）、同线程返工与逐里程碑顺序合入。 | 需严格冻结需求规格且零文件写冲突的中大型多模块交互式研发任务。 |
| **`$swarm`** | 基于 SQLite `#seq` 频道（`main`、`#backend`、`#ui`、`#verify` 等）的多成员蜂窝并发协同、增量游标 `board_read(since_seq)`、紧凑 `board_status(compact=True)` 状态同步、看门狗停滞分类与 Web 实时人工干预。 | 需要成员间点对点交接、交互式协同与实时可观测性的并行工作流。 |
| **`$pipeline`** | 宿主终端确定性自动闭环 DAG 管线：冻结 `pipeline_spec.json` 后由 `tri-orchestrator-pipeline` CLI 驱动独立隔离的 `codex app-server --listen stdio://` 工作线程，自动完成并发/拓扑切片、独立只读验收、最多 3 轮同线程返工、串行合入与 `--resume-run` 断点续跑。 | 多模块批量重构、长时无人值守批处理、需要消除根会话中继 Token 开销或关窗后台续跑的确定性工程任务。 |

---

### 工作区模式与依赖边界

- `mode="git_clone"` 才要求在分配的分支提交、报告真实 commit SHA，并在验收/合并前保持 Git 工作树干净。`mode="snapshot"` 的 `branch_name=None` 正常；合并前 `dirty=true` 表示相对基线有改动，应检查 `workspace_diff`、文件所有权和真实测试输出，不要求提交或初始化 Git。
- 两种模式都先 `workspace_merge_preview` 再 `workspace_merge(strategy="merge")`；snapshot 执行三方目录比较与应用。合入后完成主目录集成验证，再由 `workspace_cleanup` 按对应模式核验已合入且工作区未新增改动，不能用 Git 的干净工作树要求阻断快照验收。
- `dep_mode="none"` 不配置依赖目录。需要独立依赖文件时使用 `copy`；`fresh_venv_uv` 创建新的 `.venv`，依赖包需在其中安装，不负责创建 `node_modules`。
- `link_ro_cache` 的名称保留兼容，实际使用目录链接/硬链接（无法链接时复制），是**共享可写**依赖，不是只读或写入隔离。经链接写入可能修改源项目依赖；需隔离依赖写入时使用 `copy` 或 `fresh_venv_uv`。
- `$boost` Solo 验收失败由协调者本人修复，再交同一个 verifier；Delegation 则交原 coder 返工，再交原 verifier。静态技能契约测试只检查文档约束，不证明原生代理已执行完整闭环。

## 架构概览

```text
Codex CLI / V2 Subagents / Lifecycle Hooks / Pipeline CLI
   │  stdio JSON-RPC（MCP shim） / 本地 HTTP RPC（hook & pipeline）
   ▼
6 个 CLI 入口：tri-orchestrator-{mcp,daemon,verify,hook,challenge,pipeline}
   │  daemon.lock 互斥锁（msvcrt / fcntl）-> 自动拉起唯一后台守护进程（hook 离线绝不拉起）
   ▼
单例 HTTP/SSE 守护进程（127.0.0.1:18790）
   ├── /api/rpc & /api/tools   ──► RpcDispatcher（35 个公开 MCP 工具 + 1 个内部钩子 RPC）
   ├── /api/events             ──► EventBus（SSE 实时事件流）
   ├── /                       ──► Boost / Teamwork / Swarm 入口选择
   ├── /boost, /teamwork, /swarm ──► 各模式控制台（含原生 V2 与 App-Server 管线诚实归属徽章）
   ├── Watchdog Loop           ──► workflow5/4 进展分类与需处理告警；旧合同保留心跳规则
   ├── SQLite WAL Engine       ──► schema 8（projects、runs、session_run_bindings、
   │                               task_packets、workers、board_messages、workspaces 等）
   └── WorkspaceManager        ──► .swarm-workspaces/<run>-<worker>
                                   （git clone --local / 三方快照 + 端口池）
```

### 仓库目录结构

```text
.
├── .agents/plugins/marketplace.json            # Codex 插件市场目录（指向 ./plugin）
├── plugin/                                     # 自包含可分发插件包（v0.6.0）
│   ├── .codex-plugin/plugin.json               # 唯一 Codex 插件清单（元数据、skills、hooks、mcpServers）
│   ├── .mcp.json                               # Codex MCP 覆盖配置（env_vars、超时设置）
│   ├── mcp.json                                # MCP 启动配置（${PLUGIN_ROOT}）
│   ├── hooks/hooks.json                        # 4 类生命周期钩子声明（SessionStart, PostCompact, UserPromptSubmit, Stop）
│   ├── pyproject.toml & uv.lock                # Python 运行时包（>=3.12）与 6 个 CLI 脚本入口定义
│   ├── agents/*.toml                           # 6 个角色配置（interviewer, planner, investigator, coder, verifier, swarm_worker）
│   ├── assets/{icon.svg,logo.svg}              # 插件图标资源
│   ├── skills/
│   │   ├── route-a-workflow.md                 # 共享工作流合同（workflow5 原子宏与 workflow1..4 兼容分支）
│   │   └── {boost,teamwork,swarm,pipeline}/    # 4 个显式工作流技能（SKILL.md）
│   └── src/orchestrator/                       # 守护进程、MCP shim、宏 RPC、钩子、挑战与管线运行器、Web 控制台
├── scripts/                                    # 跨平台安装脚本（install.ps1 / install.sh）、卸载、受控升级与 E2E 验证脚本
├── docs/verification/                          # 版本发布记录、钩子实测、隔离验收与正式升级回执摘要
├── tests/                                      # 单元、契约与真实 codex.exe 端到端回归测试套件
└── pyproject.toml & uv.lock                    # 仓库根开发/测试配置（可编辑安装 plugin + pytest）
```

---

## CLI 可执行程序（6 个）

| 可执行程序 | 入口模块 | 用途说明 |
| :--- | :--- | :--- |
| `tri-orchestrator-mcp` | `orchestrator.mcp_shim:main` | Codex stdio MCP 桥接层，按互斥锁自动确保本地单例守护进程在线并转发 35 个公开 MCP 工具。 |
| `tri-orchestrator-daemon` | `orchestrator.daemon:main` | 本地单例 HTTP/SSE 守护进程（默认 `127.0.0.1:18790`），承载 SQLite WAL 状态机与中文 Web 控制台。 |
| `tri-orchestrator-verify` | `orchestrator.verify_runner:main` | 终端硬验收采集器，按冻结产物清单与 `env_profile` 键名白名单执行测试命令并输出 SHA-256 `receipt.json`。 |
| `tri-orchestrator-hook` | `orchestrator.hook_runner:main` | 生命周期钩子运行器（`SessionStart` / `PostCompact` / `UserPromptSubmit` / `Stop`），硬超时 `<=1.5s`，守护进程离线时静默 `exit 0`。 |
| `tri-orchestrator-challenge` | `orchestrator.challenge_runner:main` | 宿主侧只读并发盲审采集器（并发上限 `<=4`），校验源码零突变并产出带 SHA-256 的挑战报告。 |
| `tri-orchestrator-pipeline` | `orchestrator.pipeline_runner:main` | `$pipeline` 确定性自动闭环 DAG 管线运行器，通过隔离 `codex app-server` 推进多步实施、独立验收、自动返工与 `--resume-run` 断点续跑。 |

---

## MCP 工具（当前源码与正式 0.6.0 安装版均为 35 个公开工具）

> 注：除下方 35 个公开 MCP 工具外，`/api/rpc` 另保留 1 个内部只读/单次门禁查询接口 `session_hook_check` 供本地 `tri-orchestrator-hook` 调用，不向模型 `tools/list` 暴露以节省上下文。

| 分类 | 工具名 | 功能说明 |
| :--- | :--- | :--- |
| **运行与会话管理** | `run_create` | 创建 `boost` / `teamwork` / `swarm` 运行实例（默认 `workflow5`），支持绑定根会话 `session_id` 并返回 `cli_executables`。 |
| | `run_update` | 更新运行状态（`active`、`completed`、`failed`、`archived`）、`title` 或 `metadata`。 |
| | `run_list` | 按 `project_id`、`status` 或 `mode` 筛选并列出运行记录。 |
| | `session_bind` | 将宿主根会话 `session_id` 显式绑定至指定 `run_id` 与 `project_root`，供生命周期钩子精确关联。 |
| **任务契约** | `task_packet_save` | 持久化单调递增版本的任务契约（`goal`、`file_ownership`、`acceptance_criteria`）。 |
| | `task_packet_get` | 获取指定运行最新（或指定 `version`）的任务契约。 |
| **消息板 (`#seq`)** | `board_post` | 发布消息、差异或测试报告至指定频道（`main`、`#verify` 等），返回紧凑指针 `{seq, channel, mentions, summary}`。 |
| | `board_read` | 按 `since_seq` 或 `reader_name` 游标增量读取消息（支持 `advance_cursor=true`）。 |
| | `board_status` | 只读返回成员状态、待办、核对项、进展分类与 `cli_executables`；支持 `compact=true` 大幅裁剪轮询载荷，不隐式 ACK。 |
| | `board_channel_list` | 列出当前运行的活跃频道及消息计数。 |
| **成员管理** | `worker_register` | 绑定真实 Codex V2 `/root/<task_name>` 或管线 `appserver://<thread_id>` 身份并注册/更新成员节点。 |
| | `worker_heartbeat` | 刷新成员心跳时间戳并清除 `stalled_alerted` 告警标记。 |
| | `worker_status_update` | 变更成员状态（`waiting`、`working`、`stopped`、`killed`）。 |
| | `worker_control_ack` | 协调者或管线运行器执行原生中断后，提交带真实结果的 `applied` / `failed` 回执；只有 `applied` 原子更新成员状态。 |
| **原子派发与转交账本** | `workflow_prepare_dispatch` | 单事务原子完成工作区创建、成员预登记、步骤保存/推进、收件箱投递与原生调用领取（支持 `workflow5` 首轮直达与延迟 `verifier`）。 |
| | `native_action_record_call` | 原子将宿主调用原始回执落盘、由服务端计算 64 位 SHA-256 摘要并完成转交确认/对账（自动回填 `workflow5` 子代理路径）。 |
| | `native_action_update` | 细粒度领取/开始/记录/核对原生转交账本；支持冻结收件人批量，工具自身不调用宿主或完成业务。 |
| **持久收件箱** | `inbox_send` | 发送绑定收件人和步骤版本的持久消息，同 `key` 同 `payload` 幂等。 |
| | `inbox_get` | 按稳定消息 ID 读取完整内容及回执历史。 |
| | `inbox_ack` | 记录 `accepted` / `completed` / `failed` 或明确取消；消息回执不等于独立验收。 |
| | `inbox_wait` | 有界异步等待未终态消息，不隐式 ACK；已 `accepted` 工作需核对而非盲重放。 |
| **工作流、宏回合与证据** | `workflow_step_save` | 创建或显式修订冻结步骤；乐观锁版本检查、原绑定与预算保留，强制文件所有权互斥准入。 |
| | `workflow_step_advance` | 按门禁推进、观察、交接/精确 ACK 或生成本地单次修订边界；不伪造原生调用。 |
| | `workflow_author_turn` | 作者端原子宏：单事务打包 `inbox_ack`、成员工作态切换、步骤 `accept` / `submit` 及可选消息板摘要发布。 |
| | `workflow_verifier_turn` | 验收端原子宏：`prepare`（确认收件、切换工作态、捕获产物并返回 `verify_executable`）或 `record`（自动挑战绑定、校验真实回执并记录裁决）。 |
| | `artifact_capture` | 捕获候选/集成源码指纹与冻结命令清单（支持 `env_profile="windows_minimal_py" \| "posix_minimal_py"` 键名白名单）。 |
| | `verification_record` | 原 `verifier` 追加不可变逐项验收及 `tri-orchestrator-verify` 采集回执；仅消息板 PASS 不能通过门禁。 |
| | `workflow_review` | 只读 `challenger` 记录映射发现；原 `verifier` 核实 FAIL、驳回或解决，原作者返工预算不变。 |
| | `workflow_closeout_run` | 协调者收尾原子宏：单次受门禁校验调用完成全部已通过集成审查步骤的 `complete`、干净工作区清理、成员 `stopped` 与运行 `completed`。 |
| **隔离工作区** | `workspace_create` | 在 `.swarm-workspaces/` 下创建隔离的 `git clone --local`（或三方快照）工作区并分配测试端口（`18800..18999`）。 |
| | `workspace_status` | 返回工作区脏标记、领先提交数及变更文件列表。 |
| | `workspace_diff` | 返回文件变更状态及统一 Diff 文本。 |
| | `workspace_merge_preview` | 非破坏性合并冲突预检（`git merge-tree --write-tree` 或三方快照），不改动主仓库。 |
| | `workspace_merge` | Git 模式合并/cherry-pick 已提交的干净分支；snapshot 模式按基线执行三方目录合并，冲突不写入。 |
| | `workspace_cleanup` | 核验已成功合入且工作区无合入后的新增改动；按 Git 提交证据或 snapshot 基线检查，再删除工作区目录。 |

---

## 中文 Web 控制台（`http://127.0.0.1:18790`）

### 任务概览与按需详情

首页提供三个模式入口，共用一个服务和数据库，不额外占用端口（`$pipeline` 任务按其声明的 `mode` 在对应入口展示，并带有高对比度 `App-Server 管线` 诚实归属徽章）：

- [`/boost`](http://127.0.0.1:18790/boost)：聚焦单个目标，直接执行或按需委派，再独立验收。
- [`/teamwork`](http://127.0.0.1:18790/teamwork)：先澄清与规划，再按模块分工开发和验收。
- [`/swarm`](http://127.0.0.1:18790/swarm)：多成员并行，通过频道交接与汇总进展。

每个入口只列出对应模式的任务，默认先展示任务概览：任务状态、成员职责与已登记工作、最近动态。顶部“项目 / 任务”用于选择工作目录及其中的一次编排记录；无指定上下文时优先选择本模式进行中的任务。日志全文按需展开，协作图、工作区检查和人工消息入口仍保留。

旧版 `/?project_id=...&run_id=...` 或仅带 `run_id` 的链接会按任务实际模式跳转；超过最近 100 条的历史任务也可通过链接打开。指定错误的模式、项目或不存在的任务会明确报错，不会自动替换成另一任务。切换模式使用独立页面导航，项目内无本模式任务时显示空状态。当前任务的实时更新仍由 SSE 接收；新建任务通过“立即同步”刷新列表。模式入口是展示分区，不是权限、进程或存储隔离。

2026-11-16 已在 Swarm 全部结束后通过官方插件安装流程更新到 **0.1.2**，合并此前工作流、工作区与概览修复及三个独立入口。安装版、数据迁移与真实浏览器验收见 [安装验证记录](docs/verification/installed-20261116/report.md)；此前源码隔离证据保留在 `docs/verification/mode-entries-20261116/report.md`。

成员状态是协调者或管线运行器登记的状态，不是后台直接探测原生代理进程。恢复旧任务时，应显式重新登记成员及当前工作；发布消息本身不恢复成员状态。

摘要快照与 `board_status` 不返回内部文件哈希清单，摘要快照不扫描工作目录。未执行详细检查的文件变更数显示为“未检查”，不等于零变更；需要时打开工作区详情读取差异。完整 manifest 仍保存在数据库中供差异与合并使用。

源码验证、运行审计及安装生效边界见 `docs/verification/dashboard-20261116/audit.md`。

MCP shim 拉起守护进程后，可在浏览器打开 **`http://127.0.0.1:18790`** 使用 Antigravity 极简风格中文控制台：

- **项目/任务选择与浅/深色主题**：切换仓库项目与运行实例，查看编排模式、健康状态与 SSE 连接状态；支持浅/深色主题、一键同步与导出 `.md` / `.json` 运行报告。
- **`消息板` 视图**：支持按频道（`全部` / `主频道` / `人工` / 自定义频道）、`@提及`（含中文相邻提及识别）与关键词检索过滤连续消息流（含转义安全的 Markdown 渲染、跳至最新与自动滚动开关），并在底部提供悬浮人工干预输入框向 `human` 频道实时发送指令（`POST /api/runs/{run_id}/human-message`）。
- **`成员` 视图**：顶部全宽展示冻结的任务契约目标、验收标准与互斥文件所有权矩阵；下方按活跃与已结束分组显示成员图标、职责角色、执行归属徽章、1 秒级心跳延迟、卡死告警、分配端口与未合入文件数。
- **`协作图` 视图**：基于 `#seq` 消息与成员动态生成的交互式 SVG 拓扑协作图，支持时间轴滑块与自动回放（`▶` / `⏸`）、隐藏未活跃成员及 `@all` 广播边展开，点击或悬停节点可高亮关联协作路径。
- **侧滑工作区检查器（`工作区检查`）**：点击成员行或顶栏开关即可滑出右侧检查器，查看工作区元数据、文件变更列表及统一 Diff，并严格限定 4 类安全操作（合并冲突预检、打开本地目录、请求暂停/结束任务，等待协调者或管线运行器执行并回执，保留文件、仅针对已合入且干净工作区的清理与 HTTP `409` 拦截）。危险操作端点（`git push`、`reset`、未合入强制删除）均被显式禁止并返回 `HTTP 403`。

### 运行时环境变量（`TRI_ORCH_*`）

| 变量名 | 默认值 | 说明 |
| :--- | :--- | :--- |
| `TRI_ORCH_HOST` | `127.0.0.1` | 守护进程监听主机。 |
| `TRI_ORCH_PORT` | `18790` | 守护进程 HTTP/SSE 端口。 |
| `TRI_ORCH_DATA_DIR` | `%LOCALAPPDATA%\tri-orchestrator`（Windows）<br>`~/.local/share/tri-orchestrator`（POSIX） | 运行时数据目录，存放 `state.db`、`daemon.lock`、`daemon.pid.json`、`shim.log` 与 `daemon.log`。 |
| `TRI_ORCH_STALL_SEC` | `300` | 默认成员心跳卡死告警阈值（秒）。 |
| `TRI_ORCH_WATCHDOG_INTERVAL_SEC` | `15` | 看门狗后台巡检间隔（秒）。 |
| `TRI_ORCH_PORT_RANGE` | `18800..18999` | 分配给隔离工作区的测试端口池范围。 |
| `TRI_ORCH_HOOK_TIMEOUT_SEC` | `1.5` | `tri-orchestrator-hook` 访问本地守护进程的超时秒数（硬上限封顶为 `1.5` 秒）。 |

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

自动停止正在运行的守护进程、移除插件与市场注册信息、清理 `config.toml` 中与本插件关联的 `[hooks.state]` 条目及 `~/.codex/agents/` 中的 6 个角色 TOML（若存在安装前基线文件则自动还原），并支持按需恢复 `config.toml` 备份或清理运行时数据目录：

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

- 控制台的暂停/结束任务是**请求**，HTTP 202 仅表示已入队。界面显示“待协调者执行”，不提前显示停止成功。未绑定真实代理身份的历史成员不能请求控制。
- 协调者（或 `$pipeline` 运行器）从 `board_status.control_requests` 读取请求，使用原生中断工具（或向 `app-server` 发送 `turn/interrupt`）后再 `worker_control_ack`。失败保留原因，成员状态不变；重启不会丢失请求。结束任务表示中断并停止继续派发该成员，不表示杀死宿主 Codex 主会话。
- 协调者在工具边界和最长 30 秒的等待前后检查请求（`$pipeline` 运行器在后台高频轮询并在 `<=2s` 内中断活跃线程）；协调者离线、模型断网或执行长工具时仍可能延迟，界面会保持待处理，不自动伪造回执。
- 人工文字消息只写入消息板，不会自动投递给原生代理。`$boost` 协调者须在派发/返工前、等待前后和收尾前，通过 `board_read(reader_name="coordinator", advance_cursor=False)` 读取，分别记录原始 `#seq` 的收到、拟采取行动、实际转发与后续完成证据；批次处置已记载后，再按相同 `since_seq` 和非空批次数量以 `advance_cursor=True` 推进持久游标。中断恢复时先核对收据和原生状态，不保证 exactly-once；游标前进或转发成功不等于任务完成。
- 原生派发前的成员登记为待命；成功绑定并实际派发后才记为工作中。原生线程完成后，仍待审查/返工的成员记为待命；整个工作流收尾才记为已停止。状态来自协调者或管线运行器明确报告，不是后台直接读取 Codex 内部数据库。
- 已停止/已终止成员和已结束运行不显示当前卡住告警；历史告警留在消息板。迟到心跳不能恢复已暂停或已终止的成员。
- MCP 转发不重放结果未知的写请求。读超时后先核对原操作是否落地，不盲目重试。健康检查异常或已有存活但不健康实例时明确报错，不再据此拉起第二个 daemon。失败启动不得覆盖正常实例的 PID 文件。
- 插件连接错误不会被伪装成模型连接错误，也不会自动绕过 MCP 直写数据库。Codex 模型网络、代理配置不由此插件修改。

本轮实现与验证记录见 `docs/superpowers/plans/2026-11-15-reliable-control.md`。


## Large result delivery

Codex stdio is a bounded message transport, not a bulk-file channel. Small MCP results keep their original JSON schema. When the final encoded tool response exceeds the inline budget, the shim atomically stores the **complete original JSON result** under the runtime data directory's `results/` folder and returns an explicit `delivery: "file"` receipt with the absolute result path, byte count and SHA-256. This is not silent truncation. Read the local result file; do not replay a completed write tool call. File creation errors are reported as tool errors, not success.

The browser HTTP API keeps its existing payload contract. No model/proxy settings or workspace files are changed by result delivery. Result files are retained for audit and can consume disk space; this release does not automatically delete them. Binary diff detection scans in bounded chunks before text decoding, and existing binary files remain visible in the diff listing.
