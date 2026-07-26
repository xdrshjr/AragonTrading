"""大周期趋势带波段策略单测 (spec trend-band-swing-strategy §6.1 的 18 个用例 + 评审补 T-19)。

覆盖三层决策（方向 / 位置 / 择时）、四条铁律（I-1 列契约、I-2 开仓 fail-closed 与离场
fail-open、I-3a 绝不逆势、I-4 按 ts 计时）与两个 v2 P0 的守门用例：

- T-12 ①：空头开仓后第一根**不得**被跟踪止盈平掉（`_extreme_px` 若用 0.0 哨兵必红）；
- T-17：冻结 12H 帧、只动基础收盘价，**必须**能触发目标带离场（若用 12H 收盘 %B 必红）。
"""
from __future__ import annotations

import pandas as pd
import pytest

from app.engine.backtester import run_backtest
from app.engine.simulator import BrokerContext
from app.strategies import BUILTIN_ORDER, STRATEGY_REGISTRY
from app.strategies import _band_structure as bs
from app.strategies.trend_band_swing import TrendBandSwing
from tests.conftest import make_synthetic_candles

# 便于触发的测试参数（小 EMA / 带 / RSI 窗），逻辑与默认值同构。
TEST_PARAMS = {
    "trend_ema_fast": 3, "trend_ema_slow": 6, "trend_slope_lookback": 2,
    "trend_slope_min": 0.05, "band_period": 5, "band_std_mult": 2.0,
    "atr_period": 5, "retrace_lookback": 4, "trigger_ema": 5, "trigger_rsi_period": 5,
    # 离场倍数在用例里显式钉住：默认值调整不应静默改变这些用例的判定阈值。
    "stop_atr_mult": 1.2, "trail_atr_mult": 1.5,
}

_T0 = 1_752_100_000                 # 基础根起点（刻意不对齐任何整点）
# 真实 12H 根的 ts：OKX 的 12H/1D 是港时（UTC+8）开盘，故 ts % 86400 == 57600。
# 用它构造即可锁死「频控桶身份必须取 tb_bar_ts，不得按 ts // 43200 整除分桶」（v2 C-3）。
_BUCKET_A = 1_751_990_400
_BUCKET_B = _BUCKET_A + 43200

_REASON_STOP = "结构止损(12H ATR)"
_REASON_TARGET = "触及目标带离场"
_REASON_TRAIL = "跟踪止盈(15m ATR)"

# `prepare()` 必须无条件建出的列（I-1）。
_BASE_COLUMNS = ["trig_ema", "trig_rsi", "base_atr"]
_PREFIXES = ("tb_", "mb_", "cf_")


def _strategy(**overrides) -> TrendBandSwing:
    return TrendBandSwing({**TEST_PARAMS, **overrides})


def _flat_ctx() -> BrokerContext:
    return BrokerContext(position_side="flat", bars_in_position=0, entry_price=0, equity=10000)


def _short_ctx(entry_price: float = 103.6) -> BrokerContext:
    return BrokerContext(position_side="short", bars_in_position=1,
                         entry_price=entry_price, equity=10000)


def _base_rows(n: int = 23, *, start_ts: int = _T0, step: int = 300) -> list[dict]:
    """平缓交替的 5m 基础根：RSI≈50、trig_ema≈104，便于末根精确控制扳机。"""
    rows: list[dict] = []
    for i in range(n):
        o = 104.0
        c = 104.2 if i % 2 else 103.8
        rows.append({"ts": start_ts + i * step, "o": o, "h": max(o, c) + 0.1,
                     "l": min(o, c) - 0.1, "c": c, "vol": 1000.0})
    return rows


def _short_trigger_rows(*, close: float = 103.6, start_ts: int = _T0) -> list[dict]:
    """末根满足做空扳机：收阴 + 跌破 trig_ema + 破前根低点 + RSI 未超卖。"""
    rows = _base_rows(start_ts=start_ts)
    prev = rows[-1]
    rows.append({"ts": prev["ts"] + 300, "o": 104.0, "h": 104.1,
                 "l": close - 0.1, "c": close, "vol": 1000.0})
    return rows


