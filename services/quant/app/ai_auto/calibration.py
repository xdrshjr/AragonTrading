"""校准三问纯核心（ai-trade-realized-edge-calibration 轴 C）。

从 `profitability.py` 下沉出来的独立模块（§四 [P2-1] 预授权）：那边装 `pooled` + `risk_profile` +
六切片 + 冻结结论表已经贴着 300 行预算，本轮的纪律是**宁可拆文件也不许删注释压行**（先例：
`prior_service.py`）。

三问共用一个形状：`{n, coverage, ...读数, conclusive}`。`coverage` = 有该读数的往返数 / 总往返数，
**`coverage < 1` 时前端必须把覆盖率显示出来**（G-8）——一个在 8% 子样本上算出来的读数和一个在
100% 样本上算出来的读数并排放着而不说明，本身就是误导。

问一（门禁分子准不准）有一条必须写进 UI 的注解：第 2 轮的分子是
`min(atr_target_mult × ATR, OCO 止盈距离)`，是「若走对能吃到多少」的**幅度**而非期望，故理论上
应当系统性高于已实现毛额中位（后者含走错的那一半）。`signAgreementRate` 因此实际度量的是胜率，
**不得被读成「门禁预测准确率」**。

问二（成本模型准不准）的 `n` / `coverage` 一律按**带费子样本**计（`feeKnown`）：实测 193 笔里
172 笔的腿费恒 0，那是「没记」不是「免费」（D-9）。
"""
from __future__ import annotations

from .profitability import CONFIDENCE_BUCKETS, _finite, _median, _round


def calibration(trips: list[dict], *, bucket_min_trips: int = 10,
                model_constant_bps: float | None = None) -> dict:
    """三问的完整产物；空样本 ⇒ 三块各自 `n=0 / coverage=0 / conclusive=false`（不抛、不藏）。"""
    return {"edgeNumerator": edge_numerator(trips, bucket_min_trips=bucket_min_trips),
            "costModel": cost_model(trips, bucket_min_trips=bucket_min_trips,
                                    model_constant_bps=model_constant_bps),
            "confidenceReliability": confidence_reliability(
                trips, bucket_min_trips=bucket_min_trips)}


def edge_numerator(trips: list[dict], *, bucket_min_trips: int) -> dict:
    """问一：门禁的毛 edge 粗估与已实现毛额差多少。

    `coverage` 的分子是**带开仓时刻估计**的往返数——轴 A 之前的历史一笔都没有，故上线当天恒 0。
    这**不是缺陷、是事实**，卡片必须显式写「需要新会话积累」。
    """
    total = len(trips)
    rows = [t for t in trips if _finite(t.get("estEdgeBps")) is not None]
    n = len(rows)
    signs = [(1 if (_finite(t.get("realizedGrossBps")) or 0.0) > 0 else 0)
             for t in rows if (_finite(t.get("estEdgeBps")) or 0.0) > 0]
    return {
        "n": n, "coverage": _round(n / total, 4) if total else 0.0,
        "medianEstEdgeBps": _median([_finite(t.get("estEdgeBps")) for t in rows]),
        "medianRealizedGrossBps": _median([_finite(t.get("realizedGrossBps")) for t in trips]),
        "medianErrorBps": _median([_finite(t.get("edgeErrorBps")) for t in rows]),
        "signAgreementRate": _round(sum(signs) / len(signs), 4) if signs else None,
        "conclusive": n >= int(bucket_min_trips),
    }


def cost_model(trips: list[dict], *, bucket_min_trips: int,
               model_constant_bps: float | None) -> dict:
    """问二：成本模型的往返常数与实测腿费差多少（分母是 `feeTrips`，不是 `roundTrips`）。"""
    total = len(trips)
    rows = [t for t in trips if t.get("feeKnown")]
    n = len(rows)
    coverage = (n / total) if total else 0.0
    return {
        "n": n, "coverage": _round(coverage, 4),
        "medianEstCostBps": _median([_finite(t.get("estCostBps")) for t in rows]),
        "medianRealizedCostBps": _median([_finite(t.get("realizedCostBps")) for t in rows]),
        "medianErrorBps": _median([_finite(t.get("costErrorBps")) for t in rows]),
        "modelConstantBps": _round(model_constant_bps, 4),
        # 两道门同时成立才叫「可下结论」：样本量够 **且** 覆盖率不是一个零头。
        "conclusive": n >= int(bucket_min_trips) and coverage >= 0.05,
        "note": _cost_note(n, total),
    }


def confidence_reliability(trips: list[dict], *, bucket_min_trips: int) -> dict:
    """问三：模型自报的置信度到底有没有区分度（D-4 的唯一出路）。

    `min_confidence=0.50` 该不该存在，只能由「0.35~0.5 桶与 0.5~0.7 桶的胜率差」回答。
    本轮**只出读数，不改门槛**——拿一个还没有数据的结论去改门槛，就是把猜换成另一个猜。
    """
    total = len(trips)
    rows = [t for t in trips if _finite(t.get("decisionConfidence")) is not None]
    buckets = [_bucket_row(rows, low, high, bucket_min_trips) for low, high in CONFIDENCE_BUCKETS]
    return {"n": len(rows), "coverage": _round(len(rows) / total, 4) if total else 0.0,
            "buckets": buckets, "monotonic": _monotonic(buckets),
            "conclusive": len(rows) >= int(bucket_min_trips)}


def _bucket_row(rows: list[dict], low: float, high: float, bucket_min_trips: int) -> dict:
    """一个置信度桶；桶内样本不足 ⇒ 比例类读数一律 None（**绝不用 2 笔冒充统计结论**）。"""
    inside = [t for t in rows if _in_bucket(_finite(t.get("decisionConfidence")), low, high)]
    n = len(inside)
    enough = n >= int(bucket_min_trips)
    nets = [_finite(t.get("netPnlUsd")) or 0.0 for t in inside]
    wins = sum(1 for v in nets if v > 0)
    return {"key": f"{low:g}-{high:g}", "trips": n,
            "winRate": _round(wins / n, 4) if (n and enough) else None,
            "expectancyUsd": _round(sum(nets) / n, 4) if (n and enough) else None,
            "medianRMultiple": (_median([_finite(t.get("rMultiple")) for t in inside])
                                if enough else None)}


def _monotonic(buckets: list[dict]) -> bool | None:
    """胜率是否随桶单调不减；**只在全部桶都可得时给结论**，否则 None（半张表推不出单调性）。"""
    rates = [b.get("winRate") for b in buckets]
    if any(v is None for v in rates):
        return None
    return all(rates[i] <= rates[i + 1] for i in range(len(rates) - 1))


def _in_bucket(value: float | None, low: float, high: float) -> bool:
    if value is None:
        return False
    return low <= value < high or (high >= 1.0 and value >= high)


def _cost_note(fee_trips: int, total: int) -> str | None:
    """带费分母的一句话；全样本带费 ⇒ None（不写「100% 都带费」这种噪音）。"""
    if total <= 0 or fee_trips >= total:
        return None
    return f"{total} 笔往返里只有 {fee_trips} 笔带费率记录，其余为「未记录」而非「零费率」。"
