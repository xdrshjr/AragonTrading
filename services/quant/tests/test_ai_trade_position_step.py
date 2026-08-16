"""用户可配置「仓位操作档位」单测（ai-trade-position-sizing-steps §六）。

覆盖六组：① 档位阶梯唯一真源 `position_ladder`（[R-5]）；② 量化吸附 `quantize_size_fraction`
（含「输出恒 ∈ 阶梯」不变式与「≥一格不吞」下限）；③ 决策收口 `_map_decision` 档位吸附（唯一硬约束点）
与 `step=0` 零回归；④ prompt / 工具 schema 由真源派生（软引导）；⑤ 装配链路 request/spec 直接构造默认 0
（既有测试零回归）+ decider 穿参；⑥ API 缺省回填默认 / 越界 422 / `0` 连续被接受（[R-2]）。

纯离线（②③④⑤ 无网络无 DB；⑥ 用测试内 SQLite + 全桩 provider/LLM，绝不触网）。
"""
from __future__ import annotations

import asyncio
import time

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.ai_auto.backtest import AiBacktestRunner, AiBacktestSpec
from app.ai_auto.backtest_manager import AiBacktestStartRequest, get_ai_backtest_manager
from app.ai_auto.decider import AiTradeDecider
from app.ai_auto.manager import AiTradeGroupStartRequest
from app.ai_auto.prompts import (
    DECISION_TOOL_SCHEMA,
    build_ai_trade_system_prompt,
    build_decision_tool_schema,
)
from app.ai_auto.signals import position_ladder, quantize_size_fraction
from app.api.ai_auto import _resolve_position_step
from app.config import Settings
from app.db.models import Strategy
from app.db.session import dispose_engine, get_sessionmaker, init_db


# ============================ ① 档位阶梯唯一真源（§6.1）============================
@pytest.mark.parametrize("step, expected", [
    (0.1, [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0]),
    (0.2, [0.2, 0.4, 0.6, 0.8, 1.0]),
    (0.25, [0.25, 0.5, 0.75, 1.0]),
    (0.5, [0.5, 1.0]),
    (1.0, [1.0]),
    (0.0, []),                         # 连续/不约束 → 无离散档位
    (0.3, [0.3, 0.6, 0.9]),            # 非整除边界：顶格 0.9（<100%），佐证只放能整除 100 的预设
])
def test_position_ladder_table(step, expected):
    assert position_ladder(step) == pytest.approx(expected)


def test_position_ladder_negative_is_empty():
    assert position_ladder(-0.5) == []


# ============================ ② 量化吸附（§6.2）============================
@pytest.mark.parametrize("raw, step, expected", [
    (0.37, 0.1, 0.4),         # 就近吸附
    (0.04, 0.1, 0.1),         # 极小开仓意图 → 至少一格（不因四舍五入到 0 被吞）
    (0.93, 0.25, 1.0),        # 就近吸附到顶格
    (1.2, 0.25, 1.0),         # 越界先夹到 1.0 再吸附（顶格封顶）
    (0.5, 0.25, 0.5),         # 已是档位 → 不变
    (0.37, 0.0, 0.37),        # step=0 连续直通（clamp01）
    (1.5, 0.0, 1.0),          # step=0 仍夹到 [0,1]
])
def test_quantize_size_fraction_table(raw, step, expected):
    assert quantize_size_fraction(raw, step) == pytest.approx(expected)


@pytest.mark.parametrize("step", [0.1, 0.2, 0.25, 0.5, 1.0])
@pytest.mark.parametrize("raw", [0.01, 0.13, 0.37, 0.5, 0.66, 0.93, 1.0])
def test_quantize_output_is_always_on_ladder(step, raw):
    """顶格同源不变式（[R-5]）：任何 >0 输入的量化结果必落在 `position_ladder(step)` 上。"""
    out = quantize_size_fraction(raw, step)
    assert out in position_ladder(step)


# ============================ ③ 决策收口档位吸附 + 零回归（§6.3）============================
def _decider(step: float) -> AiTradeDecider:
    """构造 decider（llm 不被 _map_decision 使用，传 object 即可）；min_confidence 默认 0=门禁不触发。"""
    return AiTradeDecider(object(), "sys", context_refresh_rounds=10,
                          settings=Settings(), position_step_pct=step)


def test_map_decision_snaps_open_size_to_ladder():
    """position_step=0.25 时 LLM 报 sizeFraction=0.37 → 吸附到 0.25（唯一硬约束点）。"""
    decision, degrade = _decider(0.25)._map_decision(
        {"action": "open_long", "sizeFraction": 0.37, "confidence": 0.8, "reason": "r"})
    assert degrade is None
    assert decision.action == "open_long"
    assert decision.size_fraction == pytest.approx(0.25)


