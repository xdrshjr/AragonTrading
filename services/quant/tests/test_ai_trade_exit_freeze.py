"""X-7 冻结门禁（ai-trade-exit-priority-and-commitment G-1 / G-2 / G-9 · #34）。

**未装配即逐字节回退**：两条运维总闸关闭时，系统 / 决策 / 反思三份 prompt 逐字节不变、决策工具
schema 恒等于模块常量、`tradingHorizon` 快照的两个新嵌套键与三个 `exitIntent*` 键全部缺席。

判据沿用仓库既有模式（**不得硬编码旧哈希**）：「显式传空垫片 vs 完全不传」的 sha256 相等——
新形参一旦被写成必填、或垫片默认值被改成非空，用例立刻判红。
"""
from __future__ import annotations

import ast
import hashlib
import re
from pathlib import Path

from app.ai_auto.acceptance_text import EXIT_SOURCE_LABELS
from app.ai_auto.exit_attribution import EXIT_SOURCES
from app.ai_auto.experience.taxonomy import AXIS_EXIT, AXIS_VALUES, VALUE_LABEL_ZH
from app.ai_auto.hold_horizon import (
    RELEASE_GATES,
    HorizonFacts,
    HorizonState,
    build_snapshot,
    resolve_horizon_rule,
)
from app.ai_auto.hold_horizon_text import (
    format_charter,
    format_horizon_for_prompt,
    format_reflection_note,
)
from app.ai_auto.prompts import (
    DECISION_TOOL_SCHEMA,
    build_ai_trade_system_prompt,
    build_decision_tool_description,
    build_decision_tool_schema,
    build_reflection_system_prompt,
)
from app.config import Settings

_WEB = Path(__file__).resolve().parents[3] / "apps" / "web"
_NOW = 1_700_000_000.0


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _off() -> Settings:
    return Settings(ai_trade_exit_priority_enabled=False,
                    ai_trade_exit_commitment_enabled=False)


def _facts(**over) -> HorizonFacts:
    base = {"seq": 1, "side": "long", "entry_px": 100.0, "mark_px": 100.05,
            "leverage": 3.0, "now_wall": _NOW, "px_age_s": 1.0, "cost_bps": 13.0}
    return HorizonFacts(**{**base, **over})


# ---------------- G-1：三份 prompt 逐字节回退 ----------------
def test_g1_unarmed_keeps_three_prompts_byte_identical():
    """总闸关 ⇒ 三份 prompt 与本特性合入前逐字节相等（空垫片 vs 完全不传）。"""
    before = build_ai_trade_system_prompt("BTC-USDT-SWAP", "1H", 100.0, 3.0)
    after = build_ai_trade_system_prompt("BTC-USDT-SWAP", "1H", 100.0, 3.0,
                                         horizon_text="")
    assert _sha(before) == _sha(after)
    assert _sha(build_reflection_system_prompt()) == _sha(build_reflection_system_prompt(""))
    assert format_reflection_note(None) == ""
    assert format_horizon_for_prompt(None) == ""


def test_g1_charter_has_no_exit_lane_section_when_unarmed():
    """本层未装配（`exit_priority is None`）⇒ 宪章尾部**不追加任何一个字节**。"""
    rule = resolve_horizon_rule({"profile": "intraday_swing"}, _off(), bar="1H")
    assert rule.exit_priority is None
    assert "离场优先通道" not in format_charter(rule)
    armed = resolve_horizon_rule({"profile": "intraday_swing"}, Settings(), bar="1H")
    assert "离场优先通道" in format_charter(armed)     # 证伪：装配时确实会出现


def test_g1_round_section_omits_both_new_lines_when_unarmed():
    """键缺席 ⇒ 两行整行省略（`_exit_priority_line` / `_exit_commitment_line`）。"""
    rule = resolve_horizon_rule({"profile": "intraday_swing"}, _off(), bar="1H")
    snap = build_snapshot(rule, HorizonState(epoch=("long", 1), opened_wall=_NOW - 3600),
                          _facts())
    text = format_horizon_for_prompt(snap)
    assert "离场优先级" not in text and "离场意图正在兑现" not in text


