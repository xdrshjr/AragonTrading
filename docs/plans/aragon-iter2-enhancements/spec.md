# Aragon Trading — 迭代 2 增强方案 (spec, iteration 2)

- **Feature**: `aragon-iter2-enhancements`
- **文档版本**: v2（迭代 2 · 已评审修订 · Subtask #1）
- **作者 / 角色**: Solution Architect (Anthropic Engineering)
- **创建日期**: 2026-06-12
- **迭代**: Iteration 2 / 3
- **全局目标**: 完成项目开发，确保可用，数据真实可用
- **基线**: 迭代 1 已交付并提交（commit `414c28c`）的 monorepo（`apps/web` Next.js 14 + `services/quant` FastAPI），其设计见 `docs/plans/aragon-trading-platform/spec.md`（v2，已评审）。
- **关系声明**: 本文档**只规划在迭代 1 基线之上的增量增强**，不重述、不推翻 v2 的架构、接口语义、数据模型与 5 策略逻辑。v2 的 5 条放行条件（真实数据无假数据 / 凭据不入库 / 秒级 UTC ts / 锁 `lightweight-charts@^4.2` / SimBroker·VWAP·注册表合并）在本迭代中**继续生效，不得回退**。

---

## 评审记录（Review Notes · Subtask #1）

- **评审人 / 角色**：Senior Reviewer（Anthropic Engineering）
- **评审日期**：2026-06-12
- **评审方式**：逐文件核对迭代 1 已交付源码（`services/quant/app/{api,okx,paper,db,engine,strategies}/*`、`apps/web/{lib,components/Chart,components/Strategy}/*`、`pyproject.toml`）后，按**可行性 / 完整性 / 一致性 / 规模适配**四维评审，并校验是否回退 v2 五条放行条件。
- **速览结论**：架构方向正确，**纯增量**，未触碰 5 策略、SimBroker 撮合内核与既有 DB schema；复用的契约（`run_backtest`、`PaperSession.feed_candle`、`repositories.create_run/persist_backtest_result`、`run_out`、`get_rest_client().get_candles`、`upsert_candles`、注册表合并）经核对**全部真实存在且签名匹配**。发现 **P0 = 0，P1 = 4，P2 = 5**。下列 **P1 已在本 v2 正文就地修复**；P2 已修复 4 项，剩余 1 项（C8）列入“评审结论”条件。
- 五条放行条件（真实数据无假数据 / 凭据不入库 / 秒级 UTC ts / 锁 `lightweight-charts@^4.2` / SimBroker·VWAP·注册表合并）**未回退**；F5 回放数据全部取自真实 OKX 历史，不合成 K 线，符合“数据真实可用”。

| # | 维度 | 严重度 | 位置 | 问题 | 处置 |
|---|---|---|---|---|---|
| C1 | 可行性 / 一致性 | **P1** | §4.3 CandleChart | 对比净值已归一化为百分比，却规定挂“**右价轴**”——而右价轴是 K 线绝对价格刻度（BTC ≈ 6w+）。百分比（约 `[-0.1, 0.3]`）被压在 6 万量级的刻度上会渲染成贴底直线，F3 的核心可视化（多策略净值叠加对比）实际不可读。 | **已修复**：改用**独立归一化刻度**（左价轴 / 专用 `overlay` priceScaleId，与 K 线右价轴隔离）；`compare` 模式清空单策略净值线避免刻度冲突。见 §4.3。 |
| C2 | 完整性 / 错误路径 | **P1** | §4.5 / §5.2 / §6.1 | 回放节奏 `await asyncio.sleep(0.2/speed)`：`speed` 仅声明“下限 0”、未做 schema 校验，`speed=0` 时**除零崩溃**；`replayBars` 未设上界，> 600 会溢出 `_WINDOW_MAX=600` 滚动窗、丢早段 K 线。 | **已修复**：`source: Literal["live","replay"]="live"`、`speed: float = Field(1.0, gt=0, le=64)`、`replayBars: int = Field(300, ge=10, le=600)`；越界由 FastAPI 入参校验返回 422（与既有 `BacktestReq` 同机制）。见 §4.5 / §6.1。 |
| C3 | 完整性 / 数据一致性 | **P1** | §4.5 / §9-R3 | 回放结束**自动 `stop()`**，而用户手动 `POST /paper/{sid}/stop`、`PaperManager.shutdown` 兜底也会进入 `stop()`；现有 `PaperSession.stop()` **无幂等保护**（不判 `state`），二次进入会**重复 `broker.finalize` + 重复落库**，造成结算与净值/成交重复。live-only 时此路径不触发，F5 自动停止把它变成常态路径。 | **已修复**：`stop()` 增 `if self.state != "running": return` 幂等保护；回放后台任务存 `self._replay_task`，`stop()` 内先 `cancel()+await` 再结算。见 §4.5 / R3。 |
| C4 | 完整性 / 一致性 | **P1** | §4.2 / §8.1 / §5.2 | 文称网络测试“默认随 `-m network` 才跑（不污染离线 CI）”，但 pytest **默认不会**排除带标记用例；当前 `pyproject.toml` 仅有 `filterwarnings`、**无 `addopts`**，裸跑 `pytest` 会执行真实 OKX 网络用例并在离线环境失败，**违背验收 #7“后端 pytest（含新增、不含 network）”**。此外既有 `pyproject` 并未注册 `network` marker，“与既有 marker 一致”表述不准确。 | **已修复**：`pyproject` 增 `addopts = "-m 'not network'"` 实现默认排除 + 注册 `network` marker；opt-in 用 `pytest -m network`。见 §4.2 / §5.2 / §8.1。 |
| C5 | 一致性 | P2 | §5.3 | `Mode` 仅定义在 `lib/types.ts`，`lib/store.ts` 是 `import type { Mode }` 的**消费者**；变更表“`store.ts` 修改：`Mode` 增 `compare`”是无效改动。 | **已修复**：更正变更表——`Mode` 单一来源在 `types.ts`，`store.ts` 无需为 `Mode` 改动（默认值仍 `"backtest"`）。 |
| C6 | 规模适配 / 可测性 | P2 | §5.3 | 新增 `lib/compare.test.ts` 却无被测的**纯函数模块**；既有约定是 `lib/format.ts` + `lib/format.test.ts`（纯函数 + 同名测试）。把排序/归一化逻辑埋进 `CompareView.tsx` / `CandleChart.tsx` 组件里则无法被 `.test.ts` 单测。 | **已修复**：新增 `lib/compare.ts` 承载榜单排序键与 `equity/initial-1` 归一化纯函数，组件改为消费它；`compare.test.ts` 测该模块。 |
| C7 | 完整性 | P2 | §4.2 | `wsConfigured:bool` 语义未定义；`config.okx_ws_public` 有默认值（`wss://…/ws/v5/business`），该字段恒为 true、无信息量。 | **已修复**：明确 `wsConfigured = bool(get_settings().okx_ws_public)`，表征“上游 OKX WS 端点已配置”，并在文中标注其在默认配置下通常为 true。 |
| C8 | 规模适配 | P2 | §4.4 | 每次“运行对比”为每个被比策略落一行 `mode="backtest"` run（≤ 12 行 / 次），F4 历史列表会被对比产生的 run **淹没**；且历史 Tab 现规划在 `selected &&` 块内，无选中策略时不可见，而运行历史是全局的。 | **列入条件**（见评审结论）：F4 `GET /runs` 已支持 `mode` 过滤，前端历史列表应默认/可切换按 `mode` 过滤并标注来源；历史 Tab 宜提到 `selected` 外层。 |
| C9 | 一致性 | P2 | §6.1 / §4.3 | `CompareReq.limit` 未给上下限，应对齐 `BacktestReq(ge=10, le=1000)`；§6.1 称 `strategyIds` 越界“→ 400”，但 `Field(min_length/max_length)` 校验实际返回 **422**。 | **已修复**：`CompareReq.limit = Field(500, ge=10, le=1000)`；§6.1 状态码措辞统一为 422（与既有入参校验一致），并注明如需 `400 + {error:{code}}` 形态须在路由内手校。 |

