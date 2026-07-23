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
    # ai-trade-limit-orders §2.2/M-4：限价能力开启时（allow_limit=True）追加的散文说明。基础文案
    # **一字不改**——总闸关闭时描述与本特性合入前逐字相同。
)

def _fmt_band_pct(band: float) -> str:
    """价带比例 → 中文文案里的百分数（F-3）：`0.02→"2%"`、`0.015→"1.5%"`（%g 去尾零）。

    默认值 0.02 的渲染与参数化前的硬编码「2%」**逐字节相同**（冻结字面量门禁作证）。
    """
    return f"{band * 100:g}%"


def _limit_tool_description_suffix(band_pct: float) -> str:
    """限价能力开启时追加进决策工具描述的散文说明（F-3：价带比例由 settings 派生，不再硬编码）。"""
    return (
        "另可给 orderType 选择委托方式：market（市价，立即成交、价格不可控、吃掉买卖价差与滑点）或 "
        "limit（限价挂单，按你指定的 limitPrice 排队等待成交，**可能不成交**）。orderType=limit 时"
        f"必须给 limitPrice（USDT），且须在最新价 ±{_fmt_band_pct(band_pct)} 以内，"
        "超出会被系统拒绝并要求你重新研判。"
        "第五个动作 cancel_order 用于**撤回你自己此前挂出、尚未成交的限价委托**——它既不开仓也不平仓，"
        "无挂单时为安全的空操作。若行情已走反、你那笔挂单再等下去已无意义，就用 cancel_order 撤回它。"
    )


