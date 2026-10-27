# AI 自动交易 · 用户可配置「仓位操作档位」方案（feature: `ai-trade-position-sizing-steps`）

- 文档版本：**v2**（Subtask #1 方案评审与修复；base v1 by Subtask #0）
- 需求来源：task-board.md「Original Task」——开启 AI 自动交易后，弹出的设置面板缺少「允许 AI 操作的仓位档位」设置；当前 AI 自主决定任意仓位百分比（随机/主观金额），不可控。应让用户指定一组离散仓位档位（如每次 +10%：10/20/…/100%；每次 +20%：20/40/…/100%；或 50%/100%），AI 只能在该档位集合内选择开仓规模。
- 约束遵循：本节点**只产出设计文档**，不写实现代码、不改 `docs/plans/<feature>/` 以外的任何源文件、不 `git commit`。

---

## 评审记录（Review Notes · Subtask #1）

> 评审方式：逐节对照 `services/quant` 与 `apps/web` 现源码核实每一处技术断言（决策器 / signals / prompts / manager / backtest / api / schemas / config / executor + 前端弹窗 / toggle / store）。维度：可行性 / 完整性 / 一致性 / 规模合理性。所有 **P0/P1 已就地修复并标注 `[R-n]`**；无 P0。

**总体判断**：方案根因定位准确（`sizeFraction` 是连续自由变量，源自 `decider._map_decision` 收口后直贯 `Signal.strength → LiveExecutor._compute_sz`），核心机制（在 `_map_decision` 单点做档位吸附、`quantize` 以 `max(1,…)` 保底一格、软引导 enum/prompt + 硬保证服务端量化）经源码核实**可行且安全**（不新增敞口面、不改执行器）。以下为需修复项。

### P1（must-fix，均已就地修复）

| # | 维度 | 章节 | 问题 | 修复 |
|---|---|---|---|---|
| R-1 | 可行性/一致性 | §2.5、§三·文件#6、§六 | 回测 spec 数据类被写作 `_BacktestSpec`，**实际类名是 `AiBacktestSpec`**（`backtest.py:65`，`_BacktestSpec` 在仓库中不存在）。Subtask #2「严格按 spec」执行会 grep 不到目标。 | 全文改为 `AiBacktestSpec`。 |
| R-2 | 一致性/完整性 | §2.5、§三·文件#8、§四.1、§五、§六·5 | 自相矛盾：§文件#9 让 schema 用 `Field(ge=0, le=1)`（越界→422 拒绝），但 §六·测试5 又要求 API「`1.5→夹 1.0`」。二者不可兼得；且 `_resolve_position_step` 的 clamp 与 schema 边界**重复**（既有 `leverage: Field(gt=0,le=125)` 也只在 schema 兜边界，API 不复夹）。 | 保留 schema `ge=0,le=1`（越界 422，与既有字段一致）；`_resolve_position_step` **只做 `None→settings 默认`**（去掉冗余 clamp）；测试改为「越界→422、缺省→默认」。 |
| R-3 | 完整性（崩溃） | §五、§三·文件#15、§七·6 | `migrate` 写 `aiTradeConfig.positionStepPct ??= 默认`，但 `aiTradeConfig` 初始值/多版本 migrate 落点均为 **`null`**（`store.ts:516/1100`）。对 `null` 取属性赋值会抛 `TypeError` → 水合崩溃。 | migrate 必须 **null 守卫**；且读点已 `aiTradeConfig?.positionStepPct ?? DEFAULT`，迁移仅为防御性（见 §七·6 与 P2·R-11）。 |
| R-4 | 完整性（静默失效） | §三·文件#14 | 偏好落不了盘：`setAiTradeConfig` 是**整对象替换**（`store.ts:789`），而 `AiTradeToggle` 现有落盘 `setAiTradeConfig({ strategyIds: res.strategyIds, contextRefreshRounds: res.contextRefreshRounds })` **取值自服务端 `res`**，`res` 不回显 `positionStepPct` → 用户选的档位每次重开都回落默认、永不持久。 | 落盘调用显式带上 `positionStepPct: opts.positionStepPct`（与既有两键同一次替换写入）。 |
| R-5 | 一致性（单一真源） | §2.2、§2.4、§四.2 | 档位阶梯在三处独立枚举（`quantize` 的 `max_gears`、schema `enum`、系统 prompt 阶梯文案），实现漂移会让「引导阶梯」与「硬吸附顶格」不一致（尤其顶格 ≤100% 不变式）。违反本项目「单一契约真源」惯例（[[aragon-factor-norm-contract]]）。 | 在 `signals.py` 抽 `position_ladder(step) -> list[float]` 唯一真源；`quantize` 顶格、schema `enum`、prompt 文案、前端 `positionLadder` 全部由它派生（前端为等价纯函数镜像 + 等值单测）。 |
| R-6 | 规模/需求对齐 | §2.1、§三·文件#10、§四.4 | UI 预设里把 `0=连续（AI 自由）` 作为**用户可选项**暴露，直接把用户明确判定「这个是不对的」的行为重新作为一等选项提供，违背需求本意。 | UI 预设 `AI_TRADE_POSITION_STEP_OPTIONS` **移除连续项**（仅留 10/20/25/50/100%）；`step=0` 连续仅保留为**后端零回归默认 + API 可接受值**（供既有直接构造测试 / 运营退出口），不在弹窗露出。 |

### P2（建议项，酌情就地优化，其余记录在案）

