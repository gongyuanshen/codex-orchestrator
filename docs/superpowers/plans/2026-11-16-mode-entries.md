# Mode entry implementation plan

Spec: `../specs/2026-11-16-mode-entries.md`

- [x] Read entry, list, snapshot, SSE and test paths; freeze mode-entry contract.
- [x] Capture production process identity, active Swarm and installed hashes; back up owned source files.
- [x] Backend worker: explicit page routes and lean exact-run API; real HTTP regression tests.
- [x] Frontend worker: root chooser, fixed mode navigation, scoped queries, deep-link resolution and context validation; Node behavior tests.
- [x] Coordinator: README, isolated mixed-mode fixture service and real browser verification.
- [x] Independent review and full regression; fix concrete failures and rerun.
- [x] Stop isolated service; verify production identity and installation unchanged; record pending combined activation.

Ownership: backend worker owns `daemon.py` and `test_daemon_web.py`; frontend worker owns the three web assets and `test_dashboard_behavior.py`; coordinator owns README and these evidence/spec/plan files. No owner may write the installed plugin or live Swarm state. Current checkout is non-Git; backups provide rollback and no new worktree is needed.
