"""进程级单例与设置派生（ai-trade-audit-journal §3.1）。

范式沿用本仓库既有的四个 okx-link 单例：一个 `get_*()` + 一个 `reset_*()`，后者只给测试夹具用。
**`get_journal_writer()` 绝不自己 `start()`**——装配点唯一，在 `main.lifespan` 里；
一个「被读到就顺手起后台任务」的单例会在单测里悄悄拉起十几个 drain 循环
（本仓库在经验层已为此付过一次代价）。
"""
from __future__ import annotations

from ...config import Settings, get_settings
from .records import CaptureLimits
from .writer import JournalWriter, WriterConfig

_writer: JournalWriter | None = None


def writer_config_from_settings(settings: Settings) -> WriterConfig:
    """`Settings` → `WriterConfig`（**唯一**派生点）。"""
    return WriterConfig(
        enabled=bool(settings.ai_trade_journal_enabled),
        queue_max=int(settings.ai_trade_journal_queue_max),
        batch_size=int(settings.ai_trade_journal_batch_size),
        flush_interval_s=float(settings.ai_trade_journal_flush_interval_s),
        drain_timeout_s=float(settings.ai_trade_journal_drain_timeout_s),
        event_min_level=str(settings.ai_trade_journal_event_min_level))


def capture_limits_of(settings: Settings) -> CaptureLimits:
    """`Settings` → `CaptureLimits`（**唯一**派生点；三档裁剪的取数口径只有这一处）。

    ⚠️ 这个名字是**被一道既有门禁挑出来的**：情报层的 `G-7`
    （`test_ai_trade_intel_session_binding_core.py`）逐字断言 `session.py` 全文不含
    `limits_from_settings` 这个**子串**——那道门禁守的是「会话侧一个情报设置键都不许直读，
    生效值只经 `intel.binding.intel_limits_for` 取」。任何以 `..._limits_from_settings` 结尾
    的名字（含加前缀的）都会把它打红，故这里换成完全不同的构词。放宽那道门禁的代价，
    远大于在这里换一个名字。
    """
    return CaptureLimits(
        tier=str(settings.ai_trade_journal_capture_tier),
        text_max_chars=int(settings.ai_trade_journal_text_max_chars),
        payload_max_chars=int(settings.ai_trade_journal_payload_max_chars),
        context_chain=bool(settings.ai_trade_journal_context_chain_enabled))


def get_journal_writer() -> JournalWriter:
    """进程级单例。首次调用即按当前 `Settings` 构造（**不启动** drain task）。"""
    global _writer
    if _writer is None:
        from ...db.session import get_sessionmaker
        _writer = JournalWriter(writer_config_from_settings(get_settings()), get_sessionmaker)
    return _writer


def reset_journal_writer() -> None:
    """测试夹具用：丢弃单例，使下一次 `get_journal_writer()` 按当前 `Settings` 重建。"""
    global _writer
    if _writer is not None:
        _writer.reset()
    _writer = None


def start_journal_writer() -> None:
    """装配点（`main.lifespan` 调用一次）。总闸关 ⇒ `start()` 自身直接返回。"""
    get_journal_writer().start()


async def stop_journal_writer() -> None:
    """关停步（`main._shutdown_steps()` 的第 8 位）。有界 drain → cancel → await。"""
    await get_journal_writer().stop()
