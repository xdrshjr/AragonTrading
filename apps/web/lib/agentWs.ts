// Agent WebSocket：复用 ReconnectingWS，浏览器直连 /api/ws/agent/{convId}（同 market/paper）。
import { ReconnectingWS, type WsEvent } from "./ws";

export function agentWS(
  conversationId: string,
  onEvent: (e: WsEvent) => void,
  onState?: (open: boolean) => void,
): ReconnectingWS {
  return new ReconnectingWS(
    `/api/ws/agent/${encodeURIComponent(conversationId)}`,
    onEvent,
    onState,
  );
}
