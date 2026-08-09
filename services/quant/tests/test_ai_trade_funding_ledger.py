"""资金费账本纯核心（ai-trade-horizon-acceptance-loop 轴 A · §6.1 F-1~F-13）。

守的是三条最容易复发的口径：
- **「不知道」必须能被表达**：判据是 `interval_known` 而不是 `interval_s <= 0`——上游会把未知
  兜底成 28800，按后者写的分支在生产里恒不成立（[P0-1]，与上一轮同一个坑的第二次复发）；
- **`None` 与 `0` 是两个结论**：前者「没算成」，后者「真的没跨过结算点」；
- **累计成本读数只增不减**：交易所结算有延迟，直接替换会让资金费先跳水再补回。
"""
from dataclasses import dataclass

from app.ai_auto.funding_ledger import (
    FundingEpoch,
    FundingLedger,
    FundingWindow,
    BacktestFundingCurve,
    accrue,
    merge_funding_wires,
    settlement_points,
)

_RATE = 0.0001          # 1 bps / 期
_INTERVAL = 28800.0     # 8h
_ANCHOR = 28800


@dataclass
class _Snap:
    """`FundingSnapshot` 的最小替身（账本只读这四个字段）。"""

    rate: float = _RATE
    next_funding_ts: int = _ANCHOR
    interval_s: float = _INTERVAL
    interval_known: bool = True


def _window(**kw) -> FundingWindow:
    base = {"rate": _RATE, "anchor_ts": _ANCHOR, "interval_s": _INTERVAL,
            "interval_known": True}
    base.update(kw)
    return FundingWindow(**base)


def _epoch(**kw) -> FundingEpoch:
    base = {"side": "long", "seq": 1, "qty_coin": 1.0, "opened_wall": 0.0}
    base.update(kw)
    return FundingEpoch(**base)


# ---------------- F-1 / F-13：结算周期已知性是唯一判据 ----------------
def test_unknown_interval_yields_no_settlement_points():
    """F-1 [P0-1]：`interval_known is False` ⇒ 平铺为空 ⇒ 不计提。"""
    points = settlement_points(from_ts=0, to_ts=10 * _INTERVAL,
                               window=_window(interval_known=False))
    assert points == []


def test_unknown_interval_is_not_the_same_as_nonpositive_interval():
    """F-1 反面：`interval_s` **是正数**（上游兜底 8h）但 `interval_known` 为假——按
    `interval_s <= 0` 写的判据在这条用例上恒不成立，故它必须依然不计提。"""
    window = _window(interval_known=False)
    assert window.interval_s > 0            # 上游兜底值，看起来完全正常
    assert not window.is_usable()


def test_unknown_interval_tick_is_counted_and_interval_s_is_hidden():
    """F-13：结算周期未知的 tick 记 `unknownIntervalTicks`，且 `intervalS` 下发 `None`。"""
    ledger = FundingLedger(enabled=True, ct_val=1.0)
    ledger.note_open({"side": "long", "qty": 1.0}, seq=1, now_wall=0.0)
    ledger.accrue(_Snap(interval_known=False), now_wall=_INTERVAL * 2, mark_px=100.0)
    stats = ledger.stats(settled_usd=None)
    assert stats["unknownIntervalTicks"] == 1
    assert stats["intervalKnown"] is False
    assert stats["intervalS"] is None       # 绝不把兜底的 28800 当读数展示
    assert stats["crossings"] == 0


def test_interval_outside_sanity_range_is_treated_as_bad_reading():
    """区间外的结算周期（例如 1 秒）视为坏读数 ⇒ 不计提。"""
    window = _window(interval_s=1.0, min_interval_s=1800.0, max_interval_s=28800.0)
    assert settlement_points(from_ts=0, to_ts=100000, window=window) == []


# ---------------- F-2 / F-3：跨点计提与多空极性 ----------------
def test_three_crossings_accrue_exactly_three_times():
    """F-2：持仓跨 3 个结算点 ⇒ 恰计提 3 次。"""
    points = settlement_points(from_ts=0, to_ts=90000, window=_window())
    assert points == [28800, 57600, 86400]
    new_epoch, crossed = accrue(_epoch(), window=_window(), now_wall=90000.0,
                                mark_px=50000.0)
    assert crossed == 3
    assert new_epoch.crossings == 3
    assert new_epoch.accrued_usd == 0.0001 * 50000.0 * 3


