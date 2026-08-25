"""事件 → 语境桶 → 统计量 → 模板化陈述（§4.4）—— 纯函数、零 IO、零 LLM、完全可复现。

三条设计约束：

1. **桶的枚举范围刻意受限**（一阶 / 二阶 / 四组三阶 + 同形的跨标的桶），防组合爆炸。全轴笛卡尔积
   在 11 条轴上是天文数字，而绝大多数格子只会有 0~1 条样本——那不是经验，是噪音。
2. **只统计 `cost_basis=="net" && fee_known` 的事件**（不变式 I-7）。口径混用会让整张表整体偏移
   一个往返成本，而那正是本仓库另一个特性刚刚修掉的误差。被排除的条数如实落 `excluded` 并在 UI
   显示——「排除了但不说」和「没排除」一样坏。
3. **`costRegime` 是聚合期现算的轴**（评审 P1-9 / P2-9）：事件只落原始量 `cost_bps`，分位点在
   每次物化 / 重建时按 `(instId, bar)` 分组从**当前事件全集**现算。把标签冻结进事件会让同一个
   `realized_cost_bps` 因写入时刻不同拿到不同标签 ⇒ 半年后的 `low` 与今天的不是一回事，而桶聚合
   把它们当同一格：缓慢失真且不可察觉。

模板产出**恒 ≤120 字**（各字段有格式化上限），满足不变式 I-6。
"""
from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, field

from . import taxonomy as tax

_ROUND = 4

# 三阶桶：只枚举业务上确有意义的 4 组（§4.4）。
THIRD_ORDER: tuple[tuple[str, ...], ...] = (
    (tax.AXIS_SYMBOL, tax.AXIS_BAR, tax.AXIS_REGIME, tax.AXIS_SIDE),
    (tax.AXIS_SYMBOL, tax.AXIS_BAR, tax.AXIS_VOLATILITY, tax.AXIS_SIDE),
    (tax.AXIS_SYMBOL, tax.AXIS_BAR, tax.AXIS_WAKE, tax.AXIS_SIDE),
    (tax.AXIS_SYMBOL, tax.AXIS_BAR, tax.AXIS_EXIT),
)

# 模板 id（进 `lesson:` 哈希的输入之一 ⇒ 同桶换模板 = 换 key = 一条新经验 + 一条 refines 链）。
TEMPLATE_LOSS = "loss"
TEMPLATE_WIN = "win"
TEMPLATE_EXIT = "exit"
TEMPLATE_EDGE_ERROR = "edge_error"
TEMPLATE_TURNOVER = "turnover"
TEMPLATE_CONFIDENCE = "confidence"

_LABEL_MAX = 120


@dataclass(frozen=True)
class Bucket:
    """一个语境桶：签名 + 落在其中的事件下标。"""

    signature: dict[str, str]
    scope: str                              # symbol | global
    event_ids: tuple[int, ...] = ()

    @property
    def key(self) -> str:
        return tax.signature_key(self.signature)


@dataclass
class BucketStats:
    """一个桶的全部统计量（`stats_json` 的内容；键名即 wire 键）。"""

    n: int = 0
    wins: int = 0
    winRate: float | None = None
    meanNetBps: float | None = None
    medianNetBps: float | None = None
    stdNetBps: float | None = None
    tStat: float | None = None
    meanHoldS: float | None = None
    meanCostBps: float | None = None
    costShare: float | None = None
    meanEdgeErrorBps: float | None = None
    firstTs: int = 0
    lastTs: int = 0
    symbols: int = 0
    excluded: int = 0
    costRegimeFallback: bool = False
    template: str = TEMPLATE_LOSS
    prevSign: int = 0
    signStreak: int = 0

    def wire(self) -> dict:
        return {k: v for k, v in self.__dict__.items()}


