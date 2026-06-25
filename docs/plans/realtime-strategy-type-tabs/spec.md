# Realtime Strategy Type Tabs Spec

Version: v2

## 评审记录

| ID | 维度 | 严重级别 | 发现 | v2 修复 |
|---|---|---|---|---|
| R1 | 完整性 / 可行性 | P1 | 组合策略 `lookbackBars` 后端和前端上限是 1000，但当前 paper 会话 `_WINDOW_MAX`、`replayBars`、`primingBars` 都固定为 600。若按 v1 只让 `LiveComboStrategy.warmup()` 返回 1000，deque 仍会截断到 600，实时组合会长期达不到 warmup，无法产生信号。 | 明确引入 `PAPER_WINDOW_MAX = 1200`，同步调整 schemas 的 `replayBars` / `primingBars` 上限、session 基础与 HTF deque 容量、预热取数夹取规则，并增加窗口容量测试。 |
| R2 | 完整性 / 数据一致性 | P1 | v1 复用 `_build_members(...)` 时允许“模板不可用则跳过并记 note”。这适合回测预览的 best-effort，但不适合 paper start：用户启动保存组合时若静默跳过成员，会模拟一个与保存配置不同的组合。 | `combo_specs.build_members(...)` 增加 strict 模式；paper factory 必须 strict，任一成员模板失效或嵌套组合直接 400 `UNAVAILABLE`，成员不足仍 400 `COMBO_MEMBERS`。回测 / compare 保持现有 best-effort 语义。 |
| R3 | 完整性 | P1 | v1 只在风险章节提到删除组合后 `paperSelectionIds` 可能残留，但没有落到前端数据流与测试；跨页签多选后删除隐藏页签策略会导致启动时发送 stale id，返回 404 或启动用户看不见的策略。 | 前端正文补充策略列表变化时清理 `paperSelectionIds`，启动前再次按最新 `allSelectableStrategies` 过滤；无有效选择时禁用启动。测试计划补 stale selection 用例。 |
| R4 | 一致性 / 规模适配 | P2 | `strategyListTab` 同时影响 backtest 和 paper，用户在一个模式切到组合页签后另一个模式也会停留组合页签。 | 保持 v1 决策：这是可接受的显示偏好复用，不为本次新增持久化迁移；若后续用户反馈，再拆 `paperStrategyListTab`。 |
| R5 | 规模适配 | P2 | v1 建议在组合再平衡点同步跑 throwaway `run_backtest`。live 低频可接受，但 replay 高倍速和多组合并发下可能阻塞事件循环。 | 风险章节升级为明确优化边界：v2 不把异步 worker 化列为 P1，但要求 scoring 函数纯计算、窗口硬上限、只在 rebalance boundary 执行，并预留后续缓存 / 线程池优化。 |

## Overview

本方案把“常规策略 / 组合策略”分类页签从回测模式扩展到实时模拟模式。当前项目已经在回测列表中实现了策略分类：`StrategyPanel` 读取 `strategyListTab`，通过 `visibleStrategiesForStrategyPanel(...)` 在回测模式下切换常规策略与保存后的组合策略；但该 helper 在非回测模式会强制返回常规策略，因此用户从 K 线图上方切到“实时”后，右侧交易策略面板仍只能选择常规策略。实时模式已经支持多策略批量启动、多个 paper WebSocket、图表多净值曲线、成交 marker 过滤和底部按策略切换，本次不重造这些展示能力，而是把实时模式的策略选择入口与后端 paper start 能力补齐。

目标体验是：用户切到实时模式后，右侧策略面板在策略列表上方同样显示“常规策略”和“组合策略”两个页签。用户可以在两个页签之间来回切换，勾选多个常规策略，也可以切到组合策略页签勾选多个保存后的组合策略；这些勾选会跨页签保留，点击“开始模拟”后一次性启动所有被勾选的策略。启动成功后，每个策略仍对应一个独立 paper session 和 run，左侧图表沿用现有 `PaperStrategySession`、overlay trades、overlay equity、策略过滤和底部策略标签。

