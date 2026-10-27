"""路由：AI 自动交易 · 实证准入（ai-trade-evidence-gated-capital §5.3）。

两个端点，经 `api/ai_auto.router.include_router` 挂在 `/api` 下：

- `GET /ai-trade/evidence`：启动向导里「当前证据」预览。规则取**当前 settings 默认**（用户还没启动，
  没有会话规则）；`policy` 查询参数只影响 `gating` 派生，不影响统计。**不守运维总闸**（只读历史表）。
- `POST /ai-trade/evidence/backfill`：从已有真实开仓腿回放出证据。服务端 240 s 截止，回执带
  `truncated` / `remaining`，唯一约束保证再点一次可以接着做。前端须把本路径登记为长请求。
"""
from __future__ import annotations

import time
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from ..ai_auto.evidence_gate import rule_from_settings
from ..ai_auto.evidence_service import EvidenceFetchError, backfill, evidence_view, with_policy
from ..ai_auto.shadow_ledger import fallback_cost_bps
from ..config import Settings, get_settings
from ..db.session import get_session
from ..schemas import (
    AiTradeEvidenceBackfillIn,
    AiTradeEvidenceBackfillOut,
    AiTradeEvidenceOut,
)

router = APIRouter(tags=["ai-trade-evidence"])

_MAX_INSTS = 20


def _inst_list(raw: str) -> list[str]:
    insts = [part.strip() for part in raw.split(",") if part.strip()]
    if not insts or len(insts) > _MAX_INSTS:
        raise HTTPException(422, detail={
            "code": "AI_TRADE_EVIDENCE_INSTS_INVALID",
            "message": f"instIds 须为 1–{_MAX_INSTS} 个以逗号分隔的标的（收到 {len(insts)} 个）"})
    return insts


@router.get("/ai-trade/evidence", response_model=AiTradeEvidenceOut)
async def ai_trade_evidence(
        instIds: str = Query(..., description="逗号分隔的 instId"),
        bar: str = Query(...),
        policy: Literal["strict", "balanced", "observe"] | None = Query(default=None),
        session: AsyncSession = Depends(get_session),
        settings: Settings = Depends(get_settings)) -> dict:
    """按「标的 × 方向」汇总已结束的影子样本与三态；空库 ⇒ `buckets=[]`。"""
    rule = with_policy(rule_from_settings(settings), policy)
    return await evidence_view(session, rule=rule, inst_ids=_inst_list(instIds), bar=bar,
                               now=time.time())


@router.post("/ai-trade/evidence/backfill", response_model=AiTradeEvidenceBackfillOut)
async def ai_trade_evidence_backfill(
        body: AiTradeEvidenceBackfillIn,
        session: AsyncSession = Depends(get_session),
        settings: Settings = Depends(get_settings)) -> dict:
    """用历史成交回填证据；公共读整体失败 ⇒ 502，部分失败计入 `candlesMissing`。"""
    cap = int(settings.ai_trade_evidence_backfill_max_days)
    if body.days > cap:
        raise HTTPException(422, detail={
            "code": "AI_TRADE_EVIDENCE_BACKFILL_DAYS",
            "message": f"回填天数最多 {cap} 天（收到 {body.days}）"})
    try:
        return await backfill(session, rule=rule_from_settings(settings),
                              inst_ids=body.instIds or None, bar=body.bar, days=body.days,
                              fallback_cost_bps=fallback_cost_bps(settings))
    except EvidenceFetchError as exc:
        raise HTTPException(502, detail={"code": "AI_TRADE_EVIDENCE_BACKFILL_FETCH_FAILED",
                                         "message": str(exc)}) from exc
