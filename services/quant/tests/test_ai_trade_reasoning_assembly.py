"""三条生产装配链的 `system` 捕获断言（ai-trade-deep-reasoning-chain §7.5 S-7 / S-8 / S-9）。

> **为什么必须单开这个文件。** 全部 prompt 用例都在直调 `build_*`，全部决策器用例都在手工构造
> 决策器——于是「协议根本没被装配进去」这一整类缺陷在 CI 里**完全不可见**（评审 P0-5 就是它的
> 一个实例：架构图把反思协议指向 manager/backtest，而 `build_reflection_system_prompt` 全仓唯一
> 的调用点在 `decider._stream_reflection`）。本仓库刚在 `docs/plans/ai-trade-backstop-repair` 上
> 栽过同形的一次（双通道共用纯核心，只有自动侧被实现）。
>
> 故本文件的判据一律取自**假客户端 `stream(system=…)` 实际收到的那一份字符串**，而不是任何
> `build_*` 的直接返回值——断言后者是零成本的自欺。
"""
from __future__ import annotations

import json

import pytest

from app.agent.llm.base import StreamEvent
from app.ai_auto.decider import AiTradeDecider
from app.ai_auto.llm_budget import build_ai_trade_llm_clients
from app.ai_auto.prompts import build_ai_trade_system_prompt
from app.ai_auto.prompts_en import SystemPromptTexts
from app.ai_auto.reasoning_protocol import build_deliberation_protocol, resolve_depth_profile
from app.config import Settings

_PAYLOAD = {"round": 1, "barTs": 1_700_000_000, "lastPx": 30000.0, "position": None,
            "sessionPnlUsd": 0.0, "budgetUsd": 100.0, "takeProfitUsd": None,
            "stopLossUsd": None, "signalBoard": []}


class _CapturingLLM:
    """记录每一次 `stream()` 实际收到的 `system` 文本（本文件的**唯一**判据来源）。"""

    provider = "anthropic"

    def __init__(self, tool: str | None = None, args: dict | None = None):
        self.systems: list[str] = []
        self._tool = tool
        self._args = args or {}

    async def stream(self, *, system, messages, tools):
        self.systems.append(system)
        if self._tool:
            yield StreamEvent("tool_use_start", id="t1", name=self._tool)
            yield StreamEvent("tool_use_args_delta", id="t1",
                              partial_json=json.dumps(self._args, ensure_ascii=False))
        yield StreamEvent("message_done", stop_reason="tool_use",
                          usage={"input_tokens": 11, "output_tokens": 7})


def _assemble(settings: Settings, *, backtest: bool = False, override: str | None = None):
    """把 manager / backtest 装配点的**同一段逻辑**跑一遍（三份预算 + 两处协议注入）。"""
    depth = resolve_depth_profile(settings, backtest=backtest, override=override)
    decision_llm = _CapturingLLM("submit_trade_decision",
                                 {"action": "open_long", "sizeFraction": 0.25,
                                  "confidence": 0.8, "reason": "r"})
    reflection_llm = _CapturingLLM("submit_trade_reflection",
                                   {"verdict": "confirm", "reason": "ok"})
    system_prompt = build_ai_trade_system_prompt(
        "BTC-USDT-SWAP", "30m", 1000.0, 3.0,
        texts=SystemPromptTexts(
            reasoning_text=build_deliberation_protocol(
                depth, reserve_tokens=settings.ai_trade_reasoning_reserve_tokens)))
    decider = AiTradeDecider(decision_llm, system_prompt, context_refresh_rounds=10,
                             settings=settings, reflection_enabled=True,
                             reflection_llm=reflection_llm, depth_profile=depth,
                             allow_reasoning_digest=depth.enabled)
    return depth, decider, decision_llm, reflection_llm


def _quiet(**kw) -> Settings:
    """把重试 / 追问关掉，使断言只看「装配了什么」而不受尝试次数影响。"""
    base = dict(ai_trade_decision_max_retries=0, ai_trade_reflection_max_retries=0,
                ai_trade_reasoning_reask_max=0)
    base.update(kw)
    return Settings(**base)


# ---------------------------------------------------------------- S-7（实盘车道）
async def test_assembled_system_prompts_carry_both_protocols():
    """S-7：决策侧含 `## S12`、**反思侧含 `## R7`**——判据取自模型真正收到的那一份 `system`。

    反思协议只能经 `AiTradeDecider(depth_profile=…)` 携带进去（`_stream_reflection` 每轮现造
    `system`）。任何「在 manager 里把 reasoning_text 传给 build_reflection_system_prompt」的
    写法都会让本用例判红——那正是它存在的理由。
    """
    from app.ai_auto.decider import TradeDecision

    depth, decider, decision_llm, reflection_llm = _assemble(_quiet())
    assert depth.name == "deep"
    await decider.decide(dict(_PAYLOAD))
    await decider.reflect(dict(_PAYLOAD), TradeDecision("open_long", 0.25, 0.8, "r"))
    assert "## S12" in decision_llm.systems[0]
    assert "## S1 " in decision_llm.systems[0]
    assert "## R7" in reflection_llm.systems[0]
    assert "## R1 " in reflection_llm.systems[0]


