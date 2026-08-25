"""会话侧运行时门面（§4.10）—— 严格镜像 `TrackRecordRuntime` / `PriorRuntime` / `CostRuntime`。

`session.py` 已 2409 行（超 CLAUDE.md 的 800 行阈值），故它在本特性上只允许**接线**：
`__init__` 一个形参、`_dispatch` 两处 hook、`_build_decision_payload` 一行、
`_reflect_before_execute` 一行、`stop`/`pause` 各一行、`_status_payload` / `_build_run_metrics`
各一行。全部逻辑住在本包里。「超了就再下沉一层而不是再放宽阈值」是 `prior_service.py` 的成文先例。

未装配（`graph is None` / `rule is None` / `rule.enabled is False`）⇒ 以下方法**全部空转** ⇒
payload 无 `experience` 键 ⇒ `build_decision_prompt` 输出逐字节等于本特性合入前（不变式 I-2）。
"""
from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

from ...db import repositories as repo
from ...db.session import get_sessionmaker
from . import taxonomy as tax
from .disclosure import build_wire, experience_tier, format_counter, format_disputed
from .events import ContextFacts, EventIdentity, ExperienceEvent, build_event, freeze_open_context
from .retrieval import ContextSeed, RetrievalConfig, most_conflicting, retrieve
from .rules import ExperienceRule

if TYPE_CHECKING:                       # 只为类型标注；运行期不导入 `service`（它反向依赖本模块）
    from .service import SessionGraph

logger = logging.getLogger(__name__)

# 本会话在内存里最多留多少条已采事件（供蒸馏取素材）。会话跑很久也不会涨——蒸馏只看最近这些。
_SESSION_EVENT_CAP = 200

# ★ E-13（评审 P1-7）· 开仓语境槽的容量上限 + FIFO 淘汰。
#
# D-4 把退化匹配收紧为「恰好一个 `seq` 匹配才取」之后，「≥2 个 `seq` 匹配」的那些槽**永远不会
# 被弹出**，而 `_open_slots` 此前既无容量上限也无淘汰规则 ⇒ 长会话下单调增长。本仓库对每一个
# 会话内缓冲都有成文的有界纪律（`AttributionLedger._CAPACITY = 200` / `_SESSION_EVENT_CAP = 200` /
# 提交 `f48a714` 的 capped trade buffers），这里不能是例外。
# **每淘汰一个即 `harvest_gap += 1`**——被看见的丢弃，而不是无声增长的字典（用例 A-42）。
_OPEN_SLOT_CAP = 64

# 会话收尾把本次的采集缺口累加进去的 `app_settings` 键（★ E-8）。
HARVEST_GAP_KEY = "experience.harvest_gap_total"


