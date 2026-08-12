"""成交时钟对齐（ai-trade-discipline-preset-and-integrity 轴 D / D-3；判据 G-5 / G-5b）。

被修的缺陷：非 K 线唤醒（定时 / 补跑 / 信号翻转）与盯市 tick 产出的成交，`ts` 取的是**墙钟秒**，
而 K 线驱动轮取的是**K 线开盘秒**。`CandleChart` 直接把 `trade.ts` 当 `UTCTimestamp` 画标记并按精确
ts 建索引，于是默认配置（唤醒 2 分钟 / K 线 1H）下绝大多数真实成交的标记落不到任何一根 K 线上。

**本文件的第一等公民是 `test_one_day_hk_phase_*`（G-5b）**：OKX 的 `12H` / `1D` 是港时（UTC+8）
开盘，`(wall // bar_seconds) * bar_seconds` 这种 UTC 整除分桶不但修不好 1D，还会把 K 线驱动轮自己
的 ts 从 16:00 系列改写成 00:00。故对齐必须是**锚点式**——相位由交易所给的那根确认根决定，不猜时区。
"""
from __future__ import annotations

from app.config import Settings
from app.engine.simulator import RiskParams
from app.live_auto.executor import LiveExecutor
from app.okx.accounts import AccountCredentials
from app.okx.types import BAR_SECONDS

_DAY = BAR_SECONDS["1D"]
# 港时（UTC+8）开盘的 1D 根：UTC 16:00 系列。2026-08-10 16:00:00 UTC。
_HK_DAILY_ANCHOR = 1_786_723_200
assert _HK_DAILY_ANCHOR % _DAY == 57_600, "夹具必须是非 UTC 相位（否则 G-5b 恒绿，用例白写）"


def _executor(**over) -> LiveExecutor:
    kwargs = {"creds": AccountCredentials("ak", "sk-000000", "pp-000000"), "account_id": 1,
              "inst_id": "BTC-USDT-SWAP", "td_mode": "cross", "pos_mode": "long_short_mode",
              "leverage": 3.0, "budget_usd": 1000.0, "risk": RiskParams(), "ct_val": 0.01,
              "lot_sz": 0.01, "min_sz": 0.01, "settings": Settings()}
    kwargs.update(over)
    return LiveExecutor(**kwargs)


