"""签名迁移的零遗漏门禁（ai-trade-exit-priority-and-commitment G-11 · #35）。

本轮**仅有的两处**非加法式签名变更：

  · `hold_horizon.evaluate_horizon_gate` —— 二元组 → **三元组**（+ `pending`）
  · `reflection.apply_reflection`        —— 二元组 → **三元组**（+ `fail_open_code`）

两者都是「漏一处即 `ValueError: too many values to unpack`」——**会红，但会红在离根因很远的
地方**（一次被拦的平仓、一次超时的反思，root cause 却在另一个模块的解包语句上）。故给它们一条
专属的 AST 门禁：全仓扫调用点，断言每一处都按新元数解包。

同时断言两者的**回退等价性**：`priority_gates=()` / `fail_open_actions=()` 时第三元恒为
`False` / `None`，C-1 与 C-8 的一键回退因此逐字节成立。
"""
from __future__ import annotations

import ast
import inspect
from pathlib import Path

from app.ai_auto.decider import ReflectionVerdict, TradeDecision
from app.ai_auto.hold_horizon import HorizonRule, evaluate_horizon_gate
from app.ai_auto.reflection import apply_reflection

_ROOT = Path(__file__).resolve().parents[1]
_SCAN_DIRS = (_ROOT / "app", _ROOT / "tests")

# 每个函数期望的解包元数（改这两个数之前先读本文件的模块 docstring）。
_ARITY = {"evaluate_horizon_gate": 3, "apply_reflection": 3}


def _python_files() -> list[Path]:
    return [p for directory in _SCAN_DIRS for p in directory.rglob("*.py")]


