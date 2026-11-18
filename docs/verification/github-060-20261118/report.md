# 0.6.0 GitHub 发布记录

日期：2026-11-18。版本：**0.6.0**。当前状态：**发布准备中**。

## 本次变更

- 新增工作流版本 5（`workflow5`）与数据库版本 8（`schema 8`），提供原子宏工具 `workflow_prepare_dispatch`、`native_action_record_call`、`workflow_author_turn`、`workflow_verifier_turn`、`workflow_closeout_run` 与 `session_bind`，消除子代理首轮空转并大幅压缩轮询载荷，同时完整保留 `workflow0..4` 历史门禁语义。
- 新增 `$pipeline` 技能与 `tri-orchestrator-pipeline` 宿主终端 DAG 管线运行器，通过隔离 `codex app-server --listen stdio://` 自动推进并发/串行切片、独立只读验收、最多 3 轮同线程返工及 `--resume-run` 断点续跑。
- 收敛至唯一插件清单 `plugin/.codex-plugin/plugin.json`（移除旧版根级 `plugin/plugin.json`），新增 `plugin/hooks/hooks.json` 与 `tri-orchestrator-hook`（支持 `SessionStart`、`PostCompact`、`UserPromptSubmit`、`Stop` 四类生命周期钩子，离线静默退出）。
- 新增 `tri-orchestrator-challenge` 只读并发盲审采集器，并为 `artifact_capture` / `tri-orchestrator-verify` 增加平台最小环境变量键名白名单（`windows_minimal_py` / `posix_minimal_py`，绝不序列化环境变量值）。
- 对齐 Codex V2 子代理协议与跨平台安装脚本（`install.ps1` / `install.sh` 幂等启用 `multi_agent_v2 = true`、`hooks/list` 四钩子自检及可选信任开关），公开 MCP 工具扩展至 35 个，CLI 可执行入口扩展至 6 个。

## 发布前验证

- 完整严格回归（含真实 `codex.exe` 回环 SSE 端到端套件）：**857 passed, 1 skipped in 504.48s**；退出码 0，启用 `-W error --strict-config --strict-markers`。跳过项为 Windows 不适用的 POSIX 进程组测试。
- 静态检查与脚本行尾核验：`node --check plugin/src/orchestrator/web/app.js` 与 `git diff --check` 退出码均为 0，`scripts/install.sh` 保持纯 LF 行尾。
- 本机正式环境已按受控升级规程从 `0.5.0 / schema 7` 平滑升级至 `0.6.0 / schema 8`，`upgrade.py verify` 确认全部 16 项能力在线，既有 15 张 SQLite 表的全部历史行前缀 SHA-256 100% 一致。
- 本地运行数据库、备份包、原始会话日志和机器私有路径不纳入公共仓库；新增验证摘要已做占位符脱敏。

## 功能与后续使用

日常交互式任务继续由根会话调用 `$boost`、`$teamwork`、`$swarm` 原生 V2 子代理，配合生命周期钩子保持上下文与收尾连续性；多模块确定性批处理与长时无人值守任务可通过 `$pipeline` 在宿主终端驱动独立 `codex app-server` 自动闭环。后台 `18790` 守护进程不新增任何命令执行端点。

## 发布结果

提交、主分支、标签及 Release 的远端读回结果将在发布后记录于此。
