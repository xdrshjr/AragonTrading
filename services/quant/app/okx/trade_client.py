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

from .call_stats import get_call_stats
from .inst_family import inst_family_of
from .private_client import OKXPrivateClient, OKXPrivateError, _iso_timestamp
from .types import FeeAttemptError
from .write_safety import may_retry_write


def should_advance(exc: OKXPrivateError) -> bool:
    """这一跳的失败是不是「参数不对」——**只有它才配让费率阶梯往下走一格**（轴 A-2）。

    判据取自 ``exc.http_status``（A-1 新增的只读加法字段），**严禁解析 message 字符串**：
    今天 ``_get`` 把「HTTP 400 参数错」与「429 退避耗尽」都抛成
    ``OKXPrivateError("OKX_HTTP_ERROR", …)``，以 message 为判据就是在一个**不携带该信息的
    入参**上做判定（本仓成文的缺陷形状，黑板已为它登记过四次）。

    真值表（逐格由门禁 G-A7b 冻结）：

    ==================== ======= ==============================================
    ``http_status``      结果    理由
    ==================== ======= ==============================================
    ``None``             False   传输层失败 / ``OKX_UNREACHABLE``：换个参数也连不上
    ``429``              False   退避已耗尽，再发一跳是在给限频火上浇油
    ``401`` / ``403``    False   凭证问题，与参数无关
    ``400 ≤ s < 500``    True    「你传错了，具体是这个」——正是阶梯要处理的那一类
    ``s ≥ 500``          False   交易所侧故障，重试策略归 ``_get`` 管，不归阶梯管
    ==================== ======= ==============================================

    **不推进即原样上抛，而不是返回空**：后者会把一次真实的链路故障静默降级成「费率取不到」，
    于是真值源卡片会在断网时说「交易所拒绝了费率查询」——一句**指着错误方向**的人话，比不
    说话更糟。
    """
    status = getattr(exc, "http_status", None)
    if status is None:
        return False
    code = int(status)
    if code in (401, 403, 429):
        return False
    return 400 <= code < 500


