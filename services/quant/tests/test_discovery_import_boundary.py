"""**安全门禁**（strategy-factor-discovery §7.1 / D-2 / R-8）：发现层与交易层零 import 依赖。

做法：递归解析 ``app/discovery`` 下每个 ``.py`` 的 AST import 图，断言不含 ``live_auto`` /
``ai_auto`` / ``trading`` / ``okx.trade_client``。这条边界是本特性**最重要的安全设计**——
发现层永不下单，靠的不是「我们记得不要写」，而是这个机器判据。
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

FORBIDDEN = ("live_auto", "ai_auto", "trading", "trade_client", "paper")
DISCOVERY_DIR = Path(__file__).resolve().parents[1] / "app" / "discovery"


def _module_files() -> list[Path]:
    return sorted(DISCOVERY_DIR.rglob("*.py"))


def _imported_names(path: Path) -> set[str]:
    """收集一个模块引用到的全部 import 名（含 ``from x import y`` 的相对导入）。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            names.add(module)
            names.update(f"{module}.{alias.name}" for alias in node.names)
    return names


def test_discovery_package_is_non_empty():
    files = _module_files()
    assert len(files) >= 12, "发现层模块数异常，门禁可能扫了空目录"


@pytest.mark.parametrize("path", _module_files(), ids=lambda p: p.name)
def test_no_module_imports_the_trading_layer(path: Path):
    """逐模块断言：发现层**永不**引入下单相关的任何模块（含延迟导入的函数内 import）。"""
    for name in _imported_names(path):
        for forbidden in FORBIDDEN:
            assert forbidden not in name, f"{path.name} 引入了禁止的依赖：{name}"


def test_package_init_only_reexports_zero_dependency_modules():
    """``discovery/__init__`` 只再导出三个零依赖模块（评审 P2-7）。

    若它再导出 ``validation`` / ``searchers`` / ``runtime``，``library._expr_factor`` 的延迟
    导入就会经 ``__init__`` 把 ``engine.factors.evaluation`` 整条链拉起，形成
    ``engine ↔ discovery`` 回边——那是一条随时会炸的循环导入。
    """
    names = _imported_names(DISCOVERY_DIR / "__init__.py")
    modules = {name.split(".")[0] for name in names if name and not name.startswith("app")}
    assert modules <= {"expression", "expression_text", "mechanisms", "__future__", ""}
    for banned in ("validation", "searchers", "runtime", "service", "repositories",
                   "llm_agents", "promote"):
        assert not any(banned in name for name in names), f"__init__ 不得再导出 {banned}"


def test_library_expr_bridge_imports_the_submodule_directly():
    """``_expr_factor`` 必须**直达子模块**（``discovery.expression``），不得走包再导出。

    用 AST 判定而非文本匹配：文档字符串里正好会提到这条规则，文本匹配会自己把自己打红。
    """
    library = DISCOVERY_DIR.parents[0] / "engine" / "factors" / "library.py"
    tree = ast.parse(library.read_text(encoding="utf-8"))
    modules = {node.module for node in ast.walk(tree)
               if isinstance(node, ast.ImportFrom) and node.module}
    assert "discovery.expression" in modules
    assert "discovery" not in modules      # 走包再导出即形成 engine ↔ discovery 回边
