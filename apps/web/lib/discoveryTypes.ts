// 策略与因子挖掘的全部 WS / REST 类型（strategy-factor-discovery §5.4）。
// 与后端 camelCase 一一对应；新增字段务必同步 lib/discovery.ts 的字段往返用例，
// 否则会重演仓库既知坑「后端算了、类型补了，用户看不见」（R-11）。

export type DiscoveryAxis = "factor" | "factor_combo" | "strategy_param" | "strategy_combo";

export type DiscoveryStatus = "running" | "paused" | "stopped" | "succeeded" | "failed";

export type DiscoverySource = "seed" | "random" | "mutate" | "crossover" | "perturb" | "llm";

export type DiscoveryVerdict = "kept" | "rejected";

export interface SourceBudget {
  mutate: number;
  crossover: number;
  perturb: number;
  random: number;
  llm: number;
}

export interface DiscoveryCounters {
  tested: number;
  kept: number;
  llmCalls?: number;
  prunedCandidates?: number;
  prunedIterations?: number;
  rejected: Record<string, number>;
}

export interface FsaBan {
  skeleton: string;
  share: number;
  bannedAtIter: number;
}

export interface DiscoveryDataWindow {
  startTs: number;
  endTs: number;
  lastRefreshTs: number;
}

export interface DiscoveryLlmUsage {
  callsThisHour: number;
  limit: number;
  /** 本小时用量**自进程启动起算**（不跨重启累计）；UI 必须如实标注（§2.11）。 */
  sinceProcessStart: boolean;
  enabled: boolean;
}

/** WS `state` 事件负载 = 运行态全量快照（订阅即首发）。 */
export interface DiscoveryStateEvent {
  runId: number;
  status: DiscoveryStatus;
  pauseReason: string;
  iteration: number;
  counters: DiscoveryCounters;
  budget: SourceBudget;
  poolSizes: Record<string, number>;
  axisPlan?: Record<string, number>;
  axes?: DiscoveryAxis[];
  axisSnapshots?: Record<string, Record<string, number>>;
  mechanismCoverage: Record<string, number>;
  fsaBans: FsaBan[];
  nextIterationTs: number | null;
  dataWindow: DiscoveryDataWindow;
  llmUsage: DiscoveryLlmUsage;
  rebuilding: boolean;
  keptCount: number;
  testedCount: number;
}

export interface DiscoveryIterationRow {
  index: number;
  startedTs: number;
  finishedTs: number;
  budget: SourceBudget;
  generated: number;
  ruleRejected: number;
  llmRejected: number;
  validated: number;
  kept: number;
  rejectedByReason: Record<string, number>;
  bySource: Record<string, number>;
  truncatedByTimeout: number;
  truncatedByBacktestCap?: number;
  overrun: boolean;
  skipped: string;
  durationMs: number;
  /** 逐轴计数（漏斗按轴分列直接读它）。 */
  perAxis?: Record<string, DiscoveryAxisTally>;
  axisShares?: Record<string, number>;
  /** BL-9：上游库存不足时的让渡记录（必须有 UI 落点，否则等于没做）。 */
  yieldedByPool?: Record<string, AxisPlanEntry>;
  llm: {
    calls: number;
    latencyMs: number;
    degraded: boolean;
    degradedReason: string;
    promptChars: number;
  };
  note: string;
}

export interface DiscoveryMetricSide {
  rankIc: number | null;
  ic: number | null;
  ir: number | null;
  tStat: number | null;
  monotonicity: number | null;
  topBottomSpread: number | null;
  signStability: number | null;
  samples: number;
  bars: number;
}

export interface DiscoveryMetrics {
  direction: "long" | "short";
  coverage: number;
  horizon: number;
  trials: number;
  requiredIr: number;
  turnoverProxy: number | null;
  roundTripCost: number;
  costGate: number;
  maxIcCorr: number;
  maxValueCorr: number;
  is: DiscoveryMetricSide;
  oos: DiscoveryMetricSide;
}

