# Factor Lab Weight Training Fails on Long Date Ranges (socket hang up / ECONNRESET)

> 节点职责：仅做问题定位与根因分析，**不写修复代码、不 git commit**。

## Problem description

用户在 **因子实验室（Factor Lab）→ 因子组合 / 权重训练**（`训练权重` 按钮）训练因子权重时，
**只要选择的时间区间（startTs~endTs）过长，训练就失败报错**；区间短则正常。用户明确表示
**并未关闭桌面端程序**，训练仍然报错。

CODEX 给出的报错样本（前端 / Next 代理日志）：

```text
Failed to proxy http://127.0.0.1:18760/api/factor-sets/train Error: socket hang up
    code: 'ECONNRESET'
```

并在同批日志「附近」看到：

```text
[process-manager] web exited (code=1, signal=null)
[process-manager] backend exited (code=1, signal=null)
```

`main.log` 中**多次**出现：

```text
收到退出信号，正在停止前后端 …
前后端已停止。
```

CODEX 的推断是「桌面端关闭 / 重启打断了请求」。**本报告的结论与之不同**：这并非用户关闭程序，
而是 `/api/factor-sets/train` 是一条**无界的长时阻塞请求**——区间越长，串行取数与训练耗时越长，
最终被链路里的某个传输层超时 / 进程生命周期事件**重置连接**（ECONNRESET），在「请求未返回」时
表现为 `socket hang up`。下文给出证据与定位。

## Reproduction steps

### 路径（UI 复现）

1. 打开桌面端 Aragon Trading（或 `apps/web` 开发态），进入左侧 **因子实验室**。
2. 顶部工作台条选择标的（如 `BTC-USDT-SWAP`）、周期 `bar`（默认 `1H`），并把
   **「区间」起止日期拉到一个很长的跨度**（例如 `1H` 周期下选 2~3 年；或 `15m` 周期下选数月——
   只要 `(endTs-startTs)/bar` 的根数足够大即可）。区间控件见
   `apps/web/components/Factor/FactorLab.tsx:89-90` 与日期输入 `FactorLab.tsx:194-203`。
3. 进入「因子组合 / 权重训练」工作区，添加 1~12 个因子成员，选训练方法（`linear` 默认，
   或 `random_forest` / `xgboost`），点击 **「训练权重」**（`FactorSetBuilder.tsx:360-361` →
   `train.mutate()` → `factorApi.train({...range, members, method, config})`，见
   `FactorSetBuilder.tsx:205-207`，**range 取自工作台、未带 maxBars**）。
4. 观察：短区间秒级返回 `200`；**长区间挂起数十秒~数分钟后**，前端 `训练失败：...`，
   桌面端日志出现上文 `Failed to proxy ... socket hang up / ECONNRESET`。

### 关键事实：UI 不发送 `maxBars`，后端默认取数上限 = 20000

- 前端 `factorApi.train` 只传 `instId/bar/startTs/endTs/members/method/config`，**不传 `maxBars`**
  （`apps/web/lib/factorApi.ts:142-149`）。
- 后端 `FactorTrainReq.maxBars` 默认 `None`（`services/quant/app/schemas.py:982-987`）。
- 路由 `_window(body)` 把 `max_bars=None` 透传（`services/quant/app/api/factors.py:41-43, 201-207`）。
- 故取数上限回落到 `settings.backtest_max_bars`，**默认 20000**（`services/quant/app/config.py:115`）。

### 后端层最小复现（演示「无界取数随区间线性放大 + 缺 413 守卫」）

下述脚本用 FakeRest（不连真实 OKX）统计 `get_candles` 调用次数，证明：长区间下
`service.train` 会一路翻页到 `backtest_max_bars` 上限，**没有任何 workload 守卫提前拒绝**
（与 `mine` 形成对照）。在 `services/quant` 目录运行：

