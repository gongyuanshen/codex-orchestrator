# 三重编排器 (Tri Orchestrator)

面向 Codex CLI / Codex Desktop 的自包含多代理编排插件，提供 **`$boost`**、**`$teamwork`**、**`$swarm`** 三种原生 V2 多代理工作流，由本地单例 HTTP/SSE 守护进程（`127.0.0.1:18790`）、SQLite WAL 状态机、`#seq` 消息板、物理隔离工作区与生命周期钩子组成。

## 核心特性

- **三种编排工作流**：按任务规模选择 `$boost`（单目标极速执行）、`$teamwork`（澄清规划 + 分模块协同）或 `$swarm`（多成员并行 + 频道交接）。
- **物理工作区隔离**：Git 仓库使用 `git clone --local` 隔离分支，非 Git 目录使用三方快照隔离；合入前执行非破坏性冲突预检（`workspace_merge_preview`）。
- **硬验收与并发盲审**：通过 `tri-orchestrator-verify` 冻结产物清单与命令输出生成 SHA-256 验收回执，并支持 `tri-orchestrator-challenge` 只读并发盲审。
- **Codex 原生会话对齐**：只读桥接本地 Codex 项目与会话标题，按「工作区项目 → Codex 会话 → 编排轮次」三层归并展示，隔离多会话并发与 `Stop` 钩子检查。
- **实时中文 Web 控制台**：提供多模式控制台（`/boost`、`/teamwork`、`/swarm`）、`Ctrl+K` 跨项目跨模式检索、协作图回放、成员状态与工作区 Diff 检查器。

---

## 环境要求

