"""WS 建连参数单测（okx-link-resilience [评审 P0-1]）——本轮最危险的一处。

`websockets` 15 的 `connect(proxy=…)` **默认值是 `True`**（走 `urllib.request.getproxies()`，
Windows 上**含注册表里的系统代理**）；传 `None` 是「**显式禁用**代理」而不是「不指定」。
而 `settings.proxy_url` 只读 `HTTPS_PROXY / HTTP_PROXY / ALL_PROXY` 三个 env——本仓库的用户大量
只配了系统代理（Clash Verge 等）而没有这三个 env，此时它恒为 `None`。

于是「无条件传 `proxy=settings.proxy_url`」这一手会把「有时断线」变成「**这批用户彻底连不上且
永不恢复**」，比现状严重得多。今天 WS 能连上，靠的正是库默认的系统代理发现。

⚠️ 这条只能守住「kwargs 里有没有那个键」；真正的判据是 §7.6-8 的真机人工门禁（只开系统代理、
不设 env 的 Windows 机器上启动打包版），因为离线替身根本不关心 `proxy` 传了什么。
"""
from __future__ import annotations

import inspect
from dataclasses import dataclass

import websockets

from app.okx.ws_client import _connect_kwargs

_LIB_SUPPORTS_PROXY = "proxy" in inspect.signature(websockets.connect).parameters


@dataclass
class _Settings:
    proxy_url: str | None


def test_empty_proxy_url_omits_the_proxy_key_entirely():
    """为空 ⇒ kwargs 里**不许出现** `proxy` 键（不是 `proxy=None`）。"""
    kwargs = _connect_kwargs(_Settings(proxy_url=None))
    assert "proxy" not in kwargs
    assert _connect_kwargs(_Settings(proxy_url="")) == kwargs


def test_a_configured_proxy_url_is_passed_through():
    kwargs = _connect_kwargs(_Settings(proxy_url="http://127.0.0.1:7897"))
    if _LIB_SUPPORTS_PROXY:
        assert kwargs["proxy"] == "http://127.0.0.1:7897"
    else:                       # pin 的 14.1 没有这个形参：能力探测须一律不传，否则 TypeError
        assert "proxy" not in kwargs


def test_ping_interval_none_is_preserved():
    """`ping_interval=None` 保留不动：应用层三重活性判据已覆盖存活，而改它会同时改变既有替身
    面对的参数面（`tests/test_okx_ws_endpoints.py` 断言的正是这份 kwargs）。"""
    kwargs = _connect_kwargs(_Settings(proxy_url=None))
    assert kwargs["ping_interval"] is None
    assert kwargs["open_timeout"] == 15


def test_the_library_default_for_proxy_is_truthy_not_none():
    """把 P0-1 的**前提事实**本身钉成一条用例：库默认不是 `None`，所以「不传」≠「传 None」。"""
    if not _LIB_SUPPORTS_PROXY:
        return
    default = inspect.signature(websockets.connect).parameters["proxy"].default
    assert default is not None
