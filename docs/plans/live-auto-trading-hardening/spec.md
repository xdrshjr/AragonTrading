# 实时自动交易·可靠性与人机交互加固（Live Auto-Trading Hardening）开发方案

- **Feature key**: `live-auto-trading-hardening`
- **版本**: **v2**（iteration 2 · Subtask #1 方案评审与修复节点；v1 由 Subtask #0 方案设计节点产出，已对照工作树逐行复核）。本轮**不重建**功能，而是在 iteration 1 已落地并提交（commit `f0d44a7`「feat: add live auto-trading mode」，依据 `docs/plans/live-auto-trading/spec.md` v2）的实时自动交易能力之上，按全局目标**「使得实盘交易正常可靠稳健，并且符合人机交互最佳实践」**做一轮针对性加固。
- **作者**: 方案设计节点（solution architect）
- **演进链**: `live-auto-trading` v1（设计）→ v2（评审修复）→ 实现并提交（iteration 1）→ `live-auto-trading-hardening` v1（iteration 2 加固设计）→ **本方案：v2（iteration 2 评审修复，本节点）**。
- **基线代码**（本方案逐项核验对象，全部为已合并入库的实代码）：
  - 后端 `services/quant/app/live_auto/{session.py,executor.py,manager.py}`、`api/live_auto.py`、`config.py`、`db/repositories.py`、`okx/{trading.py,ws_client.py}`。
  - 前端 `apps/web/app/page.tsx`、`lib/{store.ts,liveAuto.ts,liveAutoApi.ts,liveAutoTypes.ts}`、`components/Strategy/{AutoTradeToggle.tsx,AutoTradeConfirmModal.tsx,AutoTradeStatusBar.tsx}`、`components/Live/LiveTradePanel.tsx`。
- **核心约束（与 iteration 1 一致，继续遵守）**：真实下单一律经既有 `okx/trading.py` 服务层，**不新增任何绕过护栏的下单路径**；双闸守门（`LIVE_AUTO_TRADING_ENABLED && LIVE_TRADING_ENABLED`）、预算硬上限夹紧、单笔名义上限、无既有持仓门禁、单会话独占等安全契约**保持不变**；联调铁律不变（最多 1% 资金、0.01 手 BTC、占位假凭证入测）。本方案只**增强**可靠性/可观测性/交互，不放宽任何安全护栏。

---

## 评审记录（Review Notes · v2）

> 评审节点（senior reviewer · Subtask #1）对 v1 全文逐节、逐 `file:line` 复核工作树（commit `f0d44a7` 后的当前代码）。评审维度：**可行性 / 完整性 / 一致性 / 规模合理性**。结论先说：v1 的 8 项加固点（H1–H8）的缺陷判定与 `file:line` 证据**经复核全部属实、定位准确**；但 v1 的**修复设计本身**遗漏了若干边界，其中 **1 项为 P0（加固反而引入新的真实资金安全漏洞）**。下表所有 P0/P1 已就地修复并体现在正文（§三 / §四 / §六 / §七 / §八），P2 一并收口或在正文标注为可接受取舍。
>
> 严重度口径与 v1 一致：P0=真实资金安全/可靠性硬伤、P1=可靠性或交互重大缺陷、P2=稳健性/打磨。

### v1 既有缺陷判定复核（结论：全部成立）

| v1 编号 | 复核结论 | 复核证据（工作树实代码） |
|---|---|---|
| H1（P0，盯市仅收线触发） | ✅ 成立 | `session.py:160/166` reconcile 与 `_maybe_session_exit` 仅在 `_on_confirmed` 内；`live_auto_poll_interval_s` 全仓 grep 仅命中定义行 `config.py:63`，确无引用。 |
| H2（P1，reconcile 未捕获且污染行情链路） | ✅ 成立（容错口径已在 §3.2 注明可精确收敛到 `except OKXError`） | `executor.py:220` 裸 `await self._read_positions()`；异常经 `_handle_message→_dispatch_row→on_candle`（`ws_client.py:158/166`）冒泡至 `_run_endpoint` 宽捕获 `ws_client.py:113` → 重连。**补强**：传输层异常已被 `private_client.py:122-127` / `trade_client.py:42-47` 统一归一为 `OKXPrivateError(OKXError)`，故 `except OKXError` 足以兜住网络抖动（正文 §3.2 已注明，避免实现者再加宽泛 `except`）。 |
| H3（P1，无停滞看门狗） | ✅ 成立 | `session.py` 无独立循环；`_last_confirmed_ts`（:66）未用于健康判定。 |
| H4（P1，停止平仓失败误报干净） | ✅ 成立 | `stop` 先置 `state="stopped"`（:118）后 `flatten`（:125）；`executor.flatten` 失败返回 `{ok:False}`（:244）且不改 `projected`。注：`stop` 现已有 try/except（:124-127），H4 应在其上增强而非重写。 |
| H5（P2，start TOCTOU） | ✅ 成立 | `_require_capacity`（:117）与 `self._session=session`（:73）之间存在多个 `await`（`fetch_trading_config` / `fetch_live_positions` / `_create_run`），并发 start 确可同时通过容量检查。 |
| H6（P1，前端丢弃 `error`） | ✅ 成立 | `handleLiveAutoEvent`（`page.tsx:707-736`）仅 snapshot/trade/position/status/candle 分支，无 `error`；`status` 分支（:723-726）未取 `message`。 |
| H7（P1，终止/自动停止不告知） | ✅ 成立 | `AutoTradeStatusBar.tsx` 唯一守卫为 `if (!liveAutoSessionId) return null`（:44），其后恒渲染「自动交易运行中」（:53-56），从不读 `liveAutoStatus.state`。 |
| H8（P2，弹窗风险敞口可更清晰） | ✅ 成立 | `AutoTradeConfirmModal.tsx` 已展示 availUsdt/默认/预览预算，但无会话金额阈值 / 单笔 OCO / 名义上限 / 杠杆默认说明；config 失败仅「读取失败」（:104）。 |

### v1 修复设计的遗漏（本节点新增问题清单）

