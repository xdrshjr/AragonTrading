"""交易所侧兜底真值化（ai-trade-turnover-budget-and-backstop §2.3）。

本模块存在的**全部理由**是消除一句从未被核实过的话。`hold_horizon_text._exchange_snapshot_line`
每轮都对模型说「交易所侧兜底：止盈 X / 止损 Y（进程退出后仍生效）」，而这两个数的真源是
`profit_guard_runtime.exchange_oco_px`——它首选最近一笔**本地开仓成交回执**里的触发价，取不到
就本地公式现算。也就是说这句承诺的证据链是「我下单时**打算**附上这两个价」，而不是「交易所现在
**确实**挂着这两张单」。而这句承诺恰恰是 `trend_hold`（目标持仓 1–3 天、唤醒周期 0）能够成立的
唯一前提——宪章原话是「即使本进程退出它们依然生效，故你**不必每轮盯盘**」。

故本模块的第一纪律是：**核实到才敢说「进程退出后仍生效」；核实不到就如实说未核实。**
第二纪律（[P0-2] / D-14）是：**「算不出」是第三态，一律 fail-open。** 把「不知道」渲染成红色的
确定坏消息，比原来那句没核实的话更坏——它会稳定地误导模型收紧退出计划。

本轮**明确不做**自动补挂 / 重挂（§2.3.5）：补挂需要一条全新的下单写路径，而本仓库全部 AI 侧安全层
的共同纪律是绝不新增下单路径。先把「知道有没有」做扎实——没有可信的检测，任何自动补挂都是在盲目
下单。

结构与 `cost_model` / `cost_runtime` / `cost_text` 的三分法同构：唯一那条 IO 收在 `fetch_algo_backstop`
里，分类与门禁都是纯函数，`BackstopRuntime` 只做单飞调度；文案层本就在 `hold_horizon_text`。
"""
from __future__ import annotations

import asyncio
import logging

from ..okx.trade_client import OKXTradeClient
from ..okx.trading import _normalize_algo_order
from .profit_guard_runtime import exchange_oco_px

logger = logging.getLogger(__name__)

BASIS_EXCHANGE = "exchange"
BASIS_LOCAL = "local"
BASIS_UNAVAILABLE = "unavailable"

# `unavailableReason` 的值域（前端标签逐条镜像；`None` 只出现在 `basis == "exchange"`）。
REASON_DISABLED = "disabled"
REASON_NO_CREDS = "no_creds"
REASON_NOT_RUN = "not_run"
REASON_ERROR = "error"
REASON_STALE = "stale"

REASON_UNCOVERED = "horizon_backstop_uncovered"

_OPEN_FOR_SIDE = {"long": "open_long", "short": "open_short"}
# 平仓腿的委托方向（多头靠 sell 平、空头靠 buy 平）。
_CLOSING_LEG_SIDE = {"long": "sell", "short": "buy"}
# 一条算法委托的两种腿；**只认止损腿**（D-7）：兜底的定义是「最坏情况下有人替你退出」，
# 止盈腿再多也不构成兜底。
_STOP_KIND = "sl"
_TAKE_KIND = "tp"


# ==================== §2.3.1 取数（唯一一条 IO） ====================
async def fetch_algo_backstop(creds, inst_id: str, *,
                              timeout_s: float) -> list[dict] | None:
    """并发查 `oco` 与 `conditional` 两类未触发算法委托；失败 ⇒ `None`（**绝不抛给调用方**）。

    **不新建任何客户端、端点与归一函数**：`OKXTradeClient.get_algo_pending_orders` 已存在，
    `okx.trading.fetch_live_open_orders` 已经在以完全相同的方式使用它（并发查两类 +
    `_normalize_algo_order` 归一）。[P1-7] 本函数**直接复用同一个归一产物形状**，不再定义
    第三种形状——两处各归一一份正是本仓库反复出事的形态。

    与 `fetch_live_open_orders` 的两点差异（都是刻意的）：
      · **不查 `orders-pending`**（普通挂单与兜底无关，省一条请求）；
      · **不上抛**：前者会把 `OKXError` 翻成 `OKXPrivateError` 抛出，本函数必须全吞并返回
        `None`——这条取数跑在盯市 tick 的后台任务里，一次网络抖动不该变成一次会话降级。

    Args:
        creds: 账户凭证；`None`（模拟盘 / 无凭证）⇒ **一个请求都不发**。
        inst_id: 只保留该标的的委托（其余标的的兜底与本会话无关）。
        timeout_s: 硬超时（秒）。

    Returns:
        归一后的委托列表（可为空列表 = 交易所侧确认一条都没有）；取数失败 ⇒ `None`。
        **空列表与 `None` 是两个结论**，调用方不得混用。
    """
    if creds is None:
        return None
    client = OKXTradeClient(creds.api_key, creds.secret_key, creds.passphrase)
    try:
        oco, cond = await asyncio.wait_for(
            asyncio.gather(client.get_algo_pending_orders("oco", inst_id),
                           client.get_algo_pending_orders("conditional", inst_id)),
            timeout=float(timeout_s))
    except Exception as exc:  # noqa: BLE001 —— 见 docstring：本函数绝不上抛
        logger.debug("ai-trade backstop fetch failed (%s): %s", inst_id, exc)
        return None
    finally:
        await client.aclose()
    out: list[dict] = []
    for row in (*oco, *cond):
        out.extend(r for r in _normalize_algo_order(row)
                   if not inst_id or r.get("instId") == inst_id)
    return out


