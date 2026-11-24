# 三重编排器 · Tri Orchestrator

Tri Orchestrator 是一个在本机运行的 Codex 插件。它把一项任务拆成有负责人和验收标准的步骤：作者在自己的工作区修改，另一位成员对提交的内容运行检查，协调者看过结果后再合入。你可以在浏览器里看到谁在做什么、改了哪些文件、哪条检查没过，以及最后合入的是哪一版。

## 安装

需要 Git、uv、Python 3.12 或更高版本，以及支持 `codex plugin`、`multi_agent` 和 `multi_agent_v2` 的 Codex CLI。首次安装执行：

```sh
codex plugin marketplace add gongyuanshen/codex-orchestrator@v0.6.12
codex plugin add tri-orchestrator@tri-orchestrator
codex features enable multi_agent
codex features enable multi_agent_v2
codex plugin list --marketplace tri-orchestrator
codex mcp list
```

最后两条命令分别检查插件和它的工具服务：插件应为 `0.6.12`，`tri_orchestrator` 应为已启用。**打开新的 Codex 会话**使用插件；第一次调用工具时后台会自动启动。

### 可选：安装六个专用角色

只装插件也能使用三种模式：没有专用角色时，技能会在派单时给出角色要求。要让 Codex 使用独立的角色配置，从与插件相同标签的**完整仓库源码**运行安装脚本。插件发行 ZIP 和 Python wheel 不包含这些脚本。

```sh
git clone --branch v0.6.12 --depth 1 https://github.com/gongyuanshen/codex-orchestrator.git
cd codex-orchestrator
```

| 平台 | 在源码根目录执行 |
| --- | --- |
| Windows PowerShell | `powershell -ExecutionPolicy Bypass -File scripts/install.ps1 -SkipPluginInstall` |
| macOS / Linux | `bash scripts/install.sh --skip-plugin-install` |

跳过的是**重复登记市场**，不是角色安装，这样能保留前面设置的 GitHub 来源。脚本会备份配置并安装 `interviewer`（澄清需求）、`planner`（划分任务）、`investigator`（只读调查）、`coder`（实现）、`verifier`（独立验收）和 `swarm_worker`（并行实现）。角色写入 `CODEX_HOME/agents/`，`CODEX_HOME` 默认为 `~/.codex`；如果改过同名角色，先比较差异。

插件有四个生命周期钩子：`SessionStart`、`UserPromptSubmit`、`PostCompact` 和 `Stop`。要使用会话恢复或收尾提醒，先在 Codex 中检查并信任这些钩子。升级后如果显示「已修改」，需要重新确认，不必重装数据库。

## 第一次使用：做一个能核对结果的小任务

在**已有一次提交**的测试仓库中打开新的 Codex 会话，输入：

```text
$boost 新建 hello.txt，文件内容为一行 hello world。只改这一个文件，让独立验收者读取文件并核对内容。
```

打开 `http://127.0.0.1:18790/boost`，选中这轮任务，在「流程与验收」里看三件事：作者是否提交、验收者实际运行了什么命令、整轮是否已完成。目录里出现 `hello.txt` 只说明文件写出来了；有独立验收回执而且协调者已收尾，才算这轮结束。

## 三种模式怎么选

| 情况 | 用法与执行过程 |
| --- | --- |
| 修一个边界清楚的问题，需要另一个人验收 | **`$boost`**：先定允许修改的文件和通过条件。简单改动由主会话实现（Solo），需要隔离才委派作者。独立验收者针对候选内容运行检查，协调者抽查差异和输出；不通过交回原作者，仍由原验收者复验。 |
| 多人要一起做，但接口、验收标准或文件归属还没定 | **`$teamwork`**：先澄清需求，把接口、每个人能改的文件、验收条件和合入顺序写进任务包。作者在各自工作区实现；先验候选，再按顺序合入并验集成结果。后续里程碑依赖前一步时，等验收及必要的交接确认完成再开工。 |
| 工作已拆清，有几条能独立推进且需要持续交接的工作线 | **`$swarm`**：每位作者只写自己的工作区。结果、阻塞和交接发到带 `#seq` 编号的消息板，聊天里报编号即可。协调者按编号增量查看，收到通过的候选后依次合入，再验集成结果。 |
| 改一段文档，或几项操作都要写同一台设备、账号、存档 | **用原生 Codex**。对同一资源的写入和验证串行做，不必为了用插件额外开任务。 |