| # | 维度 | 严重度 | 问题（含证据） | 处置 |
|---|---|---|---|---|
| **R-1** | 可行性（H6） | **P1** | v1 §3.6 / §八 称「toast 由**既有通知原语**承载关键 `error`」，但全仓 `apps/web` **不存在任何 toast / notification 原语**（grep `toast|notify|Toast|sonner|notification` 零命中）；现有错误呈现范式是**就地 `role="alert"` 文本**（如 `AutoTradeConfirmModal.tsx:146-150`）。依赖不存在的基建即不可实现，且自造全局 toast 系统违背本方案「最小且收敛」原则。 | §3.6 / §八：**移除 toast 依赖**，关键 `error` 改由新增 `AutoTradeEventLog`（错误红、置顶高亮）+ 终止横幅承载；不新建全局通知系统。 |
| **R-2** | 可行性 / 安全（H1） | **P0** | **盯市循环自取消导致 flatten 被中断 → 残留真实持仓无人管理**。v1 §3.1 让 `stop()` 执行 `self._monitor_task.cancel()`；但会话金额止损正是在**盯市循环内部**经 `_maybe_session_exit → stop()` 触发，此时 `self._monitor_task` 即当前任务，`cancel()` 等于**取消自身**，`CancelledError` 会在 `stop()` 的下一个 `await`（即 `await self.executor.flatten(...)`，`session.py:125`）抛出，**中断真实平仓并跳过 `_finalize_run`**——恰好重演 H4 试图消灭的「停止却留下无人管理真实仓」安全漏洞，且由本次加固自身引入。 | §3.1：`stop()` 取消盯市任务前加 `asyncio.current_task()` 守卫（仅取消「非自身」任务）；循环内触发的 `stop` 依赖 `while self.state=="running"` 自然退出，**绝不自取消**。 |
| **R-3** | 完整性 / 可靠性（H1） | **P1** | **净值缓冲无界增长（长跑真实资金进程内存泄漏）**。`_emit_equity` 向裸 `list` 追加（`session.py:264`，字段定义 :69 `self._equity: list[dict]`，snapshot 取末 1000 条 :282）。v1 把它从「每收线一次」移入「每 `poll_interval_s`（默认 5s）一次」，记录速率放大 `bar_seconds/5` 倍——1H 周期下达 720×，多日实盘会话该 list 无上限膨胀，直接违背「可靠稳健」。 | §3.1 / §六：净值缓冲改**有界**（`deque(maxlen)` ≥ snapshot 窗口 `_SNAPSHOT_EQUITY_MAX`）；盯市循环净值推送限频（每根落一条历史点，秒级仅更新瞬时 PnL 经 `status`/`position`），杜绝无界增长。 |
| **R-8** | 完整性 / 测试（H6） | **P1** | **P1 行为缺测**。v1 §四 / §七测试矩阵覆盖了 H1-H5 后端与 H7 前端，但**无任何用例覆盖 H6 的 `error` / `status.message` 上浮链路**（`handleLiveAutoEvent` 新 `error` 分支、`AutoTradeEventLog` 渲染）。CLAUDE.md §七要求「公开行为都要有单元测试覆盖正常路径与至少一条异常路径」，真实资金下单失败的可见性属 P1 关键行为，不可缺测。 | §四 / §七：新增 `AutoTradeEventLog.test.tsx`（或事件分发单测），断言 `error` 事件 → 红色事件项、`status.message`/`degraded` → 对应级别事件项。 |
| **R-4** | 完整性（H1） | **P2** | `_latest_mark_px()` 兜底用 `get_rest_client().get_ticker()`，但其签名为 `float \| None`（`rest_client.py:219-224`，无行情时返回 `None`，非抛错）。v1 仅处理「抛错回落 entry_price」，未处理 **`None` 返回**，会把 `None` 标记价传入 reconcile/净值数学。 | §3.1：`_latest_mark_px` 对 `None` 与异常**一并**回落（末确认根收盘 → `executor.entry_price`）。 |
| **R-5** | 完整性（H3） | **P2** | 看门狗初值缺定义。`_check_staleness` 依赖新增 `_last_candle_wall`，若不在 `start()`/构造时初始化（`time.time()`），暖机后首根 WS 确认根到来前（最长 `bar_seconds`）会**误判停滞**；且暖机/未起 WS 阶段应抑制判定。 | §3.3：`_last_candle_wall` 在 `start()` 订阅 WS 时以 `time.time()` 初始化；WS 未起或 `state!="running"` 时跳过停滞判定。 |
| **R-6** | 一致性（H6/H7） | **P2** | 前端类型表述与现状有偏差：①`AutoStopReason`（`liveAutoTypes.ts:5-10`）**缺 `stale_feed`**，而 §3.7 `describeStopReason` 要映射它——须**扩充**枚举并在映射保留未知值回落；②`LiveAutoStatus` 的 `message?`/`stopReason?` **已存在**（:68-69），v1 §3.6/§四「新增」措辞会误导实现者重复添加——真正新增的仅 `degraded?`/`flattenFailed?`/`residual?`。 | §四 / §六：明确「扩充 `AutoStopReason` +`stale_feed`」「`message?`/`stopReason?` 已存在，仅增 `degraded?/flattenFailed?/residual?`」。 |
| **R-7** | 规模合理性（H1） | **P2** | 盯市循环在 `self._lock` 内执行 `reconcile` 与 `_latest_mark_px` 两次网络读（各 ≤~15s 超时），子分钟周期下持锁可能短暂推迟收线信号处理。**但**这与既有 `_on_confirmed`（同样持锁内 reconcile，`session.py:144→160`）口径一致，非新增回归；信号按根触发、超时有界，属可接受取舍，不必为此过度工程化。 | §八：作为**已知可接受取舍**记录；不改设计（如未来子分钟周期成为主用法，再评估锁外预取）。 |

> **修复落点汇总**：R-2(P0)/R-1(P1)/R-3(P1)/R-8(P1) 已就地改入 §3.1 / §3.3 / §3.6 / §四 / §六 / §七 / §八；R-4/R-5/R-6 一并收口于对应小节；R-7 记入 §八 风险表。下游实现者以 v2 正文为准。

---

## 一、Overview（概述）

iteration 1 已经把「实时模式（`mode==="paper"`）+ 实盘账户（`accountMode==="live"`）」下的「自动交易」开关、确认弹窗、运行状态条以及后端 `LiveAutoSession`（逐根驱动）+ `LiveExecutor`（信号→真实 OKX 下单/平仓）+ `LiveAutoManager`（单会话独占 + 前置门禁）完整落地并通过单测。功能主链路成立：策略信号能被翻译为真实下单，随单挂交易所侧 OCO 止盈止损，K 线图画买卖 markers，左下真实持仓/成交表照常刷新。

但以「**正常可靠稳健 + 人机交互最佳实践**」为验收尺度逐行复核当前实代码后，发现若干**会在真实资金场景下造成损害或误导用户**的缺陷，集中在三类：① **盯市时效**——会话级金额止盈止损与持仓 reconcile **只在确认 K 线收线时触发**（`session.py:160/166`），对 1H 及以上周期意味着「止损金额阈值」每根才校验一次，用户设置的金额止损在一根之内可被大幅击穿，等同形同虚设；配置项 `live_auto_poll_interval_s`（`config.py:63`，注释明写「持仓 reconcile / 会话盯市轮询基准间隔」）**在全仓代码中从未被使用**。② **故障韧性**——`executor.reconcile` 拉取真实持仓（`executor.py:218`）**未捕获网络异常**，且该调用运行在行情 WS 的 K 线回调链路内，一次瞬时网络抖动会被 `ws_client.py:113` 当作断线触发整条行情 WS 重连，并跳过该根的盯市；停止会话时若平仓失败（`session.py:118` 先置 `state="stopped"` 再 `flatten`），残留的真实持仓被无人管理，而 UI 仍显示「已停止」，属安全/诚实缺陷。③ **人机交互**——`error` 类 WS 事件被前端**直接丢弃**（`page.tsx:707` 的 `handleLiveAutoEvent` 无 `error` 分支），下单失败（余额不足/被拒/IP 限制）用户**完全无感知**；`status` 事件携带的中文诊断 `message`（如「反手平仓尚未清算，本根跳过」「OCO 已触发平仓」）从不展示；状态条（`AutoTradeStatusBar.tsx`）在 `liveAutoSessionId` 存在期间**恒显示「自动交易运行中」**，无视 `liveAutoStatus.state`，故会话因金额止损被后端自动停止后，状态条仍显示「运行中」且数据停滞，用户既不知会话已结束、也不知为何结束。