> 处置说明：本 v2 仅修订 `spec.md` 正文与本表；源码改动留待 Subtask #2 按修订后的设计实施。所有“已修复”均指**设计层面**已在下文相应小节落实约束。

---

## 1. Overview（概述）

迭代 1 已经把"左图右策略"的量化模拟交易工作台从 0 搭到可用：基于 OKX 真实公共行情，前端用 `lightweight-charts` 渲染可缩放 / 平移 / 日期跳转 / 滚动回溯的 K 线，右侧可切换 5 个内置高频 / 日内策略并即时回测、在左图叠加买卖 markers 与净值折线；后端 FastAPI 跑可插拔策略注册表、SimBroker 撮合、回测器、绩效、SQLite 持久化与实时模拟（paper）会话，且已用真实 OKX 数据端到端复验、20/20 后端测试与前端 tsc/vitest/build 全绿。换言之，**核心闭环已经存在且数据是真实的**。

但对照全局目标"**完成项目开发，确保可用，数据真实可用**"，当前基线仍有三类可被客观验证的缺口：(1) **可用性 / 启动门槛**——把整套系统跑起来需要手动创建 venv、装依赖、跑预检、设 `PYTHONUTF8`、起 uvicorn，再单独 `pnpm install && pnpm dev`，没有一键编排，新机器或评审者首次启动成本高；(2) **产品完整度**——用户的原始诉求是"切换不同交易策略、**查看不同策略实际交易情况与收益**"并"对比"，而当前一次只能看一个策略，切换即替换，缺少**多策略横向对比**；同时 `strategy_runs` 已持久化却没有"历史回放 / 运行历史"入口；(3) **"模拟快速交易"未真正可演示**——原始需求是"本阶段只开发模拟**快速**交易"，但实时 paper 仅在 OKX 确认 K 线时推进，1H 周期下要等一小时才有一个事件，无法即时展示策略"快速交易"。

因此迭代 2 聚焦三条线、共五个增量特性：**(A) 让它一键可跑、真实数据可持续校验**（F1 开发编排、F2 真实数据端到端校验与健康增强）；**(B) 补齐多策略对比这一核心产品能力**（F3 多策略对比榜单、F4 运行历史浏览）；**(C) 让"模拟快速交易"立即可演示**（F5 历史回放加速模拟）。所有特性均为**纯增量扩展**，复用现有 `run_backtest`、`PaperSession.feed_candle`、`repositories` 与 REST/WS 通道，不改 DB schema（仅复用既有 JSON 配置列与 `mode` 字符串），不破坏 v2 任何契约。

---

## 2. Goals / Non-Goals（迭代 2）

### Goals
1. **一键启动**：单条命令在 Windows / macOS / Linux 上完成预检 + 依赖就绪 + 同时拉起前后端，并聚合两端日志、Ctrl-C 优雅退出（F1）。
2. **真实数据可持续校验**：提供可重复的端到端真实数据冒烟脚本与网络标记测试，`/api/health` 暴露 OKX 时延 / 最近成功取数时间 / WS 可达性，让"数据真实可用"随时可证（F2）。
3. **多策略对比**：一次对同一标的 / 周期回测多个策略，输出可排序榜单（收益 / 夏普 / 胜率 / 最大回撤 / 交易数），并在左图叠加多条归一化净值曲线带图例（F3）。
4. **运行历史**：列出并可重新打开历史回测 / 模拟运行的净值与成交（F4）。
5. **模拟快速交易可演示**：历史回放加速模式，按真实近端 K 线以可调倍速驱动现有 paper 撮合管线，立即可见策略"快速交易"（F5）。
6. 全程不违反 v2 的 5 条放行条件；新增代码同样**严禁以假数据冒充真实行情**。