# ==================== §2.3.2 纯核心分类（三态 basis + 三态覆盖率） ====================
def classify_backstop(orders: list[dict] | None, *, side: str, position_sz: float | None,
                      local_sl_px: float | None, local_tp_px: float | None,
                      checked_at_wall: float | None, now_wall: float, max_age_s: float,
                      unavailable_reason: str | None) -> dict:
    """把一次核实结果分类成 §5.4 的 wire；纯函数、无 IO。

    三态 `basis`（沿用资金费账本 `settled+accrued / accrued / unavailable` 的同款分级）：
      · `exchange`    —— 在 `max_age_s` 内向交易所核实过，触发价为**交易所读数**；
      · `local`       —— 未核实（未开启 / 无仓 / 尚未跑过一次），只有本地回执口径，
                         **不得**宣称持久性；
      · `unavailable` —— 尝试过但失败（`no_creds` / `error` / `stale`），同样不得宣称。

    覆盖率是**三态**（[P0-2]）：`coverageRatio` 为 `null` 是一等公民，它与 `0` 分别是
    「还不知道」与「确认裸露」两个结论。`mismatch`（最危险的一格）**只在**
    `basis == "exchange"` **且**交易所侧确认一条方向正确的止损都没有、**且**本地回执声称有止损价
    时为真——`coverageRatio is None` 永不构成 `mismatch`。
    """
    if unavailable_reason == REASON_DISABLED:
        return _wire(BASIS_LOCAL, REASON_DISABLED, local_sl_px, local_tp_px,
                     position_sz=position_sz)
    if orders is None:
        reason = unavailable_reason or REASON_NOT_RUN
        basis = BASIS_LOCAL if reason == REASON_NOT_RUN else BASIS_UNAVAILABLE
        return _wire(basis, reason, local_sl_px, local_tp_px, position_sz=position_sz)
    age = None if checked_at_wall is None else float(now_wall) - float(checked_at_wall)
    if age is None or (max_age_s > 0 and age > float(max_age_s)):
        # 绝不拿一个旧读数背书：超龄即降级，且**不再下发交易所价**（B-6）。
        return _wire(BASIS_UNAVAILABLE, REASON_STALE, local_sl_px, local_tp_px,
                     position_sz=position_sz)
    stops = [o for o in orders
             if o.get("kind") == _STOP_KIND and _is_closing_leg(o, side)]
    takes = [o for o in orders
             if o.get("kind") == _TAKE_KIND and _is_closing_leg(o, side)]
    covered = _covered_size(stops)
    # [P0-2] 覆盖判据**分两级**：先看张数，张数不可得再看 `closeFraction`，两者都不可得 ⇒ `None`。
    ratio = _coverage_ratio(covered, position_sz)
    if ratio is None and stops and position_sz is not None and float(position_sz) > 0:
        fraction = _close_fraction(stops)
        ratio = None if fraction is None else round(float(fraction), 6)
    wire = _wire(BASIS_EXCHANGE, None, local_sl_px, local_tp_px, position_sz=position_sz)
    wire.update({
        "checkedAgoSeconds": round(max(0.0, age), 3),
        "stopLossPx": _best_stop_px(stops, side),
        "takeProfitPx": _best_take_px(takes, side),
        "hasStop": bool(stops),
        "coveredSz": covered,
        "coverageRatio": ratio,
        "orders": len(stops) + len(takes),
        "mismatch": bool(not stops and local_sl_px is not None),
    })
    return wire