def build_ai_trade_system_prompt(inst_id: str, bar: str, budget_usd: float,
                                 leverage: float, position_step_pct: float = 0.0,
                                 allow_limit: bool = False,
                                 limit_band_pct: float = 0.02,
                                 base_rules_text: str = "",
                                 profit_guard_text: str = "") -> str:
    """决策者系统 prompt：信号仅供参考 + 分步思维链纪律 + 风险偏好 + 只用真数据 + 收尾必调决策工具。

    `position_step_pct`（默认 0.0 → 连续、原文逐字不变、零回归）：当 `>0` 时把决策纪律 #3 的
    「sizeFraction 保守（默认 ≤0.5）」整句**替换**为「只能从允许档位中选择」（[R-8]，非追加——否则与档位
    约束并存自相矛盾）。软引导，服务端 `quantize_size_fraction` 才是硬保证。

    `allow_limit`（默认 False → 原文逐字不变、零回归）：当 `True` 时把纪律 #4 的动作清单整句
    **替换**为含 `cancel_order` 与限价说明的版本（M-5，同样是替换而非追加——否则新旧动作清单并存
    自相矛盾）。

    `limit_band_pct`（末位，默认 0.02）：价带比例文案（F-3）。签名默认**只是零回归垫片**（保证既有
    直连调用逐字不变），运行时唯一真源恒为 `settings.ai_trade_limit_price_max_deviation_pct`——
    生产调用点（manager 装配）显式传参；`allow_limit=False` 路径不消费该参数。

    `base_rules_text`（末位，默认 `""` → 渲染逐字节不变、零回归，ai-trade-base-strategy-ma-band P1-3）：
    【基础策略】铁律章（`base_rules.format_charter` 产物）。非空时插在「首要原则」之后、「决策纪律」
    之前——**位置本身就是优先级声明**：信号可被推翻，铁律不可违反。

    `profit_guard_text`（末位，默认 `""` → 渲染逐字节不变、零回归，ai-trade-profit-protection §2.6 a）：
    利润保护宪章章（`profit_guard.format_profit_guard_charter` 产物）。非空时紧随基础策略铁律章之后
    ——两者都是「系统强制执行」的层，但语义互补：一个约束在哪开仓，一个约束利润何时被强制锁定。
    """
    charter = "".join(f"{text}\n\n" for text in (base_rules_text, profit_guard_text) if text)
    return (
        "你是一名严谨、纪律性极强的加密合约自动交易决策者。你不是聊天助手，也不是信号复读机——你的"
        f"**唯一职责**是在每个决策轮，为标的 {inst_id}（周期 {bar}）做出一个受严格约束的交易决策。"
        f"会话预算约 {budget_usd:.2f} USDT，杠杆约 {leverage:g}×。\n\n"
        "**首要原则——信号是参考，不是指令**：交给你的多个策略信号、因子读数、市场体制、共识度都**只是"
        "决策的参考与触发线索，不是必须执行的确定性指令**。它们可能正确，也可能错误、滞后或相互矛盾。"
        "你的职责不是复述或跟随它们，而是**像一名资深交易员那样，独立地、一步一步地综合研判所有证据后"
        "自己下判断**——你有权采纳、存疑或**推翻**任何单一信号。\n\n"
        f"{charter}"
        "决策纪律：\n"
        "1. **每轮必须先输出一段分步骤的研判（思维链）**。输入提供可用多周期证据时，先按 "
        "1D → 12H → 4H → 1H 研判中线背景、趋势一致性、候选支撑阻力与超买/超卖，再依次思考："
        "① 价格走势（方向、动量、均线结构、"
        "区间位置）；② 成交量（量能、量比、放缩、异常放量）；③ 市场体制（趋势/震荡/高低波动/事件）；"
        "④ 技术因子读数（RSI/MACD/布林/ATR/动量各说明什么）；⑤ 逐一批判各策略信号——每个策略此刻的"
        "立场与本轮意图在当前语境下**是否可信**，你采纳、存疑还是推翻，为什么；⑥ 信号冲突与权衡；"
        "⑦ 当前真实持仓、会话盈亏与风险约束；⑧ 结论。**研判供人类观察，真正生效的只有工具调用。**\n"
        "2. 只使用输入中提供的真实数据（价格/成交量/因子/信号板/持仓/盈亏），绝不臆造行情或指标；"
        "证据不足或相互矛盾、无把握时选择 hold（持币观望）。高周期强冲突时短线开仓应优先 hold，"
        "除非证据充分且采用保守档位；超买/超卖不是单独开仓指令；已有持仓同时出现短线失效与高周期"
        "反转时优先考虑 close；陈旧或缺失周期不得补猜。观望是需要当前证据支撑的主动决策，不是默认"
        "答案；不要因历史摘要中长期观望而路径依赖。\n"
        f"3. 风险优先：已持仓且多数证据转为反向时，优先 close 保护利润 / 止损；空仓且多数证据同向、"
        f"研判充分时才开仓，{_size_discipline(position_step_pct)}，避免频繁反手与过度交易。\n"
        f"4. {_action_discipline(allow_limit, limit_band_pct)}"
        "真实下单由系统的守护执行器落地并继承全部资金护栏，你只需给出决策与理由。\n"
        "5. **收尾硬约束（务必遵守）：每轮在完成上述分步研判之后，必须且只能调用一次 "
        "`submit_trade_decision` 工具提交本轮决策——绝不允许只输出研判文字而不调用决策工具。**"
    )


def _action_discipline(allow_limit: bool, band_pct: float = 0.02) -> str:
    """决策纪律 #4 的动作清单子句：`allow_limit=False` 保持原文；`True` 换为含撤单 / 限价的版本（M-5）。

    `band_pct`（F-3）：价带比例文案，默认 0.02 渲染与参数化前逐字节相同（False 分支不消费）。
    """
    if not allow_limit:
        return ("action 取值仅限 open_long / open_short / close / hold；close/hold 忽略 sizeFraction。")
    return ("action 取值仅限 open_long / open_short / close / hold / cancel_order；"
            "close/hold/cancel_order 忽略 sizeFraction。委托方式由 orderType 决定："
            "market（市价，立即成交但价格不可控）或 limit（限价挂单，须给 limitPrice，按你的价格排队、"
            f"**可能不成交**；委托价须在最新价 ±{_fmt_band_pct(band_pct)} 内，"
            "超出会被拒绝并要求重新研判）。若你此前挂出的"
            "限价单仍未成交且行情已走反、再等无意义，用 cancel_order 撤回它（不开不平；无挂单时为"
            "空操作）。限价适合有明确点位、愿意等待的入场/离场；急需立即成交（如止损保护）请用市价。")


