"""单轮计数与事件投影（strategy-factor-discovery §5.2 / §4.2）—— 纯函数，无状态。

自 ``runtime.py`` 拆出（该文件超 800 行清洁度上限）：本模块只负责「一轮跑完之后，把计数
折成落库的 ``stats_json`` 与下发给前端的事件」。所有「如实上报」的纪律都收口在这里——
截断了多少条、是否超过间隔、是否触发 FSA 冻结，一律进 note 与 stats，**绝不静默**。
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class IterationTally:
    """单轮的逐段计数（落 ``stats_json``，UI 漏斗直接读它）。"""

    generated: int = 0
    rule_rejected: int = 0
    llm_rejected: int = 0
    validated: int = 0
    kept: int = 0
    rejected_by_reason: dict = field(default_factory=dict)
    by_source: dict = field(default_factory=dict)
    truncated_by_timeout: int = 0
    overrun: bool = False
    skipped: str = ""

    def count_source(self, source: str) -> None:
        self.by_source[source] = self.by_source.get(source, 0) + 1

    def count_reject(self, reason: str) -> None:
        self.rejected_by_reason[reason] = self.rejected_by_reason.get(reason, 0) + 1


def rate(numerator: int, denominator: int) -> float:
    """比率（分母为 0 → 0.0；用于入库率趋势，绝不对空集取比值）。"""
    return round(numerator / denominator, 6) if denominator else 0.0


def tally_to_stats(tally: IterationTally) -> dict:
    """计数 → ``stats_json``（camelCase，前端漏斗与来源堆叠柱直接消费）。"""
    return {"generated": tally.generated, "ruleRejected": tally.rule_rejected,
            "llmRejected": tally.llm_rejected, "validated": tally.validated,
            "kept": tally.kept, "rejectedByReason": dict(tally.rejected_by_reason),
            "bySource": dict(tally.by_source),
            "truncatedByTimeout": tally.truncated_by_timeout,
            "overrun": tally.overrun, "skipped": tally.skipped}


def iteration_note(tally: IterationTally) -> str:
    """人类可读的一句话（落 ``DiscoveryIteration.note``，UI 时间线直接显示）。

    ``truncatedByTimeout``（本轮墙钟到点，丢了多少条没验证）与 ``overrun``（本轮跑完但超过了
    间隔）是两件事，必须分别说清——把它们混成一句「本轮较慢」，用户就再也分不清是配置太重
    还是机器太忙。
    """
    parts = [f"生成 {tally.generated}／审出 {tally.validated}／入库 {tally.kept}"]
    if tally.truncated_by_timeout:
        parts.append(f"本轮墙钟到点，丢弃 {tally.truncated_by_timeout} 条未验证候选")
    if tally.overrun:
        parts.append("耗时超过迭代间隔（不排队补跑）")
    if tally.skipped:
        parts.append(tally.skipped)
    return "；".join(parts)[:256]


def candidate_event(row) -> dict:
    """候选行 → WS ``candidate`` 事件（只带 UI 需要的字段，不下发大 JSON）。"""
    return {"kind": row.kind, "genomeHash": row.genome_hash, "exprText": row.expr_text,
            "describe": row.describe_text, "mechanism": row.mechanism,
            "source": row.source, "verdict": row.verdict,
            "rejectReason": row.reject_reason, "metrics": row.metrics,
            "createdTs": row.created_ts}
