# 实盘交易模式（Live Trading Mode）开发方案

- **Feature key**: `live-trading-mode`
- **版本**: **v4**（iteration 2 · Subtask #1 复评节点）。演进链：v1 方案设计 → Subtask #1 评审修复（v2）→ Subtask #2 落地并记录实施偏差 → v3（iteration 2 方案设计节点刷新：以「已实现并通过测试」的工作树为设计基线，收编 I-1/I-2/I-3，修正 §4.1 「文档/实现不一致」I-4）→ **本轮 v4（iteration 2 · Subtask #1 复评）**：对照工作树逐项核验 v3 全部 `file:line` 引用与设计断言，修复 §3 两处文档滞后（C3-1 P1 / C3-2 P2），结论 **通过**。历史 v2 / v3 评审记录（评审记录 / 二次复核 / 评审结论 / I-1..I-4）整体保留以备审计。
- **作者**: 方案设计节点（solution architect）
- **关联红线**: 本方案是项目索引中明确预留的 **Phase 2「guarded order placement」**，首次跨越 Phase 1「real trading disabled」红线，故 *安全护栏* 与 *诚实数据* 是一等需求，而非可选项。

---

## 现状与 v3 变更（Current State & v3 Changes · iteration 2）

> 本节为 v3（iteration 2 方案设计节点）新增：以**已实现并通过测试**的工作树为设计基线，记录与 v2 的差异，供下游 Subtask #1（评审）/#2（开发）/#3（提交）按真相对齐。下方「现状」均经 iteration 2 对照工作树逐一复核（附 `file:line`）。**本节及 v3 全部改动仅触及本设计文档，未改任何源码**（实现已符合 v3 设计）。

### A. 实现现状（v2「仍待实现」项已全部落地）

| 能力 | 工作树落点 | 状态 |
|---|---|---|
| 签名 POST 下单客户端 | `okx/trade_client.py:23` `OKXTradeClient(OKXPrivateClient)`：`_post:26`（紧凑 JSON + `content=`）、`place_order:49`、`set_leverage:66`、`get_fills:82`、`get_pending_orders:89` | ✅ |
| 纯件下单组装/校验 | `okx/order_builder.py`：`InstrumentMeta:23`、`build_order_payload:130`、`validate_order:153`、`compute_notional/compute_margin`、`_map_direction:104`、`build_attach_algo_orders:115`、`is_instrument_supported:52` | ✅ |
| 服务层编排 | `okx/trading.py`：`place_live_order:124`、净仓反向拦截 `_would_reduce_net_position:93`、唯一 `_gen_cl_ord_id:61`、成功+失败**双留痕** `:154/:148`、`set_account_leverage/fetch_live_positions/fetch_live_fills/fetch_trading_config`、`_map_trade_code:45` | ✅ |
| REST 路由 + 开关守门 | `api/trading.py`：`order:106`/`leverage:96`/`config:70`/`positions:79`/`fills:87`；`_require_live_enabled:47` → 403 | ✅ |
| 配置护栏（C1-7 已收紧） | `config.py:42-47`：`live_trading_enabled=False`、`live_max_order_notional_usd=1000.0`、`live_default_td_mode="cross"`、`live_default_leverage=3.0` | ✅ |
| 审计表（无密钥） | `db/models.py:174` `LiveOrderAudit` + `repositories.create_live_order_audit:573` / `list_live_order_audit:588` | ✅ |
| 合约元数据 | `okx/rest_client.py:226` `get_instrument_meta`（返回 ctVal/ctValCcy/lotSz/minSz/tickSz/maxLever/ctType/settleCcy） | ✅ |
| 路由挂载 | `main.py:85` `include_router(trading.router, prefix="/api")` | ✅ |
| 前端纯件/类型/API | `lib/liveOrder.ts`、`lib/tradingTypes.ts`、`lib/tradingApi.ts` | ✅ |
| Live 组件族（左右分栏 + 下单面板） | `components/Live/`：`LiveBottomPanel`、`LiveTradePanel`、`DirectionToggle`、`QuantitySlider`、`LeverageControl`、`TpSlFields`、`OrderConfirmModal`、`LivePositionsTable`(5s 轮询)、`LiveFillsTable`(8s 轮询) | ✅ |
| Slider 原语 | `components/ui/index.tsx:191`（**复用**，未重复新建） | ✅ |
| 顶部切换 + 水合守门 | `components/Header/AccountModeSwitch.tsx`：启用实盘可点 `:44`；挂载守门——停在 live 但无就绪默认账户即回落 sim `:32-36`（I-1） | ✅ |
| 工作区接线 | `components/Chart/ChartWorkspace.tsx`：读 `accountMode:97`，live 渲染 `<LiveBottomPanel>:274` | ✅ |
| store | `lib/store.ts`：`persist version:10`、瞬态 `liveAcknowledged:57`（不持久化）、水合守门收口于 `AccountModeSwitch`（无新增 `onRehydrateStorage`） | ✅ |

### B. 测试现状（CI 必跑）

- 后端 pytest：`tests/test_okx_trade_signing.py`（POST 签名向量）、`test_order_builder.py`（映射/校验/拒单异常路径）、`test_trading_api.py`（`LIVE_TRADING_ENABLED=false`→403 / 成功留痕 / 失败留痕 `status=error` / 默认账户回落 / 不存在账户 404）。
- 前端 vitest（须 `NODE_ENV=test`）：`lib/liveOrder.test.ts`、`components/Live/LiveTradePanel.test.tsx`、`components/Live/QuantitySlider.test.tsx`。
- iteration 1 记录：后端 326 / 前端 vitest 全绿；本轮 v3 无源码改动，结论沿用，留待 Subtask #2/#3 复跑验证。

### C. v3 相对 v2 的设计变更（仅文档，**不改源码**）

1. **§4.1 `instMeta` wire schema 据实收敛**：v2 §4.1 JSON 示例把 `ctType/settleCcy` 列入下发 `instMeta`，但实现的 `InstMetaOut`（`schemas.py:388`）/ 前端 `InstMeta`（`tradingTypes.ts:11`）**仅含**数学字段，C1-4 标的判定由后端 `is_instrument_supported(meta)` 完成、仅下发布尔 `instrumentSupported`（`trading.py:253`）。v3 **采纳实现的更稳健做法**——标的范围可信源**单点在后端**，前端只消费布尔、不重算；`ctType/settleCcy` 留作后端内部字段（`get_instrument_meta` 仍返回供判定用），**不进 wire schema**。§4.1 示例已更正（详见 §「I-4」）。
2. **收编 Subtask #2 安全等价偏差 I-1/I-2/I-3** 为正式设计（水合守门落 `AccountModeSwitch`；`liveTradingEnabled=false` 面板内禁用而非回落 sim；行内 `role=status/alert` 替代 toast）——见 §2.2⑦ / §5.3 / 「实施过程发现的方案缺陷」「v3 收编结论」，不再视为偏差，后续节点不得据 v2 旧表述回改。
3. **写路径传输层重试的幂等性据实记录**：`_post` 对 `httpx.TransportError` 做**一次**重试（`trade_client.py:35`），重试复用同一 body；因下单体携带服务层生成的**唯一 clOrdId**，OKX 按 clOrdId 去重，**绝不双重成交**——见 §8 新增风险行。
4. **C1-7 确认落地**：`live_max_order_notional_usd` 默认已收紧为 `1000.0`（`config.py:45`），v2 评审放行条件 7 已满足。

---

## 评审记录（Review Notes · Subtask #1 · iteration 2 复评）

> 复评人：资深评审（senior reviewer）。本轮针对 **v3** 文档，对照**当前工作树实代码**逐项核验 v3 的全部 `file:line` 引用与设计断言；维度：可行性 / 完备性 / 一致性 / 规模适配。
> **结论速览**：可行性 / 完备性 / 规模适配三维**无 P0/P1**——核心写链路、安全护栏、前端左右分栏接线、桌面打包均已实现并通过测试，spec 断言与代码一一吻合（见下「核验证据」16 项）。**一致性**维度发现 **1 个 P1 + 1 个 P2** 文档滞后项，已在本轮就地修复。
>
> **版本处置说明**：评审模板要求「bump 到 v2」，但本文档经 iteration 2 方案设计节点已推进至 **v3**；回退至 v2 会丢弃已收编（ratified）的 v3 工作并违反 loop「在历史基础上推进、勿重复」原则。故本轮按模板**意图**（推进版本以反映本次评审）将版本**前进至 v4**，并完整保留 v2 / v3 历史评审记录以备审计。

### 核验证据（spec 断言 ↔ 工作树实代码，逐项命中）

