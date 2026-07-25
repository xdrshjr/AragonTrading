"""永续资金费率的会话级快照缓存（ai-trade-cost-accounting-and-edge-gate §3.5，D-2）。

存在的唯一理由是让**决策轮内零网络 IO**这条硬约束成立：取数跑在 5s 盯市 tick（`_monitor_tick`）
上，决策轮只调同步的 `snapshot()` 读内存。

三条不变式：
1. **异常一律吞掉**（`logger.debug`），绝不上抛——资金费率是成本模型的一个加项，取不到就降级，
   不该有能力打断盯市或决策。
2. **陈旧数据绝不冒充新鲜**：超过 `ttl_s × stale_factor` 即判定陈旧，`snapshot()` 返回 `None`
   ⇒ 成本模型 `confidence="degraded"` ⇒ 门禁阈值收紧。
3. **`enabled=False` ⇒ 一个请求都不发**。调用方在「资金费率总闸关」或「门禁本就空转（`k_min=0`，
   默认）」时构造禁用态缓存——门禁不启用时取这个数没有任何消费方，白烧配额（见 spec §十二 I-2）。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# 陈旧倍数：连续 5 个 TTL 未刷新成功即判定读数不可信（默认 TTL 60s ⇒ 5 分钟）。
_STALE_FACTOR = 5.0


@dataclass(frozen=True)
class FundingSnapshot:
    """一份资金费率读数（不可变；`fetched_wall` 是它的真实年龄基准）。"""

    rate: float
    funding_ts: int
    next_funding_ts: int
    interval_s: float
    fetched_wall: float


class FundingCache:
    """单标的资金费率缓存：TTL 内去重、异常吞并、陈旧即 `None`。"""

    def __init__(self, inst_id: str, *, ttl_s: float, stale_factor: float = _STALE_FACTOR,
                 enabled: bool = True) -> None:
        self.inst_id = inst_id
        self.ttl_s = float(ttl_s)
        self.stale_factor = float(stale_factor)
        self.enabled = bool(enabled)
        self._snapshot: FundingSnapshot | None = None
        self._last_try_wall = 0.0

    async def refresh(self, fetch_fn, now_wall: float) -> bool:
        """未到 TTL / 已禁用 ⇒ 直接 `False`；取数异常 ⇒ 吞掉并 `False`（上一份快照仍可读）。

        Args:
            fetch_fn: `async (inst_id) -> dict | None`（生产为 `OKXRestClient.get_funding_rate`）。
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
            row = await fetch_fn(self.inst_id)
        except Exception as exc:  # noqa: BLE001 —— 资金费率取数失败绝不打断盯市（§3.5）
            logger.debug("funding rate refresh failed (%s): %s", self.inst_id, exc)
            return False
        if not row:
            return False
        self._snapshot = FundingSnapshot(
            rate=float(row.get("fundingRate") or 0.0),
            funding_ts=int(row.get("fundingTs") or 0),
            next_funding_ts=int(row.get("nextFundingTs") or 0),
            interval_s=float(row.get("intervalS") or 0.0),
            fetched_wall=float(now_wall))
        return True

    def snapshot(self, now_wall: float) -> FundingSnapshot | None:
        """当前可用快照；从未取到 / 已陈旧 ⇒ `None`（调用方据此走 `confidence="degraded"`）。"""
        snap = self._snapshot
        if snap is None:
            return None
        if now_wall - snap.fetched_wall > self.ttl_s * self.stale_factor:
            return None
        return snap

    def wire(self, now_wall: float) -> dict | None:
        """camelCase 只读回执（供 trace / 排查）；无可用快照 ⇒ `None`。"""
        snap = self.snapshot(now_wall)
        if snap is None:
            return None
        return {"fundingRate": snap.rate, "fundingTs": snap.funding_ts,
                "nextFundingTs": snap.next_funding_ts, "intervalS": snap.interval_s}
