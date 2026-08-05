"""因子挖掘测试：工作量计量、bar 不兼容跳过、abs(IR) 降序、落库去重 / None-IR 不落库 / 计数（H5/H8）。"""
from __future__ import annotations

from app.engine.backtester import candles_to_df
from app.engine.factors import (
    FACTOR_LIBRARY,
    default_candidates,
    mine_factors,
    mining_workload,
    visible_factor_keys,
)
from app.okx.types import Candle
from tests.conftest import make_synthetic_candles


def _df(n: int = 360):
    return candles_to_df(make_synthetic_candles(n, step=3600))


_RANGE = {"instId": "BTC-USDT-SWAP", "bar": "1H",
          "startTs": 1_700_000_000, "endTs": 1_700_000_000 + 295 * 3600}


class _FakeRest:
    async def health(self) -> bool:
        return True

    async def get_candles(self, inst_id, bar, limit=300, before=None, after=None):
        rows = make_synthetic_candles(min(limit, 300), start_ts=1_700_000_000, step=3600)
        return [Candle(ts=r["ts"], o=r["o"], h=r["h"], l=r["l"], c=r["c"], vol=r["vol"]) for r in rows]


# ============================ 纯引擎层 ============================
def test_mining_workload_is_candidate_times_bars():
    assert mining_workload(10, 100) == 1000
    assert mining_workload(-1, 100) == 0    # 负数夹到 0，绝不返回负工作量
    assert mining_workload(5, -3) == 0


def test_default_candidates_cover_whole_library():
    """经典挖掘候选集覆盖**目录可见**全库；``hidden`` 的 ``expr`` 桥绝不进候选（§3.6）。"""
    keys = {c["factorKey"] for c in default_candidates("1H")}
    assert keys == set(visible_factor_keys())
    assert "expr" not in keys
    assert "expr" in FACTOR_LIBRARY


def test_mine_ranks_by_abs_ir_descending():
    """有效项按 abs(RankIC IR) 降序；无 IR 的占位项沉底（绝不静默截断，排名口径稳定）。"""
    ranked = mine_factors(_df(), default_candidates("1H"), bar="1H", horizons=(1, 3, 6, 12))
    irs = [abs(r["ir"]) for r in ranked if r.get("ir") is not None]
    assert irs == sorted(irs, reverse=True)


def test_bar_incompatible_candidate_is_marked_not_crashed():
    """日内限定因子在非日内 bar 标 note=bar_incompatible（直接跳过，绝不抛错拖垮整轮）。"""
    cands = [{"factorKey": "px_vwap_dist", "params": {}, "direction": "long", "category": "liquidity"}]
    ranked = mine_factors(_df(), cands, bar="1D", horizons=(1, 3, 6))
    assert ranked[0]["note"] == "bar_incompatible"
    assert ranked[0]["ir"] is None


# ============================ service 落库去重 / 计数 ============================
async def test_mine_persist_dedupes_and_reports_counts():
    """连续两次同区间 mine(persist=true)：第二次 persisted=0（全部去重）；计数自洽。"""
    from app.db.session import dispose_engine, get_sessionmaker, init_db
    from app.factors.service import FactorWindow, mine
    await init_db()
    try:
        sm = get_sessionmaker()
        window = FactorWindow(**{"inst_id": _RANGE["instId"], "bar": _RANGE["bar"],
                                 "start_ts": _RANGE["startTs"], "end_ts": _RANGE["endTs"]})
        async with sm() as session:
            first = await mine(session, _FakeRest(), window, categories=None,
                               horizons=(1, 3, 6, 12), top_n=8, persist=True)
        assert first["evaluated"] == first["persisted"] + first["skipped"]
        valid = [it for it in first["items"]
                 if it.get("ir") is not None and it.get("note") != "bar_incompatible"]
        assert first["persisted"] <= len(valid)   # None-IR / bar_incompatible 绝不落库

        # 二次同区间：全部已存 → 去重（与先前 DB 状态无关，落库幂等）
        async with sm() as session:
            second = await mine(session, _FakeRest(), window, categories=None,
                                horizons=(1, 3, 6, 12), top_n=8, persist=True)
        assert second["persisted"] == 0
        assert second["skipped"] == second["evaluated"]
    finally:
        await dispose_engine()


async def test_mine_without_persist_reports_zero_persist_counts():
    """persist=false（预览/Agent 路径）：evaluated 反映候选数，不落库不计 skipped。"""
    from app.db.session import dispose_engine, get_sessionmaker, init_db
    from app.factors.service import FactorWindow, mine
    await init_db()
    try:
        sm = get_sessionmaker()
        window = FactorWindow(inst_id=_RANGE["instId"], bar=_RANGE["bar"],
                              start_ts=_RANGE["startTs"], end_ts=_RANGE["endTs"])
        async with sm() as session:
            res = await mine(session, _FakeRest(), window, categories=None,
                             horizons=(1, 3, 6, 12), top_n=5, persist=False)
        assert res["evaluated"] == len(res["items"]) <= 5
        assert res["persisted"] == 0 and res["skipped"] == 0
    finally:
        await dispose_engine()


async def test_bulk_create_factors_returns_only_new_rows():
    """repo 去重：重复 (factor_key, 规范化 params, direction) 已存即跳过，仅返回新写入行。"""
    from app.db import repositories as repo
    from app.db.session import dispose_engine, get_sessionmaker, init_db
    await init_db()
    try:
        sm = get_sessionmaker()
        values = [repo.FactorValues(factor_key="mom_roc", name="动量", params={"win": 33},
                                    direction="long", category="momentum", source="mined"),
                  repo.FactorValues(factor_key="mom_roc", name="动量", params={"win": 33},
                                    direction="long", category="momentum", source="mined")]
        async with sm() as session:
            inserted = await repo.bulk_create_factors(session, values)
        assert len(inserted) == 1                 # 批内自去重
        async with sm() as session:
            again = await repo.bulk_create_factors(session, [values[0]])
        assert len(again) == 0                     # 与已存行去重
    finally:
        await dispose_engine()
