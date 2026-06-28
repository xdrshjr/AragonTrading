# 实盘持仓平仓 / 一键市价全平（live-position-close）开发方案

- **版本**：v2（评审已修复 · 有条件通过）
- **作者**：Subtask #0 · 方案设计
- **评审**：Subtask #1 · 方案评审与修复（评审记录与结论见下；P0/P1 已就地修复）
- **依赖既有特性**：实盘交易模式（`docs/plans/live-trading-mode/spec.md`，已落地的下单 / 设杠杆 / 查持仓 / 查成交全链路）
- **需求来源**：实盘当前只能「买多 / 买空」开仓，缺少对已有持仓的「平仓」与「一键市价全平」能力；需在当前持仓栏目为每条持仓增加平仓按钮（弹窗输入价格等信息的**挂单平仓**），并提供**一键市价全平**（按当前市价、二次确认后执行）。相关 OKX 接口必须真实调用并跑通。
- **OKX 接口参考**：<https://www.okx.com/docs-v5/zh/>（`POST /api/v5/trade/close-position`、`POST /api/v5/trade/order` 的 reduce-only / posSide 平仓语义）

---

## 评审记录（v2 · Subtask #1 方案评审与修复）

> **评审维度**：可行性（Feasibility）/ 完整性（Completeness）/ 一致性（Consistency）/ 适度性（Right-sizing）。
> **评审基线**：逐一核对方案依赖的既有实现——后端 `okx/trading.py`（`place_live_order`/`_would_reduce_net_position`/`_map_trade_code`/`_LOCAL_REJECT_CODES`/`_audit_values`）、`okx/trade_client.py`（`_post`/`place_order`，已具签名 POST 能力）、`okx/order_builder.py`（`build_order_payload`/`validate_order`/`snap_size`/`snap_price`）、`api/trading.py`（`_require_live_enabled`/`_resolve_account` 守门）、`db/repositories.py`（`LiveOrderAuditValues`/`create_live_order_audit`）、`okx/accounts.py`（`_normalize_position` 已含 `posSide/pos/avgPx/last/lever/marginMode`、`_map_okx_code`、`_redact`），前端 `LivePositionsTable`/`LiveBottomPanel`/`LiveTradePanel`/`OrderConfirmModal`/`tradingApi`/`tradingTypes`/`accountTypes.OkxAccountPosition`，以及 `apps/web/next.config.mjs` 的 `/api/py/:path* → /api/:path*` rewrite（转发全部方法，含 POST，新写路由可行）。
> **总体结论**：方向安全设计正确（对冲 `posSide` 锁侧、净持仓 `reduceOnly`、服务层以实时 `|pos|` 二次校验 `sz≤|pos|`），平仓独立链路、护栏取舍合理，与既有实盘纪律一致。**无 P0**；发现 **2 个 P1**（已就地修复）、6 个 P2（已记录，部分留待实现期处置）。