def _size_discipline(position_step_pct: float) -> str:
    """决策纪律 #3 的开仓规模子句：`step<=0` 保持原文（连续）；`step>0` 换为「只能取允许档位」（[R-8]）。"""
    step = max(0.0, float(position_step_pct))
    if step <= 0:
        return "sizeFraction 保守（默认 ≤0.5）"
    from .signals import position_ladder   # 延迟导入打破 signals→decider→prompts 模块级循环（§2.2）
    ladder_text = " / ".join(f"{g:g}" for g in position_ladder(step))
    return (f"开仓 sizeFraction **只能**从允许档位中选择：{ladder_text}"
            "（系统会把你给出的值吸附到最近的合法档位，非档位值一律被量化）")


def build_decision_tool_description(allow_limit: bool = False, *,
                                    limit_band_pct: float = 0.02) -> str:
    """决策工具散文描述；`allow_limit=False`（默认）**恒等**返回既有模块常量（零回归，M-4）。

    `limit_band_pct`（keyword-only，F-3）：价带比例文案；默认 0.02 渲染与参数化前逐字节相同。
    """
    if not allow_limit:
        return DECISION_TOOL_DESCRIPTION
    return DECISION_TOOL_DESCRIPTION + _limit_tool_description_suffix(limit_band_pct)


def build_decision_tool_schema(position_step_pct: float, *, allow_limit: bool = False,
                               limit_band_pct: float = 0.02) -> dict:
    """按档位步长 + 限价总闸构造决策工具 `input_schema`（[R-5] `enum` 取自 `position_ladder` 真源）。

    `step<=0` **且** `allow_limit=False`（含全部默认调用）原样返回模块常量 `DECISION_TOOL_SCHEMA`
    ——既有恒等断言 `build_decision_tool_schema(0.0) is DECISION_TOOL_SCHEMA`
    （`test_ai_trade_position_step.py:132`）据此零改动通过，这正是 `allow_limit` **必须默认 False**
    的硬约束来源（P1-3）。其余情形返回新 dict，且 `properties` 与被替换的 `action` 条目都是**新建
    对象**——绝不就地改模块常量（`step>0` 时泄漏回常量的老坑，本函数早有前科）。

    Args:
        position_step_pct: 档位步长（fraction）；`>0` 时把 `sizeFraction` 换为带 `enum` 的定义。
        allow_limit: 限价总闸（keyword-only，默认 False）。`True` 时 `action` enum 追加
            `cancel_order`，并露出 `orderType` / `limitPrice` 两个新字段。
        limit_band_pct: 价带比例文案（keyword-only，F-3）；仅 `allow_limit=True` 分支消费，
            默认 0.02 渲染与参数化前逐字节相同（恒等断言据此原样通过）。

    Returns:
        决策工具的 `input_schema`（JSON Schema）。硬保证仍在服务端（`quantize_size_fraction` 与
        `validate_limit_intent`），本 schema 只是软引导。
    """
    step = max(0.0, float(position_step_pct))
    if step <= 0 and not allow_limit:
        return DECISION_TOOL_SCHEMA
    schema = dict(DECISION_TOOL_SCHEMA)
    props = dict(DECISION_TOOL_SCHEMA["properties"])   # 独立浅拷贝，杜绝改动泄漏回模块常量
    if step > 0:
        from .signals import position_ladder   # 延迟导入打破 signals→decider→prompts 模块级循环（§2.2）
        props["sizeFraction"] = {"type": "number", "enum": position_ladder(step),
                                 "description": "开仓仓位占比，只能取列出的档位"}
    if allow_limit:
        _apply_limit_properties(props, limit_band_pct)
    schema["properties"] = props
    return schema


