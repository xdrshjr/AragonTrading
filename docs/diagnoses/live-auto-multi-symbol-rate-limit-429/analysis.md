# Live multi-symbol auto-trade start trips OKX 429 rate limit

> Subtask #0 — 问题定位与分析。本节不写修复代码。Bug slug: `live-auto-multi-symbol-rate-limit-429`。

## Problem description

启动多标的自动交易（一次选多个标的、点「启动分组」）时，后端把大量 OKX **签名私有**
请求几乎在同一毫秒窗口内并发打出，触发 OKX 对该 API Key / UID 的限流，返回 HTTP
`429 Too Many Requests`。私有客户端**既不限流、也不对 429 退避重试**，因此 429 被原样
抛到 API 层，整组启动直接失败（HTTP 502）。

- 用户期望：能同时挂多个标的自动交易而不报限流。
- 用户给出的修复方向：**不要一次并发调这么多，排队并发，并发上限 2，每 2 个一组去调用。**
- 单标的旧路径几乎不触发（仅 3 个签名请求）；标的越多越容易复现，模拟盘（`x-simulated-trading`）
  限频更低、更易撞线。

## Reproduction steps

前置条件：
- 在设置中心配置一个有效 OKX 账户（实盘或模拟盘均可），凭证 `secret_key` / `passphrase` 完整。
- 环境变量 `LIVE_AUTO_TRADING_ENABLED=1` 与 `LIVE_TRADING_ENABLED=1` 同时开启
  （双闸守门见 `services/quant/app/api/live_auto.py:34-39`），否则会先被 403 拦下而到不了限流路径。

启动后端（Windows，README §运行）：

```powershell
cd services/quant
.venv\Scripts\uvicorn app.main:app --port 18760 --reload
```

触发分组启动（成员数取 6~8，越多越稳定复现；上限默认 8，见 `config.py:65`）：

```bash
curl -X POST http://127.0.0.1:18760/api/live-auto/group/start \
  -H "Content-Type: application/json" \
  -d '{
    "strategyId": 1, "bar": "5m", "allocationMode": "even",
    "members": [
      {"instId": "BTC-USDT-SWAP",  "allocationPct": 12},
      {"instId": "ETH-USDT-SWAP",  "allocationPct": 12},
      {"instId": "SOL-USDT-SWAP",  "allocationPct": 12},
      {"instId": "XRP-USDT-SWAP",  "allocationPct": 12},
      {"instId": "DOGE-USDT-SWAP", "allocationPct": 12},
      {"instId": "ADA-USDT-SWAP",  "allocationPct": 12},
      {"instId": "LTC-USDT-SWAP",  "allocationPct": 12},
      {"instId": "BNB-USDT-SWAP",  "allocationPct": 12}
    ]
  }'
```

观察到的失败（OKX 回 429 时）：分组启动整体失败，API 返回 HTTP 502：

```json
{"error": {"code": "OKX_HTTP_ERROR", "message": "OKX HTTP 429"}}
```

UI 表现为「启动失败 / OKX HTTP 429」，整组无一标的启动成功（在 `_gather_context` 阶段就抛错，
尚未进入下单，故无残留持仓）。

### 最小复现（无需真实下单 / 真实 429）

下面两个独立断言即可证明根因，且不依赖 OKX 真实限流时序：

1. **「无节流」证明**——统计同时在途的签名请求数。用一个计数桩替换
   `OKXTradeClient.get_account_config` / `get_balance`，在进入时 `inflight += 1`、`await sleep`、
   退出时 `inflight -= 1`，记录峰值；以 8 个成员调用 `_gather_context`，峰值会达到约 `2N`（不是 ≤2），
   证明启动期签名请求无任何并发上限。

2. **「429 不退避」证明**——用 `httpx.MockTransport` 让首个请求返回 `429`、第二个返回正常 `200`，
   注入 `OKXPrivateClient`，调用 `get_account_config()`：当前实现会**立即**抛
   `OKXPrivateError("OKX_HTTP_ERROR", "OKX HTTP 429")`，不会重试到第二个 `200`
   （对比公共客户端 `OKXRestClient._request` 会退避重试并最终成功）。

## Root cause analysis

