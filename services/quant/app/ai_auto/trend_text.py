"""《本轮趋势基调》段的 prompt 渲染（ai-trade-execution-trend-verdict 轴 B）。

沿用 `market_text.py` / `cost_text.py` / `wake_text.py` 的「文案模块 + 延迟导入」先例：
`prompts.py` 已 1116 行且在 `.claude-index/config.md` 里挂着一条「下一个触碰它的特性必须先还
外移债」的硬要求，本轮不得再往里塞十几行文案（本轮的第五次登记已如实说明）。

两条渲染纪律：

1. **`verdict is None` ⇒ 空表**（整段省略）⇒ `build_decision_prompt` 的产出与本特性合入前
   **逐字节相同**。这是本仓库贯穿全仓的降级范式（`baseRules` / `sizingPlan` / `tradingHorizon` /
   `intel` 全同款）。
2. **不写死任何算例**。逆势闸的两个阈值随 verdict 的 `counterGate` 子块下发（真源是会话的
   `TrendVerdictRule`），渲染层只读不算——写死 `0.65 / ×0.5` 会让一个改过 env 的会话读到假话。
"""
from __future__ import annotations

_DIRECTION_TEXT = {"up": "偏多", "down": "偏空", "range": "中性", "unknown": "不可用"}
_STRENGTH_TEXT = {"strong": "强", "moderate": "中", "weak": "弱"}
_TACTICAL_TEXT = {
    "aligned": "与执行周期体制一致（aligned）",
    "diverged": "与执行周期体制背离（diverged）",
    "insufficient": "证据不足（insufficient）",
}


def _num(value, digits: int = 2) -> str:
    """带符号的定点数；`None` ⇒ `"—"`（**绝不**把「没有这个数」画成 0）。"""
    if value is None:
        return "—"
    try:
        return f"{float(value):+.{digits}f}"
    except (TypeError, ValueError):
        return "—"


def _weight(value) -> str:
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return "—"


def _degraded_lines(verdict: dict) -> list[str]:
    """降级态（两档同形，只两行）：如实说「算不出来」，并**堵死**用高周期冒充本级别基调的退路。"""
    missing = "、".join(_missing_labels(verdict)) or "未知"
    return [
        f"【本轮趋势基调 · 判定级别 {verdict.get('bar') or '—'}】基调不可用"
        f"（成分缺失：{missing}）。",
        "本轮不给方向基调，请自行从执行周期证据推导，**不得**用高周期背景冒充本级别基调。",
    ]


def _missing_labels(verdict: dict) -> list[str]:
    """缺失成分的中文标签（取自 `components` 自带的 label，绝不在渲染层另抄一份表）。"""
    missing = set(verdict.get("missing") or [])
    return [str(row.get("label") or row.get("key"))
            for row in (verdict.get("components") or []) if row.get("key") in missing]


def _evidence_line(verdict: dict) -> str:
    """逐成分明细（按权重），缺失项如实写「不可用」——它是「不得补猜」在文案上的兑现。"""
    cells = []
    for row in verdict.get("components") or []:
        label = row.get("label") or row.get("key")
        weight = _weight(row.get("weight"))
        if row.get("score") is None:
            cells.append(f"{label} 不可用({weight})")
        elif row.get("key") == "htf":
            cells.append(f"{label} {_num(row.get('score'))}({weight}，仅调制、不参与强度)")
        else:
            cells.append(f"{label} {_num(row.get('score'))}({weight})")
    return "判据（按权重）：" + " · ".join(cells)


def _htf_line(verdict: dict) -> str:
    """高周期背景一行：状态 + 是否与本级别基调冲突 + 「可削弱、不可反转」的硬边界。"""
    htf = verdict.get("higherTimeframe") or {}
    status = htf.get("status") or "不可用"
    bars = list(htf.get("bullishBars") or []) + list(htf.get("bearishBars") or [])
    tail = f"（{'/'.join(bars)}）" if bars else ""
    bar = verdict.get("bar") or "执行周期"
    conflicted = verdict.get("conflict") in ("htf_conflict", "both")
    verdict_word = f"——与 {bar} 基调**冲突**" if conflicted else (
        "——与本级别基调一致" if htf.get("agree") else "——对本级别基调不表态")
    return (f"高周期背景：整体={status}{tail}{verdict_word}。高周期可以把本级别基调的方向拉向"
            f"中性，但**不能反转它**；它也**不参与**本级别基调的强度判定。")


