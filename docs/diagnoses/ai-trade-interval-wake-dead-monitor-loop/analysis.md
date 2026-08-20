# AI 自动交易「定时触发」失效 —— 盯市循环被一个非 OKXError 异常打死

- **诊断日期**：2026-08-19
- **代码基线**：`master` @ `cec907d`（工作树无未提交的 tracked 改动）
- **产物**：本文 + 可直接运行的最小复现 `repro.py`（同目录）
- **结论一句话**：墙钟定时唤醒 `_maybe_interval_wake()` 是**挂在盯市循环最后一行**的；私有 REST
  在代理抖动 / 休眠唤醒时抛出的 `anyio.ClosedResourceError` **不是 httpx 异常、也不是 OKXError**，
  它穿透四层 `except` 把盯市任务**永久打死**，于是墙钟唤醒（以及利润保护、会话止盈止损、交易所侧
  兜底等全部盯市职责）静默停摆，而挂在另一条 WS 回调链上的确认 K 线唤醒照常工作 —— 这正是用户
  看到的「只有信号还能触发 Agent，定时触发再也不来了」。

---

## 一、问题描述

【AI 自动交易】开启后：

- **仍然有效**：策略信号（确认 K 线收线）能正常触发 Agent 的分析与执行；
- **已失效**：配置的「Agent 唤醒周期」（墙钟定时触发）不再触发任何决策轮；
- 面板**照常显示「运行中 / 已武装」**，没有任何报错、没有降级横幅、没有断连提示。

用户环境（取自生产库 `%APPDATA%/aragon-desktop/aragon.db`，`strategy_runs` 最近 5 行
`id=1605..1609`）：

| 项 | 值 |
|---|---|
| 分组 | `aitradegrp_fd49f82bd690476ab614958b7e83c925`（5 标的：BTC / ETH / SOL / DOGE / SATS） |
| 执行周期 `bar` | `30m` |
| `wakeIntervalMinutes` | **3.0**（= 墙钟每 3 分钟唤醒一次） |
| 交易周期模式 | `intraday_swing`（日内波段） |
| 模式 | `ai-trade`（实盘） |

也就是说：**正常情况下每根 30m K 线之间应当有约 9 次墙钟唤醒**，K 线唤醒只占决策轮的一小部分。
`metrics_json.finalSummary` 里 Agent 自己的记述可以逐字印证这一点（健康时段）：

> `1605` BTC：「**91–95、97–100 轮为同份数据重复定时唤醒**（各合计一次观望证据）；**96 轮为 K 线
> 触发的独立研判**」
> `1609` SATS：「累计约 110 轮唤醒（rounds 101-103、105-109 为**重复定时唤醒**…）」

`roundsCompleted` 分别为 106 / 180 / 133 / 127 / 111 —— 而同期 30m 确认根只有几十根，多出来的轮
全部来自墙钟唤醒。**这条唤醒源本来是主力，它一停，Agent 的思考频率就掉到 1/9。**

---

## 二、复现步骤

### A. 最小复现（离线、确定性、约 12 秒，推荐先跑这个）

```bash
cd services/quant
python -u ../../docs/diagnoses/ai-trade-interval-wake-dead-monitor-loop/repro.py
```

它构造一个真实的 `AiTradeSession`（`wake_interval_s=120`、盯市周期压到 1s、LLM / OKX 全桩），
真的跑 `_monitor_loop()`，然后在**持仓读数**上注入一次 `anyio.ClosedResourceError`（生产里真实
发生的那一类），实测输出：

```
[0] candle round done, rounds=1
[1] baseline interval wake fired=True, rounds=2                     <- 盯市循环活着时定时唤醒正常
[2] monitor task dead=True, exception=ClosedResourceError()         <- 一次异常即打死盯市任务
[3] after 5s: rounds=2 (was 2) -> interval wake DEAD                <- 定时唤醒永久停摆
[4] candle wake still alive=True, rounds=3                          <- K 线唤醒照常（= 用户症状）
[5] lifecycle wire: state=running armed=True reason=armed nextWakeInS=120   <- 面板仍说「已武装」
REPRODUCED: ...
```

退出码 0 = 复现成功。**第 [4] 行与第 [5] 行就是用户报告的两个观察面**：信号还能触发、界面还说
一切正常。

### B. 生产复现（真实桌面端，验证同一条路径）

1. 启动桌面端，开启【AI 自动交易】，任选 ≥2 个标的、`bar=30m`、**唤醒周期填 3 分钟**；
2. 等首轮决策完成（面板出现「下一次决策 xxx 秒」倒计时）；
3. 制造一次**私有 REST 传输层抖动**——任选其一，效果等价：
   - 把系统代理（本机 Clash 在 `127.0.0.1:7897`）**停掉 40 秒再开回来**；
   - 或直接**合上笔记本睡眠 2 分钟再唤醒**（生产日志里就是这一条）；
4. 网络恢复后观察 **10 分钟**：
   - 面板「下一次决策」倒计时**走到 0 就卡住不动**，不再有新的决策轮（`nextWakeInS` 由
     `session.py:862` 计算，只有决策轮开始才会被重置）；
   - 只有到 30m 整点收线时才会蹦出一轮（K 线触发），随后倒计时再次卡在 0；
   - 顶栏 / 面板**不会**出现任何降级或断连提示，状态仍是「运行中」。
5. 取证：结束该分组或再开一组新的（让旧 session 对象被回收），然后看
   `%APPDATA%/aragon-desktop/logs/backend.log`，会出现
   `Task exception was never retrieved … AiTradeSession._monitor_loop() … exception=ClosedResourceError()`。
   ⚠️ 这条日志是在**任务被 GC 时**才打印的（session 一直持有 `_monitor_task` 引用），所以
   **循环早已死了很久，日志才姗姗来迟** —— 这也是故障如此隐蔽的原因之一。

---

## 三、根因分析

### 3.1 现场证据（生产日志，非推测）

`%APPDATA%/aragon-desktop/logs/backend.log`（147 MB，最后一次进程运行 2026-08-18 12:28 →
2026-08-19 02:44）：

```
2026-08-19 02:38:48,248 ERROR asyncio: Task exception was never retrieved
future: <Task finished name='Task-2043' coro=<AiTradeSession._monitor_loop() done,
        defined at app\ai_auto\session.py:898> exception=ClosedResourceError()>
Traceback (most recent call last):
  File "app\ai_auto\session.py", line 906, in _monitor_loop
  File "app\ai_auto\session.py", line 915, in _monitor_tick
  File "app\live_auto\executor.py", line 622, in reconcile
  File "app\live_auto\executor.py", line 169, in _default_read_positions
  File "app\okx\trading.py", line 524, in fetch_live_positions
  File "app\okx\private_client.py", line 254, in get_positions
  File "app\okx\private_client.py", line 213, in _get
  File "httpx\_client.py", line 1768, in get
  ...
  File "httpcore\_async\http11.py", line 376, in start_tls
  File "anyio\streams\tls.py", line 167, in wrap
  File "anyio\_backends\_asyncio.py", line 1293, in receive
    raise ClosedResourceError from None
anyio.ClosedResourceError
```

**同一份日志里连着 4 条**（`Task-2043 / 2128 / 2384 / 2507`，02:38:48.248 ~ .288）：**4 个 AI 自动
交易会话的盯市循环以完全相同的方式终结**。行号与当前 HEAD 源码**逐行对得上**（`session.py:898`
= `_monitor_loop` 定义、`:906` = `await self._monitor_tick()`、`:915` = `reconcile`），说明用户跑的
就是这棵树的产物。

⚠️ **这四条日志的时间戳是任务被 GC 的时刻，不是死亡时刻**（`session._monitor_task` 一直持有引用，
`Task.__del__` 才打印「never retrieved」）。02:38:48 恰好是两次 `POST /ai-trade/<grp>/resume` 被
拒（**409**，`cec907d` 新增的武装复检）之后旧 session 对象被丢弃的时刻。**故本日志只能证明
「这条死法在生产上真实发生过、发生了 4 次」，不能用来断言死亡时刻** —— 而「死了很久都没有任何
信号」本身就是缺陷的一部分（见 3.5）。

**可用于验收的量化指标**：`/account/positions` 是 `reconcile` 的 5s 心跳。健康时段（08-18
21:20–21:37）该端点 **66 次/分钟**，其中前端 `/api/trading/positions` 只占 **11 次/分钟**，
余下 ~55 次/分钟 = **5 个成员 × 12 次/分钟**，与 `live_auto_poll_interval_s=5` 逐字吻合。
**一个成员的盯市循环死亡 ⇒ 该成员那 12 次/分钟永久消失且不会自行恢复**，这是修复前后最直接的
对账口径。（日志里 21:38 → 02:36 的整段空白是**机器休眠**，不是本缺陷；02:37 唤醒瞬间伴随大量
`getaddrinfo failed` / `WinError 10054` / `WinError 64` —— 那正是本缺陷所需的传输层抖动条件。）

