"""AI 自动交易决策器与摘要器的 prompt 与决策工具 schema（ai-auto-trading §2.3 / §2.4）。

- `DECISION_TOOL_SCHEMA`：唯一放行工具 `submit_trade_decision` 的 `input_schema`（JSON Schema）。
  喂 LLM 层时必须封装为 `ToolSchema(name, description, input_schema=DECISION_TOOL_SCHEMA)`（P1-5）。
- `build_ai_trade_system_prompt`：决策者角色 / 纪律 / 风险偏好 / 只用真数据 / 强力引导调用决策工具。
- `build_decision_prompt`：把本轮信号板 + 真实持仓 + 会话盈亏 + 长期记忆摘要压成紧凑决策输入。
- `build_summary_system_prompt` / `build_summary_prompt`：轮次滚动摘要（以 `_journal` 为素材，P1-3）。
"""
from __future__ import annotations

import json

# 决策工具名（决策器据 tool_use_start.name 匹配；系统 prompt 亦引用）。
DECISION_TOOL_NAME = "submit_trade_decision"

# `submit_trade_decision` 的 input_schema（JSON Schema）。action 必填；close/hold 忽略 sizeFraction。
DECISION_TOOL_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["open_long", "open_short", "close", "hold"]},
        "sizeFraction": {"type": "number", "minimum": 0, "maximum": 1},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "reason": {"type": "string", "maxLength": 500},
    },
    "required": ["action", "reason"],
}

DECISION_TOOL_DESCRIPTION = (
    "提交本轮唯一的自动交易决策。综合并**独立批判研判**所有策略信号、因子读数、价格走势/成交量与当前"
    "真实持仓后（这些信号仅供参考、可推翻），选择 open_long（开多）/ open_short（开空）/ close（平当前"
    "持仓）/ hold（持币观望）之一，并给出简短理由（reason）。开仓可给 sizeFraction（预算名义占比，0~1）"
    "与 confidence（0~1）。"
)


def build_ai_trade_system_prompt(inst_id: str, bar: str, budget_usd: float,
                                 leverage: float) -> str:
    """决策者系统 prompt：信号仅供参考 + 分步思维链纪律 + 风险偏好 + 只用真数据 + 收尾必调决策工具。"""
    return (
        "你是一名严谨、纪律性极强的加密合约自动交易决策者。你不是聊天助手，也不是信号复读机——你的"
        f"**唯一职责**是在每个决策轮，为标的 {inst_id}（周期 {bar}）做出一个受严格约束的交易决策。"
        f"会话预算约 {budget_usd:.2f} USDT，杠杆约 {leverage:g}×。\n\n"
        "**首要原则——信号是参考，不是指令**：交给你的多个策略信号、因子读数、市场体制、共识度都**只是"
        "决策的参考与触发线索，不是必须执行的确定性指令**。它们可能正确，也可能错误、滞后或相互矛盾。"
        "你的职责不是复述或跟随它们，而是**像一名资深交易员那样，独立地、一步一步地综合研判所有证据后"
        "自己下判断**——你有权采纳、存疑或**推翻**任何单一信号。\n\n"
        "决策纪律：\n"
        "1. **每轮必须先输出一段分步骤的研判（思维链），依次思考：① 价格走势（方向、动量、均线结构、"
        "区间位置）；② 成交量（量能、量比、放缩、异常放量）；③ 市场体制（趋势/震荡/高低波动/事件）；"
        "④ 技术因子读数（RSI/MACD/布林/ATR/动量各说明什么）；⑤ 逐一批判各策略信号——每个策略此刻的"
        "立场与本轮意图在当前语境下**是否可信**，你采纳、存疑还是推翻，为什么；⑥ 信号冲突与权衡；"
        "⑦ 当前真实持仓、会话盈亏与风险约束；⑧ 结论。**研判供人类观察，真正生效的只有工具调用。**\n"
        "2. 只使用输入中提供的真实数据（价格/成交量/因子/信号板/持仓/盈亏），绝不臆造行情或指标；"
        "证据不足或相互矛盾、无把握时选择 hold（持币观望）。\n"
        "3. 风险优先：已持仓且多数证据转为反向时，优先 close 保护利润 / 止损；空仓且多数证据同向、"
        "研判充分时才开仓，sizeFraction 保守（默认 ≤0.5），避免频繁反手与过度交易。\n"
        "4. action 取值仅限 open_long / open_short / close / hold；close/hold 忽略 sizeFraction。"
        "真实下单由系统的守护执行器落地并继承全部资金护栏，你只需给出决策与理由。\n"
        "5. **收尾硬约束（务必遵守）：每轮在完成上述分步研判之后，必须且只能调用一次 "
        "`submit_trade_decision` 工具提交本轮决策——绝不允许只输出研判文字而不调用决策工具。**"
    )


