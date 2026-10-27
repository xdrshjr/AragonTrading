# AI 自动交易 · 总预算驱动标的分配（ai-trade-total-budget-allocation）

- **版本**: v2（Subtask #1 评审修复：2 项 P1 已就地修复、5 项 P2 列为落地条件；详见文首 §评审记录 与文末 §评审结论）
- **所属迭代**: 第 7 轮
- **一句话目标**: 让「启动 AI 自动交易」弹窗里，用户修改**总预算**后，上方每个标的的**分配金额随之按比例重算**，端到端（弹窗预览 + 后端实盘落地）都honor 用户填入的总预算，而不再被一个很小的硬编码上限（可用余额 × 5%）静默锁死。

---

## 评审记录（Review Notes · Subtask #1，v1→v2）

> 评审维度：可行性 / 完备性 / 一致性 / 规模适配。评审已逐节比对真实代码（`AiTradeLaunchModal.tsx`、
> `AutoTradeGroupConfirmModal.tsx`、`lib/autoTrade.ts`、`lib/autoTradeSettingsApi.ts`、`api/ai_auto.py`、
> `api/app_settings.py`、`config.py`、`ai_auto/manager.py`、`schemas.py`、`tests/test_ai_trade_group.py`）。
> **核心方案成立**：根因定位（前端 `clampBudgetToCap` 硬编码 5% + 后端 `min(maxBudgetPct/100, 5%)`）经代码逐行核实无误；
> 所拟复用的纯函数与设置接口**全部真实存在**且与非 AI 弹窗同款；后端配置默认值改动路径唯一（仅 `api/ai_auto.py:95` 消费），无 P0 阻断。
> 下列 P1 已在本文正文就地修复并升 v2；P2 作为「落地条件」写入 §评审结论，交由代码开发节点执行。

### P0（阻断）
- **无。** 默认配置（`maxBudgetPct=100`、`ai_trade_max_capital_frac` 改后 =1.0、`live_auto_group_max_capital_frac`=1.0）下方案端到端自洽、可编译、可运行。

### P1（须修复，已就地修复）
- **P1-1 · 一致性/完备性｜遗漏一处会「撒谎」的用户可见文案。** `AiTradeLaunchModal.tsx:517-519` 的**超额订阅（oversubscribed）提示**硬编码「真实敞口仍受总预算 **5% 天花板**、单笔名义上限与单仓 OCO 约束」。原 §3.2 的文案改动清单**只列了「生效总预算」Row 标签**，遗漏了这处提示；放开 5% 后它将成为错误陈述（对照非 AI 弹窗 `AutoTradeGroupConfirmModal.tsx:246-251` 的等价文案已无「5%」字样）。**修复**：§3.2 新增第 8 条、§四 备注显式要求同步此文案（去掉「5% 天花板」，改述为「受总预算上限（可用余额 × `maxBudgetPct`%）+ 单笔名义上限 + 单仓 OCO 约束」）。
- **P1-2 · 一致性/完备性｜「预览==落地/单一可信来源」在非默认运维配置下不成立，且偏差在不安全方向。** 前端预览读的是 `effectiveMaxBudgetPct = min(maxBudgetPct, live_auto_group_max_capital_frac×100)`（`api/app_settings.py:21-23`，烘焙的是 **live-auto 分组运营天花板**），而 AI 后端夹紧用的是 `ai_trade_max_capital_frac`（`api/ai_auto.py:95`）。二者仅在**默认配置**（两个 frac 均 1.0）下相等；一旦运维把 `live_auto_group_max_capital_frac` 下调到 <1.0，前端预览随 `effectiveMaxBudgetPct` **变小**，而 AI 落地仍按 `maxBudgetPct` 计 → **落地 > 预览（AI 实际下单大于弹窗所示，不安全方向）**。原 §七 风险表只覆盖了相反（安全）方向。**修复**：后端 `cap_frac` 增补 `live_auto_group_max_capital_frac` 项，使 AI 落地天花板 = `effectiveMaxBudgetPct/100` **逐配置严格相等**（§3.1 原则 2、§3.3、§3.4、§四、§五、§七、§八 同步更新，并新增一条后端测试锁死该等式）。此项只会「收紧不放大」，不违背原 P0-1 的安全意图，反而把「AI 与非 AI 分组预算口径统一」贯彻到运营天花板层。