### 直接原因：启动瞬间把签名私有请求一次性打爆

入口 `POST /live-auto/group/start` 把 `body.members`（最多 `live_auto_max_group_symbols=8`）
组装成 `GroupStartRequest` 并调 `start_group`：

- `services/quant/app/api/live_auto.py:101-126` — 路由构造请求并 `await get_live_auto_manager().start_group(req)`。
- `services/quant/app/live_auto/manager.py:170` — `start_group` 内调 `await self._gather_context(creds, req, members)`。

`_gather_context` 把 N 个标的的 `fetch_trading_config` **全部并发**，再加一发持仓查询：

```python
# services/quant/app/live_auto/manager.py:207-210
configs = await asyncio.gather(*[
    fetch_trading_config(creds, req.account_row.id, m.inst_id, settings=req.settings)
    for m in members])                          # N 个标的全部并发
positions = await fetch_live_positions(creds)   # 再 +1 个 /account/positions
```

而每个 `fetch_trading_config` 内部又并发两发签名私有 GET，且**每次都 new 一个独立的
`OKXTradeClient`**（各自一个 httpx client / 连接池，连共享限流的可能性都没有）：

```python
# services/quant/app/okx/trading.py:462-469
async def fetch_trading_config(creds, account_id, inst_id, *, settings):
    client = OKXTradeClient(creds.api_key, creds.secret_key, creds.passphrase)   # :465 每标的一个新 client
    try:
        config, balance = await asyncio.gather(
            client.get_account_config(), client.get_balance())                   # :467 两发签名私有 GET
        meta = await _load_meta(inst_id)        # 公共 REST（带缓存/节流，非瓶颈）
        last_px = await get_rest_client().get_ticker(inst_id)  # 公共 REST（带节流，非瓶颈）
```

`fetch_live_positions` 同样 new 一个新 client 再发一发签名 GET：

```python
# services/quant/app/okx/trading.py:319-323
async def fetch_live_positions(creds):
    client = OKXTradeClient(...)                # :321 又一个新 client
    positions = await client.get_positions()    # :323 /account/positions（签名私有）
```

**签名私有请求总数 = N×2 + 1**（`/account/config` + `/account/balance` 每标的各一，再加一发
`/account/positions`）。N=8 时约 **17** 个签名请求在同一窗口并发，且分散在 N+1 个互不相识的
httpx client 上。OKX 私有接口按端点 / 按 UID 限频（`account/*` 大致 5~10 次 / 2 秒量级），
这种突发直接超限 → 429。

### 根因：签名私有客户端既不限流、也不对 429 退避重试

429 落在 `httpx.HTTPStatusError` 分支，被**立即**原样抛出，没有任何缓冲：

```python
# services/quant/app/okx/private_client.py:111-127
for attempt in range(2):
    ...
    try:
        resp = await client.get(path, params=params, headers=headers)
        resp.raise_for_status()
        ...
    except httpx.TransportError as exc:   # :122 仅传输层异常重试
        last_exc = exc
    except httpx.HTTPStatusError as exc:   # :124 429 落这里 —— 立即抛，不退避不重试
        raise OKXPrivateError("OKX_HTTP_ERROR",
                              f"OKX HTTP {exc.response.status_code}") from exc
```

写客户端 `OKXTradeClient._post` 同形（同样无限流、无 429 退避）：

```python
# services/quant/app/okx/trade_client.py:35-46
except httpx.TransportError as exc:    # :42 仅传输层异常重试
    last_exc = exc
except httpx.HTTPStatusError as exc:    # :44 429 立即抛
    raise OKXPrivateError("OKX_HTTP_ERROR", f"OKX HTTP {exc.response.status_code}") from exc
```

**关键对比**——公共客户端 `OKXRestClient` 两样都有（节流器 + 429 指数退避）：

```python
# services/quant/app/okx/rest_client.py:38-53  最小请求间隔节流（0.12s）
class _RateLimiter:
    def __init__(self, min_interval: float = 0.12): ...
    async def acquire(self): ...   # 串行化、保证最小间隔

# services/quant/app/okx/rest_client.py:95-99  每请求先取节流令牌，429 指数退避重试
await self._limiter.acquire()
resp = await client.get(path, params=params)
if resp.status_code == 429:
    await asyncio.sleep(0.5 * (2 ** attempt)); continue   # 退避后重试
```

