# AI 自动交易 · 限价挂单修复与「委托交易」面板（AI Trade Limit Orders Fix & Open Orders Panel）设计方案

> Feature slug: `ai-trade-limit-orders-fix`
> 版本：**v2**（Subtask #0 产出 v1；Subtask #1 评审修订至 v2——全部 P0/P1 已修入正文，见〈评审记录〉）
> 关联既有特性：`ai-trade-limit-orders`（**已实现、未提交**，见 §〇）、`live-pending-orders-visualization`
> （已上线）、`ai-trade-pre-trade-reflection`（`407d274`）、`ai-auto-trading`（v2 已上线）
> 需求出处：Task Board「Original Task」——「这个项目最近添加了 AI 自动交易功能对应的限价挂单的相关功能，
> 但是现在似乎无法正确的有效进行限价挂单；另外，客户端在自动交易的时候，并没有实际显示对应的限价挂单的
> 情况。需要修复，使得自动限价挂单正常，并且客户端要显示出来：显示在『当前持仓』/『成交记录』这里，增加
> 一个『委托交易』，然后在其中显示所有的挂单情况，包括挂单的买入情况，以及挂单的止盈止损情况等等」

---

## 评审记录（Review Notes · Subtask #1，v1 → v2）

> 评审方法：v1 全部 A-1…A-14 断言逐条回源码复核（含实测：限价四件套 **97 passed** 复跑确认 A-2）；
> 全仓 grep 验证「唯一位置重建点」与「无 import 环」两个关键前提；F-1/F-2/F-3 代码草样与真实签名
> （`TradeDecision` 字段序、`_resolve_limit(raw, last_px)`、`validate_limit_intent`、prompts 三函数）
> 逐一对照。核对结论：**v1 的 14 条 as-built 断言全部成立**，三处后端根因与两处前端根因定位准确；
> 但发现 v1 **漏诊了第四处集成缝隙**（R-1），以及两处方案与自身承诺/代码库事实的矛盾（R-2/R-3）。
> 以下按维度列出全部关切；P0/P1 均已修入正文并升版 v2。

| # | severity | 维度 | 关切 | 处置 |
|---|---|---|---|---|
| R-1 | **P1** | 完整性 | **反思同样会吞撤单——v1 漏诊的第四处集成缝隙。**`session._reflect_before_execute`（`session.py:476`）只对 `hold` 直通，`cancel_order` 会照跑一整轮反思 LLM：裁决 `abort` 或 fail-closed（超时/LLM 错误，生产网络抖动即可触发）→ `apply_reflection` 把撤单降级为 hold → **撤单意图被吞**，与 F-2 同症状、同在生产默认路径（反思默认开）。且反思词汇表根本不认识撤单：`_format_reflection_decision`（`prompts.py:370-373`）只映射 open/close，系统 prompt 通篇「会真实下单、动用真金」——对不下单的 cancel 是语义错位，复核官更倾向 abort。不修则 v1 自己的验收标准 #2（撤单必达）**无法稳定成立**，还平白多烧一次 LLM 调用 | **已修**：新增 F-7（§2.8）+ A-15 基线行 + M-8/T-5 变更行；`session.py` 从「显式零改动」名单移出 |
| R-2 | **P1** | 完整性/一致性 | **F-4 防御式解析样例与其自身承诺矛盾。**v1 样例只判 `attachAlgoOrds` 是否为 list，未守卫**元素**类型：OKX 若回带 `["…"]`（非 dict 元素），`attach.get` 抛 `AttributeError`——而 `_normalize_pending_order` 在 `fetch_live_open_orders` 的列表推导里，一行异常炸掉**整个** `GET /trading/open-orders`（持仓子行、图表虚线、新页签三处同灭）。与 R-3 风险行「任何异常 → None，绝不抛错阻断列表」直接冲突 | **已修**：§2.5 样例改为元素级 `isinstance` 守卫；T-4 畸形态明确含「list 元素非 dict」用例 |
| R-3 | **P1** | 一致性 | **F-6 指名的主题变量 `--accent-2` 不存在。**`globals.css` 全部主题变量为 `--bg/--surface/--surface-2/--border/--text/--muted/--accent/--accent-soft/--long/--short`（明暗两套皆无 `--accent-2`）。spec 虽有「不存在则退 --muted」的兜底措辞，但设计文档指名不存在的 token 会诱导实现期写出解析失败的 `var(--accent-2)`（图表层 resolve 失败 → 线不可见或回退黑色） | **已修**：§2.7 直接指定既有 `--muted`（与 tp `--long` / sl `--short` / limit_close `--accent` 三色互异，label + 虚线样式再作区分）；§八 R-4 文案同步 |
| R-4 | P2 | 完整性 | F-4 只取 `attachAlgoOrds[0]`：本系统自挂单恒单条（`build_order_payload` 只附一条 OCO），但外部手工「分批止盈」挂单可带多条 attach——页签只显示首条 | 已在 §2.5 记录为已知局限（本系统挂单无损；外部分批挂单属边缘展示，本期不做多行展开） |
| R-5 | P2 | 欠明确 | F-5 排序规则「当前标的高亮/置顶」与「createdTs 降序」并述而未定义复合次序 | 已在 §2.6 写明：先按「是否当前 instId」分组置顶，组内 `createdTs` 降序 |
| R-6 | P2 | 一致性 | 证据行号轻微漂移（工作树日内变动所致，不影响任何结论）：总闸在 `config.py:163`（v1 写 162）、价带 `:166`（写 165）、TTL `:169`（写 167）、`manager.py:379`（写 380）、恒等断言在 `test_ai_trade_position_step.py:131`（写 132） | 已就地更正表内引用 |
| R-7 | P2 | 一致性 | F-3 各函数签名默认 `limit_band_pct: float = 0.02` 与 `config.py:166` 的默认值构成第二默认源——若未来只改 config 默认，直连调用（测试/回测）仍渲染 2% | 已在 §2.4 注明：签名默认**只是零回归垫片**（且 `allow_limit=False` 路径不消费该参数，回测/既有测试恒不受影响）；运行时唯一真源恒为 settings，生产调用点全部显式传参 |
| R-8 | P2 | 右尺寸 | 其余维度检视：可行性——F-1/F-2/F-3 草样与真实签名逐字吻合、`dataclasses.replace` 对普通 dataclass 适用、React Query/Tabs 范式均有既例，可建；右尺寸——Non-goals（不做撤单端点/归属徽标/TTL 调整）边界清晰，无过度设计；CLAUDE.md 合规——「修 bug 先复现测试」「文件 ≤800 行/方法 ≤50 行」「interface 优先」均已落入方案 | 无需处置（记录性结论） |

