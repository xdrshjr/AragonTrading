"""路径依赖的回测求值车道（discovery-strategy-axes §2.5）—— **承重**。

与上游 §2.7.0 对位：那节解决「统计式求值怎么切样本外」，本节解决「回测怎么切样本外」。
三条镜像但方向相反的坑，按最直觉的写法实现都**不报错**，只是安全网从未生效：

- **BL-1**：成本必须传 **bps 原值**（``run_backtest`` 内部才 ``/1e4``）。上游 F8 的
  ``round_trip_cost`` 要 ``/1e4`` 是因为它拿 bps 去和**小数**收益差比较；这里是把 bps 交给一个
  **收 bps 的函数**。混起来就是一次「挖出来的策略全都很赚」的静默事故。
- **BL-2**：IS/OOS 用两次独立回测 + **两个独立策略实例**；OOS 段前伸 ``pad`` 根只暖机不交易。
- **BL-13**：指标 = ``compute_metrics`` **并** ``compute_extended_metrics`` 的并集。前者的返回
  契约只有 7 项且**不含 calmar**，只调它会让 S5 恒判「不可判定即拒」——**所有候选卡在 S5**。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..engine.backtester import BacktestResult, run_backtest
from ..engine.combo import run_combo_backtest
from ..engine.metrics import compute_metrics
from ..engine.metrics_ext import compute_extended_metrics
from ..engine.simulator import EquityRecord
from ..strategies.base import BaseStrategy, get_strategy_class

MIN_WARMUP_BARS = 20
GATE_EPSILON = 1e-6

# BL-11（评审 P0-2）：轮动策略里**属于成本模型**的标量，一律锁到与回测成本同源的值。
FROZEN_POLICY_KEYS: frozenset[str] = frozenset({"turnoverBps"})


def costs_of(settings) -> tuple[float, float, float, float]:
    """``(initial_capital, taker_fee_bps, slippage_bps, leverage)`` —— **bps 原值**（BL-1）。

    ⚠️ 承重：``engine/simulator.py`` 内部才 ``/1e4``。**严禁**在本层再除一次——除了就等于
    成本消失，挖出来的策略个个都赚，而且没有任何错误信息。反向的那半条纪律见
    ``validation.round_trip_cost``（它必须继续返回小数）。

    字段名以既有 ``agent/services.py::_resolve_costs`` 为准：本金字段是
    ``settings.initial_capital``（**不带** ``default_`` 前缀），另三个才带。
    """
    return (float(settings.initial_capital), float(settings.default_taker_fee_bps),
            float(settings.default_slippage_bps), float(settings.default_leverage))


def frozen_policy_values(settings) -> dict:
    """轮动策略的成本类标量（BL-11）：``turnoverBps`` = 一次往返的 fee + slip（bps）。"""
    _capital, fee_bps, slip_bps, _lev = costs_of(settings)
    return {"turnoverBps": round(2.0 * (fee_bps + slip_bps), 4)}


def metrics_of(points: list, trades: list, bar: str, initial_capital: float) -> dict:
    """本轮**唯一**的策略指标出口（BL-13）：两个引擎函数的并集 + 一个显式派生量。

    ``tradeRate``（S7 用）两个引擎函数里都没有——``metrics_ext`` 有的是 ``tradesPerWeek``
    （按墙钟秒换算），与 S7「按根数的交易密度」不是一回事，**不得**拿它冒充。
    """
    base = compute_metrics(points, trades, bar, initial_capital)
    ext = compute_extended_metrics(points, trades, bar, initial_capital)
    bars = len(points)
    return {**base, **ext, "bars": bars,
            "tradeRate": round(int(base["tradeCount"]) / bars, 6) if bars else 0.0}


class GatedStrategy(BaseStrategy):
    """把一个策略包成「前 N 根只暖机不交易」（BL-2）。

    OOS 段必须前伸 ``pad`` 根，否则 ``prepare`` 从 OOS 头部重算 rolling，前 ``warmup`` 根全
    NaN、整段样本外白丢一截；但前伸的这几根若允许开仓，IS 段的成交就会被算进 OOS——「样本外」
    名不副实。gate 之后，pad 区间内**零成交、权益恒等于 initial_capital**，于是切片是一次
    纯切片，不需要 rebase，drawdown 也天然正确。
    """

    def __init__(self, inner: BaseStrategy, first_index: int):
        # ``run_backtest`` 本身**不读** meta（评审 P2-2：核对过 engine/backtester.py 全文）；
        # 这里透传只是让包装器在日志 / 调试处与被包者同名，不构成任何耦合契约。
        self.meta = inner.meta
        self._inner, self._first = inner, int(first_index)
        self.params = inner.params

    def prepare(self, df, bar):
        return self._inner.prepare(df, bar)

    def attach_timeframes(self, df, bar, frames):
        return self._inner.attach_timeframes(df, bar, frames)

    def timeframes(self):
        return self._inner.timeframes()

    def htf_warmup_bars(self):
        return self._inner.htf_warmup_bars()

    def warmup(self):
        return self._inner.warmup()

    def risk_params(self):
        return self._inner.risk_params()

    def on_candle(self, df, i, ctx):
        return [] if i < self._first else self._inner.on_candle(df, i, ctx)


def warmup_pad(strategy: BaseStrategy) -> int:
    return max(int(strategy.warmup()), MIN_WARMUP_BARS)


@dataclass(frozen=True)
class ClippedResult:
    """切片后的样本外产物（指标一律经 ``metrics_of``，BL-13）。"""

    points: list
    trades: list
    metrics: dict


def clip_result(result: BacktestResult, *, from_ts: int, initial_capital: float,
                bar: str) -> ClippedResult | None:
    """gated 模式的**纯切片**；gate 泄漏（起点权益 ≠ 初始资金）一律 ``None``（fail-closed）。

    起点权益恒等于初始资金是「gate 真的生效」的机器判据，比注释可靠：pad 区零成交 ⇒
    无未实现盈亏 ⇒ 权益 == 现金 == 初始资金。
    """
    points = [p for p in result.equity_points if p.ts >= from_ts]
    if not points or abs(float(points[0].equity) - float(initial_capital)) > GATE_EPSILON:
        return None
    trades = [t for t in result.trades if t.ts >= from_ts]
    return ClippedResult(points, trades, metrics_of(points, trades, bar, initial_capital))


def clip_rebased(result, *, from_ts: int, initial_capital: float,
                 bar: str) -> ClippedResult | None:
    """``strategy_combo`` 的 rebase 切片（BL-3）：组合净值无法 gate，只能重基。

    ``drawdown`` **必须按切片后的运行峰值重算**——原值是相对整段峰值的，直接沿用会把 IS 段的
    回撤记到 OOS 头上。切片为空或起点权益已归零/为负（爆仓）时一律 ``None``（评审 P2-5：
    否则 rebase 与 peak 两处都会除零）。
    """
    points = [p for p in result.equity_points if p.ts >= from_ts]
    if not points or float(points[0].equity) <= 0.0:
        return None
    scale = float(initial_capital) / float(points[0].equity)
    peak = float(points[0].equity) * scale
    rebased: list[EquityRecord] = []
    for point in points:
        equity = float(point.equity) * scale
        peak = max(peak, equity)
        rebased.append(EquityRecord(ts=int(point.ts), equity=equity,
                                    drawdown=min(equity / peak - 1.0, 0.0) if peak > 0 else 0.0))
    trades = [t for t in result.trades if t.ts >= from_ts]
    return ClippedResult(rebased, trades, metrics_of(rebased, trades, bar, initial_capital))


@dataclass
class SplitBacktest:
    """IS / OOS 两段产物 + 口径标注（``clipMode`` 必须落库，BL-3）。"""

    is_metrics: dict
    oos_metrics: dict
    is_points: list
    oos_points: list
    pad: int
    clip_mode: str
    backtests: int
    reject_reason: str = ""
    notes: list = field(default_factory=list)


# ============================ 三个 Assembler（genome → 可回测对象） ============================
class ParamAssembler:
    """``strategy_param``：注册表模板 + 完整参数（BL-7：被回测的就是被入库的那份）。"""

    kind = "strategy_param"
    is_combo = False

    def __init__(self, template_key: str, params: dict):
        self.template_key = str(template_key)
        self.params = dict(params)

    def build(self) -> BaseStrategy:
        cls = get_strategy_class(self.template_key)
        if cls is None:
            raise ValueError(f"未知策略模板：{self.template_key}")
        return cls({**cls.meta.default_params, **self.params})

    def backtests_per_segment(self) -> int:
        return 1

    def htf_frames(self, ctx) -> dict:
        return {tf: ctx.htf_frames[tf] for tf in self.build().timeframes()
                if tf in ctx.htf_frames}

    def preflight(self, ctx) -> str:
        """BL-6 后半：验证层入口的 fail-closed 帧校验（安全不变式必须立在产出层）。"""
        from ..engine.mtf import bar_to_seconds

        base = bar_to_seconds(ctx.bar)
        for tf in self.build().timeframes():
            if tf != ctx.bar and bar_to_seconds(tf) > base and tf not in ctx.htf_frames:
                return f"S0:missing_htf:{tf}"
        return ""

    def postflight(self, strategy) -> str:
        return ""


class FactorComboAssembler:
    """``factor_combo``：成员 + 权重 + 阈值 → ``factor_score`` 策略实例。

    **BL-12（评审 P0-3）**：权重必须**逐成员烘焙**进 ``weight`` 键后才交给
    ``factors.specs.validate_members``——它对缺 ``weight`` 的成员填 **0.0** 且不报错，
    而 ``FactorScoreStrategy.prepare`` 在无顶层 ``weights`` 时走的正是成员烘焙那条路径。
    结果是复合得分恒 0 ⇒ 零成交 ⇒ 全轴卡 S1「样本量不足」，看起来像市场没有 alpha。
    """

    kind = "factor_combo"
    is_combo = False

    def __init__(self, members_raw: list[dict], thresholds: dict):
        self.members_raw = [dict(member) for member in members_raw]
        self.thresholds = dict(thresholds)

    def members(self) -> list[dict]:
        from ..factors import specs as factor_specs      # 全限定名，见评审 P2-4

        return factor_specs.validate_members(self.members_raw)

    def build(self) -> BaseStrategy:
        from ..factors import specs as factor_specs
        from ..strategies.factor_score_strategy import FactorScoreStrategy

        return FactorScoreStrategy({
            **FactorScoreStrategy.meta.default_params,
            "members": self.members(),
            "thresholds": factor_specs.normalize_thresholds(self.thresholds)})

    def backtests_per_segment(self) -> int:
        return 1

    def htf_frames(self, ctx) -> dict:
        return {}                                        # 因子打分不跨周期

    def preflight(self, ctx) -> str:
        return ""

    def postflight(self, strategy) -> str:
        """P1-6：``prepare`` 把打分失败吞成「安静空仓」，这里在**产出层**把它揪出来。

        判据取 ``_score`` **是否全 NaN**，而不是 ``_breakdown is not None``：后者只覆盖抛
        ``ValueError`` / ``KeyError`` 的那条路径，而「表达式解析得通但求值全 NaN」
        （如低于最小复杂度门槛的 expr、与当前 bar 不兼容的库内因子）根本不抛错。
        两者不等价，只认后者会漏掉整整一类静默失效。
        """
        breakdown = getattr(strategy, "_breakdown", None)
        score = getattr(breakdown, "score", None) if breakdown is not None else None
        if score is None or not bool(score.notna().any()):
            return "S0:score_unavailable"
        return ""


class RotationAssembler:
    """``strategy_combo``：成员策略 + 轮动参数 → ``run_combo_backtest``（不走 ``run_backtest``）。"""

    kind = "strategy_combo"
    is_combo = True

    def __init__(self, member_specs: list[dict], policy_spec: dict, settings):
        self.member_specs = [dict(spec) for spec in member_specs]
        self.policy_spec = dict(policy_spec)
        self._settings = settings

    def members(self) -> list:
        from ..strategies import combo_specs

        return combo_specs.build_members(self.member_specs, [], strict=True)

    def policy(self):
        from ..strategies import combo_specs

        return combo_specs.policy_from_spec(
            {**self.policy_spec, **frozen_policy_values(self._settings)})

    def backtests_per_segment(self) -> int:
        return max(1, len(self.member_specs))

    def htf_frames_by_member(self, ctx, members=None) -> dict:
        """评审 P1-4：**按 ``member.key``（展示 key）索引**，不是 ``template_key``。

        ``build_members`` 对同模板的第二个实例生成 ``<key>#2``，而 ``run_combo_backtest``
        是按 ``member.key`` 取帧的。按 ``template_key`` 组装，``#2`` 成员就会静默走
        「无 MTF 数据」路径——正是 BL-6 要防的事。
        """
        return {member.key: {tf: ctx.htf_frames[tf] for tf in member.timeframes
                             if tf in ctx.htf_frames}
                for member in (members if members is not None else self.members())}

    def preflight(self, ctx) -> str:
        from ..engine.mtf import bar_to_seconds

        members = self.members()
        frames = self.htf_frames_by_member(ctx, members)
        if {member.key for member in members} != set(frames):
            return "S0:missing_htf:member_key_mismatch"
        base = bar_to_seconds(ctx.bar)
        for member in members:
            for tf in member.timeframes:
                if tf != ctx.bar and bar_to_seconds(tf) > base and tf not in frames[member.key]:
                    return f"S0:missing_htf:{member.key}:{tf}"
        return ""

    def postflight(self, strategy) -> str:
        return ""


def combo_warmup_floor(members: list, policy) -> int:
    """与 ``engine/combo.py::_warmup_floor`` **同源**的首个可决策根（评审 P1-11）。

    v1 写的「成员 warmup + lookbackBars」漏了 **regime 起算根**。轮动参数是被挖的，
    ``lookbackBars`` 可低至 24，此时 pad 会小于 ``_warmup_floor``，OOS 段头部就会有一截
    「组合还没开始决策」的恒定权益被算进样本外指标，把 S6 稀释成一条更容易通过的闸。
    """
    from ..engine.combo import _warmup_floor

    return int(_warmup_floor(members, policy, None))


# ============================ 双段回测（BL-2 / BL-3） ============================
def _cost_kwargs(settings, mult: float) -> dict:
    initial, fee_bps, slip_bps, leverage = costs_of(settings)
    return {"initial_capital": initial, "taker_fee_bps": fee_bps * mult,
            "slippage_bps": slip_bps * mult, "leverage": leverage}


def run_split_backtest(assembler, ctx, *, cost_mult: float = 1.0) -> SplitBacktest:
    """IS / OOS 双段回测（两次独立实例；OOS 前伸 pad 只暖机不交易）。"""
    reason = assembler.preflight(ctx)
    if reason:
        return SplitBacktest({}, {}, [], [], 0, "", 0, reject_reason=reason)
    if assembler.is_combo:
        return _run_combo_split(assembler, ctx, cost_mult)
    return _run_gated_split(assembler, ctx, cost_mult)


def _run_gated_split(assembler, ctx, cost_mult: float) -> SplitBacktest:
    kwargs = _cost_kwargs(ctx.settings, cost_mult)
    initial = kwargs["initial_capital"]
    frames = assembler.htf_frames(ctx) or None
    strategy_is = assembler.build()
    pad = warmup_pad(strategy_is)
    res_is = run_backtest(strategy_is, ctx.candles[:ctx.split], ctx.bar, htf_frames=frames,
                          **kwargs)
    reason = assembler.postflight(strategy_is)
    if reason:
        return SplitBacktest({}, {}, [], [], pad, "gated", 1, reject_reason=reason)
    start = max(0, ctx.split - pad)
    gated = GatedStrategy(assembler.build(), first_index=ctx.split - start)
    res_oos = run_backtest(gated, ctx.candles[start:], ctx.bar, htf_frames=frames, **kwargs)
    clipped = clip_result(res_oos, from_ts=ctx.split_ts, initial_capital=initial, bar=ctx.bar)
    if clipped is None:
        return SplitBacktest({}, {}, [], [], pad, "gated", 2, reject_reason="S0:gate_leak")
    return SplitBacktest(
        is_metrics=metrics_of(res_is.equity_points, res_is.trades, ctx.bar, initial),
        oos_metrics=clipped.metrics, is_points=res_is.equity_points,
        oos_points=clipped.points, pad=pad, clip_mode="gated", backtests=2)


def _run_combo_split(assembler, ctx, cost_mult: float) -> SplitBacktest:
    kwargs = _cost_kwargs(ctx.settings, cost_mult)
    initial = kwargs["initial_capital"]
    policy = assembler.policy()
    members_is = assembler.members()
    pad = combo_warmup_floor(members_is, policy) + int(policy.rebalance_bars)
    per_run = assembler.backtests_per_segment()
    res_is = run_combo_backtest(
        members_is, ctx.candles[:ctx.split], ctx.bar,
        htf_frames_by_member=assembler.htf_frames_by_member(ctx, members_is), policy=policy,
        **kwargs)
    start = max(0, ctx.split - pad)
    members_oos = assembler.members()                      # BL-2：绝不复用同一批实例
    res_oos = run_combo_backtest(
        members_oos, ctx.candles[start:], ctx.bar,
        htf_frames_by_member=assembler.htf_frames_by_member(ctx, members_oos), policy=policy,
        **kwargs)
    clipped = clip_rebased(res_oos, from_ts=ctx.split_ts, initial_capital=initial, bar=ctx.bar)
    if clipped is None:
        return SplitBacktest({}, {}, [], [], pad, "rebased", 2 * per_run,
                             reject_reason="S0:rebase_undefined")
    return SplitBacktest(
        is_metrics=metrics_of(res_is.equity_points, res_is.trades, ctx.bar, initial),
        oos_metrics=clipped.metrics, is_points=res_is.equity_points,
        oos_points=clipped.points, pad=pad, clip_mode="rebased", backtests=2 * per_run,
        notes=list(res_is.notes or []))


def run_stress_is(assembler, ctx, *, cost_mult: float) -> tuple[dict, int]:
    """S9 成本压力：以 ``cost_mult ×`` 成本**只重跑 IS 段**（同 assembler、同窗口）。"""
    kwargs = _cost_kwargs(ctx.settings, cost_mult)
    initial = kwargs["initial_capital"]
    per_run = assembler.backtests_per_segment()
    if assembler.is_combo:
        members = assembler.members()
        result = run_combo_backtest(
            members, ctx.candles[:ctx.split], ctx.bar,
            htf_frames_by_member=assembler.htf_frames_by_member(ctx, members),
            policy=assembler.policy(), **kwargs)
    else:
        result = run_backtest(assembler.build(), ctx.candles[:ctx.split], ctx.bar,
                              htf_frames=assembler.htf_frames(ctx) or None, **kwargs)
    return metrics_of(result.equity_points, result.trades, ctx.bar, initial), per_run


def bar_returns(points: list) -> list[float]:
    """逐根收益率序列（S8 去重基准；与 ``compute_metrics`` 的口径逐字一致）。"""
    equities = [float(p.equity) for p in points]
    return [cur / prev - 1.0 for prev, cur in zip(equities, equities[1:]) if prev != 0.0]
