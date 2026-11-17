# 第二批 M3–M5：源码交付与分层验收

日期：2026-11-17。开发版本 **0.5.0 / schema7 / workflow4**。分支 `codex/native-gap-next`；复用包含第一批未提交交付的工作目录，修改前全量备份在 `docs/backups/20261117-batch2-start`。

## 当前交付（2026-11-17）

0.5.0 的开发、正式安装、桌面接口和补充验收已完成。当前公开摘要见 [0.5.0 发布记录](../github-050-20261117/report.md)。严格交接、异常控制、返工上限和三模式界面均已核验；原始回执、数据库、截图和备份保留在本地，不上传公共仓库。

实际任务的同题、同模型比较由用户后续进行。自动接管研究已结束；日常任务继续使用原生代理，插件不在主助手退出协调后独立接管旧代理。

## 历史检查点：第二批源码交付阶段（以下旧结论非当前状态）

以下记录保留当时未安装、直接handoff协议FAIL、尚未完成的GUI/异常/比较门槛和真实失败。上述缺口已由本轮独立新增实验分别补充；不追补原调用或把历史FAIL改写为PASS。当前剩余项以本页开头及closeout主报告为准。

## 当前结论

M3–M5 源码、共享接口、三模式技能、六角色及内联合同、只读页面、版本元数据已实现。最终严格全量回归 **722 passed / 1 POSIX-only skipped，97.79 秒**。这不是正式安装或第二批全部验收结论。

正式安装仍保持第一批 0.4.0/schema6；本轮不操作正式18790、不 commit/merge/push、不创建 Release。GUI 按方案留给用户操作；终端 SDK 和隔离原生调用不冒充已安装桌面工具目录。

## 实现与独立审查

| 范围 | 实现 | 独立检查 |
|---|---|---|
| M3 计划准入 | 事务原子 ownership 准入，目录/大小写/glob/别名保守冲突；显式串行依赖、真实旧作者静止边界；依赖历史准入与当前最终有效性分离；持久真实合入链和最终原 verifier 复验 | 原 reviewer 的 159 项相关/历史回归及合同同步后76项通过；所有已发现P1已修复复验 |
| M3 交接与审查 | 不可变紧凑包、当前事实摘要、指定接手方精确digest ACK、只读恢复/准备度；四维审查和映射发现，真实FAIL/原作者预算；原 verifier 可有证据驳回不成立发现 | 过期/错误接手/未完消息/控制/未知合入/三轮耗尽等反例通过 |
| M4 Boost | 有实质未知才调查、已知最小路径、真实route依据、只读挑战、不换原作者/verifier/attempt；调查报告由协调者交付并独立验证 | 三技能/六角色/全部inline同步，65项最终合同兼容通过；静态测试不证明模型总能正确路由 |
| Solo 本地边界 | 冻结 `execution_kind=coordinator_local` 仅适用于workflow4 Boost/no workspace；明确同步阶段证据，不假报运行中的root为idle。`local_revision_boundary`单次绑定版本与实际文件摘要，packet修订后须新边界再修step | 真实原root失败修复；独立范围/任务包修订反例及旧凭据/内容变化拒绝通过，不放宽native依赖边界 |
| M5 批量与停滞 | 同一M1账本的按收件人冻结批量claim/begin/record/reconcile；逐消息ID/ACK不丢失；复用watchdog分类、事务重查与去重，只提示协调者，不自动唤醒 | 独立98项及6类对抗缺陷修复复验；正常等待、失败待处理、过期观察、停止竞态分开 |
| 集成 | 29工具同一HTTP/MCP合同，5项新health能力；只读UI折叠交接/准备度/阻塞/审查维度，保留collector；0.5.0元数据/升级校验一致 | root集成独立193项通过、Node语法通过；GUI视觉未测 |
| 共享启动竞态 | 第一轮基线出现真实SDK短会话失败；根因为初始exclusive-bind健康探测不在启动锁内。将首次探测移入既有锁，不加重试或静默回退 | RED后14项生命周期/并发通过，最终全套与独立审查再次覆盖 |

明细：`batch2/m3-report.md`、`m3-review.md`、`contracts-report.md`、`contracts-review.md`、`m5-report.md`、`m5-review.md`、`integration-review.md`。旧报告中的OPEN/FAIL为带时间的历史检查点，当前以各最终addendum为准。

## 源码和包验证

- 最终命令：`.venv\Scripts\python.exe -m pytest -q -W error --strict-markers --strict-config --basetemp <新建仓库外临时目录>`；原始输出 `batch2/final-acceptance-suite.txt`。Node语法和 `git diff --check` exit0；Git仅有LF/CRLF通知。
- 初次基线599通过/1失败/1跳过，以及中间663/695/700阶段输出全部保留；它们不是最终722结论。新增源码和合同均留RED/GREEN，不删除实际失败。
- 最终wheel：`batch2/package-final/tri_orchestrator-0.5.0-py3-none-any.whl`；完整插件：`batch2/package-final/tri-orchestrator-0.5.0-candidate.zip`。38个插件源文件逐项归档读回；wheel的20个runtime文件逐字节匹配源码和真实原生验收运行包v3。摘要/大小/文件清单见 `batch2/package-final/receipt.json`。
- 独立最终wheel迁移：以备份0.4真实API创建schema6/workflow3任务、消息和真实collector PASS，迁移schema7后15张旧表旧列旧行全部保留；原workflow3完成门禁仍能执行，新任务默认4。旧代码拒绝future schema且DB行及文件SHA不变。workflow0–2仍为自动化fixture兼容层，不冒充旧版本真实API历史。
- 原生隔离包在中文/空格路径启动；最终v3真实SDK目录29工具，参数包含execution_kind/local_revision_boundary/groups；board_status成功，短SDK会话结束daemon PID不变。该层不是正式桌面目录重载。