本方案以**最小且收敛**的改动闭合上述缺陷：后端为 `LiveAutoSession` 增加一个**独立盯市循环**（复用 `PaperSession._replay_task` 的 `asyncio.create_task` 生命周期范式，`paper/session.py:88/118/247`），按 `live_auto_poll_interval_s` 周期 reconcile + 盯市 + 推送，使金额止盈止损与持仓校正具备秒级时效；为 reconcile/flatten 增加结构化容错与诚实失败上报；为行情停滞增加看门狗与降级提示。前端补齐 `error` 与终止态的呈现，新增轻量「自动交易事件」反馈通道与终止横幅，使「bot 此刻在做什么、为什么停、哪笔单失败」全程对用户透明。所有改动不触碰既有下单写链路与安全护栏。

---

## 二、现状基线与缺陷盘点（Defect Inventory · 加固清单）

> 每条均附**实代码 file:line 证据**与处置落点（§三/§五）。严重度：P0=真实资金安全/可靠性硬伤、P1=可靠性或交互重大缺陷、P2=稳健性/打磨。

| # | 维度 | 严重度 | 缺陷（含 file:line 证据） | 处置（落点小节） |
|---|---|---|---|---|
| **H1** | 可靠性 / 安全 | **P0** | **会话级金额止盈止损与 reconcile 仅在确认 K 线收线触发**：`_maybe_session_exit` 与 `executor.reconcile` 只在 `_on_confirmed`（`session.py:159-166`）被调用；无任何独立轮询。配置 `live_auto_poll_interval_s=5`（`config.py:63`）**全仓未引用**（grep 仅命中定义行）。对 1H+ 周期，用户设置的「止损金额阈值」每根才校验一次，单根内可被严重击穿。 | §3.1 新增 `LiveAutoSession._monitor_loop` 独立轮询任务（reconcile + 盯市 + 会话止盈止损 + 权益/持仓推送）。 |
| **H2** | 稳健性 | **P1** | **reconcile 网络异常未捕获且污染行情链路**：`executor.reconcile`（`executor.py:218-229`）直接 `await self._read_positions()`（→ `fetch_live_positions`，会抛 `OKXPrivateError`）。该方法经 `_on_confirmed`→`feed_candle`（行情 WS 回调）运行，未捕获异常会冒泡到 `ws_client._run_endpoint`（`okx/ws_client.py:113-118`）被当作断线 → 触发整条行情 WS 重连 + 跳过该根盯市。 | §3.2 reconcile / 盯市读取一律 try/except OKXError，连续失败计数 + 降级事件，不污染撮合链路。 |
| **H3** | 稳健性 / 透明 | **P1** | **无行情停滞看门狗**：行情 WS 若静默卡死（连接在但无数据），`LiveAutoSession` 既不推进 K 线也不再 reconcile（无独立循环），bot 持仓裸奔；用户无任何提示。`session.py` 仅记录 `_last_confirmed_ts`（:66）但未用于健康判定。 | §3.3 盯市循环加「最近确认根墙钟」陈旧度判定 → 降级 `status` 事件（默认仅告警，不自动平仓）。 |
| **H4** | 安全 / 诚实 | **P1** | **停止平仓失败被误报为干净停止**：`session.stop` 先置 `state="stopped"`（`session.py:118`）再 `flatten`；`executor.flatten` 平仓失败返回 `{ok:False,...}`（`executor.py:244`）但会话仍标 stopped、`manager` 清空 `_session`（`manager.py:81/108`）→ 残留真实持仓无人管理，UI 显示已停止。 | §3.4 flatten 有界重试 + 失败时高优先级 `error` + `status.flattenResidual`，UI 强提示「请手动平仓」；Run 终态记录。 |
| **H5** | 稳健性 | **P2** | **start 存在 TOCTOU**：`_require_capacity`（`manager.py:51/117`）与 `self._session=session`（`manager.py:73`）之间无锁，两个并发 `POST /start` 可同时通过容量检查，短暂违反 cap=1。 | §3.5 `LiveAutoManager` 加 `asyncio.Lock` 包裹 start 关键区。 |
| **H6** | 人机交互 | **P1** | **`error` WS 事件被前端丢弃**：`handleLiveAutoEvent`（`page.tsx:707-736`）有 snapshot/trade/position/status/candle 分支，**无 `error` 分支**；而 `executor` 每笔失败均推 `error`（`executor.py:318`）。下单失败用户零感知。`status.message`（`executor.py:314`）亦从不渲染。 | §3.6 前端新增 `error`/`status.message` 通道 → 事件流 + toast；新增 `AutoTradeEventLog`。 |
| **H7** | 人机交互 | **P1** | **终止/自动停止不被告知**：`AutoTradeStatusBar`（`AutoTradeStatusBar.tsx:50-81`）在 `liveAutoSessionId` 存在期恒渲染「自动交易运行中」，无视 `liveAutoStatus.state`；后端自动停止（`session_stop_loss`/`take_profit`/`shutdown`）后只推一条 `status{state:"stopped",stopReason}`，UI 仍显示运行中且数据停滞。 | §3.7 状态条按 `state` 切换为终止横幅（中文 stopReason + 会话盈亏 + 关闭/确认），停计时。 |
| **H8** | 人机交互 / 打磨 | **P2** | **确认弹窗风险敞口表达可更清晰**：`AutoTradeConfirmModal`（`AutoTradeConfirmModal.tsx`）已展示 availUsdt/默认/生效预算，但未点明「止盈/止损为**会话累计金额阈值**」、未展示生效杠杆默认值与单笔名义上限（1000 USD）、未说明「每笔自带交易所侧 OCO」；config 读取失败时（:104）仅显示「读取失败」但不软提示「服务端仍会夹紧」。 | §3.8 弹窗补风险敞口说明与软失败提示（纯展示，不阻断）。 |

