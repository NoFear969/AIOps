"""水位增量与 last_partial 单元测试（本地 LogSource，无需 MySQL/SFTP）。"""

from __future__ import annotations

from pathlib import Path

from logagg.parse import parse_error_events
from logagg.source import LocalLogSource, Watermark, apply_increment


def test_apply_increment_keeps_partial_and_advances_file_offset() -> None:
    wm = Watermark(system="checkout", log_date="20261022", byte_offset=0)
    chunk = b"line1\nline2\npartial"
    result = apply_increment(wm, chunk, remote_size=len(chunk), path="/tmp/x")
    assert result.lines == ["line1", "line2"]
    assert result.watermark.last_partial == b"partial"
    # 文件偏移覆盖已读字节；半行只留在 last_partial，下轮不再 seek 回去
    assert result.watermark.byte_offset == len(chunk)


def test_last_partial_joins_across_rounds() -> None:
    wm = Watermark(system="checkout", log_date="20261022", byte_offset=0)
    part1 = b"2026-10-22 ERROR hello "
    r1 = apply_increment(wm, part1, remote_size=len(part1))
    assert r1.lines == []
    assert r1.watermark.byte_offset == len(part1)
    assert r1.watermark.last_partial == part1

    more = b"world\nat com.example.Foo.bar(Foo.java:1)\n"
    remote_size = len(part1) + len(more)
    r2 = apply_increment(r1.watermark, more, remote_size=remote_size)
    assert r2.lines == [
        "2026-10-22 ERROR hello world",
        "at com.example.Foo.bar(Foo.java:1)",
    ]
    assert r2.watermark.last_partial == b""
    assert r2.watermark.byte_offset == remote_size

    events = parse_error_events("checkout", r2.lines)
    assert len(events) == 1
    assert "hello world" in events[0].message


def test_no_new_data_skips() -> None:
    wm = Watermark(system="checkout", log_date="20261022", byte_offset=100)
    r = apply_increment(wm, b"", remote_size=100)
    assert r.skipped
    assert r.reason == "no_new_data"


def test_truncate_resets_watermark() -> None:
    wm = Watermark(
        system="checkout",
        log_date="20261022",
        byte_offset=500,
        last_partial=b"abc",
    )
    r = apply_increment(wm, b"", remote_size=10)
    assert r.watermark.reset
    assert r.watermark.byte_offset == 0
    assert r.watermark.last_partial == b""
    assert r.reason == "watermark_reset"


def test_local_source_only_reads_new_bytes(tmp_path: Path) -> None:
    """验收：append ERROR 后下一轮只读新字节，byte_offset 前进。"""
    log_root = tmp_path
    system = "checkout"
    log_date = "20261022"
    path = log_root / f"{system}-{log_date}"

    first = (
        b"2026-10-22 07:00:00 INFO boot\n"
        b"2026-10-22 07:01:00 ERROR first failure\n"
        b"java.lang.RuntimeException: first\n"
        b"\tat com.example.A.a(A.java:1)\n"
    )
    path.write_bytes(first)

    src = LocalLogSource(str(log_root))
    wm0 = Watermark(system=system, log_date=log_date)
    r1 = src.read_increment(system, log_date, wm0)
    assert not r1.skipped
    assert r1.watermark.byte_offset == len(first)
    events1 = parse_error_events(system, r1.lines)
    assert len(events1) == 1
    assert "first" in events1[0].message

    # 无新数据
    r_skip = src.read_increment(system, log_date, r1.watermark)
    assert r_skip.skipped
    assert r_skip.watermark.byte_offset == len(first)

    # append 新 ERROR
    second = (
        b"2026-10-22 07:05:00 ERROR second failure\n"
        b"java.lang.IllegalStateException: second\n"
        b"\tat com.example.B.b(B.java:2)\n"
    )
    with path.open("ab") as f:
        f.write(second)

    r2 = src.read_increment(system, log_date, r1.watermark)
    assert r2.watermark.byte_offset == len(first) + len(second)
    # 只应看到第二段相关行，不应重扫 first failure
    assert all("first" not in line for line in r2.lines)
    events2 = parse_error_events(system, r2.lines)
    assert len(events2) == 1
    assert "second" in events2[0].message


def test_local_source_partial_line_across_polls(tmp_path: Path) -> None:
    log_root = tmp_path
    system = "pay"
    log_date = "20261022"
    path = log_root / f"{system}-{log_date}"
    src = LocalLogSource(str(log_root))

    path.write_bytes(b"2026-10-22 ERROR incomplete")
    r1 = src.read_increment(system, log_date, Watermark(system=system, log_date=log_date))
    assert r1.lines == []
    assert r1.watermark.last_partial.startswith(b"2026-10-22 ERROR incomplete")
    assert r1.watermark.byte_offset == path.stat().st_size

    with path.open("ab") as f:
        f.write(b" message\n\tat com.x.Y.z(Y.java:9)\n")

    r2 = src.read_increment(system, log_date, r1.watermark)
    assert "incomplete message" in r2.lines[0]
    events = parse_error_events(system, r2.lines)
    assert len(events) == 1