def test_long_pays_and_short_receives_on_positive_rate():
    """F-3：`rate > 0` ⇒ 多头支出为正、空头为负（收取）。符号约定：支出为正。"""
    long_epoch, _ = accrue(_epoch(side="long"), window=_window(), now_wall=30000.0,
                           mark_px=1000.0)
    short_epoch, _ = accrue(_epoch(side="short"), window=_window(), now_wall=30000.0,
                            mark_px=1000.0)
    assert long_epoch.accrued_usd > 0
    assert short_epoch.accrued_usd == -long_epoch.accrued_usd


def test_settlement_points_are_exclusive_on_the_left():
    """区间是 `(from, to]`：正好落在 from 上的那个点属于上一次，不得重复计提。"""
    assert settlement_points(from_ts=28800, to_ts=57600, window=_window()) == [57600]


# ---------------- F-4：部分成交累加而不是重置 ----------------
def test_partial_fills_accumulate_qty_without_resetting_accrual():
    """F-4：同一 `(side, seq)` 的第二次 `note_open` 累加张数、**不清零已计提额**。"""
    ledger = FundingLedger(enabled=True, ct_val=1.0)
    ledger.note_open({"side": "long", "qty": 1.0}, seq=1, now_wall=0.0)
    ledger.accrue(_Snap(), now_wall=30000.0, mark_px=1000.0)
    accrued_before = ledger.epoch.accrued_usd
    assert accrued_before > 0
    ledger.note_open({"side": "long", "qty": 2.0}, seq=1, now_wall=30001.0)
    assert ledger.epoch.qty_coin == 3.0
    assert ledger.epoch.accrued_usd == accrued_before      # 绝不因加仓而漏记


def test_new_seq_starts_a_fresh_epoch():
    """`seq` 变化 = 新的持仓周期 ⇒ 新 epoch（不继承上一段的计提额）。"""
    ledger = FundingLedger(enabled=True, ct_val=1.0)
    ledger.note_open({"side": "long", "qty": 1.0}, seq=1, now_wall=0.0)
    ledger.accrue(_Snap(), now_wall=30000.0, mark_px=1000.0)
    ledger.note_open({"side": "long", "qty": 1.0}, seq=2, now_wall=30001.0)
    assert ledger.epoch.accrued_usd == 0.0
    assert ledger.epoch.seq == 2


# ---------------- F-5：坏判定价不推进游标 ----------------
def test_bad_mark_px_neither_accrues_nor_advances_the_cursor():
    """F-5：`mark_px` 非正 ⇒ 本次不计提且不推进 ⇒ 下一次 tick 把那个结算点补上。"""
    ledger = FundingLedger(enabled=True, ct_val=1.0)
    ledger.note_open({"side": "long", "qty": 1.0}, seq=1, now_wall=0.0)
    ledger.accrue(_Snap(), now_wall=30000.0, mark_px=0.0)
    assert ledger.crossings == 0
    assert ledger.epoch.settled_through_ts == 0
    ledger.accrue(_Snap(), now_wall=30001.0, mark_px=1000.0)
    assert ledger.crossings == 1              # 补上了，绝不因一次坏读数永久丢点


def test_missing_snapshot_counts_a_stale_tick():
    ledger = FundingLedger(enabled=True, ct_val=1.0)
    ledger.note_open({"side": "long", "qty": 1.0}, seq=1, now_wall=0.0)
    ledger.accrue(None, now_wall=30000.0, mark_px=1000.0)
    assert ledger.stats(settled_usd=None)["staleTicks"] == 1


# ---------------- F-6：封顶 ----------------
def test_settlement_points_are_capped():
    """F-6：坏 interval 下不产生上万次计提（只取最近的那批并记 `capHits`）。"""
    window = _window(interval_s=1800.0, min_interval_s=60.0)
    points = settlement_points(from_ts=0, to_ts=1800.0 * 5000, window=window)
    assert len(points) == 64
    new_epoch, crossed = accrue(_epoch(), window=window, now_wall=1800.0 * 5000,
                                mark_px=100.0)
    assert crossed == 64
    assert new_epoch.cap_hits == 1


