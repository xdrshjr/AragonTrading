"""效用闭环（W-C）：注入台账 → 三个量 → **唯一一条**失效判据 → 自动静音。

`node.weight` 表达的是「这个语境桶历史上赚不赚」，**不是**「把这句话讲给模型之后有没有变好」。
两者是完全不同的量，而后者此前系统从来没有记录过答案。缺了它：

- 用户在管理界面上回答不了「这条经验值不值得留着」——而那正是「经验管理」这件事的核心问题；
- `v1:R-1`（叙事 → 被读到 → 被照做 → 又被写成经验的自我强化回路）**没有任何检测手段**，
  上一轮只能靠「叙事不得自动升 iron」这条静态规则挡住一半。

**四条纪律**（J-1 / J-5 在本模块的具体化，G-11 / G-13 是它们的机械判据）：

1. **只静音，绝不反向加权。** 一条「说亏其实赚」的经验被静音，**不会**变成一条「说赚」的新经验
   ——那等于让系统自己发明一条从未被统计支撑的结论。本模块里因此没有任何一处对 `pinned` /
   `tier` / `l0_max` / `l1_max` / `*_max_chars` 的赋值。
2. `mute_reason="ineffective"` 与 `"user"` **严格分离**：自动解除只碰非 `user` 的静音；样本增长后
   不再满足判据 ⇒ 自动解除。
3. **置顶的经验不参与自动静音**——用户的显式意志优先于统计。但**效用读数照常展示**，让用户自己
   看见「你置顶的这条，注入 30 次之后的均值是 −18bps」。
4. **文案一律「注入后表现」/「与事实一致率」**，绝不使用「贡献」「带来」「因此」这类因果措辞
   （J-5）：「这条经验被注入之后那一单赚了/亏了」是**共现**，模型是否真的采纳了它我们并不知道。
"""
from __future__ import annotations

import math
import time

from sqlalchemy.ext.asyncio import AsyncSession

from ...db import repositories as repo

# 台账里参与 bps 聚合的口径闸（与 `v1:I-7` 逐字同义：毛净混用会让整张表偏移一个往返成本）。
_ELIGIBLE_BASIS = "net"

# 自动静音的原因值。与 `stale` / `flipped` / `user` 并列，是 `mute_reason` 的第四个取值。
MUTE_INEFFECTIVE = "ineffective"


def score(records: list[dict], *, min_samples: int, t_threshold: float) -> dict:
    """一条经验的注入记录 → 三个量 + 一个失效判定（**纯函数**，零 IO）。

    Args:
        records: 该经验的注入台账行（`load_experience_injections` 的产物）。
        min_samples: 判据的第一道门槛（**口径合格**的注入次数，即 `scored`）。
        t_threshold: 判据的第三道门槛（|t|）。

    Returns:
        `{"injected", "scored", "meanNetAfter", "tAfter", "agreement", "claimSign",
          "ineffective"}`；`injected == 0` ⇒ 除计数外全为 `None` / `False`。
    """
    injected = len(records)
    out = {"injected": injected, "scored": 0, "meanNetAfter": None, "tAfter": None,
           "agreement": None, "claimSign": 0, "ineffective": False}
    if injected <= 0:
        return out
    out["claimSign"] = _dominant_sign(records)
    kept = [row for row in records
            if row.get("netBps") is not None and bool(row.get("feeKnown"))
            and (row.get("costBasis") or "gross") == _ELIGIBLE_BASIS]
    out["scored"] = len(kept)
    if not kept:
        return out
    nets = [float(row["netBps"]) for row in kept]
    out["meanNetAfter"] = round(sum(nets) / len(nets), 4)
    out["tAfter"] = _t_stat(nets)
    claim = int(out["claimSign"])
    if claim:
        agree = sum(1 for value in nets if (value > 0) == (claim > 0) and value != 0)
        out["agreement"] = round(agree / len(nets), 4)
    out["ineffective"] = _is_ineffective(out, min_samples=min_samples,
                                         t_threshold=t_threshold)
    return out


def _is_ineffective(metrics: dict, *, min_samples: int, t_threshold: float) -> bool:
    """**唯一一条**失效判据（三重门槛，且只导向静音）。

    `scored >= min_samples` 且 `claim_sign != 0` 且 `sign(meanNetAfter) == -claim_sign`
    且 `|tAfter| >= t_threshold`。

    门槛数的是 `scored`（真的进了 bps 聚合的条数）而**不是** `injected`：口径不合格的注入
    （毛额 / 费率未知）对 `meanNetAfter` 一点贡献都没有，拿它们凑够样本数等于用空气把一条
    经验静音掉。

    三重门槛缺一不可：只看符号会被小样本噪声误杀，只看 |t| 会把「说亏确实亏」也判成失效。
    """
    claim = int(metrics.get("claimSign") or 0)
    mean = metrics.get("meanNetAfter")
    t_value = metrics.get("tAfter")
    if not claim or mean is None or t_value is None:
        return False
    if int(metrics.get("scored") or 0) < int(min_samples):
        return False
    observed = 0 if mean == 0 else (1 if mean > 0 else -1)
    return observed == -claim and abs(float(t_value)) >= float(t_threshold)


