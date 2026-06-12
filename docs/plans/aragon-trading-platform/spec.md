# Aragon Trading — 多策略量化模拟交易平台 设计方案 (spec)

- **Feature**: `aragon-trading-platform`
- **文档版本**: v2（已评审修复）
- **作者 / 角色**: Solution Architect (Anthropic Engineering)
- **评审 / 角色**: Senior Reviewer (Anthropic Engineering)
- **创建日期**: 2026-06-12
- **评审日期**: 2026-06-12
- **状态**: Reviewed — 有条件通过（见文末「评审结论」）
- **关联需求**: 见 `task-board.md` 的 "Original Task"

---

## 0. 评审记录（Review Notes）

> 本节由 Subtask #1（方案评审）追加。评审维度：**可行性 / 完整性 / 一致性 / 规模适配（Feasibility / Completeness / Consistency / Right-sizing）**。
> 严重度定义：**P0** = 阻塞，影响核心目标可达性，必须本轮修复；**P1** = 重要缺陷/歧义，本轮必须修复；**P2** = 改进建议/后续项，记录但不阻塞。
> **所有 P0 / P1 已在本 v2 文档正文就地修复**，修复位置见「落点」列。

| # | 维度 | 严重度 | 问题 | 落点（已修复/已记录） |
|---|---|---|---|---|
| C1 | 可行性 / 一致性 | **P0** | 全局目标硬约束是「数据真实可用」。OKX 在本环境可能受地域限制、依赖 Clash 代理（127.0.0.1:7897），但原方案未把「连通性预检」列为实施前置门，也未规定不可达时的**显式行为**——存在静默回退到假数据或首屏空白却无提示的风险，会让核心验收（真实数据）无法保证。 | §7.1 新增「连通性预检与真实数据硬约束」；§15 新增 step 0；§13.2 验收 #2 强化 |
| C2 | 一致性 | **P1** | `ts` 时间戳单位在全文不统一：§7/R7 说归一化为「秒级」，但 §9.2 WS、§9.3 类型、§10 数据表均只写 `ts:number` 未定单位；秒/毫秒混用会导致图表错位、缓存键冲突、跨表 join 错乱。 | §4.4 新增「时间戳统一约定」；§9.3、§10、R7 同步标注 |
| C3 | 可行性 / 完整性 | **P1** | `lightweight-charts` 仅写 "v4"，未锁主版本。v5 对 `series.setMarkers` / 多 series API 有破坏性变更，`npm i` 拉到 v5 会直接编译失败。 | §3 选型表、§8.2 `package.json` 意图：锁定 `^4.2`，并注明 markers API |
| C4 | 完整性 | **P1** | SimBroker 账户模型欠定义：`Signal.strength`（0~1）如何换算成 `qty`？是否建模杠杆？多/空的盯市净值与平仓 PnL 公式？SWAP 合约面值（ctVal）是否忽略？缺这些下游无法实现一致的撮合。 | §6.1 新增「SimBroker 账户与撮合模型（精确定义）」 |
| C5 | 完整性 / 可行性 | **P1** | 策略 5（VWAP+RSI）依赖「当日 VWAP」，但 §6 指标层把 `vwap` 当作一条普通累计列，未规定**按 UTC 自然日重置（session anchor）**；跨多日累计的 VWAP 对日内策略无意义，且 ≥1D 周期下 VWAP 无定义。 | §6 指标层、§5.2 策略 5 补充 VWAP 锚定规则 |
| C6 | 一致性 / 完整性 | **P1** | 策略「注册表（代码）」与「strategies 表（DB）」双源，但未说明二者如何合并：`params_schema` / `description` 只在代码 `StrategyMeta` 里，DB 只存实例参数。`GET /strategies` 必须在响应时用 `template_key` 回查注册表 meta 合并，否则自定义实例拿不到 schema、删除内置模板会孤儿化。 | §5.1、§10 补充「注册表 = schema 唯一真源；API 合并规则」 |
| C7 | 安全 | **P1** | 需求在明文 prompt 中下发了 apikey/secret。R3 只说「不入库不下发」，但未指出这两个凭据**已被明文传输、应视为已泄露**，且本期根本用不到（仅公共行情）。 | §12、R3 补充：视为已泄露须轮换；本期不写入任何文件，仅占位 |
| C8 | 完整性 | P2 | `metrics.sharpe`「按 bar 年化」未给每周期的年化因子（1H→√(24×365) 等），实现易取错。 | §6.3 Metrics 补充年化因子表（已记录并给出） |
| C9 | 可行性 | P2 | `pandas-ta` 与新版 numpy/pandas 有兼容问题（numpy 2.x 移除 `NaN` 等）。方案已 hedge「或自实现」，建议默认自实现这 5 个简单指标，去掉重依赖。 | §3、§6 标注：默认自实现，`pandas-ta` 仅可选 |
| C10 | 可行性 | P2 | OKX candles/history-candles 有限频（约 20~40 req/2s）。无限回溯增量加载若不节流会触发 429。 | §7.2 补充限频与节流策略（已记录） |
| C11 | 规模适配 | P2 | 单机单用户工具下，PaperSession「多会话 + 多浏览器 fan-out」略过度。建议本期约束为「同一时刻 ≤ N 个活跃会话」，fan-out 保留但不追求规模。 | §6.4 标注右尺寸约束（已记录） |
| C12 | 完整性 | P2 | `POST /backtest` 是同步返回 `{runId, metrics}` 还是先返回 `runId` 再轮询，正文略含糊。 | §9.1 标注：小数据集**同步**返回，置于线程池，limit 受限（已记录） |

> P2 项不阻塞本轮，已在正文相应位置以简短说明落实或标注为「二期/可选」，留给 Subtask #2 自行权衡。

---

## 1. Overview（概述）

Aragon Trading 是一个面向加密合约 / 现货的**多策略量化模拟交易平台**。它使用真实的欧易（OKX）行情数据，提供一个"左图右策略"的交互式工作台：用户进入后即可在**左侧**看到所选标的（如 `BTC-USDT-SWAP`）的 K 线图，支持缩放、平移、十字光标、按日期区间拖拽查看；在**右侧**选择某一个交易策略，左侧 K 线图会即时叠加该策略产生的**买卖点标记**与**盈亏 / 净值折线图**，从而直观对比不同策略在同一段真实行情上的表现与收益曲线。

本期（Phase 1）只交付**模拟快速交易（paper / simulation）**能力，不接入真实下单。即：所有行情来自 OKX 公共接口的真实数据，但订单成交由平台内置的**模拟撮合引擎（SimBroker）**完成，计算手续费、滑点、持仓与净值。系统默认内置 **5 个高频 / 日内交易策略**，且策略层采用可插拔注册框架，支持后续对策略进行**增删改查（CRUD）**与持续扩充，而无需改动核心引擎。

技术架构遵循需求：前端为 **React + Next.js（App Router, TypeScript）**，并额外引入 **Python（FastAPI）后端**作为量化核心，负责对接 OKX、运行策略引擎、执行回测与实时模拟、持久化交易与净值数据，并通过 REST + WebSocket 对前端供数。UI 追求 Anthropic 式的优雅克制：暖中性色板、清晰排版、留白充足、动效轻盈，符合人机交互最佳实践。本文档详尽到下游工程师可据此逐行实现，无需再做架构决策。

---

## 2. Goals / Non-Goals（目标与非目标）

### Goals
1. 真实 OKX 行情：通过 OKX V5 公共 REST（历史 K 线）+ 公共 WebSocket（实时 K 线 / ticker）获取真实数据。
2. 左右布局工作台：左侧专业 K 线图（缩放 / 平移 / 日期拖拽 / 十字光标），右侧策略选择与详情。
3. 策略切换联动：右侧切换策略 → 左侧叠加该策略的买卖点 + 净值/盈亏折线。
4. 内置 5 个可盈利导向的高频/日内策略，逻辑清晰、参数可调。
5. 可插拔策略框架，支持策略 CRUD 与参数化扩展。
6. 模拟撮合引擎：手续费 / 滑点 / 持仓 / 净值 / 回撤 / 关键绩效指标（PnL、胜率、夏普、最大回撤）。
7. 两种运行模式：**回测（backtest，历史数据一次性跑完出曲线）** 与 **实时模拟（paper，订阅实时 K 线边走边模拟）**。
8. 代码规范、分层清晰、可扩展、可测试。

