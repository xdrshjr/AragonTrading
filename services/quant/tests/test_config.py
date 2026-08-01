"""Application configuration defaults and environment overrides."""

import pytest
from pydantic import ValidationError

from app.config import Settings


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
