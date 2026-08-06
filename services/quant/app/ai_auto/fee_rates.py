"""官方费率档位的会话级快照缓存（ai-trade-cost-truth-and-net-pnl 轴 A · A-3）。

在本模块之前，全仓库**从来没有任何一行代码问过交易所「我这个账户这个合约到底收我多少」**：
`ai_trade_maker_fee_bps=2.0` / `ai_trade_taker_fee_bps=5.0` 是 OKX Lv1 的挂牌费率，而真实账户的
档位、OKB 抵扣、返佣与 USDT / USDC / 币本位合约的分档全都可能不同。本模块把 `GET
/api/v5/account/trade-fee` 的回执做成一份会话级快照，成为 `FeeLedger`（记账）与 `CostParams`
（门禁 / prompt）的**同一个**真源。

逐字复用 `funding.FundingCache` 的三条不变式（异常一律吞、`enabled=False` 一个请求都不发、取数失败
绝不静默按 0 计），差异只有两点：

1. TTL 默认 3600s（费率档位按日变，不是按分钟变）。
2. **没有「陈旧 ⇒ None」的降级**：一份一小时前的真实档位仍然比配置常数准，故陈旧只影响 `ageS` 读数
   与 wire 上的 `stale` 标记，**不撤回数值**。资金费率那边撤回是因为「陈旧的费率会让成本被低估」，
   而费率档位陈旧只意味着「可能错过了一次升档」，方向与量级都不同。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# 陈旧倍数：连续 3 个 TTL 未刷新成功即在 wire 上标 `stale`（默认 TTL 3600s ⇒ 3 小时）。
_STALE_FACTOR = 3.0


@dataclass(frozen=True)
class FeeRateSnapshot:
    """一份官方费率档位读数（不可变；`fetched_wall` 是它的真实年龄基准）。"""

    maker_bps: float
    taker_bps: float
    level: str
    rule_type: str
    rebate: bool
    source: str          # "exchange" | "config"
    fetched_wall: float
    maker_bps_raw: float = 0.0
    taker_bps_raw: float = 0.0


def snapshot_from_wire(wire: dict | None, now_wall: float) -> FeeRateSnapshot | None:
    """归一 wire（`okx.trading._normalize_trade_fee` 产物）→ 快照；`None` / 缺键 ⇒ `None`。

    启动期预取（`AiTradeManager._gather_context`）与运行期热更新走的是**同一个**装配函数，故两条
    路径不可能产出两种形状的快照。

    Args:
        wire: 归一后的费率 dict（含 `makerBps` / `takerBps`），或 `None`。
        now_wall: 当前墙钟（秒），作为快照年龄基准。

    Returns:
        `FeeRateSnapshot`；入参缺席或两个 bps 任一缺失 ⇒ `None`。
    """
    if not wire or wire.get("makerBps") is None or wire.get("takerBps") is None:
        return None
    return FeeRateSnapshot(
        maker_bps=float(wire["makerBps"]), taker_bps=float(wire["takerBps"]),
        level=str(wire.get("level") or ""), rule_type=str(wire.get("ruleType") or ""),
        rebate=bool(wire.get("rebate")), source=str(wire.get("source") or "exchange"),
        fetched_wall=float(now_wall),
        maker_bps_raw=float(wire.get("makerBpsRaw", wire["makerBps"])),
        taker_bps_raw=float(wire.get("takerBpsRaw", wire["takerBps"])))


def resolve_fee_rates(settings, snapshot: FeeRateSnapshot | None) -> tuple[float, float, str]:
    """`(maker_bps, taker_bps, source)`：快照缺席 ⇒ 配置常数 + `source="config"`。

    这是全系统**唯一**的费率回落点——记账链与决策链都经此取数，因此不存在「账本按官方档位记、门禁
    按配置常数判」的口径分叉（A-6 单一真源约束）。
    """
    if snapshot is None:
        return (float(settings.ai_trade_maker_fee_bps),
                float(settings.ai_trade_taker_fee_bps), "config")
    return snapshot.maker_bps, snapshot.taker_bps, snapshot.source


class FeeRateCache:
    """单标的官方费率缓存：TTL 内去重、异常吞并、陈旧只标记不撤回。"""

    def __init__(self, inst_id: str, *, ttl_s: float, stale_factor: float = _STALE_FACTOR,
                 enabled: bool = True) -> None:
        self.inst_id = inst_id
        self.ttl_s = float(ttl_s)
        self.stale_factor = float(stale_factor)
        self.enabled = bool(enabled)
        self._snapshot: FeeRateSnapshot | None = None
        self._last_try_wall = 0.0

    def seed(self, snapshot: FeeRateSnapshot | None, now_wall: float) -> None:
        """植入启动期预取到的快照（不消耗一次网络请求，但要占住 TTL 窗口）。"""
        if snapshot is None:
            return
        self._snapshot = snapshot
        self._last_try_wall = float(now_wall)

    async def refresh(self, fetch_fn, now_wall: float) -> bool:
        """未到 TTL / 已禁用 ⇒ 直接 `False`；取数异常 ⇒ 吞掉并 `False`（上一份快照仍可读）。

        Args:
            fetch_fn: `async (inst_id) -> dict | None`（生产由 `CostRuntime` 用凭证绑好）。
            now_wall: 当前墙钟（秒）。

        Returns:
            本次是否成功写入了新快照。
        """
        if not self.enabled:
            return False
        if now_wall - self._last_try_wall < self.ttl_s:
            return False
        self._last_try_wall = float(now_wall)
        try:
            wire = await fetch_fn(self.inst_id)
        except Exception as exc:  # noqa: BLE001 —— 费率取数失败绝不打断盯市（回落配置常数即可）
            logger.debug("trade fee refresh failed (%s): %s", self.inst_id, exc)
            return False
        snap = snapshot_from_wire(wire, now_wall)
        if snap is None:
            return False
        self._snapshot = snap
        return True

    def snapshot(self) -> FeeRateSnapshot | None:
        """当前快照（**陈旧也照返**，见模块 docstring 的第 2 点差异）。"""
        return self._snapshot

    @property
    def attempted(self) -> bool:
        """本会话是否真的尝试过取数（含被 TTL 跳过之前的第一次）。

        「回落到配置常数」的提示必须挂在**尝试过且没拿到**上：还没轮到取数就先告知一遍，说的是一件
        当时并不成立的事。"""
        return self._last_try_wall > 0.0

    def wire(self, now_wall: float) -> dict | None:
        """camelCase 只读回执（进 `costSnapshot.feeRates`）；从未取到 ⇒ `None`。"""
        snap = self._snapshot
        if snap is None:
            return None
        age = max(0.0, float(now_wall) - snap.fetched_wall)
        return {"makerBps": snap.maker_bps, "takerBps": snap.taker_bps, "level": snap.level,
                "ruleType": snap.rule_type, "source": snap.source, "rebate": snap.rebate,
                "ageS": round(age, 3), "stale": age > self.ttl_s * self.stale_factor,
                "makerBpsRaw": snap.maker_bps_raw, "takerBpsRaw": snap.taker_bps_raw}
