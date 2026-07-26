"""策略 9：大周期趋势带波段 `trend_band_swing` (spec trend-band-swing-strategy)。

12H 与 1D 两个高周期共同定方向（冲突或数据缺失一律不开仓），12H 布林带定位置（贴上轨 /
中轨 / 下轨），15m 结构确认 + 基础周期（推荐 5m）扳机定进场根。所谓「做 T」是在同一段大趋势
内沿同一方向反复完成「贴带进场 → 到目标带离场 → 等下一次贴带」的往返，**不是多空来回换手**。

四条铁律（改本文件前先读一遍）：

- I-1  `prepare()` 无条件建出 `on_candle()` 会读的全部列（不足填 NaN），`attach_timeframes()`
       只做「有真实帧就覆盖」。否则无帧回测会 KeyError 崩测。
- I-2  开仓 fail-closed（任一决策输入 NaN / bias 为 flat ⇒ 不开）、离场 fail-open（缺数据只
       跳过该项检查，绝不因缺数据阻塞平仓）。两条极性相反，混淆任意一条都是事故。
- I-3a 开仓方向恒 == bias，**无任何参数可绕过**；`flip_exit` 只控制 I-3b（趋势失效即平仓）。
- I-4  一切计时按 `ts` 秒级。paper/live 下 `i` 会冻结在 `window_max-1`，按 bar index 计时的
       冷却 / 计数在实盘必然失效。

⚠️ 生命周期：`warmup()` 早于 `prepare()` 调用，故为 params 的纯函数；`prepare()` 在 paper/live
逐根重复调用，故运行态只在 `__init__` 建立、只在 `on_candle` 内写——**绝不在 `prepare()` 写入**。
按笔运行态（极值 / 进场带）空仓时清零，会话级运行态（冷却、每趋势根计数）不清，否则冷却
永远不生效。

缺帧降级：取不到 12H/1D/15m 真实 K 线时回退基础周期重采样兜底；仍不足则相关列为 NaN ⇒ 零开仓
（不报错、不造假数据），可用 `run.config.htfBars` 自查。
"""
from __future__ import annotations

import pandas as pd

from ..engine.indicators import atr, ema, rsi
from ..engine.mtf import bar_to_seconds
from ..engine.simulator import BrokerContext
from ..okx.types import BAR_SECONDS
from . import _band_structure as bs
from .base import BaseStrategy, Signal, StrategyMeta, register_strategy

_ZONE_LABEL = {"lower": "下轨", "mid": "中轨", "upper": "上轨"}
# bias -> (趋势词, 回归词, 动作词)，用于拼出「哪条带 + 什么方向」的中文成交理由。
_BIAS_LABEL = {"long": ("多势", "回踩", "做多"), "short": ("空势", "反抽", "做空")}

_REASON_FLIP = "大周期趋势失效离场"
_REASON_TARGET = "触及目标带离场"