**评审复核过的关键前提**（供实施节点直接引用，不必重查）：全仓 `TradeDecision(` 构造点仅 4 处——
`decider.py:114`（_hold）、`decider.py:352-353`（_map_decision 出口）、`signals.py:159`（共识门禁 hold）、
`reflection.py:49`（**唯一**丢字段的位置重建点，F-1 目标）；`order_plan.py:17` 已 `from .decider import
TradeDecision` 而 `decider.py` 不 import order_plan → M-3 移常量**无环**；`backtest.py:357` 共用
`apply_reflection` 但 `allow_limit` 恒 False（`backtest.py:177,180`）→ F-1 对回测逐字等价；
`ChartWorkspace.tsx:333` 渲染 `LiveBottomPanel` 未传 `accountId`（undefined → `openOrdersKey` 归一 null
键）→ F-5 共键去重成立。

---

## 〇、As-built 对齐（Reconciliation · 本方案的事实基线）

> 房规沿用 `ai-auto-trading-enhancements` v3 §2.0：**以 as-built 代码为准，不据历史 spec 纸面签名**。
> 下表结论全部经本次动工前逐行回源码核对（含实测），评审节点请以此为基线。

| # | 命题 | 核对结论 | 证据（file:line） |
|---|---|---|---|
| A-1 | 限价基础特性（`ai-trade-limit-orders` v2）是否已实现 | **已实现、未提交**（工作树 +1036/-90 行，含 2 个新建模块）。总闸 `ai_trade_limit_orders_enabled` 默认 **True** | `config.py:163`、新建 `ai_auto/order_plan.py`、`live_auto/resting.py`；`.env.example:60` |
| A-2 | 该实现的单测状态 | **97 个限价相关后端测试全部通过**（本次实测）——单元级链路是通的，缺陷在**集成缝隙** | `tests/test_live_executor_limit.py`、`test_ai_trade_cancel_action.py`、`test_okx_cancel_order.py`、`test_resting_order.py` |
| **A-3** | 反思收缩是否保留限价意图 | **不保留（本修复的 P0 根因）**。`apply_reflection` 收缩仓位时用 4 参**位置构造**重建 `TradeDecision`，`ord_type`/`limit_px` 落回默认 `"market"`/`None` | `ai_auto/reflection.py:49-50`；`decider.py:105-110`（末两字段带默认值） |
| A-4 | 反思在生产是否默认开启 | **默认开启**（`ai_trade_reflection_enabled=True`，manager 生产装配显式传入）——A-3 不是边角，是**主路径** | `ai_auto/manager.py:379`（`reflection_enabled=req.settings...`）、`config.py:135` |
| **A-5** | `_map_decision` 限价校验的作用范围 | **对全部动作生效（P1 根因）**：`_resolve_limit` 在 action 白名单校验之后、动作分支之前**无条件**执行——`hold`/`cancel_order` 若被 LLM 附带非法 `orderType`/`limitPrice`，会被降级 hold（撤单意图**被吞**） | `decider.py:337-340`（`ord_type, limit_px, limit_degrade = self._resolve_limit(...)` 在所有 action 共用路径上） |
| **A-6** | 价带比例的 prompt/配置一致性 | **漂移（P1 根因）**：价带 env 可配（`ai_trade_limit_price_max_deviation_pct`，默认 0.02），但系统 prompt、工具描述、schema 字段描述**三处硬编码「±2%」字样**——运维改 env 后 Agent 仍按 2% 出价（或反之），出价被门禁反复拒绝 | `config.py:166`；`prompts.py:40` `_LIMIT_TOOL_DESCRIPTION_SUFFIX`（「±2% 以内」）、`prompts.py:95` `_action_discipline`（「±2% 内」）、`prompts.py:160` `_apply_limit_properties`（limitPrice description「±2% 内」） |
| **A-7** | 客户端是否有「委托交易」页签 | **没有**。实盘底部右栏只有「当前持仓 / 成交记录」两页签 | `apps/web/components/Live/LiveBottomPanel.tsx:9-12` |
| **A-8** | 既有挂单可视化是否覆盖开仓限价挂单 | **不覆盖（前端 P0 根因）**：`POSITION_KINDS` 刻意排除 `limit_open`，且 `groupOrdersByPosition` 只保留**命中当前持仓**的委托——AI 挂出的开仓限价单（此时尚无持仓）在持仓子行与图表虚线**双双不可见** | `apps/web/lib/openOrders.ts:11-15`、`:22-38`、`:77-100` |
| A-9 | 挂单读回数据通路是否存在 | **存在且够用**：`GET /trading/open-orders` 聚合普通挂单 + OCO/conditional 策略委托，归一为 `kind ∈ {limit_open, limit_close, tp, sl}`，前端 `tradingApi.openOrders` + `openOrdersKey` 5s 轮询已在持仓表/图表使用 | `okx/trading.py:488-510`、`api/trading.py:130`、`apps/web/lib/tradingApi.ts:65`、`LivePositionsTable.tsx:52-57` |
| A-10 | 归一挂单是否带随单止盈止损 | **不带**：`_normalize_pending_order` 未读 OKX 回带的 `attachAlgoOrds`——需求「挂单的止盈止损情况」中**未成交挂单随附的 TP/SL** 无处可显 | `okx/trading.py:537-556`（无 attach 字段）；随单 OCO 在**成交前**不会出现在 `orders-algo-pending` |
| A-11 | AI 面板是否已有挂单微显示 | 有**一行**：MemberRow 从 `lastDecision.restingOrder` 显示「当前挂单 价 · 张 · 剩余 s」——但它是**每轮决策断面**（轮间不刷新、跳过轮无此段），不能替代委托列表 | `AiTradePanel.tsx:96-124`（工作树版） |
| A-12 | 实盘模式下哪个面板承载「当前持仓/成交记录」 | `accountMode === "live"` 时底部渲染 `LiveBottomPanel`（左下单/右两页签）；`ChartWorkspace` 自身的 `BOTTOM_TABS` 只服务回测/模拟——**「委托交易」页签应加在 `LiveBottomPanel`** | `ChartWorkspace.tsx:331-334`、`:46-49` |
| A-13 | TTL 与唤醒周期的关系 | 无系统性冲突：TTL 默认 300s > 唤醒默认 120s，Agent 有 ≥2 轮机会管理挂单（撤/保）；不作为本次修复项 | `config.py:169`、`config.py:184` |
| A-14 | 手工撤单 REST 端点 | **不存在**（服务层 `cancel_live_order` 仅供执行器内部用）；本方案**不新增**（见 §七 Non-goals） | `api/trading.py`（仅 8 个路由，无 cancel） |
| **A-15** | 反思是否会拦截 `cancel_order`（评审 R-1 补诊） | **会（P1 根因，v1 漏诊）**：`_reflect_before_execute` 只对 `hold` 直通，`cancel_order` 照跑反思——裁决 `abort` 或 fail-closed（LLM 超时/网络错误）→ `apply_reflection` 降级 hold → **撤单被吞**；且反思词汇表只认识 open/close（cancel 落 raw 透传），系统 prompt 通篇「会真实下单」语义错位，复核官天然倾向 abort | `session.py:476`（`decision.action == "hold"` 单值门控）、`prompts.py:370-373`（`_format_reflection_decision` 只映射 open/close）、`reflection.py:39-40`（abort/fail-closed 一律 `_hold`） |

