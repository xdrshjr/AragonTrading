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
import logging
import math
import time
from copy import deepcopy
from dataclasses import dataclass

from ..config import Settings
from ..db.session import get_sessionmaker
from ..engine.simulator import BrokerContext, RiskParams
from ..okx.accounts import AccountCredentials
from ..okx.order_builder import (
    CancelOrderInput,
    ClosePositionInput,
    OrderInput,
    SubmissionTrace,
    snap_size,
)
from ..okx.rest_client import OKXError
from ..okx.trading import (
    cancel_live_order,
    close_live_position,
    fetch_live_fills,
    fetch_live_order,
    fetch_live_positions_at,
    place_live_order,
    new_client_order_id,
)
from ..wire_caps import TRADE_LEDGER_MAX
from .fee_ledger import FeeLedger, FeeOverride, audit_fills, trade_fee_fields
from .execution_receipt import receipt_from_trace
from ..ai_auto.orphan import (
    OrphanFacts,
    classify_orphan,
    orphan_message,
    orphan_wire,
)
from .settlement import (
    SettlementFact,
    SettlementQuery,
    validate_settlement,
    resolve_settlement_increment,
)
from .settlement_identity import (
    IdentityConfig, SettlementIdentityTracker, finite_number,
)
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
# 台账封顶的迟滞量（D5）：超过 `TRADE_LEDGER_MAX + slack` 才一次性裁到上限，把 `del` 的 O(n)
# 成本摊到 slack 笔成交上；逐笔裁会让每一笔成交都付一次整列搬移。
_LEDGER_TRIM_SLACK = 500


logger = logging.getLogger(__name__)


def _num_or_zero(value) -> float:
    """转有限浮点；非数值 / NaN / inf ⇒ `0.0`（持仓读数里 NaN 比 0 危险得多）。"""
    try:
        out = float(value)
    except (TypeError, ValueError):
        return 0.0
    return out if math.isfinite(out) else 0.0


# 已认领的结算 `posId` 上限（FIFO 淘汰）。它只是一层**廉价的额外保险**，不是正确性的承重墙：
# 一笔往返只会进入 `settling` 一次（入口条件是 `projected != "flat"`，落账即归 flat 且
# `entry_qty_sz = 0`），而 OKX 的 `posId` 对一次持仓生命周期唯一。故「同一 posId 被认领两次」需要
# 它在 256 笔结算之后**再次出现在 positions-history 里且本地又恰好处于 settling**——`limit=20` 的
# 窄窗口取数下不可能。淘汰后最坏退化为按 `local_mark` 落账，**不会造成错账**（不变式 I-3）。


