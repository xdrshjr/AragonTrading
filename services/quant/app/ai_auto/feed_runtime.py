"""行情断连自愈的**会话运行时**（ai-trade-feed-self-healing 轴 B 的动作层）。

范式逐字镜像既有的 `horizon_runtime.py` / `discipline_runtime.py` / `cost_runtime.py`：运行时对象
持有自愈状态与动作实现，`session.py` 只持有一个 `self._feed = FeedRuntime(self)` 并在
`_check_staleness` 里调它一次。**这不是「超出再拆」的逃生门，而是本轮的硬约束**——`session.py`
已 2923 行（CLAUDE.md 800 行红线的 3.6 倍），把五个动作方法与七个状态字段留在那里会把它推到 ~3130。

**判定一律来自 `feed_watchdog.plan()`，本模块只执行。** 两处各写一套判据是本仓库反复踩过的信任
崩塌来源（`edgeSource` 逐键手抄那次）。

**每一个动作都自带 `try/except Exception` 并只降级不外抛**：它们跑在 `_monitor_tick` 里，而那条
循环守着真实资金。`ai-trade-interval-wake-dead-monitor-loop` 那一轮的全部教训就是「一步失败即跳过
本 tick 余下全部步骤」，而 `_maybe_interval_wake` 恰好是 tick 的最后一行。
"""
from __future__ import annotations

import asyncio
import logging
import time

from ..okx.gap_repair import fetch_missing
from ..okx.session_link import ingest_ai_auto_live
from ..okx.types import BAR_SECONDS
from . import feed_watchdog

logger = logging.getLogger("aragon.ai_auto.feed")


class AiTradeFeedSelfHealDisabled(Exception):
    """运维总闸 `ai_trade_feed_selfheal_enabled` 关闭时【立即重连】被调用（端点层译成 409）。

    与 `AiTradeManualExitDisabled` / `AiTradeRepairBlocked` 同款姿态：会话层只抛稳定异常，中文与
    HTTP 码的翻译点**只有一个**（`api/ai_auto.py`），绝不在服务层拼 HTTP 语义。
    """

    code = "AI_TRADE_FEED_SELFHEAL_DISABLED"
    message = "【立即重连】未开启（行情自愈运维总闸关闭）"


