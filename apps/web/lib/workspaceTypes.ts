// workspace 偏好持久化类型（watchlist-auto-trade-persistence §4，camelCase 与后端 wire 对齐）。
import type { AutoTradeAllocationMode, AutoTradeMember } from "./liveAutoTypes";

// 持久化到后端的 workspace 偏好：自选标的 + 自动交易成员（watchlist 有序子集）+ 分配模式。
export interface WorkspacePrefs {
  watchlist: string[];
  autoTradeMembers: AutoTradeMember[];
  autoTradeAllocationMode: AutoTradeAllocationMode;
}

// GET/PUT 响应：present 区分「从未保存」（前端 seed）与「保存为空」（前端 respect），§2.2。
export interface WorkspaceWatchlistOut extends WorkspacePrefs {
  present: boolean;
}

// PUT 入参（整文档 upsert）。
export type WorkspaceWatchlistIn = WorkspacePrefs;
