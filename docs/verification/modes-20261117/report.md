# Route A 第二批最终交付与本机安装 — 2026-11-17

**状态：计划内两批全部完成。** 正式运行 **0.3.0 / schema5 / 127.0.0.1:18790 / PID25084**；新任务workflow2，历史workflow0/1保留各自合同，不补造验收记录。

产品代码锚点：2e33713f5714ce487cc2454efb08b784307e38cd。保留feature/route-a-mode-enhancements本地分支和当前检出；未合并主分支、推送、创建PR或公开Release。

## 交付范围

| 工作项 | 已完成范围 |
| --- | --- |
| Boost | 精确候选产物绑定挑战；固定原作者/验收者；Solo及委派修复；3轮上限；所有最终集成门禁完成前保留原工作区。 |
| Teamwork | 双里程碑依赖与实施准入复核；候选/集成验收；指定接手方真实确认；沿用工作项返工次数；根协调者保留原生调度职责。 |
| Swarm | task/rework/review/result绑定步骤、任务包、轮次及合同；角色/阶段/当前绑定校验；单次有界等待、显式唤醒/取消和不确定收件核对。回执不冒充步骤完成。 |
| 共享基础 | schema5兼容屏障；workflow1/2合入/完成/清理门禁；旧版行为保留；紧凑v2展示、能力、角色/技能/合同和版本元数据一致。 |
| 根因修复 | 迁移失败关闭实际连接池后重抛；升级子进程明确UTF-8；测试拥有并回收实际句柄。不伪造成功或过滤警告。 |
| 正式更新 | 官方CLI选中0.3；安装运行时、6受管角色、历史数据/config/旧缓存读回；新桌面原生宿主与次要SDK只读验收。 |

## 自动化与独立审查

- 基线：348 passed in65.37s，C:/workspace/.test-backups/mode-enhancements-20261117-012805/baseline.log。
- 最终源码严格回归：**422 passed in67.31s**，full-strict-final2.log。
- 安装版全套严格回归：**422 passed in67.51s，exit0**，full-strict-installed-final.log。先断言实际导入缓存0.3.0/src，再以-W error、仓库外basetemp执行；未向正式库写测试任务。
- 独立源码复审：whole-source-review.md末尾PASS覆盖保留的首轮FAIL；包括保留工作区后原作者修复/再次合入且保留其它成员修改的实际探针。
- 独立隔离审查：isolated-final-review.md；独立安装后审查：installed-final-review.md与installed-final-review-evidence.json，均PASS。审查者分别说明执行和查证范围，不冒充第二次全套重跑。
- Node、数据契约、入口HTTP/模式隔离及JS/CSS字节检查通过；本批无人工GUI操作。

安装回归的实际等价命令（原执行另在pytest前断言安装导入路径）：

```powershell
Set-Location 'C:/workspace/codex-orchestrator'
& .venv/Scripts/python.exe -m pytest -q -W error -o 'pythonpath=C:/Users/user/.codex/plugins/cache/tri-orchestrator/tri-orchestrator/0.3.0/src' --basetemp 'C:/Users/user/AppData/Local/Temp/tri-final-installed-20261117-040008'
```

预期422 passed，退出0；原日志保留实际导入路径与外部临时目录。

## 隔离包与真实原生场景

ZIP revision3共33载荷文件，源码/解包版/正式安装版逐文件匹配；wheel15运行时文件逐字节匹配。实际隔离后端从解包版Python3.12.14启动，不依赖开发目录；SDK/测试运行器Python3.14.4是另一层。

| 产物 | SHA-256 |
| --- | --- |
| dist/tri-orchestrator-0.3.0-plugin.zip | 5b218ba7f7e1483fcaff0208f6486aa499aa835eb101989b71cf414e3cc10725 |
| dist/tri_orchestrator-0.3.0-py3-none-any.whl | 65b177bdb34d980387fc0b505fbdef102f8241f19d741d57ef780343e933ab55 |

| 场景 | 实际结果 | 证据 |
| --- | --- | --- |
| Boost Solo | completed；真实初始FAIL、根作者修复、同验收者候选/集成PASS | boost-solo-final-readback.json |
| Boost委派 | completed；原作者实际修复、同验收者复验、实际合入后集成PASS | boost-delegated-final-readback.json |
| Teamwork双依赖里程碑 | completed；真实失败/修复；前置集成及指定接手方ACK后准入后继；最终重验/定向ACK | teamwork-final-readback.json |
| Swarm并行/跨代理消息 | completed；原生A/B回合实际重叠；两项真实失败/修复；另一原生代理消费绑定消息 | swarm-final-readback.json、native-parallel-observation.json |
| 返工耗尽 | 机制验收PASS，业务 **failed / needs_decision / attempt3**；0/1/2/3四次真实FAIL；第4轮和伪完成被拒，原工作区保留 | retry-cap-final-readback.json |
| 人工停止/恢复 | 机制验收PASS，业务 **archived / stopped**；真实interrupt，已接收收据重启后仍需核对，显式取消而不自动重放 | stop-native-interrupt-proof.json、stop-recovery-final-readback.json |