| spec 断言 | 工作树核验 | 结论 |
|---|---|---|
| 签名 POST 紧凑 JSON + `content=` + 传输层**一次**重试 | `trade_client.py:33`（`separators=(",",":")`）/`:39`（`content=body`）/`:35,42`（`TransportError` 单次重试） | ✅ |
| `place_order` 同看顶层 `code` 与 `data[0].sCode` | `trade_client.py:58-63` | ✅ |
| C0-1 net_mode 反向单本地拦截 | `trading.py:134-136` + `_would_reduce_net_position:93` | ✅ |
| 唯一 clOrdId（≤32 alnum） | `trading.py:_gen_cl_ord_id:61`（`aragon{ms:x}{seq:03x}`） | ✅ |
| 成功 + 失败**双留痕** | `trading.py:148`（失败 `rejected`/`error`）/`:154`（成功 `accepted`） | ✅ |
| C1-4 标的判定后端单点、仅下发布尔（I-4） | `order_builder.is_instrument_supported:52`、`trading.py:253`、`schemas.InstMetaOut:388`（仅数学字段，无 ctType/settleCcy） | ✅ |
| C1-7 名义上限默认 1000.0 | `config.py:45` `live_max_order_notional_usd: float = 1000.0` | ✅ |
| 写类接口 403 守门，先于凭证读取 | `api/trading.py:_require_live_enabled:47` + `:100/:110` | ✅ |
| 默认账户回落 / 缺凭证 400 / 不存在 404 | `api/trading.py:_resolve_account:54-67` | ✅ |
| C1-5 水合守门落 `AccountModeSwitch`（I-1） | `AccountModeSwitch.tsx:33-36` | ✅ |
| 启用实盘可点 / 缺默认账户引导设置 | `AccountModeSwitch.tsx:43-51` | ✅ |
| `ChartWorkspace` live 渲染 `<LiveBottomPanel>` | `ChartWorkspace.tsx:97`（读 `accountMode`）/`:274-276` | ✅ |
| store `version:10` + 瞬态 `liveAcknowledged` 不持久化 | `store.ts:272`(version) /`:57,132`(瞬态) /`:275`(partialize 未含) /`:328`(migrate 补默认) | ✅ |
| 桌面打包透传 `LIVE_*`（C1-6） | `desktop/src/lib/config.js:61`（`key.startsWith('LIVE_')`） | ✅ |
| `LiveOrderAudit` 无密钥留痕 + `get_instrument_meta` | `db/models.py:174` / `okx/rest_client.py:226` | ✅ |
| 后端 / 前端测试存在 | `tests/test_okx_trade_signing.py`、`test_trading_api.py`、`test_order_builder.py`；`Live/LiveTradePanel.test.tsx`、`QuantitySlider.test.tsx`、`lib/liveOrder.test.ts`（iteration 1：后端 326 / 前端 vitest 全绿） | ✅ |

### 本轮新增问题（iteration 2 复评）

| # | 维度 | 严重度 | 问题 | 处置 |
|---|---|---|---|---|
| C3-1 | 一致性 | **P1** | §3 文件清单 `config.py` 行括注仍写「**当前工作树仍为 `200000.0`，下游须同步**」，与 §A 现状表 / §C④ / §8 风险①护栏③（三处均记 `1000.0` 已落地，`config.py:45` 复核确为 `1000.0`）**自相矛盾**；v3 明示目标即「文档↔实现↔测试一致」，对真实资金名义上限这一安全护栏的内部矛盾会令下游维护者无法判断权威值，违背 v3 目标，故定 P1。 | **已修复**：§3 `config.py` 行括注更正为「已落地 `1000.0`（`config.py:45`，iteration 2 复评核验）」，消除矛盾。历史 v2「放行条件 7」作审计记录保留不动；本轮**评审结论**显式标注条件 7 现已满足。 |
| C3-2 | 一致性 | P2 | §2.1 数据流图 / §3 `ChartWorkspace` 行写 `<LiveBottomPanel>` 传 `instId`、`market`、`markPrice`，但实现仅 `<LiveBottomPanel instId markPrice />`（`ChartWorkspace.tsx:276`），组件入参为 `{instId, markPrice, accountId?}`（`LiveBottomPanel.tsx:21-29`）——`market` 既未传也非 prop，文档过度指定一个不存在的入参。 | **已修复**：§3 `ChartWorkspace` 行更正为「传 `instId`、`markPrice`（可选 `accountId`）」，与组件签名对齐。 |

---

## 评审记录（Review Notes · Subtask #1）

> 评审人：资深评审（senior reviewer）。维度：**可行性 / 完备性 / 一致性 / 规模适配**。
> 所有 **P0 / P1 已在本 v2 文档正文就地修复**（每条标注落点小节）；P2 作为实施提示保留。
> 评审基线：逐一核对正文对现有代码的引用——`OKXPrivateClient._sign/_iso_timestamp/_headers/_get`、`main.okx_error_handler` 与 `http_exception_handler`（均回 `{error:{code,message}}`，502/HTTP 状态）、`/accounts` 的 `defaultId`、`store.ts` `version:9` 与 `accountMode/chartView`、`AccountModeSwitch` 实盘置灰「敬请期待」、`ui.Modal/Badge`（无 Slider）、`OkxAccountPosition`、`config.proxy_url`、`rest_client.get_instruments/get_ticker`、`desktop/src/lib/config.js`——引用基本属实，下列为发现的偏差。（注：本行为 v1 首轮基线；其中 `store.ts version:9` 与「`ui` 无 Slider」已被实现追平，**以下文「二次复核增补 A」为准**。）

| # | 维度 | 严重度 | 问题 | 处置 |
|---|---|---|---|---|
| C0-1 | 可行性/安全 | **P0** | `net_mode` 下对**已有持仓标的**反向下单会减少/平掉既有持仓，与任务硬约束「不能动现有持仓」冲突；§8① 的「⑤ 仅开新仓」在 net_mode 不成立，联调真实账户时尤其危险。 | **已修复**：§2.3 增 net_mode 语义警示；§2.2 下单时序增「取持仓→反向减仓本地拦截」；§6 增 `LIVE_WOULD_REDUCE_POSITION`；§7.3 强制选无持仓标的；§8 风险①⑤ 修正。 |
| C1-1 | 可行性 | **P1** | 全部下单数学依赖 `instMeta`(ctVal/ctValCcy/lotSz/minSz/tickSz/maxLever)，但现有 `Instrument`/`get_instruments`（基于 `market/tickers`）**不含**这些字段，`/api/v5/public/instruments` 当前无人调用；§3 把 `rest_client.py` 标为「✎（按需）」属误导。 | **已修复**：§3 改为**必做**，定义 `get_instrument_meta` 来源端点/返回结构/缓存；§4.1 注明 instMeta 来源。 |
| C1-2 | 完备性/安全 | **P1** | 接口示例 `clOrdId:"aragon0001"` 为固定值且未定义在途防重——会导致重复真实下单或被 OKX 拒（clOrdId 必须唯一）。 | **已修复**：§2.2 增唯一 clOrdId 生成 + 提交按钮在途禁用；§4.3/§5.1 同步。 |
| C1-3 | 安全 | **P1** | 「确认弹窗首单强制、之后会话记忆」表述歧义，可能被实现为后续单跳过逐单明细确认，削弱真实资金安全。 | **已修复**：§2.2 明确**每一单**都弹明细复核弹窗，仅「风险告知勾选」可会话记忆。 |
| C1-4 | 完备性 | **P1** | 标的类型/结算币种未限定，全部数学假设 USDT 本位合约；SPOT/币本位会算错。 | **已修复**：§2.3 限定仅 USDT 本位 SWAP/FUTURES；§4.1 回 `ctType/settleCcy/instrumentSupported`；§6 增 `LIVE_INSTRUMENT_UNSUPPORTED`，不支持类型禁用面板。 |
| C1-5 | 完备性 | **P1** | 持久化 `accountMode="live"` 重水合无守门：刷新后可能直接进入实盘而默认账户已失效/开关关闭。 | **已修复**：§5.3/§3 增水合校验，缺默认账户或 `liveTradingEnabled=false` 时回落 sim。 |
| C1-6 | 完备性 | **P1** | 打包 env 缺口：`desktop/src/lib/config.js` 的 `backendEnv` 仅透传 `AGENT_*`，`LIVE_*` 不进打包后端；§3 文件清单漏列该文件。 | **已修复**：§3 新增「桌面」小节列入该文件并扩展白名单透传 `LIVE_*`；§8 风险行同步。 |
| C2-1 | 一致性 | P2 | `set-leverage` 的 `posSide` 用法在 §2.3 与 §4.2 示例间不一致（cross 不应带 posSide）。 | 实施提示：§2.3 明确规则、§4.2 示例已对齐。 |
| C2-2 | 规模适配 | P2 | 仅开仓、无平仓/减仓动作。 | 保留：契合需求聚焦买卖开仓；§2.2 注明平仓为 out-of-scope，右栏持仓只读。 |
| C2-3 | 完备性 | P2 | 市价单名义/保证金/止盈止损金额换算依赖可能滞后的 `lastPx`。 | 实施提示：§2.4 注明滑点风险、市价单名义上限取保守价。 |
| C2-4 | 安全 | P2 | §7.3 远价限价单仍可能被极端行情吃单而开新仓。 | 已在 §7.3 强化：极端远价 + minSz + 验后即撤 + 强制无持仓标的。 |

### 二次复核增补（Subtask #1 · 对照工作树实代码）

> 复核方法：对本文档引用的现有 / 拟建代码逐一回查工作树（`services/quant/app/okx/*`、`apps/web/lib/*`、`desktop/src/lib/config.js`、`apps/web/lib/store.ts`、`components/ui/index.tsx`）。结论：**核心安全设计已在代码侧落实且与本方案一致**——`OKXTradeClient._post` 确以紧凑 JSON（`separators=(",",":")`）+ `content=` 原样发送（守 50113，§4.6 ✓）；`order_builder._map_direction` 的 net / long_short 映射正确；`place_live_order` 已实现 C0-1 反向单本地拦截（`_would_reduce_net_position`）与失败留痕；`_gen_cl_ord_id` 生成唯一 clOrdId（C1-2 ✓）。下列为复核发现的需更正 / 补强项。

