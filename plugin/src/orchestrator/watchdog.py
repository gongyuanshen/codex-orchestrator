"""Background stall detection loop posting board alerts and SSE stall_alert events."""

from __future__ import annotations

import asyncio
import logging
import json
import time
from typing import Any

from orchestrator.board import BoardService
from orchestrator.db import Database

logger = logging.getLogger(__name__)


def classify_run(db: Database, run_id: str, *, now: float | None = None,
                 precomputed_native: dict[str, Any] | None = None,
                 precomputed_readiness: dict[str, Any] | None = None) -> dict[str, Any]:
    """Read persisted facts, never infer host death or perform admission/dispatch.

    Planned steps are potential work, not proof of current artifact admission.
    Only the original coordinator may check that gate and perform native calls.
    """
    from orchestrator.db import NotFoundError
    from orchestrator.native_actions import NativeActionStore
    from orchestrator.workflow import WorkflowService

    run = db.get_run(run_id)
    if run is None:
        raise NotFoundError(f'Run not found: {run_id}')
    ts = time.time() if now is None else float(now)
    flow = WorkflowService(db)
    with db.read_connection() as conn:
        steps = [dict(row) for row in conn.execute('SELECT * FROM workflow_steps WHERE run_id=? ORDER BY created_at,id', (run_id,))]
    workers = {w['name']: w for w in db.list_workers(run_id=run_id)}
    pending_steps = [s for s in steps if s['state'] != 'done']
    native = precomputed_native if precomputed_native is not None else NativeActionStore(db).project(run_id)
    readiness = precomputed_readiness if precomputed_readiness is not None else flow.plan_readiness(run_id)
    with db.read_connection() as conn:
        outstanding_inbox = [dict(row) for row in conn.execute("SELECT id,status,step_id FROM inbox_messages WHERE run_id=? AND kind!='notice' AND status IN ('pending','accepted')", (run_id,))]
        failed_native = [dict(row) for row in conn.execute("SELECT action_id,source_type,source_id,recipient FROM native_actions WHERE run_id=? AND status='failed' AND outcome='failed'", (run_id,))]
        native_wakes = [dict(row) for row in conn.execute("SELECT recipient,snapshot_json,history_json FROM native_actions WHERE run_id=? AND source_type!='control' AND status IN ('invoking','confirmed')", (run_id,))]
        control_pending = conn.execute("SELECT count(*) FROM worker_controls WHERE run_id=? AND status='pending'", (run_id,)).fetchone()[0]
    wake_epochs = {}
    for wake in native_wakes:
        snap = json.loads(wake['snapshot_json'])
        binding = (wake['recipient'], snap.get('agent_id') or snap.get('pending_agent_path'))
        for event in json.loads(wake['history_json']):
            if event['operation'] == 'begin':
                wake_epochs[binding] = max(wake_epochs.get(binding, 0), event['at'])
    unsettled_native = any(native['counts'][status] for status in ('claimed', 'invoking', 'needs_reconciliation'))
    incomplete = run['status'] == 'active' and bool(not steps or pending_steps or outstanding_inbox or control_pending or unsettled_native or failed_native or readiness['uncovered_criteria'] or native['next_actions_count'] or native['needs_clarification_count'])
    blockers, executable, potential, dependency_waits = [], [], [], []
    blocked_kinds = set()

    def block(kind, reason, **context):
        blocked_kinds.add(kind)
        blockers.append(dict(kind=kind, reason=reason, **context))

    for item in failed_native:
        block('native_failure', 'Native invocation definitively failed; original coordinator must decide, not automatically replay', **item)
    for item in native['needs_reconciliation']:
        block('reconcile_native', 'Inspect the original native call; never automatically replay',
              action_id=item['action_id'], revision=item['revision'], recipient=item['recipient'])
    for item in native['needs_clarification']:
        block('clarify_source', item['reason'], source_type=item['source_type'], source_id=item['source_id'])
    for item in native['next_actions']:
        if item['source_type'] == 'control':
            executable.append(dict(kind='native_control', source_id=item['source_id'], recipient=item['recipient']))
        elif item['action_type'] == 'spawn_agent':
            executable.append(dict(kind='native_spawn', source_id=item['source_id'], recipient=item['recipient'],
                                   pending_agent_path=item.get('pending_agent_path')))
    for group in native['next_action_groups']:
        executable.append(dict(kind='native_handoff', recipient=group['recipient'], agent_id=group['agent_id'],
                               source_ids=[m['source_id'] for m in group['members']]))
    states = {s['id']: s['state'] for s in steps}
    for message in outstanding_inbox:
        if message['status'] == 'accepted' and states.get(message['step_id']) == 'done':
            block('reconcile_inbox', 'Step is done but its accepted business receipt is unresolved', message_id=message['id'])
    for step in pending_steps:
        state = step['state']
        checkpoint = json.loads(step['checkpoint_json'])
        identity = dict(step_id=step['id'], revision=step['revision'], state=state,
                        attempt=step['attempt'], contract_revision=step['contract_revision'],
                        state_entered_at=checkpoint.get('state_entered_at', step['created_at']))
        if state == 'needs_decision':
            block('user_decision', 'Original rework budget exhausted; an explicit decision is required', **identity)
            continue
        if state == 'reconciliation_required':
            block('reconcile_step', 'Inspect the saved workflow checkpoint before continuing', **identity)
            continue
        role = 'verifier' if state in {'candidate_review', 'integrated_review'} else 'owner'
        worker = workers.get(step[role])
        expected_aid = step[role + '_agent_id']
        if run['workflow_version'] == 5 and expected_aid == '':
            if worker is None or worker['agent_id'] is not None:
                block('binding_changed', 'Original native binding is unavailable; do not replace it automatically', **identity)
                continue
            if worker['status'] in {'stopped', 'killed'}:
                block('user_decision', 'Original worker is stopped; automatic resume is forbidden', worker=worker['name'], **identity)
                continue
            dependencies = json.loads(step['dependencies_json'])
            unresolved = [dep_id for dep_id in dependencies if states.get(dep_id) != 'done']
            if state == 'planned':
                if unresolved:
                    dependency_waits.append(dict(step_id=step['id'], dependencies=unresolved))
                else:
                    potential.append(dict(kind='dispatch_requires_admission', step_id=step['id']))
                continue
            if state in {'candidate_review', 'integrated_review'}:
                potential.append(dict(kind='verifier_spawn_required', step_id=step['id'], state=state, verifier=worker['name']))
                continue
            continue
        if worker is None or worker['agent_id'] != expected_aid:
            block('binding_changed', 'Original native binding is unavailable; do not replace it automatically', **identity)
            continue
        if worker['status'] in {'stopped', 'killed'}:
            block('user_decision', 'Original worker is stopped; automatic resume is forbidden', worker=worker['name'], **identity)
            continue
        dependencies = json.loads(step['dependencies_json'])
        unresolved = [identity for identity in dependencies if states.get(identity) != 'done']
        if state == 'planned':
            if unresolved:
                dependency_waits.append(dict(step_id=step['id'], dependencies=unresolved))
            else:
                potential.append(dict(kind='dispatch_requires_admission', step_id=step['id']))
            continue
        if state in {'rework', 'ready_to_merge', 'handoff'}:
            potential.append(dict(kind='workflow_gate_required', step_id=step['id'], state=state))
            continue
        observation = checkpoint.get('native_observations', {}).get(worker['agent_id'])
        after = checkpoint.get('owner_last_dispatch_at', step['created_at']) if role == 'owner' else checkpoint.get('state_entered_at', step['created_at'])
        after = max(after, wake_epochs.get((worker['name'], worker['agent_id']), 0))
        current = observation is not None and observation['observed_at'] >= after and ts - observation['observed_at'] <= worker['stall_threshold_sec']
        if current and observation['state'] == 'ended':
            block('user_decision', 'Original native agent ended; replacement requires a decision', worker=worker['name'], **identity)
        elif current and observation['state'] in {'idle', 'interrupted'}:
            block('idle_incomplete', 'Observed native agent is not progressing its incomplete step', worker=worker['name'], **identity)
        elif not current and worker['status'] == 'working' and ts - worker['last_heartbeat_at'] > worker['stall_threshold_sec']:
            block('observe_host', 'Heartbeat expired; inspect native state, not evidence of agent death', worker=worker['name'], **identity)
    if not steps and not executable:
        potential.append(dict(kind='plan_required'))
    if not incomplete:
        classification = 'completed' if run['status'] == 'completed' or run['status'] == 'active' and steps and not pending_steps else 'inactive'
    elif executable:
        classification = 'ready'
    elif blocked_kinds & {'reconcile_native', 'reconcile_step', 'binding_changed', 'clarify_source', 'reconcile_inbox'}:
        classification = 'needs_reconciliation'
    elif blocked_kinds & {'user_decision', 'native_failure'}:
        classification = 'user_decision'
    elif 'observe_host' in blocked_kinds:
        classification = 'host_unknown'
    elif 'idle_incomplete' in blocked_kinds:
        classification = 'no_progress'
    elif dependency_waits:
        classification = 'dependency_wait'
    else:
        classification = 'normal_wait'
    # A draft is not proven executable, but is also not proof of global stagnation.
    alert_required = bool(incomplete and not executable and not potential and blockers)
    return dict(classification=classification, incomplete=incomplete,
                executable_candidates=executable, potential_candidates=potential,
                blockers=blockers, dependency_waits=dependency_waits, plan_readiness=readiness,
                coordinator_required=bool(incomplete and (executable or potential or blockers)),
                alert_required=alert_required, lifecycle_authority='original_root_coordinator')


