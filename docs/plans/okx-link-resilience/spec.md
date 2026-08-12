# OKX 链路韧性加固（断线自愈 · 调用可靠 · 连接可见）

> **状态**：**v2**（已评审 · 有条件通过；P0/P1 已在正文内闭合，见文首「评审记录」节）
> **Feature slug**：`okx-link-resilience`
> **要解决的用户诉求**：「对接欧易的接口有时会断线；希望断线后自动重连更稳健，相关调用更稳健可靠」。
> **范围纪律**：**只加法**（不改任何既有安全门禁的判定式）、**零 DB 迁移**、**每个新机制都有一个
> 关得掉的取值**（不新增分叉代码路径）、**不动策略/回测/AI 决策语义**、**不新增下单路径**。

---

## 评审记录（v1 → v2）

评审口径：可行性（本仓库现有技术栈能不能真建出来）、完备性（边界 / 错误路径 / 上线路径有没有洞）、
一致性（与 `CLAUDE.md` 与仓库既有约定冲不冲突）、规模适配（过度设计 / 欠定义）。
**每一条都按行核实到具体文件与行号**，未经核实的怀疑不写进来。

### 0.1 P0（会造成线上故障或直接打红既有用例，必须改）

| # | 结论 | 现场 | 修法（已落正文） |
|---|---|---|---|
| **P0-1** | **`proxy=` 能力探测写反了方向，会把「有时断线」变成「彻底连不上」。** `websockets` 15 的 `proxy` **默认值是 `True`**（= 采用系统代理配置），**传 `None` 是「显式禁用代理」**（已在本机 15.0.1 实测：`inspect.signature(websockets.connect).parameters['proxy'].default is True`）。而 `settings.proxy_url` 在**只配了 Windows 系统代理**（本仓库开发/用户环境即 Clash Verge 系统代理）时**恒为 `None`**——因为它只读 `HTTPS_PROXY/HTTP_PROXY/ALL_PROXY` 三个 env（`config.py:684-686`）。今天 WS 能连上，正是因为 `websockets` 默认走 `urllib.request.getproxies()`，**在 Windows 上会读注册表里的系统代理**；一旦按 v1 的写法无条件传 `proxy=settings.proxy_url`，这批用户的 WS **全部立刻断连且永不恢复**。F-26「WS 侧当前完全没用它」只在「没用那个 *设置项*」的意义上成立，**不等于 WS 当前没有代理**。 | `okx/ws_client.py:99`、`config.py:684-686`、websockets 15.0.1 签名 | §2.2.6-4 改为**只在 `proxy_url` 为真值时才传 `proxy=`**，为空一律**不传该键**（保留库默认的系统代理发现）；新增 §7.1 `test_okx_ws_connect_kwargs.py` 断言「`proxy_url` 为空 ⇒ kwargs 里没有 `proxy` 键」 |
| **P0-2** | **全局建连令牌桶会打红 §7.3 声明「必须原样绿」的 `test_okx_ws_endpoints.py`。** 桶是进程单例，而 `pytest` 同进程连跑三个用例：`test_start_opens_business_and_public_loops` 先耗掉 2 个令牌（burst=2），紧接着的 `test_start_opens_only_business_loop_when_market_disabled` 就得等 0.5s 才拿得到令牌——但该用例的 `_wait_for` 只是 `asyncio.sleep(0)` 空转 200 次（`tests/test_okx_ws_endpoints.py:84-90`），**墙钟根本推不到 0.5s**，于是 `recorder.subscriptions` 为空，`assert list(...) == [_BUSINESS_URL]` 直接红。 | `tests/test_okx_ws_endpoints.py:84-90/115-128`、§2.2.3 | §2.2.3 改为**建连闸只作用于「重连」（attempt ≥ 1），首次建连不取令牌**（语义上也更对：首建的并发度已由 `live_auto_start_concurrency` 约束，需要压制的是重连风暴）；并在 §7.1 要求 `tests/conftest.py` 增一个 **autouse 单例重置夹具** |
| **P0-3** | **登记簿只增不删 → 顶栏芯片会在正常使用几分钟后永久显示「连接不稳 / 不可达」。** 通道键含 `instId:bar`（§4.1），而用户在图表上**每换一次标的或周期**就会拆掉旧的浏览器 WS、建一条新的（`api/ws_market.py:62-87` 每个浏览器连接新建一个 `OKXMarketWSClient`）。旧键留在登记簿里，`last_ok_ts` 越来越旧，`derive_state` 判它 `degraded/down`，而 `overall` 取「最差的那个」（§2.5.1）——**换三次标的，芯片就再也回不到「已连接」**。LRU 上限 32 只在超过 32 个键后才淘汰，救不了这条。 | §2.5.1、§4.1、`api/ws_market.py:62-87`、`components/Chart/ChartToolbar.tsx:177-206` | §2.5.1 增 `close_channel(key)`（supervisor 收到 `stop` 即调用，标 `closed` 并从 `overall` 与面板列表中剔除）；`overall` **只对活跃通道求最差**；通道键增 `owner` 段避免多方共写同一键 |
| **P0-4** | **ai_auto 的补根口径含糊，最坏解读会让利润保护层拿 10 分钟前的价当「刚刚的价」。** §2.2.4 表里写「不刷新 `_last_candle_wall` **之外的**唤醒锁存」——最自然的读法是「`_last_candle_wall` 可以刷」。但 `feed_candle` 的确认根分支是 `self._last_candle_wall = self._last_px_wall = time.time()`（`ai_auto/session.py:653`），而 `_last_px_wall` 是**利润保护层新鲜度豁免的唯一真源**（`:235` 注释、`:818` 传入 `PreviewRequest`、`:1236` 传入保护层）。补根若照抄这一行，一次 10 分钟断线之后保护层会认为判定价是「0 秒前的」，据此执行棘轮 / 兑现——这正是上一轮 `ai-trade-profit-protection` D-1 修过的同一形状的坑。 | `ai_auto/session.py:230-235/612-616/643/653/818/1236` | §2.2.4 表改为**显式禁写清单**：补根**一律不写** `_last_candle_wall` / `_last_px_wall` / `_live_row` / `_last_live_px`，并说明理由（这四个字段的语义是「实时证据的年龄」，补根是历史证据） |
| **P0-5** | **熔断器会把一次瞬时公共读故障放大成「图表实时流被拆掉最长 120 秒」——与本轮目标正相反。** `/api/health` 的 `okx` 键来自 `rest.health()`（`api/market.py:31-33`），前端据此算 `okxUp`（`app/page.tsx:161`），而 `shouldStreamMarket(mode, okxUp)` 在 `okxUp === false` 时**返回 false**（`lib/chartLive.ts:45-47`），页面据此**卸载浏览器行情 WS**（`app/page.tsx:189/416`）。熔断一旦打开，`health()` 在整个冷却期必失败 ⇒ `okx: "down"` ⇒ 行情流被拆；半开探针若被别的取数调用消费掉并失败，冷却**翻倍**至上限 120s，于是网络其实已经恢复、界面仍黑着。同一条还会打死 §2.2.4 的补根——断线与公共读故障高度同源，**补根恰好发生在熔断打开的窗口里**。 | `api/market.py:30-47`、`app/page.tsx:161/189/416`、`lib/chartLive.ts:45-47`、§2.3.3 | §2.3.3 增**豁免名单**：`health()`、`get_server_time_ms()`、`gap_repair` 的取数、`/api/okx/connectivity/probe` **一律不受熔断阻挡**（它们本身就是探针）；任一次成功即**清零失败计数并把冷却时长复位**（不再翻倍累积） |

### 0.2 P1（正确性 / 一致性 / 规模，必须改）

| # | 结论 | 现场 | 修法（已落正文） |
|---|---|---|---|
| **P1-1** | 连接池与 `OKXPrivateClient._get_client` 的**实例级缓存**冲突：`self._client` 一旦缓存，池的 `invalidate(fp)` 关掉底层 `AsyncClient` 后，仍持有旧引用的实例下一次请求会撞 `RuntimeError: Cannot send a request, as the client has been closed.`——而 `invalidate` 的触发条件正是「传输层异常」，也就是**故障期间必然发生**。 | `okx/private_client.py:66-82`、§2.4.1 | §2.4.1 明写：`_get_client` **不得记忆** `self._client`，每次从池 `acquire`（池内自己保证同 fp 复用同一实例）；`aclose()` = `release` 且**必须清空** `self._client` |
| **P1-2** | F-9 声称「公共侧照此对齐，**口径统一**」，但 v1 让公共侧抛新码 `OKX_RATE_LIMITED`，私有侧却仍抛 `OKX_HTTP_ERROR`——后者被既有用例**冻结**（`tests/test_okx_signing.py:95-110`、`test_okx_trade_signing.py:129`），而 §7.3 又要求这两个文件原样绿。结果是同一个「被限频」在 UI 上有两种码、两句中文。 | `okx/private_client.py:136-137`、`okx/trade_client.py:56-57`、`tests/test_okx_signing.py:95` | §2.3.5 增一段**显式登记这处有意的不对齐**（私有侧维持 `OKX_HTTP_ERROR` 以守等价门禁），并把它列进 §十一 欠账；F-9 的「口径统一」措辞改为「**写法**对齐、**码**暂不对齐」 |
| **P1-3** | §3.2 让 `private_client` 把 `_get`/`_post` 的重试逻辑抽成共用 `_execute`——这项重构**对五条根因零贡献**，却直接威胁两个必须原样绿的签名用例：它们 monkeypatch 的是 `app.okx.trade_client.asyncio.sleep` 与 `app.okx.private_client.asyncio.sleep`（`test_okx_trade_signing.py:118/131`、`test_okx_signing.py:84/100/120`），一旦 sleep 的调用现场搬到别的模块，替身立刻失效、用例开始真睡。 | §3.2、`tests/test_okx_trade_signing.py:118`、`tests/test_okx_signing.py:84` | §3.2 **删除 `_execute` 抽取**（规模适配：本轮不做与韧性无关的去重复）；正文补一条硬约束——`asyncio.sleep` 的调用现场必须**留在各自模块**里 |
| **P1-4** | `derive_state` 把「很久没被调用」判成 `degraded`（`rest: >90s`）——**空闲不等于坏掉**。后端在无浏览器打开时可以几分钟不发一次公共请求，届时面板会无中生有地报「连接不稳」。诚实原则是双向的：既不许把坏的说成好的，也不许把闲的说成坏的。 | §2.5.1 | §2.5.1 改判据：状态由**失败**驱动（`consecutive_failures` / 熔断 / WS 活性），「最近一次成功很旧**且期间没有失败**」判 `idle`（面板显示「空闲」，**不参与 `overall` 变差**）；只有 WS 通道保留「静默即 degraded」（它本就该每 15s 有入站帧） |
| **P1-5** | `overall` 把 `restPrivate` 一起取最差 ⇒ **没配实盘账户或密钥填错的用户，行情完全正常时顶栏也会显示「OKX 不可达」**。这会训练用户忽略这枚芯片，等真断线时没人看。 | §2.5.1、§2.5.3 | §2.5.1 拆成两个派生值：`overall`（**只由行情面**：`restPublic` + 活跃 WS 通道决定，驱动芯片文案与色）与 `accountState`（私有面，面板内单独一行 + 计入 `summary`），并明写芯片**不得**因私有面变红 |
| **P1-6** | `close-position` 的「读回核验」缺时序，会**把成功说成不确定**：平仓请求送达后交易所侧持仓归零有毫秒级到秒级延迟，立刻 `get_positions()` 很可能仍看到仓位 ⇒ 抛 `OKX_SEND_UNCERTAIN`。更危险的是**调用方契约没定义**：`live_auto/executor.py:314/673` 与 `okx/trading.py:251/483` 收到这个新码之后会不会再发一次平仓？上一轮 `ai-trade-backstop-repair` 的最重一条缺陷正是「待核实态被覆盖，人工连点两次发出两张全仓止损」。 | `okx/trade_client.py:176-197`、`live_auto/executor.py:314/673`、`okx/trading.py:251/483` | §2.4.3 增**有界重读**（间隔 `okx_link_verify_backoff_s` 默认 0.6s，至多 `okx_link_verify_attempts` 默认 3 次，总计 <2s）；并写死**调用方契约**：`OKX_SEND_UNCERTAIN` 是**终态**，任何上层（含自愈层与人工按钮）**禁止据它自动重发平仓**，只能提示人工核对 |
| **P1-7** | 补根的**失败路径完全没定义**：`fetch_missing` 抛错时，是打死 supervisor 的 pump？打死 session？还是静默留一个洞？空洞留着会让下一根真实确认根之后的指标窗口不连续，而这恰恰是最难查的一类错。 | §2.2.4 | §2.2.4 增失败语义：补根在 **session 侧的独立 task** 里跑，**绝不**回传异常给 supervisor；失败即登记 `gap_repair_failed`、推一条 status 说明**空洞未补齐**（禁止静默），并在下一次 `resync` 时按同一 `since_ts` 再试一次 |
| **P1-8** | 行数事实过期：`ai_auto/session.py` **实为 1790 行**（v1 写「~1400 行」），`live_auto/session.py` 490、`paper/session.py` 457。据错误的数字得出的「若单文件超限**首选**下沉」是软的——1790 行早已两倍于 CLAUDE.md 的 800 行红线，没有「首选」可言。 | 实测 `wc -l` | §3.2 数字更正，并把下沉 `okx/session_link.py` 从「首选做法」改为**强制做法**（三处 session 只留 ≤10 行的绑定） |
| **P1-9** | ai_auto 补根只刷 `_atr_px` 而不调 `_wake.note_atr(...)`，会把「ATR 判据与参照断面共用这一个数（**唯一入口**）」（`ai_auto/session.py:660` 注释）劈成两份，唤醒器据旧 ATR 判定。 | `ai_auto/session.py:658-660` | §2.2.4 表补 `_wake.note_atr(self._atr_px)`，与 `_atr_px` 同进同出 |
| **P1-10** | `connect_factory` 的返回值**是否 await 没定死**，而这决定既有替身还能不能用：`_ConnectRecorder.__call__` 返回的 `_FakeConnection` **不可 await**，只实现了 `__aenter__/__aexit__/send/__aiter__/__anext__`——**没有 `recv`，也没有 `close`**。 | `tests/test_okx_ws_endpoints.py:27-63` | §2.2.6 写死三条：① 工厂返回值**不得 await**，一律交给 `async with`；② `_recv(ws)` 先 `recv` 后回退 `__anext__`；③ 强制关闭走 `getattr(ws, "close", None)`，替身缺 `close` 时依赖 `async with` 退出即可 |
| **P1-11** | 本轮新增 4 个进程单例（`LinkRegistry` / `LimiterRegistry` / `OKXClientPool` / `ServerClock`），而 `tests/conftest.py` **没有任何 autouse 夹具**，测试间必然相互污染（P0-2 只是它最先炸的一个面）。 | `tests/conftest.py:1-43` | §7.1 增一条硬要求：`conftest.py` 加 autouse 夹具，逐个 `reset()` 四个单例；四个单例都必须提供 `reset()` |

### 0.3 P2（建议，不阻断放行）

| # | 结论 | 处置 |
|---|---|---|
| P2-1 | F-19 的数字不准：`okx/trading.py` 实为 **13 处** `OKXTradeClient(...)` 构造，全仓私有客户端构造点合计 **17 处**。机械判据里的数字一旦算错，判据本身就失效。 | 正文已更正 |
| P2-2 | F-11 说 `close_position`「无去重键」，但它**已有** `cl_ord_id` 形参（`trade_client.py:176-177`）。准确说法是：**键有，但 OKX 只对 `order`/`order-algo` 承诺按 `clOrdId` 去重**，`close-position` 没有这个承诺。 | 正文已更正措辞 |
| P2-3 | 补根逐根调 `_maybe_refresh_htf` 会在补 300 根时打出 300 次 REST。 | 已改为**补完之后统一刷一次** |
| P2-4 | paper 补根产生的成交与实时成交在前端不可区分（`_seeded_trade_count` 只标预热段）。 | 记为已知取舍，不改（补根成交本就是真实因果撮合的一部分） |
| P2-5 | 是否保留 `ping_interval=None` 没写。 | 正文补：**保留**（应用层三重判据已覆盖，且改动它会同时改变既有替身的行为面）；同时**允许**实现节点额外传 `ping_timeout` 作纵深防御 |
| P2-6 | wire 的 `latencyMs` 与登记簿的 `latency_ms_ewma` 命名不一致，单位/口径未写。 | 正文补一句：wire 的 `latencyMs` = EWMA(α=0.3) 后**四舍五入到整毫秒** |
| P2-7 | 池化后 `AsyncClient` 的 timeout 由**首个**创建者的 `Settings` 决定；`release` 若没配对的 `acquire` 会把引用计数打成负数。 | 正文补：指纹加入 timeout 量化档；`release` 对未持有的 fp 是 **no-op** |
| P2-8 | `/api/okx/connectivity` 10s 轮询与 `/api/health` 15s 轮询叠加，面板关着时也在跑。 | 正文补：面板收起时轮询降到 30s，展开时 5s（`refetchInterval` 取函数式） |

---

## 一、概述

### 1.1 要解决的问题

本仓库与 OKX 的连接面共四条：**公共 REST**（`okx/rest_client.py`）、**公共 WebSocket**
（`okx/ws_client.py`，business=K 线 / public=盘口成交）、**私有签名 REST 读**
（`okx/private_client.py`）、**私有签名 REST 写**（`okx/trade_client.py`）。四条链路当前各自实现了
一套「够用」的容错，但都停在**「连得上就行」**的层面，缺的是**「连不上/半死不活时能被发现、能自
愈、且不把一次网络抖动放大成一次错误交易」**。逐条按行核实后，断线与调用不可靠的根因收敛为五类：

**根因 A：WS 没有任何活性判据，半开连接永远不会触发重连。**
`ws_client.py:99` 用 `{"ping_interval": None, "open_timeout": 15}` 连接——`ping_interval=None`
**同时关掉了 websockets 库的协议级 ping 与 pong 超时**，于是整条连接的存活判定只剩下
`async for raw in ws`（`:107`）会不会抛异常。应用层虽然每 20s 发一个文本 `"ping"`
（`:130-136`），但**没有任何人检查 `"pong"` 有没有回来**：`_heartbeat` 把所有异常吞掉后
直接 `return`（`:135-136`），读循环则继续挂在一个 TCP 层「看起来还开着」的 socket 上。
代理中断、NAT 老化、笔记本休眠唤醒、机场节点切换——这些**最常见**的断线形态恰恰都是
「不 FIN 不 RST，就是不再来数据」。**这类断线现在永远不会重连**，只会表现为「K 线不动了」，
最终由会话侧看门狗（`live_auto/session.py:269-292`、`ai_auto/session.py:611-634`）在
`bar_seconds × 3` 之后推一条降级提示——1H 周期下就是 **3 小时**。

**根因 B：退避在「连上了但立刻失败」时被复位，形成热重连。**
`ws_client.py:104` 在 `send(subscribe)` 之后立刻 `backoff = 1.0`。只要 TCP/TLS 能握上手，
哪怕订阅被拒（OKX 回 `event:error`，`:145-151` 只是转发给上层并**保持连接**）或对端立刻关闭，
下一轮退避都从 1s 重来。结果是**故障期间以 ~1s 的节奏反复连接**，而 OKX 对**建连**本身有
「3 次/秒/IP」的限制——退避机制在最需要它的场景下失效，还会把自己拉黑。

