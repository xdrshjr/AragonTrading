"""每分组一份 `IntelRuntime` + 进程内快照注册表（ai-trade-market-intel §3.5 / §3.6.1）。

**热路径不查库**（§3.0 第 3 条不可越过的方向）：决策 prompt 里的情报段取自本模块的进程内快照，
由采集 / 整理轮结束、用户经 REST 改动条目、以及 `POST /seen` 之后刷新。`session.snapshot()` 是
一个同步 `def`，`AiTradeIntelSummary` 的 `counts` / `unread` / `dailyUsed` / `nextDueTs` /
`degrade` 却全部来自 DB——两条要求会在实现时正面撞车，故这里**缓存两份**（评审 P1-3）：

| 快照 | 同步取数 | 消费者 |
|---|---|---|
| `IntelDigest` | `intel_digest_for(inst_id)` | `AiTradeSession` 装决策 payload |
| `IntelSummary` | `intel_summary_for(inst_id)` | `session.snapshot()` / `push_intel()` |

**未装配的会话不起任务，但仍要看得见**（E-6 第三条纪律 + 评审 P1-3）：`provider is None` 时
`scheduler.start` 不建任何后台任务、不开任何 DB 会话——一个持着锁、永远什么都采不到的任务只有
害处。但如果连 runtime 都不注册，`no_provider` 这个降级码就**永远不会**经 WS / `snapshot()` 到达
前端，§3.8.2 那条「按钮染警示色」在**最该出现的那种降级**上恒不出现。故此时仍注册一个
**runtime 壳**（`enabled=False`），它只持一份静态降级 summary，不起任务、不查库。
"""
from __future__ import annotations

import asyncio
import itertools
import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace

from . import arming
from . import service
from .arming import (  # noqa: F401 —— 原样重新导出，见下方注释
    arm_error_for,
    clear_arm_error,
    record_arm_error,
)
from .arming import ARM_NOT_RUNNING, ArmState  # noqa: F401 —— 原样重新导出
from .session_rule import IntelSessionRule
from .binding import (
    alert_pending_ttl_s,
    binding_wire,
    build_binding,
    live_pin,
    pin_item_id,
    set_pin,
)
from .service import IntelDeps, RoundOutcome
# `static_summary` 与 `read_summary` 同为 `AiTradeIntelSummary` 的装配点，故同住 `summary.py`
# （本文件在 session-binding 轮越 800 行，处置是「再下沉一层」而不是放宽阈值）。
from .summary import static_summary as _static_summary  # noqa: F401

logger = logging.getLogger(__name__)

# 进程级注册表。**模块级**而不是实例级：`get_ai_trade_manager()` 是进程级单例，模块级让
# 「runtime 活得比 manager 实例久」这件事在类型上就不可能发生（镜像 `experience/scheduler.py`）。
_RUNTIMES: dict[str, "IntelRuntime"] = {}
_BY_INST: dict[str, "IntelRuntime"] = {}
# 进程级单调重装序号（`itertools.count`）：只增不减、**不按分组分区**——序号只用于比大小，
# 跨分组共用一个计数器不会出错且少一层字典。
_ARM_SEQ = itertools.count(1)
# `ARM_NOT_RUNNING` / `arm_result` / `arm_result_of` **已外移**到 `arming.py`（本文件在
# session-binding 轮再次越 800 行，处置同 `summary.py`：纯搬运、零语义变化）；`ARM_NOT_RUNNING`
# 在上面的 import 段原样重新导出，故既有调用点一个字节不改。


