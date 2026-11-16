"""Background stall detection loop posting board alerts and SSE stall_alert events."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from orchestrator.board import BoardService
from orchestrator.db import Database

logger = logging.getLogger(__name__)


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
        ts = float(now) if now is not None else time.time()
        stalled_workers = self.db.find_stalled_workers(now=ts)
        alerts: list[dict[str, Any]] = []

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
