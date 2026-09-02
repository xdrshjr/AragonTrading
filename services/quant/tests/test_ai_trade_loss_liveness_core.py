"""亏损根因与活性加固的**纯核心 + 门禁**用例（ai-trade-loss-and-liveness-hardening §八）。

本文件只测「不需要起会话就能判红」的那一半：四条轴的纯函数、三张镜像表的等值门禁、11 个新设置
的 `.env.example` 双向对齐，以及两条源码正则扫描。会话 / API 级的编排用例在
`test_ai_trade_loss_liveness_session.py`。

分文件的理由与本仓库既有做法一致（CLAUDE.md 800 行红线），不是随手拆的。
"""
from __future__ import annotations

import pathlib
import re

import pytest

from app.ai_auto import liveness as L
from app.ai_auto.attribution import exit_discretion_verdict
from app.ai_auto.discipline import inert_thresholds, preset_table, unguarded_horizon
from app.ai_auto.exit_attribution import (
    DETERMINISTIC_SOURCES,
    DISCRETIONARY_SOURCES,
    EXIT_SOURCES,
    attach_exit_discretion,
)
from app.ai_auto.hold_horizon import (
    GATE_EXIT_CONFIRM,
    GATE_HYSTERESIS,
    GATE_MIN_HOLD,
    RELEASE_GATES,
    RELEASE_LOSS,
    RELEASE_LOSS_COVERED,
    HorizonFacts,
    HorizonState,
    build_snapshot,
    release_reason_for,
    resolve_horizon_rule,
    round_is_structurally_idle,
)
from app.ai_auto.wake_runtime import coherent_wake_floor_s
from app.config import Settings

_ROOT = pathlib.Path(__file__).resolve().parents[3]
_WEB = _ROOT / "apps" / "web"


def _ts(name: str) -> str:
    return (_WEB / "lib" / name).read_text(encoding="utf-8")


# =========================== 轴 A（利润侧确定性离场） ===========================
class _Horizon:
    def __init__(self, min_hold_s: float) -> None:
        self.min_hold_s = min_hold_s


def test_g_a9_unguarded_horizon_truth_table_is_frozen():
    """`G-A9`：致命组合判据逐格。**唯一真源**，端点与用例都只调它。"""
    guard = object()
    assert unguarded_horizon(None, None, enabled=True) is False        # 两者都未装配
    assert unguarded_horizon(None, _Horizon(0.0), enabled=True) is False   # 周期装了但没锁
    assert unguarded_horizon(None, _Horizon(7200.0), enabled=True) is True  # ← 致命组合
    assert unguarded_horizon(guard, _Horizon(7200.0), enabled=True) is False
    # 总闸关 ⇒ **恒 False**（判据整段不执行，行为逐字节回退）
    assert unguarded_horizon(None, _Horizon(7200.0), enabled=False) is False


def test_g_a6_inert_thresholds_names_only_the_ones_whose_consumer_is_absent():
    """`G-A6`：保护层未装配 ⇒ 恰含硬止盈那一条；装配 ⇒ 空。"""
    table = preset_table("balanced")
    assert "ai_trade_profit_guard_hard_tp_return_pct" in table, "预设表变了，本用例的前提失效"
    absent = inert_thresholds(table, profit_rule=None, sizing_rule=None, horizon_rule=None)
    assert absent == ["ai_trade_profit_guard_hard_tp_return_pct"]
    armed = inert_thresholds(table, profit_rule=object(), sizing_rule=None, horizon_rule=None)
    assert armed == []
    # 预设本身没有这一行（如 `off`）⇒ 空，绝不凭空造出一个不存在的键。
    assert inert_thresholds(preset_table("off"), profit_rule=None,
                            sizing_rule=None, horizon_rule=None) == []


def test_g_a6b_frontend_inert_predicate_mirrors_the_backend_one():
    """前端启动弹窗在**按下启动键之前**现算的那一份 `inertKeys` 必须与后端判据同构。

    启动之前服务端还没被问过，故前端必须本地算一次；两侧一旦分叉，用户看到的删除线就会与真正
    失效的那一行不是同一行。**判据同构由本用例钉死**（源码扫描：键名逐字一致）。
    """
    modal = (_WEB / "components" / "Agent" / "AiTradeLaunchModal.tsx").read_text(encoding="utf-8")
    assert "const inertThresholdKeys = profitGuardEnabled ? []" in modal
    assert '"ai_trade_profit_guard_hard_tp_return_pct"' in modal


