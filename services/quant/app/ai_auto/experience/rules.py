"""经验模式规则 + 「缺省即开启」的**唯一**解析点（§4.1 / §五）。

本仓库已经在 `baseRules` / `profitGuard` / `tradingHorizon` 上踩过三次同一个坑：
**「字段整键缺省」与「显式 `{enabled:false}`」是两件不同的事**。前者意味着「老客户端 / 桌面旧包
没发这个字段」⇒ 按服务端默认装配；后者是用户**亲手取消了勾选** ⇒ 该会话不装配。把两者合成一个
判据，用户就再也关不掉这个功能，而界面上那个复选框看起来是生效的。

解析必须**单点**：实盘启动、冷续跑（`resume.py` 的 S2 用 `start_wire` 原文重跑
`build_start_request`）共用这一条链。两处各写一遍必然漂移，而这里漂移的是「用户到底开没开经验
模式」——不会报错，只会让 Agent 悄悄按一套用户以为已经关掉的经验下单。
"""
from __future__ import annotations

from dataclasses import dataclass

SCOPE_SYMBOL = "symbol"
SCOPE_GLOBAL = "global"


@dataclass(frozen=True)
class ExperienceRule:
    """会话级不可变契约（随 `AiTradeSession` 生命周期）。"""

    enabled: bool = True
    scope: str = SCOPE_SYMBOL      # symbol（只用本标的 + global 经验）| global（放宽跨标的权重）
    write: bool = True             # 是否把本次会话的经验写回资产（回测恒 False，见 §十一.2）

    def wire(self) -> dict:
        """回执镜像（启动回执 / 会话状态共用；未装配一律由调用方发 `None`）。"""
        return {"enabled": self.enabled, "scope": self.scope, "write": self.write}


def resolve_experience(raw: dict | None, settings) -> ExperienceRule | None:
    """「缺省即开启」语义的唯一解析点。

    三层优先级：运维总闸 > 用户显式 > 服务端默认。

    Args:
        raw: 启动请求里的 `experience` 键原文（`None` = 整键缺省）。
        settings: 会话 `Settings`（可能已被纪律预设换成派生副本）。

    Returns:
        装配好的 `ExperienceRule`；总闸关 / 用户显式关闭 ⇒ `None`（该会话不装配 ⇒ payload 无
        `experience` 键 ⇒ `build_decision_prompt` 输出逐字节等于本特性合入前，不变式 I-2）。
    """
    if not getattr(settings, "ai_trade_experience_enabled", False):
        return None                                   # ① 运维总闸：一键回滚
    if raw is None:                                   # ② 整键缺省 ⇒ 按服务端默认
        default_on = bool(getattr(settings, "ai_trade_experience_default_on", True))
        if not default_on:
            return None
        return ExperienceRule(enabled=True, scope=SCOPE_SYMBOL,
                              write=bool(settings.ai_trade_experience_write_enabled))
    if raw.get("enabled") is False:                   # ③ 用户显式关闭
        return None
    scope = str(raw.get("scope") or SCOPE_SYMBOL)
    if scope not in (SCOPE_SYMBOL, SCOPE_GLOBAL):
        scope = SCOPE_SYMBOL
    write = raw.get("write")
    return ExperienceRule(
        enabled=True, scope=scope,
        # 写闸取「用户意愿 ∧ 运维总闸」：只读模式（用经验但不沉淀）是用户的合法选择，
        # 但运维关掉写入时，用户勾了也不写——两者是「与」而不是「或」。
        write=bool(settings.ai_trade_experience_write_enabled
                   and (True if write is None else bool(write))))