def test_g1_confirm_line_is_verbatim_when_there_is_no_lapse():
    """`lapses == 0` ⇒ 离场确认那一句逐字节回退今天（衰减说明整段不出现）。"""
    rule = resolve_horizon_rule({"profile": "intraday_swing"}, Settings(), bar="1H")
    state = HorizonState(epoch=("long", 1), opened_wall=_NOW - 3600,
                         exit_confirm_streak=1, first_exit_intent_wall=_NOW - 600)
    text = format_horizon_for_prompt(build_snapshot(rule, state, _facts()))
    assert "按衰减计入" not in text
    from dataclasses import replace
    lapsed = format_horizon_for_prompt(
        build_snapshot(rule, replace(state, exit_intent_lapses=1), _facts()))
    assert "按衰减计入" in lapsed                     # 证伪


# ---------------- G-2：决策工具恒等 ----------------
def test_g2_schema_identity_still_holds():
    """`build_decision_tool_schema(0.0) is DECISION_TOOL_SCHEMA` —— 新形参**必须默认 False**。"""
    assert build_decision_tool_schema(0.0) is DECISION_TOOL_SCHEMA
    assert build_decision_tool_schema(0.0, allow_exit_priority=False) is DECISION_TOOL_SCHEMA
    armed = build_decision_tool_schema(0.0, allow_exit_priority=True)
    assert armed is not DECISION_TOOL_SCHEMA
    assert "exitConviction" in armed["properties"]
    # 常量本体绝不被就地改写（`step>0` 时泄漏回常量是本函数的前科）
    assert "exitConviction" not in DECISION_TOOL_SCHEMA["properties"]
    assert armed["required"] == DECISION_TOOL_SCHEMA["required"]   # 两者永远可选


def test_g2_tool_description_is_verbatim_when_unarmed():
    """描述同款恒等：关闭时与本特性合入前逐字相同。"""
    from app.ai_auto.prompts import DECISION_TOOL_DESCRIPTION

    assert build_decision_tool_description() is DECISION_TOOL_DESCRIPTION
    assert build_decision_tool_description(allow_exit_priority=False) is DECISION_TOOL_DESCRIPTION
    assert build_decision_tool_description(allow_exit_priority=True) != DECISION_TOOL_DESCRIPTION


# ---------------- 快照键集合 ----------------
def test_snapshot_keys_are_additive_only():
    """`tradingHorizon` 只多三个 `exitIntent*` 键；两个新嵌套键由**会话层**按需挂上。

    快照本身**永不**带 `exitPriority` / `exitCommitment`——它们是运行期装配（会话侧
    `_attach_exit_wires`），未装配 / 本轮非平仓 / 未武装时整键缺席（不是 null）。
    """
    rule = resolve_horizon_rule({"profile": "intraday_swing"}, _off(), bar="1H")
    snap = build_snapshot(rule, HorizonState(epoch=("long", 1), opened_wall=_NOW - 3600),
                          _facts())
    assert "exitPriority" not in snap and "exitCommitment" not in snap
    assert snap["exitPendingMaxSeconds"] == 0.0        # 总闸关 ⇒ 上界 0 ⇒ 豁免关闭
    assert snap["exitIntentLapses"] == 0
    assert snap["exitIntentAgeSeconds"] is None


def test_unarmed_rule_projection_is_all_defaults():
    """C-1 一键回退：总闸关 ⇒ 三个投影字段全部回到关闭值。"""
    rule = resolve_horizon_rule({"profile": "intraday_swing"}, _off(), bar="1H")
    assert rule.exit_intent_decay_rounds == 0
    assert rule.exit_pending_max_s == 0.0
    assert rule.exit_priority is None


# ---------------- G-9 ②：枚举镜像的双向覆盖 ----------------
def _ts(name: str) -> str:
    return (_WEB / "lib" / name).read_text(encoding="utf-8")


def test_g9_every_exit_source_is_reachable_in_all_six_mirrors():
    """【R:P0-5】`EXIT_SOURCES` 的每一个值都必须能在六处镜像里查到。

    只查「前端有没有硬编码」是不够的——**漏加一条镜像不会产生硬编码，只会产生一个裸英文码**
    （或让该类在经验图预演选择器里根本选不到，X-9 因此在那一侧无法被查询）。
    """
    acceptance_ts = _ts("aiTradeAcceptance.ts")
    experience_ts = _ts("experience.ts")
    assert "agentOverride" in EXIT_SOURCES
    for source in EXIT_SOURCES:
        assert EXIT_SOURCE_LABELS.get(source), f"后端 acceptance_text 缺 {source}"
        assert VALUE_LABEL_ZH.get(source), f"后端 taxonomy 缺 {source}"
        assert source in AXIS_VALUES[AXIS_EXIT], f"后端 AXIS_VALUES 缺 {source}"
        assert f"{source}:" in acceptance_ts, f"前端 EXIT_SOURCE_LABELS/COLORS 缺 {source}"
        assert f"{source}:" in experience_ts, f"前端 VALUE_LABEL_ZH 缺 {source}"
        assert f'"{source}"' in experience_ts, f"前端 AXIS_VALUES.exit 缺 {source}"


