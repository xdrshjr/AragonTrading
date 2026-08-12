"""纪律预设的会话级贯通（轴 A/B/C；判据 G-1 / G-2 / G-7 / G-7b / G-10 / G-13 / G-14 / G-16）。

纯函数单测看不见的四件事：
1. 派生副本真的把仓位 / 止损 / 刹车三层**装配起来了**（G-1），而不是只改了几个数字；
2. `status.disciplines` 这条呈现通道真的有数（后端算了但用户看不见 = 死数据）；
3. 裸仓三态判据按 `AiTradeBackstopWire` 的**实名字段**走，未核实态**绝不报红**（G-7b）；
4. 「一堆闸武装着却一单没开过」会被自检抓住（G-16）——本仓库四次「恒真判据」前科的护栏。
"""
from __future__ import annotations

import pytest

from app.ai_auto.decider import AiTradeDecider
from app.ai_auto.discipline import preset_table
from app.ai_auto.discipline_view import naked_backstop
from app.ai_auto.session import AiTradeSession
from app.ai_auto.sizing_runtime import build_sizing_runtime
from app.config import Settings
from app.db.session import dispose_engine, init_db
from app.engine.simulator import RiskParams
from app.live_auto.executor import LiveExecutor
from app.okx.accounts import AccountCredentials
from app.okx.types import BAR_SECONDS
from app.paper.strategy_factory import PaperStrategySpec
from app.strategies.base import BaseStrategy, StrategyMeta


class _Stub(BaseStrategy):
    meta = StrategyMeta(key="_stub_discipline", name="Stub", description="t",
                        default_params={}, params_schema={})

    def warmup(self) -> int:
        return 1

    def on_candle(self, df, i, ctx):
        return []


def _derived(preset_id: str = "balanced", **over) -> Settings:
    """模拟 API 层产出的会话私有派生副本（`_resolve_discipline` 已由纯核心单测覆盖）。"""
    return Settings(_env_file=None, **{**preset_table(preset_id), **over})


def _session(settings: Settings, *, specs: int = 3, preset_id: str = "balanced",
             budget: float = 1000.0, min_sz: float = 0.01) -> AiTradeSession:
    executor = LiveExecutor(
        creds=AccountCredentials("ak", "sk-000000", "pp-000000"), account_id=1,
        inst_id="BTC-USDT-SWAP", td_mode="cross", pos_mode="long_short_mode",
        leverage=3.0, budget_usd=budget, risk=RiskParams(), ct_val=0.01, lot_sz=0.01,
        min_sz=min_sz, settings=settings, bar_seconds=BAR_SECONDS["1H"])
    decider = AiTradeDecider(object(), "sys", context_refresh_rounds=50, settings=settings)
    return AiTradeSession(
        "aitrade_disc", 1,
        [PaperStrategySpec(i + 1, f"S{i}", "atomic", _Stub({}), {}) for i in range(specs)],
        "BTC-USDT-SWAP", "1H", executor, decider, priming=False, priming_bars=10,
        context_refresh_rounds=50,
        sizing=build_sizing_runtime(settings, "BTC-USDT-SWAP", BAR_SECONDS["1H"],
                                    budget_usd=budget),
        discipline_preset=preset_id)


def _rows(session: AiTradeSession) -> dict[str, dict]:
    return {row["key"]: row for row in session.discipline_wire()["rows"]}


# ============================ G-1：装配后四条闸真的武装 ============================
async def test_balanced_preset_arms_the_four_profit_critical_gates():
    await init_db()
    try:
        session = _session(_derived("balanced"))
        assert session._sizing.armed is True
        rows = _rows(session)
        for key in ("volSizing", "atrStop", "drawdownBrake", "lossStreak"):
            assert rows[key]["armed"] is True, key
            assert rows[key]["threshold"], f"{key} 武装了却报不出阈值 = 用户看不见"
        assert rows["edgeGate"]["armed"] is True
        assert rows["exposureCap"]["armed"] is True
    finally:
        await dispose_engine()


async def test_off_preset_leaves_every_dark_switch_dark():
    """`off` ⇒ 逐字回退到本特性合入前：四条闸全灭，且**每一行都说得出为什么**。"""
    await init_db()
    try:
        rows = _rows(_session(Settings(_env_file=None), preset_id="off"))
        for key in ("volSizing", "atrStop", "drawdownBrake", "lossStreak", "edgeGate",
                    "exposureCap"):
            assert rows[key]["armed"] is False, key
            assert rows[key]["note"], f"{key} 未生效却没说原因"
    finally:
        await dispose_engine()