### Non-Goals（迭代 2 不做）
- 不接入真实下单 / 真实资金（与 v2 一致，Phase 1 仅模拟）。
- 不引入多用户账号 / 鉴权。
- 不做参数自动寻优 / 网格搜索（对比仅用各策略当前参数）。
- 不改 SimBroker 撮合内核语义、不改 5 策略逻辑、不改既有 DB 表结构。
- 对比不追求大规模并行回测（默认上限 ≤ 12 个策略；超出报 400）。

---

## 3. 优先级与范围控制

| 优先级 | 特性 | 服务的全局目标 | 备注 |
|---|---|---|---|
| **P0** | F1 一键开发编排 | 确保可用 | 纯新增脚本 / 根配置，零业务侵入 |
| **P0** | F2 真实数据端到端校验 + 健康增强 | 数据真实可用 | 硬约束的可证明性 |
| **P1** | F3 多策略对比榜单 | 完成项目开发（核心诉求） | 复用 `run_backtest`，候选 candles 只取一次 |
| **P1** | F5 历史回放加速模拟 | 完成"模拟快速交易" | 复用 `PaperSession.feed_candle` |
| **P2** | F4 运行历史浏览 | 完成 + 可用 | 复用已持久化的 `strategy_runs` |

> 下游 Subtask #2 按 P0 → P1 → P2 实施；若 P2 受时间限制未完成，须在本文档 §11"实施过程发现"段记录，不得静默偏离。

---

## 4. 技术设计（架构 / 关键路径 / 时序）

整体架构沿用 v2：浏览器 → Next.js（BFF：`/api/py/*` rewrite 到 Python `/api/*`，WS 经 `NEXT_PUBLIC_PY_WS_URL` 直连）→ FastAPI（OKX 适配 / 策略 / 引擎 / paper / SQLite）→ OKX V5（经系统 / Clash 代理）。迭代 2 不新增层，仅在既有层内追加路由、脚本、组件与状态。

### 4.1 F1 一键开发编排
- **根 `package.json`（新增）**：定义 pnpm 顶层脚本：
  - `setup`：调用 `python scripts/dev.py setup`（建 venv + 装 Python 依赖 + `pnpm -C apps/web install`）。
  - `preflight`：`python scripts/dev.py preflight`（=现有 `services/quant/scripts/check_okx.py` 的封装）。
  - `dev`：`python scripts/dev.py up`（预检 → 并发起后端 + 前端）。
  - `test`：分别跑后端 pytest 与前端 vitest。
- **`scripts/dev.py`（新增，跨平台纯 Python）**：核心编排器。
  - `setup` 子命令：若无 `services/quant/.venv` 则 `python -m venv`，再用 venv 的解释器 `pip install -r requirements.txt`；调用 `pnpm -C apps/web install`。
  - `preflight` 子命令：导入并调用 `check_okx`（或直接子进程跑 `scripts/check_okx.py`），失败则非零退出并打印"先解决网络 / 代理"。
  - `up` 子命令：先 `preflight`（可 `--skip-preflight` 跳过）；用 `subprocess.Popen` 同时启动 `uvicorn app.main:app --port 8000 --reload`（cwd=`services/quant`，env 注入 `PYTHONUTF8=1`、透传 `HTTP(S)_PROXY`）与 `pnpm -C apps/web dev`；各自前缀着色聚合 stdout；捕获 `KeyboardInterrupt` → 向两子进程发 terminate → 等待退出。Windows 用 `CREATE_NEW_PROCESS_GROUP`，POSIX 用进程组，保证 Ctrl-C 双杀。
- 该特性**不触碰任何业务源码**，仅新增根级脚本/配置 + 更新 README 启动段。

### 4.2 F2 真实数据端到端校验 + 健康增强
- **`scripts/smoke_e2e.py`（新增）**：纯脚本，串起真实数据全链路并断言：
  1. 经代理 `OKXRestClient.get_candles("BTC-USDT-SWAP","1H",limit=120)` 取真实 K 线，断言 `len>=100`、`ts` 升序且为**秒级**（`ts < 2_000_000_000`，非毫秒）、价格在合理量级（`c>0`）。
  2. 对内置策略（如 `momentum_breakout`）调用 `run_backtest(...)`，断言 `equity_points` 非空、`start_ts<end_ts`、metrics 字段齐全。
  3. 打印 `PASS/FAIL` 摘要并以退出码反映（CI 可用）。脚本必须直连真实 OKX，**禁止 mock**；OKX 不可达时显式 FAIL 并提示代理。
- **`services/quant/tests/test_e2e_realdata.py`（新增，`@pytest.mark.network`）**：把上面断言包成可选测试。**（C4 修复）** 经核对，当前 `pyproject.toml` 仅有 `filterwarnings`、**未注册 `network` marker、也无 `addopts`**；pytest 默认**不会**自动排除带标记用例。故本迭代须在 `[tool.pytest.ini_options]`：(a) 注册 marker `markers = ["network: 需真实 OKX 连通的端到端用例"]`；(b) 增 `addopts = "-m 'not network'"`，使**裸跑 `pytest` 默认排除** network 用例（满足验收 #7“不含 network”、不污染离线 CI），opt-in 时用 `pytest -m network` 显式启用。
- **`/api/health` 增强**（改 `api/market.py` 的 `health()` 与 `okx/rest_client.py`）：返回体在 v2 `{status, okx}` 基础上**新增** `okxLatencyMs:int|null`、`lastOkTs:int|null`（最近一次成功取数的秒级 UTC）、`wsConfigured:bool`。`rest_client` 内部在每次 `_request` 成功后更新 `_last_ok_ts`（秒级 UTC），并在 `health()` 探活时用 `time.monotonic()` 计一次往返毫秒数；OKX 不可达时 `okx:"down"`、`okxLatencyMs=null`。**（C7 修复）** `wsConfigured = bool(get_settings().okx_ws_public)`，表征“上游 OKX WS 端点已配置”（默认配置下恒为 true，仅当部署显式清空该端点时为 false，用于排障）。前端 Header 连接点 tooltip 展示时延与 lastOk，使“真实可达性”可视。