| 编号 | 维度 | 严重度 | 问题 | 处置 |
|---|---|---|---|---|
| P1-1 | 完整性 / 一致性 | **P1** | 平仓新增的本地拒码 `LIVE_CLOSE_REJECTED_LOCAL` / `LIVE_CLOSE_SIZE_EXCEEDS` / `LIVE_NO_POSITION` 未纳入 `_map_trade_code` 与本地拒集合。经核对 `accounts._map_okx_code` 对未知码一律返回 `OKX_ERROR`，故现状下这三码会在归一时被**拍平成 `OKX_ERROR`**——既违反 §4.1「这些码原样回传前端」的契约，又使 `place_live_order` 同款审计分类（`status = "rejected" if mapped in _LOCAL_REJECT_CODES else "error"`）把本地拒**误判为 `error`** 而非 `rejected`。 | **已修复**：§2.3 / §3 / §4.1 明确要求在 `trading.py` 新增 `_CLOSE_LOCAL_REJECT_CODES` 并扩展 `_map_trade_code`，使三码原样透传、审计判 `rejected`。 |
| P1-2 | 完整性 / 一致性 | **P1** | `validate_close` 仅校验「正价 + `sz>0` + `sz≤\|pos\|` + lotSz 取整」，**缺 `sz≥minSz`**，与既有 `validate_order`（已含 minSz 边界）不一致。后果一：部分限价平仓在 `0<sz<minSz` 时本地放行却必被 OKX 拒（51000 类），违反「明知会拒不发往真实资金接口」的边界校验纪律；后果二：当剩余仓位本身 `<minSz`（dust 尾仓）时，限价路径恒被拒，而方案未指明退路。 | **已修复**：§2.4 / §4.1 / §6 给 `validate_close` 增加 minSz 边界（不足抛 `LIVE_CLOSE_REJECTED_LOCAL`）；§2.6 明确尾仓不足 minSz 时引导用户用「市价整仓」退出（`close-position` 无 minSz 限制，可平任意残量）。 |
| P2-1 | 一致性 | P2 | 以 `aragonc` 前缀区分开 / 平仓不够严谨：`c` 是合法 hex 字符，理论上与开仓 `aragon`+hex 的 clOrdId 在边界上不可绝对区分（实际因毫秒时间戳高位长期为 `1...`，数千年内不会发生，且唯一性由 ms+seq 保证不受影响）。 | 保留：审计另存 `ord_type`(limit/market)，区分不依赖前缀；实现期可改非 hex 分隔符进一步去歧义（见 §5 备注）。 |
| P2-2 | 完整性 | P2 | limit 平仓返回的 `closedSz` 实为「**委托**平仓量」而非「**已成交**量」（挂单可能不成交）。 | 保留：§2.6 / §4.1 文案明确为「已提交挂单平仓 N 张」，避免误读为已平。 |
| P2-3 | 完整性 | P2 | `close_position` 仅判顶层 `code=="0"`；若 OKX 在 `data[0].sCode` 另带错误码，建议一并判（与 `place_order` 口径一致）。 | 保留：§2.2 注明实现期对 `data[0].sCode` 做兜底判定。 |
| P2-4 | 完整性 | P2 | `_post` 对 `TransportError` 的一次重试，在 close-position「首发已成交、响应丢失」场景可能二次触发并回「无持仓」假阴性。 | 保留：依赖 `clOrdId` 幂等；§7 新增 R11 说明该可接受残余风险。 |
| P2-5 | 一致性 | P2 | 新确认弹窗未说明复用既有 `OrderConfirmModal` / `useAppStore.liveAcknowledged` 确认范式，存在 UI 孤岛风险（CLAUDE.md §8.8）。 | 保留：§2.6 补注「确认 UX 与既有实盘下单确认范式 / 基元对齐」。 |
| P2-6 | 适度性 / 性能 | P2 | `LivePositionsTable` 为读全局 `liveTradingEnabled` 而引入 `config` 查询，应复用既有查询键避免重复轮询。 | 保留：§2.6 注明复用 `["trading","config",instId,accountId]` 查询键，命中 React Query 去重（已确认 `LiveTradePanel` 用此键、15s 轮询）。 |

---

## 一、Overview（概述）

本特性在既有「实盘交易模式」之上补齐**持仓的退出能力**。当前 `apps/web/components/Live/LivePositionsTable.tsx` 是**只读**的真实持仓表（5s 轮询 `GET /api/trading/positions`），用户能看到持仓却无法在此直接平仓；下单面板 `LiveTradePanel` 又只走「开仓」时序（`place_live_order`），其本地护栏 `_would_reduce_net_position`（net 模式反向单本地拒）与名义上限恰恰会**挡住**平仓这种减仓动作。因此平仓不能复用开仓链路，必须新增一条独立的、语义为「reduce / close」的写链路。

交付两类用户动作：(1) **挂单平仓**——持仓行点击「平仓」弹出弹窗，用户输入委托价格与平仓数量（默认全仓、可部分），后端以 reduce-only / posSide 限定的**限价单**挂出；(2) **一键市价全平**——持仓表顶部一个按钮，弹出列出全部待平持仓的确认弹窗，经**二次确认**后对每个持仓调用 OKX `close-position` 按当前市价整仓平掉。两类动作都复用既有实盘安全纪律：受 `LIVE_TRADING_ENABLED` 全局开关守门、唯一 `clOrdId` 防重、每次操作写 `live_order_audit` 留痕、错误码统一归一、密钥绝不外泄，且**绝不假成功**（OKX 真源，失败显式上抛）。

设计的核心约束是「方向安全」：平仓单一旦方向 / 数量算错，会从「减仓」变成「反向加仓」，在真实资金上是严重事故。为此平仓链路在多空对冲模式（`long_short_mode`）用 `posSide` 锁定被平的那一侧、在单向净持仓模式（`net_mode`）用 `reduceOnly=true`，并在服务层以「实时持仓的 `|pos|`」二次校验平仓数量不得超过现有持仓（`sz ≤ |pos|`），杜绝平成反向仓。

---

## 二、Technical design（技术设计）

### 2.1 架构与数据流

复用既有四层：Next 前端 → BFF 代理 `/api/py/*` → FastAPI `api/trading.py` → 服务层 `okx/trading.py` → OKX V5 私有/交易客户端。新增两条写路由，挂在既有 `trading` 路由前缀下：

```
持仓行「平仓」按钮 ─▶ ClosePositionModal ─▶ tradingApi.closePosition
  └▶ POST /api/py/trading/close ─▶ api.trading.trading_close
       └▶ okx.trading.close_live_position
            ├─ limit：build_close_order_payload ─▶ OKXTradeClient.place_order (POST /trade/order, reduceOnly/posSide)
            └─ market：OKXTradeClient.close_position (POST /trade/close-position, 整仓)

持仓表「一键市价全平」 ─▶ MarketCloseAllConfirm（二次确认）─▶ tradingApi.closeAll
  └▶ POST /api/py/trading/close-all ─▶ api.trading.trading_close_all
       └▶ okx.trading.close_all_live_positions
            └─ 遍历每个未平持仓 ─▶ OKXTradeClient.close_position（逐仓收集结果，单仓失败不中断全批）
```

