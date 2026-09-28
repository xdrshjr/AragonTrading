"""在真正 POST 前同步比较冻结预览与实际发送证据。"""
from __future__ import annotations

import copy
import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Callable

from ..okx.private_client import OKXPrivateError


@dataclass(frozen=True)
class DispatchEvidence:
    """底层重取的事实；observed_at 使用单调时钟。"""
    payload: dict
    pos_mode: str
    ticker: float | None
    observed_at: float
    meta: object | None = None
    positions: list | None = None
    acct_lv: str | None = None


class DispatchGuard:
    """外层最早证据与底层重取证据必须在同一个五秒窗口内。"""

    def __init__(self, preview: dict, expires_ts: float,
                 enabled: Callable[[], bool]):
        self.preview = copy.deepcopy(preview)
        self.expires_ts = expires_ts
        self.enabled = enabled
        self.observed_at = time.monotonic()
        self.account_identity = None
        self.identity_checker = None

    async def refresh_identity(self) -> None:
        """限流和客户端等待后再核对数据库身份，随后同步守卫不再让出控制。"""
        if self.identity_checker is not None:
            await self.identity_checker()

    def validate(self, evidence: DispatchEvidence) -> None:
        """本地确定性拒绝发生在 trace.sent 之前。"""
        if not self.enabled():
            self._reject("AGENT_TRADING_DISABLED", "交易入口已关闭")
        if self.account_identity is not None:
            from .binding import current_environment
            if current_environment() != self.account_identity.environment:
                self._reject("ENVIRONMENT_CHANGED", "交易环境已变化")
        if (time.time() >= self.expires_ts or time.monotonic()
                - min(self.observed_at, evidence.observed_at) > 5):
            self._reject("PREVIEW_STALE", "交易证据或预览已过期")
        expected = self._normalized(self.preview["payload"])
        actual = self._normalized(evidence.payload)
        if actual != expected:
            self._reject("PREVIEW_STALE", "实际订单与冻结预览不一致")
        if self.preview.get("action") == "cancel":
            return
        if evidence.pos_mode not in {"net_mode", "long_short_mode"}:
            self._reject("PREVIEW_STALE", "账户持仓模式无法确认")
        frozen_mode = self.preview.get("posMode")
        if frozen_mode and frozen_mode != evidence.pos_mode:
            self._reject("PREVIEW_STALE", "账户持仓模式与预览不一致")
        if (self.preview.get("acctLv") is not None
                and str(evidence.acct_lv) != str(self.preview["acctLv"])):
            self._reject("PREVIEW_STALE", "账户模式与预览不一致")
        self._check_meta(evidence.meta)
        if self.preview.get("action") == "close":
            from ..okx.intent_close import match_position
            match_position(evidence.positions or [], self.preview)
        if actual.get("ordType") == "market":
            self._check_price(evidence.ticker)
        if self.preview.get("action") == "open":
            self._check_funds(evidence)

    def _check_meta(self, meta):
        constraints = self.preview.get("instrumentConstraints")
        if not constraints:
            return
        mapping = {"ctVal": "ct_val", "lotSz": "lot_sz",
                   "minSz": "min_sz", "tickSz": "tick_sz"}
        if meta is None or meta.ct_val_ccy != constraints["ctValCcy"]:
            self._reject("PREVIEW_STALE", "合约规则与预览不一致")
        if any(Decimal(str(getattr(meta, field))) != Decimal(str(constraints[key]))
               for key, field in mapping.items()):
            self._reject("PREVIEW_STALE", "合约精度或面值与预览不一致")

    def _check_funds(self, evidence):
        from ..config import get_settings
        body = evidence.payload
        price = Decimal(str(body.get("px") or evidence.ticker or 0))
        if body["ordType"] == "market":
            price *= Decimal("1.005")
        if evidence.meta is None:
            self._reject("PREVIEW_STALE", "缺少合约元数据")
        notional = Decimal(body["sz"]) * Decimal(str(evidence.meta.ct_val)) * price
        leverage = Decimal(str(self.preview.get("leverageObserved") or 0))
        available = Decimal(str(self.preview.get("availableMarginUsdt") or 0))
        if (leverage <= 0 or notional / leverage > available
                or notional > Decimal(str(get_settings().live_max_order_notional_usd))):
            self._reject("INSUFFICIENT_BALANCE", "实际订单超过可用保证金或名义上限")

    def _check_price(self, ticker) -> None:
        reference = Decimal(str(self.preview.get("referencePrice") or 0))
        price = Decimal(str(ticker or 0))
        if reference <= 0 or price <= 0:
            self._reject("PREVIEW_STALE", "缺少有效行情证据")
        if abs(price / reference - 1) > Decimal("0.005"):
            self._reject("PREVIEW_STALE", "市价相对预览变化超过 50 基点")

    @staticmethod
    def _normalized(payload):
        result = copy.deepcopy(payload)
        if result.get("posSide") == "net":
            result.pop("posSide")
        return result

    @staticmethod
    def _reject(code, message):
        raise OKXPrivateError(code, message)
