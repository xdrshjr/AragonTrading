"use client";

import { Badge, Card } from "@/components/ui";
import {
  STATUS_CN,
  countdownSeconds,
  keptRate,
  keptRateSeries,
  pauseReasonCn,
} from "@/lib/discovery";
import { AXIS_SHORT_CN } from "@/lib/discoveryStrategy";
import { useAppStore } from "@/lib/store";
import DiscoveryFunnel from "./DiscoveryFunnel";
import DiscoverySourceBars from "./DiscoverySourceBars";
import DiscoveryTimeline from "./DiscoveryTimeline";
import { useDiscovery } from "./DiscoveryLab";

/** 入库率 sparkline（自绘 SVG）：样本不足的轮次留断点，绝不补 0 连成假曲线。 */
function Sparkline({ values }: { values: (number | null)[] }) {
  const points = values.filter((value): value is number => value !== null);
  if (points.length < 2) {
    return <span className="text-[11px] text-[var(--muted)]">样本不足</span>;
  }
  const max = Math.max(...points, 0.0001);
  const width = 160;
  const height = 28;
  const step = width / Math.max(1, values.length - 1);
  const path = values
    .map((value, index) =>
      value === null ? null : `${index * step},${height - (value / max) * height}`,
    )
    .filter(Boolean)
    .join(" L ");
  return (
    <svg width={width} height={height} role="img" aria-label="入库率趋势">
      <path d={`M ${path}`} fill="none" stroke="var(--accent)" strokeWidth="1.5" />
    </svg>
  );
}

