# 第一批原始规格归档：M0、M1、M2 与 R1 研究条件

日期：2026-11-17。本文保留开发前的完整规格，不作为当前待办清单。

- 当前状态以 [下一阶段开发方案](G:/工具/下一阶段开发方案.md) 和各阶段验收报告为准。
- 下方复选框是原始要求，保持原样；不表示当前全部未完成，也不把未做的比较实验改成已通过。
- M0 已完成确定性六案例基线；36 次前后对照、原生 token/成本和 Antigravity 同题比较未执行或不可获得。
- M1/M2、第一批收尾和 Windows 生命周期修复已完成；M3–M5 不在本归档范围。

## M0：先固定“变好了”的判断标准

**产物：**一份可重复运行的隔离案例清单、现有版本基线记录、能力/来源等级清单。沿用现有模式验收脚本与夹具，不复制生产任务充当新实验。

- [ ] 固定 6 个案例：Boost 已知修复、Boost 未知根因；Teamwork 双里程碑、Teamwork 中断交接；Swarm 并行消息、Swarm 停止/断连核对。每个案例都含一个可实际触发的失败，不要求代理口头扮演失败。
- [ ] 首轮各运行一次，确认夹具与可测指标；发布对比时每版本每例 3 次，旧/新共 36 次隔离运行。这是小样本工程比较，不是统计上的能力排名。
- [ ] 记录：正确完成、实际发现的缺陷、返工轮次、非计划人工救援次数、重复原生副作用、未处置消息、空等模型回合、总耗时与可获得的 token 数据。
- [ ] token/宿主状态没有真实来源时记“不可获得”，不填 0；小样本展示中位数与范围，不宣传 P95 或任意百分比提升。
- [ ] 任务、源代码起点、权限、模型、并发量和验收标准固定；不同产品无法保持模型一致时单独标注混杂因素。
- [ ] 有可用 Antigravity 环境后，再执行同一案例清单。此前只报告本插件前后改进，不报告原生同题胜负。

**代码/测试位置：**新增 `G:/工具/codex-orchestrator/docs/verification/native-gap-next/` 保存报告与本地证据索引；复用 `G:/工具/codex-orchestrator/docs/verification/modes-20261117/` 的运行方式。原始私有日志不自动提交仓库。

**完成条件：**6 个可复现案例、明确机器可判定的失败/成功条件、每项指标的数据来源；未运行的产品比较明确保留空缺。

## M1：协调者待办投影＋原生转交领取账本

**目标：**解决“本地已登记，但漏转交/重复转交”的问题，不让 daemon 假装能调用原生工具。

**最小设计：**
- 扩展现有 `board_status`，返回有界 `next_actions` 与待核对项。事实源仍是步骤、inbox、人工消息和控制请求；不复制成另一套任务状态。
- 只新增一张原生转交账本，关联源消息/控制请求 ID、run、step（适用时）、packet、attempt、绑定 agent、动作类型和当前修订。
- 新增一个 `native_action_update` 工具，操作为 `claim / begin / record / reconcile`。这是拟议接口，不是当前可调用工具。
- `claim` 返回领取 token、租约期限和修订号；`begin` 再检查待执行控制、版本和绑定后标记“可能开始外部调用”。根协调者随后调用真实原生工具。
- 业务消息先持久化并绑定已准入步骤，再领取转交动作并唤醒；不先启动作者、再补收件消息。控制动作引用原 control_request_id，人工指导引用原 seq；同一来源、版本、目标和动作类型生成稳定键。
- `record` 保存实际工具结果的证据引用；执行结果与业务完成、独立验收分开。
- 租约过期、服务重启或调用结果丢失，进入 `needs_reconciliation`；旧 token 不能提交新结果。核对不能仅凭超时认定“没执行”。
- 账本保留 `claimed / invoking / confirmed / failed / needs_reconciliation / cancelled`。只有确知未调用或宿主明确拒绝执行才能归类为可重新领取；结果未知保持需核对。
- 人工消息原文和原 seq 保留；模糊收件人进入待澄清，不随意广播。

**原生调用边界：**本地 CAS/租约只避免重复领取和过期写回；不能撤销已经离开插件的调用。SQLite 与宿主没有共同事务，不能承诺副作用“恰好一次”。停止恰逢实际调用时，记录竞态并处理真实中断，不报告瞬时强制阻断。

**修改位置：**
- 新增 `G:/工具/codex-orchestrator/plugin/src/orchestrator/native_actions.py`：仅管理转交账本与待办投影，不执行宿主调用。
- 复用/修改 `G:/工具/codex-orchestrator/plugin/src/orchestrator/db.py`、`G:/工具/codex-orchestrator/plugin/src/orchestrator/board.py`、`G:/工具/codex-orchestrator/plugin/src/orchestrator/rpc_handlers.py` 的事务、查询和工具目录。
- 同步 `G:/工具/codex-orchestrator/plugin/skills/route-a-workflow.md` 和三个模式技能。
- 新增 `G:/工具/codex-orchestrator/tests/test_native_actions.py`；关联现有 `G:/工具/codex-orchestrator/tests/test_worker_control.py`、`G:/工具/codex-orchestrator/tests/test_mode_inbox.py`。

**失败先行验收：**
- [ ] 两个协调循环抢同一动作，仅一个成功；同键不同内容明确冲突。
- [ ] 查询后停止/换 attempt/换绑定，claim 或 begin 拒绝；不发旧任务。
- [ ] 调用前崩溃、调用后回执前断开、租约过期后迟到回执分别有真实保留证据。
- [ ] 重启后保留待核对动作；不自动重发、不重复中断、不把转交成功当业务 PASS。
- [ ] 用真实原生子代理完成一次消息→转交→接收→返工链，不能只用 fake host 证明宿主效果。

