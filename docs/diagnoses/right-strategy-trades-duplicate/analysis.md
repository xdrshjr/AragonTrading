# Right Strategy Trades Duplicate Analysis

## Problem description

用户反馈：中间 K 线面板下方已经有“成交记录”，右侧“交易策略”面板下方的
“成交明细”与其重复，右侧“成交明细”不再需要。

本轮任务只做问题定位和分析，不写修复代码。

## Reproduction steps

手工复现路径：

1. 在仓库根目录准备依赖。

   ```powershell
   pnpm setup
   ```

2. 启动前后端。

   ```powershell
   pnpm dev
   ```

3. 打开 `http://localhost:18761`。
4. 在顶部模式切换中选择“实时”。
5. 右侧切到“交易策略”面板，选择“常规策略”或“组合策略”，勾选一个或多个策略。
6. 点击“启动实时模拟”，等待 WebSocket 快照或成交事件进入。
7. 在中间 K 线面板下方选择“成交记录”。
8. 在右侧交易策略面板底部选择“成交明细”。

预期：成交列表只在中间 K 线面板下方保留一个入口。

实际：中间下方“成交记录”和右侧“成交明细”两个区域都渲染成交表。实时模式下，
中间下方显示当前激活策略的实时成交，右侧继续通过当前 `runId` 拉取同一运行的
成交明细，因此信息重复。

最小状态复现模型：

- `mode = "paper"`
- `paperSessionId != null`
- `runId != null`
- `bottomTab = "trades"`
- 右侧 `StrategyPanel` 的局部 `tab = "trades"`

在这组状态下，页面会同时渲染两个 `TradesTable`。

## Root cause analysis

根因是假设冲突：上轮提交把“常规策略 / 组合策略”页签扩展到实时模拟面板，但没有重新收敛
右侧交易策略面板底部的结果区。结果是实时模式中，右侧仍保留历史的
`绩效指标 / 成交明细 / 历史` 页签，同时中间 K 线面板下方已经有独立的
`成交记录 / 当前持仓` 区域。

证据 1：中间 K 线面板下方定义了“成交记录”页签，并在该页签渲染 `TradesTable`。

- `apps/web/components/Chart/ChartWorkspace.tsx:40`
- `apps/web/components/Chart/ChartWorkspace.tsx:41`
- `apps/web/components/Chart/ChartWorkspace.tsx:348`
- `apps/web/components/Chart/ChartWorkspace.tsx:349`

证据 2：页面在实时模式下把实时模拟成交数据整理为 `bottomTrades`，再传给
`ChartWorkspace`，所以中间下方是实时成交列表的主展示面。

- `apps/web/app/page.tsx:375`
- `apps/web/app/page.tsx:376`
- `apps/web/app/page.tsx:399`
- `apps/web/app/page.tsx:404`
- `apps/web/app/page.tsx:500`
- `apps/web/app/page.tsx:505`

证据 3：右侧 `StrategyPanel` 仍无条件保留“成交明细”页签，并在选中该页签时再次渲染
`TradesTable`。

- `apps/web/components/Strategy/StrategyPanel.tsx:627`
- `apps/web/components/Strategy/StrategyPanel.tsx:630`
- `apps/web/components/Strategy/StrategyPanel.tsx:687`

证据 4：右侧 `StrategyPanel` 在实时模式且存在 `paperSessionId` 时，每 3 秒按当前
`runId` 调用 `api.trades` 拉取成交明细。这与中间下方实时成交流展示的是同一运行语义下
的成交数据。

- `apps/web/components/Strategy/StrategyPanel.tsx:286`
- `apps/web/components/Strategy/StrategyPanel.tsx:288`
- `apps/web/components/Strategy/StrategyPanel.tsx:290`

评审补充证据：

- 右侧底部结果区的外层条件不是“仅回测”。`StrategyPanel` 在 `selected && (...)`
  下只把 `mode === "backtest" && backtestKind === "walkforward"` 特判为
  `WalkForwardResults`，其它情况都会进入 `绩效指标 / 成交明细 / 历史` 页签分支。
  因此 `mode === "paper"` 会实际执行到右侧成交页签：
  `apps/web/components/Strategy/StrategyPanel.tsx:613`、
  `apps/web/components/Strategy/StrategyPanel.tsx:614`、
  `apps/web/components/Strategy/StrategyPanel.tsx:627`、
  `apps/web/components/Strategy/StrategyPanel.tsx:630`。
- 实时模拟启动和对账恢复都会把第一条 paper session 的 `runId` 写入全局 store，
  右侧 `api.trades(runId)` 使用的就是这个句柄：
  `apps/web/components/Strategy/StrategyPanel.tsx:174`、
  `apps/web/components/Strategy/StrategyPanel.tsx:178`、
  `apps/web/components/Strategy/StrategyPanel.tsx:313`、
  `apps/web/components/Strategy/StrategyPanel.tsx:317`。
- 中间下方的实时成交不是右侧 query 的同一份 React state；它由 paper WebSocket 的
  `snapshot` 和 `trade` 事件写入 `paperTradesByStrategyId`，再汇总成
  `bottomTrades` 传给 `ChartWorkspace`：
  `apps/web/app/page.tsx:570`、
  `apps/web/app/page.tsx:572`、
  `apps/web/app/page.tsx:598`、
  `apps/web/app/page.tsx:600`、
  `apps/web/app/page.tsx:404`、
  `apps/web/app/page.tsx:505`。
- 多策略实时模拟时，右侧 `runId` 只取 `Object.values(sessions)[0]`，而中间下方按
  `paperTradesByStrategyId` 支持按策略聚合/切换。右侧成交明细不仅与中间入口重复，
  还可能只展示第一条 session 的成交，使多选实时模拟语义更模糊。