**关键对照（同一份日志、同一个异常类，公有链路却优雅降级）**：

```
2026-08-17 23:20:18 WARNING aragon: okx link sweeper tick skipped:
    [OKX_UNREACHABLE] 无法连接 OKX (/api/v5/public/time): ClosedResourceError:
2026-08-17 23:38:40 WARNING app.ai_auto.resume: ai-trade resume exchange probe failed (...):
    [CONNECTION_FAILED] 无法连接 OKX (/api/v5/public/instruments): ClosedResourceError:
```

同样是 `anyio.ClosedResourceError`：**走公有 REST 客户端的被归一成 `OKX_UNREACHABLE` 并降级，
走私有 REST 客户端的直接把任务打死。**这不是偶然，见 3.3。

### 3.2 为什么「只剩信号触发」——两条唤醒源的宿主不同

AI 自动交易有三条唤醒源，宿主任务**不一样**：

| 唤醒源 | 代码位置 | 宿主任务 | 本故障下 |
|---|---|---|---|
| 确认 K 线 | `session.py:1130` `feed_candle()` | **OKX WS 回调**（`OKXWSClient` 自带重连 supervisor） | ✅ 存活 |
| 信号翻转（轴 C） | `session.py:951` `_maybe_signal_flip_wake()` | 盯市循环 | ❌ 死亡（且 `ai_trade_signal_wake_enabled` 默认 False，`config.py:277`，生产本就没开） |
| **墙钟定时** | `session.py:952` `_maybe_interval_wake()` | **盯市循环** | ❌ 死亡 |

```python
# services/quant/app/ai_auto/session.py:898
async def _monitor_loop(self) -> None:
    try:
        while self.is_active:
            await asyncio.sleep(self.poll_interval_s)
            if not self.is_active:
                break
            await self._monitor_tick()          # :906
    except asyncio.CancelledError:              # :907  <-- 只吞取消，其它异常一律终结本任务
        pass
```

`_monitor_tick()` 的**最后一行**才是墙钟唤醒（`session.py:952`）。循环一死：

- `_maybe_interval_wake()` 再也不会被调用 → 定时触发彻底消失；
- 没有任何东西会重启它 —— 全仓只有 `resume()`（`session.py:788-789`）在会话从 `paused` 回到
  `running` 时会检查 `_monitor_task.done()` 并重建。用户不点「继续」，它就永远躺着。

而 `feed_candle` 由 WS 客户端回调驱动，与盯市任务完全独立，所以**确认根照常起轮** ——
症状与用户描述逐字吻合。

### 3.3 异常为什么能穿透所有 `except` —— 四层漏斗，层层用错谓词

```
anyio.ClosedResourceError            (代理 CONNECT 隧道在 TLS 握手中途被拆)
  ↑ private_client.py:226   except httpx.TransportError      ← 它不是 httpx 异常，不匹配
  ↑ trading.py:525          except OKXError                  ← 不匹配
  ↑ executor.py:623         except OKXError                  ← 不匹配
  ↑ session.py:907          except asyncio.CancelledError     ← 不匹配 ⇒ 任务终结
```

1. **`services/quant/app/okx/private_client.py:226`**
   ```python
   except httpx.TransportError as exc:   # 仅传输层异常重试，鉴权/业务错误立即上抛
       last_exc = exc
       rate_limited = False
       await self._invalidate_pooled()   # :229 抖动即整池换代
   ```
   谓词是**异常类型**。`anyio.ClosedResourceError` 由 httpcore 的 anyio 后端在 TLS 握手里抛出，
   httpx 的 `map_httpcore_exceptions` 不认识它、原样放行 ⇒ 私有 GET **直接把裸异常抛给调用方**，
   而不是承诺中的 `OKXPrivateError`。

2. **`services/quant/app/okx/trading.py:520-530`** `fetch_live_positions` 只 `except OKXError`。

3. **`services/quant/app/live_auto/executor.py:621-624`**
   ```python
   try:
       positions = await self._read_positions()
   except OKXError as exc:                       # :623
       return events + self._on_reconcile_failure(ts, exc)
   ```
   `reconcile` 的 docstring 明写「传输层异常**已被** private/trade_client 归一为
   `OKXPrivateError(OKXError)`」—— 这个前提在第 1 层就已经不成立了。既有的降级机制
   （`_on_reconcile_failure` → 连续失败推 `LIVE_AUTO_RECONCILE_DEGRADED`）因此**完全没有被触发**。

4. **`services/quant/app/ai_auto/session.py:907`** 只吞 `CancelledError`，任务终结。

**这一层层的谓词错误，仓库自己早就知道了。** 公有 REST 客户端在 `ec3c9f9` 已经改对，并把理由写在
注释里（`services/quant/app/okx/rest_client.py:291`）：

```python
except Exception as exc:  # noqa: BLE001 —— 分级交给 classify_exception，此处只搬运
    # 失效即换代。谓词是「进了这个 except 分支」而不是 `httpx.TransportError`：… 而生产日志
    # 证明真实发生过的失败类里有 `anyio.ClosedResourceError`（代理 CONNECT 隧道在 TLS 握手中途
    # 被拆），它根本不是 httpx 异常——用类型当谓词会恰好在**已知会发生**的那一类上静默失效。
```

配套的回归用例 `services/quant/tests/test_okx_rest_resilience.py:313`
（`test_a_non_httpx_transport_failure_also_swaps_the_client`）里甚至有一句原话：

> 「用 `httpx.TransportError` 当谓词的写法，恰好会在这个**已知会发生**的类上静默失效
> （**私有侧现在就正栽在这上面**）。」

**修法只做了公共侧，私有侧留在原地 —— 而 AI 自动交易的盯市循环恰恰只走私有侧。**

### 3.4 放大器：共享连接池「立刻 aclose」把一次抖动变成 N 个死亡

`3c2155b` 把私有客户端改成**按凭证指纹共享的进程级连接池**。抖动时的换代动作是：

```python
# services/quant/app/okx/client_pool.py:95
async def invalidate(self, fp: str) -> None:
    async with self._lock:
        entry = self._entries.pop(fp, None)     # 从表里摘掉（这一步是对的）
        ...
    if entry is not None:
        await _safe_aclose(entry.client)        # <-- 不看 refcount，直接关
```

`_Entry` 明明记着 `refcount`，`invalidate` 却**无视它**直接 `aclose()`。而 5 个成员会话每 5 秒各打
一次 `/account/positions`，用的是**同一个** `httpx.AsyncClient`：

> 会话 A 撞到一次真实的 `httpx.TransportError` → `_invalidate_pooled()` → 池 `aclose()` →
> **正在飞的会话 B/C/D/E 的请求当场收到 `anyio.ClosedResourceError`** → 它们各自沿 3.3 的漏斗
> 一路裸奔 → **一次抖动可以一口气打死多个盯市循环**。

这条机制由上面的代码直接读出（`invalidate` 不看 `refcount`；`httpx.AsyncClient.aclose()` 会关掉
底层连接池里**正在使用**的连接），并与日志现象一致：致命的 4 条全部落在私有侧，而同期公有侧
的同类异常只留下几条 WARNING。

换代前，`3c2155b^` 的私有客户端是**每次调用现建现关**的短生命周期客户端 —— 一次抖动只能伤到
自己那一个请求，伤不到别的会话。所以：

- **潜在缺陷**（类型谓词 + `except OKXError` 链 + 会死的守护循环）是老的；
- **把它从「偶发」推成「代理抖动 / 休眠唤醒基本必中，且一中就是一片」的，是最近这几笔改动**
  （`3c2155b` 共享池 + 立刻 `aclose`；`ec3c9f9` 只修了公共侧的谓词）。用户「最近的修改导致定时
  触发失效」的判断方向是对的。

### 3.5 为什么用户什么提示都没看到

`cec907d` 刚上线的生命周期真值层（`ai_auto/lifecycle.py:95` `classify_member`）判定阶梯是
`stopped → paused → not_warmed → feed_down → feed_stale → armed`，**读的全是行情侧读数**
（窗口根数、`feed_age_s`、链路态）。盯市任务是死是活**不在判据里**。于是：

