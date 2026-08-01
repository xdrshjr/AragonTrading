"""唤醒原因 / 战术周期 / 重复轮压缩三类 prompt 文案（ai-trade-wake-aware-context §2.8）。

`prompts.py` 逼近本仓库 800 行红线，故渲染主体落本模块，`prompts.py` 只留薄壳与接线（镜像
`profit_guard_text.py` / `sizing_text.py` 的「文案模块 + 延迟导入」先例；同批把既有的多周期渲染
搬去 `market_text.py`，见该模块 docstring）。

三条措辞纪律（都是本仓库付过学费的）：
1. 凡断言「数据未变」一律**条件化**（「若…相同」）——F4c 守卫合入后能到达 prompt 的 interval 轮
   多是持仓 / 挂单 / 越阈轮，断言式「数据未变」在这些轮失实并反向强化观望（R-01）。
2. 每一个 ATR 倍数都必须带**周期名**（`0.91×30m ATR`），阈值必须取折算后的 `moveThresholdAtrEff`
   ——写「0.5×ATR」而实际按 0.158 判，就是新造一次「说的和做的不一致」。
3. 文案取值一律从 `wake` / `tacticalContext` 对象**直读**，绝不在渲染层重算判据（判定与文案同源）。
"""
from __future__ import annotations

_ALSO_TRUE_LABEL = {
    "candle_close": "新 K 线收盘", "bar_advanced": "补看新 K 线", "resting_order": "挂单盯守",
    "profit_tier": "浮盈跨档", "price_breakout": "价格异动", "position_watch": "持仓盯市",
}
_TRUNCATION_HINT = "…（战术周期证据已按 {max_chars} 字上限截断）"


def format_wake_for_prompt(wake: dict | None) -> str:
    """决策 prompt 首行的唤醒原因后缀；`wake` 缺席 ⇒ 空串（调用方回退既有 `_trigger_suffix`）。"""
    if not wake:
        return ""
    kind = wake.get("kind")
    body = _WAKE_BODY.get(kind)
    if body is None:
        return ""
    suffix = f"｜唤醒原因={body(wake)}"
    also = [_ALSO_TRUE_LABEL[name] for name in (wake.get("alsoTrue") or [])
            if name in _ALSO_TRUE_LABEL]
    return f"{suffix}｜同时成立={'/'.join(also)}" if also else suffix


def format_tactical_for_prompt(context: dict | None, tier: str, *, max_chars: int) -> list[str]:
    """战术周期段（`brief` 每周期一行；`full` 追加第二行）；`context` 为 None ⇒ 空表（整段省略）。

    截断**绝不静默**：超 `max_chars` 时保留抬头并追加可见截断标记，让人与模型都知道有东西被省了。
    """
    if not context:
        return []
    lines = [_tactical_header(context)]
    change = _format_change(context.get("changeSinceLastRound"))
    if change:
        lines.append(change)
    for frame in context.get("frames") or []:
        lines.append(_frame_line(frame))
        if tier == "full":
            lines.append(_frame_detail_line(frame))
    lines.extend(_tactical_footnotes(context))
    return _truncate(lines, max_chars)


def format_multi_timeframe_repeat(context: dict | None) -> list[str] | None:
    """重复轮的压缩中线背景；返回 None ⇒ 调用方改用既有全量渲染（**降级分支同源**）。

    `context is None`（总闸关 / 无 policy）与 `frames` 为空（取数失败的降级行）两种情形都返回 None：
    前者既有渲染本就返回空表，后者既有渲染返回「中线背景暂不可用，已回退基础周期…」这条降级行——
    把它压成表头等于输出「整体=None；候选支撑=None；候选阻力=None」这种噪音行。
    """
    if not context or not (context.get("frames") or []):
        return None
    from .market_text import _format_candidate   # 与全量形态共用同一个表头渲染真源
    alignment = (context.get("trendAlignment") or {}).get("status")
    header = "；".join((
        f"中线背景：整体={alignment}",
        _format_candidate("候选支撑", context.get("nearestSupport")),
        _format_candidate("候选阻力", context.get("nearestResistance")),
    ))
    return [header, "（各周期明细与本根首次研判相同，未重复列出）"]


