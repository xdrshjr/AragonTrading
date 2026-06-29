# 自动交易总预算上限设置 + 分配模式（最大比例）设计文档

> Feature slug: `auto-trade-budget-and-allocation-settings`
> 关联既有特性：`watchlist-auto-trade-group`、`live-auto-trading`、`live-auto-trading-hardening`、`agent-model-provider-settings`
> 状态：设计稿（design-only，本节点只产出本文档，不写实现代码）
> 文档版本：**v2**（经方案评审修复，见下「评审记录」；P0 / P1 已就地修复，版本由 v1 升 v2）

---

## 评审记录（Review Notes · v2）

> 评审人：Anthropic 工程团队资深评审。维度：可行性（Feasibility）/ 完整性（Completeness）/
> 一致性（Consistency）/ 合理尺寸（Right-sizing）。已逐节对照真实代码核验：`apps/web/lib/autoTrade.ts`、
> `apps/web/lib/liveAuto.ts`、`apps/web/components/Strategy/AutoTradeConfirmModal.tsx`、
> `services/quant/app/live_auto/manager.py`、`services/quant/app/api/live_auto.py`、`config.py`、
> `schemas.py`、`apps/web/lib/store.ts`、`SettingsNav.tsx` / `SettingsPanel.tsx`、`main.py`。

| 编号 | 严重度 | 维度 | 问题 | 处置 |
|---|---|---|---|---|
| RN-1 | **P0** | 完整性 / 一致性 / 安全 | v1 把**全局** env `live_auto_max_capital_frac` 默认 `0.05→1.0`。该字段经**共享**的 `_resolve_budget_group`（`manager.py:435`）同时作用于**单标的旧路径**（`/live-auto/start` → `manager.start()` 兼容 wrapper，`manager.py:367`）。单标的确认弹窗 `AutoTradeConfirmModal` 在用户**留空预算**时发送 `capitalUsd: null`（UI 明示「留空＝默认 1%」，`AutoTradeConfirmModal.tsx:83/138`），后端据此把留空默认从 `avail×1%` 变为 `avail×100%`、硬上限从 `5%` 变为 `100%`——真实资金敞口**静默放大约 100×**；同时前端 `liveAuto.ts` 仍写死「1% / 5%」，前后端口径漂移。直接证伪 DoD#5「单标的旧路径行为不变」与 §八。 | **已修复**（§2.2 / §三 / §六 / §八）。引入**分组专用运营天花板** `live_auto_group_max_capital_frac`（默认 `1.0`），`live_auto_max_capital_frac` **保持 `0.05` 不变**专作单标的天花板；`_resolve_budget_group` 改参数化 `(capital_usd, avail, cap_frac, default_frac)`，由各入口分别解析：分组路径 `default_frac=cap_frac`（留空→上限），单标的 wrapper 仍传 `live_auto_default_capital_frac`(1%)/`live_auto_max_capital_frac`(5%)——单标的行为**真正不变**、零前端改动。 |
| RN-2 | **P1** | 完整性 / 一致性 | 分组前端预览只按 `maxBudgetPct` 算上限（`avail×pct%`），不知后端运营天花板 `live_auto_group_max_capital_frac`。若运营把天花板设到低于用户 `pct`，确认弹窗（**真实资金知情同意闸**）会**高估**默认 / 生效总预算，预览 ≠ 实际。 | **已修复**（§4.1 / §2.2）。`GET /api/settings/auto-trade` 增返 `effectiveMaxBudgetPct = min(maxBudgetPct, 天花板×100)`；分组弹窗预览改用 `effectiveMaxBudgetPct`，使「预览 ≈ 实际」（最终仍以服务端回执 `totalBudgetUsd` 为准）。 |
| RN-3 | P2 | 一致性 | §三 称 app_settings 为「第 10 个 router」；实际 `live_auto` 已是第 10 个（`main.py:117`），新 router 应为**第 11 个**。 | **已修复**：§三改为第 11 个。 |
| RN-4 | P2 | 合理尺寸 / 一致性 | 现有「设置类」持久化均为**专表**（`AgentProvider` / `AgentToolState` / `OkxAccount`）；v1 改用通用 KV `app_settings`。对单一标量 `maxBudgetPct` 而言通用 KV 略偏前瞻，与既有「一域一专表」惯例略有出入。 | **保留并记录决策**：通用 KV 为设置中心后续扩展（`allocationMode` / `maxRatioDefault` 默认值落库）留口，`create_all` 幂等无迁移成本；若评审更倾向专表，改单行 `auto_trade_settings` 工作量相当（见 §评审结论条件②）。 |
| RN-5 | P2 | 完整性 | `test_live_auto_group.py::test_resolve_budget_group_scales_with_member_count`（`:130`，直接断言成员数缩放 + `_resolve_budget_group` 旧签名）与 `autoTrade.test.ts:58-82`（断言被移除的 5% / 缩放语义）必随实现重写，否则编译 / 断言失败。 | **已点名**：§6.1 显式列出须重写 / 删除的用例，杜绝实现节点遗漏。 |
| RN-6 | P2 | 合理尺寸 / 一致性 | max_ratio 的「最大限制比例」基准取**总预算**（`budget_i = total×pct/100`，故单标的实际敞口 = `avail×maxBudgetPct%×pct%`，两层百分比相乘）。属合理解读且 §八已把「按可用余额为基准」列为 out-of-scope，但与产品口径建议二次确认。 | **保留为待确认假设**：见 §七 R2 与 §评审结论条件①。 |

