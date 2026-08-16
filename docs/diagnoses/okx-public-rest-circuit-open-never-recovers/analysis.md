# AI 自动交易跑一段时间后公共行情链路永久熔断

> Bug slug：`okx-public-rest-circuit-open-never-recovers`
> 诊断日期：2026-08-16 ｜ 证据来源：`%APPDATA%/aragon-desktop/logs/backend.log`（135 MB，2026-07-26 ~ 2026-08-16 01:08）
> 本节点只定位不修代码。

---

## 一、问题描述（Problem description）

用户报告：桌面端 AI 自动交易跑一段时间之后，下单面板出现

```
加载下单上下文失败：交易所连接暂时熔断，49 秒后自动重试 (/api/v5/public/instruments)
```

此后**桌面端再也连不上交易所，界面上的「重试」与自动重试也一律失败**。

实测下来症状比报告更严重也更具体：

- 熔断一旦进入这个状态就**不会自愈**。生产日志里出现过 4 次，每一次的唯一出口都是**重启后端进程**，
  持续时长分别是 **7h41m / 14h23m / 16h54m / ≥58m（用户报 bug 时仍在持续）**。
- 故障期间**私有链路完全正常**：同一进程、同一代理、同一主机，`/api/v5/account/positions`
  每小时仍有 700~3400 次 `200 OK`，而公共读（`/api/v5/market/*`、`/api/v5/public/*`）**归零**。
- 因此「AI 自动交易失去行情」「图表不再刷新」「下单面板打不开」是同一个根因的三种表现。

---

## 二、复现步骤（Reproduction steps）

### 2.1 生产复现（已在真机上发生 4 次）

1. 桌面端在**本机代理（Clash Verge，`127.0.0.1:7897`）**下启动，`HTTPS_PROXY` 经环境变量透传给
   Python 后端（`services/quant/app/config.py:777-780`）。
2. 启动 AI 自动交易多标的分组（本次为 `BTC/ETH/SOL/DOGE/SATS-USDT-SWAP`，5s 盯市）。
3. 挂机等待一次**持续超过约 100 秒**的公共链路抖动（代理换节点 / 笔记本休眠唤醒 / OKX 侧瞬时不可达都可触发）。
4. 抖动过去之后回到桌面端，打开任意合约的下单面板 → 立刻看到本文标题里的报错；点「重试」仍是同一条。
5. `/api/health` 顶栏芯片、`/api/okx/connectivity` 面板里的「行情接口(REST)」恒为 `down`，
   手动「探测」按钮也恒失败。
6. **重启桌面端**（进程重启）→ 2 秒内公共读全部恢复正常。

> 判据非常干净：**故障期间网络是好的**（私有读满速、重启后 2 秒即通），所以这不是网络问题。

### 2.2 从生产日志重新导出全部证据

```powershell
python docs/diagnoses/okx-public-rest-circuit-open-never-recovers/repro/log_forensics.py
```

（只读扫描 `%APPDATA%/aragon-desktop/logs/backend.log`，约 30s，输出下文引用的三张表。）

### 2.3 最小复现一：熔断棘轮（无网络、无账户、秒级）

```powershell
python docs/diagnoses/okx-public-rest-circuit-open-never-recovers/repro/breaker_ratchet.py
```

复用**真实**的 `OKXRestClient` / `_Breaker`，只把单次网络尝试换成恒失败替身。实际输出：

```
config: threshold=5 base_cooldown=20s

--- 普通公共读（非探针）：熔断器计数 ---
read #5: [OKX_UNREACHABLE] '无法连接 OKX (/api/v5/market/ticker): '
        failures=5 cooldown=20s open_for=20s
read #6: [OKX_CIRCUIT_OPEN] '交易所连接暂时熔断，21 秒后自动重试 (/api/v5/market/ticker)'
        failures=5 cooldown=20s open_for=20s

--- 故障持续；只剩被豁免的探针还能真的发出请求 ---
probe #1: [OKX_UNREACHABLE] '无法连接 OKX (/api/v5/public/time): '
        failures=5 cooldown=40s  open_for=40s
probe #2: ...                    cooldown=80s  open_for=80s
probe #3: ...                    cooldown=120s open_for=120s
probe #4: ...                    cooldown=120s open_for=120s

--- 窗口 armed 期间下单面板拿到的东西 ---
GET /api/trading/config -> HTTP 502
  {"error": {"code": "OKX_CIRCUIT_OPEN", "message": "交易所连接暂时熔断，121 秒后自动重试 (/api/v5/public/instruments)"}}
  UI: 加载下单上下文失败：交易所连接暂时熔断，121 秒后自动重试 (/api/v5/public/instruments)
```

最后两行与用户截图**逐字同形**（用户看到的 49 秒 = 80 秒那一档里已经走掉 31 秒）。

### 2.4 最小复现二：代理异常的分级漏网 + 连接池不泄漏（反证）

```powershell
python docs/diagnoses/okx-public-rest-circuit-open-never-recovers/repro/proxy_error_classification.py
```

实际输出：

```
--- 代理卡住：调用方按 market_window 的写法 8s 放弃 ---
round 0: wait_for 放弃于 8.0s；pool=0（无泄漏）
round 1: wait_for 放弃于 8.0s；pool=0（无泄漏）
round 2: wait_for 放弃于 8.0s；pool=0（无泄漏）

--- 代理开始回应（但回 502）：看异常类型与分级 ---
probe 0: httpx.ProxyError str='502 Bad Gateway' -> classify_exception = 'terminal'；pool=0
```

---

## 三、根因分析（Root cause analysis）

### 3.1 证据一：故障只在**进程重启**时结束，从不自愈

`repro/log_forensics.py` 的事件时间线（PUB_OK = 一次公共读 200；PROBE_FAIL = 60s 链路清扫协程的
`/api/v5/public/time` 失败；RESTART = `Application startup complete`）：

| # | 首次探针失败 | 出口 | 恢复时刻 | 中断时长 |
|---|---|---|---|---|
| 1 | 2026-08-13 09:40:08 | 自愈（下一次公共读成功） | 09:40:29 | 21 s |
| 2 | 2026-08-13 12:56:36 | 自愈 | 12:56:38 | 2 s |
| 3 | **2026-08-13 13:12:58** | **进程重启** | 20:53:52 | **7 h 41 m** |
| 4 | 2026-08-14 08:28:36 | 自愈 | 08:28:41 | 5 s |
| 5 | **2026-08-14 12:48:39** | **进程重启** | 2026-08-15 03:11:23 | **14 h 23 m** |
| 6 | 2026-08-15 03:57:13 | 自愈 | 03:57:48 | 35 s |
| 7 | 2026-08-15 04:14:18 | 自愈 | 04:14:24 | 6 s |
| 8 | **2026-08-15 05:15:48** | **进程重启** | 22:09:40 | **16 h 54 m** |
| 9 | **2026-08-16 00:10:14** | 未恢复（日志结束于 01:08:55） | — | **≥ 58 m** |

**4 / 4 的不可恢复段，出口全部是进程重启；每次重启后 2 秒内公共读恢复。**
短抖动能自愈、长抖动必须重启——这正是「进程内状态被卡住」的指纹，而不是网络的指纹。

### 3.2 证据二：同一进程里公共读归零、私有读满速

`log_forensics.py` 第二张表（节选）：

```
hour            public  private
2026-08-15 04      670     3251
2026-08-15 05      114     2999   <- 05:15 起公共读死掉
2026-08-15 06        0     3367
2026-08-15 07        0     3375
...（连续 16 小时 public=0，private 1300~3400/h）
2026-08-15 21        0     1333
2026-08-15 22      613     3047   <- 22:09 重启后立刻恢复
2026-08-16 00       49     2944
2026-08-16 01        0      726   <- 用户报 bug 的那一小时
```

公共与私有走**同一个代理**（`rest_client.py:165` 与 `private_client.py:92` 都用
`settings.proxy_url`）、**同一个 base_url**（都用 `settings.okx_rest_base`）、**同一个进程**。
私有能通而公共不能通 ⇒ 故障在**公共客户端对象自身**，不在网络、不在代理、不在 OKX。

补充旁证：诊断当时用同样参数（走代理与不走代理各一次）直连 OKX 公共端点，
`/api/v5/public/time` 与 `/api/v5/public/instruments` 均 200，耗时 0.36~1.67s。

### 3.3 证据三：失败恒为 20.000 秒，且**日志里没有任何异常信息**

1748 行清扫协程失败日志，形如：

