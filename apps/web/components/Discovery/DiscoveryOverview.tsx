"use client";

import { useEffect, useState } from "react";
import { Badge, Button, Card } from "@/components/ui";
import {
  STATUS_CN,
  coverageEntries,
  formatDuration,
  keptRate,
  pauseReasonCn,
  runtimeSeconds,
} from "@/lib/discovery";
import { discoveryApi } from "@/lib/discoveryApi";
import { discoveryAssetApi } from "@/lib/discoveryAssetApi";
import type { DiscoveryAssetSummary } from "@/lib/discoveryAssetTypes";
import { HEALTH_STATUS_CN, HEALTH_STATUS_ORDER, isGuardStalled, relativeTime } from "@/lib/discoveryHealth";
import { shouldShowCoverage } from "@/lib/discoveryStrategy";
import type { DiscoveryVocabulary } from "@/lib/discoveryTypes";
import { useAppStore } from "@/lib/store";
import { useDiscovery } from "./DiscoveryLab";
import DiscoveryLauncher from "./DiscoveryLauncher";

/**
 * 成果资产摘要卡（discovery-asset-ledger §2.10）。
 *
 * **关掉的功能必须在界面上看得出来**：守护被关闭 / 疑似停摆时，健康状态其实不会更新，
 * 而一屏「健康」的徽标会让用户以为一切正常——这正是本轮反复要防的那种「不报错的空转」。
 */
function AssetLedgerCard({ summary, onOpen }: {
  summary: DiscoveryAssetSummary | null;
  onOpen: () => void;
}) {
  if (!summary) {
    return (
      <Card className="p-3">
        <h4 className="mb-2 text-xs font-medium text-[var(--text)]">成果资产</h4>
        <p className="text-[11px] text-[var(--muted)]">台账摘要加载中…</p>
      </Card>
    );
  }
  const guard = summary.guard;
  const stalled = isGuardStalled(guard.lastTickTs, guard.tickS);
  return (
    <Card className="p-3">
      <div className="mb-2 flex items-center gap-2">
        <h4 className="text-xs font-medium text-[var(--text)]">成果资产</h4>
        <Badge tone="neutral">{summary.total}</Badge>
        <button type="button" onClick={onOpen} className="ml-auto text-[11px] text-[var(--accent-ink)] underline">
          查看台账
        </button>
      </div>
      <ul className="grid grid-cols-3 gap-x-3 gap-y-1 text-[11px] tabular">
        {HEALTH_STATUS_ORDER.map((status) => (
          <li key={status} className="flex items-center justify-between">
            <span className="text-[var(--muted)]">{HEALTH_STATUS_CN[status]}</span>
            <span className="text-[var(--text)]">{summary.byStatus?.[status] ?? 0}</span>
          </li>
        ))}
      </ul>
      <p className="mt-2 text-[11px] text-[var(--muted)]">
        近 24 小时体检 <b className="tabular text-[var(--text)]">{summary.checkedLast24h}</b> 次 ·
        近 7 天新增衰减 <b className="tabular text-[var(--text)]">{summary.newlyDecayedLast7d}</b> 条
      </p>
      {!guard.enabled && (
        <p className="mt-1 text-[11px] text-[var(--short-ink)]">
          体检守护已关闭，健康状态不会更新。
        </p>
      )}
      {guard.enabled && stalled && (
        <p className="mt-1 text-[11px] text-[var(--short-ink)]">
          守护似乎已停止：最近一次巡检在 {relativeTime(guard.lastTickTs)}。
        </p>
      )}
      {summary.backfilled > 0 && (
        <p className="mt-1 text-[11px] leading-relaxed text-[var(--muted)]">
          其中 {summary.backfilled} 条为本功能上线前回填。它们的首次结论基于
          <b>上线前挖到的成果</b>的第一次样本外体检，出现较多「已衰减」是正常的。
        </p>
      )}
    </Card>
  );
}

/** 机制族覆盖热力（自绘方格；0 覆盖用空心格提示「尚未探索」，绝不用颜色深浅骗人）。 */
function CoverageGrid({
  coverage,
  vocabulary,
}: {
  coverage: Record<string, number>;
  vocabulary: DiscoveryVocabulary | null;
}) {
  const entries = coverageEntries(coverage);
  if (entries.length === 0) {
    return <p className="text-[11px] text-[var(--muted)]">还没有入库因子，覆盖度尚无样本。</p>;
  }
  const max = Math.max(...entries.map((item) => item.count), 1);
  const cn = new Map((vocabulary?.mechanisms ?? []).map((item) => [item.key, item.cn]));
  return (
    <ul className="grid grid-cols-2 gap-1.5">
      {entries.map((item) => (
        <li key={item.key} className="flex items-center gap-1.5">
          <span
            aria-hidden="true"
            className="h-3 w-3 shrink-0 rounded-[3px] border border-[var(--border)]"
            style={{
              background:
                item.count > 0
                  ? `color-mix(in srgb, var(--accent) ${Math.round((item.count / max) * 80) + 20}%, transparent)`
                  : "transparent",
            }}
          />
          <span className="flex-1 truncate text-[11px] text-[var(--muted)]">
            {cn.get(item.key) ?? item.key}
          </span>
          <span className="tabular text-[11px] text-[var(--text)]">{item.count}</span>
        </li>
      ))}
    </ul>
  );
}