/** 策略轴的单段指标（``metrics_of`` 的并集出口；``calmar`` 只可能来自 metrics_ext）。 */
export interface DiscoveryStrategySide {
  bars?: number;
  sharpe?: number;
  totalReturn?: number;
  maxDrawdown?: number;
  calmar?: number;
  profitFactor?: number;
  winRate?: number;
  tradeCount?: number;
  tradeRate?: number;
}

export interface DiscoveryGateRow {
  id: string;
  label: string;
  value: number | null;
  gate: number | null;
  passed: boolean;
}

export interface DiscoveryParamDiff {
  key: string;
  label: string;
  default: unknown;
  value: unknown;
}

/** 策略轴 `metrics_json`（discovery-strategy-axes §5.1）。 */
export interface DiscoveryStrategyMetrics {
  kind: DiscoveryAxis;
  templateKey?: string;
  /** 模板中文名（后端 `describe_strategy.template_name` 的唯一真源，缺失才回落 key）。 */
  templateName?: string;
  trials: number;
  requiredSharpe: number;
  costs: {
    initialCapital: number;
    takerFeeBps: number;
    slippageBps: number;
    leverage: number;
  };
  /** `gated`（单策略纯切片）| `rebased`（组合净值重基）—— 两种口径**不可比**，必须如实标注。 */
  clipMode: "gated" | "rebased";
  warmupPad: number;
  is: DiscoveryStrategySide;
  oos: DiscoveryStrategySide;
  stress?: { costMult: number; totalReturn: number | null };
  maxEquityCorr: number;
  thresholds?: Record<string, number>;
  paramDiff?: DiscoveryParamDiff[];
  members?: Record<string, unknown>[];
  policy?: Record<string, unknown>;
}

export interface DiscoveryEquityPoint {
  ts: number;
  equity: number;
}

export interface TemplateSpec {
  key: string;
  name: string;
  family: string;
  familyCn: string;
  minable: boolean;
  /** 不可挖时的中文原因——**必须展示**，绝不灰着不说为什么。 */
  reason: string;
  paramCount: number;
  htfBars: string[];
}

export interface AxisInfo {
  label: string;
  desc: string;
  backtestsPerCandidate: number;
  dependsOn: DiscoveryAxis[];
  cost: number;
}

export interface AxisPlanEntry {
  count: number;
  reason: string;
  toAxis: string;
}

export interface DiscoveryAxisTally {
  generated: number;
  ruleRejected: number;
  llmRejected: number;
  validated: number;
  kept: number;
  truncated: number;
  truncatedByBacktestCap: number;
  rejectedByReason: Record<string, number>;
}

export interface DiscoveryCandidateRow {
  id?: number;
  runId?: number;
  iterationIdx?: number;
  kind: string;
  genomeHash: string;
  exprText: string;
  describe: string;
  skeleton?: string;
  mechanism: string;
  source: DiscoverySource;
  verdict: DiscoveryVerdict;
  rejectReason: string;
  demoted?: boolean;
  trialsAtAccept?: number;
  promotedKind?: string;
  promotedId?: number | null;
  createdTs: number;
  metrics: Partial<DiscoveryMetrics> & Partial<DiscoveryStrategyMetrics>;
}

export interface DiscoveryCandidateDetail extends DiscoveryCandidateRow {
  series: {
    isIc?: number[];
    oosIc?: number[];
    quantiles?: { q: number; meanRet: number; count: number }[];
    equityIs?: DiscoveryEquityPoint[];
    equityOos?: DiscoveryEquityPoint[];
    gates?: DiscoveryGateRow[];
  };
  genome: Record<string, unknown>;
}

export interface DiscoveryRunSummary {
  runId: number;
  label: string;
  instId: string;
  bar: string;
  status: DiscoveryStatus;
  pauseReason: string;
  iteration: number;
  keptCount: number;
  testedCount: number;
  counters: Partial<DiscoveryCounters>;
  startedTs: number;
  lastIterTs: number | null;
  finishedTs: number | null;
  errorCode: string;
  errorMessage: string;
}

export interface DiscoveryRunDetail extends DiscoveryRunSummary {
  config: Record<string, unknown>;
  checkpoint: Record<string, unknown>;
  state: DiscoveryStateEvent | null;
  iterations: DiscoveryIterationRow[];
}

