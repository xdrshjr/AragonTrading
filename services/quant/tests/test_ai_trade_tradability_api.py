"""可交易性预检端点 + 历史预设对照卡端点（轴 A / 轴 E · G-3 / G-5 / §5.1 / §5.2）。

核心纪律：**取数失败绝不 5xx**。探针是一个只提示、不阻断的旁路，把用户挡在启动之外的探针，
比一个给错建议的探针更坏。
"""
from __future__ import annotations

import json

import pytest
from httpx import ASGITransport, AsyncClient

from app.ai_auto import tradability_probe as probe_mod
from app.db.models import StrategyRun
from app.db.session import dispose_engine, get_sessionmaker, init_db
from app.okx.types import Candle


def _candles(n: int = 200) -> list[Candle]:
    return [Candle(ts=1_700_000_000 + i * 60, o=100_000.0, h=100_030.0, l=99_970.0,
                   c=100_000.0 + (30 if i % 2 else -30), vol=1.0) for i in range(n)]


class _Rest:
    def __init__(self, rows, fail=False):
        self.rows, self.fail = rows, fail

    async def get_candles(self, inst_id, bar, limit, **_kw):
        if self.fail:
            raise RuntimeError("okx down")
        return self.rows


async def _client() -> AsyncClient:
    from app.main import app          # 延迟导入：与既有 api 用例同款姿态（init_db 之后再建 app）
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


# ---------- §5.1 端点 ----------
async def test_probe_endpoint_returns_a_full_wire(monkeypatch):
    await init_db()
    try:
        monkeypatch.setattr(probe_mod, "get_rest_client", lambda: _Rest(_candles()))
        async with await _client() as client:
            res = await client.get("/api/ai-trade/tradability",
                                   params={"instId": "BTC-USDT-SWAP", "bar": "1m"})
        assert res.status_code == 200
        body = res.json()
        assert body["instId"] == "BTC-USDT-SWAP" and body["bar"] == "1m"
        assert body["level"] in ("ok", "marginal", "infeasible", "unknown")
        assert body["disclaimer"] and "不是收益预测" in body["disclaimer"]
        # `response_model` 必须真的把这些子块带出来（本仓库有过「静默吞新 wire 键」的事故）。
        for key in ("costBps", "costConfidence", "requiredBps", "vetoRate", "alternatives"):
            assert key in body
    finally:
        await dispose_engine()


async def test_probe_endpoint_never_5xx_when_market_data_is_unreachable(monkeypatch):
    """G-3：无缓存 ⇒ 200 且 `level=="unknown"`——**任何情况下都不得 5xx**。"""
    await init_db()
    try:
        monkeypatch.setattr(probe_mod, "get_rest_client", lambda: _Rest([], fail=True))
        async with await _client() as client:
            res = await client.get("/api/ai-trade/tradability",
                                   params={"instId": "NOPE-USDT-SWAP", "bar": "1m"})
        assert res.status_code == 200
        assert res.json()["level"] == "unknown"
    finally:
        await dispose_engine()


async def test_probe_endpoint_rejects_an_unknown_bar_with_422():
    await init_db()
    try:
        async with await _client() as client:
            res = await client.get("/api/ai-trade/tradability",
                                   params={"instId": "BTC-USDT-SWAP", "bar": "7m"})
        assert res.status_code == 422
        assert res.json()["error"]["code"] == "BAD_BAR"
    finally:
        await dispose_engine()


async def test_probe_endpoint_is_not_gated_by_the_ai_master_switch(monkeypatch):
    """沿用 `/ai-trade/horizon-profiles` 的成文先例：读一张表不下任何单。"""
    await init_db()
    try:
        monkeypatch.setattr(probe_mod, "get_rest_client", lambda: _Rest(_candles()))
        from app.config import get_settings
        get_settings.cache_clear()
        monkeypatch.setenv("AI_TRADE_ENABLED", "false")
        async with await _client() as client:
            res = await client.get("/api/ai-trade/tradability",
                                   params={"instId": "BTC-USDT-SWAP", "bar": "1m"})
        assert res.status_code == 200
    finally:
        get_settings.cache_clear()
        await dispose_engine()


async def test_unknown_preset_is_treated_as_off_rather_than_422(monkeypatch):
    """一个看不懂的预设名不该把用户的预检挡掉（fail-open 的极性纪律）。"""
    await init_db()
    try:
        monkeypatch.setattr(probe_mod, "get_rest_client", lambda: _Rest(_candles()))
        async with await _client() as client:
            res = await client.get("/api/ai-trade/tradability",
                                   params={"instId": "BTC-USDT-SWAP", "bar": "1m",
                                           "preset": "not-a-preset"})
        assert res.status_code == 200
    finally:
        await dispose_engine()


