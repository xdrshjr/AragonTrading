"""AI 分组的组合相关性预算：收益率 / 名义额登记簿 + 有效敞口闭式缩减（§3.5）。

**为什么独立成模块而不是塞进 runtime**：登记簿的生命周期是**分组级**（`AiTradeGroup`），而
`SizingRuntime` 是**会话级**（每个成员一个）。把分组级共享状态放进会话级对象会立刻产生「谁是所有者」
的二义，且 N 个成员各持一份就等于没共享。

**并发安全（S-7）**：本模块的全部方法都是**纯同步、无 `await`**。一个 AI 分组的全部成员会话跑在同一个
asyncio 事件循环里，同步方法之间不可能交错，因此不需要锁。**任何后续给这些方法加 `await` 的改动都会
破坏该不变式**——那一刻起两个成员就可能在对方读到一半时改写登记簿。这条禁令是死的。

有效敞口的定义（带符号的相关加权二次型）：

    E(w) = sqrt( Σᵢ Σⱼ wᵢ·wⱼ·ρᵢⱼ )

取符号相乘是刻意的：两个成员一多一空且高相关时 `wᵢwⱼρᵢⱼ < 0`，有效敞口**应当**下降——那才是真的对冲。
样本不足时该对取 `ρ=1.0`（最保守），**绝不按 0 处理、绝不跳过该对**。
"""
from __future__ import annotations

import math
from dataclasses import dataclass

# 每个成员保留的收益率样本上限（按 ts 键存；远超任何合理的 corr_min_samples，防长跑无界增长）。
_RETURNS_MAX = 2000


@dataclass(frozen=True)
class ExposureView:
    """一次有效敞口判定的只读产物。

    Attributes:
        effective: 按**提议原值**（s=1）算出的有效敞口（USD 名义额）。
        cap_abs: 本分组的有效敞口上限 = `exposure_cap_mult × 平均单标的满仓名义额`。
        scale: 闭式解出的缩减系数 ∈ [0,1]（1.0 = 无需缩减）。
        unreachable: 其余成员自身的二次型已越界，`s=0` 也救不回来（[P1-3] 的边缘态）。
        members: 参与计算的成员数（含本成员）。
        min_overlap: 与其余成员的最小重叠样本数（`None` = 没有其余成员）。
    """

    effective: float
    cap_abs: float
    scale: float
    unreachable: bool
    members: int
    min_overlap: int | None


def solve_exposure_scale(*, a: float, b: float, c: float,
                         cap_abs: float) -> tuple[float, bool]:
    """解满足 `E(s·proposed) ≤ cap_abs` 的**最大** `s ∈ [0,1]`（闭式，零新依赖）。

    [P1-3] **绝不能用 `min(1, cap/E)`**：`E` 对单个分量**非齐次**。只缩成员 i 的名义额时

    ```
    E(s)² = s²·a + 2s·b + c        a = wᵢ² > 0
                                   b = wᵢ · Σ_{j≠i} wⱼρᵢⱼ            （交叉项）
                                   c = Σ_{j≠i} Σ_{k≠i} wⱼwₖρⱼₖ ≥ 0   （其余成员自身的二次型）
    ```

    取 `s = cap/E(1)` 后 `E(s) ≤ cap` 需要 `(s−1)·[2bs + c(s+1)] ≥ 0`，在 `c>0`、`b≥0`（同向高相关
    ——**正是本层要治的场景**）时**恒不成立**，故那个写法会让「有效敞口 ≤ cap」这条验收指标长期不达标。
    正确解法是直接解二次不等式 `a·s² + 2b·s + (c − cap²) ≤ 0`。

    Returns:
        `(s, unreachable)`。`unreachable=True` ⇒ `[0,1]` 内无解（其余成员自身已越界），此时返回
        `s=0` 并**放行**（S-3：本层永不改写 action）——一个算得出来却做不到的上限，必须如实标注而不是
        假装缩到位了。
    """
    cap = max(0.0, float(cap_abs))
    q0 = c - cap * cap
    if a <= 0:                              # 本成员本轮没有提议敞口：无从缩起
        return 1.0, q0 > 0
    if a + 2.0 * b + q0 <= 0:               # s=1 已在上限内
        return 1.0, False
    disc = b * b - a * q0
    if disc < 0:                            # 抛物线整体在零上方 ⇒ 任何 s 都超限
        return 0.0, True
    root = math.sqrt(disc)
    lo, hi = (-b - root) / a, (-b + root) / a
    if hi < 0 or lo > 1:                    # 可行区间与 [0,1] 不相交
        return 0.0, True
    return max(0.0, min(1.0, hi)), False