def _htf_rows(n: int, step: int, start_ts: int, first: float, drift: float) -> list[dict]:
    """线性漂移的高周期真实根（供 attach_timeframes 的 frames 字典）。"""
    rows: list[dict] = []
    for i in range(n):
        c = first + i * drift
        o = c - drift
        rows.append({"ts": start_ts + i * step, "o": o, "h": max(o, c) + 1.0,
                     "l": min(o, c) - 1.0, "c": c, "vol": 1000.0})
    return rows


def _inject(df: pd.DataFrame, prefix: str, **values) -> None:
    """把某个前缀的高周期列整列置为常量（模拟「两次高周期收盘之间恒不变」）。"""
    for name, value in values.items():
        df[f"{prefix}{name}"] = float(value)


def _inject_short_setup(df: pd.DataFrame, *, bar_ts: float = _BUCKET_A,
                        trend_dir: float = -1.0) -> None:
    """§2.4.1 走查的十步数据：12H/1D 空势 + 已反弹 3 根 + 12H 带 [98,104,110]。"""
    _inject(df, "tb_", trend_dir=trend_dir, bar_ts=bar_ts, band_low=98.0, band_mid=104.0,
            band_up=110.0, band_width=0.115, atr=1.0, up_run=3.0, down_run=0.0,
            rebound_pct_b=0.39, pullback_pct_b=0.0, pct_b=0.467)
    _inject(df, "mb_", trend_dir=trend_dir)
    _inject(df, "cf_", trend_dir=trend_dir, atr=0.5)


def _inject_long_setup(df: pd.DataFrame) -> None:
    """做多镜像：12H/1D 多势 + 已回踩 3 根 + 价格回到中轨。"""
    _inject(df, "tb_", trend_dir=1.0, bar_ts=_BUCKET_A, band_low=98.0, band_mid=104.0,
            band_up=110.0, band_width=0.115, atr=1.0, up_run=0.0, down_run=3.0,
            rebound_pct_b=0.0, pullback_pct_b=0.39, pct_b=0.467)
    _inject(df, "mb_", trend_dir=1.0)
    _inject(df, "cf_", trend_dir=1.0, atr=0.5)


def _short_setup_df(strat: TrendBandSwing, *, bar_ts: float = _BUCKET_A,
                    start_ts: int = _T0, close: float = 103.6) -> pd.DataFrame:
    rows = _short_trigger_rows(close=close, start_ts=start_ts)
    df = strat.prepare(pd.DataFrame(rows), "5m")
    _inject_short_setup(df, bar_ts=bar_ts)
    return df


def _set_bar(df: pd.DataFrame, i: int, *, o: float, h: float, low: float, c: float) -> None:
    """覆写第 i 根的 OHLC（持仓期的离场判定只读这四个数）。"""
    df.loc[i, "o"] = float(o)
    df.loc[i, "h"] = float(h)
    df.loc[i, "l"] = float(low)
    df.loc[i, "c"] = float(c)


def _entered_short(**overrides) -> tuple[TrendBandSwing, pd.DataFrame, int]:
    """先走一次真实开仓，使按笔运行态（进场带 / 极值哨兵）由代码自己建立。"""
    strat = _strategy(**overrides)
    df = _short_setup_df(strat)
    i = len(df) - 1
    assert [s.side for s in strat.on_candle(df, i, _flat_ctx())] == ["short"]
    return strat, df, i


# ---------------- T-1 / T-2：注册与周期声明 ----------------

def test_registered_and_atomic():
    assert "trend_band_swing" in STRATEGY_REGISTRY
    assert "trend_band_swing" in BUILTIN_ORDER
    assert STRATEGY_REGISTRY["trend_band_swing"].meta.kind == "atomic"