**一句话结论**：限价能力的执行内核（挂单生命周期/三态撤单/幻影成交防护）**是好的且有测试**；「无法有效
限价挂单」的真实来源是四处**集成缝隙**——反思收缩把限价单静默变市价（A-3，生产默认命中）、非下单动作
被限价校验误伤（A-5）、prompt 与价带配置漂移（A-6）、反思拦截撤单（A-15，评审补诊）；「客户端看不到
挂单」的真实来源是**前端从未给开仓挂单留过展示位**（A-7/A-8/A-10）。修复=4 个后端点缺陷 + 1 个前端
新页签，**不动执行器与挂单状态机**。

---

## 一、Overview（概述）

`ai-trade-limit-orders` 特性已在工作树完整落地：Agent 可在 `submit_trade_decision` 里给
`orderType=limit + limitPrice` 挂单、用 `cancel_order` 撤单，执行器以 resting 状态机对抗乐观成交模型的
幻影成交陷阱，97 个单测全绿。但用户实测反馈「无法正确有效进行限价挂单、客户端也看不到挂单」。本次勘察
把症状拆成了两半，并各自找到了确凿根因。

**第一半（后端，限价意图丢失/误伤）**：生产装配默认开启「下单前二次反思」，而 `apply_reflection` 在
采纳反思的仓位收缩值时，用 `TradeDecision(action, new_size, confidence, reason)` 四参重建决策——
`ord_type`/`limit_px` 是带默认值的末位字段，**静默落回 `market`/`None`**。也就是说：Agent 研判出限价
入场点 → 反思说「方向对但仓位收一收」→ 系统就用**市价**把单下了。没有报错、没有降级原因、审计里是一笔
正常市价单——这正是「似乎无法正确有效进行限价挂单」的主路径。次要缺陷有三：`_map_decision` 对**所有**
动作跑限价校验，`hold`/`cancel_order` 若被 LLM 附带残留的 `orderType` 字段会被整轮降级（撤单意图被吞）；
prompt 三处硬编码「±2%」而价带实际可配，运维一改 env 双方立刻互相矛盾；下单前反思对不下单的
`cancel_order` 同样照跑（评审 R-1 补诊），复核官 abort 或 LLM 瞬时失败即把撤单降级为 hold——撤单被吞的
**第二条**通路，且反思词汇表根本不认识撤单动作。

**第二半（前端，挂单不可见）**：既有 `live-pending-orders-visualization` 的设计初衷是「持仓的附属委托」
——`openOrders.ts` 明确过滤掉 `limit_open` 且只保留命中当前持仓的委托。AI 限价开仓单在成交前**没有**
对应持仓，于是在持仓子行、图表虚线两处都被过滤殆尽；客户端又没有独立的委托列表页签。修复方案即需求
原文：在实盘底部「当前持仓 / 成交记录」旁**新增「委托交易」页签**，无过滤地展示账户全部未结委托
（限价开仓 / 限价平仓 / 止盈 / 止损四类），并补齐挂单随附止盈止损的展示（后端归一层透出
`attachAlgoOrds`）；同时让图表虚线也画出开仓挂单价位线。

设计纪律与本仓一致：（1）**不触碰执行器与挂单状态机**——`live_auto/executor.py`、`live_auto/resting.py`
零改动，97 个既有限价测试原样全绿是硬门禁；（2）**修 bug 先写复现测试**（CLAUDE.md §七）——四个后端
缺陷各先落一条会失败的测试再改代码；（3）**加法式接口演进**——`LiveOpenOrder` 只增可选字段，既有消费方
零改动。

---

## 二、Technical design（技术设计）

### 2.1 分层总览（✎=修改，✚=新建，—=显式零改动）

```
后端（修复限价意图链路）
 LLM ──submit_trade_decision(action, sizeFraction, orderType, limitPrice, reason)
       │
       ▼
  decider._map_decision      ← ✎ F-2：限价解析/价带门禁只对下单动作（open_*/close）执行；
       │                            hold/cancel_order 恒 (market, None)，绝不再被误降级
       ▼
  session 共识门禁            ← —（只会重建 hold，无限价意图可丢）
       ▼
  session._reflect_before_execute ← ✎ F-7（评审 R-1）：直通名单 hold → (hold, cancel_order)；
       │                            撤单不进反思（不下单动作无「下单前」复核语义）
       ▼
  reflection.apply_reflection ← ✎ F-1（P0）：收缩分支改 dataclasses.replace(decision,
       │                            size_fraction=new_size)——除仓位外全字段原样保留
       ▼
  order_plan / LiveExecutor   ← —（零改动；resting 状态机原样）

  prompts.py                  ← ✎ F-3：三处「±2%」文案 + schema 描述改由
  manager.py                  ← ✎      settings.ai_trade_limit_price_max_deviation_pct 派生

后端（委托展示增强）
  okx/trading.py `_normalize_pending_order` ← ✎ F-4：透出 attachAlgoOrds 的 tp/sl 触发价
  schemas.py `LiveOpenOrder`                ← ✎     （加法式可选字段）

前端（「委托交易」页签）
  LiveBottomPanel.tsx    ← ✎ F-5：RIGHT_TABS 增第三页签「委托交易」
  LiveOpenOrdersTable.tsx← ✚ F-5：全量未结委托表（5s 轮询，共用 openOrdersKey 去重）
  openOrders.ts          ← ✎ F-6：orderKindMeta 补 limit_open 显式分支；图表价格线纳入 limit_open
  tradingTypes.ts        ← ✎     LiveOpenOrder 类型同步加法字段
```