签名私有路径完全没有这两层，所以 OKX 一回 429 就把 `OKX_HTTP_ERROR` 抛到顶层；
经全局错误处理器包成 **HTTP 502**：

```python
# services/quant/app/main.py:65-69
@app.exception_handler(OKXError)
async def okx_error_handler(request, exc):
    return JSONResponse(status_code=502, content={"error": {"code": exc.code, "message": exc.message}})
```

（`OKXPrivateError` 是 `OKXError` 子类，故被此处理器统一捕获。）

### 次要放大点：账户级数据被**逐标的重复拉取**

`fetch_trading_config` 是为「单标的下单面板」设计的，把**账户级**的 `account/config`（`posMode`）
与 `account/balance`（`availUsdt`）和**标的级**的 meta / lastPx 打包在一起。但 config / balance
对同一账户的所有成员是**完全相同**的，分组路径却对每个成员各拉一遍。证据是
`_gather_context` 拿到 N 份 config 后只用 `next(...)` 取第一份的 `availUsdt`、其余直接丢弃：

```python
# services/quant/app/live_auto/manager.py:211-212
config_map = {m.inst_id: cfg for m, cfg in zip(members, configs)}
avail = next((cfg.get("availUsdt") or 0.0 for cfg in configs), 0.0)   # 只用第一份，其余 balance 纯浪费
```

讽刺的是 `_gather_context` 的 docstring（`manager.py:206`）写着「账户可用 USDT 与全账户持仓
**整组各取一次**」——设计意图本就是账户级数据取一次，但因为复用了按标的封装的
`fetch_trading_config`，实际把 `account/config` + `account/balance` 各重复拉了 N 次。
这意味着 **2N 个签名请求里有 2(N-1) 个是冗余的**。

### 其他放大因素

- 标的越多越容易触发；单标的旧路径仅 3 个签名请求（1×2 + 1），几乎不撞线。
- 模拟盘（`x-simulated-trading:1`，`config.py:39` `okx_account_demo`）私有限频通常比实盘更低。
- 同账户的下单面板、盯市轮询若同时在跑，请求叠加到同一 UID 限额上。
- 同形隐患：账户校验路径 `services/quant/app/okx/accounts.py:105`
  也是 `asyncio.gather(config, balance, positions)` 走无限流的 `OKXPrivateClient._get`
  （仅 3 发，不是分组突发，但同样无 429 退避，属同类技术债）。

## Impact surface

- **直接受影响**：`POST /api/live-auto/group/start`（多标的自动交易分组启动）。成员数越多越易整组失败，
  对依赖一次性挂多标的的用户基本不可用。
- **同根代码受影响（潜在）**：所有走签名私有 / 交易客户端的端点——下单 `place_live_order`、平仓
  `close_live_position` / `close_all_live_positions`、设杠杆、查持仓 / 成交 / 挂单
  （`services/quant/app/okx/trading.py`、`trade_client.py`、`private_client.py`）。它们目前都缺
  429 退避，一旦 UID 因任何原因被限流，会直接把 `OKX HTTP 429` 抛给用户而非自愈。
- **不受影响**：纸面 / 模拟批量启动（`paper/manager.py` 不走 OKX 签名私有请求，确认无此突发）；
  公共行情拉取（`rest_client.py` 自带节流 + 429 退避）。

## Candidate fixes

