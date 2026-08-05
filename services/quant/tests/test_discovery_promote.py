"""候选入库测试（strategy-factor-discovery §7.1 / §2.12）：落库形状、幂等、
``member_names`` 互异、入库后既有 ``factor_score`` 链路可跑通、安全边界。"""
from __future__ import annotations

import json

import pytest
from fastapi import HTTPException
from sqlalchemy import delete, select

from app.db.models import DiscoveryCandidate, DiscoveryIteration, DiscoveryRun, Factor
from app.db.session import dispose_engine, get_sessionmaker, init_db
from app.discovery import promote
from app.discovery import repositories as repo
from app.engine.factors import member_names
from app.factors.specs import validate_members

_TS = 1_700_000_000
_EXPR = "sub(ts_mean(overnight,20),delta(amplitude,5))"


async def _fresh():
    await init_db()
    sm = get_sessionmaker()
    async with sm() as session:
        await session.execute(delete(DiscoveryCandidate))
        await session.execute(delete(DiscoveryIteration))
        await session.execute(delete(DiscoveryRun))
        await session.execute(delete(Factor).where(Factor.source == "mined"))
        await session.commit()
    return sm


async def _seed_candidate(sm, *, expr: str = _EXPR, verdict: str = "kept") -> tuple[int, int]:
    async with sm() as session:
        run = await repo.create_run(session, values=repo.RunValues(
            label="x", inst_id="BTC-USDT-SWAP", bar="1H", symbols=["BTC-USDT-SWAP"],
            config={}, checkpoint={}, started_ts=_TS))
        await repo.commit_iteration(
            session, run.id,
            candidates=[repo.CandidateRow(
                kind="factor", genome_hash="h1", genome={"expr": expr}, expr_text=expr,
                describe_text="20 根跨根跳空均值 减去 5 根振幅差分",
                skeleton="sub(ts_mean(?,_),delta(?,_))", mechanism="gap_premium",
                source="random", verdict=verdict,
                metrics={"direction": "short", "trials": 7}, series={}, trials_at_accept=7,
                created_ts=_TS)],
            iteration=repo.IterationRow(idx=1, started_ts=_TS, finished_ts=_TS, budget={},
                                        stats={}, llm={}),
            checkpoint_json="{}", counters={"tested": 1, "kept": 1},
            candidate_row_cap=100, iteration_row_cap=100)
        row = (await repo.list_candidates(session, run.id, verdict=None))[0][0]
    return int(run.id), int(row.id)


# ============================ 落库形状 ============================
async def test_promote_writes_a_factor_row_with_bounded_fields():
    sm = await _fresh()
    try:
        _run_id, candidate_id = await _seed_candidate(sm)
        async with sm() as session:
            result = await promote.promote_candidate(session, candidate_id)
            entity_id = result["promoted"]["entityId"]
            factor = await session.get(Factor, entity_id)
        assert factor.factor_key == "expr"
        assert json.loads(factor.params_json) == {"expr": _EXPR}
        assert factor.source == "mined"
        assert factor.direction == "short"          # 取自 IS 段冻结的方向
        # 名字必须落在 String(128) 内且带可溯源的 hash8。
        assert len(factor.name) <= 128 and factor.name.startswith("挖掘因子 ")
        # category 恒为 discovered：机制族绝不污染既有类别词表（评审 P1-5）。
        assert factor.category == "discovered"
        assert "跨根跳空" in factor.notes and "discovery #" in factor.notes
    finally:
        await dispose_engine()


def test_factor_name_is_always_within_the_column_contract():
    """``Factor.name`` 是 ``String(128)``，而中文渲染上限 512——必须在此收口截断。"""
    long_describe = "很长的中文渲染" * 60
    name = promote.build_factor_name("deadbeef", long_describe)
    assert len(name) <= promote.FACTOR_NAME_MAX
    assert name.startswith("挖掘因子 deadbeef：")


# ============================ 幂等 ============================
async def test_promote_is_idempotent():
    sm = await _fresh()
    try:
        _run_id, candidate_id = await _seed_candidate(sm)
        async with sm() as session:
            first = await promote.promote_candidate(session, candidate_id)
            second = await promote.promote_candidate(session, candidate_id)
        assert first["promoted"]["entityId"] == second["promoted"]["entityId"]
        assert first["promoted"]["reused"] is False
        assert second["promoted"]["reused"] is True
        async with sm() as session:
            rows = (await session.execute(
                select(Factor).where(Factor.source == "mined"))).scalars().all()
        assert len(rows) == 1        # 绝不重复建行
    finally:
        await dispose_engine()


