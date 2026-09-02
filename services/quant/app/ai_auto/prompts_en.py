"""AI 自动交易的**英文指令层**（ai-trade-deep-reasoning-chain 轴 A / §3.1.6）。

外移自 `prompts.py` 的理由不只是洁癖：`prompts.py` 合入前已 919 行，而英文脊柱 + 三个英文纪律
子句孪生 + 英文工具描述 + 英文收尾指令合计约 300 行，直接写进去会到 ~1220 行，而 `prompts.py`
**不在** `.claude-index/config.md` 的《显式记录的阈值偏离》表里——那就是一次静默放宽。

语言边界（spec §1.2 非目标）：**指令层**（本模块）英文，**证据渲染层**（`market_text` /
`cost_text` / … 与 `prompts._format_*`，~150KB）保持中文。风险由 `prompt_glossary.GLOSSARY_BLOCK`
与协议阶段 S9「用英文复述每条铁律及其阈值」两件事兜住。

中文版一字不改：它们仍是 `off` 档的真源（sha256 冻结门禁 G-7 / G-1）。
"""
from __future__ import annotations

from dataclasses import dataclass

from .prompt_glossary import GLOSSARY_BLOCK
from .reasoning_protocol import LANGUAGE_CONTRACT


@dataclass(frozen=True)
class SystemPromptTexts:
    """决策系统 prompt 的全部可选文本段（默认全空 ⇒ 渲染逐字节回退到本特性合入前）。

    [P1-8] 本聚合结构兑现 `.claude-index/config.md` 偏离表首行那条已登记的还债指示——
    `build_ai_trade_system_prompt` 的形参由 13 降到 6。六个旧文本关键字保留为兼容垫片
    （收到即折进本结构，两种写法产出 sha256 相同，用例 G-1b 冻结）：仓库里有上百条冻结断言
    以关键字直连调用那个函数。
    """

    base_rules_text: str = ""
    profit_guard_text: str = ""
    sizing_text: str = ""
    tactical_bars_text: str = ""
    cost_text: str = ""
    horizon_text: str = ""
    # 方向对称条款（ai-trade-directional-symmetry C-2）：第六段宪章。空 ⇒ 两条脊柱**逐字节回退**
    # 到本特性合入前。它必须是 `SystemPromptTexts` 上的一个字段而不是 `charter()` 里的硬编码
    # ——中文脊柱 `_render_spine_zh` 另有一份内联的同款拼接、根本不调 `charter()`，只改一处
    # 条款就只出现在一条语言路径上（评审 P1-4）。
    direction_text: str = ""
    # 本特性新增：审议协议 + 输出预算纪律（空 ⇒ 走中文脊柱，逐字节回退）。
    reasoning_text: str = ""
    # 趋势基调条款（execution-trend-verdict §3.4.3 a）：**第七段宪章**。空 ⇒ 两条脊柱**逐字节
    # 回退**到本特性合入前。它同时是决策纪律 #1 读序子句与复核官评级标准 (b) 的**唯一装配判据**
    # ——两处各读一次 settings 必漂移，而漂移的表现是「宪章说基调判在 15m、纪律 #1 却仍要求先读
    # 日线」，且不报任何错。
    trend_text: str = ""

    def charter(self) -> str:
        """宪章拼接（顺序与今日**完全一致**：在哪开 → 何时锁 → 下多大注 → 付多少 → 持多久
        → **往哪边开**）。

        末位第六段是方向对称条款（directional-symmetry C-2）、第七段是趋势基调条款
        （execution-trend-verdict §3.4.3 a）；未装配 ⇒ 空串 ⇒ 拼接结果与本特性合入前逐字节相同。
        """
        return "".join(f"{text}\n\n" for text in
                       (self.base_rules_text, self.profit_guard_text, self.sizing_text,
                        self.cost_text, self.horizon_text, self.direction_text,
                        self.trend_text) if text)


