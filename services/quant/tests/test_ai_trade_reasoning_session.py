"""会话侧的深度研判观测（ai-trade-deep-reasoning-chain §7.5 S-2 / S-3 / S-4 / S-4b / S-5）。

`ReasoningRuntime` 是纯核心（无 IO、零会话依赖），故 S-4/S-4b/S-5 直接在它上面测；S-2/S-3 测的是
摘录换源的三级回退，它是一段纯函数逻辑 + 两个消费者的共享取值。
"""
from __future__ import annotations

from app.ai_auto.decider import ReasoningMeta, TradeDecision
from app.ai_auto.reasoning_protocol import resolve_analysis_excerpt
from app.ai_auto.reasoning_runtime import ReasoningRuntime
from app.config import Settings

_ANALYSIS_EXCERPT_CHARS = 400


def _meta(**kw) -> ReasoningMeta:
    base = dict(depth="deep", chars=9412, stages=12, truncated=False, reasked=False,
                reask_adopted=False, deadline_hit=False, input_tokens=100,
                output_tokens=200, reflection_chars=4180, reflection_stages=7)
    base.update(kw)
    return ReasoningMeta(**base)


def _excerpt(decision: TradeDecision, analysis: str) -> str | None:
    """走的就是 `session._analysis_excerpt` 调的**那一个**纯核心函数（不另写第二份口径）。"""
    return resolve_analysis_excerpt(decision.evidence_digest, analysis,
                                    limit=_ANALYSIS_EXCERPT_CHARS)


# ---------------------------------------------------------------- S-2 / S-3（摘录换源）
def _twelve_stage_analysis() -> str:
    body = "\n".join(f"## S{i} CODE\n{'x' * 300}" for i in range(1, 12))
    return f"{body}\n## S12 VERDICT\n采纳 4H 趋势，选择开多 0.25。"


def test_journal_excerpt_prefers_the_chinese_digest():
    """S-2 ①：模型给了中文 `evidenceDigest` ⇒ 摘录就是它（截到 400）。"""
    decision = TradeDecision("open_long", 0.25, 0.9, "r",
                             evidence_digest="采纳 4H 趋势与量能配合，推翻布林回归信号。")
    assert _excerpt(decision, _twelve_stage_analysis()).startswith("采纳 4H 趋势")


def test_journal_excerpt_falls_back_to_s12_when_the_digest_is_english():
    """S-2 ② + D-12：英文摘要 ⇒ 视作未提供，回落 S12 尾段（三处中文界面不会同时变英文）。"""
    decision = TradeDecision("open_long", 0.25, 0.9, "r",
                             evidence_digest="Adopted the 4H trend; overturned Bollinger.")
    excerpt = _excerpt(decision, _twelve_stage_analysis())
    assert excerpt.startswith("采纳 4H 趋势，选择开多")


def test_journal_excerpt_falls_back_to_today_behaviour_without_stage_markers():
    """S-2 ③：既无摘要也无阶段标记 ⇒ 前 400 字符（合入前行为，零回归回退路径）。"""
    decision = TradeDecision("hold", None, None, "r")
    flat = "y" * 900
    assert _excerpt(decision, flat) == "y" * 400


def test_journal_excerpt_is_never_the_s1_boilerplate():
    """S-3（C-6 的可证伪判据）：12 阶段全文入参下，摘录**不以** `## S1` 开头。"""
    decision = TradeDecision("hold", None, None, "r")
    assert not _excerpt(decision, _twelve_stage_analysis()).startswith("## S1")


def test_degraded_round_yields_no_excerpt():
    """降级轮 `last_analysis` 恒为空串 ⇒ 摘录为 None（journal / 决策事件不带残段）。"""
    assert _excerpt(TradeDecision("hold", None, None, ""), "") is None


# ---------------------------------------------------------------- S-4 / S-4b（压力提示）
def _runtime(interval_s: float = 120.0, **kw) -> ReasoningRuntime:
    return ReasoningRuntime(Settings(**kw), wake_interval_s=interval_s)


def test_pressure_note_appears_only_above_ratio():
    """S-4：未过阈 ⇒ 整键缺席；过阈 ⇒ `suggestIntervalMin` 算式正确，字段名是 `ewmaMs`。"""
    rt = _runtime()
    rt.note_round(_meta(), elapsed_ms=30_000, degrade=None)     # 30s < 0.6×120s
    assert rt.pressure_wire() is None
    for _ in range(8):
        rt.note_round(_meta(), elapsed_ms=186_000, degrade=None)
    wire = rt.pressure_wire()
    assert wire is not None
    assert "p50Ms" not in wire and wire["ewmaMs"] > 0            # 算的是 EWMA，不是中位数
    assert wire["intervalS"] == 120.0 and wire["level"] == "warn"
    assert wire["suggestIntervalMin"] >= 5


def test_pressure_note_absent_when_interval_wake_disabled():
    """S-4b：`wake_interval_s == 0`（用户刻意关闭定时唤醒）⇒ **整键恒缺席**，无论耗时多长。

    v1 的判据在此退化为 `ewma > 0` 恒真，会给一个不按周期交易的用户永久挂一条无意义的建议。
    """
    rt = _runtime(interval_s=0.0)
    for _ in range(10):
        rt.note_round(_meta(), elapsed_ms=3_600_000, degrade=None)
    assert rt.pressure_wire() is None


