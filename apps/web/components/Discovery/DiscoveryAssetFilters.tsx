"use client";

import { Button, Select } from "@/components/ui";
import { AXIS_SHORT_CN } from "@/lib/discoveryStrategy";
import { HEALTH_STATUS_CN, HEALTH_STATUS_ORDER } from "@/lib/discoveryHealth";
import type { DiscoveryAssetQuery } from "@/lib/discoveryAssetTypes";

const SORT_CN: Record<string, string> = {
  lastFound: "最近发现",
  firstFound: "首次发现",
  lastCheck: "最近体检",
  retention: "留存度",
  acceptStrength: "入库时试验数",
};

/**
 * 台账筛选条。
 *
 * 筛选状态**不进 store 持久化**（由父组件用 `useState` 持有）：下次打开时看到一个自己
 * 忘了设过的筛选，用户会以为「资产没了」——这类误读比少一个便利功能贵得多。
 */
export default function DiscoveryAssetFilters({
  query,
  onChange,
  onReset,
}: {
  query: DiscoveryAssetQuery;
  onChange: (patch: Partial<DiscoveryAssetQuery>) => void;
  onReset: () => void;
}) {
  return (
    <div className="flex flex-wrap items-center gap-2">
      <input
        type="search"
        value={query.q ?? ""}
        onChange={(event) => onChange({ q: event.target.value })}
        placeholder="搜索描述或表达式"
        aria-label="搜索成果资产"
        className="w-52 rounded-[10px] border border-[var(--border)] bg-[var(--surface)] px-2.5 py-1.5 text-xs text-[var(--text)] outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]"
      />

      <Select
        value={query.kind ?? ""}
        aria-label="搜索轴"
        onChange={(event) => onChange({ kind: event.target.value })}
      >
        <option value="">全部类型</option>
        {Object.entries(AXIS_SHORT_CN).map(([key, label]) => (
          <option key={key} value={key}>
            {label}
          </option>
        ))}
      </Select>

      <Select
        value={query.status ?? ""}
        aria-label="健康状态"
        onChange={(event) => onChange({ status: event.target.value })}
      >
        <option value="">全部健康状态</option>
        {HEALTH_STATUS_ORDER.map((status) => (
          <option key={status} value={status}>
            {HEALTH_STATUS_CN[status]}
          </option>
        ))}
      </Select>

      <Select
        value={query.promoted ?? ""}
        aria-label="入库状态"
        onChange={(event) => onChange({ promoted: event.target.value })}
      >
        <option value="">入库与否</option>
        <option value="1">已入库</option>
        <option value="0">未入库</option>
      </Select>

      <Select
        value={query.archived ?? "0"}
        aria-label="归档状态"
        onChange={(event) => onChange({ archived: event.target.value })}
      >
        <option value="0">未归档</option>
        <option value="1">已归档</option>
        <option value="">全部</option>
      </Select>

      <Select
        value={query.sort ?? "lastFound"}
        aria-label="排序"
        onChange={(event) =>
          onChange({ sort: event.target.value as DiscoveryAssetQuery["sort"] })
        }
      >
        {Object.entries(SORT_CN).map(([key, label]) => (
          <option key={key} value={key}>
            按{label}
          </option>
        ))}
      </Select>

      <Button variant="ghost" size="sm" onClick={onReset}>
        清空筛选
      </Button>
    </div>
  );
}