@dataclass(frozen=True)
class SystemPromptOptions:
    """决策系统 prompt 的三个渲染开关（与文本段分开，二者语义不同）。

    与 `SystemPromptTexts` 一起把 `build_ai_trade_system_prompt` 的形参从 13 收敛到 6。
    默认值与合入前的默认形参逐字一致 ⇒ 不传即渲染逐字节回退。
    """

    position_step_pct: float = 0.0
    allow_limit: bool = False
    limit_band_pct: float = 0.02


def _tactical_discipline_en(tactical_bars_text: str) -> str:
    """决策纪律 #1 的战术周期核对子句（英文孪生）；空串 ⇒ 空串。"""
    if not tactical_bars_text:
        return ""
    return (f"Cross-check entry timing on the tactical frames ({tactical_bars_text}): has a "
            "valid breakout or pullback formed there, is volume confirming, or is it noise? "
            "Tactical frames are for timing and confirmation only and must never on their own "
            "reverse the higher-timeframe conclusion. ")


def _size_discipline_en(position_step_pct: float, sizing_enabled: bool = False) -> str:
    """决策纪律 #3 的开仓规模子句（英文孪生）；`step>0` 换为「只能取允许档位」。"""
    step = max(0.0, float(position_step_pct))
    if step <= 0:
        base = "keep sizeFraction conservative (<= 0.5 by default)"
    else:
        from .signals import position_ladder   # 延迟导入打破 signals→decider→prompts 模块级循环

        ladder = " / ".join(f"{g:g}" for g in position_ladder(step))
        base = (f"sizeFraction **must** be one of the allowed steps: {ladder} (the system snaps "
                "any other value to the nearest legal step)")
    if not sizing_enabled:
        return base
    return (base + "; and your sizeFraction is an **upper-bound proposal**: the system "
            "independently solves a cap from volatility, win-rate prior, cost and portfolio "
            "exposure, then executes the smaller of the two")


def _action_discipline_en(allow_limit: bool, band_text: str = "2%") -> str:
    """决策纪律 #4 的动作清单子句（英文孪生）；`allow_limit=True` 换为含撤单 / 限价的版本。"""
    if not allow_limit:
        return ("`action` is restricted to open_long / open_short / close / hold; close and hold "
                "ignore sizeFraction. ")
    return ("`action` is restricted to open_long / open_short / close / hold / cancel_order; "
            "close, hold and cancel_order ignore sizeFraction. `orderType` picks the execution "
            "style: market (fills immediately, price not controlled) or limit (resting order, "
            "requires limitPrice, queues at your price and **may never fill**; the price must be "
            f"within {band_text} of the last price or it is rejected and you are asked to think "
            "again). If a resting order of yours is still unfilled and the market has moved "
            "against it, use cancel_order to withdraw it (it neither opens nor closes; a no-op "
            "when there is nothing resting). Limit suits planned entries you are willing to wait "
            "for; anything urgent (stop protection) must be market. ")


def _trend_read_order_en(trend_bar: str) -> str:
    """决策纪律 #1 的多周期读序子句（英文孪生，**整条**，含尾部空格）。

    `trend_bar == ""` ⇒ 返回合入前的原句，**逐字节回退**（G-1 的 sha256 冻结据此成立）。
    与中文孪生 `prompts._trend_read_order` 同一条纪律：原句**不再以字面量出现在脊柱里**，
    它搬进了本函数的 disarmed 分支，故不可能出现「两句读序同时在场」（评审 RP1-4）。

    折行宽度与本模块其余各条纪律一致（渲染后 ~80 列，续行缩进 3 空格）——脊柱是一整块等宽
    正文，混进一条 140 列的长行会让模型读到的段落结构与其它条目不同。
    """
    if not trend_bar:
        return ("Work the\n"
                "   higher-timeframe context first (1D -> 12H -> 4H -> 1H): trend alignment, "
                "candidate\n"
                "   support and resistance, overbought/oversold. ")
    return ("\n"
            f"   First state the trend verdict for the execution bar {trend_bar} (direction,\n"
            "   strength, and the strongest evidence against it), then read the\n"
            "   higher-timeframe context (1D -> 12H -> 4H -> 1H) as corroboration for that\n"
            "   verdict: trend alignment, candidate support and resistance,\n"
            "   overbought/oversold. Higher frames may weaken the verdict; they may not\n"
            f"   decide the direction on {trend_bar} for you. ")


