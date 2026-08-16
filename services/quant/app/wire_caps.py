"""实时快照的 wire 级封顶（单一真源，resilience-and-hci-hardening D4）。

**下发与前端留存一律封顶；服务端的会计真值只有在「判定不再依赖列表长度」之后才允许封顶。**
`LiveExecutor.trades`（AI 自动交易与实时自动交易两条会话共用）与 `SimBroker.trades`（模拟盘）
曾经同时承担三个角色——明细台账、计数器、闸门判据——那时裁它等于**静默改变交易行为**：

* `ai_auto/discipline_runtime.py` 的会话累计开仓次数 → 换手纪律闸（裁剪即静默放松阈值）；
* `ai_auto/session.py` 的 `tradeCount` / `avgPnl` 分母 → run metrics 落库（裁剪即历史统计低报）；
* `ai_auto/session.py` 的 `_cost.reconcile_tick(trades=...)` 成本对账窗口（裁剪即净额盈亏失真）；
* `ai_auto/resume.py` 续跑时对整列的回灌（类型与长度契约）。

**红线因此从「绝不裁剪」改写为「先有计数器，再谈封顶」**（trust-boundary-and-signal-integrity D5）。
前提现已满足：`LiveExecutor` 新增了两个显式计数器（`trade_count` / `open_count`），四个 append 点
全部收敛到 `_record_trade`，上面前两条**数列表长度**的读点已改读计数器；第三条（成本对账窗口）
读的是近端切片、第四条是整列回灌，二者都不依赖长度。**刻意不设第三个「已实现盈亏总额」计数器**：
那个数的真值是 `realized_pnl_usd`（平仓回执累加），再按成交腿累加一份就是同一个数落两份，
而这里漂移的是真实资金的盈亏显示。因此 `TRADE_LEDGER_MAX` 只是**内存护栏**——它封的是明细的长度，
封不动任何一个判定。顺序不可颠倒：先封顶后加计数器，就是上一轮明令禁止的那种越界。

`SimBroker.trades`（`engine/simulator.py`）**至今不封顶**：它是回测/模拟撮合的完整产物，会被
整体落库并参与指标计算，封顶会改变回测结果本身。

取值是**安全网**而不是展示策略：前端有两处统计（`AiTradeStatsLive` 的全会话总盘与盈利捕获率、
`AutoTradePnlPanel` 的时间窗汇总）按「全会话」口径直接由这份成交序列求和，封得太紧会静默低报。
2000 笔既能钉死内存与断线重放载荷的上界，又高于真实会话的量级；只有被真正触到时才需要在 UI 上
说明口径变了——那时前端也确实会说明（`trades.length >= TRADES_MAX` 本地即可判定，故 wire 上
**不新增** `tradesTotal` 之类没有读点的字段）。

前端镜像常量在 `apps/web/lib/wireCaps.ts`；两个数字由 `wireCaps.source.test.ts` 钉在一起。
"""
from __future__ import annotations

SNAPSHOT_TRADES_MAX = 2000

# 服务端成交台账（`LiveExecutor.trades`）的内存护栏（D5 第 2 步）。取值按 AI 会话最密的档位估算，
# **远超任何现实会话**：正常情况下永不触发，触发时三个计数器仍是全量真值，故没有任何读点会低报。
# 它不是展示策略——展示侧的封顶是上面的 `SNAPSHOT_TRADES_MAX`。
TRADE_LEDGER_MAX = 20000


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
