# 本地运行与维护

[返回 README](../README.md) · [升级现有安装](../README.md#从旧版升级)

## 离线迁移与回滚

**数据库结构较旧**（例如 `0.6.0 / 0.6.1 → schema 10`）时，先检出**目标版本**的源码，从仓库根目录用该版本的 Python 环境准备备份并离线迁移。将命令中的目录替换为实际路径；`<old-plugin-dir>` 是旧版已安装缓存，`<backup-dir>` 是一个新目录。使用非默认端口时为 `prepare` 加上 `--port`：

```bash
uv run --locked --project ./plugin python scripts/upgrade.py prepare --source-plugin ./plugin --target-plugin "<old-plugin-dir>" --data-dir "<data-dir>" --backup-dir "<backup-dir>"
uv run --locked --project ./plugin python scripts/upgrade.py apply --database-only --backup-dir "<backup-dir>"
```

`--database-only` 在备份目录内迁移并核对历史数据，通过后才更新 `state.db`；不会复制文件到旧版托管缓存。冷启动不会自动做这一步。

若做过离线迁移，在新后台启动**之后**再运行：

```bash
uv run --locked --project ./plugin python scripts/upgrade.py verify --backup-dir "<backup-dir>" --keep-backups 3
```

> 需要回滚时，先停止新后台，再用目标版本源码执行 `uv run --locked --project ./plugin python scripts/upgrade.py rollback --backup-dir "<backup-dir>"`。回滚通过后按 [README 的换标签步骤](../README.md#从旧版升级)安装旧插件，再启动后台。若新版本已有数据写入，脚本会拒绝覆盖，保留当前数据供修复。

## Web 控制台（`http://127.0.0.1:18790`）

守护进程启动后，在浏览器访问 **`http://127.0.0.1:18790`**（各模式共用同一服务与数据库，入口仅作展示分区）：

控制台和 API 没有登录认证，面向本机使用；不要把端口开放到局域网或公网。

- **模式入口**：[`/boost`](http://127.0.0.1:18790/boost)、[`/teamwork`](http://127.0.0.1:18790/teamwork)、[`/swarm`](http://127.0.0.1:18790/swarm)，支持携带 `run_id` 链接直达历史任务，侧栏可在「仅本模式 / 全部工作模式」与「全部来源 / 桌面端 / CLI」间筛选，顶栏支持浅色/深色主题切换与立即同步。
- **三层导航与全局检索**：按「Codex 项目 → 对话（含无编排纯对话）→ 编排轮次」组织侧栏与项目选择器（Codex 目录不可用时自动回退至已登记项目与会话）；支持 `Ctrl+K` 跨项目、跨模式及按模型/推理强度检索，并标记同项目非隔离直改并发风险。
- **概况与日志**：展示任务状态、主对话实时模型与触发快照对比（切模时标注 `⇄`）、模型分布汇总、触发原话、执行目录与带模型徽章的轮次分隔/消息流，支持全文展开及 `.md` / `.json` 运行报告导出（含模型与辅助子代理明细）。
- **阶段耗时**：「流程与验收」区按流程转换日志统计每个步骤在派发、作者实现、候选验收、返工等待、合入、集成验收、交接各阶段的耗时，以及整个 run 的记录跨度，用于定位慢在哪里；历史 run 同样可看。仅供观测，不参与门禁，也不进入 `board_status(compact=True)`。
- **成员推理档**：成员卡片与详情面板显示派发时登记的计划档（`effort_plan`：目标档、选择理由、来源）和派生后独立观察到的实际档（`observed_effort`：档位、来源、证据），两者不一致时标黄；三种模式统一遵守按任务选档规则（只读核对 `medium`，跨模块排查与关键审查 `high`，确有必要才用 `ultra`）。仅作记录，不改变验收门禁。
- **消息板与人工干预**：按频道、`@提及` 与关键词过滤 `#seq` 消息流，底部可向 `human` 频道发送人工指令。
- **成员与协作图**：展示冻结任务契约、互斥文件矩阵、成员模型/线程信息、心跳/卡死分类、L2+ 辅助子代理树与主会话未登记原生子代理；交互式 SVG 协作图支持回放、节点模型展示及「显示辅助子代理」派生连线开关。
- **工作区检查器与存储管理**：查看文件变更与统一 Diff，执行冲突预检、打开本地目录、请求暂停/停止成员及受门禁保护的清理操作（`git push`、`reset` 与未合入强制删除端点均禁用并返回 `HTTP 403`）；顶栏「存储与清理」面板可查看磁盘占用并一键安全清理。

---

## 工作区、依赖与运行边界

- **工作区隔离模式**：
  - `mode="git_clone"` 需要在分配分支提交真实 commit SHA，并在验收/合入前保持工作树干净。
  - 使用 [Git 2.36 或更新版本](https://git-scm.com/docs/git-worktree/2.36.0) 时，可在 `workspace_create` 或 `workflow_prepare_dispatch` 显式传入 `git_backend="worktree"`，与原仓库共享 Git 对象和引用。`workflow_prepare_dispatch` 的 `workspace_base_mode="git_worktree"` 也可为 `auto` 选择 worktree；要改后台默认值，见[运行时环境变量](#运行时环境变量)。复制未受版本管理的 `.env` 或依赖前，链接工作区的 Git 忽略规则须已覆盖这些路径；否则保持默认 clone。清理期间不要手动切换或移动工作区分支。
  - `mode="snapshot"` 下 `branch_name=None` 属正常状态；合入前 `dirty=true` 表示相对基线存在改动，合入与清理按三方目录快照基线核验。
  - 两种模式均应先执行 `workspace_merge_preview`，再执行 `workspace_merge(strategy="merge")` 与 `workspace_cleanup`。
- **本地环境文件**：不需要补拷 `.env` / `.env.local` 时传入 JSON 布尔值 `copy_env: false`，不是字符串 `"false"`。Git 模式已提交的文件仍按提交检出；快照里其他目录的文件按 `.triignore` 过滤。
- **依赖目录策略（`dep_mode`）**：
  - `none` 不配置依赖目录；需要独立依赖副本时使用 `copy`。
  - `fresh_venv_uv` 创建全新 `.venv` 供测试安装依赖，不负责创建 `node_modules`。
  - `link_ro_cache` 为兼容历史保留的命名，底层采用目录链接/硬链接（失败时复制），属于**共享可写**依赖而非只读隔离；需要写入隔离时请改用 `copy` 或 `fresh_venv_uv`。
- **控制、重试与结果传递语义**：
  - 守护进程与看门狗不会自动代调或重启原生代理，也不会自动跨步骤重放结果未知的写操作；人工文字消息不会自动投递给子代理，暂停/结束请求由协调者在工具边界读取并执行原生中断后回执。
  - 成员状态由协调者登记，不通过后台扫描原生进程推断；守护进程与钩子仅**只读**采集 Codex 本地目录、模型/推理强度与子代理派生树（离线时保留历史快照），绝不会修改 Codex 的模型或网络代理配置。
  - L2+ 只读辅助子代理（`max_depth = 2`）仅允许深度 1 角色用于跨模块只读调查，不得编辑源码、不向编排器注册为作者/验收者，也不提交独立验收回执。
  - 当 MCP 单次返回体超过 stdio 内联预算时，完整 JSON 会原子落盘至运行时目录 `results/` 下，并返回含路径、字节数与 SHA-256 的 `delivery: "file"` 回执（直接读取结果文件，勿重复重放写操作）。
- **并行验收与心跳保活边界（`tri-orchestrator-verify`）**：
  - `artifact_capture` 与 `workflow_verifier_turn(phase="prepare")` 支持为连续命令标注同一 `parallel_group` 并设置正整数 `max_parallel`，仅组内命令受并发上限约束并行执行；未分组命令保持串行并隔离前后组。
  - 准备阶段生成的 `verify_argv_template` / `verify_command_hint` 会附带 `--heartbeat-url`（指向 `POST /api/runs/{run_id}/workers/{worker_name}/heartbeat`，默认每 `60s` 由 `--heartbeat-interval-sec` 触发一次），用于在长耗时验收期间续期已在 `working` 状态的验证者心跳；保活既不更改成员状态，也不能替代宿主存活判断。

---

## 磁盘占用、缓存位置与清理机制

### 1. 非 Git 目录 `snapshot` 隔离、体积上限与 `.triignore` 过滤

- 没有已提交 Git 基线时使用 `mode="snapshot"`。指定 `execution_root` 后，只从该子目录复制文件、扫描准入指纹和合并，`file_ownership` 与验收路径也相对于该子目录；未指定时使用项目根。已有 Git 提交的 clone/worktree 仍以仓库根为源码坐标。
- 派单前查看 `run_create` 返回的 `project_root`（项目归属）和 `source_root`（扫描及合并范围）；派单返回的 `workspace.path` 是隔离副本，`workspace.source_root` 是它的来源。新派单不接收来源不匹配的历史工作区；已准入步骤继续使用原有源码坐标，不搬移旧工作区，也不重解释旧证据。
- **体积阈值保护（`TRI_ORCH_MAX_SNAPSHOT_MB`）**：在执行快照拷贝前会先估算源目录的文件数与总字节数；默认上限为 **`500` MB**（设为 `0` 表示不限制）。一旦超限将直接拒绝创建并给出明确错误提示，严禁静默硬拷贝数 GB 构建目录；可通过改用 Git 仓库、缩小 `execution_root`、配置 `.triignore` 或调整环境变量解决。
- **内置排除与 `.triignore`**：快照与状态比对会自动跳过内置 `SNAPSHOT_EXCLUDE_DIRS`（含 `.swarm-workspaces`、`.git`、`node_modules`、`.venv`、`__pycache__`、`.pytest_cache`、`.ruff_cache`、`.mypy_cache` 以及常见构建/缓存目录 `bin`、`obj`、`dist`、`build`、`out`、`target` 等），并读取有效源码根下的可选 **`.triignore`**（逐行设置目录名或 glob 模式）。子目录快照不继承父目录规则。

### 2. 数据目录、隔离工作区与备份位置及生命周期

| 存储路径 | 内容组成 | 生命周期与清理边界 |
| :--- | :--- | :--- |
| **`%LOCALAPPDATA%\tri-orchestrator`**（Windows）<br>`~/.local/share/tri-orchestrator`（POSIX）<br>（`TRI_ORCH_DATA_DIR`） | - `state.db`：SQLite WAL 状态机库<br>- `daemon.log` / `shim.log`：运行日志（`RotatingFileHandler` 自动轮转）<br>- `results/`：超大 MCP 返回体缓存 JSON<br>- `receipts/`：派发与验收 SHA-256 回执 | `state.db` 与 `receipts/` 长期留存以保留审计轨迹；`results/` 下缓存文件超过 `TRI_ORCH_RESULT_TTL_SEC`（默认 `86400` 秒）或超出最近 `TRI_ORCH_RESULT_MAX_FILES`（默认 `50` 份）时自动清理。 |
| **`<execution_root 或 source_root>/.swarm-workspaces/`** | 每个隔离写入子代理一个子目录（`git_clone` 或 `snapshot` 复制目录） | 任务结束调用 `workflow_closeout_run` / `workspace_cleanup` 时回收；已合入且无未提交改动的过期工作区超过 `TRI_ORCH_WORKSPACE_RETENTION_SEC`（默认 `86400` 秒）由 GC 清理。可检测到的未合入改动会阻止清理；被 Git 或快照规则忽略的文件不算改动，不要把它们的唯一副本放在临时工作区。 |
| **`~/.codex/backups/`** 与<br>`~/.codex/plugins/cache/tri-orchestrator/` | - `tri-orchestrator-baseline`：首次安装前配置基线<br>- `tri-orchestrator-2*`：历次安装配置与角色备份<br>- `plugin-backup-*`：插件重装/升级残留 staging 目录 | 安装脚本仅备份 `config.toml` 与必要角色 TOML（绝不拷贝 `.venv`），永久保留唯一 `tri-orchestrator-baseline` 与最近 `N=3` 个时间戳备份（`-KeepBackups` / `--keep-backups` 可覆盖）；`scripts/upgrade.py verify` / `prune` 轮转清理已验证完成的旧升级备份与过期 `plugin-backup-*` staging 目录。 |

### 3. 三种存储清理与 GC 触发方式

> **作废从未合入的残留工作区（v0.6.11）**：GC 与 `workspace_cleanup` 只回收**已合入**的工作区。run 失败、归档或完成后，如果某个工作区从未合入且与基线完全一致（快照 +A:0 / ~M:0 / -D:0；Git 检出停在基线提交、分支未变、工作树干净），且该成员的原生动作已全部对账、作者也不在 `working` 状态，就可以用 `workspace_discard(workspace_id, reason)` 作废（`dry_run=True` 只做检查）。只要有任何改动、未决动作或作者仍在工作，一律拒绝，"未合入不删"护栏不变。控制台「存储与清理」面板把这类工作区单独列为「已废弃（可安全作废）」，提供需要二次确认的「作废」按钮；自动 GC 不会删除它们。`run_update(status="archived"|"failed")` 的返回值附带 `residuals`：残留工作区（含能否作废及原因）和未对账的原生动作。概况页的「原生转交」区在有 `needs_reconciliation` 时会显示对账步骤。

1. **后台定时自动 GC**：守护进程内嵌周期 GC 任务（默认每隔 `TRI_ORCH_GC_INTERVAL_SEC=3600` 秒执行一次），仅清理过期 `results/` 缓存以及所属轮次已完成/归档、状态为 `merged` 且静态无未合入变更、超过保留期的隔离工作区。
2. **Web 控制台「存储与清理」面板**：打开 `http://127.0.0.1:18790` 点击顶栏「**存储与清理**」按钮，可实时查看运行时目录各子项占用、逐条区分 `.swarm-workspaces` 状态（「可安全清理」、「含未合入变更待核查」、「进行中」），并一键执行安全清理。
3. **离线 / 手动运行 CLI**：无需打开浏览器或启动 Web 服务，可在终端直接执行垃圾回收（支持在线或离线 SQLite WAL）：

```bash
# 常规安全清理（清理过期 results/ 与达到保留期限的已合入无脏改动工作区）
tri-orchestrator-daemon --gc

# 仅预演估算可释放空间，不实际删除任何文件
tri-orchestrator-daemon --gc --gc-dry-run

# 立即清理所有已完成/已归档轮次的已合入且无未合入改动的工作区（跳过等待保留期）
tri-orchestrator-daemon --gc --gc-all-completed

# 也可直接通过独立 GC 模块运行
python -m orchestrator.gc --dry-run
```

---

## CLI 可执行程序与 MCP 工具

### CLI 入口（5 个）

| 可执行程序 | 入口模块 | 用途说明 |
| :--- | :--- | :--- |
| `tri-orchestrator-mcp` | `orchestrator.mcp_shim:main` | Codex stdio MCP 桥接层，按互斥锁确保单例守护进程在线并转发 MCP 工具调用。 |
| `tri-orchestrator-daemon` | `orchestrator.daemon:main` | 本地 HTTP/SSE 守护进程，承载 SQLite WAL 状态机与 Web 控制台。 |
| `tri-orchestrator-verify` | `orchestrator.verify_runner:main` | 终端验收采集器，按冻结清单、可选 `parallel_group` / `max_parallel` 并发组与白名单 `env_profile` 执行命令，支持 `--heartbeat-url` / `--heartbeat-interval-sec` 保活并输出 SHA-256 `receipt.json`。 |
| `tri-orchestrator-hook` | `orchestrator.hook_runner:main` | 生命周期钩子运行器（硬超时 `<=1.5s`，离线静默 `exit 0`）。 |
| `tri-orchestrator-challenge` | `orchestrator.challenge_runner:main` | 宿主侧只读并发盲审采集器（支持 `--codex-bin` / `CODEX_BIN` 指定 CLI），校验零源码突变并生成挑战回执。 |

### 公开 MCP 工具（37 个，默认列出 20 个核心工具）

> `/api/rpc` 另保留 1 个内部接口 `session_hook_check` 供本地 `tri-orchestrator-hook` 调用，不暴露于模型 `tools/list`。
>
> **工具分级暴露**：默认只展示常规工作流需要的工具。手动注册、步骤修订、挑战者审查和归档等低频原语经 `tool_advanced(tool=..., arguments=...)` 调用；省略 `arguments` 可查看完整参数。见[高级工具表](../plugin/skills/reference/advanced-tools.md)。HTTP、完整目录与代理共用校验；`TRI_ORCH_TOOL_TIER=full` 恢复全部公开 schema。单步 Solo 在创建任务包后用 `accept_local` 开工、`submit_local_for_verify` 提交并准备独立验收、`workflow_closeout_run` 收尾；这三次不包含真实的宿主唤醒、回执和协调者抽查。

| 分类 | 工具列表 |
| :--- | :--- |
| **运行与会话** | `run_create`、`run_update`、`run_list`、`session_bind` |
| **任务契约** | `task_packet_save`、`task_packet_get` |
| **消息板 (`#seq`)** | `board_post`、`board_read`、`board_status`、`board_channel_list` |
| **成员与控制** | `worker_register`、`worker_heartbeat`、`worker_status_update`、`worker_control_ack` |
| **原生派发账本** | `workflow_prepare_dispatch`、`native_action_record_call`、`native_action_update` |
| **持久收件箱** | `inbox_send`、`inbox_get`、`inbox_ack`、`inbox_wait` |
| **工作流与验收** | `workflow_step_save`、`workflow_step_advance`、`workflow_author_turn`、`workflow_verifier_turn`、`artifact_capture`、`verification_record`、`workflow_review`、`workflow_closeout_run` |
| **隔离工作区** | `workspace_create`、`workspace_status`、`workspace_diff`、`workspace_merge_preview`、`workspace_merge`、`workspace_cleanup`、`workspace_discard` |
| **高级工具入口** | `tool_advanced` |

> 补充说明：`inbox_get` 支持按 `message_id` 或 `(run_id, sender, message_key)` 查回派单状态以恢复丢失响应；`artifact_capture` / `workflow_verifier_turn(phase="prepare")` 支持 `parallel_group` 与 `max_parallel`；`workspace_create` / `workflow_prepare_dispatch` 支持 `git_backend`（及 `workspace_base_mode="git_worktree"`）。

---

## 运行时环境变量

> `plugin/.mcp.json` 的 `env_vars` 白名单会将 `CODEX_HOME`、`LOCALAPPDATA`、`USERPROFILE`、`HOME`、`PATH` 以及下表中所有 MCP/守护进程配置变量（`TRI_ORCH_HOST`、`TRI_ORCH_PORT`、`TRI_ORCH_DATA_DIR`、`TRI_ORCH_STALL_SEC`、`TRI_ORCH_WATCHDOG_INTERVAL_SEC`、`TRI_ORCH_PORT_RANGE`、`TRI_ORCH_MAX_SNAPSHOT_MB`、`TRI_ORCH_GC_INTERVAL_SEC`、`TRI_ORCH_RESULT_TTL_SEC`、`TRI_ORCH_RESULT_MAX_FILES`、`TRI_ORCH_WORKSPACE_RETENTION_SEC`、`TRI_ORCH_GIT_BACKEND`、`TRI_ORCH_TOOL_TIER`、`TRI_ORCH_HOST_WAKE`）透传给 `mcp_shim` 与守护进程；`TRI_ORCH_HOOK_TIMEOUT_SEC` 与 `TRI_ORCH_BASE_URL` 仅作用于 `hooks.json` 拉起的 `tri-orchestrator-hook`；`CODEX_BIN` 作用于宿主侧 `tri-orchestrator-challenge` CLI。

| 变量名 | 默认值 | 说明 |
| :--- | :--- | :--- |
| `TRI_ORCH_HOST` | `127.0.0.1` | 守护进程监听地址。 |
| `TRI_ORCH_PORT` | `18790` | 守护进程 HTTP/SSE 端口。 |
| `TRI_ORCH_DATA_DIR` | `%LOCALAPPDATA%\tri-orchestrator`（Windows）<br>`~/.local/share/tri-orchestrator`（POSIX） | 运行时数据目录（`state.db`、`daemon.lock`、`daemon.pid.json`、`results/`、日志等）。 |
| `TRI_ORCH_STALL_SEC` | `300` | 成员心跳停滞基准阈值（秒）。未显式传 `stall_threshold_sec` 时按角色放大：`verifier` ×3、`coder` / `swarm_worker` ×2、其余 ×1；派发时可用 `options.stall_threshold_sec` 为该步骤的成员单独覆盖。成员自己发起的 MCP 调用（发板、读板、`inbox_wait`、作者/验收宏等）会自动续心跳；仅有心跳超时、没有原生状态证据时，看门狗只发 `info` 级「Heartbeat hint」，不再发 alert。 |
| `TRI_ORCH_WATCHDOG_INTERVAL_SEC` | `15` | 看门狗巡检间隔（秒）。 |
| `TRI_ORCH_PORT_RANGE` | `18800..18999` | 隔离工作区测试端口池范围。 |
| `TRI_ORCH_MAX_SNAPSHOT_MB` | `500` | 非 Git 目录 `snapshot` 模式允许复制的最大估算体积（MB），`0` 表示不限制。 |
| `TRI_ORCH_GC_INTERVAL_SEC` | `3600.0` | 守护进程后台定时 GC 轮询间隔（秒）。 |
| `TRI_ORCH_RESULT_TTL_SEC` | `86400` | `results/` 下 MCP 大结果缓存文件的保留时长（秒）。 |
| `TRI_ORCH_RESULT_MAX_FILES` | `50` | `results/` 目录下最多保留的近期缓存文件数量。 |
| `TRI_ORCH_WORKSPACE_RETENTION_SEC` | `86400` | 已完结轮次中已合入且无脏改动的 `.swarm-workspaces` 工作区保留时长（秒）。 |
| `TRI_ORCH_TOOL_TIER` | `core` | MCP `tools/list` 暴露档位：`core` 只列核心工具与 `tool_advanced` 代理，`full` 列出全部公开工具（所有工具在两档下都可按名调用）。 |
| `TRI_ORCH_HOST_WAKE` | `off` | 预留的宿主唤醒开关。`off` 时子代理之间的 `@mention` 交接一律由协调者中转（`followup_task` / `send_message`）。`auto` 时守护进程读取 `codex features list`（缓存 5 分钟），只有 `agent_message_board` 与 `wake_on_agent_messages` 都已开启，`board_post` 返回的 `wake_route` 才会变成 `host_message_board`；否则仍是 `coordinator_relay`。`/api/health` 的 `host_wake` 字段显示当前判断。仅为路由提示，不改变任何门禁。 |
| `TRI_ORCH_GIT_BACKEND` | `clone` | 新启动的守护进程为 `git_backend="auto"` 使用的 Git 工作区后端（`clone` 或 `worktree`）。 |
| `TRI_ORCH_HOOK_TIMEOUT_SEC` | `1.2` | `tri-orchestrator-hook` 本地 RPC 默认超时（秒，硬上限 `1.5`）。 |
| `TRI_ORCH_BASE_URL` | 空（按 `HOST`/`PORT` 拼装） | 可选显式覆盖 `tri-orchestrator-hook` 请求的守护进程基础 URL。 |
| `CODEX_HOME` | `~/.codex` | Codex 本地目录路径；只读目录/模型桥接及安装卸载脚本据此定位状态库与 `agents/`。 |
| `CODEX_BIN` | `codex`（Windows 优先自动探测 `codex.exe`） | `tri-orchestrator-challenge` 宿主侧盲审使用的 Codex CLI 路径（也可通过 `--codex-bin` 覆盖）。 |

**切换 Git 工作区的默认后端：** 在启动承载插件的 Codex 进程前设置变量，例如 Bash 中运行 `TRI_ORCH_GIT_BACKEND=worktree codex`，PowerShell 中运行 `$env:TRI_ORCH_GIT_BACKEND = 'worktree'; codex`。从桌面启动时，变量也须由桌面 Codex 进程继承；安装脚本不会持久化该设置。已有健康守护进程会被复用，仅重开 Codex 或切换会话不会更新它的环境。等活跃任务结束后，按[升级步骤](../README.md#从旧版升级)核对并停止旧后台，再从带有新变量的环境启动 Codex。无需重启就想覆盖单次请求时，显式传入 `git_backend="clone"` 或 `git_backend="worktree"`。

---

## 卸载

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

脚本只移除能核实由安装器写入且未被编辑的角色。若安装前有同名角色，符合移除条件时会恢复备份；自行编辑过或无法确认来源的角色会保留。

**macOS / Linux**：执行上述 `codex plugin ...` 移除命令。若用脚本安装过角色，先和已安装版本的 `plugin/agents/` 源文件比较。只处理能确认由脚本安装且内容未变的角色：原件在配置目录的 `backups/tri-orchestrator-baseline/agents/` 时恢复原件；确认安装前没有该角色时才删除。停止后台前按升级章节核对进程；保留数据目录可保留历史记录。