### P2（建议，列为落地条件）
- **P2-1 · 测试可行性｜新查询的 mock 必须是模块级。** §8.2 需明确 `vi.mock("@/lib/autoTradeSettingsApi", …)` 写在**文件顶层**（与既有 `tradingApi` mock 同级），否则未 mock 的既有用例会触发真实 `fetch`。另：`availUsdt` **已由既有 `tradingApi.config` mock 提供**（`beforeEach` 返回 `{availUsdt:1000}`），预览 AC 无需再造该桩——已在 §8.2 澄清。
- **P2-2 · 一致性｜`manager.py` 的 dataclass 默认是「值」不是「注释」。** `ai_auto/manager.py:118` 的 `budget_cap_frac: float = 0.05` 是**默认值**，与放开后的新契约矛盾；虽被 `api/ai_auto.py:106` 恒覆盖（无运行期影响），但 §四 把 manager.py 标注「仅注释」掩盖了它。**条件**：把该默认一并改为 `1.0`（或加注释「恒由 api 层显式覆盖」），消除潜在陷阱与文档漂移（已在 §3.3/§四补明）。
- **P2-3 · 文档一致性｜测试内「5%」措辞会过时。** `tests/test_ai_trade_group.py` 的**模块 docstring（第 2-3 行）**与 `test_resolve_budget_group_anchors_ai_ceiling` 的 docstring 仍写「敞口天花板锚定 5%」；放开后需同步为新口径（已在 §8.1 补一句）。
- **P2-4 · 措辞准确性｜不存在「既有留空 1% 预览断言」。** 已核实现有 `AiTradeLaunchModal.test.tsx` **没有任何预算预览断言**，故 §四/§8.2 原「修正/保留既有留空 1% 预览断言」措辞不准——三条预览 AC 均为**净新增**（已改正措辞，避免下游误找不存在的断言）。
- **P2-5 · 规模适配｜方案规模恰当，无过度设计。** 复用既有纯函数 + 一个已存在的设置接口 + 一处配置默认值 + 一处 `min` 增项，无新 lib/REST/DB 迁移；与「对齐非 AI 弹窗」的既有约定一致，规模适配良好。仅提醒：`aiTrade.ts` 对 `clampBudgetToCap` 的 **re-export 保留不动**（`aiTrade.test.ts:98` 仍依赖它），只移除弹窗内的**使用**——已在 §九 note 2 正确交代。

---

## 一、Overview（概述）

第 6 轮已让弹窗自动回填左侧「自动交易」面板的多标的与时间级别。本轮（第 7 轮）修复一个用户实测到的预算体验缺陷：**在弹窗里改大「总预算（USDT）」后，上方标的清单里每个标的的「预算预览」金额几乎不变，始终被限制在一个很小的数值内**。用户的心智模型是「总预算 × 各标的百分比 = 该标的分配金额」，与「启动多标的自动交易」（非 AI 版）弹窗一致；但 AI 版弹窗当前的行为与之不符。

根因（详见 §二）是：`AiTradeLaunchModal` 的预算预览复用了**单策略实盘自动交易**那条链路的纯函数 `clampBudgetToCap`（来自 `apps/web/lib/liveAuto.ts`），它把总预算**硬编码夹紧到「可用余额 × 5%」**（`LIVE_AUTO_MAX_CAPITAL_FRAC = 0.05`），留空默认取「可用余额 × 1%」（`deriveDefaultBudget`）。因此当账户可用余额不大时，5% 的天花板很低，用户把总预算改到远超该天花板的数值也不会反映到每标的分配——预览「压死」在 5% 上。更关键的是，**后端 `services/quant/app/api/ai_auto.py` 也用同一套 5% 天花板**（`cap_frac = min(maxBudgetPct/100, ai_trade_max_capital_frac=0.05)`），所以仅改前端预览会让「预览显示大、实际下单仍很小」，预览变成谎言。故本修复**必须前后端一起改**。

对照参考：**非 AI 的「启动多标的自动交易」弹窗 `AutoTradeGroupConfirmModal` 已经是用户想要的正确行为**——它读取账户级、用户可在「设置中心 · 自动交易设置」调整的 `maxBudgetPct`（默认 **100%**），用 `clampGroupBudgetToCap` / `deriveGroupDefaultBudget` 计算预览，改总预算即联动每标的分配，并在被夹紧时明确回显「已按 N% 上限夹紧」。本轮的设计就是**把 AI 版弹窗与后端对齐到这条既有的、可配置的 `maxBudgetPct` 天花板**，使两条自动交易链路的预算口径统一、行为一致、单一可信来源（预览与落地共用 `effectiveMaxBudgetPct` = `maxBudgetPct` 与运营天花板取小；见 §评审记录 P1-2），从而彻底消除用户困惑。此举把 AI 分组原先写死的 5% 绝对天花板放开为「由 `maxBudgetPct` 统治」，是一次**明确、有意的安全策略变更**（原 5% 红线来自 `ai-trade-multi-symbol-and-timeframe` spec 的 P0-1），我们通过保留 `maxBudgetPct` 作为可配置安全阀、保留 `ai_trade_max_capital_frac` 配置旋钮（默认放开、运维可再收紧）、并保持所有二级护栏（单笔名义上限、单仓 OCO、会话总止盈止损）不变来把风险约束住（详见 §七）。

---

## 二、根因分析（Key code paths）

### 2.1 前端预览链路（`AiTradeLaunchModal.tsx`）

```
capitalRaw ──parseOptionalUsd──▶ capital.value (number|null)
                                     │
availUsdt (configQ)                  ▼
   └────────────▶ previewBudget = clampBudgetToCap(              ← ★ 问题点
                       capital.value ?? deriveDefaultBudget(availUsdt),  // 留空→avail×1%
                       availUsdt)                                        // 夹到 avail×5%
                     每标的预览 = memberBudget(previewBudget, m.allocationPct)  // = total×pct/100
```

- `clampBudgetToCap(budget, avail)`（`lib/liveAuto.ts:39`）= `min(budget, avail × 0.05)` → **硬编码 5% 天花板**。
- `deriveDefaultBudget(avail)`（`lib/liveAuto.ts:34`）= `avail × 0.01` → 留空默认 1%。
- 因此 `previewBudget` 在 `capital.value > avail×5%` 后恒等于 `avail×5%`，改总预算不再联动 → **用户观察到的现象**。
- 弹窗还**不读取** `maxBudgetPct`（不像非 AI 版弹窗）。

