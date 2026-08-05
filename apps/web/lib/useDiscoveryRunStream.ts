// 挖掘任务实时流 hook（strategy-factor-discovery §2.13）：一 run 一连接，镜像
// useAiTradeGroupStream。事件经纯 reducer applyDiscoveryEvent 落 store（可单测）。
import { useEffect } from "react";
import { discoveryWS } from "./discoveryApi";
import { emptyRunState } from "./discovery";
import type { DiscoveryWsEvent } from "./discoveryTypes";
import { useAppStore } from "./store";
import type { WsEvent } from "./ws";

/**
 * 订阅某个挖掘任务的实时流。
 *
 * `runId` 为 null 时不建连接（未选中任务 / 已切走）。断线由 `ReconnectingWS` 指数退避重连，
 * 连接状态写入 `connected`，UI 据此显示明确的「重连中」而不是假装还在跑（§2.13）。
 */
export function useDiscoveryRunStream(runId: number | null): void {
  const setDiscoveryRun = useAppStore((s) => s.setDiscoveryRun);
  const applyDiscoveryWsEvent = useAppStore((s) => s.applyDiscoveryWsEvent);
  const setDiscoveryConnected = useAppStore((s) => s.setDiscoveryConnected);

  useEffect(() => {
    if (!runId) {
      setDiscoveryRun(null);
      return;
    }
    const current = useAppStore.getState().discoveryRun;
    if (current?.runId !== runId) setDiscoveryRun(emptyRunState(runId));
    const ws = discoveryWS(
      runId,
      (event: WsEvent) => {
        if (event.type === "ping") return;
        applyDiscoveryWsEvent(event as DiscoveryWsEvent);
      },
      (open: boolean) => setDiscoveryConnected(open),
    );
    ws.connect();
    return () => {
      ws.close();
      setDiscoveryConnected(false);
    };
  }, [runId, setDiscoveryRun, applyDiscoveryWsEvent, setDiscoveryConnected]);
}
