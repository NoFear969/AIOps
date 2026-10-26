"""Analyzer 接口与结果模型。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Literal


AnalysisStatus = Literal[
    "ok",
    "failed",
    "skipped_no_creds",
    "stub",
]


@dataclass
class Cause:
    cause_id: str
    title: str
    summary: str
    severity: str = "medium"
    confidence: float = 0.0
    fingerprints: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    suggested_actions: list[str] = field(default_factory=list)


@dataclass
class AnalysisResult:
    system: str
    log_date: str
    analyzer: str  # stub | workbuddy
    causes: list[Cause] = field(default_factory=list)
    unresolved: list[str] = field(default_factory=list)
    session_id: str | None = None
    status: AnalysisStatus = "ok"
    raw: dict[str, Any] = field(default_factory=dict)

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "system": self.system,
            "log_date": self.log_date,
            "analyzer": self.analyzer,
            "session_id": self.session_id,
            "status": self.status,
            "causes": [
                {
                    "cause_id": c.cause_id,
                    "title": c.title,
                    "summary": c.summary,
                    "severity": c.severity,
                    "confidence": c.confidence,
                    "fingerprints": list(c.fingerprints),
                    "evidence": list(c.evidence),
                    "suggested_actions": list(c.suggested_actions),
                }
                for c in self.causes
            ],
            "unresolved": list(self.unresolved),
        }


@dataclass
class ClusterEvidence:
    fingerprint: str
    count: int
    first_seen_at: str | None
    last_seen_at: str | None
    exception_type: str
    sample_messages: list[str]
    sample_stacks: list[str]


@dataclass
class EvidencePack:
    run_id: str
    system: str
    log_date: str
    window: dict[str, int]
    clusters: list[ClusterEvidence]

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "system": self.system,
            "log_date": self.log_date,
            "window": dict(self.window),
            "clusters": [
                {
                    "fingerprint": c.fingerprint,
                    "count": c.count,
                    "first_seen_at": c.first_seen_at,
                    "last_seen_at": c.last_seen_at,
                    "exception_type": c.exception_type,
                    "sample_messages": list(c.sample_messages),
                    "sample_stacks": list(c.sample_stacks),
                }
                for c in self.clusters
            ],
        }


class Analyzer(ABC):
    """原因分析器接口。"""

    name: str = "analyzer"

    @abstractmethod
    def analyze(self, evidence: EvidencePack) -> AnalysisResult:
        raise NotImplementedError


def build_analyzer(settings) -> Analyzer | None:
    """
    按 ANALYZER=stub|workbuddy 构造分析器。

    workbuddy 且缺凭证时返回 None，由 poll 记 skipped_no_creds。
    """
    mode = (settings.analyzer or "stub").lower()
    if mode == "stub":
        from logagg.stub import StubAnalyzer

        return StubAnalyzer()
    if mode == "workbuddy":
        if not settings.has_workbuddy_creds():
            return None
        from logagg.workbuddy import WorkbuddyAnalyzer

        return WorkbuddyAnalyzer(settings)
    raise ValueError(f"未知 ANALYZER={settings.analyzer!r}，期望 stub|workbuddy")
