# Workflow correctness and isolated acceptance

Approved scope: fix the five audited issues; execute complete boost and teamwork native-agent workflows including verifier FAIL, original actor rework, verifier PASS; preserve active swarm.

Fix Git cleanup proof after a new post-merge commit. Correct the shared writable dependency-link description. Fix boost Solo rework and human-message consumption. Distinguish Git and snapshot acceptance in teamwork. Reuse current infrastructure; no new dependencies.

Acceptance uses an independent localhost server, database, and disposable projects. An explicitly fault-injected candidate exercises real failing tests, never a fabricated verdict. Preserve actual native identities, command outputs, packets, board receipts, final integrated verification and cleanup.

Do not modify installed plugin files, global Codex settings, live state.db, or active swarm. Do not restart production port 18790. All test writes go through the isolated server's supported HTTP/RPC endpoints, not the configured production MCP connection.

Mode separation is advice only: separate entry pages and mode-filtered lists on one service are preferred to three production daemons. A temporary test port is infrastructure isolation, not a UI feature change.