### 2.2 平仓方向映射规则（一等安全约束）

设持仓行携带 `posSide`（`long|short|net`）、`pos`（带符号张数，net 模式靠符号判向）、`marginMode`（`cross|isolated`）。

- **平仓单 side**：平多 → `sell`；平空 → `buy`。
  - `net` 模式：`pos > 0`（净多）→ `sell`；`pos < 0`（净空）→ `buy`。
  - 对冲模式：`posSide=="long"` → `sell`；`posSide=="short"` → `buy`。
- **限价挂单平仓**（`POST /api/v5/trade/order`）：
  - 对冲模式（`long_short_mode`）：`{side, posSide=持仓 posSide, ordType:"limit", px, sz, tdMode=marginMode, clOrdId}`——**不**带 `reduceOnly`（`posSide` 已把该单限定为平该侧仓，避免部分账户模式对 `reduceOnly` 的兼容性问题）。
  - 净持仓模式（`net_mode`）：`{side, reduceOnly:true, ordType:"limit", px, sz, tdMode=marginMode, clOrdId}`——不带 `posSide`。
- **市价平仓**（单仓或全平，`POST /api/v5/trade/close-position`）：`{instId, posSide(对冲取持仓 posSide / 净持仓取 "net"), mgnMode=marginMode, autoCxl:true, clOrdId}`。该接口**按市价整仓平掉**，不接受 `sz`（故市价平仓 = 全平该持仓，UI 侧锁定数量输入），且**响应不返回 `ordId`**（返回 `instId/posSide/clOrdId`），以顶层 `code=="0"` 判成功。**（P2-3）** 与 `place_order` 口径一致，`close_position` 判成功时除顶层 `code` 外，若 `data[0]` 含非 `"0"` 的 `sCode` 也须按错误处理（兜底，以防 OKX 在逐项位另带错误码）。

### 2.3 平仓链路与开仓链路的护栏差异（关键）

平仓**不复用** `place_live_order`，新建独立函数，护栏取舍如下：

| 护栏 | 开仓 `place_live_order` | 平仓 `close_live_position` / `close_all_live_positions` |
|---|---|---|
| `LIVE_TRADING_ENABLED` 全局守门 | ✅ | ✅（写类，先于凭证读取，403 `LIVE_TRADING_DISABLED`） |
| 标的可下单（USDT 本位线性） | ✅ | 限价平仓 ✅（依赖 `lotSz/tickSz` 取整）；市价平仓 ❌跳过（`close-position` 无价量数学，OKX 自行整仓平，避免把非常规标的持仓困住） |
| `_would_reduce_net_position`（反向减仓本地拒） | ✅ | ❌**显式不应用**（平仓本就是减仓，这是动作目的） |
| 名义上限 `live_max_order_notional_usd` | ✅ | ❌**显式不应用**（平仓降低风险应始终允许，否则 >1000U 的持仓将无法平） |
| `sz ≤ |pos|`（不得平成反向仓） | —（开仓无此概念） | ✅ 新增本地校验，超出抛 `LIVE_CLOSE_SIZE_EXCEEDS`，绝不发往 OKX |
| 唯一 `clOrdId` 防重 | ✅ 前缀 `aragon` | ✅ 前缀 `aragonc`（便于审计区分开/平仓；区分实际以审计 `ord_type` 为准，见 P2-1） |
| `live_order_audit` 留痕（不含密钥） | ✅ | ✅（复用同表，`direction` 记被平方向，`ord_type` 记 limit/market） |

**（P1-1）错误码归一与审计分类必须扩展**：平仓本地拒码 `LIVE_CLOSE_REJECTED_LOCAL` / `LIVE_CLOSE_SIZE_EXCEEDS` / `LIVE_NO_POSITION` 必须在 `trading.py` 显式纳入「本地拒集合」与 `_map_trade_code`，否则会被 `accounts._map_okx_code` 拍平成 `OKX_ERROR`（既丢失 §4.1 的语义码契约，又使审计 `status` 误判为 `error`）。落地要求：

- 新增 `_CLOSE_LOCAL_REJECT_CODES = {"LIVE_CLOSE_REJECTED_LOCAL", "LIVE_CLOSE_SIZE_EXCEEDS", "LIVE_NO_POSITION"}`；
- `_map_trade_code` 在现有 `_LOCAL_REJECT_CODES` / `LIVE_TRADING_DISABLED` 透传分支基础上，**追加**「`code in _CLOSE_LOCAL_REJECT_CODES` 时原样返回」；
- 平仓时序复用与 `place_live_order` 同款的审计分类：`status = "rejected" if mapped in (_LOCAL_REJECT_CODES | _CLOSE_LOCAL_REJECT_CODES) else "error"`。这样本地拒（含 `sz>|pos|` / 无价 / 无持仓）写 `rejected`、OKX 业务失败写 `error`，与开仓口径一致。

