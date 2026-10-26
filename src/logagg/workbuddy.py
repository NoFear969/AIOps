"""
WorkBuddy Managed Agents 适配器（Slice 1 占位）。

控制面接口（已核实，可调用 SDK 后接入）：
- CreateAgentSession → SessionId + EndpointSet[].Url
- DescribeMessageEventList → 回放 USER/TOOL/ASSISTANT

【禁止编造】往 EndpointSet.Url 发用户消息的 HTTP method/path/Header/SSE。
实现发消息调用前，必须用真实账号打开官方 OpenAPI / API Explorer 核对 schema。

参见：
- https://cloud.tencent.com/document/product/1831/138581
- docs/log-aggregation-plan.md §3
"""

from __future__ import annotations

from logagg.analyze import AnalysisResult, Analyzer, EvidencePack


class WorkbuddyAnalyzer(Analyzer):
    """
    生产分析器占位。

    有凭证时由 build_analyzer 加载；真正发消息 HTTP 在 OpenAPI 核对前保持未实现。
    """

    name = "workbuddy"

    def __init__(self, settings) -> None:
        self.settings = settings

    def analyze(self, evidence: EvidencePack) -> AnalysisResult:
        # TODO: 1) CreateAgentSession(AgentId[, VersionId])
        # TODO: 2) 按官方 OpenAPI 向 EndpointSet.Url 发送 evidence JSON（勿编造 schema）
        # TODO: 3) DescribeMessageEventList 取 ASSISTANT 终态并解析 §7 JSON
        raise NotImplementedError(
            "WorkbuddyAnalyzer.analyze 尚未实现：请先用官方 OpenAPI / API Explorer "
            "核对聊天接入点 HTTP schema 后再写入发消息调用。"
            f" agent_id={getattr(self.settings, 'workbuddy_agent_id', None)!r}"
            f" evidence_system={evidence.system!r}"
        )
