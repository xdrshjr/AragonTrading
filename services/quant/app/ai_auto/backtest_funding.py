"""历史资金费时间索引：预测与计提分别保留来源和可见窗口。"""
from __future__ import annotations

import math
from bisect import bisect_right
from dataclasses import dataclass, field

from .funding import FundingSnapshot


def finite_number(value) -> float | None:
    """解析有限数，未知及布尔值保持缺席。"""
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def funding_timestamp(value) -> int | None:
    """读取已经归一为秒的正整数时间，不再次转换单位。"""
    number = finite_number(value)
    if number is None or number <= 0 or not number.is_integer():
        return None
    return int(number)


def _rate(row: dict) -> tuple[float | None, str]:
    realized = finite_number(row.get("realizedRate"))
    if realized is not None:
        return realized, "realized"
    estimated = finite_number(row.get("fundingRate"))
    if estimated is not None:
        return estimated, "estimated"
    return None, "unknown"


@dataclass
class BacktestFundingCurve:
    """不可跨断点的时间索引；全局诊断不得用于早先决策资格。"""

    rows: list[dict] = field(default_factory=list)
    load_coverage: str = "complete"
    load_gaps: list[tuple[int, int]] = field(default_factory=list)
    load_reasons: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.invalid_rows = 0
        self.untimed_invalid_rows = 0
        self.conflicting_points = 0
        grouped: dict[int, set[tuple[float | None, str]]] = {}
        for row in self.rows or []:
            row = row if isinstance(row, dict) else {}
            ts = funding_timestamp(row.get("fundingTs"))
            rate, source = _rate(row)
            if ts is None or rate is None:
                self.invalid_rows += 1
            if ts is None:
                self.untimed_invalid_rows += 1
                continue
            grouped.setdefault(ts, set()).add((rate, source))
        self._events: list[tuple[int, float | None, str]] = []
        for ts, values in sorted(grouped.items()):
            if len(values) > 1:
                self.conflicting_points += 1
                self._events.append((ts, None, "unknown"))
            else:
                rate, source = next(iter(values))
                self._events.append((ts, rate, source))
        self._times = [event[0] for event in self._events]

    def points_in(self, from_ts: int, to_ts: int) -> list[tuple[int, float]]:
        """返回 `(from_ts,to_ts]` 内已实测结算点，预计率不入账。"""
        left = bisect_right(self._times, from_ts)
        right = bisect_right(self._times, to_ts)
        return [(ts, rate) for ts, rate, source in self._events[left:right]
                if source == "realized"]

    @property
    def available(self) -> bool:
        """离线诊断：文件是否含两个可用点，不表示任一过去轮可用。"""
        return sum(rate is not None for _, rate, _ in self._events) >= 2

    @property
    def interval_hint(self) -> float | None:
        """离线末段间隔；不得参与决策或历史窗口资格。"""
        if len(self._events) < 2:
            return None
        previous, latest = self._events[-2:]
        if previous[1] is None or latest[1] is None:
            return None
        return float(latest[0] - previous[0])

    def snapshot_at(self, ts: int, *, now_wall: float = 0.0
                    ) -> FundingSnapshot | None:
        """只按最后两个相邻可见事件预测；断点或已过期返回缺席。"""
        index = bisect_right(self._times, ts) - 1
        if index < 1:
            return None
        previous, latest = self._events[index - 1:index + 1]
        if previous[1] is None or latest[1] is None:
            return None
        interval = latest[0] - previous[0]
        if ts > latest[0] + interval:
            return None
        return FundingSnapshot(
            rate=latest[1], funding_ts=latest[0],
            next_funding_ts=latest[0] + interval,
            interval_s=float(interval), fetched_wall=now_wall,
            interval_known=False, interval_source="historical_observed")

    def window_evidence(self, from_ts: int, to_ts: int) -> dict:
        """返回可见窗口计提证据；未知不能被空列表或未来数据洗白。"""
        left = bisect_right(self._times, from_ts)
        right = bisect_right(self._times, to_ts)
        events = self._events[left:right]
        invalid = sum(rate is None for _, rate, _ in events)
        estimated = sum(source == "estimated" for _, _, source in events)
        complete = self._window_is_covered(from_ts, to_ts, left, right)
        coverage = "complete" if complete else (
            "partial" if right else "unavailable")
        return {
            "points": self.points_in(from_ts, to_ts),
            "coverage": coverage,
            "firstVisibleTs": self._times[0] if right else None,
            "lastVisibleTs": self._times[right - 1] if right else None,
            "invalidPointCount": invalid,
            "estimatedPointCount": estimated,
        }

    def _window_is_covered(self, start, end, left, right) -> bool:
        anchor = self.snapshot_at(start)
        if anchor is None or self._events[left - 1][2] != "realized":
            return False
        for index in range(left, right):
            ts, _, source = self._events[index]
            if source != "realized" or ts > anchor.next_funding_ts:
                return False
            anchor = self.snapshot_at(ts)
            if anchor is None:
                return False
        # 到期仍无实测点不能证明零；预测快照的有效期是另一项证据。
        if end >= anchor.next_funding_ts:
            return False
        return not any(a < end and b > start for a, b in self.load_gaps)

    def diagnostics(self) -> dict:
        """整次加载的完整性诊断，仅在最终结果披露。"""
        return {
            "invalidRows": self.invalid_rows,
            "conflictingPoints": self.conflicting_points,
            "untimedInvalidRows": self.untimed_invalid_rows,
            "loadCoverage": self.load_coverage,
            "loadReasons": list(self.load_reasons),
        }