# ============================ G-10：13 行齐、armed=false 必带 note ============================
async def test_digest_has_thirteen_rows_in_a_fixed_order_and_never_leaves_grey_unexplained():
    await init_db()
    try:
        wire = _session(_derived("balanced")).discipline_wire()
        rows = wire["rows"]
        assert len(rows) == 13
        assert [r["key"] for r in rows][:4] == ["baseRuleMaBand", "consensus", "confidence",
                                                "edgeGate"]
        assert all(r["label"] for r in rows)
        for row in rows:
            if not row["armed"]:
                assert row["note"], f"{row['key']}：armed=false 必须带 note（G-10）"
    finally:
        await dispose_engine()


async def test_signal_prior_row_explains_how_to_turn_it_on():
    """[P2-5]：这一行与其余 12 行不同源，note 必须写「怎么开」而不是只写一个 false。"""
    await init_db()
    try:
        row = _rows(_session(_derived("balanced")))["signalPrior"]
        assert row["armed"] is False
        assert "rebuild" in row["note"] and "SIGNAL_PRIOR_ENABLED" in row["note"]
    finally:
        await dispose_engine()


# ============================ G-3 呈现端：夹紧要自解释 ============================
async def test_clamped_consensus_explains_itself_in_the_digest():
    await init_db()
    try:
        settings = _derived("steady", ai_trade_min_consensus=1)   # 已被 R-1 夹到 1
        row = _rows(_session(settings, specs=1))["consensus"]
        assert row["armed"] is True and row["threshold"] == "≥1 / 共 1 策略"
        assert "夹紧" in (row["note"] or "")
    finally:
        await dispose_engine()


# ============================ G-13：解析顺序（利润保护那一行不是死字母）============================
async def test_profit_guard_threshold_comes_from_the_preset_not_the_config_default():
    """顺序错（`_resolve_discipline` 排在 `_resolve_profit_guard` 之后）时该值恒为 config 默认 0.0，
    且**不报任何错**——故这条断言是那条时序约束在会话侧的唯一护栏。"""
    from app.api.ai_auto import _resolve_discipline, _resolve_profit_guard

    base = Settings(_env_file=None)
    assert base.ai_trade_profit_guard_hard_tp_return_pct == 0.0
    derived, _, _ = _resolve_discipline(None, base, spec_count=3, priming_bars=300)
    correct = _resolve_profit_guard(None, derived)     # 正序：先派生，再解析
    wrong = _resolve_profit_guard(None, base)          # 逆序：预设那一行成为死字母
    assert correct.hard_tp_return_pct == 0.50
    assert wrong.hard_tp_return_pct == 0.0


# ============================ G-7 / G-7b：裸仓三态 ============================
@pytest.mark.parametrize("wire,expected", [
    ({"basis": "exchange", "mismatch": True, "coverageRatio": None}, True),
    ({"basis": "exchange", "mismatch": False, "coverageRatio": 0.0}, True),
    ({"basis": "exchange", "mismatch": False, "coverageRatio": 0.5}, True),
    ({"basis": "exchange", "mismatch": False, "coverageRatio": 1.0}, False),
    ({"basis": "exchange", "mismatch": False, "coverageRatio": None}, False),   # 未核实
    ({"basis": "local", "mismatch": True, "coverageRatio": 0.0}, False),        # 非交易所读数
    ({"basis": "unavailable", "mismatch": False, "coverageRatio": None}, False),
    (None, False),                                                              # 空仓
])
def test_naked_judgement_uses_the_real_wire_field_names(wire, expected):
    """[P0-3]：`coverage` / `status` 这两个键**根本不存在**——照 v1 写法实现，红条永不出现。
    而 `coverageRatio is None` 是「还不知道」，报红会把真红线一起废掉（G-7b）。"""
    assert naked_backstop(wire, 0.999) is expected


async def test_unverified_backstop_row_is_grey_with_a_note_never_red():
    await init_db()
    try:
        session = _session(_derived("balanced"))
        session._backstop.wire = lambda _now: {"basis": "exchange", "mismatch": False,
                                               "coverageRatio": None}
        row = _rows(session)["exchangeBackstop"]
        assert row["armed"] is False and row["tone"] != "danger" and row["note"]
    finally:
        await dispose_engine()


