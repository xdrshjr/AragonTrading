// 自选自动交易分组运行期流（watchlist-auto-trade-group §2.7）：挂载于 page.tsx。
// 1) 挂载 / 切到实盘时 GET /live-auto/group/status 重发现孤儿分组（renderer 刷新后后端会话仍在跑）；
// 2) 对分组每个成员各开一条 /ws/live-auto/{sessionId}，事件按 instId 路由进 store.liveAutoGroup.members。
import { useEffect } from "react";
import { liveAutoGroupApi, liveAutoWS } from "./liveAutoGroupApi";
import type { LiveAutoGroupRow, LiveAutoSnapshot, LiveAutoStatus } from "./liveAutoTypes";
import { type LiveAutoGroupState, useAppStore } from "./store";
import type { PaperPositionEvent, Trade } from "./types";
import type { WsEvent } from "./ws";

// 成员标识分隔符（instId 仅含字母数字与「-」、sessionId 为 hex，两者均不含「@@」，可安全拼接 / 拆分）。
const SEP = "@@";

function groupStateFromStatus(row: LiveAutoGroupRow): LiveAutoGroupState {
  const members: LiveAutoGroupState["members"] = {};
  for (const m of row.members) {
    members[m.instId] = {
      instId: m.instId,
      sessionId: m.sessionId,
      runId: m.runId,
      allocationPct: m.allocationPct,
      budgetUsd: m.budgetUsd,
      status: {
        state: m.state,
        sessionPnlUsd: m.sessionPnlUsd ?? 0,
        positionSide: m.positionSide ?? undefined,
        degraded: m.degraded ?? undefined,
        flattenFailed: m.flattenFailed ?? undefined,
      },
      position: null,
      trades: [],
    };
  }
  return {
    groupId: row.groupId,
    strategyId: row.strategyId,
    strategyName: row.strategyName,
    bar: row.bar,
    totalBudgetUsd: row.totalBudgetUsd,
    leverage: row.leverage,
    state: "running",
    startedTs: row.createdTs,
    members,
  };
}

export function useLiveAutoGroupStream() {
  const accountMode = useAppStore((s) => s.accountMode);
  const liveAutoGroup = useAppStore((s) => s.liveAutoGroup);
  const setLiveAutoGroup = useAppStore((s) => s.setLiveAutoGroup);
  const setLiveAutoMemberStatus = useAppStore((s) => s.setLiveAutoMemberStatus);
  const setLiveAutoMemberPosition = useAppStore((s) => s.setLiveAutoMemberPosition);
  const setLiveAutoMemberTrades = useAppStore((s) => s.setLiveAutoMemberTrades);
  const appendLiveAutoMemberTrade = useAppStore((s) => s.appendLiveAutoMemberTrade);

  // 重发现：实盘模式下拉活跃分组，重建瞬态运行期态（不覆盖已在跑的本地分组的成交流，仅在缺失时重建）。
  useEffect(() => {
    if (accountMode !== "live") return;
    let cancelled = false;
    liveAutoGroupApi
      .status()
      .then((res) => {
        if (cancelled || !res.group) return;
        const current = useAppStore.getState().liveAutoGroup;
        if (current?.groupId === res.group.groupId) return; // 已有同组本地态，避免覆盖成交流
        setLiveAutoGroup(groupStateFromStatus(res.group));
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [accountMode, setLiveAutoGroup]);

  // 成员集合稳定键：成员增删 / 重启即重建全部 WS（值变才触发，避免每次渲染重连）。
  const memberKey = liveAutoGroup
    ? Object.values(liveAutoGroup.members)
        .map((m) => `${m.instId}${SEP}${m.sessionId}`)
        .sort()
        .join(",")
    : "";

  useEffect(() => {
    if (!memberKey) return;
    const pairs = memberKey.split(",").map((entry) => {
      const [instId, sessionId] = entry.split(SEP);
      return { instId, sessionId };
    });
    const clients = pairs.map(({ instId, sessionId }) => {
      const ws = liveAutoWS(sessionId, (event: WsEvent) =>
        routeMemberEvent(instId, event, {
          setLiveAutoMemberStatus,
          setLiveAutoMemberPosition,
          setLiveAutoMemberTrades,
          appendLiveAutoMemberTrade,
        }),
      );
      ws.connect();
      return ws;
    });
    return () => clients.forEach((c) => c.close());
  }, [
    memberKey,
    setLiveAutoMemberStatus,
    setLiveAutoMemberPosition,
    setLiveAutoMemberTrades,
    appendLiveAutoMemberTrade,
  ]);
}

interface MemberSetters {
  setLiveAutoMemberStatus: (instId: string, status: LiveAutoStatus | null) => void;
  setLiveAutoMemberPosition: (instId: string, position: PaperPositionEvent | null) => void;
  setLiveAutoMemberTrades: (instId: string, trades: Trade[]) => void;
  appendLiveAutoMemberTrade: (instId: string, trade: Trade) => void;
}

// 单成员 WS 事件 → store（事件协议与 paper / 单会话同构，按 instId 路由）。
function routeMemberEvent(instId: string, event: WsEvent, setters: MemberSetters) {
  if (event.type === "snapshot") {
    const snap = event.data as LiveAutoSnapshot;
    setters.setLiveAutoMemberTrades(instId, snap.trades ?? []);
    setters.setLiveAutoMemberPosition(instId, snap.position ?? null);
    if (snap.status) setters.setLiveAutoMemberStatus(instId, snap.status);
    return;
  }
  if (event.type === "trade") {
    setters.appendLiveAutoMemberTrade(instId, event.data as Trade);
    return;
  }
  if (event.type === "position") {
    setters.setLiveAutoMemberPosition(instId, event.data as PaperPositionEvent);
    return;
  }
  if (event.type === "status") {
    setters.setLiveAutoMemberStatus(instId, event.data as LiveAutoStatus);
  }
  // candle / error：K 线由市场 WS 兜底；error 由 status.message 渠道呈现，这里不重复处理。
}
