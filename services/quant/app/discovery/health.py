"""体检判级的**纯函数**层（discovery-asset-ledger §2.3 / §2.5）—— 无 async / 无 DB / 无 OKX。

一条错误的「健康」比崩溃危险得多：崩溃会被发现，虚假的绿灯不会。因此本模块的三条纪律：

- **AL-2**：判级只看「挖掘窗口右端之后新产生的那一段」（fresh 段），且 fresh 段起点锚定
  ``minedWindow.endTs`` 并**对长度夹上界**——从 ``now`` 固定回看会让老资产的暖机段恒为 0 根、
  永久判 ``unknown``，而 ``unknown`` 是中性文案，界面上看不出任何异常。
- **AL-3**：指标一律来自挖掘用的那把尺子（``evaluate_split`` / ``run_split_backtest``），
  本模块**不新写任何指标计算**，只做比较与状态机。
- **AL-8**：``unknown`` / ``error`` 不计入连续失败；降级要迟滞。
"""
from __future__ import annotations

import bisect
from dataclasses import dataclass

# 六个判据码。``grade()`` 能返回的码集合必须与本元组**完全相等**（§7.1 的机器判据），
# 前端 ``discoveryHealth.ts`` 的中文映射键集合同样对齐这六个。
HEALTH_CODES: tuple[str, ...] = ("H1", "H2", "H3", "H4", "H5", "H6")

# 后端短标签（日志 / 导出用）。**UI 中文的唯一真源在前端** ``lib/discoveryHealth.ts``，
# 与 ``validation.FILTER_LABELS`` ↔ 前端 ``REJECT_REASON_CN`` 同一分工。
HEALTH_LABELS: dict[str, str] = {
    "H1": "新增样本不足",
    "H2": "暖机段不足",
    "H3": "新增段覆盖率不足",
    "H4": "方向反号",
    "H5": "强度衰减",
    "H6": "风险恶化",
}

UNKNOWN_CODES = ("H1", "H2", "H3")
FAIL_CODES = ("H4", "H5", "H6")
HEALTH_STATUSES = ("fresh", "healthy", "watch", "decayed", "unknown", "error")

MIN_FRESH_SAMPLE_RATIO = 0.2      # fresh 段配对样本下限 = minSamples × 该系数（与 F1 同姿态）
EPSILON = 1e-9
MAX_ERROR_BACKOFF_POWER = 3       # error 退避上限 8×interval


@dataclass(frozen=True)
class HealthLimits:
    """体检的全部标量口径（聚合传参；逐项来自 settings，绝不在下游再读一次配置）。"""

    window_bars: int
    warmup_bars: int
    min_fresh_bars: int
    min_warmup_bars: int
    min_fresh_trades: int
    retain_frac: float
    fail_streak: int
    interval_s: float

    @staticmethod
    def of(settings) -> "HealthLimits":
        return HealthLimits(
            window_bars=int(settings.discovery_health_window_bars),
            warmup_bars=int(settings.discovery_health_warmup_bars),
            min_fresh_bars=int(settings.discovery_health_min_fresh_bars),
            min_warmup_bars=int(settings.discovery_health_min_warmup_bars),
            min_fresh_trades=int(settings.discovery_health_min_fresh_trades),
            retain_frac=float(settings.discovery_health_retain_frac),
            fail_streak=int(settings.discovery_health_fail_streak),
            interval_s=float(settings.discovery_health_interval_s))

    @property
    def max_fresh_bars(self) -> int:
        """fresh 段根数上界；配置校验已保证它为正（``config.py::_check_health_window``）。"""
        return max(1, self.window_bars - self.warmup_bars)


@dataclass(frozen=True)
class HealthWindow:
    """一个资产在本次体检里的窗口切分产物（三个 ts 全部落库，可回溯）。"""

    window_start_ts: int
    window_end_ts: int
    fresh_start_ts: int
    split: int
    fresh_bars: int
    warmup_bars: int


def aligned_check_end(now: float, step: int) -> int:
    """已确认根对齐的右端（与 ``runtime._window_bounds`` 逐字同口径）。"""
    return int(now) // int(step) * int(step) - int(step)


def group_window_start(check_end: int, step: int, limits: HealthLimits) -> int:
    """组取数窗口左端；组内**任一**资产的暖机段左端都不会越过它（§2.3 的证明）。"""
    return int(check_end) - limits.window_bars * int(step)


