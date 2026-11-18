---
name: pipeline
description: "宿主终端确定性自动闭环 DAG 管线工作流（$pipeline），通过 tri-orchestrator-pipeline CLI 与独立 codex app-server --listen stdio:// 驱动多模块批量重构与长时无人值守任务，实现零主助手中继税与关窗后台续跑。日常交互式任务请继续使用 $boost / $teamwork / $swarm 原生 V2 子代理。"
metadata:
  short-description: "零中继税 App-Server 自动闭环 DAG 管线与断点续跑"
---

## M11 workflow5 App-Server 管线基线（software 0.6.0/schema8）

`$pipeline` 构建于 `workflow5`（`0.6.0/schema8`）之上，复用单守护进程 `http://127.0.0.1:18790`、SQLite WAL 状态机与全套验收门禁，但不占用根会话聊天上下文做逐轮消息中继：

- **诚实归属与协议版本边界**：所有管线步骤固定声明 `execution_kind="app_server_pipeline"`（仅允许在 `workflow_version == 5` 且 `mode` 为 `boost | teamwork | swarm` 的任务中使用），成员身份固定绑定 `appserver://<thread_id>`。执行或排障前请先加载 [共享工作流契约](../route-a-workflow.md)；对于已存储的历史任务（`workflow_version=1/2/3/4/5`），必须严格按照 `board_status.workflow.workflow_version` 分支保留其原始门禁语义，严禁伪造历史回执或混绑身份。
- **双层真实验收与同线程自动返工**：每个 DAG 切片在独立隔离工作区（`.swarm-workspaces`）内由持久化 Author 线程（`workspace-write`）实施，随后由物理只读沙箱的独立 Verifier 线程（`verifier_agent_id != owner_agent_id`，`read-only`）结合 `artifact_capture` + `tri-orchestrator-verify` 真实命令回执（`local_collector_v1` `receipt.json`）完成候选盲审；失败时自动携带精确报错与审查发现回到**同一 `appserver://<author_thread_id>`** 执行返工（首次失败后最多 **3 轮**自动返工，第 4 次失败立即停机进入 `needs_decision`），候选通过后按拓扑顺序串行预检合入主树并执行 `scope="integrated"` 集成验收。
- **确定性沙箱拦截与零守护进程命令 RPC**：`AppServerStdioClient` 对所有越权审批与交互提问服务请求（`item/commandExecution/requestApproval`、`item/fileChange/requestApproval`、`item/permissions/requestApproval`、`mcpServer/elicitation/request`）在 `<100ms` 内确定性返回 `decline`（遇 `item/tool/requestUserInput` 立即收口并标记 `needs_clarification`），并在 Windows 下挂载 `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` 确保零孤儿进程；`18790` 守护进程绝不新增任何子进程或命令执行 RPC。

---

# `$pipeline` — 确定性 App-Server 自动闭环 DAG 管线

在保持文件所有权互斥、独立双层验收与最多 3 轮同线程返工红线不变的前提下，将多模块执行循环下沉至宿主终端 `tri-orchestrator-pipeline` CLI，彻底消除主助手中继协议税。

## 何时使用 `$pipeline`（与 `$boost` / `$teamwork` / `$swarm` 的边界）

- **优先使用 `$pipeline` 的场景**：
  1. **多模块批量重构与确定性 DAG 切片**：跨多个目录/子系统、依赖拓扑清晰且可通过真实测试命令客观判定的批量改造。
  2. **长时无人值守任务**：耗时较长、包含多轮编译测试与串行集成验证的批处理工程任务。
  3. **零主助手中继税与关窗韧性**：用户希望消除根会话逐轮轮询/转发的 Token 开销，或希望在关闭当前聊天窗口后管线仍在独立终端持续推进并由 `http://127.0.0.1:18790` 实时监控。
  4. **显式指令调用**：用户在提示词中明确指定使用 `$pipeline`。
- **继续使用 `$boost` / `$teamwork` / `$swarm` 的场景**：
  - 日常交互式单点开发与快速修复（`$boost`）、需要多轮深度需求访谈与动态方案共创的中大型功能（`$teamwork`）、或需要主助手在聊天内实时协同干预的交互式并发流（`$swarm`），请继续使用 Codex V2 原生子代理（`/root/<task_name>`，`execution_kind="native"` 或 Boost Solo `coordinator_local`）。

---

## 确定性 3 步宿主工作流（Deterministic 3-Step Host Workflow）

### 步骤 1：只读勘探上下文并冻结 `pipeline_spec.json`

1. **精简上下文勘探（最多 1 轮澄清）**：
   - 先只读检查仓库结构、模块接口边界与现有测试命令。
   - 若用户需求存在实质性歧义，**最多进行 1 轮简明澄清**（给出 2–4 个具体选项并标注推荐默认值）；若目标、边界与验收命令已清晰，直接进入规格冻结，不空转追问。
