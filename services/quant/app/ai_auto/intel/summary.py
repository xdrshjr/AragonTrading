"""`AiTradeIntelSummary` 的唯一装配点（**从 `service.py` 整段搬过来的，零语义变化**）。

搬家的理由与 `settings_bridge.py` 逐字同源：`service.py` 在 session-binding 轮加了
`IntelDeps.session_rule` / `inject_on` / `intel_mode` 与 `build_snapshot` 的档位透传之后实测
越过 CLAUDE.md 二的 800 行阈值，而 CLAUDE.md 对越线的处置是「超了就再下沉一层」而不是放宽
阈值。方向是**单向**的——本模块不 import `service` 的模块级名字（那两个辅助函数在函数体内
延迟取，见下），`service` 在文件末尾原样重新导出 `read_summary`，故既有三个
`intel_service.read_summary(...)` 调用点**一个字节不改**。
"""
from __future__ import annotations

import time

from ...db import repositories as repo
from . import arming
from . import efficacy as efficacy_mod
from . import taxonomy as tax
from .binding import unbound_wire


def static_summary(inst_id: str, *, code: str) -> dict:
    """未装配 / 已关闭时的静态 summary（**仍然下发**，否则降级在界面上恒不可见）。

    **签名一个字节不改**（评审 P1-2）：`code` 仍是一个降级码，arm 四键由
    `arming.arm_of_code(code)` 解出来之后经 `arm_wire` 这**同一个**写点出键，§3.0 第 3 条
    「同一列只有一个人写」因此没有被破坏，而 `tests/test_ai_intel_alert_shutdown.py` 里那次
    `_static_summary(INST, code="no_provider")` 也继续绿（`no_provider` 不在 `ARM_REASONS` 里，
    走 `arm_of_code` 的诚实回落分支）。

    `"degrade": None` 是一个**占位**（与 `service.read_summary` 逐字同形）：`arm_wire` 只在未
    装配时写它，占位保证这个键恒在位——前端读到 `undefined` 会把「没装上」画成「一切正常」。
    """
    return {
        "instId": inst_id,
        "counts": {"ultra": 0, "mid": 0, "long": 0, "archived": 0},
        "unread": 0, "lastSeenTs": 0, "lastCollectTs": None, "lastCurateTs": None,
        "nextDueTs": None, "inFlight": False,
        "degrade": None,
        "dailyUsed": 0, "dailyCap": 0,
        # closed-loop §3.6：两块新读数在**静态 summary 上也必须在位**（形状一致），否则未装配的
        # 分组会让前端读到 `undefined` 并把「情报层没装上」画成「引用了 0 次」。
        "citations": {"cited": 0, "entryCited": 0, "settledTrips": 0, "winCount": 0,
                      "netPnlUsd": 0.0, "neverCited": 0},
        "verify": {"confirmed": 0, "refuted": 0, "unresolved": 0, "pending": 0,
                   "lastVerifyTs": None},
        # evidence-grounding §4.2：静态 summary 上也必须**在位**（形状一致），否则未装配的分组会
        # 让前端读到 `undefined`。`None` = 分母 0（「没提过引文」），与 0（「提了全没中」）分开。
        "quoteHitRate": None,
        # actionability §4.2：轴 A 的两个新键与轴 B 的摘要块**在静态 summary 上同样必须在位**
        # ——上一轮 `citations` / `verify` / `quoteHitRate` 三次踩过同一个坑：前端读到
        # `undefined` 会把「情报层没装上」画成「零告警」。
        "alerts24h": 0,
        "lastAlertTs": None,
        "efficacy": {"enabled": False, "kMin": 0, "sampleN": 0,
                     "overall": {"settled": 0, "wins": 0, "netPnlUsd": 0.0,
                                 "confirmed": 0, "refuted": 0, "unresolved": 0,
                                 "samples": 0, "accuracy": None}},
        # session-binding 轴 C：未装配的壳同样带绑定块（**形状恒定优先于省一个键**）：
        # 前端读到 `undefined` 会把「没绑定」画成「一切正常」（E-18，历轮四次）。
        "binding": unbound_wire(),
        # arm-state 轴 A：四键 + `degrade` 从**同一个写点**出（§3.0 第 3 条）。
        **arming.arm_wire(arming.arm_of_code(code)),
    }


