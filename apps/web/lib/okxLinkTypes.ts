// OKX 链路健康的 wire 类型（okx-link-resilience §5.2）。
// 端点**不声明** `response_model`，故这里的键与后端 `link_state.snapshot()` 一一对应。

export type OkxLinkState = "up" | "idle" | "degraded" | "down" | "unknown";

/**
 * 单条 WS 的连接状态机（后端 `ws_supervisor.WsState`）。
 *
 * 注意它与 `OkxLinkState` **不是**同一套词表：这里是「一条 socket 走到了哪一步」
 * （connecting → subscribing → live），而 `OkxLinkState` 是「这条通道健不健康」的聚合判定。
 * 两者故意不互转——真要把 WS 状态折进顶栏芯片，请显式写映射，别假定同名同义。
 */
export type OkxWsStreamState = "connecting" | "subscribing" | "live" | "degraded" | "down";

/**
 * 市场 WS 上的链路状态帧（后端 `ws_market.py` 的 `on_stream`）。与 K 线**共用同一条流**，
 * 每次建流必发。图表不消费它；顶栏链路芯片走 REST `/api/okx/connectivity`。
 * 之所以要在类型层建模：不建模就没人知道这条流上还跑着别的东西——白屏事故即由此而来
 * （docs/diagnoses/desktop-white-screen-market-ws-link-frame）。
 */
export interface MarketLinkEvent {
  /** 与 `OkxLinkChannel.key` 同一标识（如 `ws:business:BTC-USDT-SWAP:1H:chart`）。 */
  key: string;
  state: OkxWsStreamState;
  attempt: number;
  /** 本次断连持续时长，仅在回到 live 时有意义。 */
  downtimeS: number;
  detail: string;
}
export type OkxLinkKind = "restPublic" | "restPrivate" | "wsBusiness" | "wsPublic";
export type OkxCircuit = "closed" | "open" | "halfOpen";

export interface OkxLinkChannel {
  key: string;
  kind: OkxLinkKind;
  /** 通道中文名由**后端**下发（含标的名）：值域属于后端，前端只转发。 */
  label: string;
  state: OkxLinkState;
  lastOkTs: number | null;
  lastErrorTs: number | null;
  lastErrorCode: string | null;
  lastErrorMessage: string | null;
  /** EWMA(α=0.3) 之后四舍五入到整毫秒。 */
  latencyMs: number | null;
  consecutiveFailures: number;
  reconnects1h: number;
  resubscribes1h: number;
  gapRepairs1h: number;
  lastMessageTs: number | null;
  circuit: OkxCircuit;
  circuitOpenUntilTs: number | null;
}

export interface OkxConnectivity {
  ts: number;
  /** **只由行情面决定**（restPublic + 活跃 WS 通道）。账户面的问题不许拉红顶栏芯片。 */
  overall: OkxLinkState;
  /** 私有面单列：没配实盘 / 密钥填错 / IP 未白名单都会让它 down。 */
  accountState: OkxLinkState;
  summary: string;
  clockOffsetMs: number | null;
  clockSyncedTs: number | null;
  /** 已停用（用户换了标的 / 周期）的通道**不在其中**。 */
  channels: OkxLinkChannel[];
}