- **R-7**：`AiTradeConfig.positionStepPct` 声明为 **可选** `positionStepPct?: number`，与「老配置无此键 + 可为 null」的运行时事实一致（已在 §三·文件#11 标注）。
- **R-8**：系统 prompt 决策纪律 #3 的「`sizeFraction 保守（默认 ≤0.5）`」在 `step>0` 时须**替换**而非追加——否则与「只能取档位」并存自相矛盾（`step=1.0` 全仓档尤甚）。已在 §2.4 写明「替换」。
- **R-9**：`_map_decision` 量化插入点固定为「过 `low_confidence` 门禁后、构造 `TradeDecision` 前」（`decider.py:250→251` 之间），措辞已收紧（§2.3）；因 `low_confidence` 只看 confidence，前后等价，但明确落点便于 Subtask #2/#3 核对。
- **R-10**：弹窗提示文案须写明「档位约束**开仓规模**，非分批加/减仓；`close` 为全平」——执行器单向单仓、同向不加仓（`_project_signals` `if projected==side: continue`），避免用户误解（§七·3，已要求进 UI 文案）。
- **R-11**：persist `version 27→28` 属**防御性**（读点已 `?? DEFAULT` 兜底，新增可选字段本不强制迁移）；保留但须配 null 守卫（R-3），已在 §五 注明其性质。
- **R-12**：`_resolve_position_step` 对**非 100 整除**的自由 step（如 API 直传 0.3 → 顶格 0.9，永不满仓）仅夹 `[0,1]` 不吸附预设。UI 只出 10/20/25/50/100 故无此问题；API 侧属可接受边界，已在 §七·5 记录（如需可后续加「snap 到预设」增强，本期不做）。

### 复核确认为**准确无误**的关键断言（无需改）

- `decider.__init__` 在 `*` 后为 keyword-only，追加 `position_step_pct: float = 0.0` 零回归（`decider.py:98`）。
- `signals.py` **未** import `math`，方案 §2.2 已含 `import math`，正确。
- `build_decision_tool_schema` / 前端三符号（`AI_TRADE_POSITION_STEP_OPTIONS` 等）确不存在，为新增，正确。
- `config.py` 无 `env_prefix`、字段名即 env 名、`extra="ignore"`；`desktop/config.js:80` prod 透传含 `AI_TRADE_` 前缀 → 新 env 免改白名单，§五断言正确。
- manager `AiTradeGroupStartRequest`（dataclass，`manager.py:95`）、`AiBacktestStartRequest`（`backtest_manager.py:46`）、`ai_trade_start`/`ai_backtest_start` 读 `body.*`、`_run_config`/`_create_group_run` config 回显位点，均与方案一致。
- 「legacy `AiTradeStartIn` 未被 API 引用」经 grep 证实为孤儿（`schemas.py:337`），§文件#9 判断正确。

---

## 一、Overview（概述）

本项目 `AragonTrading` 是「量化交易平台」，后端为 `services/quant`（FastAPI + Python），前端为 `apps/web`（Next.js + React + TypeScript + zustand + react-query），并有 `desktop`（Electron 打包）。界面最右侧的「AI 助手」面板顶部有一枚「AI 自动交易」开关（`apps/web/components/Agent/AiTradeToggle.tsx`），点开后弹出启动弹窗 `AiTradeLaunchModal.tsx`。启动后由后端 `services/quant/app/ai_auto/` 包接管：多策略「信号板」逐根影子回放（`signals.py`）→ `AiTradeDecider`（`decider.py`）综合研判产出**唯一受约束决策** `submit_trade_decision{action, sizeFraction, confidence, reason}` → 映射为 `Signal` → `LiveExecutor`（`live_auto/executor.py`）守护下单。

问题的**根因**在于决策输出里的 `sizeFraction`：它是一个**连续自由变量** `∈(0,1]`，由 LLM 自行给出（`decider._map_decision` 仅做 `_clamp01` 与 `>0` 校验，`prompts.DECISION_TOOL_SCHEMA` 也只约束 `minimum:0/maximum:1`）。该值随后成为 `Signal.strength`（`signals.decision_to_signals`），并在 `LiveExecutor._compute_sz` 里换算为下单名义：`target_notional = clamp(strength,0,1) × budget_usd × leverage`。因此 AI 每次开仓的仓位规模是**任意小数**，正是需求所述「随机数量 / 主观金额」的来源。

本方案在**不新增任何绕过资金护栏的下单路径**、**不改 `LiveExecutor` 一行**的前提下，引入一个用户可配置的「仓位操作档位」（position sizing step，下称**档位步长** `positionStepPct`）。用户在启动弹窗里从预设档位中选择（10% 档 / 20% 档 / 25% 档 / 50% 档 / 100% 全仓 / 由 AI 连续决定）；该步长生成一条**离散档位阶梯** `{k·step | k=1..⌊1/step⌋}`（如 step=0.1 → 10/20/…/100%；step=0.2 → 20/40/…/100%；step=0.5 → 50/100%），并在决策器唯一决策收口处 `_map_decision` 把 LLM 给出的 `sizeFraction` **量化吸附**到最近的合法档位。这样：AI 仍**自主决定方向与是否开仓**（开多/开空/平仓/观望不受影响），但**开仓规模只能落在用户指定的档位上**，从「随机金额」变为「用户可控的固定百分比档位」。

## 二、Technical design（技术设计）

### 2.1 核心模型：一个标量步长 → 一条离散阶梯

需求给的三个例子（+10%、+20%、50%/100%）都是**等差阶梯**，公差即首项。因此用**单一标量 `positionStepPct ∈ (0,1]`** 即可完整表达，无需传整份列表：

- `step = 0.1` → 阶梯 `{0.1,0.2,…,1.0}`（10 档）
- `step = 0.2` → 阶梯 `{0.2,0.4,0.6,0.8,1.0}`（5 档）
- `step = 0.25` → 阶梯 `{0.25,0.5,0.75,1.0}`（4 档）
- `step = 0.5` → 阶梯 `{0.5,1.0}`（2 档）
- `step = 1.0` → 阶梯 `{1.0}`（每次全仓）
- `step = 0`（哨兵）→ **连续/不约束**（旧行为）。**[R-6]** 该值仅作为**后端零回归默认**（`decider`/`AiTradeGroupStartRequest`/`AiBacktestStartRequest` dataclass 默认 `0.0`，保直接构造的既有测试逐字不变）与 **API 可接受的显式退出口**（运营/测试），**不在弹窗预设中露出**——需求已明确判定「AI 自由决定金额」是错误行为，UI 只允许用户在离散档位中选择（见 §2.1 末与 §4.4）。