```
2026-08-16 01:08:55,605 WARNING aragon: okx link sweeper tick skipped: [OKX_UNREACHABLE] 无法连接 OKX (/api/v5/public/time):
```

注意**冒号后面是空的**。相邻两次失败的间隔直方图（`log_forensics.py` 第三张表）：

```
samples=1748
    80.0s  1683      <- 96.3%
    80.1s  18
    ...（长尾 ≤ 85.5s）
```

`_okx_link_sweeper` 的循环体是「时钟同步 → `await asyncio.sleep(60)`」（`main.py:260-270`），
所以 80.0s − 60s = **单次调用恒为 20.000 秒**，正是 `httpx.Timeout(20.0, connect=10.0)`
（`rest_client.py:164`）的 read / pool 档，而**从来不是** 10s 的 connect 档。

而消息为空是因为 `rest_client.py:210` / `:220` 用 `f"...: {last_exc}"` 格式化异常：
**httpx 的全部超时类（`ReadTimeout` / `PoolTimeout` / `ConnectTimeout`）`str()` 都是空串**。
于是三天 1748 次失败，日志里连「是哪一类失败」都没留下——这本身是一条 P1 观测缺陷。

> 〔评审复核 ✅ 成立，并已加固〕独立重扫同一份 `backend.log` 得到：`okx link sweeper tick skipped:`
> 共 **1748** 行，且**去重之后只有一种字符串**——
> `'[OKX_UNREACHABLE] 无法连接 OKX (/api/v5/public/time):'`，冒号后恒为空、1748/1748 无一例外。
> 本机实测（httpx **0.28.1** / httpcore **1.0.9**）：`str(httpx.ReadTimeout(''))` 与
> `str(httpx.PoolTimeout(''))` 均为 `''`，而 `str(httpx.ProxyError(...))` 为
> `'502 Bad Gateway'`（见 §2.4 repro 实测行）。
> ⇒ **这 1748 次失败恒为 httpx 的超时类，并且可以据此排除 `ProxyError`**（它带非空文本）。
> 这一条同时把 §3.6 的定性收窄（见该节评审注）。
> 顺带纠正两处行号：`f"...: {last_exc}"` 的两处格式化在 `rest_client.py:209` 与 `:220`（正文写作
> `:210`），`_note_fail` 的 `f"{path}: {exc}"` 在 `:258`（正确）。

### 3.4 根因 A（主因）：公共 `AsyncClient` 是**进程级永不重建**的单例，没有任何失效换代机制

```python
# services/quant/app/okx/rest_client.py:160-168
async def _get_client(self) -> httpx.AsyncClient:
    if self._client is None:
        self._client = httpx.AsyncClient(
            base_url=self._settings.okx_rest_base,
            timeout=httpx.Timeout(20.0, connect=10.0),
            proxy=self._settings.proxy_url,
            headers={"User-Agent": "aragon-trading/0.1"},
        )
    return self._client
```

- 全仓只有 `main.py:83`（应用关停）会调 `aclose()`，`_client` 此后再无第二个赋值点；
- 没有传 `limits=`，所以连私有侧那套 `POOL_LIMITS`（`client_pool.py:31-32`）也没有；
- 60s 的 `_okx_link_sweeper`（`main.py:248-272`）**只清扫私有连接池**，公共客户端从不被清扫；
- 更关键的是：清扫协程先做时钟同步再做 `pool.sweep()`，而时钟同步本身走的就是公共读——
  它一失败就跳到 `except`，**`pool.sweep()` 三天一次都没跑过**。

对照私有链路——**同一轮加固（commit `3c2155b`）给私有链路做了正面修复，公共链路被漏掉了**：

```python
# services/quant/app/okx/private_client.py:225-229
except httpx.TransportError as exc:      # 基类，天然覆盖 ProxyError/PoolTimeout/ReadTimeout/…
    last_exc = exc
    rate_limited = False
    await self._invalidate_pooled()      # 抖动即整池换代，而非让后续请求各自超时一遍
```

```python
# services/quant/app/okx/client_pool.py:95-106
async def invalidate(self, fp: str) -> None:
    """关闭该指纹的连接并置代：下一次 acquire 会建一条全新的。"""
```

`client_pool.py` 的模块 docstring 甚至把这条纪律写死了：
「**失效即换代**：任何传输层异常触发 `invalidate(fp)` …… 这正是『笔记本休眠唤醒后所有池化 socket
都是半死的』的解药。」——**这剂解药只发给了私有链路**。公共链路一旦其 `AsyncClient` 进入不可用
状态，进程存续期内没有任何代码路径能把它换掉，只能靠重启。这与 §3.1 / §3.2 的观测完全吻合。

> 关于「客户端到底是怎么卡死的」的残余不确定性：日志把异常类丢掉了（§3.3），因此无法区分
> `PoolTimeout`（连接槽被永久占住）与 `ReadTimeout`（隧道半死但对端不给 FIN）。
> `repro/proxy_error_classification.py` 已**排除**「被 `asyncio.wait_for` 取消的请求泄漏连接槽」
> 这一条（httpcore 1.0.9 会把连接从池里摘干净，`pool` 恒为 0）。
> 两种残余机制的**修法完全相同**（失效即换代 + 观测补全），所以这点不确定性不阻塞修复；
> §3.3 提出的日志补全会在下一次复发时一次性定死它。

〔评审复核 ✅ 主因成立；机制侧已进一步收窄，且**结论不变**〕

1. **「同一条半死 socket 被永远复用」这个直觉解释站不住**——本机安装的 httpcore 1.0.9
   `_async/connection_pool.py:270-299` 在每次 `_assign_requests_to_connections()` 里会把
   `is_closed()` 的连接从 `self._connections` 里摘掉，而 `_async/http11.py` 在请求异常出口
   会走 `_response_closed()` → 半途失败的 HTTP/1.1 连接被关闭。**死连接是会被回收的。**
2. **能同时满足全部观测的机制只剩「连接池槽位账目」一侧**：同一文件 `:324` 的
   `elif len(self._connections) < self._max_connections:` 是新建连接的**唯一**入口；一旦
   `_connections` 被既不 closed、又不 expired、又不 idle、又不 available 的条目占满
   （默认 `max_connections=100`，见 `httpx._config.DEFAULT_LIMITS` 实测），排队请求就只能在
   `pool_request.wait_for_connection(timeout=pool)` 上等到 **pool 超时=20.0s**（`httpx.Timeout(20.0, …)`
   的 pool 档）后抛 `PoolTimeout`——**恒 20.000 秒、`str()` 为空、进程级、公共侧独有、重启即清**，
   与 §3.1/§3.2/§3.3 的四项观测逐条吻合。
3. 但这仍**不是**定死的结论（区分 `PoolTimeout` 与 `ReadTimeout` 只差一个类名，见 F4）。
   **两者都被「整客户端换代」覆盖**：新 `AsyncClient` 意味着新的 `AsyncConnectionPool`、新的
   `_connections` 列表、新的槽位账目，与坏在哪一层无关。原文的「对残余不确定性免疫」成立。
4. 行号订正：私有侧那段的 `except httpx.TransportError` 实际在 `private_client.py:226`
   （正文写作 `:225`），`_invalidate_pooled()` 在 `:229`。

**已排除的替代解释**（评审补做，逐条给出证伪依据）：

| 替代解释 | 证伪依据 |
|---|---|
| OKX / 代理按 IP 封禁了公共端点（公共按 IP 限、私有按账户限，故可只死公共） | 进程重启后 **2 秒**即通，且出口 IP、代理、base_url 全未变（§3.1 时间线 4/4）；IP 级封禁不会因进程重启而解除，且会回 429/403 而非 20s 空异常 |
| 公共令牌桶 `get_limiters().bucket("public", …)` 卡死（进程级、公共独有） | 若卡在 `_limiter.acquire()`，清扫协程的间隔就不会恒为 60+20；实测 1683/1748 个间隔精确等于 80.0s，**20.000s 只可能来自 httpx 超时档** |
| DNS 解析失效 | `settings.proxy_url` 存在时全部连接打到 `127.0.0.1:7897`，链路上没有目标域名解析 |
| 有人在运行期把公共 client 关掉了 | 全仓 `get_rest_client().aclose()` 只有 `main.py:83` 一处（评审全仓复扫确认），且 `aclose()` 自身会把 `_client` 置 `None`，下一跳就会重建——真发生了反而**不会**卡住 |

### 3.5 根因 B（放大器）：熔断器是**只上不下的棘轮**，还被自己的豁免探针不断重新上膛