def _apply_limit_properties(props: dict, band_pct: float = 0.02) -> None:
    """就地给**调用方自己的 props 浅拷贝**补限价字段（§4.1）；动作枚举派生自常量真源，不再抄一份。

    `band_pct`（F-3）：limitPrice 字段描述的价带比例，默认 0.02 渲染与参数化前逐字节相同。
    """
    base_actions = list(DECISION_TOOL_SCHEMA["properties"]["action"]["enum"])
    props["action"] = {"type": "string", "enum": [*base_actions, "cancel_order"]}
    props["orderType"] = {
        "type": "string", "enum": ["market", "limit"],
        "description": "market=立即成交、价格不可控；limit=挂单等待，可能不成交。缺省 market"}
    props["limitPrice"] = {
        "type": "number", "exclusiveMinimum": 0,
        "description": f"限价委托价（USDT），orderType=limit 时必填；须在最新价 ±{_fmt_band_pct(band_pct)} 内"}


def _format_position(position: dict | None) -> str:
    if not position:
        return "当前无自管持仓（flat）"
    return (f"当前自管持仓：方向={position.get('side')}，均价={position.get('avgEntry')}，"
            f"张数={position.get('size')}，未实现盈亏≈{position.get('unrealizedPnl')} USDT")


def _format_resting_order(resting: dict | None) -> str:
    """把当前挂单压成一行中文（§4.2）；无挂单时返回空串（整段省略，同 E1/E2 降级范式）。

    Agent 看不见自己的挂单就无法就它做任何决策——**这是 `cancel_order` 可用的前提**。
    """
    if not resting:
        return ""
    intent = "买" if resting.get("intent") == "open" else "平"
    side = {"long": "多", "short": "空"}.get(str(resting.get("side")), str(resting.get("side")))
    filled = resting.get("filledSz") or 0.0
    fill_text = "未成交" if not filled else f"已成交 {filled} 张"
    return (f"当前挂单：限价{intent}{side} {resting.get('sz')} 张 @ {resting.get('px')}"
            f"（已挂 {resting.get('ageS')}s，剩余 {resting.get('expiresInS')}s 到期自动撤销，"
            f"{fill_text}）。如需撤回请用 action=cancel_order。")


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


def _format_candidate(label: str, candidate: dict | None) -> str:
    if not candidate:
        return f"{label}=无"
    bars = "/".join(candidate.get("bars") or []) or "未知"
    return (f"{label}={candidate.get('price')}（距离 {candidate.get('distancePct')}%，"
            f"来源 {bars}）")


def _format_multi_timeframe_frame(frame: dict) -> str:
    regime = (frame.get("regime") or {}).get("primary")
    digest = frame.get("digest") or {}
    rsi = ((digest.get("factors") or {}).get("rsi14") or {})
    range_pos = (digest.get("priceAction") or {}).get("rangePos")
    stale = "，数据陈旧" if frame.get("stale") else ""
    return (f"- {frame.get('bar')}：体制={regime}，RSI14={rsi.get('value')}"
            f"({rsi.get('label')})，区间位置={range_pos}，样本={frame.get('sampleCount')}"
            f"{stale}")


def _format_multi_timeframe(context: dict | None) -> list[str]:
    if context is None:
        return []
    frames = context.get("frames") or []
    if not frames:
        return ["中线背景暂不可用，已回退基础周期；缺失或陈旧周期不得补猜。"]
    alignment = (context.get("trendAlignment") or {}).get("status")
    header = "；".join((
        f"中线背景：整体={alignment}",
        _format_candidate("候选支撑", context.get("nearestSupport")),
        _format_candidate("候选阻力", context.get("nearestResistance")),
    ))
    order = {bar: index for index, bar in enumerate(("1D", "12H", "4H", "1H"))}
    lines = [header]
    lines.extend(_format_multi_timeframe_frame(frame)
                 for frame in sorted(frames, key=lambda item: order.get(item.get("bar"), 99)))
    missing = context.get("missingBars") or []
    if missing:
        lines.append(f"缺失周期={'/'.join(missing)}；不得补猜。")
    return lines