```powershell
@'
import asyncio, os, tempfile, uuid
from pathlib import Path
os.environ["DB_PATH"] = str(Path(tempfile.gettempdir()) / f"aragon_train_repro_{uuid.uuid4().hex}.db")
os.environ["ALLOW_CACHED_FALLBACK"] = "false"

from app.factors import service
from app.factors.service import FactorWindow
from app.okx.types import Candle

class FakeRest:
    """每页最多返回 100 根（仿 OKX history-candles），统计请求次数。"""
    def __init__(self, total=200000, step=3600):
        self.calls = 0
        base = 1_500_000_000
        self.rows = [Candle(ts=base + i*step, o=100.0, h=101.0, l=99.0,
                            c=100.0 + (i % 50) * 0.1, vol=1000.0) for i in range(total)]
    async def get_candles(self, inst_id, bar, limit=300, before=None, after=None):
        self.calls += 1
        rows = [r for r in self.rows if before is None or r.ts < before]
        return list(reversed(rows[-min(100, len(rows)):]))   # OKX 单页 ≤100

async def main():
    fake = FakeRest()
    bar_sec, base = 3600, 1_500_000_000
    members = [{"factorKey": "rsi", "params": {}, "weight": 0.0, "direction": "long"}]
    for label, span_bars in [("short=300bars", 300), ("long=30000bars", 30000)]:
        fake.calls = 0
        service.clear_candle_cache()
        win = FactorWindow(inst_id="BTC-USDT-SWAP", bar="1H",
                           start_ts=base, end_ts=base + span_bars * bar_sec)
        try:
            res = await service.train(fake, win, members=members, method="linear",
                                      config={}, horizon=6)
            print(label, "-> rest_calls =", fake.calls,
                  "| nTrain+nValid =", res["metrics"].get("nTrain"), "+", res["metrics"].get("nValid"))
        except Exception as e:
            print(label, "-> rest_calls =", fake.calls, "| raised:", type(e).__name__, e)

asyncio.run(main())
'@ | .venv/Scripts/python -
```

预期观察：`short` 仅数次 `get_candles`；`long` 的 `get_calls` **数量级跃升并逼近
`ceil(20000/100)≈200+` 次串行请求**，且**全程无 `413` / 提前拒绝**。把每次请求换成真实
OKX（限频 0.12s + 网络 RTT + 偶发 429/重试），这 200+ 次串行往返即把单请求拖到数十秒~数分钟，
正是「区间越长越容易失败」的根。

> 注：本工作区当前可能未安装 `services/quant/.venv`（任务 working dir 为运行态目录）。脚本目的
> 在于把「无界取数随区间放大、且 train 无 workload 守卫」做成可执行验证；若 venv 不在场，仍可
> 直接以下文代码引用核对同一结论。

## Root cause analysis

### 主根因：`/api/factor-sets/train` 是无界的长时同步请求，长区间下耗时不可预测

调用链（浏览器 → Next → FastAPI → OKX）全程**只占用一条 HTTP 连接、同步等待返回**：

1. 前端 `fetch` **无超时、无 AbortController**（`apps/web/lib/factorApi.ts:23-28`，仅
   `cache:"no-store"`）。浏览器对这种 fetch 不会主动超时。
2. Next rewrite 把 `/api/py/*` 透传到后端 `/api/*`（`apps/web/next.config.mjs:15-22`）。
3. 后端 `train_factor_set`（`services/quant/app/api/factors.py:201-207`）→ `service.train`
   （`services/quant/app/factors/service.py:217-237`）。
4. `service.train` 先 `_load_df`（`service.py:224` → `service.py:86-108`）→
   `load_range_candles`（`services/quant/app/agent/services.py:81-110`）。
5. `load_range_candles` 计算：
   - `ceiling = settings.backtest_max_bars`（=20000，`agent/services.py:98` + `config.py:115`）；
   - `expected = (end_ts - start_ts)//bar_sec + 1`（`agent/services.py:101`）；
   - `target = min(expected, ceiling)`（`agent/services.py:102`）。
   **`target` 随区间长度线性增长，直到撞 20000 上限。**
6. `_page_candles_back`（`agent/services.py:47-78`）以
   `pages_cap = max(1, target // _PAGE_CHUNK + 3)`（`agent/services.py:65`，`_PAGE_CHUNK=1000`，
   `agent/services.py:38`）**串行翻页**；每次外层 `get_candles(..., 1000, before=cursor)`
   在 `rest_client.py` 内部又按 history-candles **每页 ≤100 根再翻 ≤12 次**
   （`rest_client.py:156-184`，`max_pages=limit//100+2`）。即 `target=20000` 时
   **最坏 ≈200+ 次串行 OKX REST 请求**。
