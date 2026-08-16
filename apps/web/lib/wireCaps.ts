/**
 * 实时缓冲的前端封顶（单一真源，resilience-and-hci-hardening D4）。
 *
 * 与后端 `services/quant/app/wire_caps.py` **镜像**：`TRADES_MAX` 必须等于
 * `SNAPSHOT_TRADES_MAX`，`EQUITY_MAX` 必须等于 `paper/session.py` 的 `_SNAPSHOT_EQUITY_MAX`。
 * 两地各写一个数字，下一次调参必然悄悄分叉——故 `wireCaps.source.test.ts` 会把两个数字钉在一起。
 *
 * 为什么是 2000 而不是更小：`AiTradeStatsLive` 的全会话总盘 / 盈利捕获率与 `AutoTradePnlPanel`
 * 的时间窗汇总**直接由 `member.trades` 求和**——它不只是展示缓冲，也是这几处统计的分母。
 * 封得太紧会让一个交易产品静默低报自己的收益率。2000 是安全网量级：真实会话触不到，
 * 一旦触到，UI 必须把口径如实改成「最近 2000 笔成交」（见 `AutoTradeTradesTable` 等落点）。
 */

/** 单条成交序列在前端留存的上限（末尾即最新）。与后端 `SNAPSHOT_TRADES_MAX` 镜像。 */
export const TRADES_MAX = 2000;

/** 单条净值序列在前端留存的上限。与后端 `_SNAPSHOT_EQUITY_MAX` 镜像（后端 snapshot 早就只回末 1000 条）。 */
export const EQUITY_MAX = 1000;
