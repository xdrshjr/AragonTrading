"""组合相关性预算单测（vol-sizing-and-drawdown-brake §7.1 T-6/T-7/T-8/T-26/T-27）。

四条判据里有两条是**可证明**的不变式，故用属性测试而不是样例断言：
- S-15（T-26）：`unreachable=False` 的解必须真的满足 `E(s·proposed) ≤ cap_abs`——v1 的
  `min(1, cap/E)` 在同向高相关时恒不满足它，那正是本模块存在闭式解的理由；
- S-8（T-8）：`cap_mult ≥ 1.0` ⇒ 单成员分组恒不受约束（结构性零回归保证，不靠默认值碰巧成立）。
"""
from __future__ import annotations

import ast
import math
import pathlib
import random

from app.ai_auto import exposure_book as exposure_module
from app.ai_auto.exposure_book import GroupExposureBook, exposure_wire, solve_exposure_scale

_BAR = 900
_TS = 1_700_000_000


def _book(*, cap_mult: float = 1.5, unit: float = 10_000.0) -> GroupExposureBook:
    return GroupExposureBook(unit_notional=unit, cap_mult=cap_mult, bar_seconds=_BAR)


def _walk(seed: int, n: int = 200, start: float = 100.0) -> list[float]:
    rng = random.Random(seed)
    out, px = [start], start
    for _ in range(n):
        px *= math.exp(rng.gauss(0.0, 0.01))
        out.append(px)
    return out


def _effective(weights: dict[str, float], rho: dict[tuple[str, str], float]) -> float:
    total = 0.0
    for i, w_i in weights.items():
        for j, w_j in weights.items():
            total += w_i * w_j * (1.0 if i == j else rho[(i, j)])
    return math.sqrt(max(0.0, total))


# ---------- T-7（S-7）：全部方法纯同步，一个 await 都不许有 ----------
def test_exposure_book_has_no_await():
    tree = ast.parse(pathlib.Path(exposure_module.__file__).read_text(encoding="utf-8"))
    offenders = [type(node).__name__ for node in ast.walk(tree)
                 if isinstance(node, (ast.AsyncFunctionDef, ast.Await, ast.AsyncFor,
                                      ast.AsyncWith))]
    assert offenders == [], f"敞口簿必须保持纯同步（无锁的前提）：{offenders}"


# ---------- T-6：重叠样本不足 ⇒ ρ=1（最保守），绝不按 0、绝不跳过该对 ----------
def test_insufficient_overlap_uses_rho_one():
    book = _book(cap_mult=1.0)
    book.publish_returns("A", _walk(1, 20), _TS)
    book.publish_returns("B", _walk(2, 20), _TS)
    book.publish_notional("B", 10_000.0)
    view = book.effective_exposure(inst_id="A", proposed_notional=10_000.0, min_samples=96)
    assert view is not None
    # ρ=1 ⇒ E = |10000 + 10000| = 20000（取最大值，而不是按独立同分布的 √2 倍缩小）。
    assert math.isclose(view.effective, 20_000.0, rel_tol=1e-6)
    assert view.min_overlap == 20 < 96                  # 重叠不足 ⇒ 走的是最保守的 ρ=1 分支


def test_zero_overlap_is_still_rho_one():
    book = _book(cap_mult=1.0)
    book.publish_returns("A", _walk(1, 200), _TS)
    book.publish_returns("B", _walk(2, 200), _TS + 10_000 * _BAR)   # 时间戳完全不相交
    book.publish_notional("B", 10_000.0)
    view = book.effective_exposure(inst_id="A", proposed_notional=10_000.0, min_samples=5)
    assert view is not None and view.min_overlap == 0
    assert math.isclose(view.effective, 20_000.0, rel_tol=1e-6)


# ---------- 符号相消：一多一空且高相关 ⇒ 有效敞口应当下降（那才是真的对冲）----------
def test_opposite_sides_reduce_effective_exposure():
    book = _book(cap_mult=1.0)
    closes = _walk(7, 300)
    book.publish_returns("A", closes, _TS)
    book.publish_returns("B", closes, _TS)              # 完全同步 ⇒ ρ=1
    book.publish_notional("B", -10_000.0)               # 空头
    view = book.effective_exposure(inst_id="A", proposed_notional=10_000.0, min_samples=10)
    assert view is not None
    assert view.effective < 1e-6                        # |10000 − 10000| ≈ 0
    assert view.scale == 1.0 and view.unreachable is False


