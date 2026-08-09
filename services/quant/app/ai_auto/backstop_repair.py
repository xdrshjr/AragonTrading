"""交易所侧兜底自愈（ai-trade-backstop-repair-and-net-backtest §2.2）。

**两条不变式（改本文件前先读，它们比本层的任何功能都重要）：**

1. **本层永不参与决策。** 它不返回 `TradeDecision`、不产生降级码、不进 `gate_reason` 的或链、
   不进决策轨迹。本轮刻意不新增 G 编号（改用 H1/H2/H3），正是为了让这条边界在命名上就刺眼——
   用 G 编号命名会让下一个维护者去 `gate_reason` 的或链里找它，找不到再自己加一条。
2. **本层发出的每一张单都只可能减少敞口。** 每一张都带 `reduceOnly`（net 模式）或 `posSide`
   （对冲模式）锁死为平仓腿，且张数恒 ≤ 交易所实时持仓中**本层自管的、尚未被覆盖的**那一部分。

上一轮建成的 `exchange_backstop` 能诚实地告诉模型与用户交易所侧到底挂着什么，但对「一条止损都
没有」这个结论的全部处置只有改写 prompt 与拦下加仓：一个 `trend_hold` 会话（目标持仓 1–3 天、
唤醒周期 0）在发现自己的止损单不存在之后，会继续裸奔 1–3 天。本模块补上那个执行体。

**只补差额是唯一一种无论判断对错都不会让事情变糟的操作**——这句话是本层全部取舍的出发点，也是
不撤单 / 不改价 / 不补止盈腿 / 不自动平仓（§九 D-3 / D-4）的理由：那几种操作都要先判断「哪张单
是我的」，而判断错了就会让事情变糟。

分层纪律（[P1-5]，下游改本文件前必须遵守）：

| 层 | 允许依赖 | 禁止 |
|---|---|---|
| **纯核心** `RepairRule` / `RepairFacts` / `RepairPlan` / `plan_repair` / 常量 | 只有标准库 | 一切业务模块（含 `okx.*`） |
| **运行时** `RepairRuntime` / `schedule_repair` / `facts_from_executor` | `okx.*`、`db.session`、`exchange_backstop` 的常量、`profit_guard_runtime.exchange_oco_px` | **`hold_horizon` / `horizon_runtime`**（后者要读本模块的 wire，反向即成环） |
"""
from __future__ import annotations

import asyncio
import hashlib
import inspect
import logging
import math
from dataclasses import dataclass, replace

from ..db.session import get_sessionmaker
from ..okx.order_builder import AlgoStopInput
from ..okx.private_client import OKXPrivateError
from ..okx.error_text import trade_code_message
from ..okx.trading import place_backstop_stop
from .exchange_backstop import BASIS_EXCHANGE
from .profit_guard_runtime import SessionReadings, exchange_oco_px

logger = logging.getLogger(__name__)

REPAIR_MODES = ("plan", "arm")

STATE_IDLE, STATE_PLANNED, STATE_BLOCKED = "idle", "planned", "blocked"
STATE_PLACING, STATE_PLACED, STATE_VERIFIED, STATE_FAILED = (
    "placing", "placed", "verified", "failed")

# `blockedReason` 的值域（后端常量 / prompt 文案表 / 前端标签表 / 端点错误体**四处**镜像，
# 任一处新增取值而另三处未跟进，用例即红——放行条件 7）。
BLOCK_NOT_APPLICABLE = "not_applicable"    # 人工通道专用：此刻根本没有需要补的单
BLOCK_COVERAGE_UNKNOWN = "coverage_unknown"
BLOCK_NO_LOCAL_PX = "no_local_px"
BLOCK_NO_MARK_PX = "no_mark_px"
BLOCK_PX_STALE = "px_stale"
BLOCK_PX_THROUGH = "px_through"
BLOCK_PX_TOO_CLOSE = "px_too_close"
BLOCK_SIZE_BELOW_MIN = "size_below_min"
BLOCK_ORDER_IN_FLIGHT = "order_in_flight"
BLOCK_COOLING = "cooling"
BLOCK_ATTEMPTS = "attempts_exhausted"
BLOCK_VERIFY_FAILED = "verify_failed"