```python
# services/quant/app/okx/rest_client.py:116-131
def note_failure(self) -> None:
    if not self.enabled:
        return
    if self._half_open:
        self._cooldown = min(_BREAKER_COOLDOWN_MAX_S, self._cooldown * 2)
        self._open()
        return
    self.failures += 1
    if self.failures >= self._threshold:
        self._open()

def _open(self) -> None:
    self._half_open = False
    self.opened_until = time.monotonic() + self._cooldown
```

三处缺陷叠加（`repro/breaker_ratchet.py` 逐条复现）：

1. **`failures` 在 `_open()` 时不清零**。一旦到达阈值 5，此后**每一次**失败都满足
   `failures >= threshold`，即每次失败都重新把窗口推满。
2. **豁免探针的失败照样喂给熔断器**。`_request` 只在 `probe=False` 时查熔断
   （`rest_client.py:195-196`），但失败收口 `_note_fail`（`:253-258`）**对探针一视同仁**地调
   `breaker.note_failure()`。于是 `health()`（`:260-281`，`/api/health` 顶栏 15s 轮询）与
   `get_server_time_ms()`（`:283-291`，60s 清扫协程）——当初被豁免正是为了让它们能把熔断治好——
   反而成了让熔断永远上膛的那只手。
3. **冷却只涨不落**：20 → 40 → 80 → 120s（`_BREAKER_COOLDOWN_MAX_S`，`:40`），只有一次**成功**
   才会复位（`note_ok`，`:108-114`）。第三次翻倍之后窗口（120s）已长于清扫协程的探针间隔（80s），
   于是普通公共读**再也拿不到半开名额**——它们连一次网络尝试的机会都没有了，只会瞬间抛
   `OKX_CIRCUIT_OPEN`。这就是用户看到的「自动重试也是失败的」。

另外 `ensure_closed_or_raise`（`:98-106`）在冷却走完时只置 `_half_open = True` 而**不清
`opened_until`**，且 `_half_open` 是所有并发调用共享的单个布尔——冷却一过，**所有**在途调用都被
当作「那一个半开探针」放行，其中任意一个失败都会翻倍并重新开闸。

> 〔评审复核 ⚠️ 缺陷 1/2/3 全部成立，但第 3 条的**推理链要改**〕
>
> - 缺陷本身逐条复核通过：`_open()` 确实不清 `failures`（`:128-131`）；`_note_fail`（`:253-258`）
>   确实对 `probe=True` 一视同仁；`repro/breaker_ratchet.py` 实跑输出与文档逐字一致
>   （`failures=5` 恒定、冷却 20→40→80→120 封顶、末尾 502 文案逐字同形）。
> - **但「第三档起窗口(120s)长于探针间隔(80s)，普通公共读再也拿不到半开名额」这句是非因果推理。**
>   半开名额是由 `ensure_closed_or_raise` 发的，而**探针根本不调它**（`_request:195-196` 是
>   `if not probe:`）。探针从不申请、也就从不抢占半开名额；两个时长之间没有竞争关系。
>   `repro/breaker_ratchet.py:71-72` 之所以能演出这个棘轮，是因为它**手工**把
>   `breaker.opened_until` 拨到过去并替探针调了一次 `ensure_closed_or_raise`——生产里做这件事的
>   是**普通公共读**（`/api/candles`、`market_window` 的 HTF 补根、`get_ticker`），不是探针。
> - 生产里真正发生的是：窗口一过期，下一个普通公共读（5s 盯市量级，秒级内必至）翻半开并发起
>   真实尝试；这次尝试要 20~40s 才失败，而在这段时间里任何一次**探针失败**都会抢先消费掉
>   `_half_open`、翻倍冷却并重新开闸。**净效果与原文描述一致**（普通公共读平均每 120s 才拿得到
>   一次真实尝试机会），**但成因是「探针抢先消费半开态」，不是「窗口长于探针间隔」。**
> - 这条更正不改变 §3.5 是「放大器」的定性，但它把 F2 从「liveness 修复」降为
>   「时延 / UX 修复」——详见 `## 评审结论` 第 3 节。

### 3.6 根因 C（次因）：错误分级对「代理链路」是瞎的

```python
# services/quant/app/okx/retry.py:29-32
_RETRYABLE_EXCEPTIONS = (
    httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout, httpx.WriteTimeout,
    httpx.PoolTimeout, httpx.RemoteProtocolError, httpx.ReadError, httpx.WriteError,
)
# :46-48
def classify_exception(exc: BaseException) -> Outcome:
    return "retryable" if isinstance(exc, _RETRYABLE_EXCEPTIONS) else "terminal"
```

这是一个**白名单**，而 `httpx.ProxyError` 不在其中（它是 `httpx.TransportError` 的直接子类，
**不是** `ConnectError` 的子类）。本部署恰恰跑在本机代理后面，于是：

- 代理抖动 → `httpx.ProxyError` → 判 `terminal` → `_request` **一次都不重试**（`:207-209`）
  并立刻给熔断器记一次失败；
- 同理漏网的还有 `httpx.CloseError`、`httpx.LocalProtocolError`，以及**根本不是 httpx 异常**的
  `anyio.ClosedResourceError`——后者在本次生产日志里有 5 次真实 traceback，栈顶正是
  `httpcore/_async/http_proxy.py → http11.start_tls → anyio/streams/tls.py`，即
  「代理 CONNECT 隧道在 TLS 握手中途被拆掉」。

对照私有侧捕的是**基类** `httpx.TransportError`（`private_client.py:225`），天然覆盖全部传输层异常。
公共侧这条白名单让「一次代理抖动」以最快速度（零重试 × 5 次 = 瞬间）把熔断器顶开。

> 〔评审复核 ⚠️ 结论保留，但两处证据必须改口径〕
>
> **(a) `httpx.ProxyError` 的类继承成立，但它在本次故障里没有证据。** 本机实测
> `ProxyError.__mro__ = [ProxyError, TransportError, RequestError, HTTPError, …]`——确非
> `ConnectError` 子类，白名单漏它属实。**但**：全量 `backend.log` 里 `ProxyError` 出现 **0 次**，
> 而 §3.3 已证明那 1748 次失败的 `str(exc)` 恒为空，`ProxyError` 的 `str()` 却带
> `'502 Bad Gateway'` 这类文本（§2.4 repro 实测）。⇒ **日志反过来排除了 `ProxyError` 是本次
> wedge 的失败类。** F3 因此是**防御性加固**，不是本次症状的成因；原文「公共侧这条白名单让一次
> 代理抖动瞬间顶开熔断」是一个**未被本次证据支持的假设**，应作为「下次可能」而非「本次已然」陈述。
>
> **(b) 那 5 次 `anyio.ClosedResourceError` traceback 全部在私有路径上，不在公共路径上。**
> 评审逐条提取了它们的完整调用栈（全部发生在 2026-08-13 09:40:18~19，即 §3.1 时间线第 1 行那次
> **21 秒即自愈**的短抖动），栈底三例分别是：
>
> ```
> app\api\trading.py:104  trading_positions   -> app\okx\trading.py:524  fetch_live_positions
>   -> app\okx\private_client.py:254 get_positions      -> private_client.py:213 _get   -> ...
> app\api\trading.py:96   trading_config      -> app\okx\trading.py:808  fetch_trading_config
>   -> app\okx\private_client.py:244 get_account_config -> private_client.py:213 _get   -> ...
> app\api\trading.py:139  trading_open_orders -> app\okx\trading.py:681  fetch_live_open_orders
>   -> app\okx\trade_client.py:319 get_pending_orders   -> private_client.py:213 _get   -> ...
>   -> httpcore\_async\http_proxy.py:316 -> http11.py:376 start_tls
>   -> anyio\streams\tls.py:167 wrap -> anyio\_backends\_asyncio.py:1293 raise ClosedResourceError
> anyio.ClosedResourceError            (随后是 "GET /api/trading/… HTTP/1.1" 500 Internal Server Error)
> ```
>
> 这把原文的对照结论**反转**了一半：`anyio.ClosedResourceError` **不是** `httpx` 异常，所以
> 私有侧那句 `except httpx.TransportError` **同样接不住它**——它以未捕获异常的形式冲到 ASGI 顶层，
> 变成 **HTTP 500**（而不是既有的 502 契约），并且**跳过了 `_invalidate_pooled()`**：私有池在这
> 一类异常上也不会换代。所以正确的说法是：**这是公私两侧共同的盲区**，而不是「私有侧天然覆盖、
> 公共侧漏了」。
>
> **这条对修法有直接约束**：任何以 `httpx.TransportError` 为触发谓词的「失效即换代」，都会在
> 生产已证明真实发生过的这一类异常上**静默失效**。详见 `## 评审结论` 第 2 节（谓词选择）。
>
> 同期旁证（同一秒）：`aragon.okx.ws` 6 条链路同时 `disconnected (connect_error: )`，其中一条带
> `[WinError 64] 指定的网络名不再可用`——即代理隧道被整体拆掉，与 TLS 握手中途 `ClosedResourceError`
> 同源。注意 `connect_error:` 后面**又是空的**，与 §3.3 是同一个观测缺陷的第二个现场。