**根因 C：REST 重试把「永久错误」当成「暂时错误」，又把「暂时错误」报成了错的原因。**
`rest_client.py:96-116` 的 `_request`：
1. `except (httpx.TransportError, httpx.HTTPStatusError)` 对**所有** 4xx/5xx 一视同仁重试——
   一个 400（参数不合法）会白白空转 3 次、等待 ~2.8s，最后报 `OKX_UNREACHABLE`，把「你传错了」
   说成「连不上交易所」；
2. 429 分支（`:104-106`）`continue` 后若**最后一次仍是 429**，循环自然结束，落到 `:116`
   抛 `OKX_UNREACHABLE: 无法连接 OKX (path): None`——**`last_exc` 是 `None`**，用户看到一句
   带 `None` 的串，且丢失了「其实是被限频」这个唯一有用的信息。私有客户端**已经**修过同一个
   坑（`private_client.py:113-138` 的 `rate_limited` 标志），**公共客户端至今没修**。
3. 退避是 `0.4 × 2^n` 的**纯确定性**指数（`:115`），没有抖动：多标的分组同时失败时，所有
   会话在同一毫秒一起重试，制造自我同步的请求尖峰（`docs/diagnoses/live-auto-multi-symbol-rate-limit-429`
   记录的正是这个形状的事故）。

**根因 D：每一次私有调用都新建一条 TLS 连接。**
`okx/trading.py` 有 **13 处** `OKXTradeClient(...)` 构造（`:160/244/318/386/432/464/509/522/536/578/635/679/806`），
连同 `ai_auto/exchange_backstop.py:80`、`accounts.py:79/103` 等，全仓私有客户端构造点合计 **17 处**
（[评审 P2-1]：v1 写「14 处」是数错了——机械判据里的数字一旦算错，判据本身就失效）。每个实例在 `_get_client`
（`private_client.py:68-77`）里**新建一个 `httpx.AsyncClient`**，用完 `aclose()`。也就是说
AI 自动交易那条 **5 秒一次**的盯市循环，每次 reconcile 都要做一次完整的 TCP+TLS 握手。
后果有三：握手时延占了请求耗时的大头；代理链路上握手是最容易被打断的一段（
`docs/diagnoses/desktop-paper-batch-start-socket-reset` 记录的 `ECONNRESET / socket hang up`
就发生在这类瞬时连接上）；连接数尖峰在多标的场景下叠乘（8 标的 × 每轮 2~3 个请求）。

**根因 E：本机时钟偏差没有任何补偿，且「不确定的写」被当成「失败的写」。**
OKX 对签名请求的 `OK-ACCESS-TIMESTAMP` 有 ±30s 的容忍，超出即 `50102`。`_iso_timestamp`
（`private_client.py:36-39`）**直接取本机 UTC**，本机时钟漂移（尤其 Windows 笔记本休眠唤醒后）
会让**全部**私有请求同时失败，且归一码 `OKX_TIME_SKEW` 之后**没有任何自愈动作**。
另一头，`trade_client._post`（`:39-58`）对 `TransportError` **一律重试 3 次**——对
`place_order`/`place_algo_order` 是安全的（`clOrdId`/`algoClOrdId` 由 OKX 去重，`:32-33` 已明写），
但 `close-position`（`:176-197`）**没有任何去重键语义保证**——注意它**已有** `cl_ord_id` 形参
（`:176-177`），但 OKX 只对 `order` / `order-algo` 承诺按 `clOrdId` 去重，`close-position` 没有这个
承诺（[评审 P2-2]：「有键」不等于「有去重语义」，别被形参骗了）。于是一次「请求已送达、响应在回程丢了」
的超时，重试可能变成第二次平仓动作。而反过来，真正正确的处置——**读回交易所状态确认**——
现在一次也没有做。

### 1.2 要建的东西

四条轴 + 一屏，全部为**加法**：

| 轴 | 目标 | 落点 |
|---|---|---|
| **A · WS 断线自愈** | 半开连接在 ~20s 内被发现并重连；退避带抖动且只在**真的健康**之后复位；建连有全局速率闸；重连后**补齐缺根**（只补证据、不补决策） | 新 `okx/ws_supervisor.py`；改 `okx/ws_client.py`、三处 session |
| **B · REST 调用可靠** | 错误分级（终态 vs 可重试 vs 限频）、带抖动的退避、单次调用总预算、`Retry-After` 尊重、熔断器（**只作用于公共读**）、令牌桶限速 | 新 `okx/retry.py`、`okx/rate_limit.py`；改 `okx/rest_client.py` |
| **C · 私有链路可靠** | 连接池（按凭证指纹复用 TLS，抖动即失效重建）、服务器时钟对齐（`50102` 自愈）、**写请求幂等分级 + 不确定态读回核验** | 新 `okx/client_pool.py`、`okx/clock.py`、`okx/write_safety.py`；改 `private_client.py`、`trade_client.py` |
| **D · 连接可见** | 进程内唯一的链路健康登记簿；一个只读端点；顶栏一枚状态芯片 + 一屏可展开的连接详情（三路分别可见、可手动重探） | 新 `okx/link_state.py`、`api/okx_link.py`、`lib/okxLink.ts`、`components/Chart/ConnectionStatus.tsx` |

一句话概括设计立场：**「断线」不是一个需要用户去发现的现象，而是一个系统必须在秒级自己
发现、自己修复、并且诚实地告诉用户「刚才断过、断了多久、现在补回来了」的事件。**

### 1.3 为什么这样切

- **纯核心与 IO 分离**：`retry.py` / `rate_limit.py` / `link_state.py` / `write_safety.py` 全部
  是**无 IO、时钟可注入**的纯逻辑，故障注入测试不需要网络也不需要 sleep。这是本仓库既有的
  分层习惯（`ai_auto/profit_guard.py`、`ai_auto/base_rules.py` 同构）。
- **不改既有调用方签名**：`OKXMarketWSClient` / `OKXWSClient` / `OKXRestClient` /
  `OKXPrivateClient` / `OKXTradeClient` 的**公开方法签名一律不变**，新能力通过带默认值的
  可选形参与新方法接入，故 20+ 个调用点零改动。
- **不新建第二条代码路径**：不做 `if hardening_enabled: 新逻辑 else: 老逻辑`。每个机制的
  「关掉」都是**参数取值**（熔断阈值 0 = 不熔断、连接池 TTL=0 = 退化为每次新建、连接闸速率
  0 = 不限速）。双路径会让红线路径（真实下单）出现两种未被同等测试的行为，这是本仓库多轮
  评审反复付出过代价的形状。
- **`trading.py` 一行不加**：该文件已 **844 行**，越过 CLAUDE.md 的 800 行红线且已在
  `docs/plans/ai-trade-backstop-repair-and-net-backtest/spec.md` §11.10 登记为欠账。因此新错误码的
  归一落在 `okx/accounts.py:41-57` 的 `_map_okx_code`（159 行，余量充足），中文落在
  `okx/error_text.py`——`_map_trade_code` 对未知码本来就回落 `_map_okx_code`
  （`trading.py:85-88`），链路码天然经由那一跳，**无需触碰 `trading.py`**。

### 1.4 前置事实核对（本轮依赖的既有构件，逐条已按行核实）

| # | 事实 | 位置 | 对本轮的意义 |
|---|---|---|---|
| F-1 | WS 连接参数写死 `ping_interval=None`，无 pong 超时 | `okx/ws_client.py:99` | 根因 A 的唯一现场 |
| F-2 | 应用层 ping 每 20s 发一次，异常全吞 | `okx/ws_client.py:130-136` | 心跳存在但**无判据**，改造点 |
| F-3 | `backoff = 1.0` 紧跟 `send(subscribe)` | `okx/ws_client.py:104` | 根因 B 的唯一现场 |
| F-4 | `event:error` 只转发不断链 | `okx/ws_client.py:145-152` | 订阅失败后连接空转，须纳入 ack 判据 |
| F-5 | 每 endpoint 一个 `asyncio.Task`，`_tasks` 列表 | `okx/ws_client.py:73-95` | 既有测试断言 `len(client._tasks) == 2`，**必须保留该语义** |
| F-6 | 既有 WS 测试 monkeypatch 目标是 `app.okx.ws_client.websockets.connect` | `tests/test_okx_ws_endpoints.py:96` | **建连必须仍由 `ws_client` 模块内的符号发起**，否则替身失效 |
| F-7 | 公共 REST 重试对 4xx 一视同仁；429 耗尽落 `last_exc=None` | `okx/rest_client.py:100-116` | 根因 C 的两处现场 |
| F-8 | `_RateLimiter` 是全局 0.12s 最小间隔 + 单锁 | `okx/rest_client.py:42-56` | 被令牌桶替代（等价速率 ≈8.3 req/s），须保持顺序公平 |
| F-9 | 私有 `_get` 已有 `rate_limited` 标志的正确写法，耗尽后抛 `OKX_HTTP_ERROR` | `okx/private_client.py:113-138` | 公共侧照此对齐**写法**；**码不统一**（私有侧被 `tests/test_okx_signing.py:95` 冻结，本轮有意不动，见 §2.3.5 与 §十一，[评审 P1-2]） |
| F-10 | `_post` 对 `TransportError` 一律重试，注释声明依赖 `clOrdId` 去重 | `okx/trade_client.py:29-58` | 幂等分级的依据；`close-position` 是唯一反例 |
| F-11 | `close_position` **有** `cl_ord_id` 形参但 OKX 未承诺据它去重，且不返回 `ordId` | `okx/trade_client.py:176-197` | 须走「读回核验」而非盲重试（[评审 P2-2]） |
| F-12 | `_map_okx_code` 未知码一律拍平 `OKX_ERROR`；`OKX_UNREACHABLE/OKX_HTTP_ERROR → CONNECTION_FAILED` | `okx/accounts.py:41-57` | 新链路码**必须显式登记**，否则语义在这一跳丢失 |
| F-13 | `error_text.py` 是归一码中文的**唯一**展示口径，有 AST 门禁 | `okx/error_text.py:1-54`、`tests/test_ai_trade_backstop_repair_io.py:160-178` | 新码必须同步补中文 |
| F-14 | 会话看门狗阈值 = `bar_seconds × live_auto_staleness_factor(3.0)`，`flatten_on_stale` 默认 False | `config.py:84-86`、`live_auto/session.py:271-292` | 链路态是**更快**的信号，与之并联而非替换；**平仓语义一字不改** |
| F-15 | `ai_auto` 确认根 = 一轮 AI 决策（含 LLM 调用） | `ai_auto/session.py:652-680` | 补根**绝不可**走 `feed_candle`，否则一次断线补 10 根 = 10 次 LLM 决策 |
| F-16 | `live_auto._on_confirmed` 里确认根直接驱动**真实下单** | `live_auto/session.py:309-327` | 同上，补根必须绕开信号/执行 |
| F-17 | `paper._process_confirmed(seeded=…)` 已有回放路径（模拟撮合，无真单） | `paper/session.py:282-291` | paper 的补根可复用既有回放语义 |
| F-18 | 去重判据已存在：`live_auto._is_duplicate`、`ai_auto._window.is_duplicate`、`paper` 行内判据 | 三处 session | 补根天然幂等，无需新去重 |
| F-19 | 私有客户端每次调用新建 `AsyncClient`；`trading.py` **13 处**构造、全仓合计 **17 处** | `private_client.py:66-82`、`trading.py` 多处 | 根因 D；连接池须**不改这些调用点**；且 `_get_client` 的实例级缓存必须去掉（[评审 P1-1]） |
| F-20 | 签名时间戳取本机 UTC，无偏移补偿 | `private_client.py:36-39` | 根因 E 的现场；`50102/50104` 已归一为 `OKX_TIME_SKEW`（F-12） |
| F-21 | `/api/health` 返回 dict，**未声明 `response_model`** | `api/market.py:30-47` | 加法键不会被静默吞（对比：声明了 `response_model` 的端点会吞新键） |
| F-22 | 顶栏状态点 `ConnDot` 只有 `title` 提示，无展开面 | `components/Chart/ChartToolbar.tsx:177-206` | UI 落点；`Badge` 来自 `@/components/ui` |
| F-23 | 前端 `ReconnectingWS` 退避 1s→15s 无抖动、无静默检测 | `apps/web/lib/ws.ts:29-61` | 浏览器 ↔ 本地后端一跳，同样需要活性判据 |
| F-24 | 桌面打包 env 白名单只放行 `AGENT_/LIVE_/AI_TRADE_/DISCOVERY_` | `desktop/src/lib/config.js:72-79` | **新增 `OKX_` 前缀必须同步白名单**，否则打包版所有新旋钮静默失效 |
| F-25 | `requirements.txt` 钉 `websockets==14.1`，本机 venv 实为 `15.0.1` | `services/quant/requirements.txt:8` | **禁止使用 15.0 才有的 `proxy=` 关键字**，须能力探测 |
| F-26 | 代理真源 `settings.proxy_url` = `https_proxy or http_proxy or all_proxy or None`（**只读这三个 env**） | `config.py:684-686` | WS 侧当前没用**这个设置项**，但**不等于 WS 没有代理**（见 F-27）；本轮以能力探测方式补上 |
| **F-27** | `websockets` 15 的 `connect(proxy=…)` **默认 `True`**（走 `urllib.request.getproxies()`，Windows 上**含注册表系统代理**）；**传 `None` 等于显式禁用代理** | 本机 15.0.1 实测 `inspect.signature(websockets.connect).parameters['proxy'].default is True` | **只配了系统代理**（Clash Verge 等）的用户，`proxy_url` 恒 `None`；无条件传 `proxy=` 会让 WS 全断（[评审 P0-1]） |
| **F-28** | `/api/health` 的 `okx` 键 ⇒ 前端 `okxUp` ⇒ `shouldStreamMarket` ⇒ **卸载浏览器行情 WS** | `api/market.py:31-33`、`app/page.tsx:161/189/416`、`lib/chartLive.ts:45-47` | 任何让 `rest.health()` 变 false 的机制（尤其熔断）都会**主动拆掉图表实时流**（[评审 P0-5]） |
| **F-29** | `tests/conftest.py` **没有任何 autouse 夹具**（只有临时 DB 环境变量与一个合成 K 线 fixture） | `tests/conftest.py:1-43` | 本轮 4 个新进程单例必然跨用例污染，须自带 `reset()` + autouse 夹具（[评审 P1-11]、[P0-2]） |
| **F-30** | 既有 WS 用例的等待器只 `asyncio.sleep(0)` 空转 200 次，**推不动墙钟** | `tests/test_okx_ws_endpoints.py:84-90` | 任何在建连路径上引入真实等待的机制都会打红它（[评审 P0-2]） |
| **F-31** | 三处 session 实测行数：`ai_auto` **1790**、`live_auto` **490**、`paper` **457** | `wc -l` | `ai_auto` 已两倍于 800 行红线，链路处理**必须**下沉（[评审 P1-8]） |

---

## 二、技术设计

### 2.1 架构位置与依赖方向

```
                        ┌──────────────────────────────────────────┐
                        │  纯核心（无 IO、时钟可注入、可单测）        │
                        │  retry.py  rate_limit.py  write_safety.py │
                        │  link_state.py（登记簿，只记不做）          │
                        └──────────────┬───────────────────────────┘
                                       │ 只被下面依赖，绝不反向 import
   ┌───────────────────────────────────┼───────────────────────────────────┐
   │                                   │                                   │
┌──┴───────────────┐        ┌──────────┴────────────┐        ┌─────────────┴──────────┐
│ ws_supervisor.py │        │ rest_client.py        │        │ client_pool.py         │
│ （连接状态机）    │        │ （公共读 + 熔断）      │        │ clock.py（时钟对齐）    │
└──┬───────────────┘        └──────────┬────────────┘        └─────────────┬──────────┘
   │                                   │                                   │
┌──┴───────────────┐                   │                     ┌─────────────┴──────────┐
│ ws_client.py     │                   │                     │ private_client.py      │
│ （对外 API 不变）│                   │                     │ trade_client.py        │
└──┬───────────────┘                   │                     └─────────────┬──────────┘
   │                                   │                                   │
   ├──► live_auto/session.py           ├──► api/market.py                  ├──► okx/trading.py（零改动）
   ├──► ai_auto/session.py             └──► 全仓公共取数                    └──► okx/accounts.py（+3 码）
   ├──► paper/session.py
   └──► api/ws_market.py
                                       ▼
                              api/okx_link.py（只读端点）
                                       ▼
                    前端 lib/okxLink.ts → ConnectionStatus.tsx（顶栏一屏）
```

**硬性依赖纪律**：`link_state.py` 只被写入、不主动做任何 IO；`ws_supervisor.py` **不 import**
任何 `live_auto` / `ai_auto` / `paper`；`gap_repair` 通过回调把「补根」交回 session 自己完成
（session 才知道自己的窗口与去重口径）。

### 2.2 轴 A · WS 断线自愈

#### 2.2.1 连接状态机

单条 endpoint 连接的状态取值（`link_state.WsState`）：

```
                 connect() 被闸放行
   down ─────────────────────────────► connecting
    ▲                                      │ 建连成功 + subscribe 已发
    │                                      ▼
    │                                  subscribing ──超时未收齐 ack──┐
    │                                      │ 全部频道 ack             │
    │                                      ▼                          │
    │                                    live ◄──首帧数据/pong────┐    │
    │                                      │                      │    │
    │            pong 逾期 / 静默逾期       ▼                      │    │
    └──────────────────── degraded ───强制关闭 socket──────────────┴────┘
```

- `live` 是**唯一**对上层意味着「行情可信」的状态。
- `degraded` 是**过渡**：一旦进入，立即 `ws.close()` 并走重连（不做「原地等等看」）。
  这是刻意的：一条已经证明不再送数据的连接，等待它自愈的期望收益为零，而代价是持仓在裸奔。

#### 2.2.2 三重活性判据（根因 A 的正面修复）

| 判据 | 参数 | 行为 |
|---|---|---|
| **心跳-应答** | `okx_link_ws_ping_interval_s`（默认 15）、`okx_link_ws_pong_timeout_s`（默认 8） | 距上一帧**任意入站数据**超过 ping_interval 即发 `"ping"`；发出后若 `pong_timeout` 内**没有任何入站帧**（`"pong"` 或任何业务帧都算），判定 `degraded` |
| **静默看门狗** | `okx_link_ws_silence_timeout_s`（默认 45） | 距上一帧任意入站数据超过该值即 `degraded`，与心跳判据互为兜底（心跳协程若自身异常退出，看门狗仍生效） |
| **订阅确认** | `okx_link_ws_subscribe_timeout_s`（默认 10） | `send(subscribe)` 后收集 `event=="subscribe"` 回执；超时仍有未确认频道 → `degraded`；期间收到 `event=="error"` 且该错误针对本次订阅参数 → 立即 `degraded`（同时**照旧**把错误转发给 `on_error`，不改既有语义，F-4） |