**结论速览**：P0 / P1 共 2 项已就地修复并下沉到各章节；P2 共 4 项均为可接受的设计取舍或已点名的实现注意项，列入「评审结论」条件，不阻塞进入开发节点。

---

## 一、Overview（概述）

当前「启动多标的自动交易」弹窗（`AutoTradeGroupConfirmModal`）里的「生效总预算」被一条
**硬编码的 5% 上限**夹紧：前端 `apps/web/lib/autoTrade.ts` 的 `LIVE_AUTO_MAX_CAPITAL_FRAC = 0.05`
与后端 `services/quant/app/config.py` 的 `live_auto_max_capital_frac = 0.05` 双侧强制，且留空默认值
还会按成员数线性缩放（`min(avail×1%×N, avail×5%)`）。这套保守默认对真实资金安全友好，但对希望
用更大仓位跑多标的自动交易的用户而言不可配置、不可解释，体验割裂。本需求要求：把这条上限从「写死
5%」改为**用户可在设置中心配置**，为此在设置中心**新增一个独立类别「自动交易设置」**，用户在此设置
总预算上限百分比，**默认 100%**（即默认最大 / 默认总预算 = 全部可用余额），且**不再按成员数缩放**。

第二项需求是给多标的自动交易增加一种**仓位分配模式**。现状只有「平均分配」一种语义：各成员百分比是
对总预算的**切分**，整组 Σ ≤ 100%（`evenSplit` 把 100 均分给成员，`clampAllocationOnEdit` 把单成员
夹到 `100 − Σ其余`）。本需求要求新增「**最大比例模式**」：当用户同时跑多只自动交易标的时，为每只
标的各自设置一个**最大限制比例**（互相独立、不再要求合计 ≤ 100%）；之后无论哪只标的触发买/卖，都
**按它各自的最大限制比例**去开仓 / 操作。换言之，比例从「瓜分总预算的份额」语义改为「每只标的对
总预算的独立上限」语义。

这两项改动落在同一条数据链路上（设置 → 分组启动请求 → `LiveAutoManager` 预算解析与成员校验 →
逐成员 `LiveExecutor`），因此合并为一个特性统一设计，确保上限配置、默认值、分配模式三者在前后端、
预览与真实下单口径上完全一致。安全护栏（双闸 `LIVE_AUTO_TRADING_ENABLED`/`LIVE_TRADING_ENABLED`、
单笔名义上限、逐组前置门禁、确认勾选、平仓兜底）一律保留；新增的「运营方绝对上限」env 天花板进一步
保证「前端可配上限」不会越过运营策略。

---

## 二、Technical design（技术设计）

### 2.1 架构与分层

改动遵循既有分层：**纯件**（`apps/web/lib`、可单测无副作用）承载百分比/预算口径；**store**
承载持久化偏好；**组件**承载 UI；**API 路由**承载边界校验与持久化读写；**`LiveAutoManager`** 承载
编排与安全门禁；**`LiveExecutor` / `LiveAutoSession` 零改动**（每会话仍只管一个标的、按 `budget_usd`
下单）。预算上限作为**安全关键值**必须服务端权威：后端永不信任前端送来的「上限」，而是从 DB 设置 +
env 天花板自行解析。

```text
设置中心「自动交易设置」(AutoTradeSettingsSection)
  └─PUT /api/settings/auto-trade {maxBudgetPct}──► app_settings KV 表（DB 持久化，服务端权威）
启动多标的自动交易弹窗 (AutoTradeGroupConfirmModal)
  ├─GET /api/settings/auto-trade ──► 读 effectiveMaxBudgetPct 驱动「默认/夹紧」预览（RN-2）
  └─POST /api/live-auto/group/start {members, allocationMode, totalCapitalUsd}
        └─ live_auto.py 路由：读 DB maxBudgetPct → 算 cap_frac=min(pct/100, 分组天花板)、default_frac=cap_frac → 填入 GroupStartRequest
             └─ LiveAutoManager.start_group
                  ├─ _validate_members(members, settings, mode)  # 按 mode 决定是否强制 Σ≤100
                  ├─ _resolve_budget_group(capital, avail, cap_frac, default_frac)  # 不再按 N 缩放（RN-1：单标的路径传旧 0.05/0.01）
                  └─ _build_member: budget_i = total × pct/100（两模式同式，仅校验语义不同）
```

### 2.2 预算上限：从「写死 5%」到「可配 + 分组运营天花板」（**RN-1 / RN-2 修复后**）

> ⚠️ **RN-1（P0）核心修复**：`_resolve_budget_group` 被**单标的旧路径**与**分组路径**共享。绝不可
> 直接上调全局 `live_auto_max_capital_frac` 默认（会把单标的留空默认从 1% 抬到 100%、上限 5% 抬到
> 100%，真实资金敞口静默放大约 100×）。故本版**新增分组专用天花板**、`live_auto_max_capital_frac`
> 保持 `0.05` 不变，并把预算解析器**参数化**，让两条路径各自携带 cap / default 分数、互不影响。

**两个独立天花板（env，运营方权威）**：

- `live_auto_max_capital_frac = 0.05`（**保持不变**）：**单标的旧路径**绝对天花板。
- `live_auto_group_max_capital_frac = 1.0`（**新增**，默认 100% = 不额外限制）：**分组路径**运营天花板；
  运营可经环境变量收紧（如生产设 `LIVE_AUTO_GROUP_MAX_CAPITAL_FRAC=0.2` 强制分组 ≤ 20%，无视前端配置）。

