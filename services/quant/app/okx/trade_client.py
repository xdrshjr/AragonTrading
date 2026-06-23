"""OKX V5 交易客户端（实盘下单 · Phase 2「guarded order placement」）。

在只读 ``OKXPrivateClient`` 之上**扩展**签名 POST 与下单 / 设杠杆 / 查成交方法，复用基类的
``_get_client / _headers / _get`` 与模块级 ``_sign / _iso_timestamp``，**只新增写能力**——只读基类与
设置中心红线保持不变（写能力收敛于本子类与服务层）。

签名关键（C / §4.6）：POST 的签名预串为 ``ts + "POST" + requestPath + body``，``body`` 必须与实际
发送字节**完全一致**。故 ``_post`` 用紧凑 JSON（``separators=(",", ":")``）生成串并以 ``content=`` 原样
发送（严禁 ``json=payload``，httpx 默认序列化会与签名串不一致导致 50113 签名错误）。

显式错误（C1）：业务码 / sCode 非 ``"0"`` 或连通失败一律抛 ``OKXPrivateError``（原始 OKX 码），由服务层
``_map_trade_code`` 归一；绝不静默回退假数据。
"""
from __future__ import annotations

import json

import httpx

from .private_client import OKXPrivateClient, OKXPrivateError, _iso_timestamp


class OKXTradeClient(OKXPrivateClient):
    """实盘交易客户端：签名 POST 下单 / 设杠杆 + 签名 GET 查成交 / 挂单。"""

    async def _post(self, path: str, payload: dict) -> dict:
        """签名 POST：返回 OKX 完整响应体（dict）；连通 / HTTP 失败抛 ``OKXPrivateError``。

        业务码判定交由调用方（下单须同看顶层 ``code`` 与 ``data[0].sCode``，§2.2f）。
        对 ``httpx.TransportError`` 做**一次**重试，与基类 ``_get`` 口径一致。
        """
        client = await self._get_client()
        body = json.dumps(payload, separators=(",", ":"))
        last_exc: Exception | None = None
        for _ in range(2):
            ts = _iso_timestamp()
            headers = self._headers(ts, "POST", path, body)
            try:
                resp = await client.post(path, content=body, headers=headers)
                resp.raise_for_status()
                return resp.json()
            except httpx.TransportError as exc:  # 仅传输层异常重试，业务错误立即上抛
                last_exc = exc
            except httpx.HTTPStatusError as exc:
                raise OKXPrivateError("OKX_HTTP_ERROR",
                                      f"OKX HTTP {exc.response.status_code}") from exc
        raise OKXPrivateError("OKX_UNREACHABLE", f"无法连接 OKX ({path}): {last_exc}")

    async def place_order(self, payload: dict) -> dict:
        """``POST /api/v5/trade/order``：先看顶层 ``code`` 再看 ``data[0].sCode``，任一非 ``"0"`` 抛错。

        成功返回 ``data[0]``（含 ``ordId / clOrdId``）；失败以**具体 sCode**（缺则顶层 code）抛
        ``OKXPrivateError``，原始 ``sMsg`` 作 message，由服务层归一为语义码（§6）。
        """
        body = await self._post("/api/v5/trade/order", payload)
        rows = body.get("data") or []
        row = rows[0] if rows else {}
        top_code = str(body.get("code"))
        s_code = str(row.get("sCode") or "")
        if top_code != "0" or (s_code and s_code != "0"):
            code = s_code if s_code and s_code != "0" else top_code
            msg = row.get("sMsg") or body.get("msg") or "OKX 拒单"
            raise OKXPrivateError(code, str(msg))
        return row

    async def set_leverage(self, inst_id: str, lever: float, mgn_mode: str,
                           pos_side: str | None = None) -> dict:
        """``POST /api/v5/account/set-leverage``：设杠杆（幂等）。

        ``posSide`` 仅 ``mgnMode=="isolated"`` 且 long_short_mode 时传；cross / net_mode 省略（C2-1）。
        """
        payload: dict = {"instId": inst_id, "lever": str(lever), "mgnMode": mgn_mode}
        if pos_side:
            payload["posSide"] = pos_side
        body = await self._post("/api/v5/account/set-leverage", payload)
        if str(body.get("code")) != "0":
            raise OKXPrivateError(str(body.get("code")),
                                  str(body.get("msg") or "设置杠杆失败"))
        rows = body.get("data") or []
        return rows[0] if rows else {}

    async def get_fills(self, inst_id: str | None = None, limit: int = 100) -> list:
        """``GET /api/v5/trade/fills``：近 3 天成交明细（签名 GET，复用基类 ``_get``）。"""
        params: dict[str, str] = {"limit": str(limit)}
        if inst_id:
            params["instId"] = inst_id
        return await self._get("/api/v5/trade/fills", params)

    async def get_pending_orders(self, inst_id: str | None = None) -> list:
        """``GET /api/v5/trade/orders-pending``：当前未成交挂单（签名 GET）。"""
        params: dict[str, str] = {}
        if inst_id:
            params["instId"] = inst_id
        return await self._get("/api/v5/trade/orders-pending", params)
