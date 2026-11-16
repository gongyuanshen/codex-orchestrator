# Route A Mode Enhancements Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans task-by-task.

**Goal:** Finish the second delivery batch in C:/workspace/plan.md without altering production tasks.
**Architecture:** Extend the existing Python/SQLite workflow, inbox and native-coordinator contracts. Preserve workflow0 legacy and workflow1 foundation runs; new runs use workflow2. Schema5 prevents older schema4 programs from silently bypassing workflow2 gates. No second scheduler, queue or session owner.
**Tech Stack:** Existing Python, SQLite, HTTP/SSE, MCP, native collaboration agents and Node dashboard checks; no new dependency.
**Spec:** C:/workspace/plan.md, sections III–V. User delegates engineering choices and asks continuous execution with Superpowers.

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
- [ ] RED tests: workflow2 Swarm task/rework/review/result require a same-run step and the proper bound owner/verifier recipient for that kind; notice stays unlinked and nonblocking while pending.
- [ ] RED tests: initial dispatch and rework cannot be delivered for incompatible step stages; stale accepted work remains reconciliation-required; old message must not execute after changed attempt/contract/binding; cancellation with evidence remains possible.
- [ ] Persist the minimum delivery context in existing message storage (schema5 migration columns only if needed); preserve idempotency and workflow0/1 behavior. Terminal receipts describe delivery, never auto-advance steps.
- [ ] GREEN focused and whole suite; independently review spec and quality before proceeding.

### Task 3: Three mode instructions and compact presentation
**Files:** plugin/skills/{route-a-workflow.md,boost/SKILL.md,teamwork/SKILL.md,swarm/SKILL.md}; plugin/agents/*.toml; contract tests; dashboard only if new fields require presentation.
- [ ] RED contract checks for exact Boost execute/challenge/original-author repair/same-verifier final review sequence, candidate artifact binding and retry cap.
- [ ] Add explicit Teamwork multi-milestone dependency admission and intended-recipient checkpoint handoff; root retains all native child lifecycle control.
- [ ] Add explicit Swarm task/attempt links, single bounded inbox wait per turn, native observation before wakeup and heartbeat-not-death distinction; keep long logs folded.
- [ ] GREEN contracts, dashboard/Node checks and whole suite; independent review.

### Task 4: Package and real native acceptance
**Files:** package metadata/locks, isolated fixture/harness and docs/verification/modes-20261117/report.md.
- [ ] Set coherent package version0.3.0/schema5, capabilities and build package from source; test fresh package outside checkout with Chinese/space paths, singleton and large result delivery.
- [ ] Start only isolated daemon/data (not18790); run real MCP from packaged code, actual native children and actual commands. Record genuine failing test, original-author repair, same-verifier PASS and integrated checks for Boost Solo/delegation, Teamwork2 dependent milestones+targeted handoff, Swarm parallel members+cross-agent inbox.
- [ ] Exercise retry exhaustion, manual stop semantics and service restart/reconciliation without inventing success or reviving stopped agents.
- [ ] Fresh whole-change review; resolve confirmed findings and rerun regression. Software checks, native sampling and user GUI observations remain distinct evidence.

### Task 5: Controlled installation and synchronization
- [ ] Check all modes for active production tasks, read-only. If any active, leave installation pending, never force-close it; finish all independent source/acceptance work first.
- [ ] If inactive and existing controlled upgrade requirements are satisfied, back up package+consistent database, stop only the owned daemon, install/migrate/read back and reopen admission; preserve production data and global config.
- [ ] Refresh-dependent host acceptance is user-operated; retain pending item if the current host cannot prove fresh tools/skills. Do not claim old-chat hot refresh.
- [ ] Atomically synchronize C:/workspace/plan.md, canonical execution ledger, this staged plan and verification report with completed scope/evidence/next action/unverified items; no public Release.

## Progress Ledger
- Baseline:348 tests passed in65.37s; full output C:/workspace/.test-backups/mode-enhancements-20261117-012805/baseline.log. Existing uncommitted changes backed up beside it.
- Ruling: use a feature branch in the existing checkout, not a second worktree, because this task continues the selected local repository; all execution fixtures/data/basetemp remain external and production is untouched. Cost if wrong: source collaboration needs strict ownership rather than separate Git worktrees.
- Ruling: workflow2/schema5 is a deliberate compatibility boundary, not a fallback. Existing0/1 retain their recorded contracts; new runs get2. Cost: fixtures testing foundation behavior must explicitly select stored workflow1.
- Pre-flight: Task2 consumes Task1 workflow2 and schema5; migration changes must have one owner. Task3 consumes exact challenge/handoff parameters. Task4 packages the final combined code, never the source editable installation. Task5 depends on all prior acceptance and idle production.

- Task1 implemented; first combined suite380passed1warning; independent review in progress (task1-report.md/task1-scoped.diff). Upgrade child encoding warning reproduced and fixed in root-owned script, with retained RED evidence.
- Task3 preparatory spec+quality review PASS after2repair rounds: prebind verifier/admit before implementation; per-milestone integrate+targeted ACK before dependency; retain original workspaces until final gates. Contracts18passed; transport/UI subset26passed. This is not packaged/native acceptance.
- Root upgrade checks23passed with -W error after explicit UTF-8 child streams and closing existing test-fixture SQLite connections; no warning suppression or errors=replace. Version metadata candidate0.3.0/locks coherent; metadata2passed.

- Task1 COMPLETE after original-author fix round1: independent spec+quality PASS;75focused tests plus4differential v1/v2 entry probes passed. Shared dependency dispatch remains compatible withv1; new implementation-entry rechecks apply onlyv2. See task1-review.md. Task2 is running; no full/native/installed completion is claimed.
