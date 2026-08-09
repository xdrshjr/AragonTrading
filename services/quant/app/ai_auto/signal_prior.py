"""信号先验的数据模型 / 加载契约 / 方向聚合（ai-trade-signal-prior-and-ev-gate §3.5 / §3.6）。

**刻意不 import pandas**：本模块是决策轮内每轮都跑的纯计算，保持零 DataFrame 依赖既让单测可以用
裸 dict 构造样本，也杜绝「在热路径上顺手 groupby」的诱惑。离线那一半（事件抽取 / 分段统计）在
`prior_builder.py`，它才吃 pandas。

三条不可动摇的口径（改本文件前先读）：

1. **事件同源律**（§3.2 推论 5）：离线统计的条件事件 `A` 与在线取用该条目的条件 `A'` 必须是**同一个
   断言**。`signal_edge` 两侧都是「本根触发」，`strategy_replay` 两侧都是「本根立场」。否则
   `p / W / L` 不是「估计得不准」，而是**没有定义**——而它要去当真实资金开仓门禁的分子。
2. **costBasis 恒为 `gross`**：加载侧对非 `gross` 条目 fail-closed 直接丢弃，绝不做「把成本加回去」
   的反演。双重计费会让任何真实 edge 被判死（R-1，本仓库有「反思致永不开单」的同类事故前科）。
3. **绝不因为「有条目」就造一个 p 出来**：`Σw == 0`、无贡献者、契约不满足一律返回 `None`（整键 /
   整条目缺席），由下游逐字回退到先验合入前的行为。一个假装有信息的常数比缺席更危险。
"""
from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass

# 加权净立场的死区（§3.8）：`|p_long − p_short|` 不超过它即判 flat。config 刻意不为它开字段——
# 它是「多空概率差到多少才算有方向」的展示口径，不是风险参数，两处各存一份必然漂移。
TIE_EPS = 0.02

# logit 前的概率夹取（防 0/1 处 logit 发散）与聚合后的输出夹取（§3.6 第 3 条）。
_LOGIT_CLAMP = (0.02, 0.98)
_OUTPUT_CLAMP = (0.05, 0.95)

# 恒存在的兜底体制桶：当前体制桶缺失时降级到它；它本身缺失才判整条目缺席（§3.5 在线降级链）。
ALL_REGIME = "all"

_ROUND = 6


@dataclass(frozen=True)
class SignalPriorEntry:
    """快照单条目（§5.1）：某 `(来源, 归集键, 方向, 体制桶)` 上的一份历史统计。

    `avg_loss_bps` 存**正数**（幅度），聚合时才带上符号——存负数会让「EV = p·W − (1−p)·L」这个式子
    在实现里出现两处符号约定，而符号错一次就是把亏损当收益。
    """

    source: str                       # signal_edge | strategy_replay
    event: str                        # trigger | stance（条件事件的名字，§5.1）
    direction: str                    # long | short
    regime: str                       # all | trend_up | trend_down | range
    win_rate: float                   # 段级衰减加权胜率 ∈ [0,1]
    mean_ret_bps: float
    avg_win_bps: float
    avg_loss_bps: float               # 正数（幅度）
    sample_n: int
    loss_n: int
    segments: int                     # 有效段数
    run_n: int                        # 独立事件段数；signal_edge 条目恒 == sample_n
    usable: bool
    cost_basis: str                   # 恒 "gross"（C-4 校验字段）
    signal_key: str | None = None
    strategy_id: int | None = None
    strategy_name: str = ""
    strategy_fingerprint: str | None = None
    ir: float | None = None
    t_stat: float | None = None
    unusable_reason: str | None = None

    @property
    def label(self) -> str:
        """人读标签（prompt 证据行与前端 contributors 共用，绝不各写一份）。"""
        return self.strategy_name or self.signal_key or self.source

    @property
    def identity(self) -> tuple:
        """体制桶之外的归集身份——同一身份的多个体制桶之间才做降级选桶。"""
        return (self.source, self.signal_key, self.strategy_id, self.direction)


@dataclass(frozen=True)
class DirectionPrior:
    """某个方向本轮聚合出的先验（§3.6）：概率 + 期望 + 谁在贡献。"""

    p: float
    ev_bps: float
    win_rate_raw: float
    w_sum: float
    contributors: tuple[dict, ...]        # 已按权重降序截到 top_n（prompt 密度上限）
    contributor_n: int                    # **未截断**的贡献条目数
    strategy_ids: tuple[int, ...]         # **未截断**的贡献策略集（覆盖率口径的分子来源）
    regime_used: str
    sample_n_total: int