def test_g_a5_profit_guard_fields_hardcodes_no_threshold_number():
    """`G-A5`：后果清单里的数值一律来自 props / 后端下发，`ProfitGuardFields.tsx` **零阈值字面量**。

    判据只看**表达式与文本节点**，不看 `className`（Tailwind 的 `gap-0.5` / `py-1.5` 不是阈值）。
    """
    src = (_WEB / "components" / "Agent" / "ProfitGuardFields.tsx").read_text(encoding="utf-8")
    stripped = re.sub(r'className="[^"]*"', "", src)
    for literal in ("0.5", "50%", "35%", "28%", "20%", "180"):
        assert literal not in stripped, f"ProfitGuardFields.tsx 里出现了阈值字面量 {literal}"


# =========================== 轴 B（亏损侧豁免收窄） ===========================
def _facts(**over) -> HorizonFacts:
    base = dict(seq=1, side="long", entry_px=30_000.0, mark_px=27_000.0, leverage=3.0,
                now_wall=1_700_000_000.0, px_age_s=1.0, stale_feed=False,
                bars_in_position=1, max_hold_bars=None, stop_loss_pct=0.05,
                cost_bps=10.0, exchange_sl_px=None, exchange_tp_px=None)
    base.update(over)
    return HorizonFacts(**base)


def _horizon_rule() -> object:
    return resolve_horizon_rule({"profile": "intraday_swing"}, Settings(), bar="1H")


def _state() -> HorizonState:
    return HorizonState(epoch=("long", 1), opened_wall=1_700_000_000.0 - 600.0)


@pytest.mark.parametrize("covered,expected", [
    (True, RELEASE_LOSS_COVERED),      # G-B1：已核实覆盖 ⇒ 降档
    (False, RELEASE_LOSS),             # G-B2：未覆盖 ⇒ 逐字等于今日
    (None, RELEASE_LOSS),              # G-B3：缺省（回测 / 未装配 / 总闸关）⇒ 同 G-B2
])
def test_g_b1_b3_loss_release_splits_by_backstop_coverage(covered, expected):
    rule, state, facts = _horizon_rule(), _state(), _facts()
    # 浮亏 −30%（保证金口径）远超解除阈（5% × 3 × 0.5 = 7.5%）
    reason = release_reason_for(rule, state, facts, return_pct=-0.30,
                                backstop_covered=covered)
    assert reason == expected


def test_g_b1_covered_release_keeps_g1_and_frees_only_g2_g3():
    """`G-B1` 的另一半：解闸集合恰是 `G2+G3`——**G1 仍然生效**（那正是本轮要收窄的东西）。"""
    assert RELEASE_GATES[RELEASE_LOSS_COVERED] == (GATE_EXIT_CONFIRM, GATE_HYSTERESIS)
    assert GATE_MIN_HOLD not in RELEASE_GATES[RELEASE_LOSS_COVERED]
    # 既有四行**逐字未动**（§十一.8：不改冻结表的既有行、不调整豁免的相对顺序）
    assert RELEASE_GATES[RELEASE_LOSS] == (GATE_MIN_HOLD, GATE_EXIT_CONFIRM, GATE_HYSTERESIS)


def test_g_b3_build_snapshot_default_is_the_zero_regression_anchor():
    """缺省形参就是零回归的结构性支点：回测（不传该键）落在这一格。"""
    rule, state, facts = _horizon_rule(), _state(), _facts()
    snap = build_snapshot(rule, state, facts)
    assert snap["releaseReason"] == RELEASE_LOSS
    assert snap["releasedGates"] == [GATE_MIN_HOLD, GATE_EXIT_CONFIRM, GATE_HYSTERESIS]


