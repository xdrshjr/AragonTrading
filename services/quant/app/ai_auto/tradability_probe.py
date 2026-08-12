"""可交易性预检的**取数层**（ai-trade-tradability-and-open-funnel 轴 A · §3.1.2）。

为什么另起一个模块而不是塞进 `api/ai_auto.py`：后者开工前已 671 行，而本层含 REST/缓存双路 +
ATR 序列 + 逐周期成本重算 + `alternatives` 并发，直接把它推过 CLAUDE.md 的 800 行硬线。下沉还顺带
满足本仓库已成文的另一条纪律（`discipline_runtime.py:7-8`）：**判据在纯核心、取数在运行时层**
——判据能被纯函数单测逐行钉死，取数只能靠集成测试。判据全在 `tradability.py`，本模块只取数。

**取数失败绝不 5xx**：探针是一个只提示、不阻断的旁路，任何失败一律退化成 `level="unknown"` 并
返回 200。把用户挡在启动之外的探针，比一个给错建议的探针更坏。

> **[P1-4] 有意偏离：缓存回落不受 `settings.allow_cached_fallback` 门控。**
> `config.py` 的 `allow_cached_fallback` 默认 **False**，若照抄 `api/market.py:100` 的门控，
> OKX 一不可达探针就只会说 `unknown`——而用户本机 `candles_cache` 里**正躺着**本轮实测用的
> 那几万根 K 线。偏离的理由是这两条路径的风险根本不同源：那个开关要防的是**用陈旧价格去下单**，
> 而探针算的是一个**历史窗口统计量**、不驱动任何交易判定、也不参与任何 decision。
> 故本模块**无条件**读缓存，代价是必须如实标注：wire 上 `source:"cache"` + `asOfTs`（缓存最后
> 一根的 ts），文案写「基于本地缓存的历史 K 线」。
> **这条偏离必须留着，防止后来者按「与 market.py 保持一致」把它改回去。**
"""
from __future__ import annotations

import asyncio
import logging
import time

import pandas as pd

from ..engine.indicators import atr as atr_series
from ..okx.rest_client import get_rest_client
from ..okx.types import BAR_SECONDS
from .cost_runtime import CostRuntime
from .tradability import TradabilityInputs, TradabilityVerdict, evaluate, verdict_wire

logger = logging.getLogger(__name__)

# 弹窗周期下拉的候选集合（与 `AiTradeLaunchModal.tsx` 的 `BARS` 同序）；`alternatives` 只探测
# **比当前更长**的项（≤6 个），单次 `limit` 见下。
BARS: tuple[str, ...] = ("1m", "5m", "15m", "30m", "1H", "4H", "1D")

# 单次取数根数上限：`_SINGLE_PAGE_LIMIT_MAX=300` 的单发区间内，不触发 history-candles 翻页（R-3）。
_MAX_WINDOW_BARS = 300


async def probe(session, settings, *, inst_id: str, bar: str,
                with_alternatives: bool = True) -> dict | None:
    """一次完整预检：取 K 线 → 算 ATR 序列 → 取两个口径的成本 → 判定 → 装 wire。

    Args:
        session: `AsyncSession`（只用于缓存回落；`None` ⇒ 不读缓存）。
        settings: 生效配置（**必须是会话/请求那一份派生副本**，阈值的唯一真源）。
        inst_id: 标的。
        bar: 执行周期（调用方保证已在 `BAR_SECONDS` 键集内）。
        with_alternatives: 是否并发探测更长周期。**递归调用一律传 `False`**（§十.8：
            否则一次弹窗打开会放大成 O(n²) 次请求）。

    Returns:
        `verdict_wire` 产物；轴 A 总闸关 ⇒ 一个恒 `unknown` 的 wire（端点仍在、前端整段不渲染）。
    """
    preset = str(getattr(settings, "ai_trade_discipline_default_preset", "") or "")
    if not getattr(settings, "ai_trade_tradability_enabled", True):
        return verdict_wire(TradabilityVerdict(level="unknown", samples=0,
                                               headline="本机已整轴关闭可交易性预检"
                                                        "（AI_TRADE_TRADABILITY_ENABLED=false）。"),
                            inst_id=inst_id, bar=bar, preset=preset, source=None)
    window = _window_bars(settings)
    rows, source = await _load_candles(session, inst_id, bar, window)
    alternatives = ()
    if with_alternatives:
        alternatives = await _probe_alternatives(session, settings, inst_id=inst_id, bar=bar,
                                                 window=window)
    verdict = _evaluate_rows(rows, settings, inst_id=inst_id, bar=bar, alternatives=alternatives)
    return verdict_wire(verdict, inst_id=inst_id, bar=bar, preset=preset, source=source,
                        as_of_ts=(int(rows[-1]["ts"]) if rows else None),
                        alternatives=alternatives)


