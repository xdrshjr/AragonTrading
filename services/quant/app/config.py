"""应用配置 —— 从环境变量 / .env 读取 (spec §12)。

所有密钥仅存于环境变量，绝不下发前端、不入库、不进日志。
"""
from __future__ import annotations

from functools import lru_cache

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
    max_paper_sessions: int = 3

    # ---- 撮合默认参数 (spec §6.1) ----
    default_taker_fee_bps: float = 5.0       # OKX 永续 taker ≈ 5 bps
    default_slippage_bps: float = 2.0
    default_leverage: float = 1.0

    @property
    def proxy_url(self) -> str | None:
        """优先 HTTPS_PROXY，其次 HTTP_PROXY / ALL_PROXY；都没有则 None。"""
        return self.https_proxy or self.http_proxy or self.all_proxy or None


@lru_cache
def get_settings() -> Settings:
    """单例配置。"""
    return Settings()