**A. 既有评审项的事实更正（原「现状」描述已被实现追平，就地更正，不改其结论）**

| # | 原描述 | 复核实况 | 处置 |
|---|---|---|---|
| C1-1 | 「`/api/v5/public/instruments` 当前无人调用」 | `rest_client.get_instrument_meta` 已实现并被 `trading._load_meta` 调用；`order_builder.InstrumentMeta` 已定义 | 已满足；§3 / §4.1 标注「已落实」。 |
| C1-6 | 「`config.js` 仅透传 `AGENT_*`」 | `backendEnv` 白名单已含 `key.startsWith('LIVE_')` | 已满足；§3 桌面行更正。 |
| 基线 | 「`ui` 无 Slider」 | `components/ui/index.tsx` 已导出 `Slider`（range / 受控 / aria） | §3 改为**复用**，不重复新建（右规模）。 |
| §5.3 | 「`persist` version 9→10」 | 工作树已是 `version:10` 且 `migrate(v<10)` 已补 `liveAcknowledged` | §5.3 / §3 更正；**剩余**仅 C1-5 水合守门。 |

**B. 二次复核新增问题**

| # | 维度 | 严重度 | 问题 | 处置 |
|---|---|---|---|---|
| C1-7 | 安全 / 右规模 | **P1** | `live_max_order_notional_usd` 默认 **200000.0**（§3 / `config.py:43`）——名义上限本是 §8 风险①的护栏③，$20 万的默认值近乎架空该护栏，与「真实资金安全为一等需求」相悖。 | **已修复**：§3 / §8 将方案默认收紧为 **1000.0 USDT**，放大须显式 env 覆写；下游须同步源码 `config.py` 默认值（本评审仅改 `spec.md`，不动源码）。 |
| C2-5 | 完备性 | P2 | §2.2 下单时序仅描述**成功**留痕，未描述**失败**留痕路径，而 §7.1 已就 `status=error` 立测、代码 `place_live_order` 亦已实现——文档与实现 / 测试不一致。 | 已修复：§2.2 增步骤 h，明确失败（本地拦截 `rejected` / OKX 失败 `error`）同样落 `live_order_audit`。 |
| C2-6 | 一致性 / 安全 | P2 | 「不能动现有持仓」在 long_short_mode **加仓**场景未被本地拦截，易被误读为 C0-1 漏洞。 | 已澄清：§2.3 注明 C0-1 仅拦 net_mode 反向单；long_short 加仓为合法产品行为，硬约束在**联调**期由 §7.3「无持仓标的」保证（覆盖两种 posMode）。 |

**C. 仍属本期未完成的 P1 工作（v2 时点记录；**v3 已全部落地**）**

> （v2 记录）后端写链路（`okx/trade_client` / `order_builder` / `trading`、`api/trading`、`config` 的 live_* 字段、`LiveOrderAudit` 留痕）与前端纯件（`tradingApi` / `tradingTypes` / `liveOrder`）已在工作树存在；**当时尚缺**：① C1-5 水合守门；② `AccountModeSwitch` 启用实盘可点；③ `ChartWorkspace` 渲染 `<LiveBottomPanel>`；④ `Live/` 组件族与 `OrderConfirmModal`。
>
> **v3 更新（iteration 2 对照工作树复核）：以上 4 项已全部实现**——① 水合守门落 `AccountModeSwitch`（I-1，§5.3，`AccountModeSwitch.tsx:32-36`）；② `AccountModeSwitch` 已启用实盘可点（`:44`）；③ `ChartWorkspace` 已读 `accountMode`（`:97`）并渲染 `<LiveBottomPanel>`（`:274`）；④ `Live/` 11 组件 + `OrderConfirmModal` 均已建。详见「现状与 v3 变更 · A」。

---

## 一、Overview（概述）

当前 Aragon Trading 是「左图右策略」的量化**模拟**工作台：OKX 公共行情只读、回测与 `SimBroker` 实时模拟、设置中心可管理只读实盘账户凭证（仅查 config/balance/positions）。`OKXPrivateClient` 在文件级硬约束「不暴露任何下单/撤单/划转写方法」，顶部「实盘」分段按钮诚实置灰为「敬请期待」。本方案要把这条红线后的能力——**用用户在设置中心配置的真实 OKX 账户进行真实下单买卖**——以「默认关闭、显式确认、可审计」的方式落地。

交互层面：当用户在顶部把账户模式切到「实盘」后，左侧工作区**下方**原本「成交记录 / 当前持仓」两个页签的区域要改为**左右分栏**。右栏延续展示成交记录与当前持仓（但数据源切换为该实盘账户的**真实** OKX 成交与持仓）；左栏变为**下单面板**：支持输入限价或按市价、买多（开多）/买空（开空）、设置杠杆倍率、用滑块＋手工输入设定仓位数量（两者双向联动）、并可设置止盈/止损数额。整体要求美观优雅、顶级设计、符合人机交互最佳实践。

工程层面：后端新增一个**专用交易客户端**（在只读私有客户端之上扩展签名 POST 与下单/设杠杆/查成交方法）、一个**交易服务层**（编排「设杠杆→下单」、错误码归一、密钥脱敏、下单上限校验）、一组**`/api/trading/*` REST 接口**（受 `LIVE_TRADING_ENABLED` 全局开关守门）；前端新增 `Live/` 组件族、`tradingApi`/`tradingTypes`/`liveOrder`（纯计算）三个 lib、以及一个可复用 `Slider` 原语。订单的唯一真源是 OKX 本身，本地仅落一张**下单审计表**（`live_order_audit`）作为操作留痕，绝不缓存/伪造成交。所有改动严格复用既有错误体（`{error:{code,message}}`）、密钥单点脱敏、秒级 UTC 时间口径、设计 token 配色等约定，不制造孤岛。

---

## 二、Technical design（技术设计）

### 2.1 架构与数据流

```text
浏览器/Electron
  ├─ 顶部 AccountModeSwitch：选「实盘」→ store.accountMode="live"（受「默认账户已配置」守门）
  ├─ 左侧 ChartWorkspace（下方区域）：accountMode==="live" → 渲染 <LiveBottomPanel>
  │     ├─ 左栏 <LiveTradePanel>（下单）──── tradingApi ──┐
  │     └─ 右栏 Tabs：成交记录/当前持仓 ── tradingApi ──┐ │
  └─ 经 Next 同源代理 /api/py/* → FastAPI :18760         │ │
        └─ api/trading.py（受 LIVE_TRADING_ENABLED 守门） │ │
              └─ okx/trading.py（服务层：编排/脱敏/限额/错误归一）
                    ├─ okx/order_builder.py（纯：posMode 映射 + 仓位/名义/上限校验）
                    ├─ okx/trade_client.py（OKXTradeClient：签名 POST/GET）
                    │     └─ 复用 private_client._sign/_iso_timestamp/_headers/_get_client
                    ├─ okx/rest_client.py（公共：取 instrument 元数据 ctVal/lotSz/tickSz）
                    └─ db.repositories.create_live_order_audit（操作留痕，不存密钥）
                          → 真实 OKX V5 私有签名 REST（经 settings.proxy_url 透传代理）
```

要点：
- 凭证真源是库内 `okx_accounts`（多账户，至多一行 `is_default`）。实盘面板默认使用**默认账户**（需求「当前默认的真实账户」），通过现有 `GET /accounts` 拿 `defaultId`，后续 `/trading/*` 调用带 `accountId`（缺省回落默认账户）。secret/passphrase 永不出接口、不进日志。
- `OKXTradeClient` **继承** `OKXPrivateClient`，复用其 `_get_client/_headers/_get` 与模块级 `_sign/_iso_timestamp`，**只新增**签名 POST 与交易方法。只读私有客户端保持文件级红线不变（设置中心仍只用只读基类），写能力收敛在子类与服务层。
- OKX 对地区受限，所有私有请求与既有约定一致透传 `settings.proxy_url`。

### 2.2 关键代码路径与下单时序

**下单（限价/市价，开多/开空，可带止盈止损）一次完整时序：**