class FundingDecisionEvidence:
    """按实际决策身份去重，preview 和模型重试不增加分母。"""

    def __init__(self) -> None:
        self._seen: set[object] = set()
        self._counts = dict.fromkeys((
            "decisionRounds", "enabledRounds", "disabledRounds",
            "visibleRateRounds", "degradedRounds", "missingRounds"), 0)

    def note(self, identity, snapshot, enabled: bool) -> None:
        """首次进入决策即记一轮，包括随后模型失败的轮。"""
        if identity in self._seen:
            return
        self._seen.add(identity)
        self._counts["decisionRounds"] += 1
        self._counts["enabledRounds" if enabled else "disabledRounds"] += 1
        if not enabled:
            return
        self._counts[
            "visibleRateRounds" if snapshot else "missingRounds"] += 1
        if snapshot is None or not snapshot.interval_known:
            self._counts["degradedRounds"] += 1

    def wire(self, curve: BacktestFundingCurve) -> dict:
        """计数恒等式和曲线诊断共用的结果投影。"""
        return {"version": 2, **self._counts, **curve.diagnostics()}


async def load_history_curve(rest, request: dict) -> BacktestFundingCurve:
    """有界向前翻页，保留已取点和缺口原因，暖机至少两个过去事件。"""
    rows: list[dict] = []
    cursor = (request["end_ts"] + 1) * 1000
    seen_times: set[int] = set()
    reasons: list[str] = []
    for _ in range(request["max_pages"]):
        try:
            page = await rest.get_funding_rate_history(
                request["inst_id"], after_ms=cursor, limit=100)
        except Exception as exc:  # 公共观测失败保留已取得的历史。
            reasons.append(f"request_failed:{type(exc).__name__}")
            break
        if not page:
            reasons.append("history_exhausted")
            break
        times = {ts for row in page if isinstance(row, dict)
                 if (ts := funding_timestamp(row.get("fundingTs")))}
        rows.extend(page)
        if times and min(times) * 1000 >= cursor:
            reasons.append("pagination_no_progress")
            break
        if not times:
            reasons.append("untimed_page")
            break
        seen_times.update(times)
        if sum(ts < request["start_ts"] for ts in seen_times) >= 2:
            break
        cursor = min(times) * 1000
    else:
        reasons.append("page_limit")
    return BacktestFundingCurve(
        rows, load_coverage="partial" if reasons else "complete",
        load_reasons=reasons)
