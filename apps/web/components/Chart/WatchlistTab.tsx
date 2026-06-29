"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { accountApi } from "@/lib/accountApi";
import { ApiRequestError } from "@/lib/api";
import { isAutoTradable, resolveGroupGate, totalAllocation } from "@/lib/autoTrade";
import { liveAutoGroupApi } from "@/lib/liveAutoGroupApi";
import type { LiveAutoGroupStartResult } from "@/lib/liveAutoTypes";
import { type LiveAutoGroupState, useAppStore } from "@/lib/store";
import type { Instrument } from "@/lib/types";
import AutoTradeGroupConfirmModal, {
  type AutoTradeGroupConfirmOpts,
} from "./AutoTradeGroupConfirmModal";
import AutoTradePanel, { type AutoTradeMemberView } from "./AutoTradePanel";
import WatchOtherPanel from "./WatchOtherPanel";

// 自选页签上下分区容器（watchlist-auto-trade-group §2.7）：上 AutoTradePanel（自动交易，置顶）/
// 下 WatchOtherPanel（其他自选），默认各占 50%。承接拖拽编排、运行期锁、分组启停与确认弹窗。
interface WatchlistTabProps {
  rows: Instrument[]; // 已 resolve + 搜索过滤的自选行（含行情）
  active: string;
  onSelect: (instId: string) => void;
  livePrice?: number | null;
  changePct?: number | null;
}

// 由分组启动回执构建瞬态运行期态（各成员初始无持仓 / 无成交，待 WS 增量）。
function groupStateFromResult(res: LiveAutoGroupStartResult): LiveAutoGroupState {
  const members: LiveAutoGroupState["members"] = {};
  for (const m of res.members) {
    members[m.instId] = {
      instId: m.instId,
      sessionId: m.sessionId,
      runId: m.runId,
      allocationPct: m.allocationPct,
      budgetUsd: m.budgetUsd,
      status: { state: m.state, sessionPnlUsd: 0 },
      position: null,
      trades: [],
    };
  }
  return {
    groupId: res.groupId,
    strategyId: res.strategyId,
    strategyName: res.strategyName,
    bar: res.bar,
    totalBudgetUsd: res.totalBudgetUsd,
    leverage: res.leverage,
    state: "running",
    startedTs: Math.floor(Date.now() / 1000),
    members,
  };
}

function describePreflight(err: ApiRequestError): string {
  if (err.code === "LIVE_AUTO_GROUP_PREFLIGHT" && err.members?.length) {
    return err.members.map((m) => `${m.instId}：${m.message}`).join("；");
  }
  return err.message;
}

