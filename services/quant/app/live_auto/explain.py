"""TriggerExplain 纯构造器（auto-trade-explainability §2.4）。

为执行器生命周期分支合成统一形状的 ``TriggerExplain``（camelCase，与 WS wire / DB JSON 同构）：
- ``atomic``：原子策略 / seeded 持仓初始化的通用开 / 平解释（无因子归因）；
- ``risk_exit``：``max_hold`` 时间止盈；
- ``reverse``：反手「先平后开」的平仓侧；
- ``session_stop``：会话停止 / 会话级金额止盈止损的整仓平；
- ``external_close``：交易所侧 OCO / 外部干预平仓的只读合成成交；
- ``profit_guard``：利润保护层（移动止盈 / 保本 / 硬止盈）触发的强制平仓。

所有构造器为**纯函数、无副作用**，只含行情 / 风控数值与公开标签，**绝不含任何凭证**（C7）：
凭证只在 ``okx/*`` 下单链路使用，本模块从不接收 api_key / secret / passphrase。
"""
from __future__ import annotations

_SCHEMA = 1

_SIDE_LABEL = {"long": "多", "short": "空", "close": "平"}


def _metric(label: str, value: str, *, tone: str | None = None, hint: str | None = None) -> dict:
    metric = {"label": label, "value": value}
    if tone:
        metric["tone"] = tone
    if hint:
        metric["hint"] = hint
    return metric


def _base(kind: str, side: str, headline: str, *, summary: str | None = None,
          metrics: list[dict] | None = None, risk: dict | None = None) -> dict:
    explain = {"schema": _SCHEMA, "kind": kind, "side": side, "headline": headline,
               "metrics": metrics or []}
    if summary:
        explain["summary"] = summary
    if risk:
        explain["risk"] = risk
    return explain


def _fmt_px(value: float | None) -> str:
    return f"{float(value):.2f}" if value is not None else "—"


def _fmt_usd(value: float | None) -> str:
    return f"{float(value):+.2f} USDT" if value is not None else "—"


def _pnl_tone(value: float | None) -> str:
    if value is None or value == 0:
        return "neutral"
    return "long" if value > 0 else "short"


def _side_label(side: str | None) -> str:
    return _SIDE_LABEL.get(side or "", side or "—")


def _opposite(side: str) -> str:
    return "short" if side == "long" else "long"


def build_atomic_explain(side: str, reason: str, *, risk: dict | None = None,
                         summary: str | None = None) -> dict:
    """原子策略 / seeded 持仓的通用解释（无因子归因，据 reason 给类别 + 一句话）。

    seeded（持仓初始化 / 外部种子）trade 无信号来源，回落本构造器并由调用方传入对应 summary，
    保证前端展开始终有内容、不空洞（R7）。
    """
    is_open = side in ("long", "short")
    headline = reason or ("开仓" if is_open else "平仓")
    if summary is None:
        summary = "原子策略开仓信号触发" if is_open else "策略平仓信号触发"
    return _base("atomic", side, headline, summary=summary, risk=risk)


def build_risk_exit_explain(*, bars_held: int, max_hold_bars: int | None,
                            entry_price: float, exit_price: float, pnl: float) -> dict:
    """时间止盈（``max_hold``）：持仓达最大持有根数主动平仓。"""
    metrics = [
        _metric("持仓根数", str(int(bars_held))),
        _metric("上限根数", str(int(max_hold_bars)) if max_hold_bars else "—"),
        _metric("入场价", _fmt_px(entry_price)),
        _metric("平仓价", _fmt_px(exit_price)),
        _metric("本笔盈亏", _fmt_usd(pnl), tone=_pnl_tone(pnl)),
    ]
    return _base("risk_exit", "close", "持仓时间止盈（max_hold）",
                 summary="持仓达到最大持有根数，按时间规则主动平仓", metrics=metrics)


def build_reverse_explain(*, pos_side: str, entry_price: float, exit_price: float,
                          pnl: float) -> dict:
    """反手平仓侧解释（出现反向信号，先平当前仓再开反向仓）。"""
    metrics = [
        _metric("原方向", _side_label(pos_side)),
        _metric("新方向", _side_label(_opposite(pos_side))),
        _metric("入场价", _fmt_px(entry_price)),
        _metric("平仓价", _fmt_px(exit_price)),
        _metric("本笔盈亏", _fmt_usd(pnl), tone=_pnl_tone(pnl)),
    ]
    return _base("reverse", "close", "反手平仓（先平后开）",
                 summary="出现反向信号，先平掉当前持仓再开反向仓", metrics=metrics)


def build_session_stop_explain(*, pos_side: str, entry_price: float, exit_price: float,
                               pnl: float, session_pnl: float) -> dict:
    """会话级整仓平仓（会话停止 / 会话金额止盈止损触发）。"""
    metrics = [
        _metric("会话累计盈亏", _fmt_usd(session_pnl), tone=_pnl_tone(session_pnl)),
        _metric("原方向", _side_label(pos_side)),
        _metric("入场价", _fmt_px(entry_price)),
        _metric("平仓价", _fmt_px(exit_price)),
        _metric("本笔盈亏", _fmt_usd(pnl), tone=_pnl_tone(pnl)),
        _metric("平仓范围", "整仓平"),
    ]
    return _base("session_stop", "close", "会话级整仓平仓",
                 summary="会话停止或会话级金额止盈 / 止损触发，平掉全部自管持仓",
                 metrics=metrics)