def _format_position(position: dict | None) -> str:
    if not position:
        return "当前无自管持仓（flat）"
    return (f"当前自管持仓：方向={position.get('side')}，均价={position.get('avgEntry')}，"
            f"张数={position.get('size')}，未实现盈亏≈{position.get('unrealizedPnl')} USDT")


def _format_board(board: list[dict]) -> str:
    """把信号板逐策略序列化为紧凑要点（stance/latestSignal/strength/reason + 风控）。"""
    lines = []
    for v in board:
        lines.append(
            f"- [{v.get('strategyId')}] {v.get('name')}：立场={v.get('stance')}，"
            f"本轮信号={v.get('latestSignal')}（强度 {v.get('strength')}），"
            f"理由={v.get('reason') or '—'}，"
            f"风控(sl/tp/maxHold)={v.get('stopLossPct')}/{v.get('takeProfitPct')}/{v.get('maxHoldBars')}")
    return "\n".join(lines) if lines else "（无策略信号）"


def _format_market_context(mc: dict | None) -> str:
    """把市场体制上下文压成一行（E1）；`mc is None` 时返回空串（整段省略，保持 prompt 纯净）。"""
    if not mc:
        return ""
    return (f"市场体制：趋势结构={mc.get('primary')}，波动={mc.get('volatility')}，"
            f"事件={mc.get('event')}（判定置信 {mc.get('confidence')}）")


def _format_consensus(consensus: dict | None) -> str:
    """把策略共识度压成一行（E2）；`consensus is None` 时返回空串（该段省略）。"""
    if not consensus:
        return ""
    return (f"策略共识：{consensus.get('longVotes')}多 / {consensus.get('shortVotes')}空 / "
            f"{consensus.get('flatVotes')}空仓，净立场={consensus.get('netStance')}，"
            f"一致度={consensus.get('agreement')}")


def _format_price_action(pa: dict | None) -> str:
    """把价格走势证据压成一行（R1）；`pa is None` 时返回空串（整段省略，同 _format_market_context）。"""
    if not pa:
        return ""
    return (f"价格走势：趋势={pa.get('trend')}（近窗涨跌 {pa.get('changePct')}%），"
            f"均线={pa.get('emaRelation')}（快线斜率 {pa.get('emaSlopePct')}%），"
            f"区间位置={pa.get('rangePos')}，末端连阳/连阴={pa.get('consecUp')}/{pa.get('consecDown')}")


def _format_volume(vol: dict | None) -> str:
    """把成交量证据压成一行（R1）；`vol is None` 时返回空串（整段省略）。"""
    if not vol:
        return ""
    spike = "，异常放量" if vol.get("spike") else ""
    return (f"成交量：量比={vol.get('ratio')}（zscore {vol.get('zscore')}），"
            f"放缩趋势={vol.get('trend')}{spike}")


def _format_factors(factors: dict | None) -> str:
    """把技术因子读数压成一行（R1）；`factors is None` 时返回空串（整段省略）。"""
    if not factors:
        return ""

    def _reading(key: str) -> str:
        r = factors.get(key) or {}
        return f"{r.get('value')}({r.get('label')})"

    return (f"技术因子：RSI14={_reading('rsi14')}，MACD柱={_reading('macdHist')}，"
            f"布林%B={_reading('bollPctB')}，ATR%={_reading('atrPct')}，动量ROC={_reading('rocPct')}")


