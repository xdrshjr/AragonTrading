"""13 个机制族定义与分类（strategy-factor-discovery §2.3.2）—— 加密永续重排版。

报告的机制族是 A 股截面语境；本项目是 OKX 单标的时序，故按永续市场重排：``gap_premium``
（跨根跳空）在 7×24 永续上极弱，**保留但不给任何先验加权**；``orderflow_proxy`` 必须显式
标注为**代理**（OKX 公共 K 线不含主动买卖量，只能用 ``body × volume`` 之类近似）。

``classify_mechanism`` 是族归属的**唯一真源**：UI 分类、LLM prompt 覆盖度统计、候选表分组
全部读它，杜绝三处各判一套。它实现为**有序规则链**（最具辨识度的特征在前，首个命中即返回）
而非加权打分——打分在算子高度重叠的表达式空间里会让「趋势」被判成「量价背离」这类明显错分，
而有序规则链是确定性的、每条都能写出正向用例。
"""
from __future__ import annotations

from dataclasses import dataclass

from .expression import Node, fields_of, ops_of, subtrees

UNCLASSIFIED = "unclassified"

# 分类用的字段分组（规则链共用，单一口径）。
_PRICE_FIELDS = frozenset({"close", "open", "high", "low", "typical"})
_FLOW_FIELDS = frozenset({"volume", "turnover"})
_SHADOW_FIELDS = frozenset({"up_shadow", "down_shadow"})
_SEASON_FIELDS = frozenset({"hour_sin", "hour_cos"})
_RANGE_FIELDS = frozenset({"amplitude", "hl_ratio"})
_EXTREME_OPS = frozenset({"ts_max", "ts_min"})
_SMOOTH_OPS = frozenset({"ts_mean", "ema"})
_TREND_OPS = frozenset({"ts_mean", "ema", "delta", "roc", "ts_sum", "ts_delay"})


@dataclass(frozen=True)
class MechanismFamily:
    """单个机制族：偏好字段 / 算子（供生成器采样）+ 原型模板 + 面向 LLM 的研究假设。"""

    key: str
    cn: str
    hint: str
    fields: tuple[str, ...]
    ops: tuple[str, ...]
    prototypes: tuple[str, ...]
    proxy_note: str = ""