**分组有效上限分数 = min(maxBudgetPct/100, live_auto_group_max_capital_frac)**，其中
`maxBudgetPct` 为用户在「自动交易设置」配置的总预算上限百分比（DB 持久化，默认 `100`）。两默认
（pct=100、天花板=1.0）→ 分组有效上限 = 全部可用余额，符合需求「默认 100%」。

**分组留空默认总预算（不再按成员数缩放）**：`default_total = avail × 分组有效上限分数`。即留空时
默认总预算 = 上限本身（默认 100% → 全部可用余额）。`live_auto_default_capital_frac`（1%）**不弃用**——它
仍是**单标的旧路径**的留空默认（仅分组路径不再引用它，因分组留空默认 = 上限本身）。

**分组非空夹紧**：用户填了总预算 → `min(capital, avail × 分组有效上限分数)`。

后端 `LiveAutoManager._resolve_budget_group` 改为**纯参数化**（不再读 `settings`、不再含 `member_count`，
≤ 4 形参，CLAUDE.md 二 / 六）：

```python
@staticmethod
def _resolve_budget_group(capital_usd, avail, cap_frac, default_frac) -> float:
    """留空→ avail×default_frac；非空→ 夹到 ≤ avail×cap_frac。两路径共用，分数由各入口解析。"""
    hard_cap = avail * cap_frac
    budget = capital_usd if (capital_usd and capital_usd > 0) else avail * default_frac
    return min(budget, hard_cap) if hard_cap > 0 else budget
```

- **分组入口**（`live_auto.py` 路由）：从 DB 读 `maxBudgetPct`，算
  `cap_frac = min(maxBudgetPct/100, settings.live_auto_group_max_capital_frac)`、`default_frac = cap_frac`
  （留空→上限），填入 `GroupStartRequest`。服务端权威，**不取前端任何「上限」入参**。
- **单标的入口**（`manager.start()` 兼容 wrapper）：传
  `cap_frac = settings.live_auto_max_capital_frac`(0.05)、`default_frac = settings.live_auto_default_capital_frac`(0.01)
  → 与现状**逐位一致**（留空 1%、非空夹到 5%），单标的行为**真正不变**，前端 `liveAuto.ts` /
  `AutoTradeConfirmModal` **零改动**。

`GroupStartRequest` 新增 `budget_cap_frac: float`、`default_budget_frac: float`（由入口解析后填入，
替代 v1 设想的 `max_budget_pct` 字段）；`start_group` 调用处改为
`self._resolve_budget_group(req.total_capital_usd, avail, req.budget_cap_frac, req.default_budget_frac)`。

**RN-2（P1）预览口径**：`GET /api/settings/auto-trade` 除回 `maxBudgetPct` 外，再回
`effectiveMaxBudgetPct = min(maxBudgetPct, live_auto_group_max_capital_frac × 100)`。前端
`AutoTradeGroupConfirmModal` 的「默认 / 夹紧」预览一律用 `effectiveMaxBudgetPct`（而非裸 `maxBudgetPct`），
确保运营天花板低于用户配置时预览不高估真实可用预算。

前端 `apps/web/lib/autoTrade.ts` 把上限从模块常量改为**函数入参**（统一用 `effectiveMaxBudgetPct` 传入）：

- `groupBudgetCap(availUsdt, maxBudgetPct)`：`avail × maxBudgetPct/100`（avail null/≤0 → 0）。
- `clampGroupBudgetToCap(budget, availUsdt, maxBudgetPct)`：`min(budget, cap)`（cap ≤ 0 原样返回）。
- `deriveGroupDefaultBudget(availUsdt, maxBudgetPct)`：`avail × maxBudgetPct/100`（**删除** `memberCount`
  形参与 `LIVE_AUTO_DEFAULT_CAPITAL_FRAC` 乘项）。
- `isGroupBudgetClamped(capitalUsd, availUsdt, maxBudgetPct)`：`cap>0 && capital>cap`。
- `LIVE_AUTO_MAX_CAPITAL_FRAC` / `LIVE_AUTO_DEFAULT_CAPITAL_FRAC` 在 `autoTrade.ts` 改为不再用于分组
  口径；推荐删除分组侧导出、改由查询数据兜底默认 `100`（注意 `liveAuto.ts` 中的同名常量**保留**供单标的路径）。

`AutoTradeGroupConfirmModal` 通过 `useQuery(["settings","auto-trade"])` 取 `effectiveMaxBudgetPct`，把硬编码
「5% / 随成员数缩放」文案改为基于该值的动态文案（如「总预算硬上限为可用余额的 {effectiveMaxBudgetPct}%」
「默认总预算 = 可用余额 × {effectiveMaxBudgetPct}%」）。

前端 `apps/web/lib/autoTrade.ts` 把上限从模块常量改为**函数入参**：

- `groupBudgetCap(availUsdt, maxBudgetPct)`：`avail × maxBudgetPct/100`（avail null/≤0 → 0）。
- `clampGroupBudgetToCap(budget, availUsdt, maxBudgetPct)`：`min(budget, cap)`（cap ≤ 0 原样返回）。
- `deriveGroupDefaultBudget(availUsdt, maxBudgetPct)`：`avail × maxBudgetPct/100`（**删除** `memberCount`
  形参与 `LIVE_AUTO_DEFAULT_CAPITAL_FRAC` 乘项）。
- `isGroupBudgetClamped(capitalUsd, availUsdt, maxBudgetPct)`：`cap>0 && capital>cap`。
- 保留 `LIVE_AUTO_MAX_CAPITAL_FRAC` 常量仅作「前端兜底默认上限分数」用途，或直接以 `maxBudgetPct=100`
  兜底；推荐删除该导出常量、改由查询数据兜底默认 `100`。

