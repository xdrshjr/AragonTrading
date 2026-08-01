"""无副作用信号预演单测（ai-trade-wake-scheduler-closed-loop §6.1 T-9~T-11/T-15b/T-15c/T-25/G-8）。

覆盖本轴的四条不变式：
- **零污染**：预演跑十次，真身策略与影子 broker 逐字段不变（T-9）。
- **判据是投影立场**：排队成交场景**不得**报翻转、本根新边沿场景**必须**报翻转（T-10b，P0-1）。
  两条断言方向相反，任一实现走样都会红。
- **预演输入必须合法且新鲜**：重复 ts / 判定价陈旧一律不预演（T-15b，P0-2）。
- **在飞守卫不随超时解锁**：线程还在烧 CPU 时下一次投递必须被挡住（T-15c，P1-2）。

无网络、无 LLM、无 DB：只用真实 `momentum_breakout` + `SimBroker` + `MarketWindow`。
"""
from __future__ import annotations

import asyncio
import copy
import threading
import time

import pytest

from app.ai_auto import signal_preview
from app.ai_auto.market_window import MarketWindow
from app.ai_auto.signal_preview import (
    PreviewRequest,
    PreviewRunner,
    PreviewSnapshot,
    clone_for_preview,
    clone_shadow,
    flip_signature,
    projected_stance,
    run_preview,
)
from app.ai_auto.signals import evaluate_signal_board, make_shadow_broker
from app.config import Settings
from app.paper.strategy_factory import PaperStrategySpec
from app.strategies.base import BaseStrategy, StrategyMeta
from app.strategies.momentum_breakout import MomentumBreakout

_BAR = "15m"
_STEP = 900
_START_TS = 1_785_600_000
_WINDOW = 20            # momentum_breakout 的 breakout_window 默认值
_QUIET_ROWS = 32        # > warmup(=22)，且给 prior_high / vol_mean 留满窗口


def _row(idx: int, *, close: float = 100.0, high: float | None = None,
         low: float | None = None, vol: float = 100.0) -> dict:
    return {"ts": _START_TS + idx * _STEP, "o": close, "h": high if high is not None else close,
            "l": low if low is not None else close, "c": close, "vol": vol}


def _quiet_rows(count: int = _QUIET_ROWS) -> list[dict]:
    """一段完全静止的行情：既不越 prior_high、也不放量 ⇒ 策略恒无信号。"""
    return [_row(i) for i in range(count)]


def _surge_row(idx: int) -> dict:
    """一根必然触发 `open_long` 的暴涨放量根（c > prior_high、累计涨幅 10% > 3%、量 10× > 1.5×）。"""
    return _row(idx, close=110.0, high=110.0, low=100.0, vol=1000.0)


def _spec(strategy_id: int = 7) -> PaperStrategySpec:
    return PaperStrategySpec(strategy_id, "暴涨动量突破", "atomic", MomentumBreakout({}), {})


def _frames(specs: list, rows: list[dict]) -> list:
    from app.ai_auto.market_window import build_frames_for
    return build_frames_for(specs, rows, {}, _BAR)


def _step_through(specs: list, shadows: list, rows: list[dict]) -> list:
    """把 rows 逐根喂进真身影子，返回**最后一根**的信号板（= 会话里的 `_last_board`）。"""
    frames = _frames(specs, rows)
    board = []
    for i in range(len(rows)):
        board = evaluate_signal_board(specs, shadows, frames, i)
    return board


def _snapshot(rows: list[dict], live_row: dict) -> PreviewSnapshot:
    return PreviewSnapshot(rows=[*rows, live_row], htf_rows={}, bar=_BAR,
                           row_key=(live_row["ts"], live_row["c"], live_row["h"], live_row["l"]),
                           bar_ts=int(live_row["ts"]))


@pytest.fixture(autouse=True)
def _reset_global_concurrency():
    """模块级并发计数是进程共享态：每个用例前后归零，避免用例之间互相污染。"""
    signal_preview._inflight_global = 0
    yield
    signal_preview._inflight_global = 0


