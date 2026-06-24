# 高级回测：滑动窗口 / Walk-forward 开发方案（spec v2）

> Feature slug: `advanced-backtest-walk-forward`
> 适用仓库：Aragon Trading（Next.js Web + FastAPI 量化后端 + Electron 壳）
> 本文档为 **方案设计（Subtask #0）** 产物，仅描述设计，不含实现代码。
> 下游工程师据此可逐行落地，无需再做架构决策。
> **v2（方案评审与修复 · Subtask #1）**：经逐节评审，已就地修复全部 P0/P1 问题（见下「评审记录」），
> 修订点在正文以 `（v2 修订）` 标注；评审结论见文末「评审结论」。

---

## 评审记录 / Review Notes（v2）

> 评审维度：可行性（Feasibility）/ 完备性（Completeness）/ 一致性（Consistency）/ 适度性（Right-sizing）。
> 评审基线：已逐一核对 spec 引用的源文件与符号（`okx/rest_client.get_candles`、`agent/services.py`
> 取数与编排、`engine/backtester.run_backtest`、`engine/metrics.compute_metrics`、`db/models.StrategyRun`、
> `db/repositories.create_run/persist_backtest_result`、`schemas.BacktestReq/ErrorResponse`、`main.py` 错误处理器、
> 以及前端 `store.ts`(version 12)/`api.ts`(`/api/py`)/`types.ts`(`Metrics`)/`StrategyPanel.tsx` 自动回测 effect/
> `Chart/ChartToolbar.tsx` 分段控件/`RunHistory.tsx` mode 过滤）。**核心机制全部属实、可行**。
> 以下为评审发现，P0/P1 已在正文修复。

### P0（阻断性）
- 无。设计与现有技术栈契合，无阻断性缺陷。

### P1（合入前必须修复，已就地修复）

| # | 维度 | 问题 | 修复 |
|---|---|---|---|
| P1-1 | 可行性 / 完备性（最高优先级需求） | 基础回测「全区间覆盖」是本任务**最高优先级**目标，但 `effectiveCandleCount` 仅写入 `config_json`，而 `run_out`（`schemas.py:170`）**不返回 config_json**、`/backtest` 响应仅 `{runId, metrics}`——用户与验收测试**无法从产品面看到是否真的全覆盖、是否在 `backtest_max_bars` 处被静默截断**，直接架空「收益率可信」的目标。 | §3.2 让 `run_single_backtest` 响应**新增** `effectiveCandleCount / requestedBars / truncated`；§5/§6 增列响应字段与 TS 类型；§7 截断时前端显式横幅「区间过长，仅覆盖最近 N 根」；§9.3 验收①改为断言**API 返回的** `effectiveCandleCount`。 |
| P1-2 | 完备性 | 方案多处承诺前端提示（窗口偏短 / `uncoveredBars` / 数据不足一个窗口 / 区间截断），但响应结构 `WalkForwardSummary`/`WalkForwardResult` **没有任何字段承载这些提示**，提示无从传输。 | §6.2 `WalkForwardSummary` 新增 `notes: string[]` 与 `uncoveredBars: number`；§3.3 `summarize_windows`/`run_walk_forward` 负责填充；§5.1 响应示例补字段。 |
| P1-3 | 完备性 / 正确性 | `windowBars` 下界 `ge=50` 可能**小于策略 warmup**（多数策略 warmup ≈ 60，MTF 更高），导致整窗口零成交、指标恒为 0 而无硬约束，仅靠软提示。 | §5.1 下界 `ge=50 → ge=100`，并在文档明确「`windowBars` 必须显著大于策略 warmup」；运行期 `windowBars < warmup+5` 经 P1-2 的 `notes` 输出「窗口偏短」。 |
| P1-4 | 可行性 / 正确性 | 滑动窗口的 MTF 因果性依赖「高周期帧覆盖到 `start_ts` 之前的暖机段」。改造 `load_htf_frames` 为「全区间覆盖」时，若被误实现为仅取 `[start_ts, end_ts]`，会**饿死靠前窗口的高周期指标暖机**（NaN/0 信号），结论失真。 | §3.2 / §3.3 明确：改造后**必须保留** `start_ts` 之前 `htf_warmup_bars` 根的覆盖（以 `end_ts` 为锚向更早回溯）；§9.1 增「最早窗口高周期覆盖早于 start_ts」断言。 |
| P1-5 | 完备性 / 一致性 | `persist_walk_forward_result` 契约不清：parent run 先以**不含** summary/windows 的 config 落库，需后续**合并写回**；若实现为覆盖式写 config_json 将**丢失窗口参数**。且 `metrics_json` 投影（§6.1）描述了形状却未指明由谁构造。 | §4 / §6.1 钉死契约：该函数**读取并合并** `config_json`（叠加 `summary`/`windows`，不覆盖既有键）+ 构造 `metrics_json` 投影 + 写 `start_ts/end_ts/status`。 |

### P2（建议，部分已顺手在正文订正）
- **P2-1（适度性）**：`load_range_candles` 外层翻页与 `get_candles` 内部翻页**重复**——`get_candles(before=cursor, limit=N)` 本就会内部翻页累积到 `N` 根（`rest_client.py:160` `max_pages=limit//100+2`）。原「不改 rest_client 保持单一真源」的理由站不住（单次大 `limit` 调用同样不改 rest_client）。已在 §3.2 订正理由为「分块 checkpoint + `max_bars` 收口」，并说明外层循环是**可选**的有界分块，而非必要。
- **P2-2（一致性）**：基础回测用 `effectiveCandleCount`、WF parent 用 `totalCandleCount`，命名不一致。已在 §6.1 统一改用 `effectiveCandleCount`（保留 `totalCandleCount` 为别名说明）。
- **P2-3（一致性·阈值）**：`run_walk_forward` 叠加守卫/notes 后易超 CLAUDE.md「单函数 ≤50 行」。已在 §3.3 拆出 `_run_wf_windows(...)` 子函数承载逐窗口循环。
- **P2-4（性能）**：每窗口 `run_backtest` 都会对**完整高周期序列**重算指标，复杂度 O(窗口数 × HTF 长度)；窗口数受 `walk_forward_max_windows≤200` 约束总体可控，已在 §10 记风险并留预算化优化点。
- **P2-5（一致性）**：§10 原称「RunHistory 默认按 `mode="backtest"` 过滤」，实测 `RunHistory.tsx` 默认筛选为「全部」，WF run 会出现在默认列表（带 mode 徽章）。已在 §7/§10 订正，并建议追加「高级」筛选项。
- **P2-6（适度性）**：`cls(params)` 为读 `timeframes()/htf_warmup_bars()` 被多次实例化；已在 §3.3 改为构建一个元信息实例复用。
- **P2-7（一致性·路径）**：分段控件实际位于 `components/Chart/ChartToolbar.tsx`（非 Strategy 目录），已在 §4/§7 订正路径。

## 1. 概述 / Overview

本需求在现有回测能力上做两件事，且二者目的不同、**不可混为一谈**：

1. **优化基础回测（连续回测，最高优先级）**：现状下基础回测的 K 线取数受 `limit ≤ 1000`
   约束（见 `services/quant/app/schemas.py:28` 的 `BacktestReq.limit` 与
   `okx/rest_client.py:130` 的 `get_candles` 末尾 `candles[-limit:]` 截断）。当用户选择的
   `startTs ~ endTs` 跨度超过 1000 根时，回测**只会跑到最近的一部分数据**，导致总收益率、
   最大回撤等指标失真、不可信。本方案改为「**完整时间区间分页拉取**」：只要 OKX 有历史，
   就保证回测覆盖 `startTs ~ endTs` 的全部已确认 K 线（受一个明确的安全上限 `BACKTEST_MAX_BARS`
   约束），并把实际有效根数透明地写入 run 的 `config_json.effectiveCandleCount`。

