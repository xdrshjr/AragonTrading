# AI 自动交易「弹窗自动填入自动交易标的 + 可编辑时间级别」开发方案

- **Feature**: `ai-trade-multi-symbol-and-timeframe`
- **文档版本**: v2（v1 由「方案设计」节点产出；本节点「方案评审与修复」补入「评审记录 / 评审结论」，并就地修复全部 P0/P1）
- **所属迭代**: 第 6 轮
- **上游依赖轮次**: 第 1~5 轮已落地 AI 自动交易（Agent 决策者 + 回测模式 + 右侧「AI 助手」执行轨迹分段视图 + 左侧标的化面板 + 侧栏可拖拽调宽）。本轮在其上「继续修改」，**不回滚**任何已完成代码。

---

## 评审记录（Review Notes · v2）

> 评审人：Anthropic 资深工程评审（「方案评审与修复」节点）。维度：**可行性 / 完整性 / 一致性 / 合理规模**。
> 已逐处交叉核验 v1 引用的关键代码路径——`ai_auto/manager.py`、`live_auto/manager.py`、`api/ai_auto.py`、
> `api/live_auto.py`、`schemas.py`、`config.py`、`AiTradeLaunchModal.tsx`、`AiTradeToggle.tsx`、
> `useLiveAutoGroupStream.ts`、`useAiTradeStream.ts`、`store.ts`。核心判断（group-of-sessions 镜像 live-auto
> 分组、`AiTradeSession` 零改动、总预算切分非倍增）**成立且可行**。以下全部 P0/P1 已在下文正文就地修复。

### P0（阻断性 · 已修复）

- **P0-1 · 预算天花板分数必须锚定 AI 侧 5%（`ai_trade_max_capital_frac`），严禁照搬 live-auto 分组的 100% 天花板。**
  v1 §4.1 令 `start_group` 调 `_resolve_budget_group(..., req.budget_cap_frac, req.default_budget_frac)`（镜像 live-auto），
  却**未指明 `budget_cap_frac` 由谁、以何值解析**；同时 §5 指示 `api/ai_auto.py`「镜像 `live_auto.py:134-146`」。而 live-auto
  分组路由用的天花板是 `settings.live_auto_group_max_capital_frac = 1.0`（**100%** 可用余额，见 `config.py:77` /
  `api/live_auto.py:122`）。若开发节点照搬该行，AI 分组总预算将被夹到 `min(maxBudgetPct/100, 100%)`——**最高可达账户可用
  余额的 100%**，较预期 5% 放大 **20×**，直接击穿本方案「总敞口 ≤ `ai_trade_max_capital_frac`（默认 5%）」这一核心安全承诺
  （真实资金）。**修复见 §4.1「预算/敞口安全不变式」、§5 `api/ai_auto.py` 行、§六 6.1、§九**：`api/ai_auto.py` 显式
  `cap_frac = min(at["maxBudgetPct"]/100, settings.ai_trade_max_capital_frac)`，`default_frac = settings.ai_trade_default_capital_frac`，
  二者一同填入请求；**绝不引用** `live_auto_group_max_capital_frac`，且**不新增** `ai_trade_group_max_capital_frac`。

### P1（重要 · 已修复）

- **P1-1 · 留空预算的默认分数须保持 1%（`ai_trade_default_capital_frac`），以维持单会话等价与弹窗「×1%」文案。**
  live-auto 分组令 `default_budget_frac = cap_frac`（留空=打满上限）；若 AI 照搬，则「留空预算」默认从现状 **1%**（单会话）
  跳到 **5%**（上限）——(a) 与弹窗现有文案「默认预算（可用余额 × 1%）」(`AiTradeLaunchModal.tsx:340`) 直接矛盾；
  (b) 破坏 §8.1 `single_member_group_equivalent` 回归与 AC-7；(c) 对「留空即启动」的用户构成 **5× 静默加仓**（真实资金意外）。
  **修复见 §4.1 / §8.1**：`default_frac = ai_trade_default_capital_frac`（保持 1%）；等价断言限定在「显式给定 `capitalUsd`」，
  并追加「留空默认=1%」独立断言。

- **P1-2 · 弹窗自动填入须与 `ai_trade_max_symbols` 上限、`allocationMode` 语义对齐，避免「回填即提交即 400」。**
  v1 §4.2 令弹窗把 `autoTradeMembers` 原样回填，但：(a) `autoTradeMembers` 受 `live_auto_max_group_symbols = 8`
  约束、可达 8 个，而 AI 侧 `ai_trade_max_symbols = 5`——6~8 成员用户回填后一提交即撞后端 400；(b) `autoTradeMembers`
  的 allocation 可能是在 `max_ratio` 模式下维护的（Σ 可 >100），若弹窗 `allocationMode` 写死 `"even"`，后端 even 模式
  Σ≤100 校验立即 400。这两点都会让**本轮头号需求（自动填入并交易）在常见配置下直接失败**。**修复见 §4.2 / §5 弹窗行 / §8.2 / AC-9**：
  回填至多 `ai_trade_max_symbols` 个（新增前端常量 `AI_TRADE_MAX_SYMBOLS`，超出者提示「已达 AI 自动交易标的上限 N」并禁止再加）；
  `allocationMode` 默认从 `store.autoTradeAllocationMode` 播种。

- **P1-3 · 逐成员预检失败码须与既有实现对齐，`AI_TRADE_MEMBER_BUDGET_TOO_SMALL` 属本轮新增（非「沿用既有」）。**
  v1 §4.1 称预检码「沿用既有 `AI_TRADE_INSTRUMENT_UNSUPPORTED` / `AI_TRADE_PREEXISTING_POSITION` /
  `AI_TRADE_MEMBER_BUDGET_TOO_SMALL`」。实测：既有「标的不支持」码是 `LIVE_INSTRUMENT_UNSUPPORTED`（`ai_auto/manager.py:205`，
  live-auto 成员预检亦然 `live_auto/manager.py:256`），**并非** `AI_TRADE_INSTRUMENT_UNSUPPORTED`；且单会话 AI **从无**预算过小
  校验（`_resolve_budget` 只夹取），`AI_TRADE_MEMBER_BUDGET_TOO_SMALL` 实为**本轮新增**，须镜像 live-auto `_budget_too_small`
  （`snap_size(raw, lot_sz) < min_sz`，依赖 config `lastPx`）落地——否则「总预算 1% 切 N 份」下极易出现下不出单又不报错的死角。
  **修复见 §4.1 / §六 6.1**：更正码值与「新增」定性，补 `_budget_too_small` 实现说明。

### P2（建议 · 已就地澄清，残留列入评审结论条件）

- **P2-1** 回测段仍单标的，其 `instId` 播种来源未定——§4.2 已定为 `members[0]`（空则当前图表标的）。
- **P2-2** 分组下弹窗预算预览的 `availUsdt` 取数口径（账户级、取任一成员 config）与「总预算」语义须明示——§4.2 已补。
- **P2-3** 「总止盈损 USD 按 `pct/Σpct` 分摊」与「单仓 OCO pct 各成员同值」是两套机制，v1 §4.1 措辞易混——已就地区分。
- **P2-4** `status()`→`group_status()` 后须确保 `stop_group`/`stop_member` 在无运行成员时清 `self._group`（`AiTradeSession`
  仅经 `stop()` 离开 `"running"`、不自终止），使互斥反查不误报僵尸分组——§4.1 已明确并加回归 `single_member_group_equivalent`。
- **P2-5** 成员 run 是否落 live-auto 式专用索引列（`group_id`/`group_started_ts`/`group_total_budget_usd`）由开发节点定夺：
  AI 无分组历史聚合功能，仅入 `config_json` 即可（零迁移）；`strategy_runs` 已含该三列，如顺带回填亦无害。

---

## 一、Overview（概述）

前五轮把「AI 自动交易」做成了一条完整的新交易形态：右侧「AI 助手」头部的 `AiTradeToggle` 开关弹出 `AiTradeLaunchModal`，用户在弹窗里多选策略、填预算/杠杆/总止盈损/单仓 OCO/上下文刷新轮数，后端 `ai_auto/` 以 Agent LLM 为决策者，对**单个标的**逐根确认 K 线产出决策并经既有 `LiveExecutor` 下真实单；右侧面板逐轮呈现思考/分析/工具调用/结果轨迹。但直到目前，AI 自动交易始终是**单标的、单活跃会话**：`AiTradeManager` 只持有一个 `self._session`（`services/quant/app/ai_auto/manager.py:80-84`），启动弹窗只有一个自由文本 `instId` 输入框（`apps/web/components/Agent/AiTradeLaunchModal.tsx:307-316`），且时间级别 `bar` 只读展示、不可修改（`AiTradeLaunchModal.tsx:327` / `:355`）。

本轮（第 6 轮）要做两件相互独立、都收敛在既有约定内的改动：**(1) 多标的自动填入并交易**——点击「启动 AI 自动交易」时，弹窗要**自动填入左侧「自动交易」面板上半区**已经维护好的那一组「需要自动交易的标的」（即持久化偏好 `autoTradeMembers`，`apps/web/lib/store.ts:225`，成员形如 `{instId, allocationPct}`），并让 AI 对**这一整组标的**同时进行自动交易；**(2) 可编辑时间级别**——弹窗里显示当前 K 线所选周期（现已由 `store.bar` 透传，只是只读），要把它改成**可编辑下拉**（默认回填 K 线所选周期，用户可改），并让修改后的周期真正生效到启动请求。