# ============================ T-10b（P0-1）：判据必须是投影立场 ============================
def test_queued_fill_from_the_previous_bar_is_not_reported_as_a_flip():
    """场景 ①：上一确认根已喊 `open_long`（`_pending` 非空），本未确认根无新边沿 ⇒ **无翻转**。

    若实现误用裸 `stance`，此处会返回一个 `flat>long` 的翻转——而那件事是**确定性**的（本根一开盘
    就已成交、整根不再变），签名恒定必过防抖 ⇒ 每一根有排队单的 K 线都会多烧一次 LLM，直接违反
    花费中性（A-4）。
    """
    specs, shadows = [_spec()], [make_shadow_broker()]
    rows = [*_quiet_rows(), _surge_row(_QUIET_ROWS)]
    base = _step_through(specs, shadows, rows)
    assert (base[0].stance, base[0].latest_signal) == ("flat", "open_long")   # 排队中，尚未成交
    assert shadows[0]._pending, "夹具前提：上一确认根必须留下一张排队单"

    clones = clone_for_preview(specs, shadows)
    preview = run_preview(clones[0], clones[1], _snapshot(rows, _row(_QUIET_ROWS + 1, close=111.0)))
    assert preview[0].stance == "long"            # 裸 stance 看：flat → long，会误报翻转
    assert projected_stance(base[0]) == projected_stance(preview[0]) == "long"
    assert flip_signature(base, preview) is None


def test_a_brand_new_edge_on_the_unconfirmed_bar_is_reported_as_a_flip():
    """场景 ②：上一确认根无信号、本未确认根 `on_candle` 喊 `open_long` ⇒ **必须**报翻转。

    若实现误用裸 `stance`，此处恒为 None（`submit` 只排队、`stance` 不动）⇒ 整个轴 C 对它最想抓的
    东西完全失明。本用例与上一条方向相反，任一走样都会红。
    """
    specs, shadows = [_spec()], [make_shadow_broker()]
    rows = _quiet_rows()
    base = _step_through(specs, shadows, rows)
    assert (base[0].stance, base[0].latest_signal) == ("flat", "none")

    clones = clone_for_preview(specs, shadows)
    preview = run_preview(clones[0], clones[1], _snapshot(rows, _surge_row(_QUIET_ROWS)))
    assert preview[0].stance == "flat"            # 裸 stance 看：flat → flat，会完全失明
    assert preview[0].latest_signal == "open_long"
    matched = flip_signature(base, preview)
    assert matched is not None
    signature, wire = matched
    assert wire["netStanceFrom"] == "flat" and wire["netStanceTo"] == "long"
    assert wire["provisional"] is True
    assert wire["strategies"] == [{"strategyId": 7, "name": "暴涨动量突破",
                                   "from": "flat", "to": "long"}]
    assert signature == "flat>long|7:flat>long"


# ============================ T-9：零污染（本轴的核心不变式）============================
def test_ten_previews_leave_the_real_strategy_and_shadow_byte_identical():
    """预演十次，真身策略内部状态与影子 broker **逐字段不变**；同时证明克隆体确实被步进过。

    第二条断言不可省：没有它，一个「什么都没跑」的实现也会绿。
    """
    specs, shadows = [_spec()], [make_shadow_broker()]
    rows = [*_quiet_rows(), _surge_row(_QUIET_ROWS)]
    _step_through(specs, shadows, rows)
    before_state = copy.deepcopy((specs[0].strategy._peak, specs[0].strategy._trough))
    before_pos = copy.deepcopy(shadows[0].pos)
    before_pending = copy.deepcopy(shadows[0]._pending)
    before_trades = len(shadows[0].trades)

    stepped = False
    for step in range(10):
        clones = clone_for_preview(specs, shadows)
        board = run_preview(clones[0], clones[1],
                            _snapshot(rows, _row(_QUIET_ROWS + 1, close=111.0 + step)))
        stepped = stepped or board[0].stance == "long"      # 克隆体真的走了一步（排队单已成交）
    assert stepped, "克隆体必须真的被步进过，否则本用例会因为「什么都没跑」而假绿"
    assert (specs[0].strategy._peak, specs[0].strategy._trough) == before_state
    assert shadows[0].pos == before_pos
    assert shadows[0]._pending == before_pending
    assert len(shadows[0].trades) == before_trades


