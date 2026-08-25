"""会话接线的集成断言（ai-trade-experience-context-graph §4.10 的 W-6…W-13）。

覆盖 A-11（决策轮零 IO）/ A-18 与 A-19（启动 wire 与续跑同源）/ A-30（`harvestGap` 有渲染落点）/
A-31（inject 的顺序约束）/ W-13（轨迹键）。

脱网、桩 LLM、不下真单。
"""
from __future__ import annotations

import asyncio
import json
import time

from app.agent.llm.base import StreamEvent
from app.ai_auto.decider import AiTradeDecider
from app.ai_auto.experience import SessionGraph
from app.ai_auto.experience.events import EventIdentity
from app.ai_auto.experience.graph import Graph, GraphEdge, GraphNode
from app.ai_auto.experience.rules import ExperienceRule
from app.ai_auto.experience.runtime import ExperienceRuntime
from app.ai_auto.session import AiTradeSession
from app.ai_auto.trace import new_round_trace
from app.config import Settings
from app.engine.simulator import RiskParams
from app.live_auto.executor import LiveExecutor
from app.okx.accounts import AccountCredentials
from app.okx.types import Candle
from app.paper.strategy_factory import PaperStrategySpec
from app.strategies.base import BaseStrategy, StrategyMeta

_START_TS = 1_700_000_000
_STEP = 3600
_PX = 30_000.0


class _StubStrategy(BaseStrategy):
    meta = StrategyMeta(key="_stub_experience", name="Stub", description="t",
                        default_params={}, params_schema={})

    def warmup(self) -> int:
        return 1

    def on_candle(self, df, i, ctx):
        return []


class _StubLLM:
    async def stream(self, *, system, messages, tools):
        if tools:
            yield StreamEvent("tool_use_start", id="t", name="submit_trade_decision")
            yield StreamEvent("tool_use_args_delta", id="t", partial_json=json.dumps(
                {"action": "hold", "reason": "r", "confidence": 0.7}))
            yield StreamEvent("message_done", stop_reason="tool_use")
        else:
            yield StreamEvent("text_delta", text="摘要")
            yield StreamEvent("message_done", stop_reason="end")


def _snapshot() -> SessionGraph:
    """一份最小可检索快照：一条铁律 + 它的两条 `applies_in` 边。

    `last_seen_ts` 必须取**当下**的墙钟：`recency = 0.5 ** (elapsed / halflife)` 会把一条三年前
    的经验的 `score` 压到 `round(..., 9)` 之下变成 0，随后被 `retrieve` 的 `score > 0` 剔除。
    那是**正确的**产品行为（三年前的教训不该与昨天同权），但会让这条接线用例测不到它想测的东西。
    """
    now = int(time.time())
    lesson = GraphNode(key="lesson:x", kind="lesson", label="高波动追突破做多净额为负",
                       source="stat", tier="iron", support=23, weight=0.9,
                       stats={"tStat": -2.8, "meanNetBps": -31.0, "n": 23},
                       signature={"symbol": "BTC-USDT-SWAP", "volatility": "high_volatility"},
                       last_seen_ts=now)
    contexts = [
        GraphNode(key="symbol:BTC-USDT-SWAP", kind="context", axis="symbol",
                  value="BTC-USDT-SWAP", weight=1.0),
        GraphNode(key="volatility:high_volatility", kind="context", axis="volatility",
                  value="high_volatility", weight=1.0),
    ]
    edges = [GraphEdge("lesson:x", "symbol:BTC-USDT-SWAP", "applies_in", weight=1.0),
             GraphEdge("lesson:x", "volatility:high_volatility", "applies_in", weight=1.0)]
    return SessionGraph(graph=Graph([lesson, *contexts], edges, as_of_ts=now),
                        totals={"lessons": 148, "events": 2043, "sessions": 37},
                        as_of_ts=now)


