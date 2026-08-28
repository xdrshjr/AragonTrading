"""深度思维链协议纯核心单测（ai-trade-deep-reasoning-chain §7.2 P-1…P-7 + §7.1 G-6/G-7b）。

纯离线：不读网络、不建 DB、不造决策器。本文件守的是「协议本身说了什么」与「档位派生量是什么」
这两件事——它们是轴 A / 轴 B 的全部前提，一旦这里漂了，上层用例再绿也没有意义。
"""
from __future__ import annotations

import pytest

from app.ai_auto.reasoning_protocol import (
    CONTENT_FILTER_STOP_REASONS,
    DECISION_STAGES,
    LANGUAGE_CONTRACT,
    REVIEW_STAGES,
    TRUNCATION_STOP_REASONS,
    build_deliberation_protocol,
    build_review_protocol,
    count_stages,
    is_probably_chinese,
    profile_by_name,
    resolve_depth_profile,
    tail_stage_excerpt,
)
from app.config import Settings

_DEEP = profile_by_name("deep")
_OFF = profile_by_name("off")


# ---------------------------------------------------------------- P-1 / P-2 / P-3
def test_protocol_declares_all_twelve_stages_in_order():
    """P-1：`## S1`…`## S12` 全部出现且**下标单调递增**；`## S13` 不出现。"""
    text = build_deliberation_protocol(_DEEP)
    positions = [text.index(f"## S{i} ") for i in range(1, 13)]
    assert positions == sorted(positions)
    assert "## S13" not in text
    assert count_stages(text, "S") == 12


def test_protocol_states_word_floor_and_budget_from_profile():
    """P-2：篇幅 / 预算 / 收尾预留三个数**跟着档位与 settings 走**（防写死算例）。"""
    deep = build_deliberation_protocol(_DEEP, reserve_tokens=768)
    assert "120 words" in deep and "8192 output" in deep and "768" in deep
    standard = build_deliberation_protocol(profile_by_name("standard"), reserve_tokens=512)
    assert "60 words" in standard and "4096 output" in standard and "512" in standard
    assert "120 words" not in standard      # 换档后三个数必须**跟着变**


def test_protocol_puts_the_tool_call_hard_constraint_last():
    """P-3：协议以「宁可分析不完整也要调工具」的预算纪律收尾（近因位）。"""
    text = build_deliberation_protocol(_DEEP).rstrip()
    assert text.endswith("lost round and the system will fall back to `hold`.**")


# ---------------------------------------------------------------- P-4
def test_language_contract_names_the_three_chinese_fields():
    """P-4：语言契约含且仅含 `reason` / `evidenceDigest` / `exitEvidence` 三个中文例外字段。"""
    assert "Conduct 100% of your reasoning in English" in LANGUAGE_CONTRACT
    for field in ("`reason`", "`evidenceDigest`", "`exitEvidence`"):
        assert field in LANGUAGE_CONTRACT
    # 「只有这三个」这句话本身必须在（否则模型会把别的字段也写成中文）。
    assert "and only these" in LANGUAGE_CONTRACT


# ---------------------------------------------------------------- P-5
def test_review_protocol_declares_seven_stages_and_keeps_safety_anchors():
    """P-5：`## R1`…`## R7` 齐全；三条安全锚点逐字仍在（英文脊柱不得稀释安全语义）。"""
    text = build_review_protocol(_DEEP)
    positions = [text.index(f"## R{i} ") for i in range(1, 8)]
    assert positions == sorted(positions)
    assert count_stages(text, "R") == 7
    assert "You may only tighten." in text
    assert "name one substantive defect" in text
    assert "costs exactly as much as taking a bad one" in text


# ---------------------------------------------------------------- P-6
@pytest.mark.parametrize("text,expected", [
    ("## S1 REGIME\nhalf written…", 1),                    # 流式半截
    ("## S1 A\n## S3 C\n## S12 Z\n", 3),                   # 漏阶段
    ("## S3 C\n## S1 A\n", 2),                             # 乱序
    ("#### S1 A\n", 0),                                    # 四级标题不算
    ("##S1 A\n", 0),                                       # [P2-1] 无空格不算
    ("   ## S1 A\n", 0),                                   # [P2-1] 缩进不算
    ("", 0),
])
def test_count_stages_tolerates_partial_and_extra_markers(text, expected):
    """P-6：四种畸形输入都不抛错；`##S1` 与缩进标题**都不**计为阶段（三处正则同口径）。"""
    assert count_stages(text, "S") == expected


def test_count_stages_does_not_mix_decision_and_review_prefixes():
    """两条协议共用同一个正则，但计数必须按前缀分开——混一起会让反思段读出 12/7。"""
    text = "## S1 A\n## R1 B\n## R2 C\n"
    assert count_stages(text, "S") == 1
    assert count_stages(text, "R") == 2


# ---------------------------------------------------------------- P-7
def test_tail_stage_excerpt_prefers_s12():
    """P-7：有 S12 ⇒ 取该段正文前 400 字符；无 S12 ⇒ 返回 `""` 让调用方兜底。"""
    text = "## S1 REGIME\n" + "boilerplate " * 60 + "\n## S12 VERDICT\nchosen action: hold."
    excerpt = tail_stage_excerpt(text)
    assert excerpt.startswith("chosen action: hold")
    assert "## S12" not in excerpt              # 标题行本身也是样板抬头，同样不该摆到界面上
    assert tail_stage_excerpt("## S1 REGIME\nno verdict here") == ""
    assert tail_stage_excerpt("") == ""