2. **新增高级回测（滑动窗口 / Walk-forward）**：把一个长区间拆成多个等长窗口（窗口长度
   `windowBars`、滑动步长 `stepBars`），**每个窗口独立跑一次回测**（默认重置本金与仓位），
   再汇总各窗口表现。它回答的不是「这段时间最终赚多少」，而是「策略稳不稳、是否只在某段行情
   赚钱、参数是否过拟合、换个时间段还能不能盈利」。它是一项**独立的鲁棒性测试功能**，与连续
   回测并存，互不替代。

设计原则与现有架构保持一致：纯计算放 `engine/`，OKX 取数等 IO 留在边界（REST client / service），
回测在 `run_in_threadpool` 中执行（避免阻塞事件循环），新增接口**不改动现有接口的行为**，
camelCase 贯穿前后端契约，时间一律为**秒级 UTC 整数**，绝不伪造市场数据（取数失败显式报错）。
新增功能尽量**复用既有回测引擎**（`engine/backtester.run_backtest` + `engine/metrics.compute_metrics`），
仅在其上做窗口编排与跨窗口聚合。

---

## 2. 需求拆解与范围边界 / Scope

| 项 | 范围内（v1） | 范围外（后续迭代） |
|---|---|---|
| 基础回测全区间分页 | ✅ 全区间覆盖 + 安全上限 | 增量缓存复用（仅命中缓存的区间免拉） |
| 滑动窗口回测 | ✅ 单一原子策略 | 组合策略（combo）走窗口（v1 显式拒绝） |
| 入口模式切换 | ✅ 普通回测 \| 高级回测；高级下选「滑动窗口回测」 | Anchored/Expanding walk-forward、参数寻优 |
| 展示 | ✅ 窗口收益柱状图 / 回撤 / 胜率占比 / 最差最好 / 中位数 / 稳定性评分 | 每窗口独立净值曲线叠加 |
| 重置本金 | ✅ `resetCapital` 默认 true；false 时资金链式滚动 | 复利再投比例可调 |

**明确边界**：高级回测默认 `resetCapital=true`，每个窗口独立重置本金与仓位 —— 其语义是
「策略在不同时间片段的鲁棒性测试」，**不是连续交易模拟**。`resetCapital=false` 提供资金链式
滚动（上一窗口期末权益作为下一窗口起始本金）作为可选项，但默认关闭，避免与连续回测语义混淆。

**组合策略（combo）**：v1 不支持对组合策略做滑动窗口（组合需逐成员加载高周期帧、编排复杂），
后端遇到 `kind=="combo"` 的策略 id 时返回 `400 WF_COMBO_UNSUPPORTED`，前端在该策略选中时禁用
「高级回测」并给出提示。这是经过权衡的范围决策，留出明确扩展点（见 §10 风险）。

---

## 3. 技术设计 / Technical design

### 3.1 架构与数据流

```
[前端 StrategyPanel]
  普通回测  → api.backtest(id, {instId,bar,startTs,endTs,...})        （已存在，仅去掉 limit 硬上限）
  高级回测  → api.walkForward(id, {instId,bar,startTs,endTs,windowBars,stepBars,resetCapital,...})
                              │  Next rewrite /api/py/* → FastAPI /api/*
                              ▼
[api/strategies.py]  POST /api/strategies/{id}/walk-forward
                              │  解包 + 调用复用 helper（与现有 /backtest 同构）
                              ▼
[agent/services.py]  run_walk_forward(session, rest, ...)
        1) _resolve_strategy_spec()      取策略行 + 注册表类 + 解析后 params（组合→拒绝）
        2) load_range_candles()          【改造】完整区间分页拉取，覆盖 startTs~endTs
        3) load_htf_frames()             【改造】MTF 策略的高周期帧也按全区间覆盖（拉一次）
        4) plan_walk_forward_windows()   纯函数：按 (n, windowBars, stepBars) 生成窗口索引区间
        5) for each window:              切片 base candles，run_in_threadpool(run_backtest, 全量 htf_frames)
        6) summarize_windows()           纯函数：跨窗口聚合 → summary
        7) persist_walk_forward_result() 落一条 mode="walkforward" 的 parent run（detail 入 config_json）
                              ▼
        返回 {runId, summary, windows[]}
                              ▼
[前端 WalkForwardResults]  汇总卡片 + 窗口收益柱状图 + 窗口表（标注最好/最差窗口）
```

历史复看：`GET /api/runs/{run_id}/walk-forward` 从 `config_json` 还原同构结构，供 RunHistory 重开。

### 3.2 关键代码路径（基础回测全区间分页）

现状（`agent/services.py:32`）：
```python
async def load_range_candles(rest, *, inst_id, bar, limit, start_ts, end_ts):
    candles = await rest.get_candles(inst_id, bar, limit, before=end_ts, after=start_ts)
    ...  # get_candles 末尾 candles[-limit:] → 长区间被截断到最近 limit 根
```

改造为「以 `end_ts` 为起点向更早方向**反复翻页**，直到覆盖 `start_ts` 或触达安全上限」：

```python
# agent/services.py（改造后签名；去掉 limit 作为“覆盖上限”的语义，新增 max_bars 安全闸）
async def load_range_candles(rest, *, inst_id, bar, start_ts, end_ts,
                             max_bars: int | None = None) -> list[Candle]:
    """完整区间分页拉取：保证 [start_ts, end_ts] 全覆盖（受 max_bars 安全上限约束）。

    复用既有 rest.get_candles(before=cursor, limit=chunk)（OKX 游标语义已在其内部处理），
    外层循环按 cursor 向更早推进，dedupe by ts，直到 oldest <= start_ts / 空页 / 游标不前进 /
    达到 max_bars。max_bars 缺省取 settings.backtest_max_bars。
    """
    bar_sec = bar_to_seconds(bar)
    ceiling = max_bars or get_settings().backtest_max_bars
    expected = (end_ts - start_ts) // bar_sec + 1
    target = min(expected, ceiling)
    chunk = 1000                                   # 单次 get_candles 上限（其内部 history-candles 翻页）
    collected: dict[int, Candle] = {}
    cursor = end_ts + bar_sec                       # 取“早于 cursor”的更早历史
    pages_cap = target // chunk + 3                 # 防死循环
    for _ in range(pages_cap):
        batch = await rest.get_candles(inst_id, bar, chunk, before=cursor)
        if not batch:
            break
        for c in batch:
            collected[c.ts] = c
        oldest = min(c.ts for c in batch)
        if oldest <= start_ts or oldest >= cursor:  # 已覆盖左界 / 游标不再前进（含 stub 固定返回）→ 停
            break
        cursor = oldest
        if len(collected) >= target:
            break
    filtered = _filter_range_candles(list(collected.values()), start_ts, end_ts)
    filtered.sort(key=lambda c: c.ts)
    if not filtered:
        raise HTTPException(400, detail={"code": "EMPTY_RANGE",
                                         "message": "No confirmed candles in requested range"})
    return filtered
```