**为什么静默阈值 45s 对冷门标的也安全**：我们每 15s 主动发一次 `ping`，OKX 恒回 `"pong"`，
因此**入站帧的最小节律由我们自己保证**，与该标的成交是否活跃无关。这正是把「pong」也算作
静默看门狗喂狗事件的原因——心跳的产物同时充当活性证据，无需第二套机制。

**实现要点（伪码，落在 `ws_supervisor.py`）**：

```python
async def _pump(self, ws) -> str:
    """在一条已建立的连接上收发，直到判定失活。返回终止原因（写进登记簿）。"""
    self._last_inbound = self._clock()          # 建连即喂一次狗
    pending = set(self._channel_keys())          # 待 ack 频道
    ping_sent_at: float | None = None
    while True:
        idle = self._clock() - self._last_inbound
        if idle >= self._cfg.silence_timeout_s:
            return "silence_timeout"
        if ping_sent_at is not None and self._clock() - ping_sent_at >= self._cfg.pong_timeout_s:
            return "pong_timeout"
        if pending and self._clock() - self._opened_at >= self._cfg.subscribe_timeout_s:
            return "subscribe_timeout"
        timeout = self._next_tick_timeout(idle, ping_sent_at)   # ≤1s，保证判据及时
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
        except asyncio.TimeoutError:
            if idle >= self._cfg.ping_interval_s and ping_sent_at is None:
                await ws.send("ping")
                ping_sent_at = self._clock()
            continue
        self._last_inbound = self._clock()
        ping_sent_at = None
        await self._on_raw(raw, pending)          # 解析 / ack 收敛 / 分发给 handlers
```

`_next_tick_timeout` 是纯函数（`retry.py` 内，可单测）：返回下一次需要检查判据的最短等待，
夹在 `[0.05, 1.0]`，保证判据的检查延迟 ≤1s，同时不做忙轮询。

#### 2.2.3 退避与建连闸（根因 B 的正面修复）

- **退避算法**：去相关抖动（decorrelated jitter）
  `delay = min(max_delay, uniform(base, prev × 3))`，`base=okx_link_ws_backoff_base_s`（1.0），
  `max=okx_link_ws_backoff_max_s`（30.0）。随机源经形参注入（`rand: Callable[[float, float], float]`），
  测试里传确定性替身。**理由**：纯指数退避会让 N 个会话在同一毫秒同时重试（根因 C-3 同源）。
- **退避复位条件**（关键修复）：**不再**在 `send(subscribe)` 后复位，而是满足以下**任一**才复位：
  1. 全部频道 ack 完成**且**收到过至少一帧业务数据；
  2. 本次连接的存活时长 ≥ `okx_link_ws_healthy_after_s`（默认 20）。
  也就是说「连上就断」不再被误判为成功，退避会如实增长。
- **建连闸**：全局令牌桶 `ws:connect`，速率 `okx_link_ws_connect_rate_per_s`（默认 2.0，桶容量 2），
  **所有** WS 连接（含多标的分组同时启动、含浏览器订阅触发的 `api/ws_market.py`）共用。
  OKX 对建连有 3 次/秒/IP 的限制，2/s 留出安全边际。取 0 即关闭该闸。
- **闸只作用于「重连」（`attempt ≥ 1`），首次建连不取令牌**（[评审 P0-2]）。两个理由，缺一不可：
  1. **语义上更对**：首建的并发度本就由 `live_auto_start_concurrency`（`config.py:70`，默认 2）与
     「一个浏览器一次订阅」约束住了；真正会失控、需要被压制的是**故障期间的重连风暴**——那正是根因 B。
  2. **不这样写会打红 §7.3 声明必须原样绿的 `test_okx_ws_endpoints.py`**：桶是进程单例，同进程连跑的
     第一个用例会耗尽 burst=2，第二个用例便需等 0.5s 才拿得到令牌，而它的等待器只 `asyncio.sleep(0)`
     空转 200 次（F-30），墙钟推不到 0.5s ⇒ 订阅记录为空 ⇒ 断言红。
  另配 `LimiterRegistry.reset()` 与 `conftest.py` 的 autouse 夹具兜住跨用例污染（[评审 P1-11]）。
- **停止语义不变**：`stop()` 仍取消任务并置停止事件；`_stop.is_set()` 时**不再重连**。

#### 2.2.4 恢复后的补根：只补证据，不补决策

重连成功且中断时长 ≥ 1 根 bar 时，supervisor 触发一次 `StreamEvent(kind="resync")`。
session 收到后调用 `okx/gap_repair.py`：

```python
async def fetch_missing(inst_id: str, bar: str, *, since_ts: int | None,
                        max_bars: int) -> list[Candle]:
    """取 since_ts 之后的已确认历史根（升序，最多 max_bars 根，不含当前未收线根）。"""
```

- 数据源是既有的 `rest_client.get_candles`（**不新增取数路径**）。
- 只返回 `ts > since_ts` 且 `ts + bar_seconds <= now` 的根（**排除未收线根**，避免把
  forming 根当确认根喂进策略）。
- 上限 `okx_link_gap_repair_max_bars`（默认 300）；超过上限即**只补最近 300 根**并在
  登记簿记 `gap_truncated`，同时推一条 status 说明（**禁止静默截断**）。

**三处 session 的补根口径（本轮最重要的安全决定）**：

| session | 补根做什么 | 补根**不**做什么 | 依据 |
|---|---|---|---|
| `live_auto` | 逐根 `_is_duplicate` → `_append_candle` + `_bar_index += 1` + 推 `candle` 事件；**全部补完之后统一调一次** `_maybe_refresh_htf(最后一根 ts)` | 不调 `_drive_signals`、不调 `executor.apply/reconcile`、不触发会话止盈止损、**不写 `_last_candle_wall`** | F-16：确认根直接下真单，按 10 分钟前的价下单是纯损失；HTF 逐根刷会打出 N 次 REST（[评审 P2-3]） |
| `ai_auto` | 逐根 `_window.is_duplicate` → `_window.append_confirmed` + `_sizing.note_candles`+ 推 `candle`；**补完之后**刷一次 `_atr_px` **并同步 `_wake.note_atr(self._atr_px)`** | 不触发决策轮、不调 `_wake.note_new_bar()`、**不写 `_last_candle_wall` / `_last_px_wall` / `_live_row` / `_last_live_px`** | F-15：一根一次 LLM 决策，补 10 根 = 10 次付费决策且全部基于过期价；后四个字段见下方【禁写清单】 |
| `paper` | 走既有 `_process_confirmed(candle, seeded=False, emit_candle=True)` | —— | F-17：模拟撮合无真金白银，补齐反而使净值曲线连续、真实 |

**【禁写清单】ai_auto 的四个「实时证据年龄」字段，补根一律不许碰（[评审 P0-4]）**：
`_last_candle_wall`（`ai_auto/session.py:230`）、`_last_px_wall`（`:235`）、`_live_row`、`_last_live_px`。
其中 `_last_px_wall` 是**利润保护层新鲜度豁免的唯一真源**（`:235` 注释；`:818` 传入 `PreviewRequest`、
`:1236` 传入保护层）。`feed_candle` 的确认根分支写的是
`self._last_candle_wall = self._last_px_wall = time.time()`（`:653`）——补根**照抄这一行就是事故**：
一次 10 分钟断线之后，保护层会认为判定价是「0 秒前的」，据此执行棘轮与兑现。
这四个字段的语义是「**实时**证据的年龄」，而补根拿到的是**历史**证据；它们只能由真正的实时帧推进。
（同一形状的坑上一轮已在 `ai-trade-profit-protection` 的 D-1 付过一次代价。）

**【`_atr_px` 与 `note_atr` 同进同出】**（[评审 P1-9]）：`ai_auto/session.py:658-660` 的注释写明
「ATR 判据与参照断面共用这一个数（**唯一入口**）」。补根只刷 `_atr_px` 而漏掉 `_wake.note_atr(...)`
会把这个唯一入口劈成两份，唤醒器据旧 ATR 判定。

**【补根的失败路径】**（[评审 P1-7]）：补根在 **session 侧的独立 `asyncio.Task`** 里跑，
**绝不**把异常回传给 supervisor（一次取数失败不许打死刚刚才恢复的连接）。失败时：
1. 登记簿记 `gap_repair_failed`（含 `since_ts` 与错误码）；
2. 推一条 status 如实说明「缺失的 N 根 K 线**未能补齐**」——**禁止静默留洞**，空洞会让后续
   指标窗口不连续，而这是最难查的一类错；
3. 保留同一个 `since_ts`，下一次 `resync` 事件到来时**再试一次**（不做独立重试定时器，
   不新增第二条调度路径）。

补根完成后，**下一根真正的实时确认根**照常驱动完整链路——此时窗口/指标已完整，策略看到的
是一条没有空洞的序列。补根天然幂等（F-18 的三处去重判据），重复触发不会重复计入。

#### 2.2.5 与既有会话看门狗的分工

既有 `_check_staleness`（F-14）是**行情停滞**判据（阈值 = bar × 3），本轮**一字不改**其
平仓语义（`live_auto_flatten_on_stale` 仍默认 False，仍是 `> stale_after × 4` 才平）。
新增的是一条**更快、只告警不平仓**的链路判据：

- 链路进入非 `live` 状态持续 ≥ `okx_link_down_alert_s`（默认 20s）→ 推一条 status：
  `「实时链路已断开 {n} 秒，正在重连（第 {k} 次尝试）；交易所侧止盈止损仍生效」`
- 链路回到 `live` → 推 `「实时链路已恢复，已补齐 {m} 根缺失 K 线」`（m=0 时不提补根）。
- 两条消息与既有停滞消息**语义可区分**：断线 = 我们收不到数据；停滞 = 连接活着但市场/频道
  没有新根。用户过去只能看到后者一句笼统的「行情连接异常」。
- **迟滞**：状态变化才推（沿用 `ai_auto/session.py` 唤醒计数「变化才推」的既有范式），
  避免重连风暴刷屏。

#### 2.2.6 兼容约束（必须逐条守住，否则既有测试红）

1. `websockets.connect(...)` 的调用**仍写在 `okx/ws_client.py` 模块内**（F-6）。做法：
   `ws_client.py` 提供模块级 `def _connect(url, **kwargs)` 直接 `return websockets.connect(url, **kwargs)`，
   supervisor 通过构造参数 `connect_factory` 拿到它。
   **工厂的返回值一律不得 `await`，只能交给 `async with`**（[评审 P1-10]）：真实的
   `websockets.connect(...)` 既可 await 又是异步上下文管理器，但既有替身
   `_ConnectRecorder.__call__` 返回的 `_FakeConnection` **不可 await**
   （`tests/test_okx_ws_endpoints.py:27-63` 只实现了 `__aenter__/__aexit__/send/__aiter__/__anext__`）。
   写成 `async with await factory(...)` 会立刻打红三个既有用例。
2. `OKXMarketWSClient._tasks` 语义不变：一个 endpoint 一个 task（F-5）。
3. `MarketWSHandlers` 新增字段 `on_stream: StreamHandler = _noop_stream`（**带默认值**），
   `OKXWSClient.__init__` 新增**末位**关键字参数 `on_stream: StreamHandler | None = None`，
   既有构造点零改动。
4. **代理（本轮最危险的一处，[评审 P0-1]）**：新增 `ws_client._connect_kwargs(settings)`，规则**只有一条**：

   ```python
   def _connect_kwargs(settings) -> dict:
       kwargs: dict = {"ping_interval": None, "open_timeout": 15}
       proxy = settings.proxy_url
       # ⚠️ websockets 15 的 proxy 默认值是 True（走 urllib.request.getproxies()，Windows 上
       # **含注册表里的系统代理**）；传 None 是「显式禁用代理」而不是「不指定」。本仓库的用户
       # 大量只配了系统代理（Clash Verge 等）而没有 HTTPS_PROXY env，此时 settings.proxy_url
       # 恒为 None——无条件传 proxy= 会把这批用户的 WS 全部断掉且永不恢复（F-27）。
       if proxy and "proxy" in inspect.signature(websockets.connect).parameters:
           kwargs["proxy"] = proxy
       return kwargs
   ```

   即：**只在 `proxy_url` 为真值且库支持该形参时才传 `proxy=`；为空一律不传这个键**。
   `settings.proxy_url` 只读 `HTTPS_PROXY / HTTP_PROXY / ALL_PROXY` 三个 env（F-26），
   桌面端由 `desktop/src/lib/config.js:57` 的 `proxyEnv()` 注入其中之一时才有值。
   **禁止**硬依赖 15.0 的关键字（F-25），也**禁止**顺手改 pin。
5. 既有替身 `_FakeConnection` 只实现 `send/__aiter__/__anext__`，**既没有 `recv`、也没有 `close`**。
   因此（[评审 P1-10]）：
   - **读**：必须走 `ws.recv()` **且**在 `AttributeError` 时回退到 `__anext__()`——写成一个 5 行的
     `_recv(ws)` 适配函数并附注释说明原因（同时新测试用自带 `recv` 的替身覆盖主路径）。
   - **强制关闭**：判定 `degraded` 后的 `ws.close()` 必须走 `close = getattr(ws, "close", None)`，
     没有就直接退出 `async with` 让 `__aexit__` 收口——**不得**假设替身有 `close`。
   **这是不改既有测试就能通过的唯一写法。**
6. **`ping_interval=None` 保留不动**（[评审 P2-5]）：应用层三重判据（§2.2.2）已覆盖活性，
   而改动这个 kwarg 会同时改变既有替身面对的参数面。实现节点**可以**额外传 `ping_timeout`
   作纵深防御，但不得移除 `ping_interval=None`。

### 2.3 轴 B · REST 调用可靠性

#### 2.3.1 错误分级表（`okx/retry.py`，纯函数）

```python
Outcome = Literal["ok", "retryable", "rate_limited", "terminal", "clock_skew"]

def classify_status(status: int) -> Outcome:
    if status == 429: return "rate_limited"
    if status in (408, 425, 500, 502, 503, 504): return "retryable"
    if 200 <= status < 300: return "ok"
    return "terminal"                      # 其余 4xx / 3xx：重试没有意义

def classify_exception(exc: BaseException) -> Outcome:
    if isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout,
                        httpx.WriteTimeout, httpx.PoolTimeout, httpx.RemoteProtocolError,
                        httpx.ReadError, httpx.WriteError)): return "retryable"
    return "terminal"

_RETRYABLE_OKX_CODES = {"50001", "50004", "50011", "50013", "50026"}
_CLOCK_SKEW_CODES = {"50102", "50104"}

def classify_okx_code(code: str) -> Outcome:
    if code == "0": return "ok"
    if code in _CLOCK_SKEW_CODES: return "clock_skew"
    if code in _RETRYABLE_OKX_CODES: return "rate_limited" if code == "50011" else "retryable"
    return "terminal"
```

> `50001` 服务暂不可用 / `50004` 端点请求超时 / `50011` 请求频率过高 / `50013` 系统繁忙 /
> `50026` 系统错误——这五个是 OKX 侧**明确的暂时态**；其余业务码（参数、余额、权限）重试
> 只会浪费配额并延长用户等待。

#### 2.3.2 退避、预算与 `Retry-After`

```python
@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int          # 含首次
    base_delay_s: float
    max_delay_s: float
    budget_s: float            # 单次逻辑调用的总墙钟预算（含等待）

def next_delay(prev_delay: float, policy: RetryPolicy,
               rand: Callable[[float, float], float]) -> float:
    """去相关抖动：min(max, uniform(base, prev*3))。prev<=0 时以 base 起步。"""
```

- `Retry-After` 头若存在且可解析为秒数，**取它与算法退避的较大者**（尊重交易所指令）。
- **预算**：`budget_s`（`okx_link_rest_budget_s`，默认 25）从进入 `_request` 起算，
  **包含**等待时间；剩余预算不足以支撑下一次退避时立即停止重试并如实抛错。
  这条直接解决「AI 会话 5s 盯市循环被一次 60s 的重试串阻塞」的堆积问题。

#### 2.3.3 熔断器（只作用于公共读，写路径豁免）

```python
@dataclass
class Breaker:
    failures: int = 0
    opened_until: float = 0.0
```

- 连续失败达 `okx_link_breaker_failures`（默认 5）→ 打开 `okx_link_breaker_cooldown_s`
  （默认 20s）。打开期间**立即**抛 `OKXError("OKX_CIRCUIT_OPEN", "交易所连接暂时熔断，{n} 秒后自动重试")`，
  不发请求。冷却结束进入半开：**放行一个探针请求**，成功即关闭并清零，失败则重新打开
  （冷却时长翻倍，上限 120s）。

**豁免名单（[评审 P0-5]，必须实现，否则本轮加固会自己制造断线）**：以下四类调用
**永不被熔断阻挡**，且它们的成功**会**清零失败计数并把冷却时长**复位**到 `okx_link_breaker_cooldown_s`
（不再翻倍累积）：

| 豁免 | 为什么 |
|---|---|
| `rest.health()` | `/api/health` 的 `okx` 键 → 前端 `okxUp` → `shouldStreamMarket(mode, okxUp)` 在 `false` 时**卸载浏览器行情 WS**（F-28：`api/market.py:31-33`、`app/page.tsx:161/189/416`、`lib/chartLive.ts:45-47`）。熔断一旦让 `health()` 恒失败，图表实时流会被主动拆掉；再叠加冷却翻倍，网络早已恢复而界面还黑着最长 120s——**把一次瞬时故障放大成一次真实断线**，与本轮目标正相反。 |
| `get_server_time_ms()` | 它是 `50102` 自愈的唯一数据源（§2.4.2）。熔断挡住它 = 时钟偏差永远修不好。 |
| `gap_repair.fetch_missing` 的取数 | 断线与公共读故障高度同源，补根**恰好**发生在熔断最可能打开的窗口里。挡住它 = 空洞永远补不上。 |
| `POST /api/okx/connectivity/probe` | 这个端点的**全部意义**就是「人工强制探一次」。 |

一句话原则：**熔断的作用是省掉注定失败的批量取数，不是拦住用来判断「是否已恢复」的那几个探针。**
- **作用域仅 `rest:public`**。私有读（账户/持仓/成交）与**全部私有写**（下单/撤单/平仓/
  设杠杆/补挂止损）**永不熔断**——一个正在裸奔的持仓需要平仓时，最不该出现的就是一个
  本地开关拒绝把请求发出去。这条是安全不变式，须在代码注释与测试里同时钉死。
- `okx_link_breaker_failures = 0` 即整体关闭熔断。

#### 2.3.4 令牌桶（替换 F-8 的最小间隔）

```python
class TokenBucket:          # rate_limit.py，monotonic 时钟经构造注入
    def __init__(self, rate_per_s: float, burst: float, clock=time.monotonic): ...
    async def acquire(self, n: float = 1.0) -> float:   # 返回实际等待秒数
```

- 作用域：`public`（`okx_link_public_rate_per_s`，默认 8.0 / burst 4）、
  `private:{指纹}`（`okx_link_private_rate_per_s`，默认 5.0 / burst 5，**按账户独立**）、
  `ws:connect`（§2.2.3）。
- 速率 0 = 不限速（等价关闭）。默认 8.0 与既有 0.12s 最小间隔（≈8.3 req/s）**基本等价**，
  但允许小突发，避免把并发取数排成一条 8 秒长的队。
- 公平性：桶内等待用 `asyncio.Lock` + 单调授时，保证先到先得（与既有 `_RateLimiter` 一致）。

