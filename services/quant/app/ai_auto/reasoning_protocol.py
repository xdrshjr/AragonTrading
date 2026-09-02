"""AI 自动交易深度思维链协议（ai-trade-deep-reasoning-chain 轴 A / 轴 B）。

纯核心：不读 settings 之外的任何东西、不碰 IO、可离线单测。`resolve_depth_profile` 是
settings → profile 的**唯一**转换点，prompts / decider / session / backtest 一律只认
`DepthProfile`，杜绝「四处各读一次 settings」的漂移。

三条铁律（改本文件前先读）：
1. `off` 档是**运维回退开关**：它必须回退**全部**行为——prompt、预算、追问、超时、尝试次数
   上限、轮级墙钟闸。任何「只在 off 档回退 prompt」的写法都会在真正需要止血的那一刻失效
   （spec §5.3 / P0-4，用例 G-7b）。
2. 截断口径必须 **provider 无关**：`TRUNCATION_STOP_REASONS` 是唯一真源，新增 provider 必须
   在这里登记（spec §3.3.3 / P0-1，用例 D-1b）。
3. `is_probably_chinese` 是**纯呈现层**守卫：绝不产生 degrade、绝不改 `TradeDecision`、
   绝不触发追问（spec §3.1.7 / P1-7，用例 D-12）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, replace

# 阶段标记的**唯一真源**：模型被要求逐字输出 `## S1` … `## S12` / `## R1` … `## R7` 作为行首。
# [P2-1] 口径收紧为「行首顶格 `##` + 恰好一个空格 + `S|R` + 数字」。宽松写法
# （`^\s{0,3}#{2,3}\s*`）会把 `##S1`（无空格）与 `   ## S1`（缩进）也计为阶段——但这条标记有
# **三个**消费者，第三个是 `apps/web/lib/markdown.ts` 的 `HEADING_RE = /^(#{1,6})\s+(.*)$/`，
# 它要求行首顶格且至少一个空格。三者口径不一致时，`##S1` 会被前两者计为阶段、被渲染层当成
# 普通正文——表现是「导轨说有 12 段，展开后某一段的标题混在上一段正文里」。三处必须同口径。
STAGE_HEADING_RE = re.compile(r"^## (S|R)(\d{1,2})\b", re.MULTILINE)

DEPTHS = ("off", "standard", "deep", "exhaustive")

# 「输出预算耗尽」的 provider 归一口径（**唯一真源**；新增 provider 必须在这里登记）。
# anthropic: stop_reason == "max_tokens"   （agent/llm/anthropic_client.py 的 message_delta）
# openai:    finish_reason == "length"     （agent/llm/openai_client.py 把它塞进同一个字段）
# 写死任一家的口径 ⇒ 根因 C-4 在另外半个 provider 家族上原样存活，且**不报任何错**。
TRUNCATION_STOP_REASONS = frozenset({"max_tokens", "length"})
# 内容安全拦截：与「写太长」是两回事。重试同一份 prompt 只会再被拦一次、白烧一次预算，
# 故单列且**不可重试**（`decider._RETRYABLE_DEGRADE_REASONS` 刻意不含它）。
CONTENT_FILTER_STOP_REASONS = frozenset({"content_filter", "content_filtered"})

# 尾段摘录默认取的阶段（S12 = 校准结论）与字符上限；调用方可覆盖。
TAIL_STAGE = "S12"
_TAIL_EXCERPT_CHARS = 400
# `is_probably_chinese` 的 CJK 占比下限（呈现层回落判据，绝不参与交易语义）。
_CJK_FLOOR = 0.3
_CJK_RE = re.compile(r"[一-鿿]")
# 协议正文按本值渲染「留给收尾工具调用的输出 token」；生产调用点传 settings 的真值。
DEFAULT_RESERVE_TOKENS = 768


@dataclass(frozen=True)
class DepthProfile:
    """一档深度的全部派生量（单一真源：预算、篇幅、追问、超时、墙钟闸都从这里取）。"""

    name: str                     # off | standard | deep | exhaustive
    enabled: bool                 # False ⇒ 全链路逐字节回退（off 档）
    words_per_stage: int          # 每阶段最小篇幅（英文词），写进协议正文
    decision_max_tokens: int      # 决策调用的输出预算
    reflection_max_tokens: int    # 反思调用的输出预算
    min_analysis_chars: int       # 浅研判追问阈（英文字符）；0 ⇒ 不追问
    # ---- [P0-4] 超时与尝试次数随档位派生，**不再**改 config 的全局默认 ----
    decision_timeout_s: float     # 单次决策尝试超时；off 档 = 今日 60.0（逐字回退）
    reflection_timeout_s: float   # 单次反思尝试超时；off 档 = 今日 45.0（逐字回退）
    max_attempts_cap: int         # 单轮「首次 + 重试」的尝试次数上限；0 ⇒ 不封顶（off 档）
    round_deadline_s: float       # 单轮墙钟总闸（全部尝试 + 追问共享）；0 ⇒ 不设闸（off 档）


# 四档的全部派生量。`off` 一行的取值即「本特性合入前的形态」：60/45 是 `config.py` 今日的两个
# 超时默认值，`max_attempts_cap=0` = 不封顶（沿用 `ai_trade_decision_max_retries`），
# `round_deadline_s=0.0` = 不设墙钟闸。改这一行等于改回退开关的语义，请先读 §5.3。
_PROFILES: dict[str, DepthProfile] = {
    "off": DepthProfile("off", False, 0, 0, 0, 0, 60.0, 45.0, 0, 0.0),
    "standard": DepthProfile("standard", True, 60, 4096, 3072, 1500, 150.0, 100.0, 3, 420.0),
    "deep": DepthProfile("deep", True, 120, 8192, 5120, 3000, 210.0, 150.0, 2, 600.0),
    "exhaustive": DepthProfile("exhaustive", True, 200, 16384, 8192, 6000, 420.0, 240.0, 2,
                               1200.0),
}


@dataclass(frozen=True)
class ReasoningStage:
    """协议里的一个阶段：稳定 id（模型逐字输出）+ 英文码 + 英文短标题 + 英文 mandate。"""

    id: str        # S1 … S12 / R1 … R7
    code: str      # REGIME / EXECUTION / …（模型在标题行里复述，供前端做二次校验）
    title: str     # 英文短标题（只进协议正文；中文阶段名的唯一真源在前端，见 §3.5.1）
    mandate: str   # 英文祈使句：这一阶段必须回答什么


# —— 决策侧《审议协议》十二阶段（本表是产品文案，逐字采用，勿"顺手润色"）——
DECISION_STAGES: tuple[ReasoningStage, ...] = (
    ReasoningStage(
        "S1", "REGIME", "Higher-timeframe regime",
        "Read the higher-timeframe context strictly in the order 1D -> 12H -> 4H -> 1H. For "
        "each frame state: trend direction, structure (higher-highs / lower-lows / range), the "
        "nearest support and resistance with its distance in %, and whether RSI is at an "
        "extreme. End with one sentence naming the dominant regime and one sentence naming the "
        "single strongest piece of evidence **against** that regime. Mark any missing or stale "
        "frame explicitly; never infer a frame that was not given."),
    ReasoningStage(
        "S2", "EXECUTION", "Execution-bar structure",
        "Analyse the execution bar: direction, momentum, moving-average structure, position "
        "inside the recent range, and distance to the range edges. State whether the execution "
        "bar agrees or disagrees with S1, and quantify the disagreement."),
    ReasoningStage(
        "S3", "TACTICAL", "Tactical timing",
        "If tactical frames are supplied, judge **timing only**: has a valid breakout or "
        "pullback formed on this frame, is volume confirming, or is this noise? Tactical frames "
        "may confirm or delay an entry; they may **never** on their own reverse the S1 "
        "conclusion. If no tactical frame is supplied, say so in one line and move on."),
    ReasoningStage(
        "S4", "FLOW", "Volume and participation",
        "Volume and participation: current volume vs its mean, volume ratio, "
        "expansion/contraction trend, and any anomaly. Map each observation to a directional "
        "claim (\"rising volume into a breakout supports continuation\") rather than restating "
        "the number."),
    ReasoningStage(
        "S5", "FACTORS", "Factor readings",
        "Take every supplied factor reading (RSI / MACD / Bollinger / ATR / momentum ...) one "
        "at a time. For each: the value, what it claims directionally, and how much weight it "
        "deserves **in the current regime** (a mean-reversion factor in a strong trend deserves "
        "little). Do not list a factor you were not given."),
    ReasoningStage(
        "S6", "SIGNALS", "Signal adjudication",
        "Adjudicate the signal board **one strategy per paragraph**. For each strategy state: "
        "its current stance and intent, whether its precondition actually holds in the current "
        "regime, and your verdict - adopt / doubt / overturn - with the specific evidence that "
        "drives the verdict. A signal is a lead, never an order. Explicitly name at least one "
        "signal you are **not** following and why."),
    ReasoningStage(
        "S7", "CONFLICT", "Conflict and weighting",
        "Resolve the conflicts you have just surfaced. Assign each block of evidence (S1-S6) an "
        "explicit weight and justify the ranking. State the net directional lean and its "
        "strength on a 0-1 scale. If the evidence is genuinely balanced, say so - a forced lean "
        "is worse than an honest tie."),
    ReasoningStage(
        "S8", "PRIOR", "History and priors",
        "Bring in history: the consensus quality prior, the measured track record, and every "
        "injected experience lesson. For each lesson state whether the current situation matches "
        "its context; a lesson whose context does not match must be discarded **out loud**. Give "
        "the base rate you are implicitly assuming for the trade you are considering."),
    ReasoningStage(
        "S9", "BOOK", "Hard-constraint restatement",
        "Restate **in English**, one line each, every armed hard-constraint section that appears "
        "in your system prompt or in this round's evidence - base-strategy MA band, profit "
        "protection, position sizing and drawdown brake, trading cost, holding horizon, exit "
        "priority - together with its numeric thresholds as given. Then state, for the action "
        "you are leaning towards, whether it satisfies each of them. Also state the current real "
        "position, session P&L versus budget, remaining take-profit / stop-loss headroom, and "
        "the round-trip cost in bps that this action must overcome."),
    ReasoningStage(
        "S10", "HYPOTHESIS", "Candidates and expected value",
        "Put at least three candidate actions on the table (one of them must be `hold`). For "
        "each: entry logic, the price or condition that would prove it wrong, a realistic "
        "target, the approximate win probability, and the resulting expected value net of the "
        "cost from S9. Rank them."),
    ReasoningStage(
        "S11", "FALSIFY", "Pre-mortem and steel-man",
        "Pre-mortem. Assume your top candidate has already lost money. Write the most likely "
        "story of how that happened. Then steel-man the opposite side: the strongest possible "
        "argument for the reverse action, stated as its own advocate would state it. List the "
        "three specific observations that would force you to abandon your candidate. If S11 "
        "changes your mind, say so and go back to S10 - that is the point of this stage."),
    ReasoningStage(
        "S12", "VERDICT", "Calibrated verdict",
        "Conclude. Name the chosen action, the size fraction with its justification, and a "
        "calibrated confidence (state what you would need to see to move that confidence up or "
        "down by 0.2). Name the single observation you will look for next round. Then, and only "
        "then, call the decision tool."),
)

# —— 反思侧《对抗式复核协议》七阶段。安全语义一个字节都不改，改的只有「怎么想」——
REVIEW_STAGES: tuple[ReasoningStage, ...] = (
    ReasoningStage(
        "R1", "RESTATE", "Restate the proposal",
        "Restate the proposed decision and the trader's central claim in one sentence each, in "
        "your own words. If you cannot restate the claim, the reasoning was too vague - note "
        "that."),
    ReasoningStage(
        "R2", "AUDIT", "Audit the citations",
        "Audit every piece of evidence the trader cited **against the input block above**. For "
        "each citation: does the input actually say that? Is the number right? Is the frame the "
        "trader used the frame that was supplied? A miscited number is a named, concrete "
        "defect."),
    ReasoningStage(
        "R3", "OMISSION", "Hunt for omissions",
        "Hunt for what the trader did not mention. Scan the evidence for the strongest item "
        "pointing the other way and state it plainly. Silence about a strong counter-signal is "
        "itself a finding."),
    ReasoningStage(
        "R4", "CONFLICT", "Regime and hard-constraint conflict",
        "Check the proposal against the higher-timeframe regime and against every armed "
        "hard-constraint section, restating each threshold in English. Name any conflict and "
        "whether the trader offered a hedge rationale for it."),
    ReasoningStage(
        "R5", "EXPOSURE", "Exposure and cost",
        "Given the current real position, the session P&L against budget, the round-trip cost in "
        "bps, the holding-horizon state and the correlated exposure already on the book, is this "
        "incremental exposure acceptable? Is the size heavier than the conviction justifies? "
        "Quantify."),
    ReasoningStage(
        "R6", "PREMORTEM", "Pre-mortem",
        "Assume this order is filled and loses money. Write the most likely path to that loss in "
        "three or four sentences. Then answer one question explicitly: is that path acceptable "
        "given the stop and the budget? An acceptable loss path is a reason to confirm, not to "
        "abort."),
    ReasoningStage(
        "R7", "VERDICT", "Graded verdict",
        "Grade it. `confirm` if the proposal is broadly consistent with the evidence and the "
        "exposure is tolerable. `confirm` with a smaller `adjustedSizeFraction` if your only "
        "concern is size - shrinking is your first-choice conservative tool. `abort` **only** "
        "when you can name one substantive defect from R2-R5, quoted concretely. A generic "
        "unease is not a defect. Missing a well-evidenced trade costs exactly as much as taking "
        "a bad one. You may only tighten. Then call the reflection tool exactly once."),
)

# 语言契约（逐字采用）：用英文思考、用中文向操作员汇报，例外字段只有三个。
LANGUAGE_CONTRACT = """# LANGUAGE CONTRACT
Conduct 100% of your reasoning in English. Every stage of the deliberation protocol, every
intermediate note, and every internal argument must be written in English.