@dataclass(frozen=True)
class RepairRule:
    """会话级不可变契约；`None` ⇒ 整层不装配（wire 整键缺席、一个写请求都不发）。

    Attributes:
        mode: `plan`（只给方案，等人工一键执行）| `arm`（盯市 tick 上自动补挂）。
        min_gap_bps: 触发价与现价之间要求的最小距离——**补一张会立刻触发的止损 = 立刻市价平仓**。
        max_per_epoch: 单持仓周期上限；`0` ⇒ 本层整体关闭（自动与人工都不补），不是「无限次」。
        cooldown_s: 两次尝试之间的最小间隔（失败也计入）。
        max_per_session: 单会话硬上限，触顶后恒 `blocked(attempts_exhausted)`。
        min_coverage: **复用** `ai_trade_backstop_min_coverage`（G6 同一个数，不新开旋钮）。
        max_px_age_s: **复用** `ai_trade_profit_guard_max_px_age_s`（本层是它的第三个复用方）。
    """

    mode: str
    min_gap_bps: float
    max_per_epoch: int
    cooldown_s: float
    max_per_session: int
    min_coverage: float
    max_px_age_s: float


@dataclass(frozen=True)
class RepairFacts:
    """决策瞬刻的断面（[P1-5] **唯一读取点** `facts_from_executor`，自动与人工两条通道共用）。

    `mark_px` / `px_age_s` 的真源都是会话的 `_current_px()` / `_last_px_wall`——本层**绝不**另找
    一个时钟或另取一次价（上一轮 D-1 的阻断级修正：新鲜度判据取了只在确认根更新的时钟 ⇒ 15m
    执行下保护层 90% 时间恒 unavailable，就是取错时钟的成本）。
    """

    side: str                    # executor.projected（投影立场）
    mark_px: float | None
    px_age_s: float
    local_sl_px: float | None
    order_in_flight: bool


@dataclass(frozen=True)
class RepairPlan:
    """一次判定的产物：要么是一个可执行的具体方案，要么是一条**说得出原因**的暂缓。"""

    side: str
    stop_px: float | None
    sz: float | None             # 差额张数（未取整；取整与向下收敛在 H2 按 lotSz 做）
    covered_sz: float | None
    position_sz: float | None
    state: str
    blocked_reason: str | None


class AiTradeRepairBlocked(Exception):
    """人工通道被 H1/H2/H3 拦下（`reason` 取自上面的值域，API 层据此回 409）。"""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason
        self.message = message


# ==================== 纯核心（只用标准库；无 IO、绝不抛） ====================
def rule_from_settings(settings, mode: str | None, *,
                       horizon_armed: bool) -> RepairRule | None:
    """装配契约；下列任一成立 ⇒ `None`（整层不装配，一个写请求都不发）：

    运维总闸关 / `mode` 非法 / `max_per_epoch == 0` / **本会话未装配交易周期契约**。

    [P1-4] 最后一条是硬安全约束：未装配周期契约时 `HorizonRuntime.refresh` 恒返回 `None`，
    `status.horizon` 下发 `null`——挂在 `horizon.backstop.repair` 上的 wire、运行卡第五行、prompt
    子句会**同时全部消失**，而本层只受自己的运维总闸约束、`arm` 模式照样发单。
    **一条用户完全看不见的自动下单路径不允许存在**，这比「少一个功能」严重得多。
    """
    if not getattr(settings, "ai_trade_backstop_repair_enabled", False) or not horizon_armed:
        return None
    resolved = str(mode or settings.ai_trade_backstop_repair_default_mode)
    if resolved not in REPAIR_MODES:
        return None
    per_epoch = int(settings.ai_trade_backstop_repair_max_per_epoch)
    if per_epoch <= 0:
        return None
    return RepairRule(
        mode=resolved,
        min_gap_bps=float(settings.ai_trade_backstop_repair_min_gap_bps),
        max_per_epoch=per_epoch,
        cooldown_s=float(settings.ai_trade_backstop_repair_cooldown_s),
        max_per_session=int(settings.ai_trade_backstop_repair_max_per_session),
        min_coverage=float(getattr(settings, "ai_trade_backstop_min_coverage", 0.999)),
        max_px_age_s=float(getattr(settings, "ai_trade_profit_guard_max_px_age_s", 90.0)))


def _px(value) -> float | None:
    """判定用数值归一：非有限 / 非正 / 不可转 ⇒ `None`（沿用 `hold_horizon._is_bad_px` 的极性）。"""
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) and out > 0 else None


def _size(value) -> float | None:
    """张数归一：非有限 / 负 / 不可转 ⇒ `None`。**`0.0` 是一等公民**（「确认一张都没挂」）。"""
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) and out >= 0 else None