def fresh_start_of(mined_end_ts: int, check_end: int, step: int,
                   limits: HealthLimits) -> int:
    """fresh 段起点（**承重的那一行**，AL-2 / 评审 P0-1）。

    ``max(minedEndTs, checkEnd − maxFreshBars × step)``：下界保证「只用挖掘之后的数据」，
    上界保证「暖机段恒有 ``warmup_bars`` 根」且「fresh 段不随资产年龄无界增长」。
    少了上界，资产一老过 ``windowBars × step``（15m 下约 15.6 天）暖机段就恒为 0 根 ⇒
    H2 恒触发 ⇒ 全部老资产永久 ``unknown``。
    """
    return max(int(mined_end_ts), int(check_end) - limits.max_fresh_bars * int(step))


def split_window(ts_values: list, *, fresh_start_ts: int, window_start_ts: int,
                 window_end_ts: int) -> HealthWindow:
    """按 ts 定位切点（``bisect_left`` 而非算术推算——缺根时算术会错位，R-13）。"""
    series = [int(value) for value in ts_values]
    split = bisect.bisect_left(series, int(fresh_start_ts))
    split = max(0, min(split, len(series)))
    return HealthWindow(window_start_ts=int(window_start_ts), window_end_ts=int(window_end_ts),
                        fresh_start_ts=int(fresh_start_ts), split=split,
                        fresh_bars=len(series) - split, warmup_bars=split)


# ============================ 判级（H1–H6） ============================
def window_reasons(fresh_bars: int, warmup_bars: int, limits: HealthLimits) -> list[str]:
    """窗口根数不足的两条（H1 / H2）——**fail-closed 到 ``unknown``**，不是到 ``decayed``。"""
    reasons = []
    if int(fresh_bars) < limits.min_fresh_bars:
        reasons.append("H1")
    if int(warmup_bars) < limits.min_warmup_bars:
        reasons.append("H2")
    return reasons


def _num(value) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if out == out and abs(out) != float("inf") else None    # NaN / inf → None


def grade_factor(fresh: dict, accept: dict, thresholds: dict,
                 limits: HealthLimits) -> list[str]:
    """因子资产的 H1–H6。

    ``fresh`` 取 ``evaluate_split(...).res_oos.horizons[0]`` 的四项 + fresh 段现算的覆盖率
    与配对样本数；``accept`` 是登记时冻结的 ``metrics["is"]``（分母，AL-3）。
    """
    reasons: list[str] = []
    min_samples = int(thresholds.get("minSamples", 200))
    if int(fresh.get("samples", 0)) < max(2, int(min_samples * MIN_FRESH_SAMPLE_RATIO)):
        reasons.append("H1")
    coverage = _num(fresh.get("coverage"))
    if coverage is None or coverage < float(thresholds.get("minCoverage", 0.6)):
        reasons.append("H3")
    if reasons:
        return _dedupe(reasons)
    rank_ic = _num(fresh.get("rankIc"))
    if rank_ic is None or rank_ic <= 0.0:
        return ["H4"]                    # 方向已冻结，非正即反号
    accept_ic = abs(_num(accept.get("rankIc")) or 0.0)
    if abs(rank_ic) < max(float(thresholds.get("minAbsIc", 0.03)),
                          accept_ic * limits.retain_frac):
        reasons.append("H5")
    ir = abs(_num(fresh.get("ir")) or 0.0)
    if ir < float(thresholds.get("minAbsIr", 0.5)) * limits.retain_frac:
        reasons.append("H6")
    return reasons


def grade_strategy(fresh: dict, accept: dict, thresholds: dict,
                   limits: HealthLimits) -> list[str]:
    """策略资产的 H1 / H2 / H4 / H5 / H6（**没有 H3**——覆盖率是因子概念）。

    ``S0:*`` 装配失败一律判 ``unknown`` 并把原码原样带出，绝不用「无帧回测」的结果判级
    （R-10：fail-closed 沿用 BL-6）。
    """
    reject = str(fresh.get("rejectReason") or "")
    if reject.startswith("S0:"):
        return ["H2", reject]
    if int(fresh.get("tradeCount", 0) or 0) < limits.min_fresh_trades:
        return ["H1"]
    total_return = _num(fresh.get("totalReturn"))
    sharpe = _num(fresh.get("sharpe"))
    if total_return is None or sharpe is None or total_return <= 0.0 or sharpe <= 0.0:
        return ["H4"]
    reasons: list[str] = []
    accept_sharpe = _num(accept.get("sharpe"))
    # H5 在非正分母上一律跳过（评审 P1-6）：S6 不要求 OOS 夏普为正，分母可为负，
    # 那时 ``accept × retain`` 更靠近 0，判据方向被悄悄反转；真该拦的情况 H4 已经拦了。
    if accept_sharpe is not None and accept_sharpe > EPSILON:
        if sharpe < accept_sharpe * limits.retain_frac:
            reasons.append("H5")
    drawdown = abs(_num(fresh.get("maxDrawdown")) or 0.0)
    accept_dd = abs(_num(accept.get("maxDrawdown")) or 0.0)
    if drawdown > max(float(thresholds.get("maxDrawdown", 0.35)), accept_dd * 1.5):
        reasons.append("H6")
    return reasons


