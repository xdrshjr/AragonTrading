"""资金费账本纯核心（ai-trade-horizon-acceptance-loop 轴 A · §2.2）。

无 IO、可单测、**绝不抛**。存在的理由：上一轮把持仓从「几十分钟」拉到「小时～天」级，而
`short_swing`（12–48h）与 `trend_hold`（1–3d）恰好横跨 3～9 个资金费结算点——一笔 5× 杠杆的
3 天持仓按 0.01%/8h 的常见费率，资金费约等于 **0.27% 的名义额、1.35% 的保证金**，与调查样本里
那笔 3.06 USDT 手续费同一量级。而本地净额**按定义不含资金费**（`reconcile.py:10-13`）、回测侧
`SimBroker` 全文没有任何资金费扣减路径 ⇒ 两个世界都把它记成 0。于是「持仓拉长是否真的更划算」
这个问题在系统里根本无法回答。

四条不可动摇的口径（改本文件前先读）：

1. **支出为正。** 与 `okx/trading.py` 的 `_normalize_position_history` 归一同向（该处已把 OKX 的
   「负数=支出」取反）。故 `netAfterFundingUsd = netPnlUsd − fundingCostUsd`。
2. **不知道就说不知道。** 结算周期取不到 ⇒ **不计提**，绝不按 8h 猜（调查约束 #7 原文：OKX 会
   动态变为 1/2/4/8 小时，不能固定假设结算频率）。判据只认 `FundingWindow.interval_known`，
   **绝不**用 `interval_s <= 0`——上游 `rest_client` 会把未知兜底成 28800，那个判据在生产里恒
   不成立，写了等于没写（[P0-1]，与上一轮「有默认值的字段无法表达『不知道』」同一个坑）。
3. **`fundingCostUsd` 为 `None` 与为 `0` 是两个结论**：前者是「没算成」，后者是「真的没跨过
   结算点」。验收卡在 `None` 时判 `insufficient` 而不是 `pass`。
4. **绝不回写盈亏。** 本模块只产出一份独立的账，永不进 `trade["pnl"]`、永不进权益曲线——沿用
   `reconcile.py:5-6` 的既定纪律（回写会让「盈亏正确性 = 网络可用性 × 持仓模式假设」）。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, replace

from .acceptance_text import funding_note
from .backtest_funding import BacktestFundingCurve

# 单次计提可平铺的结算点上限：坏读数（interval 极小）下防止一次 tick 造出上万次计提。
# 触顶即记一次 `capHits`——不记的话，「只取了最近 64 个」与「本来就只跨了这么多」在 wire 上无法
# 区分（P2-5）。正常链路上一个 5s tick 至多跨 1 个点，触顶本身即是一次异常。
_MAX_SETTLEMENTS = 64

_ROUND_USD = 6


@dataclass(frozen=True)
class FundingWindow:
    """一次计提所需的全部外生量（全部来自 `FundingSnapshot`，本模块不发任何请求）。

    Attributes:
        rate: 当期资金费率（**小数**，非 bps）。
        anchor_ts: 交易所给的 `nextFundingTs`（秒）——结算网格的锚点。
        interval_s: 交易所给的 `intervalS`（秒）。
        interval_known: 结算周期是否**真的**来自交易所（[P0-1]）。`False` ⇒ 一律不计提。
        min_interval_s / max_interval_s: 合理性区间；越界即视为坏读数 ⇒ 不计提。
    """

    rate: float
    anchor_ts: int
    interval_s: float
    interval_known: bool = False
    min_interval_s: float = 1800.0
    max_interval_s: float = 28800.0

    def is_usable(self) -> bool:
        """本窗口能否用于计提（[P0-1] 的唯一判据入口，四处调用共用）。"""
        if not self.interval_known:
            return False
        if not _finite(self.interval_s) or self.interval_s <= 0:
            return False
        return self.min_interval_s <= self.interval_s <= self.max_interval_s


@dataclass
class FundingEpoch:
    """一段持仓的资金费计提状态；身份 `(side, seq)` 与 `HorizonRuntime` 完全同源。

    Attributes:
        side: `long` / `short`。
        seq: 会话级持仓序号（实盘取 `_position_seq`，回测取 `pos.entry_bar`）。
        qty_coin: 累计成交数量（币本位）；**部分成交累加而不是重置**。
        opened_wall: 开仓时刻（首次计提的起点——之前的结算点与本仓无关）。
        settled_through_ts: 已计提到的最后一个时刻（0 = 尚未计提过）。
        accrued_usd: 本段已计提额（支出为正）。
        crossings: 本段已跨过的结算点数。
        cap_hits: 本段触顶 `_MAX_SETTLEMENTS` 的次数（>0 ⇒ 计提可能少记）。
    """

    side: str
    seq: int
    qty_coin: float
    opened_wall: float
    settled_through_ts: int = 0
    accrued_usd: float = 0.0
    crossings: int = 0
    cap_hits: int = 0
    coverage: str = "unavailable"


def settlement_points(*, from_ts: float, to_ts: float,
                      window: FundingWindow) -> list[int]:
    """`(from_ts, to_ts]` 内的结算时刻升序列表；窗口不可用 ⇒ `[]`。

    `anchor_ts` 是交易所给的**下一次**结算时刻，按 `interval_s` 向前后平铺即得整条网格。
    平铺数量硬封顶 `_MAX_SETTLEMENTS`，超出即只取**最近的**那批（更早的那些已经无从补记，
    与其少记不如少记且明说）。

    Args:
        from_ts: 区间左端（开区间）。
        to_ts: 区间右端（闭区间）。
        window: 本次计提的外生量。

    Returns:
        升序结算时刻（秒）；不可用 / 区间为空 ⇒ 空表。
    """
    if not window.is_usable() or not _finite(from_ts) or not _finite(to_ts):
        return []
    if to_ts <= from_ts:
        return []
    step = float(window.interval_s)
    anchor = float(window.anchor_ts)
    if not _finite(anchor) or anchor <= 0:
        return []
    # 第一个 > from_ts 的网格点：由锚点按整步长回推 / 前推，避免逐点循环。
    k = math.floor((from_ts - anchor) / step) + 1
    points: list[int] = []
    while True:
        point = anchor + k * step
        if point > to_ts:
            break
        if point > from_ts:
            points.append(int(round(point)))
        k += 1
        if len(points) > _MAX_SETTLEMENTS * 4:      # 硬护栏：坏读数下也必然终止
            break
    return points[-_MAX_SETTLEMENTS:] if len(points) > _MAX_SETTLEMENTS else points


def accrue(epoch: FundingEpoch, *, window: FundingWindow, now_wall: float,
           mark_px: float) -> tuple[FundingEpoch, int]:
    """把 `(settled_through_ts, now_wall]` 内跨过的结算点逐个计提。

    首次调用（`settled_through_ts == 0`）时起点取 `opened_wall`——开仓那一刻之前的结算点与本仓
    无关。`mark_px` 非正 / 非有限 ⇒ 本次不计提且**不推进** `settled_through_ts`（下一次 tick 会
    补上；绝不因为一次坏读数永久丢掉一个结算点）。窗口不可用同理。

    Args:
        epoch: 当前 epoch（不会被就地修改）。
        window: 本次计提的外生量。
        now_wall: 判定时刻（秒）。
        mark_px: 名义额所用的标记价（实盘取会话 `_current_px()`，回测取该根收盘价）。

    Returns:
        `(新 epoch, 本次跨点数)`。
    """
    if not window.is_usable() or not _is_good_px(mark_px) or not _finite(now_wall):
        return epoch, 0
    start = float(epoch.settled_through_ts or epoch.opened_wall)
    points = settlement_points(from_ts=start, to_ts=float(now_wall), window=window)
    raw = _raw_crossings(start, float(now_wall), window)
    capped = epoch.cap_hits + (1 if raw > _MAX_SETTLEMENTS else 0)
    if not points:
        through = max(int(epoch.settled_through_ts), int(now_wall))
        return replace(epoch, settled_through_ts=through, cap_hits=capped), 0
    notional = float(epoch.qty_coin) * float(mark_px)
    sign = 1.0 if epoch.side == "long" else -1.0
    delta = float(window.rate) * notional * sign * len(points)
    return replace(epoch, settled_through_ts=int(points[-1]),
                   accrued_usd=float(epoch.accrued_usd) + delta,
                   crossings=int(epoch.crossings) + len(points),
                   cap_hits=capped), len(points)


def _raw_crossings(from_ts: float, to_ts: float, window: FundingWindow) -> int:
    """未封顶时**本应**跨过的结算点数（只用于判定是否触顶，不参与计提）。"""
    if to_ts <= from_ts or window.interval_s <= 0:
        return 0
    return int(math.floor((to_ts - from_ts) / float(window.interval_s))) + 1


class FundingLedger:
    """一个会话（或一次回测）的资金费账本。

    `enabled=False` ⇒ 全部方法空转、`stats()` 恒 `None`（整键缺席 ⇒ 逐字回退）。
    """

    def __init__(self, *, enabled: bool, ct_val: float,
                 min_interval_s: float = 1800.0,
                 max_interval_s: float = 28800.0,
                 backtest: bool = False) -> None:
        self.enabled = bool(enabled)
        # 合约乘数：名义额 = `张数 × ctVal × 价`。漏掉它会让资金费差一个合约乘数量级
        # （BTC-USDT-SWAP 的 ctVal=0.01 ⇒ 高估 100 倍）。
        self.ct_val = float(ct_val or 0.0)
        self.min_interval_s = float(min_interval_s)
        self.max_interval_s = float(max_interval_s)
        self.epoch: FundingEpoch | None = None
        self.closed_accrued_usd = 0.0
        self.crossings = 0
        self.stale_ticks = 0              # 快照缺席 / 已陈旧而跳过的 tick 数
        self.unknown_interval_ticks = 0   # [P0-1] 结算周期未知而跳过的 tick 数
        self.cap_hits = 0
        self.accrual_ticks = 0            # 真正跑过一次有效计提判定的 tick 数
        self._interval_known = False
        self._interval_s: float | None = None
        self._last_rate: float | None = None
        self._backtest = bool(backtest)
        self._interval_source = "unknown"
        self.closed_coverage = "complete"

    # ---------- 成交事件收口 ----------
    def note_open(self, trade: dict, *, seq: int, now_wall: float) -> None:
        """开仓成交：建 / 续 epoch（身份 `(side, seq)` 与 `HorizonRuntime` 逐字相同）。

        **部分成交累加 `qty_coin` 而不是重置**——重置会把已计提额清零，等于漏记。
        """
        if not self.enabled:
            return
        side = (trade or {}).get("side")
        if side not in ("long", "short"):
            return
        qty = _num((trade or {}).get("qty")) * self.ct_val
        epoch = self.epoch
        if epoch is not None and epoch.side == side and epoch.seq == int(seq):
            self.epoch = replace(epoch, qty_coin=epoch.qty_coin + qty)
            return
        self.epoch = FundingEpoch(side=side, seq=int(seq), qty_coin=qty,
                                  opened_wall=float(now_wall))

    def note_close(self, trade: dict, now_wall: float) -> None:
        """平仓成交：把本 epoch 的 `accrued` 结转进 `closed_accrued_usd` 并清空 epoch。"""
        if not self.enabled or self.epoch is None:
            return
        self.closed_accrued_usd += float(self.epoch.accrued_usd)
        if self._backtest and self.epoch.coverage != "complete":
            self.closed_coverage = "partial"
        self.epoch = None

    # ---------- 计提挂点 ----------
    def accrue(self, snapshot, *, now_wall: float, mark_px: float | None) -> None:
        """盯市挂点（纯算、零 IO）；`snapshot is None`（未取到 / 已陈旧）⇒ 记一次 `stale_ticks`。"""
        if not self.enabled or self.epoch is None:
            return
        if snapshot is None:
            self.stale_ticks += 1
            return
        window = self.window_from(snapshot)
        self._interval_known = bool(window.interval_known)
        self._interval_s = float(snapshot.interval_s)
        self._last_rate = float(snapshot.rate)
        if not window.is_usable():
            self.unknown_interval_ticks += 1
            return
        if mark_px is None or not _is_good_px(mark_px):
            self.stale_ticks += 1
            return
        self.accrual_ticks += 1
        before = self.epoch.cap_hits
        self.epoch, crossed = accrue(self.epoch, window=window, now_wall=now_wall,
                                     mark_px=float(mark_px))
        self.crossings += crossed
        # 按**增量**累加：epoch 会随每次平仓重建，直接赋值会让上一段的触顶次数在换仓时归零。
        self.cap_hits += self.epoch.cap_hits - before

    def window_from(self, snapshot) -> FundingWindow:
        """`FundingSnapshot` → `FundingWindow`（本层唯一的转换点，两个世界共用合理性区间）。"""
        return FundingWindow(
            rate=float(snapshot.rate), anchor_ts=int(snapshot.next_funding_ts),
            interval_s=float(snapshot.interval_s),
            interval_known=bool(getattr(snapshot, "interval_known", False)),
            min_interval_s=self.min_interval_s, max_interval_s=self.max_interval_s)

    def accrue_bars(self, curve: "BacktestFundingCurve", *, from_ts: int, to_ts: int,
                    mark_px: float) -> None:
        """回测逐根计提：按**真实历史结算时刻**逐点计提（不平铺，见 `BacktestFundingCurve`）。"""
        if not self.enabled or self.epoch is None:
            return
        self._backtest = True
        start = max(int(from_ts), int(self.epoch.settled_through_ts),
                    int(self.epoch.opened_wall))
        if to_ts < start:
            return
        evidence = curve.window_evidence(start, int(to_ts))
        coverage = evidence["coverage"]
        if (self.epoch.coverage == "partial"
                or (self.epoch.settled_through_ts
                    and self.epoch.coverage != "complete")):
            coverage = "partial"
        points = evidence["points"]
        snapshot = curve.snapshot_at(int(to_ts))
        self._interval_known = False
        self._interval_source = (snapshot.interval_source if snapshot
                                 else "unknown")
        self._interval_s = snapshot.interval_s if snapshot else None
        if points and not _is_good_px(mark_px):
            self.epoch = replace(self.epoch, coverage="partial")
            return
        self.accrual_ticks += 1
        sign = 1.0 if self.epoch.side == "long" else -1.0
        # 空窗口不需要价格，避免 0 × NaN 污染整个持仓的后续计提。
        delta = 0.0
        if points:
            notional = float(self.epoch.qty_coin) * float(mark_px)
            delta = sum(rate for _, rate in points) * notional * sign
            self._last_rate = points[-1][1]
        self.epoch = replace(self.epoch, settled_through_ts=int(to_ts),
                             accrued_usd=float(self.epoch.accrued_usd) + delta,
                             crossings=int(self.epoch.crossings) + len(points),
                             coverage=coverage)
        self.crossings += len(points)

    # ---------- 统计 ----------
    def stats(self, *, settled_usd: float | None,
              unavailable_reason: str | None = None) -> dict | None:
        """`aiStats.funding` / `costSnapshot.funding` 共用的只读 wire；未启用 ⇒ `None`。

        Args:
            settled_usd: 交易所结算合计（支出为正）。**调用方须在不可信时传 `None`**——三道门
                （对账总闸 / 凭证 / 样本可能被截断）见 `CostRuntime._settled_funding_usd`。
            unavailable_reason: 上一项为 `None` 时的原因短语（进 note，让运维知道是哪一道门没过）。

        Returns:
            §2.2.2 形状的 wire。
        """
        if not self.enabled:
            return None
        open_usd = None if self.epoch is None else round(self.epoch.accrued_usd, _ROUND_USD)
        closed_usd = round(self.closed_accrued_usd, _ROUND_USD)
        basis, total = self._compose(settled_usd, closed_usd, open_usd)
        wire = {
            "enabled": True, "basis": basis,
            "accruedClosedUsd": closed_usd, "accruedOpenUsd": open_usd,
            "settledUsd": (None if settled_usd is None
                           else round(float(settled_usd), _ROUND_USD)),
            "fundingCostUsd": None if total is None else round(total, _ROUND_USD),
            "crossings": int(self.crossings),
            "intervalKnown": bool(self._interval_known),
            # 结算周期未知时下发 `null`——绝不把上游的 8h 兜底值当读数展示（[P0-1]）。
            "intervalS": (float(self._interval_s)
                          if self._interval_known and self._interval_s else None),
            "lastRateBps": (None if self._last_rate is None
                            else round(self._last_rate * 1e4, 6)),
            "staleTicks": int(self.stale_ticks),
            "unknownIntervalTicks": int(self.unknown_interval_ticks),
            "capHits": int(self.cap_hits),
            "settledTrusted": settled_usd is not None,
            "note": funding_note(basis=basis, settled_usd=settled_usd,
                                 accrued_closed_usd=closed_usd, accrued_open_usd=open_usd,
                                 unavailable_reason=unavailable_reason),
        }
        if self._backtest:
            self._backtest_coverage_wire(wire)
        return wire

    def _backtest_coverage_wire(self, wire: dict) -> None:
        """已知计提独立披露，任一持仓历史缺口永久保留。"""
        epoch = self.epoch
        open_coverage = epoch.coverage if epoch else None
        complete = (self.closed_coverage == "complete"
                    and (epoch is None or open_coverage == "complete"))
        wire.update({
            "knownAccruedUsd": round(self.closed_accrued_usd
                + (epoch.accrued_usd if epoch else 0), _ROUND_USD),
            "knownAccruedPoints": int(self.crossings),
            "openCoverage": open_coverage,
            "closedCoverage": self.closed_coverage,
            "coverage": "complete" if complete else "partial",
            "intervalSource": self._interval_source,
        })
        if epoch and open_coverage != "complete":
            wire["accruedOpenUsd"] = None
        if self.closed_coverage != "complete":
            wire["accruedClosedUsd"] = None
        if not complete:
            wire["fundingCostUsd"] = None
            wire["basis"] = "unavailable"
            wire["note"] = (
                "历史点本地计提覆盖不足；已知计提单列，完整资金费未知。")

    def _compose(self, settled_usd: float | None, closed_usd: float,
                 open_usd: float | None) -> tuple[str, float | None]:
        """§2.2.1 的合成口径（[P1-6]：取 `max` 保证读数**单调不减**）。

        直接用 `settledUsd` 替换会让每平一笔仓时资金费先跳水再补回——`positions-history` 对刚平
        的那一笔存在结算延迟（`reconcile.build_reconciliation` 的 `final` 注释写明）。一个会
        **倒退**的累计成本读数比不显示更糟。
        """
        open_part = float(open_usd or 0.0)
        if settled_usd is not None:
            return "settled+accrued", max(float(settled_usd), closed_usd) + open_part
        if self.accrual_ticks > 0 or self.crossings > 0 or closed_usd != 0.0:
            return "accrued", closed_usd + open_part
        return "unavailable", None


def merge_funding_wires(wires: list[dict | None]) -> dict | None:
    """多成员的资金费 wire 合并（分组运行期与历史详情共用）。

    `basis` 取**最弱**的那一档、`fundingCostUsd` 只在**全部**成员都有值时相加——任一成员不可得
    即整体不可得，绝不让一个取到数的成员替另一个没取到的成员背书。计数类逐项相加，
    `intervalKnown` 取「全部成员都已知」（有一个不知道，整组的周期就说不准）。
    """
    rows = [w for w in wires if w]
    if not rows:
        return None
    costs = [w.get("fundingCostUsd") for w in rows]
    total = (None if any(c is None for c in costs)
             else round(sum(float(c) for c in costs), _ROUND_USD))
    rank = min(_BASIS_RANK.get(str(w.get("basis") or "unavailable"), 0) for w in rows)
    basis = _BASIS_BY_RANK[rank]
    opens = [w.get("accruedOpenUsd") for w in rows if w.get("accruedOpenUsd") is not None]
    settled = [w.get("settledUsd") for w in rows if w.get("settledUsd") is not None]
    return {
        "enabled": True, "basis": basis,
        "accruedClosedUsd": round(sum(_num(w.get("accruedClosedUsd")) for w in rows),
                                  _ROUND_USD),
        "accruedOpenUsd": (round(sum(float(v) for v in opens), _ROUND_USD)
                           if opens else None),
        "settledUsd": (round(sum(float(v) for v in settled), _ROUND_USD)
                       if settled else None),
        "fundingCostUsd": total,
        "crossings": sum(int(w.get("crossings") or 0) for w in rows),
        "intervalKnown": all(bool(w.get("intervalKnown")) for w in rows),
        "intervalS": next((w.get("intervalS") for w in rows if w.get("intervalS")), None),
        "lastRateBps": next((w.get("lastRateBps") for w in rows
                             if w.get("lastRateBps") is not None), None),
        "staleTicks": sum(int(w.get("staleTicks") or 0) for w in rows),
        "unknownIntervalTicks": sum(int(w.get("unknownIntervalTicks") or 0) for w in rows),
        "capHits": sum(int(w.get("capHits") or 0) for w in rows),
        "settledTrusted": all(bool(w.get("settledTrusted")) for w in rows),
        # 合并后的 note 不转述任何一个成员的文案（那会把一个标的的口径说成整组的），
        # 只如实说清「这是几个标的的合计」与「合并后的口径是哪一档」。
        "note": (f"{len(rows)} 个标的的合计；口径取最弱的一档（"
                 + {"settled+accrued": "交易所结算 + 当前持仓计提",
                    "accrued": "本地计提",
                    "unavailable": "口径不可得"}[basis] + "）"),
    }


# `basis` 的强弱序（与 `horizon_acceptance` 同一张表：任一成员不可得 ⇒ 合并结果不可得）。
_BASIS_RANK = {"settled+accrued": 2, "accrued": 1, "unavailable": 0}
_BASIS_BY_RANK = {2: "settled+accrued", 1: "accrued", 0: "unavailable"}


def _finite(value) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _is_good_px(value) -> bool:
    """判定价是否可用于名义额（非有限 / 非正一律不可用）。"""
    try:
        px = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(px) and px > 0


def _num(value) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return 0.0
    return out if math.isfinite(out) else 0.0
