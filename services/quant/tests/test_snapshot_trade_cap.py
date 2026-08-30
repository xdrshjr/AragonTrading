"""快照成交序列的 wire 级封顶（resilience-and-hci-hardening D4）。

三条会话流的 `snapshot()` 在**每一次 WS 连接与重连**时都会整段重放成交；此前它是无界的，
长跑会话的重放载荷与前端内存都会持续劣化。本文件钉住两件事：

1. **下发**封顶在 `SNAPSHOT_TRADES_MAX`（末尾即最新）；
2. **真值列一条不少**——`LiveExecutor.trades` / `SimBroker.trades` 是换手闸、run metrics 与成本
   对账的会计真值，裁它等于静默改变交易行为。这是本轮的红线，故第 2 条是独立断言而不是附带检查。

`snapshot()` 用**未绑定方法 + 替身 self** 驱动：真会话的构造要拉起 OKX 客户端、DB 与策略工厂，
那些与「切尾切得对不对」毫无关系，拖进来只会引入一条新的顺序依赖。
"""
from __future__ import annotations

from types import SimpleNamespace

from app.ai_auto.session import AiTradeSession
from app.live_auto.session import LiveAutoSession
from app.paper.session import PaperSession
from app.wire_caps import SNAPSHOT_TRADES_MAX, tail_trades

OVERFLOW = SNAPSHOT_TRADES_MAX + 50


def _dict_trades(count: int) -> list[dict]:
    """live/ai 两侧的成交是裸 dict（`LiveExecutor.trades` 的元素形状）。"""
    return [{"ts": idx, "side": "buy", "price": 100.0 + idx} for idx in range(count)]


def _broker_trades(count: int) -> list[SimpleNamespace]:
    """paper 侧的成交是 `SimBroker` 的 Trade 对象，经 `_trade_record_out` 转 wire dict。"""
    return [SimpleNamespace(ts=idx, side="buy", price=100.0 + idx, qty=1.0, fee=0.0, pnl=0.0,
                            position_after=1.0, reason="entry", stop_loss_pct=None,
                            take_profit_pct=None, max_hold_bars=None, stop_loss_price=None,
                            take_profit_price=None)
            for idx in range(count)]


def test_tail_trades_keeps_the_newest_and_never_lends_out_the_source():
    source = _dict_trades(OVERFLOW)
    tail = tail_trades(source)
    assert len(tail) == SNAPSHOT_TRADES_MAX
    assert tail[-1] is source[-1]                       # 末尾即最新
    assert tail is not source
    tail.append({"ts": -1})                             # 对返回值的写入不得回灌真值列
    assert len(source) == OVERFLOW


def test_tail_trades_passes_short_lists_through_as_a_copy():
    source = _dict_trades(3)
    tail = tail_trades(source)
    assert tail == source and tail is not source


def test_ai_trade_snapshot_caps_the_wire_but_not_the_ledger():
    executor = SimpleNamespace(trades=_dict_trades(OVERFLOW))
    fake = SimpleNamespace(
        executor=executor,
        # `inst_id`：`snapshot()` 的第八键 `intel` 用它去查进程内快照（market-intel §3.7.3）。
        # 替身 self 必须携带 `snapshot()` **实际读到的**每一个字段——这份替身就是那份契约的注册表，
        # 少一个字段的症状是 AttributeError，而不是「切尾切错了」。
        inst_id="BTC-USDT-SWAP",
        _equity=[],
        _decisions=[],
        _traces=[],
        decider=SimpleNamespace(summary=None),
        _ref_px=lambda: 100.0,
        _position_payload=lambda mark: None,
        _status_payload=lambda: {"state": "running"},
    )
    snap = AiTradeSession.snapshot(fake)
    assert len(snap["trades"]) == SNAPSHOT_TRADES_MAX
    assert snap["trades"][-1]["ts"] == OVERFLOW - 1
    assert len(executor.trades) == OVERFLOW             # 红线：会计真值一条不少


def test_live_auto_snapshot_caps_the_wire_but_not_the_ledger():
    executor = SimpleNamespace(trades=_dict_trades(OVERFLOW), entry_price=100.0,
                               position_payload=lambda mark: None)
    fake = SimpleNamespace(
        executor=executor,
        _equity=[],
        _candles=[],
        _status_payload=lambda: {"state": "running"},
    )
    snap = LiveAutoSession.snapshot(fake)
    assert len(snap["trades"]) == SNAPSHOT_TRADES_MAX
    assert snap["trades"][-1]["ts"] == OVERFLOW - 1
    assert len(executor.trades) == OVERFLOW             # 红线：会计真值一条不少


def test_paper_snapshot_caps_the_wire_but_not_the_ledger():
    broker = SimpleNamespace(trades=_broker_trades(OVERFLOW), equity_points=[])
    fake = SimpleNamespace(
        broker=broker,
        _seeded_trade_count=0,
        _position_payload=lambda: None,
        _status_payload=lambda: {"state": "running"},
    )
    snap = PaperSession.snapshot(fake)
    assert len(snap["trades"]) == SNAPSHOT_TRADES_MAX
    assert snap["trades"][-1]["ts"] == OVERFLOW - 1
    assert len(broker.trades) == OVERFLOW               # 红线：会计真值一条不少


def test_paper_snapshot_seeded_flag_uses_absolute_indices_after_the_cap():
    """预热边界按**绝对索引**判定：先切尾再 enumerate 会把最新的 20 笔误标成预热成交。

    这里 20 笔预热早已滑出窗口，故窗口内 `seeded` 必须**恒为 false**——若实现写反了，
    这条断言会在窗口的头 20 笔上变红。
    """
    broker = SimpleNamespace(trades=_broker_trades(OVERFLOW), equity_points=[])
    fake = SimpleNamespace(
        broker=broker,
        _seeded_trade_count=20,
        _position_payload=lambda: None,
        _status_payload=lambda: {"state": "running"},
    )
    snap = PaperSession.snapshot(fake)
    assert all(record["seeded"] is False for record in snap["trades"])


def test_paper_snapshot_still_marks_seeded_when_the_window_covers_them():
    """未触上限时行为逐字不变：前 20 笔仍被如实标为预热。"""
    broker = SimpleNamespace(trades=_broker_trades(50), equity_points=[])
    fake = SimpleNamespace(
        broker=broker,
        _seeded_trade_count=20,
        _position_payload=lambda: None,
        _status_payload=lambda: {"state": "running"},
    )
    snap = PaperSession.snapshot(fake)
    assert [record["seeded"] for record in snap["trades"][:20]] == [True] * 20
    assert all(record["seeded"] is False for record in snap["trades"][20:])