**[R-5] 阶梯单一真源**：上述 `{k·step}` 阶梯必须由**唯一函数** `signals.position_ladder(step)` 生成，`quantize_size_fraction` 的顶格上限、决策工具 schema 的 `enum`、系统 prompt 的档位文案三者**全部由它派生**（前端 `positionLadder` 为等价纯函数镜像，配等值单测）。禁止在三处各自 `floor(1/step)`，避免「引导阶梯」与「硬吸附顶格」漂移、破坏「顶格恰为 100%」不变式（对齐本项目单一契约真源惯例）。

线格式与既有 `stopLossPct/takeProfitPct` 一致，用 **fraction**（`0.1` 表示 10%），UI 展示 `%`。

### 2.2 阶梯真源 + 量化函数（纯函数、单一真源）

在 `ai_auto/signals.py` 新增两个纯函数（与既有 `compute_consensus`/`apply_consensus_gate`/`decision_to_signals` 同风格、易单测、实盘与回测共用）。**[R-5]** `position_ladder` 是档位阶梯的**唯一真源**：`quantize_size_fraction` 的顶格、decider 工具 schema 的 `enum`、系统 prompt 的档位文案，全部由它派生，杜绝三处各自 `floor(1/step)` 漂移。

```python
import math
_STEP_EPS = 1e-9

def position_ladder(step: float) -> list[float]:
    """档位阶梯的唯一真源：{k·step | k=1..max_gears}，顶格 ≤ 100%。

    step<=0 → 返回 []（连续/不约束，无离散档位）。max_gears=floor((1+eps)/step) 保证顶格不越
    100%（step=0.25→[.25,.5,.75,1.0]；step=0.3→[.3,.6,.9]，故预设只放能整除 100 的 10/20/25/50/100）。
    """
    if step <= 0:
        return []
    max_gears = int(math.floor((1.0 + _STEP_EPS) / step))
    return [round(k * step, 6) for k in range(1, max_gears + 1)]

def quantize_size_fraction(raw: float, step: float) -> float:
    """把开仓仓位占比吸附到最近的合法档位（step 的整数倍），夹到 [step, 顶格]。

    step<=0 → 连续（仅夹到 [0,1] 直通，等价旧行为）。开仓量至少一格（保证一次真实开仓意图
    不因四舍五入到 0 而被吞成 hold）；顶格取自 position_ladder（≤100%），单一真源。
    """
    lo = max(0.0, min(raw, 1.0))
    ladder = position_ladder(step)
    if not ladder:                       # step<=0：连续直通
        return lo
    gears = max(1, min(round(lo / step), len(ladder)))   # 顶格 = 阶梯长度（真源一致）
    return ladder[gears - 1]
```

要点：① `max(1, …)` 使任何 `>0` 的开仓意图至少落到最小一格，绝不因吸附到 0 被误判 `missing_size`；② 顶格由 `len(ladder)` 决定，与 `position_ladder` **同源封顶 100%**（step=0.25→顶格 1.0；step=0.3→顶格 0.9，故预设只放能整除 100 的 10/20/25/50/100，避免出现 99% 这种别扭顶格）；③ 阶梯元素已 `round(…,6)`，消除浮点尾差。

### 2.3 决策收口处量化（唯一改动点，实盘＋回测同时生效）

`AiTradeDecider`（`decider.py`）是实盘 `session.py` 与回测 `backtest.py` **共用**的唯一决策脑，二者都调 `decider.decide → _attempt_decision → _map_decision`。因此把量化放在 `_map_decision` 是**单一收口**，一处改动两处生效，且量化后的 `size_fraction` 会自然贯穿到：`TradeDecision → Signal.strength → LiveExecutor._compute_sz`（真实下单量）、`decision` WS 事件、`agent_trace` 的 `toolCall`、`_journal`/滚动摘要——UI、审计、执行**三者显示与落地完全一致**。

- 构造签名新增 keyword 参数 `position_step_pct: float = 0.0`（默认 0＝连续，**保证所有既有直接构造 `AiTradeDecider(...)` 的位置零回归**）；存 `self._position_step = max(0.0, float(position_step_pct))`。
- `self._tool` 改为经 `build_decision_tool_schema(self._position_step)` 构造（见 §2.4），把档位 `enum` 注入工具 schema，进一步引导模型只报合法档位（但**服务端量化才是硬保证**，模型忽略 enum 也无碍——本项目 LLM 层无 `tool_choice` 强制，见 [[aragon-ai-auto-trading]] P1-5）。
- **[R-9]** `_map_decision` 内，`open_*` 分支在拿到合法 `size = _clamp01(...)`（`None`/`≤0` 仍回落 `missing_size`，`decider.py:246-248`）**且过完 `low_confidence` 门禁之后**（`decider.py:249-250`）、构造 `TradeDecision` 之前（`decider.py:251`），插入一行：`size = quantize_size_fraction(size, self._position_step)`。因 `low_confidence` 只读 `confidence`、与 `size` 无关，量化落在门禁前后等价；固定为门禁后是为让 Subtask #2/#3 有唯一可核对落点。`close/hold`、`confidence` 门禁、其余安全回落**逐字不变**。注意 `quantize` 的 `max(1,…)` 保证任何 `>0` 输入不会被吸附回 0，故**不会新触发 `missing_size`**（该回退仍只由 `_clamp01` 的 `None/≤0` 决定，回归安全）。

### 2.4 Prompt 与工具 schema 引导（软约束，配合硬量化）

`ai_auto/prompts.py`：

