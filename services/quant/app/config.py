"""应用配置 —— 从环境变量 / .env 读取 (spec §12)。

所有密钥仅存于环境变量，绝不下发前端、不入库、不进日志。
"""
from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """全局设置。字段名即环境变量名（大小写不敏感）。"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ---- OKX 端点 ----
    okx_rest_base: str = "https://www.okx.com"
    # ⚠️ candle{bar} 频道由 OKX 的 **business** WS 端点提供，public 端点会返回
    # "channel ... doesn't exist"。实测确认：/ws/v5/business 才能订阅到 K 线。
    okx_ws_public: str = "wss://ws.okx.com:8443/ws/v5/business"

    # ---- OKX 凭据（本期仅占位，公共行情不需要）----
    okx_api_key: str = ""
    okx_secret_key: str = ""
    okx_passphrase: str = ""

    # ---- 实盘账户（设置中心 · 实盘账户私有签名请求，spec §12）----
    # 字段名即环境变量名（OKX_ACCOUNT_*，大小写不敏感）；凭证真源为库内多账户，env 不参与。
    okx_account_demo: bool = False             # True 时私有请求附 x-simulated-trading:1（模拟盘）
    okx_account_timeout_seconds: float = 15.0  # 私有请求超时上限

    # ---- 代理（透传给 httpx / websockets）----
    http_proxy: str = ""
    https_proxy: str = ""
    all_proxy: str = ""

    # ---- 持久化 ----
    db_path: str = "aragon.db"

    # ---- 默认值 ----
    default_inst: str = "BTC-USDT-SWAP"
    default_bar: str = "1H"
    initial_capital: float = 10000.0

    # ---- 行为开关 ----
    allow_cached_fallback: bool = False
    max_paper_sessions: int = 12

    # ---- 撮合默认参数 (spec §6.1) ----
    default_taker_fee_bps: float = 5.0       # OKX 永续 taker ≈ 5 bps
    default_slippage_bps: float = 2.0
    default_leverage: float = 1.0

    # ---- Agent（对话式 AI 助手）LLM 接入 (Agent Trading Copilot spec §3.1) ----
    # 字段名即环境变量名（AGENT_*，大小写不敏感）；密钥仅存后端，绝不下发前端 / 不入库 / 不进日志。
    agent_provider: str = "anthropic"                       # anthropic | openai（双标准适配）
    agent_base_url: str = "https://api.kimi.com/coding"     # KIMI Coding 端点
    agent_api_key: str = ""                                 # 缺省为空 → 面板优雅引导而非崩溃
    # 默认采用任务显式指定值 kimi-k2.6；kimi-k2.5 为 docs/examples 已实测可用的回退值（R1/P1-A）。
    agent_model: str = "kimi-k2.6"
    agent_anthropic_version: str = "2023-06-01"
    agent_user_agent: str = "claude-code/1.0"               # KIMI 端点校验 UA（R2）
    agent_max_tokens: int = 2048
    agent_temperature: float = 1.0
    agent_top_p: float = 0.95
    # 失控护栏 / 上下文裁剪（RS-13 边界校验：轮数 ≥1、回喂条数 ≥2，杜绝 .env 误填 0/负值）。
    agent_max_tool_rounds: int = Field(default=12, ge=1)     # 失控护栏（spec §2.3）
    agent_max_context_messages: int = Field(default=40, ge=2)  # 回喂模型的近 N 条裁剪（P2-2）
    agent_timeout_seconds: float = 120.0
    # Agent LLM 独立代理：留空回落 settings.proxy_url；显式 "none" 强制直连（R5）。
    agent_proxy: str = ""
    # ---- 加固增强（iteration 2）：LLM 传输层有界重试 + 断连 drain 超时 (hardening H1/H4) ----
    # 字段名即 AGENT_* 环境变量名（大小写不敏感），可经 .env 覆盖。
    agent_max_retries: int = Field(default=2, ge=0)  # pre-stream 重试上限（最多 1+max 次，0 即不重试）
    agent_retry_base_ms: int = 500      # 指数退避基数（第 i 次退避 base*2**i，封顶 max）；RN-6：不新增校验
    agent_retry_max_ms: int = 8000      # 单次退避封顶（亦为 Retry-After 整数秒解析上限）；RN-6：不新增校验
    agent_drain_timeout_seconds: float = Field(default=8.0, gt=0)  # 断连后兜底 drain 超时
    # ---- 韧性流式（iteration 3）：run/socket 解耦续流 + 心跳保活（RS-1/RS-3/RS-7/RS-13）----
    # 字段名即 AGENT_* 环境变量名；缺省回落以下默认值，桌面端经白名单按需覆盖。
    agent_detach_grace_seconds: float = Field(default=30.0, ge=0)   # 断连存活宽限；0 即断连立即取消（回退迭代2）
    agent_heartbeat_seconds: float = Field(default=20.0, gt=0)      # run 期心跳间隔（须 < 中间层 idle 超时）
    agent_event_buffer_size: int = Field(default=512, ge=64)        # 会话事件缓冲上限（重放窗口）

    @property
    def proxy_url(self) -> str | None:
        """优先 HTTPS_PROXY，其次 HTTP_PROXY / ALL_PROXY；都没有则 None。"""
        return self.https_proxy or self.http_proxy or self.all_proxy or None

    @property
    def agent_proxy_url(self) -> str | None:
        """Agent LLM 调用专用代理（R5）：'none' → None（强制直连）；空 → 回落 proxy_url。"""
        if self.agent_proxy.strip().lower() == "none":
            return None
        return self.agent_proxy or self.proxy_url


@lru_cache
def get_settings() -> Settings:
    """单例配置。"""
    return Settings()