# ============================ T-10：签名稳定性 ============================
def test_signature_is_stable_and_independent_of_board_iteration_order():
    """同一组翻转两次调用得到相同字符串，且与策略遍历顺序无关（防抖去重靠它）。"""
    specs = [_spec(9), _spec(3)]
    shadows = [make_shadow_broker(), make_shadow_broker()]
    rows = _quiet_rows()
    base = _step_through(specs, shadows, rows)
    clones = clone_for_preview(specs, shadows)
    preview = run_preview(clones[0], clones[1], _snapshot(rows, _surge_row(_QUIET_ROWS)))
    first = flip_signature(base, preview)[0]
    assert first == flip_signature(base, preview)[0]
    assert flip_signature(list(reversed(base)), list(reversed(preview)))[0] == first
    assert first == "flat>long|3:flat>long,9:flat>long"


def test_a_single_strategy_flip_that_does_not_move_net_consensus_is_not_a_flip():
    """只看**净共识**立场：单策略投影变了但净立场未变 ⇒ None。

    改成「任一策略立场变了」在多策略板上几乎每根未确认根都成立，会把唤醒打成事实上的流式，
    比现状更糟。
    """
    from app.ai_auto.signals import StrategySignalView
    from app.engine.simulator import RiskParams

    def _view(sid: int, stance: str, latest: str = "none") -> StrategySignalView:
        return StrategySignalView(strategy_id=sid, name=f"S{sid}", stance=stance,
                                  latest_signal=latest, strength=0.0, reason="",
                                  risk_params=RiskParams())

    base = [_view(1, "long"), _view(2, "short"), _view(3, "long")]
    preview = [_view(1, "long"), _view(2, "short", "open_long"), _view(3, "long")]
    assert flip_signature(base, preview) is None            # 净立场 long→long
    flipped = [_view(1, "long", "close"), _view(2, "short"), _view(3, "long", "close")]
    assert flip_signature(base, flipped) is not None        # 净立场 long→short


# ============================ T-11：克隆失败即整轴降级 ============================
class _UnclonableStrategy(BaseStrategy):
    """持有一把线程锁的策略——`copy.deepcopy` 对它必然抛（真实策略也可能持有客户端 / 锁）。"""

    meta = StrategyMeta(key="_unclonable_preview", name="Unclonable", description="t",
                        default_params={}, params_schema={})

    def __init__(self, params: dict | None = None):
        super().__init__(params)
        self._lock = threading.Lock()

    def warmup(self) -> int:
        return 1

    def on_candle(self, df, i, ctx):
        return []


def test_clone_failure_returns_none_and_never_raises():
    """fail-open：一个观测特性没有资格弄坏交易，克隆不了就整轴不生效。"""
    specs = [PaperStrategySpec(1, "U", "atomic", _UnclonableStrategy({}), {})]
    assert clone_for_preview(specs, [make_shadow_broker()]) is None


async def test_repeated_clone_failures_disable_the_axis_and_emit_one_alert():
    """连续失败即自动关轴 + 一条**可见**降级告警（静默降级会被当成故障）。"""
    runner = PreviewRunner("aitrade_a", _settings())
    specs = [PaperStrategySpec(1, "U", "atomic", _UnclonableStrategy({}), {})]
    window = _window_with_rows(_quiet_rows())
    now = time.time()
    for _ in range(3):
        assert await runner.run(PreviewRequest(specs=specs, shadows=[make_shadow_broker()],
                                               window=window, live_row=_row(_QUIET_ROWS),
                                               now_wall=now, last_px_wall=now)) is None
    assert runner.enabled is False
    alerts = runner.take_alerts()
    assert [a["data"]["code"] for a in alerts] == ["AI_TRADE_SIGNAL_WAKE_DISABLED"]
    assert runner.take_alerts() == []            # 一次性推送，绝不每 tick 刷屏