@dataclass(frozen=True)
class PriorParams:
    """从 settings 解析一次的先验运行参数（此后纯函数据此运算，不再碰 settings）。"""

    horizon_bars: int
    min_samples: int
    ir_clip: float
    top_n: int
    max_age_s: float
    tie_eps: float


def resolve_prior_params(settings, *, horizon_bars: int | None = None) -> PriorParams:
    """`Settings` → `PriorParams`（唯一解析点；`horizon_bars` 与成本模型同源，§3.2 推论 1）。

    Args:
        settings: 全局配置。
        horizon_bars: 前瞻根数覆盖（ai-trade-holding-horizon-modes §2.6）；`None` ⇒ 逐字回退全局
            常数。**必须与 `CostRuntime.expected_hold_bars` 传同一个数**——两者不同源即造出第三个
            前瞻口径，而 C-3 契约校验恰好是按这个数去判快照该不该被整份丢弃的。
    """
    return PriorParams(
        horizon_bars=int(horizon_bars if horizon_bars is not None
                         else settings.ai_trade_cost_expected_hold_bars),
        min_samples=int(settings.ai_trade_prior_min_samples),
        ir_clip=float(settings.ai_trade_prior_ir_clip),
        top_n=int(settings.ai_trade_prior_top_n),
        max_age_s=float(settings.ai_trade_prior_max_age_s),
        tie_eps=TIE_EPS)


def strategy_fingerprint(template_key: str | None, params_json: str | None) -> str:
    """策略身份指纹（C-6，§3.5）：`sha1("{template_key}|{params_json}")` 前 16 位。

    条目按 `strategyId` 归集，而 `template_key` / `params_json` 可被用户随时改且**不改 id**——改完
    新鲜度与持有期契约全部照过，先验却已经在描述另一个策略。**刻意不用 `updated_at`**：改名 / 改
    `enabled` 也会推进它，那会造成大量无意义的整条目丢弃；指纹只认真正影响信号的两个字段。
    """
    raw = f"{template_key or ''}|{params_json or ''}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


# ============================ 序列化（KV 落库 ⇄ 运行时 dataclass） ============================
def entry_to_wire(entry: SignalPriorEntry) -> dict:
    """`SignalPriorEntry` → camelCase dict（§5.1 字段名的唯一真源，builder 与 API 共用）。"""
    return {"source": entry.source, "event": entry.event, "signalKey": entry.signal_key,
            "strategyId": entry.strategy_id, "strategyName": entry.strategy_name,
            "strategyFingerprint": entry.strategy_fingerprint, "direction": entry.direction,
            "regime": entry.regime, "winRate": entry.win_rate,
            "meanRetBps": entry.mean_ret_bps, "avgWinBps": entry.avg_win_bps,
            "avgLossBps": entry.avg_loss_bps, "sampleN": entry.sample_n,
            "lossN": entry.loss_n, "segments": entry.segments, "runN": entry.run_n,
            "ir": entry.ir, "tStat": entry.t_stat, "usable": entry.usable,
            "unusableReason": entry.unusable_reason, "costBasis": entry.cost_basis}


def entry_from_wire(raw: dict) -> SignalPriorEntry | None:
    """camelCase dict → `SignalPriorEntry`；形状不合法 ⇒ `None`（坏条目单独剔除，不毒死整键）。"""
    try:
        return SignalPriorEntry(
            source=str(raw["source"]), event=str(raw.get("event") or ""),
            direction=str(raw["direction"]), regime=str(raw.get("regime") or ALL_REGIME),
            win_rate=float(raw["winRate"]), mean_ret_bps=float(raw["meanRetBps"]),
            avg_win_bps=float(raw["avgWinBps"]), avg_loss_bps=abs(float(raw["avgLossBps"])),
            sample_n=int(raw["sampleN"]), loss_n=int(raw.get("lossN") or 0),
            segments=int(raw.get("segments") or 0), run_n=int(raw.get("runN") or 0),
            usable=bool(raw.get("usable")), cost_basis=str(raw.get("costBasis") or ""),
            signal_key=raw.get("signalKey"), strategy_id=raw.get("strategyId"),
            strategy_name=str(raw.get("strategyName") or ""),
            strategy_fingerprint=raw.get("strategyFingerprint"),
            ir=_opt_float(raw.get("ir")), t_stat=_opt_float(raw.get("tStat")),
            unusable_reason=raw.get("unusableReason"))
    except (KeyError, TypeError, ValueError):
        return None