- WS 行情还在流 ⇒ `armed = true`、`state = running` ⇒ 面板「运行中 / 已武装」；
- `nextWakeInS = max(0, wake_interval_s - (now - _last_decision_started_wall))`（`session.py:862`）
  只会走到 0 然后**永远卡在 0**（没有决策轮去重置它）—— 这是界面上**唯一**能看出问题的地方，
  而且它长得就像「马上就要唤醒了」。

`repro.py` 的第 [5] 行把这一点也复现了：`state=running armed=True reason=armed`。

### 3.6 【评审补证】异常为什么「不是 httpx 异常」——精确到 upstream 的那一行

原文只说「httpx 的 `map_httpcore_exceptions` 不认识它」。实际机制比这更具体，且**决定了故障的边界**：

`httpcore/_backends/anyio.py`（本机 httpcore 1.0.9 / anyio 4.12.1 / httpx 0.28.1）里，
`AnyIOStream` 的三个方法各自带一张 `exc_map`：

```python
async def read(...):        exc_map = {TimeoutError: ReadTimeout,  anyio.BrokenResourceError: ReadError,
                                       anyio.ClosedResourceError: ReadError,   anyio.EndOfStream: ReadError}   # :26-31
async def write(...):       exc_map = {TimeoutError: WriteTimeout, anyio.BrokenResourceError: WriteError,
                                       anyio.ClosedResourceError: WriteError}                                   # :43-47
async def start_tls(...):   exc_map = {TimeoutError: ConnectTimeout, anyio.BrokenResourceError: ConnectError,
                                       anyio.EndOfStream: ConnectError,  ssl.SSLError: ConnectError}            # :61-66
                            #          ^^^ anyio.ClosedResourceError 独独缺席
```

`read` / `write` 都映射了 `ClosedResourceError`，**只有 `start_tls` 漏了**。于是：

- 在**已建立**的连接上，同一个 `ClosedResourceError` 会被归一成 `ReadError` / `WriteError` ——
  两者都是 `httpx.TransportError` 的子类，`private_client.py:226` 现有的 `except` **接得住**；
- 只有在 **TLS 握手** 那一段，它不被映射、原样穿出 httpcore ⇒ httpx 的 `map_httpcore_exceptions`
  只认 httpcore 异常，同样原样放行 ⇒ 调用方收到一个**裸 anyio 异常**。

生产 traceback 的最深四帧逐帧对上了这条路径，且它走的是**代理 CONNECT 隧道**：
`http_proxy.py:316`（`stream.start_tls(**kwargs)`）→ `http11.py:376`（`AsyncHTTP11UpgradeStream.start_tls`）
→ `_backends/anyio.py:70`（`TLSStream.wrap`）→ `anyio/streams/tls.py` → `_asyncio.py` `receive()`。

**结论**：故障被严格限制在「**新建连接 + 走代理 + TLS 握手中途**」这一段。这为 §四 的受影响用户
判断（(b) 走本地代理）提供了机制上的理由，而不只是相关性。

### 3.7 【评审补证】`ClosedResourceError` ⇒ 是**本进程自己**关的 ⇒ 3.4 不是放大器，是触发器

anyio 的 `SocketStream.receive()`（`_asyncio.py:1277-1281`）在读队列空时三选一：

```python
except IndexError:
    if self._closed:                    raise ClosedResourceError from None   # ← 只有这一条
    elif self._protocol.exception:      raise self._protocol.exception from None
    else:                               raise EndOfStream from None
```

而 `self._closed` 在**整个 anyio 里只有一处**会被置真 —— `SocketStream.aclose()`（`_asyncio.py:1322`）。
对端拆链、代理挂掉、网线拔了，走的都是 `protocol.exception`（→ `BrokenResourceError`）或 `EndOfStream`
两条路，**都被 `start_tls` 的 exc_map 正确映射成 `ConnectError`**。

> 也就是说：`anyio.ClosedResourceError` 在语义上等价于
> **「本进程在别人的读还在飞的时候，把那条 socket 关掉了」**。

全仓能做出这个动作、且**不看 refcount** 的地方只有一处 —— `client_pool.invalidate`（`client_pool.py:101-106`）。
其余三条关闭路径都是引用计数安全的：`release`（:90 `refcount == 0` 才关）、`sweep`（:112 同）、
`aclose_all`（:121，仅应用关停）。链路末端也确认无误：`httpx.AsyncClient.aclose()` →
`httpcore AsyncConnectionPool.aclose()`（`connection_pool.py:347-353`）把 `self._connections` **整表**
取走交给 `_close_connections`（:341-345），**不区分该连接此刻是否正在服务一个请求**。

**⇒ 原文 3.4 把 `invalidate` 立即 `aclose` 定性为「放大器」是低估了：它是这一类异常的必要触发条件。**
没有一次并发的本地 `aclose`，`start_tls` 里根本产生不出 `ClosedResourceError`。

**端到端实证（真 httpx / httpcore / anyio，无 mock，脚本见 §七.4）**：

```
exception type              : anyio.ClosedResourceError
is httpx.TransportError     : False          <- 现有 except 谓词恰好不匹配
is httpx.HTTPError          : False
is anyio.ClosedResourceError: True
--- deepest frames ---
  \httpcore\_backends\anyio.py:79 in start_tls
  \httpcore\_backends\anyio.py:70 in start_tls
  \anyio\streams\tls.py:172 in wrap
  \anyio\streams\tls.py:187 in _call_sslobject_method
  \anyio\_backends\_asyncio.py:1278 in receive
```

触发动作正是「请求挂在 TLS 握手上时，另一个协程 `aclose()` 掉共享客户端」——`invalidate` 干的就是这一下。

### 3.8 【评审补证】时间上的自然实验：共享池落地前的两个月，一次都没有过

全量扫描 `backend.log`（**1,298,764 行，覆盖 2026-06-13 → 2026-08-19**，跨两个月同一台机器、
同一个 Clash 代理、同样会休眠），按日统计**裸终结行** `anyio.ClosedResourceError`：

| 日期 | 次数 |
|---|---|
| 2026-06-13 ~ 2026-08-12 | **0** |
| 2026-08-13 | 5 |
| 2026-08-17 | 27 |
| 2026-08-18 | 5 |
| 2026-08-19 | 4 |
| **合计** | **41** |

而 `3c2155b`（引入 `client_pool.py` 共享池 + 无视 refcount 的 `invalidate`）的提交日期是
**2026-08-12** —— 首次出现在**次日**。原文「自 2026-08-13 起共出现 41 次」的计数**核对无误**
（口径 = 独占一行的异常终结行；含 traceback 帧与消息内文的「提及」共 91 处）。

同一次扫描还给出两条旁证：

- 全日志里 `Task exception was never retrieved` **总共只有 4 条**，全部是
  `AiTradeSession._monitor_loop`，全部在 `2026-08-19 02:38:48`。这类死法在本仓库不是背景噪音。
- 唯一 5 条「被优雅降级」的 `ClosedResourceError` 全部落在 2026-08-17 的**公共**链路
  （`OKX_UNREACHABLE` / `CONNECTION_FAILED` 消息内文），与 3.1 的对照一致。

**⇒ 这条时间线独立支持 3.7 的机制结论，并逐字印证用户「最近的修改导致失效」的判断。**

### 3.9 【评审补证】健康基线的直接数据：轮间隔就是 3 分 0 秒

原文只用 `roundsCompleted` 的总数做推断。生产库 `metrics_json.decisionsDigest` 其实**逐轮带 `ts`**，
可以直接还原轮间隔（取 `id=1605..1609`，digest 保留最近 50 轮）：

```
1605 BTC : r97@21:03(+3m7s) r98@21:06(+3m15s) r99@21:09(+3m1s) r100@21:12(+3m2s)
           r101@21:15(+3m1s) r102@21:18(+3m4s) r103@21:21(+3m2s) r104@21:24(+3m4s) r106@21:33(+8m32s)
1606 ETH : r174@21:18(+3m1s) r175@21:22(+3m22s) r176@21:25(+3m3s) r177@21:28(+3m0s) r180@21:36(+3m11s)
1607 SOL : r127@21:17(+3m1s) r128@21:20(+3m20s) r129@21:23(+3m4s) ... r133@21:37(+4m44s)
```

**轮间隔稳定在 3 分 0 秒 ~ 3 分 5 秒，与 `wakeIntervalMinutes=3.0` 逐字吻合**（30m 执行周期下，
K 线唤醒不可能产生这个节奏）。这比「多出来的轮数」是强得多的证据：**墙钟唤醒此前确实是主力唤醒源。**