对照 `AutoTradeGroupConfirmModal.tsx:115-119` 的正确写法：
```ts
const maxBudgetPct = settingsQ.data?.effectiveMaxBudgetPct ?? DEFAULT_AUTO_TRADE_MAX_BUDGET_PCT; // 默认100
const previewTotal = clampGroupBudgetToCap(capital.value ?? deriveGroupDefaultBudget(avail, maxBudgetPct), avail, maxBudgetPct);
```

### 2.2 后端预算解析链路（`api/ai_auto.py` + `ai_auto/manager.py`）

- `api/ai_auto.py:94-95`：`at = repo.get_auto_trade_settings(session)`；`cap_frac = min(at["maxBudgetPct"]/100.0, settings.ai_trade_max_capital_frac)`。
- `config.py:99`：`ai_trade_max_capital_frac: float = 0.05` → 因 `min(...)` 取小，**无论 `maxBudgetPct` 多大，AI 分组总预算天花板恒 ≤ avail×5%**。
- `manager.py:474-484` `_resolve_budget_group(capital_usd, avail, cap_frac, default_frac)`：留空→`avail×default_frac(1%)`；填值→`min(capital_usd, avail×cap_frac)`。随后每成员 `budget_i = total × pct/100`（切分，非倍增）。

**结论**：5% 天花板同时钉死在前端预览与后端落地两处，本轮须两处一并放开、并统一到 `maxBudgetPct`。

---

## 三、Technical design（技术方案）

### 3.1 设计原则

1. **单一可信来源**：AI 分组与非 AI 分组的总预算天花板都取账户级 `maxBudgetPct`（设置中心可调，默认 100%）。用户想收紧 AI 敞口，只需在同一处把 `maxBudgetPct` 调低——对两条链路同时生效，零认知负担。
2. **前端预览 == 后端落地（逐配置严格相等，P1-2 修复）**：弹窗预览天花板必须与后端夹紧口径**在任意运维配置下**一致，否则预览撒谎。前端读的是 `effectiveMaxBudgetPct = min(maxBudgetPct, live_auto_group_max_capital_frac×100)`（`api/app_settings.py`，**已含分组运营天花板**），故后端 AI cap 也必须并入同一运营天花板：`cap_frac = min(maxBudgetPct/100, ai_trade_max_capital_frac(默认1.0), live_auto_group_max_capital_frac)`。这样 AI 落地天花板 ≡ `effectiveMaxBudgetPct/100`：默认三阀皆 1.0 → cap=avail×`maxBudgetPct`%＝预览；运维单独下调 `live_auto_group_max_capital_frac` 时**预览与落地同步收紧**（消除原设计「落地>预览」的不安全背离）。唯一保留的**安全方向**残差：运维把 AI 专属 `ai_trade_max_capital_frac` 下调到 < `maxBudgetPct/100` 时预览略高于落地——此为弹窗预览语义所允许，一律以回执 `totalBudgetUsd` 为权威（与非 AI 弹窗同款容差）。
3. **留空默认保持保守 1%**：本轮用户诉求是「**改了**总预算要联动」，并未要求改「留空默认」。把留空默认从 1% 跳到 100% 会造成远大于诉求的真实资金意外，故**留空默认仍取 `avail×1%`**（`deriveDefaultBudget` / `ai_trade_default_capital_frac` 不变），弹窗「可用余额 × 1%」文案保持真实。
4. **最小改动、复用既有纯函数**：前端所需的 `clampGroupBudgetToCap` / `deriveGroupDefaultBudget` / `groupBudgetCap` / `isGroupBudgetClamped` / `DEFAULT_AUTO_TRADE_MAX_BUDGET_PCT` 与 `autoTradeSettingsApi.get` **均已存在**（非 AI 弹窗在用），无需新增 lib 函数或后端接口。
5. **护栏不动**：单笔名义上限（`LIVE_MAX_ORDER_NOTIONAL_USD`）、单仓 OCO、会话总止盈/止损、成员按 pct 切分（非倍增）全部保持，二级风控继续兜底。

### 3.2 前端改造（`AiTradeLaunchModal.tsx`）

1. **新增 settings 查询**（镜像非 AI 弹窗）：
   ```ts
   const settingsQ = useQuery({
     queryKey: ["settings", "auto-trade"],
     queryFn: autoTradeSettingsApi.get,
     enabled: open && mode === "live",   // 仅实盘用总预算天花板；回测走 initialCapital
   });
   const maxBudgetPct = settingsQ.data?.effectiveMaxBudgetPct ?? DEFAULT_AUTO_TRADE_MAX_BUDGET_PCT;
   ```
2. **改写 `previewBudget`**（留空仍 1%，天花板改 `maxBudgetPct`）：
   ```ts
   const previewBudget = useMemo(() => {
     if (availUsdt == null) return null;
     const raw = capital.value ?? deriveDefaultBudget(availUsdt);          // 留空→avail×1%（不变）
     return clampGroupBudgetToCap(raw, availUsdt, maxBudgetPct);           // 天花板→avail×maxBudgetPct%（原 5%）
   }, [availUsdt, capital.value, maxBudgetPct]);
   ```
3. **夹紧态回显**：
   ```ts
   const isClamped = isGroupBudgetClamped(capital.value, availUsdt, maxBudgetPct);
   const budgetCap = groupBudgetCap(availUsdt, maxBudgetPct);
   ```