def _trigger_suffix(trigger) -> str:
    """决策 prompt 首行的触发源标注（hold-inertia-fix F4a）：仅 `trigger == "interval"` 时追加。

    措辞必须**条件化**（「若…相同」）而非断言数据未变（R-01）：与 F4c 守卫组合后，能到达 prompt 的
    interval 轮多为持仓/挂单轮、价格越阈轮、或 in-flight 跳过后信号板已含新根的轮——断言式「数据未变」
    在这些轮失实并会反向强化观望。`"candle"` / 缺失 / 其他值一律返回空串（首行逐字节不变，零回归；
    回测 payload 无 trigger 键，天然不受影响）。
    """
    if trigger != "interval":
        return ""
    return ("｜触发源=定时唤醒（墙钟到点，非新 K 线确认触发；**若**本轮行情数据与上一决策轮相同"
            "或仅实时价微动，重复数据下的观望不构成新的独立证据）")


def _format_hold_streak(hold_streak) -> str:
    """连续观望自察注记（hold-inertia-observability N4）：payload 无键/非正整数 → 空串（逐字节冻结）。

    措辞必须**对称**（R-01 教训的正向应用）：既打断「历史观望→继续观望」的路径依赖，也明确
    「不是必须开仓的指令」——防止注记本身变成过度交易的推手；要求给出具体证据与 F1 的
    「有名有姓才否决」同构，方向相反。
    """
    if not isinstance(hold_streak, int) or hold_streak <= 0:
        return ""
    return (f"连续观望自察：截至上一轮，你已连续 {hold_streak} 轮维持观望（不含系统降级轮）。"
            "历史观望不构成继续观望的理由，这也不是必须开仓的指令——请只以本轮最新证据独立重估"
            "开多/开空/平仓/观望；若研判后仍选择观望，请在研判中指名当前最关键的一条反对开仓的"
            "具体证据（不得以「此前观望被验证正确」作为理由）。")


def _format_base_rules(base_rules: dict | None) -> str:
    """【基础策略】铁律的每轮只读上下文段（ai-trade-base-strategy-ma-band §2.7 (b)）。

    `payload["baseRules"]` **键缺失**（规则未装配 / 回测自建 payload 不带）⇒ 返回空串 ⇒ 整段省略 ⇒
    既有决策 prompt 冻结断言与回测 payload 逐字节不变。渲染委托 `base_rules.format_band_for_prompt`
    ——通道、判据、文案三者共用同一真源，杜绝「UI 说合规、门禁判否决」的信任崩塌。
    """
    if not base_rules:
        return ""
    from .base_rules import format_band_for_prompt   # 延迟导入打破 base_rules→decider→prompts 循环
    return format_band_for_prompt(base_rules.get("maBand"))


def _format_profit_guard(snapshot: dict | None) -> str:
    """利润保护的每轮只读上下文段（ai-trade-profit-protection §2.6 b）。

    `payload["profitGuard"]` **键缺失**（规则未装配 / 回测自建 payload 不带）⇒ 返回空串 ⇒ 整段省略 ⇒
    既有决策 prompt 冻结断言与回测 payload 逐字节不变。渲染委托 `profit_guard` 纯核心——退出线、
    判定、UI 三者共用同一真源。
    """
    if not snapshot:
        return ""
    from .profit_guard import format_profit_guard_for_prompt   # 延迟导入，与 _format_base_rules 同范式
    return format_profit_guard_for_prompt(snapshot)