每个成员的**最后一轮**落在 **08-18 21:33 ~ 21:37**，且 `roundsCompleted`（106/180/133/127/111）
**恰等于各自 digest 里的最大轮号** ⇒ 此后再没有任何一轮。随后 21:38 机器休眠 → 02:37 唤醒并撞上
`getaddrinfo failed` / `WinError 10054` / `WinError 64` 风暴 → 02:38:48 四条盯市任务的死讯被 GC 打印。

> ⚠️ **诚实的边界（评审加注）**：日志在 **02:44** 结束，唤醒后只有 ~7 分钟窗口，按 3 分钟周期
> 期望仅 ~2 次定时轮。因此「定时轮停了」这一点**归档数据的样本量不足以单独证明**；它由
> 「3.6-3.7 的机制 + `repro.py` 的确定性复现 + 用户现场观察到的『K 线还在、定时没了』」三者共同
> 支撑。本报告不把这 7 分钟当作定量证据。

### 3.10 【评审补证】备选解释的逐条排除

一个称职的复核必须先问「还有什么能让**只有**定时唤醒消失」。逐条排除如下：

| # | 备选解释 | 结论 | 排除依据 |
|---|---|---|---|
| A | **空仓重复唤醒守卫**（F4c hold-inertia，`wake_guard.py`）持续跳过 | **排除** | 它有饿死兜底：`ai_trade_wake_max_skip_streak` 默认 **5**（`config.py:256`），连续跳过到 5 即**强制放行**一轮。守卫最多把 3 分钟拉长到 ~18 分钟，做不到「永久不来」。且 `repro.py` 已把 `ai_trade_wake_skip_unchanged_pct` 置 0，与本路径正交。**这是最像的竞争假设，必须显式排除。** |
| B | `_deciding` 卡死（决策任务永不收尾） | **排除** | `feed_candle`（`session.py:1140`）同样有 `if self._deciding: return`。若 `_deciding` 卡死，**K 线唤醒也会一起停**——与用户观察矛盾。 |
| C | 会话被暂停 / 已终止 | **排除** | 同上，`feed_candle:1138` 在 `state != 「running」` 时早退，两条唤醒源会一起停；且 `classify_member` 会把 paused/stopped 如实报到面板。 |
| D | `_stale_degraded` 为真 | **排除** | 它与 `classify_member` 的 `feed_age_s > stale_after_s` 是**同一判据**，面板会显示「行情已 … 没有新 K 线，Agent 暂不响应」——与「面板一切正常」矛盾。 |
| E | `wake_interval_s <= 0`（配置没读进去） | **排除** | 生产库该组 5 行全是 `wakeIntervalMinutes=3.0`，且 3.9 的轮间隔实测就是 3 分钟。 |
| F | 盯市 tick **挂住**（hang）而非**死掉** | **本次不成立，但必须记账** | 本次有 4 条 traceback 证明是任务终结。但「挂住」会产生**完全相同的症状**，且 **F1 挡不住它**、`_monitor_task.done()` 也**发现不了它**——见评审结论对 F4 的设计修正。 |

**一条结构性观察（原文没有，但它解释了症状的「选择性」）**：`_maybe_interval_wake()` 是
`_monitor_tick` 的**最后一行**（`session.py:952`）。因此它是整条 tick 的**金丝雀**——tick 里
**任何**一步出问题（修复前是杀死循环，修复后是中止本 tick），症状都恰好是
「定时触发没了、K 线触发还在」。这既解释了本次故障，也意味着**同样的用户报告以后还会再来**，
只是根因换一个。这一点直接决定了下面的推荐次序。

---

## 四、影响面

盯市循环 `_monitor_tick()`（`session.py:910-952`）承载的**全部**职责一起停摆，按危害排序：

| 失效项 | 代码 | 后果 |
|---|---|---|
| **利润保护平仓**（阶梯回吐 / 承诺退出线） | `_maybe_profit_exit` | 浮盈仓位失去本地退出执行者 |
| **会话级止盈止损** | `_maybe_session_exit` | 会话总亏损闸不再触发 |
| **交易所侧兜底核实 + 自愈补挂** | `schedule_from_executor` / `schedule_repair` | 交易所侧 OCO 掉了也没人补 |
| **持仓真值 reconcile** | `executor.reconcile` | 外部/交易所侧平仓不再被感知，`projected` 与浮盈冻结在故障那一刻 |
| **行情停滞看门狗** | `_check_staleness` | 断流不再降级；且 `_stale_degraded` 一旦为真也**再无处清零**（`_clear_stale_if_recovered` 同样只在这条循环上） |
| **墙钟定时唤醒**（本次报告的主诉） | `_maybe_interval_wake` | 决策频率掉到只剩 K 线，`wake_interval` 配置形同虚设 |
| 信号翻转唤醒（轴 C） | `_maybe_signal_flip_wake` | 默认关，暂无影响 |
| 持仓周期倒计时 / 冷却过期 / 成本维护 / 可续跑心跳 | `_horizon.refresh` / `_expire_profit_cooldown` / `_cost.schedule_maintenance` / `_beat_state_row` | 面板 chip 不动、`lastSeenTs` 冻结（续跑卡会误报「离线很久」） |

**受影响用户**：所有开启【AI 自动交易】实盘的用户，尤其是
（a）多标的分组（共享连接池 ⇒ 一次抖动打翻一片）、
（b）走本地代理（Clash 等，CONNECT 隧道随时被拆）、
（c）笔记本会休眠的用户。本机日志显示 `anyio.ClosedResourceError` 自 2026-08-13 起共出现 **41 次**。

**同形缺陷（同一根因、另一个入口）**：`services/quant/app/live_auto/session.py:231` 的
多策略实盘 `_monitor_loop` 是**逐字同构**的写法（`try: while ... except asyncio.CancelledError`），
它的 `_monitor_tick` 同样以 `executor.reconcile` 开头 ⇒ **多策略自动交易的盯市与会话止盈止损会以
完全相同的方式静默死亡**。本次不改代码，但修复轮必须一并处理。

**写路径的次生风险**：`okx/trade_client.py:62` 的 `_post` 用的是同一个 `except httpx.TransportError`
谓词。下单途中撞上 anyio 异常时，`may_retry_write` 的幂等分级判定会被整个绕开，裸异常直接上抛
（目前被决策轮的 `except Exception`（`session.py:1254`）兜住，不至于崩会话，但「写状态未知」的
既有语义没有被执行）。

---

## 五、候选修复

| # | 方案 | 落点 | 侵入性 | 风险 | 工作量 | 覆盖面 |
|---|---|---|---|---|---|---|
| **F1** | **守护化盯市循环**：`while` 体内 `try/except Exception` —— 记日志 + 连续失败计数 + 达阈推一条降级 `error` 事件，然后**继续下一跳**；`CancelledError` 仍原样上抛 | `ai_auto/session.py:898-908`、`live_auto/session.py:231-245` | 极小（各 ~8 行） | 低。唯一要防的是「每 5 秒刷屏」⇒ 用既有 `_reconcile_fail_streak` 同款节流 | 0.5 天（含 2 条用例） | **最广**：兜住 tick 里**任何**步骤的任何异常，包括未来新增的 |
| **F2** | **把 `ec3c9f9` 的谓词修法镜像到私有侧**：`private_client._get:226` 与 `trade_client._post:62` 的 `except httpx.TransportError` 改为「进了 `client.get/post` 的 except 分支即传输失败」（`except Exception` + `classify_exception`），归一为 `OKXPrivateError` | `okx/private_client.py`、`okx/trade_client.py` | 小（2 处 except + 用例） | 中。`except Exception` 也会把编程错误当传输失败重试 —— 公共侧已接受同样取舍并写明理由；写路径必须保住 `may_retry_write` 的幂等分级 | 0.5 天 | 只覆盖这一条路径，但**让既有降级链路（`_on_reconcile_failure` → 降级横幅）真正生效** |
| **F3** | **连接池换代不误伤在飞请求**：`OKXClientPool.invalidate` 先从表里摘掉（保持「下一跳拿新连接」的既有承诺），`aclose()` 推迟到 `refcount == 0` 或一个宽限期之后 | `okx/client_pool.py:95-106` | 小（~15 行 + 用例） | 中低。需保证不泄漏（宽限期兜底关闭 + `sweep` 收尾） | 0.5 天 | 断掉**放大器**：一次抖动不再制造 N 个 `ClosedResourceError` |
| **F4** | **让「任务死了」可见并自愈**：`lifecycle.MemberReadings` 增 `monitor_alive` 读数并纳入 `armed` 判据；前端既有的 15s 轮询每次都会打到 `/ai-trade/status` → `group_status()` → `lifecycle_readings()`，在那条既有路径上判 `_monitor_task.done()`（零新增请求），发现即重建 + 推一条 `error` | `ai_auto/lifecycle.py`、`ai_auto/session.py`、`ai_auto/manager.py`、前端武装清单 | 中 | 中。重启须幂等、须防重启风暴（退避 + 上限） | 1.5 天 | 把「静默死亡」变成「可见 + 自愈」，与 `cec907d` 生命周期真值层同源 |
| F5 | 只调参：把唤醒周期改大 / 让用户手动「继续」以重建循环 | 无 | 无 | —— | —— | **无效**。循环死后周期多长都不会再唤醒；「继续」只是碰巧走到 `session.py:788` 的重建分支 |

