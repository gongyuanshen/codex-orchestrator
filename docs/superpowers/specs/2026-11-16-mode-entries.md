# Mode-specific dashboard entries

User-approved scope: retain production port 18790; give Boost, Teamwork and Swarm separate entries showing only their own mode's tasks. Implement and verify source first. Do not modify/restart the installed plugin while the existing Swarm is running. Combine with the pending workflow/performance fixes after that task ends.

## Contract

- One daemon, one database, one shared HTML/CSS/JS asset set. `/` offers three entries; `/boost`, `/teamwork`, `/swarm` fix the mode for the lifetime of the page. Native links perform navigation.
- Explain the modes in concise user-facing language; preserve overview-first display, expandable logs and secondary controls. No new dependency or unrelated layout rewrite.
- Both default active-run discovery and project task lists filter by the current mode. Project selection can show a mode-specific empty state. Refresh discovers new runs only in that mode and keeps the current selection.
- Preserve old root links with `run_id`: exact lookup, validate optional project, then replace the URL with its canonical mode path. Root project-only links retain project context in the entry links.
- An explicit old run outside the latest 100 remains accessible and selected. A lean exact-run metadata endpoint avoids reading full logs twice.
- Unknown run, wrong project or wrong mode must produce an explicit error, never silently show another task. Validate snapshot identity before displaying it or starting its SSE connection.
- Preserve selection generation guards, old-response rejection, SSE ownership and late-human-receipt behavior. Nested run updates cannot change the page's run/mode.
- This separates views, not authorization or storage; no extra ports, instances, databases or automatic cross-run subscription.

## Verification and release boundary

Use temporary test databases and a source-only service on 19890. Exercise mixed-mode projects, empty modes, all three entries, legacy links, old run links, wrong context, refresh, race guards, light/dark and narrow layouts. Run the full regression suite and independent review. Compare production health identity and installed file hashes before/after. Do not copy production data into fixtures or mutate the running Swarm.