def test_timeframes_declares_three_bars():
    assert TrendBandSwing().timeframes() == ["15m", "12H", "1D"]
    # 三个周期允许两两相同：去重后帧数减少，不报错。
    assert TrendBandSwing({"macro_bar": "12H"}).timeframes() == ["15m", "12H"]


# ---------------- T-3 / T-4：方向层与位置层的纯函数 ----------------

def test_bias_requires_dual_timeframe_consensus():
    kw = {"allow_partial": True, "partial_scale": 0.6}
    assert bs.classify_bias(1.0, 1.0, **kw) == ("long", 1.0)
    assert bs.classify_bias(-1.0, -1.0, **kw) == ("short", 1.0)
    assert bs.classify_bias(1.0, 0.0, **kw) == ("long", 0.6)
    assert bs.classify_bias(0.0, -1.0, **kw) == ("short", 0.6)
    assert bs.classify_bias(1.0, 0.0, allow_partial=False, partial_scale=0.6) == ("flat", 0.0)
    assert bs.classify_bias(1.0, -1.0, **kw) == ("flat", 0.0)      # 冲突 ⇒ 不许交易
    assert bs.classify_bias(-1.0, 1.0, **kw) == ("flat", 0.0)
    assert bs.classify_bias(0.0, 0.0, **kw) == ("flat", 0.0)
    assert bs.classify_bias(float("nan"), -1.0, **kw) == ("flat", 0.0)   # fail-closed
    assert bs.classify_bias(-1.0, float("nan"), **kw) == ("flat", 0.0)


def test_zone_of_anchors_are_disjoint_and_cover_out_of_band():
    assert bs.zone_of(0.10, 0.15) == "lower"
    assert bs.zone_of(0.467, 0.15) == "mid"
    assert bs.zone_of(0.92, 0.15) == "upper"
    assert bs.zone_of(0.30, 0.15) == "none"
    # 破轨（%B ∉ [0,1]）必须归入最近的外侧锚，不得静默落回 none（v2 P1-2）。
    assert bs.zone_of(-0.2, 0.15) == "lower"
    assert bs.zone_of(1.3, 0.15) == "upper"
    assert bs.zone_of(float("nan"), 0.15) == "none"
    # tol 上界 0.24：三锚区仍两两互斥（0.25 处存在空档，锚区首尾不相接）。
    assert bs.zone_of(0.24, 0.24) == "lower"
    assert bs.zone_of(0.25, 0.24) == "none"
    assert bs.zone_of(0.26, 0.24) == "mid"
    assert bs.zone_of(0.74, 0.24) == "mid"
    assert bs.zone_of(0.76, 0.24) == "upper"


# ---------------- T-5 ~ T-8：开仓决策表 ----------------

def test_user_scenario_downtrend_rebound_to_mid_opens_short():
    """需求原文样例：下跌势 + 已反弹 3 根 12H + 回到中轨附近 ⇒ 做空。"""
    strat = _strategy()
    df = _short_setup_df(strat)
    signals = strat.on_candle(df, len(df) - 1, _flat_ctx())
    assert [s.side for s in signals] == ["short"]
    assert signals[0].strength == pytest.approx(0.6)
    assert signals[0].reason == "12H/1D空势·中轨反抽做空"


def test_never_enters_against_macro_trend():
    """上升趋势 + 中轨 + 看跌的 5m 扳机 ⇒ 绝不产生 short（硬不变式 I-3a）。"""
    strat = _strategy()
    df = strat.prepare(pd.DataFrame(_short_trigger_rows()), "5m")
    _inject_long_setup(df)
    for i in range(strat.warmup(), len(df)):
        for sig in strat.on_candle(df, i, _flat_ctx()):
            assert sig.side == "long"       # 开仓方向恒 == bias，永不反向


