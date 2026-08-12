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
import time

from ..config import Settings
from ..db.session import get_sessionmaker
from ..engine.simulator import BrokerContext, RiskParams
from ..okx.accounts import AccountCredentials
from ..okx.order_builder import (
    CancelOrderInput,
    ClosePositionInput,
    OrderInput,
    snap_size,
)
from ..okx.rest_client import OKXError
from ..okx.trading import (
    cancel_live_order,
    close_live_position,
    fetch_live_fills,
    fetch_live_order,
    fetch_live_positions,
    place_live_order,
)
from .fee_ledger import FeeLedger, audit_fills, trade_fee_fields
from .explain import (
    as_close,
    build_atomic_explain,
    build_external_close_explain,
    build_reverse_explain,
    build_risk_exit_explain,
    build_session_stop_explain,
    with_risk,
)
from .resting import (
    RestingOrder,
    adopt_fill_delta,
    build_resting,
    classify_state,
    fill_delta,
    is_expired,
    is_same_intent,
)

# net_mode 反手清算确认（R5）：close 返回后短轮询净仓直至 pos==0，避免 open 被反向单护栏误拒。
_CLEAR_CONFIRM_MAX_TRIES = 6
_CLEAR_CONFIRM_INTERVAL_S = 0.5
# H4：停止会话平仓失败的有界重试间隔（次数由 settings.live_auto_flatten_max_retries 控制）。
_FLATTEN_RETRY_INTERVAL_S = 0.5


def _plan_limit_px(order_plan) -> float | None:
    """`OrderPlan | None` → 限价委托价；市价 / 无计划一律 None（= 今日行为，逐字零回归）。"""
    if order_plan is None or getattr(order_plan, "ord_type", "market") != "limit":
        return None
    return getattr(order_plan, "px", None)