### 3.7 从后端状态到用户那句话的完整链路

```
LiveTradePanel(15s 轮询)                       apps/web/components/Live/LiveTradePanel.tsx:68-72
  -> GET /api/py/trading/config?instId=…       apps/web/lib/tradingApi.ts:20-39（解析 {error:{code,message}}）
  -> GET /api/trading/config                   services/quant/app/api/trading.py:90-96
  -> fetch_trading_config                      services/quant/app/okx/trading.py:803-816
  -> _load_meta -> rest.get_instrument_meta    services/quant/app/okx/trading.py:108-112
  -> _request("/api/v5/public/instruments")    services/quant/app/okx/rest_client.py:392-417（probe=False！）
  -> _Breaker.ensure_closed_or_raise           services/quant/app/okx/rest_client.py:98-106
     raise OKXError("OKX_CIRCUIT_OPEN", "交易所连接暂时熔断，49 秒后自动重试 (/api/v5/public/instruments)")
  -> HTTP 502 {"error":{...}}                  services/quant/app/main.py:290-296
  -> 界面：加载下单上下文失败：<message>        apps/web/components/Live/LiveTradePanel.tsx:142-150
```

`get_instrument_meta` 的 1 小时 TTL 缓存（`rest_client.py:398-400`）只在**曾经成功过**时才挡得住这条
路；熔断打开期间没有任何一次成功可以写进缓存，所以面板每 15 秒重犯一次，点「重试」也只是重犯一次。

> 〔评审复核 ✅ 链路成立，补一跳被略过的中转〕`OKXError` 并不是直接落到 `main.py:290-296` 的，
> 中间还有一次**重新包装**：
>
> ```python
> # services/quant/app/okx/trading.py:807-815（fetch_trading_config）
> config, balance = await asyncio.gather(client.get_account_config(), client.get_balance())  # 私有：成功
> meta = await _load_meta(inst_id)                     # 公共：在这里被熔断打回
> except OKXError as exc:
>     raise OKXPrivateError(_map_trade_code(exc.code), _redact(exc.message, …)) from exc
> ```
>
> 这一跳之所以不改变用户看到的文案，靠的是两件**都必须成立**的事，实现节点改动错误码时要盯住：
> 1. `OKXPrivateError` 是 `OKXError` 的**子类**（`private_client.py:36`），所以仍被
>    `main.okx_error_handler` 捕获成 502；
> 2. `OKX_CIRCUIT_OPEN` 在 `accounts._LINK_CODES = {"OKX_RATE_LIMITED", "OKX_CIRCUIT_OPEN",
>    "OKX_SEND_UNCERTAIN"}`（`accounts.py:44`）里，`_map_okx_code` 对它**原样透传**；否则它会被
>    `:64` 那行压成 `OKX_ERROR`。
>
> 同时这一跳还解释了一个次生现象：面板报错时**私有两跳其实已经成功**（`get_account_config` /
> `get_balance` 先执行），所以「账户能用、行情不能用」在同一个请求里就已经成立。
>
> 生产计数复核（同一份 `backend.log`，与本节因果一致）：`GET /api/trading/config` 502 = **165** 次，
> `GET /api/candles` 502 = **121** 次（§4 表里写作 102，应订正），全量 502 = 513 次。

### 3.8 触发侧的两个加剧因素（不是根因，但会把抖动放大成阈值事件）

- `market_window._fetch_tactical`（`ai_auto/market_window.py:360-372`）用
  `asyncio.wait_for(..., ai_trade_tactical_fetch_timeout_s=8.0)` 硬砍公共取数：链路慢时**每一次**
  都会在 8 秒被放弃，而 `_request` 的重试预算是 25 秒——于是每一轮盯市都白白给熔断器送失败计数。
- `cost_runtime._bounded_maintenance`（`ai_auto/cost_runtime.py:132-142`，预算 30s）同理会砍掉在途的
  `/api/v5/public/funding-rate`。
- 多标的分组 × 5s 盯市 ⇒ 阈值 5 在一次抖动里几乎必然被打穿（全量日志里 OKX 回过 1023 次 HTTP 429，
  说明公共读压力本就贴着限频线）。

> 〔评审复核 ❌ 本节三条**全部未通过**，须整体改写。这是本文唯一实质出错的一节。〕
>
> **(1) `market_window._fetch_tactical` 的 `wait_for` 取消，一次都没有喂过熔断器。**
> `asyncio.wait_for` 超时是把 `CancelledError` 掷进内层协程，而 `CancelledError` 在 Python 3.8+
> 继承自 `BaseException`——`_attempt` 的 `except Exception`（`rest_client.py:229`）**捕不到它**，
> 于是它一路穿出 `_request`，`_note_fail`（`:216` / `:207`）**根本没有机会执行**。
> `market_window.py:363-365` 的 docstring 自己就写明了这件事（「`_safe_get_candles` 的
> `except Exception` 捕不到被取消时抛的 `CancelledError`」），只是原文推论时把它漏了。
>
> 评审用真实 `OKXRestClient` + 永不返回的替身 client 实测（10 次 8s 级 `wait_for` 取消）：
>
> ```
> after 10 wait_for-cancelled tactical reads:
>   http gets issued     = 10
>   breaker.failures     = 0        <- 不是 10
>   breaker.opened_until = 0.0      <- 熔断从未打开
> ```
>
> **(2) `cost_runtime._bounded_maintenance` 同理。** 它也是 `asyncio.wait_for`
> （`cost_runtime.py:136-139`，30s 预算），取消路径与 (1) 完全同形，同样不喂熔断器。
>
> ⇒ 真正把熔断顶开的是**没有外层 `wait_for` 的那些公共读**：`/api/candles` 路由、`market_window`
> 的 HTF 补根（`_safe_get_candles` 直调，`market_window.py:260` / `:281`）、`get_ticker`、
> 以及**探针自己**（`health()` 每次 `/api/health` 一发；日志里 `/api/health` 共 34681 次 200，
> 约合每 10~15 秒一次）。这反而**强化**了 §3.5 的第 2 条：故障期间喂熔断器的主力就是被豁免的探针。
>
> **(3) 「OKX 回过 1023 次 HTTP 429」在这份日志里不成立。** 评审全量重扫 `backend.log`：
> 含 `429` 的行共 2564 行，其中 access 日志里状态码为 429 的只有 **20** 行，且**全部**是
> `POST /api/paper/batch/start`——那是**本应用自己的**纸面会话数上限，与 OKX 无关；
> `OKX HTTP 429` / `OKX_RATE_LIMITED` / OKX 业务码 `50011` 在全量日志里各 **0** 次。
> ⇒ 该数字应删除。它在 §六 第 5 条里被用来支撑「不采纳 F7」，**那个结论仍然正确，但要换理由**
> （见 `## 评审结论` 第 4 节）。

---

## 四、影响面（Impact surface）

| 受影响能力 | 表现 | 严重度 |
|---|---|---|
| **AI 自动交易的行情输入** | 战术/高周期 K 线窗口停止更新（`market_window` 全部取数被熔断拒绝），资金费率快照停更 | **P0**：决策依据静止，但**持仓仍在**——最危险的组合 |
| 实盘下单面板 | `加载下单上下文失败：交易所连接暂时熔断…`，无法下单/改杠杆；「重试」无效 | P0 |
| 图表 / 历史 K 线 | `/api/candles` 502（日志中 102 次），`/api/instruments` 不可用 | P1 |
| 顶栏链路芯片与链路面板 | 恒 `down`；手动「探测」按钮（`api/okx_link.py:47-68`）也恒失败 | P1 |
| 纸面/实盘自动交易（`live_auto` / `paper`） | 同样依赖 `get_rest_client().get_candles/get_ticker`，一并停摆 | P1 |
| **私有链路（持仓/委托/下单/平仓）** | **不受影响**，仍然正常 | — |

风险要点：**行情断了而仓位还在**。AI 会话的止损/利润保护判定依赖公共行情推进，公共读被熔断拒绝
意味着这些保护层拿不到新数据；而私有侧仍可下单——一个「看不见行情却还能开单」的窗口期。

