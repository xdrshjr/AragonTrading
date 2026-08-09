"""交易周期模式端点单测（ai-trade-holding-horizon-modes §6.1 · api 组）。

覆盖 `GET /ai-trade/horizon-profiles` 的形状与默认值、start 的四层解析语义、回执镜像必带两个派生量、
越界 422、目标区间倒置 422、总闸关闭时 start 成功但回执 `tradingHorizon = null`。

解析层（`_resolve_horizon`）不发任何网络请求、不建会话，故用例直接调它——把「解析语义」与「整组
启动前置门禁」分开验，后者已由 `test_ai_trade_group.py` 覆盖。
"""
from __future__ import annotations

from httpx import ASGITransport, AsyncClient

from app.api.ai_auto import _resolve_backtest_horizon, _resolve_horizon
from app.ai_auto.hold_horizon import rule_wire
from app.config import Settings
from app.db.session import dispose_engine, init_db
from app.schemas import AiTradeGroupStartIn, AiTradeHorizonIn


def _load_app():
    from app.main import app
    return app


async def test_horizon_profiles_endpoint_shape():
    """端点形状：五个模式 + 默认模式 + 两个解除阈系数；`custom` 条目除三个字符串外全为 null。"""
    await init_db()
    try:
        app = _load_app()
        async with AsyncClient(transport=ASGITransport(app=app),
                               base_url="http://t") as client:
            res = await client.get("/api/ai-trade/horizon-profiles", params={"bar": "1H"})
        assert res.status_code == 200
        body = res.json()
        assert body["defaultProfile"] == Settings().ai_trade_horizon_default_profile
        assert body["lossReleaseFrac"] == Settings().ai_trade_horizon_loss_release_frac
        ids = [p["id"] for p in body["profiles"]]
        assert ids == ["hourly_swing", "intraday_swing", "short_swing", "trend_hold", "custom"]
        custom = next(p for p in body["profiles"] if p["id"] == "custom")
        assert custom["bar"] is None and custom["minHoldSeconds"] is None
        assert custom["expectedHoldBars"] is None and custom["confirmWindowSeconds"] is None
        intraday = next(p for p in body["profiles"] if p["id"] == "intraday_swing")
        assert intraday["bar"] == "1H" and intraday["minHoldSeconds"] == 7200.0
    finally:
        await dispose_engine()


async def test_horizon_profiles_endpoint_is_not_gated_by_ai_switch(monkeypatch):
    """**不守 AI 总闸**：读预设表不下任何单，弹窗在总闸关闭时也要能把「有哪些模式」画出来。"""
    await init_db()
    try:
        from app import config as config_module

        monkeypatch.setattr(config_module, "get_settings",
                            lambda: Settings(ai_trade_enabled=False))
        app = _load_app()
        async with AsyncClient(transport=ASGITransport(app=app),
                               base_url="http://t") as client:
            res = await client.get("/api/ai-trade/horizon-profiles")
        assert res.status_code == 200
    finally:
        await dispose_engine()


def test_resolve_four_layer_semantics_and_receipt_mirror():
    """四层解析语义 + 回执镜像必带 `expectedHoldBars` / `confirmWindowSeconds`（[P1-9]/[P0-3]）。"""
    settings = Settings()
    assert _resolve_horizon(None, Settings(ai_trade_horizon_enabled=False), bar="1H") is None
    assert _resolve_horizon(AiTradeHorizonIn(enabled=False), settings, bar="1H") is None
    default = _resolve_horizon(None, settings, bar="1H")
    assert default.profile_id == settings.ai_trade_horizon_default_profile
    wire = rule_wire(default)
    assert wire["expectedHoldBars"] == 12 and wire["confirmWindowSeconds"] == 3600.0
    # 派生量按**用户实际选择的 bar** 现算，而不是预设自带的建议周期。
    on_5m = _resolve_horizon(AiTradeHorizonIn(profile="intraday_swing"), settings, bar="5m")
    assert on_5m.expected_hold_bars == 144


def test_backtest_resolver_adds_one_more_ops_switch():
    """回测侧在实盘语义之上再叠一层运维总闸（一键回到不装配的回测行为）。"""
    off = Settings(ai_trade_horizon_backtest_enabled=False)
    assert _resolve_backtest_horizon(None, off, bar="1H") is None
    assert _resolve_backtest_horizon(None, Settings(), bar="1H") is not None


def test_custom_all_defaults_receipt_is_a_no_op_contract():
    """`custom` 全默认的回执：三道闸参数全为「不约束」，`expectedHoldBars` 为 **null**（[P1-1]）。"""
    rule = _resolve_horizon(AiTradeHorizonIn(profile="custom"), Settings(), bar="1H")
    wire = rule_wire(rule)
    assert wire["minHoldSeconds"] == 0.0
    assert wire["exitConfirmRounds"] == 1
    assert wire["turnoverHysteresisK"] == 0.0
    assert wire["expectedHoldBars"] is None


def test_schema_rejects_out_of_range_and_inverted_targets():
    """schema 层 422（服务端只做缺省回填、不复夹）：越界与目标区间倒置各一条。"""
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        AiTradeHorizonIn(exitConfirmRounds=99)
    with pytest.raises(ValidationError):
        AiTradeHorizonIn(minHoldSeconds=-1)
    with pytest.raises(ValidationError):
        AiTradeHorizonIn(targetHoldMinSeconds=7200, targetHoldMaxSeconds=3600)
    with pytest.raises(ValidationError):
        AiTradeHorizonIn(profile="not_a_profile")


def test_group_start_body_accepts_trading_horizon():
    """启动体接得住该字段，且**缺省即开启**（老客户端 / 桌面旧包不发也默认装配）。"""
    body = AiTradeGroupStartIn(members=[{"instId": "BTC-USDT-SWAP", "allocationPct": 100}],
                               strategyIds=[1])
    assert body.tradingHorizon is None
    assert _resolve_horizon(body.tradingHorizon, Settings(), bar=body.bar) is not None