7. 每次请求受限频 `0.12s` 最小间隔（`rest_client.py:38-52`）+ 网络 RTT（经本机 Clash 代理到 OKX，
   `config.js` proxyEnv 透传）+ httpx 读超时 `20s`/连接 `10s`（`rest_client.py:75-83`）+ 失败
   重试 3 次指数退避（`rest_client.py:90-110`）。**长区间的总墙钟时间因此累计到数十秒~数分钟。**
8. 取数完成后还有 CPU 训练（`build_factor_matrix` + `train_weights`，
   `factors/service.py:225-228`、`engine/factors/training.py:257-298`），经 `run_in_threadpool`
   下放线程池（`service.py:225, 243`）。线程池不阻塞事件循环，故后端**仍能应答 health/其它请求**
   ——与 CODEX 观察「后端随后又多次 200 OK」一致。

**这条请求没有任何「时长 / 工作量上界」**：既无服务端 workload 守卫，也无后台任务 / 进度 / 取消
模型。请求能否返回，取决于链路里**最先触发的那个底层超时或生命周期事件**（Node HTTP server 的
默认 `requestTimeout`、操作系统 / 代理层超时、或桌面端任何一次后端重启）。长区间下其中之一必然
先于「训练返回」触发，**把上游 socket 重置**，Next 即记为
`Failed to proxy ... socket hang up / ECONNRESET`。

### 为什么是 ECONNRESET（传输层），而不是 400/500（应用层）

- 若是**取数失败**（OKX 不可达 / 业务码非 0），`_request` 抛 `OKXError`
  （`rest_client.py:103-110`），经 `okx_error_handler` 返回 **502**
  （`services/quant/app/main.py:85-91`）——是结构化错误体，不是 socket hang up。
- 若是**样本不足 / 未知方法**，`_train_weights_guarded` 收敛为 **400 `FACTOR_TRAIN_FAILED`**
  （`factors/service.py:239-246`）；缺 rf/xgb 库则 **400 `FACTOR_TRAINER_UNAVAILABLE`**
  （`factors/service.py:229-230`，且 rf/xgb 还有内置兜底恒可用，`training.py:199-207`）。

CODEX 已确认日志中**没有**这些错误码 / 4xx/5xx。**唯一能产生「连接被重置且无应用层错误体」的情形，
就是请求在返回前连接被传输层切断**——即长请求超时 / 进程被打断。这与「短区间 200 OK、长区间
socket hang up」完全自洽。

### 对 CODEX「桌面端关闭/重启打断」推断的修正

CODEX 把 `web/backend exited (code=1)` 与 `收到退出信号` 当作「请求被进程生命周期打断」的证据。需澄清：

- `[process-manager] {name} exited (code=...)` 是受管子进程退出的**通用日志**
  （`desktop/src/lib/process-manager.js:369-375`）。Windows 上 `taskkill /F` 收尾的子进程退出码
  正是 `1`、`signal=null`，故 `code=1` 既可能是崩溃、也可能是被强杀。
- `收到退出信号，正在停止前后端 …` / `前后端已停止` 只在 `gracefulQuit()` 打印
  （`desktop/src/main.js:493-511`），而 `gracefulQuit` 由 `before-quit` 触发——**不止用户关窗**，
  `app.quit()`（启动失败、`window-all-closed`）与**主进程 `uncaughtException`**
  （`main.js:569-572`，随后 `app.exit(1)`）都会进入它。CODEX 自陈这些行在 `main.log`
  「**多次**出现」，说明它们横跨**多次 App 会话**，**与本次训练失败并非必然同一时刻**——属于
  「就近相关」而非因果。
- 真正会因后端意外退出而立即重置 in-flight 连接的是 `onUnexpectedExit` 路径
  （`main.js:396-407`，且会弹「Aragon 引擎已停止」对话框）；它**不会**打印「收到退出信号」。

因此「用户没关程序、训练仍报错」与证据并不矛盾：**根因是无界长请求被传输层/生命周期切断，
不依赖用户主动关闭程序。**

### 与既有诊断的同构性（佐证）

