"""中线背景（多周期）prompt 文案（ai-trade-wake-aware-context §2.8 逃生口）。

`prompts.py` 已逼近本仓库 800 行红线，故把既有的三个多周期渲染函数整体搬到本模块，`prompts.py`
经延迟导入消费（镜像 `profit_guard_text.py` / `sizing_text.py` / `wake_text.py` 的「文案模块 + 延迟
导入」先例）。**函数体逐字未改**——这是纯搬家，渲染输出逐字节不变（G-1 冻结门禁作证）。

`_format_candidate` 同时被 `wake_text.format_multi_timeframe_repeat` 消费：重复轮压缩形态与全量
形态的表头必须是同一个渲染真源，否则「压缩版」和「全量版」会各写各的候选支撑阻力。
"""
from __future__ import annotations


def _format_candidate(label: str, candidate: dict | None) -> str:
    if not candidate:
        return f"{label}=无"
    bars = "/".join(candidate.get("bars") or []) or "未知"
    return (f"{label}={candidate.get('price')}（距离 {candidate.get('distancePct')}%，"
            f"来源 {bars}）")


def _format_multi_timeframe_frame(frame: dict) -> str:
    regime = (frame.get("regime") or {}).get("primary")
    digest = frame.get("digest") or {}
    rsi = ((digest.get("factors") or {}).get("rsi14") or {})
    range_pos = (digest.get("priceAction") or {}).get("rangePos")
    stale = "，数据陈旧" if frame.get("stale") else ""
    return (f"- {frame.get('bar')}：体制={regime}，RSI14={rsi.get('value')}"
            f"({rsi.get('label')})，区间位置={range_pos}，样本={frame.get('sampleCount')}"
            f"{stale}")


def _format_multi_timeframe(context: dict | None) -> list[str]:
    if context is None:
        return []
    frames = context.get("frames") or []
    if not frames:
        return ["中线背景暂不可用，已回退基础周期；缺失或陈旧周期不得补猜。"]
    alignment = (context.get("trendAlignment") or {}).get("status")
    header = "；".join((
        f"中线背景：整体={alignment}",
        _format_candidate("候选支撑", context.get("nearestSupport")),
        _format_candidate("候选阻力", context.get("nearestResistance")),
    ))
    order = {bar: index for index, bar in enumerate(("1D", "12H", "4H", "1H"))}
    lines = [header]
    lines.extend(_format_multi_timeframe_frame(frame)
                 for frame in sorted(frames, key=lambda item: order.get(item.get("bar"), 99)))
    missing = context.get("missingBars") or []
    if missing:
        lines.append(f"缺失周期={'/'.join(missing)}；不得补猜。")
    return lines
