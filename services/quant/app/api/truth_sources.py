"""路由：四行「真值源」只读端点（ai-trade-silent-degradation-repair 轴 E · E-1）。

两条纪律与 `api/okx_link.py` **逐字同源**（那份文件的模块 docstring 是本文件的直接出处）：

- **不声明 ``response_model``**：声明之后加法键会被 FastAPI 静默剔除，本仓已为此栽过两次
  （``edgeSource`` 那回，以及本轮 P0-2 差点在 ``/intel/documents/health`` 上重犯）。
- **永不 5xx**：一个用来观察故障的端点，自己不许在故障时也跟着倒下。

第三条是本轮新加的、也是评审 P1-4 修正过的那一条：**至多一次只读 DB**。v1 写的是「零 IO」，
与它自己的数据来源矛盾——四行里 ``intelReader`` 的真源 ``repo.document_health()`` 就是一次
数据库读。准确的纪律是：**那一次 DB 读失败即该行降级为 ``unknown``，绝不让整个端点 5xx**；
另外三行保持零 IO（全部来自进程内状态），因为 WS 的计数摘要只由它们派生。
"""
from __future__ import annotations

import logging
import time

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from ..ai_auto import get_ai_trade_manager
from ..ai_auto.truth_wire import build_sources
from ..config import Settings, get_settings
from ..db import repositories as repo
from ..db.session import get_session
from ..okx.call_stats import get_call_stats

router = APIRouter(tags=["system"])
logger = logging.getLogger(__name__)


@router.get("/system/truth-sources")
async def read_truth_sources(session: AsyncSession = Depends(get_session),
                             settings: Settings = Depends(get_settings)) -> dict:
    """四行真值源快照（只读、永不 5xx、至多一次只读 DB）。

    每一行的形状：``{key, label, state, reading, reason, since, action}``。
    ``state`` ∈ ``ok | degraded | unknown | off``——**四个取值各有明确语义，不许压成两态**：
    「没在跑所以不知道」（``unknown``）和「在跑但坏了」（``degraded``）是两件事，压成一句话就是
    第 1 轮修过的那种假话。

    ``action`` 为 ``null`` 时前端**不画按钮**——一个点了没反应的按钮比没有按钮更糟；不为
    ``null`` 时它指向的端点 / 锚点必须真的存在（门禁 G-E3 直接比对 ``app.routes``）。
    """
    intel_health: dict | None = None
    try:
        intel_health = await repo.document_health(session)
    except Exception as exc:  # noqa: BLE001 —— 见模块 docstring：那一行降级，端点绝不 5xx
        logger.warning("truth-sources: intel document_health unavailable: %s", exc)
    try:
        manager = get_ai_trade_manager()
        group = manager.group_status()
        sources = build_sources(
            fee_wire=(group or {}).get("feeRates"), running=bool(group),
            intel_health=intel_health,
            call_stats=get_call_stats().wire() or None,
            positions_ttl_s=float(settings.ai_trade_positions_snapshot_ttl_s),
            summary_health=_summary_health(manager))
    except Exception as exc:  # noqa: BLE001 —— **永不 5xx**：一个用来观察故障的端点，
        # 自己不许在故障时也跟着倒下。整段兜住之后仍然回四行——`unknown` 也是一个诚实的答案，
        # 而一个 500 会让用户读成「真值源这个功能坏了」，从而看不到它本来要说的那四件事。
        logger.warning("truth-sources: assembly failed, degrading every row: %s", exc)
        sources = build_sources(
            fee_wire=None, running=False, intel_health=intel_health,
            call_stats=None, positions_ttl_s=0.0, summary_health=None)
    return {"generatedTs": int(time.time()), "sources": sources}


def _summary_health(manager) -> dict | None:
    """取任一成员会话的整理健康读数（分组共享一份 provider，逐成员各读一份只是噪音）。

    **整段 `getattr` 兜底**：测试替身用 `SimpleNamespace` 直接构造分组与会话，新增读点对它们
    必须是加法——不能让一个只读视图把它们打红（本仓库为这条已立过明文）。
    """
    group = getattr(manager, "_group", None)
    for member in getattr(group, "members", None) or []:
        decider = getattr(getattr(member, "session", None), "decider", None)
        wire = getattr(decider, "summary_health_wire", None)
        if callable(wire):
            try:
                return wire()
            except Exception:  # noqa: BLE001 —— 观察端点不许因为一个读数崩掉
                return None
    return None