#### 2.3.5 三个既有缺陷的确定修法

| 缺陷 | 修法 | 断言 |
|---|---|---|
| 429 耗尽后报 `OKX_UNREACHABLE ... None`（F-7） | 记 `rate_limited` 标志（对齐 F-9 的私有侧**写法**），耗尽后抛 **`OKX_RATE_LIMITED`**，message 写明「已退避重试 N 次仍被限频」 | 新用例断言 `exc.code == "OKX_RATE_LIMITED"` 且 message 不含 `None` |
| 4xx 空转三次 | `classify_status` 判 `terminal` → **首次即抛** `OKX_HTTP_ERROR`，message 带状态码 | 断言只发 1 次请求 |
| 无抖动同步重试 | `next_delay` 去相关抖动 | 断言两次调用的延迟序列不相等（注入确定性 rand 后断言公式） |

**【有意的不对齐：私有侧 429 仍抛 `OKX_HTTP_ERROR`】**（[评审 P1-2]，登记在 §十一 D-1）
公共侧改抛 `OKX_RATE_LIMITED`，**私有侧一字不动**——因为它被两个既有用例逐字冻结：
`tests/test_okx_signing.py:95-110`（`test_get_exhausts_429_raises_http_error`）与
`tests/test_okx_trade_signing.py:129`，而 §7.3 要求这两个文件**未经改动**仍绿。
所以本轮的口径是「**写法**对齐（都记 `rate_limited` 标志、都不再落 `last_exc=None` 的误导串），
**码暂不对齐**」。副作用如实说出来：同一个「被限频」，公共链路在 UI 上显示
「请求过于频繁，已自动退避重试仍被交易所限频」，私有链路仍显示「连接交易所失败」
（`OKX_HTTP_ERROR → CONNECTION_FAILED → 连接交易所失败`）。这是一处**已知的、被登记的**
展示不一致，留给后续一轮连同签名用例一起改；本轮**不许**顺手改用例来「统一」它。

`_request` 改造后的骨架（保持函数 ≤50 行；分类/退避/记账全部委托给纯函数与登记簿）：

```python
async def _request(self, path, params, *, retries=None):
    policy = self._policy(retries)
    deadline = time.monotonic() + policy.budget_s
    self._breaker.ensure_closed_or_raise(path)      # 熔断快速失败
    delay, rate_limited, last_exc = 0.0, False, None
    for attempt in range(policy.max_attempts):
        await self._bucket.acquire()
        outcome, payload, exc = await self._attempt(path, params)   # ≤30 行的私有方法
        if outcome == "ok":
            self._note_ok(path, payload.latency_ms)
            return payload.data
        rate_limited = rate_limited or outcome == "rate_limited"
        last_exc = exc or last_exc
        if outcome == "terminal":
            self._note_fail(path, exc); raise _as_okx_error(exc)
        delay = max(next_delay(delay, policy, self._rand), payload.retry_after or 0.0)
        if time.monotonic() + delay >= deadline:
            break
        await asyncio.sleep(delay)
    self._note_fail(path, last_exc)
    raise OKXError("OKX_RATE_LIMITED" if rate_limited else "OKX_UNREACHABLE", ...)
```

> `last_ok_ts` / `last_latency_ms` 两个既有属性（`rest_client.py:73-79`，被 `/api/health` 读取）
> **语义与写入时机一字不变**，只是同时写一份进登记簿。

### 2.4 轴 C · 私有签名链路

#### 2.4.1 连接池（根因 D）

```python
def fingerprint(api_key: str, base_url: str, demo: bool) -> str:
    """sha256(api_key|base|demo)[:16]——**绝不**在任何日志/回执里出现原始 key。"""

class OKXClientPool:
    async def acquire(self, fp: str, factory: Callable[[], httpx.AsyncClient]) -> httpx.AsyncClient
    async def release(self, fp: str) -> None            # 引用计数 -1，不关闭
    async def invalidate(self, fp: str) -> None         # 关闭并置代，下次 acquire 建新连接
    async def sweep(self, now: float) -> int            # 关闭 idle > TTL 且 refcount==0 的
    async def aclose_all(self) -> None                  # 应用关停
```

- `OKXPrivateClient._get_client` 改为从池里 `acquire`；`aclose()` 改为 `release`
  （**方法名与语义对外不变**：调用方仍然「用完就 aclose」，只是不再销毁 TCP 连接）。
  17 处调用点**零改动**（F-19）。
- **`_get_client` 不得记忆 `self._client`**（[评审 P1-1]，阻断级）。既有实现是
  `if self._client is None: self._client = httpx.AsyncClient(...)`（`private_client.py:68-77`），
  这个实例级缓存与连接池**根本冲突**：`invalidate(fp)` 关掉底层 `AsyncClient` 之后，任何仍持有
  旧引用的 `OKXPrivateClient` 实例下一次请求会撞
  `RuntimeError: Cannot send a request, as the client has been closed.`——而 `invalidate` 的触发
  条件正是「传输层异常」，也就是**故障期间必然发生**，于是「换代自愈」会变成「换代自爆」。
  正确写法：`_get_client` **每次**都 `await pool.acquire(fp, factory)`（同 fp 的复用由池内保证，
  这是池唯一该管的事）；`aclose()` = `release(fp)` 并**把 `self._client` 置回 `None`**。
- `release` 对**未曾 acquire 过**的 fp 是 **no-op**（[评审 P2-7]）：`probe_account` 一类路径存在
  「构造了客户端但从未发请求就 `aclose`」的分支（`accounts.py:79-94`），引用计数不许被打成负数。
- `httpx.AsyncClient` 建构参数补 `limits=httpx.Limits(max_connections=16,
  max_keepalive_connections=8, keepalive_expiry=90.0)`，超时保持既有
  `settings.okx_account_timeout_seconds` 口径不变。
- **失效重建**：任何 `TransportError` 或 `50111`（Key 无效，可能是凭证轮换）触发
  `invalidate(fp)`——这正是「笔记本休眠唤醒后所有池化 socket 都是半死的」的解药：
  第一次失败即整池换代，而不是让后续每个请求各自超时一遍。
- **清扫**：应用启动时注册一个 60s 周期的 sweeper（挂在 `main.py` 既有 lifespan，与
  已有 `sweeper` 协程同段），关闭空闲超过 `okx_link_pool_idle_ttl_s`（默认 120）的客户端；
  关停时 `aclose_all`。TTL=0 即退化为「每次用完就关」的既有行为。
- **安全**：池的键含 api_key 摘要 + base_url + demo 标志，**不同账户绝不复用连接**；池内
  只存 `httpx.AsyncClient`，**不存任何凭证**（签名头每请求现算）。
  指纹再加一段 **timeout 量化档**（[评审 P2-7]）：`AsyncClient` 的超时是建构参数，池化后会由
  **首个**创建者的 `Settings` 决定，测试或多配置场景下会串味；把
  `round(settings.okx_account_timeout_seconds, 1)` 并进指纹即可，代价为零。

#### 2.4.2 服务器时钟对齐（根因 E 前半）

```python
class ServerClock:
    offset_ms: float = 0.0        # 服务器 - 本机
    synced_ts: float | None = None
    async def sync(self, fetch: Callable[[], Awaitable[float]]) -> float
    def now_iso(self) -> str      # 带偏移的 ISO8601 毫秒 UTC，格式与既有一字不差
```

- 数据源：`GET /api/v5/public/time`（公共无签名，走 `rest_client` 既有 `_request`，
  新增薄方法 `get_server_time_ms()`）。
- 触发：应用启动一次；`okx_link_clock_sync_interval_s`（默认 900）周期一次；
  **收到 `50102/50104` 立即同步并把该请求重试一次**（`classify_okx_code` 的 `clock_skew` 分支）。
- 夹紧：`|offset| > 300_000ms` 视为读数异常，丢弃并保留旧值（防一次坏读把签名全带偏）。
- `private_client._iso_timestamp` 改为 `_server_clock().now_iso()`；**函数签名与输出格式不变**，
  既有签名测试（`test_okx_signing.py` / `test_okx_trade_signing.py`）保持绿。

#### 2.4.3 写请求幂等分级与「不确定态」（根因 E 后半）

```python
# okx/write_safety.py
IDEMPOTENT_WRITES = {
    "/api/v5/trade/order":          "clOrdId",       # OKX 按 clOrdId 去重
    "/api/v5/trade/order-algo":     "algoClOrdId",
    "/api/v5/trade/cancel-order":   None,            # 天然幂等（51400/51401 已三分）
    "/api/v5/account/set-leverage": None,            # 幂等设置
}
NON_IDEMPOTENT_WRITES = {"/api/v5/trade/close-position"}

def may_retry_write(path: str, payload: dict) -> bool:
    """只有「携带去重键」或「天然幂等」的写才允许在传输层异常后重试。"""
```

- `trade_client._post` 增加 `*, allow_retry: bool | None = None` 形参（默认 `None` =
  由 `may_retry_write` 判定），**其余行为与既有一字不变**。
- `close_position` 走**读回核验**：传输层异常/超时后**不重试**，改为
  `_verify_closed(inst_id, pos_side)` —— 调 `get_positions()`，
  - 该方向持仓已为 0 → 视为**成功**，返回既有形状的 `data[0]` 并附加 `"verified": True`；
  - 仍有持仓 → 抛 `OKXPrivateError("OKX_SEND_UNCERTAIN", "平仓请求结果未确认，交易所侧仍有持仓，请重试或人工核对")`；
  - 核验本身也失败 → 同样抛 `OKX_SEND_UNCERTAIN`（**绝不**假成功、**绝不**盲目再发一次）。
- **核验必须有界重读，不能只读一次**（[评审 P1-6]）：平仓请求送达后，交易所侧持仓归零有毫秒级到
  秒级的延迟，**立刻**读 `positions` 很可能仍看到仓位——只读一次会把「其实成功了」稳定地误报成
  「不确定」，而「不确定」在自愈层里是最贵的一种回答。判据：至多 `okx_link_verify_attempts`
  次（默认 3），间隔 `okx_link_verify_backoff_s`（默认 0.6s，线性不退避），总耗时 <2s；
  任何一次读到「该方向持仓为 0」即判成功并立即返回。
- **调用方契约（安全不变式，须在代码注释与用例里同时钉死）**：`OKX_SEND_UNCERTAIN` 是**终态**。
  任何上层——`live_auto/executor.py:314/673`、`okx/trading.py:251/483`、自愈层、以及界面上的
  人工平仓按钮——**一律禁止**据它自动重发平仓，只能如实展示并请人工核对。理由是本仓库刚付过一次
  代价：`ai-trade-backstop-repair` 那一轮最重的缺陷正是「待核实态被状态覆盖，于是人工连点两次
  发出了两张全仓止损，冷却锁与次数锁一起被跳过」。一个新的「不确定」错误码，如果没有配套写死
  「谁都不许据它重试」，就是在同一个坑上重新铺一块木板。
- 新归一码三个：`OKX_RATE_LIMITED` / `OKX_CIRCUIT_OPEN` / `OKX_SEND_UNCERTAIN`，
  须在 `accounts._map_okx_code` 显式登记为**原码透传**（F-12），并在 `error_text.py` 补中文（F-13）：
  - `OKX_RATE_LIMITED` → 「请求过于频繁，已自动退避重试仍被交易所限频」
  - `OKX_CIRCUIT_OPEN` → 「交易所连接暂时熔断，稍后自动恢复」
  - `OKX_SEND_UNCERTAIN` → 「请求已发出但结果未确认，请核对交易所侧状态」

### 2.5 轴 D · 连接可见（`link_state.py` + 端点 + 一屏）

#### 2.5.1 登记簿

```python
Kind = Literal["restPublic", "restPrivate", "wsBusiness", "wsPublic"]
State = Literal["up", "idle", "degraded", "down", "unknown"]   # idle 为评审新增，见下

@dataclass
class ChannelHealth:
    key: str; kind: Kind; label: str
    last_ok_ts: float | None = None
    last_error_ts: float | None = None
    last_error_code: str | None = None
    last_error_message: str | None = None      # 已脱敏、≤200 字
    latency_ms_ewma: float | None = None        # α=0.3
    consecutive_failures: int = 0
    reconnects: deque[float] = field(default_factory=lambda: deque(maxlen=64))
    resubscribes: deque[float] = field(default_factory=lambda: deque(maxlen=64))
    gap_repairs: deque[float] = field(default_factory=lambda: deque(maxlen=64))
    last_message_ts: float | None = None
    circuit: Literal["closed", "open", "halfOpen"] = "closed"
    circuit_open_until: float | None = None
    closed: bool = False                        # 该通道已被显式停用（见「通道生命周期」）

def derive_state(h: ChannelHealth, now: float, thresholds: StateThresholds) -> State:
    """纯函数。状态由**失败**驱动，不由「多久没被调用」驱动：

    - 无任何样本                                   → unknown
    - 连续失败 ≥ 2 或熔断打开                       → down
    - 有近期失败（last_error_ts 在 error_window 内） → degraded
    - WS 通道且入站静默 > silence_timeout           → degraded
    - REST 通道且最近一次成功很旧**但期间没有任何失败** → idle
    - 否则                                          → up
    """
```

- `LinkRegistry` 是进程内单例（`get_link_registry()`），**只记不做**；所有时钟经形参传入
  （`now: float | None = None`）以便测试。
- 通道键上限 32（LRU 淘汰最久未更新的 ws 通道），防多标的长跑把登记簿撑大。

**【`idle` 为什么必须有】**（[评审 P1-4]）v1 的判据是「REST 最近一次成功 >90s 即 degraded」——
**空闲不等于坏掉**。后端在没有浏览器打开时可以几分钟不发一次公共请求，届时面板会无中生有地
报「连接不稳」。诚实原则是双向的：既不许把坏的说成好的，也不许把闲的说成坏的。所以 REST 侧
只有「**发过请求且失败了**」才降级；`idle` 在面板上显示为「空闲」，**不参与 `overall` 变差**。
WS 侧保留「静默即 degraded」是因为它本就该每 15s 有一帧入站（§2.2.2 的心跳保证了最小节律）。

**【通道生命周期：只增不删会让顶栏永久变红】**（[评审 P0-3]，阻断级）
通道键含 `instId:bar`，而用户**每换一次标的或周期**，浏览器就会拆掉旧的 WS、建一条新的
（`api/ws_market.py:62-87` 每个浏览器连接新建一个 `OKXMarketWSClient`）。若登记簿只增不删，
旧键的 `last_ok_ts` 越来越旧 → 判 `degraded/down` → `overall` 取最差 → **换三次标的，顶栏芯片
就再也回不到「已连接」**。LRU 上限 32 只在超过 32 个键后才淘汰，救不了这条。因此：

- `LinkRegistry` 增 `close_channel(key)`：supervisor 的 `run()` 退出（`stop` 被置位）时**必调**，
  把该通道标 `closed=True`；
- `closed` 通道**不进** `overall` 计算、**不进**端点的 `channels` 数组（保留在内存里只为
  1 小时滚动计数不丢），下一次同键 `note_*` 会自动复活（`closed=False`）；
- 通道键增一段 **owner**：`ws:{endpoint}:{instId}:{bar}:{owner}`，`owner` ∈
  `{"chart", "live:{sessionId}", "ai:{sessionId}", "paper:{sessionId}"}`。不加这一段，
  一个 live_auto 会话与一个浏览器图表订阅同一 `BTC-USDT-SWAP:1H` 时会**共写同一条记录**，
  一方断线另一方健康 → 面板状态来回翻，而两条连接的真实健康度谁也没被如实表达。

**【`overall` 只由行情面决定】**（[评审 P1-5]）快照给**两个**派生值：

| 字段 | 由谁决定 | 用在哪 |
|---|---|---|
| `overall` | **只看** `restPublic` + 活跃的 `wsBusiness/wsPublic` | 顶栏芯片的文案与颜色 |
| `accountState` | `restPrivate` | 面板内单独一行 + 计入 `summary` 句 |

理由：`restPrivate` 只有在用户配了实盘账户时才存在，而**密钥填错、IP 未白名单、根本没配**
都会让它 `down`。若它能把 `overall` 拉红，一个只看行情、从不实盘的用户会在行情完全正常时
天天看到「OKX 不可达」——这会把这枚芯片训练成噪声，等真断线时没人再看它。
**诚实原则仍然成立**：任何一路行情通道 degraded，`overall` 不得显示 `up`；账户面的问题
不藏起来，只是不冒充行情断线，它出现在面板行与 `summary` 里。

#### 2.5.2 端点

`api/okx_link.py`（新路由，`prefix` 沿用 `main.py` 既有 `/api` 装配方式）：

- `GET /api/okx/connectivity` → 登记簿快照（**不声明 `response_model`**，F-21：声明后加法键会
  被静默剔除，本仓库已为此栽过一次）。
- `POST /api/okx/connectivity/probe` → 强制一次公共探活（`rest.health()`）+ 一次时钟同步，
  返回同上快照。**只读语义**：不触碰任何私有写路径，不需要 `live_trading_enabled`。
- `GET /api/health` 加一个键 `"link": {"overall": ..., "summary": ...}`（加法，
  `Health` TS 接口对应字段设为**可选**，老前端不受影响）。

#### 2.5.3 前端：顶栏一屏

设计立场：**状态不是一个 tooltip 能承载的东西**。当前 `ConnDot`（F-22）把三条链路压成
一个「已连接 / 不可达」，且只有 hover 才看得到时延——用户在断线时最想知道的三件事
（断的是哪一路、断了多久、系统在做什么）一件也答不了。

- **芯片（常态）**：沿用 `Badge` 视觉语汇，`tone` 由 `overall` 决定（up=long / **idle=long** /
  degraded=warn / down=short / unknown=neutral），文案分别为
  「OKX 已连接 / **OKX 已连接**（idle 不另造一句文案，见下） / 连接不稳 / OKX 不可达 / 探测中」。
  点击展开面板，`aria-expanded` / `aria-haspopup="dialog"` 齐备。
  **芯片不得因账户面变红**（[评审 P1-5]）：`accountState` 只在面板行与 `summary` 里出现。
  `idle` 在芯片上等同 `up`——顶栏是给人扫一眼的，「空闲」这种技术细节属于面板里的那一行，
  把它升到芯片上只会制造第五种需要被解释的颜色。
- **面板（展开）**：宽 280px，`role="dialog" aria-label="OKX 连接状态"`，内容三行 + 页脚：

```
┌───────────────────────────────────────────┐
│ OKX 连接状态                    ● 连接不稳 │
├───────────────────────────────────────────┤
│ ● 行情接口(REST)     28ms   12 秒前成功    │
│ ◐ 实时流(K 线)       重连中  已断 34 秒     │
│ ● 实时流(盘口)       正常    1 秒前有数据   │
│ ○ 账户接口           空闲    3 分钟前成功    │   ← accountState 单独一行，不拉红芯片
├───────────────────────────────────────────┤
│ 最近错误 · 12:04:31                        │
│ 请求过于频繁，已自动退避重试仍被交易所限频   │
├───────────────────────────────────────────┤
│ 1 小时内重连 3 次 · 补齐 K 线 2 根          │
│                          [ 重新探测 ]      │
└───────────────────────────────────────────┘
```