### Non-Goals（本期不做）
- 不接入真实下单 / 真实资金交易（私有交易接口仅在架构上预留）。
- 不做多用户账号体系 / 登录鉴权（单机本地工具形态，预留扩展位）。
- 不做撮合层深度订单簿建模（模拟成交以收盘价/下一根开盘价 + 滑点近似）。
- 不做移动端适配（以桌面宽屏为主，但布局响应式不崩）。

---

## 3. 技术栈与选型理由

| 层 | 选型 | 理由 |
|---|---|---|
| 前端框架 | Next.js 14（App Router）+ React 18 + TypeScript | 满足需求指定；App Router 支持 RSC/SSR，BFF 代理简洁 |
| 样式 | Tailwind CSS 3 + CSS 变量主题 | 快速构建一致设计语言，便于实现 Anthropic 暖色调与暗色模式 |
| 图表 | `lightweight-charts@^4.2`（TradingView 开源库，**锁定主版本 4**） | 专为 K 线设计，原生支持缩放/平移/十字光标/多 series 叠加（蜡烛 + 折线 + markers），性能优秀。**注意：v5 对 `series.setMarkers` 与多 series API 有破坏性变更**，本方案 markers / 多轴叠加均基于 v4 API，依赖必须锁 `^4.2`，不得自动升级到 v5（C3） |
| 前端状态 | Zustand（UI/选择态）+ TanStack Query（服务端数据缓存） | 轻量、可组合，避免 Redux 样板 |
| 后端框架 | Python 3.11 + FastAPI + Uvicorn | 异步、类型友好、自动 OpenAPI；量化生态（pandas/numpy/ta）丰富 |
| 行情/网络 | `httpx`（异步 REST）+ `websockets`（OKX WS 订阅） | 异步并发拉取与订阅 |
| 计算 | `pandas` + `numpy` + **自实现指标**（`pandas-ta` 仅可选） | 指标（EMA/RSI/BOLL/ATR/VWAP）逻辑简单，**默认在 `engine/indicators.py` 自实现**以规避 `pandas-ta` 与 numpy 2.x / 新版 pandas 的兼容问题（C9）；`pandas-ta` 列为可选对拍参考 |
| 持久化 | SQLite + SQLAlchemy 2.x（async）+ Alembic（可选） | 零运维、单文件、便于本地工具；schema 可迁移 |
| 数据校验 | Pydantic v2 | 请求/响应模型、策略参数 schema |
| 测试 | 前端 Vitest + React Testing Library；后端 pytest + httpx AsyncClient | 单元 + 接口测试 |
| 包管理 | 前端 pnpm；后端 `uv` 或 `pip + venv`（requirements.txt） | — |

---

## 4. 技术设计（架构 / 关键路径 / 时序）

### 4.1 总体架构

```
┌──────────────────────────────────────────────────────────────────┐
│ Browser (React UI)                                                 │
│  左: CandleChart (lightweight-charts)   右: StrategyPanel          │
│      └ 蜡烛 series + 净值 line + 买卖 markers                       │
│  状态: Zustand(selected inst/bar/strategy/run) + TanStack Query    │
└───────────────▲───────────────────────────────┬───────────────────┘
                │ HTTP(/api/py/*) + WS           │
┌───────────────┴───────────────────────────────▼───────────────────┐
│ Next.js (apps/web)  —  BFF / SSR / 静态资源                        │
│  next.config.mjs rewrites: /api/py/:path*  ->  PY_BACKEND/:path*    │
│  WS: 浏览器直接连 Python WS（NEXT_PUBLIC_PY_WS_URL）                │
└───────────────────────────────────────────────┬───────────────────┘
                                                 │ REST + WS
┌────────────────────────────────────────────────▼──────────────────┐
│ Python Backend (services/quant)  FastAPI                           │
│  ┌ api/ (routers: market, strategies, runs, paper, health)        │
│  ┌ okx/ (rest_client, ws_client, proxy 支持)                       │
│  ┌ strategies/ (base, registry, 5 内置策略)                        │
│  ┌ engine/ (indicators, simulator/SimBroker, backtester, metrics) │
│  ┌ paper/ (live paper session manager)                            │
│  ┌ db/ (models, session, repositories)  -> SQLite                 │
└────────────────────────────────────────────────┬──────────────────┘
                                                 │ HTTPS / WSS (+proxy)
┌────────────────────────────────────────────────▼──────────────────┐
│ OKX V5 API  (真实行情)                                             │
│  REST: /api/v5/market/candles | history-candles | tickers         │
│  WS  : wss://ws.okx.com:8443/ws/v5/public  (channel: candle*)      │
└────────────────────────────────────────────────────────────────────┘
```

**两套后端的职责划分**：Next.js 作为前端 + BFF（服务端渲染、静态资源、统一代理 `/api/py/*` 到 Python 以规避 CORS）；Python FastAPI 作为量化核心（数据、策略、模拟、绩效、持久化）。前端不直接持有 OKX 密钥；密钥仅存于 Python 后端环境变量。

### 4.2 关键代码路径

1. **首屏加载**：`app/page.tsx` (RSC) 调用 `GET /api/py/instruments` 与 `GET /api/py/strategies` 预取列表 → 客户端组件 hydrate → 默认选中 `BTC-USDT-SWAP` + `1H` + 第 1 个策略。
2. **拉取 K 线**：`CandleChart` 通过 TanStack Query 调 `GET /api/py/candles?instId&bar&limit` → Python `okx.rest_client.get_candles()`（带本地缓存 `candles_cache`）→ 归一化为升序数组 → 渲染蜡烛 series。
3. **运行回测并叠加曲线**：用户选策略/改参数 → `POST /api/py/strategies/{id}/backtest` → Python `backtester.run()` 调 `engine.simulator.SimBroker` 跑完历史 → 写 `strategy_runs/trades/equity_points` → 返回 `run_id + metrics` → 前端 `GET /runs/{id}/equity` + `/trades` → 在 K 线图叠加净值 line series（独立右轴）与买卖 markers。
4. **实时模拟（paper）**：`POST /api/py/paper/start` 创建会话 → Python `paper.session` 订阅 OKX WS `candleNm` → 每根确认 K 线驱动 `strategy.on_candle()` → 信号入 `SimBroker` → 通过 `ws /ws/paper/{sid}` 推送 `trade/equity/signal` 事件 → 前端增量更新图表。
5. **缩放/平移/日期**：lightweight-charts 内置 `timeScale`；触发 `subscribeVisibleLogicalRangeChange`，当滚动到左边界时按需 `GET /candles?before=` 增量加载历史（无限回溯）。

### 4.3 实时模拟时序（Sequence）

```
User      Web(Next)     PY:/paper/start   PaperSession   OKX WS      SimBroker   WS:/ws/paper
 │  start ──▶          │                  │              │           │           │
 │           POST ─────▶ create session ──▶ subscribe ───▶           │           │
 │           ◀── {sid} │                  │   candle ◀───│           │           │
 │  connect ───────────────────────────────────────────────────────────────────▶ open
 │                                          on_candle ──▶ signal ───▶ fill ─────▶ push trade/equity
 │  ◀─────────────────────────────────── 实时更新图表 (markers + 净值) ◀───────────
```

### 4.4 时间戳统一约定（Canonical Timestamp — C2）

为消除秒/毫秒混用导致的图表错位、缓存键冲突与跨表 join 错误，**全系统采用单一规范单位**：