# ---------- T-8（S-8）：单成员分组恒不受约束 ----------
def test_single_member_group_never_capped():
    for cap_mult in (1.0, 1.5, 10.0):
        book = _book(cap_mult=cap_mult, unit=10_000.0)
        view = book.effective_exposure(inst_id="A", proposed_notional=10_000.0, min_samples=96)
        assert view is not None
        assert view.scale == 1.0 and view.unreachable is False


def test_cap_mult_zero_disables_the_layer():
    book = _book(cap_mult=0.0)
    assert book.effective_exposure(inst_id="A", proposed_notional=10_000.0,
                                   min_samples=96) is None
    assert exposure_wire(None)["corrScale"] == 1.0
    assert exposure_wire(None)["exposureCapUnreachable"] is False


# ---------- T-26（S-15）：闭式解满足上限，200 组随机 3 成员相关矩阵 ----------
def test_exposure_scale_satisfies_cap():
    rng = random.Random(20260725)
    checked = 0
    for _ in range(200):
        w_j, w_k = rng.uniform(-2e4, 2e4), rng.uniform(-2e4, 2e4)
        rho_ij, rho_ik, rho_jk = (rng.uniform(-1, 1) for _ in range(3))
        proposed = rng.uniform(-2e4, 2e4)
        cap = rng.uniform(5e3, 4e4)
        a = proposed * proposed
        b = proposed * (w_j * rho_ij + w_k * rho_ik)
        c = w_j * w_j + w_k * w_k + 2.0 * w_j * w_k * rho_jk
        if c < 0:                       # 随机 ρ 不必半正定，跳过非法二次型
            continue
        scale, unreachable = solve_exposure_scale(a=a, b=b, c=c, cap_abs=cap)
        assert 0.0 <= scale <= 1.0
        if unreachable:
            continue
        checked += 1
        effective_sq = scale * scale * a + 2.0 * scale * b + c
        assert effective_sq <= cap * cap + 1e-6, "闭式解必须真的满足上限（v1 的 cap/E 写法不满足）"
    assert checked > 50                 # 反向保证「全部走了 unreachable 分支」不会让本用例恒真


def test_naive_cap_over_effective_would_violate_the_cap():
    """反证 [P1-3]：同向高相关时 `s = cap/E(1)` 缩完仍越界，故绝不能用那个写法。"""
    proposed, other, cap = 10_000.0, 10_000.0, 15_000.0
    a, b, c = proposed ** 2, proposed * other, other ** 2      # ρ = 1
    naive = min(1.0, cap / math.sqrt(a + 2 * b + c))
    assert naive * naive * a + 2 * naive * b + c > cap * cap + 1.0
    solved, unreachable = solve_exposure_scale(a=a, b=b, c=c, cap_abs=cap)
    assert unreachable is False
    assert solved * solved * a + 2 * solved * b + c <= cap * cap + 1e-6


# ---------- T-27：其余成员自身已越界 ⇒ 如实标注并放行，绝不否决（守 S-3）----------
def test_exposure_cap_unreachable_never_vetoes():
    scale, unreachable = solve_exposure_scale(a=1e8, b=0.0, c=4e8, cap_abs=10_000.0)
    assert unreachable is True and scale == 0.0          # c ≥ cap² ⇒ s=0 也救不回来
    book = _book(cap_mult=1.0, unit=10_000.0)
    closes = _walk(11, 300)
    for inst, notional in (("B", 30_000.0), ("C", 30_000.0)):
        book.publish_returns(inst, closes, _TS)
        book.publish_notional(inst, notional)
    book.publish_returns("A", closes, _TS)
    view = book.effective_exposure(inst_id="A", proposed_notional=10_000.0, min_samples=10)
    assert view is not None and view.unreachable is True and view.scale == 0.0
    wire = exposure_wire(view, corr_scale=view.scale)
    assert wire["exposureCapUnreachable"] is True        # 必须如实标注而不是假装缩到位了
    assert wire["effectiveExposureCap"] == 10_000.0


# ---------- 缩减系数只缩不放 ----------
def test_scale_never_exceeds_one():
    book = _book(cap_mult=10.0, unit=10_000.0)
    closes = _walk(3, 300)
    book.publish_returns("A", closes, _TS)
    book.publish_returns("B", closes, _TS)
    book.publish_notional("B", 1_000.0)
    view = book.effective_exposure(inst_id="A", proposed_notional=1_000.0, min_samples=10)
    assert view is not None and view.scale == 1.0        # 远未触顶也只会是 1.0，绝不 >1


def test_flat_members_are_ignored():
    book = _book(cap_mult=1.0)
    book.publish_notional("B", 0.0)                      # 空仓成员不进二次型
    view = book.effective_exposure(inst_id="A", proposed_notional=10_000.0, min_samples=10)
    assert view is not None and view.members == 1 and view.scale == 1.0
