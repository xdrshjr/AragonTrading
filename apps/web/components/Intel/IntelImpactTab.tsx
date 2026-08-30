"use client";

import { useEffect, useState } from "react";
import {
  IMPACT_CAVEAT,
  formatNetPnl,
  impactEmptyText,
  netPnlTone,
  type ImpactEmptyKind,
} from "@/lib/intelClosedLoop";
import { intelApi } from "@/lib/intelApi";
import type { AiTradeIntelSummary, IntelImpactReport, IntelItem } from "@/lib/intelTypes";
import IntelAttributionRail from "./IntelAttributionRail";
import IntelEfficacyBlock from "./IntelEfficacyBlock";
import IntelVerdictBadge from "./IntelVerdictBadge";

/**
 * 「影响力」页签（ai-trade-intel-closed-loop §3.7.3）——回答「情报到底有没有用」。
 *
 * 三条纪律：
 * - **「关联净额」四个字与顶部那句口径说明不是装饰**（评审留意事项 1）。它们是这份设计里唯一
 *   挡在「这条情报值 18 USDT」这个误读前面的东西，实现时不要为了排版把它折叠起来。
 * - **四种空态四段不同文案**：未开启引用 / 已开启但尚无引用 / 归因总闸关闭 / 情报层未装配。
 *   一个假装「没有数据」的面板会被读成「情报没用」。
 * - 「从未被引用」给的是**一句诚实的解释**而不是一个孤零零的数字：那些条目仍在库中并可能被
 *   注入，只是模型尚未在决策里点名引用过。
 */
export default function IntelImpactTab({
  instId,
  reloadToken = 0,
  summary,
  settingsCiteEnabled,
  settingsAttributionEnabled,
  onClearVerdict,
  onVerifyNow,
}: {
  instId: string;
  /** 每次写动作成功后由弹窗 +1 ⇒ 本页签重取。缺省 0 ⇒ 只在挂载时取一次（既有行为）。 */
  reloadToken?: number;
  summary: AiTradeIntelSummary | null;
  settingsCiteEnabled: boolean;
  settingsAttributionEnabled: boolean;
  onClearVerdict: (item: IntelItem) => void;
  onVerifyNow: (item: IntelItem) => void;
}) {
  const [impact, setImpact] = useState<IntelImpactReport | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let alive = true;
    intelApi
      .impact(instId)
      .then((res) => {
        if (alive) setImpact(res);
      })
      .catch((err) => {
        if (alive) setError(err instanceof Error ? err.message : "读取影响力数据失败");
      });
    return () => {
      alive = false;
    };
  }, [instId, reloadToken]);

  const empty = resolveEmptyKind({
    impact,
    summary,
    citeEnabled: settingsCiteEnabled,
    attributionEnabled: settingsAttributionEnabled,
  });

  return (
    <div className="flex flex-col gap-3 px-4 py-3" data-testid="intel-impact-tab">
      <p className="text-[10px] leading-relaxed text-[var(--muted)]">{IMPACT_CAVEAT}</p>
      {error && <p className="text-[11px] text-[var(--danger)]">{error}</p>}

      <div className="grid grid-cols-3 gap-2">
        <StatCard label="本标的引用次数" value={String(impact?.totals.citeCount ?? 0)} />
        <StatCard label="已结算往返" value={String(impact?.totals.settledCount ?? 0)} />
        <StatCard
          label="关联净额"
          value={formatNetPnl(impact?.totals.netPnlUsd ?? 0)}
          tone={netPnlTone(impact?.totals.netPnlUsd ?? 0)}
        />
      </div>

      {empty ? (
        <p
          data-testid="intel-impact-empty"
          className="rounded-[10px] border border-dashed border-[var(--border)] px-3 py-4 text-[11px] leading-relaxed text-[var(--muted)]"
        >
          {impactEmptyText(empty)}
        </p>
      ) : (
        <>
          <Board
            title="贡献榜"
            hint="按关联净额降序"
            items={impact?.top ?? []}
            onClearVerdict={onClearVerdict}
            onVerifyNow={onVerifyNow}
          />
          <Board
            title="拖累榜"
            hint="按关联净额升序（仅负值）"
            items={impact?.worst ?? []}
            onClearVerdict={onClearVerdict}
            onVerifyNow={onVerifyNow}
          />
        </>
      )}

      {/* 「战绩与信誉」块排在空态判定**之外**：即使一次引用都还没有，用户也该看到核查判定
          积累到了哪一步——那是本轮见效最快的一半（每个核查轮都在产出判定）。 */}
      <IntelEfficacyBlock efficacy={impact?.efficacy} />

      <p className="text-[10px] leading-relaxed text-[var(--muted)]">
        从未被引用：{impact?.neverCited ?? 0} 条。
        它们仍在情报库中并可能被注入，只是模型尚未在决策里点名引用过。
      </p>
    </div>
  );
}

/** 四种空态的判定（顺序是刻意的：越靠外层的总闸越先解释）。 */
function resolveEmptyKind({
  impact,
  summary,
  citeEnabled,
  attributionEnabled,
}: {
  impact: IntelImpactReport | null;
  summary: AiTradeIntelSummary | null;
  citeEnabled: boolean;
  attributionEnabled: boolean;
}): ImpactEmptyKind | null {
  if (summary && !summary.providerConfigured) return "not-armed";
  if (!citeEnabled) return "cite-off";
  if (!attributionEnabled) return "attribution-off";
  if (impact && impact.totals.citeCount === 0) return "no-citations";
  return null;
}

function StatCard({ label, value, tone }: { label: string; value: string; tone?: string }) {
  return (
    <div className="rounded-[10px] border border-[var(--border)] bg-[var(--bg)] px-2.5 py-2">
      <div className="text-[10px] text-[var(--muted)]">{label}</div>
      <div className="mt-0.5 text-sm font-medium tabular" style={tone ? { color: tone } : undefined}>
        {value}
      </div>
    </div>
  );
}

function Board({
  title,
  hint,
  items,
  onClearVerdict,
  onVerifyNow,
}: {
  title: string;
  hint: string;
  items: IntelItem[];
  onClearVerdict: (item: IntelItem) => void;
  onVerifyNow: (item: IntelItem) => void;
}) {
  if (!items.length) return null;
  return (
    <section className="flex flex-col gap-1">
      <h3 className="text-[11px] font-medium text-[var(--text)]">
        {title}
        <span className="ml-1.5 text-[10px] font-normal text-[var(--muted)]">{hint}</span>
      </h3>
      <ul className="flex flex-col gap-1">
        {items.map((item) => (
          <li
            key={item.id}
            className="flex items-center justify-between gap-2 rounded-[10px] border border-[var(--border)] bg-[var(--bg)] px-2.5 py-1.5"
          >
            <span className="min-w-0 flex-1">
              <span className="block truncate text-[11px] text-[var(--text)]">
                {item.headline}
              </span>
              <IntelAttributionRail item={item} />
            </span>
            <IntelVerdictBadge
              item={item}
              onClearVerdict={onClearVerdict}
              onVerifyNow={onVerifyNow}
            />
          </li>
        ))}
      </ul>
    </section>
  );
}
