"""退出来源归因纯核心（ai-trade-horizon-acceptance-loop 轴 B · §6.1 E-1~E-19）。

枚举的每一个可达来源各一条用例（ai-trade-exit-priority-and-commitment 新增第十值
`agentOverride`——它由**会话侧的一次性打标槽**产出而非分类器，故用例在
`test_ai_trade_exit_session.py` 与 `test_ai_trade_exit_freeze.py`）。守的三条：
- **实盘的 oco / external 之别必须来自机器可读键**，绝不解析中文文案（展示层随时会改）；
- **`session_end` 归 `windowEnd` 而不是 `unknown`**：`trend_hold` 下每次回测必有一笔，落进
  「未记录来源」会让回测的退出分布凭空多一类（[P1-8]）；
- **`hold_seconds is None` 的样本不进时长均值**，但照样进笔数与净额（用 0 冒充会把平均持仓
  时长拉向 0）。
"""
from app.ai_auto.exit_attribution import (
    EXIT_SOURCES,
    ExitLedger,
    classify_backtest,
    classify_live,
    exit_counters,
    is_deterministic_reason,
)
from app.live_auto.explain import (
    build_atomic_explain,
    build_external_close_explain,
    build_profit_guard_explain,
    build_reverse_explain,
    build_risk_exit_explain,
    build_session_stop_explain,
)


def _external(source: str) -> dict:
    return build_external_close_explain(pos_side="long", source=source, entry_price=100.0,
                                        exit_price=101.0, pnl=1.0)


# ---------------- E-1~E-8：实盘八个可达枚举 ----------------
def test_agent_close_is_classified_from_atomic_explain():
    """E-1：Agent 主动平仓（`kind == "atomic"` 且 `side == "close"`）。"""
    assert classify_live(build_atomic_explain("close", "模型主动平仓")) == "agent"


def test_atomic_open_leg_has_no_exit_source():
    """开仓腿没有「退出来源」可言 ⇒ `unknown`（打标点也只给 close 打）。"""
    assert classify_live(build_atomic_explain("long", "开多")) == "unknown"


def test_agent_reverse_is_its_own_source():
    """E-2：反手的平仓腿是**换方向**而不是离场，与主动平仓分列。"""
    explain = build_reverse_explain(pos_side="long", entry_price=100.0, exit_price=99.0,
                                    pnl=-1.0)
    assert classify_live(explain) == "agentReverse"


def test_profit_guard_close_is_classified():
    """E-3：利润保护层平仓——本轮 prompt 偏置矫正的效果就靠这一类占比是否上升来验收。"""
    explain = build_profit_guard_explain(pos_side="long", trigger_label="移动止盈",
                                         entry_price=100.0, exit_price=105.0,
                                         peak_pnl=6.0, giveback_ratio=0.2, pnl=5.0)
    assert classify_live(explain) == "profitGuard"


def test_exchange_oco_and_external_are_split_by_a_machine_readable_key():
    """E-4 / E-7：oco 与 external 靠**顶层 `source` 键**分流，绝不解析中文 metric 文案。"""
    assert classify_live(_external("oco")) == "exchangeOco"
    assert classify_live(_external("external")) == "external"


def test_external_close_explain_carries_the_source_key():
    """加法键必须真的在 wire 上（否则分类器只能回退去猜）。"""
    assert _external("oco")["source"] == "oco"


def test_time_exit_and_session_stop_are_classified():
    """E-5 / E-6：强制时间平仓与会话级止盈损。"""
    risk = build_risk_exit_explain(bars_held=10, max_hold_bars=10, entry_price=100.0,
                                   exit_price=101.0, pnl=1.0)
    stop = build_session_stop_explain(pos_side="long", entry_price=100.0,
                                      exit_price=101.0, pnl=1.0, session_pnl=5.0)
    assert classify_live(risk) == "timeExit"
    assert classify_live(stop) == "sessionStop"


def test_missing_or_unknown_explain_is_unknown():
    """E-8：无 explain / 未知 kind / 本特性上线前的历史成交 ⇒ `unknown`（一等公民）。"""
    assert classify_live(None) == "unknown"
    assert classify_live({}) == "unknown"
    assert classify_live({"kind": "brand_new_kind"}) == "unknown"


# ---------------- E-9~E-13 / E-19：回测侧 ----------------
def test_backtest_deterministic_reasons_map_without_a_stamp():
    """E-9：`SimBroker` 写死的成交理由不经运行时，槽为空也能定源。"""
    assert classify_backtest("stop_loss", stamped=None) == "exchangeOco"
    assert classify_backtest("take_profit", stamped=None) == "exchangeOco"
    assert classify_backtest("max_hold", stamped=None) == "timeExit"
    assert classify_backtest("session_take_profit", stamped=None) == "sessionStop"
    assert classify_backtest("session_stop_loss", stamped=None) == "sessionStop"


def test_backtest_uses_the_stamp_when_reason_is_not_deterministic():
    """E-10：运行时自己提交的平仓（保护层 / 主动 / 反手）靠打标槽定源。"""
    assert classify_backtest("profit_guard_trail", stamped="profitGuard") == "profitGuard"
    assert classify_backtest("ai_close", stamped="agent") == "agent"
    assert classify_backtest("ai_reverse", stamped="agentReverse") == "agentReverse"


def test_deterministic_reason_wins_over_a_stale_stamp():
    """E-11（R-6）：槽非空但 reason 命中确定性字面量 ⇒ **以 reason 为准**。"""
    assert classify_backtest("stop_loss", stamped="agent") == "exchangeOco"
    assert is_deterministic_reason("stop_loss") is True
    assert is_deterministic_reason("ai_close") is False


