# 韧性与人机交互加固（resilience-and-hci-hardening）· 方案设计 v2

> 轮次：loop `tge-2add488c` 第 2/3 轮 · 节点 #0（方案设计）→ 节点 #1（设计评审，本版）
> 上一轮产出：`docs/plans/stability-hardening-audit/spec.md`（v2），已由 `a8ed650` 合入（27 文件）。
> 本轮**不复用**上一轮的任何缺陷编号，全部为重新审计得到的、上一轮未覆盖的新缺陷。
> **v2 变更**：评审节点逐条复核了 v1 的全部取证，给出 16 条判据（1 P0 / 9 P1 / 6 P2，见 §评审记录）；
> P0 与 P1 已就地改进文档正文；D4 的封顶值、UI 落点、文件计划与门禁判据均有实质修改。

---

## 评审记录（Review Notes · v2）

评审姿态：**不采信 v1 的任何转述**——逐个文件重读了被引用的每一行，重跑了 ruff（`--select F,E9` → 17 条，
与 v1 一致）、desktop `node --test`（本地 27 passed）、以及 WCAG 相对亮度公式（v1 的 6 个对比度数字**全部复算一致**）。
**没有一条缺陷判断被推翻**：D1–D9 描述的现象逐条属实。以下 16 条是「照 v1 实施会出问题」的地方。

| # | 级别 | 缺陷 | 结论 |
|---|---|---|---|
| R-1 | **P0** | D4 的前端封顶会**静默改写已展示的交易统计**：`aiTradeStats/AiTradeStatsLive.tsx:108-134` 的 `fullSummary` 与 `capture` 直接由 `member.trades` 求和，源码注释明写「**未筛选全会话**总盘」；`Chart/AutoTradePnlPanel.tsx:49-61` 的 `buildLedger(members)` 同理。把 `trades` 截到 300，这两处的「全会话收益 / 盈利捕获率 / 年化」在超过 300 笔的会话里**低报且无任何提示**——正是 v1 自己红线表里「统计低报」那一格，只是发生在 wire 的另一侧 | 已修：封顶值由 300 改为 **2000**（安全网而非展示策略），并要求截断时两处口径显式标注；见 §三 D4 |
| R-2 | **P1** | D4 漏掉**第四处同形无界缓冲**：`store.ts:394` `liveAutoTrades: Trade[]` + `:814-820` `appendLiveAutoTrade`（同样的 `some()` 去重 + `[...prev, trade]`），而它正是唯一渲染成交明细的 `Strategy/AutoTradeTradesTable.tsx:31` 的数据源 | 已修：并入 §三 D4 与文件计划 |
| R-3 | **P1** | D4 的「UI 如实告知」与新增 wire 字段 `tradesTotal` 在 §七 **没有任何落点文件**——本仓已两次栽在「有写点没读点 / 算了但看不见」上，v1 等于第三次 | 已修：删掉 `tradesTotal`（无读点的 wire 字段即死码），改用两处**本地可判定**的截断提示，并把落点文件写进 §七 |
| R-4 | **P1** | D6 的 `initialFocusRef` **挂不上去**：`ui/index.tsx:9-36` 的 `Button` 是普通函数组件（非 `forwardRef`），React 18.3 下 `ref` 不是合法 prop，`tsc --noEmit` 会直接红（G1 挂） | 已修：D6 增加「先把 `Button` 改成 `React.forwardRef`」这一前置步骤 |
| R-5 | **P1** | D2 的兜底 **自己会抛**：catch 里裸调 `paths.ensureLogsDir()`（会建目录、可失败），而 D2 自己列举的首要失败模式就是「`openLogStreams()` 建日志目录失败」——那时兜底跟着抛 ⇒ 回到未处理拒绝 ⇒ 仍然静默消失 | 已修：改用无副作用的 `paths.logsDir()`，并把整个 catch 体包进 try |
| R-6 | **P1** | D1 的 `asyncio.wait_for` **不是硬死线**：Py3.11 的 `wait_for` 超时后会 `cancel` 并**继续 await** 那个步骤，一个吞掉取消的步骤照样能把关停链永久堵死——这正是 D1 要消灭的失败模式。同文件 `_cancel_background` 用的是 `asyncio.wait(..., timeout=)`，同一份纪律在同一个文件里不该有两种实现 | 已修：改为 `create_task` + `asyncio.wait` 两段式硬死线 |
| R-7 | **P1** | D3 的「7 条 WS 路由」计数错误（实为 **9** 条），且 `ws_market` 被无声略过。**它必须被显式排除而不是被忘记**：那条路由上「静默即证据」，给它加 ping 会把上一轮刚引入的 45s 看门狗**致盲**——行情停了却永远不重连，比现状更糟 | 已修：D3 补 9 条路由的完整分类 + `ws_market` 的显式排除理由 + 3 条会话流加 ping 后的补偿信号说明 |
| R-8 | **P1** | D5 选定的取值**在它自己设计的配对上不达 AA**：实测 `--warn #9a5a0d` on `--warn-soft #f6e6cf` = **4.46**（< 4.5），而 §九 R8 明确要求警告面一律用 `--warn-soft` 打底 | 已修：light `--warn` 改为 `#8f5209`（5.71 / 6.16 / 5.42，配 soft **5.07**） |
| R-9 | **P1** | G8「干净检出复核 → 与 G5 同结果」**不可能成立**：本地 `npm test` 是 27 passed，干净检出只有 19（差额来自**未跟踪**的 `desktop/scripts/ensure-wincodesign.test.mjs`），与 DoD-4「别人的未跟踪文件必须留在未跟踪」直接冲突 | 已修：G8 判据改为「干净检出基线 19 + 本轮新增」，并写明差额来源 |
| R-10 | **P1** | §七 文件计数错误：表头写「26 个文件（新建 8 / 修改 18）」，表体实际 38 行；DoD-1 的「26 + 13」同错。而 DoD-1 要求实施节点**逐行核对本表** | 已修：按 v2 最终表重算为 **47 + 16** |
| R-11 | P2 | D3 声称 agent 改动「**不引入并发发送**」不成立：心跳发自接收循环，而 `emit` 发自 run 任务；且 `agent/loop.py:202-207` 的 run 期心跳（`agent_heartbeat_seconds=20.0`）与之重复 | 已修：更正表述，说明本仓两处路由早已是多任务并发发送、以及为何仍可接受 |
| R-12 | P2 | D4 的 paper 侧真值列**不是** `executor.trades` 而是 `self.broker.trades`（`paper/session.py:395-396`）；照 v1 写会得到 `AttributeError` | 已修：§三 D4 与红线表指名 |
| R-13 | P2 | D9 的 ruff 触及面是 **14** 个测试文件（不是 13）+ 2 个应用文件；本轮已把 17 条的完整清单贴进 §三 D9，实施时不必再猜 | 已修 |
| R-14 | P2 | G4 的 `ruff check .` 会连**未跟踪的在制文件**一起扫（今天恰好 0 条 F/E9，所以能绿），但判据里没写这条口径，一旦别人的在制文件带进一条 F401，实施者要么改别人的文件（违反 DoD-4）要么卡死 | 已修：G4 判据补口径说明 |
| R-15 | P2 | D3 的用例落点没说清：本仓 **WS 用例禁用 `TestClient`**，既定手法是 `_FakeWS` + 直接 `await` 路由协程（`tests/test_weight_train_ws.py:228/261` 有现成的 monkeypatch 与假 WS）；且三条会话流路由会先 `manager.get(sid)`，用例必须先往管理器注册表注入假会话 | 已修：写进 §三 D3 与 §八 D3 验收 |
| R-16 | P2 | 前后端两个封顶常量是「两地各写一个数字」，下一次调参必然悄悄分叉 | 已修：新增 `wireCaps.source.test.ts` 把两个数字钉在一起 |

**未推翻、也不再追加要求的判断**（复核记录，供实施节点省一次重读）：
`main.py:69-83` 确为 12 条裸 await 且顺序与 v1 步骤表逐字一致；`main.js:627` 确无 `.catch()`、`:645` 的
`uncaughtException` 钩子确实通向 `app.exit(1)`；`ws.ts:13` 的 45s 看门狗确实**无任何调用方覆盖**（9 个
`new ReconnectingWS(` 调用点全部只传 3 个实参）；6 处 `--warn/--warning` 调用点与行号逐条属实，
`globals.css:9-36` 确无 warn token；`ConfirmDialog.tsx:43` 的 `autoFocus` 与 `Modal` 的 `:272-273`
首可聚焦元素规则确实互相覆盖；`FactorSetList.tsx:50` 确为全仓最后一处 `window.confirm`，
`FactorSetList.test.tsx:91-124` 的两个用例确实靠 `vi.spyOn(window,"confirm")` 驱动；`Tabs` 的 11 个调用点
与 v1 列举**一字不差**；`app/` 下确有 9 个 > 800 行文件（`schemas.py` 2494）。

---

## 一、概览（Overview）

本项目（Aragon Trading）是一套「Next.js 前端 + FastAPI 后端 + Electron 桌面外壳」的量化交易工作台，
真实对接 OKX 行情与下单，最核心的使用形态是**长时间无人值守运行**：AI 自动交易分组可以连跑 24 小时，
挖掘任务可以连跑一整天，桌面端窗口经常一开就是一整周。这决定了「稳健」在本项目里的判据不是
「功能跑得通」，而是**「跑一整天之后仍然跑得对、看得懂、退得干净」**。

上一轮（stability-hardening-audit）把两条红着的质量门禁扳绿，并补上了渲染进程崩溃自愈、根布局兜底、
后台协程有界收敛这几处「从来没有过的兜底」。本轮沿同一条思路继续，但换了三个审计视角，因此挖到的
是完全不同的一批问题：**（1）关停与启动这两个「只在异常时才走」的路径上，错误传播是断的；
（2）几条实时流在「什么都没发生」的空闲期是不可探测的，而客户端上一轮引入的 45s 静默看门狗会把
这种空闲误判成断连；（3）设计系统里有一个语义色根本不存在，六处调用各自用不同的硬编码兜底，
其中最常见的那个值在浅色主题下对比度只有 2.78–3.16:1，低于 WCAG AA 的 4.5:1。**

本方案是一次**纯加固**：不新增任何用户可见功能，不改任何下单 / 撮合 / 风控的判定逻辑，不做 DB 迁移，
不改任何既有 wire 字段的语义（只做加法）。目标是把上面三类问题按「有证据、可复现、可冻结」的标准
逐条修掉，并为每一条补一个会在未来回归时变红的用例——本仓库反复出现的教训是，
**没有门禁守着的修复，会在三个月后以另一个名字复发**。

全部 9 条缺陷均由本轮实跑门禁 + 逐行读码取证，取证命令与实测输出见 §二。

---

## 二、审计方法与实测证据（本轮真跑，不采信上游报告）

| 门禁 | 命令 | 实测结果 |
|---|---|---|
| 前端类型 | `cd apps/web && npx tsc --noEmit` | **exit 0**（上一轮修复保持有效） |
| 后端全量 | `cd services/quant && .venv/Scripts/python -u -m pytest -q` | **7 failed, 3629 passed, 5 skipped, 2 deselected, 798.06s** |
| 后端 lint | `.venv/Scripts/python -m ruff check .`（本轮临时装 ruff 0.16.3 测量） | 默认集 **445 条**；精选集 `F,E9` **17 条**（15 条可自动修） |
| 源码尺寸 | 自写脚本统计 | `app/` 下 9 个文件 > 800 行（CLAUDE.md 阈值），最大 `schemas.py` 2494 行 |

**关于那 7 条红**：全部来自 `services/quant/tests/test_factor_library_expansion.py`——这是一个
**未被 git 跟踪的在制文件**（`git status` 中处于 `??`），CI 检出里根本不存在。CI 视角的真实基线因此是
**3629 passed / 5 skipped / 0 failed**，与上一轮终审记录的 3630 一致（差 1 为本轮另一未跟踪文件带入）。
本轮**不得**修改该文件，也**不得**把它计入基线。

**关于工作树**：`git status --porcelain --untracked-files=no` 为空——已跟踪文件全部干净，
本轮所有改动都是从 `a8ed650` 起算的净增量。

**评审节点的独立复测（v2 追加，用于把基线钉成可复算的数字）**：

| 门禁 | 命令 | 评审实测 |
|---|---|---|
| 后端 lint | `services/quant/.venv/Scripts/ruff.exe check . --select "F,E9" --output-format concise` | **17 errors，15 fixable**（与 v1 一致；完整清单见 §三 D9） |
| 桌面单测 | `cd desktop; node --test "scripts/*.test.mjs"`（Node v22.18.0） | **27 pass / 0 fail**——其中 8 条来自**未跟踪**的 `scripts/ensure-wincodesign.test.mjs`，故**干净检出只有 19**（G8 判据依此改写） |
| 对比度 | WCAG 2.1 相对亮度公式复算 | v1 的 6 个数字全部一致；但**新发现** `--warn` 配 `--warn-soft` 只有 4.46（见 D5） |

pytest 全量（798s）在评审节点**未重跑**——v1 的取证已足够具体（7 条红全部指名到未跟踪文件），
且评审的价值在判据而不在复读一次 13 分钟的输出。**实施节点必须自己跑一遍 G3**，这是它的门禁不是本节点的。

---

## 三、缺陷清单（9 条，含逐行取证与修法）

### P1-D1 · 后端关停链没有异常隔离，一步失败会吃掉后面全部清理

**取证**：`services/quant/app/main.py:69-83`。`lifespan` 的 `yield` 之后是 12 条**首尾相连的裸 await**：

