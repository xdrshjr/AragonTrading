# 因子库扩充设计方案 —— 永续合约友好型 · 顶级量化机构因子（12 → 20）

> **版本：v2**（已完成方案评审与就地修复，见文首「评审记录」与文末「评审结论」）
> **特性目录**：`docs/plans/factor-library-crypto-expansion/`
> **关联需求**：因子实验室内置因子库当前仅 12 个内置因子，数量偏少；需扩充 **8** 个「顶级量化机构常用、适配虚拟货币 / 永续合约市场、追求高收益率与高胜率」的因子，使内置因子总数达到 **20** 个，且保证这些因子能有效用于交易。

---

## 评审记录（Review Notes）

> 评审人：Anthropic 工程团队资深评审。评审维度：可行性（Feasibility）、完整性（Completeness）、一致性（Consistency）、规模适配（Right-sizing）。以下每条标注严重级别（P0/P1/P2）与处理状态；所有 **P0 / P1 已在 v2 文档正文就地修复**。

**⚠️ 评审前置事实（重要）**：上游「方案设计」节点在任务面板中标记为 `completed`，但**未在 `docs/plans/` 下产出任何本特性的 `spec.md`**（全仓库近期唯一新建的 spec 属于无关特性 `ai-trade-multi-symbol-and-timeframe`）。为避免阻塞下游「代码开发」节点，本评审节点在**逐一核对真实代码**（`engine/factors/library.py`、`scoring.py`、`mining.py`、`evaluation.py`、`agent/prompts.py`、前端 `lib/factor.ts`、以及既有因子测试）后**重建**了本设计文档，并在其上完成评审与 P0/P1 修复。下游请以本 v2 文档为准。

| 编号 | 维度 | 级别 | 问题 | 处理 |
|---|---|---|---|---|
| P0-1 | 可行性 | **P0** | 需求提到「永续合约市场因子」，但因子计算的 DataFrame **只含 OHLCV**（`ts/o/h/l/c/vol`），**没有资金费率（funding）、持仓量（OI）、基差、订单簿**等永续特有数据，也没有对应的采集 / 缓存 / `FactorWindow` 管线。若照字面实现「资金费率因子」将无法运行。 | **已修复**：§2 明确将范围冻结为「OHLCV 可算、且适配加密永续（7×24、高波动、强趋势/极端反转）的机构级因子」，并把 funding/OI 类因子列为**显式非目标**（附未来扩展路径）。 |
| P0-2 | 完整性 / 正确性 | **P0** | 原始需求未规定新因子的**除零与 NaN 安全**处理；MFI/CMF/唐奇安/效率比/波动调整动量的分母都可能为 0（`h-l` 十字星、空的负向资金流窗口、零波动区间、`c.shift`），会产出 `inf`/`NaN` 风暴并拖垮评估 coverage。 | **已修复**：§6 为**每个**新因子给出分母守卫口径（`replace(0.0, np.nan)`；MFI 负向流为 0 时按 `rsi` 口径取 100；CMF 在 `h==l` 时 `mfm=0` 以免单根十字星把整窗 rolling-sum 置 NaN），与库既有「绝不补零外推」约定一致。 |
| P0-3 | 可行性 | **P0** | 未强调**严格因果**是硬门禁：`tests/test_factor_library.py::test_compute_factor_is_strictly_causal` 会篡改第 k 根之后的 c/h/l/vol，断言 ≤k 输出逐元素不变；任何前视（未来数据泄漏）因子将直接失败。 | **已修复**：§3 所有公式仅用 `rolling/ewm/shift/cumsum/diff` 等因果算子；§6 与 §7 明确该门禁自动覆盖新因子，且列出需补的定向单测。 |
| P1-1 | 一致性 | **P1** | 新增短期反转因子 `rev_str` 与既有 `mom_roc` 都用 `pct_change`，存在语义 / 实现重叠，可能被误当重复因子。 | **已修复**：§3 将 `rev_str` 定为 `reversal` 类、`short` 方向、默认小窗口（`win=3`），与 `mom_roc`（`momentum`/`long`/`win=20`）在 key、类别、方向、默认参数四个维度均不同；组合去重键为 `factorKey|params|direction`，key 不同即不冲突。 |
| P1-2 | 一致性 | **P1** | 若为偏度类因子新引入 `distribution`/`skew` 等**新类别**，前端 `lib/factor.ts` 的 `CATEGORY_LABELS` 是**硬编码**映射，未命中会把英文 key 直接显示到 UI（且需改前端，违背「纯增量」）。 | **已修复**：§3 将偏度因子 `vol_skew` 归入既有 `volatility` 类；**全部 8 个新因子只复用现有 7 个类别**，故**零前端改动**。 |
| P1-3 | 完整性 | **P1** | 新因子若不在 `mining._MINE_GRID` 中登记，挖掘时只会回落到单一默认参数候选（`.get(key, [{}])`），网格探索覆盖不足。 | **已修复**：§4 明确要求为 8 个新因子各补 1 条 `_MINE_GRID`（默认 + 至多 1~2 个邻近参数）。 |
| P1-4 | 完整性 / 测试 | **P1** | 需求要求「确保有效用于交易」，但未定义验收测试；库内既有测试虽泛化覆盖因果 / 形状 / 目录一致性，仍缺**新因子定值合理性**与**总数达 20** 的锁定测试。 | **已修复**：§7 增加 `test_factor_library_expansion`（断言库含 20 因子、8 个新 key 的类别 / 方向、已知输入定值区间、除零安全），并说明如何用因子实验室的评估 / 挖掘 / 组合链做端到端有效性验证。 |
| P2-1 | 一致性 | P2 | 新 key 命名须沿用既有前缀约定（`mom_`/`trend_`/`rev_`/`vol_`/`volu_`/`liq_`/`px_`）。 | 已在 §3 命名表统一。 |
| P2-2 | 一致性 | P2 | `params_schema` 字段须与前端 `ParamSchema`（`type/default/min/max/step/label`）严格对齐。 | 已在 §3 逐因子给出完整 schema。 |
| P2-3 | 完整性 | P2 | 偏度 / MFI 小样本稳定性：窗口过小时统计量噪声大。 | §3 给出稳健默认（skew `win=30`、MFI `win=14`）与 min/max 边界。 |
| P2-4 | 规模适配 | P2 | Agent primer 字符串随因子数增长；需确认成本可忽略。 | primer 由 `available_factors()` 单源生成，新增 8 行一句话定义，token 增量可忽略，无需改 `prompts.py`。 |