1. 用户在 `<LiveTradePanel>` 选方向（买多/买空）、订单类型（限价/市价）、价格（限价才需）、杠杆、仓位数量（滑块或输入），可选展开止盈/止损。
2. 前端 `liveOrder.ts` 实时计算：名义价值 `notionalUsd = sz * ctVal * px`、所需保证金 `margin = notionalUsd / lever`、币本位数量 `coinQty = sz * ctVal`，并按 `tickSz/lotSz/minSz` 校验与取整；任一不合法即禁用「下单」按钮并就地提示。
3. 点击「买入开多/卖出开空」→ **每一单都弹出** `<OrderConfirmModal>`，逐项列出 instId、方向、tdMode、ordType、px、sz（张）、coinQty、lever、名义价值、保证金、止盈/止损触发价（**C1-3**：逐单明细复核弹窗不可跳过）。其中「我已知晓这是**真实下单**」风险勾选**首单强制**，之后会话内记忆 `liveAcknowledged`——**仅记忆该勾选**、**不**跳过逐单明细确认，刷新即复位。
4. 确认 → 提交按钮立即进入**在途禁用**态（防重复点击，**C1-2**），响应（成功/失败）返回前保持禁用 → `tradingApi.placeOrder(req)` → `POST /api/py/trading/order`。
5. 后端 `api/trading.py`：先查 `LIVE_TRADING_ENABLED`，关闭即 `403 {code:"LIVE_TRADING_DISABLED"}`；取 `accountId`（或默认）→ 取库内凭证 → 调 `okx/trading.place_live_order`。
6. 服务层 `place_live_order`：
   a. 取账户 `config.posMode`（long_short_mode / net_mode）与 instrument 元数据（`rest_client.get_instrument_meta`，公共 REST），并校验标的为 **USDT 本位 SWAP/FUTURES**（`settleCcy=="USDT"` 且有 `ctVal`），否则抛 `OKXPrivateError("LIVE_INSTRUMENT_UNSUPPORTED", …)`（**C1-4**）；
   b. **既有持仓守门（C0-1）**：取该标的当前持仓；若 `posMode=="net_mode"` 且本单方向与既有净仓相反（会减少/平掉既有持仓）→ 抛 `OKXPrivateError("LIVE_WOULD_REDUCE_POSITION", …)` 本地拦截、**不**下单（守任务硬约束「不能动现有持仓」）；
   c. 生成**唯一 clOrdId**（前缀 `aragon` + 单调序号/时间，≤32 位 alnum，**C1-2**），经 `order_builder.build_order_payload` 组装 OKX 下单体；
   d. 经 `order_builder.validate_order` 校验名义价值 ≤ `LIVE_MAX_ORDER_NOTIONAL_USD`、sz≥minSz、按 lotSz/tickSz 取整、px>0（限价）等，越界即抛 `OKXPrivateError("LIVE_ORDER_REJECTED_LOCAL", …)`；
   e. 若请求带 `lever` 且与目标不同 → 先 `set_leverage`（幂等），失败即上抛、**不**下单（避免错杠杆成交）；
   f. `OKXTradeClient.place_order` 签名 POST `/api/v5/trade/order`：先看顶层 `code`，再看 `data[0].sCode`，任一非 `"0"` → `_map_trade_code` 归一后抛 `OKXPrivateError`；
   g. 成功取 `ordId/clOrdId` → `create_live_order_audit` 落审计行（status=`accepted`，含 ordId/clOrdId）→ 返回 `{ok, ordId, clOrdId}`。
   h. **失败留痕（C2-5）**：a–f 任一步抛错（本地拦截 / OKX 拒单 / 连通失败）→ 先写一行 `live_order_audit`（本地拦截 `status=rejected`、OKX / 连通失败 `status=error`，`error_code` 记归一码）再脱敏上抛，确保失败同样留痕（与 §7.1「status=error」测试一致；代码 `place_live_order` 已实现该路径）。
7. 前端成功：**面板内 `role=status`** 显示「下单成功 · ordId=…」（**v3 收编 I-3**：工作树无 toast 基础设施，沿用既有 `flash` / `role=status` 行内反馈约定），并 `queryClient.invalidateQueries(["trading","positions"|"fills"|"config"])`，右栏轮询随即刷新真实持仓/成交。失败：在确认弹窗内以 **`role=alert`** 显示归一后的友好中文（复用 `ConnectivityResult` 风格的码表）。

**右栏实时数据**：`<LivePositionsTable>` 与 `<LiveFillsTable>` 各自 `useQuery` 轮询（`refetchInterval` 持仓 5s、成交 8s，`refetchOnWindowFocus`），数据源为真实 OKX，鉴权失败显式错误态（绝不假数据，C1）。

### 2.3 杠杆与仓位模式映射（OKX 语义，务必精确）

- `posMode = long_short_mode`：买多 → `side="buy", posSide="long"`；买空 → `side="sell", posSide="short"`。买多/买空各自独立开仓，互不影响既有反向仓。
- `posMode = net_mode`：买多 → `side="buy"`（不传 posSide）；买空 → `side="sell"`（不传 posSide）。
  - ⚠️ **net_mode 反向单会减/平既有净仓（C0-1）**：net 模式同标的不能多空并存，对已持净多仓的标的发「买空(sell)」会**减少/平掉**该多仓而非另开空仓。故服务层在 net_mode 必须先查持仓，若本单方向与既有净仓相反则**本地拒单**（`LIVE_WOULD_REDUCE_POSITION`），守「不能动现有持仓」。
- `set-leverage` 的 `posSide`（**C2-1**）：仅 `mgnMode=="isolated"` **且** long_short_mode 时随 `posSide`（long/short）逐方向设置；`mgnMode=="cross"` 或 net_mode **不传** `posSide`（误传可能被 OKX 拒）。§4.2 示例据此修正。
- 「不动现有持仓」边界（**C2-6**）：C0-1 本地拦截**仅**针对 net_mode 反向单（机制上会减 / 平既有净仓）；long_short_mode 对同向已有仓「加仓」是合法用户行为、**不**本地拦截，故任务硬约束「不能动现有持仓」在**联调**期由 §7.3「强制选无持仓标的」保证（对两种 posMode 同样有效），而非依赖逐模式拦截。
- `tdMode`：合约取 `LIVE_DEFAULT_TD_MODE`（默认 `"cross"`，UI 提供 cross/isolated 切换）。
- 单位：合约 `sz` 单位是**张**（contracts），非币量。名义价值/币量经 `ctVal`（合约面值）换算，UI 必须显式标注「张」并展示币量与名义价值，杜绝单位误解。
- 标的范围（**C1-4**）：本期实盘下单**仅支持 USDT 本位永续/交割合约**（`settleCcy=="USDT"`、有 `ctVal`）。SPOT 与币本位合约的数量/保证金口径不同，下单面板对不支持类型**禁用并提示**，不做错算。

### 2.4 止盈止损（attachAlgoOrds）

随单附 OCO 止盈止损，落在下单体的 `attachAlgoOrds`：
```jsonc
"attachAlgoOrds": [{
  "tpTriggerPx": "<止盈触发价>", "tpOrdPx": "-1",   // -1 = 触发后市价
  "slTriggerPx": "<止损触发价>", "slOrdPx": "-1",
  "tpTriggerPxType": "last", "slTriggerPxType": "last"
}]
```
UI 提供两种录入方式（纯前端换算，后端只收**触发价**）：
- 按价格：直接填 `tpTriggerPx / slTriggerPx`。
- 按金额：填预计盈亏额（USDT），`liveOrder.ts` 换算触发价（`entryPx` = 限价或市价 last）：
  - 多：`tpTriggerPx = entryPx + tpAmount/(sz*ctVal)`，`slTriggerPx = entryPx − slAmount/(sz*ctVal)`；
  - 空：`tpTriggerPx = entryPx − tpAmount/(sz*ctVal)`，`slTriggerPx = entryPx + slAmount/(sz*ctVal)`。
仅填止盈或仅填止损均合法（缺项不下发对应字段）。

> 市价单换算（**C2-3**）：市价单无 `px`，`entryPx`/名义/保证金/按金额止盈止损一律以 `lastPx`（可能滞后）估算，UI 须标注「按最新价估算，实际成交含滑点」；市价单的本地名义上限校验取**保守价**（买多取 `lastPx×(1+滑点缓冲)`、买空取 `lastPx×(1−缓冲)`）以免越限漏拦。

---

## 三、File / module change plan（文件 / 模块变更计划）

> 约定：✚=新建，✎=修改。所有改动遵循 CLAUDE.md 阈值（单文件≤800 行、单函数≤50 行、≤5 形参、圈复杂度≤10、嵌套≤4）。

### 后端 `services/quant/app/`

| 文件 | 操作 | 一句话意图 |
|---|---|---|
| `okx/trade_client.py` | ✚ | `OKXTradeClient(OKXPrivateClient)`：新增签名 `_post`、`place_order`、`set_leverage`、`get_fills`、`get_pending_orders`（GET 复用基类 `_get`）。 |
| `okx/order_builder.py` | ✚ | 纯函数：`build_order_payload`（posMode→side/posSide、ordType、px/sz、attachAlgoOrds）、`compute_notional/compute_margin`、`validate_order`（minSz/lotSz/tickSz/名义上限）。 |
| `okx/trading.py` | ✚ | 服务层：`place_live_order`/`set_account_leverage`/`fetch_live_positions`/`fetch_live_fills`/`fetch_trading_config`；`_map_trade_code` 错误归一；`_redact` 复用脱敏；下单上限守门。 |
| `api/trading.py` | ✚ | 路由：`POST /trading/order`、`POST /trading/leverage`、`GET /trading/config`、`GET /trading/positions`、`GET /trading/fills`、（可选）`POST /trading/cancel`；统一受 `LIVE_TRADING_ENABLED` 守门。 |
| `main.py` | ✎ | `from .api import ... trading` 并 `app.include_router(trading.router, prefix="/api")`。 |
| `config.py` | ✎ | 新增 `live_trading_enabled: bool=False`、`live_max_order_notional_usd: float=1000.0`（**C1-7**：保守默认，护栏须默认收紧；放大须显式 env 覆写。**已落地 `1000.0`（`config.py:45`，iteration 2 复评核验，C3-1）**）、`live_default_td_mode: str="cross"`、`live_default_leverage: float=3.0`。 |
| `schemas.py` | ✎ | 新增 `InstMetaOut`（**仅** ctVal/ctValCcy/lotSz/minSz/tickSz/maxLever——**v3**：ctType/settleCcy 不进 wire，标的判定单点在后端，见 I-4）、`PlaceOrderIn/PlaceOrderOut`、`SetLeverageIn/SetLeverageOut`、`TradingConfigOut`（含后端计算的 `instrumentSupported`）、`LiveFillsOut`、`LivePositionsOut`（持仓行复用 `OkxAccountPosition`）。 |
| `db/models.py` | ✎ | 新增 `LiveOrderAudit`（操作留痕；不含任何密钥）。`init_db` 由 `Base.metadata` 自动建表，无迁移脚本。 |
| `db/repositories.py` | ✎ | 新增 `create_live_order_audit(...)`、`list_live_order_audit(...)`，沿用 commit/refresh 模式。 |
| `okx/rest_client.py` | ✎ **（必做，C1-1）** | 现有 `Instrument`/`get_instruments`（基于 `market/tickers`）**不含** ctVal/lotSz/minSz/tickSz/maxLever，故**必须**新增 `get_instrument_meta(inst_type, inst_id)`：调 `GET /api/v5/public/instruments`（公共无签名）返回 `{ctVal, ctValCcy, lotSz, minSz, tickSz, maxLever, ctType, settleCcy}`，进程内按 instId 短 TTL 缓存（元数据低频变动）。全部下单数学依赖此数据，**非可选**。**（二次复核：`get_instrument_meta` 与 `order_builder.InstrumentMeta` 已在工作树落实，C1-1 已满足。）** |