- `build_ai_trade_system_prompt(inst_id, bar, budget_usd, leverage, position_step_pct=0.0)` 新增**末位** keyword 参（默认 `0.0`）——现有 `manager._build_member`（`manager.py:334-337`）与 `backtest` runner（`backtest.py:157-160`）都是**位置传前 4 参**，追加带默认的末参**不破位置调用**，零回归。**[R-8]** 当 `step>0` 时，把决策纪律 #3 的「`sizeFraction 保守（默认 ≤0.5）`」整句**替换**（**非追加**，否则与档位约束并存自相矛盾——`step=1.0` 全仓档尤甚）为：「**开仓 sizeFraction 只能从允许档位中选择：{position_ladder(step) 逗号列表}；系统会把你给出的值吸附到最近的合法档位**」；`step==0` 时保持原文（连续）。
- 新增 `build_decision_tool_schema(position_step_pct)`：返回 `DECISION_TOOL_SCHEMA` 的浅拷贝；`step>0` 时把 `properties.sizeFraction` 替换为 `{"type":"number","enum": position_ladder(step), "description":"开仓仓位占比，只能取列出的档位"}`（**[R-5]** `enum` 直接取自 `position_ladder` 真源），`step==0` 时原样返回。模块常量 `DECISION_TOOL_SCHEMA` 本身**不改**（回退兜底 + 现有引用不破）。
- `build_decision_prompt(payload)` 可选：当 `payload` 带 `positionSteps` 时追加一行「本轮开仓档位仅限：…」。为降耦，主引导放系统 prompt（会话内固定），此项为可选增强，下游可略。

### 2.5 装配链路（把 step 从请求穿到决策器）

- **实盘** `manager.py`：`AiTradeGroupStartRequest`（dataclass，`manager.py:95`）**在其带默认值字段区末尾**（`default_budget_frac: float = 0.01` 之后，`manager.py:121`）追加 `position_step_pct: float = 0.0`（默认 0＝连续，**保证直接构造该 dataclass 的既有测试零回归**；实际值由 API 解析后填入，见下）。`_build_member`（`manager.py:334-337`）里：`build_ai_trade_system_prompt(plan.inst_id, req.bar, plan.budget_usd, ctx.lever, position_step_pct=req.position_step_pct)` 且 `AiTradeDecider(..., context_refresh_rounds=req.context_refresh_rounds, settings=req.settings, position_step_pct=req.position_step_pct)`。可选（P2 观测项）：`_create_group_run` 的 `config` dict（`manager.py:536-544`）与 `AiTradeGroup`/`group_status()`（`manager.py:135/409`）回显 `positionStepPct`，供运行态面板/审计展示。
- **回测** `backtest.py`：**`AiBacktestSpec`**（dataclass，`backtest.py:65`——**[R-1]** 实际类名，仓库中无 `_BacktestSpec`）追加 `position_step_pct: float`（置于字段区、与既有全必填字段对齐，或给默认 `= 0.0` 以便直接构造）；runner（`backtest.py:157-160`）构造 `self.decider = AiTradeDecider(..., position_step_pct=spec.position_step_pct)` 且系统 prompt 同传末参；`_run_config()`（`backtest.py:510-519`）回显 `positionStepPct`。`backtest_manager.py`：`AiBacktestStartRequest`（`backtest_manager.py:46`）在带默认字段区追加 `position_step_pct: float = 0.0`，`_build_spec`（`backtest_manager.py:109-128`）透传进 `AiBacktestSpec`。
- **API** `api/ai_auto.py`：新增私有 `_resolve_position_step(raw: float | None, settings) -> float`：**[R-2]** **只做缺省回填**——`raw is None → settings.ai_trade_default_position_step_pct`，否则原样返回 `raw`。**不再重复夹 `[0,1]`**：schema `Field(ge=0, le=1)` 已在入口拒绝越界（返回 422，与既有 `leverage: Field(gt=0, le=125)` 只在 schema 兜边界、API 不复夹的惯例一致）。`ai_trade_start`（`api/ai_auto.py:82-121`）与 `ai_backtest_start`（`176-196`）各取 `body.positionStepPct` 经此解析后填入对应 request（位置与既有 `default_budget_frac=settings.ai_trade_default_capital_frac` 的 settings 缺省注入同构）。

### 2.6 时序（一次开仓决策）

1. `feed_candle`（实盘）/ `_run_decision`（回测）→ 装配 payload → `decider.decide`。
2. LLM 流式产出 `submit_trade_decision`，`sizeFraction` 可能为任意 `∈(0,1]`（即便有 enum 引导也不保证严格）。
3. `_map_decision`：合法性校验 → **`quantize_size_fraction` 吸附到档位** → `TradeDecision.size_fraction = 档位值`。
4. `decision_to_signals` → `Signal(strength=档位值)` → `LiveExecutor.apply → _compute_sz`：`notional = 档位值 × budget × leverage`。
5. `decision`/`agent_trace` 事件、`_journal`、滚动摘要均记录**档位值**，前端既有渲染无需改动即显示档位化后的 sizeFraction。

### 2.7 关键不变式 / 边界

- **方向自主性不变**：`open_long/open_short/close/hold` 的选择仍由 AI 决定；档位只约束**开仓规模**。
- **不改执行器 & 不新增敞口面**：`sizeFraction` 语义（`budget×leverage` 名义占比）不变；`budget` 已经过分组总预算天花板与 allocation 切分；单笔名义上限、单仓 OCO、分组预算上限**全部照旧生效**。「100% 档」= 满该标的预算名义，仍 ≤ 既有一切上限，无新增敞口。
- **执行器单向单仓、无金字塔**：`LiveExecutor._project_signals` 对「与当前持仓同向的再次开仓」直接跳过（`if projected == side: continue`）。故档位作用于**建仓时的规模**，同向再报不加仓、不缩仓（既有行为）。此点须在文档与前端提示中写清，避免误解为「可用档位分批加/减仓」。
- **最小下单量**：极小档位×极小预算可能 `< minSz`，`LiveExecutor._do_open` 已有「预算/杠杆过小…已跳过该信号」的 status 兜底，行为不变。

