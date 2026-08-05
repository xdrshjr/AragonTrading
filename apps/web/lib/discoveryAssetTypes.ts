// 成果资产台账的 REST 类型（discovery-asset-ledger §四 / §5）。
// 与后端 camelCase 一一对应；本轮**无 WebSocket**（D-21），因此没有事件联合类型。

import type { DiscoveryAxis } from "./discoveryTypes";

/** 六态。`fresh` = 已登记未体检；`unknown` **不是**坏消息（D-26）。 */
export type DiscoveryHealthStatus =
  | "fresh"
  | "healthy"
  | "watch"
  | "decayed"
  | "unknown"
  | "error";

/** 判据码；`S0:*`（策略装配失败）会原样混进 `reasons`，故类型放宽为 string。 */
export type DiscoveryHealthCode = "H1" | "H2" | "H3" | "H4" | "H5" | "H6";

export interface DiscoveryAssetHealth {
  status: DiscoveryHealthStatus;
  /** `null` 是合法值（分母不可用），**绝不**当成 0 渲染。 */
  retention: number | null;
  consecutiveFail: number;
  lastCheckTs: number;
  nextCheckTs: number;
  reasons: string[];
  checksCount: number;
}

export interface DiscoveryAssetPromoted {
  kind: string;
  entityId: number;
  companionStrategyId: number | null;
}

/** 溯源一条；`runExists` **由服务端算**（前端不得自己拉 run 列表比对）。 */
export interface DiscoveryAssetSource {
  runId: number;
  candidateId: number;
  iterationIdx: number;
  foundTs: number;
  source: string;
  genomeHash: string;
  acceptSummary: Record<string, number | null>;
  runExists: boolean;
}

export interface DiscoveryAssetRow {
  id: number;
  assetKey: string;
  kind: DiscoveryAxis;
  label: string;
  instId: string;
  bar: string;
  horizon: number;
  direction: string;
  exprText: string;
  describe: string;
  mechanism: string;
  mechanismCn: string;
  firstFoundTs: number;
  lastFoundTs: number;
  sourceCount: number;
  /** 被合并的不同基因数；`>1` 时详情必须显式提示体检口径取首次那一组（评审 P1-7）。 */
  variantCount: number;
  backfilled: boolean;
  acceptMetrics: Record<string, Record<string, number | null>>;
  promoted: DiscoveryAssetPromoted | null;
  health: DiscoveryAssetHealth;
  archived: boolean;
  note: string;
}

export interface DiscoveryAssetCheckRow {
  id: number;
  checkedTs: number;
  status: DiscoveryHealthStatus;
  reasons: string[];
  retention: number | null;
  clipMode: string;
  error: string;
  windowStartTs: number;
  windowEndTs: number;
  freshStartTs: number;
  freshBars: number;
  warmupBars: number;
  metrics: {
    fresh?: Record<string, number | null>;
    history?: Record<string, number | null>;
  };
}

export interface DiscoveryAssetDetailRow extends DiscoveryAssetRow {
  genome: Record<string, unknown>;
  minedWindow: {
    startTs?: number;
    endTs?: number;
    isRatio?: number;
    windowBars?: number;
    /** 回填资产恒为 0（当时的 df 已不在，切点无法重算）——UI 必须如实标注。 */
    splitTs?: number;
  };
  acceptThresholds: Record<string, number>;
  trialsAtAccept: number;
  sources: DiscoveryAssetSource[];
  checks: DiscoveryAssetCheckRow[];
  checksTotal: number;
  checksPruned: number;
}

/** 守护自身状态；`running === false` 或 `lastTickTs` 过旧都必须在界面上看得出来（R-9）。 */
export interface DiscoveryGuardState {
  enabled: boolean;
  running: boolean;
  lastTickTs: number;
  lastError: string;
  checkedTotal: number;
  skippedByCap: number;
  dataFailures: number;
  assetLimitHits: number;
  inFlight: number[];
  intervalS: number;
  tickS: number;
  warmupBars: number;
  windowBars: number;
}

export interface DiscoveryAssetSummary {
  total: number;
  byStatus: Record<DiscoveryHealthStatus, number>;
  promoted: number;
  archived: number;
  backfilled: number;
  checkedLast24h: number;
  newlyDecayedLast7d: number;
  nextDueTs: number | null;
  guard: DiscoveryGuardState;
}

export type DiscoveryAssetSort =
  | "firstFound"
  | "lastFound"
  | "lastCheck"
  | "retention"
  | "acceptStrength";

export interface DiscoveryAssetQuery {
  kind?: string;
  status?: string;
  promoted?: string;
  archived?: string;
  instId?: string;
  bar?: string;
  mechanism?: string;
  q?: string;
  sort?: DiscoveryAssetSort;
  order?: "asc" | "desc";
  limit?: number;
  offset?: number;
}