/** 总览：当前运行卡 + 机制覆盖热力 + 近期入库时间线 + 快捷新建。 */
export default function DiscoveryOverview() {
  const { runs, defaults, activeRunId, selectRun, refresh, goSection } = useDiscovery();
  const run = useAppStore((s) => s.discoveryRun);
  const [vocabulary, setVocabulary] = useState<DiscoveryVocabulary | null>(null);
  const [assetSummary, setAssetSummary] = useState<DiscoveryAssetSummary | null>(null);
  const [launcherOpen, setLauncherOpen] = useState(false);

  useEffect(() => {
    discoveryApi.vocabulary().then(setVocabulary).catch(() => setVocabulary(null));
    discoveryAssetApi.summary().then(setAssetSummary).catch(() => setAssetSummary(null));
  }, []);

  const summary = runs.find((row) => row.runId === activeRunId) ?? runs[0] ?? null;
  const rate = keptRate(run?.counters.tested ?? summary?.testedCount ?? 0,
                        run?.counters.kept ?? summary?.keptCount ?? 0);
  const recentKept = (run?.candidates ?? []).filter((row) => row.verdict === "kept").slice(-8).reverse();

  return (
    <div className="flex flex-col gap-3">
      <Card className="flex flex-col gap-2 p-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h3 className="text-sm font-semibold text-[var(--text)]">当前挖掘</h3>
          <Button variant="primary" size="sm" onClick={() => setLauncherOpen(true)}
                  disabled={!defaults?.enabled}>
            新建挖掘
          </Button>
        </div>

        {!summary ? (
          <p className="text-xs leading-relaxed text-[var(--muted)]">
            还没有挖掘任务。新建一个后，系统会按「生成 → 审查 → 验证 → 入库」四步持续迭代，
            并把每一轮的过程完整记录下来供回溯。
          </p>
        ) : (
          <>
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-sm text-[var(--text)]">{summary.label || `#${summary.runId}`}</span>
              <Badge tone={(run?.status ?? summary.status) === "running" ? "accent" : "neutral"}>
                {STATUS_CN[run?.status ?? summary.status] ?? summary.status}
              </Badge>
              {(run?.pauseReason || summary.pauseReason) && (
                <Badge tone="neutral">
                  {pauseReasonCn(run?.pauseReason || summary.pauseReason)}
                </Badge>
              )}
            </div>
            <dl className="grid grid-cols-3 gap-x-4 gap-y-1 text-[11px] tabular sm:grid-cols-5">
              {[
                ["运行时长", formatDuration(runtimeSeconds(summary.startedTs))],
                ["迭代", String(run?.iteration ?? summary.iteration)],
                ["已测", String(run?.counters.tested ?? summary.testedCount)],
                ["已入库", String(run?.counters.kept ?? summary.keptCount)],
                ["入库率", rate === null ? "—" : `${(rate * 100).toFixed(2)}%`],
              ].map(([label, value]) => (
                <div key={label}>
                  <dt className="text-[var(--muted)]">{label}</dt>
                  <dd className="text-[var(--text)]">{value}</dd>
                </div>
              ))}
            </dl>
            <div className="flex gap-2 pt-1">
              <Button variant="ghost" size="sm" onClick={() => goSection("monitor")}>
                实时监控
              </Button>
              <Button variant="ghost" size="sm" onClick={() => goSection("candidates")}>
                候选库
              </Button>
            </div>
          </>
        )}
      </Card>

      <div className="grid grid-cols-2 gap-3">
        {shouldShowCoverage(run?.axes) ? (
          <Card className="p-3">
            <h4 className="mb-2 text-xs font-medium text-[var(--text)]">机制族覆盖</h4>
            <CoverageGrid coverage={run?.mechanismCoverage ?? {}} vocabulary={vocabulary} />
          </Card>
        ) : (
          <Card className="p-3">
            <h4 className="mb-2 text-xs font-medium text-[var(--text)]">机制族覆盖</h4>
            {/* P2-11：两条组合轴的 mechanism 恒为单值，画出来就是一根 100% 的柱子——
                那是在假装有信息量。整块隐藏并说明理由，比画一张漂亮的假图诚实。 */}
            <p className="text-[11px] leading-relaxed text-[var(--muted)]">
              本任务只启用了组合类搜索轴，其机制标签恒为单一取值（因子组合 / 策略轮动），
              覆盖度图对它们没有信息量，故不展示。
            </p>
          </Card>
        )}

        <Card className="p-3">
          <h4 className="mb-2 text-xs font-medium text-[var(--text)]">近期入库</h4>
          {recentKept.length === 0 ? (
            <p className="text-[11px] text-[var(--muted)]">本次会话尚无新入库候选。</p>
          ) : (
            <ul className="flex flex-col gap-1.5">
              {recentKept.map((row) => (
                <li key={row.genomeHash} className="text-[11px]">
                  <p className="truncate font-mono text-[var(--text)]" title={row.exprText}>
                    {row.exprText}
                  </p>
                  <p className="truncate text-[var(--muted)]">{row.describe}</p>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>

      <AssetLedgerCard summary={assetSummary} onOpen={() => goSection("assets")} />

      <Card className="p-3">
        <h4 className="mb-1 text-xs font-medium text-[var(--text)]">关于本功能</h4>
        <p className="text-[11px] leading-relaxed text-[var(--muted)]">
          系统以「生成 → 审查 → 验证 → 入库」四步循环持续搜索因子表达式：生成层用变异 / 交叉 /
          参数扰动 / 随机探索 / LLM 机制引导五种操作产出候选，审查层用确定性规则挡掉退化与越界
          结构，验证层用**硬编码**的样本内外联合过滤裁决是否入库 —— 达标与否绝不由大模型说了算。
          所有展示的指标都是**历史样本统计**，不构成任何收益预期；样本不足时一律显示「—」而不外推。
        </p>
      </Card>

      <DiscoveryLauncher
        open={launcherOpen}
        defaults={defaults}
        onClose={() => setLauncherOpen(false)}
        onStarted={(runId) => {
          selectRun(runId);
          void refresh();
          goSection("monitor");
        }}
      />
    </div>
  );
}
