"""一次 AI 自动交易会话对情报层的采信约定（ai-trade-intel-session-binding 轴 A 的纯核心）。

**纯核心：零 IO、零 DB、零时钟**；`resolve_session_rule` 是这条链上**唯一**读 `Settings` 的地方，
而它读的也只有轴 A 的那一把总闸。

两张值域表**刻意分开**（评审 P1-5）。混成一张的后果是一份被手改过的 `start_wire`
（``{"enabled": true, "mode": "off"}``）会解析成「`inject_on` 为真、`intel_mode` 为 ``"off"``」，
于是 `build_snapshot` 拿着一个没有任何 renderer 认识的档去选渲染器——而这不会报错。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# 请求体与冷续跑兜底的**唯一**值域。
REQUESTABLE_MODES: tuple[str, ...] = ("index", "full")
# 派生态的全值域（``"off"`` 只由 `enabled=False` 生，永远不会成为一个 `mode` 值）。
INTEL_MODES: tuple[str, ...] = ("off", *REQUESTABLE_MODES)


@dataclass(frozen=True)
class IntelSessionRule:
    """一次会话对情报层的采信约定（**与 Settings 无关，重装时原样带过去**）。

    默认值 ``enabled=True, mode="full"`` 是**给既有构造点用的**：`IntelDeps` 上带默认值的字段
    让 30+ 个直接构造 deps 的离线用例一个字节不改，且行为逐字回到 `4bf512d`。生产路径**永远**
    显式传一个由 `resolve_session_rule` 解析出来的规则，绝不吃这个默认值。
    """

    enabled: bool = True
    mode: str = "full"

    def __post_init__(self) -> None:
        # ★ 评审 P1-5：`mode` 只能取 `REQUESTABLE_MODES`。``"off"`` 是一个**派生态**，
        # 由 `enabled=False` 唯一产生；把它也塞进 `mode` 会造出「开着但档位是关」这种没有
        # 任何 renderer 认识、且不报错的第三态。
        if self.mode not in REQUESTABLE_MODES:
            raise ValueError(
                f"IntelSessionRule.mode 必须 ∈ {REQUESTABLE_MODES}，收到 {self.mode!r}")

    @property
    def effective_mode(self) -> str:
        """派生档：`off` | `index` | `full`。`build_snapshot` 据它选 renderer。"""
        return self.mode if self.enabled else "off"


def resolve_session_rule(raw: dict | None, settings, *,
                         legacy: bool = False) -> IntelSessionRule:
    """`AiTradeGroupStartIn.intel` → `IntelSessionRule`（**唯一**解析点）。

    Args:
        raw: 请求体里那一段（``{"enabled": bool, "mode": str}``）；``None`` / 非 dict ⇒ 关闭。
        settings: 启动请求那条派生链上的 `Settings`（**不是**情报库内行派生副本——轴 A 的总闸
            必须在启动请求解析时就生效，那一刻还没有 runtime、也拿不到情报派生副本；见
            `api/ai_auto._resolve_intel` 的 docstring）。
        legacy: 本次启动请求来自**本特性合入前**落库的 `start_wire`。

    语义：

    - 总闸 ``ai_trade_intel_session_opt_in_enabled=False`` ⇒ 恒返回 `IntelSessionRule()`
      （= 逐字节回退：由进程级 `ai_trade_intel_inject` 独裁）。
    - ``legacy=True`` ⇒ 同上回退（回执 `source="legacy_fallback"` 显式披露，见 spec §3.1 A-4）。
    - ``raw is None``（新客户端没勾 / 老客户端不认识这个字段）⇒ `IntelSessionRule(enabled=False)`。
      **这一条是需求的明文要求，与仓库既有六个字段「缺省 ⇒ 开启」的惯例方向相反**
      （用例 `G-A1` 反向冻结：照抄既有惯例即判红）。
    - ``raw["mode"]`` 不在 `REQUESTABLE_MODES`（★ 评审 P1-5：**不是** `INTEL_MODES`）里 ⇒
      收口成 ``"index"``，**不抛**（入口 `Literal` 已经拦过一次，这里是给冷续跑读回来的坏值
      兜底，与 `settings_bridge` 的三处同形收口一致）。

    Returns:
        规则对象。**本函数自身不抛任何异常**（值域已收口、`raw` 非 dict 时按 ``None`` 处理）。
    """
    if not bool(getattr(settings, "ai_trade_intel_session_opt_in_enabled", True)):
        return IntelSessionRule()
    if legacy:
        return IntelSessionRule()
    if not isinstance(raw, dict):
        return IntelSessionRule(enabled=False)
    mode = str(raw.get("mode") or "index").strip().lower()
    if mode not in REQUESTABLE_MODES:
        logger.warning("ai-trade intel: 会话档位 %r 不在值域内，收口成 index", mode)
        mode = "index"
    return IntelSessionRule(enabled=bool(raw.get("enabled")), mode=mode)