```
_cancel_background → health_guard → agent → discovery → paper → ai_trade
→ ai_backtest → live_auto → weight_batch → weight_train
→ rest_client.aclose() → client_pool.aclose_all() → dispose_engine()
```

其中任何一步抛异常，后面全部**不会执行**。而 `get_ai_trade_manager().shutdown()`
（`app/ai_auto/manager.py:1157`）在 `ai_trade_shutdown_action == "flatten"` 时会**下真实平仓单**——
这是一个网络动作，既可能抛也可能长时间挂住，且**整条链上没有任何死线**。后果按严重度排列：

1. `dispose_engine()` 不执行 ⇒ SQLite 引擎与其连接池不释放（Windows 上文件句柄悬挂，
   桌面端下次启动的 `init_db` 可能撞锁）；
2. `get_rest_client().aclose()` / `get_client_pool().aclose_all()` 不执行 ⇒ httpx 连接泄漏；
3. 若 `ai_trade` 那步挂住，整个进程**永不退出**——桌面端 `pm.stop()` 8s 宽限后 `taskkill /F /T` 强杀，
   于是「优雅关停」在最需要它的那一次彻底不发生。

上一轮的 D4 只解决了**后台协程取消**的有界收敛（`_cancel_background`），没有碰这 12 步本身。

**修法**（`main.py`）：把关停链改成**数据驱动的步骤表** + 一个逐步隔离的执行器。

```python
def _shutdown_steps() -> tuple[tuple[str, Callable[[], Awaitable[object]]], ...]:
    """关停步骤表（顺序逐字保持既有 `main.py:71-83`）。

    **每一项都是零参 lambda**：单例 getter 与它返回的协程一起在 `_run_shutdown` 的 `try` 内求值，
    这样「getter 自己抛」也只损失那一步，不会把整张表连根拔掉（[R-6]）。
    """
    return (
        ("health_guard", lambda: get_health_guard().shutdown()),
        ("agent", lambda: get_agent_manager().shutdown()),
        ("discovery", lambda: get_discovery_manager().shutdown()),
        ("paper", lambda: get_paper_manager().shutdown()),
        ("ai_trade", lambda: get_ai_trade_manager().shutdown()),
        ("ai_backtest", lambda: get_ai_backtest_manager().shutdown()),
        ("live_auto", lambda: get_live_auto_manager().shutdown()),
        ("weight_batch", lambda: get_weight_batch_manager().shutdown()),
        ("weight_train", lambda: get_weight_train_manager().shutdown()),
        ("okx_rest", lambda: get_rest_client().aclose()),
        ("okx_pool", lambda: get_client_pool().aclose_all()),
        ("db_engine", lambda: dispose_engine()),
    )


async def _run_shutdown(steps: tuple[tuple[str, Callable[[], Awaitable[object]]], ...],
                        *, timeout: float) -> None:
    """逐步执行关停，**任何一步的失败或超时都不得影响后续步骤**。

    死线是硬性的：一个吞掉取消或卡在网络上的关停步骤，绝不能反过来让引擎与连接池永远不被释放。
    **不用 `asyncio.wait_for`**——它超时后会 `cancel` 并**继续 await** 那个步骤，于是一个吞掉
    `CancelledError` 的步骤照样能把关停链永久堵死，正是本条要消灭的失败模式（[R-6]）。姿态与同文件
    `_cancel_background` 逐字一致：`asyncio.wait` 拿到硬边界，超时就 cancel + 再给一个短暂的收尾窗口，
    收不干净就**抛下它继续往下走**——「没停干净」是要被看见的事实，不是要被制造的异常。
    """
    for name, factory in steps:
        try:
            task = asyncio.ensure_future(factory())
        except Exception as exc:  # noqa: BLE001 —— 连 getter/工厂本身抛都不得吃掉后续步骤
            logger.warning("shutdown step %s could not start: %s; continuing", name, exc)
            continue
        done, _ = await asyncio.wait({task}, timeout=timeout)
        if not done:
            task.cancel()
            await asyncio.wait({task}, timeout=_SHUTDOWN_CANCEL_GRACE_S)   # 给一次收尾机会，不等第二遍
            logger.warning("shutdown step %s did not settle in %.1fs; continuing", name, timeout)
            continue
        if not task.cancelled() and task.exception() is not None:
            logger.warning("shutdown step %s failed: %r; continuing", name, task.exception())
```

**为什么工厂调用也包在 try 里**：`_shutdown_steps()` 里的 `get_xxx_manager()` 是在**构表那一刻**求值的，
若某个 getter 自己抛，整张表都建不出来，12 步一步都不会执行——隔离器必须把这一段也罩住。
故步骤表存的是**零参 lambda**（`("ai_trade", lambda: get_ai_trade_manager().shutdown())`），
getter 与被调用的协程一起落在 `try` 内。

`lifespan` 的 `yield` 之后收敛为两行：

```python
    yield
    await _cancel_background(background)
    await _run_shutdown(_shutdown_steps(), timeout=get_settings().shutdown_step_timeout_s)
```

**新增配置**（`app/config.py`，紧邻 `live_auto_poll_interval_s`（`:113`）所在的运行期参数段）：

```python
    # 关停链单步收敛死线（秒）。超时即取消该步并继续——绝不让一步挂死拖住引擎释放。
    shutdown_step_timeout_s: float = 20.0
```

**并在 `main.py` 追加模块级常量**（与既有 `_BACKGROUND_SETTLE_TIMEOUT_S` 并列）：

```python
_SHUTDOWN_CANCEL_GRACE_S = 1.0   # 超时步被 cancel 后的收尾窗口；收不干净就抛下它继续
```

**为什么死线是配置而收尾窗口是常量**：前者是用户可能要按部署环境调的策略（慢盘 / 慢盘符），
后者是「给协程一次跑完 `finally` 的机会」这一实现细节，不构成可调策略——配置项越少越好。

**为什么超时可以取消资金相关的那两步**：`ai_trade_shutdown_action` 的**默认值是 `"hold"`**
（`manager.py:1176`，只写 DB 不下单），20s 绰绰有余；只有用户显式改成 `"flatten"` 才会下真单，
而那条路径本身在 §五风险表里已有交易所侧兜底与启动对账两道网。相比「无界挂起 ⇒ 进程不退出 ⇒
被 `/F` 强杀 ⇒ 什么都没做成」，有界取消是严格更优的权衡。

---

### P1-D2 · 桌面端 `bootstrap()` 没有 catch：启动期任何异常都表现为「双击后什么都没发生」

**取证**：`desktop/src/main.js:627` —— `app.whenReady().then(bootstrap);`，**没有 `.catch()`**。
`bootstrap()`（`main.js:523-557`）内部对**它自己预料到的**两种失败都有对话框（venv 缺失 → `showErrorBox`；
端口不可用 → `ensurePortsAvailable` 弹窗后 `app.quit()`），但对**预料之外**的异常一个兜底都没有：
`openLogStreams()` 建日志目录失败、`installAppMenu()` 抛、`createWindow()` 抛、`startEngines()` 拒绝……
都会变成一个未处理的 Promise 拒绝。Node ≥15 的默认策略是把它抬成 `uncaughtException`，
于是命中 `main.js:645` 的钩子 → `gracefulQuit().finally(() => app.exit(1))`
（上一轮终审已在本仓库确认过这条链路：`main.js` 未 catch 的拒绝 = `app.exit(1)`）。

**用户看到的**：双击图标，窗口闪一下（或干脆不出现），进程消失，没有任何提示，日志写在
`%APPDATA%/aragon-desktop/logs/main.log` 里而用户并不知道它存在。对一个「顶级产品」而言，
**静默消失是最差的失败模式**——它甚至不如崩溃，因为崩溃至少会被操作系统报告。

**修法**：

1. 新建纯函数模块 `desktop/src/lib/startup-failure.js`（镜像上一轮 `renderer-health.js` 的手法：
   纯函数 + 可被 `node --test` 覆盖，不碰 Electron API）：

```js
'use strict';
/** 启动失败的用户可读描述（纯函数，便于用例冻结文案契约）。 */
function describeStartupFailure(err, logDir) {
  const detail = err && err.stack ? String(err.stack) : String(err);
  return {
    title: 'Aragon Trading 启动失败',
    body:
      '应用在启动过程中遇到无法恢复的错误，已安全退出。\n\n' +
      `错误详情：\n${detail.slice(0, 800)}\n\n` +
      `完整日志：${logDir}\n` +
      '常见原因：端口被占用、Python 后端依赖缺失（请在仓库根执行 `pnpm setup`）、磁盘只读。',
  };
}
module.exports = { describeStartupFailure };
```

2. `main.js:627` 改为（**v2 [R-5] 关键修正**）：

```js
  app.whenReady().then(bootstrap).catch((err) => {
    try {
      logMain(`bootstrap failed: ${err && err.stack ? err.stack : err}`);
      // `logsDir()` 是纯路径拼接；**绝不在这里调 `ensureLogsDir()`**——它会建目录、会失败，
      // 而「建日志目录失败」恰恰是本条要兜的头号失败模式，兜底自己抛等于什么都没做。
      const { title, body } = describeStartupFailure(err, paths.logsDir());
      dialog.showErrorBox(title, body);
    } catch (inner) {
      // 极早期（GUI 未就绪 / 磁盘只读）连日志和对话框都不可用：那就只剩退出这一件事可做。
      try { process.stderr.write(`[main] startup failure handler failed: ${inner}\n`); } catch { /* 无处可写 */ }
    }
    gracefulQuit().finally(() => app.exit(1));
  });
```

   顶部按既有风格追加 `const { describeStartupFailure } = require('./lib/startup-failure');`。
   （`paths.logsDir` 与 `paths.ensureLogsDir` 都已在 `desktop/src/lib/paths.js` 导出，本轮不改 paths。）

3. `desktop/package.json` 追加静态语法门禁 `"check:startupfailure": "node --check src/lib/startup-failure.js"`
   （`check` 是 `run-s check:*`，加一行即自动进门禁）。

4. 新增 `desktop/scripts/startup-failure.test.mjs`：断言标题固定、body 含日志目录、含 `pnpm setup` 指引、
   对 `Error` 与非 `Error`（字符串 / undefined）两类入参都不抛、超长 stack 被截断到 800 字符内。
   **导入姿势照抄 `desktop/scripts/renderer-health.test.mjs:11-16`**：`createRequire(import.meta.url)`
   加载 CJS 模块（本仓既定手法，别用 `import ... from`）。

**注意**：**整个 catch 体**（不只是 `showErrorBox`）都包在 try 里。若失败发生在极早期，对话框 API
可能不可用，那时**唯一**该做的事是把能写的写下去然后退出，绝不能让兜底自己抛。
（上一轮终审刚修过同形问题：崩溃对话框的 Promise 没 catch，兜底自己会杀掉它正要救的会话。
v1 把 `try` 只圈在 `showErrorBox` 上，恰好漏掉了它上一行那个会建目录的调用——同一个坑的第三次。）

---

### P1-D3 · 空闲期的实时流不可探测：客户端 45s 静默看门狗 + 服务端无空闲心跳 = 每 46 秒一次假断连

**取证（这是两处正确的改动叠加出来的缺陷，任何一处单独看都没问题）**：

- 客户端：`apps/web/lib/ws.ts:13` `DEFAULT_STALE_MS = 45_000`，`armStaleWatchdog()` 在 45s 内没收到
  **任何**入站帧就主动 `close()` 触发重连。全仓**没有任何调用方**覆盖 `staleMs`
  （评审复核：9 个 `new ReconnectingWS(` 调用点全部只传 3 个实参）。
- 服务端：**9** 条 WS 路由分成三类（v1 写「7 条」是漏数，[R-7]）——
  - **有**空闲保活的 4 条：`/ws/ai-backtest/{jobId}`（`api/ai_auto.py:763`）、`/ws/discovery/{runId}`
    （`api/discovery.py:182`）、`/ws/weight-train/{jobId}`（`api/weight_models.py:160`）、
    `/ws/weight-batch/{batchId}`（`api/weight_batches.py:129`），都用
    `asyncio.wait_for(queue.get(), timeout=_WS_HEARTBEAT_SECONDS)`（20.0s）并在超时时下发 `{"type":"ping"}`；
  - **没有**空闲保活、且**本轮要补**的 4 条：`/ws/ai-trade/{sid}`（`api/ai_auto.py:673` 裸 `await queue.get()`）、
    `/ws/live-auto/{sid}`（`api/live_auto.py:183`）、`/ws/paper/{sid}`（`api/paper.py:180`）、
    `/ws/agent/{convId}`（`api/agent.py:221` 裸 `await websocket.receive_text()`）；
  - **没有空闲保活、且本轮明确不补**的 1 条：`/ws/market`（`api/ws_market.py:92-96`，只有一个 30s 的
    `wait_for(receive_text())` 空转，超时后 `continue`，**不发任何帧**）。理由见下方「为什么 `ws_market`
    必须保持沉默」——这不是遗漏，是设计判断。

**最严重、且可当场复现的是 agent 流**：`app/agent/loop.py:263` 的心跳任务是**run 作用域**的
（`run()` 开始时 `create_task`，`finally` 里 `_await_heartbeat_stop()` 回收）。也就是说
**会话空闲时没有任何心跳**。于是「Agent 面板开着、用户在读上一条回答」这个最常见的状态下：

```
t=0s    连接建立，服务端发 ready + history（整表）
t=45s   客户端看门狗认定静默 → ws.close()
        → onConnState(false) → 连接指示灯变「断开」，AgentPanel 置 running=false
t≈46s   自动重连 → 服务端 attach() + 再发一次 ready + 一次 history（整表）
        → agentReducer 的 `case "history"` 走 buildHistory/reconcileHistory：消息列表整体重建
t=91s   重复上一轮 …… 只要面板开着就永不停止
```