class IntelRuntime:
    """一个 AI 自动交易分组的情报流水线运行时。

    分组内**串行**（`asyncio.Lock`）：N 个标的排队跑，理由与 `experience` 的进程级单槽相同——
    并发只会同时撞检索源限流与 LLM 限流（R-10）。
    """

    def __init__(self, group_id: str, members: Iterable[str], deps: IntelDeps, *,
                 notify: Callable[[str, dict], None] | None = None, enabled: bool = True,
                 notify_alert: Callable[[str, object], None] | None = None):
        self.group_id = group_id
        self.members: list[str] = list(members)
        self.deps = deps
        self.enabled = bool(enabled)
        self.paused = False
        self._notify = notify or (lambda inst_id, summary: None)
        # actionability 轴 A 的第三跳（**同步回调，逐行镜像 `_notify`**）。缺省 no-op ⇒ 未接线
        # 的运行时（回测车道、离线用例）整条告警链路不存在。
        self._notify_alert = notify_alert or (lambda inst_id, handoff: None)
        self._lock = asyncio.Lock()
        self._digest: dict[str, dict] = {}
        self._digest_ids: dict[str, frozenset[int]] = {}
        # ★ 评审 P1-1：置顶是**状态**，不是形参。`refresh` 有四个调用点（`_after_round` /
        # `invalidate`（REST 每一次写，含「标记已读」）/ 停调度收尾 / `scheduler._loop`），
        # 只在其中一个传参等于「任何一次标记已读都会把置顶抹掉」——而 `_alert` 早已把
        # `in_digest=True` 戳进台账与唤醒句，于是 prompt 说「已在下方情报段中列出」而它并不在。
        # 值域 `inst_id -> (item_id, expire_wall)`；窗口复用告警自己的唤醒窗口，不造第二个常数。
        self._pinned: dict[str, tuple[int, float]] = {}
        # 轴 C：最近一次决策轮真正渲染出来的读数（由会话经 `binding.note_disclosure` 回填）。
        self._last_round: dict[str, dict] = {}
        self._summary: dict[str, dict] = {}
        self._in_flight: set[str] = set()
        # 一轮的**收尾段**（`_after_round`）跑在锁外、且在 `_in_flight` 被清掉之后，故它需要
        # 自己的计数器；`_await_round_settled` 靠它才等得到那次快照刷新与告警交接（实施发现 I-3）。
        self._tail = 0
        # arm-state 轴 A / 轴 B：装配状态的唯一真值 + 重装序号 + 配置指纹 + 拆卸标志。
        # **全部私有**，外部一律经 `arm` / `arm_pending` / `arm_fingerprint` 三个只读 property
        # 读（评审 P1-6：公私成员名对不上，实现出来就是 `AttributeError`）。
        self._arm: ArmState = arming.arm_of_deps(deps)
        self._arm_seq = 0
        self._arm_fingerprint: tuple = ()
        self._retired = False
        for inst_id in self.members:
            # 首帧兜底：`scheduler._loop` 起步就会 refresh 一次，但 `snapshot()` 可能更早被调到。
            # arm 四键由 `summary_for` 在**出口**统一打戳，故这里只剩 `degrade` 需要兜一手：
            # `arm_wire` 在 armed 时**刻意不写** `degrade`（那个键要留给一次真实的轮内降级），
            # 于是一个装配成功的运行时若沿用未装配 seed 的 `degrade`，弹窗会在最初那一瞬显示
            # 「尚未配置情报检索源」并给出一个用户根本不需要点的设置入口。
            seed = _static_summary(inst_id, code=self._arm.reason or "no_provider")
            if self._arm.armed:
                seed["degrade"] = None
            self._summary[inst_id] = seed

    # ---------- 同步取数（决策热路径与 snapshot 都走这里） ----------
    def digest_for(self, inst_id: str) -> dict | None:
        return self._digest.get(inst_id)

    def summary_for(self, inst_id: str) -> dict | None:
        """缓存里的那一份 + 两个出口瞬态。

        arm 四键在**出口**统一打戳：缓存里的那一份可能是上一次装配期写的，而 `rearm` 之后它
        必须立刻说新话。这也是 §3.0 第 3 条「同一列只有一个人写」的落点。

        session-binding 轴 C 的 `binding` 块**同址打戳**（同一条理由 + 同一个写点
        `binding.binding_wire`）：三条读路径（WS `intel` 事件、`snapshot.intel`、
        `GET /intel/{instId}`）因此天然同形，不存在「WS 一份、REST 一份」的分叉。
        """
        summary = self._summary.get(inst_id)
        if summary is None:
            return None
        return {**summary, "inFlight": inst_id in self._in_flight,
                **arming.arm_wire(self._arm),
                "binding": binding_wire(build_binding(self, inst_id))}

    def last_round_for(self, inst_id: str) -> dict | None:
        """`binding.lastRound` 的取数点；该标的还没渲染过任何一轮 ⇒ ``None``。"""
        return self._last_round.get(inst_id)

    def note_last_round(self, inst_id: str, wire: dict) -> None:
        """会话回填「上一轮情报段真正渲染成什么样」（`binding.note_disclosure` 的落点）。

        **同步、不查库、不排任务**：它跑在决策 payload 装配那一行之后，一次 await 就会让真实
        资金决策的时延取决于一块观测数据。
        """
        if inst_id in self._summary:
            self._last_round[inst_id] = dict(wire)

    @property
    def _pin_enabled(self) -> bool:
        """轴 B-3 的 L3 总闸（告警置顶）。关 ⇒ `pin_ids` 恒空 ⇒ 注入排序逐字节回退。"""
        return bool(getattr(self.deps.settings, "ai_trade_intel_alert_pin_enabled", True))

    def _set_pin(self, inst_id: str, item_id: int) -> None:
        """**唯一**写点（薄转发到 `binding.set_pin`）：`_after_round` 在 `refresh` 之前调它。"""
        set_pin(self._pinned, inst_id, item_id,
                now=self.deps.now(),
                ttl_s=alert_pending_ttl_s(self.deps.settings))

    def _live_pin(self, inst_id: str) -> int:
        """**唯一**读点（薄转发）：过期即自动为空，故 `refresh` 的四个调用点一字节不改。"""
        return live_pin(self._pinned, inst_id, now=self.deps.now())

    @property
    def lock(self) -> asyncio.Lock:
        return self._lock

    # ---------- 装配状态：三个只读 property + 一个置位方法（写点全部收敛在类内） ----------
    @property
    def arm(self) -> ArmState:
        return self._arm

    @property
    def arm_pending(self) -> bool:
        """是否有一次重装排在队里等这一轮跑完。

        **不是**一个独立的自变量：它就是 ``self._arm.reason == "arm_pending"``。把它做成第二个
        字段的那一刻，`read_summary`（不传 pending）与缓存那条打了戳的通路就会在同一份 wire 上
        说两句相反的话（评审 P1-8）。这里让它退化成 `_arm` 的一个投影，物理上杜绝分叉。
        """
        return self._arm.reason == "arm_pending"

    @property
    def arm_fingerprint(self) -> tuple:
        return self._arm_fingerprint

    @property
    def busy(self) -> bool:
        """这一轮真的收完尾了吗。

        `_in_flight` **不够**：`_run` 的 `async with self._lock` 在 `await self._after_round(...)`
        **之前**就退出了，而 `_in_flight.discard` 又在那把锁里的 `finally` 上——于是快照刷新与
        告警交接这两件事发生在「`_in_flight` 已空」之后。只看 `_in_flight` 的等待会立刻返回，
        然后 `scheduler.cancel` 把它们静默丢掉（实施发现 I-3）。
        """
        return bool(self._in_flight) or self._tail > 0

    def mark_arm_pending(self) -> None:
        """把这个运行时标成「重装排队中」（`rearm_group` 的超时支路唯一的置位入口）。

        **不动 `enabled`、不动 `deps`**：旧配置继续跑，只是界面上多一句「设置已保存，当前这一轮
        情报跑完后自动生效」。
        """
        self._arm = arming.arm_of_code("arm_pending")

    async def rearm(self, deps: IntelDeps, arm: ArmState, seq: int,
                    fingerprint: tuple) -> bool:
        """就地换掉这个运行时的配置。**同一实例、同一把锁、同一份缓存。**

        Args:
            deps / arm: `_build_deps` 在**锁外**造好的新依赖与新状态（造它可能失败，而一次失败
                的重装绝不允许把一条正在正常工作的流水线弄停——§3.0 第 2 条）。
            seq: 单调递增的重装序号。**只应用 `seq >= self._arm_seq` 的那一次**：用户连点两个
                开关会排出两次后台重装，晚发起的那一次必须赢。
            fingerprint: 这份配置的指纹。★ 它**必须和 `deps` 在同一把锁内、同一时刻**赋值
                （评审 P1-9）：让调用方在 `wait_for` **之前**先写指纹，两个 PUT 在
                `resolve_intel_settings` 那次 `await` 上交错时就会出现「B 的配置应用了、A 的
                指纹后写进去了」——运行时跑着 B、指纹说是 A，此后任何一次把设置改回 A 的保存都
                被 `changed=False` 短路，**A 永远装不上，且不报错**。指纹是「此刻真的在跑的那份
                配置」的标签，谁应用谁贴。

        Returns:
            是否真的应用了。`retired`（分组已拆卸）或 `seq` 过期 ⇒ False。

        ⚠️ **锁内不得有任何 await。** 五次赋值原子完成，故一次被 `wait_for` 取消的等待要么完全
        没应用、要么完全应用，不存在中间态。
        """
        if self._retired:
            return False
        async with self._lock:                      # 等在飞轮跑完；绝不打断它
            if self._retired or seq < self._arm_seq:
                return False
            self.deps, self._arm, self._arm_seq = deps, arm, seq
            self._arm_fingerprint = fingerprint
            self.enabled = arm.armed
        await _sync_scheduler(self)                 # 起 / 停调度循环（延迟导入，同 invalidate）
        return True

    def retire(self) -> None:
        """分组拆卸时置位。此后 `rearm` / `refresh` 全部是 no-op。"""
        self._retired = True

    # ---------- 刷新 ----------
    async def refresh(self, inst_id: str, *, degrade_code: str = "",
                      reason: str = "", rejected: int = 0) -> dict | None:
        """重算并缓存该标的的两份快照，然后 `notify` 一次。

        Args:
            inst_id: 目标标的。
            degrade_code: **本轮**的降级码（不是库存的属性，故不由 `build_snapshot` 自己算）。
            reason / rejected: 降级文案的占位。

        `retryInS` 在这里**用刷新后的 `nextDueTs`** 算：本轮已经把新的到期时刻写进库了，用刷新前
        缓存里的那个旧值算出来的倒计时会指向一个已经过去的时刻——一个走到 0 却什么都没发生的
        倒计时，比不显示倒计时更坏。
        """
        if self._retired:
            return self._summary.get(inst_id)
        if not self.enabled:
            if inst_id not in self._summary:
                # 兜底也必须走同一个写点：**绝不**把一份没有 arm 四键的裸字典推出去。前端读到
                # `undefined` 会把「没装上」画成「一切正常」（E-18；历轮四次踩过同一个坑）。
                self._summary[inst_id] = _static_summary(
                    inst_id, code=self._arm.reason or "no_provider")
            summary = self.summary_for(inst_id)
            self._notify(inst_id, summary)
            return summary
        try:
            # 轴 B-3：置顶从**状态**里取（`_live_pin` 过期即为空）而不是从形参，见 `_set_pin`。
            snapshot = await service.build_snapshot(self.deps, inst_id, arm=self._arm,
                                                    pin_item_id=self._live_pin(inst_id))
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 —— 快照是加法；失败只放弃这一次刷新
            logger.warning("ai-trade intel snapshot failed for %s: %s", inst_id, exc)
            return self._summary.get(inst_id)
        self._digest[inst_id] = snapshot["digest"]
        # ★ 评审 P0-3：`in_digest` 的判据。`cite=False` 时 wire 上没有 `refs`，而「这一条到底
        # 有没有进 prompt」仍然必须答得出来——故定稿时把收进来的 id 集合一并记下，绝不在别处
        # 从渲染文本里反查标题（那会在标题相同的两条上判错，且不报错）。
        self._digest_ids[inst_id] = frozenset(snapshot.get("digestItemIds") or ())
        summary = snapshot["summary"]
        if degrade_code:
            due = summary.get("nextDueTs")
            retry_in = max(0, int(due - self.deps.now())) if due else None
            summary["degrade"] = service.degrade_wire(
                degrade_code, retry_in_s=retry_in, reason=reason or "未知原因", n=rejected)
        self._summary[inst_id] = summary
        self._notify(inst_id, self.summary_for(inst_id) or summary)
        return summary

    async def run_collect(self, inst_id: str, *, force: bool = False) -> RoundOutcome | None:
        """跑一轮采集并刷新快照（分组内串行）。"""
        return await self._run(inst_id, "collect",
                               service.run_collect_round(self.deps, inst_id, force=force))

    async def run_curate(self, inst_id: str) -> RoundOutcome | None:
        """跑一轮整理并刷新快照（分组内串行）。"""
        return await self._run(inst_id, "curate",
                               service.run_curate_round(self.deps, inst_id))

    async def run_verify(self, inst_id: str, *,
                         only_item_id: int | None = None) -> RoundOutcome | None:
        """跑一轮核查并刷新快照（closed-loop 轴 C）。

        **逐行镜像 `run_curate`**：同一把 `_lock`、同一个 `_in_flight`、同一个 `_run` 护栏、同一个
        `_after_round`——**不新建任何任务集、不新建任何循环**（E-13）。快照刷新由 `_after_round`
        承担，编排里不得再刷一次（评审 P2-7）。

        Args:
            inst_id: 目标标的。
            only_item_id: 只核查这一条（`POST /intel/items/{itemId}/verify`，绕过候选过滤）。
        """
        from .service_verify import run_verify_round   # 延迟导入：它反向依赖 service 的公开面

        return await self._run(inst_id, "verify",
                               run_verify_round(self.deps, inst_id,
                                                only_item_id=only_item_id))

    async def _run(self, inst_id: str, kind: str, coro) -> RoundOutcome | None:
        """两个轮入口共用的护栏：串行锁 + 在飞标记 + **绝不抛**。

        这一层不是防御性噪音：这些轮跑在 tracked 后台任务里，一个逃出去的异常只会变成
        「Task exception was never retrieved」——既不会自愈，也不会有任何人看见。情报是加法
        流水线，它的失败必须收敛成一个降级码，而不是一条无人读的日志。
        """
        if not self.enabled or self.paused:
            coro.close()
            return None
        async with self._lock:
            self._in_flight.add(inst_id)
            try:
                outcome = await coro
            except asyncio.CancelledError:
                raise                       # 关停信号：绝不吞
            except Exception as exc:  # noqa: BLE001 —— 见 docstring
                logger.warning("ai-trade intel %s round crashed for %s: %s", kind, inst_id, exc)
                return None
            finally:
                self._in_flight.discard(inst_id)
        if outcome is None:
            return None
        # 收尾段跑在锁**外**（快照刷新 + 告警交接），故 `_in_flight` 已经空了。`_tail` 是
        # `_await_round_settled` 唯一等得到它的抓手——少这一层，一次「关掉总闸」会把这一轮的
        # `_alert` 告警交接静默丢掉，症状是「今天为什么一次都没告警」恰好在这一次没有答案。
        self._tail += 1
        try:
            await self._after_round(inst_id, outcome)
        finally:
            self._tail -= 1
        return outcome

    async def _after_round(self, inst_id: str, outcome: RoundOutcome) -> None:
        handoff = getattr(outcome, "alert", None)
        # ★ 轴 B-3：把「触发了本轮的那条情报」钉进即将定稿的 digest。判据在 `refresh` **之前**
        # 就已经在手（`outcome.alert`），故不必调换 `refresh` / `_alert` 的顺序（那个顺序是
        # actionability 评审 P0-3 用血换来的，不许动）。
        self._set_pin(inst_id, pin_item_id(handoff, enabled=self._pin_enabled))
        await self.refresh(inst_id, degrade_code=outcome.degrade_code,
                           reason=outcome.error_text, rejected=outcome.rejected)
        self._alert(inst_id, handoff)

    def _alert(self, inst_id: str, handoff) -> None:
        """把一条待处理告警交给该标的的交易会话（actionability §3.1.3 的第二跳）。

        **顺序不可换**：必须排在 `refresh` **之后**。两件事都依赖那份**刚刚重算好**的 digest：

        - `ref` 要从它的 `refs` 反查（``item_id → "I03"``），这样 prompt 里的唤醒句能说「本轮由
          情报 [I03] 触发」，而模型可以用同一个编号把它引用回来（复用 7e2d5be 的既有编号表，
          不发明第二套标识）；`cite_enabled=False` 时 `refs` 整键缺席 ⇒ `ref` 为 ``""``。
        - ★ `in_digest` 要从它的 `lines` 反查（评审 P0-3）。「这条情报有编号」与「这条情报真的
          出现在 prompt 里」**不是同一件事**：`_TIER_RANK` 让 ultra 排在最后、`prompt_max_chars`
          默认只装得下 ~5–6 行，长期层一旦攒到 6 条 active，告警条目在**稳态上**就进不了
          prompt；`inject_enabled=False` 时整段情报都不存在。用「有 ref」冒充「在 prompt 里」，
          会把一句确定错误的话逐字写进一次真实资金决策的 prompt 首行。

        算出来的这两样还要**补写进台账**（`stamp_intel_alert_digest`，台账三个写点里的第二个）：
        建行那一刻它们还不知道，而动态页签的 `[Ixx]` 前缀与「未进入情报段」都只读它们。

        **未装配的 runtime 壳上是 no-op**：那种 runtime 不起任何后台任务，也不该产生告警。
        """
        if handoff is None or not self.enabled:
            return
        digest = self._digest.get(inst_id) or {}
        item_id = int(handoff.candidate.item_id)
        refs = digest.get("refs") or {}
        ref = next((token for token, key in refs.items() if int(key) == item_id), "")
        # ★ `cite` 关时 `refs` 整键缺席，而「这一条到底在不在 prompt 里」仍然必须答得出来。
        # 判据取自 `build_digest_and_ids` 在**定稿那一刻**一并交出来的 id 集合，**不是**从
        # 渲染文本里反查标题（那会在两条同题条目上判错，而且不报错），也**不是**「有没有 ref」。
        in_digest = item_id in (self._digest_ids.get(inst_id) or frozenset())
        # 两者也必须**落进台账**：它们在建行那一刻还不知道（那时 digest 还是上一轮的），而
        # 动态页签的「未进入情报段」与 `[Ixx]` 前缀都读它们。不补这一跳，两列就是「有列、有
        # 读点、没有写点」——界面会给每一条告警挂一句关于数据的假话。
        # 写法逐行照抄同文件的 `note_alert_outcome`：同步上下文里 `scheduler.schedule(...)`
        # 一个协程，交给既有 tracked 任务集（已被 `_shutdown_steps` 第 7 步 drain）。
        from . import scheduler

        if handoff.alert_id:
            scheduler.schedule(_stamp_alert_digest(self, int(handoff.alert_id), ref=ref,
                                                   in_digest=in_digest))
        self._notify_alert(inst_id, replace(handoff, ref=ref, in_digest=in_digest))


