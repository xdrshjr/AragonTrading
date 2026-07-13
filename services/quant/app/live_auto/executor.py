"""LiveExecutor —— 策略信号 → 真实 OKX 订单的纯编排层（live-auto-trading §2.2 / §2.3）。

把一份策略 `Signal` 列表按 SimBroker 的单向单仓语义翻译为对既有 `okx/trading.py` 服务层的
真实下单 / 平仓调用，从而继承全部安全护栏与审计（名义上限、净仓反向拦截、唯一 clOrdId、
`live_order_audit` 留痕、脱敏）。本层**不新增任何绕过护栏的下单路径**。

关键不变量：
- 反手走「先平后开」两步（`close_live_position` → `place_live_order`），不依赖反向单（会被
  `LIVE_WOULD_REDUCE_POSITION` 拦截）；net_mode 反手在 close 返回后须确认净仓清算再 open（R5）。
- 每根至多一次状态跃迁（一次平 + 一次开）；下单失败不崩会话（记 error 事件，待下次 reconcile）。
- 每次下单 / 平仓经 `get_sessionmaker()` 开**短生命周期** session 落审计（R3，绝不复用长 session）。
- 平仓收 `LIVE_NO_POSITION` 于平仓路径属良性（已平），归 flat 而非异常告警（R8）。
"""
from __future__ import annotations

import asyncio
import math

from ..config import Settings
from ..db.session import get_sessionmaker
from ..engine.simulator import BrokerContext, RiskParams
from ..okx.accounts import AccountCredentials
from ..okx.order_builder import ClosePositionInput, OrderInput, snap_size
from ..okx.rest_client import OKXError
from ..okx.trading import close_live_position, fetch_live_positions, place_live_order
from .explain import (
    as_close,
    build_atomic_explain,
    build_external_close_explain,
    build_reverse_explain,
    build_risk_exit_explain,
    build_session_stop_explain,
    with_risk,
)

# net_mode 反手清算确认（R5）：close 返回后短轮询净仓直至 pos==0，避免 open 被反向单护栏误拒。
_CLEAR_CONFIRM_MAX_TRIES = 6
_CLEAR_CONFIRM_INTERVAL_S = 0.5
# H4：停止会话平仓失败的有界重试间隔（次数由 settings.live_auto_flatten_max_retries 控制）。
_FLATTEN_RETRY_INTERVAL_S = 0.5