`AutoTradeGroupConfirmModal` 通过 `useQuery(["settings","auto-trade"])` 取 `maxBudgetPct`，把硬编码
「5% / 随成员数缩放」文案改为基于 `maxBudgetPct` 的动态文案（如「总预算硬上限为可用余额的
{maxBudgetPct}%」「默认总预算 = 可用余额 × {maxBudgetPct}%」）。

### 2.3 分配模式：even（平均分配）/ max_ratio（最大比例）

新增类型 `AutoTradeAllocationMode = "even" | "max_ratio"`，默认 `"even"`（向后兼容）。

**核心不变量**：两模式下逐成员预算公式**相同** —— `budget_i = total_budget × pct_i / 100`；差异仅在
**百分比的校验与语义**：

| 维度 | even（平均分配，现状） | max_ratio（最大比例，新增） |
|---|---|---|
| pct 语义 | 对总预算的**份额**（切分） | 对总预算的**独立上限**（互不影响） |
| 单成员范围 | (0, 100] | (0, 100] |
| 整组约束 | Σ pct ≤ 100 | **无 Σ 约束**（可超 100%） |
| 「平均分配」按钮 | `evenSplit`（100 均分） | 不适用（改为「重置上限」或隐藏） |
| 新增成员默认 pct | 把剩余额度给它（`100 − Σ`） | 固定保守默认 `LIVE_AUTO_DEFAULT_MAX_RATIO_PCT`（建议 20） |
| 聚合风险 | Σbudget_i ≤ total | Σbudget_i 可 > total（超额订阅，见风险§七） |

前端 `apps/web/lib/autoTrade.ts` 改为 mode 感知：

- `clampAllocationOnEdit(members, instId, next, mode)`：even → 夹到 `[1, 100 − Σ其余]`；
  max_ratio → 夹到 `[1, 100]`（各自独立）。
- `defaultAllocationOnAdd(members, mode)`：even → `max(0, 100 − Σ)`；max_ratio → `LIVE_AUTO_DEFAULT_MAX_RATIO_PCT`。
- `resolveGroupGate(input)`：`input` 增加 `mode` 字段；仅 even 模式保留
  `totalAllocation > 100 + 1e-9 → "各标的百分比之和超过 100%，请下调"`；max_ratio 模式跳过该闸（仍保留
  「存在百分比为 0 的标的」「含不支持标的」「超 8 标的」等闸）。
- `evenSplit` 仅 even 模式按钮调用，逻辑不变。

后端 `LiveAutoManager._validate_members(members_in, settings, mode)` 增加 `mode` 形参：`mode == "even"`
时保留 `sum(...) > 100 + 1e-9 → LIVE_AUTO_ALLOCATION_INVALID`；`mode == "max_ratio"` 时跳过 Σ 校验，
仅保留「非空 / 去重 / 超上限标的数 / 单成员 ∈ (0,100]」。`start_group` 透传 `req.allocation_mode`。
`GroupStartRequest` 新增 `allocation_mode: str = "even"`。`_scale_threshold`（总止盈止损按 `pct/Σpct`
归一）**无需改动**：max_ratio 下 Σpct 可 > 100，但比例分摊仍成立、各阈值之和恒等于总额。

后端 schema `LiveAutoGroupStartIn` 新增 `allocationMode: Literal["even","max_ratio"] = "even"`，并把现有
`@field_validator("members")` 的 Σ ≤ 100 校验迁移到 `@model_validator(mode="after")`（需同时读 `members`
与 `allocationMode`）：even 强制 Σ ≤ 100；max_ratio 跳过 Σ，仅保留非空 + 去重（单成员 (0,100] 已由
`LiveAutoGroupMemberIn.allocationPct` 的 `Field(gt=0, le=100)` 保证）。

`_create_group_run` 的 run config 增记 `"allocationMode"`（审计可追溯每次启动的模式与各成员上限比例）。

### 2.4 关键代码路径（端到端时序）

1. 用户在「自动交易设置」改 `maxBudgetPct` → `PUT /api/settings/auto-trade` → `repo.set_app_setting`
   → DB。前端 `invalidate(["settings","auto-trade"])`。
2. 用户在自动交易区切换分配模式（segmented：平均分配 / 最大比例）→ `store.setAutoTradeAllocationMode`
   （持久化）→ 成员行的 pct 输入夹取与默认随之切换；header 聚合态文案随模式变化。
3. 用户点分组开关 → `WatchlistTab` 打开 `AutoTradeGroupConfirmModal`。弹窗 `useQuery` 读 `maxBudgetPct`
   与该标的 `trading.config().availUsdt`，按 §2.2 算「默认/夹紧」预览；按 §2.3 渲染各成员预算预览
   （max_ratio 额外提示「合计可超总预算」）。
4. 确认 → `liveAutoGroupApi.start({members, allocationMode, totalCapitalUsd, ...})` →
   `POST /api/live-auto/group/start`。
5. 路由 `_require_auto_enabled` 双闸 → `_resolve_account` → 读 DB `maxBudgetPct` → 算
   `cap_frac = min(maxBudgetPct/100, settings.live_auto_group_max_capital_frac)`、`default_frac = cap_frac`
   填入 `GroupStartRequest.budget_cap_frac` / `default_budget_frac` 与 `allocation_mode` → `manager.start_group`。
