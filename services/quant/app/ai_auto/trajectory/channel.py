"""每标的一个采集通道：会话侧写点、回合作用域三态机、成交水位与序号分配（§2.3 / §2.4 / §2.6）。

硬约束（用例 S-7 / G-T3 冻结）：
- `TrajectoryChannel` 每个公开方法的函数体顶层**恰好一个** `try/except Exception`，`except` 只做
  `self._recorder.note_serialize_error(exc)`——采集层的任何缺陷都不许在真实资金热路径上抛出来；
- 全部同步、零文件 IO、不取会等待写线程的锁（字节预算锁里只有两次整数运算）；
- episode 关闭之后（或 recorder 已停止受理）一律直通空转：只由 `open_call` 计 `lateCalls`，
  **不分配序号、不计 dropped、不写 gap**（RV6：晚到的调用不是缺口）；
- **在飞回合拥有它窗口里的全部成交**：回合作用域未关闭时，盯市 / 暂停 / 停止三处同步一律让路，由回合
  自己的收尾（W3b / W4 / W5 / 关 episode）取走。否则一轮自己的开仓腿会在 `_execute_decision` 之后那几次
  await 里被盯市 tick 抢走，而编译器只把 `round` 承载的腿记进该轮奖励——真正开了仓的那一轮会以空成交、
  未结算的样子进入 steps.jsonl。
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ..accounting_replay import trade_meta
from . import records
from .llm_tap import ROUND_SITES, SITE_DISTILL, CallCapture, CallOpening, classify_site
from .wire import canonical_json, envelope, freeze_request, join_objects, origin
from .writer import QueuedCall, QueuedLine

if TYPE_CHECKING:
    from .recorder import TrajectoryRecorder

SCOPE_OPEN = "open"
SCOPE_STAGED = "staged"
SCOPE_CLOSED = "closed"
_EPISODE_IDLE = "idle"
_EPISODE_OPEN = "open"
_EPISODE_CLOSED = "closed"
# 冷续跑离线补记在回灌台账里找不到上一进程最后一笔事件键时的回看笔数（多补无害：编译器按事件键去重）。
CATCH_UP_LOOKBACK = 64


@dataclass(frozen=True, slots=True)
class SessionBinding:
    """通道绑定的会话身份。"""

    session_id: str
    run_id: int
    inst_id: str
    bar: str


@dataclass(frozen=True, slots=True)
class EpisodeFacts:
    """W1 取自会话既有属性的事实（不做计算）。"""

    session_id: str
    run_id: int
    inst_id: str
    bar: str
    strategy_ids: list
    strategy_names: list
    context_refresh_rounds: int
    wake_interval_s: float
    take_profit_usd: float | None
    stop_loss_usd: float | None
    started_ts: int
    accounting: dict | None          # v2 · RV13：会话累加器 context 的拷贝


@dataclass(frozen=True, slots=True)
class EpisodeEnd:
    """W8 的收尾事实。"""

    stop_reason: str | None
    trade_count: int
    realized_pnl_usd: float | None
    flatten_result: dict | None


@dataclass(frozen=True, slots=True)
class RoundCapture:
    """W3a 暂存的回合断面：只承载 `_run_decision_round` 里**已经存在**的局部变量引用。"""

    round_no: int
    ts: int
    wake: dict
    payload: dict
    stage1_decision: object          # 任何门禁之前的 TradeDecision
    decision: object                 # 门禁 + 反思 + 仓位求解之后、真正执行的 TradeDecision
    executed: dict
    llm_degrade: str | None
    gate_degrade: str | None
    degrade: str | None
    reflection_verdict: str | None
    reflection_reason: str | None
    reasoning: object | None         # decider.last_reasoning（ReasoningMeta | None）
    elapsed_ms: float | None


@dataclass(slots=True)
class RoundScope:
    """一个回合作用域；`state ∈ {open, staged, closed}`，每个写点的迁移唯一（§2.3.2）。"""

    round_no: int
    ts: int
    wake: dict
    calls: list = field(default_factory=list)
    state: str = SCOPE_OPEN
    staged: RoundCapture | None = None


class TrajectoryChannel:
    """每标的一个通道（由 `TrajectoryRecorder.channel(inst_id)` 幂等产出）。"""

    def __init__(self, recorder: "TrajectoryRecorder", inst_id: str) -> None:
        self._recorder = recorder
        self.inst_id = inst_id
        self._binding: SessionBinding | None = None
        self._executor = None
        self._episode = _EPISODE_IDLE
        self._ledger_seen = 0
        self._scope: RoundScope | None = None

    # ---------- 公开写点（每个方法顶层恰一个 try/except Exception）----------
    def bind(self, binding: SessionBinding, *, executor) -> None:
        """绑定会话身份与执行器（成交水位的唯一数据源）。"""
        try:
            self._binding, self._executor = binding, executor
            self._recorder.note_member(self.inst_id, run_id=binding.run_id)
        except Exception as exc:  # noqa: BLE001 —— 见模块 docstring
            self._recorder.note_serialize_error(exc)

    def open_episode(self, facts: EpisodeFacts) -> None:
        """W1：写 `episode_start`，冷续跑时先按上一份 manifest 的水位补记离线成交。"""
        try:
            self._open_episode(facts)
        except Exception as exc:  # noqa: BLE001 —— 见模块 docstring
            self._recorder.note_serialize_error(exc)

    def begin_round(self, round_no: int, ts: int, wake: dict) -> None:
        """W2：建回合作用域（上一个若仍未关闭，先按 W5 规则写出）。"""
        try:
            self._begin_round(round_no, ts, wake)
        except Exception as exc:  # noqa: BLE001 —— 见模块 docstring
            self._recorder.note_serialize_error(exc)

    def stage_round(self, cap: RoundCapture) -> None:
        """W3a：暂存断面（零序列化、零入队）。成交已经发生，断面必须先于任何 await 留住。"""
        try:
            scope = self._active_scope(cap.round_no)
            if scope is not None and scope.state == SCOPE_OPEN:
                scope.staged, scope.state = cap, SCOPE_STAGED
        except Exception as exc:  # noqa: BLE001 —— 见模块 docstring
            self._recorder.note_serialize_error(exc)

    def end_round(self, *, summary_installed: bool) -> None:
        """W3b：把暂存断面补上成交腿与摘要位后以 `completion:"full"` 写出。"""
        try:
            scope = self._scope
            if self._live() and scope is not None and scope.state == SCOPE_STAGED:
                self._write_round(scope, records.closure(
                    "full", summary_installed=bool(summary_installed)))
        except Exception as exc:  # noqa: BLE001 —— 见模块 docstring
            self._recorder.note_serialize_error(exc)

    def fail_round(self, round_no: int, ts: int, exc: BaseException) -> None:
        """W4：`open` ⇒ `round_failed`；`staged`（成交之后才抛）⇒ `round{partial, exception}`。"""
        try:
            self._fail_round(round_no, ts, exc)
        except Exception as error:  # noqa: BLE001 —— 见模块 docstring
            self._recorder.note_serialize_error(error)

    def release_round(self, round_no: int) -> None:
        """W5：只处理仍为 `open` / `staged` 的作用域；`closed` ⇒ 空转。"""
        try:
            scope = self._active_scope(round_no)
            if scope is not None:
                self._release(scope)
        except Exception as exc:  # noqa: BLE001 —— 见模块 docstring
            self._recorder.note_serialize_error(exc)

    def skip_round(self, round_no: int, ts: int, detail: dict) -> None:
        """W6：写 `round_skipped` 并关闭作用域（`finally` 里的 W5 因此空转，RV5）。"""
        try:
            self._skip_round(round_no, ts, detail)
        except Exception as exc:  # noqa: BLE001 —— 见模块 docstring
            self._recorder.note_serialize_error(exc)

    def sync_fills(self, context: str) -> None:
        """W7 / W8：把水位之后的新成交腿写成 `fills{context}`（有在飞回合时留给该回合，见模块 docstring）。"""
        try:
            self._sync_fills(context)
        except Exception as exc:  # noqa: BLE001 —— 见模块 docstring
            self._recorder.note_serialize_error(exc)

    def note_paused(self, reason: str, flatten: bool) -> None:
        """W9：补采暂停产生的成交 → `lifecycle{pause}` → 令写线程立即 fsync（桌面端退出走这条路）。"""
        try:
            if self._live():
                self._sync_fills("pause")
                self._offer("lifecycle", records.lifecycle(
                    "pause", reason=reason, flatten=bool(flatten)))
                self._recorder.request_flush()
        except Exception as exc:  # noqa: BLE001 —— 见模块 docstring
            self._recorder.note_serialize_error(exc)

    def note_resumed(self, path: str) -> None:
        """W10：热续跑（冷续跑是新 epoch，由 W1 写 `episode_start{resumeOf}`）。"""
        try:
            if self._live():
                self._offer("lifecycle", records.lifecycle("resume", path=path))
        except Exception as exc:  # noqa: BLE001 —— 见模块 docstring
            self._recorder.note_serialize_error(exc)

    def close_episode(self, end: EpisodeEnd) -> None:
        """W8：写 `episode_end`；此后全部写点直通空转。"""
        try:
            self._close_episode(end)
        except Exception as exc:  # noqa: BLE001 —— 见模块 docstring
            self._recorder.note_serialize_error(exc)

    def open_call(self, *, system, messages, tools, params: dict) -> CallCapture | None:
        """包装器开流时调用：冻结请求、登记进当前回合；蒸馏 / 晚到调用返回 `None`（纯直通）。"""
        try:
            return self._open_call(system, messages, tools, params)
        except Exception as exc:  # noqa: BLE001 —— 见模块 docstring
            self._recorder.note_serialize_error(exc)

    def emit_call(self, opening: CallOpening, body: dict) -> None:
        """`CallCapture` 收尾时调用：拼信封、分配序号、入队。"""
        try:
            self._emit_call(opening, body)
        except Exception as exc:  # noqa: BLE001 —— 见模块 docstring
            self._recorder.note_serialize_error(exc)

    def note_error(self, exc: BaseException) -> None:
        """观察器内部的失败只计数（`serializeErrors`）。"""
        try:
            self._recorder.note_serialize_error(exc)
        except Exception as error:  # noqa: BLE001 —— 见模块 docstring
            self._recorder.note_serialize_error(error)

    def register_secret(self, secret) -> None:
        """登记一个需要在落盘前剔除的凭证原文（长度 < 8 的串不登记）。"""
        try:
            self._recorder.register_secret(secret)
        except Exception as exc:  # noqa: BLE001 —— 见模块 docstring
            self._recorder.note_serialize_error(exc)

    # ---------- 私有实现 ----------
    def _live(self) -> bool:
        return self._episode == _EPISODE_OPEN and self._recorder.accepting

    def _active_scope(self, round_no: int) -> RoundScope | None:
        scope = self._scope
        if (not self._live() or scope is None or scope.state == SCOPE_CLOSED
                or scope.round_no != int(round_no)):
            return None
        return scope

    def _open_episode(self, facts: EpisodeFacts) -> None:
        if not self._recorder.accepting or self._executor is None:
            return
        self._episode = _EPISODE_OPEN
        count = int(self._executor.trade_count)
        catch_up, fills, gap = self._catch_up(count)
        self._offer("episode_start", records.episode_start(
            facts, env=self._recorder.env, ledger_seq_at_open=count,
            resume_of=self._recorder.resume_of, catch_up=catch_up))
        if gap is not None:
            self._offer("gap", gap)
        if fills:
            self._offer("fills", records.fills_body("offline_catch_up", fills))
        self._set_ledger_seen(count)

    def _catch_up(self, count: int) -> tuple[str, list[dict], dict | None]:
        """冷续跑的离线补记：覆盖 `settle_closed_offline` 在离线期补记的平仓腿（§2.6）。

        起点**按成交身份**找，不按计数比：`restore_trades` 令 `trade_count = len(DB 行)`，而「落库失败、
        仅留内存」的成交会让上一进程的水位虚高——只比 `captured >= count` 会把离线补记的那笔平仓整条
        漏掉且不留任何缺口。上一份 manifest 记下的最后一笔事件键在回灌台账里找得到 ⇒ 从它之后补；
        找不到 ⇒ 回看 `CATCH_UP_LOOKBACK` 笔。多补无害：编译器按事件键去重（RV12）。
        """
        if self._recorder.resume_of is None:
            return "none", [], None
        members = (self._recorder.previous_manifest or {}).get("members") or {}
        previous = members.get(self.inst_id) or {}
        captured = previous.get("ledgerSeqCaptured")
        if isinstance(captured, bool) or not isinstance(captured, int):
            return "unavailable", [], None
        start = self._catch_up_start(captured, count, previous.get("lastAccountingEventId"))
        if start >= count:
            return "none", [], None
        fills, gap = self._slice_fills(start, count)
        return "written", fills, gap

    def _catch_up_start(self, captured: int, count: int, last_event: str | None) -> int:
        """补记起点（本 run 的第几笔之后）：优先按上一进程最后采到的事件键定位，否则有界回看。"""
        if last_event:
            for offset, trade in enumerate(reversed(self._executor.trades)):
                if _event_id(trade) == last_event:
                    return count - offset
        return max(0, min(captured, count) - CATCH_UP_LOOKBACK)

    def _begin_round(self, round_no: int, ts: int, wake: dict) -> None:
        if not self._live():
            return
        if self._scope is not None and self._scope.state != SCOPE_CLOSED:
            self._release(self._scope)          # 防御「上一轮的 finally 没跑到」
        self._scope = RoundScope(int(round_no), int(ts), dict(wake or {}))

    def _fail_round(self, round_no: int, ts: int, exc: BaseException) -> None:
        scope = self._active_scope(round_no)
        if scope is None:
            return
        if scope.state == SCOPE_STAGED:
            self._write_round(scope, records.closure(
                "partial", abort_reason="exception", error=records.error_wire(exc)))
            return
        try:
            self._offer("round_failed", records.round_failed(scope, ts, exc))
        finally:
            scope.state = SCOPE_CLOSED

    def _release(self, scope: RoundScope, abort_reason: str = "cancelled") -> None:
        if scope.state == SCOPE_STAGED:
            self._write_round(scope, records.closure("partial", abort_reason=abort_reason))
            return
        try:
            self._offer("round_aborted", records.round_aborted(scope))
        finally:
            scope.state = SCOPE_CLOSED

    def _skip_round(self, round_no: int, ts: int, detail: dict) -> None:
        scope = self._active_scope(round_no)
        if scope is None or scope.state != SCOPE_OPEN:
            return
        try:
            self._offer("round_skipped", records.round_skipped(round_no, ts, detail))
        finally:
            scope.state = SCOPE_CLOSED

    def _write_round(self, scope: RoundScope, ending: dict) -> None:
        """W3b / W4 / W5 在 `staged` 作用域上共用的**同一段**装配（绝不写两份）。"""
        try:
            fills = self._take_new_fills()
            self._offer("round", records.build_round_record(scope, scope.staged, fills, ending))
        finally:
            scope.state = SCOPE_CLOSED

    def _sync_fills(self, context: str) -> None:
        if not self._live() or self._round_in_flight():
            return
        fills = self._take_new_fills()
        if fills:
            self._offer("fills", records.fills_body(context, fills))

    def _round_in_flight(self) -> bool:
        return self._scope is not None and self._scope.state != SCOPE_CLOSED

    def _close_episode(self, end: EpisodeEnd) -> None:
        if not self._live():
            return
        try:
            if self._round_in_flight():
                # 在飞轮先落盘（W5 会晚于 episode_end 到达）。标 `episode_closed` 而不是 `cancelled`：
                # `_post_round` 里触发的会话止盈 / 止损是在本轮内部停的会话，这一轮其实已经跑完。
                self._release(self._scope, abort_reason="episode_closed")
            self._sync_fills("stop")            # 回合收尾之后剩下的腿（平仓回执 / 结算 drain）
            self._offer("episode_end", records.episode_end(end))
        finally:
            self._episode = _EPISODE_CLOSED

    def _take_new_fills(self) -> list[dict]:
        """判据是单调的 `executor.trade_count`，**不是** `len(executor.trades)`（后者会按块裁剪）。"""
        count = int(self._executor.trade_count)
        fills, gap = self._slice_fills(self._ledger_seen, count)
        if gap is not None:
            self._offer("gap", gap)
        if count > self._ledger_seen:
            self._set_ledger_seen(count)
        return fills

    def _slice_fills(self, seen: int, count: int) -> tuple[list[dict], dict | None]:
        new = count - seen
        if new <= 0:
            return [], None
        ledger = self._executor.trades
        available = min(new, len(ledger))
        first_seq = count - available + 1                 # 1-based：本 run 的第几笔成交
        tail = ledger[len(ledger) - available:] if available > 0 else []
        # 浅拷贝即可：随后在同一个同步调用里就被序列化冻结，中间没有任何 await。
        fills = [{**trade, "ledgerSeq": first_seq + offset} for offset, trade in enumerate(tail)]
        gap = records.ledger_gap(seen + 1, count - available) if available < new else None
        return fills, gap

    def _set_ledger_seen(self, count: int) -> None:
        self._ledger_seen = count
        ledger = self._executor.trades
        self._recorder.note_member(self.inst_id, ledger_seq_captured=count,
                                   last_event_id=_event_id(ledger[-1]) if ledger else None)

    def _open_call(self, system, messages, tools, params: dict) -> CallCapture | None:
        site = classify_site(tools)
        if site == SITE_DISTILL or not self._live():
            self._recorder.note_late_call()      # 蒸馏跑在 stop 之后（RV6）：不是缺口
            return None
        started = time.monotonic()
        frozen = freeze_request(system, messages, tools)
        serialize_ms = (time.monotonic() - started) * 1000.0
        call_id = uuid.uuid4().hex
        scope = self._scope
        in_round = site in ROUND_SITES and scope is not None and scope.state != SCOPE_CLOSED
        placement = {"phase": "out_of_round", "roundNo": None, "callIndex": None}
        if in_round:
            placement = {"phase": "round", "roundNo": scope.round_no,
                         "callIndex": len(scope.calls) + 1}
            scope.calls.append({"callId": call_id, "site": site,
                                "callIndex": placement["callIndex"]})
        return CallCapture(self, CallOpening(call_id, site, placement, frozen, dict(params or {}),
                                             serialize_ms))

    def _emit_call(self, opening: CallOpening, body: dict) -> None:
        if not self._live():
            self._recorder.note_late_call()
            return
        body_json = canonical_json(body)
        seq = self._recorder.allocate_seq(self.inst_id)
        head = join_objects(canonical_json(self._envelope("llm_call", seq)), body_json)
        system, messages, tools = opening.frozen
        size = len(head) + len(system) + len(tools) + sum(len(text) for text in messages)
        self._recorder.offer(QueuedCall(self.inst_id, seq, self._recorder.epoch, head, system,
                                        messages, tools, size))

    def _offer(self, kind: str, body: dict) -> None:
        """先序列化记录体、成功后才分配序号：序列化失败绝不凭空造出一段缺口。"""
        body_json = canonical_json(body)
        seq = self._recorder.allocate_seq(self.inst_id)
        line = join_objects(canonical_json(self._envelope(kind, seq)), body_json)
        self._recorder.offer(QueuedLine(self.inst_id, seq, self._recorder.epoch, kind, line,
                                        len(line)))

    def _envelope(self, kind: str, seq: int) -> dict:
        run_id = self._binding.run_id if self._binding is not None else None
        return envelope(kind, seq, origin(self._recorder.epoch, self._recorder.group_id,
                                          self.inst_id, run_id))


def _event_id(trade: dict) -> str | None:
    """成交的会计事件键（`explain.meta.accountingEventId`，兼容回灌行的 `explain_json`）。"""
    event = trade.get("accountingEventId") or trade_meta(trade).get("accountingEventId")
    return str(event) if event else None


class NullTrajectoryChannel:
    """空对象：全部写点空转。会话默认持有它，十二个写点因此无需判 `None`。"""

    def bind(self, binding, *, executor) -> None:
        """空转。"""

    def open_episode(self, facts) -> None:
        """空转。"""

    def begin_round(self, round_no, ts, wake) -> None:
        """空转。"""

    def stage_round(self, cap) -> None:
        """空转。"""

    def end_round(self, *, summary_installed) -> None:
        """空转。"""

    def fail_round(self, round_no, ts, exc) -> None:
        """空转。"""

    def release_round(self, round_no) -> None:
        """空转。"""

    def skip_round(self, round_no, ts, detail) -> None:
        """空转。"""

    def sync_fills(self, context) -> None:
        """空转。"""

    def note_paused(self, reason, flatten) -> None:
        """空转。"""

    def note_resumed(self, path) -> None:
        """空转。"""

    def close_episode(self, end) -> None:
        """空转。"""

    def open_call(self, *, system, messages, tools, params) -> None:
        """空转（包装器不会装在空对象上，此方法只为接口完整）。"""

    def register_secret(self, secret) -> None:
        """空转。"""


NULL_TRAJECTORY = NullTrajectoryChannel()