用户可见性也很差：`/api/health` 永远返回 200（`api/market.py:31-52` 把探活失败吞成 `okx: down`），
熔断状态只在顶栏芯片里体现，没有任何提示告诉用户「重启应用即可恢复」。

---

## 五、候选修法（Candidate fixes）

| # | 方案 | 做什么 | 侵入性 | 风险 | 工作量 | 能否根治 |
|---|---|---|---|---|---|---|
| **F1** | **公共客户端失效换代**（镜像 `client_pool.invalidate`） | 给 `OKXRestClient` 加 `_invalidate_client()`：`_attempt` 捕到 `httpx.TransportError` 即 `aclose()` 旧 client 并置 `self._client = None`；下一跳 `_get_client()` 建全新的。同时补 `limits=POOL_LIMITS` | 小（单文件、约 15 行） | 低——私有侧同姿态已在生产跑了整轮 | 0.5 d | ✅ 根治 §3.4 |
| **F2** | **熔断器去棘轮** | `_open()` 时清零 `failures`；`ensure_closed_or_raise` 翻半开时清 `opened_until`；`_half_open` 改为「只放行一个」的名额（令牌/时间戳）；冷却随时间或成功衰减；**探针失败不再喂 `note_failure`**（或只喂计数不喂 `_open`） | 小（单文件、约 20 行） | 中——须补齐 `tests/test_okx_rest_resilience.py` 的冻结用例 | 0.5 d | ✅ 根治 §3.5 |
| **F3** | **错误分级改基类** | `_RETRYABLE_EXCEPTIONS` 白名单 → `httpx.TransportError` 基类 + 显式排除 `UnsupportedProtocol`；并把 `anyio.ClosedResourceError`/`OSError` 归入可重试 | 极小（约 5 行） | 低 | 0.2 d | ⚠️ 只减少触发概率 |
| **F4** | **观测补全** | `f"...: {last_exc}"` → `f"...: {type(last_exc).__name__}: {last_exc}"`（`rest_client.py:210` / `:220` / `:258`）；熔断开/合各打一行 INFO | 极小 | 无 | 0.1 d | ⚠️ 不修行为，但让下次复发 5 分钟定位 |
| **F5** | **兜底自愈守护** | 在 `_okx_link_sweeper` 里加：公共链路连续 `down` 超过 N 分钟 ⇒ 强制 `rest.aclose()` + 熔断复位 + 一次探活；并把 `clock.sync` 与 `pool.sweep` 拆成互不连坐的两个 try | 中（`main.py` + `rest_client.py`） | 中——须保证不与 F1/F2 重复动作 | 0.5 d | ✅ 兜底，覆盖未知残余机制 |
| **F6** | 仅前端：熔断态给「重启后端」按钮 | 链路面板加一个显式的「重建连接」动作，打到新端点 | 中（前后端各一处） | 低 | 0.5 d | ❌ 治标 |
| **F7** | 关掉熔断（`OKX_LINK_BREAKER_FAILURES=0`） | 改配置即可 | 零 | **高**——失去限频保护，故障期会打成请求风暴 | 0 | ❌ 仅可作为临时缓解 |

> 〔评审复核 ⚠️ F1 的「同时补 `limits=POOL_LIMITS`」这一子项必须**剔除**〕
> 它被写成一个顺手的对齐动作，但它不是中性的：
> - `POOL_LIMITS = httpx.Limits(max_connections=16, max_keepalive_connections=8, keepalive_expiry=90.0)`
>   （`client_pool.py:31-32`），而公共侧当前用的是 httpx 默认
>   `Limits(max_connections=100, max_keepalive_connections=20, keepalive_expiry=5.0)`（本机实测）。
> - 若 §3.4 收窄后的机制（连接池槽位账目占满 ⇒ `PoolTimeout`）成立，把 `max_connections` 从
>   **100 降到 16** 会让同一个 wedge **提前约 6 倍**到达；把 `keepalive_expiry` 从 **5s 拉到 90s**
>   会让半死连接在池里多留 18 倍的时间。**两个方向都是加重，不是减轻。**
> - 私有侧之所以需要这组上限，是因为它有 17 处构造点共享一个池、且带 5s 盯市的 keepalive 诉求；
>   公共侧是单例单池，没有同样的诉求。
>
> ⇒ 本轮**只做失效换代，不动 `limits`**。若将来要调公共池上限，那是一次独立的、需要自带证据的
> 容量变更（属于「制造新岛」而非「对齐既有纪律」）。

---

## 六、推荐修法（Recommended fix）

> ⚠️ 本节是**诊断节点的初版建议**，已被下方 `## 评审结论` 部分修订（F1 的谓词与子项、F2/F3/F5 的
> 优先级、以及第 2 / 第 5 条理由）。实现节点请以 `## 评审结论` 为准。

**采用 F1 + F2 + F3 + F4 作为一次修复；F5 作为同一轮的兜底守护。F6/F7 不做。**

理由：

1. **F1 是主因的正面修复，且不需要发明新东西。** §3.4 已经证明「失效即换代」这套机制在本仓库
   已经存在、已经被评审钉死、已经在私有链路上跑了整整一轮——公共链路只是被漏掉了。把
   `client_pool.invalidate()` 的姿态镜像到 `OKXRestClient` 上，是最小惊喜、最低风险的修法，
   而且它对 §3.4 末尾那点残余不确定性（`PoolTimeout` 还是 `ReadTimeout`）**免疫**：不论客户端是
   怎么坏的，换一个新的就都好了。

2. **F2 是「为什么用户等 49 秒也等不回来」的正面修复。** 单有 F1 而不修熔断，公共读仍会被一个
   120 秒且不断被探针重新上膛的窗口挡住；单有 F2 而不修 F1，探针即使被放行也仍然会失败。
   两者缺一都不成闭环——这也是本次故障能撑 17 小时的原因：**两道本应互相兜底的机制同时失灵，
   而且第二道恰好把第一道的自愈信号（探针成功）永远堵死了。**

3. **F3 + F4 成本几乎为零，但决定下一次故障是 5 分钟还是 3 天。** 本次诊断之所以要靠「失败间隔
   直方图」去反推异常类型，就是因为 1748 行日志冒号后面是空的。这条必须补。

4. **F5 是对残余未知的诚实兜底。** §3.4 承认无法从现有日志区分两种客户端卡死机制；F5 用「连续
   down 超过 N 分钟就无条件重建 + 复位」把任何未被枚举到的机制一并覆盖，代价是一个可配 0 关闭的
   阈值（符合本仓库「每个新机制都有一个关得掉的取值」的既有纪律，`config.py:47`）。
   同时必须把 `_okx_link_sweeper` 里 `clock.sync` 与 `pool.sweep` 的连坐拆开——本次故障里
   `pool.sweep()` **三天没跑过**，就是因为它排在一个必然抛异常的调用后面。

5. **F7 明确不采纳。** 关掉熔断会让故障期的重试风暴直接撞 OKX 限频（日志里已有 1023 次 429），
   把一个「看不见行情」的故障换成一个「被交易所限频」的故障。若需**临时缓解**，正确动作是
   **重启桌面端**（已验证 2 秒恢复），而不是动这个旋钮。

### 验收判据（供实现节点使用）

- **必须先写复现用例再动业务代码**（CLAUDE.md §七）：
  - `tests/test_okx_rest_resilience.py` 新增：连续失败打开熔断后，**一次成功**必须让熔断完全复位，
    且 `failures` 归零；**连续 N 次探针失败不得让冷却无限上膛**。
  - 新增：`_attempt` 抛 `httpx.ProxyError` / `anyio.ClosedResourceError` 时，
    `classify_exception` 判 `retryable`，且 `_request` 会重试。
  - 新增：`_attempt` 抛 `httpx.TransportError` 之后，`OKXRestClient._client` 必须已被置空
    （下一跳会建新连接）。
- 回归：`docs/diagnoses/okx-public-rest-circuit-open-never-recovers/repro/breaker_ratchet.py`
  修复后应当在第 3~4 次探针失败时**不再**出现 `cooldown=120s` 的单调上膛。
- 端到端：模拟公共链路断 5 分钟后恢复，`/api/okx/connectivity` 的 `restPublic` 应在**恢复后
  一个探针周期内**回到 `up`，无需重启进程。

---

## 评审结论（Review Verdict）

> 评审日期：2026-08-16 ｜ 评审对象：本文全文 + `repro/` 三个脚本 ｜ 结论：**主因判定成立，可以进入
> 实现节点**；但候选修法的**组合与优先级需要修订**，且 §3.8 整节须改写。

