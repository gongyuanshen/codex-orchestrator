# Route A execution ledger

Approved specification: G:/工具/计划文档.md (retained snapshot: docs/verification/route-a-20261116/approved-plan.md).

## Ownership
- workflow worker: Database migration/hooks, workflow and artifacts, focused tests.
- inbox worker: formal inbox store/waits, focused tests.
- transport worker: daemon/RPC/shim and focused tests.
- merge worker: workspace manager gates/recovery and focused tests.
- UI worker: existing dashboard assets and focused tests.
- upgrade worker: controlled package helper and focused tests.
- root: BoardService summary, skills/roles/contracts, old compatibility fixtures, versionmetadata, integration/native acceptance and finalreview.

## Baseline
119 original tests passed in51.36s. Source/tests snapshotted before edits. Installed29files andproductiontableshashedreadonly. Active Boost run_aa711e5bb5ed exists; no productiondeployment while anymodeactive. Noproductionlistenerwasobservedatstart; do notinferendedrunfromabsentlistener.

## Decisions
- Stage delivery firstcommonfoundation, thenmodeenhancements. Newruns strict workflow1, migratedruns0 preservelegacy.
- Max3automaticrework rounds following firstfailedcandidate; no implicitbudgetreset.
- Explicit inbox cancelled disposition needed for pendingwork stopped by user; do not fabricate acceptance or execute pausedagent.
- Shared manager gates and fixed stagedsnapshotmerge; no filesystem/SQLiteatomicityclaim.

## Checkpoints
- [x] Read approvedspec, baseline/regression, backup and productionstateinventory.
- [x] Commonfoundation TDD andintegration.
- [x] Modecontracts andcompact UIintegration.
- [x] Fresh packaged source/nativechainacceptance andindependentreview (final evidence report linked below).
- [x] Productioncutover (2026-11-17 user-stopped taskcheckpoint preserved; allmodesinactive; selected0.2.0/schema4; freshdesktopchild native27tools).
- [x] Secondbatchcomplete (workflow2/schema5, four native paths, retry/stop recovery, installed0.3.0 and fresh actual desktop-native acceptance).

Final source regression:348passed69.18s. Firstbatchproductioncutover deferred by activeBoost; secondbatch remains unchecked. Verification: docs/verification/route-a-20261116/report.md.

2026-11-17: installed0.2.0 afterexplicituserstop; see docs/verification/install-020-20261117/report.md. Historicaldeferredstatementsabove describeearliercheckpoint. Secondbatchstillpending.

2026-11-17 post-install follow-up: repaired both existing stale codex.cmd entries via the registered desktop package locator; persistent-PATH CMD/PowerShell and independent checks passed. Fresh native child again loaded27tools; user reported allthreeentrypages normal. Full regression348passed with basetemp outside the repository; retained first-run7failures caused by in-repo basetemp and quarantined only its own10clones/restored appendedexclude. No product source/package/config change or daemon restart. See docs/verification/postinstall-small-fixes-20261117/report.md. Secondbatchremainsunchecked.


2026-11-17 second-batch development IN PROGRESS: feature/route-a-mode-enhancements; baseline348passed. Task1 workflow2/schema5, artifact-bound challenge, original bindings, failure-budget preservation, dependency admission and targeted handoff implemented; independent review pending. First combined suite380passed1warning (UTF-8 child stderr root cause separately fixed). Task3 instruction/surface preparation independently PASS after admission/cleanup corrections; focused contracts18 and surfaces26. Upgrade error-log/fixture cleanup strict23passed. Swarm linkage, final combined regression, packaged real-native acceptance and controlled cutover remain unchecked; production untouched. Staged plan: docs/superpowers/plans/2026-11-17-route-a-modes.md.

2026-11-17 continuing checkpoint: second-batch SOURCE COMPLETE, final source spec+qualityPASS; strict422passed67.31s, commit2e33713. Native Solo/delegated/Swarm closed after actual failure/rework/candidate+integrated gates; Teamwork final integration/renewed handoffs pending. Archive rev3 matches33sourcefiles; actual large file2400482bytes and singleton reuse verified. Retrycap/stop-recovery, final isolated evidence review and production0.3cutover remain unchecked;18790/productiontasks unchanged. Full staged ledger:2026-11-17-route-a-modes.md.

2026-11-17 Task4 isolated acceptance COMPLETE: independent source/package/native evidencePASS;33payloadfiles/15wheelruntimefiles/23records46digests/6currentintegratedartifacts/7freshfixturetests. InitialTask5activation truly backedupandstoppedidleprod, officialCLIselected0.3 andrestoredoldcaches, thenFAILEDinvalidnewvenv-absenceassumption. CLIcopiedSOURCEdevruntime1750files(dist0.1.0/sourcepth). CurrentproductionOFFLINE/schema4,8oldrunsunchanged; migration/roles/newstartnotdone. Originalauthorcontrollerfix2+independentresume reviewongoing; no blindreinstall/rollback/falsewholecompletion. See stagedplan and docs/verification/modes-20261117/resume.md.

2026-11-17 laterCURRENTstate: observed native-sessionMCPrefresh bootednewshim/daemon03:31, aligningruntime0.3 andschema5 outsidefailedcontroller. Eightoldruns/allbusinessrowsunchanged; expectednullablecolumns+schema5rowonly. Originreceipt host-bootstrap-origin.json pinsactualsource/log/eventchronology (exactPIDparentunavailable). Rootmaintenancelease19444/session13895 holdsstartupmutex+BOUND-not-LISTEN18790; serviceoffline. Originalcontrollerfix3explicitverifiedhost-bootstrapcontinuationunderreview; no repeatinstall/backup/stop/sync/migration orrollback. Task5stillpendingroles/readback/start/freshhost.

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