## 本次真实原生收口

最终只读快照 `batch2/native/config.json` 指向的外部 `evidence/final-summary.json` / `final-boards.json`：**5个run completed、7个step done；6条FAIL、15条PASS；32条inbox全部completed、32条native action全部confirmed；0待转交/待核对，SQLite integrity_check=ok。**

| 实际任务 | 最终结果 |
|---|---|
| Boost Solo | 原root、本地同步边界；初始真实FAIL→attempt1修复→原独立verifier候选/集成PASS |
| Boost定向委派 | 原A comma parser；初始真实FAIL→原A attempt1→同原verifier PASS |
| Teamwork | 原A parse/原B sum同pipeline.py显式串行；A跨turn compact handoff由B精确ACK；B原始total=-1真实FAIL后原B修复并真实merge；B全量集成2passed后原A在最终main重新验收1passed，fresh handoff/ACK后完成 |
| Swarm | 原A label/原B parity独立文件；各自真实FAIL后attempt1修复。三轮各将2个真实评审消息一次分组唤醒同verifier，分别ACK/验收；不是M0新旧对照 |
| 纯调查 | 实际只读investigator查初始源码/调用/失败；root仅交付批准report，独立verifier核对快照及证据；attempt0，无伪造源码merge |

本次native同文件流程未制造“已知明显缺陷但候选PASS”。**同文件集成FAIL→历史准入允许原B返工→原A/B最终复验**由独立审查的自动化真实文件反例覆盖；native采用候选FAIL和最终真实复验。两层不能混写为同一次实验。

## 原生运行证据边界

原生作者A/B、原verifier和只读investigator使用本聊天的实际 `collaboration`；业务数据只进入中文/空格临时根下12237隔离wheel服务。根执行真实工具调用，作者/verifier经公开HTTP RPC操作同一账本，并以独立collector运行冻结业务pytest。测试文件哈希始终固定。

这不是宿主签名身份认证、实际上下文压缩、跨宿主接管或后台自主调度。接手B此前没有Teamwork实现任务，只接收compact pack及指定引用后跨turn继续；B仍是已存在原生代理，不能称为全新空白宿主上下文。

实现/评审业务消息均保留独立ID和实际派发回执。交接包读取/ACK采用直接原生followup，另留协调者事后逐字义记录和实际ACK；它没有追补成M1账本动作，不能宣称每一条协调者指令均经过领取账本。普通spawn/身份bootstrap也不算业务处理成功。

### 真实失败、修正与额外协调

1. 根运行中的Solo不能诚实提供native idle；新冻结本地执行种类和显式修订边界经原作者修复、独立审查后应用。保留原库/身份，隔离包显式前向更新两次，PID7992→8788→19000。不是零重启或零救援。
2. PowerShell JSON标准输出解码损坏中文路径，首个collector准备报FileNotFound；未执行业务命令、未记录verdict。改为Python进程内值传递，原artifact保留。
3. Teamwork集成采集错误地把whole-root候选范围缩成显式文件列表，真实HTTP409拒绝；没有artifact/collector/verdict。只修验收helper保持whole-root，新增纠正消息经账本真实派发，原verifier完成原accepted消息及纠正消息。后台门禁未放宽。
4. 独立迁移探针首次重复完成已完成run，被原有门禁拒绝。保留失败root；新独立库正确保留旧run active再验迁移，产品未改。

不把受控初始业务缺陷当产品bug，也不把这些额外协调成本隐藏为零。未执行完整M0新旧36次比较，不能据测试数或一次分组演示宣称整体性能、token或人工救援改善。

## 留存与最终复核

原M3 reviewer独立复核21条descriptor/manifest/证据/receipt/stdout/stderr摘要，7个最新集成receipt均按当前产物`require_success=True`通过；旧A产物真实报checkpoint mismatch，新A最终产物通过。32条派发证据文件SHA匹配，原绑定/attempt和两次前向更新的旧记录保留。

**其严格全协议判定仍为FAIL：直接handoff指令未走M1账本。** 这是本次执行协议覆盖缺口，不是源码缺陷；当前原生业务和源码门禁通过，不冒充全协议通过。后续需单独验证账本覆盖的交接指令路径，不能追补历史派发或把直接调用改记成账本调用。

最终仅核验并停止本轮自有PID19000/端口12237；正式18790未操作。数据、原始证据和修正前helper均保留。仓库内`batch2/native-evidence.zip`（逐成员读回核验）、`native-evidence-receipt.json`及只读备份`native-final-state.db`提供长期留存；独立迁移的成功及失败根另存`migration-evidence.zip`和receipt。原临时根未删除。README当前工具清单残留19/27的文档问题已补齐到真实29并经独立复查，历史27工具段保留。

## 历史未覆盖门槛（源码交付时点，非当前待办）

- 用户操作的新UI三入口、浅/深主题、窄屏、长内容、键盘/焦点与live更新视觉验收。
- 另行批准后的正式安装、生产数据保留、已安装桌面新29工具及参数写入闭环、原聊天下一轮真实加载。独立wheel迁移不是正式升级。
- 交接指令经M1账本的严格全协议覆盖；本次直接handoff不足以覆盖此项。
- 第二批所有异常的真实宿主重演（例如不确定派发/控制竞态/结束代理/三轮耗尽）；源码负面测试已覆盖，但不得冒充均做过新原生实验。
- M0完整36次新旧对照、空等/唤醒及真实token与成本统计；Antigravity对照；跨日和非Windows运行。

以上是源码交付时点的历史未完成队列，不是当前待办；新增收尾证据见页首和closeout主报告。仍不标记整份无限后续路线全完成。