# ---------------- 注册表 ----------------
def register(runtime: IntelRuntime) -> None:
    """装配一个分组的情报运行时（`start_group` / `resume_group` 之后）。"""
    _RUNTIMES[runtime.group_id] = runtime
    for inst_id in runtime.members:
        _BY_INST[inst_id] = runtime


def unregister(group_id: str) -> None:
    """拆卸（`stop_group` / `end_group`）。**按身份比对再删** `_BY_INST`：同一标的可能已经属于
    下一个分组了。"""
    runtime = _RUNTIMES.pop(group_id, None)
    arming.clear_arm_error(group_id)      # 残留会让下一个分组读到上一个分组的失败原因
    if runtime is None:
        return
    runtime.retire()
    for inst_id in list(_BY_INST):
        if _BY_INST.get(inst_id) is runtime:
            del _BY_INST[inst_id]


def all_runtimes() -> list[IntelRuntime]:
    return list(_RUNTIMES.values())


def runtime_for(inst_id: str) -> IntelRuntime | None:
    return _BY_INST.get(inst_id)


def get_runtime(group_id: str) -> IntelRuntime | None:
    return _RUNTIMES.get(group_id)


def intel_digest_for(inst_id: str) -> dict | None:
    """决策 payload 的 `intel` 键取数。**同步、读进程内缓存、不触 DB**。

    未装配 / 零条目 / 尚未成功采集过 ⇒ ``None`` ⇒ payload 整键缺席 ⇒ `build_decision_prompt`
    输出与本特性合入前**逐字节相同**（G-6）。
    """
    runtime = _BY_INST.get(inst_id)
    if runtime is None:
        return None
    digest = runtime.digest_for(inst_id)
    return digest if digest and digest.get("lines") else None