- 细节纪律：数字用 `tabular-nums` 对齐；相对时间用既有 `fmtTime`/自写 `relativeTime`
  （`lib/discoveryHealth.ts` 已有同名实现可复用，**不另写第二份**）；脉冲动画在
  `prefers-reduced-motion: reduce` 下自动关闭；Esc 与点击外部关闭并把焦点还给触发按钮；
  面板内容为空/端点 404（老后端）时**回落到既有 `okxUp` 二元展示**，不白屏。
- **状态中文的唯一口径**在 `lib/okxLink.ts`（`STATE_LABEL` / `KIND_LABEL`），组件不另抄字符串；
  通道 `label`（含标的名）由后端下发（值域属于后端，前端只转发）。

### 2.6 明确不碰的东西（边界）

1. **不改任何下单/平仓的决策语义**，不新增下单路径，不动 `order_builder` 的任何校验。
2. **不改 `live_auto_flatten_on_stale` 的触发条件**（链路判据只告警）。
3. **不改回测 / 策略 / 因子 / AI 决策链路**的任何行为。
4. **不动前端 `ReconnectingWS` 的既有接口**；只在其内部补抖动与静默检测（§三 3.4）。
5. **零 DB 迁移**：全部新状态都在进程内存里；断电即忘是刻意的（连接健康是瞬态事实）。
6. **不改 `requirements.txt` 的 `websockets` pin**（F-25）。

---

## 三、文件 / 模块变更计划

### 3.1 后端 · 新建

| 文件 | 意图 | 预算(行) |
|---|---|---|
| `services/quant/app/okx/retry.py` | 错误分级、去相关抖动退避、单次调用预算、supervisor 的 tick 超时——**纯函数** | ≤140 |
| `services/quant/app/okx/rate_limit.py` | `TokenBucket` + 作用域注册表（public / private:{fp} / ws:connect） | ≤120 |
| `services/quant/app/okx/link_state.py` | `ChannelHealth` / `LinkRegistry` / `derive_state`（含 `idle` 态）/ `close_channel` 生命周期 / `overall` 与 `accountState` 双派生 / 快照 wire 化 | ≤300（评审自 260 上调：P0-3 / P1-4 / P1-5 三条修复都落在本文件） |
| `services/quant/app/okx/ws_supervisor.py` | 连接状态机：建连闸（**只作用于重连**）→订阅 ack→心跳/静默判据→退避重连→事件回调 | ≤300 |
| `services/quant/app/okx/session_link.py` | 三处 session 共用的链路事件处理与补根装配（**强制存在**：`ai_auto/session.py` 已 1790 行，见 §3.2 注与 [评审 P1-8]） | ≤180 |
| `services/quant/app/okx/client_pool.py` | 按凭证指纹复用 `httpx.AsyncClient`，引用计数 / 失效换代 / 空闲清扫 | ≤180 |
| `services/quant/app/okx/clock.py` | 服务器时钟偏移追踪与 ISO 时间戳产出 | ≤110 |
| `services/quant/app/okx/write_safety.py` | 私有写的幂等分级表与判定函数 | ≤80 |
| `services/quant/app/okx/gap_repair.py` | 断线后缺根拉取（升序、排除未收线根、上限截断如实上报） | ≤110 |
| `services/quant/app/api/okx_link.py` | `GET /okx/connectivity`、`POST /okx/connectivity/probe` | ≤90 |

### 3.2 后端 · 修改

| 文件 | 改动 | 增量 |
|---|---|---|
| `okx/ws_client.py` | `_run_endpoint` 主体让位给 supervisor；模块级 `_connect`（**返回值不 await**）/ `_connect_kwargs`（代理能力探测，**空值一律不传 `proxy` 键**，[评审 P0-1]）；`MarketWSHandlers.on_stream` 加法字段；`OKXWSClient` 末位 `on_stream` 形参 | +65 / −40 |
| `okx/rest_client.py` | `_request` 接 retry/rate_limit/breaker/登记簿；`_RateLimiter` 改为 `TokenBucket` 薄封装（**保留类名**，避免外部引用断裂）；新增 `get_server_time_ms()` | +90 / −30 |
| `okx/private_client.py` | `_get_client` 走连接池（**不再记忆 `self._client`**）；`aclose` 改 release 并清空引用；`_iso_timestamp` **函数体内部**改走 `ServerClock`。**取消 v1 的 `_execute` 抽取**（[评审 P1-3]） | +45 / −15 |
| `okx/trade_client.py` | `_post` 增 `allow_retry` 形参并接 `write_safety`；`close_position` 接读回核验 `_verify_closed` | +55 |
| `okx/accounts.py` | `_map_okx_code` 登记三个新链路码为原码透传（**只加不改既有映射**） | +6 |
| `okx/error_text.py` | 三个新码的中文 | +3 |
| `config.py` | **22** 个 `okx_link_*` 设置项（§六；v1 的表其实是 20 条，写「18 个」是数错了，评审新增 2 条） | +48 |
| `main.py` | 装配 `api/okx_link.py` 路由；lifespan 注册连接池 sweeper 与 `aclose_all`、启动时一次时钟同步 | +12 |
| `live_auto/session.py` | `on_stream` 接线；`_on_link_event` / `_ingest_backfill` **实体在 `okx/session_link.py`**，此处只留绑定与转发 | **≤10** |
| `ai_auto/session.py` | 同上（补根口径见 §2.2.4 表与**禁写清单**）。1790 行，**只许留绑定** | **≤10** |
| `paper/session.py` | 同上（复用 `_process_confirmed`），只留绑定 | **≤10** |
| `api/market.py` | `/health` 加 `link` 摘要键 | +4 |
| `api/ws_market.py` | 透传 `on_stream`，把链路事件作为 `link` 事件下发浏览器（加法事件类型，前端未知类型本就忽略） | +12 |

> **`asyncio.sleep` 的调用现场必须留在各自模块里**（[评审 P1-3]，硬约束）：`private_client._get`
> 的 sleep 必须仍由 `app.okx.private_client` 发起、`trade_client._post` 的 sleep 必须仍由
> `app.okx.trade_client` 发起。既有签名用例 monkeypatch 的正是这两个模块路径
> （`tests/test_okx_signing.py:84/100/120`、`tests/test_okx_trade_signing.py:118/131`）——
> 一旦把重试循环抽到共用基类方法里，替身立刻失效，用例**不会变红、只会开始真睡**。
> 这也是本轮**取消 `_execute` 抽取**的原因：它对五条根因零贡献，却拿两个等价门禁去换一点去重复。

> **`ai_auto/session.py` 与 `live_auto/session.py` 的行数红线**（[评审 P1-8] 已更正事实并改判）：
> 实测 `ai_auto/session.py` = **1790 行**、`live_auto/session.py` = 490、`paper/session.py` = 457（F-31）。
> v1 写的「~1400 行」是过期数字；1790 已经是 CLAUDE.md 800 行红线的**两倍多**，所以这里没有
> 「首选」可言：**把 `_on_link_event` / `_ingest_backfill` 下沉到新文件 `app/okx/session_link.py`
> （提供可绑定的纯函数 / mixin，三处 session 共用）是强制做法**，三处 session 各自只许留
> **≤10 行**的绑定与转发。实现节点仍须在 §十一 实施记录里如实登记三个文件的行数变化，不得静默扩张。

### 3.3 前端 · 新建

| 文件 | 意图 |
|---|---|
| `apps/web/lib/okxLinkTypes.ts` | `OkxConnectivity` / `OkxLinkChannel` wire 类型 |
| `apps/web/lib/okxLink.ts` | 纯展示模型：状态中文、tone 映射、相对时间、摘要句、回落逻辑 |
| `apps/web/components/Chart/ConnectionStatus.tsx` | 芯片 + 可展开面板（§2.5.3） |
| `apps/web/lib/okxLink.test.ts` / `components/Chart/ConnectionStatus.test.tsx` | 见 §七 |

### 3.4 前端 · 修改

| 文件 | 改动 |
|---|---|
| `apps/web/lib/api.ts` | `okxConnectivity()` / `okxProbe()` 两个方法（沿用既有 `request<T>` 与 `ApiRequestError`） |
| `apps/web/lib/types.ts` | `Health.link?: { overall: string; summary: string }`（**可选**，加法） |
| `apps/web/components/Chart/ChartToolbar.tsx` | `ConnDot` 换成 `ConnectionStatus`；`ChartToolbarProps` 加可选 `connectivity`；**既有三个 props 保留**（回落路径） |
| `apps/web/app/page.tsx` | 新增 `useQuery(["okxLink"], api.okxConnectivity, { refetchInterval })`，结果并入既有 `toolbar` 对象。**`refetchInterval` 取函数式**（[评审 P2-8]）：面板展开时 5s、收起时 30s——顶栏已经有一条 15s 的 `/api/health` 轮询，再无条件叠一条 10s 的定频轮询是白烧的往返 |
| `apps/web/lib/ws.ts` | `scheduleReconnect` 加 ±25% 抖动；新增可选静默看门狗（`staleMs` 默认 45s 无消息即主动 `close()` 触发重连）；**构造函数签名与既有两个工厂函数不变** |

### 3.5 配置与打包

| 文件 | 改动 | 为什么必须 |
|---|---|---|
| `desktop/src/lib/config.js` | 白名单加 `key.startsWith('OKX_')` | F-24：不加，打包桌面版**所有** `OKX_LINK_*` 旋钮静默失效（`Settings` 为 `extra="ignore"`，这类漏改从不报错） |
| `desktop/scripts/config.test.mjs` | 加一条 `OKX_LINK_WS_PING_INTERVAL_S` 透传断言 | 把上一条变成会红的用例 |
| `.env.example` | 追加 `OKX_LINK_*` 注释块（默认值即注释） | 与既有约定一致 |
| `services/quant/tests/test_config.py` | 新设置项默认值断言 | 既有惯例 |

> ⚠️ **工作树现状**：`.env.example`、`desktop/src/lib/config.js`、`services/quant/app/config.py`、
> `services/quant/tests/test_config.py` **当前已被另一个在制特性改动**（`ai_trade_enabled` 默认翻转）。
> 实现与提交节点必须**按 hunk 行级暂存**本特性的改动，不得整文件 `git add`。

---

## 四、接口设计

### 4.1 `GET /api/okx/connectivity`（新增）

```jsonc
{
  "ts": 1786000000,
  "overall": "degraded",                  // up | idle | degraded | down | unknown —— **只由行情面决定**
  "accountState": "up",                   // 私有面单列，不参与 overall（[评审 P1-5]）
  "summary": "K 线实时流正在重连（已断 34 秒），其余链路正常",
  "clockOffsetMs": -120,
  "clockSyncedTs": 1785999400,
  // 已 close_channel 的通道**不出现在这个数组里**（[评审 P0-3]）
  "channels": [
    {
      "key": "rest:public", "kind": "restPublic", "label": "行情接口(REST)",
      "state": "up", "lastOkTs": 1785999988, "lastErrorTs": null,
      "lastErrorCode": null, "lastErrorMessage": null,
      "latencyMs": 28, "consecutiveFailures": 0,
      "reconnects1h": 0, "resubscribes1h": 0, "gapRepairs1h": 0,
      "lastMessageTs": null, "circuit": "closed", "circuitOpenUntilTs": null
    },
    {
      // 键含 owner 段，避免图表订阅与自动交易会话共写同一条记录（[评审 P0-3]）
      "key": "ws:business:BTC-USDT-SWAP:1H:chart", "kind": "wsBusiness",
      "label": "实时流(K 线 · BTC-USDT-SWAP 1H)", "state": "degraded",
      "lastOkTs": 1785999954, "lastErrorTs": 1785999966,
      "lastErrorCode": "WS_PONG_TIMEOUT", "lastErrorMessage": "心跳应答超时，已强制重连",
      "latencyMs": null, "consecutiveFailures": 2,
      "reconnects1h": 3, "resubscribes1h": 3, "gapRepairs1h": 1,
      "lastMessageTs": 1785999954, "circuit": "closed", "circuitOpenUntilTs": null
    }
  ]
}
```

- **不声明 `response_model`**（F-21）。
- `latencyMs` = EWMA(α=0.3) 之后**四舍五入到整毫秒**（[评审 P2-6]：登记簿内部字段叫
  `latency_ms_ewma`，wire 上叫 `latencyMs`，两个名字指同一个数，此处一次性写死口径）。
- 失败语义：本端点**永不 502**——登记簿是本地状态，取不到就是 `unknown`。

### 4.2 `POST /api/okx/connectivity/probe`（新增）

请求体空。行为：`await rest.health()` + `await server_clock.sync(...)`，随后返回 §4.1 同形快照。
并发保护：进程内单飞（in-flight 时直接返回当前快照，不排队），避免用户连点制造探活风暴。

### 4.3 `GET /api/health`（既有 · 加法键）

```jsonc
{ "...既有键一字不变": true,
  "link": { "overall": "degraded", "summary": "K 线实时流正在重连（已断 34 秒）…" } }
```

### 4.4 内部函数签名（下游按此实现，不再二次决策）

```python
# okx/retry.py
Outcome = Literal["ok", "retryable", "rate_limited", "terminal", "clock_skew"]
def classify_status(status: int) -> Outcome
def classify_exception(exc: BaseException) -> Outcome
def classify_okx_code(code: str) -> Outcome
@dataclass(frozen=True)
class RetryPolicy: max_attempts: int; base_delay_s: float; max_delay_s: float; budget_s: float
def next_delay(prev_delay: float, policy: RetryPolicy,
               rand: Callable[[float, float], float] = random.uniform) -> float
def parse_retry_after(value: str | None) -> float | None
def tick_timeout(idle_s: float, ping_interval_s: float, floor_s: float = 0.05,
                 ceil_s: float = 1.0) -> float

# okx/rate_limit.py
class TokenBucket:
    def __init__(self, rate_per_s: float, burst: float,
                 clock: Callable[[], float] = time.monotonic) -> None
    async def acquire(self, n: float = 1.0) -> float
class LimiterRegistry:
    def bucket(self, scope: str, rate_per_s: float, burst: float) -> TokenBucket
def get_limiters() -> LimiterRegistry

# okx/link_state.py
def get_link_registry() -> LinkRegistry
class LinkRegistry:
    def note_ok(self, key: str, *, kind: Kind, label: str,
                latency_ms: float | None = None, now: float | None = None) -> None
    def note_failure(self, key: str, *, kind: Kind, label: str, code: str,
                     message: str, now: float | None = None) -> None
    def note_ws(self, key: str, *, kind: Kind, label: str, event: WsNote,
                now: float | None = None) -> None      # connected|resubscribed|message|gap_repaired
    def note_circuit(self, key: str, state: str, open_until: float | None) -> None
    def close_channel(self, key: str) -> None          # 通道停用（[评审 P0-3]）：不进 overall / 不进 channels
    def snapshot(self, now: float | None = None) -> dict
    def reset(self) -> None                            # 仅测试用（autouse 夹具调用，[评审 P1-11]）

# 四个进程单例都必须提供 reset()（[评审 P1-11]）：
#   link_state.get_link_registry() / rate_limit.get_limiters()
#   client_pool.get_client_pool()  / clock.get_server_clock()
# tests/conftest.py 增一个 autouse 夹具逐个调用——该文件当前**没有任何 autouse 夹具**（F-29）。

# okx/ws_supervisor.py
@dataclass(frozen=True)
class WSStreamConfig:
    url: str; args: list[dict[str, str]]; kind: Kind; label: str; key: str
@dataclass(frozen=True)
class StreamEvent:
    key: str; state: WsState; attempt: int; downtime_s: float; detail: str
StreamHandler = Callable[[StreamEvent], Awaitable[None]]
class WSSupervisor:
    def __init__(self, cfg: WSStreamConfig, *, settings: Settings,
                 connect_factory: ConnectFactory, on_raw: Callable[[str], Awaitable[None]],
                 on_stream: StreamHandler) -> None
    async def run(self, stop: asyncio.Event) -> None

# okx/client_pool.py  /  okx/clock.py  /  okx/write_safety.py  /  okx/gap_repair.py
# —— 签名见 §2.4.1 / §2.4.2 / §2.4.3 / §2.2.4
```

---

## 五、数据模型

### 5.1 进程内（唯一新增状态，全部易失）

见 §2.5.1 `ChannelHealth`。三个单例：`LinkRegistry`、`OKXClientPool`、`ServerClock`，
均由 `get_*()` 惰性创建，`main.py` 关停时统一收口。

### 5.2 wire（TypeScript）

```ts
export type OkxLinkState = "up" | "idle" | "degraded" | "down" | "unknown";
export type OkxLinkKind = "restPublic" | "restPrivate" | "wsBusiness" | "wsPublic";

export interface OkxLinkChannel {
  key: string; kind: OkxLinkKind; label: string; state: OkxLinkState;
  lastOkTs: number | null; lastErrorTs: number | null;
  lastErrorCode: string | null; lastErrorMessage: string | null;
  latencyMs: number | null; consecutiveFailures: number;
  reconnects1h: number; resubscribes1h: number; gapRepairs1h: number;
  lastMessageTs: number | null;
  circuit: "closed" | "open" | "halfOpen"; circuitOpenUntilTs: number | null;
}
export interface OkxConnectivity {
  ts: number;
  overall: OkxLinkState;            // 只由行情面决定（[评审 P1-5]）
  accountState: OkxLinkState;       // 私有面单列
  summary: string;
  clockOffsetMs: number | null; clockSyncedTs: number | null;
  channels: OkxLinkChannel[];       // 已停用通道不在其中（[评审 P0-3]）
}
```

### 5.3 数据库

**零迁移、零新表、零新列。** 连接健康是瞬态事实，落库只会产生一张永远在写、没人查的表。
既有 `live_order_audit` 的落库口径**一字不变**（新链路码只是让 `error_code` 更精确）。

---

## 六、配置项（全部 `OKX_LINK_*` 前缀，桌面白名单须放行 `OKX_`）

