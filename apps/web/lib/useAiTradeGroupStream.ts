// AI 自动交易「分组」运行期流（ai-trade-multi-symbol-and-timeframe §4.2）：挂载于 page.tsx。
// 1) 挂载 / 切到实盘时 GET /ai-trade/status 重发现孤儿分组（renderer 刷新后后端会话仍在跑）；
// 2) 对分组每个成员各开一条 /ws/ai-trade/{sessionId}，事件按 instId 路由进 store.aiTradeGroup.members
//    （snapshot/status/trade/position/decision/summary/agent_trace 各归成员槽）。镜像 useLiveAutoGroupStream。
import { useEffect, useRef } from "react";
import {
  AI_TRADE_RECONCILE_FAST_MS,
  reconcileAiTradeLifecycle,
  type AiTradeReconcileSetters,
} from "./aiTrade";
import { aiTradeApi, aiTradeWS } from "./aiTradeApi";
import type {
  AiTradeDecision,
  AiTradeErrorEvent,
  AiTradeGroupRow,
  AiTradeGroupState,
  AiTradeLifecycleEvent,
  AiTradeRoundTrace,
  AiTradeSessionRow,
  AiTradeSnapshot,
  AiTradeStatusEvent,
  AiTradeTraceEvent,
} from "./aiTradeTypes";
import { useAppStore } from "./store";
import type { PaperPositionEvent, Trade } from "./types";
import type { WsEvent } from "./ws";

// 成员标识分隔符（instId 仅含字母数字与「-」、sessionId 为 hex，两者均不含「@@」，可安全拼接 / 拆分）。
const SEP = "@@";

export function groupStateFromStatus(row: AiTradeGroupRow): AiTradeGroupState {
  const members: AiTradeGroupState["members"] = {};
  for (const m of row.members) {
    members[m.instId] = {
      instId: m.instId,
      sessionId: m.sessionId,
      allocationPct: m.allocationPct,
      session: m,
      trades: [],
      position: null,
      // 冷却期与 position 平级：平仓后 position 恒 null，挂在它之下 chip 永远拿不到数据（§4.2）。
      profitGuardCooldown: null,
      decisions: m.lastDecision ? [m.lastDecision] : [],
      traces: [],
      summary: m.contextSummary ?? null,
    };
  }
  return {
    groupId: row.groupId,
    bar: row.bar,
    strategyIds: row.strategyIds,
    strategyNames: row.strategyNames,
    totalBudgetUsd: row.totalBudgetUsd,
    leverage: row.leverage,
    contextRefreshRounds: row.contextRefreshRounds,
    wakeIntervalMinutes: row.wakeIntervalMinutes,   // wake-interval：供面板显示「唤醒周期 N 分钟」
    // 交易周期契约（holding-horizon §3.4）：分组共享一份（R-12：模式是会话级契约而非标的属性）。
    // 逐轮快照走 status/decision 事件**整对象透传**进 member.session.horizon，与此处的静态契约分工。
    tradingHorizon: row.tradingHorizon ?? null,
    // 验收闭环三键（horizon-acceptance [P0-3]）：本函数是**逐键手抄**的映射，不补就永远拿不到
    // ——`groupStateFromStatus` 正是本仓库「静默吞新键」缺陷的那个函数（与整体 spread 的
    // `patchAiTradeMemberStatus` 不同源）。`?? null` 要求后端**恒下发**这三个键，才能把
    // 「本轮没数据」与「后端还没上线这个键」区分开。
    acceptance: row.acceptance ?? null,
    exits: row.exits ?? null,
    funding: row.funding ?? null,
    // 交易纪律预设（discipline-preset 轴 A/B）：同上——本函数是逐键手抄的映射，不补这一行，
    // 纪律条在「刷新页面后重发现」这条路径上就恒为空（而 WS status 那条通路是好的，故这个坑
    // 只在重载后复现，最难被发现）。`aiTradeDiscipline.test.ts` 有一条用例专门守它。
    discipline: row.discipline ?? null,
    // pause-resume D-7：这里此前**写死**成 "running"，于是暂停态在「刷新页面后重发现」这条路径上
    // 恒显示为运行中——最难被发现的那种坑。`?? "running"` 让老后端逐字回退。
    // lifecycle-truth L-1：`state` 优先吃后端**派生**的那个（`lifecycle.state`），其次
    // `group.state`（老后端 / 总闸关时它才是唯一可得的那个），最后才是 "running" 兜底。
    state: row.lifecycle?.state ?? row.state ?? "running",
    pauseReason: row.pauseReason ?? null,
    pausedTs: row.pausedTs ?? null,
    resumedCount: row.resumedCount ?? null,
    // 风险 R-8：**本函数是逐键手抄的映射**（这已经是第四条同款注释了）。不补这两行，
    // 「刷新页面后重发现」这条路径上生命周期证据恒缺席——最难发现的那类坑。
    // `?? null` 要求后端恒下发，才能把「本轮没数据」与「后端还没上线这个键」区分开。
    declaredState: row.declaredState ?? null,
    lifecycle: row.lifecycle ?? null,
    startedTs: row.createdTs,
    members,
  };
}