---

## 六、推荐修复

> ⚠️ **本节已被文末的 `## 评审结论` 取代（评审于 2026-08-19）。** 评审保留了 F1 的首位，但把
> 「推荐修复」收敛为**单一的 F1**，并把 F2 / F3 重新定位为**从属项**；其中 **F2 的落地写法被判定为
> 阻断级错误**（按本节字面落地会在实盘写路径上制造重复下单风险，见 §8.2），**F3 的范围被扩大**
> （§8.3），**F4 的读数设计被修正**（§8.4）。实现节点请以 `## 评审结论` 为准。

**F1 + F2 + F3 同轮落地（按此顺序），F4 紧随其后。**

理由：

1. **F1 是唯一能保证「不再复发」的那一层。** 本次是 `anyio.ClosedResourceError`，下一次可能是
   DB 写、推送队列、某个新加的调度器抛出的别的东西 —— `_monitor_tick` 现在有十余个 await 步骤，
   而它们全都在一个「任何异常即永久停机」的循环里。仓库自己在**决策轮**上早就用对了这个范式
   （`session.py:1252-1263`：`except CancelledError: raise` / `except Exception: 记录并继续` /
   `finally: 复位`），盯市循环是唯一的例外。**先把守护循环变成真正的守护循环。**
2. **F2 让故障回到既有的降级语义里。** 光有 F1，故障会变成「每 5 秒失败一次、日志有、用户
   仍不知道」；F2 一改，同一次失败会被 `_on_reconcile_failure` 计数并在达阈时推
   `LIVE_AUTO_RECONCILE_DEGRADED`（既有 UX，无需新设计）。而且这是**把仓库自己已经写下、
   却只做了一半的修法补齐**（`rest_client.py:291` 的注释 + `test_okx_rest_resilience.py:313`
   的用例注释都明确指着私有侧），成本最低、争议最小。
3. **F3 断掉放大器。** 没有它，单个会话的抖动会继续给同分组的其它会话注入非预期异常；有了 F1
   虽然不再致命，但每次抖动仍会让 N-1 个会话白丢一轮盯市。`invalidate` 的 docstring 自己说
   目的是「让并发的其它请求**下一跳**就拿到新客户端」—— 从表里摘掉就已经做到了，**立刻
   `aclose()` 是纯粹的误伤**。
4. **F4 是「诚实」的补齐。** `cec907d` 刚刚把分组生命周期改成「由事实推导而不是由动作宣称」，
   而「盯市任务是否活着」恰恰是最该进入那份事实的一条 —— 现在的 `armed=true` 在本故障下就是
   一句谎话。它工作量最大、且触前端，故排在后面，但**不做的话，同类故障下一次仍然只有用户
   自己能发现**。

**必须补的测试（缺一即视为未修完）**

- `session.py` / `live_auto/session.py`：注入一次任意 `Exception`，断言循环**存活**并继续下一跳
  （直接以 `repro.py` 的场景改写为用例：注入后墙钟唤醒仍能触发）；
- `private_client` / `trade_client`：以 `anyio.ClosedResourceError` 驱动，断言抛出的是
  `OKXPrivateError` 且换代发生（镜像 `test_okx_rest_resilience.py:313` 那条公共侧用例）；
- `client_pool`：`refcount > 0` 时 `invalidate` 不得关闭在用客户端，且新 `acquire` 拿到的是新一代；
- 回归护栏：`repro.py` 应在修复后**退出码变为 1**（"NOT REPRODUCED"），可直接当作验收脚本。

---

## 七、附：本次诊断用到的取证命令

```bash
# 1) 生产日志里的致命异常（本机 147MB，直接 grep 会很慢，用 python 逐行扫）
python -c "
import re,sys; sys.stdout.reconfigure(encoding='utf-8',errors='replace')
p='C:/Users/user/AppData/Roaming/aragon-desktop/logs/backend.log'
[print(l.rstrip()[:200]) for l in open(p,encoding='utf-8',errors='replace')
 if 'Task exception was never retrieved' in l or 'ClosedResourceError' in l]"

# 2) 生产库里这一组的唤醒周期配置（只读打开，不动生产库）
python -c "
import sqlite3,json,sys; sys.stdout.reconfigure(encoding='utf-8',errors='replace')
c=sqlite3.connect('file:C:/Users/user/AppData/Roaming/aragon-desktop/aragon.db?mode=ro',uri=True)
for r in c.execute(\"select id,inst_id,bar,config_json from strategy_runs where mode='ai-trade' order by id desc limit 5\"):
    print(r[0], r[1], r[2], json.loads(r[3]).get('wakeIntervalMinutes'))"

# 3) 最小复现
cd services/quant && python -u ../../docs/diagnoses/ai-trade-interval-wake-dead-monitor-loop/repro.py
```
```bash
# 4) 【评审补充】端到端证明：aclose 掉一个正在做 TLS 握手的共享客户端，
#    会让调用方收到一个**逃出 httpx** 的裸 anyio.ClosedResourceError（3.7 的实证）。
#    真 httpx / httpcore / anyio，无 mock；本地假代理，无外网。退出码 0 = 证明成立。
python -u - <<'PY'
import asyncio, sys, traceback
import anyio, httpx

async def fake_proxy(reader, writer):
    try:
        while True:                       # 读完 CONNECT 请求头
            line = await reader.readline()
            if not line or line in (b"\r\n", b"\n"):
                break
        writer.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
        await writer.drain()
        await asyncio.sleep(3600)         # 隧道建立后装死：TLS ClientHello 永不被回应
    except Exception:
        pass

async def main():
    server = await asyncio.start_server(fake_proxy, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    client = httpx.AsyncClient(proxy=f"http://127.0.0.1:{port}",
                               timeout=httpx.Timeout(30.0, connect=30.0))

    async def do_get():
        try:
            await client.get("https://www.okx.com/api/v5/account/positions")
        except BaseException as exc:
            return exc

    task = asyncio.create_task(do_get())
    await asyncio.sleep(0.8)              # 让它走到 start_tls 的 do_handshake
    await client.aclose()                 # <- client_pool.invalidate() 干的就是这一下
    exc = await task
    server.close()

    print("exception type              :", type(exc).__module__ + "." + type(exc).__name__)
    print("is httpx.TransportError     :", isinstance(exc, httpx.TransportError))
    print("is httpx.HTTPError          :", isinstance(exc, httpx.HTTPError))
    print("is anyio.ClosedResourceError:", isinstance(exc, anyio.ClosedResourceError))
    for f in traceback.extract_tb(exc.__traceback__)[-5:]:
        print(f"  {f.filename.split('site-packages')[-1]}:{f.lineno} in {f.name}")
    return 0 if (isinstance(exc, anyio.ClosedResourceError)
                 and not isinstance(exc, httpx.TransportError)) else 1

sys.exit(asyncio.run(main()))
PY

# 5) 【评审补充】异常出现的时间自然实验（3.8）：按日统计裸终结行，并核对全日志的任务死讯条数。
#    147MB / 130 万行，单次扫描约 40 秒。
python -u -c "
import re, sys, collections
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
p = 'C:/Users/user/AppData/Roaming/aragon-desktop/logs/backend.log'
term, cur, deaths, n = collections.Counter(), None, 0, 0
for line in open(p, encoding='utf-8', errors='replace'):
    n += 1
    m = re.match(r'^(\d{4}-\d{2}-\d{2}) ', line)
    if m: cur = m.group(1)
    if line.strip() == 'anyio.ClosedResourceError': term[cur] += 1
    if 'Task exception was never retrieved' in line: deaths += 1
print('lines', n, '| by date', dict(sorted(term.items())), '| total', sum(term.values()))
print('Task exception was never retrieved:', deaths)"

# 6) 【评审补充】健康基线的轮间隔（3.9）：从 decisionsDigest 的逐轮 ts 还原墙钟节奏。
python -u -c "
import sqlite3, json, sys, datetime as dt
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
c = sqlite3.connect('file:C:/Users/user/AppData/Roaming/aragon-desktop/aragon.db?mode=ro', uri=True)
for rid in (1605, 1606, 1607, 1608, 1609):
    inst, mj = c.execute('select inst_id, metrics_json from strategy_runs where id=?', (rid,)).fetchone()
    rows = sorted(((x['round'], x['ts']) for x in json.loads(mj)['decisionsDigest'] if x.get('ts')),
                  key=lambda r: r[1])
    gaps = [(b[1] - a[1]) for a, b in zip(rows, rows[1:])]
    print(rid, inst, 'last round', rows[-1][0],
          dt.datetime.fromtimestamp(rows[-1][1]).strftime('%m-%d %H:%M'),
          '| median gap', sorted(gaps)[len(gaps)//2], 's')"
```
---