本轮的核心工程判断是：**多标的 AI 自动交易应当复刻业已验证的「策略式 live-auto 分组」范式**——`AiTradeManager` 升级为「分组」注册表，一个分组内每个标的各自是一个**保持不变的 `AiTradeSession`**（各有独立的 decider / executor / MarketWindow / WS / 轨迹），总预算按成员 `allocationPct` **切分**（而非按标的**倍增**），从而**总账户敞口仍受 `ai_trade_max_capital_frac` 单一上限约束**——这恰好化解了历史上把「多标的 AI 交易」搁置的唯一顾虑（敞口倍增）。后端因此只改 `manager.py / schemas.py / api/ai_auto.py / config.py` 四处，**`session.py / decider.py / signals.py / trace.py / market_context.py / market_window.py / backtest.py / prompts.py 全部零改动**；前端把单会话运行期态泛化为分组态（镜像既有 `liveAutoGroup`），并把启动弹窗改造为多标的选择 + 可编辑周期。回测模式仍为单标的（回测 runner 单标的，本轮不扩），仅同享「可编辑周期」这一改动。

---

## 二、背景与现状（关键代码路径 · 已交叉核验）

### 2.1 后端 AI 自动交易＝严格单标的、单活跃会话

- 注册表：`AiTradeManager.__init__` 仅持 `self._session: AiTradeSession | None`（`ai_auto/manager.py:80-84`）。
- 启动聚合体 `AiTradeStartRequest` 的 `inst_id: str` / `bar: str` 皆为标量（`manager.py:60-77`）；`start()`（`manager.py:87-108`）对**单个** `req.inst_id` 依次跑 `fetch_trading_config` → `_require_instrument` → `fetch_live_positions` → `_require_no_position` → `_resolve_budget` → `_build_session`。
- 硬上限＝1 且与 live-auto 互斥：`_require_capacity`（`manager.py:177-185`）在 `self._session` 运行时抛 `AI_TRADE_SESSION_LIMIT`，并 `from ..live_auto.manager import get_live_auto_manager; if get_live_auto_manager().group_status() is not None: raise ...`。整个检查在 `async with get_admission_lock()` 内做到 check-and-register 原子（P1-4）。
- 会话构建（`_build_session` `manager.py:110-123`）：一份 `LiveExecutor`、一份 `AiTradeDecider`、一份把 `inst_id/bar` 烘进系统提示的 prompt、一条 `_create_run(mode="ai-trade")`（`manager.py:258-272`）。
- `stop/status/get/shutdown`（`manager.py:139-167`）全部围绕单 `self._session`；`status()` 返回**单个 dict 或 None**（`session.info()`，`session.py:624-637`）。
- 单例 `get_ai_trade_manager()`（`manager.py:275-282`）。
- **重要**：`AiTradeSession`（`session.py`，629 行，含 decider/consensus/trace/OCO/monitor 全套）已高度复杂但**功能自洽**，把它当作「分组成员」原样复用是本方案最大的降险点。

### 2.2 已验证的「多标的 live-auto 分组」范式（本轮直接镜像）

- 分组注册表：`LiveAutoManager` 持 `self._group: LiveAutoGroup | None` + `self._start_lock`（`live_auto/manager.py:161-164`），单活跃分组独占。
- `start_group(req)`（`manager.py:167-186`）两阶段全成/全败：`async with get_admission_lock(), self._start_lock:` → `_require_capacity()` → `_validate_members()` → `_gather_context()` → `_resolve_budget_group()` → `_preflight_group()` → `_build_group()` → `_launch_group_or_rollback()`。
- 成员校验 `_validate_members`（`manager.py:188-211`）：非空、按 `inst_id` 去重、`≤ settings.live_auto_max_group_symbols`、每个 `allocation_pct ∈ (0,100]`、`even` 模式 Σ≤100。
- 预算：总额一次解析 `_resolve_budget_group`（`manager.py:482-492`，`min(capital or avail×default_frac, avail×cap_frac)`），每成员 `budget_i = total × allocation_pct / 100`（`manager.py:242`），总止盈损按 `pct/Σpct` 分摊 `_scale_threshold`（`manager.py:494-499`）。
- 逐成员预检 `_preflight_group`（`manager.py:235-250`）返回 `{instId, code, message}` 失败明细，任一失败抛 `LiveAutoGroupPreflightError(failures)`（整组拒绝、带 per-symbol 明细）。
- 只读视图 `group_status()`（`manager.py:364-373`）+ `_member_row`（`manager.py:375-384`）。停止 `stop_group`/`stop_member`（`manager.py:330-354`），`get(session_id)` 扫成员（`manager.py:386-391`）。
- 互斥：`_require_capacity`（`manager.py:463-475`）在锁内检查本侧分组 + `get_ai_trade_manager().status() is not None`（懒导入避免环）。
- **WS 为 per-session**：仅 `/ws/live-auto/{session_id}`（`api/live_auto.py:198-219`），**无分组多路 WS**；前端对每个成员各开一条。分组路由由 `group_status()` 提供以便刷新后重连。
- 分组路由 `POST /live-auto/group/start`、`/live-auto/group/{group_id}/stop`、`/live-auto/group/{group_id}/member/{inst_id}/stop`、`GET /live-auto/group/status`（`api/live_auto.py:108-171`），**均返回手搓 dict、未设 response_model**（schema 仅作文档/TS 对齐镜像）。
- 前端：`liveAutoGroupApi`（`lib/liveAutoGroupApi.ts:40-58`，复用 per-session `liveAutoWS`）、`useLiveAutoGroupStream`（`lib/useLiveAutoGroupStream.ts`）——`groupStateFromStatus`（:14-45）把 `LiveAutoGroupRow` 归约成 `LiveAutoGroupState.members: Record<instId, member>`；刷新重发现（:57-72）；稳定订阅键 `memberKey`（:75-80）仅在成员集变化时重订；扇出（:82-107）每成员各开一条 `liveAutoWS`；`routeMemberEvent`（:118-138）按 instId 归入成员槽。

### 2.3 前端「自动交易面板上半区」＝ `autoTradeMembers`

- `store.autoTradeMembers: AutoTradeMember[]`（`store.ts:225`，默认 `[]` `:478`），成员 `AutoTradeMember = { instId: string; allocationPct: number }`（`lib/liveAutoTypes.ts:117-120`）。**持久化**（partialize `store.ts:919`、迁移默认 `:1022`）。
- `WatchlistTab` 读 `autoTradeMembers`（`:76`）→ `memberViews`（`:115-130`）→ 传给 `AutoTradePanel`（`:270-297`，`members` prop）。`AutoTradePanel` 顶部即渲染这组「需要自动交易的标的」（`AutoTradePanel.tsx:181-199`）。
- 分配模式 `autoTradeAllocationMode: "even" | "max_ratio"`（`store.ts:228`，持久化 `:920`）。

### 2.4 前端启动弹窗与时间级别现状

- `AiTradeLaunchModal`：单 `instId` 文本框（`:99` state、`:307-316` input）；`bar` 为只读 prop，仅在信息行 `Row label="周期"` 展示（`:327` 实盘、`:355` 回测），**无 setBar、无下拉**。回测决策数学用到 `bar`（`:166` / `:181`）。
- `AiTradeToggle`：`defaultInstId={instId}`（`:216`，来自 `store.instId`）、`bar={bar}`（`:215`，来自 `store.bar` `:27`）。**两条启动 mutation 都无视弹窗、直接用 store `bar`**：`aiTradeApi.start({instId: opts.instId, bar, ...})`（`:80-91`）、`aiBacktestApi.start({instId: opts.instId, bar, ...})`（`:127-144`）。成功后写单会话（`:94-116`）。
- 时间级别候选＝ `["1m","5m","15m","30m","1H","4H","1D"]`（`ChartToolbar.tsx:9`），即 `Bar` 联合类型（`lib/types.ts:1`）；`setBar`（`store.ts:531`）。
- AI 运行期态：`aiTradeSessionId`/`aiTradeSession`（瞬态，`store.ts:232-233`）、`aiTradeConfig`（持久化，`store.ts:240`，形如 `{strategyIds, contextRefreshRounds}` `aiTradeTypes.ts:211-214`）。`useAiTradeStream`（`lib/useAiTradeStream.ts`）为单会话：重发现（:34-50）、单 WS 订阅（:53-81）、`routeEvent`（:96-134）。

---

## 三、架构决策（为何 group-of-sessions，而非 N-symbols-per-session）

多标的有两条实现路径：

1. **分组内 N 个独立 `AiTradeSession`（本方案）**：`AiTradeManager` 升级为分组注册表，每成员＝一个原样复用的 `AiTradeSession`。优点：`session.py` 及其下游 decider/consensus/trace/backtest **零改动**（最大降险）；直接复刻已上线、已被验证的 live-auto 分组骨架（预算切分、逐成员预检、per-session WS、刷新重发现、互斥准入锁）；总预算切分保证敞口不倍增。缺点：N 个 decider 各自每决策轮各一次 LLM 调用（成本随标的数线性上升）——以 `ai_trade_max_symbols` 上限 + 各会话既有上下文刷新/摘要压缩来约束。
2. **单会话内挂 N 个标的**：需把 `session.py` 的单 `MarketWindow`/单 `LiveExecutor`/单 `OKXWSClient`/单决策管线全部改成 N 份，深度侵入 629 行的复杂会话并会牵动 trace/consensus/backtest。风险与工作量都远大于路径 1，且无收益。

**结论：采用路径 1。** 安全语义（预算切分、逐成员 OCO、单笔名义上限、`_require_no_position`、双闸、互斥）与 live-auto 分组等价；LLM 成本以上限 + 摘要压缩约束。

---

## 四、Technical design（技术设计）

### 4.1 需求(1)·后端：AI 自动交易「分组」

**注册表与数据类（镜像 live-auto，置于 `ai_auto/manager.py`）：**

- `AiTradeGroupMemberInput`（≈`GroupMemberInput` `live_auto/manager.py:73-78`）：`inst_id: str`、`allocation_pct: float`。
- `AiTradeGroupStartRequest`（聚合启动参数，守 ≤5 形参约定）：`members_in: list[AiTradeGroupMemberInput]`、`specs: list[PaperStrategySpec]`、`bar: str`、`account_row`、`settings`、`context_refresh_rounds: int`、`allocation_mode: str = "even"`、`total_capital_usd/leverage/take_profit_usd/stop_loss_usd/stop_loss_pct/take_profit_pct: … | None`、`budget_cap_frac/default_budget_frac: float`、`priming/priming_bars`。
- `AiTradeGroupMember`：`inst_id`、`allocation_pct`、`budget_usd`、`session: AiTradeSession`、`run_id`。
- `AiTradeGroup`：`group_id`、`bar`、`strategy_ids`、`strategy_names`、`total_budget_usd`、`leverage`、`context_refresh_rounds`、`created_ts`、`members: list[AiTradeGroupMember]`。
- `AiTradeManager`：`self._session` → `self._group: AiTradeGroup | None` + 新增 `self._start_lock = asyncio.Lock()`。

**`start_group(req)`（新，取代 `start`，两阶段全成/全败，镜像 `live_auto/manager.py:167-186`）：**

```
self._require_enabled(req.settings)                       # 双闸（沿用 _require_enabled 170-175 语义）
provider = await resolve_active_provider(...)             # Agent 已配置，否则 AGENT_NOT_CONFIGURED
self._validate_strategies(req)                            # 沿用 187-192（1..ai_trade_max_strategies）
self._validate_context_rounds(req)                        # 沿用 194-200
async with get_admission_lock(), self._start_lock:
    self._require_capacity()                              # 本侧分组运行中 or live-auto 分组活跃 → 409
    members = self._validate_members(req.members_in, req.settings, req.allocation_mode)
    creds = _creds(req.account_row)
    avail, positions, configs = await self._gather_context(creds, req, members)  # 逐成员 config + 全账户持仓一次
    total = self._resolve_budget_group(req.total_capital_usd, avail, req.budget_cap_frac, req.default_budget_frac)
        # ↑ req.budget_cap_frac / req.default_budget_frac 由 api/ai_auto.py 预解析（P0-1/P1-1，见下「预算/敞口安全不变式」）
    lever = req.leverage or req.settings.live_default_leverage
    plans = self._preflight_group(members, configs, positions, total, lever)     # 逐成员 instrument + no-position
    provider_client_factory = ...                          # provider 已解析，逐成员各 make_llm_client_from_config
    group = await self._build_group(req, creds, provider, plans, total, lever)
    self._group = group
    return await self._launch_group_or_rollback(group)