- **规范单位 = 秒级 UNIX 时间戳（UTC, integer seconds）**。选择秒的原因：`lightweight-charts` 的 `time` 字段要求秒级 UTC 时间戳，前端可直接使用、无需再换算。
- **唯一转换边界 = OKX 适配层**：`okx/rest_client.py` 与 `okx/ws_client.py` 在归一化时，将 OKX 返回的**毫秒**时间戳 `ms` 一次性转换为 `int(ms // 1000)`，此后系统内部（REST 响应、WS 推送、SQLite 全部 `ts` 列、TS 类型 `ts:number`）一律为**秒**。
- **入参约定**：`/candles` 的 `before/after` 翻页参数，对**前端/后端接口**以秒传递；后端在调用 OKX 时再 ×1000 还原为毫秒（OKX 要求毫秒）。该转换同样只发生在 OKX 适配层。
- 所有涉及 `ts` 的字段（`Candle.ts`、`Trade.ts`、`EquityPoint.ts`、`strategy_runs.start_ts/end_ts`、`candles_cache.ts`、WS 各事件 `ts`）**含义一致 = 秒**。单测 R7 覆盖此归一化。

---

## 5. 策略框架与 5 个默认策略

### 5.1 可插拔框架（核心抽象）

```python
# strategies/base.py
class Signal(BaseModel):
    side: Literal["long", "short", "close"]
    strength: float = 1.0          # 0~1 仓位比例
    reason: str                    # 触发原因（用于 marker tooltip）

class StrategyMeta(BaseModel):
    key: str                       # 唯一标识，如 "long_shadow_reversal"
    name: str
    description: str
    default_params: dict           # 默认参数
    params_schema: dict            # JSON Schema，供前端渲染参数编辑器

class BaseStrategy(ABC):
    meta: StrategyMeta
    def __init__(self, params: dict): ...
    @abstractmethod
    def on_candle(self, df: pd.DataFrame, i: int, ctx: Context) -> list[Signal]:
        """df: 截至第 i 根的全部 K 线（含指标列）。返回本根产生的信号。"""
    def warmup(self) -> int: ...    # 需要的最小历史根数
```

- **注册机制**：`@register_strategy` 装饰器把策略类登记进 `STRATEGY_REGISTRY`（dict[key -> cls]）。新增策略 = 新建一个文件 + 装饰器，无需改引擎，满足"可扩充/增删改查"。
- **参数 CRUD**：策略实例及其参数持久化在 `strategies` 表；前端按 `params_schema` 动态渲染编辑器；`POST/PUT/DELETE /strategies` 实现 CRUD（删除/禁用自定义实例，内置模板只读但可"另存为"）。
- **代码注册表 ↔ DB 表的合并规则（C6，唯一真源约定）**：
  - **`STRATEGY_REGISTRY`（代码）是 `meta` 的唯一真源**：`name`(默认)、`description`、`default_params`、`params_schema`、策略逻辑类，均来自代码。
  - **`strategies` 表（DB）只存"实例化覆盖项"**：`template_key`（指向注册表 key）、用户自定义 `name`、`params_json`（覆盖 `default_params`）、`enabled`、`builtin`。
  - **响应组装**：`GET /strategies(/{id})` 在返回时，以 `template_key` 回查注册表得到 `description` / `params_schema`，与 DB 行的 `params` / `name` / `enabled` / `builtin` **合并**为 §9.3 的 `Strategy` 对象。DB 不冗余存 schema，避免代码与库不一致。
  - **失效保护**：若某 DB 行的 `template_key` 在注册表中已不存在（模板被删/改名），该实例标记为 `disabled` 并在响应中带 `unavailable:true`，不得 500；前端置灰。
  - **删除规则**：`builtin=1` 的内置模板实例**不可删除**（DELETE 返回 409），只能禁用或"另存为"派生新实例；只有 `builtin=0` 的自定义实例可 DELETE。

### 5.2 五个默认策略（高频 / 日内，偏向可盈利）

> 默认标的 `BTC-USDT-SWAP`，默认周期 `1H`（策略 3/4 可切 `5m/15m` 做更高频）。所有策略统一接入风控：单笔止损 `stop_loss_pct`、止盈 `take_profit_pct`、最大持仓时间 `max_hold_bars`、单向持仓。

1. **长影线反转 `long_shadow_reversal`**（用户重点提示）
   - 思路：1H K 线出现**长下影线**（下影 ≥ 实体 × `shadow_ratio` 且发生在一段下跌后）→ 抄底做多；出现**长上影线**（上影 ≥ 实体 × `shadow_ratio` 且发生在一段上涨/暴涨后）→ 做空。
   - 入场条件：`lower_shadow / body >= shadow_ratio (默认2.0)` 且 `close>open`（锤子形）且近 `lookback` 根跌幅 ≥ `move_pct` → long；镜像逻辑 → short。
   - 出场：触及止盈/止损或回到中轨。参数：`shadow_ratio, lookback, move_pct, stop_loss_pct, take_profit_pct`。

2. **暴涨动量突破 `momentum_breakout`**（用户重点提示）
   - 思路：当近 `n` 根**累计涨幅非常大**（≥ `surge_pct`）且突破近 `breakout_window` 根高点、成交量放大（≥ `vol_mult` × 均量）→ 顺势追多；镜像暴跌追空。
   - 出场：跟踪止损（`trail_pct`）或动量衰竭。参数：`surge_pct, breakout_window, vol_mult, trail_pct`。

3. **双 EMA 交叉剥头皮 `ema_cross_scalper`**（高频，建议 5m/15m）
   - 思路：快 EMA(`fast`) 上穿 慢 EMA(`slow`) → 做多；下穿 → 平/反手做空。加 `slope_filter` 过滤震荡。
   - 出场：反向交叉或止盈止损。参数：`fast, slow, slope_filter, stop_loss_pct`。

4. **布林带均值回归 `bollinger_reversion`**（高频，建议 5m/15m）
   - 思路：价格跌破下轨（`close < lower`）且 RSI < `rsi_os` → 做多回归中轨；冲破上轨且 RSI > `rsi_ob` → 做空。
   - 出场：回到中轨（`ma`）或止损。参数：`period, std_mult, rsi_period, rsi_os, rsi_ob`。

5. **VWAP + RSI 动量 `vwap_rsi_momentum`**（日内）
   - 思路：价格站上**当日 VWAP**（按 UTC 自然日重置，见 §6 VWAP 锚定）且 RSI 上穿 50（多头动能确认）→ 做多；跌破 VWAP 且 RSI 下穿 50 → 做空。VWAP 作为日内多空分界。
   - 出场：跌回 VWAP 或止盈止损。参数：`rsi_period, rsi_mid, stop_loss_pct, take_profit_pct`。
   - **VWAP 锚定（C5）**：仅在**日内周期（`bar ∈ {1m,5m,15m,30m,1H}`）**下有效；VWAP 累计量（`Σ price·vol`、`Σ vol`）在每个 **UTC 自然日的第一根 K 线**清零重算。当 `bar ≥ 1D` 时 VWAP 无意义，该策略对此类周期返回空信号并在 UI 提示"请切换到日内周期"。

> 说明：策略默认参数将以"在最近一段真实 K 线上回测为正期望（盈利导向）"为调参目标，但**不承诺真实盈利**（见风险章）。

---

## 6. 模拟撮合 / 回测引擎

- **指标层** `engine/indicators.py`：纯函数，输入 `pd.DataFrame(o,h,l,c,vol)`，输出附加列（`ema_fast`, `rsi`, `boll_upper/ma/lower`, `atr`, `vwap`）。统一在跑策略前批量计算，避免重复。**VWAP 按 UTC 自然日重置（C5）**：实现为 `groupby(floor(ts/86400))` 内做 `cumsum(typical_price·vol)/cumsum(vol)`，`typical_price=(h+l+c)/3`；非日内周期（`bar≥1D`）该列置 `NaN`。其余指标为全历史滚动计算。

### 6.1 SimBroker 账户与撮合模型（精确定义 — C4）

为保证下游一致实现，撮合账户模型精确定义如下：