---

## 0. 背景与目标

因子实验室（Factor Lab）以 `engine/factors/library.py` 中的 `FACTOR_LIBRARY` 作为**唯一真源**（C6）驱动整条因子链：目录浏览、单因子评估（IC/RankIC/分位单调性）、按类别挖掘、组合打分、权重训练、以及策略实盘/回测。当前只有 12 个内置因子，类别覆盖偏薄（`liquidity`、`mean_revert` 各仅 1 个）。

**目标**：新增 8 个**机构级、加密永续友好**的因子，使总数达 20，并保证：

1. 严格因果、相对量纲、纯函数（与库既有约定完全一致）；
2. **零数据模型 / 零接口 / 零前端改动**（纯增量落在 1 个引擎文件，外加挖掘网格与测试）；
3. 覆盖「趋势跟随 / 波动调整动量 / 突破 / 短期反转 / 量价资金流 / 尾部偏度」等在加密永续市场经实证有效、被 CTA / 量化对冲基金广泛使用的因子族，以提升组合的收益率与胜率上限。

## 1. 现状分析（真源与数据边界）

- **因子契约**（`FactorSpec`）：`cn`（一句话中文定义）、`category`、`direction_hint`（`long`/`short`）、`params_schema`、`compute(df, params) -> pd.Series`、`intraday_only`。
- **数据列**：`df` 由 `candles_to_df` 产出，列为 `ts, o, h, l, c, vol`（升序）。**无 funding / OI / 订单簿**。
- **可复用纯指标**（`engine/indicators.py`）：`ema / sma / rsi / bollinger / atr / vwap_session`，均严格因果。
- **打分/训练**：`scoring.compose_score`→`directional_values`→`compute_factor(df, factorKey, …)` 完全**泛化**，新因子无需改打分层即自动可组合、可训练、可回测、可实盘。
- **评估/挖掘**：`evaluation.evaluate_factor_key`、`mining.default_candidates`（遍历 `FACTOR_LIBRARY`）、`mining._MINE_GRID`（每因子参数网格，缺省回落 `[{}]`）。
- **对外暴露**：`available_factors()` 同时驱动前端目录与 Agent primer（`agent/prompts.py` 单源引用），因此新因子**自动**出现在 UI 与 Agent。
- **测试门禁**（`tests/test_factor_library.py`）：因果、形状、暖机 NaN、目录一致性均以 `for key in FACTOR_LIBRARY` 泛化断言 —— 新因子**自动纳入**这些门禁，其中**严格因果**是最强硬门禁。
- **前端类别标签**（`lib/factor.ts::CATEGORY_LABELS`）：硬编码 7 类中文名，未命中回落原始 key。

