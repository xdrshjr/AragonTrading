# 体制自适应策略组合（Regime-Adaptive Strategy Combination）开发方案 · spec

> 版本：v2（方案评审与修复）
> 作者：方案设计节点（Subtask #0）；评审与修复：方案评审节点（Subtask #1）
> 评审摘要：逐节核对真实源码后修复 1×P0（预览落 run 与 `strategy_id` 非空外键 / 「不改表」约束的矛盾）、2×P1（同模板成员的体制 gating 失配、`annualizedReturn` 边界发散），并就地校正若干 P2（`_per_bar_primaries` 返回型/暖机口径、默认成员可选优性、`BARS_PER_YEAR` 字典取用）。详见下方「评审记录」与文末「评审结论」。
> 关联需求：自动识别不同时间段的市场趋势类型，自动切换到该时段收益率最高的策略，形成「策略组合」；避免过拟合、避免未来函数；界面提供策略组合页签/开关；可回测多种策略组合的期望收益与收益率等指标。
> 适用代码库：`M:/takoAI/AragonTrading`（pnpm workspace：`apps/web` Next.js + `services/quant` FastAPI）。

---

## 评审记录（Review Notes · v2）

> 评审方法：以「方案声明 vs. 真实源码」逐条对照，覆盖可行性 / 完整性 / 一致性 / 规模四个维度。每条标注严重度（P0 阻断 / P1 需修 / P2 建议），P0+P1 已在本 v2 正文就地修复，P2 视成本就地校正或列入「评审结论」条件。

| # | 严重度 | 维度 | 位置 | 问题 | 处置 |
|---|---|---|---|---|---|
| R1 | **P0** | 可行性×一致性 | §3.2 / §5.1 / §6 / §3.6 | 预览（未保存组合）调用 `repo.create_run(mode="backtest")` 取 `runId`，但 `db/models.py::StrategyRun.strategy_id` 是 **`ForeignKey("strategies.id")` 且非空**（`Mapped[int]`，无 `nullable=True`）；未保存组合没有 `strategies` 行，而 §6 又承诺「无任何表结构变更」。两者不可同时成立 → 按原文实现会在写 run 时抛 `IntegrityError`，**无法落地**。 | **已修**：预览的 `strategy_id` 解析到 **已 seed 的 `regime_rotation` builtin 行**（`_seed_builtin_strategies` 经 `STRATEGY_REGISTRY` 兜底必然种入，且 builtin 不可删，行恒在），真实成员/轮动参数落 `config_json`。不新建临时行、不改表、`runId` 渲染链路不变。详见修订后的 §3.2 / §5.1 / §6。 |
| R2 | **P1** | 可行性（正确性） | §3.3 `ComboMember` / `_eligible_members` | 体制 gating 用成员「展示 `key`」与 `REGIME_STRATEGY_HINTS`（裸 templateKey）求交；但 §3.3 注明 key「同模板多份可加序号」，且 §5.1 去重口径是 `templateKey+params`（**允许同模板不同参数并存**）。带序号后缀的成员永远匹配不到体制候选 → 被静默排除，gating 失真。 | **已修**：`ComboMember` 增 `template_key` 字段，`_eligible_members` 一律按 `member.template_key`（非展示 key）与 `REGIME_STRATEGY_HINTS` 求交。见修订后的 §3.3。 |
| R3 | **P1** | 可靠稳健 | §3.3 步骤 8 | `annualizedReturn = (1+totalReturn)^(BARS_PER_YEAR/有效根数) - 1`：当 `leverage>1` 致权益转负时 `totalReturn ≤ -1`，底数 `≤0` 取分数次幂得 `NaN`/复数；有效根数为 0 时除零。头部指标「期望年化收益」可能渲染 `NaN`，违背「功能可靠稳健」。`BARS_PER_YEAR` 实为按 bar 索引的 **dict**（非标量），原式直接除会 `TypeError`。 | **已修**：加边界守卫——有效根数 ≤0 → 0.0；`1+totalReturn ≤ 0` → -1.0（全损封顶）；其余用 `BARS_PER_YEAR.get(bar, 8760)`。见修订后的 §3.3 步骤 8。 |
| R4 | P2 | 一致性（接口准确性） | §3.3 步骤 2 | `engine.regime._per_bar_primaries` 真实返回 `list[tuple[int, str]]`（升序），非文中所写 `{i: primary}` dict；且其从 `max(ema_slow_p=50, trend_win=30)=50` 根起才产出，前 50 根**无条目**（非自动 `range`）。 | **已修文**：步骤 2 改述为真实返回型 + 前缀根（`i < 50`）由 combo 层显式补 `range`。 |
| R5 | P2 | 完整性 | §3.3 步骤 6 | `warmup_floor` 的「regime min 起算根」易被误读为 `classify_regime.min_bars=60`；逐根标签实际起算于 `max(ema_slow_p, trend_win)=50`，且逐根路径不调用 `classify_regime`。 | **已修文**：步骤 6 明确「逐根体制起算根 = `max(ema_slow_p, trend_win)`」。 |
| R6 | P2 | 右尺寸（需求达成度） | §3.4 默认成员 | 默认 3 成员（`trend_pullback_long` / `range_scalping_strategy` / `trend_pullback_short`）在 `regimeGating=True` 下，每个体制（trend_up/trend_down/range）恰好只有 **1 个** 命中候选 → 头号需求「采用收益率最高的那个策略」中的「选优」从不在竞争者间发生（只按体制切换）。 | **已修**：默认成员补入 `ema_cross_scalper`（trend_up 与 trend_down 均命中），使趋势体制下候选 ≥2，`selectionMetric` 选优真正生效；见修订后的 §3.4。 |
| R7 | P2 | 完整性 | §3.5 `run_compare` / §5.2 `/backtest` | (a) 对比中组合项的「base candles 复用 + 各成员 HTF 并集自取」已述但偏粗；(b) `/strategies/{id}/backtest` 经 `BacktestReq` 入参，**无 `params` 字段**，故该路由对保存组合的 `params_override` 实为恒空（仅用已存参数）。均不阻断。 | 列入「评审结论」条件，开发节点实现时按注释落实即可，无需改方案结构。 |

**未发现的正向确认（feasibility 已逐一查证为真）**：`run_backtest` 每根 `mark` 一次 → `equity_points` 长度=根数且 ts 对齐（净值轮动前提成立）；`compute_metrics` 返回 7 键齐全；`StrategyMeta` 为 Pydantic `BaseModel`，加 `kind: str = "atomic"` 向后兼容；`ParamEditor` 支持 `bool/enum/int/number` 四型（组合参数模式全覆盖）；API 路由返回裸 `dict`（无严格 `response_model`，追加 `combo` 字段不被剥离）；`BacktestReq.limit` 确为 `ge=10 le=1000`；store 现版 `version:10`（→11）；`Mode` 现为 `"backtest"|"compare"|"paper"`；`BASE="/api/py"`。包级循环依赖经「函数内局部 import」规避的策略成立（`combination.py` 仅在模块层依赖 `.base`）。

