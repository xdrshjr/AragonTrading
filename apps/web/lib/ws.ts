// WebSocket 客户端（market + paper），自动重连 (spec §8.2 / §9.2)。
// 浏览器直连 Python WS：NEXT_PUBLIC_PY_WS_URL + /api/ws/*。

const WS_BASE = process.env.NEXT_PUBLIC_PY_WS_URL || "ws://localhost:18760";

export type WsEvent = { type: string; data: any };

export class ReconnectingWS {
  private url: string;
  private ws: WebSocket | null = null;
  private closedByUser = false;
  private backoff = 1000;
  private onEvent: (e: WsEvent) => void;
  private onState?: (open: boolean) => void;

  constructor(path: string, onEvent: (e: WsEvent) => void, onState?: (open: boolean) => void) {
    this.url = `${WS_BASE}${path}`;
    this.onEvent = onEvent;
    this.onState = onState;
  }

  connect() {
    this.closedByUser = false;
    this.open();
  }

  private open() {
    if (this.closedByUser) return;
    try {
      this.ws = new WebSocket(this.url);
    } catch {
      this.scheduleReconnect();
      return;
    }
    this.ws.onopen = () => {
      this.backoff = 1000;
      this.onState?.(true);
    };
    this.ws.onmessage = (ev) => {
      try {
        this.onEvent(JSON.parse(ev.data));
      } catch {
        /* ignore non-JSON */
      }
    };
    this.ws.onclose = () => {
      this.onState?.(false);
      this.scheduleReconnect();
    };
    this.ws.onerror = () => {
      this.ws?.close();
    };
  }

  private scheduleReconnect() {
    if (this.closedByUser) return;
    setTimeout(() => this.open(), this.backoff);
    this.backoff = Math.min(this.backoff * 2, 15000);
  }

  send(data: unknown) {
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(typeof data === "string" ? data : JSON.stringify(data));
    }
  }

  close() {
    this.closedByUser = true;
    this.ws?.close();
    this.ws = null;
  }
}

export function marketWS(
  instId: string,
  bar: string,
  onEvent: (e: WsEvent) => void,
  onState?: (open: boolean) => void,
): ReconnectingWS {
  const params = new URLSearchParams({ instId, bar });
  return new ReconnectingWS(`/api/ws/market?${params.toString()}`, onEvent, onState);
}

export function paperWS(
  sessionId: string,
  onEvent: (e: WsEvent) => void,
  onState?: (open: boolean) => void,
): ReconnectingWS {
  return new ReconnectingWS(`/api/ws/paper/${sessionId}`, onEvent, onState);
}
