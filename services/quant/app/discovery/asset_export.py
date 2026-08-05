"""台账的对外表示：列表项 / 详情 / 体检行 / 自包含导出快照（discovery-asset-ledger §2.9）。

导出与 REST 用**同一段字段提取**，因此不会出现「界面上看到的数字和导出的数字不一样」——
两份提取迟早漂移，而漂移了没人看得出来（这是本轮反复援引的估计量同源律在展示层的版本）。

**AL-10**：导出必须能在另一台机器上复现每一个数字，因此含资产键、genome / 表达式、冻结方向、
挖掘窗口口径、登记时的完整指标与阈值、体检序列、当前状态与 ``schemaVersion``；
**绝不**含任何密钥 / 账户 / 本机路径（``test_discovery_asset_export.py`` 对整份 JSON 文本
做黑名单正则守门）。
"""
from __future__ import annotations

import time

from sqlalchemy.ext.asyncio import AsyncSession

from . import asset_repositories as asset_repo
from .mechanisms import mechanism_cn

SCHEMA_VERSION = 1
GENERATOR = "aragon-discovery-asset-ledger/1"
EXPORT_HARD_LIMIT = 500


def _loads(raw: str | None, fallback):
    return asset_repo.loads(raw, fallback)


def health_of(row) -> dict:
    return {"status": str(row.health_status or "fresh"), "retention": row.retention,
            "consecutiveFail": int(row.consecutive_fail or 0),
            "lastCheckTs": int(row.last_check_ts or 0),
            "nextCheckTs": int(row.next_check_ts or 0),
            "reasons": [code for code in str(row.last_reasons or "").split(",") if code],
            "checksCount": int(row.checks_count or 0)}


def promoted_of(row) -> dict | None:
    if not row.promoted_id:
        return None
    return {"kind": str(row.promoted_kind or ""), "entityId": int(row.promoted_id),
            "companionStrategyId": (int(row.companion_strategy_id)
                                    if row.companion_strategy_id else None)}


def asset_item(row) -> dict:
    """列表项（§4.1）。"""
    return {
        "id": int(row.id), "assetKey": str(row.asset_key), "kind": str(row.kind),
        "label": str(row.label or ""), "instId": str(row.inst_id or ""),
        "bar": str(row.bar or ""), "horizon": int(row.horizon or 0),
        "direction": str(row.direction or ""), "exprText": str(row.expr_text or ""),
        "describe": str(row.describe_text or ""), "mechanism": str(row.mechanism or ""),
        "mechanismCn": mechanism_cn(str(row.mechanism or "")) if row.mechanism else "",
        "firstFoundTs": int(row.first_found_ts or 0),
        "lastFoundTs": int(row.last_found_ts or 0),
        "sourceCount": int(row.source_count or 0),
        "variantCount": int(row.variant_count or 1), "backfilled": bool(row.backfilled),
        "acceptMetrics": _loads(row.accept_metrics_json, {}) or {},
        "promoted": promoted_of(row), "health": health_of(row),
        "archived": bool(row.archived), "note": str(row.note or "")}


def check_item(row) -> dict:
    """一条体检记录（§4.3）。"""
    return {
        "id": int(row.id), "checkedTs": int(row.checked_ts or 0),
        "status": str(row.status or ""),
        "reasons": [code for code in str(row.reasons or "").split(",") if code],
        "retention": row.retention, "clipMode": str(row.clip_mode or ""),
        "error": str(row.error or ""), "windowStartTs": int(row.window_start_ts or 0),
        "windowEndTs": int(row.window_end_ts or 0),
        "freshStartTs": int(row.fresh_start_ts or 0), "freshBars": int(row.fresh_bars or 0),
        "warmupBars": int(row.warmup_bars or 0),
        "metrics": _loads(row.metrics_json, {}) or {}}


def sources_of(row, existing_runs: set[int]) -> list[dict]:
    """溯源列表；``runExists`` **由服务端算**（评审 P2-4）。

    前端**不得**自己再拉一次 ``GET /discovery/runs`` 做比对：两套判据迟早不一致，而且分页后的
    run 列表根本不全，那样删掉的 run 反而会显示成还在。
    """
    out = []
    for item in (_loads(row.sources_json, []) or []):
        run_id = int(item.get("runId") or 0)
        out.append({**item, "runExists": run_id in existing_runs})
    return out


async def asset_detail(session: AsyncSession, row, *, checks_limit: int = 50) -> dict:
    """详情（§4.2）：列表项 + genome / 窗口 / 阈值 / 溯源（含 ``runExists``）/ 体检序列。"""
    sources_raw = _loads(row.sources_json, []) or []
    existing = await asset_repo.existing_run_ids(
        session, [int(item.get("runId") or 0) for item in sources_raw])
    checks, total = await asset_repo.list_checks(session, int(row.id), limit=checks_limit)
    return {**asset_item(row), "genome": _loads(row.genome_json, {}) or {},
            "minedWindow": _loads(row.mined_window_json, {}) or {},
            "acceptThresholds": _loads(row.accept_thresholds_json, {}) or {},
            "trialsAtAccept": int(row.trials_at_accept or 0),
            "sources": sources_of(row, existing),
            "checks": [check_item(check) for check in checks], "checksTotal": total,
            "checksPruned": int(row.checks_pruned or 0)}


async def build_snapshot(session: AsyncSession, rows: list, *,
                         matched: int | None = None) -> dict:
    """自包含可回溯快照（AL-10）。

    ``matched`` 非空表示这是**筛选式**导出且命中数超过了硬上限——响应里必须带
    ``truncated`` / ``matched``，**绝不静默截断**（§4.8）。
    """
    assets_payload = []
    for row in rows:
        existing = await asset_repo.existing_run_ids(
            session, [int(item.get("runId") or 0)
                      for item in (_loads(row.sources_json, []) or [])])
        checks, _total = await asset_repo.list_checks(session, int(row.id), limit=200)
        item = asset_item(row)
        assets_payload.append({
            "assetKey": item["assetKey"], "kind": item["kind"], "label": item["label"],
            "instId": item["instId"], "bar": item["bar"], "horizon": item["horizon"],
            "direction": item["direction"], "exprText": item["exprText"],
            "genome": _loads(row.genome_json, {}) or {}, "describe": item["describe"],
            "mechanism": item["mechanism"],
            "minedWindow": _loads(row.mined_window_json, {}) or {},
            "acceptMetrics": item["acceptMetrics"],
            "acceptThresholds": _loads(row.accept_thresholds_json, {}) or {},
            "trialsAtAccept": int(row.trials_at_accept or 0),
            "sources": sources_of(row, existing), "sourceCount": item["sourceCount"],
            "variantCount": item["variantCount"], "backfilled": item["backfilled"],
            "promoted": item["promoted"], "health": item["health"],
            "checks": [check_item(check) for check in checks]})
    payload = {"schemaVersion": SCHEMA_VERSION, "exportedTs": int(time.time()),
               "generator": GENERATOR, "assets": assets_payload}
    if matched is not None:
        payload["truncated"] = matched > len(rows)
        payload["matched"] = int(matched)
    return payload


def filename_of(now: int | None = None) -> str:
    return f"aragon-discovery-assets-{int(now if now is not None else time.time())}.json"
