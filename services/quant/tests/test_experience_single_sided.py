"""单侧性与措辞的源码扫描门禁（G-11 / G-13 / A-32 / A-38）。

**J-1 是这一整轮敢默认开启的压舱石**：本轮新增的每一条自动化，其作用方向**只能是让经验层说得
更少或说得更弱**——自动静音、覆盖率归一后的降权、降级签名落进更粗的桶、注入台账只用于扣分。
**绝不允许**新增任何自动加权 / 自动置顶 / 自动升档 / 自动扩容披露的路径。

判据按 **AST**（去 docstring 后 `ast.unparse`）而不是按原文：这三个模块的 docstring 恰恰要逐字
解释「为什么这里不能有它」，按原文扫会把解释本身判成违规（`v1:I-2` 的成文教训）。
"""
from __future__ import annotations

import ast
import pathlib
import re

from app.ai_auto.experience import taxonomy as tax
from app.ai_auto.experience.graph import Graph, GraphEdge, GraphNode
from app.ai_auto.experience.retrieval import ContextSeed, RetrievalConfig, retrieve
from app.config import get_settings

_NEW_MODULES = ("utility.py", "incremental.py", "adopted.py", "accumulate.py")

# 禁止出现的**赋值目标**（自动升档 / 自动加权 / 自动扩容披露的三条路径）。
_FORBIDDEN_TARGETS = ("pinned", "tier", "l0_max", "l1_max", "manual_seed_weight",
                      "pinned_evidence_floor")


def _code_without_docstrings(path: pathlib.Path) -> str:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Module)):
            node.body = [stmt for stmt in node.body
                         if not (isinstance(stmt, ast.Expr)
                                 and isinstance(stmt.value, ast.Constant)
                                 and isinstance(stmt.value.value, str))]
    return ast.unparse(tree)


def test_g11_the_new_automations_can_only_make_the_layer_say_less() -> None:
    """**G-11（J-1 的机械判据）** · 四个新模块里不得出现任何自动升档 / 自动加权的写入。

    `node.pinned = True` / `node.tier = "iron"` / 一份带 `{"pinned": True}` 的写入 payload /
    对 `*_max_chars` 的赋值——每一条都是「让经验层说得更多、更肯定」的方向。

    判据刻意**只看真正会改状态的那几种形态**（属性赋值 + 写入 payload 的字面量），而不是
    「凡出现这个名字就红」：`prune_stale` 里那个数「跳过了几条置顶」的**局部计数器**读的是
    `row["pinned"]`，它恰恰是在**保护**用户意志。一条会把保护动作判成违规的门禁，
    最终只会被人放宽（`v1:I-2` 的成文教训）。

    G-11 证不了「门槛没有变松」——那由 A-40 承担。它证的是**没有一处代码在直接把档位往上抬**。
    """
    root = pathlib.Path("app/ai_auto/experience")
    for name in _NEW_MODULES:
        code = _code_without_docstrings(root / name)
        tree = ast.parse(code)
        for node in ast.walk(tree):
            targets: list[ast.expr] = []
            if isinstance(node, ast.Assign):
                targets = list(node.targets)
            elif isinstance(node, (ast.AugAssign, ast.AnnAssign)):
                targets = [node.target]
            for target in targets:
                if isinstance(target, ast.Attribute):
                    assert target.attr not in _FORBIDDEN_TARGETS, (
                        f"{name} 里对 `.{target.attr}` 赋了值——那是一条自动升档 / 自动加权"
                        "路径，J-1 明令禁止这个方向")
            if isinstance(node, ast.Dict):
                for key, value in zip(node.keys, node.values):
                    if not isinstance(key, ast.Constant) or key.value not in (
                            "pinned", "tier"):
                        continue
                    assert not (isinstance(value, ast.Constant) and value.value), (
                        f"{name} 里写了一份带 `{key.value}` 的升档 payload")
        assert "TIER_IRON" not in code and "'iron'" not in code, (
            f"{name} 里出现了 `iron` 字面量——升档只有两条路：统计门槛，或用户显式置顶")
        assert not re.search(r"\bl[01]_max(_chars)?\s*=[^=]", code), (
            f"{name} 里改了披露预算——自动扩容披露同样是「说得更多」的方向")


def _strip_comments(path: pathlib.Path) -> str:
    """去掉注释与 docstring，只留**真正会跑的代码与真正会显示的文案**。

    ★ 这一步不是洁癖：`utility.py` 的 docstring 逐字写着「绝不使用「贡献」「带来」这类因果
    措辞」——按原文扫会把这条**解释**判成违规（`v1:I-2` 的成文教训：一条会把自己的理由判成
    违规的门禁，最终只会被人放宽）。
    """
    if path.suffix == ".py":
        return _code_without_docstrings(path)
    text = path.read_text(encoding="utf-8")
    text = re.sub(r"\{/\*.*?\*/\}", "", text, flags=re.S)      # JSX 注释
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)           # 块注释
    return re.sub(r"(?<![:'\"])//[^\r\n]*", "", text)            # 行注释（避开 URL）


