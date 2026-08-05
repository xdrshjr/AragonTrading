"""资产身份键与溯源合并（discovery-asset-ledger §2.2 · AL-4 / AL-11 / P0-3 / P1-7）。

**同源门禁**（放行条件）：把 ``promote.strategy_match_key`` 换成返回常量的桩，
``identity_key`` 的输出必须随之改变。这是「一个实现两个调用点」的**行为**判据——比注释和
AST 结构断言都可靠（上游 ``rebuild_kept_pool`` 的单一实现门禁用的是同一招）。
"""
from __future__ import annotations

import pytest
from sqlalchemy import delete

from app.db.models import DiscoveryAsset
from app.db.session import dispose_engine, get_sessionmaker, init_db
from app.discovery import asset_repositories as asset_repo
from app.discovery import assets, promote

TS0 = 1_700_000_000
PARAM_GENOME = {"templateKey": "momentum_breakout", "params": {"lookback": 20}}


def _combo(weights: list[float]) -> dict:
    return {"members": [{"factorKey": "expr", "params": {"expr": "ts_mean(ret,20)"},
                         "direction": "long", "name": "expr:aaaa"},
                        {"factorKey": "expr", "params": {"expr": "ts_std(ret,30)"},
                         "direction": "short", "name": "expr:bbbb"}],
            "weights": weights, "thresholds": {}}


# ============================ AL-4：与 promote 同源 ============================
def test_identity_key_delegates_to_the_promote_match_key(monkeypatch) -> None:
    """桩换掉 ``strategy_match_key`` ⇒ ``identity_key`` 的输出必须跟着变。"""
    before = assets.identity_key("strategy_param", genome=PARAM_GENOME)
    monkeypatch.setattr(promote, "strategy_match_key", lambda *_a, **_k: "STUBBED")
    after = assets.identity_key("strategy_param", genome=PARAM_GENOME)
    assert before != after


def test_every_axis_has_its_own_prefix() -> None:
    keys = {
        "factor": assets.identity_key("factor", expr_text="ts_mean(ret,20)"),
        "strategy_param": assets.identity_key("strategy_param", genome=PARAM_GENOME),
        "factor_combo": assets.identity_key("factor_combo", genome=_combo([0.5, 0.5])),
        "strategy_combo": assets.identity_key("strategy_combo", genome={
            "members": [PARAM_GENOME], "policy": {}}),
    }
    assert [key[0] for key in keys.values()] == ["f", "s", "c", "r"]
    assert all(len(key) <= 48 for key in keys.values())
    assert len(set(keys.values())) == 4


def test_member_order_does_not_change_the_combo_identity() -> None:
    """成员集合是**集合**，顺序不同即同一条资产（与 ``_find_existing_set`` 逐字同源）。"""
    genome = _combo([0.5, 0.5])
    reversed_genome = {**genome, "members": list(reversed(genome["members"])),
                       "weights": [0.5, 0.5]}
    assert (assets.identity_key("factor_combo", genome=genome)
            == assets.identity_key("factor_combo", genome=reversed_genome))


def test_commutative_expressions_share_one_identity() -> None:
    """**P0-3**：``add(a,b)`` 与 ``add(b,a)`` 同键——与 D-27 后的 promote 幂等键一致。"""
    assert (assets.identity_key("factor", expr_text="add(ts_mean(ret,20),delta(volume,5))")
            == assets.identity_key("factor",
                                   expr_text="add(delta(volume,5),ts_mean(ret,20))"))


def test_unknown_axis_is_rejected_loudly() -> None:
    with pytest.raises(ValueError):
        assets.identity_key("bogus_axis", expr_text="ts_mean(ret,20)")


# ============================ AL-11：溯源合并 ============================
def test_sources_keep_the_earliest_one_when_capped() -> None:
    """超 20 条时保留**最早 1 条 + 最近 19 条**：首次发现是溯源里最有价值的一条。"""
    existing = [{"runId": index} for index in range(20)]
    merged = asset_repo.merge_sources(existing, {"runId": 999})
    assert len(merged) == 20
    assert merged[0]["runId"] == 0            # 最早那条绝不能被挤掉
    assert merged[-1]["runId"] == 999


async def _fresh_session():
    await init_db()
    sm = get_sessionmaker()
    async with sm() as session:
        await session.execute(delete(DiscoveryAsset))
        await session.commit()
    return sm


def _values(key: str, **overrides) -> asset_repo.AssetValues:
    base = dict(asset_key=key, kind="factor_combo", inst_id="BTC-USDT-SWAP", bar="15m",
                horizon=0, direction="", expr_text="", genome=_combo([0.5, 0.5]),
                describe_text="两因子组合", mechanism="factor_combo", label="组合",
                found_ts=TS0, source={"runId": 1, "candidateId": 11, "genomeHash": "aaa"},
                mined_window={"endTs": TS0}, accept_metrics={"oos": {"sharpe": 1.0}},
                accept_thresholds={"minSharpe": 0.8}, trials_at_accept=5,
                next_check_ts=TS0 + 100)
    base.update(overrides)
    return asset_repo.AssetValues(**base)


async def test_merge_keeps_the_first_snapshot_and_counts_variants() -> None:
    """**P1-7**：同成员不同权重 ⇒ 一行、``variant_count == 2``、指标快照保持首次那份。

    身份键不含 ``weight``（与 promote 同源的必然结果），被合并掉的变体只有在溯源的
    ``genomeHash`` + ``acceptSummary`` 里才留得下痕迹。
    """
    sm = await _fresh_session()
    try:
        key = assets.identity_key("factor_combo", genome=_combo([0.5, 0.5]))
        assert key == assets.identity_key("factor_combo", genome=_combo([0.8, 0.2]))
        async with sm() as session:
            row = await asset_repo.create_asset(session, _values(key))
            await asset_repo.merge_source(
                session, row, found_ts=TS0 + 500,
                source={"runId": 2, "candidateId": 22, "genomeHash": "bbb",
                        "acceptSummary": {"oosSharpe": 0.9}})
            async with sm() as check:
                rows, total = await asset_repo.list_assets(check, asset_repo.AssetQuery())
        assert total == 1
        merged = rows[0]
        assert merged.source_count == 2
        assert merged.variant_count == 2
        assert merged.first_found_ts == TS0 and merged.last_found_ts == TS0 + 500
        # 判级分母绝不被二次登记覆盖（换了分母，健康时间线会出现与市场无关的断崖）
        assert asset_repo.loads(merged.accept_metrics_json, {})["oos"]["sharpe"] == 1.0
        hashes = {item["genomeHash"]
                  for item in asset_repo.loads(merged.sources_json, [])}
        assert hashes == {"aaa", "bbb"}
    finally:
        await dispose_engine()