# ============================ G-5b：1D 港时相位专项（v1 的 UTC 整除方案下必红）============================
def test_one_day_hk_phase_wake_trade_snaps_back_to_the_real_bar_open():
    """`bar="1D"`、锚 = 16:00 UTC，墙钟落在锚+3h ⇒ ts 必须等于锚，而**不是** UTC 00:00。"""
    ex = _executor(bar_seconds=_DAY, bar_anchor_ts=_HK_DAILY_ANCHOR)
    wall = _HK_DAILY_ANCHOR + 3 * 3600
    trade = ex._make_trade(wall, "long", 30_000.0, 1.0, pnl=0.0, reason="wake")
    assert trade["ts"] == _HK_DAILY_ANCHOR
    utc_floor = (wall // _DAY) * _DAY
    assert trade["ts"] != utc_floor, "UTC 整除分桶会落到 00:00，那不是任何一根真实 K 线"
    assert trade["wallTs"] == wall


def test_one_day_hk_phase_candle_round_ts_is_byte_identical():
    """同一断面下 K 线驱动轮（ts == 某根开盘秒）逐字节不变——这是本轴的零回归支点。"""
    ex = _executor(bar_seconds=_DAY, bar_anchor_ts=_HK_DAILY_ANCHOR)
    for bars_ahead in (0, 1, 5):
        bar_open = _HK_DAILY_ANCHOR + bars_ahead * _DAY
        trade = ex._make_trade(bar_open, "close", 30_000.0, 1.0, pnl=1.0, reason="candle")
        assert trade["ts"] == bar_open


def test_alignment_floors_backwards_for_walls_before_the_anchor():
    """墙钟早于锚点（暖机期刚拿到第一根就补记一笔）⇒ 向前 floor 到真实相位，绝不越到锚点之后。"""
    ex = _executor(bar_seconds=_DAY, bar_anchor_ts=_HK_DAILY_ANCHOR)
    trade = ex._make_trade(_HK_DAILY_ANCHOR - 3600, "close", 30_000.0, 1.0, pnl=0.0, reason="r")
    assert trade["ts"] == _HK_DAILY_ANCHOR - _DAY


# ============================ G-5：1H 主路径 + wallTs 单调 ============================
def test_interval_wake_trade_lands_on_the_owning_bar_open():
    hour = BAR_SECONDS["1H"]
    anchor = 1_786_723_200
    ex = _executor(bar_seconds=hour, bar_anchor_ts=anchor)
    wall = anchor + hour + 1_837          # 下一根内的任意一秒
    trade = ex._make_trade(wall, "long", 30_000.0, 1.0, pnl=0.0, reason="interval")
    assert (trade["ts"] - anchor) % hour == 0
    assert trade["ts"] == anchor + hour
    assert trade["wallTs"] == wall


def test_wall_ts_is_monotonic_while_ts_repeats_within_one_bar():
    """同一根内的多笔成交**共享 ts**（正是我们要的对齐），`wallTs` 仍严格单调 —— 排序 / 悬停 /
    导出一律改读 `wallTs`，时间倒挂由此消失。"""
    hour = BAR_SECONDS["1H"]
    anchor = 1_786_723_200
    ex = _executor(bar_seconds=hour, bar_anchor_ts=anchor)
    walls = [anchor + 30, anchor + 900, anchor + 3599]
    trades = [ex._make_trade(w, "close", 30_000.0, 1.0, pnl=0.0, reason="r") for w in walls]
    assert {t["ts"] for t in trades} == {anchor}
    assert [t["wallTs"] for t in trades] == walls


# ============================ 零回归：两条降级路径 ============================
def test_live_auto_without_bar_seconds_falls_back_verbatim():
    """`live_auto` 的 `_build_executor` 不传 `bar_seconds` ⇒ ts 为原 `int(ts)` 且**整键无 wallTs**。

    键集也必须逐字不变：`test_ai_trade_switches_never_leak_into_non_ai_executor` 冻结了非 AI
    executor 的成交 dict 键集，无条件多一个键就是一次非 AI 链路的 wire 变更（见 spec I-3）。
    """
    ex = _executor()
    assert ex.bar_seconds == 0 and ex.bar_anchor_ts == 0
    wall = 1_786_726_837
    trade = ex._make_trade(wall, "long", 30_000.0, 1.0, pnl=0.0, reason="r")
    assert trade["ts"] == wall and "wallTs" not in trade


def test_warmup_without_a_confirmed_bar_falls_back_to_wall_clock():
    """暖机期尚无确认根 ⇒ `bar_anchor_ts` 仍为 0 ⇒ 有意降级回墙钟（此时也确实没有落点可言）。"""
    ex = _executor(bar_seconds=BAR_SECONDS["1H"])
    wall = 1_786_726_837
    trade = ex._make_trade(wall, "long", 30_000.0, 1.0, pnl=0.0, reason="r")
    assert trade["ts"] == wall and trade["wallTs"] == wall


def test_align_is_a_pure_function_of_seconds_and_anchor():
    """`_align_bar_ts` 不读时钟、不读持仓——纯函数，故回测 / 单测可逐点对拍。"""
    ex = _executor(bar_seconds=300, bar_anchor_ts=1_000_000)
    assert ex._align_bar_ts(1_000_000) == 1_000_000
    assert ex._align_bar_ts(1_000_299) == 1_000_000
    assert ex._align_bar_ts(1_000_300) == 1_000_300
    assert ex._align_bar_ts(999_999) == 999_700


# ============================ 会话侧接线：锚点由确认根写入 ============================
async def test_session_writes_the_anchor_from_the_confirmed_bar():
    """G-5 接线端：每根已确认 K 线落地即把**它的开盘秒**写成锚点，唤醒轮成交据此归根。

    锚点绝不能取 `session._ref_ts()` 的无根回落值（`started_ts`，**非 bar 相位**）——那会把整条
    对齐链钉在一个假相位上。故 `_prime` 只在窗口非空时写锚（spec §3.4 的显式约束）。
    """
    from app.ai_auto.session import AiTradeSession
    from app.ai_auto.decider import AiTradeDecider
    from app.db.session import dispose_engine, init_db
    from app.okx.types import Candle
    from app.paper.strategy_factory import PaperStrategySpec
    from app.strategies.base import BaseStrategy, StrategyMeta

    class _Stub(BaseStrategy):
        meta = StrategyMeta(key="_stub_clock", name="Stub", description="t",
                            default_params={}, params_schema={})

        def warmup(self) -> int:
            return 1

        def on_candle(self, df, i, ctx):
            return []

    hour = BAR_SECONDS["1H"]
    bar_open = _HK_DAILY_ANCHOR            # 一个非 UTC 整点相位的整小时锚
    await init_db()
    try:
        settings = Settings()

        async def positions_fn():
            return []

        ex = _executor(bar_seconds=hour, settings=settings, positions_fn=positions_fn)
        assert ex.bar_anchor_ts == 0       # 暖机前无锚 ⇒ 成交回退墙钟
        session = AiTradeSession(
            "aitrade_clock", 1, [PaperStrategySpec(1, "Stub", "atomic", _Stub({}), {})],
            "BTC-USDT-SWAP", "1H", ex,
            AiTradeDecider(object(), "sys", context_refresh_rounds=50, settings=settings),
            priming=False, priming_bars=10, context_refresh_rounds=50)
        session._deciding = True           # 只测锚点写入，不起决策轮
        await session.feed_candle(Candle(ts=bar_open, o=30_000.0, h=30_010.0, l=29_990.0,
                                         c=30_000.0, vol=1.0, confirm=True))
        assert ex.bar_anchor_ts == bar_open
        # 该根内任意一秒发生的唤醒轮成交 ⇒ 归到这根的开盘秒
        trade = ex._make_trade(bar_open + 2_711, "long", 30_000.0, 1.0, pnl=0.0, reason="wake")
        assert trade["ts"] == bar_open and trade["wallTs"] == bar_open + 2_711
    finally:
        await dispose_engine()