## 八、【评审】候选修复的边界审计

### 8.1 F1（守护化盯市循环）—— 边界全部通过，但有两条必须写进实现约束

已逐条核对，**未发现新增回归面**：

| 边界 | 结论 |
|---|---|
| `_exec_lock` 泄漏 | **安全**。`_monitor_tick:914` 是 `async with self._exec_lock`，异常路径由上下文管理器正常释放。 |
| `_deciding` 状态损坏 | **安全**。`_deciding` 只在 `_maybe_interval_wake:988` 认领槽位时被置真，而 tick 死于 `reconcile:915` 时它根本没被触碰。 |
| `CancelledError` 语义 | **不变**。它是 `BaseException`，`except Exception` 天然不接；外层既有的 `except asyncio.CancelledError: pass`（:907）逐字保留。仍建议显式写 `except CancelledError: raise` 以对齐决策轮（:1252）的既有范式。 |
| `live_auto` 同形改动 | **安全**，且**更该改**：`live_auto/session.py` 的 `_monitor_task` **只在 `start()`（:167）创建过一次**，全仓**没有任何** resume 重建分支（对比 `ai_auto/session.py:788-789`）。它一旦死亡，除非整个会话重启否则永不复活。 |

**必须写进实现约束的两条（否则 F1 是「看起来修好了」）**：

- **(a) 连续失败计数与达阈可见不是锦上添花，是 F1 正确性的一部分。**
  tick 级守卫意味着**一步失败即跳过该 tick 余下全部步骤**。`reconcile` 在 tick 的**第 1 步**
  （:915），而 `_maybe_interval_wake` 在**最后一步**（:952）。若失败持续存在，循环虽然活着，
  `_maybe_interval_wake` 依然**永不执行**——症状与修复前**完全一样**，只是这次连 traceback 都没有了。
  所以「连续失败计数 + 达阈推一条降级 error」是 F1 的组成部分，不是可选装饰。
- **(b) F1 只挡异常，挡不住挂起。** 见 3.10 行 F。这条欠账由 F4 承接，且 F4 的读数必须改设计（见 8.4）。

**验收可用性核对**：`repro.py` 的故障注入是**一次性**的（`_Fault.armed and not _Fault.fired`，:106-107），
忠实于生产里的瞬时抖动。因此 F1 单独落地即可让它翻绿：第 [2] 步 `dead=False` 与第 [3] 步
「interval wake still fires」会各产生一条 `NOT REPRODUCED`，退出码变 1。**它是一个有效且充分的验收门禁。**

### 8.2 F2 —— 【阻断级】原文写法会造成一个比原缺陷更严重的回归

原文 F2 写的是「把 `except httpx.TransportError` 改为 `except Exception` + `classify_exception`」。
**私有侧不能这么改**，因为私有侧的 `try` 块结构与公共侧**不同**：

```
# services/quant/app/okx/private_client.py:212-226
try:
    resp = await client.get(path, params=params, headers=headers)      # :213
    if resp.status_code == 429: ...; continue
    resp.raise_for_status()                                            # :218
    body = resp.json()                                                 # :219
    if str(body.get("code")) != "0":                                   # :220
        await self._maybe_heal_clock(str(body.get("code")))            # :221
        raise OKXPrivateError(str(body.get("code")), ...)              # :222  <- 在 try 里面
    ...
except httpx.TransportError as exc:                                    # :226
```

`OKXPrivateError` 是 `OKXError` 的子类，而 `OKXError(Exception)`（`rest_client.py:46`）。裸放宽谓词会：

1. 把**鉴权 / 业务错误**（`50113` 签名错、`50102` 时钟偏差、余额 / 权限类码）当成传输失败
   **重试 3 次**，再归一成 `OKX_UNREACHABLE` —— 直接推翻 `_get` docstring 白纸黑字的承诺
   「`code != "0"` … 抛 `OKXPrivateError`」「鉴权/业务错误立即上抛」；
2. 让 `_maybe_heal_clock` 的时钟自愈与 `trading._map_trade_code` 的整套语义码**全部失效**
   （所有业务码坍缩成一个 `OKX_UNREACHABLE`）；
3. 在 `trade_client._post` 上更危险：`resp.json()`（:61）同样在 try 里，一个畸形响应会被当成
   传输失败，而 `place_order` 的 `retryable` 为真 ⇒ **重复下单**。这正是 `_post` docstring 里
   标为「R-1，本轮最严重的风险」的那件事。

**正确修法是镜像公共侧的「结构」，而不是它的「关键字」**：`rest_client._attempt`（:287-293）的 `try`
里**只有 `await client.get(...)` 一句**，状态码判定与响应体解析全在 try **之外**——它的注释
（:288「这个 except 的唯一来源就是上面那句 `client.get`，所以它在语义上**就等于**传输层失败」）
说的正是这件事。私有侧必须同样先把 try 收窄到只包住那一次网络调用，再放宽谓词。

**两条附带核实（对 F2 有利）**：

- `classify_exception` 的 `_RETRYABLE_EXCEPTIONS`（`retry.py:29-33`）是一张 **httpx 类型白名单**，
  不含 `ClosedResourceError` ⇒ 判为 `terminal` ⇒ **不重试，直接 `OKX_UNREACHABLE`**。这对私有侧
  是正确且期望的行为（当前代码反而会对它重试 3 次）。
- F2 恢复的降级 UX 是真实存在的：`executor._on_reconcile_failure`（:665-677）每次失败推一条
  status 文案，连续 `live_auto_reconcile_max_failures = 6` 次（`config.py:122`，即 6 × 5s = **30 秒**）
  推一条 `LIVE_AUTO_RECONCILE_DEGRADED`。注意它签名是 `exc: OKXError` 且读 `exc.code`（:676）——
  **非 `OKXError` 传进去会当场 `AttributeError`**，这也从反面说明「归一」必须在客户端层完成。

### 8.3 F3 —— 方向正确，但原文漏了同源的第二个缺陷

由 3.7 的机制结论，F3 的定性要从「放大器」升级为「**触发器**」：`invalidate` 的即时 `aclose` 是本类
异常的**必要条件**。原文对 `invalidate` 的判断（「从表里摘掉就已经做到了承诺，立刻 `aclose()` 是纯粹的
误伤」）**成立**。

**但原文漏了一条同源缺陷，不一并修会让 F3 建立在一个不准的计数上**：

`_Entry` 有 `generation` 字段（`client_pool.py:51`），**而 `release()` 从不校验它**（:84-93）。
`invalidate` 之后，其它实例手里那些针对**旧世代**的 `_pool_holds`，会在
`OKXPrivateClient.aclose()`（`private_client.py:126-131`：`for _ in range(holds): await pool.release(fp)`）
里被拿去减**新世代**条目的 refcount。`_invalidate_pooled`（:134-140）只把**自己**的
`_pool_holds` 清零，其它并发实例无从知晓。后果：

- 默认 `okx_link_pool_idle_ttl_s = 120.0`（`config.py:70`）下，只是让 `sweep` 的判据失真；
- 一旦配成 `0`（代码明确支持的「用完即关」退路），`release` 会在 refcount 被打到 0 时
  **当场 `_safe_aclose` 掉一个别人还在用的客户端**（:90-93）—— 那就是本缺陷的
  **第二条独立触发路径**，且与 `invalidate` 无关。

**⇒ F3 的范围必须包含 `release` 的世代校验**，否则「按 refcount 延迟关闭」的新逻辑本身就跑在错的计数上。

### 8.4 F4 —— 设计要改：读数应当是「心跳年龄」，不是 `_monitor_task.done()`

原文提议给 `MemberReadings` 加 `monitor_alive`，判据取 `_monitor_task.done()`。
**这只能发现「死」，发现不了「挂」**（3.10 行 F）：一个卡在某个 await 上的任务 `done()` 恒为 `False`，
而它的症状与本次故障**完全一致**。

