"""原始成交的幂等落库与持久重放校验，不触发交易或经济重算。"""
from sqlalchemy import case, func, select

from ..db import repositories as repo
from ..db.models import Trade


def session_has_pending_persistence(session) -> bool:
    """停止标志只表示不再交易，未保存的原始事实仍需要原会话持有。"""
    receipt = getattr(session, "flatten_result", None) or {}
    return bool(getattr(session, "_unpersisted_trades", None)
                or getattr(session, "_trade_persist_failed", False)
                or getattr(session, "_trade_backlog_persist_failed", False)
                or receipt.get("tradePersistenceFailed"))


async def append_trade_once(db, run_id: int, trade: dict) -> bool:
    """按运行及稳定事件身份落库；返回是否新增，数据库异常原样上抛。"""
    meta = (trade.get("explain") or {}).get("meta") or {}
    event_id = meta.get("accountingEventId")
    if event_id:
        existing = (await db.execute(select(Trade.id).where(
            Trade.run_id == run_id,
            func.json_extract(case(
                (func.json_valid(Trade.explain_json), Trade.explain_json),
                else_="{}"), "$.meta.accountingEventId") == event_id,
        ).limit(1))).scalar_one_or_none()
        if existing is not None:
            return False
    await repo.append_live_trade(db, run_id, trade)
    return True


def pending_trades_for_member(member: dict) -> list[dict]:
    """读取可重放的原始事件；不可读或跨运行身份抛错并保留原契约。"""
    runtime = member.get("runtimeIntegrity") or {}
    if not isinstance(runtime, dict):
        raise ValueError("成员运行契约不可读")
    queue = runtime.get("pendingTrades")
    if queue is None:
        return []
    if not isinstance(queue, list):
        raise ValueError("未落库成交队列不可读")
    for trade in queue:
        if not isinstance(trade, dict):
            raise ValueError("未落库成交事件不可读")
        explain = trade.get("explain")
        meta = explain.get("meta") if isinstance(explain, dict) else None
        identity = (meta.get("accountingEventId")
                    if isinstance(meta, dict) else None)
        if not isinstance(identity, str) or not identity:
            raise ValueError("未落库成交缺少稳定事件身份")
        if (trade.get("runId") != member.get("runId")
                or meta.get("runId") != member.get("runId")):
            raise ValueError("未落库成交运行身份不符")
    return queue