- **状态**：`cash`（保证金账户现金）、`position{qty, side∈{long,short,flat}, entry_price}`、`equity`（盯市权益）、`fees_paid`、`peak_equity`（算回撤）。
- **持仓与仓位换算**：单向单仓（同一时刻只持有一个方向）。`Signal.strength∈[0,1]` 表示**目标名义敞口占当前权益的比例**：`notional = strength × equity × leverage`，`qty = notional / fill_price`。
  - `leverage` 为账户级参数，**默认 `1`（无杠杆）**；本期不建模逐仓/全仓与强平线，杠杆仅放大敞口与盈亏，风险由策略级 `stop_loss_pct` 兜底。
  - **简化约定**：SWAP 以"线性合约、1 张 = 1 计价单位"近似，**忽略 OKX 合约面值 `ctVal`**（本期为模拟/教学，§14 R5 已声明不承诺真实数值精度）；该简化集中在 SimBroker，未来接真实下单时再引入 `ctVal`。
- **成交价（避免未来函数）**：第 `i` 根产生的信号一律在**第 `i+1` 根开盘价**成交；买入成交价 `= open×(1+slippage_bps/1e4)`，卖出 `= open×(1−slippage_bps/1e4)`。
- **手续费**：每次成交按 `taker_fee_bps`（OKX 永续 taker ≈ 5 bps）对成交名义额计费，开/平**双边各计一次**，累加进 `fees_paid` 并从 `cash` 扣除。
- **盯市权益（每根更新）**：
  - 多头 `equity = cash + qty×(close − entry_price)`；空头 `equity = cash + qty×(entry_price − close)`（`cash` 在开仓时已扣除/记入保证金占用，平仓时结算实现盈亏回 `cash`）。
  - 平仓**实现 PnL**：多 `qty×(exit−entry) − fees`，空 `qty×(entry−exit) − fees`，写入 `Trade.pnl`（开仓行 `pnl=0`）。
- **风控（每根、按"先触发先平"）**：检查 `stop_loss_pct / take_profit_pct / max_hold_bars`，触发即在**当根**以触发价（止损/止盈价，叠加滑点）强平；`max_hold` 到期以下一根开盘平。同根内止损优先于止盈（保守）。
- **产出**：`Trade` 列表 + 每根 `EquityPoint(ts, equity, drawdown)`，`drawdown = equity/peak_equity − 1 ≤ 0`。

### 6.2 Backtester

- **Backtester** `engine/backtester.py`：编排 = 取历史 K 线 → 计算指标 → 逐根 `strategy.on_candle` → SimBroker 撮合 → 落库 → 算 metrics。回测放线程池执行（§9.1、R9）。

### 6.3 Metrics（含年化因子 — C8）

- **Metrics** `engine/metrics.py`：`total_return, win_rate, profit_factor, max_drawdown, sharpe, trade_count, avg_pnl`。
- **Sharpe 年化**：`sharpe = mean(bar_returns)/std(bar_returns) × √(bars_per_year)`，`bars_per_year` 按周期取：`1m→525600, 5m→105120, 15m→35040, 30m→17520, 1H→8760, 4H→2190, 1D→365`（以一年≈365 天计；无风险利率取 0）。`std=0` 时 `sharpe=0`，避免除零。

### 6.4 PaperSession（含右尺寸约束 — C11）

- **PaperSession** `paper/session.py`：复用 SimBroker，但数据源为实时 WS；维护内存态（滚动 `DataFrame` 窗口）+ 周期落库 + WS 推送；**仅在 `confirm=1` 的已确认 K 线上驱动 `on_candle`**（避免未确认 K 线重绘导致重复信号）。
- **右尺寸约束**：本期为单机单用户工具，`manager.py` 限制**同一时刻活跃会话数 ≤ `MAX_PAPER_SESSIONS`（默认 3）**；浏览器 fan-out 保留但不追求规模，超限的 `paper/start` 返回 429。

---

## 7. OKX 集成细节（真实数据）

### 7.1 连通性预检与「真实数据」硬约束（C1 — P0）

全局目标为「**数据真实可用**」。OKX 在部分地区受限且本环境依赖 Clash 代理（127.0.0.1:7897），因此把"真实数据能否流通"作为**实施前置门与显式失败策略**，而非事后补救：

- **实施 step 0（见 §15）= 连通性预检**：在写业务代码前，先用后端经代理实跑一次 `GET /api/v5/market/candles?instId=BTC-USDT-SWAP&bar=1H&limit=2`，确认能取到真实行情。提供脚本 `services/quant/scripts/check_okx.py`（读取 `HTTP_PROXY/HTTPS_PROXY`，打印连通性与样例数据）。预检失败必须先解决网络/代理，再继续，避免在不可用的数据通道上空转开发。
- **严禁静默假数据**：任何代码路径**不得**在 OKX 不可达时静默回退到随机/mock/合成 K 线冒充真实数据（与验收 §13.2 #2「数据非随机/非 mock」直接冲突）。`okx/rest_client` 取数失败必须抛出明确错误，`/api/candles` 返回 `5xx + {error:{code:"OKX_UNREACHABLE",...}}`，前端显示**显式错误态**（"无法连接 OKX，请检查代理/网络"），而非空白或假图。
- **唯一允许的离线物料**：仅供**本地开发/单测**使用、且**带显著 UI 横幅标注"DEV CACHED SAMPLE — 非实时"**的快照数据（来自 `candles_cache` 真实历史落库），绝不在生产/验收路径默认启用，由独立开关 `ALLOW_CACHED_FALLBACK`（默认 `false`）控制。
- `/api/health` 暴露 `okx:"up|down"`，前端启动即探测并在 Header 连接状态点反映真实可达性。

### 7.2 通用集成细节

- REST Base：`https://www.okx.com`；文档：https://www.okx.com/docs-v5/zh/
- **K 线**：`GET /api/v5/market/candles?instId={inst}&bar={bar}&limit={<=300}`；更早历史用 `GET /api/v5/market/history-candles`（`limit<=100`），通过 `after`/`before`（毫秒时间戳）翻页。返回行格式 `[ts, o, h, l, c, vol, volCcy, volCcyQuote, confirm]`，**时间为倒序**，需反转为升序并转 number。
- **行情快照**：`GET /api/v5/market/tickers?instType=SWAP` 取可选标的列表；`GET /api/v5/market/ticker?instId=` 取单标的最新价。
- **WebSocket（K 线）**：`wss://ws.okx.com:8443/ws/v5/business`（**勘误，见 §19 F1**：`candle{bar}` 频道由 OKX 的 **business** 端点提供，`/ws/v5/public` 会返回 "channel ... doesn't exist"）；订阅 `{"op":"subscribe","args":[{"channel":"candle1H","instId":"BTC-USDT-SWAP"}]}`；需每 ~25s 发送 `ping` 维持心跳；断线指数退避重连。`bar` 与 channel 映射：`1m->candle1m, 5m->candle5m, 15m->candle15m, 1H->candle1H, 4H->candle4H, 1D->candle1D`。
- **instType / 标的**：合约用 `SWAP`（如 `BTC-USDT-SWAP`），现货用 `SPOT`（如 `BTC-USDT`）。本期默认提供 BTC/ETH/SOL 的 USDT 永续。
- **网络与地域**：OKX 在部分地区受限，且本环境检测到 Clash 代理（127.0.0.1:7897）。Python 后端必须支持 `HTTP_PROXY/HTTPS_PROXY/ALL_PROXY` 环境变量（httpx/websockets 透传），否则真实数据拉取会失败。
- **鉴权（仅为未来实盘预留，本期不调用）**：私有接口需 `OK-ACCESS-KEY / OK-ACCESS-SIGN / OK-ACCESS-TIMESTAMP / OK-ACCESS-PASSPHRASE`；`SIGN = Base64(HMAC_SHA256(timestamp+method+requestPath+body, secretKey))`。**⚠️ 需求只提供了 apikey + secretkey，缺少 passphrase**，因此任何私有/实盘接口当前不可用；本期仅用公共行情，不受影响（见风险 R1）。OKX 另有 `x-simulated-trading:1` 的官方模拟盘可作未来选项。
- **限频与节流（C10）**：OKX 公共行情按 IP 限频（`candles`/`history-candles` 量级约 20~40 req/2s）。`rest_client` 内置**令牌桶/最小请求间隔**节流 + 429 指数退避；前端"滚动到左边界增量回溯"对增量请求做防抖（合并快速滚动），并优先命中 `candles_cache`，未命中再请求 OKX。