每一次循环在服务端是一次 `manager.attach()/detach()`（`detach` 会启动 in-flight run 的宽限窗口机制）
外加一次**整段会话历史的 DB 读**；在客户端是一次连接指示灯闪断 + 一次整表重建（工具调用展开态、
滚动锚点等一切与消息身份绑定的局部 UI 状态被重置）。

**另外 3 条流**（ai-trade / live-auto / paper）今天被掩盖了，但掩盖它的是巧合而不是设计：
ai-trade 与 live-auto 靠 `live_auto_poll_interval_s = 5`（`config.py:113`）的盯市 tick 每 5s 推一次
`equity`/`position`；paper 靠 OKX 行情 WS 的未确认 K 线推送。**一旦盯市循环因会话终态而停止、
或 OKX 行情断流，这三条流立刻退化成与 agent 流完全相同的 46 秒 churn**——而「行情断了」恰恰是
最需要连接保持稳定、以便把降级状态如实显示给用户的时刻。

**修法**（服务端补齐，客户端零改动）：

1. `api/paper.py` / `api/live_auto.py` 顶部各加常量（与既有 4 条逐字一致）：
   `_WS_HEARTBEAT_SECONDS = 20.0   # 静默此秒数后下发 ping（半开探测，镜像 ws_weight_train）`
   `api/ai_auto.py` 已有该常量（`:76`），直接复用。
2. 三条 `while True: event = await queue.get()` 统一改为：

```python
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=_WS_HEARTBEAT_SECONDS)
            except asyncio.TimeoutError:
                await websocket.send_text(json.dumps({"type": "ping"}))
                continue
            await websocket.send_text(json.dumps(event))
```

   （**不**照搬 job 流的「终态即补发并关闭」分支：会话流的终态语义由 `status` 事件承载，
   由前端决定何时收连接；擅自在服务端关连接会打断「会话已停止但用户还在看成交明细」这个正常场景。）

3. `api/agent.py:220-222` 的接收循环改为**带超时的接收**（沿用 `ws_market.py:94` 已有的同款写法，
   不引入并发发送）：

```python
        while True:
            try:
                raw = await asyncio.wait_for(websocket.receive_text(), timeout=_WS_IDLE_SECONDS)
            except asyncio.TimeoutError:
                await _send(websocket, "heartbeat", {})   # 纯活性帧，无 seq（RS-7/RN-2 契约）
                continue
            await _dispatch(raw, conversation_id, manager, sess, emit, websocket)
```

   新增常量 `_WS_IDLE_SECONDS = 20.0`。**帧类型选 `heartbeat` 而不是 `ping`**：`AgentPanel.tsx:198`
   已有 `case "heartbeat": break`（且注释明确写着「不入消息流、不推进 lastSeq」），复用它意味着
   **前端一行不改**，也不会污染 `lastSeq` 对账锚点。

   **[R-11] 关于并发发送**：v1 说这条改动「不引入并发发送」是**不准确**的——心跳发自**接收循环任务**，
   而 `emit` 发自 **run 任务**，两者确实会并发调用 `_send_raw`。但这不是本条引入的新姿态：
   ① 同一路由今天就有并发发送（`_handle_user_message` 的 BUSY `error` 帧发自接收循环，而按其触发条件
   `sess.running` 为真，彼时 run 任务正在 emit）；② `ws_market` 的回调分别来自 business / public 两条
   上游连接的任务，并发发送已在生产跑了整个项目周期；③ `_send_raw`（`api/agent.py:271-275`）整段
   `try/except` 吞发送异常。**因此实施照上文写即可，不要为此额外引入锁**；若实施节点观测到帧交错，
   再用一个 `asyncio.Lock` 串行化 `emit` 与心跳两处发送，并在「实施过程发现的方案缺陷」段记一笔。
   另：`agent/loop.py:202-207` 的 run 期心跳（`agent_heartbeat_seconds` 默认 20.0，`config.py:738`）
   在 run 进行时也发 `heartbeat`，与本条重复——重复是**良性**的：前端该 case 是 no-op，多一帧只多一次喂狗。

4. **用例落点（[R-15]，本仓既定手法，实施时别再自己发明）**：WS 用例**禁用 `TestClient`**，
   一律用「假 WS 对象 + 直接 `await` 路由协程」。现成模板：`tests/test_weight_train_ws.py:257-271`
   （`monkeypatch.setattr("app.api.weight_models._WS_HEARTBEAT_SECONDS", 0.05)` + `_PingThenDisconnectWS`）。
   注意三条会话流路由都会先 `manager.get(sid)` 并在 `None` 时发 `error` 后关闭，
   所以用例必须**先往管理器注册表注入一个假会话**（需要 `subscribe()` / `snapshot()` / `unsubscribe()` 三个方法），
   姿态照抄同文件 `:234` 的 `get_weight_train_manager()._jobs["gb3-job"] = job`，并在 `finally` 里摘掉。

5. **为什么 `ws_market` 必须保持沉默（[R-7]，本轮明确不改它）**：那条路由是**纯数据泵**，
   它的静默**本身就是证据**——上一轮引入 45s 看门狗，要的正是「行情停了 ⇒ 重连」。给它加保活帧会把
   看门狗**致盲**：连接永远「活着」，而图表可以静止一小时无人察觉，比现状更糟。三条会话流恰恰相反：
   静默是正常态（没有事件就是没有发生事情），静默不构成任何证据，看门狗在那里从一开始就是误伤。

   **代价与补偿（三条会话流加 ping 之后）**：传输层看门狗从此不再替它们发现「会话还在跑、但盯市循环
   停摆」这类**应用级**停滞。补偿信号**早已存在且已在 UI 上**：`live_auto/session.py:460` 的
   `degraded: self.executor.degraded or self._stale_degraded` 与 `status` 事件里的降级标记，
   本来就是这类停滞的权威判据——传输层看门狗此前只是碰巧兼职了这件它不该管的事，且做得很糟
   （它的「补救」是断开重连，而重连恰恰把停摆掩盖成一次闪断）。

**前端为什么零改动**：4 个消费者全部是**白名单式**路由，未知 `type` 静默忽略——
`useAiTradeGroupStream.routeMemberEvent`（switch 无 default 分支，落空即返回）、
`useLiveAutoGroupStream.routeMemberEvent`（if 链）、`page.tsx:724 handlePaperEvent`（if 链）、
`AgentPanel.onEvent`（switch 且已有 `heartbeat` case）。这条前提必须在实施时**再核一次**：
本仓库有过一次血的教训——`link` 帧被 else 兜底当成 K 线，导致桌面端开屏白屏
（`docs/diagnoses/desktop-white-screen-market-ws-link-frame`）。

---

### P1-D4 · 成交序列前后端双向无界，且合并是 O(n)：长跑会话会持续劣化

**取证**：

- 后端 `services/quant/app/live_auto/executor.py:123` —— `self.trades: list[dict] = []`，
  4 处 `append`（`:310 / :517 / :560 / :925`），**全仓没有任何裁剪**。
- 与之对照，同一个会话对象里**其它所有实时缓冲都是有界的**：
  `app/ai_auto/session.py:287-292` 的 `_equity`（`maxlen=_SNAPSHOT_EQUITY_MAX=1000`）、
  `_decisions`（`maxlen=50`）、`_traces`（`maxlen=_TRACES_MAX`）。
  `app/paper/session.py:389` 的 snapshot docstring 甚至**明写**「**全部成交** + 末 N 条净值」——
  作者知道净值要封顶，成交这一条是被漏掉的。
- 三处 `snapshot()` 全量下发成交：`ai_auto/session.py:2018`、`live_auto/session.py:465`、
  `paper/session.py:389`。而 snapshot 在**每一次 WS 连接与重连**时都会重放一遍（见 D3：今天的重连
  频率远高于设计预期）。
- 前端同样无界，且更贵：
  - `apps/web/lib/store.ts:908 / :955` —— `trades: [...m.trades, trade]`，
    而**紧挨着的** `decisions` 与 `traces` 都做了 `.slice(-AI_TRADE_DECISIONS_MAX)` / `.slice(-AI_TRADE_TRACES_MAX)`
    （`store.ts:123-127` 定义了 3 个上限常量，唯独没有成交的）；
  - `store.ts:951` 的 `appendAiTradeMemberTrade` 每次追加前跑一次 `m.trades.some(...)` 去重
    ⇒ 会话全程 **O(n²)**；
  - `apps/web/lib/paperMulti.ts:68-74` `mergePaperTrade` 无界；
  - `apps/web/lib/paperMulti.ts:76-85` `mergePaperEquity` **每来一个净值点就重建一次 Map 并对全表排序**
    ⇒ O(n log n)/点，且 n 无界。
  - **[R-2] v1 漏了第四处、也是唯一有成交明细 UI 的那处**：`store.ts:394` `liveAutoTrades: Trade[]`
    与 `:814-820` `appendLiveAutoTrade`——同样的 `some()` 去重、同样的 `[...prev, trade]`、同样无界，
    由 `page.tsx:806` 的 snapshot 整列灌入。它正是 `Strategy/AutoTradeTradesTable.tsx:31` 的数据源。

**红线（这条必须原样写进实现，违反即引入静默的交易行为变化）**：
`executor.trades` 这个 list **本身不得裁剪**，因为它是若干**会计与判定**的真值来源：

| 消费点 | 用途 | 裁剪后果 |
|---|---|---|
| `ai_auto/discipline_runtime.py:95` | `opens = sum(...)` 会话累计开仓次数 → 换手纪律闸 | 闸门阈值被静默放松 |
| `ai_auto/session.py:2073-2074` | `tradeCount` / `avgPnl` 分母 → run metrics 落库 | 历史统计低报 |
| `ai_auto/session.py:570 / :819` | `_cost.reconcile_tick(trades=...)` 成本对账窗口 | 净额盈亏失真 |
| `ai_auto/resume.py:279` | 续跑时整体回灌 `executor.trades` | 类型/长度契约 |
| `ai_auto/profit_guard_runtime.py:81`、`session.py:451` | `reversed(...)` 反向扫最近一腿 | 只读最近若干条，安全 |

因此本轮**只封顶「发出去的」和「前端留着的」**，不动会计真值。

**第二条红线（v2 [R-1 / P0] 新增，v1 缺失）：前端的 `member.trades` 也是「统计真值」，不只是展示缓冲。**
评审复核发现**两处已在展示的统计直接由它求和**：

| 消费点 | 用途 | 封顶后果 |
|---|---|---|
| `Agent/aiTradeStats/AiTradeStatsLive.tsx:108-124` | `fullSummary = summarizeRoundTrips(ledger, …)`，源码注释明写「**未筛选全会话**总盘（returnPct/tradeCount）」 | 会话收益率 / 成交笔数**静默低报** |
| `Agent/aiTradeStats/AiTradeStatsLive.tsx:128-134` | `summarizeProfitCaptureAcross(members.map(m => ({trades: m.trades})))`，注释明写「取**全会话**」 | 盈利捕获率静默失真 |
| `Chart/AutoTradePnlPanel.tsx:49-61` | `buildLedger(members)` → 自动交易盈亏面板的时间窗汇总 | 早于窗口起点的回合整段消失 |

v1 把这一侧截到 300，等于把红线表里「统计低报」那一格原样搬到 wire 的另一侧。**本轮不接受静默低报**，
故封顶值与告知方式按下面重定：

**修法**：

1. 新建 `services/quant/app/wire_caps.py`（三个会话模块共用的单一真源，避免第四次各写各的）：

```python
"""实时快照的 wire 级封顶（单一真源）。

**只封顶下发与前端留存，绝不裁剪服务端的会计真值**——`executor.trades` 是换手闸、run metrics
与成本对账的真值来源（见 spec §三 P1-D4 红线表），裁它等于静默改变交易行为。

取值是**安全网**而不是展示策略（spec §三 D4 / [R-1]）：前端有两处统计按「全会话」口径直接由这份
成交序列求和，封得太紧会静默低报。2000 笔既能钉死内存与重放载荷的上界，又高于真实会话的量级，
只有在被真正触到时才需要在 UI 上说明——那时也会说明。
"""
SNAPSHOT_TRADES_MAX = 2000


def tail_trades(trades: list[dict], cap: int = SNAPSHOT_TRADES_MAX) -> list[dict]:
    """返回末 ``cap`` 条（不足则原样）。**永远返回新 list**，绝不把内部真值列表借出去。"""
    return list(trades[-cap:]) if len(trades) > cap else list(trades)
```

2. 三处 `snapshot()` 改为下发 `trades` = 末 2000 条（**不新增 `tradesTotal` wire 字段**，理由见第 4 点）：

- `ai_auto/session.py:2018`：`"trades": tail_trades(self.executor.trades)`；
- `live_auto/session.py:465`：同上；
- `paper/session.py:389-397`：**注意两件事**——
  ① 这里的真值列**不是** `executor.trades` 而是 **`self.broker.trades`**（[R-12]，照 v1 写会 `AttributeError`）；
  ② `seeded` 标记依赖**绝对索引**（`idx < self._seeded_trade_count`），必须**先在全表上 enumerate 出带
  seeded 的记录、再取末 2000 条**，绝不能先切片再 enumerate（先切会把预热成交全部误标成非预热）。
  即：`trades = tail_trades([_trade_record_out(t, seeded=(idx < n)) for idx, t in enumerate(self.broker.trades)])`。
  该文件的 snapshot docstring（`:390`）也要从「全部成交」改成「末 N 条成交」——**文档字符串是契约的一部分**。