| 字段（`Settings`） | env | 默认 | 含义 | 关掉它 |
|---|---|---|---|---|
| `okx_link_ws_ping_interval_s` | `OKX_LINK_WS_PING_INTERVAL_S` | 15.0 | 入站静默多久后发 `ping` | 设极大值 |
| `okx_link_ws_pong_timeout_s` | `…_PONG_TIMEOUT_S` | 8.0 | 发 ping 后多久无任何入站帧即判失活 | 0 = 不判 |
| `okx_link_ws_silence_timeout_s` | `…_SILENCE_TIMEOUT_S` | 45.0 | 入站静默多久即判失活（兜底） | 0 = 不判 |
| `okx_link_ws_subscribe_timeout_s` | `…_SUBSCRIBE_TIMEOUT_S` | 10.0 | 订阅确认超时 | 0 = 不判 |
| `okx_link_ws_healthy_after_s` | `…_HEALTHY_AFTER_S` | 20.0 | 连接存活多久才允许复位退避 | 0 = 连上即复位（旧行为） |
| `okx_link_ws_backoff_base_s` | `…_WS_BACKOFF_BASE_S` | 1.0 | 退避基数 | — |
| `okx_link_ws_backoff_max_s` | `…_WS_BACKOFF_MAX_S` | 30.0 | 退避上限 | — |
| `okx_link_ws_connect_rate_per_s` | `…_WS_CONNECT_RATE_PER_S` | 2.0 | 全局建连速率闸，**只作用于重连**（attempt ≥ 1），首次建连不取令牌（[评审 P0-2]） | 0 = 不限 |
| `okx_link_rest_max_attempts` | `…_REST_MAX_ATTEMPTS` | 4 | 公共 REST 最大尝试次数（含首次） | 1 = 不重试 |
| `okx_link_rest_budget_s` | `…_REST_BUDGET_S` | 25.0 | 单次公共调用总墙钟预算 | 极大值 = 不限 |
| `okx_link_public_rate_per_s` | `…_PUBLIC_RATE_PER_S` | 8.0 | 公共 REST 令牌桶速率 | 0 = 不限 |
| `okx_link_public_burst` | `…_PUBLIC_BURST` | 4.0 | 公共桶容量 | — |
| `okx_link_private_rate_per_s` | `…_PRIVATE_RATE_PER_S` | 5.0 | **每账户**私有令牌桶速率 | 0 = 不限 |
| `okx_link_private_burst` | `…_PRIVATE_BURST` | 5.0 | 私有桶容量 | — |
| `okx_link_breaker_failures` | `…_BREAKER_FAILURES` | 5 | 公共读连续失败多少次熔断 | 0 = 不熔断 |
| `okx_link_breaker_cooldown_s` | `…_BREAKER_COOLDOWN_S` | 20.0 | 熔断冷却（失败翻倍，上限 120s） | — |
| `okx_link_pool_idle_ttl_s` | `…_POOL_IDLE_TTL_S` | 120.0 | 私有连接池空闲存活 | 0 = 用完即关（旧行为） |
| `okx_link_clock_sync_interval_s` | `…_CLOCK_SYNC_INTERVAL_S` | 900.0 | 时钟同步周期 | 0 = 只在 50102 时同步 |
| `okx_link_gap_repair_max_bars` | `…_GAP_REPAIR_MAX_BARS` | 300 | 断线补根上限 | 0 = 不补根 |
| `okx_link_down_alert_s` | `…_DOWN_ALERT_S` | 20.0 | 链路断开多久推降级提示 | 极大值 = 不提示 |
| `okx_link_verify_attempts` | `…_VERIFY_ATTEMPTS` | 3 | 平仓「读回核验」最多读几次持仓（[评审 P1-6]） | 1 = 只读一次（不建议） |
| `okx_link_verify_backoff_s` | `…_VERIFY_BACKOFF_S` | 0.6 | 两次核验读之间的间隔（线性，不退避） | — |

> **默认值纪律**：熔断默认**开**（`okx_link_breaker_failures = 5`）之所以可接受，前提是
> §2.3.3 的**豁免名单**已落实——探针类调用永不被挡、任一次成功即复位冷却。若实现节点因故
> 无法落实豁免名单，则**必须**把该项默认值改为 `0`（关闭熔断）后再放行，二者取其一，
> 不许「豁免没做、默认还开着」（[评审 P0-5]）。

---

## 七、测试与验收

### 7.1 后端新增用例（全部离线，禁止真实网络、禁止真实 sleep 超过 0.2s）

| 文件 | 用例 | 判据 |
|---|---|---|
| `test_okx_retry_policy.py` | `classify_status` 全分支；`classify_okx_code` 五个可重试码；`next_delay` 单调有界；`parse_retry_after`；`tick_timeout` 夹紧 | 纯函数表驱动 |
| `test_okx_rate_limit.py` | 桶按速率放行；突发耗尽后等待；`rate=0` 不阻塞；并发 acquire 先到先得 | 注入假 monotonic |
| `test_okx_link_state.py` | `derive_state` 四态；EWMA；1h 滚动计数；LRU 上限 32；快照 wire 键完整；脱敏 | 纯函数 |
| `test_okx_ws_supervisor.py` | **①半开连接**（替身只接 ping 不回任何帧）→ `pong_timeout` 内判失活并重连；**②订阅永不 ack** → `subscribe_timeout` 后重连；**③连上即断的热循环** → 第 3 次退避 > 第 1 次（退避未被复位）；**④建连闸** → 1 秒内建连次数 ≤ 桶容量+速率；**⑤stop 幂等** | 全部注入假时钟 + 假连接 |
| `test_okx_rest_resilience.py` | 400 只发 1 次且抛 `OKX_HTTP_ERROR`；三次 429 抛 `OKX_RATE_LIMITED` 且 message 不含 `"None"`；`Retry-After: 3` 被采纳；预算耗尽提前停；熔断打开后**不发请求**、半开放行一个探针 | 替身 `httpx.AsyncClient` |
| `test_okx_client_pool.py` | 同指纹复用同一实例；不同账户**不**复用；`aclose` 后仍可复用（release 语义）；`invalidate` 后换新实例；sweep 关闭空闲；`aclose_all` | — |
| `test_okx_clock_skew.py` | 偏移写入后 `now_iso` 平移；`|offset|>300s` 被丢弃；`50102` 触发一次同步 + 一次重试后成功 | — |
| `test_okx_write_safety.py` | `place_order`/`order-algo`/`cancel`/`set-leverage` 允许重试；**`close-position` 不重试**；核验发现无持仓→成功且带 `verified`；仍有持仓→`OKX_SEND_UNCERTAIN`；核验自身失败→`OKX_SEND_UNCERTAIN`（**不得**假成功） | 替身 client |
| `test_okx_gap_repair.py` | 只返回 `> since_ts` 的已收线根；排除 forming 根；上限截断并记 `gap_truncated`；空洞为 0 时返回空 | 替身 rest |
| `test_okx_link_api.py` | 端点形状齐全（逐键断言）；无 `response_model` 吞键；probe 单飞；后端异常时仍 200 | `httpx.ASGITransport` |
| `test_okx_session_link.py` | 三处 session：链路断开 ≥ 阈值推降级消息、恢复推恢复消息（**变化才推**）；**`live_auto` 补根不调用 `executor.apply`**；**`ai_auto` 补根不触发决策轮**；`paper` 补根走既有回放；补根后窗口无空洞且不重复 | 直接注入 `StreamEvent` |
| `test_okx_error_codes.py` | 三个新码经 `_map_okx_code` **原样透传**；`error_text` 有中文；既有码映射**逐条未变** | 表驱动 + 冻结断言 |

**评审新增用例（每一条都对应一处 P0/P1，缺了它那处修复就是「改了但没人守」）**：

| 文件 | 用例 | 对应 |
|---|---|---|
| `test_okx_ws_connect_kwargs.py` | `proxy_url` 为空 ⇒ `_connect_kwargs()` **不含 `proxy` 键**（不是 `proxy=None`）；`proxy_url` 有值 ⇒ 含且等于该值；库无 `proxy` 形参时一律不含 | **P0-1** |
| `test_okx_ws_supervisor.py`（补） | 首次建连**不消耗**建连令牌（把桶容量设 0 仍能首连）；第 2 次及以后取令牌 | **P0-2** |
| `test_okx_link_state.py`（补） | `close_channel` 后该通道**不出现在** `snapshot()["channels"]`、**不影响** `overall`；同键再 `note_ok` 即复活；两个 owner 不同的同标的通道**互不覆盖** | **P0-3** |
| `test_okx_session_link.py`（补） | ai_auto 补根后 `_last_px_wall` / `_last_candle_wall` / `_live_row` / `_last_live_px` **逐个未变**（补根前后取值相等）；补根后 `_wake.note_atr` 被调用且实参 `is` `_atr_px` | **P0-4 / P1-9** |
| `test_okx_rest_resilience.py`（补） | 熔断打开时 `health()` / `get_server_time_ms()` / 补根取数**仍然发出请求**；其中任一成功后失败计数清零且冷却时长**复位**（不翻倍） | **P0-5** |
| `test_okx_client_pool.py`（补） | `invalidate` 之后，**同一个** `OKXPrivateClient` 实例的下一次请求走的是新 `AsyncClient`（不复现 `client has been closed`）；未 acquire 就 `release` 是 no-op | **P1-1 / P2-7** |
| `test_okx_link_state.py`（补） | REST 通道「很久没成功但期间零失败」判 `idle` 且 `overall` 仍 `up`；WS 通道同样条件判 `degraded` | **P1-4** |
| `test_okx_link_api.py`（补） | `restPrivate` 为 `down` 时，`overall` 仍随行情面为 `up`，`accountState` 为 `down` | **P1-5** |
| `test_okx_write_safety.py`（补） | 第 1 次读仍见持仓、第 2 次读为 0 ⇒ **判成功**（不误报 UNCERTAIN）；三次都见持仓 ⇒ `OKX_SEND_UNCERTAIN` 且 `close_position` 只被**发出过一次** | **P1-6** |
| `test_okx_session_link.py`（补） | `fetch_missing` 抛错时：session **不崩**、supervisor **不被打断**、推出一条含「未能补齐」的 status、登记簿有 `gap_repair_failed` | **P1-7** |
| `test_okx_session_link.py`（补） | 三处 session 各自的链路方法体 ≤10 行（AST 断言），实体在 `okx/session_link.py` | **P1-8** |
| `test_okx_ws_supervisor.py`（补） | 用**不带 `recv`、不带 `close`** 的替身（逐字复制既有 `_FakeConnection`）跑通一轮建连→判失活→重连，全程无 `AttributeError`；工厂返回值未被 `await` | **P1-10** |
| `tests/conftest.py` | autouse 夹具逐个 `reset()` 四个进程单例 | **P1-11** |

### 7.2 前端用例

| 文件 | 用例 |
|---|---|
| `lib/okxLink.test.ts` | overall 推导（任一行情通道 degraded 不得显示已连接）；**`accountState=down` 时 overall 不变红**（P1-5）；**`idle` 在芯片上等同 `up`**（P1-4）；中文标签全覆盖（含 `idle`）；相对时间边界；端点缺失时回落 `okxUp` |
| `components/Chart/ConnectionStatus.test.tsx` | 芯片文案随 overall 变；点击展开/Esc 关闭/焦点归还；四通道逐行渲染；**账户面异常只出现在面板行、芯片仍为绿**（P1-5）；`connectivity=null` 时回落旧展示不崩 |

> 组件用例必须 `NODE_ENV=test` 运行（否则 React `act()` 报错，仓库既有坑）。

### 7.3 必须原样绿的既有用例（等价门禁）

`test_okx_ws_endpoints.py`（双端点路由 + `_tasks` 语义 + stop 幂等）、`test_ws_market.py`、
`test_okx_signing.py`、`test_okx_trade_signing.py`（**签名串一字节不变**）、`test_okx_cancel_order.py`、
`test_okx_normalize.py`、`test_accounts_api.py`、`test_live_auto_session.py`、`test_live_auto_group.py`、
`test_ai_trade_*`（全部）、`test_paper_*`、`test_market_headers.py`、`test_config.py`。
**任何一条需要改动才能通过，都说明本轮越界了**——除 `test_config.py`（新设置项）与
`desktop/scripts/config.test.mjs`（新白名单）这两处**加法**之外。
（评审补充：`tests/conftest.py` 新增 autouse 夹具属于**测试基建加法**，不算改既有用例。）

**评审点名的三个高危等价门禁**（它们最容易被本轮的三个机制打红，实现节点每写完一步就单跑一次）：

| 文件 | 它守住的是什么 | 会被什么打红 |
|---|---|---|
| `test_okx_ws_endpoints.py` | 建连路径上**不许有真实等待**（等待器只 `asyncio.sleep(0)` 空转，F-30）；替身**没有 `recv` / `close` / 不可 await**（F-6） | 建连闸作用于首连（P0-2）、工厂返回值被 `await`（P1-10）、强制关闭硬调 `ws.close()`（P1-10） |
| `test_okx_signing.py` | 私有 429 耗尽 = `OKX_HTTP_ERROR`；`_iso_timestamp` 的格式；`app.okx.private_client.asyncio.sleep` 是有效的 patch 目标 | 私有侧改抛新码（P1-2）、`_execute` 把 sleep 搬走（P1-3） |
| `test_okx_trade_signing.py` | 同上，patch 目标是 `app.okx.trade_client.asyncio.sleep` | `_execute` 抽取（P1-3）——注意它**不会红，只会开始真睡**，是最隐蔽的一种失效 |

### 7.4 红灯基线（先量后改，避免把既有红当成回归）

实现节点**必须先在干净 HEAD worktree 实测**一次全量并记录基线。按最近数轮的记录，预期基线为：
`test_ai_trade_prior_session.py` 7 条 + `factor_library_expansion` 孤儿测试 7 条（HEAD 已红），
以及 `test_agent_ws.py` 的全量顺序 flake（分块跑必绿）。**若实测与此不符，以实测为准并登记。**

运行纪律（仓库既有坑，逐条照做）：
- 全量 `pytest` **不可并发两个进程**（共用临时 DB 会在 ~19% 处静默挂住）；上一次跑完须确认进程已退出。
- 用 `python -u -m pytest -q > log 2>&1` 而非直接 `pytest -q`（块缓冲会让长跑看起来卡死）。
- 源码正则扫描类冻结用例一律写 `\r?\n`（`core.autocrlf=true`）。

### 7.5 手工验收（真实环境，人工门禁）

1. **拔网线 30s 再插回**：顶栏芯片 ≤20s 变「连接不稳/不可达」；恢复后 ≤10s 变回；面板显示
   「1 小时内重连 1 次 · 补齐 K 线 N 根」；K 线图无空洞；**运行中的自动交易不因此下任何单**。
2. **切换代理节点**（模拟半开连接）：同上，且日志里出现一次 `pong_timeout` 或 `silence_timeout`。
3. **本机时钟手动调快 60s**：私有请求先 `50102`，随后自动同步并成功；面板显示 `clockOffsetMs`。
4. **8 标的分组同时启动**：无 429 整组失败（既有 `live_auto_start_concurrency` + 新令牌桶双保险）。
5. **打包桌面版**：`OKX_LINK_WS_PING_INTERVAL_S=5` 写进 `userData/agent.env` 后生效（验证 F-24 的白名单）。

### 7.6 放行条件（DoD）

1. §7.1 / §7.2 全部用例新增并绿；§7.3 全部既有用例**未经改动**仍绿。
2. §7.4 的红灯基线已实测记录，且改动后**没有新增红**。
3. 四个新码/新状态在**前端有真实展示落点**（不得只有后端算了没人看——本仓库已多轮栽在此）。
4. 桌面白名单 `OKX_` 已加且有用例守门。
5. `okx/*.py` 每个文件 ≤800 行、每个方法 ≤50 行、形参 ≤5、嵌套 ≤4；若触碰既有欠账文件，
   在实施记录里如实登记行数变化。
6. 手工验收 §7.5 的 1、2 两项由人在真实 Windows 桌面版上确认（截图或日志）。
7. **（评审新增）§评审记录 的 5 条 P0 与 11 条 P1 逐条有对应用例**，且实现节点在 §十一 里对每一条写出
   「怎么改的 / 用例名 / 实测绿」三段——**光在正文里改了字不算闭合**。
8. **（评审新增）代理回归的人工门禁**：在**只开系统代理、不设 `HTTPS_PROXY` env** 的 Windows 机器上
   （即本仓库最常见的 Clash Verge 场景）启动打包版，确认 K 线实时流能连上。这条是 P0-1 的
   最终判据——它在离线用例里**测不出来**，因为替身根本不关心 `proxy` 传了什么。
9. **（评审新增）熔断豁免二选一已落实**：要么豁免名单在代码与用例里都在，要么
   `okx_link_breaker_failures` 默认值为 `0`。

---

## 八、风险与缓解

| # | 风险 | 影响 | 缓解 |
|---|---|---|---|
| R-1 | **写请求重试造成重复下单** | 资金损失（最严重） | 幂等分级表（§2.4.3）：只有带去重键或天然幂等的写才重试；`close-position` 改读回核验；`test_okx_write_safety.py` 逐条钉死；`clOrdId` 生成逻辑一字不改 |
| R-2 | **熔断器在需要平仓时挡住请求** | 无法止损 | 熔断**只作用于公共读**；私有读写永不熔断，代码注释 + 用例双重锁定 |
| R-3 | **连接池跨账户复用连接** | 凭证串号（安全事故） | 池键含 `sha256(api_key)|base|demo`；池内不存凭证；`test_okx_client_pool.py` 断言不同账户不复用 |
| R-4 | **活性判据过敏，健康连接被误杀** | 无谓重连、增加建连配额压力 | 判据以「我们自己每 15s 发的 ping 必得 pong」为最小节律，与市场活跃度解耦；阈值 45s 是 ping 周期的 3 倍；建连闸兜住误杀带来的重连速率 |
| R-5 | **补根把过期行情喂进下单链路** | 按 10 分钟前的价下单 | §2.2.4「只补证据不补决策」三处口径表 + 三条专门用例（断言 `executor.apply` 未被调用、决策轮未触发） |
| R-6 | **`websockets` 版本差异**（pin 14.1 / 实测 15.0.1） | 打包版启动即崩 | 代理参数走 `inspect.signature` 能力探测；不使用任何 15.0 独占 API；不改 pin |
| R-7 | **既有 WS 测试替身没有 `recv`** | 既有测试红 | `_recv(ws)` 适配（`recv` 优先，缺失回退 `__anext__`），并保留 `websockets.connect` 的模块内调用点（F-6） |
| R-8 | **`ai_auto/session.py` 已近行数红线** | 触发 CLAUDE.md 硬规则 | 首选把链路处理下沉到 `okx/session_link.py` 供三处共用（§3.2 注） |
| R-9 | **新键被 `response_model` 静默吞掉** | 前端永远拿不到数据（本仓库栽过） | 新端点**不声明** `response_model`；`/health` 本就没有；用例逐键断言 |
| R-10 | **桌面打包漏透传新 env** | 所有旋钮在打包版静默失效（本仓库栽过两次） | §3.5 白名单 + `config.test.mjs` 用例 |
| R-11 | **UI 过度告警**（重连风暴刷屏） | 用户被噪声淹没 | 状态变化才推；面板用滚动计数而非逐事件流水；芯片只有 4 种终态文案 |
| R-12 | **令牌桶把并发取数排成长队** | 首屏变慢 | 默认速率与既有 0.12s 最小间隔等价并允许 burst 4；`rate=0` 可一键关闭 |
| R-13 | 工作树已有他人在制改动（4 文件） | 误提交别人的半成品 | 提交节点按 hunk 行级暂存；`config.py` / `.env.example` / `desktop/src/lib/config.js` / `test_config.py` 四文件逐 hunk 核对 |
| **R-14** | **代理参数写反，WS 全断**（[评审 P0-1]） | 只配系统代理的用户（本仓库主流场景）**彻底连不上**，比现状严重得多 | `proxy_url` 为空**一律不传** `proxy` 键（§2.2.6-4）；`test_okx_ws_connect_kwargs.py` 守；§7.6-8 人工门禁在真机确认 |
| **R-15** | **加固机制自己制造断线**（[评审 P0-5]） | 熔断 → `health()` 假报 down → 前端卸载行情流 → 冷却翻倍最长 120s | §2.3.3 豁免名单 + 成功即复位冷却；用例断言熔断打开时探针仍发；兜底是把熔断默认关掉 |
| **R-16** | **可见性组件自己说谎**（[评审 P0-3 / P1-4 / P1-5]） | 顶栏芯片永久变红或无中生有报警 → 用户学会忽略它 → 真断线时没人看 | 通道生命周期（`close_channel`）+ `idle` 态 + `overall` 只看行情面；三条各有用例 |
| **R-17** | **新「不确定」码被上层当成「失败」而自动重发平仓**（[评审 P1-6]） | 两张全仓平仓单（本仓库上一轮刚付过的代价） | `OKX_SEND_UNCERTAIN` 写死为终态 + 有界重读先把误报压掉 + 用例断言 `close_position` 只发出一次 |