`docs/diagnoses/multi-strategy-combo-compare-backtest-timeout/analysis.md` 已对**同一类问题**定性：
`/api/strategies/compare` 是「同步请求、工作量随数据规模放大、无后台任务/取消/降采样、长区间逼近
`backtest_max_bars=20000` 即超出调用方/代理交互窗口」（见该文 Root cause 与
`Impact surface` 的 “Long date ranges, especially near the backtest_max_bars ceiling”）。
`/api/factor-sets/train` 是**完全同构**的一条同步长请求，且**比 compare 更缺保护**——见下。

### 可定位的代码缺陷：train 缺 workload 守卫（mine 有，train 没有）

同在 `factors/service.py`：

- `mine` 在取数前先 `_guard_mine_workload(...)`：候选数 × 有效根数超 `MINE_WORK_UNIT_LIMIT`
  即抛 **413 `FACTOR_MINE_TOO_LARGE`**（`factors/service.py:182-213`，守卫体 `service.py:202-213`），
  且其 `ceiling` 也夹取 `backtest_max_bars`（`service.py:206-208`）。
- `train`（`factors/service.py:217-237`）**直接 `_load_df`，没有任何工作量 / 根数上界检查**，
  也不向前端回传「区间过长 / 已截断」。这是「长区间训练无界拖死」最直接、最可定位的落点。

## Impact surface

- **受影响功能**：因子实验室 → 因子组合 / 权重训练（`训练权重`，所有训练方法 `equal / ic_weight /
  linear / random_forest / xgboost`，因为瓶颈在取数与请求时长，与具体训练器无关）。
- **触发条件**：`(endTs - startTs) / bar` 折算根数偏大（低周期如 `1m/15m` 更易触发，因同样日历
  跨度根数更多）；尤其逼近 `backtest_max_bars=20000` 时。
- **同根因可能波及**：`/api/factor-sets/score`、`/factor-sets/score-preview`、`/factors/compute`、
  `/factors/evaluate`、`/factors/mine` 等同样走 `_load_df` 的长区间请求；其中 `mine` 有 413 守卫，
  其余无（`compute/evaluate/score` 至少接受 `maxBars` 入参但 UI 默认不传）。
- **用户面**：桌面端与浏览器端行为一致（同走 `/api/py/*` 代理 → 同一后端）。表现为「训练失败」
  且错误信息是底层 `socket hang up`，**对用户不可读、不可诉**（无产品级错误码）。

## Candidate fixes

| 方案 | 概述 | 侵入性 | 风险 | 工作量 |
|---|---|---:|---:|---:|
| A | **服务端 workload 守卫**：在 `service.train` 取数前，仿 `mine` 的 `_guard_mine_workload`，按 `min(expected, backtest_max_bars)` 估算根数，超出同步训练预算即 **413/400 结构化错误**（`{code:"FACTOR_TRAIN_TOO_LARGE"}`）；前端把该码渲染成「区间过长，请缩短区间或调高上限后重试」。 | 低 | 低 | 低 |
| B | **取数/请求提速 + 透明截断**：复用 service 内 K 线缓存（`_load_df` 已有 TTL/LRU，`service.py:86-108`），并在响应里回传 `effectiveBars/requested/truncated`（对齐 backtest `truncated` 语义）；前端显示「仅覆盖最近 N 根」。降低墙钟时长、消除静默截断。 | 中 | 中 | 中 |
| C | **改为后台任务 + 进度 + 取消 + 轮询**：`train` 立即返回 `jobId`，后台执行、前端轮询结果，彻底摆脱「单请求长占连接」。 | 高 | 中-高 | 高 |
| D | **桌面/代理层**：给训练这类长请求显式放宽并显式封顶超时（如 Next/Node `server.requestTimeout`、前端 AbortController + 友好超时提示）。仅治表（仍是无界长请求），需与 A 合用。 | 低-中 | 中 | 低-中 |

## Recommended fix

**首选 A（必须），叠加 D 的前端友好化（建议），并把 B 的「缓存复用 + 透明截断」作为下一步。**

理由：