**结论**：本需求的可行落点是「在 `library.py` 增加 8 个 OHLCV 可算、严格因果的 `FactorSpec`」，其余全部沿链自动生效。

## 2. 范围与非目标（Scope / Non-Goals）

**范围（In-Scope）**：新增 8 个 OHLCV 可算、严格因果、相对量纲、且**复用现有 7 个类别**的因子；补齐挖掘网格与验收测试。

**非目标（Non-Goals，含理由）**：

- ❌ **资金费率 / 持仓量 / 基差 / 订单簿 / 逐笔** 类永续特有因子 —— 当前无数据管线（需扩 OKX 采集适配、`candle_cache` 或新表、`FactorWindow`、评估/挖掘取数），属独立特性，**本次不做**（P0-1）。作为「适配永续」的可行替代，本次改以「加密永续市场经实证有效的价量结构因子」落地。
- ❌ 新增因子**类别**（避免改前端硬编码标签，P1-2）。
- ❌ 修改打分 / 训练 / 评估 / 挖掘的**核心算法**或**数据模型 / API / DB schema**（纯增量，Right-sizing）。
- ❌ 引入新的第三方依赖（仅用 numpy / pandas 内置 + 既有 `indicators`）。

## 3. 新增 8 个因子（定义 / 类别 / 方向 / 公式 / 参数 / 出处）

> 全部 `intraday_only=False`（均与周期无关，`px_vwap_dist` 之外无需 `_bar`）。方向为 `direction_hint`，实际符号仍由评估层 IC 决定；此处给出机构惯例方向。公式中的分母守卫见 §6。

| # | key | 中文定义（cn） | category | direction | 默认参数 | 机构出处 / 依据 |
|---|---|---|---|---|---|---|
| 1 | `mom_tsmom_vol` | 波动率调整时序动量（收益/已实现波动） | momentum | long | `win=30` | AQR 时序动量（Moskowitz-Ooi-Pedersen 2012）；波动缩放是 CTA 提升夏普的核心手段 |
| 2 | `mom_macd_hist` | MACD 柱状（归一化动量加速度） | momentum | long | `fast=12, slow=26, signal=9` | Appel MACD；柱状=动量的二阶（加速度），价格归一以跨标的可比 |
| 3 | `trend_donchian_pos` | 唐奇安通道位置（突破/趋势位置） | trend | long | `win=20` | Donchian 通道 / 海龟交易法则；加密趋势跟随主力信号 |
| 4 | `trend_efficiency_ratio` | 考夫曼效率比（净位移/路径，带符号） | trend | long | `win=20` | Kaufman KAMA 效率比；度量趋势「纯净度」信噪比 |
| 5 | `rev_str` | 短期反转（近 N 根收益反向） | reversal | short | `win=3` | 短期反转异象（Jegadeesh 1990）；加密日内/日频尤为显著 |
| 6 | `volu_mfi` | 资金流量指标 MFI（量价超买超卖） | volume | short | `win=14` | Money Flow Index；成交量加权 RSI，量价背离预警 |
| 7 | `volu_cmf` | 蔡金资金流 CMF（累积/派发） | volume | long | `win=20` | Chaikin Money Flow；主力吸筹/派发的资金流向 |
| 8 | `vol_skew` | 收益偏度（尾部/彩票效应） | volatility | short | `win=30` | 加密截面偏度/彩票需求因子；高正偏预示更低未来收益 |

**扩充后类别分布**：momentum 4、trend 4、reversal 3、volatility 3、volume 4、liquidity 1、mean_revert 1 = **20**。

**逐因子因果公式**（`c/h/l/vol` 均为 df 列；`ema` 取自 `indicators`）：