def grade(kind: str, fresh: dict, accept: dict, thresholds: dict,
          limits: HealthLimits) -> list[str]:
    """判级总入口；窗口不足（H1/H2）优先于任何结论——数据不足不是资产的错（AL-2）。"""
    short = window_reasons(int(fresh.get("freshBars", 0)),
                           int(fresh.get("warmupBars", 0)), limits)
    if short:
        return short
    if kind == "factor":
        return grade_factor(fresh, accept, thresholds, limits)
    return grade_strategy(fresh, accept, thresholds, limits)


def _dedupe(reasons: list[str]) -> list[str]:
    seen: set[str] = set()
    return [code for code in reasons if not (code in seen or seen.add(code))]


# ============================ 留存度 ============================
def retention_of(kind: str, fresh: dict, accept: dict) -> float | None:
    """留存度；分母不可用一律 ``None``（**绝不**填 0 或 1 顶替）。

    落库**不夹紧**（>1 是真实信息：这条资产比挖到时更强）；UI 画条形图时才按 ``min(1, r)``。
    策略侧分母 ``<= 1e-9``（含负值）也回 ``None``——否则「越差的 fresh 夏普给出越高的留存度」，
    UI 上会是一条更长的绿条，还排在留存度倒序的最前面（评审 P1-6）。
    """
    if kind == "factor":
        numerator = abs(_num(fresh.get("rankIc")) or 0.0)
        denominator = abs(_num(accept.get("rankIc")) or 0.0)
    else:
        numerator = _num(fresh.get("sharpe"))
        denominator = _num(accept.get("sharpe"))
        if numerator is None or denominator is None or denominator <= EPSILON:
            return None
    if denominator is None or denominator <= EPSILON:
        return None
    return round(float(numerator) / float(denominator), 6)


# ============================ 状态机与迟滞（AL-8） ============================
def next_status(prev_fail_streak: int, reasons: list[str], *,
                fail_streak_gate: int) -> tuple[str, int]:
    """``(健康状态, 新的连续失败计数)``。``unknown`` / ``error`` 一律不动计数。"""
    unknowns = [code for code in reasons if code in UNKNOWN_CODES]
    fails = [code for code in reasons if code in FAIL_CODES]
    if unknowns:
        return "unknown", int(prev_fail_streak)
    if not fails:
        return "healthy", 0
    streak = int(prev_fail_streak) + 1
    return ("decayed" if streak >= int(fail_streak_gate) else "watch"), streak


# ============================ 下次体检时刻（四条分支，评审 P1-5） ============================
@dataclass(frozen=True)
class SchedulePlan:
    """推进 ``next_check_ts`` 所需的全部输入（聚合传参）。"""

    status: str
    now: int
    step: int
    fresh_bars: int
    consecutive_error: int = 0


def next_check_ts(plan: SchedulePlan, limits: HealthLimits) -> int:
    """正常路径也**必须**推进：不推进就等于队头那 ``batch`` 条资产每 tick 被反复体检、
    其余永远排不上，而 ``summary.guard`` 里一切正常——又一条「不报错的空转」。

    - ``healthy`` / ``watch`` / ``decayed`` ⇒ ``now + interval``
    - ``unknown`` ⇒ ``now + max(interval, 补足 minFreshBars 所需秒数)``（攒够了自然回队）
    - ``error`` ⇒ 有界指数退避（上限 8×interval）
    - 取数失败 ⇒ **不进本函数**：整组跳过、``next_check_ts`` 不动（AL-8）
    """
    interval = int(limits.interval_s)
    if plan.status == "error":
        power = min(max(int(plan.consecutive_error), 0), MAX_ERROR_BACKOFF_POWER)
        return int(plan.now) + interval * (2 ** power)
    if plan.status == "unknown":
        missing = max(0, limits.min_fresh_bars - int(plan.fresh_bars))
        return int(plan.now) + max(interval, missing * int(plan.step))
    return int(plan.now) + interval