### 2.2 F-1（P0）：反思收缩保留限价意图

**现状**（`ai_auto/reflection.py:49-50`）：

```python
return TradeDecision(decision.action, new_size, decision.confidence,
                     decision.reason), degrade
```

**改为**：

```python
from dataclasses import replace
...
return replace(decision, size_fraction=new_size), degrade
```

`TradeDecision` 是普通（非 frozen）dataclass，`dataclasses.replace` 直接适用；除 `size_fraction` 外
**全部**字段（含未来新增字段）原样保留——这同时消灭了「下次再给 `TradeDecision` 加字段时反思又丢一遍」
的复发模式（与记忆中 launch-modal 复发缺陷同型：**位置重建是复发根，replace 是根治**）。行为对市价决策
逐字等价（`ord_type="market"`/`limit_px=None` 被原样带过，与今日默认一致）。

**先写复现测试**（`tests/test_ai_trade_reflection.py` 追加）：
`test_reflection_shrink_preserves_limit_intent`——构造 `TradeDecision("open_long", 0.5, 0.9, "r",
"limit", 100.0)` + `confirm/adjusted=0.25` 裁决 → 断言最终决策 `ord_type == "limit"` 且
`limit_px == 100.0` 且 `size_fraction == 0.25`。该测试在现行代码上**必须失败**（红），F-1 落地后转绿。

### 2.3 F-2（P1）：限价解析只对下单动作执行

**现状**（`decider.py:337-340`）：`_resolve_limit` 在动作分支之前无条件执行。后果：

- `cancel_order` + LLM 残留 `orderType:"limit"`（schema 里该字段对所有动作可见，LLM 带上极常见）且
  `limitPrice` 缺失/越带 → 整轮降级 hold → **撤单被吞**，挂单只能等 TTL；
- `hold` + 同类残留 → 无意义的 degrade + 至多 3 次重试（3 次白烧 LLM 调用）。

**改为**（`_map_decision` 内，动作白名单校验之后）：

```python
if action in ORDERING_ACTIONS:          # ("open_long", "open_short", "close")
    ord_type, limit_px, limit_degrade = self._resolve_limit(raw, last_px)
    if limit_degrade is not None:
        return _hold(limit_degrade), limit_degrade
else:                                   # hold / cancel_order：无委托语义，忽略残留字段
    ord_type, limit_px = "market", None
```

`ORDERING_ACTIONS` 提为 `decider.py` 模块级常量（单一真源），`ai_auto/order_plan.py` 现有的私有
`_ORDERING_ACTIONS` 改为 `from .decider import ORDERING_ACTIONS`（order_plan 本就 import decider，
无新增环）。对下单动作路径**逐字零变化**（校验时机/降级原因/重试语义均不变）。

**先写复现测试**（`tests/test_ai_trade_cancel_action.py` 追加）：
`test_cancel_order_ignores_spurious_order_type`——`allow_limit=True` 决策器收到
`{"action":"cancel_order","orderType":"limit","reason":"撤"}`（无 limitPrice）→ 断言 action 仍为
`cancel_order`、degrade 为 None。现行代码上失败（被降级 `missing_limit_price`）。
另补 `test_hold_ignores_spurious_order_type`（同型，hold 不降级）。

### 2.4 F-3（P1）：价带比例的单一真源化

把三处硬编码「±2%」全部改为由 `settings.ai_trade_limit_price_max_deviation_pct` 派生的文案：

- `prompts.py`：
  - 新增纯格式化函数 `_fmt_band_pct(band: float) -> str`：`f"{band * 100:g}%"`（`0.02→"2%"`、
    `0.015→"1.5%"`——默认值渲染与今日文案**逐字节相同**）；
  - `_LIMIT_TOOL_DESCRIPTION_SUFFIX` 常量改为函数 `_limit_tool_description_suffix(band_pct)`；
  - `_action_discipline(allow_limit)` → `_action_discipline(allow_limit, band_pct)`；
  - `_apply_limit_properties(props)` → `_apply_limit_properties(props, band_pct)`（limitPrice 的
    description 同步）；
  - `build_ai_trade_system_prompt(..., allow_limit=False)` 增末位 keyword `limit_band_pct: float = 0.02`；
  - `build_decision_tool_description(allow_limit)` / `build_decision_tool_schema(step, *, allow_limit)`
    增 keyword-only `limit_band_pct: float = 0.02`。
- `decider.py`：`AiTradeDecider.__init__` 构造 `self._tool` 时传
  `limit_band_pct=settings.ai_trade_limit_price_max_deviation_pct`（settings 已是必传参，无需新形参）。
- `manager.py`：`build_ai_trade_system_prompt(...)` 调用处补
  `limit_band_pct=req.settings.ai_trade_limit_price_max_deviation_pct`。

**零回归保障（三层）**：① 既有恒等断言 `build_decision_tool_schema(0.0) is DECISION_TOOL_SCHEMA`
原样通过——`limit_band_pct` 仅在 `allow_limit=True` 分支被消费；② `allow_limit=False` 时全部文案
逐字不变（band 参数不进入任何 False 分支）；③ 新增**冻结字面量**门禁测试（P1-3 教训：绝不与模块常量
自比）：`test_limit_prompt_band_default_binary_identical`——`_limit_tool_description_suffix(0.02)`
与写死在测试里的今日全文逐字相等；`test_limit_prompt_band_follows_setting`——`band=0.05` 时文案含
「±5%」且不含「±2%」。

**注（评审 R-7）**：各签名默认 `limit_band_pct=0.02` **只是零回归垫片**（保证既有直连调用逐字不变），
不是第二配置真源——运行时唯一真源恒为 `settings.ai_trade_limit_price_max_deviation_pct`，生产两个
调用点（decider `__init__` / manager 装配）**全部显式传参**；`allow_limit=False` 路径（回测与全部
既有测试）不消费该参数，签名默认与 config 默认即便日后漂移也互不影响。

### 2.5 F-4：挂单归一层透出随单止盈止损

OKX `GET /trade/orders-pending` 对带随单 OCO 的挂单会回带 `attachAlgoOrds` 数组（下单时
`build_order_payload` 附上的 `tpTriggerPx`/`slTriggerPx`）。随单 OCO 在**挂单成交前**不会出现在
`orders-algo-pending`——不透出它，需求「挂单的止盈止损情况」对未成交挂单就无解。