3. 前端：
- 新建 `apps/web/lib/wireCaps.ts`（前端侧单一真源，避免 store / paperMulti / 各表格各写一个数字）：
  `export const TRADES_MAX = 2000;` / `export const EQUITY_MAX = 1000;`，
  注释写明与后端 `wire_caps.SNAPSHOT_TRADES_MAX` / `_SNAPSHOT_EQUITY_MAX` 镜像；
- `store.ts:814-820`（live-auto 单会话，[R-2]）、`:908`（live-auto 成员）、`:955`（ai-trade 成员）、
  以及对应的三处整列 setter（`:813 / :899 / :946`）：一律 `.slice(-TRADES_MAX)`；
- `paperMulti.ts:mergePaperTrade` 追加 `.slice(-TRADES_MAX)`；
  `mergePaperEquity` 追加 `.slice(-EQUITY_MAX)`（净值这一侧**无信息损失**：后端 snapshot 早就只回末 1000 条，
  见 `paper/session.py:397`，前端镜像同一个数字只是把增量路径也钉住）；
- 去重的扫描范围随之被封顶（`some` 的 n 恒 ≤ TRADES_MAX），会话全程的 O(n²) 上界被钉死，语义不变。

4. **告知与口径（[R-3]：wire 上不加 `tradesTotal`）**。v1 想加一个 `tradesTotal` 字段来渲染
  「共 M 笔」，但 §七 里**没有任何文件承接它**——本仓已经栽过两次「有写点没读点 / 算了但看不见」，
  一个没有读点的 wire 字段就是死码。而截断这件事**在前端本地就可判定**（`trades.length >= TRADES_MAX`），
  于是取消该字段，改为三个**有明确落点**的告知：

  - `Chart/ChartWorkspace.tsx`：复用**已有**的截断横幅位（`:257` 的 `tradeCapReached` banner，
    回测 5000 笔上限用的就是它），新增一条 `liveTradeCapReached` 分支，文案
    「实时成交仅保留最近 2000 笔，更早的记录见运行历史」。判据由 `app/page.tsx` 计算并透传
    （`page.tsx:505` 的 `bottomTrades` 就在那里，**且只有它知道当前是不是回测车道**——
    绝不能在表格内部按长度自判，否则一次 ≥2000 笔的回测会误报）；
  - `Strategy/AutoTradeTradesTable.tsx`：自持数据源（`useAppStore(s => s.liveAutoTrades)`），
    在表尾加同一句次要文案，条件 `trades.length >= TRADES_MAX`；
  - `Agent/aiTradeStats/AiTradeStatsLive.tsx` 与 `Chart/AutoTradePnlPanel.tsx`：当任一成员触到上限时，
    把「全会话」口径标注改成「口径：最近 2000 笔成交」。**统计的口径变了就必须说**——
    这正是上一条红线要防的事，不能因为它发生在前端就降格处理。

  **「看不到全部」必须是被说出来的事实**，不能让用户以为自己的成交丢了；
  **「统计的分母变了」更必须被说出来**，否则一个交易产品在报一个它自己都不相信的收益率。

---

### P1-D5 · 警告语义色不存在于设计系统：六处调用三种回退，最常用的那个在浅色主题下不达 WCAG AA

**取证**：`apps/web/app/globals.css:9-36` 定义的 token 全集是
`--bg / --surface / --surface-2 / --border / --text / --muted / --accent / --accent-soft / --long / --short`，
**没有任何 warn/warning**。而组件层有 6 处在用它：

| 文件:行 | 写法 | 实际渲染 |
|---|---|---|
| `Strategy/BacktestConfigModal.tsx:602` | `var(--warn,#d97706)` | `#d97706` |
| `Strategy/BacktestConfigModal.tsx:611` | `var(--warn,#d97706)` | `#d97706` |
| `Strategy/BacktestConfigModal.tsx:658` | `var(--warn,#d97706)` | `#d97706` |
| `Chart/AutoTradeGroupConfirmModal.tsx:247` | `var(--warning,#d97706)` | `#d97706`（**变量名还拼成了另一个**） |
| `Agent/AiTradeTradabilityFields.tsx:243` | `var(--warn, var(--short))` | 回退到**红色** `--short` |
| `Agent/AiTradeTradabilityFields.tsx:244` | `var(--warn, var(--short))` | 回退到**红色** `--short` |

于是同一个「警告」语义在同一个应用里渲染成**两种颜色**（琥珀 / 铁锈红），并且：

- `#d97706` 是**写死的**，明暗主题下**完全相同**——而这套设计系统的每一个语义色都是双主题的
  （`--short` 是 `#c0573e` → `#d97a62`）；
- 本轮实测对比度（WCAG 2.1 相对亮度公式）：

| 前景 | `--bg #f7f5f2` | `--surface #fffefb` | `--surface-2 #f2efe9` |
|---|---|---|---|
| `#d97706`（当前） | **2.93** | **3.16** | **2.78** |

  普通文本的 AA 门槛是 4.5:1 —— **当前的警告文案在浅色主题的全部三种底色上都不合格**，
  而警告文案恰恰是最需要被读到的那一类文字。（暗色主题下 4.52–5.52，勉强合格，但那是巧合。）

**修法**：

1. `globals.css` 两个主题各补一对 token（取值由本轮实测选定，见下表）：

```css
:root {
  ...
  --warn: #8f5209;        /* amber-ink：在全部三种浅色底 **与 --warn-soft 上** 都达 AA */
  --warn-soft: #f6e6cf;   /* 警告底色，配 --warn 前景 */
}
.dark {
  ...
  --warn: #e0a75f;
  --warn-soft: #3a2f1e;
}
```

| 取值 | 对 `--bg` | 对 `--surface` | 对 `--surface-2` | 对 `--warn-soft` |
|---|---|---|---|---|
| light `#8f5209`（**v2 定稿**） | 5.71 | 6.16 | 5.42 | **5.07** |
| ~~light `#9a5a0d`（v1）~~ | 5.02 | 5.42 | 4.76 | **4.46 ✗** |
| dark `#e0a75f` | 8.26 | 7.57 | 6.76 | 6.14 |

  **[R-8] 为什么换值**：v1 的 `#9a5a0d` 在三种页面底色上都达标，却在**它自己配套的 `--warn-soft` 底色上
  只有 4.46**——而 §九 R8 恰恰承诺「警告面一律配 `--warn-soft` 底色」。一个为 AA 而生的 token，
  在自己设计的那个配对上不达 AA，是本条最不该留的尾巴。`#8f5209` 四项全过且色相不变
  （明度降一档，与 `--accent #cc785c` 的区分度反而更好）。以上 9 个数字均由评审节点用 WCAG 2.1
  相对亮度公式复算，可复现。

  （对照：既有 `--short` 在浅色下只有 3.91–4.45，本轮新 token 严格优于现状；`--short` 自身的
  对比度整改牵动整套配色，明确列为**本轮范围外**，见 §十。）

2. 六处调用统一改成 `var(--warn)`，**删掉全部内联回退**——回退值是这类问题的温床：
   它让「token 不存在」这件事永远不会被发现。
3. 新增源码扫描用例（沿用 `apps/web/app/globals.source.test.ts` 已确立的手法，注意 CRLF：
   跨行正则一律写 `\r?\n`，否则干净检出上恒红）：
   - `globals.css` 的 `:root` 与 `.dark` **都**定义了 `--warn` 与 `--warn-soft`；
   - `components/` 下不再出现 `--warning`、不再出现 `#d97706`。

---

### P2-D6 · 破坏性确认框的初始焦点无契约、无用例，且源码写着「焦点在删除键上」

**取证**：`apps/web/components/ui/ConfirmDialog.tsx:39-46` 给**确认（危险）**按钮加了 `autoFocus`；
而它外层的 `Modal`（`components/ui/index.tsx:268-280`）在 `useEffect` 里做
`dialog?.querySelector(FOCUSABLE)` 并 focus **DOM 中第一个可聚焦元素**。两者相互覆盖，
最终落点取决于「React 的 autoFocus 在 layout 阶段执行、父组件的 passive effect 在其后执行」
这个实现细节——也就是说：**代码表达的意图（焦点在危险按钮）与实际行为（焦点在取消按钮）相反，
而且没有任何用例把二者中的任何一个钉住**。

`ui/` 目录下有 `Modal.test.tsx`、`CrashFallback.test.tsx`、`PaneSplitHandle.test.tsx`，
**唯独没有 `ConfirmDialog.test.tsx`**；`Modal.test.tsx:30-34` 也只断言
「焦点落在对话框**内部某处**」（`dialog.contains(document.activeElement)`），不钉具体元素。

后果是**回归无声**：今天恰好是安全的（取消键在 DOM 里排在前面），但任何一次调用方重排
（把确认键写在前面、在 message 里插一个链接、给标题加一个「了解更多」按钮）都会让
**回车键的落点从「取消」变成「永久删除」**，且没有任何用例会红。ConfirmDialog 当前有 6+ 个调用方
（DiscoveryRunList ×2、ExploreResultPanel、SymbolGroupModal、WeightBatchArena、WeightBatchList…），
守的全是不可撤销的删除。

**修法**：

0. **前置步骤（[R-4]，v1 缺失，缺了这步 `tsc` 直接红）**：`ui/index.tsx:9-36` 的 `Button` 是普通函数
   组件，React 18.3 下 `ref` **不是**它的合法 prop（类型上 `ButtonHTMLAttributes` 不含 `ref`，
   运行期也拿不到 DOM 节点）。故先把 `Button` 改成
   `React.forwardRef<HTMLButtonElement, …>((props, ref) => <button ref={ref} … />)`
   并补 `Button.displayName = "Button"`。这是**纯加法**：既有几十个调用方一行不改，
   forwardRef 组件对不传 ref 的调用方行为逐字相同。
1. `Modal` 增加**可选** prop `initialFocusRef?: React.RefObject<HTMLElement>`；挂载 effect
   （`ui/index.tsx:268-280`）改为 `(initialFocusRef?.current ?? first ?? dialog)?.focus()`。
   默认行为**逐字不变**（不传即旧语义），11 个既有调用方零改动。
   注意 effect 依赖仍只列 `[open]`——`initialFocusRef` 是 ref 对象，身份稳定，
   加进依赖数组只会引入本仓已复发过的「依赖不稳 → effect 反复重跑」那类缺陷。
2. `ConfirmDialog` 删掉 `autoFocus`（`ConfirmDialog.tsx:43`），改为持一个
   `cancelRef = useRef<HTMLButtonElement>(null)` 挂在取消按钮上并传给 `Modal` 的 `initialFocusRef`，
   在注释里写清理由：**破坏性确认框的初始焦点必须钉在安全选项上**（HIG / WAI-ARIA 一致的实践：
   默认动作不应是不可逆动作）。
3. 新增 `ConfirmDialog.test.tsx`：初始焦点 === 取消按钮；Esc → `onCancel`；点确认 → `onConfirm`；
   `danger=false` 时初始焦点仍在取消。
4. `Modal.test.tsx` 追加：传 `initialFocusRef` 时焦点落在指定元素；不传时行为与现状一致。

---

### P2-D7 · 全仓最后一处 `window.confirm`：删除因子组合走的是原生阻塞对话框

**取证**：`apps/web/components/Factor/FactorSetList.tsx:49-54`。这是全仓**唯一**残留的
`window.confirm`（已 grep 全量确认），而仓库里同时有：

- `components/ui/ConfirmDialog.tsx:8` 的 docstring：「**替换破坏性操作的阻塞式原生 `window.confirm`**」；
- `Agent/AiTradePauseDialog.tsx:17`：「**绝不用 `window.confirm`**：走既有 `Modal` 体系」；
- `Agent/AiTradeTradabilityFields.tsx:285`：「`window.confirm` 不可用——桌面端体验差，
  且它会打断弹窗内的表单态」。

也就是说：约定早已成文并被两处显式引用，只有这一处漏网。在 Electron 打包形态下，原生 confirm 会
**同步阻塞渲染进程**（期间行情 WS 帧堆积、图表停画），并弹出一个与自绘标题栏（WCO）风格割裂的
系统对话框——这正是上一轮 D8「探索弹窗自绘遮罩收编进 Modal 原语」同形的孤岛。

**修法**：改用 `ConfirmDialog`，受控 `const [pendingDelete, setPendingDelete] = useState<FactorSet | null>(null)`，
文案逐字沿用现有 `window.confirm` 的那句（「将同时移除其在交易策略面板中对应的因子组合得分策略」），
`onConfirm` 里 `remove.mutate(pendingDelete.id)` 后置 null。

**必须同步改测试**：`FactorSetList.test.tsx:91-125` 现有两个用例用
`vi.spyOn(window, "confirm").mockReturnValue(true/false)` 驱动流程，改造后必然红——
须改为点击应用内弹窗的「确定 / 取消」按钮，并保留原有断言（双失效 `["factor","sets"]` + `["strategies"]`、
选中详情被清空、取消时不发请求）。

---

### P2-D8 · 共享 `Tabs` 原语没有页签语义：11 处调用对读屏是「一排普通按钮」，且无方向键

**取证**：`components/ui/index.tsx:343-396` 的 `Tabs` 渲染的是裸 `<button>`，
**没有** `role="tablist"` / `role="tab"` / `aria-selected` / roving tabindex / 方向键。
它被 **11 处**调用（RightPanel 主页签、ChartHeaderBar 视图段控、LiveBottomPanel、InstrumentSidebar、
StrategyPanel、MemberPicker、AiTradeStatsPanel、AiBacktestModal、CandleChart、ChartWorkspace、
StrategyListSection）——**几乎是整个应用的主导航**。