# ============================ T-25：有界克隆等价门禁 ============================
def test_bounded_clone_matches_full_deepcopy_step_for_step():
    """同一初始 broker：`clone_shadow` 与 `copy.deepcopy` 跑 K=20 步，stance 轨迹逐步相等。

    没有这条，「只复制会影响 stance 的字段」就只是一句声明。
    """
    specs, shadows = [_spec()], [make_shadow_broker()]
    rows = [*_quiet_rows(), _surge_row(_QUIET_ROWS)]
    _step_through(specs, shadows, rows)
    tail = [_row(_QUIET_ROWS + 1 + k, close=110.0 + k, high=111.0 + k, low=105.0 + k,
                 vol=200.0) for k in range(20)]
    frames = _frames(specs, [*rows, *tail])

    bounded, full = clone_shadow(shadows[0]), copy.deepcopy(shadows[0])
    bounded_specs, full_specs = copy.deepcopy(specs), copy.deepcopy(specs)
    for k in range(20):
        i = len(rows) + k
        left = evaluate_signal_board(bounded_specs, [bounded], frames, i)[0]
        right = evaluate_signal_board(full_specs, [full], frames, i)[0]
        assert (left.stance, left.latest_signal) == (right.stance, right.latest_signal), f"步 {k}"
    assert bounded.pos == full.pos
    assert bounded.cash == pytest.approx(full.cash)
    assert bounded.fees_paid == pytest.approx(full.fees_paid)


# ============================ T-15b（P0-2）：预演输入的两道前置闸 ============================
def _window_with_rows(rows: list[dict]) -> MarketWindow:
    window = MarketWindow("BTC-USDT-SWAP", _BAR, [_spec()], 40)
    for row in rows:
        window.prime_append(row)
    return window


def test_preview_snapshot_rejects_a_duplicate_timestamp_row():
    """闸 ①：`live_row.ts <= last_confirmed_ts` ⇒ None（帧里绝不出现两根同 ts）。"""
    rows = _quiet_rows()
    window = _window_with_rows(rows)
    now = time.time()
    dup = dict(rows[-1])
    assert window.preview_snapshot(dup, now_wall=now, last_px_wall=now,
                                   stale_after_s=90.0) is None
    fresh = _row(_QUIET_ROWS, close=101.0)
    snapshot = window.preview_snapshot(fresh, now_wall=now, last_px_wall=now, stale_after_s=90.0)
    assert snapshot is not None and len(snapshot.rows) == len(rows) + 1
    assert snapshot.index == len(rows)
    assert snapshot.row_key == (fresh["ts"], 101.0, 101.0, 101.0)


def test_preview_snapshot_rejects_a_stale_decision_price():
    """闸 ②：判定价太旧 ⇒ None。**必须读 `_last_px_wall`**（每次价格推送即刷新）。

    读 `_last_candle_wall`（只在确认根更新）会让 12H/1D 执行周期下 WS 静默时 `_live_row` 冻结不动，
    而防抖器仍按墙钟累积「已连续保持 45 秒」——零新数据凑出一条假证据。
    """
    window = _window_with_rows(_quiet_rows())
    now = time.time()
    fresh = _row(_QUIET_ROWS, close=101.0)
    assert window.preview_snapshot(fresh, now_wall=now, last_px_wall=now - 91.0,
                                   stale_after_s=90.0) is None
    assert window.preview_snapshot(fresh, now_wall=now, last_px_wall=0.0,
                                   stale_after_s=90.0) is None
    assert window.preview_snapshot(None, now_wall=now, last_px_wall=now,
                                   stale_after_s=90.0) is None
    assert window.preview_snapshot(fresh, now_wall=now, last_px_wall=now - 89.0,
                                   stale_after_s=90.0) is not None


# ============================ T-15c / G-8：在飞守卫与并发闸 ============================
def _settings(**overrides) -> Settings:
    values = dict(ai_trade_signal_wake_enabled=True,
                  ai_trade_signal_wake_preview_interval_s=15.0,
                  ai_trade_signal_wake_timeout_s=0.05)
    values.update(overrides)
    return Settings(**values)


def _busy_preview(hold_s: float):
    """**GIL 密集**的假预演：`sleep` 会释放 GIL，用它测不到真问题（P1-4）。"""
    def _fake(specs, shadows, snapshot):
        end = time.monotonic() + hold_s
        spins = 0
        while time.monotonic() < end:
            spins += 1
        return []
    return _fake


async def _wait_until_free(runner: PreviewRunner, timeout_s: float = 5.0) -> None:
    deadline = time.monotonic() + timeout_s
    while runner._inflight and time.monotonic() < deadline:
        await asyncio.sleep(0.02)