---

## 1. 概述（Overview）

本方案为 Aragon Trading 平台新增「**策略组合**（策略轮动 / regime rotation）」能力。一个策略组合由 2–8 个已有原子策略（`long_shadow_reversal`、`ema_cross_scalper`、`range_scalping_strategy` 等）构成。系统在回测窗口内**因果地**逐根识别市场体制（趋势上行 / 趋势下行 / 区间震荡，并叠加波动 / 事件标签），并在每个再平衡边界，从「与当前体制匹配的候选成员」中挑出近端**收益率最高**（默认按风险调整后收益）的成员，作为下一段时间的执行策略，从而实现「不同时段自动采用最赚钱的那个策略并自动切换」。

平台已具备实现该能力的全部底层件：`engine/regime.py` 提供严格因果的体制识别（`classify_regime` / 逐根结构标签 `_per_bar_primaries` / `REGIME_STRATEGY_HINTS` 体制→候选策略映射）；`engine/backtester.py::run_backtest` + `engine/simulator.py::SimBroker` 提供单策略的逐根撮合与盯市净值；`engine/metrics.py::compute_metrics` 从净值/成交计算收益率、夏普、胜率、回撤等。本方案**不重写**这些底层件，而是新增一个编排层 `engine/combo.py`，以「**成员净值轮动**」模型把多个成员策略的真实回测净值，按因果选优规则拼接成组合净值，再复用 `compute_metrics` 得到组合的期望收益与各项指标。组合本身作为一种特殊的「策略模板」(`template_key="regime_rotation"`) 注册进 `STRATEGY_REGISTRY`，因此天然复用既有的策略 CRUD、回测、对比、运行历史链路——**无需任何数据库表结构变更**，成员清单与轮动参数以 JSON 形式存于既有 `strategies.params_json`。

前端在顶部模式段（现有「回测 / 对比 / 实时」）新增「**组合**」模式，右侧 `StrategyPanel` 据此渲染新的 `ComboView`：选择成员、配置轮动策略、一键回测组合并展示组合净值、收益率、夏普、回撤、**轮动时间线**（哪段时间用了哪个策略 / 对应何种体制）与**分体制贡献表**；可「另存为组合」，保存后的组合在「对比」模式中与原子策略/其它组合横向对比期望收益。设计严格贯彻平台两条硬规则：**不假数据**（数据不足显式报错）与**因果无未来函数**（所有体制识别与选优只用截至「上一根已收盘 K 线」的数据，选中的成员决定**下一根**的收益，留 1 根决策滞后）。

---

## 2. 术语与硬约束（Definitions & Invariants）

- **成员（member）**：组合中的一个原子策略，由 `{templateKey, params}` 描述（自包含快照，不引用其它策略行的主键，避免悬挂引用）。
- **再平衡（rebalance）**：每隔 `rebalanceBars` 根，重新决策当前应执行哪个成员。
- **体制（regime）**：`engine/regime.py` 的 `primary`（trend_up / trend_down / range）+ 可选 volatility / event 标签。
- **滞后 1 根（1-bar decision lag）**：在第 `i` 根做的决策只允许使用「第 `i-1` 根及之前」的信息，决策出的活跃成员作用于「第 `i` 根」的组合收益。这是杜绝未来函数的核心纪律。
- **硬约束（沿用平台既有约定）**：
  - **C1 不假数据**：成员/体制所需根数不足时抛 `ValueError`/返回 HTTP 400，绝不用合成或填充数据。
  - **R4 无未来函数**：成员回测沿用 SimBroker「信号次根开盘成交」；组合层的体制识别与选优全部因果。
  - **C6 注册表唯一真源**：组合作为一个 `template_key` 注册；成员/参数 meta 经注册表回查。
  - **C8 时间口径**：全系统秒级 UTC。

---

## 3. 技术设计（Technical Design）

### 3.1 执行模型决策：净值轮动（Net-Value Rotation）

组合回测有两种候选实现：

1. **委派式元策略（delegation）**：组合作为 `BaseStrategy`，其 `on_candle` 把信号委派给当前活跃成员，所有成员共享同一个 `SimBroker`。问题：`run_backtest` 只在循环前调用一次 `strategy.risk_params()`，无法逐根切换各成员各自的止损/止盈/最大持仓；切换时还需处理仓位交接（一个成员无法理解另一个成员建立的仓位）。需要改动核心 `backtester`，回归面大。
2. **成员净值轮动（net-value rotation）✅ 采纳**：每个成员先**独立**完整回测一次（各自风控、真实成本，行为与单策略页完全一致），得到逐根盯市净值序列 `E_s[i]`；组合在每个再平衡点因果选出活跃成员，组合净值按「活跃成员当根收益率」复利累积，切换时扣一笔换手成本 `turnoverBps`。

**采纳净值轮动的理由**：(a) 逐字节复用经过测试的 `run_backtest`，零核心引擎改动，回归面最小；(b) 因果性显而易见、易单测；(c) 各成员保留自身风控，无仓位交接与单一风控参数的限制；(d) 直接产出组合净值 → `compute_metrics` 即得期望收益/收益率/夏普/回撤，正好对齐需求「回测多种组合的期望收益与收益率」。其近似代价（切入成员时"继承"其既有仓位）由换手成本近似抵消，并在 §8 风险中显式记录，委派式留作 v2 增强。

### 3.2 后端架构与关键代码路径

```
POST /strategies/combo/preview  (api/strategies.py, 薄包装)
  └─ services.run_combo_preview(session, rest, body)            # agent/services.py 新增
       ├─ load_range_candles(rest, ...)                         # 复用：取真实区间 K 线（空区间→400）
       ├─ load_htf_frames(rest, ...) ×成员并集                  # 复用：多周期成员的高周期帧
       ├─ sid = _combo_template_strategy_id(session)            # R1 修复：解析已 seed 的 regime_rotation builtin 行 id（不新建行、不改表）
       ├─ repo.create_run(strategy_id=sid, mode="backtest", config={... "combo":True,"preview":True,"members":[...],"policy":{...}})
       ├─ run_in_threadpool(combo.run_combo_backtest, ...)      # engine/combo.py 新增（纯计算）
       │     ├─ 为每个成员 run_backtest(...) → 对齐逐根净值 E_s[] + 成员成交        # 复用 run_backtest
       │     ├─ per-bar 因果体制标签（engine.regime._per_bar_primaries）            # 复用
       │     ├─ 再平衡选优（regime gating + 近端收益排序 + 滞后/迟滞/最短持有）
       │     └─ 拼接组合净值 EquityRecord[] + 组合成交 TradeRecord[] + 轮动时间线
       ├─ repo.persist_backtest_result(...)                     # 复用：落 trades/equity/metrics
       └─ return {runId, metrics, combo:{timeline, breakdown, annualizedReturn}}

POST /strategies/{id}/backtest  →  若该策略是组合，services.run_single_backtest 内部转 combo 路径
POST /strategies/compare        →  items 中遇到组合 id，逐项走 combo 路径（与原子策略混排对比）
```

