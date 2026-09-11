"""
Durable SQLite implementation of BIMAP's AuditResultStore port.

Architectural role
------------------
This adapter persists the latest completed AuditResultRecord per BIMAP order.

It deliberately owns only infrastructure concerns:

- durable SQLite storage;
- transactional replacement of the latest result per order;
- idempotent replay of the exact same completed job;
- stale-result rejection;
- persistence-integrity verification.

It does NOT:

- execute audits;
- interpret SLAI output;
- modify deterministic findings;
- make governance decisions;
- define a second workspace schema;
- change AuditResultRecord semantics.

The existing app.ports.audit_results.AuditResultStore remains the application
boundary and can later be implemented by PostgreSQL or another provider without
changing AuditService, API routes, or frontend contracts.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import sqlite3

from pathlib import Path
from threading import RLock
from typing import Any, NoReturn

from ..app.ports.audit_results import (
    AuditResultRecord,
    AuditResultStore,
)
from ..app.utils.app_errors import *
from ..app.utils.app_helpers import *
from logs.logger import PrettyPrinter, get_logger  # type: ignore


logger = get_logger(
    "BIMAP SQLite Audit Result Store"
)
printer = PrettyPrinter()


_COMPONENT = "sqlite_audit_result_store"

_SCHEMA_VERSION = 1
_TABLE_NAME = "bimap_audit_results"

_DEFAULT_BUSY_TIMEOUT_MS = 10_000


class SQLiteAuditResultStore(
    AuditResultStore
):
    """
    Durable transactional AuditResultStore implementation.

    Storage invariant
    -----------------
    Exactly one latest completed result is retained per order.

    Job invariant
    -------------
    A job identifier may belong to only one order.

    Replay invariant
    ----------------
    Re-saving the exact same completed record is idempotent.

    Mutation invariant
    ------------------
    Reusing the same job_id for different content is rejected.

    Ordering invariant
    ------------------
    A different job may replace the current order result only when its
    completion timestamp is strictly newer.
    """

    def __init__(
        self,
        database_path: str | Path,
        *,
        busy_timeout_ms: int = (
            _DEFAULT_BUSY_TIMEOUT_MS
        ),
    ) -> None:
        printer.status(
            "BIMAP",
            "Initializing durable audit-result store",
            "info",
        )

        raw_path = str(
            database_path
        ).strip()

        if not raw_path:
            raise AppConfigurationError(
                "SQLite audit-result database path cannot be empty.",
                component=_COMPONENT,
                operation="initialize",
                field="database_path",
            )

        if (
            isinstance(busy_timeout_ms, bool)
            or not isinstance(
                busy_timeout_ms,
                int,
            )
            or busy_timeout_ms <= 0
        ):
            raise AppConfigurationError(
                "busy_timeout_ms must be a positive integer.",
                component=_COMPONENT,
                operation="initialize",
                field="busy_timeout_ms",
            )

        path = Path(
            raw_path
        ).expanduser()

        try:
            path = path.resolve(
                strict=False
            )
        except OSError as exc:
            raise AppConfigurationError(
                "Unable to resolve SQLite audit-result database path.",
                component=_COMPONENT,
                operation="initialize",
                field="database_path",
                cause=exc,
            ) from exc

        if (
            path.exists()
            and path.is_dir()
        ):
            raise AppConfigurationError(
                "SQLite audit-result database path points to a directory.",
                component=_COMPONENT,
                operation="initialize",
                field="database_path",
            )

        try:
            path.parent.mkdir(
                parents=True,
                exist_ok=True,
            )
        except OSError as exc:
            raise AppConfigurationError(
                "Unable to create the SQLite audit-result database directory.",
                component=_COMPONENT,
                operation="initialize",
                field="database_path",
                cause=exc,
            ) from exc

        self._database_path = path
        self._busy_timeout_ms = (
            busy_timeout_ms
        )
        self._lock = RLock()

        super().__init__()

        self._initialize_schema()

        logger.info(
            {
                "event":
                    "sqlite_audit_result_store_initialized",
                "schema_version":
                    _SCHEMA_VERSION,
                "database_parent":
                    str(
                        self._database_path.parent
                    ),
            }
        )

    @property
    def database_path(
        self,
    ) -> Path:
        """
        Return the configured database path.

        Callers should not expose this path in API responses or user-facing
        diagnostics.
        """

        return self._database_path

    # ------------------------------------------------------------------
    # Connection / schema
    # ------------------------------------------------------------------

    def _connect(
        self,
    ) -> sqlite3.Connection:
        try:
            connection = sqlite3.connect(
                str(
                    self._database_path
                ),
                timeout=(
                    self._busy_timeout_ms
                    / 1000.0
                ),
                isolation_level=None,
                check_same_thread=False,
            )

            connection.row_factory = (
                sqlite3.Row
            )

            connection.execute(
                "PRAGMA foreign_keys = ON"
            )

            connection.execute(
                "PRAGMA synchronous = FULL"
            )

            connection.execute(
                (
                    "PRAGMA busy_timeout = "
                    f"{self._busy_timeout_ms}"
                )
            )

            return connection

        except sqlite3.Error as exc:
            self._raise_database_error(
                "connect",
                exc,
            )

    def _initialize_schema(
        self,
    ) -> None:
        with self._lock:
            connection = (
                self._connect()
            )

            try:
                # WAL improves concurrent reader/writer behavior while
                # retaining transactional durability for this local database.
                connection.execute(
                    "PRAGMA journal_mode = WAL"
                )

                current_version_row = (
                    connection.execute(
                        "PRAGMA user_version"
                    ).fetchone()
                )

                current_version = int(
                    current_version_row[0]
                    if current_version_row
                    else 0
                )

                if current_version not in {
                    0,
                    _SCHEMA_VERSION,
                }:
                    raise AppConfigurationError(
                        "Unsupported audit-result database schema version.",
                        component=_COMPONENT,
                        operation="initialize_schema",
                        context={
                            "expected_version":
                                _SCHEMA_VERSION,
                            "actual_version":
                                current_version,
                        },
                    )

                connection.execute(
                    "BEGIN IMMEDIATE"
                )

                connection.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS {_TABLE_NAME} (
                        order_id TEXT PRIMARY KEY NOT NULL,
                        job_id TEXT NOT NULL UNIQUE,
                        product_code TEXT NOT NULL,
                        completed_at TEXT NOT NULL,
                        payload_json TEXT NOT NULL,
                        record_sha256 TEXT NOT NULL
                    )
                    """
                )

                connection.execute(
                    f"""
                    CREATE INDEX IF NOT EXISTS
                        idx_{_TABLE_NAME}_completed_at
                    ON {_TABLE_NAME}(completed_at)
                    """
                )

                if current_version == 0:
                    connection.execute(
                        (
                            "PRAGMA user_version = "
                            f"{_SCHEMA_VERSION}"
                        )
                    )

                connection.commit()

            except AppError:
                self._rollback_quietly(
                    connection
                )
                raise

            except sqlite3.Error as exc:
                self._rollback_quietly(
                    connection
                )
                self._raise_database_error(
                    "initialize_schema",
                    exc,
                )

            finally:
                connection.close()

    # ------------------------------------------------------------------
    # AuditResultStore implementation
    # ------------------------------------------------------------------

    def _save(
        self,
        record: AuditResultRecord,
    ) -> AuditResultRecord:
        payload_json = (
            self._canonical_json(
                dict(
                    record.payload
                )
            )
        )

        incoming_fingerprint = (
            self._record_fingerprint(
                record
            )
        )

        with self._lock:
            connection = (
                self._connect()
            )

            try:
                connection.execute(
                    "BEGIN IMMEDIATE"
                )

                current_row = (
                    connection.execute(
                        f"""
                        SELECT
                            order_id,
                            job_id,
                            product_code,
                            completed_at,
                            payload_json,
                            record_sha256
                        FROM {_TABLE_NAME}
                        WHERE order_id = ?
                        """,
                        (
                            record.order_id,
                        ),
                    ).fetchone()
                )

                job_row = (
                    connection.execute(
                        f"""
                        SELECT
                            order_id
                        FROM {_TABLE_NAME}
                        WHERE job_id = ?
                        """,
                        (
                            record.job_id,
                        ),
                    ).fetchone()
                )

                if (
                    job_row is not None
                    and str(
                        job_row["order_id"]
                    )
                    != record.order_id
                ):
                    raise AppIntegrityError(
                        "Audit job identifier is already bound to another order.",
                        component=_COMPONENT,
                        operation="save",
                        field="job_id",
                        context={
                            "job_id":
                                record.job_id,
                            "order_id":
                                record.order_id,
                        },
                    )

                if current_row is None:
                    connection.execute(
                        f"""
                        INSERT INTO {_TABLE_NAME} (
                            order_id,
                            job_id,
                            product_code,
                            completed_at,
                            payload_json,
                            record_sha256
                        )
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            record.order_id,
                            record.job_id,
                            record.product_code,
                            record.completed_at,
                            payload_json,
                            incoming_fingerprint,
                        ),
                    )

                    connection.commit()
                    return record

                current = (
                    self._row_to_record(
                        current_row
                    )
                )

                current_fingerprint = str(
                    current_row[
                        "record_sha256"
                    ]
                )

                if (
                    current.job_id
                    == record.job_id
                ):
                    if not hmac.compare_digest(
                        current_fingerprint,
                        incoming_fingerprint,
                    ):
                        raise AppIntegrityError(
                            "The same audit job identifier attempted to persist different completed result content.",
                            component=_COMPONENT,
                            operation="save",
                            field="job_id",
                            context={
                                "order_id":
                                    record.order_id,
                                "job_id":
                                    record.job_id,
                            },
                        )

                    connection.commit()
                    return current

                current_completed = (
                    ensure_app_utc_datetime(
                        current.completed_at,
                        field=(
                            "current.completed_at"
                        ),
                        error_type=(
                            AppIntegrityError
                        ),
                        component=_COMPONENT,
                        operation="save",
                    )
                )

                incoming_completed = (
                    ensure_app_utc_datetime(
                        record.completed_at,
                        field=(
                            "record.completed_at"
                        ),
                        error_type=(
                            AppIntegrityError
                        ),
                        component=_COMPONENT,
                        operation="save",
                    )
                )

                if (
                    incoming_completed
                    <= current_completed
                ):
                    raise AppIntegrityError(
                        "An older or equally completed audit job cannot replace the current workspace result.",
                        component=_COMPONENT,
                        operation="save",
                        field="completed_at",
                        context={
                            "order_id":
                                record.order_id,
                            "current_job_id":
                                current.job_id,
                            "incoming_job_id":
                                record.job_id,
                            "current_completed_at":
                                current.completed_at,
                            "incoming_completed_at":
                                record.completed_at,
                        },
                    )

                connection.execute(
                    f"""
                    UPDATE {_TABLE_NAME}
                    SET
                        job_id = ?,
                        product_code = ?,
                        completed_at = ?,
                        payload_json = ?,
                        record_sha256 = ?
                    WHERE order_id = ?
                    """,
                    (
                        record.job_id,
                        record.product_code,
                        record.completed_at,
                        payload_json,
                        incoming_fingerprint,
                        record.order_id,
                    ),
                )

                connection.commit()

                return record

            except AppError:
                self._rollback_quietly(
                    connection
                )
                raise

            except sqlite3.IntegrityError as exc:
                self._rollback_quietly(
                    connection
                )

                raise AppIntegrityError(
                    "SQLite rejected an audit-result persistence integrity constraint.",
                    component=_COMPONENT,
                    operation="save",
                    context={
                        "order_id":
                            record.order_id,
                        "job_id":
                            record.job_id,
                    },
                    cause=exc,
                ) from exc

            except sqlite3.Error as exc:
                self._rollback_quietly(
                    connection
                )

                self._raise_database_error(
                    "save",
                    exc,
                )

            finally:
                connection.close()

    def _get_by_order(
        self,
        order_id: str,
    ) -> AuditResultRecord | None:
        connection = self._connect()

        try:
            row = connection.execute(
                f"""
                SELECT
                    order_id,
                    job_id,
                    product_code,
                    completed_at,
                    payload_json,
                    record_sha256
                FROM {_TABLE_NAME}
                WHERE order_id = ?
                """,
                (
                    order_id,
                ),
            ).fetchone()

            if row is None:
                return None

            return self._row_to_record(
                row
            )

        except AppError:
            raise

        except sqlite3.Error as exc:
            self._raise_database_error(
                "get_by_order",
                exc,
            )

        finally:
            connection.close()

    # ------------------------------------------------------------------
    # Serialization / integrity
    # ------------------------------------------------------------------

    @staticmethod
    def _canonical_json(
        value: Any,
    ) -> str:
        try:
            return json.dumps(
                value,
                ensure_ascii=False,
                sort_keys=True,
                separators=(
                    ",",
                    ":",
                ),
                allow_nan=False,
            )

        except (
            TypeError,
            ValueError,
        ) as exc:
            raise AppSerializationError(
                "Audit-result content cannot be serialized canonically.",
                component=_COMPONENT,
                operation="canonical_json",
                cause=exc,
            ) from exc

    @classmethod
    def _record_fingerprint(
        cls,
        record: AuditResultRecord,
    ) -> str:
        serialized = cls._canonical_json(
            record.to_dict()
        )

        return hashlib.sha256(
            serialized.encode(
                "utf-8"
            )
        ).hexdigest()

    @classmethod
    def _row_to_record(
        cls,
        row: sqlite3.Row,
    ) -> AuditResultRecord:
        try:
            payload = json.loads(
                str(
                    row[
                        "payload_json"
                    ]
                )
            )

        except (
            json.JSONDecodeError,
            TypeError,
            ValueError,
        ) as exc:
            raise AppIntegrityError(
                "Stored audit-result payload is not valid JSON.",
                component=_COMPONENT,
                operation="load_record",
                cause=exc,
            ) from exc

        if not isinstance(
            payload,
            dict,
        ):
            raise AppIntegrityError(
                "Stored audit-result payload is not a JSON object.",
                component=_COMPONENT,
                operation="load_record",
                field="payload_json",
            )

        record = AuditResultRecord(
            order_id=str(
                row["order_id"]
            ),
            job_id=str(
                row["job_id"]
            ),
            product_code=str(
                row["product_code"]
            ),
            completed_at=str(
                row["completed_at"]
            ),
            payload=payload,
        )

        expected_fingerprint = str(
            row[
                "record_sha256"
            ]
        )

        actual_fingerprint = (
            cls._record_fingerprint(
                record
            )
        )

        if not hmac.compare_digest(
            expected_fingerprint,
            actual_fingerprint,
        ):
            raise AppIntegrityError(
                "Stored audit-result record failed integrity verification.",
                component=_COMPONENT,
                operation="load_record",
                context={
                    "order_id":
                        record.order_id,
                    "job_id":
                        record.job_id,
                },
            )

        return record

    # ------------------------------------------------------------------
    # Database failure translation
    # ------------------------------------------------------------------

    @staticmethod
    def _rollback_quietly(
        connection: sqlite3.Connection,
    ) -> None:
        try:
            connection.rollback()
        except sqlite3.Error:
            pass

    @staticmethod
    def _raise_database_error(
        operation: str,
        exc: sqlite3.Error,
    ) -> NoReturn:
        message = str(
            exc
        ).casefold()

        if (
            "locked" in message
            or "busy" in message
        ):
            raise AppPortUnavailableError(
                "Audit-result database is temporarily unavailable.",
                component=_COMPONENT,
                operation=operation,
                cause=exc,
            ) from exc

        raise AppPortOperationError(
            "Audit-result database operation failed.",
            component=_COMPONENT,
            operation=operation,
            cause=exc,
        ) from exc


__all__ = [
    "SQLiteAuditResultStore",
]
