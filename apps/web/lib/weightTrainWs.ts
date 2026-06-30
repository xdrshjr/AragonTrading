// 权重训练流 WS 订阅器（复用 ReconnectingWS，浏览器经 NEXT_PUBLIC_PY_WS_URL 直连 /api/ws/weight-train/{jobId}）。
// 连接即全量回放缓冲（含终态，绝不悬挂，R-06）；断线重连由 ReconnectingWS 自动续连并重放。
import type { TrainStreamEvent } from "./weightModelTypes";
import { ReconnectingWS, type WsEvent } from "./ws";

export function weightTrainWS(
  jobId: string,
  onEvent: (event: TrainStreamEvent) => void,
  onState?: (open: boolean) => void,
): ReconnectingWS {
  return new ReconnectingWS(
    `/api/ws/weight-train/${jobId}`,
    onEvent as (event: WsEvent) => void,
    onState,
  );
}