def intel_summary_for(inst_id: str) -> dict | None:
    """`session.snapshot()` 的 `intel` 键取数。**同步、读进程内缓存、不触 DB**。"""
    runtime = _BY_INST.get(inst_id)
    return runtime.summary_for(inst_id) if runtime is not None else None


def note_alert_outcome(inst_id: str, alert_id: int, *, outcome: str, run_id: int = 0,
                       round_no: int = 0) -> None:
    """回写一条告警的结果（台账的第二个写点；**同步函数，绝不是协程**）。

    评审 P1-10：「结账」要写一次库，而 `session.note_intel_alert` 与 `_maybe_intel_wake` 的
    清理分支**都在同步上下文里**。修法逐行照抄同文件里的 `invalidate`——同步函数体内
    `scheduler.schedule(...)` 一个协程，交给既有的 tracked 任务集（它已被 `_shutdown_steps`
    第 7 步 drain，故本轮**不新增任何关停步骤**）。

    Args:
        inst_id: 目标标的（只用来定位 runtime 与它的 sessionmaker）。
        alert_id: 台账行 id（来自 `AlertHandoff.alert_id`）。
        outcome: `taxonomy.ALERT_OUTCOMES` 的元素。
        run_id / round_no: 只在 ``outcome == "woke"`` 时有意义。

    Note:
        运行时不在（会话已停、回测车道）⇒ **no-op**。一条留在库里 `outcome == ""` 的行由动态
        页签按 TTL 渲染成 `expired_no_response`——渲染期判定，**不回写**（一个只为了让界面好看
        而产生的写操作会与真正的回写竞态）。
    """
    runtime = _BY_INST.get(inst_id)
    if runtime is None or not alert_id:
        return
    from . import scheduler

    scheduler.schedule(_write_alert_outcome(runtime, int(alert_id), outcome=outcome,
                                            run_id=int(run_id), round_no=int(round_no)))


