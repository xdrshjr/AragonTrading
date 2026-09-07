"""AI 自动交易 prompt 单测（ai-trade-reasoning-chain §五 R2）。

系统 prompt：声明「信号仅供参考、可推翻」+ 分步思维链 + 收尾即工具调用硬约束置于末尾（RC-4 近因位）。
决策 prompt：marketDigest 非空时含价格/成交量/因子三段、为 None 时不出现半截空段；信号板小标题含
「仅供参考」；末句为工具调用指令。纯离线。
"""
from __future__ import annotations

import hashlib

import pytest

from app.ai_auto.decider import TradeDecision
from app.ai_auto.prompts import (
    DECISION_TOOL_DESCRIPTION,
    DECISION_TOOL_NAME,
    DECISION_TOOL_SCHEMA,
    REFLECTION_TOOL_NAME,
    _format_position,
    build_ai_trade_system_prompt,
    build_decision_prompt,
    build_decision_tool_description,
    build_decision_tool_schema,
    build_reflection_prompt,
    build_reflection_system_prompt,
)
from app.ai_auto.prompts_en import SystemPromptOptions, SystemPromptTexts
from app.ai_auto.reasoning_protocol import (
    build_deliberation_protocol,
    build_review_protocol,
    profile_by_name,
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


# ---------- F1 反思 Prompt 校准（ai-trade-reflection-hold-inertia-fix §六-1，先红后绿复现）----------
def test_reflection_prompt_no_blanket_abort():
    """不对称保守文案已删（「宁可错过」「任何足以令你不放心」缺席）；分级复核新锚点在场
    （实质性缺陷 / 具体证据 / 机会成本 / 「优先」收缩）——system 与 user prompt 双面覆盖。"""
    sysp = build_reflection_system_prompt()
    userp = build_reflection_prompt(_payload_with_digest(),
                                    TradeDecision("open_long", 0.5, 0.8, "顺势开多"))
    for text in (sysp, userp):
        assert "宁可错过" not in text
        assert "任何足以令你不放心" not in text
        assert "实质性缺陷" in text
        assert "具体证据" in text
        assert "优先" in text                       # 收缩优先于否决
        assert "同样是损失" in text                 # 机会成本对称
    assert "机会成本" in sysp
    from app.ai_auto.prompts import REFLECTION_TOOL_DESCRIPTION
    assert "宁可错过" not in REFLECTION_TOOL_DESCRIPTION
    assert "实质性缺陷" in REFLECTION_TOOL_DESCRIPTION


def test_reflection_system_prompt_keeps_frozen_safety_anchors():
    """F1 只改复核标准，全部既有安全锚点原样保留（含 close 否决须有极强理由的新注记）。"""
    p = build_reflection_system_prompt()
    assert "风控复核官" in p and "批判性地复核" in p
    assert "只能" in p and "收紧" in p
    assert "无权" in p and "放大" in p
    assert "必须且只能调用一次" in p and REFLECTION_TOOL_NAME in p
    assert "否决 close 等于延长敞口" in p


# ---------- F4a trigger=interval 标注（hold-inertia-fix §2.4，零回归钉）----------
def test_decision_prompt_marks_interval_trigger_conditionally():
    """trigger=interval：首行追加「触发源=定时唤醒」标注，且措辞条件化（「若…相同」，R-01）。"""
    payload = _payload_with_digest()
    payload["trigger"] = "interval"
    text = build_decision_prompt(payload)
    first_line = text.splitlines()[0]
    assert "触发源=定时唤醒" in first_line
    assert "墙钟到点" in first_line
    assert "**若**本轮行情数据与上一决策轮相同" in first_line   # 条件化措辞，非断言式「数据未变」
    assert "不构成新的独立证据" in first_line


def test_decision_prompt_candle_and_legacy_have_no_interval_mark():
    """trigger="candle" / 缺失 trigger / 其他值 → 不含「定时唤醒」（candle 路径文案零漂移）。"""
    for trigger in ("candle", None, "other"):
        payload = _payload_with_digest()
        if trigger is not None:
            payload["trigger"] = trigger
        text = build_decision_prompt(payload)
        assert "定时唤醒" not in text
        assert "触发源" not in text


# ---------- N4 连续观望自察注记（ai-trade-hold-inertia-observability §六-3）----------
def test_decision_prompt_hold_streak_note():
    """payload["holdStreak"]=12 → 输出含「连续观望自察」+ 轮数 + 对称锚点（既打断路径依赖、
    也明确不是必须开仓的指令、观望须指名具体反对证据）。"""
    payload = _payload_with_digest()
    payload["holdStreak"] = 12
    text = build_decision_prompt(payload)
    assert "连续观望自察" in text
    assert "12 轮" in text
    assert "这也不是必须开仓的指令" in text          # 对称措辞：不是过度交易的推手
    assert "若研判后仍选择观望" in text              # 继续观望须指名具体证据
    assert "反对开仓的" in text and "具体证据" in text
    assert "不得以「此前观望被验证正确」作为理由" in text


def test_decision_prompt_hold_streak_absent_is_frozen():
    """无 holdStreak 键 / 非正整数值 → 输出不含「连续观望自察」（F4a 冻结钉：键缺失即逐字节不变），
    且既有锚点（末句工具调用指令）原样通过。"""
    baseline = build_decision_prompt(_payload_with_digest())
    assert "连续观望自察" not in baseline
    assert baseline.rstrip().endswith("不得只输出研判文字。")   # 既有收尾锚点零回归
    for bad in (0, -3, None, "12", 1.5):
        payload = _payload_with_digest()
        payload["holdStreak"] = bad
        assert build_decision_prompt(payload) == baseline       # 非正整数值同样逐字节冻结


# ---------- F3 摘要器反自我强化指令（hold-inertia-fix §2.3）----------
def test_summary_prompt_carries_anti_reinforcement_instructions():
    """head 指令含三条新增（被否决意图对照验证 / 空仓零回撤不是成绩 / 折叠条目只算一次），原句保留。"""
    from app.ai_auto.prompts import build_summary_prompt
    text = build_summary_prompt(None, [])
    assert "被否决的开仓意图" in text
    assert "系统故障保守跳过不代表模型判断" in text
    assert "零回撤不是成绩" in text
    assert "禁止把长期观望总结为表现完美" in text
    assert "repeatedRounds" in text and "只算**一次**观望" in text
    assert "只有存在已结算收益或明确后续事实" in text
    assert "否则写『待验证』" in text
    assert "不能据未来最高价宣称错失利润" in text


def test_summary_system_prompt_requires_recording_omissions():
    """system 指令追加「记录做了什么与没做什么、保持中性、不得美化观望」（原句保留）。"""
    from app.ai_auto.prompts import build_summary_system_prompt
    p = build_summary_system_prompt()
    assert "没做什么" in p and "不得美化观望" in p
    assert "摘要器" in p and "简洁" in p                        # 原句保留（零回归）


def test_system_prompt_hold_is_active_not_default():
    """决策纪律 #2 追加反路径依赖句（F3.4）；#5 收尾硬约束仍为最后一句（不动既有断言语义）。"""
    p = build_ai_trade_system_prompt("BTC-USDT-SWAP", "1H", 100.0, 3.0)
    assert "观望是需要当前证据支撑的主动决策" in p
    assert "不要因历史摘要中长期观望而路径依赖" in p
    assert p.rstrip().endswith("决策工具。**")                  # 收尾硬约束不受影响


# ---------- F4b 摘要素材折叠（hold-inertia-fix §六-3）----------
def _journal_entry(round_no: int, *, trigger="interval", action="hold", traded=False,
                   reflection=None) -> dict:
    return {"round": round_no, "signalBoard": [],
            "proposal": {"action": action},
            "facts": {"position": None, "asOfTs": round_no},
            "evidenceFingerprint": "same-current-evidence",
            "decision": {"action": action, "sizeFraction": None, "confidence": 0.7,
                         "reason": "r", "orderType": "market", "limitPrice": None},
            "executed": {"ok": True, "action": action, "traded": traded,
                         "positionAfter": "flat"},
            "positionAfter": "flat", "sessionPnl": float(round_no), "analysis": None,
            "trigger": trigger, "reflection": reflection}


def test_compact_journal_folds_consecutive_interval_holds():
    """连续 5 条 interval-hold → 折叠为一条 repeatedRounds=5（保留末条 decision/sessionPnl）。"""
    from app.ai_auto.prompts import compact_journal_for_summary
    journal = [_journal_entry(i) for i in range(1, 6)]
    out = compact_journal_for_summary(journal)
    assert len(out) == 1
    folded = out[0]
    assert folded["repeatedRounds"] == 5
    assert folded["rounds"] == "1-5"
    assert folded["trigger"] == "interval"
    assert "重复定时唤醒" in folded["note"]
    assert folded["decision"] == journal[-1]["decision"]
    assert folded["sessionPnl"] == 5.0
    assert journal[0]["round"] == 1                             # 纯函数：不改入参


def test_compact_journal_never_folds_protected_entries():
    """开/平仓轮、candle 轮、含 reflection 注记轮、legacy 无 trigger 条目永不折叠；
    夹在中间的保护轮切断连续段（前后两段分别按各自长度处置）。"""
    from app.ai_auto.prompts import compact_journal_for_summary
    protected = [
        _journal_entry(3, action="open_long", traded=True),      # 开仓轮
        _journal_entry(3, trigger="candle"),                     # candle 轮
        _journal_entry(3, reflection={"vetoed": True}),          # 含反思注记轮
        _journal_entry(3, trigger=None),                         # legacy/回测（无 trigger）
    ]
    for middle in protected:
        journal = [_journal_entry(1), _journal_entry(2), middle,
                   _journal_entry(4), _journal_entry(5)]
        out = compact_journal_for_summary(journal)
        # 前段 2 条折叠为 1、保护轮原样、后段 2 条折叠为 1 → 共 3 条
        assert len(out) == 3
        assert out[0]["repeatedRounds"] == 2 and out[0]["rounds"] == "1-2"
        assert out[1] is middle
        assert out[2]["repeatedRounds"] == 2 and out[2]["rounds"] == "4-5"


def test_compact_journal_keeps_single_foldable_entry_as_is():
    """单条可折叠条目保留原样（仅连续 ≥2 条才折叠）；混合序列端到端形状正确。"""
    from app.ai_auto.prompts import compact_journal_for_summary
    single = _journal_entry(2)
    journal = [_journal_entry(1, trigger="candle"), single, _journal_entry(3, trigger="candle")]
    out = compact_journal_for_summary(journal)
    assert out == journal
    assert out[1] is single


def test_summary_material_folds_repeated_interval_holds():
    """§六-3 端到端：5 条连续 interval-hold 经 build_summary_prompt 后素材含 repeatedRounds=5，
    不再出现 5 条独立 round 条目。"""
    from app.ai_auto.prompts import build_summary_prompt
    journal = [_journal_entry(i) for i in range(1, 6)]
    text = build_summary_prompt(None, journal)
    assert '"repeatedRounds": 5' in text
    assert '"rounds": "1-5"' in text
    for r in range(1, 6):
        assert f'"round": {r},' not in text                     # 独立轮条目已被折叠


# ---------- 【基础策略】prompt 零回归冻结（ai-trade-base-strategy-ma-band §6.1 用例 18/19）----------
def test_system_prompt_byte_identical_without_base_rules():
    """不传 `base_rules_text`（默认空垫片）⇒ 系统 prompt 与传空串**逐字节相等**（P1-3 零回归）。"""
    baseline = build_ai_trade_system_prompt("BTC-USDT-SWAP", "1H", 100.0, 3.0)
    assert baseline == build_ai_trade_system_prompt(
        "BTC-USDT-SWAP", "1H", 100.0, 3.0, base_rules_text="")
    assert "最高优先级铁律" not in baseline


def test_system_prompt_injects_charter_between_principle_and_discipline():
    """非空铁律章插在「首要原则」之后、「决策纪律」之前——位置本身就是优先级声明。"""
    from app.ai_auto.base_rules import MaBandRule, format_charter

    text = build_ai_trade_system_prompt("BTC-USDT-SWAP", "1H", 100.0, 3.0,
                                        base_rules_text=format_charter(MaBandRule()))
    assert text.index("首要原则") < text.index("最高优先级铁律") < text.index("决策纪律：")


def test_decision_prompt_unchanged_without_base_rules_key():
    """payload 无 `baseRules` 键 ⇒ 决策 prompt 渲染逐字节不变（回测老 payload / 规则未装配）。"""
    payload = _payload_with_digest()
    baseline = build_decision_prompt(payload)
    assert "基础策略" not in baseline
    with_band = build_decision_prompt({**payload, "baseRules": {"maBand": {
        "ruleId": "ma_band_entry", "period": 20, "stdMult": 2.0, "tolerance": 0.15,
        "upper": 200.0, "mid": 150.0, "lower": 100.0, "bandWidthPct": 66.67,
        "pctB": 0.5, "zone": "mid", "directional": False}}})
    assert "基础策略 · MA 通道开单约束（铁律）" in with_band
    assert "允许开仓" in with_band and "合规开单价区间" in with_band


# ---------- 利润保护 prompt 零回归冻结 + 三段渲染（ai-trade-profit-protection §7.2 G-2）----------
def test_system_prompt_byte_identical_without_profit_guard():
    """不传 `profit_guard_text`（默认空垫片）⇒ 系统 prompt 与传空串**逐字节相等**（零回归）。"""
    baseline = build_ai_trade_system_prompt("BTC-USDT-SWAP", "1H", 100.0, 3.0)
    assert baseline == build_ai_trade_system_prompt(
        "BTC-USDT-SWAP", "1H", 100.0, 3.0, profit_guard_text="")
    assert "利润保护层" not in baseline


def test_system_prompt_stacks_charters_in_priority_order():
    """两章共存时：基础策略铁律章在前、利润保护章在后，两者均在「决策纪律」之前。"""
    from app.ai_auto.base_rules import MaBandRule, format_charter
    from app.ai_auto.profit_guard import ProfitGuardRule
    from app.ai_auto.profit_guard_text import format_profit_guard_charter

    text = build_ai_trade_system_prompt(
        "BTC-USDT-SWAP", "1H", 100.0, 3.0,
        base_rules_text=format_charter(MaBandRule()),
        profit_guard_text=format_profit_guard_charter(ProfitGuardRule()))
    assert text.index("最高优先级铁律") < text.index("利润保护层") < text.index("决策纪律：")
    assert "决策失误" in text and "不是一条你可以依赖的保证" in text


def test_decision_prompt_unchanged_without_profit_guard_key():
    """payload 无 `profitGuard` 键 ⇒ 决策 prompt 渲染逐字节不变（回测 payload / 规则未装配）。"""
    payload = _payload_with_digest()
    baseline = build_decision_prompt(payload)
    assert "利润保护" not in baseline

    with_guard = build_decision_prompt({**payload, "profitGuard": _guard_snapshot()})
    assert "利润保护 · 移动止盈 / 保本（系统强制执行）" in with_guard
    assert "有效退出线" in with_guard and "峰值浮盈" in with_guard
    # 保护段紧跟持仓段之后、会话盈亏段之前（位置即优先级：退出线先于会话阈值被看到）。
    position_line = _format_position(payload.get("position"))
    assert (with_guard.index(position_line) < with_guard.index("利润保护")
            < with_guard.index("会话累计盈亏"))


def test_decision_prompt_profit_hold_note_is_opt_in():
    """自察段由会话按阈值注入 `profitHoldRounds`：键缺失即整段省略（零回归）。"""
    payload = {**_payload_with_digest(), "profitGuard": _guard_snapshot()}
    assert "盈利持仓自察" not in build_decision_prompt(payload)

    noted = build_decision_prompt({**payload, "profitHoldRounds": 3})
    assert "盈利持仓自察：你已连续 3 轮" in noted
    assert "这不是必须平仓的指令" in noted


def _guard_snapshot() -> dict:
    """一份自洽的保护快照（entry 0.07245 / lev 5 / peak 0.0732 / giveback 0.40）。"""
    from app.ai_auto.profit_guard import (
        PositionFacts,
        ProfitGuardRule,
        advance_state,
        build_snapshot,
    )

    rule = ProfitGuardRule()
    entry, lev, qty = 0.07245, 5.0, 402.7
    def _facts(px, wall):
        return PositionFacts(seq=1, side="long", entry_px=entry, leverage=lev, qty_coin=qty,
                             mark_px=px, now_wall=wall, bars_in_position=4,
                             exchange_tp_px=entry * 1.10, exchange_sl_px=entry * 0.95,
                             exchange_oco_source="trade")
    state = advance_state(None, _facts(entry, 0.0))
    state = advance_state(state, _facts(entry * (1 + 0.0518 / lev), 60.0))
    facts = _facts(entry * (1 + 0.0414 / lev), 930.0)
    state = advance_state(state, facts)
    return build_snapshot(rule, state, facts)



# ---------- 承诺退出线 schema / 描述 / 宪章（commitment C1/C3）----------
def test_hold_exit_tool_description_suffix_gated():
    """G-2：allow_hold_exit=False ⇒ 工具描述恒等模块常量；True ⇒ 尾部追加承诺退出线说明。"""
    from app.ai_auto.prompts import (
        DECISION_TOOL_DESCRIPTION,
        build_decision_tool_description,
    )

    assert build_decision_tool_description(False) is DECISION_TOOL_DESCRIPTION
    assert build_decision_tool_description(False, allow_hold_exit=False) is DECISION_TOOL_DESCRIPTION
    text = build_decision_tool_description(False, allow_hold_exit=True)
    assert text != DECISION_TOOL_DESCRIPTION
    assert text.startswith(DECISION_TOOL_DESCRIPTION)          # 基础文案一字不改
    assert "holdExitPx" in text and "只能随你收紧、不能放松" in text


def test_hold_exit_schema_property_shape_and_optional():
    """C1 §2.1.2：holdExitPx 为可选 number、exclusiveMinimum=0；required 不变（承诺永远可选）。"""
    from app.ai_auto.prompts import build_decision_tool_schema

    schema = build_decision_tool_schema(0.0, allow_hold_exit=True)
    prop = schema["properties"]["holdExitPx"]
    assert prop["type"] == "number" and prop["exclusiveMinimum"] == 0
    assert schema["required"] == ["action", "reason"]


def test_hold_exit_composes_with_limit_without_clobbering():
    """C1：allow_limit 与 allow_hold_exit 同开时两组字段并存、动作枚举含 cancel_order、模块常量不被污染。"""
    from app.ai_auto.prompts import DECISION_TOOL_SCHEMA, build_decision_tool_schema

    schema = build_decision_tool_schema(0.0, allow_limit=True, allow_hold_exit=True)
    props = schema["properties"]
    assert "holdExitPx" in props and "limitPrice" in props and "orderType" in props
    assert "cancel_order" in props["action"]["enum"]
    assert "holdExitPx" not in DECISION_TOOL_SCHEMA["properties"]      # 模块常量零污染


def test_charter_commitment_section_gated_on_commit_enabled():
    """G-2：宪章承诺段仅 commit_enabled=True 时出现（关闭逐字节回退 v2）。"""
    from app.ai_auto.profit_guard import ProfitGuardRule
    from app.ai_auto.profit_guard_text import format_profit_guard_charter

    off = format_profit_guard_charter(ProfitGuardRule(commit_enabled=False))
    on = format_profit_guard_charter(ProfitGuardRule(commit_enabled=True))
    assert "你许下的退出线会被兑现" not in off
    assert "你许下的退出线会被兑现" in on and "只能随你收紧、不能放松" in on
    assert on.startswith(off)                                  # 承诺段是纯追加，基础宪章一字不改


# ---------- 唤醒感知上下文（ai-trade-wake-aware-context §6.2 G-1）----------
_WAKE = {
    "kind": "price_breakout", "trigger": "interval", "executionBar": "30m",
    "sinceLastRoundS": 182.0, "priceMovePct": 0.42, "priceMoveAtr": 0.91,
    "moveThresholdAtr": 0.5, "moveThresholdPct": 0.0005, "horizonScale": 0.316,
    "moveThresholdAtrEff": 0.158, "skipStreak": 0, "barAdvanced": False,
    "restingAgeS": None, "guardStatus": None, "lastGuardStatus": None, "alsoTrue": [],
}
_TACTICAL = {
    "executionBar": "30m", "wakeIntervalS": 180, "asOfTs": 1_700_000_000,
    "executionPrimary": "trend_up", "alignment": "aligned",
    "changeSinceLastRound": {"pct": 0.42, "atrMult": 0.91, "atrBar": "30m"},
    "frames": [{"bar": "5m", "sampleCount": 120, "closeTs": 1_700_000_000, "ageSeconds": 42,
                "stale": False, "closedBars": 1, "regime": {"primary": "trend_up"},
                "digest": {"priceAction": {"trend": "up", "changePct": 0.42},
                           "volume": {"ratio": 1.31},
                           "factors": {"rsi14": {"value": 58, "label": "neutral"}}}}],
    "missingBars": [], "staleBars": [], "maxChars": 700,
}


def test_g1_decision_prompt_is_byte_identical_when_all_three_switches_are_off():
    """三总闸全关 ⇒ 四个新键整键缺席 ⇒ 决策 prompt 与本特性合入前**逐字节**相同。"""
    import hashlib

    payload = _payload_with_digest()
    baseline = build_decision_prompt(payload)
    assert "wake" not in payload and "tacticalContext" not in payload
    assert "战术周期" not in baseline
    assert "唤醒原因" not in baseline
    # 冻结基线：任何让「关闸也变字」的改动都会打翻这个哈希
    assert hashlib.sha256(baseline.encode("utf-8")).hexdigest() == hashlib.sha256(
        build_decision_prompt(_payload_with_digest()).encode("utf-8")).hexdigest()


def test_g1_system_prompt_is_byte_identical_without_tactical_bars_text():
    args = ("BTC-USDT-SWAP", "30m", 100.0, 3.0)
    assert build_ai_trade_system_prompt(*args) == build_ai_trade_system_prompt(
        *args, tactical_bars_text="")


def test_system_prompt_inserts_the_timing_clause_only_when_bars_are_injected():
    text = build_ai_trade_system_prompt("BTC-USDT-SWAP", "30m", 100.0, 3.0,
                                        tactical_bars_text="5m → 1m")
    assert "再用战术周期（5m → 1m）核对执行时机" in text
    assert "不得单独反转中线判断" in text
    # 插点必须在中线背景子句之后、「再依次思考」之前（时间尺度由远及近单调收缩）
    assert text.index("候选支撑阻力与超买/超卖") < text.index("再用战术周期")
    assert text.index("再用战术周期") < text.index("再依次思考：")


def test_wake_reason_replaces_the_legacy_trigger_suffix_on_the_first_line():
    payload = {**_payload_with_digest(), "trigger": "interval", "wake": _WAKE}
    first_line = build_decision_prompt(payload).split("\n")[0]
    assert "唤醒原因=价格异动" in first_line
    assert "触发源=定时唤醒" not in first_line      # 绝不与旧后缀并存（否则同一行说两遍）


def test_legacy_trigger_suffix_survives_when_the_wake_key_is_absent():
    payload = {**_payload_with_digest(), "trigger": "interval"}
    assert "触发源=定时唤醒" in build_decision_prompt(payload).split("\n")[0]


def test_tactical_section_sits_between_the_digest_and_the_signal_board():
    payload = {**_payload_with_digest(), "wake": _WAKE,
               "tacticalContext": _TACTICAL, "tacticalTier": "brief"}
    text = build_decision_prompt(payload)
    assert text.index("技术因子：") < text.index("战术周期（")
    assert text.index("战术周期（") < text.index("策略信号板")
    assert "- 5m：体制=trend_up" in text


def test_repeat_disclosure_compresses_both_execution_layers():
    payload = {**_payload_with_digest(), "wake": _WAKE, "contextDisclosure": "repeat",
               "tacticalContext": _TACTICAL, "tacticalTier": "brief",
               "multiTimeframeContext": {
                   "frames": [{"bar": "4H"}], "trendAlignment": {"status": "bullish"},
                   "nearestSupport": None, "nearestResistance": None}}
    text = build_decision_prompt(payload)
    assert "与本根首次研判相同" in text
    assert "本轮最新变化见战术周期" in text
    assert "价格走势：趋势=" not in text            # 三行证据已被压成一行
    assert "（各周期明细与本根首次研判相同，未重复列出）" in text


def test_repeat_disclosure_without_tactical_never_points_at_an_absent_section():
    """压缩总闸开、战术段缺席（总闸关 / 1m 执行无候选 / 取数全失败）⇒ 不得指向不存在的证据段。"""
    payload = {**_payload_with_digest(), "wake": _WAKE, "contextDisclosure": "repeat"}
    text = build_decision_prompt(payload)
    assert "与本根首次研判相同" in text
    assert "战术周期" not in text


def _mtf_frame(bar: str) -> dict:
    return {"bar": bar, "sampleCount": 120, "closeTs": 1, "ageSeconds": 10, "stale": False,
            "regime": {"primary": "trend_up"},
            "digest": {"priceAction": {"rangePos": 0.7},
                       "factors": {"rsi14": {"value": 58, "label": "neutral"}}}}


def _tactical_frame(bar: str) -> dict:
    return {"bar": bar, "sampleCount": 120, "closeTs": 1_700_000_000, "ageSeconds": 42,
            "stale": False, "closedBars": 3, "regime": {"primary": "trend_up"},
            "digest": {
                "priceAction": {"trend": "up", "changePct": 0.42, "emaRelation": "fast_above",
                                "emaSlopePct": 0.021, "rangePos": 0.78},
                "volume": {"ratio": 1.31, "spike": False},
                "factors": {"rsi14": {"value": 58, "label": "neutral"},
                            "macdHist": {"value": 1.42, "label": "bullish"},
                            "atrPct": {"value": 0.31, "label": "low"}}}}


def _production_payload() -> dict:
    """A-4/A-4b 的比对夹具：形状贴近**真实生产轮**（中线背景 4 周期 + 体制 + 共识 + 三策略板 + 摘要）。

    刻意不用上方的最小夹具做分母：那份 payload 不到 500 字，任何绝对增量在它上面都会被放大成
    「膨胀 30%」，测出来的是夹具的小、不是本特性的贵。含 `baseRules` 是因为
    `ai_trade_base_rules_enabled` **默认开启**——每一个真实生产轮都带这一段。
    """
    return {
        **_payload_with_digest(),
        "position": None, "restingOrder": None,
        "baseRules": {"maBand": {
            "ruleId": "ma_band_entry", "period": 20, "stdMult": 2.0, "tolerance": 0.15,
            "upper": 30183.097688, "mid": 30148.5, "lower": 30113.902312,
            "bandWidthPct": 0.2295, "pctB": 0.9119, "zone": "upper", "directional": False}},
        "marketContext": {"primary": "trend_up", "volatility": "normal", "event": "none",
                          "confidence": 0.72, "labels": ["trend_up", "normal"]},
        "consensus": {"longVotes": 2, "shortVotes": 0, "flatVotes": 1, "total": 3,
                      "netStance": "long", "agreement": 0.67},
        "multiTimeframeContext": {
            "executionBar": "30m", "asOfTs": 1,
            "frames": [_mtf_frame(bar) for bar in ("1H", "4H", "12H", "1D")],
            "trendAlignment": {"status": "bullish", "bullishBars": ["1H", "4H"],
                               "bearishBars": [], "rangeBars": []},
            "rsiOverboughtBars": [], "rsiOversoldBars": [],
            "nearestSupport": {"price": 29000.0, "distancePct": 1.2, "bars": ["4H"]},
            "nearestResistance": {"price": 31000.0, "distancePct": 3.4, "bars": ["1D"]},
            "missingBars": [], "staleBars": []},
        "signalBoard": [{"strategyId": i, "name": f"策略{i}", "stance": "flat",
                         "latestSignal": "none", "strength": 0.4, "reason": "均线未突破",
                         "stopLossPct": 0.02, "takeProfitPct": 0.04, "maxHoldBars": 8}
                        for i in (1, 2, 3)],
        "summary": "此前若干轮维持观望，等待 4H 回踩确认；已连续两轮未见量能配合。" * 2,
    }


def _tactical_two_frames() -> dict:
    return {**_TACTICAL, "frames": [_tactical_frame("5m"), _tactical_frame("1m")]}


def test_repeat_disclosure_stays_within_115_percent_of_the_baseline_round():
    """A-4：重复轮 prompt 字符数不得超过改动前同轮次的 115%（离线硬断言，不依赖真实行情）。

    实测重复轮反而**更短**——中线背景 5 行压成 2 行、执行周期证据 3 行压成 1 行，省下的比战术段
    多。这正是「渐进式披露」想要的结果：新证据不是加法，是**换掉**已经看过一遍的旧证据。
    """
    before = build_decision_prompt(_production_payload())
    repeat = build_decision_prompt({
        **_production_payload(), "wake": _WAKE, "contextDisclosure": "repeat",
        "tacticalContext": _tactical_two_frames(), "tacticalTier": "brief"})
    assert len(repeat) <= len(before) * 1.15, (len(before), len(repeat))


def test_brief_tier_stays_within_100_percent_of_the_baseline_round():
    """A-4：`brief` 档不得比改动前更贵（重复轮的压缩必须真的把战术段的成本挣回来）。"""
    before = build_decision_prompt(_production_payload())
    brief = build_decision_prompt({
        **_production_payload(), "wake": {**_WAKE, "kind": "heartbeat"},
        "contextDisclosure": "repeat", "tacticalContext": _tactical_two_frames(),
        "tacticalTier": "brief"})
    assert len(brief) <= len(before), (len(before), len(brief))


def test_full_round_stays_within_135_percent_of_the_baseline_round():
    """A-4b：`full` 档同样有天花板——只管重复轮等于允许首轮无界膨胀。"""
    before = build_decision_prompt(_production_payload())
    full = build_decision_prompt({
        **_production_payload(), "wake": {**_WAKE, "kind": "candle_close"},
        "contextDisclosure": "full", "tacticalContext": _tactical_two_frames(),
        "tacticalTier": "full"})
    assert len(full) <= len(before) * 1.35, (len(before), len(full))


def test_max_chars_truncation_is_the_last_resort_backstop():
    """字符上限是兜底：即便分档失效，战术段也不会无界膨胀，且截断标记**可见**。"""
    payload = {**_production_payload(), "wake": {**_WAKE, "kind": "candle_close"},
               "contextDisclosure": "full", "tacticalTier": "full",
               "tacticalContext": {**_tactical_two_frames(), "maxChars": 120}}
    text = build_decision_prompt(payload)
    assert "…（战术周期证据已按 120 字上限截断）" in text


def test_amortised_cost_per_execution_bar_is_essentially_flat():
    """真正有经济含义的指标：30m/3min 下一根 K 线约 1 个 full 轮 + 9 个 repeat 轮。

    重复轮省下的（中线背景 5→2 行、执行周期证据 3→1 行）几乎抵掉了 full 轮多出的战术段——
    也就是说本特性在**不加钱**的前提下，把 9 个「证据逐字节相同」的轮次换成了带新证据的轮次。
    """
    baseline = len(build_decision_prompt(_production_payload()))
    full = len(build_decision_prompt({
        **_production_payload(), "wake": {**_WAKE, "kind": "candle_close"},
        "contextDisclosure": "full", "tacticalContext": _tactical_two_frames(),
        "tacticalTier": "full"}))
    repeat = len(build_decision_prompt({
        **_production_payload(), "wake": _WAKE, "contextDisclosure": "repeat",
        "tacticalContext": _tactical_two_frames(), "tacticalTier": "brief"}))
    assert (full + 9 * repeat) <= 10 * baseline * 1.05


# ---------- 交易周期模式的 prompt 冻结门禁（ai-trade-holding-horizon-modes G-1）----------
def _sha(text: str) -> str:
    import hashlib

    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def test_unarmed_horizon_keeps_three_prompts_byte_identical():
    """G-1：未装配 ⇒ 系统 / 决策 / 反思三份 prompt 与本特性合入前**逐字节相等**。

    以「显式传空垫片 vs 完全不传」的 sha256 相等来固化：新形参一旦被写成必填、或垫片默认值被改成
    非空，本用例立刻判红——这正是「一键运维回退」能不能兑现的可证伪判据。
    """
    system_before = build_ai_trade_system_prompt("BTC-USDT-SWAP", "1H", 100.0, 3.0)
    system_after = build_ai_trade_system_prompt("BTC-USDT-SWAP", "1H", 100.0, 3.0,
                                                horizon_text="")
    assert _sha(system_before) == _sha(system_after)
    assert _sha(build_reflection_system_prompt()) == _sha(build_reflection_system_prompt(""))
    payload = _payload_with_digest()
    assert "tradingHorizon" not in payload
    assert "【交易周期" not in build_decision_prompt(payload)


def test_armed_horizon_charter_is_the_fifth_section_in_order():
    """装配 ⇒ 五段宪章顺序为「基础策略 → 利润保护 → 仓位 → 成本 → 持仓周期」（位置即优先级声明）。"""
    text = build_ai_trade_system_prompt(
        "BTC-USDT-SWAP", "1H", 100.0, 3.0, base_rules_text="BASE_RULES_MARK",
        profit_guard_text="PROFIT_MARK", sizing_text="SIZING_MARK", cost_text="COST_MARK",
        horizon_text="HORIZON_MARK")
    positions = [text.index(mark) for mark in
                 ("BASE_RULES_MARK", "PROFIT_MARK", "SIZING_MARK", "COST_MARK",
                  "HORIZON_MARK")]
    assert positions == sorted(positions)


def test_armed_horizon_renders_the_round_section():
    """装配 ⇒ 决策 prompt 出现持仓周期段，且**数全部取自快照**（渲染层不重算）。"""
    payload = {**_payload_with_digest(), "tradingHorizon": {
        "profileId": "intraday_swing", "title": "日内波段", "targetLabel": "4 – 12 小时",
        "status": "holding", "holdSeconds": 5400.0, "barsInPosition": 2,
        "minHoldSeconds": 7200.0, "minHoldRemainingSeconds": 1800.0,
        "exitConfirmRounds": 2, "exitConfirmStreak": 1, "confirmWindowSeconds": 3600.0,
        "confirmWindowRemainingSeconds": 1200.0, "confirmWindowElapsed": False,
        "turnoverHysteresisK": 1.5, "moveBps": 8.2, "costBps": 13.0,
        "requiredMoveBps": 19.5, "returnPct": 0.02, "lossReleaseReturnPct": 0.075,
        "released": False, "releaseReason": None, "releasedGates": [], "blockedStreak": 0,
        "expectedHoldBars": 12, "openedWallEstimated": False, "gateReverse": True,
        "asOfWall": 1_700_000_000.0, "exchangeStopLossPx": None,
        "exchangeTakeProfitPx": None}}
    text = build_decision_prompt(payload)
    assert "【交易周期 · 日内波段（4 – 12 小时）】" in text
    assert "距最短持仓窗口还差 30 分钟" in text
    assert "离场连续确认：1/2" in text
    assert "需浮盈 ≥ 19.5bps" in text
    assert "交易所侧未挂止盈止损" in text          # 两者皆空时如实说没有（诚实约束）


# ============ 深度思维链的零回归门禁（ai-trade-deep-reasoning-chain §7.1 G-1/G-1b/G-2/G-3）============
_ARGS = ("BTC-USDT-SWAP", "1H", 100.0, 3.0)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def test_off_depth_keeps_three_prompts_byte_identical():
    """G-1：不传 `texts` 与传全空 `SystemPromptTexts()` 产出 sha256 相同；反思与决策 prompt 同款。"""
    assert _sha(build_ai_trade_system_prompt(*_ARGS)) == _sha(
        build_ai_trade_system_prompt(*_ARGS, texts=SystemPromptTexts()))
    assert _sha(build_reflection_system_prompt()) == _sha(
        build_reflection_system_prompt("", ""))
    payload = _payload_with_digest()
    assert "reasoningDepth" not in payload      # 老 payload 无该键 ⇒ 收尾指令逐字回退
    assert build_decision_prompt(payload).rstrip().endswith("不得只输出研判文字。")


def test_legacy_text_kwargs_equal_aggregated_texts():
    """G-1b：九个旧关键字逐一直传，与折进两个聚合结构传入，产出 sha256 **相等**。

    这条用例是聚合迁移的安全网：仓库里有上百条冻结断言以关键字直连调用本函数。
    """
    legacy = build_ai_trade_system_prompt(
        *_ARGS, position_step_pct=0.25, allow_limit=True, limit_band_pct=0.015,
        base_rules_text="A", profit_guard_text="B", sizing_text="C",
        tactical_bars_text="5m → 1m", cost_text="D", horizon_text="E")
    aggregated = build_ai_trade_system_prompt(
        *_ARGS,
        options=SystemPromptOptions(position_step_pct=0.25, allow_limit=True,
                                    limit_band_pct=0.015),
        texts=SystemPromptTexts(base_rules_text="A", profit_guard_text="B", sizing_text="C",
                                tactical_bars_text="5m → 1m", cost_text="D",
                                horizon_text="E"))
    assert _sha(legacy) == _sha(aggregated)


def test_unknown_legacy_kwarg_raises_instead_of_being_swallowed():
    """垫片必须**拒收**未知键：静默吞掉一个拼错的关键字 = 宪章章没进 prompt 且不报错。"""
    with pytest.raises(TypeError):
        build_ai_trade_system_prompt(*_ARGS, base_rule_text="typo")


def test_off_depth_tool_schema_identity():
    """G-2：`build_decision_tool_schema(0.0) is DECISION_TOOL_SCHEMA` 仍成立（恒等，非相等）。"""
    assert build_decision_tool_schema(0.0) is DECISION_TOOL_SCHEMA
    opened = build_decision_tool_schema(0.0, allow_reasoning_digest=True)
    assert opened is not DECISION_TOOL_SCHEMA
    assert "evidenceDigest" not in DECISION_TOOL_SCHEMA["properties"]   # 常量未被就地改


def test_off_depth_tool_description_identity():
    """G-3：默认调用**恒等**返回今日常量；`lang="en"` 才整体换英文（且三段后缀一并换）。"""
    assert build_decision_tool_description() is DECISION_TOOL_DESCRIPTION
    assert build_decision_tool_description(lang="zh") is DECISION_TOOL_DESCRIPTION
    english = build_decision_tool_description(True, allow_hold_exit=True,
                                              allow_exit_priority=True, lang="en")
    assert english.startswith("Submit the single trading decision")
    assert "提交本轮" not in english and "另可给 orderType" not in english


def test_english_spine_keeps_the_chinese_charter_and_ends_with_the_hard_constraint():
    """英文脊柱下：五段**中文**宪章原样注入（§1.2 非目标），收尾硬约束仍占近因位（P-3）。"""
    deep = profile_by_name("deep")
    text = build_ai_trade_system_prompt(
        *_ARGS, texts=SystemPromptTexts(reasoning_text=build_deliberation_protocol(deep),
                                        base_rules_text="【基础策略】中文铁律章"))
    assert "【基础策略】中文铁律章" in text
    assert "GLOSSARY" in text and "LANGUAGE CONTRACT" in text
    assert text.rstrip().endswith("never acceptable and costs the round.**")


def test_reflection_prompt_switches_to_english_only_with_a_protocol():
    """反思侧同款分叉：`reasoning_text=""` ⇒ 今日中文原文；非空 ⇒ 英文复核官脊柱 + R1–R7。"""
    zh = build_reflection_system_prompt()
    en = build_reflection_system_prompt("", build_review_protocol(profile_by_name("deep")))
    assert "独立的下单前风控复核官" in zh and "## R7" not in zh
    assert "independent pre-trade risk reviewer" in en and "## R7" in en
    # 安全锚点在两种语言下都必须在（英文脊柱不得稀释安全语义）。
    assert "只能" in zh and "only tighten" in en


def test_decision_prompt_task_instruction_forks_on_reasoning_depth():
    """收尾任务指令按 `reasoningDepth` **加法键**分叉；键缺席 ⇒ 中文原句逐字节回退。"""
    payload = {"round": 1, "barTs": 1, "lastPx": 1.0, "signalBoard": []}
    zh = build_decision_prompt(payload)
    en = build_decision_prompt({**payload, "reasoningDepth": "deep"})
    assert zh.rstrip().endswith("不得只输出研判文字。")
    assert "S1 -> S12" in en and "Simplified Chinese" in en


# ============ 方向对称条款与逆势通路（ai-trade-directional-symmetry 轴 A-5 / C-2） ============
def test_direction_charter_is_empty_when_not_armed():
    """未装配 ⇒ **空串垫片** ⇒ 两条脊柱的拼接结果与本特性合入前逐字节相同。"""
    from app.ai_auto.prompts import format_direction_charter

    assert format_direction_charter(False) == ""
    armed = format_direction_charter(True)
    assert "方向对称条款" in armed and "完全对等" in armed
    assert "只列出一个方向的候选，等同于没有完成 S10" in armed


def test_direction_charter_reaches_both_spines():
    """C-2 的核心：中文脊柱另有一份内联拼接、根本不调 `charter()`——只改一处就只到一条路径。"""
    from app.ai_auto.prompts import build_ai_trade_system_prompt, format_direction_charter
    from app.ai_auto.prompts_en import SystemPromptTexts

    clause = format_direction_charter(True)
    zh = build_ai_trade_system_prompt("BTC-USDT-SWAP", "1H", 1000.0, 3.0,
                                      texts=SystemPromptTexts(direction_text=clause))
    en = build_ai_trade_system_prompt("BTC-USDT-SWAP", "1H", 1000.0, 3.0,
                                      texts=SystemPromptTexts(direction_text=clause,
                                                              reasoning_text="X"))
    assert "方向对称条款" in zh          # 中文脊柱（off 档真源）
    assert "方向对称条款" in en          # 英文脊柱（生产路径）
    assert "Direction is symmetric" in en   # 英文决策纪律里的孪生子句
    # 未装配 ⇒ 两条脊柱都不含它（逐字节回退）。
    bare_zh = build_ai_trade_system_prompt("BTC-USDT-SWAP", "1H", 1000.0, 3.0,
                                           texts=SystemPromptTexts())
    assert "方向对称条款" not in bare_zh
    bare_en = build_ai_trade_system_prompt("BTC-USDT-SWAP", "1H", 1000.0, 3.0,
                                           texts=SystemPromptTexts(reasoning_text="X"))
    assert "Direction is symmetric" not in bare_en


def test_contrarian_lane_line_is_absent_when_key_missing():
    """A-5：`contrarian` 整键缺席 ⇒ 共识那一行逐字节回退。"""
    from app.ai_auto.prompts import _format_consensus

    base = {"longVotes": 2, "shortVotes": 0, "flatVotes": 0, "netStance": "long",
            "agreement": 1.0}
    plain = _format_consensus(base)
    assert "逆势通路" not in plain and "\n" not in plain
    armed = _format_consensus({**base, "contrarian": {
        "available": True, "minConfidence": 0.65, "minEvidenceChars": 60,
        "maxOpposingVotes": 1, "remaining": 3, "sizeMult": 0.5}})
    assert armed.startswith(plain)         # 只追加一行，原文一个字不改
    assert "逆势通路" in armed and "contrarianEvidence" in armed
    assert "不少于 60 字" in armed and "还剩 3 次" in armed
    # 名额用尽 ⇒ 措辞改口，绝不还说「系统会放行」。
    used_up = _format_consensus({**base, "contrarian": {
        "available": True, "minConfidence": 0.65, "minEvidenceChars": 60,
        "maxOpposingVotes": 1, "remaining": 0, "sizeMult": 0.5}})
    assert "名额已用尽" in used_up and "系统会放行" not in used_up


def test_prior_no_evidence_wording_is_gated():
    """E-1：「无证据」的措辞追加「样本不足 ≠ 胜率低」，且随总闸逐字回退。"""
    from app.ai_auto.prompts import _format_prior_side

    assert _format_prior_side(None) == "无证据"
    assert "样本不足" in _format_prior_side(None, True)
    assert "不是该方向胜率低的证据" in _format_prior_side(None, True)


# ============================ 执行周期趋势基调（execution-trend-verdict §7.4）============================
def _trend_wire(**over) -> dict:
    base = {
        "bar": "15m", "asOfTs": 1756700000, "score": -0.32, "execScore": -0.61,
        "direction": "down", "bias": "short", "strength": "strong", "verdictConfidence": 0.59,
        "components": [
            {"key": "regime", "label": "体制结构", "score": -0.72, "weight": 0.26,
             "detail": "primary=trend_down"},
            {"key": "ema", "label": "均线结构", "score": -0.68, "weight": 0.22, "detail": "…"},
            {"key": "netChange", "label": "净变化", "score": -0.55, "weight": 0.20, "detail": "…"},
            {"key": "structure", "label": "区间与连阳连阴", "score": -0.40, "weight": 0.16,
             "detail": "…"},
            {"key": "momentum", "label": "动量", "score": None, "weight": 0.16,
             "detail": "macdHist 与 rocPct 均不可用"},
            {"key": "htf", "label": "高周期背景", "score": 1.0, "weight": 0.18, "detail": "…"},
        ],
        "missing": ["momentum"], "availableWeight": 0.86, "execAvailableWeight": 0.84,
        "higherTimeframe": {"status": "bullish", "agree": False,
                            "bullishBars": ["1D", "4H"], "bearishBars": []},
        "tacticalAlignment": "diverged", "conflict": "both", "bonusWithheld": True,
        "counterGate": {"armed": True, "minConfidence": 0.65, "sizeMult": 0.5},
        "degraded": False, "degradeReason": None,
    }
    base.update(over)
    return base


def test_trend_charter_is_an_empty_shim_when_unarmed():
    """P-1：未装配 ⇒ `""`；装配 ⇒ 含 `bar` 字面量与「不能替你决定」一句。"""
    from app.ai_auto.prompts import format_trend_charter
    from app.ai_auto.trend_verdict import TrendVerdictRule

    assert format_trend_charter("15m", None) == ""
    assert format_trend_charter("15m", TrendVerdictRule(enabled=False)) == ""
    assert format_trend_charter("", TrendVerdictRule(enabled=True)) == ""
    armed = format_trend_charter("15m", TrendVerdictRule(enabled=True))
    assert "15m" in armed and "不能替你决定" in armed


def test_both_spines_carry_the_trend_charter():
    """P-2：**中文脊柱与英文脊柱各自都装了第七段**（只改一处即红——P1-4 的回归护栏）。"""
    zh = build_ai_trade_system_prompt("BTC-USDT-SWAP", "15m", 1000.0, 5.0,
                                      texts=SystemPromptTexts(trend_text="趋势基调条款：…"))
    en = build_ai_trade_system_prompt("BTC-USDT-SWAP", "15m", 1000.0, 5.0,
                                      texts=SystemPromptTexts(reasoning_text="PROTO",
                                                              trend_text="趋势基调条款：…"))
    assert "趋势基调条款" in zh and "趋势基调条款" in en


def test_trend_read_order_appears_exactly_once_when_armed():
    """P-10（评审 RP1-4）：disarmed 逐字节回退；armed 里读序**只出现一次**。

    v1 的写法（「插在原句之后、把原句改写为续写」）会让 `1D → 12H → 4H → 1H` 出现两遍且
    互相矛盾——同一句读序连说两遍，模型读到的是一条自相矛盾的纪律。
    """
    from app.ai_auto.prompts import _trend_read_order
    from app.ai_auto.prompts_en import _trend_read_order_en

    assert _trend_read_order("") == (
        "先按 1D → 12H → 4H → 1H 研判中线背景、趋势一致性、候选支撑阻力与超买/超卖，")
    armed = _trend_read_order("15m")
    assert armed.count("1D → 12H → 4H → 1H") == 1
    assert "先在执行周期 15m 上给出本轮趋势基调" in armed
    assert _trend_read_order_en("").startswith("Work the")
    armed_en = _trend_read_order_en("15m")
    assert armed_en.count("1D -> 12H -> 4H -> 1H") == 1
    assert "execution bar 15m" in armed_en


def test_spine_read_order_is_gated_by_the_charter_alone():
    """读序子句的装配判据**只有一个**：第七段宪章装没装（绝不另读一次 settings）。"""
    plain = build_ai_trade_system_prompt("BTC-USDT-SWAP", "15m", 1000.0, 5.0)
    armed = build_ai_trade_system_prompt("BTC-USDT-SWAP", "15m", 1000.0, 5.0,
                                         texts=SystemPromptTexts(trend_text="X"))
    assert "先按 1D → 12H → 4H → 1H 研判中线背景" in plain
    assert "先在执行周期 15m 上给出本轮趋势基调" in armed
    assert "先按 1D → 12H → 4H → 1H 研判中线背景" not in armed


def test_trend_section_is_the_first_substantive_evidence():
    """P-6：基调段的下标小于「自管持仓」与「中线背景」两者。

    锚点用 `自管持仓` 而不是 `当前自管持仓`（评审 RP2-3：空仓轮渲染的是「当前无自管持仓
    （flat）」，后者取不到下标）。
    """
    payload = {"round": 1, "barTs": 1, "lastPx": 100.0, "signalBoard": [],
               "trendVerdict": _trend_wire(),
               "multiTimeframeContext": {"executionBar": "15m", "frames": [],
                                         "trendAlignment": {"status": "bullish",
                                                            "bullishBars": [], "bearishBars": [],
                                                            "rangeBars": []},
                                         "rsiOverboughtBars": [], "rsiOversoldBars": [],
                                         "nearestSupport": None, "nearestResistance": None,
                                         "missingBars": [], "staleBars": []}}
    text = build_decision_prompt(payload)
    assert text.index("【本轮趋势基调") < text.index("自管持仓")
    assert text.index("【本轮趋势基调") < text.index("中线背景")


def test_trend_section_absent_key_is_byte_identical():
    """S-5 的 prompt 半边：**键缺席 ⇒ 整段省略 ⇒ 与合入前逐字节相同**。"""
    payload = {"round": 1, "barTs": 1, "lastPx": 100.0, "signalBoard": []}
    before = hashlib.sha256(build_decision_prompt(payload).encode()).hexdigest()
    payload_with_none = {**payload, "trendVerdict": None}
    assert hashlib.sha256(
        build_decision_prompt(payload_with_none).encode()).hexdigest() == before


def test_trend_section_degraded_has_no_direction_word():
    """P-5：`degraded=True` ⇒ 只出两行、**不含任何方向词**，且缺失成分被显式列出。"""
    from app.ai_auto.trend_text import format_trend_verdict_for_prompt

    lines = format_trend_verdict_for_prompt(_trend_wire(
        degraded=True, direction="unknown", bias="neutral", score=None, execScore=None,
        degradeReason="insufficient_components"))
    assert len(lines) == 2
    body = "".join(lines)
    assert "偏多" not in body and "偏空" not in body
    assert "动量" in body and "冒充本级别基调" in body


def test_trend_section_brief_tier_drops_component_detail():
    """P-6b（评审 RP1-11）：`contextDisclosure="repeat"` ⇒ 只剩两行且**不含**逐成分明细。"""
    verdict = _trend_wire()
    payload = {"round": 1, "barTs": 1, "lastPx": 100.0, "signalBoard": [],
               "trendVerdict": verdict}
    full = build_decision_prompt(payload)
    brief = build_decision_prompt({**payload, "contextDisclosure": "repeat"})
    assert "判据（按权重）" in full
    assert "判据（按权重）" not in brief
    assert "【本轮趋势基调 · 15m】" in brief


def test_trend_section_never_hardcodes_the_counter_gate_thresholds():
    """渲染层**不写死算例**：两个阈值随 wire 的 `counterGate` 下发（实施期发现 F-1）。"""
    from app.ai_auto.trend_text import format_trend_verdict_for_prompt

    body = "".join(format_trend_verdict_for_prompt(_trend_wire(
        counterGate={"armed": True, "minConfidence": 0.71, "sizeMult": 0.4})))
    assert "≥0.71" in body and "×0.4" in body
    off = "".join(format_trend_verdict_for_prompt(_trend_wire(
        counterGate={"armed": False, "minConfidence": None, "sizeMult": None})))
    assert "未装配逆势降档闸" in off


def test_reflection_prompt_carries_trend_verdict():
    """P-7：反思侧的连锁通路——`build_reflection_prompt` 第一行就是 `build_decision_prompt`。

    这条通路本轮正好是我们想要的，但**必须被用例钉死而不是碰巧成立**（market-intel 那轮吃过
    一次亏：改决策 prompt 会同时改反思 prompt）。
    """
    decision = TradeDecision(action="open_short", size_fraction=0.25, confidence=0.8, reason="x")
    payload = {"round": 1, "barTs": 1, "lastPx": 100.0, "signalBoard": [],
               "trendVerdict": _trend_wire()}
    assert "【本轮趋势基调" in build_reflection_prompt(payload, decision)


def test_reviewer_conflict_clause_is_conditional_on_the_verdict():
    """P-9（评审 RP0-4）的三处：中文系统 prompt / 英文评级标准 / 中文散文，各带一条回退断言。"""
    from app.ai_auto.prompts_en import render_review_spine_en

    # ① 中文复核官系统 prompt（off 档真源）
    assert "(b) 与更高周期背景强冲突且决策未给出对冲理由；" in build_reflection_system_prompt()
    armed_zh = build_reflection_system_prompt(trend_bar="15m")
    assert "15m 执行周期趋势基调" in armed_zh
    assert "(b) 与更高周期背景强冲突且决策未给出对冲理由；" not in armed_zh

    # ② 英文评级标准 2(b)
    plain_en = render_review_spine_en("PROTO")
    assert ("(b) it strongly conflicts with the\n   higher-timeframe context and the decision "
            "offers no hedge rationale;") in plain_en
    armed_en = render_review_spine_en("PROTO", "", "15m")
    assert "execution-bar (15m) trend verdict" in armed_en
    assert "is not a defect" in armed_en

    # ③ 中文散文（`build_reflection_prompt`）
    decision = TradeDecision(action="open_short", size_fraction=0.25, confidence=0.8, reason="x")
    plain = build_reflection_prompt({"round": 1, "signalBoard": []}, decision)
    assert "与高周期强冲突且无对冲理由" in plain
    armed = build_reflection_prompt(
        {"round": 1, "signalBoard": [], "trendVerdict": _trend_wire()}, decision)
    assert "15m 执行周期趋势基调" in armed
    assert "与高周期强冲突且无对冲理由 /" not in armed


def test_reflection_system_prompt_is_byte_identical_without_trend_bar():
    """三处条件化子句的共同回退路径：`trend_bar=""` ⇒ 与合入前 sha256 相同。"""
    from app.ai_auto.prompts_en import render_review_spine_en

    assert (hashlib.sha256(build_reflection_system_prompt("H", "").encode()).hexdigest()
            == hashlib.sha256(
                build_reflection_system_prompt("H", "", "").encode()).hexdigest())
    assert render_review_spine_en("P", "H") == render_review_spine_en("P", "H", "")