4. **更新「生效总预算」Row 标签**（原写死「已按 5% 上限夹紧」）：
   ```ts
   label={usingDefault
     ? "默认总预算（可用余额 × 1%）"                                   // 留空默认仍 1%
     : isClamped
       ? `生效总预算（预览 · 已按 ${maxBudgetPct}% 上限夹紧）`
       : "生效总预算（预览）"}
   ```
5. **总预算输入框 clamp 提示**（`Field` 支持 `hint`，与非 AI 弹窗一致）：`isClamped && capital.value != null` 时提示「输入 X 超过可用余额 maxBudgetPct% 上限（budgetCap USDT），已按上限计算」。
6. **imports 调整**：从 `@/lib/autoTrade` 增补 `clampGroupBudgetToCap, deriveGroupDefaultBudget, groupBudgetCap, isGroupBudgetClamped, DEFAULT_AUTO_TRADE_MAX_BUDGET_PCT`（`memberBudget`/`totalAllocation`/`isAutoTradable` 已在导）；新增 `import { autoTradeSettingsApi } from "@/lib/autoTradeSettingsApi"`。从 `@/lib/aiTrade` 保留 `deriveDefaultBudget`，**移除对 `clampBudgetToCap` 的引用**（不再需要 5% 夹紧函数）。
7. **提交负载不变**：`onConfirm` 仍原样传 `capitalUsd: capital.value`；后端负责按 `maxBudgetPct` 夹紧后落地。
8. **同步「超额订阅」提示文案（P1-1，勿遗漏）**：`AiTradeLaunchModal.tsx` 中 `oversubscribed` 分支的提示当前硬编码「……真实敞口仍受**总预算 5% 天花板**、单笔名义上限与单仓 OCO 约束。」——放开 5% 后此句变谎言，须改为不含「5%」的表述，例如「……真实敞口仍受**总预算上限（可用余额 × {maxBudgetPct}%）**、单笔名义上限与单仓 OCO 约束。」（对齐非 AI 弹窗 `AutoTradeGroupConfirmModal.tsx` 同类文案已无「5%」的做法）。这是**面向用户的资金上限文案**，与「注释即文档」同等重要，不得漂移。

> 注意：每标的分配预览 `memberBudget(previewBudget, m.allocationPct)` 一行**不改**——它本就 `= previewBudget × pct/100`，`previewBudget` 修好后即自动联动。

### 3.3 后端改造（放开 AI 分组绝对天花板到 `maxBudgetPct`）

- `services/quant/app/config.py`：`ai_trade_max_capital_frac` 默认 **`0.05 → 1.0`**，并改注释为「AI 分组预算的**可选**绝对天花板分数（默认 1.0＝不约束，实际天花板委托给账户级 `maxBudgetPct` 与分组运营天花板 `live_auto_group_max_capital_frac`，与非 AI 多标的分组口径统一）；运维如需 AI 专属更紧的绝对上限，可下调本值」。
- `api/ai_auto.py:95` 的 `cap_frac` **增补分组运营天花板项（P1-2，一处真实代码改动，非仅注释）**：
  ```python
  cap_frac = min(at["maxBudgetPct"] / 100.0,
                 settings.ai_trade_max_capital_frac,          # AI 专属可选绝对天花板（默认 1.0＝不约束）
                 settings.live_auto_group_max_capital_frac)   # 分组运营天花板（与 effectiveMaxBudgetPct 同源）
  ```
  这样 AI 落地天花板 ≡ 前端所读 `effectiveMaxBudgetPct/100`（后者 = `min(maxBudgetPct, live_auto_group_max_capital_frac×100)/100`）**逐配置严格相等**。默认三阀皆 1.0 → `cap_frac = maxBudgetPct/100`，天花板即 `maxBudgetPct`；两个可选旋钮（AI 专属 / 运营）均保留，`min` 只能收紧不能放大，不违背原 P0-1 安全意图。
- `_resolve_budget_group`、`default_budget_frac = ai_trade_default_capital_frac(1%)` **逻辑不变**：留空仍 1%，填值夹到 `avail×cap_frac`，成员按 pct 切分不变。
- `ai_auto/manager.py:118` 的 dataclass 默认 **`budget_cap_frac: float = 0.05 → 1.0`（P2-2）**：该默认恒被 `api/ai_auto.py:106` 显式覆盖、无运行期影响，但把它连同放开一并对齐，可消除「新契约放开、默认仍写 5%」的文档漂移与潜在陷阱（若未来有直连 manager 的调用方漏传）。
- `schemas.py:455`、`ai_auto/manager.py`（类头 docstring、`_resolve_budget_group` docstring、模块头）相关**注释文案更新**（把「min(maxBudgetPct/100, 5%)」「≤5%」等描述改为「min(maxBudgetPct/100, ai_trade_max_capital_frac=默认1.0, live_auto_group_max_capital_frac)＝maxBudgetPct 与运营天花板共同主导」），保持文档与实现一致；除上述 `cap_frac`/`budget_cap_frac` 两处外无逻辑改动。

### 3.4 端到端序列（实盘，avail=1000、maxBudgetPct=100、两标的各 50%、用户填 800）

```
用户在弹窗改「总预算」=800
  └▶ 前端：previewBudget = clampGroupBudgetToCap(800, 1000, 100) = min(800, 1000)=800
        每标的预览 = 800×50% = 400 USDT（修复前恒为 min(800,50)=50 → 25/标的）
用户确认 → POST /api/ai-trade/start { capitalUsd: 800, members:[...50/50], ... }
  └▶ 后端：cap_frac=min(100/100, 1.0, 1.0)=1.0；_resolve_budget_group(800,1000,1.0,0.01)=min(800,1000)=800
        member_i = 800×pct/100 → 400 / 400；回执 totalBudgetUsd=800，逐成员 budgetUsd=400
前端渲染回执 → 预览与落地一致（400 USDT/标的）
```

