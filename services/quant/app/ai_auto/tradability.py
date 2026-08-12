"""可交易性预检的**纯核心**（ai-trade-tradability-and-open-funnel 轴 A）。

回答一个此前系统从不在事前回答的问题：**在这个标的 / 这个周期 / 这组纪律阈值下，模型的开仓意图
会有多大比例被最小 edge 门禁否决**。本轮用用户本机 `candles_cache` 的真实 K 线实测出的结论是：
BTC-USDT-SWAP `balanced` 预设、市价口径下，**1m 有 98.6%、5m 有 65.0%** 的 K 线断面上
`1.0 × ATR(14)` 达不到 `1.5 × 13.0 bps` 的分母要求——而 `1m` 正是启动弹窗周期下拉的第一项。
用户看到的现象就是「开着、在跑、每轮都有分析、就是从不开单」。

四条不可动摇的纪律（改本文件前先读）：

1. **只提示、不阻断**：`TradabilityVerdict` 永不参与任何交易判定、不改写任何 decision、不进
   `degrade`。本仓库四次「永不开单」事故全都源于把「不确定」翻译成了「否决」。
2. **判据必须与门禁同构**：`required_bps = k_min × cost × degraded_multiplier(...)`，其中
   `degraded_multiplier` **直接复用 `edge_gate` 那一个公开纯函数**（别人拥有的值域必须单点转发）。
   探针说「会被否 98%」而门禁实际放行，比不给探针更坏。
3. **算不出就说算不出**：样本不足 / 成本缺席 ⇒ `unknown`，**绝不**回落 `ok`。
4. **本模块不取数、不算 ATR**：入参是**已经算好的** ATR bps 序列 + 两个成本读数。取数在
   `tradability_probe`（判据能被纯函数单测逐行钉死，取数只能靠集成测试）。
5. **产出的文案是纯文本，不是 markdown**：`headline` / `advice` / `disclaimer` 三串直接进 JSX
   文本节点（`TradabilityCard`、`AiTradePanel` 的红条、启动弹窗的预检块），**不经任何 markdown
   渲染器**——写 `**粗体**` 会原样显示成两个星号。本仓库为这条栽过一次（JSX 里的 `**` 字面星号）。
   强调一律用「」与句式，绝不用 markdown 标记。本模块 docstring 与注释不受此限（那是给人读的）。

**诚实边界（探针是一个乐观下界）**：本模块的分子只取 `atr_bps × atr_target_mult`，而门禁的分子
还会再对两项取 `min`——单仓 OCO 止盈距离（`edge_gate.py:117-118`）与信号先验 EV
（`edge_gate.py:123-124`），两者都只会让分子**更小**、让否决**更多**。故真实否决率只会 ≥ 探针
读数。方向是刻意选的（宁可说「偏紧」也不该说「不可行」而把用户吓退），但必须写明。
同理本模块的 `atr_bps` 由调用方按 `close` 算，而门禁按 `ref_px`（限价即委托价，`edge_gate.py:93`）
——bps 尺度下差异可忽略，但它们是两个价。
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from .edge_gate import degraded_multiplier
from .cost_model import CostBreakdown

# 判定的最小样本根数：低于它一律 `unknown`。60 根是一个「统计上还能说点什么」的下界，
# 且与 `ai_trade_tradability_window_bars` 的 `ge=60` 同源（窗口配置的下界即本常数）。
_MIN_SAMPLES = 60

# 读数统一小数位（避免浮点尾巴污染 wire 与断言；不改变任何判定量级）。
_ROUND = 6

LEVELS = ("ok", "marginal", "infeasible", "unknown")

# 本模块产出的所有文案里唯一那句免责——**任何 UI 都不得重写它**（文案纪律：不得出现
# 「已校准 / 经回测验证」之类的收益承诺）。
DISCLAIMER = (
    "以上是按最近 {samples} 根真实 K 线与当前成本常数算出的确定性统计，不是收益预测；"
    "它衡量的是「若在某根 K 线上想开仓、会不会被最小 edge 门禁否决」，"
    "且未计入止盈上界与信号先验带来的额外收紧，故为乐观下界。")


@dataclass(frozen=True)
class TradabilityInputs:
    """探针入参：**已经算好的** ATR bps 序列 + 两个成本读数。本模块不取数、不算 ATR。

    Attributes:
        atr_bps: 每根的 `ATR(period)/close × 1e4`，已剔除暖机段。
        cost_bps_market: 市价往返成本（bps）；`None` ⇒ 整体 `unknown`（判定走市价口径）。
        cost_bps_limit: 限价开仓往返成本（bps）；`None` ⇒ 限价口径整体缺席。
        k_min: 生效预设的 `ai_trade_edge_gate_k_min`。
        atr_target_mult: `ai_trade_edge_atr_target_mult`。
        stop_atr_mult: `ai_trade_stop_atr_mult`；`0` ⇒ 止损带判据**整体缺席**（不填 0 冒充）。
        cost_confidence_market: `CostBreakdown.confidence` **原样**（"ok" | "degraded"）。
        cost_confidence_limit: 同上，限价口径。
        degraded_mult: `ai_trade_edge_gate_degraded_mult`（默认 1.2）。
        stop_cost_floor_mult: 轴 B 的止损下界倍数（只用于文案，不参与 level 判定）。

    [P0-1] 两个 `confidence` 与 `degraded_mult` **必须由调用方从真实 `CostBreakdown` 取**，严禁在
    本模块里假设 `"ok"`：门禁的分母是 `k_min × cost × degraded_multiplier(cost, degraded_mult)`
    （`edge_gate.py:148`），而探针构造的那个 `CostRuntime` 从不被 `schedule_maintenance` 轮询
    ⇒ 资金费快照恒 `None` ⇒ `confidence` 在探针路径上**恒为 `degraded`**。丢掉这一项 =
    探针恒定低估 20% 的否决率，方向恰好与「探针说能开、门禁却否决」这条最坏失败一致。
    """

    atr_bps: tuple[float, ...]
    cost_bps_market: float | None
    cost_bps_limit: float | None
    k_min: float
    atr_target_mult: float
    stop_atr_mult: float
    cost_confidence_market: str
    cost_confidence_limit: str
    degraded_mult: float
    stop_cost_floor_mult: float = 0.0


@dataclass(frozen=True)
class TradabilityVerdict:
    """一次预检的完整产物（只读；**永不**参与任何交易判定）。"""

    level: str
    samples: int
    atr_p10: float | None = None
    atr_p50: float | None = None
    atr_p90: float | None = None
    required_bps_market: float | None = None
    required_bps_limit: float | None = None
    veto_rate_market: float | None = None
    veto_rate_limit: float | None = None
    stop_bps_p50: float | None = None
    stop_inside_cost_rate_market: float | None = None
    stop_inside_cost_rate_limit: float | None = None
    cost_bps_market: float | None = None
    cost_bps_limit: float | None = None
    cost_confidence_market: str = "ok"
    cost_confidence_limit: str = "ok"
    degraded_mult_applied: float | None = None
    headline: str = ""
    advice: str | None = None


def required_bps(cost: CostBreakdown | None, *, k_min: float, degraded_mult: float) -> float | None:
    """门禁分母的**唯一**计算式：`k_min × cost.total_bps × degraded_multiplier(cost, mult)`。

    与 `apply_edge_gate`（`edge_gate.py:148`）逐字同构，且 `degraded_multiplier` 是直接 import
    过来的**同一个函数**——绝不在本模块重写一份「`"degraded"` 就乘 1.2」的等价逻辑。
    **刻意不 round**：门禁那一行也没有 round，判定式两侧必须逐字相同（round 只发生在装 wire 时）。
    `cost is None` 或 `k_min <= 0` ⇒ `None`（门禁空转，没有分母可言）。
    """
    if cost is None or k_min <= 0:
        return None
    return k_min * cost.total_bps * degraded_multiplier(cost, degraded_mult)


@dataclass(frozen=True)
class _ConfidenceOnly:
    """只承载 `confidence` 的最小载体：`degraded_multiplier` 读的就只有这一个属性。

    存在的唯一理由是让**运行期那条路径**也走 `edge_gate.degraded_multiplier`，而不是在本模块
    重写一份「`"degraded"` 就乘 1.2」的等价逻辑——[P0-1] 要钉死的正是这一点，而探针只持有
    标量读数（`total_bps` + `confidence`），手上没有一个完整的 `CostBreakdown` 可以直接转发。
    """

    confidence: str


def _required_from_readings(cost_bps: float | None, confidence: str, *,
                            k_min: float, degraded_mult: float) -> float | None:
    """标量口径的分母（与 `required_bps` **同一条式子、同一个收紧倍数函数**）。

    收紧倍数一律经 `edge_gate.degraded_multiplier` 取（别人拥有的值域必须单点转发）：在这里
    照抄一份 `confidence == "degraded"` 的判断，就是把 [P0-1] 修好的那条漂移原地再造一遍
    ——门禁那侧将来多一个降级态时，探针会静默地继续按老口径算。
    """
    cost = _finite(cost_bps)
    if cost is None or k_min <= 0:
        return None
    return k_min * cost * degraded_multiplier(_ConfidenceOnly(str(confidence)), degraded_mult)


def evaluate(inputs: TradabilityInputs, *, infeasible_rate: float, marginal_rate: float,
             bar: str = "", inst_id: str = "",
             alternatives: tuple[dict, ...] = ()) -> TradabilityVerdict:
    """判定四档 level + 装配中文 headline / advice；**判定一律走市价口径**（保守）。

    Args:
        inputs: 已算好的 ATR 序列与两个成本读数。
        infeasible_rate: `ai_trade_tradability_infeasible_veto_rate`（默认 0.80）。
        marginal_rate: `ai_trade_tradability_marginal_veto_rate`（默认 0.40）。
        bar: 当前执行周期（仅文案用）。
        inst_id: 当前标的（仅文案用）。
        alternatives: 更长周期的探测结果 `[{bar, vetoRateMarket, costBpsMarket, level}]`
            （由调用方按**同一个**探针逐周期算出，每项用它自己的 `bar_seconds` 重算过成本）。

    Returns:
        `TradabilityVerdict`；样本不足或市价成本缺席 ⇒ `level == "unknown"`（**绝不**回落 `ok`）。
    """
    samples = len(inputs.atr_bps)
    if samples < _MIN_SAMPLES or inputs.cost_bps_market is None:
        return TradabilityVerdict(level="unknown", samples=samples,
                                  headline=_unknown_headline(samples))
    mult = max(0.0, float(inputs.atr_target_mult))
    req_market = _required_from_readings(inputs.cost_bps_market, inputs.cost_confidence_market,
                                         k_min=inputs.k_min, degraded_mult=inputs.degraded_mult)
    req_limit = _required_from_readings(inputs.cost_bps_limit, inputs.cost_confidence_limit,
                                        k_min=inputs.k_min, degraded_mult=inputs.degraded_mult)
    veto_market = veto_rate(inputs.atr_bps, mult, req_market)
    veto_limit = veto_rate(inputs.atr_bps, mult, req_limit)
    stops = _stop_series(inputs)
    verdict = TradabilityVerdict(
        level=_level(veto_market, infeasible_rate=infeasible_rate, marginal_rate=marginal_rate),
        samples=samples,
        atr_p10=_pct(inputs.atr_bps, 0.10), atr_p50=_pct(inputs.atr_bps, 0.50),
        atr_p90=_pct(inputs.atr_bps, 0.90),
        required_bps_market=_round(req_market), required_bps_limit=_round(req_limit),
        veto_rate_market=veto_market, veto_rate_limit=veto_limit,
        stop_bps_p50=_pct(stops, 0.50),
        stop_inside_cost_rate_market=_inside_rate(stops, inputs.cost_bps_market),
        stop_inside_cost_rate_limit=_inside_rate(stops, inputs.cost_bps_limit),
        cost_bps_market=inputs.cost_bps_market, cost_bps_limit=inputs.cost_bps_limit,
        cost_confidence_market=inputs.cost_confidence_market,
        cost_confidence_limit=inputs.cost_confidence_limit,
        # 下发的是**真的乘进 required 的那个数**，故同样经 `degraded_multiplier` 取——
        # 这里另写一份判断，前端解释「阈值为什么是这个数」时就会与判定式说两套话。
        degraded_mult_applied=degraded_multiplier(
            _ConfidenceOnly(str(inputs.cost_confidence_market)), inputs.degraded_mult))
    headline = build_headline(verdict, bar=bar, inst_id=inst_id)
    return TradabilityVerdict(**{**verdict.__dict__, "headline": headline,
                                 "advice": build_advice(verdict, alternatives, bar=bar)})


def veto_rate(atr_bps, atr_target_mult: float, required: float | None) -> float | None:
    """被否比例 = `|{ i : atr_bps[i] × mult < required }| / samples`；分母缺席 ⇒ `None`。

    **它是一个条件概率**：分母是窗口内**全部** K 线断面，而门禁只在开仓意图轮上判定。它回答
    「若在这一根上想开仓，会不会被否」，**不是**「模型的开仓意图里有多大比例被否」（后者是轴 C
    漏斗的 `blocked["edgeGate"] / intents`）。两个数会不一样，而且都对——文案两处都必须带口径词。
    """
    rows = [value for value in (_finite(v) for v in atr_bps) if value is not None]
    if not rows or required is None:
        return None
    # `round(..., _ROUND)` 与 `estimate_gross_edge_bps` 对 `target_bps` 的 round 逐字同构
    # （`edge_gate.py:125`），否则边界断面上探针与门禁会在第 7 位小数上给出相反结论（G-2）。
    blocked = sum(1 for value in rows if round(value * atr_target_mult, _ROUND) < required)
    return round(blocked / len(rows), _ROUND)


def _level(veto_market: float | None, *, infeasible_rate: float,
           marginal_rate: float) -> str:
    """四档判定；`veto_market is None`（门禁空转 / 成本缺席）⇒ `unknown`，**绝不**回落 `ok`。"""
    if veto_market is None:
        return "unknown"
    if veto_market >= infeasible_rate:
        return "infeasible"
    if veto_market >= marginal_rate:
        return "marginal"
    return "ok"


def _stop_series(inputs: TradabilityInputs) -> tuple[float, ...]:
    """`stop_atr_mult × ATR(bps)` 序列；`stop_atr_mult <= 0` ⇒ 空元组（该判据**整体缺席**）。"""
    mult = float(inputs.stop_atr_mult)
    if mult <= 0:
        return ()
    return tuple(value * mult for value in (_finite(v) for v in inputs.atr_bps)
                 if value is not None)


def _inside_rate(stops: tuple[float, ...], cost_bps: float | None) -> float | None:
    """止损距离落进往返成本带以内的比例；无止损序列 / 成本缺席 ⇒ `None`（**不填 0 冒充**）。"""
    if not stops or cost_bps is None or not _finite(cost_bps):
        return None
    return round(sum(1 for value in stops if value < float(cost_bps)) / len(stops), _ROUND)


def _unknown_headline(samples: int) -> str:
    """`unknown` 的一句话：说清「算不出」而不是暗示「不能交易」（fail-open 的文案面）。"""
    if samples <= 0:
        return "暂时算不出可交易性：行情不可达且本地没有该周期的缓存 K 线。这不影响启动。"
    return (f"暂时算不出可交易性：只取到 {samples} 根 K 线（少于 {_MIN_SAMPLES} 根的最小样本）。"
            "这不影响启动。")


def build_headline(v: TradabilityVerdict, *, bar: str, inst_id: str = "") -> str:
    """一句话结论（**后端下发、前端一个字都不重写**）。

    句子里必须写进「最近 {samples} 根」——附录 A 的实测表用的是本机缓存全量（39764 根 BTC 1m），
    与线上探针窗口（默认 200 根）**不是同一口径**，两个数字并排出现时极易被读成
    「200 根就复算出了 98.6%」（[P2-4]）。
    """
    if v.level == "unknown":
        return v.headline or _unknown_headline(v.samples)
    who = f"{inst_id} " if inst_id else ""
    head = (f"在 {who}{bar} 周期上，最近 {v.samples} 根 K 线的 ATR 中位数约 "
            f"{_bps(v.atr_p50)}，而一笔市价往返成本约 {_bps(v.cost_bps_market)}")
    if v.required_bps_market is None:
        return (f"{head}；本会话的最小 edge 门禁未启用（k=0），故没有据以否决开仓的分母。")
    tail = (f"；最小 edge 门禁要求 ≥ {_bps(v.required_bps_market)}，据此约 "
            f"{_rate(v.veto_rate_market)} 的开仓意图会被否决")
    degraded = ("（成本估算处于降级态，门禁阈值已按 "
                f"{v.degraded_mult_applied:g}× 收紧）"
                if v.cost_confidence_market == "degraded" and v.degraded_mult_applied else "")
    return f"{head}{tail}{degraded}。"


def build_advice(v: TradabilityVerdict, alternatives: tuple[dict, ...] = (), *,
                 bar: str = "") -> str | None:
    """对症建议（有序规则链，第一条命中即返回）；`level == "ok"` / `"unknown"` ⇒ `None`。

    第 1 条之所以首推**换周期**而不是换预设，依据是本轮实测：同一批 BTC 1m K 线上，最宽松的
    `aggressive`（k=1.0）仍有 93.0% 的否决率——「改选更宽松的预设」在这个断面上是**错误**的
    补救方向（那正是第 1 轮 `strictness_hint` 给出的建议）。
    """
    if v.level in ("ok", "unknown"):
        return None
    better = _first_feasible(alternatives)
    if v.level == "infeasible":
        if better is not None:
            return (f"建议改用 {better.get('bar')} 或更长的时间级别："
                    f"该周期上约 {_rate(better.get('vetoRateMarket'))} 的开仓意图会被否决"
                    f"（当前 {bar} 上是 {_rate(v.veto_rate_market)}）。"
                    "换预设解决不了——门禁的分母是往返成本，而短周期的单笔波动幅度本身就覆盖"
                    "不了它；放宽 k 只能把这条曲线整体平移一点。")
        return ("本标的在所有可探测的周期上波动率都覆盖不了交易成本，建议换标的或等待波动回升；"
                "也可在弹窗把交易纪律预设调到「关闭」自担风险（不推荐——那等于取消了成本覆盖判据）。")
    tail = (f"；也可改用 {better.get('bar')}（否决率约 {_rate(better.get('vetoRateMarket'))}）"
            if better is not None else "")
    return (f"约 {_rate(v.veto_rate_market)} 的开仓意图会被最小 edge 门禁否决，属可交易但偏紧。"
            f"若希望更活跃可改用「激进」预设（门槛 k 更低）或改用更长周期{tail}。")


def _first_feasible(alternatives: tuple[dict, ...]) -> dict | None:
    """第一个 `level` 非 `infeasible` / `unknown` 的更长周期候选；没有 ⇒ `None`。"""
    for item in alternatives or ():
        if isinstance(item, dict) and item.get("level") in ("ok", "marginal"):
            return item
    return None


def verdict_wire(v: TradabilityVerdict | None, *, inst_id: str = "", bar: str = "",
                 preset: str = "", source: str = "", as_of_ts: int | None = None,
                 alternatives: tuple[dict, ...] = ()) -> dict | None:
    """`TradabilityVerdict` → camelCase 只读 wire（端点 / status / 落库共用同一份装配）。

    缺席语义：任一子块算不出即该键为 `null`（`stopBps` 在 `stop_atr_mult<=0` 时为 `null`），
    **绝不填 0 冒充**。`level == "unknown"` 时除 `headline` / `samples` / `source` 外允许全 `null`。
    """
    if v is None:
        return None
    return {
        "instId": inst_id, "bar": bar, "preset": preset,
        "level": v.level, "samples": v.samples, "source": source or None,
        "asOfTs": as_of_ts,
        "atrBps": (None if v.atr_p50 is None
                   else {"p10": v.atr_p10, "p50": v.atr_p50, "p90": v.atr_p90}),
        "costBps": {"market": v.cost_bps_market, "limit": v.cost_bps_limit},
        "costConfidence": {"market": v.cost_confidence_market,
                           "limit": v.cost_confidence_limit},
        "degradedMultApplied": v.degraded_mult_applied,
        "requiredBps": {"market": v.required_bps_market, "limit": v.required_bps_limit},
        "vetoRate": {"market": v.veto_rate_market, "limit": v.veto_rate_limit},
        "stopBps": (None if v.stop_bps_p50 is None else {"p50": v.stop_bps_p50}),
        "stopInsideCostRate": {"market": v.stop_inside_cost_rate_market,
                               "limit": v.stop_inside_cost_rate_limit},
        "alternatives": list(alternatives),
        "headline": v.headline, "advice": v.advice,
        "disclaimer": DISCLAIMER.format(samples=v.samples),
    }


def status_wire(wire: dict | None) -> dict | None:
    """会话 `status.tradability` 的**瘦身**投影（面板只需要四个键 + `asOfTs`）。"""
    if not wire:
        return None
    return {"level": wire.get("level"), "bar": wire.get("bar"),
            "vetoRateMarket": (wire.get("vetoRate") or {}).get("market"),
            "headline": wire.get("headline"), "advice": wire.get("advice"),
            "samples": wire.get("samples"), "source": wire.get("source"),
            "asOfTs": wire.get("asOfTs")}


def stats_wire(wire: dict | None) -> dict | None:
    """`metricsJson.aiStats.tradability`（§6.2 落**启动断面**）：回看历史时要问的是
    「我当时是在什么条件下开的这一局」，而不是「现在的行情如何」。"""
    if not wire:
        return None
    return {"level": wire.get("level"), "bar": wire.get("bar"),
            "vetoRateMarket": (wire.get("vetoRate") or {}).get("market"),
            "samples": wire.get("samples")}


def _pct(values, q: float) -> float | None:
    """分位数（线性插值，与 numpy 默认一致）；空序列 ⇒ `None`。"""
    rows = sorted(value for value in (_finite(v) for v in values) if value is not None)
    if not rows:
        return None
    if len(rows) == 1:
        return round(rows[0], _ROUND)
    pos = q * (len(rows) - 1)
    low = math.floor(pos)
    high = math.ceil(pos)
    if low == high:
        return round(rows[low], _ROUND)
    return round(rows[low] + (rows[high] - rows[low]) * (pos - low), _ROUND)


def _bps(value: float | None) -> str:
    return "未知" if value is None else f"{value:.1f} bps"


def _rate(value) -> str:
    out = _finite(value)
    return "未知" if out is None else f"{out * 100:.1f}%"


def _round(value: float | None) -> float | None:
    """wire 读数的统一小数位（判定式一律用未 round 的原值，见 `required_bps` 的 docstring）。"""
    return None if value is None else round(value, _ROUND)


def _finite(value) -> float | None:
    """有限数归一：非有限 / 不可转 ⇒ `None`（绝不把 NaN 带进判定）。"""
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None