`_normalize_pending_order`（`okx/trading.py`）追加两个键（防御式解析，缺失/异常一律 None，绝不猜）。
**评审 R-2 修订**：必须做**元素级**类型守卫——`_normalize_pending_order` 跑在 `fetch_live_open_orders`
的列表推导里，任何一行抛异常都会炸掉**整个** `GET /trading/open-orders`（持仓子行/图表虚线/新页签
三处同灭），故 attach 元素非 dict 时也必须静默落 `{}`：

```python
raw_attach = r.get("attachAlgoOrds")
first = raw_attach[0] if isinstance(raw_attach, list) and raw_attach else None
attach = first if isinstance(first, dict) else {}
...
"attachTpTriggerPx": _algo_px(attach.get("tpTriggerPx")),
"attachSlTriggerPx": _algo_px(attach.get("slTriggerPx")),
```

`_normalize_algo_order` 产出的 tp/sl 行**不加**这两个键（Pydantic 默认 None 兜底）。复用既有
`_algo_px`（`"-1"`/空 → None）。`schemas.py` 的 `LiveOpenOrder` 增两个可选字段（见 §四）。

**已知局限（评审 R-4，本期不做）**：只取 `attachAlgoOrds[0]`——本系统自挂单恒单条（`build_order_payload`
只附一条 OCO，无损）；外部手工「分批止盈」挂单可带多条 attach，页签仅显示首条，属边缘展示场景，
不做多行展开（避免为外部数据形态过度设计）。

### 2.6 F-5：前端「委托交易」页签

**位置**：`LiveBottomPanel.tsx`——`RIGHT_TABS` 增 `{ key: "orders", label: "委托交易" }`（置于
「当前持仓」「成交记录」之后），`RightTab` 联合类型同步扩为 `"positions" | "trades" | "orders"`；
内容区三分支渲染 `<LiveOpenOrdersTable instId={instId} accountId={accountId} />`。

**新组件** `apps/web/components/Live/LiveOpenOrdersTable.tsx`（≤200 行；镜像 `LiveFillsTable` 的
数据/状态范式）：

- 数据：`useQuery({ queryKey: openOrdersKey(accountId), queryFn: () => tradingApi.openOrders(accountId),
  refetchInterval: 5_000, refetchOnWindowFocus: true })`——与持仓表/图表**共用同一查询键**（C2 同源
  去重，不产生第三路轮询）。
- 展示范围：**全部未结委托、不过滤**（这是与既有持仓子行的本质差异）：四类 kind 全收，无持仓匹配要求，
  跨 instId 全账户展示（`instId` prop 仅用于排序提示，不做过滤——需求为「显示所有的挂单情况」）。
  排序（评审 R-5 明确复合次序）：**先按「是否当前 instId」分组置顶（当前标的行在前并高亮），组内
  `createdTs` 降序**。
- 列（10 列）：合约 / 类型（kind→中文，见下）/ 方向（`side` 买/卖 + `posSide` 多/空/净 Badge）/
  委托价（`px`，tp/sl 触发后市价显示「市价」）/ 触发价（`triggerPx`，普通挂单「—」）/ 数量（`sz`）/
  随单止盈（`attachTpTriggerPx` ?? "—"）/ 随单止损（`attachSlTriggerPx` ?? "—"）/ 状态（`state`
  中文化：`live`→已挂出、`partially_filled`→部分成交，未知值原样透传不臆造）/ 挂出时间（`createdTs`
  本地时分秒）。
- kind 中文标签走 `openOrders.ts` 新增的 `openOrderKindLabel(kind)`：`limit_open`→限价开仓、
  `limit_close`→限价平仓、`tp`→止盈委托、`sl`→止损委托（**不复用** `orderKindMeta.label`——那套是
  图表线短标签「挂单/平仓/止盈/止损」，语境不同，且改它会波及既有图表/子行文案）。
- 状态三态：加载「加载委托…」/ 失败显式错误文案（C1 绝不假数据）/ 空态「当前无未结委托。AI 自动交易
  挂出的限价委托与止盈止损委托会显示在这里。」

### 2.7 F-6：图表价格线纳入开仓挂单

`openOrders.ts`：

- `orderKindMeta` 把 `limit_open` 从 default 分支提为显式 case：
  `{ label: "开仓挂单", colorVar: "--muted", lineStyle: 2 }`（**评审 R-3 修订**：v1 拟用的 `--accent-2`
  在 `globals.css` 明暗两套主题中均**不存在**，指名会诱导实现期写出解析失败的 `var(--accent-2)`；改用
  既有 `--muted`——与 tp `--long`、sl `--short`、limit_close `--accent` 三色互异，再以「开仓挂单」标签 +
  虚线样式区分；不为一条线新增主题变量。default 分支保留兜底）；
- 新增 `CHART_LINE_KINDS: readonly OpenOrderKind[] = ["limit_close", "tp", "sl", "limit_open"]` 与
  `isChartLineOrder(kind)`；`buildOrderPriceLines` 的过滤由 `isPositionOrder` 改为 `isChartLineOrder`
  ——图表上画出开仓挂单的委托价虚线；
- `POSITION_KINDS` / `isPositionOrder` / `groupOrdersByPosition` **原样不动**（持仓子行语义不变：
  开仓挂单不属于任何持仓，继续不进子行）。

### 2.8 F-7（P1，评审 R-1 新增）：撤单不进反思

**现状**（`session.py:476`）：`_reflect_before_execute` 只对 `hold` 直通——

```python
if not self.decider.reflection_enabled or decision.action == "hold":
    return decision, degrade
```

`cancel_order` 于是照跑一整轮反思 LLM：复核官 `abort`（其词汇表只认识 open/close，对不下单的撤单
语义错位，天然倾向 abort）或 fail-closed（`reflection_timeout`/`reflection_llm_error`，生产网络抖动
即可触发）→ `apply_reflection` 把撤单降级 hold → **撤单被吞**（与 F-2 同症状的第二条通路，挂单只能
等 TTL），还平白多烧一次 LLM 调用。

**改为**（一行门控 + 注释）：

```python
if (not self.decider.reflection_enabled
        or decision.action in ("hold", "cancel_order")):   # 不下单动作无「下单前」复核语义
    return decision, degrade
```

**理由（为什么直通是对的而非放宽安全）**：反思的宪章是「**下单前**二次复核、只会更保守」
（`reflection.py` 模块 docstring）——`cancel_order` 不产生任何委托（`order_plan` 对其恒返回 None），
无「下单」可复核；而**拦截撤单恰恰是反保守的**：挂单本身才是风险敞口，撤回它是收敛动作，把撤单
降级 hold 反而让敞口多活到 TTL。无挂单时 `cancel_order` 是执行器既有的安全空操作，直通零风险。
`reflection.py`/`apply_reflection` **零改动**（收敛映射本身不变，只是撤单不再进入）。

