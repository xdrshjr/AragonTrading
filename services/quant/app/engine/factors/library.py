"""因子库（FACTOR_LIBRARY，唯一真源 C6）—— 纯函数、严格因果、相对量纲 (spec §2.2)。

每个因子复用 ``engine.indicators`` 的纯函数（ema/sma/rsi/bollinger/atr/vwap_session），
``compute(df, params)`` 返回与 df 等长、index 0..n-1 的 ``pd.Series``，**只用 ≤ i 数据**
（rolling / ewm / shift 全部因果，绝不前视）。NaN 暖机段由评估 / 组合层统一按「跳过」处理，
绝不补零外推（C1）。``px_vwap_dist`` 仅日内 bar 有意义，非日内返回全 NaN（coverage=0）。
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
import pandas as pd

from ..indicators import atr, bollinger, ema, rsi, vwap_session
from ...okx.types import INTRADAY_BARS


@dataclass(frozen=True)
class FactorSpec:
    """因子库单条目（key 隐含在 dict 键）。

    Attributes:
        cn: 一句话中文定义（目录 / Agent primer 共用真源 C6）。
        category: 语义分组（momentum/trend/reversal/volatility/volume/liquidity/mean_revert）。
        direction_hint: "long" | "short"，因子值高时倾向看多还是看空。
        params_schema: 与前端 ParamSchema 对齐的参数定义（缺省即生产值）。
        compute: 纯计算 ``(df, params) -> pd.Series``（严格因果，与 df 等长）。
        intraday_only: 仅日内 bar 有效（非日内返回全 NaN，挖掘中跳过）。
        hidden: 仅供内部桥接、**不进目录**（strategy-factor-discovery §2.2 / D-1）。
            末位带默认值 ⇒ 既有 12 条构造零改。``available_factors`` /
            ``visible_factor_keys`` / seed / ``default_candidates`` /
            Agent 工具枚举五处遍历现场**必须**一致过滤它（漏一处即出空壳因子，R-10）。
    """

    cn: str
    category: str
    direction_hint: str
    params_schema: dict
    compute: Callable[[pd.DataFrame, dict], pd.Series]
    intraday_only: bool = False
    hidden: bool = False


def _param(params: dict | None, key: str, default: float) -> float:
    """读单个参数并回落库默认（非法 / 缺失即默认，边界一次性兜底）。"""
    value = (params or {}).get(key, default)
    try:
        return type(default)(value)
    except (TypeError, ValueError):
        return default


# ============================ 因子计算（每个均严格因果） ============================
def _mom_roc(df: pd.DataFrame, params: dict) -> pd.Series:
    """N 根收益率动量：c[i]/c[i-win] - 1。"""
    win = int(_param(params, "win", 20))
    return df["c"].pct_change(win)


def _mom_ema_slope(df: pd.DataFrame, params: dict) -> pd.Series:
    """EMA 斜率（归一化）：EMA 相对 period 根前的变化率。"""
    period = int(_param(params, "period", 20))
    line = ema(df["c"], period)
    prev = line.shift(period)
    return (line - prev) / prev.replace(0.0, np.nan)


def _trend_ema_dist(df: pd.DataFrame, params: dict) -> pd.Series:
    """收盘相对 EMA 的偏离度：(c - ema)/ema。"""
    period = int(_param(params, "period", 60))
    line = ema(df["c"], period)
    return (df["c"] - line) / line.replace(0.0, np.nan)


def _trend_adx_proxy(df: pd.DataFrame, params: dict) -> pd.Series:
    """方向性强度代理：period 根净位移 / 同期 ATR（带符号，按波动归一）。"""
    period = int(_param(params, "period", 14))
    travel = df["c"] - df["c"].shift(period)
    band = atr(df, period).replace(0.0, np.nan)
    return travel / band


def _rev_rsi(df: pd.DataFrame, params: dict) -> pd.Series:
    """RSI 反向：原始 RSI（高=超买，direction_hint=short）。"""
    period = int(_param(params, "period", 14))
    return rsi(df["c"], period)


def _rev_boll_z(df: pd.DataFrame, params: dict) -> pd.Series:
    """收盘在布林带内的 z 位置：(c - 中轨)/标准差。"""
    period = int(_param(params, "period", 20))
    std_mult = float(_param(params, "std", 2.0))
    upper, ma, lower = bollinger(df["c"], period, std_mult)
    width = (upper - lower).replace(0.0, np.nan)
    return (df["c"] - ma) / (width / (2.0 * std_mult))


def _vol_atr_pct(df: pd.DataFrame, params: dict) -> pd.Series:
    """相对波动：ATR / 收盘。"""
    period = int(_param(params, "period", 14))
    return atr(df, period) / df["c"].replace(0.0, np.nan)


def _vol_realized(df: pd.DataFrame, params: dict) -> pd.Series:
    """对数收益滚动标准差（已实现波动）。"""
    win = int(_param(params, "win", 20))
    log_ret = np.log(df["c"] / df["c"].shift(1))
    return log_ret.rolling(window=win, min_periods=win).std(ddof=0)


def _volu_zscore(df: pd.DataFrame, params: dict) -> pd.Series:
    """成交量 z-score：(vol - 滚动均)/滚动标准差。"""
    win = int(_param(params, "win", 20))
    mean = df["vol"].rolling(window=win, min_periods=win).mean()
    std = df["vol"].rolling(window=win, min_periods=win).std(ddof=0).replace(0.0, np.nan)
    return (df["vol"] - mean) / std


def _volu_obv_slope(df: pd.DataFrame, params: dict) -> pd.Series:
    """OBV 斜率（资金流向）：OBV 在 win 内的变化，按同期均量归一。"""
    win = int(_param(params, "win", 20))
    sign = np.sign(df["c"].diff()).fillna(0.0)
    obv = (sign * df["vol"]).cumsum()
    avg_vol = df["vol"].rolling(window=win, min_periods=win).mean().replace(0.0, np.nan)
    return (obv - obv.shift(win)) / (avg_vol * win)


def _liq_amihud(df: pd.DataFrame, params: dict) -> pd.Series:
    """Amihud 非流动性：|收益率| / 成交量 的滚动均值（高=越不流动）。"""
    win = int(_param(params, "win", 20))
    ret = df["c"].pct_change().abs()
    illiq = ret / df["vol"].replace(0.0, np.nan)
    return illiq.rolling(window=win, min_periods=win).mean()


def _px_vwap_dist(df: pd.DataFrame, params: dict) -> pd.Series:
    """收盘相对 session VWAP 偏离：(c - vwap)/vwap（仅日内，非日内全 NaN）。"""
    bar = str((params or {}).get("_bar", ""))
    vwap = vwap_session(df, bar)
    return (df["c"] - vwap) / vwap.replace(0.0, np.nan)


def _expr_factor(df: pd.DataFrame, params: dict) -> pd.Series:
    """通用表达式因子（D-1 桥）：``params["expr"]`` 为 discovery 规范式。

    降级契约（strategy-factor-discovery §2.2 硬约束，评审 P1-1）：解析 / 求值任何失败路径
    都必须返回**与 df 等长、index 0..n-1 的全 NaN Series**。既有
    ``test_factor_library.py`` 的因果性 / 形状两条用例会以 ``params=None`` 遍历全库到本条目，
    返回空 Series 或长度不符即打红。

    延迟导入 + **直达子模块**（绝不 ``from ...discovery import ...``）：避免
    ``discovery/__init__`` 的再导出把 ``engine.factors.evaluation`` 整条链拉起，形成
    ``engine ↔ discovery`` 回边（评审 P2-7）。``expression`` 只依赖 numpy/pandas。
    """
    from ...discovery.expression import evaluate_expression_text

    return evaluate_expression_text(df, str((params or {}).get("expr", "")))


# ============================ 因子库（唯一真源 C6） ============================
def _schema(label: str, **fields: dict) -> dict:
    """构造单因子 params_schema（与前端 ParamSchema 对齐；label 仅占位说明）。"""
    _ = label
    return dict(fields)


FACTOR_LIBRARY: dict[str, FactorSpec] = {
    "mom_roc": FactorSpec(
        "N 根收益率动量", "momentum", "long",
        {"win": {"type": "int", "default": 20, "min": 2, "max": 200, "step": 1, "label": "回看根数"}},
        _mom_roc),
    "mom_ema_slope": FactorSpec(
        "EMA 斜率（归一化）", "momentum", "long",
        {"period": {"type": "int", "default": 20, "min": 2, "max": 200, "step": 1, "label": "EMA 周期"}},
        _mom_ema_slope),
    "trend_ema_dist": FactorSpec(
        "收盘相对 EMA 偏离度", "trend", "long",
        {"period": {"type": "int", "default": 60, "min": 5, "max": 300, "step": 1, "label": "EMA 周期"}},
        _trend_ema_dist),
    "trend_adx_proxy": FactorSpec(
        "方向性强度代理（净位移/ATR）", "trend", "long",
        {"period": {"type": "int", "default": 14, "min": 2, "max": 100, "step": 1, "label": "周期"}},
        _trend_adx_proxy),
    "rev_rsi": FactorSpec(
        "RSI 反向（超买超卖）", "reversal", "short",
        {"period": {"type": "int", "default": 14, "min": 2, "max": 100, "step": 1, "label": "RSI 周期"}},
        _rev_rsi),
    "rev_boll_z": FactorSpec(
        "收盘在布林带内的 z 位置", "reversal", "short",
        {"period": {"type": "int", "default": 20, "min": 5, "max": 100, "step": 1, "label": "布林周期"},
         "std": {"type": "number", "default": 2.0, "min": 1.0, "max": 4.0, "step": 0.5, "label": "标准差倍数"}},
        _rev_boll_z),
    "vol_atr_pct": FactorSpec(
        "ATR / 收盘（相对波动）", "volatility", "short",
        {"period": {"type": "int", "default": 14, "min": 2, "max": 100, "step": 1, "label": "ATR 周期"}},
        _vol_atr_pct),
    "vol_realized": FactorSpec(
        "对数收益滚动标准差", "volatility", "short",
        {"win": {"type": "int", "default": 20, "min": 5, "max": 200, "step": 1, "label": "回看根数"}},
        _vol_realized),
    "volu_zscore": FactorSpec(
        "成交量 z-score", "volume", "long",
        {"win": {"type": "int", "default": 20, "min": 5, "max": 200, "step": 1, "label": "回看根数"}},
        _volu_zscore),
    "volu_obv_slope": FactorSpec(
        "OBV 斜率（资金流向）", "volume", "long",
        {"win": {"type": "int", "default": 20, "min": 5, "max": 200, "step": 1, "label": "回看根数"}},
        _volu_obv_slope),
    "liq_amihud": FactorSpec(
        "Amihud 非流动性（|ret|/vol）", "liquidity", "short",
        {"win": {"type": "int", "default": 20, "min": 5, "max": 200, "step": 1, "label": "回看根数"}},
        _liq_amihud),
    "px_vwap_dist": FactorSpec(
        "收盘相对 session VWAP 偏离", "mean_revert", "short",
        {}, _px_vwap_dist, intraday_only=True),
    # D-1 桥（strategy-factor-discovery §2.2）：挖掘产物经本条目零改地复用整条既有管线
    # （评估 / 权重训练 / 组合 / 回测 / AI 上下文）。``hidden=True`` ⇒ 不进因子目录、不进
    # Agent 工具枚举、不 seed、不进经典挖掘候选集——它只作为挖掘产物的落库载体存在。
    "expr": FactorSpec(
        "自动挖掘表达式因子", "discovered", "long",
        {"expr": {"type": "expr", "default": "", "label": "因子表达式", "readonly": True}},
        _expr_factor, hidden=True),
}


# ============================ 公开 API ============================
def factor_meta(key: str) -> dict | None:
    """单因子元信息 dict（未知 key 返回 None）。"""
    spec = FACTOR_LIBRARY.get(key)
    if spec is None:
        return None
    return {"key": key, "cn": spec.cn, "category": spec.category,
            "directionHint": spec.direction_hint, "paramsSchema": spec.params_schema,
            "intradayOnly": spec.intraday_only}


def visible_factor_keys() -> tuple[str, ...]:
    """目录可见的因子 key（过滤 ``hidden``）—— 五处遍历现场的**唯一口径**（§2.2）。

    ``hidden`` 条目（当前仅 D-1 的 ``expr`` 桥）仍在 ``FACTOR_LIBRARY`` 里可被
    ``compute_factor`` / ``validate_factor_key`` 正常解析，只是不出现在任何面向用户或
    面向模型的目录里——否则会出现「一条永远算不出值的空表达式因子」。
    """
    return tuple(key for key, spec in FACTOR_LIBRARY.items() if not spec.hidden)


def available_factors() -> list[dict]:
    """因子目录 ``[{key,cn,category,directionHint,paramsSchema,intradayOnly}]``（UI / primer 共用真源 C6）。

    只回 ``visible_factor_keys()``：``hidden`` 桥接条目绝不进目录（§2.2 四处改点之一）。
    """
    return [factor_meta(key) for key in visible_factor_keys()]  # type: ignore[misc]


def is_bar_compatible(key: str, bar: str) -> bool:
    """该因子是否兼容当前 bar（日内限定因子在非日内 bar 不兼容，挖掘据此跳过）。"""
    spec = FACTOR_LIBRARY.get(key)
    if spec is None:
        return False
    return (not spec.intraday_only) or (bar in INTRADAY_BARS)


def compute_factor(df: pd.DataFrame, key: str, params: dict | None = None, *,
                   bar: str = "") -> pd.Series:
    """计算单因子值（与 df 等长、严格因果）。

    Args:
        df: ``candles_to_df`` 产物（列 ts/o/h/l/c/vol，升序）。
        key: ``FACTOR_LIBRARY`` 中的因子 key。
        params: 覆盖库默认参数（缺省即生产默认）。
        bar: 当前周期（日内限定因子据此判定；非日内返回全 NaN）。

    Returns:
        index 对齐 ``0..n-1`` 的 ``pd.Series``（NaN 暖机段由上层按「跳过」处理）。

    Raises:
        ValueError: ``key`` 不在 ``FACTOR_LIBRARY``（工具 / 路由层转显式错误）。
    """
    spec = FACTOR_LIBRARY.get(key)
    if spec is None:
        raise ValueError(f"未知因子：{key}")
    work = df.reset_index(drop=True)
    if spec.intraday_only and bar not in INTRADAY_BARS:
        return pd.Series(np.nan, index=work.index)
    call_params = {**(params or {}), "_bar": bar}
    series = spec.compute(work, call_params)
    return pd.Series(series, index=work.index, dtype="float64")
