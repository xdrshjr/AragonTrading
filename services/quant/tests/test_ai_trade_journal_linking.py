"""决策 ⇄ 成交 ⇄ 往返的四步关联（ai-trade-audit-journal §2.4.2）。

最要紧的那条是 `test_pairing_must_use_trade_id_not_ts`：AI 自动交易的 `trades.ts` 由**两套
时钟**写入（K 线轮 = bar 开盘秒 / 定时唤醒轮与盯市轮 = 墙钟），按 `ts` 排序会破坏真实的开→平
交替。这条判据已在 `ai_auto_repositories._pair_round_trips` 的 docstring 里被完整记录，
本用例用一组 `ts` 逆序的数据把它钉死。
"""
from __future__ import annotations

from app.ai_auto.journal.linking import (
    EXACT,
    HEURISTIC,
    NONE,
    build_trip,
    link_one,
    meets_confidence,
    resolve_min_link_confidence,
)


def _decision(**over) -> dict:
    base = {"runId": 7, "ts": 1000, "roundNo": 12, "clOrdId": "",
            "degradeReason": None,
            "fillRef": {"traded": True, "legs": [
                {"side": "long", "px": 100.0, "sz": 1.0, "clOrdId": "cl-1",
                 "source": "round"}]}}
    return {**base, **over}


def _trade(**over) -> dict:
    base = {"id": 1, "runId": 7, "ts": 1000, "side": "long", "price": 100.0, "qty": 1.0,
            "fee": 0.1, "pnl": 0.0, "meta": {"clOrdId": "cl-1"}}
    return {**base, **over}


def test_exact_anchor_wins():
    out = link_one(_decision(), [_trade()])
    assert out["linkConfidence"] == EXACT
    assert out["fill"]["id"] == 1


def test_heuristic_when_client_order_id_is_absent():
    """`clOrdId` 在 `live_auto/executor` 里是 **best-effort 可选**（缺失即省略键）。"""
    decision = _decision(fillRef={"traded": True, "legs": [
        {"side": "long", "px": 100.0, "sz": 1.0, "source": "round"}]})
    out = link_one(decision, [_trade(meta={})])
    assert out["linkConfidence"] == HEURISTIC


def test_two_heuristic_candidates_are_judged_none_not_the_nearest():
    """命中两条及以上一律判 `none`，绝不「挑一个最近的」——误配换来的是一条标签错误的样本。"""
    decision = _decision(fillRef={"traded": True, "legs": [
        {"side": "long", "px": 100.0, "sz": 1.0, "source": "round"}]})
    trades = [_trade(id=1, meta={}), _trade(id=2, ts=1030, meta={})]
    assert link_one(decision, trades)["linkConfidence"] == NONE


def test_no_fill_round_reports_why():
    decision = _decision(fillRef={"traded": False, "legs": [],
                                  "reason": "LIVE_NO_POSITION"})
    out = link_one(decision, [])
    assert out["linkConfidence"] == NONE
    assert out["noFillReason"] == "LIVE_NO_POSITION"


def test_post_round_fill_is_not_treated_as_a_no_fill_round():
    """「`executed` 说没成交」不等于「这一轮没有成交」：利润保护会在 `_post_round` 里再平一笔。"""
    decision = _decision(fillRef={"traded": False, "legs": [
        {"side": "close", "px": 108.0, "sz": 1.0, "clOrdId": "cl-9",
         "source": "post_round", "reason": "profit_guard_peak"}]})
    trades = [_trade(id=5, side="close", price=108.0, pnl=7.8, meta={"clOrdId": "cl-9"})]
    out = link_one(decision, trades)
    assert out["linkConfidence"] == EXACT
    assert out["fillSource"] == "post_round", "详情页据它说明「本笔由利润保护触发」"


def test_pairing_must_use_trade_id_not_ts():
    """构造 `ts` 逆序的两组往返：按 `ts` 配对会配错，按 `id` 配对才是对的。

    这不是风格问题：K 线轮写的 `ts` 是 bar 开盘秒，定时唤醒轮与盯市轮写的是墙钟，同一个 run 上
    两套时钟并存。`build_trip` 的判据是 `trades.id`，故它**与入参的排列顺序无关**——本用例
    同时证明这一点（把同一批腿按 ts 打乱后结果逐字不变），并用一个内联的「朴素 ts 配对」
    演示那条被拒绝的写法会配出什么。
    """
    open_a = _trade(id=10, ts=9000, side="long", meta={"clOrdId": "cl-a"})
    close_a = _trade(id=11, ts=1, side="close", price=110.0, pnl=9.5,
                     meta={"clOrdId": "cl-a2", "realizedBps": 95.0, "exitSource": "agent"})
    open_b = _trade(id=12, ts=9100, side="long", meta={"clOrdId": "cl-b"})
    close_b = _trade(id=13, ts=2, side="close", price=90.0, pnl=-8.0,
                     meta={"clOrdId": "cl-b2", "realizedBps": -80.0,
                           "exitSource": "profit_guard"})
    by_id = [open_a, close_a, open_b, close_b]
    assert build_trip(open_a, by_id)["exitId"] == 11
    assert build_trip(open_b, by_id)["exitId"] == 13
    assert build_trip(open_a, by_id)["realizedBps"] == 95.0

    # 同一批腿按 ts 重排：id 判据使结果**逐字不变**（顺序无关正是它的价值）。
    by_ts = sorted(by_id, key=lambda t: t["ts"])
    assert [t["id"] for t in by_ts] == [11, 13, 10, 12]
    assert build_trip(open_a, by_ts)["exitId"] == 11
    assert build_trip(open_b, by_ts)["exitId"] == 13

    # 被拒绝的那种写法：「在列表里找这条开仓腿之后的第一条 close」。按 ts 排序后它配不出来。
    def _naive_next_close(open_leg, legs):
        after = legs[legs.index(open_leg) + 1:]
        return next((t for t in after if t["side"] == "close"), None)

    assert _naive_next_close(open_a, by_ts) is None, "这正是按 ts 排序会造出的错误"


def test_trip_labels_are_omitted_when_absent_never_zero_filled():
    open_leg = _trade(id=1, side="long")
    close_leg = _trade(id=2, side="close", pnl=3.0, meta={})
    trip = build_trip(open_leg, [open_leg, close_leg])
    assert trip["pnl"] == 3.0
    for key in ("realizedBps", "rMultiple", "exitSource", "holdSeconds"):
        assert key not in trip, "`trip_meta_wire` 的约束 6：键一律「算不出即省略」"


def test_unsettled_position_has_no_trip():
    open_leg = _trade(id=1, side="long")
    assert build_trip(open_leg, [open_leg]) is None


def test_min_link_confidence_auto_relaxes_for_paperless_sessions():
    """纸面 / 无凭证会话在结构上没有交易所单号；恒定 `exact` 会让它导出 0 条（评审 RP1-6）。"""
    with_ids = resolve_min_link_confidence("auto", has_client_order_id=True)
    assert with_ids == {"value": EXACT, "reason": "client_order_id_present", "auto": True}
    without = resolve_min_link_confidence(None, has_client_order_id=False)
    assert without["value"] == HEURISTIC
    assert without["reason"] == "no_client_order_id_in_scope"
    explicit = resolve_min_link_confidence("exact", has_client_order_id=False)
    assert explicit == {"value": EXACT, "reason": "explicit", "auto": False}


def test_confidence_order_is_total():
    assert meets_confidence(EXACT, HEURISTIC) and meets_confidence(HEURISTIC, HEURISTIC)
    assert not meets_confidence(HEURISTIC, EXACT)
    assert meets_confidence(NONE, NONE)
