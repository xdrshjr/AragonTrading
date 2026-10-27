// 「实证准入」证据查询与历史回填的 REST 客户端（ai-trade-evidence-gated-capital §5.3）。
//
// 复用 `aiTradeApi.ts` 的 `request`：超时决策集中在 `lib/httpTimeout.ts`——回填登记在长路径表
// （`LONG_MS`），因为服务端要逐段拉历史 K 线、自带 240 s 截止；落 20 s 的 `FAST_MS` 会让界面
// 在后端还在回填时就报失败。
import { request } from "./aiTradeApi";
import type {
  AiTradeEvidenceBackfillIn,
  AiTradeEvidenceBackfillOut,
  AiTradeEvidencePolicy,
  AiTradeEvidenceResponse,
} from "./aiTradeEvidence";

export const aiTradeEvidenceApi = {
  /**
   * 当前证据（规则取服务端 settings 默认；`policy` 只影响 `gating` 派生，不影响统计）。
   *
   * @param instIds 向导里已选的标的。
   * @param bar 执行周期。
   * @param policy 向导里正在选的档位。
   */
  evidence: (instIds: readonly string[], bar: string, policy: AiTradeEvidencePolicy) => {
    const query = new URLSearchParams({ instIds: instIds.join(","), bar, policy });
    return request<AiTradeEvidenceResponse>(`/ai-trade/evidence?${query.toString()}`);
  },
  /**
   * 用历史成交回填影子样本。幂等（`(source, source_ref)` 唯一），`truncated=true` 时再点一次
   * 会从没处理的地方接着做。公共读整体失败 ⇒ 502 `AI_TRADE_EVIDENCE_BACKFILL_FETCH_FAILED`，
   * 由 `ApiRequestError.message` 带出后端文案。
   */
  backfill: (body: AiTradeEvidenceBackfillIn) =>
    request<AiTradeEvidenceBackfillOut>("/ai-trade/evidence/backfill", {
      method: "POST",
      body: JSON.stringify(body),
    }),
};