---

## 8. 文件 / 模块变更计划

> 目录采用 monorepo 风格：`apps/web`（Next.js）+ `services/quant`（Python）。以下为**需创建**的文件（本期全新项目，均为新增）。

### 8.1 根 / 文档 / 配置
| 文件 | 意图 |
|---|---|
| `README.md` | 项目说明、启动步骤（前后端）、环境变量 |
| `.gitignore` | 忽略 `node_modules/.next/__pycache__/.venv/*.db/.env` |
| `.env.example` | 列出 `PY_BACKEND_URL, NEXT_PUBLIC_PY_WS_URL, OKX_API_KEY, OKX_SECRET_KEY, OKX_PASSPHRASE, HTTP_PROXY` 等占位 |
| `docs/plans/aragon-trading-platform/spec.md` | 本设计文档（Subtask #0 产出） |

### 8.2 前端 `apps/web`
| 文件 | 意图 |
|---|---|
| `apps/web/package.json` | 依赖：next, react, typescript, tailwindcss, **`lightweight-charts@^4.2`（锁主版本 4，禁升 v5，C3）**, zustand, @tanstack/react-query |
| `apps/web/next.config.mjs` | `rewrites` 把 `/api/py/:path*` 代理到 `PY_BACKEND_URL` |
| `apps/web/tsconfig.json` / `tailwind.config.ts` / `postcss.config.js` | TS 与样式配置 |
| `apps/web/app/layout.tsx` | 根布局、字体、主题、Query Provider |
| `apps/web/app/globals.css` | Anthropic 风格设计 token（CSS 变量）+ Tailwind 基础 |
| `apps/web/app/page.tsx` | 主工作台：左右两栏布局容器，预取 instruments/strategies |
| `apps/web/components/Chart/CandleChart.tsx` | lightweight-charts 封装：蜡烛 + 净值 line + markers + 缩放/平移/增量加载 |
| `apps/web/components/Chart/ChartToolbar.tsx` | 标的选择、周期切换、日期跳转、回测/实时模式切换 |
| `apps/web/components/Strategy/StrategyPanel.tsx` | 右栏容器（列表 + 详情 + 指标 + 交易表） |
| `apps/web/components/Strategy/StrategyList.tsx` | 策略卡片列表，点击切换（联动左图） |
| `apps/web/components/Strategy/StrategyDetail.tsx` | 策略说明 + 参数编辑器（按 params_schema 渲染）+ "运行回测/启动模拟" |
| `apps/web/components/Strategy/MetricsCards.tsx` | 收益/胜率/最大回撤/夏普 指标卡 |
| `apps/web/components/Strategy/TradesTable.tsx` | 最近成交明细表 |
| `apps/web/components/ui/*` | 复用基础组件（Button/Select/Card/Tabs/Skeleton） |
| `apps/web/lib/api.ts` | REST 客户端（封装 `/api/py/*`） |
| `apps/web/lib/ws.ts` | WS 客户端（market + paper），自动重连 |
| `apps/web/lib/types.ts` | 与后端共享的 TS 类型（Candle/Strategy/Run/Trade/Equity/Signal） |
| `apps/web/lib/store.ts` | Zustand：selectedInst/bar/strategyId/runId/mode |
| `apps/web/lib/format.ts` | 数字/时间/百分比格式化 |

### 8.3 Python 后端 `services/quant`
| 文件 | 意图 |
|---|---|
| `services/quant/pyproject.toml` 或 `requirements.txt` | fastapi, uvicorn, httpx, websockets, pandas, numpy, pydantic, sqlalchemy, aiosqlite |
| `services/quant/app/main.py` | FastAPI 实例、CORS、路由挂载、启动/关闭钩子、`/api/health` |
| `services/quant/app/config.py` | Pydantic Settings：读取环境变量（密钥、代理、DB 路径、默认标的） |
| `services/quant/app/okx/rest_client.py` | OKX 异步 REST（candles/history-candles/tickers），归一化 + 重试 + 代理 |
| `services/quant/app/okx/ws_client.py` | OKX 公共 WS 订阅 candle/ticker，心跳 + 重连 |
| `services/quant/app/okx/types.py` | OKX 原始/归一化数据模型 |
| `services/quant/app/strategies/base.py` | `BaseStrategy/Signal/StrategyMeta/Context` 抽象 + 注册表 |
| `services/quant/app/strategies/long_shadow_reversal.py` | 策略 1 |
| `services/quant/app/strategies/momentum_breakout.py` | 策略 2 |
| `services/quant/app/strategies/ema_cross_scalper.py` | 策略 3 |
| `services/quant/app/strategies/bollinger_reversion.py` | 策略 4 |
| `services/quant/app/strategies/vwap_rsi_momentum.py` | 策略 5 |
| `services/quant/app/strategies/__init__.py` | 导入全部策略以触发注册 |
| `services/quant/app/engine/indicators.py` | EMA/RSI/BOLL/ATR/VWAP 指标 |
| `services/quant/app/engine/simulator.py` | SimBroker 模拟撮合（手续费/滑点/风控/持仓/净值） |
| `services/quant/app/engine/backtester.py` | 回测编排 |
| `services/quant/app/engine/metrics.py` | 绩效指标计算 |
| `services/quant/app/paper/session.py` | 实时模拟会话管理（订阅→on_candle→撮合→推送） |
| `services/quant/app/paper/manager.py` | 多会话注册/生命周期 |
| `services/quant/app/db/models.py` | SQLAlchemy ORM：strategies/strategy_runs/trades/equity_points/candles_cache |
| `services/quant/app/db/session.py` | async engine/session、建表、seed 内置 5 策略 |
| `services/quant/app/db/repositories.py` | CRUD 数据访问层 |
| `services/quant/app/api/market.py` | 路由：instruments/candles |
| `services/quant/app/api/strategies.py` | 路由：策略 CRUD + backtest 触发 |
| `services/quant/app/api/runs.py` | 路由：run 详情 / trades / equity |
| `services/quant/app/api/paper.py` | 路由：paper start/stop/sessions + `ws /ws/paper/{sid}` |
| `services/quant/app/api/ws_market.py` | `ws /ws/market` 转发 OKX 实时 K 线 |
| `services/quant/app/schemas.py` | Pydantic 请求/响应模型 |
| `services/quant/scripts/check_okx.py` | **连通性预检脚本（C1）**：经代理实跑一次 OKX candles，打印连通性与样例数据 |
| `services/quant/tests/test_*.py` | 引擎/策略/接口测试 |

---

## 9. 接口设计（REST / WebSocket / 类型）

### 9.1 REST（前端经 `/api/py/*` 代理；以下为后端真实路径）

| Method | Path | 说明 | 关键参数 / Body | 响应 |
|---|---|---|---|---|
| GET | `/api/health` | 健康检查 | — | `{status:"ok", okx:"up\|down"}` |
| GET | `/api/instruments` | 可选标的 | `?instType=SWAP` | `Instrument[]` |
| GET | `/api/candles` | 历史 K 线（真实 OKX，带缓存） | `instId,bar,limit,before?,after?` | `Candle[]`（升序） |
| GET | `/api/strategies` | 策略列表（含 meta + params_schema） | `?enabled` | `Strategy[]` |
| GET | `/api/strategies/{id}` | 策略详情 | — | `Strategy` |
| POST | `/api/strategies` | 新建策略实例（基于模板 key + 自定义参数） | `{templateKey,name,params}` | `Strategy` |
| PUT | `/api/strategies/{id}` | 更新参数/名称/启用 | `{name?,params?,enabled?}` | `Strategy` |
| DELETE | `/api/strategies/{id}` | 删除自定义策略实例 | — | `204` |
| POST | `/api/strategies/{id}/backtest` | 触发回测 | `{instId,bar,limit\|start,end,initialCapital,fees,slippage}` | `{runId, metrics}` |
| GET | `/api/runs/{runId}` | 回测/会话运行详情 | — | `Run`（含 metrics） |
| GET | `/api/runs/{runId}/trades` | 成交明细 | `?limit,offset` | `Trade[]` |
| GET | `/api/runs/{runId}/equity` | 净值/盈亏曲线点 | — | `EquityPoint[]` |
| POST | `/api/paper/start` | 启动实时模拟会话 | `{strategyId,instId,bar,initialCapital}` | `{sessionId, runId}` |
| POST | `/api/paper/{sid}/stop` | 停止会话 | — | `Run`（最终结算） |
| GET | `/api/paper/sessions` | 运行中会话列表 | — | `Session[]` |