---

## 九、实施顺序（每步独立可测、可单独回滚）

1. **纯核心先行**：`retry.py` + `rate_limit.py` + `link_state.py` + `write_safety.py` 与其四个测试文件。
   此步零接线、零行为变化，先把判据钉死。
2. **REST 韧性**：改 `rest_client.py`（分级/退避/预算/熔断/桶/登记簿）+ `test_okx_rest_resilience.py`。
   验收：`/api/health` 行为不变、既有取数用例全绿。
3. **WS 自愈**：`ws_supervisor.py` + 改 `ws_client.py` + `test_okx_ws_supervisor.py`。
   验收：`test_okx_ws_endpoints.py` / `test_ws_market.py` **未经改动**仍绿。
4. **私有链路**：`client_pool.py` + `clock.py` + 改 `private_client.py` / `trade_client.py` /
   `accounts.py` / `error_text.py` + 三个测试。验收：签名类既有用例逐字节不变。
5. **会话接线与补根**：`gap_repair.py` + `session_link.py` + 三处 session + `test_okx_session_link.py`。
6. **可见性**：`api/okx_link.py` + `main.py` 装配 + 前端三文件 + 两个前端测试 + 配置/打包/`.env.example`。

> 每一步结束都跑一次分块测试；只有第 6 步结束才跑全量并对账 §7.4 基线。

---

## 十、给下游节点的注意事项

1. **先复现再修**（CLAUDE.md §七）：根因 A/B/C 的每一条都要先有一条**会红**的用例，再动业务代码。
   「半开连接」用例是本轮的核心资产——它是过去所有断线故障的最小复现。
2. **中文只有一份**：新码中文只进 `okx/error_text.py`；前端状态中文只进 `lib/okxLink.ts`。
   本仓库已两次因为「中文活在注释里 / 前端另抄一份」而在用户界面上显示原始英文码。
3. **别造死数据**：任何新增计数/状态，若在前端没有展示落点，就不要加——先想清楚它显示在哪一格。
4. **别改安全语义**：`live_auto_flatten_on_stale`、名义上限、双闸、`clOrdId` 生成、平仓的
   `sz ≤ |pos|` 二次校验——本轮**一个字都不动**。
5. **提交纪律**：只暂存本特性文件；四个混合文件按 hunk 精确暂存；不 `git commit` 于设计/评审节点。
6. **（评审新增）先跑等价门禁再跑新用例**：§7.3 点名的三个高危文件，每完成 §九 的一步就单跑一次。
   本轮有两处失效是**不会变红、只会变慢或变错**的（`_execute` 搬走 sleep、`proxy` 传 `None`），
   靠「全量绿」发现不了它们——只能靠这里点名的判据去正面验。
7. **（评审新增）不许用「改用例」来消解冲突**：若发现某处修改需要动 §7.3 的既有用例才能过，
   正确处置是**改设计**（本轮已为此砍掉 `_execute` 抽取、把建连闸限定到重连），而不是改用例。

---

## 十一、已登记的欠账与有意偏离（评审新增）

本节是给实现节点与后续轮次看的**账本**。写在这里的每一条都是「知道、认了、且说清了代价」，
不是「忘了」。实现节点须在本节下追加「实施记录」小节，对 §评审记录 的 5 条 P0 与 11 条 P1
逐条写出「怎么改的 / 用例名 / 实测绿」。

| # | 欠账 / 偏离 | 代价 | 什么时候还 |
|---|---|---|---|
| **D-1** | 私有链路 429 耗尽仍抛 `OKX_HTTP_ERROR`，与公共侧新码 `OKX_RATE_LIMITED` **不统一**（[评审 P1-2]） | 同一个「被限频」在 UI 上有两句不同中文（公共：「请求过于频繁…」；私有：「连接交易所失败」） | 下一轮连同 `test_okx_signing.py:95` / `test_okx_trade_signing.py:129` 一起改；本轮**不许**顺手改用例 |
| **D-2** | `okx/trading.py` 仍为 **844 行**，越过 CLAUDE.md 800 行红线 | 既有欠账，本轮**一行不加**（新码归一走 `accounts._map_okx_code`） | 沿用 `ai-trade-backstop-repair-and-net-backtest` §11.10 的登记，独立一轮拆分 |
| **D-3** | `ai_auto/session.py` **1790 行**（F-31），远超红线 | 本轮靠 `okx/session_link.py` 下沉把增量压到 ≤10 行/处，但**存量没动** | 独立一轮拆分；本轮实施记录须写出改动前后行数 |
| **D-4** | paper 补根产生的成交与实时成交在前端**不可区分**（`_seeded_trade_count` 只标预热段，[评审 P2-4]） | 净值曲线连续了，但「这几笔是断线后补出来的」这件事没有展示落点 | 若后续有人依据 paper 成交做归因，须先补一个边界标记 |
| **D-5** | 前端 `ReconnectingWS` 只补抖动与静默看门狗，**不做**与后端登记簿对等的状态机（§3.4） | 浏览器↔本地后端这一跳的健康度仍只有二元「open/closed」 | 本跳是 localhost，故障率远低于 OKX 跳；有实测证据再说 |


---

## 十一之二、实施记录（实现节点填写）

> 本节兑现 §7.6-7 与放行条件 12：对 §评审记录 的 5 条 P0 与 11 条 P1 逐条写「怎么改的 / 用例名 /
> 实测绿」。**在正文里改了字不等于闭合。**

### A. P0 逐条闭合

| # | 怎么改的 | 用例名 | 实测 |
|---|---|---|---|
| **P0-1** `proxy` 只在有值时才传 | `okx/ws_client.py:_connect_kwargs` 只有一条规则：`if proxy and "proxy" in inspect.signature(websockets.connect).parameters` 才写这个键；为空**一律不写**（保留库默认的系统代理发现）。`ping_interval=None` 原样保留。 | `test_okx_ws_connect_kwargs.py`（4 条：为空不含键 / 有值透传 / `ping_interval` 保留 / **库默认不是 `None`** 这条前提事实本身也钉成用例） | 绿 |
| **P0-2** 建连闸只作用于重连 | `ws_supervisor.run()` 里 `if attempt >= 1: await self._await_connect_token()`——首连不取令牌。 | `test_okx_ws_supervisor.py::test_the_first_connect_never_takes_a_connect_token`（把桶容量压到取不到令牌，首连仍发生）+ `::test_reconnects_do_take_a_connect_token` | 绿；且 `test_okx_ws_endpoints.py` **未经改动** 3 条全绿 |
| **P0-3** 登记簿生命周期 | `LinkRegistry.close_channel()`；supervisor `run()` 的 `finally` 必调；`snapshot()` 只输出未停用通道；`overall` 只对活跃通道求最差；通道键含 owner 段（`ws:{endpoint}:{instId}:{bar}:{owner}`，由 `OKXMarketWSClient(owner=…)` 传入，三处 session 各传 `live:/ai:/paper:{sessionId}`，浏览器图表为 `chart`）。 | `test_okx_link_state.py`（停用后不进 channels / 不影响 overall；同键再上报即复活；两个 owner 不同的同标的通道互不覆盖）、`test_okx_link_api.py::test_a_closed_channel_disappears_from_the_endpoint`、`test_okx_ws_supervisor.py::test_stop_is_idempotent_and_closes_the_channel_in_the_registry` | 绿 |
| **P0-4** ai_auto 补根禁写四字段 | 补根实体在 `okx/session_link.py:ingest_ai_auto`，全文**不出现**对 `_last_candle_wall` / `_last_px_wall` / `_live_row` / `_last_live_px` 的写入。live_auto 侧因既有 `_append_candle` 会顺手写 `_last_candle_wall`，改为**快照后还原**（保持「行的形状只有一个真源」）。 | `test_okx_session_link.py::test_ai_auto_backfill_leaves_all_four_realtime_age_fields_untouched`（四字段逐个断言补根前后相等）、`::test_live_auto_backfill_leaves_the_staleness_watchdog_baseline_alone` | 绿 |
| **P0-5** 熔断豁免名单 | `rest_client._request(..., probe: bool = False)`；`probe=True` 跳过 `ensure_closed_or_raise`。`health()` / `get_server_time_ms()` 恒传 `probe=True`；`get_candles(probe=…)` 供 `gap_repair` 与探测端点传入。`_Breaker.note_ok()` 同时清零失败计数**并把冷却时长复位**到基准（不再翻倍累积）。 | `test_okx_rest_resilience.py::test_probe_calls_are_never_blocked_by_an_open_breaker`、`::test_a_successful_probe_resets_the_breaker_and_its_cooldown`、`::test_breaker_failures_zero_disables_it_entirely` | 绿；故 §六「默认值纪律」的二选一取**豁免已落实**这一支，`okx_link_breaker_failures` 保持默认 5 |

### B. P1 逐条闭合

| # | 怎么改的 | 用例名 | 实测 |
|---|---|---|---|
| **P1-1** `_get_client` 不记忆 `self._client` | 每次 `await get_client_pool().acquire(fp, factory, idle_ttl_s=…)`；`self._client` 只作为**外部注入口**被读（既有签名用例直接塞 MockTransport 客户端）。`aclose()` = `release(fp)` 并清空引用。`_get` / `_post` 的重试循环里**每一跳**重新取客户端，换代后下一跳即拿到新连接。 | `test_okx_client_pool.py::test_the_same_private_client_instance_keeps_working_after_invalidate`、`::test_aclose_returns_the_client_to_the_pool_and_clears_the_reference`、`::test_an_injected_client_is_owned_by_whoever_injected_it`、`::test_releasing_a_fingerprint_never_acquired_is_a_no_op` | 绿 |
| **P1-2** 私有侧 429 有意不对齐 | `private_client._get` / `trade_client._post` 的 429 耗尽仍抛 `OKX_HTTP_ERROR`，并在代码注释里显式登记这是**有意的**不对齐（§十一 D-1），不顺手改用例来「统一」它。 | `test_okx_signing.py` / `test_okx_trade_signing.py` **未经改动**全绿 | 绿 |
| **P1-3** 取消 `_execute` 抽取 | 未做该重构；`asyncio.sleep` 的调用现场仍分别留在 `app.okx.private_client` 与 `app.okx.trade_client` 模块内。 | 正面验（这处失效**不会变红、只会开始真睡**）：`test_okx_trade_signing.py` 6 条 + `test_okx_signing.py` 全部合计耗时 **<1s**；若替身失效，两条 429 用例单独就会真睡 ~1.5s 与 ~3.5s | 绿 |
| **P1-4** `idle` 态 | `derive_state` 由**失败**驱动；REST 侧「最近一次成功很旧**且期间零失败**」判 `idle`，`_SEVERITY` 里 `idle` 与 `up` 同级（不参与 `overall` 变差）。WS 侧保留「静默即 degraded」（它本就该每 15s 有一帧入站）。 | `test_okx_link_state.py::test_rest_idle_is_idle_not_degraded`、`::test_ws_silence_is_degraded_under_the_same_condition`、`::test_idle_does_not_drag_overall_below_up`；前端 `lib/okxLink.test.ts`「idle 在芯片上等同 up」 | 绿 |
| **P1-5** `overall` 只由行情面决定 | `snapshot()` 把通道分成 `_MARKET_KINDS` 与 `restPrivate` 两组，分别派生 `overall` 与 `accountState`；私有面照实进 `summary`。前端 `chipView` 只读 `overall`，`accountLine` 单列一行。 | `test_okx_link_state.py::test_a_broken_private_channel_never_reddens_overall`、`test_okx_link_api.py::test_a_broken_private_channel_leaves_overall_on_the_market_side`；前端 `okxLink.test.ts`「账户面 down 不许把芯片变红」、`ConnectionStatus.test.tsx`「账户面异常只出现在面板行，芯片仍是已连接」 | 绿 |
| **P1-6** 平仓有界重读 + 终态契约 | `trade_client._verify_closed`：至多 `okx_link_verify_attempts`（默认 3）次、间隔 `okx_link_verify_backoff_s`（默认 0.6s，线性不退避）；任一次读到该方向持仓为 0 即判成功并返回带 `verified` 的既有形状。契约（**禁止任何上层据 `OKX_SEND_UNCERTAIN` 自动重发平仓**）写进方法 docstring；中文文案刻意**不含**「请重试」。业务拒单不走核验路径（确定的失败不该被改判成「不确定」）。 | `test_okx_write_safety.py::test_a_bounded_reread_recognises_a_close_that_actually_succeeded`、`::test_a_position_that_never_clears_yields_a_terminal_uncertain_code`（断言 `close_position` **只发出过一次**）、`::test_a_failing_verification_never_fakes_success`、`::test_a_business_rejection_is_not_turned_into_uncertain`、`test_okx_error_codes.py::test_the_uncertain_message_does_not_invite_a_retry` | 绿 |
| **P1-7** 补根失败路径 | `SessionLink._repair` 在**独立 `asyncio.Task`** 里跑，异常不回传 supervisor；失败即登记 `gap_repair_failed`、推一条含「未能补齐」的 status，并保留 `_pending_repair` 使下一次恢复事件按同一 `since_ts` 再试一次（不新增第二条调度路径）。 | `test_okx_session_link.py::test_a_failing_backfill_never_leaves_a_silent_hole`、`::test_a_failed_backfill_is_retried_on_the_next_recovery` | 绿 |
| **P1-8** 强制下沉 `session_link.py` | 三处 session 各只加 **4 行**（1 行 import + 3 行绑定与传参）。行数：`ai_auto` 1790 → **1794**、`live_auto` 490 → **494**、`paper` 457 → **461**；`okx/session_link.py` 新增 215 行。 | `test_okx_session_link.py::test_sessions_only_bind_and_never_reimplement_the_link_logic`（AST：有且只有一次 `make_session_link`，且三处 session 源码里不许出现 `fetch_missing` / `get_link_registry` / 任何 `ingest_*`）、`::test_the_link_binding_costs_each_session_at_most_ten_lines` | 绿 |
| **P1-9** `_atr_px` 与 `note_atr` 同进同出 | `ingest_ai_auto` 补完之后刷一次 `_atr_px` **并紧接着** `self._wake.note_atr(self._atr_px)`（`_sizing.note_candles` 同样只在补完后刷一次）。 | `test_okx_session_link.py::test_ai_auto_keeps_atr_and_note_atr_in_lockstep`（断言实参 `is` `_atr_px`） | 绿 |
| **P1-10** 工厂返回值不 await / `_recv` 回退 / `close` 可选 | `_one_connection` 直接 `async with self._connect(url)`；`_recv(ws)` 先 `recv` 后回退 `__anext__`；`_force_close` 走 `getattr(ws, "close", None)`。 | `test_okx_ws_supervisor.py::test_runs_against_a_stub_without_recv_or_close`（替身**刻意不继承**可用版，确保真的没有那两个方法）、`::test_the_connect_factory_return_value_is_never_awaited` | 绿 |
| **P1-11** 四单例 autouse 重置 | `tests/conftest.py` 新增 `_reset_okx_link_singletons`（autouse），逐个 `reset()` 登记簿 / 令牌桶 / 连接池 / 服务器时钟；四个单例均已提供 `reset()`。 | 该夹具本身即门禁；`test_okx_ws_endpoints.py` 三条同进程连跑仍绿即其效果 | 绿 |

### C. 实施过程发现的方案缺陷 / 代码缺陷

| # | 发现 | 处置 |
|---|---|---|
| **I-1（阻断级 · 真缺陷）** | `LinkRegistry._touch` 在**新建通道之后、盖 `updated_ts` 之前**调 `_evict_if_needed()`，于是新建那条的 `updated_ts` 还是 `0.0`，它自己就是「最久未更新」的那个——**每次超上限都会把刚建的通道当场淘汰掉，旧的反而永存**。这恰好是 P0-3 要修的失效形状（旧键留着拖 `overall`、新键根本进不来），修了一半等于没修。 | 先盖时间戳再淘汰，并给 `_evict_if_needed(protect=key)` 加保护键。用例 `test_okx_link_state.py::test_channel_cap_evicts_the_least_recently_updated_ws_channel` 守它（修复前红）。 |
| **I-2（真缺陷 · 会把进程转死）** | `TokenBucket.acquire` 的补令牌循环用严格 `self._tokens >= n` 判据，而 `deficit / rate` 睡完之后重算出的令牌数在浮点上常常差最后一个 ulp，循环会以纳秒级步长空转下去。更硬的一条：**Windows 的 `time.monotonic()` 默认分辨率约 15.6ms**，睡 5ms 醒来墙钟可能一点没动，没有睡眠下限就是一个真正的热循环——一个用来「限速」的闸自己把进程转死。本轮实测中它直接把测试进程挂住。 | 判据带 ε（`1e-9`）+ 单次睡眠下限（`1ms`），`try_acquire` 同步处理。用例 `test_okx_rate_limit.py::test_acquire_converges_even_when_the_clock_barely_moves` 复现「时钟只差一个 ulp」的场景。 |
| **I-3（对 spec 的实现口径澄清）** | spec §2.2.4 写「supervisor 触发一次 `StreamEvent(kind="resync")`」，但 §4.4 定义的 `StreamEvent` 并没有 `kind` 字段（只有 `key/state/attempt/downtime_s/detail`）。 | 按 §4.4 的签名实现：`state == "live"` 且 `downtime_s >= bar_seconds`（或上一次补根欠账未清）即触发补根。语义与 §2.2.4 完全一致，不新增字段。 |
| **I-4（行数预算超标 · 如实登记）** | `okx/session_link.py` **215 行**（spec 预算 ≤180）、`okx/ws_supervisor.py` **326 行**（预算 ≤300）；`okx/link_state.py` 290 行（预算 ≤300，未超）。超出的部分全是 docstring 与「为什么」注释——补根口径与三重活性判据正是本轮最该被读懂的两段代码。 | **不删注释去对齐一个估算出来的数字**：两者都远在 CLAUDE.md 的 800 行硬红线内，且方法均 ≤50 行、形参 ≤5、嵌套 ≤4。按 §7.6-5 在此如实登记。 |
| **I-5** | `paper/session.py` 没有 `executor` 属性（它直接持有 `SimBroker`），而 `SessionLink._settings()` 原按 `session.executor.settings` 取。 | `_settings()` 对没有 `executor` 的会话回落 `get_settings()`；用例三处 flavor 各跑一遍。 |
| **I-6** | 补根失败的 status 文案里若写 markdown `**未能补齐**`，前端状态行会显示字面星号（`ai-trade-holding-horizon` 那轮已付过一次代价）。 | 文案里一律不写星号，并在代码注释里说明原因。 |
| **I-7** | `okx/trading.py` 仍为 **844 行**（越过 800 行红线）。 | 本轮**一行未加**（新码归一走 `accounts._map_okx_code`，中文走 `okx/error_text.py`），沿用 §十一 D-2 的既有登记。 |

