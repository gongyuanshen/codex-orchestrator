# Route A Mode Enhancements Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans task-by-task.

**Goal:** Finish the second delivery batch in G:/工具/计划文档.md without altering production tasks.
**Architecture:** Extend the existing Python/SQLite workflow, inbox and native-coordinator contracts. Preserve workflow0 legacy and workflow1 foundation runs; new runs use workflow2. Schema5 prevents older schema4 programs from silently bypassing workflow2 gates. No second scheduler, queue or session owner.
**Tech Stack:** Existing Python, SQLite, HTTP/SSE, MCP, native collaboration agents and Node dashboard checks; no new dependency.
**Spec:** G:/工具/计划文档.md, sections III–V. User delegates engineering choices and asks continuous execution with Superpowers.

## Global Constraints
- 保留 **18790** 和 `/boost`、`/teamwork`、`/swarm` 三个入口。
- 首次交付失败后，每个工作项最多自动返工 **3 轮**；仍失败则进入“待用户决策”，不标记完成。
- 不新增消息队列、第二个调度服务或路线 B 的自管会话。
- 开发和测试不修改生产任务；需要界面操作时由用户完成。
- 原有暂停、终止要求优先，返工和恢复不得自动重新启动被用户停止的代理。
- This plan does not authorize public GitHub releases, changing unrelated files, or fabricated native/GUI acceptance.
- Keep existing unrelated README/ledger changes and verification directories. Run pytest basetemp outside the repository.

## Review Focus
- Old programs must reject schema5 before any DDL; workflow0/1 rows remain unchanged, and new workflow2 cannot evade shared completion/merge/cleanup gates.
- Changing packet, binding, candidate or retry bookkeeping must not reset failure budgets or reuse a challenge from another delivery.
- A handoff must name its intended native recipient; sending alone and confirmation by another arbitrary agent must not unlock dependencies.
- Swarm execution messages must link to the correct work step/attempt and role; receipts never fabricate step progression or independent acceptance.
- User stop, real transport failure and uncertain merge must remain explicit blockers, not automatic replay/revival/success.

## Ownership and Interfaces
- Task1 worker: db.py, workflow.py, workspace_mgr.py, rpc_handlers.py and focused workflow/migration/RPC tests.
- Task2 worker: inbox.py and inbox/transport fixtures/tests; consume workflow2 and Task1 APIs without altering Task1 ownership unless agreed.
- Root: instructions/roles, package metadata, compact dashboard adaptation only if needed, packaging/native acceptance, documentation and controlled upgrade.
- Read-only reviewers: independent spec/quality checks and commands; never edit or touch production tasks.
- All agents share the checkout and must preserve other agents' work. No agent may commit unrelated edits.

### Task 1: Versioned mode contracts and workflow gates
**Files:** plugin/src/orchestrator/{db,workflow,workspace_mgr,rpc_handlers}.py; tests/test_mode_workflows.py and relevant existing migration/gate/RPC tests.
**Interfaces:** Existing workflow_step_save, workflow_step_advance, artifact_capture and verification_record. Add optional artifact_id and recipient_agent_id parameters to step_advance; require them only for workflow2 challenge/handoff respectively.
- [x] Write and run RED tests: new runs workflow2/schema5; stored workflow0/1 unmodified; all strict shared gates cover both1/2; ordinary RPC cannot set workflow version.
- [x] RED tests: Boost challenge must bind exact current candidate artifact/attempt/contract, only bound verifier; new delivery requires a new challenge; original author/verifier cannot be swapped to bypass rework; task packet edits cannot revive rework/needs_decision.
- [x] RED tests: Teamwork two dependent milestones; predecessor must have current integrated PASS and explicit targeted handoff receipt; wrong recipient and send-only are blocked; milestone uses its own existing attempt counter, not a nested retry loop.
- [x] Implement the smallest changes in existing services and schemas. Preserve workflow1 behavior except fixing shared gate version recognition. Existing primitives are reused, not reimplemented.
- [x] GREEN focused tests, then full `.venv/Scripts/python.exe -m pytest -q --basetemp <unique external temp directory>`; retain actual RED/GREEN logs under docs/verification/modes-20261117/.
- [x] Independent spec and quality review; repair findings on original worker, at most3 automatic repair rounds.

