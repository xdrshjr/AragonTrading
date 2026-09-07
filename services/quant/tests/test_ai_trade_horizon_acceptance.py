"""周期验收卡纯核心（ai-trade-horizon-acceptance-loop 轴 C · §6.1 A-1~A-15）。

守的是「一个恒真的门槛比没有门槛更危险」这条判断，落成四类回归：
- **样本不足时没有任何一行是绿色**（L1 放行纪律唯一的技术保证）；
- **没有目标区间就不判、不除零**（`custom` 全默认是文档化的安全退出口，[P0-2]）；
- **合并的是原始计数器**，按 round 后的展示值反乘再合并会得到另一个数（[P1-2]）；
- **墙钟口径与在场口径同时下发且不相等**（后者与平均持仓时长代数等价，单独用它恒真）。
"""
from app.ai_auto.attribution import AttributionLedger
from app.ai_auto.exit_attribution import ExitLedger, exit_counters
from app.ai_auto.hold_horizon import HorizonRule
from app.ai_auto.horizon_acceptance import (
    AcceptanceInput,
    build_acceptance,
    build_input,
    counters_from_input,
    input_from_wire,
    input_wire,
    merge_inputs,
    resolve_thresholds,
)

_HOUR = 3600.0
_DAY = 86400.0


class _Settings:
    ai_trade_acceptance_enabled = True
    ai_trade_acceptance_min_round_trips = 20
    ai_trade_acceptance_min_within_target_rate = 0.6
    ai_trade_acceptance_max_fee_to_gross = 0.35


def _rule(lo: float = 12 * _HOUR, hi: float = 48 * _HOUR) -> HorizonRule:
    return HorizonRule(profile_id="short_swing", title="短波段",
                       target_hold_min_s=lo, target_hold_max_s=hi)


def _input(**kw) -> AcceptanceInput:
    base = dict(round_trips=30, hold_seconds_sum=30 * 20 * _HOUR, hold_samples=30,
                within_target_samples=27, truncated_samples=0,
                gross_pnl_usd=100.0, fee_usd=10.0, net_pnl_usd=90.0,
                abs_gross_pnl_usd=100.0, funding_cost_usd=1.0,
                funding_basis="settled+accrued", wall_seconds=30 * _DAY,
                exit_counts={"agent": 20, "profitGuard": 10})
    base.update(kw)
    return AcceptanceInput(**base)


def _card(inp: AcceptanceInput | None, rule=None, settings=None) -> dict | None:
    settings = settings or _Settings()
    rule = _rule() if rule is None else rule
    return build_acceptance(inp, rule, resolve_thresholds(settings, rule),
                            exits=None, funding=None)


def _rows(card: dict) -> dict:
    return {row["key"]: row for row in card["rows"]}


# ---------------- A-1：五行各一 pass / 一 fail ----------------
def test_all_five_rows_can_pass():
    card = _card(_input())
    assert card["verdict"] == "pass"
    assert all(row["verdict"] == "pass" for row in card["rows"])


def test_each_row_can_fail_independently():
    """A-1：五行判据各造一条 fail（避免「某一行其实从来不会红」）。"""
    cases = {
        "avgHold": _input(hold_seconds_sum=30 * 1 * _HOUR),
        "withinTargetRate": _input(within_target_samples=1),
        "turnoverPerDay": _input(wall_seconds=1 * _DAY),
        "feeToGross": _input(fee_usd=90.0),
        "netAfterFunding": _input(net_pnl_usd=-5.0),
    }
    for key, inp in cases.items():
        card = _card(inp)
        assert _rows(card)[key]["verdict"] == "fail", key
        assert card["verdict"] == "fail", key


# ---------------- A-2：样本不足 ----------------
def test_below_sample_floor_is_insufficient_and_never_green():
    """A-2：样本 < `sampleFloor` ⇒ 整卡 `insufficient` 且**没有任何一行是 pass**。"""
    card = _card(_input(round_trips=5, hold_samples=5,
                        hold_seconds_sum=5 * 20 * _HOUR, within_target_samples=5))
    assert card["verdict"] == "insufficient"
    assert [row["verdict"] for row in card["rows"]] == ["insufficient"] * 5
    assert "样本 5 / 20" in card["sampleNote"]


