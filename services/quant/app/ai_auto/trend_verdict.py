"""执行周期趋势基调的**纯核心**（ai-trade-execution-trend-verdict 轴 A / 轴 A-2）。

## 这一层解决什么

《审议协议》的第一步此前锚在**高周期**（S1 = 1D → 12H → 4H → 1H，S2 被定义为「对 S1 的附议或
异议」，S3 明令战术周期不得单独反转 S1）。用户选 15m 启动自动交易，拿到的「整体基调」里却
**一根 15m K 线都没有**（`multi_timeframe_context._CONTEXT_POLICY` 是「严格高于执行周期」）。
于是一段以周为尺度的上涨里，每一轮的第一步都得到「整体=bullish」——「一直在买多」不是模型的
偏好，是协议的结构性产物。

本模块在**用户所选执行周期**上算出一份确定性的《趋势基调》：方向、强度、两个连续分、逐成分
可解释的证据。高周期与战术周期只作**调制项**，权重被硬性封顶且执行周期可用权重有独立下界，
**永远无法翻转执行周期的结论，也永远不能抬高或压低它的强度**。

## 入参是四份**已经算好**的 wire，不是 DataFrame

零新增计算（不再开第二个因果窗口、不再跑一遍 `classify_regime`）、零口径漂移（同一个量在两处
各算一次，一旦漂移就会出现「窗口在看 A、prompt 却在讲 B」且不报任何错）、实盘与回测天然同源。

## 三条不可动摇的不变式（改本文件前先读）

- **I-1 执行周期主导性**：任何 `degraded=False` 的基调，其执行周期可用权重必
  ≥ `_MIN_EXEC_WEIGHT`(0.5) > `_HTF_WEIGHT_CAP`(0.30) ≥ `rule.htf_weight`；因此「执行周期各项
  一致看空 + 高周期满分看多」的 `score` 必然 < 0。这是**构造性**保证（评审 RP1-7）：v1 只用
  `_HTF_WEIGHT_CAP` 论证，那只覆盖「执行周期五项全在」的情形——只剩 `regime`(0.26) 可用而
  `htf`(0.30) 在场时，高周期权重反而**大于**执行周期可用权重，合成分当场被高周期决定。
- **I-1b 强度不含高周期**：`strength` 只由 `|execScore|` 判。把 `htf` 从 `+1` 改到 `−1`
  **必须**保持 `strength` 与 `execScore` 逐字不变。理由见评审 RP0-3：`strength` 是轴 D 三条
  通路唯一的触发条件，让高周期参与它，等于让高周期在本需求的目标断面（日线看多、15m 翻空）
  上一键关停整个特性。
- **I-2 符号对称性**：把所有输入的方向取反，产出的 `score` 与 `execScore` 必须**严格等于**
  原值取负，`direction` / `bias` / `strength` 严格镜像。
- **I-3 只读**：本模块不持任何状态、不接受任何会话对象、不产生任何 degrade 码之外的副作用。
  所有会话级计数住在 `trend_runtime.TrendRuntime`。

## 三条实现期必踩的坑（照本模块写法实现，否则 I-2 必红）

1. **`ema` 成分不得读 `emaRelation` 标签**。`market_digest._ema_structure` 的
   `relation = "fast_above" if fast_last >= slow_last else "fast_below"`——**相等时判多头**，
   是一个真实存在的（极小的）单向阀。必须按 `emaFast − emaSlow` 的**数值符号**打分。
2. **`momentum` 成分不得读 `macdHist.label`**。`_macd_reading` 的
   `label = "bullish" if last > 0 else "bearish"`——**hist 恰为 0 时判空**，同样不对称。
3. **缺失成分不得按 0 计入分母**。把「不知道」当成「中性」会系统性地把分数往 0 稀释，表现是
   「数据越缺，基调越像 range」，而那正是本仓库明令禁止的「把没有的事实画出来」。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, replace

from .decider import TradeDecision
from .signals import quantize_size_fraction

# 开仓动作枚举（与 `open_funnel._OPEN_ACTIONS` / `signals._OPEN_ACTIONS` 同值）。
_OPEN_ACTIONS = ("open_long", "open_short")

# 顺势 / 逆势 / 无基调的三态。**注意它与统计桶名的映射**（评审 RP2-5）：`TrendCounters` 的第三格
# 叫 `unknown`（与 `open_funnel.SIDES` 同姿态），承接「本轮确实是一次开仓意愿，但方向不可辨或
# 基调不可用」的那一格；`close` / `hold` 这类**根本不是开仓意愿**的轮次压根不进那张表。
ALIGNMENTS = ("aligned", "counter", "neutral")

# 逆势降档闸的两个细分降级码（前缀 `counter_trend_`，`open_funnel` / `discipline_runtime` 各登记一条）。
REASON_COUNTER_CONFIDENCE = "counter_trend_confidence"
REASON_COUNTER_SIZE = "counter_trend_size"

# 顺势放宽通路的五个 lane（`consensus` wire 的 `trendBonusLane`，前端按它解释「为什么没放宽」）。
BONUS_LANE_NONE = "none"
BONUS_LANE_APPLIED = "applied"
BONUS_LANE_NOT_ALIGNED = "blocked_not_aligned"
BONUS_LANE_CONFLICT = "blocked_conflict"
BONUS_LANE_NO_NATIVE_VOTE = "blocked_no_native_vote"

# —— 打分常量。**刻意不做成 `AI_TRADE_` env**，沿用 `market_digest.py` 顶部「指标周期与阈值为
# 模块级常量，规避三处联动」的成文姿态：它们是打分函数的形状，不是运维旋钮。——
_REGIME_FLOOR = 0.35        # regime 表了态但 confidence 很低时的分数地板
_SLOPE_FULL = 0.6           # emaSlopePct 打满的绝对值（%）
_CHANGE_FULL_PCT = 2.0      # changePct 打满的绝对值（%）
_CONSEC_FULL = 4.0          # 连阳/连阴差打满的根数
_ROC_FULL = 3.0             # rocPct 打满的绝对值（%）
_MIN_AVAILABLE_WEIGHT = 0.5  # 全部成分的可用权重占比下界，低于它整段判 degraded
_MIN_EXEC_WEIGHT = 0.5      # **执行周期五项**的可用权重下界（评审 RP1-7；I-1 的构造性保证）
_HTF_WEIGHT_CAP = 0.30      # 高周期调制项权重的硬封顶（解析点夹紧，保 I-1）

# 执行周期五项的权重（和恒为 1.00）。顺序即 wire 里 `components` 的顺序（前端只做布局）。
_EXEC_WEIGHTS: tuple[tuple[str, str, float], ...] = (
    ("regime", "体制结构", 0.26),
    ("ema", "均线结构", 0.22),
    ("netChange", "净变化", 0.20),
    ("structure", "区间与连阳连阴", 0.16),
    ("momentum", "动量", 0.16),
)
_HTF_LABEL = "高周期背景"

# wire 的小数位。判定一律用**全精度**，只有落进 wire 的读数才 round——`verdictConfidence`
# 取的是未舍入的 `|score|`（spec §3.2.4 的算例即按此口径自洽）。
_ROUND = 2


@dataclass(frozen=True)
class TrendVerdictRule:
    """本会话的趋势基调配置（frozen，会话生命周期内不变）。

    四把独立总闸的语义边界（§6.3，改本类前先读）：

    - 关掉 `enabled` ⇒ 另外三把无论开关一律不生效（它们的判据全部来自 verdict），且 payload /
      prompt / 协议（含复核侧 R4）/ 门禁 / trace / 统计**逐字节回退**；
    - 只关 `counter_gate_enabled` ⇒ 基调仍进 prompt 与统计，只是不再拦任何一笔（**收紧**通路关闭）；
    - 只关 `consensus_vote_enabled` / `contrarian_allowance_enabled` ⇒ 对应的**放宽**通路关闭。

    三者的极性必须分开记：`counter_gate` 是唯一会**减少**成交的闸，另两把是唯一会**增加**成交
    的通路。把它们绑在一把总闸上，运维就无法只回退「变宽」的那一半。
    """

    enabled: bool = False                    # 总闸；False ⇒ `build_trend_verdict` 恒返回 None
    enter_threshold: float = 0.22            # |score| ≥ 该值才给方向（对称死区）
    strong_threshold: float = 0.55           # **|execScore|** ≥ 该值记 strong（RP0-3：不含高周期）
    htf_weight: float = 0.18                 # 高周期调制项权重（**被封顶**，见 I-1）
    counter_gate_enabled: bool = True        # 轴 D-2 逆势降档闸
    counter_min_confidence: float = 0.65     # 逆势开仓的**模型自报**置信度下界（RP1-8）
    counter_size_mult: float = 0.5           # 逆势开仓的强制降档倍数
    consensus_vote_enabled: bool = True      # 轴 D-1a 顺势追加一票
    contrarian_allowance_enabled: bool = True  # 轴 D-1b 顺势放宽逆势通路反向票上限（RP0-2）


def trend_rule_from_settings(settings) -> TrendVerdictRule:
    """settings → `TrendVerdictRule` 的**唯一**转换点（镜像 `signals.consensus_rule_from_settings`）。

    ⚠️ 传进来的必须是 `_resolve_discipline` **之后**那份派生 settings 副本
    （`executor.settings` / `runner.settings`）：读进程级单例会拿到一组死字母阈值
    （`api/ai_auto.py` 的 [P1-2] / G-13 已把这个顺序钉死过一次）。

    两处**夹紧**（不抛异常——一个拼错的运维开关不该让会话起不来）：

    - `strong_threshold` 夹到 `> enter_threshold + 0.05`：两个阈值倒挂时 `strength` 语义会崩；
    - `htf_weight` 封顶到 `_HTF_WEIGHT_CAP`，保 I-1。

    Args:
        settings: `app.config.Settings`（只读十个 `ai_trade_trend_*` 键）。

    Returns:
        本会话的 `TrendVerdictRule`。
    """
    enter = float(getattr(settings, "ai_trade_trend_enter_threshold", 0.22))
    strong = float(getattr(settings, "ai_trade_trend_strong_threshold", 0.55))
    return TrendVerdictRule(
        enabled=bool(getattr(settings, "ai_trade_trend_verdict_enabled", False)),
        enter_threshold=enter,
        strong_threshold=max(strong, enter + 0.05),
        htf_weight=min(float(getattr(settings, "ai_trade_trend_htf_weight", 0.18)),
                       _HTF_WEIGHT_CAP),
        counter_gate_enabled=bool(
            getattr(settings, "ai_trade_trend_counter_gate_enabled", True)),
        counter_min_confidence=float(
            getattr(settings, "ai_trade_trend_counter_min_confidence", 0.65)),
        counter_size_mult=float(getattr(settings, "ai_trade_trend_counter_size_mult", 0.5)),
        consensus_vote_enabled=bool(getattr(settings, "ai_trade_trend_consensus_vote", True)),
        contrarian_allowance_enabled=bool(
            getattr(settings, "ai_trade_trend_contrarian_allowance", True)))


@dataclass(frozen=True)
class TrendInputs:
    """`build_trend_verdict` 的聚合入参（守 CLAUDE.md「函数形参 ≤ 5」）。

    四份 wire 全部**已经在 payload 里**（`session._build_decision_payload` 与
    `backtest._build_payload` 各自都已装配），本模块只读不算。

    Attributes:
        execution_bar: 用户所选级别，判定所在的级别（wire 里的 `bar`）。
        regime: `payload["marketContext"]`。
        digest: `payload["marketDigest"]`。
        multi_timeframe: `payload["multiTimeframeContext"]`（只参与 `htf` 调制项）。
        tactical: `payload["tacticalContext"]`（**只参与 conflict，不参与打分**；回测侧恒缺席）。
        as_of_ts: 判定瞬刻的时间戳（决策 payload 的 `barTs` 同源）。
    """

    execution_bar: str
    regime: dict | None = None
    digest: dict | None = None
    multi_timeframe: dict | None = None
    tactical: dict | None = None
    as_of_ts: int = 0


@dataclass(frozen=True)
class _Component:
    """一个成分的打分结果（`score is None` = 该成分不可用，**绝不补猜**）。"""

    key: str
    label: str
    weight: float
    score: float | None
    detail: str


def _finite(value) -> float | None:
    """把入参归一为有限实数；非数 / NaN / Inf ⇒ `None`（判据一律「无法验证 ≠ 合规」）。"""
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _clamp(value: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(value, hi))


def _sign(value: float) -> float:
    """严格奇函数的符号（`0 → 0.0`）——**不得**写成 `1 if x >= 0 else -1`（那是一个单向阀）。"""
    if value > 0:
        return 1.0
    if value < 0:
        return -1.0
    return 0.0


def _blend(terms: list[tuple[float, float]]) -> float | None:
    """子项按权重合成并**按可用权重归一**；一个子项都不可用 ⇒ `None`（成分整体缺失）。

    Args:
        terms: `[(weight, score), ...]`，只含**实际可用**的子项。

    Returns:
        合成分 ∈ [-1, 1]，或 `None`。
    """
    if not terms:
        return None
    total = sum(w for w, _ in terms)
    if total <= 0:
        return None
    return _clamp(sum(w * s for w, s in terms) / total)


# ============================ 六个成分的打分（全部为奇函数）============================
def _score_regime(regime: dict | None) -> _Component:
    """体制结构：`trend_up → +max(conf, _REGIME_FLOOR)`；`trend_down → −…`；`range → 0.0`。"""
    key, label, weight = _EXEC_WEIGHTS[0]
    primary = str((regime or {}).get("primary") or "")
    if primary not in ("trend_up", "trend_down", "range"):
        return _Component(key, label, weight, None, "marketContext 缺席或未给出体制判定")
    if primary == "range":
        return _Component(key, label, weight, 0.0, "primary=range，体制不表态")
    conf = _finite((regime or {}).get("confidence"))
    magnitude = max(abs(conf) if conf is not None else 0.0, _REGIME_FLOOR)
    score = magnitude if primary == "trend_up" else -magnitude
    shown = "未知" if conf is None else f"{abs(conf):.2f}"
    return _Component(key, label, weight, _clamp(score),
                      f"primary={primary}，判定置信 {shown}")


def _score_ema(price_action: dict | None) -> _Component:
    """均线结构：`0.5·sign(emaFast−emaSlow) + 0.5·clamp(emaSlopePct/_SLOPE_FULL)`。

    ⚠️ **绝不读 `emaRelation`**（相等时它判多头，是一个单向阀，见模块 docstring 坑 1）。
    """
    key, label, weight = _EXEC_WEIGHTS[1]
    pa = price_action or {}
    fast, slow = _finite(pa.get("emaFast")), _finite(pa.get("emaSlow"))
    slope = _finite(pa.get("emaSlopePct"))
    terms: list[tuple[float, float]] = []
    notes: list[str] = []
    if fast is not None and slow is not None:
        terms.append((0.5, _sign(fast - slow)))
        notes.append(f"快线{'高于' if fast > slow else ('低于' if fast < slow else '等于')}慢线")
    if slope is not None:
        terms.append((0.5, _clamp(slope / _SLOPE_FULL)))
        notes.append(f"快线斜率 {slope:+.3f}%")
    score = _blend(terms)
    return _Component(key, label, weight, score,
                      "、".join(notes) if notes else "emaFast/emaSlow 与斜率均不可用")


def _score_net_change(price_action: dict | None) -> _Component:
    """净变化：`clamp(changePct / _CHANGE_FULL_PCT)`。"""
    key, label, weight = _EXEC_WEIGHTS[2]
    change = _finite((price_action or {}).get("changePct"))
    if change is None:
        return _Component(key, label, weight, None, "changePct 不可用")
    return _Component(key, label, weight, _clamp(change / _CHANGE_FULL_PCT),
                      f"窗口净变化 {change:+.2f}%")


def _score_structure(price_action: dict | None) -> _Component:
    """区间与连阳连阴：`0.5·(2·rangePos−1) + 0.5·clamp((consecUp−consecDown)/_CONSEC_FULL)`。"""
    key, label, weight = _EXEC_WEIGHTS[3]
    pa = price_action or {}
    range_pos = _finite(pa.get("rangePos"))
    up, down = _finite(pa.get("consecUp")), _finite(pa.get("consecDown"))
    terms: list[tuple[float, float]] = []
    notes: list[str] = []
    if range_pos is not None:
        terms.append((0.5, _clamp(2.0 * _clamp(range_pos, 0.0, 1.0) - 1.0)))
        notes.append(f"区间位置 {range_pos:.2f}")
    if up is not None and down is not None:
        terms.append((0.5, _clamp((up - down) / _CONSEC_FULL)))
        notes.append(f"连阳 {int(up)} / 连阴 {int(down)}")
    score = _blend(terms)
    return _Component(key, label, weight, score,
                      "、".join(notes) if notes else "rangePos 与连阳连阴均不可用")


def _score_momentum(factors: dict | None) -> _Component:
    """动量：`0.5·sign(macdHist.value) + 0.5·clamp(rocPct.value/_ROC_FULL)`。

    ⚠️ **绝不读 `macdHist.label`**（hist 恰为 0 时它判空，见模块 docstring 坑 2）。
    """
    key, label, weight = _EXEC_WEIGHTS[4]
    data = factors or {}
    hist = _finite((data.get("macdHist") or {}).get("value"))
    roc = _finite((data.get("rocPct") or {}).get("value"))
    terms: list[tuple[float, float]] = []
    notes: list[str] = []
    if hist is not None:
        terms.append((0.5, _sign(hist)))
        notes.append(f"MACD 柱 {hist:+.6g}")
    if roc is not None:
        terms.append((0.5, _clamp(roc / _ROC_FULL)))
        notes.append(f"ROC {roc:+.2f}%")
    score = _blend(terms)
    return _Component(key, label, weight, score,
                      "、".join(notes) if notes else "macdHist 与 rocPct 均不可用")


def _score_htf(multi_timeframe: dict | None, weight: float) -> _Component:
    """高周期背景（**调制项**）：`bullish → +1`；`bearish → −1`；`mixed`/`range` → `0`。

    `insufficient` / 整段缺席 ⇒ **该成分缺失**（绝不当成中性 0 计入分母）。
    """
    alignment = ((multi_timeframe or {}).get("trendAlignment") or {})
    status = str(alignment.get("status") or "")
    table = {"bullish": 1.0, "bearish": -1.0, "mixed": 0.0, "range": 0.0}
    if status not in table:
        return _Component("htf", _HTF_LABEL, weight, None,
                          "多周期证据缺席或不足，本轮不作调制")
    bars = alignment.get("bullishBars") if table[status] > 0 else alignment.get("bearishBars")
    tail = f"（{'/'.join(bars)}）" if bars else ""
    return _Component("htf", _HTF_LABEL, weight, table[status],
                      f"整体={status}{tail}；仅作调制、不参与强度")


def _components(inputs: TrendInputs, rule: TrendVerdictRule) -> list[_Component]:
    """六个成分的固定顺序打分（缺失项也在列，`score=None`）。"""
    price_action = (inputs.digest or {}).get("priceAction")
    factors = (inputs.digest or {}).get("factors")
    return [
        _score_regime(inputs.regime),
        _score_ema(price_action),
        _score_net_change(price_action),
        _score_structure(price_action),
        _score_momentum(factors),
        _score_htf(inputs.multi_timeframe, rule.htf_weight),
    ]


# ============================ 合成与判定 ============================
def _weighted(rows: list[_Component]) -> tuple[float | None, float]:
    """`(Σw·s / Σw, Σw)`；一个可用成分都没有 ⇒ `(None, 0.0)`。缺失成分**不进分母**。"""
    live = [row for row in rows if row.score is not None and row.weight > 0]
    total = sum(row.weight for row in live)
    if total <= 0:
        return None, 0.0
    return sum(row.weight * float(row.score) for row in live) / total, total


def _conflict(bias: str, htf: _Component, tactical_alignment: str | None) -> str:
    """`"none" | "htf_conflict" | "tactical_conflict" | "both"`。

    冲突的作用被收敛为「**只收紧、不放松**」（评审 RP0-3）：它只撤销 D-1a / D-1b 的放宽，
    **绝不**降低 `strength`、**绝不**豁免 D-2 的逆势门槛，也**绝不**翻转 `direction`。
    """
    htf_conflict = bool(
        bias != "neutral" and htf.score is not None and htf.score != 0.0
        and ((htf.score > 0) != (bias == "long")))
    tactical_conflict = tactical_alignment == "diverged"
    if htf_conflict and tactical_conflict:
        return "both"
    if htf_conflict:
        return "htf_conflict"
    if tactical_conflict:
        return "tactical_conflict"
    return "none"


def _higher_timeframe_wire(multi_timeframe: dict | None, htf: _Component, bias: str) -> dict:
    """`higherTimeframe` 子块：状态 + 是否与本级别基调同向 + 逐周期名单（原样透传，不再判一次）。"""
    alignment = ((multi_timeframe or {}).get("trendAlignment") or {})
    status = alignment.get("status")
    agree: bool | None = None
    if htf.score is not None and htf.score != 0.0 and bias != "neutral":
        agree = (htf.score > 0) == (bias == "long")
    return {"status": status, "agree": agree,
            "bullishBars": list(alignment.get("bullishBars") or []),
            "bearishBars": list(alignment.get("bearishBars") or [])}


def _degraded_wire(inputs: TrendInputs, rows: list[_Component], shares: tuple[float, float],
                   reason: str) -> dict:
    """降级态的完整 wire——**返回对象而不是 None**：「算不出来」与「没装配」是两个不同的事实。"""
    available, exec_available = shares
    return {
        "bar": inputs.execution_bar, "asOfTs": int(inputs.as_of_ts or 0),
        "score": None, "execScore": None,
        "direction": "unknown", "bias": "neutral", "strength": "weak",
        "verdictConfidence": None,
        "components": [{"key": row.key, "label": row.label,
                        "score": None if row.score is None else round(row.score, _ROUND),
                        "weight": row.weight, "detail": row.detail} for row in rows],
        "missing": [row.key for row in rows if row.score is None],
        "availableWeight": round(available, _ROUND),
        "execAvailableWeight": round(exec_available, _ROUND),
        "higherTimeframe": {"status": None, "agree": None,
                            "bullishBars": [], "bearishBars": []},
        "tacticalAlignment": (inputs.tactical or {}).get("alignment"),
        "conflict": "none", "bonusWithheld": True,
        "counterGate": {"armed": False, "minConfidence": None, "sizeMult": None},
        "degraded": True, "degradeReason": reason,
    }


def build_trend_verdict(inputs: TrendInputs, rule: TrendVerdictRule) -> dict | None:
    """《本轮趋势基调》的**唯一**真源（前端只做布局，一个判据都不复制）。

    判定顺序（顺序即语义，改前先读 §3.2.4）：

    1. `rule.enabled` 为假 ⇒ **返回 `None`**（不是空 dict）⇒ payload 整键缺席 ⇒ prompt / trace /
       门禁全链路**逐字节回退**到本特性合入前。
    2. `availableWeight < _MIN_AVAILABLE_WEIGHT` **或** `execAvailableWeight < _MIN_EXEC_WEIGHT`
       ⇒ `degraded`。第二个判据是 I-1 的构造性保证：过了它，执行周期可用权重必 ≥ 0.50 >
       `htf_weight` 的封顶 0.30。
    3. `direction` / `bias` 取自 **`score`**（含 htf）：高周期因此可以把一个边缘方向拉回死区，
       但**翻不动符号**。
    4. `strength` 取自 **`|execScore|`**（不含 htf，评审 RP0-3）——它是轴 D 三条通路唯一的触发
       条件，让高周期参与它，等于让高周期在目标断面上一键关停整个特性。
    5. `conflict` **只收紧、不放松**：撤销 D-1a / D-1b 的放宽（`bonusWithheld`），**不**降
       `strength`、**不**豁免 D-2。

    Args:
        inputs: 四份已算好的 wire + 执行周期名 + 判定瞬刻。
        rule: 本会话的趋势基调配置。

    Returns:
        camelCase 只读 wire（§3.2.4）；总闸关 ⇒ `None`。
    """
    if not rule.enabled:
        return None
    rows = _components(inputs, rule)
    exec_rows = rows[:-1]
    htf = rows[-1]
    exec_score, exec_weight = _weighted(exec_rows)
    score, _ = _weighted(rows)
    all_weight = sum(row.weight for row in rows)
    available = (exec_weight + (htf.weight if htf.score is not None else 0.0)) / all_weight \
        if all_weight > 0 else 0.0
    exec_available = exec_weight / sum(w for _, _, w in _EXEC_WEIGHTS)
    if (available < _MIN_AVAILABLE_WEIGHT or exec_available < _MIN_EXEC_WEIGHT
            or score is None or exec_score is None):
        return _degraded_wire(inputs, rows, (available, exec_available),
                              "insufficient_components")
    direction, bias = _direction_of(score, rule)
    strength = _strength_of(exec_score, rule)
    tactical_alignment = (inputs.tactical or {}).get("alignment")
    conflict = _conflict(bias, htf, tactical_alignment)
    return {
        "bar": inputs.execution_bar, "asOfTs": int(inputs.as_of_ts or 0),
        "score": round(score, _ROUND), "execScore": round(exec_score, _ROUND),
        "direction": direction, "bias": bias, "strength": strength,
        # **纯呈现**，绝不参与轴 D 的任何判定（RP1-8）；判据一律取 `decision.confidence`。
        "verdictConfidence": round(min(1.0, abs(score) / rule.strong_threshold), _ROUND),
        "components": [{"key": row.key, "label": row.label,
                        "score": None if row.score is None else round(row.score, _ROUND),
                        "weight": row.weight, "detail": row.detail} for row in rows],
        "missing": [row.key for row in rows if row.score is None],
        "availableWeight": round(available, _ROUND),
        "execAvailableWeight": round(exec_available, _ROUND),
        "higherTimeframe": _higher_timeframe_wire(inputs.multi_timeframe, htf, bias),
        "tacticalAlignment": tactical_alignment,
        "conflict": conflict, "bonusWithheld": conflict != "none",
        # 逆势闸的**生效阈值**随基调一同下发（实施期发现的方案缺陷 F-1，见 spec §实施过程发现的
        # 方案缺陷）：§3.4.2 的渲染样例要写「当前：置信度 ≥0.65，仓位 ×0.5」，而
        # `format_trend_verdict_for_prompt(verdict, tier)` 的签名只吃 verdict——把这两个数写死在
        # 文案里，一个改过 env 的会话就会读到一句假话（本仓库对「绝不写死算例」已有成文纪律）。
        "counterGate": {"armed": bool(rule.counter_gate_enabled),
                        "minConfidence": rule.counter_min_confidence,
                        "sizeMult": rule.counter_size_mult},
        "degraded": False, "degradeReason": None,
    }


def _direction_of(score: float, rule: TrendVerdictRule) -> tuple[str, str]:
    """`score`（含 htf）→ `(direction, bias)`；对称死区，`0` 恒落 `range/neutral`。"""
    if score >= rule.enter_threshold:
        return "up", "long"
    if score <= -rule.enter_threshold:
        return "down", "short"
    return "range", "neutral"


def _strength_of(exec_score: float, rule: TrendVerdictRule) -> str:
    """`|execScore|`（**不含 htf**）→ `strong | moderate | weak`（I-1b）。"""
    magnitude = abs(exec_score)
    if magnitude >= rule.strong_threshold:
        return "strong"
    if magnitude >= rule.enter_threshold:
        return "moderate"
    return "weak"


# ============================ 轴 A-2：顺势 / 逆势与三条调制通路 ============================
def _directional(verdict: dict | None) -> bool:
    """基调是否给出了一个可用的**方向**——`alignment_of` 的判据，**刻意不含强度**。

    ⚠️ 它与 `_usable` 是**两条不同的判据**，不得合并（spec §3.3.1 步骤 ④ 与 §3.3.2 的共享前置
    各自单列了强度那一条，正是因为如此）：强度只是轴 D 三条通路的**触发条件**；顺势 / 逆势
    这个**关系**在 `moderate` 基调上一样成立。把强度并进本判据，一个「偏空·强度中」的轮次开出
    的空单会被归成「无基调」，于是统计卡第 2、4 行（开仓意愿 / 顺势逆势成绩）会把大量本可归属
    的样本冲进 `unknown` 格——而 §7.7-3 的产品验收恰恰要在那两行上读出「空单意愿出现了没有」。
    """
    return bool(verdict and not verdict.get("degraded")
                and verdict.get("bias") in ("long", "short"))


def _usable(verdict: dict | None) -> bool:
    """基调是否强到足以让轴 D 的**三条通路**生效（= `_directional` 且强度为 `strong`）。"""
    return _directional(verdict) and (verdict or {}).get("strength") == "strong"


def alignment_of(action: str, verdict: dict | None) -> str:
    """本轮动作与基调的关系：`aligned` / `counter` / `neutral`。

    `close` / `hold` / `cancel_order` 恒 `neutral`——**平仓永不被本层触碰**（与全仓每一道闸
    「只拦开、不拦平」的极性一致）。`verdict is None` / `bias == "neutral"` / `degraded`
    同样恒 `neutral`。

    **强度不在本函数的判据里**（见 `_directional`）：`moderate` 基调照样给得出顺势 / 逆势，
    轴 D 那三条通路各自再判一次 `strength == "strong"`。本函数还是观测侧（`TrendCounters` 的
    开仓意愿与成绩归属）的分桶判据，两者共用一条关系定义、但触发条件各自独立。

    ⚠️ 本函数是多空差异的**唯一**产生点（不变式 I-4）：轴 D 的三条通路里不允许再出现任何
    `action == "open_short"` 的字面判断，否则我们只是把偏斜换了个方向。
    """
    if action not in _OPEN_ACTIONS or not _directional(verdict):
        return "neutral"
    long_side = action == "open_long"
    return "aligned" if (verdict or {}).get("bias") == ("long" if long_side else "short") \
        else "counter"


@dataclass(frozen=True)
class TrendGateOutcome:
    """一次基调闸判定的完整产物（`reason is None` = 放行）。"""

    decision: TradeDecision
    reason: str | None       # 非 None ⇒ 本轮被降级为 hold
    lane: str                # "exempt" | "aligned" | "counter" | "veto"
    wire: dict               # trace / 注记 / payload 共用同一份只读快照


def _gate_wire(lane: str, decision: TradeDecision, verdict: dict | None,
               rule: TrendVerdictRule, sizes: tuple[float | None, float | None]) -> dict:
    """基调闸的 camelCase 只读快照。

    **两个 confidence 必须各有其名**（评审 RP1-8）：`modelConfidence` 是模型自报值、是本层唯一
    的判据；`verdictConfidence` 纯呈现、**绝不参与判定**。共用一个 `confidence` 键会让取错的那
    一方向相反——取 verdict 的等于「基调越强越容易开逆势单」。
    """
    before, after = sizes
    return {"lane": lane, "alignment": alignment_of(decision.action, verdict),
            "strength": (verdict or {}).get("strength"),
            "score": (verdict or {}).get("score"),
            "execScore": (verdict or {}).get("execScore"),
            "minConfidence": rule.counter_min_confidence,
            "modelConfidence": _finite(decision.confidence),
            "verdictConfidence": (verdict or {}).get("verdictConfidence"),
            "sizeMult": rule.counter_size_mult,
            "sizeBefore": before, "sizeAfter": after,
            "reason": None}


def apply_trend_bias_gate(decision: TradeDecision, verdict: dict | None,
                          rule: TrendVerdictRule, *,
                          position_step: float) -> TrendGateOutcome:
    """轴 D-2：逆势开仓的**模型自报置信度下界 + 强制降档**（顺序即降级码）。

    判定顺序（前四条一律**原对象直通**，`is` 相等）：

    1. `rule.counter_gate_enabled` 为假 ⇒ `exempt`；
    2. 非开仓动作 ⇒ `exempt`（平仓永不被拦）；
    3. `alignment != "counter"` ⇒ `aligned` / `exempt`（`alignment_of` **不含强度**）；
    4. `strength != "strong"` ⇒ `exempt`（闸只在基调强时咬）。`strength` 取自 `|execScore|`，
       与高周期无关（RP0-3）；`conflict` 在这里**不豁免**任何东西（只收紧不放松）。

    然后：① 置信度不达标 ⇒ `counter_trend_confidence`；② 降档后仓位不合法 ⇒ `counter_trend_size`；
    ③ 放行 ⇒ `replace(decision, size_fraction=size)`。

    **②的三判据不得简化成 `size <= 0`**：这是 directional-symmetry 实施期发现的方案缺陷 I-1 的
    同款形状——在 `step=0.5` 这类粗档位上 `0.5×0.5=0.25` 会被量化吸附回 `0.5`，承诺的「强制降档」
    当场失效**且不报任何错**。

    **fail-closed**：`decision.confidence` 非有限、`size_fraction` 缺失一律走否决分支
    （「无法验证」不等于「合规」）。

    Args:
        decision: 本轮决策（回撤刹车之后、共识门禁之前的那一个）。
        verdict: 本轮基调 wire；`None` ⇒ 恒 `exempt`。
        rule: 本会话的趋势基调配置。
        position_step: 仓位档位步长，真源是 `decider.position_step`（keyword-only）。

    Returns:
        `TrendGateOutcome`。
    """
    alignment = alignment_of(decision.action, verdict)
    if not rule.counter_gate_enabled or alignment != "counter":
        lane = "aligned" if alignment == "aligned" else "exempt"
        return TrendGateOutcome(decision, None, lane,
                                _gate_wire(lane, decision, verdict, rule, (None, None)))
    # ④ **闸只在基调强时咬**（spec §3.3.1 的第 4 步，单列的一条判据）。它必须写在这里而不是
    # 藏进 `alignment_of`：那个函数同时是观测侧的分桶判据，把强度并进去会让「偏空·强度中」的
    # 逆势开仓在统计卡上变成「无基调」。`strength` 取自 `|execScore|`，与高周期无关（RP0-3）；
    # `conflict` 在这里**不豁免**任何东西（只收紧不放松）。
    if (verdict or {}).get("strength") != "strong":
        return TrendGateOutcome(decision, None, "exempt",
                                _gate_wire("exempt", decision, verdict, rule, (None, None)))
    confidence = _finite(decision.confidence)
    if confidence is None or confidence < rule.counter_min_confidence:
        shown = "未知" if confidence is None else f"{confidence:.2f}"
        reason = (f"{REASON_COUNTER_CONFIDENCE}(score={(verdict or {}).get('execScore')},"
                  f"conf={shown}/{rule.counter_min_confidence:.2f})")
        return _veto(decision, reason, verdict, rule)
    proposed = float(_finite(decision.size_fraction) or 0.0)
    size = quantize_size_fraction(proposed * rule.counter_size_mult, position_step)
    shrink_requested = rule.counter_size_mult < 1.0
    if size <= 0 or size > proposed or (shrink_requested and size >= proposed):
        reason = f"{REASON_COUNTER_SIZE}(score={(verdict or {}).get('score')})"
        return _veto(decision, reason, verdict, rule)
    final = replace(decision, size_fraction=size)
    return TrendGateOutcome(final, None, "counter",
                            _gate_wire("counter", final, verdict, rule, (proposed, size)))


def _veto(decision: TradeDecision, reason: str, verdict: dict | None,
          rule: TrendVerdictRule) -> TrendGateOutcome:
    """把开仓硬降级为 hold（构造出的 hold 与其余各闸的否决产物逐字同形）。"""
    wire = _gate_wire("veto", decision, verdict, rule,
                      (_finite(decision.size_fraction), None))
    wire["reason"] = reason
    hold = TradeDecision(action="hold", size_fraction=None, confidence=None, reason=reason)
    return TrendGateOutcome(hold, reason, "veto", wire)


def trend_bonus_lane(action: str, verdict: dict | None, consensus, rule) -> str:
    """顺势放宽通路的**唯一**判定源（`consensus` wire 的 `trendBonusLane` 与两个整数都取自它）。

    `"none"`（总闸关 / 基调不可用）→ `"blocked_not_aligned"` → `"blocked_conflict"` →
    `"blocked_no_native_vote"` → `"applied"`。顺序即优先级：先判到哪条就报哪条。

    Args:
        action: **本轮动作**（评审 RP0-1：判据不含动作 ⇒ 逆势开仓会白拿这一票）。
        verdict: 本轮基调 wire。
        consensus: `signals.Consensus`（读该方向的原生票数）。
        rule: 本会话的趋势基调配置。

    Returns:
        五个 lane 之一。
    """
    if not rule.consensus_vote_enabled or not _usable(verdict):
        return BONUS_LANE_NONE
    if alignment_of(action, verdict) != "aligned":
        return BONUS_LANE_NOT_ALIGNED
    if (verdict or {}).get("conflict") != "none":
        return BONUS_LANE_CONFLICT
    votes = getattr(consensus, "long_votes" if action == "open_long" else "short_votes", 0)
    if int(votes or 0) < 1:
        return BONUS_LANE_NO_NATIVE_VOTE
    return BONUS_LANE_APPLIED


def trend_consensus_bonus(action: str, verdict: dict | None, consensus, rule) -> int:
    """轴 D-1a：顺势且基调强时给共识门禁追加的票数（0 或 1，上限恒为 1、不随强度放大）。

    五条硬约束（缺一即 0）：独立总闸 / 顺势 / 仅 `strong` / `conflict == "none"` /
    **该方向上信号板已有 ≥1 票**（「锦上添花，不雪中送炭」）。

    它的作用域**只有「差一票」**：在 `balanced`（`min_consensus=1`）下只影响本来就要放行的轮；
    真正吃到红利的是 `steady`（`min_consensus=2`）。**它解决不了「信号板一票都没有」那个断面**
    ——那是 D-1b（`trend_contrarian_allowance`）的作用域（评审 RP0-2）。

    **首参必须是本轮动作**（评审 RP0-1）：`resolve_consensus_gate` 把返回值加到
    `decision.action` 那一侧的票上，判据不含动作 ⇒ 逆势开仓会白拿这一票，与本通路声称的
    「顺势才追加」正好相反，且**不报任何错**。
    """
    return 1 if trend_bonus_lane(action, verdict, consensus, rule) == BONUS_LANE_APPLIED else 0


def trend_contrarian_allowance(action: str, verdict: dict | None, rule) -> int:
    """轴 D-1b：顺势且基调强时给**逆势通路**的反向票上限追加的额度（0 或 1，评审 RP0-2）。

    它**不发票**：`votes` 一张不加、`effective` 一张不涨，只把 `_contrarian_block` 首行那条
    `opposing > contrarian_max_opposing_votes` 的上限抬高 1。逆势通路的其余全部代价
    （`contrarianEvidence` 逐字证据、置信度下界、会话配额、强制 ×0.5 降档）**一条不减**。

    这是「板子清一色看多、做空原生票数 0、反向票数 ≥ 2」那个断面的**唯一**出口（§2.4）——
    D-1a 的第三条硬约束（该方向须已有 ≥1 票）在那里恒不满足，追加票被自己的护栏挡死。

    与 D-1a 的判据**故意不同**：`htf_conflict` 在这里**不构成阻断**（判定级别是用户所选的执行
    周期，高周期只是调制项，而这条通路放行的单已经付了逆势通路的全部代价）；但战术周期背离
    （`tactical_conflict` / `both`）**一律不放宽**——战术分歧说的是「此刻不是入场时机」，
    而本通路放宽的恰恰是入场资格。
    """
    if not rule.contrarian_allowance_enabled or not _usable(verdict):
        return 0
    if alignment_of(action, verdict) != "aligned":
        return 0
    return 1 if (verdict or {}).get("conflict") in ("none", "htf_conflict") else 0
