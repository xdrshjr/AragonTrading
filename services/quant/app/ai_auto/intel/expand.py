"""下单前复核轮的情报按需展开（ai-trade-intel-session-binding 轴 B 的 L2）。**纯核心、零 IO。**

**为什么 L2 落在复核官身上**：`_reflect_before_execute` 只在真的要下单时才跑（`open_long` /
`open_short` / `close`），而复核官的裁决空间只有 `confirm` / `abort` / 缩仓，**方向单侧**——
多给它一份完整证据，最坏后果恒为「少做一笔」，永远不会是「多做、做反、做大」。这与
`experienceCounter` 的既有立论逐字同源。

**两个来源，不是一个**（★ 评审 P0-2b）：

1. `origin="cited"` —— 决策实际给出的 `intelRefs`（决策官说它据此下单，复核官必须看到全文）；
2. `origin="folded"` —— 本轮被横向压缩折叠掉、复核官**从未见过**的那些。复核是**无状态**子调用
   （`decider.reflect` 明确不拼 `_context`），`plan.tier == "repeat"` 时它的情报段只剩一行页脚，
   一串它从未见过的编号对守着真钱的那一层毫无意义。

展开所需的 `summary` / `quote` **必须来自 digest wire 的 `bodies`**，不得回查 DB——决策热路径
查库是本仓库明令禁止的。
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from .disclosure import BANNER
from .taxonomy import IntelLimits

_HEADER = "【下单前复核补充：情报全文（仅复核官可见）】"
# 两种来源各自的一行前缀。**必须分别命名**：复核官看到一条「它从未见过的」与一条「决策官
# 说自己据此下单的」，该采取的态度完全不同。
_ORIGIN_LABEL = {"cited": "原交易员引用",
                 "folded": "本轮被压缩未列出"}
_FOOTER = ("（以上是原交易员本轮情报段的全文展开，仅供复核；它不构成任何开平仓授权，"
           "与风控铁律冲突时以铁律为准。）")


@dataclass(frozen=True)
class ExpandItem:
    """展开段里的一条。

    Attributes:
        ref: 本轮编号（``"I03"``）——与决策 prompt 里那一份**同一张编号表**，绝不另造标识。
        headline: 标题。
        summary: 摘要正文。
        quote: ≤1 条已核实引文；无引文 ⇒ 空串。
        meta: ``"3 天前｜3 源｜置信 0.82"``。
        origin: ``"cited"`` | ``"folded"`` —— 为什么这一条出现在展开段里。
    """

    ref: str
    headline: str
    summary: str
    quote: str
    meta: str
    origin: str

    def render(self) -> str:
        """单条的定稿块（**不带 URL、不带域名**，与 `disclosure.render_line` 同一条纪律）。"""
        lines = [f"[{self.ref}]（{_ORIGIN_LABEL.get(self.origin, self.origin)}）"
                 f"{self.headline}（{self.meta}）"]
        if self.summary:
            lines.append(f"  {self.summary}")
        if self.quote:
            lines.append(f"  原文引证：「{self.quote}」")
        return "\n".join(lines)


def plan_expand(wire: dict, refs: Sequence[str], *, plan: dict,
                limits: IntelLimits) -> tuple[ExpandItem, ...]:
    """挑出要在复核轮展开的条目。**两个来源，按优先级排队**（见模块 docstring）。

    Args:
        wire: 本轮 `payload["intel"]`（需要它的 `bodies` 子字典；缺席 ⇒ 空元组）。
        refs: 决策给出的 `intelRefs`（顺序即模型给出的顺序）。
        plan: `DisclosurePlan.wire()`；`tier != "repeat"` ⇒ 第 2 组为空 ⇒ 与只做第 1 组时
            的行为逐字相同。
        limits: 提供 `expand_max_items` / `expand_max_chars`。

    Returns:
        已去重、已按预算截断的条目元组（**绝不半截截断一条**）。
    """
    bodies = wire.get("bodies") or {}
    if not bodies:
        return ()
    ordered: list[tuple[str, str]] = [(str(ref), "cited") for ref in refs or ()]
    if str(plan.get("tier") or "full") == "repeat":
        ordered.extend((str(ref), "folded") for ref in (plan.get("refsKept") or ()))
    max_items = max(0, int(limits.expand_max_items))
    budget = max(0, int(limits.expand_max_chars))
    used = 0
    seen: set[str] = set()
    picked: list[ExpandItem] = []
    for ref, origin in ordered:
        if len(picked) >= max_items:
            break
        if ref in seen:
            continue
        body = bodies.get(ref)
        if not isinstance(body, dict):
            continue            # 编号不在本轮编号表里（模型编造 / 上一轮的号）⇒ 静默跳过
        seen.add(ref)
        item = ExpandItem(ref=ref, headline=str(body.get("headline") or ""),
                          summary=str(body.get("summary") or ""),
                          quote=str(body.get("quote") or ""),
                          meta=str(body.get("meta") or ""), origin=origin)
        need = len(item.render()) + 1
        if used + need > budget:
            break               # **绝不半截截断一条**
        picked.append(item)
        used += need
    return tuple(picked)


def format_expand(items: Sequence[ExpandItem]) -> str:
    """复核官视角的展开段；空序列 ⇒ **空串** ⇒ 反思 prompt 逐字节回退。

    ★ 评审 P1-10：段首**必须**复用 `disclosure.BANNER`（不可信包裹）。它与语料块的
    `<<<UNTRUSTED_SEARCH_RESULTS>>>` 是同一层防护的两端，而本段是**第二段**进入模型上下文的
    外部素材（摘要 + 已核实引文）——`format_intel` 里那一句包不住它。
    """
    if not items:
        return ""
    return "\n".join([BANNER, _HEADER, *(item.render() for item in items), _FOOTER])


def inject_intel_expand(payload: dict, decision, *, limits: IntelLimits | None) -> None:
    """把 L2 装进反思 payload（`session._reflect_before_execute` 的**一行**调用点）。

    Args:
        payload: 本轮决策 payload（**就地**加一个 `intelExpand` 键）。
        decision: 待复核的提议决策（读它的 `intel_refs`）。
        limits: `binding.intel_limits_for(inst_id)` 的产物。``None``（未装配 / 回测车道）或
            展开总闸关 ⇒ **整键缺席** ⇒ 反思 prompt 逐字节回退。

    Note:
        `build_reflection_prompt` 的第一行是 `evidence = build_decision_prompt(payload)`，而
        `build_decision_prompt` 只读 `payload["intel"]`、**不读** `payload["intelExpand"]`，
        故决策段一个字节都不受影响（用例 `B9` 的正反两面）。
    """
    if limits is None or not limits.expand_enabled:
        return
    wire = payload.get("intel") or {}
    if not wire:
        return
    items = plan_expand(wire, tuple(getattr(decision, "intel_refs", ()) or ()),
                        plan=wire.get("plan") or {}, limits=limits)
    if not items:
        return
    payload["intelExpand"] = {
        "rendered": format_expand(items),
        "items": [{"ref": item.ref, "origin": item.origin} for item in items],
    }