### 桌面 `desktop/`

| 文件 | 操作 | 一句话意图 |
|---|---|---|
| `src/lib/config.js` | ✎ **（C1-6，已满足）** | `backendEnv` 白名单须透传 `LIVE_*`（`LIVE_TRADING_ENABLED`/`LIVE_MAX_ORDER_NOTIONAL_USD`/`LIVE_DEFAULT_TD_MODE`/`LIVE_DEFAULT_LEVERAGE`），否则打包桌面无法启用实盘。**二次复核：工作树 `backendEnv` 白名单已含 `key.startsWith('LIVE_')`，已满足。** dev 经 `services/quant/.env` 读取，不受影响。 |

### 前端 `apps/web/`

| 文件 | 操作 | 一句话意图 |
|---|---|---|
| `components/ui/index.tsx` | ✎（**已存在，复用**） | `Slider`（range，受控，键盘可达，aria-valuemin/max/now）**已在工作树提供**（二次复核确认）；本期直接**复用**，仅在缺特性时补强，**不重复新建**。 |
| `lib/tradingTypes.ts` | ✚ | 与后端 schema 对齐的 camelCase 类型：`TradingConfig`、`PlaceOrderReq/Result`、`LiveFill`、`LivePosition`、`OrderDirection`。 |
| `lib/tradingApi.ts` | ✚ | `/api/py/trading/*` REST 客户端（复用 `accountApi` 的 `request` 风格与错误体解析）。 |
| `lib/liveOrder.ts` | ✚ | 纯计算：仓位↔名义↔保证金、滑块%↔张数、minSz/lotSz/tickSz 取整、止盈止损价换算、下单校验。 |
| `lib/liveOrder.test.ts` | ✚ | `liveOrder` 纯函数单测（正常 + 边界 + 多/空对称）。 |
| `components/Live/LiveBottomPanel.tsx` | ✚ | 实盘下方左右分栏容器：左 `<LiveTradePanel>`，右「成交记录/当前持仓」Tabs。 |
| `components/Live/LiveTradePanel.tsx` | ✚ | 下单面板编排：拉 `tradingApi.config`、组合方向/类型/价格/杠杆/数量/止盈止损、触发确认与提交。 |
| `components/Live/DirectionToggle.tsx` | ✚ | 买多（绿）/买空（红）分段切换。 |
| `components/Live/QuantitySlider.tsx` | ✚ | 滑块 + 手工输入双向联动（张），展示币量/名义/保证金。 |
| `components/Live/LeverageControl.tsx` | ✚ | 杠杆输入 + 快捷档（1/3/5/10/20×），按 maxLever 夹取。 |
| `components/Live/TpSlFields.tsx` | ✚ | 止盈止损折叠区：按价格/按金额双模录入。 |
| `components/Live/OrderConfirmModal.tsx` | ✚ | 真实下单确认弹窗（复用 `Modal`），逐项复核 + 风险勾选。 |
| `components/Live/LivePositionsTable.tsx` | ✚ | 实盘持仓表（轮询，复用 `OkxAccountPosition` 渲染列）。 |
| `components/Live/LiveFillsTable.tsx` | ✚ | 实盘成交表（轮询，列：时间/方向/价/量/手续费）。 |
| `components/Live/LiveTradePanel.test.tsx` | ✚ | 面板组件测试（禁用态、确认流、提交调用）。 |
| `components/Header/AccountModeSwitch.tsx` | ✎（**已落实**） | 启用「实盘」：有**就绪默认账户**（含 secret/passphrase）才可点（`useQuery(["okx","accounts"])`），点击 `setAccountMode("live")`；否则置灰并引导去设置中心。**并承载 C1-5 水合守门**——挂载后若停在 live 但无就绪默认账户即回落 sim（**v3 收编 I-1**，`:32-36`）。 |
| `components/Chart/ChartWorkspace.tsx` | ✎（**已落实**，`:97/:274`） | 从 store 取 `accountMode`；`==="live"` 时下方渲染 `<LiveBottomPanel>`（传 `instId`、`markPrice`；可选 `accountId`，见 `LiveBottomPanel.tsx:21-29` 签名，C3-2），否则保持现状。 |
| `lib/store.ts` | ✎（**已落实**） | `setAccountMode("live")` 联动 `chartView="live-follow"`；瞬态 `liveAcknowledged`（不持久化）；`persist` 已 `version:10` + `migrate(v<10)` 补 `liveAcknowledged`。**v3：水合守门（C1-5）按 I-1 收口于 `AccountModeSwitch` 挂载处，`store.ts` 无需新增 `onRehydrateStorage` 回调**（见 §5.3）。 |

---

## 四、Interface design（接口设计）

所有 `/trading/*` 在 `LIVE_TRADING_ENABLED=false` 时统一返回 `403 {error:{code:"LIVE_TRADING_DISABLED", message:"实盘下单未启用"}}`；读类接口（config/positions/fills）不受该开关限制（仅查不写）。

### 4.1 `GET /api/trading/config?accountId=&instId=`
返回下单面板上下文（鉴权失败→502，绝不假数据）。
```jsonc
{
  "accountId": 1, "instId": "BTC-USDT-SWAP",
  "posMode": "long_short_mode",            // | "net_mode"
  "tdMode": "cross",
  "availUsdt": 1234.56,                     // 可用 USDT（用于滑块上限）
  // instMeta 仅承载下单数学所需字段（来源 GET /api/v5/public/instruments，公共，C1-1）。
  // v3：C1-4 标的范围判定（依赖 ctType/settleCcy）在后端单点完成（is_instrument_supported），
  //     仅下发布尔 instrumentSupported；前端不重算，ctType/settleCcy 不进 wire schema（见 I-4）。
  "instMeta": { "ctVal": 0.01, "ctValCcy": "BTC", "lotSz": 1, "minSz": 1, "tickSz": 0.1, "maxLever": 125 },
  "lastPx": 64210.5,
  "instrumentSupported": true,              // 后端按 settleCcy=USDT 且 SWAP/FUTURES 计算，否则面板禁用（C1-4）
  "liveTradingEnabled": true
}
```

### 4.2 `POST /api/trading/leverage`
```jsonc
// PlaceLeverageIn（posSide 仅 mgnMode=isolated 且 long_short_mode 时传；cross / net_mode 省略，C2-1）
{ "accountId": 1, "instId": "BTC-USDT-SWAP", "lever": 5, "mgnMode": "cross" }
// isolated + long_short 时：{ "accountId": 1, "instId": "BTC-USDT-SWAP", "lever": 5, "mgnMode": "isolated", "posSide": "long" }
// → SetLeverageOut
{ "ok": true, "instId": "BTC-USDT-SWAP", "lever": 5, "mgnMode": "cross", "posSide": null }
```

### 4.3 `POST /api/trading/order`
```jsonc
// PlaceOrderIn（px 仅 limit 必填；tpTriggerPx/slTriggerPx 可选；lever 可选，提供则先设杠杆）
{
  "accountId": 1, "instId": "BTC-USDT-SWAP",
  "direction": "long",          // "long"=买多 | "short"=买空
  "ordType": "limit",           // "limit" | "market"
  "tdMode": "cross",            // "cross" | "isolated"
  "px": 64000.0,
  "sz": 2,                       // 张
  "lever": 5,
  "tpTriggerPx": 66000.0,
  "slTriggerPx": 62000.0
}
// → PlaceOrderOut（clOrdId 为后端生成的**唯一**值，前端不传；示例非固定，C1-2）
{ "ok": true, "ordId": "987654321", "clOrdId": "aragon7f3a1c20", "code": null, "message": null }
```
后端按 §2.3/§2.4 组装 OKX `POST /api/v5/trade/order`，校验顶层 `code` 与 `data[0].sCode`。

### 4.4 `GET /api/trading/positions?accountId=`
```jsonc
{ "positions": [ /* OkxAccountPosition：instId/posSide/pos/avgPx/last/upl/uplRatio/lever/marginMode */ ] }
```

### 4.5 `GET /api/trading/fills?accountId=&instId=&limit=100`
```jsonc
{ "fills": [ { "ts": 1718000000, "instId": "BTC-USDT-SWAP", "side": "buy", "fillPx": 64020.1, "fillSz": 1, "fee": -0.03, "ordId": "987654321" } ] }
```

### 4.6 OKX 上游端点（后端→OKX）