### 一、评审做了哪些独立验证（不是复述原文）

| 动作 | 结果 |
|---|---|
| 复跑 `repro/log_forensics.py` | ✅ 三张表与正文**逐行一致**；4 个不可恢复段的出口全部是 `RESTART`，恢复均在 2 秒内 |
| 复跑 `repro/breaker_ratchet.py` | ✅ 输出与正文逐字一致（`failures=5` 恒定、20→40→80→120 封顶、502 文案同形） |
| 复跑 `repro/proxy_error_classification.py` | ✅ `wait_for` 取消 3 轮 `pool` 恒为 0；`ProxyError.str='502 Bad Gateway'` → `terminal` |
| 独立重扫 `backend.log`（129 MB） | 见 §3.3 / §3.6 / §3.7 / §3.8 的评审注：1748 行失败**去重后只有一种串且冒号后恒空**；`anyio.ClosedResourceError` 共 5 次 traceback，**全在私有路径**；`ProxyError` **0 次**；OKX 侧 429 **0 次** |
| 逐条核对全部代码引用 | ✅ 除两处 off-by-one（`rest_client.py:210`→`:209`，`private_client.py:225`→`:226`）外全部命中 |
| 读安装态三方库源码（httpx 0.28.1 / httpcore 1.0.9 / anyio） | 用于收窄 §3.4 的机制并证伪「stale socket 被永远复用」，见 §3.4 评审注 |
| 新做一次取消路径实测（真实 `OKXRestClient` + 永挂替身） | ❌ **证伪了 §3.8 的第 1/2 条**：10 次 `wait_for` 取消后 `breaker.failures == 0` |

### 二、最终推荐修法（唯一一条）：**F1′ —— 公共 `AsyncClient` 在传输异常出口做「身份比对的失效换代」**

即原表的 F1，但**收窄触发谓词、剔除 `limits` 子项、补三条并发/关闭约束**。其余全部候选都不是根治项。

```python
# services/quant/app/okx/rest_client.py —— 唯一的行为改动，约 12 行
async def _attempt(self, path, params):
    client = await self._get_client()
    started = time.monotonic()
    try:
        resp = await client.get(path, params=params)
    except Exception as exc:                       # noqa: BLE001 —— 分级仍交给 classify_exception
        await self._invalidate_client(client)      # ← 新增：失效即换代（镜像 client_pool.invalidate）
        return classify_exception(exc), None, exc, None
    ...

async def _invalidate_client(self, stale: httpx.AsyncClient) -> None:
    """传输层失败即换代：下一次 `_get_client()` 建一条全新的连接池。

    并发失败里只有**第一个**负责换代（身份比对）；否则后到的那个会把别人刚建好的新客户端关掉。
    先把引用摘掉再关闭，让并发调用**下一跳就拿到新客户端**，而不是继续撞一个已经半死的池。
    """
    if self._client is not stale:
        return
    self._client = None
    try:
        await stale.aclose()
    except Exception:                              # noqa: BLE001 —— 关一个已经坏掉的池失败是常态
        pass
```

**为什么谓词是「进了这个 `except` 分支」而不是 `httpx.TransportError`：**
这个 `except` 的**唯一**来源是 `await client.get(...)`（`classify_status` / `resp.json()` 都在它之后，
`rest_client.py:231-237`），所以「进了这个分支」在语义上**就等于**「传输层失败」，不需要再分类一次。
这不是偷懒，而是本次评审的硬约束：生产日志已经证明**真实发生过的失败类里有非 httpx 异常**
（`anyio.ClosedResourceError`，§3.6 评审注），任何以 `httpx.TransportError` 为谓词的写法都会在
**恰好是已知会发生的那一类**上静默失效——而私有侧现在就正栽在这上面（它那一类异常直接冲成 HTTP 500
并跳过 `_invalidate_pooled()`）。

**六条实现约束（缺一即留缺口）：**

1. **不改 `limits=`**（理由见 §五 表下评审注：`POOL_LIMITS` 在两个方向上都会加重本故障）。
2. **身份比对**：`if self._client is not stale: return`。故障期会有多路并发同时失败，没有这一行就会
   出现「A 换代 → B 把 A 刚建的新 client 关掉 → C 再换一次」的连环拆台。
3. **先摘引用、后关闭**，且**吞掉关闭异常**（镜像 `client_pool.invalidate` + `_safe_aclose` 的既有姿态，
   `client_pool.py:95-106` / `:139-144`）。注意 `rest_client.py` **目前没有 import logging**，
   要么本地补一个 module logger，要么静默 `pass`；**不要**去 import `client_pool._safe_aclose`
   （那会让公共链路凭空依赖私有池模块）。
4. **换代必须留在 `_attempt` 里，不许上移到 `_note_fail`。** `_note_fail`（`:253-258`）是**所有**失败的
   统一收口，包含 HTTP 4xx/5xx 与 OKX 业务码——把换代放那儿，等于每一次 429/400 都拆一次连接池。
5. **取消路径不换代。** `CancelledError` 是 `BaseException`，天然不进 `except Exception`；**要保持这样**。
   `market_window` 每 5 秒放弃一次取数（8s 硬超时），若把取消也当成换代信号，等于每 5 秒重建一次
   连接池；而 `repro/proxy_error_classification.py` 已证明取消**不泄漏**槽位（`pool` 恒 0），没有换代的必要。
6. **恢复时延上界写进验收**：换代由**任何一次走完超时的公共读**触发，其中最稳定的是探针——
   `health()` 约 10~15s 一次（日志 `/api/health` 共 34681 次 200）、`_okx_link_sweeper` 恒 80s 一次。
   ⇒ **故障后最迟 80 秒内客户端必被换掉，网络一恢复即自愈，无需重启进程。**

### 三、为什么 F1′ **单独一条**就足以根治，而 F2 不是根治项

这是本次评审对原文第 2 条理由（「两者缺一都不成闭环」）的**修订**。把「网络已恢复」之后的时序走一遍：

- 探针**豁免熔断**（`_request:195-196` 的 `if not probe:`），所以熔断开着也照发；
- 有了 F1′，探针发出去时用的**已经是新客户端**，于是它**成功**；
- 探针一成功即走 `_note_ok` → `note_ok()`（`:108-114`）→ `failures=0`、`opened_until=0`、
  `_cooldown` 复位到 base。**熔断被完全解除。**

也就是说：**棘轮从来不能阻止恢复，它只能推迟恢复**。本次故障之所以撑了 17 小时，唯一的原因是
「探针永远失败」——而那是根因 A，不是根因 B。原文「单有 F1 而不修熔断，公共读仍会被一个 120 秒
且不断被探针重新上膛的窗口挡住」不成立：探针**成功**时它是解药，探针**失败**时它才是毒药，而
F1′ 恰好把「探针失败」这一前提拿掉了。（另外 §3.5 评审注已说明，「窗口 120s > 探针间隔 80s」这个
推理本身也不成立——探针根本不申请半开名额。）

**F2 仍然要做，但它是 P1 的时延/UX 修复，不是 liveness 修复**：真实网络中断期间，它决定用户看到的是
「21 秒后自动重试」还是「121 秒后自动重试」，也决定 `_half_open` 这个共享布尔不再把**所有**在途
并发调用一起当成「那一个探针」。做它时必须让
`tests/test_okx_rest_resilience.py:218 test_a_successful_probe_resets_the_breaker_and_its_cooldown`
保持绿——那条用例已经把「任一次成功即完全复位（含冷却时长）」逐字冻结了。

### 四、其余候选的处置（含理由更换）