def build_external_close_explain(*, pos_side: str, source: str, entry_price: float,
                                 exit_price: float, pnl: float) -> dict:
    """交易所侧 / 外部平仓的只读合成解释（仅记录已发生的平仓，本系统不下任何单，P1-1 / R11）。

    返回 dict 带一个**顶层加法键** ``source``（ai-trade-horizon-acceptance-loop 轴 B）：oco 与
    external 的区别在此之前**只写在中文 metric 文案里**，机器读不到，退出来源归因就只能去解析
    展示层——把展示层当契约是本仓库反复出事的模式。该键不参与任何 ``live_auto`` 分支判断，
    ``live_auto`` 的渲染按 ``metrics`` 数组走、对未知顶层键无感（冻结用例 W-7 守「除 source 外
    与改动前逐键相等」）。
    """
    source_label = {
        "oco": "交易所侧止盈止损（OCO）触发",
        "external": "外部 / 手动平仓",
    }.get(source, "交易所侧平仓")
    metrics = [
        _metric("来源", source_label),
        _metric("原持仓方向", _side_label(pos_side)),
        _metric("入场价", _fmt_px(entry_price)),
        _metric("平仓价", _fmt_px(exit_price)),
        _metric("本笔盈亏", _fmt_usd(pnl), tone=_pnl_tone(pnl)),
    ]
    explain = _base("external_close", "close", "交易所侧 / 外部平仓",
                    summary="本系统据 reconcile / flatten 侦测到交易所侧已平仓并据此校正；非策略主动下单",
                    metrics=metrics)
    explain["source"] = source
    return explain


_TRAIL_BASIS_LABEL = {
    "giveback": "回吐比例",
    "atr_floor": "波动率下界",
    "atr_cap": "波动率上界",
    "clamped_breakeven": "夹至保本线",
    "suppressed_at_entry": "本轮作废（低于入场价）",
}


def build_profit_guard_explain(*, pos_side: str, trigger_label: str, entry_price: float,
                               exit_price: float, peak_pnl: float | None,
                               giveback_ratio: float | None, pnl: float | None,
                               trail: dict | None = None) -> dict:
    """利润保护层强制平仓（ai-trade-profit-protection §2.7）：移动止盈 / 保本 / 硬止盈触发。

    与 ``risk_exit``（时间止盈）刻意分列一个新 kind：前者是「持仓根数到了」，本条是「已到手的浮盈
    正在回吐」——归因语义不同，前端色调与文案也不同，混用会让用户看不出利润是被谁锁住的。

    ``pnl`` 为触发瞬间的**本地派生浮盈**（不含手续费 / 资金费，比交易所 upl 略乐观），故标注为
    「触发时浮盈」而非「本笔盈亏」——真实已实现盈亏以 trade.pnl 为准，绝不在此冒充。

    ``trail``（adaptive §3.1）：本轮**规则量**断面 ``{givebackPctEffective, trailBasis}``——构造时算得
    出来，故可以进 metrics。**「峰值→落袋」捕获率刻意不在这里**（P1-5）：本函数在 ``executor.apply``
    **之前**被调用，此刻 ``trade.pnl`` 尚不存在，捕获率是成交后量；它一律由前端 ``summarizeProfitCapture``
    与后端 ``_summarize_pairs`` 在成交后派生。``trail`` 为聚合结构体而非再加两个形参——本函数已有 7 个
    keyword 形参，继续堆参会更偏离 CLAUDE.md「形参 ≤ 5」。
    """
    giveback = "—" if giveback_ratio is None else f"{float(giveback_ratio) * 100:.1f}%"
    metrics = [
        _metric("触发规则", trigger_label, tone="short"),
        _metric("原持仓方向", _side_label(pos_side)),
        _metric("峰值浮盈", _fmt_usd(peak_pnl), tone=_pnl_tone(peak_pnl)),
        _metric("自峰值回吐", giveback),
        _metric("入场价", _fmt_px(entry_price)),
        _metric("平仓价", _fmt_px(exit_price)),
        _metric("触发时浮盈", _fmt_usd(pnl), tone=_pnl_tone(pnl),
                hint="本地派生口径，不含手续费与资金费"),
    ]
    trail_metric = _profit_guard_trail_metric(trail)
    if trail_metric is not None:
        metrics.insert(1, trail_metric)
    return _base("profit_guard", "close", f"利润保护平仓（{trigger_label}）",
                 # §六.1b / P0-1：措辞由 pnl **符号**派生——保本线触发 / 滑点不利时这笔可能打平甚至
                 # 净亏，无条件写「锁定利润」就是在一笔亏损出场上撒谎。
                 summary=("持仓浮盈自峰值回吐触及系统保护线，由利润保护层直接市价平仓锁定利润"
                          if pnl is not None and float(pnl) > 0
                          else "价格触及系统保护线，由利润保护层直接市价平仓离场"),
                 metrics=metrics)


def _profit_guard_trail_metric(trail: dict | None) -> dict | None:
    """本轮有效回吐阈 + 约束来源的规则量 metric；断面缺失 / 无有效阈 ⇒ None（整条 metric 省略）。"""
    giveback_eff = (trail or {}).get("givebackPctEffective")
    if giveback_eff is None:
        return None
    basis = _TRAIL_BASIS_LABEL.get((trail or {}).get("trailBasis"), "—")
    return _metric("本轮回吐阈", f"{float(giveback_eff) * 100:.1f}%",
                   hint=f"约束来源：{basis}")


def with_risk(explain: dict, risk: dict) -> dict:
    """返回带 ``risk`` 风控快照的解释副本（不修改原 dict；用于给信号解释挂开仓 OCO 触发价）。"""
    if not isinstance(explain, dict):
        return explain
    return {**explain, "risk": risk}


def as_close(explain: dict) -> dict:
    """把信号解释（开仓断面）转为平仓侧解释副本（side="close"；退出带平仓复用因子断面）。"""
    if not isinstance(explain, dict):
        return explain
    return {**explain, "side": "close"}