### 2.4 单仓平仓时序（`close_live_position`）

1. 服务层生成唯一 `clOrdId`（`aragonc`+毫秒 hex+序号）。
2. 构造 `OKXTradeClient`，并发/顺序取：`account/config`（拿 `posMode`）、`account/positions`（拿实时该方向 `|pos|`，作为 `sz` 上限的可信来源——**绝不信前端传入的 pos**）。
3. 若该方向无可平持仓（`|pos|<=0`）→ 抛 `LIVE_NO_POSITION`。
4. **市价**：直接 `client.close_position(instId, posSide, mgnMode, autoCxl=True, clOrdId)`；`closedSz` 以本地 `|pos|` 回填。
5. **限价**：`_load_meta` + `ensure_instrument_supported`；`validate_close`（限价须有正价、取整后 `sz>0`、**`sz≥minSz`（P1-2，与 `validate_order` 一致，不足抛 `LIVE_CLOSE_REJECTED_LOCAL`）**、`sz≤|pos|`、按 `lotSz` 取整）；`build_close_order_payload`；`client.place_order(payload)`，取回 `ordId/clOrdId`。**尾仓退路（P1-2）**：当剩余 `|pos| < minSz`（dust）时限价路径必被拒，应在 UI 引导用户改用「市价整仓」退出（`close-position` 不受 minSz 约束，可平任意残量）。
6. 失败：`OKXError` → `_map_trade_code` 归一 + `_redact` 脱敏，按是否本地拒写 `rejected/error` 审计行后上抛 `OKXPrivateError`（经全局 `okx_error_handler` 回 502）。
7. 成功：写 `accepted` 审计行，返回 `{ok:true, mode, ordId, clOrdId, closedSz}`。

### 2.5 一键市价全平时序（`close_all_live_positions`）

1. 取 `posMode` 与 `positions`，过滤出 `pos != 0` 的未平持仓。
2. **逐仓**调用 `close_position`（对冲模式 `posSide` 取每行自身、净持仓模式取 `"net"`，`mgnMode` 取每行 `marginMode`），各自唯一 `clOrdId`，各自写审计行。
3. **单仓失败不中断全批**：捕获该仓 `OKXError`，归一+脱敏后并入 `results[i]={ok:false, code, message}`，继续平下一仓；成功并入 `{ok:true, clOrdId}`。
4. 仅当 `config/positions` 这种**整批前置**请求失败（如鉴权失败）才整体抛 502。
5. 返回 `{ok: failedCount==0, closedCount, failedCount, results[]}`（诚实汇总，不假成功）。

### 2.6 前端交互

- **持仓行**：新增「操作」列，每行一个「平仓」按钮 → 打开 `ClosePositionModal`（带入该行 `instId/posSide/pos/marginMode/avgPx/last`）。
- **持仓表头**：仅当存在未平持仓时显示「一键市价全平」按钮 → 打开 `MarketCloseAllConfirm`。
- **`ClosePositionModal`**：订单类型段「限价（默认 · 挂单平仓）/ 市价（整仓）」；限价时显示「委托价格」（默认预填标记价、按 `tickSz` 取整）+「平仓数量（张）」（默认全仓 `|pos|`、可改、`≤|pos|`、按 `lotSz` 取整）；市价时锁定数量并提示「市价平仓将平掉该持仓全部 N 张，按当前盘口成交、可能有滑点，并自动撤销该合约未成交挂单」；实时预览「预计实现盈亏 / 名义价值 / 剩余持仓」。该弹窗自身即为平仓确认（提交在途禁用防重）。**（P2-5）** 该确认弹窗应复用既有实盘下单的确认范式 / UI 基元（`OrderConfirmModal` 的 Modal + acknowledge 结构、`useAppStore.liveAcknowledged` 心智模型），避免另起 UI 孤岛。**（P2-2）** 限价提交成功后文案为「已提交挂单平仓 N 张」（挂单未必即时成交，`closedSz` 表「委托量」而非「已平量」）。**（P1-2）** 当 `|pos| < minSz` 时禁用限价、引导用户用市价整仓退出。`instMeta` 经 `tradingApi.config(行.instId, accountId)` 获取（多持仓可能跨标的，必须按行 `instId` 取元数据）。
- **`MarketCloseAllConfirm`（二次确认）**：列出全部待平持仓（合约 / 方向 / 张数 / 标记价 / 浮盈）；底部强制勾选「我已知晓将以市价一键平掉以上全部持仓，结果不可撤销」；「确认市价全平」按钮在勾选后启用，**首次点击进入二次确认态**（按钮文案变为「再次确认 · 立即全平」），**再次点击**才真正执行——即「勾选 + 二次点击」三道闸构成显式二次确认。
- 任一平仓成功后 `invalidate` 查询键 `["trading","positions"]`、`["trading","fills"]`、`["trading","config"]`，使持仓 / 成交 / 可用额刷新。
- 平仓按钮与全平按钮在 `liveTradingEnabled===false` 时禁用并提示（`liveTradingEnabled` 取自 `tradingApi.config`，全局标志）。**（P2-6）** 该门控查询须复用既有查询键 `["trading","config",instId,accountId]`（`LiveTradePanel` 已以此键、15s 轮询 config），命中 React Query 去重，避免为读一个全局布尔而新增重复轮询。