| 候选 | 评审处置 | 理由 |
|---|---|---|
| **F1′** | ✅ **本轮唯一的根治项，必做** | 见上 |
| **F4**（观测补全） | ✅ 同轮必做，但它是**零行为变更**，不构成第二个修法 | 它是把 §3.4 那点残余不确定性（`PoolTimeout` vs `ReadTimeout`）一次性定死的唯一手段；改动就是 `f"...: {last_exc}"` → `f"...: {type(last_exc).__name__}: {last_exc}"`（`rest_client.py:209` / `:220` / `:258`）。建议顺手把 `okx/ws` 那句 `connect_error: ` 一起补上（§3.6 评审注末尾的同形现场） |
| **F2**（熔断去棘轮） | 🟡 同轮做，**降为 P1**，非根治 | 见第三节 |
| **F5-a**（拆开 `clock.sync` 与 `pool.sweep` 的连坐） | 🟡 同轮做，P1，3 行 | 硬证据充分：`main.py:261-269` 里 `pool.sweep()` 排在必然抛异常的 `clock.sync` 之后，**三天一次都没跑过**。这是一条**独立于本 bug** 的私有池维护缺陷，代价极低 |
| **F3**（分级改基类） | 🟢 P2，可本轮可下轮 | 采纳 F1′ 之后它**不再是前置条件**（F1′ 不依赖分类）。它的剩余价值只是「同一次调用内多给一次重试」。且要注意：`anyio.ClosedResourceError` **不是** httpx 异常，改成 `httpx.TransportError` 基类**接不住它**——若做 F3，白名单必须显式加上 `anyio.ClosedResourceError` 与 `OSError`，否则等于没修 |
| **F5-b**（连续 down N 分钟强制重建 + 熔断复位的兜底守护） | ❌ **不做** | 采纳 F1′ 之后它是**死码**：能满足「探针连续 N 分钟失败」的每一次失败，都已经各自换过一次代了。它还会引入第二个「谁拥有 `_client` 生命周期」的主体（与 F1′ 的 `aclose` 竞态），并新增一个生产永不触发的配置项 |
| **F6**（前端「重建连接」按钮） | ❌ 不做 | 治标；且 `POST /api/okx/connectivity/probe`（`api/okx_link.py:47-68`）本来就是这个按钮，F1′ 之后它会真的有用 |
| **F7**（关熔断） | ❌ 不做——**但原理由要换** | 原文的「会撞 OKX 限频（1023 次 429）」在这份日志里**没有证据**（§3.8 评审注：OKX 侧 429 为 0）。真正的理由是：关掉熔断后，故障期每个 5s tick × N 标的都会排满 `okx_link_rest_max_attempts=4` × `okx_link_rest_budget_s=25s` 的重试预算，把事件循环与公共令牌桶灌满，UI 的 15s 轮询会被拖成几十秒级阻塞；而且它**根本不碰根因**（客户端依旧不换代）。临时缓解仍是**重启桌面端**（已验证 2 秒恢复） |

### 五、修订后的验收判据（覆盖原 §六 的那一份）

先写用例、再动业务代码（CLAUDE.md §七）。以下每条都对应本次评审发现的一个真实缺口：

1. **[新增·核心] 谓词覆盖非 httpx 异常**：`_attempt` 抛 `anyio.ClosedResourceError` 之后，
   `OKXRestClient._client` 必须已被置空。**必须用这个异常类写，不能只用 `httpx.TransportError`**——
   用后者写的用例选不出「谓词过窄」这个错误，而生产日志证明前者真实发生过。
2. **[新增] 并发只换代一次**：3 个并发失败请求持有同一个旧 client 时，旧 client 只被 `aclose` 一次，
   且**不会**关掉期间新建的那一个（身份比对）。
3. **[新增·冻结更正] 取消不换代**：`asyncio.wait_for` 取消一次公共读之后，`_client` 必须**仍是原来那个**，
   且 `breaker.failures` 必须仍为 0（把 §3.8 的更正冻进用例）。
4. **[新增] 换代不发生在业务失败上**：`_attempt` 收到 HTTP 400 / OKX 业务码 `51000` 时，
   `_client` 必须**不变**（守住第 4 条实现约束）。
5. **[⚠️ 必须同提交处理·会红] `tests/test_okx_rest_resilience.py` 的注入缝会被 F1′ 打破。**
   该文件 `:63` 用 `client._client = fake` 绕过真实 httpx；F1′ 把 `_client` 置空后，
   `:152 test_a_transport_error_is_retryable_and_a_parse_error_is_not`（脚本是
   `[httpx.ConnectError, _Resp(200, _OK_BODY)]`）的**第二次尝试会去构造一个真实的
   `httpx.AsyncClient` 并打真网络**，断言 `len(fake.calls) == 2` 必然失败。
   修法建议：把建构抽成 `OKXRestClient._new_client()`，`_get_client()` 调它，测试夹具改为注入
   `_new_client`（一处改动同时修好全部 17 处 `client._client =` 风格的注入点里受影响的那些）。
   **这条不处理就是 CI 红 + 单测打真网络。**
6. **[保持绿·不许改] `:218 test_a_successful_probe_resets_the_breaker_and_its_cooldown`**——
   它冻结了「任一次成功即完全复位（含冷却时长）」，那正是第三节所依赖的自愈信号。
7. **[F2 专属]** 熔断打开后 `failures` 归零；连续 N 次探针失败不得让冷却单调上膛；
   回归 `repro/breaker_ratchet.py` 在第 3~4 次探针失败时不再出现 `cooldown=120s` 的单调上膛。
8. **[F3 专属]** `classify_exception` 对 `httpx.ProxyError` / `anyio.ClosedResourceError` 判 `retryable`，
   且 `_request` 会重试。（F3 不做则本条不写——不要把它挂在 F1′ 的用例里。）
9. **[端到端]** 模拟公共链路断 5 分钟后恢复：`/api/okx/connectivity` 的 `restPublic` 必须在
   **≤80 秒（一个清扫探针周期）**内回到 `up`，全程不重启进程。

### 六、评审记录的欠账（本轮**不修**，供后续排期）

- **`resp.json()` 在 `try` 之外**（`rest_client.py:231-237`）：解析异常会穿透 `_attempt` 与 `_request`，
  既不喂 `_note_fail`、也不被 `okx_error_handler` 归一，最终以 **HTTP 500** 冲到 ASGI 顶层，打破本仓库
  「OKX 不可达一律 502 `{error:{code,message}}`」的契约。与本 bug 无因果关系，独立记账。
- **私有侧对 `anyio.ClosedResourceError` 同样是盲区**（§3.6 评审注）：它会绕过 `_invalidate_pooled()`
  并变成 500。修法与 F1′ 同形，但属于私有链路，应单独立项而不是塞进本轮。
- **正文两处数字需订正**：§四表里 `/api/candles` 502 是 **121** 次（写作 102）；§3.8 的「1023 次 429」
  应删除。

### 七、判定（承接下方 `## 实施过程发现的方案缺陷`）

**证据链在补齐 §3.3 / §3.4 / §3.6 / §3.7 的评审注、并按 §3.5 / §3.8 更正之后，可以判定为严密。**
主因 A（公共 `AsyncClient` 永不换代）由四组彼此独立的证据锁死：进程重启是 4/4 的唯一出口且恢复恒在
2 秒内、同进程同代理下公私读非对称、失败恒 20.000s 且异常文本恒空、以及全仓只有一处 `aclose()`。
**最终推荐修法：F1′（并同轮携带零行为变更的 F4）。**
其余候选按第四节的优先级处置，其中 `limits=POOL_LIMITS` 与 F5-b 明确不做。

---

## 实施过程发现的方案缺陷

> 实施日期：2026-08-16 ｜ 实施范围：F1′ + F4（必做）、F2 + F5-a（评审列为「同轮做」的 P1）；
> F3 按评审判为 P2，本轮**不做**（故第五节验收判据第 8 条不写）。

### 一、F2 的「只放行一个半开名额」按字面写会造出**第二个「永远合不上」的死结**

评审第三节要求把 `_half_open` 那个共享布尔改成「只放行一个」的名额（令牌 / 时间戳）。按字面实现
——「发出名额 ⇒ 后来者一律拒绝 ⇒ 直到持有者调 `note_ok` / `note_failure` 才归还」——会立刻踩中
**本文自己刚刚证明过的那条铁律**（§3.8 评审注）：

- 名额持有者是一次普通公共读，而普通公共读**会被取消**：`market_window._fetch_tactical` 每 5 秒
  用 `asyncio.wait_for(…, 8s)` 硬砍一次；
- `CancelledError` 继承 `BaseException`，`_attempt` 的 `except Exception` 接不住它 ⇒ 它一路穿出
  `_request` ⇒ **`note_ok` 与 `note_failure` 一个都不会执行**；
- 于是名额被永久焊死，此后**每一次**普通公共读都拿 `OKX_CIRCUIT_OPEN`——一个用来自愈的机制自己
  变成了本文标题里那个故障的第二种形态。

**修正**：名额带**租期**（`_Breaker._half_open_lease_s = max(1.0, okx_link_rest_budget_s)`，即
「一次公共读最多能花多久」，不新增配置项）。租期一到名额自动归还，最坏情况退化成今天的「一小撮
并发一起试探」，而不是永久拒绝。冻结用例：
`test_the_half_open_slot_expires_instead_of_wedging_the_breaker`。

### 二、其余按原方案实施，无偏差