后端当前不能只“放开组合 id”。`paper_start` 和 `paper_batch_start` 直接用 `get_strategy_class(row.template_key)(params)` 实例化策略，而组合模板 `CombinationStrategy.on_candle()` 明确抛错，因为组合回测走的是 `engine.combo.run_combo_backtest` 的净值轮动编排，不是逐根信号策略。本方案要求新增一个实时组合适配层：保存后的组合策略启动 paper session 时，会构造一个 `LiveComboStrategy`，它在每根已确认 K 线上按组合 policy 选择当前活跃成员，并只把活跃成员的信号提交给该组合 session 自己的 `SimBroker`。这样组合实时模拟保持“一个组合 = 一个独立实时模拟会话”的用户模型，同时兼容现有图表、成交、净值、停止与运行历史链路。

## Technical Design

### Current architecture context

前端是 Next.js 14 + React 18 + TypeScript + Tailwind + TanStack Query + Zustand。右侧策略工作流集中在 `apps/web/components/Strategy/StrategyPanel.tsx`，策略分类 helper 在 `apps/web/lib/strategyCatalog.ts`，REST client 在 `apps/web/lib/api.ts`，paper 多策略图表辅助在 `apps/web/lib/paperMulti.ts`。`apps/web/app/page.tsx` 已经把 `paperSessionsByStrategyId` 转为多个 `paperWS(sessionId)` 连接，并把事件合并到 `paperTradesByStrategyId`、`paperEquityByStrategyId` 和 `paperPositionsByStrategyId`。`ChartWorkspace` 和 `ChartHeaderBar` 已经支持多策略 marker / 净值过滤，不需要新增 WebSocket 或图表协议。

后端是 FastAPI + SQLite + 进程内 `PaperManager`。`services/quant/app/api/paper.py` 提供单条和批量 paper start/stop；`PaperManager.start()` 为每个策略创建一个 `strategy_runs.mode="paper"` 运行记录和一个 `PaperSession`；`PaperSession` 负责真实 OKX live/replay K 线、预热、逐根 `on_candle`、`SimBroker` 撮合、持久化 trades/equity 以及 `/ws/paper/{sid}` fan-out。组合回测相关 helper 目前在 `services/quant/app/agent/services.py` 中，包括 `_combo_spec_from_row(...)`、`_build_members(...)`、`_policy_from_spec(...)` 和 `_is_combo(...)`。

### Frontend realtime tabs and cross-tab selection

`strategyListTab` 继续作为“常规策略 / 组合策略”当前页签状态，不新增持久化字段，也不 bump Zustand version。修改 `visibleStrategiesForStrategyPanel(...)` 的规则：

1. `mode === "compare"` 不使用该 helper 渲染列表，保持 `CompareView` 现状。
2. `mode === "backtest"` 按 `strategyListTab` 返回常规策略或用户保存组合。
3. `mode === "paper"` 也按 `strategyListTab` 返回常规策略或用户保存组合。
4. `combo` 页签只返回 `isUserCombo(strategy)`，不显示 builtin `regime_rotation` 模板行。

`StrategyPanel` 在 `mode === "backtest" || mode === "paper"` 时都渲染 `STRATEGY_LIST_TABS`，位置仍在 `PaperSelectionBar` 下方、`StrategyList` 上方。实时模式下页签只控制当前列表可见项，不清空 `paperSelectionIds`。因此用户可以在常规策略页签勾选 `EMA Cross`，切到组合策略页签再勾选 `Combo One`，最终 `paperSelectionIds` 包含两个 id。`PaperSelectionBar` 的计数应表达全局选择，例如 `已选 3 / 12`；“全选”改为“全选当前页”，只添加当前页签可见且 enabled 的策略，遇到 `maxPaperSessions` 上限停止继续添加；“清空”清空全部选择，避免隐藏页签里残留的选择让用户无法理解为何会启动更多策略。

`enabledStrategies`、`disabledIds` 和 cap 判断不能再只基于 `catalog.regular`。实现时应派生：

```ts
const visibleSelectableStrategies = listStrategies.filter((s) => !s.unavailable && s.enabled);
const allSelectableStrategies = [...catalog.regular, ...catalog.combos].filter(...);
```

`disabledIds` 只需要传给当前列表；当已有 running session 时禁用当前列表所有 checkbox；当达到 cap 时禁用当前列表里尚未选中的 checkbox。`startDisabledReason` 基于全局 `paperSelectionIds.length`，不是当前页签数量。切换页签时，`strategyId` 仍通过 `resolveVisibleStrategyId(...)` 解析当前详情区的 active row；但不要因此丢弃隐藏页签已勾选的策略。