独立核对23条不可变验收记录、46份证据摘要、6个当前集成产物、8个当前冻结测试文件及36份历史测试摘要；另执行7项最终夹具检查。第一次跨代理单次等待25.03秒超时并返回控制；随后根显式唤醒同一已结束接收者。2,400,482字节大结果文件、完整Unicode/转义正文及摘要、两SDK会话同PID复用均实际核对。

原生代理调用终端SDK是“真实原生执行者→隔离stdio”证据，不是桌面宿主工具加载证明。早期初始失败来自revision1；资源清理修复后的revision2后端通过最终集成，revision3仅改Boost指令，不声称所有初始失败在最后ZIP下重跑。

19894隔离服务已明确停止并释放端口；保留数据、失败及cap/stop工作区。/root/mode_native_author_a最终仍interrupted，不恢复它。

## 正式安装故障与前向恢复

这是有中断且取证恢复的安装，不是单次无瑕原子切换：

1. 全模式无活动任务/待控制后完成一致性备份。在启动互斥与SQLite BEGIN IMMEDIATE写入预约下停止唯一验证旧daemon；官方CLI真实安装并选中0.3，恢复保留旧缓存运行时。
2. 初次控制器错误断言新目录不得有.venv而失败。CLI实际复制开发环境1750非字节码文件，元数据0.1.0且.pth指向开发目录，不能当合格独立运行时。controller-execute.log、deployment/fatal-error.json保留原失败。
3. 后续原生会话MCP初始化对齐运行时并启动shim/daemon，schema5于03:31:19迁移。host-bootstrap-origin.json保存事件/文件时间/DDL/日志链；精确历史父PID无审计，不补造归因。schema4专用恢复因此正确拒绝。
4. root以启动互斥与18790绑定但不监听维护租约阻止再启动；原作者第3轮显式--resume-after-host-bootstrap经同一独立审查PASS，只验证已知schema5/原数据/身份、更新6角色、读回并启动；未重复安装、备份、停止、uvsync或迁移，未用旧快照覆盖数据。
5. root释放自身租约后实际续接exit0，最终daemon PID25084；controller-host-resume-execute.log与deployment-host-resume/保留检查点。回归及独立审查后仍同PID。

最终审查控制器SHA：14e502cfb69fc2287dab906915abedbef6b3b9005d794c9030d6ad8efc467241。它是一次性运维证据，不属于33文件产品包。旧控制器、schema4恢复版本与较早HOST_ACCEPTANCE_PENDING原始收据均保留不改。

私有备份：C:/Users/user/.codex/backups/tri-orchestrator-030-20261117，含一致性schema4数据库、旧完整缓存、6角色和config；私有ACL保留，不将配置/凭据内容写入项目。

最终所有基线表旧列投影一致；8历史任务原workflow0/原状态保持，0活动/0待控制/0收件。仅新增schema5记录和3个空来源列，未创建生产测试任务/验收/工作区/消息。6角色匹配0.3包、config完整字节一致；0.1.0/0.1.2/0.2.0旧缓存非字节码清单及备份全部路径保留。未停止Codex或旧MCP客户端。

## 安装后新宿主验收

- **主证明：**新/root/mode_installed_native_probe实际发现27工具、可选artifact_id/recipient_agent_id字段，执行8次真实宿主调用，全部isError=false。all/Boost/Teamwork/Swarm数量8/4/0/4，已关闭任务注册成员wait(0)返回run_closed，存储状态未变。独立审查逐条比对实际会话原始输出，见installed-native-host-report.md/json。
- **次要证明：**installed-sdk-readonly.json由实际安装Python/stdio初始化0.3、27工具/两参数及2次只读调用，前后PID25084；不冒充桌面宿主证明。
- HTTP三入口与静态资源匹配。新原生上下文已实际调用，不需要为此重启桌面；旧主聊天热刷新不作承诺。

## 最终状态与边界

C:/workspace/plan.md、canonical execution ledger、staged plan和resume同步；Task1–5完成，无计划内待开发/待安装事项。completion-readback.json补充最终摘要而不篡改较早pending收据。

未进行本批人工GUI/完整无障碍、跨平台或任意负载穷举；HTTP/测试不冒充这些证明。未在正式库重复workflow2写场景，不承诺旧聊天热重连。失败/停止保持真实业务终态，不新增后台调度器，不公开发布，不自动扩展计划外功能。
