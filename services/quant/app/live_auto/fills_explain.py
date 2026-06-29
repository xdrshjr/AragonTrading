"""成交 ↔ 触发解释关联纯函数（live-fills-explainability §2.3）。

把已落在 ``trades.explain_json`` 里的自动交易触发解释，按连接键关联到真实 OKX 成交行上：
``clOrdId`` 为主键、``ordId`` 为副键。本模块**无凭证、无 IO、不依赖 schemas**（坏 JSON 安全解析
在此内联，避免 ``live_auto → schemas`` 反向耦合，RV4），便于单测与复用。
"""
from __future__ import annotations

import json
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
    """按 clOrdId / ordId 把「带解释的 trade」建成 {key: explain} 索引。

    同键保留最新（输入须按 ts 倒序，遇已存在键则跳过）；空键 / 坏 JSON / 无 meta 安全跳过。
    """
    index: dict[str, dict] = {}
    for trade in trades:
        explain = _safe_parse_explain(getattr(trade, "explain_json", None))
        if explain is None:
            continue
        meta = explain.get("meta") or {}
        for key in (meta.get("clOrdId"), meta.get("ordId")):
            if key and key not in index:
                index[key] = explain
    return index


def attach_explains(fills: list[dict], index: dict[str, dict]) -> list[dict]:
    """逐 fill：clOrdId 主、ordId 副命中则挂 explain，未中置 None；返回新列表（不就地改）。"""
    out: list[dict] = []
    for fill in fills:
        explain = index.get(fill.get("clOrdId") or "") or index.get(fill.get("ordId") or "")
        out.append({**fill, "explain": explain})
    return out