6. `start_group`：`_require_capacity` → `_validate_members(..., mode)` → `_gather_context`（avail/持仓/各
   标的 config）→ `_resolve_budget_group(req.total_capital_usd, avail, req.budget_cap_frac, req.default_budget_frac)`
   → `_preflight_group`（逐成员
   不支持/已有持仓/预算过小，**不下单**）→ `_build_group`（建 Run+Executor+Session，止盈止损按比例分摊）
   → `_launch_group_or_rollback`（任一起会话失败整组回滚）。
7. 回执 `totalBudgetUsd` 为夹紧后最终生效预算，逐成员 `budgetUsd` 即 `total × pct/100`。

---

## 三、File / module change plan（文件 / 模块改动清单）

| 文件 | C/M | 一行意图 |
|---|---|---|
| `services/quant/app/db/models.py` | M | 新增 `AppSetting(key PK, value JSON, updated_at)` KV 表（设置中心通用持久化） |
| `services/quant/app/db/repositories.py` | M | 新增 `get_app_setting/set_app_setting` + 类型化 `get_auto_trade_settings/set_auto_trade_settings` |
| `services/quant/app/schemas.py` | M | 新增 `AutoTradeSettingsOut/In`；`LiveAutoGroupStartIn` 加 `allocationMode` 并把 Σ≤100 迁到 model_validator |
| `services/quant/app/api/app_settings.py` | C | 新路由：`GET/PUT /api/settings/auto-trade`（读写 maxBudgetPct，边界校验 0<pct≤100） |
| `services/quant/app/main.py` | M | 挂载第 **11** 个 router（app_settings；`live_auto` 已是第 10 个，RN-3） |
| `services/quant/app/config.py` | M | **RN-1**：`live_auto_max_capital_frac` **保持 0.05 不变**（注释改「单标的路径绝对天花板」）；**新增 `live_auto_group_max_capital_frac`（默认 1.0，分组路径运营天花板，可选 env）**；`live_auto_default_capital_frac` **不弃用**（仍为单标的留空默认，注释标注「分组路径不引用」）；新增 `live_auto_default_max_ratio_pct`（默认 20，可选 env） |
| `services/quant/app/live_auto/manager.py` | M | **RN-1**：`GroupStartRequest` 加 `budget_cap_frac/default_budget_frac/allocation_mode`；`_resolve_budget_group` 改 `(capital_usd, avail, cap_frac, default_frac)`（去 member_count / settings）；**单标的 wrapper `start()` 传旧 frac（0.05/0.01）保行为不变**；`_validate_members` 加 mode 分支；run config 记 allocationMode |
| `services/quant/app/api/live_auto.py` | M | `group/start` 路由读 DB maxBudgetPct → 算 `cap_frac/default_frac`（夹分组天花板）填入 `GroupStartRequest` + 透传 allocationMode；单标的 `/live-auto/start` 路由**不改** |
| `apps/web/lib/liveAutoTypes.ts` | M | 新增 `AutoTradeAllocationMode`；`AutoTradeMember`/`LiveAutoGroupStartReq` 加 `allocationMode`；新增 `AutoTradeSettings` 类型 |
| `apps/web/lib/autoTrade.ts` | M | 上限函数参数化（maxBudgetPct）、default 去成员缩放、clamp/gate/default 加 mode 分支 |
| `apps/web/lib/autoTradeSettingsApi.ts` | C | 新 REST 客户端：`get/update` 自动交易设置 |
| `apps/web/lib/store.ts` | M | 新增持久化 `autoTradeAllocationMode` + `setAutoTradeAllocationMode`；add/edit 走 mode；rehydrate 默认补 "even" |
| `apps/web/components/Settings/SettingsNav.tsx` | M | 注册表新增 `{ key:"auto-trade", label:"自动交易设置", desc:"总预算上限与分配默认" }` |
| `apps/web/components/Settings/SettingsPanel.tsx` | M | `section==="auto-trade"` 渲染 `AutoTradeSettingsSection` |
| `apps/web/components/Settings/AutoTradeSettingsSection.tsx` | C | 新设置面板：maxBudgetPct 输入（0–100，默认 100）+ 保存（乐观更新/回滚，镜像 AgentToolsSection 模式） |
| `apps/web/components/Chart/AutoTradePanel.tsx` | M | header 增分配模式 segmented + mode 感知聚合态/按钮文案 |
| `apps/web/components/Chart/AutoTradeGroupConfirmModal.tsx` | M | 用 useQuery 的 maxBudgetPct 替换 5% 文案；mode 感知成员预算预览 + 超额订阅提示 |
| `apps/web/components/Chart/WatchlistTab.tsx` | M | 取 `autoTradeAllocationMode` 透传给面板/弹窗/start 请求；gate/clamp 传 mode |
| `apps/web/lib/autoTrade.test.ts` | M | 上限参数化 + mode 分支 + 默认无缩放 用例 |
| `apps/web/lib/store.test.ts` | M | allocationMode 持久化 + add/edit 按 mode 用例 |
| `apps/web/components/Settings/AutoTradeSettingsSection.test.tsx` | C | 加载/保存/边界校验用例（NODE_ENV=test） |
| `apps/web/components/Chart/WatchlistTab.test.tsx` | M | 模式切换 + 弹窗预览/启动入参用例 |
| `services/quant/tests/test_app_settings.py` | C | repo + API：默认 100、读写、越界 422、缺失回默认 |
| `services/quant/tests/test_live_auto_group.py` | M | max_ratio Σ>100 放行 / even Σ>100 拒绝；预算解析（默认 100、无成员缩放、上限夹紧、env 天花板 min） |
| `services/quant/tests/test_live_auto_api.py` | M | `group/start` 带 allocationMode；服务端按 DB maxBudgetPct 夹紧（无视前端） |

---