async def read_summary(db, inst_id: str, *, settings, arm,
                       now: float | None = None) -> dict:
    """`AiTradeIntelSummary` 的**唯一**装配点（runtime 快照与 REST 读端点共用）。

    两处各拼一份的后果不是抽象的：本仓库的 `groupStateFromStatus` 就是那样一个逐键手抄的映射，
    它反复制造「WS 通路是好的，只有刷新后重发现拿不到值」这类最难发现的坑。

    Args:
        arm: `arming.ArmState`（**取代**原来的 `provider_configured: bool`，形参个数不变）。
            arm 四键 + 未装配时的 `degrade` 由 `arming.arm_wire` 这**一个**写点在返回字典的
            末尾展开——§3.0 第 3 条「同一列只有一个人写」的落点。
    """
    # 延迟 import 打破搬家造成的环：`service` 在文件末尾重新导出本函数，模块级反向 import
    # 会让「谁先被 import」决定成不成功——那是一个只在部分入口下复现的 ImportError。
    from .service import daily_used, read_efficacy

    wall = time.time() if now is None else now
    watch = await repo.load_intel_watch(db, inst_id) or {}
    counts = await repo.count_intel_by_tier(db, inst_id)
    unread = await repo.count_intel_unread(
        db, inst_id, last_seen_ts=int(watch.get("lastSeenTs") or 0))
    impact = await repo.read_intel_impact(db, inst_id, limit=1)
    verdicts = await repo.count_intel_verdicts(db, inst_id)
    # evidence-grounding 评审 P1-13：`quoteHitRate` 就在**这一个只读会话**里求出。写在
    # `build_snapshot` 里要么多开一个会话，要么走进「同步路径造不出一个 DB 派生键」那个坑。
    quote_stats = await repo.count_recent_quote_stats(db, inst_id)
    # actionability §4.2：告警的两个会话级读数走**既有** `intel` 事件，不新增任何 WS 事件类型。
    alerts = await repo.count_recent_alerts(db, inst_id, since_ts=int(wall) - 86400)
    # actionability §3.2.4：**摘要形态**的四个数（完整的 `by_category` / `by_domain` 由 REST
    # 拉——summary 走 WS 逐轮广播给每个前端连接，一张几十行的表广播出去是纯浪费）。
    #
    # ⚠️ 拼在这里而不是 `build_snapshot` 里：本函数是 `AiTradeIntelSummary` 的**唯一**装配点，
    # 而 `api/ai_intel._summary` 也调它。写在 `build_snapshot` 里会让 WS 推的那一份带
    # `efficacy`、REST 读的那一份不带 —— 两条通路形状分叉，而分叉**不报错**。
    # 代价是快照路径上多求解一次：`efficacy.solve` 是纯函数，两处同值（G-28 的性质）。
    limits = tax.limits_from_settings(settings)
    eff = await read_efficacy(db, inst_id, limits=limits, now=wall)
    return {
        "instId": inst_id,
        "counts": {"ultra": counts["ultra"], "mid": counts["mid"], "long": counts["long"],
                   "archived": counts["archived"]},
        "unread": unread,
        "lastSeenTs": int(watch.get("lastSeenTs") or 0),
        "lastCollectTs": int(watch.get("lastCollectTs") or 0) or None,
        "lastCurateTs": int(watch.get("lastCurateTs") or 0) or None,
        "nextDueTs": int(watch.get("nextDueTs") or 0) or None,
        "inFlight": False,
        "degrade": None,
        "dailyUsed": daily_used(watch, now=wall),
        "dailyCap": int(settings.ai_trade_intel_daily_round_cap),
        # ---- closed-loop §3.6：两块新读数（`snapshot()` 的第八键与 WS `intel` 事件共用同一个
        # 出口，**不新增第九键**）。`netPnlUsd` 是**关联**净额（共现统计，不是因果归因，R-1）。
        "citations": {
            "cited": int(impact["totals"]["citeCount"]),
            "entryCited": int(impact["totals"]["citeEntryCount"]),
            "settledTrips": int(impact["totals"]["settledCount"]),
            "winCount": int(impact["totals"]["winCount"]),
            "netPnlUsd": float(impact["totals"]["netPnlUsd"]),
            "neverCited": int(impact["neverCited"]),
        },
        "verify": {
            "confirmed": int(verdicts.get("confirmed", 0)),
            "refuted": int(verdicts.get("refuted", 0)),
            "unresolved": int(verdicts.get("unresolved", 0)),
            "pending": int(verdicts.get("pending", 0)),
            "lastVerifyTs": int(watch.get("lastVerifyTs") or 0) or None,
        },
        # 近 20 轮 `quotesVerified / quotesProposed`。**分母 0 ⇒ `None` 而不是 0**：
        # 「没提过引文」与「提了全没中」在设置卡上是两句不同的话。
        "quoteHitRate": quote_stats["hitRate"],
        # actionability 轴 A：近 24 小时 fired 计数与最近一次告警时刻。
        # `lastAlertTs` 为 `None` = **从未告警过**，与 0 分开（后者会被渲染成 1970 年）。
        # 未装配的分组由 `runtime._static_summary` 给出**同形状**的零值（G-25）。
        "alerts24h": int(alerts["alerts24h"]),
        "lastAlertTs": alerts["lastAlertTs"],
        "efficacy": {
            "enabled": bool(limits.efficacy_enabled),
            "kMin": int(limits.efficacy_k_min),
            "sampleN": int(eff.overall.samples),
            "overall": efficacy_mod.stat_wire(eff.overall),
        },
        # session-binding 轴 C：绑定块。**DB 路径同样要写一份**（未绑定态）——形状恒定优先于
        # 省一个键：缓存缺席时 REST 会直接返回本函数的产物，少一个键会让前端读到 `undefined`
        # 并把「没绑定」画成「一切正常」（E-18 的成文教训，历轮四次）。运行时那一份由
        # `IntelRuntime.summary_for` 在**出口**覆写，与 arm 四键同一条纪律。
        "binding": unbound_wire(),
        # arm-state 轴 A：`providerConfigured` / `intelEnabled` / `armed` / `armReason` 四键 +
        # 未装配时的 `degrade`，**全部由这一个写点出**。展开在字典**末尾**是刻意的：它对
        # `degrade` 的写入要盖住上面那行 `"degrade": None`，而 `IntelRuntime.refresh` 里那段
        # 「本轮降级」的覆写仍在本函数**之后**发生 —— 三者的优先级：轮内降级 > 未装配 > 无。
        **arming.arm_wire(arm),
    }