**先写复现测试**（`tests/test_ai_trade_session.py` 追加，复用既有 `_make_reflect_session` 桩 +
`reflection_calls` 计数器）：`test_cancel_order_bypasses_reflection`——反思开启的会话给出
`cancel_order` 决策 → 断言 `llm.reflection_calls == 0` 且最终动作仍为 `cancel_order`。现行代码上
**失败**（reflection_calls == 1；abort 剧本下动作被降级 hold）→ F-7 后转绿。既有「hold 轮不反思」
与全部反思剧本测试原样通过（open/close 进反思的行为逐字不变）。

### 2.9 关键时序（修复后的限价开仓一条龙）

```
Agent 决策轮（allow_limit=True）
  1. LLM → submit_trade_decision{action:open_long, orderType:limit, limitPrice:P, sizeFraction:f}
  2. _map_decision：action∈ORDERING_ACTIONS → validate_limit_intent(P, lastPx, band)   [F-2/F-3]
  3. 共识门禁通过 → reflect：confirm + adjusted=f' → replace(decision, size_fraction=f') [F-1：仍是 limit]
  4. _execute_decision → decision_to_order_plan → OrderPlan("limit", P)
  5. LiveExecutor._do_open：place_order(ord_type=limit, px=P, 随单 OCO 锚 P) → _enter_resting（不写 projected）
  6. 5s monitor → reconcile() → _poll_resting：live→等待 / filled→采纳 avgPx / TTL→撤销
前端（并行）
  7. LiveBottomPanel「委托交易」页签 5s 轮询 GET /trading/open-orders
     → 该挂单以 kind=limit_open + attachTp/SlTriggerPx 行显示                          [F-4/F-5]
  8. 图表：buildOrderPriceLines 画出 P 的「开仓挂单」虚线                                [F-6]
  9. 成交后：挂单行消失，持仓表出现新持仓 + tp/sl 子行（既有行为）
```

---

## 三、File / module change plan（文件 / 模块变更计划）

> ✎=修改 ✚=新建。**显式零改动**（评审已盯核）：`live_auto/executor.py`、`live_auto/resting.py`、
> `ai_auto/order_plan.py`（仅常量改导入，见 M-3）、`ai_auto/reflection.py` 的收敛映射之外部分、
> `okx/trade_client.py`、`api/trading.py`、`signals.py`。
> **评审 R-1 修订**：`session.py` 从零改动名单移出——F-7 需要其 `_reflect_before_execute` 一行门控
> （M-8）；除该行外 session.py 其余部分仍零改动。

| # | 文件 | 动作 | 一句话意图 |
|---|---|---|---|
| M-1 | `services/quant/app/ai_auto/reflection.py` | ✎ | F-1（P0）：收缩分支改 `dataclasses.replace`，保留 `ord_type`/`limit_px` 等全部非仓位字段 |
| M-2 | `services/quant/app/ai_auto/decider.py` | ✎ | F-2：`ORDERING_ACTIONS` 模块常量 + `_map_decision` 仅对下单动作调 `_resolve_limit`；F-3：构造 `_tool` 时传 `limit_band_pct` |
| M-3 | `services/quant/app/ai_auto/order_plan.py` | ✎ | 私有 `_ORDERING_ACTIONS` 改为 `from .decider import ORDERING_ACTIONS`（消灭第二真源；无新增 import 环） |
| M-4 | `services/quant/app/ai_auto/prompts.py` | ✎ | F-3：`_fmt_band_pct` + 三处「±2%」文案/描述参数化（默认渲染逐字节等于今日文案） |
| M-5 | `services/quant/app/ai_auto/manager.py` | ✎ | F-3：`build_ai_trade_system_prompt` 调用处补 `limit_band_pct=req.settings.ai_trade_limit_price_max_deviation_pct` |
| M-6 | `services/quant/app/okx/trading.py` | ✎ | F-4：`_normalize_pending_order` 防御式透出 `attachTpTriggerPx`/`attachSlTriggerPx` |
| M-7 | `services/quant/app/schemas.py` | ✎ | F-4：`LiveOpenOrder` 增两个可选字段（加法，默认 None） |
| M-8 | `services/quant/app/ai_auto/session.py` | ✎ | F-7（评审 R-1）：`_reflect_before_execute` 直通名单 `hold` → `("hold", "cancel_order")`（一行 + 注释；其余零改动） |
| T-1 | `services/quant/tests/test_ai_trade_reflection.py` | ✎ | 复现测试（先红后绿）：收缩保留限价意图；市价决策收缩行为逐字不变 |
| T-2 | `services/quant/tests/test_ai_trade_cancel_action.py` | ✎ | 复现测试：`cancel_order`/`hold` 携残留 `orderType` 不被降级；下单动作校验语义不变 |
| T-3 | `services/quant/tests/test_ai_trade_prompts.py` | ✎ | F-3 门禁：默认 band 文案与冻结字面量逐字相等；band=0.05 → 「±5%」；恒等断言原样通过 |
| T-4 | `services/quant/tests/test_trading_api.py`（或既有 open-orders 测试所在模块） | ✎ | F-4：attachAlgoOrds 有/无/畸形三态归一断言（畸形须含**「list 元素非 dict」**用例，评审 R-2）；既有归一断言零改动通过 |
| T-5 | `services/quant/tests/test_ai_trade_session.py` | ✎ | F-7 复现测试（先红后绿，评审 R-1）：`cancel_order` 不触发反思子调用且动作不被降级；既有反思剧本原样通过 |
| W-1 | `apps/web/components/Live/LiveBottomPanel.tsx` | ✎ | F-5：`RIGHT_TABS` 增「委托交易」+ 三分支渲染 |
| W-2 | `apps/web/components/Live/LiveOpenOrdersTable.tsx` | ✚ | F-5：全量未结委托表（共用 openOrdersKey，5s 轮询，四态 UI） |
| W-3 | `apps/web/lib/openOrders.ts` | ✎ | F-6：`openOrderKindLabel` + `CHART_LINE_KINDS`/`isChartLineOrder` + `orderKindMeta` 显式 `limit_open` 分支；`POSITION_KINDS` 不动 |
| W-4 | `apps/web/lib/tradingTypes.ts` | ✎ | `LiveOpenOrder` 增 `attachTpTriggerPx?`/`attachSlTriggerPx?`（与 M-7 同构） |
| W-5 | `apps/web/lib/openOrders.test.ts` | ✎ | 图表线含 `limit_open`；`groupOrdersByPosition` 仍排除 `limit_open`（回归钉）；kind 标签 |
| W-6 | `apps/web/components/Live/LiveOpenOrdersTable.test.tsx` | ✚ | 渲染四类 kind / 随单 TP/SL 列 / 空态 / 错误态（NODE_ENV=test，见 §六） |

