"""战绩与信誉先验的纯核心（ai-trade-intel-actionability G-2 / G-13 / G-14 / G-28①）。

**离线、零 IO**。三条被反复强调的性质在这里逐条冻结：

1. **`eff=None` ⇒ `score_item` 逐字节回退**（G-2）——先验是一条加法，关掉它必须什么都不发生；
2. **`eff` 只透传到该传的两处**（G-13）——`gate.admit` 的 over_cap 截断与 `render_listing`
   的清单排序必须与合入前逐字节相同，否则就是两处没人要求、也没人会发现的行为变更；
3. **样本不足 ⇒ 权重严格 0.0，`accuracy()` 返回 `None` 而不是 0.0**（G-14）。
"""
from __future__ import annotations

import pytest

from app.ai_auto.intel import efficacy as eff_mod
from app.ai_auto.intel import gate
from app.ai_auto.intel.gate import IntelItem, score_item
from app.ai_auto.intel.taxonomy import IntelLimits

NOW = 1_788_110_000.0
LIMITS = IntelLimits(efficacy_enabled=True, efficacy_k_min=4, efficacy_max_weight=0.25,
                     efficacy_min_domain_samples=3)


def _item(item_id: int = 1, *, category: str = "risk", **over) -> IntelItem:
    base = {"item_id": item_id, "inst_id": "BTC-USDT-SWAP", "tier": "ultra",
            "category": category, "headline": "h", "summary": "s" * 20, "stance": "bearish",
            "impact": "high", "confidence": 0.8, "event_ts": int(NOW - 3600),
            "captured_ts": int(NOW - 3600), "expires_ts": int(NOW + 86400),
            "domain_count": 3}
    return IntelItem(**{**base, **over})


def _cite(item_id: int, outcome: str, net: float) -> dict:
    return {"itemId": item_id, "role": "entry", "settled": True, "outcome": outcome,
            "netPnlUsd": net}


def _rows(category: str, verdicts: list[str], item_id_base: int = 100) -> list[dict]:
    return [{"id": item_id_base + i, "category": category, "verdict": verdict,
             "sources": [{"domain": "example.com"}]}
            for i, verdict in enumerate(verdicts)]


# ---------- G-14：样本不足 ⇒ 中性 ----------
def test_zero_sample_bucket_weighs_exactly_zero_and_has_no_accuracy():
    empty = eff_mod.Efficacy()
    assert eff_mod.weight_for("risk", empty, limits=LIMITS) == 0.0
    assert eff_mod.accuracy(eff_mod.Stat()) is None


def test_a_bucket_below_k_min_weighs_exactly_zero():
    """`samples < k_min` ⇒ **严格 0.0**（不是「一个很小的数」）。

    「没数据」与「全错」是两件事：写成同一个值，就是把一次没跑过的统计伪装成一次很差的成绩。
    """
    eff = eff_mod.solve([], _rows("risk", ["refuted", "refuted"]), limits=LIMITS, now=NOW)
    assert eff.by_category["risk"].samples == 2 < LIMITS.efficacy_k_min
    assert eff_mod.weight_for("risk", eff, limits=LIMITS) == 0.0


def test_accuracy_excludes_unresolved_from_the_denominator():
    """`unresolved` 的含义是「查不出来」——算进分母会让准确率变成一句关于核查能力的话。"""
    stat = eff_mod.Stat(confirmed=3, refuted=1, unresolved=6)
    assert eff_mod.accuracy(stat) == pytest.approx(0.75)
    assert eff_mod.accuracy(eff_mod.Stat(unresolved=9)) is None


def test_a_negative_track_record_yields_a_negative_but_capped_weight():
    eff = eff_mod.solve([_cite(1, "loss", -50.0), _cite(2, "loss", -80.0)],
                        _rows("risk", ["refuted"] * 6), limits=LIMITS, now=NOW)
    weight = eff_mod.weight_for("risk", eff, limits=LIMITS)
    assert -LIMITS.efficacy_max_weight <= weight < 0.0


def test_a_disabled_switch_produces_an_empty_efficacy():
    """`efficacy_enabled=False` ⇒ 空产物 ⇒ 四个消费点全部逐字节回退。"""
    off = IntelLimits(efficacy_enabled=False)
    eff = eff_mod.solve([_cite(1, "win", 10.0)], _rows("risk", ["confirmed"] * 9),
                        limits=off, now=NOW)
    assert eff.by_category == {} and eff.weights == {} and eff.overall.samples == 0


