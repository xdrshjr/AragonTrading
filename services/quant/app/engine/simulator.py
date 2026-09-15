"""SimBroker —— 模拟撮合账户 (spec §6.1，精确定义 C4)。

关键不变量：
- 信号在「下一根开盘价」成交（避免未来函数，R4）。
- 单向单仓；strength∈[0,1] 表示目标名义敞口占当前权益比例：notional = strength·equity·leverage。
- 手续费按成交名义额 taker_fee_bps 计，开/平双边各计一次。
- 盯市权益每根更新；风控 stop/tp 当根触发价强平（止损优先），max_hold 到期次根开盘平。
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal


@dataclass
class RiskParams:
    """策略级风控参数（pct 为小数，如 0.02 = 2%）。"""

    stop_loss_pct: float | None = None
    take_profit_pct: float | None = None
    max_hold_bars: int | None = None


@dataclass
class TradeRecord:
    ts: int
    side: str          # long | short | close
    price: float
    qty: float
    fee: float
    pnl: float         # 平仓净实现盈亏（开仓为 0）
    position_after: float
    reason: str
    stop_loss_pct: float | None = None
    take_profit_pct: float | None = None
    max_hold_bars: int | None = None
    stop_loss_price: float | None = None
    take_profit_price: float | None = None


@dataclass
class EquityRecord:
    ts: int
    equity: float
    drawdown: float    # = equity/peak - 1 ≤ 0


@dataclass
class _Position:
    side: Literal["long", "short", "flat"] = "flat"
    qty: float = 0.0
    entry_price: float = 0.0
    entry_bar: int = -1
    entry_fee: float = 0.0
    stop_loss_pct: float | None = None
    take_profit_pct: float | None = None
    max_hold_bars: int | None = None
    stop_loss_price: float | None = None
    take_profit_price: float | None = None


@dataclass
class BrokerContext:
    """传给策略 on_candle 的只读账户上下文。"""

    position_side: Literal["long", "short", "flat"]
    bars_in_position: int
    entry_price: float
    equity: float


@dataclass
class _Order:
    action: Literal["open", "close"]
    side: Literal["long", "short"] | None = None
    strength: float = 1.0
    reason: str = ""
    risk_limit: tuple[float, float] | None = None


def _positive_float(value) -> float | None:
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None
    return n if math.isfinite(n) and n > 0 else None


def _positive_int(value) -> int | None:
    try:
        n = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return n if n > 0 else None


class SimBroker:
    """逐根驱动的模拟撮合引擎。"""

    def __init__(
        self,
        initial_capital: float,
        taker_fee_bps: float = 5.0,
        slippage_bps: float = 2.0,
        leverage: float = 1.0,
    ):
        self.initial_capital = float(initial_capital)
        self.fee_rate = taker_fee_bps / 1e4
        self.slip = slippage_bps / 1e4
        self.leverage = max(leverage, 0.0) or 1.0

        self.cash = self.initial_capital
        self.pos = _Position()
        self.peak_equity = self.initial_capital
        self.fees_paid = 0.0

        self.trades: list[TradeRecord] = []
        self.equity_points: list[EquityRecord] = []
        self._pending: list[_Order] = []
        self._cur_bar = -1
        self._risk = RiskParams()

    def set_risk(self, risk: RiskParams) -> None:
        """保存下一次开仓要使用的标准化风控快照。"""
        self._risk = RiskParams(
            stop_loss_pct=_positive_float(risk.stop_loss_pct),
            take_profit_pct=_positive_float(risk.take_profit_pct),
            max_hold_bars=_positive_int(risk.max_hold_bars),
        )

    # ---- 对外只读上下文 ----
    def context(self) -> BrokerContext:
        return BrokerContext(
            position_side=self.pos.side,
            bars_in_position=(self._cur_bar - self.pos.entry_bar) if self.pos.side != "flat" else 0,
            entry_price=self.pos.entry_price,
            equity=self.equity_at(self.pos.entry_price if self.pos.side != "flat" else 0.0),
        )

    # ---- 权益计算（盯市）----
    def equity_at(self, close: float) -> float:
        if self.pos.side == "long":
            return self.cash + self.pos.qty * (close - self.pos.entry_price)
        if self.pos.side == "short":
            return self.cash + self.pos.qty * (self.pos.entry_price - close)
        return self.cash

    # ---- 成交价（叠加滑点）----
    def _buy_price(self, ref: float) -> float:
        return ref * (1 + self.slip)

    def _sell_price(self, ref: float) -> float:
        return ref * (1 - self.slip)

    # ================= 逐根流程 =================
    def fill_pending(self, open_price: float, ts: int, bar_index: int) -> None:
        """阶段 1：用本根开盘价成交上一根排队的订单。"""
        self._cur_bar = bar_index
        if not self._pending:
            return
        orders, self._pending = self._pending, []
        for od in orders:
            if od.action == "close":
                if self.pos.side != "flat":
                    self._close_at(open_price, ts, od.reason or "signal_close")
            elif od.action == "open" and self.pos.side == "flat":
                self._open(od.side, od.strength, open_price, ts, bar_index,
                           od.reason or "signal_open", risk_limit=od.risk_limit)

    def apply_risk(self, bar_index: int, row, _risk: RiskParams) -> None:
        """阶段 2：本根内风控（止损/止盈触发价强平；max_hold 到期排队次根平）。"""
        self._cur_bar = bar_index
        if self.pos.side == "flat":
            return
        high, low, ts = float(row["h"]), float(row["l"]), int(row["ts"])
        stop_p = self.pos.stop_loss_price
        tp_p = self.pos.take_profit_price
        hold_bars = self.pos.max_hold_bars

        if self.pos.side == "long":
            if stop_p is not None and low <= stop_p:               # 止损优先（保守）
                self._close_at(stop_p, ts, "stop_loss")
                return
            if tp_p is not None and high >= tp_p:
                self._close_at(tp_p, ts, "take_profit")
                return
        else:  # short
            if stop_p is not None and high >= stop_p:
                self._close_at(stop_p, ts, "stop_loss")
                return
            if tp_p is not None and low <= tp_p:
                self._close_at(tp_p, ts, "take_profit")
                return

        # max_hold：到期排队次根开盘平
        if hold_bars and (bar_index - self.pos.entry_bar) >= hold_bars:
            if not any(o.action == "close" for o in self._pending):
                self._pending.append(_Order(action="close", reason="max_hold"))

    def mark(self, ts: int, close: float) -> None:
        """阶段 3：盯市记录权益点。"""
        eq = self.equity_at(close)
        self.peak_equity = max(self.peak_equity, eq)
        dd = (eq / self.peak_equity - 1.0) if self.peak_equity > 0 else 0.0
        self.equity_points.append(EquityRecord(ts=ts, equity=eq, drawdown=min(dd, 0.0)))

    def submit(self, signals: list, *, open_risk_limit=None) -> None:
        """阶段 4：把策略信号翻译为排队订单（次根开盘成交）。"""
        projected = self.pos.side
        for sig in signals:
            side = sig.side
            reason = getattr(sig, "reason", "") or ""
            strength = float(getattr(sig, "strength", 1.0) or 1.0)
            if side == "close":
                if projected != "flat":
                    self._pending.append(_Order(action="close", reason=reason or "signal_close"))
                    projected = "flat"
            elif side in ("long", "short"):
                if projected == side:
                    continue
                if projected != "flat":            # 反手：先平再开
                    self._pending.append(_Order(action="close", reason=reason or "reverse"))
                self._pending.append(_Order(action="open", side=side, strength=max(0.0, min(strength, 1.0)),
                                            reason=reason or "signal_open", risk_limit=open_risk_limit))
                projected = side

    def finalize(self, ts: int, close: float) -> None:
        """会话/回测结束：仍持仓则按收盘价平仓。

        最后一根已在主循环 mark 过，故此处**就地更新**同 ts 的权益点（结算后含平仓费），
        避免产生重复时间戳（lightweight-charts 要求 time 严格递增）。
        """
        if self.pos.side == "flat":
            return
        self._close_at(close, ts, "session_end")
        eq = self.cash  # 平仓后为 flat，equity == cash
        self.peak_equity = max(self.peak_equity, eq)
        dd = (eq / self.peak_equity - 1.0) if self.peak_equity > 0 else 0.0
        point = EquityRecord(ts=ts, equity=eq, drawdown=min(dd, 0.0))
        if self.equity_points and self.equity_points[-1].ts == ts:
            self.equity_points[-1] = point
        else:
            self.equity_points.append(point)

    def close_at_price(self, px: float, ts: int, reason: str) -> None:
        """按**指定触发价**立即平仓（`_close_at` 的公有薄封装；持仓为 flat 时空转）。

        供 AI 回测的利润保护层按保护线成交（ai-trade-profit-protection-adaptive §2.3.3）。刻意不走
        「把 `pos.stop_loss_price` 改写成移动止盈线、借 `apply_risk` 的手平仓」那种取巧：那会让成交
        理由被记成 ``stop_loss``，归因当场撒谎——保护层的存在感恰恰来自「用户能看出利润是被谁锁住的」。

        私有 ``_close_at`` 与 ``run_backtest`` 的既有调用序**零改动**：本方法只是转调。
        """
        self._close_at(px, ts, reason)

    # ================= 成交内部实现 =================
    def _open(self, side, strength, ref_price, ts, bar_index, reason, *, risk_limit=None) -> None:
        strength = max(0.0, min(float(strength), 1.0))
        if risk_limit is not None:
            from ..ai_auto.sizing import risk_fraction_candidates
            cap, step = risk_limit
            full = self.cash * self.leverage
            if not math.isfinite(full) or full <= 0 or not math.isfinite(cap):
                return
            candidates = risk_fraction_candidates(min(strength, cap / full), step)
            strength = candidates[0] if candidates else 0.0
        if strength <= 0:
            return
        equity = self.cash  # 开仓时为 flat，equity == cash
        notional = strength * equity * self.leverage
        if notional <= 0:
            return
        fill = self._buy_price(ref_price) if side == "long" else self._sell_price(ref_price)
        if risk_limit is not None and _positive_float(fill) is None:
            return
        qty = notional / fill
        if risk_limit is not None and _positive_float(qty) is None:
            return
        fee = qty * fill * self.fee_rate
        self.cash -= fee
        self.fees_paid += fee
        risk = self._open_risk_snapshot(side, fill)
        self.pos = _Position(side=side, qty=qty, entry_price=fill, entry_bar=bar_index,
                             entry_fee=fee, **risk)
        self.trades.append(TradeRecord(
            ts=ts, side=side, price=fill, qty=qty, fee=fee, pnl=0.0,
            position_after=(qty if side == "long" else -qty), reason=reason, **risk,
        ))

    def _open_risk_snapshot(self, side: str, entry_price: float) -> dict:
        stop_loss_price = None
        take_profit_price = None
        if self._risk.stop_loss_pct is not None:
            if side == "long":
                stop_loss_price = entry_price * (1 - self._risk.stop_loss_pct)
            else:
                stop_loss_price = entry_price * (1 + self._risk.stop_loss_pct)
        if self._risk.take_profit_pct is not None:
            if side == "long":
                take_profit_price = entry_price * (1 + self._risk.take_profit_pct)
            else:
                take_profit_price = entry_price * (1 - self._risk.take_profit_pct)
        return {
            "stop_loss_pct": self._risk.stop_loss_pct,
            "take_profit_pct": self._risk.take_profit_pct,
            "max_hold_bars": self._risk.max_hold_bars,
            "stop_loss_price": stop_loss_price,
            "take_profit_price": take_profit_price,
        }

    def _close_at(self, ref_price, ts, reason) -> None:
        if self.pos.side == "flat":
            return
        side = self.pos.side
        qty = self.pos.qty
        entry = self.pos.entry_price
        fill = self._sell_price(ref_price) if side == "long" else self._buy_price(ref_price)
        gross = qty * (fill - entry) if side == "long" else qty * (entry - fill)
        exit_fee = qty * fill * self.fee_rate
        self.cash += gross - exit_fee
        self.fees_paid += exit_fee
        net_pnl = gross - (self.pos.entry_fee + exit_fee)
        self.trades.append(TradeRecord(
            ts=ts, side="close", price=fill, qty=qty, fee=exit_fee, pnl=net_pnl,
            position_after=0.0, reason=reason,
            stop_loss_pct=self.pos.stop_loss_pct,
            take_profit_pct=self.pos.take_profit_pct,
            max_hold_bars=self.pos.max_hold_bars,
            stop_loss_price=self.pos.stop_loss_price,
            take_profit_price=self.pos.take_profit_price,
        ))
        self.pos = _Position()
