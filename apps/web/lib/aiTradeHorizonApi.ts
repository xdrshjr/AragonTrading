// 交易周期模式预设表客户端（ai-trade-holding-horizon-modes §4.1）。
//
// **只读、不守 AI 总闸**：读预设表不下任何单，弹窗在总闸关闭时也要能把「有哪些模式」画出来。
// 数值与中文标签一律由后端下发——前端复制一份必然漂移（R-3 / G-10）。
import type {
  AiTradeDisciplinePresetsResponse,
  AiTradeHorizonProfilesResponse,
  AiTradeTradabilityResponse,
} from "./aiTradeTypes";

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

// 交易纪律预设的只读阈值表（discipline-preset 轴 A）。与上面同一条纪律：**数值与中文标签全部由后端
// 下发，前端一个都不复制**——一张用户按下启动键之前看的表，如果与真正生效的那组数分叉，比不给更坏。
export const aiTradeDisciplineApi = {
  presets: async (): Promise<AiTradeDisciplinePresetsResponse> => {
    const res = await fetch(`${BASE}/ai-trade/discipline-presets`, { cache: "no-store" });
    if (!res.ok) throw new Error(`discipline presets ${res.status}`);
    return (await res.json()) as AiTradeDisciplinePresetsResponse;
  },
};

// 可交易性预检（tradability-and-open-funnel 轴 A）。同一条纪律：**只读、不守总闸、判据与文案
// 全部由后端下发**。后端保证「取数失败也回 200 + `level:"unknown"`」，故这里的非 2xx 是真故障。
export const aiTradeTradabilityApi = {
  probe: async (
    instId: string,
    bar: string,
    preset?: string | null,
  ): Promise<AiTradeTradabilityResponse> => {
    const params = new URLSearchParams({ instId, bar });
    if (preset) params.set("preset", preset);
    const res = await fetch(`${BASE}/ai-trade/tradability?${params.toString()}`, {
      cache: "no-store",
    });
    if (!res.ok) throw new Error(`tradability ${res.status}`);
    return (await res.json()) as AiTradeTradabilityResponse;
  },
};