### 4.3 F3 多策略对比榜单
- **后端 `POST /api/strategies/compare`（新增路由，置于 `api/strategies.py`）**：
  - 入参 `CompareReq{ strategyIds:int[](2..12), instId, bar, limit, initialCapital?, fees?, slippage?, leverage? }`。**（C9 修复）** `strategyIds = Field(min_length=2, max_length=12)`、`limit = Field(500, ge=10, le=1000)`（对齐 `BacktestReq`，配合 R2 上限）；`strategyIds` 进路由后**去重**（保序）再逐一回测，重复 id 不重复落库。
  - **候选 candles 只取一次**（`get_rest_client().get_candles(instId,bar,limit)`），随后 `upsert_candles` 落缓存；避免 N× OKX 调用触发限频（呼应 v2 C10）。OKX 不可达照常抛 `OKXError → 502`。
  - 对每个 `strategyId`：读取 DB 行 + 注册表合并参数（复用 `get_strategy_row` + `cls.meta.default_params` 合并逻辑，与单回测一致）→ `await run_in_threadpool(run_backtest, strategy, candles, bar, ...)` → `repo.create_run` + `repo.persist_backtest_result` 落库（每个策略一个 `runId`，便于"打开详情"）。
  - 返回 `{ instId, bar, items: CompareItem[] }`，`CompareItem{ strategyId, name, runId, metrics, equity: EquityPoint[](归一化前的真实净值) }`。`equity` 直接随响应返回，省去前端 N 次 `/equity` 往返；其余明细仍可按 `runId` 拉 `/trades`。
  - 排序由前端做（默认按 `totalReturn` 降序）。
- **前端**：
  - `ChartToolbar` 模式段从二态（回测 / 实时）扩为三态：**回测 / 对比 / 实时**（`store.ts` 的 `Mode` 增加 `"compare"`）。
  - 新增 `components/Strategy/CompareView.tsx`：勾选要对比的策略（默认全选 builtin）+ "运行对比"按钮 → 调 `api.compare(...)` → 渲染**可排序榜单表**（列：策略、收益率、夏普、胜率、最大回撤、交易数；点列头排序；每行有色块对应曲线颜色 + "打开"按钮设 `runId` 跳回详情）。
  - `CandleChart` 支持**多条净值曲线叠加**：新增可选 `overlays?: { id:number; name:string; color:string; equity:EquityPoint[] }[]`。在 `compare` 模式下为每个 item 建一条 line series，**按 `equity/initial-1` 归一化为百分比**以便跨策略可比，统一图例/颜色板（取自设计 token，最多 12 色循环）。**（C1 修复）** 经核对，迭代 1 的 `CandleChart` 把 K 线挂在**默认右价轴**、单策略净值线挂在**左价轴**（绝对值）。归一化百分比（约 `[-0.1, 0.3]`）**绝不能挂回 K 线所在的右价轴**——否则会被 6 万量级的价格刻度压成贴底直线、对比不可读。故 `overlays` 必须使用**与 K 线隔离的独立归一化刻度**：复用左价轴或新建专用 `priceScaleId:"overlay"`（`scaleMargins` 自适应百分比区间）；进入 `compare` 模式时把既有单策略净值线 `setData([])` 清空，退出时逐条 `removeSeries` 释放（R6）。`overlays` 的 ts 去重升序复用 v2 F3 修复逻辑（即 `CandleChart` 既有 `equity` effect 的 `Map` 去重 + 升序）。
  - `page.tsx`：当 `mode==="compare"` 时把 compare 结果映射成 `overlays` 传入 `CandleChart`，并隐藏单策略 markers。

### 4.4 F4 运行历史浏览
- **后端 `GET /api/runs`（新增列表路由，置于 `api/runs.py`）**：查询参数 `strategyId?`, `instId?`, `mode?`, `limit=50(1..200)`, `offset=0`；按 `id` 降序（最新在前）。返回 `Run[]`（复用 `run_out`）。新增 `repo.list_runs(...)`（`select(StrategyRun)` + 条件过滤 + 分页）。
- **前端**：`components/Strategy/RunHistory.tsx`——StrategyPanel 底部 Tab 增加"历史"页，列出近期 runs（标的 / 周期 / 模式 / 收益 / 时间），点击 → `setRunId(run.id)` + 切到 `backtest` 视图复用既有 `/equity`+`/trades` 叠加渲染。`api.ts` 增加 `runsList(filter)`。
- **（C8 缓解）** 一次 F3“运行对比”会落 ≤ 12 行 `mode="backtest"` run，会稀释历史列表。故 `GET /runs` 的 `mode` 过滤要被前端用上：RunHistory 默认/可切换按 `mode` 过滤（如先看 `paper` 实盘模拟、或单独看回测），并在行上标注来源；历史 Tab 宜挂在 `StrategyPanel` 的 `selected &&` 块**外层**（运行历史是全局的，不应因未选中策略而隐藏）。该项为受控条件，见“评审结论”。