```

- `_validate_members`（镜像 188-211）：非空、按 `inst_id` 去重、`≤ settings.ai_trade_max_symbols`（**新配置**）、`allocation_pct ∈ (0,100]`、`even` 模式 Σ≤100（`max_ratio` 跳过 Σ 检查）。
- `_gather_context`（镜像 213-233）：逐成员 `fetch_trading_config`，`fetch_live_positions` 一次取全账户，`asyncio.Semaphore(settings.live_auto_start_concurrency)` 限并发（复用同一并发配置，避免多标的启动触发 OKX 429）。`avail` 取首个 config 的 `availUsdt`。
- `_preflight_group`（镜像 235-264）：逐成员 `_require_instrument`（USDT 永续/交割，沿用 `manager.py:202-206` 判据）+ `_require_no_position`（该标的无持仓，沿用 `208-214`）+ **新增** `_budget_too_small`（镜像 live-auto `manager.py:266-277`：`snap_size(budget_i×lever / (ctVal×lastPx), lotSz) < minSz`，行情缺失则不拦、运行期再守），`budget_i = total × pct/100`；任一失败 → `AiTradeGroupPreflightError(failures)`（`failures=[{instId,code,message}]`）。**失败码精确对齐既有实现（P1-3，勿臆造）**：
  - 标的不支持 → `LIVE_INSTRUMENT_UNSUPPORTED`（既有码，`manager.py:205`，**非** `AI_TRADE_INSTRUMENT_UNSUPPORTED`）；
  - 该标的已有持仓 → `AI_TRADE_PREEXISTING_POSITION`（既有码，`manager.py:213`）；
  - 分配预算不足最小下单量 → `AI_TRADE_MEMBER_BUDGET_TOO_SMALL`（**本轮新增码**——单会话 AI 原无此校验，因总预算切 N 份后每份可能过小，必须新增，语义镜像 live-auto `LIVE_AUTO_MEMBER_BUDGET_TOO_SMALL`）。
- `_build_member`（镜像 294-309）：对每成员构建**一个原样 `AiTradeSession`**——`executor = self._build_executor(creds, member.inst_id, member.budget_usd, lever, sl_pct, tp_pct)`、`decider = AiTradeDecider(make_llm_client_from_config(provider), build_ai_trade_system_prompt(member.inst_id, req.bar, member.budget_usd, lever), context_refresh_rounds, settings)`、`run_id = await self._create_run(...)`（每标的一条 run，`config` 追加 `groupId/allocationPct/allocationMode/totalBudgetUsd`）、`AiTradeSession(f"aitrade_{uuid4}", run_id, req.specs, member.inst_id, req.bar, executor, decider, take_profit_usd=…, stop_loss_usd=…, priming, priming_bars, context_refresh_rounds)`。**`AiTradeSession` 构造签名不变。**
  - **两套风控须区分（P2-3，勿混）**：① **会话级止盈损 USD**（`take_profit_usd`/`stop_loss_usd`）是**分组总额按 `pct/Σpct` 分摊**到各成员（镜像 live-auto `_scale_threshold` `manager.py:494-499`），各成员阈值之和恒等于总额；② **单仓交易所侧 OCO pct**（`stop_loss_pct`/`take_profit_pct`）经 `_resolve_trade_risk` 夹紧后**各成员同值**（非分摊，来自 `req.stop_loss_pct/take_profit_pct`，留空回落 config 默认）。`_build_member` 建 executor 时传前者的分摊值与后者的同值。
- `_launch_group_or_rollback`（镜像 311-327）：逐成员 `session.start()`；任一异常 → 停掉所有已启成员 + `self._group = None` + 原样抛出。

**分组只读 / 生命周期：**

- `group_status()`（镜像 364-384）→ `{groupId, bar, strategyIds, strategyNames, totalBudgetUsd, leverage, contextRefreshRounds, createdTs, members:[member_row...]}`；`_member_row` 复用 `member.session.info()` 并注入 `allocationPct`（`{**session.info(), "allocationPct": member.allocation_pct}`）。
- `status()` → **改为 `return self.group_status()`**（dict 或 None）。**关键**：这样 `live_auto/manager.py:472` 的 `if get_ai_trade_manager().status() is not None:` 无需任何改动即可正确检测活跃 AI 分组 → **live_auto 侧零改动**。**互斥正确性前提（P2-4）**：单会话时代 `status()` 靠 `session.state != "running"` 兜底（因 `stop()` **不清** `self._session`）；分组时代必须由 `stop_group`/`stop_member` 在无运行成员时**清 `self._group`** 来保证 `group_status() is not None` ⟺ 存在活跃成员。因 `AiTradeSession` 仅经 `stop()` 离开 `"running"`（`session.py:190`，不自终止），只要各停止路径都清空即无僵尸分组长期阻塞 live-auto 的风险。
- `stop_group(group_id, reason="user_stop")`（镜像 330-341）：停所有成员，聚合 `{instId, sessionId, runId, flattened}`，返回 `{groupId, stopReason, members}`，清 `self._group`。
- `stop_member(group_id, inst_id, reason)`（镜像 343-354）：停单成员；无运行成员时清分组。
- `get(session_id)`（镜像 386-391）：扫成员返回对应 `AiTradeSession`（供 WS 路由）。
- `shutdown()`：停所有运行成员、清 `self._group`。
- `_require_capacity`（改造 177-185）：本侧 `self._group` 有运行成员 → 409；`get_live_auto_manager().group_status() is not None` → 409（沿用）。

**预算/敞口安全不变式（P0-1 / P1-1，须显式声明且不可偏离）**：

- **天花板分数解析口径（P0-1，安全关键）**：`api/ai_auto.py` 分组启动路由**必须**在服务端读 `get_auto_trade_settings` 后计算
  `cap_frac = min(at["maxBudgetPct"]/100, settings.ai_trade_max_capital_frac)`（即 `min(用户配置上限, 5%)`），并作为 `budget_cap_frac` 填入请求。
  **严禁**照搬 live-auto 分组路由的 `settings.live_auto_group_max_capital_frac`——该值为 **1.0（100%）**（`config.py:77`），若误用会把 AI 分组总预算
  抬到最高 100% 可用余额（较预期 5% 放大 20×，真实资金灾难）。本方案**不新增** `ai_trade_group_max_capital_frac`：AI 分组沿用与单会话同一把 5% 尺子。
- **留空默认分数（P1-1）**：`default_budget_frac = settings.ai_trade_default_capital_frac`（**保持 1%**，非 live-auto 分组的「留空=打满上限」语义）。
  这样「留空预算」在分组时代仍默认 `avail×1%`，与单会话完全一致，弹窗「默认预算（可用余额 × 1%）」文案继续成立，`single_member_group_equivalent` 回归可通过。
- **总预算夹取与切分**：`total = _resolve_budget_group(capital, avail, cap_frac, default_frac) = min(capital or avail×default_frac, avail×cap_frac)`，与单会话同一夹取；
  成员预算按 `pct` **切分**（`budget_i = total×pct/100`，非倍增）。故**开启 N 个标的的 AI 交易，总账户敞口仍 ≤ `ai_trade_max_capital_frac`（默认 5%）**，
  与单标的时代等价；每标的另受 `LIVE_MAX_ORDER_NOTIONAL_USD`（≤1000/单）与单仓 OCO 约束。

### 4.2 需求(1)·前端：弹窗自动填入 + 分组运行期态

**启动请求走「分组」：** `AiTradeLaunchModal` 的 `instId` 单文本框 → **多标的选择器**：默认从 `autoTradeMembers` 回填（含各自 `allocationPct`）；若 `autoTradeMembers` 为空则回退为「当前图表标的」单成员（100%）。UI 复刻策略式 `AutoTradeGroupConfirmModal` 的成员分配片段（每成员一行：标的 + allocation% 输入 + 移除；底部「均分」按钮 + 分配模式 even/max_ratio）。仅 `isAutoTradable` 的 USDT 永续/交割可入选。

**自动填入的保真约束（P1-2，直接决定头号需求能否跑通）：**

- **标的数封顶**：`autoTradeMembers` 受 `live_auto_max_group_symbols = 8` 约束、可达 8 个，而 AI 侧上限为 `ai_trade_max_symbols = 5`。回填时**至多取前 `ai_trade_max_symbols` 个**（新增前端常量 `AI_TRADE_MAX_SYMBOLS`，镜像既有 `AI_TRADE_MAX_STRATEGIES` 置于 `lib/aiTrade.ts`，值须与后端 `ai_trade_max_symbols` 对齐），并在弹窗内以浅提示说明「已达 AI 自动交易标的上限 N，仅前 N 个入选（可手动增删）」；成员数达上限时禁用「新增标的」。后端 `_validate_members` 仍作硬闸（>N → 400），前后端双保险。
- **分配模式播种**：`allocationMode` 段控默认从 `store.autoTradeAllocationMode` 播种（**不写死 `"even"`**）。否则当用户在左侧以 `max_ratio` 维护了 Σ>100 的分配、而弹窗默认 `even` 时，后端 even 模式 Σ≤100 校验会立即 400。
- **预算预览取数口径（P2-2）**：分组下「可用余额 / 默认预算 / 生效预算」预览的 `availUsdt` 为**账户级**（对所有成员相同），取任一成员（如 `members[0]`）的 `tradingApi.config` 即可；「总预算」输入代表分组**总额**（回填后按各成员 `pct` 切分展示），弹窗「默认预算（可用余额 × 1%）/ 已按 5% 上限夹紧」文案沿用不变（与 P1-1 保持一致）。
- **回测段仍单标的（P2-1）**：回测模式沿用单 `instId` 输入，其默认值取 `members[0].instId`（`autoTradeMembers` 空则当前图表标的），与实盘多标的选择器互不影响。

**运行期态泛化（镜像 `liveAutoGroup`）：** 新增瞬态 store 切片 `aiTradeGroup: AiTradeGroupState | null`，成员 `Record<instId, AiTradeGroupMemberState>`。`useAiTradeStream` → `useAiTradeGroupStream`（镜像 `useLiveAutoGroupStream`）：`GET /ai-trade/status` 重发现分组 → 每成员各开一条 `aiTradeWS(sessionId)` → 事件按 instId 归入 `aiTradeGroup.members[instId]`（snapshot/status/trade/position/decision/summary/agent_trace 各槽）。

**左侧 `AiTradePanel` 泛化为「多标的」：** 分组头（`AI 自动交易`＋运行徽标＋分组累计 PnL＝Σ 成员 `sessionPnlUsd`）＋**逐成员标的行**（每行 `instId·bar`＋持仓徽标＋成员预算/allocation 副标＋可点击 `setInst` 切图）。单成员时视觉与第 5 轮单会话面板等价。

**右侧 `AiTradeAgentTrace` 加标的切换：** 顶部加一个**标的段控/下拉**（分组成员集），默认聚焦「当前图表标的（若在组内）否则首成员」；展示 `aiTradeGroup.members[focused].traces` 逐轮时间线。各成员轨迹独立留存于其成员槽，切换不丢历史。

### 4.3 需求(2)：可编辑时间级别

- `AiTradeLaunchModal` 新增本地状态 `const [bar, setBar] = useState<Bar>(propBar)`（`propBar` 仍由 `store.bar` 透传，每次打开回填），并把只读 `Row label="周期"` 换成 `<select>`（选项＝ `BARS`，`ChartToolbar.tsx:9`）。回测决策数学（`defaultDecisionEvery/planRounds`）改用本地 `bar`。
- `AiTradeLaunchOpts` 增 `bar: string`；`handleConfirm`（`:200-225`）把 `bar` 并入 `base`。
- `AiTradeToggle` 两条 mutation 改用 `opts.bar` 取代直接读 `store.bar`（`:81`、`:132`）——**这是「支持用户修改」真正生效的关键接线**：分组每成员会话都用该 `bar`；回测 job 也用该 `bar`。
- 分组的 `bar` 为整组共享（与 live-auto 分组同，`LiveAutoGroupStartIn.bar` 单值）。

### 4.4 关键时序（实盘多标的）

```
用户在 AutoTradePanel 上半区维护好 autoTradeMembers（BTC 60% / ETH 40%）
用户点右侧「AI 自动交易」开关 → openBlockedReason 通过（Agent 已配置）→ 弹窗打开
  弹窗自动填入：标的行 [BTC 60%, ETH 40%]（来自 autoTradeMembers）
                周期 <select> 默认＝store.bar（如 1H），可改