def test_no_chase_at_extreme_band():
    """下跌趋势 + 已贴下轨（%B≈0.02）⇒ 既不追空也不做多。"""
    strat = _strategy()
    df = _short_setup_df(strat, close=98.2)
    assert strat.on_candle(df, len(df) - 1, _flat_ctx()) == []


def test_conflicting_timeframes_stay_flat():
    """12H 空 / 1D 多 ⇒ 无共识，恒空仓。"""
    strat = _strategy()
    df = _short_setup_df(strat)
    _inject(df, "mb_", trend_dir=1.0)
    for i in range(strat.warmup(), len(df)):
        assert strat.on_candle(df, i, _flat_ctx()) == []


# ---------------- T-9 / T-16：列契约与部分帧 ----------------

def test_missing_htf_frames_is_fail_closed_not_crash():
    strat = _strategy()
    df = strat.prepare(pd.DataFrame(_base_rows(40)), "5m")
    for col in _BASE_COLUMNS:
        assert col in df.columns
    for prefix in _PREFIXES:
        for name in bs.BAND_FEATURE_COLUMNS:
            assert f"{prefix}{name}" in df.columns      # I-1：无条件建全列
    df = strat.attach_timeframes(df, "5m", {})
    for i in range(strat.warmup(), len(df)):
        assert strat.on_candle(df, i, _flat_ctx()) == []


def test_partial_frames_only_overrides_present_prefixes():
    """paper 侧只装非空窗口，`attach_timeframes` 会收到不完整字典（v2 P2-6）。"""
    strat = _strategy()
    rows = _base_rows(40)
    prepared = strat.prepare(pd.DataFrame(rows), "5m")
    without = strat.attach_timeframes(prepared, "5m", {})
    # 15m 帧刻意用与基础根（≈104）明显不同的价位，使「是否被真实帧覆盖」一眼可判。
    frames = {"15m": pd.DataFrame(_htf_rows(60, 900, _T0 - 50 * 900, 205.0, -0.05))}
    partial = strat.attach_timeframes(prepared, "5m", frames)

    kept = [c for c in without.columns if c.startswith(("tb_", "mb_"))]
    pd.testing.assert_frame_equal(without[kept], partial[kept])   # 无帧前缀保留兜底值
    assert float(partial["cf_ema_fast"].iat[-1]) > 200.0          # 有帧前缀被真实帧覆盖
    for i in range(strat.warmup(), len(partial)):
        assert strat.on_candle(partial, i, _flat_ctx()) == []     # tb_ 仍 NaN ⇒ 零开仓


# ---------------- T-10 ~ T-13、T-17：离场层 ----------------

def test_regime_flip_forces_exit():
    strat, df, i = _entered_short()
    _inject(df, "tb_", trend_dir=1.0)
    _inject(df, "mb_", trend_dir=1.0)
    signals = strat.on_candle(df, i, _short_ctx())
    assert [s.side for s in signals] == ["close"]
    assert "趋势失效" in signals[0].reason


def test_exit_priority_stop_before_target():
    """同根同时满足结构止损与目标带 ⇒ 命中止损（保守优先），并进入冷却。"""
    strat, df, i = _entered_short()
    _set_bar(df, i, o=104.0, h=105.0, low=98.0, c=98.4)
    signals = strat.on_candle(df, i, _short_ctx())
    assert signals[0].reason == _REASON_STOP
    assert strat._cooldown_until_ts == int(df["ts"].iat[i]) + 60 * 60


def test_trailing_exit_does_not_fire_before_threshold():
    """① 开仓后第一根小幅回抽不得触发（0.0 哨兵会让本条必红）；② 走足阈值才触发。"""
    strat, df, i = _entered_short()
    _inject(df, "cf_", atr=5.0)      # 跟踪距离 = 1.5 × 5 = 7.5
    _inject(df, "tb_", atr=10.0)     # 结构止损距离 = 1.2 × 10 = 12，避免抢先命中
    _set_bar(df, i, o=103.6, h=104.0, low=103.5, c=103.9)
    assert strat.on_candle(df, i, _short_ctx()) == []
    assert strat._extreme_px == pytest.approx(103.5)

    _set_bar(df, i, o=104.0, h=111.5, low=104.0, c=111.2)
    signals = strat.on_candle(df, i, _short_ctx())
    assert [s.side for s in signals] == ["close"]
    assert signals[0].reason == _REASON_TRAIL