### 3.3 `engine/combo.py`（新增 · 纯计算 · 无 IO）

核心数据结构与函数签名（实现时严格照此）：

```python
@dataclass
class ComboPolicy:
    rebalance_bars: int = 24        # 再平衡间隔（根）
    lookback_bars: int = 168        # 近端表现回看窗口（根）
    selection_metric: str = "sharpe"  # "return" | "sharpe" | "calmar"
    min_dwell_bars: int = 24        # 选中成员最短持有（根），抑制频繁切换
    switch_margin: float = 0.10     # 迟滞：挑战者需在该相对优势(=10%)以上才替换在任者
    regime_gating: bool = True      # 仅在「与当前体制匹配的候选成员」内选优
    turnover_bps: float = 5.0       # 每次切换的换手成本（基点，按组合权益计）

@dataclass
class ComboMember:
    key: str                # 成员展示用 key（同模板多份可加序号，仅用于展示/配色/时间线）
    template_key: str       # 注册表模板 key（R2：体制 gating 一律用此字段，非展示 key）
    name: str
    strategy: BaseStrategy  # 已实例化的成员（由 services 层构建注入）
    timeframes: list[str]   # 该成员声明的高周期（用于取数并集）

@dataclass
class ComboSegment:        # 轮动时间线一段
    start_ts: int
    end_ts: int
    member_key: str
    member_name: str
    regime: str            # 决策时刻（i-1）的 primary 体制
    bars: int
    contribution: float    # 该段组合净值变动（结束/开始 - 1）

@dataclass
class ComboBacktestResult:
    trades: list[TradeRecord]
    equity_points: list[EquityRecord]
    metrics: dict
    start_ts: int
    end_ts: int
    segments: list[ComboSegment]
    breakdown: list[dict]   # 分体制聚合：{regime, memberKey, memberName, bars, contribution}

def run_combo_backtest(
    members: list[ComboMember],
    candles: list,
    bar: str,
    *,
    initial_capital: float,
    taker_fee_bps: float,
    slippage_bps: float,
    leverage: float,
    htf_frames_by_member: dict[str, dict[str, list]],   # memberKey -> {htf_bar: candles}
    policy: ComboPolicy,
    regime_params: RegimeParams | None = None,
) -> ComboBacktestResult: ...
```

关键内部实现（分函数，单函数 ≤50 行、嵌套 ≤4，遵守 §10 阈值）：

1. `_build_member_curves(...)`：对每个成员调用 `run_backtest(member.strategy, candles, bar, ..., htf_frames=...)`，把返回的 `equity_points`（每根一个，ts 与基础 df 对齐）转成与基础 `ts` 索引对齐的 `list[float]` 净值数组（缺口前向填充上一净值）。同时保留各成员成交 `list[TradeRecord]`。**仅在此处使用 run_backtest，函数内局部 import 以规避包级循环依赖**（见 §8 风险）。
2. `_per_bar_regime(df, regime_params)`：复用 `engine.regime._per_bar_primaries`（**R4：真实签名返回 `list[tuple[int, str]]`，升序，且从第 `max(ema_slow_p, trend_win)=50` 根才开始产出**），转为按根索引的 `dict[int,str]`；起算根之前（`i < 50`，函数不产条目）由本层显式补 `range`（中性）。逐根结构标签廉价；不在每根调用昂贵的 `classify_regime`。
3. `_eligible_members(regime, members, gating)`：`gating=False` → 全部成员可选；`True` → 取 `member.template_key ∈ REGIME_STRATEGY_HINTS[regime]` 的成员子集（**R2：按 `template_key` 而非展示 `key`，杜绝同模板多份带序号后缀时的失配静默排除**）；若交集为空 → 回退为全部成员（不空转，记一条 note）。
4. `_score(curve, i, lookback, metric)`：用 `curve[max(0,i-lookback)..i]`（**只读 ≤ i 的净值**）计算近端表现：`return`=区间净值比-1；`sharpe`=逐根收益均值/标准差；`calmar`=区间收益/区间最大回撤。返回可比浮点。
5. `_select(prev_active, scores, policy, bars_held)`：在任者未满 `min_dwell_bars` → 保持；否则取分数最高者为挑战者，仅当 `score_challenger > score_incumbent * (1 + switch_margin)`（或在任者已不在 eligible 集合内）才切换。
6. `_combine(...)`：自第一个可决策根（`i ≥ warmup_floor`，`warmup_floor = max(各成员 warmup, lookback 起算根, 逐根体制起算根)`，其中**逐根体制起算根 = `max(ema_slow_p, trend_win)`（=50，R5；逐根路径不调用 `classify_regime`，故非 `min_bars=60`）**）起逐根：
   - 决策用 `regime[i-1]` 与 `scores@(i-1)`（滞后 1 根）；得活跃成员 `a`。
   - 组合当根收益 `r = E_a[i]/E_a[i-1] - 1`；`equity[i] = equity[i-1] * (1 + r)`。
   - 若发生切换：`equity[i] *= (1 - turnover_bps/1e4)`，并记一条 `switch` 备注与新 `ComboSegment`。
   - 生成 `EquityRecord(ts, equity, drawdown=equity/peak-1)`。
7. `_combo_trades(...)`：组合成交 = 各成员在「其活跃区间 ts ∈ [seg.start_ts, seg.end_ts]」内的成交记录（`reason` 前缀加成员 key，如 `ema_cross_scalper:signal_open`），用于 `compute_metrics` 计算胜率/盈亏比/平均盈亏（**近似**：成交盈亏取自成员独立回测，组合净值由净值轮动得出，二者口径在 §8 记录）。
8. 末根 `compute_metrics(equity_points, trades, bar, initial_capital)` → `metrics`；并计算 `annualizedReturn`，**带边界守卫（R3）**：
   ```python
   bars_per_year = BARS_PER_YEAR.get(bar, 8760)   # 注意：BARS_PER_YEAR 是按 bar 索引的 dict，非标量
   base = 1.0 + metrics["totalReturn"]
   if effective_bars <= 0:
       annualized = 0.0
   elif base <= 0.0:                              # 杠杆致权益转负 → totalReturn ≤ -1，封顶全损
       annualized = -1.0
   else:
       annualized = base ** (bars_per_year / effective_bars) - 1.0
   ```
   杜绝 `NaN`/复数（负底数分数次幂）与除零，使头部「期望年化收益」恒为有限实数。