async def test_off_depth_assembles_neither_protocol():
    """G-7 的装配侧：`off` 档下两份 `system` 都不含任何阶段标记（整条链逐字回退）。"""
    from app.ai_auto.decider import TradeDecision

    depth, decider, decision_llm, reflection_llm = _assemble(_quiet(), override="off")
    assert depth.enabled is False
    await decider.decide(dict(_PAYLOAD))
    await decider.reflect(dict(_PAYLOAD), TradeDecision("open_long", 0.25, 0.8, "r"))
    assert "## S" not in decision_llm.systems[0]
    assert "## R" not in reflection_llm.systems[0]
    # 中文脊柱与反思中文原文仍在（它们是 off 档的真源）。
    assert "决策纪律" in decision_llm.systems[0]
    assert "独立的下单前风控复核官" in reflection_llm.systems[0]


# ---------------------------------------------------------------- S-9（回测车道）
async def test_backtest_lane_assembles_backtest_depth():
    """S-9：回测车道走**同一条**注入路径，且取到的是**回测档**（默认 standard）而非实盘档。"""
    from app.ai_auto.decider import TradeDecision

    depth, decider, decision_llm, reflection_llm = _assemble(_quiet(), backtest=True)
    assert depth.name == "standard"
    await decider.decide(dict(_PAYLOAD))
    await decider.reflect(dict(_PAYLOAD), TradeDecision("open_long", 0.25, 0.8, "r"))
    assert "## S12" in decision_llm.systems[0]
    assert "## R7" in reflection_llm.systems[0]
    # 回测档的篇幅要求必须是 standard 的 60 词，而不是实盘 deep 的 120 词。
    assert "60 words" in decision_llm.systems[0]
    assert "120 words" not in decision_llm.systems[0]


# ---------------------------------------------------------------- S-8（续跑车道）
def test_start_request_depth_survives_pause_resume():
    """S-8：`reasoningDepth` 落在**请求对象**上 ⇒ 冷续跑重跑同一条解析链即自动同档。

    若深度档只读 settings，一次「深度档启动 → 暂停 → 续跑」会静默降回默认档，**且不报任何错**。
    """
    from app.schemas import AiTradeGroupStartIn

    body = AiTradeGroupStartIn(members=[{"instId": "BTC-USDT-SWAP", "allocationPct": 100}],
                               strategyIds=[1], reasoningDepth="exhaustive")
    start_wire = body.model_dump(mode="json")
    assert start_wire["reasoningDepth"] == "exhaustive"
    # 冷续跑：用 `start_wire` 原样重建请求体，再跑同一条解析链。
    replayed = AiTradeGroupStartIn(**start_wire)
    depth = resolve_depth_profile(Settings(), override=replayed.reasoningDepth)
    assert depth.name == "exhaustive"


def test_start_request_rejects_an_unknown_depth_at_the_boundary():
    """越界值 422（`Literal` 校验），**不做静默夹紧**——拼错的档静默回落会骗过用户。"""
    from app.schemas import AiTradeGroupStartIn

    with pytest.raises(Exception):
        AiTradeGroupStartIn(members=[{"instId": "BTC-USDT-SWAP", "allocationPct": 100}],
                            strategyIds=[1], reasoningDepth="deeep")


def test_start_request_without_depth_falls_back_to_settings():
    """缺省 ⇒ `None` ⇒ 用 settings 默认档（老客户端零影响）。"""
    from app.schemas import AiTradeGroupStartIn

    body = AiTradeGroupStartIn(members=[{"instId": "BTC-USDT-SWAP", "allocationPct": 100}],
                               strategyIds=[1])
    assert body.reasoningDepth is None
    assert resolve_depth_profile(Settings(), override=body.reasoningDepth).name == "deep"


# ---------------------------------------------------------------- C-6 的离线替身
def test_deep_profile_prompt_is_at_least_five_times_the_off_profile():
    """G-3 在 CI 里的**影子**：同一组入参下 deep 档系统 prompt 的长度 ≥ off 档的 5 倍。

    它证不了模型会不会真的多写（那要真实 provider，见 `scripts/ai_trade_cot_probe.py`），但能挡住
    「协议根本没被装配」与「协议被删剩一句」两整类回归——那是探针在 CI 里唯一能留下的影子。
    """
    args = ("BTC-USDT-SWAP", "30m", 1000.0, 3.0)
    off = build_ai_trade_system_prompt(*args)
    deep = build_ai_trade_system_prompt(
        *args, texts=SystemPromptTexts(
            reasoning_text=build_deliberation_protocol(resolve_depth_profile(Settings()))))
    assert len(deep) / len(off) >= 5.0, (len(deep), len(off))