def test_exit_works_when_htf_missing():
    """持仓中 12H 断供：目标带 / 结构止损被跳过而非抛错，跟踪止盈仍然工作（I-2 fail-open）。"""
    nan = float("nan")
    strat, df, i = _entered_short()
    _inject(df, "tb_", trend_dir=nan, atr=nan, band_low=nan, band_up=nan, band_width=nan)
    _inject(df, "mb_", trend_dir=nan)
    _inject(df, "cf_", atr=5.0)
    _set_bar(df, i, o=104.0, h=111.5, low=103.5, c=111.2)
    signals = strat.on_candle(df, i, _short_ctx())
    assert [s.reason for s in signals] == [_REASON_TRAIL]

    other, df2, j = _entered_short()
    _inject(df2, "tb_", band_low=nan, band_up=nan)
    _set_bar(df2, j, o=100.0, h=100.2, low=98.2, c=98.4)   # 本该到目标带，但带缺失
    assert other.on_candle(df2, j, _short_ctx()) == []


def test_target_band_exit_uses_live_price_not_stale_htf_pct_b():
    """冻结 12H 帧、只让基础收盘从中轨跌到下轨 ⇒ 必须在这一根产出目标带离场（v2 P0-1）。"""
    strat, df, i = _entered_short()
    frozen_pct_b = float(df["tb_pct_b"].iat[i])
    _set_bar(df, i, o=100.0, h=100.2, low=98.2, c=98.4)
    signals = strat.on_candle(df, i, _short_ctx())
    assert [s.reason for s in signals] == [_REASON_TARGET]
    # 12H 收盘 %B 全程未动：若实现按它判定，本用例必然失败，「做 T」也不成立。
    assert float(df["tb_pct_b"].iat[i]) == pytest.approx(frozen_pct_b)


# ---------------- T-19：成交理由按实际周期参数落字 ----------------

def test_reasons_name_the_configured_bars_not_the_defaults():
    """三个周期都是面板可改的枚举：非默认配置下成交归因不得仍写死「12H/1D」「15m」。"""
    bars = {"trend_bar": "4H", "macro_bar": "12H", "confirm_bar": "30m"}
    strat = _strategy(**bars)
    df = _short_setup_df(strat)
    i = len(df) - 1
    assert strat.on_candle(df, i, _flat_ctx())[0].reason == "4H/12H空势·中轨反抽做空"

    _set_bar(df, i, o=104.0, h=105.0, low=98.0, c=98.4)     # 同根同时满足止损与目标带
    assert strat.on_candle(df, i, _short_ctx())[0].reason == "结构止损(4H ATR)"

    trail, df_t, j = _entered_short(**bars)
    _inject(df_t, "cf_", atr=5.0)
    _inject(df_t, "tb_", atr=10.0)                          # 放宽结构止损，避免抢先命中
    _set_bar(df_t, j, o=103.6, h=104.0, low=103.5, c=103.9)
    assert trail.on_candle(df_t, j, _short_ctx()) == []
    _set_bar(df_t, j, o=104.0, h=111.5, low=104.0, c=111.2)
    assert trail.on_candle(df_t, j, _short_ctx())[0].reason == "跟踪止盈(30m ATR)"


# ---------------- T-14：频控与冷却按 ts，不按 bar index ----------------

