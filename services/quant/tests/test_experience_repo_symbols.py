"""门禁 G-8 · 数据层符号齐备（ai-trade-experience-context-graph 评审 P0-1 的机械判据）。

**为什么必须是这条用例，而不是「跑一次物化看看」**：`experience` 包里每一处 `repo.<name>` 的
`AttributeError` 都会落进某个 `except Exception` 并被吞成一行 `warning`——跑一次物化是**绿的**，
日志全绿、门禁全过、零回归断言也全绿，而一个字都不喂。只有符号齐备性检查能看见它。

节点 #1 的实测就是这么抓到 `upsert_lesson_nodes` 未被再导出的（16 个符号里唯一缺席的那个，
恰好是物化的**唯一写路径**）。这条用例让以后任何一次漏再导出都无处可藏。
"""
from __future__ import annotations

import re
from pathlib import Path

from app.db import repositories as repo

_PACKAGE = Path(__file__).resolve().parents[1] / "app" / "ai_auto" / "experience"
_REPO_CALL = re.compile(r"\brepo\.([A-Za-z_][A-Za-z0-9_]*)")


def _referenced_symbols() -> set[str]:
    """扫出 `app/ai_auto/experience/**` 里全部 `repo.<name>` 引用。"""
    names: set[str] = set()
    for path in sorted(_PACKAGE.rglob("*.py")):
        names.update(_REPO_CALL.findall(path.read_text(encoding="utf-8")))
    return names


def test_every_repo_symbol_used_by_experience_package_is_reexported():
    referenced = _referenced_symbols()
    assert referenced, "扫不到任何 repo.<name>——正则或包路径坏了，这条门禁会假绿"
    missing = sorted(name for name in referenced if not hasattr(repo, name))
    assert missing == [], (
        f"`app.db.repositories` 缺少再导出：{missing}。这类缺席**不会**让任何用例变红——"
        "调用点的 AttributeError 会被 except Exception 吞成一行 warning，图因此永不建立，"
        "而日志全绿。")


def test_materialize_write_path_is_reexported():
    """`upsert_lesson_nodes` 是物化的**唯一**写路径，单独钉一条（它就是 P0-1 本身）。"""
    assert hasattr(repo, "upsert_lesson_nodes")
    assert "upsert_lesson_nodes" in _referenced_symbols(), (
        "没有任何地方调它了？那要么写路径被换掉（评审 P0-2 明令禁止第二份 SQL），"
        "要么这条用例已经失去意义——两种情况都需要人来看一眼。")
