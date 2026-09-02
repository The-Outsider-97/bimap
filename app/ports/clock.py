"""
BIMAP application clock port.

The clock port abstracts wall-clock time so application services, commands,
workers, expiry checks, and retention calculations can be deterministic under
test.  It defines an interface only; concrete system/test clock implementations
belong to composition/adapters and are injected into application services.

Temporal policy
---------------
* Public clock values are always timezone-aware UTC datetimes.
* Naive datetimes are rejected rather than guessed to be UTC.
* ``is_expired()`` treats a deadline as expired when ``now >= deadline``.
* ``deadline_after()`` accepts only non-negative durations.  The duration itself
  is supplied by an outer policy/configuration owner; this port never invents
  retention periods, payment windows, or signed-URL lifetimes.
* No sleeping, scheduling, retrying, or worker orchestration is owned here.

Adapters implement :meth:`Clock._read_utc_now`.  Keeping the public :meth:`now`
method concrete guarantees that every adapter result passes the same validation,
error translation, logging, and ``PrettyPrinter`` boundary.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime, timedelta

from ..utils.app_errors import *
from ..utils.app_helpers import *
from logs.logger import PrettyPrinter, get_logger  # type: ignore


logger = get_logger("BIMAP Clock Port")
printer = PrettyPrinter()

_COMPONENT = "clock"


class Clock(ABC):
    """Abstract UTC wall-clock dependency for BIMAP application logic."""

    def __init__(self) -> None:
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Initializing clock port",
            event="clock_init_start",
        )
        logger.debug({"event": "clock_initialized", "implementation": type(self).__name__})

    @abstractmethod
    def _read_utc_now(self) -> datetime:
        """Return the implementation's current wall-clock value.

        Implementations should return a timezone-aware datetime.  The public
        :meth:`now` method performs the authoritative validation/UTC conversion.
        Implementations should not log customer data and should translate
        provider-specific state only when necessary.
        """
        raise NotImplementedError

    def now(self) -> datetime:
        """Return the current time as a validated timezone-aware UTC datetime."""
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Reading current UTC time",
            event="clock_now_start",
        )
        try:
            value = self._read_utc_now()
        except AppError:
            raise
        except Exception as exc:
            raise ClockReadError(
                "Clock implementation failed to provide the current time.",
                component=_COMPONENT,
                operation="now",
                context={"implementation": type(self).__name__},
                cause=exc,
            ) from exc

        try:
            return ensure_app_utc_datetime(
                value,
                field="clock_now",
                error_type=ClockValidationError,
                component=_COMPONENT,
                operation="now",
            )
        except ClockValidationError as exc:
            raise ClockReadError(
                "Clock implementation returned an invalid wall-clock value.",
                component=_COMPONENT,
                operation="now",
                field="clock_now",
                context={"implementation": type(self).__name__},
                cause=exc,
            ) from exc

    def deadline_after(self, duration: timedelta) -> datetime:
        """Return ``now + duration`` without defining the duration policy itself."""
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Calculating application deadline",
            event="clock_deadline_after_start",
        )
        normalized_duration = require_non_negative_timedelta(
            duration,
            field="duration",
            error_type=ClockValidationError,
            component=_COMPONENT,
            operation="deadline_after",
        )
        return self.now() + normalized_duration

    def is_expired(self, expires_at: datetime | str) -> bool:
        """Return whether an aware UTC-normalizable deadline has been reached."""
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Evaluating application expiry",
            event="clock_is_expired_start",
        )
        deadline = ensure_app_utc_datetime(
            expires_at,
            field="expires_at",
            error_type=ClockValidationError,
            component=_COMPONENT,
            operation="is_expired",
        )
        return self.now() >= deadline

    def remaining(self, expires_at: datetime | str) -> timedelta:
        """Return signed time remaining until a deadline.

        A negative result means the deadline has passed.  The method deliberately
        does not clamp to zero because callers may need the exact lateness for
        observability or deterministic policy evaluation.
        """
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Calculating remaining application time",
            event="clock_remaining_start",
        )
        deadline = ensure_app_utc_datetime(
            expires_at,
            field="expires_at",
            error_type=ClockValidationError,
            component=_COMPONENT,
            operation="remaining",
        )
        return deadline - self.now()


__all__ = ["Clock"]


if __name__ == "__main__":
    from datetime import timezone

    print("\n=== Running Clock Port Self-Test ===\n")
    printer.status("TEST", "Clock port module initialized", "info")

    class _FixedClock(Clock):
        def __init__(self, value: datetime) -> None:
            self._value = value
            super().__init__()

        def _read_utc_now(self) -> datetime:
            return self._value

    fixed = _FixedClock(datetime(2026, 9, 2, 20, 0, tzinfo=timezone.utc))
    assert fixed.now() == datetime(2026, 9, 2, 20, 0, tzinfo=timezone.utc)
    assert fixed.deadline_after(timedelta(minutes=5)) == datetime(
        2026, 9, 2, 20, 5, tzinfo=timezone.utc
    )
    assert fixed.is_expired("2026-09-02T20:00:00Z") is True
    assert fixed.remaining("2026-09-02T20:05:00Z") == timedelta(minutes=5)
    printer.status("PASS", "Clock UTC/deadline behavior", "success")

    print("\n=== Test ran successfully ===\n")