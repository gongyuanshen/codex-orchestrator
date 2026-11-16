# Workflow fixes and isolated acceptance implementation plan

> For agentic workers: use subagent-driven-development, with RED before GREEN and independent review.

**Goal:** Fix audited defects and demonstrate native-agent failure/rework paths without changing live swarm.
**Architecture:** Existing workspace manager, SQLite and skills; isolated HTTP/RPC test server and native collaboration APIs.
**Tech Stack:** Python, SQLite, PowerShell and native Codex agents; no new dependencies.
**Spec:** ../specs/2026-11-16-workflow-fixes.md

## Ownership and tasks
- [x] Backend worker: workspace_mgr.py, db.py if needed, test_workspace_mgr.py, test_db_board.py and the approved schema-version assertion in test_worker_control.py. Reproduce cleanup after post-merge commit; persist actual merge proof; test merge/cherry-pick, no-op merge, missing proof, branch changes and snapshot non-regression.
- [x] Workflow worker: boost/teamwork/swarm skills, tests/test_workflow_contracts.py, README relevant semantics. Fix Solo rework, explicit human-message reading/routing, Git/snapshot rules and shared dependency semantics. Contract checks do not prove native execution.
- [x] Coordinator: backups, plan, isolated acceptance fixtures and evidence. Create real runs and native agents; execute boost Solo and delegated rework, then teamwork parallel snapshot writers, real FAIL, same-writer rework, same-verifier PASS, sequential merge, integrated tests and cleanup.
- [x] Independent verifier: audit fixes and acceptance evidence; run relevant tests. Coordinator repeats full suite and compares production health and installed hashes.

## Review focus
- New post-merge commits must survive cleanup rejection; old records without proof must fail explicitly.
- Cherry-pick remains cleanable; snapshot does not require commits.
- Shared dependency links must not be described as read-only or write-isolated.
- Human text delivery does not imply execution; original writer/verifier are retained through rework.
- No installed-file mutation, production daemon restart or live-run test writes.

## Validation
Run `.venv/Scripts/python.exe -m pytest -q` and `node --check plugin/src/orchestrator/web/app.js`. Record exact commands and real outputs. This source directory is not a Git checkout; no Git commit is performed here.

## Recorded rulings
- Preserve schema v3 legacy NULL proof; never manufacture historical integration. Repeated cherry-pick uses paired source/target ancestry, not whitespace-insensitive patch equivalence.
- Native premerge verification assesses candidate criteria; merge/integration/cleanup criteria remain explicitly pending until the final same-verifier gate. No criterion is deleted to obtain PASS.
- Teamwork verifier rejected coordinator fault stimulus mismatch; harness corrected to frozen AC05 +1 and reverified. Empty writer evidence bodies were corrected using body_md and retained actual outputs, not fabricated reruns.