| 方案 | 做法 | 是否满足用户诉求（并发=2） | 侵入面 | 风险 | 工作量 |
|---|---|---|---|---|---|
| A. 启动 fan-out 限流到 2 | 在 `_gather_context` 用 `asyncio.Semaphore(2)`（或每 2 个一组分块 gather）限制同时在途的 `fetch_trading_config`，并把 `fetch_live_positions` 纳入同一并发预算。可加 `live_auto_start_concurrency=2` 配置项。 | ✅ 直接对应「并发限制为 2，每 2 个一组」 | 小（仅 `manager.py` 一个方法） | 低 | 小 |
| B. 私有/交易客户端加节流 + 429 退避 | 给 `OKXPrivateClient._get` / `OKXTradeClient._post` 加一层 429 指数退避重试（镜像 `rest_client.py:98-99`），并引入按 UID 共享的 `_RateLimiter`；分组内复用同一 client 而非每标的 new。 | ⚠️ 间接（治本但不等于「2 个一组」语义） | 中（触及所有实盘签名热路径） | 中（需回归全部下单/平仓/查询路径） | 中 |
| C. 去重账户级请求 | 分组启动只拉**一次** `account/config` + `account/balance` + `account/positions`（账户级），每成员仅走公共 meta / ticker（带缓存 + 节流）。把签名突发从 2N+1 降到常数 3，与 `_gather_context` docstring「整组各取一次」的原意一致。 | ⚠️ 间接（从源头消除突发，但非显式队列） | 中（需把 `fetch_trading_config` 拆出账户级 vs 标的级，或新增分组专用聚合函数） | 中（改下单面板复用的函数需谨慎，建议新增函数而非改旧函数） | 中 |

## Recommended fix

**以方案 A 为主（落地用户明确要求的「并发上限 2，每 2 个一组」），并补方案 B 的 429 退避作为兜底。**

理由：
1. **A 精确命中用户诉求与触发点**。把 `_gather_context` 中 `fetch_trading_config` 的 fan-out
   用 `asyncio.Semaphore(2)` 串成「每 2 个一组排队」，瞬时签名并发从 ~2N 降到 ~4（2 个成员 × 2 发），
   稳稳落在 OKX `account/*` 限额内。改动集中在 `manager.py:207-210` 一处，侵入面最小、风险最低，
   且不破坏现有两段式门禁 / 整组回滚契约。并发数建议做成配置项 `live_auto_start_concurrency`（默认 2）。
2. **补一小段 B（仅 429 退避，不强求全局限流器）**作为防御纵深。即便并发已限到 2，仍可能因下单面板 /
   盯市轮询叠加而偶发 429；在 `private_client._get` / `trade_client._post` 的 `HTTPStatusError`
   分支前加一个「429 → `await asyncio.sleep(0.5 * 2**attempt)` 后重试」分支（镜像
   `rest_client.py:98-99`），让偶发限流自愈而非直接 502。这一步同时治好整条实盘签名路径的同类技术债，
   且不改变非 429 的错误语义（其余 `HTTPStatusError` 仍立即上抛）。
3. **方案 C 列为强烈建议的后续优化**：它从源头把签名突发降为常数级，是最稳健的结构性修法，但需要拆分
   或新增聚合函数（避免改动被单标的下单面板复用的 `fetch_trading_config`），改动面比 A 大。建议在 A+B
   落地、复现步骤验证通过后，作为独立小改动跟进。

对实现节点（Subtask #2）的最小落地清单：
- `services/quant/app/live_auto/manager.py:_gather_context` — 引入 `asyncio.Semaphore`（默认 2）
  包裹每个 `fetch_trading_config`，`fetch_live_positions` 复用同一信号量预算。
- 新增配置项 `live_auto_start_concurrency: int = 2`（`config.py`），便于将来调参。
- `services/quant/app/okx/private_client.py:_get` 与 `trade_client.py:_post` — 在 `HTTPStatusError`
  分支前加 429 指数退避重试（仅针对 429，其余状态码语义不变）。
- 回归：用上文「最小复现」两个断言改写成单测（并发峰值 ≤ 2；模拟 429 后能重试成功），把这次回归固化为护栏。

## 评审结论

> Subtask #1 — 分析报告评审与修复。本节由资深评审补全：逐条核验证据、审计候选方案、加固证据链、敲定**单一**推荐方案。

### A. 证据链核验（逐条比对源码，结论：成立）

我把报告引用的每一处行号 / 断言都拉回源码核对，全部属实：