def _armed_runtime() -> ExperienceRuntime:
    """生产形状的运行时：**必须带 `EventIdentity`**。

    `seed_axes` 的 `symbol` / `bar` 两轴取自 `identity`（事件的溯源身份与检索的种子出自同一处，
    这样「采进去的语境」与「读出来的语境」在结构上不可能对不上）。缺了它，种子的 symbol 轴是
    空串 ⇒ 被 `ContextSeed.keys()` 过滤掉 ⇒ 激活集为空 ⇒ **一条经验都检索不到，而且不报错**。
    这条注释本身就是本特性反复在守的那类失败。
    """
    return ExperienceRuntime(
        _snapshot(), ExperienceRule(), Settings(ai_trade_funding_enabled=False),
        EventIdentity(group_id="grp", run_id=1, inst_id="BTC-USDT-SWAP", bar="1H"))


def _make_session(*, experience: ExperienceRuntime | None = None, dirty_calls=None):
    settings = Settings(ai_trade_funding_enabled=False)

    async def place_order_fn(order):
        return {"ok": True, "ordId": "x", "clOrdId": "aragonx"}

    async def close_position_fn(close):
        return {"ok": True, "clOrdId": "aragoncx"}

    async def positions_fn():
        return []

    async def fills_fn():
        return []

    executor = LiveExecutor(
        creds=AccountCredentials("ak", "sk-000000", "pp-000000"), account_id=1,
        inst_id="BTC-USDT-SWAP", td_mode="cross", pos_mode="long_short_mode",
        leverage=3.0, budget_usd=1000.0, risk=RiskParams(), ct_val=0.01, lot_sz=0.01,
        min_sz=0.01, settings=settings, place_order_fn=place_order_fn,
        close_position_fn=close_position_fn, positions_fn=positions_fn, fills_fn=fills_fn)
    decider = AiTradeDecider(_StubLLM(), "sys", context_refresh_rounds=50, settings=settings)
    spec = PaperStrategySpec(1, "S", "atomic", _StubStrategy({}), {})
    session = AiTradeSession(
        "aitrade_exp", 1, [spec], "BTC-USDT-SWAP", "1H", executor, decider,
        priming=False, priming_bars=10, context_refresh_rounds=50,
        experience=experience,
        on_experience_dirty=(lambda: dirty_calls.append(1)) if dirty_calls is not None else None)

    async def no_htf(*_a, **_k):
        return []

    session._window._safe_get_candles = no_htf
    return session


def _feed(session, count: int = 8) -> None:
    for i in range(count):
        session._window.append_confirmed(
            Candle(ts=_START_TS + i * _STEP, o=_PX, h=_PX + 5, l=_PX - 5,
                   c=_PX, vol=100.0, confirm=True))


def test_unarmed_session_leaves_the_payload_untouched():
    """未装配（空对象模式）⇒ payload **无** `experience` 键（不变式 I-2 的零回归路径）。"""
    session = _make_session()
    _feed(session)
    payload = session._build_decision_payload(1, _START_TS, _PX, [], {"trigger": "candle"})
    assert "experience" not in payload
    assert session._status_payload()["experience"] is None


def test_a31_inject_runs_after_wake_so_the_tier_machine_can_work():
    """A-31 · 经验层的 inject **必须**排在 `self._wake.inject(...)` 之后。

    档位判定读 `payload["contextDisclosure"]`，而那个键由 wake 层在 `_build_decision_payload`
    的最后一段写入。排在它之前，该键恒缺席 ⇒ 档位恒 `full` ⇒ §4.8 的整套压缩静默失效，
    而那正是本需求点名要解决的「上下文爆炸」。

    判据是可证伪的：**同一份 payload 上，`experience` 与 `contextDisclosure` 必须同时存在**，
    且第二轮（id 元组不变）的档位不再是 `full`。
    """
    runtime = _armed_runtime()
    session = _make_session(experience=runtime)
    _feed(session)

    first = session._build_decision_payload(1, _START_TS, _PX, [], {"trigger": "candle"})
    assert "experience" in first, "经验层没有注入 ⇒ 接线断了"
    assert first["experience"]["tier"] == "full"           # 会话首轮恒 full

    second = session._build_decision_payload(2, _START_TS + _STEP, _PX, [],
                                             {"trigger": "candle"})
    assert second["experience"]["tier"] != "full", (
        "第二轮档位仍是 full ⇒ 要么 id 元组在变，要么 inject 排在了 wake 之前"
        "（`contextDisclosure` 缺席 ⇒ 档位恒 full ⇒ 压缩静默失效）")