> 说明：iteration 1 已正确实现的安全契约（双闸守门 `manager.py:111-115` / `api/live_auto.py:31-36`、预算夹紧 `manager.py:128-134`、无既有持仓门禁 `manager.py:57-60`、单会话独占、stop 刻意不守门 `api/live_auto.py:78-88`、`LIVE_NO_POSITION` 良性处置 `executor.py:198-204`、净仓反手清算确认 `executor.py:206-215`）**均保留不动**，本方案不重复设计，仅在其外围补时效/容错/交互。

---

## 三、Technical design（技术设计）

### 3.1 H1：独立盯市循环（让金额止盈止损与 reconcile 具备秒级时效）

**目标**：会话级金额止盈止损（`LiveAutoStartIn.takeProfitUsd/stopLossUsd`）与持仓真源校正不再依赖 K 线收线，而是按 `settings.live_auto_poll_interval_s`（默认 5s）独立轮询。

**实现要点**（`live_auto/session.py`）：
- 新增字段 `self._monitor_task: asyncio.Task | None = None`（仿 `paper/session.py:88` 的 `_replay_task`）。
- `start()` 末尾在订阅行情 WS 后追加 `self._monitor_task = asyncio.create_task(self._monitor_loop())`（仿 `paper/session.py:118`）。
- `_monitor_loop()` 结构（保持单函数 ≤50 行、嵌套 ≤4）：
  ```text
  while self.state == "running":
      await asyncio.sleep(self.poll_interval_s)
      async with self._lock:                       # 与 _on_confirmed 互斥，杜绝并发改 projected
          if self.state != "running": break
          now_ts = int(time.time())                # 盯市墙钟，供 reconcile / 看门狗 / 净值
          mark = await self._latest_mark_px()      # 优先最近确认根 c；缺(None/抛错)则回落(R-4)
          events = await self.executor.reconcile(mark, now_ts)   # H2 后 reconcile 内部已容错
          for e in events: await self._push(e)
          await self._emit_equity(mark, now_ts)    # 净值缓冲有界 deque，不会无界增长(R-3)
          await self._emit_position(mark)
          await self._check_staleness(now_ts)      # H3 / R-5
          await self._maybe_session_exit(mark, now_ts)  # 命中→stop()，循环靠 state 守卫退出(R-2)
  ```
- `stop()` 中取消盯市任务：在停 WS（`session.py:120-121`）旁追加**带自身守卫**的取消：`task = self._monitor_task; if task is not None and task is not asyncio.current_task(): task.cancel()`（吞 `CancelledError`，仿 `paper/session.py:247-248`）。
  - **R-2（P0，必须）**：会话金额止损正是在盯市循环内部经 `_maybe_session_exit → stop()` 触发，此刻 `self._monitor_task` **即当前任务**。若不加 `asyncio.current_task()` 守卫而直接 `cancel()`，等于取消自身，`CancelledError` 会在随后的 `await self.executor.flatten(...)`（`session.py:125`）抛出，从而**中断真实平仓并跳过 `_finalize_run`**，留下无人管理的真实持仓——恰好重演 H4 试图消灭的安全漏洞。加守卫后：循环外触发（用户停止 / shutdown / 收线 `_on_confirmed` 路径）正常取消盯市任务；循环内触发则**绝不自取消**，靠 `_monitor_loop` 的 `while self.state == "running"` 守卫自然收尾退出（循环体顶部 `if self.state != "running": break` 亦会即时跳出）。
- `_latest_mark_px()`：有 `self._candles` 取 `float(self._candles[-1]["c"])`；否则 `await get_rest_client().get_ticker(self.inst_id)`（已在 `session.py` 用到 `get_rest_client`）。**R-4**：`get_ticker` 签名为 `float | None`（`rest_client.py:219-224`，无行情时**返回 `None` 而非抛错**），故对 **`None` 返回与异常一并**回落（末确认根收盘 → `self.executor.entry_price`），绝不把 `None` 标记价传入 reconcile / 净值数学。
- **净值缓冲有界（R-3，必须）**：把会话净值缓冲从裸 `list`（`session.py:69` `self._equity: list[dict]`）改为 `deque(maxlen=_SNAPSHOT_EQUITY_MAX)`，并把 `snapshot()` 的 `self._equity[-_SNAPSHOT_EQUITY_MAX:]`（`session.py:282`）改为 `list(self._equity)`（deque 不支持切片，maxlen 已自动裁顶）。如此盯市循环每 `poll_interval_s` 推一条实时净值（前端瞬时 PnL 跳动），**内存恒定有界**，杜绝多日实盘会话内存泄漏。可选增强：仅每根落一条**历史**净值点、秒级 tick 只更新瞬时值（进一步降事件量），非必须。
- **并发安全**：`_on_confirmed` 已在 `self._lock` 内（`session.py:144`）；盯市循环同样取 `self._lock`，二者天然互斥，`executor.projected` 状态机不会被并发修改。
- **会话级止盈止损时效**：因 `reconcile` 在循环内刷新 `executor.unrealized_usd`（`executor.py:222`），`_maybe_session_exit`（`session.py:181-190`）读 `executor.session_pnl_usd` 即为秒级最新，金额阈值得以及时触发自动平仓 + 停。

> **注**：行情 WS 的 K 线确认根仍是**策略信号唯一驱动源**（`_drive_signals` 仅在 `_on_confirmed` 调用，不放到盯市循环——避免在非收线时刻误触发策略），盯市循环只负责 reconcile / 盯市 / 会话止盈止损 / 推送，职责单一。

### 3.2 H2：reconcile / 盯市读取容错（不污染撮合链路）

`live_auto/executor.py`：
- `reconcile` 内将 `positions = await self._read_positions()` 包入 try/except `OKXError`（**评审复核确认足够**：传输层 `httpx.TransportError`/`HTTPStatusError` 已被 `private_client.py:122-127` / `trade_client.py:42-47` 统一归一为 `OKXPrivateError("OKX_UNREACHABLE"/"OKX_HTTP_ERROR")`，而 `OKXPrivateError` 子类化 `OKXError`，故 `except OKXError` 即可兜住真正的网络抖动，**无需**再加宽泛 `except Exception` 以免吞掉编程错误）：
  - 成功：维持现逻辑（`executor.py:221-229`），并把 `self._reconcile_fail_streak` 归零。
  - 失败：`self._reconcile_fail_streak += 1`；**不**改 `projected`、保留上次 `unrealized_usd`；返回 `[self._status_event("账户/行情连接抖动，本次盯市跳过（不影响已挂交易所侧止盈止损）")]`；当 `_reconcile_fail_streak >= settings.live_auto_reconcile_max_failures`（默认 6，约 30s）追加一条 `error{code:"LIVE_AUTO_RECONCILE_DEGRADED"}` 并置 `degraded=True`，恢复后推一条「已恢复」status。
- 新增构造字段 `self._reconcile_fail_streak: int = 0`、`self.degraded: bool = False`。
- 收益：单次拉取失败不再冒泡进 `feed_candle`→`ws_client`，行情 WS 不再被误判断线重连；用户在持续失败时得到明确降级提示，而非静默。