def test_map_decision_tiny_size_snaps_up_not_missing_size():
    """极小 0.04 被吸附到最小一格 0.25（**不**回落 missing_size——max(1,…) 保证一格）。"""
    decision, degrade = _decider(0.25)._map_decision(
        {"action": "open_long", "sizeFraction": 0.04, "reason": "r"})
    assert degrade is None
    assert decision.size_fraction == pytest.approx(0.25)


def test_map_decision_zero_size_still_missing_size():
    """报 0 仍回落 missing_size（回退只由 _clamp01 的 None/≤0 决定，量化不改变该边界）。"""
    decision, degrade = _decider(0.25)._map_decision(
        {"action": "open_long", "sizeFraction": 0, "reason": "r"})
    assert degrade == "missing_size"
    assert decision.action == "hold"


def test_map_decision_step_zero_is_byte_identical_legacy():
    """step=0（连续）→ 决策值逐字不变（回归门禁：既有 API/构造路径行为不变）。"""
    decision, degrade = _decider(0.0)._map_decision(
        {"action": "open_long", "sizeFraction": 0.37, "confidence": 0.8, "reason": "r"})
    assert degrade is None
    assert decision.size_fraction == pytest.approx(0.37)


def test_map_decision_close_and_hold_unaffected():
    """close/hold 不带 size、不受档位影响（档位只约束开仓规模）。"""
    d = _decider(0.25)
    close, _ = d._map_decision({"action": "close", "reason": "exit"})
    hold, _ = d._map_decision({"action": "hold", "reason": "wait"})
    assert close.action == "close" and close.size_fraction is None
    assert hold.action == "hold" and hold.size_fraction is None


# ============================ ④ prompt / 工具 schema 由真源派生（软引导）============================
def test_tool_schema_enum_derives_from_ladder():
    schema = build_decision_tool_schema(0.25)
    assert schema["properties"]["sizeFraction"]["enum"] == position_ladder(0.25)


def test_tool_schema_step_zero_returns_module_constant_unchanged():
    """step=0 原样返回模块常量（回退兜底 + 现有引用不破）；step>0 不泄漏改动回常量。"""
    assert build_decision_tool_schema(0.0) is DECISION_TOOL_SCHEMA
    build_decision_tool_schema(0.25)
    assert DECISION_TOOL_SCHEMA["properties"]["sizeFraction"] == {
        "type": "number", "minimum": 0, "maximum": 1}


def test_system_prompt_replaces_size_rule_when_step_positive():
    """[R-8]：step>0 时纪律 #3 的「sizeFraction 保守（默认 ≤0.5）」被「只能取档位」整句替换（非追加）。"""
    p = build_ai_trade_system_prompt("BTC-USDT-SWAP", "1H", 100.0, 3.0, position_step_pct=0.25)
    assert "只能" in p and "0.25 / 0.5 / 0.75 / 1" in p
    assert "sizeFraction 保守（默认 ≤0.5）" not in p


def test_system_prompt_step_zero_keeps_legacy_size_rule():
    """step=0（默认）→ 原文逐字保留（连续，零回归）。"""
    p = build_ai_trade_system_prompt("BTC-USDT-SWAP", "1H", 100.0, 3.0)
    assert "sizeFraction 保守（默认 ≤0.5）" in p
    assert p.rstrip().endswith("决策工具。**")     # 收尾硬约束仍在末位（不被档位改动破坏）


# ============================ ⑤ 装配链路：直接构造默认 0（零回归）+ decider 穿参 ============================
def test_request_dataclasses_default_position_step_zero():
    """直接构造 request/spec dataclass 默认 0（连续）→ 保既有集成/单测逐字零回归。"""
    assert AiTradeGroupStartRequest.__dataclass_fields__["position_step_pct"].default == 0.0
    assert AiBacktestStartRequest.__dataclass_fields__["position_step_pct"].default == 0.0
    assert AiBacktestSpec.__dataclass_fields__["position_step_pct"].default == 0.0


def test_decider_stores_step_and_injects_enum():
    """_build_member/backtest runner 造 decider 的口径：step 存 _position_step 且注入工具 schema enum。"""
    d = _decider(0.5)
    assert d._position_step == pytest.approx(0.5)
    assert d._tool.input_schema["properties"]["sizeFraction"]["enum"] == position_ladder(0.5)