async def _stamp_alert_digest(runtime: "IntelRuntime", alert_id: int, *, ref: str,
                              in_digest: bool) -> None:
    """`_alert` 的写段。**绝不抛**：台账是加法，失败只放弃这一次补写。"""
    from ...db import repositories as repo

    try:
        async with runtime.deps.sessionmaker()() as db:
            await repo.stamp_intel_alert_digest(db, alert_id, ref=ref, in_digest=in_digest)
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 —— 见 docstring
        logger.warning("ai-trade intel alert digest stamp failed (#%s): %s", alert_id, exc)


async def _write_alert_outcome(runtime: "IntelRuntime", alert_id: int, *, outcome: str,
                               run_id: int, round_no: int) -> None:
    """`note_alert_outcome` 的写段。**绝不抛**：台账是加法，失败只放弃这一次回写。"""
    from ...db import repositories as repo

    try:
        async with runtime.deps.sessionmaker()() as db:
            await repo.mark_intel_alert_outcome(db, alert_id, outcome=outcome,
                                                outcome_ts=int(runtime.deps.now()),
                                                run_id=run_id, round_no=round_no)
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 —— 见 docstring
        logger.warning("ai-trade intel alert outcome write failed (#%s): %s", alert_id, exc)


def invalidate(inst_id: str) -> None:
    """通知运行时「这个标的的库存变了，请重算快照」。运行时不在时是 no-op。

    REST 的每一次写（新增 / 编辑 / 移层 / 置顶 / 静音 / 删除 / 标记已读）都要调它，否则用户改完
    之后决策 prompt 里还是旧的那一份，而**这件事不会报任何错**。

    延迟导入 `scheduler`：它反向依赖本模块（要遍历注册表），模块级互相 import 会成环。
    """
    runtime = _BY_INST.get(inst_id)
    if runtime is None:
        return
    from . import scheduler

    scheduler.schedule(runtime.refresh(inst_id))