### 3.3 H3：行情停滞看门狗

`live_auto/session.py`：
- 盯市循环每轮调用 `_check_staleness(now_ts)`：以**墙钟**比对最近一次**确认根**到达时间（新增 `self._last_candle_wall: float`，在 `_append_candle`/`feed_candle` 收到确认根时用 `time.time()` 刷新；`session.py` 已 `import time`）。**R-5**：`_last_candle_wall` 须在 `start()` 订阅 WS 时即以 `time.time()` 初始化——否则暖机（REST 填窗）后、首根**实时 WS 确认根**到来前的最长 `bar_seconds` 窗口会被**误判停滞**；同时 `state != "running"` 或 WS 尚未起的阶段跳过判定。
- 阈值 `stale_after = BAR_SECONDS.get(self.bar,60) * settings.live_auto_staleness_factor`（默认 `3.0`）。超阈：推 `status{state:"running", degraded:True, message:"行情连接异常，自动交易暂停响应K线（交易所侧止盈止损仍生效）"}`（限频，避免每轮刷屏）；恢复后推一条「行情已恢复」。
- **默认不自动平仓**（瞬时抖动平仓危害更大）；提供 `settings.live_auto_flatten_on_stale: bool = False`，仅当运维显式开启且停滞超长（`> stale_after * 4`）时才 `await self.stop("stale_feed")`。
- 文档强调：**每笔交易所侧 OCO（来自策略 `risk_params`）在 bot 停滞/断线/进程退出后仍由 OKX 服务器兜底**（`executor._risk_trigger_px`/`order_builder.build_attach_algo_orders`），停滞期单笔风险仍有界。

### 3.4 H4：停止平仓失败的诚实上报与有界重试

`live_auto/executor.py` `flatten`：
- 平仓失败（非 `LIVE_NO_POSITION`）时**有界重试**：最多 `settings.live_auto_flatten_max_retries`（默认 2）次，每次间隔 `0.5s`；仍失败则返回 `{"ok": False, "code": ..., "message": ..., "residual": {"side": self.projected, "size": self.entry_qty_sz}}`，且**不**把 `projected` 改 flat（如实保留残留仓）。

`live_auto/session.py` `stop`：
- 将「先置 stopped 再 flatten」改为**先 flatten 取结果再据结果置状态**的安全顺序，或保留顺序但据 `flatten_result.ok` 决定后续：
  - flatten 成功：维持现行（推 trade + finalize + status）。
  - flatten 失败：额外推 `error{code:"LIVE_AUTO_FLATTEN_FAILED", message:"自动平仓失败，请立即手动平仓", residual:{...}}`，并在 `_status_payload` 增 `flattenFailed:True` + `residual`；`_finalize_run` 的 metrics 写入 `flattenFailed=True`。
- `manager.stop`/`shutdown`：flatten 失败时**不静默吞**——`shutdown`（`manager.py:104-108`）记录日志且保留可观测（进程退出场景日志为唯一出口）。

### 3.5 H5：start 并发锁

`live_auto/manager.py`：新增 `self._start_lock = asyncio.Lock()`；`start()` 以 `async with self._start_lock:` 包裹「`_require_capacity` → 建 executor/run → `self._session=session` → `session.start()`」关键区，消除 cap=1 的 TOCTOU。`stop`/`status`/`get` 为快速读，无需入锁。

### 3.6 H6：前端补齐 `error` 与 `status.message` 呈现

- `apps/web/lib/store.ts`：新增**瞬态** `liveAutoEvents: LiveAutoEvent[]`（环形缓冲，上限 50）与 `pushLiveAutoEvent(e)`；`clearLiveAutoRuntime`（`store.ts:332-338`）一并清空。
- `apps/web/lib/liveAutoTypes.ts`：`LiveAutoStatus` 增可选 `degraded?`/`flattenFailed?`/`residual?`（**R-6**：`message?`/`stopReason?` **已存在**于 `liveAutoTypes.ts:68-69`，勿重复添加）；**扩充** `AutoStopReason`（`liveAutoTypes.ts:5-10`，现为 `user_stop|session_take_profit|session_stop_loss|shutdown|start_failed`）+`"stale_feed"`；新增 `interface LiveAutoEvent { ts:number; level:"info"|"warn"|"error"; code?:string; message:string }`。
- `apps/web/app/page.tsx` `handleLiveAutoEvent`（:707）：
  - 新增 `if (event.type === "error") { setters.pushLiveAutoEvent({level:"error", ...}); return; }`。
  - `status` 分支：除 `setLiveAutoStatus` 外，若带 `message`/`degraded` 则 `pushLiveAutoEvent({level: degraded?"warn":"info", message})`。
- `apps/web/components/Strategy/AutoTradeEventLog.tsx`（✚）：紧凑活动流（最近 N 条：开/平/反手跳过/降级/OCO 已平/失败），错误红、降级黄、信息灰；置于状态条下方，仅有会话或有事件时显示。**R-1（P1，必须）**：本仓 `apps/web` **无任何既有 toast / 通知原语**（grep `toast|notify|Toast|sonner|notification` 零命中；现有错误呈现范式是就地 `role="alert"` 文本，如 `AutoTradeConfirmModal.tsx:146-150`），故本方案**不依赖也不新建全局 toast 系统**——关键 `error` 一律由 `AutoTradeEventLog` 承载：错误级条目**置顶 + 红色高亮 + 短时强调**（新错误进入时 1–2s 高亮，自然衰减），并与 H7 终止横幅（`flattenFailed` 升红 + 「请到下方实盘面板手动平仓」指引）双重保证可见性，符合「最小且收敛」。

### 3.7 H7：状态条终止态与降级态

`apps/web/components/Strategy/AutoTradeStatusBar.tsx`：
- 读取 `liveAutoStatus.state`：`state==="stopped"` → 渲染**终止横幅**：「自动交易已停止 · {describeStopReason(stopReason)} · 会话盈亏 ±X USDT」，停止脉冲点与计时器（`setNow` 定时器据 state 停），提供「关闭」按钮调用 `clearLiveAutoRuntime`；`flattenFailed` 时横幅升红并附「⚠ 自动平仓失败，请到下方实盘面板手动平仓」。
- `degraded===true` 且仍 running → 在「运行中」旁加黄色「连接异常」徽标。
- 中文 stopReason 映射（`lib/liveAuto.ts` 纯函数 `describeStopReason`）：`user_stop`→「手动停止」、`session_stop_loss`→「触发会话止损」、`session_take_profit`→「触发会话止盈」、`shutdown`→「服务重启」、`stale_feed`→「行情中断保护」、`start_failed`→「启动失败」。

### 3.8 H8：确认弹窗风险敞口打磨