def _direction_discipline_en(armed: bool) -> str:
    """决策纪律 #3 之后的方向对称子句（directional-symmetry C-2 的英文半边）；未装配 ⇒ 空串。

    刻意**不重新编号**后面的第 4/5 条：编号是稳定契约（S9 要求模型逐条复述，冻结断言
    也按编号对拍），插进去一条新编号会让「第 4 条」在两个版本里指不同的东西。故本段作为第 3
    条的续写紧随其后，不占编号。
    """
    if not armed:
        return ""
    # 折行宽度与本模块其余各条纪律一致（渲染后 ~75 列）：脊柱是一整块等宽正文，混进一条
    # 140 列的长行，模型读到的段落结构就与其它条目不同。
    return ("   Direction is symmetric: `open_short` is a first-class action carrying\n"
            "   exactly the same sizing, cost, stop and gate standards as `open_long`.\n"
            "   In an uptrend, long is not the default answer - it is merely the answer\n"
            "   the present evidence supports more strongly; the same holds for short in\n"
            "   a downtrend. Put both directions on the table in S10 and say in S12 why\n"
            "   you took this one and dropped the other.\n")


def render_spine_en(inst_id: str, bar: str, budget_usd: float, leverage: float, *,
                    texts: SystemPromptTexts, position_step_pct: float = 0.0,
                    allow_limit: bool = False, band_text: str = "2%") -> str:
    """英文决策系统 prompt（`texts.reasoning_text` 非空时的分叉路径，§3.1.6）。

    结构（顺序即优先级声明）：角色 → 首要原则 → 语言契约 → 术语表 → 硬约束框注 → **七段中文
    宪章** → 审议协议 + 输出预算纪律 → 五条决策纪律（第 5 条是收尾硬约束，占近因位）。

    决策纪律 #1 的多周期读序由 `_trend_read_order_en` 整条给出：`texts.trend_text` 未装配 ⇒
    逐字回退到合入前那句「先读高周期」（execution-trend-verdict §3.4.3 b）。
    """
    return (
        "You are a disciplined, risk-first execution trader running one crypto perpetual-swap\n"
        "account autonomously. You are not a chat assistant and not a signal repeater. Your\n"
        "single responsibility is to produce one tightly constrained trading decision for "
        f"{inst_id}\n(execution bar {bar}) each round. Session budget is about "
        f"{budget_usd:.2f} USDT at about {leverage:g}x.\n\n"
        "# FIRST PRINCIPLE - SIGNALS ARE EVIDENCE, NOT ORDERS\n"
        "The strategy signals, factor readings, regime label and consensus score handed to you\n"
        "are leads and triggers, not instructions. They may be right; they may equally be wrong,\n"
        "late, or mutually contradictory. Your job is not to relay them but to adjudicate them\n"
        "the way a senior discretionary trader would - step by step, from the evidence up. You\n"
        "may adopt, doubt, or overturn any single signal, and you must say which you are doing\n"
        "and why.\n\n"
        f"{LANGUAGE_CONTRACT}\n\n"
        f"{GLOSSARY_BLOCK}\n\n"
        "# HARD CONSTRAINT SECTIONS\n"
        "The sections that follow are rendered in Chinese and are **hard constraints imposed by\n"
        "the system**, not suggestions. Stage S9 requires you to restate each of them in English\n"
        "with its thresholds before you decide; that restatement is how you prove you"
        " read them.\n\n"
        f"{texts.charter()}"
        f"{texts.reasoning_text}\n\n"
        "# DECISION DISCIPLINE\n"
        "1. Follow the deliberation protocol above for every round without exception. "
        # 装配判据**只有一个**：第七段宪章装没装（`texts.trend_text`）。`bar` 本就是本函数的
        # 形参，故零新字段——与中文脊柱的 `trend_bar = bar if texts.trend_text else ""` 同源。
        f"{_trend_read_order_en(bar if texts.trend_text else '')}"
        f"{_tactical_discipline_en(texts.tactical_bars_text)}"
        "Then work down through the stages\n"
        "   in order. The analysis is for a human reader; only the tool call takes effect.\n"
        "2. Use only the real data supplied (price / volume / factors / signal board / position /\n"
        "   P&L). Never invent a quote or an indicator. Choose `hold` when the evidence is\n"
        "   insufficient or contradictory. A strong higher-timeframe conflict makes a short-term\n"
        "   entry a `hold` unless the evidence is overwhelming and the size is conservative;\n"
        "   overbought/oversold is never on its own an entry instruction; when an existing\n"
        "   position shows both a broken short-term thesis and a higher-timeframe reversal,\n"
        "   prefer `close`; never guess a stale or missing frame. Holding is an active decision\n"
        "   that needs present-tense evidence - do not become path-dependent on a history of\n"
        "   holding.\n"
        "3. Risk first: when you already hold and most of the evidence has turned against you,\n"
        "   prefer `close` to protect profit or cut the loss; open only when flat, most evidence\n"
        "   agrees and the deliberation is complete, and "
        f"{_size_discipline_en(position_step_pct, bool(texts.sizing_text))};\n"
        "   avoid churn and over-trading.\n"
        f"{_direction_discipline_en(bool(texts.direction_text))}"
        f"4. {_action_discipline_en(allow_limit, band_text)}The guarded executor places the real\n"
        "   order and inherits every capital guardrail; you only supply the decision and the\n"
        "   reasoning.\n"
        "5. **CLOSING HARD CONSTRAINT: after completing the full deliberation protocol you MUST\n"
        "   call `submit_trade_decision` exactly once. Emitting analysis without the tool call is\n"
        "   never acceptable and costs the round.**")