def _dominant_sign(records: list[dict]) -> int:
    """该经验注入当时断言的净额符号；多次注入取**最近一次**的冻结值（绝不回填）。

    经验会随新证据翻转，用今天的符号去评判昨天那次注入是一句事后诸葛的假话。
    """
    for row in reversed(records):
        claim = int(row.get("claimSign") or 0)
        if claim:
            return claim
    return 0


def _t_stat(values: list[float]) -> float | None:
    """单样本 t 统计量；与 `aggregate._t_stat` **逐字同口径**（两处算出不同的 t 就是两套事实）。"""
    n = len(values)
    if n < 2:
        return None
    mean = sum(values) / n
    var = sum((v - mean) ** 2 for v in values) / (n - 1)
    if var <= 0:
        return None
    return round(mean / (math.sqrt(var) / math.sqrt(n)), 4)


# ---------------- 编排（跑在**调用方已持有的**物化锁内，★ E-13） ----------------
async def refresh(session: AsyncSession, settings) -> dict:
    """重算全部经验的效用读数，并据唯一那条判据自动静音 / 自动解除。

    成本上界：一次 `SELECT` 扫注入台账（与事件表同量级、每笔往返 ≤ `l0_max + l1_max` 行）
    + 纯内存聚合，**无 CPU 密集段**，故不进 `run_in_threadpool`。

    Args:
        session: DB 会话（调用方持物化锁——它与 `mark_stale_lessons` 写同一批列）。
        settings: 派生后的 `Settings`。

    Returns:
        `{"scored": 有注入样本的经验条数, "ineffective": 本次判为失效的条数,
          "muted": 本次新静音条数, "unmuted": 本次自动解除条数}`。
    """
    if not bool(settings.ai_trade_experience_utility_enabled):
        return {"scored": 0, "ineffective": 0, "muted": 0, "unmuted": 0}
    by_key = _group(await repo.load_experience_injections(session))
    nodes = {row["key"]: row for row in
             await repo.load_experience_nodes(session, kinds=("lesson",))}
    min_samples = int(settings.ai_trade_experience_utility_min_samples)
    t_threshold = float(settings.ai_trade_experience_utility_t_threshold)
    scored = ineffective = muted = unmuted = 0
    for key, records in sorted(by_key.items()):
        node = nodes.get(key)
        if node is None:
            continue
        scored += 1
        metrics = score(records, min_samples=min_samples, t_threshold=t_threshold)
        if metrics["ineffective"]:
            ineffective += 1
        # 纪律 3：用户的显式意志优先于统计——置顶的经验读数照常算，但绝不自动静音。
        if node.get("pinned"):
            continue
        if metrics["ineffective"] and not node.get("muted"):
            await repo.set_experience_lesson_mute(session, node["id"], muted=True,
                                                 reason=MUTE_INEFFECTIVE)
            muted += 1
        elif (not metrics["ineffective"] and node.get("muted")
              and node.get("mute_reason") == MUTE_INEFFECTIVE):
            # 纪律 2：样本增长后不再满足判据 ⇒ 自动解除；`user` 静音**绝不**被自动改写。
            await repo.set_experience_lesson_mute(session, node["id"], muted=False,
                                                 reason=None)
            unmuted += 1
    return {"scored": scored, "ineffective": ineffective, "muted": muted, "unmuted": unmuted}


async def muted_count(session: AsyncSession, settings) -> int:
    """当前因「注入后表现与其结论系统性相反」被自动静音的条数（启动弹窗那一行的取数）。"""
    if not bool(settings.ai_trade_experience_utility_enabled):
        return 0
    rows = await repo.load_experience_nodes(session, kinds=("lesson",))
    return sum(1 for row in rows
               if row.get("muted") and row.get("mute_reason") == MUTE_INEFFECTIVE)


async def overview_wire(session: AsyncSession, settings) -> dict | None:
    """概览条的 `utility` 块；总闸关 ⇒ `None`（**整键缺席**，不造一个恒为 0 的假读数）。"""
    if not bool(settings.ai_trade_experience_utility_enabled):
        return None
    by_key = _group(await repo.load_experience_injections(session))
    return {"scored": len(by_key), "ineffective": await muted_count(session, settings)}