def _wire(basis: str, reason: str | None, local_sl_px, local_tp_px, *,
          position_sz: float | None) -> dict:
    """未核实 / 不可得分支的 wire 骨架（零样本口径：没有读数一律 `null`，绝不用 `0` 冒充）。

    这些分支下 `stopLossPx` / `takeProfitPx` **保持为空**：它们的语义是「交易所侧读到的价」，
    把本地回执的价填进去正是本轮要根除的那类谎言。本地口径另有 `localStopLossPx` 两键承载，
    文案层据 `basis` 决定用哪一套措辞（§2.3.3）。
    """
    return {"basis": basis, "unavailableReason": reason, "checkedAgoSeconds": None,
            "stopLossPx": None, "takeProfitPx": None, "hasStop": None,
            "coveredSz": None,
            "positionSz": (None if position_sz is None or float(position_sz) <= 0
                           else float(position_sz)),
            "coverageRatio": None, "orders": None,
            "localStopLossPx": local_sl_px, "localTakeProfitPx": local_tp_px,
            "mismatch": False}


def _is_closing_leg(order: dict, side: str) -> bool:
    """该委托是否是**当前持仓的**平仓腿（方向判据与 `okx.trading._classify_pending_kind` 同款）。

    [P0-2] `reduceOnly` 只作为**充分条件之一**，绝不作为必要条件：它走 `_as_bool`、缺省即
    `False`，而单向持仓（net）模式下的随单 OCO 未必带该字段——拿它当必要条件会把真实的止损腿
    全部剔掉，覆盖率恒 0。

    但「充分条件」的作用域是**本仓**：双向持仓下另一侧（`posSide` 与当前持仓相反）的平仓腿哪怕
    带着 `reduceOnly`，也不覆盖本仓一张——先判 `posSide` 归属再判方向，顺序反了会把对侧的止损
    算成自己的兜底，那是比没有兜底更危险的一种错。
    """
    pos_side = str(order.get("posSide") or "net")
    leg = str(order.get("side") or "")
    if pos_side in ("long", "short"):
        if pos_side != side:
            return False
        return bool(order.get("reduceOnly")) or leg == _CLOSING_LEG_SIDE[pos_side]
    if order.get("reduceOnly"):        # net（单向持仓）：此时 reduceOnly 即已限定为本仓
        return True
    return side in _CLOSING_LEG_SIDE and leg == _CLOSING_LEG_SIDE[side]


def _covered_size(stops: list[dict]) -> float | None:
    """已被止损委托覆盖的张数；**算不出即 `None`**（[P0-2]），绝不落 0 冒充「确认裸露」。

    两级判据：委托的 `sz > 0` ⇒ 累加；`sz` 不可用但 `closeFraction` 可解析 ⇒ 由调用方按比例
    处置。两者都不可用 ⇒ `None`。

    **一条止损腿都没有是一个确定的结论、不是「不知道」**：本函数只在核实成功（`basis ==
    "exchange"`）时被调用，此时空列表的语义是「交易所侧确认一张都没挂」⇒ `0.0`。把它也落成
    `None` 会让 `mismatch` 那一格永远显示成「还不知道」——而那一格正是本模块存在的全部理由。
    """
    if not stops:
        return 0.0
    total = 0.0
    seen = False
    for order in stops:
        size = order.get("sz")
        if size is None or float(size) <= 0:
            continue
        total += float(size)
        seen = True
    return total if seen else None


def _close_fraction(stops: list[dict]) -> float | None:
    """`closeFraction` 合计（R-10：**只在逐字等于 `"1"` 时判全仓**，不认识的取值一律 `None`）。

    OKX 的 `closeFraction` 是字符串字段，取值未必只有 `"1"`（部分平仓可能是 `"0.5"`）。把不认识的
    取值当成 1 会把覆盖率高估为全仓——那正好是本模块要根除的那类谎言的镜像。
    """
    total = 0.0
    seen = False
    for order in stops:
        raw = str(order.get("closeFraction") or "").strip()
        if not raw:
            continue
        if raw == "1":
            return 1.0
        try:
            value = float(raw)
        except (TypeError, ValueError):
            continue
        if 0 < value < 1:
            total += value
            seen = True
    return min(1.0, total) if seen else None