async def build_group_runtime(*, group_id: str, members: Iterable[str], settings,
                              sessionmaker, provider_cfg, make_client,
                              bar: str = "15m", change_24h_pct=None,
                              notify: Callable[[str, dict], None] | None = None,
                              notify_alert: Callable[[str, object], None] | None = None,
                              session_rule: IntelSessionRule | None = None
                              ) -> "IntelRuntime":
    """装配一个分组的情报运行时（`manager.start_group` / `resume_group` 的唯一入口）。

    **判定全在这里，manager 只做薄转发**：`manager.py` 实读 1952 行，早已超过 CLAUDE.md 的 800
    行阈值——「超了就再下沉一层而不是再放宽阈值」是 `prior_service.py` 的成文先例。

    Args:
        group_id / members / bar: 分组事实。
        settings: 会话生效的 `Settings`（本函数会叠加库内行产出运行时真正读到的那一份）。
        sessionmaker: `get_sessionmaker`（**函数本身**，不是会话）。
        provider_cfg: 当前生效的 LLM provider 配置。
        make_client: 造 LLM client 的工厂（**调用方必须传自己模块里的那一份**，见 `intel/llm.py`）。
        change_24h_pct: 同步取「近 24 小时涨跌」的回调；取不到回 None ⇒ 该行省略。
        notify: `(inst_id, summary) -> None`，把 summary 推给会话的 WS 出口。
        notify_alert: `(inst_id, AlertHandoff) -> None`，把一条待处理告警交给会话（**同步**，
            绝不 await——情报后台任务里出现一次对会话的 await，就等于把「停止按钮」的响应时间
            与情报轮绑在一起）。
        session_rule: 本次会话对情报层的采信约定（session-binding 轴 A）。**必须是一个独立的、
            显式的 keyword 形参**，在 `resolve_intel_settings` **之后**才与运维总闸合取——
            把它编码成 `Settings.ai_trade_intel_inject` 会被 `INTEL_SETTING_BRIDGE` 里那一行
            库内行静默覆盖，而且不报错（spec §3.1 A-2 的陷阱）。``None`` ⇒ 默认规则
            ⇒ 逐字节回退（由进程级总闸独裁）。

    Returns:
        `IntelRuntime`。总闸关 / 未配置检索源 ⇒ `enabled=False` 的**壳**（不起任务、不查库，
        但仍持一份静态降级 summary，使 `no_provider` 在界面上可见——评审 P1-3）。
    """
    async with sessionmaker()() as db:
        derived = await service.resolve_intel_settings(db, settings)
        api_key = await service.resolve_api_key(db, derived)
    deps, arm = _build_deps(
        settings=derived, api_key=api_key, provider_cfg=provider_cfg, make_client=make_client,
        ident=GroupIdentity(sessionmaker=sessionmaker, group_id=group_id, bar=bar,
                            change_24h_pct=change_24h_pct,
                            session_rule=session_rule or IntelSessionRule()))
    return IntelRuntime(group_id, members, deps, notify=notify, enabled=arm.armed,
                        notify_alert=notify_alert)


@dataclass(frozen=True)
class GroupIdentity:
    """分组身份：**与设置无关、重装时必须原样带过去**的五个字段。

    ★ 评审 P0-4：第 5 个字段 `session_rule` 属于这里，理由与本 dataclass 的名字逐字吻合——
    用户在启动弹窗上的选择是**会话身份**的一部分，与设置无关。不加这一条，
    `PUT /settings/intel` 触发的 `rearm_group` 会用一个默认规则重建 deps，把「不采信」悄悄
    翻成「全文采信」，且不报任何错。
    """

    sessionmaker: object
    group_id: str
    bar: str
    change_24h_pct: object | None
    session_rule: IntelSessionRule = IntelSessionRule()


def identity_of(deps: IntelDeps) -> GroupIdentity:
    """从一份在跑的依赖里取出分组身份（重装时原样带给下一份）。

    ⚠️ 本函数是**逐字段手抄**的构造，而 `rearm_group` 正是经它拿身份。漏抄任何一行的表现都是
    静默的；`session_rule` 那一行的漏抄方向还是**最坏的那一个**（`off` 翻成 `full`）。
    """
    return GroupIdentity(sessionmaker=deps.sessionmaker, group_id=deps.group_id,
                         bar=deps.bar, change_24h_pct=deps.change_24h_pct,
                         session_rule=deps.session_rule)   # ★ P0-4：漏这一行 = off 翻成 full