- **Codex CLI**（支持插件机制、原生 V2 子代理与生命周期钩子）
- **[`uv`](https://docs.astral.sh/uv/)**（管理 Python `>=3.12` 运行环境）
- **`git`**（用于 Git 工作区克隆隔离与合并；非 Git 目录自动回退到目录快照）

---

## 安装与升级

### 方式一：通过 Codex CLI 直接安装（零脚本）

适用于首次安装，无需本地克隆仓库或运行额外脚本：

```bash
codex plugin marketplace add gongyuanshen/codex-orchestrator
codex plugin add tri-orchestrator@tri-orchestrator
```

安装完成后：
- Codex 会将 `./plugin/` 缓存到本地插件目录（`~/.codex/plugins/cache/tri-orchestrator/tri-orchestrator/<version>/`）。
- 首次触发 MCP 工具时，Codex 自动执行 `uv run --quiet --project . python -m orchestrator.mcp_shim` 初始化依赖并拉起后台单例守护进程。
- 若未安装专用角色 TOML，`$boost`、`$teamwork` 与 `$swarm` 调用 `spawn_agent` 时会省略 `agent_type`、`model` 与 `reasoning_effort`，直接继承当前主会话配置并在提示词中内联角色契约。

### 方式二：运行仓库安装脚本（完整角色与钩子配置）

克隆仓库后运行平台脚本，可额外完成 6 个角色 TOML（`~/.codex/agents/`）安装、安全启用 `multi_agent` 与 `multi_agent_v2`、缓存依赖预热以及 `hooks/list` 四钩子自检：

**Windows（PowerShell）：**

```powershell
powershell -ExecutionPolicy Bypass -File scripts/install.ps1
# 如需在 CLI 中直接写入钩子信任哈希，可附加 -TrustPluginHooks
```

**macOS / Linux（Bash）：**

```bash
bash scripts/install.sh
# 如需在 CLI 中直接写入钩子信任哈希，可附加 --trust-plugin-hooks
```

### 旧版本运行库升级

如果本地已存在旧版 Schema 运行库，需先结束全部活跃任务并停止守护进程，再使用 `scripts/upgrade.py` 执行受控备份与离线升级，最后通过 Codex 重新安装插件：

```bash
python scripts/upgrade.py prepare --source-plugin ./plugin --target-plugin <old-plugin-dir> --data-dir <data-dir> --backup-dir <backup-dir>
python scripts/upgrade.py apply --backup-dir <backup-dir>
python scripts/upgrade.py verify --backup-dir <backup-dir>
```

> 守护进程冷启动不会自动迁移旧版 Schema，也不要直接覆盖旧版托管缓存目录；升级遇到新数据写入后拒绝用旧备份回滚，保留数据进行前向修复。

---

## 编排工作流

| 技能指令 | 编排定位 | 适用场景 |
| :--- | :--- | :--- |
| **`$boost`** | 单目标极速执行：路由单兵直达或定向委派，锁定契约、独立 `verifier` 验收与返工闭环。 | 聚焦修复、小中型功能或局部重构。 |
| **`$teamwork`** | 结构化协同流水线：需求澄清（`interviewer`，支持 `[skip-interview]`）→ 规划与互斥文件所有权（`planner`）→ 隔离开发（`coder`）→ 双层验收（`verifier`）→ 逐里程碑合入。 | 需冻结规格且多模块并行、零文件写冲突的中大型任务。 |
| **`$swarm`** | 基于 `#seq` 频道与持久游标的多成员并发协同、停滞分类与人工实时干预。 | 需要多成员点对点交接与实时协同观测的并行任务。 |

---

## Web 控制台（`http://127.0.0.1:18790`）

守护进程启动后，在浏览器访问 **`http://127.0.0.1:18790`**（各模式共用同一服务与数据库，入口仅作展示分区）：

- **模式入口**：[`/boost`](http://127.0.0.1:18790/boost)、[`/teamwork`](http://127.0.0.1:18790/teamwork)、[`/swarm`](http://127.0.0.1:18790/swarm)，支持携带 `run_id` 链接直达历史任务。
- **三层导航与全局检索**：按「真实工作区 → Codex 会话 → 编排轮次」组织侧栏与顶部选择器；支持 `Ctrl+K` 跨项目、跨模式直达活跃任务，并标记同项目非隔离直改并发风险。
- **概况与日志**：展示任务状态、触发原话、执行目录与轮次介入分隔，支持全文展开及 `.md` / `.json` 运行报告导出。
- **消息板与人工干预**：按频道、`@提及` 与关键词过滤 `#seq` 消息流，底部可向 `human` 频道发送人工指令。
- **成员与协作图**：展示冻结任务契约、互斥文件矩阵、成员心跳/卡死分类与交互式 SVG 拓扑回放图。
- **工作区检查器**：查看文件变更与统一 Diff，执行冲突预检、打开本地目录、请求暂停/停止成员及受门禁保护的清理操作（`git push`、`reset` 与未合入强制删除端点均禁用并返回 `HTTP 403`）。

---

## 工作区、依赖与运行边界

- **工作区隔离模式**：
  - `mode="git_clone"` 需要在分配分支提交真实 commit SHA，并在验收/合入前保持工作树干净。
  - `mode="snapshot"` 下 `branch_name=None` 属正常状态；合入前 `dirty=true` 表示相对基线存在改动，合入与清理按三方目录快照基线核验。
  - 两种模式均应先执行 `workspace_merge_preview`，再执行 `workspace_merge(strategy="merge")` 与 `workspace_cleanup`。
- **依赖目录策略（`dep_mode`）**：
  - `none` 不配置依赖目录；需要独立依赖副本时使用 `copy`。
  - `fresh_venv_uv` 创建全新 `.venv` 供测试安装依赖，不负责创建 `node_modules`。
  - `link_ro_cache` 为兼容历史保留的命名，底层采用目录链接/硬链接（失败时复制），属于**共享可写**依赖而非只读隔离；需要写入隔离时请改用 `copy` 或 `fresh_venv_uv`。
- **控制、重试与结果传递语义**：
  - 守护进程与看门狗不会自动代调或重启原生代理，也不会自动跨步骤重放结果未知的写操作；人工文字消息不会自动投递给子代理，暂停/结束请求由协调者在工具边界读取并执行原生中断后回执。
  - 成员状态由协调者登记，不通过后台扫描原生进程推断；守护进程不会修改 Codex 的模型或网络代理配置。
  - 当 MCP 单次返回体超过 stdio 内联预算时，完整 JSON 会原子落盘至运行时目录 `results/` 下，并返回含路径、字节数与 SHA-256 的 `delivery: "file"` 回执（直接读取结果文件，勿重复重放写操作）。

---

## 架构与目录结构

```text
Codex CLI / V2 Subagents / Lifecycle Hooks
   │  stdio JSON-RPC (mcp_shim) / 本地 HTTP RPC (hook_runner)
   ▼
CLI 入口：tri-orchestrator-{mcp,daemon,verify,hook,challenge}
   │  daemon.lock 互斥锁（hook 在守护进程离线时静默退出，不自动拉起服务）
   ▼
单例 HTTP/SSE 守护进程（127.0.0.1:18790）
   ├── /api/rpc & /api/tools   ──► RpcDispatcher（35 个公开 MCP 工具 + 1 个内部钩子 RPC）
   ├── /api/sessions, /projects, /runs & /api/events（SSE 实时流）
   ├── /, /boost, /teamwork, /swarm（中文 Web 控制台）
   ├── Watchdog Loop           ──► 进展分类与卡死/人工决策告警
   ├── SQLite WAL Engine       ──► schema 9（projects, runs, session_turns,
   │                               session_run_bindings, task_packets, workers,
   │                               board_messages, workspaces 等）
   └── WorkspaceManager        ──► .swarm-workspaces/<run>-<worker> + 端口池
```

```text
.
├── .agents/plugins/marketplace.json   # Codex 本地市场清单
├── plugin/                            # 自包含 Codex 插件包
│   ├── .codex-plugin/plugin.json      # 插件元数据、skills/hooks/mcp 声明
│   ├── .mcp.json & mcp.json           # Codex MCP 启动配置
│   ├── hooks/hooks.json               # SessionStart / PostCompact / UserPromptSubmit / Stop
│   ├── agents/*.toml                  # 6 个角色定义（interviewer/planner/investigator/coder/verifier/swarm_worker）
│   ├── skills/                        # route-a-workflow.md 与 {boost,teamwork,swarm}/SKILL.md
│   ├── src/orchestrator/              # 守护进程、MCP shim、桥接器、钩子、验收/盲审运行器与 Web 前端
│   └── pyproject.toml & uv.lock       # 插件运行时依赖与 CLI 入口定义
├── scripts/                           # install.ps1 / install.sh / uninstall.ps1 / upgrade.py / verify_e2e.ps1
├── tests/                             # 单元、契约、UI 与端到端测试套件
└── pyproject.toml & uv.lock           # 仓库开发与测试环境配置
```

---

## CLI 可执行程序与 MCP 工具

### CLI 入口（5 个）

| 可执行程序 | 入口模块 | 用途说明 |
| :--- | :--- | :--- |
| `tri-orchestrator-mcp` | `orchestrator.mcp_shim:main` | Codex stdio MCP 桥接层，按互斥锁确保单例守护进程在线并转发 MCP 工具调用。 |
| `tri-orchestrator-daemon` | `orchestrator.daemon:main` | 本地 HTTP/SSE 守护进程，承载 SQLite WAL 状态机与 Web 控制台。 |
| `tri-orchestrator-verify` | `orchestrator.verify_runner:main` | 终端验收采集器，按冻结清单与白名单 `env_profile` 执行命令并输出 SHA-256 `receipt.json`。 |
| `tri-orchestrator-hook` | `orchestrator.hook_runner:main` | 生命周期钩子运行器（硬超时 `<=1.5s`，离线静默 `exit 0`）。 |
| `tri-orchestrator-challenge` | `orchestrator.challenge_runner:main` | 宿主侧只读并发盲审采集器，校验零源码突变并生成挑战回执。 |

### 公开 MCP 工具（35 个）

> `/api/rpc` 另保留 1 个内部接口 `session_hook_check` 供本地 `tri-orchestrator-hook` 调用，不暴露于模型 `tools/list`。

| 分类 | 工具列表 |
| :--- | :--- |
| **运行与会话** | `run_create`、`run_update`、`run_list`、`session_bind` |
| **任务契约** | `task_packet_save`、`task_packet_get` |
| **消息板 (`#seq`)** | `board_post`、`board_read`、`board_status`、`board_channel_list` |
| **成员与控制** | `worker_register`、`worker_heartbeat`、`worker_status_update`、`worker_control_ack` |
| **原生派发账本** | `workflow_prepare_dispatch`、`native_action_record_call`、`native_action_update` |
| **持久收件箱** | `inbox_send`、`inbox_get`、`inbox_ack`、`inbox_wait` |
| **工作流与验收** | `workflow_step_save`、`workflow_step_advance`、`workflow_author_turn`、`workflow_verifier_turn`、`artifact_capture`、`verification_record`、`workflow_review`、`workflow_closeout_run` |
| **隔离工作区** | `workspace_create`、`workspace_status`、`workspace_diff`、`workspace_merge_preview`、`workspace_merge`、`workspace_cleanup` |

---

## 运行时环境变量

| 变量名 | 默认值 | 说明 |
| :--- | :--- | :--- |
| `TRI_ORCH_HOST` | `127.0.0.1` | 守护进程监听地址。 |
| `TRI_ORCH_PORT` | `18790` | 守护进程 HTTP/SSE 端口。 |
| `TRI_ORCH_DATA_DIR` | `%LOCALAPPDATA%\tri-orchestrator`（Windows）<br>`~/.local/share/tri-orchestrator`（POSIX） | 运行时数据目录（`state.db`、`daemon.lock`、`daemon.pid.json`、`results/`、日志等）。 |
| `TRI_ORCH_STALL_SEC` | `300` | 成员心跳停滞告警阈值（秒）。 |
| `TRI_ORCH_WATCHDOG_INTERVAL_SEC` | `15` | 看门狗巡检间隔（秒）。 |
| `TRI_ORCH_PORT_RANGE` | `18800..18999` | 隔离工作区测试端口池范围。 |
| `TRI_ORCH_HOOK_TIMEOUT_SEC` | `1.5` | `tri-orchestrator-hook` 本地 RPC 超时上限（秒，最大 `1.5`）。 |

---

## 开发测试与卸载

### 运行测试

```bash
uv run pytest -v
```

### 卸载

**零脚本卸载：**

```bash
codex plugin remove tri-orchestrator@tri-orchestrator
codex plugin marketplace remove tri-orchestrator
```

**Windows 脚本卸载（自动停服、移除插件与市场、还原配置与角色备份）：**

```powershell
powershell -ExecutionPolicy Bypass -File scripts/uninstall.ps1
# 可选参数：-RestoreConfigBackup -RemoveDataDir
```

**macOS / Linux**：执行上述 `codex plugin ...` 移除命令后，按需清理 `~/.codex/agents/` 下对应角色文件，停止 `daemon.pid.json` 进程并清理 `~/.local/share/tri-orchestrator`。

---

## 许可证

[MIT](LICENSE)