class OKXTradeClient(OKXPrivateClient):
    """实盘交易客户端：签名 POST 下单 / 设杠杆 + 签名 GET 查成交 / 挂单。"""

    async def _post(self, path: str, payload: dict, *, allow_retry: bool | None = None) -> dict:
        """签名 POST：返回 OKX 完整响应体（dict）；连通 / HTTP 失败抛 ``OKXPrivateError``。

        业务码判定交由调用方（下单须同看顶层 ``code`` 与 ``data[0].sCode``，§2.2f）。HTTP ``429``
        照旧指数退避重试（与基类 ``_get`` 口径一致）；**传输层异常的重试改为按幂等分级放行**
        （okx-link-resilience §2.4.3）：带去重键或天然幂等的写才重试，其余首次异常即上抛。

        默认兼容原有写请求重试策略。客户端 ID 仅在当前挂单中唯一，不能视为
        历史订单幂等保证；AI 开仓必须显式传 False，响应丢失后改查原 ID。
        ``close-position`` 同样不能盲目重发，否则可能重复平仓。

        F2（ai-trade-interval-wake-dead-monitor-loop §8.2）：与 ``_get`` 同款结构化修法——``try``
        收窄到**只包住那一次网络调用**，谓词随之放宽为「进了这个 except 分支」，使
        ``anyio.ClosedResourceError`` 一类**非 httpx** 的传输失败也走幂等分级而不是原样穿透。
        **收窄 ``try`` 在写路径上是硬性前提**：``resp.json()`` 原本也在 ``try`` 里，只放宽关键字会
        把一个畸形响应当成传输失败，而 ``place_order`` 的 ``retryable`` 为真 ⇒ **重复下单**，正是
        上面 R-1 标注的那件事。收窄之后，``json()`` 的解析错误照旧原样上抛，重试判定只受真正的
        网络失败驱动。

        Args:
            path: OKX 请求路径。
            payload: 请求体（``may_retry_write`` 会检查它是否**确实带上了**去重键）。
            allow_retry: 覆盖幂等判定；``None`` 即交给 ``write_safety.may_retry_write``。
        """
        retryable = may_retry_write(path, payload) if allow_retry is None else bool(allow_retry)
        body = json.dumps(payload, separators=(",", ":"))
        last_exc: Exception | None = None
        rate_limited = False
        for attempt in range(3):
            await self._acquire_slot()
            client = await self._get_client()   # 每跳都问池要（换代后立刻拿到新连接，P1-1）
            ts = _iso_timestamp()
            headers = self._headers(ts, "POST", path, body)
            try:
                resp = await client.post(path, content=body, headers=headers)
            except Exception as exc:  # noqa: BLE001 —— 谓词见 docstring：进了这里即传输层失败
                get_call_stats().note("POST", path, transport_fail=True)  # 轴 D-3：签名 POST 出口
                last_exc = exc                   # 传输层异常：只有幂等写才允许再发一次
                rate_limited = False
                await self._invalidate_pooled()
                if not retryable:
                    break
                continue
            get_call_stats().note("POST", path, status=resp.status_code)
            if resp.status_code == 429:   # 限流：退避后重试，不直接抛
                rate_limited = True
                if allow_retry is False:
                    break
                await asyncio.sleep(0.5 * (2 ** attempt))
                continue
            try:
                resp.raise_for_status()
            except httpx.HTTPStatusError as exc:  # 非 429 的 4xx/5xx：语义不变，立即上抛
                # 轴 A-1 / A-3（POST 侧，与基类 `_get` 逐字同形）：带出 OKX 的 code/msg，并按
                # HTTP 状态分级登记（4xx 走中性的 `note_rejected`，不再污染链路连续失败计数）。
                status = exc.response.status_code
                detail = self._http_detail(status, exc.response.content)
                self._note_http_status(path, status, detail)
                raise OKXPrivateError("OKX_HTTP_ERROR", detail, http_status=status) from exc
            self._note_link_ok()
            return resp.json()
        if rate_limited:   # 退避耗尽仍 429：显式回 OKX_HTTP_ERROR，避免 last_exc=None 的误导串
            self._note_link_fail("OKX_HTTP_ERROR", f"{path}: HTTP 429")
            raise OKXPrivateError("OKX_HTTP_ERROR", "OKX HTTP 429", http_status=429)
        self._note_link_fail("OKX_UNREACHABLE", f"{path}: {last_exc}")
        raise OKXPrivateError("OKX_UNREACHABLE", f"无法连接 OKX ({path}): {last_exc}")

    async def place_order(self, payload: dict, *,
                          allow_retry: bool | None = None) -> dict:
        """``POST /api/v5/trade/order``：先看顶层 ``code`` 再看 ``data[0].sCode``，任一非 ``"0"`` 抛错。

        成功返回 ``data[0]``（含 ``ordId / clOrdId``）；失败以**具体 sCode**（缺则顶层 code）抛
        ``OKXPrivateError``，原始 ``sMsg`` 作 message，由服务层归一为语义码（§6）。
        """
        body = await self._post(
            "/api/v5/trade/order", payload, allow_retry=allow_retry)
        rows = body.get("data") or []
        row = rows[0] if rows else {}
        top_code = str(body.get("code"))
        s_code = str(row.get("sCode") or "")
        if top_code != "0" or (s_code and s_code != "0"):
            code = s_code if s_code and s_code != "0" else top_code
            msg = row.get("sMsg") or body.get("msg") or "OKX 拒单"
            raise OKXPrivateError(code, str(msg))
        return row

    async def place_algo_order(self, payload: dict) -> dict:
        """``POST /api/v5/trade/order-algo``：下一张算法委托（本类**唯一**的算法委托写路径）。

        业务码判定与 ``place_order``（:60-75）**逐字同构**：先看顶层 ``code`` 再看 ``data[0].sCode``，
        任一非 ``"0"`` 即以更具体的那个码抛 ``OKXPrivateError``，由服务层 ``_map_trade_code`` 归一。

        **本方法只被交易所侧兜底自愈路径调用**（``okx.trading.place_backstop_stop``），不对外开放
        通用算法委托能力：本仓库全部 AI 侧安全层此前的共同纪律是「绝不新增下单路径」，本轮是第一次
        破例，故这条写能力必须收敛在一个有唯一调用方的方法上。

        Args:
            payload: ``order_builder.build_algo_stop_payload`` 组装的请求体
                （含确定性 ``algoClOrdId``——``_post`` 会在 ``TransportError`` 上重试同一 body）。

        Returns:
            ``data[0]``（含 ``algoId`` / ``algoClOrdId``）。

        Raises:
            OKXPrivateError: 连通失败、或业务码非 ``"0"``（带**原始** sCode）。
        """
        body = await self._post("/api/v5/trade/order-algo", payload)
        rows = body.get("data") or []
        row = rows[0] if rows else {}
        top_code = str(body.get("code"))
        s_code = str(row.get("sCode") or "")
        if top_code != "0" or (s_code and s_code != "0"):
            code = s_code if s_code and s_code != "0" else top_code
            msg = row.get("sMsg") or body.get("msg") or "OKX 算法委托被拒"
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
        try:
            body = await self._post("/api/v5/trade/close-position", payload)
        except OKXPrivateError as exc:
            if exc.code != "OKX_UNREACHABLE":     # 业务拒单是确定的失败，不需要核验
                raise
            return await self._verify_closed(inst_id, pos_side)
        rows = body.get("data") or []
        row = rows[0] if rows else {}
        top_code = str(body.get("code"))
        s_code = str(row.get("sCode") or "")
        if top_code != "0" or (s_code and s_code != "0"):
            code = s_code if s_code and s_code != "0" else top_code
            msg = row.get("sMsg") or body.get("msg") or "OKX 平仓失败"
            raise OKXPrivateError(code, str(msg))
        return row

    async def _verify_closed(self, inst_id: str, pos_side: str) -> dict:
        """平仓请求送出后连通失败：**读回交易所状态**判定，而不是再发一次平仓（R-1 / R-17）。

        为什么必须**有界重读**而不是只读一次：平仓送达后交易所侧持仓归零有毫秒级到秒级的延迟，
        立刻读 ``positions`` 很可能仍看到仓位——只读一次会把「其实成功了」稳定地误报成「不确定」，
        而「不确定」在自愈层里是最贵的一种回答。至多 ``okx_link_verify_attempts`` 次、间隔
        ``okx_link_verify_backoff_s``（线性不退避），总耗时 <2s。

        **调用方契约（安全不变式）**：``OKX_SEND_UNCERTAIN`` 是**终态**。任何上层——``live_auto``
        执行器、``okx.trading``、自愈层、界面上的人工平仓按钮——**一律禁止**据它自动重发平仓，
        只能如实展示并请人工核对。上一轮 ``ai-trade-backstop-repair`` 最重的缺陷正是「待核实态被
        覆盖，于是人工连点两次发出了两张全仓止损」；一个新的「不确定」码若没有配套写死「谁都不许
        据它重试」，就是在同一个坑上重新铺一块木板。
        """
        cfg = self._settings
        attempts = max(1, int(cfg.okx_link_verify_attempts))
        for i in range(attempts):
            if i:
                await asyncio.sleep(float(cfg.okx_link_verify_backoff_s))
            try:
                positions = await self.get_positions()
            except OKXPrivateError:
                continue          # 核验自身失败：继续重读，读不到就落 UNCERTAIN（绝不假成功）
            if not _has_open_position(positions, inst_id, pos_side):
                return {"instId": inst_id, "posSide": pos_side, "verified": True}
        raise OKXPrivateError("OKX_SEND_UNCERTAIN",
                              "平仓请求结果未确认，交易所侧仍有持仓，请人工核对后再操作")

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

    async def get_trade_fee(self, inst_type: str, inst_id: str | None = None
                            ) -> tuple[dict, str | None, tuple[FeeAttemptError, ...]]:
        """``GET /api/v5/account/trade-fee``：本账户的**真实**费率档位，走参数阶梯（轴 A-2）。

        关键回读字段：``level``（如 ``Lv1``）/ ``ruleType``/ ``maker``/``taker``（币本位与现货）/
        ``makerU``/``takerU``（USDT 保证金合约）/ ``makerUSDC``/``takerUSDC``。费率为**有符号字符串**，
        负数 = 平台收取、正数 = 返佣（归一与夹紧在服务层 ``_normalize_trade_fee``）。

        阶梯（`ai_trade_fee_rate_family_fallback_enabled=False` ⇒ 只发今天那一跳，逐字节等价）::

            ① {"instType": …, "instFamily": inst_family_of(instId)}   family 为 None ⇒ 直接跳 ②
               should_advance(exc) 为真  ⇒ 记下这一跳的 (status, code, msg)，跳 ②
               否则（传输层 / 429 / 401 / 5xx）⇒ **原样上抛，绝不跳 ②**
            ② {"instType": …}                                          账户在该产品类型上的通用档
               should_advance(exc) 为真  ⇒ 记下，返回 ({}, None, errors)
               否则                      ⇒ 原样上抛

        **本方法是唯一知道「哪一跳成功了」的地方**，故 ``rung`` 由它产出并一路带到
        ``trading._normalize_trade_fee``——``source`` 的唯一产出点在那里，只改 ``fee_rates.py``
        （它是**读**这个值的）永远不会让 ``exchange:family`` 这个值出现（评审 P1-6）。

        次数代价（评审 P1-11 更正了 v1 的量纲误用）：阶梯把启动期私有 GET 的**次数**从 N 提到
        最坏 2N；在按账户 5 次/秒的令牌桶下，N=5 的最坏增量是 1 秒量级的排队。而**真实断网时
        次数一次都不会翻倍**——``should_advance`` 为假，第 ① 跳直接上抛。

        Args:
            inst_type: 产品类型（本项目恒 ``"SWAP"``）。
            inst_id: 合约 ID（用于推 ``instFamily``；``SPOT``/``MARGIN`` 仍按 ``instId`` 发）。

        Returns:
            ``(row, rung, errors)``——``row`` 是 ``data[0]``（全败即 ``{}``），
            ``errors`` 是每一跳的失败事实（成功即空元组），``rung`` 是**哪一跳成功了**：

            * ``"family"`` / ``"type"``：阶梯的第 ① / ② 跳，下游据此产出
              ``source="exchange:family"`` / ``"exchange:type"``；
            * ``""``：**今天那一跳**（总闸关，或 ``SPOT``/``MARGIN`` 的 ``instId`` 契约）。
              空串让下游 ``_normalize_trade_fee`` 逐字保留老值 ``"exchange"``——回退档必须
              **连 wire 上的字符串都回到** ``8451163``，否则「逐字节回退」是一句自称；
            * ``None``：一跳都没成功（与 ``""`` 是两件事，不许压成同一个假值）。

        Raises:
            OKXPrivateError: 任何**不该推进阶梯**的失败（传输层 / 429 / 401 / 403 / 5xx）原样上抛。
        """
        errors: list[FeeAttemptError] = []
        for rung, params in self._fee_rungs(inst_type, inst_id):
            try:
                rows = await self._get("/api/v5/account/trade-fee", params)
            except OKXPrivateError as exc:
                if not should_advance(exc):
                    raise
                errors.append(FeeAttemptError(
                    rung=rung, http_status=getattr(exc, "http_status", None),
                    code=str(exc.code or ""), msg=str(exc.message or "")))
                continue
            return (rows[0] if rows else {}), rung, tuple(errors)
        return {}, None, tuple(errors)

    def _fee_rungs(self, inst_type: str, inst_id: str | None
                   ) -> list[tuple[str, dict[str, str]]]:
        """阶梯的参数表（纯函数，无 IO）。总闸关 ⇒ **只有今天那一跳**。

        ``SPOT`` / ``MARGIN`` 走 ``instId``（OKX 对这两类接受它），其余产品类型才推 ``instFamily``。
        ``instId`` 形参因此保留——本仓今天不走现货，但契约不该被删掉。

        今天那一跳的 rung 名是**空串**而不是 ``"type"``：它发的参数里带 ``instId``，与阶梯第 ②
        跳（账户在该产品类型上的通用档）**不是同一件事**。叫成 ``"type"`` 会有两个后果，每个
        都是本轮在修的那类假话——回退档下 ``source`` 变成 ``"exchange:type"``（不再逐字节等于
        ``8451163`` 的 ``"exchange"``），而界面会把一份按 ``instId`` 取到的档位说成「账户通用档位」。
        """
        upper = str(inst_type or "").upper()
        legacy = {"instType": upper}
        if inst_id:
            legacy["instId"] = inst_id
        if not self._settings.ai_trade_fee_rate_family_fallback_enabled:
            return [("", legacy)]
        if upper in ("SPOT", "MARGIN"):
            return [("", legacy)]
        family = inst_family_of(inst_id or "")
        rungs: list[tuple[str, dict[str, str]]] = []
        if family:
            rungs.append(("family", {"instType": upper, "instFamily": family}))
        rungs.append(("type", {"instType": upper}))
        return rungs

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
        if after_ms is not None:
            params["before"] = str(max(0, int(after_ms)))
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


def _has_open_position(positions: list, inst_id: str, pos_side: str) -> bool:
    """该合约该方向是否仍有非零持仓（纯函数，供 ``_verify_closed`` 判定）。

    ``posSide`` 在净持仓模式下是 ``"net"``；OKX 回执里 ``pos`` 为字符串且可能是 ``""`` / ``"0"``。
    判据刻意宽松地**只认「明确读到非零」**：读不出来就不算「已平掉」，宁可落 UNCERTAIN
    也绝不假成功——一个被误判成功的平仓会让上层以为已经空仓，而实际持仓还在裸奔。
    """
    for row in positions or []:
        if str(row.get("instId") or "") != inst_id:
            continue
        side = str(row.get("posSide") or "net")
        if pos_side not in ("net", side) and side != "net":
            continue
        try:
            if abs(float(row.get("pos") or 0.0)) > 0:
                return True
        except (TypeError, ValueError):
            return True      # 读不懂的持仓字段一律按「还有仓」处理（诚实的保守）
    return False