## 四、Interface design（接口设计）

### 4.1 设置读写（新）

```
GET /api/settings/auto-trade
  200 → { "maxBudgetPct": number,             # 用户配置值，缺记录回默认 100
          "effectiveMaxBudgetPct": number }   # RN-2：min(maxBudgetPct, 分组天花板×100)，预览口径

PUT /api/settings/auto-trade
  body → { "maxBudgetPct": number }           # 0 < pct ≤ 100，越界 422
  200 → { "maxBudgetPct": number,             # 回写后最终值
          "effectiveMaxBudgetPct": number }   # 与天花板取 min 后的可用上限
```

Pydantic（`effectiveMaxBudgetPct` 由路由按 `settings.live_auto_group_max_capital_frac` 计算后注入，不入库）：

```python
class AutoTradeSettingsIn(BaseModel):
    maxBudgetPct: float = Field(gt=0, le=100)
class AutoTradeSettingsOut(BaseModel):
    maxBudgetPct: float
    effectiveMaxBudgetPct: float   # RN-2：= min(maxBudgetPct, live_auto_group_max_capital_frac × 100)
```

错误体沿用全局 `{error:{code,message}}`；422 走 FastAPI 校验。前端预览（默认 / 夹紧）一律读
`effectiveMaxBudgetPct`，杜绝运营天花板低于用户配置时高估真实可用预算（RN-2）。

### 4.2 分组启动（扩展，向后兼容）

```
POST /api/live-auto/group/start
  body 新增 → "allocationMode": "even" | "max_ratio"   # 省略=「even」
  其余字段不变；totalCapitalUsd 仍可留空（服务端按 DB maxBudgetPct 解析默认/夹紧）
```

- even：`members` Σ allocationPct ≤ 100（model_validator 强制，违反 422）。
- max_ratio：跳过 Σ 校验；单成员 allocationPct ∈ (0,100]、instId 唯一、非空仍校验。
- 回执结构不变（`totalBudgetUsd` 为夹紧后值，逐成员 `budgetUsd = total × pct/100`）。

### 4.3 前端纯件签名（关键）

```ts
groupBudgetCap(availUsdt: number | null, maxBudgetPct: number): number
clampGroupBudgetToCap(budget: number, availUsdt: number, maxBudgetPct: number): number
deriveGroupDefaultBudget(availUsdt: number, maxBudgetPct: number): number   // 无 memberCount
isGroupBudgetClamped(capitalUsd: number|null, availUsdt: number|null, maxBudgetPct: number): boolean
clampAllocationOnEdit(members, instId, next, mode: AutoTradeAllocationMode): number
defaultAllocationOnAdd(members, mode: AutoTradeAllocationMode): number
resolveGroupGate(input: GroupGateInput & { mode: AutoTradeAllocationMode }): string | null
```

---

## 五、Data model（数据模型）

### 5.1 DB（新表，SQLite，create_all 自动建，幂等）

```python
class AppSetting(Base):
    __tablename__ = "app_settings"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)   # e.g. "auto_trade.max_budget_pct"
    value: Mapped[str] = mapped_column(Text, default="")             # JSON 编码标量/对象
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)
```

- 稀疏：无记录即默认（`get_auto_trade_settings` 缺 key → `maxBudgetPct=100.0`）。
- `init_db` 经 `Base.metadata.create_all` 自动建表，无需手写迁移（与既有表一致）；老库首次启动即补表，
  无数据迁移。
- 通用 KV 设计便于设置中心后续扩展（如把 `allocationMode` 默认、`maxRatioDefault` 也落 DB）。

### 5.2 前端 store（Zustand persist，key `aragon-ui`）

```ts
autoTradeAllocationMode: AutoTradeAllocationMode   // 持久化偏好，默认 "even"
// partialize 增列；onRehydrateStorage 老用户合并默认 "even"
```

`autoTradeMembers[i].allocationPct` 字段复用，语义随 `autoTradeAllocationMode` 变化（不新增列）。

### 5.3 run config（审计）

`strategy_runs.config_json` 增 `"allocationMode"`，与既有 `allocationPct/totalBudgetUsd/groupId` 同处。

---

## 六、Testing & acceptance criteria（测试与验收）

### 6.1 单元 / 集成测试（先写复现再改码，遵 CLAUDE.md 七）

后端（pytest，注意 `services/quant` 测试共享临时 DB，见既有 conftest 约定）：

- `test_app_settings.py`：缺记录 `GET` 回 `maxBudgetPct=100` 且 `effectiveMaxBudgetPct=100`；`PUT 30` 后
  `GET` 回 30；`PUT 0`/`PUT 101` → 422；repo `set_app_setting`/`get_app_setting` 往返一致；
  **RN-2**：`live_auto_group_max_capital_frac=0.2` + 存 `maxBudgetPct=80` → `GET.effectiveMaxBudgetPct==20`。
- `test_live_auto_group.py`（注意 `_resolve_budget_group` **新签名** `(capital_usd, avail, cap_frac, default_frac)`）：
  - even 模式 Σ=120 → `LIVE_AUTO_ALLOCATION_INVALID`；max_ratio 模式 Σ=120 → 放行。
  - 分组留空 + `cap_frac=default_frac=1.0` → `totalBudgetUsd == avail`（**不随成员数缩放**；2 成员与 5 成员同值）。
  - 分组填值 > 上限 → 夹到 `avail × cap_frac`。
  - **RN-2**：`live_auto_group_max_capital_frac=0.2` + `maxBudgetPct=100` → 路由解析出 `cap_frac=0.2`（min 生效）。
  - **RN-1 回归（关键）**：单标的兼容路径 `manager.start()` 留空 `capital_usd=None` → `totalBudgetUsd == avail×0.01`
    （仍 1%，**未被分组天花板上调**）；填值 > 5% → 夹到 `avail×0.05`。证明单标的行为零变更。
  - **RN-5**：删除 / 重写既有 `test_resolve_budget_group_scales_with_member_count`（`:130`，旧签名 + 成员数缩放断言已失效）。
