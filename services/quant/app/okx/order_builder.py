"""下单体组装与本地校验纯函数（实盘交易模式 spec §2.2/§2.3/§2.4）。

无 IO / 无网络：只把「方向 + 标的元数据 + 持仓模式」映射为 OKX V5 ``/trade/order`` 体，
并按 ``lotSz/tickSz/minSz`` 取整、按名义上限本地拦截越界单（守真实资金安全护栏）。

硬约束：
- 仅 USDT 本位 SWAP/FUTURES（``settleCcy=="USDT"`` 且有 ``ctVal``）才可下单（C1-4），其余由
  ``ensure_instrument_supported`` 抛 ``LIVE_INSTRUMENT_UNSUPPORTED`` 本地拦截。
- 越界单（名义超上限 / 低于 minSz / 限价无价）抛 ``LIVE_ORDER_REJECTED_LOCAL``，**不**发往 OKX。
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from .private_client import OKXPrivateError

# 支持下单的标的类型（本期仅 USDT 本位永续 / 交割合约，C1-4）。
_SUPPORTED_CT_TYPES = {"linear"}
_SUPPORTED_SETTLE = "USDT"


@dataclass(frozen=True)
class InstrumentMeta:
    """合约元数据（来源 ``GET /api/v5/public/instruments``，C1-1）。下单数学全依赖之。"""

    ct_val: float        # 合约面值（每张对应的币量）
    ct_val_ccy: str      # 面值计价币（如 BTC）
    lot_sz: float        # 下单数量精度（张步进）
    min_sz: float        # 最小下单张数
    tick_sz: float       # 价格精度
    max_lever: float     # 最大杠杆
    ct_type: str         # linear（USDT 本位）| inverse（币本位）
    settle_ccy: str      # 结算币种


@dataclass(frozen=True)
class OrderInput:
    """归一化下单意图（API 层校验后构造；clOrdId 由服务层生成后单独传入）。"""

    inst_id: str
    direction: str            # long=买多 | short=买空
    ord_type: str             # limit | market
    td_mode: str              # cross | isolated
    px: float | None          # 限价价格（市价为 None）
    sz: float                 # 张数
    lever: float | None
    tp_trigger_px: float | None
    sl_trigger_px: float | None


def is_instrument_supported(meta: InstrumentMeta) -> bool:
    """是否为可下单标的：USDT 本位线性合约且面值有效（C1-4）。"""
    return (meta.settle_ccy == _SUPPORTED_SETTLE
            and meta.ct_type in _SUPPORTED_CT_TYPES
            and meta.ct_val > 0)


def ensure_instrument_supported(meta: InstrumentMeta) -> None:
    """不支持的标的类型在边界一次性拦截（C1-4），抛 ``LIVE_INSTRUMENT_UNSUPPORTED``。"""
    if not is_instrument_supported(meta):
        raise OKXPrivateError(
            "LIVE_INSTRUMENT_UNSUPPORTED",
            "仅支持 USDT 本位永续 / 交割合约下单（当前标的结算币种或类型不适用）")


def _decimals(step: float) -> int:
    """由步进值推断小数位数（OKX px/sz 以字符串下发，须按精度格式化）。"""
    if step <= 0:
        return 0
    text = f"{step:.12f}".rstrip("0")
    return len(text.split(".")[1]) if "." in text else 0


def snap_size(sz: float, lot_sz: float) -> float:
    """张数向下对齐到 ``lotSz`` 整数倍（宁可少下，杜绝超额）。"""
    if lot_sz <= 0:
        return sz
    return math.floor(sz / lot_sz + 1e-9) * lot_sz


def snap_price(px: float, tick_sz: float) -> float:
    """价格对齐到 ``tickSz`` 最近整数倍。"""
    if tick_sz <= 0:
        return px
    return round(px / tick_sz) * tick_sz


def fmt_step(value: float, step: float) -> str:
    """按步进精度把数值格式化为 OKX 接受的字符串。"""
    return f"{value:.{_decimals(step)}f}"


def compute_notional(sz: float, ct_val: float, px: float) -> float:
    """名义价值（USDT）= 张数 × 合约面值 × 价格。"""
    return sz * ct_val * px


def compute_margin(notional: float, lever: float) -> float:
    """所需保证金（USDT）= 名义价值 / 杠杆（lever≤0 视为 1，避免除零）。"""
    return notional / lever if lever and lever > 0 else notional


def _map_direction(direction: str, pos_mode: str) -> tuple[str, str | None]:
    """方向 → ``(side, posSide)``（C0-1 / §2.3）。

    long_short_mode：买多=buy/long、买空=sell/short（各自独立开仓）。
    net_mode：买多=buy、买空=sell，**不**传 posSide（同标的不可多空并存）。
    """
    if pos_mode == "net_mode":
        return ("buy" if direction == "long" else "sell"), None
    return ("buy", "long") if direction == "long" else ("sell", "short")


def build_attach_algo_orders(tp_trigger_px: float | None, sl_trigger_px: float | None,
                             tick_sz: float) -> list[dict]:
    """随单 OCO 止盈止损（§2.4）：仅在填了止盈或止损时附 ``attachAlgoOrds``，触发后市价（-1）。"""
    algo: dict[str, str] = {}
    if tp_trigger_px is not None:
        algo["tpTriggerPx"] = fmt_step(snap_price(tp_trigger_px, tick_sz), tick_sz)
        algo["tpOrdPx"] = "-1"
        algo["tpTriggerPxType"] = "last"
    if sl_trigger_px is not None:
        algo["slTriggerPx"] = fmt_step(snap_price(sl_trigger_px, tick_sz), tick_sz)
        algo["slOrdPx"] = "-1"
        algo["slTriggerPxType"] = "last"
    return [algo] if algo else []


def build_order_payload(order: OrderInput, meta: InstrumentMeta, pos_mode: str,
                        cl_ord_id: str) -> dict:
    """把下单意图组装为 OKX ``/api/v5/trade/order`` 请求体（取整后字符串化）。"""
    side, pos_side = _map_direction(order.direction, pos_mode)
    sz = snap_size(order.sz, meta.lot_sz)
    payload: dict = {
        "instId": order.inst_id,
        "tdMode": order.td_mode,
        "side": side,
        "ordType": order.ord_type,
        "sz": fmt_step(sz, meta.lot_sz),
        "clOrdId": cl_ord_id,
    }
    if pos_side is not None:
        payload["posSide"] = pos_side
    if order.ord_type == "limit" and order.px is not None:
        payload["px"] = fmt_step(snap_price(order.px, meta.tick_sz), meta.tick_sz)
    attach = build_attach_algo_orders(order.tp_trigger_px, order.sl_trigger_px, meta.tick_sz)
    if attach:
        payload["attachAlgoOrds"] = attach
    return payload


def validate_order(order: OrderInput, meta: InstrumentMeta, *, max_notional_usd: float,
                   ref_px: float) -> dict:
    """本地越界拦截（§2.2d）：限价须有正价、sz≥minSz、名义≤上限。

    ``ref_px``：名义/上限校验所用参考价——限价单取限价，市价单取保守价（§2.4，调用方负责取保守）。
    命中任一边界抛 ``OKXPrivateError("LIVE_ORDER_REJECTED_LOCAL", …)``，绝不发往 OKX。返回计算明细。
    """
    if order.ord_type == "limit" and (order.px is None or order.px <= 0):
        raise OKXPrivateError("LIVE_ORDER_REJECTED_LOCAL", "限价单必须填写大于 0 的价格")
    if ref_px <= 0:
        raise OKXPrivateError("LIVE_ORDER_REJECTED_LOCAL", "缺少有效参考价，无法校验名义价值")
    sz = snap_size(order.sz, meta.lot_sz)
    if sz < meta.min_sz or sz <= 0:
        raise OKXPrivateError(
            "LIVE_ORDER_REJECTED_LOCAL",
            f"下单数量需 ≥ 最小下单量 {fmt_step(meta.min_sz, meta.lot_sz)} 张")
    notional = compute_notional(sz, meta.ct_val, ref_px)
    if notional > max_notional_usd:
        raise OKXPrivateError(
            "LIVE_ORDER_REJECTED_LOCAL",
            f"下单名义价值 {notional:.2f} USDT 超出本地上限 {max_notional_usd:.2f} USDT")
    return {"sz": sz, "notional": notional,
            "margin": compute_margin(notional, order.lever or 1.0)}


@dataclass(frozen=True)
class ClosePositionInput:
    """归一化平仓意图（API 层校验后构造；clOrdId 由服务层生成后单独传入，平仓 spec §2.2/§5）。

    限价平仓走 ``/trade/order``（对冲 posSide / 净持仓 reduceOnly 语义）；市价整仓平走
    ``/trade/close-position``（不接受 sz，OKX 自行整仓平）。
    """

    inst_id: str
    pos_side: str        # long | short | net（持仓行带出；net 模式靠 pos 符号判向）
    pos: float           # 带符号张数（net 判向用；sz 上限仍以服务层实时 |pos| 为可信源）
    mgn_mode: str        # cross | isolated
    ord_type: str        # limit（挂单平仓）| market（整仓市价平）
    px: float | None     # 限价价格（市价为 None）
    sz: float | None     # 限价部分平仓张数（缺省=全仓 |pos|；市价忽略）


@dataclass(frozen=True)
class CancelOrderInput:
    """归一化撤单意图（ai-trade-limit-orders §4.4）。

    撤单本身只需 ``inst_id`` + ``cl_ord_id``，但 ``live_order_audit`` 留痕需要 direction / px / sz
    这些**撤单请求里没有天然取值**的字段——它们一律取自调用方持有的挂单快照（`RestingOrder`）。
    聚合为值对象而非堆形参，以守 CLAUDE.md 的「形参 ≤5」（镜像 ``ClosePositionInput`` 的既有范式）。
    """

    inst_id: str
    cl_ord_id: str       # 我方自定义订单 ID（挂单归属判定的主键）
    direction: str       # 审计用：被撤挂单的方向（long | short）
    td_mode: str         # cross | isolated
    px: float | None     # 审计用：被撤挂单的委托价
    sz: float | None     # 审计用：被撤挂单的张数


def _close_side(pos_side: str, pos: float) -> str:
    """平仓单 side（§2.2，一等安全约束）：平多 → ``sell``、平空 → ``buy``。

    net 模式按 ``pos`` 符号判向（净多 → sell、净空 → buy）；对冲模式按 ``posSide``。
    """
    if pos_side == "net":
        return "sell" if pos > 0 else "buy"
    return "sell" if pos_side == "long" else "buy"


def build_close_order_payload(close: ClosePositionInput, meta: InstrumentMeta, pos_mode: str,
                              sz: float, cl_ord_id: str) -> dict:
    """组装限价平仓 ``/api/v5/trade/order`` 请求体（§2.2/§5）。

    对冲模式（long_short_mode）：带 ``posSide`` 锁侧、**不**带 ``reduceOnly``（posSide 已限定平该侧仓，
    避免部分账户模式对 reduceOnly 的兼容性问题）。净持仓模式（net_mode）：带 ``reduceOnly=true``、
    **不**带 ``posSide``。``sz`` 由调用方传入（已 ``validate_close`` 取整且 ``≤|pos|``）。
    """
    payload: dict = {
        "instId": close.inst_id,
        "tdMode": close.mgn_mode,
        "side": _close_side(close.pos_side, close.pos),
        "ordType": "limit",
        "sz": fmt_step(snap_size(sz, meta.lot_sz), meta.lot_sz),
        "clOrdId": cl_ord_id,
    }
    if pos_mode == "net_mode":
        payload["reduceOnly"] = True
    else:
        payload["posSide"] = close.pos_side
    if close.px is not None:
        payload["px"] = fmt_step(snap_price(close.px, meta.tick_sz), meta.tick_sz)
    return payload


@dataclass(frozen=True)
class AlgoStopInput:
    """归一化「补挂止损」意图（ai-trade-backstop-repair-and-net-backtest §4.5）。

    聚合为值对象而非堆形参，守 CLAUDE.md 的「形参 ≤5」（镜像 ``ClosePositionInput`` 的既有范式）。
    后两个字段是**收敛上界的两个真源**，与下单意图一起旅行（[P0-4]）：缺任何一个，服务层就只能
    在下单现场再去猜一次「该补多少张」，而那正是本仓库反复出事的「同一口径两处各存一份」形态。
    """

    inst_id: str
    pos_side: str            # long | short | net（net 模式靠 pos 符号判向）
    pos: float               # 带符号张数（net 判向用）
    td_mode: str             # cross | isolated
    sz: float                # 待补差额张数（未取整；服务层按实时 |pos| 重算后才下单）
    stop_px: float           # 触发价（本地回执口径，**绝不新建第二条求价路径**）
    algo_cl_ord_id: str      # 确定性幂等键（三段式：会话身份 + 持仓周期 + 尝试序号）
    self_position_sz: float  # 本层自管仓张数——守「**绝不处置本层不拥有的仓**」
    covered_sz: float        # 已被交易所侧止损覆盖的张数（检测层原样转发，不重算）


def ensure_close_only(payload: dict) -> None:
    """补挂腿必须锁死为平仓腿：``reduceOnly`` 与 ``posSide`` **二者必居其一**（H2 第 4 条）。

    这是「本层永不建立敞口」这条不变式的代码化断言。两种持仓模式的锁侧写法不同
    （``build_close_order_payload``:238-241），漏了任何一侧，那张单在触发时就可能开出反向敞口。

    Raises:
        OKXPrivateError: 两个锁侧字段都缺（``LIVE_BACKSTOP_REJECTED_LOCAL``，绝不发往 OKX）。
    """
    if not payload.get("reduceOnly") and not payload.get("posSide"):
        raise OKXPrivateError("LIVE_BACKSTOP_REJECTED_LOCAL",
                              "补挂止损缺少锁侧字段（reduceOnly / posSide 必居其一），已本地拦截")


def build_algo_stop_payload(stop: AlgoStopInput, meta: InstrumentMeta, pos_mode: str) -> dict:
    """补挂止损的 ``/api/v5/trade/order-algo`` 请求体（backstop-repair §2.2.2）。

    ``ordType="conditional"`` + ``slTriggerPx`` + ``slOrdPx="-1"`` + ``slTriggerPxType="last"``
    —— 与随单 OCO 的 ``build_attach_algo_orders``（:115-127）**逐字一致**，不发明第二套写法。
    锁侧：``net_mode ⇒ reduceOnly=True 且不带 posSide``；对冲 ⇒ 带 ``posSide`` 且不带
    ``reduceOnly``（与 ``build_close_order_payload``:238-241 同款），组装完即以
    ``ensure_close_only`` 断言不变式。
    """
    payload: dict = {
        "instId": stop.inst_id,
        "tdMode": stop.td_mode,
        "side": _close_side(stop.pos_side, stop.pos),
        "ordType": "conditional",
        "sz": fmt_step(snap_size(stop.sz, meta.lot_sz), meta.lot_sz),
        "slTriggerPx": fmt_step(snap_price(stop.stop_px, meta.tick_sz), meta.tick_sz),
        "slOrdPx": "-1",
        "slTriggerPxType": "last",
        "algoClOrdId": stop.algo_cl_ord_id,
    }
    if pos_mode == "net_mode":
        payload["reduceOnly"] = True
    else:
        payload["posSide"] = stop.pos_side
    ensure_close_only(payload)
    return payload


def validate_algo_stop(stop: AlgoStopInput, meta: InstrumentMeta, *, abs_pos: float) -> dict:
    """H2 越界锁；命中即抛，**绝不发往 OKX**。返回 ``{"sz": 收敛并取整后的张数}``。

    [P0-4] 先按 §2.2.2 **向下收敛**到 ``min(abs_pos, self_position_sz) − covered_sz`` 再取整：
    ``abs_pos`` 守「绝不信本地读数」（本地偏大时不能按本地下），``self_position_sz`` 守「绝不处置
    本层不拥有的仓」（账户上另有 live_auto / 手工持仓时 ``abs_pos`` 是全账户口径，按它补出来的
    ``reduceOnly`` 止损一旦触发就替用户平掉了他自己建的仓）。**能收敛的越界不抛**——本地读数暂时
    偏大是正常的时序差，不是故障，抛它只会把一次读数偏差渲染成一次吓人的失败并烧掉一次频率配额。

    [P1-1] **不收 ``max_notional_usd``**：名义上限守的是「单笔**新增**敞口」，而补挂腿是减仓腿。
    仓库既有纪律已写明「平仓**不**应用名义上限与反向减仓护栏（平仓即减仓，是动作目的）；仅以实时
    ``|pos|`` 二次校验 ``sz≤|pos|``」（``okx/trading.py``:224-225）。默认上限仅 1000 USDT，而持仓由
    多次加仓合法累积而成——照单笔开仓的口径拒单，会让仓位越大、越裸露、越需要兜底时补挂越是被拒死。

    Raises:
        OKXPrivateError: ``LIVE_BACKSTOP_REJECTED_LOCAL``（触发价非法 / 收敛取整后 < ``minSz``）；
            ``LIVE_BACKSTOP_SIZE_EXCEEDS`` 是**内部不变式断言**——收敛后仍 ``> abs_pos`` 在数学上
            不可能，命中即说明上游算错了，必须炸。
    """
    px = stop.stop_px
    if px is None or not math.isfinite(float(px)) or float(px) <= 0:
        raise OKXPrivateError("LIVE_BACKSTOP_REJECTED_LOCAL",
                              "补挂止损必须有大于 0 的有效触发价")
    room = max(0.0, min(float(abs_pos), float(stop.self_position_sz)) - float(stop.covered_sz))
    sz = snap_size(min(float(stop.sz), room), meta.lot_sz)
    if sz <= 0 or sz < meta.min_sz:
        raise OKXPrivateError(
            "LIVE_BACKSTOP_REJECTED_LOCAL",
            f"补挂张数需 ≥ 最小下单量 {fmt_step(meta.min_sz, meta.lot_sz)} 张"
            f"（未覆盖差额仅 {fmt_step(max(0.0, room), meta.lot_sz)} 张）")
    if sz > float(abs_pos):
        raise OKXPrivateError(
            "LIVE_BACKSTOP_SIZE_EXCEEDS",
            f"补挂张数 {fmt_step(sz, meta.lot_sz)} 超过实时持仓 "
            f"{fmt_step(float(abs_pos), meta.lot_sz)} 张（内部不变式违反）")
    return {"sz": sz}


def validate_close(close: ClosePositionInput, meta: InstrumentMeta, abs_pos: float) -> dict:
    """限价平仓本地越界拦截（§2.4，P1-2）：限价须有正价、取整后 ``sz>0``、``sz≥minSz``、``sz≤|pos|``。

    ``abs_pos``：实时持仓的 ``|pos|``（服务层取自 OKX，作 ``sz`` 上限的可信来源——**绝不**信前端 pos）。
    缺价 / 不足 minSz / 取整后 ≤0 抛 ``LIVE_CLOSE_REJECTED_LOCAL``；超出持仓抛 ``LIVE_CLOSE_SIZE_EXCEEDS``；
    一律绝不发往 OKX。尾仓 ``abs_pos<minSz`` 时默认全仓亦会被 minSz 拦截，UI 应引导改用市价整仓（§2.6）。
    返回取整后的 ``{sz}``。
    """
    if close.px is None or close.px <= 0:
        raise OKXPrivateError("LIVE_CLOSE_REJECTED_LOCAL", "限价平仓必须填写大于 0 的委托价格")
    requested = close.sz if close.sz is not None else abs_pos
    sz = snap_size(requested, meta.lot_sz)
    if sz <= 0 or sz < meta.min_sz:
        raise OKXPrivateError(
            "LIVE_CLOSE_REJECTED_LOCAL",
            f"平仓数量需 ≥ 最小下单量 {fmt_step(meta.min_sz, meta.lot_sz)} 张")
    if sz > abs_pos:
        raise OKXPrivateError(
            "LIVE_CLOSE_SIZE_EXCEEDS",
            f"平仓数量 {fmt_step(sz, meta.lot_sz)} 张超过当前持仓 {fmt_step(abs_pos, meta.lot_sz)} 张")
    return {"sz": sz}