async def test_naked_backstop_row_is_red_and_says_what_to_do():
    await init_db()
    try:
        session = _session(_derived("balanced"))
        session._backstop.wire = lambda _now: {"basis": "exchange", "mismatch": True,
                                               "coverageRatio": 0.0, "coveredSz": 0,
                                               "positionSz": 5}
        row = _rows(session)["exchangeBackstop"]
        assert row["armed"] is False and row["tone"] == "danger"
        assert "补挂" in row["note"]
        assert session._discipline.naked is True
    finally:
        await dispose_engine()


async def test_naked_flip_is_reported_once_per_transition_not_every_tick():
    """轴 C [P0-2]：翻转即补推一次 status；未翻转的 tick 不推（否则 5s 一条噪音）。"""
    await init_db()
    try:
        session = _session(_derived("balanced"))
        covered = {"basis": "exchange", "mismatch": False, "coverageRatio": 1.0}
        naked = {"basis": "exchange", "mismatch": True, "coverageRatio": 0.0}
        session._backstop.wire = lambda _now: covered
        assert session._discipline.naked_changed() is False   # 首次判定不算翻转
        assert session._discipline.naked_changed() is False
        session._backstop.wire = lambda _now: naked
        assert session._discipline.naked_changed() is True
        assert session._discipline.naked_changed() is False   # 同一状态不重复推
    finally:
        await dispose_engine()


# ============================ G-14 / G-16：两条「看不见就等于没做」的自检 ============================
async def test_dust_skips_surface_on_the_vol_sizing_row():
    """[P0-4] 第三件：运行期「不足最小下单量」的跳过必须**看得见**——否则现象与「AI 一直不开单」
    完全一致，而唯一的诊断信息（executor 那条 status 文案）在前端没有渲染落点。"""
    await init_db()
    try:
        session = _session(_derived("balanced"))
        assert _rows(session)["volSizing"]["tone"] is None
        session.executor.dust_skips = 3
        row = _rows(session)["volSizing"]
        assert row["tone"] == "warn" and "3 次" in row["note"]
    finally:
        await dispose_engine()


async def test_dust_risk_warns_on_the_vol_sizing_row_before_the_first_skip_happens():
    """[P0-4] 第一件的**呈现半边**（G-14 要的是回执与 digest 两个落点，不是只有回执）。

    只在启动回执里写一句是不够的：回执用户看一眼就走了，而纪律条会一直挂在主面板上。若这一行在
    「每一轮开仓都将被静默跳过」的会话上仍显示成一片正常的绿，用户看到的就只有「AI 一直不开单」
    ——本轮最可能被误判成「纪律预设把 AI 弄坏了」的断面。
    """
    await init_db()
    try:
        session = _session(_derived("balanced"))
        assert _rows(session)["volSizing"]["note"] is None      # 未复核出风险 ⇒ 不吓唬人
        session.executor.dust_risk = True                       # manager._build_executor 置入
        row = _rows(session)["volSizing"]
        assert row["armed"] is True and row["tone"] == "warn"
        assert "最小下单量" in (row["note"] or "")
        session.executor.dust_skips = 2                         # 真发生了 ⇒ 改报已发生的次数
        assert "2 次" in _rows(session)["volSizing"]["note"]
    finally:
        await dispose_engine()


async def test_strictness_hint_fires_only_after_enough_rounds_with_zero_opens():
    """G-16：闸都武装着 + 跑了足够多轮 + 一单没开 ⇒ 显著提示；反向断面不提示。"""
    await init_db()
    try:
        session = _session(_derived("balanced"))
        session.rounds_completed = 3
        assert session.discipline_wire()["hint"] is None        # 样本不足，不吓唬人
        session.rounds_completed = 20
        hint = session.discipline_wire()["hint"]
        assert hint is not None and "尚未开过任何仓" in hint
        session.executor.trades.append({"side": "long", "ts": 1, "price": 1.0})
        assert session.discipline_wire()["hint"] is None        # 开过仓 ⇒ 不提示
    finally:
        await dispose_engine()