- **F1′ 谓词**照评审写成「进了 `client.get` 的 `except Exception` 分支」，未使用 `httpx.TransportError`；
  身份比对、先摘引用后关闭、吞掉关闭异常、不改 `limits=`、不上移到 `_note_fail`、取消不换代——
  六条实现约束逐条落地并各有冻结用例。
- **F2 的「探针失败不喂熔断」**落在 `_note_fail(probe=…)` 而非 `_Breaker` 内部：`_Breaker` 拿不到
  `probe` 这个事实，判据只能放在唯一持有它的那一层。
- **验收判据第 5 条（会红）已确认属实**：不改注入缝的话
  `test_a_transport_error_is_retryable_and_a_parse_error_is_not` 会在第二次尝试构造真实
  `httpx.AsyncClient` 打真网络。按建议抽出 `OKXRestClient._new_client()`，夹具改注入这条建构缝。
- **F4 顺带补的 `okx/ws` 那句 `connect_error:`**：`_reason_code` / `_reason_text` 只取第一个冒号
  之前的头部，故 `WS_CONNECT_ERROR` 这个错误码值域一字未变，属零行为变更。

### 三、主因缺少可跑的复现，已补一个

原报告的 3 个 repro 覆盖的是**放大器**（`breaker_ratchet.py`）与**已排除的岔路**
（`proxy_error_classification.py`），**主因 A 本身没有任何可跑的复现**，而第五节验收判据第 9 条
（断 5 分钟后恢复）需要它。新增
`repro/client_regeneration.py`（无外网、约 15 秒），按 §3.4 评审注收窄后的机制——
**连接池槽位账目占满 ⇒ `PoolTimeout`**——用一条 `max_connections=1` 的真实 `AsyncClient` 把唯一
槽位永久占住，服务端全程健康。实跑输出（对照臂把 `_invalidate_client` 换成 no-op 即修复前行为）：

```
=== 对照臂：公共 AsyncClient 永不换代（修复前） ===
  read #1..#5: [OKX_UNREACHABLE] 无法连接 OKX (/api/v5/public/instruments): PoolTimeout:
  read #6:     [OKX_CIRCUIT_OPEN] 交易所连接暂时熔断，21 秒后自动重试 (/api/v5/public/instruments)
  链路面板：行情接口(REST) state=down circuit=open
  probe #1/#2: [OKX_UNREACHABLE] 无法连接 OKX (/api/v5/public/time): PoolTimeout:
  客户端代数=1 => 探针永远失败 => 熔断等不到那次让它复位的成功。出口只剩：重启后端进程。

=== 实验臂：F1′ 传输失败即换代 ===
  read #1: [OKX_UNREACHABLE] 无法连接 OKX (/api/v5/public/instruments): PoolTimeout:
  read #2: 200 OK（下单面板可用）
  read #3: 200 OK（下单面板可用）
  链路面板：行情接口(REST) state=degraded circuit=closed
  客户端代数=2；熔断 failures=0 opened_until=0 => 一次都没打开，全程未重启进程。
```

对照臂第 6 行与用户截图**逐字同形**，实验臂在**第二跳**（远早于验收要求的 80 秒）即恢复。
顺带：`PoolTimeout:` 这个类名正是 F4 补上的——生产日志里这个位置三天空了 1748 次，
§3.4 那点「`PoolTimeout` 还是 `ReadTimeout`」的残余不确定性，下次复发看一眼日志即可定死。

### 四、`repro/breaker_ratchet.py` 的一处适配（非缺陷）

该脚本**手工**调 `breaker.ensure_closed_or_raise()` 替探针申请半开名额（生产里探针从不申请）。
名额改成「只发一个」之后，它第二跳起会如实抛 `OKX_CIRCUIT_OPEN` 并中断脚本，故用
`contextlib.suppress(OKXError)` 包住那一行。修复后实跑：`failures` 开闸即归零、冷却**恒为 20s**，
20 → 40 → 80 → 120 的单调上膛消失（验收判据第 7 条）。

---

## 终审记录（Final Review）

> 终审日期：2026-08-16 ｜ 终审对象：`main.py` / `okx/rest_client.py` / `okx/ws_supervisor.py` /
> `tests/test_okx_rest_resilience.py` 的在制改动 + `repro/` 四个脚本 ｜ 结论：**与本文逐条对齐，
> 补两处缺口后合入。**

### 一、逐条对齐（无偏差）

| 承诺 | 落点 | 核验 |
|---|---|---|
| F1′ 谓词 = 「进了 `client.get` 的 `except Exception` 分支」 | `rest_client.py:291-299` | ✅ 不是 `httpx.TransportError`；`anyio.ClosedResourceError` 有专门冻结用例 |
| 身份比对 / 先摘引用后关闭 / 吞关闭异常 | `_invalidate_client`（`:214-230`） | ✅ 三条各有用例（并发只换一次、不拆别人刚建的、`aclose` 失败只落 debug 日志） |
| 不改 `limits=` | `_new_client`（`:195-207`） | ✅ 建构参数与 HEAD 逐字一致，仅把建构从 `_get_client` 抽出来 |
| 换代不上移到 `_note_fail` | `_note_fail`（`:322-330`）无换代 | ✅ HTTP 400 与业务码 `51000` 均冻结「客户端不变」 |
| 取消不换代 | `except Exception` 天然不接 `CancelledError` | ✅ 用真实 `wait_for` 取消冻结（`_client` 不变且 `failures==0`） |
| F4 观测补全（三处 + WS 一处） | `_exc_label`（`:55-66`）、`ws_supervisor.py:140` | ✅ `_reason_code`/`_reason_text` 只取首个冒号前的头部，`WS_CONNECT_ERROR` 值域一字未变 |
| F5-a 拆开清扫协程的连坐 | `main.py:261-275` | ✅ 两个 `try` 各自收口，`settings` 不跨块引用 |

### 二、终审补的两处缺口

1. **F5-a 缺回归用例（CLAUDE.md §七）。** 它是一条真实的行为修复（`pool.sweep()` 三天没跑过），
   却是本轮唯一没有冻结用例的改动。补
   `test_the_link_sweeper_sweeps_the_pool_even_when_the_clock_sync_fails`：替身时钟的 `sync`
   抛 `OKX_UNREACHABLE`，断言 `pool.sweep()` 仍被调到。**已确认它在 HEAD 版 `main.py` 上为红**
   （`asyncio.wait_for` 2s 超时，日志里正是那句 `okx link sweeper tick skipped`）。
2. **`repro/client_regeneration.py` 在本项目的默认控制台上跑不完。** 它在两处 `print` 里用了
   `⇒`（U+21D2），而 Windows 控制台默认 GBK 编不出这个字符——按 §2 那条 PowerShell 命令直接跑，
   脚本会在对照臂末尾以 `UnicodeEncodeError` 崩掉，实验臂（也就是证明修复有效的那一半）**根本
   不会执行**。打印改用 ASCII `=>`（正文引用的输出同步订正），docstring 里的 `⇒` 不受影响。
   实跑复核：默认控制台下两臂均跑完，对照臂恒 `PoolTimeout` 且探针永不成功，实验臂第二跳即
   200 OK、熔断一次都没打开。

### 三、终审跑过的验证

- `pytest tests/test_okx_rest_resilience.py` → **27 passed**（新增 1 条）。
- OKX 相关 20 个测试文件（`client_pool` / `gap_repair` / `link_api` / `link_state` / `ws_*` / …）
  → **271 passed**。
- 全量 `pytest`（`services/quant`）→ 见提交说明；既有 7 条失败全部落在**未跟踪**的
  `tests/test_factor_library_expansion.py`（它断言一个 20 因子库，本树只有 13 个），与本次改动无关。
- `ruff check` 四个改动文件 → All checks passed。
- `repro/breaker_ratchet.py` → 冷却**恒为 20s**，20→40→80→120 的单调上膛消失（验收判据第 7 条）。
- `repro/client_regeneration.py` → 见上第 2 条。

### 四、终审明确**不改**的两处（留档，避免下一轮当成缺陷重提）

1. **`_invalidate_client` 会在其它公共读在途时关掉连接池**，那些在途请求会被打断。这与私有侧
   `client_pool.invalidate()` 的既有姿态完全一致（它同样不看 refcount 就 `_safe_aclose`），且被
   打断者会在自己的重试预算内拿到新客户端重来一次；给公共单例引入引用计数属于新机制，超出本轮。
2. **`_exc_label` 会给 `OKXError` 也加类名前缀**（`path: OKXError: [OKX_HTTP_ERROR] …`）。信息冗余但
   不丢东西，且正是评审第四节对 F4 的字面要求；改它属于纯观感调整，不在本轮范围内。