- `live_auto.py:34-39`（双闸 `_require_auto_enabled`）、`live_auto.py:101-130`（路由 `live_auto_group_start` → `start_group`，第 126 行）—— 属实。
- `manager.py:163-177`（`start_group` 编排），第 170 行 `await self._gather_context(...)` —— 属实。
- `manager.py:207-213`（`_gather_context`：`asyncio.gather` 全量并发 `fetch_trading_config` + 随后一发 `fetch_live_positions`，`next(...)` 只取第一份 `availUsdt`）—— 属实，「2(N-1) 发冗余」成立。
- `trading.py:462-475`（`fetch_trading_config`：第 465 行每标的 new 一个 `OKXTradeClient`，第 467 行 `gather(get_account_config, get_balance)` 两发签名 GET）、`trading.py:319-329`（`fetch_live_positions` 再 new 一个 client 发 `/account/positions`）—— 属实。
- `private_client.py:111-127`（`_get` 循环：`TransportError` 重试、`HTTPStatusError`（含 429）第 124-126 行立即抛 `OKX_HTTP_ERROR`，无退避）、`trade_client.py:35-47`（`_post` 同形，第 44-46 行）—— 属实。
- `rest_client.py:38-53`（`_RateLimiter(0.12)`）、`rest_client.py:90-110`（第 95 行 `acquire()` 取令牌、第 98-100 行 `status_code==429` 先退避后 `continue`）—— 属实，公共/私有两条路径的不对称是真实的。
- `main.py:65-71`（`OKXError` 处理器统一回 **502**）+ `OKXPrivateError(OKXError)`（`private_client.py:27`）—— 属实，502 包装链成立。
- `config.py:39`（`okx_account_demo`）、`config.py:65`（`live_auto_max_group_symbols=8`）—— 属实。
- 同类技术债 `accounts.py:97-111`（`fetch_account_detail` 一发 `gather(config,balance,positions)` 走无退避 `_get`）—— 属实。

### B. 证据加固（评审补充，原报告未点透但对定论关键）

1. **错误指纹唯一指向签名路径，排他性证据。** 全仓 grep `OKX_HTTP_ERROR`：**抛出点**仅 `private_client.py:125` 与 `trade_client.py:45` 两处（`accounts.py:54` 只是 `_map_okx_code` 的**透传映射**，不产生该码）。因此观测到的 `{"code":"OKX_HTTP_ERROR","message":"OKX HTTP 429"}` 在本仓**只可能**由签名私有 / 交易客户端产生——公共客户端 `rest_client` 失败只会抛 `OKX_UNREACHABLE`，**绝不会**产出这个码。这把「是签名私有路径撞限流」从推断升级为**排他性结论**，封死了「其实是公共行情 429」之类替代解释。映射链 `_map_trade_code("OKX_HTTP_ERROR")`→`_map_okx_code`（`accounts.py:54` 透传）→ 仍为 `OKX_HTTP_ERROR`，与复现 JSON 逐字一致。

2. **瞬时并发峰值是 `2N`，不是 `2N+1`。** `_gather_context` 里 `fetch_live_positions` 在 `gather` **之后**单独 `await`（`manager.py:210`），与那 `2N` 发不同时在途。故签名**总数** = `2N+1`（报告正确），但**瞬时并发峰值** = `2N`（那一发 positions 紧随其后、非叠加）。这点不改结论，但对「限并发=2 后峰值降到多少」的估算是必要前提。

3. **`fetch_trading_config` 内部还有一层并发，影响限流粒度。** 每个 `fetch_trading_config` 内部 `gather(get_account_config, get_balance)` 是 2 发**不同端点**的并发。故即便外层 `Semaphore(2)` 把同时在途的 `fetch_trading_config` 压到 2，签名瞬时并发仍是 `2×2=4`，但落在 `account/config` / `account/balance` 两个端点上各 2 发——**单端点瞬时并发=2**。报告「降到 ~4」表述正确，这里补足「按端点拆分后单端点=2」的粒度，正是它能落进 OKX 单端点限额的原因。

### C. 候选方案审计（边界 / 最小性 / 回归）

**方案 A（`Semaphore(2)` 限 fan-out）——命中诉求，但有一处必须说清的局限：**