def plan_repair(backstop_wire: dict | None, *, rule: RepairRule | None, facts: RepairFacts,
                attempts_epoch: int, attempts_session: int,
                last_attempt_wall: float | None, now_wall: float) -> RepairPlan | None:
    """H1 判定锁（纯函数、无 IO、绝不抛）。不适用 ⇒ `None`（**≠ blocked**）。

    判据只取自上一轮已经算好的那份 wire，**不做第二次分类**（本仓库已为「同一口径两处各存一份」
    出过三次事故）。适用性四条（§2.2.1）：
      a. `basis == "exchange"`——**未核实就动手 = 盲目下单**，绝不允许；
      b. 有仓（`side` 为多空且 `positionSz > 0`）；
      c. [P0-2] `hasStop is False` **或** `coverageRatio is None` **或** `< min_coverage`
         ——语义是「覆盖没达标**或**还不知道达没达标」，两者都要进入处置流；
      d. `coveredSz` 必须可知，否则 `blocked(coverage_unknown)`。

    (d) 与 G6 的 fail-open 方向**刻意相反**：G6 面对「不知道」时放行（不该因不确定剥夺交易能力），
    本层面对「不知道」时不动手（不知道覆盖了多少就算不出该补多少张；硬按全仓补，在对冲模式下补出
    来的单不带 `reduceOnly`，一旦原有止损其实存在，两张同时触发会把仓位**打穿到反向**）。

    判据顺序**即**降级码优先级，且与 §2.2.1 / §2.2.2 / §2.2.3 逐行同序：
    `coverage_unknown → no_local_px → no_mark_px → px_stale → px_through → px_too_close →
    size_below_min → order_in_flight → attempts_exhausted → cooling`。
    （`px_stale` 排在 `px_through` **之前**是刻意的：价过期时「有没有越过」这个结论本身不成立，
    先报「不知道现价」比先报一个用旧价算出来的越过结论更诚实。）

    [P0-4] 本函数算出的 `sz` 只用 wire 的 `positionSz`（**本会话自管仓**），是**预览值**；真正下单
    的张数由 `place_backstop_stop` 在拿到交易所实时 `abs_pos` 后按 §2.2.2 重算。
    """
    if rule is None or not isinstance(backstop_wire, dict):
        return None
    if backstop_wire.get("basis") != BASIS_EXCHANGE or facts.side not in ("long", "short"):
        return None
    position_sz = _px(backstop_wire.get("positionSz"))
    if position_sz is None:
        return None
    ratio = backstop_wire.get("coverageRatio")
    if (backstop_wire.get("hasStop") and ratio is not None
            and float(ratio) >= rule.min_coverage):
        return None                                     # 覆盖达标：不动手
    covered = _size(backstop_wire.get("coveredSz"))
    stop_px, mark_px = _px(facts.local_sl_px), _px(facts.mark_px)
    sz = None if covered is None else max(0.0, position_sz - covered)
    plan = RepairPlan(side=facts.side, stop_px=stop_px, sz=sz, covered_sz=covered,
                      position_sz=position_sz, state=STATE_BLOCKED, blocked_reason=None)
    reason = _block_reason(rule, facts, stop_px=stop_px, mark_px=mark_px, sz=sz,
                           attempts_epoch=attempts_epoch, attempts_session=attempts_session,
                           last_attempt_wall=last_attempt_wall, now_wall=now_wall)
    if reason is not None:
        return replace(plan, blocked_reason=reason)
    return replace(plan, state=STATE_PLANNED if rule.mode == "plan" else STATE_PLACING)


def _block_reason(rule: RepairRule, facts: RepairFacts, *, stop_px, mark_px, sz,
                  attempts_epoch: int, attempts_session: int,
                  last_attempt_wall: float | None, now_wall: float) -> str | None:
    """按固定优先级取第一条不满足的判据；全过 ⇒ `None`。

    顺序固定的理由与 `hold_horizon` 一致：用户看到的那**一个**原因，必须是最具体、最能指导下一步
    动作的那一个。
    """
    if sz is None:
        return BLOCK_COVERAGE_UNKNOWN
    if stop_px is None:
        return BLOCK_NO_LOCAL_PX
    if mark_px is None:
        return BLOCK_NO_MARK_PX
    if rule.max_px_age_s > 0 and float(facts.px_age_s) > rule.max_px_age_s:
        return BLOCK_PX_STALE
    gap_bps = ((mark_px - stop_px) if facts.side == "long" else (stop_px - mark_px)) / mark_px * 1e4
    if gap_bps <= 0:
        return BLOCK_PX_THROUGH
    if gap_bps + 1e-9 < rule.min_gap_bps:
        return BLOCK_PX_TOO_CLOSE
    if sz <= 0:
        return BLOCK_SIZE_BELOW_MIN
    if facts.order_in_flight:
        return BLOCK_ORDER_IN_FLIGHT
    if attempts_epoch >= rule.max_per_epoch or attempts_session >= rule.max_per_session:
        return BLOCK_ATTEMPTS
    if (last_attempt_wall is not None
            and float(now_wall) - float(last_attempt_wall) < rule.cooldown_s):
        return BLOCK_COOLING
    return None


