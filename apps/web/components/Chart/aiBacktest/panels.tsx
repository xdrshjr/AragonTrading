// AI 回测结果面板组件（ai-auto-trading-backtest-analytics §2.5）：KPI 概览 / 扩展指标网格 / 基准对比。
// 抽到 sibling 文件避免 AiBacktestModal.tsx 破 800 行阈值（P1-5）；各组件 ≤50 行、复用设计 token。
import ExitSourceCard from "@/components/Agent/aiTradeStats/ExitSourceCard";
import HorizonAcceptanceCard from "@/components/Agent/aiTradeStats/HorizonAcceptanceCard";
import { turnoverVetoLine } from "@/lib/aiTradeHorizon";
import { fmtDuration } from "@/lib/backtestStats";
import type {
  AiBacktestAiStats,
  AiBacktestBenchmark,
  AiBacktestExtendedMetrics,
} from "@/lib/aiBacktestTypes";
import { colorForReturn, fmtMoney, fmtNum, fmtPct } from "@/lib/format";
import type { Metrics } from "@/lib/types";

interface Kpi {
  label: string;
  value: string;
  color: string;
  hint?: string;
}

/** KPI 概览：大字号五连（总收益 / 超额 α（有基准时）/ 夏普 / 最大回撤 / 胜率）——一眼看懂的顶层视觉锚。 */
export function KPIHero({ metrics, benchmark }: { metrics: Metrics; benchmark?: AiBacktestBenchmark }) {
  const items: Kpi[] = [
    { label: "总收益", value: fmtPct(metrics.totalReturn), color: colorForReturn(metrics.totalReturn) },
    ...(benchmark
      ? [{
          label: "超额 α",
          value: fmtPct(benchmark.alpha),
          color: colorForReturn(benchmark.alpha),
          hint: "策略总收益 − 买入持有总收益（跑赢「什么都不做」的幅度）",
        }]
      : []),
    { label: "夏普比率", value: fmtNum(metrics.sharpe), color: colorForReturn(metrics.sharpe) },
    { label: "最大回撤", value: fmtPct(metrics.maxDrawdown), color: colorForReturn(metrics.maxDrawdown) },
    { label: "胜率", value: fmtPct(metrics.winRate), color: "var(--text)" },
  ];
  return (
    <div className="grid grid-cols-2 gap-2 sm:grid-cols-4 md:grid-cols-5">
      {items.map((it) => (
        <div
          key={it.label}
          title={it.hint}
          className="rounded-[12px] border border-[var(--border)] bg-[var(--surface-2)] px-3 py-2.5"
        >
          <div className="text-[10px] uppercase tracking-wide text-[var(--muted)]">{it.label}</div>
          <div className="tabular mt-0.5 text-xl font-semibold" style={{ color: it.color }}>
            {it.value}
          </div>
        </div>
      ))}
    </div>
  );
}

// 扩展指标条目（label / value / 释义 tooltip / 可选着色）。
function extendedItems(ext: AiBacktestExtendedMetrics): Kpi[] {
  const N = "var(--text)";
  return [
    { label: "年化收益", value: fmtPct(ext.annualizedReturn), color: colorForReturn(ext.annualizedReturn), hint: "CAGR：按年化的复合收益率" },
    { label: "年化波动", value: fmtPct(ext.annualizedVolatility), color: N, hint: "逐根收益率的年化标准差（样本方差 ddof=1）" },
    { label: "索提诺", value: fmtNum(ext.sortino), color: colorForReturn(ext.sortino), hint: "下行风险调整收益：分母仅计下行波动" },
    { label: "卡玛", value: fmtNum(ext.calmar), color: colorForReturn(ext.calmar), hint: "年化收益 ÷ 最大回撤幅度" },
    { label: "最长水下", value: fmtDuration(ext.maxDrawdownSecs), color: N, hint: "权益连续处于回撤中的最长时长" },
    { label: "在场占比", value: fmtPct(ext.timeInMarketPct), color: N, hint: "持仓根数 ÷ 总根数" },
    { label: "盈亏比·赔率", value: fmtNum(ext.payoffRatio), color: N, hint: "平均盈利 ÷ 平均亏损（赔率，区别于「盈利因子」＝毛盈利/毛亏损）" },
    { label: "平均盈利", value: fmtMoney(ext.avgWin), color: "var(--long)", hint: "平仓盈利笔的平均已实现盈亏" },
    { label: "平均亏损", value: fmtMoney(ext.avgLoss), color: "var(--short)", hint: "平仓亏损笔的平均已实现亏损（绝对值）" },
    { label: "单笔最大盈利", value: fmtMoney(ext.largestWin), color: "var(--long)" },
    { label: "单笔最大亏损", value: fmtMoney(ext.largestLoss), color: "var(--short)" },
    { label: "最大连胜", value: `${ext.maxConsecutiveWins} 笔`, color: N },
    { label: "最大连亏", value: `${ext.maxConsecutiveLosses} 笔`, color: N },
    { label: "平均持仓", value: `${fmtNum(ext.avgHoldBars)} 根`, color: N, hint: "每笔开→平的平均持仓 K 线数" },
    { label: "交易频率", value: `${fmtNum(ext.tradesPerWeek)} 笔/周`, color: N },
  ];
}