### Task 2: Swarm step-linked inbox execution
**Files:** plugin/src/orchestrator/inbox.py; tests/test_mode_inbox.py and inbox/transport fixtures if new-run assumptions need explicit workflow1 setup.
**Interfaces:** Existing inbox_send/get/ack/wait. Use step_id plus stored receipt context; do not add a second state machine or make completed receipt imply workflow completion.
- [x] RED tests: workflow2 Swarm task/rework/review/result require a same-run step and the proper bound owner/verifier recipient for that kind; notice stays unlinked and nonblocking while pending.
- [x] RED tests: initial dispatch and rework cannot be delivered for incompatible step stages; stale accepted work remains reconciliation-required; old message must not execute after changed attempt/contract/binding; cancellation with evidence remains possible.
- [x] Persist the minimum delivery context in existing message storage (schema5 migration columns only if needed); preserve idempotency and workflow0/1 behavior. Terminal receipts describe delivery, never auto-advance steps.
- [x] GREEN focused and whole suite; independently review spec and quality before proceeding.

### Task 3: Three mode instructions and compact presentation
**Files:** plugin/skills/{route-a-workflow.md,boost/SKILL.md,teamwork/SKILL.md,swarm/SKILL.md}; plugin/agents/*.toml; contract tests; dashboard only if new fields require presentation.
- [x] RED contract checks for exact Boost execute/challenge/original-author repair/same-verifier final review sequence, candidate artifact binding and retry cap.
- [x] Add explicit Teamwork multi-milestone dependency admission and intended-recipient checkpoint handoff; root retains all native child lifecycle control.
- [x] Add explicit Swarm task/attempt links, single bounded inbox wait per turn, native observation before wakeup and heartbeat-not-death distinction; keep long logs folded.
- [x] GREEN contracts, dashboard/Node checks and whole suite; independent review.

### Task 4: Package and real native acceptance
**Files:** package metadata/locks, isolated fixture/harness and docs/verification/modes-20261117/report.md.
- [x] Set coherent package version0.3.0/schema5, capabilities and build package from source; test fresh package outside checkout with Chinese/space paths, singleton and large result delivery.
- [x] Start only isolated daemon/data (not18790); run real MCP from packaged code, actual native children and actual commands. Record genuine failing test, original-author repair, same-verifier PASS and integrated checks for Boost Solo/delegation, Teamwork2 dependent milestones+targeted handoff, Swarm parallel members+cross-agent inbox.
- [x] Exercise retry exhaustion, manual stop semantics and service restart/reconciliation without inventing success or reviving stopped agents.
- [x] Fresh whole-change review; resolve confirmed findings and rerun regression. Software checks, native sampling and user GUI observations remain distinct evidence.

### Task 5: Controlled installation and synchronization
- [x] Check all modes for active production tasks, read-only. If any active, leave installation pending, never force-close it; finish all independent source/acceptance work first.
- [x] If inactive and existing controlled upgrade requirements are satisfied, back up package+consistent database, stop only the owned daemon, install/migrate/read back and reopen admission; preserve production data and global config.
- [x] Refresh-dependent host acceptance is user-operated; retain pending item if the current host cannot prove fresh tools/skills. Do not claim old-chat hot refresh.
- [x] Atomically synchronize G:/工具/计划文档.md, canonical execution ledger, this staged plan and verification report with completed scope/evidence/next action/unverified items; no public Release.

## Progress Ledger — chronological historical checkpoints
- Baseline:348 tests passed in65.37s; full output G:/工具/.test-backups/mode-enhancements-20261117-012805/baseline.log. Existing uncommitted changes backed up beside it.
- Ruling: use a feature branch in the existing checkout, not a second worktree, because this task continues the selected local repository; all execution fixtures/data/basetemp remain external and production is untouched. Cost if wrong: source collaboration needs strict ownership rather than separate Git worktrees.
- Ruling: workflow2/schema5 is a deliberate compatibility boundary, not a fallback. Existing0/1 retain their recorded contracts; new runs get2. Cost: fixtures testing foundation behavior must explicitly select stored workflow1.
- Pre-flight: Task2 consumes Task1 workflow2 and schema5; migration changes must have one owner. Task3 consumes exact challenge/handoff parameters. Task4 packages the final combined code, never the source editable installation. Task5 depends on all prior acceptance and idle production.

- Task1 implemented; first combined suite380passed1warning; independent review in progress (task1-report.md/task1-scoped.diff). Upgrade child encoding warning reproduced and fixed in root-owned script, with retained RED evidence.
- Task3 preparatory spec+quality review PASS after2repair rounds: prebind verifier/admit before implementation; per-milestone integrate+targeted ACK before dependency; retain original workspaces until final gates. Contracts18passed; transport/UI subset26passed. This is not packaged/native acceptance.
- Root upgrade checks23passed with -W error after explicit UTF-8 child streams and closing existing test-fixture SQLite connections; no warning suppression or errors=replace. Version metadata candidate0.3.0/locks coherent; metadata2passed.

- Task1 COMPLETE after original-author fix round1: independent spec+quality PASS;75focused tests plus4differential v1/v2 entry probes passed. Shared dependency dispatch remains compatible withv1; new implementation-entry rechecks apply onlyv2. See task1-review.md. Task2 is running; no full/native/installed completion is claimed.

- Source Task1/2/3 COMPLETE and final whole-source spec/quality PASS (whole-source-review.md). Final review found Boost early cleanup, fixed as Task3 repair3/3 using actual RED→GREEN19 and independent retained-workspace repair/remerge probe. Full strict regression422passed67.31s (full-strict-final2.log), no warning filters. Commits b3ef06c,43b8b28,1f9d716,2e33713; no push/release.
- Current archive revision3 matches33 source files; wheel source bytes/metadata audited; actual extracted runtime usesPython3.12 and SDKclientPython3.14. Native Solo/delegated/Swarm business runs now completed after genuine initial failures, original-author repairs and same-verifier candidate+integrated gates. Teamwork final sum/parse integrated checks pending; its initial targeted handoff and blocked early/wrong-recipient admission were actually exercised.
- Real Swarm authorA/B native turns overlapped (native-parallel-observation.json); first single receiver wait timed out25.03s and returned control, root explicitly woke same ended nativeB after A sent result; exact durable message accepted/completed without changing step. The20s subscription-observer timing probe timed out before receiver entered; retained as harness evidence, not hidden/product failure.
- Actual archive stdio large-result transport returned2400482byte file; complete Unicode/escaped body bytes/hash verified, second SDKsession reusedPID26852 (packaged-transport-result.json).
- Native campaign revision1 failure/candidate proofs preceded constructor resource cleanup. Backend revision2 restart fromPID10096 to26852 preserved all persisted steps/messages, final integrated gates use revised backend; archive revision3 changes onlyBoost instruction text. Original inventories retained as rev1/rev2; current33file inventory/wheel audit match finalsource. Production18790 still0.2.0/schema4, untouched exceptread-only checks.
- Remaining: Teamwork final targeted receipts, retry exhaustion rounds1..3, native stop+accepted-message recovery, isolated independent evidence review/gate file, then controlled0.3 activation/freshhost check. New controller worker owns onlyactivate_030.py; --preflight isreadonly, --execute notrun.

- Task4 COMPLETE: isolated-final-review.md independent spec/qualityPASS;33payloadfiles/15wheel files/23records46digests/6latestintegratedartifacts/7freshfixturetests; allfour mainnativepathscompleted, expectedcapfailed andstoparchived retained. native-final-readback.json nowvalid; testdaemon16700 explicitlystoppedandportreleased.
- Task5 initialexecution reachedofficialCLI0.3installation thenfailed installed-runtime absenceguard; actualCLI copied oldSOURCEdevvenv(dist0.1.0/sourcepth). Privatebackups complete, oldcache runtimesrestored, original8rows/schema4 preserved,18790temporarilyoffline. No migration/roles/start yet; source33filesinstalled0.3selected. Sameoriginalcontrollerauthorfix2 andindependentreview/explicitresume areinprogress; no blindreinstall/rollback/fakePASS. Fullcontinuationmap docs/verification/modes-20261117/resume.md.

- CURRENTreconciledstateadvancedbyhostMCPbootstrap: runtimecorrect0.3/cachepth, schema5,8oldrowsunchanged/0controls; serviceoffline. Exact03:31event/log/DDLreceipt host-bootstrap-origin.json, no falseclaimprecisePIDparent. Fix2schema4-onlyresume correctlyfailsclosed. Rootmaintenancelease19444/session13895 blocksnewautoboot; originalauthorfix3(last)explicit --resume-after-host-bootstrap skipsalreadydoneinstall/sync/migration, onlyvalidates/roles/readback/start. Independentboundedreviewrequired; noexecuteuntilPASSandrootleasehandoff.

## 2026-11-17 第二批最终完成与安装后验收

第二批Task1–5全部完成，计划内无待开发或待安装项。正式0.3.0/schema5，18790实际PID25084；新任务workflow2，历史workflow0/1保留合同。Boost挑战/原作者修复、Teamwork双里程碑/指定接手方确认、Swarm步骤轮次绑定和共享门禁均已通过独立审查。

最终源码422项严格回归通过；安装目录源码再次以-W error通过422项（67.51秒，外部basetemp，实际安装导入路径断言）。隔离独立审查核对33包文件/15wheel文件、23验收记录/46证据、6当前集成产物并执行7项夹具检查。四条真实原生业务路径闭环；返工耗尽保留failed/needs_decision(3)，停止恢复保留archived/stopped，不伪造完成或恢复已停代理。19894隔离服务结束，失败证据和应保留工作区未删。

受控更新经历初次.venv误判失败及宿主MCP自动bootstrap；取证并经原作者修复/独立复核后显式执行剩余尾段，未重装/重迁移/覆盖旧快照。6角色、config、旧缓存及所有基线表旧列投影读回一致；8历史任务原值保持，无生产测试写入。新实际桌面原生宿主27工具/8次只读调用、次要SDK27工具/2次调用通过；安装后独立审查PASS，回归后仍同PID。

完整交付与恢复：G:/工具/codex-orchestrator/docs/verification/modes-20261117/report.md；独立证据同目录installed-final-review.md和installed-native-host-report.md/json。私有备份C:/Users/Administrator/.codex/backups/tri-orchestrator-030-20261117。

下一步：本计划执行结束，不扩展新功能。保留本地feature/route-a-mode-enhancements分支，未推送/主分支合并/公开Release。未进行本批人工GUI/完整无障碍、跨平台验收，不承诺旧聊天自动热刷新。上方进行中/离线/待验收均为历史检查点，以本最终状态为准。

## 2026-11-17 GUI 与旧聊天刷新后续验收

按用户追加要求完成真实三模式GUI及原旧聊天本轮刷新检查。发现并根因修复Diff响应乱序/跨工作区loading、离开协作图后台回放、图节点键盘及播放状态ARIA问题；18个新增行为case，独立关联67项PASS，全套-W error 440 passed in69.39s。源码修复871cfe181bfb60d8c5a6f54696dbff05f664b014已推送GitHub开发分支feature/route-a-mode-enhancements并远端读回核对，不合main/不创建公开Release；文档同分支整理提交。

真实IAB检查三个模式桌面1440×900及窄屏390×844、v2完成/失败/双里程碑、日志筛选全文、成员/检查器、主题、JSON下载和Tab/Enter/Space交互。正式只原子更新app.js/styles.css，无服务重启；18790仍PID25084，14表全哈希/8历史任务保持。原根聊天同ID新turn实际27工具/2原生调用通过，未换新聊天或用SDK冒充；不承诺任意进行中旧turn即时刷新。

完整证据：G:/工具/codex-orchestrator/docs/verification/gui-hot-refresh-20261117/report.md。旧FAIL/安装事故和旧包清单保留，本轮两静态文件新哈希另记。19894自有GUI服务已结束，停止代理不恢复。未穷举实体手机触控、所有无障碍及副作用GUI操作；此范围不是后端/宿主能力承诺。以上补充覆盖此前未做本批GUI/未验证旧聊天的历史边界，不改写当时事实。

## 2026-11-17 主分支集成与无障碍最终收尾

按用户对上一轮未合main/未完整检查无障碍的追加要求，完成三模式工程无障碍清单和主分支快进集成。源码1e4fc5766b9ef9f329364121927b6f39f1bc9c62；开发分支480项严格回归通过，合入main后全套480 passed in73.03s。最终独立关联107项PASS。普通非force推送并读回远端main；保留开发分支历史，不创建PR/标签/GitHub Release。

根因修复页签/频道语义、菜单/模态键盘与焦点、真实SSE图/概况/成员重绘焦点、关闭details语义、状态播报、reduced-motion和文字对比度。真实浏览器最终48场景零axe violations；24场景3428文本的计算样式对比度无失败。11项实际键盘/下载/SSE记录及正式历史任务模态复验通过，保留全部RED和incomplete人工核对，未将工程清单误报正式WCAG认证。

正式仅更新3静态文件及最后CSS色值，18790保持PID/starttime、0.3.0/schema5；14表/8历史任务不变，33安装载荷与候选字节及main规范化内容核对一致。19894复制数据内UI专用run已归档、服务已结束，原生代理不唤醒。

旧聊天同ID下一轮27工具/2真实原生只读调用的既有验收有效；本轮正式能力只读构造tools.listChanged=false。固定目录/下一轮加载是当前已验收合同，不是原计划未实现功能；不承诺任意执行中turn强制替换，未改宿主实验配置。实体手机触控、各屏幕阅读器组合不纳入本次已执行范围。

本计划与本轮追加项均已收尾，未新增开发队列。完整说明：codex-orchestrator/docs/verification/main-closeout-20261117/report.md（仓库内相对路径docs/verification/main-closeout-20261117/report.md）。上方所有进行中/未合main/未做GUI条目保留为历史，不改写当时事实。