def _build_deps(*, settings, api_key: str, provider_cfg, make_client,
                ident: GroupIdentity) -> tuple[IntelDeps, ArmState]:
    """按**已解析好的** settings 造一份新依赖（纯装配，无 DB 会话、无出网）。

    Args:
        settings: `resolve_intel_settings` 的派生副本。
        api_key: `resolve_api_key` 的产物（**显式入参**——评审 P1-1）。
        provider_cfg / make_client: LLM provider 配置与 client 工厂。
        ident: 分组身份。★ **评审 P0-1**：这四个字段**必须显式传**，绝不能「从上一份继承」。
            原实现的两个壳分支构造 `IntelDeps` 时只传了 `settings` 与 `sessionmaker`，
            `group_id` / `bar` / `change_24h_pct` 取的是 dataclass 默认值 `""` / `"15m"` /
            `None`——**壳恰恰是没有分组身份的那一个**。而「开跑时没配源、跑起来之后才去设置里
            配」正是本轮用户报告的那一种处境：从壳继承身份，会让修好之后采到的每一条情报都挂着
            空 `groupId`、三种轮存根同样落空、Agent 拿到写死的 15m 市场上下文、「近 24 小时
            涨跌」整行消失——**而且没有任何东西会报错**。

    Returns:
        `(deps, arm)`。检索源装不上 ⇒ 返回一个 `search=None` 的 deps 与一个未装配的
        `ArmState`——**不抛**，「没配源」是一个正常状态而不是错误。

    ⚠️ 本函数是 `IntelDeps` 的**唯一**构造点：`build_group_runtime` 的三条分支（总闸关 /
    没配源 / 实装）与 `rearm_group` 全部走它，于是「两条壳分支形状不一致」与「壳没有身份」被
    同一次重构一起消灭。
    """
    from . import evidence
    from . import search as search_mod
    from .llm import build_intel_llm_client

    base = {"settings": settings, "sessionmaker": ident.sessionmaker,
            "group_id": ident.group_id, "bar": ident.bar,
            "change_24h_pct": ident.change_24h_pct,
            # ★ 评审 P0-4：会话规则同样是分组身份的一部分，**三条壳分支与实装分支都要带**。
            # 漏掉它 ⇒ 重装后回落 dataclass 默认值（enabled=True, mode="full"），把用户
            # 启动时选的「不采信」翻成「全文采信」，且不报错。
            "session_rule": ident.session_rule}
    code = arming.classify_config(settings)
    if code:
        return IntelDeps(**base), arming.arm_of_code(code)
    provider = search_mod.resolve_search_provider(
        slug=str(settings.ai_trade_intel_search_provider), api_key=api_key,
        base_url=str(settings.ai_trade_intel_search_base_url),
        timeout_s=float(settings.ai_trade_intel_search_timeout_s),
        max_retries=int(settings.ai_trade_intel_search_max_retries),
        proxy=settings.proxy_url)
    if provider is None:
        # `classify_config` 说配齐了、适配器却造不出来。两张真值表本该同源（G-25），走到这里
        # 只可能是适配器构造本身失败——那是装配失败，不是「你没配」。
        return IntelDeps(**base), arming.arm_of_code("arm_failed")
    runner = search_mod.SearchRunner(
        provider, timeout_s=float(settings.ai_trade_intel_search_timeout_s))
    # closed-loop 评审 P1-3：核查轮的**第二个** client（`verify_max_tokens` 只能走构造参）。
    # 装配失败 ⇒ `verify_llm=None` ⇒ 编排层回落 `deps.llm`，**不报错**（§十一 该行的定稿）。
    # 这个 try 不是防御性噪音：它守的是「核查轮的预算装配」这一件**加法**的事，而它下面那次
    # `build_intel_llm_client` 守的是采集轮——采集轮造不出 client 就该照旧向上抛，两者不同命。
    try:
        verify_llm = build_intel_llm_client(
            provider_cfg, settings, make_client=make_client,
            max_tokens_override=int(getattr(settings, "ai_trade_intel_verify_max_tokens",
                                            2048)))
    except Exception as exc:  # noqa: BLE001 —— 见上：回落比整组不装配好
        logger.warning("ai-trade intel: 核查轮 client 装配失败，回落采集轮预算（%s）: %s",
                       ident.group_id, exc)
        verify_llm = None
    # evidence-grounding 轴 A：抓取器**只持配置**（frozen dataclass），`httpx.AsyncClient` 在
    # `fetch_many` 内部 `async with` 建与关。`proxy` 复用进程代理（E-15），**不新增配置项**；
    # 关掉 `reader_enabled` ⇒ `fetch_many` 一个请求都不发、语料块逐字节回到上一轮。
    deps = IntelDeps(**base, search=runner,
                     llm=build_intel_llm_client(provider_cfg, settings,
                                                make_client=make_client),
                     verify_llm=verify_llm, reader=evidence.reader_config(settings))
    return deps, ArmState(True, True, True)


# ---------------- 轴 B：就地热重装（换配置、不换实例） ----------------
# 收尾等待的轮询节拍。比 `TICK_SECONDS` 小两个数量级——它等的是一次已经在跑的收尾段，不是排期。
_SETTLE_POLL_S = 0.02