def test_decider_negative_step_clamped_to_zero():
    """负 step 兜底为 0（连续），不产生非法阶梯。"""
    assert _decider(-1.0)._position_step == 0.0


# ============================ ⑥ API 解析 + schema 校验（§6.6）============================
def test_resolve_position_step_only_fills_none():
    """[R-2] resolver 仅缺省回填：None→settings 默认；有值原样返回（含 0），不复夹。"""
    settings = Settings()   # ai_trade_default_position_step_pct=0.25
    assert _resolve_position_step(None, settings) == pytest.approx(0.25)
    assert _resolve_position_step(0.5, settings) == pytest.approx(0.5)
    assert _resolve_position_step(0.0, settings) == 0.0   # 连续退出口原样透传


async def _idle_run(self, job):
    try:
        await asyncio.Event().wait()
    except asyncio.CancelledError:
        job.mark_terminal("cancelled", {"jobId": job.job_id})
        raise


def _load_app():
    from app.main import app
    return app


async def _fresh() -> int:
    await init_db()
    mgr = get_ai_backtest_manager()
    mgr._jobs.clear()
    mgr._reserved = 0
    async with get_sessionmaker()() as s:
        return (await s.execute(select(Strategy.id).order_by(Strategy.id.asc()).limit(1))).scalar_one()


async def _client(app) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _bt_body(sid: int, **over) -> dict:
    now = int(time.time())
    body = {"instId": "BTC-USDT-SWAP", "bar": "1H", "strategyIds": [sid],
            "startTs": now - 100_000, "endTs": now - 1_000, "decisionEveryBars": 100,
            "contextRefreshRounds": 10}
    body.update(over)
    return body


def _stub_provider(monkeypatch, provider=object()) -> None:
    async def fake_resolve(*_a, **_k):
        return provider

    monkeypatch.setattr("app.ai_auto.backtest_manager.resolve_active_provider", fake_resolve)
    monkeypatch.setattr("app.ai_auto.backtest.make_llm_client_from_config", lambda cfg: object())
    monkeypatch.setattr(AiBacktestRunner, "run", _idle_run)


async def _cleanup(app):
    mgr = get_ai_backtest_manager()
    for job in list(mgr._jobs.values()):
        job.force_cancel()
        await job.join()
    mgr._jobs.clear()
    mgr._reserved = 0
    app.dependency_overrides.clear()
    await dispose_engine()


async def _started_runner(client, body):
    """启动一个回测作业（run 被桩为 idle）并回其已构造的 runner（含解析后的 position_step_pct）。"""
    r = await client.post("/api/ai-trade/backtest/start", json=body)
    assert r.status_code == 200, r.text
    return get_ai_backtest_manager().get(r.json()["jobId"]).runner


async def test_api_position_step_defaults_when_omitted(monkeypatch):
    """缺省（未传 positionStepPct）→ 落 settings 默认 0.25，且 runner/_run_config 一致回显（[R-2]）。"""
    sid = await _fresh()
    app = _load_app()
    _stub_provider(monkeypatch)
    try:
        async with await _client(app) as client:
            runner = await _started_runner(client, _bt_body(sid))
            assert runner.position_step_pct == pytest.approx(0.25)
            assert runner._run_config()["positionStepPct"] == pytest.approx(0.25)
    finally:
        await _cleanup(app)


async def test_api_position_step_passthrough_and_continuous_exit(monkeypatch):
    """显式值原样透传；`0`（连续退出口）被接受并落 0（非 UI 路径，仅 API/后端）。"""
    sid = await _fresh()
    app = _load_app()
    _stub_provider(monkeypatch)
    try:
        async with await _client(app) as client:
            assert (await _started_runner(
                client, _bt_body(sid, positionStepPct=0.5))).position_step_pct == pytest.approx(0.5)
            assert (await _started_runner(
                client, _bt_body(sid, positionStepPct=0))).position_step_pct == 0.0
    finally:
        await _cleanup(app)


@pytest.mark.parametrize("bad", [1.5, -1])
async def test_api_position_step_out_of_range_rejected_422(monkeypatch, bad):
    """[R-2] 越界（>1 / <0）在入口 schema 即 422 拒绝（**非**夹紧），与既有 leverage 边界同惯例。"""
    sid = await _fresh()
    app = _load_app()
    _stub_provider(monkeypatch)
    try:
        async with await _client(app) as client:
            r = await client.post("/api/ai-trade/backtest/start",
                                  json=_bt_body(sid, positionStepPct=bad))
            assert r.status_code == 422, r.text
    finally:
        await _cleanup(app)
