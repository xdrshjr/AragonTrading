"""可交易性预检的**取数层**（轴 A · G-3 / G-3b / §十.8）。

判据本身由 `test_ai_trade_tradability.py` 逐行钉死；本文件只守取数：REST/缓存双路、无条件读缓存
的**有意偏离**、逐周期成本重算、以及「`alternatives` 绝不递归」。
"""
from __future__ import annotations

import pytest

from app.ai_auto import tradability_probe as probe_mod
from app.ai_auto.tradability_probe import BARS, atr_bps_series, probe
from app.config import Settings
from app.okx.types import Candle


def _candles(n: int, *, start: float = 100_000.0, amp: float = 30.0) -> list[Candle]:
    """一段可复算的合成 K 线（锯齿波，保证 ATR 恒为正且样本足够）。"""
    rows = []
    for i in range(n):
        close = start + (amp if i % 2 else -amp)
        rows.append(Candle(ts=1_700_000_000 + i * 60, o=start, h=close + amp,
                           l=close - amp, c=close, vol=1.0))
    return rows


class _Rest:
    """REST 桩：`fail=True` ⇒ 模拟 OKX 不可达（探针必须回落缓存且仍 200）。"""

    def __init__(self, rows: list[Candle], fail: bool = False):
        self.rows = rows
        self.fail = fail
        self.calls: list[tuple[str, str, int]] = []

    async def get_candles(self, inst_id: str, bar: str, limit: int, **_kw):
        self.calls.append((inst_id, bar, limit))
        if self.fail:
            raise RuntimeError("okx down")
        return self.rows


class _Db:
    """缓存桩：只暴露 `get_cached_candles` 会用到的那条查询路径。"""

    def __init__(self, rows: list[dict] | None):
        self.rows = rows
        self.calls = 0


@pytest.fixture(autouse=True)
def _patch_repo(monkeypatch):
    """把缓存回落接到 `_Db` 桩上（真实 `repositories` 需要一个 AsyncSession）。"""

    async def _cached(session, inst_id, bar, limit):
        session.calls += 1
        return list(session.rows or [])[:limit]

    import app.db.repositories as repo
    monkeypatch.setattr(repo, "get_cached_candles", _cached)


def _settings(**over) -> Settings:
    base = dict(_env_file=None, ai_trade_edge_gate_k_min=1.5, ai_trade_stop_atr_mult=2.0,
                ai_trade_tradability_window_bars=200)
    base.update(over)
    return Settings(**base)


# ---------- ATR 序列复用同一条公式 ----------
def test_atr_series_drops_the_warmup_rows():
    rows = [{"ts": i, "o": 1.0, "h": 1.1, "l": 0.9, "c": 1.0} for i in range(30)]
    series = atr_bps_series(rows, 14)
    assert len(series) == 30 - 14 + 1        # `ewm(min_periods=14)` ⇒ 前 13 行 NaN
    assert all(v > 0 for v in series)
    assert atr_bps_series(rows[:10], 14) == ()


# ---------- G-3：OKX 不可达 + 有缓存 ⇒ 200 且 source == "cache" ----------
async def test_cache_fallback_works_with_allow_cached_fallback_left_at_its_default(monkeypatch):
    """**[P1-4] 有意偏离**：探针无条件读缓存，不受 `allow_cached_fallback`（默认 False）门控。

    那个开关要防的是「用陈旧价格去下单」，而探针算的是一个历史窗口统计量、不驱动任何交易判定。
    照抄 `api/market.py:100` 的门控会让「数据就在本地却说算不出」——用户本机 `candles_cache` 里
    正躺着几万根 BTC 1m。
    """
    settings = _settings()
    assert settings.allow_cached_fallback is False       # 默认值原样，不靠改配置蒙混过关
    monkeypatch.setattr(probe_mod, "get_rest_client", lambda: _Rest([], fail=True))
    db = _Db([{"ts": 1_700_000_000 + i * 60, "o": 100_000.0, "h": 100_030.0,
               "l": 99_970.0, "c": 100_000.0 + (30 if i % 2 else -30)} for i in range(200)])
    wire = await probe(db, settings, inst_id="BTC-USDT-SWAP", bar="1m",
                       with_alternatives=False)
    assert wire["source"] == "cache"
    assert wire["level"] != "unknown"
    assert wire["asOfTs"] == 1_700_000_000 + 199 * 60
    assert db.calls == 1


