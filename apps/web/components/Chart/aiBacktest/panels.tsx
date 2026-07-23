// AI 回测结果面板组件（ai-auto-trading-backtest-analytics §2.5）：KPI 概览 / 扩展指标网格 / 基准对比。
// 抽到 sibling 文件避免 AiBacktestModal.tsx 破 800 行阈值（P1-5）；各组件 ≤50 行、复用设计 token。
import { fmtDuration } from "@/lib/backtestStats";
import type { AiBacktestBenchmark, AiBacktestExtendedMetrics } from "@/lib/aiBacktestTypes";
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