错误返回统一：`{ "error": { "code": str, "message": str } }`，HTTP 4xx/5xx。

> 补充约定：
> - **时间单位（C2）**：所有路径中的 `ts` 及 `candles` 的 `before/after` 参数均为**秒级 UTC 时间戳**（后端在调 OKX 时内部 ×1000 还原为毫秒）。
> - **回测同步性（C12）**：`POST /backtest` 对受限规模数据集（`limit` 上限默认 1000 根）**同步**返回 `{runId, metrics}`，计算在 `run_in_threadpool` 中执行以不阻塞事件循环（R9）；明细/曲线随后由 `/runs/{id}/trades`、`/runs/{id}/equity` 拉取。若未来支持超大区间，再演进为"先返回 `runId(status=running)` + 轮询/WS 进度"。

### 9.2 WebSocket

- `GET ws /ws/market?instId=&bar=` → 服务端转发 OKX 实时 K 线：
  `{"type":"candle","data":{ts,o,h,l,c,vol,confirm}}`
- `GET ws /ws/paper/{sessionId}` → 实时模拟事件流：
  - `{"type":"signal","data":{ts,side,reason}}`
  - `{"type":"trade","data":Trade}`
  - `{"type":"equity","data":EquityPoint}`
  - `{"type":"status","data":{state:"running|stopped"}}`

### 9.3 共享 TypeScript 类型（`apps/web/lib/types.ts`，与后端 schema 对齐）

```ts
// 约定：本文件所有 ts 字段均为「秒级 UTC 时间戳」（C2，见 §4.4）。
type Candle = { ts:number; o:number; h:number; l:number; c:number; vol:number }; // ts: seconds (UTC)
type Instrument = { instId:string; instType:"SWAP"|"SPOT"; base:string; quote:string };
type ParamSchema = Record<string, { type:"number"|"int"|"bool"|"enum"; default:any; min?:number; max?:number; step?:number; options?:string[]; label:string }>;
type Strategy = { id:number; templateKey:string; name:string; description:string; params:Record<string,any>; paramsSchema:ParamSchema; enabled:boolean; builtin:boolean };
type Trade = { id:number; runId:number; ts:number; side:"long"|"short"|"close"; price:number; qty:number; fee:number; pnl:number; positionAfter:number; reason:string };
type EquityPoint = { ts:number; equity:number; drawdown:number };
type Metrics = { totalReturn:number; winRate:number; profitFactor:number; maxDrawdown:number; sharpe:number; tradeCount:number; avgPnl:number };
type Run = { id:number; strategyId:number; instId:string; bar:string; mode:"backtest"|"paper"; status:string; startTs:number; endTs?:number; metrics:Metrics };
```

---

## 10. 数据模型（SQLite / SQLAlchemy）

```
strategies
  id INTEGER PK
  template_key TEXT      -- 对应注册表 key
  name TEXT
  description TEXT
  params_json TEXT       -- 当前参数（JSON）
  enabled INTEGER        -- 0/1
  builtin INTEGER        -- 内置模板=1（只读，可"另存为"）
  created_at, updated_at  DATETIME

strategy_runs
  id INTEGER PK
  strategy_id INTEGER FK -> strategies.id
  inst_id TEXT
  bar TEXT
  mode TEXT              -- backtest | paper
  status TEXT            -- running | done | stopped | error
  start_ts, end_ts INTEGER
  config_json TEXT       -- initialCapital/fees/slippage 等
  metrics_json TEXT      -- 结算后写入

trades
  id INTEGER PK
  run_id INTEGER FK -> strategy_runs.id  (index)
  ts INTEGER
  side TEXT              -- long | short | close
  price REAL
  qty REAL
  fee REAL
  pnl REAL              -- 该笔平仓实现盈亏（开仓为0）
  position_after REAL
  reason TEXT

equity_points
  id INTEGER PK
  run_id INTEGER FK -> strategy_runs.id  (index)
  ts INTEGER
  equity REAL
  drawdown REAL         -- 相对峰值回撤（<=0）

candles_cache                      -- 降低 OKX 调用、加速回测
  inst_id TEXT, bar TEXT, ts INTEGER  (复合唯一索引 inst_id+bar+ts)
  o,h,l,c,vol REAL
```

> 时间单位（C2）：上表所有 `ts` / `start_ts` / `end_ts` 列均存**秒级 UTC 整数**，与 §4.4 / §9.3 一致。

内存形态（运行时）：`PaperSession` 持有 `pd.DataFrame` 滚动窗口、`SimBroker` 账户状态、订阅的 WS 连接句柄、下游浏览器 WS 集合（fan-out）。

---

## 11. 前端布局与设计语言（Anthropic 风格）

### 11.1 布局
- 顶部 48px Header：Logo "Aragon"、标的选择、周期切换、模式切换（回测/实时）、连接状态点。
- 主体两栏（CSS grid `grid-cols-[1fr_380px]`，可拖拽分隔条调整；窄屏下右栏折叠为抽屉）：
  - **左栏**：`CandleChart` 占满高度。蜡烛主图 + 右副轴净值折线（绿涨红跌随主题）+ 入场/出场三角 markers（hover 显示 reason/价格/盈亏）。底部 `timeScale` 支持滚轮缩放、拖拽平移、双击复位；左边界滚动触发历史增量加载。
  - **右栏**：`StrategyPanel` = 上方 `StrategyList`（卡片：名称 + 一句话 + 当前收益徽标），中部 `StrategyDetail`（说明 + 参数编辑器 + 运行按钮），下方 `MetricsCards` + `TradesTable`（Tab 切换）。

### 11.2 设计 token（`globals.css` CSS 变量）
- 色板（暖中性 + 克制强调，呼应 Anthropic）：底色 `#F7F5F2`（cream）/暗色 `#1A1915`；文本 `#1F1E1B`；强调 `#CC785C`（Claude clay/赭）；多 `#3B8C6E`（green）空 `#C0573E`（rust）；分隔线 `#E6E1D8`。
- 字体：界面 `ui-sans-serif`（可选 Inter）；数字等宽 `ui-monospace` 保证对齐。
- 风格：16px 基础间距、12px 圆角、1px 细描边、低饱和阴影、150ms 缓动过渡、骨架屏加载、空态/错误态友好文案。支持明/暗主题切换。

---

## 12. 配置与环境变量

| 变量 | 用途 | 默认 |
|---|---|---|
| `PY_BACKEND_URL` | Next.js 代理目标 | `http://localhost:8000` |
| `NEXT_PUBLIC_PY_WS_URL` | 浏览器 WS 地址 | `ws://localhost:8000` |
| `OKX_API_KEY` / `OKX_SECRET_KEY` / `OKX_PASSPHRASE` | 仅 Python 持有（本期不用于私有接口） | 空 |
| `HTTP_PROXY` / `HTTPS_PROXY` / `ALL_PROXY` | 访问 OKX 的代理（本机检测到 7897） | — |
| `OKX_REST_BASE` / `OKX_WS_PUBLIC` | OKX 端点（便于切换） | 见 §7 |
| `DB_PATH` | SQLite 文件路径 | `services/quant/aragon.db` |
| `DEFAULT_INST` / `DEFAULT_BAR` / `INITIAL_CAPITAL` | 默认值 | `BTC-USDT-SWAP / 1H / 10000` |
| `ALLOW_CACHED_FALLBACK` | 是否允许 DEV 离线缓存样例（C1，仅开发） | `false` |
| `MAX_PAPER_SESSIONS` | 同时活跃实时模拟会话上限（C11） | `3` |

