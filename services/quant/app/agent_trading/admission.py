"""聊天与自动交易共用的进程内恢复屏障；锁沿用 AI 准入锁。"""
from __future__ import annotations

from .errors import TradingError

_ready = False
_shutting_down = False


def set_ready(ready: bool) -> None:
    """仅完整恢复成功后开放屏障。"""
    global _ready, _shutting_down
    _ready = bool(ready)
    if ready:
        _shutting_down = False


def begin_shutdown() -> None:
    """同步封闭所有尚未发送的聊天交易。"""
    global _ready, _shutting_down
    _ready, _shutting_down = False, True


def ensure_ready() -> None:
    """恢复未完成或停止期间拒绝任何新准入。"""
    if not _ready or _shutting_down:
        raise TradingError("TRADING_RECOVERY_PENDING")


def is_ready() -> bool:
    """供同步发送守卫读取内存屏障。"""
    return _ready and not _shutting_down


def is_closing() -> bool:
    """恢复探测不得重新开放正在关闭的进程。"""
    return _shutting_down


async def assert_no_unresolved(session_factory) -> None:
    """调用者持有共享锁；数据库不可读也不能授权自动交易。"""
    from ..db.agent_trade_repositories import AgentTradeRepository
    from sqlalchemy.exc import SQLAlchemyError
    ensure_ready()
    try:
        rows = await AgentTradeRepository(session_factory).list_unsettled()
    except SQLAlchemyError:
        set_ready(False)
        raise TradingError("STATE_STORE_UNAVAILABLE") from None
    if rows:
        raise TradingError("TRADE_INTENT_IN_FLIGHT")


async def assert_auto_idle() -> None:
    """任意非空托管占位均阻止聊天写入，包括未来的新状态。"""
    from ..ai_auto.manager import get_ai_trade_manager
    from ..live_auto.manager import get_live_auto_manager
    if (get_ai_trade_manager().status() is not None
            or get_live_auto_manager().group_status() is not None):
        raise TradingError("AUTO_TRADE_OWNS_ACCOUNT")


def check_chain(intent, unsettled) -> None:
    """只允许同账户同订单的原单链新增一个撤销请求。"""
    other = [row for row in unsettled if row.id != intent.id]
    if not other:
        return
    import json
    allowed = intent.action == "cancel" and all(
        row.action in {"open", "close"}
        and row.status in {"accepted", "partially_filled"}
        and row.account_id == intent.account_id
        and row.environment == intent.environment
        and json.loads(row.result_json or "{}").get("ordId")
        == intent.target_ord_id
        for row in other
    )
    if not allowed:
        raise TradingError("TRADE_INTENT_IN_FLIGHT")