> **决策（v2 修订 · P2-1）**：**不改 `okx/rest_client.py`**，全区间翻页只在 service 层实现。
> ⚠️ 须澄清：`get_candles(before=cursor, limit=N)` **本身已内部翻页**累积到 `N` 根
> （`rest_client.py:160` `max_pages = limit//100 + 2`，每页 ≤100，末尾 `candles[-limit:]`）。
> 因此本设计的外层循环**并非「让 get_candles 能翻页」的必要条件**——单次大 `limit` 调用同样不改
> rest_client。外层循环的真实作用是：**以 `chunk=1000` 做分块 checkpoint，把每次 `get_candles` 的内部
> 页数限制在 ≤12，并由 `max_bars` 在外层收口**，避免一次性发起数百个内部请求且便于未来插入缓存命中/
> 进度回报。`oldest >= cursor` 的「游标不前进」判据保证返回固定批次的 stub 一轮即终止（防死循环）。
> 实现者若偏好更简形态，可等价地**单次** `get_candles(inst_id, bar, target, before=end_ts+bar_sec)`
> 后再 `_filter_range_candles`；两者覆盖结果一致，外层分块仅为有界性与可观测性，非语义必需。

**覆盖透明化（v2 修订 · P1-1，最高优先级需求）**：全区间分页**必须把覆盖情况透传到产品面**，否则
「`startTs ~ endTs` 全覆盖、收益率可信」无从验证，且 `max_bars`/OKX 历史深度导致的截断会被静默吞掉。
为此，`run_single_backtest` 的返回从 `{runId, metrics}` **扩展**为
`{runId, metrics, effectiveCandleCount, requestedBars, truncated}`（组合路径同样追加，向后兼容）：

```python
# run_single_backtest 内，拿到 filtered candles 后派生覆盖字段（纯计算，无新增 IO）
bar_sec = bar_to_seconds(bar)
requested_bars = (end_ts - start_ts) // bar_sec + 1
effective = len(candles)
# 截断判据：最早一根仍晚于左界（未覆盖到 start_ts，含撞 max_bars 上限 / OKX 历史不足两种情形）
truncated = candles[0].ts > start_ts + bar_sec
return {"runId": run.id, "metrics": result.metrics,
        "effectiveCandleCount": effective, "requestedBars": int(requested_bars),
        "truncated": bool(truncated)}
```
`config_json.effectiveCandleCount` 仍照写（DB 留痕），但**真值以响应字段为准**——前端据 `truncated`
显示「区间过长 / 历史不足，仅覆盖最近 N 根」横幅（§7）。

调用方相应改造（去掉把请求 `limit` 当覆盖上限的传参）：
- `run_single_backtest`（`services.py:128`）：`load_range_candles(rest, inst_id=..., bar=..., start_ts=..., end_ts=...)`，并按上方派生覆盖字段。
- `run_compare`（`services.py:190`）、`_run_combo`（`services.py:346`）：同样改为全区间覆盖（对比/组合不强制回包覆盖字段，避免响应形状漂移；如需可记入各 item 的 config）。
- `load_htf_frames`（`services.py:43`）：把 `limit = min(1000, span+warmup)` 改为按高周期 bar
  数全覆盖（用同样的反复翻页或直接对 HTF 调用新逻辑），并以 `max_bars` 安全上限收口。高周期根数
  远少于基础周期，但长区间（如 4H 跨 2 年 ≈ 4380 根）仍可能 > 1000，必须覆盖。
  **⚠️（v2 修订 · P1-4）必须保留 `start_ts` 之前 `htf_warmup_bars` 根的覆盖**：现状以 `before=end_ts+1`
  为锚、`limit = span + htf_warmup_bars` 向更早回溯，天然覆盖到 ≈ `start_ts - warmup·htf_sec`。改造为
  全区间后**不得**退化为仅取 `[start_ts, end_ts]`——否则滑动窗口里靠前的窗口拿不到已暖机的高周期指标，
  `merge_asof(backward)` 在 `start_ts` 处取到 NaN，信号失真。仍以 `end_ts` 为锚、保留 `+ htf_warmup_bars`
  即可。

`BacktestReq.limit / CompareReq.limit / ComboPreviewReq.limit`：保留字段以向后兼容，但**不再用作
覆盖上限**；若显式提供则作为 `max_bars` 覆盖（仍被 `backtest_max_bars` 二次夹取）。放宽校验上界
（`le=1000` → 取消上界或设为 `le=200000`），默认 `None`。前端 `runBacktest` 去掉硬编码的 `limit:1000`。

### 3.3 关键代码路径（滑动窗口编排）

窗口规划（纯函数，置于 `engine/backtester.py`，与 `run_backtest` 同档）：
```python
def plan_walk_forward_windows(n: int, window_bars: int, step_bars: int) -> list[tuple[int, int]]:
    """按 (总根数, 窗口长度, 步长) 生成 [(i0, i1), ...] 半开区间索引。

    - n < window_bars：返回单个退化窗口 [(0, n)]（窗口数=1，前端标注“数据不足一个完整窗口”）。
    - 否则：start = 0, step, 2*step, ... 只要 start + window_bars <= n 即纳入。
    - 末尾不足一个完整窗口的尾段被丢弃（uncoveredBars 记入 config，前端可提示）。
    """
    if n <= 0 or window_bars <= 0 or step_bars <= 0:
        return []
    if n < window_bars:
        return [(0, n)]
    out, start = [], 0
    while start + window_bars <= n:
        out.append((start, start + window_bars))
        start += step_bars
    return out
```

聚合与稳定性评分（纯函数，置于 `engine/metrics.py`）：
```python
def compute_stability_score(returns: list[float], profitable_rate: float) -> float:
    """稳定性评分 ∈ [0,1]，越高越稳健。

    consistency = 1/(1+cv)，cv = std/(|avg|+1e-9)（离散度惩罚，越分散越低）；
    score = 0.6*profitable_rate + 0.4*consistency，夹取 [0,1]，round(6)。
    例：全部窗口正收益且方差小 → 接近 1；盈亏参半且高离散 → 接近 0。
    （v1 启发式，纯函数可单测；后续可演进为含 calmar / 尾部惩罚的版本。）
    """

def summarize_windows(windows: list[dict], *, uncovered_bars: int = 0,
                      notes: list[str] | None = None) -> dict:
    """跨窗口聚合，返回与 TS WalkForwardSummary 对齐的 dict：
    windowCount / profitableWindows / profitableRate / medianReturn / avgReturn /
    worstReturn / bestReturn / avgMaxDrawdown / stabilityScore / uncoveredBars / notes。
    returns 取各窗口 metrics.totalReturn；avgMaxDrawdown 取各窗口 metrics.maxDrawdown 均值（≤0）。
    （v2 修订 · P1-2）`uncovered_bars` 为末尾被丢弃的尾段根数；`notes` 承载用户可读提示
    （窗口偏短 / 数据不足一个窗口 / 区间截断 等），二者随 summary 一并回包，前端据此提示。"""
```

编排（`agent/services.py`，新增 `run_walk_forward`）：
（v2 修订 · P2-3 / P2-6）逐窗口循环抽到 `_run_wf_windows(...)` 子函数，使 `run_walk_forward`
满足 CLAUDE.md「单函数 ≤50 行」；策略元信息（`timeframes()/htf_warmup_bars()`）只实例化**一次**复用。