---

## 四、File / module change plan（文件 / 模块变更清单）

| 文件 | 动作 | 变更意图（一行） |
|---|---|---|
| `apps/web/components/Agent/AiTradeLaunchModal.tsx` | 修改 | 预算预览改用 `effectiveMaxBudgetPct` 天花板（`clampGroupBudgetToCap`）+ 新增 auto-trade settings 查询 + 夹紧态回显；留空默认仍 1%；每标的分配随总预算联动。**并同步 `oversubscribed` 提示文案去「5% 天花板」（P1-1）。** |
| `apps/web/components/Agent/AiTradeLaunchModal.test.tsx` | 修改 | **文件顶层**新增 `vi.mock("@/lib/autoTradeSettingsApi", …)`（P2-1）；新增三条**净新增**预览 AC（联动 / 夹紧回显 / 留空 1%，复用既有 `tradingApi.config` 的 `availUsdt` mock）。 |
| `services/quant/app/config.py` | 修改 | `ai_trade_max_capital_frac` 默认 `0.05 → 1.0`（放开 AI 分组绝对天花板，委托给 `maxBudgetPct` + 运营天花板）；更新注释。 |
| `services/quant/app/api/ai_auto.py` | 修改（**注释 + 一处代码**） | `cap_frac` **增补 `settings.live_auto_group_max_capital_frac` 项**（P1-2），使 AI 落地天花板 ≡ 前端 `effectiveMaxBudgetPct/100`；更新 `ai_trade_start` docstring/内联注释。 |
| `services/quant/app/ai_auto/manager.py` | 修改（**注释 + 默认值**） | dataclass 默认 `budget_cap_frac: 0.05 → 1.0`（P2-2，恒被 api 覆盖、对齐去漂移）；更新 `_resolve_budget_group`、类头与模块 docstring 里「≤5%」措辞为「≤ avail×`cap_frac`（maxBudgetPct 与运营天花板共同主导）」。 |
| `services/quant/app/schemas.py` | 修改（仅注释） | `AiTradeGroupStartIn` 里「min(maxBudgetPct/100, 5%)」注释更新为新口径；无字段改动。 |
| `services/quant/tests/test_ai_trade_group.py` | 修改 | 更新 `test_total_exposure_capped_at_five_percent`：默认 `maxBudgetPct=100` 时填 900 → **honor 900**（不再夹到 50）；新增 `test_budget_capped_by_max_budget_pct`（`maxBudgetPct` 收紧到 5 → 填 900 夹到 `avail×5%(=50)`）与 `test_ai_budget_honors_group_operational_ceiling`（P1-2：`live_auto_group_max_capital_frac=0.5` → 填 900 夹到 `avail×50%(=500)`，锁死落地=`effectiveMaxBudgetPct`）。`test_blank_capital_defaults_to_one_percent`、`test_resolve_budget_group_anchors_ai_ceiling`（纯函数、显式传 frac）保持；**同步模块/测试 docstring 内「5%」措辞（P2-3）**。 |

> **不新增**：无新 lib 函数、无新 REST 接口、无 DB 迁移、无 desktop 白名单键（仅改既有配置默认值 + 一处 `min` 增项）。

---

## 五、Interface design（接口设计）

**无新增/破坏性接口。** 复用既有：

- **REST（读取账户级总预算上限，前端预览用）**：`GET /api/py/settings/auto-trade` → `AutoTradeSettings { maxBudgetPct: number; effectiveMaxBudgetPct: number }`（`autoTradeSettingsApi.get`）。弹窗取 `effectiveMaxBudgetPct`（已含运营天花板 `live_auto_group_max_capital_frac`），未读到时兜底 `DEFAULT_AUTO_TRADE_MAX_BUDGET_PCT = 100`。**P1-2 保证**：AI 后端 `cap_frac` 现同并入 `live_auto_group_max_capital_frac`，故 AI 落地天花板逐配置严格等于本字段 `/100`。
- **REST（启动，负载不变）**：`POST /api/ai-trade/start`，body `AiTradeGroupStartIn`（`capitalUsd/members[].allocationPct/...` 全不变）。回执 `group_status()` 仍含 `totalBudgetUsd` 与逐成员 `budgetUsd`——现按 `effectiveMaxBudgetPct`（= `maxBudgetPct` 与运营天花板取小）而非硬编码 5% 夹紧。
- **前端纯函数签名（复用，均在 `apps/web/lib/autoTrade.ts`）**：
  - `deriveGroupDefaultBudget(avail: number, maxBudgetPct: number): number`（本方案留空仍走 `deriveDefaultBudget`，此函数备用于文案）。
  - `clampGroupBudgetToCap(budget: number, avail: number, maxBudgetPct: number): number`。
  - `groupBudgetCap(avail: number | null, maxBudgetPct: number): number`。
  - `isGroupBudgetClamped(capitalUsd: number | null, avail: number | null, maxBudgetPct: number): boolean`。
  - `memberBudget(totalBudgetUsd: number, allocationPct: number): number`（＝`total×pct/100`）。

---

## 六、Data model（数据模型）

