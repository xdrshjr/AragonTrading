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
from dataclasses import dataclass, replace

# 每个成员保留的收益率样本上限（按 ts 键存；远超任何合理的 corr_min_samples，防长跑无界增长）。
_RETURNS_MAX = 2000


@dataclass(frozen=True)
class ExposureView:
    """一次有效敞口判定的只读产物。

    Attributes:
        effective: 按**提议原值**（s=1）算出的有效敞口（USD 名义额）。
        cap_abs: 本分组的有效敞口上限 = `exposure_cap_mult × 平均单标的满仓名义额`。
        scale: 闭式解出的缩减系数 ∈ [0,1]（1.0 = 无需缩减）。
        unreachable: 当前提议在 `s ∈ [0,1]` 内没有可行解；不表示所有对冲都不可行。
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
        `(s, unreachable)`。`unreachable=True` 表示该提议在 `[0,1]` 内无解，返回
        `s=0`；仓位运行时必须拒绝没有可行正仓位的提议。该纯函数不发送订单。
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
        self._providers: dict = {}

    def register(self, inst_id: str, provider) -> None:
        """登记同步断面函数，返回持仓名义额与可能成交的剩余名义额。"""
        self._providers[inst_id] = provider

    def _bounds(self, exclude: str) -> dict[str, tuple[float, float]]:
        bounds = {inst: (value, value) for inst, value in self._notional.items()
                  if inst != exclude}
        for inst, provider in self._providers.items():
            if inst == exclude:
                continue
            actual, pending = provider()
            bounds[inst] = (min(actual, actual + pending),
                            max(actual, actual + pending))
        return bounds

    def allows(self, inst_id: str, proposed: float, min_samples: int) -> bool:
        """检查最终名义额及在途订单所有成交端点的保守二次型上界。

        固定持仓按带符号相关性计入；挂单用区间表示，未成交对冲不抵扣。
        逐项取区间最大值是 O(n²) 的安全上界，可能保守拒绝，绝不放宽上限。
        容差为计算项量级的 16 ULP（USD²），不使用展示舍入精度。
        """
        if _finite(proposed) is None:
            return False
        if self.cap_mult <= 0 or self.unit_notional <= 0:
            return True
        bounds = self._bounds(inst_id)
        bounds[inst_id] = (proposed, proposed)
        terms = []
        for left, xs in bounds.items():
            for right, ys in bounds.items():
                if not all(math.isfinite(v) for v in (*xs, *ys)):
                    return False
                rho = self._rho(left, right, min_samples)
                terms.append(max(x * y * rho for x in xs for y in ys))
        cap_sq = (self.cap_mult * self.unit_notional) ** 2
        magnitude = max(cap_sq, math.fsum(abs(term) for term in terms))
        if not math.isfinite(magnitude):
            return False
        return math.fsum(terms) <= cap_sq + 16 * math.ulp(magnitude)

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
        current = dict(self._notional)
        current.update({inst: provider()[0]
                        for inst, provider in self._providers.items()})
        others = [(other, w) for other, w in current.items()
                  if other != inst_id and abs(w) > 0]
        cap_abs = self.cap_mult * self.unit_notional
        a = proposed * proposed
        b = proposed * sum(w * self._rho(inst_id, other, min_samples) for other, w in others)
        c = sum(w_j * w_k * self._rho(j, k, min_samples)
                for j, w_j in others for k, w_k in others)
        scale, unreachable = solve_exposure_scale(a=a, b=b, c=c, cap_abs=cap_abs)
        overlaps = [self._overlap(inst_id, other) for other, _ in others]
        return ExposureView(
            effective=math.sqrt(max(0.0, a + 2.0 * b + c)),
            cap_abs=cap_abs, scale=scale, unreachable=unreachable,
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
    return {"effectiveExposure": round(view.effective, 4), "effectiveExposureCap": round(view.cap_abs, 4),
            "corrScale": round(corr_scale, 6), "exposureCapUnreachable": view.unreachable}


def _finite(value) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


class OrderRiskGuard:
    """单成员开仓提交保护，复用执行器订单身份及恢复状态，无 IO。"""

    def __init__(self, executor, book, rule, step: float):
        self.executor, self.book, self.rule = executor, book, rule
        self.step = step
        self.max_fraction = 0.0
        self.max_notional = 0.0
        self.mark_px = 0.0
        self.reserved = None
        self.close_pending = False
        if book is not None:
            book.register(executor.inst_id, self.snapshot)

    def set_limit(self, fraction: float, full: float) -> None:
        """冻结决策时风险上限，后续权益或预算上涨不能放大该提议。"""
        self.max_fraction = fraction
        self.max_notional = fraction * full

    def _allows(self, signed: float) -> bool:
        return (self.book is None or self.rule.exposure_cap_mult <= 0
                or self.book.allows(self.executor.inst_id, signed,
                                    self.rule.corr_min_samples))

    def reserve(self, order, ref_px: float, strength: float):
        """按实际提交价格取张数，检查并占用在一次同步调用中完成。"""
        from .sizing import risk_fraction_candidates

        ex = self.executor
        if ex.projected == "flat":
            # 反手可在两次组合快照之间完成，旧平仓状态不能传给新开仓腿。
            self.close_pending = False
        if self.reserved is not None:
            return None
        px = ref_px if order.px is None else order.px
        full = ex.budget_usd * ex.leverage
        values = (px, full, ex.ct_val, ex.lot_sz, ex.min_sz,
                  self.max_fraction, self.max_notional, strength)
        if not all(_finite(v) is not None and v > 0 for v in values):
            return None
        upper = min(strength, self.max_fraction, self.max_notional / full)
        sign = 1 if order.direction == "long" else -1
        for fraction in risk_fraction_candidates(upper, self.step):
            size = ex._compute_sz(fraction, px)
            notional = size * ex.ct_val * px
            # snap_size 自带 epsilon；最终比较仍不可把它变成额外风险额度。
            if notional > fraction * full:
                size = max(0.0, size - ex.lot_sz)
                notional = size * ex.ct_val * px
            if size < ex.min_sz or size <= 0 or not self._allows(sign * notional):
                continue
            selected = replace(order, sz=size)
            self.mark_px = ref_px
            self.reserved = (selected, notional)
            return selected
        return None

    def validate(self, order, *, size: float, ct_val: float, px: float) -> bool:
        """跨 await 后及传输前按最终量复核；不改已持久化的委托数量。"""
        if self.reserved is None:
            return False
        reserved, cap = self.reserved
        values = (size, ct_val, px, cap)
        if not all(_finite(v) is not None and v > 0 for v in values):
            return False
        notional = size * ct_val * px
        if (reserved.client_order_id != order.client_order_id
                or abs(size - reserved.sz) > 8 * math.ulp(reserved.sz)
                or notional > cap + 8 * math.ulp(cap)):
            return False
        return self._allows(notional if order.direction == "long" else -notional)

    def finish(self, receipt) -> None:
        """明确未发送/拒绝可释放；确认单移交既有挂单/持仓/恢复身份。"""
        if self.reserved is None or receipt is None:
            return
        order, _ = self.reserved
        if receipt.cl_ord_id != order.client_order_id:
            return
        ex = self.executor
        pending = getattr(getattr(ex, "open_attempts", None), "pending", None)
        transferred = (pending is not None and pending.cl_ord_id == receipt.cl_ord_id)
        if (receipt.status in {"not_submitted", "rejected"} or transferred
                or (receipt.status == "accepted"
                    and (ex.resting is not None or ex.projected != "flat"))):
            self.reserved = None

    def snapshot(self) -> tuple[float, float]:
        """平仓未决时对冲腿可能归零；撤余单不能证明旧持仓尚完整。"""
        ex = self.executor
        actual, pending = self._open_snapshot()
        if ex.projected == "flat":
            self.close_pending = False
        if ((ex.resting is not None and ex.resting.intent == "close")
                or getattr(ex, "_settling", None) is not None):
            self.close_pending = True
        if self.close_pending:
            # 同时存在未决开仓与平仓时不猜净数量；只限制分组新增风险。
            return actual, -actual if pending == 0 else math.inf
        return actual, pending

    def _open_snapshot(self) -> tuple[float, float]:
        """实时持仓与余单区间，部分成交只计一次；未知恢复契约封闭放行。"""
        ex = self.executor
        sign = 1 if ex.projected == "long" else -1 if ex.projected == "short" else 0
        px = self.mark_px or ex.entry_price
        actual = sign * ex.entry_qty_sz * ex.ct_val * px
        attempts = getattr(ex, "open_attempts", None)
        pending = getattr(attempts, "pending", None)
        if pending is not None:
            if pending.ack == "rejected":
                return actual, 0.0
            if "riskNotionalCap" not in pending.order:
                return actual, math.inf
            return actual, self._pending_notional(
                pending.order, pending.adopted_qty)
        if attempts is not None and attempts.has_pending:
            return actual, math.inf
        resting = ex.resting
        if resting is not None and resting.intent == "open":
            details = attempts.order_details(resting.cl_ord_id) if attempts else {}
            if attempts is not None and "riskNotionalCap" not in details:
                return actual, math.inf
            details = {"sz": resting.sz, "px": resting.px,
                       "direction": resting.side, **details}
            return actual, self._pending_notional(details, resting.adopted_sz)
        if self.reserved is not None:
            order, notional = self.reserved
            return actual, notional if order.direction == "long" else -notional
        return actual, 0.0

    def _pending_notional(self, order: dict, adopted: float) -> float:
        size = float(order["sz"])
        cap = order.get("riskNotionalCap")
        if cap is None:
            px = order.get("px")
            if _finite(px) is None or px <= 0:
                return math.inf
            cap = size * self.executor.ct_val * px
        if _finite(cap) is None or cap <= 0 or size <= 0:
            return math.inf
        remaining = max(0.0, size - adopted) / size * cap
        return remaining if order["direction"] == "long" else -remaining