@dataclass
class EventView:
    """聚合层读到的事件视图（DB 行 / 内存事件共用；聚合层**不认识** ORM 对象）。"""

    inst_id: str
    bar: str
    context: dict[str, str]
    cost_basis: str
    fee_known: bool
    net_bps: float | None
    gross_bps: float | None
    cost_bps: float | None
    edge_error_bps: float | None
    hold_seconds: float
    exit_wall_ts: int
    seq: int = 0
    group_id: str = ""
    extra: dict = field(default_factory=dict)

    @property
    def eligible(self) -> bool:
        """是否进 bps 类统计（不变式 I-7 的唯一判据）。"""
        return self.cost_basis == "net" and bool(self.fee_known) and self.net_bps is not None


def cost_regime_cuts(events: list[EventView], *, min_samples: int) -> dict:
    """按 `(instId, bar)` 分组算 `cost_bps` 三分位；样本不足回落全库分位（评审 P2-9）。

    Args:
        events: 全部事件（含被 I-7 排除的——分位点是**成本量纲**的刻度尺，与净额口径无关）。
        min_samples: 分组分位的最小样本数；不足即回落全库并标注 `fallback`。

    Returns:
        `{"global": [c1, c2] | None, "byScope": {"BTC-USDT-SWAP|15m": [c1, c2]}, "minSamples": n}`；
        全库样本 <3 时 `global` 为 None ⇒ 全部事件的 `costRegime` 折成 `unknown`。
    """
    all_costs = sorted(e.cost_bps for e in events if e.cost_bps is not None)
    out: dict = {"global": _tertiles(all_costs), "byScope": {}, "minSamples": int(min_samples)}
    groups: dict[str, list[float]] = {}
    for event in events:
        if event.cost_bps is None:
            continue
        groups.setdefault(f"{event.inst_id}|{event.bar}", []).append(float(event.cost_bps))
    for scope_key, costs in groups.items():
        if len(costs) >= min_samples:
            cuts = _tertiles(sorted(costs))
            if cuts is not None:
                out["byScope"][scope_key] = cuts
    return out


