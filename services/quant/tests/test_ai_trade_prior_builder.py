"""离线先验 builder 单测（§8.1 T-3~T-6 / T-21；验收门禁 G-3 / G-5 / G-11）。

合成序列驱动、不触网络。四条被冻结的性质：**植入的 edge 能被还原**、**绝无前视**（末 `h` 根不产
事件）、**时间衰减真的在起作用**、**五条 `usable` 判据各自可被单独证伪**。另加 P0-2 的核心：立场根
事件的 `sampleN` 数的是根、`runN` 数的是**独立立场段**——一段 31 根的单边趋势只讲了一件事。
"""
from __future__ import annotations

import math

import pandas as pd

from app.ai_auto.backtest_frames import build_backtest_frames
from app.ai_auto.prior_builder import build_prior_snapshot
from app.engine.backtester import candles_to_df
from app.okx.types import Candle
from app.paper.strategy_factory import PaperStrategySpec
from app.strategies.base import BaseStrategy, Signal, StrategyMeta

_START_TS = 1_780_000_000
_STEP = 900          # 15m
_BARS = 600
_HORIZON = 8


class _StanceStrategy(BaseStrategy):
    """在指定根发 long / close 边沿（其余根静默），用于精确控制影子 broker 的立场区间。"""

    meta = StrategyMeta(key="_prior_stance_stub", name="立场桩", description="t",
                        default_params={}, params_schema={})

    def __init__(self, opens: set[int], closes: set[int]):
        super().__init__({})
        self._opens = opens
        self._closes = closes

    def warmup(self) -> int:
        return 0

    def on_candle(self, df, i, ctx):
        if i in self._opens:
            return [Signal(side="long", strength=1.0, reason="stub")]
        if i in self._closes:
            return [Signal(side="close", reason="stub")]
        return []


def _spec(opens: set[int], closes: set[int], strategy_id: int = 7) -> PaperStrategySpec:
    return PaperStrategySpec(strategy_id=strategy_id, strategy_name="立场桩",
                             strategy_kind="builtin",
                             strategy=_StanceStrategy(opens, closes), config_extra={})


def _candles(prices: list[float]) -> list[Candle]:
    """`o == c == p`（前瞻收益 = `p[i+h]/p[i+1] − 1`，可手算对拍）；h/l 只做形状占位。"""
    return [Candle(ts=_START_TS + k * _STEP, o=p, h=p * 1.001, l=p * 0.999, c=p,
                   vol=100.0, confirm=True) for k, p in enumerate(prices)]


def _rising_prices(target_bps: float = 50.0) -> list[float]:
    """每 `h−1` 步恰好涨 `target_bps` 的准几何序列（叠微小波动，使段间均值不退化为常数）。"""
    step = (1.0 + target_bps / 1e4) ** (1.0 / (_HORIZON - 1)) - 1.0
    prices, px = [], 100.0
    for k in range(_BARS):
        prices.append(px)
        px *= 1.0 + step * (1.0 + 0.02 * math.sin(k / 7.0))
    return prices


