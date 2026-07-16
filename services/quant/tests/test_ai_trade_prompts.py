"""AI 自动交易 prompt 单测（ai-trade-reasoning-chain §五 R2）。

系统 prompt：声明「信号仅供参考、可推翻」+ 分步思维链 + 收尾即工具调用硬约束置于末尾（RC-4 近因位）。
决策 prompt：marketDigest 非空时含价格/成交量/因子三段、为 None 时不出现半截空段；信号板小标题含
「仅供参考」；末句为工具调用指令。纯离线。
"""
from __future__ import annotations

from app.ai_auto.decider import TradeDecision
from app.ai_auto.prompts import (
    DECISION_TOOL_NAME,
    REFLECTION_TOOL_NAME,
    build_ai_trade_system_prompt,
    build_decision_prompt,
    build_reflection_prompt,
    build_reflection_system_prompt,
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


def _multi_timeframe_context() -> dict:
    return {
        "trendAlignment": {"status": "mixed"},
        "nearestSupport": {"price": 29000.0, "distancePct": 3.33, "bars": ["1D"]},
        "nearestResistance": {"price": 31000.0, "distancePct": 3.33, "bars": ["1H"]},
        "frames": [
            {"bar": "1H", "sampleCount": 80, "stale": False,
             "regime": {"primary": "trend_down"},
             "digest": {"priceAction": {"rangePos": 0.2},
                        "factors": {"rsi14": {"value": 28, "label": "oversold"}}}},
            {"bar": "1D", "sampleCount": 90, "stale": True,
             "regime": {"primary": "trend_up"},
             "digest": {"priceAction": {"rangePos": 0.8},
                        "factors": {"rsi14": {"value": 72, "label": "overbought"}}}},
        ],
        "missingBars": ["12H", "4H"],
        "staleBars": ["1D"],
    }


def test_system_prompt_declares_signals_as_reference_and_step_by_step():
    p = build_ai_trade_system_prompt("BTC-USDT-SWAP", "1H", 100.0, 3.0)
    assert "参考" in p and "推翻" in p
    assert "一步一步" in p or "思维链" in p
    assert "① 价格走势" in p and "⑧ 结论" in p          # 8 步分步研判
    assert "1D → 12H → 4H → 1H" in p
    assert "高周期" in p and "优先 hold" in p
    assert "超买/超卖" in p and "不是单独开仓指令" in p
    assert "陈旧" in p and "不得补猜" in p


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


def test_decision_prompt_formats_multi_timeframe_long_to_short():
    payload = _payload_with_digest()
    payload["multiTimeframeContext"] = _multi_timeframe_context()
    text = build_decision_prompt(payload)
    assert "中线背景" in text and "整体=mixed" in text
    assert text.index("1D：") < text.index("1H：")
    assert "数据陈旧" in text
    assert "缺失周期=12H/4H" in text
    assert "候选支撑" in text and "候选阻力" in text


def test_decision_prompt_reports_all_context_missing_without_guessing():
    payload = _payload_with_digest()
    payload["multiTimeframeContext"] = {
        "frames": [], "missingBars": ["1D", "12H", "4H", "1H"]}
    text = build_decision_prompt(payload)
    assert "中线背景暂不可用，已回退基础周期" in text
    assert "不得补猜" in text


# ---------- 下单前二次反思 prompt（ai-trade-pre-trade-reflection §4.2）----------
def test_reflection_system_prompt_is_reviewer_role_only_tightens():
    p = build_reflection_system_prompt()
    assert "风控复核官" in p and "批判性地复核" in p
    assert "只能" in p and ("收紧" in p or "调小仓位" in p)
    assert "无权" in p and ("放大" in p or "改变方向" in p)
    assert "必须且只能调用一次" in p and REFLECTION_TOOL_NAME in p


def test_reflection_prompt_carries_evidence_and_proposed_decision():
    """反思输入含本轮证据（复用决策证据装配）+ 被复核的提议决策 + 反思工具指令。"""
    payload = _payload_with_digest()
    decision = TradeDecision("open_long", 0.5, 0.8, "顺势开多")
    text = build_reflection_prompt(payload, decision)
    assert "价格走势" in text and "技术因子" in text      # 复用决策证据装配
    assert "待复核的交易决策" in text and "开多" in text and "顺势开多" in text
    assert REFLECTION_TOOL_NAME in text
    # 明确中和原决策工具指令，避免复核官误调 submit_trade_decision。
    assert f"不要**调用 {DECISION_TOOL_NAME}" in text or "与你无关" in text
    assert text.rstrip().endswith("也不得调用其它工具。")


# ---------- F-3 价带比例单一真源化（ai-trade-limit-orders-fix §2.4，先红后绿）----------
# 冻结字面量（P1-3 教训：绝不与模块常量自比，防「改坏常量后自比恒真」空测）——
# 与本特性合入前 `_LIMIT_TOOL_DESCRIPTION_SUFFIX` 的今日全文逐字相同。
_FROZEN_LIMIT_SUFFIX_AT_2PCT = (
    "另可给 orderType 选择委托方式：market（市价，立即成交、价格不可控、吃掉买卖价差与滑点）或 "
    "limit（限价挂单，按你指定的 limitPrice 排队等待成交，**可能不成交**）。orderType=limit 时"
    "必须给 limitPrice（USDT），且须在最新价 ±2% 以内，超出会被系统拒绝并要求你重新研判。"
    "第五个动作 cancel_order 用于**撤回你自己此前挂出、尚未成交的限价委托**——它既不开仓也不平仓，"
    "无挂单时为安全的空操作。若行情已走反、你那笔挂单再等下去已无意义，就用 cancel_order 撤回它。"
)


def test_limit_prompt_band_default_binary_identical():
    """默认 band=0.02 的渲染与冻结字面量**逐字节相同**（默认文案零漂移的硬门禁）。"""
    from app.ai_auto.prompts import _limit_tool_description_suffix
    assert _limit_tool_description_suffix(0.02) == _FROZEN_LIMIT_SUFFIX_AT_2PCT


def test_limit_prompt_band_follows_setting():
    """band=0.05 时系统 prompt / 工具描述 / schema 字段描述同步渲染「±5%」且不含「±2%」。"""
    from app.ai_auto.prompts import (
        build_decision_tool_description,
        build_decision_tool_schema,
    )
    desc = build_decision_tool_description(True, limit_band_pct=0.05)
    assert "±5%" in desc and "±2%" not in desc
    schema = build_decision_tool_schema(0.0, allow_limit=True, limit_band_pct=0.05)
    assert "±5%" in schema["properties"]["limitPrice"]["description"]
    assert "±2%" not in schema["properties"]["limitPrice"]["description"]
    sysp = build_ai_trade_system_prompt("BTC-USDT-SWAP", "1H", 100.0, 3.0,
                                        allow_limit=True, limit_band_pct=0.05)
    assert "±5%" in sysp and "±2%" not in sysp


def test_limit_prompt_band_fmt_renders_trailing_zero_free():
    """`_fmt_band_pct`：0.02→"2%"、0.015→"1.5%"（%g 去尾零，默认值渲染与今日文案逐字节相同）。"""
    from app.ai_auto.prompts import _fmt_band_pct
    assert _fmt_band_pct(0.02) == "2%"
    assert _fmt_band_pct(0.015) == "1.5%"
    assert _fmt_band_pct(0.05) == "5%"
