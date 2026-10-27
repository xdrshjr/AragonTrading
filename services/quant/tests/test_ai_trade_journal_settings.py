"""16 项设置的双向登记与四条启动期校验（ai-trade-audit-journal §七）。

`.env.example` 的双向校验不是形式主义：prod 的 PyInstaller 后端**不读 `.env`、只读进程 env**，
而桌面壳按 `AI_TRADE_` 前缀从 `agent.env` 透传——一个没写进 `.env.example` 的旋钮，用户在打包
形态下**没有任何途径**知道它存在；反过来，一个写在 `.env.example` 却已被删掉的键，是一句会被
照抄进真实配置的废话。**漏登记是静默的**，本仓库已为此栽过两次。
"""
from __future__ import annotations

import pathlib
import re

import pytest

from app.config import JOURNAL_CAPTURE_TIERS, JOURNAL_EVENT_LEVELS, Settings

_ROOT = pathlib.Path(__file__).resolve().parents[3]
EXPECTED_SETTING_COUNT = 16


def _declared_fields() -> set[str]:
    cfg = (_ROOT / "services" / "quant" / "app" / "config.py").read_text(encoding="utf-8")
    return {name.upper() for name in
            re.findall(r"^    (ai_trade_journal_[a-z0-9_]+)\s*:", cfg, re.M)}


def _env_keys() -> set[str]:
    env = (_ROOT / ".env.example").read_text(encoding="utf-8")
    return set(re.findall(r"^(AI_TRADE_JOURNAL_[A-Z0-9_]+)=", env, re.M))


def test_every_journal_setting_is_registered_in_env_example():
    fields, declared = _declared_fields(), _env_keys()
    assert fields, "正则没匹配到任何字段 ⇒ 本用例已经失效，先修它"
    assert fields - declared == set(), f"这些设置没写进 .env.example：{sorted(fields - declared)}"
    assert declared - fields == set(), f".env.example 里有已不存在的键：{sorted(declared - fields)}"


def test_the_family_has_exactly_sixteen_knobs():
    """计数断言存在的理由：设计承诺的是 16 项，多一项少一项都该在评审里被看见。"""
    assert len(_declared_fields()) == EXPECTED_SETTING_COUNT


def test_defaults_are_the_documented_ones():
    s = Settings()
    assert s.ai_trade_journal_enabled is True
    assert s.ai_trade_journal_capture_tier == "standard"
    assert s.ai_trade_journal_queue_max == 2000
    assert s.ai_trade_journal_batch_size == 32
    assert s.ai_trade_journal_retention_days == 90
    assert s.ai_trade_journal_max_rows == 100000
    assert s.ai_trade_journal_export_max_rows == 200000
    assert s.ai_trade_journal_ui_enabled is True
    assert s.ai_trade_journal_context_chain_enabled is True
    assert s.ai_trade_journal_export_time_budget_s == 240.0


def test_drain_deadline_must_be_under_the_shutdown_step_timeout():
    """倒挂时外层先 cancel、内层清理永不执行，写队列会活着穿过 dispose_engine。"""
    with pytest.raises(ValueError, match="AI_TRADE_JOURNAL_DRAIN_TIMEOUT_S"):
        Settings(ai_trade_journal_drain_timeout_s=25.0, shutdown_step_timeout_s=20.0)
    Settings(ai_trade_journal_drain_timeout_s=19.9, shutdown_step_timeout_s=20.0)


def test_capture_tier_value_domain_is_enforced_at_construction():
    with pytest.raises(ValueError, match="AI_TRADE_JOURNAL_CAPTURE_TIER"):
        Settings(ai_trade_journal_capture_tier="verbose")
    for tier in JOURNAL_CAPTURE_TIERS:
        Settings(ai_trade_journal_capture_tier=tier)


def test_event_min_level_value_domain_is_enforced_at_construction():
    with pytest.raises(ValueError, match="AI_TRADE_JOURNAL_EVENT_MIN_LEVEL"):
        Settings(ai_trade_journal_event_min_level="verbose")
    for level in JOURNAL_EVENT_LEVELS:
        Settings(ai_trade_journal_event_min_level=level)


def test_batch_size_must_fit_in_the_queue():
    with pytest.raises(ValueError, match="AI_TRADE_JOURNAL_BATCH_SIZE"):
        Settings(ai_trade_journal_batch_size=64, ai_trade_journal_queue_max=32)
    Settings(ai_trade_journal_batch_size=32, ai_trade_journal_queue_max=32)


def test_export_time_budget_is_zero_or_at_least_ten_seconds():
    """小于 10 秒的预算会让每一次导出都在首页之后立刻收尾——用户拿到的是一串空文件。"""
    with pytest.raises(ValueError, match="AI_TRADE_JOURNAL_EXPORT_TIME_BUDGET_S"):
        Settings(ai_trade_journal_export_time_budget_s=3.0)
    Settings(ai_trade_journal_export_time_budget_s=0.0)     # 该闸不生效（直连排障）
    Settings(ai_trade_journal_export_time_budget_s=10.0)