# ---------------- A-3 / A-4：合并 ----------------
def test_merge_takes_max_wall_seconds_not_sum():
    """A-3：成员是**并发**跑的，求和会把换手率腰斩（一个恰好总是通过的门槛）。"""
    merged = merge_inputs([_input(wall_seconds=10 * _DAY),
                           _input(wall_seconds=7 * _DAY)])
    assert merged.wall_seconds == 10 * _DAY
    assert merged.round_trips == 60


def test_merge_takes_the_weakest_funding_basis():
    """A-4：任一成员 `unavailable` ⇒ 合并后 `unavailable` 且金额整体不可得。"""
    merged = merge_inputs([_input(),
                           _input(funding_cost_usd=None, funding_basis="unavailable")])
    assert merged.funding_basis == "unavailable"
    assert merged.funding_cost_usd is None
    assert _rows(_card(merged))["netAfterFunding"]["verdict"] == "insufficient"


def test_merge_of_nothing_is_none():
    assert merge_inputs([]) is None


# ---------------- A-5：两个换手口径 ----------------
def test_wallclock_and_in_position_turnover_are_both_emitted_and_differ():
    """A-5：在场口径与平均持仓时长互为倒数（恒真），故必须与墙钟口径并列且不相等。"""
    row = _rows(_card(_input()))["turnoverPerDay"]
    assert row["value"] is not None and row["inPositionValue"] is not None
    assert row["value"] != row["inPositionValue"]
    # 在场口径恒等于 86400 / 平均持仓时长——它不是一个独立读数。
    assert round(row["inPositionValue"], 4) == round(_DAY / (20 * _HOUR), 4)


# ---------------- A-6 [P1-1]：全程累计 vs 200 环形窗口 ----------------
def _record_trip(ledger, pnl):
    """为累计窗口场景提供完整双腿；开仓真实零费、平仓费用来源明确。"""
    number = ledger.lifetime_cost()["roundTrips"] + 1
    shared = {"qty": 1, "price": 100, "feeKnown": True, "feeBasis": "measured"}
    ledger.note_open({**shared, "id": 2 * number - 1, "side": "long",
                      "fee": 0, "positionAfter": 1}, seq=number)
    ledger.record_close({**shared, "id": 2 * number, "side": "close",
                         "fee": .1, "pnl": pnl, "positionAfter": 0})


def test_lifetime_and_rolling_cost_agree_below_capacity_and_diverge_above():
    """A-6：≤200 笔时逐位相等；>200 笔后必然分叉，验收卡取的是 lifetime。"""
    ledger = AttributionLedger(net_mode=True)
    for _ in range(200):
        _record_trip(ledger, 1.0)
    stats = ledger.stats()
    assert stats["cost"]["costRatio"] == stats["cost"]["lifetime"]["costRatio"]
    assert stats["cost"]["roundTrips"] == stats["cost"]["lifetime"]["roundTrips"]

    for _ in range(50):
        _record_trip(ledger, 3.0)
    stats = ledger.stats()
    assert stats["cost"]["roundTrips"] == 200
    assert stats["cost"]["lifetime"]["roundTrips"] == 250
    assert stats["cost"]["costRatio"] != stats["cost"]["lifetime"]["costRatio"]


def test_build_input_consumes_the_lifetime_block():
    """验收卡的成本四项一律取 `cost.lifetime.*`（写成环形口径会悄悄换时间窗）。"""
    ledger = AttributionLedger(net_mode=True)
    for _ in range(250):
        _record_trip(ledger, 1.0)
    inp = build_input(horizon_parts={"roundTrips": 250, "holdSecondsSum": 0.0,
                                     "holdSamples": 0, "withinTargetSamples": 0},
                      lifetime_cost=ledger.lifetime_cost(), exit_stats=None,
                      funding_stats=None, wall_seconds=_DAY)
    assert inp.fee_usd == round(250 * 0.1, 6)


# ---------------- A-7：资金费不可得 ----------------
def test_missing_funding_makes_the_last_row_insufficient():
    """A-7：`funding_cost_usd is None` ⇒ 第 5 行 `insufficient`，整卡不得 `pass`。"""
    card = _card(_input(funding_cost_usd=None, funding_basis="unavailable"))
    assert _rows(card)["netAfterFunding"]["verdict"] == "insufficient"
    assert _rows(card)["netAfterFunding"]["value"] is None
    assert card["verdict"] == "insufficient"


