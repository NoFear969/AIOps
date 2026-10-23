"""Slice 1 默认 StubAnalyzer：可入库的占位分析，不发原因邮件。"""

from __future__ import annotations

from logagg.analyze import AnalysisResult, Analyzer, Cause, EvidencePack


class StubAnalyzer(Analyzer):
    """根据 fingerprint 派生 cause_id，标明 stub，置信度低。"""

    name = "stub"

    def analyze(self, evidence: EvidencePack) -> AnalysisResult:
        causes: list[Cause] = []
        for cluster in evidence.clusters:
            short = cluster.fingerprint[:12]
            cause_id = f"stub-{evidence.system}-{short}"
            msg = cluster.sample_messages[0] if cluster.sample_messages else ""
            stack = cluster.sample_stacks[0] if cluster.sample_stacks else ""
            causes.append(
                Cause(
                    cause_id=cause_id,
                    title=f"[stub] {cluster.exception_type}",
                    summary=(
                        f"Stub 占位分析：system={evidence.system} "
                        f"type={cluster.exception_type} count={cluster.count}. "
                        f"凭证就绪后由 WorkBuddy 覆盖。"
                    ),
                    severity="low",
                    confidence=0.1,
                    fingerprints=[cluster.fingerprint],
                    evidence=[
                        f"{cluster.exception_type} ×{cluster.count}",
                        msg[:200] if msg else stack[:200],
                    ],
                    suggested_actions=["等待 WorkBuddy 凭证后重新分析"],
                )
            )

        result = AnalysisResult(
            system=evidence.system,
            log_date=evidence.log_date,
            analyzer="stub",
            causes=causes,
            unresolved=[],
            session_id=None,
            status="stub",
        )
        result.raw = result.to_json_dict()
        return result
