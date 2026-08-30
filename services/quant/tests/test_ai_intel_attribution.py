"""情报引用归因运行时（ai-trade-intel-closed-loop §7.1 · test_ai_intel_attribution）。

轴 B 的会话级对象。这里用一个**假仓库门面**替掉 `repo` 的三个写函数：本轮要证的是「哪一行在
什么时刻被绑到哪个 epoch 上」，那是纯内存的决策，与 SQLite 无关；真正的落库语义另有
`test_ai_intel_migration.py` 与 `test_ai_intel_api.py` 覆盖。
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass

import pytest

from app.ai_auto.intel import attribution as attr_mod
from app.ai_auto.intel.attribution import IntelAttribution

NOW = 1_800_000_000
REFMAP = {"I01": 11, "I02": 22}


@dataclass
class _Decision:
    action: str = "open_long"
    intel_refs: tuple[str, ...] = ("I01", "I02")


@dataclass
class _Trip:
    seq: int
    net_pnl_usd: float | None
    hold_seconds: float = 900.0


@dataclass
class _Resting:
    cl_ord_id: str
    intent: str = "open"


class _FakeRepo:
    """记录三个写函数的调用（**按调用顺序**：`persist_pending` 的顺序本身是一条契约）。"""

    def __init__(self):
        self.appended: list[dict] = []
        self.promoted: list[dict] = []
        self.settled: list[dict] = []
        self.calls: list[str] = []
        self.fail = False

    async def append_intel_citations(self, _session, rows, *, commit=True):
        if self.fail:
            raise RuntimeError("boom")
        self.calls.append("append")
        self.appended.extend(rows)
        return list(range(len(rows)))

    async def promote_intel_citations_to_entry(self, _session, *, run_id, cl_ord_id,
                                               position_seq, commit=True):
        self.calls.append("promote")
        self.promoted.append({"runId": run_id, "clOrdId": cl_ord_id, "seq": position_seq})
        return 1

    async def settle_intel_citations(self, _session, *, run_id, position_seq, net_pnl_usd,
                                     hold_s, exit_source, outcome, now, commit=True):
        self.calls.append("settle")
        self.settled.append({"runId": run_id, "seq": position_seq, "net": net_pnl_usd,
                             "outcome": outcome, "holdS": hold_s, "exitSource": exit_source})
        return 1


class _FakeSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    async def commit(self):
        return None


def _sessionmaker():
    return lambda: _FakeSession()


@pytest.fixture()
def repo(monkeypatch):
    fake = _FakeRepo()
    monkeypatch.setattr(attr_mod, "repo", fake)
    return fake


def _armed(**over) -> IntelAttribution:
    return IntelAttribution("BTC-USDT-SWAP", 7, enabled=True, sessionmaker=_sessionmaker,
                            now=lambda: NOW, **over)


def _run(coro):
    return asyncio.run(coro)


# ---------------- 总闸 ----------------
def test_attribution_off_writes_nothing(repo):
    """G-5：轴 B 单键关 ⇒ 全部方法空转、一个字节都不写、`stats()` 恒 None。"""
    off = IntelAttribution("BTC-USDT-SWAP", 7, enabled=False, sessionmaker=_sessionmaker)
    off.note_decision(_Decision(), round_no=1, ts=NOW, refmap=REFMAP)
    off.note_open({"clOrdId": "x"}, seq=1)
    off.note_close({}, {}, _Trip(1, 5.0))
    _run(off.persist_pending())

    assert repo.calls == []
    assert off.stats() is None


# ---------------- 轴 A 的裁决计数 ----------------
def test_fabricated_and_no_refmap_are_counted_separately(repo):
    """评审 P2-2：把系统故障读成模型不诚实比没有读数更坏，故两个出口分开计。"""
    armed = _armed()
    armed.note_decision(_Decision(intel_refs=("I01", "I09")), round_no=1, ts=NOW, refmap=REFMAP)
    armed.note_decision(_Decision(intel_refs=("I01",)), round_no=2, ts=NOW, refmap=None)

    stats = armed.stats()
    assert stats["cited"] == 1
    assert stats["fabricated"] == 1
    assert stats["refWithoutDigest"] == 1


# ---------------- 轴 B 的绑定与结算 ----------------
def test_market_open_marks_entry_in_the_same_round(repo):
    """市价单恒走「回退本轮待写行」那条路。"""
    armed = _armed()
    armed.note_decision(_Decision(), round_no=1, ts=NOW, refmap=REFMAP)
    armed.note_executed("open_long", {"traded": True})
    armed.note_open({"clOrdId": ""}, seq=1)
    _run(armed.persist_pending())

    assert [row["role"] for row in repo.appended] == ["entry", "entry"]
    assert {row["positionSeq"] for row in repo.appended} == {1}


def test_limit_open_filled_in_a_later_round_still_marks_entry(repo):
    """G-15 / P0-1：**必须走 resting 通路**——第 N 轮下单、`persist_pending` 已跑过、
    第 N+3 轮才 `note_open`。用市价单跑出来的绿不算数。"""
    armed = _armed()
    armed.note_decision(_Decision(), round_no=1, ts=NOW, refmap=REFMAP)
    armed.note_executed("open_long", {"traded": False}, resting=_Resting("cl-77"))
    _run(armed.persist_pending())                    # ← 第 N 轮就落盘了，role 仍是 hold

    assert [row["role"] for row in repo.appended] == ["hold", "hold"]
    assert {row["clOrdId"] for row in repo.appended} == {"cl-77"}

    for round_no in (2, 3):                          # 中间两轮什么都没引用
        armed.note_decision(_Decision(intel_refs=()), round_no=round_no, ts=NOW, refmap=REFMAP)
        armed.end_round()

    armed.note_open({"clOrdId": "cl-77"}, seq=4)     # 第 N+3 轮成交
    _run(armed.persist_pending())

    assert repo.promoted == [{"runId": 7, "clOrdId": "cl-77", "seq": 4}]


def test_settle_uses_trip_seq_not_session_counter(repo):
    """P0-2：`_dispatch` 的 close 分支第一件事就是 `_position_seq += 1`；结算只能取 `trip.seq`。"""
    armed = _armed()
    armed.note_decision(_Decision(action="close", intel_refs=("I01",)), round_no=5, ts=NOW,
                        refmap=REFMAP)
    armed.note_executed("close", {"traded": True})
    # 会话计数器此刻已经是 9（下一个 epoch），而这一笔往返的 epoch 是 8。
    armed.note_close({}, {"exitSource": "agent"}, _Trip(seq=8, net_pnl_usd=12.5))
    _run(armed.persist_pending())

    assert repo.settled[0]["seq"] == 8
    assert repo.settled[0]["net"] == 12.5
    assert repo.settled[0]["outcome"] == "win"
    assert [row["role"] for row in repo.appended] == ["exit"]


def test_none_trip_settles_as_void_not_pending(repo):
    """§3.2.2：接管仓 / 离线平仓 ⇒ `settled=True, outcome="void"`。

    不写 `settled=True` 的话那些行会永远挂在待结算集合里，而它们的 epoch 已经不会再回来了。
    """
    armed = _armed()
    armed.note_decision(_Decision(action="close", intel_refs=("I01",)), round_no=5, ts=NOW,
                        refmap=REFMAP)
    armed.note_close({}, {}, None)
    _run(armed.persist_pending())

    assert repo.settled[0]["outcome"] == "void"
    assert repo.settled[0]["net"] is None
    assert armed.stats()["settledTrips"] == 0, "void **不计入** settledTrips"


def test_hold_citation_never_settles(repo):
    """`role="hold"` 的行不进结算集合——「情报影响了这次继续持有」不可证伪。"""
    armed = _armed()
    armed.note_decision(_Decision(action="hold", intel_refs=("I01",)), round_no=1, ts=NOW,
                        refmap=REFMAP)
    armed.note_executed("hold", {"traded": False})
    _run(armed.persist_pending())

    assert [row["role"] for row in repo.appended] == ["hold"]
    assert repo.settled == []


def test_settle_is_idempotent_on_repeated_close(repo):
    """重复的平仓事件排两次结算，但两次都带同一个 seq——幂等由仓库层的 `settled=0` 过滤兜住。"""
    armed = _armed()
    armed.note_close({}, {}, _Trip(3, 1.0))
    armed.note_close({}, {}, _Trip(3, 1.0))
    _run(armed.persist_pending())

    assert [row["seq"] for row in repo.settled] == [3, 3]


# ---------------- 轮级纪律 ----------------
def test_end_round_drops_pending_and_counts_it_but_keeps_claims(repo):
    """P1-9：异常轮的待写行绝不留给下一轮；**跨轮认领槽不清**（限价单要靠它活下来）。"""
    armed = _armed()
    armed.note_decision(_Decision(), round_no=1, ts=NOW, refmap=REFMAP)
    armed.note_executed("open_long", {"traded": False}, resting=_Resting("cl-9"))
    _run(armed.persist_pending())                    # 落盘 ⇒ 认领槽里有 2 行
    armed.note_decision(_Decision(intel_refs=("I01",)), round_no=2, ts=NOW, refmap=REFMAP)
    armed.end_round()                                # 第 2 轮抛错

    assert armed.stats()["dropped"] == 1
    armed.note_open({"clOrdId": "cl-9"}, seq=2)
    _run(armed.persist_pending())
    assert repo.promoted, "end_round 清掉认领槽会让限价成交永远认领不到"


def test_persist_failure_is_counted_not_silent(repo):
    """§3.2.1 的异常纪律：失败即丢弃并**计数**，绝不静默——静默丢弃正是本仓库反复栽的坑。"""
    armed = _armed()
    armed.note_decision(_Decision(), round_no=1, ts=NOW, refmap=REFMAP)
    repo.fail = True
    _run(armed.persist_pending())

    assert armed.stats()["dropped"] == 2


def test_stop_drops_pending_rows_and_claims(repo):
    """评审 P2-12：关停时两处一并丢弃并计数，**不新增任何关停步骤**。"""
    armed = _armed()
    armed.note_decision(_Decision(), round_no=1, ts=NOW, refmap=REFMAP)
    armed.note_executed("open_long", {"traded": False}, resting=_Resting("cl-1"))
    _run(armed.persist_pending())
    armed.note_decision(_Decision(intel_refs=("I01",)), round_no=2, ts=NOW, refmap=REFMAP)
    armed.stop()

    assert armed.stats()["dropped"] == 1 + 2


def test_claim_slot_is_capped_and_overflow_is_counted(repo):
    """容量上限存在只是为了让一条永不成交的挂单不会把内存占住；超量 FIFO 且**被看见**。"""
    armed = _armed()
    for i in range(attr_mod.PENDING_CITE_CLAIM_MAX + 2):
        armed.note_decision(_Decision(intel_refs=("I01",)), round_no=i, ts=NOW, refmap=REFMAP)
        armed.note_executed("open_long", {"traded": False}, resting=_Resting(f"cl-{i}"))
        _run(armed.persist_pending())

    assert armed.stats()["dropped"] == 2


def test_persist_writes_in_the_documented_order(repo):
    """落盘顺序本身是契约：INSERT 新行 → 升级入场行 → 结算。"""
    armed = _armed()
    armed.note_decision(_Decision(), round_no=1, ts=NOW, refmap=REFMAP)
    armed.note_executed("open_long", {"traded": False}, resting=_Resting("cl-3"))
    _run(armed.persist_pending())
    repo.calls.clear()

    armed.note_decision(_Decision(action="close", intel_refs=("I01",)), round_no=2, ts=NOW,
                        refmap=REFMAP)
    armed.note_open({"clOrdId": "cl-3"}, seq=2)
    armed.note_close({}, {}, _Trip(2, -4.0))
    _run(armed.persist_pending())

    assert repo.calls == ["append", "promote", "settle"]