| 用途 | OKX 端点 | 方法/签名 |
|---|---|---|
| 下单 | `/api/v5/trade/order` | POST 签名（body 精确串签名） |
| 设杠杆 | `/api/v5/account/set-leverage` | POST 签名 |
| 账户配置（posMode） | `/api/v5/account/config` | GET 签名（基类已有） |
| 余额 | `/api/v5/account/balance` | GET 签名（基类已有） |
| 持仓 | `/api/v5/account/positions` | GET 签名（基类已有） |
| 成交明细 | `/api/v5/trade/fills` | GET 签名 |
| 合约元数据 | `/api/v5/public/instruments` | GET 公共（无签名） |

> **签名关键**：POST 的签名预串为 `ts + "POST" + requestPath + body`，其中 `body` 必须与实际发送字节**完全一致**。因此 `_post` 用 `body = json.dumps(payload, separators=(",", ":"))` 生成串，并以 `content=body` 发送（**严禁** `json=payload`，httpx 默认序列化会与签名串不一致导致 50113 签名错误）。

---

## 五、Data model（数据模型）

### 5.1 新增 ORM `LiveOrderAudit`（`services/quant/app/db/models.py`）
仅留操作痕迹，绝不存任何密钥；时间秒级 UTC（C2）。
```python
class LiveOrderAudit(Base):
    __tablename__ = "live_order_audit"
    id: Mapped[int]            # pk autoincrement
    account_id: Mapped[int]    # 指向 okx_accounts.id（不加外键约束，账户可删，留痕保留）
    inst_id: Mapped[str]       # String(32)
    direction: Mapped[str]     # String(8)  long|short
    ord_type: Mapped[str]      # String(8)  limit|market
    td_mode: Mapped[str]       # String(8)
    px: Mapped[float | None]
    sz: Mapped[float]
    lever: Mapped[float | None]
    tp_trigger_px: Mapped[float | None]
    sl_trigger_px: Mapped[float | None]
    status: Mapped[str]        # String(16) accepted|rejected|error
    cl_ord_id: Mapped[str]     # String(32) 本地生成的唯一客户端单号（C1-2；防重 / 对账）
    ord_id: Mapped[str]        # String(64) 成功填 OKX ordId，否则空
    error_code: Mapped[str]    # String(64) 失败填归一码（含 LIVE_WOULD_REDUCE_POSITION 等本地拦截），否则空
    created_ts: Mapped[int]    # 秒级 UTC
```
> 不存任何 secret/passphrase/api_key；不在 message 字段落原始凭证（沿用 `_redact` 单点收口）。

### 5.2 前端内存形态（`lib/tradingTypes.ts`）
```ts
export type OrderDirection = "long" | "short";
export interface InstMeta { ctVal: number; ctValCcy: string; lotSz: number; minSz: number; tickSz: number; maxLever: number; }
export interface TradingConfig {
  accountId: number; instId: string; posMode: "long_short_mode" | "net_mode";
  tdMode: "cross" | "isolated"; availUsdt: number; instMeta: InstMeta; lastPx: number | null; liveTradingEnabled: boolean;
}
export interface PlaceOrderReq {
  accountId?: number; instId: string; direction: OrderDirection; ordType: "limit" | "market";
  tdMode: "cross" | "isolated"; px?: number; sz: number; lever?: number; tpTriggerPx?: number; slTriggerPx?: number;
}
export interface PlaceOrderResult { ok: boolean; ordId?: string; clOrdId?: string; code?: string | null; message?: string | null; }
export interface LiveFill { ts: number; instId: string; side: "buy" | "sell"; fillPx: number; fillSz: number; fee: number; ordId: string; }
export type LivePosition = OkxAccountPosition; // 复用 accountTypes
```

### 5.3 store 增量（`lib/store.ts`）
- `setAccountMode(m)`：`m==="live"` 时一并 `chartView="live-follow"`。
- 瞬态 `liveAcknowledged: boolean`（默认 false，**不**入 `partialize`，刷新即复位，确保每会话首单仍需勾选确认）。
- **水合守门（C1-5，v3 收编 I-1/I-2 最终落点）**：`accountMode` 持久化，刷新可能停在已失效的实盘态。守门**落在 `AccountModeSwitch` 挂载处**（而非 `store.onRehydrateStorage`）——因守门需「默认账户是否就绪」这一后端异步数据（`useQuery(["okx","accounts"])`），`onRehydrateStorage` 回调内拿不到 React Query 数据。规则：
  - 挂载后若 `accountMode==="live"` 但**无就绪默认账户（缺 secret/passphrase）** → 立即 `setAccountMode("sim")`（**真正不可用情形**，守「不停在无凭证可用的实盘态」）。
  - `liveTradingEnabled=false`（**非**不可用情形）**不**回落 sim，改由 `LiveTradePanel` 内**禁用下单控件 + 横幅提示**（读类 config/positions/fills 仍可查看真实数据，下单安全由后端 `LIVE_TRADING_ENABLED` 403 硬拦保证）。
- `persist` **已为 `version:10`**，`migrate(v<10)` 已补 `liveAcknowledged` 兜底；因守门收口于 `AccountModeSwitch`，`store.ts` **无需新增 `onRehydrateStorage` 回调**（v3 据实更正——§3 文件清单 `store.ts` 行已同步）。

---

## 六、错误码归一（`okx/trading._map_trade_code`）

在既有 `accounts._map_okx_code` 基础上补充下单类码（前端据此给友好中文）：

| OKX/本地码 | 语义码 | 中文提示 |
|---|---|---|
| 51008 | `INSUFFICIENT_BALANCE` | 可用余额/保证金不足 |
| 51004 | `LEVERAGE_EXCEEDS` | 下单数量超出当前杠杆可开上限 |
| 51000 / 51001 | `ORDER_PARAM_INVALID` | 下单参数不合法（价格/数量/精度） |
| 51010 | `ACCT_MODE_MISMATCH` | 账户模式与下单方式不匹配（检查持仓模式） |
| 50110 | `OKX_IP_DENIED` | 出口 IP 未加入该 API Key 白名单 |
| `LIVE_ORDER_REJECTED_LOCAL` | 同 | 超出本地名义上限/精度，本地拦截未发往 OKX |
| `LIVE_WOULD_REDUCE_POSITION` | 同 | net_mode 下该单方向与既有净仓相反、会减/平既有持仓，本地拦截（C0-1，守「不能动现有持仓」） |
| `LIVE_INSTRUMENT_UNSUPPORTED` | 同 | 非 USDT 本位 SWAP/FUTURES，数量/保证金口径不适用，本地拦截（C1-4） |
| `LIVE_TRADING_DISABLED` | 同 | 实盘下单未启用（设 `LIVE_TRADING_ENABLED=true`） |

错误体仍统一 `{error:{code,message}}`：本地校验拒单走 `OKXPrivateError`（子类化 `OKXError`）→ 全局 `okx_error_handler` 回 502；开关/未配置走 `HTTPException(403/400)` → 全局 HTTP handler 包络。message 一律先经 `_redact` 脱敏。

---

## 七、Testing & acceptance criteria（测试与验收）

### 7.1 后端单测（离线，pytest，CI 必跑）
- `test_okx_trade_signing.py`：POST 签名向量——固定 `ts/method=POST/path/body`，断言 `_sign` 输出与离线手算一致；断言 `_post` 用紧凑 JSON（无空格）且签名串与发送 body 字节一致。
- `test_order_builder.py`（纯函数）：long_short / net 两种 posMode 的 side/posSide 映射；limit/market 的 px 取舍；attachAlgoOrds 仅在有止盈/止损时出现；`validate_order` 命中名义上限/minSz/tickSz 边界（含至少一条**拒单异常路径**，如名义超限 `LIVE_ORDER_REJECTED_LOCAL`）。
- `test_trading_api.py`：仿 `test_accounts_api.py`，用 `ASGITransport` + `_FakeTradeClient` 桩（注入返回/抛错）：
  - `LIVE_TRADING_ENABLED=false` → `POST /trading/order` 返回 403（异常路径，先于一切凭证读取）；
  - 开启后下单成功路径返回 `ordId` 并写 1 行 `live_order_audit(status=accepted)`；
  - 桩抛 `OKXPrivateError("51008",…)` → 502 + `INSUFFICIENT_BALANCE` + 审计 `status=error`；
  - 默认账户回落、不存在账户 404。
  - **铁律**：测试一律用占位假凭证，**严禁**写入任务下发的真实开发凭证（沿用 `test_accounts_api` 注释规约）。
- `test_settings_redact` 扩展：确认下单错误 message 不泄露 secret/passphrase。

### 7.2 前端单测（vitest，`NODE_ENV=test`，CI 必跑）
- `liveOrder.test.ts`：滑块%↔张数互逆、名义/保证金/币量计算、tickSz/lotSz 取整、止盈止损价多空对称换算、非法输入（负数/超 maxLever/低于 minSz）返回校验错误。
- `LiveTradePanel.test.tsx`：未配置账户/开关关闭时禁用并提示；填非法数量时「下单」禁用；点击「下单」弹确认弹窗、勾选后 `tradingApi.placeOrder` 被调且入参正确。
- `QuantitySlider.test.tsx`：滑块与输入框双向联动、夹取边界。
- （组件测试运行须置 `NODE_ENV=test`，否则 React act() 报错——见既有约定。）

