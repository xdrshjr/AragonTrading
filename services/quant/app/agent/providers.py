"""设置中心 · AI 模型服务：provider 配置解析与连通性探测 (spec §2.2)。

把「库内服务商行 / 环境变量」统一解析为 `LlmProviderConfig`（喂 factory 构造 client），
并提供一次极小请求的连通性探测 `probe_provider`。tuning（max_tokens / 超时 / 代理 / 重试 等）
统一取自 `Settings`，provider 行只承载 api_standard / base_url / api_key / model / user_agent，
避免重复与漂移（P2-Settings）。密钥绝不进日志（C7）。
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, replace

from ..config import Settings
from ..db import repositories as repo
from ..db.models import AgentProvider
from .llm.base import LlmMessage, TextBlock
from .llm.factory import make_llm_client_from_config
from .llm.retry import RetryConfig

# 连通性探测上限（秒）：与极小 max_tokens 配合，避免无谓阻塞（R-Probe）。
_PROBE_TIMEOUT_CAP = 15.0
_PROBE_MAX_TOKENS = 16


@dataclass(frozen=True)
class LlmProviderConfig:
    """解析出的「当前生效服务」——喂给 factory 构造 client。tuning 取自 Settings。"""

    provider_id: int | None       # 库内行 id（env 回落为 None）
    slug: str                     # openai|anthropic|gemini|kimi|<uuid>
    label: str
    api_standard: str             # "anthropic" | "openai"
    base_url: str
    api_key: str
    model: str
    user_agent: str               # anthropic/kimi 必需 UA；openai 忽略
    # —— 以下 tuning 统一取自 Settings（provider 行不单独存）——
    max_tokens: int
    temperature: float
    top_p: float
    timeout: float
    anthropic_version: str
    proxy: str | None
    retry: RetryConfig


@dataclass(frozen=True)
class PresetMeta:
    """官方预置元信息（key 留空，待用户填）。"""

    slug: str
    label: str
    api_standard: str
    base_url: str
    model: str
    user_agent: str = "claude-code/1.0"


# 三个固定官方预置：base_url 须与 client URL 构造匹配——根 URL 由 client 追加 /v1/…，
# 整端点（Gemini）原样直通（P1-URL）。KIMI 第 4 个 official 由 seed 据 .env 单独种入。
OFFICIAL_PRESETS: list[PresetMeta] = [
    PresetMeta(slug="openai", label="OpenAI", api_standard="openai",
               base_url="https://api.openai.com", model="gpt-4o"),
    PresetMeta(slug="anthropic", label="Anthropic", api_standard="anthropic",
               base_url="https://api.anthropic.com", model="claude-sonnet-4-6"),
    PresetMeta(slug="gemini", label="Gemini", api_standard="openai",
               base_url="https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
               model="gemini-2.0-flash"),
]


def _retry_from_settings(settings: Settings) -> RetryConfig:
    return RetryConfig(max_retries=settings.agent_max_retries,
                       base_ms=settings.agent_retry_base_ms,
                       max_ms=settings.agent_retry_max_ms)


def provider_config_from_settings(settings: Settings) -> LlmProviderConfig:
    """把 `settings.agent_*`（env / .env）包成 config（env 回落 & probe 复用）。"""
    return LlmProviderConfig(
        provider_id=None,
        slug=(settings.agent_provider or "anthropic").strip().lower(),
        label="环境变量（.env）",
        api_standard=(settings.agent_provider or "anthropic").strip().lower(),
        base_url=settings.agent_base_url,
        api_key=settings.agent_api_key,
        model=settings.agent_model,
        user_agent=settings.agent_user_agent,
        max_tokens=settings.agent_max_tokens,
        temperature=settings.agent_temperature,
        top_p=settings.agent_top_p,
        timeout=settings.agent_timeout_seconds,
        anthropic_version=settings.agent_anthropic_version,
        proxy=settings.agent_proxy_url,
        retry=_retry_from_settings(settings),
    )


def config_from_row(row: AgentProvider, settings: Settings) -> LlmProviderConfig:
    """库行（api_standard/base_url/api_key/model/user_agent）+ Settings tuning 合并。"""
    return LlmProviderConfig(
        provider_id=row.id,
        slug=row.slug,
        label=row.label,
        api_standard=(row.api_standard or "anthropic").strip().lower(),
        base_url=row.base_url,
        api_key=row.api_key or "",
        model=row.model,
        user_agent=row.user_agent or settings.agent_user_agent,
        max_tokens=settings.agent_max_tokens,
        temperature=settings.agent_temperature,
        top_p=settings.agent_top_p,
        timeout=settings.agent_timeout_seconds,
        anthropic_version=settings.agent_anthropic_version,
        proxy=settings.agent_proxy_url,
        retry=_retry_from_settings(settings),
    )


async def resolve_active_provider(session_factory, settings: Settings) -> LlmProviderConfig | None:
    """三级回落解析当前生效服务：库内默认（含 key）→ env → None（未配置）。"""
    async with session_factory() as session:
        row = await repo.get_default_agent_provider(session)
    if row is not None and (row.api_key or "").strip():
        return config_from_row(row, settings)
    if (settings.agent_api_key or "").strip():
        return provider_config_from_settings(settings)
    return None


def _is_http_url(url: str) -> bool:
    return url.strip().lower().startswith(("http://", "https://"))


async def probe_provider(cfg: LlmProviderConfig) -> dict:
    """连通性测试：先边界校验（空 key / 非法 url）避免无谓往返，再发一次极小请求（C5）。"""
    if not (cfg.api_key or "").strip():
        return {"ok": False, "code": "AGENT_NOT_CONFIGURED", "message": "未填写密钥"}
    if not _is_http_url(cfg.base_url):
        return {"ok": False, "code": "INVALID_BASE_URL",
                "message": "APIURL 需以 http:// 或 https:// 开头"}
    probe_cfg = replace(cfg, max_tokens=min(cfg.max_tokens, _PROBE_MAX_TOKENS))
    client = make_llm_client_from_config(probe_cfg)
    timeout = min(cfg.timeout or _PROBE_TIMEOUT_CAP, _PROBE_TIMEOUT_CAP)
    started = time.perf_counter()
    try:
        return await asyncio.wait_for(_consume_probe(client, cfg.model, started), timeout=timeout)
    except asyncio.TimeoutError:
        return {"ok": False, "code": "CONNECTION_FAILED",
                "message": f"连通性测试超时（>{int(timeout)}s）"}
    except Exception as exc:  # noqa: BLE001 —— 探测边界：任何异常归一为连接失败，绝不裸抛 / 不记 key
        return {"ok": False, "code": "CONNECTION_FAILED", "message": str(exc)[:200]}


async def _consume_probe(client, model: str, started: float) -> dict:
    """消费探测流：首个文本 / 收尾 → 连通+时延；error → 错误码；空流 → 连接失败。"""
    async for ev in client.stream(
            system="connectivity probe",
            messages=[LlmMessage("user", [TextBlock("ping")])], tools=[]):
        if ev.kind == "error":
            return {"ok": False, "code": ev.code or "LLM_ERROR",
                    "message": ev.message or "连通性测试失败"}
        if ev.kind in ("text_delta", "message_done"):
            return {"ok": True, "latencyMs": round((time.perf_counter() - started) * 1000, 1),
                    "model": model}
    return {"ok": False, "code": "CONNECTION_FAILED", "message": "未收到模型响应"}
