"""服务器时钟对齐单测（okx-link-resilience §2.4.2）——根因 E 的前半。

OKX 对签名请求的 `OK-ACCESS-TIMESTAMP` 有 ±30s 容忍，超出即 `50102`。本机时钟漂移（尤其
Windows 笔记本休眠唤醒之后）会让**全部**私有请求在同一瞬间一起失败，而归一码 `OKX_TIME_SKEW`
之后此前没有任何自愈动作——用户只能自己去改系统时间。
"""
from __future__ import annotations

import re
import time

import pytest

from app.config import Settings
from app.okx.clock import ServerClock, get_server_clock
from app.okx.private_client import OKXPrivateClient, OKXPrivateError, _iso_timestamp

_ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")


async def test_offset_is_learned_and_shifts_the_signing_timestamp():
    clock = ServerClock()
    ahead_ms = time.time() * 1000.0 + 60_000.0

    offset = await clock.sync(lambda: _immediate(ahead_ms))

    assert offset == pytest.approx(60_000.0, abs=2000.0)
    assert clock.synced_ts is not None
    # 平移之后仍是 OKX 要求的那个格式（签名串的字节形状一字不变）
    assert _ISO_RE.match(clock.now_iso())


async def test_a_wild_reading_is_discarded_and_the_old_offset_survives():
    """`|offset| > 300s` 视为坏读：据它平移会把**全部**私有请求一起带偏。"""
    clock = ServerClock()
    await clock.sync(lambda: _immediate(time.time() * 1000.0 + 1000.0))
    good = clock.offset_ms

    await clock.sync(lambda: _immediate(time.time() * 1000.0 + 999_999.0))

    assert clock.offset_ms == good


def test_now_iso_matches_the_legacy_format_when_no_offset_is_known():
    """偏移未同步时 offset=0，输出与旧实现逐字节同形（既有签名用例因此不受影响）。"""
    clock = ServerClock()
    assert clock.offset_ms == 0.0
    assert _ISO_RE.match(clock.now_iso())
    assert _ISO_RE.match(_iso_timestamp())


def test_is_stale_respects_the_interval_and_the_disable_switch():
    clock = ServerClock()
    assert clock.is_stale(900.0) is True             # 从未同步过 ⇒ 该同步
    assert clock.is_stale(0.0) is False              # 0 = 只在 50102 时同步
    clock.synced_ts = 1000.0
    assert clock.is_stale(900.0, now=1500.0) is False
    assert clock.is_stale(900.0, now=2000.0) is True


def test_reset_clears_the_offset_and_clock_is_a_singleton():
    assert get_server_clock() is get_server_clock()
    clock = ServerClock()
    clock.offset_ms, clock.synced_ts = 123.0, 1.0
    clock.reset()
    assert clock.offset_ms == 0.0 and clock.synced_ts is None


# ---------------- 50102 自愈 ----------------


class _Resp:
    def __init__(self, body: dict):
        self.status_code = 200
        self._body = body

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._body


class _SkewClient:
    def __init__(self, bodies: list[dict]):
        self._bodies = list(bodies)
        self.calls = 0

    async def get(self, path, params=None, headers=None):
        self.calls += 1
        return _Resp(self._bodies[min(self.calls - 1, len(self._bodies) - 1)])


async def _immediate(value: float) -> float:
    return value


async def test_a_50102_triggers_exactly_one_clock_sync(monkeypatch):
    """收到 `50102` 即拉一次交易所时间；本次仍按原错误上抛（不在读路径里偷偷重试）。"""
    synced = {"count": 0}

    async def fake_sync(_self, fetch):
        synced["count"] += 1
        return 0.0

    monkeypatch.setattr("app.okx.clock.ServerClock.sync", fake_sync)
    client = OKXPrivateClient("ak", "sk-123456", "pp-123456",
                              settings=Settings(okx_link_private_rate_per_s=0.0))
    client._client = _SkewClient([{"code": "50102", "msg": "Timestamp request expired"}])

    with pytest.raises(OKXPrivateError) as exc:
        await client._get("/api/v5/account/config")

    assert exc.value.code == "50102"
    assert synced["count"] == 1


async def test_a_business_error_that_is_not_skew_never_syncs_the_clock(monkeypatch):
    synced = {"count": 0}

    async def fake_sync(_self, fetch):
        synced["count"] += 1
        return 0.0

    monkeypatch.setattr("app.okx.clock.ServerClock.sync", fake_sync)
    client = OKXPrivateClient("ak", "sk-123456", "pp-123456",
                              settings=Settings(okx_link_private_rate_per_s=0.0))
    client._client = _SkewClient([{"code": "51008", "msg": "insufficient"}])

    with pytest.raises(OKXPrivateError):
        await client._get("/api/v5/account/config")

    assert synced["count"] == 0


async def test_a_failing_clock_sync_never_replaces_the_original_error(monkeypatch):
    """自愈是尽力而为：时钟修不好不该把业务错误变成另一种失败。"""
    async def boom(_self, fetch):
        raise RuntimeError("交易所时间也拿不到")

    monkeypatch.setattr("app.okx.clock.ServerClock.sync", boom)
    client = OKXPrivateClient("ak", "sk-123456", "pp-123456",
                              settings=Settings(okx_link_private_rate_per_s=0.0))
    client._client = _SkewClient([{"code": "50104", "msg": "Timestamp expired"}])

    with pytest.raises(OKXPrivateError) as exc:
        await client._get("/api/v5/account/config")

    assert exc.value.code == "50104"
