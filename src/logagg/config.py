"""从环境变量加载配置。不编造主机名或密钥默认值。"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv()


def _split_csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [part.strip() for part in value.split(",") if part.strip()]


@dataclass(frozen=True)
class Settings:
    """运行时配置（全部来自环境变量）。"""

    tz: ZoneInfo = field(default_factory=lambda: ZoneInfo("Asia/Shanghai"))
    systems: list[str] = field(default_factory=list)
    log_root: str = ""
    log_source: str = "local"  # local | sftp
    analyzer: str = "stub"  # stub | workbuddy

    sftp_host: str | None = None
    sftp_port: int = 22
    sftp_user: str | None = None
    sftp_key_path: str | None = None
    sftp_password: str | None = None

    mysql_host: str = "127.0.0.1"
    mysql_port: int = 3306
    mysql_user: str = "root"
    mysql_password: str = ""
    mysql_database: str = "logagg"

    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_user: str | None = None
    smtp_pass: str | None = None
    smtp_tls: bool = True
    mail_from: str | None = None
    mail_to: list[str] = field(default_factory=list)

    # WorkBuddy 占位（Slice 1 不发真实 HTTP）
    workbuddy_agent_id: str | None = None
    workbuddy_version_id: str | None = None
    workbuddy_agent_key: str | None = None
    workbuddy_secret_id: str | None = None
    workbuddy_secret_key: str | None = None

    max_new_bytes: int = 64 * 1024 * 1024
    max_new_fingerprints: int = 20

    @classmethod
    def from_env(cls) -> Settings:
        tz_name = os.getenv("TZ", "Asia/Shanghai")
        smtp_tls_raw = os.getenv("SMTP_TLS", "true").strip().lower()
        return cls(
            tz=ZoneInfo(tz_name),
            systems=_split_csv(os.getenv("SYSTEMS", "checkout")),
            log_root=os.getenv("LOG_ROOT", "./fixtures/logs"),
            log_source=os.getenv("LOG_SOURCE", "local").strip().lower(),
            analyzer=os.getenv("ANALYZER", "stub").strip().lower(),
            sftp_host=os.getenv("SFTP_HOST") or None,
            sftp_port=int(os.getenv("SFTP_PORT", "22")),
            sftp_user=os.getenv("SFTP_USER") or None,
            sftp_key_path=os.getenv("SFTP_KEY_PATH") or None,
            sftp_password=os.getenv("SFTP_PASSWORD") or None,
            mysql_host=os.getenv("MYSQL_HOST", "127.0.0.1"),
            mysql_port=int(os.getenv("MYSQL_PORT", "3306")),
            mysql_user=os.getenv("MYSQL_USER", "root"),
            mysql_password=os.getenv("MYSQL_PASSWORD", ""),
            mysql_database=os.getenv("MYSQL_DATABASE", "logagg"),
            smtp_host=os.getenv("SMTP_HOST") or None,
            smtp_port=int(os.getenv("SMTP_PORT", "587")),
            smtp_user=os.getenv("SMTP_USER") or None,
            smtp_pass=os.getenv("SMTP_PASS") or None,
            smtp_tls=smtp_tls_raw in {"1", "true", "yes", "on"},
            mail_from=os.getenv("MAIL_FROM") or None,
            mail_to=_split_csv(os.getenv("MAIL_TO")),
            workbuddy_agent_id=os.getenv("WORKBUDDY_AGENT_ID") or None,
            workbuddy_version_id=os.getenv("WORKBUDDY_VERSION_ID") or None,
            workbuddy_agent_key=os.getenv("WORKBUDDY_AGENT_KEY") or None,
            workbuddy_secret_id=os.getenv("WORKBUDDY_SECRET_ID") or None,
            workbuddy_secret_key=os.getenv("WORKBUDDY_SECRET_KEY") or None,
            max_new_bytes=int(os.getenv("MAX_NEW_BYTES", str(64 * 1024 * 1024))),
            max_new_fingerprints=int(os.getenv("MAX_NEW_FINGERPRINTS", "20")),
        )

    def has_workbuddy_creds(self) -> bool:
        """是否具备启用 WorkBuddy 适配器所需的最小凭证占位。"""
        if self.workbuddy_agent_key and self.workbuddy_agent_id:
            return True
        if (
            self.workbuddy_secret_id
            and self.workbuddy_secret_key
            and self.workbuddy_agent_id
        ):
            return True
        return False

    def has_smtp(self) -> bool:
        return bool(self.smtp_host and self.mail_from and self.mail_to)
