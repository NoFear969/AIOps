"""本机 MySQL 存储：水位、事件、cluster、分析、通知。"""

from __future__ import annotations

import json
import logging
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterator

import pymysql
from pymysql.cursors import DictCursor

from logagg.analyze import AnalysisResult
from logagg.parse import ErrorEvent
from logagg.source import Watermark

logger = logging.getLogger(__name__)


@dataclass
class ClusterState:
    fingerprint: str
    system: str
    log_date: str
    count: int
    first_seen_at: datetime | None
    last_seen_at: datetime | None
    exception_type: str
    sample_message: str
    sample_stack: str


class Store:
    """MySQL 访问封装。"""

    def __init__(
        self,
        *,
        host: str,
        port: int,
        user: str,
        password: str,
        database: str,
    ) -> None:
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self.database = database

    def connect(self):
        return pymysql.connect(
            host=self.host,
            port=self.port,
            user=self.user,
            password=self.password,
            database=self.database,
            charset="utf8mb4",
            cursorclass=DictCursor,
            autocommit=False,
        )

    @contextmanager
    def session(self) -> Iterator[Any]:
        conn = self.connect()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def get_watermark(self, conn, system: str, log_date: str) -> Watermark:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT system, log_date, path, remote_size, mtime, byte_offset, last_partial
                FROM watermarks
                WHERE system=%s AND log_date=%s
                """,
                (system, log_date),
            )
            row = cur.fetchone()
        if not row:
            return Watermark(system=system, log_date=log_date)
        partial = row["last_partial"] or b""
        if isinstance(partial, str):
            partial = partial.encode("utf-8", errors="replace")
        return Watermark(
            system=row["system"],
            log_date=row["log_date"],
            path=row["path"] or "",
            remote_size=int(row["remote_size"] or 0),
            mtime=float(row["mtime"]) if row["mtime"] is not None else None,
            byte_offset=int(row["byte_offset"] or 0),
            last_partial=partial,
        )

    def upsert_watermark(self, conn, wm: Watermark) -> None:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO watermarks
                    (system, log_date, path, remote_size, mtime, byte_offset, last_partial, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, UTC_TIMESTAMP())
                ON DUPLICATE KEY UPDATE
                    path=VALUES(path),
                    remote_size=VALUES(remote_size),
                    mtime=VALUES(mtime),
                    byte_offset=VALUES(byte_offset),
                    last_partial=VALUES(last_partial),
                    updated_at=UTC_TIMESTAMP()
                """,
                (
                    wm.system,
                    wm.log_date,
                    wm.path,
                    wm.remote_size,
                    wm.mtime,
                    wm.byte_offset,
                    wm.last_partial,
                ),
            )

    def get_cluster(
        self, conn, system: str, log_date: str, fingerprint: str
    ) -> ClusterState | None:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT fingerprint, system, log_date, count, first_seen_at, last_seen_at,
                       exception_type, sample_message, sample_stack
                FROM error_clusters
                WHERE system=%s AND log_date=%s AND fingerprint=%s
                """,
                (system, log_date, fingerprint),
            )
            row = cur.fetchone()
        if not row:
            return None
        return ClusterState(**row)

    def upsert_event_and_cluster(self, conn, event: ErrorEvent, log_date: str) -> ClusterState:
        now = event.ts or datetime.now(timezone.utc)
        stack_top = "\n".join(event.stack_top)
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO error_events
                    (system, log_date, fingerprint, exception_type, message, level, logger,
                     stack_top, raw_excerpt, event_ts, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, UTC_TIMESTAMP())
                """,
                (
                    event.system,
                    log_date,
                    event.fingerprint,
                    event.exception_type,
                    event.message[:2000],
                    event.level,
                    event.logger[:255],
                    stack_top[:4000],
                    event.raw_excerpt,
                    now,
                ),
            )
            cur.execute(
                """
                INSERT INTO error_clusters
                    (system, log_date, fingerprint, count, first_seen_at, last_seen_at,
                     exception_type, sample_message, sample_stack, updated_at)
                VALUES (%s, %s, %s, 1, %s, %s, %s, %s, %s, UTC_TIMESTAMP())
                ON DUPLICATE KEY UPDATE
                    count = count + 1,
                    last_seen_at = VALUES(last_seen_at),
                    updated_at = UTC_TIMESTAMP()
                """,
                (
                    event.system,
                    log_date,
                    event.fingerprint,
                    now,
                    now,
                    event.exception_type,
                    event.message[:2000],
                    stack_top[:4000],
                ),
            )
        state = self.get_cluster(conn, event.system, log_date, event.fingerprint)
        assert state is not None
        return state

    def save_analysis(
        self,
        conn,
        result: AnalysisResult,
        *,
        analysis_status: str,
    ) -> None:
        raw_json = json.dumps(result.raw or result.to_json_dict(), ensure_ascii=False)
        with conn.cursor() as cur:
            for cause in result.causes:
                cur.execute(
                    """
                    INSERT INTO analyses
                        (cause_id, system, log_date, title, summary, severity, confidence,
                         analyzer, session_id, analysis_status, raw_json, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, UTC_TIMESTAMP())
                    ON DUPLICATE KEY UPDATE
                        title=VALUES(title),
                        summary=VALUES(summary),
                        severity=VALUES(severity),
                        confidence=VALUES(confidence),
                        analyzer=VALUES(analyzer),
                        session_id=VALUES(session_id),
                        analysis_status=VALUES(analysis_status),
                        raw_json=VALUES(raw_json),
                        updated_at=UTC_TIMESTAMP()
                    """,
                    (
                        cause.cause_id,
                        result.system,
                        result.log_date,
                        cause.title[:512],
                        cause.summary,
                        cause.severity,
                        cause.confidence,
                        result.analyzer,
                        result.session_id,
                        analysis_status,
                        raw_json,
                    ),
                )
                for fp in cause.fingerprints:
                    cur.execute(
                        """
                        INSERT INTO cluster_causes (fingerprint, cause_id, system, log_date)
                        VALUES (%s, %s, %s, %s)
                        ON DUPLICATE KEY UPDATE cause_id=VALUES(cause_id)
                        """,
                        (fp, cause.cause_id, result.system, result.log_date),
                    )

    def record_notification_intent(
        self,
        conn,
        *,
        cause_id: str,
        log_date: str,
        notify_kind: str,
        status: str,
        detail: str = "",
    ) -> None:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO notifications
                    (cause_id, log_date, notify_kind, status, detail, created_at, sent_at)
                VALUES (%s, %s, %s, %s, %s, UTC_TIMESTAMP(),
                        CASE WHEN %s='sent' THEN UTC_TIMESTAMP() ELSE NULL END)
                ON DUPLICATE KEY UPDATE
                    status=VALUES(status),
                    detail=VALUES(detail),
                    sent_at=CASE WHEN VALUES(status)='sent' THEN UTC_TIMESTAMP() ELSE sent_at END
                """,
                (cause_id, log_date, notify_kind, status, detail[:1000], status),
            )

    def commit_poll_batch(
        self,
        *,
        events: list[ErrorEvent],
        log_date: str,
        watermark: Watermark,
        analysis: AnalysisResult | None,
        analysis_status: str,
        notify_intents: list[dict[str, str]],
    ) -> list[ClusterState]:
        """
        提交顺序：业务表 → 水位 → 通知意图。
        水位后写：业务失败时下轮可重读同一字节窗口。
        """
        clusters: list[ClusterState] = []
        with self.session() as conn:
            for event in events:
                clusters.append(self.upsert_event_and_cluster(conn, event, log_date))
            if analysis is not None and analysis.causes:
                self.save_analysis(conn, analysis, analysis_status=analysis_status)
            self.upsert_watermark(conn, watermark)
            for intent in notify_intents:
                self.record_notification_intent(conn, **intent)
        return clusters


def build_store(settings) -> Store:
    return Store(
        host=settings.mysql_host,
        port=settings.mysql_port,
        user=settings.mysql_user,
        password=settings.mysql_password,
        database=settings.mysql_database,
    )
