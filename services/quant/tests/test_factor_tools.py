"""因子 Agent 工具测试：6 数据工具 ok/payload + show_factor ui-action + 启用闸门。"""
from __future__ import annotations

import pytest

from app.agent.tools.base import ToolContext, UiContext
from app.agent.tools.registry import ToolRegistry
from app.okx.types import Candle
from tests.conftest import make_synthetic_candles


class _FakeRest:
    async def health(self) -> bool:
        return True

    async def get_candles(self, inst_id, bar, limit=300, before=None, after=None):
        rows = make_synthetic_candles(min(limit, 300), start_ts=1_700_000_000, step=3600)
        return [Candle(ts=r["ts"], o=r["o"], h=r["h"], l=r["l"], c=r["c"], vol=r["vol"]) for r in rows]


@pytest.fixture
def patch_okx(monkeypatch):
    fake = _FakeRest()
    monkeypatch.setattr("app.agent.tools.factor_tools.get_rest_client", lambda: fake)
    return fake


def _ctx():
    from app.db.session import get_sessionmaker
    return ToolContext(session_factory=get_sessionmaker(), ui=UiContext(instId="BTC-USDT-SWAP", bar="1H"))


_RANGE = {"instId": "BTC-USDT-SWAP", "bar": "1H",
          "startTs": 1_700_000_000, "endTs": 1_700_000_000 + 295 * 3600}


async def _init():
    from app.db.session import init_db
    await init_db()


async def test_list_factors_tool(patch_okx):
    from app.db.session import dispose_engine
    await _init()
    try:
        reg = ToolRegistry()
        res = await reg.execute("list_factors", {}, _ctx())
        assert res.ok and "因子库" in res.summary
        assert len(res.payload["library"]) == 12
        assert len(res.payload["stored"]) >= 12  # seeded builtin
    finally:
        await dispose_engine()


async def test_compute_and_evaluate_tools(patch_okx):
    from app.db.session import dispose_engine
    await _init()
    try:
        reg = ToolRegistry()
        comp = await reg.execute("compute_factor", {"factorKey": "mom_roc", **_RANGE}, _ctx())
        assert comp.ok and comp.payload["factorKey"] == "mom_roc"

        ev = await reg.execute("evaluate_factor",
                               {"factorKey": "mom_roc", "direction": "auto", **_RANGE}, _ctx())
        assert ev.ok and "评估" in ev.summary
        assert ev.payload["direction"] in ("long", "short")
    finally:
        await dispose_engine()


async def test_mine_and_train_tools(patch_okx):
    from app.db.session import dispose_engine
    await _init()
    try:
        reg = ToolRegistry()
        mine = await reg.execute("mine_factors", {**_RANGE, "topN": 5}, _ctx())
        assert mine.ok and len(mine.payload["items"]) <= 5

        members = [{"factorKey": "mom_roc", "direction": "long"},
                   {"factorKey": "rev_rsi", "direction": "short"}]
        train = await reg.execute("train_factor_weights",
                                  {"members": members, "method": "linear", **_RANGE}, _ctx())
        assert train.ok and train.payload["available"] is True
    finally:
        await dispose_engine()


async def test_train_tool_missing_lib_ok_false(patch_okx, monkeypatch):
    """缺 xgboost → 工具 ok=False（registry 收敛 HTTPException），绝不假权重。"""
    from app.db.session import dispose_engine
    from app.engine.factors import training as training_mod
    monkeypatch.setattr(training_mod.importlib.util, "find_spec", lambda name: None)
    await _init()
    try:
        reg = ToolRegistry()
        members = [{"factorKey": "mom_roc", "direction": "long"},
                   {"factorKey": "rev_rsi", "direction": "short"}]
        res = await reg.execute("train_factor_weights",
                                {"members": members, "method": "xgboost", **_RANGE}, _ctx())
        assert res.ok is False
    finally:
        await dispose_engine()


async def test_score_factor_combo_tool(patch_okx):
    from app.db.session import dispose_engine, get_sessionmaker
    from app.db import repositories as repo
    await _init()
    try:
        sm = get_sessionmaker()
        async with sm() as session:
            row = await repo.create_factor_set(session, values=repo.FactorSetValues(
                name="测试组合",
                members=[{"factorKey": "mom_roc", "params": {}, "direction": "long", "weight": 1.0}],
                method="linear", config={"thresholds": {"normWindow": 80}},
                model={"weights": {"mom_roc": 1.0}}, metrics=None))
            set_id = row.id
        reg = ToolRegistry()
        res = await reg.execute("score_factor_combo",
                                {"factorSetId": set_id, "instId": "BTC-USDT-SWAP", "bar": "1H"}, _ctx())
        assert res.ok and res.payload["factorSetId"] == set_id
    finally:
        await dispose_engine()


async def test_show_factor_ui_action(patch_okx):
    from app.db.session import dispose_engine
    await _init()
    try:
        reg = ToolRegistry()
        assert reg.is_ui_action("show_factor")
        res = await reg.execute("show_factor", {"factorKey": "mom_roc"}, _ctx())
        assert res.ok and res.payload["action"] == "show_factor"
        assert res.payload["factorKey"] == "mom_roc"
    finally:
        await dispose_engine()


async def test_disabled_factor_tool_is_blocked(patch_okx):
    """禁用闸门：未在 enabled 集合内的工具被双闸门拦截（ok=False）。"""
    from app.db.session import dispose_engine
    await _init()
    try:
        reg = ToolRegistry()
        enabled = reg.all_names() - {"compute_factor"}
        res = await reg.execute("compute_factor", {"factorKey": "mom_roc", **_RANGE},
                                _ctx(), enabled=enabled)
        assert res.ok is False and "禁用" in res.summary
    finally:
        await dispose_engine()
