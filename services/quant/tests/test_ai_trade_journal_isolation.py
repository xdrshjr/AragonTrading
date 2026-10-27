"""隔离门禁：日志层只读取，**绝不参与任何判定**（ai-trade-audit-journal §2.1 / §8.1）。

四道 AST 门禁：

- **G-3**：`journal/` 下的任何符号都不得出现在 `session.py` / `decider.py` / `manager.py` 的
  **判定表达式**里（`if` / `while` 的条件、断言、三元表达式）。
- **G-4**：`ai_auto/backtest.py` 与 `backtest_manager.py` 不得 import 日志包、不得引用它的
  任何符号——回测是合成数据，混进后训练集会污染样本。
- **G-5**：`journal/records.py` 与 `redact.py` 里**没有 `raise` 语句**。它们跑在决策轮的
  `try` 内，一次异常会把一轮已经成交的决策翻成 `AI_TRADE_ROUND_FAILED`。
- **A-1（两条）**：`session._journal_round` / `_journal_event` 的函数体内 `ast.Await` 计数为
  0，**且**两个方法体的顶层语句恰好是一个覆盖 `Exception` 的 `ast.Try`。只查前者是设计 v1 的
  缺口——「不会 await」与「不会抛」是两件不同的事，而序列化恰好是会抛的那一件。
"""
from __future__ import annotations

import ast
import pathlib

_AI_AUTO = pathlib.Path(__file__).resolve().parents[1] / "app" / "ai_auto"
_JOURNAL = _AI_AUTO / "journal"


def _tree(path: pathlib.Path) -> ast.AST:
    return ast.parse(path.read_text(encoding="utf-8"))


def _method(path: pathlib.Path, name: str) -> ast.FunctionDef:
    for node in ast.walk(_tree(path)):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{path.name} 里找不到方法 {name}")


# 交易日志包对外暴露的符号（G-4 扫的是**这些**，不是 `journal` 这个词）。
_JOURNAL_SYMBOLS = frozenset((
    "get_journal_writer", "build_decision_row", "build_event_row", "build_gates",
    "RoundJournalInput", "EventJournalInput", "JournalScope", "DeciderSnapshot",
    "limits_from_settings", "start_journal_writer", "stop_journal_writer",
    "_journal_round", "_journal_event", "_journal_gates", "_journal_scope",
    "_journal_group_event", "_journal_decider_snapshot",
))


def test_backtest_lane_never_touches_the_journal():
    """G-4：回测车道不得接线交易日志——回测是合成数据，混进后训练集会污染样本。

    ⚠️ 判据**不能**是「全文 0 次 `journal`」（spec §8.1 的字面写法）：`decider` 早在本特性
    之前就有一个叫 `_journal` 的滚动摘要素材表，`backtest.py` 的注释里逐字引用它十余处。
    按字面扫会把一段与本特性无关的既有注释判成违规——一道会因为无关文字变红的门禁，
    下一个人只会把它放宽或删掉。故判据取 **AST 的 import 与符号引用**：既不可能被注释影响，
    也真的拦得住「回测悄悄接上了日志层」这件事本身。
    """
    for name in ("backtest.py", "backtest_manager.py"):
        tree = _tree(_AI_AUTO / name)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert "journal" not in (node.module or ""), (
                    f"{name} import 了 journal 包——回测不得接线交易日志")
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    assert "journal" not in alias.name, f"{name} import 了 journal 包"
            label = getattr(node, "id", None) or getattr(node, "attr", None)
            assert label not in _JOURNAL_SYMBOLS, f"{name} 引用了日志层符号 {label}"


def test_journal_pure_modules_have_no_raise():
    """G-5：纯函数模块不许抛——它们跑在真实资金的决策轮里。"""
    for name in ("records.py", "redact.py"):
        tree = _tree(_JOURNAL / name)
        raises = [n for n in ast.walk(tree) if isinstance(n, ast.Raise)]
        assert raises == [], f"{name} 出现了 raise：它跑在决策轮的 try 内"


def test_journal_symbols_never_appear_in_a_decision_predicate():
    """G-3：日志层不得进入任何判定表达式。

    判据取 `if` / `while` 的 `test` 子树与 `assert` 的条件：只要里面出现 `_journal` /
    `journal` 前缀的名字或属性，就是把一个观测面接进了交易判定。
    """
    offenders: list[str] = []
    for name in ("session.py", "decider.py", "manager.py"):
        tree = _tree(_AI_AUTO / name)
        for node in ast.walk(tree):
            tests = []
            if isinstance(node, (ast.If, ast.While)):
                tests.append(node.test)
            elif isinstance(node, ast.Assert):
                tests.append(node.test)
            elif isinstance(node, ast.IfExp):
                tests.append(node.test)
            for test in tests:
                for sub in ast.walk(test):
                    label = getattr(sub, "id", None) or getattr(sub, "attr", None)
                    if isinstance(label, str) and "journal" in label.lower():
                        offenders.append(f"{name}:{node.lineno} {label}")
    assert offenders == [], f"日志层进了判定表达式：{offenders}"


