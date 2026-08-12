// WebSocket 客户端（market + paper），自动重连 (spec §8.2 / §9.2)。
// 浏览器直连 Python WS：NEXT_PUBLIC_PY_WS_URL + /api/ws/*。

import type { MarketWsEvent } from "./types";

const WS_BASE = process.env.NEXT_PUBLIC_PY_WS_URL || "ws://localhost:18760";

export type WsEvent = { type: string; data: any };

// 浏览器 ↔ 本地后端这一跳的静默看门狗：`staleMs` 内一帧都没有即主动 `close()` 触发重连。
// 与后端对 OKX 那一跳同一条道理——**「不 FIN 不 RST，就是不再来数据」的连接不会自己报错**，
// 只表现为「图表不动了」。默认 45s 与后端 `okx_link_ws_silence_timeout_s` 同量级。
const DEFAULT_STALE_MS = 45_000;

export class ReconnectingWS {
  private url: string;
  private ws: WebSocket | null = null;
  private closedByUser = false;
  private backoff = 1000;
  private onEvent: (e: WsEvent) => void;
  private onState?: (open: boolean) => void;
  private staleMs: number;
  private staleTimer: ReturnType<typeof setTimeout> | null = null;

  // 构造签名与既有两个工厂函数**一字不变**（`staleMs` 是末位可选参数）。
  constructor(
    path: string,
    onEvent: (e: WsEvent) => void,
    onState?: (open: boolean) => void,
    staleMs: number = DEFAULT_STALE_MS,
  ) {
    this.url = `${WS_BASE}${path}`;
    this.onEvent = onEvent;
    this.onState = onState;
    this.staleMs = staleMs;
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
      this.armStaleWatchdog();
      this.onState?.(true);
    };
    this.ws.onmessage = (ev) => {
      this.armStaleWatchdog(); // 任何入站帧都算喂狗
      try {
        this.onEvent(JSON.parse(ev.data));
      } catch {
        /* ignore non-JSON */
      }
    };
    this.ws.onclose = () => {
      this.clearStaleWatchdog();
      this.onState?.(false);
      this.scheduleReconnect();
    };
    this.ws.onerror = () => {
      this.ws?.close();
    };
  }

  private scheduleReconnect() {
    if (this.closedByUser) return;
    // ±25% 抖动：多条流（K 线 / 分组内多标的）在同一次网络抖动里同时断开时，无抖动的
    // 1s→2s→4s 会让它们在同一毫秒一起重连，把一次抖动放大成一次自我同步的请求尖峰。
    const jittered = this.backoff * (0.75 + Math.random() * 0.5);
    setTimeout(() => this.open(), jittered);
    this.backoff = Math.min(this.backoff * 2, 15000);
  }

  private armStaleWatchdog() {
    this.clearStaleWatchdog();
    if (this.staleMs <= 0) return; // 0 = 关掉这个判据
    this.staleTimer = setTimeout(() => {
      // 主动关掉一条已经证明不再送数据的连接：等它自愈的期望收益为零。
      this.ws?.close();
    }, this.staleMs);
  }

  private clearStaleWatchdog() {
    if (this.staleTimer === null) return;
    clearTimeout(this.staleTimer);
    this.staleTimer = null;
  }

  send(data: unknown) {
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(typeof data === "string" ? data : JSON.stringify(data));
    }
  }

  close() {
    this.closedByUser = true;
    this.clearStaleWatchdog();
    this.ws?.close();
    this.ws = null;
  }
}

export function marketWS(
  instId: string,
  bar: string,
  onEvent: (e: MarketWsEvent) => void,
  onState?: (open: boolean) => void,
): ReconnectingWS {
  const params = new URLSearchParams({ instId, bar });
  return new ReconnectingWS(
    `/api/ws/market?${params.toString()}`,
    onEvent as (e: WsEvent) => void,
    onState,
  );
}

export function paperWS(
  sessionId: string,
  onEvent: (e: WsEvent) => void,
  onState?: (open: boolean) => void,
): ReconnectingWS {
  return new ReconnectingWS(`/api/ws/paper/${sessionId}`, onEvent, onState);
}