### 4.5 F5 历史回放加速模拟
- **目标**：在不等真实确认 K 线的前提下，立即演示策略"快速交易"。复用 paper 管线，仅改"数据来源 + 节奏"，**不改 SimBroker**。
- **后端**：
  - `PaperStartReq` 增字段。**（C2 修复）** 必须做 schema 校验，避免除零 / 溢出滚动窗：
    - `source: Literal["live","replay"] = "live"`；
    - `speed: float = Field(1.0, gt=0, le=64)`（仅 replay 用，事件间隔 = `BASE/speed`；`gt=0` 杜绝 `speed=0` 时 `BASE/speed` 除零崩溃，`le=64` 给上界）；
    - `replayBars: int = Field(300, ge=10, le=600)`（回放根数；上界 `600` 对齐 `PaperSession._WINDOW_MAX=600`，避免溢出滚动窗丢早段；下界 `10` 保证至少能过 warmup）。
    - 越界由 FastAPI 入参校验返回 422（与既有 `BacktestReq` 同机制，见 §6.1）。
  - `PaperManager.start(...)` 增 `source/speed/replay_bars` 形参并透传给 `PaperSession`（live 调用保持默认值，旧调用点不破坏）。
  - `PaperSession`：
    - `source=="replay"` 时，`start()` 不订阅实时 WS，而是 `get_candles(inst,bar,limit=replayBars)` 取真实历史，创建后台 `asyncio.Task`（存为 `self._replay_task`）顺序对每根调用 `await self.feed_candle(Candle(...,confirm=True))`，每根之间 `await asyncio.sleep(max(0.0, BASE/speed))`（`BASE≈0.2s`，节奏仅控制前端观感，不影响撮合正确性，因 `feed_candle` 仍串行经 `self._lock`）；回放任务包在 `try/finally` 内，正常跑完或被取消都在 `finally` 调用 `await self.stop()` 自动结算。
    - **（C3 修复）** `stop()` 改为**幂等**：入口 `if self.state != "running": return`（防回放自动 `stop()` 与用户 `POST /paper/{sid}/stop`、`PaperManager.shutdown` 兜底**重复结算 / 重复落库**——live-only 时此竞态不出现，F5 自动停止把它变成常态路径，必须显式守护）；`stop()` 内若 `self._replay_task` 存在且未完成，先 `task.cancel()` 并 `await`（吞 `CancelledError`）再做 `broker.finalize` 与 `_persist_final`。`/paper/{sid}/stop` 路由对已 `stopped` 的会话返回其 `run_out`，不再二次结算。
    - `source=="live"` 路径完全不变。
    - `mode` 在 DB run 里记 `"paper"`（保持兼容），`config_json` 记 `{source,speed,replayBars}` 以便区分。回放数据**仍是真实 OKX 历史**，不违反真实数据约束。
- **前端**：`StrategyDetail` 实时模拟区新增"来源：实时 / 回放"切换与"倍速"选择（1× / 4× / 16×）；`api.paperStart` body 透传 `source/speed/replayBars`。WS 事件流与现有 `paperWS` 完全一致，前端无需新通道。

---

## 5. 文件 / 模块变更计划

> 约定：**新增** = create，**修改** = 在既有文件内追加 / 扩展（不重写既有语义）。所有路径用正斜杠。

### 5.1 根 / 脚本 / 文档
| 文件 | 动作 | 意图 |
|---|---|---|
| `package.json`（仓库根） | 新增 | 顶层 pnpm 脚本：`setup/preflight/dev/test`，编排前后端 |
| `pnpm-workspace.yaml` | 新增 | 声明 `apps/*` 工作区（仅根脚本编排用，不改 `apps/web` 内部） |
| `scripts/dev.py` | 新增 | 跨平台编排器：`setup` / `preflight` / `up` 子命令，聚合日志 + Ctrl-C 双杀 |
| `scripts/smoke_e2e.py` | 新增 | 真实数据端到端冒烟：OKX→candles→backtest 断言，退出码反映结果 |
| `README.md` | 修改 | 新增"一键启动（`pnpm setup && pnpm dev`）"段、对比 / 历史 / 回放用法、F2 校验说明 |
| `docs/plans/aragon-iter2-enhancements/spec.md` | 新增 | 本设计文档（Subtask #0 产出） |

### 5.2 后端 `services/quant`
| 文件 | 动作 | 意图 |
|---|---|---|
| `app/schemas.py` | 修改 | 新增 `CompareReq`（`strategyIds:Field(min_length=2,max_length=12)`、`limit:Field(500,ge=10,le=1000)`）；`PaperStartReq` 增 `source:Literal`、`speed:Field(1.0,gt=0,le=64)`、`replayBars:Field(300,ge=10,le=600)`（C2/C9）；复用既有 `run_out` |
| `app/api/strategies.py` | 修改 | 新增 `POST /strategies/compare`（candles 取一次 + 逐策略回测落库 + 返回含 equity） |
| `app/api/runs.py` | 修改 | 新增 `GET /runs` 列表（filter + 分页，复用 `run_out`） |
| `app/api/market.py` | 修改 | `health()` 增 `okxLatencyMs/lastOkTs/wsConfigured` |
| `app/api/paper.py` | 修改 | `start` 透传 `source/speed/replayBars` 给 manager；`/paper/{sid}/stop` 对已 `stopped` 会话直接回 `run_out`、不二次结算（C3） |
| `app/okx/rest_client.py` | 修改 | 维护 `_last_ok_ts`；`health()` 计往返时延；不改归一化语义 |
| `app/paper/manager.py` | 修改 | `start(...)` 增 `source/speed/replay_bars` 形参并透传 |
| `app/paper/session.py` | 修改 | `__init__` 接收回放参数 + `self._replay_task`；`start()` 分支：replay 用历史 + asyncio 节奏任务驱动 `feed_candle`；`stop()` 改幂等（`state!="running"` 提前返回）+ 取消并 await 回放任务（C3） |
| `app/db/repositories.py` | 修改 | 新增 `list_runs(session, *, strategy_id, inst_id, mode, limit, offset)` |
| `pyproject.toml` | 修改 | `[tool.pytest.ini_options]` 注册 `markers=["network: …"]` **并** 增 `addopts="-m 'not network'"`，使裸跑 `pytest` 默认排除 network 用例（C4） |
| `tests/test_compare.py` | 新增 | `/strategies/compare` 用 mock OKX 跑 2~3 策略，断言 items / 排序键 / 各自 runId |
| `tests/test_runs_list.py` | 新增 | `GET /runs` 过滤 + 分页正确 |
| `tests/test_paper_replay.py` | 新增 | replay 注入历史，断言产生 equity/trade 事件且会话自动 stop |
| `tests/test_e2e_realdata.py` | 新增 | `@pytest.mark.network` 真实 OKX 端到端断言 |