> **凭据安全（C7 — P1）**：需求在明文 prompt 中下发了 `apikey/secretkey`，应**视为已泄露**——本期仅用公共行情、**根本不需要**这两个凭据，故**不得**将其写入任何文件（含 `.env.example`、源码、注释、日志、`README`），`.env.example` 仅放空占位符。进入实盘阶段前，必须**先轮换**这套密钥并补齐缺失的 `passphrase`（R1）。`.env` 已由 `.gitignore` 排除。

启动方式（写入 README）：后端 `uvicorn app.main:app --port 8000`；前端 `pnpm dev`（默认 3000）。

---

## 13. 测试与验收标准

### 13.1 单元 / 集成测试
- **后端**：
  - `indicators`：与已知数列对拍验证 EMA/RSI/BOLL/ATR/VWAP 数值。
  - `SimBroker`：构造确定性 K 线，验证手续费/滑点/止损止盈/持仓/净值计算正确（含多空双向、强平）。
  - 每个策略：构造能触发其入场条件的合成 K 线，断言产出预期 Signal（用合成数据，不依赖网络）。
  - `okx.rest_client`：用 mock/录制响应测试归一化与翻页；真实联通性测试标记为 `@pytest.mark.network` 可选执行。
  - API：用 `httpx.AsyncClient` 跑 `/strategies`、`/candles`（mock OKX）、`/backtest`（小数据集）路径。
- **前端**：`CandleChart` 渲染快照、策略切换联动状态、`api.ts` 请求拼装、`format` 工具。

### 13.2 验收标准（Acceptance Criteria）
1. 前后端均可本地启动（后端 8000 / 前端 3000），`/api/health` 返回 ok。
2. **连通性预检（§7.1）先于其余验收通过**：`scripts/check_okx.py` 经代理能取到真实样例行情。进入首页即为左右布局；左侧展示 `BTC-USDT-SWAP 1H` **真实** OKX K 线（数据非随机/非 mock），可缩放、平移、按日期拖拽、滚动加载历史。**当 OKX 不可达时，必须显示显式错误态（"无法连接 OKX…"），严禁以假数据冒充（C1）**。
3. 右侧展示 **5 个**默认策略；点击任一策略 → 触发回测 → 左侧叠加该策略的买卖 markers 与净值/盈亏折线；切换策略时曲线随之切换。
4. 指标卡正确显示该策略的收益率/胜率/最大回撤/夏普；交易明细表列出成交记录。
5. 可修改策略参数并重跑，曲线与指标随参数变化；可"另存为"新策略（CRUD 走通）。
6. 启动一次实时模拟会话，能通过 WS 看到至少一次实时净值/信号更新（依赖行情，允许在测试中以注入 K 线驱动验证）。
7. 代码分层清晰：新增第 6 个策略只需新增一个策略文件 + 注册，无需改引擎/接口（以一个示例 PR 级 diff 自检）。
8. 关键路径单测通过；`README` 启动步骤可复现。

---

## 14. 风险与缓解（Risks & Mitigations）

| # | 风险 | 影响 | 缓解 |
|---|---|---|---|
| R1 | **缺少 OKX passphrase**（需求只给了 key+secret） | 私有/实盘接口不可用 | 本期仅用公共行情，不阻塞；在 README/`.env.example` 标注 passphrase 缺失，实盘阶段补齐；私有客户端代码预留但默认禁用 |
| R2 | **OKX 地域限制 / 网络不通** | 拉不到真实数据 | 后端透传系统代理（已检测到 Clash 7897）；REST 失败重试 + 降级到 `history-candles`；明确错误提示；缓存命中减少依赖 |
| R3 | API 密钥泄露（含 prompt 明文下发，C7） | 安全 | 凭据视为已泄露，进实盘前须轮换；本期不需要故**不写入任何文件**（仅占位）；密钥只存 Python 环境变量，绝不下发前端/不入库/不进日志；`.gitignore` 排除 `.env`（见 §12 C7） |
| R4 | 策略未来函数 / 前视偏差 | 回测虚高、不可信 | 信号一律在**下一根开盘**成交；指标只用截至当前根数据；代码评审 + 单测把关 |
| R5 | "默认 5 策略要盈利"的预期 | 真实行情下策略可能亏损 | 明确定位为**模拟/教学**，不承诺收益；提供参数调优与多策略对比；文案标注风险提示 |
| R6 | lightweight-charts 与 React 生命周期/SSR 冲突 | 图表崩或内存泄漏 | 仅客户端渲染（`"use client"` + dynamic import `ssr:false`），在 `useEffect` 中创建并在卸载时 `remove()` |
| R7 | OKX K 线倒序 / 时间单位（毫秒 vs 秒，C2） | 图表错乱、缓存键冲突 | 在 `rest_client`/`ws_client` 统一归一化（升序、**秒级 UTC ts**、number 类型），全系统单一单位见 §4.4，单测覆盖 |
| R8 | WS 断线 / 限频 | 实时模拟中断 | 心跳 + 指数退避重连；订阅去重与多浏览器 fan-out；限频退避 |
| R9 | 回测大数据量阻塞事件循环 | 接口超时 | 回测放线程池（`run_in_threadpool`）；限制 limit；进度可分页查询 |
| R10 | Windows / 跨平台路径与启动差异 | 起不来 | 全部用相对路径与正斜杠；README 提供 Windows + uvicorn 命令；SQLite 路径用 `pathlib` |

---

## 15. 下游实施顺序（建议给 Subtask #2）

0. **连通性预检（前置门，C1）**：先写 `services/quant/scripts/check_okx.py`，经代理实跑一次 `GET /api/v5/market/candles`，确认能取到真实行情；通过后再进入下列开发。失败则先解决网络/代理，不在不可用数据通道上空转。
1. 后端骨架：`config + db.models/session(建表+seed 5策略) + main + /health`。
2. OKX `rest_client`（candles 归一化 + 缓存）→ `/instruments`、`/candles`。
3. 引擎：`indicators → simulator(SimBroker) → backtester → metrics`，配合单测。
4. 5 个策略 + 注册表 → `/strategies` CRUD + `/backtest` + `/runs/*`。
5. 前端骨架：`layout/page/store/api/types` + Tailwind 主题 token。
6. `CandleChart`（蜡烛 + 缩放/平移 + 增量加载）→ 接真实 candles。
7. `StrategyPanel`（列表/详情/参数编辑/指标/交易表）→ 切换联动叠加净值与 markers。
8. WS：`ws_market` + `paper.session/manager` + 前端 `ws.ts` → 实时模拟。
9. 打磨：设计语言、空/错/载态、README、端到端自测。

## 16. Open Questions（待评审确认）

1. 实时模拟的"快速"程度：是否需要在 1m 周期上做更高频撮合，或支持按 tick（trades 频道）撮合？（默认：按确认 K 线撮合，1m/5m 即可体现"快速"）
2. 默认标的范围是否只需 BTC/ETH/SOL 永续，还是开放任意 OKX 标的搜索？（默认：内置 3 个 + 可手填 instId）
3. 是否需要把 OKX 官方模拟盘（`x-simulated-trading:1`）作为本期"模拟交易"的可选后端？（默认：用内置 SimBroker，OKX 模拟盘列为未来项）
4. 是否需要持久化多次回测以做策略横向对比榜单？（默认：保留 runs 历史，前端先做单策略详情，对比榜单可二期）

---

---

## 17. 评审结论（Review Verdict）

**结论：有条件通过（Approved with Conditions）。**

本方案在四个维度上整体扎实：架构分层清晰（Next.js BFF + FastAPI 量化核心），技术选型与需求匹配且可落地，文件清单详尽到可逐项实现，风险章覆盖到位，规模与 Phase 1 目标匹配（无明显过度工程）。经本轮评审，**§0 评审记录中的全部 P0（C1）与 P1（C2–C7）已在本 v2 文档正文就地修复**，P2（C8–C12）已记录并就地给出落实/标注，无遗留 P0/P1。