反讽的是，同仓库里三处**手写**的页签**都**做对了：
`Strategy/MultiSampleResults.tsx:97-144`（tablist/tab/tabpanel + `aria-selected` + ←/→ 焦点漫游，
见 `:91` 的 `querySelectorAll('[role="tab"]')[nextIdx]?.focus()`）、`Agent/AgentPanel.tsx:339/443`、
`Agent/AiTradeAgentTrace.tsx:89/97`。**共享原语是这套代码里 a11y 最差的那一个**，
而它恰恰覆盖面最广。这与「符合人机交互最佳实践」的产品定位直接冲突：
键盘用户在主页签之间移动需要按 Tab 逐个穿过，读屏用户听不到「第 2 项，共 4 项，已选中」。

**修法**（对 11 个调用方零改动、视觉零变化）：

1. `Tabs` 两个变体（`segmented` / `underline`）共用一段包装：外层 `role="tablist"`，
   每个按钮 `role="tab"` + `aria-selected={active === t.key}` + `tabIndex={active === t.key ? 0 : -1}`
   + `type="button"`；
2. `onKeyDown` 实现 roving tabindex：`ArrowLeft/ArrowRight` 环形移动、`Home/End` 跳首尾，
   移动时同时 `onChange(next.key)` 并把焦点移到目标按钮（照抄 `MultiSampleResults.tsx:85-95` 的实现，
   保持同一套手感）；
3. 新增**可选** prop `label?: string` → `aria-label`，供多组页签共存时区分（调用方不传即无此属性，
   行为不变）；`aria-controls` 需要面板 id，多数调用方不渲染独立 tabpanel，故**不引入**——
   宁可少一个属性，也不要一个指向不存在 id 的 `aria-controls`（那比没有更糟）。

**实施风险（必须在实施节点当场验证）**：既有组件用例中若有用
`getByRole("button", { name: "…" })` 定位页签的，改成 `role="tab"` 后会红。
实施时必须跑**全量** vitest 并逐个修正受影响用例（改用 `getByRole("tab", …)`），
**不得**为了让用例绿而回退 role。

---

### P2-D9 · 两道 lint 门禁是「声明了但从不执行」：ruff 没装，`next lint` 不可运行

**取证**：

- `services/quant/pyproject.toml:10-12` 有 `[tool.ruff] line-length = 110 / target-version = "py311"`，
  但 `requirements.txt` **不含 ruff**，venv 里**没有 ruff**（本轮 `ls .venv/Scripts | grep ruff` 为空），
  `.github/workflows/ci.yml` 的 backend job **只跑 pytest**。配置是装饰性的。
- `apps/web/package.json:9` 有 `"lint": "next lint"`，但仓库**没有任何 ESLint 配置文件**，
  `devDependencies` 里**没有 eslint / eslint-config-next**。这条脚本在 CI 或非交互终端里必然失败。

**本轮实测的规模**（用临时安装的 ruff 0.16.3）：

| 规则集 | `services/quant` 全量 | 说明 |
|---|---|---|
| ruff 默认全集 | **445 条** | 其中 **141 条 B008** 是 FastAPI `Depends()` 写在默认参数里的**假阳**，不可接受 |
| 精选集 `F, E9` | **17 条**（15 条 `--fix` 自动修） | 全部是真问题：未用导入 15 条、未用局部变量 2 条 |

**修法**（只接一道**真能绿、且只报真问题**的门禁）：

1. `pyproject.toml` 显式钉规则集（不吃 ruff 默认集的版本漂移）：

```toml
[tool.ruff.lint]
# 只选「一定是缺陷」的两类：pyflakes（未用导入/变量、未定义名、重复定义）与语法/运行时错误。
# 刻意不选 B008——FastAPI 的 `Depends()` 默认参数是框架惯例，选它会产出 141 条假阳。
select = ["F", "E9"]
```

2. `requirements.txt` 追加 `ruff==0.16.3`（钉版本；lint 结果随版本漂移是门禁不可信的头号原因）。
3. `.github/workflows/ci.yml` 的 backend job 在 `Run pytest` **之前**插一步：
   `- name: Lint (ruff)` / `run: python -m ruff check .`。
4. 清掉那 17 条：`python -m ruff check . --fix` 处理 15 条；手工处理 2 条 F841
   （`tests/test_engine_signal_edge.py:195` 的 `prev`、`tests/test_trend_pullback.py:198` 的 `df`——
   **先读上下文**：若是「本该被断言却忘了用」的变量，正确的修法是补断言而不是删变量）；
   另修 `tests/test_engine_regime.py:13` 的非法 `# noqa` 指令（ruff 会 warning；它不影响退出码，
   但一条永远印在 CI 日志里的 warning 会训练所有人忽略 CI 日志）。

   **完整清单（[R-13]，评审节点实跑 `ruff check . --select "F,E9" --output-format concise` 所得，
   共 17 条 / 16 个文件 = 14 个测试文件 + 2 个应用文件；v1 写「13 个测试文件」少数了一个）**：

   | 文件:行 | 规则 |
   |---|---|
   | `app/agent/services.py:34` | F401 `..strategies.combo_specs.validate_combo_params` |
   | `app/engine/backtester.py:14` | F401 `.simulator.RiskParams` |
   | `tests/test_agent_tools.py:4` / `:13` | F401 `pytest` / `app.okx.types.Candle` |
   | `tests/test_ai_trade_position_step.py:30` | F401 `app.config.get_settings` |
   | `tests/test_ai_trade_reflection.py:20` | F401 `app.ai_auto.prompts.build_reflection_prompt` |
   | `tests/test_discovery_assets_api.py:14` | F401 `app.config.get_settings` |
   | `tests/test_engine_regime.py:13` | F401 `app.strategies`（+ 同行非法 noqa） |
   | `tests/test_engine_signal_edge.py:195` | F841 `prev` |
   | `tests/test_factor_robustness.py:19` | F401 `app.engine.factors.FACTOR_LIBRARY` |
   | `tests/test_market_headers.py:17` | F401 `pytest` |
   | `tests/test_momentum_breakout.py:12` | F401 `pandas` |
   | `tests/test_paper_combo.py:5` | F401 `pandas` |
   | `tests/test_paper_priming.py:10` | F401 `pytest` |
   | `tests/test_trading_api.py:8` | F401 `pytest` |
   | `tests/test_trading_close.py:8` | F401 `pytest` |
   | `tests/test_trend_pullback.py:198` | F841 `df` |

   **[R-14] 判定口径**：`ruff check .` 会连**未跟踪的在制文件**一起扫。评审节点实测：今天那些在制文件
   （`test_factor_library_expansion.py` 等）在 `F,E9` 下**一条都不报**，所以 G4 现在能真绿。
   但这是巧合不是保证——若实施时 G4 因**别人的未跟踪文件**变红，正确处置是**排除后判定并在报告里写明**，
   **绝不允许**去改那些文件（DoD-4）。
5. 前端：**删除** `apps/web/package.json` 的 `"lint": "next lint"`。理由：一条跑不起来的脚本比没有更糟——
   它让每个新人以为「这个仓库有 lint」。接入 ESLint（`eslint-config-next` + `next/core-web-vitals`）
   需要先在 477 个前端文件上测量整改量，属于独立一轮的工作，明确列为 §十 范围外并写明原因。

---

## 四、技术设计（架构 / 关键代码路径 / 时序）

本方案不引入新的架构层，全部改动落在**四个既有边界**上，彼此无耦合，可并行实施：

```
┌── 进程生命周期 ────────────────────────────────────────────────┐
│  desktop/src/main.js  whenReady→bootstrap→(NEW catch)→dialog+exit(1)   D2 │
│  services/quant/app/main.py  lifespan→_cancel_background→(NEW)_run_shutdown D1 │
└───────────────────────────────────────────────────────────────┘
┌── 实时流边界（9 条路由里改 4 条 + 客户端 0 改动）─────────────────┐
│  ws_paper / ws_live_auto / ws_ai_trade : queue.get() → wait_for + ping  D3 │
│  ws_agent : receive_text() → wait_for + heartbeat                      D3 │
│  ★ ws_market **刻意不改**：那条流上「静默即证据」，加保活会致盲看门狗      │
└───────────────────────────────────────────────────────────────┘
┌── 快照与前端缓冲（双向封顶，wire 零加法）───────────────────────┐
│  wire_caps.tail_trades  →  3×snapshot()  →  store/paperMulti .slice   D4 │
│  ★ executor.trades / broker.trades 本体不动（会计真值）                  │
│  ★ 触到上限 → ChartWorkspace 横幅 + 两处统计口径改标注（否则即静默低报）  │
└───────────────────────────────────────────────────────────────┘
┌── 设计系统与交互原语（apps/web/components/ui）──────────────────┐
│  globals.css --warn/--warn-soft   D5                                     │
│  Modal.initialFocusRef → ConfirmDialog 钉取消   D6                       │
│  FactorSetList: window.confirm → ConfirmDialog  D7                       │
│  Tabs: role/aria-selected/roving tabindex       D8                       │
└───────────────────────────────────────────────────────────────┘
```

**关停时序（D1 之后）**：

```
SIGTERM / lifespan exit
  └─ _cancel_background(4 个常驻协程)         有界 5s（既有，不动）
  └─ _run_shutdown(12 步)
       每步：task = ensure_future(step()); await wait({task}, timeout=20s)
         ├ 正常返回 → 取一次 exception 记日志 → 下一步
         ├ 抛异常   → logger.warning(step 名 + 异常) → 下一步        ← 新增
         ├ 建不出来 → logger.warning(step 名 + 异常) → 下一步        ← 新增（getter 自己抛）
         └ 超时     → cancel + 1s 收尾窗口 + warning → **抛下它**下一步 ← 新增（硬死线）
       第 12 步 dispose_engine() **必定**被执行           ← 这是本条的全部意义
```

**为什么不是 `wait_for`（[R-6]）**：`wait_for` 超时后会 cancel 并**继续 await**；一个吞掉取消的步骤
照样能把链堵死，于是「有界」只是写在注释里。`asyncio.wait` 拿到的是**真**边界——代价是被抛下的那一步
可能与后续步骤并发跑一小会儿（日志会记），这在「引擎永不释放 / 进程永不退出」面前是显然更优的一侧。

**空闲流时序（D3 之后，以 agent 为例）**：

```
t=0    connect → ready + history
t=20   服务端 receive_text 超时 → 下发 {"type":"heartbeat"} → 客户端喂狗（case "heartbeat": break）
t=40   同上 …… 看门狗永不触发，连接指示灯不再闪断，history 不再每 46s 整表重建
run 期  loop.py 的 run 作用域心跳照旧（20s），与本条互不冲突；重复活性帧对客户端是 no-op
```

---

## 五、接口设计（wire）

**只新增活性帧，不新增任何业务字段，无破坏性变更，无版本号变更。**

| 通道 | 变更 | 形状 | 消费者 |
|---|---|---|---|
| `WS /api/ws/paper/{sid}` | 新增帧 | `{"type":"ping"}` | 前端白名单忽略（零改动） |
| `WS /api/ws/live-auto/{sid}` | 新增帧 | `{"type":"ping"}` | 同上 |
| `WS /api/ws/ai-trade/{sid}` | 新增帧 | `{"type":"ping"}` | 同上 |
| `WS /api/ws/agent/{convId}` | 新增帧 | `{"type":"heartbeat","data":{}}`（**无 `seq`**） | `AgentPanel.tsx:198` 已有 case |
| 上述三条会话流的 `snapshot.trades` | **语义收窄** | 由「全部」变为「最近 2000 条」 | 前端本地判定并如实告知（见 D4-4） |
| ~~`snapshot.tradesTotal`~~ | **v2 撤销** | — | v1 计划新增，但全仓无读点 ⇒ 死码（[R-3]） |

`snapshot.trades` 是本轮**唯一**的语义收窄。可接受性论证：① 2000 是安全网量级，真实会话触不到；
② 触到时前端**本地即可判定**（`length >= TRADES_MAX`）并在横幅与统计口径上如实说明；
③ 历史全量另有入口（`GET /runs/{id}` 与统计看板历史页）。REST 侧一律不动。

**为什么撤销 `tradesTotal`**：它唯一的用途是渲染「共 M 笔」，而截断本身在前端本地就能判定；
本仓已两次栽在「有写点没读点」上，一个没有读点的 wire 字段不是加法，是债。

**HTTP 接口**：本轮零新增、零修改。**DB**：零迁移。

---

## 六、数据模型

**无 DB schema 变更、无迁移脚本、无 `models.py` 改动。** 涉及的仅是进程内形状：

| 位置 | 变更前 | 变更后 |
|---|---|---|
| `LiveExecutor.trades` | `list[dict]`，无界 | **不变**（会计真值，红线一） |
| `SimBroker.trades`（paper 侧真值列） | `list`，无界 | **不变**（同上，[R-12]） |
| `snapshot()["trades"]` | 全量 list | 末 `SNAPSHOT_TRADES_MAX=2000` 条 |
| ~~`snapshot()["tradesTotal"]`~~ | — | **不新增**（v2 撤销，[R-3]） |
| `store.liveAutoTrades` | `Trade[]`，无界（[R-2] v1 漏） | 尾部 2000 封顶 |
| `store.aiTradeGroup.members[*].trades` | `Trade[]`，无界 | 尾部 2000 封顶 |
| `store.liveAutoGroup.members[*].trades` | `Trade[]`，无界 | 尾部 2000 封顶 |
| `paperTradesByStrategyId[*]` | `StrategyTrade[]`，无界 | 尾部 2000 封顶 |
| `paperEquityByStrategyId[*]` | `StrategyEquityPoint[]`，无界 + 每点全表排序 | 尾部 1000 封顶（镜像后端 `_SNAPSHOT_EQUITY_MAX`，无信息损失） |
| `Settings.shutdown_step_timeout_s` | — | `float = 20.0`，新增 |
| `main.py._SHUTDOWN_CANCEL_GRACE_S` | — | `float = 1.0`，新增模块常量 |
| `ui.Button` | 普通函数组件 | `React.forwardRef` 包装（[R-4]，行为逐字不变） |
| CSS 自定义属性 | 10 个语义 token | **12 个**（`--warn` / `--warn-soft`，双主题各一套） |