def algo_cl_ord_id(session_key: str, epoch_seq: int, attempt: int) -> str:
    """确定性幂等键 `arh{sid6}{epoch4}{att2}`（§2.2.4；15 字符、全字母数字、≤32）。

    [P0-1] **三段式，前两段缺一不可**。v1 只用 `(epoch, attempt)`，而 `_position_seq` 每个会话都
    从 0 起、且**每个成员是一个独立会话**——一个 3 标的分组里三名成员的第一次补挂会算出逐字节
    相同的 ID，幂等机制就从「防重复」翻转成「**第二个成员永远补不上**」，失败原因还是一个看起来
    完全正确的去重码。`session_key` 取 `f"{inst_id}|{started_ts}"`：**确定性**（同一会话内每次算
    都相同、不含调用时刻），但跨成员、跨重启互不相同。
    """
    sid6 = hashlib.sha256(session_key.encode("utf-8")).hexdigest()[:6]
    return f"arh{sid6}{int(epoch_seq) & 0xFFFF:04x}{int(attempt) & 0xFF:02x}"


# ==================== 运行时（可用 okx.* 与 db.session；绝不 import hold_horizon） ====================
def facts_from_executor(executor, readings: SessionReadings) -> RepairFacts:
    """断面的**唯一**读取点（`schedule_repair` 与人工端点共用，绝不各读一份）。

    `readings` 是会话侧那份既有的聚合读数（`SessionReadings`），判定价与判定价年龄因此与利润保护层
    / 周期层**同源**。本地止损价只读转发 `exchange_oco_px` 的既有产物（首选最近一笔开仓成交回执里
    的触发价），**绝不重算**——补挂的语义正是「把当初打算挂的那张单补上」。
    """
    _tp_px, sl_px, _source = exchange_oco_px(executor, executor.projected)
    return RepairFacts(side=executor.projected, mark_px=readings.mark_px,
                       px_age_s=float(readings.px_age_s), local_sl_px=sl_px,
                       order_in_flight=executor.resting is not None)


async def _default_place(executor, stop: AlgoStopInput) -> dict:
    """默认下单通道：**短生命周期** DB session（[P2-4]，镜像 `live_auto/executor.py:132-134`）。

    绝不复用会话的长 session——一次补挂横跨数百毫秒的网络往返，占着长 session 会把它拖成一条
    随时可能超时的热路径。
    """
    async with get_sessionmaker()() as db:
        return await place_backstop_stop(db, executor.creds, executor.account_id, stop)


