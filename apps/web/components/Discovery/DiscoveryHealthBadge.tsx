"use client";

import { Badge } from "@/components/ui";
import {
  HEALTH_STATUS_HINT,
  HEALTH_STATUS_TONE,
  healthReasonsCn,
  healthStatusCn,
} from "@/lib/discoveryHealth";
import type { DiscoveryHealthStatus } from "@/lib/discoveryAssetTypes";

/**
 * 健康徽标——台账表格与详情抽屉**复用同一份实现**（总览卡不画徽标，但走同一份
 * `discoveryHealth.ts` 的中文与色板）。
 *
 * 各写一份的代价不是多几行代码，而是「后端新增一个状态时只改到其中一处」，
 * 其余各处静默回显裸英文（本仓库连续三次栽在这类分裂上）。
 */
export default function DiscoveryHealthBadge({
  status,
  reasons = [],
  className,
}: {
  status: DiscoveryHealthStatus;
  reasons?: string[];
  className?: string;
}) {
  const hint = HEALTH_STATUS_HINT[status] ?? "";
  const detail = reasons.length ? `${hint}（${healthReasonsCn(reasons)}）` : hint;
  return (
    <span title={detail} className={className}>
      <Badge tone={HEALTH_STATUS_TONE[status] ?? "neutral"}>{healthStatusCn(status)}</Badge>
    </span>
  );
}