### 3.4 `strategies/combination.py`（新增 · 注册组合模板）

```python
@register_strategy
class CombinationStrategy(BaseStrategy):
    meta = StrategyMeta(
        key="regime_rotation",
        name="趋势自适应策略组合",
        description="按时间段自动识别市场体制并轮动到近端表现最优的成员策略；因果无未来函数，含迟滞/最短持有抑制过拟合。",
        kind="combo",                       # 新增字段，区分原子/组合（默认 atomic）
        default_params={
            "members": [                    # 默认成员=已存在的内置 key（保证 seed 可跑）
                {"templateKey": "trend_pullback_long", "params": {}},
                {"templateKey": "ema_cross_scalper", "params": {}},     # R6：trend_up/trend_down 双命中，使趋势体制下候选 ≥2，selectionMetric 选优真正生效
                {"templateKey": "range_scalping_strategy", "params": {}},
                {"templateKey": "trend_pullback_short", "params": {}},
            ],
            "rebalanceBars": 24, "lookbackBars": 168, "selectionMetric": "sharpe",
            "minDwellBars": 24, "switchMarginPct": 0.10, "regimeGating": True,
            "turnoverBps": 5.0,
        },
        params_schema={                     # 仅轮动参数走通用 ParamEditor；members 由 ComboView 专属编辑
            "rebalanceBars": {"type": "int", "default": 24, "min": 4, "max": 240, "step": 1, "label": "再平衡间隔(根)"},
            "lookbackBars": {"type": "int", "default": 168, "min": 24, "max": 1000, "step": 1, "label": "表现回看(根)"},
            "selectionMetric": {"type": "enum", "default": "sharpe", "options": ["return", "sharpe", "calmar"], "label": "选优指标"},
            "minDwellBars": {"type": "int", "default": 24, "min": 1, "max": 240, "step": 1, "label": "最短持有(根)"},
            "switchMarginPct": {"type": "number", "default": 0.10, "min": 0.0, "max": 1.0, "step": 0.01, "label": "切换迟滞%"},
            "regimeGating": {"type": "bool", "default": True, "label": "按体制限定候选"},
            "turnoverBps": {"type": "number", "default": 5.0, "min": 0.0, "max": 50.0, "step": 0.5, "label": "换手成本(bps)"},
        },
    )

    def on_candle(self, df, i, ctx):
        raise RuntimeError("CombinationStrategy 必须经 engine.combo.run_combo_backtest 执行，不可直接进 run_backtest")
```

- 在 `strategies/base.py::StrategyMeta` 增加字段 `kind: str = "atomic"`（向后兼容，原子策略不需改动）。
- 在 `strategies/__init__.py` 增 `from . import combination` 并把 `regime_rotation` 追加到 `BUILTIN_ORDER` 末尾（展示顺序：组合排在原子策略之后）。
- `on_candle` 显式抛错（C1 失败要响，不静默返回空），确保任何误入 `run_backtest` 的路径立即暴露。

### 3.5 `agent/services.py`（新增组合编排，复用既有取数/落库）

新增/改动函数（均薄封装现有 helper）：

- `_is_combo(template_key) -> bool`：查 `STRATEGY_REGISTRY[template_key].meta.kind == "combo"`。
- `_combo_template_strategy_id(session) -> int`（**R1 修复**）：查 `strategies` 表中 `template_key="regime_rotation" AND builtin=True` 的行 id（`init_db → _seed_builtin_strategies` 必然种入，builtin 不可删，行恒在）。预览（未保存组合）的 run 以此 id 落表，真实成员/参数存 `config_json`，从而既给出可渲染的 `runId`，又不新建临时行、不改表结构。若意外查不到（异常态）→ `HTTPException(500, {"code":"COMBO_TEMPLATE_MISSING"})`，绝不静默。
- `_build_members(members_spec, params_override) -> list[ComboMember]`：逐 `{templateKey, params}` 经 `get_strategy_class` 实例化（模板失效则跳过并记 note）；可用成员 < 2 → `HTTPException(400, {"code":"COMBO_MEMBERS","message":"组合至少需要 2 个可用成员"})`。每个成员 `template_key` 取自 spec 的 `templateKey`（R2，供 gating）；展示 `key` 在同模板多份时追加 `#序号` 去歧义（仅展示/配色用）。
- `_load_combo_data(rest, members, inst_id, bar, limit, start_ts, end_ts)`：`load_range_candles` 取基础 K 线 + `upsert_candles`；对成员 `timeframes()` 并集 `load_htf_frames`，按成员归集 `htf_frames_by_member`。
- `run_combo_preview(session, rest, *, members_spec, policy_spec, inst_id, bar, limit, start_ts, end_ts, costs...) -> dict`：取数 → `repo.create_run(mode="backtest", config={... "combo": True, "members":[...], "policy":{...}})` → `run_in_threadpool(run_combo_backtest, ...)` → `repo.persist_backtest_result` → 返回 `{runId, metrics, combo:{timeline, breakdown, annualizedReturn}}`。
- `run_single_backtest`：开头 `if _is_combo(row.template_key): return await _run_saved_combo(...)`，把保存的组合（members/policy 取自 `params_json`，叠加 `params_override`）走 combo 路径；非组合保持现状（R8 等价）。
- `run_compare`：在 `_prepare_compare` 阶段对组合 id 标记；主循环遇组合走 combo 路径（复用同一批 base candles，但 combo 内部需各成员独立回测，故组合项单独取自身成员 htf 并集），其余原子项不变。组合与原子结果统一 append 进 `items`，对比表可混排。

> 注：`agent/services.py` 现 ~210 行，新增编排后预计 ~330 行，仍 < 800 行阈值；若逼近阈值，把 combo 相关函数下沉到 `agent/combo_service.py`（实现时按行数决定，不预先拆）。

### 3.6 前端数据流