/** 扩展指标网格（B1）：进阶风险/质量指标，各带释义 tooltip；旧版结果缺失时上层优雅隐藏。 */
export function ExtendedMetricsGrid({ ext }: { ext: AiBacktestExtendedMetrics }) {
  return (
    <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 md:grid-cols-5">
      {extendedItems(ext).map((it) => (
        <div
          key={it.label}
          title={it.hint}
          className="rounded-[10px] border border-[var(--border)] bg-[var(--surface-2)] px-3 py-2"
        >
          <div className="text-[10px] text-[var(--muted)]">{it.label}</div>
          <div className="tabular text-sm font-semibold" style={{ color: it.color }}>{it.value}</div>
        </div>
      ))}
    </div>
  );
}

/** 基准对比卡（B2）：策略 vs 买入持有的收益/回撤/夏普并排 + 超额 α 高亮；无基准时上层隐藏。 */
export function BenchmarkCompareCard({ metrics, benchmark }: { metrics: Metrics; benchmark: AiBacktestBenchmark }) {
  const rows = [
    { label: "总收益", ai: fmtPct(metrics.totalReturn), bench: fmtPct(benchmark.totalReturn) },
    { label: "最大回撤", ai: fmtPct(metrics.maxDrawdown), bench: fmtPct(benchmark.maxDrawdown) },
    { label: "夏普比率", ai: fmtNum(metrics.sharpe), bench: fmtNum(benchmark.sharpe) },
  ];
  return (
    <div className="rounded-[12px] border border-[var(--border)] bg-[var(--surface-2)] p-3">
      <div className="mb-2 flex items-center justify-between">
        <span className="text-[11px] font-semibold text-[var(--text)]">策略 vs 买入持有</span>
        <span className="tabular text-[11px]" style={{ color: colorForReturn(benchmark.alpha) }}>
          超额 α {fmtPct(benchmark.alpha)}
        </span>
      </div>
      <div className="grid grid-cols-3 gap-y-1 text-[11px]">
        <span className="text-[var(--muted)]">指标</span>
        <span className="text-right font-medium text-[var(--text)]">策略</span>
        <span className="text-right text-[var(--muted)]">买入持有</span>
        {rows.map((r) => (
          <FragmentRow key={r.label} label={r.label} ai={r.ai} bench={r.bench} />
        ))}
      </div>
    </div>
  );
}

function FragmentRow({ label, ai, bench }: { label: string; ai: string; bench: string }) {
  return (
    <>
      <span className="text-[var(--muted)]">{label}</span>
      <span className="tabular text-right font-medium text-[var(--text)]">{ai}</span>
      <span className="tabular text-right text-[var(--muted)]">{bench}</span>
    </>
  );
}

// 回测保护层免责全文（adaptive §2.3.5）：与后端 backtest.py 的 _PROFIT_GUARD_BACKTEST_NOTE **同源文案**。
// 回测接入保护层最大的风险恰恰是「曲线变好看 ⇒ 用户高估实盘」（R-4），故这段必须显示、必须完整，
// 且**不得**在 UI 上把回测的保护触发说成「实盘会这样」。
const PROFIT_GUARD_BACKTEST_CAVEATS = [
  "同一根内的新高不会立即抬高移动止盈线（先判回撤、后推峰值）",
  "跳空穿越按开盘价成交",
  "回测按根推进，实盘每 5 秒盯市一次，触发时机必然更早、更细",
  "回测无部分成交、无挂单撤改、无交易所降级态",
  "开仓当根不判回撤；冷却秒数短于一根 K 线时几乎恒于次根即已过期——回测无法验证冷却期的实际效果",
];

/**
 * 回测的利润保护概览：触发次数 + 保守近似免责全文。
 *
 * 门控是 `enabled`（后端 `aiStats.profitGuardEnabled`）而**不是** `exits > 0`：未装配的回测也会下发
 * `profitGuardExits: 0`，只凭它渲染就会在一次根本没有保护层的回测上挂出这张卡片，等于宣称一条并不
 * 存在的防线（v1 终审 RV-1 的同一条教训）。老结果缺该键 ⇒ 恒不渲染（那时回测本就没有保护层）。
 */