`apps/web/components/Strategy/AutoTradeConfirmModal.tsx`（纯展示增强，不改提交契约）：
- 在金额输入区上方补一行说明：「**止盈/止损为会话累计金额阈值**（达到即自动平仓并停止），留空则不按金额触发；每笔交易另自带策略止盈止损（交易所侧 OCO）。」
- 摘要区补：生效杠杆默认值（来自 `configQ` 或 `LIVE_AUTO_DEFAULT…`）、单笔名义上限「≤1000 USDT/单」。
- config 读取失败（`configQ.isError`，:104）时把「读取失败」改为软提示「可用余额读取失败，预算将由服务端按上限夹紧后生效」，**不**阻断确认（后端 `manager._resolve_budget` 仍会兜底夹紧）。

---

## 四、File / module change plan（文件 / 模块变更计划）

> ✚=新建，✎=修改。所有改动遵循 CLAUDE.md 阈值（单文件 ≤800 行、单函数 ≤50 行、≤5 形参、圈复杂度 ≤10、嵌套 ≤4、命名自解释）。**真实下单写链路（`okx/trading.py`）零改动**，安全护栏与错误码稳定不变。

### 后端 `services/quant/app/`

| 文件 | 操作 | 一句话意图 |
|---|---|---|
| `live_auto/session.py` | ✎ | 新增 `_monitor_loop` 独立盯市任务（H1）+ `_check_staleness` 看门狗（H3，`_last_candle_wall` 于 `start` 初始化·R-5）；`start` 起任务、`stop` **带 `asyncio.current_task()` 守卫**取消任务（R-2）并据 `flatten_result.ok` 诚实置终态（H4）；净值缓冲改有界 `deque(maxlen=_SNAPSHOT_EQUITY_MAX)` + `snapshot` 取 `list(...)`（R-3）；`_status_payload`/`info` 增 `degraded`/`flattenFailed`/`residual`/`stopReason`。 |
| `live_auto/executor.py` | ✎ | `reconcile` 读取容错 + 连续失败计数 + 降级标志（H2）；`flatten` 有界重试 + 失败保留残留仓并结构化返回（H4）；新增 `_reconcile_fail_streak`/`degraded` 字段；`_status_event`/`_error_event` 复用。 |
| `live_auto/manager.py` | ✎ | `start` 加 `asyncio.Lock`（H5）；`shutdown`/`stop` flatten 失败记日志不静默（H4）；`_finalize_run` 路径透传 `flattenFailed`。 |
| `config.py` | ✎ | 新增 `live_auto_staleness_factor: float=3.0`、`live_auto_flatten_on_stale: bool=False`、`live_auto_reconcile_max_failures: int=6`、`live_auto_flatten_max_retries: int=2`；**复用既有** `live_auto_poll_interval_s`（:63，本轮首次真正接线）。 |
| `schemas.py` | ✎ | `LiveAutoSessionRow`/`LiveAutoStatusOut` 增可选 `degraded`/`flattenFailed`（如需透到 `/status` REST）；WS 事件为自由 dict，主要以前端类型对齐。 |

> `api/live_auto.py` 无需改动（事件经既有 WS 透传；`/status` 若加字段则同步 `status_row`）。`db/repositories.py` 的 `finalize_run`（:258）已支持 `metrics` 透传，`flattenFailed` 并入 metrics 即可，**无需新增仓储函数**。

### 前端 `apps/web/`

| 文件 | 操作 | 一句话意图 |
|---|---|---|
| `lib/store.ts` | ✎ | 新增瞬态 `liveAutoEvents`（环形缓冲，上限 50）+ `pushLiveAutoEvent`；并入 `clearLiveAutoRuntime`（H6）。 |
| `lib/liveAutoTypes.ts` | ✎ | `LiveAutoStatus` 增 `degraded?/flattenFailed?/residual?`（`message?/stopReason?` 已存在·R-6）；**扩充** `AutoStopReason`+`stale_feed`；新增 `LiveAutoEvent` 类型（H6/H7）。 |
| `lib/liveAuto.ts` | ✎ | 新增纯函数 `describeStopReason(reason)`（中文映射）与（可选）降级文案助手（H7），供组件复用并单测。 |
| `app/page.tsx` | ✎ | `handleLiveAutoEvent`（:707）新增 `error` 分支 + `status.message`/`degraded` → `pushLiveAutoEvent`（H6）；setters 透传新增方法。 |
| `components/Strategy/AutoTradeStatusBar.tsx` | ✎ | 按 `state` 切终止横幅 + 降级徽标 + flattenFailed 强提示 + 关闭按钮；停计时（H7）。 |
| `components/Strategy/AutoTradeEventLog.tsx` | ✚ | 紧凑「自动交易事件」活动流（开/平/反手跳过/降级/OCO/失败），分级配色（H6）。 |
| `components/Strategy/AutoTradeConfirmModal.tsx` | ✎ | 风险敞口说明（会话金额阈值/单笔自带 OCO/单笔名义上限/生效杠杆）+ config 软失败提示（H8）。 |
| `components/Strategy/StrategyPanel.tsx` | ✎（接线） | 标题栏下方在 `AutoTradeStatusBar` 旁渲染 `AutoTradeEventLog`（仅 `mode==="paper"`，有会话/事件时显示）。 |

### 测试

| 文件 | 操作 | 一句话意图 |
|---|---|---|
| `services/quant/tests/test_live_auto_session.py` | ✎ | 盯市循环在两根之间触发会话金额止损自动平仓（注入桩 reconcile/mark）；行情停滞超阈推 degraded；flatten 失败置 flattenFailed 且保留残留仓（H1/H3/H4）。 |
| `services/quant/tests/test_live_executor.py` | ✎ | reconcile 读取抛错被吞（不抛、推 status）、连续失败达阈推 degraded、恢复清零；flatten 有界重试后失败返回 residual（H2/H4）。 |
| `services/quant/tests/test_live_auto_api.py` | ✎ | 并发两次 start 仅一个成功、另一个 409（锁生效，H5）。 |
| `apps/web/lib/liveAuto.test.ts` | ✎ | `describeStopReason` 全枚举映射；降级/终止文案助手（H7）。 |
| `apps/web/components/Strategy/AutoTradeStatusBar.test.tsx` | ✚ | `state==="stopped"` 渲染终止横幅与中文 reason；`flattenFailed` 升红提示；`degraded` 徽标（`NODE_ENV=test`，H7）。 |
| `apps/web/components/Strategy/AutoTradeEventLog.test.tsx` | ✚ | `error` 事件→红色置顶条目；`status.message`/`degraded`→对应级别条目；环形缓冲达上限裁顶（`NODE_ENV=test`，H6 / **R-8**：补齐 P1 错误上浮链路缺测）。 |

---

## 五、Interface design（接口设计）

REST 端点签名**不变**（`POST /api/live-auto/start`、`POST /api/live-auto/{id}/stop`、`GET /api/live-auto/status`、`WS /api/ws/live-auto/{id}`）。本方案仅**增量扩展事件与状态字段**（向后兼容，旧字段不动）：