def _unpack_arity(tree: ast.AST, func_name: str) -> list[tuple[int, int]]:
    """返回该模块里 `x, y[, z] = func_name(...)` 的 `(行号, 解包元数)`。

    只认**赋值语句**的解包：`await` 与直接调用（丢弃返回值 / 传给别的函数）不在门禁范围内——
    前者已被 `ast.Await` 剥掉，后者本就不解包，不会触发 unpack 错误。
    """
    hits: list[tuple[int, int]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target, value = node.targets[0], node.value
        if isinstance(value, ast.Await):
            value = value.value
        if not isinstance(value, ast.Call):
            continue
        called = value.func
        name = (called.attr if isinstance(called, ast.Attribute)
                else called.id if isinstance(called, ast.Name) else "")
        if name != func_name or not isinstance(target, ast.Tuple):
            continue
        hits.append((node.lineno, len(target.elts)))
    return hits


def test_g11_every_call_site_unpacks_the_new_arity():
    """全仓扫两个函数的调用点，断言**每一处**都按新元数解包（零遗漏）。"""
    offenders: list[str] = []
    seen = {name: 0 for name in _ARITY}
    for path in _python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for func_name, arity in _ARITY.items():
            for lineno, found in _unpack_arity(tree, func_name):
                seen[func_name] += 1
                if found != arity:
                    offenders.append(f"{path.relative_to(_ROOT)}:{lineno} "
                                     f"{func_name} 解包 {found} 元（应为 {arity} 元）")
    assert offenders == [], "\n".join(offenders)
    # 证伪性：扫描器必须真的找到了调用点，否则这条门禁是一个恒绿的空转
    for func_name, count in seen.items():
        assert count > 0, f"扫描器一处 {func_name} 调用点都没找到 —— 门禁失效"


def test_g11_signatures_carry_the_new_keyword_only_params_with_safe_defaults():
    """两个新形参必须是 **keyword-only 且带回退默认值**——否则既有调用点当场全红。"""
    gate = inspect.signature(evaluate_horizon_gate).parameters
    assert gate["priority_gates"].kind is inspect.Parameter.KEYWORD_ONLY
    assert gate["priority_gates"].default == ()
    assert gate["may_pend"].default is False
    reflect = inspect.signature(apply_reflection).parameters
    assert reflect["fail_open_actions"].kind is inspect.Parameter.KEYWORD_ONLY
    assert reflect["fail_open_actions"].default == ()


def test_c1_rollback_equivalence_of_the_horizon_gate():
    """C-1 回退等价：`priority_gates=()` + `may_pend=False` ⇒ 第三元恒 `False`，前两元逐字不变。"""
    rule = HorizonRule(enabled=True, min_hold_s=7200.0, exit_confirm_rounds=2,
                       confirm_window_s=3600.0, hysteresis_k=1.5)
    snapshot = {"holdSeconds": 60.0, "exitConfirmStreak": 0, "releasedGates": [],
                "confirmWindowElapsed": False, "moveBps": 5.0, "requiredMoveBps": 20.0}
    decision = TradeDecision("close", None, 0.7, "exit")
    default = evaluate_horizon_gate(decision, snapshot, rule, position_side="long")
    explicit = evaluate_horizon_gate(decision, snapshot, rule, position_side="long",
                                     priority_gates=(), may_pend=False)
    assert default[1] == explicit[1] and default[2] is explicit[2] is False
    assert default[0].action == explicit[0].action == "hold"
    # 未装配 / 非受控动作 ⇒ **同一个对象**返回（H-1/H-15 的 `is` 相等在本轮不变）
    same = evaluate_horizon_gate(decision, snapshot, None, position_side="long")
    assert same[0] is decision and same[1] is None and same[2] is False


def test_c8_rollback_equivalence_of_apply_reflection():
    """C-8 回退等价：`fail_open_actions=()` ⇒ 第三元恒 `None`，前两元逐字不变。"""
    close = TradeDecision("close", None, 0.9, "exit")
    for code in ("reflection_timeout", "reflection_llm_error", "reflection_no_tool",
                 "reflection_invalid"):
        verdict = ReflectionVerdict("abort", None, None, code, code, ran=True)
        default = apply_reflection(close, None, verdict, 0.0)
        explicit = apply_reflection(close, None, verdict, 0.0, fail_open_actions=())
        assert default[0].action == explicit[0].action == "hold"
        assert default[1] == explicit[1] == code
        assert default[2] is explicit[2] is None
        # 反转之后：同一个断面放行 close 且**不写 degrade**
        opened = apply_reflection(close, None, verdict, 0.0, fail_open_actions=("close",))
        assert opened[0] is close and opened[1] is None and opened[2] == code


def test_a_real_abort_is_never_reversed_by_fail_open():
    """R-3 缓解①：模型的**真实终态裁决** `degrade_reason is None` ⇒ 永远不在 fail-open 范围内。"""
    close = TradeDecision("close", None, 0.9, "exit")
    verdict = ReflectionVerdict("abort", None, None, "现在走是错的", None, ran=True)
    out, degrade, fail_open = apply_reflection(close, None, verdict, 0.0,
                                               fail_open_actions=("close",))
    assert out.action == "hold" and degrade == "reflection_abort" and fail_open is None


def test_open_actions_are_never_fail_open():
    """作用域钉死在 `close`：开仓侧的 fail-closed 一个字节都不动（X-1）。"""
    opening = TradeDecision("open_long", 0.5, 0.9, "entry")
    verdict = ReflectionVerdict("abort", None, None, "reflection_timeout",
                                "reflection_timeout", ran=True)
    out, degrade, fail_open = apply_reflection(opening, None, verdict, 0.0,
                                               fail_open_actions=("close",))
    assert out.action == "hold" and degrade == "reflection_timeout" and fail_open is None


# ---------------- G-4：从持仓周期层到反思层之间没有任何一层改写 close ----------------
_CLOSE_EXEMPT_LAYERS = (
    ("base_rules", "_GATED_ACTIONS"),
    ("edge_gate", "_GATED_ACTIONS"),
    ("drawdown_brake", "_GATED_ACTIONS"),
    ("sizing", "_GATED_ACTIONS"),
)


def test_g4_no_layer_between_horizon_and_reflection_touches_close():
    """G-4：`close` 从持仓周期层到反思层之间**没有任何一层**改写它。

    这条通路正是「挂起为 pendingExit、decision 保持 close 继续下行」的安全前提——将来任一层若
    开始拦 `close`，本用例即红，而不是等到线上出现「挂起的离场在半路被别的层吞掉」。
    **注意作用域**：这条论证只对 `close` 成立；反手的动作字面量是 `open_*`，它落在下面四道闸
    加共识门禁的作用域内，会被静默降级成 hold（X-1b 因此把反手先降级为 close）。
    """
    import importlib

    for module_name, const in _CLOSE_EXEMPT_LAYERS:
        module = importlib.import_module(f"app.ai_auto.{module_name}")
        gated = getattr(module, const)
        assert "close" not in gated, f"{module_name}.{const} 开始拦 close 了"
        assert set(gated) <= {"open_long", "open_short"}, module_name
    # 换手纪律层对反手只**降级为 close**（保留退出腿），从不整轮否掉平仓
    from app.ai_auto.turnover_discipline import apply_turnover_gate
    src = inspect.getsource(apply_turnover_gate)
    assert "close" in src
    # 冷却层 / 兜底覆盖层同样不拦 close（前者只作用于 open_*，后者只拦同向加仓）
    from app.ai_auto.profit_cooldown import apply_profit_cooldown
    assert "close" not in inspect.getsource(apply_profit_cooldown).split("def ")[1][:400]


def test_g7_no_new_order_write_paths():
    """G-7（X-3）：两个下单原语的**来源集合**里不得出现本轮的新纯核心；`flatten(` 只 +1 处。

    本轮的兑现重试与一键离场一律复用既有写路径（`executor.apply([Signal(side="close")])` 与
    `executor.flatten`）。**这条门禁守的是「绝不新增下单写路径」这条不变式**——判据是
    **谁在调用**，不是调用了多少次：调用点计数会随任何一次无关重构漂移，而「新纯核心里出现了
    `place_order(`」是一个永远不该发生的事实（节点 #3 复核：原实现算了一份 `counts` 却从不断言
    它，那份计算是死码，且会让读者误以为净增数真的被守住了）。
    """
    app_dir = _ROOT / "app"
    owners = {needle: set() for needle in ("place_order(", "close_position(", "flatten(")}
    for path in app_dir.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for needle in owners:
            if any(needle in line and not line.lstrip().startswith("#")
                   for line in text.splitlines()):
                owners[needle].add(path.relative_to(app_dir).as_posix())
    assert not any(o.startswith("ai_auto/exit_") for o in owners["place_order("]), \
        "新纯核心里出现了下单调用 —— 违反 X-3"
    assert not any(o.startswith("ai_auto/exit_") for o in owners["close_position("]), \
        "新纯核心里出现了平仓调用 —— 违反 X-3"
    # `session.exit_now()` 是本轮唯一允许的 `flatten(` 净增点，且必须复用同一条收尾
    session_src = (app_dir / "ai_auto" / "session.py").read_text(encoding="utf-8")
    assert "_handle_flatten_result(ts, result=result, terminal=False)" in session_src