保存组合被删除、模板失效或策略列表刷新后，`paperSelectionIds` 也必须跟着最新 catalog 做一次收敛。`StrategyPanel` 增加一个 `useEffect`：当 `mode === "paper"` 且 `strategies` 变化时，把 `paperSelectionIds` 过滤为当前 `allSelectableStrategies` 中仍存在的 id，并保持原顺序；如果过滤后为空，不自动跨页签重新选择隐藏策略，而是让 `startDisabledReason` 显示“请选择至少一个策略”。启动前再在 `startPaperSelection(...)` 内重复同一过滤，作为防御式校验，避免用户刚删除隐藏页签组合后仍向后端发送 stale id。

### Combo detail in realtime mode

当前 `selectedCombo` 分支只渲染回测操作。需要让 `ComboStrategyDetail` 支持 `mode?: "backtest" | "paper"` 或新增一个小组件 `ComboPaperDetail`。推荐扩展现有组件，避免重复组合摘要、查看、编辑、删除 UI：

```ts
export default function ComboStrategyDetail(props: {
  combo: Strategy;
  mode?: "backtest" | "paper";
  backtestKind?: BacktestKind;
  busy: boolean;
  paperActive?: boolean;
  onRunBacktest: (combo: Strategy) => void;
  onRunWalkForward?: (combo: Strategy) => void;
  onStartPaper?: (combo: Strategy, opts?: PaperOpts) => void;
  onStopPaper?: () => void;
  onView: (combo: Strategy) => void;
  onEdit: (combo: Strategy) => void;
  onDelete: (combo: Strategy) => void;
})
```

`mode === "paper"` 时显示组合摘要、查看/编辑/删除，以及与 `StrategyDetail` paper 模式一致的“开始模拟 / 停止模拟”控制和 live/replay 选项。组合不显示参数表；保存组合的参数应通过编辑弹窗维护。启动组合时不调用 `api.updateStrategy(combo.id, { params })`，直接使用已保存的 `params_json`。因此 `paperStart` mutation 的变量需要从 `{ params }` 调整为 `{ params?: Params; skipUpdate?: boolean; opts?: PaperOpts }`，`startPaperSelection(...)` 仅在 `params` 存在且 active row 不是 combo 时更新策略参数。

### Backend paper strategy factory

新增一个 paper strategy factory，替代 `paper.py` 中重复的 row/class/params 解析：

```py
@dataclass(frozen=True)
class PaperStrategySpec:
    strategy_id: int
    strategy_name: str
    strategy_kind: str  # "atomic" | "combo"
    strategy: BaseStrategy
    config_extra: dict

def build_paper_strategy_spec(row: Strategy) -> PaperStrategySpec:
    ...
```

原子策略路径保持现有行为：合并 `cls.meta.default_params` 与 `row.params_json`，实例化 `cls(params)`，`config_extra={"strategyKind": "atomic"}`。

组合策略路径解析保存组合的 `members` 和 policy，构造 `LiveComboStrategy`，并返回 `config_extra`：

```py
{
  "strategyKind": "combo",
  "combo": True,
  "members": members_spec,
  "policy": policy_spec,
  "liveComboVersion": 1
}
```

为了避免 `agent/services.py` 与 paper factory 复制组合解析逻辑，推荐新增 `services/quant/app/strategies/combo_specs.py`，把 `_is_combo(...)`、`combo_spec_from_row(...)`、`policy_from_spec(...)`、`build_members(...)` 移到该模块，再由 `agent/services.py` 和 `paper/strategy_factory.py` 共同导入。`build_members(...)` 必须支持 `strict: bool`：

- `strict=False`：供现有回测预览 / compare / Walk-forward 复用，保持当前语义；成员模板失效或成员是组合模板时跳过并记录 note，可用成员少于 2 个时抛 `400 COMBO_MEMBERS`。
- `strict=True`：供 paper start / paper batch start 使用；任一成员模板失效、成员模板是组合、或成员规格缺 `templateKey` 时立即抛 `400 UNAVAILABLE`，不静默改写用户保存的组合；可用成员少于 2 个时仍抛 `400 COMBO_MEMBERS`。

`build_paper_strategy_spec(row)` 的组合路径必须使用 `strict=True`，并在任何 `PaperManager.start(...)` / `start_batch(...)` 调用前完成所有组合成员和 policy 构造。这样批量启动中只要有一个组合非法，就不会创建部分 run/session。

### LiveComboStrategy runtime semantics

