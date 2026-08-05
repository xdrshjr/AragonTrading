"""导出快照的自包含性与敏感字段黑名单（discovery-asset-ledger §2.9 · AL-10 / §7.3-12）。

导出成 UI 表格的转储 ⇒ 换台机器打开发现无法复现任何一个数字，「回溯」名不副实。
判据因此是两条：① 用导出的字段能在另一个进程里**复算出同一个 ``identity_key``**；
② 对整份 JSON 文本做黑名单正则，绝不含密钥 / 账户 / 本机路径。
"""
from __future__ import annotations

import json
import re

from sqlalchemy import delete

from app.db.models import DiscoveryAsset, DiscoveryAssetCheck
from app.db.session import dispose_engine, get_sessionmaker, init_db
from app.discovery import asset_export
from app.discovery import asset_repositories as asset_repo
from app.discovery import assets

TS0 = 1_700_000_000
EXPR = "sub(ts_mean(overnight,20),delta(amplitude,5))"
FORBIDDEN = re.compile(
    r"apikey|api_key|secret|passphrase|accountid|account_id|[A-Za-z]:\\\\|/home/|/Users/",
    re.IGNORECASE)


async def _fresh():
    await init_db()
    sm = get_sessionmaker()
    async with sm() as session:
        await session.execute(delete(DiscoveryAssetCheck))
        await session.execute(delete(DiscoveryAsset))
        await session.commit()
    return sm


async def _seed(session) -> DiscoveryAsset:
    row = await asset_repo.create_asset(session, asset_repo.AssetValues(
        asset_key=assets.identity_key("factor", expr_text=EXPR), kind="factor",
        inst_id="BTC-USDT-SWAP", bar="15m", horizon=6, direction="long", expr_text=EXPR,
        genome={}, describe_text="跳空溢价背离", mechanism="gap_premium",
        label="挖掘因子 abcd1234：跳空溢价背离", found_ts=TS0,
        source={"runId": 3, "candidateId": 91, "iterationIdx": 47, "foundTs": TS0,
                "source": "mutate", "genomeHash": "7f3a",
                "acceptSummary": {"isRankIc": 0.044, "isIr": 0.62, "trials": 812}},
        mined_window={"startTs": TS0 - 4000 * 900, "endTs": TS0, "isRatio": 0.7,
                      "windowBars": 4000, "splitTs": TS0 - 1200 * 900},
        accept_metrics={"is": {"rankIc": 0.044, "ir": 0.62}, "oos": {"rankIc": 0.03}},
        accept_thresholds={"minAbsIc": 0.03, "minAbsIr": 0.5}, trials_at_accept=812,
        next_check_ts=TS0 + 100))
    await asset_repo.write_check(
        session,
        asset_repo.CheckValues(asset_id=int(row.id), checked_ts=TS0 + 900,
                               window_start_ts=TS0 - 1500 * 900, window_end_ts=TS0 + 900,
                               fresh_start_ts=TS0, fresh_bars=288, warmup_bars=400,
                               status="watch", reasons=["H5"], retention=0.61,
                               metrics={"fresh": {"rankIc": 0.021},
                                        "history": {"rankIc": 0.044}}),
        health={"status": "watch", "retention": 0.61, "consecutiveFail": 1,
                "nextCheckTs": TS0 + 5000}, max_rows=200)
    return row


async def test_snapshot_is_self_contained_and_reproducible() -> None:
    sm = await _fresh()
    try:
        async with sm() as session:
            row = await _seed(session)
            payload = await asset_export.build_snapshot(session, [row])
        assert payload["schemaVersion"] == asset_export.SCHEMA_VERSION
        assert payload["generator"] == asset_export.GENERATOR
        item = payload["assets"][0]
        assert set(item) >= {"assetKey", "kind", "instId", "bar", "horizon", "direction",
                             "exprText", "genome", "minedWindow", "acceptMetrics",
                             "acceptThresholds", "trialsAtAccept", "sources",
                             "sourceCount", "variantCount", "backfilled", "promoted",
                             "health", "checks"}
        # 用导出的字段在「另一个进程」里复算身份键——这是「可回溯」的真正判据
        assert assets.identity_key("factor", expr_text=item["exprText"]) == item["assetKey"]
        assert item["minedWindow"]["splitTs"] == TS0 - 1200 * 900
        assert item["checks"][0]["reasons"] == ["H5"]
        assert item["sources"][0]["acceptSummary"]["trials"] == 812
    finally:
        await dispose_engine()


async def test_snapshot_never_leaks_credentials_or_local_paths() -> None:
    sm = await _fresh()
    try:
        async with sm() as session:
            row = await _seed(session)
            payload = await asset_export.build_snapshot(session, [row])
        text = json.dumps(payload, ensure_ascii=False)
        assert FORBIDDEN.search(text) is None
    finally:
        await dispose_engine()


async def test_filtered_export_reports_truncation_instead_of_failing() -> None:
    """筛选式导出触顶回 ``truncated`` / ``matched``——**绝不静默截断**，也绝不 413。"""
    sm = await _fresh()
    try:
        async with sm() as session:
            row = await _seed(session)
            payload = await asset_export.build_snapshot(session, [row], matched=1200)
        assert payload["truncated"] is True and payload["matched"] == 1200
    finally:
        await dispose_engine()


def test_filename_is_stable_and_timestamped() -> None:
    assert asset_export.filename_of(TS0) == f"aragon-discovery-assets-{TS0}.json"