def test_cap_hits_accumulate_across_epochs():
    """触顶计数按**增量**累加：epoch 随每次平仓重建，直接赋值会让上一段的次数在换仓时归零。"""
    ledger = FundingLedger(enabled=True, ct_val=1.0, min_interval_s=60.0)
    bad = _Snap(interval_s=1800.0)
    for seq in (1, 2):
        ledger.note_open({"side": "long", "qty": 1.0}, seq=seq, now_wall=0.0)
        ledger.accrue(bad, now_wall=1800.0 * 5000, mark_px=100.0)
        ledger.note_close({}, 1800.0 * 5000)
    assert ledger.stats(settled_usd=None)["capHits"] == 2


# ---------------- F-7：总闸 ----------------
def test_disabled_ledger_is_a_no_op():
    """F-7：`enabled=False` ⇒ 全部方法空转、`stats()` 恒 `None`（整键缺席 ⇒ 逐字回退）。"""
    ledger = FundingLedger(enabled=False, ct_val=1.0)
    ledger.note_open({"side": "long", "qty": 1.0}, seq=1, now_wall=0.0)
    ledger.accrue(_Snap(), now_wall=90000.0, mark_px=1000.0)
    ledger.note_close({}, 90000.0)
    assert ledger.stats(settled_usd=None) is None


# ---------------- F-8 / F-9：三态 basis 与 None≠0 ----------------
def _ledger_with_closed_accrual() -> FundingLedger:
    ledger = FundingLedger(enabled=True, ct_val=1.0)
    ledger.note_open({"side": "long", "qty": 1.0}, seq=1, now_wall=0.0)
    ledger.accrue(_Snap(), now_wall=30000.0, mark_px=1000.0)   # 计提 0.1
    ledger.note_close({}, 30001.0)
    return ledger


def test_basis_three_states():
    """F-8：settled 可得 / 仅 accrued / 全不可得，三态的 `fundingCostUsd` 各不相同。"""
    settled = _ledger_with_closed_accrual().stats(settled_usd=0.2)
    assert settled["basis"] == "settled+accrued"
    assert settled["fundingCostUsd"] == 0.2          # max(settled, accruedClosed)

    accrued = _ledger_with_closed_accrual().stats(settled_usd=None)
    assert accrued["basis"] == "accrued"
    assert accrued["fundingCostUsd"] == 0.1

    empty = FundingLedger(enabled=True, ct_val=1.0).stats(settled_usd=None)
    assert empty["basis"] == "unavailable"
    assert empty["fundingCostUsd"] is None


def test_none_and_zero_are_distinguishable_on_the_wire():
    """F-9：`fundingCostUsd is None`（没算成）≠ `0.0`（真的没跨过结算点）。"""
    unavailable = FundingLedger(enabled=True, ct_val=1.0).stats(settled_usd=None)
    assert unavailable["fundingCostUsd"] is None

    ledger = FundingLedger(enabled=True, ct_val=1.0)
    ledger.note_open({"side": "long", "qty": 1.0}, seq=1, now_wall=0.0)
    ledger.accrue(_Snap(), now_wall=1.0, mark_px=1000.0)    # 一个结算点都没跨
    crossed_none = ledger.stats(settled_usd=None)
    assert crossed_none["fundingCostUsd"] == 0.0
    assert crossed_none["basis"] == "accrued"


def test_open_segment_is_null_when_flat():
    """空仓 ⇒ `accruedOpenUsd` 为 `None`（不是 0：没有持仓段可言）。"""
    assert _ledger_with_closed_accrual().stats(settled_usd=None)["accruedOpenUsd"] is None


# ---------------- F-11：单调不减 ----------------
def test_funding_cost_never_goes_backwards_on_settlement_delay():
    """F-11 [P1-6]：本地已计提 0.1 → 平仓 → 交易所因结算延迟仍报 0.0 ⇒ 读数**不下降**。"""
    ledger = _ledger_with_closed_accrual()
    before = ledger.stats(settled_usd=None)["fundingCostUsd"]
    delayed = ledger.stats(settled_usd=0.0)["fundingCostUsd"]
    assert delayed >= before                       # max(settled, accruedClosed) 生效
    later = ledger.stats(settled_usd=0.13)["fundingCostUsd"]
    assert later >= delayed


def test_untrusted_settled_falls_back_to_accrued_with_a_reason():
    """三道门任一没过 ⇒ `settledTrusted is False`、`basis` 降级，且 note 说出是哪一道。"""
    stats = _ledger_with_closed_accrual().stats(settled_usd=None,
                                                unavailable_reason="no_creds")
    assert stats["settledTrusted"] is False
    assert stats["basis"] == "accrued"
    assert "模拟盘" in stats["note"]