def test_void_settlements_never_enter_any_statistic():
    """`void` = 结算尝试发生过、净额不可得。把一次失败伪装成一次平局比缺一个数更坏。"""
    eff = eff_mod.solve([{"itemId": 1, "role": "entry", "settled": True,
                          "outcome": "void", "netPnlUsd": None}],
                        [{"id": 1, "category": "risk", "verdict": "", "sources": []}],
                        limits=LIMITS, now=NOW)
    assert eff.overall.n == 0


def test_hold_role_citations_are_not_settleable_evidence():
    """「情报影响了这次继续持有」不可证伪，故它一条都不进 B1。"""
    eff = eff_mod.solve([{"itemId": 1, "role": "hold", "settled": True,
                          "outcome": "win", "netPnlUsd": 9.0}],
                        [{"id": 1, "category": "risk", "verdict": "", "sources": []}],
                        limits=LIMITS, now=NOW)
    assert eff.overall.n == 0


# ---------- G-2：`eff=None` 的逐字节回退 ----------
def test_score_item_is_bit_identical_without_an_efficacy():
    item = _item()
    assert score_item(item, now=NOW, limits=LIMITS) == score_item(
        item, now=NOW, limits=LIMITS, eff=None)
    assert score_item(item, now=NOW) == score_item(item, now=NOW, eff=None)


def test_score_item_moves_by_exactly_the_category_weight():
    """加分项的**全部**贡献就是 `eff.weights[category]`——多一分少一分都能在这里看出来。"""
    eff = eff_mod.solve([_cite(1, "win", 40.0)] * 3, _rows("risk", ["confirmed"] * 6),
                        limits=LIMITS, now=NOW)
    item = _item()
    delta = score_item(item, now=NOW, limits=LIMITS, eff=eff) - score_item(
        item, now=NOW, limits=LIMITS)
    assert delta == pytest.approx(eff.weights["risk"], abs=1e-12)
    assert delta > 0.0


# ---------- G-13：`eff` 只透传到该传的两处 ----------
def test_admit_over_cap_and_render_listing_never_see_the_efficacy():
    """`gate.admit` 与 `service.render_listing` **必须**保持与合入前逐字节相同。

    实现里若「顺手」把 `eff` 也传给它们，两处的排序会在本轮**首次**与合入前不同——那是没人
    要求、也没人会发现的行为变更。判据取源码里那两处调用点的字面形态。
    """
    import inspect

    from app.ai_auto.intel import service_curate

    admit_src = inspect.getsource(gate.admit)
    listing_src = inspect.getsource(service_curate.render_listing)
    assert "score_item(it, now=wall)" in admit_src
    assert "eff" not in admit_src.split("accepted.sort")[1].split("\n")[0]
    assert "gate.score_item(it, now=now)" in listing_src


def test_build_digest_and_plan_cleanup_do_receive_it():
    """反过来：那两处**必须**传（否则轴 B 的四个消费点里有两个是死的）。"""
    import inspect

    from app.ai_auto.intel import cleanup, disclosure

    assert "eff=eff" in inspect.getsource(disclosure.build_digest_and_ids)
    assert "eff=eff" in inspect.getsource(cleanup.plan_cleanup)


# ---------- G-28①：纯函数 ⇒ 两处求解同值 ----------
def test_solving_twice_with_the_same_inputs_is_field_by_field_identical():
    """这条性质是「两处各求一次不构成两个真源」的**全部**依据——否则它只是一句好听话。"""
    cites = [_cite(1, "win", 12.0), _cite(2, "loss", -4.0)]
    items = _rows("risk", ["confirmed", "refuted", "unresolved", "confirmed"])
    first = eff_mod.solve(cites, items, limits=LIMITS, now=NOW)
    second = eff_mod.solve(list(cites), list(items), limits=LIMITS, now=NOW + 1)
    assert first.overall == second.overall
    assert first.by_category == second.by_category
    assert first.by_domain == second.by_domain
    assert first.weights == second.weights
    assert first.domain_weights == second.domain_weights


def test_domain_reputation_counts_every_source_of_a_judged_item():
    """一条被三家媒体报道的情报被证伪时，三家都该记这一笔。"""
    rows = [{"id": 1, "category": "risk", "verdict": "refuted",
             "sources": [{"domain": "a.com"}, {"domain": "b.com"}, {"domain": "a.com"}]}]
    eff = eff_mod.solve([], rows, limits=LIMITS, now=NOW)
    assert eff.by_domain["a.com"].refuted == 1      # 同一条里重复的域名只记一次
    assert eff.by_domain["b.com"].refuted == 1


def test_normalized_reason_length_ignores_padding():
    """一串全角空格凑出来的「理由」不该算数（举证责任的度量必须归一后再数）。"""
    assert eff_mod.normalized_reason_len("　" * 40) == 0
    assert eff_mod.normalized_reason_len("这条情报的方向与最近三次结算完全相反，仍建议升级。") >= 20
