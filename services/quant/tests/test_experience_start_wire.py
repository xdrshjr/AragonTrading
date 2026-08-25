"""启动请求与续跑的经验模式契约（A-18 / A-19）。

**续跑的一半无需额外代码**：`resume.py` 的 S2 用 `start_wire` 原文重跑 `build_start_request`，
而 `start_wire = body.model_dump(mode="json")` —— 只要 `experience` 键在原文里，冷续跑就自动
同配置。这条用例把「在原文里」这件事钉死：漏了它，用户暂停再继续之后，Agent 会悄悄换成
另一套经验配置，而界面上什么都不会变。
"""
from __future__ import annotations

from app.ai_auto.experience.rules import resolve_experience
from app.config import Settings
from app.schemas import AiBacktestStartIn, AiTradeGroupStartIn

_BODY = {
    "members": [{"instId": "BTC-USDT-SWAP", "allocationPct": 100.0}],
    "bar": "15m", "strategyIds": [1], "contextRefreshRounds": 10,
}


def test_a18_absent_key_means_server_default_and_explicit_false_means_off():
    """A-18 · 整键缺省 ⇒ 按 `default_on` 装配；`{"enabled": false}` ⇒ 不装配。

    「老客户端没发这个字段」与「用户亲手取消了勾选」是两件不同的事。合成一个判据，用户就再也
    关不掉这个功能，而界面上那个复选框看起来是生效的——本仓库已在 `baseRules` / `profitGuard` /
    `tradingHorizon` 上踩过三次。
    """
    settings = Settings()
    absent = AiTradeGroupStartIn(**_BODY)
    assert absent.experience is None
    assert resolve_experience(None, settings) is not None

    off = AiTradeGroupStartIn(**_BODY, experience={"enabled": False})
    assert off.experience is not None and off.experience.enabled is False
    assert resolve_experience(off.experience.model_dump(), settings) is None


def test_a19_start_wire_carries_the_key_so_resume_reparses_identically():
    """A-19 · `model_dump(mode="json")`（= `start_wire`）带 `experience` ⇒ 续跑逐字段相同。"""
    body = AiTradeGroupStartIn(**_BODY, experience={"enabled": True, "scope": "global",
                                                    "write": False})
    wire = body.model_dump(mode="json")
    assert wire["experience"] == {"enabled": True, "scope": "global", "write": False}

    settings = Settings()
    first = resolve_experience(body.experience.model_dump(), settings)
    replayed = resolve_experience(AiTradeGroupStartIn(**wire).experience.model_dump(), settings)
    assert first == replayed
    assert first is not None and first.scope == "global" and first.write is False


def test_write_flag_is_the_and_of_user_intent_and_operator_switch():
    """写闸取「用户意愿 ∧ 运维总闸」——两者是**与**而不是或。

    只读模式（用经验但不沉淀）是用户的合法选择；但运维关掉写入时，用户勾了也不写。
    """
    settings = Settings(ai_trade_experience_write_enabled=False)
    rule = resolve_experience({"enabled": True, "write": True}, settings)
    assert rule is not None and rule.write is False


def test_backtest_start_in_accepts_the_same_field():
    """回测启动体接住同一字段（v1 恒不装配，但 wire 形状两侧同源，下一轮开放时不必改协议）。"""
    body = AiBacktestStartIn(instId="BTC-USDT-SWAP", bar="15m", strategyIds=[1],
                             startTs=1, endTs=2, experience={"enabled": True})
    assert body.experience is not None and body.experience.enabled is True