def test_zero_funding_is_a_real_reading_not_a_gap():
    """`0.0` 是「真的没跨过结算点」——它必须能通过第 5 行，而 `None` 不能。"""
    card = _card(_input(funding_cost_usd=0.0, funding_basis="accrued"))
    assert _rows(card)["netAfterFunding"]["verdict"] == "pass"


# ---------------- A-8：诊断 ----------------
def test_diagnostics_fire_and_stay_silent_on_their_own_conditions():
    """A-8：三条诊断各一条命中 / 各一条不命中。"""
    hot = _card(_input(exit_counts={"agent": 29, "profitGuard": 1}, fee_usd=90.0))
    assert any("抢跑退出仍占主导" in line for line in hot["diagnostics"])
    assert not any("抢跑退出仍占主导" in line for line in _card(_input())["diagnostics"])

    losing = _card(_input(net_pnl_usd=0.5, funding_cost_usd=1.0))
    assert any("问题在方向判断" in line for line in losing["diagnostics"])
    assert not any("问题在方向判断" in line for line in _card(_input())["diagnostics"])

    blind = _card(_input(funding_cost_usd=None, funding_basis="unavailable"))
    assert any("资金费口径不可得" in line for line in blind["diagnostics"])
    short = _card(_input(hold_seconds_sum=30 * 60.0, funding_cost_usd=None,
                         funding_basis="unavailable"))
    assert not any("资金费口径不可得" in line for line in short["diagnostics"])


# ---------------- A-9 [P0-4]：对照臂 ----------------
def test_no_rule_yields_no_card_but_exits_survive():
    """A-9：未装配周期模式 ⇒ 卡片 `None`，**但退出账本照常出数**（对照臂必须可观测）。"""
    assert build_acceptance(_input(), None, resolve_thresholds(_Settings(), None),
                            exits={"totalExits": 3}, funding=None) is None
    ledger = ExitLedger(net_mode=True)
    ledger.record({"pnl": 1.0}, hold_seconds=None, source="agent")
    assert ledger.stats()["totalExits"] == 1


def test_exits_and_funding_are_referenced_not_reassembled():
    """W-9：`exitMix` / `funding` 必须是调用方传进来的**同一个对象**，不得二次装配。"""
    exits = {"totalExits": 1, "bySource": []}
    funding = {"enabled": True, "basis": "accrued"}
    card = build_acceptance(_input(), _rule(), resolve_thresholds(_Settings(), _rule()),
                            exits=exits, funding=funding)
    assert card["exitMix"] is exits
    assert card["funding"] is funding


# ---------------- A-10：阈值随模式变 ----------------
def test_thresholds_follow_the_profile():
    """A-10：第 1、3 行阈值全部由契约派生（换模式即换阈值，不引入新魔数）。"""
    short = resolve_thresholds(_Settings(), _rule(12 * _HOUR, 48 * _HOUR))
    trend = resolve_thresholds(_Settings(), _rule(24 * _HOUR, 72 * _HOUR))
    assert short.min_avg_hold_s == 12 * _HOUR and trend.min_avg_hold_s == 24 * _HOUR
    assert short.max_turnover_per_day == 2.0 and trend.max_turnover_per_day == 1.0


# ---------------- A-11 / A-12 [P0-2] / P2-3 ----------------
def test_custom_all_defaults_never_divides_by_zero_and_never_passes():
    """A-11：`target_hold_min_s == 0`（custom 全默认）⇒ 第 1、3 行 insufficient、不抛。"""
    card = _card(_input(), rule=HorizonRule(profile_id="custom", title="自定义"))
    rows = _rows(card)
    assert rows["avgHold"]["verdict"] == "insufficient"
    assert rows["turnoverPerDay"]["verdict"] == "insufficient"
    assert rows["withinTargetRate"]["verdict"] == "insufficient"
    assert card["verdict"] != "pass"
    assert rows["avgHold"]["target"] == "未设目标区间"


def test_target_max_zero_only_disables_the_hit_rate_row():
    """`target_hold_max_s <= 0` ⇒ 只有第 2 行没有靶子。"""
    rows = _rows(_card(_input(), rule=_rule(12 * _HOUR, 0.0)))
    assert rows["withinTargetRate"]["verdict"] == "insufficient"
    assert rows["avgHold"]["verdict"] == "pass"