def _coverage_ratio(covered: float | None, position_sz: float | None) -> float | None:
    """覆盖率三态：`None` = 还不知道、`0` = 确认裸露、`(0,1]` = 实测覆盖。

    `position_sz <= 0`（无仓 / 限价挂单期 `projected` 被刻意不写 / 读数缺失）⇒ **不做除法**
    （B-14 守它不抛 `ZeroDivisionError`）。
    """
    if position_sz is None or float(position_sz) <= 0:
        return None
    if covered is None:
        return None
    return round(min(1.0, float(covered) / float(position_sz)), 6)


def _best_stop_px(stops: list[dict], side: str) -> float | None:
    """最保守的那条止损触发价（多头取最高、空头取最低）；一条都没有 ⇒ `None`。"""
    prices = [float(o["triggerPx"]) for o in stops if o.get("triggerPx") is not None]
    if not prices:
        return None
    return max(prices) if side == "long" else min(prices)


def _best_take_px(takes: list[dict], side: str) -> float | None:
    """最近的那条止盈触发价（多头取最低、空头取最高）；一条都没有 ⇒ `None`。"""
    prices = [float(o["triggerPx"]) for o in takes if o.get("triggerPx") is not None]
    if not prices:
        return None
    return min(prices) if side == "long" else max(prices)


# ==================== §2.3.4 G6 覆盖闸（只拦加仓） ====================
def apply_backstop_gate(decision, wire: dict | None, *, position_side: str,
                        enabled: bool, min_coverage: float) -> tuple[object, str | None]:
    """兜底覆盖闸：**只拦「在已有持仓上继续加仓」**，其余一律直通（同一对象返回）。

    判据（**五**条同时成立才拦）：
      ① 总闸开；
      ② `basis == "exchange"`（**只有核实过才敢拦**——未核实就拦等于用一个不确定性剥夺交易能力）；
      ③ `coverageRatio is not None`（[P0-2]：**覆盖未知一律放行**，「算不出」绝不能变成「不许加」，
         与 `hold_horizon._is_bad_px` 的既有极性逐字同构）；
      ④ `coverageRatio < min_coverage`；
      ⑤ `decision.action` 是与当前持仓**同向**的开仓。

    反向开仓（反手）不拦：它由 `hold_horizon` 的 `gate_reverse` 与换手闸负责，且反手会先平掉旧仓。
    空仓开仓不拦：新单自带随单 OCO，拦它只会制造一个本来不存在的裸露。

    为什么只拦加仓而不是「拦一切并报警」：裸露已经发生的那一部分，无论拦不拦都已经裸着；唯一还能
    由系统决定的是「要不要让它变得更大」。把范围限死在这一格，本闸就永远不会成为「明明该开仓却开
    不出来」的来源（D-8）。
    """
    if not enabled or not wire or position_side not in ("long", "short"):
        return decision, None
    if decision.action != _OPEN_FOR_SIDE[position_side]:
        return decision, None
    if wire.get("basis") != BASIS_EXCHANGE:
        return decision, None
    ratio = wire.get("coverageRatio")
    if ratio is None or float(ratio) >= float(min_coverage):
        return decision, None
    from .decider import TradeDecision

    reason = f"{REASON_UNCOVERED}({float(ratio) * 100:.0f}%)"
    return TradeDecision(action="hold", size_fraction=None, confidence=None,
                         reason=reason), reason