# ============================ 加载契约（C-2 / C-3 / C-4 / C-6） ============================
def load_snapshot(raw: dict | None, params: PriorParams, *, now_wall: float,
                  fingerprints: dict[int, str] | None = None
                  ) -> tuple[list[SignalPriorEntry], str | None]:
    """按 §3.5 的加载契约把快照原文解析成条目列表。

    Args:
        raw: KV 里的快照原文（`None` / 空 ⇒ 整键缺席）。
        params: `resolve_prior_params` 产物。
        now_wall: 当前墙钟（秒）——由调用方注入，本函数不读时钟（可测）。
        fingerprints: `{strategyId: 现值指纹}`；`None` ⇒ 跳过 C-6（回测 / 单测路径）。

    Returns:
        `(entries, reject_reason)`。`reject_reason is None` 即全部契约干净；**整键缺席**时
        `entries == []` 且 reason 形如 `stale(...)` / `horizon_mismatch(...)` / `missing`；
        **部分丢条目**时 entries 非空而 reason 形如 `strategy_fingerprint(12,31)`——调用方据此
        推降级告警（静默降级会被当成故障，这是本仓库反复付过学费的教训）。
    """
    if not raw or not isinstance(raw, dict):
        return [], "missing"
    snap_h = int(raw.get("horizonBars") or 0)
    if snap_h != params.horizon_bars:                       # C-3 持有期同源
        return [], f"horizon_mismatch({snap_h}!={params.horizon_bars})"
    age_s = now_wall - float(raw.get("builtAtTs") or 0.0)
    if age_s > params.max_age_s:                            # C-2 新鲜度
        return [], f"stale({int(age_s)}s)"
    entries, dropped_cost, dropped_fp = [], 0, []
    for item in raw.get("entries") or []:
        entry = entry_from_wire(item) if isinstance(item, dict) else None
        if entry is None:
            continue
        if entry.cost_basis != "gross":                     # C-4 成本口径（fail-closed 丢条目）
            dropped_cost += 1
            continue
        if _fingerprint_stale(entry, fingerprints):         # C-6 策略身份
            dropped_fp.append(entry.strategy_id)
            continue
        entries.append(entry)
    return entries, _partial_reason(dropped_cost, dropped_fp)


def _fingerprint_stale(entry: SignalPriorEntry, fingerprints: dict[int, str] | None) -> bool:
    """C-6：策略条目的指纹与现值不符即判失配（无指纹表 / 非策略条目一律不判）。"""
    if fingerprints is None or entry.source != "strategy_replay":
        return False
    return fingerprints.get(entry.strategy_id) != entry.strategy_fingerprint


def _partial_reason(dropped_cost: int, dropped_fp: list) -> str | None:
    """把两类「丢条目」压成一条可读降级原因；都没丢 ⇒ None。"""
    parts = []
    if dropped_cost:
        parts.append(f"cost_basis({dropped_cost})")
    if dropped_fp:
        parts.append("strategy_fingerprint("
                     + ",".join(str(sid) for sid in dropped_fp) + ")")
    return "；".join(parts) if parts else None


# ============================ 在线聚合：从先验到方向概率 p（§3.6） ============================
def resolve_direction_prior(entries: list[SignalPriorEntry], *, direction: str,
                            board_view: list[tuple[int, str]],
                            active_signal_keys: set[str] | None,
                            regime: str | None,
                            params: PriorParams) -> DirectionPrior | None:
    """把本轮**真的在说话**的证据按 IR 加权聚合成方向概率与期望（无贡献者 ⇒ `None`）。

    Args:
        entries: `load_snapshot` 产物（已过 C-4 / C-6）。
        direction: `long` | `short`——要回答「本轮往这个方向开仓的期望是多少」。
        board_view: 本轮信号板投影 `[(strategy_id, stance)]`（P1-4：刻意不吃 `StrategySignalView`，
            保持本模块零 `signals` / 零 pandas 依赖）。
        active_signal_keys: 本根触发的具名信号集；**`None` = 本轮不知道**（数据不足，P2-4），
            此时 `signal_edge` 侧整体不参与聚合——空集会被读成「确定没有信号触发」，
            那是把「不知道」伪装成「知道」。
        regime: 本轮在线体制标签（`None` ⇒ 直接取 `all` 桶）。
        params: 运行参数（提供 `ir_clip` / `top_n`）。

    Returns:
        `DirectionPrior`；无贡献者或权重和为 0 ⇒ `None`（绝不凭空造一个 p）。
    """
    stances = dict(board_view)
    picked = _pick_contributors(entries, direction=direction, regime=regime,
                                stances=stances, active=active_signal_keys)
    weights = [(entry, _clamp(entry.ir or 0.0, 0.0, params.ir_clip)) for entry in picked]
    w_sum = sum(w for _, w in weights)
    if w_sum <= 0.0:                       # Σw == 0：全部证据无跨段一致性 ⇒ 不造 p（§3.6 第 2 条）
        return None
    logit_mean = sum(w * _logit(_clamp(e.win_rate, *_LOGIT_CLAMP)) for e, w in weights) / w_sum
    p = _clamp(_sigmoid(logit_mean), *_OUTPUT_CLAMP)
    win_bps = sum(w * e.avg_win_bps for e, w in weights) / w_sum
    loss_bps = sum(w * abs(e.avg_loss_bps) for e, w in weights) / w_sum
    top = sorted(weights, key=lambda pair: pair[1], reverse=True)[:max(params.top_n, 0)]
    return DirectionPrior(
        p=round(p, _ROUND), ev_bps=round(p * win_bps - (1.0 - p) * loss_bps, _ROUND),
        win_rate_raw=round(sum(w * e.win_rate for e, w in weights) / w_sum, _ROUND),
        w_sum=round(w_sum, _ROUND),
        contributors=tuple(_contributor_wire(e, w) for e, w in top),
        contributor_n=len(picked),
        strategy_ids=tuple(e.strategy_id for e in picked if e.strategy_id is not None),
        regime_used=(regime if any(e.regime == regime for e in picked) else ALL_REGIME),
        sample_n_total=sum(e.sample_n for e in picked))