---

## 七、文件 / 模块变更计划

> 共 **47 个文件**（新建 **10** / 修改 **37**），外加 D9 的 ruff 清理触及的 **16** 个文件
> （14 个测试文件 + `app/agent/services.py` + `app/engine/backtester.py`，清单见 §三 D9），
> 合计 **63**。（v1 表头写「26 个（新建 8 / 修改 18）+ 13」，与表体自相矛盾且少数了 D4 的落点文件——[R-10]。）
> 实施节点须逐行核对本表，**不得**新增表外文件，**不得**遗漏表内文件。

| # | 文件 | 动作 | 一句话意图 | 缺陷 |
|---|---|---|---|---|
| 1 | `services/quant/app/main.py` | 改 | 关停链改为步骤表 + `_run_shutdown` 逐步隔离与死线 | D1 |
| 2 | `services/quant/app/config.py` | 改 | 新增 `shutdown_step_timeout_s: float = 20.0` | D1 |
| 3 | `services/quant/tests/test_main_shutdown.py` | 改 | 追加「中间步骤抛异常 / 超时后，后续步骤仍被执行」用例（注入假步骤表，**不驱动真实 lifespan**） | D1 |
| 4 | `desktop/src/lib/startup-failure.js` | 新 | 纯函数 `describeStartupFailure(err, logDir) → {title, body}` | D2 |
| 5 | `desktop/scripts/startup-failure.test.mjs` | 新 | `node --test` 冻结上条的文案契约与非 Error 入参鲁棒性 | D2 |
| 6 | `desktop/src/main.js` | 改 | `whenReady().then(bootstrap).catch(...)`：弹窗 + 日志 + `exit(1)` | D2 |
| 7 | `desktop/package.json` | 改 | 新增 `check:startupfailure` 语法门禁（自动进 `run-s check:*`） | D2 |
| 8 | `services/quant/app/api/paper.py` | 改 | `_WS_HEARTBEAT_SECONDS` + 空闲 `ping` | D3 |
| 9 | `services/quant/app/api/live_auto.py` | 改 | 同上 | D3 |
| 10 | `services/quant/app/api/ai_auto.py` | 改 | `ws_ai_trade` 空闲 `ping`（复用已有常量 `:76`） | D3 |
| 11 | `services/quant/app/api/agent.py` | 改 | `receive_text` 改带超时 + 空闲 `heartbeat` 帧 | D3 |
| 12 | `services/quant/tests/test_ws_idle_heartbeat.py` | 新 | 四条流「空闲即发保活帧」+「保活帧不污染事件流 / 不推进 seq」 | D3 |
| 13 | `services/quant/app/wire_caps.py` | 新 | `SNAPSHOT_TRADES_MAX = 2000` + `tail_trades()` 单一真源 | D4 |
| 14 | `services/quant/app/ai_auto/session.py` | 改 | `snapshot()["trades"]` 走 `tail_trades` | D4 |
| 15 | `services/quant/app/live_auto/session.py` | 改 | 同上 | D4 |
| 16 | `services/quant/app/paper/session.py` | 改 | 同上，**真值列是 `broker.trades`**；**seeded 先全表 enumerate 再切尾**；docstring 同步改 | D4 |
| 17 | `services/quant/tests/test_snapshot_trade_cap.py` | 新 | 三处 snapshot 封顶 + paper 的 seeded 不错位 + **真值列未被裁剪**（红线用例） | D4 |
| 18 | `apps/web/lib/wireCaps.ts` | 新 | 前端单一真源 `TRADES_MAX = 2000` / `EQUITY_MAX = 1000` | D4 |
| 19 | `apps/web/lib/store.ts` | 改 | **四处**成交封顶（`liveAutoTrades` + 两个成员 + 整列 setter），去重扫描随之有界 | D4 |
| 20 | `apps/web/lib/paperMulti.ts` | 改 | `mergePaperTrade` / `mergePaperEquity` 封顶 | D4 |
| 21 | `apps/web/app/page.tsx` | 改 | 计算 `liveTradeCapReached`（**只在实时/模拟车道，回测车道恒 false**）并透传 | D4 |
| 22 | `apps/web/components/Chart/ChartWorkspace.tsx` | 改 | 复用 `:257` 既有截断横幅位，加实时成交上限一条 | D4 |
| 23 | `apps/web/components/Strategy/AutoTradeTradesTable.tsx` | 改 | 表尾「仅保留最近 2000 笔」次要文案（条件 `length >= TRADES_MAX`） | D4 |
| 24 | `apps/web/components/Agent/aiTradeStats/AiTradeStatsLive.tsx` | 改 | 触到上限时把「全会话」口径标注改成「最近 2000 笔成交」 | D4 |
| 25 | `apps/web/components/Chart/AutoTradePnlPanel.tsx` | 改 | 同上 | D4 |
| 26 | `apps/web/lib/store.test.ts` | 改 | 封顶用例（第 2001 笔后长度恒 2000 且保留最新，含 `liveAutoTrades`） | D4 |
| 27 | `apps/web/lib/paperMulti.test.ts` | 改 | 同上（成交 2000 / 净值 1000） | D4 |
| 28 | `apps/web/components/Strategy/AutoTradeTradesTable.test.tsx` | 改 | 触上限出提示、未触不出提示 | D4 |
| 29 | `apps/web/components/Agent/AiTradeStatsPanel.test.tsx` | 改 | 触上限时口径标注改写 | D4 |
| 30 | `apps/web/lib/wireCaps.source.test.ts` | 新 | 源码扫描：前端 `TRADES_MAX` 与后端 `wire_caps.SNAPSHOT_TRADES_MAX` **数字必须相等**（[R-16]，正则用 `\r?\n`） | D4 |
| 31 | `apps/web/app/globals.css` | 改 | 双主题各补 `--warn` / `--warn-soft`（light `#8f5209`） | D5 |
| 32 | `apps/web/app/globals.source.test.ts` | 改 | 追加「两个主题都定义了 --warn/--warn-soft」扫描用例（正则用 `\r?\n`） | D5 |
| 33 | `apps/web/components/Strategy/BacktestConfigModal.tsx` | 改 | 3 处统一 `var(--warn)`，删内联回退 | D5 |
| 34 | `apps/web/components/Chart/AutoTradeGroupConfirmModal.tsx` | 改 | 1 处 `--warning` → `var(--warn)` | D5 |
| 35 | `apps/web/components/Agent/AiTradeTradabilityFields.tsx` | 改 | 2 处 `var(--warn, var(--short))` → `var(--warn)` | D5 |
| 36 | `apps/web/components/ui/warnToken.source.test.ts` | 新 | 源码扫描：`components/` 下禁止出现 `--warning` 与 `#d97706` | D5 |
| 37 | `apps/web/components/ui/index.tsx` | 改 | **`Button` 改 forwardRef**；`Modal` 加 `initialFocusRef`；`Tabs` 加 tablist/tab/aria-selected/roving tabindex/方向键 | D6+D8 |
| 38 | `apps/web/components/ui/ConfirmDialog.tsx` | 改 | 去 `autoFocus`，显式把初始焦点钉在「取消」 | D6 |
| 39 | `apps/web/components/ui/ConfirmDialog.test.tsx` | 新 | 初始焦点在取消 / Esc→onCancel / 点确定→onConfirm / `danger=false` 同样钉取消 | D6 |
| 40 | `apps/web/components/ui/Modal.test.tsx` | 改 | 追加 `initialFocusRef` 用例 + 不传时行为不变（现有 `:30-34` 那条即对照） | D6 |
| 41 | `apps/web/components/ui/Tabs.test.tsx` | 新 | tablist/tab/aria-selected + ←/→/Home/End 漫游 | D8 |
| 42 | `apps/web/components/Factor/FactorSetList.tsx` | 改 | `window.confirm` → `ConfirmDialog` | D7 |
| 43 | `apps/web/components/Factor/FactorSetList.test.tsx` | 改 | 两个用例改为驱动应用内弹窗（断言逐条保留） | D7 |
| 44 | `services/quant/pyproject.toml` | 改 | `[tool.ruff.lint] select = ["F","E9"]` | D9 |
| 45 | `services/quant/requirements.txt` | 改 | 追加 `ruff==0.16.3` | D9 |
| 46 | `.github/workflows/ci.yml` | 改 | backend job 新增 `Lint (ruff)` 步骤（置于 `Run pytest` 之前） | D9 |
| 47 | `apps/web/package.json` | 改 | 删除不可运行的 `"lint": "next lint"` | D9 |
| 48 | （**14** 个后端测试文件 + `app/agent/services.py` + `app/engine/backtester.py`） | 改 | 清掉 17 条 F401/F841 与 1 处非法 noqa（15 条走 `ruff --fix`），清单见 §三 D9 | D9 |

**明确不在表内（避免实施节点「顺手」越界）**：`services/quant/app/api/ws_market.py` 与
`apps/web/lib/marketFrames.ts` / `types.ts` 的 `MarketWsEvent`——见 D3 第 5 点，`ws_market` 刻意不加保活。

**受影响但可能需要顺带修正的既有用例**（实施时以全量跑测为准，不预先猜测）：
任何用 `getByRole("button", …)` 定位页签的组件用例（D8）、任何断言 `snapshot.trades` 长度的后端用例（D4）。

---

## 八、测试与验收标准

### 8.1 门禁（每条都要贴实测输出，不采信上游报告）

| 门禁 | 命令（Windows / PowerShell） | 通过判据 |
|---|---|---|
| G1 前端类型 | `cd apps/web; npx tsc --noEmit` | exit 0 |
| G2 前端单测 | `cd apps/web; $env:NODE_ENV="test"; pnpm test` | 全绿；文件数 ≥ 基线 165 + **4**（新增 `ConfirmDialog.test.tsx` / `Tabs.test.tsx` / `warnToken.source.test.ts` / `wireCaps.source.test.ts`），用例数只增不减 |
| G3 后端全量 | `cd services/quant; .venv/Scripts/python -u -m pytest -q` | **3629 + 新增 passed / 5 skipped / 0 failed**（`test_factor_library_expansion.py` 的 7 条红属未跟踪在制文件，须排除后判定；与上一轮记录的 3630/4 合计一致，差额来自另一处未跟踪文件） |
| G4 后端 lint | `cd services/quant; .venv/Scripts/python -m ruff check .` | `All checks passed!`，exit 0。**口径**：该命令会连未跟踪在制文件一起扫（评审实测它们今天 0 条 F/E9）；若因**别人的**未跟踪文件变红，排除后判定并在报告写明，**不得**去改那些文件（[R-14] / DoD-4） |
| G5 桌面 | `cd desktop; npm run check; npm test` | `check:*` 全绿（含新增 `check:startupfailure`）；`node --test` ≥ **本地基线 27** passed + 新增（Node ≥ 21 才支持 glob；本机 v22.18.0） |
| G6 前端构建 | `cd apps/web; pnpm build` | `Compiled successfully` |
| G7 CI 姿势复核 | `cd apps/web; $env:NODE_ENV=$null; pnpm test` | 与 G2 逐值相同（证明无 NODE_ENV 耦合，沿用上一轮纪律） |
| G8 干净检出复核 | `git archive HEAD \| tar -x -C <tmp>` 后在该副本跑 G5 | **≥ 干净检出基线 19 passed + 本轮新增**（[R-9]：本地 27 与干净检出 19 的 8 条差额来自**未跟踪**的 `desktop/scripts/ensure-wincodesign.test.mjs`；v1 要求「与 G5 同结果」在不违反 DoD-4 的前提下**不可能成立**） |

### 8.2 逐缺陷验收

- **D1**：新用例注入一个「第 3 步抛 `RuntimeError`、第 5 步 `sleep(99)`、第 7 步的工厂本身抛」的假步骤表，
  断言（a）全部步骤都被尝试、第 12 步被调用；（b）超时步被 cancel；（c）`_run_shutdown` 本身不抛；
  （d）日志三条 warning 各含步骤名；（e）**整个 `_run_shutdown` 在 `timeout + grace + ε` 内返回**
  （硬死线的直接证明——用 `time.monotonic()` 夹一次，别只断言「没抛」）。
  **不得**驱动真实 `lifespan`（会打真实网络，上一轮已成文）。
- **D2**：`describeStartupFailure` 对 `new Error("boom")`、`"boom"`、`undefined` 三类入参都返回
  非空 `{title, body}`；body 含传入的 logDir；含 `pnpm setup`；长 stack 被截断到 800 字符内。
- **D3**：四条路由各一条用例（手法见 §三 D3 第 4 点：`_FakeWS` + 直接 await 路由协程 + 往管理器注入假会话）——
  把心跳间隔 monkeypatch 成 0.05s，连上后不产生任何业务事件，断言 0.2s 内收到 ≥1 帧保活帧，
  且该帧的 `type` 分别为 `ping`（三条会话流）/ `heartbeat`（agent），agent 的那帧**不含 `seq`**。
  另加一条**反向**断言：`ws_market` 在同样的静默下**不发任何帧**（把「刻意不做」也钉住，
  否则下一轮会有人「顺手补齐」而把看门狗致盲）。
