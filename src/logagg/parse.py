"""Java 多行堆栈解析与指纹计算。"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterable

# 日志级 ERROR，或独立的 Exception 字样 → 报错起点
_START_RE = re.compile(r"(?:\bERROR\b|Exception)", re.IGNORECASE)
# 栈帧 / 嵌套原因
_STACK_RE = re.compile(r"^\s*(?:at\s+|Caused by:)", re.IGNORECASE)
# 已在事件内时，Java 异常类型头（如 java.lang.FooException: …）视为续行，不另起事件
_EXCEPTION_HEADER_RE = re.compile(
    r"^\s*(?:[a-zA-Z_][\w$]*\.)*([A-Za-z_][\w$]*(?:Exception|Error))(?:\s*:|\s|$)"
)
# 带时间戳的新日志行（用于结束当前事件）
_NEW_LOG_LINE_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}"
)
# 常见日志时间戳前缀（尽力解析，失败则留空）
_TS_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:\s*[+-]\d{2}:?\d{2}|Z)?)"
)
_LEVEL_RE = re.compile(r"\b(ERROR|WARN|WARNING|INFO|DEBUG|FATAL)\b", re.IGNORECASE)
_EXCEPTION_TYPE_RE = re.compile(
    r"\b((?:[a-zA-Z_][\w$]*\.)*[A-Za-z_][\w$]*(?:Exception|Error))\b"
)
_UUID_RE = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
)
_HEX_RE = re.compile(r"\b0x[0-9a-fA-F]+\b|\b[0-9a-fA-F]{16,}\b")
_LONG_NUM_RE = re.compile(r"\b\d{5,}\b")
_DATE_IN_PATH_RE = re.compile(r"/\d{8}(?=/|\s|$)")
_LOGGER_RE = re.compile(
    r"\b(?:ERROR|WARN|WARNING|INFO|DEBUG|FATAL)\s+([a-zA-Z_][\w.$]*\.[a-zA-Z_][\w.$]*)\s*[-:]?\s*",
    re.IGNORECASE,
)

RAW_EXCERPT_MAX = 4 * 1024
STACK_TOP_MAX = 8


@dataclass
class ErrorEvent:
    """一条折叠后的报错事件。"""

    system: str
    message: str
    exception_type: str
    stack_frames: list[str] = field(default_factory=list)
    raw_excerpt: str = ""
    level: str = "ERROR"
    logger: str = ""
    ts: datetime | None = None
    fingerprint: str = ""

    @property
    def stack_top(self) -> list[str]:
        return self.stack_frames[:STACK_TOP_MAX]


def is_error_start(line: str) -> bool:
    return bool(_START_RE.search(line))


def is_stack_continuation(line: str) -> bool:
    """at… / Caused by: / 异常类型头，均可折入当前事件。"""
    if _STACK_RE.match(line):
        return True
    if _EXCEPTION_HEADER_RE.match(line):
        return True
    return False


def _is_new_error_event(line: str, *, in_event: bool) -> bool:
    """
    是否开启新事件。

    - 含日志级 ERROR 的行：总是新事件（若已有事件则先结束）。
    - 含 Exception 但已是当前事件续行（异常头 / 栈帧）：不另起。
    - 未在事件中的 Exception 行：作为起点（裸异常转储）。
    """
    if not is_error_start(line):
        return False
    if re.search(r"\bERROR\b", line, re.IGNORECASE):
        return True
    if in_event and is_stack_continuation(line):
        return False
    return True


def normalize_message(message: str) -> str:
    """去掉 UUID、十六进制、长数字、路径中的日期，便于指纹稳定。"""
    text = _UUID_RE.sub("<uuid>", message)
    text = _DATE_IN_PATH_RE.sub("/<date>", text)
    text = _HEX_RE.sub("<hex>", text)
    text = _LONG_NUM_RE.sub("<num>", text)
    return re.sub(r"\s+", " ", text).strip()


def compute_fingerprint(
    system: str,
    exception_type: str,
    message: str,
    stack_frames: Iterable[str],
) -> str:
    top3 = list(stack_frames)[:3]
    top3_norm = [normalize_message(frame.strip()) for frame in top3]
    payload = "|".join(
        [
            system,
            exception_type or "Unknown",
            normalize_message(message),
            *top3_norm,
        ]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _parse_ts(line: str) -> datetime | None:
    m = _TS_RE.match(line)
    if not m:
        return None
    raw = m.group(1).replace(",", ".")
    for fmt in (
        "%Y-%m-%d %H:%M:%S.%f%z",
        "%Y-%m-%d %H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%S.%f%z",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%d %H:%M:%S.%f",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S.%f",
        "%Y-%m-%dT%H:%M:%S",
    ):
        try:
            cleaned = raw
            if cleaned.endswith("Z"):
                cleaned = cleaned[:-1] + "+0000"
            # 支持 +08:00 → +0800
            if len(cleaned) > 5 and cleaned[-3] == ":" and cleaned[-6] in "+-":
                cleaned = cleaned[:-3] + cleaned[-2:]
            return datetime.strptime(cleaned, fmt)
        except ValueError:
            continue
    return None


def _extract_level(line: str) -> str:
    m = _LEVEL_RE.search(line)
    return m.group(1).upper() if m else "ERROR"


def _extract_logger(line: str) -> str:
    m = _LOGGER_RE.search(line)
    return m.group(1) if m else ""


def _extract_exception_type(lines: list[str]) -> str:
    for line in lines:
        m = _EXCEPTION_TYPE_RE.search(line)
        if m:
            return m.group(1)
    return "Unknown"


def _extract_message(start_line: str) -> str:
    # 去掉时间戳；保留 message。仅当 logger 含包名分隔符（.）时剥离。
    text = _TS_RE.sub("", start_line).strip()
    m = re.match(
        r"^(?:ERROR|WARN|WARNING|INFO|DEBUG|FATAL)\s+"
        r"(?:([a-zA-Z_][\w.$]*\.[a-zA-Z_][\w.$]*)\s+)?(?:[-:]\s*)?(.*)$",
        text,
        re.IGNORECASE,
    )
    if m:
        rest = (m.group(2) or "").strip()
        return rest or text
    text = _LEVEL_RE.sub("", text, count=1).strip()
    return text or start_line.strip()


def _build_event(system: str, lines: list[str]) -> ErrorEvent:
    start = lines[0]
    stack_frames = [ln.strip() for ln in lines[1:] if is_stack_continuation(ln)]
    message = _extract_message(start)
    exception_type = _extract_exception_type(lines)
    raw = "\n".join(lines)
    if len(raw.encode("utf-8")) > RAW_EXCERPT_MAX:
        raw = raw.encode("utf-8")[:RAW_EXCERPT_MAX].decode("utf-8", errors="replace")
    fp = compute_fingerprint(system, exception_type, message, stack_frames)
    return ErrorEvent(
        system=system,
        message=message,
        exception_type=exception_type,
        stack_frames=stack_frames,
        raw_excerpt=raw,
        level=_extract_level(start),
        logger=_extract_logger(start),
        ts=_parse_ts(start),
        fingerprint=fp,
    )


def parse_error_events(system: str, lines: Iterable[str]) -> list[ErrorEvent]:
    """从完整行序列中抽出 ERROR/Exception 事件，折叠 at... / Caused by:。"""
    events: list[ErrorEvent] = []
    current: list[str] | None = None

    def flush() -> None:
        nonlocal current
        if current:
            events.append(_build_event(system, current))
            current = None

    for line in lines:
        # 保留行尾之外的内容；调用方应已去掉 \n
        in_event = current is not None
        if _is_new_error_event(line, in_event=in_event):
            flush()
            current = [line]
            continue
        if current is not None and is_stack_continuation(line):
            current.append(line)
            continue
        # 非栈帧行结束当前事件（例如 INFO 行）
        if current is not None and _NEW_LOG_LINE_RE.match(line):
            flush()
            continue
        if current is not None and not line.strip():
            # 空行保留在事件内意义不大，直接结束
            flush()
            continue
        flush()

    flush()
    return events