def _window_bars(settings) -> int:
    """探针窗口根数（配置值再夹到单发区间，避免触发翻页）。"""
    return min(_MAX_WINDOW_BARS,
               max(60, int(getattr(settings, "ai_trade_tradability_window_bars", 200))))


async def _load_candles(session, inst_id: str, bar: str, limit: int) -> tuple[list[dict], str | None]:
    """REST 优先、缓存无条件回落；两者皆空 ⇒ `([], None)`（调用方据此落 `unknown`）。

    姿态镜像 `api/market.py:80-107`，**唯一的区别**是缓存回落不受 `allow_cached_fallback` 门控
    （理由见模块 docstring 的 [P1-4]）。任何异常一律吞成「取不到」——探针失败绝不能挡住启动流程。
    """
    try:
        candles = await get_rest_client().get_candles(inst_id, bar, limit)
        if candles:
            return [{"ts": c.ts, "o": c.o, "h": c.h, "l": c.l, "c": c.c} for c in candles], "live"
    except Exception as exc:  # noqa: BLE001 —— 行情不可达只降级，绝不 5xx
        logger.info("tradability probe live candles failed (%s %s): %s", inst_id, bar, exc)
    if session is None:
        return [], None
    try:
        from ..db import repositories as repo
        cached = await repo.get_cached_candles(session, inst_id, bar, limit)
    except Exception as exc:  # noqa: BLE001 —— 缓存读失败同样只降级
        logger.info("tradability probe cache read failed (%s %s): %s", inst_id, bar, exc)
        return [], None
    return (list(cached), "cache") if cached else ([], None)


def atr_bps_series(rows: list[dict], period: int) -> tuple[float, ...]:
    """逐根 `ATR(period)/close × 1e4`；暖机段（前 `period` 行恒 NaN）按 `dropna()` 剔除。

    **复用 `engine.indicators.atr` 的同一个函数**（`profit_guard.safe_atr` 的底层，`session.py:674`
    与 `backtest.py:418` 都是它的既有消费者）——全仓 ATR 只允许有这一份公式，复制即漂移。
    """
    if len(rows) <= period:
        return ()
    df = pd.DataFrame(rows)
    values = atr_series(df, period) / df["c"] * 1e4
    return tuple(float(v) for v in values.dropna().tolist() if v == v and v > 0)


def _evaluate_rows(rows: list[dict], settings, *, inst_id: str, bar: str,
                   alternatives: tuple[dict, ...] = ()) -> TradabilityVerdict:
    """把取到的 K 线 + 成本读数喂进纯核心判据（本函数**不做任何判定**）。"""
    period = int(getattr(settings, "ai_trade_profit_guard_atr_period", 14))
    series = atr_bps_series(rows, period)
    market, limit = _preview_costs(settings, inst_id=inst_id, bar=bar)
    inputs = TradabilityInputs(
        atr_bps=series,
        cost_bps_market=None if market is None else market.total_bps,
        cost_bps_limit=None if limit is None else limit.total_bps,
        k_min=float(getattr(settings, "ai_trade_edge_gate_k_min", 0.0) or 0.0),
        atr_target_mult=float(getattr(settings, "ai_trade_edge_atr_target_mult", 1.0) or 0.0),
        stop_atr_mult=float(getattr(settings, "ai_trade_stop_atr_mult", 0.0) or 0.0),
        # [P0-1] `confidence` 必须**原样**来自真实 `CostBreakdown`：本模块构造的 `CostRuntime`
        # 从不被 `schedule_maintenance` 轮询 ⇒ 资金费快照恒 None ⇒ 这里恒是 "degraded"，
        # 而门禁的分母正因此要再乘一个 `degraded_mult`。假设 "ok" = 恒定低估 20% 否决率。
        cost_confidence_market="degraded" if market is None else market.confidence,
        cost_confidence_limit="degraded" if limit is None else limit.confidence,
        degraded_mult=float(getattr(settings, "ai_trade_edge_gate_degraded_mult", 1.0) or 1.0),
        stop_cost_floor_mult=float(getattr(settings, "ai_trade_stop_cost_floor_mult", 0.0) or 0.0))
    return evaluate(inputs,
                    infeasible_rate=float(settings.ai_trade_tradability_infeasible_veto_rate),
                    marginal_rate=float(settings.ai_trade_tradability_marginal_veto_rate),
                    bar=bar, inst_id=inst_id, alternatives=alternatives)