- ✅ 直接实现用户明确要求的「并发=2 / 每 2 个一组」，改动集中在 `manager.py:_gather_context` 一处，不触碰两段式门禁 / 整组回滚契约，回归面最小。`fetch_live_positions` 已天然在 `gather` 之后，纳入同一信号量预算即可（甚至无需特殊处理）。
- ⚠️ **关键局限：`Semaphore(2)` 限的是「瞬时并发」，不是「单位时间速率」。** OKX 私有限流是**按端点的滑动窗口**（如 `account/config` 约 5 次 / 2s 量级），而非瞬时并发数。设单次 `fetch_trading_config` 往返耗时 `T`，限并发 2 时 `account/config` 的发出速率约 `2/T` 次/秒；当连接预热、`T≈0.2~0.4s` 时，2s 窗口内仍可能发出 `~10~20` 次，**依旧可能超过单端点窗口限额**。结论：**A 单独不保证消灭 429**，它消灭的是「一次性打开 17 个 socket 的瞬时风暴」，但窗口内速率仍取决于 RTT。**这把方案 B 从「兜底」提升为「与 A 同等必要」。**

**方案 B（签名路径加 429 退避）——必要，且有几处实现细节原报告未点明，必须写进交接：**

- ✅ 退避让「窗口边缘的偶发 429」自愈为最终成功，是 A 的速率局限的正解；并一并治好下单 / 平仓 / 设杠杆同类技术债。
- ⚠️ **实现陷阱（务必随 citation 交接给 Subtask #2，否则会写出「看起来加了退避其实没生效」的代码）：**
  1. **必须在 `raise_for_status()` 之前插 `status_code==429` 预检**。现状是 429 先被 `resp.raise_for_status()` 抛成 `HTTPStatusError`，落到 `private_client.py:124` / `trade_client.py:44` 立即上抛；若只在 `except HTTPStatusError` 里加退避，逻辑会和「其余 4xx/5xx 立即抛」纠缠。正解是**镜像 `rest_client.py:98-100`**：`if resp.status_code == 429: await asyncio.sleep(0.5*(2**attempt)); continue`，放在 `raise_for_status()` 之前。
  2. **重试预算要够**。现 `private_client.py:111` 是 `for attempt in range(2)`、`trade_client.py:35` 是 `for _ in range(2)`，只给 1 次重试；退避要吸收一次突发，建议把签名 GET 的尝试次数提到 ~3（与 `rest_client` 的 `retries=3` 对齐），并把循环变量用于退避指数。
  3. **退避耗尽的终态码要对**。循环走完仍 429 时，现有兜底 `raise OKXPrivateError("OKX_UNREACHABLE", f"...{last_exc}")` 会因 `last_exc=None` 产出「…: None」的错误串。429 耗尽应显式 `raise OKXPrivateError("OKX_HTTP_ERROR", "OKX HTTP 429")`，**保持非 429 的 `HTTPStatusError` 仍立即上抛**（错误语义不变）。
- ✅ **`_post` 加 429 退避是幂等安全的**：`clOrdId` 在 `place_live_order`（`trading.py:135`）生成后写入 payload，`_post` 重试用的是**同一个 body / 同一 clOrdId**；OKX 按 `clOrdId` 去重，即便首发已落地，重试也不会造成重复下单。故对 `_post` 加退避不会引入「重复成交」回归。（注：本 bug 的失败发生在 `_gather_context`，只走 `_get`；`_post` 退避属同 PR 顺手清债，边际成本低、形状一致。）

**方案 C（账户级请求去重为常数 3 发）——最稳健的结构性修法，且当前代码已自相矛盾：**

- `_gather_context` 的 docstring（`manager.py:206`）写「账户可用 USDT 与全账户持仓**整组各取一次**」，但实现因复用按标的封装的 `fetch_trading_config`，把 `account/config`+`account/balance` 各重复拉了 N 次——**代码与自身注释契约不符，C 本质是修一个潜伏缺陷，并非新增范围**。
- C 把签名突发从 `2N+1` 降到**常数 3**（`config`+`balance`+`positions` 各一发），是唯一能让账户级端点**确定性**落在任何窗口限额内的方案（1 发 << 5/2s），不依赖 RTT 假设。
- ⚠️ 回归约束：**不要改 `fetch_trading_config` 本身**（下单面板 `api/trading.py` 复用它）。应**新增**一个分组专用聚合函数：账户级一发到底（可参照 `accounts.py:97 fetch_account_detail` 已有的 `gather(config,balance,positions)` 单 client 形态），每成员仅走公共 `_load_meta`（带 1h 缓存）+ `get_ticker`（带 0.12s 节流）+ 派生 `instrumentSupported`。

