"""Stub 分析与通知策略。"""

from __future__ import annotations

from logagg.analyze import ClusterEvidence, EvidencePack, build_analyzer
from logagg.config import Settings
from logagg.notify import Notifier, should_send_cause_email
from logagg.stub import StubAnalyzer


def _pack() -> EvidencePack:
    return EvidencePack(
        run_id="2026-10-22T06:30:00Z",
        system="checkout",
        log_date="20261022",
        window={"from_offset": 0, "to_offset": 100},
        clusters=[
            ClusterEvidence(
                fingerprint="a" * 64,
                count=3,
                first_seen_at=None,
                last_seen_at=None,
                exception_type="TimeoutException",
                sample_messages=["upstream timed out"],
                sample_stacks=["at com.example.pay.ChargeService.charge(ChargeService.java:88)"],
            )
        ],
    )


def test_stub_analyzer_produces_cause() -> None:
    result = StubAnalyzer().analyze(_pack())
    assert result.analyzer == "stub"
    assert result.session_id is None
    assert len(result.causes) == 1
    assert result.causes[0].cause_id.startswith("stub-checkout-")
    assert result.causes[0].confidence <= 0.2


def test_stub_does_not_send_cause_email() -> None:
    assert not should_send_cause_email(analyzer_name="stub", analysis_status="stub")
    assert should_send_cause_email(analyzer_name="workbuddy", analysis_status="ok")
    assert not should_send_cause_email(
        analyzer_name="workbuddy", analysis_status="skipped_no_creds"
    )


def test_notifier_skips_without_smtp() -> None:
    notifier = Notifier(
        host=None,
        port=587,
        user=None,
        password=None,
        use_tls=True,
        mail_from=None,
        mail_to=[],
    )
    result = StubAnalyzer().analyze(_pack())
    intents = notifier.plan_notifications(
        analysis=result,
        analysis_status="stub",
        run_id="r1",
        fingerprint_counts={"a" * 64: 3},
    )
    assert intents
    assert intents[0]["status"] == "skipped_stub"


def test_build_analyzer_workbuddy_without_creds_returns_none() -> None:
    settings = Settings(
        analyzer="workbuddy",
        workbuddy_agent_id=None,
        workbuddy_agent_key=None,
    )
    assert build_analyzer(settings) is None