def _preview_costs(settings, *, inst_id: str, bar: str) -> tuple[object | None, object | None]:
    """两个口径的往返成本（市价 / 限价），各取**两侧方向中较贵的那一侧**。

    三条硬约束（v1 在这里写错过两条，逐条记下来）：
    1. `CostRuntime` 的构造签名第一个位置参数是 **`inst_id`**，不是 `bar`。
    2. `estimate_preview_cost(*, ord_type, side, bar_seconds)` 的 `side` **必填**（资金费只取
       不利侧），`bar_seconds` 取 `BAR_SECONDS[bar]` —— `alternatives` 各项必须用**它自己的**
       `bar_seconds`（资金费期数 = `ceil(expected_hold_bars × bar_seconds / interval)`，
       `1D × 8 根 = 8 天 ⇒ 24 期`，与 `1m` 的 1 期不是一个数量级）。
    3. 两侧各算一次取 `total_bps` 较大者——与 `CostRuntime._preview_side` 的
       「绝不因方向假设而让成本看起来更便宜」同向。
    """
    runtime = CostRuntime(inst_id, settings, None, None)
    seconds = BAR_SECONDS.get(bar) or BAR_SECONDS["1H"]

    def _worst(ord_type: str):
        both = [runtime.estimate_preview_cost(ord_type=ord_type, side=side, bar_seconds=seconds)
                for side in ("long", "short")]
        rows = [c for c in both if c is not None]
        return max(rows, key=lambda c: c.total_bps) if rows else None

    return _worst("market"), _worst("limit")


async def _probe_alternatives(session, settings, *, inst_id: str, bar: str,
                              window: int) -> tuple[dict, ...]:
    """对**比当前 bar 更长**的周期各跑一次探针（并发；任一失败即**省略该项**，不填 null 冒充）。

    每项用它自己的 `bar_seconds` 重算成本并回带 `costBpsMarket`（[P1-7]）——沿用当前 bar 的成本
    会让长周期看起来比真实**更可行**，而 `advice` 的第一条规则**正是**据此推荐周期的，
    那就变成了「用一个算错的数把用户劝去另一个周期」。`costBpsMarket` 随每项下发就是为了让这条
    能被前端与用例双向核对（G-3b）。
    """
    try:
        index = BARS.index(bar)
    except ValueError:
        return ()
    longer = BARS[index + 1:]
    if not longer:
        return ()
    results = await asyncio.gather(
        *[_probe_one(session, settings, inst_id=inst_id, bar=b, window=window) for b in longer],
        return_exceptions=True)
    return tuple(row for row in results if isinstance(row, dict))


async def _probe_one(session, settings, *, inst_id: str, bar: str, window: int) -> dict | None:
    """单个候选周期的四键读数；取不到 / 判不出 ⇒ `None`（调用方据此省略该项）。"""
    rows, _ = await _load_candles(session, inst_id, bar, window)
    if not rows:
        return None
    verdict = _evaluate_rows(rows, settings, inst_id=inst_id, bar=bar)
    if verdict.level == "unknown":
        return None
    return {"bar": bar, "vetoRateMarket": verdict.veto_rate_market,
            "costBpsMarket": verdict.cost_bps_market, "level": verdict.level}


async def probe_members(session, settings, *, inst_ids: list[str], bar: str) -> list[dict]:
    """启动期逐成员各算一份（R-10：**分组级不做任何合并**）。

    合并一个「平均否决率」既没有操作含义、也没有可信的下发通道——AI-trade 没有分组级实时通道
    （`group_status()` 只在挂载拉一次）。故这里返回的是一个**逐成员的列表**，每项自带 `instId`
    与 `asOfTs`，启动回执与会话 `status` 用的是**同一份对象**（同一 `asOfTs`，[P1-10]）。

    `alternatives` 在启动路径上**不探测**：那是弹窗做事前预览时的事，启动时再打 6 个周期的
    REST 只会拖慢真实资金会话的启动。
    """
    started = time.time()
    results = await asyncio.gather(
        *[probe(session, settings, inst_id=inst_id, bar=bar, with_alternatives=False)
          for inst_id in inst_ids],
        return_exceptions=True)
    logger.info("tradability probe for %d member(s) took %.2fs", len(inst_ids),
                time.time() - started)
    return [row for row in results if isinstance(row, dict)]
