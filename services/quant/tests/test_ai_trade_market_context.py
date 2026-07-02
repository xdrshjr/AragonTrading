"""E1 · 市场体制上下文单测（ai-auto-trading-enhancements v4 §2.2 / §7.1，R-P1-2）。

合成明确上升趋势帧 → `primary == "trend_up"`（钉真实枚举，非 `uptrend`）；<60 根 → None；
`ai_trade_regime_context=False` → None；`classify_regime` 异常被吞为 None（决策轮不崩）。纯离线。
"""
from __future__ import annotations

from app.ai_auto.market_context import build_market_context
from app.config import Settings
from app.engine.backtester import candles_to_df

_T = 1_700_000_000
_S = 3600


def _uptrend_df(n: int = 120):
    """严格单调上行（每根 +1）→ ER≈1、净变化>0、均线多头 → classify_regime 判 trend_up。"""
    rows = [{"ts": _T + i * _S, "o": 100.0 + i, "h": 105.0 + i, "l": 95.0 + i,
             "c": 100.0 + i, "vol": 1.0} for i in range(n)]
    return candles_to_df(rows)


def test_uptrend_classified_as_trend_up():
    mc = build_market_context(_uptrend_df(), "1H", Settings())
    assert mc is not None
    assert mc["primary"] == "trend_up"          # 真实枚举，非 uptrend（R-P1-2）
    assert mc["volatility"] in ("high_volatility", "low_volatility", "normal")
    assert mc["event"] in ("crash", "breakout", "none")
    assert 0.0 <= mc["confidence"] <= 1.0
    assert isinstance(mc["labels"], list) and "trend_up" in mc["labels"]


def test_insufficient_bars_returns_none():
    assert build_market_context(_uptrend_df(30), "1H", Settings()) is None   # <60 根 → 安全降级


def test_disabled_flag_returns_none():
    settings = Settings(ai_trade_regime_context=False)
    assert build_market_context(_uptrend_df(), "1H", settings) is None


def test_classify_exception_swallowed_to_none(monkeypatch):
    def _boom(*_a, **_k):
        raise RuntimeError("regime blew up")

    monkeypatch.setattr("app.ai_auto.market_context.classify_regime", _boom)
    assert build_market_context(_uptrend_df(), "1H", Settings()) is None     # 异常→None，不外抛