- **D4**：构造 2050 笔成交 →（a）`snapshot()["trades"]` 长度 == 2000 且末笔是最新一笔；
  （b）**真值列长度仍为 2050**（`executor.trades` / paper 的 `broker.trades`，红线用例）；
  （c）paper 场景下 `_seeded_trade_count = 20` 时，切尾后**不得**有任何记录被误标 `seeded`
  （且前 20 笔已不在窗口内 ⇒ 结果里 `seeded` 恒 false，这正是「先切再 enumerate」会错的地方）；
  （d）前端 `appendAiTradeMemberTrade` / `appendLiveAutoTrade` 追加第 2001 笔后长度恒 2000 且保留最新；
  （e）`AutoTradeTradesTable` 在 2000 笔时出提示、1999 笔时不出；
  （f）`wireCaps.source.test.ts` 断言前后端两个数字相等。
- **D5**：源码扫描用例见 §三；另在 spec 中留下本轮实测的对比度表作为取值依据（可复算）。
  **新增一条**：`--warn` 与 `--warn-soft` 的配对对比度 ≥ 4.5（v2 定稿值 5.07）——这条是 [R-8] 的护栏，
  它必须写在扫描用例的注释里，否则下次有人调浅 `--warn` 时不会有任何东西变红。
- **D6**：见 §三；`Button` 转 forwardRef 后须确认既有全部按钮用例不变绿（G2 全量）。
- **D7**：`FactorSetList.test.tsx` 两个用例改造后仍断言双失效与「取消不发请求」。
- **D8**：`Tabs.test.tsx` 断言 `role="tablist"`、每个 tab 的 `aria-selected`、
  非激活 tab 的 `tabIndex === -1`、按 `ArrowRight` 后 `onChange` 收到下一个 key 且焦点随之移动、
  `End` 跳到最后一个、`ArrowRight` 在末项环回首项。
- **D9**：G4 绿；`git diff` 中 `ci.yml` 的 lint 步骤位于 pytest 之前。

### 8.3 定义完成（DoD）

1. §七 的 **47 + 16** 个文件全部落地，无表外文件、无遗漏（尤其：`ws_market.py` 与 `marketFrames.ts`
   **必须零 diff**——它们是被显式排除的，见 D3 第 5 点）；
2. G1–G8 八道门禁**全部实跑并贴出实测输出**；
3. 实施中发现的任何方案缺陷，就地补写到本文档新增的「实施过程发现的方案缺陷」段；
4. **不得** `git add -A`：逐路径暂存，`docs/diagnoses/`、`ensure-wincodesign*`、
   `live_auto_repositories.py`、`test_factor_library_expansion.py` 等**别人的未跟踪在制文件**必须留在未跟踪；
5. 交易热路径（下单 / 撮合 / 风控 / 换手闸 / 成本对账）**产品代码零 diff**——本轮对它们只做「不裁剪」的承诺，
   任何触碰都视为越界。

---

## 九、风险与缓解

| # | 风险 | 影响 | 缓解 |
|---|---|---|---|
| R1 | D1 的 20s 死线在 `shutdown_action="flatten"` 下取消了半途的平仓 | 仓位半平 | 默认是 `hold`（只写 DB，不下单）；`flatten` 是显式配置；交易所侧兜底自愈 + 启动对账两道网仍在；死线可配 |
| R2 | D3 新增的 `ping` 帧被某个未审计的前端消费者当成业务帧 | 白屏（有先例） | 实施时**逐个复核** 4 个消费者确为白名单；`ws_market` 的 `link` 帧白屏事故是同形先例，必须当场重读 `docs/diagnoses/desktop-white-screen-market-ws-link-frame` |
| R3 | D3 的 agent 改动把 `receive_text` 包进 `wait_for`，取消语义变化 | 断连处理异常 | 照抄 `ws_market.py:94` 已在生产运行的同款写法；`WebSocketDisconnect` 与 `CancelledError` 的既有 except 分支不动 |
| R4 | D4 的成交封顶让用户以为成交丢了 | 信任受损 | 2000 是安全网量级；触到即在 `ChartWorkspace` 横幅与表尾明示；历史全量入口不变 |
| R5 | D4 误裁 `executor.trades` / `broker.trades` | **静默改变交易行为**（换手闸放松、metrics 低报、成本对账失真） | 红线写进 `wire_caps.py` docstring；专门的红线用例（§8.2 D4-b）；code review 逐点核对 §三 的消费点表 |
| R6 | D4 的 paper `seeded` 因切片顺序错位 | 预热成交被误显示为真实成交 | 明确规定「先全表 enumerate 再切尾」；专门用例（§8.2 D4-c） |
| R7 | D8 改 role 后既有用例大面积红 | 实施停摆 | 视为**预期内工作量**，实施节点必须跑全量 vitest 并逐个改用例；**禁止**回退 role 来换绿 |
| R8 | D5 新 token 与既有 `--accent`（赭 `#cc785c`）在暖色系里视觉过近 | 语义混淆 | 选定的 light `#8f5209` 明度显著低于 accent（5.71 vs 3.01 对 `--bg`），且警告面一律配 `--warn-soft` 底色 + 图标，不靠颜色单通道传达（色觉障碍友好） |
| R9 | D9 接入 ruff 后，未来新代码被 F401 挡住合流 | 摩擦 | 规则集刻意只选「一定是缺陷」的两类；假阳最多的 B008 已显式排除并写明理由 |
| R10 | 本轮 47 个文件跨四个子系统，实施节点认知负担大 | 遗漏 | 四个边界彼此**无依赖**，按 §十一 分五批实施，每批各自可跑门禁；批次之间可随时停 |
| R11 | `_run_shutdown` 把 `dispose_engine` 也纳入死线 | 引擎释放被中断 | 它是最后一步，超时也不会影响其它步骤；且 20s 对一次 SQLite 引擎释放是极大冗余 |
| R12（v2 新增） | D4 的 2000 上限在极端长跑会话（多标的 × 数日）被真正触到，两处「全会话」统计的分母随之改变 | 统计口径变化 | **口径必须跟着变**：`AiTradeStatsLive` / `AutoTradePnlPanel` 触上限即改标注（§三 D4-4）；权威全量口径仍在统计看板历史页（REST，不受 wire 封顶影响） |
| R13（v2 新增） | D1 改用 `asyncio.wait` 后，被抛下的超时步骤与后续步骤**并发**跑一小会儿（例如它还持着 DB session 而 `dispose_engine` 已执行） | 关停日志出现噪声异常 | 这是硬死线的必要代价，且严格优于「引擎永不释放」；被抛下的任务已 cancel、其异常会被 warning 记录；关停路径不对外抛 |
| R14（v2 新增） | 有人在未来「顺手」给 `ws_market` 补保活 | **看门狗被致盲**：行情停了却永不重连（比现状更糟） | D3 第 5 点写明理由；§7 表下「明确不在表内」；§8.2 D3 有一条**反向用例**断言 ws_market 静默时不发帧 |
| R15（v2 新增） | `Button` 转 forwardRef 触碰了全应用最热的原语 | 大面积回归 | forwardRef 对不传 ref 的调用方**行为逐字相同**；由 G1（tsc）+ G2（全量 vitest 1811 用例）兜底；该改动与 D6 同批实施，出问题即整批回退 |

---

## 十、明确不做（Out of scope）

1. **`--short` / `--muted` 的对比度整改**：`--short` 在浅色 `--surface-2` 上只有 3.91:1，同样不达 AA，
   但改它会牵动全应用的多空配色与图表 series 颜色，属于一次独立的设计系统轮次。本轮只补**缺失**的 token，
   不动**已存在**的取值。
2. **前端 ESLint 接入**：需先测量 477 个文件上的整改量，见 D9 的说明。本轮只删除跑不起来的假脚本。
3. **9 个 > 800 行的后端文件拆分**（`schemas.py` 2494 行、`ai_auto/session.py` 2213 行……）：
   违反 CLAUDE.md 阈值，但拆分是纯结构性改动、diff 面积巨大、回归风险与本轮的加固目标不匹配，
   应作为独立轮次并配套等价性门禁。
4. **`executor.trades` / `broker.trades` 的内存封顶**：需要先把 `opens` / `tradeCount` / 成本对账窗口
   三处从「由展示缓冲反推」改成「显式计数器」，是一次语义重构而非加固，明确留给下一轮。
5. **pytest 收尾时 aiosqlite 线程的 `RuntimeError: Event loop is closed` 警告**：属测试基础设施
   （每用例引擎未 dispose），不影响判定结果，本轮不动。
6. **`/ws/market` 的空闲保活**（v2 显式补入，[R-7]）：不是遗漏，是**判断**——那条流上静默即证据，
   加保活会把上一轮的 45s 看门狗致盲。若将来要给它「既保活又能发现停摆」，正确做法是让服务端下发
   **带最后一帧行情时间戳**的活性帧、由前端判据接管，那是一次独立设计而不是本轮的一行 `send`。
7. **前端 `member.trades` 统计口径的重构**（把「全会话」统计改为由服务端权威计数下发）：
   本轮用「触上限即改标注」把诚实性守住，但真正的解法是让统计不依赖展示缓冲；与第 4 条同属下一轮。
8. **任何新功能、任何 UI 布局改动、任何策略/风控参数调整。**

---

## 十一、实施顺序建议

```
批次 1（后端进程边界）   #1–#3          → 跑 G3
批次 2（桌面外壳）       #4–#7          → 跑 G5 + G8
批次 3（实时流保活）     #8–#12         → 跑 G3
批次 4（快照与前端封顶） #13–#30        → 跑 G1/G2/G3
批次 5（设计系统与原语） #31–#43        → 跑 G1/G2/G6
批次 6（lint 门禁）      #44–#48        → 跑 G3/G4，最后统一跑 G1–G8
```

（v2 按新表重排：D3 与 D4 拆成两批——D4 现在跨前后端 18 个文件，与 D3 混在一批会让「哪一步把测试跑红了」
不可分辨。）每批结束即可独立验证；批次之间无代码依赖，若某一批被迫回退，其余批次仍然自洽可交付。

---

## 评审结论（Review Verdict）

**有条件通过（Approved with conditions）。**

**为什么是通过而不是打回**：九条缺陷全部有行级取证，评审逐条重读原文件后**一条都没有推翻**；
它们确实是上一轮未覆盖、且与「长时间无人值守运行」这一使用形态直接相关的真问题。方案的技术姿态
（数据驱动步骤表、镜像既有 4 条流的保活写法、复用既有 `Modal`/`ConfirmDialog` 原语、只钉 `F,E9` 两类规则）
与本仓既有约定一致，没有引入新架构层、没有 DB 迁移、没有触碰交易热路径。

**为什么是「有条件」**：v1 有 1 条 P0 + 9 条 P1 是「照着实施会出事」的——其中三条格外值得记住，
因为它们都是**同一种错误的不同形状：修法自身携带了它要消灭的那个缺陷**：
`wait_for` 声称硬死线却不硬（D1）、兜底处理器自己会抛（D2）、为 AA 而生的 token 在自己的配对上不达 AA（D5）。
另有两条是本仓反复复发的老坑：新增 wire 字段没有读点（D4/`tradesTotal`）、
封顶动了别人当真值用的那份数据（D4/前端统计）。**以上 P0/P1 已在 v2 正文就地修完**，
条件是下面这 7 条必须在实施节点被当作硬约束执行：

1. **封顶值就是 2000，不许「顺手」调回 300**。若实施中认为 2000 太大，正确动作是先把
   `AiTradeStatsLive` / `AutoTradePnlPanel` 的「全会话」口径改成显式计数器，再谈调小——
   反过来做就是静默低报（§三 D4 第二条红线）。
2. **`ws_market.py` 与 `marketFrames.ts` 零 diff**，并且必须落地 §8.2 D3 的那条反向用例。
   这条「不做」比本轮任何一条「做」都更容易在未来被好意破坏。
3. **`Button` 转 forwardRef 必须先于 D6 的其余改动落地**，否则 G1 会红在一个与本轮主题无关的地方，
   浪费一次全量跑测。
4. **D4 的三处真值列（`executor.trades` ×2 语义、`broker.trades`）必须有红线用例守着**；
   `git diff` 里出现对这三者的任何裁剪即视为越界，直接回退该批。
5. **G3/G4 的判定一律「排除未跟踪在制文件后」**，且报告里必须写清排除了哪几个文件、各贡献多少条；
   任何对 `docs/diagnoses/`、`ensure-wincodesign*`、`live_auto_repositories.py`、
   `test_factor_library_expansion.py` 的改动都视为违反 DoD-4。
6. **G8 按干净检出基线 19 判定**（不是 27）。若实施节点算出别的数字，先解释差额来自哪个未跟踪文件，
   再动手——数字对不上时改判据是最后一步，不是第一步。
7. **实施中发现的任何方案缺陷就地补写**到本文档新增的「实施过程发现的方案缺陷」段，
   并在完成报告里逐条列出。本轮 v1→v2 的 16 条修订本身就是这条纪律的产物。

**遗留（不阻塞本轮，已写入 §十）**：`--short` 的 AA 整改、前端 ESLint 接入、9 个超长文件拆分、
真值列的内存封顶、以及把「全会话」统计从展示缓冲上解耦。前两条与本轮同属设计系统 / 门禁轴，
后三条需要先做语义重构，都应各自成轮并配套等价性门禁。

> 评审节点：本次评审只改本文件，**未触碰任何源码**，**未执行 `git commit`**。
> 评审期间实跑的命令：`ruff check . --select "F,E9"`（17 errors）、
> `node --test "scripts/*.test.mjs"`（27 pass）、WCAG 相对亮度复算（9 个取值）、
> 以及对 v1 引用的 30+ 处行号的逐条重读。

---

## 实施过程发现的方案缺陷（Issues Found During Implementation · 节点 #2）

按 §评审结论 条件 7 就地补写。以下 7 条均为**实施时实跑撞到**的，不含事后推测；每条给出最终处置。