@register_strategy
class TrendBandSwing(BaseStrategy):
    meta = StrategyMeta(
        key="trend_band_swing",
        name="大周期趋势带波段",
        description=(
            "12H+1D 双周期定势：两周期共振（或单边+震荡，可关）才给出交易方向，冲突或数据缺失"
            "一律不开仓。只在价格贴近 12H 布林中轨/上下轨、且已出现反向回归（连续反向根数或 %B "
            "回撤达阈值）时顺势开仓；15m 结构确认 + 基础周期(推荐5m)扳机决定具体进场根。小周期"
            "只决定何时进出，永远无权决定方向——逆大趋势开仓被硬性禁止。离场按「趋势失效 → 结构"
            "止损(趋势周期ATR) → 目标带 → 跟踪止盈(确认周期ATR)」固定优先级。方向判定按 12H/1D 收盘更新"
            "(最慢12小时延迟)，贴带与目标带按当根基础收盘实时判定。取不到真实高周期K线时回退"
            "重采样兜底，仍不足则零开仓(不报错、不造假数据)。实时模拟/实盘的基础周期须为 WS "
            "支持的周期(不可设12H)。"
        ),
        default_params={
            "trend_bar": "12H",
            "macro_bar": "1D",
            "confirm_bar": "15m",
            "trend_ema_fast": 20,
            "trend_ema_slow": 60,
            "trend_slope_lookback": 3,
            "trend_slope_min": 0.10,
            "band_period": 20,
            "band_std_mult": 2.0,
            "band_touch_tol": 0.15,
            "atr_period": 14,
            "retrace_lookback": 6,
            "allow_partial_bias": True,
            "partial_strength_scale": 0.6,
            "counter_run_min": 2,
            "retrace_pct_b_min": 0.20,
            "allow_edge_entry": True,
            "min_band_width": 0.02,
            "max_entries_per_trend_bar": 2,
            "cooldown_minutes": 60,
            "confirm_mode": "aligned",
            "trigger_ema": 20,
            "trigger_rsi_period": 14,
            "trigger_rsi_floor": 20,
            "trigger_rsi_ceiling": 80,
            "stop_atr_mult": 1.2,
            # 5.0 而非设计稿的 1.5：15m 与 12H 的 ATR 量纲差约 √48≈7 倍，1.5×15m ATR 只有结构止损
            # (1.2×12H ATR) 的约 1/5.5，会把其余三道离场整体压制成摆设（实测默认值下 10/10 成交
            # 全是跟踪止盈）。5.0×15m ATR ≈ 0.72×12H ATR：仍严格紧于结构止损（跟踪线本就该更紧），
            # 但不再是「入场次根即被噪音打掉」的线。详见 spec §实施过程发现的方案缺陷 I-1。
            "trail_atr_mult": 5.0,
            "target_pct_b_edge": 0.05,
            "flip_exit": True,
            # 兜底熔断而非第二条止损线：策略发的 close 要次根开盘才成交，broker 的 stop_loss_pct
            # 却是根内触发价立即成交。两者阈值接近时结构止损永不生效、归因也全变成 stop_loss。
            "stop_loss_pct": 0.05,
            "max_hold_bars": 288,
            "strength": 0.6,
        },
        # 只暴露最常调的 22 项进面板（按「趋势 → 位置 → 择时 → 风控」分组书写）；其余留在
        # default_params，可通过「另存为」编辑 params_json 调整。
        params_schema={
            "trend_bar": {"type": "enum", "default": "12H", "options": ["4H", "12H", "1D"], "label": "交易趋势周期"},
            "macro_bar": {"type": "enum", "default": "1D", "options": ["12H", "1D"], "label": "宏观趋势周期"},
            "confirm_bar": {"type": "enum", "default": "15m", "options": ["5m", "15m", "30m", "1H"], "label": "择时确认周期"},
            "trend_ema_fast": {"type": "int", "default": 20, "min": 3, "max": 120, "step": 1, "label": "高周期快EMA"},
            "trend_ema_slow": {"type": "int", "default": 60, "min": 5, "max": 240, "step": 1, "label": "高周期慢EMA"},
            "trend_slope_min": {"type": "number", "default": 0.10, "min": 0.0, "max": 2.0, "step": 0.05, "label": "趋势斜率阈值(ATR归一)"},
            "band_period": {"type": "int", "default": 20, "min": 5, "max": 120, "step": 1, "label": "布林均线周期"},
            "band_std_mult": {"type": "number", "default": 2.0, "min": 1.0, "max": 4.0, "step": 0.1, "label": "布林带标准差倍数"},
            "band_touch_tol": {"type": "number", "default": 0.15, "min": 0.02, "max": 0.24, "step": 0.01, "label": "贴带容差(%B)"},
            "allow_partial_bias": {"type": "bool", "default": True, "label": "允许单周期偏置(降权)"},
            "counter_run_min": {"type": "int", "default": 2, "min": 0, "max": 8, "step": 1, "label": "反向连续根数门槛"},
            "retrace_pct_b_min": {"type": "number", "default": 0.20, "min": 0.0, "max": 1.0, "step": 0.05, "label": "反弹/回撤幅度(%B)"},
            "allow_edge_entry": {"type": "bool", "default": True, "label": "允许上下轨顺势进场"},
            "max_entries_per_trend_bar": {"type": "int", "default": 2, "min": 1, "max": 10, "step": 1, "label": "同一趋势K线开仓上限"},
            "cooldown_minutes": {"type": "int", "default": 60, "min": 0, "max": 1440, "step": 5, "label": "止损后冷却(分钟)"},
            "confirm_mode": {"type": "enum", "default": "aligned", "options": ["aligned", "not_opposed"], "label": "确认周期严格度"},
            "stop_atr_mult": {"type": "number", "default": 1.2, "min": 0.2, "max": 5.0, "step": 0.1, "label": "结构止损(趋势ATR)"},
            "trail_atr_mult": {"type": "number", "default": 5.0, "min": 0.2, "max": 6.0, "step": 0.1, "label": "跟踪止盈(确认ATR)"},
            "flip_exit": {"type": "bool", "default": True, "label": "大趋势翻转即平仓"},
            "stop_loss_pct": {"type": "number", "default": 0.05, "min": 0.005, "max": 0.2, "step": 0.005, "label": "兜底熔断止损%"},
            "max_hold_bars": {"type": "int", "default": 288, "min": 1, "max": 2000, "step": 1, "label": "最大持仓根数(按周期换算)"},
            "strength": {"type": "number", "default": 0.6, "min": 0.1, "max": 1.0, "step": 0.05, "label": "仓位比例"},
        },
    )

    def __init__(self, params: dict | None = None):
        super().__init__(params)
        self._reset_trade_state()
        # 会话级运行态：跨笔存活，**不随空仓清零**，否则冷却与频控永远不生效。
        self._cooldown_until_ts = 0
        self._bucket_ts: float | None = None
        self._bucket_entries = 0

    def _reset_trade_state(self) -> None:
        """按笔清零持仓运行态（绝不在 `prepare()` 调用，见模块 docstring）。"""
        # 哨兵必须是 None 而不是 0.0：空头跟踪基准取 min(l)，0.0 会使基准恒为 0，
        # 于是「c >= 基准 + trail×ATR」对任何真实价格恒真，每一笔空单都会在开仓后
        # 第一根被假「跟踪止盈」平掉，而正向断言的用例还会为它背书（v2 P0-2）。
        self._extreme_px: float | None = None
        self._entry_zone = ""

    # ---------------- 声明式 hook ----------------

    def warmup(self) -> int:
        """基础周期扳机列的暖机根数，**单位是基础根**（与 `htf_warmup_bars` 不得混算）。

        它同时决定回测起信号根与 paper 会话窗口尺寸；按高周期根数填会既撑大实时窗口又推迟
        信号，填得过小则扳机列尚未激活就开始产信号。
        """
        p = self.params
        return max(int(p["trigger_ema"]), int(p["trigger_rsi_period"]),
                   int(p["retrace_lookback"])) + 5

    def timeframes(self) -> list[str]:
        """[确认, 趋势, 宏观] 去重后的高周期声明（保持插入顺序，便于诊断与断言）。

        允许三者两两相同（如趋势与宏观都填 1D）：去重后帧数减少、方向层两票同源恒共振，
        属用户自选，不报错。是否严格高于基础周期由调用方过滤。
        """
        p = self.params
        wanted = [str(p["confirm_bar"]), str(p["trend_bar"]), str(p["macro_bar"])]
        return list(dict.fromkeys(wanted))

    def htf_warmup_bars(self) -> int:
        """高周期暖机根数，**单位是高周期根**。

        不得为求保险而虚高：它同时放大三个周期的取数 target 与左界回溯（对 1D 就是往前几十
        天），是本策略取数成本的主要放大器；也不得低于慢 EMA，否则趋势列在区间前段恒 NaN。
        """
        p = self.params
        return max(int(p["trend_ema_slow"]), int(p["band_period"]),
                   int(p["atr_period"]), int(p["retrace_lookback"])) + 15

    # ---------------- 列装配（I-1：prepare 无条件建全列）----------------

    def prepare(self, df: pd.DataFrame, bar: str) -> pd.DataFrame:
        df = df.copy()
        p = self.params
        df["trig_ema"] = ema(df["c"], int(p["trigger_ema"]))
        df["trig_rsi"] = rsi(df["c"], int(p["trigger_rsi_period"]))
        df["base_atr"] = atr(df, int(p["atr_period"]))
        base_seconds = BAR_SECONDS.get(bar, 300)
        cfg = self._band_config()
        for prefix, tf in self._prefix_bars().items():
            feats = bs.attach_resampled_features(df, base_seconds, bar_to_seconds(tf),
                                                 cfg, prefix=prefix)
            for col in feats.columns:
                df[col] = feats[col]
        return df  # 不触碰 self._* 运行态

    def attach_timeframes(self, df: pd.DataFrame, bar: str,
                          frames: dict[str, pd.DataFrame]) -> pd.DataFrame:
        """有真实高周期帧就覆盖同名前缀列；缺帧的前缀保留 `prepare` 的重采样兜底值。

        `frames` 可能只含部分周期（paper 侧只装非空窗口），故逐前缀独立判断，绝不因某个周期
        取数失败而放弃其余周期。
        """
        cfg = self._band_config()
        out = df.copy()
        for prefix, tf in self._prefix_bars().items():
            htf_df = frames.get(tf)
            if htf_df is None or htf_df.empty:
                continue
            feats = bs.attach_frame_features(out, htf_df, bar_to_seconds(tf), cfg, prefix=prefix)
            for col in feats.columns:
                out[col] = feats[col]
        return out

    def _band_config(self) -> bs.BandConfig:
        p = self.params
        return bs.BandConfig(
            ema_fast=int(p["trend_ema_fast"]), ema_slow=int(p["trend_ema_slow"]),
            slope_lookback=int(p["trend_slope_lookback"]), slope_min=float(p["trend_slope_min"]),
            band_period=int(p["band_period"]), band_std=float(p["band_std_mult"]),
            atr_period=int(p["atr_period"]), retrace_lookback=int(p["retrace_lookback"]),
        )

    def _prefix_bars(self) -> dict[str, str]:
        """列前缀 → 周期参数。固定前缀而非 bar 名做前缀：三个周期允许两两相同，固定前缀让
        「同一帧喂两个前缀」只是一行循环，也让列名在测试里可直接硬编码。"""
        p = self.params
        return {"tb_": str(p["trend_bar"]), "mb_": str(p["macro_bar"]),
                "cf_": str(p["confirm_bar"])}

    # ---------------- 逐根分派 ----------------

    def on_candle(self, df: pd.DataFrame, i: int, ctx: BrokerContext) -> list[Signal]:
        row = df.iloc[i]
        if ctx.position_side in ("long", "short"):
            return self._exit_signal(row, ctx)
        self._reset_trade_state()  # 空仓第一时间按笔清零（会话级运行态不动）
        return self._entry_signal(df, i, row)

    # ---------------- 开仓：方向层 → 位置层 → 择时层 ----------------

    def _entry_signal(self, df: pd.DataFrame, i: int, row: pd.Series) -> list[Signal]:
        """三层门禁全过才开仓；任一层不表态即返回空（开仓 fail-closed，I-2）。"""
        if i < 1 or int(row["ts"]) < self._cooldown_until_ts:
            return []
        bias, conviction = self._classify_direction(row)
        if bias == "flat":
            return []
        zone = self._entry_zone_of(row, bias)
        if not zone or not self._timing_ok(df, i, row, bias):
            return []
        if not self._register_entry(row):
            return []
        p = self.params
        self._entry_zone = zone
        strength = max(0.0, min(float(p["strength"]) * conviction, 1.0))
        reason = _entry_reason(bias, zone, str(p["trend_bar"]), str(p["macro_bar"]))
        return [Signal(side=bias, strength=strength, reason=reason)]

    def _classify_direction(self, row: pd.Series) -> tuple[str, float]:
        """方向层：两个高周期的 `trend_dir` 投票，缺任一票即 flat。"""
        p = self.params
        return bs.classify_bias(
            row["tb_trend_dir"], row["mb_trend_dir"],
            allow_partial=bool(p["allow_partial_bias"]),
            partial_scale=float(p["partial_strength_scale"]),
        )

    def _entry_zone_of(self, row: pd.Series, bias: str) -> str:
        """位置层：返回允许开仓的 zone，不允许时返回空串。

        含三道过滤：带宽挤压（带被压扁时 %B 剧烈跳动、zone 判定失去意义）、决策表（顺势回归
        才开，追单与逆势一律否）、反向动能门槛（证明这是一次回归而不是趋势加速）。
        """
        p = self.params
        width = row["tb_band_width"]
        if pd.isna(width) or float(width) < float(p["min_band_width"]):
            return ""
        pct_b = bs.live_pct_b(float(row["c"]), row["tb_band_low"], row["tb_band_up"])
        zone = bs.zone_of(pct_b, float(p["band_touch_tol"]))
        if not bs.entry_zone_allowed(bias, zone, allow_edge_entry=bool(p["allow_edge_entry"])):
            return ""
        if not self._has_counter_move(row, bias):
            return ""
        return zone

    def _has_counter_move(self, row: pd.Series, bias: str) -> bool:
        """「已反弹三根 12H」的量化形态：反向连续根数 **或** %B 回归幅度达标。

        用 OR 而非 AND：两者刻画的是同一件事的两个侧面（次数与幅度），要求同时满足会把本就
        稀疏的 12H 级信号再砍掉一半；任一成立即证明这是一次回归而非趋势加速。
        """
        p = self.params
        if bias == "short":
            run, retrace = row["tb_up_run"], row["tb_rebound_pct_b"]
        else:
            run, retrace = row["tb_down_run"], row["tb_pullback_pct_b"]
        run_ok = not pd.isna(run) and float(run) >= float(p["counter_run_min"])
        retrace_ok = not pd.isna(retrace) and float(retrace) >= float(p["retrace_pct_b_min"])
        return run_ok or retrace_ok

    def _timing_ok(self, df: pd.DataFrame, i: int, row: pd.Series, bias: str) -> bool:
        """择时层：确认周期结构 + 基础周期扳机。"""
        return self._confirm_ok(row, bias) and self._trigger_ok(df, i, bias)

    def _confirm_ok(self, row: pd.Series, bias: str) -> bool:
        """确认周期（默认 15m）结构是否放行；缺该周期数据 ⇒ 不开仓（I-2）。"""
        direction = 1.0 if bias == "long" else -1.0
        confirm_dir = row["cf_trend_dir"]
        if pd.isna(confirm_dir):
            return False
        if str(self.params["confirm_mode"]) == "not_opposed":
            return float(confirm_dir) != -direction
        return float(confirm_dir) == direction

    def _trigger_ok(self, df: pd.DataFrame, i: int, bias: str) -> bool:
        """基础周期扳机：收阴/阳 + 破前根高低点 + 站在 EMA 反侧 + RSI 不在极端区追单。"""
        p = self.params
        row, prev = df.iloc[i], df.iloc[i - 1]
        trig_ema, trig_rsi = row["trig_ema"], row["trig_rsi"]
        prev_low, prev_high = prev["l"], prev["h"]
        if any(pd.isna(x) for x in (trig_ema, trig_rsi, prev_low, prev_high)):
            return False
        o, c = float(row["o"]), float(row["c"])
        if bias == "short":
            return (c < o and c < float(trig_ema) and c <= float(prev_low)
                    and float(trig_rsi) > float(p["trigger_rsi_floor"]))
        return (c > o and c > float(trig_ema) and c >= float(prev_high)
                and float(trig_rsi) < float(p["trigger_rsi_ceiling"]))

    def _register_entry(self, row: pd.Series) -> bool:
        """同一根真实趋势 K 线内的开仓上限（做 T 节流）。

        桶身份取 `tb_bar_ts`（那根真实高周期 K 线自身的 ts），**不是** `ts // 43200`——OKX 的
        12H/1D 是港时对齐，整除分桶会横跨两根真实根，「同一根内最多开 N 次」就名不副实。
        `tb_bar_ts` 为 NaN 时不计数也不拦截：缺帧场景本就已被 I-2 挡在开仓之外。
        """
        bar_ts = row["tb_bar_ts"]
        if pd.isna(bar_ts):
            return True
        bucket = float(bar_ts)
        if self._bucket_ts is None or bucket != self._bucket_ts:
            self._bucket_ts = bucket
            self._bucket_entries = 0
        if self._bucket_entries >= int(self.params["max_entries_per_trend_bar"]):
            return False
        self._bucket_entries += 1
        return True

    # ---------------- 离场：固定优先级，缺数据只跳过该项 ----------------

    def _exit_signal(self, row: pd.Series, ctx: BrokerContext) -> list[Signal]:
        """按「趋势失效 → 结构止损 → 目标带 → 跟踪止盈」固定优先级检查（离场 fail-open）。"""
        side = ctx.position_side
        self._track_extreme(row, side)
        p = self.params
        if self._is_trend_invalid(row, side):
            return [Signal(side="close", reason=_REASON_FLIP)]
        if self._is_structural_stop(row, side, float(ctx.entry_price)):
            # 冷却只覆盖策略自己发出的结构止损：broker 的兜底熔断在 apply_risk 内按触发价成交、
            # 不经 on_candle，策略侧无从观测（spec §实施过程发现的方案缺陷 I-3）。
            self._cooldown_until_ts = int(row["ts"]) + int(p["cooldown_minutes"]) * 60
            return [Signal(side="close", reason=_stop_reason(str(p["trend_bar"])))]
        if self._is_target_reached(row, side):
            return [Signal(side="close", reason=_REASON_TARGET)]
        if self._is_trailing_stop(row, side):
            return [Signal(side="close", reason=_trail_reason(str(p["confirm_bar"])))]
        return []

    def _track_extreme(self, row: pd.Series, side: str) -> None:
        """维护持仓期间的有利极值：持仓首根用当根 l（空）/ h（多）初始化，其后才累进。"""
        px = float(row["l"]) if side == "short" else float(row["h"])
        if self._extreme_px is None:
            self._extreme_px = px
        elif side == "short":
            self._extreme_px = min(self._extreme_px, px)
        else:
            self._extreme_px = max(self._extreme_px, px)

    def _is_trend_invalid(self, row: pd.Series, side: str) -> bool:
        """大趋势失效（I-3b，由 `flip_exit` 控制）。

        高周期断供（trend_dir 为 NaN）时**跳过本项**而不是当作失效平仓：离场侧缺数据只跳过该
        项检查，其余三道仍然工作。
        """
        if not bool(self.params["flip_exit"]):
            return False
        if pd.isna(row["tb_trend_dir"]) or pd.isna(row["mb_trend_dir"]):
            return False
        bias, _ = self._classify_direction(row)
        return bias != side

    def _is_structural_stop(self, row: pd.Series, side: str, entry_price: float) -> bool:
        """结构止损：开仓价 ± stop_atr_mult × 趋势周期 ATR（趋势 ATR 缺失则跳过）。"""
        trend_atr = row["tb_atr"]
        if pd.isna(trend_atr) or not entry_price > 0:
            return False
        dist = float(self.params["stop_atr_mult"]) * float(trend_atr)
        if side == "short":
            return float(row["h"]) >= entry_price + dist
        return float(row["l"]) <= entry_price - dist

    def _is_target_reached(self, row: pd.Series, side: str) -> bool:
        """目标带：用实时 %B 判定，故「反抽中轨 → 跌回下轨」的往返可在数小时内走完。"""
        target = bs.target_pct_b(side, self._entry_zone, float(self.params["target_pct_b_edge"]))
        pct_b = bs.live_pct_b(float(row["c"]), row["tb_band_low"], row["tb_band_up"])
        if pd.isna(target) or pd.isna(pct_b):
            return False
        if side == "short":
            return pct_b <= target
        return pct_b >= target

    def _is_trailing_stop(self, row: pd.Series, side: str) -> bool:
        """跟踪止盈：自有利极值反向走够 trail_atr_mult × 确认周期 ATR。"""
        confirm_atr = row["cf_atr"]
        if self._extreme_px is None or pd.isna(confirm_atr):
            return False
        dist = float(self.params["trail_atr_mult"]) * float(confirm_atr)
        c = float(row["c"])
        if side == "short":
            return c >= self._extreme_px + dist
        return c <= self._extreme_px - dist


def _entry_reason(bias: str, zone: str, trend_bar: str, macro_bar: str) -> str:
    """中文成交理由：一眼看出「哪条带 + 什么方向」（会流向图表 marker 与 trades.reason）。

    周期名取自 params 而非写死 `12H/1D`：三个周期都是面板可改的枚举，写死会让非默认配置下
    的成交归因说谎（默认配置下输出与写死时逐字相同）。
    """
    trend, move, action = _BIAS_LABEL[bias]
    return f"{trend_bar}/{macro_bar}{trend}·{_ZONE_LABEL[zone]}{move}{action}"


def _stop_reason(trend_bar: str) -> str:
    """结构止损理由：写明打的是哪个周期的 ATR（同 `_entry_reason` 的理由，不写死 12H）。"""
    return f"结构止损({trend_bar} ATR)"


def _trail_reason(confirm_bar: str) -> str:
    """跟踪止盈理由：写明用的是哪个周期的 ATR（不写死 15m）。"""
    return f"跟踪止盈({confirm_bar} ATR)"