MECHANISM_FAMILIES: tuple[MechanismFamily, ...] = (
    MechanismFamily(
        "trend_persistence", "趋势持续",
        "价格在中长窗口上的方向惯性：长短均值之差 / 均线斜率的稳定性。",
        ("close", "typical", "ret"), ("ts_mean", "ema", "delta", "roc"),
        ("sub(ema(close,20),ema(close,60))",
         "ts_zscore(delta(ema(close,30),20),90)")),
    MechanismFamily(
        "momentum_reversal", "短周期反转",
        "短窗过度反应后的回摆：短期涨幅的负向预测力。",
        ("ret", "close"), ("ts_zscore", "neg", "ts_rank"),
        ("neg(ts_zscore(ret,8))",
         "sub(ts_zscore(ret,5),ts_zscore(ret,20))")),
    MechanismFamily(
        "volatility_regime", "波动率状态",
        "短期波动相对长期波动的位置：高波动状态下的收益特征差异。",
        ("ret", "amplitude"), ("ts_std", "ts_skew", "div"),
        ("div(ts_std(ret,20),ts_std(ret,90))",
         "ts_rank(ts_std(ret,20),120)")),
    MechanismFamily(
        "range_compression", "波动压缩—扩张",
        "振幅收敛后的扩张倾向：当前振幅相对自身分布的压缩度。",
        ("amplitude", "hl_ratio"), ("div", "ts_mean", "ts_rank"),
        ("div(amplitude,ts_mean(amplitude,20))",
         "sub(ts_rank(amplitude,60),ts_rank(amplitude,8))")),
    MechanismFamily(
        "volume_price_divergence", "量价背离",
        "价格方向与成交量强度不同步时的信息含量。",
        ("volume", "ret", "turnover"), ("ts_corr", "sub", "ts_rank"),
        ("ts_corr(ret,ts_zscore(volume,20),30)",
         "sub(ts_rank(ret,20),ts_rank(volume,20))")),
    MechanismFamily(
        "liquidity_impact", "流动性冲击",
        "单位成交量推动的价格幅度（Amihud 非流动性的时序版本）。",
        ("turnover", "volume", "ret"), ("div", "abs"),
        ("div(abs(ret),ts_rank(turnover,20))",
         "ts_mean(div(amplitude,ts_rank(volume,20)),20)")),
    MechanismFamily(
        "breakout_structure", "突破与区间结构",
        "收盘相对近期高低点的位置：区间边界的突破与假突破。",
        ("high", "low", "close"), ("ts_max", "ts_min", "ts_rank"),
        ("div(sub(close,ts_min(low,20)),ts_std(close,20))",
         "sub(ts_rank(close,60),0.5)")),
    MechanismFamily(
        "shadow_pressure", "影线压力",
        "上下影线的不对称：买卖双方在极值处的承接与压制。",
        ("up_shadow", "down_shadow", "body"), ("sub", "ts_mean"),
        ("sub(ts_mean(down_shadow,20),ts_mean(up_shadow,20))",
         "ts_zscore(sub(down_shadow,up_shadow),30)")),
    MechanismFamily(
        "mean_reversion_band", "通道均值回归",
        "收盘偏离滚动中枢的标准化距离：偏离越远回归压力越大。",
        ("close", "typical"), ("ts_mean", "ts_std", "ts_zscore"),
        ("div(sub(close,ts_mean(close,20)),ts_std(close,20))",
         "neg(ts_zscore(sub(close,ema(close,60)),60))")),
    MechanismFamily(
        "cross_scale_confirm", "跨尺度确认",
        "短窗信号与长窗方向是否一致：一致时强化、背离时衰减。",
        ("ret", "close"), ("sign", "mul", "ts_rank"),
        ("mul(sign(ts_mean(ret,60)),ts_rank(ret,20))",
         "mul(sign(delta(ema(close,90),30)),ts_zscore(ret,8))")),
    MechanismFamily(
        "intraday_seasonality", "日内周期性",
        "UTC 时段的规律性偏移（仅日内周期有效，日线以上恒为 0）。",
        ("hour_sin", "hour_cos"), ("mul", "add"),
        ("mul(hour_sin,ts_zscore(ret,20))",
         "mul(hour_cos,ts_rank(volume,20))")),
    MechanismFamily(
        "gap_premium", "跨根跳空",
        "相邻两根之间的跳空溢价（永续 7×24 连续交易，本族在本市场先验极弱）。",
        ("overnight",), ("ts_mean", "delta", "ts_zscore"),
        ("sub(ts_mean(overnight,20),delta(amplitude,5))",
         "ts_zscore(ts_sum(overnight,20),90)")),
    MechanismFamily(
        "orderflow_proxy", "主动买卖代理",
        "以实体方向 × 成交量近似主动成交方向。",
        ("body", "volume", "turnover"), ("mul", "ts_mean"),
        ("ts_mean(mul(body,ts_rank(volume,20)),20)",
         "ts_zscore(mul(sign(body),ts_rank(turnover,20)),30)"),
        proxy_note="OKX 公共 K 线不含主动买卖量，本族全部指标均为**代理**，不可当真实单流读。"),
)

MECHANISM_KEYS: tuple[str, ...] = tuple(f.key for f in MECHANISM_FAMILIES)
_BY_KEY: dict[str, MechanismFamily] = {f.key: f for f in MECHANISM_FAMILIES}


def get_family(key: str) -> MechanismFamily | None:
    """按 key 取族定义（未知返回 None）。"""
    return _BY_KEY.get(key)