async def test_status_payload_carries_the_digest_so_the_frontend_can_actually_read_it():
    """「后端算了但用户看不见 = 死数据」：呈现通道必须在 status 上，且是整体 spread 合并的那一条。"""
    await init_db()
    try:
        payload = _session(_derived("balanced"))._status_payload()
        assert len(payload["disciplines"]["rows"]) == 13
        assert "hint" in payload["disciplines"]
    finally:
        await dispose_engine()


async def test_finalize_metrics_record_which_rules_were_on_not_just_how_many():
    """[P2-6]：只落一个整数，事后回答不了「当时开着哪几条」——而那是校准阈值时唯一想问的。"""
    await init_db()
    try:
        session = _session(_derived("balanced"))
        stats = session._discipline.merge_into({})
        assert stats["disciplinePreset"] == "balanced"
        assert stats["disciplineArmedCount"] == len(stats["disciplineArmedKeys"])
        assert "volSizing" in stats["disciplineArmedKeys"]
    finally:
        await dispose_engine()


# ============================ G-2：edge 门禁「该拦要拦、该过要过」 ============================
def test_edge_gate_at_the_balanced_threshold_is_not_a_tautology():
    """G-2：`balanced` 的 `k_min=1.5` 必须**两边都验**——只验「拦得住」，就是在鼓励做一条恒真判据。

    低波动断面（ATR 小 ⇒ 粗估毛 edge 覆盖不住 1.5× 成本）⇒ `open_long` 降级 `hold` + edge 码；
    高波动断面 ⇒ 原样直通。两条同用一份派生 settings，故它们证明的是同一个阈值的两侧。
    """
    from app.ai_auto.decider import TradeDecision
    from app.ai_auto.edge_gate import evaluate_edge_gate

    settings = _derived("balanced")
    assert settings.ai_trade_edge_gate_k_min == 1.5

    def _run(atr_px: float):
        return evaluate_edge_gate(
            TradeDecision(action="open_long", size_fraction=0.5, confidence=0.9, reason="r"),
            settings=settings, last_px=30_000.0, atr_px=atr_px, take_profit_pct=None,
            bar_seconds=BAR_SECONDS["1H"], funding_enabled=False)

    thin = _run(3.0)            # ATR ≈ 1bp ⇒ 毛 edge 远低于 1.5 × 成本
    assert thin is not None and thin.gated is True
    assert thin.decision.action == "hold" and thin.status == "veto"
    assert thin.reason and "edge" in thin.reason

    fat = _run(900.0)           # ATR ≈ 300bp ⇒ 毛 edge 充裕
    assert fat is not None and fat.status == "pass"
    assert fat.decision.action == "open_long", "阈值两侧都得验，否则就是一条恒真判据"


def test_edge_gate_is_completely_off_under_the_off_preset():
    """`off` ⇒ `k_min=0` ⇒ 纯核心第一句即 `None`（整层空转，逐字回退合入前）。"""
    from app.ai_auto.decider import TradeDecision
    from app.ai_auto.edge_gate import evaluate_edge_gate

    out = evaluate_edge_gate(
        TradeDecision(action="open_long", size_fraction=0.5, confidence=0.9, reason="r"),
        settings=Settings(_env_file=None), last_px=30_000.0, atr_px=3.0, take_profit_pct=None,
        bar_seconds=BAR_SECONDS["1H"], funding_enabled=False)
    assert out is None


def test_every_digest_row_matches_the_declared_wire_contract():
    """13 行的形状由 `AiTradeDisciplineRowOut` 声明——让那个模型**真的守门**，而不是一份说明文档。

    这两个端点都没有 `response_model`（wire 的真源是 `group_status()` / `_status_payload()`），
    故模型不会在运行时被 FastAPI 校验。用例在此替它跑一遍：少一个 `source`、`armed` 写成字符串、
    或 `firedCount` 填了个 `"—"` 字符串，都会在这里红。
    """
    from app.ai_auto.discipline_view import ROW_KEYS, DisciplineFacts, build_discipline_digest
    from app.schemas import AiTradeDisciplineRowOut

    facts = DisciplineFacts(
        settings=_derived("balanced"), sizing=None, horizon_rule=None, base_rule=None,
        profit_rule=None, backstop_wire=None, gate_counts={}, prior_armed=False, spec_count=2)
    rows = build_discipline_digest(facts)
    assert [row["key"] for row in rows] == list(ROW_KEYS)   # 顺序是 wire 契约的一部分
    for row in rows:
        AiTradeDisciplineRowOut.model_validate(row)