def test_truncated_samples_reason_reaches_the_note():
    """F-12（P2-6）：样本触顶 100（可能被截断）⇒ 文案里说得出「截断」。"""
    stats = _ledger_with_closed_accrual().stats(settled_usd=None,
                                                unavailable_reason="truncated")
    assert "截断" in stats["note"]


# ---------------- F-10：回测曲线用真实结算时刻，不平铺 ----------------
def test_backtest_curve_uses_real_settlement_times_not_a_grid():
    """F-10：8h→4h→8h 的变周期序列 ⇒ `points_in` 逐点取真实 ts。"""
    rows = [{"fundingTs": 0, "fundingRate": 0.0001},
            {"fundingTs": 28800, "fundingRate": 0.0002},
            {"fundingTs": 43200, "fundingRate": 0.0003},      # 只隔 4h
            {"fundingTs": 72000, "fundingRate": 0.0004}]
    curve = BacktestFundingCurve(rows)
    assert curve.available
    assert curve.points_in(0, 72000) == [(28800, 0.0002), (43200, 0.0003),
                                         (72000, 0.0004)]


def test_backtest_curve_prefers_realized_rate_when_present():
    """`realizedRate` 是结算实际值，优先于预告的 `fundingRate`。"""
    curve = BacktestFundingCurve([{"fundingTs": 100, "fundingRate": 0.1,
                                   "realizedRate": 0.2},
                                  {"fundingTs": 200, "fundingRate": 0.1,
                                   "realizedRate": 0.3}])
    assert curve.points_in(0, 300) == [(100, 0.2), (200, 0.3)]


def test_empty_curve_is_unavailable():
    """取不到曲线 ⇒ `available=False` ⇒ 回测按「口径不可得」呈现（绝不按 0）。"""
    assert BacktestFundingCurve([]).available is False
    assert BacktestFundingCurve([{"fundingTs": 1, "fundingRate": 0.1}]).available is False


def test_accrue_bars_accrues_real_points_only():
    ledger = FundingLedger(enabled=True, ct_val=1.0)
    ledger.note_open({"side": "long", "qty": 2.0}, seq=1, now_wall=0.0)
    curve = BacktestFundingCurve([{"fundingTs": 100, "fundingRate": 0.001},
                                  {"fundingTs": 200, "fundingRate": 0.002}])
    ledger.accrue_bars(curve, from_ts=0, to_ts=300, mark_px=1000.0)
    assert ledger.crossings == 2
    assert round(ledger.epoch.accrued_usd, 6) == round(0.003 * 2000.0, 6)


def test_accrue_bars_is_a_no_op_without_a_curve():
    ledger = FundingLedger(enabled=True, ct_val=1.0)
    ledger.note_open({"side": "long", "qty": 1.0}, seq=1, now_wall=0.0)
    ledger.accrue_bars(BacktestFundingCurve([]), from_ts=0, to_ts=1000, mark_px=1.0)
    assert ledger.crossings == 0
    assert ledger.stats(settled_usd=None)["basis"] == "unavailable"


# ---------------- 合并 ----------------
def test_merge_funding_wires_takes_the_weakest_basis():
    """任一成员 `unavailable` ⇒ 合并结果 `unavailable` 且 `fundingCostUsd` 为 `None`。"""
    a = _ledger_with_closed_accrual().stats(settled_usd=0.2)
    b = FundingLedger(enabled=True, ct_val=1.0).stats(settled_usd=None)
    merged = merge_funding_wires([a, b])
    assert merged["basis"] == "unavailable"
    assert merged["fundingCostUsd"] is None


def test_merge_funding_wires_sums_when_all_members_have_values():
    a = _ledger_with_closed_accrual().stats(settled_usd=None)
    b = _ledger_with_closed_accrual().stats(settled_usd=None)
    merged = merge_funding_wires([a, b])
    assert merged["basis"] == "accrued"
    assert merged["fundingCostUsd"] == round(2 * a["fundingCostUsd"], 6)
    assert merged["crossings"] == a["crossings"] + b["crossings"]


def test_merge_funding_wires_of_nothing_is_none():
    assert merge_funding_wires([None, None]) is None