**建议改为**：在 `_monitor_tick` 的**末尾**打一个内存心跳 `_last_tick_wall`，
`MemberReadings` 增 `tick_age_s`，`classify_member` 的判据是 `tick_age_s > k × poll_interval_s`。
它**同时覆盖死亡与挂起**，且成本更低。

现成的落点已经有了、只差一个读数：`_beat_state_row`（`session.py:474-485`）已经在 tick 上打心跳，
但它节流到 60 秒且**只写 DB**（`last_seen_ts`），没有进 `lifecycle` 判据。两者不冲突，各司其职。

### 8.5 F5 —— 同意原文判断：无效

循环死后周期多长都不会再唤醒；用户点「继续」只是**碰巧**走到 `session.py:788-789` 的重建分支
（已核实：`_monitor_task` 全仓只有 `start():509` 与 `resume():789` 两处创建）。

---

## 评审结论

### 最终推荐修复：**F1 —— 守护化盯市循环**（`ai_auto/session.py:898-908` + `live_auto/session.py:231-245`）

```
async def _monitor_loop(self) -> None:
    try:
        while self.is_active:
            await asyncio.sleep(self.poll_interval_s)
            if not self.is_active:
                break
            try:
                await self._monitor_tick()
            except asyncio.CancelledError:
                raise                       # 取消照旧上抛，由外层 except 收口
            except Exception as exc:        # noqa: BLE001 —— 守护循环：任何一跳失败都不得终结它
                self._note_tick_failure(exc)   # 记日志 + 连续失败计数 + 达阈推一条降级 error
    except asyncio.CancelledError:
        pass
```

**为什么是它，而不是 F2 / F3（尽管 F2 才是「契约破坏点」）**

1. **它是唯一一个正确性不依赖于「异常分类判断对不对」的候选。** F2 修的是「**这一个**异常没被归一」，
   F3 修的是「**这一个**异常不该被造出来」。两者都建立在本报告的机制分析成立之上。F1 修的是
   「**任何一个**异常都能永久终结一条守着真实资金的守护循环」——即便 3.6–3.7 的机制分析有任何一处
   是错的，F1 依然修好这个 bug。评审对一份诊断能给出的最高信任度，也到不了「押上全部修复」的程度。
2. **它是能解决所报缺陷的最小改动，且已有确定性验收门禁。** 各 ~8 行 × 2 个文件；`repro.py`
   在 F1 单独落地后即翻绿（8.1 已核实其一次性注入的忠实性）。
3. **回归面已逐条审干净**（8.1 表）：无锁泄漏、无状态损坏、`CancelledError` 语义逐字不变。
4. **仓库自己在决策轮上早就用对了这个范式**（`session.py:1252-1263`：`except CancelledError: raise` /
   `except Exception: 记录并继续` / `finally: 复位`）。盯市循环是仓库里**唯一**一条又长命、又守真钱、
   又没有顶层守卫的循环。这不是新发明，是**把已有纪律补齐到唯一的例外身上**。
5. **反过来说，F2 不适合当主修法**：它是四个候选里**改动风险最高**的一个——8.2 证明按原文写法
   落地会在实盘写路径上制造重复下单风险，正确落地需要重构 `try` 块的边界（而不是换个关键字）；
   而且即使做得完美，`_monitor_tick` 剩下的十余个 await 里再抛出**别的**非 `OKXError`，循环照死不误。
   **F2 治的是这一次的病因，F1 治的是这一类病的致命性。**

**结论：只有 F1 是「必须落地才算修完」的那一条，故推荐修复唯一指向 F1。**

### 同轮必须一并落地的从属项（非可选，但从属于 F1）

| 序 | 项 | 为什么不能拖到下一轮 |
|---|---|---|
| 1 | **F2（按 8.2 的结构化修法）**：把 `private_client._get` / `trade_client._post` 的 `try` 收窄到**只包住那一次网络调用**，再放宽谓词并归一为 `OKXPrivateError` | 只有 F1 的话，故障变成「每 5 秒静默失败一次、用户仍不知道」——而**「静默」正是这个缺陷能活这么久的原因**。F2 一到位，同一次失败即回到既有降级链路（30 秒内推 `LIVE_AUTO_RECONCILE_DEGRADED`），零新增 UX 设计。**严禁按原文的「改成 `except Exception`」落地。** |
| 2 | **F3（含 8.3 的 `release` 世代校验）**：`invalidate` 先摘表、`aclose` 推迟到 `refcount == 0` 或宽限期后；`release` 必须校验世代 | 它是**触发器**而非放大器（3.7）。不修，同分组其它成员每次抖动仍会白丢一轮盯市；且 `release` 的世代盲区是**第二条独立触发路径**。 |
| 3 | **F4（按 8.4 改为 `tick_age_s` 心跳读数）** | 可以排到下一轮，但**必须立项**：F1 挡不住「挂起」，而挂起的症状与本次一模一样。`monitor_alive = not task.done()` 的原设计发现不了它。 |

### 验收口径（缺一即视为未修完）

1. `repro.py` 退出码由 **0 → 1**（NOT REPRODUCED），直接作为回归护栏；
2. `ai_auto/session.py` 与 `live_auto/session.py` 各一条用例：向 tick 注入一次任意 `Exception`，
   断言**循环存活**且**下一跳的墙钟唤醒仍能触发**（不是只断言「没抛出」）；
3. `private_client` / `trade_client`：以 `anyio.ClosedResourceError` 驱动，断言抛出的是
   `OKXPrivateError` 且换代发生（镜像 `test_okx_rest_resilience.py:313` 那条公共侧用例）；
   **另加一条反向用例**：`code != "0"` 的业务错误**必须一次即上抛、不得重试**（守住 8.2 的回归面）；
4. `client_pool`：`refcount > 0` 时 `invalidate` 不得关闭在用客户端；`invalidate` 之后旧世代的
   `release` 不得减到新世代的计数上；
5. 生产对账：`/account/positions` 的 5 秒心跳（健康时段实测 5 成员 × 12 次/分钟 ≈ 55 次/分钟，
   与 `live_auto_poll_interval_s = 5` 逐字吻合）在一次代理抖动 / 休眠唤醒后**必须自行恢复**。

### 评审对原报告的整体判断

**根因结论成立，证据链在补入 3.6–3.10 后闭合。** 原报告的四层漏斗、宿主任务差异、生命周期真值层
读不到盯市存活这三条核心论断，全部经代码逐行复核为真（行号与生产 traceback 逐帧吻合）。
评审补入的是三样原报告缺的东西：**upstream 的精确缺口**（3.6）、**「本进程自己关的」这一步反推**
（3.7，把 F3 从放大器升级为触发器）、以及**竞争假设的显式排除**（3.10，尤其是最像的 hold-inertia
跳过守卫）。同时纠正了 F2 的落地写法（8.2，阻断级）、补齐了 F3 的范围（8.3）、修正了 F4 的读数
设计（8.4），并按「证据到哪里、话说到哪里」的原则给 3.9 加了诚实边界。

---

## 实施过程发现的方案缺陷

实施于 2026-08-19，落地范围 = 评审结论指定的 **F1（主修法）+ F2（按 §8.2 的结构化写法）+ F3
（含 §8.3 的 `release` 世代校验）**。F1 / F2 的方案按原文落地即成立，**未发现缺陷**。F3 的方案
方向成立，但 §8.3 有一处**欠指定**，照字面落地会得到一个自相矛盾的实现，记录如下。

### D-1（F3 / §8.3）：「`release` 校验世代」在当前 API 下无法落地，且它与 §3.7 引用的既有注释直接冲突

§8.3 只写了「`release` 必须校验世代」，但 `release(fp)` 的签名**拿不到世代**：调用方
`OKXPrivateClient` 把持有的引用记在一个**无世代的整数** `_pool_holds` 里（`private_client.py:82`），
`acquire()` 也只回一个客户端。所以这条要求不是「加一个 if」，它必须先有一条把世代交给调用方的路。
落地采用的最小改法：

- `OKXClientPool` 新增 `acquire_hold() -> (client, generation)`，`acquire()` 降为它的薄封装
  （池的其余 3 处调用点与全仓 17 处私有客户端构造点**一行未改**）；
- `OKXPrivateClient._pool_holds` 由 `int` 改为 `dict[世代 -> 份数]`，`aclose()` 逐世代归还。

**更要紧的一条**：`_invalidate_pooled()` 里那句 `self._pool_holds = 0`（连同它的注释「手里那几份
引用随被摘掉的那一代一起作废，不能拿它们去减新一代的计数」）在 F3 之后**变成了错的**，必须删除。
理由是 F3 改变了退役条目的关闭判据：