新增 `services/quant/app/paper/live_combo.py`，定义 `LiveComboStrategy(BaseStrategy)`。它不是注册表策略，不写入 `STRATEGY_REGISTRY`，只作为 paper session 的运行时 adapter。

构造参数：

```py
LiveComboStrategy(
    members: list[ComboMember],
    policy: ComboPolicy,
    policy_spec: dict,
    members_spec: list[dict],
)
```

核心字段：

- `active_key: str | None`
- `last_rebalance_i: int`
- `bars_held: int`
- `_frames: dict[str, pd.DataFrame]`，由 `attach_timeframes(...)` 缓存最近高周期 frames。
- `_last_switch_i: int | None`，用于避免同一根重复切换。

hook 行为：

1. `timeframes()` 返回所有成员 `timeframes` 的并集。
2. `htf_warmup_bars()` 返回成员 `htf_warmup_bars()` 的最大值。
3. `warmup()` 返回 `max(max(member.strategy.warmup()), policy.lookback_bars, max(RegimeParams().ema_slow_p, RegimeParams().trend_win))`，与 `engine.combo._warmup_floor(...)` 的起算口径一致，而不是使用 `RegimeParams().min_bars`。
4. `prepare(df, bar)` 返回原始 df；成员自己的 `prepare` 在 `on_candle` 内按需执行。
5. `attach_timeframes(df, bar, frames)` 缓存 frames 后返回 df，不修改公共 df。
6. `risk_params()` 返回当前 active member 的 `risk_params()`；active 为空时返回空 `RiskParams()`。
7. `on_candle(df, i, ctx)` 在 `i < warmup_floor` 时返回 `[]`。到达再平衡边界时，用 `per_bar_primaries(df.iloc[:i], ...)` 得到 `i-1` 的 primary regime，并用 `REGIME_STRATEGY_HINTS` 按 policy 做候选过滤。候选打分只使用 `<= i-1` 的历史窗口，不能读当前根之后的数据。

打分推荐复用 `engine.backtester.run_backtest` 的同步纯计算，在最近 `policy.lookback_bars` 根窗口上为每个候选成员做一次 throwaway 回测，按 `selectionMetric` 计算 return / sharpe / calmar。该计算只在 active 为空或 `(i - last_rebalance_i) >= policy.rebalance_bars` 时发生；成员数上限已有 8，窗口上限建议 1200，实时 live 模式负载可控。replay 高倍速下可能有卡顿，见风险章节。

当 active member 变化时，如果 `ctx.position_side != "flat"`，先返回一个 `Signal(side="close", reason="combo_switch:<old>-><new>")`，然后允许新 active member 的当根信号追加在同一个 signals 列表后面。`SimBroker.submit(...)` 已支持先平再开，成交仍发生在下一根开盘，保持现有因果语义。成员信号的 reason 统一加前缀 `combo:<memberName> / ...`，这样图表 tooltip 和成交表能看出组合当时由哪个成员驱动。

### Paper window capacity for combo lookback

当前 `services/quant/app/paper/session.py` 的 `_WINDOW_MAX=600` 与 `PaperStartReq.replayBars` / `primingBars` 的 `le=600` 是实时组合的硬阻塞点：保存组合允许 `lookbackBars <= 1000`，部分成员策略自身 warmup 也可能超过 600。v2 必须把 paper 窗口上限集中为一个常量，例如 `PAPER_WINDOW_MAX = 1200`，并同步应用到以下位置：

1. `services/quant/app/paper/session.py`：基础 K 线 deque 和 HTF deque 的 `maxlen` 使用 `self._window_max`，其值为 `min(max(self.strategy.warmup() + 5, self.priming_bars, self.replay_bars, self._htf_warmup + 5), PAPER_WINDOW_MAX)`；非组合原子策略仍会落在较小窗口，不为所有 session 固定占用 1200。
2. `_prime_history()` / `_load_warmup_window()` / `_run_replay()` / `_prefill_htf()`：所有历史取数 limit 都夹取到 `PAPER_WINDOW_MAX`，且不得超过对应 deque `maxlen`，避免取 1000 根后仍被 600 根窗口截断。
3. `services/quant/app/schemas.py`：`PaperStartReq`、`PaperBatchStartReq` 的 `replayBars` 和 `primingBars` 上限从 600 调整为 1200；注释同步改为引用 `PAPER_WINDOW_MAX`，避免 schema 与 session 常量再次漂移。
4. `LiveComboStrategy.warmup()` 大于 `PAPER_WINDOW_MAX - 5` 时，paper factory 应返回 `400 COMBO_WINDOW`，message 写明“组合 warmup 超出实时模拟窗口上限”，不要启动一个永远不会产生信号的 session。

