// OKX 链路健康的 wire 类型（okx-link-resilience §5.2）。
// 端点**不声明** `response_model`，故这里的键与后端 `link_state.snapshot()` 一一对应。

export type OkxLinkState = "up" | "idle" | "degraded" | "down" | "unknown";
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