def test_g_b6_release_label_tables_are_equal_on_both_ends():
    """`G-B6`：后端 `_RELEASE_LABEL` 与前端 `HORIZON_RELEASE_LABEL` 的豁免码集合**等值**。

    v1 比的是 `lib/aiTrade.ts`（那是 `DEGRADE_REASON_LABELS`，另一张表，比了也白比）。
    漏改不报错——`HORIZON_RELEASE_LABEL[…] ?? "限制已解除"` 会把原因静默吃掉。
    """
    from app.ai_auto.hold_horizon_text import _RELEASE_LABEL

    horizon_ts = _ts("aiTradeHorizon.ts")
    block = horizon_ts.split("const HORIZON_RELEASE_LABEL", 1)[1].split("};", 1)[0]
    front = set(re.findall(r"^\s{2}([a-z_]+):", block, re.M))
    assert front == set(_RELEASE_LABEL), f"两张表不等值：后端-前端={set(_RELEASE_LABEL) - front}"
    assert RELEASE_LOSS_COVERED in front


def test_g_b6b_covered_label_says_g1_is_still_on():
    """新码的文案**必须写明「不解最短持仓」**：只说「限制已解除」是一句会误导的话。"""
    from app.ai_auto.hold_horizon_text import _RELEASE_LABEL

    assert "不解最短持仓" in _RELEASE_LABEL[RELEASE_LOSS_COVERED]
    assert "不解最短持仓" in _ts("aiTradeHorizon.ts")


# =========================== 轴 C（节奏相干性） ===========================
def test_g_c1_c2_coherent_wake_floor():
    """`G-C1` / `G-C2`：divisor=24 + 目标 4 小时 ⇒ 600s；divisor=0（默认）⇒ 恒 0（不约束）。"""
    rule = _horizon_rule()
    assert rule.target_hold_min_s == 14400.0
    assert coherent_wake_floor_s(rule, divisor=24) == 600.0
    assert coherent_wake_floor_s(rule, divisor=0) == 0.0        # 本轮默认：只观测不改行为
    assert coherent_wake_floor_s(None, divisor=24) == 0.0       # 周期未装配 ⇒ 不约束


def test_g_c3_skip_round_predicate_needs_all_four_conditions():
    """`G-C3`：四个条件全真才跳；**逐条枚举四个反例**（任一为假 ⇒ 照常调用 LLM）。"""
    holding = {"status": "holding", "releasedGates": [], "minHoldRemainingSeconds": 900.0}
    assert round_is_structurally_idle(holding, wake_kind="heartbeat", guard_armed=False) is True
    # ① 空仓
    assert round_is_structurally_idle(
        {**holding, "status": "flat"}, wake_kind="heartbeat", guard_armed=False) is False
    # ② 已有闸被豁免解除 / 最短持仓已满
    assert round_is_structurally_idle(
        {**holding, "releasedGates": ["G1"]}, wake_kind="heartbeat", guard_armed=False) is False
    assert round_is_structurally_idle(
        {**holding, "minHoldRemainingSeconds": 0.0},
        wake_kind="heartbeat", guard_armed=False) is False
    # ③ 外部事件驱动的唤醒（安全内核）
    for kind in ("intel_alert", "signal_flip", "price_breakout", "candle_close", "resting_order"):
        assert round_is_structurally_idle(holding, wake_kind=kind, guard_armed=False) is False
    # ④ 利润保护已武装（安全内核）
    assert round_is_structurally_idle(holding, wake_kind="heartbeat", guard_armed=True) is False
    # 未装配周期模式 ⇒ 恒 False
    assert round_is_structurally_idle(None, wake_kind="heartbeat", guard_armed=False) is False


# =========================== 轴 D（活性看门狗） ===========================
def test_probe_is_independent_of_what_the_model_said():
    """四条码全部取自结构性事实；能开仓 ⇒ `None`（「有机会但我不想开」一条都不落）。"""
    ok = dict(ma_band_armed=True, ma_band={"zone": "mid"}, mark_px=30_000.0,
              stale_feed=False, llm_failed=False)
    assert L.probe(**ok) is None
    assert L.probe(**{**ok, "ma_band": None}) == L.BLOCK_MA_BAND
    # 铁律未装配 ⇒ 通道算不出来也不阻塞开仓
    assert L.probe(**{**ok, "ma_band": None, "ma_band_armed": False}) is None
    assert L.probe(**{**ok, "mark_px": 0.0}) == L.BLOCK_NO_PX
    assert L.probe(**{**ok, "mark_px": float("nan")}) == L.BLOCK_NO_PX
    assert L.probe(**{**ok, "stale_feed": True}) == L.BLOCK_FEED
    assert L.probe(**{**ok, "llm_failed": True}) == L.BLOCK_LLM
    # 顺序即优先级：通道算不出来时恒报第一条
    assert L.probe(**{**ok, "ma_band": None, "stale_feed": True}) == L.BLOCK_MA_BAND


