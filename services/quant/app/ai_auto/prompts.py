"""AI 自动交易决策器与摘要器的 prompt 与决策工具 schema（ai-auto-trading §2.3 / §2.4）。

- `DECISION_TOOL_SCHEMA`：唯一放行工具 `submit_trade_decision` 的 `input_schema`（JSON Schema）。
  喂 LLM 层时必须封装为 `ToolSchema(name, description, input_schema=DECISION_TOOL_SCHEMA)`（P1-5）。
- `build_ai_trade_system_prompt`：决策者角色 / 纪律 / 风险偏好 / 只用真数据 / 强力引导调用决策工具。
- `build_decision_prompt`：把本轮信号板 + 真实持仓 + 会话盈亏 + 长期记忆摘要压成紧凑决策输入。
- `build_summary_system_prompt` / `build_summary_prompt`：轮次滚动摘要（以 `_journal` 为素材，P1-3）。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, replace

from .prompts_en import (
    DECISION_TASK_INSTRUCTION_EN,
    DECISION_TOOL_DESCRIPTION_EN,
    REFLECTION_TASK_INSTRUCTION_EN,
    SystemPromptOptions,
    SystemPromptTexts,
    exit_priority_tool_suffix_en,
    hold_exit_tool_suffix_en,
    limit_tool_suffix_en,
    render_review_spine_en,
    render_spine_en,
)

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


def _hold_exit_tool_description_suffix() -> str:
    """承诺退出线能力开启时（allow_hold_exit=True）追加进决策工具描述的散文说明（C1 §2.1.2）。

    基础文案（`DECISION_TOOL_DESCRIPTION`）**一字不改**——总闸关闭时描述与本特性合入前逐字相同（G-2）。
    """
    return (
        "另可在 action=hold（继续持有）时给出 holdExitPx：你承诺的退出价位。系统会把它采集为一条保护线，"
        "价格触及即**由系统直接市价平仓**——这条线**只能随你收紧、不能放松**（可报更靠近现价的新线覆盖旧线，"
        "报更松的线不会生效），且**绝不会劣于保本线**。给了它等于让系统替你守住你自己的判断，你无需每轮盯盘；"
        "不给它则仅由系统的移动止盈线兜底。多头填低于现价的价、空头填高于现价的价。"
    )


# 九个旧关键字（六个文本段 + 三个开关）的兼容垫片键集（[P1-8]）。仓库里有上百条冻结断言以
# 关键字直连调用 `build_ai_trade_system_prompt`，垫片不可省；但**未知键必须抛 TypeError**——
# 一个拼错的关键字被 `**legacy` 静默吞掉，症状是「宪章章没进 prompt 而且不报错」。
_LEGACY_TEXT_KEYS = ("base_rules_text", "profit_guard_text", "sizing_text",
                     "tactical_bars_text", "cost_text", "horizon_text")
_LEGACY_OPTION_KEYS = ("position_step_pct", "allow_limit", "limit_band_pct")


def _fold_legacy_kwargs(texts: "SystemPromptTexts | None",
                        options: "SystemPromptOptions | None",
                        legacy: dict) -> tuple[SystemPromptTexts, SystemPromptOptions]:
    """把旧关键字折进两个聚合结构（两种写法产出 sha256 相同，用例 G-1b 冻结）。"""
    unknown = set(legacy) - set(_LEGACY_TEXT_KEYS) - set(_LEGACY_OPTION_KEYS)
    if unknown:
        raise TypeError("build_ai_trade_system_prompt() 收到未知关键字："
                        f"{sorted(unknown)}（旧键仅限 {_LEGACY_TEXT_KEYS + _LEGACY_OPTION_KEYS}）")
    merged_texts = texts or SystemPromptTexts()
    text_changes = {k: legacy[k] for k in _LEGACY_TEXT_KEYS if legacy.get(k)}
    if text_changes:
        merged_texts = replace(merged_texts, **text_changes)
    merged_options = options or SystemPromptOptions()
    option_changes = {k: legacy[k] for k in _LEGACY_OPTION_KEYS if k in legacy}
    if option_changes:
        merged_options = replace(merged_options, **option_changes)
    return merged_texts, merged_options


def build_ai_trade_system_prompt(inst_id: str, bar: str, budget_usd: float, leverage: float, *,
                                 texts: SystemPromptTexts | None = None,
                                 options: SystemPromptOptions | None = None,
                                 **legacy: object) -> str:
    """决策者系统 prompt：中文脊柱（默认）或英文脊柱（`texts.reasoning_text` 非空时）。

    [P1-8] 形参由 13 收敛到 6（4 位置 + `texts` + `options`），兑现
    `.claude-index/config.md` 偏离表首行那条已登记的还债指示。九个旧关键字
    （`base_rules_text` / `profit_guard_text` / `sizing_text` / `tactical_bars_text` /
    `cost_text` / `horizon_text` / `position_step_pct` / `allow_limit` / `limit_band_pct`）
    保留为兼容垫片：收到即折进两个聚合结构，产出 sha256 与直传聚合相同。

    分叉（ai-trade-deep-reasoning-chain §3.1.6）：
    - `texts.reasoning_text == ""`（默认 / `off` 档）⇒ 走今日的中文渲染路径，**一个字符都不改**
      （sha256 冻结门禁 G-7 / G-1 据此成立）；
    - 非空 ⇒ 走 `prompts_en.render_spine_en`：英文脊柱 + 语言契约 + 术语表 + **中文五段宪章**
      + 审议协议 + 五条英文纪律（第 5 条仍是收尾硬约束，占近因位）。

    各文本段的语义与插入位置与合入前逐字一致，详见 `SystemPromptTexts` 各字段。
    """
    texts, options = _fold_legacy_kwargs(texts, options, legacy)
    if texts.reasoning_text:
        return render_spine_en(inst_id, bar, budget_usd, leverage, texts=texts,
                               position_step_pct=options.position_step_pct,
                               allow_limit=options.allow_limit,
                               band_text=_fmt_band_pct(options.limit_band_pct))
    return _render_spine_zh(inst_id, bar, budget_usd, leverage,
                            texts=texts, options=options)


def format_direction_charter(enabled: bool) -> str:
    """系统 prompt 的**方向对称条款**（directional-symmetry C-2）；未启用 ⇒ **空串垫片**。

    与既有五段宪章逐字同构（`base_rules.format_charter` 等），故零新机制：空串 ⇒
    两条脊柱的拼接结果与本特性合入前逐字节相同。

    它是**软引导层**：硬保证在共识门禁的方向对称通道（轴 A）与十二阶段协议的 S10 / S11
    硬约束（轴 C-1）。三者缺一不可：只改协议，模型提了做空也会被门禁拦下；
    只改门禁，模型从来不提做空。
    """
    if not enabled:
        return ""
    return (
        "**方向对称条款**：这是一个可以双向交易的永续合约账户。"
        "做空与做多在本系统里是**完全对等**的一等动作，"
        "二者共用同一套仓位、成本、止损与门禁标准。"
        "上涨行情里做多不是默认答案，它只是「当下证据更强的那一个答案」；"
        "同理，下跌行情里做空也不是默认答案。"
        "你必须在 S10 同时给出做多与做空两个候选并各自算出净期望值，"
        "然后在 S12 说明你为什么选了这一个、放弃了另一个。"
        "**只列出一个方向的候选，等同于没有完成 S10。**"
    )


def _render_spine_zh(inst_id: str, bar: str, budget_usd: float, leverage: float, *,
                     texts: SystemPromptTexts, options: SystemPromptOptions) -> str:
    """中文脊柱（`off` 档的真源）。

    本特性合入前的**逐字节**原文，勿改一个字符。唯一的变量是孪生拼接里多出的第六段
    （directional-symmetry C-2 的 `direction_text`）：未装配 ⇒ 空串 ⇒ 拼接结果与合入前逐字节相同。

    ⚙ 这份拼接与 `SystemPromptTexts.charter()` 是**两份同款内联**（中文脊柱根本不调 `charter()`），
    改一处必须改两处——只改一处，条款就只出现在一条语言路径上（评审 P1-4）。
    """
    charter = "".join(
        f"{text}\n\n" for text in
        (texts.base_rules_text, texts.profit_guard_text, texts.sizing_text,
         texts.cost_text, texts.horizon_text, texts.direction_text) if text)
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
        "1D → 12H → 4H → 1H 研判中线背景、趋势一致性、候选支撑阻力与超买/超卖，"
        f"{_tactical_discipline(texts.tactical_bars_text)}再依次思考："
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
        f"研判充分时才开仓，{_size_discipline(options.position_step_pct, bool(texts.sizing_text))}，"
        "避免频繁反手与过度交易。\n"
        f"4. {_action_discipline(options.allow_limit, options.limit_band_pct)}"
        "真实下单由系统的守护执行器落地并继承全部资金护栏，你只需给出决策与理由。\n"
        "5. **收尾硬约束（务必遵守）：每轮在完成上述分步研判之后，必须且只能调用一次 "
        "`submit_trade_decision` 工具提交本轮决策——绝不允许只输出研判文字而不调用决策工具。**"
    )


def _tactical_discipline(tactical_bars_text: str) -> str:
    """决策纪律 #1 的战术周期核对子句；空串 ⇒ 空串（系统 prompt 渲染逐字节回退，G-1）。

    措辞里的「不得单独反转中线判断」是硬约束不是客套：1m 级别信号横跳，模型被短周期带偏会直接抬高
    交易频次、被成本吃掉——那正好反向恶化本特性要解决的「总是亏损」。
    """
    if not tactical_bars_text:
        return ""
    return (f"再用战术周期（{tactical_bars_text}）核对执行时机（本级别是否已形成有效突破/回踩、"
            "量能是否配合、还是仅为噪音；战术周期只用于择时与确认，不得单独反转中线判断），")


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


def _size_discipline(position_step_pct: float, sizing_enabled: bool = False) -> str:
    """决策纪律 #3 的开仓规模子句：`step<=0` 保持原文（连续）；`step>0` 换为「只能取允许档位」（[R-8]）。

    `sizing_enabled`（末位，默认 False → 渲染逐字节不变、零回归）：仓位层装配时**追加**一句上限声明
    （vol-sizing §3.8）——**不替换**原句：原句讲的是档位、新句讲的是上限，两者不冲突且都必须说清。
    """
    step = max(0.0, float(position_step_pct))
    if step <= 0:
        base = "sizeFraction 保守（默认 ≤0.5）"
    else:
        from .signals import position_ladder   # 延迟导入打破 signals→decider→prompts 模块级循环（§2.2）
        ladder_text = " / ".join(f"{g:g}" for g in position_ladder(step))
        base = (f"开仓 sizeFraction **只能**从允许档位中选择：{ladder_text}"
                "（系统会把你给出的值吸附到最近的合法档位，非档位值一律被量化）")
    if not sizing_enabled:
        return base
    return (base + "；且你的 sizeFraction 是**上限提议**：系统会按波动率、胜率先验、成本与组合敞口"
            "独立解一个上限，并取两者较小值执行")


def build_decision_tool_description(allow_limit: bool = False, *,
                                    limit_band_pct: float = 0.02,
                                    allow_hold_exit: bool = False,
                                    allow_exit_priority: bool = False,
                                    lang: str = "zh") -> str:
    """决策工具散文描述；三个总闸皆假 **且** `lang="zh"` 时**恒等**返回模块常量（零回归，M-4）。

    `limit_band_pct`（keyword-only，F-3）：价带比例文案；默认 0.02 渲染与参数化前逐字节相同。
    `allow_hold_exit`（keyword-only，C1）：为真时在尾部追加承诺退出线说明；关闭时逐字节回退（G-2）。
    `allow_exit_priority`（keyword-only，exit-priority §6.1）：同款——为真时追加离场优先通道说明。
    `lang`（keyword-only，deep-reasoning-chain §3.1.7）：`"zh"`（默认）⇒ 今日中文常量与中文
        后缀，逐字节回退；`"en"` ⇒ **整体**换英文（基础描述与三段后缀一并换，绝不中英混排）。
    """
    english = str(lang or "zh").strip().lower() == "en"
    if not english and not allow_limit and not allow_hold_exit and not allow_exit_priority:
        return DECISION_TOOL_DESCRIPTION
    text = DECISION_TOOL_DESCRIPTION_EN if english else DECISION_TOOL_DESCRIPTION
    if allow_limit:
        text += (limit_tool_suffix_en(_fmt_band_pct(limit_band_pct)) if english
                 else _limit_tool_description_suffix(limit_band_pct))
    if allow_hold_exit:
        text += hold_exit_tool_suffix_en() if english else _hold_exit_tool_description_suffix()
    if allow_exit_priority:
        text += (exit_priority_tool_suffix_en() if english
                 else _exit_priority_tool_description_suffix())
    return text


def _exit_priority_tool_description_suffix() -> str:
    """离场优先通道开启时（allow_exit_priority=True）追加进决策工具描述的散文说明（§6.1）。

    基础文案（`DECISION_TOOL_DESCRIPTION`）**一字不改**——总闸关闭时描述与本特性合入前逐字相同。
    """
    return (
        "另可在 action=close 时给 exitConviction 声明本次离场的确信度：normal=常规离场；"
        "high=离场理由已成熟，请求跳过「离场连续确认」这一条约束（换手迟滞仍生效）；"
        "final=你已在多轮研判中确认持仓理由失效，请求跳过全部三条约束立即执行。"
        "使用 final 必须同时在 exitEvidence 里指名「持仓理由已失效」的**新证据**"
        "（不是重复上一轮的判断）；本会话 final 配额有限，用尽后按 high 处理，且每一次都会被记录"
        "并在会话结束时与真实结果对照。"
    )


# 逆势证据串的截断上限（与 `evidence_digest` 同款「有界字段」纪律；判定字数下限的真源在
# `signals._contrarian_block`，本常量只管截断）。
_CONTRARIAN_EVIDENCE_MAX_CHARS = 600


@dataclass(frozen=True)
class ContrarianSchemaOptions:
    """决策工具 schema 里 `contrarianEvidence` 那段描述要用到的三个阈值（A-3）。

    它们**必须由后端下发进 prompt**：模型不知道门槛是多少就永远写不够字数，逆势通路就成了一个
    「有写点没读点」的功能。三个数的唯一真源是会话那份 `ConsensusRule`。
    """

    min_chars: int
    min_confidence: float
    size_mult: float


def build_decision_tool_schema(position_step_pct: float, *, allow_limit: bool = False,
                               limit_band_pct: float = 0.02,
                               allow_hold_exit: bool = False,
                               allow_exit_priority: bool = False,
                               allow_reasoning_digest: bool = False,
                               contrarian: ContrarianSchemaOptions | None = None) -> dict:
    """按档位步长 + 限价总闸 + 承诺退出线总闸构造决策工具 `input_schema`（[R-5] `enum` 取真源）。

    `step<=0` **且** `allow_limit=False` **且** `allow_hold_exit=False`（含全部默认调用）原样返回模块
    常量 `DECISION_TOOL_SCHEMA`——既有恒等断言 `build_decision_tool_schema(0.0) is DECISION_TOOL_SCHEMA`
    （`test_ai_trade_position_step.py:132`）据此零改动通过，这正是 `allow_limit` / `allow_hold_exit`
    **必须默认 False** 的硬约束来源（P1-3 / R-5）。其余情形返回新 dict，且 `properties` 与被替换的
    `action` 条目都是**新建对象**——绝不就地改模块常量（`step>0` 时泄漏回常量的老坑，本函数早有前科）。

    Args:
        position_step_pct: 档位步长（fraction）；`>0` 时把 `sizeFraction` 换为带 `enum` 的定义。
        allow_limit: 限价总闸（keyword-only，默认 False）。`True` 时 `action` enum 追加
            `cancel_order`，并露出 `orderType` / `limitPrice` 两个新字段。
        limit_band_pct: 价带比例文案（keyword-only，F-3）；仅 `allow_limit=True` 分支消费，
            默认 0.02 渲染与参数化前逐字节相同（恒等断言据此原样通过）。
        allow_hold_exit: 承诺退出线总闸（keyword-only，C1，默认 False）。`True` 时露出可选
            `holdExitPx` 字段（`required` 不变，承诺退出线永远可选）。
        contrarian: 逆势通路的 schema 选项（directional-symmetry A-3，keyword-only）。
            `None`（默认）⇒ 不露出 `contrarianEvidence` ⇒ schema 逐字节回退。

    Returns:
        决策工具的 `input_schema`（JSON Schema）。硬保证仍在服务端（`quantize_size_fraction` 与
        `validate_limit_intent`），本 schema 只是软引导。
    """
    step = max(0.0, float(position_step_pct))
    # 【R:P1-9a】早退判据必须同时含每一个总闸——漏一个，该总闸开着时 schema 会原样返回模块常量，
    # 新字段永远露不出来，而**不报任何错**（G-2 的直接依赖）。`allow_reasoning_digest`
    # （deep-reasoning-chain §3.1.7）同理。
    if (step <= 0 and not allow_limit and not allow_hold_exit and not allow_exit_priority
            and not allow_reasoning_digest and contrarian is None):
        return DECISION_TOOL_SCHEMA
    schema = dict(DECISION_TOOL_SCHEMA)
    props = dict(DECISION_TOOL_SCHEMA["properties"])   # 独立浅拷贝，杜绝改动泄漏回模块常量
    if step > 0:
        from .signals import position_ladder   # 延迟导入打破 signals→decider→prompts 模块级循环（§2.2）
        props["sizeFraction"] = {"type": "number", "enum": position_ladder(step),
                                 "description": "开仓仓位占比，只能取列出的档位"}
    if allow_limit:
        _apply_limit_properties(props, limit_band_pct)
    if allow_hold_exit:
        # C1 §2.1.2：可选承诺退出线（`required` 不变）。仅在调用方自己的 props 浅拷贝上补，绝不改常量。
        props["holdExitPx"] = {
            "type": "number", "exclusiveMinimum": 0,
            "description": "（可选）继续持有时你承诺的退出价位：价格向此价靠拢并触及即由系统市价平仓。"
                           "多头填低于现价的止盈保护价、空头填高于现价的价；系统只会据它把保护线收得更紧、"
                           "绝不放松，且不会劣于保本线。给了它等于让系统替你守住这条线，你无需每轮盯盘。"}
    if allow_exit_priority:
        _apply_exit_priority_properties(props)
    if allow_reasoning_digest:
        # deep-reasoning-chain §3.1.7：可选的中文证据摘要（`required` 不变，永远可选）。仅在
        # 调用方自己的 props 浅拷贝上补，绝不改模块常量。
        # ⚠️ 它**绝不**参与任何交易语义：缺席 / 非字符串 / 超长 / 写成英文 四种情形一律不产生
        # degrade（用例 D-11 / D-12）——一个摘要写错是排版问题，不是安全问题。
        props["evidenceDigest"] = {
            "type": "string", "maxLength": 400,
            "description": "（可选，用简体中文写）给人类操作员看的证据摘要：你采纳了哪些信号、"
                           "推翻了哪些、决定性证据是什么、什么会让你改变主意。不要复述行情数字。"}
    if contrarian is not None:
        # directional-symmetry A-3：逆势开仓证据（`required` 不变，永远可选）。字数下限由后端的
        # 纯核心判定并产出 `low_consensus_evidence` 码——这里只把那个数**如实告诉模型**，绝不
        # 在前端 / schema 里再写死一份（两处各写一个必然分叉，而分叉不会报错）。
        props["contrarianEvidence"] = {
            "type": "string", "maxLength": _CONTRARIAN_EVIDENCE_MAX_CHARS,
            "description": (
                f"（可选，用简体中文写，不少于 {contrarian.min_chars} 字）当你的开仓方向与信号板"
                "净立场相反时**必须**填：你推翻了哪几条信号、依据是什么、什么现象会让你立刻认错。"
                "写满并把 confidence 提到 "
                f"{contrarian.min_confidence:g} 以上，系统会放行这笔逆势开仓，但仓位强制乘以 "
                f"{contrarian.size_mult:g}。顺势开仓不需要填。")}
    schema["properties"] = props
    return schema


def _apply_exit_priority_properties(props: dict) -> None:
    """就地给**调用方自己的 props 浅拷贝**补两个可选离场字段（§6.1）；`required` 不变。

    两者永远可选：一个读不懂 / 没给出的确信度不该把 `close`（最安全的动作）降级——纯核心
    `exit_priority.normalize_conviction` 对值域外一律归一为 `normal`，绝不据它降级。
    """
    props["exitConviction"] = {
        "type": "string", "enum": ["normal", "high", "final"],
        "description": "（可选，**仅 action=close 时有意义**）本次离场的确信度。"
                       "normal=常规离场；high=离场理由已成熟，请求跳过离场连续确认；"
                       "final=你已在多轮研判中确认持仓理由失效，请求最高优先级立即离场"
                       "（必须同时给出 exitEvidence，本会话配额有限且会被记录核对）。"}
    props["exitEvidence"] = {
        "type": "string", "maxLength": 300,
        "description": "（可选）exitConviction=final 时必填：指名「持仓理由已失效」的新证据"
                       "——不是重复上一轮的判断。"}


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
    """把策略共识度压成一行（E2）；`consensus is None` 时返回空串（该段省略）。

    directional-symmetry A-5：`contrarian` 子对象存在时**追加一行**，把「存在这条门槛、走逆势
    通路要满足什么、还剩几次」如实告诉模型。合入前 `minConsensus` 这个阈值在实盘 payload 里
    **根本不存在**——模型不但事后不知道自己被拦了（轴 B），事前也不知道有这条门槛。
    整键缺席（总闸关 / 通路不可用）⇒ 该行不渲染 ⇒ prompt 逐字节回退。
    """
    if not consensus:
        return ""
    head = (f"策略共识：{consensus.get('longVotes')}多 / {consensus.get('shortVotes')}空 / "
            f"{consensus.get('flatVotes')}空仓，净立场={consensus.get('netStance')}，"
            f"一致度={consensus.get('agreement')}")
    lane = _format_contrarian_lane(consensus.get("contrarian"))
    return f"{head}\n{lane}" if lane else head


def _format_contrarian_lane(contrarian: dict | None) -> str:
    """逆势通路那一行（A-5）；整键缺席 / 不可用 ⇒ 空串 ⇒ 上一行逐字节回退。

    这一行是**方向对称的**：措辞讲的是「与信号板净立场相反」，而不是「做空」。给做空开后门与
    给做多开后门是同一种偏斜，只是方向相反。
    """
    if not contrarian or not contrarian.get("available"):
        return ""
    remaining = int(contrarian.get("remaining") or 0)
    if remaining <= 0:
        return ("逆势通路：本会话的逆势试探名额已用尽，与信号板净立场相反的开仓本轮不会被放行"
                "（平仓与顺势开仓不受影响）。")
    return (f"逆势通路：若你要开的方向与信号板净立场相反、同向票数不足门槛，请把 confidence 提到 "
            f"≥{float(contrarian.get('minConfidence') or 0):g} 并在 `contrarianEvidence` 里用不少于 "
            f"{int(contrarian.get('minEvidenceChars') or 0)} 字写清你推翻了哪几条信号、"
            f"什么现象会让你立刻认错——系统会放行，但仓位强制乘以 "
            f"{float(contrarian.get('sizeMult') or 0):g}。本会话还剩 {remaining} 次；"
            f"反向票数超过 {int(contrarian.get('maxOpposingVotes') or 0)} 票时本通路不开放。")


def _format_signal_priors(priors: dict | None) -> str:
    """把信号先验压成 2~3 行（§3.9）；`priors is None` 时返回空串（整段省略，同 E1/E2 降级范式）。

    三条渲染硬约束：① 必须写明「历史统计，不是当下保证」——它是对过去的度量，而加密市场体制切换
    极快；② 证据明细至多 `top_n` 条（已在 `resolve_direction_prior` 截好，此处不再截）；
    ③ 键缺失 ⇒ 整段省略 ⇒ 对不含 `signalPriors` 的 payload 输出与合入前**逐字节**相同（G-1）。
    """
    if not priors:
        return ""
    age_days = (priors.get("ageS") or 0) / 86400.0
    head = (f"信号先验（历史统计，非当下保证；毛口径，前瞻 {priors.get('horizonBars')} 根，"
            f"快照 {age_days:.1f} 天前 · 体制桶={priors.get('regimeUsed') or '—'}）：")
    lines = [head, _format_prior_directions(priors)]
    evidence = _format_prior_contributors(priors)
    if evidence:
        lines.append(evidence)
    return "\n".join(lines)


def _format_prior_directions(priors: dict) -> str:
    """多空两侧的聚合读数 + 覆盖率；某侧无贡献者即如实写「无证据」（**不写 p=0.5**）。"""
    coverage = priors.get("coverage") or {}
    cov = (f"；覆盖率 {coverage.get('boardWithPrior')}/{coverage.get('boardTotal')}"
           if coverage.get("boardTotal") else "")
    debias = bool(priors.get("directionDebias"))
    return (f"做多 {_format_prior_side(priors.get('long'), debias)}，"
            f"做空 {_format_prior_side(priors.get('short'), debias)}{cov}。")


def _format_prior_side(side: dict | None, debias: bool = False) -> str:
    """某一侧的先验读数；该侧无贡献者即如实写「无证据」（**不写 p=0.5**）。

    directional-symmetry E-1（`debias=True` ⇒ 总闸开）：措辞追加一句「样本不足 ≠ 该方向胜率低」。
    合入前的裸「无证据」与「p=0.42」在模型看来是可比的两个读数，于是「做空侧没有样本」被当成
    「做空侧证据不利」——那正是那个自我强化吸收态的第二条腿。总闸关 ⇒ 逐字节回退。
    """
    if not side:
        return "无证据（样本不足，不是该方向胜率低的证据）" if debias else "无证据"
    return f"p={side.get('p')}（{side.get('contributorN')} 条证据，IR 加权，EV {side.get('evBps')}bps）"


def _format_prior_contributors(priors: dict) -> str:
    """本轮触发证据明细（两侧合并，至多 `top_n` 条已在聚合层截好）。"""
    items = []
    for side in ("long", "short"):
        for c in (priors.get(side) or {}).get("contributors") or []:
            items.append(f"{c.get('label')}（{'多' if side == 'long' else '空'}，"
                         f"p={c.get('p')}，样本 {c.get('sampleN')}，IR {c.get('ir')}）")
    return ("本轮触发证据：" + "｜".join(items)) if items else ""


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


def _format_multi_timeframe(context: dict | None) -> list[str]:
    """中线背景段（渲染主体在 `market_text`，见该模块 docstring 的行数逃生口说明）。"""
    from .market_text import _format_multi_timeframe as render   # 延迟导入，同 _format_base_rules
    return render(context)


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


def _format_wake(wake: dict | None) -> str:
    """唤醒原因后缀（wake-aware-context §2.8）：`payload["wake"]` **键缺失** ⇒ 空串。

    调用方在空串时回退既有 `_trigger_suffix` ⇒ 回测与老 payload 的首行逐字节不变。渲染委托
    `wake_text` 模块——判定、守卫、文案共用同一个 `wake` 对象，杜绝「prompt 说没变、守卫认为变了」。
    """
    if not wake:
        return ""
    from .wake_text import format_wake_for_prompt   # 延迟导入，与 _format_profit_guard 同范式
    return format_wake_for_prompt(wake)


def _format_tactical(payload: dict) -> list[str]:
    """战术周期段；`payload["tacticalContext"]` **键缺失** ⇒ 空表（整段省略，逐字节回退）。

    字符上限随 `tacticalContext.maxChars` 一同下发（装配期从 settings 取一次），故渲染层无需拿到
    `Settings`——这与本模块其余渲染函数「只吃 payload」的既有形态一致。
    """
    context = payload.get("tacticalContext")
    if not context:
        return []
    from .wake_text import format_tactical_for_prompt   # 延迟导入，同上
    return format_tactical_for_prompt(context, payload.get("tacticalTier") or "brief",
                                      max_chars=int(context.get("maxChars") or 0))


def _multi_timeframe_lines(payload: dict) -> list[str]:
    """中线背景段：重复轮压缩成表头 + 一句说明，其余情形逐字节沿用既有全量渲染。"""
    context = payload.get("multiTimeframeContext")
    if payload.get("contextDisclosure") == "repeat":
        from .wake_text import format_multi_timeframe_repeat   # 延迟导入，同上
        repeat = format_multi_timeframe_repeat(context)
        if repeat is not None:
            return repeat
    return _format_multi_timeframe(context)


def _digest_lines(payload: dict) -> list[str]:
    """执行周期证据段：重复轮压成一行，其余情形逐字节沿用既有三行渲染。"""
    digest = payload.get("marketDigest") or {}
    if payload.get("contextDisclosure") == "repeat":
        from .wake_text import format_digest_repeat   # 延迟导入，同上
        # 「本轮最新变化见战术周期」这句指路只在战术段真的会被渲染时才给：三个总闸互相独立，
        # 战术段可能因总闸关 / 无候选周期（1m 执行）/ 取数全失败而缺席。
        line = format_digest_repeat(payload.get("marketDigest"), _payload_execution_bar(payload),
                                    has_tactical=bool(payload.get("tacticalContext")))
        return [line] if line else []
    return [line for line in (_format_price_action(digest.get("priceAction")),
                              _format_volume(digest.get("volume")),
                              _format_factors(digest.get("factors"))) if line]


def _payload_execution_bar(payload: dict) -> str | None:
    """执行周期名（压缩文案的量纲标注用）：从战术段 / 唤醒段回读，两者都缺席时 None（不假数据）。"""
    for key in ("tacticalContext", "wake"):
        bar = (payload.get(key) or {}).get("executionBar")
        if bar:
            return bar
    return None


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
    from .profit_guard_text import format_profit_guard_for_prompt   # 延迟导入，同 _format_base_rules
    return format_profit_guard_for_prompt(snapshot)


def _format_cost(view: dict | None) -> str:
    """每轮交易成本段；`payload["costAwareness"]` **键缺失** ⇒ 空串 ⇒ 整段省略（逐字节回退）。

    渲染委托 `cost_text` 纯核心——写给模型看的 bps 与门禁真正据以判定的 bps 必须是同一个数（A-6）。

    资金费**已计提**一句（horizon-acceptance §2.2.5）刻意渲染在 `acceptance_text` 而**不是**
    `cost_text`：本轮的 G-6 冻结门禁要求 `cost_model` / `cost_text` 逐字节零 diff（那是「结算周期
    未知不得渗进成本估算层」这条不变式的机械判据）。`fundingAccrued` 键缺席 ⇒ 该句为空串 ⇒ 整段
    与本轮之前逐字节相同（见 spec 《实施过程发现的方案缺陷》I-1）。
    """
    if not view:
        return ""
    from .acceptance_text import format_funding_accrued_for_prompt
    from .cost_text import format_cost_for_prompt   # 延迟导入，同 _format_base_rules
    accrued = format_funding_accrued_for_prompt(view.get("fundingAccrued"))
    base = format_cost_for_prompt(view)
    return f"{base}\n{accrued}" if accrued else base


def _format_profit_cooldown(cooldown: dict | None) -> str:
    """保护性平仓冷却段（ai-trade-profit-protection-adaptive §2.2.3）。

    `payload["profitGuardCooldown"]` **键缺失 / None**（未装配 / 冷却已过期）⇒ 返回空串 ⇒ 整段省略
    ⇒ 逐字节回退。渲染委托 `profit_cooldown` 纯核心（门禁与文案同一真源，避免「说在拦其实没拦」）。
    """
    if not cooldown:
        return ""
    from .profit_cooldown import format_profit_cooldown   # 延迟导入，与 _format_profit_guard 同范式
    return format_profit_cooldown(cooldown)


def _format_sizing(plan: dict | None) -> str:
    """仓位约束段（vol-sizing-and-drawdown-brake §3.8）。

    `payload["sizingPlan"]` **键缺失 / None**（三条规则全未装配 / 回测 payload 无此键）⇒ 返回空串 ⇒
    整段省略 ⇒ 逐字节回退。渲染委托 `sizing_text` 纯文案模块（**与判定同源**：UI 与 prompt 说的上限
    必须就是系统真正执行的那个数，否则模型会把一个被静默改写的仓位写进自己的摘要）。
    """
    if not plan:
        return ""
    from .sizing_text import format_sizing_for_prompt   # 延迟导入，与 _format_profit_cooldown 同范式
    return format_sizing_for_prompt(plan)


def _format_horizon(wire: dict | None) -> str:
    """交易周期模式的每轮只读上下文段（ai-trade-holding-horizon-modes §2.9）。

    `payload["tradingHorizon"]` **键缺失**（契约未装配 / 老回测 payload 不带）⇒ 返回空串 ⇒ 整段省略
    ⇒ 既有决策 prompt 冻结断言逐字节不变。渲染委托 `hold_horizon_text` 纯核心——门禁、轨迹、prompt
    三者共用同一份快照，绝不在此重算任何一个数。
    """
    if not wire:
        return ""
    from .hold_horizon_text import format_horizon_for_prompt   # 延迟导入，同 _format_base_rules

    return format_horizon_for_prompt(wire)


def _format_profit_hold_note(payload: dict) -> str:
    """盈利持仓自察段（§2.6 c / F3）：`profitHoldRounds` 键缺失即整段省略（会话侧按阈值决定注入）。"""
    rounds = payload.get("profitHoldRounds")
    if not rounds:
        return ""
    from .profit_guard_text import format_profit_hold_note
    return format_profit_hold_note(payload.get("profitGuard"), rounds)


def _format_track_record(wire: dict | None) -> str:
    """实测战绩段；键缺失 ⇒ 空串 ⇒ 整段省略。全文在 `track_record`，本函数不做任何算术。"""
    if not wire:
        return ""
    from .track_record import format_track_record_text   # 延迟导入，同 _format_base_rules
    return format_track_record_text(wire)


def _format_experience(wire: dict | None) -> str:
    """交易经验段（L0 + L1，按档位）；键缺失 / 零条目 ⇒ 空串 ⇒ 整段省略。

    全文渲染在 `experience/disclosure.py`（沿用「长文本渲染下沉、`prompts.py` 只留 3 行」的既有
    纪律）。**档位判定的结果住在 wire 里，绝不在这里现场重算**：`commit_round` 会把 prompt
    再渲染一次塞进 `_context`，两次渲染之间任何状态漂移都会让上下文里的那一份与模型真正看到的
    那一份不一致。
    """
    if not wire:
        return ""
    from .experience.disclosure import format_experience   # 延迟导入，同 _format_base_rules

    return format_experience(wire)


def _format_experience_counter(wire: dict | None) -> str:
    """反思轮的 L2 反例段（与提议动作最相冲突的一条）；键缺失 ⇒ 空串 ⇒ 整段省略。"""
    if not wire:
        return ""
    return str(wire.get("rendered") or "")


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
        # 唤醒原因优先（七态结构化措辞）；`wake` 键缺席时逐字节回退既有二值触发源标注。
        + (_format_wake(payload.get("wake")) or _trigger_suffix(payload.get("trigger"))),
        _format_position(payload.get("position")),
    ]
    guard_line = _format_profit_guard(payload.get("profitGuard"))
    if guard_line:                           # 键缺失时整段省略（零回归；未装配的会话无此键）
        parts.append(guard_line)
    cooldown_line = _format_profit_cooldown(payload.get("profitGuardCooldown"))
    if cooldown_line:                        # 冷却未激活 ⇒ 整段省略（不写「当前无冷却」的噪音）
        parts.append(cooldown_line)
    sizing_line = _format_sizing(payload.get("sizingPlan"))
    if sizing_line:                          # 键缺失 ⇒ 整段省略（规则未装配 / 回测 payload 无此键）
        parts.append(sizing_line)
    cost_line = _format_cost(payload.get("costAwareness"))
    if cost_line:                            # 成本**先于**盈亏：模型要先知道成本才能正确解读那个数字
        parts.append(cost_line)
    horizon_line = _format_horizon(payload.get("tradingHorizon"))
    if horizon_line:                         # 键缺失 ⇒ 整段省略（契约未装配 / 老回测 payload）
        # 紧随成本之后、与系统 prompt 的五段宪章同序：换手迟滞是一个「浮盈够不够盖住成本」的判据，
        # 把它放在成本读数旁边，模型才不必跨段回忆那个 bps。
        parts.append(horizon_line)
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
    parts.extend(_multi_timeframe_lines(payload))
    market_line = _format_market_context(payload.get("marketContext"))
    if market_line:                          # E1：mc 为 None 时整段省略（不出现半截空信息）
        parts.append(market_line)
    consensus_line = _format_consensus(payload.get("consensus"))
    if consensus_line:                       # E2：consensus 为 None 时省略；空板则如实呈现 0 票
        parts.append(consensus_line)
    priors_line = _format_signal_priors(payload.get("signalPriors"))
    if priors_line:                          # 证据相邻性：共识 → 共识的历史质量 → 行情证据
        parts.append(priors_line)
    track_line = _format_track_record(payload.get("trackRecord"))
    if track_line:                           # 键缺失 ⇒ 整段省略（四条装配门禁任一不过 / 回测 payload）
        parts.append(track_line)
    experience_line = _format_experience(payload.get("experience"))
    if experience_line:                      # 键缺失 / 零条目 ⇒ 整段省略（不变式 I-2 的零回归路径）
        # 位置：紧随实测战绩之后、证据摘要之前——「本账户的历史成绩」与「从历史里提炼的教训」
        # 是同一类证据的两个粒度，放在一起模型才不必跨段回忆。
        parts.append(experience_line)
    parts.extend(_digest_lines(payload))     # R1：各段 None 时整段省略（同 E1/E2 降级范式）
    # 战术周期段（时间尺度由远及近单调收缩）：中线背景 → 体制 → 共识 → 先验 → 执行周期证据 →
    # **战术周期** → 基础策略铁律 → 信号板 → 摘要，与系统 prompt 的思维链步骤同序。
    parts.extend(_format_tactical(payload))
    base_rules_line = _format_base_rules(payload.get("baseRules"))
    if base_rules_line:                      # 键缺失时整段省略（零回归；规则未装配 / 回测老 payload）
        parts.append(base_rules_line)
    parts += [
        "策略信号板（仅供参考的触发线索，请逐条批判研判，可推翻）：",
        _format_board(payload.get("signalBoard") or []),
    ]
    if summary:
        parts.append(f"\n【此前执行摘要（你的长期记忆）】\n{summary}")
    # deep-reasoning-chain §3.1.6：收尾任务指令按 `reasoningDepth`（**加法键**）分叉。
    # 键缺席（回测老 payload / off 档 / 全部既有用例）⇒ 中文原句逐字节回退（G-1 冻结）。
    parts.append(
        DECISION_TASK_INSTRUCTION_EN if payload.get("reasoningDepth") else
        (f"\n请先按上述纪律逐步研判（思维链），再必须且只能调用一次 {DECISION_TOOL_NAME} "
         "提交本轮决策——不得只输出研判文字。"))
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


def build_reflection_system_prompt(horizon_text: str = "", reasoning_text: str = "") -> str:
    """反思复核官系统 prompt：分级复核标准（有名有姓才否决，收缩优先），只可收紧，收尾必调反思工具。

    hold-inertia-fix F1：删除「任何不放心即 abort（宁可错过不可做错）」的不对称保守标准——它把 abort
    设成默认答案（历史样本 7/7 全否决），是「永不开单」因果链的根因 ①。替换为分级复核标准（默认
    confirm / 收缩优先 / abort 须指名实质性缺陷 / 机会成本对称）；确定性安全层（apply_reflection、
    只能收紧、fail-closed）逐字不变。

    `horizon_text`（默认 `""` → 逐字节回退，holding-horizon §2.9 c）：持仓周期模式的补充段，**追加
    而非替换**既有的「`close` 是风险收敛动作」一句——那句仍然成立，本段补的是它缺失的对称性：
    「过早的 `close`」与「该平不平」是两种同样真实的错误。

    `reasoning_text`（末位，默认 `""` → 逐字节回退，deep-reasoning-chain §3.2）：《对抗式复核
    协议》R1–R7 正文。非空 ⇒ 整体换英文复核官脊柱；安全语义（只能收紧 / abort 须指名实质性
    缺陷 / 机会成本对称 / fail-closed）**一个字节都不改**，改的只有「怎么想」。

    ⚠️ 本函数全仓**唯一**的调用点是 `decider._stream_reflection`（每轮现造 `system`）。
    manager 与 backtest **从未**引用过它——在那里注入 `reasoning_text` 是一条不报错、不判红、
    轴 B 整条死掉的死路（spec §3.2 / P0-5，装配判据见用例 S-7 / S-9）。
    """
    if reasoning_text:
        return render_review_spine_en(reasoning_text, horizon_text)
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
        f"{horizon_text}"
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
    # L2 反例段（经验层 W-12）：**必须自带足够的自解释**——当轮档位可能是 `repeat`，复核官看到的
    # L0 只是一行压缩形态，任何「见上文第 N 条」的引用都会指向已被压缩掉的内容（§4.8）。
    counter = _format_experience_counter(payload.get("experienceCounter"))
    counter_block = f"{counter}\n\n" if counter else ""
    return (
        "【以下是原交易员看到的本轮全部证据（含其收尾调用 submit_trade_decision 的任务指令）——仅供你复核"
        "时参考其判断依据；那条决策指令**是给原交易员的、与你无关**，你**不要**调用 submit_trade_decision】\n"
        f"{evidence}\n\n"
        "————\n"
        f"{counter_block}{_format_reflection_decision(decision)}\n\n"
        "请以资深风控/交易员的批判视角复核上述交易决策是否稳健（方向、与高周期背景是否冲突、当前持仓与"
        "会话盈亏下风险敞口是否可接受、仓位是否偏重、有无证据不足强行交易）。提议决策与证据大体一致即 "
        "`confirm`；对仓位偏重有疑虑时**优先**以 `confirm` + `adjustedSizeFraction` 收缩；仅当能指名一条"
        "实质性缺陷（方向与关键证据矛盾 / 与高周期强冲突且无对冲理由 / 敞口不可接受 / 明显追高杀跌频繁"
        "反手 / 证据严重不足却重仓）时才 `abort`，`reason` 须引用具体证据。错过一笔证据充分的交易与做错"
        "一笔同样是损失；只能收紧、不得放大或改向。"
        f"你的**唯一任务**是必须且只能调用一次 {REFLECTION_TOOL_NAME} 提交裁决——不得只输出复核文字，"
        "也不得调用其它工具。"
        # 同款按 `reasoningDepth` 分叉的英文收尾指令（键缺席 ⇒ 整段不追加，逐字节回退）。
        + (REFLECTION_TASK_INSTRUCTION_EN if payload.get("reasoningDepth") else ""))


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