def _format_profit_hold_note(payload: dict) -> str:
    """盈利持仓自察段（§2.6 c / F3）：`profitHoldRounds` 键缺失即整段省略（会话侧按阈值决定注入）。"""
    rounds = payload.get("profitHoldRounds")
    if not rounds:
        return ""
    from .profit_guard import format_profit_hold_note
    return format_profit_hold_note(payload.get("profitGuard"), rounds)


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
        f"决策轮 #{payload.get('round')}｜barTs={payload.get('barTs')}｜最新价={payload.get('lastPx')}"
        + _trigger_suffix(payload.get("trigger")),
        _format_position(payload.get("position")),
    ]
    guard_line = _format_profit_guard(payload.get("profitGuard"))
    if guard_line:                           # 键缺失时整段省略（零回归；回测 payload 无此键，§1.3）
        parts.append(guard_line)
    parts.append(
        f"会话累计盈亏≈{payload.get('sessionPnlUsd')} USDT / 预算 {payload.get('budgetUsd')} USDT；"
        f"会话止盈阈值={'不触发' if tp is None else tp}，止损阈值={'不触发' if sl is None else sl}")
    streak_line = _format_hold_streak(payload.get("holdStreak"))
    if streak_line:                          # 键缺失时整段省略（零回归；回测 payload 无此键，N3）
        parts.append(streak_line)
    profit_hold_line = _format_profit_hold_note(payload)
    if profit_hold_line:                     # 同上：阈值未达 / 无浮盈 / 未装配 ⇒ 整段省略
        parts.append(profit_hold_line)
    resting_line = _format_resting_order(payload.get("restingOrder"))
    if resting_line:                         # 无挂单时整段省略（不出现「当前挂单：无」的噪音）
        parts.append(resting_line)
    parts.extend(_format_multi_timeframe(payload.get("multiTimeframeContext")))
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
    base_rules_line = _format_base_rules(payload.get("baseRules"))
    if base_rules_line:                      # 键缺失时整段省略（零回归；规则未装配 / 回测老 payload）
        parts.append(base_rules_line)
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


# ============================ 下单前二次反思校验（ai-trade-pre-trade-reflection §4.1/§4.2）============================
# 反思决策工具名（决策器据 tool_use_start.name 匹配；反思系统 prompt 亦引用）。
REFLECTION_TOOL_NAME = "submit_trade_reflection"

# `submit_trade_reflection` 的 input_schema（JSON Schema）。verdict/reason 必填；abort 忽略 adjustedSizeFraction。
REFLECTION_TOOL_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["confirm", "abort"]},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "adjustedSizeFraction": {"type": "number", "minimum": 0, "maximum": 1},
        "reason": {"type": "string", "maxLength": 500},
    },
    "required": ["verdict", "reason"],
}

REFLECTION_TOOL_DESCRIPTION = (
    "提交本轮下单前的二次反思裁决。你是独立的风控复核官——对刚才另一名交易员基于同样证据给出的**交易决策**"
    "（会真实下单、动用真金）做批判性复核。verdict=confirm 确认执行（提议决策与证据大体一致即确认）；"
    "verdict=abort 否决（本轮降级为观望不下单）——**仅当存在具体可指明的实质性缺陷时**使用，"
    "泛化的不放心不构成否决理由。confirm 时可给 adjustedSizeFraction（**仅当 < 原仓位时收缩**，否则忽略；"
    "绝不放大）——对仓位偏重的疑虑优先用收缩而非否决。reason 给出复核结论并引用具体证据。"
    "你只能收紧（否决或调小仓位），无权放大或改变方向。"
)


