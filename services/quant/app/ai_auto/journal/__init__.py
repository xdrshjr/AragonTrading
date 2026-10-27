"""AI 自动交易的交易日志层（ai-trade-audit-journal）。

**一条贯穿全包的原则**：日志层只读取，**绝不参与任何判定**。本包下的任何模块都不得被
`session.py` / `decider.py` / `manager.py` 的**判定表达式**引用；它们只被 `session.py` 的三个
写点、`manager.py` 的六个事件写点与 `main.py` 的编排点调用（由 AST 门禁 G-3 冻结）。

包出口只有下面这几个符号，别的一律私有。
"""
from __future__ import annotations

from .records import (
    CaptureLimits,
    DeciderSnapshot,
    EventJournalInput,
    JournalScope,
    RoundJournalInput,
    build_decision_row,
    build_event_row,
)
from .runtime import (
    get_journal_writer,
    capture_limits_of,
    reset_journal_writer,
    start_journal_writer,
    stop_journal_writer,
)

__all__ = [
    "CaptureLimits",
    "DeciderSnapshot",
    "EventJournalInput",
    "JournalScope",
    "RoundJournalInput",
    "build_decision_row",
    "build_event_row",
    "get_journal_writer",
    "capture_limits_of",
    "reset_journal_writer",
    "start_journal_writer",
    "stop_journal_writer",
]