class RepairRuntime:
    """一个会话的自愈运行时：状态机 + 三重频率锁 + 单飞下单任务。

    句柄形状与 `BackstopRuntime._task` 完全一致（`asyncio.create_task` + `done()` 判活）；
    `close()` 之后在途任务回来不再写状态、不再推事件。
    """

    def __init__(self, settings, rule: RepairRule | None, executor, inst_id: str, *,
                 session_key: str, verify_hook=None, place_fn=None) -> None:
        """
        Args:
            settings: 运行配置（本类只读，不参与判定式——判定量全在 `rule` 里）。
            rule: 已装配契约；`None` ⇒ 整层不装配。
            executor: 本会话的 `LiveExecutor`（只读 `creds` / `account_id` / `td_mode` /
                `pos_mode` / `resting` / `projected`；**绝不**碰它的写路径）。
            inst_id: 合约 ID。
            session_key: `f"{inst_id}|{session.started_ts}"`——`algoClOrdId` 的会话身份段由它派生
                （[P0-1]，跨成员 / 跨重启不得碰撞）。
            verify_hook: 补挂成功后立刻强制下一次核实（生产传 `BackstopRuntime.force_next`）。
                `placed → verified` 必须过一次**真实核实**才改口（§九 D-6）。
            place_fn: 下单通道（脱网单测可注入；默认 `_default_place`）。
        """
        self.settings = settings
        self.rule = rule
        self.executor = executor
        self.inst_id = inst_id
        self.session_key = session_key
        self.verify_hook = verify_hook
        self._place = place_fn or _default_place
        self._state = STATE_IDLE
        self._blocked_reason: str | None = None
        self._plan: RepairPlan | None = None
        self._epoch_seq = 0
        self._attempts_epoch = 0
        self._attempts_session = 0
        self._last_attempt_wall: float | None = None
        self._last_result: dict | None = None
        self._placed_wall: float | None = None
        self._await_verify = False
        self._now_wall = 0.0
        self._task: asyncio.Task | None = None
        self._change_task: asyncio.Task | None = None
        self._closed = False

    # ---------- 生命周期 ----------
    def note_epoch(self, seq: int) -> None:
        """新持仓 epoch ⇒ 本 epoch 计数清零、状态机回到 `idle`（会话计数**不**清零）。"""
        if self.rule is None or int(seq) == self._epoch_seq:
            return
        self._epoch_seq = int(seq)
        self._attempts_epoch = 0
        self._await_verify = False
        self._placed_wall = None
        self._plan = None
        self._state, self._blocked_reason = STATE_IDLE, None

    def close(self) -> None:
        """会话停止：在途任务回来后不再写状态、不再推事件（镜像 `BackstopRuntime.close`）。"""
        self._closed = True

    # ---------- 盯市 tick（同步、单飞、绝不阻塞） ----------
    def schedule(self, backstop_wire: dict | None, *, facts: RepairFacts, now_wall: float,
                 push=None, on_change=None) -> None:
        """判定一次并按模式决定要不要发单；`mode == "plan"` ⇒ **只落状态、一个请求都不发**。

        [P1-3] `on_change`：`state` 或 `blockedReason` 变化时调用一次（会话侧传补推 status 的薄
        封装）。**变化才调**天然节流——`status` 事件本身只在换向 / 唤醒跳过 / 决策轮 / 停会话时推，
        没有挂在 5s 盯市 tick 上，而本层的目标用户恰恰是唤醒周期 0、决策轮以小时计的 `trend_hold`。
        """
        if self.rule is None or self._closed:
            return
        before = (self._state, self._blocked_reason)
        plan = self._settle(backstop_wire, facts=facts, now_wall=now_wall)
        # 三个前置条件缺一不可：方案要发单、**上一发已核实完**（`_settle` 的状态覆盖已隐含这条，
        # 这里写明是因为人工通道曾在同一条不变式上漏掉它）、且没有另一发在途（单飞）。
        if (plan is not None and plan.state == STATE_PLACING and not self._await_verify
                and (self._task is None or self._task.done())):
            self._task = asyncio.create_task(self._run_place(plan, push))
        if (self._state, self._blocked_reason) == before:
            return
        if plan is not None and self._blocked_reason == BLOCK_PX_THROUGH:
            self._alert_px_through(plan, push)
        if on_change is not None:
            self._notify(on_change)

    def _alert_px_through(self, plan: RepairPlan, push) -> None:
        """`blocked(px_through)` **由假翻真的那一刻**高声报告一次（§2.4.3）。

        这一格与「补挂失败」是本层唯一两条会推 WS `error` 的路径，理由同样刺眼：现价已越过意向止损、
        交易所侧没有兜底、而系统**刻意**不补——补一张会立刻触发的止损等于立刻市价平仓，那不是兜底，
        那是替模型在最坏价位上做了一个它没做的退出决策。此刻正确的动作是把决定权交回决策层与利润
        保护层，而它们要先知道这件事。调用点已判过「状态变了」，故**变化才推**天然节流，
        连续多个 tick 仍为真不会重复推。
        """
        if push is None:
            return
        self._spawn(push({"type": "error", "data": {
            "code": "AI_TRADE_BACKSTOP_PX_THROUGH",
            "message": (f"{self.inst_id}：现价已越过本地止损价 {plan.stop_px:g}，系统拒绝补挂一张会"
                        "立刻触发的止损（那等于立刻市价平仓）——当前无持久化兜底，退出决策完全在"
                        "你手上。")}}))

    def _notify(self, on_change) -> None:
        """状态变化的一次性回调；返回协程即排一个任务——`schedule` 是同步的，不能 await。

        会话侧传的是「推一条 status 快照」，它本身是 async；本方法让调用方不必再自己包一层。
        """
        self._spawn(on_change())

    def _spawn(self, out) -> None:
        """把一个可能是协程的产物排成任务（同步的盯市 tick 上不能 await，也绝不能阻塞它）。"""
        if inspect.isawaitable(out):
            self._change_task = asyncio.create_task(out)

    def _settle(self, backstop_wire: dict | None, *, facts: RepairFacts,
                now_wall: float) -> RepairPlan | None:
        """判定 + 状态机推进（同步、纯内存）；返回本次的方案（不适用 ⇒ `None`）。"""
        self._now_wall = float(now_wall)
        plan = plan_repair(backstop_wire, rule=self.rule, facts=facts,
                           attempts_epoch=self._attempts_epoch,
                           attempts_session=self._attempts_session,
                           last_attempt_wall=self._last_attempt_wall, now_wall=now_wall)
        self._plan = plan
        if self._resolve_verification(backstop_wire, plan, now_wall=now_wall):
            return None if plan is None else replace(plan, state=self._state)
        if plan is None:
            self._state, self._blocked_reason = STATE_IDLE, None
        elif plan.state == STATE_BLOCKED:
            self._state, self._blocked_reason = STATE_BLOCKED, plan.blocked_reason
        else:
            self._state, self._blocked_reason = plan.state, None
        return plan

    def _resolve_verification(self, backstop_wire: dict | None, plan: RepairPlan | None, *,
                              now_wall: float) -> bool:
        """`placed → verified` / `placed → blocked(verify_failed)`（§九 D-6）。

        **不许自己给自己发绿勾**：OKX 回了一个 `algoId` 不等于那张单真的挂在那里（可能被风控立刻
        撤销、可能触发价被判非法而部分接受）。故只有当一次**发生在补挂之后**的真实核实回来，才允许
        改口——核实时刻由 wire 的 `checkedAgoSeconds` 反推，早于补挂的读数一律不作数。

        Returns:
            本次是否消费掉了待核实态（`True` ⇒ 调用方不再按普通判定改写状态）。
        """
        if not self._await_verify or self._placed_wall is None:
            return False
        ago = (backstop_wire or {}).get("checkedAgoSeconds")
        if (backstop_wire or {}).get("basis") != BASIS_EXCHANGE or ago is None:
            return True                       # 还没有新的核实结果：保持「已发出 · 核实中」
        if float(now_wall) - float(ago) < self._placed_wall:
            return True                       # 这份读数早于补挂，作不了数
        self._await_verify = False
        if plan is None:
            self._state, self._blocked_reason = STATE_VERIFIED, None
        else:
            self._state, self._blocked_reason = STATE_BLOCKED, BLOCK_VERIFY_FAILED
        return True

    async def _run_place(self, plan: RepairPlan, push) -> None:
        """后台单飞体：下单 → 落状态 → 失败时追一条 error 事件（绝不把异常抛回事件循环）。"""
        try:
            await self._execute(plan)
        except OKXPrivateError as exc:
            if self._closed:
                return
            self._note_failure(exc.code, exc.message)
            if push is not None:
                await push({"type": "error", "data": {
                    "code": "AI_TRADE_BACKSTOP_REPAIR_FAILED",
                    "message": (f"{self.inst_id}：系统尝试补挂交易所侧止损失败"
                                f"（{fail_detail(exc.code)}）——当前仍无持久化兜底。")}})
        except Exception as exc:  # noqa: BLE001 —— 后台任务绝不把异常抛回事件循环
            logger.warning("ai-trade backstop repair failed (%s): %s", self.inst_id, exc)
            if not self._closed:
                self._note_failure("REPAIR_INTERNAL_ERROR", str(exc))

    async def _execute(self, plan: RepairPlan) -> dict:
        """真正发出那一张补挂止损；成功即置 `placed` 并**立刻强制下一次核实**。"""
        self._attempts_epoch += 1
        self._attempts_session += 1
        self._last_attempt_wall = self._now_wall
        result = await self._place(self.executor, self._stop_input(plan))
        if self._closed:
            return result
        self._state, self._blocked_reason = STATE_PLACED, None
        self._placed_wall = self._now_wall
        self._await_verify = True
        self._last_result = {"ok": True, "ts": int(self._now_wall),
                             "algoId": result.get("algoId"),
                             "algoClOrdId": result.get("algoClOrdId"),
                             "stopPx": result.get("stopPx"), "sz": result.get("sz"),
                             "code": None, "message": None}
        if self.verify_hook is not None:
            self.verify_hook()
        return result

    def _note_failure(self, code: str, message: str) -> None:
        """落一次失败留痕。`codeLabel` 是**预渲染的中文原因**（§2.2.7 / §2.4.2）。

        为什么在这里渲染而不是让 prompt 与前端各查一张表：归一码的值域由 `okx.trading` 拥有
        （`_map_trade_code` 随交易所错误码演进），把它抄进两个呈现层，下一次新增码就会在卡片上
        变成一个原始英文串——而 `blockedReason` 那份四处镜像的表是本层**自己**的值域，两者不同。
        """
        self._state, self._blocked_reason = STATE_FAILED, None
        self._last_result = {"ok": False, "ts": int(self._now_wall), "algoId": None,
                             "algoClOrdId": None, "stopPx": None, "sz": None,
                             "code": code, "codeLabel": fail_message(code), "message": message}

    def _stop_input(self, plan: RepairPlan) -> AlgoStopInput:
        """方案 → 下单意图（锁侧口径与 `LiveExecutor._pos_side/_signed_pos` 逐字同款）。"""
        qty = float(plan.position_sz or 0.0)
        return AlgoStopInput(
            inst_id=self.inst_id,
            pos_side="net" if self.executor.pos_mode == "net_mode" else plan.side,
            pos=qty if plan.side == "long" else -qty,
            td_mode=self.executor.td_mode, sz=float(plan.sz or 0.0),
            stop_px=float(plan.stop_px or 0.0),
            algo_cl_ord_id=algo_cl_ord_id(self.session_key, self._epoch_seq,
                                          self._attempts_epoch - 1),
            self_position_sz=qty, covered_sz=float(plan.covered_sz or 0.0))

    # ---------- 人工通道 ----------
    async def repair_now(self, backstop_wire: dict | None, *, facts: RepairFacts,
                         now_wall: float) -> dict:
        """人工授权的单次补挂：**只绕过 `mode`，绝不绕过 H1/H2/H3**。

        Returns:
            §4.1 的 200 回执。

        Raises:
            AiTradeRepairBlocked: 被三道锁中的任意一道拦下（API 层据 `reason` 回 409）。
            OKXPrivateError: 交易所拒单（API 层回 502 + 原始 sCode）。
        """
        if self.rule is None:
            raise AiTradeRepairBlocked(BLOCK_NOT_APPLICABLE, "本会话未装配交易所侧兜底自愈")
        if self._task is not None and not self._task.done():
            raise AiTradeRepairBlocked(BLOCK_COOLING, "上一次补挂仍在途，请稍候")
        plan = self._settle(backstop_wire, facts=facts, now_wall=now_wall)
        if plan is None:
            raise AiTradeRepairBlocked(
                BLOCK_NOT_APPLICABLE, "此刻无需补挂（尚未核实 / 无持仓 / 交易所侧覆盖已达标）")
        # ⚠ 待核实态**必须在读 plan.state 之前拒**：`_settle` 在待核实时会把方案的 `state` 覆盖成
        # 运行时的 `placed`（那是给**自动**路径用的——让盯市 tick 在核实回来前不再发单），而人工
        # 通道只拒 `blocked` ⇒ 覆盖后的 `placed` 会一路走到 `_execute`，把冷却锁与次数锁**一起**
        # 静默跳过。两张 `reduceOnly` 全仓止损同时挂着，双双触发就把仓位打穿到反向（§七 R-2）。
        if self._await_verify:
            raise AiTradeRepairBlocked(
                BLOCK_COOLING, "上一发补挂已发出、正在向交易所核实；核实回来之前不补第二张")
        if STATE_BLOCKED in (self._state, plan.state):
            # 原因取**运行时**那一份（wire 下发的就是它），端点 409 与卡片因此永不各说一套。
            reason = self._blocked_reason or plan.blocked_reason
            raise AiTradeRepairBlocked(reason or BLOCK_NOT_APPLICABLE, block_message(reason))
        result = await self._execute(plan)
        return {"instId": self.inst_id, "state": self._state,
                "algoId": result.get("algoId"), "algoClOrdId": result.get("algoClOrdId"),
                "stopLossPx": plan.stop_px, "sz": result.get("sz"),
                "coveredBefore": plan.covered_sz, "positionSz": plan.position_sz}

    # ---------- 只读快照 ----------
    def wire(self) -> dict | None:
        """§5.4 的 `repair` 子对象；未装配 ⇒ `None` ⇒ 整键缺席（prompt 与卡片同时逐字回退）。

        两条形状纪律：`coveredSz` / `positionSz` **原样转发检测层**（本模块不重算）；
        **`null` 与 `0` 是两个结论**——`plannedSz == null` 是「算不出」、`0` 是「不需要补」；
        `coolingSeconds == null` 是「未冷却」、`0` 是「刚好到点」。
        """
        if self.rule is None:
            return None
        plan = self._plan
        return {"mode": self.rule.mode, "state": self._state,
                "blockedReason": self._blocked_reason,
                "plannedStopPx": None if plan is None else plan.stop_px,
                "plannedSz": None if plan is None else plan.sz,
                "coveredSz": None if plan is None else plan.covered_sz,
                "positionSz": None if plan is None else plan.position_sz,
                "attemptsEpoch": self._attempts_epoch,
                "maxPerEpoch": self.rule.max_per_epoch,
                "attemptsSession": self._attempts_session,
                "maxPerSession": self.rule.max_per_session,
                "coolingSeconds": self._cooling_seconds(),
                "lastResult": self._last_result}

    def _cooling_seconds(self) -> float | None:
        """冷却剩余秒数；未冷却 ⇒ `None`（**绝不用 0 冒充**）。"""
        if self.rule is None or self._last_attempt_wall is None or self.rule.cooldown_s <= 0:
            return None
        remaining = self.rule.cooldown_s - (self._now_wall - self._last_attempt_wall)
        return round(remaining, 3) if remaining > 0 else None