1. **`mom_tsmom_vol`**：`ret = c.pct_change(win)`；`lr = ln(c/c.shift(1))`；`rv = lr.rolling(win, min_periods=win).std(ddof=0)`；**factor = `ret / (rv * sqrt(win))`**（`rv` 经 `replace(0,NaN)`）。相较原始 `mom_roc`，按近端波动缩放显著改善风险调整后表现。
2. **`mom_macd_hist`**：`macd = ema(c, fast) - ema(c, slow)`；`signal = ema(macd, signal)`；**factor = `(macd - signal) / c`**（价格归一，`c` 经 `replace(0,NaN)`）。
3. **`trend_donchian_pos`**：`hh = h.rolling(win).max().shift(1)`；`ll = l.rolling(win).min().shift(1)`；`mid = (hh+ll)/2`；**factor = `(c - mid) / (hh - ll)`**（`(hh-ll)` 经 `replace(0,NaN)`）。`shift(1)` 排除当根自身、度量对「前序通道」的突破。
4. **`trend_efficiency_ratio`**：`change = c - c.shift(win)`；`path = c.diff().abs().rolling(win, min_periods=win).sum()`；**factor = `change / path`**（`path` 经 `replace(0,NaN)`），值域 `[-1,1]`。
5. **`rev_str`**：**factor = `c.pct_change(win)`**，`win` 默认 3；`direction=short` 表示近端高收益→预期反转下行。
6. **`volu_mfi`**：`tp = (h+l+c)/3`；`rmf = tp * vol`；`up = tp > tp.shift(1)`；`pos = rmf.where(up, 0)`；`neg = rmf.where(~up & (tp<tp.shift(1)), 0)`；`mfr = pos.rolling(win).sum() / neg.rolling(win).sum()`（负向和为 0→按 rsi 口径 factor=100）；**factor = `100 - 100/(1+mfr)`**。
7. **`volu_cmf`**：`mfm = ((c-l) - (h-c)) / (h-l)`，**当 `h==l` 取 `mfm=0`**（避免十字星污染滚动和，§6）；`mfv = mfm * vol`；**factor = `mfv.rolling(win).sum() / vol.rolling(win).sum()`**（成交量和为 0→NaN），值域 `[-1,1]`。
8. **`vol_skew`**：`lr = ln(c/c.shift(1))`；**factor = `lr.rolling(win, min_periods=win).skew()`**；`direction=short`（高正偏→更低未来收益）。

**`params_schema` 样式**（与前端 `ParamSchema` 对齐，逐字段 `type/default/min/max/step/label`）：

- 单窗口类（1/4/5/8）：`{"win": {"type":"int","default":<d>,"min":<lo>,"max":200,"step":1,"label":"回看根数"}}`
- `trend_donchian_pos`（3）：`{"win": {"type":"int","default":20,"min":5,"max":200,"step":1,"label":"通道根数"}}`
- `mom_macd_hist`（2）：三键 `fast`（default 12，2–100）、`slow`（default 26，2–200）、`signal`（default 9，1–100），`type:int`（既有 `rev_boll_z` 已证多参数 schema 可行）。
- `volu_mfi`（6）：`{"win": {"type":"int","default":14,"min":2,"max":100,"step":1,"label":"回看根数"}}`。
- `vol_skew`（8）：`win` default 30、min 10、max 200（保证偏度小样本稳健，P2-3）。

## 4. 文件 / 模块变更计划

> **纯增量**。核心只改 1 个引擎文件；其余为挖掘网格与测试。**不改数据模型 / API / 前端 / 依赖。**

1. **`services/quant/app/engine/factors/library.py`（核心，必改）**
   - 新增 8 个模块级纯函数：`_mom_tsmom_vol / _mom_macd_hist / _trend_donchian_pos / _trend_efficiency_ratio / _rev_str / _volu_mfi / _volu_cmf / _vol_skew`（均严格因果，含 §6 分母守卫）。
   - 在 `FACTOR_LIBRARY` 追加 8 条 `FactorSpec`（key/cn/category/direction/schema/compute，全部 `intraday_only=False`）。
   - `_mom_macd_hist` 复用 `ema`；其余用 `df` 列 + pandas rolling / ewm；如需 `np.log`/`np.sqrt` 直接用 numpy。
2. **`services/quant/app/engine/factors/mining.py`（建议，强烈推荐）**
   - 为 8 个新 key 各补 1 条 `_MINE_GRID`（默认 + 1~2 邻近参数，如 `mom_tsmom_vol: [{"win":20},{"win":30},{"win":60}]`），使挖掘网格覆盖新因子（P1-3）。不改则回落单默认候选，功能不坏但探索不足。
