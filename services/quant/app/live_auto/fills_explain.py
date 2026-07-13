"""成交 ↔ 触发解释关联纯函数（live-fills-explainability §2.3）。

把已落在 ``trades.explain_json`` 里的自动交易触发解释，按连接键关联到真实 OKX 成交行上：
``clOrdId`` 为主键、``ordId`` 为副键。本模块**无凭证、无 IO、不依赖 schemas**（坏 JSON 安全解析
在此内联，避免 ``live_auto → schemas`` 反向耦合，RV4），便于单测与复用。
"""
from __future__ import annotations

import json
import math
from typing import TYPE_CHECKING

if TYPE_CHECKING:        # 仅类型标注用，避免运行期反依赖 db.models（RV4）
    from ..db.models import Trade


def _safe_parse_explain(raw: str | None) -> dict | None:
    """explain_json → dict（空 / 坏 JSON / 非 dict 安全回 None；不抛异常）。"""
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


def index_trade_explains(trades: list[Trade]) -> dict[str, dict]:
    """按 clOrdId / ordId 建立成交注解索引。

    同键保留最新（输入须按 ts 倒序，遇已存在键则跳过）；空键 / 坏 JSON / 无 meta 安全跳过。
    """
    index: dict[str, dict] = {}
    for trade in trades:
        explain = _safe_parse_explain(getattr(trade, "explain_json", None))
        if explain is None:
            continue
        meta = explain.get("meta") or {}
        payload = {
            "explain": explain if explain.get("kind") else None,
            "positionPct": getattr(trade, "position_pct", None),
            "orderQty": getattr(trade, "qty", None),
        }
        for key in (meta.get("clOrdId"), meta.get("ordId")):
            if key and key not in index:
                index[key] = payload
    return index


def attach_explains(fills: list[dict], index: dict[str, dict]) -> list[dict]:
    """关联解释，并按真实 fill 数量分摊整单仓位比例。"""
    out: list[dict] = []
    for fill in fills:
        payload = index.get(fill.get("clOrdId") or "") or index.get(fill.get("ordId") or "")
        out.append({
            **fill,
            "explain": payload.get("explain") if payload else None,
            "positionPct": _fill_position_pct(fill, payload),
        })
    return out


def _fill_position_pct(fill: dict, payload: dict | None) -> float | None:
    if payload is None:
        return None
    try:
        order_pct = float(payload.get("positionPct"))
        order_qty = float(payload.get("orderQty"))
        fill_sz = float(fill.get("fillSz"))
    except (TypeError, ValueError):
        return None
    values = (order_pct, order_qty, fill_sz)
    if not all(math.isfinite(value) for value in values):
        return None
    if not 0 <= order_pct <= 1 or order_qty <= 0 or fill_sz <= 0:
        return None
    allocated = order_pct * fill_sz / order_qty
    if not math.isfinite(allocated):
        return None
    return max(0.0, min(allocated, order_pct))
