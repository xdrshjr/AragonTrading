"""奖励回填（ai-trade-trajectory-capture §2.11.5）：以仓库唯一真源重放往返，不另写配对规则。

【RV-P2-16】只依赖标准库与 `..accounting_replay`。

- 【RV12】成交身份 = `accounting_event_key(trade, runId)`；它对无身份的成交退化到进程内对象地址
  （离线无意义），此时改用 `(runId, "ledger", epoch, ledgerSeq)`。`ledgerSeq` 只作排序：
  排序键 `(epoch 次序, ledgerSeq)`，同一 run 里同一 `ledgerSeq` 对应多个不同事件键 ⇒ 全部保留并计冲突。
- 【RV13】会计口径 = 该 run 最早一条 `episode_start.accounting`（并入 `runId`）；缺席即如实报告，
  **绝不猜 `costBasis`**（缺席时累加器按 legacy 费池分支重放，trip 的 `coverage` 会如实标出）。
- 腿的承载记录只表示「何时被采到」：承载记录不是 `round` 的腿，奖励只进报告，不伪造回合归属。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..accounting_replay import TradeAccountingAccumulator, accounting_event_key, trade_meta

ROLE_OPEN = "open_leg"
ROLE_CLOSE = "close_leg"
CONTEXT_MISSING = "context_missing"
# 平仓腿 `explain.meta` 里由 `trip_ledger.trip_meta_wire` 写好的结算键——逐字镜像
# `trip_ledger._WIRE_FIELDS` 的 wire 名（RV-P2-16 不许 import 它；漂移由编译用例冻结）。
TRIP_WIRE_KEYS = (
    "tripSeq", "holdSeconds", "notionalUsd", "moveBps", "realizedNetBps", "realizedGrossBps",
    "realizedCostBps", "roundTripFeeUsd", "rMultiple", "plannedRr", "stopAtrMult", "estEdgeBps",
    "estCostBps", "edgeSource", "costConfidence", "decisionConfidence", "wakeKind", "estFeeBps",
    "realizedFeeBps", "feeErrorBps", "comparisonVersion", "costComparisonBasis", "modelVersion",
    "feeMeasurement", "measuredNetEligible",
)
# 累加器按 run 的会计口径重放出的经济读数（trip 键 → 腿键）。RV13 的口径只影响这几项。
_TRIP_ECONOMICS = (("recordedPnl", "realizedPnlUsd"), ("grossPnlUsd", "grossPnlUsd"),
                   ("feeUsd", "feeUsd"), ("tradingNetUsd", "tradingNetUsd"),
                   ("coverage", "coverage"))
_CARRIER_KINDS = frozenset({"round", "fills"})
_RUN_KINDS = frozenset({"episode_start", "round", "fills"})
_LAST = float("inf")


@dataclass(frozen=True, slots=True)
class Carrier:
    """承载一条成交腿的记录。"""

    kind: str
    inst_id: str | None
    epoch: str | None
    seq: int | None
    context: str | None

    @property
    def key(self) -> tuple:
        """与 `round` 记录对齐的键 `(instId, epoch, seq)`。"""
        return self.inst_id, self.epoch, self.seq


@dataclass(frozen=True, slots=True)
class _Leg:
    trade: dict
    run_id: object
    carrier: Carrier
    order: tuple


@dataclass
class RewardBook:
    """一个成员的奖励回填结果。

    Attributes:
        legs: `(instId, epoch, seq)` → 该 `round` 承载的腿奖励（`role` / `tripIndex` / 数值）。
        issues: `(instId, epoch, seq)` → 该 `round` 承载的腿在重放时产生的原因码。
        orphans: 承载记录不是 `round` 的腿奖励（进 `report.tripsWithoutDecisionRound`）。
        trips: runId → 累加器的 `completed_trips`（重放结果原样）。
        context_missing_runs: 找不到 `episode_start.accounting` 的 run。
        ledger_seq_conflicts: 同一 run 里同一 `ledgerSeq` 多出来的不同事件键个数。
        issue_counts: 全部重放原因码的计数。
    """

    legs: dict[tuple, list[dict]] = field(default_factory=dict)
    issues: dict[tuple, list[str]] = field(default_factory=dict)
    orphans: list[dict] = field(default_factory=list)
    trips: dict[object, list[dict]] = field(default_factory=dict)
    context_missing_runs: set = field(default_factory=set)
    ledger_seq_conflicts: int = 0
    issue_counts: dict[str, int] = field(default_factory=dict)


class _TracedAccumulator(TradeAccountingAccumulator):
    """只观察、不改规则：记下每条腿（按重放序号）让 `issues` 新增了哪些原因码。

    `record_trade` 的返回值漏掉了 `_record_open` 里「换向覆盖旧仓」那条 issue，故按 `issues`
    长度差归属，而不是读返回值。
    """

    def __init__(self, context: dict | None = None) -> None:
        self.issues_by_id: dict[object, list[str]] = {}
        super().__init__(context)

    def record_trade(self, trade: dict, epoch_id=None) -> dict:
        """转发给父类，并登记本条腿新增的原因码。"""
        before = len(self.issues)
        result = super().record_trade(trade, epoch_id)
        if len(self.issues) > before:
            self.issues_by_id.setdefault(trade.get("id"), []).extend(self.issues[before:])
        return result


def settle_rewards(records: list[dict], epoch_rank: dict[str, int]) -> RewardBook:
    """汇总一个成员全部 epoch 的成交腿，逐 run 重放并把往返奖励归到承载它的回合。

    Args:
        records: `reader.read_member` 读出的记录（顺序任意）。
        epoch_rank: epoch → 时间次序（`reader.order_epochs` 的下标）。

    Returns:
        `RewardBook`。
    """
    book = RewardBook()
    by_run: dict[object, list[_Leg]] = {}
    for leg in _collect_legs(records, epoch_rank):
        by_run.setdefault(leg.run_id, []).append(leg)
    runs = {row.get("runId") for row in records if row.get("kind") in _RUN_KINDS} | set(by_run)
    for run_id in sorted(runs, key=str):
        _settle_run(book, records, run_id, by_run.get(run_id, []), epoch_rank)
    return book


def run_context(records: list[dict], run_id, epoch_rank: dict[str, int]) -> dict | None:
    """该 run 最早一条 `episode_start.accounting`（并入 `runId`）；缺席 ⇒ `None`。"""
    starts = [row for row in records
              if row.get("kind") == "episode_start" and row.get("runId") == run_id]
    if not starts:
        return None
    first = min(starts, key=lambda row: (_rank(epoch_rank, row.get("epoch")),
                                         _number(row.get("seq"))))
    accounting = first.get("accounting")
    return {**accounting, "runId": run_id} if isinstance(accounting, dict) else None


def _collect_legs(records: list[dict], epoch_rank: dict[str, int]) -> list[_Leg]:
    legs: list[_Leg] = []
    for row in records:
        fills = row.get("fills")
        if row.get("kind") not in _CARRIER_KINDS or not isinstance(fills, list):
            continue
        carrier = Carrier(row["kind"], row.get("instId"), row.get("epoch"), row.get("seq"),
                          row.get("context"))
        rank = _rank(epoch_rank, row.get("epoch"))
        for trade in fills:
            if not isinstance(trade, dict):
                continue
            run_id = trade.get("runId", trade.get("run_id", row.get("runId")))
            order = (rank, _number(trade.get("ledgerSeq")), _number(row.get("seq")))
            legs.append(_Leg(trade, run_id, carrier, order))
    legs.sort(key=lambda leg: leg.order)
    return legs


def _settle_run(book: RewardBook, records: list[dict], run_id, legs: list[_Leg],
                epoch_rank: dict[str, int]) -> None:
    context = run_context(records, run_id, epoch_rank)
    if context is None:
        book.context_missing_runs.add(run_id)
    kept, conflicts = _dedupe(legs, run_id)
    book.ledger_seq_conflicts += conflicts
    # 重放序号与 runId 只写进交给累加器的拷贝；原成交字典（也是 steps 里 execution.fills）不改。
    rows = [{**leg.trade, "id": index, "runId": run_id} for index, leg in enumerate(kept, start=1)]
    accumulator = _TracedAccumulator(context or {"runId": run_id})
    accumulator.restore(rows)
    book.trips[run_id] = accumulator.completed_trips
    _attribute_trips(book, accumulator.completed_trips, kept, rows)
    _attribute_issues(book, accumulator.issues_by_id, kept)


def _dedupe(legs: list[_Leg], run_id) -> tuple[list[_Leg], int]:
    """按事件键去重（保留排序最早的那次采到）；同一 `ledgerSeq` 的不同事件键计冲突。"""
    kept: list[_Leg] = []
    seen: set[tuple] = set()
    keys_by_seq: dict[object, set[tuple]] = {}
    for leg in legs:
        key = _event_key(leg, run_id)
        if key in seen:
            continue
        seen.add(key)
        kept.append(leg)
        ledger_seq = leg.trade.get("ledgerSeq")
        if isinstance(ledger_seq, int) and not isinstance(ledger_seq, bool):
            keys_by_seq.setdefault(ledger_seq, set()).add(key)
    return kept, sum(len(keys) - 1 for keys in keys_by_seq.values())


def _event_key(leg: _Leg, run_id) -> tuple:
    key = accounting_event_key(leg.trade, run_id)
    if key[1] == "dispatch":          # 退化到 id(trade)：离线无意义
        return run_id, "ledger", leg.carrier.epoch, leg.trade.get("ledgerSeq")
    return key


def _attribute_trips(book: RewardBook, trips: list[dict], kept: list[_Leg],
                     rows: list[dict]) -> None:
    """`closeTrade` 所在回合 ⇒ `close_leg`；重放序号 ∈ [entry, close) 的开仓腿 ⇒ `open_leg`。"""
    for index, trip in enumerate(trips):
        entry_id = (trip.get("entryTrade") or {}).get("id")
        close_id = (trip.get("closeTrade") or {}).get("id")
        if not isinstance(entry_id, int) or not isinstance(close_id, int):
            continue
        base = {"tripIndex": index, "tripId": trip.get("tripId"), **_leg_values(trip)}
        _place(book, kept[close_id - 1], {"role": ROLE_CLOSE, **base})
        for row in rows[entry_id - 1:close_id - 1]:
            if row.get("side") in ("long", "short"):
                _place(book, kept[row["id"] - 1], {"role": ROLE_OPEN, **base})


def _leg_values(trip: dict) -> dict:
    """缺键即缺席，绝不补 0。"""
    values = {wire: trip[key] for key, wire in _TRIP_ECONOMICS if trip.get(key) is not None}
    meta = trade_meta(trip.get("closeTrade") or {})
    values.update({key: meta[key] for key in TRIP_WIRE_KEYS if meta.get(key) is not None})
    if meta.get("exitSource") is not None:
        values["exitSource"] = meta["exitSource"]
    return values


def _place(book: RewardBook, leg: _Leg, reward: dict) -> None:
    carrier = leg.carrier
    if carrier.kind == "round":
        book.legs.setdefault(carrier.key, []).append(reward)
        return
    book.orphans.append({**reward, "instId": carrier.inst_id, "runId": leg.run_id,
                         "carrier": carrier.kind, "context": carrier.context,
                         "epoch": carrier.epoch, "seq": carrier.seq,
                         "ledgerSeq": leg.trade.get("ledgerSeq")})


def _attribute_issues(book: RewardBook, issues_by_id: dict, kept: list[_Leg]) -> None:
    for replay_id, codes in issues_by_id.items():
        for code in codes:
            book.issue_counts[code] = book.issue_counts.get(code, 0) + 1
        if not isinstance(replay_id, int) or not 1 <= replay_id <= len(kept):
            continue
        carrier = kept[replay_id - 1].carrier
        if carrier.kind == "round":
            book.issues.setdefault(carrier.key, []).extend(codes)


def _rank(epoch_rank: dict[str, int], epoch) -> int:
    return epoch_rank.get(epoch, len(epoch_rank))


def _number(value) -> float:
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else _LAST