### 5.3 前端 `apps/web`
| 文件 | 动作 | 意图 |
|---|---|---|
| `lib/types.ts` | 修改 | 新增 `CompareItem/CompareResult`；`Mode` 增 `"compare"`；`Health` 增新字段；`PaperStart` body 增 `source/speed/replayBars` |
| `lib/api.ts` | 修改 | 新增 `compare(...)`、`runsList(...)`；`paperStart` 透传回放参数 |
| `lib/store.ts` | ~~修改~~ **不改** | **（C5 修复）** `Mode` 单一来源在 `types.ts`，`store.ts` 仅 `import type { Mode }`，无需为 `"compare"` 改动；默认 `mode:"backtest"` 不变 |
| `lib/compare.ts` | 新增 | **（C6 修复）** 榜单排序键（收益/夏普/胜率/回撤/交易数）与 `equity/initial-1` 归一化的**纯函数**模块，供 `CompareView`/`CandleChart` 消费且可被 `compare.test.ts` 单测（对齐 `format.ts`+`format.test.ts` 约定） |
| `components/Chart/ChartToolbar.tsx` | 修改 | 模式段三态（回测 / 对比 / 实时）；连接点 tooltip 展示时延 |
| `components/Chart/CandleChart.tsx` | 修改 | 支持 `overlays` 多条归一化净值曲线 + 图例（复用 ts 去重升序） |
| `components/Strategy/CompareView.tsx` | 新增 | 勾选策略 + 运行对比 + 可排序榜单 + 打开详情 |
| `components/Strategy/RunHistory.tsx` | 新增 | 运行历史列表，点击重开净值 / 成交 |
| `components/Strategy/StrategyPanel.tsx` | 修改 | `compare` 模式渲染 `CompareView`；底部 Tab 增"历史" |
| `components/Strategy/StrategyDetail.tsx` | 修改 | 实时模拟区增"来源 / 倍速"控件 |
| `app/page.tsx` | 修改 | `compare` 模式把结果映射为 `overlays` 传图；隐藏单策略 markers |
| `lib/compare.test.ts` | 新增 | 榜单排序与净值归一化纯函数单测（vitest） |

> **不改动**：`engine/simulator.py`、`engine/backtester.py`、`engine/metrics.py`、`engine/indicators.py`、`strategies/*`、`db/models.py`、`okx/ws_client.py`、`okx/types.py`、`db/session.py`、`db/models` schema。迭代 2 是它们的**消费者**，不是修改者。

---

## 6. 接口设计（REST / WebSocket / CLI）

### 6.1 新增 / 变更 REST（后端真实路径，前端经 `/api/py/*`）
| Method | Path | 入参 / Body | 响应 |
|---|---|---|---|
| POST | `/api/strategies/compare` | `{strategyIds:int[2..12], instId, bar, limit, initialCapital?, fees?, slippage?, leverage?}` | `{instId, bar, items:CompareItem[]}` |
| GET | `/api/runs` | `?strategyId&instId&mode&limit=50&offset=0` | `Run[]`（id 降序） |
| GET | `/api/health` | — | `{status, okx:"up\|down", okxLatencyMs:int\|null, lastOkTs:int\|null, wsConfigured:bool}` |
| POST | `/api/paper/start` | v2 字段 + `{source:"live\|replay", speed:number, replayBars:int}` | `{sessionId, runId}` |

错误体沿用 v2：业务错误 `{error:{code,message}}` + 4xx/5xx；OKX 不可达 → 502 `OKX_UNREACHABLE`（由 `main` 的 `OKXError` 处理器统一转换）。**（C9/C2 修复）** 入参越界（`strategyIds` 个数 2..12、`limit` 10..1000、`speed`>0、`replayBars` 10..600）由 **Pydantic `Field` 校验**，FastAPI 返回 **422**（默认 `{detail:[…]}` 形态，与既有 `BacktestReq` 一致——`main` 未注册 `RequestValidationError` 处理器，故 422 不走 `{error:{…}}` 包装）。若需把这些边界也统一成 `400 + {error:{code}}`，须在路由内手动校验并 `raise HTTPException(400, detail={"code":...})`；本迭代为**与 v2 既有行为一致**，采用 422 默认形态、不额外手校（前端 `ApiRequestError` 已能从 `res.status` 兜底取 code）。所有新 `ts` 字段仍为**秒级 UTC**（v2 C2）。

### 6.2 类型（`apps/web/lib/types.ts`，camelCase 对齐后端）
```ts
type Mode = "backtest" | "compare" | "paper";
type CompareItem = { strategyId:number; name:string; runId:number; metrics:Metrics; equity:EquityPoint[] };
type CompareResult = { instId:string; bar:string; items:CompareItem[] };
type Health = { status:string; okx:"up"|"down"; okxLatencyMs:number|null; lastOkTs:number|null; wsConfigured:boolean };
// PaperStart body: { strategyId; instId; bar; initialCapital?; source?:"live"|"replay"; speed?:number; replayBars?:number }
```

### 6.3 WebSocket
WS 通道 / 事件 schema **不变**（`/api/ws/paper/{sid}` 仍推送 `signal/trade/equity/candle/status`）。回放只是改变这些事件的产生来源与节奏，前端 `paperWS` 零改动。

### 6.4 CLI
- `pnpm setup` / `pnpm preflight` / `pnpm dev` / `pnpm test`（根 `package.json`）。
- `python scripts/dev.py {setup|preflight|up} [--skip-preflight]`。
- `python scripts/smoke_e2e.py`（退出码 0=PASS）。
- `pytest -m network`（真实数据测试，可选）。

---

## 7. 数据模型

**不新增 / 不修改任何表**。迭代 2 全部复用 v2 的 `strategies / strategy_runs / trades / equity_points / candles_cache`：
- F3 对比：每个被比策略写一行 `strategy_runs(mode="backtest")` + 对应 `trades/equity_points`，与单回测同构。
- F4 历史：纯读 `strategy_runs`（新增 `list_runs` 查询，无 DDL）。
- F5 回放：`strategy_runs(mode="paper")`，回放元信息（`source/speed/replayBars`）写进既有 `config_json` JSON 列，无需迁移。