def test_g_d3_alternating_codes_never_accumulate_a_streak():
    """`G-D3`：阻塞码在两个值之间交替 ⇒ `streak` 恒 ≤ 1，永不告警。"""
    state = L.LivenessState()
    rule = L.LivenessRule(warn_rounds=2, halt_rounds=3)
    for i in range(20):
        code = L.BLOCK_MA_BAND if i % 2 == 0 else L.BLOCK_FEED
        state = L.advance(state, blocking_code=code, now_wall=1000.0 + i)
        assert state.streak <= 1
        assert L.verdict(state, rule)[0] == L.LEVEL_OK


def test_verdict_ladder_and_zero_means_off():
    state = L.LivenessState(code=L.BLOCK_MA_BAND, streak=20, first_wall=1.0)
    assert L.verdict(state, L.LivenessRule(20, 60))[0] == L.LEVEL_DEGRADED
    assert L.verdict(L.LivenessState(code=L.BLOCK_MA_BAND, streak=60),
                     L.LivenessRule(20, 60))[0] == L.LEVEL_HALT
    # `0 = 关`（本仓库既有惯例）：两档都关 ⇒ 恒 ok
    assert L.verdict(state, L.LivenessRule(0, 0))[0] == L.LEVEL_OK
    # 未装配 ⇒ 恒 ok
    assert L.verdict(state, None) == (L.LEVEL_OK, None)


def test_g_d2_any_fill_resets_the_streak_but_only_while_armed():
    """`G-D2`：任意一次成交 ⇒ 归零。**总闸关时不清零**——那时它只承载既有 band 计数的语义。"""
    armed = L.LivenessRuntime(L.LivenessRule(2, 3))
    for i in range(5):
        armed.advance_round(ma_band_armed=True, ma_band=None, mark_px=1.0,
                            stale_feed=False, llm_failed=False, now_wall=1000.0 + i)
    assert armed.state.streak == 5 and armed.band_streak == 5
    armed.note_fill()
    assert armed.state.streak == 0 and armed.band_streak == 0

    off = L.LivenessRuntime(None)
    for i in range(5):
        off.advance_round(ma_band_armed=True, ma_band=None, mark_px=1.0,
                          stale_feed=False, llm_failed=False, now_wall=1000.0 + i)
    off.note_fill()
    assert off.band_streak == 5, "总闸关时 note_fill 绝不清零（G-D5 的逐字节回退支点）"


def test_band_streak_has_exactly_one_owner():
    """`P1-1` / 放行条件 3：`_band_unavailable_streak` **由看门狗持有**，不新建第二份计数。"""
    src = (_ROOT / "services" / "quant" / "app" / "ai_auto" / "session.py").read_text(
        encoding="utf-8")
    assert "self._band_unavailable_streak = 0" not in src, "会话里又出现了第二份 band 计数"
    assert "return self._liveness.band_streak" in src


def test_g_d10_blocking_codes_have_no_parameter_suffix_and_a_unique_producer():
    """`G-D10`：四条码**不含 `(` 参数后缀**，且每一条在后端源码里都有唯一产出点。

    带参数的码（`base_rule_ma_band(pctB=0.42)`）按等值比永不命中、按原样比则每轮换码 ⇒ streak 恒 1
    ——「同码连续」这条判据在那种码上根本不成立。注意 `core.autocrlf=true`，正则一律写 `\\r?\\n`。
    """
    src = (_ROOT / "services" / "quant" / "app" / "ai_auto" / "liveness.py").read_text(
        encoding="utf-8")
    for code in L.BLOCKING_CODES:
        assert "(" not in code and ")" not in code, f"{code} 带参数后缀"
        # 常量行后面跟着行内注释，故只锚定「行首 + 赋值 + 该字面量」这一段。
        hits = re.findall(rf'^BLOCK_[A-Z_]+ = "{re.escape(code)}"', src, re.M)
        assert len(hits) == 1, f"{code} 的常量定义不是唯一的：{hits}"