/** 前端内存态（§5.4）：全部字段都必须能被 WS 事件穿透（discovery.test.ts 字段往返守门）。 */
export interface DiscoveryRunState {
  runId: number;
  status: DiscoveryStatus;
  pauseReason: string;
  iteration: number;
  counters: DiscoveryCounters;
  budget: SourceBudget;
  poolSizes: Record<string, number>;
  axisPlan?: Record<string, number>;
  axes?: DiscoveryAxis[];
  axisSnapshots?: Record<string, Record<string, number>>;
  mechanismCoverage: Record<string, number>;
  fsaBans: FsaBan[];
  nextIterationTs: number | null;
  dataWindow: DiscoveryDataWindow;
  llmUsage: DiscoveryLlmUsage;
  rebuilding: boolean;
  keptCount: number;
  testedCount: number;
  iterations: DiscoveryIterationRow[];
  candidates: DiscoveryCandidateRow[];
  connected: boolean;
  lastError: { code: string; message: string } | null;
}

export interface DiscoveryDefaults {
  implementedAxes: DiscoveryAxis[];
  allAxes: DiscoveryAxis[];
  bars: string[];
  windowBars: { default: number; min: number; max: number };
  candidatesPerIteration: { default: number; min: number; max: number };
  iterationIntervalS: { default: number; min: number; max: number };
  horizon: { default: number; min: number; max: number };
  dataRefreshS: { default: number; min: number };
  maxKept: { default: number; min: number; max: number };
  maxKeptPerAxis: { default: number; min: number; max: number };
  axisInfo: Record<DiscoveryAxis, AxisInfo>;
  axisLabels: Record<string, string>;
  templates: TemplateSpec[];
  strategyThresholds: Record<string, number>;
  comboSize: { default: { min: number; max: number }; min: number; max: number };
  rotationSize: { default: { min: number; max: number }; min: number; max: number };
  thresholds: Record<string, number>;
  evolution: {
    budget: SourceBudget;
    adaptEvery: number;
    fsaEvery: number;
    fsaThreshold: number;
    fsaBanTtlIters: number;
    variantCap: number;
    coldStartMin: number;
  };
  llm: { enabled: boolean; reviewSample: number; maxCallsPerHour: number; timeoutS: number };
  workUnitLimit: number;
  enabled: boolean;
  maxRuns: number;
}

export interface DiscoveryVocabulary {
  fields: { key: string; cn: string; dimension: string }[];
  operators: { key: string; cn: string; arity: number; windowed: boolean }[];
  mechanisms: {
    key: string;
    cn: string;
    hint: string;
    fields: string[];
    operators: string[];
    prototypes: string[];
    proxyNote: string;
  }[];
  note: string;
}

export interface DiscoveryStartBody {
  instId: string;
  bar: string;
  label?: string;
  windowBars: number;
  axes: DiscoveryAxis[];
  horizon: number;
  candidatesPerIteration: number;
  iterationIntervalS: number;
  dataRefreshS: number;
  windowMode: "rolling" | "fixed";
  maxIterations: number;
  maxRuntimeHours: number;
  maxKept: number;
  autoResume: boolean;
  autoPromote: boolean;
  llm: { enabled: boolean; reviewSample: number; maxCallsPerHour: number; timeoutS: number };
  thresholds: Record<string, number>;
  evolution: Record<string, unknown>;
  // 三条策略轴（§4.1）：全部可选，缺省即沿用后端默认。
  axisWeights?: Record<string, number>;
  templates?: string[];
  includeLibraryFactors?: boolean;
  comboSize?: { min: number; max: number };
  rotationSize?: { min: number; max: number };
  strategyThresholds?: Record<string, number>;
  maxKeptPerAxis?: number;
}

export type DiscoveryWsEvent =
  | { type: "state"; data: DiscoveryStateEvent }
  | { type: "iteration"; data: DiscoveryIterationRow }
  | { type: "candidate"; data: DiscoveryCandidateRow }
  | { type: "demoted"; data: { candidateId: number; reason: string } }
  | { type: "error"; data: { code: string; message: string } }
  | { type: "ping" }
  | { type: "done"; data: { status: DiscoveryStatus; summary?: Record<string, unknown> } };