1. **A 直接消除根因的可触达性**，且与代码库既有约定同构——`mine` 已用同样的 413 守卫范式
   （`factors/service.py:202-213`），`compare` 诊断的推荐方案第 5 条也明确要求「基于展开工作量的
   服务端 workload 守卫，超预算即 fail fast 给出清晰产品错误，而不是任由请求跑到外部超时」。把
   train 补齐该守卫，blast radius 最小、与现有错误体（`{error:{code,message}}`，`main.py:116-125`）
   一致，能把「不可读的 socket hang up」转成「可读、可诉的区间过长提示」。
2. **D 补足体验**：前端 `factorApi` 当前无超时（`factorApi.ts:23-28`），可加 AbortController +
   超时文案，避免长请求时 UI 无限「训练中...」；但 D 单独不治本（请求仍无界），必须与 A 合用。
3. **B 是性价比之选但非最小修复**：缓存复用与 `truncated` 透明回传能显著降低长区间墙钟时长并消除
   静默截断，建议紧随 A 落地。
4. **C 是长区间训练的正确终局架构**（后台任务/取消/进度），但改动 API 契约与 UI 状态模型，**不应
   在最小修复里实施**；待同步路径被 A 收口、可预测后再演进。

> 修复落地点（供 Subtask #2 参考，本节点不写代码）：核心在
> `services/quant/app/factors/service.py:217` 的 `train` 取数前新增 workload 守卫（复用
> `backtest_max_bars` 夹取与 413 语义）；前端在 `apps/web/components/Factor/FactorSetBuilder.tsx`
> 训练失败分支识别新错误码并给出可读文案；可选在 `apps/web/lib/factorApi.ts` 增加超时。

## Verification next step（建议下一节点先取真实日志以最终判别「超时切断 vs 后端崩溃」）

本报告基于代码静态链路定位；要在两个传输层切断子假设（① 链路超时重置 ② 后端进程意外退出）之间
做最终判别，建议下一节点收集运行态日志（打包态写在 `userData/logs/`，见 `desktop/src/main.js:35-41`）：

- 复现长区间训练失败的**那一时刻**，对照 `backend.log` 是否在 `socket hang up` 前出现 Python
  traceback / 退出（→ 指向 ② 崩溃），还是后端无任何错误、仅前端 `Failed to proxy`（→ 指向 ① 超时切断）。
- 不论 ① 还是 ②，**修复方向一致**：A 的 workload 守卫先把请求收口为有界，即可同时规避两类切断。

## 评审结论（Review Verdict）

> 本节为评审节点（Subtask #1）追加：对上文根因证据与候选方案逐条复核，补强缺失引用，并锁定**单一**推荐修复。
> 评审方式：对报告引用的每个 `file:line` 落点逐一打开核对（非仅信其文字）。

### 1. 根因证据复核：通过（evidence chain is airtight）

逐文件核对，报告引用与实际代码**实质一致**（个别行号有 ±1~2 漂移，不影响结论）：

- **train 缺 workload 守卫（主缺陷）——确认**：`service.mine` 在取数前先调 `_guard_mine_workload`
  （`services/quant/app/factors/service.py:187`），而 `service.train`（`service.py:217-236`）**直接**
  `df = await _load_df(rest, window)`（`service.py:224`），其间无任何根数 / 工作量上界检查。这是「长区间
  训练无界拖死」最直接、可定位的落点。
- **无界取数随区间线性放大——确认**：`load_range_candles` 取 `ceiling=backtest_max_bars`、
  `expected=(end-start)//bar+1`、`target=min(expected,ceiling)`（`services/quant/app/agent/services.py:98-102`）；
  `backtest_max_bars` 默认 **20000**（`services/quant/app/config.py:115`）；UI 不传 `maxBars`
  （`apps/web/components/Factor/FactorSetBuilder.tsx:202,205-207` 仅传 `range+members+method+{normWindow}`；
  `apps/web/lib/factorApi.ts:142-149` 无 `maxBars`），故 `target` 一路放大至 20000。
- **20000 根 ≈ 200+ 次串行 OKX 请求——确认**：外层 `_page_candles_back` 翻页（`agent/services.py:65`，
  `pages_cap=target//1000+3`），内层 `get_candles` 走 history-candles 每页 ≤100、`max_pages=limit//100+2`
  （`okx/rest_client.py:160`）；取满 20000 distinct 根至少需 `20000/100≈200` 次内层请求。
