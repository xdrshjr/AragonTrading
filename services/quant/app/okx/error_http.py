"""非 2xx 的 OKX 响应 → 一行可诊断描述（ai-trade-silent-degradation-repair 轴 A · A-1）。

**本模块是叶子：零仓内依赖。** 这不是风格偏好，是硬约束——它同时是 ``_redact`` 的新宿主，
而 ``_redact`` 的旧宿主 ``okx/accounts.py`` **反向 import 了** ``private_client``
（``accounts.py:16``）。在 ``private_client.py`` 里 ``from .accounts import _redact`` 即 import
期环，症状是整个后端起不来（本仓刚为一次同形的 import 期崩溃付过代价，见 ``api/ai_intel.py``
顶部关于 ``-> None`` 与 204 的注释）。``accounts.py`` 改为从这里 re-export，故
``trading.py:23`` 的既有 import 一字不改、拿到的仍是同一个函数对象（门禁 G-A13 直接断言
``accounts._redact is error_http._redact``）。

它要修的事实：`private_client._get`（:244）与 `trade_client._post`（:74）在非 429 的 4xx/5xx
上**只留下 HTTP 状态码**，而紧随其后的业务码分支是把 ``body["code"]`` / ``body["msg"]`` 原样
带上的。也就是说——**OKX 在 HTTP 200 里说的话我们听，在 HTTP 400 里说的话我们不听**，而 400
恰恰是「你的参数不对，具体是这个」的那一类。生产日志里 1,524 次 `trade-fee` 400，每一次的
响应体都写着为什么，我们一次都没读过。
"""
from __future__ import annotations

import json

# 对外 message 的截断上限（与 ``accounts._MESSAGE_MAX`` / ``link_state._MESSAGE_MAX`` 同值）。
MESSAGE_MAX = 200


def describe_http_error(status: int, body: bytes | str | None, *,
                        max_msg: int = MESSAGE_MAX) -> str:
    """把一次非 2xx 的 OKX 响应压成一行可诊断的描述。

    形状（三档，逐档退化）：

    1. 体是 JSON 且含 ``code`` / ``msg`` ⇒ ``"OKX HTTP 400 [51000] Parameter instId error"``
    2. 体是可打印文本                    ⇒ ``"OKX HTTP 400 <前 max_msg 字符，换行压成空格>"``
    3. 体缺席 / 解码失败 / 空            ⇒ ``"OKX HTTP 400"``  ← 与本轮之前逐字一致

    **绝不抛**：任何解析异常都退到第 3 档。它跑在异常处理路径上，一个会抛的诊断函数会把
    「参数错」变成「诊断函数自己崩了」。

    截断**按字符不按字节**：第 1 轮 `8451163` 已为同形问题付过一次代价（`reason` 列宽那次）。

    Args:
        status: HTTP 状态码。
        body: 响应体（``bytes`` / ``str`` / ``None``）。
        max_msg: 附加描述的字符上限（不含 ``"OKX HTTP {status}"`` 前缀）。

    Returns:
        一行描述串；无可用信息时逐字为 ``f"OKX HTTP {status}"``。
    """
    head = f"OKX HTTP {status}"
    try:
        text = _decode(body)
        if not text:
            return head
        detail = _from_json(text) or _flatten(text)
        if not detail:
            return head
        return f"{head} {detail[:max(1, int(max_msg))]}"
    except Exception:  # noqa: BLE001 —— 诊断函数绝不抛，见 docstring 第 3 档
        return head


def _decode(body: bytes | str | None) -> str:
    """响应体 → 文本；``None`` / 非 UTF-8 / 非文本 ⇒ 空串（调用方退到第 3 档）。

    **解码用 ``strict`` 而不是 ``replace``**：一串二进制垃圾被替换字符渲染出来之后仍然是二进制
    垃圾，只是多占了 200 个字符的日志宽度并让「这一行到底说了什么」更难读。既然它不可能是 OKX
    的错误体，就该老实退回第 3 档。
    """
    if body is None:
        return ""
    if isinstance(body, bytes):
        try:
            return body.decode("utf-8").strip()
        except UnicodeDecodeError:
            return ""
    return str(body).strip()


def _from_json(text: str) -> str:
    """第 1 档：体是 JSON 对象且含 ``code`` / ``msg`` ⇒ ``"[51000] Parameter instId error"``。

    两者皆缺 ⇒ 空串（退到第 2 档，把原文本身当描述——它至少还携带了一点信息）。
    """
    if not text.startswith("{"):
        return ""
    try:
        payload = json.loads(text)
    except (ValueError, TypeError):
        return ""
    if not isinstance(payload, dict):
        return ""
    code = str(payload.get("code") or "").strip()
    msg = _flatten(str(payload.get("msg") or ""))
    if code and msg:
        return f"[{code}] {msg}"
    if code:
        return f"[{code}]"
    return msg


def _flatten(text: str) -> str:
    """换行 / 制表压成单空格并折叠连续空白（日志与 wire 都要求单行）。"""
    return " ".join(str(text or "").split())


def _redact(text: str, *secrets: str) -> str:
    """把 secret / passphrase 子串（长度 ≥6）替换为脱敏占位再截断（C7 单点收口）。

    **实现逐字节沿用它在 ``okx/accounts.py:67`` 时的样子**，只是搬了家（见模块 docstring 的
    循环导入理由）。``accounts`` 原样 re-export，全仓既有 import 面一字不改。
    """
    out = text or ""
    for s in secrets:
        s = (s or "").strip()
        if s and len(s) >= 6:
            out = out.replace(s, "····")
    return out[:MESSAGE_MAX]