### 7.3 真实账户联调（opt-in，手动/网络标记，不进 CI）
按 README「真实数据硬约束 + 代理」前置，使用任务下发的 Dev-JR-US 账户（权限：读取/交易；出口 IP 须为 203.0.113.10 白名单内，经代理验证）。**联调铁律（C0-1）：先 `GET /trading/config` 与 `GET /trading/positions` 确认所选标的当前无任何持仓**；net_mode 账户尤须如此（反向单会动既有仓）。优先选一个账户当前**无持仓**的 USDT 本位 SWAP：
1. `GET /api/trading/config`：拿到真实 `posMode/availUsdt/instMeta/lastPx/instrumentSupported`（验证签名 GET + `public/instruments` 元数据链路）。
2. `POST /api/trading/leverage`：设一个小杠杆（如 3×）成功（按 §2.3 决定是否带 `posSide`）。
3. `POST /api/trading/order`：在**无持仓标的**上下一张**极端远离市价的限价单**（如买多 @ 市价 ×0.5，`sz=minSz`，必带止盈止损），验证 `ordId` 返回与右栏成交/持仓刷新；**验证完立即人工在 OKX 撤单**。极端远价 + `minSz` 使其几乎不可能成交；即便成交也仅为最小新仓、即撤。**绝不对已有持仓标的反向下单。**
4. 关闭 `LIVE_TRADING_ENABLED` 复跑下单 → 断言 403。

### 7.4 Definition of Done（本设计交付物）
- 本 `spec.md` 已建并见于 `git status`；含 Overview/Technical design/File change plan/Interface design/Data model/Testing & acceptance/Risks 全部小节，篇幅 ≥800 字。
- 文件 / 模块变更计划逐项可被下游工程师按行实现，无需再决策。

---

## 八、Risks & mitigations（风险与缓解）

| 风险 | 影响 | 缓解 |
|---|---|---|
| **真实资金误下单（最高优先级）** | 资损 | ① `LIVE_TRADING_ENABLED` 默认 false，必须显式开启；② 前端**每单**确认弹窗逐项复核 + 「真实下单」勾选（C1-3）；③ 后端 `LIVE_MAX_ORDER_NOTIONAL_USD` 名义上限本地拦截（**C1-7**：默认收紧至 1000 USDT，放大须显式 env 覆写，避免高默认值架空护栏）；④ 联调用 `minSz` 远价限价单并即撤；⑤ **net_mode 反向单会减/平既有持仓（C0-1）**：服务层先查持仓、反向单本地拒（`LIVE_WOULD_REDUCE_POSITION`）+ 联调强制选无持仓标的——「不动现有持仓」由拦截保证，而非仅靠「只开新仓」假设。 |
| 仓位单位（张 vs 币量）误解 | 下单量级错误 | UI 显式标「张」并同屏展示币量与名义价值；`order_builder` 按 `ctVal/lotSz/minSz` 取整校验。 |
| POST 签名失败（50113） | 全部下单被拒 | `_post` 用紧凑 JSON 串签名并以 `content=` 原样发送；单测固定向量守护。 |
| **下单 POST 传输层重试导致重复成交（v3 据实记录）** | 资损 / 双开仓 | `_post` 对 `httpx.TransportError` 做**一次**重试（`trade_client.py:35`），**重试复用同一 body**——body 内含服务层 `_gen_cl_ord_id` 生成的**唯一 clOrdId**（幂等键），OKX 按 clOrdId 去重，故**绝不双重成交**。残余：首发已成功但响应丢失时，重试会被 OKX 判重拒（→ 失败审计行 `status=error`），而订单实际已存在；由 `live_order_audit`（含 clOrdId）+ 右栏持仓/成交可对账，UI 失败提示引导用户**先查持仓/成交再重下**。**设计不变量**：写类请求只在传输层失败时重试且必须携带稳定 clOrdId；Subtask #1 评审须核验该不变量未被破坏。 |
| posMode 不匹配（net/long_short） | 下单报错或方向错 | `/trading/config` 回 `posMode`，`order_builder` 据此映射；`ACCT_MODE_MISMATCH` 友好提示。 |
| 出口 IP 不在白名单 | 鉴权失败 | 与读类一致透传代理；`OKX_IP_DENIED` 明确提示「出口 IP 未加入白名单」。 |
| set-leverage 与下单竞态/部分失败 | 错杠杆成交 | 服务层先设杠杆，失败即上抛**不**下单；set-leverage 幂等。 |
| 轮询频率过高触发限流 | 数据抖动/429 | 持仓 5s / 成交 8s，`refetchOnWindowFocus`，错误态退避（沿用既有 WS/REST 节流约定）。 |
| 密钥泄露 | 安全 | 凭证仅服务层构造客户端用；接口绝不回 secret/passphrase；message 单点 `_redact`；审计表不存密钥。 |
| 与「只读红线」文档冲突 | 维护困惑 | 只读 `OKXPrivateClient` 保持不变，写能力收敛在 `OKXTradeClient` 子类 + 服务层；在文件 docstring 标注 Phase 2 与开关守门。 |
| 桌面端打包未注入开关（C1-6） | 桌面无法实盘 | 现 `backendEnv` 仅透传 `AGENT_*`；须扩展白名单使 `LIVE_*` 注入打包后端子进程（见 §3「桌面」变更）。`LIVE_TRADING_ENABLED` 等经 `desktop/src/lib/config.js` 白名单透传，不在后端 special-case 桌面。 |

---

## 九、实施顺序建议（供 Subtask #2 参考）

1. 后端纯件先行：`okx/order_builder.py` + 单测 → `okx/trade_client.py` + 签名单测。
2. 服务层 `okx/trading.py` + `api/trading.py` + `schemas.py`/`config.py`/`main.py` 接线 + `test_trading_api.py`。
3. `db/models.py` `LiveOrderAudit` + `repositories` + 审计落库。
4. 前端纯件：`lib/tradingTypes.ts`/`tradingApi.ts`/`liveOrder.ts` + `liveOrder.test.ts`；`ui.Slider`。
5. `Live/` 组件族 + `ChartWorkspace`/`AccountModeSwitch`/`store` 接线 + 组件测试。
6. 真实账户 opt-in 联调（§7.3），通过后收尾。

---

## 评审结论（Review Verdict）

**有条件通过（Approved with conditions）。**

本方案架构清晰，与现有约定高度一致（统一错误体 `{error:{code,message}}` + `okx_error_handler`/`http_exception_handler`、`_redact` 单点脱敏、`proxy_url` 透传、设计 token、`persist` 迁移、`OkxAccountPosition` 复用），对真实资金的安全护栏（默认关闭开关、确认弹窗、名义上限、最小远价联调）意识到位，规模适配得当（订单真源在 OKX、本地仅 `live_order_audit` 留痕），可作为 Subtask #2 的实现依据。

**二次复核（对照工作树实代码）补充**：后端写链路（`OKXTradeClient` 签名 POST、`order_builder` 映射 / 校验、`trading.place_live_order` 编排 + C0-1 反向单拦截 + 失败留痕、`api/trading` 开关守门）与前端纯件（`tradingApi` / `tradingTypes` / `liveOrder`）**已在工作树落地且与本方案一致**，签名 `content=` 紧凑 JSON、唯一 clOrdId、错误归一 / 脱敏均已核验——**可行性由已存在的实现直接背书**。复核同时更正了 4 处「现状」已被实现追平的描述（C1-1 / C1-6 / Slider / store 版本，见「二次复核增补 A」），并新增 1 个 P1（C1-7）与 2 个 P2（C2-5 / C2-6，见「增补 B」）。

评审在 v2 已就地修复 **1 个 P0 与 7 个 P1**（原 6 项 + 二次复核新增 C1-7；详见「评审记录」），放行条件如下（实施时须满足，缺一即视为未达 DoD）：

1. **C0-1（P0）**：net_mode 下严禁对既有持仓标的反向下单——服务层必须先查持仓并本地拦截（`LIVE_WOULD_REDUCE_POSITION`），§7.3 联调强制选无持仓标的；这是守任务硬约束「不能动现有持仓」的**前置门禁**。
2. **C1-1（P1）**：`get_instrument_meta`（`GET /api/v5/public/instruments`）为**必做**项，全部下单数学依赖之，不得按「可选」处理。
3. **C1-2 / C1-3（P1）**：唯一 `clOrdId` + 在途禁用防重；逐单明细确认弹窗不可因「会话记忆」而跳过。
4. **C1-4（P1）**：实盘下单本期仅限 USDT 本位 SWAP/FUTURES，其它标的类型前端禁用（`LIVE_INSTRUMENT_UNSUPPORTED`）。
5. **C1-5（P1）**：`accountMode="live"` 水合守门，缺凭证 / 开关关闭即回落 sim。
6. **C1-6（P1）**：`desktop/src/lib/config.js` 白名单透传 `LIVE_*`，否则打包桌面实盘不可用（二次复核：已满足）。
7. **C1-7（P1，二次复核新增）**：`live_max_order_notional_usd` 默认须收紧为保守值（方案定 1000 USDT），放大须显式 env 覆写——当前源码默认仍为 `200000.0`，**下游 Subtask #2 须将 `config.py` 默认同步收紧**，否则名义上限护栏形同虚设。

> **仍待实现（v2 时点）→ v3 已全部落地**：① C1-5 水合守门（按 I-1 收口于 `AccountModeSwitch`，`:32-36`）；② `AccountModeSwitch` 已启用实盘可点（`:44`）；③ `ChartWorkspace` 已读 `accountMode`（`:97`）并渲染 `<LiveBottomPanel>`（`:274`）；④ `Live/` 组件族与 `OrderConfirmModal` 均已建。iteration 2 对照工作树复核确认；后端 326 / 前端 vitest 全绿（见「七、测试」与「现状与 v3 变更」）。详见「评审记录 · 二次复核增补 C」。