- **量化下界补强（评审新增）**：`_RateLimiter` 最小间隔 **0.12s**（`okx/rest_client.py:41`）且为单客户端串行，
  故**仅限频一项就给 20000 根取数设下 ≥ `200×0.12s ≈ 24s` 的墙钟地板**，叠加每请求 RTT（经 Clash 代理到
  OKX）、httpx 读超时 20s / 连接 10s（`rest_client.py:79`）、429/网络失败 3 次指数退避（`rest_client.py:94-110`）
  后，长区间累计至数十秒~数分钟——与「区间越长越易失败」严格自洽。此地板值是「无界长请求」的硬证据，
  与具体训练器无关。
- **ECONNRESET（传输层）而非 4xx/5xx（应用层）——确认**：取数失败 → `OKXError` → 502（`main.py:85-91`）；
  样本不足 / 未知方法 → `ValueError` 收敛 400 `FACTOR_TRAIN_FAILED`（`service.py:245-246`，
  `engine/factors/training.py:287-288`）；rf/xgb 因内置树后端**恒可用**（`training.py:195-207,213-215`），故
  `FACTOR_TRAINER_UNAVAILABLE` 现实中几乎不触达。三类应用层错误都会返回**结构化错误体**而非 socket 重置——
  CODEX 已证实日志中无这些码，故唯一自洽解释即「请求返回前连接被传输层切断」。
- **对 CODEX「关程序打断」的更正——确认成立**：`收到退出信号 …` 仅在 `gracefulQuit` 打印
  （`desktop/src/main.js:497`），其入口为 `before-quit` / `app.quit()` / 主进程 `uncaughtException→app.exit(1)`
  （`main.js:562-572`）；而真正会**即时重置 in-flight 连接**的后端意外退出走 `onUnexpectedExit`
  （`main.js:396-407`），弹的是「Aragon 引擎已停止」对话框、**不打印**「收到退出信号」。故 `main.log` 中
  「多次」出现的「收到退出信号」是**跨会话的正常退出**痕迹，属就近相关而非本次训练失败的因果。

**结论：根因证据链成立、可复核，无需推翻。** 主根因为「`/api/factor-sets/train` 是无界长时同步请求，
长区间下取数墙钟超出链路传输层窗口而被切断」，且 `train` 相对 `mine` **缺 workload 守卫**这一缺陷可精确定位。

### 2. 候选方案复审与一处关键更正

**对 Fix A 的关键更正（务必转交 Subtask #2，否则极易实现成「假护栏」）：**
报告 Fix A 写「仿 `mine` 的 `_guard_mine_workload`」。直接照搬会**失效**——`_guard_mine_workload` 比较的是
`mining_workload(len(candidates), effective_bars) = 候选数 × 有效根数`（`engine/factors/mining.py:57-59`）对阈值
`MINE_WORK_UNIT_LIMIT = 3_000_000`（`mining.py:17`）。`mine` 的护栏主要咬在**候选维度**（有效根数已被夹到
≤20000，需 >150 个候选才越 3M）。而 **`train` 没有候选维度**：≤12 个成员被合成**一张矩阵**
（`service.py:225-226` `build_factor_matrix`），`12 × 20000 = 240,000 ≪ 3,000,000`，照搬即**永不触发**。

因此 train 守卫**必须改用纯「有效根数」度量**：

- 度量 = `effective_bars = max(0, min(expected, backtest_max_bars))`（与 `_guard_mine_workload:205-209` 同口径
  夹取，含 `max_bars` 二次夹取与 `expected≤0` 归零）。
- 阈值 = **一个新的、命名的、可经环境变量覆盖的「同步训练根数预算」，且必须严格小于 `backtest_max_bars`（20000）**。
  这一点最易被忽略：若预算 ≥ 20000，因 `target` 早已被夹到 20000，护栏是**纯 no-op**。建议新增形如
  `factor_train_max_bars`（沿用 `config.py:115` 的 `Field(default=…, ge=…)` + 环境变量命名约定），保守默认在
  **6000~10000** 区间起步（依上文限频地板：8000 根 ≈ 80 次请求 ≈ ≥10s 限频地板 + RTT，落在多数链路超时窗口内）。