def test_g_d9_pause_reason_text_is_equal_across_the_four_mirrors():
    """`G-D9`：暂停原因四镜像等值。

    此闸**合入前就是红的**——`resume_rollback` 在前端文案表里缺失（评审 P2-6），本轮顺手补齐。
    """
    from app.ai_auto.pause import PAUSE_REASON_LIVENESS, PAUSE_REASON_TEXT

    types_ts = _ts("aiTradeTypes.ts")
    union_block = types_ts.split("export type AiTradePauseReason =", 1)[1].split(";", 1)[0]
    union = set(re.findall(r'\|\s*"([a-z_]+)"', union_block))
    trade_ts = _ts("aiTrade.ts")
    text_block = trade_ts.split("export const AI_TRADE_PAUSE_REASON_TEXT", 1)[1].split("};", 1)[0]
    front_text = set(re.findall(r"^\s{2}([a-z_]+):", text_block, re.M))
    backend = set(PAUSE_REASON_TEXT)
    assert backend == union == front_text, (
        f"四镜像不等值：backend-union={backend - union}，backend-text={backend - front_text}")
    assert PAUSE_REASON_LIVENESS in front_text and "resume_rollback" in front_text


def test_liveness_message_says_the_whole_group_will_be_paused():
    """§九 R11 ②：`halt` 文案**必须逐字说明将暂停整组**——不让用户事后才发现另外两个也停了。"""
    from app.ai_auto.liveness_text import wire as liveness_wire

    payload = liveness_wire(L.LEVEL_HALT, L.BLOCK_MA_BAND, 60, since_wall=1_700_000_000.0)
    assert "整个分组" in payload["message"]
    assert "60" in payload["message"] and payload["advice"]
    # 本模块下发的每一句都会被 `AiTradeLivenessBanner` 原样塞进 JSX 文本节点，那里**没有任何
    # markdown 渲染**：写 `**整个分组**` 用户看到的就是两侧带星号的字面量（仓库第三次踩这个坑）。
    for text in (payload["message"], payload["advice"]):
        assert "**" not in text, f"下发文案里出现了 markdown 强调：{text}"
    # `sinceWall<=0` ⇒ **不编一个起始时间**（编出来用户会照它去翻日志然后什么都找不到）
    quiet = liveness_wire(L.LEVEL_DEGRADED, L.BLOCK_FEED, 20, since_wall=0.0)
    assert "起于" not in quiet["message"]


# =========================== 轴 E / F（归因完整性与结论） ===========================
def test_g_e11_the_five_existing_short_codes_are_untouched():
    """`G-E11`：五个既有机器短码逐字不变，且 `_BACKTEST_REASONS` 的六个键同样逐字不变。

    把它们统一改写成 `EXIT_SOURCES` 取值 ⇒ 中文标签当场失配、回测归因判据当场失灵，
    **且两处都不会报错**（评审 P0-5）。
    """
    from app.ai_auto.exit_attribution import _BACKTEST_REASONS, is_deterministic_reason

    assert set(_BACKTEST_REASONS) == {"stop_loss", "take_profit", "max_hold",
                                      "session_take_profit", "session_stop_loss", "session_end"}
    for reason in _BACKTEST_REASONS:
        assert is_deterministic_reason(reason) is True
    # 五个既有短码的**写点**在本轮之后逐字未变：E-1 只改 LLM 决策那一条通路
    # （`session._DECISION_REASON_CODE` 的三个动作），执行器与保护层的短码一个都没碰。
    executor = (_ROOT / "services" / "quant" / "app" / "live_auto" / "executor.py").read_text(
        encoding="utf-8")
    assert 'reason="external_close"' in executor
    session_src = (_ROOT / "services" / "quant" / "app" / "ai_auto" / "session.py").read_text(
        encoding="utf-8")
    assert '"session_take_profit"' in session_src and '"session_stop_loss"' in session_src
    # 其中两个在前端标签表里有条目 ⇒ 前缀命中必须仍然成立
    labels = _ts("aiTrade.ts")
    for code in ("profit_guard_committed_exit", "profit_guard_trailing"):
        assert f'prefix: "{code}"' in labels, f"既有短码 {code} 掉出了前端标签表"