- F3 之前：`invalidate` 当场 `aclose()`，旧世代的引用确实已无意义，清零是当时唯一能防止「减到新
  一代头上」的手段；
- F3 之后：退役条目要**等自己的引用归零**才关。此时丢弃那几份引用 = 退役条目的 refcount 永远回不到
  0 ⇒ 那条连接一路挂到宽限期兜底才被关掉。而「不能减到新一代头上」这件事，已经由 `release` 的世代
  校验**在池内**保证了，不再需要调用方靠清零去近似。

**⇒ §8.3 若只按字面加校验、不动 `_invalidate_pooled`，会得到「校验有了、但每次换代都泄漏一条连接
直到宽限期」的实现。** 已按上述改法落地，并补了 `test_a_retired_client_closes_once_its_last_in_flight
_hold_comes_back` 钉住这条路径。

### D-2（验收口径 #4 的连带影响）：两条既有池用例断言的正是被 F3 推翻的行为，必须改写

`tests/test_okx_client_pool.py` 有两处逐字断言「`invalidate` 之后旧客户端 `is_closed`」
（`test_invalidate_closes_and_bumps_the_generation`、
`test_the_same_private_client_instance_keeps_working_after_invalidate`）。它们断的正是 §3.7 认定为
**触发器**的那个动作，与验收口径 #4「`refcount > 0` 时 `invalidate` 不得关闭在用客户端」直接对立。
已按新契约改写（并保留「没人在用时当场关闭」的旧行为为独立一条），而不是放松断言：

- 无人在用 ⇒ 当场关闭、置代（行为逐字不变）；
- 有人在用 ⇒ 不关、只摘表置代，下一跳立刻拿到新一代；最后一份旧世代引用还回来时才关。

### 落地与验收记录

| 验收口径 | 结果 |
|---|---|
| 1. `repro.py` 退出码 0 → 1 | ✅ 三条 `NOT REPRODUCED`，`EXIT=1` |
| 2. 两条 session 各一条「注入异常后循环存活且下一跳唤醒仍触发」用例 | ✅ 并已实测：临时撤掉守卫后两条**双双转红**（`assert 1 >= 2`），确认是真护栏 |
| 3. `private_client` / `trade_client` 的 anyio 驱动用例 + `code != "0"` 不重试的反向用例 | ✅ 另加两条写路径边界（非幂等写仍只发一次、畸形响应绝不触发第二次下单） |
| 4. `client_pool` 的两条（在用不关 / 旧世代不减新一代） | ✅ 另加宽限期兜底关闭一条 |
| 5. 生产对账（一次抖动后 5 秒心跳自行恢复） | 以离线端到端脚本代替：真 httpx/httpcore/anyio + 本地假代理，把请求停在 `start_tls` 再并发 `invalidate`，实测在飞请求**不再**收到 `ClosedResourceError`，下一跳即拿新连接，最后一份引用还回后退役连接被关 |

全量回归：`3802 passed, 1 skipped, 7 failed`。7 条红全部落在**未跟踪的在制文件**
`tests/test_factor_library_expansion.py`（它断言因子库有 20 个因子，实际 13 个——对应实现尚未编写），
与本轮改动无关，修复前后逐条一致。

---

## 终审（合入前复核）

复核口径 = 「改的是不是根因 / 会不会在相邻路径上造成回归 / 命名与注释是否与仓库一致」。
**F1 / F2 / F3 三条主线判定为成立**：守护化是唯一一条正确性不依赖机制分析成立与否的修法；F2 严格
按 §8.2 收窄了 `try` 而不是只换关键字（`resp.json()` 与 `code != "0"` 都被挪出 `try`，写路径的
重复下单风险已由 `test_a_malformed_response_body_is_never_treated_as_a_transport_failure` 钉死）；
F3 的退役表 + 世代校验与 D-1 记录的 `acquire_hold` 改法自洽。以下 5 处在合入前直接改掉。

### R-1（阻断级 / UX）：`live_auto` 的降级在用户那里**一个字都看不到**

`_note_tick_failure` 只推 `error` 帧，而自动交易分组的成员流
（`apps/web/lib/useLiveAutoGroupStream.ts` 的 `routeMemberEvent`）**显式丢弃 `error`**
（源码注释：「error 由 status.message 渠道呈现」）。于是 §8.1 (a) 认定为「F1 组成部分」的那条
可见性，在 live-auto 侧落空——**这正是本缺陷能活这么久的那种静默**。
`executor._on_reconcile_failure` 之所以每次都同时推一条 status 文案，原因就在这里。

**改法**：达阈时 `error` 与 status 文案**成对**推送，并把 `_tick_degraded` 并进
`_status_payload()` / `info()` 的 `degraded`（与 `_stale_degraded` 同款口径），使徽标与断线重连后的
snapshot 也说实话。ai-trade 侧无此问题：它的 `case "error"` 会把文案落到
`member.session.message`，恢复时的 `status.message` 覆盖同一个键，故不留过期文案。

### R-2（数据正确性）：两条恢复通知推的是**窄载荷**

`{"state": ..., "message": ...}` 缺了全量断面。前端两侧的合并语义都不容它：

- live-auto 的成员 status 是**整体替换**（`store.setLiveAutoMemberStatus`）⇒ 该成员的
  `sessionPnlUsd` / `positionSide` / `budgetUsd` 当场丢失，`WatchlistTab` 的分组合计盈亏
  **少算这一笔**，直到下一次全量 status 才恢复（而全量 status 只在换向 / 停滞 / 停止时才发）；
- ai-trade 是浅合并，但 `patchAiTradeMemberStatus` 会把 `profitGuardCooldown` 顺手清成 `null`。

**改法**：改用 `{**self._status_payload(), "message": ...}` —— 两个文件里其余**每一处**带文案的
status 推送本来就是这个形状（行情停滞 / 已恢复 / 利润保护）。

### R-3（守卫本身留了一条缝）：`else` 分支里的异常**不被那个 `try` 接住**

「一跳成功即清零并出声」原本写在 `try ... else:` 里。`else` 子句抛出的异常不经本 `try` 的
`except` —— R-2 之后这条通知要读整份 `_status_payload()`，那不是一句不会抛的字面量，于是
**守护循环仍存在一条能被打死的路径，且症状与本缺陷逐字相同**。

**改法**：把 `_note_tick_recovered()` 移进 `try` 体内；失败侧的 `_note_tick_failure()` 反过来只碰
计数、日志与字面量事件（live-auto 侧多读一个纯属性断面），故它在守卫之外也抛不出来。新增
`test_a_throwing_recovery_notice_never_kills_the_loop_either`，实测把实现改回 `else` 分支即转红。

### R-4（兜底判据）：`sweep` 的宽限期用真值判空

`now - (entry.retired_at or now) > grace`：`retired_at == 0.0` 会被当成「没打过时间戳」，那条退役
连接**永远清扫不到**。改为显式 `is not None`（`invalidate` 入表前必然打戳，判 `None` 才是本意）。

### R-5（用例收尾）：只 `cancel` 不 `await`

两条新用例取消了盯市任务却不等它真正停下，于是循环在事件循环关闭之后还在动 DB（tick 里的心跳
写），多文件同跑时在**别的模块**里冒出 `Event loop is closed`（本仓已因同一根因红过一次）。
改为 `cancel()` + `await`（`suppress(CancelledError)`）。连带纠正：helper 必须自己交出「收尾前是否
存活」，因为 cancel 之后再问 `task.done()` 恒为 `True` —— 原用例那句存活断言是**靠调度巧合**才为
真的，一旦收尾变严谨它立刻变成一句空话。

### 终审验收

| 项 | 结果 |
|---|---|
| `repro.py` | ✅ 三条 `NOT REPRODUCED`，`EXIT=1` |
| 定向用例（5 个文件） | ✅ 89 passed，0 warning |
| 守卫真伪（把 R-3 的修法改回 `else`） | ✅ 新用例转红，改回即绿 |
| 全量回归 | ✅ `3803 passed, 1 skipped, 7 failed`（+1 = R-3 的新用例）。7 条红仍全部落在未跟踪的在制文件 `tests/test_factor_library_expansion.py`，与本轮无关且与修复前逐条一致；残留的一条 `Event loop is closed` 告警归属 `test_discovery_health_runtime.py::test_guard_shutdown_is_cancel_safe`，不在本轮触及的文件里 |
| ruff / 导入 | ✅ `ruff check app tests` 全绿；`app.main` 正常导入（161 路由） |
