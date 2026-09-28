"""共享准入屏障采取失败关闭。"""
import pytest
from app.agent_trading import admission


def test_readiness_starts_closed_and_shutdown_closes_it():
    admission.set_ready(False)
    with pytest.raises(Exception):
        admission.ensure_ready()
    admission.set_ready(True)
    admission.ensure_ready()
    admission.begin_shutdown()
    with pytest.raises(Exception):
        admission.ensure_ready()


@pytest.mark.asyncio
async def test_any_nonempty_auto_state_blocks(monkeypatch):
    from app.ai_auto.manager import get_ai_trade_manager
    from app.live_auto.manager import get_live_auto_manager
    monkeypatch.setattr(get_ai_trade_manager(), "status", lambda: {"status": "future_state"})
    monkeypatch.setattr(get_live_auto_manager(), "group_status", lambda: None)
    with pytest.raises(Exception):
        await admission.assert_auto_idle()
