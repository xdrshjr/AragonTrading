"use client";

import { useEffect, useRef, useState } from "react";
import { Button, Field, Input, Select } from "@/components/ui";
import { AXIS_LABEL_ZH, SOURCE_LABEL_ZH, TIER_LABEL_ZH } from "@/lib/experience";
import type { ExperienceFilters } from "@/lib/experienceTypes";

const TIERS: { key: string; label: string }[] = [
  { key: "", label: "全部" },
  { key: "iron", label: TIER_LABEL_ZH.iron },
  { key: "normal", label: TIER_LABEL_ZH.normal },
  { key: "candidate", label: TIER_LABEL_ZH.candidate },
  { key: "muted", label: "已沉降" },
];

const SOURCES: { key: string; label: string }[] = [
  { key: "", label: "全部" },
  { key: "stat", label: SOURCE_LABEL_ZH.stat },
  { key: "narrative", label: SOURCE_LABEL_ZH.narrative },
  { key: "manual", label: SOURCE_LABEL_ZH.manual },
  { key: "imported", label: SOURCE_LABEL_ZH.imported },
];

/**
 * 经验库工具栏：标的 / 周期 / 坐标轴 / 档位 / 来源 / 搜索。
 *
 * 搜索框 300ms 防抖：每敲一个字就打一次全量取数会让一个 4000 节点的库在输入时明显卡顿，
 * 而这个筛选本来就是「边想边缩小范围」的交互。
 */
export default function ExperienceToolbar({
  filters,
  symbols,
  bars,
  onChange,
}: {
  filters: ExperienceFilters;
  symbols: string[];
  bars: string[];
  onChange: (next: ExperienceFilters) => void;
}) {
  const [draft, setDraft] = useState(filters.q ?? "");
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  // 外部（如「清空筛选」）改了 q 时同步回输入框，否则输入框会显示一个已经不生效的旧词。
  useEffect(() => setDraft(filters.q ?? ""), [filters.q]);

  useEffect(() => () => {
    if (timer.current) clearTimeout(timer.current);
  }, []);

  const setSearch = (value: string) => {
    setDraft(value);
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => onChange({ ...filters, q: value || undefined }), 300);
  };

  const patch = (part: Partial<ExperienceFilters>) => onChange({ ...filters, ...part });

  return (
    <div className="flex flex-wrap items-end gap-3" data-testid="experience-toolbar">
      <Field label="标的" htmlFor="exp-inst" className="w-[180px]">
        <Select
          id="exp-inst"
          value={filters.instId ?? ""}
          onChange={(e) => patch({ instId: e.target.value || undefined })}
        >
          <option value="">全部标的</option>
          {symbols.map((symbol) => <option key={symbol} value={symbol}>{symbol}</option>)}
        </Select>
      </Field>

      <Field label="周期" htmlFor="exp-bar" className="w-[110px]">
        <Select
          id="exp-bar"
          value={filters.bar ?? ""}
          onChange={(e) => patch({ bar: e.target.value || undefined })}
        >
          <option value="">全部</option>
          {bars.map((bar) => <option key={bar} value={bar}>{bar}</option>)}
        </Select>
      </Field>

      <Field label="坐标轴" htmlFor="exp-axis" className="w-[130px]">
        <Select
          id="exp-axis"
          value={filters.axis ?? ""}
          onChange={(e) => patch({ axis: e.target.value || undefined })}
        >
          <option value="">全部轴</option>
          {Object.entries(AXIS_LABEL_ZH).map(([axis, label]) => (
            <option key={axis} value={axis}>{label}</option>
          ))}
        </Select>
      </Field>

      <Field label="档位" htmlFor="exp-tier" className="w-[120px]">
        <Select
          id="exp-tier"
          value={filters.tier ?? ""}
          onChange={(e) => patch({ tier: e.target.value || undefined })}
        >
          {TIERS.map((tier) => <option key={tier.key} value={tier.key}>{tier.label}</option>)}
        </Select>
      </Field>

      <Field label="来源" htmlFor="exp-source" className="w-[120px]">
        <Select
          id="exp-source"
          value={filters.source ?? ""}
          onChange={(e) => patch({ source: e.target.value || undefined })}
        >
          {SOURCES.map((row) => <option key={row.key} value={row.key}>{row.label}</option>)}
        </Select>
      </Field>

      <Field label="搜索" htmlFor="exp-q" className="min-w-[200px] flex-1">
        <Input
          id="exp-q"
          value={draft}
          placeholder="在陈述与长文里搜索…"
          onChange={(e) => setSearch(e.target.value)}
        />
      </Field>

      <Button
        variant="ghost"
        size="sm"
        onClick={() => onChange({})}
        aria-label="清空全部筛选"
      >
        清空筛选
      </Button>
    </div>
  );
}
