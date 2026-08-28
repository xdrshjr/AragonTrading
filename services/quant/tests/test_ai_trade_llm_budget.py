"""三份 LLM 预算的装配单测（ai-trade-deep-reasoning-chain §7.4 B-1/B-2/B-5 + §7.1 G-5/G-6）。

根因 C-1 是「决策研判文字与工具调用 JSON 必须挤进同一个 2048」。本文件守的是那条修法真的落在
**三个调用点**上，以及它**没有**打翻 32 个假客户端赖以存在的那条契约（`stream` 签名不变）。
"""
from __future__ import annotations

import json

import pytest

from app.agent.llm.base import StreamEvent
from app.agent.llm.retry import RetryConfig
from app.agent.providers import LlmProviderConfig
from app.ai_auto.decider import AiTradeDecider
from app.ai_auto.llm_budget import build_ai_trade_llm_clients, resolve_thinking_budget
from app.ai_auto.reasoning_protocol import profile_by_name, resolve_depth_profile
from app.config import Settings


def _cfg(standard: str = "anthropic", **kw) -> LlmProviderConfig:
    base = dict(provider_id=None, slug=standard, label="t", api_standard=standard,
                base_url="https://x", api_key="k", model="m", user_agent="ua",
                max_tokens=2048, temperature=1.0, top_p=0.95, timeout=60.0,
                anthropic_version="2023-06-01", proxy=None, retry=RetryConfig())
    base.update(kw)
    return LlmProviderConfig(**base)


class _Spy:
    """记录被造出来的每一份 config（工厂注入点即测试注入缝，与生产同一条路径）。"""

    def __init__(self):
        self.configs: list[LlmProviderConfig] = []

    def __call__(self, cfg):
        self.configs.append(cfg)
        return object()


# ---------------------------------------------------------------- B-1 / B-2
def test_three_clients_get_three_budgets():
    """B-1：`deep` 档 ⇒ 决策 8192 / 反思 5120 / 摘要 1536（三者互不串味）。"""
    spy = _Spy()
    build_ai_trade_llm_clients(_cfg(), Settings(), profile=profile_by_name("deep"),
                               make_client=spy)
    assert [c.max_tokens for c in spy.configs] == [8192, 5120, 1536]


def test_explicit_setting_overrides_profile_budget():
    """B-2：`ai_trade_reasoning_max_tokens>0` 覆盖档位值；`0` ⇒ 用档位值。"""
    spy = _Spy()
    settings = Settings(ai_trade_reasoning_max_tokens=12000,
                        ai_trade_reflection_max_tokens=0,
                        ai_trade_summary_max_tokens=2048)
    build_ai_trade_llm_clients(_cfg(), settings, profile=resolve_depth_profile(settings),
                               make_client=spy)
    assert [c.max_tokens for c in spy.configs] == [12000, 5120, 2048]


def test_off_depth_keeps_the_single_shared_budget():
    """`off` 档 ⇒ 三者都用**原 cfg**，行为与今日的单 client 逐字一致。"""
    spy = _Spy()
    build_ai_trade_llm_clients(_cfg(), Settings(), profile=profile_by_name("off"),
                               make_client=spy)
    assert [c.max_tokens for c in spy.configs] == [2048, 2048, 2048]
    assert all(c.thinking_budget_tokens == 0 for c in spy.configs)


def test_non_dataclass_provider_stub_does_not_crash_the_assembly():
    """30+ 个用例以裸 `object()` 当 provider 配置：此时不可能拆预算，但装配必须起得来。"""
    spy = _Spy()
    clients = build_ai_trade_llm_clients(object(), Settings(),
                                         profile=profile_by_name("deep"), make_client=spy)
    assert clients.decision is not None and len(spy.configs) == 3


# ---------------------------------------------------------------- B-5（扩展思考预检）
def test_thinking_budget_only_lands_on_the_decision_client():
    """思考预算只施加在决策上：反思有自己的协议，摘要只是压缩流水，给它们开只会白烧 token。"""
    spy = _Spy()
    settings = Settings(agent_thinking_budget_tokens=4096)
    build_ai_trade_llm_clients(_cfg(thinking_budget_tokens=4096), settings,
                               profile=profile_by_name("deep"), make_client=spy)
    assert [c.thinking_budget_tokens for c in spy.configs] == [4096, 0, 0]


def test_thinking_budget_is_dropped_for_openai_standard():
    """B-4 的装配侧：openai 标准无扩展思考概念，显式不透传比静默忽略诚实。"""
    settings = Settings(agent_thinking_budget_tokens=4096)
    assert resolve_thinking_budget(_cfg("openai"), settings,
                                   profile=profile_by_name("deep")) == 0


def test_thinking_budget_larger_than_the_output_budget_is_refused(caplog):
    """B-5：思考 + 收尾预留吃掉整个输出预算 ⇒ 降为 0 并告警，**绝不发**一个必然截断的请求。

    可达性说明：`_check_reasoning_budget` 只校验**实盘生效档**，而这里的判据按**本会话真正
    生效的那一档**算——回测档（standard=4096）与启动请求里的降档覆盖都会走到这条分支，
    故这层运行期守卫不是死码。
    """
    settings = Settings(agent_thinking_budget_tokens=3500,
                        ai_trade_reasoning_reserve_tokens=768)
    with caplog.at_level("WARNING"):
        got = resolve_thinking_budget(_cfg(), settings, profile=profile_by_name("standard"))
    assert got == 0                                     # 4096 <= 3500 + 768
    assert "thinking budget" in caplog.text
    # 而实盘的 deep 档（8192 > 4268）不受影响——守卫只在真的放不下时才收紧。
    assert resolve_thinking_budget(_cfg(), settings, profile=profile_by_name("deep")) == 3500