用户（可改分配/周期/预算/策略/OCO/轮数）→ 勾选真实资金 → 「确认并开始」
  → onConfirm(opts{ mode:live, members, bar, allocationMode, ... })
  → aiTradeApi.start(groupReq) → POST /ai-trade/start
      后端 start_group：准入锁 → _require_capacity → _validate_members(≤ai_trade_max_symbols)
        → _gather_context（逐成员 config + 持仓）→ 总预算夹取 → 逐成员预检（instrument+no-position）
        → 每成员建一个 AiTradeSession（budget=total×pct/100；会话止盈损 USD 按 pct/Σpct 分摊、单仓 OCO pct 各成员同值）→ 全启/回滚
      → 返回 {groupId, bar, members:[{instId,sessionId,budgetUsd,allocationPct,...}]}
  → setAiTradeGroup(groupStateFromStatus)；useAiTradeGroupStream 每成员各开一条 /ws/ai-trade/{sessionId}
  → 左侧 AiTradePanel 列出 BTC/ETH 两行；右侧执行轨迹按聚焦标的展示逐轮思考
停止：开关关闭 → aiTradeApi.stopGroup(groupId) → POST /ai-trade/{groupId}/stop → 平掉各成员自管持仓
```

---

## 五、File / module change plan（文件 / 模块变更计划）

| 文件 | 变更 | 意图（一句话） |
|---|---|---|
| `services/quant/app/ai_auto/manager.py` | 改造 | `self._session`→`self._group`；新增 `AiTradeGroup*` 数据类 + `start_group/stop_group/stop_member/group_status/_validate_members/_gather_context/_resolve_budget_group/_preflight_group/_build_group/_build_member/_launch_group_or_rollback`（镜像 live-auto 分组）；`status()` 返回 `group_status()`；`get()` 扫成员；新增 `AiTradeGroupPreflightError`。逐成员复用**不变的** `AiTradeSession`。 |
| `services/quant/app/schemas.py` | 修改 | 新增 `AiTradeGroupMemberIn / AiTradeGroupStartIn / AiTradeGroupMemberRow(继承 AiTradeSessionRow + allocationPct) / AiTradeGroupRow / AiTradeGroupStartOut`；`AiTradeStatusOut.session` → `group: AiTradeGroupRow | None`。保留 `AiTradeSessionRow` 供成员行复用。 |
| `services/quant/app/api/ai_auto.py` | 修改 | `POST /ai-trade/start` 收 `AiTradeGroupStartIn`→构 `AiTradeGroupStartRequest`→`start_group`→返回手搓分组 dict（含 per-symbol preflight 409 明细，路由结构镜像 `live_auto.py:134-146`）；**但预算天花板分数须 AI 侧自解析（P0-1）**：`at = await repo.get_auto_trade_settings(session)`→`cap_frac = min(at["maxBudgetPct"]/100, settings.ai_trade_max_capital_frac)`、`default_frac = settings.ai_trade_default_capital_frac`（**不引用** `live_auto_group_max_capital_frac`）→ 填入 `AiTradeGroupStartRequest.budget_cap_frac/default_budget_frac`；`stop` 路由改 `POST /ai-trade/{group_id}/stop`＋新增 `POST /ai-trade/{group_id}/member/{inst_id}/stop`；`GET /ai-trade/status` 返回 `{group, aiTradeEnabled}`；WS `/ws/ai-trade/{session_id}` **不变**。 |
| `services/quant/app/config.py` | 修改 | 新增 `ai_trade_max_symbols: int = Field(default=5, ge=1)`（分组标的数上限；**刻意 < `live_auto_max_group_symbols=8`**，因 N 个 decider 每轮各一次 LLM 调用，成本线性上升）。**不新增** `ai_trade_group_max_capital_frac`：AI 分组沿用 `ai_trade_max_capital_frac=0.05` 单一天花板（P0-1）。预算 frac 语义澄清（总额切分、非倍增，注释）。 |
| `apps/web/lib/aiTradeTypes.ts` | 修改 | 新增 `AiTradeGroupMemberInput / AiTradeGroupStartReq / AiTradeGroupMemberRow / AiTradeGroupRow / AiTradeGroupStartResult / AiTradeGroupStatusResponse / AiTradeGroupStopResult / AiTradeGroupMemberState / AiTradeGroupState`；保留既有单会话类型（回测/兼容仍用）。 |
| `apps/web/lib/aiTradeApi.ts` | 修改 | `start(req: AiTradeGroupStartReq)`→`/ai-trade/start`；新增 `stopGroup(groupId)`→`/ai-trade/${groupId}/stop`、`stopMember(groupId, instId)`；`status()` 返回 `AiTradeGroupStatusResponse`。`aiTradeWS` 不变。 |
| `apps/web/lib/store.ts` | 修改 | 新增瞬态 `aiTradeGroup: AiTradeGroupState \| null` + 成员 setter（`setAiTradeGroup / setAiTradeMemberStatus / setAiTradeMemberTrades / appendAiTradeMemberTrade / setAiTradeMemberPosition / pushAiTradeMemberDecision / setAiTradeMemberTraces / upsertAiTradeMemberTracePhase / setAiTradeMemberSummary / clearAiTradeRuntime`，镜像 liveAutoGroup member setter）；`aiTradeGroup` **不入 partialize**（瞬态）；`aiTradeConfig` 保留并可选增 `bar?`（记忆上次周期）。移除对 `aiTradeSession/aiTradeSessionId` 的运行期读写（可保留字段兼容或删除，见 §缺陷回填）。 |
| `apps/web/lib/useAiTradeStream.ts` → `useAiTradeGroupStream.ts` | 改造 | 镜像 `useLiveAutoGroupStream`：分组重发现（`GET /ai-trade/status`）+ 每成员各开一条 `aiTradeWS(sessionId)` + `memberKey` 稳定订阅 + `routeMemberEvent(instId, event, setters)`（snapshot/status/trade/position/decision/summary/agent_trace 归入成员槽）。 |
| `apps/web/components/Agent/AiTradeLaunchModal.tsx` | 改写 | 单 `instId` 文本框 → 多标的分配选择器（默认回填 `autoTradeMembers`，**至多 `AI_TRADE_MAX_SYMBOLS` 个**、超出提示并禁增，P1-2；空则当前图表标的；每成员 allocation% + 移除 + 均分 + even/max_ratio 段控，`allocationMode` 默认从 `store.autoTradeAllocationMode` 播种，P1-2）；`bar` 只读 `Row` → 可编辑 `<select>`（`BARS`）本地态（默认回填 `propBar`）；`AiTradeLaunchOpts` 增 `members/allocationMode/bar`；预算预览 `availUsdt` 取 `members[0]` 的 config（P2-2）。**回测模式仍单标的**（沿用单 `instId` 输入、默认 `members[0].instId`，P2-1 + 本地可编辑 bar）。 |
| `apps/web/lib/aiTrade.ts` | 修改 | 新增 `export const AI_TRADE_MAX_SYMBOLS`（镜像 `AI_TRADE_MAX_STRATEGIES`，值须与后端 `ai_trade_max_symbols=5` 对齐；供弹窗回填封顶与提示，P1-2）。 |
| `apps/web/components/Agent/AiTradeToggle.tsx` | 修改 | `startM` 走 `aiTradeApi.start(groupReq{members,bar,allocationMode,...})`→成功 `setAiTradeGroup`；`onToggle(false)` → `stopGroup(groupId)`；两条 mutation 用 `opts.bar` 取代 `store.bar`；`defaultMembers` 由 `autoTradeMembers`（空则 `[{instId, allocationPct:100}]`）派生传入弹窗；`running` 由 `aiTradeGroup!=null` 判定。 |
| `apps/web/components/Chart/AiTradePanel.tsx` | 改写 | 订阅 `aiTradeGroup`（非 `aiTradeSession`）；分组头（Σ PnL）＋逐成员标的行（可点击 `setInst`）；单成员时视觉等价第 5 轮。`group==null` 返回 null。 |
| `apps/web/components/Chart/WatchlistTab.tsx` | 修改 | 分支判据 `aiTradeSession` → `aiTradeGroup`；`aiOtherRows` 剔除**所有分组成员标的**（`rows.filter(r => !groupInstIds.has(r.instId))`，沿用第 5 轮 P1-1 精神：取全量 `rows` 剔除会话标的）；`showStrategyAutoPanel = !aiTradeGroup || !!liveAutoGroup`。 |
| `apps/web/components/Agent/AiTradeAgentTrace.tsx` | 修改 | 顶部加标的段控/下拉（分组成员集，默认聚焦图表标的或首成员）；展示 `aiTradeGroup.members[focused].traces`。单成员时不显段控、行为等价第 4 轮。 |
| `apps/web/app/page.tsx` | 小改 | `useAiTradeStream()` → `useAiTradeGroupStream()`。 |
| `services/quant/app/tests/test_ai_trade_group.py` | 新增 | 后端分组：多标的启动/预算切分/逐成员预检失败整组拒绝/互斥/`status()==group_status()`/stop_group。 |
| `apps/web/components/Agent/AiTradeLaunchModal.test.tsx` | 修改 | 断言默认回填 `autoTradeMembers`、空回退当前标的、bar `<select>` 默认＝propBar 且可改并进 `onConfirm`、均分/allocation 校验。 |
| `apps/web/components/Chart/AiTradePanel.test.tsx` | 修改 | 断言按 `aiTradeGroup` 渲染多成员行、点击 `setInst`、`group==null` 不渲染；注入并逐例复位 `store.aiTradeGroup`。 |

> 未列出的文件一律不改。**后端 `session.py/decider.py/signals.py/trace.py/market_context.py/market_window.py/backtest.py/prompts.py 及 `live_auto/**` 全部零改动。**

