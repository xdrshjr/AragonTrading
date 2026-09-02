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


# ============ 方向对称化（ai-trade-directional-symmetry 轴 C） ============
#
# 用户诉求的原话是「研判交易决策和行动的时候，要考虑方向性，不能无脑开多」。它在协议正文里
# 此前**没有任何落点**：S10 只要求「三个候选，其中一个必须是 hold」——`open_long 25% /
# open_long 50% / hold` 完全合规；S11 的 "the reverse action" 在空仓状态下可以被合理地读成
# `hold` 而不是「反方向开仓」。

def test_symmetry_off_is_byte_identical():
    """总闸关（默认）⇒ 三处 mandate 与 R7 **逐字节回退**到本特性合入前。"""
    assert build_deliberation_protocol(_DEEP) == build_deliberation_protocol(_DEEP,
                                                                             symmetry=False)
    assert build_review_protocol(_DEEP) == build_review_protocol(_DEEP, symmetry=False)
    plain = build_deliberation_protocol(_DEEP)
    assert "at least one short candidate" not in plain
    assert "not holding" not in plain


def test_s10_requires_both_directions_on_the_table():
    """S10：候选集必须**同时**含做多与做空——只列一个方向等同于没有完成 S10。"""
    text = build_deliberation_protocol(_DEEP, symmetry=True)
    s10 = text[text.index("## S10 "):text.index("## S11 ")]
    assert "at least one long candidate" in s10
    assert "at least one short candidate" in s10
    assert "only one direction" in s10
    # 既有正文一个字都不改（只追加）。
    assert "Put at least three candidate actions on the table (one of them must be `hold`)." in s10


def test_s11_reverse_action_is_not_holding():
    """S11：空仓时的 "reverse action" 必须被明确成「反方向开仓」，而不是观望。"""
    text = build_deliberation_protocol(_DEEP, symmetry=True)
    s11 = text[text.index("## S11 "):text.index("## S12 ")]
    assert "opening in the opposite" in s11 and "not holding" in s11


def test_s7_requires_the_opposite_case():
    text = build_deliberation_protocol(_DEEP, symmetry=True)
    s7 = text[text.index("## S7 "):text.index("## S8 ")]
    assert "strongest case for the **opposite** direction" in s7


def test_review_r7_applies_the_same_standard_to_a_short():
    """R7：对做空提议适用与做多**完全相同**的证据标准；安全语义一个字节都不改。"""
    text = build_review_protocol(_DEEP, symmetry=True)
    r7 = text[text.index("## R7 "):]
    assert "same standard of evidence to a short proposal" in r7
    assert "never weaker merely because it is a short" in r7
    # 三条安全锚点仍在（只能收紧 / abort 需指名缺陷 / 机会成本对称）。
    assert "You may only tighten." in r7
    assert "name one substantive defect" in r7


def test_off_depth_still_returns_empty_even_with_symmetry():
    """`off` 档是运维回退开关：它必须回退**全部**行为，方向对称条款也不例外。"""
    assert build_deliberation_protocol(_OFF, symmetry=True) == ""
    assert build_review_protocol(_OFF, symmetry=True) == ""


def test_symmetry_never_changes_the_stage_count_or_order():
    """追加 mandate 不得改变阶段数、编号或顺序（S9 要求模型逐条复述，编号是稳定契约）。"""
    text = build_deliberation_protocol(_DEEP, symmetry=True)
    positions = [text.index(f"## S{i} ") for i in range(1, 13)]
    assert positions == sorted(positions)
    assert count_stages(text, "S") == 12
    assert "## S13" not in text


# ============================ 执行周期趋势基调的三处 mandate（§7.4 P-3 / P-4 / P-4b / P-8）====
def test_deliberation_protocol_is_byte_identical_without_trend_bar():
    """P-3：`trend_bar=""` ⇒ 与合入前**逐字节相同**（G-1 的零回归路径）。"""
    profile = profile_by_name("deep")
    assert build_deliberation_protocol(profile) == build_deliberation_protocol(
        profile, trend_bar="")


def test_trend_first_rewrites_s1_and_appends_to_s2_s12_only():
    """P-4：S1 整体替换、S2 / S12 各追加一句，**其余九个阶段逐字节不变**。"""
    profile = profile_by_name("deep")
    plain = build_deliberation_protocol(profile)
    armed = build_deliberation_protocol(profile, trend_bar="15m")
    assert "execution bar 15m" in armed
    assert "may not by themselves reverse" in armed
    assert "re-derive the verdict here" in armed          # S2
    assert "with it (aligned) or against it (counter-trend)" in armed   # S12
    # `id` / `code` 是稳定契约，一个字节都不改。
    assert "## S1 REGIME" in armed and "## S2 EXECUTION" in armed
    # 其余九个阶段逐字节不变（按阶段切块对拍）。
    def stages(text: str) -> dict[str, str]:
        out, current, buf = {}, None, []
        for line in text.splitlines():
            if line.startswith("## S"):
                if current:
                    out[current] = "\n".join(buf)
                current, buf = line.split()[1], []
            elif current:
                buf.append(line)
        if current:
            out[current] = "\n".join(buf)
        return out

    before, after = stages(plain), stages(armed)
    untouched = set(before) - {"S1", "S2", "S12"}
    assert untouched and all(before[key] == after[key] for key in untouched)


def test_symmetry_and_trend_switches_compose_in_a_frozen_order():
    """P-4b（评审 RP1-13）：`(symmetry, trend_bar)` 四种组合都成立，且复合顺序被写死。

    两者的阶段集合不相交（S7/S10/S11 vs S1/S2/S12），故当下顺序不影响产物——但顺序本身是
    契约：下一轮往任一集合里加一个阶段就会静默改变产物。
    """
    profile = profile_by_name("deep")
    both = build_deliberation_protocol(profile, symmetry=True, trend_bar="15m")
    only_sym = build_deliberation_protocol(profile, symmetry=True)
    only_trend = build_deliberation_protocol(profile, trend_bar="15m")
    plain = build_deliberation_protocol(profile)
    assert len({both, only_sym, only_trend, plain}) == 4
    for marker in ("the strongest case for the **opposite** direction",
                   "at least one short candidate"):
        assert marker in both and marker in only_sym and marker not in plain
    assert "execution bar 15m" in both and "execution bar 15m" in only_trend


def test_review_protocol_is_byte_identical_without_trend_bar():
    """P-8：`build_review_protocol(..., trend_bar="")` 与合入前**逐字节相同**。"""
    profile = profile_by_name("deep")
    assert build_review_protocol(profile) == build_review_protocol(profile, trend_bar="")
    assert (build_review_protocol(profile, symmetry=True)
            == build_review_protocol(profile, symmetry=True, trend_bar=""))


def test_review_r4_conditionalises_the_higher_timeframe_conflict():
    """P-9①（评审 RP0-4）：R4 追加条件化句，**其余六个复核阶段逐字节不变**。"""
    profile = profile_by_name("deep")
    plain = build_review_protocol(profile)
    armed = build_review_protocol(profile, trend_bar="15m")
    assert "judge the conflict against **that** verdict first" in armed
    assert "is not on its own a reason to abort" in armed
    # 复核官的安全语义一个字节不改。
    assert "You may only tighten" in armed
    for stage_id in ("R1", "R2", "R3", "R5", "R6", "R7"):
        head = f"## {stage_id} "
        assert plain[plain.index(head):].split("## ")[1] == armed[
            armed.index(head):].split("## ")[1]
