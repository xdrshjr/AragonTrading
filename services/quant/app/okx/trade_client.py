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

import asyncio
import json

import httpx

from .private_client import OKXPrivateClient, OKXPrivateError, _iso_timestamp


class OKXTradeClient(OKXPrivateClient):
    """实盘交易客户端：签名 POST 下单 / 设杠杆 + 签名 GET 查成交 / 挂单。"""

    async def _post(self, path: str, payload: dict) -> dict:
        """签名 POST：返回 OKX 完整响应体（dict）；连通 / HTTP 失败抛 ``OKXPrivateError``。

        业务码判定交由调用方（下单须同看顶层 ``code`` 与 ``data[0].sCode``，§2.2f）。对
        ``httpx.TransportError`` 重试，并对 HTTP ``429`` 做指数退避重试，与基类 ``_get`` 口径一致。
        重试用同一 body / 同一 ``clOrdId``，OKX 按 ``clOrdId`` 去重，故即便首发已落地重试也不会重复下单
        （幂等安全）。非 429 的 4xx/5xx 仍立即上抛。
        """
        client = await self._get_client()
        body = json.dumps(payload, separators=(",", ":"))
        last_exc: Exception | None = None
        rate_limited = False
        for attempt in range(3):
            ts = _iso_timestamp()
            headers = self._headers(ts, "POST", path, body)
            try:
                resp = await client.post(path, content=body, headers=headers)
                if resp.status_code == 429:   # 限流：退避后重试，不直接抛
                    rate_limited = True
                    await asyncio.sleep(0.5 * (2 ** attempt))
                    continue
                resp.raise_for_status()
                return resp.json()
            except httpx.TransportError as exc:  # 仅传输层异常重试，业务错误立即上抛
                last_exc = exc
                rate_limited = False
            except httpx.HTTPStatusError as exc:  # 非 429 的 4xx/5xx：语义不变，立即上抛
                raise OKXPrivateError("OKX_HTTP_ERROR",
                                      f"OKX HTTP {exc.response.status_code}") from exc
        if rate_limited:   # 退避耗尽仍 429：显式回 OKX_HTTP_ERROR，避免 last_exc=None 的误导串
            raise OKXPrivateError("OKX_HTTP_ERROR", "OKX HTTP 429")
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

    async def cancel_order(self, inst_id: str, *, ord_id: str | None = None,
                           cl_ord_id: str | None = None) -> dict:
        """``POST /api/v5/trade/cancel-order``：撤一笔未成交挂单；``ordId`` 与 ``clOrdId`` 二选一。

        与 ``place_order`` 同口径：顶层 ``code`` 与 ``data[0].sCode`` 皆须为 ``"0"``，否则以更具体的
        sCode 抛 ``OKXPrivateError``。本层**只忠实上抛原始 sCode，不做良性 / 非良性的价值判断**——
        「已撤销(51401) / 已成交(51402) / 不存在(51400)」的三态归因由服务层 ``cancel_live_order``
        与 ``LiveExecutor._cancel_resting`` 负责（ai-trade-limit-orders §2.5 / §4.3）。

        Args:
            inst_id: 合约 ID（OKX 撤单必填）。
            ord_id: OKX 订单 ID（与 ``cl_ord_id`` 至少给一个）。
            cl_ord_id: 我方自定义订单 ID。

        Returns:
            ``data[0]``（含 ``ordId`` / ``clOrdId``）。

        Raises:
            OKXPrivateError: 二者皆缺（本地拦截）、连通失败、或业务码非 ``"0"``（带原始 sCode）。
        """
        if not ord_id and not cl_ord_id:
            raise OKXPrivateError("ORDER_PARAM_INVALID", "撤单必须提供 ordId 或 clOrdId 之一")
        payload: dict = {"instId": inst_id}
        if ord_id:
            payload["ordId"] = ord_id
        if cl_ord_id:
            payload["clOrdId"] = cl_ord_id
        body = await self._post("/api/v5/trade/cancel-order", payload)
        rows = body.get("data") or []
        row = rows[0] if rows else {}
        top_code = str(body.get("code"))
        s_code = str(row.get("sCode") or "")
        if top_code != "0" or (s_code and s_code != "0"):
            code = s_code if s_code and s_code != "0" else top_code
            msg = row.get("sMsg") or body.get("msg") or "OKX 撤单失败"
            raise OKXPrivateError(code, str(msg))
        return row

    async def get_order(self, inst_id: str, *, ord_id: str | None = None,
                        cl_ord_id: str | None = None) -> dict:
        """``GET /api/v5/trade/order``：单条订单的**权威**状态（签名 GET，复用基类 ``_get``）。

        关键回读字段：``state`` ∈ {live, partially_filled, filled, canceled, mmp_canceled}、
        ``avgPx``（成交均价）、``accFillSz``（累计成交张数）、``px`` / ``sz`` / ``ordId`` / ``clOrdId``
        / ``uTime``。之所以用本接口而非 ``orders-pending``：后者只能回答「还挂着吗」，「不在挂单列表」
        对「已成交」与「已撤销」是**二义**的（§2.3）。

        Args:
            inst_id: 合约 ID（OKX 必填）。
            ord_id: OKX 订单 ID（与 ``cl_ord_id`` 至少给一个）。
            cl_ord_id: 我方自定义订单 ID。

        Returns:
            单条订单 dict；OKX 回空数组（订单不存在）时为 ``{}``。

        Raises:
            OKXPrivateError: 二者皆缺（本地拦截）、连通失败、或业务码非 ``"0"``。
        """
        if not ord_id and not cl_ord_id:
            raise OKXPrivateError("ORDER_PARAM_INVALID", "查询订单必须提供 ordId 或 clOrdId 之一")
        params: dict[str, str] = {"instId": inst_id}
        if ord_id:
            params["ordId"] = ord_id
        if cl_ord_id:
            params["clOrdId"] = cl_ord_id
        rows = await self._get("/api/v5/trade/order", params)
        return rows[0] if rows else {}

    async def close_position(self, inst_id: str, pos_side: str, mgn_mode: str, *,
                             auto_cxl: bool = True, cl_ord_id: str | None = None) -> dict:
        """``POST /api/v5/trade/close-position``：按当前市价**整仓**平掉某持仓（平仓 spec §2.2）。

        ``posSide`` 对冲取持仓方向、净持仓取 ``"net"``；``mgnMode`` 取该仓保证金模式；``autoCxl`` 撤掉该
        合约未成交挂单。该接口**不返回 ordId**（返回 ``instId/posSide/clOrdId``）。判成功：顶层 ``code=="0"``
        且 ``data[0].sCode`` 非错（P2-3，与 ``place_order`` 口径一致，防 OKX 在逐项位另带错误码）。
        """
        payload: dict = {"instId": inst_id, "mgnMode": mgn_mode, "posSide": pos_side,
                         "autoCxl": auto_cxl}
        if cl_ord_id:
            payload["clOrdId"] = cl_ord_id
        body = await self._post("/api/v5/trade/close-position", payload)
        rows = body.get("data") or []
        row = rows[0] if rows else {}
        top_code = str(body.get("code"))
        s_code = str(row.get("sCode") or "")
        if top_code != "0" or (s_code and s_code != "0"):
            code = s_code if s_code and s_code != "0" else top_code
            msg = row.get("sMsg") or body.get("msg") or "OKX 平仓失败"
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

    async def get_trade_fee(self, inst_type: str, inst_id: str | None = None) -> dict:
        """``GET /api/v5/account/trade-fee``：本账户在该合约上的**真实**费率档位（签名 GET）。

        关键回读字段：``level``（如 ``Lv1``）/ ``ruleType``/ ``maker``/``taker``（币本位与现货）/
        ``makerU``/``takerU``（USDT 保证金合约）/ ``makerUSDC``/``takerUSDC``。费率为**有符号字符串**，
        负数 = 平台收取、正数 = 返佣（归一与夹紧在服务层 ``_normalize_trade_fee``）。

        Args:
            inst_type: 产品类型（本项目恒 ``"SWAP"``）。
            inst_id: 合约 ID（可选；不传即取该 instType 的账户通用档位）。

        Returns:
            ``data[0]``；OKX 回空数组时为 ``{}``。
        """
        params: dict[str, str] = {"instType": inst_type}
        if inst_id:
            params["instId"] = inst_id
        rows = await self._get("/api/v5/account/trade-fee", params)
        return rows[0] if rows else {}

    async def get_position_history(self, inst_type: str, inst_id: str | None = None, *,
                                   limit: int = 50, after_ms: int | None = None) -> list:
        """``GET /api/v5/account/positions-history``：交易所侧**已结算持仓**的权威盈亏（签名 GET）。

        关键回读字段：``posId``/``instId``/``direction``/``openAvgPx``/``closeAvgPx``/``cTime``/
        ``uTime``/``pnl``/``fee``/``fundingFee``/``liqPenalty``/``realizedPnl``。后四项按 OKX 口径
        「负数 = 支出」，符号归一在服务层。覆盖最近 3 个月。

        Args:
            inst_type: 产品类型（本项目恒 ``"SWAP"``）。
            inst_id: 合约 ID（可选）。
            limit: 单页条数（OKX 上限 100）。
            after_ms: 只取 ``uTime`` 晚于该毫秒时间戳的记录（可选）。
        """
        params: dict[str, str] = {"instType": inst_type, "limit": str(min(int(limit), 100))}
        if inst_id:
            params["instId"] = inst_id
        if after_ms:
            params["after"] = str(int(after_ms))
        return await self._get("/api/v5/account/positions-history", params)

    async def get_pending_orders(self, inst_id: str | None = None) -> list:
        """``GET /api/v5/trade/orders-pending``：当前未成交挂单（签名 GET）。"""
        params: dict[str, str] = {}
        if inst_id:
            params["instId"] = inst_id
        return await self._get("/api/v5/trade/orders-pending", params)

    async def get_algo_pending_orders(self, ord_type: str, inst_id: str | None = None) -> list:
        """``GET /api/v5/trade/orders-algo-pending``：未触发的策略委托（签名 GET）。

        ``ordType`` 为必填，常用 ``"oco"``（随单止盈止损）/ ``"conditional"``（单向条件单）。
        """
        params: dict[str, str] = {"ordType": ord_type}
        if inst_id:
            params["instId"] = inst_id
        return await self._get("/api/v5/trade/orders-algo-pending", params)