class FeedRuntime:
    """一条会话的行情自愈状态与动作。纯执行器：判定全部来自 `feed_watchdog.plan()`。

    Attributes:
        stage: 当前阶段（`feed_watchdog.STAGE_*`）；进 wire，前端据它选颜色。
        backfill_attempts / relink_attempts: 本降级期内的尝试次数（WS 送来确认根即复位）。
        last_outcome: 上一次尝试的结果码（`ok` | `no_data` | `failed`；从未尝试 ⇒ None）。
        last_source: 最后一根确认根的来源（`ws` | `rest`；尚无 ⇒ None）。
    """

    def __init__(self, session) -> None:
        self._session = session
        self.stage = feed_watchdog.STAGE_FRESH
        self.backfill_attempts = 0
        self.relink_attempts = 0
        self.last_backfill_wall = 0.0
        self.last_relink_wall = 0.0
        self.last_attempt_wall = 0.0
        self.next_attempt_in_s: float | None = None
        self.last_outcome: str | None = None
        self.last_outcome_text: str | None = None
        self.last_source: str | None = None
        self._escalated_announced = False
        # 换代的串行闸（`AiTradeSession._rearm_feed` 全程持它）。本轮把 `_rearm_feed` 从「只有
        # `resume()` 一个调用点」变成三个（盯市 tick 的 L3、人工兜底、续跑），而它中途会把
        # `session._ws` 置空：两条并发进来时后到的那条看见 `None` ⇒ 跳过收尸 ⇒ 两条新连接同时
        # 在线，先到的那条随即被覆盖成一条没人持有、也没人停得掉的野连接。
        self.rearm_lock = asyncio.Lock()

    # ---------- 装配（供 `_check_staleness` 与 wire）----------
    @property
    def enabled(self) -> bool:
        """整轴总闸。缺字段 ⇒ 按开启处理（新代码的默认语义，与 `_lifecycle_truth_enabled` 同源）。"""
        return bool(getattr(self._session.executor.settings,
                            "ai_trade_feed_selfheal_enabled", True))

    def config(self) -> feed_watchdog.FeedConfig:
        """settings → 纯核心阈值集（纯核心不认识 `Settings`，这里是唯一一次翻译）。"""
        s = self._session.executor.settings
        return feed_watchdog.FeedConfig(
            enabled=self.enabled,
            backfill_interval_s=float(s.ai_trade_feed_backfill_interval_s),
            relink_factor=float(s.ai_trade_feed_relink_factor),
            relink_min_interval_s=float(s.ai_trade_feed_relink_min_interval_s),
            escalate_after_relinks=int(s.ai_trade_feed_escalate_after_relinks))

    def readings(self, *, feed_age_s: float, stale_after_s: float,
                 now_wall: float) -> feed_watchdog.FeedReadings:
        """本 tick 的读数（**唯一**构造点；纯内存，零 IO、零 REST）。

        `link_state` 走会话既有的 `_link_state()`（链路的实体一律住在 `okx/session_link.py`），
        `ws_alive` 走 `OKXWSClient.is_alive`——那个属性的 docstring 自己写着「这个属性就是那条静默
        的出口」，而在本轮之前**没有任何常驻代码去读它**。

        **本会话从未 `start()` 过（`_link is None`）时 `ws_alive` 报真**（见 spec §实施过程发现的
        方案缺陷 F-10）：那一档 `_rearm_feed` 是一个**有文档的零动作**（重建会造出一条没人处理其
        链路事件的野连接），判它「链路已死」只会产出一个永远不会成功的动作、外加一句「正在重建
        连接」的假话。生产上这一档恒不可达（`start()` 同时装 `_link` 与 `_ws`，而 `_check_staleness`
        的顶部判据要求 `_last_candle_wall > 0`，那也是 `start()` 写的）。
        """
        ws = getattr(self._session, "_ws", None)
        armed = getattr(self._session, "_link", None) is not None
        return feed_watchdog.FeedReadings(
            now_wall=now_wall,
            feed_age_s=feed_age_s,
            stale_after_s=stale_after_s,
            ws_alive=(bool(ws is not None and ws.is_alive) if armed else True),
            link_state=self._session._link_state(),
            is_degraded=bool(self._session._stale_degraded),
            last_backfill_wall=self.last_backfill_wall,
            last_relink_wall=self.last_relink_wall,
            relink_attempts=self.relink_attempts)

    def recovery_key(self) -> dict:
        """`{"feedRecovery": …}` 或**空字典**（总闸关时整键缺席）。

        键的存在性纪律**逐字服从它所在的那个 wire**：`member_lifecycle_wire` 的 docstring 明写
        「全部键恒下发，绝不因为『本轮没数据』而整键缺席——前端要能区分『没数据』与『后端没上线
        这个键』」。故：

        | 情形 | `feedRecovery` |
        |---|---|
        | 总闸关 | **整键缺席**（回答的是「后端有没有上线这个特性」） |
        | 已装配、行情正常 | **`null`**（键在，值为空 —— 回答的是「本轮有没有数据」） |
        | 已装配、正在自愈 / 已升级 | 对象 |
        """
        if not self.enabled:
            return {}
        if self.stage == feed_watchdog.STAGE_FRESH:
            return {"feedRecovery": None}
        return {"feedRecovery": self.wire()}

    def wire(self) -> dict:
        """恢复卡的载荷（§4.1）。前端一个数都不自己算，中文也一句都不自己拼。"""
        session = self._session
        now = time.time()
        feed_age = (max(0.0, now - session._last_candle_wall)
                    if session._last_candle_wall > 0 else 0.0)
        return {
            "state": feed_watchdog.view_state(self.stage),
            "stage": self.stage,
            "attempts": {"backfill": self.backfill_attempts, "relink": self.relink_attempts},
            "lastAttemptTs": int(self.last_attempt_wall) if self.last_attempt_wall > 0 else 0,
            "nextAttemptInS": (None if self.next_attempt_in_s is None
                               else max(0, int(round(self.next_attempt_in_s)))),
            "lastOutcome": self.last_outcome,
            "lastOutcomeText": self.last_outcome_text,
            "source": self.last_source,
            "recoveryText": feed_watchdog.describe(
                stage=self.stage, attempts_backfill=self.backfill_attempts,
                attempts_relink=self.relink_attempts, feed_age_s=feed_age,
                next_attempt_in_s=self.next_attempt_in_s, last_outcome=self.last_outcome,
                last_outcome_text=self.last_outcome_text, source=self.last_source),
        }

    # ---------- 证据驱动的复位（会话侧 `feed_candle` 确认根分支同址调用）----------
    def note_ws_bar(self) -> None:
        """WS 真的又送来了一根确认根：来源记 `ws`，换链计数复位（纯同步，零 IO）。

        **复位点只有这一个**（【R:P1-11】）。放在 `_feed_clear` 是错的：L2 补取成功时是
        `_feed_backfill` 自己清掉 `_stale_degraded`，此后 `plan()` 只会返回 `clear` / `none`，
        `_feed_clear` 再也走不到 ⇒ 计数永不复位 ⇒ 一个早已痊愈的会话会带着陈年计数走到 `escalate`。

        放在这里的另一重好处是它是**证据驱动**的：「换一次链、活 30 秒、又死」的抖动形态从不送出
        确认根，故它**仍然**能累积到 escalate。
        """
        self.last_source = "ws"
        self.relink_attempts = 0
        self.backfill_attempts = 0
        self._escalated_announced = False

    # ---------- 分派 ----------
    async def apply(self, plan: feed_watchdog.FeedPlan) -> None:
        """按 `action` 分派到五个动作之一。**绝不外抛**（跑在守着真实资金的盯市循环里）。"""
        self.stage = plan.stage
        self.next_attempt_in_s = plan.next_attempt_in_s
        handler = {
            feed_watchdog.ACTION_CLEAR: self._clear,
            feed_watchdog.ACTION_DEGRADE: self._degrade,
            feed_watchdog.ACTION_BACKFILL: self._backfill,
            feed_watchdog.ACTION_RELINK: self._relink,
            feed_watchdog.ACTION_ESCALATE: self._escalate,
        }.get(plan.action)
        if handler is None:
            return
        try:
            await handler(plan)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 —— 自愈失败只降级：绝不打死盯市循环
            self._note_outcome("failed", f"自愈动作失败（{type(exc).__name__}）")
            logger.warning("ai-trade feed action failed (%s / %s): %r",
                           self._session.inst_id, plan.action, exc)

    # ---------- 五个动作（每个 ≤25 行）----------
    async def _clear(self, _plan: feed_watchdog.FeedPlan) -> None:
        """L0：行情恢复出声。复用既有方法与既有文案（判定性文案仍是单点）。"""
        self._escalated_announced = False
        await self._session._clear_stale_if_recovered()

    async def _degrade(self, plan: feed_watchdog.FeedPlan) -> None:
        """L1：首次超阈，先说话，还没动手（文案逐字沿用本特性合入前的原文）。"""
        self._session._stale_degraded = True
        await self._push_status(plan.reason_text)

    async def _backfill(self, _plan: feed_watchdog.FeedPlan) -> None:
        """L2：REST 活性补取。取数层直接复用 `gap_repair.fetch_missing`（不新增取数路径）。"""
        session = self._session
        now = time.time()
        self.last_backfill_wall = self.last_attempt_wall = now
        self.backfill_attempts += 1
        bar_seconds = BAR_SECONDS.get(session.bar, 60)
        candles = await fetch_missing(
            session.inst_id, session.bar,
            since_ts=session._window.last_confirmed_ts,
            max_bars=int(session.executor.settings.okx_link_gap_repair_max_bars))
        # 时钟**在取数之后**再读一次（与 `_relink_once` 同口径）：`fetch_missing` 会走完整的 REST
        # 重试 / 退避，最坏能烧掉 `okx_link_rest_budget_s`（25 秒）。拿建仓前的 `now` 去判新鲜度，
        # 等于给「最后一根是不是当下这根」凭空多送一份容差——1m 周期下那是 40% 的窗口。
        result = (await ingest_ai_auto_live(session, candles, bar_seconds=bar_seconds,
                                            now_wall=time.time())
                  if candles else {"appended": 0, "fresh": False, "lastTs": None})
        if not result["fresh"]:
            self._note_outcome("no_data", "REST 未取到更新的 K 线")
            return
        self.last_source = "rest"
        self._note_outcome("ok", f"REST 已补取 {result['appended']} 根 K 线")
        await self._resume_wakes()

    async def _relink(self, plan: feed_watchdog.FeedPlan) -> None:
        """L3：强制换一条 WS（`_rearm_feed(force=True)`：无条件收尸旧的、建新的）。"""
        now = time.time()
        self.last_relink_wall = self.last_attempt_wall = now
        self.relink_attempts += 1
        await self._session._rearm_feed(force=True)
        self._note_outcome("ok", "已重建实时行情连接，等待交易所推送")
        await self._push_status(plan.reason_text)

    async def _escalate(self, plan: feed_watchdog.FeedPlan) -> None:
        """L4：自动恢复到顶，主动权交回用户。**只出声一次**，不每 5 秒刷屏。

        本层**绝不平仓**（立论 L-2）：自愈的全部权力边界是「重新拿到数据」，拿不到就如实说不知道。
        """
        if self._escalated_announced:
            return
        self._escalated_announced = True
        await self._push_status(plan.reason_text)

    # ---------- 人工兜底 ----------
    async def relink_now(self) -> dict:
        """人工授权的**单次**强制重连 + 活性补取（§3.3.4 / §4.2）。

        语义：先 `_rearm_feed(force=True)` 换一条新 WS，再立刻走一次活性补取；**不停止会话、
        不平仓、不改分组生命周期**（与 `/exit`、`/backstop/repair` 的姿态同源）。冷却对人工路径
        **不生效**（用户点了就是要现在试），但计数照常累加。

        **整个调用被一条硬死线包住**：前端 `request()` 对本路径落 `FAST_MS = 20_000`
        （`apps/web/lib/httpTimeout.ts` 的长跑白名单不含它），而本方法最该被点击的场景恰恰是
        「网络仍不通」——此时 `fetch_missing` 会走完整的 REST 重试 / 退避，很容易越过 20 秒。
        越过之后用户在**唯一一个兜底按钮**上读到的是「本次操作结果未知」，正是 `httpTimeout.ts`
        那段 docstring 写下来要消灭的失信形态。超时 ⇒ **不外抛**，返回与「重连了但没拿到更新的
        根」逐字相同的失败回执。

        Raises:
            AiTradeFeedSelfHealDisabled: 运维总闸关（端点层译成 409）。
        """
        if not self.enabled:
            raise AiTradeFeedSelfHealDisabled()
        deadline = float(self._session.executor.settings.ai_trade_feed_relink_deadline_s)
        try:
            receipt = await asyncio.wait_for(self._relink_once(), deadline)
        except asyncio.TimeoutError:
            receipt = self._relink_receipt(relinked=True, appended=0, fresh=False, last_ts=None)
        except Exception as exc:  # noqa: BLE001 —— 人工兜底永远给一个诚实的回执，绝不 500
            logger.warning("ai-trade manual relink failed (%s): %r", self._session.inst_id, exc)
            receipt = self._relink_receipt(relinked=False, appended=0, fresh=False, last_ts=None)
        if not receipt["ok"]:
            # 面板状态行的既有落点（`routeMemberEvent` 的 `case "error"`）：卡片自己会显示回执，
            # 但用户此刻可能正看着别的 tab，那条状态行是唯一会跟着他走的通道。
            await self._session._push({"type": "error", "data": {
                "ts": int(time.time()), "code": "AI_TRADE_FEED_RELINK_FAILED",
                "message": receipt["message"]}})
        return receipt

    async def _relink_once(self) -> dict:
        """换链 + 补取的单次执行体（死线由 `relink_now` 施加，这里只管做事）。"""
        session = self._session
        now = time.time()
        self.last_relink_wall = self.last_attempt_wall = now
        self.relink_attempts += 1
        await session._rearm_feed(force=True)
        self.last_backfill_wall = now
        self.backfill_attempts += 1
        bar_seconds = BAR_SECONDS.get(session.bar, 60)
        candles = await fetch_missing(
            session.inst_id, session.bar, since_ts=session._window.last_confirmed_ts,
            max_bars=int(session.executor.settings.okx_link_gap_repair_max_bars))
        result = (await ingest_ai_auto_live(session, candles, bar_seconds=bar_seconds,
                                            now_wall=time.time())
                  if candles else {"appended": 0, "fresh": False, "lastTs": None})
        if result["fresh"]:
            self.last_source = "rest"
            self._note_outcome("ok", f"REST 已补取 {result['appended']} 根 K 线")
            await self._resume_wakes()
        else:
            self._note_outcome("no_data", "REST 未取到更新的 K 线")
        return self._relink_receipt(relinked=True, appended=int(result["appended"]),
                                    fresh=bool(result["fresh"]), last_ts=result["lastTs"])

    # ---------- 辅助 ----------
    async def _resume_wakes(self) -> None:
        """补取到「当下这一根」之后的**唤醒交接**：三件事，一件都不能少。

        **绝不**从自愈层直接 `create_task(_run_decision_round(...))`：唤醒预算、空仓重复唤醒守卫、
        `_deciding` 槽位认领、待办账本全部挂在既有唤醒路径上，绕开它们就是「新通道绕过自己声称的
        约束」。但「让既有唤醒自然接手」也是假的——`_maybe_catchup_wake` 全仓唯一调用点在
        `_run_decision_round` 的 `finally`（自愈路径根本到不了）、`plan_catchup` 的第一道判据要
        待办（REST 补取不产生任何待办）、`_maybe_interval_wake` 的第一道判据是
        `wake_interval_s <= 0`（`trend_hold` 档常配 0 ⇒ 恒不唤醒）。照那样写的净后果是
        **抹掉横幅、且产出零个决策轮**，比今天诚实的停摆更糟。

        1. `_clear_stale_if_recovered()` —— 复用既有方法与既有文案；
        2. `note_bar_drop()` —— **登记一条待办**。语义完全贴切：确实有一根确认根落地了、确实没有
           为它起决策轮，这正是 `note_bar_drop` 的定义；
        3. `_maybe_catchup_wake()` —— **纯同步直调**。它不是旁路：`state != "running"` /
           `_deciding` / `_stale_degraded` / `_last_board is None` 四道守卫连同 `plan_catchup`
           的待办上限与 `should_skip` 全部写在那个函数**内部**，直调它 = 走完全部既有守卫。

        这三步之后，唤醒是否真的发生仍由既有唤醒器说了算（空仓 + 输入没真变 ⇒ `should_skip` ⇒
        不唤醒且不留悬空待办），**自愈层对「该不该交易」没有任何发言权**。
        """
        session = self._session
        await session._clear_stale_if_recovered()
        session._wake.note_bar_drop(time.time())
        session._maybe_catchup_wake()

    def _note_outcome(self, outcome: str, text: str) -> None:
        self.last_outcome = outcome
        self.last_outcome_text = text

    def _relink_receipt(self, *, relinked: bool, appended: int, fresh: bool,
                        last_ts: int | None) -> dict:
        """人工回执（失败也是 **200 + `ok: false`**，不映射 502——会话侧不抛 `OKXError`）。"""
        if fresh:
            message = f"已重建行情连接并补齐 {appended} 根 K 线，Agent 已恢复响应"
        elif relinked:
            message = ("已重建行情连接，但交易所仍未返回更新的 K 线；"
                       "该标的可能已停牌或交易所行情中断")
        else:
            message = "重建行情连接失败，请检查网络 / 代理后重试"
        return {"ok": bool(fresh), "instId": self._session.inst_id,
                "groupId": self._session.group_id, "relinked": bool(relinked),
                "appended": int(appended), "fresh": bool(fresh), "lastTs": last_ts,
                "message": message}

    async def _push_status(self, message: str) -> None:
        """带文案的 status 推送。载荷是**整份** `_status_payload()`（前端整体浅合并进成员断面，
        一份缺键的 status 会把只在全量断面里出现的键顺手清成 null）。"""
        if not message:
            return
        await self._session._push({"type": "status", "data": {
            **self._session._status_payload(), "message": message}})