def test_g9_every_release_reason_is_reachable_in_both_label_tables():
    """`RELEASE_GATES` 的每一个键都必须能在后端 `_RELEASE_LABEL` 与前端标签表里查到。"""
    from app.ai_auto.hold_horizon_text import _RELEASE_LABEL

    horizon_ts = _ts("aiTradeHorizon.ts")
    for reason in RELEASE_GATES:
        assert _RELEASE_LABEL.get(reason), f"后端 _RELEASE_LABEL 缺 {reason}"
        assert f"{reason}:" in horizon_ts, f"前端 HORIZON_RELEASE_LABEL 缺 {reason}"


def test_g9_frontend_does_not_hardcode_the_backend_thresholds():
    """G-9 ①：呈现层不出现硬编码的档位阈值。

    **必须写 `\\r?\\n`**：本仓库 `core.autocrlf=true`，正则里写死 `\\n` 会在干净检出上恒红。
    """
    src = _ts("aiTradeExit.ts")
    code = "\n".join(line for line in re.split(r"\r?\n", src)
                     if not re.match(r"^\s*(\*|//|/\*)", line))
    for magic in ("overrideRounds: 2", "maxOverridesPerSession: 6", "60.0", "300.0"):
        assert magic not in code, magic


# ---------------- G-5 ①：AST 扫描（X-1 的第一层守门） ----------------
def _open_action_literals(path: Path) -> list[str]:
    """AST 扫描：该模块里**真的作为代码字面量**出现的 `open_long` / `open_short`。

    刻意走 AST 而不是正则：注释与 docstring 里大量出现这两个词（本轮的不变式讨论就写在那里），
    按文本扫描会得到一个只能靠「别写文档」通过的假门禁。
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return [node.value for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
            and node.value in ("open_long", "open_short")]


def test_g5_pure_cores_never_use_open_actions_as_a_release_predicate():
    """G-5 ①：三个新纯核心里 `open_long` / `open_short` **只允许**出现在「与离场矛盾」的判定里。

    ①单独是不够的（泄漏点在 `hold_horizon` 的接线上，`exit_priority.py` 里确实一个 `open_long`
    都没有），故 `test_ai_trade_exit_priority.py` 另有②**行为断言**；两条缺一不可。
    """
    root = Path(__file__).resolve().parents[1] / "app" / "ai_auto"
    for name in ("exit_priority.py", "exit_commitment.py"):
        assert _open_action_literals(root / name) == [], name
    # `exit_intent` 里唯一允许出现的位置：`contradicts_exit` 的同向加仓判定（**清零**，不是放行）。
    intent = ast.parse((root / "exit_intent.py").read_text(encoding="utf-8"))
    owners = {node.name for node in ast.walk(intent)
              if isinstance(node, ast.FunctionDef)
              and any(isinstance(sub, ast.Constant) and sub.value in ("open_long", "open_short")
                      for sub in ast.walk(node))}
    assert owners == {"contradicts_exit"}


# ---------------- A-8：后端下发的**UI 文案**里不得出现字面 Markdown 粗体 ----------------
def _ui_texts() -> list[str]:
    """本轮新增的、**会被 JSX 直接渲染**的后端文案（prompt 文案不在此列——那是喂模型的）。"""
    from app.ai_auto.acceptance_text import exit_override_diagnostics
    from app.ai_auto.exit_priority import profiles_block

    block = profiles_block(Settings())
    texts = [block["note"]]
    texts += [level["label"] for level in block["levels"]]
    texts += [level["detail"] for level in block["levels"]]
    texts += exit_override_diagnostics({"count": 3, "netUsd": -12.5, "winRate": 0.33})
    return texts


def test_a8_no_literal_markdown_bold_in_any_ui_facing_string():
    """A-8：后端文案里的 `**` 在 JSX 里会**原样显示成星号**（本仓库已复发两次）。

    prompt 文案（宪章 / 每轮只读段 / 反思段）刻意不在扫描范围内：那些字符串喂的是模型，Markdown
    强调在那里是有效的语义提示，而它们永远不会进 JSX。
    """
    for text in _ui_texts():
        assert "**" not in text, text