- 顶部模式段新增「组合」→ `setMode("combo")`：store 把 `chartView` 置 `"run-range"`（组合回测后用 `runId` 在左图画**单条组合净值**与成交 markers，复用既有 run-range 渲染，无需新图层）。
- `StrategyPanel` 增分支 `mode === "combo" ? <ComboView/> :`（与 `mode === "compare"` 平级）。
- `ComboView`：
  1. **成员选择**：列出 `strategies.filter(s => s.kind !== "combo" && !s.unavailable && s.enabled)`，多选 2–8 个；每个成员旁显示「体制亲和」徽标（前端 `regimeAffinity.ts` 镜像 `REGIME_STRATEGY_HINTS` 给出「擅长：趋势上行/区间…」）。
  2. **轮动参数**：复用 `StrategyDetail` 同款 `ParamEditor`（基于 `regime_rotation` 的 `params_schema`）编辑 rebalance/lookback/metric/dwell/margin/gating/turnover；每项带 tooltip 说明其抗过拟合/抗未来函数含义。
  3. **运行组合回测**：`api.comboPreview({members, policy, instId, bar, limit:1000, startTs, endTs, initialCapital:simBalance})` → `setRunId(res.runId)`、`setMetrics(res.metrics)`、保存 `res.combo`（timeline/breakdown/annualizedReturn）。
  4. **结果区**：`MetricsCards`（复用，展示 收益率/夏普/胜率/回撤/平均盈亏）+ 「**期望年化收益**」高亮卡 + 「**轮动时间线**」色带（按成员配色分段，悬浮显示体制/区间/贡献）+ 「**分体制贡献表**」（体制 → 成员 → 根数 → 贡献）。
  5. **另存为组合**：`api.createStrategy({templateKey:"regime_rotation", name, params:{members, ...policy}})`，保存后该组合出现在「对比」列表（带「组合」徽标）。
- **多组合对比**：在「对比」模式（`CompareView`）中，列表现在包含已保存组合（`kind==="combo"` 显示「组合」徽标），勾选若干组合（可与原子策略混选）→「运行对比」→ 既有榜单按收益率/夏普等排序，即满足「回测多种策略组合的期望收益对比」。`ComboView` 底部提供「在对比中查看已保存组合 →」快捷入口（`setMode("compare")`）。

---

## 4. 文件 / 模块变更计划（File / Module Change Plan）

| 文件 | 新增/修改 | 一句话意图 |
|---|---|---|
| `services/quant/app/engine/combo.py` | 新增 | 净值轮动组合回测核心（纯计算）：成员净值对齐、因果体制选优、组合净值/成交/时间线拼接，返回 `ComboBacktestResult`。 |
| `services/quant/app/strategies/combination.py` | 新增 | 注册 `regime_rotation` 组合模板（`kind="combo"`，默认成员+轮动参数+params_schema），`on_candle` 显式抛错。 |
| `services/quant/app/strategies/base.py` | 修改 | `StrategyMeta` 增 `kind: str = "atomic"` 字段（向后兼容）。 |
| `services/quant/app/strategies/__init__.py` | 修改 | import `combination`；`regime_rotation` 追加进 `BUILTIN_ORDER`。 |
| `services/quant/app/engine/regime.py` | 修改（极小） | 暴露/复用逐根结构标签的纯函数（`_per_bar_primaries` 已存在；如需对外稳定接口，新增薄包装 `per_bar_primaries`）。 |
| `services/quant/app/agent/services.py` | 修改 | 增 `_is_combo`/`_build_members`/`_load_combo_data`/`run_combo_preview`/`_run_saved_combo`；`run_single_backtest`、`run_compare` 增组合分支（原子路径 R8 等价）。 |
| `services/quant/app/api/strategies.py` | 修改 | 新增 `POST /strategies/combo/preview` 薄路由；`backtest`/`compare` 因 services 内部分发自动支持组合。 |
| `services/quant/app/schemas.py` | 修改 | 增 `ComboMemberIn`/`ComboPolicyIn`/`ComboPreviewReq`/`combo_out` 序列化器；`merge_strategy` 输出补 `kind`（见下行）。 |
| `services/quant/app/db/repositories.py` | 修改 | `merge_strategy` 返回 dict 增 `"kind": meta.kind`（失效模板补 `"atomic"`）。 |
| `services/quant/tests/test_combo_engine.py` | 新增 | 组合引擎单测：因果性、单成员等价、体制驱动切换、迟滞/最短持有、指标形状。 |
| `services/quant/tests/test_combo_api.py` | 新增 | `combo/preview` 与 compare 混排的 API 契约测试（monkeypatch rest，沿用既有测试模式）。 |
| `services/quant/tests/test_strategies_registry.py` | 修改/新增断言 | 断言 `regime_rotation` 已注册、`kind="combo"`、被 seed 为 builtin 且可跑。 |
| `apps/web/lib/types.ts` | 修改 | `Mode` 增 `"combo"`；`Strategy` 增 `kind?`；新增 `ComboMemberSpec`/`ComboPolicy`/`ComboTimelineSeg`/`ComboBreakdownRow`/`ComboPreviewResult`。 |
| `apps/web/lib/api.ts` | 修改 | 增 `comboPreview(body)`；类型对齐。 |
| `apps/web/lib/store.ts` | 修改 | `setMode("combo")` 联动 `chartView`；持久化 `comboDraft`(members+policy)（version 11 + migrate + partialize）。 |
| `apps/web/lib/regimeAffinity.ts` | 新增 | 前端镜像 `REGIME_STRATEGY_HINTS`，把 `templateKey → 擅长体制中文标签` 供成员徽标（纯函数 + 单测）。 |
| `apps/web/lib/combo.ts` | 新增 | 组合纯助手：草稿校验（成员数 2–8、参数范围）、时间线配色、分体制贡献汇总格式化（纯函数 + 单测）。 |
| `apps/web/components/Strategy/ComboView.tsx` | 新增 | 组合构建/回测/结果主界面（成员选择、参数、运行、时间线、分体制贡献、另存为组合）。 |
| `apps/web/components/Strategy/ComboTimeline.tsx` | 新增 | 轮动时间线色带组件（按成员配色分段，悬浮 tooltip 显示体制/贡献）。 |
| `apps/web/components/Strategy/StrategyPanel.tsx` | 修改 | 增 `mode === "combo" ? <ComboView/> :` 分支。 |
| `apps/web/components/Strategy/CompareView.tsx` | 修改（极小） | 列表项对 `kind==="combo"` 显示「组合」徽标，允许与原子策略混选对比。 |
| `apps/web/components/Chart/ChartToolbar.tsx` | 修改 | `MODES` 增 `{ key:"combo", label:"组合" }`。 |
| `apps/web/app/page.tsx` | 修改（极小） | `mode==="combo"` 的 `rangeAvailable`/empty-hint 文案与 backtest 同档（单 run 净值）。 |
| `apps/web/components/Chart/ChartWorkspace.tsx` | 修改（极小） | `mode==="combo"` 空态提示文案。 |
| `apps/web/lib/combo.test.ts` / `regimeAffinity.test.ts` | 新增 | 前端纯助手单测。 |
| `apps/web/components/Strategy/ComboView.test.tsx` | 新增（可选） | 组件冒烟测（NODE_ENV=test，见记忆：组件测须设 `NODE_ENV=test`）。 |