2. **编写并落盘冻结的 `pipeline_spec.json`**：
   - 将完整规格写入运行工作目录外或受控临时路径（避免污染目标 Git 工作树），所有并行无依赖步骤的 `file_ownership` **必须严格互斥、零路径重叠**：

```json
{
  "project_root": "G:/example/repo",
  "mode": "swarm",
  "title": "批量重构鉴权与审计模块",
  "max_workers": 2,
  "allow_network": false,
  "steps": [
    {
      "step_key": "auth-token",
      "title": "重构令牌签发模块",
      "instruction": "仅修改 src/auth/token.py 与 tests/test_token.py，实现过期宽限校验并补充边界测试。",
      "file_ownership": ["src/auth/token.py", "tests/test_token.py"],
      "acceptance_criteria": [
        "AC-TOKEN-1: 过期令牌被拒绝且宽限期内刷新正常",
        "AC-TOKEN-2: tests/test_token.py 全量通过"
      ],
      "commands": [
        {
          "id": "pytest-token",
          "argv": ["python", "-m", "pytest", "-q", "-W", "error", "tests/test_token.py"],
          "cwd": ".",
          "timeout_sec": 120,
          "criteria": [
            "AC-TOKEN-1: 过期令牌被拒绝且宽限期内刷新正常",
            "AC-TOKEN-2: tests/test_token.py 全量通过"
          ]
        }
      ],
      "env_profile": "windows_minimal_py",
      "dependencies": [],
      "complexity": "normal"
    },
    {
      "step_key": "audit-hook",
      "title": "接入审计事件流",
      "instruction": "在 auth-token 合入后，仅修改 src/audit/events.py 与 tests/test_audit.py 对接新令牌声明。",
      "file_ownership": ["src/audit/events.py", "tests/test_audit.py"],
      "acceptance_criteria": [
        "AC-AUDIT-1: 审计记录完整保留令牌签发字段并通过四维高复杂审查"
      ],
      "commands": [
        {
          "id": "pytest-audit",
          "argv": ["python", "-m", "pytest", "-q", "-W", "error", "tests/test_audit.py"],
          "cwd": ".",
          "timeout_sec": 120,
          "criteria": [
            "AC-AUDIT-1: 审计记录完整保留令牌签发字段并通过四维高复杂审查"
          ]
        }
      ],
      "env_profile": "windows_minimal_py",
      "dependencies": ["auth-token"],
      "complexity": "high"
    }
  ]
}
```

- **核心字段约束**：
  - `project_root`：目标仓库绝对路径。
  - `mode`：`"boost" | "teamwork" | "swarm"`（默认 `"swarm"`，对应 Web 控制台 `/boost`、`/teamwork`、`/swarm` 入口与模式门禁；`boost` 自动包含独立 `challenge` 门禁）。
  - `title`：任务标题（兼容 `objective`）。
  - `max_workers`：并发度上限，**严格限制在 `1..4`**（`<1` 或 `>4` 立即校验拒绝）。
  - `steps[*].file_ownership`：相对 `project_root` 的独占文件路径列表；并行步骤严禁重叠（含大小写与父子目录包含重叠）。
  - `steps[*].acceptance_criteria`：非空唯一字符串列表，必须被 `commands[*].criteria` 完整覆盖。
  - `steps[*].commands`：真实硬验证命令列表（`id`、`argv`、`cwd`、`timeout_sec`、`criteria`，可选显式键名白名单 `env_keys`），由 `tri-orchestrator-verify` 执行并产出 SHA-256 回执。
  - `steps[*].env_profile`：Windows 默认 `"windows_minimal_py"`，POSIX 默认 `"posix_minimal_py"`（仅白名单环境变量键名，严禁序列化环境变量值）。
  - `steps[*].dependencies`：前置 `step_key` 数组（必须构成无环 DAG，前置步骤完成合入与集成验收后才准入后续步骤）。
  - `steps[*].complexity`：`"normal" | "high"`（`"high"` 强制要求 `review_dimensions` 的 `specification / code / stress / evidence` 四项全为 `PASS`）。

---

### 步骤 2：在宿主终端调用 `tri-orchestrator-pipeline`