### D. 放行条件对账（§评审结论 12 条）

1. ✅ `proxy` 只在有值时才传 —— `test_okx_ws_connect_kwargs.py`；**但 §7.6-8 的真机人工门禁仍未做**（见下）。
2. ✅ 建连闸只作用于重连 + `conftest.py` autouse 重置四单例；`test_okx_ws_endpoints.py` 未经改动 3 条全绿。
3. ✅ `close_channel` 生命周期 + owner 段。
4. ✅ `overall` 只由行情面决定、`accountState` 单列、REST 闲判 `idle`。
5. ✅ 熔断豁免名单已落实 ⇒ 默认值保持 5（不取「改 0」那一支）。
6. ✅ ai_auto 补根禁写四字段，用例逐个断言相等。
7. ✅ `_get_client` 不记忆 `self._client`，`aclose` = release 且清空引用。
8. ✅ `OKX_SEND_UNCERTAIN` 终态 + 有界重读 + 「只发出过一次」断言。
9. ✅ 未做 `_execute` 抽取，`asyncio.sleep` 现场留在各自模块（并用耗时正面验过没在真睡）。
10. ✅ 强制下沉 `okx/session_link.py`，三处 session 各 +4 行；行数变化已在 B/P1-8 记录。
11. ✅ 红灯基线先量后改（见 E 节）。
12. ✅ 本节即实施记录。

**唯一未闭合项**：§7.6-6 与 §7.6-8 的**真机人工门禁**——拔网线 30s 再插回 / 切换代理节点的真实观察，以及「只开系统代理、不设 `HTTPS_PROXY` env」的 Windows 机器上启动打包版确认 K 线实时流能连上。这两条按设计就**在离线用例里测不出来**（替身根本不关心 `proxy` 传了什么），须由人在真实桌面版上确认后才算本轮完成。

### E. 交付物与实测

**后端新增**（10 个模块）：`okx/retry.py`、`okx/rate_limit.py`、`okx/link_state.py`、
`okx/ws_supervisor.py`、`okx/session_link.py`、`okx/client_pool.py`、`okx/clock.py`、
`okx/write_safety.py`、`okx/gap_repair.py`、`api/okx_link.py`。

**后端修改**：`okx/ws_client.py`、`okx/rest_client.py`、`okx/private_client.py`、
`okx/trade_client.py`、`okx/accounts.py`（+`_LINK_CODES` 原码透传）、`okx/error_text.py`（+3 条中文）、
`config.py`（+22 项）、`main.py`（路由 + `_okx_link_sweeper` + `aclose_all`）、
`api/market.py`（`/health` 加 `link` 摘要键）、`api/ws_market.py`（透传 `on_stream` 为 `link` 事件）、
三处 session 各 +4 行绑定。

**前端新增**：`lib/okxLinkTypes.ts`、`lib/okxLink.ts`、`components/Chart/ConnectionStatus.tsx`
及两个测试。**前端修改**：`lib/api.ts`（`okxConnectivity` / `okxProbe`）、`lib/types.ts`
（`Health.link?`）、`components/Chart/ChartToolbar.tsx`（`ConnDot` → `ConnectionStatus`，既有三个
props 保留作回落）、`app/page.tsx`（函数式 `refetchInterval`：面板展开 5s / 收起 30s）、
`lib/ws.ts`（±25% 抖动 + 45s 静默看门狗，构造签名不变）。

**配置与打包**：`desktop/src/lib/config.js` 白名单加 `OKX_` 前缀、`desktop/scripts/config.test.mjs`
加透传断言（8 条全绿）、`.env.example` 追加 `OKX_LINK_*` 注释块、`tests/test_config.py` 加默认值断言。

**新增测试**（后端 13 个文件）：`test_okx_retry_policy.py`(51)、`test_okx_rate_limit.py`(7)、
`test_okx_link_state.py`(19)、`test_okx_ws_supervisor.py`(10)、`test_okx_ws_connect_kwargs.py`(4)、
`test_okx_rest_resilience.py`(13)、`test_okx_client_pool.py`(14)、`test_okx_clock_skew.py`(8)、
`test_okx_write_safety.py`(24)、`test_okx_gap_repair.py`(9)、`test_okx_session_link.py`(22)、
`test_okx_link_api.py`(7)、`test_okx_error_codes.py`(20)。**前端 2 个文件**：`lib/okxLink.test.ts`(17)、
`components/Chart/ConnectionStatus.test.tsx`(11)。

**后端全量实测**（单进程 `python -u -m pytest -q` 一次跑完，用时 7m26s）：
**14 failed / 3270 passed / 1 skipped**。14 条红**逐条与 §7.4 预期基线一致**，全部与本轮无关：

- `test_ai_trade_prior_session.py` **7 条**——失败原因是 `KeyError: 'signalPriors'`（AI 信号先验那
  一轮的在制特性，HEAD 已红）；
- `test_factor_library_expansion.py` **7 条**——工作树里的孤儿测试文件（未跟踪，属于因子库扩充特性）。

**新增红：0 条。** `test_agent_ws.py` 本次全量顺序未复现 flake（§7.4 记的那条概率性失败）。

**实测**：前端全量 **148 文件 / 1646 用例全绿**；`tsc --noEmit` 只余既有的
`components/Factor/WeightBatchArena.test.tsx` 一处旧错（HEAD 已有，非本轮回归）。
等价门禁（`test_okx_ws_endpoints` / `test_okx_signing` / `test_okx_trade_signing` / `test_ws_market` /
`test_okx_cancel_order` / `test_okx_normalize` / `test_accounts_api` / `test_market_headers`）
**未经改动 70 条全绿**；`test_live_auto_session` / `test_live_auto_group` / `test_ai_trade_api` /
`test_ai_trade_group` / `test_ai_trade_backstop_repair_io` 合计 98 条全绿。

### F. 终审复核（代码评审 / 提交节点）

逐条按「覆盖度 / 回归风险 / 命名与注释一致性」复核了全部交付面。**5 条 P0 与 11 条 P1 逐条核实到
代码现场，全部确实闭合**（不是只在正文里改了字）：`proxy` 为空真的不写那个键、建连闸真的只在
`attempt >= 1` 取令牌、`close_channel` 真的在 `run()` 的 `finally` 里、`ingest_ai_auto` 全文真的没有
那四个字段的写入、探针四路真的都传 `probe=True`。以下是复核中**实测到并当场修好**的 7 处，每一处
都先有一条会红的用例：

| # | 问题 | 为什么重要 | 修法 / 守门用例 |
|---|---|---|---|
| **RV-1（真缺陷）** | `fetch_missing` 按缺根数**原样**取（`limit=min(missing, max_bars)`），而 `/candles` 的最新一根是**未收线**的 forming 根、一定会被过滤器丢掉 ⇒ **每次补根都稳定地少补最老的那一根**；补完之后 `since_ts` 已前移，那个洞**再也不会被补上**。 | 一句「已补齐 N 根」的成功回报底下藏着一个洞——正是 §2.2.4 立志消灭的那类错，而且它每次都发生。 | 取数上限给 forming 根留一个位置（上界仍夹 300，留在 `/candles` 单次分支上）。`test_okx_gap_repair.py::test_the_request_leaves_room_for_the_forming_bar_so_no_bar_is_silently_lost`（新增 `_WindowRest` 替身按 `limit` 截窗，旧替身忽略 `limit` 故测不出这一类） |
| **RV-2（覆盖缺口）** | `WsNote` 里的 `gap_truncated` **无人发出**，`fetch_missing` 的 `closed[-max_bars:]` 是一次**静默**截断——§2.2.4 明写「禁止静默截断」，§十 note 3 明写「没有展示落点的新增状态就不要加」。 | 死枚举 + 静默丢数据，两条都是本仓库反复付过代价的形状。 | `SessionLink._dropped_bars()`（复用纯函数 `missing_bar_count`）→ 登记簿记 `gap_truncated` + 恢复文案追加「缺口超过上限 N 根，最早的 M 根未补齐」。`test_okx_session_link.py::test_a_truncated_backfill_says_so_instead_of_silently_dropping_bars` |
| **RV-3（真缺陷）** | 连接池引用计数**两头都错**：`_get_client` 每一跳 `acquire` 一次而 `aclose()` 只 `release` 一次（只增不减 ⇒ `sweep` 永远清扫不到、`okx_link_pool_idle_ttl_s=0` 那条「用完即关」的退路**永远不生效**）；反过来「构造了但没发请求就 aclose」的实例会 `release` 一份**自己从未持有**的引用，减掉的是**别的实例**正持有的那份——TTL=0 下等于当场关掉别人在用的连接。 | 后者正是 [评审 P2-7] 点名的形状，只在池外挡了一半（`max(0, …)` 只防负数，不防减错人）。 | 实例记 `_pool_holds`，`aclose()` **逐一**归还、没取过就什么都不还；`_invalidate_pooled` 把计数清零（引用随被摘掉的那一代一起作废）。`test_okx_client_pool.py::test_every_hop_that_took_a_reference_gives_it_back`、`::test_a_client_that_never_sent_a_request_returns_nothing_to_the_pool` |
| **RV-4（真缺陷）** | `TokenBucket` 在 `burst < n`（例如把 `OKX_LINK_PUBLIC_BURST` 配成 0）时**永不放行**：令牌被 `min(burst, …)` 夹死，补令牌循环以 1ms 步长转到天荒地老。 | 与 §十一之二 I-2 同形——**一个用来限速的闸自己把进程挂死**，只是换了个入口；而 `burst` 没有「0 = 关掉」的语义，用户很容易这么配。 | 有效容量取 `max(burst, n)`：容量不足即退化为纯速率限制，而不是永不放行。`test_okx_rate_limit.py::test_a_bucket_smaller_than_the_request_still_lets_it_through` |
| **RV-5** | `_request` 在**最后一次**尝试失败之后仍算一次退避并 `await asyncio.sleep(delay)` 才抛错——等的是一次不会发生的重试，默认最长白拖 8 秒。 | 拖的正是断线期间用户等待错误浮现的那几秒，而 AI 会话的 5s 盯市循环也在同一条路径上排队（§2.3.2 的预算就是为这件事设的）。 | 末次尝试后直接 `break`。`test_okx_rest_resilience.py::test_the_last_attempt_is_not_followed_by_a_pointless_backoff` |
| **RV-6（回归风险）** | 补根跑在**独立 task** 上，而 `paper` / `live_auto` 的确认根管线本就跑在 `self._lock` 里——补根却没持那把锁。WS 收发泵同时还在送实时帧，一根实时确认根插进补根序列中间就会让后面的历史根被去重判据整段丢掉；paper 那条更糟，两次 `_process_confirmed` 交错改的是同一条净值曲线。 | 补根**恰好**发生在重连之后，而那正是新根最可能到来的时刻。 | `SessionLink._ingest()` 在会话有锁时持锁补根（`ai_auto` 无此锁，按既有口径直调）。`test_okx_session_link.py::test_the_backfill_runs_inside_the_session_lock` |
| **RV-7（噪音）** | `POST /okx/connectivity/probe` 里 `if get_settings().okx_link_clock_sync_interval_s >= 0` 是一个**恒真**的判据（该项取值域为非负；0 关的是**周期**同步）。 | CLAUDE.md §五：不要为「理论上不会发生」的分支写防御代码，那只是噪音——何况它读起来像「0 时不同步」，与实际行为相反。 | 去掉该条件并写清「手动探测恒同步一次」；连带清掉已无用的 `get_settings` 导入。 |

**一处如实登记的偏离（不改，理由充分）**：`OKXMarketWSClient.__init__` / `OKXWSClient.__init__` 因加了
末位 `owner` 形参而各有 **6 个形参**，越过 CLAUDE.md 与 §7.6-5 的「形参 ≤5」。**不能改**：这两个构造
点被 §7.3 声明必须**未经改动**仍绿的 `tests/test_okx_ws_endpoints.py:96/118/135` 与
`tests/test_ws_market.py:171/172` 直接调用，聚合成配置对象就会打红等价门禁——而 §十 note 7 写死了
「不许用改用例来消解冲突」。两个构造器都只是带默认值的可选具名参数，不是「靠位置堆参数」那类可读性
问题；按 §7.6-5 在此如实登记，与 I-4 的行数预算同一处置。

**一处提交纪律导致的落点调整**：§3.5 原计划把 `OKX_` 白名单的守门断言加进 `desktop/scripts/config.test.mjs`，
但该文件在工作树里是**未跟踪的新文件**、且整篇属于另一个在制特性（自动交易总闸默认值翻转）——它的
「prod 默认注入 `AI_TRADE_ENABLED`」那条用例依赖那个特性的 `config.js` hunk，本轮不提交那个 hunk，
连文件一起提交就会在仓库里留下一条必红的用例。故本轮的断言独立成
**`desktop/scripts/config.okx-link.test.mjs`**（2 条，`node --test` 实测绿），只承载本特性的内容；
R-13 的「按 hunk 精确暂存」在这里的形态就是「按文件拆分」。

**复核后实测**：新增/改动的 5 个测试文件 + 全部 OKX 套件 + 三处 session 套件 **435 条全绿**；
等价门禁（`test_okx_ws_endpoints` / `test_okx_signing` / `test_okx_trade_signing` / `test_ws_market` /
`test_okx_cancel_order` / `test_okx_normalize` / `test_accounts_api` / `test_market_headers`）
**仍未经改动全绿**。后端**全量**（单进程 `python -u -m pytest -q`，6m32s）：
**15 failed / 3276 passed / 1 skipped**——比 E 节多出的 7 条 passed 正是本节新增的 7 条回归用例，
15 条红逐条等于 §7.4 基线（`test_ai_trade_prior_session` 7 + `test_factor_library_expansion` 7 +
`test_agent_ws::test_ws_emits_ready_history_and_full_run` 的全量顺序 flake，**单跑实测 6 条全绿**）。
**新增红：0 条。** 前端 `okxLink` / `ConnectionStatus` 28 条全绿，`tsc --noEmit` 仍只余既有的
`WeightBatchArena.test.tsx` 旧错。

---

## 评审结论

### 结论：**有条件通过（有条件通过 · approved with conditions）**

方案的**问题诊断是扎实的**——五条根因（WS 无活性判据、退避被过早复位、REST 错误不分级、
私有链路每次新建 TLS、时钟无自愈且不确定的写被当成失败的写）我逐条按行核实过，
每一条都指向真实的代码现场，没有一条是想象出来的。四条轴的切法（纯核心与 IO 分离、
不改公开签名、不建第二条代码路径、每个机制都有关得掉的取值）与本仓库既有的分层习惯同构，
`trading.py` 一行不加、零 DB 迁移、补根「只补证据不补决策」这三条纪律尤其是对的——
其中「补根不驱动决策」直接挡住了本轮最贵的一种事故（按十分钟前的价下真单）。

但 v1 有 **5 条 P0**，其中三条会在**实现的第一天**就暴露：`proxy=None` 会把
「有时断线」变成「只配了系统代理的用户彻底连不上」（websockets 15 的 `proxy` 默认是 `True`，
传 `None` 是显式禁用，本机 15.0.1 已实测）；全局建连闸会直接打红方案自己声明「必须原样绿」的
`test_okx_ws_endpoints.py`；登记簿只增不删会让顶栏那枚芯片在用户切三次标的之后永久变红。
另外两条更隐蔽但更贵：熔断器会经由 `health() → okxUp → shouldStreamMarket` 这条既有链路
**主动拆掉浏览器行情流**（一次瞬时故障被放大成最长 120 秒的真实断线，与本轮目标正相反）；
ai_auto 补根口径的含糊措辞会让利润保护层把十分钟前的价当作「刚刚的价」。

这五条有一个共同形状，值得记下来：**它们全都不在「新写的模块」里，而在「新模块与既有代码的
那一跳接缝上」**——库的默认值、既有替身的能力面、既有派生量的下游消费者、既有字段的语义。
新模块自己怎么写，作者是清楚的；接缝上对方是什么样子，只能一行行去看。

P0（5 条）与 P1（11 条）**已全部在正文内闭合**，每一条都落到了具体条款、具体默认值、
具体用例上，并在 §7.6 增了三条放行条件；P2（8 条）中 6 条已顺手改进正文，2 条记为已知取舍
（§十一 D-4/D-5）。**无未闭合的 P0 / P1。**

### 放行条件（下游实现节点必须逐条兑现，缺一条即不算完成）

1. **`proxy` 只在有值时才传**（§2.2.6-4）。`proxy_url` 为空时 kwargs 里**不许出现** `proxy` 键。
   `test_okx_ws_connect_kwargs.py` 守它，且 §7.6-8 的真机人工门禁必须有人做过——
   这条在离线用例里测不出来。
2. **建连闸只作用于重连**（§2.2.3），首次建连不取令牌；`tests/conftest.py` 增 autouse 夹具重置
   四个进程单例。判据：`test_okx_ws_endpoints.py` **未经改动**三条全绿。
3. **登记簿有生命周期**（§2.5.1）：`close_channel` 在 supervisor 停止时必调，已停用通道不进
   `overall`、不进 `channels`；通道键含 owner 段。
4. **`overall` 只由行情面决定，`accountState` 单列**（§2.5.1 / §2.5.3）；REST 侧「闲」判 `idle`
   而不是 `degraded`。芯片不得因账户面或空闲变红。
5. **熔断豁免名单落实，否则默认值改 0**（§2.3.3 / §六 默认值纪律）——二选一，不许两头都不做。
6. **ai_auto 补根禁写四字段**（§2.2.4 禁写清单）：`_last_candle_wall` / `_last_px_wall` /
   `_live_row` / `_last_live_px`，用例逐个断言补根前后取值相等。
7. **`_get_client` 不记忆 `self._client`**（§2.4.1），`aclose` = release 且清空引用；
   用例复现「invalidate 之后同一实例继续可用」。
8. **`OKX_SEND_UNCERTAIN` 是终态**（§2.4.3）：有界重读先把误报压掉，任何上层禁止据它自动重发平仓；
   用例断言 `close_position` 只被发出过一次。
9. **取消 `_execute` 抽取**（§3.2），`asyncio.sleep` 的调用现场留在各自模块——
   注意 `test_okx_trade_signing.py` 在这里**不会变红、只会开始真睡**，属于隐蔽失效，须正面验。
10. **链路处理强制下沉 `okx/session_link.py`**（§3.2 注），三处 session 各留 ≤10 行绑定；
    实施记录写出三个文件改动前后的行数。
11. **红灯基线先量后改**（§7.4）：在干净 HEAD worktree 实测一次全量并记录；改动后不得新增红。
    预期基线为 `test_ai_trade_prior_session` 7 条 + `factor_library_expansion` 孤儿测试 7 条
    + `test_agent_ws` 的全量顺序 flake，**实测与此不符以实测为准**。
12. **§十一 实施记录**：对 §评审记录 的 5 条 P0 与 11 条 P1 逐条写「怎么改的 / 用例名 / 实测绿」。
    在正文里改了字不等于闭合。

### 一句话给下游

这个方案值得建，而且建完之后用户真的会少掉一类「K 线不动了但没人告诉我」的沉默故障。
但请把最大的小心留给**接缝**：库的默认值、既有替身的能力面、既有派生量的下游消费者。
本轮 5 条 P0 全部长在那里，一条也不长在新模块自己身上。