> 不变更：`db/models.py`（无新表/新列——组合复用 `Strategy/StrategyRun/Trade/EquityPoint`，成员与参数存 `params_json`）；`engine/backtester.py`/`simulator.py`/`metrics.py`（仅被复用）；`paper/*`（v1 不支持组合实盘/模拟，见 §8）。

---

## 5. 接口设计（Interface Design）

### 5.1 REST：`POST /api/py/strategies/combo/preview`

即时（不强制保存）回测一个组合；落一条 `StrategyRun`（mode=backtest）以便用 `runId` 拉净值/成交。

请求体（`ComboPreviewReq`）：
```jsonc
{
  "members": [                       // 2..8，去重(templateKey+params)
    {"templateKey": "trend_pullback_long", "params": {}},
    {"templateKey": "range_scalping_strategy", "params": {}},
    {"templateKey": "trend_pullback_short", "params": {}}
  ],
  "policy": {                        // 可缺省（用模板默认）
    "rebalanceBars": 24, "lookbackBars": 168, "selectionMetric": "sharpe",
    "minDwellBars": 24, "switchMarginPct": 0.10, "regimeGating": true, "turnoverBps": 5.0
  },
  "instId": "BTC-USDT-SWAP", "bar": "1H",
  "limit": 1000,                     // 同 BacktestReq：ge=10 le=1000
  "startTs": 1700000000, "endTs": 1701000000,
  "initialCapital": 10000, "fees": null, "slippage": null, "leverage": null
}
```

响应：
```jsonc
{
  "runId": 123,
  "metrics": { "totalReturn": 0.18, "winRate": 0.55, "profitFactor": 1.7,
               "maxDrawdown": -0.09, "sharpe": 1.4, "tradeCount": 42, "avgPnl": 12.3 },
  "combo": {
    "annualizedReturn": 0.31,
    "timeline": [
      {"startTs": 1700000000, "endTs": 1700200000, "memberKey": "trend_pullback_long",
       "memberName": "趋势回调多", "regime": "trend_up", "bars": 56, "contribution": 0.07}
    ],
    "breakdown": [
      {"regime": "trend_up", "memberKey": "trend_pullback_long", "memberName": "趋势回调多",
       "bars": 120, "contribution": 0.12}
    ],
    "notes": ["range 体制无命中候选，回退全体成员"]   // 可空
  }
}
```

> **R1 修复（runId 落点）**：预览组合**未保存**，没有自己的 `strategies` 行，而 `StrategyRun.strategy_id` 是非空外键。故预览 run 的 `strategy_id` 复用「已 seed 的 `regime_rotation` builtin 模板行」（见 §3.5 `_combo_template_strategy_id`），真实成员/参数写入该 run 的 `config_json`。这样 `runId` 照常可用、`/runs/{id}/equity`、`/runs/{id}/trades`、run-range 渲染链路全部不变，且**不新增表/列、不新建临时策略行**——与 §6「无表结构变更」自洽。保存后的组合（有自身 id）走 §5.2，`strategy_id` 即其本身。

错误码：`EMPTY_RANGE`(400，区间无确认 K 线)、`COMBO_MEMBERS`(400，可用成员 < 2)、`BAD_TEMPLATE`(400，成员模板不存在)、`COMBO_TEMPLATE_MISSING`(500，组合模板未 seed，异常态)、`startTs>=endTs`(422，Pydantic validator)。

### 5.2 REST：既有端点的组合支持（无破坏性变更）

- `POST /strategies`：`templateKey="regime_rotation"`、`params={members,...policy}` 即创建保存的组合。
- `POST /strategies/{id}/backtest`：当 `{id}` 是组合时，services 内部转 combo 路径，返回结构在原 `{runId, metrics}` 基础上**追加** `combo` 字段（向后兼容：原子策略不带该字段）。
- `POST /strategies/compare`：`strategyIds` 可含组合 id；响应 `items[]` 中组合项与原子项同构（含 `metrics`+`equity`），可混排排序。
- `GET /strategies`：每项新增 `kind: "atomic" | "combo"`（前端据此分流；旧前端忽略该字段无影响）。

### 5.3 前端 API 客户端

```ts
// lib/api.ts
comboPreview: (body: {
  members: ComboMemberSpec[]; policy?: Partial<ComboPolicy>;
  instId: string; bar: string; limit?: number; startTs: number; endTs: number;
  initialCapital?: number; fees?: number; slippage?: number; leverage?: number;
}) => request<ComboPreviewResult>("/strategies/combo/preview", { method: "POST", body: JSON.stringify(body) }),
```

### 5.4 Agent 工具（可选增强，不计入 DoD）

在 `agent/tools/strategy_tools.py` 增 `compose_regime_rotation` 工具：入参 members+policy+区间，复用 `services.run_combo_preview`，回 `summary`+`payload`，让 AI 助手也能搭/试组合。可在后续迭代接线，不阻塞本期。

---

## 6. 数据模型（Data Model）

**无数据库表结构变更。** 组合复用既有表：

- `strategies` 行：`template_key="regime_rotation"`，`params_json` 形如：
  ```jsonc
  { "members": [{"templateKey":"...","params":{...}}, ...],
    "rebalanceBars":24, "lookbackBars":168, "selectionMetric":"sharpe",
    "minDwellBars":24, "switchMarginPct":0.10, "regimeGating":true, "turnoverBps":5.0 }
  ```
- `strategy_runs` 行：`mode="backtest"`，`config_json` 额外含 `{"combo":true, "preview":<bool>, "members":[...], "policy":{...}, "effectiveCandleCount":N}`；`metrics_json` 为组合 `compute_metrics` 结果。**`strategy_id` 非空外键约束（R1）**：保存组合的 run 用其自身 id；**预览** run 用已 seed 的 `regime_rotation` builtin 行 id（成员/参数以 `config_json` 为准），故全程**无表结构变更、无临时行**。
- `trades` / `equity_points`：落组合拼接后的成交/净值（`Trade.reason` 带成员前缀），与单策略 run 同构 → `GET /runs/{id}/trades`、`/runs/{id}/equity` 直接可用。

**内存形态（TS）**：
```ts
export interface ComboMemberSpec { templateKey: string; params: Record<string, unknown>; name?: string; }
export interface ComboPolicy {
  rebalanceBars: number; lookbackBars: number;
  selectionMetric: "return" | "sharpe" | "calmar";
  minDwellBars: number; switchMarginPct: number; regimeGating: boolean; turnoverBps: number;
}
export interface ComboTimelineSeg {
  startTs: number; endTs: number; memberKey: string; memberName: string;
  regime: string; bars: number; contribution: number;
}
export interface ComboBreakdownRow {
  regime: string; memberKey: string; memberName: string; bars: number; contribution: number;
}
export interface ComboPreviewResult {
  runId: number; metrics: Metrics;
  combo: { annualizedReturn: number; timeline: ComboTimelineSeg[];
           breakdown: ComboBreakdownRow[]; notes?: string[]; };
}
```

