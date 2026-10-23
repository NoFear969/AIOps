"""解析与指纹单元测试。"""

from __future__ import annotations

from pathlib import Path

from logagg.parse import (
    compute_fingerprint,
    is_error_start,
    is_stack_continuation,
    normalize_message,
    parse_error_events,
)

FIXTURE = Path(__file__).parent / "fixtures" / "java_stack_sample.log"


def test_error_start_detection() -> None:
    assert is_error_start("2026-10-22 ERROR boom")
    assert is_error_start("java.lang.NullPointerException: x")
    assert not is_error_start("2026-10-22 INFO ok")


def test_stack_continuation() -> None:
    assert is_stack_continuation("\tat com.example.Foo.bar(Foo.java:1)")
    assert is_stack_continuation("Caused by: java.net.SocketTimeoutException: Read timed out")
    assert not is_stack_continuation("2026-10-22 INFO next")


def test_normalize_strips_volatile_tokens() -> None:
    raw = (
        "id=550e8400-e29b-41d4-a716-446655440000 path=/data/logs/20261022 "
        "hex=0xdeadbeef count=12345"
    )
    norm = normalize_message(raw)
    assert "<uuid>" in norm
    assert "<hex>" in norm
    assert "<num>" in norm
    assert "/<date>" in norm


def test_parse_java_multiline_fixture() -> None:
    lines = FIXTURE.read_text(encoding="utf-8").splitlines()
    events = parse_error_events("checkout", lines)
    # 两条相同 TimeoutException + 一条 NPE
    assert len(events) == 3
    assert events[0].exception_type.endswith("TimeoutException")
    assert any(f.startswith("at ") for f in events[0].stack_frames)
    assert any(f.startswith("Caused by:") for f in events[0].stack_frames)
    assert events[2].exception_type.endswith("NullPointerException")


def test_same_stack_same_fingerprint() -> None:
    lines = FIXTURE.read_text(encoding="utf-8").splitlines()
    events = parse_error_events("checkout", lines)
    assert events[0].fingerprint == events[1].fingerprint
    assert events[0].fingerprint != events[2].fingerprint


def test_fingerprint_stable_api() -> None:
    a = compute_fingerprint(
        "checkout",
        "TimeoutException",
        "upstream timed out after 5000ms",
        ["at com.example.pay.ChargeService.charge(ChargeService.java:88)"],
    )
    b = compute_fingerprint(
        "checkout",
        "TimeoutException",
        "upstream timed out after 5000ms",
        ["at com.example.pay.ChargeService.charge(ChargeService.java:88)"],
    )
    assert a == b
    assert len(a) == 64
