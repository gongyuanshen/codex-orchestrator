# 0.5.0 GitHub 发布记录

日期：2026-11-17。版本：**0.5.0**。当前状态：**已发布**。

## 本次变更

- 增加原生调用账本，明确记录派发、完成和中断后核对。
- 增加命令行验收采集器，验收回执绑定实际产物与执行结果。
- 增加文件冲突准入、紧凑交接包、结构化审查与有上限的返工流程。
- 优化 Boost 路由、按接收者批量消息和停滞分类。
- 修复 Windows 后台进程启动；新任务使用工作流版本 4，数据库升级至版本 7，保留旧任务规则。

## 发布前验证

- 合并主分支后的严格回归：**722 passed, 1 skipped in 96.28s**；退出码 0，启用 `-W error --strict-config --strict-markers`。跳过项为 Windows 不适用的 POSIX 进程组测试。发布前首轮回归为 97.43 秒。
- 独立发布审查的元数据、升级和启动锁检查：**13 passed in 6.86s**。
- 正式安装、桌面 29 工具写读、严格原生交接、异常控制、返工上限和三模式界面已在此前收尾验收中核验。
- 本地数据库、原始宿主记录、ZIP 证据和备份不纳入公共仓库。源码、测试、接口说明和经审查的文字摘要纳入本次发布。

## 功能与后续使用

日常任务由主助手调用 Codex 原生代理，插件负责持久计划、记录、隔离工作区、验收和交接。主助手不再协调后，插件不独立唤醒或接管旧代理。自动接管研究已经结束。

同一任务、同一模型的实际效果由用户分别试用比较；本轮不追加成本、跨天或非 Windows 比较。

## 发布结果

- 仓库：[gongyuanshen/codex-orchestrator](https://github.com/gongyuanshen/codex-orchestrator)，主分支 `main`。
- 发布源码提交：[`611a89f`](https://github.com/gongyuanshen/codex-orchestrator/commit/611a89f049657f33801fa0a9bca0aab7de6239d5)。
- 正式版本：[v0.5.0](https://github.com/gongyuanshen/codex-orchestrator/releases/tag/v0.5.0)；非草稿、非预发布。
- 已读回远端主分支与标签，`v0.5.0` 指向上述源码提交；标签中的插件版本为 `0.5.0`，市场入口指向 `./plugin`。
- Release 标题与正文均为英文，正文只包含变更、安装和下载说明。
- 三个附件均重新下载，大小与 SHA-256 同本地和 GitHub 返回值一致。插件 ZIP 的 38 个文件、wheel 的 20 个运行时文件均逐字节对应 Git 源码。

| 附件 | 字节数 | SHA-256 |
|---|---:|---|
| `SHA256SUMS` | 201 | `9931d4e5a72abfc826ded1be7c898d9603f3df5103902f1f45118e14f0152a29` |
| `tri-orchestrator-0.5.0.zip` | 271745 | `fc79a2e86ad79327efed6a66d4833a1ef8daba544b3addb21abfb6f8e08d87d0` |
| `tri_orchestrator-0.5.0-py3-none-any.whl` | 161822 | `346f0ee8b92eb4d80ba5297b55f52e3222bb2dffc1955d3d1a94b5f940e11726` |

本次 GitHub 发布没有重新安装插件或迁移本机正式数据。发布包使用 Git 标准换行，并清理两个源码文件末尾的空行，程序逻辑未变。原始验收资料和此前冻结的安装包继续保留在本地。