export default function WatchlistTab({
  rows,
  active,
  onSelect,
  livePrice,
  changePct,
}: WatchlistTabProps) {
  const qc = useQueryClient();
  const accountMode = useAppStore((s) => s.accountMode);
  const bar = useAppStore((s) => s.bar);
  const strategyId = useAppStore((s) => s.strategyId);
  const autoTradeMembers = useAppStore((s) => s.autoTradeMembers);
  const autoTradeAllocationMode = useAppStore((s) => s.autoTradeAllocationMode);
  const liveAutoGroup = useAppStore((s) => s.liveAutoGroup);
  const addAutoTradeMember = useAppStore((s) => s.addAutoTradeMember);
  const removeAutoTradeMember = useAppStore((s) => s.removeAutoTradeMember);
  const setAutoTradeAllocation = useAppStore((s) => s.setAutoTradeAllocation);
  const setAutoTradeAllocationMode = useAppStore((s) => s.setAutoTradeAllocationMode);
  const evenSplitAutoTradeMembers = useAppStore((s) => s.evenSplitAutoTradeMembers);
  const setLiveAutoGroup = useAppStore((s) => s.setLiveAutoGroup);
  const setLiveAutoMemberStatus = useAppStore((s) => s.setLiveAutoMemberStatus);
  const clearLiveAutoGroup = useAppStore((s) => s.clearLiveAutoGroup);
  const toggleWatchlist = useAppStore((s) => s.toggleWatchlist);
  const acknowledged = useAppStore((s) => s.liveAutoAcknowledged);
  const setAcknowledged = useAppStore((s) => s.setLiveAutoAcknowledged);

  const [modalOpen, setModalOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [dropError, setDropError] = useState<string | null>(null);
  const [dragOverZone, setDragOverZone] = useState<"auto" | "other" | null>(null);

  const accountsQ = useQuery({ queryKey: ["okx", "accounts"], queryFn: accountApi.accounts.list });
  const defaultAccount =
    accountsQ.data?.accounts.find((a) => a.id === accountsQ.data?.defaultId) ?? null;
  const hasReadyDefault = !!defaultAccount && defaultAccount.hasSecret && defaultAccount.hasPassphrase;

  const statusQ = useQuery({
    queryKey: ["liveAutoGroup", "status"],
    queryFn: liveAutoGroupApi.status,
    enabled: accountMode === "live",
    refetchInterval: 15_000,
  });
  const liveAutoEnabled = statusQ.data?.liveAutoTradingEnabled ?? false;

  const running = !!liveAutoGroup;
  const groupId = liveAutoGroup?.groupId ?? null;

  // 自选行拆为上区（自动交易成员，按成员顺序置顶）/ 下区（其他自选）。
  const { memberViews, otherRows } = useMemo(() => {
    const rowByInst = new Map(rows.map((r) => [r.instId, r]));
    const memberIds = new Set(autoTradeMembers.map((m) => m.instId));
    const views: AutoTradeMemberView[] = [];
    for (const m of autoTradeMembers) {
      const inst = rowByInst.get(m.instId);
      if (inst) {
        views.push({
          inst,
          allocationPct: m.allocationPct,
          runtime: liveAutoGroup?.members[m.instId] ?? null,
        });
      }
    }
    return { memberViews: views, otherRows: rows.filter((r) => !memberIds.has(r.instId)) };
  }, [rows, autoTradeMembers, liveAutoGroup]);

  const totalPct = totalAllocation(autoTradeMembers);
  const aggregatePnl = useMemo(() => {
    if (!liveAutoGroup) return null;
    return Object.values(liveAutoGroup.members).reduce(
      (sum, m) => sum + (m.status?.sessionPnlUsd ?? 0),
      0,
    );
  }, [liveAutoGroup]);

  const gateReason = resolveGroupGate({
    accountMode,
    hasReadyDefault,
    liveAutoEnabled,
    members: autoTradeMembers,
    running,
    mode: autoTradeAllocationMode,
  });

  const startM = useMutation({
    mutationFn: (opts: AutoTradeGroupConfirmOpts) =>
      liveAutoGroupApi.start({
        strategyId: opts.strategyId,
        bar,
        members: autoTradeMembers,
        allocationMode: autoTradeAllocationMode,
        totalCapitalUsd: opts.totalCapitalUsd,
        leverage: opts.leverage,
        takeProfitUsd: opts.takeProfitUsd,
        stopLossUsd: opts.stopLossUsd,
      }),
    onSuccess: (res) => {
      setLiveAutoGroup(groupStateFromResult(res));
      setModalOpen(false);
      qc.invalidateQueries({ queryKey: ["liveAutoGroup", "status"] });
    },
    onError: (e) =>
      setError(e instanceof ApiRequestError ? describePreflight(e) : "启动失败，请稍后重试"),
  });

  const stopM = useMutation({
    mutationFn: (id: string) => liveAutoGroupApi.stop(id),
    onSettled: () => {
      clearLiveAutoGroup();
      qc.invalidateQueries({ queryKey: ["liveAutoGroup", "status"] });
    },
  });

  const stopMemberM = useMutation({
    mutationFn: ({ id, instId }: { id: string; instId: string }) =>
      liveAutoGroupApi.stopMember(id, instId),
    onSuccess: (_res, vars) => {
      setLiveAutoMemberStatus(vars.instId, {
        ...(liveAutoGroup?.members[vars.instId]?.status ?? { sessionPnlUsd: 0 }),
        state: "stopped",
      });
      // 无运行中成员则整组清空（与后端「停后空组清理」对齐）。
      const stillRunning = Object.values(liveAutoGroup?.members ?? {}).some(
        (m) => m.instId !== vars.instId && m.status?.state !== "stopped",
      );
      if (!stillRunning) clearLiveAutoGroup();
      qc.invalidateQueries({ queryKey: ["liveAutoGroup", "status"] });
    },
  });

  const onToggle = (next: boolean) => {
    setError(null);
    if (next) {
      if (gateReason) return;
      setModalOpen(true);
    } else if (groupId) {
      stopM.mutate(groupId);
    }
  };

  // ---------- 拖拽编排（HTML5 DnD）----------
  const handleDragStart = (e: React.DragEvent, instId: string) => {
    e.dataTransfer.setData("text/plain", instId);
    e.dataTransfer.effectAllowed = "move";
  };
  const allowDrop = (zone: "auto" | "other") => (e: React.DragEvent) => {
    if (running) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = "move";
    setDragOverZone(zone);
  };
  const handleDropToAuto = (e: React.DragEvent) => {
    e.preventDefault();
    setDragOverZone(null);
    if (running) return;
    const instId = e.dataTransfer.getData("text/plain");
    if (!instId) return;
    if (!isAutoTradable(instId)) {
      setDropError("仅支持 USDT 本位永续 / 交割合约自动交易");
      return;
    }
    setDropError(null);
    addAutoTradeMember(instId);
  };
  const handleDropToOther = (e: React.DragEvent) => {
    e.preventDefault();
    setDragOverZone(null);
    if (running) return;
    const instId = e.dataTransfer.getData("text/plain");
    if (instId) removeAutoTradeMember(instId);
  };

  const handleAddAuto = (instId: string) => {
    if (!isAutoTradable(instId)) {
      setDropError("仅支持 USDT 本位永续 / 交割合约自动交易");
      return;
    }
    setDropError(null);
    addAutoTradeMember(instId);
  };

  return (
    <div className="flex h-full min-h-0 flex-col">
      <AutoTradePanel
        members={memberViews}
        active={active}
        livePrice={livePrice}
        changePct={changePct}
        totalPct={totalPct}
        allocationMode={autoTradeAllocationMode}
        running={running}
        submitting={startM.isPending || stopM.isPending}
        gateReason={gateReason}
        aggregatePnl={aggregatePnl}
        dropError={dropError}
        dragOver={dragOverZone === "auto"}
        onToggle={onToggle}
        onAllocationModeChange={setAutoTradeAllocationMode}
        onEvenSplit={evenSplitAutoTradeMembers}
        onSelect={onSelect}
        onAllocationChange={setAutoTradeAllocation}
        onRemove={removeAutoTradeMember}
        onStopMember={(instId) => groupId && stopMemberM.mutate({ id: groupId, instId })}
        onDragStartMember={handleDragStart}
        onDragOver={allowDrop("auto")}
        onDragLeave={() => setDragOverZone(null)}
        onDrop={handleDropToAuto}
      />
      <WatchOtherPanel
        rows={otherRows}
        active={active}
        livePrice={livePrice}
        changePct={changePct}
        locked={running}
        dragOver={dragOverZone === "other"}
        onSelect={onSelect}
        onToggleStar={toggleWatchlist}
        onAddAuto={handleAddAuto}
        onDragStart={handleDragStart}
        onDragOver={allowDrop("other")}
        onDragLeave={() => setDragOverZone(null)}
        onDrop={handleDropToOther}
      />

      <AutoTradeGroupConfirmModal
        open={modalOpen}
        onClose={() => setModalOpen(false)}
        onConfirm={(opts) => startM.mutate(opts)}
        submitting={startM.isPending}
        acknowledged={acknowledged}
        onAcknowledge={setAcknowledged}
        members={autoTradeMembers}
        mode={autoTradeAllocationMode}
        bar={bar}
        defaultStrategyId={strategyId}
        error={error}
      />
    </div>
  );
}