class StallWatchdog:
    """Periodically scans working workers for heartbeat timeouts and emits stall alerts."""

    def __init__(
        self,
        db: Database,
        board: BoardService,
        event_bus: Any | None = None,
        *,
        interval_sec: float | None = None,
    ) -> None:
        self.db = db
        self.board = board
        self.event_bus = event_bus
        self.interval_sec = float(
            interval_sec
            if interval_sec is not None
            else db.settings.watchdog_interval_sec
        )
        if self.interval_sec <= 0:
            raise ValueError(f"interval_sec must be > 0, got {self.interval_sec}")
        self._stop_event: asyncio.Event | None = None
        self._running = False

    def _emit(
        self, event: str, data: dict[str, Any], *, run_id: str | None = None
    ) -> None:
        if self.event_bus is None:
            return
        if hasattr(self.event_bus, "publish"):
            try:
                self.event_bus.publish(event, data, run_id=run_id)
            except TypeError:
                self.event_bus.publish(event, data)
        elif hasattr(self.event_bus, "emit"):
            self.event_bus.emit(event, data)

    def scan_once(self, *, now: float | None = None) -> list[dict[str, Any]]:
        """Scan once for stalled workers, post board alert, mark alerted, and broadcast SSE."""
        from orchestrator.native_actions import NativeActionStore
        NativeActionStore(self.db).expire()
        ts = float(now) if now is not None else time.time()
        # Workflow0-3 keep their historical heartbeat alert contract.
        stalled_workers = [w for w in self.db.find_stalled_workers(now=ts)
                           if self.db.get_run(w['run_id'])['workflow_version'] not in {4, 5}]
        alerts: list[dict[str, Any]] = []
        with self.db.read_connection() as conn:
            run_ids = [row['id'] for row in conn.execute("SELECT id FROM runs WHERE status='active' AND workflow_version IN (4, 5)")]
        for run_id in run_ids:
            facts = classify_run(self.db, run_id, now=ts)
            if not facts['alert_required']:
                continue
            with self.db.transaction() as conn:
                # A closed run or newly executable handoff must win the read/post race.
                facts = classify_run(self.db, run_id, now=ts)
                if not facts['alert_required']:
                    continue
                from orchestrator.native_actions import digest
                signature = digest([facts['classification'], [{k: v for k, v in blocker.items() if k != 'revision'} for blocker in facts['blockers']]])[:16]
                # Heartbeat expiry alone is not host evidence of a stall (long builds and test runs are
                # normal), so without any native-state blocker it is posted as a hint, not an alert.
                heartbeat_only = bool(facts['blockers']) and all(b['kind'] == 'observe_host' for b in facts['blockers'])
                level = 'hint' if heartbeat_only else 'alert'
                prefix = 'Heartbeat hint' if heartbeat_only else 'Coordination alert'
                summary = f"{prefix}: {facts['classification']} [{signature}]"
                if conn.execute("SELECT 1 FROM board_messages WHERE run_id=? AND sender='watchdog' AND msg_type IN ('alert','info') AND summary=?", (run_id, summary)).fetchone():
                    continue
                lead = ('Heartbeat expired without native-state evidence; observe the host (list_agents / wait_agent) '
                        'before acting. This is a hint, not evidence that the agent stopped.' if heartbeat_only else
                        'Waiting for coordinator handling. Events are hints; recover from the database.')
                pointer = self.board.board_post(run_id, sender='watchdog', sender_role='system',
                    mentions=['coordinator'], msg_type='info' if heartbeat_only else 'alert', summary=summary,
                    body_md=lead + '\n\n' + '\n'.join('- ' + item['reason'] for item in facts['blockers']))
            message = self.board.get_message(pointer['seq'])
            self._emit('board_message', {'run_id': run_id, 'message': message}, run_id=run_id)
            self._emit('stall_hint' if heartbeat_only else 'stall_alert',
                       {'run_id': run_id, 'classification': facts, 'message': message}, run_id=run_id)
            alerts.append(dict(classification=facts, message=message, level=level))

        for worker in stalled_workers:
            worker_id = worker["id"]
            run_id = worker["run_id"]
            age_sec = max(0.0, ts - float(worker["last_heartbeat_at"]))
            threshold = int(worker["stall_threshold_sec"])

            summary = (
                f"Stall alert: @{worker['name']} silent for {int(age_sec)}s "
                f"(threshold {threshold}s)"
            )
            body_lines = [
                f"**Watchdog Stall Alert** for `@{worker['name']}` (role: `{worker['role']}`)",
                f"- **Heartbeat Age**: `{age_sec:.1f}s` (threshold `{threshold}s`)",
                f"- **Current Task**: {worker.get('current_task') or '(unspecified)'}",
                f"- **Workspace ID**: `{worker.get('workspace_id') or 'none'}`",
                "",
                f"@coordinator @{worker['name']} please inspect workspace_status or wake worker.",
            ]
            pointer = self.board.board_post(
                run_id,
                sender="watchdog",
                sender_role="system",
                channel="main",
                mentions=["coordinator", worker["name"]],
                msg_type="alert",
                summary=summary,
                body_md="\n".join(body_lines),
            )
            updated_worker = self.db.mark_worker_stalled_alerted(
                worker_id, alerted=True
            )
            full_msg = self.board.get_message(int(pointer["seq"]))

            if full_msg is not None:
                self._emit(
                    "board_message",
                    {"run_id": run_id, "message": full_msg},
                    run_id=run_id,
                )
            self._emit(
                "worker_updated",
                {"run_id": run_id, "worker": updated_worker},
                run_id=run_id,
            )
            self._emit(
                "stall_alert",
                {
                    "run_id": run_id,
                    "worker_id": worker_id,
                    "worker_name": updated_worker["name"],
                    "worker": updated_worker,
                    "message": full_msg,
                    "heartbeat_age_sec": round(age_sec, 3),
                    "threshold_sec": threshold,
                },
                run_id=run_id,
            )
            alerts.append(
                {
                    "worker": updated_worker,
                    "message": full_msg,
                    "heartbeat_age_sec": round(age_sec, 3),
                }
            )

        return alerts

    def stop(self) -> None:
        """Signal the background loop to exit promptly."""
        self._running = False
        if self._stop_event is not None:
            self._stop_event.set()

    async def run(self) -> None:
        """Run the periodic stall scan loop until stop() or task cancellation."""
        self._stop_event = asyncio.Event()
        self._running = True
        try:
            while self._running and not self._stop_event.is_set():
                try:
                    await asyncio.to_thread(self.scan_once)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("Watchdog scan_once failed")

                try:
                    await asyncio.wait_for(
                        self._stop_event.wait(), timeout=self.interval_sec
                    )
                except asyncio.TimeoutError:
                    continue
        finally:
            self._running = False
