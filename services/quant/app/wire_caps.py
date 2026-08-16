"""实时快照的 wire 级封顶（单一真源，resilience-and-hci-hardening D4）。

**只封顶下发与前端留存，绝不裁剪服务端的会计真值**——`LiveExecutor.trades`（AI 自动交易与实时
自动交易两条会话共用）与 `SimBroker.trades`（模拟盘）是下列判定的真值来源，裁它等于**静默改变
交易行为**：

* `ai_auto/discipline_runtime.py` 的会话累计开仓次数 → 换手纪律闸（裁剪即静默放松阈值）；
* `ai_auto/session.py` 的 `tradeCount` / `avgPnl` 分母 → run metrics 落库（裁剪即历史统计低报）；
* `ai_auto/session.py` 的 `_cost.reconcile_tick(trades=...)` 成本对账窗口（裁剪即净额盈亏失真）；
* `ai_auto/resume.py` 续跑时对整列的回灌（类型与长度契约）。

取值是**安全网**而不是展示策略：前端有两处统计（`AiTradeStatsLive` 的全会话总盘与盈利捕获率、
`AutoTradePnlPanel` 的时间窗汇总）按「全会话」口径直接由这份成交序列求和，封得太紧会静默低报。
2000 笔既能钉死内存与断线重放载荷的上界，又高于真实会话的量级；只有被真正触到时才需要在 UI 上
说明口径变了——那时前端也确实会说明（`trades.length >= TRADES_MAX` 本地即可判定，故 wire 上
**不新增** `tradesTotal` 之类没有读点的字段）。

前端镜像常量在 `apps/web/lib/wireCaps.ts`；两个数字由 `wireCaps.source.test.ts` 钉在一起。
"""
from __future__ import annotations

SNAPSHOT_TRADES_MAX = 2000


def tail_trades(trades: list[dict], cap: int = SNAPSHOT_TRADES_MAX) -> list[dict]:
    """返回末 ``cap`` 条成交（不足则原样）。

    **永远返回新 list**，绝不把内部真值列表借出去——调用方拿到的是快照载荷，一次误传引用就足以
    让某个下游的 ``append`` 写进会计真值。

    Args:
        trades: 会话内部的成交真值列（本函数只读，绝不原地修改）。
        cap: 保留的末尾条数上限。

    Returns:
        新 list，长度为 ``min(len(trades), cap)``，顺序与入参一致（末尾即最新）。
    """
    return list(trades[-cap:]) if len(trades) > cap else list(trades)