无 DB schema 变更。涉及的是**配置默认值**与**内存/回执形状**：

- **配置（`Settings`，`config.py`）**：
  - `ai_trade_max_capital_frac: float`：`0.05 → 1.0`（默认不约束；可经 `AI_TRADE_MAX_CAPITAL_FRAC` 环境变量或 `.env` 覆盖为更紧值）。
  - `ai_trade_default_capital_frac: float = 0.01`：**不变**（留空默认 1%）。
- **账户级设置（既有，`repo.get_auto_trade_settings` → `{ maxBudgetPct, effectiveMaxBudgetPct }`）**：默认 `maxBudgetPct = 100`（`DEFAULT_AUTO_TRADE_MAX_BUDGET_PCT`）。本方案不改其读写，仅让 AI 分组也消费它。
- **预算内存口径**：总预算 `total = min(capitalUsd or avail×1%, avail×maxBudgetPct%)`；成员 `budget_i = total × allocationPct/100`（切分，非倍增）；Σ`budget_i == total`。
- **前端 UI 状态**：`AiTradeLaunchModal` 新增派生态 `maxBudgetPct / isClamped / budgetCap`（由查询与现有输入计算，无新 `useState`）。

---

## 七、Risks & mitigations（风险与缓解）

| 风险 | 等级 | 缓解 |
|---|---|---|
| **放开原 P0-1 的 5% 绝对红线（`ai-trade-multi-symbol-and-timeframe`）** | 高（真实资金策略变更） | 这是**有意的、经用户多轮明确诉求驱动**的策略变更：把 AI 分组天花板统一到用户可配置的 `maxBudgetPct`（与非 AI 分组一致）。安全阀仍在且比原设计更全——用户/运维在「设置中心 · 自动交易设置」把 `maxBudgetPct` 调低即对两条链路同时收紧；`ai_trade_max_capital_frac` 旋钮保留（默认 1.0，可下调回 0.05 恢复 AI 专属 5% 绝对上限）；**分组运营天花板 `live_auto_group_max_capital_frac` 经 P1-2 也纳入 AI 落地夹紧**（可对所有自动化分组统一封顶）。**须在提交信息与 spec 变更记录中显式标注该红线放开**，供评审知情决策。 |
| 前端预览与后端落地口径不一致（预览撒谎） | 高 | **P1-2 修复后逐配置对齐**：前端读 `effectiveMaxBudgetPct`（= `min(maxBudgetPct, live_auto_group_max_capital_frac×100)`），后端 `cap_frac = min(maxBudgetPct/100, ai_trade_max_capital_frac(默认1.0), live_auto_group_max_capital_frac)`——两式在**任意 `maxBudgetPct` 与运营天花板取值下严格相等**，杜绝原设计「运维下调 `live_auto_group_max_capital_frac` → 落地 > 预览（AI 实际下单大于弹窗所示，不安全方向）」的背离。唯一残差为**安全方向**：运维把 AI 专属 `ai_trade_max_capital_frac` 下调到 < `effectiveMaxBudgetPct/100` 时预览略高于落地——以**回执 `totalBudgetUsd` 为权威**（弹窗本就是预览语义），与非 AI 弹窗同款容差。 |
| 留空默认被误改成 100% 造成意外满仓 | 高 | 明确**保持 1% 留空默认**（`deriveDefaultBudget` / `ai_trade_default_capital_frac` 不变），仅放开「填值」的天花板；`blank_capital_defaults_to_one_percent` 测试保持为绿护栏；弹窗「可用余额 × 1%」文案不变。 |
| 多标的同时触发致组合敞口叠加 | 中 | 成员按 pct **切分**（`budget_i=total×pct/100`），Σ=total ≤ avail×maxBudgetPct%；`max_ratio` 超额订阅仍有既有提示；单笔名义上限 + 单仓 OCO 二级兜底不变。 |
| 既有断言 5% 的测试变红（`test_total_exposure_capped_at_five_percent`） | 中 | 按 §四更新为新口径断言，并新增 `maxBudgetPct` 收紧后仍被夹紧的正向测试，锁住「`maxBudgetPct` 才是有效天花板」这一新契约。 |
| 组件 vitest 因新查询未 mock 而真实 fetch / act 报错 | 低 | 测试新增 `vi.mock("@/lib/autoTradeSettingsApi", …)` 返回 `{ maxBudgetPct:100, effectiveMaxBudgetPct:100 }`；运行须 `NODE_ENV=test`（本仓组件测试既有约定）。 |
| desktop 打包透传把 `ai_trade_max_capital_frac` 写死 | 低 | 仅改 Python 默认值、不涉新键；`desktop/src/lib/config.js` 的 `AI_TRADE_` 前缀透传仅在用户显式设了环境变量时覆盖，默认不受影响。实现时 grep 确认 desktop 未硬编码该键。 |

---

## 八、Testing & acceptance criteria（测试与验收）

### 8.1 后端测试（`services/quant/tests/test_ai_trade_group.py`）