### PaperManager and API flow

`PaperManager.start(...)` 增加可选 `strategy_name`、`strategy_kind`、`config_extra` 参数，并把它们写入 `create_run(..., config={...})` 和 `_session_meta`。`PaperBatchStrategy` 增加 `strategy_kind` 与 `config_extra`。`group_session_out(...)` 返回可选 `strategyKind`，`GET /paper/sessions` 也透出 `strategyName`、`strategyKind` 和 `groupId`，方便刷新后对账。

`paper_start` 和 `paper_batch_start` 都先加载所有 row，再调用 `build_paper_strategy_spec(row)`。批量 start 的校验顺序保持不变：dedupe ids、检查 max sessions、加载全部 row、构造全部 specs；只有全部 specs 构建成功后才调用 `PaperManager.start_batch(...)`。如果组合成员非法或模板失效，必须在任何 session / run 创建前失败。

## File / Module Change Plan

| File | Create / Modify | Intent |
|---|---|---|
| `docs/plans/realtime-strategy-type-tabs/spec.md` | Create | 本设计文档。 |
| `apps/web/lib/strategyCatalog.ts` | Modify | 让 `paper` 模式也按 `strategyListTab` 返回常规/用户组合策略；保留 compare 不使用列表。 |
| `apps/web/lib/strategyCatalog.test.ts` | Modify | 更新 paper 模式用例：combo tab 应返回用户组合而不是强制常规策略；补跨 tab selection fallback 用例。 |
| `apps/web/components/Strategy/StrategyPanel.tsx` | Modify | 在 paper 模式渲染策略分类页签；让 `paperSelectionIds` 支持常规与组合混选；修正全选/清空/上限禁用；组合详情在 paper 模式可启动/停止模拟。 |
| `apps/web/components/Strategy/ComboStrategyDetail.tsx` | Modify | 增加 paper 模式 props 和开始/停止模拟 UI；组合启动不更新 params。 |
| `apps/web/components/Strategy/ComboStrategyDetail.test.tsx` | Modify | 覆盖 paper 模式按钮、回测按钮隐藏/显示规则、`onStartPaper(combo)` 调用。 |
| `apps/web/components/Strategy/StrategyPanel.test.tsx` | Modify | 覆盖实时模式页签、跨页签多选、批量启动常规+组合 ids、选择上限、已有会话禁用。 |
| `apps/web/lib/types.ts` | Modify | 为 `PaperBatchSessionResult`、`PaperStrategySession`、`PaperSessionRow` 增加可选 `strategyKind?: "atomic" \| "combo"`。 |
| `apps/web/lib/paperMulti.ts` | Modify | `sessionsFromBatch` / `sessionsFromReconcile` 保留 `strategyKind`，不影响现有 overlay 逻辑。 |
| `services/quant/app/strategies/combo_specs.py` | Create | 共享组合 row 解析、policy 构造、成员构造、combo kind 判断；`build_members(strict=False/True)` 区分回测 best-effort 与 paper strict。 |
| `services/quant/app/agent/services.py` | Modify | 改为导入 `combo_specs.py` 中的共享 helper，保持现有 backtest / compare / Walk-forward 行为不变。 |
| `services/quant/app/paper/live_combo.py` | Create | 实时组合策略 adapter，实现 `BaseStrategy` hook、轮动决策、成员信号转发和 reason 前缀。 |
| `services/quant/app/paper/strategy_factory.py` | Create | 从 DB strategy row 构造原子或组合 paper strategy spec，并返回 run config metadata。 |
| `services/quant/app/paper/manager.py` | Modify | `PaperBatchStrategy`、`start()`、`start_batch()`、`_session_meta` 支持 strategy kind/name/config_extra。 |
| `services/quant/app/paper/session.py` | Modify | 引入 `PAPER_WINDOW_MAX=1200`，根据 `strategy.warmup()` / replay / priming / HTF warmup 动态设置 candle/HTF deque 容量，避免组合 lookback 被 600 根硬截断。 |
| `services/quant/app/api/paper.py` | Modify | 单条和批量 start 使用 paper strategy factory；错误在创建 session 前返回；response 透出 `strategyKind`。 |
| `services/quant/app/schemas.py` | Modify | Pydantic 输出模型增加可选 `strategyKind`；`replayBars` / `primingBars` 上限调整为 `PAPER_WINDOW_MAX=1200` 对齐 session。 |
| `services/quant/tests/test_paper_combo.py` | Create | 覆盖组合单条 start、常规+组合批量 start、非法组合不创建 session、组合 session feed 不触发 `CombinationStrategy.on_candle`。 |
| `services/quant/tests/test_paper_batch.py` | Modify | 保持现有批量用例通过，并补 response/session row 的 `strategyKind` 断言。 |