# ==================== §2.3.1 调度（单飞、只读、绝不阻塞盯市 tick） ====================
class BackstopRuntime:
    """一个会话的兜底核实运行时：单飞后台任务 + 最近一次分类结果。

    句柄形状与 `CostRuntime._maintenance` 完全一致（`asyncio.create_task` + `done()` 判活）。
    """

    def __init__(self, settings, creds, inst_id: str) -> None:
        self.settings = settings
        self.creds = creds
        self.inst_id = inst_id
        self.enabled = bool(getattr(settings, "ai_trade_backstop_check_enabled", True))
        self.last_orders: list[dict] | None = None
        self.checked_at_wall: float | None = None
        self.unavailable_reason: str | None = (
            None if self.enabled else REASON_DISABLED)
        self._side = "flat"
        self._position_sz: float | None = None
        self._local_sl_px: float | None = None
        self._local_tp_px: float | None = None
        self._task: asyncio.Task | None = None
        self._force = True          # 首次装配即核实一次（不必等一个 TTL）
        self._last_check_wall = 0.0
        # 约束 5：`stop()` 之后后台任务回来也**不再 push、不再写 last**——一个已停会话向 WS
        # 追发事件是本仓库还没犯过的错，不要从本轮开始犯。
        self._closed = False

    # ---------- 盯市 tick 调用（同步、单飞、绝不阻塞） ----------
    def schedule_check(self, now_wall: float, *, side: str, position_sz: float | None,
                       local_sl_px: float | None, local_tp_px: float | None,
                       push=None) -> None:
        """把一次只读核实排进后台任务；本方法是同步的，绝不阻塞 5s 盯市 tick（S-11）。

        四条约束：**单飞**（上一批在途即跳过）、**只在持仓时查**（无仓时「有没有兜底」这个问题
        没有意义，一个请求都不发）、**TTL 节流**（默认 120s；`force_next()` 之后下一个 tick 即查
        ——随单 OCO 是否真的挂上，是开仓后最值得立刻知道的一件事）、**异常全吞**（绝不打断盯市，
        更绝不污染 `executor._reconcile_fail_streak`——那条计数只属于持仓读取，混用会触发假降级）。
        """
        self._side = side
        self._position_sz = position_sz
        self._local_sl_px = local_sl_px
        self._local_tp_px = local_tp_px
        if not self.enabled or self._closed or self.creds is None:
            if self.creds is None and self.enabled:
                self.unavailable_reason = REASON_NO_CREDS
            return
        if side not in ("long", "short"):
            self.last_orders = None
            self.checked_at_wall = None
            self.unavailable_reason = REASON_NOT_RUN
            return
        if self._task is not None and not self._task.done():
            return
        ttl = float(getattr(self.settings, "ai_trade_backstop_poll_ttl_s", 120.0))
        if not self._force and float(now_wall) - self._last_check_wall < ttl:
            return
        self._force = False
        self._last_check_wall = float(now_wall)
        self._task = asyncio.create_task(self._run_check(push))

    def force_next(self) -> None:
        """下一个盯市 tick 立即核实一次（`note_open` 之后调用）。"""
        self._force = True

    async def check_now(self, now_wall: float, *, side: str, position_sz: float | None,
                        local_sl_px: float | None, local_tp_px: float | None,
                        timeout_s: float = 8.0) -> dict | None:
        """同步等一次核实并回 `wire()`（暂停回执 / 续跑复核专用，pause-resume §3.1.3 ⑥）。

        **复用 `schedule_check` 的同一条私有路径**（单飞、TTL、异常全吞），只是把在途任务
        `await` 出来 —— 绝不在此另写一份取数逻辑（两份取数迟早给出两种「覆盖率」）。
        超时 / 异常一律回 `self.wire(now_wall)`（那已是一个诚实的 `unavailable`），**绝不抛**。

        [P2-4]：**必须先 `force_next()`**。`schedule_check` 在 `ai_trade_backstop_poll_ttl_s`
        （默认 120s）内会直接 return 不建任务，于是这里会 await 到一个早已 `done()` 的旧任务并
        原样回一份**上一次**的读数——用户看到的「已核实覆盖 100%」其实是两分钟前的事实。
        """
        self.force_next()
        self.schedule_check(now_wall, side=side, position_sz=position_sz,
                            local_sl_px=local_sl_px, local_tp_px=local_tp_px)
        task = self._task
        if task is not None and not task.done():
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=max(0.1, timeout_s))
            except Exception as exc:  # noqa: BLE001 —— 超时 / 异常一律回诚实的 unavailable
                logger.debug("ai-trade backstop check_now degraded (%s): %s", self.inst_id, exc)
        return self.wire(now_wall)

    def close(self) -> None:
        """会话停止：置 `_closed`，在途任务回来后不再写任何状态、不再推任何事件。"""
        self._closed = True

    async def _run_check(self, push) -> None:
        """后台单飞体：取数 + 落最近读数；任何异常 ⇒ `unavailable(error)`，绝不外泄。

        `push` 只用于**一件事**：`mismatch` 由假翻真的那一刻追发一条 error 事件。那一格的语义是
        「本地记录声称挂过止损、而交易所侧确认一条都没有」——它是本轮唯一一个「确定的坏消息」，
        等到下一个决策轮才让用户知道是不可接受的。其余状态变化只随既有 status 快照下发，不额外
        制造事件流噪音。
        """
        was_mismatch = self._is_mismatch()
        try:
            timeout = float(getattr(self.settings, "ai_trade_backstop_timeout_s", 6.0))
            orders = await fetch_algo_backstop(self.creds, self.inst_id,
                                               timeout_s=timeout)
        except Exception as exc:  # noqa: BLE001 —— 后台任务绝不把异常抛回事件循环
            logger.debug("ai-trade backstop check failed (%s): %s", self.inst_id, exc)
            orders = None
        if self._closed:            # 约束 5：已停会话绝不再写状态、绝不再推事件
            return
        if orders is None:
            self.unavailable_reason = REASON_ERROR
            return
        self.last_orders = orders
        self.checked_at_wall = float(self._last_check_wall)
        self.unavailable_reason = None
        if push is not None and self._is_mismatch() and not was_mismatch:
            await push({"type": "error", "data": {
                "code": "AI_TRADE_BACKSTOP_MISSING",
                "message": (f"{self.inst_id}：本地记录声称已随单挂出交易所侧止损，但向交易所"
                            "核实的结果是一条都没有——此刻没有任何持久化兜底，"
                            "本进程是唯一的退出执行者。")}})

    def _is_mismatch(self) -> bool:
        """当前是否处于「本地声称有止损、交易所侧确认一条都没有」这一格（最危险的一格）。"""
        return bool((self.wire(self._last_check_wall) or {}).get("mismatch"))

    # ---------- 只读快照 ----------
    def wire(self, now_wall: float) -> dict | None:
        """最近一次分类结果（§5.4）；总闸关 ⇒ 仍下发一个 `local(disabled)` 的诚实空壳。

        为什么关掉总闸不整键缺席：文案层要据 `basis` 决定说不说「进程退出后仍生效」，而**关掉
        核实通道恰恰是最需要说清楚「本会话尚无核实通道」的那一档**。真正的整键缺席发生在
        `HorizonRuntime.backstop is None`（回测 / 未装配），由调用方负责。
        """
        max_age = float(getattr(self.settings, "ai_trade_backstop_max_age_s", 300.0))
        return classify_backstop(
            None if not self.enabled else self.last_orders,
            side=self._side, position_sz=self._position_sz,
            local_sl_px=self._local_sl_px, local_tp_px=self._local_tp_px,
            checked_at_wall=self.checked_at_wall, now_wall=float(now_wall),
            max_age_s=max_age, unavailable_reason=self.unavailable_reason)


