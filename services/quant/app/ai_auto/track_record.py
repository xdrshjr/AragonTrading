"""完整历史交易净额与恢复后滚动窗口的只读决策材料。"""
from __future__ import annotations

from copy import deepcopy
import math
from statistics import median

_WINDOW_CAPACITY = 200
_MIN_FEE_TRIPS = 10
_HISTORY_KEYS = (
    "roundTrips", "winRate", "payoff", "breakevenPayoff", "expectancyUsd",
    "medianMoveBps", "medianCostBps", "feeTrips", "feeCoverage",
    "expectancyR", "rSamples", "moveSamples", "feeBpsTrips",
    "travelComparisonTrips", "travelComparisonCoverage",
    "travelMedianMoveBps", "travelMedianFeeBps",
)
_SNAPSHOT_KEYS = (
    "coverage", "basis", "asOfTs", "snapshotId", "instId", "bar",
)


def build_track_record(history: dict | None, session_trips: list,
                       settings) -> dict | None:
    """只在已知版本、完整总体及样本下限满足时装配战绩。"""
    if not getattr(settings, "ai_trade_track_record_enabled", False):
        return None
    if not history or not _has_complete_history(history):
        return None
    coverage = history["coverage"]
    minimum = int(getattr(settings, "ai_trade_track_record_min_trips", 20))
    if coverage["netEligibleTrips"] < minimum:
        return None
    if (history.get("verdict") or {}).get("code") in (
            "insufficient_samples", "insufficient_evidence"):
        return None
    pooled = history.get("pooled") or {}
    return {"history": {
        **{key: deepcopy(pooled.get(key)) for key in _HISTORY_KEYS},
        **{key: deepcopy(history.get(key)) for key in _SNAPSHOT_KEYS},
        "roundTrips": coverage["netEligibleTrips"],
        "costSentenceOk": _cost_sentence_ok(pooled, settings),
    }, "session": _session_segment(session_trips)}


def _has_complete_history(history: dict) -> bool:
    coverage = history.get("coverage")
    if history.get("schemaVersion") != 2 or not isinstance(coverage, dict):
        return False
    total = _finite(coverage.get("totalTrips"))
    if total is None or total <= 0:
        return False
    return (coverage.get("netEligibleTrips") == total
            and coverage.get("measuredNetTrips") == total
            and coverage.get("excludedTrips") == 0
            and coverage.get("structuralIssueCount") == 0
            and history.get("basis") == "trading_net_ex_funding")


def _session_segment(trips: list) -> dict:
    rows = list(trips or [])[-_WINDOW_CAPACITY:]
    nets = _values(rows, "net_pnl_usd")
    moves = _values(rows, "move_bps")
    r_values = _values(rows, "r_multiple")
    known_net = round(sum(nets), 4) if nets else None
    measured = sum(
        _finite(getattr(row, "net_pnl_usd", None)) is not None
        and getattr(row, "measured_net_eligible", False) is True
        for row in rows)
    return {
        "scope": "rolling", "capacity": _WINDOW_CAPACITY,
        "roundTrips": len(rows), "sampleTrips": len(rows),
        "netSamples": len(nets), "measuredNetSamples": measured,
        "netPnlUsd": known_net if len(nets) == len(rows) else None,
        "knownNetPnlUsd": known_net,
        "medianMoveBps": _median(moves), "moveSamples": len(moves),
        "medianRMultiple": _median(r_values), "rSamples": len(r_values),
    }


def _cost_sentence_ok(pooled: dict, settings) -> bool:
    cost = _finite(pooled.get("travelMedianFeeBps"))
    coverage = _finite(pooled.get("travelComparisonCoverage"))
    samples = _finite(pooled.get("travelComparisonTrips"))
    if cost is None or cost <= 0 or samples is None:
        return False
    floor = float(getattr(
        settings, "ai_trade_track_record_min_fee_coverage", 0.5))
    return (samples >= _MIN_FEE_TRIPS
            and coverage is not None and coverage >= floor)