## M2：原 verifier 使用的验收命令采集器

**目标：**减少手工抄命令、退出码与日志造成的错误。不是给 daemon 增加任意命令执行能力。

**最小设计：**
- 随插件提供一个小型命令行入口，由原 verifier 通过宿主现有终端工具调用；继承当前审批和执行权限。
- 输入冻结的验证清单：精确 argv、cwd、超时、必要环境键、artifact/packet/attempt/contract、验收项对应关系。
- 直接执行 argv，不拼接 shell 字符串；保存真实进程退出码、起止时间、分离的原始 stdout/stderr、完整日志摘要。
- 环境中的秘密不写入日志；输出按文件落盘，返回有界摘要与路径，不重复制造大 stdio 响应。
- 执行前后及使用验收门禁时校验产物范围；测试生成物和日志落到范围外。检测到被验收内容改变必须重新捕获/验收。
- 本期保证是这些检查点的内容一致，不能检测所有“临时修改→测试读取→恢复原字节”。加入该反例，明确其不在强认证保证内；不把文件监听当作完整防篡改。若未来要求排除此类行为，需要另立冻结输入/权限隔离方案。
- 沿用 `verification_record` 与 evidence 文件链；为新合同增加结构化 receipt 校验。退出码 0 仍不代表每个验收项都完成，verifier 继续逐项判断。
- 开始即写入 running 记录；启动失败、超时、取消、采集失败保留不完整状态及部分输出，不补造 finished。
- Windows 上只终止本次采集器实际创建并拥有的进程树；进程树无法完整回收时明确失败，不扩大到同名进程。

**信任等级：**采集器证明“该次本地采集记录的进程结果”，不是防同权限恶意篡改的宿主证明。用户可写 JSON＋SHA 不构成签名认证；直接 HTTP 自报、MCP 关联信息、登记 agent_id 都不能自动升级为“已认证原生 verifier”。

**修改位置：**
- 新增 `G:/工具/codex-orchestrator/plugin/src/orchestrator/verify_runner.py` 与 `G:/工具/codex-orchestrator/tests/test_verify_runner.py`。
- 修改 `G:/工具/codex-orchestrator/plugin/pyproject.toml` 的命令入口，复用 `G:/工具/codex-orchestrator/plugin/src/orchestrator/artifacts.py`；接入 `G:/工具/codex-orchestrator/plugin/src/orchestrator/workflow.py` 和 `G:/工具/codex-orchestrator/plugin/agents/verifier.toml`。
- 扩展 `G:/工具/codex-orchestrator/tests/test_workflow_gates.py`、`G:/工具/codex-orchestrator/tests/test_package_metadata.py`。

**失败先行验收：**
- [ ] 实际非零退出、找不到程序、中文/空格参数、大输出、stdout/stderr 分流。
- [ ] 超时及产生子进程后取消；采集器中途退出不生成成功 receipt。
- [ ] receipt/输出与已登记摘要不符、命令与冻结清单不符或旧 attempt 被拒；检查点发现源码变化使结果失效。同权限重算所有摘要不是本期可认证防御。
- [ ] 包含暂改源码后恢复的反例，报告明确前后哈希的检测边界，不将它记成已防住的攻击。
- [ ] 真实 verifier 执行 FAIL→原作者修复→同 verifier 重测→集成验收。
- [ ] 从隔离安装包运行，不依赖开发目录、额外命令 RPC 或开发虚拟环境。


## R1：重新开展研究时的原始条件

状态：已作受检版本 NO-GO 结项；下列条件仅用于未来出现新公开接口证据后的复开，不是当前在做的任务。

仅当公开接口新增同宿主接入及对应控制能力时重开；届时适用以下原研究条件：

**研究边界：**最多 2 个工程工作日：一次当前版本公开接口/SDK 核对＋一组专用隔离原生夹具验证。时间估计仅用于限制探索，不是两天内必定实现。没有新接口证据，不重复此前相同失败路线。

必须分别回答并留证：

| 能力 | GO 必须证明 | NO-GO 的停止条件 |
| --- | --- | --- |
| 同宿主连接 | 连接已有桌面 owning host，明确会话归属 | 只能启动独立 App Server 或只能读取落盘历史 |
| 事件唤醒 | 根不在执行调度时，受支持接口能唤醒专用既有子代理 | 只能发通知/SSE，或仍需要根主动发起 |
| 原生中断/重连 | 同一子代理身份、真实停止结果、断线后可核对未决动作 | 只能新建会话冒充恢复，或 V2 输入被宿主拒绝 |
| 状态与身份 | 明确宿主提供什么不可由普通参数伪造的关联/凭据 | 只有 clientInfo、共享连接、名字或自报 agent_id |
| 长期等待成本 | 有真实宿主事件和用量记录支持成本结论 | 只有反复 25 秒工具等待或没有用量证据 |

- 四类控制能力可分别 GO/NO-GO，不把一个读接口成功扩大为全部可用。
- MCP context 只读取官方 SDK 明确支持且必要的字段，记录脱敏样本；请求 ID/进程 UUID 仅作审计关联，不作为原生身份认证。
- 不修改生产宿主、不注入私有进程、不猜未公开接口，不迁移用户聊天，不用后台 UI 点击冒充宿主桥。
- 若通过，先提交单独宿主桥设计，包含权限边界、订阅恢复、版本兼容和不确定动作处理；经确认再开发。
- 若失败，交付能力矩阵与停止原因，继续 M1–M5。未来只有公开能力发生变化才重开。
- 本阶段不研究/承诺执行中模型 turn 强制换工具目录；旧聊天下一轮加载继续采用已验收合同。