- **改** `test_total_exposure_capped_at_five_percent` → 语义更新（建议同时改名 `test_total_budget_honored_up_to_max_budget_pct`）：`avail=1000`、默认 `maxBudgetPct=100`、填 `capitalUsd=900` → `totalBudgetUsd == 900`（不再夹到 50），Σ 成员 `budgetUsd == 900`，各成员 = `900×pct/100`。
- **新增** `test_budget_capped_by_max_budget_pct`：把账户 `maxBudgetPct` 收紧到 5（经 `repo.set_auto_trade_settings` 写入）、填 `capitalUsd=900` → `totalBudgetUsd == 50`（`avail×5%`），证明 `maxBudgetPct` 为有效天花板。
- **新增** `test_ai_budget_honors_group_operational_ceiling`（P1-2）：`_override` 传 `live_auto_group_max_capital_frac=0.5`、默认 `maxBudgetPct=100`、填 `capitalUsd=900` → `totalBudgetUsd == 500`（`avail×50%`），锁死「AI 落地 = `effectiveMaxBudgetPct`」这一新契约（即 `min(maxBudgetPct/100, ai_trade_max_capital_frac, live_auto_group_max_capital_frac)` 的运营天花板分支真的生效）。
- **保持** `test_blank_capital_defaults_to_one_percent`：留空 → `10.0`（`avail×1%`）。
- **保持** `test_resolve_budget_group_anchors_ai_ceiling`（纯函数、**显式传 `cap_frac=0.05`**，逻辑未变故仍绿；**其 docstring 内「min(maxBudgetPct/100, 5%)」措辞与模块 docstring「敞口天花板锚定 5%」需同步为新口径，P2-3**）与 `test_starts_group_over_multiple_symbols`（切分口径未变）。
- 运行：`cd services/quant && python -m pytest tests/test_ai_trade_group.py -q`（注意本仓 quant 测试共享临时 DB，见既有约定）。

### 8.2 前端测试（`apps/web/components/Agent/AiTradeLaunchModal.test.tsx`）

- **文件顶层**新增 `vi.mock("@/lib/autoTradeSettingsApi", () => ({ autoTradeSettingsApi: { get: vi.fn() } }))`（P2-1：模块级，避免未 mock 的既有用例触发真实 `fetch`），`beforeEach` 里 `mockResolvedValue({ maxBudgetPct:100, effectiveMaxBudgetPct:100 })`；`availUsdt` **复用既有 `tradingApi.config` mock**（`{availUsdt:1000}`）无需新增。三条 AC 均为**净新增**（现有测试无任何预算预览断言，P2-4）。
- **AC-前端-1（联动）**：`availUsdt=1000`、两标的各 50%，在「总预算」输入 `800` → 每标的预览出现 `400`（修复前为 `25`）。
- **AC-前端-2（夹紧回显）**：mock `effectiveMaxBudgetPct=5`、输入 `800` → 出现「已按 5% 上限夹紧」，每标的预览 `25`。
- **AC-前端-3（留空默认不变）**：留空总预算 → 「默认总预算（可用余额 × 1%）」且每标的预览按 `avail×1%` 切分。
- 运行：`cd apps/web && NODE_ENV=test npx vitest run components/Agent/AiTradeLaunchModal.test.tsx`。

### 8.3 端到端手工验收

1. 实盘账户可用余额较小场景下打开「启动 AI 自动交易」，把总预算改大 → 上方每标的分配金额**随之按百分比增大**（不再压死小值）。
2. 把总预算填到超过「可用余额 × `maxBudgetPct`%」→ 弹窗回显「已按 N% 上限夹紧」，确认后回执 `totalBudgetUsd` 与预览一致。
3. 在「设置中心 · 自动交易设置」把 `maxBudgetPct` 调低 → AI 弹窗天花板同步收紧（与非 AI 弹窗一致）。
4. 留空总预算 → 仍取「可用余额 × 1%」保守默认。

### 8.4 Definition of Done

- 弹窗改总预算，每标的分配金额按 `total×pct/100` 实时联动，端到端（预览＝回执）一致。
- 天花板由 `effectiveMaxBudgetPct`（= `maxBudgetPct` 与运营天花板取小，默认 100%）主导且**前端预览与后端落地逐配置严格相等**（P1-2）；留空默认保持 1%；所有二级护栏不变。
- 前端所有含「5%」的资金上限文案（含 `oversubscribed` 提示）已同步（P1-1）；后端 `cap_frac`/`budget_cap_frac`/相关注释与测试 docstring 均无「5%」漂移（P2-2/P2-3）。
- 后端与前端测试按 §8.1/§8.2 更新并通过；项目可编译（`apps/web` tsc / `services/quant` import）。
- 变更文件均与本需求相关，未提交构建产物/临时文件。

---

## 九、实现注意事项（给下游代码开发节点）

1. **只动预算天花板口径，不动其它**：策略多选、成员增删、时间级别、上下文轮数、OCO、回测分支等一律不改。回测段（`mode==="backtest"`）不消费总预算天花板（走 `initialCapital`），settings 查询以 `enabled: open && mode==="live"` 收敛请求。
2. **保留 `deriveDefaultBudget` 引用**（留空 1%），仅**移除 `clampBudgetToCap`** 引用；确认 `apps/web/lib/aiTrade.ts` 的 re-export 不再被别处依赖后可保留导出不动（避免牵连其它文件）。
3. **P1-2 是一处真实代码改动，非仅注释**：`api/ai_auto.py:95` 的 `cap_frac` 须并入 `settings.live_auto_group_max_capital_frac` 项（三元 `min`），并把 `ai_auto/manager.py:118` 的 dataclass 默认 `0.05→1.0`。这两处是逻辑改动，其余后端改动为纯注释。
4. **注释即文档**：`config.py`/`api/ai_auto.py`/`manager.py`/`schemas.py` 及 `test_ai_trade_group.py` 内所有「5%」「≤5%」措辞务必同步为新口径（本仓 CLAUDE.md 明令注释解释「为什么」且不得留僵尸描述）。前端 `oversubscribed` 提示（P1-1）同理。
5. **提交信息**须以 `feat:` 前缀，一句话摘要，并在正文显式标注「放开 AI 分组 5% 绝对天花板、统一到可配置 maxBudgetPct 与运营天花板」，供审计追溯。