async def _await_round_settled(runtime: "IntelRuntime", timeout_s: float) -> bool:
    """有界地等这一轮收完尾（**不取锁**）。返回是否等到了。

    取锁会与 `rearm` 刚释放的那把锁排队，把一次有界等待变成无界等待——那正是本函数存在的理由
    的反面。判据取 `runtime.busy`（`_in_flight` **或** 收尾段计数器），因为 `_after_round`
    跑在锁外、且在 `_in_flight` 被清掉之后（实施发现 I-3）。
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + max(0.0, float(timeout_s))
    while runtime.busy:
        if loop.time() >= deadline:
            return False
        await asyncio.sleep(_SETTLE_POLL_S)
    return True


async def _sync_scheduler(runtime: "IntelRuntime") -> None:
    """让调度循环与 `runtime.enabled` 对齐。

    ★ 评审 P1-10：停机分支**不能直接 cancel**。`_run` 的 `async with self._lock` 在
    `await self._after_round(...)` **之前**就退出了，所以此刻上一轮的收尾段可能正在跑；
    `scheduler.cancel` 取消的正是承载它的那个 `_loop` 任务 ⇒ 这一轮的快照刷新与 `_alert`
    告警交接被静默丢弃。告警交接是「一条够格的高危情报当场多问一次 AI」那条链的第二跳，把它
    丢在一次开关拨动上，症状是「今天为什么一次都没告警」这个问题**恰好在这一次没有答案**。
    故：先给收尾段一个**有界**的窗口（复用同一条重装死线），超时才硬 cancel 并如实记 warning。
    """
    from . import scheduler

    if runtime.enabled:
        scheduler.start(runtime)        # 已有循环时它自己 return（既有幂等保护）
        return
    settled = await _await_round_settled(
        runtime, float(getattr(runtime.deps.settings,
                               "ai_trade_intel_rearm_deadline_s", 2.5)))
    if not settled:
        logger.warning("ai-trade intel: 分组 %s 的收尾段在死线内没有跑完，直接取消调度循环"
                       "（这一轮的快照刷新与告警交接会丢失）", runtime.group_id)
    scheduler.cancel(runtime.group_id)
    for inst_id in runtime.members:     # 停下来这件事也必须立刻在界面上看得见
        scheduler.schedule(runtime.refresh(inst_id))


def arm_result_for(group_id: str) -> dict:
    """按 `rearm_group` 的同一张回执读一个分组此刻的装配结论（`manager` 的回落支路用）。

    薄转发到 `arming.arm_result_of`——本文件只负责从注册表里把运行时找出来。
    """
    return arming.arm_result_of(group_id, runtime=_RUNTIMES.get(group_id))


async def rearm_group(*, group_id: str, settings, provider_cfg, make_client,
                      deadline_s: float = 2.5) -> dict:
    """重装一个分组的情报运行时（`manager.rearm_intel` 的唯一实现）。

    `sessionmaker` / `members` / `bar` / `change_24h_pct` / `notify` / `notify_alert` 全部
    **从已注册的那个运行时上取**——它们是分组身份的一部分，与设置无关；这也让形参收在 5 个
    以内（CLAUDE.md 二）。

    Returns:
        `{"rearmed", "pending", "armed", "reason", "detail", "changed"}`。**一次装配失败不抛**
        ——它被收敛成 `reason="arm_failed"` 的回执并记进 `_ARM_ERRORS`（与 `_wire_intel` 的
        fail-open 同源）：调用点是一个设置保存请求，情报层装不上不该让「保存设置」失败。

    ⚠️ 本函数**不是**全局的 fail-open 点：库读不出来（`resolve_intel_settings` /
    `resolve_api_key`）这类 IO 异常仍然原样上抛，由**唯一**那个吞异常的地方
    `api/app_settings._rearm_intel_pipeline` 接住。把 `except Exception` 撒在每一层，真正的
    失败就再也没有人看得见了（CLAUDE.md 五：捕获异常时尽量缩小作用域）。

    Note:
        `_RUNTIMES` 里没有这个分组时回 `not_running` 并**把裁决权交回 `manager.rearm_intel`**
        ——★ 评审 P0-3：`_wire_intel` 的 fail-open 是在 `register` **之前**吞的异常，所以
        「分组在跑、却一个 runtime 都没有」正是那个自救入口唯一要救的处境。在这里直接回
        `not_running` 就是对一个正在跑的分组说「AI 自动交易未在运行」——一句确定错误的话。
    """
    runtime = _RUNTIMES.get(group_id)
    if runtime is None:
        return dict(ARM_NOT_RUNNING)
    ident = identity_of(runtime.deps)
    async with ident.sessionmaker()() as db:
        derived = await service.resolve_intel_settings(db, settings)
        api_key = await service.resolve_api_key(db, derived)
    fingerprint = arming.settings_fingerprint(derived, api_key)
    if fingerprint == runtime.arm_fingerprint and not runtime.arm_pending:
        # 一次真正的空操作：连锁都不碰（G-18 用一把会计数的替身锁冻结这一条）。
        return arming.arm_result(rearmed=False, pending=False, arm=runtime.arm,
                                 changed=False)
    try:
        deps, arm = _build_deps(settings=derived, api_key=api_key, provider_cfg=provider_cfg,
                                make_client=make_client, ident=ident)
    except Exception as exc:  # noqa: BLE001 —— 见 docstring：保存设置绝不因情报层失败而失败
        logger.warning("ai-trade intel rearm build failed (%s): %s", group_id, exc)
        arming.record_arm_error(group_id, exc)
        # **旧运行时一个字段都没被改**（§3.0 第 2 条：一次失败的重装绝不允许把一条正在正常
        # 工作的流水线弄停）。
        return arming.arm_result(
            rearmed=False, pending=False,
            arm=ArmState(True, True, False, "arm_failed", str(exc)[:200]),
            changed=False)
    from . import scheduler

    seq = next(_ARM_SEQ)
    try:
        await asyncio.wait_for(runtime.rearm(deps, arm, seq, fingerprint),
                               timeout=max(0.0, float(deadline_s)))
    except asyncio.TimeoutError:
        # 有一轮在飞（可达 60 秒）。**不阻塞这个 HTTP 请求**：把重装排进既有 tracked 任务集
        # （它已被 `_shutdown_steps` 第 7 步 drain，故本轮不新增任何关停步骤），并如实回执。
        runtime.mark_arm_pending()              # ★ 只改 `_arm`，不动 `enabled` / `deps`
        scheduler.schedule(runtime.rearm(deps, arm, seq, fingerprint))
        return arming.arm_result(rearmed=False, pending=True, arm=runtime.arm,
                                 changed=True)
    if arm.reason == "arm_failed":
        arming.record_arm_error(group_id, "检索源适配器构造失败")
    else:
        arming.clear_arm_error(group_id)
    return arming.arm_result(rearmed=True, pending=False, arm=arm, changed=True)
