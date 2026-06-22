"""probe_provider 单测：假 LLMClient 注入 → ok / error / 异常；空 key/非法 url 边界先校验不发网络。"""
from __future__ import annotations

from app.agent.llm.base import StreamEvent
from app.agent.llm.retry import RetryConfig
from app.agent.providers import LlmProviderConfig, probe_provider


def _cfg(*, api_key="k-secret-1234", base_url="https://probe.test", model="m1") -> LlmProviderConfig:
    return LlmProviderConfig(
        provider_id=1, slug="s", label="L", api_standard="openai", base_url=base_url,
        api_key=api_key, model=model, user_agent="ua", max_tokens=2048, temperature=1.0,
        top_p=0.95, timeout=15.0, anthropic_version="2023-06-01", proxy=None,
        retry=RetryConfig())


class _OkLLM:
    async def stream(self, *, system, messages, tools):
        assert tools == []  # probe 用 tools=[]，client 不发 tools/tool_choice
        yield StreamEvent(kind="text_delta", text="pong")
        yield StreamEvent(kind="message_done", stop_reason="end_turn")


class _ErrLLM:
    async def stream(self, *, system, messages, tools):
        yield StreamEvent(kind="error", code="AGENT_AUTH_ERROR", message="密钥无效")


class _RaisingLLM:
    async def stream(self, *, system, messages, tools):
        raise RuntimeError("boom k-secret-1234")  # 异常消息含 key，断言不外泄到 code
        yield  # pragma: no cover


def _patch_client(monkeypatch, client) -> None:
    monkeypatch.setattr("app.agent.providers.make_llm_client_from_config", lambda cfg: client)


async def test_probe_ok_returns_latency_and_model(monkeypatch):
    _patch_client(monkeypatch, _OkLLM())
    result = await probe_provider(_cfg())
    assert result["ok"] is True
    assert isinstance(result["latencyMs"], (int, float)) and result["model"] == "m1"
    assert "k-secret-1234" not in str(result)  # 绝不外泄 key


async def test_probe_error_event_maps_to_code(monkeypatch):
    _patch_client(monkeypatch, _ErrLLM())
    result = await probe_provider(_cfg())
    assert result["ok"] is False and result["code"] == "AGENT_AUTH_ERROR"


async def test_probe_exception_maps_to_connection_failed(monkeypatch):
    _patch_client(monkeypatch, _RaisingLLM())
    result = await probe_provider(_cfg())
    assert result["ok"] is False and result["code"] == "CONNECTION_FAILED"


async def test_probe_exception_message_redacts_key(monkeypatch):
    """H2：异常文本含密钥时，对外 message 不得出现明文（脱敏单点收口）。"""
    _patch_client(monkeypatch, _RaisingLLM())  # 抛 "boom k-secret-1234"
    result = await probe_provider(_cfg(api_key="k-secret-1234"))
    assert result["ok"] is False
    assert "k-secret-1234" not in str(result)  # 含 message 在内整体不含明文
    assert "····" in result["message"]          # 已被脱敏占位替换


async def test_probe_error_event_message_redacts_key(monkeypatch):
    """H2：error 事件 message 含密钥时同样脱敏（②路径经末次收口）。"""

    class _ErrWithKeyLLM:
        async def stream(self, *, system, messages, tools):
            yield StreamEvent(kind="error", code="AGENT_AUTH_ERROR",
                              message="rejected token k-secret-1234")

    _patch_client(monkeypatch, _ErrWithKeyLLM())
    result = await probe_provider(_cfg(api_key="k-secret-1234"))
    assert result["ok"] is False and result["code"] == "AGENT_AUTH_ERROR"
    assert "k-secret-1234" not in str(result)


async def test_probe_factory_construction_error_no_500(monkeypatch):
    """H4：client 构造（factory）抛错也归一 CONNECTION_FAILED，绝不裸抛为 500。"""

    def _raise(cfg):
        raise ValueError("bad proxy config k-secret-1234")

    monkeypatch.setattr("app.agent.providers.make_llm_client_from_config", _raise)
    result = await probe_provider(_cfg(api_key="k-secret-1234"))
    assert result["ok"] is False and result["code"] == "CONNECTION_FAILED"
    assert "k-secret-1234" not in str(result)  # 构造期异常文本同样脱敏


async def test_probe_empty_key_short_circuits_without_network(monkeypatch):
    called = {"n": 0}

    def _boom(cfg):
        called["n"] += 1
        raise AssertionError("不应构造 client")

    monkeypatch.setattr("app.agent.providers.make_llm_client_from_config", _boom)
    result = await probe_provider(_cfg(api_key=""))
    assert result["ok"] is False and result["code"] == "AGENT_NOT_CONFIGURED"
    assert called["n"] == 0  # 边界先校验，绝不发网络


async def test_probe_invalid_url_short_circuits(monkeypatch):
    monkeypatch.setattr("app.agent.providers.make_llm_client_from_config",
                        lambda cfg: (_ for _ in ()).throw(AssertionError("不应构造 client")))
    result = await probe_provider(_cfg(base_url="ftp://bad"))
    assert result["ok"] is False and result["code"] == "INVALID_BASE_URL"