// 生命周期对账器的常态周期（lifecycle-truth F-2；上一轮它只是「验收卡轮询」）。
// 为什么是轮询而不是一条分组 WS：本仓库的 AI-trade WS 是**按成员会话**建的，加一条分组通道要动
// 会话管理与重连逻辑，代价远大于收益；而生命周期迁移是**分钟级**事件。
// 为什么必须有它：`group_status()` 的产物只经 `GET /ai-trade/status` 下发，而重发现那条路径在
// 同组时直接早退 ⇒ 不对账的话，后端侧发生的任何迁移（进程重启后的 `process_restart` 暂停、
// `quit-pause`、会话自终止、另一个窗口的操作）**页面不刷新就永远看不到**（D-3）。
const RECONCILE_POLL_MS = 15_000;

export function useAiTradeGroupStream() {
  const accountMode = useAppStore((s) => s.accountMode);
  const aiTradeGroup = useAppStore((s) => s.aiTradeGroup);
  const setAiTradeGroup = useAppStore((s) => s.setAiTradeGroup);
  const setAiTradeResumable = useAppStore((s) => s.setAiTradeResumable);
  const patchAiTradeMemberSession = useAppStore((s) => s.patchAiTradeMemberSession);
  const patchAiTradeMemberStatus = useAppStore((s) => s.patchAiTradeMemberStatus);
  const patchAiTradeGroupLifecycle = useAppStore((s) => s.patchAiTradeGroupLifecycle);
  const requestAiTradeReconcile = useAppStore((s) => s.requestAiTradeReconcile);
  const setAiTradeSyncState = useAppStore((s) => s.setAiTradeSyncState);
  const setAiTradeEnabled = useAppStore((s) => s.setAiTradeEnabled);
  const setAiTradeLifecycleNotice = useAppStore((s) => s.setAiTradeLifecycleNotice);
  const clearAiTradeRuntime = useAppStore((s) => s.clearAiTradeRuntime);
  const setAiTradeMemberTrades = useAppStore((s) => s.setAiTradeMemberTrades);
  const appendAiTradeMemberTrade = useAppStore((s) => s.appendAiTradeMemberTrade);
  const setAiTradeMemberPosition = useAppStore((s) => s.setAiTradeMemberPosition);
  const pushAiTradeMemberDecision = useAppStore((s) => s.pushAiTradeMemberDecision);
  const setAiTradeMemberDecisions = useAppStore((s) => s.setAiTradeMemberDecisions);
  const setAiTradeMemberTraces = useAppStore((s) => s.setAiTradeMemberTraces);
  const upsertAiTradeMemberTracePhase = useAppStore((s) => s.upsertAiTradeMemberTracePhase);
  const setAiTradeMemberSummary = useAppStore((s) => s.setAiTradeMemberSummary);

  // ---- 生命周期对账器（lifecycle-truth F-2，本轮最高杠杆的一处）----
  //
  // 上一轮这里是**两条** effect：一条只在挂载时重发现（同 groupId 直接早退），一条只 patch 验收
  // 三键。于是「后端侧发生的迁移」在页面上永远看不到（D-3），而那条已经在跑的轮询把回执里的
  // `group` / `resumable` 全丢了只取了三个数（D-4）。本轮把它们合并成一条**统一对账器**，
  // 零新增请求。
  //
  // [评审 R0-7] 门禁只留 `accountMode`，**必须去掉 `!groupId` 早退**：不去掉的话，判定表七行里
  // 「重发现」「冷暂停接管」「lifecycleUnknown」三行**永远不可达** —— 恰恰是「本地什么都没有、
  // 后端其实有东西」这个最需要自愈的态。第一行 `accountMode !== "live"` 保持不动：续跑卡与 AI
  // 面板**只在实盘账户模式渲染**（[P2-10]）。
  //
  // 宿主选**页面级 hook** 而不是 `AiTradeToggle`：组件会随面板可见性消失，而自愈能力不能随之
  // 消失；且对账**只能有一个 owner**（两处各按自己的时序覆盖同一份生命周期，正是 D-5「两处口径」
  // 的换皮复发）。`AiTradeToggle.statusQ` 的 `refetchInterval` 因此被撤掉。
  const reconcileUntil = useAppStore((s) => s.aiTradeReconcileUntil);
  const settersRef = useRef<AiTradeReconcileSetters | null>(null);
  settersRef.current = {
    getGroup: () => useAppStore.getState().aiTradeGroup,
    groupStateFromStatus,
    setAiTradeGroup,
    setAiTradeResumable,
    patchAiTradeGroupLifecycle,
    clearAiTradeRuntime,
    setAiTradeSyncState,
    setAiTradeEnabled,
    setAiTradeLifecycleNotice,
  };
  useEffect(() => {
    if (accountMode !== "live") return;
    let cancelled = false;
    const pull = () => {
      // 既有守卫：页面隐藏时不打请求（R-14：长期挂着的看板不该持续烧配额）。
      if (typeof document !== "undefined" && document.hidden) return;
      aiTradeApi
        .status()
        .then((res) => {
          if (cancelled || !settersRef.current) return;
          reconcileAiTradeLifecycle(res, settersRef.current);
        })
        // 连不上后端 **≠** 没有分组（L-7 的极性）：只置 `unknown`，什么都不清。
        .catch(() => {
          if (!cancelled) setAiTradeSyncState("unknown");
        });
    };
    pull();
    // 快节奏窗口（pause / resume / end 之后的 30 秒）：3s 收敛，其余 15s。
    const fast = !!reconcileUntil && reconcileUntil > Date.now();
    const timer = setInterval(pull, fast ? AI_TRADE_RECONCILE_FAST_MS : RECONCILE_POLL_MS);
    // 快窗口到点后必须**主动降回常态**：`aiTradeReconcileUntil` 只是一个时间戳，它过期
    // **不会**触发任何重渲染，本 effect 也就不会重跑 —— 不清掉的话 3 秒节奏会永久留下来，
    // 把 R-6 担心的那点压力从「动作后的 30 秒」变成「从此以后」。
    const downgrade = fast
      ? setTimeout(
          () => useAppStore.setState({ aiTradeReconcileUntil: null }),
          Math.max(0, (reconcileUntil as number) - Date.now()),
        )
      : undefined;
    // **转前台立刻补拉一次**：`document.hidden` 那道守卫会跳过隐藏期的所有 tick，不补这一次，
    // L-3 的「≤15 秒」在「切回标签页」这条最常见路径上要退化成「最坏 30 秒」。
    const onVisible = () => {
      if (typeof document !== "undefined" && !document.hidden) pull();
    };
    if (typeof document !== "undefined") {
      document.addEventListener("visibilitychange", onVisible);
    }
    return () => {
      cancelled = true;
      clearInterval(timer);
      if (downgrade !== undefined) clearTimeout(downgrade);
      if (typeof document !== "undefined") {
        document.removeEventListener("visibilitychange", onVisible);
      }
    };
  }, [accountMode, reconcileUntil, setAiTradeSyncState]);

  // 成员集合稳定键：成员增删 / 重启即重建全部 WS（值变才触发，避免每次渲染重连）。
  // 只由**有 sessionId** 的成员构成（pause-resume §3.9.4）：冷占位没有 sessionId，
  // 不过滤就会去连 `/ws/ai-trade/undefined`。
  const memberKey = aiTradeGroup
    ? Object.values(aiTradeGroup.members)
        .filter((m) => !!m.sessionId)
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
      const ws = aiTradeWS(sessionId, (event: WsEvent) =>
        routeMemberEvent(instId, event, {
          patchAiTradeMemberSession,
          patchAiTradeMemberStatus,
          setAiTradeMemberTrades,
          appendAiTradeMemberTrade,
          setAiTradeMemberPosition,
          pushAiTradeMemberDecision,
          setAiTradeMemberDecisions,
          setAiTradeMemberTraces,
          upsertAiTradeMemberTracePhase,
          setAiTradeMemberSummary,
          requestAiTradeReconcile,
          // F-4（修 D-9）：后端对不存在的会话回一帧 `{"type":"error","code":"NOT_FOUND"}` 后
          // 关闭，而 `ReconnectingWS` 会无限重连 —— 后端重启后页面停在「运行中」并静默重连风暴，
          // 没有任何一处出声。收到该码即**停止对它重连**，并立刻请一次对账定夺最终态。
          closeSession: () => ws.close(),
        }),
      );
      ws.connect();
      return ws;
    });
    return () => clients.forEach((c) => c.close());
  }, [
    memberKey,
    patchAiTradeMemberSession,
    patchAiTradeMemberStatus,
    setAiTradeMemberTrades,
    appendAiTradeMemberTrade,
    setAiTradeMemberPosition,
    pushAiTradeMemberDecision,
    setAiTradeMemberDecisions,
    setAiTradeMemberTraces,
    upsertAiTradeMemberTracePhase,
    setAiTradeMemberSummary,
    requestAiTradeReconcile,
  ]);
}