## 三、File / module change plan（文件 / 模块变更计划）

| # | 文件 | 变更意图（一行） |
|---|---|---|
| 1 | `services/quant/app/config.py` | 新增 `ai_trade_default_position_step_pct: float = 0.25`（请求留空时的默认档位；`AI_TRADE_` 前缀 desktop 透传已覆盖，无需改 config.js/白名单） |
| 2 | `services/quant/app/ai_auto/signals.py` | 新增纯函数 `position_ladder(step)`（阶梯唯一真源，**[R-5]**）＋ `quantize_size_fraction(raw, step)`（档位吸附，实盘/回测共用；顶格取自 `position_ladder`） |
| 3 | `services/quant/app/ai_auto/prompts.py` | `build_ai_trade_system_prompt` 增 `position_step_pct` 末参 + 档位纪律段；新增 `build_decision_tool_schema(step)`（sizeFraction enum）；`build_decision_prompt` 可选档位提醒行 |
| 4 | `services/quant/app/ai_auto/decider.py` | 构造增 `position_step_pct=0.0`；`self._tool` 走 `build_decision_tool_schema`；`_map_decision` 开仓分支插入 `quantize_size_fraction` |
| 5 | `services/quant/app/ai_auto/manager.py` | `AiTradeGroupStartRequest.position_step_pct=0.0`；`_build_member` 把 step 传入 decider＋系统 prompt；可选回显于 `group_status`/run config（P2） |
| 6 | `services/quant/app/ai_auto/backtest.py` | **`AiBacktestSpec`**`.position_step_pct`（**[R-1]** 非 `_BacktestSpec`）；runner 构造 decider＋prompt 传 step；`_run_config` 回显 |
| 7 | `services/quant/app/ai_auto/backtest_manager.py` | `AiBacktestStartRequest.position_step_pct=0.0`；`_build_spec` 透传进 `AiBacktestSpec` |
| 8 | `services/quant/app/api/ai_auto.py` | 新增 `_resolve_position_step`（**[R-2]** 仅 `None→settings 默认`，不复夹）；`ai_trade_start`/`ai_backtest_start` 解析 `body.positionStepPct` 后填入 request |
| 9 | `services/quant/app/schemas.py` | `AiTradeGroupStartIn`＋`AiBacktestStartIn` 增 `positionStepPct: float | None = Field(default=None, ge=0, le=1)`；可选：结果/分组行回显 `positionStepPct`；（legacy `AiTradeStartIn` 未被 API 引用，仅为契约镜像可选同步） |
| 10 | `apps/web/lib/aiTrade.ts` | 新增 `AI_TRADE_POSITION_STEP_OPTIONS`（预设档位，**[R-6]** 仅 10/20/25/50/100%，不含连续）＋`AI_TRADE_DEFAULT_POSITION_STEP=0.25`＋`positionLadder(step)`（`position_ladder` 的前端等价镜像，预览文案用，配等值单测 **[R-5]**） |
| 11 | `apps/web/lib/aiTradeTypes.ts` | `AiTradeGroupStartReq.positionStepPct?`；`AiTradeConfig.positionStepPct?`（**[R-7]** 声明为**可选**，与「老配置无此键/可为 null」一致）；可选分组行回显 |
| 12 | `apps/web/lib/aiBacktestTypes.ts` | 回测启动请求类型增 `positionStepPct?`（`aiBacktestApi.start(req)` 为泛型透传，通常无需改 `aiBacktestApi.ts`） |
| 13 | `apps/web/components/Agent/AiTradeLaunchModal.tsx` | 新增「仓位操作档位」`<Select>`（复用已 import 的 `@/components/ui` `Select`，`AiTradeLaunchModal.tsx:5`）+ 本地 state + 预览 Row（`positionLadder`）+ **在 `[open]` 键 `useEffect`（`165-190`）内 seed reset 复位**（**[R-10]** 沿用既有防 `inputs-revert` 回归模式）+ `AiTradeLaunchOpts.positionStepPct` + 写入 `handleConfirm` 的**共享 `base`**（`306-318`，实盘/回测两 tab 同得）+ 提示文案「档位约束开仓规模，非分批加/减仓」 |
| 14 | `apps/web/components/Agent/AiTradeToggle.tsx` | `startM`＋`startBacktestM` 各带 `positionStepPct: opts.positionStepPct`；**[R-4]** 成功回调落盘须**同一次** `setAiTradeConfig({ strategyIds: res.strategyIds, contextRefreshRounds: res.contextRefreshRounds, positionStepPct: opts.positionStepPct })`（整对象替换，`store.ts:789`；`res` 不回显该键，故取 `opts`）；默认从 `aiTradeConfig?.positionStepPct ?? AI_TRADE_DEFAULT_POSITION_STEP` 回填传入弹窗 |
| 15 | `apps/web/lib/store.ts` | persist `version` 27→28（**[R-11]** 防御性，读点已 `?? DEFAULT` 兜底）；`migrate` **[R-3]** **必须 null 守卫**：`if (next.aiTradeConfig) next.aiTradeConfig = { ...next.aiTradeConfig, positionStepPct: next.aiTradeConfig.positionStepPct ?? AI_TRADE_DEFAULT_POSITION_STEP }`（`aiTradeConfig` 常为 `null`，`store.ts:516/1100`）；初始化器 `aiTradeConfig: null` 不变（读点默认兜底） |
| 16 | `services/quant/tests/test_ai_trade_position_step.py`（新）| 量化表驱动 + `_map_decision` 吸附 + manager/backtest 穿参 + api 默认解析 |
| 17 | `apps/web/components/Agent/AiTradeLaunchModal.test.tsx`（扩）| 断言档位控件渲染、确认回调透传 `positionStepPct` |
| 18 | `apps/web/lib/aiTrade.test.ts`（新/扩）| `positionLadder` 与 `AI_TRADE_POSITION_STEP_OPTIONS` 单测 |