def test_empty_stamp_and_unknown_reason_is_unknown():
    """E-12：槽空且 reason 不在冻结表里 ⇒ `unknown`（不猜）。"""
    assert classify_backtest("who_knows", stamped=None) == "unknown"


def test_garbage_stamp_is_ignored():
    """E-13：槽里是一个不在枚举里的值 ⇒ 不采纳（枚举是稳定契约）。"""
    assert classify_backtest("who_knows", stamped="not_a_source") == "unknown"


def test_session_end_is_window_end_not_unknown():
    """E-19 [P1-8]：回测区间收尾平仓归 `windowEnd`，且它在实盘分类器上**永不产出**。"""
    assert classify_backtest("session_end", stamped=None) == "windowEnd"
    assert "windowEnd" in EXIT_SOURCES
    # 实盘会话停止走 session_stop；没有任何 explain kind 会映射到 windowEnd。
    assert classify_live({"kind": "session_stop"}) == "sessionStop"


# ---------------- ExitLedger ----------------
def _ledger(net_mode: bool = True) -> ExitLedger:
    return ExitLedger(net_mode=net_mode)


def test_win_rate_needs_five_samples():
    """E-14：`count < 5` ⇒ `winRate is None` ⇒ 前端显示「样本不足」。"""
    ledger = _ledger()
    for _ in range(4):
        ledger.record({"pnl": 1.0}, hold_seconds=100.0, source="agent")
    row = ledger.stats()["bySource"][0]
    assert row["count"] == 4 and row["winRate"] is None
    ledger.record({"pnl": 1.0}, hold_seconds=100.0, source="agent")
    assert ledger.stats()["bySource"][0]["winRate"] == 1.0


def test_by_source_order_is_stable():
    """E-15：count 降序 → 同数按枚举序（否则每次刷新条目会跳）。"""
    ledger = _ledger()
    for _ in range(3):
        ledger.record({"pnl": 1.0}, hold_seconds=1.0, source="profitGuard")
    for _ in range(5):
        ledger.record({"pnl": -1.0}, hold_seconds=1.0, source="agent")
    ledger.record({"pnl": 1.0}, hold_seconds=1.0, source="agentReverse")
    ledger.record({"pnl": 1.0}, hold_seconds=1.0, source="timeExit")
    sources = [row["source"] for row in ledger.stats()["bySource"]]
    assert sources == ["agent", "profitGuard", "agentReverse", "timeExit"]


def test_all_unknown_still_produces_stats():
    """E-16：全部样本是 `unknown` ⇒ 后端照常出数（**前端**负责不渲染那张表）。"""
    ledger = _ledger()
    ledger.record({"pnl": 1.0}, hold_seconds=None, source="unknown")
    stats = ledger.stats()
    assert stats["totalExits"] == 1
    assert stats["bySource"][0]["source"] == "unknown"


def test_missing_hold_seconds_does_not_pollute_the_average():
    """E-17：`hold_seconds is None` 的样本进笔数与净额，**不进时长均值**。"""
    ledger = _ledger()
    ledger.record({"pnl": 1.0}, hold_seconds=100.0, source="agent")
    ledger.record({"pnl": 2.0}, hold_seconds=None, source="agent")
    row = ledger.stats()["bySource"][0]
    assert row["count"] == 2
    assert row["netUsd"] == 3.0
    assert row["avgHoldSeconds"] == 100.0        # 不是 50.0


def test_net_mode_matches_the_attribution_rule():
    """E-18：两种口径下的 `netUsd` 与 `attribution.record_close` 的还原规则逐位相等。"""
    trade = {"pnl": 10.0, "fee": 2.0}
    net_ledger = _ledger(net_mode=True)
    gross_ledger = _ledger(net_mode=False)
    net_ledger.record(trade, hold_seconds=1.0, source="agent")
    gross_ledger.record(trade, hold_seconds=1.0, source="agent")
    assert net_ledger.stats()["bySource"][0]["netUsd"] == 10.0
    assert gross_ledger.stats()["bySource"][0]["netUsd"] == 8.0


def test_empty_ledger_returns_none():
    assert _ledger().stats() is None


def test_truncated_samples_counts_only_boundary_sources():
    """[P1-8]：`windowEnd` / `sessionStop` 的时长样本被截断，须单独计数。"""
    ledger = _ledger()
    ledger.record({"pnl": 1.0}, hold_seconds=10.0, source="agent")
    ledger.record({"pnl": 1.0}, hold_seconds=10.0, source="windowEnd")
    ledger.record({"pnl": 1.0}, hold_seconds=10.0, source="sessionStop")
    ledger.record({"pnl": 1.0}, hold_seconds=None, source="windowEnd")
    assert ledger.truncated_samples() == 2       # 只数进了时长均值的那两笔


def test_source_falls_back_to_explain_when_not_given():
    """实盘路径不显式传 source ⇒ 由 `explain` 现判（与打标点同一个纯函数）。"""
    ledger = _ledger()
    ledger.record({"pnl": 1.0, "explain": build_atomic_explain("close", "平")},
                  hold_seconds=5.0)
    assert ledger.stats()["bySource"][0]["source"] == "agent"


def test_from_counters_round_trips_through_exit_counters():
    """合并落点：`exit_counters` → `from_counters` → `stats()` 与原账本逐键相等。"""
    ledger = _ledger()
    for _ in range(6):
        ledger.record({"pnl": 1.0}, hold_seconds=10.0, source="agent")
    ledger.record({"pnl": -1.0}, hold_seconds=None, source="profitGuard")
    rebuilt = ExitLedger.from_counters(exit_counters(ledger))
    assert rebuilt.stats() == ledger.stats()


def test_exit_counters_of_none_is_empty():
    assert exit_counters(None)["counts"] == {}
