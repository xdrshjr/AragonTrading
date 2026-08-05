"""可挖模板目录与因子成员目录（discovery-strategy-axes §2.4）—— 纯计算，无 IO。

两条承重纪律：
- **BL-6**：``enum`` / ``bool`` 与 ``FROZEN_PARAM_KEYS`` 里的参数一律冻结在模板默认值。凡
  「schema 里是数值、语义上属于数据源选择或成本模型」的参数都必须进冻结清单（D-16）——挖它
  等于让每个候选随机换高周期，取帧集合无界，而且**不报错**。
- **P1-6**：因子成员进目录前必须过 ``is_bar_compatible``。不兼容的因子会让
  ``FactorScoreStrategy.prepare`` 把 ``ValueError`` 吞成「安静空仓」，最终伪装成 S1「样本量不足」。
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from ..engine.mtf import bar_to_seconds
from ..strategies.base import STRATEGY_REGISTRY

LADDER_MAX = 64              # 单参数梯子的格数上限（step=0.005 的参数否则会有几百格）
MIN_MINABLE_PARAMS = 2       # 可挖数值参数少于此数即不值得开一条搜索轴
DEFAULT_HTF_WARMUP_BARS = 60

# BL-6 承重清单：类型是数值、语义是「高周期选择」的参数。``ema_cross_scalper`` 的
# ``htf_trend_filter``(bool) / ``htf_bar``(enum) 由通用规则自动冻结，无需列表。
FROZEN_PARAM_KEYS: dict[str, frozenset[str]] = {
    "trend_pullback_long": frozenset({"htf_seconds"}),
    "trend_pullback_short": frozenset({"htf_seconds"}),
}

# 模板族（FSA 的 skeleton 词表与机制覆盖图共用）。**有序规则链**，首个命中即定——
# 加权打分分不开语义相反的情形（上游 I-5）。
TEMPLATE_FAMILY: tuple[tuple[str, str], ...] = (
    ("trend_pullback_long", "trend"),
    ("trend_pullback_short", "trend"),
    ("trend_band_swing", "trend"),
    ("momentum_breakout", "breakout"),
    ("long_shadow_reversal", "reversal"),
    ("bollinger_reversion", "reversal"),
    ("range_scalping_strategy", "range"),
    ("ema_cross_scalper", "scalp"),
    ("vwap_rsi_momentum", "scalp"),
    ("regime_rotation", "rotation"),
    ("factor_score", "factor_combo"),
)
FAMILY_CN: dict[str, str] = {
    "trend": "趋势跟随", "breakout": "突破", "reversal": "反转", "range": "区间",
    "scalp": "短线", "rotation": "策略轮动", "factor_combo": "因子组合",
}

REASON_NOT_ATOMIC = "组合与因子类模板由专用轴挖掘"
REASON_TOO_FEW_PARAMS = "可搜索参数不足"
REASON_HTF_OVER_CAP = "所需高周期帧数超出上限"


@dataclass(frozen=True)
class ParamSpec:
    """一个可挖参数及其离散梯子（基因 = 该参数在梯子上的下标）。"""

    key: str
    label: str
    kind: str                  # int | number
    default: float
    ladder: tuple


@dataclass(frozen=True)
class TemplateSpec:
    """一个注册表模板在挖掘视角下的画像（``minable=False`` 时 ``reason`` 必须可直接展示）。"""

    key: str
    name: str
    kind: str
    family: str
    minable: bool
    reason: str
    params: tuple[ParamSpec, ...]
    htf_bars: tuple[str, ...]

    def to_dict(self) -> dict:
        return {"key": self.key, "name": self.name, "family": self.family,
                "familyCn": FAMILY_CN.get(self.family, self.family),
                "minable": self.minable, "reason": self.reason,
                "paramCount": len(self.params), "htfBars": list(self.htf_bars)}


@dataclass(frozen=True)
class TemplateCatalog:
    """一次 run 的模板目录 + 该 run 需要取的高周期并集（取帧的唯一依据）。"""

    templates: tuple[TemplateSpec, ...]
    htf_bars: tuple[str, ...]
    htf_warmup_bars: int

    def minable(self) -> tuple[TemplateSpec, ...]:
        return tuple(spec for spec in self.templates if spec.minable)

    def get(self, key: str) -> TemplateSpec | None:
        return next((spec for spec in self.templates if spec.key == key), None)


def template_family(template_key: str) -> str:
    """模板 → 族（有序规则链；未登记模板归 ``other``，绝不猜）。"""
    for key, family in TEMPLATE_FAMILY:
        if key == template_key:
            return family
    return "other"


# ============================ 参数梯子 ============================
def _decimals(step: float) -> int:
    text = f"{step:.10f}".rstrip("0")
    return max(0, len(text.split(".")[1])) if "." in text else 0


def _thin(values: list, cap: int) -> list:
    """等距抽稀到 ``cap`` 格（保留首尾），避免小步长参数产生几百格。"""
    if len(values) <= cap:
        return values
    idx = [round(i * (len(values) - 1) / (cap - 1)) for i in range(cap)]
    return [values[i] for i in sorted(set(idx))]


def build_ladder(schema: dict) -> tuple:
    """把一条 ``params_schema`` 条目离散成梯子 ``[min, min+step, ..., max]``。"""
    kind = str(schema.get("type") or "")
    if kind not in ("int", "number"):
        return ()
    low, high = float(schema.get("min", 0.0)), float(schema.get("max", 0.0))
    if high <= low:
        return ()
    step = float(schema.get("step") or 0.0) or (high - low) / 20.0
    count = int(math.floor((high - low) / step + 1e-9)) + 1
    raw = [low + i * step for i in range(count)]
    if raw[-1] < high - 1e-9:
        raw.append(high)
    if kind == "int":
        values = sorted({int(round(v)) for v in raw})
    else:
        digits = _decimals(step)
        values = sorted({round(v, digits) for v in raw})
    return tuple(_thin(values, LADDER_MAX))


def minable_params(template_key: str, schema: dict) -> tuple[ParamSpec, ...]:
    """模板的可挖参数（``enum`` / ``bool`` / ``FROZEN_PARAM_KEYS`` 一律排除，BL-6）。"""
    frozen = FROZEN_PARAM_KEYS.get(template_key, frozenset())
    out: list[ParamSpec] = []
    for key, entry in (schema or {}).items():
        if key in frozen or not isinstance(entry, dict):
            continue
        ladder = build_ladder(entry)
        if len(ladder) < 2:
            continue
        out.append(ParamSpec(key=key, label=str(entry.get("label") or key),
                             kind=str(entry.get("type")),
                             default=float(entry.get("default", ladder[0])), ladder=ladder))
    return tuple(out)


def frozen_params(template_key: str, cls) -> dict:
    """冻结参数的取值 = 模板默认值（挖掘期与展示期共用一份，避免两处各写一遍）。"""
    frozen = FROZEN_PARAM_KEYS.get(template_key, frozenset())
    defaults = dict(cls.meta.default_params)
    minable = {spec.key for spec in minable_params(template_key, cls.meta.params_schema)}
    return {key: value for key, value in defaults.items()
            if key in frozen or key not in minable}


# ============================ 模板目录 ============================
def _declared_htf(cls, bar: str) -> tuple[str, ...]:
    """冻结参数下该模板声明的、**严格高于**基础周期的 bar（与 agent/services 同源过滤）。"""
    try:
        instance = cls(dict(cls.meta.default_params))
        base = bar_to_seconds(bar)
        return tuple(tf for tf in instance.timeframes()
                     if tf != bar and bar_to_seconds(tf) > base)
    except Exception:  # noqa: BLE001 —— 目录构建绝不因单个模板实例化失败而整体崩掉
        return ()


def _describe(cls, key: str, bar: str) -> tuple[TemplateSpec, tuple[str, ...]]:
    """单模板画像（不含全局帧数上限裁剪，那一步需要看全体模板）。"""
    params = minable_params(key, cls.meta.params_schema)
    htf = _declared_htf(cls, bar)
    kind = str(getattr(cls.meta, "kind", "atomic"))
    if kind != "atomic":
        return _spec(cls, key, kind, params, htf, False, REASON_NOT_ATOMIC), htf
    if len(params) < MIN_MINABLE_PARAMS:
        return _spec(cls, key, kind, params, htf, False, REASON_TOO_FEW_PARAMS), htf
    return _spec(cls, key, kind, params, htf, True, ""), htf


def _spec(cls, key: str, kind: str, params, htf, minable: bool, reason: str) -> TemplateSpec:
    return TemplateSpec(key=key, name=str(cls.meta.name), kind=kind,
                        family=template_family(key), minable=minable, reason=reason,
                        params=params, htf_bars=htf)


def _htf_union(specs: list[TemplateSpec], max_frames: int) -> tuple[tuple[str, ...], set[str]]:
    """按「需要该帧的模板数」降序保留 ``max_frames`` 个高周期，返回 (保留集, 被挤掉的帧)。"""
    demand: dict[str, int] = {}
    for spec in specs:
        for tf in spec.htf_bars:
            demand[tf] = demand.get(tf, 0) + 1
    ordered = sorted(demand, key=lambda tf: (-demand[tf], bar_to_seconds(tf)))
    kept = ordered[: max(0, int(max_frames))]
    return tuple(sorted(kept, key=bar_to_seconds)), set(ordered) - set(kept)


def minable_templates(bar: str, *, max_htf_frames: int = 3) -> TemplateCatalog:
    """遍历注册表构建可挖模板目录 + 本 run 的高周期取帧并集（§2.4.1）。

    Args:
        bar: 基础周期。
        max_htf_frames: 一个 run 允许取的高周期帧数上限（BL-6 的资源侧护栏）。

    Returns:
        ``TemplateCatalog``；被帧数上限挤掉的模板**整体退出可挖集合并写明原因**——
        绝不让一个模板在缺帧状态下被回测。
    """
    described = [_describe(cls, key, bar)[0] for key, cls in STRATEGY_REGISTRY.items()]
    kept_frames, dropped = _htf_union([s for s in described if s.minable], max_htf_frames)
    out: list[TemplateSpec] = []
    for spec in described:
        if spec.minable and dropped.intersection(spec.htf_bars):
            spec = TemplateSpec(**{**spec.__dict__, "minable": False,
                                   "reason": REASON_HTF_OVER_CAP})
        out.append(spec)
    return TemplateCatalog(templates=tuple(out), htf_bars=kept_frames,
                           htf_warmup_bars=_max_htf_warmup(out))


def _max_htf_warmup(specs: list[TemplateSpec]) -> int:
    """全部入选模板的 ``htf_warmup_bars`` **最大值**（评审 P2-6，与 agent/services 同源）。

    取小了会让最靠前的窗口在 ``start_ts`` 处 ``merge_asof(backward)`` 拿到 NaN——又是一个
    不报错、只是高周期特征整段失效的坑。
    """
    values = [DEFAULT_HTF_WARMUP_BARS]
    for spec in specs:
        cls = STRATEGY_REGISTRY.get(spec.key)
        if cls is None or not spec.minable or not spec.htf_bars:
            continue
        try:
            values.append(int(cls(dict(cls.meta.default_params)).htf_warmup_bars()))
        except Exception:  # noqa: BLE001
            continue
    return max(values)


# ============================ 因子成员目录（§2.4.2） ============================
@dataclass(frozen=True)
class MemberCatalog:
    """因子组合轴的成员目录 + 截断留痕（截断量必须进迭代记录，绝不静默）。"""

    members: tuple
    truncated: int = 0
    content_hash: str = ""


def _expr_member(expr_text: str, direction: str):
    from .expression import expr_hash, parse_expression
    from .genomes import ComboMemberRef

    name = f"expr:{expr_hash(parse_expression(expr_text))[:8]}"
    return ComboMemberRef(factor_key="expr", params={"expr": expr_text},
                          direction=direction if direction in ("long", "short") else "long",
                          name=name)


def _builtin_members(ctx, horizon: int):
    """库内可见内置因子；方向**只在 IS 段**一次性冻结（与上游 §2.7.0 同一纪律）。

    入目录前必须先过 ``is_bar_compatible``（评审 P1-6）：与当前周期不兼容的因子会在
    ``score_breakdown`` 里抛 ``ValueError``，而 ``FactorScoreStrategy.prepare`` 把它**吞成
    安静空仓**，最终伪装成 S1「样本量不足」。
    """
    from ..engine.factors import (
        available_factors,
        compute_factor,
        is_bar_compatible,
        resolve_direction,
    )
    from .genomes import ComboMemberRef

    df_is = ctx.df_full.iloc[:ctx.split].reset_index(drop=True)
    close = df_is["c"].to_numpy(dtype=float)
    out = []
    for meta in available_factors():
        key = str(meta["key"])
        if not is_bar_compatible(key, ctx.bar):
            continue
        try:
            values = compute_factor(df_is, key, {}, bar=ctx.bar)
            direction = resolve_direction(values.to_numpy(dtype=float), close, "auto",
                                          primary_horizon=int(horizon))
        except Exception:  # noqa: BLE001 —— 求值失败 / 方向不可判定的因子直接不进目录
            continue
        if direction not in ("long", "short"):
            continue
        out.append(ComboMemberRef(factor_key=key, params={}, direction=direction, name=key))
    return out


def library_members(ctx, *, mined_exprs: list, include_library: bool) -> tuple:
    """目录里**与本 run 产出无关**的那部分（既有挖掘因子 + 库内内置因子）。

    单独拆出来是因为它贵（内置因子要在 IS 段逐个求值定号）且只在数据刷新后才需要重算；
    本 run 新入库的因子每轮都在变，走 ``compose_members`` 便宜地拼上去即可。
    """
    if not include_library:
        return ()
    horizon = int(ctx.config.get("horizon", 6))
    return tuple([_expr_member(text, direction) for text, direction in mined_exprs]
                 + _builtin_members(ctx, horizon))


def compose_members(kept_exprs: list, library: tuple, max_size: int) -> MemberCatalog:
    """本 run kept → 既有挖掘因子 → 库内内置，按序去重后截断（截断量如实返回，绝不静默）。"""
    import hashlib

    from .genomes import canonical_json

    ordered = [_expr_member(text, direction) for text, direction in kept_exprs]
    ordered += list(library)
    seen: set[str] = set()
    unique = []
    for member in ordered:
        key = f"{member.factor_key}|{canonical_json(member.params)}"
        if key in seen:
            continue
        seen.add(key)
        unique.append(member)
    truncated = max(0, len(unique) - int(max_size))
    kept = unique[: max(0, int(max_size))]
    digest = hashlib.sha256(
        canonical_json([m.payload() for m in kept]).encode("utf-8")).hexdigest()[:16]
    return MemberCatalog(members=tuple(kept), truncated=truncated, content_hash=digest)


@dataclass
class SearchInputs:
    """三条策略轴搜索器共享的只读输入（每轮由 Lane 装配一次）。"""

    templates: tuple = ()
    members: tuple = ()
    combo_size: tuple = (2, 5)
    rotation_size: tuple = (2, 4)
    rotation_pool: tuple = ()      # 上游 strategy_param 轴 kept 池的 ParamGenome
    degraded: dict = None          # 交叉降级等留痕计数（就地累加，Lane 读走）

    def __post_init__(self) -> None:
        if self.degraded is None:
            self.degraded = {}