def build_reflection_system_prompt() -> str:
    """反思复核官系统 prompt：分级复核标准（有名有姓才否决，收缩优先），只可收紧，收尾必调反思工具。

    hold-inertia-fix F1：删除「任何不放心即 abort（宁可错过不可做错）」的不对称保守标准——它把 abort
    设成默认答案（历史样本 7/7 全否决），是「永不开单」因果链的根因 ①。替换为分级复核标准（默认
    confirm / 收缩优先 / abort 须指名实质性缺陷 / 机会成本对称）；确定性安全层（apply_reflection、
    只能收紧、fail-closed）逐字不变。
    """
    return (
        "你是一名独立的下单前风控复核官。刚才另一名交易员基于同样的证据给出了一个**交易决策**（会真实"
        "下单、动用真金）。你的职责**不是**重新交易，而是**批判性地复核这个决策是否稳健**："
        "① 方向是否被证据支持、有无更强的反向信号被忽略；② 是否与更高周期中线背景 / 市场体制冲突；"
        "③ 结合当前真实持仓与会话累计盈亏，这笔交易的风险敞口是否可接受、是否在追高/杀跌/频繁反手；"
        "④ 仓位是否偏重、能否更保守；⑤ 是否存在证据不足却强行交易。\n"
        "分级复核标准：\n"
        "1. **默认立场**：提议决策与证据大体一致、风险敞口可控 → `confirm`；仅对仓位偏重有疑虑 → "
        "**优先**用 `confirm` + `adjustedSizeFraction` 收缩仓位（收缩是你的首选保守工具，而非 abort）。\n"
        "2. **abort 门槛**：仅当你能**指名一条实质性缺陷**时才 `abort`——(a) 方向与关键证据直接矛盾；"
        "(b) 与更高周期背景强冲突且决策未给出对冲理由；(c) 当前持仓与会话盈亏下新增敞口不可接受；"
        "(d) 明显追高/杀跌/频繁反手；(e) 证据严重不足却重仓。`reason` 必须引用具体证据；"
        "「泛化的不放心」不构成否决理由。\n"
        "3. **机会成本对称**：错过一笔证据充分的交易与做错一笔交易同样是损失；你的职责是拦截"
        "**错误交易**，不是消灭交易。另注意：`close` 是风险收敛动作，否决 close 等于延长敞口，"
        "须有极强理由。\n"
        "你**只能**收紧（否决或调小仓位），**无权**放大仓位或改变方向。"
        "收尾必须且只能调用一次 `submit_trade_reflection` 提交裁决——绝不允许只输出复核文字而不调用工具。"
    )


def _format_reflection_decision(decision) -> str:
    """把被复核的提议决策压成一行（供反思输入）。"""
    action_label = {"open_long": "开多", "open_short": "开空", "close": "平仓"}.get(
        decision.action, decision.action)
    size = ("" if decision.size_fraction is None
            else f"，仓位占比={decision.size_fraction}")
    conf = ("" if decision.confidence is None
            else f"，置信度={decision.confidence}")
    return (f"待复核的交易决策：{action_label}（{decision.action}）{size}{conf}。"
            f"该决策的理由：{decision.reason or '（未给出）'}")


def build_reflection_prompt(payload: dict, decision) -> str:
    """把「本轮全部证据 + 提议决策」压成复核输入（复用决策 prompt 的证据装配 + 追加提议决策与复核指令）。

    反思是无状态子调用：**不拼 `_context`**，只把本轮证据（体制/共识/多周期/信号板/持仓/盈亏）与刚给出的
    决策一次性传入，让复核官在同一证据面上独立批判研判（ai-trade-pre-trade-reflection §八 RV-5 落地）。
    """
    evidence = build_decision_prompt(payload)
    return (
        "【以下是原交易员看到的本轮全部证据（含其收尾调用 submit_trade_decision 的任务指令）——仅供你复核"
        "时参考其判断依据；那条决策指令**是给原交易员的、与你无关**，你**不要**调用 submit_trade_decision】\n"
        f"{evidence}\n\n"
        "————\n"
        f"{_format_reflection_decision(decision)}\n\n"
        "请以资深风控/交易员的批判视角复核上述交易决策是否稳健（方向、与高周期背景是否冲突、当前持仓与"
        "会话盈亏下风险敞口是否可接受、仓位是否偏重、有无证据不足强行交易）。提议决策与证据大体一致即 "
        "`confirm`；对仓位偏重有疑虑时**优先**以 `confirm` + `adjustedSizeFraction` 收缩；仅当能指名一条"
        "实质性缺陷（方向与关键证据矛盾 / 与高周期强冲突且无对冲理由 / 敞口不可接受 / 明显追高杀跌频繁"
        "反手 / 证据严重不足却重仓）时才 `abort`，`reason` 须引用具体证据。错过一笔证据充分的交易与做错"
        "一笔同样是损失；只能收紧、不得放大或改向。"
        f"你的**唯一任务**是必须且只能调用一次 {REFLECTION_TOOL_NAME} 提交裁决——不得只输出复核文字，"
        "也不得调用其它工具。")