---

## 六、Interface design（接口 / 组件签名）

### 6.1 REST（`services/quant/app/api/ai_auto.py`）

```
POST /api/ai-trade/start           body: AiTradeGroupStartIn → 200 { groupId, bar, strategyIds, strategyNames,
                                     totalBudgetUsd, leverage, contextRefreshRounds, createdTs,
                                     members:[{ instId, sessionId, runId, bar, allocationPct, budgetUsd,
                                                strategyIds, strategyNames, state, positionSide, sessionPnlUsd,
                                                roundsCompleted, contextRefreshRounds, degraded }] }
                                     错误：403 双闸未开 / 400 AGENT_NOT_CONFIGURED|策略越界|周期非法|成员数>ai_trade_max_symbols|allocation 非法
                                          / 409 AI_TRADE_SESSION_LIMIT（互斥）
                                          / 409 { code:"AI_TRADE_GROUP_PREFLIGHT", members:[{instId,code,message}] }
                                            其中 member.code ∈ { "LIVE_INSTRUMENT_UNSUPPORTED"（既有码，非 AI_TRADE_ 前缀）,
                                                                "AI_TRADE_PREEXISTING_POSITION"（既有）,
                                                                "AI_TRADE_MEMBER_BUDGET_TOO_SMALL"（本轮新增，见 §4.1 P1-3）}
                                     注：预算总额天花板由服务端解析 min(maxBudgetPct/100, ai_trade_max_capital_frac=5%)，
                                          前端任何「上限」入参一概不接受（P0-1）；budgetUsd 回执为夹紧后最终生效值。
POST /api/ai-trade/{group_id}/stop            → 200 { groupId, stopReason, members:[{instId,sessionId,runId,flattened}] }；404 不存在
POST /api/ai-trade/{group_id}/member/{inst_id}/stop → 200 单成员停止；404 不存在
GET  /api/ai-trade/status                     → 200 { group: AiTradeGroupRow | null, aiTradeEnabled: bool }
WS   /api/ws/ai-trade/{session_id}            → 不变（每成员一条；连接即 subscribe→status→snapshot；事件 {type,data}）
```