> 运行时内存形态同 v2（`PaperSession` 持 `deque` 滚动窗口 + `SimBroker` 账户 + 订阅 fan-out + 新增的回放 `asyncio.Task` 句柄）。

---

## 8. 测试与验收标准

### 8.1 单元 / 集成测试
- **后端**（pytest + httpx AsyncClient，mock OKX）：
  - `test_compare`：2~3 策略对比，断言 `items` 数量 / 每项含独立 `runId` 与齐全 `metrics` / candles 仅请求一次（mock 调用计数 == 1）。
  - `test_runs_list`：插多条 run，验证 `strategyId/instId/mode` 过滤、`limit/offset` 分页、id 降序。
  - `test_paper_replay`：以注入历史驱动 replay，断言收到 ≥1 个 `equity` 事件、会话结束自动 `stopped`、`live` 路径不受影响。
  - `test_e2e_realdata`（`@pytest.mark.network`，**经 `pyproject` 的 `addopts="-m 'not network'"` 默认排除**，opt-in 用 `pytest -m network`，C4）：真实 OKX 全链路断言（秒级 ts、equity 非空）。
  - 既有 20 项测试须继续全绿（回归）。
- **前端**（vitest）：
  - `compare.test`：榜单按各指标排序正确、净值 `equity/initial-1` 归一化正确、颜色分配稳定。
  - 既有 vitest 须继续全绿。

### 8.2 验收标准（Acceptance Criteria）
1. **一键启动**：全新 checkout 后 `pnpm setup` 成功，`pnpm dev` 同时拉起 8000 + 3000 并聚合日志；Ctrl-C 双进程优雅退出（F1）。
2. **真实数据可证**：`python scripts/smoke_e2e.py` 经代理 PASS（断言真实 OKX 数据、秒级 ts、回测净值非空）；`/api/health` 返回 `okx:"up"` 且含非空 `okxLatencyMs/lastOkTs`（F2）。OKX 不可达时脚本显式 FAIL、接口仍显式报错，**无假数据**。
3. **多策略对比**：选 ≥2 策略运行对比 → 榜单按收益 / 夏普等可排序，左图叠加多条归一化净值曲线 + 图例；点"打开"可下钻到单策略详情（F3）。
4. **运行历史**：历史 Tab 列出近期 runs，点击可重现其净值与成交（F4）。
5. **快速模拟**：以"回放 16×"启动一次模拟，数秒内即见到 markers / 净值随回放推进更新，结束自动停止（F5）。
6. **回归无破坏**：v2 全部验收（真实 K 线、单策略回测叠加、参数重跑、CRUD、实时模拟、可插拔）仍成立；5 条放行条件未回退。
7. 后端 pytest（含新增、不含 network）+ 前端 vitest + `next build` + 后端 import 自检全绿；`git status` 仅含本迭代相关文件，无构建产物 / `.env` / `*.db`。

---

## 9. 风险与缓解（Risks & Mitigations）

| # | 风险 | 影响 | 缓解 |
|---|---|---|---|
| R1 | 对比 N 次回测放大 OKX 限频（C10） | 429 / 取数失败 | **candles 只取一次**复用于全部策略；命中 `candles_cache`；策略数硬上限 12 |
| R2 | 对比多策略 CPU 占用阻塞事件循环 | 接口卡顿 | 每策略 `run_in_threadpool`；`limit` 上限 1000；策略数上限 12 |
| R3 | 回放 `asyncio.Task` 未随 stop / shutdown 取消；且**回放自动 `stop()` 与手动 `/stop`、`shutdown` 兜底重复进入 `stop()`** | 任务泄漏 / 进程退出卡住 / **重复结算 + 重复落库**（C3） | `stop()` 入口 `if self.state!="running": return` 幂等守护；存 `self._replay_task` 并在 `stop()` 内 `cancel()+await`；`PaperManager.shutdown` 兜底；回放放在 `try/finally` 内自动结算；`/paper/{sid}/stop` 对已停会话只回 `run_out` |
| R4 | 回放被误读为"假数据" | 违背真实数据约束 | 回放数据**全部来自真实 OKX 历史**；UI 明确标注"历史回放（真实数据，加速）"；不合成任何 K 线 |
| R5 | F1 编排在 Windows/PowerShell 下子进程信号 / 编码问题 | 起不来 / 中文乱码 | 纯 Python `subprocess`，注入 `PYTHONUTF8=1`；Windows `CREATE_NEW_PROCESS_GROUP`，POSIX 进程组；正斜杠路径 |
| R6 | 多净值曲线叠加破坏 lightweight-charts time 单调 | 图表断言抛错 | 复用 v2 F3：每条曲线 ts 去重升序后再 `setData`；卸载时逐条 `removeSeries` |
| R7 | 健康检查时延探测拖慢 `/health` 轮询 | 前端卡顿 | 时延探测复用既有 `health()` 一次往返、加超时；失败即 `okx:"down"` 且时延置 null |
| R8 | `Mode` 增 `"compare"` 触及既有 paper/backtest 分支 | 回归 | 新模式为独立分支，backtest/paper 代码路径不改；以回归测试守护 |

---

## 10. 下游实施顺序（建议给 Subtask #2）

1. **F2 健康增强 + smoke 脚本**（先证明真实数据链路仍稳），再 **F1 编排脚本 + 根 package.json + README**（让后续验证一键可跑）。
2. **F3 后端 `/strategies/compare` + repo/schemas + 测试** → 前端 `CompareView` + `CandleChart.overlays` + 三态模式。
3. **F5 后端 replay（schemas/manager/session）+ 测试** → 前端 `StrategyDetail` 来源 / 倍速控件。
4. **F4 `GET /runs` + repo + 测试** → 前端 `RunHistory` Tab。
5. 打磨：图例 / 排序交互 / 空错载态 / README，跑全量回归（pytest 非 network + vitest + next build），真实数据手动复验对比 + 回放。
6. 把实施中对本方案的受控澄清 / 偏离记录到 §11。