1. **定位 `tri-orchestrator-pipeline` 可执行文件（零硬编码版本号）**：
   - 若当前会话已调用过 `run_create` 或 `board_status(compact=True)`，优先直接使用返回的 `cli_executables.pipeline` 精确绝对路径（`cli_executables` 同时暴露同 `.venv` 下的 `tri-orchestrator-verify` 与 `tri-orchestrator-challenge`）；
   - 否则优先使用宿主 `PATH` 中的 `tri-orchestrator-pipeline`；
   - 若未加入全局 `PATH`，按插件缓存通配目录动态解析最新已安装版本（不假设固定版本号）：
     - **Windows (PowerShell)**：`Get-ChildItem (Join-Path $(if ($env:CODEX_HOME) { $env:CODEX_HOME } else { Join-Path $HOME '.codex' }) 'plugins\cache\tri-orchestrator\tri-orchestrator\*\.venv\Scripts\tri-orchestrator-pipeline.exe') -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -First 1 -ExpandProperty FullName`（通配 `plugins/cache/tri-orchestrator/tri-orchestrator/*/.venv` 下的 `Scripts/tri-orchestrator-pipeline.exe`）
     - **POSIX (sh/bash/zsh)**：`ls -1dt "${CODEX_HOME:-$HOME/.codex}"/plugins/cache/tri-orchestrator/tri-orchestrator/*/.venv/bin/tri-orchestrator-pipeline 2>/dev/null | head -n 1`（通配 `plugins/cache/tri-orchestrator/tri-orchestrator/*/.venv` 下的 `bin/tri-orchestrator-pipeline`）
2. **标准调用命令**：
   ```bash
   tri-orchestrator-pipeline --spec <path/to/pipeline_spec.json> --output <path/to/pipeline_summary.json>
   ```
3. **前台执行 vs 后台/分离终端执行**：
   - **前台同步执行（Foreground Execution，适合短/中型管线）**：
     - 在宿主终端前台运行并等待进程退出；
     - 退出码为 `0` 时，读取 `--output` 生成的 `pipeline_summary.json`，核实 `run_id`、`status == "completed"`、`sha256`、`elapsed_sec`、各步骤 `attempt`、`appserver://<thread_id>` 绑定与真实 `tri-orchestrator-verify` 回执路径（`receipt_files`），向用户交付精简实证摘要。
   - **后台/分离终端执行（Detached / Background Execution，适合长时批处理或关窗运行）**：
     - 在独立后台终端会话启动 `tri-orchestrator-pipeline --spec <path> --output <summary.json>`，立即向用户返回输出摘要路径与本地控制台地址 `http://127.0.0.1:18790`（如 `http://127.0.0.1:18790/swarm`）；
     - Web 控制台对所有管线步骤与成员展示高对比度 `App-Server 管线` 诚实归属徽章；用户在面板点击「请求暂停」或「请求结束任务」（`pause` / `stop` `worker_control`）时，运行器轮询会在 **`<=2s`** 内向活跃线程下发 `turn/interrupt`、确认 `worker_control_ack` 并安全回收 `app-server` 子进程树（完整保留磁盘工作区文件）。

---

### 步骤 3：严格安全护栏与停机恢复（`--resume-run`）

1. **严禁主助手双写（Zero Root Dual-Write）**：
   - 当 `tri-orchestrator-pipeline` 正在运行时，根会话主助手**严禁**对 `project_root` 或 `.swarm-workspaces` 内由管线步骤独占的任何文件执行编辑、写入、删除或变更性 Git 命令；主助手仅允许通过只读查询（`board_status(run_id=..., compact=True)`、读取 `pipeline_summary.json`）观察进度。
2. **严禁伪造原生 V2 身份路径（Honest Identity Only）**：
   - 管线成员与步骤仅允许使用 `appserver://<thread_id>` 与 `execution_kind="app_server_pipeline"`；**严禁**传入或伪造 `/root/...`（`task_name` / `pending_agent_path`）原生 V2 子代理路径，严禁将同一成员跨 `native`/`coordinator_local` 与 `app_server_pipeline` 混用。
3. **停机诊断与断点续跑（`needs_decision` / `needs_clarification` / `needs_reconciliation`）**：
   - 当管线因连续失败达到上限（初始执行 + **3 轮**自动返工仍未通过）停在 `needs_decision`，或因 `requestUserInput` 提问 / 异常中断停在 `needs_clarification` / `needs_reconciliation`（非零退出码）时：
     1) **严禁盲目新建替代任务覆盖现场**；先读取 `pipeline_summary.json`、`board_status(run_id=run_id, compact=True)`、各步骤 `receipt.json` 日志与审查 `findings`，定位具体失败断言或待澄清事项。
     2) 向用户汇报证据并获取明确决策；若用户授权修订验收范围或补充澄清信息，先按规更新任务契约或解除对账阻塞。
     3) 使用原 `run_id` 执行确定性断点续跑：
        ```bash
        tri-orchestrator-pipeline --resume-run <run_id> --output <path/to/pipeline_summary.json>
        ```
        续跑器自动从 `run.metadata["pipeline_spec"]` 恢复冻结规格，**跳过所有已 `done` 的前置步骤**（绝不重复执行已合入步骤），并通过 `thread/read` + `thread/resume` 核验且复用原绑定的 `appserver://<thread_id>` 线程继续推进剩余 DAG（若原线程处于 `systemError` 或缺失，则安全停在 `needs_decision`，绝不静默替换原作者身份）。