**放行条件（Subtask #2 实施时必须满足，否则不视为达成全局目标"数据真实可用"）：**

1. **【硬条件·对应 C1】** 实施 **step 0 连通性预检**必须先行通过：`scripts/check_okx.py` 经代理实测能取到真实 OKX 行情；任何代码路径**严禁以随机/mock 数据冒充真实行情**，OKX 不可达时一律显式报错。
2. **【硬条件·对应 C7】** 严禁将 prompt 中明文下发的 `apikey/secretkey` 写入任何文件（含 `.env.example`/源码/日志）；视为已泄露，实盘前须轮换并补齐 `passphrase`（R1）。
3. **【一致性·对应 C2】** 全系统 `ts` 严格统一为**秒级 UTC**，仅在 OKX 适配层做一次毫秒↔秒转换，并以单测固化（R7）。
4. **【可行性·对应 C3】** 前端依赖锁 `lightweight-charts@^4.2`，不得升 v5。
5. **【完整性·对应 C4/C5/C6】** SimBroker 账户模型（§6.1）、VWAP 日内锚定（§6 / §5.2 策略 5）、注册表↔DB 合并规则（§5.1）须按 v2 定义实现，并由对应单测覆盖（§13.1）。

**建议（非阻塞）：** 默认自实现指标而非依赖 `pandas-ta`（C9）；对 OKX 增量回溯加节流防 429（C10）；本期实时模拟会话数设上限、不追求 fan-out 规模（C11）。

> 满足以上放行条件后，方案即达"可直接据此实现且对齐全局目标"的标准。下游 Subtask #2 若在实施中发现 v2 仍有缺陷，请记录到 `spec.md` 的"实施过程发现的方案缺陷"段，不要静默偏离本方案。

---

> 本文档为 **v2**（已评审修复）。评审记录见 §0，评审结论见 §17。

---

## 18. 实施过程发现的方案缺陷（Issues Found During Implementation）

> 本节由 Subtask #2（代码开发）追加，记录实施中对 v2 方案的澄清与受控偏离（未静默改变设计意图）。

- **D1（接口一致性）**：§4.1 的 rewrite 写作 `/api/py/:path* -> PY_BACKEND/:path*`，而 §9.1 列出的后端真实路径均含 `/api/` 前缀，二者直译会 404。**已采纳**：`next.config.mjs` 将 `/api/py/:path*` 代理到 `${PY_BACKEND_URL}/api/:path*`，前端 REST base = `/api/py`；WS 端点统一挂在 `/api/ws/market`、`/api/ws/paper/{sid}`（浏览器经 `NEXT_PUBLIC_PY_WS_URL` 直连）。
- **D2（策略框架增强）**：§5.1 的 `BaseStrategy` 仅定义 `on_candle/warmup`，但 §6 要求"跑策略前批量计算指标"。为兼顾"算一次"与"参数可调"，新增可选 hook `prepare(df, bar)`（向量化预计算各策略所需指标列）与 `risk_params()`（统一提取 stop/tp/max_hold 给 SimBroker）。为**纯增量扩展**，不改变 §5.1 注册机制与唯一真源约定。
- **D3（图表时间戳唯一性）**：`SimBroker.finalize` 在会话/回测结束平仓时**就地更新**最后一根同 ts 的权益点（结算含平仓费），而非追加，避免 `lightweight-charts` 要求的 time 严格递增被重复时间戳破坏。
- **D4（前端渲染策略）**：`app/page.tsx` 实现为客户端组件（数据经 TanStack Query 预取），`CandleChart` 用 `next/dynamic({ssr:false})` 客户端渲染，落实 R6（避免 lightweight-charts 与 SSR 生命周期冲突）。功能等价于 §4.2 的预取意图。
- **D5（跟踪止损落点）**：SimBroker 仅建模固定 `stop/tp/max_hold`（§6.1），策略 2 的 `trail_pct` 跟踪止损在策略内用运行极值状态实现（`prepare` 中重置），不污染撮合内核。
- **D6（测试/构建基础文件）**：为满足 §13.1 的前端 Vitest 与后端 pytest，补充了 §8 未逐一枚举但属设计内测试/构建基础的文件：`apps/web/{next-env.d.ts, app/providers.tsx, vitest.config.ts, lib/format.test.ts}`、`services/quant/{pyproject.toml(仅 pytest 配置), tests/__init__.py, tests/conftest.py}`。未引入超出方案的产品功能或第三方依赖。

> 以上均为可行性/一致性层面的就地澄清，核心架构、接口语义、数据模型、5 策略逻辑与 5 条放行条件（§17）均按 v2 实现。

---

## 19. 评审修复记录（Review Fixes — Subtask #3）

> 本节由 Subtask #3（代码 Review）追加。逐项对照 spec 与代码评审后，**就地修复**以下问题并以真实 OKX 数据端到端复验。后端 20/20 pytest、前端 tsc + 4/4 vitest、`next build` 均通过。

- **F1（P0·真实数据可用性）** OKX 的 `candle{bar}` 频道在 **business** WS 端点提供，原 `OKX_WS_PUBLIC=/ws/v5/public` 会被拒（"channel ... doesn't exist"），导致实时模拟（paper）与 `/ws/market` 收不到任何真实 K 线。**已改**为 `wss://ws.okx.com:8443/ws/v5/business`（`config.py` 默认值 + `.env.example` + §7.2 勘误）。复验：用真实 `OKXWSClient` 订阅 business 端点成功收到真实 K 线（秒级归一化 ts）。
- **F2（P1·历史回溯方向错误）** `okx/rest_client.get_candles` 把本接口的 `before/after` 入参 1:1 直接当作 OKX 的 `before/after` 游标，但 OKX 语义相反（实测：`after=ts` 返回**更早**记录、`before=ts` 返回**更新**记录），且翻页时同时下发两端游标致左滚回溯停滞。**已改**为：本接口 `before`（"取早于该时刻的更早历史"）映射到 OKX `after` 游标、单方向回溯、加页数上限与游标推进守卫。复验：`before=anchor` 回溯到的 120 根 K 线全部早于锚点；`limit>300` 多页累计 500 根升序去重正确。
- **F3（P1·前端实时净值崩溃风险）** `CandleChart` 净值折线 `setData` 未对 `ts` 去重升序；实时模拟会按 ts 推送净值且收尾就地更新同 ts 点（§18 D3），重复时间会触发 lightweight-charts 断言抛错。**已改**为按 ts 去重（保留最后值）后升序再 `setData`。
- **F4（P1·策略状态跨笔泄漏）** `momentum_breakout` 跟踪止损运行极值（`_peak/_trough`）仅在 `prepare()` 内（每回测一次）重置，平仓后残留、并用 `self._trough or lo` 脆弱兜底，违背 §18 D5「每笔独立」。**已改**为：空仓分支显式清零（`_peak=0.0 / _trough=inf`），持仓分支用纯 `max/min`，去掉 `or` 兜底。复验：真实数据回测 14 笔成交无异常。
- **F5（P2·注册表健壮性）** `register_strategy` 重复 key 静默覆盖。**已改**为重复且非同一类时抛 `ValueError`（同类重复导入幂等放行）。
- **F6（P2·WS 死代码）** `ws_client._run` 读取 `proxy` 却从未传入 `websockets.connect`（且 websockets 14.x 无 `proxy` 形参，原生代理直到 15.0）。**已改**：移除误导性死代码，补充诚实注释（WS 走系统/TUN 代理，REST 侧已在 httpx 显式透传），并加 `open_timeout` 守护。

> 评审结论：spec §17 的 5 条放行条件（C1 真实数据/无假数据、C7 凭据不入库、C2 秒级 ts、C3 锁 charts ^4.2、C4/C5/C6 撮合/VWAP锚定/注册表合并）均已在代码中落实且经真实数据复验；上述 F1–F6 为评审就地修复，未改变 v2 的核心架构与接口语义。