```python
async def _run_wf_windows(cls, params, candles, bar, plan, *, reset_capital, icap, fee, slip, lev,
                          htf_frames, warmup) -> tuple[list[dict], list[str]]:
    """逐窗口跑回测并收集窗口结果与提示 notes（纯编排，无 IO）。"""
    windows: list[dict] = []
    notes: list[str] = []
    rolling = icap
    short_flagged = False
    for (i0, i1) in plan:
        sub = candles[i0:i1]
        cap = icap if reset_capital else rolling
        # 每个窗口用全新策略实例，杜绝跨窗口状态泄漏（鲁棒性独立性的硬保证）。
        result = await run_in_threadpool(run_backtest, cls(params), sub, bar,
                                         initial_capital=cap, taker_fee_bps=fee, slippage_bps=slip,
                                         leverage=lev, htf_frames=htf_frames)
        if not reset_capital and result.equity_points:
            rolling = result.equity_points[-1].equity
        if not short_flagged and len(sub) < warmup + 5:   # P1-3：窗口偏短，暖机吃满 → 提示一次
            notes.append("部分窗口长度接近策略暖机根数，指标可能偏弱，建议增大 windowBars")
            short_flagged = True
        windows.append({"startTs": int(sub[0].ts), "endTs": int(sub[-1].ts),
                        "metrics": result.metrics, "tradeCount": result.metrics["tradeCount"]})
    return windows, notes


async def run_walk_forward(session, rest, *, strategy_id, inst_id, bar, start_ts, end_ts,
                           window_bars, step_bars, reset_capital,
                           initial_capital=None, fees=None, slippage=None, leverage=None) -> dict:
    row, cls, params = await _resolve_strategy_spec(session, strategy_id)   # combo → 400 WF_COMBO_UNSUPPORTED
    meta_strategy = cls(params)                                             # P2-6：元信息单实例复用
    icap, fee, slip, lev = _resolve_costs(get_settings(), initial_capital, fees, slippage, leverage)
    candles = await load_range_candles(rest, inst_id=inst_id, bar=bar, start_ts=start_ts, end_ts=end_ts)
    await repo.upsert_candles(session, inst_id, bar, candles)
    warmup = max(meta_strategy.warmup(), 1)
    htf_frames = {}
    tfs = [tf for tf in meta_strategy.timeframes() if tf != bar]
    if tfs:   # P1-4：load_htf_frames 保留 start_ts 之前 htf_warmup_bars 覆盖
        htf_frames = await load_htf_frames(rest, inst_id=inst_id, start_ts=start_ts, end_ts=end_ts,
                                           base_seconds=bar_to_seconds(bar), timeframes=tfs,
                                           htf_warmup_bars=meta_strategy.htf_warmup_bars())
        for tf, cs in htf_frames.items():
            await repo.upsert_candles(session, inst_id, tf, cs)

    plan = plan_walk_forward_windows(len(candles), window_bars, step_bars)
    if not plan:
        raise HTTPException(400, {"code": "WF_RANGE_TOO_SHORT", "message": "区间内无足够 K 线生成窗口"})
    if len(plan) > get_settings().walk_forward_max_windows:
        raise HTTPException(400, {"code": "WF_TOO_MANY_WINDOWS",
                                  "message": "窗口数过多，请增大步长或缩短区间"})

    parent = await repo.create_run(session, strategy_id=strategy_id, inst_id=inst_id, bar=bar,
                                   mode="walkforward",
                                   config={"kind": "walkforward", "windowBars": window_bars,
                                           "stepBars": step_bars, "resetCapital": reset_capital,
                                           "initialCapital": icap, "fees": fee, "slippage": slip,
                                           "leverage": lev, "startTs": start_ts, "endTs": end_ts,
                                           "effectiveCandleCount": len(candles)})
    windows, notes = await _run_wf_windows(cls, params, candles, bar, plan,
                                           reset_capital=reset_capital, icap=icap, fee=fee, slip=slip,
                                           lev=lev, htf_frames=htf_frames, warmup=warmup)
    # P1-2：末尾被丢弃的尾段根数 + 退化提示，随 summary 回包
    covered = plan[-1][1] if plan else 0
    uncovered = max(0, len(candles) - covered)
    if len(plan) == 1 and len(candles) < window_bars:
        notes.append("区间数据不足一个完整窗口，已退化为单窗口")
    summary = summarize_windows(windows, uncovered_bars=uncovered, notes=notes)
    await repo.persist_walk_forward_result(session, parent, summary=summary, windows=windows,
                                           start_ts=int(candles[0].ts), end_ts=int(candles[-1].ts))
    return {"runId": parent.id, "summary": summary, "windows": windows}
```

**MTF 因果性说明**：全量 `htf_frames` 一次性加载后传给每个窗口的 `run_backtest`，
`strategy.attach_timeframes` 用 `merge_asof(backward)` 把高周期特征因果并入窗口内 base df
（见 `engine/mtf.py:50` `causal_htf_merge`）。由于高周期列表覆盖区间且早于窗口起点，每个窗口
都能拿到「截至该根、已收盘的最新高周期值」，无未来泄漏、无需逐窗口重拉。每个窗口的前 `warmup`
根用于自暖机（`run_backtest` 内 `i >= warmup` 才开仓），故 `windowBars` 应明显大于 `warmup`
（v2 修订 · P1-3：schema 下界已抬到 `ge=100`，运行期 `windowBars < warmup+5` 经 `notes` 提示）。

> **（v2 修订 · P1-4）前置暖机硬约束**：上面「早于窗口起点」对**最靠前的窗口**也必须成立——
> 即高周期帧必须覆盖到 `start_ts` 之前至少 `htf_warmup_bars` 根。这要求 §3.2 改造后的
> `load_htf_frames` 保留 `+ htf_warmup_bars` 与「以 `end_ts` 为锚向更早回溯」的取数方式；否则第一个
> 窗口在 `start_ts` 处 `merge_asof` 取到 NaN，高周期门控失效。§9.1 增设对应断言护栏。

> **（v2 修订 · P2-4）性能说明**：每个窗口的 `run_backtest` 都会对**完整** `htf_frames` 重算高周期
> 指标（`attach_timeframes` 内 `candles_to_df` + 指标列），复杂度 ≈ O(窗口数 × HTF 长度)。窗口数受
> `walk_forward_max_windows ≤ 200` 硬约束、HTF 根数远少于基础周期，单次高级回测总体可控（秒级～十几秒）；
> 若未来出现瓶颈，可把高周期指标预计算一次再按窗口切片复用（留作后续优化点，v1 不做）。

---

## 4. 文件 / 模块变更计划 / File & module change plan

### 后端（services/quant）

| 文件 | 新建/修改 | 一句话意图 |
|---|---|---|
| `app/config.py` | 修改 | 新增 `backtest_max_bars`（默认 20000）、`walk_forward_max_windows`（默认 200）设置项 |
| `app/okx/rest_client.py` | **不改** | 全区间翻页在 service 层实现，OKX 游标语义保持单一真源 |
| `app/engine/backtester.py` | 修改 | 新增纯函数 `plan_walk_forward_windows(n, window_bars, step_bars)` |
| `app/engine/metrics.py` | 修改 | 新增纯函数 `compute_stability_score(...)`、`summarize_windows(windows)` |
| `app/agent/services.py` | 修改 | 改造 `load_range_candles` / `load_htf_frames` 为全区间覆盖；新增 `_resolve_strategy_spec`、`run_walk_forward`；更新 `run_single_backtest/run_compare/_run_combo` 去除 limit 覆盖上限 |
| `app/schemas.py` | 修改 | 新增 `WalkForwardReq`（含 §5.1 校验）、`walk_forward_out(run, summary, windows)` 序列化；放宽 `BacktestReq/CompareReq/ComboPreviewReq.limit` 为可选、解除 1000 上界；**（v2·P1-1）** `BacktestReq` 路由响应不再仅 `{runId,metrics}`，由 service 追加 `effectiveCandleCount/requestedBars/truncated`（无需新 pydantic 出参模型，路由直接回 dict，与现状一致） |
| `app/api/strategies.py` | 修改 | 新增路由 `POST /strategies/{id}/walk-forward` |
| `app/api/runs.py` | 修改 | 新增路由 `GET /runs/{run_id}/walk-forward`（历史复看，非 walkforward → 404） |
| `app/db/repositories.py` | 修改 | 新增 `persist_walk_forward_result(session, run, *, summary, windows, start_ts, end_ts)`；可加 `get_walk_forward(session, run_id)` 解析 config_json。**（v2·P1-5）契约钉死**：该函数**读取 `run.config_json` 并合并**（`cfg = json.loads(run.config_json); cfg["summary"]=summary; cfg["windows"]=windows`，**不覆盖**既有窗口参数键），写回 `run.config_json`；构造并写 `run.metrics_json`= §6.1 投影；设 `run.start_ts/end_ts/status="done"` 后 `commit`。不落 `Trade`/`EquityPoint` |