---

## 四、Interface design（接口设计）

**无新增端点、无破坏性变更。** 唯一 wire 变化是加法式字段：

`GET /trading/open-orders`（既有）响应行 `LiveOpenOrder` 增两个可选字段：

```python
class LiveOpenOrder(BaseModel):
    ...                                    # 既有字段全部不变
    attachTpTriggerPx: float | None = None  # 随单止盈触发价（仅普通挂单可能非 None）
    attachSlTriggerPx: float | None = None  # 随单止损触发价（同上）
```

前端 `tradingTypes.ts` 同构：

```ts
export interface LiveOpenOrder {
  ...                                  // 既有字段不变
  attachTpTriggerPx?: number | null;   // 随单止盈触发价（挂单成交前唯一可见来源）
  attachSlTriggerPx?: number | null;   // 随单止损触发价
}
```

Python 内部签名变化（全部为带默认值的加法，既有调用零改动）：

```python
build_ai_trade_system_prompt(inst_id, bar, budget_usd, leverage,
                             position_step_pct=0.0, allow_limit=False,
                             limit_band_pct=0.02)                      # 新末位
build_decision_tool_description(allow_limit=False, *, limit_band_pct=0.02)
build_decision_tool_schema(position_step_pct, *, allow_limit=False, limit_band_pct=0.02)
ORDERING_ACTIONS = ("open_long", "open_short", "close")               # decider.py 新模块常量
```

WebSocket 事件、`AiTradeDecisionOut`、决策工具 schema 的**结构**均不变（仅 description 文案随 band
参数化）；F-7 为 session 内部门控，无任何 wire 变化；CLI 无涉。

---

## 五、Data model（数据模型）

**零 DB 迁移。** 不新增表/列/索引：

- 挂单状态唯一真源仍在 OKX（`models.py:196` 既有立场），本方案只是把已有读回数据**展示出来**；
- `live_order_audit` 既有列已覆盖限价/撤单留痕（基础特性完成）；
- 前端无新增持久化 store 字段（「委托交易」页签数据全部来自 React Query 轮询缓存，页签选择为组件级
  瞬态 `useState`，与既有两页签一致）。

---

## 六、Testing & acceptance criteria（测试与验收）

**执行环境**（沿用仓内既有约定）：后端 `cd services/quant && python -m pytest tests/... -q`；前端
`cd apps/web && NODE_ENV=test npx vitest run ...`（**必须** NODE_ENV=test，shell 默认 production 会触发
React act() 报错）。

### 6.1 修 bug 先复现（CLAUDE.md §七硬约束，顺序不可倒）

1. T-1 `test_reflection_shrink_preserves_limit_intent`：现行代码**红**（ord_type 掉回 market）→ M-1 后绿。
2. T-2 `test_cancel_order_ignores_spurious_order_type` / `test_hold_ignores_spurious_order_type`：
   现行代码**红**（降级 `missing_limit_price`）→ M-2 后绿。
3. T-3 `test_limit_prompt_band_follows_setting`：现行代码**红**（band=0.05 文案仍是 ±2%）→ M-4/M-5 后绿。
4. T-5 `test_cancel_order_bypasses_reflection`：现行代码**红**（cancel_order 触发反思子调用、abort 剧本
   下被降级 hold）→ M-8 后绿（评审 R-1）。

### 6.2 零回归门禁

- 既有限价四件套（`test_live_executor_limit.py` / `test_ai_trade_cancel_action.py` /
  `test_okx_cancel_order.py` / `test_resting_order.py`，97 条）**原样全绿**（执行器/状态机零改动的
  实证）；
- `test_ai_trade_position_step.py:131` 恒等断言 `build_decision_tool_schema(0.0) is DECISION_TOOL_SCHEMA`
  原样通过；
- T-3 冻结字面量断言：`_limit_tool_description_suffix(0.02)` 与测试内写死的今日全文逐字相等（绝不与
  模块常量自比，防「改坏常量后自比恒真」空测）；
- W-5 回归钉：`groupOrdersByPosition` 对 `limit_open` 的排除行为逐字不变（持仓子行不受 F-6 影响）；
- F-7 回归钉：既有「hold 轮不反思」断言与全部反思剧本测试（`test_ai_trade_session.py` §反思段、
  `test_ai_trade_reflection.py` 全量）原样通过——open/close 进反思的行为逐字不变；
- 全量后端 AI-trade 测试 + 前端 vitest 相关套件通过。

### 6.3 验收标准（用户视角）

1. 生产默认配置（反思开启）下，Agent 给出限价开仓且反思收缩仓位后，OKX 侧出现的是**限价挂单**而非市价
   成交（审计 `live_order_audit.ord_type == "limit"`、挂单可在 OKX/委托页签看到）。
2. Agent 发出 `cancel_order`（即便 LLM 附带了残留 `orderType` 字段）时，挂单被真实撤销，轨迹显示「撤单」
   而非降级 hold；**反思开启（生产默认）亦然**——撤单不进反思直通执行（F-7），不因复核官 abort 或
   反思 LLM 瞬时失败而被吞。
3. 运维把 `AI_TRADE_LIMIT_PRICE_MAX_DEVIATION_PCT=0.05` 后，决策 prompt/工具描述同步显示「±5%」。
4. 实盘底部出现第三页签「委托交易」：AI 挂出的限价开仓单（含随单止盈/止损触发价两列）、限价平仓单、
   独立止盈/止损委托**全部**可见，5s 内跟随交易所状态刷新；无挂单显示空态；接口失败显示错误态。
5. 图表出现「开仓挂单」价位虚线；挂单成交/撤销后 5s 内虚线消失。
6. `AI_TRADE_LIMIT_ORDERS_ENABLED=false` 时行为逐字回退（页签仍在但仅显示手工/外部挂单——页签是账户
   级视图，不受 AI 总闸门控）。

---

## 七、Non-goals（本期显式不做）

- **不新增手工撤单端点/按钮**（`POST /trading/cancel-order` 不存在，本期不建）：AI 挂单由会话自管
  （Agent `cancel_order` + TTL 兜底 + 停止时先撤后平），外部挂单撤销请去 OKX 侧操作——「委托交易」页签
  本期是**只读视图**。若后续要做，`cancel_live_order` 服务层已可直接复用（预留一句，不展开设计）。