# `blockedReason` → 人话（端点 409 的 message；prompt 与前端各有自己那份镜像文案）。
_BLOCK_MESSAGE = {
    BLOCK_NOT_APPLICABLE: "此刻无需补挂（尚未核实 / 无持仓 / 交易所侧覆盖已达标）",
    BLOCK_COVERAGE_UNKNOWN: "交易所未回报可核对的委托张数，算不出该补多少张，故不动手",
    BLOCK_NO_LOCAL_PX: "本地没有可用的止损价，无从补起",
    BLOCK_NO_MARK_PX: "当前判定价不可用，无法确认补挂是否会立刻触发",
    BLOCK_PX_STALE: "当前判定价已过期，无法确认现价是否仍在止损的有利侧",
    BLOCK_PX_THROUGH: "现价已越过本地止损价，补挂会立刻触发（等于立刻市价平仓）",
    BLOCK_PX_TOO_CLOSE: "止损价距离现价过近，补挂可能立刻触发",
    BLOCK_SIZE_BELOW_MIN: "未覆盖差额不足最小下单量，补不出来",
    BLOCK_ORDER_IN_FLIGHT: "有一笔限价委托在途，持仓正在变化，此刻算出的差额下一秒就可能过期",
    BLOCK_COOLING: "两次补挂之间的冷却尚未结束",
    BLOCK_ATTEMPTS: "补挂次数已达上限（频率锁）",
    BLOCK_VERIFY_FAILED: "已补挂但下一次核实仍未覆盖，请人工检查交易所侧委托",
}