## 四、Interface design（接口设计）

### 4.1 REST 请求（camelCase wire）

`POST /api/py/ai-trade/start`（`AiTradeGroupStartIn`）与 `POST /api/py/ai-trade/backtest/start`（`AiBacktestStartIn`）新增可选字段：

```
positionStepPct?: number | null   // [0,1] 档位步长；null/缺省→服务端默认 0.25；0→连续（仅 API/后端退出口，UI 不露出，[R-6]）
```

字段级校验：schema `Field(default=None, ge=0, le=1)`——越界（如 `1.5`/`-1`）在入口即 **422 拒绝**（与既有 `leverage: Field(gt=0, le=125)` 同惯例）。服务端解析：**[R-2]** `_resolve_position_step(raw, settings)` **只做缺省回填**——`raw is None → settings.ai_trade_default_position_step_pct`，否则原样返回（不再重复夹 `[0,1]`，边界已由 schema 保证）。落地档位阶梯 = `position_ladder(step)`；`step==0` 走连续。

### 4.2 决策工具 schema（引导，非强制）

`step>0` 时 `submit_trade_decision.input_schema.properties.sizeFraction = {type:"number", enum: position_ladder(step), description:"开仓仓位占比，只能取列出的档位"}`（**[R-5]** `enum` 取自 `position_ladder` 真源，与 `quantize` 顶格同源）。`step==0` 原样返回 `DECISION_TOOL_SCHEMA`。

### 4.3 后端内部签名

```python
# signals.py
def position_ladder(step: float) -> list[float]: ...          # 阶梯唯一真源 [R-5]
def quantize_size_fraction(raw: float, step: float) -> float: ...
# prompts.py
def build_ai_trade_system_prompt(inst_id, bar, budget_usd, leverage, position_step_pct: float = 0.0) -> str: ...
def build_decision_tool_schema(position_step_pct: float) -> dict: ...
# decider.py
AiTradeDecider(llm, system_prompt, *, context_refresh_rounds, settings, position_step_pct: float = 0.0)
```

### 4.4 前端弹窗控件

`AiTradeLaunchOpts`（`interface`，`AiTradeLaunchModal.tsx:52`）增 `positionStepPct: number`；`<Select>`（复用已 import 的 `@/components/ui` `Select`，与时间级别选择器同款）默认值 `aiTradeConfig?.positionStepPct ?? AI_TRADE_DEFAULT_POSITION_STEP`；预设 `AI_TRADE_POSITION_STEP_OPTIONS` **[R-6]** 仅列 `10% / 20% / 25% / 50% / 100%`（**不含「连续/AI 自由」**——需求明确排斥该行为）；本地 state **必须在 `[open]` 键 `useEffect`（`165-190`）内复位** **[R-10]**（沿用既有防 `inputs-revert` 回归模式）；写入 `handleConfirm` 的共享 `base`（`306-318`）故实盘/回测两 tab 同得；预览 Row 展示 `positionLadder(step)`（如「可用档位：25% / 50% / 75% / 100%」）并附一行「档位约束**开仓规模**，非分批加/减仓；平仓为全平」。

## 五、Data model（数据模型）

- **无 DB 迁移**：档位是**每会话**参数，随请求传入；实盘经 `strategy_runs.config_json`（free-form JSON）追加 `positionStepPct`（可选回显），回测经 `metrics_json`/`config` free-form 落，均**零列迁移**（对齐既有 `stopLossPct` 等 E4 参数的落地方式）。
- **`Settings` 新增一项**：`ai_trade_default_position_step_pct: float = 0.25`；env 名 `AI_TRADE_DEFAULT_POSITION_STEP_PCT`（`AI_TRADE_` 前缀已在 desktop `config.js` prod 透传白名单内，且须真的在 `config.py` 声明——`BaseSettings extra="ignore"` 会静默丢弃未声明 env）。
- **前端持久化偏好**：`AiTradeConfig` 增**可选** `positionStepPct?`（**[R-7]**，`s.aiTradeConfig` 已在 partialize，`store.ts:979`，无需新增 partialize 项）；zustand persist `version` 27→28（**[R-11]** 属**防御性**——`aiTradeConfig` 可为 `null` 且读点已 `?? AI_TRADE_DEFAULT_POSITION_STEP` 兜底，新增可选字段本不强制迁移；保留以合仓库惯例）。**[R-3]** `migrate` 新增块**必须 null 守卫**：`if (next.aiTradeConfig) next.aiTradeConfig = { ...next.aiTradeConfig, positionStepPct: next.aiTradeConfig.positionStepPct ?? AI_TRADE_DEFAULT_POSITION_STEP }`——`aiTradeConfig` 初始值/多版本 migrate 落点均为 `null`（`store.ts:516/1100`），对 `null` 取属性赋值会抛 `TypeError` 致水合崩溃。初始化器 `aiTradeConfig: null` 保持不变（读点默认兜底覆盖全新安装）。
- **决策/事件 in-memory 形状不变**：`decision`/`agent_trace`/`_journal` 复用现有 `sizeFraction` 键，值改为档位化后的数值，无新增线字段（旧前端向前兼容）。

## 六、Testing & acceptance criteria（测试与验收）