async def test_assembled_clients_carry_three_budgets_into_the_same_decider():
    """装配点的另一半：三份预算真的被交给了同一个决策器（否则拆预算等于没拆）。"""
    from app.agent.llm.retry import RetryConfig
    from app.agent.providers import LlmProviderConfig

    cfg = LlmProviderConfig(
        provider_id=None, slug="anthropic", label="t", api_standard="anthropic",
        base_url="https://x", api_key="k", model="m", user_agent="ua", max_tokens=2048,
        temperature=1.0, top_p=0.95, timeout=60.0, anthropic_version="2023-06-01",
        proxy=None, retry=RetryConfig())
    settings = Settings()
    clients = build_ai_trade_llm_clients(cfg, settings,
                                         profile=resolve_depth_profile(settings))
    assert clients.decision._max_tokens == 8192
    assert clients.reflection._max_tokens == 5120
    assert clients.summary._max_tokens == 1536


# ------------------------------------------- S-9b（回测车道的**用户消息**，终审补）
class _RecordingLLM:
    """记录每一次 `stream()` 收到的 `system` 与用户文本（回测车道的真实装配判据）。"""

    provider = "anthropic"

    def __init__(self):
        self.systems: list[str] = []
        self.user_texts: list[str] = []

    async def stream(self, *, system, messages, tools):
        if not tools:                                   # 摘要请求（tools=[]）
            yield StreamEvent("text_delta", text="摘要")
            yield StreamEvent("message_done", stop_reason="end")
            return
        self.systems.append(system)
        self.user_texts.append("".join(
            block.text for msg in messages for block in msg.content
            if getattr(block, "text", None)))
        yield StreamEvent("tool_use_start", id="t", name="submit_trade_decision")
        yield StreamEvent("tool_use_args_delta", id="t",
                          partial_json=json.dumps({"action": "hold", "reason": "h"}))
        yield StreamEvent("message_done", stop_reason="tool_use")


async def test_backtest_lane_user_prompt_carries_the_english_closing_instruction(monkeypatch):
    """S-9b：回测车道的**用户消息**也必须跟着深度档走英文，而不只是系统 prompt。

    收尾任务指令按 payload 的 `reasoningDepth` 加法键分叉，而那个键的单一真源是
    `decider.depth_name`——实盘在 `session._build_decision_payload` 里写它，回测是**第二处
    各自手抄**的装配点。漏写这一行不报错也不判红：系统 prompt 已是英文脊柱（回测默认
    `standard` 档即生效），收尾指令却停在中文原句，反思侧的英文收尾指令永远不会追加。
    """
    from app.ai_auto.backtest import AiBacktestRunner, AiBacktestSpec, _BacktestJob
    from app.db import repositories as repo
    from app.db.models import Strategy
    from app.db.session import dispose_engine, get_sessionmaker, init_db
    from app.paper.strategy_factory import build_paper_strategy_spec
    from sqlalchemy import select

    from tests.conftest import make_synthetic_candles

    try:
        await init_db()
        async with get_sessionmaker()() as session:
            sid = (await session.execute(
                select(Strategy.id).order_by(Strategy.id.asc()).limit(1))).scalar_one()
            paper = build_paper_strategy_spec(await repo.get_strategy_row(session, sid))
        candles = make_synthetic_candles(150)
        llm = _RecordingLLM()

        async def fake_range(*_a, **_k):
            return candles

        async def fake_htf(*_a, **_k):
            return {}

        monkeypatch.setattr("app.ai_auto.backtest.load_range_candles", fake_range)
        monkeypatch.setattr("app.ai_auto.backtest.load_htf_frames", fake_htf)
        monkeypatch.setattr("app.ai_auto.backtest.make_llm_client_from_config",
                            lambda _cfg: llm)
        spec = AiBacktestSpec(
            inst_id="BTC-USDT-SWAP", bar="1H", strategy_ids=[sid], strategy_names=["S"],
            specs=[paper], start_ts=candles[60]["ts"], end_ts=candles[-1]["ts"],
            decision_every_bars=40, initial_capital=10000.0, leverage=3.0,
            stop_loss_pct=0.05, take_profit_pct=0.1, take_profit_usd=None, stop_loss_usd=None,
            context_refresh_rounds=10, min_consensus=0, planned_rounds=3,
            settings=_quiet(), rest=object(), provider=object())
        runner = AiBacktestRunner(spec)
        await runner.run(_BacktestJob("aibt_depth_key", runner))

        assert llm.systems and "## S12" in llm.systems[0]     # 回测档（standard）已生效
        assert "Now run the full deliberation protocol" in llm.user_texts[0]
        assert "请先按上述纪律逐步研判" not in llm.user_texts[0]
    finally:
        await dispose_engine()