/** 实时监控总装：漏斗 / 五维来源 / 入库率 / FSA 冻结 / LLM 轨迹 / 迭代时间线。 */
export default function DiscoveryMonitor() {
  const run = useAppStore((s) => s.discoveryRun);
  const { runs, activeRunId, goSection } = useDiscovery();
  const summary = runs.find((row) => row.runId === activeRunId) ?? null;

  if (!activeRunId || !summary) {
    return (
      <Card className="p-6 text-center">
        <p className="text-sm text-[var(--text)]">还没有选中挖掘任务</p>
        <button
          type="button"
          onClick={() => goSection("runs")}
          className="mt-2 text-xs text-[var(--accent-ink)] underline"
        >
          去挖掘任务列表选择或新建
        </button>
      </Card>
    );
  }

  const latest = run?.iterations.length ? run.iterations[run.iterations.length - 1] : null;
  const axes = run?.axes ?? [];
  const rate = keptRate(run?.counters.tested ?? 0, run?.counters.kept ?? 0);
  const countdown = countdownSeconds(run?.nextIterationTs ?? null);
  const llm = run?.llmUsage;

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="text-sm font-semibold text-[var(--text)]">
          {summary.label || `#${summary.runId}`}
        </h3>
        <Badge tone={run?.status === "running" ? "accent" : "neutral"}>
          {STATUS_CN[run?.status ?? summary.status] ?? summary.status}
        </Badge>
        {run?.rebuilding && <Badge tone="accent">正在重建候选池…</Badge>}
        {(run?.pauseReason || summary.pauseReason) && (
          <Badge tone="neutral">{pauseReasonCn(run?.pauseReason || summary.pauseReason)}</Badge>
        )}
        {run && !run.connected && (
          // 断线时显式说「重连中」，绝不假装还在跑（§2.13）。
          <Badge tone="short">实时连接已断开，重连中…</Badge>
        )}
      </div>

      {run?.lastError && (
        <p role="status" className="text-[11px] text-[var(--short-ink)]">
          {run.lastError.code}：{run.lastError.message}
        </p>
      )}

      <div className="grid grid-cols-4 gap-2">
        {[
          { label: "迭代", value: run?.iteration ?? summary.iteration },
          { label: "已测", value: run?.counters.tested ?? summary.testedCount },
          { label: "已入库", value: run?.counters.kept ?? summary.keptCount },
          {
            label: "下一轮",
            value: countdown === null ? "即将开始" : `${countdown}s`,
          },
        ].map((item) => (
          <Card key={item.label} className="p-2.5">
            <p className="text-[11px] text-[var(--muted)]">{item.label}</p>
            <p className="text-lg font-semibold tabular text-[var(--text)]">{item.value}</p>
          </Card>
        ))}
      </div>

      {/* 台账登记计数（discovery-asset-ledger §2.2.2）：登记是 fail-open 的，
          「算了但看不见」等于没做，所以这两个数必须有渲染落点。
          注意计数**落库晚一轮**（counters_json 在下一轮才写），刷新后短暂归零不是 bug。 */}
      <Card className="flex flex-wrap items-center gap-4 p-2.5">
        <span className="text-[11px] text-[var(--muted)]">
          已登记进成果资产台账{" "}
          <b className="tabular text-[var(--text)]" data-testid="ledger-registered">
            {run?.counters.ledgerRegistered ?? 0}
          </b>
        </span>
        {(run?.counters.ledgerErrors ?? 0) > 0 && (
          <span
            className="text-[11px] text-[var(--short-ink)]"
            data-testid="ledger-errors"
            title="本轮有成果未能登记进台账（登记失败不影响挖掘本身，但这些成果暂时不会出现在「成果资产」分区）"
          >
            登记失败 <b className="tabular">{run?.counters.ledgerErrors}</b> 次
          </span>
        )}
        <button
          type="button"
          onClick={() => goSection("assets")}
          className="text-[11px] text-[var(--accent-ink)] underline"
        >
          去成果资产分区
        </button>
      </Card>

      {axes.length > 1 && (
        <Card className="p-3">
          <h4 className="mb-2 text-xs font-medium text-[var(--text)]">各轴候选池</h4>
          <ul className="flex flex-wrap gap-2 text-[11px]">
            {axes.map((axis) => (
              <li
                key={axis}
                className="rounded-full bg-[var(--surface-2)] px-2 py-0.5 text-[var(--muted)]"
              >
                {AXIS_SHORT_CN[axis] ?? axis}
                <span className="ml-1 tabular text-[var(--text)]">
                  {run?.poolSizes?.[axis] ?? 0}
                </span>
                <span className="ml-1">已测 {run?.axisSnapshots?.[axis]?.tested ?? 0}</span>
              </li>
            ))}
          </ul>
        </Card>
      )}

      <div className="grid grid-cols-2 gap-3">
        <Card className="p-3">
          <h4 className="mb-2 text-xs font-medium text-[var(--text)]">最近一轮漏斗</h4>
          <DiscoveryFunnel row={latest} />
        </Card>

        <Card className="flex flex-col gap-3 p-3">
          <div>
            <h4 className="mb-2 text-xs font-medium text-[var(--text)]">五维来源分布</h4>
            <DiscoverySourceBars
              row={latest}
              budget={run?.budget ?? { mutate: 0, crossover: 0, perturb: 0, random: 0, llm: 0 }}
            />
          </div>
          <div>
            <h4 className="mb-1 text-xs font-medium text-[var(--text)]">
              入库率{" "}
              <span className="tabular text-[var(--muted)]">
                {rate === null ? "样本不足" : `${(rate * 100).toFixed(2)}%`}
              </span>
            </h4>
            <Sparkline values={keptRateSeries(run?.iterations ?? [])} />
          </div>
        </Card>
      </div>

      <div className="grid grid-cols-2 gap-3">
        <Card className="p-3">
          <h4 className="mb-2 text-xs font-medium text-[var(--text)]">被冻结的结构骨架（FSA）</h4>
          {(run?.fsaBans.length ?? 0) === 0 ? (
            <p className="text-[11px] text-[var(--muted)]">
              暂无冻结。当某个结构骨架在已入库因子里的占比超过 15%，它会被冻结一段时间，
              迫使搜索转向新方向。
            </p>
          ) : (
            <ul className="flex flex-wrap gap-1.5">
              {run?.fsaBans.map((ban) => (
                <li key={ban.skeleton}>
                  <span
                    className="inline-flex items-center gap-1 rounded-full bg-[var(--surface-2)] px-2 py-0.5 font-mono text-[10px] text-[var(--muted)]"
                    title={`占比 ${(ban.share * 100).toFixed(1)}%，冻结于第 ${ban.bannedAtIter} 轮`}
                  >
                    {ban.skeleton}
                    <span className="tabular">{(ban.share * 100).toFixed(0)}%</span>
                  </span>
                </li>
              ))}
            </ul>
          )}
        </Card>

        <Card className="p-3">
          <h4 className="mb-2 text-xs font-medium text-[var(--text)]">LLM 调用</h4>
          {!llm?.enabled ? (
            <p className="text-[11px] leading-relaxed text-[var(--muted)]">
              LLM 已降级或未配置，循环退化为**纯演化搜索**，入库标准不变。
            </p>
          ) : (
            <div className="flex flex-col gap-1 text-[11px] text-[var(--muted)]">
              <p className="tabular text-[var(--text)]">
                本小时 {llm.callsThisHour} / {llm.limit} 次
              </p>
              {llm.sinceProcessStart && <p>用量自**进程启动**起算，不跨重启累计。</p>}
              {latest?.llm.degraded && (
                <p className="text-[var(--short-ink)]">
                  最近一轮已降级：{latest.llm.degradedReason || "未知原因"}
                </p>
              )}
              {latest && latest.llm.calls > 0 && (
                <p>最近一轮 {latest.llm.calls} 次，耗时 {latest.llm.latencyMs} ms</p>
              )}
            </div>
          )}
        </Card>
      </div>

      <Card className="p-3">
        <h4 className="mb-2 text-xs font-medium text-[var(--text)]">迭代时间线</h4>
        <DiscoveryTimeline rows={run?.iterations ?? []} />
      </Card>
    </div>
  );
}