- WS `status` 事件 `data` 增可选：`message?: string`、`degraded?: boolean`、`flattenFailed?: boolean`、`residual?: {side, size}`、`stopReason?: string`。
- WS `error` 事件已存在（`{ts, code, message}`，`executor.py:318`），本方案让**前端开始消费**它。
- `GET /live-auto/status` 的 `session` 行可选增 `degraded`/`flattenFailed`（供重载后即时呈现降级/平仓失败态）。
- 新增对外语义码：`LIVE_AUTO_RECONCILE_DEGRADED`（盯市连续失败降级，非致命）、`LIVE_AUTO_FLATTEN_FAILED`（停止平仓失败，需人工介入）。两者经既有错误体 `{code,message}` 推 WS，**不**新增 HTTP 端点。
- 后端→OKX：仍只用既有只读 `fetch_live_positions`/`get_ticker` 与既有写函数，**零新增上游调用**。

---

## 六、Data model（数据模型）

- **配置（`config.py`）**：新增 `live_auto_staleness_factor`(3.0)、`live_auto_flatten_on_stale`(False)、`live_auto_reconcile_max_failures`(6)、`live_auto_flatten_max_retries`(2)；接线既有 `live_auto_poll_interval_s`(5)。
- **会话内存形态（`executor.py`/`session.py`）增量**：`LiveExecutor._reconcile_fail_streak:int`、`LiveExecutor.degraded:bool`；`LiveAutoSession._monitor_task:asyncio.Task|None`、`_last_candle_wall:float`（`start` 初始化·R-5）、`flatten_failed:bool`、`residual:dict|None`；**`_equity` 由 `list` 改 `deque(maxlen=_SNAPSHOT_EQUITY_MAX)`（R-3，杜绝盯市循环下无界增长；`snapshot` 改取 `list(self._equity)`）**。
- **持久化**：仍复用 `runs` 表，`finalize_run`（`repositories.py:258`）的 `metrics` 并入 `flattenFailed`/`degradedSeen`，**无新表、无新仓储函数**。每笔真单仍经既有 `create_live_order_audit` 留痕。
- **前端 store（瞬态，不入 `partialize`）**：新增 `liveAutoEvents: LiveAutoEvent[]`（≤50 环形，并入 `clearLiveAutoRuntime` `store.ts:332-338` 清理）；`LiveAutoStatus` 扩字段如上（仅 `degraded?/flattenFailed?/residual?` 为新增，R-6）。运行期句柄仍不持久化（沿用既有约定）。

---

## 七、Testing & acceptance criteria（测试与验收）

### 7.1 后端单测（pytest，离线，CI 必跑；铁律：占位假凭证，严禁真实凭证入库）
- **H1**：桩注入 `reconcile`/mark，使 `executor.session_pnl_usd` 在两根 K 线之间跌破 `stop_loss_usd`；断言**盯市循环**（非收线）触发 `stop("session_stop_loss")` 并调用 `flatten`；**R-2**：断言此场景下 `flatten` **完整执行**且 `_finalize_run` 落终态（验证循环内停止未因自取消而中断平仓）、`_monitor_task` 干净退出。
- **H2**：令 `positions_fn` 抛 `OKXPrivateError`；断言 `reconcile` 不抛、返回 status 事件、`projected` 不变；连续 ≥`max_failures` 次后 `degraded=True` 并推 `error`；恢复后清零。
- **H3**：模拟 `_last_candle_wall` 陈旧超阈；断言推 `status{degraded:True}`；`flatten_on_stale=False` 时不平仓。
- **H4**：令 `close_position_fn` 持续抛非 `LIVE_NO_POSITION`；断言 `flatten` 重试 `max_retries` 次后返回 `ok=False`+`residual`、`projected` 未被改 flat；`session.stop` 置 `flattenFailed` 并推 `error`。
- **H5**：`asyncio.gather` 两个 `manager.start`，断言恰一个成功、另一个 `LIVE_AUTO_SESSION_LIMIT`(409)，`_session` 唯一。
- **回归**：iteration 1 的 `test_live_executor`/`test_live_auto_api`/`test_live_auto_session` 全绿，安全门禁断言不破。

### 7.2 前端单测（vitest，`NODE_ENV=test`，CI 必跑）
- `liveAuto.test.ts`：`describeStopReason` 覆盖全部枚举（含 `stale_feed`·R-6）+ 未知值回落「已停止」。
- `AutoTradeStatusBar.test.tsx`：`state==="stopped"`+各 `stopReason` 渲染对应中文终止横幅；`flattenFailed` 升红 + 手动平仓指引；`degraded` 黄徽标；点「关闭」调 `clearLiveAutoRuntime`。
- `AutoTradeEventLog.test.tsx`（**R-8**，补齐 P1 错误上浮缺测）：`error` 事件→红色置顶高亮条目；`status.message`/`degraded`→对应级别（warn/info）条目；环形缓冲达上限裁顶。
- 回归：`liveAuto.test.ts`/`AutoTradeToggle.test.tsx` 全绿；`tsc --noEmit` 通过。

### 7.3 真实账户 opt-in 联调（手动/网络标记，不进 CI，守任务硬约束）
1. **资金/标的限制不变**：最多用开发账号 **1%** 资金、**0.01 手 BTC**（`BTC-USDT-SWAP`）、`leverage≤3`、单笔名义上限维持 1000。启动前确认该标的无持仓。
2. **H1 验证**：填一个极小 `stopLossUsd`（如 0.2 USDT），开仓后人为令浮亏跨过阈值，观察**在下一根收线之前**（≤ `poll_interval_s` 量级）自动平仓 + 会话停止 + 状态条显示「触发会话止损」。
3. **H6/H7 验证**：制造一次下单失败（如把预算设到极小使单笔不足 `minSz`，或临时断网），观察前端出现 `error`/降级事件与 toast；会话自动停止后状态条转终止横幅。
4. **H4 验证**（谨慎，仅桩或受控）：优先以单测覆盖；真实环境不刻意制造平仓失败。
5. 联调结束确认**无残留持仓/挂单**；全程绝不对已有持仓标的自动交易。

### 7.4 Definition of Done（本设计交付物）
- 本 `spec.md` 已建并见于 `git status`；含 Overview / 缺陷盘点 / Technical design / File change plan / Interface design / Data model / Testing & acceptance / Risks 全部小节，篇幅 ≥800 字。
- 「文件 / 模块变更计划」逐项可被下游工程师按行实现，无需再决策；每条缺陷均有 file:line 证据与明确落点。

---

## 八、Risks & mitigations（风险与缓解）