- `test_live_auto_api.py`：`group/start` 带 `allocationMode:"max_ratio"` + Σ>100 → 200；前端送任意「上限」
  字段被忽略，服务端只认 DB `maxBudgetPct`（经天花板夹紧）。

前端（vitest，组件测试须 `NODE_ENV=test`）：

- `autoTrade.test.ts`：`deriveGroupDefaultBudget(1000, 100)===1000` 且无 memberCount 入参；
  `clampGroupBudgetToCap(5000,1000,100)===1000`；`isGroupBudgetClamped(5000,1000,100)===true`；
  `clampAllocationOnEdit([...],id,80,"max_ratio")` 不受 Σ其余 限制；`resolveGroupGate({mode:"max_ratio",Σ=150})===null`。
  **RN-5**：删除 / 重写既有 `autoTrade.test.ts:58-82`（断言 5% 上限 + 成员数缩放的旧用例已与新口径冲突）。
- `liveAuto.test.ts`：**RN-1 回归**——`deriveDefaultBudget`/`clampBudgetToCap`（单标的）签名与 1%/5% 行为
  **保持不变**（断言不动），证明单标的前端零改动。
- `store.test.ts`：`setAutoTradeAllocationMode("max_ratio")` 持久化；max_ratio 下 `addAutoTradeMember`
  用 `LIVE_AUTO_DEFAULT_MAX_RATIO_PCT` 默认；rehydrate 老态补 "even"。
- `AutoTradeSettingsSection.test.tsx`：渲染默认 100；输入 200 提示越界/禁用保存；保存触发 PUT。
- `WatchlistTab.test.tsx`：切到 max_ratio 后两成员各设 80%/70% 可启动；start 入参含 `allocationMode`。

### 6.2 验收标准（Definition of Done 对应）

1. 设置中心出现「自动交易设置」类别，可设总预算上限百分比，默认 100%，保存后持久化、刷新仍在。
2. 分组启动弹窗不再出现写死「5%」；默认总预算 = 可用余额 × `effectiveMaxBudgetPct`%，**不随成员数变化**，
   且当运营天花板低于用户配置时预览随之收敛（RN-2）。
3. 自动交易区可切「平均分配 / 最大比例」；max_ratio 下各成员可独立设 ≤100% 且合计可超 100%，能正常启动；
   even 下合计超 100% 被拦（前端就地提示 + 后端 422）。
4. 后端为分组预算上限唯一权威：前端篡改上限无效；`live_auto_group_max_capital_frac` 天花板可进一步收紧。
5. 全部既有自动交易测试与新增测试通过；**既有单标的旧路径（1 成员 100% 分配）行为逐位不变**——尤其留空
   预算仍派生 `avail×1%`、硬上限仍 `avail×5%`（RN-1 回归测试守护）。

---

## 七、Risks & mitigations（风险与缓解）

- **R1 分组默认 100% 放大资金敞口**：分组留空默认即部署全部可用余额，远比原 5% 激进（**仅分组面板**；
  单标的旧路径默认仍 1%，见 RN-1）。缓解：①**分组运营天花板** `live_auto_group_max_capital_frac`（运营可
  强制收紧，无视前端）；②单笔名义上限 `live_max_order_notional_usd`（默认 1000 USDT）仍逐单本地拦截；
  ③确认弹窗强制「真实资金」勾选 + 显式回显「生效总预算」；④OKX 侧保证金/可用余额校验为最后兜底
  （超额订单被拒）。文档与 UI 显著提示。
- **R2 max_ratio 超额订阅（Σpct>100 → Σbudget_i>total）**：多标的同时触发可能令组合敞口超过名义总预算。
  缓解：这是用户显式选择的语义（各标的独立上限）；弹窗对 Σ>100 显示「合计上限 X%，同时触发可能超总预算」
  橙色提示；真实敞口仍受单笔名义上限 + 保证金兜底；总止盈止损按 `pct/Σpct` 分摊不受影响。
  **RN-6（待确认）**：本设计取 pct 基准为**总预算**（`budget_i = total×pct/100`，单标的实际敞口
  = `avail×maxBudgetPct%×pct%`）。若产品本意为「pct 直接对**可用余额**取比例」，需改基准（§八已列为另起特性）；
  开发前建议与产品二次确认（见评审结论条件①）。
- **R3 上限服务端权威性**：若误把前端「上限」入参当真源 → 可被绕过。缓解：路由只从 DB 读 `maxBudgetPct`
  自行解析 `cap_frac`，**不接受**任何前端上限字段；`_resolve_budget_group` 内再以 `cap_frac` 夹紧
  （cap_frac 已含分组天花板 min，双保险）。
- **R4 向后兼容**：老前端不发 `allocationMode` → 默认 "even"，Σ≤100 行为不变；老库无 `app_settings` 表
  → create_all 自动补、缺记录回默认 100；单标的兼容 wrapper（1 成员 100%）天然属 even、Σ=100 不受影响。
- **R5 预览与回执口径漂移**（参考既有诊断 `auto-trade-group-budget-not-recalculated`）：弹窗 `previewTotal`
  须把 `maxBudgetPct/members/mode/capital` 全部纳入 `useMemo` 依赖，避免改设置/切模式后预览不刷新；最终
  一律以服务端回执 `totalBudgetUsd` 为准。