### 前端（apps/web）

| 文件 | 新建/修改 | 一句话意图 |
|---|---|---|
| `lib/types.ts` | 修改 | 新增 `BacktestKind`、`WalkForwardWindow`、`WalkForwardSummary`（含 **`notes/uncoveredBars`**，v2·P1-2）、`WalkForwardResult` 类型；**（v2·P1-1）** `BacktestResult` 追加可选 `effectiveCandleCount?/requestedBars?/truncated?`（旧字段不变，向后兼容） |
| `lib/api.ts` | 修改 | 新增 `walkForward(id, body)`、`walkForwardResult(runId)`；`backtest` 去掉硬编码 `limit`（含 `StrategyPanel.tsx:487` 的 `limit:1000`，v2·P1-1） |
| `lib/store.ts` | 修改 | 新增 `backtestKind` + `wfParams`（windowBars/stepBars/resetCapital）状态与 setter；persist version 12→13 + migrate |
| `lib/walkForward.ts` | 新建 | 纯展示辅助：`windowBarGeometry(returns)`、`bestWorstWindowIndex(windows)`（可单测） |
| `components/Strategy/BacktestModeSwitch.tsx` | 新建 | 「普通回测 \| 高级回测」分段切换 + 高级下「滑动窗口回测」（v1 单选/占位）；复刻 `components/Chart/ChartToolbar.tsx` 的 `MODES` 分段样式（v2·P2-7 路径订正） |
| `components/Strategy/WalkForwardForm.tsx` | 新建 | windowBars / stepBars / resetCapital 表单 + 「运行高级回测」按钮（显式触发，不自动跑） |
| `components/Strategy/WalkForwardResults.tsx` | 新建 | 汇总卡片 + 窗口收益柱状图 + 窗口明细表（标注最好/最差窗口） |
| `components/Strategy/WalkForwardChart.tsx` | 新建 | 每窗口收益率柱状图（内联 SVG/div，无新增依赖；正绿负红） |
| `components/Strategy/StrategyPanel.tsx` | 修改 | `mode==="backtest"` 时渲染 `BacktestModeSwitch`；`backtestKind==="walkforward"` 时渲染表单+结果替代单条净值/MetricsCards；新增 `walkForward` mutation；自动回测 effect 增加 `&& backtestKind==="simple"` 守卫 |
| `components/Strategy/StrategyDetail.tsx` | 修改 | 承载子切换与参数表单的挂载点（与现有回测控件并列） |

### 测试

| 文件 | 新建/修改 | 意图 |
|---|---|---|
| `services/quant/tests/test_walk_forward.py` | 新建 | `plan_walk_forward_windows` / `compute_stability_score` / `summarize_windows` / `run_walk_forward` / 全区间分页 / API 路由与校验 |
| `services/quant/tests/test_*backtest*.py`（既有） | 修改 | 既有伪 rest client 适配新的 `load_range_candles`（重复 `get_candles(before=cursor)` 调用须能终止） |
| `apps/web/lib/walkForward.test.ts` | 新建 | 柱状图几何、最好/最差窗口定位 |
| `apps/web/lib/store.test.ts`（既有） | 修改 | `backtestKind` 持久化 + v12→v13 迁移 |

---

## 5. 接口设计 / Interface design

### 5.1 REST：新增 `POST /api/strategies/{id}/walk-forward`

请求体（`WalkForwardReq`，camelCase；继承基础回测字段并新增三项）：
```jsonc
{
  "instId": "BTC-USDT-SWAP",
  "bar": "1H",
  "startTs": 1700000000,
  "endTs": 1715000000,
  "windowBars": 1000,        // 窗口长度：[100, 5000]（v2·P1-3：下界 50→100，须显著大于策略 warmup）
  "stepBars": 200,           // 滑动步长：[10, 5000]，且 <= windowBars（避免出现未覆盖空档）
  "resetCapital": true,      // 每个窗口是否重置本金，默认 true
  "initialCapital": 10000,   // 可选，缺省取 settings.initial_capital
  "fees": 5.0,               // 可选 taker_fee_bps
  "slippage": 2.0,           // 可选 slippage_bps
  "leverage": 1.0,           // 可选
  "maxBars": null            // 可选，覆盖 backtest_max_bars（仍被其二次夹取）
}
```
校验（pydantic）：`startTs < endTs`；`windowBars` `ge=100,le=5000`（v2·P1-3：抬高下界，防窗口长度
落入策略 warmup 内导致整窗口零成交）；`stepBars` `ge=10,le=5000`；`@model_validator` 保证 `stepBars <= windowBars`。

响应（`200`，与任务约定结构一致）：
```jsonc
{
  "runId": 123,
  "summary": {
    "windowCount": 21,
    "profitableWindows": 14,
    "profitableRate": 0.6667,
    "medianReturn": 0.031,
    "avgReturn": 0.027,
    "worstReturn": -0.084,
    "bestReturn": 0.152,
    "avgMaxDrawdown": -0.063,   // ≤ 0，与全系统回撤口径一致
    "stabilityScore": 0.71,     // [0,1]
    "uncoveredBars": 12,        // （v2·P1-2）末尾不足一个完整窗口被丢弃的根数；前端可提示
    "notes": []                 // （v2·P1-2）用户可读提示（窗口偏短 / 数据不足一个窗口 / 截断 等）
  },
  "windows": [
    { "startTs": 1700000000, "endTs": 1703600000,
      "metrics": { "totalReturn": 0.031, "winRate": 0.55, "profitFactor": 1.4,
                   "maxDrawdown": -0.04, "sharpe": 1.2, "tradeCount": 18, "avgPnl": 5.2 },
      "tradeCount": 18 }
    // ... 每窗口一项
  ]
}
```

错误码（沿用 `{error:{code,message}}` 统一错误体，见 `main.py:65`）：

| HTTP | code | 触发 |
|---|---|---|
| 400 | `WF_COMBO_UNSUPPORTED` | 目标策略为组合（kind=="combo"） |
| 400 | `WF_RANGE_TOO_SHORT` | 区间内有效 K 线为空/不足以生成任何窗口 |
| 400 | `WF_TOO_MANY_WINDOWS` | 生成窗口数 > `walk_forward_max_windows` |
| 400 | `EMPTY_RANGE` | `load_range_candles` 在区间内无已确认 K 线 |
| 400 | (pydantic) | 参数越界 / `startTs>=endTs` / `stepBars>windowBars` |
| 404 | `NOT_FOUND` | 策略不存在 |
| 502 | `OKX_*` | OKX 取数失败（绝不回退假数据） |

### 5.2 REST：新增 `GET /api/runs/{run_id}/walk-forward`

从 parent run 的 `config_json` 还原 `{runId, summary, windows}`（与 POST 同构）。
若该 run `mode != "walkforward"` → `404 NOT_WALK_FORWARD`。用于 RunHistory 重开历史高级回测。

### 5.3 前端 API client（`lib/api.ts`）

