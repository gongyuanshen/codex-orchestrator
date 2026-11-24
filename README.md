# 三重编排器 · Tri Orchestrator

让 Codex 的多代理任务能回答三个问题：**谁在改哪些文件，谁独立验过，哪些结果已经合入？** 插件在本机记录派单、工作区、验收回执和交接历史；浏览器关掉后，记录仍在。它不替代 Codex，也不要求每项任务都开编排。

[最新版本 v0.6.12](https://github.com/gongyuanshen/codex-orchestrator/releases/tag/v0.6.12) · [升级现有安装](#从旧版升级) · [控制台、存储与运维](docs/operations.md) · [MIT 许可证](LICENSE)

## 先选工作方式

| 你的任务 | 怎么做 |
| --- | --- |
| 常规小修、文档，或多个操作要动同一设备、账号、存档 | 用原生 Codex。对同一资源的写入和验收串行做。 |
| 目标已经清楚，但结果必须由实现者之外的人验收 | 用 `$boost`。实现可留在主会话（Solo），也可交给子代理；两条路都保留独立验收。 |
| 动手前还说不清接口、验收标准或文件归谁 | 用 `$teamwork`。先把契约定下来，再分工实现、核验与合入。 |
| 边界已定，有几条能独立推进、又需持续交接的工作线 | 用 `$swarm`。成员在隔离工作区写代码，通过带 `#seq` 编号的消息板交接。 |

## 安装与第一次使用

需要支持 `codex plugin` 和 `multi_agent_v2` 的 **Codex CLI**、[uv](https://docs.astral.sh/uv/) 和 Git。插件使用 Python 3.12 或更高版本。以下命令适用于**首次安装**，将插件固定在本次发布的标签；已有安装请走[升级步骤](#从旧版升级)。

```sh
codex plugin marketplace add gongyuanshen/codex-orchestrator@v0.6.12
codex plugin add tri-orchestrator@tri-orchestrator
codex features enable multi_agent
codex features enable multi_agent_v2
codex plugin list --marketplace tri-orchestrator
codex mcp list
```

确认插件版本为 `0.6.12`、`tri_orchestrator` 已启用，然后打开一个新会话。仅用 CLI 安装不会改写已有角色文件；没有专用角色时，技能会在派单提示中提供角色要求。需要生命周期钩子时，先在 Codex 中检查并信任它们。

要安装 **6 个专用角色**，取同标签的**完整仓库源码**（插件 ZIP 和 wheel 不含安装脚本）。以下命令接在上面的 CLI 安装之后，保留已登记的 GitHub 市场来源：

```sh
git clone --branch v0.6.12 --depth 1 https://github.com/gongyuanshen/codex-orchestrator.git
cd codex-orchestrator
```

| 平台 | 在仓库根目录执行 |
| --- | --- |
| Windows PowerShell | `powershell -ExecutionPolicy Bypass -File scripts/install.ps1 -SkipPluginInstall` |
| macOS / Linux | `bash scripts/install.sh --skip-plugin-install` |

脚本会备份并替换 `CODEX_HOME/agents/` 下的同名角色；有自定义内容先比较差异。`CODEX_HOME` 默认为 `~/.codex`。省略跳过注册选项时，脚本会用本地源码登记市场，适合开发安装。

**试一次：**在已有初始提交的测试仓库中输入 `$boost 新建 hello.txt，内容为 hello world。只改这个文件，由另一人核对内容。` 首次工具调用会启动本地后台。打开[控制台](http://127.0.0.1:18790/boost)，在「流程与验收」查看作者提交、验收命令及结果。控制台与 API 没有登录认证，不要把它们暴露到公网或局域网。

### 从旧版升级

1. **先停写。** 等所有活跃任务结束，关闭使用旧插件的会话。已分离的后台不会随会话退出；核对数据目录中 `daemon.pid.json` 的 PID、URL 与该地址 `/api/health` 的 `pid`、`data_dir`，只停止确认过的那个进程。保留数据目录和备份。
2. **看数据库版本。** `v0.6.11 → v0.6.12` 同为 schema 10，不需要迁移，也不要删除 `state.db`。更旧的 schema 必须在停服后先备份并迁移，再更新插件，见[离线迁移与回滚](docs/operations.md#离线迁移与回滚)。
3. **按原来的来源更新。** 跟踪默认分支的安装：运行 `codex plugin marketplace upgrade tri-orchestrator`，再运行 `codex plugin add tri-orchestrator@tri-orchestrator`。固定在旧标签的安装：先 `codex plugin marketplace remove tri-orchestrator`，再执行上面带 `@v0.6.12` 的市场登记和插件安装命令。本地源码市场则检出目标版本并重跑该版本的安装脚本。
4. **核对实际生效版本。** 装过专用角色的用户要从新标签源码同步角色。用 `codex plugin list --marketplace tri-orchestrator` 检查缓存版本；打开新会话启动后台，到[健康页](http://127.0.0.1:18790/api/health)检查 `software_version`、`schema_version`、`data_dir`。`stale_role_contracts` 列出仍带旧版标记的角色；它不把未安装角色算作过期。升级过数据库的用户还需按运维手册执行迁移后 `verify`。

## v0.6.12 改了什么

- **Solo 少做重复登记，不少做验收。** 单步任务可以一次接单、一次提交并准备独立验收；协调者仍需唤醒验收者、取得真实回执并抽查。显式要求重新验收时，旧 PASS 不再算数。
- **等待与收尾区分开。** Stop 钩子和看门狗使用同一套依据提示下一步；步骤做完但整轮尚未关闭时，提示收尾，而不是把成员误报为卡死。
- **默认加载更少。** 默认列出 20 个核心工具（含 `tool_advanced`）；完整目录仍有 37 个。在统一换行和紧凑 JSON 下，Boost 入口、共享流程和核心工具描述合计从 32,158 降至 21,920 UTF-8 字节（−31.8%）。这是输入大小，不是耗时或 token 的测量。[逐项复核与测试边界](docs/gemini-v0611-native-audit.md)
- **清理前看清楚。** 存储面板按名称与大小列出未分类项目，它们不因此成为删除候选；丢弃链接工作区先核对 Git 登记。

## 隔离与验收边界

- Git 项目默认本地克隆，亦可显式采用 worktree。非 Git 项目按三方目录快照合入：`branch_name=None` 正常，`dirty=true` 表示相对基线存在改动。先预检冲突，再合入。
- `link_ro_cache` 虽然名字带 `ro`，实际是**共享可写**依赖；需要隔离写入用 `copy`，需要新的 Python 环境用 `fresh_venv_uv`。
- 守护进程不会自动重启子代理，也不会自动重放结果未知的写操作。文件有变更不等于通过验收；用冻结的产物、命令与回执确认结果。

## 深入使用

| 想查什么 | 去哪里 |
| --- | --- |
| 控制台、数据目录、清理、环境变量与卸载 | [运维手册](docs/operations.md) |
| 三种工作流的具体步骤 | [Boost](plugin/skills/boost/SKILL.md) · [Teamwork](plugin/skills/teamwork/SKILL.md) · [Swarm](plugin/skills/swarm/SKILL.md) |
| 验收与派发恢复 | [验收契约](plugin/skills/reference/verification.md) · [交接与恢复](plugin/skills/reference/transfers-and-recovery.md) |
| 默认列表之外的工具 | [高级工具表](plugin/skills/reference/advanced-tools.md) |

开发者请从**完整 Git 克隆**（非浅克隆）运行 `uv run --locked pytest -q`；历史迁移测试需要旧提交，部分集成测试还使用 Node.js、Codex CLI 和平台 shell。