def build_summary_system_prompt() -> str:
    """摘要器系统 prompt：把执行历程压成简洁背景，供后续轮以纯净上下文继续。"""
    return (
        "你是自动交易执行历程的摘要器。请把给定的逐轮执行流水压缩成一段**简洁**的中文执行摘要，"
        "用作后续决策的背景记忆。"
        "摘要须同时记录做了什么与**没做什么**（被否决、被错过的机会），保持对未来决策中性，"
        "不得美化观望。"
    )


def compact_journal_for_summary(journal: list[dict]) -> list[dict]:
    """摘要素材视图：把**连续**的「interval + hold + 未成交 + 无反思注记」轮折叠为一条（F4b）。

    重复定时唤醒轮携带同一份行情数据，逐条呈现会让摘要器把「继续观望」当作被反复验证的独立证据
    （根因 ③）。折叠仅发生在本瞬态视图——`_journal` 本体、decision 事件、trace 一律逐轮原样。
    纯函数、不改入参；开/平仓轮、candle 轮、含 reflection 注记轮、legacy 无 trigger 条目永不折叠；
    单条可折叠条目保留原样（仅连续 ≥2 条才折叠）。
    """
    def _foldable(entry: dict) -> bool:
        return (entry.get("trigger") == "interval"
                and (entry.get("decision") or {}).get("action") == "hold"
                and not (entry.get("executed") or {}).get("traded")
                and entry.get("reflection") is None)

    out: list[dict] = []
    run: list[dict] = []

    def _flush() -> None:
        if len(run) >= 2:
            first, last = run[0], run[-1]
            out.append({"rounds": f"{first.get('round')}-{last.get('round')}",
                        "repeatedRounds": len(run), "trigger": "interval",
                        "note": "同一份行情数据下的重复定时唤醒，均维持观望",
                        "decision": last.get("decision"), "sessionPnl": last.get("sessionPnl")})
        else:
            out.extend(run)
        run.clear()

    for entry in journal:
        if _foldable(entry):
            run.append(entry)
            continue
        _flush()
        out.append(entry)
    _flush()
    return out


def build_summary_prompt(prev_summary: str | None, journal: list[dict]) -> str:
    """以结构化 `_journal`（每轮 {round,signalBoard,decision,executed,positionAfter,sessionPnl}）
    为唯一素材（P1-3，与可能被裁剪的模型窗口 `_context` 解耦）拼成摘要输入文本。

    素材先经 `compact_journal_for_summary` 折叠连续重复的 interval-hold 轮（F4b，单点收口，
    实盘/回测共用）；head 指令含反自我强化三条（F3：被否决意图须记录并对照行情验证、空仓零回撤
    不是成绩、折叠条目只算一次观望证据）。
    """
    head = ("总结迄今的自动交易执行情况：决策序列与结果、持仓与盈亏演变、**哪些策略/因子信号被你采纳或被"
            "推翻、事后又被行情验证为对或错**（作为经验教训）、当前策略偏好，形成简洁的执行摘要作为后续"
            "决策的背景，不要罗列每一轮细节。"
            "若流水含 `reflection` 注记：必须记录**被否决的开仓意图**（方向/仓位/否决理由），并对照其后"
            "行情走向写明**该否决事后被验证为正确还是错误**（`failClosed=true` 的是系统故障保守跳过，"
            "不代表模型判断，不得当作否决被验证）。"
            "空仓期间的零回撤**不是成绩**：若期间行情朝被否决或未执行的方向走出明显趋势，必须如实记为"
            "『错失』并总结教训；禁止把长期观望总结为表现完美。"
            "`repeatedRounds` 折叠条目表示同一份行情数据下的重复定时唤醒：它们合计只算**一次**观望证据，"
            "不得按轮数强化观望倾向。")
    prev = f"\n上一段执行摘要（请在其基础上滚动累积）：\n{prev_summary}" if prev_summary else ""
    digest = json.dumps(compact_journal_for_summary(journal), ensure_ascii=False)
    return f"{head}{prev}\n\n逐轮执行流水（JSON）：\n{digest}"