def _wobbly_prices(up_first: bool = True) -> list[float]:
    """确定性折返序列：前半程与后半程方向相反（供时间衰减与混合胜负样本使用）。"""
    prices, px = [], 100.0
    for k in range(_BARS):
        prices.append(px)
        trend = 1.0 if (k < _BARS // 2) == up_first else -1.0
        px *= 1.0 + trend * 0.0008 + 0.0011 * math.sin(k / 3.0)
    return prices


def _runs(count: int, length: int, stride: int, offset: int) -> tuple[set[int], set[int]]:
    """`count` 段各 `length` 根的立场区间（第 j 段起于 `offset + j*stride`）→ (开仓边沿, 平仓边沿)。

    信号在第 `s-1` 根发出、第 `s` 根开盘成交，故立场覆盖 `[s, s+length-1]`。
    """
    starts = [offset + j * stride for j in range(count)]
    return ({s - 1 for s in starts}, {s + length - 1 for s in starts})


def _build(prices: list[float], opens: set[int], closes: set[int], **over) -> dict:
    specs = [_spec(opens, closes)]
    rows = _candles(prices)
    frames = build_backtest_frames(specs, rows, {}, "15m")
    kwargs = {"inst_id": "BTC-USDT-SWAP", "bar": "15m", "horizon_bars": _HORIZON,
              "segments": 6, "embargo_bars": 0, "half_life_days": 1e9, "min_samples": 30,
              "min_runs": 10, "built_at_ts": _START_TS + _BARS * _STEP,
              "fingerprints": {7: "fp7"}, "min_segments": 6}
    kwargs.update(over)
    return build_prior_snapshot(candles_to_df(rows), specs, frames, **kwargs)


def _stance_all(snapshot: dict) -> dict | None:
    """取策略侧 long 方向的 `all` 桶条目（在线降级链的兜底桶，也是本文件的断言对象）。"""
    return next((e for e in snapshot["entries"]
                 if e["source"] == "strategy_replay" and e["direction"] == "long"
                 and e["regime"] == "all"), None)


# ============================ T-3：植入的 edge 可被还原 ============================
def test_planted_edge_is_recovered_and_zero_loss_blocks_usability():
    """信号后 8 根必涨 ≈50bps ⇒ `winRate=1.0`、`meanRetBps≈50`，但 `lossN < 5` ⇒ **不可用**。

    第四条判据专治「零亏损样本」：此时 `avgLossBps = 0`，EV 退化为 `p·W`，会给出一个不可能的
    乐观分子。
    """
    opens, closes = _runs(count=6, length=6, stride=100, offset=20)
    entry = _stance_all(_build(_rising_prices(), opens, closes))
    assert entry is not None
    assert entry["winRate"] == 1.0
    assert abs(entry["meanRetBps"] - 50.0) < 2.0
    assert entry["lossN"] == 0 and entry["avgLossBps"] == 0.0
    assert entry["usable"] is False and entry["unusableReason"] == "loss_n<5"


# ============================ T-4：无前视 ============================
def test_no_lookahead_last_horizon_bars_produce_no_events():
    """末 `h` 根不产生事件：立场铺到最后一根，`sampleN` 恰等于 `i + h <= n−1` 的根数。"""
    start = 500
    snapshot = _build(_wobbly_prices(), {start - 1}, set())     # 无平仓 ⇒ 立场持到末根
    entry = _stance_all(snapshot)
    assert entry["sampleN"] == (_BARS - 1 - _HORIZON) - start + 1
    assert snapshot["asOfTs"] == _START_TS + (_BARS - 1) * _STEP


def test_snapshot_head_fields_are_self_describing():
    """快照自带判 `usable` 时用的门槛与口径——「这份数据是按什么标准判的」必须可追溯。"""
    opens, closes = _runs(count=6, length=6, stride=100, offset=20)
    snapshot = _build(_wobbly_prices(), opens, closes)
    assert snapshot["costBasis"] == "gross" and snapshot["horizonBars"] == _HORIZON
    assert snapshot["purgeBars"] == _HORIZON and snapshot["minRuns"] == 10
    assert snapshot["bars"] == _BARS and snapshot["segments"] == 6
    # G-5：结构上产不出净额条目（双重计费 ⇒ 永不开仓，本仓库有同类事故前科）。
    assert all(e["costBasis"] == "gross" for e in snapshot["entries"])


# ============================ T-5：时间衰减 ============================
def test_time_decay_shifts_win_rate_toward_recent_segments():
    """近端全亏、远端全赚的序列上：加大衰减 ⇒ 胜率被拉向近端（亏损侧），远端样本被折价。"""
    opens, closes = _runs(count=6, length=6, stride=100, offset=20)
    prices = _wobbly_prices(up_first=True)      # 前半程涨（远端赢）、后半程跌（近端亏）
    flat = _stance_all(_build(prices, opens, closes, half_life_days=1e9))
    decayed = _stance_all(_build(prices, opens, closes, half_life_days=0.5))
    assert flat["sampleN"] == decayed["sampleN"]        # 同样本量，只有权重不同
    assert decayed["winRate"] < flat["winRate"]


# ============================ T-6：五条 usable 判据 ============================
def test_sample_floor_is_the_first_criterion():
    opens, closes = _runs(count=6, length=6, stride=100, offset=20)
    entry = _stance_all(_build(_wobbly_prices(), opens, closes, min_samples=999))
    assert entry["usable"] is False and entry["unusableReason"] == "sample_n<999"


def test_effective_segments_floor_blocks_short_windows():
    entry = _stance_all(_build(_wobbly_prices(), {9}, {40}))    # 全部事件落在第 0 段
    assert entry["segments"] == 1
    assert entry["usable"] is False and entry["unusableReason"] == "segments<6"


def test_constant_segment_means_yield_no_ir_and_no_t_stat():
    """各段均值近似常数 ⇒ `std = 0` ⇒ `ir = None` ⇒ 整条不可用（假装有信息的常数最危险）。"""
    prices = [100.0 * (1.0002 ** k) for k in range(_BARS)]      # 严格等比 ⇒ 逐根收益恒等
    opens, closes = _runs(count=6, length=6, stride=100, offset=20)
    entry = _stance_all(_build(prices, opens, closes))
    assert entry["ir"] is None and entry["tStat"] is None
    assert entry["usable"] is False and entry["unusableReason"] == "t_stat_unavailable"


def test_run_floor_is_the_binding_criterion_for_overlapping_stances():
    """6 段各 6 根 ⇒ `sampleN=36` 过了样本门槛，却只有 6 个独立段 ⇒ 被 `runN` 判据拦下。"""
    opens, closes = _runs(count=6, length=6, stride=100, offset=20)
    entry = _stance_all(_build(_wobbly_prices(), opens, closes))
    assert entry["sampleN"] == 36 and entry["runN"] == 6 and entry["lossN"] >= 5
    assert entry["usable"] is False and entry["unusableReason"] == "run_n<10"


def test_run_floor_can_be_satisfied_and_entry_becomes_usable():
    """12 段各 3 根 ⇒ `runN=12 ≥ 10`，其余四条同时满足 ⇒ 条目**可用**（判据不是恒假的）。"""
    opens, closes = _runs(count=12, length=3, stride=48, offset=20)
    entry = _stance_all(_build(_wobbly_prices(), opens, closes, min_samples=30))
    assert entry["runN"] == 12 and entry["sampleN"] == 36
    assert entry["usable"] is True and entry["unusableReason"] is None


# ============================ T-21：事件同源与 runN ============================
def test_single_stance_run_counts_bars_as_samples_but_one_run():
    """一段 31 根的单边立场：`sampleN=31`（根）而 `runN=1`（段）；`event` 恒为 `stance`。

    不设 `runN`，`sampleN ≥ 30` 会被**一段行情**单独满足，显著性判据形同虚设。此构造里首个不满足的
    判据是有效段数（全部事件挤在第 0 段），`runN` 的独立证伪见上一条用例。
    """
    entry = _stance_all(_build(_wobbly_prices(), {9}, {40}))
    assert entry["sampleN"] == 31 and entry["runN"] == 1
    assert entry["event"] == "stance" and entry["strategyId"] == 7
    assert entry["strategyFingerprint"] == "fp7"
    assert entry["usable"] is False


def test_signal_edge_entries_are_trigger_sourced_with_run_n_equal_sample_n():
    """`signal_edge` 侧事件恒为**本根触发**，故 `runN == sampleN`（G-11 的另一半）。"""
    opens, closes = _runs(count=6, length=6, stride=100, offset=20)
    snapshot = _build(_wobbly_prices(), opens, closes)
    edges = [e for e in snapshot["entries"] if e["source"] == "signal_edge"]
    assert edges, "合成序列上一个具名信号都没触发，本用例已失去证伪力"
    assert all(e["event"] == "trigger" for e in edges)
    assert all(e["runN"] == e["sampleN"] for e in edges)
    assert all(e["strategyId"] is None and e["signalKey"] for e in edges)


def test_regime_buckets_are_additive_to_the_all_bucket():
    """体制桶与 `all` 桶并存：`all` 恒是全样本，体制桶是它的子集（在线降级链的前提）。"""
    opens, closes = _runs(count=6, length=6, stride=100, offset=20)
    snapshot = _build(_wobbly_prices(), opens, closes)
    rows = [e for e in snapshot["entries"]
            if e["source"] == "strategy_replay" and e["direction"] == "long"]
    all_bucket = next(e for e in rows if e["regime"] == "all")
    others = [e for e in rows if e["regime"] != "all"]
    assert others, "合成序列上一个体制桶都没产出，本用例已失去证伪力"
    assert all(e["regime"] in ("trend_up", "trend_down", "range") for e in others)
    assert sum(e["sampleN"] for e in others) <= all_bucket["sampleN"]


def test_empty_specs_builds_signal_edge_side_only():
    """`strategyIds` 为空数组 = 只建 `signal_edge` 侧（不是错误，也不该崩）。"""
    rows = _candles(_wobbly_prices())
    snapshot = build_prior_snapshot(
        candles_to_df(rows), [], [], inst_id="BTC-USDT-SWAP", bar="15m",
        horizon_bars=_HORIZON, segments=6, embargo_bars=0, half_life_days=30.0,
        min_samples=30, min_runs=10, built_at_ts=_START_TS + _BARS * _STEP,
        fingerprints={})
    assert snapshot["coverage"]["strategies"] == 0
    assert all(e["source"] == "signal_edge" for e in snapshot["entries"])


def test_embargo_drops_events_near_segment_ends():
    """段末隔离带真的在起作用：开隔离后样本严格减少（否则 IR 会把自相关误读成稳健性）。"""
    opens, closes = _runs(count=6, length=6, stride=100, offset=78)   # 贴着段末
    plain = _stance_all(_build(_wobbly_prices(), opens, closes, embargo_bars=0))
    fenced = _stance_all(_build(_wobbly_prices(), opens, closes, embargo_bars=12))
    assert fenced["sampleN"] < plain["sampleN"]


def test_forward_return_matches_hand_computed_estimand():
    """估计量对拍：入场 `open[i+1]`、出场 `close[i+h]`——取 `close[i]` 会引入乐观偏差。"""
    prices = _wobbly_prices()
    entry = _stance_all(_build(prices, {9}, {10}))       # 立场仅覆盖第 10 根
    frame = pd.Series(prices)
    expected = (frame[10 + _HORIZON] / frame[11] - 1.0) * 1e4
    assert entry["sampleN"] == 1
    assert abs(entry["meanRetBps"] - expected) < 1e-6