**后端（pytest，遵循 [[aragon-quant-test-isolation]]）**：
1. **[R-5]** `position_ladder` 表驱动：`0.1→[.1,.2,…,1.0]`（10 档）、`0.2→[.2,.4,.6,.8,1.0]`、`0.25→[.25,.5,.75,1.0]`、`0.5→[.5,1.0]`、`1.0→[1.0]`、`0→[]`、`0.3→[.3,.6,.9]`（顶格 0.9，佐证非整除边界）。
2. `quantize_size_fraction` 表驱动：`(0.37,0.1)→0.4`、`(0.04,0.1)→0.1`（下限一格不吞）、`(0.93,0.25)→1.0`、`(1.2,0.25)→1.0`（顶格封顶）、`(0.5,0.25)→0.5`（已是档位不变）、`(x,0)→clamp01(x)`（连续直通）；**且断言 `quantize` 输出 ∈ `position_ladder(step)`（顶格同源不变式）**。
3. `_map_decision`：注入 `position_step_pct=0.25`，LLM 报 `sizeFraction=0.37`→`decision.size_fraction==0.25`；报 `0`→`hold("missing_size")`；报极小 `0.04`→吸附 `0.25`（**不**回落 `missing_size`）；`position_step_pct=0` 时值逐字不变（回归门禁）。
4. `manager` `_build_member`：请求带/不带 step 时，成员 `decider._position_step` 与系统 prompt 档位段正确；`AiTradeGroupStartRequest` 直接构造（默认 0）行为不变（既有集成测试零回归）。
5. `backtest`：runner 决策的 `size_fraction` 被吸附；`AiBacktestSpec` 透传、`_run_config` 回显 `positionStepPct`。
6. `api`：`positionStepPct` **缺省（None）→落 `ai_trade_default_position_step_pct`**；**[R-2]** 越界 `1.5`/`-1` → schema 校验 **422 拒绝**（`TestClient` 断言 status_code==422，**非**夹紧）；`0`（连续）显式传入被接受。

**前端（vitest，`NODE_ENV=test`，见 [[aragon-web-vitest-node-env]]）**：
7. `AiTradeLaunchModal.test.tsx`：档位 `<Select>` 渲染且默认值正确（`aiTradeConfig` 缺省时为 `AI_TRADE_DEFAULT_POSITION_STEP`）；`onConfirm` 载荷含 `positionStepPct`（实盘与回测**两条路径**）；重开弹窗 seed reset 后档位回默认（**[R-10]** 防 inputs-revert）。
8. `aiTrade.test.ts`：`positionLadder(0.2)===[0.2,0.4,0.6,0.8,1.0]` 且与后端 `position_ladder` 同表（等值镜像 **[R-5]**）；**[R-6]** `AI_TRADE_POSITION_STEP_OPTIONS` 值集 == `{0.1,0.2,0.25,0.5,1.0}`（**不含 0/连续**）。

**验收标准（Definition of Done）**：
- 弹窗（实盘与回测两 tab）出现「仓位操作档位」选择器，**[R-6]** 可选 10%/20%/25%/50%/100%（**UI 不含连续/AI 自由**）；预览列出对应阶梯；用户所选档位跨会话持久（**[R-4]**）。
- 启动后，AI 每次开仓的 `sizeFraction`（`decision` 事件/审计/真实下单量）**只落在所选档位阶梯上**；方向决策（多/空/平/观望）不受影响。
- `positionStepPct=0`（仅经 API/后端默认，非 UI）时行为与今日逐字一致（连续，回归安全阀）。
- 后端 pytest 全绿、前端 vitest 全绿、`apps/web` tsc 除既存无关 `WeightBatchArena.test.tsx` 报错外无新增错误。
- 无新增 DB 迁移；desktop 打包透传无需改动（`AI_TRADE_` 前缀已覆盖）。

## 七、Risks & mitigations（风险与缓解）

1. **默认档位翻转既有连续行为**：把默认从「AI 自由」改为 0.25 档，会改变通过 **API 路径**启动会话的下单规模。缓解：① 决策器与 `AiTradeGroupStartRequest` dataclass 默认均为 `0`（连续），使**直接构造**的既有后端测试零回归，回归面收敛到少量走 `ai_trade_start` 的 API 测试（就地更新期望或显式传 `positionStepPct=0`）；② 保留 `0=连续` 作为显式退出口；③ 运维可经 `AI_TRADE_DEFAULT_POSITION_STEP_PCT` 调默认。
2. **模型忽略 schema `enum` / prompt**：LLM 层无 `tool_choice` 强制（[[aragon-ai-auto-trading]] P1-5）。缓解：`enum`＋prompt 只是软引导，**服务端 `quantize_size_fraction` 是硬保证**——无论模型给什么，落地一律吸附到档位（belt-and-suspenders）。
3. **误解为「分批加/减仓」**：执行器单向单仓、同向不加仓。缓解：档位定义为「建仓规模」，弹窗提示与文档写明「档位约束开仓规模，非分批加减仓」。
4. **小档位×小预算 < 最小下单量**：`_do_open` 已有跳过兜底。缓解：沿用现有 status 提示，弹窗预算预览已提示可用余额，用户可提高总预算/该标的百分比或选更大档位。
5. **浮点尾差/顶格越界/非整除 step**：`position_ladder` 内 `_STEP_EPS` 容差 + `round(...,6)`；`quantize` 顶格 = `len(ladder)`，与阶梯**同源**（**[R-5]**）。缓解：UI 预设只放能整除 100 的步长（10/20/25/50/100），阶梯干净、顶格恰为 100%。**[R-12]** API 直传非整除 step（如 0.3 → 顶格 0.9，永不满仓）仅经 `[0,1]` 校验、不吸附预设，属可接受边界（UI 不产生此值）；如需后续可加「snap 到最近预设」增强，本期不做。
6. **持久化偏好迁移（含水合崩溃）**：新增 `AiTradeConfig.positionStepPct?` 走 store 迁移。**[R-3]** 关键：`aiTradeConfig` 常为 `null`（`store.ts:516/1100`），`migrate` 新增块**必须 null 守卫**（对 `null` 取属性赋值会抛 `TypeError` 致水合崩溃）。缓解：`version 27→28`（**[R-11]** 防御性）+ null 守卫的 `migrate` 补默认 + 读点 `?? AI_TRADE_DEFAULT_POSITION_STEP` 兜底（三重保险，覆盖全新安装与老用户）。
7. **多标的分组一致性**：分组内各成员 decider 应用**同一** step。缓解：`_build_member` 对每个成员统一传 `req.position_step_pct`（与 E4 单仓 OCO「各成员同值」口径一致）。

