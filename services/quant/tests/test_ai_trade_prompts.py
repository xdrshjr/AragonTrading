"""AI 自动交易 prompt 单测（ai-trade-reasoning-chain §五 R2）。

系统 prompt：声明「信号仅供参考、可推翻」+ 分步思维链 + 收尾即工具调用硬约束置于末尾（RC-4 近因位）。
决策 prompt：marketDigest 非空时含价格/成交量/因子三段、为 None 时不出现半截空段；信号板小标题含
「仅供参考」；末句为工具调用指令。纯离线。
"""
from __future__ import annotations

from app.ai_auto.prompts import (
    DECISION_TOOL_NAME,
    build_ai_trade_system_prompt,
    build_decision_prompt,
)


def _payload_with_digest() -> dict:
    return {
        "round": 1, "barTs": 1, "lastPx": 30000.0, "position": None,
        "sessionPnlUsd": 0.0, "budgetUsd": 100.0, "takeProfitUsd": None, "stopLossUsd": None,
        "signalBoard": [{"strategyId": 1, "name": "S", "stance": "flat",
                         "latestSignal": "open_long", "strength": 1.0, "reason": "r"}],
        "marketDigest": {
            "priceAction": {"trend": "up", "changePct": 1.2, "emaRelation": "fast_above",
                            "emaSlopePct": 0.4, "rangePos": 0.8, "swingHigh": 1.0, "swingLow": 0.0,
                            "consecUp": 3, "consecDown": 0,
                            "lastBar": {"o": 1.0, "h": 2.0, "l": 0.0, "c": 1.0, "bodyPct": 0.1}},
            "volume": {"last": 100.0, "sma": 80.0, "ratio": 1.25, "zscore": 0.5,
                       "trend": "rising", "spike": False},
            "factors": {"rsi14": {"value": 63.2, "label": "neutral"},
                        "macdHist": {"value": 12.4, "label": "bullish"},
                        "bollPctB": {"value": 0.82, "label": "upper_half"},
                        "atrPct": {"value": 1.1, "label": "normal"},
                        "rocPct": {"value": 2.3, "label": "positive"}}},
    }


def test_system_prompt_declares_signals_as_reference_and_step_by_step():
    p = build_ai_trade_system_prompt("BTC-USDT-SWAP", "1H", 100.0, 3.0)
    assert "参考" in p and "推翻" in p
    assert "一步一步" in p or "思维链" in p
    assert "① 价格走势" in p and "⑧ 结论" in p          # 8 步分步研判


def test_system_prompt_tool_close_constraint_is_last():
    """RC-4：收尾即工具调用硬约束必须存在，且置于系统 prompt 纪律的最末位（近因效应）。"""
    p = build_ai_trade_system_prompt("BTC-USDT-SWAP", "1H", 100.0, 3.0).rstrip()
    assert "必须且只能调用一次" in p and DECISION_TOOL_NAME in p
    assert p.endswith("决策工具。**")                    # 硬约束恰为最后一句


def test_decision_prompt_includes_digest_segments():
    text = build_decision_prompt(_payload_with_digest())
    assert "价格走势" in text and "趋势=up" in text
    assert "成交量" in text and "放缩趋势=rising" in text
    assert "技术因子" in text and "RSI14=" in text and "bullish" in text
    assert "仅供参考" in text                            # 信号板小标题改写


def test_decision_prompt_last_line_is_tool_call_instruction():
    """RC-4：决策 prompt 末句为「逐步研判后必须调用决策工具」（近因位）。"""
    text = build_decision_prompt(_payload_with_digest()).rstrip()
    last_line = text.splitlines()[-1]
    assert DECISION_TOOL_NAME in last_line
    assert text.endswith("不得只输出研判文字。")


def test_decision_prompt_omits_digest_when_absent():
    """marketDigest 缺省时不出现价格/成交量/因子半截空段（保持 prompt 纯净）。"""
    payload = _payload_with_digest()
    del payload["marketDigest"]
    text = build_decision_prompt(payload)
    assert "价格走势" not in text
    assert "成交量" not in text
    assert "技术因子" not in text


def test_decision_prompt_omits_digest_when_none():
    payload = _payload_with_digest()
    payload["marketDigest"] = None
    text = build_decision_prompt(payload)
    assert "价格走势" not in text and "技术因子" not in text