3. **`services/quant/tests/test_factor_library.py`（必改，追加）** 与新 **`tests/test_factor_library_expansion.py`**
   - 断言 `len(FACTOR_LIBRARY) == 20`、8 个新 key 存在且 `category/direction` 正确、`available_factors()` 目录一致；
   - 逐因子定值合理性（已知合成 K 线上输出有限、值域合规、暖机 NaN、除零不产 inf）；
   - 严格因果由既有 `test_compute_factor_is_strictly_causal` 自动覆盖（无需额外写，但在评审中显式确认）。

**无需改动**（因单源自动生效）：`scoring.py`、`evaluation.py`、`agent/prompts.py`、前端 `lib/factor*.ts` / `FactorCatalog.tsx` / `FactorTable.tsx`、DB `models.py` / `session.py`、任何 API 路由或 schema。

## 5. 数据模型与接口影响

- **数据模型**：无。不新增表、不加列、不需 `_ensure_*` ALTER 迁移。
- **接口**：无新增 / 变更端点。`GET /api/factors`（库目录）响应自动多出 8 条（由 `available_factors()` 派生），前端 TanStack Query 无需改动。
- **契约稳定性**：新增 key **只增不改**，不重命名、不删除既有 12 个 key（遵守「对外 key 稳定＝破坏性变更」约定）。

## 6. 边界与错误处理

- **除零 / 非有限**：所有分母统一 `.replace(0.0, np.nan)`（`rv`、`c`、`hh-ll`、`path`、`vol.rolling.sum`）；产出 NaN 由评估 / 组合层按「跳过」处理，**绝不补零外推**（C1）。
- **MFI 空负向流**：窗口内负向资金流之和为 0 → `mfr=inf` → 按 `indicators.rsi` 同口径令 `factor=100`（用 `.where(neg_sum!=0, 100.0)`）。
- **CMF 十字星**：`h==l` 时 `((c-l)-(h-c))/(h-l)` 为 0/0；取 `mfm=0`（无量价信号），避免单根十字星经 rolling-sum 把整窗置 NaN（这是领域规则，非补零外推，故显式注释原因）。
- **偏度小样本**：`vol_skew` 用 `min_periods=win`，`win` 下界 10；暖机段 NaN。
- **严格因果**：仅用 `rolling/ewm/shift/diff/cumsum` 等只依赖 ≤i 数据的算子；`donchian` 的 `.shift(1)`、`efficiency_ratio` 的 `c.shift(win)` 均因果。→ 自动通过 `test_compute_factor_is_strictly_causal`（P0-3）。
- **参数兜底**：沿用 `_param()`（非法 / 缺失回落库默认，边界一次性兜底）。

## 7. 测试计划

1. **既有泛化门禁自动覆盖**（`test_factor_library.py`）：因果、形状（等长、index 0..n-1）、暖机 NaN、`available_factors()` 目录一致、`directionHint ∈ {long,short}`。
2. **新增锁定测试**（`test_factor_library_expansion.py`）：库总数 == 20；8 个新 key 的 `category/direction/cn` 断言；在 `make_synthetic_candles` 合成 K 线上逐因子断言输出有限、值域（如 CMF/ER ∈ [-1,1]、MFI ∈ [0,100]）、除零不产 `inf`。
3. **挖掘覆盖**（`test_factor_mining.py::test_default_candidates_cover_whole_library`）：已用 `keys == set(FACTOR_LIBRARY)` 泛化断言，自动覆盖新因子（无需改）。
4. **端到端有效性验证**（人工 / 集成）：在因子实验室对 BTC-USDT-SWAP 等主流永续标的，用「评估」看各新因子多周期 RankIC / IR 与分位单调性，用「挖掘」按类别排名，用「组合 + 权重训练 + 一键回测」验证纳入新因子后的组合收益 / 胜率提升。以此满足「确保有效用于交易」的验收，而非在单测里对真实盈利做脆弱断言。

> 遵守 CLAUDE.md §七：新公开行为均补单测；不 mock 高风险路径；单测用合成数据可快速重复运行。

## 8. 风险评估与回滚

