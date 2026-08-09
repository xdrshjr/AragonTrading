// 交易周期模式预设表客户端（ai-trade-holding-horizon-modes §4.1）。
//
// **只读、不守 AI 总闸**：读预设表不下任何单，弹窗在总闸关闭时也要能把「有哪些模式」画出来。
// 数值与中文标签一律由后端下发——前端复制一份必然漂移（R-3 / G-10）。
import type { AiTradeHorizonProfilesResponse } from "./aiTradeTypes";

const BASE = "/api/py";

export const aiTradeHorizonApi = {
  /**
   * 取模式预设表。
   *
   * `bar` 决定 `estimatedCostBps` 的资金费期数口径（成本随执行周期变），故弹窗切换时间级别后应
   * 重新取一次——契约摘要第三行的「需 ≥N bps」必须与门禁真正判定的那个数同源。
   */
  profiles: async (bar: string): Promise<AiTradeHorizonProfilesResponse> => {
    const res = await fetch(`${BASE}/ai-trade/horizon-profiles?bar=${encodeURIComponent(bar)}`, {
      cache: "no-store",
    });
    if (!res.ok) throw new Error(`horizon profiles ${res.status}`);
    return (await res.json()) as AiTradeHorizonProfilesResponse;
  },
};