def test_pressure_suggestion_is_clamped_by_the_wake_interval_ceiling():
    """`suggestIntervalMin` 按 `ai_trade_wake_interval_ceil_min` 夹紧，绝不建议一个后端不接受的周期。"""
    rt = _runtime(interval_s=60.0, ai_trade_wake_interval_ceil_min=10.0)
    for _ in range(10):
        rt.note_round(_meta(), elapsed_ms=3_600_000, degrade=None)
    assert rt.pressure_wire()["suggestIntervalMin"] == 10


def test_pressure_note_speaks_from_the_first_sample():
    """样本门槛是 1：最坏单轮接近 10 分钟，要 3 个样本意味着半小时后才第一次被告知。"""
    rt = _runtime()
    rt.note_round(_meta(), elapsed_ms=600_000, degrade=None)
    assert rt.pressure_wire() is not None


def test_pressure_ratio_zero_disables_the_note():
    """`ai_trade_reasoning_pressure_ratio=0` ⇒ 关闭提示（运维开关必须真的能关）。"""
    rt = _runtime(ai_trade_reasoning_pressure_ratio=0.0)
    rt.note_round(_meta(), elapsed_ms=600_000, degrade=None)
    assert rt.pressure_wire() is None


def test_degraded_rounds_do_not_pollute_the_ewma():
    """只统计**非降级轮**：一次超时回落的 210 秒不是「模型想了多久」。"""
    rt = _runtime()
    rt.note_round(_meta(), elapsed_ms=210_000, degrade="decision_timeout")
    assert rt.pressure_wire() is None


# ---------------------------------------------------------------- S-5（会话级落痕）
def test_ai_stats_reasoning_is_written_with_the_right_counters():
    """S-5：`aiStats.reasoning` 的统计量正确，且**不新增任何列**（写既有 JSON blob）。"""
    rt = _runtime()
    rt.note_round(_meta(chars=9000, stages=12), elapsed_ms=100_000, degrade=None)
    rt.note_round(_meta(chars=11000, stages=11, truncated=True), elapsed_ms=100_000,
                  degrade=None)
    rt.note_round(_meta(chars=7000, stages=12, reasked=True, reask_adopted=True),
                  elapsed_ms=100_000, degrade=None)
    rt.note_round(_meta(chars=8000, stages=12, deadline_hit=True), elapsed_ms=100_000,
                  degrade=None)
    stats = rt.stats()
    assert stats["depth"] == "deep" and stats["rounds"] == 4
    assert stats["medianChars"] == 8500.0 and stats["medianStages"] == 12.0
    assert stats["truncatedRounds"] == 1
    assert stats["reaskedRounds"] == 1 and stats["reaskAdoptedRounds"] == 1
    assert stats["deadlineHitRounds"] == 1
    assert stats["reflectionRounds"] == 4 and stats["reflectionMedianChars"] == 4180.0
    assert stats["inputTokens"] == 400 and stats["outputTokens"] == 800
    assert stats["tokensProviderKnown"] is True


def test_ai_stats_is_absent_without_any_sample():
    """零样本 ⇒ `None` ⇒ 不写这个键（老会话与 off 档的历史详情整段隐藏）。"""
    assert _runtime().stats() is None
    rt = _runtime()
    rt.note_round(None, elapsed_ms=1000, degrade=None)      # off 档 / 未装配
    assert rt.stats() is None and rt.depth == "" and rt.tokens_wire() is None


def test_usage_absent_marks_provider_unknown():
    """B-9 的会话侧：provider 全程不回 usage ⇒ 两个读数为 `None` 且诚实位为 False。

    不区分「没花 token」与「provider 没告诉我们花了多少」，一份 `outputTokens: 0` 的历史会被
    当成「这个会话几乎没推理」而误导后续调参。
    """
    rt = _runtime()
    rt.note_round(_meta(input_tokens=None, output_tokens=None), elapsed_ms=1000, degrade=None)
    stats = rt.stats()
    assert stats["tokensProviderKnown"] is False
    assert stats["inputTokens"] is None and stats["outputTokens"] is None
    assert rt.tokens_wire() is None                       # 整键缺席，而不是一对 0


def test_content_filtered_rounds_are_counted_even_without_meta():
    """内容拦截轮**没有** `ReasoningMeta`（那一轮没产出研判），但它必须被计数。"""
    rt = _runtime()
    rt.note_round(None, elapsed_ms=1000, degrade="content_filtered")
    rt.note_round(_meta(), elapsed_ms=1000, degrade=None)
    assert rt.stats()["contentFilteredRounds"] == 1


def test_tokens_wire_accumulates_across_rounds():
    """`status.reasoningTokens` 是会话级累计（概览条上那一行的取值源）。"""
    rt = _runtime()
    rt.note_round(_meta(input_tokens=10, output_tokens=3), elapsed_ms=1000, degrade=None)
    rt.note_round(_meta(input_tokens=20, output_tokens=7), elapsed_ms=1000, degrade=None)
    assert rt.tokens_wire() == {"inputTokens": 30, "outputTokens": 10}
