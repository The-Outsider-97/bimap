"""
Process-local audit consumer for BIMAP development mode.

The canonical Queue port intentionally owns submission only.  InProcessQueue
therefore retains submitted AuditJob objects but does not execute them.

This host is the missing local-development process owner: it polls a queue that
exposes ``snapshot()`` and routes each newly observed AuditJob through the
already-composed Worker Runner exactly once per process lifetime.

Production deployments must use a durable broker/worker process instead.
"""

from __future__ import annotations

import asyncio

from collections.abc import Callable
from typing import Any, Final

from ..contracts.audit_job import AuditJob
from .runner import Runner
from logs.logger import PrettyPrinter, get_logger  # type: ignore


logger = get_logger("BIMAP Local Audit Worker Host")
printer = PrettyPrinter()

_COMPONENT: Final[str] = "local_audit_worker_host"


class LocalAuditWorkerHost:
    """Consume InProcessQueue-style audit snapshots without changing Queue port."""

    __slots__ = (
        "_queue",
        "_runner",
        "_poll_seconds",
        "_attempted",
        "_task",
        "_stopping",
    )

    def __init__(
        self,
        queue: Any,
        runner: Runner,
        *,
        poll_seconds: float = 0.20,
    ) -> None:
        snapshot = getattr(queue, "snapshot", None)
        if not callable(snapshot):
            raise TypeError(
                "LocalAuditWorkerHost requires a queue exposing snapshot()."
            )
        if not isinstance(runner, Runner):
            raise TypeError("runner must be a BIMAP Runner.")
        if (
            isinstance(poll_seconds, bool)
            or not isinstance(poll_seconds, (int, float))
            or float(poll_seconds) <= 0.0
        ):
            raise ValueError("poll_seconds must be a positive number.")

        self._queue = queue
        self._runner = runner
        self._poll_seconds = float(poll_seconds)
        self._attempted: set[str] = set()
        self._task: asyncio.Task[None] | None = None
        self._stopping = asyncio.Event()

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(self) -> None:
        if self.running:
            return
        self._stopping.clear()
        self._task = asyncio.create_task(
            self._run(),
            name="bimap-local-audit-worker",
        )
        logger.info(
            {
                "event": "local_audit_worker_started",
                "poll_seconds": self._poll_seconds,
            }
        )

    async def close(self) -> None:
        task = self._task
        if task is None:
            return

        self._stopping.set()
        try:
            await task
        except asyncio.CancelledError:
            pass
        finally:
            self._task = None

        logger.info({"event": "local_audit_worker_stopped"})

    async def _run(self) -> None:
        while not self._stopping.is_set():
            try:
                snapshot = tuple(self._queue.snapshot())
            except Exception:
                logger.exception("Local audit queue snapshot failed.")
                await self._sleep_or_stop()
                continue

            for job in snapshot:
                if not isinstance(job, AuditJob):
                    logger.error(
                        {
                            "event": "local_audit_worker_invalid_job",
                            "received_type": type(job).__name__,
                        }
                    )
                    continue

                if job.job_id in self._attempted:
                    continue

                # Mark before execution. This host deliberately avoids accidental
                # repeated execution. A user retry creates a new canonical job.
                self._attempted.add(job.job_id)

                logger.info(
                    {
                        "event": "local_audit_worker_job_started",
                        "job_id": job.job_id,
                        "order_id": job.order_id,
                    }
                )

                execution = await asyncio.to_thread(
                    self._runner.run_audit,
                    job,
                )

                if execution.succeeded:
                    logger.info(
                        {
                            "event": "local_audit_worker_job_completed",
                            "job_id": job.job_id,
                            "order_id": job.order_id,
                            "duration_ms": execution.duration_ms,
                        }
                    )
                else:
                    error = execution.error
                    logger.error(
                        {
                            "event": "local_audit_worker_job_failed",
                            "job_id": job.job_id,
                            "order_id": job.order_id,
                            "duration_ms": execution.duration_ms,
                            "error_type": (
                                type(error).__name__
                                if error is not None
                                else None
                            ),
                            "error_code": getattr(error, "code", None),
                        }
                    )

            await self._sleep_or_stop()

    async def _sleep_or_stop(self) -> None:
        try:
            await asyncio.wait_for(
                self._stopping.wait(),
                timeout=self._poll_seconds,
            )
        except asyncio.TimeoutError:
            return


__all__ = ["LocalAuditWorkerHost"]