async def test_inflight_guard_is_not_released_by_a_timeout(monkeypatch):
    """超时**不得**解锁在飞守卫：`wait_for` 取消的是 future，线程本身仍在烧 CPU。

    照「`finally` 里清标志」的写法，此处第二次 `run()` 会照常投递第二个线程——与「同一时刻至多
    一个预演」直接矛盾。
    """
    monkeypatch.setattr(signal_preview, "run_preview", _busy_preview(0.6))
    runner = PreviewRunner("aitrade_a", _settings())
    window = _window_with_rows(_quiet_rows())
    now = time.time()
    request = PreviewRequest(specs=[_spec()], shadows=[make_shadow_broker()], window=window,
                             live_row=_row(_QUIET_ROWS), now_wall=now, last_px_wall=now)
    assert await runner.run(request) is None                  # 超时
    assert runner._inflight is True                           # 线程还在跑 ⇒ 守卫仍生效
    assert runner.due(time.time()) is False                   # 下一 tick 直接不予投递
    assert runner.stats()["previewTimeouts"] == 1
    await _wait_until_free(runner)
    assert runner._inflight is False                          # 线程真的结束才解锁


async def test_timeout_backs_off_the_preview_interval(monkeypatch):
    """超时即退避（间隔翻倍，上限 300s）：连续失败等价于自动降频，而不是每 tick 白烧。"""
    monkeypatch.setattr(signal_preview, "run_preview", _busy_preview(0.3))
    runner = PreviewRunner("aitrade_b", _settings())
    window = _window_with_rows(_quiet_rows())
    now = time.time()
    await runner.run(PreviewRequest(specs=[_spec()], shadows=[make_shadow_broker()],
                                    window=window, live_row=_row(_QUIET_ROWS),
                                    now_wall=now, last_px_wall=now))
    assert runner._interval_s == pytest.approx(30.0)
    assert runner.due(now + 20.0) is False                    # 退避期内不再投递
    await _wait_until_free(runner)


async def test_module_level_concurrency_cap_skips_instead_of_queueing(monkeypatch):
    """G-8：同一 tick 三个会话都到期 ⇒ 至多 2 个进入预演，第三个**立即**返回 None。

    绝不排队等：排队等于把一个会话的盯市 tick 挂在别的会话身上。
    """
    monkeypatch.setattr(signal_preview, "run_preview", _busy_preview(0.4))
    window = _window_with_rows(_quiet_rows())
    now = time.time()

    def _request() -> PreviewRequest:
        return PreviewRequest(specs=[_spec()], shadows=[make_shadow_broker()], window=window,
                              live_row=_row(_QUIET_ROWS), now_wall=now, last_px_wall=now)

    runners = [PreviewRunner(sid, _settings(ai_trade_signal_wake_timeout_s=1.5))
               for sid in ("aitrade_a", "aitrade_b", "aitrade_c")]
    first = asyncio.ensure_future(runners[0].run(_request()))
    second = asyncio.ensure_future(runners[1].run(_request()))
    await asyncio.sleep(0.05)                                 # 让前两个真的投出去
    assert signal_preview._inflight_global == 2
    assert await runners[2].run(_request()) is None           # 第三个立即跳过
    assert runners[2]._inflight is False
    await asyncio.gather(first, second)
    for runner in runners:
        await _wait_until_free(runner)


def test_first_due_is_staggered_by_a_stable_hash_of_the_session_id():
    """按 `session_id` 的**稳定**哈希错峰（crc32，非内置 `hash`——后者按进程随机化，不可复现）。"""
    offsets = [PreviewRunner(sid, _settings()).offset_s
               for sid in ("aitrade_a", "aitrade_b", "aitrade_c")]
    assert len(set(offsets)) == 3
    assert offsets == [6.0, 0.0, 7.0]
    assert PreviewRunner("aitrade_a", _settings()).offset_s == offsets[0]


def test_disabled_axis_never_becomes_due_so_nothing_is_ever_cloned():
    """A-7：总闸关 ⇒ `due()` 恒 False ⇒ 一次都不构造克隆、不投递线程（零成本空转）。"""
    runner = PreviewRunner("aitrade_a", Settings(ai_trade_signal_wake_enabled=False))
    assert runner.enabled is False
    assert runner.due(time.time() + 10_000) is False