class LiveExecutor:
    """编排层：持有会话级凭证 / 预算 / 杠杆 / 标的元数据与自管持仓状态机。

    OKX 调用经五个可注入回调（默认走 `okx/trading.py` 服务层），便于纯/桩单测。

    限价挂单（ai-trade-limit-orders）：`apply(..., order_plan=None)` 默认市价——`live_auto` 的全部
    调用点一字不改，payload 逐字节相同（R-9）。仅 `ai_auto` 会传 `order_plan`，届时至多一笔挂单
    （`self.resting`，不变式 I-1）经 `_poll_resting` 向 OKX 权威核对成交 / 撤销 / 超时。
    """

    def __init__(self, *, creds: AccountCredentials, account_id: int, inst_id: str,
                 td_mode: str, pos_mode: str, leverage: float, budget_usd: float,
                 risk: RiskParams, ct_val: float, lot_sz: float, min_sz: float,
                 settings: Settings,
                 place_order_fn=None, close_position_fn=None, positions_fn=None,
                 cancel_order_fn=None, get_order_fn=None, fills_fn=None,
                 fee_ledger: FeeLedger | None = None,
                 bar_seconds: int = 0, bar_anchor_ts: int = 0):
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
        self.fee_ledger = fee_ledger       # §3.2.0 第七缝：只有 ai_auto 传；None ⇒ live_auto 逐字回退
        # 成交时钟对齐的两个缝（discipline-preset-and-integrity 轴 D）：**任一为 0 ⇒ 不对齐**，
        # 故 `live_auto` 的调用点一字不改即逐字节回退。`bar_anchor_ts` 由 `ai_auto` 的会话在每根
        # 已确认 K 线落地时同步一次（相位由交易所给的真实根决定，不猜时区，见 `_align_bar_ts`）。
        self.bar_seconds = int(bar_seconds or 0)
        self.bar_anchor_ts = int(bar_anchor_ts or 0)
        self.dust_skips = 0                # [P0-4]：因解算量 < minSz 而跳过开仓的次数（纪律条读它）
        # [P0-4]：启动期按预设保守仓位下界复核出的「解算量可能不足最小下单量」结论。**只有 ai_auto
        # 的 `_build_executor` 会置真**；本类自己不算它（它需要预设的 f_floor，那是 AI 侧的概念）。
        self.dust_risk = False
        self.entry_fee_usd = 0.0           # 当前持仓已累计的**开仓腿**费率（三处复位点，§3.3b）

        self.projected = "flat"            # 自管持仓状态机（镜像 SimBroker._Position.side）
        self.entry_price = 0.0
        self.entry_bar = -1
        self.entry_qty_sz = 0.0            # 自管持仓张数（contracts）
        self.entry_position_pct: float | None = None
        self.realized_pnl_usd = 0.0        # 累计已实现（平仓回执累加）
        self.unrealized_usd = 0.0          # 最近一次 reconcile 的浮盈（净额口径，B-2）
        self.gross_unrealized_usd = 0.0    # 同一次 reconcile 的**毛**浮盈（只读，供对账/排查）
        self.trades: list[dict] = []       # 已执行真单→Trade（驱动图表 markers）
        self._reconcile_fail_streak = 0    # H2：连续 reconcile 读取失败计数（成功即清零）
        self.degraded = False              # H2：盯市降级标志（连续失败达阈→True，恢复→False）
        # 当前挂着的那一笔限价委托（不变式 I-1：单会话至多一笔）。恒为 None ⇒ 市价路径 ⇒ live_auto
        # 的每一处判定都短路，行为与本特性合入前逐字相同。
        self.resting: RestingOrder | None = None
        # 刚采纳完开仓成交的一轮宽限（O-3，见 `_on_reconcile_ok`）。恒为 False ⇒ live_auto 零影响。
        self._fill_grace = False

        self._place_order = place_order_fn or self._default_place_order
        self._close_position = close_position_fn or self._default_close_position
        self._read_positions = positions_fn or self._default_read_positions
        # P1-2：挂单生命周期依赖的两个新缝（镜像上方三缝的既有范式）——没有它们，限价整组单测无法脱网。
        self._cancel_order = cancel_order_fn or self._default_cancel_order
        self._get_order = get_order_fn or self._default_get_order
        self._read_fills = fills_fn or self._default_read_fills

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

    async def _default_cancel_order(self, cancel: CancelOrderInput) -> dict:
        sm = get_sessionmaker()
        async with sm() as s:
            return await cancel_live_order(s, self.creds, self.account_id, cancel)

    async def _default_get_order(self, cl_ord_id: str) -> dict:
        return await fetch_live_order(self.creds, self.inst_id, cl_ord_id=cl_ord_id)

    async def _default_read_fills(self) -> list[dict]:
        return await fetch_live_fills(self.creds, self.inst_id, limit=100)

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

    def risk_trigger_px(self, side: str, fill: float) -> tuple[float | None, float | None]:
        """交易所侧 OCO 触发价的**只读**转发（ai-trade-profit-protection §2.1）。

        存在的唯一理由是让「UI / prompt 展示的兜底价」与「真实下单时附的那两个价」共用同一份公式；
        零行为改动、零既有调用点变更，`live_auto` payload 逐字节相同。

        **它只是回落路径**（P1-4）：限价成交时 `entry_price` 被 `_adopt_fill` 置为真实成交价 `avgPx`，
        而随单 OCO 是按**挂单价** `order.px` 附的，照 `entry_price` 现算会得到一个交易所侧根本不存在
        的价。首选真源恒为最近一笔开仓 `trade` 的 `takeProfitPrice` / `stopLossPrice`。
        """
        return self._risk_trigger_px(side, fill)

    # ---------- 核心：每根确认 K 线驱动 ----------
    async def apply(self, signals: list, *, ref_px: float, bar_index: int,
                    ts: int, order_plan=None) -> list[dict]:
        """把信号翻译为真实订单，返回供 WS 扇出的事件列表（先时间止盈再处理信号）。

        `order_plan`（keyword-only，默认 None → 市价，`live_auto` 全部调用点一字不改、payload 逐字节
        相同，R-9）：本轮的订单类型意图。**只绑定终态腿**（`_project_signals` 产出的最后一个动作，
        P1-6）——反手的 close 腿一律强制市价：它是「为开新仓而必须先清掉的障碍」，不是 Agent 的价格
        意图所在；且若 close 腿挂起，`_confirm_net_cleared` 必然失败 → open 腿被跳过 → 会话陷入
        「一笔挂着的平仓单 + 没开成的新仓」的半反手态，还占掉了 I-1 的唯一挂单额度。
        """
        self._bar_cursor = bar_index
        events: list[dict] = []
        events += await self._maybe_time_exit(ref_px, ts)
        actions = self._project_signals(signals)
        for index, action in enumerate(actions):
            plan = order_plan if index == len(actions) - 1 else None   # P1-6：只绑终态腿
            if action[0] == "close":
                _, reason, sig, close_kind = action
                events += await self._do_close(ref_px, ts, reason, signal=sig,
                                               close_kind=close_kind, order_plan=plan)
            else:
                _, side, strength, reason, reverse, sig = action
                events += await self._do_open(side, strength, ref_px, bar_index, ts,
                                              reason, require_clear=reverse, signal=sig,
                                              order_plan=plan)
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
                       ts: int, reason: str, *, require_clear: bool, signal=None,
                       order_plan=None) -> list[dict]:
        sz = self._compute_sz(strength, ref_px)
        if sz <= 0 or sz < self.min_sz:
            # [P0-4]：这条 status 文案在 AI-trade 前端**没有渲染落点**，装配仓位层后它又会从
            # 「预检盲区」变成常态（真实下单量是 f×budget，f 常落在 0.2~0.4）。计数使纪律条能说出
            # 「本会话已有 N 次因不足最小下单量跳过开仓」——否则现象与「AI 一直不开单」完全一致。
            self.dust_skips += 1
            return [self._status_event(
                f"预算 / 杠杆过小，单笔不足最小下单量（{side}），已跳过该信号")]
        limit_px = _plan_limit_px(order_plan)
        pre, slot_free = await self._prepare_resting_slot(ts, "open", side, limit_px)
        if not slot_free:
            return pre
        if require_clear and not await self._confirm_net_cleared():
            return pre + [self._status_event(
                "反手平仓尚未在交易所侧清算，本根跳过开仓，待下一根重试")]
        # §2.7：限价单的**预期成交价是 limit_px 而非 ref_px**——随单 OCO 若仍以 ref_px 为基准，
        # 止损距离会系统性偏移（挂单越远偏得越多）。这是限价特有的正确性要求，非可选优化。
        tp, sl = self._risk_trigger_px(side, ref_px if limit_px is None else limit_px)
        order = OrderInput(inst_id=self.inst_id, direction=side,
                           ord_type="market" if limit_px is None else "limit",
                           td_mode=self.td_mode, px=limit_px, sz=sz, lever=self.leverage,
                           tp_trigger_px=tp, sl_trigger_px=sl)
        try:
            result = await self._place_order(order)
        except OKXError as exc:
            return pre + [self._error_event(ts, exc)]
        if limit_px is not None:      # I-2a：挂单期间**不写 projected**，绕开幻影成交陷阱（R-1）
            return pre + self._enter_resting(result, ts, intent="open", side=side,
                                             px=limit_px, sz=sz, strength=strength,
                                             reason=reason)
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
                                 position_pct=self.entry_position_pct, expected_px=ref_px)
        self.entry_fee_usd += float(trade["fee"])
        self.trades.append(trade)
        return pre + [{"type": "trade", "data": trade}]

    async def _do_close(self, ref_px: float, ts: int, reason: str, *, signal=None,
                        close_kind: str = "signal", order_plan=None) -> list[dict]:
        if self.projected == "flat":
            return []
        limit_px = _plan_limit_px(order_plan)
        pre, slot_free = await self._prepare_resting_slot(ts, "close", self.projected, limit_px)
        if not slot_free:
            return pre
        close = ClosePositionInput(
            inst_id=self.inst_id, pos_side=self._pos_side(), pos=self._signed_pos(),
            mgn_mode=self.td_mode, ord_type="market" if limit_px is None else "limit",
            px=limit_px, sz=None if limit_px is None else self.entry_qty_sz)
        # 解释须在 _settle_close 复位 projected / entry 前据当前持仓断面合成（信号平仓优先用因子解释）。
        explain = self._close_explain(close_kind, reason, signal, ref_px)
        try:
            result = await self._close_position(close)
        except OKXError as exc:
            if exc.code == "LIVE_NO_POSITION":
                return pre + self._settle_benign_close(ref_px, ts)
            return pre + [self._error_event(ts, exc)]
        if limit_px is not None:      # I-2b：平仓挂单期间 projected **保持原方向**（真仓确实还在）
            # sz 取服务层回执的 closedSz（已按 lotSz 取整且 ≤ 实时 |pos|），而非本地 entry_qty_sz。
            resting_sz = float(result.get("closedSz") or self.entry_qty_sz)
            return pre + self._enter_resting(result, ts, intent="close", side=self.projected,
                                             px=limit_px, sz=resting_sz, strength=0.0,
                                             reason=reason)
        return pre + self._settle_close(ref_px, ts, reason, result, explain=explain)

    # ---------- 挂单生命周期（ai-trade-limit-orders §2.3/§2.5；resting is None 时全部短路）----------
    def _enter_resting(self, result: dict, ts: int, *, intent: str, side: str, px: float,
                       sz: float, strength: float, reason: str) -> list[dict]:
        """下单回执 → `self.resting`；**绝不**乐观置位 projected（I-2a/I-2b，R-1 的正面防线）。"""
        cl_ord_id, ord_id = self._order_ids(result)
        self.resting = build_resting(
            cl_ord_id=cl_ord_id or "", ord_id=ord_id or "", intent=intent, side=side,
            px=px, sz=sz, strength=strength, reason=reason, now_ts=ts,
            ttl_s=self.settings.ai_trade_limit_order_ttl_s)
        label = "限价开仓" if intent == "open" else "限价平仓"
        return [self._status_event(
            f"已挂出{label}委托：{sz} 张 @ {px}（{int(self.settings.ai_trade_limit_order_ttl_s)}s "
            f"未成交则自动撤销）")]

    def _cancel_input(self, order: RestingOrder) -> CancelOrderInput:
        """撤单意图 + 供审计的挂单快照（撤单请求本身没有 direction/px/sz 的天然取值，§4.4）。"""
        return CancelOrderInput(inst_id=self.inst_id, cl_ord_id=order.cl_ord_id,
                                direction=order.side, td_mode=self.td_mode,
                                px=order.px, sz=order.sz)

    async def _prepare_resting_slot(self, ts: int, intent: str, side: str,
                                    limit_px: float | None) -> tuple[list[dict], bool]:
        """下新单前腾出 I-1 的唯一挂单额度：撤旧挂新（§2.5）。返回 `(events, 可否下新单)`。

        三态撤单的第一个红利：收到 `"filled"` 时**绝不**继续下新单——否则会在一笔已经成交的真实
        仓位之上再开一笔，敞口翻倍、预算失控，而自管 `projected` 还以为自己是 flat（R-2b）。
        """
        order = self.resting
        if order is None:
            return [], True
        if limit_px is not None and is_same_intent(order, intent=intent, side=side, px=limit_px):
            return [self._status_event(          # P2-3 幂等：同向同价不重挂，保住 maker 排队优先级
                f"同向同价挂单仍在排队（{limit_px}），本轮保留原单不撤不重挂")], False
        outcome, events = await self._cancel_resting("replace", ts)
        if outcome == "canceled":
            return events, True
        if outcome == "filled":
            return events + [self._status_event(
                "撤单时原挂单已成交，已采纳该成交；本轮不再下新单（避免在已成交仓位上重复开仓）")], False
        return events + [self._status_event(
            "原挂单撤销未确认，本轮不下新单（绝不双挂），待下轮核对挂单状态")], False

    async def cancel_resting(self, reason: str, ts: int) -> list[dict]:
        """撤回本会话当前挂着的未成交委托（Agent `cancel_order` 动作的落点）。

        无挂单时为**良性 no-op**（回 status 事件、不报错、**不改 `projected`**、不触碰任何持仓）。
        """
        if self.resting is None:
            return [self._status_event("当前没有未成交挂单，撤单请求无需执行（未改变任何持仓）")]
        _, events = await self._cancel_resting(reason, ts)
        return events

    async def _cancel_resting(self, reason: str, ts: int) -> tuple[str, list[dict]]:
        """撤单**三态**（`"canceled"|"filled"|"unknown"`，P0-2）——刻意不是 bool。

        真实的第三态是「撤单请求发出的同时，那笔挂单成交了」（OKX `51402`）。把它当成「良性幂等」
        丢弃 resting，会丢失真实持仓；在 cancel-replace 路径下更会双开。故调用方**一律**只在
        `"canceled"` 时才继续原计划。
        """
        order = self.resting
        if order is None:
            return "canceled", []
        try:
            result = await self._cancel_order(self._cancel_input(order))
        except OKXError as exc:      # 连通类失败：状态未知 → 保留挂单，下轮 _poll_resting 再判
            return "unknown", [self._error_event(ts, exc)]
        outcome = str(result.get("outcome") or "unknown")
        if outcome == "canceled":
            self.resting = None
            return "canceled", [self._status_event(f"已撤销未成交挂单（{reason}）")]
        if outcome == "filled":
            return "filled", await self._adopt_resting_fill(ts)
        return "unknown", [self._status_event(
            f"撤单未获确认（{result.get('code')}），保留挂单待下轮核对状态")]

    async def _adopt_resting_fill(self, ts: int) -> list[dict]:
        """撤单撞成交（`51402`）：回查 `get_order` 取权威 `avgPx`/`accFillSz` 并采纳。

        回查失败时**保留 resting 并不采纳**（绝不猜成交价）——下一轮 `_poll_resting` 会再采纳一次，
        代价只是延后一轮；而调用方已因 `"filled"` 放弃本轮新单，敞口不会翻倍。
        """
        order = self.resting
        if order is None:
            return []
        try:
            state = await self._get_order(order.cl_ord_id)
        except OKXError as exc:
            return [self._error_event(ts, exc)]
        return self._settle_resting_fill(order, state, ts)

    async def _poll_resting(self, ts: int) -> list[dict]:
        """向 OKX 核对挂单权威状态并收敛（成交 / 撤销 / 超时）。**必须在 reconcile 内最先执行**（O-1）。

        异常**自行隔离**：`get_order` 失败绝不外泄进 `reconcile()` 既有的 `except OKXError`——否则一次
        读单失败会被误归因为「持仓读取失败」，污染 `_reconcile_fail_streak` 并触发假降级。
        """
        order = self.resting
        if order is None:
            return []
        try:
            state = await self._get_order(order.cl_ord_id)
        except OKXError as exc:
            return [self._status_event(
                f"挂单状态本轮读取失败（[{exc.code}]），保留挂单待下轮核对")]
        return await self._advance_resting(order, state, ts)

    async def _advance_resting(self, order: RestingOrder, state: dict,
                               ts: int) -> list[dict]:
        """按归一生命周期态推进挂单（状态分派走 `resting.classify_state` 查找表，守复杂度红线）。"""
        phase = classify_state(state.get("state"))
        if phase == "filled":
            return self._settle_resting_fill(order, state, ts)
        if phase == "partial":
            return self._adopt_partial(order, state, ts)
        if phase == "canceled":
            self.resting = None
            return [self._status_event("挂单已撤销（我方或交易所侧），已收敛为无挂单")]
        if phase == "live" and is_expired(order, ts):
            _, events = await self._cancel_resting("expired", ts)
            return events
        return []       # live 未到期 / unknown：保持挂单，下轮再判（兜底方向恒为「不动」）

    def _settle_resting_fill(self, order: RestingOrder, state: dict, ts: int) -> list[dict]:
        """挂单**全部成交**：按 intent 分派采纳，清空 resting 并推 trade 事件。"""
        self.resting = None
        avg_px = float(state.get("avgPx") or order.px)
        acc = float(state.get("accFillSz") or order.sz)
        delta = fill_delta(order, acc)
        if delta <= 0:      # 已在此前的 partially_filled 轮全额采纳过，本轮只做收敛
            return [self._status_event("挂单已全部成交（成交量此前已采纳），已收敛为无挂单")]
        if order.intent == "open":
            return self._adopt_fill(order, avg_px, acc, ts)
        return self._adopt_close_fill(order, avg_px, ts)

    def _adopt_partial(self, order: RestingOrder, state: dict, ts: int) -> list[dict]:
        """部分成交：按**增量** `accFillSz - adopted_sz` 采纳，剩余继续挂到 TTL（R-19 防重复计入）。

        仅开仓路径做增量采纳。平仓路径**刻意不做**部分结算：`_settle_close` 是「先算 pnl 再复位
        entry」的全额结算，另写一份部分结算逻辑会立刻产生第二个盈亏口径真源——这正是 §2.3 明令
        禁止的。平仓挂单一律等全额成交或 TTL 撤单（残余偏差见 spec 文末〈实施过程发现的方案缺陷〉）。
        """
        acc = float(state.get("accFillSz") or 0.0)
        delta = fill_delta(order, acc)
        self.resting = adopt_fill_delta(order, acc)
        if delta <= 0 or order.intent != "open":
            return []
        avg_px = float(state.get("avgPx") or order.px)
        return self._adopt_fill(order, avg_px, acc, ts, delta_sz=delta)

    def _adopt_fill(self, order: RestingOrder, avg_px: float, total_sz: float, ts: int,
                    *, delta_sz: float | None = None) -> list[dict]:
        """采纳**开仓**挂单成交：projected 置位 + `entry_price` 取真实成交价 `avgPx`（非 `ref_px`）。

        这是 I-2a 的代价所偿：挂单期间 projected 恒为 flat 使幻影成交分支不成立，成交便只能由本方法
        （经 `_poll_resting`）侦测——它是成交侦测的**唯一**路径，没有第二道网（A-14/R-2）。

        置 `_fill_grace`（O-3）：本方法把 projected 从 flat 置为有仓，而 `reconcile` 紧接着就会用
        **可能尚未跟上的**持仓读数去判「自管有仓、交易所无仓」——详见 `_on_reconcile_ok` 的 O-3 注释。
        """
        self._fill_grace = True
        self.projected = order.side
        self.entry_price = avg_px
        self.entry_bar = self._cur_bar
        self.entry_qty_sz = total_sz
        self.entry_position_pct = self._position_pct_for_size(
            sz=total_sz, ct_val=self.ct_val, reference_price=avg_px,
            member_budget_usd=self.budget_usd, leverage=self.leverage)
        # OCO 触发价随单在下单时已按 limit_px 附于交易所侧（§2.7），此处如实回显同一基准。
        tp, sl = self._risk_trigger_px(order.side, order.px)
        explain = self._open_explain(order.side, order.reason, None, tp, sl)
        trade = self._make_trade(ts, order.side, avg_px, delta_sz if delta_sz is not None
                                 else total_sz, pnl=0.0, reason=order.reason or "signal_open",
                                 tp=tp, sl=sl, explain=explain, cl_ord_id=order.cl_ord_id,
                                 ord_id=order.ord_id, position_pct=self.entry_position_pct,
                                 ord_type="limit", expected_px=order.px)
        self.entry_fee_usd += float(trade["fee"])   # §3.3a：按本轮采纳增量计费（sz 已是 delta）
        self.trades.append(trade)
        return [{"type": "trade", "data": trade}]

    def _adopt_close_fill(self, order: RestingOrder, avg_px: float, ts: int) -> list[dict]:
        """采纳**平仓**挂单成交：复用既有 `_settle_close(ref_px=avgPx, …)`（P0-3/P1-1）。

        关键：`ref_px` 传**真实成交价 `avgPx`** 而非 `mark_px`，盈亏才是真的。复用 `_settle_close`
        而非另写，是刻意的——它已封装「先算 pnl/pnlPct 再复位 entry」这个**顺序敏感**的既有约束
        （`executor.py` v2 的 P1-2 修复过一次）。另起炉灶必然重蹈覆辙，且会产生第二个盈亏口径真源。
        """
        explain = self._close_explain("signal", order.reason, None, avg_px)
        result = {"clOrdId": order.cl_ord_id, "ordId": order.ord_id}
        return self._settle_close(avg_px, ts, order.reason or "signal_close", result,
                                  explain=explain, ord_type="limit", expected_px=order.px)

    def _external_close_suppressed(self) -> bool:
        """O-2 兜底：平仓挂单在飞时禁用 `external_close` 合成分支（§2.3）。

        O-1（`_poll_resting` 先于 `_on_reconcile_ok`）保正确——同一轮里成交总是先被 `_adopt_close_fill`
        正确采纳、projected 已归 flat，该分支自然不触发。本条保**安全**：即便 O-1 因异常 / 时序被绕过
        （如 `_poll_resting` 本轮读取失败），最坏也只是延后一轮采纳，绝不把我方主动的限价平仓误记成
        「外部干预平仓」并按 mark_px 捏造盈亏。两者都要。
        """
        return self.resting is not None and self.resting.intent == "close"

    def _settle_close(self, ref_px: float, ts: int, reason: str, result: dict, *,
                      explain: dict | None = None, ord_type: str = "market",
                      expected_px: float | None = None) -> list[dict]:
        pnl = self._close_pnl(ref_px, ord_type=ord_type)
        pnl_pct = self._close_return_pct(ref_px, ord_type=ord_type)   # 须在复位 entry 前算（P1-2）
        qty = self.entry_qty_sz
        position_pct = self.entry_position_pct
        self.realized_pnl_usd += pnl
        self.unrealized_usd = self.gross_unrealized_usd = 0.0
        self.projected = "flat"
        self.entry_qty_sz = 0.0
        self.entry_fee_usd = 0.0                   # §3.3b 复位点 1/3：主动平仓 / 限价平仓采纳
        self.entry_position_pct = None
        cl_ord_id, ord_id = self._order_ids(result)
        trade = self._make_trade(ts, "close", ref_px, qty, pnl=pnl, pnl_pct=pnl_pct,
                                 reason=reason, explain=explain, cl_ord_id=cl_ord_id,
                                 ord_id=ord_id, position_pct=position_pct,
                                 ord_type=ord_type, expected_px=expected_px)
        self.trades.append(trade)
        return [{"type": "trade", "data": trade}]

    def _settle_benign_close(self, ref_px: float, ts: int) -> list[dict]:
        """平仓收 LIVE_NO_POSITION（交易所侧 OCO 已先平）：合成只读外部平仓成交 + 良性归 flat（R8）。

        v2（P1-1）：在归 flat 前合成一条 kind="external_close" 成交，记录已发生的交易所侧平仓；
        **不调用任何下单 / 平仓接口**，best-effort + 异常隔离。
        """
        events = self._synthesize_external_close(ref_px, ts, source="oco")
        self.realized_pnl_usd += self._close_pnl(ref_px)
        self.unrealized_usd = self.gross_unrealized_usd = 0.0
        self.projected = "flat"
        self.entry_qty_sz = 0.0
        self.entry_fee_usd = 0.0                   # §3.3b 复位点 2/3：交易所侧 OCO 已先平
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

        O-1（ai-trade-limit-orders §2.3，P0-3）：`_poll_resting` 必须在此**最先**执行，先于
        `_read_positions` 与 `_on_reconcile_ok`。这样同一轮里挂单成交总是先被正确采纳（平仓成交后
        projected 已归 flat），`_on_reconcile_ok` 随后便不会把我方主动的限价平仓合成为幻影
        `external_close`。`reconcile()` 是全部 6 处调用点的**唯一收口**，在此处收口即全覆盖。
        `resting is None`（含 `live_auto` 全部场景）时 `_poll_resting` 返回 `[]`，行为逐字不变。
        """
        events = await self._poll_resting(ts)          # O-1：必须最先；异常已在内部自隔离
        await audit_fills(self.fee_ledger, fills_fn=self._read_fills,
                          now_wall=time.time(), settings=self.settings)
        try:
            positions = await self._read_positions()
        except OKXError as exc:
            return events + self._on_reconcile_failure(ts, exc)
        return events + self._on_reconcile_ok(mark_px, positions, ts)

    def _on_reconcile_ok(self, mark_px: float, positions: list[dict], ts: int) -> list[dict]:
        """持仓读数 → 校正自管立场（幻影成交分支见 O-2 / O-3）。

        O-3（ai-trade-limit-orders，Subtask #3 终审补）：`_adopt_fill` 刚把 projected 由 flat 置为
        有仓时，**本轮**的持仓读数可能尚未跟上（OKX 的 `/trade/order` 与 `/account/positions` 之间
        存在最终一致性窗口，而 O-1 令二者在同一轮里仅相隔一次网络往返）。此时下方分支会把一笔**刚
        采纳的真实持仓**合成为 `external_close` 幻影平仓、按 mark_px 捏造盈亏并归 flat——即 R-1 在
        开仓路径上的原样复发，且比市价路径危险得多（市价路径的两次调用相隔一整个 5s 轮询周期）。
        故给刚成交的那一轮一次宽限：本轮不判幻影，下一轮持仓读数若仍为空再判（延后一轮，代价与
        O-2 同）。宽限**恒被消费**（不受下方短路影响），绝不跨轮累积成永久豁免。
        """
        events = self._clear_degraded_if_recovered()
        grace = self._fill_grace
        self._fill_grace = False
        abs_pos = self._net_abs_pos(positions)
        self.gross_unrealized_usd = self._net_upl(positions)     # OKX upl：纯价差、不含任何费用
        self.unrealized_usd = self._net_unrealized(self.gross_unrealized_usd, mark_px)
        if (self.projected != "flat" and abs_pos <= 0 and not grace
                and not self._external_close_suppressed()):
            # v2（P1-1）：侦测到交易所侧 / 外部已平仓，合成只读 external_close 成交后再校正为 flat。
            events += self._synthesize_external_close(mark_px, ts, source="external")
            self.realized_pnl_usd += self._close_pnl(mark_px)
            self.unrealized_usd = self.gross_unrealized_usd = 0.0
            self.projected = "flat"
            self.entry_qty_sz = 0.0
            self.entry_fee_usd = 0.0               # §3.3b 复位点 3/3：外部干预平仓（漏掉即静默重扣）
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

        §2.8：**先撤挂单，且撤单必须先于早退判定**。`close_position(auto_cxl=True)` 确会撤掉该合约
        全部挂单，但它仅在**有持仓**时被调用——「无持仓 + 有挂单」（挂单从未成交）恰恰命中下方
        `projected == "flat"` 早退，会把挂单遗留在交易所（孤儿单，R-3）。更要紧的是三态撤单的第二个
        红利：停止瞬间挂单恰好成交（`51402`）时，采纳成交会把 projected 置为非 flat，早退**自然不再
        命中**，第 4 步照常把这笔刚成交的仓位平掉——否则会「报告干净停止，却在交易所留一笔无人看管
        的真实持仓」，与本函数承诺的「绝不假报干净停止」直接冲突。
        """
        events: list[dict] = []
        if self.resting is not None:
            _, events = await self._cancel_resting("session_stop", ts)   # 采纳成交会改写 projected
        if self.projected == "flat":
            return self._with_cancel_events({"ok": True, "closedSz": 0}, events)
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
                    return self._with_cancel_events(
                        {"ok": True, "closedSz": 0, "note": "already_flat", "trade": trade},
                        events)
                last_exc = exc
                if attempt < self.settings.live_auto_flatten_max_retries:
                    await asyncio.sleep(_FLATTEN_RETRY_INTERVAL_S)
                continue
            return self._with_cancel_events(self._settle_flatten_ok(ref_px, ts, result), events)
        return self._with_cancel_events(
            {"ok": False, "code": last_exc.code, "message": last_exc.message,
             "residual": {"side": self.projected, "size": self.entry_qty_sz}}, events)

    @staticmethod
    def _with_cancel_events(result: dict, events: list[dict]) -> dict:
        """把 flatten 期间的撤单 / 采纳成交事件挂上结果（**空则不加键**，`live_auto` 结果逐字不变）。"""
        if events:
            result["events"] = events
        return result

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

    def _close_pnl(self, exit_px: float, *, ord_type: str = "market") -> float:
        """平仓盈亏；账本未装配恒走毛口径、位相同（§3.3/Z-2）。**纯读**：绝不在此消费 entry_fee_usd。"""
        gross = self._gross_close_pnl(exit_px)
        if self.fee_ledger is None:
            return gross
        return self.fee_ledger.net_close_pnl(
            gross, entry_fee_usd=self.entry_fee_usd, exit_px=exit_px,
            sz=self.entry_qty_sz, ord_type=ord_type)

    def _gross_close_pnl(self, exit_px: float) -> float:
        qty_coin = self.entry_qty_sz * self.ct_val
        if self.projected == "long":
            return qty_coin * (exit_px - self.entry_price)
        if self.projected == "short":
            return qty_coin * (self.entry_price - exit_px)
        return 0.0

    def _close_return_pct(self, exit_px: float, *, ord_type: str = "market") -> float | None:
        """平仓收益率：已实现盈亏 / 开仓占用保证金（等价于方向化价格变动% × 杠杆）。

        v2（P1-2）：先校验后相除——杠杆或名义价值非正（异常断面）直接返回 None，杜绝在
        未被 try/except 包裹的 _settle_close 调用栈抛 ZeroDivisionError；缺收益率即省略 pnlPct 键。
        """
        notional = self.entry_qty_sz * self.ct_val * self.entry_price
        if self.leverage <= 0 or notional <= 0:
            return None
        return self._close_pnl(exit_px, ord_type=ord_type) / (notional / self.leverage)

    def _unrealized_at(self, mark_px: float) -> float:
        """本地派生浮盈（**净额口径**，B-2）：账本未装配 / 空仓恒返回毛额，live_auto 逐字节回退。"""
        if self.projected == "long":
            gross = self.entry_qty_sz * self.ct_val * (mark_px - self.entry_price)
        elif self.projected == "short":
            gross = self.entry_qty_sz * self.ct_val * (self.entry_price - mark_px)
        else:
            return 0.0
        return self._net_unrealized(gross, mark_px)

    def _net_unrealized(self, gross: float, mark_px: float) -> float:
        """浮盈净额化的**唯一**入口；账本未装配（live_auto 全部路径）/ 空仓 ⇒ 恒返回 gross（位相同）。"""
        if self.fee_ledger is None or self.projected == "flat":
            return gross
        return self.fee_ledger.net_unrealized(gross, entry_fee_usd=self.entry_fee_usd,
                                              mark_px=mark_px, sz=self.entry_qty_sz)

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

    def _align_bar_ts(self, wall: int) -> int:
        """把墙钟秒吸附到**所属那根真实 K 线**的开盘秒；相位由交易所给的锚点决定，不猜时区。

        **绝不能写成 `(wall // bar_seconds) * bar_seconds`**：那隐含「交易所 K 线以 UTC 整点相位
        开盘」，而 OKX 的 `12H` / `1D` 是港时（UTC+8）开盘（`_band_structure.py:154` 已成文），
        UTC 整除既修不好 1D，还会把 K 线驱动轮自己的 ts 从 16:00 系列改写成 00:00。

        两条有意的降级（不静默）：`bar_seconds<=0` 即 `live_auto` 未装配本轴；`bar_anchor_ts<=0`
        即会话暖机期尚无确认根——此时也确实还没有任何一根 K 线可供落点，逐字回退墙钟。
        """
        secs, anchor = self.bar_seconds, self.bar_anchor_ts
        if secs <= 0 or anchor <= 0:
            return wall
        return anchor + ((wall - anchor) // secs) * secs   # 负差走 floor，落到锚点前一根的真实相位

    def _make_trade(self, ts: int, side: str, price: float, sz: float, *, pnl: float,
                    reason: str, pnl_pct: float | None = None, tp: float | None = None,
                    sl: float | None = None, explain: dict | None = None,
                    cl_ord_id: str | None = None, ord_id: str | None = None,
                    seeded: bool = False, position_pct: float | None = None,
                    ord_type: str = "market", expected_px: float | None = None) -> dict:
        position_after = sz if side == "long" else (-sz if side == "short" else 0.0)
        # 轴 D 的**唯一写点**：`ts` 吸附到所属那根 K 线的开盘秒（图表标记 / 悬停索引读它）。
        wall = int(ts)
        trade = {"ts": self._align_bar_ts(wall),
                 "side": side, "price": float(price), "qty": float(sz),
                 "fee": 0.0, "pnl": float(pnl), "positionAfter": position_after,
                 "reason": reason, "stopLossPct": self.risk.stop_loss_pct,
                 "takeProfitPct": self.risk.take_profit_pct,
                 "maxHoldBars": self.risk.max_hold_bars,
                 "stopLossPrice": sl, "takeProfitPrice": tp, "seeded": seeded,
                 "explain": explain}
        # `wallTs`（真实墙钟秒，排序 / 悬停展示 / 导出读它，单调不减）沿用本方法既有的「缺失即省略
        # 键」风格：**只有装配了对齐的链路（= ai_auto）才带这个键**。这不是可选的洁癖——`live_auto`
        # 的成交 dict 形状被 `test_ai_trade_switches_never_leak_into_non_ai_executor` 的键集冻结门禁
        # 守着，无条件多一个键就是一次非 AI 链路的 wire 变更。读侧一律 `wallTs ?? ts`，故省略无损。
        if self.bar_seconds > 0:
            trade["wallTs"] = wall
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
        trade.update(trade_fee_fields(self.fee_ledger, cl_ord_id=cl_ord_id, side=side, sz=sz,
                                      px=price, ord_type=ord_type, expected_px=expected_px))
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
                                     position_pct=self.entry_position_pct, expected_px=ref_px)
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
