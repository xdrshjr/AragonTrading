"use client";

import { useMemo } from "react";
import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseQueryResult,
} from "@tanstack/react-query";
import {
  evidencePolicyOption,
  type AiTradeEvidenceBackfillOut,
  type AiTradeEvidenceGateIn,
  type AiTradeEvidencePolicy,
  type AiTradeEvidenceResponse,
} from "@/lib/aiTradeEvidence";
import { aiTradeEvidenceApi } from "@/lib/aiTradeEvidenceApi";
import type { UseAiTradeLaunchState } from "./useAiTradeLaunchState";

/**
 * 实证准入的档位派生、提交体、证据预览与历史回填（ai-trade-evidence-gated-capital §3.5.3）。
 *
 * 为什么是单独一个文件：`useAiTradeLaunchForm.ts` 贴着 G-1 的 800 行红线，本特性在那里只许
 * 净增 4 行。**本文件不得出现 `useEffect`**——档位的复位只经由唯一的 `state.seed()`
 * （modal-revert 复发根因的成文教训）。
 *
 * AI 回测不装配本层（spec §1.3）：回测模式下提交体 / 风险档位 / 摘要一律为空，查询也不发。
 */
export interface EvidenceBackfillView {
  run: () => void;
  pending: boolean;
  result: AiTradeEvidenceBackfillOut | null;
  /** 失败文案（后端 `message` 原样）；无错 ⇒ `null`。 */
  error: string | null;
}

export interface EvidenceGateFieldsView {
  /** 本层是否作用于当前模式（仅实盘）。 */
  active: boolean;
  policy: AiTradeEvidencePolicy;
  setPolicy: (policy: AiTradeEvidencePolicy) => void;
  /** 实盘启动体的 `evidenceGate`；回测 ⇒ `undefined`（整键缺席）。 */
  payload: AiTradeEvidenceGateIn | undefined;
  /** 风险汇聚的入参；回测 ⇒ `null`（不产生任何实证准入风险项）。 */
  riskPolicy: AiTradeEvidencePolicy | null;
  /** 第 5 步摘要的档位短名；回测 ⇒ `null`。 */
  summaryLabel: string | null;
  instIds: string[];
  preview: UseQueryResult<AiTradeEvidenceResponse>;
  backfill: EvidenceBackfillView;
}

const EVIDENCE_QUERY_ROOT = ["aiTrade", "evidence"] as const;

function errorText(error: unknown): string | null {
  if (error == null) return null;
  return error instanceof Error ? error.message : String(error);
}

export function useEvidenceGateFields(
  state: UseAiTradeLaunchState,
  open: boolean,
): EvidenceGateFieldsView {
  const { mode, members, bar, evidencePolicy, setEvidencePolicy } = state;
  const active = mode === "live";
  const instIdsKey = members.map((m) => m.instId).join(",");
  const instIds = useMemo(
    () => (instIdsKey ? instIdsKey.split(",") : []),
    [instIdsKey],
  );
  const queryClient = useQueryClient();

  // 预览失败只在卡片里显示一行错误，**绝不**进步骤模型：证据读不到不是不能启动的理由。
  const preview = useQuery({
    queryKey: [...EVIDENCE_QUERY_ROOT, instIdsKey, bar, evidencePolicy],
    queryFn: () => aiTradeEvidenceApi.evidence(instIds, bar, evidencePolicy),
    enabled: open && active && instIds.length > 0,
    staleTime: 60_000,
    retry: false,
  });

  const backfillM = useMutation({
    mutationFn: () => aiTradeEvidenceApi.backfill({ instIds, bar }),
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: EVIDENCE_QUERY_ROOT }),
  });

  return {
    active,
    policy: evidencePolicy,
    setPolicy: setEvidencePolicy,
    payload: active ? { enabled: true, policy: evidencePolicy } : undefined,
    riskPolicy: active ? evidencePolicy : null,
    summaryLabel: active ? evidencePolicyOption(evidencePolicy).short : null,
    instIds,
    preview,
    backfill: {
      run: () => backfillM.mutate(),
      pending: backfillM.isPending,
      result: backfillM.data ?? null,
      error: errorText(backfillM.error),
    },
  };
}