### D. 最终推荐方案（单一）

**推荐落地 = 方案 A + 方案 B（联合，作为本 bug 的最小且正确修复）；方案 C 作为强烈建议的同 PR 折叠项 / 紧邻 fast-follow。**

定论理由（为何是 A+B 而非任一单独）：

1. **A 精确兑现用户的显式指令**（「并发限制为 2，每 2 个一组」），且侵入面最小（`manager.py:_gather_context` 一处 + 新增配置 `live_auto_start_concurrency: int = 2`），不破坏两段式门禁与整组回滚。
2. **B 是正确性必需而非可选**：如 C 节所证，`Semaphore(2)` 只限瞬时并发、不限单端点窗口速率；只有 429 指数退避能把「窗口边缘的残余 429（叠加下单面板 / 盯市轮询同 UID）」转成最终成功，而不是直接 502。**A 决定『不再瞬时打爆』，B 决定『偶发限流能自愈』，二者缺一则 bug 可复发。**
3. **C 是最稳的根治**，且因代码已违反自身 docstring，它修的是潜伏缺陷而非扩范围；唯改动面比 A 大、需新增聚合函数。若 Subtask #2 能在不动 `fetch_trading_config` 的前提下干净新增分组聚合函数，**建议直接在同 PR 折叠 C**（届时 A 退化为「对每成员的公共 meta/ticker 限并发」、B 仍保留作纵深）；若排期紧，**先发 A+B，C 立即 fast-follow**。

**给 Subtask #2 的最小落地清单（含上文陷阱）：**

1. `config.py` 新增 `live_auto_start_concurrency: int = 2`（沿 `LIVE_AUTO_*` 命名，可 env 覆写）。
2. `manager.py:_gather_context` 用 `asyncio.Semaphore(settings.live_auto_start_concurrency)` 包裹每个 `fetch_trading_config`；`fetch_live_positions` 已在 `gather` 后，纳入同一信号量预算即可。
3. `private_client.py:_get`：在 `raise_for_status()` **之前**加 `status_code==429` 预检退避（镜像 `rest_client.py:98-100`），提升尝试次数至 ~3，退避耗尽显式抛 `OKX_HTTP_ERROR 429`；非 429 的 `HTTPStatusError` 行为不变。
4. `trade_client.py:_post`：同 3 加 429 退避（幂等由 `clOrdId` 保证，安全）。
5. （建议折叠 C）新增分组账户级聚合函数，签名请求降为常数 3；不改 `fetch_trading_config`。
6. **回归测试**（把本次回归固化为护栏，复用现有约定）：
   - 并发峰值断言：`monkeypatch.setattr("app.live_auto.manager.fetch_trading_config", <inflight 计数桩>)`（沿用 `test_live_auto_group.py:_patch_okx` 的 patch 点），以 8 成员调 `_gather_context`，断言**单端点瞬时在途 ≤ 2**（或 fetch 级在途 ≤ 2）。
   - 429 退避断言：用 `httpx.MockTransport` 让首发 429、次发 200 注入 `OKXPrivateClient`（沿 `test_okx_signing.py` 直接构造 client 的约定；因 client 在 `_get_client` 内部自建 `AsyncClient`，需允许注入 transport 或 monkeypatch `_get_client`），断言 `get_account_config()` 退避后**最终成功**而非立即抛 `OKX_HTTP_ERROR`。

**Definition of Done 自检：** 证据链已逐行核验并加固（B 节排他性指纹 + 并发粒度），候选方案的边界 / 最小性 / 回归均已审计，本节给出**单一**推荐方案（A+B，C 折叠/紧随）与带陷阱的落地清单。**遗留约束：OKX 真实单端点限频数值（如 `account/config` 5/2s）为业界经验量级、非本仓可断言常量；A 单独是否足够依赖真实 RTT，故 B 必须随附——此判断已写入推荐理由，Subtask #2 无需再就此决策。**