## Interface Design

### REST

不新增 REST path。现有接口扩展可接受的 strategy id 类型：

```http
POST /api/paper/start
Content-Type: application/json

{
  "strategyId": 20,
  "instId": "BTC-USDT-SWAP",
  "bar": "1H",
  "initialCapital": 10000,
  "source": "live",
  "speed": 1,
  "replayBars": 300,
  "priming": true,
  "primingBars": 300
}
```

`strategyId` 可以是原子策略，也可以是保存后的组合策略。单条响应保持兼容：

```json
{ "sessionId": "abc", "runId": 123 }
```

批量接口保持 request shape：

```http
POST /api/paper/batch/start
```

```json
{
  "strategyIds": [1, 20, 21],
  "instId": "BTC-USDT-SWAP",
  "bar": "1H",
  "initialCapital": 10000,
  "source": "live",
  "priming": true
}
```

响应向后兼容地增加可选字段：

```json
{
  "groupId": "paper_x",
  "instId": "BTC-USDT-SWAP",
  "bar": "1H",
  "sessions": [
    {
      "strategyId": 1,
      "name": "EMA Cross Scalper",
      "strategyKind": "atomic",
      "sessionId": "s1",
      "runId": 101,
      "state": "running"
    },
    {
      "strategyId": 20,
      "name": "Combo One",
      "strategyKind": "combo",
      "sessionId": "s2",
      "runId": 102,
      "state": "running"
    }
  ]
}
```

错误语义：

- `404 NOT_FOUND`: strategy id 不存在。
- `400 UNAVAILABLE`: strategy template 不存在或组合成员模板不可用。
- `400 COMBO_MEMBERS`: 组合可用成员少于 2 个。
- `400 COMBO_WINDOW`: 组合 `warmup()` 超出 `PAPER_WINDOW_MAX - 5`，实时模拟窗口无法满足该组合 lookback / 成员 warmup。
- `429 SESSION_LIMIT`: 请求会话数超过 `settings.max_paper_sessions`。
- `500 PAPER_BATCH_START_FAILED`: 预校验后启动过程中出现运行时失败，已回滚已启动 session。

### WebSocket

不新增 WebSocket。组合实时模拟仍使用：

```http
WS /api/ws/paper/{session_id}
```

事件 shape 不变：`status`、`snapshot`、`trade`、`equity`、`position`、`candle`、`signal`。组合 session 的 trade/signal reason 会带 `combo:<memberName>` 前缀，前端无需协议改造即可在 tooltip / table 中显示。

### Frontend component interfaces

`visibleStrategiesForStrategyPanel(...)` 语义变更但签名不变。`ComboStrategyDetail` 增加 paper props，`StrategyPanel` 的 `paperStart` mutation 变量建议调整为：

```ts
type PaperStartVars = {
  params?: Record<string, unknown>;
  skipUpdate?: boolean;
  opts?: PaperOpts;
};
```

## Data Model

无需数据库迁移。每个被启动的常规策略或组合策略仍创建一条 `strategy_runs`：

```py
strategy_runs.strategy_id = selected_strategy_id
strategy_runs.mode = "paper"
strategy_runs.status = "running" | "stopped"
```

组合 paper run 的 `config_json` 增加 metadata：

```json
{
  "initialCapital": 10000,
  "source": "live",
  "speed": 1,
  "replayBars": 300,
  "priming": true,
  "primingBars": 300,
  "htfBars": ["4H"],
  "strategyKind": "combo",
  "combo": true,
  "members": [
    { "templateKey": "trend_pullback_long", "params": {} },
    { "templateKey": "range_scalping_strategy", "params": {} }
  ],
  "policy": {
    "rebalanceBars": 24,
    "lookbackBars": 168,
    "selectionMetric": "sharpe",
    "minDwellBars": 24,
    "switchMarginPct": 0.1,
    "regimeGating": true,
    "turnoverBps": 5
  },
  "liveComboVersion": 1
}
```

