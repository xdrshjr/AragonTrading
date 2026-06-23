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