class GroupExposureBook:
    """AI 分组内跨成员共享的收益率 / 名义额登记簿（纯同步容器，无锁——见模块 docstring 的 S-7）。

    Attributes:
        unit_notional: 平均单标的满仓名义额 `(Σ budget_i) × leverage / n`（分组建组时算一次）。
        cap_mult: 有效敞口上限倍数（`0` ⇒ 本层不生效，`effective_exposure` 根本不该被调用）。
        bar_seconds: 分组执行周期秒数（收益率按 ts 对齐的刻度）。
    """

    def __init__(self, *, unit_notional: float, cap_mult: float, bar_seconds: int) -> None:
        self.unit_notional = max(0.0, float(unit_notional))
        self.cap_mult = max(0.0, float(cap_mult))
        self.bar_seconds = max(1, int(bar_seconds))
        self._returns: dict[str, dict[int, float]] = {}
        self._notional: dict[str, float] = {}

    # ---------- 喂数（纯同步，全部无 await）----------
    def publish_returns(self, inst_id: str, closes, as_of_ts: int) -> None:
        """把一个成员最近的收盘价序列折成**按 ts 键**的对数收益（对齐的唯一依据）。

        只存收益不存价格：跨成员相关性算的就是收益率，且按 ts 键存使「不同标的的 WS 推送节奏不同」
        自动退化为「重叠样本变少」而不是错位相关（自我反驳五）。
        """
        rows = [px for px in (_finite(c) for c in (closes or [])) if px is not None and px > 0]
        if len(rows) < 2:
            return
        book = self._returns.setdefault(inst_id, {})
        last_ts = int(as_of_ts)
        for offset in range(len(rows) - 1, 0, -1):
            ts = last_ts - (len(rows) - 1 - offset) * self.bar_seconds
            book[ts] = math.log(rows[offset] / rows[offset - 1])
        if len(book) > _RETURNS_MAX:
            for ts in sorted(book)[:len(book) - _RETURNS_MAX]:
                del book[ts]

    def publish_notional(self, inst_id: str, signed_notional: float) -> None:
        """登记一个成员当前的**带符号**名义额（多为正、空为负、空仓 0）。"""
        value = _finite(signed_notional)
        self._notional[inst_id] = 0.0 if value is None else value

    # ---------- 判定 ----------
    def effective_exposure(self, *, inst_id: str, proposed_notional: float,
                           min_samples: int) -> ExposureView | None:
        """本成员按 `proposed_notional` 开仓后的组合有效敞口与缩减系数；上限未配置 ⇒ `None`。

        单成员分组（`n=1`）在 `cap_mult ≥ 1.0` 时**恒不受约束**（S-8）：此时 `E = |w| ≤ unit ≤ cap`。
        这是零回归的**结构性**保证，而不是靠默认值碰巧成立——`cap_mult` 的 `{0} ∪ [1,10]` 校验在启动期
        生效正是为了守住它。
        """
        if self.cap_mult <= 0 or self.unit_notional <= 0:
            return None
        proposed = _finite(proposed_notional) or 0.0
        others = [(other, w) for other, w in self._notional.items()
                  if other != inst_id and abs(w) > 0]
        cap_abs = self.cap_mult * self.unit_notional
        a = proposed * proposed
        b = proposed * sum(w * self._rho(inst_id, other, min_samples) for other, w in others)
        c = sum(w_j * w_k * self._rho(j, k, min_samples)
                for j, w_j in others for k, w_k in others)
        scale, unreachable = solve_exposure_scale(a=a, b=b, c=c, cap_abs=cap_abs)
        overlaps = [self._overlap(inst_id, other) for other, _ in others]
        return ExposureView(
            effective=round(math.sqrt(max(0.0, a + 2.0 * b + c)), 4),
            cap_abs=round(cap_abs, 4), scale=round(scale, 6), unreachable=unreachable,
            members=len(others) + 1, min_overlap=min(overlaps) if overlaps else None)

    # ---------- 相关性 ----------
    def _rho(self, left: str, right: str, min_samples: int) -> float:
        """两成员在**共同时间戳**上的皮尔逊相关；重叠不足 / 方差为 0 ⇒ `1.0`（最保守）。"""
        if left == right:
            return 1.0
        xs, ys = self._aligned(left, right)
        if len(xs) < max(2, int(min_samples)):
            return 1.0
        mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
        cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
        var_x = sum((x - mx) ** 2 for x in xs)
        var_y = sum((y - my) ** 2 for y in ys)
        if var_x <= 0 or var_y <= 0:
            return 1.0
        rho = cov / math.sqrt(var_x * var_y)
        return max(-1.0, min(1.0, rho)) if math.isfinite(rho) else 1.0

    def _aligned(self, left: str, right: str) -> tuple[list[float], list[float]]:
        a_book, b_book = self._returns.get(left) or {}, self._returns.get(right) or {}
        shared = sorted(set(a_book) & set(b_book))
        return [a_book[ts] for ts in shared], [b_book[ts] for ts in shared]

    def _overlap(self, left: str, right: str) -> int:
        a_book, b_book = self._returns.get(left) or {}, self._returns.get(right) or {}
        return len(set(a_book) & set(b_book))


def exposure_wire(view: ExposureView | None, *, corr_scale: float = 1.0) -> dict:
    """`ExposureView` → camelCase 只读读数；未配置上限 ⇒ 三键为 None、`corrScale` 如实 1.0。"""
    if view is None:
        return {"effectiveExposure": None, "effectiveExposureCap": None,
                "corrScale": round(corr_scale, 6), "exposureCapUnreachable": False}
    return {"effectiveExposure": view.effective, "effectiveExposureCap": view.cap_abs,
            "corrScale": round(corr_scale, 6), "exposureCapUnreachable": view.unreachable}


def _finite(value) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None