`PaperManager` 进程内 metadata 增加：

```py
{
  "strategyId": 20,
  "strategyName": "Combo One",
  "strategyKind": "combo",
  "runId": 102,
  "groupId": "paper_x"
}
```

前端 `paperSelectionIds` 继续是 runtime-only，不进入 Zustand `partialize`。它现在可以同时包含 atomic 和 combo ids。`strategyListTab` 已是持久化显示偏好，复用即可。

## Testing & Acceptance Criteria

### Backend automated tests

新增 `services/quant/tests/test_paper_combo.py`：

1. `test_paper_start_accepts_saved_combo`: 创建保存组合，调用 `/api/paper/start`，断言 200、run mode 为 `paper`、`config_json.combo is True`、manager session 存在且 `strategyKind == "combo"`。
2. `test_paper_batch_start_accepts_atomic_and_combo`: 批量传入一个原子策略 id 和一个保存组合 id，断言返回两个 sessions，顺序与去重后的请求一致，`strategyKind` 分别为 `atomic` 和 `combo`。
3. `test_paper_start_rejects_invalid_combo_before_creating_session`: 创建成员不足或成员模板失效的组合，调用 start，断言 400，`get_paper_manager().list_sessions()` 为空。
4. `test_live_combo_strategy_feed_candle_does_not_call_combination_on_candle`: 直接构造 combo paper session 或 `LiveComboStrategy`，喂入足够 K 线，断言没有抛出 `CombinationStrategy 必须经 ...`，且 signals/trades reason 可带 combo 前缀。
5. `test_combo_paper_stop_finalizes_run`: 组合 session stop 后 run status 为 `stopped`，metrics shape 与常规 paper run 一致。
6. `test_paper_combo_member_unavailable_is_strict`: 保存组合包含一个不可用成员模板，即使剩余成员数仍 >= 2，paper start 也返回 `400 UNAVAILABLE`，且不创建 session/run；回测预览 best-effort 行为另由既有 combo tests 保持。
7. `test_paper_combo_window_allows_lookback_1000`: 保存 `lookbackBars=1000` 的组合，断言 `LiveComboStrategy.warmup() <= PAPER_WINDOW_MAX - 5` 时可启动，session 基础 deque `maxlen >= 1005`，`replayBars/primingBars=1000` 通过 schema 校验。
8. `test_paper_combo_window_rejects_oversized_warmup`: 构造成员 warmup + lookback 超过 `PAPER_WINDOW_MAX - 5` 的组合，断言 `400 COMBO_WINDOW` 且没有创建 session。

更新 `test_paper_batch.py`，确保现有 session limit、rollback、stop-all 用例仍通过，并在 batch response 中断言可选 `strategyKind` 不破坏旧字段。

### Frontend automated tests

更新 `apps/web/lib/strategyCatalog.test.ts`：

1. `paper` + `regular` 返回常规策略。
2. `paper` + `combo` 返回用户保存组合。
3. builtin `regime_rotation` 仍不出现在 combo tab。
4. `resolveVisibleStrategyId` 在 paper 模式切页签时落到当前页第一项或 `null`。

更新 `StrategyPanel.test.tsx`：

1. 设置 store `mode="paper"`，渲染后能看到“常规策略 / 组合策略”页签。
2. 常规页签只显示常规策略，组合页签只显示保存组合。
3. 勾选一个常规策略，切到组合页签再勾选一个组合，`paperSelectionIds` 同时包含两个 id。
4. 点击“开始模拟”后 `api.paperBatchStart` 收到 `[atomicId, comboId]`，且未对 combo 调用 `api.updateStrategy`。
5. 当前页签全选只添加当前页策略，并尊重 `maxPaperSessions`。
6. 已有 running session 时，两个页签下的 checkbox 都禁用。
7. 删除或刷新掉隐藏页签中已勾选的组合后，`paperSelectionIds` 被过滤，启动请求不包含 stale id；若过滤后为空，开始按钮显示“请选择至少一个策略”。

更新 `ComboStrategyDetail.test.tsx`，覆盖 paper mode 的开始/停止按钮、查看/编辑/删除按钮存在，以及 simple/walkforward 回测按钮不在 paper mode 混入。