def block_message(reason: str | None) -> str:
    """降级码 → 中文原因（端点错误体与本模块共用同一张表，绝不各写一份）。"""
    return _BLOCK_MESSAGE.get(str(reason or ""), "补挂被暂缓")


def fail_message(code: str | None) -> str:
    """补挂失败码 → 中文原因（呈现层唯一入口；未知码原样回码本身，绝不显示一句猜的话）。

    绝大多数码来自 `okx.trading._map_trade_code` 的产物域，故直接读那边的表——码域归它拥有。
    只有本层自己造的 `REPAIR_INTERNAL_ERROR` 在这里补一句。
    """
    if str(code or "") == "REPAIR_INTERNAL_ERROR":
        return "系统内部错误（详见后端日志）"
    return trade_code_message(code)


def fail_detail(code: str | None) -> str:
    """事件流里的失败原因：中文 + 归一码。

    WS `error` 与卡片的取舍不同：卡片求一眼看懂（只给中文），而 `error` 事件是用户拿去交易所后台
    核对的那条线索，故把码一并带上。未知码只出现一次（此时中文就是码本身），不重复。
    """
    label = fail_message(code)
    return label if label == str(code or "") else f"{label} · {code}"


def schedule_repair(repair: RepairRuntime | None, backstop, executor,
                    readings: SessionReadings, *, push=None, on_change=None) -> None:
    """会话侧只留这一行：读断面 → 判定 → （`arm` 时）排一次单飞下单。

    断面经 `facts_from_executor`（**唯一读取点**）取得，自动与人工两条通道共用同一份口径。
    `backstop` 只被读一次 wire——本层从不自己向交易所核实，检测与执行的职责边界就在这一行。
    """
    if repair is None or backstop is None:
        return
    repair.schedule(backstop.wire(readings.now_wall),
                    facts=facts_from_executor(executor, readings),
                    now_wall=readings.now_wall, push=push, on_change=on_change)


async def repair_now_from_executor(repair: RepairRuntime | None, backstop, executor,
                                   readings: SessionReadings, *, on_change=None) -> dict:
    """人工通道的会话侧薄封装：断面走**同一个** `facts_from_executor`（自动与人工绝不各读一份）。

    Raises:
        AiTradeRepairBlocked: 整层未装配、或被 H1/H2/H3 拦下（API 层据 `reason` 回 409）。
    """
    if repair is None or backstop is None:
        raise AiTradeRepairBlocked(BLOCK_NOT_APPLICABLE, "本会话未装配交易所侧兜底自愈")
    result = await repair.repair_now(backstop.wire(readings.now_wall),
                                     facts=facts_from_executor(executor, readings),
                                     now_wall=readings.now_wall)
    if on_change is not None:
        await on_change()
    return result