def format_digest_repeat(digest: dict | None, bar: str | None, *, has_tactical: bool) -> str:
    """重复轮的压缩执行周期证据（三行压成一行）；`digest` 为 None ⇒ 空串（整段省略）。

    `has_tactical` 决定是否追加「本轮最新变化见战术周期」这句**指路**。三个总闸互相独立，故
    「压缩开、战术关」是可达配置（也可能是运行期降级：1m 执行周期无候选战术周期、或全周期取数
    失败），此时那句话会把模型指向一段根本不存在的证据——正是本特性反复要杜绝的「说的和做的不
    一致」。指路只在真有路时才给。
    """
    if not digest:
        return ""
    price_action = digest.get("priceAction") or {}
    volume = digest.get("volume") or {}
    factors = digest.get("factors") or {}
    rsi = factors.get("rsi14") or {}
    atr = factors.get("atrPct") or {}
    scope = f"执行周期（{bar}）证据" if bar else "执行周期证据"
    pointer = "；本轮最新变化见战术周期" if has_tactical else ""
    return (f"{scope}：与本根首次研判相同（趋势={price_action.get('trend')}，"
            f"RSI14={rsi.get('value')}({rsi.get('label')})，量比={volume.get('ratio')}，"
            f"ATR%={atr.get('value')}）{pointer}")


# ---------- 唤醒原因七态文案 ----------
def _wake_candle_close(wake: dict) -> str:
    return f"新 K 线收盘（{_bar(wake)} 确认根落地，信号板已按新根重算）"


def _wake_bar_advanced(wake: dict) -> str:
    return ("补看新 K 线（上一轮研判仍在进行时该根已收线，本轮是它的**首次**研判"
            "——不要把上一轮结论当作对这根 K 线的判断）")


def _wake_resting_order(wake: dict) -> str:
    return (f"挂单盯守（你有一笔未成交限价委托，已挂 {_duration(wake.get('restingAgeS'))}；"
            "若行情已走反，可用 cancel_order 撤回）")


def _wake_profit_tier(wake: dict) -> str:
    before = wake.get("lastGuardStatus") or "未知"
    after = wake.get("guardStatus") or "未知"
    return f"浮盈跨档（保护层状态 {before}→{after}，移动止盈线已激活）"


def _wake_price_breakout(wake: dict) -> str:
    return (f"价格异动（距上轮 {_duration(wake.get('sinceLastRoundS'))}，"
            f"价格 {_signed_pct(wake.get('priceMovePct'))}{_atr_clause(wake)}，"
            f"已越过{_threshold_text(wake)}的唤醒阈值）")


def _wake_position_watch(wake: dict) -> str:
    return (f"持仓盯市（距上轮 {_duration(wake.get('sinceLastRoundS'))}，"
            f"价格 {_signed_pct(wake.get('priceMovePct'))}）。"
            "退出保护（移动止盈 / 保本 / 交易所侧 OCO）由系统 5 秒盯市层确定性执行，无需你重复确认；"
            "你本轮的价值在于判断是否应**提前**离场或反手")


def _wake_heartbeat(wake: dict) -> str:
    return (f"心跳复盘（距上轮 {_duration(wake.get('sinceLastRoundS'))}；无新收线、无挂单、"
            f"价格变动 {_signed_pct(wake.get('priceMovePct'))} 未达{_threshold_text(wake)}阈值"
            "——**若**本轮证据确与上轮相同，重复数据下的观望不构成新的独立证据）")


_WAKE_BODY = {
    "candle_close": _wake_candle_close,
    "bar_advanced": _wake_bar_advanced,
    "resting_order": _wake_resting_order,
    "profit_tier": _wake_profit_tier,
    "price_breakout": _wake_price_breakout,
    "position_watch": _wake_position_watch,
    "heartbeat": _wake_heartbeat,
}


# ---------- 战术周期段 ----------
def _tactical_header(context: dict) -> str:
    wake_s = context.get("wakeIntervalS")
    pace = f"贴合 {_minutes(wake_s)}唤醒节奏；" if wake_s else ""
    return f"战术周期（{pace}仅用于择时与确认，不得单独反转中线判断）："


def _format_change(change: dict | None) -> str:
    if not change:
        return ""
    atr_mult = change.get("atrMult")
    atr_text = "" if atr_mult is None else f"（≈{atr_mult:.2f}×{change.get('atrBar')} ATR）"
    return f"自上一决策轮：价 {_signed_pct(change.get('pct'))}{atr_text}"