例如，接口还没定，可以输入 `$teamwork 给订单加 CSV 导出，先确认字段和验收样例，再拆分实现。` 如果接口和样例已定、前后端各有独立目录，可以输入 `$swarm 按已定的 CSV 样例并行实现后端导出和前端下载，分别验收后再合入。`

## 控制台：看进度、查回执、接续中断

三个入口是 `http://127.0.0.1:18790/boost`、`http://127.0.0.1:18790/teamwork`、`http://127.0.0.1:18790/swarm`。它们共用同一后台和数据库，关闭浏览器不影响历史记录。**控制台和 API 没有登录认证，端口应只对本机开放。**

| 想知道什么 | 在哪里看，如何判断 |
| --- | --- |
| 某项任务在哪个会话？ | 侧栏按项目、会话、轮次展开，`Ctrl+K` 可以跨项目搜索。概况里能看触发任务的原话、执行目录、成员和模型，也能导出 Markdown 或 JSON 报告。 |
| 卡在实现、验收还是等合入？ | 「流程与验收」显示步骤、当前阶段及耗时。验收者准备命令清单不等于测试已执行；看实际记录的命令、结果和对应产物。所有步骤已完成但整轮仍活跃时，应由协调者收尾。 |
| 成员改了哪些文件？ | 在工作区检查器查看文件列表、逐行差异和合入冲突预检。消息板可按频道、成员或关键词筛选；人工指令发到 `human` 频道，由协调者处理。 |
| 心跳超时，要重派吗？ | 心跳超时不是进程死亡的证明。先查工作区差异、最后一条消息和 Codex 中原线程的实际状态；原成员空闲时接续原线程。守护进程不会自动重启成员。 |
| 派单响应丢了怎么办？ | 用 `inbox_get` 按原来的轮次、发送者和消息键查回票据。`not_visible_yet` 不表示写入失败；`needs_reconciliation` 表示要核对原线程实际执行情况。无法确认时保留工作区，不另起一轮重复派单。Solo 本地开工没有作者票据，按原 `step_key` 查步骤。 |

## 工作区和验收

- **Git 项目**默认本地克隆。作者在分配的分支提交，验收前工作树应保持干净。Git 2.36 及以上可按次选 `git_backend="worktree"`，共享原仓库的 Git 对象；不要在清理过程中改分支或手动搬目录。
- **非 Git 项目**用目录快照。大项目先用 `execution_root` 限定实际要改的子目录，再在该目录放 `.triignore` 排除大文件。默认快照上限 500 MB，超限会拒绝复制。`branch_name=None` 是正常结果，`dirty=true` 表示相对基线有改动，并非合入失败。
- **依赖和环境文件**：`dep_mode="none"` 不准备依赖；`copy` 给工作区独立副本；`fresh_venv_uv` 建新的 Python 环境，不创建 `node_modules`。`link_ro_cache` 是**共享可写**依赖，不要把它当只读隔离。无需额外复制 `.env` 时用布尔值 `copy_env: false`；已提交的文件仍随 Git 检出。
- **验收依据**：原验收者先冻结候选文件和命令清单，在自己的终端执行，再按验收条件登记 `PASS`、`FAIL` 或 `NOT_RUN`。可独立的连续命令用同一 `parallel_group` 和正整数 `max_parallel` 分组；共享设备或运行环境的检查保持串行。返工交给原作者，显式要求复验时必须取新证据，旧 `PASS` 不能再次交差。
- **合入顺序**：委派工作先用 `workspace_merge_preview` 看冲突，再逐个合入已通过的候选并核对集成结果。如果后续合入改到了已验内容，重新打开受影响步骤验收。Solo 直接改主工作区，没有分支要合；候选未变且证据有效时可直接收尾。