# ---------- §5.2 历史预设对照卡 ----------
async def _seed_run(group_id: str, preset: str, *, run_id_seed: int) -> None:
    sm = get_sessionmaker()
    async with sm() as db:
        db.add(StrategyRun(
            strategy_id=run_id_seed, mode="ai-trade", inst_id="BTC-USDT-SWAP", bar="1H",
            start_ts=1_700_000_000, end_ts=1_700_003_600, status="stopped",
            group_id=group_id, group_started_ts=1_700_000_000, group_total_budget_usd=1000.0,
            config_json=json.dumps({"budgetUsd": 1000.0, "costBasis": "net"}),
            metrics_json=json.dumps({"aiStats": {
                "disciplinePreset": preset,
                "edgeGate": {"gated": 10, "veto": 4},
                "openFunnel": {"intents": 10, "filled": 3}}})))
        await db.commit()


async def test_preset_comparison_groups_by_preset_and_flags_small_samples():
    """分组聚合 + `samples < minSamples ⇒ conclusive=false`。

    ⚠️ 预设 id 用**本用例专属的合成值**（`__cmp_*`）而不是 `balanced` / `off`：本仓库的用例共用
    一个临时 DB，同批次里其它文件也会写入真实预设名的 ai-trade run，按真名断言绝对样本数会随
    「今天跑了哪些文件」而红——那是一条不稳定的判据，不是一条更严的判据。
    """
    await init_db()
    try:
        await _seed_run("cmp1", "__cmp_two", run_id_seed=101)
        await _seed_run("cmp2", "__cmp_two", run_id_seed=102)
        await _seed_run("cmp3", "__cmp_one", run_id_seed=103)
        async with await _client() as client:
            res = await client.get("/api/ai-trade/history/preset-comparison")
        assert res.status_code == 200
        body = res.json()
        groups = {g["preset"]: g for g in body["groups"]}
        assert groups["__cmp_two"]["samples"] == 2
        assert groups["__cmp_two"]["conclusive"] is False        # < minSamples ⇒ 不下结论
        assert groups["__cmp_one"]["samples"] == 1
        assert groups["__cmp_two"]["edgeVetoRate"] == pytest.approx(0.4)
        assert groups["__cmp_two"]["openConversion"] == pytest.approx(0.3)
        assert body["minSamples"] >= 3
    finally:
        await dispose_engine()


async def test_preset_comparison_route_is_not_swallowed_by_the_group_id_path():
    """`/history/preset-comparison` 必须排在 `/history/{group_id}` **之前**，否则会被当成
    一个 group_id 去查并返回 404。"""
    await init_db()
    try:
        async with await _client() as client:
            res = await client.get("/api/ai-trade/history/preset-comparison")
        assert res.status_code == 200
        # 被路径参数吞掉时会是 404 + `{"error":{"code":"NOT_FOUND"}}`，故断言的是**形状**而不是
        # 空列表（本仓库的用例共用一个临时 DB，同文件的前序用例已写入过历史 run）。
        assert "groups" in res.json() and "minSamples" in res.json()
    finally:
        await dispose_engine()


async def test_legacy_runs_without_a_preset_never_pollute_a_named_bucket():
    """缺 `disciplinePreset` 的老 run 归 `unknown` 并如实标注——**不能**默默算进 `balanced`。"""
    await init_db()
    try:
        sm = get_sessionmaker()
        async with sm() as db:
            db.add(StrategyRun(
                strategy_id=109, mode="ai-trade", inst_id="ETH-USDT-SWAP", bar="1H",
                start_ts=1_700_000_000, status="stopped", group_id="legacy",
                group_started_ts=1_700_000_000,
                config_json=json.dumps({"budgetUsd": 500.0}), metrics_json="{}"))
            await db.commit()
        async with await _client() as client:
            body = (await client.get("/api/ai-trade/history/preset-comparison")).json()
        groups = {g["preset"]: g for g in body["groups"]}
        assert "unknown" in groups                            # 独立成桶，绝不并进任一具名档
        assert groups["unknown"]["edgeVetoRate"] is None      # 算不出 ⇒ null，不填 0 冒充
        assert groups["unknown"]["openConversion"] is None
    finally:
        await dispose_engine()