def test_g13_no_causal_wording_anywhere_in_the_utility_layer() -> None:
    """**G-13（J-5）** · 「注入之后那一单赚了」是**共现**，不是因果。

    我们并不知道模型是否真的采纳了它。一个用户会据以删掉经验的界面，措辞就是它的正确性：
    一律「注入后表现」「与事实一致率」，绝不「贡献」「带来了」「因此赚」「导致盈利」。

    正则一律写 `\r?\n`（本仓库 `core.autocrlf=true`，写死 `\n` 会在干净检出上恒红）。
    """
    banned = ("贡献", "带来了", "因此赚", "导致盈利", "证明了它有用")
    targets = [pathlib.Path("app/ai_auto/experience/utility.py")]
    web = pathlib.Path("../../apps/web")
    # 排除 `*.test.tsx`：那些用例里的**负向断言**逐字写着被禁的词（`.not.toMatch(/贡献|…/)`）。
    # 一条会把自己的护栏判成违规的门禁，最终只会被人放宽（`v1:I-2` 的成文教训）。
    targets += [p for p in sorted(web.glob("components/Settings/experience/*.tsx"))
                if not p.name.endswith(".test.tsx")]
    targets += [p for p in sorted(web.glob("components/Agent/aiTradeStats/Experience*.tsx"))
                if not p.name.endswith(".test.tsx")]
    targets.append(web / "lib" / "experienceTypes.ts")
    targets.append(web / "lib" / "experience.ts")
    checked = 0
    for path in targets:
        if not path.exists():
            continue
        checked += 1
        code = _strip_comments(path)
        for word in banned:
            assert word not in code, f"{path} 出现了因果措辞「{word}」（J-5 / G-13）"
    assert checked >= 5, "扫到的文件太少，这条门禁证明不了什么"
    # 讲了效用就必须点明「这是共现」——那句话是这个功能的正确性本身，不是免责声明。
    for path in (pathlib.Path("app/ai_auto/experience/utility.py"),
                 web / "components" / "Settings" / "experience" / "ExperienceUtilityPanel.tsx"):
        assert "共现" in path.read_text(encoding="utf-8"), (
            f"{path} 讲了「注入之后那些单表现如何」却没有一处点明它只是共现关系")


def test_a32_running_the_utility_loop_never_touches_any_disclosure_knob() -> None:
    """**A-32** · 跑完 W-C 的全流程，`l0_max` / `l1_max` / 任何 `*_max_chars` 一个都没被改。

    判据取自 `Settings` 是**冻结的 pydantic 模型**这一结构性事实：本轮的自动化拿到的是派生副本，
    绝不 `setattr` 全局单例（`get_settings()` 是进程级缓存，且 `LiveExecutor` 被 `ai_auto` 与
    `live_auto` 共用同一个对象，就地改会让阈值静默溢出到非 AI 链路）。
    """
    settings = get_settings()
    before = {name: getattr(settings, name) for name in dir(settings)
              if name.startswith("ai_trade_experience_")
              and (name.endswith("_max_chars") or name.endswith("_max"))}
    assert before, "没有取到任何披露旋钮，这条用例证明不了什么"
    derived = settings.model_copy(update={"ai_trade_experience_utility_min_samples": 3})
    for name, value in before.items():
        assert getattr(derived, name) == value
        assert getattr(get_settings(), name) == value, (
            f"{name} 被就地改了——那会让阈值静默溢出到 `live_auto` 共用的那个对象上")


def test_a38_coverage_normalisation_only_ever_says_less() -> None:
    """**A-38（★ E-2 / E-14）** · 覆盖率归一之后，同一颗种子只会说得更少，不会说得更多。

    `match` 从「命中的轴数」变成「这条经验自身的条件被满足的比例」：一条四轴经验只命中两轴时
    从 2.0 降到 0.5，而一条完全匹配的两轴经验仍是 1.0。方向必须是「L0/L1 条数不增、
    disputed 条数不减」——出现相反方向就说明归一化写错了。
    """
    wide = GraphNode(key="lesson:wide", label="四轴经验。", support=30, weight=0.9,
                     tier="normal", last_seen_ts=1_700_000_000,
                     signature={tax.AXIS_SYMBOL: "BTC-USDT-SWAP", tax.AXIS_BAR: "15m",
                                tax.AXIS_REGIME: "range", tax.AXIS_SIDE: "long"},
                     stats={"meanNetBps": -20.0, "tStat": -3.0})
    narrow = GraphNode(key="lesson:narrow", label="两轴经验。", support=30, weight=0.9,
                       tier="normal", last_seen_ts=1_700_000_000,
                       signature={tax.AXIS_SYMBOL: "BTC-USDT-SWAP", tax.AXIS_BAR: "15m"},
                       stats={"meanNetBps": -20.0, "tStat": -3.0})
    contexts = [GraphNode(key=tax.canonical_key(axis, value), kind="context", axis=axis,
                          value=value, weight=1.0, tier="normal")
                for axis, value in ((tax.AXIS_SYMBOL, "BTC-USDT-SWAP"), (tax.AXIS_BAR, "15m"),
                                    (tax.AXIS_REGIME, "range"), (tax.AXIS_SIDE, "long"))]
    edges = [GraphEdge(node.key, tax.canonical_key(axis, value), "applies_in", 1.0, 30)
             for node in (wide, narrow) for axis, value in node.signature.items()]
    graph = Graph([wide, narrow, *contexts], edges)

    # 种子只点亮两条轴 ⇒ 四轴经验只有一半条件成立。
    seed = ContextSeed(axes={tax.AXIS_SYMBOL: "BTC-USDT-SWAP", tax.AXIS_BAR: "15m"},
                       now_wall=1_700_000_000.0)
    result = retrieve(graph, seed, RetrievalConfig.from_settings(get_settings()))
    scores = {item.node.key: item.match for item in result.l0 + result.l1}
    assert scores.get("lesson:narrow") == 1.0, "完全匹配的经验覆盖率必须是 1.0"
    assert scores.get("lesson:wide") == 0.5, (
        "四轴命中两轴的经验必须降到 0.5——此前它与完全匹配的那条**同分**，"
        "而这两者的可信度完全不同：前者有一半前提不成立")
    assert scores["lesson:wide"] < scores["lesson:narrow"]