## 从旧版升级

**从 v0.6.11 到 v0.6.12 不需要迁移数据库**：两版都是 schema 10，保留 `state.db`。插件缓存、后台进程和专用角色是三份不同的状态，不能只看市场里显示的版本。

1. **停写并确认要停的进程。** 等活跃任务结束，关闭使用旧插件的会话。先记录 `http://127.0.0.1:18790/api/health` 返回的 `pid`、`data_dir`、`schema_version`，与数据目录中 `daemon.pid.json` 的 PID 和地址核对，只停止匹配的后台。停服后备份数据目录；安装器的配置备份不包含任务数据库。数据库早于 schema 10 的，先执行下方的离线迁移。
2. **更新插件。** 固定在旧标签时，依次运行下面三条命令。若原来跟踪默认分支，改用 `codex plugin marketplace upgrade tri-orchestrator`，然后运行第三条；若登记的是本地源码市场，则检出目标标签并重跑该版本的安装脚本。

   ```sh
   codex plugin marketplace remove tri-orchestrator
   codex plugin marketplace add gongyuanshen/codex-orchestrator@v0.6.12
   codex plugin add tri-orchestrator@tri-orchestrator
   ```

3. **同步角色。** 只在装过专用角色时需要：从新标签的完整源码执行前面对应平台的角色安装命令。检查四个钩子的信任状态。
4. **确认新后台。** 用 `codex plugin list --marketplace tri-orchestrator` 看缓存版本，打开新会话并调用一次插件工具，再读 `/api/health`。本版应返回 `software_version` 为 `0.6.12`、`schema_version` 为 `10`，`data_dir` 仍是原目录。`stale_role_contracts` 列出带旧版本标记的角色；空列表不代表六个角色都已安装。

### 旧数据库：离线迁移与回滚

下面以 PowerShell 为例。在**新版本的源码根目录**执行，输入旧插件缓存目录、原数据目录和一个**尚不存在**的备份目录的绝对路径。三者与源码的 `plugin` 目录不能重叠。非默认端口要给 `prepare` 加 `--port` 和实际端口号。每条命令成功后再执行下一条。

```powershell
$old = Read-Host '旧版插件缓存目录'
$data = Read-Host '原后台数据目录'
$backup = Read-Host '新的升级备份目录'
uv run --locked --project ./plugin python scripts/upgrade.py prepare --source-plugin ./plugin --target-plugin "$old" --data-dir "$data" --backup-dir "$backup"
uv run --locked --project ./plugin python scripts/upgrade.py apply --database-only --backup-dir "$backup"
```

`prepare` 备份数据库和新旧插件文件；`apply --database-only` 在备份目录中迁移并核对历史数据，通过后才更新原数据库，不会改写旧插件缓存。现在安装目标版本、启动新后台，再从同一终端核验：

```powershell
uv run --locked --project ./plugin python scripts/upgrade.py verify --backup-dir "$backup" --keep-backups 3
```

如果需要撤回此次数据库迁移，先停新后台，执行下面的命令。成功后按原来源装回旧版本、同步旧角色，再启动后台。若数据库已有升级后的新写入，脚本会拒绝覆盖，不要强行还原旧库。

```powershell
uv run --locked --project ./plugin python scripts/upgrade.py rollback --backup-dir "$backup"
```

## 数据存在哪里，哪些能清理