def _counter_gate_line(verdict: dict) -> str:
    """逆势开仓的代价一行（阈值随 wire 下发，绝不写死）。闸未装配时如实说它没装配。

    行首缩进是刻意的：本行与其后那句「你有权推翻…」都是上一行「说明：」的**续写**，三行合成
    一段。各自另起一个「说明：」会让同一段里出现两个并列的段首标记，模型读到的段落结构与
    本仓库其余证据段不同。
    """
    gate = verdict.get("counterGate") or {}
    if not gate.get("armed"):
        return ("      顺势开仓与逆势开仓在本系统里共用同一套仓位、成本、止损与门禁标准；"
                "本会话未装配逆势降档闸，逆势开仓不额外收紧。")
    floor = gate.get("minConfidence")
    mult = gate.get("sizeMult")
    floor_text = "—" if floor is None else f"{float(floor):.2f}"
    mult_text = "—" if mult is None else f"{float(mult):g}"
    return ("      顺势开仓与逆势开仓在本系统里共用同一套仓位、成本、止损与门禁标准；"
            f"逆势开仓需你自报更高的置信度并会被强制降档（当前：置信度 ≥{floor_text}，"
            f"仓位 ×{mult_text}）。")


def _brief_lines(verdict: dict) -> list[str]:
    """`contextDisclosure="repeat"` 的压缩形态（评审 RP1-11）：只两行，**不含**逐成分明细。"""
    bar = verdict.get("bar") or "执行周期"
    direction = _DIRECTION_TEXT.get(str(verdict.get("direction")), "不可用")
    strength = _STRENGTH_TEXT.get(str(verdict.get("strength")), "—")
    htf = (verdict.get("higherTimeframe") or {}).get("status")
    conflicted = verdict.get("conflict") in ("htf_conflict", "both")
    htf_text = "" if not htf else (
        f"，高周期 {htf} 与之冲突" if conflicted else f"，高周期 {htf}")
    return [
        f"【本轮趋势基调 · {bar}】{direction}（强度={strength}，"
        f"执行周期分 {_num(verdict.get('execScore'))} / 合成分 {_num(verdict.get('score'))}）"
        f"{htf_text}。",
        "逐成分明细与上一轮相同，本轮最新变化见下方执行周期证据段。",
    ]


def format_trend_verdict_for_prompt(verdict: dict | None, tier: str = "full") -> list[str]:
    """《本轮趋势基调》段；`verdict` 为 `None` ⇒ 空表（整段省略，逐字节回退）。

    Args:
        verdict: `trend_verdict.build_trend_verdict` 的产物或 `None`。
        tier: `"full"`（全量）或 `"brief"`（压缩轮两行形态）。调用方按
            `payload.get("contextDisclosure") == "repeat"` 取档，与 `_multi_timeframe_lines` /
            `_digest_lines` 的既有姿态逐字同构——RK-4 承诺的「压缩轮同样压缩基调段」必须有这个
            形参才落得下来，否则每一个重复轮的 prompt 顶部都会多出 8 行。

    Returns:
        prompt 行列表（每行是 `build_decision_prompt` 的 `parts` 里的一个元素）。
    """
    if not verdict:
        return []
    if verdict.get("degraded"):
        return _degraded_lines(verdict)
    if tier == "brief":
        return _brief_lines(verdict)
    bar = verdict.get("bar") or "执行周期"
    direction = _DIRECTION_TEXT.get(str(verdict.get("direction")), "不可用")
    strength = _STRENGTH_TEXT.get(str(verdict.get("strength")), "—")
    lines = [
        f"【本轮趋势基调 · 判定级别 {bar}（= 你正在交易的级别）】",
        f"基调：{direction}（direction={verdict.get('direction')}，强度={strength}）。"
        f"执行周期分 {_num(verdict.get('execScore'))}（决定强度，只由 {bar} 证据算出）；"
        f"含高周期调制后的合成分 {_num(verdict.get('score'))}（决定方向）。",
        _evidence_line(verdict),
        _htf_line(verdict),
    ]
    tactical = verdict.get("tacticalAlignment")
    if tactical:
        lines.append(f"战术周期：{_TACTICAL_TEXT.get(str(tactical), str(tactical))}。")
    missing = _missing_labels(verdict)
    if missing:
        lines.append(f"成分缺失：{'、'.join(missing)}（不得补猜）。")
    lines.append("说明：这是系统按确定性规则在**你所选级别**上给出的总基调，"
                 "是本轮研判的起点而不是命令。")
    lines.append(_counter_gate_line(verdict))
    lines.append("      你有权推翻这个基调，但必须在 S12 说明理由。")
    return lines