def test_journal_write_points_are_synchronous_and_never_await():
    """A-1 判据①：两个写点的函数体内 `ast.Await` 计数为 0，且它们是 `def` 而不是 `async def`。"""
    path = _AI_AUTO / "session.py"
    for name in ("_journal_round", "_journal_event"):
        node = _method(path, name)
        awaits = [n for n in ast.walk(node) if isinstance(n, ast.Await)]
        assert awaits == [], f"{name} 里出现了 await：决策轮的取消域不接受新的 CancelledError 落点"


def test_journal_write_points_are_wrapped_in_a_single_try_covering_exception():
    """A-1 判据②：函数体的顶层语句**恰好是一个** `ast.Try`，且 handler 覆盖 `Exception`。

    只查 `await` 是设计 v1 的缺口：`json.dumps` 跑在这里，一次
    `TypeError: Object of type X is not JSON serializable` 会把一轮**已经成功成交**的决策
    整个翻成 `AI_TRADE_ROUND_FAILED`——审计需求恰好害了它声称绝不碰的东西。
    """
    path = _AI_AUTO / "session.py"
    for name in ("_journal_round", "_journal_event"):
        node = _method(path, name)
        body = [stmt for stmt in node.body if not _is_docstring(stmt)]
        assert len(body) == 1 and isinstance(body[0], ast.Try), (
            f"{name} 的函数体必须整体裹在一个 try 里（当前顶层语句 {len(body)} 条）")
        handlers = body[0].handlers
        covered = any(isinstance(h.type, ast.Name) and h.type.id == "Exception"
                      for h in handlers)
        assert covered, f"{name} 的 except 必须覆盖 Exception（CancelledError 不在其下，照常传播）"


def _is_docstring(stmt: ast.stmt) -> bool:
    return isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant) \
        and isinstance(stmt.value.value, str)


def test_manager_lifecycle_helper_is_also_wrapped():
    """六个生命周期写点共用的那一个入队点同样必须整体兜住。"""
    node = _method(_AI_AUTO / "manager.py", "_journal_group_event")
    body = [stmt for stmt in node.body if not _is_docstring(stmt)]
    assert len(body) == 1 and isinstance(body[0], ast.Try)
    awaits = [n for n in ast.walk(node) if isinstance(n, ast.Await)]
    assert awaits == []


def test_no_backend_copy_carries_markdown_bold():
    """本轮新增的**会下发到前端**的文案里不得出现 `**` 粗体标记。

    JSX 会把它显示成字面星号，而本仓库已为此复发过**四次**
    （见 `docs/plans/ai-trade-loss-and-liveness-hardening/`）。前端的门禁看不见后端的字面量，
    故这道门禁必须长在后端这一侧。

    只扫**代码里的字符串字面量**，不扫注释与 docstring：那两处的 `**` 是给读代码的人看的
    Markdown 强调，永远不会到达任何一个 JSX 节点。
    """
    import re
    targets = list(_JOURNAL.rglob("*.py"))
    targets += [_AI_AUTO.parent / "api" / "ai_trade_journal.py",
                _AI_AUTO.parent / "api" / "schemas_journal.py"]
    offenders: list[str] = []
    for path in targets:
        for lineno, line in enumerate(path.read_text(encoding="utf-8").split("\n"), 1):
            head = line.strip()
            if head.startswith("#") or head.startswith('"""') or head.startswith("*"):
                continue
            for match in re.finditer(r'"([^"\n]*)"', line):
                if "**" in match.group(1):
                    offenders.append(f"{path.name}:{lineno} {match.group(1)[:60]}")
    assert offenders == [], f"这些文案在 JSX 里会显示成字面星号：{offenders}"


def test_lifecycle_event_messages_are_plain_sentences():
    """`manager` 的六个生命周期写点：文案是一句中文事实陈述，不带任何标记语法。"""
    import re
    text = (_AI_AUTO / "manager.py").read_text(encoding="utf-8")
    messages = re.findall(r'_journal_group_event\(\s*[^,]+,\s*"[A-Z_]+",\s*"([^"]*)"', text)
    assert len(messages) == 6, f"应有 6 个生命周期写点，实得 {len(messages)}：{messages}"
    for message in messages:
        assert "**" not in message and "`" not in message, message