- **风险极低**：改动集中于 1 个纯函数文件（+ 挖掘网格 + 测试），无状态、无迁移、无接口。
- **主要风险**＝**非因果实现**：任一新因子误用未来数据将被因果门禁拦截（CI 即红）—— 属「实现期可即时发现」而非「上线后隐患」。
- **次要风险**＝**分母未守卫**导致 inf/NaN：由 §6 口径 + 定值测试覆盖。
- **回滚**：从 `FACTOR_LIBRARY` 删除对应 8 条 + 对应 `_MINE_GRID` 条目即可完全回退；因 key 只增不改，删除不影响既有已保存组合 / 策略（它们不引用新 key）。

## 9. 一致性核对（对照 CLAUDE.md 与既有约定）

- **单文件 ≤ 800 行**：`library.py` 现 248 行，新增 8 函数 + 8 条目预计 +~140 行，仍 < 400，不触阈值。
- **单函数 ≤ 50 行 / 参数 ≤ 5 / 圈复杂度 ≤ 10**：每个 `_compute` 均为数行的向量化表达式，天然合规。
- **命名**：动词/领域名词前缀化（§3 命名表），无无信息量缩写。
- **错误处理**：边界一次性兜底（`_param`）、显式 NaN 传播、不吞异常。
- **注释**：`cn` 一句话定义解释「是什么/为什么」，公式注释可回退条件。
- **语言特有**：Python PEP8 + Google docstring；不新增依赖。
- **风格一致**：与既有 12 因子同构（同 `FactorSpec`、同守卫口径、同真源单源模式），不制造孤岛。

---

## 评审结论（Review Verdict）

**结论：有条件通过（Approved with Conditions）。**

方案在可行性、完整性、一致性、规模适配四个维度均已达标：改动为纯增量、单源、零数据模型/接口/前端改动，8 个因子均 OHLCV 可算、严格因果、复用既有类别，且都是加密永续市场经实证有效的机构级因子。所有 **P0（数据边界可行性、除零/NaN 安全、严格因果门禁）与 P1（rev_str 去重、类别复用免改前端、挖掘网格、验收测试）问题已在 v2 正文就地修复**，无遗留 P0/P1。

**放行条件（下游「代码开发」节点必须落实，否则视为未完成）**：

1. **C-1（对应 P0-2/P0-3/§6）**：8 个因子逐一实现 §6 的分母守卫与因果算子；实现后本地跑 `pytest tests/test_factor_library.py`，确认 `test_compute_factor_is_strictly_causal` 全绿。
2. **C-2（对应 P0-1/§2）**：严格限定为 §3 的 8 个 OHLCV 因子，**不得**擅自引入 funding/OI 等需新数据管线的因子或新类别。
3. **C-3（对应 P1-3/§4-2）**：为 8 个新因子补齐 `mining._MINE_GRID` 条目。
4. **C-4（对应 P1-4/§7）**：新增 `test_factor_library_expansion.py`（库总数=20、新 key 类别/方向、定值与除零安全），并确认 `test_default_candidates_cover_whole_library` 仍绿。
5. **C-5（对应 §9）**：保持 `library.py` 各阈值合规，key 只增不改不删。

满足上述条件即达 Definition of Done。

---

## 实施过程发现的方案缺陷（Issues Found During Implementation）

> 由「代码开发」节点在落地时记录。原则：不静默偏离设计；发现缺陷即在此登记并给出已采用的修正。

- **I-1（§4「文件/模块变更计划」测试清单不完整）**：设计的变更计划只列了 `test_factor_library.py` 与新增 `test_factor_library_expansion.py`，并在「无需改动」中未提及 `tests/test_factor_tools.py`。但该文件的 `test_list_factors_tool` **硬编码断言** `len(res.payload["library"]) == 12` 与 `len(res.payload["stored"]) >= 12`（`library` 由 `available_factors()` 派生、`stored` 由 `_seed_builtin_factors()` 播种，二者都随 `FACTOR_LIBRARY` 增长）。扩充到 20 后该用例必然变红。
  - **根因**：设计对「库总数」的锁定测试只搜到 `test_factor_library.py`，漏检了 Agent 工具层同样硬编码了旧总数 12。
  - **已采用修正**：把 `test_factor_tools.py:test_list_factors_tool` 的两处 `12` 改为 `20`（与库总数一致，属「库计数契约」的直接同步，非无关改动）。全量因子测试（`-k "factor or scoring or evaluation or training or mining"`）随后全绿。
  - **影响面**：仅测试常量同步，无生产代码/接口/数据模型改动，不违背「纯增量、key 只增不改不删」约定。