### Manual acceptance

1. 启动 `pnpm dev`，打开 `http://localhost:18761`。
2. 从 K 线图上方切到“实时”模式，右侧“交易策略”面板显示“常规策略 / 组合策略”页签。
3. 常规页签只列出原子策略；组合页签只列出用户保存组合，builtin `regime_rotation` 模板不显示为可选项。
4. 在常规页签勾选两个策略，切到组合页签勾选一个组合，已选数量保持累计。
5. 点击开始实时模拟，后端启动三个 paper sessions；左图 marker、净值 overlay、顶部策略过滤、底部策略标签均出现三条策略记录。
6. 组合策略产生的成交 reason 包含当前成员信息，例如 `combo:区间剥头皮 / ...`。
7. 停止模拟后所有 sessions 停止，runs 最终状态为 `stopped`，刷新页面不会恢复 stale session id。
8. 运行 `pnpm test`，后端非 network pytest 和前端 Vitest 全部通过。

## Risks & Mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| 组合实时语义与组合回测净值轮动不完全相同。 | 用户可能把实时组合结果与回测 combo timeline 做逐点对比。 | 文档和代码注释明确：实时组合是 active-member signal adapter，一个组合 session 只有一个真实 `SimBroker`；回测仍是成员净值轮动。两者共享 policy、成员和因果 selection，但不承诺逐点等价。 |
| 直接把 `CombinationStrategy` 传入 `PaperSession` 会在 `on_candle` 抛错。 | 组合实时模拟启动后立即失败。 | paper API 必须通过 `build_paper_strategy_spec(...)` 构造 `LiveComboStrategy`；新增测试断言不会触发 `CombinationStrategy.on_candle`。 |
| replay 高倍速下组合 rebalance 打分阻塞事件循环。 | UI 更新卡顿，尤其 1m/64x/多组合并发。 | 只在 rebalance boundary 打分；成员数限制 2..8；rolling window 上限 1200；后续如仍慢，再把 scoring cache 或 `on_candle` 外包到线程池作为优化。 |
| 跨页签选择不可见，用户忘记隐藏页签里还勾了策略。 | 启动数量超预期。 | `PaperSelectionBar` 使用全局 `已选 X / max`，按钮文案改为“全选当前页”和“清空全部”；启动前 running list 显示所有 sessions。 |
| `strategyListTab` 同时影响 backtest 和 paper。 | 用户在实时切到组合后回测也停留组合页。 | 这是可接受的显示偏好复用；避免新增迁移。若用户反馈需要独立偏好，再新增 `paperStrategyListTab`。 |
| 组合 policy lookback 大于 paper session 默认窗口。 | 轮动选择长期只在不足窗口上打分，结果不稳定。 | `PaperSession` 根据 `strategy.warmup()` 动态提升 deque 容量到上限 1200；不足窗口时 reason/note 可标记 warmup，不伪造历史。 |
| 批量启动中某个组合非法但前面策略已启动。 | 产生部分 running sessions 和 UI/backend 漂移。 | API 在调用 `PaperManager.start_batch` 前构造全部 specs；任何组合校验失败都在创建 run/session 前返回；保留现有 rollback 防线。 |
| 保存组合编辑后旧 `paperSelectionIds` 指向已删除 id。 | 点击启动时 404 或启动隐藏策略。 | start 前根据最新 `strategies` 过滤 selection；`resolveVisibleStrategyId` 已处理详情 active id，`paperSelectionIds` 也应在策略列表变化时剔除不存在 id。 |
| paper 路径复用回测组合成员构造时继续 best-effort 跳过不可用成员。 | 启动的实时组合与用户保存的组合不一致，排查困难。 | `build_members(strict=True)` 供 paper factory 专用；任一成员不可用直接 `400 UNAVAILABLE`，不创建 run/session。 |

## 评审结论

**有条件通过**。

v2 已修复全部 P1：paper 窗口容量与 schema 上限对齐、paper 组合成员构造改为 strict、跨页签选择增加 stale id 清理与启动前过滤。没有 P0 遗留。

条件：代码开发节点必须按本 spec 的 `PAPER_WINDOW_MAX=1200`、`build_members(strict=True)`、`COMBO_WINDOW` 错误码和 stale selection 测试逐项落地；P2 的 replay 高倍速 scoring 性能可作为后续优化，但本轮不得引入未来函数或静默跳过组合成员。