def test_e1_machine_codes_have_a_frontend_label():
    """E-1：三个新机器码必须在 `DEGRADE_REASON_LABELS` 里有条目——漏加只会显示裸英文码。"""
    labels = _ts("aiTrade.ts")
    for code in ("agent_close", "open_long", "open_short"):
        assert f'prefix: "{code}"' in labels, f"前端标签表缺 {code}"
    for code in L.BLOCKING_CODES:
        assert f'prefix: "{code}"' in labels, f"前端标签表缺阻塞码 {code}"


def test_e2_reason_column_is_wide_enough_and_truncation_is_loud():
    """E-2：列宽 128 + 按**字符**截断（绝不切碎半个汉字）+ 截断时告警一次。"""
    from app.db.repositories import TRADE_REASON_MAX_CHARS, _fit_reason

    assert TRADE_REASON_MAX_CHARS == 128
    text = "中" * 200
    fitted = _fit_reason(text)
    assert len(fitted) == 128 and fitted == "中" * 128     # 按字符切，不产生半个汉字
    assert _fit_reason("短") == "短"
    models = (_ROOT / "services" / "quant" / "app" / "db" / "models.py").read_text(
        encoding="utf-8")
    assert "reason: Mapped[str] = mapped_column(String(128)" in models


@pytest.mark.parametrize("agent_net,best_net,min_samples,level", [
    (-67.52, 14.14, 20, "adverse"),        # 自主为负、确定性为正 ⇒ 结论成立
    (-67.52, -1.0, 20, "neutral"),         # 没有任何正的确定性来源 ⇒ 不下结论
    (12.0, 14.14, 20, "neutral"),          # 自主本身为正 ⇒ 不下结论
])
def test_g_f1_exit_discretion_levels(agent_net, best_net, min_samples, level):
    rows = [{"source": "agent", "count": 36, "netUsd": agent_net},
            {"source": "profitGuard", "count": 7, "netUsd": best_net}]
    out = exit_discretion_verdict(rows, min_samples=min_samples,
                                  discretionary=DISCRETIONARY_SOURCES,
                                  deterministic=DETERMINISTIC_SOURCES)
    assert out["level"] == level


def test_g_f1_insufficient_samples_says_so_verbatim():
    """`G-F1`：样本不足 ⇒ `insufficient` 且文案如实写「样本 M/N，结论未生效」。"""
    rows = [{"source": "agent", "count": 12, "netUsd": -5.0},
            {"source": "profitGuard", "count": 3, "netUsd": 4.0}]
    out = exit_discretion_verdict(rows, min_samples=20,
                                  discretionary=DISCRETIONARY_SOURCES,
                                  deterministic=DETERMINISTIC_SOURCES)
    assert out["level"] == "insufficient"
    assert out["note"] == "样本 12/20，结论未生效。"


def test_exit_discretion_note_uses_the_chinese_label_not_a_bare_enum():
    """结论是**给人读的**：`note` 里绝不能出现 `profitGuard` 这种裸英文枚举。

    标签的唯一真源是 `acceptance_text.EXIT_SOURCE_LABELS`（前后端六处镜像的那一份），
    由 `attach_exit_discretion` 喂给纯函数——判定与措辞因此永远指向同一张表。
    """
    exits = {"totalExits": 43, "bySource": [
        {"source": "agent", "count": 36, "netUsd": -67.524},
        {"source": "profitGuard", "count": 7, "netUsd": 14.136}]}
    out = attach_exit_discretion(exits, min_samples=20)["exitDiscretion"]
    assert out["level"] == "adverse"
    assert "利润保护层" in out["note"] and "profitGuard" not in out["note"]
    assert out["bestDeterministic"]["label"] == "利润保护层"


