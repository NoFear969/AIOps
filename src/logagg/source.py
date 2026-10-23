"""日志源：本地目录（测试）与 SFTP（生产）增量读取。"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Protocol
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)


@dataclass
class Watermark:
    """(system, log_date) 上的字节水位。"""

    system: str
    log_date: str  # YYYYMMDD
    path: str = ""
    remote_size: int = 0
    mtime: float | None = None
    byte_offset: int = 0
    last_partial: bytes = b""
    reset: bool = False  # 本轮是否因截断归零

    def copy(self) -> Watermark:
        return Watermark(
            system=self.system,
            log_date=self.log_date,
            path=self.path,
            remote_size=self.remote_size,
            mtime=self.mtime,
            byte_offset=self.byte_offset,
            last_partial=self.last_partial,
            reset=self.reset,
        )


@dataclass
class IncrementalRead:
    """一次增量读的结果。"""

    lines: list[str] = field(default_factory=list)
    watermark: Watermark = field(default_factory=lambda: Watermark(system="", log_date=""))
    skipped: bool = False
    reason: str = ""


def log_path_for(log_root: str, system: str, log_date: str) -> str:
    """拼装 `{LOG_ROOT}/{system}-{YYYYMMDD}`。"""
    return str(Path(log_root) / f"{system}-{log_date}")


def today_log_date(tz: ZoneInfo | None = None) -> str:
    """按给定时区返回今天的 YYYYMMDD（默认 Asia/Shanghai）。"""
    zone = tz or ZoneInfo("Asia/Shanghai")
    return datetime.now(zone).strftime("%Y%m%d")


def decode_utf8_replacement(data: bytes) -> str:
    return data.decode("utf-8", errors="replace")


def apply_increment(
    watermark: Watermark,
    new_bytes: bytes,
    *,
    remote_size: int,
    mtime: float | None = None,
    path: str = "",
) -> IncrementalRead:
    """
    纯函数：把 last_partial + 新字节切成完整行，更新水位。

    - 只提交完整行（以 \\n 结尾）
    - 半行写入 last_partial，供下轮拼接
    - byte_offset **按已从文件读出的字节前进**（含半行），下轮 seek 不再重读
    """
    wm = watermark.copy()
    wm.path = path or wm.path
    wm.remote_size = remote_size
    wm.mtime = mtime
    wm.reset = False

    if remote_size < wm.byte_offset:
        # 截断/替换：归零；调用方应在归零后重读
        wm.byte_offset = 0
        wm.last_partial = b""
        wm.reset = True
        return IncrementalRead(lines=[], watermark=wm, skipped=False, reason="watermark_reset")

    if not new_bytes and remote_size == wm.byte_offset:
        return IncrementalRead(lines=[], watermark=wm, skipped=True, reason="no_new_data")

    buf = wm.last_partial + new_bytes
    if not buf:
        return IncrementalRead(lines=[], watermark=wm, skipped=True, reason="empty")

    parts = buf.split(b"\n")
    if buf.endswith(b"\n"):
        complete = parts[:-1]
        partial = b""
    else:
        complete = parts[:-1]
        partial = parts[-1]

    lines = [decode_utf8_replacement(p) for p in complete]
    # 文件侧水位：本轮读到的新字节全部消费，避免下轮 seek 重读半行
    wm.last_partial = partial
    wm.byte_offset = wm.byte_offset + len(new_bytes)

    return IncrementalRead(lines=lines, watermark=wm, skipped=False, reason="ok")


class LogSource(ABC):
    """日志源抽象。"""

    @abstractmethod
    def read_increment(
        self,
        system: str,
        log_date: str,
        watermark: Watermark,
        *,
        max_new_bytes: int = 64 * 1024 * 1024,
    ) -> IncrementalRead:
        raise NotImplementedError


class LocalLogSource(LogSource):
    """本地目录源，便于 CI / fixture 追加 ERROR 行验证水位。"""

    def __init__(self, log_root: str) -> None:
        self.log_root = log_root

    def read_increment(
        self,
        system: str,
        log_date: str,
        watermark: Watermark,
        *,
        max_new_bytes: int = 64 * 1024 * 1024,
    ) -> IncrementalRead:
        path = log_path_for(self.log_root, system, log_date)
        p = Path(path)
        if not p.exists():
            wm = watermark.copy()
            wm.path = path
            return IncrementalRead(
                lines=[], watermark=wm, skipped=True, reason="file_missing"
            )

        stat = p.stat()
        remote_size = stat.st_size
        mtime = stat.st_mtime
        wm = watermark.copy()
        wm.path = path

        if remote_size < wm.byte_offset:
            logger.warning(
                "本地文件截断，重置水位 system=%s date=%s size=%s offset=%s",
                system,
                log_date,
                remote_size,
                wm.byte_offset,
            )
            wm.byte_offset = 0
            wm.last_partial = b""
            wm.reset = True

        if remote_size == wm.byte_offset and not wm.reset:
            wm.remote_size = remote_size
            wm.mtime = mtime
            return IncrementalRead(
                lines=[], watermark=wm, skipped=True, reason="no_new_data"
            )

        to_read = min(remote_size - wm.byte_offset, max_new_bytes)
        with p.open("rb") as f:
            f.seek(wm.byte_offset)
            new_bytes = f.read(to_read)

        return apply_increment(
            wm, new_bytes, remote_size=remote_size, mtime=mtime, path=path
        )


class SFTPLogSource(LogSource):
    """生产路径：经 paramiko SFTP 增量读。"""

    def __init__(
        self,
        *,
        host: str,
        port: int,
        username: str,
        log_root: str,
        key_path: str | None = None,
        password: str | None = None,
    ) -> None:
        if not host or not username:
            raise ValueError("SFTP_HOST 与 SFTP_USER 必填")
        if not key_path and password is None:
            raise ValueError("需要 SFTP_KEY_PATH 或 SFTP_PASSWORD")
        self.host = host
        self.port = port
        self.username = username
        self.log_root = log_root
        self.key_path = key_path
        self.password = password

    def _connect(self):
        import paramiko

        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        connect_kwargs: dict = {
            "hostname": self.host,
            "port": self.port,
            "username": self.username,
            "allow_agent": False,
            "look_for_keys": False,
        }
        if self.key_path:
            connect_kwargs["key_filename"] = self.key_path
        if self.password is not None:
            connect_kwargs["password"] = self.password
        client.connect(**connect_kwargs)
        return client

    def read_increment(
        self,
        system: str,
        log_date: str,
        watermark: Watermark,
        *,
        max_new_bytes: int = 64 * 1024 * 1024,
    ) -> IncrementalRead:
        path = log_path_for(self.log_root, system, log_date)
        wm = watermark.copy()
        wm.path = path

        client = self._connect()
        try:
            sftp = client.open_sftp()
            try:
                try:
                    attr = sftp.stat(path)
                except FileNotFoundError:
                    return IncrementalRead(
                        lines=[], watermark=wm, skipped=True, reason="file_missing"
                    )
                except OSError as exc:
                    # paramiko 对缺失文件常抛 IOError
                    if getattr(exc, "errno", None) == 2:
                        return IncrementalRead(
                            lines=[], watermark=wm, skipped=True, reason="file_missing"
                        )
                    raise

                remote_size = int(attr.st_size)
                mtime = float(attr.st_mtime) if attr.st_mtime is not None else None

                if remote_size < wm.byte_offset:
                    logger.warning(
                        "远端文件截断，重置水位 system=%s date=%s size=%s offset=%s",
                        system,
                        log_date,
                        remote_size,
                        wm.byte_offset,
                    )
                    wm.byte_offset = 0
                    wm.last_partial = b""
                    wm.reset = True

                if remote_size == wm.byte_offset and not wm.reset:
                    wm.remote_size = remote_size
                    wm.mtime = mtime
                    return IncrementalRead(
                        lines=[], watermark=wm, skipped=True, reason="no_new_data"
                    )

                to_read = min(remote_size - wm.byte_offset, max_new_bytes)
                with sftp.open(path, "rb") as remote:
                    remote.seek(wm.byte_offset)
                    new_bytes = remote.read(to_read)

                return apply_increment(
                    wm, new_bytes, remote_size=remote_size, mtime=mtime, path=path
                )
            finally:
                sftp.close()
        finally:
            client.close()


def build_log_source(settings) -> LogSource:
    """按 LOG_SOURCE=local|sftp 构造实现。"""
    mode = (settings.log_source or "local").lower()
    if mode == "local":
        return LocalLogSource(settings.log_root)
    if mode == "sftp":
        return SFTPLogSource(
            host=settings.sftp_host or "",
            port=settings.sftp_port,
            username=settings.sftp_user or "",
            log_root=settings.log_root,
            key_path=settings.sftp_key_path,
            password=settings.sftp_password,
        )
    raise ValueError(f"未知 LOG_SOURCE={settings.log_source!r}，期望 local|sftp")


class _SettingsProto(Protocol):
    log_source: str
    log_root: str
    sftp_host: str | None
    sftp_port: int
    sftp_user: str | None
    sftp_key_path: str | None
    sftp_password: str | None