Some input blocks below are rendered with Simplified-Chinese labels (the numbers, symbols
and enum values inside them are language-neutral). A glossary is provided. Reading Chinese
labels is expected; writing Chinese reasoning is not.

Three fields are the exception, because a human operator reads them on a Chinese-language
console. Write these in Simplified Chinese, and only these:
  - `reason`          - the operator-facing justification of the decision.
  - `evidenceDigest`  - the operator-facing digest of what you adopted and what you overturned.
  - `exitEvidence`    - the named new evidence for a high-conviction exit.
Everything else you write is English.

One further optional field is language-neutral and may be written whenever the intel section
carries `[Ixx]` markers:
  - `intelRefs`       - the ids of the external-intel lines you actually relied on. It is a
                        post-hoc attribution record only; it changes no risk or sizing decision,
                        so leaving it empty when you relied on none costs you nothing."""

# —— 方向对称化的三处 mandate 追加（ai-trade-directional-symmetry 轴 C · C-1）——
# **只追加、不改写**既有正文：S7 / S10 / S11 的原句一个字节都不动，故总闸关时
# `_render_stages` 的产物与本特性合入前逐字相同（用例 C-4 冻结这一点）。
#
# 用户诉求的原话是「研判交易决策和行动的时候，要考虑方向性，不能无脑开多」。它在协议正文里
# 此前**没有任何落点**：S10 只要求「三个候选，其中一个必须是 hold」——`open_long 25% /
# open_long 50% / hold` 完全合规；S11 的 "the reverse action" 在空仓状态下可以被合理地读成
# `hold` 而不是「反方向开仓」。
_SYMMETRY_MANDATES: dict[str, str] = {
    "S7": (" State also the strongest case for the **opposite** direction, phrased as its own "
           "advocate would phrase it. If you cannot state the opposite case, you have not "
           "finished this stage."),
    "S10": (" The candidate set **must** contain at least one long candidate, at least one short "
            "candidate, and `hold` - listing candidates in only one direction means S10 is not "
            "done. Short and long are first-class, perfectly symmetric actions on this account: "
            "they share the same sizing, cost, stop and gate standards."),
    "S11": (" When you are flat, `the reverse action` means **opening in the opposite "
            "direction**, not holding."),
}

_REVIEW_SYMMETRY_MANDATES: dict[str, str] = {
    "R7": (" Apply exactly the same standard of evidence to a short proposal as you would to a "
           "long one; a proposal is never weaker merely because it is a short."),
}


def _with_symmetry(stages: tuple[ReasoningStage, ...],
                   mandates: dict[str, str]) -> tuple[ReasoningStage, ...]:
    """把方向对称条款追加进指定阶段的 mandate 末尾；未列出的阶段**原对象直通**。"""
    return tuple(replace(stage, mandate=stage.mandate + mandates[stage.id])
                 if stage.id in mandates else stage for stage in stages)


# —— 执行周期趋势基调的三处 mandate（ai-trade-execution-trend-verdict 轴 C）——
#
# 根因 R-1：协议的第一步锚在高周期，执行周期被降格为「附议」。S1 逐字要求「按 1D -> 12H ->
# 4H -> 1H 读高周期并给出 dominant regime」，S2 的原文是「说明执行周期与 S1 是否一致，并量化
# 分歧」，S3 又明令战术周期**绝不能**单独反转 S1。三句连起来的语义是：**基调由高周期产生，
# 执行周期只能附议或提异议**。而用户的诉求是基调由**他所选的执行周期**产生。
#
# S1 是**整体替换**（题面 + mandate），S2 / S12 是**只追加不改写**，故总闸关（`trend_bar=""`）
# 时 `_render_stages` 的产物与本特性合入前逐字节相同（用例 P-3 冻结）。
# `id` / `code` **一个字节不改**——它们是稳定契约，前端阶段表与 `STAGE_HEADING_RE` 三处消费者
# 都按它对拍。
_TREND_S1_TITLE = "Execution-bar trend verdict, then higher-timeframe corroboration"


def _trend_s1_mandate(bar: str) -> str:
    """S1 的 armed mandate（**整体替换**：基调先行，高周期降为佐证）。"""
    return (
        f"First state the trend verdict for the execution bar {bar}, quoting the "
        "system-supplied 《本轮趋势基调》 block: direction, strength, the continuous score, "
        "and the single strongest piece of evidence against it. You may disagree with the "
        f"supplied verdict, but you must say so explicitly and name the {bar} evidence that "
        "overrides it. Only then read the higher-timeframe context in the order 1D -> 12H -> "
        "4H -> 1H, and for each frame state trend direction, structure (higher-highs / "
        "lower-lows / range), the nearest support and resistance with its distance in %, and "
        "whether RSI is at an extreme. Higher frames are corroboration for the "
        f"{bar} verdict: they may weaken its strength, they may not by themselves reverse its "
        f"direction - only stronger evidence on {bar} itself can. Mark any missing or stale "
        "frame explicitly; never infer a frame that was not given.")


def _trend_mandates(bar: str) -> dict[str, str]:
    """S2 / S12 的 armed **追加**句（原文一字不改，故 disarmed 路径逐字节回退）。"""
    return {
        "S2": (" If the structure you have just described contradicts the S1 verdict, "
               "re-derive the verdict here and state plainly what changed and why; do not "
               "carry a verdict forward that your own S2 analysis has just falsified."),
        "S12": (" State the round's trend verdict, whether your chosen action is with it "
                "(aligned) or against it (counter-trend), and - if counter-trend - the "
                f"specific {bar} evidence that justifies going against it, together with the "
                "confidence floor and size multiplier you are accepting."),
    }


def _trend_review_mandates(bar: str) -> dict[str, str]:
    """R4 的 armed **追加**句（评审 RP0-4：不改它，本特性会死在链路的最后一米）。

    复核官位于整条决策链的**最下游**（共识与 edge 门禁之后、真实下单之前），而它的评级标准与
    R4 的 mandate 都逐字要求它按「与高周期冲突」否掉提议——正是本特性想放行的那一种单。
    改的只有「高周期分歧本身算不算一条实质性缺陷」这**一个判据**；复核官「只能收紧、不能改
    方向、不能放大、abort 须指名实质性缺陷、fail-closed」这五条安全语义**一个字节不动**。
    """
    return {
        "R4": (f" When a trend verdict for the execution bar {bar} was supplied, judge the "
               "conflict against **that** verdict first. A disagreement with a higher "
               "timeframe is a substantive defect only when the proposal **also** runs "
               f"against the {bar} verdict; when the proposal is aligned with the {bar} "
               "verdict, the higher-timeframe disagreement has already been disclosed in the "
               "verdict block and is not on its own a reason to abort. Say explicitly which "
               "of the two cases you are in.")
    }


def _with_trend_first(stages: tuple[ReasoningStage, ...],
                      bar: str) -> tuple[ReasoningStage, ...]:
    """把「基调先行」装进 S1（整体替换）与 S2 / S12（追加）；未列出的阶段**原对象直通**。"""
    appended = _trend_mandates(bar)
    out: list[ReasoningStage] = []
    for stage in stages:
        if stage.id == "S1":
            out.append(replace(stage, title=_TREND_S1_TITLE, mandate=_trend_s1_mandate(bar)))
        elif stage.id in appended:
            out.append(replace(stage, mandate=stage.mandate + appended[stage.id]))
        else:
            out.append(stage)
    return tuple(out)


def _with_trend_review(stages: tuple[ReasoningStage, ...],
                       bar: str) -> tuple[ReasoningStage, ...]:
    """把 R4 的条件化子句追加进复核协议；未列出的阶段**原对象直通**。"""
    return _with_symmetry(stages, _trend_review_mandates(bar))


_STAGE_HEADING_RULE = (
    "each opened by a line that begins exactly with `## S<k>` (no leading spaces, exactly one "
    "space between `##` and `S`) followed by the stage code")

_REVIEW_HEADING_RULE = (
    "each opened by a line that begins exactly with `## R<k>` (no leading spaces, exactly one "
    "space between `##` and `R`) followed by the stage code")


def resolve_depth_profile(settings, *, backtest: bool = False,
                          override: str | None = None) -> DepthProfile:
    """settings（+ 启动请求覆盖）→ `DepthProfile` 的**唯一**转换点。

    Args:
        settings: `app.config.Settings`（只读三个档位键与四个覆盖键）。
        backtest: 取回测独立档（默认 `standard`）而非实盘档（默认 `deep`），§3.6 的成本护栏。
        override: 启动请求里的 `reasoningDepth`（§4.4 / P1-1）。**深度档必须落在请求对象上**，
            否则暂停→冷续跑会静默降回 settings 默认档而不报任何错。

    Returns:
        该档的 `DepthProfile`；未知档名一律回落 `"deep"`（pydantic 的 `Literal` 已在入口挡住，
        这里是二次保险，**不抛异常**——一个拼错的运维开关不该让整个会话起不来）。

    `off` 档**不接受任何覆盖**：它是运维回退开关，必须逐字回退到合入前形态；一个还生效着的
    `AI_TRADE_REASONING_ROUND_DEADLINE_S` 会让「按下 off」之后仍有一道合入前不存在的闸。
    """
    name = override or (settings.ai_trade_reasoning_depth_backtest if backtest
                        else settings.ai_trade_reasoning_depth)
    profile = _PROFILES.get(str(name or "").strip().lower(), _PROFILES["deep"])
    if not profile.enabled:
        return profile
    profile = _apply_overrides(profile, settings)
    if backtest:
        # 回测车道**不做浅研判追问**（spec §实施过程发现的方案缺陷 I-2）：追问会把决策 LLM 调用
        # 数直接翻倍，而 §3.6 那道成本护栏的全部理由就是「用户点一次回测不该收到一张意外账单」。
        # 追问的价值是让操作员在实盘里读到一份更深的研判——回测没有那个读者，只有那张账单。
        # 归零必须排在 `_apply_overrides` **之后**：否则一个显式的
        # `AI_TRADE_REASONING_MIN_CHARS=1500` 会把追问又装回回测车道，那道成本护栏静默失效。
        profile = replace(profile, min_analysis_chars=0)
    return profile


def _apply_overrides(profile: DepthProfile, settings) -> DepthProfile:
    """把四个显式覆盖键折进档位（0 / -1 = 用档位表的值）；返回新的 frozen 实例。"""
    changes: dict = {}
    if settings.ai_trade_reasoning_max_tokens > 0:
        changes["decision_max_tokens"] = int(settings.ai_trade_reasoning_max_tokens)
    if settings.ai_trade_reflection_max_tokens > 0:
        changes["reflection_max_tokens"] = int(settings.ai_trade_reflection_max_tokens)
    if settings.ai_trade_reasoning_min_chars >= 0:
        changes["min_analysis_chars"] = int(settings.ai_trade_reasoning_min_chars)
    if settings.ai_trade_reasoning_round_deadline_s > 0:
        changes["round_deadline_s"] = float(settings.ai_trade_reasoning_round_deadline_s)
    return replace(profile, **changes) if changes else profile


def profile_by_name(name: str) -> DepthProfile:
    """按档名直取档位（不读 settings，供离线用例与 `_check_reasoning_budget` 使用）。"""
    return _PROFILES.get(str(name or "").strip().lower(), _PROFILES["deep"])


def _render_stages(stages: tuple[ReasoningStage, ...]) -> str:
    return "\n\n".join(f"## {s.id} {s.code} - {s.title}\n{s.mandate}" for s in stages)


def build_deliberation_protocol(profile: DepthProfile, *,
                                reserve_tokens: int = DEFAULT_RESERVE_TOKENS,
                                symmetry: bool = False, trend_bar: str = "") -> str:
    """渲染决策侧《审议协议》正文（`profile.enabled=False` ⇒ 空串 ⇒ 系统 prompt 逐字节回退）。

    篇幅、预算、收尾预留三个数一律**现算**（沿用 `format_horizon_charter` 按本会话真实杠杆
    现算解除阈的先例）——写死算例会在换档后说谎，而说谎的 prompt 不会报错。

    Args:
        profile: 本会话的研判深度档。
        reserve_tokens: 留给收尾工具调用的输出 token。
        symmetry: 方向对称性总闸（directional-symmetry C-1）。`False`（默认）⇒ S7 / S10 / S11
            三处 mandate **逐字节回退**；`True` ⇒ 各追加一句方向对称条款。
        trend_bar: 执行周期趋势基调的总闸兼参数（execution-trend-verdict 轴 C）。
            **空串（默认）⇒ S1 / S2 / S12 三处 mandate 逐字节回退**（用例 P-3 冻结）；
            非空 ⇒ S1 整体替换为「基调先行、高周期降为佐证」，S2 / S12 各追加一句。

    ⚠️ **两个总闸的复合顺序必须写死并冻结**（评审 RP1-13）：两者互相独立、四种组合都必须成立。
    它们的阶段集合不相交（S7/S10/S11 vs S1/S2/S12），故当下顺序不影响产物——但下一轮往任一
    集合里加一个阶段就会静默改变产物，故顺序不是「随便哪个都行」而是一条被用例 P-4b 冻结的契约。
    """
    if not profile.enabled:
        return ""
    stages = DECISION_STAGES
    if symmetry:
        stages = _with_symmetry(stages, _SYMMETRY_MANDATES)   # S7 / S10 / S11
    if trend_bar:
        stages = _with_trend_first(stages, trend_bar)         # S1 / S2 / S12
    total = len(DECISION_STAGES)
    return (
        "# DELIBERATION PROTOCOL - MANDATORY\n\n"
        f"Think in an explicit chain of thought. Before the tool call you MUST write out your\n"
        f"reasoning as {total} numbered stages, in order, {_STAGE_HEADING_RULE}. Stages are not\n"
        "optional and may not be merged.\n"
        f"Write at least {profile.words_per_stage} words per stage. Do not summarise a stage\n"
        "away with \"as above\" - every stage must stand on its own, because a later reader will\n"
        "read stages in isolation.\n\n"
        "Reason from evidence to conclusion, never from conclusion to evidence. Whenever you\n"
        "make a claim, name the input line it comes from. If an input is missing, stale, or\n"
        "contradicts another input, say so at the stage where you first notice it - silent\n"
        "smoothing over a gap is the single most expensive mistake you can make here.\n\n"
        f"{_render_stages(stages)}\n\n"
        "# OUTPUT BUDGET DISCIPLINE\n"
        f"Your entire response for this turn is capped at {profile.decision_max_tokens} output\n"
        f"tokens, and the final tool call must fit inside the last ~{reserve_tokens} of them.\n"
        "Pace yourself: if you are past stage S10 and approaching the cap, compress the\n"
        "remaining stages to their conclusions and call the tool anyway. **An unfinished\n"
        "analysis with a tool call is recoverable; a complete analysis with no tool call is a\n"
        "lost round and the system will fall back to `hold`.**")


def build_review_protocol(profile: DepthProfile, *, symmetry: bool = False,
                          trend_bar: str = "") -> str:
    """渲染反思侧《对抗式复核协议》正文（`profile.enabled=False` ⇒ 空串 ⇒ 逐字节回退）。

    ⚠️ 本文本的**唯一**注入路径是 `decider._stream_reflection` 每轮现造 `system` 时传给
    `build_reflection_system_prompt(reasoning_text=…)`（spec §3.2 / P0-5）。manager 与
    backtest **从未**调用过 `build_reflection_system_prompt`——在那里注入是一条不报错、
    不判红、轴 B 整条死掉的死路。装配判据见用例 S-7 / S-9。

    Args:
        profile: 本会话的研判深度档。
        symmetry: 方向对称性总闸（directional-symmetry C-3）。`True` ⇒ R7 追加「对做空提议
            适用与做多**完全相同**的证据标准」。复核官「只能收紧、不能改方向」的既有语义
            **一个字节都不改**。
        trend_bar: 执行周期趋势基调（execution-trend-verdict 轴 C′ / 评审 RP0-4）。
            **空串（默认）⇒ R1–R7 逐字节回退**（用例 P-8 冻结）；非空 ⇒ R4 追加一句
            **条件化**子句：与高周期冲突，只有在**同时**与执行周期基调冲突时才算实质性缺陷。
            装配判据是「本轮 payload 里有 `trendVerdict`」，由**唯一**调用点
            `decider._stream_reflection` 从当轮 payload 现取——绝不再读一次 settings。

    复合顺序与 `build_deliberation_protocol` 同款写死（先 symmetry 后 trend）；两者的阶段集合
    不相交（R7 vs R4），顺序不影响产物，但顺序本身是被冻结的契约（评审 RP1-13）。
    """
    if not profile.enabled:
        return ""
    stages = REVIEW_STAGES
    if symmetry:
        stages = _with_symmetry(stages, _REVIEW_SYMMETRY_MANDATES)   # R7
    if trend_bar:
        stages = _with_trend_review(stages, trend_bar)               # R4
    total = len(REVIEW_STAGES)
    words = max(1, profile.words_per_stage // 2)   # 复核比研判短，但同样按档位现算
    return (
        "# ADVERSARIAL REVIEW PROTOCOL - MANDATORY\n\n"
        f"Review in an explicit chain of thought. Before the tool call you MUST write out your\n"
        f"review as {total} numbered stages, in order, {_REVIEW_HEADING_RULE}. Stages are not\n"
        "optional and may not be merged.\n"
        f"Write at least {words} words per stage. You are auditing another trader's work: your\n"
        "value comes from what you check, not from how confident you sound.\n\n"
        f"{_render_stages(stages)}")


def count_stages(text: str, prefix: str = "S") -> int:
    """文本里命中的**不重复**阶段标记数（流式半截 / 漏阶段 / 乱序 / 多写都不抛错）。

    Args:
        text: 模型产出的研判或复核全文（可能是流式中的半截文本）。
        prefix: `"S"`（决策侧）或 `"R"`（复核侧）。

    Returns:
        命中的阶段编号去重后的个数；`text` 为空或无标记时为 0。
    """
    if not text:
        return 0
    want = (prefix or "S").upper()
    return len({m.group(2) for m in STAGE_HEADING_RE.finditer(text) if m.group(1) == want})


def is_probably_chinese(text: str, *, floor: float = _CJK_FLOOR) -> bool:
    """CJK 字符占比是否达标；用于**呈现层**取值回落，绝不参与任何交易语义。

    三条铁律（spec §3.1.7 / P1-7）：① 它**绝不**产生 degrade；② **绝不**改变 `TradeDecision`
    的任何字段；③ **绝不**触发浅研判追问。一个摘要写成英文是排版问题，不是安全问题——让它
    影响下单就是把 R-4 的教训（为质量问题新造安全回落）再走一遍。
    """
    stripped = "".join(text.split()) if text else ""
    if not stripped:
        return False
    return len(_CJK_RE.findall(stripped)) / len(stripped) >= floor


def resolve_analysis_excerpt(digest: str | None, analysis: str, *,
                             limit: int = _TAIL_EXCERPT_CHARS) -> str | None:
    """journal 摘录与左栏决策卡首段的**同一份**取值（三级回退，§3.3.4 / 补根因 C-6）。

    ① `digest`（模型给的中文证据摘要，先过语言守卫）→ ② S12 段落前 `limit` 字符 →
    ③ 研判全文前 `limit` 字符（合入前行为，零回归回退路径）。三级都空 ⇒ `None`。

    放在纯核心而不是 `session` 上，是为了让用例断言的与生产跑的**是同一份逻辑**——两处各写
    一遍的判据是零成本的自欺（本特性评审给实现节点的那条提醒）。
    """
    clean = (digest or "").strip()
    if clean and is_probably_chinese(clean):
        return clean[:limit]
    tail = tail_stage_excerpt(analysis, limit=limit)
    return tail or ((analysis or "")[:limit] or None)


def tail_stage_excerpt(text: str, *, stage: str = TAIL_STAGE,
                       limit: int = _TAIL_EXCERPT_CHARS) -> str:
    """取最后一个 `## <stage>` 段落的前 `limit` 个字符；找不到该阶段则返回 `""`。

    存在的理由（根因 C-6）：12 阶段协议下研判**前** 400 字符恒为 S1 的样板抬头，而 journal
    摘录与左栏决策卡首段都取自那 400 字符——长期记忆会被样板文喂满。S12 是模型自己的结论段，
    是这份全文里唯一「一句话说清本轮结论」的位置。
    """
    if not text:
        return ""
    marker = str(stage or TAIL_STAGE).upper()
    last = -1
    for match in STAGE_HEADING_RE.finditer(text):
        if f"{match.group(1)}{match.group(2)}" == marker:
            last = match.start()
    if last < 0:
        return ""
    # 「取其后」：跳过标题行本身——`## S12 VERDICT` 同样是样板抬头，把它摆到决策卡首段
    # 与取 S1 抬头没有本质区别。行尾缺失（流式半截）时退回从标记处取。
    head_end = text.find("\n", last)
    body = text[last:] if head_end < 0 else text[head_end + 1:]
    return body[:max(0, int(limit))].strip()