**`regime → 体制中文/候选成员`** 真源仍是后端 `engine/regime.REGIME_LABELS` 与 `REGIME_STRATEGY_HINTS`；前端 `regimeAffinity.ts` 是其只读镜像（仅用于成员徽标，不参与决策）。

---

## 7. 测试与验收（Testing & Acceptance Criteria）

### 7.1 后端单测（pytest，沿用既有隔离约定，见记忆：services/quant 测试共享临时 DB）

- `test_combo_causality_no_lookahead`（**核心，先写**）：构造一段 K 线，跑 combo 得 `equity[:i]`；篡改第 `i` 根之后的所有 K 线再跑，断言 `equity[:i]`、各段决策 `≤ i` 部分逐字节不变 → 证明无未来函数。
- `test_combo_single_member_equals_member`：`members` 仅 1 个可用成员、`turnoverBps=0`、`regimeGating=False` 时，组合净值/收益与该成员单策略 `run_backtest` 在重叠区间内一致（容差内）→ 退化等价。
- `test_combo_switches_with_regime`：合成「先单边上行后区间」的数据 + 成员含 trend 与 range 策略，断言活跃成员随体制切换；且在 `min_dwell_bars`/`switch_margin` 下切换次数有上界（抗 whipsaw）。
- `test_combo_metrics_and_equity_shape`：`metrics` 含全部 7 键；`equity_points` ts 严格递增；组合成交 ts 落在对应活跃段内。
- `test_combo_members_guard`：可用成员 < 2 → 400 `COMBO_MEMBERS`；成员模板失效被跳过并出现在 `notes`。
- `test_combo_api_preview`（monkeypatch `app.api.strategies.get_rest_client`）：返回 `{runId, metrics, combo{...}}`，`runId` 可经 `/runs/{id}/equity` 取回净值。
- `test_combo_in_compare`：`compare` 传 1 组合 id + 1 原子 id，`items` 含两项且结构同构。
- `test_combo_seed_registry`：`regime_rotation` 在注册表、`kind=="combo"`、被 seed 为 builtin、默认成员可跑通一次 preview。

### 7.2 前端单测（vitest；组件测须 `NODE_ENV=test`，见记忆）

- `regimeAffinity.test.ts`：templateKey → 体制中文标签映射正确、未知 key 安全降级。
- `combo.test.ts`：草稿校验（<2 或 >8 成员报错、参数越界夹取）、时间线配色稳定、分体制贡献汇总正确。
- `ComboView.test.tsx`（可选冒烟）：默认成员渲染、运行按钮在成员 < 2 时禁用、结果区在有 `runId` 时显示指标卡。

### 7.3 验收标准（对齐原始需求）

1. **自动识别体制并按时段切换**：组合回测后，「轮动时间线」清晰展示不同时间段对应的体制与所采用成员，且切换发生在再平衡边界、决策仅用历史数据。
2. **采用收益率最高的策略**：在 `regimeGating` 候选集合内，按所选 `selectionMetric` 近端表现选最优；单测证明随表现/体制变化而切换。
3. **避免未来函数**：`test_combo_causality_no_lookahead` 通过；成员回测沿用「次根开盘成交」；决策滞后 1 根。
4. **避免过拟合**：默认开启 regime gating + 迟滞(`switchMarginPct`) + 最短持有(`minDwellBars`) + 再平衡间隔；轮动规则自由参数少；UI 文案给出保守建议。
5. **界面有组合页签/开关**：顶部模式段新增「组合」；右侧 `ComboView` 美观、符合既有设计系统（`ui` 原语 + CSS 变量主题），交互含禁用态/错误态/加载态。
6. **可回测多种组合的期望收益/收益率**：`combo/preview` 即时回测单组合并显示收益率/夏普/回撤/期望年化；保存后的多个组合可在「对比」模式横向排名。
7. **可靠稳健**：数据不足/成员不足/区间为空均显式报错（不假数据）；组合复用既有测试覆盖的 `run_backtest`/`compute_metrics`。
8. `spec.md` 存在并出现在 `git status`，含全部规定章节，篇幅 ≥ 800 字。

---

## 8. 风险与缓解（Risks & Mitigations）

| 风险 | 影响 | 缓解 |
|---|---|---|
| **净值轮动在切入成员时"继承"其既有仓位**（成员独立回测时该仓位是逐步建立的） | 组合收益略偏乐观 | 每次切换扣 `turnoverBps` 换手成本近似建仓代价；默认 5bps；在文档与 UI tooltip 标注「轮动近似」；v2 提供委派式精确模型作为可选执行引擎。 |
| **逐根体制识别成本 O(n)** | 大窗口回测变慢 | 逐根仅用廉价结构标签 `_per_bar_primaries`；`classify_regime` 只在需要 evidence 的再平衡点调用；`limit ≤ 1000` 上限封顶；`run_combo_backtest` 在 `run_in_threadpool` 执行不阻塞事件循环。 |
| **过拟合**（成员过多 / lookback 过短 / 频繁切换） | 样本内好看、样本外差 | regime gating 注入领域先验、迟滞 + 最短持有 + 再平衡间隔抑制 whipsaw；成员上限 8；保守默认；UI 提示「成员宜少而互补、lookback 宜覆盖多个再平衡周期」。 |
| **成员模板失效 / 可用成员 < 2** | 组合无法运行 | 失效成员跳过并入 `notes`；可用 < 2 显式 400 `COMBO_MEMBERS`，绝不静默降级。 |
| **包级循环依赖**（`strategies` → `engine.regime`/`engine.backtester` → `strategies.base`） | import 期崩溃 | `engine/regime.py` 现有 `from ..strategies import STRATEGY_REGISTRY` 仅在 `candidate_strategies` 使用，保持现状；`engine/combo.py` 与 `strategies/combination.py` 对 `run_backtest`、regime 函数一律**函数内局部 import**，规避加载期环。 |
| **组合实盘/快速模拟（paper）未支持** | 用户在「实时」模式选组合会困惑 | v1 明确范围：组合仅支持回测/对比；`ComboView` 不暴露 paper 入口，`PaperSession` 不接组合；文档标注 v2 再做（需委派式实时轮动）。 |
| **成交口径近似**（组合净值来自净值轮动，胜率/盈亏比来自成员独立成交） | 指标轻微不一致 | 在结果区 tooltip 标注「收益类指标来自组合净值，成交类指标来自成员区间成交」；以收益率/夏普/回撤/期望年化为主指标。 |
| **回测请求并发跑 N 个成员独立回测** | 单次 combo 请求耗时随成员数线性增长 | 成员 ≤ 8；`run_in_threadpool` 串行成员回测（CPU 密集，避免线程争用）；UI 显示「回测中…」并禁用重复提交。 |