| 风险 | 影响 | 缓解 |
|---|---|---|
| **盯市循环与 K 线回调并发改 `projected`** | 状态错乱、重复下单 | 二者均取同一 `self._lock`（`session.py:144` 既有 + 盯市循环新增），天然互斥；盯市循环**不**驱动策略信号，仅 reconcile/盯市/会话止盈止损/推送，职责单一。 |
| **盯市轮询过密 → OKX 限频** | 被风控/限流 | 默认 `poll_interval_s=5`，reconcile 为只读 `get_positions`（与既有人工流轮询同源）；可经 config 调大；连续失败有降级与计数保护（H2）。 |
| **看门狗在瞬时抖动时误判停滞** | 误平仓 | 默认 `flatten_on_stale=False`（仅告警不平仓）；阈值 `3×bar` 起步、自动平仓另需 `>4×` 且显式开关；交易所侧 OCO 在停滞期仍兜底单笔风险。 |
| **flatten 重试期间行情继续不利** | 残留仓亏损扩大 | 重试有界（默认 2 次/0.5s）；失败即高优先级 `error`+UI 强提示人工平仓；交易所侧 OCO 仍在 OKX 生效（除非 `auto_cxl` 已撤——失败路径不撤，OCO 保留兜底）。 |
| **新增 WS/状态字段破坏旧前端** | 兼容性 | 全部为**可选增量字段**，旧字段与事件类型不变；前端按存在性读取，向后兼容。 |
| **会话级金额止损是 bot 侧软控（进程退出即失效）** | 进程死时会话止损不执行 | 明确告知用户：会话金额阈值为 bot 侧控制；**每笔交易所侧 OCO 才是进程无关的硬兜底**；`main.py` lifespan 关停仍会平仓（iteration 1 既有）。文案与弹窗如实说明，不夸大保障。 |
| **改动牵连既有下单写链路** | 回归/护栏失效 | 本方案**不改** `okx/trading.py` 任何写函数与错误码；改动集中在 `live_auto/` 编排层与前端呈现层；回归测试守住既有断言。 |
| **事件流刷屏淹没关键信息** | 交互噪音 | `liveAutoEvents` 环形上限 50；降级/停滞类 status 限频推送；`error` 级条目置顶高亮，info/warn 仅入活动流（**无 toast 依赖**·R-1）。 |
| **盯市循环触发 `stop` 时自取消任务中断 flatten**（R-2） | 残留真实持仓无人管理（重演 H4 漏洞） | `stop()` 取消盯市任务前以 `asyncio.current_task()` 守卫排除自身；循环内触发的停止靠 `while self.state=="running"` 自然退出，真实平仓与 `_finalize_run` 完整执行。 |
| **盯市循环持 `self._lock` 内做网络读**（R-7） | 子分钟周期下短暂推迟收线信号 | 与既有 `_on_confirmed`（同样持锁内 reconcile，`session.py:144→160`）口径一致，非新增回归；网络读超时有界（≤~15s）、信号按根触发；列为**已知可接受取舍**，子分钟周期若成主用法再评估锁外预取。 |
| **净值缓冲随盯市轮询无界增长**（R-3） | 多日实盘进程内存泄漏 | `_equity` 改有界 `deque(maxlen=_SNAPSHOT_EQUITY_MAX)`，`snapshot` 取 `list(...)`，内存恒定有界。 |

---

> 版本：**v2**（iteration 2 · Subtask #1 方案评审与修复）· 基线：对照已入库实代码（commit `f0d44a7`）逐项复核。v1 定位的 8 项加固点（H1 P0 / H2-H4·H6·H7 P1 / H5·H8 P2）经复核证据全部属实；本节点另发现并就地修复 8 项遗漏（R-1…R-8，含 1 项 P0：盯市循环自取消中断 flatten）。下一节点（Subtask #2 代码开发）请严格按 v2 正文「§三 / §四 文件 / 模块变更计划」逐项落代码。

---

## 评审结论（Review Verdict）

**有条件通过（Approved with Conditions）。**

本方案目标清晰、与全局目标「实盘交易正常可靠稳健 + 人机交互最佳实践」高度对齐，遵循「最小且收敛、不碰下单写链路与安全护栏」的正确原则；v1 对 H1–H8 的缺陷判定与 `file:line` 证据经评审节点逐行复核**全部属实、定位准确**，且修复落点明确、可被下游按行实现。评审维度结论：

- **可行性**：除 R-1（依赖不存在的 toast 原语）、R-2（自取消中断 flatten）、R-4（`get_ticker` 可返回 `None`）三处外，其余设计均可在现有技术栈直接落地；上述三处已在 v2 就地修正，修正后**全部可行**。
- **完整性**：补齐了 R-3（净值缓冲无界增长）、R-5（看门狗初值/暖机窗误判）、R-8（P1 错误上浮链路缺测）三处边界与测试缺口。
- **一致性**：与 CLAUDE.md 阈值（单函数 ≤50 行、嵌套 ≤4、显式错误传播、修 bug 先补复现测试）一致；澄清了 R-6（`LiveAutoStatus.message?/stopReason?` 已存在、`AutoStopReason` 需扩 `stale_feed`）以免实现者类型churn。
- **规模合理性**：H3 看门狗、R-7 锁内网络读等均为有界、默认保守（`flatten_on_stale=False`），无过度工程化；R-7 列为已知可接受取舍。

**所有 P0 / P1 问题（R-1、R-2、R-3、R-8 及 v1 既有 H1–H7）均已在 v2 正文就地修复，无残留。** 放行下游的**约束条件**（须在 Subtask #2 实现时强制遵守，否则视为未达 DoD）：

1. **（P0·R-2）** `stop()` 取消盯市任务**必须**带 `asyncio.current_task()` 自身守卫；并以单测断言「会话止损在盯市循环内触发时，`flatten` 完整执行 + `_finalize_run` 落终态」。
2. **（P1·R-1）** **不得**新建/引入全局 toast 系统；关键 `error` 仅经 `AutoTradeEventLog`（置顶高亮）+ H7 终止横幅呈现。
3. **（P1·R-3）** 会话净值缓冲**必须**为有界 `deque(maxlen)`，`snapshot` 改取 `list(...)`，禁止裸 `list` 无界 append。
4. **（P1·R-8）** **必须**补 `AutoTradeEventLog` 错误上浮单测（`NODE_ENV=test`）。
5. **（硬约束·不变）** 真实账户联调严守任务铁律：最多 **1%** 资金、**0.01 手 BTC**、占位假凭证入测，下单写链路（`okx/trading.py`）零改动，双闸守门 / 预算夹紧 / 单会话独占 / 无既有持仓门禁等安全契约保持不变。

P2 项（R-4 / R-5 / R-6 / R-7）已在正文一并收口或标注为可接受取舍，建议在实现时顺手落实，不作为放行阻断条件。

> 评审人：senior reviewer（iteration 2 · Subtask #1）· 复核对象：工作树 commit `f0d44a7` 后当前代码 · 复核方式：逐节、逐 `file:line` 核验源码（后端 `live_auto/*`、`config.py`、`okx/{ws_client,rest_client,private_client,trade_client,trading}.py`、`db/repositories.py`；前端 `page.tsx`、`lib/{store,liveAuto,liveAutoTypes}.ts`、`components/Strategy/*`）。