| 位置 | 内容和保留规则 |
| --- | --- |
| Windows：`%LOCALAPPDATA%\tri-orchestrator`；macOS / Linux：`~/.local/share/tri-orchestrator` | `state.db` 保存任务历史，`receipts/` 保存回执，`daemon.log` 和 `shim.log` 用于排错。`results/` 存放过大而未直接返回的工具结果；收到 `delivery: "file"` 时按返回路径读取，不要重新执行写操作。 |
| 源码根下的 `.swarm-workspaces/` | 作者的隔离副本。清理前确认已合入且没有未合入改动。被 Git 或快照规则忽略的文件不计入改动，不要把唯一副本放在这里。 |
| `CODEX_HOME/backups/` | 安装前基线和配置、角色备份，默认保留基线和最近三份安装备份；不是运行数据库的备份。 |

控制台顶栏的「存储与清理」会区分可清理、含改动和需核查的项目。自动清理默认每小时运行，只处理过期结果缓存，以及已完成或归档任务中超过保留期、已合入且无未合入改动的工作区。未分类文件即使占用空间，也不会因此被删除。

要手动检查，在对应版本的源码根目录先预演，确认列出的内容后再清理。若使用自定义数据目录，请在同一个终端设置 `TRI_ORCH_DATA_DIR` 指向该目录。

```sh
uv run --locked --project ./plugin tri-orchestrator-daemon --gc --gc-dry-run
uv run --locked --project ./plugin tri-orchestrator-daemon --gc
```

**未合入的工作区不会被自动清理。** 已结束的任务若留下从未合入、与基线完全一致的副本，可以在面板中单独作废。`workspace_discard` 的 `dry_run=true` 只检查；只要有改动、作者仍在工作、原生动作未对账或链接工作区被锁定，就不能作废。

## 工具和配置

日常在对话中输入模式名即可。要诊断或手动接续某一步，MCP 默认展示 **20 个核心工具**；另外 **17 个**通过 `tool_advanced` 使用。下面是完整的 37 个工具：

| 用途 | 默认可见 | 通过 `tool_advanced` 调用 |
| --- | --- | --- |
| 轮次与任务包 | `run_create`、`run_list`、`task_packet_save`、`task_packet_get` | `run_update`、`session_bind` |
| 消息板与成员 | `board_post`、`board_read`、`board_status`、`worker_control_ack` | `board_channel_list`、`worker_register`、`worker_heartbeat`、`worker_status_update` |
| 派单与收件箱 | `workflow_prepare_dispatch`、`native_action_record_call`、`inbox_get`、`inbox_wait` | `native_action_update`、`inbox_send`、`inbox_ack` |
| 实现与验收 | `workflow_author_turn`、`workflow_verifier_turn`、`workflow_closeout_run` | `workflow_step_save`、`workflow_step_advance`、`artifact_capture`、`verification_record`、`workflow_review` |
| 工作区 | `workspace_status`、`workspace_diff`、`workspace_merge_preview`、`workspace_merge` | `workspace_create`、`workspace_cleanup`、`workspace_discard` |
| 工具查询 | `tool_advanced` | — |

例如 `tool_advanced(tool="workspace_discard")` 只返回参数说明，并不会删除工作区；传入 `arguments` 才调用工具。命令行有五个入口：`tri-orchestrator-mcp` 连接 Codex 和后台，`tri-orchestrator-daemon` 提供本机服务与清理命令，`tri-orchestrator-verify` 在验收者终端跑冻结的命令，`tri-orchestrator-challenge` 做只读盲审，`tri-orchestrator-hook` 处理四个生命周期事件。