async def test_no_data_anywhere_yields_unknown_but_never_raises(monkeypatch):
    monkeypatch.setattr(probe_mod, "get_rest_client", lambda: _Rest([], fail=True))
    wire = await probe(_Db(None), _settings(), inst_id="BTC-USDT-SWAP", bar="1m",
                       with_alternatives=False)
    assert wire["level"] == "unknown" and wire["source"] is None
    assert "算不出" in wire["headline"]


async def test_live_path_is_preferred_and_labelled(monkeypatch):
    rest = _Rest(_candles(200))
    monkeypatch.setattr(probe_mod, "get_rest_client", lambda: rest)
    db = _Db(None)
    wire = await probe(db, _settings(), inst_id="BTC-USDT-SWAP", bar="1m",
                       with_alternatives=False)
    assert wire["source"] == "live" and db.calls == 0
    assert rest.calls[0][2] == 200                  # 单发区间内，不触发 history-candles 翻页


# ---------- 总闸关 ⇒ 端点仍在但恒 unknown ----------
async def test_master_switch_off_keeps_the_endpoint_alive_but_silent(monkeypatch):
    monkeypatch.setattr(probe_mod, "get_rest_client", lambda: _Rest(_candles(200)))
    wire = await probe(_Db(None), _settings(ai_trade_tradability_enabled=False),
                       inst_id="BTC-USDT-SWAP", bar="1m")
    assert wire["level"] == "unknown" and "整轴关闭" in wire["headline"]


# ---------- G-3b：`alternatives` 逐周期重算成本 ----------
async def test_alternatives_recompute_cost_with_their_own_bar_seconds(monkeypatch):
    """`funding_periods = ceil(expected_hold_bars × bar_seconds / interval)`：
    `1D × 8 根 = 8 天 ⇒ 24 期`，与 `1m` 的 1 期不是一个数量级。沿用当前 bar 的成本会让长周期
    看起来比真实**更可行**，而 `advice` 的第一条规则**正是**据此推荐周期的。
    """
    monkeypatch.setattr(probe_mod, "get_rest_client", lambda: _Rest(_candles(200)))
    settings = _settings(ai_trade_funding_enabled=True)
    wire = await probe(_Db(None), settings, inst_id="BTC-USDT-SWAP", bar="1m")
    bars = [item["bar"] for item in wire["alternatives"]]
    assert bars == list(BARS[1:])                      # 只探测**比当前更长**的周期
    assert all(item["costBpsMarket"] is not None for item in wire["alternatives"])


async def test_alternatives_are_omitted_not_faked_when_a_bar_cannot_be_probed(monkeypatch):
    """任一周期失败即**从列表里省略该项**（不填 null 冒充）。"""

    class _PartialRest(_Rest):
        async def get_candles(self, inst_id, bar, limit, **_kw):
            if bar in ("5m", "15m"):
                raise RuntimeError("no data")
            return self.rows

    monkeypatch.setattr(probe_mod, "get_rest_client", lambda: _PartialRest(_candles(200)))
    wire = await probe(_Db(None), _settings(), inst_id="BTC-USDT-SWAP", bar="1m")
    bars = [item["bar"] for item in wire["alternatives"]]
    assert "5m" not in bars and "15m" not in bars
    assert all(item is not None for item in wire["alternatives"])


# ---------- §十.8：alternatives 绝不递归 ----------
async def test_alternatives_never_recurse_into_their_own_alternatives(monkeypatch):
    """否则一次弹窗打开会放大成 O(n²) 次请求。7 个周期 ⇒ 恰好 1 + 6 次取数。"""
    rest = _Rest(_candles(200))
    monkeypatch.setattr(probe_mod, "get_rest_client", lambda: rest)
    await probe(_Db(None), _settings(), inst_id="BTC-USDT-SWAP", bar="1m")
    assert len(rest.calls) == len(BARS)          # 当前 bar 1 次 + 更长的 6 个各 1 次
