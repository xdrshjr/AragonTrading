"""E1 · 市场体制上下文（ai-auto-trading-enhancements v4 §2.2）。

薄封装 `engine.regime.classify_regime`：对当前决策窗口末根做一次单点体制判定（纯计算、严格因果、
`min_bars=60`），把结论压成紧凑 dict 注入决策 prompt，让 AI 在「多策略立场」之外再叠加「当前是趋势/
震荡/高低波动/突破/急跌」这层市场态势。**只读增强、零新增下单面**：禁用 / 数据不足(<60) / 计算异常一律
返回 None（安全降级——决策轮从不因体制判定崩溃或超时）。

真实标签枚举（`classify_regime` 固定产出，前端 `describeRegime` 与单测须以此为准，勿臆造）：
- `primary ∈ {trend_up, trend_down, range}`
- `volatility ∈ {high_volatility, low_volatility, normal}`
- `event ∈ {crash, breakout, none}`
"""
from __future__ import annotations

import pandas as pd

from ..config import Settings
from ..engine.regime import classify_regime


def build_market_context(df: pd.DataFrame, bar: str, settings: Settings) -> dict | None:
    """把当前窗口末根的体制判定压成紧凑上下文；禁用 / 数据不足 / 异常一律 None（安全降级）。

    Args:
        df: 当前基础周期窗口（`candles_to_df` 产物，列 ts/o/h/l/c/vol，升序）。
        bar: 标准 OKX 周期（透传给 `classify_regime`，当前判据全相对量纲、不分周期）。
        settings: 运行配置；`ai_trade_regime_context=False` 时直接返回 None（不注入）。

    Returns:
        `{primary, volatility, event, confidence, labels}`——体制判定紧凑视图；
        E1 关闭 / 窗口 < `min_bars` / 计算异常时返回 None（决策 prompt 省略该段，保持纯净）。
    """
    if not settings.ai_trade_regime_context:
        return None
    try:
        result = classify_regime(df, bar)          # 严格因果单点判定（不足即 raise ValueError）
    except Exception:  # noqa: BLE001 —— 数据不足(<60) / 计算异常 → 省略该层，不崩决策轮（安全降级）
        return None
    return {"primary": result.primary, "volatility": result.volatility,
            "event": result.event, "confidence": round(result.confidence, 3),
            "labels": list(result.labels)}