P2 项（C2-1 set-leverage posSide 用法、C2-2 平仓 out-of-scope、C2-3 市价单滞后价、C2-4 联调残余风险、**C2-5 失败留痕文档化**、**C2-6 long_short 加仓边界澄清**）已在正文以提示 / 就地修复形式补强，留作实施注意，不阻塞放行。

> 版本：**v2**（含 Subtask #1 二次复核增补） · 评审人：资深评审（senior reviewer） · 维度：可行性 / 完备性 / 一致性 / 规模适配 · 复核基线：工作树实代码逐一回查。

---

## 实施过程发现的方案缺陷（Issues Found During Implementation · Subtask #2）

> 本节由代码开发节点（Subtask #2）记录：实现「仍待实现」P1 项时对方案做的少量、安全等价的落点调整与澄清，已在源码就地实现，留待 Subtask #3 复核。**未改动任何安全语义**（默认关闭开关、每单确认、名义上限、净仓反向单本地拦截一律保留）。

### I-1（C1-5 落点）：水合守门实现在 `AccountModeSwitch` 挂载处，而非 `store.onRehydrateStorage`
- **方案原文**：§5.3 / 放行条件 5 写「`store.onRehydrateStorage` 水合守门」。
- **实况**：水合守门需「默认账户是否已配置」这一**后端异步数据**（`GET /accounts`），`onRehydrateStorage` 回调内拿不到 React Query 数据；且 `store.ts` 既有注释（migrate v<10 处）已明确「由 `AccountModeSwitch` 挂载时校验」。
- **处置**：在 `AccountModeSwitch` 用 `useQuery(["okx","accounts"])` 取默认账户，挂载后若 `accountMode==="live"` 且无「就绪默认账户（含 secret/passphrase）」即 `setAccountMode("sim")`。与 §5.3「或顶层组件挂载时」一致，`store.ts` 无需新增回调。

### I-2（C1-5 的 `liveTradingEnabled` 分支）：改为面板内显式禁用而非回落 sim
- **方案原文**：§5.3 写「若 `accountMode==="live"` 但…或 `liveTradingEnabled=false`，立即 `setAccountMode("sim")`」。
- **取舍理由**：`liveTradingEnabled=false` 时**读类接口（config/positions/fills）仍可用**且写类已被后端 403 硬拦截；若一进实盘即弹回 sim，会让用户**无法查看自己的真实持仓 / 成交**，也无法预览实盘 UI（本需求「界面美观优雅」需可见）。安全不变量（绝不误下单）已由后端 `LIVE_TRADING_ENABLED` 守门保证，与前端是否回落无关。
- **处置**：`LiveTradePanel` 读 `/trading/config` 后，`liveTradingEnabled=false` 时**禁用下单控件并就地横幅提示**（"实盘下单未启用…当前仅可查看真实持仓 / 成交"），持仓 / 成交照常轮询展示。**「缺默认账户」这一真正不可用的情形仍回落 sim（见 I-1），守住 §5.3 的核心意图「不停在无凭证可用的实盘态」。**

### I-3（toast → 行内反馈）：下单成功 / 失败用行内 `role=status/alert` 反馈
- **方案原文**：§2.2⑦ 写「toast『下单成功 · ordId=…』」。
- **实况**：工作树**无 toast 基础设施**；既有同类反馈（如 `OkxAccountsSection` 的 `flash`）一律用行内 `role=status` 文案。
- **处置**：成功在面板内以 `role=status` 显示「下单成功 · ordId=…」并 `invalidateQueries(["trading","positions"|"fills"|"config"])` 刷新右栏；失败在确认弹窗内以 `role=alert` 显示归一后的中文错误。语义与 §2.2⑦ 等价，遵循既有约定不引入新依赖。

> 以上均为落点 / 反馈机制层面的安全等价调整，未触及方案的下单时序、错误归一、护栏与数据真源（OKX）设计；§3 文件清单中 `store.ts` 因 I-1 实际无需改动（既有 `liveAcknowledged` / `version:10` / `migrate` 已满足，水合守门收口于 `AccountModeSwitch`）。

### I-4（v3 · 设计据实修正）：§4.1 `instMeta` wire schema 收敛 + `instrumentSupported` 后端单点判定

- **背景**：v2 §4.1 的 JSON 示例把 `ctType/settleCcy` 列入下发 `instMeta`，但实现的 `InstMetaOut`（`schemas.py:388`）与前端 `InstMeta`（`tradingTypes.ts:11`）**仅含** ctVal/ctValCcy/lotSz/minSz/tickSz/maxLever；C1-4 标的判定由后端 `is_instrument_supported(meta)` 完成、仅下发布尔 `instrumentSupported`（`trading.py:253`、`TradingConfigOut.instrumentSupported`，`schemas.py:409`）。`ctType/settleCcy` 仍由 `rest_client.get_instrument_meta`（`:226`）返回，供后端判定使用。
- **v3 设计裁定**：采纳**实现的更稳健做法**——标的范围（C1-4）的可信源**单点在后端**，前端只消费 `instrumentSupported` 布尔、**不重算**；`ctType/settleCcy` 保留为后端内部字段、**不进 wire schema**。§4.1 JSON 示例已据此据实更正，消除文档/实现不一致。**无需改动源码**（实现已符合 v3 设计）。
- **影响面**：仅文档；前端 `LiveTradePanel` 已按 `instrumentSupported` 做面板禁用守门，行为不变。

### v3 收编结论（Ratification · iteration 2）

I-1（水合守门落 `AccountModeSwitch`）、I-2（`liveTradingEnabled=false` 面板内禁用而非回落 sim）、I-3（行内 `role=status/alert` 反馈替代 toast）三项 Subtask #2 **安全等价偏差**，经 iteration 2 对照工作树复核，确认**未削弱任一安全不变量**——默认关闭（`live_trading_enabled=False`）、每单确认弹窗、名义上限 1000 USDT、净仓反向单本地拦截、数据真源 OKX 一律保留。v3 将其**正式收编进主设计**（§2.2⑦ / §5.3 / §3 文件清单），不再视为「偏差」；**后续 Subtask #1/#2/#3 不应据 v2 旧表述回改**。叠加 I-4（§4.1 据实收敛）与 §8 新增「传输层重试幂等性」风险行，v3 使「文档 ↔ 实现 ↔ 测试」三者一致，服务本轮全局目标「功能正确、稳健、可靠」。

---

> 版本：**v3**（iteration 2 方案设计节点刷新；收编 I-1/I-2/I-3、新增 I-4 与传输层重试幂等性记录） · 基线：工作树实代码逐一复核（附 `file:line`） · 历史 v2 评审记录整体保留以备审计。

---

## 评审结论（Review Verdict · iteration 2 复评）

**通过（Approved）。**

本轮复评以 **v3** 文档为对象，对照当前工作树实代码逐项核验其全部 `file:line` 引用与设计断言（见「评审记录 · iteration 2 复评 · 核验证据」16 项全部命中）：实盘下单的**可行性**由已实现并通过测试的写链路直接背书；安全护栏（默认关闭开关、每单确认、名义上限 1000 USDT、net_mode 反向单本地拦截、唯一 clOrdId 幂等、密钥单点脱敏、无密钥审计留痕）、前端左右分栏 + 下单面板接线、水合守门、桌面 `LIVE_*` 透传均与设计一致。**可行性 / 完备性 / 规模适配三维无 P0/P1**。

**一致性**维度本轮发现并**就地修复** 1 个 P1（C3-1：§3 `config.py` 名义上限括注与 §A/§C/§8 自相矛盾，已统一为「已落地 `1000.0`」）与 1 个 P2（C3-2：`<LiveBottomPanel>` 入参 `market` 文档过度指定，已对齐实现）。修复后**无 P0/P1 遗留**。

历史 v2 评审结论「放行条件」截至本轮复核：
1. C0-1 net_mode 反向单拦截 ✅ `trading.py:134-136`；
2. C1-1 `get_instrument_meta` 必做 ✅ `rest_client.py:226`；
3. C1-2 唯一 clOrdId + 在途禁用 / C1-3 逐单确认 ✅ `trading.py:61` + `OrderConfirmModal`；
4. C1-4 仅 USDT 本位 SWAP/FUTURES ✅ `order_builder.is_instrument_supported:52`；
5. C1-5 水合守门 ✅ `AccountModeSwitch.tsx:33-36`；
6. C1-6 桌面 `LIVE_*` 透传 ✅ `desktop/src/lib/config.js:61`；
7. C1-7 名义上限默认收紧至 1000 USDT ✅ `config.py:45`——**本轮 C3-1 已修正 §3 滞后表述**，v2 verdict 中「当前源码仍为 200000.0」一句为历史记录，现已不成立。

七项放行条件全部满足；设计无 P0/P1 遗留，实现与文档经本轮逐项核验一致，服务本轮全局目标「功能正确、稳健、可靠」。建议下游提交节点据本一致版本提交。

> 版本：**v4**（iteration 2 · Subtask #1 复评：对照工作树核验 v3 全部断言，修复 C3-1/C3-2 文档滞后，结论通过） · 评审人：资深评审（senior reviewer） · 维度：可行性 / 完备性 / 一致性 / 规模适配 · 复核基线：工作树实代码逐一回查（附 `file:line`）。历史 v2 / v3 评审记录整体保留以备审计。
