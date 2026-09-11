"""后训练轨迹采集的会话级约定与唯一解析点（ai-trade-trajectory-capture §2.10.2）。

**整键缺省 ⇒ 不采集**：与 `intel` 同向，与 `baseRules` / `profitGuard` 那些「缺省即开启」的字段
方向相反——采集是一份写进用户磁盘的数据，用户没有明确要求时不该悄悄发生。

本模块是纯函数、零 IO、绝不抛。目录可用性由端点 `_require_trajectory_directory` 探测，**绝不在
解析链里校验**：冷续跑用 `start_wire` 重跑同一条 `build_start_request`，任何 `HTTPException` 都会
把续跑翻成 409（`_require_profit_exit_with_horizon` 的成文理由）。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

SOURCE_REQUEST = "request"
SOURCE_NOT_REQUESTED = "not_requested"
SOURCE_OPS_OFF = "ops_off"
SOURCE_RESOLVE_FAILED = "resolve_failed"


@dataclass(frozen=True, slots=True)
class TrajectoryRule:
    """一次启动的采集约定。`directory=None` ⇒ 用服务端默认根目录（默认值随部署变，前端不复制）。"""

    enabled: bool = False
    directory: str | None = None


def resolve_trajectory_rule(raw: dict | None, settings) -> tuple[TrajectoryRule, str]:
    """把启动请求里的 `trajectoryCapture` 解析成规则与回执码。

    顺序即优先级：未请求 → 运维关 → 用户关 → 开。

    Args:
        raw: `AiTradeTrajectoryIn.model_dump()`；整键缺省时为 `None`。
        settings: 启动请求那条派生链上的 `Settings`（只读 `ai_trade_trajectory_enabled`）。

    Returns:
        `(规则, source)`，`source ∈ {request, not_requested, ops_off, resolve_failed}`。它进
        `group_status().trajectoryCapture`，是「服务端替我决定了什么」的显式回执。

    Note:
        任何异常都回退成**关闭**并回执 `resolve_failed`（方向是关闭，不是开启）：采集层绝不
        阻挡一次真实资金启动，也不在端点外层再包一层 `except`。
    """
    try:
        if raw is None:
            return TrajectoryRule(), SOURCE_NOT_REQUESTED
        if not settings.ai_trade_trajectory_enabled:
            return TrajectoryRule(), SOURCE_OPS_OFF
        if not raw.get("enabled"):
            return TrajectoryRule(), SOURCE_REQUEST
        directory = str(raw.get("directory") or "").strip() or None
        return TrajectoryRule(enabled=True, directory=directory), SOURCE_REQUEST
    except Exception as exc:  # noqa: BLE001 —— 见 Note：解析失败一律落到「不采集」
        logger.warning("ai-trade trajectory rule resolve failed: %s", exc)
        return TrajectoryRule(), SOURCE_RESOLVE_FAILED
