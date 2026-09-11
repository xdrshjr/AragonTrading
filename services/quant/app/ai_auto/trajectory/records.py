"""各种轨迹记录体的纯构造函数（ai-trade-trajectory-capture §2.4 / §2.5 / §2.7.2）。

只读入参、零 IO、不参与任何交易判定。返回值是**记录体**（不含公共信封）：信封与序号由通道在入队
那一刻拼上，使「序列化失败」发生在分配序号之前，不会凭空造出一段缺口。
"""
from __future__ import annotations

from ..open_funnel import classify_block
from .wire import decision_wire

SITE_DECISION = "decision"
_MESSAGE_MAX = 2000


def closure(completion: str, *, abort_reason: str | None = None, error: dict | None = None,
            summary_installed: bool | None = None) -> dict:
    """`round` 记录的收尾四键（W3b / W4 / W5 共用同一段装配，只差这四个值）。"""
    return {"completion": completion, "abortReason": abort_reason, "error": error,
            "summaryInstalled": summary_installed}


def error_wire(exc: BaseException) -> dict:
    """异常 → `{type, message}`（消息截到 2000 字符：它是诊断信息，不是训练文本）。"""
    return {"type": type(exc).__name__, "message": str(exc)[:_MESSAGE_MAX]}


def adopted_decision_call_id(calls: list[dict], reasoning) -> str | None:
    """「被采纳的那一次决策调用」（§2.5 的五行表，用例 T-9 逐行冻结）。

    决策器控制流确定：本轮 `site == "decision"` 的调用序列恒为 `[首次, 重试…, 追问?]`。
    追问被拒收（`reasked and not reask_adopted`）时回滚到上一次的研判与决策 ⇒ 倒数第二次；
    其余情形（含深度档未生效、未追问、追问被采纳）⇒ 最后一次；本轮没有决策调用 ⇒ `None`。
    """
    ids = [call.get("callId") for call in calls or [] if call.get("site") == SITE_DECISION]
    if not ids:
        return None
    if _flag(reasoning, "reasked", "reasked") and not _flag(reasoning, "reask_adopted",
                                                              "reaskAdopted"):
        return ids[-2] if len(ids) >= 2 else None
    return ids[-1]


def accounting_context_of(session) -> dict | None:
    """会话成本层那份 `TradeAccountingAccumulator` 的 `context` **拷贝**（纯读、绝不抛，RV13）。

    持有者见 `attribution.py`（`{"costBasis": net|gross, "ctVal": …}`）。取原对象、不另算：它是
    编译期重放奖励的会计口径唯一来源，缺 `costBasis` 即走 legacy 费池分支。
    """
    try:
        context = session._cost.attribution.accounting.context
    except Exception:  # noqa: BLE001 —— 见 docstring：测试替身与未装配成本层一律视为缺席
        return None
    return dict(context) if isinstance(context, dict) else None


def build_round_record(scope, cap, fills: list[dict], ending: dict) -> dict:
    """暂存断面 + 成交腿 + 收尾四键 → `round` 记录体。"""
    return {
        "roundNo": cap.round_no, "ts": cap.ts, "trigger": (cap.wake or {}).get("trigger"),
        "wake": cap.wake, "observation": cap.payload,
        "decision": {"model": decision_wire(cap.stage1_decision),
                     "final": decision_wire(cap.decision)},
        # vetoLayer 取 gate_degrade 而非合成后的 degrade：`exit_order_slot_busy` 不是门禁。
        "degrade": {"llm": cap.llm_degrade, "gate": cap.gate_degrade, "final": cap.degrade,
                    "vetoLayer": classify_block(cap.gate_degrade)},
        "reflection": {"verdict": cap.reflection_verdict, "reason": cap.reflection_reason},
        "reasoning": _reasoning_wire(cap.reasoning),
        "executed": cap.executed, "fills": fills, "elapsedMs": cap.elapsed_ms,
        "calls": calls_wire(scope),
        "adoptedDecisionCallId": adopted_decision_call_id(scope.calls, cap.reasoning),
        **ending,
    }