---

## 三、File / module change plan（文件 / 模块变更计划）

### 后端（services/quant）

| 文件 | 新建/修改 | 一句话意图 |
|---|---|---|
| `app/okx/order_builder.py` | 修改 | 新增 `ClosePositionInput` 值对象、`_close_side`、`build_close_order_payload`（对冲用 posSide / 净持仓用 reduceOnly）、`validate_close`（限价正价 + 取整后 **`sz≥minSz`（P1-2）** + `sz≤|pos|` 本地校验）。 |
| `app/okx/trade_client.py` | 修改 | 新增 `close_position(inst_id, pos_side, mgn_mode, *, auto_cxl=True, cl_ord_id=None)`，POST `/api/v5/trade/close-position`，以顶层 `code=="0"` 判成功（**兼判 `data[0].sCode`，P2-3**）。 |
| `app/okx/trading.py` | 修改 | 新增 `_CLOSE_LOCAL_REJECT_CODES` 并扩展 `_map_trade_code`（**P1-1**：使平仓本地拒码原样透传、审计判 `rejected`）、`_gen_close_cl_ord_id`、`_abs_open_pos`、`_close_audit_values`、`close_live_position`（单仓限价/市价平仓时序）、`close_all_live_positions`（逐仓收集结果），护栏差异见 §2.3。 |
| `app/schemas.py` | 修改 | 新增 `ClosePositionIn/ClosePositionOut/CloseAllIn/CloseAllResultRow/CloseAllOut` Pydantic 契约。 |
| `app/api/trading.py` | 修改 | 新增 `POST /trading/close`、`POST /trading/close-all` 两路由（写类，`_require_live_enabled` 守门 + `_resolve_account`）。 |

### 前端（apps/web）

| 文件 | 新建/修改 | 一句话意图 |
|---|---|---|
| `lib/tradingTypes.ts` | 修改 | 新增 `ClosePositionReq/ClosePositionResult/CloseAllResult/CloseAllResultRow` 类型（与后端 camelCase 对齐）。 |
| `lib/tradingApi.ts` | 修改 | 新增 `closePosition(req)` → `POST /trading/close`、`closeAll(accountId?)` → `POST /trading/close-all`。 |
| `lib/closePosition.ts` | 新建 | 纯计算：`defaultCloseSize`（全仓默认、对齐 lotSz）、`validateCloseSize`（`>0 && ≤|pos|`）、`estimateRealizedPnl`（多/空对称按 close px 估实现盈亏）。 |
| `components/Live/ClosePositionModal.tsx` | 新建 | 单仓平仓弹窗：限价/市价段、价格 + 数量输入、盈亏预览、提交调 `tradingApi.closePosition`。 |
| `components/Live/MarketCloseAllConfirm.tsx` | 新建 | 一键全平二次确认弹窗：列出全部持仓、勾选 + 二次点击执行，调 `tradingApi.closeAll`。 |
| `components/Live/LivePositionsTable.tsx` | 修改 | 新增「操作」列「平仓」按钮 + 表头「一键市价全平」按钮，挂接两弹窗与成功后 `invalidate`；接收新增 `instId` 入参用于 `liveTradingEnabled` 门控；更新顶部「只读」注释。 |
| `components/Live/LiveBottomPanel.tsx` | 修改 | 向 `LivePositionsTable` 传入 `instId`（门控 `liveTradingEnabled`）。 |

### 测试（与上述同目录新增）

| 文件 | 新建 | 一句话意图 |
|---|---|---|
| `services/quant/tests/test_okx_close_builder.py` | 新建 | 覆盖 `build_close_order_payload`/`_close_side`/`validate_close` 四象限与边界。 |
| `services/quant/tests/test_trading_close.py` | 新建 | 以打桩客户端覆盖 `close_live_position`/`close_all_live_positions` 与 `/trading/close[-all]` 路由（含 403 守门）。 |
| `apps/web/lib/closePosition.test.ts` | 新建 | 覆盖 `closePosition.ts` 三个纯函数。 |
| `apps/web/components/Live/ClosePositionModal.test.tsx` | 新建 | 覆盖数量校验、市价锁量、提交调用。 |
| `apps/web/components/Live/MarketCloseAllConfirm.test.tsx` | 新建 | 覆盖二次确认门控（未勾选 / 单击未执行 / 二次点击执行）。 |