---

### 附：下游落地提示（供 Subtask #2 代码开发）
- 单一硬约束点＝`decider._map_decision` 调 `signals.quantize_size_fraction`；`prompts`/`schema enum` 为软引导，二者缺一不可但硬约束优先。
- 阶梯唯一真源＝`signals.position_ladder(step)`；`quantize` 顶格、schema `enum`、prompt 文案、前端 `positionLadder` 全部由它派生（**[R-5]**），禁止各自 `floor(1/step)`。
- 精确暂存注意：本仓工作树含多轮未提交改动（见 [[aragon-ai-trade-budget-cap-relaxation]] §Subtask #3 陷阱），Subtask #3 须 `git add <具体路径>` 只暂存本 feature 文件，严禁 `git add -A`。

---

## 评审结论（Review Verdict · Subtask #1）

**结论：有条件通过（Approved with conditions）。**

方案根因定位准确、核心机制经 `services/quant` 与 `apps/web` 现源码逐条核实**可行且安全**（在 `_map_decision` 单点做档位吸附，不改执行器、不新增敞口面；软引导 + 服务端硬量化 belt-and-suspenders；实盘/回测经共享决策脑单点收口）。6 项 P1 与相关 P2 已**就地修复并标注 `[R-n]`**，无 P0、无遗留未决 P1。

**放行条件**（Subtask #2 代码开发**必须逐条落实**，否则视为未完成 spec）：

1. **[R-1]** 回测数据类用真实类名 **`AiBacktestSpec`**（`backtest.py:65`），**不得**出现 `_BacktestSpec`。
2. **[R-2]** `positionStepPct` schema `Field(default=None, ge=0, le=1)`（越界 **422**）；`_resolve_position_step` **仅** `None→settings 默认`，**不复夹**；对应 API 测试断言 422（非夹紧）。
3. **[R-3]** store `migrate` 新增块**必须 null 守卫** `aiTradeConfig`（其常为 `null`），杜绝水合 `TypeError`。
4. **[R-4]** 偏好落盘在**同一次** `setAiTradeConfig(...)` 整对象替换中带上 `positionStepPct: opts.positionStepPct`（`res` 不回显），确保跨会话持久。
5. **[R-5]** `signals.position_ladder(step)` 为阶梯**唯一真源**；`quantize` 顶格 / schema `enum` / prompt 文案 / 前端 `positionLadder` 全部由它派生并配等值单测。
6. **[R-6]** UI 预设 `AI_TRADE_POSITION_STEP_OPTIONS` **仅** 10/20/25/50/100%，**不暴露连续/AI 自由**；`step=0` 仅保留为后端零回归默认 + API 退出口。

**建议项**（P2，酌情，不阻断）：R-7（`positionStepPct?` 可选）、R-8（prompt 纪律 #3 替换非追加）、R-9（量化插入点固定门禁后）、R-10（弹窗 `[open]` effect 复位 + 「非加/减仓」提示）、R-11（版本 bump 属防御性）、R-12（非整除 API step 边界记录在案）。

**验收口径**：以 §六「测试与验收」为准——后端 pytest（`position_ladder`/`quantize`/`_map_decision`/`manager`/`backtest`/`api` 六组）全绿、前端 vitest（弹窗档位控件 + `aiTrade` 阶梯/选项）全绿、`apps/web` tsc 除既存无关 `WeightBatchArena.test.tsx` 外无新增错误、无新增 DB 迁移、desktop 透传免改。

---

## 实施过程发现的方案缺陷（Issues Found During Implementation · Subtask #2）

> 方案整体准确、可实施，全部 18 项文件按计划落地并测试通过（后端 172 项 / 前端 78 项全绿，tsc 仅余既存无关 `WeightBatchArena.test.tsx` 报错）。仅记录一处方案未言明、实现时必须处理的**模块级循环导入**约束，供 Subtask #3 review 理解为何 `prompts.py`/`decider.py` 采用延迟导入。

- **[I-1] `signals.position_ladder` 单一真源（[R-5]）与既有 `signals → decider` 依赖构成循环导入，须以延迟导入打破。**
  - **现象**：`signals.py` 顶部本就 `from .decider import TradeDecision`（`apply_consensus_gate` 运行期**构造** `TradeDecision`，非仅类型注解，无法降级为 `TYPE_CHECKING`）；`decider.py` 顶部 `from .prompts import ...`。方案要求 `prompts.build_decision_tool_schema` 的 `enum` 与 `prompts` 系统 prompt 档位文案、`decider._map_decision` 的 `quantize` 全部派生自 `signals.position_ladder/quantize_size_fraction`。若在 `prompts.py`/`decider.py` **模块顶层** `from .signals import ...`，则形成 `signals → decider → prompts → signals` 与 `decider → signals → decider` 两条环——加载 `decider` 时 `TradeDecision` 尚未定义即被 `signals` 反向导入，抛 `ImportError`。方案 §2.2/§三 只指定「放 `signals.py`、由其派生」，**未言明**如何破环。
  - **处理（不改设计意图，单一真源不变）**：在 `prompts._size_discipline`/`build_decision_tool_schema` 与 `decider._map_decision` 的**函数体内**延迟导入 `position_ladder`/`quantize_size_fraction`（调用时全部模块已就绪，Python 缓存后开销可忽略）。此为本仓既有惯用法（`manager.py` 同样以函数内延迟导入 `get_live_auto_manager` 破 live-auto 循环），改动最小、零架构影响、契约与真源不变。相关行均加注释标明破环原因。
  - **影响面**：无。真源、量化落点、schema/prompt/前端派生与所有验收标准均按方案达成。
