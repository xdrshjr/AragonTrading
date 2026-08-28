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
    # Anthropic 扩展思考预算（ai-trade-deep-reasoning-chain §3.3.2）：**末位带默认 0** ⇒ 既有
    # 全部位置/关键字构造零改动，且 0 = 不往请求体里写 `thinking` 字段 ⇒ 请求体逐字节不变
    # （用例 G-4 冻结）。仅 `api_standard == "anthropic"` 消费；openai 标准无此概念，
    # factory 显式不透传（显式不接比静默忽略诚实）。
    thinking_budget_tokens: int = 0


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
        thinking_budget_tokens=settings.agent_thinking_budget_tokens,
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
        thinking_budget_tokens=settings.agent_thinking_budget_tokens,
    )


async def resolve_active_provider(session_factory, settings: Settings) -> LlmProviderConfig | None:
    """显式默认权威解析：含 key 即生效；缺 key 视为「未配置」(引导补 key)，不静默回退 env。
    仅当「全无任何默认行」时才回落 env（纯 env 部署 / 删默认无含 key 行的兜底，H1）。"""
    async with session_factory() as session:
        row = await repo.get_default_agent_provider(session)
    if row is not None:
        if (row.api_key or "").strip():
            return config_from_row(row, settings)
        return None                      # 缺 key 的显式默认不再回退 env：交由 UI 引导补 key
    if (settings.agent_api_key or "").strip():
        return provider_config_from_settings(settings)
    return None


def _is_http_url(url: str) -> bool:
    return url.strip().lower().startswith(("http://", "https://"))


def _redact(text: str, secret: str) -> str:
    """把可能出现在错误文本中的密钥子串替换为脱敏占位（纵深防御，C7）。
    仅当密钥长度 ≥6 才替换，避免短串误伤无关文本。"""
    s = (secret or "").strip()
    if s and len(s) >= 6:
        text = text.replace(s, "····")
    return text


def _fail(code: str, message: str, api_key: str) -> dict:
    """探测失败统一收口：错误文本一律先脱敏再截断（C7 单点 redaction，杜绝散落遗漏）。"""
    return {"ok": False, "code": code, "message": _redact(message, api_key)[:200]}


async def probe_provider(cfg: LlmProviderConfig) -> dict:
    """连通性测试：先边界校验（空 key / 非法 url）避免无谓往返，再发一次极小请求（C5）。

    所有 ``ok is False`` 的对外 message 统一经 ``_redact`` 脱敏（H2）；密钥的真实对外
    暴露面是本函数返回的 message（流向前端 API 响应），故在此单点收口。该路径当前不写
    日志；若未来在此新增任何 logger 调用，须同样复用 ``_redact``。
    """
    if not (cfg.api_key or "").strip():
        return {"ok": False, "code": "AGENT_NOT_CONFIGURED", "message": "未填写密钥"}
    if not _is_http_url(cfg.base_url):
        return {"ok": False, "code": "INVALID_BASE_URL",
                "message": "APIURL 需以 http:// 或 https:// 开头"}
    timeout = min(cfg.timeout or _PROBE_TIMEOUT_CAP, _PROBE_TIMEOUT_CAP)
    started = time.perf_counter()
    try:
        probe_cfg = replace(cfg, max_tokens=min(cfg.max_tokens, _PROBE_MAX_TOKENS))
        client = make_llm_client_from_config(probe_cfg)   # H4：构造纳入兜底，异常不裸抛
        result = await asyncio.wait_for(_consume_probe(client, cfg.model, started), timeout=timeout)
    except asyncio.TimeoutError:
        return _fail("CONNECTION_FAILED", f"连通性测试超时（>{int(timeout)}s）", cfg.api_key)
    except Exception as exc:  # noqa: BLE001 —— 探测边界：任何异常归一为连接失败，绝不裸抛 / 不记 key
        return _fail("CONNECTION_FAILED", str(exc), cfg.api_key)
    if not result.get("ok"):              # _consume_probe 的 error 事件文本末次收口（②）
        result["message"] = _redact(result.get("message", ""), cfg.api_key)[:200]
    return result


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