### 6.2 Pydantic（`schemas.py`，镜像 live-auto 分组，路由手搓 dict 不设 response_model）

```python
class AiTradeGroupMemberIn(BaseModel):
    instId: str = Field(min_length=1, max_length=32)
    allocationPct: float = Field(gt=0, le=100)

class AiTradeGroupStartIn(BaseModel):
    members: list[AiTradeGroupMemberIn]
    bar: str = "1H"
    strategyIds: list[int] = Field(min_length=1)
    accountId: int | None = None
    allocationMode: Literal["even", "max_ratio"] = "even"
    capitalUsd: float | None = Field(default=None, gt=0)          # 分组总预算
    leverage: float | None = Field(default=None, gt=0, le=125)
    takeProfitUsd: float | None = Field(default=None, gt=0)       # 分组总止盈，按 pct/Σpct 分摊
    stopLossUsd: float | None = Field(default=None, gt=0)
    stopLossPct: float | None = Field(default=None, gt=0)         # 单仓 OCO（各成员同值，服务端夹紧）
    takeProfitPct: float | None = Field(default=None, gt=0)
    contextRefreshRounds: int = Field(default=10, ge=1)
    priming: bool = True
    primingBars: int = Field(default=300, ge=10, le=PAPER_WINDOW_MAX)
    # validators：members 非空 + instId 唯一；even 模式 Σ allocationPct ≤ 100（镜像 schemas.py:299-316）

class AiTradeGroupMemberRow(AiTradeSessionRow):   # 复用单会话行全字段 + 分配占比
    allocationPct: float

class AiTradeGroupRow(BaseModel):
    groupId: str
    bar: str
    strategyIds: list[int]
    strategyNames: list[str]
    totalBudgetUsd: float
    leverage: float
    contextRefreshRounds: int
    createdTs: int
    members: list[AiTradeGroupMemberRow]

class AiTradeStatusOut(BaseModel):     # 改：session → group
    group: AiTradeGroupRow | None = None
    aiTradeEnabled: bool
```

### 6.3 前端 wire 类型（`lib/aiTradeTypes.ts`，与后端同构 camelCase）

```ts
export interface AiTradeGroupMemberInput { instId: string; allocationPct: number; }
export interface AiTradeGroupStartReq {
  members: AiTradeGroupMemberInput[]; bar: string; strategyIds: number[];
  accountId?: number | null; allocationMode: "even" | "max_ratio";
  capitalUsd?: number | null; leverage?: number | null;
  takeProfitUsd?: number | null; stopLossUsd?: number | null;
  stopLossPct?: number | null; takeProfitPct?: number | null;
  contextRefreshRounds: number; priming?: boolean; primingBars?: number;
}
export interface AiTradeGroupMemberRow extends AiTradeSessionRow { allocationPct: number; }
export interface AiTradeGroupRow {
  groupId: string; bar: string; strategyIds: number[]; strategyNames: string[];
  totalBudgetUsd: number; leverage: number; contextRefreshRounds: number;
  createdTs: number; members: AiTradeGroupMemberRow[];
}
export interface AiTradeGroupStatusResponse { group: AiTradeGroupRow | null; aiTradeEnabled: boolean; }
// 运行期成员槽（瞬态；镜像 LiveAutoGroupMemberState + AI 专属轨迹/决策/摘要）
export interface AiTradeGroupMemberState {
  instId: string; bar: string; sessionId: string; runId: number;
  allocationPct: number; budgetUsd: number; strategyIds: number[]; strategyNames: string[];
  session: AiTradeSessionRow | null; decisions: AiTradeDecision[]; traces: AiTradeRoundTrace[];
  summary: string | null; trades: Trade[]; position: unknown | null;
}
export interface AiTradeGroupState {
  groupId: string; bar: string; totalBudgetUsd: number; leverage: number;
  contextRefreshRounds: number; state: "running" | "stopped"; startedTs: number;
  members: Record<string, AiTradeGroupMemberState>;
}
```

### 6.4 组件 / 弹窗签名（关键差异）

```ts
// AiTradeLaunchOpts（改）
interface AiTradeLaunchOpts {
  mode: "live" | "backtest";
  members: { instId: string; allocationPct: number }[];   // 新：实盘多标的
  allocationMode: "even" | "max_ratio";                   // 新
  instId: string;                                         // 保留：回测单标的用（members[0] 兜底）
  bar: string;                                            // 新：可编辑周期（取代对 store.bar 的直读）
  strategyIds: number[]; capitalUsd; leverage; takeProfitUsd; stopLossUsd;
  stopLossPct; takeProfitPct; contextRefreshRounds;
  startTs?; endTs?; decisionEveryBars?; minConsensus?;    // 回测专属
}
// AiTradeLaunchModal 新增 prop：
//   defaultMembers: { instId: string; allocationPct: number }[]  // 来自 autoTradeMembers，由 Toggle 侧已截到 ≤ AI_TRADE_MAX_SYMBOLS
//   defaultAllocationMode: "even" | "max_ratio"                  // 来自 store.autoTradeAllocationMode（P1-2）
```

> 无 CLI 变更。WS 事件协议不变（仍 `{type,data}`：status/snapshot/trade/position/decision/summary/agent_trace）。

---

## 七、Data model（数据模型 / 内存态 / 持久化）

- **DB**：零 schema 变更。分组每成员各写一条 `strategy_runs`（`mode="ai-trade"`，`config_json` 追加 `groupId/allocationPct/allocationMode/totalBudgetUsd`），沿用既有 `create_run`（`mode` 为自由 `String(16)`，无迁移）。审计仍走既有 `live_order_audit`。
- **后端内存态**：`AiTradeManager._group: AiTradeGroup | None`（单活跃分组独占）；成员为原样 `AiTradeSession`。
- **前端持久化（`aragon-ui` zustand persist）**：**不新增**任何持久化运行期态；`aiTradeGroup` 为**瞬态**（不入 partialize，刷新经 `GET /ai-trade/status` 重发现，镜像 `liveAutoGroup`）。种子来源 `autoTradeMembers` **已经是**持久化偏好（`store.ts:919`）——本轮直接复用、不新增偏好。`aiTradeConfig` 若增 `bar?` 记忆上次周期，需进 partialize 并补版本迁移默认（可选，非必须）。**store version 是否需 bump**：仅当给 `aiTradeConfig` 增持久字段时才 bump（`26→27`，补默认）；否则无需 bump。
- **配置**：`config.py` 新增 `ai_trade_max_symbols=5`；**不新增分组天花板配置**——AI 分组沿用 `ai_trade_max_capital_frac=0.05` / `ai_trade_default_capital_frac=0.01`（P0-1/P1-1），天花板分数由 `api/ai_auto.py` 服务端解析（`min(maxBudgetPct/100, 5%)`）。`desktop/src/lib/config.js` 的 `AI_TRADE_` 前缀透传已覆盖 `ai_trade_max_symbols` 新键（无需改 desktop 白名单）。

---

## 八、Testing & acceptance criteria（测试与验收）

### 8.1 后端单测（pytest，真实集成、共享临时 DB）

1. `test_ai_trade_group.py`：
   - `starts_group_over_multiple_symbols`：2 成员启动成功，各得 `budget=total×pct/100`，`status()==group_status()` 且含 2 成员行。
   - `rejects_group_when_any_member_has_position`：某成员预存持仓 → 整组拒绝、`AiTradeGroupPreflightError.failures` 指名该 `instId`，`self._group` 保持 None（无副作用）。
   - `total_exposure_capped`（**P0-1 敞口门禁**）：无论标的数，`Σ member.budget_usd == total ≤ avail×ai_trade_max_capital_frac`（默认 5%）；**并显式断言天花板取的是 `ai_trade_max_capital_frac`（5%）而非 `live_auto_group_max_capital_frac`（100%）**——构造 `maxBudgetPct=100` 场景，验证 `total ≈ avail×5%`（若误用 100% 天花板此断言即红）。
   - `blank_capital_defaults_to_one_percent`（**P1-1**）：留空 `capitalUsd` 时 `total ≈ avail×ai_trade_default_capital_frac`（1%），非打满 5% 上限。
   - `member_budget_too_small_rejects_group`（**P1-3 新增码**）：某成员 `total×pct` 不足最小下单量 → 整组拒绝、`failures` 该 `instId` 的 `code=="AI_TRADE_MEMBER_BUDGET_TOO_SMALL"`。
   - `mutual_exclusion_with_live_auto`：live-auto 分组活跃时 AI `start_group` 抛 `AI_TRADE_SESSION_LIMIT`；反向亦然（live-auto `_require_capacity` 读 `get_ai_trade_manager().status()` 仍生效，无 live_auto 改动）。
   - `rejects_over_max_symbols`：成员数 > `ai_trade_max_symbols` → 400。
   - `stop_group_clears_group_for_mutual_exclusion`（**P2-4**）：`stop_group` 后 `status()`/`group_status()` 均回 `None`，随即 live-auto `start_group` 不再被 AI 侧阻塞（验证无僵尸分组）。
   - `stop_group_flattens_all_members`：`stop_group` 停所有成员、清分组。
   - `single_member_group_equivalent`：1 成员、100% 分配、**显式给定 `capitalUsd`** 的分组，其成员行字段与旧单会话 `info()` 逐键一致（回归保障；预算默认差异已由 `blank_capital_defaults_to_one_percent` 单独覆盖）。