def test_thinking_budget_is_zero_when_the_feature_is_off():
    """默认 0 ⇒ 请求体逐字节不变（G-4 的前置条件）。"""
    assert resolve_thinking_budget(_cfg(), Settings(), profile=profile_by_name("deep")) == 0
    assert resolve_thinking_budget(_cfg(), Settings(agent_thinking_budget_tokens=4096),
                                   profile=profile_by_name("off")) == 0


# ---------------------------------------------------------------- G-5（32 个假客户端零改动）
class _OneShotLLM:
    def __init__(self, name: str, sink: list[str]):
        self._name = name
        self._sink = sink

    async def stream(self, *, system, messages, tools):
        """签名与仓库里 32 个假客户端**逐字相同**——本特性刻意不改 `LLMClient.stream`。"""
        self._sink.append(self._name)
        yield StreamEvent("message_done", stop_reason="end_turn")


async def test_decider_without_extra_clients_uses_the_single_client():
    """G-5：只传一个 `llm` 构造决策器 ⇒ 反思与摘要都打在同一个假客户端上（零回归判据）。"""
    sink: list[str] = []
    decider = AiTradeDecider(_OneShotLLM("only", sink), "sys",
                             context_refresh_rounds=10,
                             settings=Settings(ai_trade_decision_max_retries=0,
                                               ai_trade_reflection_max_retries=0),
                             reflection_enabled=True)
    from app.ai_auto.decider import TradeDecision

    await decider.decide({"round": 1, "signalBoard": []})
    await decider.reflect({"round": 1, "signalBoard": []},
                          TradeDecision("open_long", 0.25, 0.8, "r"))
    await decider.summarize()
    assert sink == ["only", "only", "only"]


async def test_decider_routes_each_call_site_to_its_own_client():
    """三份预算真的被用上：决策 / 反思 / 摘要各打各的 client（否则拆预算等于没拆）。"""
    sink: list[str] = []
    from app.ai_auto.decider import TradeDecision

    decider = AiTradeDecider(_OneShotLLM("decision", sink), "sys",
                             context_refresh_rounds=10,
                             settings=Settings(ai_trade_decision_max_retries=0,
                                               ai_trade_reflection_max_retries=0),
                             reflection_enabled=True,
                             reflection_llm=_OneShotLLM("reflection", sink),
                             summary_llm=_OneShotLLM("summary", sink))
    await decider.decide({"round": 1, "signalBoard": []})
    await decider.reflect({"round": 1, "signalBoard": []},
                          TradeDecision("open_long", 0.25, 0.8, "r"))
    await decider.summarize()
    assert sink == ["decision", "reflection", "summary"]


# ---------------------------------------------------------------- B-6（启动即 fail-fast）
def test_settings_validator_rejects_impossible_budget():
    """B-6：预算容不下「思考 + 收尾工具调用」⇒ `Settings()` 构造即抛，消息里含三个数。"""
    with pytest.raises(Exception) as exc:
        Settings(ai_trade_reasoning_max_tokens=2048,
                 ai_trade_reasoning_reserve_tokens=4096)
    text = str(exc.value)
    assert "2048" in text and "4096" in text


def test_settings_validator_names_the_model_side_cap_for_exhaustive():
    """[P2-4]：`exhaustive` 档的报错必须点名「这是模型侧上限」，否则用户会在本地反复找。"""
    with pytest.raises(Exception) as exc:
        Settings(ai_trade_reasoning_depth="exhaustive",
                 ai_trade_reasoning_max_tokens=1024,
                 ai_trade_reasoning_reserve_tokens=4096)
    assert "模型侧上限" in str(exc.value)


def test_settings_validator_only_checks_the_active_depth():
    """只校验**当前生效的那一档**：一个用户永远用不到的档配得再离谱也不该让后端起不来。"""
    settings = Settings(ai_trade_reasoning_depth="off",
                        ai_trade_reasoning_max_tokens=1024,
                        ai_trade_reasoning_reserve_tokens=4096)
    assert settings.ai_trade_reasoning_depth == "off"


def test_settings_rejects_an_unknown_depth_at_the_boundary():
    """`Literal` 在入口挡住拼错的档名（`resolve_depth_profile` 的回落只是二次保险）。"""
    with pytest.raises(Exception):
        Settings(ai_trade_reasoning_depth="dep")


def test_reask_max_upper_bound_matches_what_the_decider_actually_does():
    """终审回归：追问总闸的上界必须是 1，不能接一个只生效一半的 2。

    `_maybe_reask` 全链只跑**一次**追问（§3.4.2 与架构图都写死「至多 1 次」）。若还接受 2，
    一个填 2 的运维开关会通过校验、然后静默地只做一半——本仓库反复栗过的「被静默忽略的旋钮」。
    """
    assert Settings(ai_trade_reasoning_reask_max=1).ai_trade_reasoning_reask_max == 1
    assert Settings(ai_trade_reasoning_reask_max=0).ai_trade_reasoning_reask_max == 0
    with pytest.raises(Exception):
        Settings(ai_trade_reasoning_reask_max=2)


def test_json_wire_of_the_reasoning_meta_is_camel_case():
    """`ReasoningMeta.wire()` 是 `result` 帧与 `aiStats` 共用的**唯一**一份装配。"""
    from app.ai_auto.decider import ReasoningMeta

    wire = ReasoningMeta("deep", 9412, 12, False, True, False, False, 12083, 3401, 4180, 7).wire()
    assert json.loads(json.dumps(wire))["reaskAdopted"] is False
    assert set(wire) == {"depth", "chars", "stages", "truncated", "reasked", "reaskAdopted",
                         "deadlineHit", "inputTokens", "outputTokens", "reflectionChars",
                         "reflectionStages"}