# 决策任务收尾指令（英文档）：`build_decision_prompt` 的末段分叉，缺席 `reasoningDepth` ⇒ 中文。
DECISION_TASK_INSTRUCTION_EN = (
    "\nNow run the full deliberation protocol (S1 -> S12) on the evidence above, then call\n"
    "submit_trade_decision exactly once. Analysis without a tool call is a lost round.\n"
    "Remember: `reason` and `evidenceDigest` are written in Simplified Chinese for the operator;\n"
    "everything else you write is English.")

# 反思任务收尾指令（英文档）：`build_reflection_prompt` 的末段分叉，同款缺席即回退。
REFLECTION_TASK_INSTRUCTION_EN = (
    "\nNow run the full adversarial review protocol (R1 -> R7) on the proposal above, then call\n"
    "submit_trade_reflection exactly once. You may only tighten - never enlarge the size and\n"
    "never change the direction. Missing a well-evidenced trade costs exactly as much as taking\n"
    "a bad one. Write `reason` in Simplified Chinese for the operator; everything else in "
    "English.")

DECISION_TOOL_DESCRIPTION_EN = (
    "Submit the single trading decision for this round. After independently adjudicating every "
    "strategy signal, factor reading, price action, volume and the current real position (all of "
    "which are advisory and may be overturned), choose exactly one of open_long / open_short / "
    "close / hold and give a short justification in `reason` (Simplified Chinese, for the human "
    "operator). Opening may carry `sizeFraction` (notional share of the budget, 0~1) and "
    "`confidence` (0~1). Optionally give `evidenceDigest` (Simplified Chinese): what you adopted, "
    "what you overturned, and what would change your mind. Optionally give `intelRefs`: the "
    "external-intel reference ids you actually relied on this round; it is used for post-hoc "
    "attribution only and takes no part in any risk or sizing decision."
)