### 8.2 前端单测（vitest；组件测试须 `NODE_ENV=test`）

1. `AiTradeLaunchModal.test.tsx`：默认回填 `defaultMembers`（来自 `autoTradeMembers`）；`autoTradeMembers` 空 → 回退当前图表标的 100%；bar `<select>` 默认＝`propBar`、改选 `4H` 后 `onConfirm` 收到 `bar:"4H"`；均分按钮把 Σ 归 100；仅 `isAutoTradable` 可入选。**P1-2 新增**：回填 > `AI_TRADE_MAX_SYMBOLS` 个成员时只保留前 N 个并显示上限提示、「新增标的」禁用；`allocationMode` 默认＝注入的 `defaultAllocationMode`（`store.autoTradeAllocationMode`）而非写死 `"even"`；回测段 `instId` 默认＝`members[0].instId`。
2. `AiTradePanel.test.tsx`：注入 `store.aiTradeGroup`（逐例复位）；渲染 N 成员行、点击某行调 `setInst(instId)`；`group==null` 不渲染。
3. store setter：`setAiTradeGroup` / 成员 setter 归约正确；`aiTradeGroup` 不在 partialize 快照内。

### 8.3 交互 / 集成验收（人工，`pnpm --filter web dev`；真实资金联调为手动 opt-in）

- **AC-1**：左侧 `AutoTradePanel` 维护 `autoTradeMembers`（如 BTC/ETH）后点「AI 自动交易」→ 弹窗**自动列出这两标的及其分配**，无需手输。
- **AC-2**：`autoTradeMembers` 为空 → 弹窗回退为「当前图表标的」单成员（向后兼容单标的路径）。
- **AC-3**：弹窗「周期」为可编辑下拉，默认＝K 线所选周期；改成 `4H` 并启动后，分组各成员会话与后端 run 的 `bar` 均为 `4H`（不再被 `store.bar` 覆盖）。回测模式改周期同样生效（修正现状直读 `store.bar` 的问题）。
- **AC-4**：启动后左侧列出全部成员标的行（可点击切图），右侧「AI 助手 → 执行轨迹」可按标的切换查看各自逐轮思考；分组头 PnL＝Σ 成员。
- **AC-5**：任一成员标的已有持仓 / 非 USDT 合约 → 整组被拒并逐标的提示原因，未产生任何真实下单。
- **AC-6**：与策略式 live-auto 分组互斥：一方运行时另一方启动被禁用/拒绝。
- **AC-7（敞口回归门禁）**：开 3 标的与开 1 标的，**总预算一致**（≤ 账户 5%），仅切分不同——敞口不随标的数倍增。
- **AC-8**：停止开关 → 整组停止并平掉各成员自管持仓；刷新页面经 `GET /ai-trade/status` 正确重发现分组并重连各成员 WS。
- **AC-9（自动填入保真，P1-2）**：左侧维护 6~8 个 `autoTradeMembers` 时，弹窗只回填前 `ai_trade_max_symbols`（5）个并给出上限提示，提交成功（不再撞 400）；左侧以 `max_ratio`（Σ>100）维护时弹窗默认落 `max_ratio` 段控、提交成功。

### 8.4 回归门禁

- `pytest services/quant`（含 `test_ai_trade_group` + 既有 ai-trade/backtest/live-auto 全绿；注意既知 1 例 flaky 计时测试与本轮无关）。
- `pnpm --filter web test` / `build` / `tsc` 通过；`WeightBatchArena.test.tsx` 既有 tsc 报错为历史遗留、非本轮回归，不得据此误判。
- 既有 `AiTradeAgentTrace.test.tsx / AiTradeToggle.test.tsx / aiTrade.test.ts` 更新后保持通过；单成员分组行为与第 4/5 轮等价。

---

## 九、Risks & mitigations（风险与缓解）

| 风险 | 影响 | 缓解 |
|---|---|---|
| 泛化单会话→分组波及第 3/4/5 轮已上线 UI（右侧轨迹、左侧标的面板） | 回归 | 1 成员分组严格保持与旧单会话等价（AC-1/AC-8、`single_member_group_equivalent` 后端回归、`AiTradePanel/AiTradeAgentTrace` 更新测试）；`AiTradeSession` 后端零改动，行为不变。 |
| 多标的敞口倍增（历史搁置该功能的唯一原因） | 资金风险 | **预算切分非倍增**：总额 `≤ avail×ai_trade_max_capital_frac`，成员按 pct 分；每标的另受单笔名义上限 + 单仓 OCO；AC-7 + `total_exposure_capped` 门禁。 |
| **（P0-1）误照搬 live-auto 分组的 100% 天花板** `live_auto_group_max_capital_frac` | **真实资金灾难（20× 放大）** | `api/ai_auto.py` 显式解析 `cap_frac=min(maxBudgetPct/100, ai_trade_max_capital_frac=5%)`，**绝不引用** `live_auto_group_max_capital_frac`；不新增 AI 分组天花板配置；`total_exposure_capped` 断言 `maxBudgetPct=100` 时 `total≈avail×5%`（误用即红）。 |
| **（P1-1）留空预算默认从 1% 静默跳到 5%** | 5× 意外加仓 | `default_budget_frac=ai_trade_default_capital_frac`（1%），非 live-auto 分组的「留空=打满上限」；`blank_capital_defaults_to_one_percent` 门禁 + 弹窗「×1%」文案不变。 |
| **（P1-2）`autoTradeMembers` 数量/分配模式与 AI 上限不匹配致提交即 400** | 头号需求跑不通 | 回填封顶 `AI_TRADE_MAX_SYMBOLS` + 上限提示 + 禁增；`allocationMode` 从 `store.autoTradeAllocationMode` 播种；后端 `_validate_members` 硬闸兜底；AC-9。 |
| N 个 decider 每轮各一次 LLM 调用，成本随标的数线性上升 | Token/费用 | `ai_trade_max_symbols=5` 上限；各会话既有「每 N 轮摘要压缩 + 清历史」控 token；弹窗显示计划标的数供用户预判。 |
| 多标的启动逐成员取 config/持仓触发 OKX 429 | 启动失败 | 复用 `live_auto_start_concurrency` 信号量限并发 + 持仓一次取全账户（镜像 `_gather_context`）。 |
| 互斥语义改动引入 live_auto 侧连锁改动 | 跨特性回归 | `AiTradeManager.status()` 返回 `group_status()`（dict/None），使 `live_auto/manager.py:472` 的既有检查**零改动**即正确；双向互斥各读对方 group_status，锁同为 `get_admission_lock()`。 |
| 启动请求契约从单标的改多标的，破坏回测路径 | 回测回归 | 回测仍单标的：弹窗回测段保留单 `instId`（`members[0]` 兜底），仅同享可编辑 bar；`aiBacktestApi` 契约不变（除 `bar` 改用 `opts.bar`）。 |
| `autoTradeMembers` 同时是策略式分组的成员源，两特性共享 | 语义混淆 | 二者**互斥运行**、共享的是**持久化偏好列表**（读列表不启动任何一方）；弹窗仅读取种子，启动前经互斥门禁把关。 |
| 时间级别可编辑后与主图不一致 | 认知负担 | 弹窗周期默认回填 K 线所选周期（`propBar`），用户显式改动即视为有意；分组头/成员行展示实际生效周期。 |
| store 运行期字段清理不当留悬挂引用 | 编译/运行错误 | `aiTradeSession/aiTradeSessionId` 及其 setter 的清理与 `useAiTradeGroupStream` 一并落，`tsc` 全量校验；保留或删除策略在「实施过程发现的方案缺陷」回填决断。 |

---

## 十、实施顺序建议（供「代码开发」节点）

1. **后端**：`config.py` 加 `ai_trade_max_symbols`（**不加分组天花板配置**，P0-1）→ `schemas.py` 加分组 schema → `ai_auto/manager.py` 分组化（数据类 + `start_group/stop_group/stop_member/group_status`，`status()`→`group_status()`，逐成员复用 `AiTradeSession`，`_preflight_group` 含**新增** `_budget_too_small`，P1-3）→ `api/ai_auto.py` 路由改造（**含 P0-1 天花板分数解析 `min(maxBudgetPct/100, ai_trade_max_capital_frac)` + P1-1 `default_frac=ai_trade_default_capital_frac`**）→ `test_ai_trade_group.py` 跑绿（含单成员等价 + 留空默认 1% + 互斥 + 敞口门禁 + budget-too-small）。
2. **前端契约/数据**：`aiTradeTypes.ts` 分组类型 → `aiTradeApi.ts`（start/stopGroup/stopMember/status）→ `store.ts`（`aiTradeGroup` + 成员 setter，瞬态）→ `useAiTradeGroupStream.ts`（镜像 `useLiveAutoGroupStream`）。
3. **前端弹窗（收口需求 1+2）**：`lib/aiTrade.ts` 加 `AI_TRADE_MAX_SYMBOLS`（P1-2）→ `AiTradeLaunchModal.tsx`（多标的选择器回填 `autoTradeMembers`、**封顶 `AI_TRADE_MAX_SYMBOLS` + `allocationMode` 从 store 播种**，P1-2；可编辑 bar `<select>`）→ `AiTradeToggle.tsx`（走分组 start/stop，用 `opts.bar`；`defaultMembers`/`defaultAllocationMode` 派生传入）。
4. **前端呈现泛化**：`AiTradePanel.tsx`（多成员行）→ `WatchlistTab.tsx`（`aiTradeGroup` 分支 + 剔除全部成员标的）→ `AiTradeAgentTrace.tsx`（标的切换）→ `page.tsx`（换 hook）。
5. **补测 + 全量门禁**：前端 3 组测试更新/新增 → `pnpm --filter web test/build/tsc`、`pytest services/quant` 全绿。