---

## 四、Interface design（接口设计）

### 4.1 `POST /api/py/trading/close`（单仓平仓，写类·受 LIVE_TRADING_ENABLED 守门）

请求体 `ClosePositionIn`：

```jsonc
{
  "accountId": 12,            // 可选，缺省回落默认账户
  "instId": "BTC-USDT-SWAP",  // 必填
  "posSide": "long",          // long | short | net（持仓行带出）
  "pos": 5,                   // 带符号张数（net 模式判向；后端仅作判向参考，sz 上限以实时持仓为准）
  "mgnMode": "cross",         // cross | isolated（持仓行 marginMode）
  "ordType": "limit",         // limit（挂单平仓）| market（整仓市价平）
  "px": 65000.0,              // 限价必填、>0；市价忽略
  "sz": 3                     // 可选；限价部分平仓张数，缺省=全仓 |pos|；市价忽略
}
```

校验（Pydantic + `@model_validator`）：`instId` 1..32；`posSide∈{long,short,net}`；`ordType∈{limit,market}`；`mgnMode∈{cross,isolated}`；`px>0` 且 `ordType=="limit"` 时必填；`sz>0`（给定时）。**`sz≥minSz` / `sz≤|pos|` 等依赖合约元数据与实时持仓的边界在服务层 `validate_close` 校验（P1-2）**，Pydantic 层不掌握 `minSz`/`|pos|`，仅做形态校验。

响应 `ClosePositionOut`：

```jsonc
{ "ok": true, "mode": "limit", "ordId": "1234", "clOrdId": "aragonc...", "closedSz": 3, "code": null, "message": null }
```

错误体统一 `{error:{code,message}}`：`LIVE_TRADING_DISABLED`(403)、`ACCOUNT_NOT_FOUND`(404)、`ACCOUNT_NOT_CONFIGURED`(400)、`LIVE_CLOSE_REJECTED_LOCAL`/`LIVE_CLOSE_SIZE_EXCEEDS`/`LIVE_NO_POSITION`/`LIVE_INSTRUMENT_UNSUPPORTED`(本地拒，经 OKXPrivateError→502)、以及归一后的 OKX 业务码（`INSUFFICIENT_BALANCE`/`ORDER_PARAM_INVALID`/`OKX_AUTH_*` 等）。**这些平仓本地拒码须经 §2.3（P1-1）扩展后的 `_map_trade_code` 原样透传，不得被拍平为 `OKX_ERROR`。**

### 4.2 `POST /api/py/trading/close-all`（一键市价全平，写类·守门）

请求体 `CloseAllIn`：`{ "accountId": 12 }`（均可选）。

响应 `CloseAllOut`（逐仓诚实汇总，单仓失败不致整体失败）：

```jsonc
{
  "ok": false, "closedCount": 1, "failedCount": 1,
  "results": [
    { "instId": "BTC-USDT-SWAP", "posSide": "long", "ok": true,  "clOrdId": "aragonc...", "code": null, "message": null },
    { "instId": "ETH-USDT-SWAP", "posSide": "short","ok": false, "clOrdId": "aragonc...", "code": "INSUFFICIENT_BALANCE", "message": "..." }
  ]
}
```

整批前置失败（鉴权/连通）仍回 502 `{error:{code,message}}`。

### 4.3 客户端方法签名

- 后端 `OKXTradeClient.close_position(self, inst_id: str, pos_side: str, mgn_mode: str, *, auto_cxl: bool = True, cl_ord_id: str | None = None) -> dict`。
- 前端 `tradingApi.closePosition(req: ClosePositionReq): Promise<ClosePositionResult>`、`tradingApi.closeAll(accountId?: number): Promise<CloseAllResult>`。
- 前端纯函数：`defaultCloseSize(pos, lotSz): number`、`validateCloseSize(sz, pos, minSz, lotSz): {valid:boolean; error?:string}`、`estimateRealizedPnl(isLong, avgPx, closePx, sz, ctVal): number`。

---

## 五、Data model（数据模型）

- **无新增数据库表 / 字段**：平仓审计复用既有 `live_order_audit`（`LiveOrderAuditValues`）。约定：`direction` 记被平持仓方向（`long`/`short`，net 模式按 `pos` 符号映射）；`ord_type` 记 `limit`/`market`；`px`/`sz` 记委托价/平仓张数（市价单 `px=None`、`sz=|pos|`）；`tp_trigger_px`/`sl_trigger_px`/`lever` 置空；`status∈{accepted,rejected,error}`；`cl_ord_id` 用 `aragonc` 前缀。**（P2-1）** 严格区分开 / 平仓应以审计列 `ord_type`（limit/market）+ 平仓语义为准——`aragonc` 仅作可读约定，因 `c` 是合法 hex 字符，与开仓 `aragon`+hex 前缀在边界上不能绝对区分（实际毫秒时间戳高位长期为 `1...`，数千年内不会撞，且唯一性由 `ms+seq` 保证不受影响）；实现期可改用非 hex 分隔符进一步去歧义，无需改表。
- **后端值对象**（`order_builder.py`，frozen dataclass）`ClosePositionInput`：`inst_id/pos_side/pos/mgn_mode/ord_type/px/sz`。
- **OKX 请求体形**：
  - 限价平仓（对冲）：`{instId, tdMode, side, posSide, ordType:"limit", px, sz, clOrdId}`。
  - 限价平仓（净持仓）：`{instId, tdMode, side, reduceOnly:true, ordType:"limit", px, sz, clOrdId}`。
  - 市价平仓：`{instId, mgnMode, posSide, autoCxl:true, clOrdId}`。
