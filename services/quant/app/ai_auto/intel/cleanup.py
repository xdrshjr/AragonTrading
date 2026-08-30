"""确定性清理：TTL / 存量扫除 / 已排期失效 / 溢出淘汰（market-intel §3.4.4 + closed-loop §3.4.2）。

**纯核心**：无 IO、无网络、无 DB、无时钟（`now` 一律由调用方传入）。

本模块**是从 `gate.py` 整段搬过来的，零语义变化**：evidence-grounding 的闸⑦ 落地后 `gate.py`
实测 843 行，越过 CLAUDE.md 的 800 行阈值，而 CLAUDE.md 对越线的处置是「超了就再下沉一层」而
不是放宽阈值（spec §十二 / 交付条件 C-9 已把这条处置写死）。搬家的方向是**单向**的——
`cleanup` 依赖 `gate`，`gate` 不依赖 `cleanup`，否则两者会成环。

这四步是「噪声或无效的短线情报可以进行整理或清理」这条需求的**兜底实现**：模型哑火时噪声
照样被清。
"""
from __future__ import annotations

from dataclasses import dataclass, replace

from . import taxonomy as tax
from .gate import CurationAction, IntelItem, expires_for_tier, score_item
from .taxonomy import IntelLimits


@dataclass(frozen=True)
class CleanupPlan:
    """四步纯规则清理的产物（TTL / 溢出 / 已排期失效 / 存量扫除）。全部跳过两把免死金牌。"""

    expire_ids: tuple[int, ...]
    overflow_ids: tuple[int, ...]
    lapsed: tuple[CurationAction, ...]
    # closed-loop §3.4.2 第 5 步：「`sources` 非空且**每一个** source 的 domain 都被拉黑」的条目。
    blocked_ids: tuple[int, ...] = ()

    @property
    def counts(self) -> dict[str, int]:
        # `blocked_sweep` 必须在这张表里（漏了它，`actions_json` 里那个数永远是 0，而拉黑到底
        # 扫掉了几条是用户唯一能看见的后果）。
        return {"ttl": len(self.expire_ids), "overflow": len(self.overflow_ids),
                "lapsed_schedule": len(self.lapsed), "blocked_sweep": len(self.blocked_ids)}


def _all_sources_blocked(item: IntelItem, blocked: frozenset[str]) -> bool:
    """该条目的**每一个**来源域名是否都在黑名单里（来源为空 ⇒ False，绝不扫掉无来源条目）。"""
    domains = [s.domain for s in item.sources if s.domain]
    return bool(domains) and all(domain in blocked for domain in domains)


def plan_cleanup(items: list[IntelItem], *, limits: IntelLimits, now: float,
                 blocked_domains: frozenset[str]) -> CleanupPlan:
    """按当前库存算出四步清理动作。

    这四步是「噪声或无效的短线情报可以进行整理或清理」这条需求的**兜底实现**：模型哑火时噪声
    照样被清。`expires_ts` 恒由**当前 tier** 求出（不变式 I-3）——只要它没随升级重算，第 1 步就会
    在事件发生 72 小时后把中期与长期两层一并扫空。

    Args:
        items: 该标的的库存（本函数自行过滤非 active）。
        limits: 阈值聚合（TTL / 层上限 / 失效宽限 / 引用加分）。
        now: 墙钟秒。
        blocked_domains: 用户拉黑的可注册域名。**无默认值的 keyword-only 必填形参**（评审 P1-8）
            ——给它一个 `frozenset()` 默认值，漏传的症状就是「扫除永远算出空集」而不是一个
            `TypeError`，而静默空转正是本轮要修的那类缺陷。

    Returns:
        `CleanupPlan`。**被证伪不是一条归档理由**（评审 P1-12）：归档只由这四条不依赖模型判断的
        确定性规则做出。
    """
    expire: list[int] = []
    lapsed: list[CurationAction] = []
    blocked_ids: list[int] = []
    survivors: list[IntelItem] = []
    for item in items:
        if item.status != "active" or item.item_id is None:
            continue
        if item.protected:
            survivors.append(item)
            continue
        if item.expires_ts > 0 and now > item.expires_ts:
            expire.append(item.item_id)
            continue
        # 第 5 步：存量扫除。排在 TTL 之后、溢出之前——一条已经过期的条目不必再被算一次拉黑。
        if _all_sources_blocked(item, blocked_domains):
            blocked_ids.append(item.item_id)
            continue
        # 已排期事件失效（评审 P0-1）：没有这一步，「永远的明天」会在库里堆成一排再也不会发生的
        # 预告，而它们的 `stance` 还在持续喂给交易 Agent。
        # ★ evidence-grounding F-1（§3.4.1）：判据的最后半条从 `updated_ts <= event_ts` 换成
        # 专用的兑现位。`apply_intel_verdicts` 末尾无条件写 `updated_ts = now`，而核查候选的
        # **最高优先级 P1** 恰恰是「已排期且时刻已过」——两条代码分属两轮，合起来的效果是：
        # 一条「9 月 3 日主网升级」在 9 月 3 日之后**第一次**被核查，无论判成什么，都会永久退出
        # 这条清理。删掉那半条而不是留着它，是因为「有人动过就别删」这件事已经由 `protected`
        # 在更早一行做掉了（把两个语义压在同一个 `updated_ts` 上正是本缺陷的成因）。
        if (item.is_scheduled and item.event_ts > 0
                and now > item.event_ts + limits.lapse_grace_s
                and item.schedule_resolved_ts == 0):
            target = tax.prev_tier(item.tier)
            if target is None:
                expire.append(item.item_id)
            else:
                lapsed.append(CurationAction(
                    item.item_id, "demote", target, None, "lapsed_schedule",
                    expires_for_tier(tier=target, event_ts=item.event_ts,
                                     captured_ts=item.captured_ts, pinned=False,
                                     limits=limits)))
                survivors.append(replace(item, tier=target))
            continue
        survivors.append(item)
    overflow: list[int] = []
    for tier in tax.TIERS:
        cap = limits.max_active.get(tier, 40)
        rows = [it for it in survivors if it.tier == tier and not it.protected]
        held = len([it for it in survivors if it.tier == tier])
        if held <= cap:
            continue
        # closed-loop §3.2.4 第 3 条：**必须把 limits 显式传下去**——不传就是「第 1 条写了但
        # 溢出淘汰这一路没走到」，正是本仓库的成文教训「用例绿着但分支从没被走到」。
        rows.sort(key=lambda it: score_item(it, now=now, limits=limits))
        overflow.extend(int(it.item_id) for it in rows[:held - cap] if it.item_id)
    return CleanupPlan(tuple(expire), tuple(overflow), tuple(lapsed), tuple(blocked_ids))