- 超限抛 **413** `{"code":"FACTOR_TRAIN_TOO_LARGE","message":"训练区间过长，请缩短区间（或调高上限）后重试。"}`，
  与既有错误体 `{error:{code,message}}`（`main.py:116-125`）一致。

**前端改动几乎为零（评审新增证据）**：`factorApi.request` 已把 `body.error.code/message` 解包进 `ApiRequestError`
（`apps/web/lib/factorApi.ts:29-43`），`FactorSetBuilder` 训练失败分支已渲染 `训练失败：{message}`
（`FactorSetBuilder.tsx:366-368`）。故 413 的可读 `message` **会自动透传上屏**；是否再针对 `FACTOR_TRAIN_TOO_LARGE`
码做更友好排版属可选润色，**非最小修复必需**。

**逐方案裁决：**

- **A（服务端根数守卫）——采纳为主修复。** 边界/并发/边缘输入复核通过：守卫是取数前纯前置检查，无 DB 副作用
  （`train` 不 commit）；`expected≤0`、`startTs>endTs`、未传 `maxBars`（→ceiling=20000）均被 `max(0,min(...))`
  收敛，越界后由下游 `EMPTY_RANGE`(400) 兜底。**唯一须把控的回归**：阈值若设得过低，会把**当前能成功**的中等
  区间（如约 10s 内返回者）误拒——这正是「阈值须命名 + 可配 + 保守起步、后续按运行态日志校准」的理由。
- **B（缓存复用 + 透明截断）——下一步，非最小修复。** `_load_df` 已有 TTL/LRU 缓存（`service.py:86-108`），叠加
  响应回传 `effectiveBars/requested/truncated` 可降墙钟并消除静默截断；性价比高但改响应契约，紧随 A。
- **C（后台任务 + 进度 + 取消）——正确终局，暂不在最小修复实施。** 改 API 契约与 UI 状态模型，待 A 收口后演进。
- **D（前端 AbortController/超时 + 服务端显式封顶超时）——降级为可选防御。** A 落地后请求即有界、快速返回 413，
  D 原本要消解的「无限训练中…」长挂起已消失；D 仅对「预算内但仍偏慢」区间有 UX 价值，**不属最小修复**。报告把 D
  列为「建议」，评审下调为「可选润色」。

> 补充：采「413 fail-fast」而非「静默把 train 的 `maxBars` 调小做隐式截断」，与本代码库通篇「绝不静默
> （少算/降级/假数据）」的既定约定一致（`service.py:78,159-163`、`agent/services.py:107-109`、
> `training.py:262-264` 等多处明示），亦与 `mine`(413) / `compare` 诊断推荐的「服务端 workload 守卫 fail-fast」
> 同构。这是 A 优于 B-截断 的文化与一致性依据。

### 3. 最终推荐修复（single recommended fix）

**唯一推荐：方案 A——在 `services/quant/app/factors/service.py` 的 `train` 取数（`_load_df`，service.py:224）之前，
新增一个基于「有效根数」的 workload 守卫，超出「同步训练根数预算」即抛 413 `FACTOR_TRAIN_TOO_LARGE`。**

落地要点（交付 Subtask #2 的硬约束）：

1. **度量用 `effective_bars`，不得用 `mining_workload`**（train 无候选维度，照搬必成 no-op）。
2. **新增独立阈值 `factor_train_max_bars`（可经环境变量覆盖），且严格 `< backtest_max_bars(20000)`**；保守默认
   6000~10000 起步，最终值依「Verification next step」收集的运行态日志（确认真正先触发的传输层窗口）回校。
3. **413 结构化错误**，复用现有 `{error:{code,message}}`；前端无需改动即可经
   `factorApi.ts:29-43`→`FactorSetBuilder.tsx:366-368` 显示可读文案（码级润色可选）。
4. **范围最小**：不改 API 形状、不引依赖、不动 `mine/score/evaluate/compute` 现有行为；B（缓存+透明截断）、
   C（后台任务）、D（前端超时）列为后续，不在本次最小修复内实施。

**理由一句话**：A 以最小 blast radius 把「无界长请求」收口为「有界、可预测、对用户可读可诉」的快速失败，既消除根因的
可触达性，又与代码库既有 413/「绝不静默」范式同构；其余方案要么改契约（B/C）要么治标（D），均不应混入最小修复。