@dataclass
class SettlementPending:
    """一笔「交易所侧已平仓、正在取回结算价」的未决中间态（至多一笔，随会话回收）。

    刻意**可变**（非 frozen）：`last_poll_wall` 每次取数后就地推进，而它是本对象唯一会变的字段。
    """

    detect_ts: int          # 侦测到那一秒（也是 `explain.meta.settlementWaitedS` 的起点）
    mark_px: float          # 侦测那一刻的本地标记价（超时回落就用它，逐字等于今天的行为）
    deadline: float         # 墙钟死线（R-1 的第一重保险）
    entry_ts: int           # 本地开仓腿 ts（匹配判据 5）
    direction: str          # 本地立场（匹配判据 2，fail-open）
    qty: float              # 侦测那一刻的自管张数（只用于日志与自检）
    frozen_unrealized: float        # 冻结的净额浮盈（不变式 I-2）
    frozen_gross_unrealized: float  # 冻结的毛额浮盈
    last_poll_wall: float = 0.0
    context: object = None
    increment: object = None
    validation: dict | None = None


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
                 settlement_fn=None,
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
        # 已执行真单→Trade（驱动图表 markers）。**容器类型必须是 `list`，不得换成 `deque`**：
        # `wire_caps.tail_trades` 的实现是 `trades[-cap:]`，而 deque 不支持切片
        # （`TypeError: sequence index must be integer, not 'slice'`），两条会话的快照下发主路径
        # （`live_auto/session.py` 与 `ai_auto/session.py`）会每次都炸。封顶改在 `_record_trade` 里按块裁。
        self.trades: list[dict] = []
        # 显式计数器（trust-boundary-and-signal-integrity D5）。**它们是封顶的前置条件**：
        # 这份台账同时承担三个角色——明细、计数器、换手闸判据。先把后两个从「数列表长度」里
        # 解耦出来，裁剪才不会静默改变交易行为（裁 `trades` 等于静默放松换手纪律阈值）。
        # 刻意**只有两个**：已实现盈亏的真值是上面的 `realized_pnl_usd`（平仓回执累加，续跑时由
        # continuity 一并回灌），再按成交腿累加一份「总额」就是同一个数落两份——而这里漂移的是
        # 真实资金的盈亏显示（`ai_auto/resume.rebuild_continuity` 的不变式原文）。
        self.trade_count = 0               # 会话累计成交笔数（单调递增，不受封顶影响）
        self.open_count = 0                # 其中开仓笔数（side in long/short）——换手闸判据
        self._reconcile_fail_streak = 0    # H2：连续 reconcile 读取失败计数（成功即清零）
        self.degraded = False              # H2：盯市降级标志（连续失败达阈→True，恢复→False）
        # 当前挂着的那一笔限价委托（不变式 I-1：单会话至多一笔）。恒为 None ⇒ 市价路径 ⇒ live_auto
        # 的每一处判定都短路，行为与本特性合入前逐字相同。
        self.resting: RestingOrder | None = None
        # E-7（loss-and-liveness-hardening 轴 E）：**开仓**限价委托未成交即被撤销的单调累计计数，
        # 按委托腿自己的方向分格。撤单常在数轮之后才发生，会话侧的轮级方向标志那时早已清零，故
        # 方向必须在这里定格（`open_funnel.note_expired` 的成文理由）。live_auto（市价路径）恒为空。
        self.open_expired_by_side: dict[str, int] = {}
        # 最近一次 `apply` 中「平仓未下发」的原因码（exit-priority §5.5）；`None` = 未发生。
        # 会话侧据它把 F-4 那条**此前完全静默**的路径写进本轮 `degrade`。
        self.last_close_block: str | None = None
        # 刚采纳完开仓成交的一轮宽限（O-3，见 `_on_reconcile_ok`）。恒为 False ⇒ live_auto 零影响。
        self._fill_grace = False
        # ---- 轴 A：成交真值化（fill-truth-and-effective-controls）----
        # 第八条可注入缝。**默认 None ⇒ `settling` 状态机整条不存在**，`_on_reconcile_ok` 走今天的
        # 立即合成 ⇒ `live_auto` 与全部既有单测逐字节回退（`G-A10`）。装配点唯一：
        # `ai_auto/manager._build_executor` 的函数体内（不新增形参，用已在作用域的 creds/inst_id 闭包）。
        self._settlement_fn = settlement_fn
        self.settlement_identity = None
        self.settlement_instrument = {}
        self.last_settlement_validation = None
        self.persist_settlement_identity = None
        self.restore_claimed_settlement = None
        self._settling: SettlementPending | None = None
        # 轮级槽：`SettlementFact | None` + 上下文，**消费即清空**。逐字镜像
        # `session._pending_horizon_meta` 的既有范式——八个 `settlement*` 键的装配点是
        # `session._inject_explain_meta`（在 `exitSource` 之后），**不是** `live_auto/explain.py`
        # （那是 live_auto 与 ai_auto 的共用模块，写在那里会同时改掉非 AI 链路的成交形状，
        # 并造出第二个 explain.meta 装配现场——评审 RV-P0-6）。
        self._pending_settlement_meta: dict | None = None
        self.settlement_exchange_count = 0     # 拿到交易所结算价的外部平仓笔数（`AC-1` 的分子）
        self.settlement_local_mark_count = 0   # 状态机跑过了但没拿到的笔数（`AC-1` 分母的另一半）
        # ---- 轴 B：孤儿持仓（只写状态与事件，零交易所写请求，不变式 I-4）----
        self._orphan_streak = 0
        self._orphan_since_ts = 0
        self.orphan: dict | None = None
        self.orphan_alerted = False
        # ---- 轴 E 观测半：挂单去留三计数（`aiStats.sizing` 的读点）----
        # `restingKept` 与既有的 `restingSkips` **不是同一件事**，绝不复用（`G-E5`）：后者是
        # 「因为有挂单而跳过本轮开仓」，前者是「本轮想挂的与已挂的逐字同向同价，于是保留原单
        # 不撤不重挂」——今天这条幂等短路一次都没有被计数过。
        self.resting_kept = 0
        self.resting_repriced = 0
        self.resting_cancelled = 0
        self.resting_expired = 0        # 其中因 TTL 到期被撤的笔数（轴 E 双时钟修复的效果读数）

        self._place_order = place_order_fn or self._default_place_order
        self._close_position = close_position_fn or self._default_close_position
        # 轴 C-2（silent-degradation-repair）：**两个属性，由同一个 `positions_fn` 派生两条规则**。
        # 刻意不写成「两个方法」——`self._read_positions = ...` 这一行是属性赋值，同名方法会被它
        # 遮掉，而遮掉的方向取决于赋值顺序，是一个会随重构静默翻转的行为。
        #
        # `positions_fn` 一旦给出，**两条路径都用它**：30+ 个用例的替身否则只能拦住一半调用，
        # 另一半会真的出网（门禁 G-C13 守的正是这件事）。
        self._read_positions = positions_fn or self._default_read_positions
        self._read_positions_fresh = positions_fn or self._default_read_positions_fresh
        # 轴 C-4 防御纵深：本执行器最近一次**自己发起的私有写**的墙钟。比它更早的持仓快照
        # 不得用来裁决幻影平仓（构造性保证，不依赖 C-3 的 `invalidate` 被正确调用）。
        # 进程内、重启即 0 ⇒ 冷启后第一轮不设防，与本轮之前逐字一致。
        self._last_private_write_wall = 0.0
        # 本轮持仓读数**真实取得**的墙钟。`reconcile` 每轮先把它置成「此刻」（这样注入替身与
        # `live_auto` 的行为逐字节不变，恒 fail-open），两个默认读法再各自覆盖成真值。
        self._positions_taken_wall = 0.0
        # P1-2：挂单生命周期依赖的两个新缝（镜像上方三缝的既有范式）——没有它们，限价整组单测无法脱网。
        self._cancel_order = cancel_order_fn or self._default_cancel_order
        self._get_order = get_order_fn or self._default_get_order
        self._read_fills = fills_fn or self._default_read_fills
        self.open_attempts = None
        self.last_open_receipt = None
        self._open_attempt_id = ""

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
        """盯市 `reconcile` 的读法：**允许命中进程内共享快照**（轴 C-2）。

        语义是「校正」，本来就是 `live_auto_poll_interval_s` 秒一次；N 个成员共享一份不改变任何
        单个成员看到的内容（`_net_abs_pos` 按 `instId` 过滤，成员之间本就互不消费对方的持仓）。
        `ai_trade_positions_snapshot_ttl_s = 0` ⇒ 完全旁路 ⇒ 逐字节回到今天。
        """
        rows, taken_wall = await fetch_live_positions_at(
            self.creds, use_cache=True,
            ttl_s=float(self.settings.ai_trade_positions_snapshot_ttl_s))
        self._positions_taken_wall = taken_wall
        return rows

    async def _default_read_positions_fresh(self) -> list[dict]:
        """**强制穿透**的读法（轴 C-2 的两个有交易后果的消费点）。

        `_confirm_net_cleared` 是一个**轮询等待**：读缓存会让每次重试拿到同一个答案，等于把
        「等净仓归零」变成一个恒假的死循环。`session._read_exchange_position`（暂停时读持仓
        真值）同理——它的既有 docstring 已经写死了理由：「我关机时到底还有多少仓」这个数不该
        来自本地投影，同样也不该来自一份 4 秒前的缓存。
        """
        rows, taken_wall = await fetch_live_positions_at(self.creds, force=True)
        self._positions_taken_wall = taken_wall
        return rows

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
    def session_pnl_usd(self) -> float | None:
        """会话累计盈亏 = 已实现 + 当前持仓未实现（reconcile 自 OKX upl）。"""
        if self.realized_pnl_usd is None:
            return None
        return self.realized_pnl_usd + self.unrealized_usd

    def context(self, bar_index: int, mark_px: float) -> BrokerContext:
        bars = (bar_index - self.entry_bar) if self.projected != "flat" else 0
        equity = (None if self.realized_pnl_usd is None else
                  self.budget_usd + self.realized_pnl_usd + self._unrealized_at(mark_px))
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
        # exit-priority §5.5：本次 apply 的「平仓未下发」原因码（每次调用先清，绝不跨轮残留）。
        self.last_close_block = None
        self.last_open_receipt = None
        self._open_attempt_id = new_client_order_id()
        events: list[dict] = []
        events += await self._maybe_time_exit(ref_px, ts)
        actions = self._project_signals(signals)
        if not any(action[0] == "open" for action in actions):
            if any(getattr(signal, "side", None) in ("long", "short")
                   for signal in signals):
                self._block_open("same_side_position")
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
    def _block_open(self, reason: str) -> None:
        self.last_open_receipt = receipt_from_trace(
            self._open_attempt_id, self.inst_id, None,
            SubmissionTrace(), reason)

    async def _submit_open(self, order: OrderInput) -> dict | None:
        """真实发送边界：AI 先持久化，finally 保留不可降级的 ack。"""
        manager = self.open_attempts
        if manager is not None:
            try:
                prepared = await manager.prepare(order)
            except asyncio.CancelledError:
                self._block_open("canceled_before_send")
                raise
            if not prepared:
                self._block_open(manager.block_reason or "attempt_persist_failed")
                return None
            self._open_attempt_id = manager.pending.attempt_id
        trace = order.submission_trace or SubmissionTrace()
        reason = "pre_submit_error"
        try:
            result = await self._place_order(order)
            # 注入适配器可直接返回确认回执；异常仍以旁路 trace 为准。
            if result.get("ordId") and trace.ack == "none":
                trace.phase, trace.ack = "sent", "accepted"
                trace.ord_id = str(result["ordId"])
            elif trace.ack == "none":
                trace.phase = "sent"
            self._note_private_write()
            return result if trace.ack == "accepted" else None
        except asyncio.CancelledError:
            reason = "canceled_before_send"
            raise
        finally:
            self.last_open_receipt = receipt_from_trace(
                self._open_attempt_id, self.inst_id,
                order.client_order_id, trace, reason)
            if manager is not None:
                await manager.observe(trace, reason=reason)

    async def _do_open(self, side: str, strength: float, ref_px: float, bar_index: int,
                       ts: int, reason: str, *, require_clear: bool, signal=None,
                       order_plan=None) -> list[dict]:
        if self._settling is not None:
            self._block_open("position_not_cleared")
            return [self._status_event("前一持仓结算尚未核实，本轮不新增仓位")]
        if self.open_attempts is not None and self.open_attempts.has_pending:
            self._block_open("acceptance_unknown")
            return []
        sz = self._compute_sz(strength, ref_px)
        if sz <= 0 or sz < self.min_sz:
            # [P0-4]：这条 status 文案在 AI-trade 前端**没有渲染落点**，装配仓位层后它又会从
            # 「预检盲区」变成常态（真实下单量是 f×budget，f 常落在 0.2~0.4）。计数使纪律条能说出
            # 「本会话已有 N 次因不足最小下单量跳过开仓」——否则现象与「AI 一直不开单」完全一致。
            self.dust_skips += 1
            self._block_open("below_min_size")
            return [self._status_event(
                f"预算 / 杠杆过小，单笔不足最小下单量（{side}），已跳过该信号")]
        limit_px = _plan_limit_px(order_plan)
        # F-4 的作用域边界：**开仓路径逐字不变**（双开风险真实存在，`close_position` 的 autoCxl
        # 论证只对平仓成立）。此处只解包第三元、一行逻辑不改（G-6 冻结）。
        pre, slot_free, outcome = await self._prepare_resting_slot(
            ts, "open", side, limit_px)
        if not slot_free:
            self._block_open({
                "skipped": "resting_kept", "filled": "resting_filled",
                "unknown": "resting_cancel_unknown",
            }.get(outcome, "resting_slot_busy"))
            return pre
        if require_clear and not await self._confirm_net_cleared():
            self._block_open("position_not_cleared")
            return pre + [self._status_event(
                "反手平仓尚未在交易所侧清算，本根跳过开仓，待下一根重试")]
        # §2.7：限价单的**预期成交价是 limit_px 而非 ref_px**——随单 OCO 若仍以 ref_px 为基准，
        # 止损距离会系统性偏移（挂单越远偏得越多）。这是限价特有的正确性要求，非可选优化。
        tp, sl = self._risk_trigger_px(side, ref_px if limit_px is None else limit_px)
        order = OrderInput(inst_id=self.inst_id, direction=side,
                           ord_type="market" if limit_px is None else "limit",
                           td_mode=self.td_mode, px=limit_px, sz=sz, lever=self.leverage,
                           tp_trigger_px=tp, sl_trigger_px=sl,
                           client_order_id=(new_client_order_id()
                                            if self.open_attempts else None),
                           submission_trace=(SubmissionTrace()
                                             if self.open_attempts else None))
        try:
            result = await self._submit_open(order)
        except OKXError as exc:
            return pre + [self._error_event(ts, exc)]
        if result is None:
            return pre
        if self.open_attempts is not None:
            return pre + await self.open_attempts.resolve_pending(self, ts)
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
        if self.entry_fee_usd is not None:
            self.entry_fee_usd += float(trade["fee"])
        self._record_trade(trade)
        return pre + [{"type": "trade", "data": trade}]

    async def _do_close(self, ref_px: float, ts: int, reason: str, *, signal=None,
                        close_kind: str = "signal", order_plan=None) -> list[dict]:
        if self.projected == "flat":
            return []
        if self._settling_blocks_close():
            # 不变式 I-2b（评审 RV-P0-3）：仓位在交易所侧**已经不在了**，此刻再发一张平仓单只会
            # 换回一个 `LIVE_NO_POSITION`，而那条良性分支会按 `mark_px` 抢先落一次账 ⇒ 与随后
            # `_finish_settling` 的落账**双记已实现盈亏**。**不发任何交易所写请求**，本轮止步。
            self.last_close_block = "settlement_pending"
            return [self._status_event(
                "该持仓已在交易所侧平掉，正在取回真实结算价：本轮不再发出平仓单"
                "（结算落账后会自动归 flat，代价只是延后一轮）")]
        limit_px = _plan_limit_px(order_plan)
        pre, slot_free, outcome = await self._prepare_resting_slot(
            ts, "close", self.projected, limit_px)
        if not slot_free:
            # F-4（P0）：**市价平仓**在「撤单未获确认」（unknown）时继续下发。安全前提已在仓库里：
            # `close_position` 带 `autoCxl=True`，由**交易所侧**撤掉该合约全部未成交挂单
            # （okx/trade_client.py），且重复平仓走下方 `LIVE_NO_POSITION` 良性分支。
            # `filled` 仍必须止步：那是真的已经平掉了。限价平仓与**全部开仓路径**逐字不变。
            if limit_px is not None or outcome != "unknown":
                # F-4 的另一半：这条路径此前**连降级码都没有**——轨迹上会显示「决策工具 · 平仓」
                # 而没有任何成交、没有任何降级码。把它变成一个可读的码，会话侧据它写 `degrade`。
                self.last_close_block = f"exit_order_slot_busy({outcome})"
                return pre
            pre = pre + [self._status_event(
                "原挂单撤销未获确认，但本轮是市价离场：仍按市价平仓"
                "（交易所侧会同时撤掉该合约未成交挂单）")]
        close = ClosePositionInput(
            inst_id=self.inst_id, pos_side=self._pos_side(), pos=self._signed_pos(),
            mgn_mode=self.td_mode, ord_type="market" if limit_px is None else "limit",
            px=limit_px, sz=None if limit_px is None else self.entry_qty_sz)
        # 解释须在 _settle_close 复位 projected / entry 前据当前持仓断面合成（信号平仓优先用因子解释）。
        explain = self._close_explain(close_kind, reason, signal, ref_px)
        try:
            result = await self._close_position(close)
            self._note_private_write()             # 轴 C-4：成功的写才置位
        except OKXError as exc:
            if exc.code == "LIVE_NO_POSITION":
                # F-4 放行分支的第二个后果（【R:P1-1】）：挂单还在飞 ⇒ 交易所侧已无仓这件事**多半
                # 就是我方那张限价平仓单成交造成的**，此刻本地无从分辨它与「交易所 OCO 先平了」。
                # 故本轮**不就地结算**（既不合成 `external_close` 错归因，也不按 `ref_px` 捏一个
                # 盈亏），把收敛交给下一轮 `_poll_resting`：读到 `filled` 走 `_adopt_close_fill`
                # （真实 `avgPx` / 真实张数 / 归 `agent`），读到 `canceled` 则由 `_on_reconcile_ok`
                # 合成 `external_close(source="external")`。两条支路都比就地猜一个来源诚实，代价
                # 只是延后一轮——`_external_close_suppressed()` 在 `reconcile` 侧立的正是这条规矩。
                if self._external_close_suppressed():
                    return pre + [self._status_event(
                        "交易所侧已无该持仓，而本会话的限价平仓单状态尚未核对："
                        "本轮不就地结算，下一轮核对挂单后按真实成交价收敛")]
                return pre + self._settle_benign_close(ref_px, ts)
            return pre + [self._error_event(ts, exc)]
        if limit_px is not None:      # I-2b：平仓挂单期间 projected **保持原方向**（真仓确实还在）
            # sz 取服务层回执的 closedSz（已按 lotSz 取整且 ≤ 实时 |pos|），而非本地 entry_qty_sz。
            resting_sz = float(result.get("closedSz") or self.entry_qty_sz)
            return pre + self._enter_resting(result, ts, intent="close", side=self.projected,
                                             px=limit_px, sz=resting_sz, strength=0.0,
                                             reason=reason)
        # 【R:P1-1】市价平仓成功后显式清挂单：交易所侧的 `autoCxl=True` 已经撤掉了它，本地留着只会
        # 让下一轮 `_poll_resting` 去查一张不存在的单，并在读到 `filled` 时二次结算出一条幽灵成交。
        self.resting = None
        return pre + self._settle_close(ref_px, ts, reason, result, explain=explain)

    # ---------- 挂单生命周期（ai-trade-limit-orders §2.3/§2.5；resting is None 时全部短路）----------
    def _enter_resting(self, result: dict, ts: int, *, intent: str, side: str, px: float,
                       sz: float, strength: float, reason: str) -> list[dict]:
        """下单回执 → `self.resting`；**绝不**乐观置位 projected（I-2a/I-2b，R-1 的正面防线）。"""
        cl_ord_id, ord_id = self._order_ids(result)
        # ★ 轴 E（fill-truth-and-effective-controls，实施期 E-0 前置调查的产物）：TTL 的起点必须与
        # **判定它的那个时钟**同源。`ts` 在确认根唤醒里是 `int(row["ts"])` 即 **K 线开盘时间戳**
        # （`session.py` 的 `_on_confirmed_candle`），而在定时 / 情报 / 信号翻转三种唤醒里又是
        # `int(time.time())` 墙钟——同一个形参承载两个时钟。而 `is_expired` 恒与盯市循环的墙钟比
        # （`_monitor_tick` 的 `now_ts = int(time.time())`）。15m 根 + TTL 300s 时
        # `expire_ts = 开盘 + 300` 而确认轮发生在开盘 + 900 ⇒ **挂单出生即过期**，下一个盯市 tick
        # （2~5s）立刻把它撤掉。生产日志逐行印证：06:00:53.805 下单 → 06:00:55.284 撤单。
        # 连带后果有三个：①`resting_payload` 喂给 LLM 的 `expiresInS` 恒为 0；②下方那条状态文案
        # 对用户说「300s 未成交则自动撤销」是一句假话；③挂单从来没有机会成交（ETH 单标的
        # `submitted → filled` 只有 15.4%）。总闸关 ⇒ 逐字回退今天的双时钟行为。
        stamp = (int(time.time())
                 if getattr(self.settings, "ai_trade_resting_ttl_wall_clock", False)
                 else int(ts))
        self.resting = build_resting(
            cl_ord_id=cl_ord_id or "", ord_id=ord_id or "", intent=intent, side=side,
            px=px, sz=sz, strength=strength, reason=reason, now_ts=stamp,
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
                                    limit_px: float | None
                                    ) -> tuple[list[dict], bool, str]:
        """下新单前腾出 I-1 的唯一挂单额度：撤旧挂新（§2.5）。

        三态撤单的第一个红利：收到 `"filled"` 时**绝不**继续下新单——否则会在一笔已经成交的真实
        仓位之上再开一笔，敞口翻倍、预算失控，而自管 `projected` 还以为自己是 flat（R-2b）。

        Returns:
            `(events, 可否下新单, outcome)`——第三元为撤单结果 `"canceled"|"filled"|"unknown"`，
            另有 `"skipped"`（无挂单 / 同向同价幂等，两条提前返回）。**调用方只允许据
            `outcome == "unknown"` 放行市价平仓**（F-4，见 `_do_close`）；`"skipped"` 天然止步。
        """
        order = self.resting
        if order is None:
            return [], True, "skipped"
        if limit_px is not None and is_same_intent(order, intent=intent, side=side, px=limit_px):
            # 轴 E 观测半：这条幂等短路今天**一次都没有被计数过**，于是「本轮保留了原单」在任何
            # 读数上都不存在。它与既有的 `restingSkips`（因为有挂单而跳过本轮开仓）不是同一件事，
            # 故另开一个计数器而不是复用（`G-E5`：同一轮里两者至多有一个 +1）。
            self.resting_kept += 1
            return [self._status_event(          # P2-3 幂等：同向同价不重挂，保住 maker 排队优先级
                f"同向同价挂单仍在排队（{limit_px}），本轮保留原单不撤不重挂")], False, "skipped"
        outcome, events = await self._cancel_resting("replace", ts)
        if outcome == "canceled":
            return events, True, outcome
        if outcome == "filled":
            if intent == "close" and order.intent == "open":
                return events, True, outcome
            return events + [self._status_event(
                "撤单时原挂单已成交，已采纳该成交；本轮不再下新单（避免在已成交仓位上重复开仓）")], False, outcome
        return events + [self._status_event(
            "原挂单撤销未确认，本轮不下新单（绝不双挂），待下轮核对挂单状态")], False, outcome

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
            self._note_private_write()             # 轴 C-4：撤单也可能改变持仓（三态之「已成交」）
        except OKXError as exc:      # 连通类失败：状态未知 → 保留挂单，下轮 _poll_resting 再判
            return "unknown", [self._error_event(ts, exc)]
        outcome = str(result.get("outcome") or "unknown")
        if outcome == "canceled":
            if self.open_attempts is not None and order.intent == "open":
                return await self._confirm_open_canceled(order, reason, ts)
            self._note_open_expired(order)          # E-7：委托发出了、没成交、没了
            self._note_resting_cancel(reason)       # 轴 E 观测半：撤单原因分格
            self.resting = None
            return "canceled", [self._status_event(f"已撤销未成交挂单（{reason}）")]
        if outcome == "filled":
            return "filled", await self._adopt_resting_fill(ts)
        return "unknown", [self._status_event(
            f"撤单未获确认（{result.get('code')}），保留挂单待下轮核对状态")]

    async def _confirm_open_canceled(self, order: RestingOrder,
                                     reason: str, ts: int) -> tuple:
        """撤单只保证取消剩余量，查清累计成交才能腾出开仓槽。"""
        try:
            state = await self._get_order(order.cl_ord_id)
        except Exception:
            return "unknown", [self._status_event("撤单后的成交量仍待核实")]
        if not self.open_attempts.matches_resting(order, state):
            return "unknown", [self._status_event("撤单后的订单身份仍待核实")]
        if classify_state(state.get("state")) not in {"canceled", "filled"}:
            return "unknown", []
        accumulated = float(state["accFillSz"])
        price = _num_or_zero(state.get("avgPx"))
        if accumulated > order.adopted_sz and price <= 0:
            return "unknown", [self._status_event("已成部分的真实价格仍待核实")]
        events = self._adopt_partial(order, state, ts)
        self.resting = None
        self._note_resting_cancel(reason)
        if accumulated > 0:
            return "filled", events
        self._note_open_expired(order)
        return "canceled", events + [self._status_event("已核实撤单且无成交")]

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
        if (order.intent == "open" and self.open_attempts is not None
                and not self.open_attempts.matches_resting(order, state)):
            return [self._status_event("原挂单的身份与成交事实仍待核实")]
        phase = classify_state(state.get("state"))
        if phase == "filled":
            return self._settle_resting_fill(order, state, ts)
        if phase == "partial":
            return self._adopt_partial(order, state, ts)
        if phase == "canceled":
            events = (self._adopt_partial(order, state, ts)
                      if order.intent == "open" else [])
            self._note_open_expired(order)          # E-7：同上（交易所侧撤 / TTL 到期撤）
            self.resting = None
            return events + [self._status_event(
                "挂单已撤销（我方或交易所侧），已收敛为无挂单")]
        if phase == "live" and is_expired(order, ts):
            _, events = await self._cancel_resting("expired", ts)
            return events
        return []       # live 未到期 / unknown：保持挂单，下轮再判（兜底方向恒为「不动」）

    def _settle_resting_fill(self, order: RestingOrder, state: dict, ts: int) -> list[dict]:
        """挂单**全部成交**：按 intent 分派采纳，清空 resting 并推 trade 事件。"""
        if (order.intent == "open" and self.open_attempts is not None
                and not self.open_attempts.matches_resting(order, state)):
            return [self._status_event("原挂单的身份与成交事实仍待核实")]
        self.resting = None
        avg_px = float(state.get("avgPx") or order.px)
        acc = float(state.get("accFillSz") or order.sz)
        delta = fill_delta(order, acc)
        if delta <= 0:      # 已在此前的 partially_filled 轮全额采纳过，本轮只做收敛
            return [self._status_event("挂单已全部成交（成交量此前已采纳），已收敛为无挂单")]
        if order.intent == "open":
            return self._adopt_fill(order, avg_px, acc, ts, delta_sz=delta)
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
        delta = delta_sz if delta_sz is not None else total_sz
        previous_notional = order.adopted_sz * self.entry_price
        if self.open_attempts is not None:
            known = self.open_attempts.adopted_notional(order.cl_ord_id)
            if known is not None:
                previous_notional = known
        delta_px = ((total_sz * avg_px - previous_notional) / delta
                    if delta > 0 and order.adopted_sz > 0 else avg_px)
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
        details = (self.open_attempts.order_details(order.cl_ord_id)
                   if self.open_attempts is not None else {})
        if details:
            tp, sl = details.get("tp_trigger_px"), details.get("sl_trigger_px")
        ord_type = details.get("ord_type", "limit")
        explain = self._open_explain(order.side, order.reason, None, tp, sl)
        trade = self._make_trade(ts, order.side, delta_px, delta,
                                 pnl=0.0, reason=order.reason or "signal_open",
                                 tp=tp, sl=sl, explain=explain, cl_ord_id=order.cl_ord_id,
                                 ord_id=order.ord_id, position_pct=self.entry_position_pct,
                                 ord_type=ord_type,
                                 expected_px=order.px if ord_type == "limit" else None)
        if self.entry_fee_usd is not None:
            self.entry_fee_usd += float(trade["fee"])
        self._record_trade(trade)
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
        cl_ord_id, ord_id = self._order_ids(result)
        trade = self._make_trade(ts, "close", ref_px, qty, pnl=pnl, pnl_pct=pnl_pct,
                                 reason=reason, explain=explain, cl_ord_id=cl_ord_id,
                                 ord_id=ord_id, position_pct=position_pct,
                                 ord_type=ord_type, expected_px=expected_px)
        if self.realized_pnl_usd is not None:
            self.realized_pnl_usd += pnl
        self.unrealized_usd = self.gross_unrealized_usd = 0.0
        self.projected = "flat"
        self.entry_qty_sz = 0.0
        self.entry_fee_usd = 0.0                   # §3.3b 复位点 1/3：主动平仓 / 限价平仓采纳
        self.entry_position_pct = None
        self._record_trade(trade)
        return [{"type": "trade", "data": trade}]

    def _settle_benign_close(self, ref_px: float, ts: int) -> list[dict]:
        """平仓收 LIVE_NO_POSITION（交易所侧 OCO 已先平）：合成只读外部平仓成交 + 良性归 flat（R8）。

        v2（P1-1）：在归 flat 前合成一条 kind="external_close" 成交，记录已发生的交易所侧平仓；
        **不调用任何下单 / 平仓接口**，best-effort + 异常隔离。

        调用前提（exit-priority【R:P1-1】）：**平仓挂单不在飞**。挂单在飞时这笔多半是我方限价平仓
        成交的，此处的 `source="oco"` 与按 `ref_px` 算出的盈亏都会是假的——那条时序由 `_do_close`
        自己拦下并延后一轮收敛，绝不走到这里。
        """
        events = self._synthesize_external_close(ref_px, ts, source="oco")
        trades = [e["data"] for e in events if e.get("type") == "trade"]
        if not trades:
            return events
        if self.realized_pnl_usd is not None:
            self.realized_pnl_usd += trades[0]["pnl"]
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
            # 轴 C-2：**每一次重试都强制穿透**。走缓存会让三次重试读到同一个答案，
            # 于是「等净仓归零」变成一个恒假的死循环（门禁 G-C7 直接构造这个场景）。
            if self._net_abs_pos(await self._read_positions_fresh()) <= 0:
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
        # 轴 C-4：默认「此刻」——注入替身不会覆盖它，于是 30+ 个用例与 `live_auto` 的行为逐字节
        # 不变（恒 fail-open）；两个默认读法会把它覆盖成这份读数**真实取得**的时刻。
        self._positions_taken_wall = time.time()
        try:
            positions = await self._read_positions()
        except OKXError as exc:
            return events + self._on_reconcile_failure(ts, exc)
        if self._settling is not None:
            # 轴 A：未决态有它自己的三条出路（幻影撤销 / 结算命中 / 死线），**不进** `_on_reconcile_ok`
            # ——那个函数是同步纯判定，而取回结算价必须 await。分派留在这里即满足原则 2「判定与 IO
            # 分离」，也使不变式 I-2（`settling` 期间浮盈冻结）成为**构造性**结论而不是一句断言：
            # 重写 `unrealized_usd` 的那两行根本不在这条路径上。
            #
            # ⚠️ 降级恢复**必须照常发生**：这一行读数是真的取回来了（上面的 try 已经过了），
            # 而 `_clear_degraded_if_recovered` 原本住在 `_on_reconcile_ok` 里。不在这里补一次，
            # 一次落在结算窗口内的连接恢复就会被推迟最多一整个 deadline（90s）才被承认，
            # 界面上那条「盯市已降级」会挂着不走——那是一条与轴 A 毫无关系的静默回归。
            return (events + self._clear_degraded_if_recovered()
                    + await self._advance_settling(self._net_abs_pos(positions), ts))
        events += self._on_reconcile_ok(mark_px, positions, ts)
        if self.persist_settlement_identity is not None:
            await self.persist_settlement_identity()
        return events

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
        if (not grace and self.settlement_identity is not None
                and self._positions_taken_wall > 0
                and self._snapshot_outranks_last_write()):
            self.settlement_identity.observe_position({
                "positions": positions,
                "observedAtTs": self._positions_taken_wall,
                "accountId": self.account_id, "fresh": True})
        abs_pos = self._net_abs_pos(positions)
        # 不变式 I-2（轴 A）：`settling` **期间**这两行一次都不会被执行——`reconcile` 在调本方法
        # 之前就把未决态分派给了 `_advance_settling`。但**建立未决态的这一轮**它们已经跑过了，而
        # 此刻交易所侧已空 ⇒ `_net_upl` 恒返回 0。所以「冻结在侦测那一刻」要冻的是**上一轮**的值
        # ——那是这笔仓最后一次真的被看见时的浮盈。不先存下来再回填，界面会显示「持有中 /
        # 浮盈 0.00」，而那正是评审 RV-P1-9 点名的那个假数字。
        prev_unrealized, prev_gross = self.unrealized_usd, self.gross_unrealized_usd
        self.gross_unrealized_usd = self._net_upl(positions)      # OKX upl：纯价差、不含任何费用
        self.unrealized_usd = self._net_unrealized(self.gross_unrealized_usd, mark_px)
        if (self.projected != "flat" and abs_pos <= 0 and not grace
                and self._snapshot_outranks_last_write()
                and not self._external_close_suppressed()):
            if self._settlement_armed():
                # ① 轴 A：不就地合成，先冻结立场去向交易所要结算价（最多 deadline 秒）。
                # **本轮不改 projected / realized_pnl_usd / unrealized**（不变式 I-2）。
                self.unrealized_usd, self.gross_unrealized_usd = prev_unrealized, prev_gross
                return events + self._begin_settling(mark_px, ts)
            # v2（P1-1）：侦测到交易所侧 / 外部已平仓，合成只读 external_close 成交后再校正为 flat。
            # 总闸关 / `settlement_fn is None`（live_auto 全部路径）走这里：**零加法键**（A-4 ⓐ 列）。
            events += self._book_external_close(mark_px, ts, settlement=None)
            events.append(self._status_event("持仓已在交易所侧平仓 / 外部干预，已校正为 flat"))
            return events
        return events + self._note_orphan(abs_pos, ts, positions, grace)

    # ---------- 轴 A：settling 状态机（`settlement_fn is None` ⇒ 整条不存在）----------
    def configure_settlement_identity(self, config: IdentityConfig) -> None:
        """仅有结算读取接口的 AI 会话装配持仓证据跟踪器。"""
        if self._settlement_fn is not None:
            self.settlement_identity = SettlementIdentityTracker(config)

    def _settlement_armed(self) -> bool:
        """轴 A 是否装配：取数缝在 + 总闸开。两者缺一即逐字回退今天的立即合成。"""
        return (self._settlement_fn is not None
                and bool(getattr(self.settings, "ai_trade_settlement_truth_enabled", False)))

    def _settling_blocks_close(self) -> bool:
        """`settling` 期间**禁止一切出站平仓**（不变式 I-2b，评审 RV-P0-3）。

        与既有 `_external_close_suppressed()` 立的是同一条规矩，方向也一致——**延后比编一个数
        诚实，代价只是延后一轮**。缺了它会出现本轮唯一一条能同时污染真实资金账本与下游全部学习
        样本的错误：`settling` 期间 `projected` 刻意不变，于是利润保护 / 持仓周期 / 模型 close /
        flatten 仍会真的发平仓单，交易所回 `LIVE_NO_POSITION` ⇒ `_settle_benign_close` 抢先按
        `mark_px` 落一条账并 `realized_pnl_usd +=`；下一 tick 结算命中再落一次 ⇒ **已实现盈亏被
        加了两次**。`_synthesize_external_close` 的首行守卫只挡第二条 `trade`，挡不住调用方那一行。
        """
        return self._settling is not None

    def _begin_settling(self, mark_px: float, ts: int) -> list[dict]:
        """建立未决态：冻结立场与浮盈，推一条能看懂的 status。**不落任何库、不推 trade**（I-1）。"""
        deadline_s = float(getattr(self.settings, "ai_trade_settlement_deadline_s", 90.0))
        self._settling = SettlementPending(
            detect_ts=int(time.time()), mark_px=float(mark_px),
            deadline=time.time() + max(1.0, deadline_s),
            entry_ts=self._entry_wall_ts(), direction=self.projected,
            qty=self.entry_qty_sz,
            frozen_unrealized=self.unrealized_usd,
            frozen_gross_unrealized=self.gross_unrealized_usd,
            context=deepcopy(self.settlement_identity.context())
            if self.settlement_identity is not None else None)
        self._set_settlement_reason(self._settling, "not_found", time.time())
        return [self._status_event(
            f"该持仓已在交易所侧平掉，正在取回真实结算价（最多 {int(deadline_s)} 秒），"
            "期间本会话不会再对它发出任何平仓单")]

    def _entry_wall_ts(self) -> int:
        """本地开仓腿的**墙钟**秒（结算匹配判据 5 的左端）。

        取最近一条开仓成交的 `wallTs ?? ts`：`_make_trade` 把 `ts` 吸附到了所属 K 线的开盘秒，而
        交易所的 `cTime` 是真实墙钟——拿一个被吸附过的秒去比最多会早 `bar_seconds`，那正是判据 5
        的 `tol_s` 要吸收的东西；但能拿到墙钟就绝不用吸附值（本仓库双时钟的成文教训）。
        """
        tracker = self.settlement_identity
        context = tracker.context() if tracker is not None else None
        if context is not None:
            return int(context.first_open_wall_ts or 0)
        wall = 0
        for trade in reversed(self.trades):
            if trade.get("side") == "close" and trade.get("positionAfter") == 0:
                break
            if trade.get("side") in ("long", "short"):
                meta = (trade.get("explain") or {}).get("meta") or {}
                candidate = trade.get("wallTs") or meta.get("wallTs")
                if candidate:
                    wall = int(candidate)
        return wall

    async def _advance_settling(self, abs_pos: float, ts: int) -> list[dict]:
        """未决态的三条出路：幻影撤销 / 结算命中 / 死线到期。**绝不第四条**（R-1）。"""
        pending = self._settling
        if pending is None:                      # 防御：并发路径已清掉它
            return []
        if abs_pos > 0:
            # ② a. 幻影：持仓又回来了（快照抖动 / 部分平仓）⇒ 撤销等待，一个字节都不落。
            self._settling = None
            self.unrealized_usd = pending.frozen_unrealized
            self.gross_unrealized_usd = pending.frozen_gross_unrealized
            return [self._status_event("持仓仍在交易所侧，已撤销本次结算等待，恢复正常盯市")]
        if self.projected == "flat" or self.entry_qty_sz <= 0:
            return self._finish_settling(None, ts, reject_reason=None)
        now = time.time()
        if (self.restore_claimed_settlement is not None
                and await self.restore_claimed_settlement(pending)):
            self._settling = None
            return [self._status_event("已恢复本次持仓已保存的结算记录")]
        if (pending.validation or {}).get("status") == "already_claimed":
            return []
        fact, reject = await self._poll_settlement(pending, now)
        if (pending.validation or {}).get("status") == "already_claimed":
            return []
        if fact is not None:
            return self._finish_settling(fact, ts, reject_reason=reject)
        if now >= pending.deadline:
            # ② c. 死线：按侦测那一刻的 `mark_px` 落账（价格与今天**逐位相同**，只多三个留痕键）。
            reason = (pending.validation or {}).get("reason")
            return self._finish_settling(None, ts, reject_reason=reason)
        return []

    async def _poll_settlement(self, pending: SettlementPending,
                               now: float):
        """沿原节拍校验冻结 epoch，证据暂缺只更新原因。"""
        interval = self.settings.ai_trade_settlement_poll_interval_s
        if now - pending.last_poll_wall < max(0.5, interval):
            return None, None
        pending.last_poll_wall = now
        try:
            rows = await self._settlement_fn()
        except Exception as exc:
            logger.debug("settlement fetch failed (%s): %s", self.inst_id, exc)
            self._set_settlement_reason(pending, "fetch_failed", now)
            return None, "fetch_failed"
        tracker = self.settlement_identity
        query = SettlementQuery(
            self.inst_id, pending.direction, pending.entry_ts,
            pending.detect_ts, context=pending.context,
            tol_s=self.settings.ai_trade_settlement_match_tol_s)
        validation = validate_settlement(
            rows or [], query, tracker.claimed if tracker else {}, now)
        pending.validation = validation.wire()
        self.last_settlement_validation = deepcopy(pending.validation)
        if validation.status != "matched":
            return None, validation.reason
        if not self._matches_settlement_quantity(pending):
            self._set_settlement_reason(pending, "quantity_mismatch", now)
            return None, "quantity_mismatch"
        basis = "net" if self.settings.ai_trade_net_pnl_enabled else "gross"
        result = resolve_settlement_increment(
            validation.fact, pending.context, basis)
        if result.increment is None:
            self._set_settlement_reason(pending, result.reason, now)
            return None, result.reason
        pending.increment = result.increment
        pending.validation.update(
            priceBasis=result.increment.price_basis,
            feeAdjustment=result.increment.fee_adjustment)
        self.last_settlement_validation = deepcopy(pending.validation)
        return validation.fact, None

    def _matches_settlement_quantity(self, pending) -> bool:
        """历史累计量还必须与检测时及当前执行投影相容。"""
        context = pending.context
        if context is None:
            return False
        for value in (pending.qty, self.entry_qty_sz):
            quantity = finite_number(value)
            if (quantity is None or quantity <= 0 or not math.isclose(
                    quantity, context.remaining_qty, rel_tol=1e-9)):
                return False
        return True

    def _keep_settling_quantity_gap(self, pending) -> list[dict]:
        """截止允许估算金额，不能把缺失数量伪造为完整往返。"""
        self._settling = pending
        self._set_settlement_reason(pending, "quantity_mismatch", time.time())
        return [self._status_event("持仓数量与成交记录不符，保留结算核对状态")]

    def _set_settlement_reason(self, pending, reason, now) -> None:
        """只保存安全简报，未知字段不造零值。"""
        context = pending.context
        pending.validation = {
            "schemaVersion": 1, "status": "pending", "reason": reason,
            "detailReason": reason, "message": "结算证据尚未齐全",
            "asOfTs": int(now),
            "epochKey": context.epoch_key if context else None,
            "settlementKey": None, "identityBasis": "unknown",
            "amountCoverage": "unavailable", "queryCoverage": "single_page",
            "priceBasis": "unknown", "feeAdjustment": None}
        self.last_settlement_validation = deepcopy(pending.validation)

    def _finish_settling(self, fact: SettlementFact | None, ts: int, *,
                         reject_reason: str | None) -> list[dict]:
        """结束未决态并落账**一次**。首行自检使本方法幂等（评审 RV-P0-3 / 门禁 `G-A13`）。"""
        pending, self._settling = self._settling, None
        if pending is None:
            return []
        if self.projected == "flat" or self.entry_qty_sz <= 0:
            # 这笔仓已被别的通路落过账（如 `_settle_benign_close` 抢先）⇒ **一个字节都不落**。
            # 只清状态并说明，绝不再加一次 `realized_pnl_usd`。
            return [self._status_event(
                "结算价取回时该持仓已由其它通路落账，本轮不重复记账（已实现盈亏只计一次）")]
        if (pending.context is not None
                and not self._matches_settlement_quantity(pending)):
            return self._keep_settling_quantity_gap(pending)
        if fact is None:
            self._set_settlement_reason(
                pending, reject_reason or "not_found", time.time())
            pending.validation.update(status="local_estimate",
                                      reason="deadline_exceeded",
                                      priceBasis="local_mark")
        self.last_settlement_validation = deepcopy(pending.validation)
        self._pending_settlement_meta = {
            "fact": fact, "markPx": pending.mark_px,
            "waitedS": max(0, int(ts) - pending.detect_ts),
            "bookedOpenFeeUsd": self.entry_fee_usd, "rejectReason": reject_reason,
            "validation": deepcopy(pending.validation),
            "increment": pending.increment}
        try:
            events = self._book_external_close(
                pending.mark_px, ts, settlement=fact)
        except Exception as exc:
            logger.warning("settlement trade build failed (%s): %s",
                           self.inst_id, exc)
            self._pending_settlement_meta = None
            self._settling = pending
            return [self._status_event("结算记录尚未生成，保留当前持仓核对状态")]
        if not any(e.get("type") == "trade" for e in events):
            # 合成抛异常被 best-effort 吞掉（`_synthesize_external_close` 的 except 返回 []）⇒
            # 槽里那份 meta 没有归属者了。不就地清掉，它会被**下一笔**平仓认领——那是一次张冠李戴
            # 的归因，而且看起来完全正常。轮级槽的纪律是「消费即清空」，这里是它的异常出口。
            self._pending_settlement_meta = None
            self._settling = pending
            return events
        if fact is not None:
            self.settlement_exchange_count += 1
        else:
            self.settlement_local_mark_count += 1
        source = "交易所结算价" if fact is not None else "本地标记价（未能取回结算价）"
        return events + [self._status_event(
            f"持仓已在交易所侧平仓 / 外部干预，已按{source}落账并校正为 flat")]

    def _book_external_close(self, ref_px: float, ts: int, *,
                             settlement: SettlementFact | None) -> list[dict]:
        """外部平仓落账的**唯一**出口（合成成交 + 立场复位六行），三列口径见 spec §4.A A-4。

        `settlement is None` 时逐位等于本轮之前那六行（`G-A3` / `G-A8` 守）。
        """
        events = self._synthesize_external_close(ref_px, ts, source="external",
                                                 settlement=settlement)
        # 与上面那条腿**同源**：命中结算时按 `closeAvgPx` 与交易所口径，否则逐位等于今天。
        trades = [e["data"] for e in events if e.get("type") == "trade"]
        if not trades:
            return events
        if self.realized_pnl_usd is not None:
            self.realized_pnl_usd += trades[0]["pnl"]
        self.unrealized_usd = self.gross_unrealized_usd = 0.0
        self.projected = "flat"
        self.entry_qty_sz = 0.0
        self.entry_fee_usd = 0.0                   # §3.3b 复位点 3/3：外部干预平仓（漏掉即静默重扣）
        self.entry_position_pct = None
        return events

    def take_settlement_meta(self) -> dict | None:
        """取走并清空轮级槽（`session._inject_explain_meta` 的唯一消费点，**消费即清空**）。"""
        slot, self._pending_settlement_meta = self._pending_settlement_meta, None
        return slot

    async def drain_settlement(self, ts: int) -> list[dict]:
        """停手 / 暂停再做一次有超时的查询；未到原截止时间保留未决身份。"""
        pending = self._settling
        if pending is None:
            return []
        if (self.restore_claimed_settlement is not None
                and await self.restore_claimed_settlement(pending)):
            self._settling = None
            return []
        if (pending.validation or {}).get("status") == "already_claimed":
            return []
        timeout = float(getattr(self.settings, "ai_trade_reconcile_stop_timeout_s", 3.0))
        fact = reject = None
        if self._settlement_fn is not None:
            pending.last_poll_wall = 0.0            # 收尾这一次不受节流约束
            try:
                fact, reject = await asyncio.wait_for(
                    self._poll_settlement(pending, time.time()), timeout=max(0.5, timeout))
            except Exception as exc:  # noqa: BLE001 —— 超时仍遵循原截止时间
                logger.debug("ai-trade settlement drain gave up (%s): %s", self.inst_id, exc)
        if fact is None and time.time() < pending.deadline:
            return []
        if (pending.validation or {}).get("status") == "already_claimed":
            return []
        reject = reject or (pending.validation or {}).get("reason")
        return self._finish_settling(fact, ts, reject_reason=reject)

    # ---------- 轴 B：孤儿持仓的对称分支（只写状态与事件，零交易所写请求）----------
    def _note_orphan(self, abs_pos: float, ts: int, positions: list[dict],
                     grace: bool) -> list[dict]:
        """`projected == "flat" and abs_pos > 0` —— 今天 `_on_reconcile_ok` 全文缺失的那个方向。

        总闸关 ⇒ 本方法整条空转（`self.orphan` 恒 None ⇒ `_status_payload` 无该键，`G-B7`）。
        """
        if not bool(getattr(self.settings, "ai_trade_orphan_detect_enabled", False)):
            return []
        state = classify_orphan(OrphanFacts(
            projected=self.projected, abs_pos=abs_pos, settling=self._settling is not None,
            fill_grace=grace, streak=self._orphan_streak),
            confirm_rounds=int(getattr(self.settings, "ai_trade_orphan_confirm_rounds", 3)))
        if state == "none":
            self._orphan_streak = 0
            self._orphan_since_ts = 0
            self.orphan = None
            self.orphan_alerted = False           # 消失即复位，下次再出现要能重新告警
            return []
        self._orphan_streak += 1
        if self._orphan_since_ts <= 0:
            self._orphan_since_ts = int(ts)
        self.orphan = orphan_wire(
            state, qty=abs_pos, avg_px=self._net_avg_px(positions),
            upl_usd=self._net_upl(positions), since_ts=self._orphan_since_ts)
        if state != "confirmed" or self.orphan_alerted:
            return []
        self.orphan_alerted = True                # 幂等：只推一条，`resume()` 里清零
        return [{"type": "error", "data": {
            "ts": int(ts), "code": "AI_TRADE_ORPHAN_POSITION",
            "message": orphan_message(
                self.orphan, inst_id=self.inst_id,
                block_open=bool(getattr(self.settings, "ai_trade_orphan_block_open", False)))}}]

    def _net_avg_px(self, positions: list[dict]) -> float:
        """该标的持仓的开仓均价（多行时按张数加权；算不出 ⇒ 0.0）。"""
        rows = [p for p in positions if str(p.get("instId")) == self.inst_id]
        weight = sum(abs(_num_or_zero(p.get("pos"))) for p in rows)
        if weight <= 0:
            return 0.0
        total = sum(abs(_num_or_zero(p.get("pos"))) * _num_or_zero(p.get("avgPx"))
                    for p in rows)
        return total / weight

    def _snapshot_outranks_last_write(self) -> bool:
        """这份持仓读数是不是**比我自己最近一次私有写更新**（轴 C-4 防御纵深）。

        与 C-3（写即作废）是**有意的冗余**：C-3 是「记得调用」的纪律，C-4 是「忘了调用也不会
        出事」的构造性保证。不许以「重复」为由删掉任何一半——一次刚成交的开仓撞上一份 4 秒前的
        空快照，会被 `_on_reconcile_ok` 合成为 `external_close` 幻影平仓并按 mark_px 捏造盈亏，
        那正是 `ai-trade-limit-orders` O-3 已经付过一次代价的形状。

        条件不满足 ⇒ 本轮**只更新未实现盈亏，不裁决幻影**（与既有 `_fill_grace` 的「延后一轮」
        代价完全相同，且两者可叠加）。

        判据用的是 `self._positions_taken_wall`——**这份读数真实取得的时刻**，而不是 tick 的
        `ts`，也不是「我收到它的时刻」。一份 4 秒前的缓存快照此刻被交到手上，它承载的事实仍然
        是 4 秒前的；用收到时刻去比，等于让缓存把自己洗成新鲜的，C-4 就成了一句空话。

        **两条 fail-open 的退路，一条都不许省**（极性弄反 = 幻影平仓再也不会被裁决，而那是一条
        真实存在、必须被裁决的路径——交易所侧 OCO 触发就走它）：

        1. `_last_private_write_wall == 0`（没写过 / 冷启后第一轮）⇒ 恒真 ⇒ 逐字节回到今天。
        2. `_positions_taken_wall == 0`（**不知道这份读数是什么时候取的**）⇒ 同样恒真。
           它发生在两种情形：注入了 `positions_fn` 替身，或调用方绕过 `reconcile()` 直接调
           `_on_reconcile_ok`。C-4 是一道「知道得更多时才更严」的防御纵深，**不知道的时候它必须
           什么都不做**——否则一个测试替身或一条未来的新调用路径会静默关掉幻影平仓的裁决。
        """
        if self._last_private_write_wall <= 0 or self._positions_taken_wall <= 0:
            return True
        return self._positions_taken_wall >= self._last_private_write_wall

    def _note_private_write(self) -> None:
        """记下「我刚刚成功发出了一次会改变持仓的私有写」（轴 C-4 的置位点）。

        四个调用点（开仓 / 平仓 / 撤单 / flatten）都紧跟在 `await` 成功返回之后——**失败的写
        不置位**：一次被拒的下单没有改变任何持仓，把它记成「刚写过」只会让下一轮白白放弃一次
        本来正确的幻影判定。
        """
        self._last_private_write_wall = time.time()

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
        # 先只读核对已经消失的仓位；原截止时间内不能转而发送第二张平仓单。
        if self._settling is not None:
            events += await self.drain_settlement(ts)
            if self._settling is not None:
                return self._with_cancel_events({
                    "ok": False, "code": "AI_TRADE_SETTLEMENT_PENDING",
                    "message": "交易所结算仍待核实，保留恢复入口",
                    "residual": {"side": self.projected,
                                 "size": self.entry_qty_sz}}, events)
        if self.projected == "flat":
            return self._with_cancel_events({"ok": True, "closedSz": 0}, events)
        close = ClosePositionInput(
            inst_id=self.inst_id, pos_side=self._pos_side(), pos=self._signed_pos(),
            mgn_mode=self.td_mode, ord_type="market", px=None, sz=None)
        last_exc: OKXError | None = None
        for attempt in range(self.settings.live_auto_flatten_max_retries + 1):
            try:
                result = await self._close_position(close)
                self._note_private_write()         # 轴 C-4：flatten 同样是一次会改仓的私有写
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
                    ord_type: str = "market", expected_px: float | None = None,
                    override_fee_usd: float | None = None,
                    fee_override: FeeOverride | None = None) -> dict:
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
        if side == "close" and self.entry_fee_usd is None:
            envelope = trade.get("explain") or {}
            envelope.setdefault("meta", {}).update(
                pnlBasis="estimated", entryFeeBasis="unknown")
            trade["explain"] = envelope
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
                                      px=price, ord_type=ord_type, expected_px=expected_px,
                                      override_fee_usd=override_fee_usd,
                                      override=fee_override))
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

    def _synthesize_external_close(self, ref_px: float, ts: int, *, source: str,
                                   settlement: SettlementFact | None = None):
        """合成已发生的平仓，价格与金额共用同一份增量。"""
        if self.projected == "flat" or self.entry_qty_sz <= 0:
            return []
        slot = self._pending_settlement_meta or {}
        increment = slot.get("increment") if settlement is not None else None
        if settlement is not None and increment is None:
            return []
        exit_px = increment.price if increment else ref_px
        pnl = increment.pnl if increment else self._close_pnl(exit_px)
        explain = build_external_close_explain(
            pos_side=self.projected, source=source, entry_price=self.entry_price,
            exit_price=exit_px, pnl=pnl)
        meta = explain.setdefault("meta", {})
        if slot.get("validation") is not None:
            meta["settlementValidation"] = deepcopy(slot["validation"])
        if increment is not None:
            meta.update(accountingEventId=increment.accounting_event_id,
                        feeAdjustment=increment.fee_adjustment,
                        priceBasis=increment.price_basis,
                        settlementKey=settlement.settlement_key,
                        epochKey=slot["validation"].get("epochKey"))
        trade = self._make_trade(
            ts, "close", exit_px,
            increment.qty if increment else self.entry_qty_sz,
            pnl=pnl, pnl_pct=self._close_return_pct(exit_px),
            reason="external_close", explain=explain,
            position_pct=self.entry_position_pct, expected_px=exit_px,
            fee_override=(FeeOverride(increment.fee, increment.fee_basis)
                          if increment else None))
        self._record_trade(trade)
        return [{"type": "trade", "data": trade}]

    def _record_trade(self, trade: dict) -> None:
        """成交入账的**唯一入口**（trust-boundary-and-signal-integrity D5）。

        四个 append 点全部收敛到这里，是为了让「新增一个 append 点却忘了计数」在结构上不可能发生
        ——那类漏改不会报错，只会让换手闸悄悄放松、让统计悄悄低报。

        封顶按块裁（一次删到 `TRADE_LEDGER_MAX`，而不是每笔删一笔），把 `del` 的 O(n) 成本摊薄。
        它是**内存护栏**不是展示策略：`TRADE_LEDGER_MAX` 远超任何现实会话，正常情况下永不触发；
        真触发时三个计数器仍是全量真值，所以下游没有任何一处会因此低报。

        Args:
            trade: `_make_trade` 产出的成交字典（本方法只读它的 ``side``）。
        """
        if self.open_attempts is not None:
            self.open_attempts.decorate_trade(trade)
        if self.settlement_identity is not None:
            self.settlement_identity.record_trade(trade)
        meta = (trade.get("explain") or {}).get("meta") or {}
        if (self.settings.ai_trade_net_pnl_enabled
                and trade.get("side") == "close"
                and meta.get("pnlBasis") == "estimated"
                and meta.get("entryFeeBasis") == "unknown"):
            self.realized_pnl_usd = None
        self.trades.append(trade)
        self.trade_count += 1
        if trade.get("side") in ("long", "short"):
            self.open_count += 1
        if len(self.trades) > TRADE_LEDGER_MAX + _LEDGER_TRIM_SLACK:
            del self.trades[:len(self.trades) - TRADE_LEDGER_MAX]

    def restore_trades(self, rows: list[dict]) -> None:
        """续跑时回灌整段台账并**就地派生**两个计数器（D5 / P1-6）。

        计数器**不落库**：`ai_auto/resume.rebuild_continuity` 的不变式原文是「派生量绝不落库：
        落两份必然漂移，而这里漂移的是真实资金的盈亏显示」，而它本就从 DB 的**全量 `Trade` 行**
        重建 `rows`（无裁剪）。因此恢复后的计数器与暂停前**逐位相同**，既不需要新增持久化键，
        也不存在需要在 UI 上解释的降级口径。同一条理由也解释了这里为什么**不**接收已实现盈亏：
        它的真值是 `realized_pnl_usd`，由调用方从同一份 continuity 一并回灌。

        Args:
            rows: 从 DB 重建的全量成交行。
        """
        if self.settlement_identity is not None:
            self.settlement_identity.restore(rows, None)
        for trade in rows:
            meta = (trade.get("explain") or {}).get("meta") or {}
            if trade.get("side") == "close" and meta.get("settlementValidation"):
                self.last_settlement_validation = deepcopy(
                    meta["settlementValidation"])
        self.trades[:] = rows                     # 原地替换，绝不换掉容器对象本身
        if self.open_attempts is not None:
            self.open_attempts.restore_adopted(rows)
        self.trade_count = len(rows)
        self.open_count = sum(1 for row in rows if row.get("side") in ("long", "short"))
        if len(self.trades) > TRADE_LEDGER_MAX + _LEDGER_TRIM_SLACK:
            del self.trades[:len(self.trades) - TRADE_LEDGER_MAX]

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

    def _note_resting_cancel(self, reason: str) -> None:
        """一次真的发生了的撤单按原因分格（轴 E 观测半；`aiStats.sizing` 的三个读点之二）。

        `"replace"` = cancel-replace（撤掉再按新价挂回去，真正的「重挂」）；`"expired"` = TTL 到期；
        其余（`agent` / `pause` / `session_stop`）归 `restingCancelled`。三格分开是刻意的：E-0 前置
        调查的结论正是「那 5 次同价重挂里一次都不是 cancel-replace」，而只有分格才能让下一轮直接
        从生产读数上读出这件事，不必再翻一次日志。
        """
        if reason == "replace":
            self.resting_repriced += 1
        elif reason == "expired":
            self.resting_expired += 1
            self.resting_cancelled += 1
        else:
            self.resting_cancelled += 1

    def _note_open_expired(self, order: RestingOrder | None) -> None:
        """一笔**开仓**挂单未成交即消失（我方撤 / 到期撤 / 交易所侧撤）：记进 E-7 的累计计数。

        平仓挂单不计——「想平没平成」由 `last_close_block` 与 `exit_order_slot_busy` 承载，
        与开仓漏斗不是同一张图。方向取委托腿自己的 `side`（跨轮到达，轮级标志不可用）。
        """
        if order is None or order.intent != "open":
            return
        side = order.side if order.side in ("long", "short") else "unknown"
        self.open_expired_by_side[side] = self.open_expired_by_side.get(side, 0) + 1