```ts
walkForward: (id: number, body: {
  instId: string; bar: string; startTs: number; endTs: number;
  windowBars: number; stepBars: number; resetCapital: boolean;
  initialCapital?: number; fees?: number; slippage?: number; leverage?: number; maxBars?: number;
}) => request<WalkForwardResult>(`/strategies/${id}/walk-forward`,
                                 { method: "POST", body: JSON.stringify(body) }),

walkForwardResult: (runId: number) =>
  request<WalkForwardResult>(`/runs/${runId}/walk-forward`),
```
（`request` 已带 `/api/py` 基址，经 Next rewrite → 后端 `/api/*`。）

---

## 6. 数据模型 / Data model

### 6.1 持久化（**免迁移**，复用现有表）

不新增表、不新增列。一次高级回测落 **一条** `StrategyRun`：

- `mode = "walkforward"`（现有列为自由字符串，见 `models.py:52`）。
- `start_ts / end_ts`：整体覆盖区间（首/末有效 K 线 ts）。
- `config_json`（Text）：存窗口参数 + 汇总 + 明细：
  ```jsonc
  { "kind": "walkforward", "windowBars": 1000, "stepBars": 200, "resetCapital": true,
    "initialCapital": 10000, "fees": 5, "slippage": 2, "leverage": 1,
    "startTs": ..., "endTs": ..., "effectiveCandleCount": 4200,   // v2·P2-2：与基础回测同名
    "summary": { ... }, "windows": [ ... ] }
  ```
  > （v2·P1-5）`create_run` 先落**不含** `summary/windows` 的上述 config；之后
  > `persist_walk_forward_result` **读出并合并**（叠加 `summary`/`windows`，不覆盖既有键）再写回。
- `metrics_json`（Text）：存**展示投影**（Metrics 形状，仅供通用 run 列表卡片渲染，非真实单次回测指标）：
  `totalReturn=avgReturn`、`winRate=profitableRate`、`maxDrawdown=avgMaxDrawdown`、
  `sharpe=stabilityScore`、`tradeCount=Σ窗口 tradeCount`、`profitFactor=0`、`avgPnl=medianReturn`。
  → （v2·P1-5）此投影**由 `persist_walk_forward_result` 构造**（summary 已在手），写入 `run.metrics_json`；
  `run_out`（`schemas.py:170`）无需改动即可返回；RunHistory 用 `mode` 徽章区分，真实明细由
  §5.2 接口取。**不**为高级回测落 `Trade` / `EquityPoint`（窗口各自独立、无单条连续净值）。

> 选型理由：`config_json` 本就是自由 JSON Text，窗口数受 `walk_forward_max_windows`（≤200）约束，
> 每窗口仅 ~9 个数值，整体 KB 级，落 config_json 完全可控，且**避免 SQLite 加列迁移**（项目用
> `Base.metadata.create_all`，对既有表加列不会自动生效）。

### 6.2 内存形状（与前端 TS 对齐，`lib/types.ts`）

```ts
export type BacktestKind = "simple" | "walkforward";

export interface WalkForwardWindow {
  startTs: number;
  endTs: number;
  metrics: Metrics;     // 复用现有 Metrics（totalReturn/winRate/maxDrawdown/sharpe/...）
  tradeCount: number;   // == metrics.tradeCount，冗余便于表格直读
}

export interface WalkForwardSummary {
  windowCount: number;
  profitableWindows: number;
  profitableRate: number;
  medianReturn: number;
  avgReturn: number;
  worstReturn: number;
  bestReturn: number;
  avgMaxDrawdown: number;  // ≤ 0
  stabilityScore: number;  // [0,1]
  uncoveredBars: number;   // （v2·P1-2）末尾被丢弃的尾段根数
  notes: string[];         // （v2·P1-2）用户可读提示（窗口偏短 / 数据不足一个窗口 / 截断 等）
}

export interface WalkForwardResult {
  runId: number;
  summary: WalkForwardSummary;
  windows: WalkForwardWindow[];
}

// （v2·P1-1）基础回测响应扩展：旧字段不变，新增三项可选，承载「全区间覆盖」透明度。
export interface BacktestResult {
  runId: number;
  metrics: Metrics;
  effectiveCandleCount?: number;  // 实际参与回测的已确认 K 线根数
  requestedBars?: number;         // (endTs-startTs)/barSec + 1，请求区间应有根数
  truncated?: boolean;            // 未覆盖到 startTs（撞 max_bars 上限或 OKX 历史不足）
  combo?: ComboResult;            // 组合路径才有（现状字段，保持不变）
}
```

### 6.3 配置项（`config.py`，BaseSettings 风格，env 大写）

```python
backtest_max_bars: int = Field(default=20000, ge=1000)        # 单次回测取数安全上限（防超长区间拖垮）
walk_forward_max_windows: int = Field(default=200, ge=1)      # 高级回测窗口数硬上限
```

### 6.4 前端 store 形状（`store.ts`，persist key `aragon-ui`，version 12→13）

```ts
backtestKind: BacktestKind;                 // 默认 "simple"，持久化
wfParams: { windowBars: number; stepBars: number; resetCapital: boolean };  // 默认 {1000,200,true}，持久化
walkForwardResult: WalkForwardResult | null;  // 不持久化（避免刷新后陈旧），运行后写入
setBacktestKind(kind): void;                // 切到 walkforward 时不切 chartView 到 run-range
setWfParams(patch): void;
```
`migrate(persisted, v)`：`v < 13` 时补 `backtestKind:"simple"`、`wfParams` 默认值。

---

## 7. 前端展示设计 / UI

1. **入口**：`StrategyPanel` 在 `mode==="backtest"` 时，于参数区上方渲染 `BacktestModeSwitch`
   分段控件「普通回测 | 高级回测」（复刻 `ChartToolbar` 的 `MODES` 分段样式，
   见 frontend `ChartToolbar.tsx`）。选「高级回测」后展开二级选择「滑动窗口回测」（v1 仅此一项，
   样式预留为可扩展的 Select/分段）。状态写入 `store.backtestKind`。
2. **参数**：`WalkForwardForm` 三个控件 —— `windowBars`（建议预设 500/1000/2000）、
   `stepBars`（建议 100/250/500）、`resetCapital` 开关（默认开）。底部「运行高级回测」按钮**显式触发**
   （区别于普通回测的随参自动跑：滑动窗口计算较重，不自动触发）。
3. **结果展示**（`WalkForwardResults`，替代普通回测的单条净值 + `MetricsCards`）：
   - **汇总卡片**：窗口数、盈利窗口占比（profitableRate）、中位数收益、平均收益、最好窗口、
     最差窗口、平均最大回撤、稳定性评分（8 项，复刻 `MetricsCards` 的卡片网格风格）。
   - **窗口收益柱状图**（`WalkForwardChart`）：横轴窗口序号，纵轴 `metrics.totalReturn`，
     正绿负红（`var(--long)`/`var(--short)`），最好/最差窗口高亮。内联 SVG/div 实现，无新增依赖。
   - **窗口明细表**：窗口# / 时间区间 / 收益率 / 最大回撤 / 成交笔数；最好/最差行标注。
   - **（v2·P1-2）提示条**：`summary.notes` 非空时在汇总卡片上方渲染一行警示（如「窗口偏短」「数据
     不足一个完整窗口」）；`summary.uncoveredBars > 0` 时附注「末尾 N 根未纳入任何窗口」。
4. **左图行为**：进入 `walkforward` kind **不**切到 `run-range`（无单条净值），左图维持 K 线视图；
   结果只在右侧 StrategyPanel 呈现。普通回测路径与现状逐字节一致。
