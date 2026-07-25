"""资金费率会话级缓存单测（ai-trade-cost-accounting-and-edge-gate §7.2）。

三条不变式各有专项用例：TTL 去重（不刷配额）、异常全吞（取不到费率绝不该有能力打断盯市）、
陈旧即 `None`（陈旧数据绝不冒充新鲜——它会让成本模型以为自己 confidence="ok"）。
"""
from __future__ import annotations

from app.ai_auto.cost_model import estimate_round_trip_cost, resolve_cost_params, query_for_decision
from app.ai_auto.decider import TradeDecision
from app.ai_auto.funding import FundingCache
from app.config import Settings

_ROW = {"fundingRate": 0.0001, "fundingTs": 1_700_000_000, "nextFundingTs": 1_700_028_800,
        "intervalS": 28800.0}


def _counting_fetch(rows=None, exc: Exception | None = None):
    calls = {"n": 0, "inst": []}

    async def fetch(inst_id: str):
        calls["n"] += 1
        calls["inst"].append(inst_id)
        if exc is not None:
            raise exc
        return _ROW if rows is None else rows

    return fetch, calls


async def test_ttl_dedupes_repeated_refresh():
    """用例 1：TTL 内二次 refresh 不再调用 fetch_fn（5s 盯市 × 60s TTL ⇒ 每分钟至多一次）。"""
    cache = FundingCache("BTC-USDT-SWAP", ttl_s=60.0)
    fetch, calls = _counting_fetch()
    assert await cache.refresh(fetch, 1_000.0) is True
    assert await cache.refresh(fetch, 1_030.0) is False
    assert calls["n"] == 1 and calls["inst"] == ["BTC-USDT-SWAP"]
    assert await cache.refresh(fetch, 1_061.0) is True
    assert calls["n"] == 2


async def test_fetch_error_is_swallowed_and_last_snapshot_survives():
    """用例 2：取数异常 ⇒ 返回 False、**不上抛**、上一份快照仍可读。"""
    cache = FundingCache("BTC-USDT-SWAP", ttl_s=60.0)
    ok_fetch, _ = _counting_fetch()
    await cache.refresh(ok_fetch, 1_000.0)
    boom, _ = _counting_fetch(exc=RuntimeError("network down"))
    assert await cache.refresh(boom, 1_100.0) is False
    assert cache.snapshot(1_100.0) is not None and cache.snapshot(1_100.0).rate == 0.0001


async def test_stale_snapshot_never_masquerades_as_fresh():
    """用例 3：超过 `ttl_s × stale_factor` ⇒ snapshot 返回 None ⇒ 成本模型走 degraded。"""
    cache = FundingCache("BTC-USDT-SWAP", ttl_s=60.0, stale_factor=5.0)
    fetch, _ = _counting_fetch()
    await cache.refresh(fetch, 1_000.0)
    assert cache.snapshot(1_000.0 + 300.0) is not None      # 恰好 5×TTL：仍算新鲜
    assert cache.snapshot(1_000.0 + 301.0) is None
    assert cache.wire(1_000.0 + 301.0) is None


async def test_disabled_cache_never_sends_a_request():
    """门禁空转（默认 k_min=0）时缓存被构造为禁用态 ⇒ 一个公共请求都不发（spec §十二 I-2）。"""
    cache = FundingCache("BTC-USDT-SWAP", ttl_s=60.0, enabled=False)
    fetch, calls = _counting_fetch()
    assert await cache.refresh(fetch, 1_000.0) is False
    assert calls["n"] == 0
    assert cache.snapshot(1_000.0) is None


async def test_empty_response_does_not_write_a_snapshot():
    """非永续 / 空回执 ⇒ 不写快照（绝不用 0 费率冒充真实读数）。"""
    cache = FundingCache("BTC-USDT", ttl_s=60.0)
    fetch, _ = _counting_fetch(rows=None if False else {})
    assert await cache.refresh(fetch, 1_000.0) is False
    assert cache.snapshot(1_000.0) is None


def test_switch_off_yields_zero_rate_while_missing_yields_none():
    """用例 4：总闸关 ⇒ `0.0` + confidence ok；数据缺席 ⇒ `None` + degraded。二者不得混同。"""
    decision = TradeDecision(action="open_long", size_fraction=0.5, confidence=0.7, reason="x")
    params = resolve_cost_params(Settings())
    off = query_for_decision(decision, bar_seconds=900, funding=None, funding_enabled=False)
    missing = query_for_decision(decision, bar_seconds=900, funding=None, funding_enabled=True)
    assert off.funding_rate == 0.0
    assert missing.funding_rate is None
    assert estimate_round_trip_cost(off, params).confidence == "ok"
    assert estimate_round_trip_cost(missing, params).confidence == "degraded"


async def test_wire_is_camel_case_readonly_echo():
    cache = FundingCache("BTC-USDT-SWAP", ttl_s=60.0)
    fetch, _ = _counting_fetch()
    await cache.refresh(fetch, 1_000.0)
    wire = cache.wire(1_000.0)
    assert wire["fundingRate"] == 0.0001 and wire["intervalS"] == 28800.0
