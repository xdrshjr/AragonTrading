"""Application configuration defaults and environment overrides."""

import pytest
from pydantic import ValidationError

from app.config import Settings


def test_ai_trade_is_enabled_by_default(monkeypatch):
    monkeypatch.delenv("AI_TRADE_ENABLED", raising=False)

    settings = Settings(_env_file=None)

    assert settings.ai_trade_enabled is True


def test_ai_trade_can_be_explicitly_disabled(monkeypatch):
    monkeypatch.setenv("AI_TRADE_ENABLED", "false")

    settings = Settings(_env_file=None)

    assert settings.ai_trade_enabled is False


# ---------- 唤醒调度闭环（ai-trade-wake-scheduler-closed-loop §4.2 / C-7）----------
def test_wake_scheduler_defaults_ship_axis_c_switched_off():
    """轴 A / B 默认开、轴 C 默认**关**。

    轴 C 是唯一会占用守着真实资金的 5 秒盯市 tick 与线程池的一条，镜像 signal-prior 那轮
    「新轴默认全关、验收达标再翻」的既有做法。晋级需要一次真实会话 ≥24 小时的人工观察窗。
    """
    settings = Settings(_env_file=None)

    assert settings.ai_trade_wake_backlog_enabled is True
    assert settings.ai_trade_wake_backlog_max_chain == 3
    assert settings.ai_trade_wake_skip_position_watch is True
    assert settings.ai_trade_wake_position_watch_max_skip_streak == 3
    assert settings.ai_trade_signal_wake_enabled is False
    assert settings.ai_trade_signal_wake_hold_s == 45.0
    assert settings.ai_trade_signal_wake_min_samples == 3
    assert settings.ai_trade_signal_wake_min_gap_s == 120.0
    assert settings.ai_trade_signal_wake_preview_interval_s == 15.0
    assert settings.ai_trade_signal_wake_timeout_s == 3.0
    assert settings.ai_trade_signal_wake_stale_s == 90.0


@pytest.mark.parametrize(("field", "value"), [
    # 两个上限**没有**「0=不封顶」这条路：一个是无限连烧 LLM，一个是持仓期无限期不研判。
    # 「关掉」的语义各自的总闸已经提供，不需要第二条通往危险形态的路。
    ("ai_trade_wake_backlog_max_chain", 0),
    ("ai_trade_wake_position_watch_max_skip_streak", 0),
    # `hold_s=0 + min_samples=1` 是「零防抖」，正是本轴最怕的 repaint 放大器。
    ("ai_trade_signal_wake_min_samples", 1),
    ("ai_trade_signal_wake_min_gap_s", 29.0),
    ("ai_trade_signal_wake_preview_interval_s", 4.0),
    ("ai_trade_signal_wake_timeout_s", 0.0),
    ("ai_trade_signal_wake_stale_s", 0.0),
])
def test_unsafe_wake_scheduler_values_are_rejected_at_the_boundary(field, value):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{field: value})


@pytest.mark.parametrize(("field", "value"), [
    ("ai_trade_wake_backlog_max_chain", 1),
    ("ai_trade_wake_backlog_max_chain", 20),
    ("ai_trade_wake_position_watch_max_skip_streak", 1),
    ("ai_trade_signal_wake_min_samples", 2),
    ("ai_trade_signal_wake_hold_s", 0.0),          # 0 = 只看样本数（合法：双条件仍留一条）
    ("ai_trade_signal_wake_min_gap_s", 30.0),
    ("ai_trade_signal_wake_preview_interval_s", 5.0),
])
def test_wake_scheduler_boundary_values_are_accepted(field, value):
    assert getattr(Settings(_env_file=None, **{field: value}), field) == value


def test_wake_scheduler_env_names_use_the_desktop_passthrough_prefix(monkeypatch):
    """全部新键都在 desktop `config.js` 的 `AI_TRADE_` 前缀透传白名单内 ⇒ 无需改 desktop。

    但 `Settings` 的 `extra="ignore"` 会**静默丢弃**未声明的 env——本用例证明它们真的被声明了。
    """
    monkeypatch.setenv("AI_TRADE_SIGNAL_WAKE_ENABLED", "true")
    monkeypatch.setenv("AI_TRADE_WAKE_SKIP_POSITION_WATCH", "false")
    monkeypatch.setenv("AI_TRADE_WAKE_BACKLOG_MAX_CHAIN", "7")

    settings = Settings(_env_file=None)

    assert settings.ai_trade_signal_wake_enabled is True
    assert settings.ai_trade_wake_skip_position_watch is False
    assert settings.ai_trade_wake_backlog_max_chain == 7


# ---------------- OKX 链路韧性加固（okx-link-resilience §六）----------------


def test_okx_link_defaults_match_the_spec_table():
    """22 个 `okx_link_*` 的默认值即 spec §六 的表。

    默认值本身就是安全承诺：熔断默认**开**（5 次）之所以可接受，前提是探针豁免名单已落实
    （§2.3.3）——否则一次瞬时公共读故障会经 `health() → okxUp → shouldStreamMarket` 主动拆掉
    浏览器行情流最长 120 秒，加固机制自己制造断线。二者取其一，不许「豁免没做、默认还开着」。
    """
    s = Settings(_env_file=None)
    assert (s.okx_link_ws_ping_interval_s, s.okx_link_ws_pong_timeout_s) == (15.0, 8.0)
    assert (s.okx_link_ws_silence_timeout_s, s.okx_link_ws_subscribe_timeout_s) == (45.0, 10.0)
    assert s.okx_link_ws_healthy_after_s == 20.0
    assert (s.okx_link_ws_backoff_base_s, s.okx_link_ws_backoff_max_s) == (1.0, 30.0)
    assert s.okx_link_ws_connect_rate_per_s == 2.0
    assert (s.okx_link_rest_max_attempts, s.okx_link_rest_budget_s) == (4, 25.0)
    assert (s.okx_link_public_rate_per_s, s.okx_link_public_burst) == (8.0, 4.0)
    assert (s.okx_link_private_rate_per_s, s.okx_link_private_burst) == (5.0, 5.0)
    assert (s.okx_link_breaker_failures, s.okx_link_breaker_cooldown_s) == (5, 20.0)
    assert s.okx_link_pool_idle_ttl_s == 120.0
    assert s.okx_link_clock_sync_interval_s == 900.0
    assert s.okx_link_gap_repair_max_bars == 300
    assert s.okx_link_down_alert_s == 20.0
    assert (s.okx_link_verify_attempts, s.okx_link_verify_backoff_s) == (3, 0.6)


def test_okx_link_env_names_use_the_desktop_passthrough_prefix(monkeypatch):
    """全部新键都以 `OKX_` 开头 ⇒ 落在 desktop `config.js` 新加的透传白名单内。

    `Settings` 的 `extra="ignore"` 会**静默丢弃**未声明的 env，所以「字段真的被声明了」这件事
    必须有一条会红的用例；打包侧那半由 `desktop/scripts/config.test.mjs` 守。
    """
    monkeypatch.setenv("OKX_LINK_WS_PING_INTERVAL_S", "5")
    monkeypatch.setenv("OKX_LINK_BREAKER_FAILURES", "0")
    monkeypatch.setenv("OKX_LINK_GAP_REPAIR_MAX_BARS", "0")

    settings = Settings(_env_file=None)

    assert settings.okx_link_ws_ping_interval_s == 5.0
    assert settings.okx_link_breaker_failures == 0      # 0 = 整体关闭熔断
    assert settings.okx_link_gap_repair_max_bars == 0   # 0 = 不补根
