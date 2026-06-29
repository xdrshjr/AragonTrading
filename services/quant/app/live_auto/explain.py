"""TriggerExplain 纯构造器（auto-trade-explainability §2.4）。

为执行器生命周期分支合成统一形状的 ``TriggerExplain``（camelCase，与 WS wire / DB JSON 同构）：
- ``atomic``：原子策略 / seeded 持仓初始化的通用开 / 平解释（无因子归因）；
- ``risk_exit``：``max_hold`` 时间止盈；
- ``reverse``：反手「先平后开」的平仓侧；
- ``session_stop``：会话停止 / 会话级金额止盈止损的整仓平；
- ``external_close``：交易所侧 OCO / 外部干预平仓的只读合成成交。

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
    """交易所侧 / 外部平仓的只读合成解释（仅记录已发生的平仓，本系统不下任何单，P1-1 / R11）。"""
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
    return _base("external_close", "close", "交易所侧 / 外部平仓",
                 summary="本系统据 reconcile / flatten 侦测到交易所侧已平仓并据此校正；非策略主动下单",
                 metrics=metrics)


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