### I-1（阻断级）· D9 第 4 点的清理顺序会**删掉一个刻意的副作用导入**

spec 写「`--fix` 处理 15 条；手工处理 2 条 F841；另修 `test_engine_regime.py:13` 的非法 noqa」。
但那 15 条**可自动修**里就包含 `test_engine_regime.py:13` 的 `import app.strategies`——它带
`# noqa: F401` 却因为**指令非法**而未被抑制，于是 ruff 把它当成一条 `[*]` 可修错误。
**先跑 `--fix` 就会把这行删掉**，而它的全部意义正是「填充 `STRATEGY_REGISTRY` 的副作用」，
删掉之后注册表静默变空——一个 lint 门禁把生产语义改了，还没有任何东西会红。

**处置**：把顺序倒过来——**先**修非法 noqa（改成注释在上一行 + 行尾裸 `# noqa: F401`），
**再**跑 `--fix`。故实测是「手工 3 条（2 条 F841 + 1 条 noqa）+ 自动 14 条」，
而不是 spec 写的「自动 15 + 手工 2 + noqa 1」（后者对 17 这个总数是重复计数的）。
最终 `ruff check .` → `All checks passed!`，exit 0，且那条 warning 也消失了。

### I-2 · G8 的干净检出基线是 **20** 不是 19（差额来源的条数被数错了）

评审记录说未跟踪的 `desktop/scripts/ensure-wincodesign.test.mjs` 贡献 **8** 条、故干净检出基线 19。
实测该文件单跑是 **7** 条（`node --test scripts/ensure-wincodesign.test.mjs` → `# pass 7`），
本轮改动前的本地总数 27 = 20（已跟踪）+ 7（未跟踪）。

**处置**：按条件 6「数字对不上时先解释差额、再动手」执行——差额来源文件与评审判断一致（就是它），
只是条数少一条。故 G8 判据落到 **20 + 本轮新增 4 = 24**，实测**恰为 24 pass / 0 fail**。
本地 G5 为 31 = 24 + 7，两侧自洽。

### I-3 · `npm run check` 在干净检出副本里**跑不起来**（G8 的 check 部分需换等价手段）

`git archive` 抽出的副本没有 `node_modules`，而 `check` 是 `run-s check:*`，`run-s`（npm-run-all）
是 devDependency ⇒ 直接报「不是内部或外部命令」。这不是本轮引入的问题，但 §8.1 G8 的判据里
没有写它，照字面执行会得到一个假红。

**处置**：G8 的语法门禁改为在副本上逐个跑 `node --check`（12 个受管文件，全绿，含新增的
`src/lib/startup-failure.js`）；`run-s check:*` 的编排本身由真实工作树的 G5 覆盖（13 个 `check:*` 全绿）。

### I-4 · D8 的既有用例波及面比 §七 表大 **2 个文件**，且「一刀切替换」会制造新的红

§七 表下的「受影响但可能需要顺带修正的既有用例」预告了这件事，但没进表。实测受影响的是 **3** 个文件：
`AiTradeStatsPanel.test.tsx`（已在表内 #29，8 处）、`StrategyPanel.test.tsx`（16 处）、
`AiBacktestModal.test.tsx`（6 处），共 33 条用例红。故最终落地文件数为 **65**（= 63 + 2）。

**更值得记的坑**：`StrategyPanel.test.tsx` 里的「多样本回测」**不是页签而是普通按钮**
（同文件另一条用例正是用 `getAllByRole("button")` 断言它在 walk-forward 模式下消失）。
把该文件里的 `getButton(X)` 一律换成 `getTab(X)` 会让那 3 条用例反过来变红——
**页签与按钮必须逐个判定，不能按常量名批量替换**。已按此逐个改正，`role` 一处都没有回退（§9 R7）。

### I-5 · G7 观测到一条**既有的**载荷相关闪红（非本轮引入）

首次 G7（`NODE_ENV` 未设）出现 1 条红：`AiTradeLaunchModal.test.tsx > carries the position step
into onConfirm on the backtest path too`。单文件重跑 **74 passed**；G7 整轮重跑 **169 files /
1858 tests 全绿**，与 G2 逐值相同。该文件在 `NODE_ENV` 未设时会打印 React `act()` 环境警告
（即上一轮已成文的 NODE_ENV 耦合面），叠加全量并发下的时序敏感即偶发。

**处置**：不改该用例（本轮范围外，且改它需要先决定「给全套组件用例统一注入 act 环境」这件事）。
记录于此，供下一轮判断是否值得单独成条——**不要**在下一轮把它误判成本轮 D6/D8 的回归。

### I-6 · `ws_ai_trade` 复用的 `_WS_HEARTBEAT_SECONDS` 是**回测 job 流**的那一个

spec D3 第 1 点说「`api/ai_auto.py` 已有该常量（`:76`），直接复用」——属实，实施照此执行。
但该常量的原注释是「半开探测 **+ 满队列丢终态兜底**，镜像 weight-train」，那是**回测 job 流**
（`ws_ai_backtest`）的语义；会话流 `ws_ai_trade` 现在与它共用同一个旋钮，于是「调 AI 回测的心跳」
会连带改「AI 交易会话的心跳」。今天两者都该是 20s，故无行为问题。

**处置**：本轮不拆（拆常量属表外改动）。若将来要给 job 流单独调参，正确做法是给会话流另立一个
常量而不是把这一个调走——两条流的静默语义本来就不同（job 流的静默可能是终态，会话流的静默是正常态）。

### I-7（阻断级 · 与 I-1 同形的第二个形状）· `ruff --fix` 删掉了一个**对外再导出**，19 条用例转红

I-1 说的是「副作用导入」被删；实跑全量 pytest 后撞到同一把刀的第二个形状：**再导出**。
`app/agent/services.py:34` 的 `from ..strategies.combo_specs import (... validate_combo_params)`
在本模块内**一次都没被调用**，故 F401 判它未使用并 `[*]` 自动删除；但
`app/api/strategies.py:50` 与 `:64` 走的是 `services.validate_combo_params(...)`——
删掉之后 `POST /strategies` 与 `PATCH /strategies/{id}` 在**运行期**抛
`AttributeError: module 'app.agent.services' has no attribute 'validate_combo_params'`。

**实测代价**：全量 pytest 由基线的 7 红（全部来自未跟踪的 `test_factor_library_expansion.py`）
变成 **26 红**——多出的 19 条散落在 6 个文件
（`test_strategies_api` 6、`test_paper_combo` 6、`test_combo_api` 4、`test_walk_forward` 1、
`test_factor_set_strategy_sync` 1、`test_api` 1）。**注意它不是导入期错误**：模块照常 import 成功，
只在那两条写路由被调用时才炸——`ruff check` 绿、`tsc` 绿、前端全绿，唯有跑到那条路由的用例会红。

**处置**：恢复该导入并加 `# noqa: F401` + 三行注释写明它是再导出、以及删掉它的确切后果。
`app/engine/backtester.py:14` 的 `RiskParams` 经核查**不是**再导出（全仓 12 处消费点一律
`from ..engine.simulator import RiskParams` 直连原模块），故那一条删除是安全的，保留删除。

**给 D9 的通用结论（比这两条个例更重要）**：`F401` 的「未使用」是**单文件视角**，
而 Python 的再导出与副作用导入都是**跨文件契约**。因此接入 ruff 时，
`--fix` 对 `app/` 下**应用代码**的每一条 F401 都必须逐条人工核对（本轮 2 条里错了 1 条，命中率 50%），
**且核对之后必须跑一遍全量 pytest 才算完**——`ruff check` 自己永远不会告诉你它删错了什么。
`tests/` 下的 14 条无此风险（测试文件不被别人 import），可放心自动修。

---

## 终审记录（Final Review · 节点 #3）

评审姿态：**逐个文件读完 66 条暂存路径的 diff，并自己重跑门禁**——不采信实施节点的任何转述。
结论：**通过并合入**。§七 的文件计划逐行对得上（47 + 16 + 2 D8 波及 + spec.md = 66），
被显式排除的 `app/api/ws_market.py` 与 `lib/marketFrames.ts` **确为零 diff**，
交易热路径（`live_auto/executor.py`、`ai_auto/discipline_runtime.py`、`paper/broker`）**一行未动**，
`docs/diagnoses/`、`ensure-wincodesign*`、`live_auto_repositories.py`、`test_factor_library_expansion.py`
等别人的在制文件**全部仍在未跟踪**（DoD-4）。

**独立复核的两处高风险前提**（不是重读实施报告，是重读源码）：

1. **[R2] 四个 WS 消费者确为白名单路由**，新增的 `ping` / `heartbeat` 帧落空即返回，无 else 兜底：
   `useAiTradeGroupStream.routeMemberEvent`（switch 无 default）、`useLiveAutoGroupStream.routeMemberEvent`
   （if 链，尾部注释显式说明 candle/error 不在此处理）、`page.tsx` 的 `handlePaperEvent` 与
   `handleLiveAutoEvent`（if 链，最后一条 `candle` 分支还先过 `isUsableCandle` 形状闸）、
   `AgentPanel` 的 `case "heartbeat"`。白屏事故（link 帧被当 K 线）的同形风险不成立。
2. **[R14] 全仓 WS 路由确为 9 条**（`grep -rn "@router.websocket"`），本轮改 4 条、显式不改 `ws_market`。
   重读 `ws_market.py:92-96` 确认它在空闲期一帧不发，反向用例锁的正是这一点。

### 终审改动（4 条，均已就地修完，未留 TODO）

| # | 级别 | 位置 | 问题与修法 |
|---|---|---|---|
| FR-1 | **P1** | `apps/web/app/page.tsx` | `liveTradeCapReached` 拿**合并后**的 `bottomTrades.length` 判截断，而封顶是**按序列**施加的：模拟盘叠加视图把多条策略的成交并成一列，三条各 999 笔合起来就 ≥2000，**一条都没被截断却会弹出「仅保留最近 2000 笔」**。这与 D4 想消灭的「静默低报」是同一枚硬币的另一面——谎报截断与瞒报截断一样伤信任。已改为逐条序列判定：实时/AI 车道各看自己那一条，模拟盘看「可见策略 ∪ 当前选中策略」的每一条。（本仓 `page.tsx` 无用例落点，这一段与它紧邻的 `overlayTrades` 车道链一样靠 `tsc` + 类型守着；新增 page 级测试夹具属表外改动，未做。） |
| FR-2 | P2 | `services/quant/app/main.py` + `tests/test_main_shutdown.py` | `_run_shutdown` 的超时步被 `cancel` 后，若该步把取消**吞成了另一个异常**，那个异常从不被取出 ⇒ GC 时印出一条无主的 `Task exception was never retrieved`：没有步骤名、没有时间点，还常落在别的模块的日志段里。而本函数的全部主张就是「『没停干净』是要被看见的事实」。已在收尾窗口之后取出并按步骤名记 warning，并补一条用例（步骤在 `except CancelledError` 里改抛 `RuntimeError`）钉住它。 |
| FR-3 | P2 | `apps/web/components/ui/index.tsx` | `rovingTabIndex` 的 docstring 写「结果与当前相同则返回 `null`」，而实现对首项按 `Home` 会返回 `0`（= 当前）。**docstring 是契约的一部分**，两者必须一致。已改文档并写明为何实现是对的：那一下仍是页签自己消费掉的按键，放行给页面滚动才是意外行为。 |
| FR-4 | P3 | `tests/test_agent_tools.py`、`test_momentum_breakout.py`、`test_paper_priming.py` | `ruff --fix` 删掉整行导入后留下的双空行（`from __future__ import annotations` 与下一组导入之间）。`E3` 不在本轮选中的规则集里，故门禁不会报——但它是本轮改动留下的、可见的风格噪声。已清。 |

### 终审实跑门禁

| 门禁 | 命令 | 终审实测 |
|---|---|---|
| G1 前端类型 | `apps/web` → `tsc --noEmit` | **exit 0**（含 FR-1 改动后重跑一次） |
| G2 前端单测 | `apps/web` → `NODE_ENV=test vitest run` | **169 files / 1858 tests / 0 fail** |
| G3 后端全量 | `services/quant` → `.venv/Scripts/python -u -m pytest -q` | **7 failed / 3648 passed / 4 skipped / 2 deselected / 738.51s**；7 条红**全部**来自未跟踪的 `tests/test_factor_library_expansion.py`（`git status` 里是 `??`，CI 检出中不存在），故 **CI 视角为 3648 passed / 0 failed**（[R-14] 口径，与实施节点实测逐值一致）。FR-2 的用例在该次全量之后加入，故对受影响的 6 个文件补跑一次：`test_main_shutdown / test_agent_tools / test_momentum_breakout / test_paper_priming / test_ws_idle_heartbeat / test_snapshot_trade_cap` → **49 passed**（CI 视角总数随之为 3649） |
| G4 后端 lint | `services/quant` → `python -m ruff check .` | **All checks passed!**，exit 0 |
| G5 桌面 | `desktop` → `npm run check`；`npm test` | 13 个 `check:*` 全绿（含 `check:startupfailure`）；`node --test` **31 pass / 0 fail** |
| G8 干净检出 | `git write-tree` + `git archive` 抽副本 | `node --test "scripts/*.test.mjs"` **24 pass / 0 fail**（= 已跟踪基线 20 + 本轮新增 4，与 I-2 一致）；12 个受管文件 `node --check` 全绿 |

G6（`pnpm build`）与 G7（不设 `NODE_ENV` 重跑 G2）沿用实施节点的实测，本节点未重跑：
FR-1..FR-4 不触及构建配置，且 FR-1 已由重跑的 G1/G2 覆盖。**这一行是如实标注，不是省略。**
