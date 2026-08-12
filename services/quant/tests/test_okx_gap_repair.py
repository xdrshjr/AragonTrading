"""断线补根取数单测（okx-link-resilience §2.2.4）。

两条硬边界：**不新增取数路径**（数据源就是既有的 `rest_client.get_candles`）、
**绝不返回未收线根**（把 forming 根当确认根喂进策略，等于让策略看见一根还在跳的 K 线）。
"""
from __future__ import annotations

import time

import pytest

from app.okx.gap_repair import fetch_missing, missing_bar_count
from app.okx.rest_client import OKXError
from app.okx.types import Candle

_BAR_S = 3600


class _FakeRest:
    """替身公共 REST：记录取数参数，按预置返回（或抛错）。"""

    def __init__(self, candles: list[Candle] | Exception):
        self._candles = candles
        self.calls: list[dict] = []

    async def get_candles(self, inst_id, bar, limit=300, *, before=None, after=None, probe=False):
        self.calls.append({"instId": inst_id, "bar": bar, "limit": limit, "probe": probe})
        if isinstance(self._candles, Exception):
            raise self._candles
        return list(self._candles)


class _WindowRest:
    """替身：像 OKX ``/candles`` 那样**只回最近 limit 根**，且最新一根是未收线的 forming 根。

    上面的 `_FakeRest` 忽略 `limit`，因此测不出「取数上限没给 forming 根留位置」这类缺陷。
    """

    def __init__(self, series: list[Candle]):
        self._series = series
        self.calls: list[int] = []

    async def get_candles(self, inst_id, bar, limit=300, *, before=None, after=None, probe=False):
        self.calls.append(limit)
        return list(self._series[-limit:])


def _candle(ts: int, confirm: bool = True) -> Candle:
    return Candle(ts=ts, o=1.0, h=1.0, l=1.0, c=1.0, vol=1.0, confirm=confirm)


def test_missing_bar_count_counts_only_bars_that_should_have_closed():
    now = 1_700_000_000 + 5 * _BAR_S
    assert missing_bar_count(1_700_000_000, _BAR_S, now) == 4    # 第 5 根还没收线
    assert missing_bar_count(None, _BAR_S, now) == 0             # 还没开始 ≠ 有空洞
    assert missing_bar_count(now, _BAR_S, now) == 0
    assert missing_bar_count(1_700_000_000, 0, now) == 0


async def test_only_closed_bars_after_since_ts_are_returned():
    now = int(time.time())
    base = now - now % _BAR_S - 10 * _BAR_S
    rest = _FakeRest([_candle(base + i * _BAR_S) for i in range(12)])

    out = await fetch_missing("BTC-USDT-SWAP", "1H", since_ts=base + 2 * _BAR_S,
                              max_bars=300, rest=rest)

    assert out, "有洞就该补上"
    assert all(c.ts > base + 2 * _BAR_S for c in out), "只补 since_ts 之后的"
    assert all(c.ts + _BAR_S <= now for c in out), "**绝不**返回未收线根"
    assert [c.ts for c in out] == sorted(c.ts for c in out), "升序"


async def test_a_forming_bar_is_excluded_even_if_the_exchange_returns_it():
    now = int(time.time())
    base = now - now % _BAR_S - 5 * _BAR_S
    forming = _candle(base + 4 * _BAR_S, confirm=False)
    rest = _FakeRest([_candle(base + i * _BAR_S) for i in range(4)] + [forming])

    out = await fetch_missing("BTC-USDT-SWAP", "1H", since_ts=base, max_bars=300, rest=rest)

    assert forming.ts not in [c.ts for c in out]


async def test_the_cap_truncates_to_the_most_recent_bars():
    now = int(time.time())
    base = now - now % _BAR_S - 20 * _BAR_S
    rest = _FakeRest([_candle(base + i * _BAR_S) for i in range(20)])

    out = await fetch_missing("BTC-USDT-SWAP", "1H", since_ts=base, max_bars=3, rest=rest)

    assert len(out) == 3
    assert out == sorted(out, key=lambda c: c.ts)
    assert rest.calls[0]["limit"] <= 4, "上限也要传给取数层，别白拉 300 根回来再扔掉（+1 是 forming 根的位置）"


async def test_the_request_leaves_room_for_the_forming_bar_so_no_bar_is_silently_lost():
    """回归：按缺根数**原样**取，每次都会稳定地少补最老的那一根。

    `/candles` 的最新一根是未收线的 forming 根，它一定会被 `fetch_missing` 的过滤器丢掉；取数
    上限若不给它留位置，返回的就永远比缺口少一根——而补完之后 `since_ts` 已经前移，那个洞再也
    不会被补上。一次「已补齐 N 根」的成功回报底下藏着一个洞，正是最难查的一类错。
    """
    now = int(time.time())
    base = now - now % _BAR_S - 5 * _BAR_S
    series = [_candle(base + i * _BAR_S) for i in range(5)] + [_candle(base + 5 * _BAR_S,
                                                                      confirm=False)]
    rest = _WindowRest(series)

    out = await fetch_missing("BTC-USDT-SWAP", "1H", since_ts=base, max_bars=300, rest=rest)

    assert [c.ts for c in out] == [base + i * _BAR_S for i in range(1, 5)], "四根缺口一根都不许漏"


async def test_no_gap_means_no_request_at_all():
    rest = _FakeRest([])
    assert await fetch_missing("BTC-USDT-SWAP", "1H", since_ts=int(time.time()),
                               max_bars=300, rest=rest) == []
    assert rest.calls == []


@pytest.mark.parametrize(("since_ts", "max_bars"), [(None, 300), (1_700_000_000, 0)])
async def test_disabled_or_unstarted_returns_empty_without_fetching(since_ts, max_bars):
    """`max_bars=0` 即关闭补根（关得掉的取值）；`since_ts=None` 是「还没开始」，不是空洞。"""
    rest = _FakeRest([_candle(1)])
    assert await fetch_missing("X", "1H", since_ts=since_ts, max_bars=max_bars, rest=rest) == []
    assert rest.calls == []


async def test_the_fetch_is_marked_as_a_probe_so_the_breaker_cannot_block_it():
    """断线与公共读故障高度同源：补根**恰好**发生在熔断最可能打开的窗口里。"""
    now = int(time.time())
    base = now - now % _BAR_S - 5 * _BAR_S
    rest = _FakeRest([_candle(base + i * _BAR_S) for i in range(5)])

    await fetch_missing("BTC-USDT-SWAP", "1H", since_ts=base, max_bars=300, rest=rest)

    assert rest.calls[0]["probe"] is True


async def test_a_fetch_failure_propagates_rather_than_pretending_there_was_no_gap():
    """本层**绝不**把一次取数失败伪装成「没有缺根」——失败语义由 session 侧的独立 task 决定。"""
    now = int(time.time())
    rest = _FakeRest(OKXError("OKX_UNREACHABLE", "boom"))

    with pytest.raises(OKXError):
        await fetch_missing("BTC-USDT-SWAP", "1H", since_ts=now - 10 * _BAR_S,
                            max_bars=300, rest=rest)