def test_exit_discretion_min_samples_zero_means_the_key_is_absent():
    """`0` ⇒ 恒不出结论 ⇒ **整键缺席**（不是 null）——这是轴 F 的逐字回退支点。"""
    exits = {"totalExits": 43, "bySource": [{"source": "agent", "count": 36, "netUsd": -67.5}]}
    assert exit_discretion_verdict(exits["bySource"], min_samples=0,
                                   discretionary=DISCRETIONARY_SOURCES,
                                   deterministic=DETERMINISTIC_SOURCES) is None
    assert attach_exit_discretion(exits, min_samples=0) == exits
    assert "exitDiscretion" in attach_exit_discretion(exits, min_samples=20)
    assert attach_exit_discretion(None, min_samples=20) is None


def test_discretionary_and_deterministic_sets_are_derived_not_hand_copied():
    """两个子集必须**从 `EXIT_SOURCES` 派生**：手抄第二遍必然在下一次新增取值时分叉。"""
    assert set(DISCRETIONARY_SOURCES) | set(DETERMINISTIC_SOURCES) | {"unknown"} == set(
        EXIT_SOURCES)
    assert not (set(DISCRETIONARY_SOURCES) & set(DETERMINISTIC_SOURCES))
    assert "unknown" not in DISCRETIONARY_SOURCES and "unknown" not in DETERMINISTIC_SOURCES


def test_g_f2_verdict_copy_never_claims_it_was_calibrated():
    """`G-F2`：结论文案不得出现「已校准」「经回测验证」——本轮一个阈值都没有被回测校准过。

    注意 `core.autocrlf=true`，正则一律写 `\\r?\\n`。
    """
    src = (_ROOT / "services" / "quant" / "app" / "ai_auto" / "attribution.py").read_text(
        encoding="utf-8")
    body = src.split("def exit_discretion_verdict", 1)[1]
    for banned in ("已校准", "经回测验证"):
        assert not re.search(rf'"[^"\r\n]*{banned}', body), f"结论文案里出现了「{banned}」"
    rows = [{"source": "agent", "count": 36, "netUsd": -67.52},
            {"source": "profitGuard", "count": 7, "netUsd": 14.14}]
    note = exit_discretion_verdict(rows, min_samples=20, discretionary=DISCRETIONARY_SOURCES,
                                   deterministic=DETERMINISTIC_SOURCES)["note"]
    assert "已校准" not in note and "经回测验证" not in note
    assert "36" in note and "7" in note        # 结论必须是**数据化**的


# =========================== 门禁：11 个新设置的双向登记 ===========================
_NEW_SETTINGS = (
    "ai_trade_require_profit_exit_with_horizon",
    "ai_trade_loss_release_requires_uncovered",
    "ai_trade_liveness_enabled",
    "ai_trade_liveness_warn_rounds",
    "ai_trade_liveness_halt_rounds",
    "ai_trade_wake_horizon_divisor",
    "ai_trade_skip_round_when_gated",
    "ai_trade_peak_tracking_always",
    "ai_trade_exit_verdict_min_samples",
    "ai_trade_exit_discretion_autotune_enabled",
    "ai_trade_sizing_margin_precheck",
)


def test_g_env_every_new_setting_is_registered_in_env_example():
    """`G-Env`：11 个新键在 `Settings` 与**仓库根目录**的 `.env.example` 双向对齐。

    照抄 `test_ai_intel_wiring.py:480` 的写法。**漏登记不会报错**——prod 的 PyInstaller 后端不读
    `.env`、只读进程 env，而桌面壳按 `AI_TRADE_` 前缀透传：一个没写进 `.env.example` 的旋钮，
    用户在打包形态下没有任何途径知道它存在。本仓库已为此栽过两次。
    """
    env = (_ROOT / ".env.example").read_text(encoding="utf-8")
    declared = set(re.findall(r"^(AI_TRADE_[A-Z0-9_]+)=", env, re.M))
    settings = Settings()
    for name in _NEW_SETTINGS:
        assert hasattr(settings, name), f"`Settings` 上没有 {name}"
        assert name.upper() in declared, f"{name.upper()} 没写进 .env.example"