class LiveExecutor:
    """编排层：持有会话级凭证 / 预算 / 杠杆 / 标的元数据与自管持仓状态机。

    OKX 调用经三个可注入回调（默认走 `okx/trading.py` 服务层），便于纯/桩单测。
    """

    def __init__(self, *, creds: AccountCredentials, account_id: int, inst_id: str,
                 td_mode: str, pos_mode: str, leverage: float, budget_usd: float,
                 risk: RiskParams, ct_val: float, lot_sz: float, min_sz: float,
                 settings: Settings,
                 place_order_fn=None, close_position_fn=None, positions_fn=None):
        self.creds = creds
        self.account_id = account_id
        self.inst_id = inst_id
        self.td_mode = td_mode
        self.pos_mode = pos_mode
        self.leverage = leverage
        self.budget_usd = budget_usd
        self.risk = risk
        self.ct_val = ct_val
        self.lot_sz = lot_sz
        self.min_sz = min_sz
        self.settings = settings

        self.projected = "flat"            # 自管持仓状态机（镜像 SimBroker._Position.side）
        self.entry_price = 0.0
        self.entry_bar = -1
        self.entry_qty_sz = 0.0            # 自管持仓张数（contracts）
        self.entry_position_pct: float | None = None
        self.realized_pnl_usd = 0.0        # 累计已实现（平仓回执累加）
        self.unrealized_usd = 0.0          # 最近一次 reconcile 的浮盈（OKX upl）
        self.trades: list[dict] = []       # 已执行真单→Trade（驱动图表 markers）
        self._reconcile_fail_streak = 0    # H2：连续 reconcile 读取失败计数（成功即清零）
        self.degraded = False              # H2：盯市降级标志（连续失败达阈→True，恢复→False）

        self._place_order = place_order_fn or self._default_place_order
        self._close_position = close_position_fn or self._default_close_position
        self._read_positions = positions_fn or self._default_read_positions

    # ---------- 默认 OKX 调用（短生命周期 session，R3）----------
    async def _default_place_order(self, order: OrderInput) -> dict:
        sm = get_sessionmaker()
        async with sm() as s:
            return await place_live_order(s, self.creds, self.account_id, order,
                                          settings=self.settings)

    async def _default_close_position(self, close: ClosePositionInput) -> dict:
        sm = get_sessionmaker()
        async with sm() as s:
            return await close_live_position(s, self.creds, self.account_id, close)

    async def _default_read_positions(self) -> list[dict]:
        return await fetch_live_positions(self.creds)

    # ---------- 对外只读上下文（供 strategy.on_candle）----------
    @property
    def session_pnl_usd(self) -> float:
        """会话累计盈亏 = 已实现 + 当前持仓未实现（reconcile 自 OKX upl）。"""
        return self.realized_pnl_usd + self.unrealized_usd

    def context(self, bar_index: int, mark_px: float) -> BrokerContext:
        bars = (bar_index - self.entry_bar) if self.projected != "flat" else 0
        equity = self.budget_usd + self.realized_pnl_usd + self._unrealized_at(mark_px)
        return BrokerContext(position_side=self.projected, bars_in_position=bars,
                             entry_price=self.entry_price, equity=equity)

    def position_payload(self, mark_px: float) -> dict | None:
        if self.projected == "flat":
            return None
        payload = {"side": self.projected, "size": self.entry_qty_sz,
                   "avgEntry": self.entry_price, "markPrice": mark_px,
                   "unrealizedPnl": self.unrealized_usd or self._unrealized_at(mark_px)}
        if self.entry_position_pct is not None:
            payload["positionPct"] = self.entry_position_pct
        return payload

    # ---------- 核心：每根确认 K 线驱动 ----------
    async def apply(self, signals: list, *, ref_px: float, bar_index: int,
                    ts: int) -> list[dict]:
        """把信号翻译为真实订单，返回供 WS 扇出的事件列表（先时间止盈再处理信号）。"""
        self._bar_cursor = bar_index
        events: list[dict] = []
        events += await self._maybe_time_exit(ref_px, ts)
        for action in self._project_signals(signals):
            if action[0] == "close":
                _, reason, sig, close_kind = action
                events += await self._do_close(ref_px, ts, reason, signal=sig,
                                               close_kind=close_kind)
            else:
                _, side, strength, reason, reverse, sig = action
                events += await self._do_open(side, strength, ref_px, bar_index, ts,
                                              reason, require_clear=reverse, signal=sig)
        return events

    def _project_signals(self, signals: list) -> list[tuple]:
        """收敛信号为有序动作（镜像 SimBroker.submit）：同向跳过、反向先平后开、平仓即平。

        动作元组透传原始 `Signal`（携 `.explain`）与平仓类别 `close_kind`，使开 / 平能挂上
        对应解释（factor_score 满因子归因 / 反手 / 信号平仓），下游解耦于决策来源。
        """
        projected = self.projected
        actions: list[tuple] = []
        for sig in signals:
            side = sig.side
            reason = (getattr(sig, "reason", "") or "")
            strength = max(0.0, min(float(getattr(sig, "strength", 1.0) or 1.0), 1.0))
            if side == "close":
                if projected != "flat":
                    actions.append(("close", reason or "signal_close", sig, "signal"))
                    projected = "flat"
            elif side in ("long", "short"):
                if projected == side:
                    continue
                reverse = projected != "flat"
                if reverse:
                    actions.append(("close", reason or "reverse", sig, "reverse"))
                actions.append(("open", side, strength, reason or "signal_open", reverse, sig))
                projected = side
        return actions

    async def _maybe_time_exit(self, ref_px: float, ts: int) -> list[dict]:
        """时间止盈（max_hold_bars）：达到持仓根数即主动平仓（对齐 SimBroker.apply_risk）。"""
        hold = self.risk.max_hold_bars
        if self.projected == "flat" or not hold:
            return []
        if (self._cur_bar - self.entry_bar) < hold:
            return []
        return await self._do_close(ref_px, ts, "max_hold", close_kind="max_hold")

    # ---------- 开 / 平 ----------
    async def _do_open(self, side: str, strength: float, ref_px: float, bar_index: int,
                       ts: int, reason: str, *, require_clear: bool, signal=None) -> list[dict]:
        sz = self._compute_sz(strength, ref_px)
        if sz <= 0 or sz < self.min_sz:
            return [self._status_event(
                f"预算 / 杠杆过小，单笔不足最小下单量（{side}），已跳过该信号")]
        if require_clear and not await self._confirm_net_cleared():
            return [self._status_event("反手平仓尚未在交易所侧清算，本根跳过开仓，待下一根重试")]
        tp, sl = self._risk_trigger_px(side, ref_px)
        order = OrderInput(inst_id=self.inst_id, direction=side, ord_type="market",
                           td_mode=self.td_mode, px=None, sz=sz, lever=self.leverage,
                           tp_trigger_px=tp, sl_trigger_px=sl)
        try:
            result = await self._place_order(order)
        except OKXError as exc:
            return [self._error_event(ts, exc)]
        self.projected = side
        self.entry_price = ref_px
        self.entry_bar = bar_index
        self.entry_qty_sz = sz
        self.entry_position_pct = self._position_pct_for_size(
            sz=sz, ct_val=self.ct_val, reference_price=ref_px,
            member_budget_usd=self.budget_usd, leverage=self.leverage)
        explain = self._open_explain(side, reason, signal, tp, sl)
        cl_ord_id, ord_id = self._order_ids(result)
        trade = self._make_trade(ts, side, ref_px, sz, pnl=0.0, reason=reason, tp=tp, sl=sl,
                                 explain=explain, cl_ord_id=cl_ord_id, ord_id=ord_id,
                                 position_pct=self.entry_position_pct)
        self.trades.append(trade)
        return [{"type": "trade", "data": trade}]

    async def _do_close(self, ref_px: float, ts: int, reason: str, *, signal=None,
                        close_kind: str = "signal") -> list[dict]:
        if self.projected == "flat":
            return []
        close = ClosePositionInput(
            inst_id=self.inst_id, pos_side=self._pos_side(), pos=self._signed_pos(),
            mgn_mode=self.td_mode, ord_type="market", px=None, sz=None)
        # 解释须在 _settle_close 复位 projected / entry 前据当前持仓断面合成（信号平仓优先用因子解释）。
        explain = self._close_explain(close_kind, reason, signal, ref_px)
        try:
            result = await self._close_position(close)
        except OKXError as exc:
            if exc.code == "LIVE_NO_POSITION":
                return self._settle_benign_close(ref_px, ts)
            return [self._error_event(ts, exc)]
        return self._settle_close(ref_px, ts, reason, result, explain=explain)

    def _settle_close(self, ref_px: float, ts: int, reason: str, result: dict, *,
                      explain: dict | None = None) -> list[dict]:
        pnl = self._close_pnl(ref_px)
        pnl_pct = self._close_return_pct(ref_px)   # v2（P1-2）：须在复位 entry/projected 前算
        qty = self.entry_qty_sz
        position_pct = self.entry_position_pct
        self.realized_pnl_usd += pnl
        self.unrealized_usd = 0.0
        self.projected = "flat"
        self.entry_qty_sz = 0.0
        self.entry_position_pct = None
        cl_ord_id, ord_id = self._order_ids(result)
        trade = self._make_trade(ts, "close", ref_px, qty, pnl=pnl, pnl_pct=pnl_pct,
                                 reason=reason, explain=explain, cl_ord_id=cl_ord_id,
                                 ord_id=ord_id, position_pct=position_pct)
        self.trades.append(trade)
        return [{"type": "trade", "data": trade}]

    def _settle_benign_close(self, ref_px: float, ts: int) -> list[dict]:
        """平仓收 LIVE_NO_POSITION（交易所侧 OCO 已先平）：合成只读外部平仓成交 + 良性归 flat（R8）。

        v2（P1-1）：在归 flat 前合成一条 kind="external_close" 成交，记录已发生的交易所侧平仓；
        **不调用任何下单 / 平仓接口**，best-effort + 异常隔离。
        """
        events = self._synthesize_external_close(ref_px, ts, source="oco")
        self.realized_pnl_usd += self._close_pnl(ref_px)
        self.unrealized_usd = 0.0
        self.projected = "flat"
        self.entry_qty_sz = 0.0
        self.entry_position_pct = None
        return events + [self._status_event("标的已在交易所侧平仓（OCO 触发），自管持仓归 flat")]

    async def _confirm_net_cleared(self) -> bool:
        """net_mode 反手：短轮询确认该 instId 净仓 pos==0 再 open（R5）。"""
        if self.pos_mode != "net_mode":
            return True
        for attempt in range(_CLEAR_CONFIRM_MAX_TRIES):
            if self._net_abs_pos(await self._read_positions()) <= 0:
                return True
            if attempt < _CLEAR_CONFIRM_MAX_TRIES - 1:
                await asyncio.sleep(_CLEAR_CONFIRM_INTERVAL_S)
        return False

    # ---------- reconcile / flatten ----------
    async def reconcile(self, mark_px: float, ts: int) -> list[dict]:
        """持仓真源校正（§2.5）：拉 OKX 持仓校正 projected 并更新未实现盈亏。

        H2：读取一律经 `except OKXError` 兜住（传输层异常已被 private/trade_client 归一为
        `OKXPrivateError(OKXError)`），失败不冒泡污染行情链路；连续失败达阈推降级事件，恢复后清零。
        """
        try:
            positions = await self._read_positions()
        except OKXError as exc:
            return self._on_reconcile_failure(ts, exc)
        return self._on_reconcile_ok(mark_px, positions, ts)

    def _on_reconcile_ok(self, mark_px: float, positions: list[dict], ts: int) -> list[dict]:
        events = self._clear_degraded_if_recovered()
        abs_pos = self._net_abs_pos(positions)
        self.unrealized_usd = self._net_upl(positions)
        if self.projected != "flat" and abs_pos <= 0:
            # v2（P1-1）：侦测到交易所侧 / 外部已平仓，合成只读 external_close 成交后再校正为 flat。
            events += self._synthesize_external_close(mark_px, ts, source="external")
            self.realized_pnl_usd += self._close_pnl(mark_px)
            self.unrealized_usd = 0.0
            self.projected = "flat"
            self.entry_qty_sz = 0.0
            self.entry_position_pct = None
            events.append(self._status_event("持仓已在交易所侧平仓 / 外部干预，已校正为 flat"))
        return events

    def _clear_degraded_if_recovered(self) -> list[dict]:
        """成功 reconcile：清零失败计数；若此前降级则消降级并推一条「已恢复」status。"""
        self._reconcile_fail_streak = 0
        if self.degraded:
            self.degraded = False
            return [self._status_event("账户连接已恢复，盯市校正恢复正常")]
        return []

    def _on_reconcile_failure(self, ts: int, exc: OKXError) -> list[dict]:
        """失败 reconcile：保留 projected / 上次浮盈不动；达阈仅推一次降级 error（避免刷屏）。"""
        self._reconcile_fail_streak += 1
        events: list[dict] = [self._status_event(
            "账户 / 行情连接抖动，本次盯市跳过（不影响已挂交易所侧止盈止损）")]
        if (self._reconcile_fail_streak >= self.settings.live_auto_reconcile_max_failures
                and not self.degraded):
            self.degraded = True
            events.append({"type": "error", "data": {
                "ts": int(ts), "code": "LIVE_AUTO_RECONCILE_DEGRADED",
                "message": f"账户连接连续 {self._reconcile_fail_streak} 次读取失败，盯市已降级"
                           f"（[{exc.code}]；交易所侧止盈止损仍生效，请检查网络 / 凭证）"}})
        return events

    async def flatten(self, ref_px: float, ts: int) -> dict:
        """停止会话时平掉自管持仓（市价，autoCxl 撤随单 OCO）。绝不触碰非自管持仓。

        H4：平仓失败有界重试（`settings.live_auto_flatten_max_retries` 次 / `0.5s`）；仍失败则
        **保留**残留仓（不改 projected）并结构化返回 `residual`，绝不假报干净停止。
        """
        if self.projected == "flat":
            return {"ok": True, "closedSz": 0}
        close = ClosePositionInput(
            inst_id=self.inst_id, pos_side=self._pos_side(), pos=self._signed_pos(),
            mgn_mode=self.td_mode, ord_type="market", px=None, sz=None)
        last_exc: OKXError | None = None
        for attempt in range(self.settings.live_auto_flatten_max_retries + 1):
            try:
                result = await self._close_position(close)
            except OKXError as exc:
                if exc.code == "LIVE_NO_POSITION":
                    benign = self._settle_benign_close(ref_px, ts)
                    trade = next((e["data"] for e in benign if e["type"] == "trade"), None)
                    return {"ok": True, "closedSz": 0, "note": "already_flat", "trade": trade}
                last_exc = exc
                if attempt < self.settings.live_auto_flatten_max_retries:
                    await asyncio.sleep(_FLATTEN_RETRY_INTERVAL_S)
                continue
            return self._settle_flatten_ok(ref_px, ts, result)
        return {"ok": False, "code": last_exc.code, "message": last_exc.message,
                "residual": {"side": self.projected, "size": self.entry_qty_sz}}

    def _settle_flatten_ok(self, ref_px: float, ts: int, result: dict) -> dict:
        # session_stop 解释须在复位前据当前持仓断面合成（会话停止 / 会话级金额止盈止损的整仓平）。
        explain = build_session_stop_explain(
            pos_side=self.projected, entry_price=self.entry_price, exit_price=ref_px,
            pnl=self._close_pnl(ref_px), session_pnl=self.session_pnl_usd)
        events = self._settle_close(ref_px, ts, "session_stop", result, explain=explain)
        trade = events[0]["data"] if events else None
        return {"ok": True, "closedSz": trade["qty"] if trade else 0,
                "clOrdId": result.get("clOrdId"), "trade": trade}

    # ---------- 纯计算辅助 ----------
    @property
    def _cur_bar(self) -> int:
        """当前根序（time_exit 用）：由 apply 写入；未驱动时回落 entry_bar。"""
        return getattr(self, "_bar_cursor", self.entry_bar)

    def _compute_sz(self, strength: float, ref_px: float) -> float:
        values = (strength, ref_px, self.ct_val, self.budget_usd, self.leverage)
        if not all(math.isfinite(value) for value in values):
            return 0.0
        if ref_px <= 0 or self.ct_val <= 0:
            return 0.0
        target_notional = max(0.0, min(strength, 1.0)) * self.budget_usd * self.leverage
        if not math.isfinite(target_notional):
            return 0.0
        raw = target_notional / (self.ct_val * ref_px)
        if not math.isfinite(raw):
            return 0.0
        return snap_size(raw, self.lot_sz)

    @staticmethod
    def _position_pct_for_size(*, sz: float, ct_val: float,
                               reference_price: float, member_budget_usd: float,
                               leverage: float) -> float | None:
        values = (sz, ct_val, reference_price, member_budget_usd, leverage)
        if not all(math.isfinite(value) and value > 0 for value in values):
            return None
        order_notional = sz * ct_val * reference_price
        budget_margin = order_notional / leverage
        if not math.isfinite(order_notional) or not math.isfinite(budget_margin):
            return None
        position_pct = budget_margin / member_budget_usd
        if not math.isfinite(position_pct):
            return None
        return max(0.0, min(position_pct, 1.0))

    def _risk_trigger_px(self, side: str, fill: float) -> tuple[float | None, float | None]:
        """策略风控 pct → 交易所侧 OCO 触发价（多空对称，对齐 SimBroker._open_risk_snapshot）。"""
        slp = self.risk.stop_loss_pct
        tpp = self.risk.take_profit_pct
        tp = sl = None
        if slp is not None:
            sl = fill * (1 - slp) if side == "long" else fill * (1 + slp)
        if tpp is not None:
            tp = fill * (1 + tpp) if side == "long" else fill * (1 - tpp)
        return tp, sl

    def _close_pnl(self, exit_px: float) -> float:
        qty_coin = self.entry_qty_sz * self.ct_val
        if self.projected == "long":
            return qty_coin * (exit_px - self.entry_price)
        if self.projected == "short":
            return qty_coin * (self.entry_price - exit_px)
        return 0.0

    def _close_return_pct(self, exit_px: float) -> float | None:
        """平仓收益率：已实现盈亏 / 开仓占用保证金（等价于方向化价格变动% × 杠杆）。

        v2（P1-2）：先校验后相除——杠杆或名义价值非正（异常断面）直接返回 None，杜绝在
        未被 try/except 包裹的 _settle_close 调用栈抛 ZeroDivisionError；缺收益率即省略 pnlPct 键。
        """
        notional = self.entry_qty_sz * self.ct_val * self.entry_price
        if self.leverage <= 0 or notional <= 0:
            return None
        return self._close_pnl(exit_px) / (notional / self.leverage)

    def _unrealized_at(self, mark_px: float) -> float:
        if self.projected == "long":
            return self.entry_qty_sz * self.ct_val * (mark_px - self.entry_price)
        if self.projected == "short":
            return self.entry_qty_sz * self.ct_val * (self.entry_price - mark_px)
        return 0.0

    def _pos_side(self) -> str:
        return "net" if self.pos_mode == "net_mode" else self.projected

    def _signed_pos(self) -> float:
        return self.entry_qty_sz if self.projected == "long" else -self.entry_qty_sz

    def _net_abs_pos(self, positions: list[dict]) -> float:
        return sum(abs(p.get("pos") or 0.0) for p in positions
                   if str(p.get("instId")) == self.inst_id)

    def _net_upl(self, positions: list[dict]) -> float:
        return sum(p.get("upl") or 0.0 for p in positions
                   if str(p.get("instId")) == self.inst_id)

    def _make_trade(self, ts: int, side: str, price: float, sz: float, *, pnl: float,
                    reason: str, pnl_pct: float | None = None, tp: float | None = None,
                    sl: float | None = None, explain: dict | None = None,
                    cl_ord_id: str | None = None, ord_id: str | None = None,
                    seeded: bool = False, position_pct: float | None = None) -> dict:
        position_after = sz if side == "long" else (-sz if side == "short" else 0.0)
        trade = {"ts": int(ts), "side": side, "price": float(price), "qty": float(sz),
                 "fee": 0.0, "pnl": float(pnl), "positionAfter": position_after,
                 "reason": reason, "stopLossPct": self.risk.stop_loss_pct,
                 "takeProfitPct": self.risk.take_profit_pct,
                 "maxHoldBars": self.risk.max_hold_bars,
                 "stopLossPrice": sl, "takeProfitPrice": tp, "seeded": seeded,
                 "explain": explain}
        # pnlPct 为平仓派生收益率（开仓 / 退化断面省略键，与 clOrdId/ordId 同样的「缺失即省略」风格）。
        if pnl_pct is not None:
            trade["pnlPct"] = float(pnl_pct)
        if position_pct is not None and math.isfinite(position_pct):
            trade["positionPct"] = float(position_pct)
        # clOrdId / ordId 为 best-effort 可选（开仓取 _place_order 回执、平仓取 result.clOrdId）：
        # 缺失即省略键，绝不阻塞 trade / explain（P1-2）。
        if cl_ord_id:
            trade["clOrdId"] = cl_ord_id
        if ord_id:
            trade["ordId"] = ord_id
        return trade

    # ---------- 解释合成（纯函数、异常隔离，绝不进入 place / close 调用栈，R1）----------
    def _risk_snapshot(self, tp: float | None, sl: float | None) -> dict:
        """开仓时的风控快照（交易所侧 OCO 触发价 + 策略 pct + max_hold），随 explain 落库。"""
        return {"stopLossPrice": sl, "takeProfitPrice": tp,
                "stopLossPct": self.risk.stop_loss_pct,
                "takeProfitPct": self.risk.take_profit_pct,
                "maxHoldBars": self.risk.max_hold_bars}

    def _open_explain(self, side: str, reason: str, signal, tp: float | None,
                      sl: float | None) -> dict | None:
        """开仓解释：factor_score 用信号满因子归因 + 风控快照；原子策略走通用构造器。"""
        risk = self._risk_snapshot(tp, sl)
        sig_explain = getattr(signal, "explain", None) if signal is not None else None
        if sig_explain:
            return with_risk(sig_explain, risk)
        return build_atomic_explain(side, reason or "signal_open", risk=risk)

    def _close_explain(self, close_kind: str, reason: str, signal, ref_px: float) -> dict | None:
        """平仓解释：按类别选构造器（max_hold / reverse / 信号平仓优先用因子断面）。"""
        pnl = self._close_pnl(ref_px)
        if close_kind == "max_hold":
            return build_risk_exit_explain(
                bars_held=self._cur_bar - self.entry_bar, max_hold_bars=self.risk.max_hold_bars,
                entry_price=self.entry_price, exit_price=ref_px, pnl=pnl)
        if close_kind == "reverse":
            return build_reverse_explain(pos_side=self.projected, entry_price=self.entry_price,
                                         exit_price=ref_px, pnl=pnl)
        sig_explain = getattr(signal, "explain", None) if signal is not None else None
        if sig_explain:
            return as_close(sig_explain)
        return build_atomic_explain("close", reason or "signal_close")

    def _synthesize_external_close(self, ref_px: float, ts: int, *, source: str) -> list[dict]:
        """合成一条交易所侧 / 外部平仓的只读成交（仅记录已发生的平仓，不下任何单；best-effort，R11）。"""
        if self.projected == "flat" or self.entry_qty_sz <= 0:
            return []
        try:
            pnl = self._close_pnl(ref_px)
            pnl_pct = self._close_return_pct(ref_px)   # v2（P1-2）：复位前算，先校验后相除
            explain = build_external_close_explain(
                pos_side=self.projected, source=source, entry_price=self.entry_price,
                exit_price=ref_px, pnl=pnl)
            trade = self._make_trade(ts, "close", ref_px, self.entry_qty_sz, pnl=pnl,
                                     pnl_pct=pnl_pct, reason="external_close", explain=explain,
                                     position_pct=self.entry_position_pct)
            self.trades.append(trade)
            return [{"type": "trade", "data": trade}]
        except Exception:  # noqa: BLE001 —— 合成失败回落原 status，绝不影响 reconcile / flatten 主流程
            return []

    @staticmethod
    def _order_ids(result) -> tuple[str | None, str | None]:
        """从下单 / 平仓回执 best-effort 取 (clOrdId, ordId)；非 dict 或缺失安全留空（P1-2）。"""
        if not isinstance(result, dict):
            return None, None
        cl = result.get("clOrdId")
        od = result.get("ordId")
        return (str(cl) if cl else None), (str(od) if od else None)

    def _status_event(self, message: str) -> dict:
        return {"type": "status", "data": {"state": "running", "message": message,
                                           "sessionPnlUsd": self.session_pnl_usd}}

    def _error_event(self, ts: int, exc: OKXError) -> dict:
        return {"type": "error", "data": {"ts": int(ts), "code": exc.code,
                                          "message": exc.message}}
