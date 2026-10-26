"""
定时入口：cron / systemd 每约 5 分钟执行一次。

  uv run python -m logagg.poll
  uv run python -m logagg.poll --dry-run   # 不写 MySQL，只打印解析结果
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections import defaultdict
from datetime import datetime, timezone

from logagg.analyze import (
    AnalysisResult,
    ClusterEvidence,
    EvidencePack,
    build_analyzer,
)
from logagg.config import Settings
from logagg.notify import build_notifier
from logagg.parse import ErrorEvent, parse_error_events
from logagg.source import Watermark, build_log_source, today_log_date
from logagg.store import Store, build_store

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("logagg.poll")


def _cluster_events(events: list[ErrorEvent]) -> dict[str, list[ErrorEvent]]:
    grouped: dict[str, list[ErrorEvent]] = defaultdict(list)
    for ev in events:
        grouped[ev.fingerprint].append(ev)
    return grouped


def _build_evidence(
    *,
    run_id: str,
    system: str,
    log_date: str,
    from_offset: int,
    to_offset: int,
    events: list[ErrorEvent],
    max_fingerprints: int,
) -> tuple[EvidencePack, int]:
    grouped = _cluster_events(events)
    overflow = max(0, len(grouped) - max_fingerprints)
    clusters: list[ClusterEvidence] = []
    for fp, items in list(grouped.items())[:max_fingerprints]:
        first = items[0]
        last = items[-1]
        stacks = []
        for it in items:
            stacks.extend(it.stack_top[:1])
        clusters.append(
            ClusterEvidence(
                fingerprint=fp,
                count=len(items),
                first_seen_at=first.ts.isoformat() if first.ts else None,
                last_seen_at=last.ts.isoformat() if last.ts else None,
                exception_type=first.exception_type,
                sample_messages=[first.message],
                sample_stacks=stacks[:3],
            )
        )
    pack = EvidencePack(
        run_id=run_id,
        system=system,
        log_date=log_date,
        window={"from_offset": from_offset, "to_offset": to_offset},
        clusters=clusters,
    )
    return pack, overflow


def _analyze(
    settings: Settings,
    evidence: EvidencePack,
) -> tuple[AnalysisResult | None, str]:
    """返回 (result, analysis_status)。"""
    mode = settings.analyzer
    if mode == "workbuddy" and not settings.has_workbuddy_creds():
        logger.warning(
            "ANALYZER=workbuddy 但缺少凭证，analysis_status=skipped_no_creds"
        )
        return None, "skipped_no_creds"

    analyzer = build_analyzer(settings)
    if analyzer is None:
        return None, "skipped_no_creds"

    try:
        result = analyzer.analyze(evidence)
        status = result.status if result.status else ("stub" if mode == "stub" else "ok")
        return result, status
    except NotImplementedError as exc:
        logger.error("分析器未实现: %s", exc)
        return None, "failed"
    except Exception:  # noqa: BLE001
        logger.exception("分析失败")
        return None, "failed"


def process_system(
    *,
    settings: Settings,
    system: str,
    log_date: str,
    store: Store | None,
    dry_run: bool,
) -> dict:
    """处理单个 system 当天文件，返回摘要。"""
    run_id = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    source = build_log_source(settings)
    notifier = build_notifier(settings)

    if dry_run or store is None:
        wm = Watermark(system=system, log_date=log_date)
    else:
        with store.session() as conn:
            wm = store.get_watermark(conn, system, log_date)

    from_offset = wm.byte_offset
    try:
        inc = source.read_increment(
            system, log_date, wm, max_new_bytes=settings.max_new_bytes
        )
    except Exception:  # noqa: BLE001 — 单系统失败不影响其他
        logger.exception("读取失败 system=%s", system)
        return {"system": system, "ok": False, "error": "read_failed"}

    if inc.skipped and not inc.lines:
        logger.info(
            "跳过 system=%s reason=%s offset=%s",
            system,
            inc.reason,
            inc.watermark.byte_offset,
        )
        return {
            "system": system,
            "ok": True,
            "skipped": True,
            "reason": inc.reason,
            "byte_offset": inc.watermark.byte_offset,
        }

    events = parse_error_events(system, inc.lines)
    evidence, overflow = _build_evidence(
        run_id=run_id,
        system=system,
        log_date=log_date,
        from_offset=from_offset,
        to_offset=inc.watermark.byte_offset,
        events=events,
        max_fingerprints=settings.max_new_fingerprints,
    )

    analysis: AnalysisResult | None = None
    analysis_status = "skipped"
    if evidence.clusters:
        analysis, analysis_status = _analyze(settings, evidence)

    fp_counts = {c.fingerprint: c.count for c in evidence.clusters}
    # Slice 1：stub 不发原因邮件；缺 SMTP → skipped_no_smtp
    notify_intents = notifier.plan_notifications(
        analysis=analysis,
        analysis_status=analysis_status,
        run_id=run_id,
        fingerprint_counts=fp_counts,
    )

    summary = {
        "system": system,
        "ok": True,
        "lines": len(inc.lines),
        "events": len(events),
        "fingerprints": len(evidence.clusters),
        "overflow": overflow,
        "from_offset": from_offset,
        "byte_offset": inc.watermark.byte_offset,
        "last_partial_len": len(inc.watermark.last_partial),
        "analysis_status": analysis_status,
        "notify": [i.get("status") for i in notify_intents],
        "dry_run": dry_run,
    }

    if dry_run or store is None:
        logger.info("dry-run 摘要: %s", summary)
        for ev in events[:5]:
            logger.info(
                "  event fp=%s type=%s msg=%s",
                ev.fingerprint[:12],
                ev.exception_type,
                ev.message[:80],
            )
        return summary

    # 即使分析 skipped/failed，水位仍前进，避免死循环重放
    store.commit_poll_batch(
        events=events,
        log_date=log_date,
        watermark=inc.watermark,
        analysis=analysis,
        analysis_status=analysis_status,
        notify_intents=notify_intents,
    )
    logger.info("提交完成: %s", summary)
    return summary


def run_once(settings: Settings | None = None, *, dry_run: bool = False) -> int:
    settings = settings or Settings.from_env()
    log_date = today_log_date(settings.tz)
    store: Store | None = None
    if not dry_run:
        store = build_store(settings)

    logger.info(
        "开始轮询 date=%s systems=%s source=%s analyzer=%s dry_run=%s",
        log_date,
        settings.systems,
        settings.log_source,
        settings.analyzer,
        dry_run,
    )

    exit_code = 0
    for system in settings.systems:
        result = process_system(
            settings=settings,
            system=system,
            log_date=log_date,
            store=store,
            dry_run=dry_run,
        )
        if not result.get("ok"):
            exit_code = 1
    return exit_code


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="logagg 增量轮询（Slice 1）")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="不写 MySQL，仅读取并解析（适合本地 fixture）",
    )
    args = parser.parse_args(argv)
    return run_once(dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