def test_new_settings_defaults_are_the_documented_ones():
    """默认值即「本轮承诺的行为」：三条行为轴默认开，两条待校准的旋钮默认关。"""
    s = Settings()
    assert s.ai_trade_require_profit_exit_with_horizon is True
    assert s.ai_trade_loss_release_requires_uncovered is True
    assert s.ai_trade_liveness_enabled is True
    assert (s.ai_trade_liveness_warn_rounds, s.ai_trade_liveness_halt_rounds) == (20, 60)
    assert s.ai_trade_peak_tracking_always is True
    assert s.ai_trade_exit_verdict_min_samples == 20
    # 三个「只建不开」：本轮不改行为，下一轮按验收卡定值
    assert s.ai_trade_wake_horizon_divisor == 0
    assert s.ai_trade_skip_round_when_gated is False
    assert s.ai_trade_exit_discretion_autotune_enabled is False
    assert s.ai_trade_sizing_margin_precheck is False


def test_audit_marker_is_registered_and_excluded_by_default():
    """`R10`：`audit` marker 两处都要改——只加 markers 会让 A0 进全量跑，只改 addopts 会告警。"""
    pyproject = (_ROOT / "services" / "quant" / "pyproject.toml").read_text(encoding="utf-8")
    assert "not network and not audit" in pyproject
    assert re.search(r'^\s*"audit: ', pyproject, re.M)


# =========================== A0：证据复现脚本（opt-in，默认排除） ===========================
@pytest.mark.audit
def test_a0_audit_script_reproduces_the_evidence_baseline():
    """`A0`：在**真实生产库**上重跑 §二 的读数并断言关键数字。

    默认由 `addopts` 排除（`-m 'not network and not audit'`）——它读的是用户的 119 MB 生产库。
    本机没有那个库 ⇒ `skip`（它不是一条会在别人机器上凭空判红的用例）。
    opt-in：`pytest -m audit`。
    """
    import sys

    sys.path.insert(0, str(_ROOT / "services" / "quant"))
    from scripts.audit_ai_trade_pnl import default_db_path, main

    if not default_db_path().exists():
        pytest.skip(f"本机没有生产库：{default_db_path()}")
    assert main(["--skip-log", "--assert"]) == 0


def test_a0_pairing_rule_is_a_named_function_not_scattered_sql():
    """`R12`：配对规则必须是脚本里一个**具名函数**，日志签名必须是脚本里的**具名常量**。

    v1 的日志计数只写了数字没写匹配式，于是评审复算得到的是另一组数——两个数都不错，
    错的是「口径没写下来」，下一轮无法复现同一个数。
    """
    src = (_ROOT / "services" / "quant" / "scripts" / "audit_ai_trade_pnl.py").read_text(
        encoding="utf-8")
    assert "def pair_round_trips(" in src
    assert "LOG_SIGNATURES: dict[str, str] = {" in src
    assert "mode=ro" in src, "生产库必须**只读**打开"


def test_e8_margin_precheck_knob_has_exactly_one_reader_and_tells_the_truth():
    """E-8：`ai_trade_sizing_margin_precheck` **本轮只建不实现**，故它必须自己说出这件事。

    一个「置 true 也什么都不会发生」的开关如果不说出来，就是本仓库明令禁止的「显示了但不作数」
    ——这正是本轮 D2 要根除的那个形态，绝不能在同一轮里自己再造一个。
    """
    from app.ai_auto.sizing_runtime import margin_precheck_notice

    assert margin_precheck_notice(Settings()) is None, "默认关 ⇒ 回执逐字不变"
    notice = margin_precheck_notice(Settings(ai_trade_sizing_margin_precheck=True))
    assert notice is not None and "尚未实现" in notice["reason"]
    assert "不改变任何一次真实" in notice["reason"]
    # `.env.example` 与 `config.py` 的说明同样不得声称它会降档
    env = (_ROOT / ".env.example").read_text(encoding="utf-8")
    block = env.split("AI_TRADE_SIZING_MARGIN_PRECHECK", 1)[0].rsplit("# E-8", 1)[-1]
    assert "只建不实现" in block
