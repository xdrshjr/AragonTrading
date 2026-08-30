"""不变式 I-1 的源码扫描门禁（§7.1 · test_ai_intel_no_gate_reads / G-5）。

**情报不进入任何确定性门禁、不改变任何阈值。** 它只进两个 LLM 调用点（决策与复核）的证据面，
且两处逐字节相同（那一条由 `test_ai_intel_disclosure` 冻结）。

判据取自 **AST 而不是原文**：这些模块的 docstring 恰恰要解释「为什么这里不能有情报」，按原文扫
会把解释本身判成违规——那会逼实现者删掉注释来让门禁变绿，正好是反向激励。

前端那条正则一律写 `\\r?\\n`：本仓库 `core.autocrlf=true`，写死 `\\n` 会在干净检出上恒红。
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

_APP = Path(__file__).resolve().parents[1] / "app"
_WEB = Path(__file__).resolve().parents[3] / "apps" / "web"

# 七个确定性门禁模块：情报**绝不**参与它们任何一道判据。
_GATE_MODULES = (
    "ai_auto/gate_veto.py",
    "ai_auto/open_funnel.py",
    "ai_auto/edge_gate.py",
    "ai_auto/sizing.py",
    "ai_auto/hold_horizon.py",
    "ai_auto/profit_guard.py",
    "ai_auto/base_rules.py",
)


def _code_without_docstrings(path: Path) -> str:
    """模块的**代码**（逐个函数/类去掉 docstring 后 `ast.unparse`）。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                                 ast.ClassDef)):
            continue
        body = list(node.body)
        if (body and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)):
            node.body = body[1:] or [ast.Pass()]
    return ast.unparse(tree)


def test_deterministic_gates_never_mention_intel():
    offenders = []
    for rel in _GATE_MODULES:
        path = _APP / rel
        assert path.exists(), rel
        if "intel" in _code_without_docstrings(path).lower():
            offenders.append(rel)
    assert offenders == [], (
        f"{offenders} 里出现了情报读点。届时唯一正确的动作是把那处读点移回 prompt 层，"
        "而**不是**删掉这条断言——情报是 advisory-only，它不该能改变任何确定性判据。")


def test_intel_package_never_imports_a_gate_module():
    """反向也要守：情报包里 import 一个门禁模块，等于给自己开一条反向影响的门。"""
    offenders = []
    gate_names = {Path(rel).stem for rel in _GATE_MODULES}
    for path in sorted((_APP / "ai_auto" / "intel").glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                tail = node.module.rsplit(".", 1)[-1]
                if tail in gate_names:
                    offenders.append(f"{path.name} → {node.module}")
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.rsplit(".", 1)[-1] in gate_names:
                        offenders.append(f"{path.name} → {alias.name}")
    assert offenders == []


def test_intel_is_injected_only_through_the_prompt_layer():
    """`intel_digest_for` 的读点只允许出现在 `prompts.py` / `session.py` 这条注入链上。"""
    allowed = {"ai_auto/session.py", "ai_auto/intel/runtime.py", "ai_auto/intel/__init__.py"}
    offenders = []
    for path in sorted(_APP.rglob("*.py")):
        rel = path.relative_to(_APP).as_posix()
        if rel in allowed or rel.startswith("ai_auto/intel/"):
            continue
        if "intel_digest_for" in _code_without_docstrings(path):
            offenders.append(rel)
    assert offenders == []


def test_backtest_lane_carries_no_intel_key():
    """R-12 的红线：历史 point-in-time 语料取不回来，接进回测必然引入前视偏差。"""
    for rel in ("ai_auto/backtest.py", "ai_auto/backtest_manager.py"):
        code = _code_without_docstrings(_APP / rel)
        assert "intel" not in code.lower(), rel


def test_body_is_only_read_inside_render_block():
    """evidence-grounding §3.6 第 3 条：抓回的正文**只**进不可信区。

    `render_local_facts`（可信区）一个字节不改；`doc.body` 只在 `render_block` 与
    `attach_bodies` 里被读。多一个读点就是把外部网页文本放进可信区的一条新通道，而那条通道
    不会报错。
    """
    tree = ast.parse((_APP / "ai_auto" / "intel" / "corpus.py").read_text(encoding="utf-8"))
    readers = set()
    for func in ast.walk(tree):
        if not isinstance(func, ast.FunctionDef):
            continue
        for node in ast.walk(func):
            if (isinstance(node, ast.Attribute) and node.attr == "body") or (
                    isinstance(node, ast.keyword) and node.arg == "body"):
                readers.add(func.name)
    assert readers <= {"render_block", "attach_bodies"}, \
        f"`body` 多出了读点：{sorted(readers - {'render_block', 'attach_bodies'})}"


def test_pure_core_evidence_modules_import_no_io():
    """`extract.py` / `quotes.py` 是纯核心：零 IO、零网络、零 DB、零时钟。"""
    forbidden = {"httpx", "socket", "asyncio", "sqlalchemy", "time", "logging", "random"}
    for name in ("extract.py", "quotes.py"):
        tree = ast.parse((_APP / "ai_auto" / "intel" / name).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = {alias.name.split(".")[0] for alias in node.names}
            elif isinstance(node, ast.ImportFrom):
                names = {(node.module or "").split(".")[0]}
            else:
                continue
            assert not (names & forbidden), f"{name} import 了 IO 模块：{names & forbidden}"


def test_reader_is_the_only_module_that_goes_out():
    """不变式 I-5：出网抓取的调用点被**唯一化**在 `reader.py` 里。

    放开它等于把一个受限于搜索源的取数器变成一个通用的服务端代理——SSRF 的实质风险面从
    「搜索源被投毒」放大到「任何能写库的人」。
    """
    offenders = []
    for path in sorted((_APP / "ai_auto" / "intel").glob("*.py")):
        if path.name in ("reader.py", "search.py"):
            continue
        code = _code_without_docstrings(path)
        if "httpx" in code:
            offenders.append(path.name)
    assert offenders == [], f"{offenders} 里出现了出网 client"


def test_frontend_intel_components_never_use_dangerous_html():
    """情报文本来自不可信网页；`dangerouslySetInnerHTML` 在这里等于把 XSS 邀请进来。"""
    intel_dir = _WEB / "components" / "Intel"
    assert intel_dir.exists()
    offenders = []
    for path in sorted(intel_dir.rglob("*.tsx")):
        # 正则写 `\r?\n`：本仓库 `core.autocrlf=true`，写死 `\n` 会在干净检出上恒红。
        text = path.read_text(encoding="utf-8")
        if re.search(r"dangerouslySetInnerHTML", text):
            offenders.append(path.name)
    assert offenders == []