---

## 9. 实施顺序建议（供下游开发节点）

1. `base.py` 加 `kind` → `combination.py` 注册 → `__init__.py`/seed 接通（先让组合在 `GET /strategies` 露出且 `kind="combo"`）。
2. `engine/combo.py` 纯计算 + `test_combo_engine.py`（先写因果性测试，TDD）。
3. `agent/services.py` 编排 + `api/strategies.py` 路由 + `schemas.py` + `test_combo_api.py`。
4. 前端 `types.ts`/`api.ts`/`store.ts`/`regimeAffinity.ts`/`combo.ts`（含单测）。
5. `ComboView.tsx`/`ComboTimeline.tsx` + `StrategyPanel`/`ChartToolbar`/`CompareView`/`page.tsx` 接线。
6. 端到端：建一个默认组合 → preview → 看时间线/指标 → 另存为 → 在「对比」中与原子策略混排。

---

## 10. Clean Code 合规自查（对照 CLAUDE.md 阈值）

- 单文件 ≤ 800 行：`engine/combo.py` 预计 ~300 行（多小函数）；`agent/services.py` 增后 ~330 行；`ComboView.tsx` 若逼近阈值则拆出 `ComboBuilder`/`ComboResult` 子组件。
- 单函数 ≤ 50 行、形参 ≤ 5、嵌套 ≤ 4：`run_combo_backtest` 拆为 `_build_member_curves`/`_per_bar_regime`/`_select`/`_combine`/`_combo_trades`；多参数用 `ComboPolicy`/`ComboMember` 聚合传递。
- 错误显式传播：成员不足/区间为空/模板失效均显式 4xx 或 `ValueError`，不吞异常、不假数据。
- 命名自解释、英文标识符 + 中文 docstring，沿用仓库风格；布尔参数以 `regime_gating` 等语义化命名。
- 测试覆盖正常路径与异常路径，并先写「无未来函数」复现性测试，将因果纪律固化为护栏。

---

## 评审结论（Review Verdict）

**结论：有条件通过（Approved with Conditions）。**

本方案技术路线（净值轮动 + 因果体制选优 + 注册表复用 + 零核心引擎改动）经逐节核对真实源码，**可行、与现有架构一致、规模适中**，对「自动识别体制 / 切换至最优策略 / 避免未来函数 / 避免过拟合 / 界面页签 / 可回测多组合期望收益」六项需求均有可落地映射。原 v1 的 1×P0、2×P1 已在本 v2 正文就地修复（R1–R3），3×P2 文档/默认值缺陷已就地校正（R4–R6）。无遗留 P0/P1。

放行附以下条件，开发节点（Subtask #2）须遵守：

1. **R1 落点必须照修订实现**：预览 run 的 `strategy_id` 取已 seed 的 `regime_rotation` builtin 行 id，**严禁**让 `create_run` 收到 `None`（会触发 `IntegrityError`），亦**严禁**为绕过而新增表/列或新建临时策略行——保持 §6「无表结构变更」。
2. **R2 gating 必须按 `template_key`**：`_eligible_members` 与一切「成员↔体制候选」匹配一律用 `ComboMember.template_key`，不得回退到展示 `key`。
3. **R3 守卫必须保留**：`annualizedReturn` 的非正底数 / 零有效根数守卫不可省，`BARS_PER_YEAR` 必须按 `bar` 字典取用。
4. **TDD 因果护栏先行**：先写 `test_combo_causality_no_lookahead`（§7.1）并令其通过，再写组合逻辑——这是「避免未来函数」的硬验收，不可后补。
5. **R7 实现期落实（不阻断）**：(a) `run_compare` 的组合项复用共享 base candles、各成员 HTF 并集自取，原子项行为逐字节不变；(b) `/strategies/{id}/backtest` 经 `BacktestReq`（无 `params` 字段），保存组合仅用已存 `params_json`，文档勿暗示该路由可透传 `params_override`。
6. **阈值自查兜底**：`engine/combo.py` 与 `ComboView.tsx` 逼近 800 行阈值时，按 §10 预案拆分（`combo_service.py` / `ComboBuilder`+`ComboResult`），不得超阈提交。

满足以上条件即视为方案验收通过，可进入编码阶段。

---

## 实施过程发现的方案缺陷（Issues Found During Implementation）

> 由代码开发节点（Subtask #2）在落地时记录；均已在实现中按修正方案处理，未静默偏离。

- **D1（选优迟滞公式对负分在任者会反向发散）**：§3.3 步骤 5 / 文中迟滞判据写作
  `score_challenger > score_incumbent * (1 + switch_margin)`。当在任者近端表现为**负**（`score_incumbent < 0`，
  常见于回撤段或 sharpe 为负）时，`score_incumbent * (1 + switch_margin)` 反而**更负**（阈值被降低），
  导致挑战者更容易顶替在任者——与「迟滞应抬高替换门槛」的意图相反。**已修**：`engine/combo.py::_select`
  改用加性迟滞 `threshold = incumbent + abs(incumbent) * switch_margin`，使无论在任者分数正负，替换门槛
  都比在任者高出一个正向裕度，方向恒定、无发散。语义与正分情形一致，仅修正了负分边界。

- **D2（组合不接 paper，需从回测/实时策略列表中排除以防误启动）**：§4 文件变更计划对
  `StrategyPanel.tsx` 仅描述「增 `mode === "combo"` 分支」；但 §3.6 / §8 风险已明确「组合仅支持回测/对比，
  `PaperSession` 不接组合」。若不从「回测/实时」模式的 `StrategyList` 与 paper 多选集合中过滤掉
  `kind === "combo"` 的行，用户可在「实时」模式选中组合并启动 paper，会令 `PaperSession` 实例化
  `CombinationStrategy` 并在 `on_candle` 触发 `RuntimeError`。**已修**：`StrategyPanel` 用
  `atomicStrategies = strategies.filter(s => s.kind !== "combo")` 作为回测/实时列表与 paper 选择候选；
  组合统一在「组合」页签构建/回测、在「对比」页签横向比较。这是对原 §3.6/§8 意图的落地补强。

- **D3（ComboView 加载守门须置于全部 hooks 之后）**：实现初版把 `useMutation`（preview/saveAs）放在
  `if (!strategies) return ...` 之后，触发 React「Rendered more hooks than during the previous render」。
  **已修**：所有 hooks 无条件前置，加载守门下移到 hooks 之后；派生值用 `strategies ?? []` 兜底。属实现细节，
  不改方案结构。

---

（完）