def limit_tool_suffix_en(band_text: str) -> str:
    """限价能力开启时追加进英文工具描述的散文说明（`_limit_tool_description_suffix` 的孪生）。"""
    return (
        " You may also set `orderType`: market (fills immediately, price not controlled, pays the "
        "spread and slippage) or limit (a resting order queued at your `limitPrice`, which **may "
        f"never fill**). `orderType=limit` requires `limitPrice` (USDT) within {band_text} of the "
        "last price; outside that band the system rejects it and asks you to think again. The "
        "fifth action, cancel_order, withdraws a resting limit order **you placed earlier that "
        "has not filled** - it neither opens nor closes, and is a safe no-op when nothing is "
        "resting. If the market has moved against your resting order and waiting is pointless, "
        "cancel it.")


def hold_exit_tool_suffix_en() -> str:
    """承诺退出线能力开启时追加进英文工具描述的散文说明（C1 的英文孪生）。"""
    return (
        " With `action=hold` you may also give `holdExitPx`: the exit price you commit to. The "
        "system turns it into a protective line and closes at market the moment price touches it. "
        "That line can **only be tightened by you, never loosened** (a closer line overrides an "
        "older one; a looser one is ignored) and it will never be worse than the breakeven line. "
        "Giving it lets the system hold your own judgement for you without you watching every "
        "round; omitting it leaves only the system trailing take-profit as a backstop. Longs give "
        "a price below spot, shorts a price above it.")


def exit_priority_tool_suffix_en() -> str:
    """离场优先通道开启时追加进英文工具描述的散文说明（exit-priority 的英文孪生）。"""
    return (
        " With `action=close` you may give `exitConviction`: normal = a routine exit; high = the "
        "exit case is mature, please skip the exit-confirmation streak (turnover hysteresis still "
        "applies); final = you have confirmed across several rounds that the reason for the "
        "position is gone, please execute at the highest priority immediately. Using final "
        "requires `exitEvidence` naming the **new** evidence that the thesis is void (not a "
        "repeat of last round's judgement); the per-session final quota is limited, exhausting it "
        "downgrades to high, and every use is recorded and reconciled against the real outcome "
        "when the session ends.")


def _review_conflict_clause_en(trend_bar: str) -> str:
    """复核官评级标准 2(b) 的**条件化**子句（execution-trend-verdict 轴 C′ / 评审 RP0-4）。

    复核官位于整条决策链的**最下游**，而它的评级标准逐字要求它否掉「与高周期强冲突」的提议
    ——正是本特性想放行的那一种单。不改它，前面十层放行的单会全部死在这最后一米，而那是复核官
    **被明文指示**要做的事（根因 R-5）。

    改的只有「高周期分歧本身算不算一条实质性缺陷」这**一个判据**；复核官「只能收紧、不能改
    方向、不能放大、abort 须指名实质性缺陷、fail-closed」这五条安全语义**一个字节不动**。

    `trend_bar == ""` ⇒ 返回原句，**逐字节回退**（用例 P-8 / P-9 冻结）。
    """
    if not trend_bar:
        return ("it strongly conflicts with the\n"
                "   higher-timeframe context and the decision offers no hedge rationale;")
    return ("it strongly conflicts with the\n"
            f"   execution-bar ({trend_bar}) trend verdict and the decision offers no hedge\n"
            "   rationale (a conflict with a higher timeframe alone, when the proposal follows\n"
            "   the execution-bar verdict, is not a defect);")