def calls_wire(scope) -> list[dict]:
    """本轮登记的全部调用（按发生序）。"""
    return [dict(call) for call in scope.calls]


def round_failed(scope, ts: int, exc: BaseException) -> dict:
    """失败早于暂存（`payload` 可能都未绑定）：只写原因与已发生的调用 id。"""
    return {"roundNo": scope.round_no, "ts": int(ts), "errorType": type(exc).__name__,
            "message": str(exc)[:_MESSAGE_MAX], "calls": calls_wire(scope)}


def round_aborted(scope) -> dict:
    """在飞轮于任何成交之前被暂停 / 停止打断。"""
    return {"roundNo": scope.round_no, "ts": scope.ts, "calls": calls_wire(scope),
            "reason": "not_completed"}


def round_skipped(round_no: int, ts: int, detail: dict) -> dict:
    """整轮跳过：与 WS `round_skipped` 事件同一组键、同一个数。"""
    return {"roundNo": int(round_no), "ts": int(ts), **(detail or {})}


def episode_start(facts, *, env: str, ledger_seq_at_open: int, resume_of: dict | None,
                  catch_up: str) -> dict:
    """一个会话 epoch 的开始（W1）。"""
    return {"sessionId": facts.session_id, "bar": facts.bar, "env": env,
            "accounting": facts.accounting, "strategyIds": list(facts.strategy_ids),
            "strategyNames": list(facts.strategy_names),
            "contextRefreshRounds": facts.context_refresh_rounds,
            "wakeIntervalS": facts.wake_interval_s, "takeProfitUsd": facts.take_profit_usd,
            "stopLossUsd": facts.stop_loss_usd, "startedTs": facts.started_ts,
            "ledgerSeqAtOpen": int(ledger_seq_at_open), "resumeOf": _resume_wire(resume_of),
            "catchUp": catch_up}


def episode_end(end) -> dict:
    """一个会话 epoch 的结束（W8）。"""
    return {"stopReason": end.stop_reason, "tradeCount": end.trade_count,
            "realizedPnlUsd": end.realized_pnl_usd, "flattenResult": end.flatten_result}


def fills_body(context: str, fills: list[dict]) -> dict:
    """决策轮之外采到的成交腿。承载记录只表示「在何时被采到」，不表示「由谁产生」。"""
    return {"context": context, "fills": fills}


def lifecycle(event: str, *, reason: str | None = None, flatten: bool | None = None,
              path: str | None = None) -> dict:
    """暂停（W9）与继续（W10）。"""
    return {"event": event, "reason": reason, "flatten": flatten, "path": path}


def ledger_gap(from_seq: int, to_seq: int) -> dict:
    """成交台账缺口：两次同步之间成交数超过台账保留量，取不到的那几笔（`scope="ledger"`）。"""
    return {"fromSeq": int(from_seq), "toSeq": int(to_seq),
            "count": int(to_seq) - int(from_seq) + 1, "reason": "ledger_trimmed",
            "scope": "ledger"}


def _reasoning_wire(reasoning) -> dict | None:
    if reasoning is None:
        return None
    wire = getattr(reasoning, "wire", None)
    if callable(wire):
        return wire()
    return dict(reasoning) if isinstance(reasoning, dict) else None


def _flag(reasoning, attr: str, key: str) -> bool:
    if reasoning is None:
        return False
    if isinstance(reasoning, dict):
        return bool(reasoning.get(key))
    return bool(getattr(reasoning, attr, False))


def _resume_wire(resume_of: dict | None) -> dict | None:
    if not resume_of:
        return None
    return {"groupId": resume_of.get("groupId"), "createdTs": resume_of.get("createdTs"),
            "resumedCount": resume_of.get("resumedCount")}