- **R6 圈复杂度 / 形参膨胀**（CLAUDE.md 二/六）：`_resolve_budget_group` 去掉 member_count、保持 ≤5 形参；
  `GroupStartRequest` 用聚合 dataclass 承载新字段；`resolveGroupGate` 的 mode 分支用早返回，嵌套 ≤4。
- **R7 测试隔离**：后端共享临时 DB 时 `app_settings` 行需各用例自清或用独立 key，避免串扰；组件测试
  设 `NODE_ENV=test` 防 React act() 报错。

---

## 八、Out of scope（不在本次范围）

- 单标的旧路径（`/live-auto/start`）不暴露分配模式（其语义恒为 1 成员 100%）；**其预算口径（留空默认
  1%、硬上限 5%）亦完全不变**（RN-1：分组天花板与 `maxBudgetPct` 设置只作用于分组面板，绝不外溢到单标的；
  `liveAuto.ts` / `AutoTradeConfirmModal` 零改动）。「自动交易设置 · 总预算上限」当前仅治理分组面板。
- 不改 `LiveExecutor`/`LiveAutoSession` 下单与盯市逻辑。
- 不引入「按可用余额而非总预算」的第三种比例基准（如有需要另起特性）。
- 不做 per-account 维度的差异化上限（当前为全局单值；KV 表已为未来扩展留口）。

---

## 实施过程发现的方案缺陷（Issues Found During Implementation）

> 实现节点（代码开发）按本文档逐项落地时发现的方案瑕疵与就地纠偏记录。除以下条目外，全部按 v2 设计实现。

- **IFD-1（§三 文件清单一行意图与 §2.3/§4.3/§5.2 详设不一致，已按详设纠偏）**：§三「文件 / 模块变更计划」表
  中 `apps/web/lib/liveAutoTypes.ts` 一行写「`AutoTradeMember`/`LiveAutoGroupStartReq` 加 `allocationMode`」。
  但详设各节一致把分配模式建模为**分组级单值**：§2.3「新增类型 `AutoTradeAllocationMode`，默认 `even`」由
  store 顶层 `autoTradeAllocationMode` 承载、作为 `mode` 形参传入纯函数；§4.3 纯件签名中 `mode` 是函数入参；
  §5.2 明确「`autoTradeMembers[i].allocationPct` 字段**复用**，语义随 `autoTradeAllocationMode` 变化（**不新增
  列**）」；后端 §2.3 亦只在 `LiveAutoGroupStartIn` 加 `allocationMode`，`LiveAutoGroupMemberIn` 保持
  `{instId, allocationPct}`。**纠偏**：`AutoTradeMember` **不**加 `allocationMode`（否则与 §5.2「不新增列」直接
  冲突，且分配模式对整组统一、并非逐成员属性）；`allocationMode` 加在 `LiveAutoGroupStartReq`（与后端 wire 对齐），
  并新增 `AutoTradeAllocationMode` / `AutoTradeSettings` 两类型。该一行意图属术语含混，按详设落地，无功能偏差。
- **IFD-2（UI 文案去歧义，非功能性）**：AutoTradePanel 头部新增「平均分配 / 最大比例」分配模式 segmented 后，
  原 even 模式下的「平均分配」快捷按钮（调用 `evenSplit`）与模式名「平均分配」字面冲突，故把该快捷按钮重命名为
  「均分」以消除歧义（仅 even 模式显示，max_ratio 模式按 §2.3 表「不适用 → 隐藏」处理）。逻辑不变。

---

## 评审结论（Review Verdict）

**结论：有条件通过（Approved with conditions）。**

方案整体可行、与既有分层 / 安全护栏一致、尺寸适中，已对照真实代码核验各项断言。原 v1 存在一处
**P0**（全局天花板上调静默放大单标的旧路径真实资金敞口约 100×，并造成前后端口径漂移）与一处 **P1**
（分组预览未计运营天花板，可在真实资金知情同意闸上高估预算）。二者均已在 v2 就地修复：

- **RN-1（P0，已修复）**：拆分单标的 / 分组天花板（`live_auto_max_capital_frac` 保持 0.05；新增
  `live_auto_group_max_capital_frac=1.0`），`_resolve_budget_group` 参数化为 `(capital_usd, avail, cap_frac,
  default_frac)`，单标的 wrapper 传旧分数 → 单标的行为逐位不变、零前端改动，并补 RN-1 回归测试。
- **RN-2（P1，已修复）**：`GET/PUT /api/settings/auto-trade` 增 `effectiveMaxBudgetPct`，前端预览改用之。

**进入开发节点前需满足的条件（均为非阻塞 P2）：**

1. **（RN-6）** 与产品二次确认 max_ratio 的比例基准为「总预算」而非「可用余额」；若改基准则属另起特性。
2. **（RN-4）** 确认采用通用 KV `app_settings` 表（而非单行专表 `auto_trade_settings`）——本评审认可通用 KV
   以利设置中心后续扩展，如团队偏好专表惯例可平替，工作量相当。
3. **（RN-3 / RN-5）** 实现时遵循已就地修订的 router 计数（第 11 个）与测试重写清单（删除成员数缩放旧用例、
   更新 `_resolve_budget_group` 新签名相关断言）。

满足以上后无 P0 / P1 遗留，可进入「代码开发」节点。

> 评审范围：仅本 `spec.md`（未改任何源代码，未执行 `git commit`）。文档版本：**v2**。