def test_a35_prompt_numbers_come_from_the_stock_not_from_this_session():
    """A-35（不变式 I-9 / 评审 F-3）· 页眉里的两个整数是**库存真值**，首轮也一样。

    此前它们是 `{"sessions": 0, "events": 本会话计数}` ⇒ 任何一次会话的第一轮，模型收到的都是
    「（来自 0 组有成交记录的自动交易的 0 笔已结算往返）」**紧跟着三条铁律**——一段自相矛盾的
    证据。**在喂给下真单的模型的 prompt 里写一个假数字，比不写这一段更坏。**
    """
    runtime = _armed_runtime()
    session = _make_session(experience=runtime)
    _feed(session)
    payload = session._build_decision_payload(1, _START_TS, _PX, [], {"trigger": "candle"})
    assert runtime.events_harvested == 0                  # 首轮：本会话一条都还没采
    assert payload["experience"]["totals"] == {"lessons": 148, "events": 2043, "sessions": 37}


async def test_a11_decision_round_opens_no_db_session():
    """A-11 · 决策轮**零 IO**：把 `get_sessionmaker` 打成会抛的桩，跑完一整轮不抛。

    决策轮阻塞 = 真实资金错过行情。图在会话启动时一次性载入内存，会话内的写只挂在**已有**的
    IO 点（`_dispatch` 的成交路径）。
    """
    import app.ai_auto.session as session_mod

    runtime = _armed_runtime()
    session = _make_session(experience=runtime)
    _feed(session)
    original = session_mod.get_sessionmaker

    def _explode():
        raise AssertionError("决策轮不得开 DB 会话（不变式 I-3）")

    session_mod.get_sessionmaker = _explode
    try:
        payload = session._build_decision_payload(1, _START_TS, _PX, [], {"trigger": "candle"})
        assert "experience" in payload
    finally:
        session_mod.get_sessionmaker = original


def test_a30_harvest_gap_reaches_both_status_and_run_metrics():
    """A-30 · 采不到的往返被**看见**：`status.experience` 与 `aiStats.experience` 各有一处落点。

    续跑接管与暂停期离线平仓这两条路径**恰好偏向亏损样本**，不露出就是教科书式的幸存者偏差。
    后端算了却没有呈现落点的观测量等于死码（I-8）。
    """
    runtime = _armed_runtime()
    runtime.note_close({"side": "close"}, {}, None)        # 无开仓断面 ⇒ 计一次缺口
    session = _make_session(experience=runtime)
    status = session._status_payload()
    assert status["experience"]["harvestGap"] == 1
    assert status["experience"]["graphAsOfTs"] > 0
    metrics = session._build_run_metrics()
    assert metrics["aiStats"]["experience"]["harvestGap"] == 1


def test_w13_round_start_trace_carries_the_injected_experience():
    """W-13 · `round_start` 轨迹带 `experience` 键。

    `new_round_trace` 是**显式取键**装配：漏抄这一键，`AiTradeRoundTrace` 永远拿不到本轮实际
    喂入的经验 ⇒ 后端发什么前端都收不到，**跑不出错、功能静默缺失**。
    """
    wire = {"tier": "full", "l0": [{"key": "lesson:x", "text": "t"}], "l1": []}
    trace = new_round_trace(1, _START_TS, _PX, {"experience": wire}, [])
    assert trace["experience"] == wire
    assert new_round_trace(1, _START_TS, _PX, {}, [])["experience"] is None


async def test_dirty_callback_fires_once_per_batch_and_is_not_awaited():
    """W-7② · 落盘成功后**同步**回调 manager；没有新事件时不回调。

    它跑在 `_dispatch` 这个「全部 executor 事件的唯一收口」上——await 它等于把后台物化的时延
    搬回热路径。
    """
    runtime = _armed_runtime()
    calls: list[int] = []
    _make_session(experience=runtime, dirty_calls=calls)

    assert runtime.take_dirty() is False       # 没落盘过 ⇒ 不脏
    runtime._dirty = True
    assert runtime.take_dirty() is True
    assert runtime.take_dirty() is False       # 取走即清（同一批事件只起一个任务）
    await asyncio.sleep(0)