export function ProfitGuardBacktestCard({ enabled, exits }: { enabled?: boolean; exits?: number }) {
  if (!enabled || exits == null) return null;
  return (
    <div className="rounded-[12px] border border-[var(--border)] bg-[var(--surface-2)] px-3 py-2.5">
      <div className="flex items-center justify-between gap-2">
        <span className="text-[11px] font-semibold text-[var(--text)]">利润保护（回测 · 保守近似）</span>
        <span className="tabular text-[11px] text-[var(--text)]" data-testid="bt-profit-guard-exits">
          触发平仓 {exits} 次
        </span>
      </div>
      <ul className="mt-1.5 flex flex-col gap-0.5 text-[10px] leading-relaxed text-[var(--muted)]">
        {PROFIT_GUARD_BACKTEST_CAVEATS.map((line) => (
          <li key={line}>· {line}</li>
        ))}
      </ul>
      <p className="mt-1 text-[10px] leading-relaxed text-[var(--muted)]">
        故回测的保护触发次数与落袋结果<strong className="text-[var(--text)]">相对实盘偏保守</strong>，
        不得据此推断实盘一定更好或更差。
      </p>
    </div>
  );
}

// 回测的资金费口径免责（horizon-acceptance §2.2.4 第 4/5 点）：与后端 `_funding_notes` 同向。
// **必须显示**——回测资金费不进权益曲线，用户会同时看到两个「收益」数字；不写清楚就是误导（R-5）。
const FUNDING_BACKTEST_CAVEATS = [
  "资金费按交易所历史费率曲线逐结算点计提，但未计入权益曲线与上方 metrics",
  "「资金费后净收益」是本卡自带的一次显式减法：= 净额 − 资金费",
  "取不到历史费率曲线时该行按「口径不可得」呈现，绝不按 0 计",
  "回测里给换手迟滞与 edge 门禁用的成本估算仍不含资金费（实盘含），此不对称留待下一轮重估",
];

/**
 * 回测结果面板的周期验收区（horizon-acceptance §2.5 落点之三）。
 *
 * 与运行期看板 / 历史详情**共用同一对组件、同一份 props 类型**——形状本来就同源，任何一处分叉
 * 都会立刻在 tsc 上报错。两卡是两个独立入参：未装配周期契约的对照回测上 `horizonAcceptance`
 * 为 null，而退出来源卡照常渲染（[P0-4]）。两卡都缺 ⇒ 整段不出现。
 */
export function AcceptanceBacktestSection({ aiStats }: { aiStats: AiBacktestAiStats }) {
  const card = aiStats.horizonAcceptance;
  const exits = aiStats.exits;
  if (!card && !exits) return null;
  return (
    <section className="flex flex-col gap-2" data-testid="bt-acceptance-section">
      <HorizonAcceptanceCard acceptance={card} />
      <ExitSourceCard exits={exits} />
      <TurnoverBacktestCard horizon={aiStats.horizon} />
      {card ? (
        <ul className="flex flex-col gap-0.5 rounded-[12px] border border-[var(--border)] bg-[var(--surface-2)] px-3 py-2 text-[10px] leading-relaxed text-[var(--muted)]">
          {FUNDING_BACKTEST_CAVEATS.map((line) => (
            <li key={line}>· {line}</li>
          ))}
        </ul>
      ) : null}
    </section>
  );
}

// 换手纪律的回测口径免责（turnover-budget §2.4.4）：与后端 `_turnover_backtest_notes` 同向。
// 第二句尤其重要——回测**没有交易所**，用户不能拿这份结论去为「兜底真的挂着」背书。
const TURNOVER_BACKTEST_CAVEATS = [
  "回测按根推进，再入场冷却的实际生效粒度是 ceil(冷却秒数 / 一根)；冷却短于一根 K 线时回测中恒不生效",
  "回测不装配交易所侧兜底核实（回测没有交易所），覆盖闸恒放行——本次结论不覆盖这一轴",
  "被换手闸拦下的反手会降级为平仓（退出腿保留、再入场腿否掉），故这类退出在退出来源分布里记为「Agent 主动平仓」而非「Agent 反手」",
];

/**
 * 换手纪律的两个拦截计数（turnover-budget §2.4.4）。
 *
 * `budgetVetoes` 是本轮最有信息量的新读数：**「模型想换手而被制度拦住」的次数**。它极高（例如
 * 超过每日往返数的 3 倍）说明模型持续想换手而被压着——那时正确的动作是**换一个更短的周期模式**，
 * 而不是调大预算；调大预算等于把用户自己选的周期契约悄悄改掉。
 *
 * 未装配（老回测 / 回测侧总闸关）⇒ 三个计数缺席 ⇒ **整块不渲染**（绝不挂一排 0）。
 */
function TurnoverBacktestCard({ horizon }: { horizon?: AiBacktestAiStats["horizon"] }) {
  const line = turnoverVetoLine(horizon ?? null);
  if (!line) return null;
  return (
    <div
      className="flex flex-col gap-1 rounded-[12px] border border-[var(--border)] bg-[var(--surface-2)] px-3 py-2"
      data-testid="bt-turnover-card"
    >
      <span className="text-[11px] text-[var(--text)]">{line}</span>
      <ul className="flex flex-col gap-0.5 text-[10px] leading-relaxed text-[var(--muted)]">
        {TURNOVER_BACKTEST_CAVEATS.map((item) => (
          <li key={item}>· {item}</li>
        ))}
      </ul>
    </div>
  );
}