interface MemberSetters {
  patchAiTradeMemberSession: (instId: string, patch: Partial<AiTradeSessionRow>) => void;
  patchAiTradeMemberStatus: (instId: string, status: AiTradeStatusEvent) => void;
  setAiTradeMemberTrades: (instId: string, trades: Trade[]) => void;
  appendAiTradeMemberTrade: (instId: string, trade: Trade) => void;
  setAiTradeMemberPosition: (instId: string, pos: PaperPositionEvent | null) => void;
  pushAiTradeMemberDecision: (instId: string, decision: AiTradeDecision) => void;
  setAiTradeMemberDecisions: (instId: string, decisions: AiTradeDecision[]) => void;
  setAiTradeMemberTraces: (instId: string, traces: AiTradeRoundTrace[]) => void;
  upsertAiTradeMemberTracePhase: (instId: string, evt: AiTradeTraceEvent) => void;
  setAiTradeMemberSummary: (instId: string, summary: string | null) => void;
  /** 请一次即时对账（分组级结论仍由后端算，前端只负责把成员事实更新掉）。 */
  requestAiTradeReconcile: () => void;
  /** 停止对这条会话重连（`NOT_FOUND` 之后再连只会造成重连风暴）。 */
  closeSession: () => void;
}

// 单成员 WS 事件 → store.aiTradeGroup.members[instId]（事件协议与单会话同构，按 instId 路由）。
function routeMemberEvent(instId: string, event: WsEvent, setters: MemberSetters) {
  switch (event.type) {
    case "snapshot": {
      const snap = event.data as AiTradeSnapshot;
      setters.setAiTradeMemberTrades(instId, snap.trades ?? []);
      setters.setAiTradeMemberPosition(instId, (snap.position as PaperPositionEvent) ?? null);
      setters.setAiTradeMemberDecisions(instId, snap.decisions ?? []);
      setters.setAiTradeMemberTraces(instId, snap.agentTraces ?? []);
      setters.setAiTradeMemberSummary(instId, snap.contextSummary ?? null);
      if (snap.status) setters.patchAiTradeMemberStatus(instId, snap.status);
      return;
    }
    case "agent_trace":
      setters.upsertAiTradeMemberTracePhase(instId, event.data as AiTradeTraceEvent);
      return;
    case "trade":
      setters.appendAiTradeMemberTrade(instId, event.data as Trade);
      return;
    case "position":
      setters.setAiTradeMemberPosition(instId, event.data as PaperPositionEvent);
      return;
    case "status":
      setters.patchAiTradeMemberStatus(instId, event.data as AiTradeStatusEvent);
      return;
    case "decision": {
      const decision = event.data as AiTradeDecision;
      setters.pushAiTradeMemberDecision(instId, decision);
      setters.patchAiTradeMemberSession(instId, { lastDecision: decision });
      return;
    }
    case "summary": {
      const summary = (event.data as { summary: string }).summary;
      setters.setAiTradeMemberSummary(instId, summary);
      setters.patchAiTradeMemberSession(instId, { contextSummary: summary });
      return;
    }
    // F-3（修 D-10）：唯一一条**实时**的暂停 / 继续信号。上一轮后端推了、前端没有 case，
    // 于是它被丢在地上；本轮才第一次消费它。
    //
    // **本 case 不直接写分组级 `state`**：分组结论是后端 `derive_group_state` 的产物（L-1），
    // 前端只负责把成员事实更新掉并请后端重算一次。少了这条纪律，L-1 就在前端被重新破一次。
    case "lifecycle": {
      const evt = event.data as AiTradeLifecycleEvent;
      setters.patchAiTradeMemberSession(instId, {
        state: evt.action === "paused" ? "paused" : "running",
        pauseReason: evt.reason ?? null,
        pausedTs: evt.ts,
        lifecycle: evt.lifecycle ?? null,
      });
      setters.requestAiTradeReconcile();
      return;
    }
    // F-4（修 D-9）：`NOT_FOUND` ⇒ 停连 + 立即对账（由对账决定最终态）；其余错误码落
    // `member.session.message`，由既有状态行渲染 —— 一条静默的重连风暴是本轮要消灭的
    // 「没有任何一处出声」。
    case "error": {
      const err = event.data as AiTradeErrorEvent;
      if (err?.code === "NOT_FOUND") {
        setters.closeSession();
        setters.requestAiTradeReconcile();
        return;
      }
      // **绝不碰 `state`**：一条错误帧不是一次状态迁移，替它猜一个 `state` 就是新造一句谎话。
      if (err?.message) setters.patchAiTradeMemberSession(instId, { message: err.message });
      return;
    }
    // equity / candle：净值由面板派生，K 线由市场 WS 兜底。
  }
}