async def test_same_expression_from_another_run_reuses_the_factor():
    sm = await _fresh()
    try:
        _r1, first_id = await _seed_candidate(sm)
        _r2, second_id = await _seed_candidate(sm)
        async with sm() as session:
            a = await promote.promote_candidate(session, first_id)
            b = await promote.promote_candidate(session, second_id)
        assert a["promoted"]["entityId"] == b["promoted"]["entityId"]
    finally:
        await dispose_engine()


# ============================ 守门 ============================
async def test_rejected_candidate_cannot_be_promoted():
    sm = await _fresh()
    try:
        _run_id, candidate_id = await _seed_candidate(sm, verdict="rejected")
        async with sm() as session:
            with pytest.raises(HTTPException) as exc:
                await promote.promote_candidate(session, candidate_id)
        assert exc.value.status_code == 409
        assert exc.value.detail["code"] == "DISCOVERY_CANDIDATE_NOT_KEPT"
    finally:
        await dispose_engine()


async def test_missing_candidate_returns_404():
    sm = await _fresh()
    try:
        async with sm() as session:
            with pytest.raises(HTTPException) as exc:
                await promote.promote_candidate(session, 9999)
        assert exc.value.status_code == 404
    finally:
        await dispose_engine()


async def test_unparsable_expression_is_rejected_at_the_boundary():
    sm = await _fresh()
    try:
        _run_id, candidate_id = await _seed_candidate(sm, expr="ts_mean(ret,21)")
        async with sm() as session:
            with pytest.raises(HTTPException) as exc:
                await promote.promote_candidate(session, candidate_id)
        assert exc.value.detail["code"] == "FACTOR_EXPR_INVALID"
    finally:
        await dispose_engine()


# ============================ member_names 互异且与表达式绑定 ============================
def test_member_name_is_expression_bound_and_distinct():
    exprs = [_EXPR, "div(ts_std(ret,20),ts_std(ret,90))", "ts_corr(ret,ts_zscore(volume,20),30)"]
    members = validate_members([
        {"factorKey": "expr", "params": {"expr": text}, "weight": 1.0, "direction": "long",
         "name": promote.member_name(text)} for text in exprs])
    names = member_names(members)
    assert len(set(names)) == 3
    assert all(name.startswith("expr:") for name in names)
    # 同一表达式恒得同一列名（与位置无关）。
    assert promote.member_name(_EXPR) == promote.member_name(_EXPR)
    assert promote.member_name(_EXPR) != promote.member_name(exprs[1])


def test_factor_score_params_are_ready_for_the_existing_backtest_chain():
    params = promote._factor_score_params(_EXPR, "short")
    assert len(params["members"]) == 1
    assert params["members"][0]["factorKey"] == "expr"
    assert params["members"][0]["direction"] == "short"
    assert params["members"][0]["name"].startswith("expr:")
    assert "enterLong" in params["thresholds"]


# ============================ 安全不变式 ============================
def test_promote_only_touches_the_three_entity_tables():
    """入库只写 factors / factor_sets / strategies，**绝不**触碰运行历史与交易配置（R-8）。

    用 AST 收集**真实标识符**（import 名 + 属性名 + 变量名），而不是扫源码文本——本模块的
    文档字符串正好在申明这条不变式，文本扫描会自己把自己打红。
    """
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(promote))
    identifiers: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            identifiers.add(node.id)
        elif isinstance(node, ast.Attribute):
            identifiers.add(node.attr)
        elif isinstance(node, ast.ImportFrom):
            identifiers.add(node.module or "")
            identifiers.update(alias.name for alias in node.names)
        elif isinstance(node, ast.Import):
            identifiers.update(alias.name for alias in node.names)
    for banned in ("StrategyRun", "watchlist", "live_auto", "ai_auto", "LiveOrderAudit",
                   "trading", "trade_client"):
        assert not any(banned in name for name in identifiers), f"promote 不得触碰 {banned}"