def _tertiles(sorted_costs: list[float]) -> list[float] | None:
    """升序成本序列的两个三分位点；样本 <3 ⇒ None（**绝不编一把尺子**）。"""
    n = len(sorted_costs)
    if n < 3:
        return None
    return [round(sorted_costs[n // 3], _ROUND), round(sorted_costs[(2 * n) // 3], _ROUND)]


def label_cost_regime(event: EventView, cuts: dict) -> tuple[str, bool]:
    """给一条事件贴 `costRegime` 标签；返回 `(label, fallback)`。

    `fallback=True` 表示这条用的是全库分位（该 `(instId,bar)` 分组样本不足），UI 须明示——
    BTC 与 PEPE 的成本量纲不是一回事，把它们混进同一格是这条轴最容易出的错。
    """
    if event.cost_bps is None:
        return tax.UNKNOWN, False
    scoped = (cuts.get("byScope") or {}).get(f"{event.inst_id}|{event.bar}")
    fallback = scoped is None
    edges = scoped or cuts.get("global")
    if not edges:
        return tax.UNKNOWN, fallback
    value = float(event.cost_bps)
    if value <= edges[0]:
        return "low", fallback
    return ("mid", fallback) if value <= edges[1] else ("high", fallback)


def enumerate_buckets(events: list[EventView], cuts: dict) -> dict[str, Bucket]:
    """把事件铺进语境桶（一阶 / 二阶 / 四组三阶 + 同形的跨标的桶）。

    Args:
        events: 全部事件。
        cuts: `cost_regime_cuts` 产物（`costRegime` 轴在这里现算现贴）。

    Returns:
        `{bucketKey: Bucket}`；`Bucket.event_ids` 是 `events` 的下标元组（决定性：入序即下标序）。
    """
    members: dict[str, list[int]] = {}
    signatures: dict[str, tuple[dict[str, str], str]] = {}
    for index, event in enumerate(events):
        signature = dict(event.context)
        regime, _fallback = label_cost_regime(event, cuts)
        signature[tax.AXIS_COST_REGIME] = regime
        signature.setdefault(tax.AXIS_SYMBOL, event.inst_id)
        signature.setdefault(tax.AXIS_BAR, event.bar)
        # ★ 实施发现 I-3：**同一条事件的组合必须先去重再落桶**。`_combos_for` 会为同一个签名
        # 产出两次——三阶的跨标的同形桶 `{bar, exit}` 与二阶的 `{bar} × exit` 逐字相同。此前每次
        # 都 `append(index)`，于是那些桶的 `event_ids` 里同一条事件出现两遍：`n` 翻倍、而
        # `t = mean/(std/√n)` 随 √n 增大 ⇒ **一条只有 10 个样本的经验会拿着「20 笔、t=3.5」晋升
        # 成铁律**，然后把那个假数字逐字写进喂给模型的 prompt（违反 I-9）。失败形态是完全静默的。
        seen: set[str] = set()
        for combo in _combos_for(signature):
            key = tax.signature_key(combo)
            if key in seen:
                continue
            seen.add(key)
            scope = "symbol" if tax.AXIS_SYMBOL in combo else "global"
            members.setdefault(key, []).append(index)
            signatures.setdefault(key, (combo, scope))
    return {key: Bucket(signature=signatures[key][0], scope=signatures[key][1],
                        event_ids=tuple(ids))
            for key, ids in members.items()}


def _combos_for(signature: dict[str, str]) -> list[dict[str, str]]:
    """一条事件所属的全部桶签名（受限枚举，见模块 docstring 第 1 条）。"""
    others = [axis for axis in tax.AXES if axis not in (tax.AXIS_SYMBOL, tax.AXIS_BAR)]
    combos: list[dict[str, str]] = []
    for axis in others + [tax.AXIS_BAR]:                       # 一阶：{symbol} × 每一条其他轴
        combos.append({tax.AXIS_SYMBOL: signature[tax.AXIS_SYMBOL], axis: signature[axis]})
        combos.append({axis: signature[axis]})                 # 跨标的同形桶
    for axis in others:                                        # 二阶：{symbol,bar} × 每一条其他轴
        pair = {tax.AXIS_BAR: signature[tax.AXIS_BAR], axis: signature[axis]}
        combos.append({tax.AXIS_SYMBOL: signature[tax.AXIS_SYMBOL], **pair})
        combos.append(dict(pair))
    for axes in THIRD_ORDER:                                   # 三阶：4 组
        combos.append({axis: signature[axis] for axis in axes})
        combos.append({axis: signature[axis] for axis in axes if axis != tax.AXIS_SYMBOL})
    return combos


def compute_stats(bucket: Bucket, events: list[EventView], cuts: dict) -> BucketStats:
    """算一个桶的全部统计量；**只聚合 `eligible` 的事件**（I-7），被排除的进 `excluded`。"""
    rows = [events[i] for i in bucket.event_ids]
    kept = [e for e in rows if e.eligible]
    stats = BucketStats(n=len(kept), excluded=len(rows) - len(kept),
                        symbols=len({e.inst_id for e in rows}))
    stats.costRegimeFallback = any(label_cost_regime(e, cuts)[1] for e in rows)
    if not kept:
        stats.firstTs = min((e.exit_wall_ts for e in rows), default=0)
        stats.lastTs = max((e.exit_wall_ts for e in rows), default=0)
        stats.template = _pick_template(bucket, stats)
        return stats
    nets = [float(e.net_bps) for e in kept]
    stats.wins = sum(1 for v in nets if v > 0)
    stats.winRate = round(stats.wins / len(nets), _ROUND)
    stats.meanNetBps = round(sum(nets) / len(nets), _ROUND)
    stats.medianNetBps = _median(nets)
    stats.stdNetBps = _std(nets)
    stats.tStat = _t_stat(nets)
    stats.meanHoldS = round(sum(e.hold_seconds for e in kept) / len(kept), 2)
    costs = [float(e.cost_bps) for e in kept if e.cost_bps is not None]
    stats.meanCostBps = round(sum(costs) / len(costs), _ROUND) if costs else None
    grosses = [abs(float(e.gross_bps)) for e in kept if e.gross_bps is not None]
    mean_gross = (sum(grosses) / len(grosses)) if grosses else None
    if stats.meanCostBps is not None and mean_gross:
        stats.costShare = round(stats.meanCostBps / mean_gross, _ROUND)
    errors = [float(e.edge_error_bps) for e in kept if e.edge_error_bps is not None]
    stats.meanEdgeErrorBps = round(sum(errors) / len(errors), _ROUND) if errors else None
    stats.firstTs = min(e.exit_wall_ts for e in kept)
    stats.lastTs = max(e.exit_wall_ts for e in kept)
    stats.template = _pick_template(bucket, stats)
    return stats


def _pick_template(bucket: Bucket, stats: BucketStats) -> str:
    """按桶的**主导信号**在 6 个模板里择一（决定性：判据顺序即优先级，无随机、无 LLM）。"""
    if tax.AXIS_EXIT in bucket.signature:
        return TEMPLATE_EXIT
    if (stats.meanEdgeErrorBps is not None and abs(stats.meanEdgeErrorBps) >= 20.0
            and stats.n >= 5):
        return TEMPLATE_EDGE_ERROR
    if tax.AXIS_CONFIDENCE in bucket.signature:
        return TEMPLATE_CONFIDENCE
    if stats.meanHoldS is not None and stats.meanHoldS > 0 and stats.n >= 5 and (
            stats.costShare is not None and stats.costShare >= 0.5):
        return TEMPLATE_TURNOVER
    if stats.meanNetBps is not None and stats.meanNetBps >= 0:
        return TEMPLATE_WIN
    return TEMPLATE_LOSS


def lesson_key(bucket: Bucket, template: str) -> str:
    """经验节点的稳定 key：`lesson:` + sha1(桶签名 + 模板 id)[:12]。

    同一桶重算时 key 不变 ⇒ 物化按 key upsert = **更新而非新增**，这正是「经验是累积的」的落点。
    """
    raw = f"{bucket.key}#{template}".encode()
    return "lesson:" + hashlib.sha1(raw).hexdigest()[:12]      # noqa: S324 —— 身份哈希，非安全用途


def render_label(bucket: Bucket, stats: BucketStats) -> str:
    """把桶统计压成一句 ≤120 字的中文陈述（六模板择一，纯字符串拼接）。"""
    prefix = _prefix(bucket.signature)
    body = _TEMPLATES[stats.template](bucket, stats)
    text = f"{prefix}{body}"
    return text if len(text) <= _LABEL_MAX else text[:_LABEL_MAX - 1] + "…"


def _prefix(signature: dict[str, str]) -> str:
    """`{标的} {周期} · {语境}：` 前缀；跨标的桶写「全标的」。"""
    symbol = signature.get(tax.AXIS_SYMBOL) or "全标的"
    bar = signature.get(tax.AXIS_BAR) or ""
    context = tax.describe(signature)
    head = f"{symbol} {bar}".strip()
    return f"{head} · {context}：" if context != "全局" else f"{head}："


def _loss(_bucket: Bucket, stats: BucketStats) -> str:
    losses = max(0, stats.n - stats.wins)
    share = "" if stats.costShare is None else f"，成本吃掉毛额 {round(stats.costShare * 100)}%"
    return (f"{stats.n} 笔中亏 {losses} 笔，净额均值 {_bps(stats.meanNetBps)}"
            f"（t={_num(stats.tStat)}）{share}。")


def _win(_bucket: Bucket, stats: BucketStats) -> str:
    return (f"{stats.n} 笔净额均值 {_bps(stats.meanNetBps)}（t={_num(stats.tStat)}），"
            f"胜率 {_pct(stats.winRate)}。")


def _exit(bucket: Bucket, stats: BucketStats) -> str:
    source = tax.label_of(tax.AXIS_EXIT, bucket.signature.get(tax.AXIS_EXIT, tax.UNKNOWN))
    return (f"由「{source}」了结的 {stats.n} 笔：净额均值 {_bps(stats.meanNetBps)}，"
            f"平均持仓 {_hold(stats.meanHoldS)}。")


def _edge_error(_bucket: Bucket, stats: BucketStats) -> str:
    value = stats.meanEdgeErrorBps or 0.0
    direction = "低" if value > 0 else "高"
    return (f"事前 edge 估计比实测系统性{direction} {_bps(abs(value))}"
            f"（{stats.n} 笔，净额均值 {_bps(stats.meanNetBps)}）。")


def _turnover(_bucket: Bucket, stats: BucketStats) -> str:
    return (f"{stats.n} 笔平均持仓仅 {_hold(stats.meanHoldS)}，成本吃掉毛额 "
            f"{round((stats.costShare or 0) * 100)}%，净额均值 {_bps(stats.meanNetBps)}。")


def _confidence(bucket: Bucket, stats: BucketStats) -> str:
    band = tax.label_of(tax.AXIS_CONFIDENCE, bucket.signature.get(tax.AXIS_CONFIDENCE, tax.UNKNOWN))
    return (f"置信度{band}的 {stats.n} 笔：胜率 {_pct(stats.winRate)}，"
            f"净额均值 {_bps(stats.meanNetBps)}（t={_num(stats.tStat)}）。")


_TEMPLATES = {
    TEMPLATE_LOSS: _loss, TEMPLATE_WIN: _win, TEMPLATE_EXIT: _exit,
    TEMPLATE_EDGE_ERROR: _edge_error, TEMPLATE_TURNOVER: _turnover,
    TEMPLATE_CONFIDENCE: _confidence,
}


def _bps(value) -> str:
    return "未知" if value is None else f"{float(value):+.0f}bps"


def _pct(value) -> str:
    return "未知" if value is None else f"{float(value) * 100:.0f}%"


def _num(value) -> str:
    return "未知" if value is None else f"{float(value):.1f}"


def _hold(seconds) -> str:
    if seconds is None:
        return "未知"
    value = float(seconds)
    if value < 3600:
        return f"{value / 60:.0f} 分钟"
    return f"{value / 3600:.1f} 小时" if value < 86400 else f"{value / 86400:.1f} 天"


def _median(values: list[float]) -> float | None:
    rows = sorted(values)
    if not rows:
        return None
    mid = len(rows) // 2
    out = rows[mid] if len(rows) % 2 else (rows[mid - 1] + rows[mid]) / 2
    return round(out, _ROUND)


def _std(values: list[float]) -> float | None:
    if len(values) < 2:
        return None
    mean = sum(values) / len(values)
    var = sum((v - mean) ** 2 for v in values) / (len(values) - 1)
    return round(math.sqrt(var), _ROUND) if var > 0 else 0.0


def _t_stat(values: list[float]) -> float | None:
    """单样本 t 统计量（`mean / (std_ddof1 / √n)`）；`n<2` 或零方差 ⇒ None。

    与 `attribution._t_stat` **逐字同口径**——两处算出不同的 t 会让「同一批往返在体检页显著、
    在经验库不显著」，而没有任何地方会报错。
    """
    n = len(values)
    if n < 2:
        return None
    mean = sum(values) / n
    var = sum((v - mean) ** 2 for v in values) / (n - 1)
    if var <= 0:
        return None
    return round(mean / (math.sqrt(var) / math.sqrt(n)), _ROUND)
