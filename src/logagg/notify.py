"""公司内部 SMTP 通知。Slice 1：stub 不发原因邮件；缺 SMTP 则 skipped。"""

from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage
from typing import Sequence

from logagg.analyze import AnalysisResult, Cause

logger = logging.getLogger(__name__)


def should_send_cause_email(*, analyzer_name: str, analysis_status: str) -> bool:
    """
    Slice 1 规则：
    - stub 只入库，不发原因邮件
    - workbuddy 且 JSON 合法（status=ok）才发
    """
    if analyzer_name == "stub" or analysis_status == "stub":
        return False
    if analysis_status != "ok":
        return False
    return analyzer_name == "workbuddy"


def build_email(
    *,
    cause: Cause,
    system: str,
    count: int,
    run_id: str,
    session_id: str | None,
    mail_from: str,
    mail_to: Sequence[str],
) -> EmailMessage:
    msg = EmailMessage()
    msg["Subject"] = f"[logagg] {system} {cause.severity} {cause.title} ({count})"
    msg["From"] = mail_from
    msg["To"] = ", ".join(mail_to)
    evidence = "\n".join(f"- {e}" for e in cause.evidence[:2]) or "- （无）"
    actions = "\n".join(f"- {a}" for a in cause.suggested_actions) or "- （无）"
    body = (
        f"摘要：{cause.summary}\n"
        f"置信度：{cause.confidence}\n"
        f"指纹计数：{count}\n"
        f"证据：\n{evidence}\n"
        f"建议：\n{actions}\n"
        f"run_id：{run_id}\n"
        f"session_id：{session_id or ''}\n"
    )
    msg.set_content(body)
    return msg


class Notifier:
    def __init__(
        self,
        *,
        host: str | None,
        port: int,
        user: str | None,
        password: str | None,
        use_tls: bool,
        mail_from: str | None,
        mail_to: list[str],
    ) -> None:
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self.use_tls = use_tls
        self.mail_from = mail_from
        self.mail_to = mail_to

    @property
    def configured(self) -> bool:
        return bool(self.host and self.mail_from and self.mail_to)

    def send(self, message: EmailMessage) -> None:
        if not self.configured:
            raise RuntimeError("SMTP 未配置")
        assert self.host and self.mail_from
        with smtplib.SMTP(self.host, self.port, timeout=30) as smtp:
            if self.use_tls:
                smtp.starttls()
            if self.user and self.password:
                smtp.login(self.user, self.password)
            smtp.send_message(message)

    def plan_notifications(
        self,
        *,
        analysis: AnalysisResult | None,
        analysis_status: str,
        run_id: str,
        fingerprint_counts: dict[str, int],
    ) -> list[dict[str, str]]:
        """
        返回写入 notifications 表的意图列表（不在此处真正发信，由 poll 决定）。
        Slice 1：stub / 无 SMTP → skipped；不调用 smtplib 发原因邮件。
        """
        intents: list[dict[str, str]] = []
        if analysis is None or not analysis.causes:
            return intents

        sendable = should_send_cause_email(
            analyzer_name=analysis.analyzer, analysis_status=analysis_status
        )
        for cause in analysis.causes:
            count = 0
            for fp in cause.fingerprints:
                count += fingerprint_counts.get(fp, 0)
            kind = "new_cause"
            if not sendable:
                status = "skipped_stub" if analysis.analyzer == "stub" else "skipped"
                detail = f"analyzer={analysis.analyzer} status={analysis_status}"
                intents.append(
                    {
                        "cause_id": cause.cause_id,
                        "log_date": analysis.log_date,
                        "notify_kind": kind,
                        "status": status,
                        "detail": detail,
                    }
                )
                continue
            if not self.configured:
                intents.append(
                    {
                        "cause_id": cause.cause_id,
                        "log_date": analysis.log_date,
                        "notify_kind": kind,
                        "status": "skipped_no_smtp",
                        "detail": "SMTP_HOST/MAIL_FROM/MAIL_TO 未配置",
                    }
                )
                continue
            # Slice 1 即使 workbuddy ok 且 SMTP 齐，也先记 intent；真正发送留给联调
            # 此处仍尝试发送，失败记 failed
            try:
                msg = build_email(
                    cause=cause,
                    system=analysis.system,
                    count=count,
                    run_id=run_id,
                    session_id=analysis.session_id,
                    mail_from=self.mail_from or "",
                    mail_to=self.mail_to,
                )
                self.send(msg)
                intents.append(
                    {
                        "cause_id": cause.cause_id,
                        "log_date": analysis.log_date,
                        "notify_kind": kind,
                        "status": "sent",
                        "detail": "",
                    }
                )
            except Exception as exc:  # noqa: BLE001
                logger.exception("发送邮件失败 cause_id=%s", cause.cause_id)
                intents.append(
                    {
                        "cause_id": cause.cause_id,
                        "log_date": analysis.log_date,
                        "notify_kind": kind,
                        "status": "failed",
                        "detail": str(exc),
                    }
                )
        return intents


def build_notifier(settings) -> Notifier:
    return Notifier(
        host=settings.smtp_host,
        port=settings.smtp_port,
        user=settings.smtp_user,
        password=settings.smtp_pass,
        use_tls=settings.smtp_tls,
        mail_from=settings.mail_from,
        mail_to=list(settings.mail_to),
    )