- **前端类型**：见 §4；`ClosePositionResult.closedSz` 市价单回填本地 `|pos|`（OKX 不回 `ordId/closedSz`）。

---

## 六、Testing & acceptance criteria（测试与验收标准）

**单元 / 集成（离线，进 CI）：**

1. `build_close_order_payload` 四象限：对冲平多 `sell+posSide=long`、对冲平空 `buy+posSide=short`、净多 `sell+reduceOnly`、净空 `buy+reduceOnly`；`sz` 按 `lotSz` 取整、`px` 按 `tickSz` 取整；对冲单**不含** `reduceOnly`、净单**不含** `posSide`。
2. `validate_close`：限价缺价 → `LIVE_CLOSE_REJECTED_LOCAL`；取整后 `sz<minSz` → `LIVE_CLOSE_REJECTED_LOCAL`（P1-2）；`sz>|pos|` → `LIVE_CLOSE_SIZE_EXCEEDS`；`sz<=0` 拒。
3. `close_live_position`：限价路径调 `place_order`、市价路径调 `close_position`；无持仓 → `LIVE_NO_POSITION`；OKX 拒单 → 归一码 + 写 `rejected/error` 审计 + 不假成功；成功写 `accepted` 审计。**`_map_trade_code` 对 `LIVE_CLOSE_REJECTED_LOCAL`/`LIVE_CLOSE_SIZE_EXCEEDS`/`LIVE_NO_POSITION` 原样返回（不变 `OKX_ERROR`）、且本地拒三码审计 `status==rejected`（P1-1）。**
4. `close_all_live_positions`：多仓逐仓平、单仓失败并入 `results` 且不中断、`ok=failedCount==0`；空持仓返回 `closedCount=0`。
5. 路由守门：`LIVE_TRADING_ENABLED=false` 时 `/trading/close` 与 `/trading/close-all` 均 403 `LIVE_TRADING_DISABLED`（先于凭证读取）。
6. 前端纯函数：`defaultCloseSize/validateCloseSize/estimateRealizedPnl` 数值与边界（含 net 负 pos、lotSz 取整、空/超额）。
7. 组件（`NODE_ENV=test`，原生 DOM 断言）：`MarketCloseAllConfirm` 未勾选时确认禁用、单击仅进入二次态、二次点击才触发 `closeAll`；`ClosePositionModal` 市价锁量、`sz>|pos|` 报错且禁用提交、提交调 `closePosition`。

**真实联调（手动 opt-in，不进 CI，对齐既有实盘联调纪律）：** 需 `LIVE_TRADING_ENABLED=true` + 就绪默认账户 + 出口 IP 在 OKX 白名单 + 经代理连通。用默认凭证以**最小张数**实测：开一笔最小仓 → 行内「平仓」限价部分平（OKX `orders-pending` 可见挂单）→「一键市价全平」整仓平（OKX 持仓归零、`fills` 出现成交、`live_order_audit` 三态留痕可核对）。**全程不得对无关持仓动手**，绝不以假数据兜底。

**验收 Definition of Done：** 上述离线用例全绿；`pnpm test`（后端 pytest 不含 network + 前端 vitest）通过；真实联调可在文档记录的步骤下平掉一笔自建最小仓；接口、审计、错误码与本方案一致。

---

## 七、Risks & mitigations（风险与缓解）