def test_cooldown_and_bucket_cap_use_timestamps():
    """两段：同一根趋势 K 线内的开仓上限；结构止损后的冷却。`i` 全程冻结（模拟 paper deque）。"""
    strat = _strategy()
    df_a = _short_setup_df(strat, bar_ts=_BUCKET_A)
    df_b = _short_setup_df(strat, bar_ts=_BUCKET_B, start_ts=_T0 + 43200)
    i = len(df_a) - 1                       # 两个窗口等长 ⇒ i 恒为常数
    assert [s.side for s in strat.on_candle(df_a, i, _flat_ctx())] == ["short"]
    assert [s.side for s in strat.on_candle(df_a, i, _flat_ctx())] == ["short"]
    assert strat.on_candle(df_a, i, _flat_ctx()) == []      # 同一根 12H 内已开满 2 次
    assert [s.side for s in strat.on_candle(df_b, i, _flat_ctx())] == ["short"]  # 换根即放行

    cooled = _strategy()
    df_c = _short_setup_df(cooled, bar_ts=_BUCKET_A)
    assert [s.side for s in cooled.on_candle(df_c, i, _flat_ctx())] == ["short"]
    _set_bar(df_c, i, o=104.0, h=105.0, low=103.0, c=104.5)
    assert cooled.on_candle(df_c, i, _short_ctx())[0].reason == _REASON_STOP
    stop_ts = int(df_c["ts"].iat[i])
    assert cooled._cooldown_until_ts == stop_ts + 60 * 60

    _set_bar(df_c, i, o=104.0, h=104.1, low=103.5, c=103.6)   # 扳机条件恢复
    assert cooled.on_candle(df_c, i, _flat_ctx()) == []        # 仍在冷却窗内
    df_d = _short_setup_df(cooled, bar_ts=_BUCKET_B, start_ts=stop_ts + 3600)
    assert [s.side for s in cooled.on_candle(df_d, i, _flat_ctx())] == ["short"]


# ---------------- T-15：因果前缀不变性 ----------------

def _causal_frames() -> dict[str, pd.DataFrame]:
    return {
        "15m": pd.DataFrame(_htf_rows(80, 900, _T0 - 60 * 900, 106.0, -0.05)),
        "12H": pd.DataFrame(_htf_rows(45, 43200, _T0 - 40 * 43200, 120.0, -0.4)),
        "1D": pd.DataFrame(_htf_rows(45, 86400, _T0 - 40 * 86400, 130.0, -0.8)),
    }


def _replay(rows: list[dict], frames: dict[str, pd.DataFrame], k: int):
    """用全新实例从第 warmup 根重放到第 k-1 根（策略含会话级运行态，单点调用不可比）。"""
    strat = _strategy()
    df = strat.prepare(pd.DataFrame(rows[:k]), "5m")
    cut = int(rows[k - 1]["ts"])
    sliced = {b: f[f["ts"] <= cut].reset_index(drop=True) for b, f in frames.items()}
    df = strat.attach_timeframes(df, "5m", sliced)
    emitted = [[(s.side, s.strength, s.reason) for s in strat.on_candle(df, i, _flat_ctx())]
               for i in range(strat.warmup(), k)]
    return df, emitted


def test_causal_prefix_invariance():
    rows = _base_rows(60)
    frames = _causal_frames()
    k = 40
    df_prefix, prefix_signals = _replay(rows, frames, k)
    df_full, full_signals = _replay(rows, frames, len(rows))

    cols = [c for c in df_prefix.columns if c.startswith(_PREFIXES)]
    pd.testing.assert_frame_equal(df_prefix[cols], df_full[cols].iloc[:k])
    assert prefix_signals == full_signals[:len(prefix_signals)]


# ---------------- T-18：无帧回测冒烟（对齐全局用例 A-17）----------------

def test_backtest_smoke_without_frames():
    candles = make_synthetic_candles(300)
    result = run_backtest(TrendBandSwing(), candles, "1H", initial_capital=10000,
                          taker_fee_bps=5, slippage_bps=2, htf_frames=None)
    assert len(result.equity_points) == len(candles)
    assert set(result.metrics.keys()) >= {"totalReturn", "winRate", "sharpe", "maxDrawdown"}