def format_track_record_text(wire: dict | None) -> str:
    """按各自分母渲染历史与滚动窗口；两段不相加为独立总样本。"""
    if not wire:
        return ""
    hist = wire.get("history") or {}
    scope = " ".join(str(value) for value in (
        hist.get("instId") or "本标的", hist.get("bar")) if value)
    trips = int(hist.get("roundTrips") or 0)
    lines = [
        f"【历史交易净额，不含资金费】（{scope}，历史 {trips} 笔往返）",
        f"- 胜率 {_pct(hist.get('winRate'))}，"
        f"赔率 {_num(hist.get('payoff'))}（{trips} 笔可核算净额）；"
        f"盈亏平衡赔率 {_num(hist.get('breakevenPayoff'))}。",
        f"- 交易净额每笔期望 {_num(hist.get('expectancyUsd'))} USDT"
        f"（{trips} 笔；不含资金费）。",
        f"- 价格位移中位数 {_bps(hist.get('medianMoveBps'))}"
        f"（{int(hist.get('moveSamples') or 0)} 笔可算位移）。",
    ]
    if hist.get("costSentenceOk"):
        count = int(hist.get("travelComparisonTrips") or 0)
        lines.append(
            f"- 往返手续费约 {_bps(hist.get('travelMedianFeeBps'))}"
            f"（{count} 笔同笔尺度 / 历史 {trips} 笔；不含资金费）。")
    lines.append(
        f"- 已实现 R 倍数均值 {_num(hist.get('expectancyR'))}"
        f"（{int(hist.get('rSamples') or 0)} 笔可算 R）。")
    lines.extend(_session_text(wire.get("session") or {}))
    lines.append("以上是历史统计，不是对未来的承诺；样本或证据不足时本段不出现。")
    return "\n".join(lines)


def _session_text(session: dict) -> list[str]:
    count = int(session.get("sampleTrips") or 0)
    known = int(session.get("netSamples") or 0)
    measured = int(session.get("measuredNetSamples") or 0)
    qualifier = "；含估算或来源未确认" if known > measured else ""
    return [
        f"【滚动窗口】本次进程/恢复后可用的最近 {count} 笔，窗口上限 200。"
        "与启动历史可能重叠，不能相加为独立总样本。",
        f"- 交易净额 {_num(session.get('netPnlUsd'))} USDT"
        f"（已知 {known}/{count} 笔，实测 {measured}/{count} 笔"
        f"{qualifier}；不含资金费）；"
        f"已知部分 {_num(session.get('knownNetPnlUsd'))} USDT"
        f"（{known} 笔）。",
        f"- 位移中位数 {_bps(session.get('medianMoveBps'))}"
        f"（{int(session.get('moveSamples') or 0)} 笔）；"
        f"R 中位数 {_num(session.get('medianRMultiple'))}"
        f"（{int(session.get('rSamples') or 0)} 笔）。",
    ]


class TrackRecordRuntime:
    """历史启动快照只读，滚动窗口通过零 IO 的 provider 每轮装配。"""

    def __init__(self, snapshot: dict | None, settings,
                 trips_provider=None) -> None:
        self.snapshot = snapshot
        self.settings = settings
        self._trips_provider = trips_provider or (lambda: [])
        self.shown = False

    def inject(self, payload: dict) -> None:
        """资格不足时移除旧键，防止复用 payload 留下过期战绩。"""
        wire = build_track_record(
            self.snapshot, self._trips_provider(), self.settings)
        payload.pop("trackRecord", None)
        if wire is not None:
            payload["trackRecord"] = wire
            self.shown = True


def _finite(value) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) else None


def _values(rows: list, name: str) -> list[float]:
    return [value for row in rows
            if (value := _finite(getattr(row, name, None))) is not None]


def _pct(value) -> str:
    value = _finite(value)
    return "未知" if value is None else f"{value * 100:.1f}%"


def _bps(value) -> str:
    value = _finite(value)
    return "未知" if value is None else f"{value:.1f} bps"


def _num(value) -> str:
    value = _finite(value)
    return "未知" if value is None else f"{value:.3g}"


def _median(values: list) -> float | None:
    return round(median(values), 4) if values else None
