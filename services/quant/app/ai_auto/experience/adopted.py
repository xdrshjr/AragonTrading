"""接管 / 离线平仓的**合成开仓断面**与降级语境（W-A）—— 纯函数、零 IO、**绝不抛**。

本模块存在的唯一理由是一条幸存者偏差：会被跨进程接管、或在暂停期间被交易所平掉的仓位，恰恰是
**那些没能在会话内平掉的仓位**（超时、被套、等反弹）。它们在净额分布上系统性偏左。把这一整类
剔除在事件表之外，等于给整张经验表做了一次「只留活下来的」筛选，而筛掉的方向正是「教训」那一侧。

三条不可让步的纪律：

1. **绝不新写一份 bps 换算。** `trip_ledger.settle(open_snap, close_trade, ctx)` 已经把口径
   （`net_mode` / `ct_val` / 双腿费 / `fee_known` 传染）全部处理妥当。本模块只负责给它一个
   **合成的 `OpenSnapshot`**，然后让**既有的**结算链原样跑完（A-12 逐位断言）。
2. **合成断面绝不注册进 `AttributionLedger`。** 那条链跑在真实资金上，喂给它一个合成断面会改变
   `aiStats` / 已实现 edge 校准 / 验收卡上用户已经在看的数字，收益不抵风险（A-15 逐字节断言）。
   经验层需要的只是一个口径正确的 `SettledTrip`，本模块给它，仅此而已。
3. **双时钟不混用。** 合成断面的 `ts` 必须与 `close_trade["ts"]` 同一时钟。离线平仓那条 trade 的
   `ts` 取自 `positions-history` 的 `uTime`（**墙钟秒**），故本模块的 `ts` 必须取交易所 `cTime`
   （同一时钟）。一侧 bar 钟一侧墙钟会让 `hold_seconds` 变成一个天文数字，且**不会报错**——
   本仓库在 `holdSeconds` 上有成文前科（提交 `a8ed650` 的双时钟审计）。
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from ..trip_ledger import OpenSnapshot
from . import taxonomy as tax


@dataclass(frozen=True)
class AdoptedOpen:
    """一次接管的开仓事实（聚合形参，满足 CLAUDE.md「形参 ≤5」）。

    `entry` 里的三个数**必须同源**：入场价 / 张数 / 交易所开仓时刻要么全取
    `contract["position"]`（离线平仓路径），要么全取 `assessed["actual"]`（仍持仓路径）。
    混着取会让「合成断面的入场价」与「那条平仓 trade 的数量」来自两份互不相干的快照。
    """

    seq: int
    side: str
    entry: dict                      # {"px": float, "qty": float, "ts": int（墙钟秒）}
    open_fee_usd: float | None
    ct_val: float
    horizon_profile_id: str | None = None


def _finite(value) -> float | None:
    """有限浮点或 `None`（**绝不填 0**：0 会被下游读成「价格为零」并把整笔算成天文 bps）。"""
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _int(value) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def synth_open_snapshot(*, seq: int, side: str, entry: dict,
                        open_fee_usd: float | None) -> OpenSnapshot | None:
    """接管持仓 → 一个**合成**开仓断面；价 / 量不可用 ⇒ `None`（调用方据此计 `harvestGap`）。

    `estimate=None`（接管仓没有本进程的门禁估计）⇒ `edge_error_bps` 恒 `None` ⇒「估计偏差型」
    模板对这类事件天然不触发。这是**对的**：我们确实不知道它当初估了多少。

    `open_fee_known` 的语义沿用 `v1:I-7`：入场费可得 ⇒ `True`；不可得 ⇒ `False`，该事件因此被
    排除在 bps 统计外但**仍然入表**并计入桶的 `excluded`——**被看见的缺失，而不是不存在的缺失**。

    Args:
        seq: 会话级持仓序号（`(side, seq)` 是开仓槽的 epoch 身份）。
        side: `long` | `short`。
        entry: `{"px": 入场价, "qty": 张数, "ts": 交易所开仓时刻（墙钟秒，见模块 docstring 第 3 条）}`。
        open_fee_usd: 入场腿累计费用；`None` ⇒ `open_fee_known=False`。

    Returns:
        合成的 `OpenSnapshot`；价或量不可用 ⇒ `None`。
    """
    entry_px = _finite((entry or {}).get("px"))
    qty = _finite((entry or {}).get("qty"))
    if not entry_px or entry_px <= 0 or not qty or qty <= 0:
        return None
    fee = _finite(open_fee_usd)
    return OpenSnapshot(
        seq=int(seq), side=str(side or ""), ts=_int((entry or {}).get("ts")),
        wall_ts=float(_int((entry or {}).get("ts"))),
        entry_px=entry_px, qty=abs(qty),
        # `notional_usd` 由 `settle()` 用会话的 `ct_val` 重算，这里的值不参与任何 bps 换算。
        notional_usd=0.0,
        open_fee_usd=(fee if fee is not None else 0.0),
        open_fee_known=fee is not None,
        stop_px=None, take_px=None, atr_px=None, estimate=None)


def degraded_context(*, inst_id: str, bar: str, side: str,
                     horizon_profile_id: str | None) -> dict[str, str]:
    """接管仓的开仓语境：**只写确定为真的轴**，其余轴一个都不写（★ J-4 的另一半）。

    `regime` / `volatility` / `event` / `wake` / `confidence` 在接管那一刻是**真的不知道**——
    那笔仓是在上一个进程、上一段行情里开的。`v1:§十一.7` 写得对：「猜出来的语境比没有语境更坏」。
    但它当时的结论是「所以整笔都不采」，那是把幸存者偏差留了下来。

    正解是**降级而不是丢弃**：不写的轴在 `aggregate._combos_for`（★ J-4）里天然不参与桶枚举，
    于是这条事件只会落进由已知轴构成的**更粗的桶**（`{symbol,bar,side}` / `{symbol,bar,exit}` …）。
    标签声称的范围因此**恒等于**它的支撑范围——这正是 F-2 修法给出的结构保证。

    `horizon` 只在**确知**该成员挂了周期档时才写；`None` ⇒ 连它也不写（不是写 `"none"`——
    「没挂档」与「不知道有没有挂档」是两件事）。

    Args:
        inst_id: 标的。
        bar: 周期。
        side: `long` | `short`（接管那一刻交易所侧的真实方向）。
        horizon_profile_id: 该成员的持仓周期档 id；不确定 ⇒ `None`。

    Returns:
        只含确定为真的轴的语境签名。
    """
    signature = {
        tax.AXIS_SYMBOL: tax.fold(tax.AXIS_SYMBOL, inst_id),
        tax.AXIS_BAR: tax.fold(tax.AXIS_BAR, bar),
        tax.AXIS_SIDE: tax.fold(tax.AXIS_SIDE, side),
    }
    if horizon_profile_id:
        signature[tax.AXIS_HORIZON] = tax.fold(tax.AXIS_HORIZON, horizon_profile_id)
    # 折不进闭合词表的轴（`side` 缺失 / `bar` 越界）一律**不写**，绝不留一个 `unknown`：
    # J-4 会把它从桶身份里剔掉，留着只会让签名与桶对不上。
    return {axis: value for axis, value in signature.items() if value != tax.UNKNOWN}


def synth_from_contract(contract: dict, *, seq: int, ct_val: float,
                        open_fee_usd: float | None) -> AdoptedOpen | None:
    """`pause` 契约里的持仓断面 → `adopt_open` 的入参；不可用 ⇒ `None`。

    数据源刻意是 `contract["position"]` 而**不是** `assessed["actual"]`（★ E-5）：被判
    `closed_offline` 的成员，`actual` 是 `None`（仓已在交易所侧平掉），而 `_offline_exit_trade`
    已经在读 `contract["position"]` 的 `openCTime` / `size` —— 两处必须同源，否则「合成断面的
    入场价」与「那条平仓 trade 的数量」会来自两份互不相干的快照。

    ⚠️ **入场价的列名是 `avgEntry`，不是 `avgPx`。** 这个断面由
    `session._state_position_snapshot()` / `_exchange_position()` 落进 `members_json`，两处写的都是
    `avgEntry`（`avgPx` 只是交易所归一行里的原始列名，落库那一步就换掉了）。读错列名的后果
    不是异常而是**恒回 `None`** ⇒ 建不出合成断面 ⇒ 离线平仓那条往返照旧只记一个采集缺口，
    而 `tsc` / `ruff` / 全量用例全绿——W-A 的这一半在生产上一条事件也产不出来。
    保留 `avgPx` / `entryPrice` 作为回落，是为了让调用方直接把交易所归一行喂进来时同样能用。

    Args:
        contract: `pause` 契约里的成员条目（含 `position`）。
        seq: 持仓序号。
        ct_val: 合约乘数（`executor.ct_val`）——`settle()` 的全部 bps 分母都靠它。
        open_fee_usd: 入场腿累计费用（`rebuild_continuity` 的 `entryFeeUsd`）；不可得 ⇒ `None`。

    Returns:
        装配好的 `AdoptedOpen`；持仓断面不可用 ⇒ `None`。
    """
    position = (contract or {}).get("position") or {}
    entry_px = _finite(position.get("avgEntry") or position.get("avgPx")
                       or position.get("entryPrice"))
    qty = _finite(position.get("size") or position.get("pos"))
    if not entry_px or not qty:
        return None
    side = str(position.get("posSide") or position.get("side") or "").lower()
    if side not in ("long", "short"):
        side = "long" if float(qty) > 0 else "short"
    return AdoptedOpen(
        seq=int(seq), side=side,
        entry={"px": entry_px, "qty": abs(float(qty)),
               "ts": _int(position.get("openCTime") or position.get("openWallTs"))},
        open_fee_usd=open_fee_usd, ct_val=float(ct_val or 0.0))
