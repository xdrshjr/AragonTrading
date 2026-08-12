"""私有写请求的幂等分级（okx-link-resilience §2.4.3）——纯表 + 纯判定，无 IO。

为什么必须分级：``trade_client._post`` 今天对 ``TransportError`` **一律**重试三次。对
``place_order`` / ``order-algo`` 这是安全的（OKX 按 ``clOrdId`` / ``algoClOrdId`` 去重，同一 body
重发不会变成第二张单）；但 ``close-position`` **没有这个承诺**——注意它虽然有 ``clOrdId`` 形参，
OKX 只对 ``order`` / ``order-algo`` 承诺按它去重。于是一次「请求已送达、响应在回程丢了」的超时，
重试就可能变成第二次平仓动作。

R-1 是本轮最严重的风险（资金损失），所以判据写成一张**白名单**：不在表里的写路径一律**不重试**。
新增写路径时忘了登记，最坏结果是「少一次自动重试」，而不是「多发一张单」。
"""
from __future__ import annotations

# 允许在传输层异常后重试的写路径 → 其去重键（``None`` 表示天然幂等，无需去重键）。
IDEMPOTENT_WRITES: dict[str, str | None] = {
    "/api/v5/trade/order": "clOrdId",          # OKX 按 clOrdId 去重
    "/api/v5/trade/order-algo": "algoClOrdId",  # OKX 按 algoClOrdId 去重
    "/api/v5/trade/cancel-order": None,        # 天然幂等（51400/51401/51402 已在服务层三分）
    "/api/v5/account/set-leverage": None,      # 幂等设置
}

# 显式登记的**非**幂等写：走「读回核验」而不是盲重试（§2.4.3）。
NON_IDEMPOTENT_WRITES = frozenset({"/api/v5/trade/close-position"})


def may_retry_write(path: str, payload: dict | None = None) -> bool:
    """该写路径在传输层异常后允许重试吗？

    Args:
        path: OKX 请求路径（不含 query）。
        payload: 请求体；带去重键的路径须**确实带上了**那个键，否则不许重试。

    Returns:
        ``True`` 仅当路径在 ``IDEMPOTENT_WRITES`` 且（若需要）去重键非空。未登记的路径一律
        ``False``——白名单的默认答案必须是「不重试」。
    """
    if path in NON_IDEMPOTENT_WRITES or path not in IDEMPOTENT_WRITES:
        return False
    dedup_key = IDEMPOTENT_WRITES[path]
    if dedup_key is None:
        return True
    return bool((payload or {}).get(dedup_key))