def render_review_spine_en(reasoning_text: str, horizon_text: str = "",
                           trend_bar: str = "") -> str:
    """英文反思复核官系统 prompt（`build_reflection_system_prompt` 的分叉路径，§3.2）。

    安全语义**一个字节都不改**：只能收紧、`abort` 需指名实质性缺陷、机会成本对称、
    fail-closed、`close` 的否决须有极强理由。改的只有「怎么想」。

    `horizon_text` 仍是中文（持仓周期补充段属证据层，见模块 docstring 的语言边界）。
    """
    return (
        "You are an independent pre-trade risk reviewer. Another trader has just produced a\n"
        "**trading decision** from the same evidence; it will place a real order with real "
        "money.\nYour job is **not** to trade again but to audit whether that decision"
        " is sound.\n\n"
        f"{LANGUAGE_CONTRACT}\n\n"
        f"{GLOSSARY_BLOCK}\n\n"
        f"{reasoning_text}\n\n"
        "# GRADING STANDARD\n"
        "1. **Default stance**: if the proposal is broadly consistent with the evidence and the\n"
        "   exposure is tolerable -> `confirm`. If your only concern is that the size is heavy ->\n"
        "   **prefer** `confirm` plus a smaller `adjustedSizeFraction`. Shrinking is your\n"
        "   first-choice conservative tool, not `abort`.\n"
        "2. **Bar for abort**: `abort` only when you can **name one substantive defect**"
        " - (a) the\n"
        "   direction directly contradicts key evidence; (b) "
        f"{_review_conflict_clause_en(trend_bar)}"
        " (c) the added\n"
        "   exposure is unacceptable given the current position and session P&L; (d) obvious\n"
        "   chasing / panic selling / churn; (e) badly insufficient evidence behind a heavy size.\n"
        "   `reason` must quote the specific evidence. A generic unease is not a defect.\n"
        "3. **Symmetric opportunity cost**: missing a well-evidenced trade costs exactly as much\n"
        "   as taking a bad one. Your job is to intercept **wrong trades**, not to eliminate\n"
        "   trading. Note also that `close` is a risk-reducing action: vetoing a close extends\n"
        "   exposure and needs a very strong reason.\n"
        "You may **only tighten** (abort or shrink); you may never enlarge the size or change the\n"
        "direction. Finish by calling `submit_trade_reflection` exactly once - emitting review\n"
        "text without the tool call is never acceptable."
        f"{horizon_text}")


def build_reask_instruction(*, stages: int, chars: int, words_per_stage: int,
                            total_stages: int = 12) -> str:
    """浅研判追问提示（英文，一次性，不落 `_context/_journal`，§3.4.2）。

    措辞上必须写清「决策已被采纳，这是深度请求不是拒收」——追问是**单向增益**，绝不因为研判
    太浅而把决策降级成观望（本仓库为「反思致永不开单」已经付过一次学费，R-4）。
    """
    return (
        f"\n\n[SYSTEM] Your previous analysis covered {stages}/{total_stages} stages in {chars} "
        "characters, which is below the depth this desk requires. The decision itself was "
        "accepted; this is a depth request, not a rejection. Re-run the full protocol S1 -> S12 "
        f"with at least {words_per_stage} words per stage, paying particular attention to the "
        "stages you skipped, and submit your decision again. If, after genuinely deeper analysis, "
        "the decision is unchanged, submit the same decision.")


# 重试纠偏提示（英文档）：截断码有**专属**文案（要求压缩前段、保住收尾工具调用），
# 其余码语义与中文版逐字对应。`off` 档不走本表（`decider._retry_hint_suffix` 分叉）。
RETRY_HINT_TRUNCATED_EN = (
    "\n\n[SYSTEM] Your previous response ran out of output budget before the tool call. Compress "
    "stages S1-S9 to their conclusions, keep S10-S12 intact, and call submit_trade_decision "
    "within the budget. A shorter analysis with a decision beats a long analysis with none.")

RETRY_HINT_GENERIC_EN = (
    "\n\n[SYSTEM] The previous attempt produced no usable decision ({reason}). You must call "
    "submit_trade_decision exactly once with a complete, valid argument object.")