---

## 评审结论（Review Verdict）

**有条件通过（Approved with conditions）。**

方案的**核心判断与技术路线成立**：第 7 轮缺陷根因（前端 `clampBudgetToCap` 硬编码 avail×5% + 后端 `min(maxBudgetPct/100, ai_trade_max_capital_frac=0.05)`）经逐行核实无误；所复用的纯函数（`clampGroupBudgetToCap`/`groupBudgetCap`/`isGroupBudgetClamped`/`deriveGroupDefaultBudget`/`DEFAULT_AUTO_TRADE_MAX_BUDGET_PCT`）、设置接口（`autoTradeSettingsApi.get` → `effectiveMaxBudgetPct`）与参照实现（`AutoTradeGroupConfirmModal`）**全部真实存在**且口径一致；后端配置默认值改动路径唯一、无副作用外溢，**无 P0 阻断**。评审发现的 **2 项 P1 已在本文正文就地修复并升 v2**：

- **P1-1**：补齐 `AiTradeLaunchModal` `oversubscribed` 提示的「5% 天花板」文案同步（§3.2 第 8 条、§四）。
- **P1-2**：修正「预览==落地/单一可信来源」在非默认运维配置下的不安全背离——后端 `cap_frac` 并入 `live_auto_group_max_capital_frac`，使 AI 落地天花板 ≡ 前端 `effectiveMaxBudgetPct/100` 逐配置严格相等（§3.1/§3.3/§3.4/§四/§五/§七/§八）。

**放行条件（代码开发节点须逐条落实，均为 P2）：**

1. **P2-1**：`AiTradeLaunchModal.test.tsx` 的 `@/lib/autoTradeSettingsApi` mock 必须写在**文件顶层**（模块级），复用既有 `tradingApi.config` 的 `availUsdt` mock。
2. **P2-2**：`ai_auto/manager.py:118` 的 `budget_cap_frac` dataclass 默认一并 `0.05→1.0`（去契约漂移；其恒被 api 层覆盖，无运行期风险）。
3. **P2-3**：`test_ai_trade_group.py` 模块 docstring 与 `test_resolve_budget_group_anchors_ai_ceiling` docstring 内「5%」措辞同步为新口径。
4. **P2-4**：实现时知悉「现有前端测试无任何预算预览断言」，三条预览 AC 为净新增（勿去找不存在的旧断言改写）。
5. **P2-5（守界）**：`aiTrade.ts` 对 `clampBudgetToCap` 的 re-export 保留不动（`aiTrade.test.ts` 仍依赖），只移除弹窗内的**使用**；单策略实盘弹窗 `AutoTradeConfirmModal` 的 5% 口径**不受本轮影响**（它走 `live_auto_max_capital_frac`，与分组无关）——不得误改。

**安全备注**：本轮**有意放开** `ai-trade-multi-symbol-and-timeframe` 遗留的 AI 分组 5% 绝对红线（原 P0-1），系用户多轮明确诉求驱动；放开后天花板委托给用户可配置的 `maxBudgetPct` 并叠加运营天花板 `live_auto_group_max_capital_frac`，且所有二级护栏（单笔名义上限、单仓 OCO、会话总止盈止损、成员按 pct 切分）不变。**须在提交信息正文显式标注该红线放开**，供审计追溯。

上述条件均落实且 §8.1/§8.2 测试通过后，本方案可进入代码开发。

---

## 实施过程发现的方案缺陷（Issues Found During Implementation · Subtask #2）

> 代码开发节点按 v2 逐项落地。所有 P0/P1/P2 均已实现并通过 §8.1（16 passed）/§8.2（25 passed）测试、
> `apps/web` tsc 无新增错误（唯一 `WeightBatchArena.test.tsx` 报错为既存、与本轮无关）、后端模块 import 正常。
> 仅一处对 §3.2 导入清单的**极小偏离**，行为与设计完全一致，记录如下：

- **I-1 · 未导入 `deriveGroupDefaultBudget`（不影响行为）。** §3.2 第 6 条把 `deriveGroupDefaultBudget` 列入
  `@/lib/autoTrade` 的增补导入集，但 §五「接口设计」同时注明该函数「本方案留空仍走 `deriveDefaultBudget`，
  **此函数备用于文案**」，且 §3.1 原则 3 明确「留空默认保持保守 1%」——故 `deriveGroupDefaultBudget`
  （= `avail × maxBudgetPct%`）在本实现中**无任何使用点**（留空默认仍由 `deriveDefaultBudget` 给出 `avail×1%`）。
  本仓 `next lint` 会将未使用的导入判为 `no-unused-vars` 错误，且 `CLAUDE.md` 明令删除僵尸代码。因此仅导入
  **实际使用**的 `clampGroupBudgetToCap / groupBudgetCap / isGroupBudgetClamped / DEFAULT_AUTO_TRADE_MAX_BUDGET_PCT`，
  略去 spare 的 `deriveGroupDefaultBudget`。此举与设计的**运行行为逐字节一致**（仅省一个未用符号），
  不改变任何预算口径或天花板语义。