def _pick_contributors(entries: list[SignalPriorEntry], *, direction: str, regime: str | None,
                       stances: dict, active: set[str] | None) -> list[SignalPriorEntry]:
    """候选筛选 + 体制降级选桶：`当前 regime 桶 → all 桶 → 该身份整条缺席`（§3.5 / §3.6）。"""
    buckets: dict[tuple, SignalPriorEntry] = {}
    for entry in entries:
        if entry.direction != direction or not entry.usable:
            continue
        if not _is_speaking_now(entry, direction=direction, stances=stances, active=active):
            continue
        if entry.regime not in (regime, ALL_REGIME):
            continue
        best = buckets.get(entry.identity)
        # 当前体制桶优先于 all 桶；同桶重复条目（理论上不该出现）保留先到的那条。
        if best is None or (best.regime == ALL_REGIME and entry.regime != ALL_REGIME):
            buckets[entry.identity] = entry
    return list(buckets.values())


def _is_speaking_now(entry: SignalPriorEntry, *, direction: str, stances: dict,
                     active: set[str] | None) -> bool:
    """在线取用条件——**必须与 §3.3 的离线事件定义逐字同源**（G-11 按本函数逐条对照）。

    `strategy_replay` = 本轮板上该策略 `stance == direction`（**持久立场根**，绝不加回
    `latest_signal == open_*` 的边沿子句：那一支度量的是边沿、用的是立场，是本设计头号缺陷）；
    `signal_edge` = 本根 `signalKey ∈ active_signal_keys`（**本根触发**）。
    """
    if entry.source == "strategy_replay":
        return stances.get(entry.strategy_id) == direction
    if entry.source == "signal_edge":
        return active is not None and entry.signal_key in active
    return False


def _contributor_wire(entry: SignalPriorEntry, weight: float) -> dict:
    """单条证据的只读明细（prompt 证据行 / 前端 contributors 共用）。"""
    return {"label": entry.label, "source": entry.source, "p": entry.win_rate,
            "ir": entry.ir, "sampleN": entry.sample_n, "weight": round(weight, _ROUND),
            "strategyId": entry.strategy_id, "signalKey": entry.signal_key}


# ============================ wire 装配（payload / 事件共用） ============================
def direction_wire(prior: DirectionPrior | None) -> dict | None:
    """`DirectionPrior` → payload 片段；`None` 表示该方向本轮无贡献者（**不是 p=0.5**）。"""
    if prior is None:
        return None
    return {"p": prior.p, "evBps": prior.ev_bps, "wSum": prior.w_sum,
            "sampleN": prior.sample_n_total, "contributorN": prior.contributor_n,
            "contributors": list(prior.contributors)}


def prior_wire(long_: DirectionPrior | None, short: DirectionPrior | None,
               weighted, meta: dict) -> dict | None:
    """装配 `payload["signalPriors"]`（§5.3）；两侧皆空且无加权共识 ⇒ `None` ⇒ 整键缺席。"""
    if long_ is None and short is None and weighted is None:
        return None
    out = dict(meta)
    out["long"] = direction_wire(long_)
    out["short"] = direction_wire(short)
    if weighted is not None:
        out["weighted"] = weighted
    return out


# ============================ 数值原语 ============================
def _logit(p: float) -> float:
    return math.log(p / (1.0 - p))


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(float(value), hi))


def _opt_float(value) -> float | None:
    """可选浮点归一：`None` / 非有限 / 不可转 ⇒ `None`（`ir` 缺席即权重 0，绝不当 0.0 之外的值用）。"""
    if value is None:
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None