5. **（v2·P1-1）基础回测覆盖横幅**：普通回测返回 `truncated===true` 时，在 `MetricsCards` 上方显示一条
   非阻断警示「区间过长 / 历史不足，仅覆盖最近 `effectiveCandleCount` / `requestedBars` 根，收益率为部分
   区间结果」。这是「全区间覆盖、收益率可信」需求的可见性兜底——绝不静默截断。`truncated===false`
   时不渲染，普通回测观感与现状一致。

---

## 8. 操作时序 / Sequence

1. 用户在回测面板切到「高级回测」→「滑动窗口回测」，填 `windowBars/stepBars/resetCapital`，点「运行」。
2. 前端 `api.walkForward(id, body)` → `POST /api/py/strategies/{id}/walk-forward`。
3. 后端解析策略（组合→400）→ 全区间分页拉 K 线（+ MTF 高周期一次性）→ 规划窗口 →
   逐窗口 `run_in_threadpool(run_backtest, fresh strategy)` → 聚合 summary。
4. 落一条 `mode="walkforward"` run（明细入 `config_json`，投影入 `metrics_json`）。
5. 返回 `{runId, summary, windows}`；前端写 `store.walkForwardResult` 并渲染分布视图。
6. （历史复看）RunHistory 选中该 run → `api.walkForwardResult(runId)` → `GET /runs/{id}/walk-forward`。

---

## 9. 测试与验收标准 / Testing & acceptance

### 9.1 后端单测（pytest，真实集成风格，OKX 用伪 client）

- `plan_walk_forward_windows`：① 整除窗口数正确；② `n<window_bars` 退化为单窗口；
  ③ 末尾尾段被丢弃且窗口边界精确；④ 退化入参（n/W/S ≤ 0）返回空。
- `compute_stability_score`：给定向量断言区间与单调性（全正低方差→接近 1；盈亏参半高方差→低）。
- `summarize_windows`：中位数/均值/最好/最差/盈利占比/平均回撤算对（含偶数个窗口的中位数）。
- `load_range_candles` 全覆盖：伪 client 跨多页返回 > 1000 根，断言覆盖到 `start_ts`、
  根数 ≈ 期望、去重正确；并断言「固定返回同一批次的 stub」一轮即终止（不死循环）。
- **（v2·P1-1）覆盖透明度**：① 长区间全覆盖时 `run_single_backtest` 返回 `truncated==False` 且
  `effectiveCandleCount==requestedBars`（容差内）；② 人为把 `backtest_max_bars` 调小（或伪 client
  历史不足）时返回 `truncated==True` 且 `effectiveCandleCount < requestedBars`、`candles[0].ts > start_ts`。
- **（v2·P1-4）MTF 前置暖机覆盖**：MTF 策略 + 多页伪 HTF client，断言 `load_htf_frames` 返回的最早
  高周期 ts **早于** `start_ts`（覆盖到暖机段），且最靠前窗口在 `start_ts` 处的高周期列非 NaN。
- `run_walk_forward` 端到端：用内置简单策略 + 伪 client，断言 `windowCount` 与窗口边界 ts；
  `resetCapital=true` 时各窗口起始本金一致；`false` 时链式滚动；组合策略 → `WF_COMBO_UNSUPPORTED`。
  **（v2·P1-2）** 断言 `summary.uncoveredBars` 与末尾尾段一致；窗口偏短 / 退化单窗口时 `summary.notes` 非空。
- **（v2·P1-5）persist 合并**：`persist_walk_forward_result` 后重查该 run，断言 `config_json` **同时含**
  原窗口参数键（windowBars/stepBars/...）与 `summary`/`windows`（未被覆盖），`metrics_json` 为投影、
  `status=="done"`、`start_ts/end_ts` 正确。
- API：`POST /strategies/{id}/walk-forward` 正常 200 结构齐全（含 `summary.notes/uncoveredBars`）；越界/
  `startTs>=endTs`/`stepBars>windowBars`/`windowBars<100`/窗口过多/区间过短各自对应错误码；
  `GET /runs/{id}/walk-forward` 复看与非 walkforward run → 404。
- **回归护栏**：既有 `/backtest`、`/compare` 测试在 `load_range_candles` 改造后仍绿
  （伪 rest client 适配重复 `get_candles` 调用）；`/backtest` 响应**新增** `effectiveCandleCount/requestedBars/truncated`
  不破坏既有断言（旧断言只读 `runId/metrics`）。

### 9.2 前端单测（vitest，组件测试须 `NODE_ENV=test`）

- `lib/walkForward.ts`：柱状图几何映射（正负、最大绝对值归一）、最好/最差窗口定位。
- `lib/store.ts`：`backtestKind`/`wfParams` 持久化字段进入 `partialize`；v12→v13 迁移补默认值。
- 可选组件测试：`WalkForwardResults` 给定 mock summary/windows 渲染 8 项汇总与表格行数；
  `summary.notes` 非空 / `uncoveredBars>0` 时渲染提示条（v2·P1-2）。
- （v2·P1-1）基础回测横幅：给定 `truncated===true` 的 mock `BacktestResult`，断言渲染「仅覆盖最近 N 根」
  警示；`truncated===false`（或字段缺省，向后兼容）时不渲染。

### 9.3 验收标准（Definition of Done）

1. 选超过 1000 根的长区间跑**普通回测**，**响应** `effectiveCandleCount` 反映全区间根数（不再固定 ≤1000）、
   `truncated===false`（区间在 `backtest_max_bars` 内且 OKX 有历史时），总收益率随区间扩展而变化（覆盖完整）；
   超 `backtest_max_bars` 时 `truncated===true` 且前端显示「仅覆盖最近 N 根」横幅（v2·P1-1，不再静默截断）。
2. 切「高级回测 → 滑动窗口回测」，填参运行后返回 `{runId, summary, windows}`，前端展示窗口收益
   柱状图、平均最大回撤、盈利窗口占比、最好/最差窗口、中位数收益、稳定性评分。
3. `resetCapital` 默认 true：每窗口独立重置本金与仓位（鲁棒性测试语义）。
4. 现有普通回测 / 对比 / 组合 / 实时路径行为不回归；新增接口不影响既有接口。
5. 后端 pytest、前端 vitest 全绿；项目可启动 / 编译。

---

## 10. 风险与缓解 / Risks & mitigations

| 风险 | 影响 | 缓解 |
|---|---|---|
| 长区间全量取数耗时 / OKX 限频 | 单次回测变慢、可能触达 OKX 历史深度上限 | `backtest_max_bars` 安全上限 + `get_candles` 内置节流/退避；超上限只取最近部分并经**响应字段** `truncated/effectiveCandleCount/requestedBars` 透明回传（v2·P1-1），前端横幅「区间过长，仅覆盖最近 N 根」——绝不静默截断 |
| 改造 `load_range_candles` 影响 compare/combo 既有测试 | 回归 | 伪 rest client 适配 + 「游标不前进即停」判据保证 stub 终止；保留 `EMPTY_RANGE` 行为；以等价性测试护栏 |
| 跨窗口策略状态泄漏 | 鲁棒性结论失真 | 每窗口 `cls(params)` **全新实例**；窗口间不共享 broker/df |
| 窗口过短 → 指标恒为 0（暖机吃满） | 误导用户 | （v2·P1-3）schema `windowBars ge=100`（抬高下界）；运行期 `windowBars < warmup+5` 时经 `summary.notes` 输出「窗口偏短」提示，前端渲染（不再是无处落地的口头提示） |
| 稳定性评分含义争议 | 解读偏差 | 评分公式写入 docstring + 单测固定向量；前端 tooltip 标注「v1 启发式，越高越稳健」 |
| `metrics_json` 投影被误当真实单次指标 | 误读 | RunHistory 以 `mode="walkforward"` 徽章区分；真实明细经 §5.2 取；文档显式标注「展示投影」 |
| 组合策略不支持高级回测 | 功能缺口 | v1 显式 `WF_COMBO_UNSUPPORTED` + 前端禁用并提示；留扩展点（逐成员窗口编排为后续迭代） |
| 高级回测落 run 进入通用 run 列表造成噪音 | 列表混淆 | （v2·P2-5 订正）`RunHistory.tsx` 默认筛选为「全部」（**非** `backtest`），故 walkforward run 会出现在默认列表——但带 `mode` 徽章可辨；后端 `list_runs` 已支持 `mode` 过滤。建议 RunHistory `MODE_FILTERS` 追加「高级」（`mode=walkforward`）项，让用户可单列查看 |
| 每窗口对完整 HTF 重算指标（P2-4） | 窗口多时变慢 | 窗口数 `walk_forward_max_windows ≤ 200` + HTF 根数少，总体秒级；留「HTF 指标预计算一次再切片」为后续优化点 |

