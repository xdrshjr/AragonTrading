"""情报引用的服务端裁决与结果码判定（ai-trade-intel-closed-loop 轴 A / 轴 B 的纯核心）。

**纯核心**：无 IO、无网络、无 DB、无时钟。这是本轮「模型只能引用，不能创造」这条承诺的落点——
交易 Agent 只能写 ``I01..I20``，编号表由服务端在定稿注入行时生成；一个编号在表里查不到，就是
一次可观测的异常，而不是一次被静默当成空的情形。

两条贯穿本模块的判断：

1. **「编造」与「这一轮压根没有编号表」是两件事**（评审 P2-2）。把后者也计进 ``fabricated_ref``
   会让「情报层今天降级了」在面板上显示成「模型今天一直在编造引用」——一个把系统故障读成模型
   不诚实的读数，比没有读数更坏。故 `CitationResolution` 带**两个**计数出口。
2. **``void`` 不是 ``flat``**。往返未结算 / 净额不可得写 ``void``，绝不写成 0.0：
   「没结算成功」与「结算成功但打平」写成同一个数，就等于把一次失败伪装成一次平局。
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# 交易 Agent 可写的引用编号（两位定长）。与语料的 ``D[0-9]{1,2}`` 和核查候选的 ``C[0-9]{1,2}``
# 三套正则互斥且各自锚定 ``^…$``——串号不会报错，只会安静地把统计记到别的对象上（R-11）。
REF_RE = re.compile(r"^I[0-9]{2}$")


def format_ref(index: int) -> str:
    """第 k 条（k 从 1 计）的引用编号。**编号表与注入行必须在同一次定稿里生成**（E-14）。"""
    return "I%02d" % int(index)


@dataclass(frozen=True)
class CitationResolution:
    """一次 ``intelRefs`` 的服务端裁决结果。

    Attributes:
        valid: ``(ref_token, item_id)`` 有序元组，顺序与模型给出的顺序一致。
        unknown: 编号表**存在**、但这些编号不在里面 ⇒ 计 ``fabricated_ref``。
        no_refmap: 这一轮压根没有编号表（未注入 / cite 关）时模型仍写了的编号 ⇒ 计
            ``ref_without_digest``。它是系统状态的读数，不是模型诚信的读数（评审 P2-2）。
    """

    valid: tuple[tuple[str, int], ...] = ()
    unknown: tuple[str, ...] = ()
    no_refmap: tuple[str, ...] = ()

    @property
    def item_ids(self) -> tuple[int, ...]:
        return tuple(item_id for _token, item_id in self.valid)


def resolve_citations(refs, refmap: dict[str, int] | None) -> CitationResolution:
    """把模型写的编号裁决成 ``(合法, 编造, 无编号表)`` 三份。

    Args:
        refs: `TradeDecision.intel_refs`（已由 `decider` 过完正则与去重）。
        refmap: 本轮定稿的编号表（``{"I01": item_id, ...}``）；``None`` / 空 ⇒ 这一轮没有编号表。

    Returns:
        `CitationResolution`。``refmap`` 缺席时全部落 `no_refmap`（**而不是全部落 valid**）：
        「模型引用了编号，但这一轮根本没有编号表」是一个应当可观测的异常。
    """
    seen: list[str] = []
    for raw in refs or ():
        token = str(raw or "").strip().upper()
        # **在裁决层也去一次重**：`decider._resolve_intel_refs` 已经去过，但重复的编号在这里
        # 会变成同一条情报的两条引用行 ⇒ `cite_count` 静默翻倍。两处都做的成本是一行。
        if REF_RE.match(token) and token not in seen:
            seen.append(token)
    tokens = seen
    if not refmap:
        return CitationResolution(no_refmap=tuple(tokens))
    valid: list[tuple[str, int]] = []
    unknown: list[str] = []
    for token in tokens:
        item_id = refmap.get(token)
        if item_id is None:
            unknown.append(token)
            continue
        valid.append((token, int(item_id)))
    return CitationResolution(tuple(valid), tuple(unknown), ())


def classify_outcome(net_pnl_usd: float | None, *, eps: float = 1e-9) -> str:
    """净额 → 结果码。

    Args:
        net_pnl_usd: 该笔往返的净额；``None`` ⇒ 结算不可得（接管仓 / 离线平仓）。
        eps: 判平的绝对容差。

    Returns:
        ``win`` / ``loss`` / ``flat`` / ``void``。``None`` 恒回 ``void``，**绝不写成 0.0**：
        ``void`` 不计入 `settled_count`、也不计入 `win_count`（§3.2.2）。
    """
    if net_pnl_usd is None:
        return "void"
    try:
        value = float(net_pnl_usd)
    except (TypeError, ValueError):
        return "void"
    if value > eps:
        return "win"
    if value < -eps:
        return "loss"
    return "flat"


def is_settleable(role: str) -> bool:
    """该角色的引用行是否参与往返结算。

    只有 ``entry`` / ``exit`` 参与：「情报影响了这笔开仓 / 这次离场」可证伪，「情报影响了这次
    继续持有」不可证伪（§1.2 轴 B 的判据）。``hold`` 行永远 ``settled=False`` /
    ``outcome="void"``。
    """
    return str(role or "") in ("entry", "exit")
