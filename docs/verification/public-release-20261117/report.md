# Public repository and v0.2.0 pre-release verification

## Published scope

- Repository: https://github.com/gongyuanshen/codex-orchestrator (public, `main`).
- Pre-release tag: `v0.2.0`, pointing to source commit `156ad8882b1114281588e25c59c69dfef865979f`.
- The source plugin is version 0.2.0; the existing user installation remained enabled at version 0.1.2. Publication did not upgrade it.
- Source, tests, plans and verification summaries are tracked. Runtime databases, local configurations, raw evidence and backups are excluded. A pattern-based scan of the staged files found no matches for the checked credential formats; it is not a complete security audit.

## Checks performed

- `.venv/Scripts/python.exe -m pytest -q`: **348 passed in 66.89 s** on Windows, Python 3.14.4.
- `node --check plugin/src/orchestrator/web/app.js`: passed.
- Read back GitHub's repository visibility, default branch, release metadata and tag commit; the tag matched the published source commit.
- Using the bundled Codex CLI 0.167.0-alpha.4.1 and a separate temporary `CODEX_HOME`, added `gongyuanshen/codex-orchestrator@v0.2.0`, installed `tri-orchestrator@tri-orchestrator`, and read back enabled version 0.2.0. Codex warned that it would not create PATH aliases inside the temporary directory.
- `git diff --cached --check` before the initial commit reported existing trailing whitespace in `plugin/src/orchestrator/web/app.js:1983` and an extra blank line at the end of `tests/test_db_board.py`. Their contents were left unchanged.

## Boundaries

The isolated marketplace check did not start the installed 0.2.0 daemon, migrate production data or exercise the desktop MCP interface. The normal `codex` command on this host points to a missing executable; the bundled CLI was used for the installation check without changing the user's launcher or global configuration. Initial direct Git push was reset; retry with the existing local proxy succeeded without changing persistent Git network settings.