def _frame_line(frame: dict) -> str:
    digest = frame.get("digest") or {}
    price_action = digest.get("priceAction") or {}
    rsi = (digest.get("factors") or {}).get("rsi14") or {}
    volume = digest.get("volume") or {}
    closed = frame.get("closedBars")
    closed_text = "" if closed is None else f"，自上轮收 {closed} 根"
    stale = "，数据陈旧" if frame.get("stale") else ""
    # 刻意不渲染 sampleCount：它是数据充足度诊断而非可决策的证据，模型对它无从下手，而 stale
    # 与「缺失周期」已经承担了诚实标注的职责。它仍随 wire 下发、仍在轨迹面板上可见（A-4b 收紧）。
    return (f"- {frame.get('bar')}：体制={(frame.get('regime') or {}).get('primary')}，"
            f"方向={price_action.get('trend')}（近窗 {price_action.get('changePct')}%），"
            f"RSI14={rsi.get('value')}({rsi.get('label')})，量比={volume.get('ratio')}"
            f"{closed_text}{stale}")


def _frame_detail_line(frame: dict) -> str:
    """`full` 档的第二行：只留对**择时**边际信息量最高的四项。

    刻意不含布林%B 与末根 O/H/L/C：前者与区间位置高度重合，后者是四个数、字符成本最高而对
    「现在是不是好进场点」几乎不增信息。渐进式披露的收紧顺序就是先砍这两处（A-4b）。
    """
    digest = frame.get("digest") or {}
    price_action = digest.get("priceAction") or {}
    factors = digest.get("factors") or {}
    macd = factors.get("macdHist") or {}
    atr = factors.get("atrPct") or {}
    return (f"  └ 均线={price_action.get('emaRelation')}（斜率 {price_action.get('emaSlopePct')}%），"
            f"区间位置={price_action.get('rangePos')}，MACD柱={macd.get('value')}"
            f"({macd.get('label')})，ATR%={atr.get('value')}({atr.get('label')})")


def _tactical_footnotes(context: dict) -> list[str]:
    lines: list[str] = []
    if context.get("alignment") == "diverged":
        pairs = [f"{context.get('executionBar')}={context.get('executionPrimary')}"]
        pairs.extend(f"{item.get('bar')}={(item.get('regime') or {}).get('primary')}"
                     for item in context.get("frames") or [])
        lines.append(f"战术与执行周期体制背离（{'，'.join(pairs)}）："
                     "优先以执行周期与中线背景为准，战术周期只回答「现在是不是好的进场点」。")
    missing = context.get("missingBars") or []
    if missing:
        lines.append(f"缺失周期={'/'.join(missing)}；不得补猜。")
    return lines


def _truncate(lines: list[str], max_chars: int) -> list[str]:
    """按字符预算截断，**保留抬头**并追加可见标记；`max_chars<=0` ⇒ 不截断。"""
    if max_chars <= 0 or sum(len(line) + 1 for line in lines) <= max_chars:
        return lines
    kept = [lines[0]]
    used = len(lines[0]) + 1
    for line in lines[1:]:
        if used + len(line) + 1 > max_chars:
            break
        kept.append(line)
        used += len(line) + 1
    kept.append(_TRUNCATION_HINT.format(max_chars=max_chars))
    return kept


# ---------- 小工具 ----------
def _bar(wake: dict) -> str:
    return wake.get("executionBar") or "执行周期"


def _duration(seconds) -> str:
    """秒 → 「3 分 2 秒」；缺失 ⇒ 「未知时长」（绝不假数据）。"""
    if not isinstance(seconds, (int, float)):
        return "未知时长"
    total = int(max(0.0, float(seconds)))
    return f"{total // 60} 分 {total % 60} 秒"


def _minutes(seconds) -> str:
    if not isinstance(seconds, (int, float)) or seconds <= 0:
        return "定时"
    return f"{seconds / 60:g} 分钟"


def _signed_pct(pct) -> str:
    return "未知" if not isinstance(pct, (int, float)) else f"{pct:+.2f}%"


def _atr_clause(wake: dict) -> str:
    atr_mult = wake.get("priceMoveAtr")
    if not isinstance(atr_mult, (int, float)):
        return ""
    return f"，约 {atr_mult:.2f}×{_bar(wake)} ATR"


def _threshold_text(wake: dict) -> str:
    """实际生效的门槛文案：ATR 可用时取**折算后**的 `moveThresholdAtrEff`，否则取 pct 回退判据。"""
    if isinstance(wake.get("priceMoveAtr"), (int, float)):
        eff = wake.get("moveThresholdAtrEff")
        if isinstance(eff, (int, float)) and eff > 0:
            return f" {eff:.2f}×{_bar(wake)} ATR "
    pct = wake.get("moveThresholdPct")
    return f" {pct * 100:.2f}% " if isinstance(pct, (int, float)) else " 唤醒 "