def test_tail_stage_excerpt_takes_the_last_s12_when_the_model_repeats_it():
    """模型把 S12 写了两遍（追问轮常见）时取**最后**一段——那才是它最终的结论。"""
    text = "## S12 VERDICT\nfirst try\n## S12 VERDICT\nsecond and final"
    assert tail_stage_excerpt(text) == "second and final"


# ---------------------------------------------------------------- 中文守卫（P1-7）
@pytest.mark.parametrize("text,expected", [
    ("采纳 4H 趋势与量能配合，推翻布林回归信号。", True),
    ("Adopted the 4H trend; overturned the Bollinger mean-reversion signal.", False),
    ("", False),
    ("   ", False),
    ("4H 趋势与量能配合，推翻 BOLL 回归信号；1H 跌破 29,850 即失效。", True),   # 中英混排但中文占比达标
    ("BTC-USDT-SWAP 30m 开多 0.25", False),          # 中文占比不足 ⇒ 视作未提供（回落 S12 尾段）
])
def test_is_probably_chinese_is_a_presentation_guard_only(text, expected):
    """呈现层守卫：CJK 占比达标即视作中文。它绝不参与任何交易语义（判据见 decider 侧用例）。"""
    assert is_probably_chinese(text) is expected


# ---------------------------------------------------------------- G-6 / G-7b
def test_backtest_default_depth_is_standard():
    """G-6：回测独立档默认 `standard`（§3.6 的成本护栏），实盘默认 `deep`。"""
    settings = Settings()
    assert resolve_depth_profile(settings).name == "deep"
    assert resolve_depth_profile(settings, backtest=True).name == "standard"


def test_off_depth_restores_today_timeouts():
    """G-7b：`off` 档回退的是**行为**——超时 / 尝试次数上限 / 墙钟闸全部回到合入前形态。"""
    profile = resolve_depth_profile(Settings(), override="off")
    assert profile.enabled is False
    assert profile.decision_timeout_s == 60.0        # config.py 今日的默认值
    assert profile.reflection_timeout_s == 45.0
    assert profile.max_attempts_cap == 0             # 不封顶
    assert profile.round_deadline_s == 0.0           # 不设闸
    assert profile.min_analysis_chars == 0           # 不追问
    assert build_deliberation_protocol(profile) == ""
    assert build_review_protocol(profile) == ""


def test_off_depth_ignores_every_override():
    """回退开关必须回退**全部**行为：一个还生效着的墙钟闸会让 `off` 不再等于合入前。"""
    settings = Settings(ai_trade_reasoning_round_deadline_s=300.0,
                        ai_trade_reasoning_max_tokens=12000,
                        ai_trade_reasoning_min_chars=5000)
    profile = resolve_depth_profile(settings, override="off")
    assert profile == _OFF


def test_unknown_depth_falls_back_to_deep_without_raising():
    """拼错的运维开关不该让整个会话起不来（与 §3.1.1 的取舍一致）。"""
    assert resolve_depth_profile(Settings(), override="dep").name == "deep"
    assert resolve_depth_profile(Settings(), override="").name == "deep"


def test_explicit_settings_override_the_profile_table():
    """三个显式覆盖键生效，且**只覆盖被显式设置的那一个**。"""
    profile = resolve_depth_profile(Settings(ai_trade_reasoning_max_tokens=12000,
                                             ai_trade_reasoning_min_chars=0))
    assert profile.decision_max_tokens == 12000
    assert profile.min_analysis_chars == 0                     # 0 = 关闭追问
    assert profile.reflection_max_tokens == _DEEP.reflection_max_tokens   # 未覆盖 ⇒ 档位值


def test_backtest_lane_never_reasks():
    """回测车道不做追问：它会把决策 LLM 调用数直接翻倍，而 §3.6 那道护栏正是为成本而设。"""
    assert resolve_depth_profile(Settings(), backtest=True).min_analysis_chars == 0


def test_backtest_guard_survives_an_explicit_min_chars_override():
    """回测归零必须排在显式覆盖**之后**（终审修复）。

    两者顺序写反时，一个显式的 `AI_TRADE_REASONING_MIN_CHARS=1500` 会把追问又装回回测车道，
    使 I-2 那道成本护栏静默失效（决策 LLM 调用数翻倍），而界面上看不出任何异常。
    实盘车道**不受**影响：同一个覆盖在那里依然生效。
    """
    settings = Settings(ai_trade_reasoning_min_chars=1500)
    assert resolve_depth_profile(settings, backtest=True).min_analysis_chars == 0
    assert resolve_depth_profile(settings).min_analysis_chars == 1500


# ---------------------------------------------------------------- provider 归一（P0-1）
def test_truncation_stop_reasons_cover_both_provider_dialects():
    """截断口径必须 **provider 无关**：写死任一家会让另外半个家族原样退回「看不出原因」。"""
    assert "max_tokens" in TRUNCATION_STOP_REASONS     # anthropic
    assert "length" in TRUNCATION_STOP_REASONS         # openai 标准的 finish_reason
    # 内容安全拦截**不是**截断：它单列且不可重试。
    assert TRUNCATION_STOP_REASONS.isdisjoint(CONTENT_FILTER_STOP_REASONS)
    assert "content_filter" in CONTENT_FILTER_STOP_REASONS


# ---------------------------------------------------------------- 阶段文案完整性
def test_every_stage_carries_a_non_trivial_mandate():
    """每个阶段都必须有**明确的必答问题**——协议的价值不在阶段数，而在每一段的义务。"""
    for stage in DECISION_STAGES + REVIEW_STAGES:
        assert len(stage.mandate) >= 120, stage.id
        assert stage.code and stage.title
