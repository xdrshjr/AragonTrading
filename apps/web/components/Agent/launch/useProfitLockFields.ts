"use client";

import { useMemo } from "react";
import { AI_TRADE_PROFIT_LOCK_DEFAULTS } from "@/lib/aiTradeProfitLock";
import type { AiTradeProfitLockRule } from "@/lib/aiTradeTypes";
import type { UseAiTradeLaunchState } from "./useAiTradeLaunchState";

/**
 * 收益锁字段的校验与提交体组装（ai-trade-profit-lock-and-entry-unblock C-2 · 评审 RV-P0-1）。
 *
 * 为什么是单独一个文件：`useAiTradeLaunchForm.ts` 已贴着 G-1 的 800 行红线，这里的校验与 payload
 * 放进去就越线。**本文件不得出现 `useEffect`**——表单复位只经由唯一的 `state.seed()`
 * （modal-revert 复发根因的成文教训）。
 */
export type ProfitLockField = "armCostMult" | "keepRatio" | "confirmSeconds";

export interface ProfitLockFieldsView {
  /** 首个字段级错误（中文，含字段名与合法区间）；无错 ⇒ null。 */
  error: string | null;
  fieldErrors: Partial<Record<ProfitLockField, string>>;
  /** enabled=false ⇒ `{enabled:false}`；否则只带用户改过的字段（空串 = 服务端默认）。 */
  payload: AiTradeProfitLockRule;
}

interface FieldSpec {
  key: ProfitLockField;
  label: string;
  min: number;
  max: number;
  minOpen?: boolean;
}

const FIELDS: FieldSpec[] = [
  { key: "armCostMult", label: "保本锁武装倍数", min: 1, max: 10 },
  { key: "keepRatio", label: "利润锁保留比例", min: 0, max: 0.95, minOpen: true },
  { key: "confirmSeconds", label: "越线确认秒数", min: 0, max: 60 },
];

function rangeText(spec: FieldSpec): string {
  return `${spec.minOpen ? "(" : "["}${spec.min}, ${spec.max}]`;
}

function parseField(raw: string, spec: FieldSpec): { value: number | null; error: string | null } {
  const text = raw.trim();
  if (!text) return { value: null, error: null };
  const n = Number(text);
  const below = spec.minOpen ? n <= spec.min : n < spec.min;
  if (!Number.isFinite(n) || below || n > spec.max) {
    return { value: null, error: `收益锁 · ${spec.label}须在 ${rangeText(spec)} 之间` };
  }
  return { value: n, error: null };
}

export function useProfitLockFields(state: UseAiTradeLaunchState): ProfitLockFieldsView {
  const { profitLockEnabled, lockArmCostRaw, lockKeepRatioRaw, lockConfirmRaw } = state;
  return useMemo(() => {
    if (!profitLockEnabled) return { error: null, fieldErrors: {}, payload: { enabled: false } };
    const raws: Record<ProfitLockField, string> = {
      armCostMult: lockArmCostRaw,
      keepRatio: lockKeepRatioRaw,
      confirmSeconds: lockConfirmRaw,
    };
    const fieldErrors: ProfitLockFieldsView["fieldErrors"] = {};
    const payload: AiTradeProfitLockRule = { enabled: true };
    for (const spec of FIELDS) {
      const parsed = parseField(raws[spec.key], spec);
      if (parsed.error) fieldErrors[spec.key] = parsed.error;
      else if (parsed.value != null) payload[spec.key] = parsed.value;
    }
    // 利润锁武装倍数不在表单上暴露（取服务端默认 3），故保本锁倍数不得越过它，否则服务端 422。
    const trail = AI_TRADE_PROFIT_LOCK_DEFAULTS.trailCostMult;
    if (!fieldErrors.armCostMult && payload.armCostMult != null && payload.armCostMult > trail) {
      fieldErrors.armCostMult = `收益锁 · 保本锁武装倍数须 ≤ 利润锁武装倍数 ${trail}`;
    }
    const error = FIELDS.map((spec) => fieldErrors[spec.key]).find(Boolean) ?? null;
    return { error, fieldErrors, payload };
  }, [profitLockEnabled, lockArmCostRaw, lockKeepRatioRaw, lockConfirmRaw]);
}