def schedule_from_executor(runtime: BackstopRuntime | None, executor, *,
                           now_wall: float, push=None) -> None:
    """从 executor 读断面并排一次核实（会话侧只留一行；本函数不判定、不阻塞）。

    断面口径与本仓库既有各层一致：立场取 `executor.projected`（**投影立场**），本地回执口径的
    两个价只读转发 `exchange_oco_px` 的既有产物（§2.8 第 3 条：绝不重算）。
    """
    if runtime is None:
        return
    tp_px, sl_px, _ = exchange_oco_px(executor, executor.projected)
    runtime.schedule_check(now_wall, side=executor.projected,
                           position_sz=float(getattr(executor, "entry_qty_sz", 0.0) or 0.0),
                           local_sl_px=sl_px, local_tp_px=tp_px, push=push)


async def check_now_from_executor(runtime: BackstopRuntime | None, executor, *,
                                  now_wall: float, timeout_s: float = 8.0) -> dict | None:
    """从 executor 读断面并**同步等**一次核实（pause-resume §3.1.3 ⑥）。

    断面口径与 `schedule_from_executor` **同一处**：立场取 `executor.projected`，两个价只读
    转发 `exchange_oco_px` 的既有产物。刻意不让调用方自己算——那就是第二份口径。
    """
    if runtime is None:
        return None
    tp_px, sl_px, _ = exchange_oco_px(executor, executor.projected)
    return await runtime.check_now(
        now_wall, side=executor.projected,
        position_sz=float(getattr(executor, "entry_qty_sz", 0.0) or 0.0),
        local_sl_px=sl_px, local_tp_px=tp_px, timeout_s=timeout_s)