def build_decision_prompt(payload: dict) -> str:
    """把本轮决策输入（体制/共识 + 信号板 + 真实持仓 + 会话盈亏 + 摘要）压成紧凑文本喂决策器。

    `payload` 由决策器/会话装配：round, barTs, lastPx, position, sessionPnlUsd, budgetUsd,
    takeProfitUsd, stopLossUsd, signalBoard(list[dict]), summary，
    以及可选 marketContext(dict|None, E1) / consensus(wire dict|None, E2) /
    marketDigest(dict|None, R1：{priceAction, volume, factors}，禁用/数据不足时整体省略)。
    """
    summary = payload.get("summary")
    tp = payload.get("takeProfitUsd")
    sl = payload.get("stopLossUsd")
    parts = [
        f"决策轮 #{payload.get('round')}｜barTs={payload.get('barTs')}｜最新价={payload.get('lastPx')}",
        _format_position(payload.get("position")),
        (f"会话累计盈亏≈{payload.get('sessionPnlUsd')} USDT / 预算 {payload.get('budgetUsd')} USDT；"
         f"会话止盈阈值={'不触发' if tp is None else tp}，止损阈值={'不触发' if sl is None else sl}"),
    ]
    market_line = _format_market_context(payload.get("marketContext"))
    if market_line:                          # E1：mc 为 None 时整段省略（不出现半截空信息）
        parts.append(market_line)
    consensus_line = _format_consensus(payload.get("consensus"))
    if consensus_line:                       # E2：consensus 为 None 时省略；空板则如实呈现 0 票
        parts.append(consensus_line)
    digest = payload.get("marketDigest") or {}   # R1：各段 None 时整段省略（同 E1/E2 降级范式）
    for line in (_format_price_action(digest.get("priceAction")),
                 _format_volume(digest.get("volume")),
                 _format_factors(digest.get("factors"))):
        if line:
            parts.append(line)
    parts += [
        "策略信号板（仅供参考的触发线索，请逐条批判研判，可推翻）：",
        _format_board(payload.get("signalBoard") or []),
    ]
    if summary:
        parts.append(f"\n【此前执行摘要（你的长期记忆）】\n{summary}")
    parts.append(
        f"\n请先按上述纪律逐步研判（思维链），再必须且只能调用一次 {DECISION_TOOL_NAME} "
        "提交本轮决策——不得只输出研判文字。")
    return "\n".join(parts)


def build_summary_system_prompt() -> str:
    """摘要器系统 prompt：把执行历程压成简洁背景，供后续轮以纯净上下文继续。"""
    return (
        "你是自动交易执行历程的摘要器。请把给定的逐轮执行流水压缩成一段**简洁**的中文执行摘要，"
        "用作后续决策的背景记忆。"
    )


def build_summary_prompt(prev_summary: str | None, journal: list[dict]) -> str:
    """以结构化 `_journal`（每轮 {round,signalBoard,decision,executed,positionAfter,sessionPnl}）
    为唯一素材（P1-3，与可能被裁剪的模型窗口 `_context` 解耦）拼成摘要输入文本。"""
    head = "总结迄今的自动交易执行情况：决策序列与结果、持仓与盈亏演变、**哪些策略/因子信号被你采纳或被" \
           "推翻、事后又被行情验证为对或错**（作为经验教训）、当前策略偏好，形成简洁的执行摘要作为后续" \
           "决策的背景，不要罗列每一轮细节。"
    prev = f"\n上一段执行摘要（请在其基础上滚动累积）：\n{prev_summary}" if prev_summary else ""
    digest = json.dumps(journal, ensure_ascii=False)
    return f"{head}{prev}\n\n逐轮执行流水（JSON）：\n{digest}"