class ExperienceRuntime:
    """经验层的会话门面：载入一次 / 逐轮 inject / 成交时增量 / 收尾 flush。"""

    def __init__(self, snapshot: "SessionGraph | None", rule: ExperienceRule | None, settings,
                 identity: EventIdentity | None = None) -> None:
        """装配会话门面。

        Args:
            snapshot: 启动期一次性取数的 `SessionGraph{graph, totals, as_of_ts}`。**图与库存总量
                必须出自同一次取数**（不变式 I-9 / 评审 F-3）：L0 页眉那两个整数是要喂给下真单的
                模型的，各取各的会让「来自 0 组有成交记录的自动交易」紧跟着三条铁律一起出现。
            rule: 经验模式规则（`resolve_experience` 的唯一产物）；`None` = 未装配。
            settings: 会话 `Settings`（已经过 `resolve_experience_settings` 派生，★ E-3）。
            identity: 事件溯源身份。
        """
        self._snapshot = snapshot
        self.graph = None if snapshot is None else snapshot.graph
        self.rule = rule
        self.settings = settings
        self.identity = identity or EventIdentity(group_id="", run_id=0, inst_id="", bar="")
        self._open_slots: dict[tuple[str, int], object] = {}
        self._pending: list[ExperienceEvent] = []
        self._session_events: list[ExperienceEvent] = []
        self._prev_ids: tuple[str, ...] | None = None
        self._last_wire: dict | None = None
        self._last_result = None
        self.events_harvested = 0
        # 「续跑接管的持仓」与「暂停期间被交易所平掉的持仓」两条路径不经 `_dispatch`、也不建开仓
        # 断面，故它们的往返在 v1 采不到。这两类**恰好偏向亏损样本**，不处理就是教科书式的幸存者
        # 偏差。v1 的处置是**不补采、但如实计数并露出**（§4.11 / R-15）——`status.experience`
        # 与设置中心概览条各有一处渲染落点，后端算了却看不见的观测量等于死码。
        self.harvest_gap = 0
        self._persisted_gap = 0        # 已累加进 `app_settings` 的部分（收尾可重入，不双计）
        self.injections = 0
        self._dirty = False            # 自上次 `take_dirty()` 以来是否有新事件落盘（D-3 的输入）

    # ---------- 装配判据 ----------
    @property
    def armed(self) -> bool:
        """本会话是否真的装配了经验层（读侧总闸）。"""
        return (self.rule is not None and self.rule.enabled
                and self.graph is not None and not self.graph.is_empty)

    @property
    def writing(self) -> bool:
        """本会话是否往经验资产回写（只读模式：用经验但不沉淀）。"""
        return self.rule is not None and self.rule.enabled and self.rule.write

    # ---------- 采集（挂在**已有** IO 点上，不新增 IO 面） ----------
    def note_open(self, trade: dict, *, seq: int, facts: ContextFacts) -> None:
        """开仓成交 ⇒ 冻结九轴语境（出场时体制可能已变，用出场语境标注入场决策的教训是错的）。"""
        if not self.writing:
            return
        slot = (str(trade.get("side") or ""), int(seq))
        if slot in self._open_slots:      # 部分成交：同一 epoch 不重新冻结
            return
        self._open_slots[slot] = freeze_open_context(facts, seq=seq, now_wall=time.time())
        while len(self._open_slots) > _OPEN_SLOT_CAP:      # ★ E-13：FIFO 淘汰，且**被看见**
            self._open_slots.pop(next(iter(self._open_slots)))
            self.harvest_gap += 1

    def facts(self, trade: dict, *, market_context: dict | None, wake_kind: str | None,
              horizon_profile_id: str | None) -> ContextFacts:
        """开仓瞬刻可读的九轴事实（装配下沉到本层，`session.py` 只留一次调用）。

        `symbol` / `bar` 取自 `self.identity`——**事件的溯源身份与检索的种子出自同一处**，
        这样「采进去的语境」与「读出来的语境」在结构上不可能对不上。

        `wake_kind` 必须是 `WAKE_KINDS` 的取值，**不是** `payload["trigger"]` 的
        `candle`/`interval` 二值枚举：两者混用会让整条 `wake` 轴永远匹配不上（§4.2 明写）。
        `confidence` 取 `explain.meta.confidence`（AI 开仓腿在 `_ai_explain` 里写的那一个）。
        """
        return ContextFacts(
            inst_id=self.identity.inst_id, bar=self.identity.bar,
            side=str(trade.get("side") or ""), market_context=market_context,
            wake_kind=wake_kind, horizon_profile_id=horizon_profile_id,
            confidence=((trade.get("explain") or {}).get("meta") or {}).get("confidence"))

    def note_close(self, trade: dict, meta: dict, trip) -> None:
        """平仓成交 ⇒ 组装事件入待落盘队列；`trip is None` ⇒ **不落事件**，改计 `harvestGap`。

        宁可缺一条并说明，也不编一条语境或读数是猜的事件——同 `settle_closed_offline`
        「绝不写 pnl=0.0」的成文纪律。

        Args:
            trade: 平仓成交（`_dispatch` 的 `side=="close"` 分支）。
            meta: `trade["explain"]["meta"]`；本层只读 `exitSource` / `wallTs`。
            trip: `SettledTrip` 本体，由调用方从 `CostRuntime.last_settled_trip()` 取（评审 P0-3）。
        """
        if not self.writing:
            return
        opened = self._take_slot(trip)
        if trip is None or opened is None:
            self.harvest_gap += 1
            return
        enriched = dict(meta or {})
        enriched["_costBasis"] = ("net" if getattr(self.settings, "ai_trade_net_pnl_enabled", False)
                                  else "gross")
        event = build_event(opened, trip, enriched, self.identity)
        if event is None:
            self.harvest_gap += 1
            return
        self._pending.append(event)
        self._session_events.append(event)
        del self._session_events[:-_SESSION_EVENT_CAP]
        self.events_harvested += 1

    def _take_slot(self, trip):
        """取出并清掉该 `(side, seq)` 的开仓语境槽；找不到 ⇒ None（调用方计 `harvestGap`）。"""
        if trip is None:
            return None
        slot = (str(getattr(trip, "side", "") or ""), int(getattr(trip, "seq", 0) or 0))
        opened = self._open_slots.pop(slot, None)
        if opened is not None:
            return opened
        # ★ D-4（评审 F-4）· 退化匹配收紧为「**恰好只有一个** `seq` 相同的槽才取」。
        # 反手腿 / 部分成交交错时可能有两个同 `seq` 不同 `side` 的槽，此前「取第一个」会默默
        # 用另一个 epoch 的语境标注这一笔往返——事件表里多一条**语境错误**的样本，且永远不报错。
        # 宁可缺一条并说明（调用方计 `harvestGap`），也不编一条语境是猜的事件（用例 A-36）。
        matches = [key for key in self._open_slots if key[1] == slot[1]]
        if len(matches) != 1:
            return None
        return self._open_slots.pop(matches[0])

    async def persist_pending(self) -> None:
        """把待落盘事件写进真源表（`INSERT OR IGNORE`）。**只做这一件事**（★ D-1 / 评审 F-1）。

        **绝不在这里物化**。此前的实现在落盘成功后 `await self._materialize_bounded()`，而它调的是
        `service.rebuild` = **每笔平仓一次全库重建**，跑在 `_dispatch` 这个「全部 executor 事件的
        唯一收口」上。两条后果都是静默的：① 热路径最坏挂 5 秒，期间该会话的 WS 扇出与后续成交
        落库全部排队；② `flush()` 挂在 `stop()` / `pause()` 的 await 链上，而 `manager.stop_group`
        **串行**遍历成员、客户端 `/stop` 的超时档是 `FAST_MS = 20_000` ⇒ 3 成员 × 5 秒 = +15 秒
        ⇒ **真实资金的停止按钮弹「本次操作结果未知」**。

        物化改由 manager 侧的 tracked 后台任务承担（§4.5 的四个触发点），本方法因此恒为毫秒级
        本地 IO。**best-effort**：任何失败只 warning——经验沉淀绝不允许阻断真实资金的成交落库。
        """
        if not self._pending:
            return
        rows = [event.row() for event in self._pending]
        self._pending = []
        try:
            async with get_sessionmaker()() as session:
                await repo.append_experience_events(session, rows)
        except Exception as exc:  # noqa: BLE001 —— 见 docstring
            # **落盘失败必须被看见**（§十二 P7 的第一个问题：这处 `except` 失败时用户从哪里知道）。
            # 这些事件已经从 `_pending` 里取走、且 `events_harvested` 早在 `note_close` 就加过了
            # ——不补这一笔，会话卡会说「已沉淀 N 条」而库里只有 N-k 条，差额无人可见。
            # 记进 `harvest_gap` 的语义恰好对：**这张表因此偏乐观了 k 笔**，与续跑 / 离线平仓
            # 那两条采集缺口是同一个口径，也共用 §7.1 与 §7.3 的同一处渲染落点。
            self.harvest_gap += len(rows)
            logger.warning("ai-trade experience events not persisted (%d lost): %s",
                           len(rows), exc)
            return
        self._dirty = True

    def take_dirty(self) -> bool:
        """取走「自上次取走以来有新事件落盘」这一位（取走即清）。

        调用方（`session._dispatch` 的 close 侧 hook）据此**同步、不 await** 地回调 manager，
        由 manager 决定要不要起一个后台物化任务（去抖 + 进程级单槽，§4.5）。
        """
        dirty, self._dirty = self._dirty, False
        return dirty

    def adopt_snapshot(self, snapshot: "SessionGraph") -> None:
        """后台物化完成后的**单点**引用替换（同一事件循环内无竞态）。

        这就是「经验是累积的，随着交易的进行不断更新」在**同一会话内**的兑现方式；没有它，
        用户看到的就是一个刚刚亏过同样的钱却毫无反应的 Agent（用例 A-38）。
        """
        if snapshot is None:
            return
        self._snapshot = snapshot
        self.graph = snapshot.graph

    async def flush(self) -> None:
        """暂停 / 停止的收尾：剩余事件落盘。**恒等于 `persist_pending()`，毫秒级本地 IO**。

        ★ 物化与 LLM 叙事蒸馏**都不在**这条 await 链上（评审 P0-1 / F-1）：`manager.stop_group`
        串行遍历成员，而客户端 `/ai-trade/{groupId}/stop` 的超时档是 `FAST_MS = 20_000` 且被判为
        写路径——一次 45 秒的 LLM 调用（或一次 5 秒的物化 ×3 成员）会让停止按钮弹出
        「本次操作结果未知，请刷新持仓/会话状态核对」。在真实资金的停止/平仓按钮上制造一次假
        警报，代价远大于本特性的全部收益。两者都由 manager 在 `_stop_session` / `pause_group`
        返回前 spawn 成 tracked 后台任务。
        """
        await self.persist_pending()

    async def persist_harvest_gap(self) -> None:
        """★ E-8（评审 P1-2）· 会话收尾把本次的采集缺口**累加**进 `app_settings`。

        `harvest_gap` 此前只是一个实例字段，会话一结束就归零；而 `GET /overview` 的
        `harvestGapTotal` 与 §7.1 的概览条都要显示它 ⇒ **那个数字恒为 0**，而它恰恰是用来告诉
        用户「有 K 笔往返没采到、这张表偏乐观」的。这是 I-8 的镜像形态：**有渲染落点、没有取数
        来源**。

        可重入：只累加「尚未累加过」的增量（`stop` 与 `pause` 两条路径都会走到这里）。
        best-effort：失败只 warning，绝不阻断真实资金的停止收尾。
        """
        delta = self.harvest_gap - self._persisted_gap
        if delta <= 0:
            return
        try:
            async with get_sessionmaker()() as session:
                prior = int((await repo.get_app_setting(session, HARVEST_GAP_KEY, 0)) or 0)
                await repo.set_app_setting(session, HARVEST_GAP_KEY, prior + delta)
        except Exception as exc:  # noqa: BLE001 —— 见 docstring
            logger.warning("ai-trade experience harvest gap not persisted: %s", exc)
            return
        self._persisted_gap = self.harvest_gap

    # ---------- 注入（决策轮零 IO，不变式 I-3） ----------
    def inject(self, payload: dict, *, seed: ContextSeed) -> None:
        """把 `experience` 装进本轮 payload；未装配 / 零条目 ⇒ **整键缺席**（不是 None）。

        ⚠️ **调用位置有硬顺序约束**：必须排在 `self._wake.inject(...)` **之后**——档位判定读
        `payload["contextDisclosure"]`，而那个键由 wake 层在 `_build_decision_payload` 的最后一段
        写入。排在它之前，`contextDisclosure` 恒缺席 ⇒ 档位恒 `full` ⇒ §4.8 的整套压缩静默失效，
        而那正是本需求点名要解决的「上下文爆炸」（评审 P1-3，用例 A-31 钉死）。
        """
        if not self.armed:
            return
        result = retrieve(self.graph, seed, RetrievalConfig.from_settings(self.settings))
        # 检索结果**无条件**留存：L2 的争议对在 `_suppress` 那一步就被踢出了 L0/L1，于是
        # 「本轮全部候选都是势均力敌的矛盾对」恰恰是 L0/L1 皆空的一种。早退时不留 `_last_result`
        # ⇒ 那些争议哪里都不会出现，而 §7.1 与执行轨迹上那两句「它们只会进入下单前的反思环节」
        # 就成了假话。
        prev_ids, self._prev_ids = self._prev_ids, result.ids()
        self._last_result = result
        if not result.l0 and not result.l1:
            self._last_wire = None      # 本轮一个字都没喂，`stats()` 不许还报上一轮的档位与字数
            return
        # 档位判定吃的是**上一轮**的 id 元组——`_prev_ids` 上面刚被换成本轮的，故这里必须用
        # 换出来的那一份，否则两者恒等 ⇒ 档位恒非 `full` ⇒ 语境真的变了也不会重新展开。
        tier = experience_tier(result, prev_ids, payload.get("contextDisclosure"))
        wire = build_wire(
            result, tier=tier,
            # ★ D-2（评审 F-3 / 不变式 I-9）· 这两个整数会被逐字渲染进 L0 页眉喂给要下真单的
            # 模型。此前它们是 `{"sessions": 0, "events": 本会话计数}` ⇒ 任何一次会话的**第一轮**
            # 模型收到的都是「（来自 0 组有成交记录的自动交易的 0 笔已结算往返）」**紧跟着三条
            # 铁律**——一段自相矛盾的证据。**prompt 里出现一个假数字，比不出现这一段更坏。**
            totals=dict(self._snapshot.totals),
            max_chars={"l0": int(self.settings.ai_trade_experience_l0_max_chars),
                       "l1": int(self.settings.ai_trade_experience_l1_max_chars)},
            graph_as_of_ts=self.graph.as_of_ts)
        payload["experience"] = wire
        self._last_wire = wire
        self.injections += 1

    def inject_round(self, payload: dict, *, wake_kind: str | None,
                     horizon_profile_id: str | None, position_side: str | None) -> None:
        """决策轮的注入入口：**种子装配 + `inject`**（`session.py` 只留一次调用）。

        ⚠️ 调用位置有硬顺序约束——见 `inject` 的 docstring：它必须排在
        `self._wake.inject(...)` **之后**，否则 `contextDisclosure` 恒缺席 ⇒ 档位恒 `full`
        ⇒ §4.8 的整套压缩静默失效（用例 A-31 钉死）。
        """
        if not self.armed:
            return
        self.inject(payload, seed=ContextSeed(
            axes=self.seed_axes(market_context=payload.get("marketContext"),
                                wake_kind=wake_kind,
                                horizon_profile_id=horizon_profile_id,
                                position_side=position_side),
            now_wall=time.time()))

    def inject_reflection(self, payload: dict, decision) -> None:
        """把 L2 反例装进反思 payload：**最相冲突的 1 条，没有则退到争议对**；都没有 ⇒ 整键缺席。

        §4.8 的 L2 写的是「1 条（最相冲突者**或**争议对）」，而争议对在 `_suppress` 那一步就被
        踢出了 L0/L1，`most_conflicting` 只在 L0∪L1 里找 ⇒ 少了这条回退，一对势均力敌的矛盾
        结论**哪里都不会出现**，而设置中心与执行轨迹上那两句「它们只会进入下单前的反思环节」
        就是产品在说假话。

        经验层能改变「这一单下不下」——这不是零影响（评审 P1-5）。可依赖的是**方向单侧**：
        反思官只能 `confirm` / `abort` / 缩仓（裁决 schema 二值 + `adjustedSizeFraction` 经
        `apply_reflection` 取 `min`），故最坏后果恒为「少做一笔」，永远不会是「多做、做反、做大」。
        """
        if not self.armed or self._last_result is None:
            return
        budget = int(self.settings.ai_trade_experience_reflection_max_chars)
        wire = self._counter_wire(decision, budget) or self._disputed_wire(budget)
        if wire is not None:
            payload["experienceCounter"] = wire

    def _counter_wire(self, decision, budget: int) -> dict | None:
        """L2 首选：与提议动作最相冲突的 1 条。"""
        counter = most_conflicting(self.graph, self._last_result,
                                   str(getattr(decision, "action", "") or ""))
        if counter is None:
            return None
        node = counter.node
        text = format_counter(
            {"text": node.label, "support": node.support, "tStat": node.stats.get("tStat")},
            budget)
        if not text:
            return None
        return {"kind": "counter", "key": node.key, "text": node.label,
                "support": node.support, "tStat": node.stats.get("tStat"), "rendered": text}

    def _disputed_wire(self, budget: int) -> dict | None:
        """L2 回退：两条势均力敌的矛盾结论。

        把一个未解决的冲突当成事实讲给交易员是产品在撒谎；讲给**风控复核官**才是对的
        （§4.8）——他的裁决空间只有 confirm / abort / 缩仓，多给一份争议只会让他更谨慎。
        """
        pairs = [{"a": a, "b": b} for a, b in (self._last_result.disputed or [])]
        if not pairs:
            return None
        labels = {key: {"text": node.label} for key, node in self.graph.nodes.items()}
        text = format_disputed(pairs, labels, budget)
        if not text:
            return None
        return {"kind": "disputed", "pairs": pairs[:2], "rendered": text}

    # ---------- 观测 ----------
    def distill_inputs(self) -> dict | None:
        """供 manager 侧后台蒸馏取素材；未装配 / 无事件 ⇒ `None`（不起那个任务）。

        `injectedKeys` 让蒸馏 prompt 能排除本会话已经喂过的经验——不然模型会把喂进去的话原样
        吐回来，那正是 R-1「自我强化的幻觉回路」的第一步。
        """
        if not self.writing or not self._session_events:
            return None
        return {
            "instId": self.identity.inst_id, "bar": self.identity.bar,
            "groupId": self.identity.group_id, "runId": self.identity.run_id,
            "injectedKeys": [item["key"] for item in (self._last_wire or {}).get("l0", [])],
            "trips": [{"seq": e.seq, "side": e.side, "netBps": e.net_bps,
                       "costBps": e.cost_bps, "holdSeconds": e.hold_seconds,
                       "exitSource": e.exit_source, "context": e.context}
                      for e in self._session_events[-40:]],
        }

    def stats(self) -> dict | None:
        """`status.experience` / `aiStats.experience` 共用的只读 wire；未装配 ⇒ `None`。

        `harvestGap` 与 `graphAsOfTs` 是两个**诚实性键**：前者让「有 N 笔往返没采到」这件事
        被看见（§4.11），后者让「该会话用的是哪一刻的经验库」可核对（§7.1 预演状态行的对照量）。
        两者在 §7.3 的 `ExperienceCard` 各有明确渲染位置——后端算了却没有呈现落点的观测量等于死码。
        """
        if self.rule is None or not self.rule.enabled:
            return None
        wire = self._last_wire or {}
        return {**self.rule.wire(),      # enabled / scope / write 的唯一镜像点，绝不各抄一份
                "tier": wire.get("tier"), "l0": len(wire.get("l0") or []),
                "l1": len(wire.get("l1") or []), "injections": self.injections,
                "chars": self._chars(), "eventsHarvested": self.events_harvested,
                "harvestGap": self.harvest_gap,
                "graphAsOfTs": self.graph_as_of_ts}

    @property
    def graph_as_of_ts(self) -> int:
        """本会话正在使用的经验库快照时刻（§7.1 预演状态行的对照量）。"""
        return 0 if self._snapshot is None else int(self._snapshot.as_of_ts)

    def _chars(self) -> int:
        """本轮真实喂入的字符数（渲染一次即得，与模型看到的那一份同源）。"""
        from .disclosure import format_experience

        return len(format_experience(self._last_wire)) if self._last_wire else 0

    def seed_axes(self, *, market_context: dict | None, wake_kind: str | None,
                  horizon_profile_id: str | None, position_side: str | None) -> dict:
        """决策轮的种子（**7 轴**；已有持仓时补第 8 轴 `side`，评审 P1-2）。

        `side`（本轮要开多还是开空）与 `confidence` 此刻都还不存在——它们要到 `decide()` 返回之后
        才有。已有持仓时把 `side` 置为**当前持仓方向**是合法的：那是一个已发生的事实，不是提议。
        """
        market = market_context or {}
        axes = {
            tax.AXIS_SYMBOL: self.identity.inst_id,
            tax.AXIS_BAR: self.identity.bar,
            tax.AXIS_REGIME: tax.fold(tax.AXIS_REGIME, market.get("primary")),
            tax.AXIS_VOLATILITY: tax.fold(tax.AXIS_VOLATILITY, market.get("volatility")),
            tax.AXIS_EVENT: tax.fold(tax.AXIS_EVENT, market.get("event")),
            tax.AXIS_WAKE: tax.fold(tax.AXIS_WAKE, wake_kind),
            tax.AXIS_HORIZON: tax.fold(tax.AXIS_HORIZON, horizon_profile_id or "none"),
        }
        if position_side in ("long", "short"):
            axes[tax.AXIS_SIDE] = position_side
        return axes
