"""门禁 G-6 · 四条源码扫描（ai-trade-experience-context-graph §9.1）。

四条都在守同一类失败：**代码写对了但接线没接上 / 接错地方，而任何编译器与运行期用例都不会红。**
前端那条正则一律写 `\\r?\\n`——本仓库 `core.autocrlf=true`，写死 `\\n` 会在干净检出上恒红
（先例：`contrastTokens.source.test.ts`）。

判据取自 **AST 而不是原文**：三条 Python 侧的扫描都关心「代码里有没有这个调用」，而这些方法的
docstring 恰恰要逐字解释「为什么这里不能有它」。按原文扫会把解释本身判成违规——那会逼实现者
删掉注释来让门禁变绿，正好是反向激励。
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

_APP = Path(__file__).resolve().parents[1] / "app"
_WEB = Path(__file__).resolve().parents[3] / "apps" / "web"

# 六个确定性门禁模块：经验层**绝不**参与它们任何一道判据（不变式 I-1）。
_GATE_MODULES = (
    "ai_auto/edge_gate.py",
    "ai_auto/base_rules.py",
    "ai_auto/profit_guard.py",
    "ai_auto/hold_horizon.py",
    "ai_auto/sizing.py",
    "ai_auto/discipline.py",
)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _method_code(path: Path, name: str) -> str:
    """一个函数 / 方法的**代码**（去掉 docstring 后 `ast.unparse` 的结果）。找不到 ⇒ 空串。"""
    tree = ast.parse(_read(path))
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) or node.name != name:
            continue
        body = list(node.body)
        if (body and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)):
            body = body[1:]                       # 去掉 docstring：它要解释纪律，不是违反纪律
        return "\n".join(ast.unparse(stmt) for stmt in body)
    return ""


def test_g6_1_deterministic_gates_never_mention_experience():
    """① 六个门禁模块的源码里不得出现 `experience` 字样（不变式 I-1）。

    经验层是 advisory：它只进 prompt 与 UI。一旦某个门禁开始读它，「最坏后果恒为少做一笔」
    这条唯一成立的安全论证当场失效——那才是本特性敢默认开启的全部理由。
    """
    offenders = [name for name in _GATE_MODULES if "experience" in _read(_APP / name).lower()]
    assert offenders == [], f"门禁模块里出现了 experience：{offenders}（不变式 I-1）"


def test_g6_2_stop_and_pause_never_await_distillation():
    """② `session` / `manager` 的停止 / 暂停路径里**不得等待**叙事蒸馏。

    一次 45 秒的 LLM 调用挂在 `stop()` 的 await 链上 ⇒ 3 成员分组会让客户端 20 秒放弃并弹
    「本次操作结果未知」——**在真实资金的停止/平仓按钮上制造一次假警报**。

    判据比 §9.1 的字面措辞**更精确**（实施发现 I-2）：`pause_group` 按 ★ E-5 必须
    `spawn(_experience_finalize(session, distill=False))`，那一行按字面扫会被判违规，但它恰恰是
    「不蒸馏」的表达。真正要禁的是**等待**，故断言分两层：会话侧两个方法一个 `distill` 字样都
    不许有；manager 侧不得出现 `await ...distill...`（spawn 不 await 是允许且必需的）。
    """
    session_py = _APP / "ai_auto" / "session.py"
    for method in ("stop", "pause"):
        code = _method_code(session_py, method)
        assert code, f"session.py 里找不到 {method}——这条门禁会假绿"
        assert "distill" not in code, f"session.{method} 里出现了 distill（评审 P0-1）"

    manager_py = _APP / "ai_auto" / "manager.py"
    for method in ("stop_group", "pause_group", "_stop_session"):
        code = _method_code(manager_py, method)
        assert code, f"manager.py 里找不到 {method}——这条门禁会假绿"
        awaited = re.findall(r"await[^\n]*distill", code)
        assert awaited == [], f"manager.{method} 里 await 了蒸馏：{awaited}（评审 P0-1）"


def test_g6_3_persist_pending_and_flush_never_materialize():
    """③ `runtime.flush` / `persist_pending` 的代码里不得出现 `materialize`（★ D-1 / 评审 F-1）。

    物化是 CPU 密集 + 线程池 + 全库扫描。它一旦从这两个方法走进来，就同时落在
    `_dispatch` 的热路径与 `stop()` 的 await 链上——上一轮修掉的故障从另一扇门复发。
    """
    runtime_py = _APP / "ai_auto" / "experience" / "runtime.py"
    for method in ("flush", "persist_pending"):
        code = _method_code(runtime_py, method)
        assert code, f"runtime.py 里找不到 {method}——这条门禁会假绿"
        assert "materialize" not in code, f"runtime.{method} 里出现了 materialize（评审 F-1）"


def test_g6_4_http_timeout_registers_experience_paths():
    """④ `lib/httpTimeout.ts` 的两处登记（★ E-11），并禁止改动末段表波及既有路径。

    那张表是**逐条列举**的：不登记就静默落 `FAST_MS`（20 秒），而全库重建在大库上超 20 秒是
    常态 ⇒ 重建按钮**必然**假超时；而 `tsc` / `vitest` 都不会红。
    """
    source = _read(_WEB / "lib" / "httpTimeout.ts")
    assert source.count('"/experience/rebuild"') == 2, (
        "`/experience/rebuild` 必须出现两次：一次在 LONG_EXACT_PREFIXES（长跑档），"
        "一次在 WRITE_PREFIXES（写类 ⇒ 超时说「结果未知」而不是「失败」）")
    for path in ('"/experience/export"', '"/experience/import"'):
        assert path in source, f"{path} 未登记，会落默认档"
    suffixes = re.search(r"(?s)WRITE_ACTION_SUFFIXES\s*=\s*\[(.*?)\]", source)
    assert suffixes is not None, "找不到 WRITE_ACTION_SUFFIXES——这条门禁会假绿"
    assert "/rebuild" not in suffixes.group(1), (
        "别往末段表里加 /rebuild：那会让**既有的** `/ai-trade/prior/rebuild` 一并被重判为写路径，"
        "等于在一个与本特性无关的按钮上悄悄改掉超时文案")