- 不动回测（`allow_limit` 恒 False 的既有决策不变）。
- 不做挂单归属徽标（「这笔是 AI 会话挂的」）：`clOrdId` 前缀只能区分本系统/外部，无法区分 AI/手工，
  强行标注会误导；留待挂单归属有可靠数据源时再议。
- 不调 TTL/唤醒周期默认值（A-13：现值无系统性冲突）。

---

## 八、Risks & mitigations（风险与缓解）

| # | 风险 | 缓解 |
|---|---|---|
| R-1 | **工作树多特性未提交且共享文件**：本修复改动的 `decider.py`/`prompts.py`/`manager.py`/`session.py`（M-8，评审补入）/`trading.py`/`schemas.py` 已携带未提交的 `ai-trade-limit-orders` 基础特性 hunk（另有 stats-dashboard 等在制特性）。Subtask #3 提交时**严禁 `git add -A`**，须按本表路径精确暂存；`reflection.py`/`LiveBottomPanel.tsx`/`openOrders.ts` 当前干净，hunk 天然可分 | 沿用 position-ladder Subtask#3 的既证方法：`git add <具体路径>` + 必要时 `git apply --cached` 行级暂存；提交序建议「基础特性 commit → 本修复 commit」两笔，或经拍板合为一笔 |
| R-2 | F-3 文案参数化改坏默认渲染（Agent 行为漂移） | 冻结字面量逐字节比对测试（6.2）；`allow_limit=False` 分支不消费 band 参数 |
| R-3 | OKX `attachAlgoOrds` 回带形态不稳定（缺失/空数组/字符串价格畸形） | 防御式解析三态测试（T-4）；任何异常 → None（页签显示「—」），绝不抛错阻断列表 |
| R-4 | F-6 图表新增虚线与既有 tp/sl 线视觉冲突/过密 | `limit_open` 用 `--muted`（与既有三色互异，评审 R-3 修订）+「开仓挂单」标签 + 既有 reconcile 主键 `${ordId}:${kind}` 不撞；仅画当前 instId（`buildOrderPriceLines` 既有过滤保留） |
| R-5 | 「委托交易」页签跨标的全量展示引发信息过载 | createdTs 降序 + 当前标的高亮；列表天然有界（OKX 单账户未结委托量级小）；不做分页，避免过度设计 |
| R-6 | F-2 改动 `_map_decision` 误伤下单动作的校验语义 | 下单动作分支代码逐字不动（仅包一层 `if action in ORDERING_ACTIONS`）；既有 decider 全套测试 + 97 条限价测试作回归网 |
| R-7 | `dataclasses.replace` 对未来 frozen 化的 `TradeDecision` 仍适用性 | `replace` 对 frozen/非 frozen 均有效，无需防御；注释注明「位置重建是复发根」防止回改 |
| R-8 | F-7 被误读为「放宽反思安全面」 | 撤单不产生委托（`order_plan` 恒 None）、本身即风险收敛动作，拦截它反而反保守（§2.8 已论证）；open/close 进反思的行为逐字不变（F-7 回归钉 + 既有反思剧本全量作证）；M-8 注释写明宪章依据防止回改 |

---

## 九、实施过程发现的方案缺陷（Subtask #2 填写）

> 实施节点若发现本方案与 as-built 冲突或有遗漏，在此逐条记录（编号 D-1…），不得静默偏离。

| # | 发现 | 处置 |
|---|---|---|
| D-1 | §2.5 样例直接调 `_algo_px(attach.get(...))`，但 `_algo_px` 对**不可解析的价格字符串不异常安全**——`_optional_float("abc")` 直接 `float("abc")` 抛 `ValueError`（`okx/types.py:172-175` 只容错 None/空串）。这与 §八 R-3 自己的承诺「任何异常 → None，绝不抛错阻断列表」冲突：一行畸形 attach 价格照样炸掉整个 open-orders 端点 | 新增 4 行防御包装 `_attach_px`（try/except (TypeError, ValueError) → None），**仅** attach 两字段使用；`_normalize_algo_order` 的 `_algo_px` 调用零改动（既有行为不变）。T-4 畸形态补入 `tpTriggerPx:"abc"` 用例作证（`test_pending_order_malformed_attach_never_breaks_endpoint`） |
| D-2 | §2.6 列定义「挂出时间（createdTs 本地时分秒）」——仓内唯一时间格式化真源 `fmtTime`（`lib/format.ts`）产出「日期 + 时:分」（成交记录同款），无「秒」变体；且跨日挂单只显示时分秒会产生歧义 | 「挂出时间」列复用 `fmtTime(createdTs)`（与成交记录页签一致，不为一列新造格式化函数）；秒级精度对 5s 轮询的挂单列表无决策价值 |

---

## 评审结论（Review Verdict · Subtask #1）

**有条件通过（approved with conditions）**，文档升版 **v2**。

v1 的勘察质量高：14 条 as-built 断言经逐条回源码复核（含实测 97 passed）**全部成立**，根因定位准确、
零 DB 迁移/零新端点/执行器状态机零改动的边界纪律与本仓惯例一致，Non-goals 右尺寸恰当。评审发现的
3 个 P1（漏诊的反思吞撤单缝隙 R-1、F-4 防御样例自相矛盾 R-2、F-6 指名不存在的主题变量 R-3）与全部
P2 均已按〈评审记录〉修入正文——**无未解决的 P0/P1**。

**实施节点（Subtask #2）必须遵守的条件**：

1. **F-7 是评审新增的第 4 个后端修复，不得遗漏**：`session.py:476` 直通名单一行门控（M-8）+
   `test_cancel_order_bypasses_reflection` 复现测试（T-5，先红后绿）；`reflection.py` 的收敛映射与
   open/close 进反思的行为**逐字不变**。
2. **T-4 的畸形态必须包含「attachAlgoOrds list 元素非 dict」用例**（R-2）——这是 §2.5 元素级守卫
   存在的理由，缺了它守卫可被静默删除。
3. **F-6 使用 `--muted`**（R-3），不得引入新主题变量、不得写 `--accent-2`。
4. 修 bug 顺序不可倒（§6.1 四条复现测试全部先红后绿）；97 条既有限价测试 + 既有反思剧本 +
   `groupOrdersByPosition` 排除 `limit_open` 回归钉原样全绿（§6.2）。
5. 提交纪律沿用 §八 R-1：严禁 `git add -A`，`session.py` 等 mixed 文件按 hunk 精确暂存。