def _has_band_deviation(node: Node) -> bool:
    """树中是否存在「原始价格字段 − 其平滑值」的通道偏离结构（均值回归的结构签名）。

    只认 ``sub`` 的两个操作数中**恰有一个是价格字段叶子、另一个是平滑算子**的形态；
    「两个平滑值相减」（长短均线差）属趋势结构，不在此列——两者用同一个算子却是相反的
    经济含义，这正是加权打分分不开、必须用结构判据的地方。
    """
    for _path, sub in subtrees(node):
        if sub.op != "sub" or len(sub.args) != 2:
            continue
        kinds = {"leaf": 0, "smooth": 0}
        for arg in sub.args:
            if arg.op == "field" and arg.field in _PRICE_FIELDS:
                kinds["leaf"] += 1
            elif arg.op in _SMOOTH_OPS:
                kinds["smooth"] += 1
        if kinds["leaf"] == 1 and kinds["smooth"] == 1:
            return True
    return False


def classify_mechanism(node: Node) -> str:
    """判定表达式的机制族（唯一真源）；无任何特征命中 → ``unclassified``。

    有序规则链，首个命中即返回（顺序即优先级，越靠前越具辨识度）。顺序本身是承重的：
    例如「均值回归」必须排在「波动率状态」之前，否则 ``div(sub(c,ma),std)`` 会因含
    ``ts_std`` 被判成波动率族。
    """
    fields = set(fields_of(node))
    ops = set(ops_of(node))
    if fields & _SEASON_FIELDS:
        return "intraday_seasonality"
    if "overnight" in fields:
        return "gap_premium"
    if fields & _SHADOW_FIELDS:
        return "shadow_pressure"
    if "body" in fields and (fields & _FLOW_FIELDS):
        return "orderflow_proxy"
    if (fields & _FLOW_FIELDS) and ("abs" in ops or fields & _RANGE_FIELDS):
        return "liquidity_impact"
    if (fields & _FLOW_FIELDS) and (fields & (_PRICE_FIELDS | {"ret"})):
        return "volume_price_divergence"
    if fields & _FLOW_FIELDS:
        return "liquidity_impact"
    if "sign" in ops:
        return "cross_scale_confirm"
    if (ops & _EXTREME_OPS) or ("ts_rank" in ops and fields and fields <= _PRICE_FIELDS):
        return "breakout_structure"
    if fields & _RANGE_FIELDS:
        return "range_compression"
    if _has_band_deviation(node) or (
            "ts_zscore" in ops and (fields & _PRICE_FIELDS) and not (ops & {"delta", "roc"})):
        return "mean_reversion_band"
    if ops & {"ts_std", "ts_skew"}:
        return "volatility_regime"
    if "neg" in ops:
        return "momentum_reversal"
    if ops & _TREND_OPS:
        return "trend_persistence"
    if "ret" in fields:
        return "momentum_reversal"
    return UNCLASSIFIED


def mechanism_cn(key: str) -> str:
    """机制族中文名（未知回落原 key，UI 与 notes 共用）。"""
    family = _BY_KEY.get(key)
    return family.cn if family else key


def empty_coverage() -> dict[str, int]:
    """全零覆盖表（检查点初值 / UI 热力图基线；13 族一个不少，绝不按需惰性建键）。"""
    return {key: 0 for key in MECHANISM_KEYS}


def uncovered_families(coverage: dict[str, int]) -> list[str]:
    """当前 0 覆盖的机制族（LLM 机制引导探索优先补这些方向，§2.5）。"""
    return [key for key in MECHANISM_KEYS if int(coverage.get(key, 0)) <= 0]


def mechanism_catalog() -> list[dict]:
    """机制族词表（``GET /discovery/vocabulary`` 与 prompt 同源，§2.11）。"""
    return [{"key": f.key, "cn": f.cn, "hint": f.hint,
             "fields": list(f.fields), "operators": list(f.ops),
             "prototypes": list(f.prototypes), "proxyNote": f.proxy_note}
            for f in MECHANISM_FAMILIES]