async def by_keys(session: AsyncSession, settings, keys: list[str]) -> dict[str, dict]:
    """一批经验 key → 效用读数（列表页的「效用」列与详情抽屉共用**同一个换算点**）。

    没有注入样本的 key **不出现在返回值里**——列表页据此渲染 `—`，而不是渲染一个「注入 0 次、
    之后 0bps」的假读数。
    """
    if not keys or not bool(settings.ai_trade_experience_utility_enabled):
        return {}
    wanted = set(keys)
    by_key = _group(await repo.load_experience_injections(session))
    min_samples = int(settings.ai_trade_experience_utility_min_samples)
    t_threshold = float(settings.ai_trade_experience_utility_t_threshold)
    return {key: score(records, min_samples=min_samples, t_threshold=t_threshold)
            for key, records in by_key.items() if key in wanted}


async def detail(session: AsyncSession, settings, key: str) -> dict | None:
    """详情抽屉的「注入后表现」区：三个量 + 最近 N 条注入记录；总闸关 / 无样本 ⇒ `None`。"""
    if not key or not bool(settings.ai_trade_experience_utility_enabled):
        return None
    records = await repo.load_experience_injections(session, lesson_key=key)
    if not records:
        return None
    metrics = score(records,
                    min_samples=int(settings.ai_trade_experience_utility_min_samples),
                    t_threshold=float(settings.ai_trade_experience_utility_t_threshold))
    rows = int(settings.ai_trade_experience_utility_detail_rows)
    return {**metrics, "injections": await repo.load_experience_injections(
        session, lesson_key=key, limit=rows)}


async def list_view(session: AsyncSession, settings, filters: dict) -> dict:
    """`GET /api/experience/utility`：效用面板的清单（失效优先、按 |t| 降序）。

    筛选判据复用 `service._match`——**绝不另写一份**：两条路径必然漂移，而这里漂移的是
    「同一筛选下面板说 12 条、列表说 9 条」，用户完全无从判断哪个是真的。
    """
    from .service import _match

    min_injected = max(0, int(filters.get("minInjected") or 0))
    limit = max(1, min(int(filters.get("limit") or 200), 500))
    nodes = [row for row in await repo.load_experience_nodes(session, kinds=("lesson",))
             if _match(row, filters)]
    metrics = await by_keys(session, settings, [row["key"] for row in nodes])
    items = []
    for row in nodes:
        hit = metrics.get(row["key"])
        if hit is None or int(hit["injected"]) < min_injected:
            continue
        items.append({"key": row["key"], "id": row["id"], "label": row.get("label"),
                      "muted": bool(row.get("muted")), "muteReason": row.get("mute_reason"),
                      "pinned": bool(row.get("pinned")), **hit})
    items.sort(key=lambda item: (not item["ineffective"],
                                 -abs(float(item.get("tAfter") or 0.0)), item["key"]))
    return {"items": items[:limit], "total": len(items), "asOfTs": int(time.time()),
            "minSamples": int(settings.ai_trade_experience_utility_min_samples),
            "tThreshold": float(settings.ai_trade_experience_utility_t_threshold)}


async def prune_stale(session: AsyncSession, *, dry_run: bool) -> dict:
    """清理**沉降**经验（`source="stat"` 且已静音且非用户静音且未置顶）。

    ★ 为什么必须同批交付：J-4 落地当天会让一批 `unknown` 桶的经验在下一次物化中不再出现 ⇒ 被
    `mark_stale_lessons` 判为沉降。那是**正确的终局**，但会在管理界面上一次性冒出一批用户
    无法处置的条目——不给清理入口，用户看到的就是一次「数据坏了」。

    **绝不静默批删**：`dry_run=True` 必须先跑一次并把条数显示给用户（A-34 断言两次的条数一致）。
    顺带清掉指向已删经验的孤儿台账行（R-9 的无界增长防线）。

    Args:
        session: DB 会话。
        dry_run: 只数不删。

    Returns:
        `{"matched", "deleted", "skippedPinned", "skippedUserMuted"}`。
    """
    rows = await repo.load_experience_nodes(session, kinds=("lesson",))
    matched, skipped_pinned, skipped_user = [], 0, 0
    for row in rows:
        if row.get("source") != "stat" or not row.get("muted"):
            continue
        if row.get("pinned"):
            skipped_pinned += 1
            continue
        if row.get("mute_reason") == "user":
            skipped_user += 1
            continue
        matched.append(row)
    out = {"matched": len(matched), "deleted": 0, "skippedPinned": skipped_pinned,
           "skippedUserMuted": skipped_user}
    if dry_run or not matched:
        return out
    for row in matched:
        await repo.delete_experience_node(session, row["id"])
    await repo.delete_orphan_experience_injections(
        session, {row["key"] for row in matched})
    out["deleted"] = len(matched)
    return out


def _group(records: list[dict]) -> dict[str, list[dict]]:
    """台账行按 `lessonKey` 分桶（保持 `exit_ts` 升序——`_dominant_sign` 取最近一条靠它）。"""
    out: dict[str, list[dict]] = {}
    for row in records:
        out.setdefault(str(row.get("lessonKey") or ""), []).append(row)
    out.pop("", None)
    return out