- 回测模式也存在同类重复入口：普通回测成功时 `StrategyPanel` 设置 `runId`，并在显式运行时
  设置 `chartView="run-range"`；`page.tsx` 随后用该 `runId` 的 `api.trades` 作为
  `overlayTrades/bottomTrades`，右侧 `StrategyPanel` 仍有另一条 `api.trades(runId)`。
  相关证据：
  `apps/web/components/Strategy/StrategyPanel.tsx:249`、
  `apps/web/components/Strategy/StrategyPanel.tsx:253`、
  `apps/web/components/Strategy/StrategyPanel.tsx:254`、
  `apps/web/app/page.tsx:242`、
  `apps/web/app/page.tsx:245`、
  `apps/web/app/page.tsx:398`、
  `apps/web/app/page.tsx:404`。

上轮提交检查：

```text
git show --stat --oneline --name-only aea0862d6560859b0ba4e16fd7dbbda3d7cc48d7
aea0862 feat: add realtime combo paper strategy tabs
```

该提交主要让实时模式支持常规/组合策略分类、多选和批量启动；右侧底部已有的
“成交明细”结果区没有按新交互目标删除或隐藏。

测试备注：

```text
pnpm --dir apps/web test StrategyPanel.test.tsx
```

该命令当前失败于测试环境问题：`act(...) is not supported in production builds of React`，
失败入口在 `apps/web/components/Strategy/StrategyPanel.test.tsx:256`。因此本报告未把
自动化测试结果作为业务回归证据，只记录为现有测试环境阻塞。

## Impact surface

- 受影响用户：使用“实时”模拟并查看交易策略面板的用户。
- 受影响功能：右侧交易策略面板底部结果区、K 线面板下方成交/持仓区。
- 主要影响：
  - 同一批成交数据有两个入口，增加界面噪音。
  - 多策略实时模拟时，用户需要同时理解中间策略标签和右侧结果页签，认知负担增加。
  - 右侧保留 `api.trades` 轮询，实时模式下存在不必要请求。
- 不直接影响：
  - 后端撮合、下单、WebSocket 事件和成交持久化。
  - 中间 K 线面板下方“成交记录”的实际数据展示。

## Candidate fixes

| 方案 | 改动 | 侵入性 | 风险 | 工作量 |
| --- | --- | --- | --- | --- |
| A | 仅在 `mode === "paper"` 时隐藏右侧“成交明细”页签和对应 `api.trades` 轮询 | 低 | 回测模式仍保留重复入口；若只改 tabs 列表但不重置 `tab` 或删除渲染分支，旧的 `tab="trades"` 状态仍可能渲染表格 | 小 |
| B | 从 `StrategyPanel` 底部结果区彻底移除“成交明细”页签、对应 `TradesTable` 分支和右侧 `api.trades` query，成交统一在中间下方查看 | 中 | 会移除右侧现有“导出 CSV”入口；若产品确认导出仍必需，应另行迁移 | 小 |
| C | 移除右侧“成交明细”，并把 CSV 导出能力迁移到中间下方“成交记录”区域 | 中 | 能保留导出能力，但需要给 `ChartWorkspace`/底部表格补导出 props，触及面更大，不是解决重复入口的最小改动 | 中 |

## Recommended fix

推荐修正为方案 B 的严格版本：把成交查看的单一入口统一到中间 K 线面板下方
“成交记录”，删除右侧交易策略面板底部的“成交明细”页签、右侧 `TradesTable`
渲染分支、右侧成交 CSV 按钮以及该分支专用的 `api.trades(runId)` query。

理由：

- 完整满足用户“右边交易策略面板下方的成交明细不需要了”的要求。
- 这是解决重复入口和不必要轮询的最小改动；迁移 CSV 导出会触及 `ChartWorkspace`/
  底部表格 props，属于保留附加能力的产品决策，不应默认绑定到本 bug 修复。
- 数据职责更清晰：右侧交易策略面板负责策略选择、参数、启动/停止、绩效和历史；
  中间工作区负责图表、成交、持仓这些运行态明细。
- 修复时不能只从 tabs 数组删掉 `trades` 标签；还应删除或收敛 `tab === "trades"`
  分支，并移除右侧 `tradesToCsv`/`api.trades` 依赖，避免隐藏标签后由旧 `tab`
  状态继续渲染右侧表格。
- 修复后应补一个组件测试或集成测试，覆盖 `mode="paper"` 和普通回测 `run-range`
  下右侧不出现“成交明细”，中间下方仍出现“成交记录”。

## 评审结论

根因证据成立：失败路径中确实会同时出现两个成交入口。中间工作区通过
`paperTradesByStrategyId`/`bottomTrades` 展示实时成交；右侧 `StrategyPanel`
在 paper 模式仍进入通用结果 tabs，并用同一个运行句柄 `runId` 轮询 `api.trades`。
多策略实时模拟下右侧还只绑定第一条 session 的 `runId`，进一步说明它不适合作为成交明细入口。

最终推荐修复：执行方案 B 的严格版本，彻底移除右侧交易策略面板底部的“成交明细”
页签、右侧 `TradesTable` 渲染分支、右侧成交 CSV 按钮和该分支专用的
`api.trades(runId)` query；保留“绩效指标”和“历史”。中间 K 线面板下方的
“成交记录”继续作为唯一成交查看入口。CSV 导出迁移到中间区域可以作为后续增强或在产品明确要求时再做，
不应作为本次去重 bug 的必需改动。