---

## 十一、实施过程发现的方案缺陷

由「代码开发」节点在实现中回填。以下为落地时相对 §5 变更计划的**必要补充**（均已就地实现并全绿）：

1. **§5 变更计划遗漏消费方 `AgentPanel.tsx`（已就地修复）。** 右侧「AI 助手」分段视图容器
   `apps/web/components/Agent/AgentPanel.tsx` 读 `aiTradeSession`/`aiTradeTraces` 并传给 `AiTradeAgentTrace`，
   未列入变更计划表；泛化为分组后必须同步改：改读 `aiTradeGroup`+`instId`，「执行轨迹」页签在
   `aiTradeGroup!=null` 时显示、运行圆点取「任一成员运行中」、轮次徽标取「各成员最大 roundsCompleted」，
   自动切入 keyed on `groupId`；`AiTradeAgentTrace` 由收 `session/traces` 改收 `group/activeInstId`（标的
   段控内置）。单成员时视觉与第 4 轮等价。

2. **后端测试 `test_ai_trade_api.py` / `test_auto_trade_admission.py` 未列入变更计划但必须同步（已更新）。**
   二者测的是被本轮**替换**的单会话契约（`/ai-trade/start` 单 `instId`、`status.session`、`/{sid}/stop`、
   `AiTradeStartRequest`、`manager.start`、`._session`）。已改到分组契约、保持各测试意图；其中「既有持仓」
   由整组预检 `409 AI_TRADE_GROUP_PREFLIGHT`（成员码 `AI_TRADE_PREEXISTING_POSITION`）表达，E4 pct 夹紧回执
   改从成员 `session.executor.risk` 核验（分组回执为只读视图、无顶层 pct）。`test_ai_trade_agent_trace.py`
   直接构造 `AiTradeSession`（零改动），不受影响。

3. **端点为「替换」而非 live-auto 式「并存」（按 §5/§6.1 落实）。** live-auto 保留了单会话端点并新增
   `/live-auto/group/*`；本轮按方案将 `/ai-trade/start|status|{id}/stop` **原地改为分组契约**（AI 单会话从非
   稳定公开契约、前端全量迁移），单标的=1 成员 100% 分组、行为等价（`single_member_group_equivalent` 门禁）。
   新增 `/ai-trade/{group_id}/member/{inst_id}/stop`。

4. **store 单会话运行期字段取「删除」而非「保留兼容」（§5 允许二选一）。** 移除
   `aiTradeSessionId/aiTradeSession/aiTradeTrades/aiTradePosition/aiTradeDecisions/aiTradeTraces/aiTradeSummary`
   及其 setter，新增 `aiTradeGroup` + 逐成员 setter（`setAiTradeMember*`/`patchAiTradeMember*`/
   `upsertAiTradeMemberTracePhase`），`clearAiTradeRuntime` 改清 `aiTradeGroup`。所有消费方同步。
   **无 store version bump**：`aiTradeConfig` 未加持久字段，运行期态全瞬态（刷新经 `GET /ai-trade/status`
   重发现），符合 §7「仅当 aiTradeConfig 增持久字段才 bump」。

5. **弹窗「新增标的」交互（§4.2 要求可增删但未定 UI）。** 实现为成员列表底部一个受 `isAutoTradable` +
   `AI_TRADE_MAX_SYMBOLS` 封顶守门的「新增标的」输入框（回车 / 按钮添加，达上限即禁用并改占位提示
   「已达上限 N 个」）；回填种子仍以 `autoTradeMembers`（前 N 个）为准，超出显浅提示（P1-2）。

6. **P2-5 决断：成员 run 仅入 `config_json`。** 追加 `groupId/allocationPct/allocationMode/totalBudgetUsd`，
   **不**落 live-auto 式 `group_id/group_started_ts/group_total_budget_usd` 专用索引列（AI 无分组历史聚合
   功能，零 DB 迁移，符合 §7）。

7. **P2-1 / P2-2 回填。** 回测段仍单标的，`instId` 默认取 `members[0].instId`（`autoTradeMembers` 空则当前
   图表标的）；分组预算预览 `availUsdt` 取 `members[0]` 的 `tradingApi.config`（账户级）、「总预算」代表分组总额。

8. **回归门禁全绿。** `pytest services/quant`：`test_ai_trade_group.py`(15) + 更新后的
   `test_ai_trade_api.py`/`test_auto_trade_admission.py` 及既有 ai-trade/live-auto 全绿（唯一 1 例
   `test_agent_manager_detach` 计时 flaky 与本轮无关，隔离重跑即过）。`pnpm --filter web test` 946 全绿、
   `tsc` 仅剩既有 `WeightBatchArena.test.tsx` 历史报错（非本轮回归）。

---

## 评审结论（Review Verdict · v2）

**结论：有条件通过（Approved with conditions）。**

本方案的核心架构判断——**多标的 AI 自动交易＝镜像已上线、已验证的 live-auto「分组」范式，成员为零改动的 `AiTradeSession`，总预算按 `allocationPct` 切分（非倍增）**——经逐处交叉核验，**可行、一致、规模得当**：后端仅动 4 文件、`session.py/decider.py/trace.py/backtest.py` 及 `live_auto/**` 全零改动，`status()→group_status()` 使 live-auto 侧互斥检查无需任何改动即生效，回测保持单标的、仅同享可编辑周期。需求(2)「可编辑周期」的关键接线（`AiTradeToggle` 两条 mutation 从直读 `store.bar` 改用 `opts.bar`）已在正文明确，且经核验确为现状缺陷（`AiTradeToggle.tsx:81/132` 确直读 `store.bar`）。

评审共提出 **1 个 P0、3 个 P1、5 个 P2**，其中 **全部 P0/P1 已在本文档正文就地修复并升 v2**：

- **P0-1**（预算天花板须锚定 5%、严禁照搬 live-auto 100% 天花板）→ §4.1「预算/敞口安全不变式」、§5 `api/ai_auto.py`/`config.py` 行、§6.1、§8.1 `total_exposure_capped`、§9 已落。
- **P1-1**（留空默认保持 1%）→ §4.1、§8.1 `blank_capital_defaults_to_one_percent`、§9 已落。
- **P1-2**（自动填入封顶 `AI_TRADE_MAX_SYMBOLS` + `allocationMode` 播种）→ §4.2、§5 弹窗/`aiTrade.ts` 行、§8.2、AC-9、§9 已落。
- **P1-3**（预检失败码对齐既有 + `_budget_too_small` 属新增）→ §4.1 `_preflight_group`、§6.1 已落。

**放行给「代码开发」节点须满足的条件（即上列已写入正文的约束，实现时不得偏离）：**

1. **（安全红线）** `api/ai_auto.py` 分组启动必须服务端自解析 `cap_frac = min(maxBudgetPct/100, ai_trade_max_capital_frac)`（5%），**绝不**引用 `live_auto_group_max_capital_frac`（100%）；`default_frac = ai_trade_default_capital_frac`（1%）。`total_exposure_capped` 与 `blank_capital_defaults_to_one_percent` 两条门禁必须存在且为绿。
2. 弹窗回填必须封顶 `AI_TRADE_MAX_SYMBOLS`（新前端常量，值与后端 `ai_trade_max_symbols` 对齐）并从 `store.autoTradeAllocationMode` 播种 `allocationMode`；后端 `_validate_members` 硬闸兜底。
3. 逐成员预检失败码严格用 `LIVE_INSTRUMENT_UNSUPPORTED` / `AI_TRADE_PREEXISTING_POSITION` / 新增 `AI_TRADE_MEMBER_BUDGET_TOO_SMALL`；`_budget_too_small` 镜像 live-auto 落地。
4. 各停止路径（`stop_group`/`stop_member`/`shutdown`）在无运行成员时必须清 `self._group`，保证 `group_status()` 不误报僵尸分组（P2-4）。

**残留 P2（不阻断放行，由「代码开发」节点在实现中定夺并回填 §十一）：** P2-1 回测段单标的 `instId` 播种、P2-2 分组预算预览取数口径、P2-5 成员 run 是否落专用索引列。这些均为 UI/数据细节，正文已给出建议默认，不影响安全与主干正确性。

> 复核范围：本次评审仅改 `spec.md`，未触碰任何源码，未执行 `git commit`（符合任务约束）。