## 11. 实施过程发现的方案缺陷（Subtask #2 实施记录）

> 实施完成。本 v2 方案**无重大缺陷**，全部文件按 §5 变更计划落地；下列为实施过程中的**受控澄清**（非偏离设计语义），按放行条件 #2/#5 留痕。

**I1（C8 所采方案）**：`RunHistory` 采用「来源标签 + mode 过滤」组合落实 C8——顶部提供 `全部 / 回测 / 模拟` 三态过滤（调用 `GET /runs?mode=`），且每行渲染来源 Badge（回测/模拟），避免对比产生的 ≤12 行 `backtest` run 淹没历史；「历史」Tab 已挂在 `StrategyPanel` 的 `selected &&` 块**外层**（运行历史全局可见）。

**I2（compare 结果跨组件传递机制）**：为遵守 C5「`store.ts` 不改」，`compare` 结果不入全局 store，而是经 React Query 共享缓存键 `["compare", instId, bar]` 传递——`CompareView` 成功后 `qc.setQueryData(...)` 写入，`page.tsx` 用同键 `useQuery({enabled:false, initialData:null})` 响应式消费并映射为 `CandleChart` 的 `overlays`；切换标的/周期键变化即自动清空叠加。配色由 `compare.ts` 的 `colorForIndex(items 原始序)` 统一，榜单色块与左图曲线一一对应。

**I3（health 增强的回归保护）**：`api/market.py` 读取 `rest.last_latency_ms / last_ok_ts` 时用 `getattr(..., None)` 兜底，使迭代 1 既有测试中的假 OKX 客户端（未实现新属性）无需改动即不致 500，既有 20 项测试零回归。

**I4（compare「打开」详情）**：榜单行「打开」按钮设 `strategyId + runId + mode="backtest"` 下钻单策略详情；可能触发 `StrategyPanel` 等价自动回测（同策略/同数据，结果一致），属可接受行为。

**验收实测**：后端 `pytest` 27 passed / 1 network deselected；`pytest -m network` 经代理 1 passed；前端 `vitest` 11 passed、`tsc --noEmit` 0 error、`next build` 成功；`scripts/smoke_e2e.py` 经代理 PASS（120 根真实 K 线、秒级 ts、回测净值非空）；本地起服务实测 `/api/health`（含 `okxLatencyMs=359/lastOkTs/wsConfigured`）、`/strategies/compare`（3 策略各独立 runId、candles 仅取一次）、`/runs` 过滤、`/paper/start source=replay speed=16`（120 根回放自动 stopped、speed=0 → 422、已停会话再 /stop 返回 run_out 不二次结算）均通过。

---

## 评审结论（Review Verdict · Subtask #1）

**结论：有条件通过（Approved with Conditions）。**

本方案方向正确、范围克制、纯增量：未触碰 5 策略、SimBroker 撮合内核与既有 DB schema，所复用的后端契约（`run_backtest`、`PaperSession.feed_candle`、`repositories.create_run/persist_backtest_result`、`run_out`、`get_rest_client().get_candles`、`upsert_candles`、注册表参数合并）与前端结构（`CandleChart` 双价轴叠加、`StrategyPanel` Tabs、`api.request<T>`、`Mode`）经逐文件核对**全部真实存在且签名匹配**，可在现有 Next.js 14 + FastAPI 技术栈上落地。五条放行条件未回退，F5 回放仅用真实 OKX 历史，符合全局目标“数据真实可用”。

四个 P1（C1 归一化刻度、C2 回放参数校验、C3 幂等 `stop()`、C4 network 默认排除）与四个 P2（C5/C6/C7/C9）已在本 v2 正文就地修复；据此**批准进入 Subtask #2 实施**，但须满足以下**放行条件**：

1. **必须落实本 v2 修复的 4 个 P1**：(C1) `overlays` 用与 K 线右价轴隔离的独立归一化刻度、`compare` 模式清空单策略净值线；(C2) `PaperStartReq` 的 `source/speed(gt=0,le=64)/replayBars(ge=10,le=600)` 与 `CompareReq` 的 `strategyIds(2..12)/limit(10..1000)` 入参校验到位；(C3) `PaperSession.stop()` 幂等 + 回放 `self._replay_task` 取消并 await + `/stop` 对已停会话不二次结算；(C4) `pyproject` 增 `addopts="-m 'not network'"` 且裸跑 `pytest` 实测不触网。任一缺失即视为未达本结论。
2. **C8（受控 P2）**：F4 `RunHistory` 须用上 `GET /runs` 的 `mode` 过滤并标注来源、历史 Tab 提到 `selected` 外层；若工期紧张，至少保证对比产生的 ≤12 行 run 不淹没历史（默认过滤或来源标签二选一），并在 §11 记录所采方案。
3. **验收门槛照 §8.2 执行**：后端 `pytest`（含新增、不含 network）+ 前端 vitest + `next build` + 后端 import 自检全绿；真实数据手动复验 F2 smoke / F3 对比 / F5 回放；`git status` 不含构建产物 / `.env` / `*.db`。
4. **真实数据约束不回退**：F5 回放 UI 明确标注“历史回放（真实数据，加速）”，不合成任何 K 线；OKX 不可达时各路径显式报错、无假数据兜底。
5. **受控偏离须留痕**：下游若发现本 v2 新缺陷或需偏离，按 §11 就地记录澄清与受控偏离，不得静默偏离。

> 全部条件达成后，本设计即视为**通过**；否则在 Subtask #3（代码 Review）阶段须就未达条款回修。

---

> 本文档为迭代 2 增量设计 **v2（已评审修订）**。Subtask #1 评审已完成：按“可行性 / 完整性 / 一致性 / 规模适配”四维 + v2 五条放行条件校验，得 **P0=0、P1=4（全部就地修复）、P2=5（4 项已修复，C8 列为受控条件）**，结论为**有条件通过**。下游 Subtask #2 须按本 v2 实施。