- **R1 方向/数量算错致反向加仓（最高危）**：对冲用 `posSide` 锁侧、净持仓用 `reduceOnly`，服务层以**实时持仓** `|pos|` 二次校验 `sz≤|pos|`，单测覆盖四象限，真实联调先用最小仓验证方向。
- **R2 `close-position` 不回 `ordId`** 致前端误判失败：以顶层 `code=="0"` 判成功，`closedSz` 回填本地 `|pos|`，UI 文案「已提交市价平仓」。
- **R3 `reduceOnly` 在部分账户模式被拒**（参数错 51000）：对冲模式**不带** `reduceOnly`（`posSide` 已限定平仓），仅净持仓模式带；错误码归一为 `ORDER_PARAM_INVALID` 友好提示。
- **R4 名义上限/反向减仓护栏误伤平仓**：平仓走**独立链路**，显式**不**应用 `live_max_order_notional_usd` 与 `_would_reduce_net_position`，仅保留 `sz≤|pos|`。
- **R5 重复点击重复平仓**：唯一 `clOrdId` 防重 + 提交在途禁用按钮 + 成功后 `invalidate` 刷新持仓。
- **R6 `autoCxl` 撤掉用户其它挂单**：弹窗明确提示「市价平仓会自动撤销该合约未成交挂单」；`autoCxl` 仅作用于被平合约。
- **R7 部分平仓后 UI 不刷新**：成功后 `invalidate` `positions/fills/config` 三键，5s 轮询亦兜底。
- **R8 市价滑点**：UI 明示「按当前盘口成交、可能有滑点」；名义/盈亏仅为按最新价的**估算**。
- **R9 跨标的持仓元数据错配**：`ClosePositionModal` 按**该行 `instId`** 取 `tradingApi.config`，不沿用图表当前 `instId`。
- **R10 一键全平中途部分失败**：逐仓 `try/except` 收集 `results`，`ok` 仅在全成功为真，前端逐行展示失败原因，绝不汇报「全部成功」。
- **R11 传输层重试致假阴性（P2-4）**：`_post` 对 `TransportError` 做一次重试，若 close-position「首发已实际成交、响应丢失」，重试会二次触发并以「无持仓」类错误返回——表现为持仓已平却报错的**假阴性**（非假成功，资金安全方向无害）。缓解：依赖唯一 `clOrdId` 幂等，UI 报错后 5s 轮询会显示持仓已归零；该残余风险与既有 `place_live_order`（同款 `_post`）一致，可接受。

---

## 附：实施提示（给下游开发节点）

- 写路由必须放在 `_require_live_enabled(settings)` 之后再 `_resolve_account`，与既有 `/trading/order` 一致。
- `close_live_position`/`close_all_live_positions` 的 `OKXTradeClient` 须 `try/finally aclose()`；错误经 `_map_trade_code` + `_redact`（复用 accounts 实现）后上抛 `OKXPrivateError`。
- 前端组件测试遵循 `[[aragon-web-vitest-node-env]]`：`NODE_ENV=test`，jest-dom 未全局 setup，断言用原生 DOM（如 `(btn as HTMLButtonElement).disabled`）。
- 不要把开仓的 `_would_reduce_net_position` 误加到平仓路径；不要让平仓复用 `place_live_order`。
- 提交阶段精确 `git add` 上述清单文件，禁用 `git add -A`，避免带入 `.agentmesh/` 等运行期产物。

---

## 评审结论（v2 · Subtask #1）

**结论：有条件通过（Approved with conditions）。**

方案在四个维度均成立：**可行性**——所依赖的后端链路（`OKXTradeClient._post`/`place_order`、`_map_trade_code`/`_redact`、`LiveOrderAuditValues`、`/api/py/*` rewrite）与前端基元（`tradingApi`、`OkxAccountPosition` 已含 `posSide/pos/marginMode/avgPx/last`、确认弹窗范式）均已存在，新增 `close-position` 客户端方法与两条写路由可直接落地；**完整性**——平仓独立链路、护栏取舍、逐仓诚实汇总、错误归一与审计三态覆盖周全，本轮已补齐 2 个 P1 缺口；**一致性**——守门 / 防重 / 脱敏 / 审计与既有实盘纪律一致，方向安全约束（`posSide` / `reduceOnly` / `sz≤|pos|`）正确；**适度性**——未过度设计，per-position 市价整仓选项是合理超集而非膨胀。

P0：无。P1：2 项（P1-1 错误码归一与审计分类、P1-2 `validate_close` 缺 minSz 与尾仓退路）**已在 v2 正文就地修复**。

**放行条件（下游须满足）：**

1. **（落地 P1-1）** `trading.py` 须新增 `_CLOSE_LOCAL_REJECT_CODES` 并扩展 `_map_trade_code`，平仓本地拒码原样透传、审计判 `rejected`（§2.3 / §6 测点 3 已要求覆盖）。
2. **（落地 P1-2）** `validate_close` 须含 `sz≥minSz` 边界，UI 在尾仓 `<minSz` 时引导市价整仓（§2.4 / §6 测点 2 已要求覆盖）。
3. **（真实联调）** 须按 §6「真实联调」用默认凭证以最小张数完成一次端到端实测（开仓→限价部分平→一键市价全平→OKX 持仓归零 / `fills` / `live_order_audit` 三态可核），并将步骤与结果记录在案——这是「API 必须真实调用并跑通」需求的验收闭环，离线 CI 不能替代。
4. **（建议处置 P2）** P2-1～P2-6 在实现期酌情落实（`data[0].sCode` 兜底判、确认弹窗复用既有范式、`config` 查询键去重、文案澄清「委托量 vs 已平量」等），不作放行硬条件，但应在代码 Review（Subtask #3）逐项核对。

满足以上条件即视为通过，可进入 Subtask #2 代码开发。