def test_short_profile_turnover_threshold_is_capped_and_admits_it():
    """A-12（P2-3）：`hourly_swing` 的纯派生阈是 240 次/日（恒真）⇒ 由 24 兜底并自认弱门槛。"""
    th = resolve_thresholds(_Settings(), _rule(360.0, 7200.0))
    assert th.derived_turnover_per_day == 240.0
    assert th.max_turnover_per_day == 24.0
    note = _rows(_card(_input(), rule=_rule(360.0, 7200.0)))["turnoverPerDay"]["note"]
    assert "不构成周期契约意义上的门槛" in note


# ---------------- A-13 [P1-3]：wall_seconds ----------------
def test_nonpositive_wall_seconds_is_insufficient_not_a_crash():
    """A-13：`wall_seconds <= 0` ⇒ 第 3 行 `insufficient`（不除零）。"""
    rows = _rows(_card(_input(wall_seconds=0.0)))
    assert rows["turnoverPerDay"]["verdict"] == "insufficient"
    assert rows["turnoverPerDay"]["value"] is None


# ---------------- A-14 [P1-8]：截断样本不剔除 ----------------
def test_truncated_samples_are_disclosed_but_kept_in_the_denominator():
    """A-14：剔除即幸存者偏差 ⇒ 分母不变，只在 note 里如实披露。"""
    plain = _rows(_card(_input()))["avgHold"]
    truncated = _rows(_card(_input(truncated_samples=3)))["avgHold"]
    assert plain["value"] == truncated["value"]          # 分母未变
    assert "被区间 / 会话收尾截断" in (truncated["note"] or "")
    assert plain["note"] is None


# ---------------- A-15 [P1-2]：原始计数器 vs round 反乘 ----------------
def test_exact_merge_differs_from_rounding_then_multiplying_back():
    """A-15：把 `avg` round 到 3 位再反乘，与精确合并**不相等**。"""
    a = _input(round_trips=3, hold_samples=3, hold_seconds_sum=10.0)
    b = _input(round_trips=3, hold_samples=3, hold_seconds_sum=10.0)
    exact = merge_inputs([a, b])
    exact_avg = exact.hold_seconds_sum / exact.hold_samples
    # `stats()` 只下发 round 3 位的 `avgHoldSeconds`；照它反乘再合并即得下面这个数。
    rounded_avg = (round(10.0 / 3, 3) * 3 + round(10.0 / 3, 3) * 3) / 6
    assert exact_avg != rounded_avg
    assert exact.hold_seconds_sum == 20.0        # 原始计数器逐位精确


def test_input_wire_round_trips_exactly():
    """落库 → 读回的原始计数器必须逐字段相等（否则分组历史的合并会静默失真）。"""
    inp = _input(exit_hold_samples={"agent": 20}, exit_wins={"agent": 12})
    assert input_from_wire(input_wire(inp)) == inp


def test_input_from_wire_of_none_is_none():
    assert input_from_wire(None) is None
    assert input_wire(None) is None


def test_counters_from_input_feeds_the_exit_ledger_rebuild():
    """合并后的退出分布必须由**同一个** `stats()` 渲染，不得另写一份合并版渲染器。"""
    ledger = ExitLedger(net_mode=True)
    for _ in range(6):
        ledger.record({"pnl": 1.0}, hold_seconds=10.0, source="agent")
    inp = build_input(horizon_parts={"roundTrips": 6, "holdSecondsSum": 60.0,
                                     "holdSamples": 6, "withinTargetSamples": 6},
                      lifetime_cost={}, exit_stats=exit_counters(ledger),
                      funding_stats=None, wall_seconds=_DAY)
    rebuilt = ExitLedger.from_counters(counters_from_input(inp))
    assert rebuilt.stats() == ledger.stats()


def test_build_input_returns_none_without_any_sample():
    """零往返、零退出 ⇒ `None` ⇒ status 三键为 None ⇒ 卡片不渲染（W-10）。"""
    assert build_input(horizon_parts=None, lifetime_cost={}, exit_stats=None,
                       funding_stats=None, wall_seconds=_DAY) is None