| 环境变量 | 默认值及用途 |
| --- | --- |
| `TRI_ORCH_HOST`、`TRI_ORCH_PORT`、`TRI_ORCH_DATA_DIR` | 监听 `127.0.0.1:18790`；数据目录按上表选择。`TRI_ORCH_PORT_RANGE` 默认 `18800..18999`，是工作区测试端口池，不是控制台端口。 |
| `TRI_ORCH_GIT_BACKEND`、`TRI_ORCH_MAX_SNAPSHOT_MB` | Git 后端默认 `clone`，可选 `worktree`。非 Git 快照上限默认 `500` MB，`0` 表示不限制。 |
| `TRI_ORCH_GC_INTERVAL_SEC`、`TRI_ORCH_WORKSPACE_RETENTION_SEC` | 默认每 `3600` 秒清理一次；符合条件的已合入工作区默认保留 `86400` 秒。 |
| `TRI_ORCH_RESULT_TTL_SEC`、`TRI_ORCH_RESULT_MAX_FILES` | 大结果缓存默认保留 `86400` 秒，最多保留最近 `50` 份。 |
| `TRI_ORCH_STALL_SEC`、`TRI_ORCH_WATCHDOG_INTERVAL_SEC` | 心跳停滞基准默认 `300` 秒，实际阈值按角色调整；后台每 `15` 秒检查一次。 |
| `TRI_ORCH_TOOL_TIER`、`TRI_ORCH_HOST_WAKE` | 工具默认 `core`，设为 `full` 可展开全部 37 项。唤醒默认 `off`，由协调者中转；`auto` 只在宿主支持且启用对应功能时返回宿主唤醒提示，不证明消息已送达。 |
| `TRI_ORCH_BASE_URL`、`TRI_ORCH_HOOK_TIMEOUT_SEC` | 只影响钩子。默认按监听地址和端口连接后台，RPC 超时 `1.2` 秒，硬上限 `1.5` 秒。 |
| `CODEX_HOME`、`CODEX_BIN` | 前者默认为 `~/.codex`；后者可指定宿主盲审使用的 Codex CLI 路径。 |

后台配置在进程启动时读取。只开一个新会话会复用健康的旧后台；要更换端口、数据目录或默认工作区后端，先结束活跃任务、按升级步骤核对并停止旧后台，再从设好变量的环境启动。

## v0.6.12 的变化

- **Solo 少登记，不省验收。** 单步任务用 `accept_local` 一次接单，用 `submit_local_for_verify` 提交并准备验收。准备失败时提交一起回滚；协调者仍要唤醒独立验收者、核对回执并抽查。显式复验不会复用旧的通过记录。
- **等待和收尾分开显示。** 步骤做完、只差协调者收尾时，不再把验收者误报为卡死。派单断连可以按原消息键核对；角色版本过期、工作区不能作废的原因和未分类磁盘占用也能在控制台看到。
- **默认加载更少。** 按相同换行和 JSON 格式统计，Boost 入口、共享流程和核心工具描述合计由 32,158 降至 21,920 个 UTF-8 字节，减少 31.8%。这是固定输入大小，不是 token 数或任务耗时。
- **验证范围。** Windows 发布回归 1,092 项通过、3 项跳过。跳过项是 POSIX 命名管道、POSIX 进程组和需要明确旧基线及场景的升级演练；没有用这些结果推算真实多代理任务的提速。

## 卸载

只通过 CLI 安装过插件时，结束任务并核对、停止本机后台，然后移除插件和市场：

```sh
codex plugin remove tri-orchestrator@tri-orchestrator
codex plugin marketplace remove tri-orchestrator
```

Windows 如果还用脚本装过角色，可直接从完整源码根目录运行 `powershell -ExecutionPolicy Bypass -File scripts/uninstall.ps1`。它会处理插件、市场及能确认由安装器写入且未改动的角色；自行编辑过的角色保留。默认保留任务历史，只有明确要删除数据才加 `-RemoveDataDir`。

macOS / Linux 用上面的两条 CLI 命令移除插件和市场。另装过角色时，只处理能确认是安装器写入且未改动的角色：安装前有同名文件，从 `CODEX_HOME/backups/tri-orchestrator-baseline/agents/` 恢复；安装前没有才移除。不删运行数据目录即可保留历史。

## 开发与测试

在**非浅克隆**的完整仓库根目录运行 `uv run --locked pytest -q`。历史迁移测试会读取旧提交，部分集成测试还使用 Node.js、Codex CLI 和平台 shell。项目采用 MIT 许可证。