---

## 11. 命名与约定 / Conventions

- 时间：秒级 UTC 整数贯穿前后端；OKX 毫秒换算仅在 adapter 内。
- 契约字段：camelCase（前后端一致）；错误体 `{error:{code,message}}`。
- 纯计算（窗口规划、聚合、稳定性评分）置于 `engine/`，IO（取数/落库）留在 service/repo 边界。
- 回测在 `run_in_threadpool` 中执行；绝不伪造市场数据，取数失败显式 5xx。
- 新增功能不 monkeypatch、不特判 desktop；遵循单文件 ≤800 行、单函数 ≤50 行、嵌套 ≤4 层等
  CLAUDE.md 清洁码阈值；公开函数写 docstring（参数/返回/异常）。

---

## 实施过程发现的方案缺陷 / Issues Found During Implementation（Subtask #2）

> 落地时发现 §4「文件 / 模块变更计划」存在两处**轻微不完备**（非阻断性，均已就地修正并通过全量测试）。

1. **`load_range_candles` 签名变更的连带调用方漏列（已修正）**：§4 变更计划仅列出
   `run_single_backtest/run_compare/_run_combo` 三处去除 `limit` 覆盖上限的传参，但
   `load_range_candles` 的签名实际还被 `app/agent/tools/signal_tools.py:167` 与
   `app/agent/tools/regime_tools.py:188` 两处 Agent 工具以 `limit=` 关键字调用。签名由
   `(…, limit, start_ts, end_ts)` 改为 `(…, start_ts, end_ts, max_bars=None)` 后，这两处不更新会
   `TypeError`。**修正**：两处 `limit=limit` → `max_bars=limit`（语义等价——原 `limit` 即取数根数上限，
   现作为 `max_bars` 安全闸，仍被 `backtest_max_bars` 二次夹取）。

2. **既有 `/backtest`、`/compare` 「请求区间」测试断言 OLD 单次取数形状（已适配）**：§4 测试变更计划
   仅笼统提到「既有伪 rest client 适配新的 `load_range_candles`（重复 `get_candles` 调用须能终止）」，
   但 `tests/test_api.py::test_backtest_uses_requested_time_range` 与
   `tests/test_compare.py::test_compare_uses_requested_time_range` 还**精确断言**了旧的单次调用入参
   `{limit, before=end_ts, after=start_ts}`。全区间分页改以 `before=end_ts+bar_sec` 为锚、不再用 `after`，
   故这两条断言需同步更新为新契约（断言锚游标 `before==end_ts+bar_sec`、`after is None`，并保留 equity
   全覆盖断言）。`candle_calls==1` 不变（`oldest<=start_ts` 判据保证伪 client 一轮即终止）。

3. **组合路径不回包覆盖字段（设计内取舍，已澄清）**：§3.2 正文「组合路径同样追加 `effectiveCandleCount/
   requestedBars/truncated`」与同节 bullet「对比/组合不强制回包覆盖字段，避免响应形状漂移」存在措辞张力。
   落地按后者执行——仅**原子**回测路径（`run_single_backtest` 非 combo 分支）回包三字段；combo 路径
   （`_run_saved_combo`）维持 `{runId, metrics, combo}` 不变。前端 `BacktestResult` 三字段均为可选，
   combo 路径下为 `undefined`、不触发横幅，向后兼容。P1-1 的核心目标（**基础回测**覆盖透明）完整达成。

---

## 评审结论 / Review Verdict（v2）

**结论：有条件通过（Approved with conditions）。**

**评审依据**：本方案的全部核心机制已逐一对照源码核验，**真实可行、与现有架构一致**：
- 取数：`okx/rest_client.get_candles(before=…)` 内部已支持向更早翻页累积到 `limit`（`rest_client.py:130`）；
  service 层全区间分页可行。
- 回测：`engine/backtester.run_backtest(...)` 签名与 `BacktestResult`/`EquityRecord.equity` 形状与方案逐字吻合；
  `engine/metrics.compute_metrics` 返回的 camelCase 键（`totalReturn/winRate/profitFactor/maxDrawdown/sharpe/tradeCount/avgPnl`）
  与方案窗口指标完全对齐。
- 落库免迁移：`StrategyRun.mode` 为 `String(16)`（"walkforward" 11 字符，容得下）、`config_json/metrics_json` 为自由 Text、
  `create_run(mode,config)` 与 `list_runs(mode=…)` 现成可用（`models.py:43`、`repositories.py:129/163`）。
- 错误体：`main.py:65` 已把 `HTTPException(detail={code,message})` 统一包成 `{error:{code,message}}`，方案错误码即插即用。
- 路由：`/strategies/{id}/backtest`、`/runs/{run_id}` 既有形态与新增 `…/walk-forward` 完全同构。
- 前端：`store.ts` version=12（→13 可行）、`api.ts` `/api/py` 基址、`types.ts` `Metrics` 七字段、
  `Chart/ChartToolbar.tsx` 分段控件、`RunHistory.tsx` mode 过滤、`StrategyPanel.tsx:150` 自动回测 effect 均已确认。

**已在 v2 就地修复（无遗留 P0/P1）**：P1-1 覆盖透明化（响应回传 `effectiveCandleCount/requestedBars/truncated` + 前端横幅）、
P1-2 `summary.notes/uncoveredBars` 提示通道、P1-3 `windowBars` 下界抬至 100、P1-4 HTF 前置暖机覆盖硬约束 + 测试、
P1-5 `persist_walk_forward_result` 合并写 + 投影构造契约。

**合入/实施条件（Subtask #2 必须遵守）**：
1. **严格按 v2 修订点落地**，尤其五项 P1 修复——它们是「全区间可信」与「滑动窗口结论正确」的硬保证，不得简化掉。
2. 改造 `load_range_candles`/`load_htf_frames` 后，**既有 `/backtest`、`/compare`、`/combo` 测试必须仍绿**；
   `/backtest` 响应新增字段不得破坏既有断言。
3. `run_walk_forward` 及其子函数须满足 CLAUDE.md 阈值（单函数 ≤50 行、嵌套 ≤4 层）；逐窗口循环走 `_run_wf_windows`。
4. 绝不伪造市场数据：取数失败显式 5xx；窗口/暖机不足时经 `notes` 显式告知，不静默产出误导性零指标。

**非阻断跟进（P2，可在本迭代顺手或排后续）**：P2-1 翻页理由已订正（外层分块为可选有界化，非必需）；
P2-4 每窗口 HTF 重算的预计算优化；P2-5 RunHistory 追加「高级」筛选项。这些不构成合入阻碍。

> 复核签署：高级回测方案 v2 在满足上述 4 项条件的前提下**批准进入开发**。
