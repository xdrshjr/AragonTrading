"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import PanelResizeHandle from "@/components/ui/PanelResizeHandle";
import { Modal, Tabs } from "@/components/ui";
import { journalApi } from "@/lib/journalApi";
import type {
  DecisionBrief,
  JournalEvent,
  JournalFillRow,
  JournalSession,
  JournalStats,
} from "@/lib/journalTypes";
import { useAppStore } from "@/lib/store";
import JournalDecisionDetail from "./JournalDecisionDetail";
import JournalDecisionTable from "./JournalDecisionTable";
import JournalEventTable from "./JournalEventTable";
import JournalExportDialog from "./JournalExportDialog";
import JournalFillTable from "./JournalFillTable";
import JournalHealthCard from "./JournalHealthCard";
import JournalToolbar from "./JournalToolbar";

/**
 * 交易日志管理台的视图外壳（ai-trade-audit-journal §2.6.1）。
 *
 * 工具条 + 三页签（决策轮次 / 成交与订单 / 系统事件）+ 双栏（列表 | 详情）+ 分栏拖拽。
 *
 * **双栏而不是弹窗**：日志浏览的核心动作是「在列表里连续上下走、右边跟着变」。`Modal` 会锁
 * `body` 滚动、抢焦点、并在每次切换时销毁重建，是列表浏览的反模式。
 *
 * `active` 为 false 时**一个请求都不发**——未打开过日志台的用户对既有请求路径零额外开销
 * （沿用 `FactorLab` / `DiscoveryLab` 的「首激活后常驻」范式）。
 */
const PAGE_SIZE = 50;
const MIN_LIST_WIDTH = 360;
const MAX_LIST_WIDTH = 1200;
const DEFAULT_LIST_WIDTH = 620;

const TABS = [
  { key: "decisions", label: "决策轮次" },
  { key: "fills", label: "成交与订单" },
  { key: "events", label: "系统事件" },
];

export default function JournalView({ active }: { active: boolean }) {
  const filters = useAppStore((s) => s.journalFilters);
  const setFilters = useAppStore((s) => s.setJournalFilters);

  const [tab, setTab] = useState("decisions");
  const [listWidth, setListWidth] = useState(DEFAULT_LIST_WIDTH);
  const [sessions, setSessions] = useState<JournalSession[]>([]);
  const [sessionsLoading, setSessionsLoading] = useState(false);
  const [stats, setStats] = useState<JournalStats | null>(null);

  const [decisions, setDecisions] = useState<DecisionBrief[]>([]);
  const [decisionCursor, setDecisionCursor] = useState<number | null>(null);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [fills, setFills] = useState<JournalFillRow[]>([]);
  const [fillCursor, setFillCursor] = useState<number | null>(null);
  const [events, setEvents] = useState<JournalEvent[]>([]);
  const [eventCursor, setEventCursor] = useState<number | null>(null);
  const [eventLevel, setEventLevel] = useState("");

  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [exportOpen, setExportOpen] = useState(false);
  const [healthOpen, setHealthOpen] = useState(false);
  const [reloadKey, setReloadKey] = useState(0);

  const refresh = useCallback(() => setReloadKey((k) => k + 1), []);

  // 会话列表与健康度：只在首次激活与手动刷新时拉，不做轮询——日志台是**事后**面，
  // 运行期的实时轨迹已由既有 `agent_trace` 承担（§N4）。
  useEffect(() => {
    if (!active) return;
    let cancelled = false;
    setSessionsLoading(true);
    Promise.allSettled([journalApi.sessions(), journalApi.stats()]).then(([s, st]) => {
      if (cancelled) return;
      if (s.status === "fulfilled") setSessions(s.value.items);
      if (st.status === "fulfilled") setStats(st.value);
      setSessionsLoading(false);
    });
    return () => {
      cancelled = true;
    };
  }, [active, reloadKey]);

  // 三页各自的首页拉取（过滤条件变化即从头来）。
  useEffect(() => {
    if (!active) return;
    let cancelled = false;
    setLoading(true);
    setError("");
    const load = async () => {
      if (tab === "decisions") {
        const page = await journalApi.decisions(filters, { limit: PAGE_SIZE });
        if (cancelled) return;
        setDecisions(page.items);
        setDecisionCursor(page.nextBeforeId);
        setSelectedId(page.items[0]?.id ?? null);
      } else if (tab === "fills") {
        const page = await journalApi.fills(filters, { limit: PAGE_SIZE });
        if (cancelled) return;
        setFills(page.items);
        setFillCursor(page.nextBeforeId);
      } else {
        const page = await journalApi.events(
          { groupId: filters.groupId, instId: filters.instId, level: eventLevel || undefined },
          { limit: PAGE_SIZE },
        );
        if (cancelled) return;
        setEvents(page.items);
        setEventCursor(page.nextBeforeId);
      }
    };
    load()
      .catch((err) => {
        if (!cancelled) setError(err instanceof Error ? err.message : String(err));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [active, tab, filters, eventLevel, reloadKey]);

  const loadMore = async () => {
    setLoading(true);
    try {
      if (tab === "decisions" && decisionCursor) {
        const page = await journalApi.decisions(filters, {
          limit: PAGE_SIZE,
          beforeId: decisionCursor,
        });
        setDecisions((prev) => [...prev, ...page.items]);
        setDecisionCursor(page.nextBeforeId);
      } else if (tab === "fills" && fillCursor) {
        const page = await journalApi.fills(filters, { limit: PAGE_SIZE, beforeId: fillCursor });
        setFills((prev) => [...prev, ...page.items]);
        setFillCursor(page.nextBeforeId);
      } else if (tab === "events" && eventCursor) {
        const page = await journalApi.events(
          { groupId: filters.groupId, instId: filters.instId, level: eventLevel || undefined },
          { limit: PAGE_SIZE, beforeId: eventCursor },
        );
        setEvents((prev) => [...prev, ...page.items]);
        setEventCursor(page.nextBeforeId);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  };

  const openDecision = (decisionId: number) => {
    setTab("decisions");
    setSelectedId(decisionId);
  };

  // 空态要说清**为什么空**：一句放之四海而皆准的「暂无数据」在审计台上等于什么都没说。
  const emptyReason = (() => {
    if (stats && !stats.enabled) {
      return "交易日志采集当前处于关闭状态（AI_TRADE_JOURNAL_ENABLED=false），故没有任何留痕。";
    }
    if (stats && stats.tables.decisions === 0) {
      return "还没有任何决策留痕：本特性接线之后跑过的 AI 自动交易会话才会在这里出现。";
    }
    const narrowed =
      filters.instId || filters.action || filters.q || filters.executedOnly ||
      filters.degradedOnly || filters.gateVetoedOnly || filters.trigger || filters.bar;
    if (narrowed) return "当前筛选下没有决策轮次。放宽一两个过滤条件再看看。";
    if (filters.groupId) return "该会话下还没有决策留痕。";
    return "没有匹配的决策轮次。";
  })();

  // 以**分栏容器自己的左边缘**为锚换算宽度，而不是减一个写死的导航栏宽度：那个常数会在
  // 下一次改导航栏尺寸时静默失准，而症状是「拖一下手柄，面板跳一段」。
  const splitRef = useRef<HTMLDivElement>(null);
  const onResize = (clientX: number) => {
    const left = splitRef.current?.getBoundingClientRect().left ?? 0;
    setListWidth(Math.min(MAX_LIST_WIDTH, Math.max(MIN_LIST_WIDTH, clientX - left)));
  };

  return (
    <div className="flex h-full min-h-0 flex-col">
      <JournalToolbar
        sessions={sessions}
        sessionsLoading={sessionsLoading}
        filters={filters}
        onFiltersChange={setFilters}
        stats={stats}
        onOpenExport={() => setExportOpen(true)}
        onOpenHealth={() => setHealthOpen(true)}
        onRefresh={refresh}
      />

      <div className="shrink-0 border-b border-[var(--border)] px-2 py-1">
        <Tabs tabs={TABS} active={tab} onChange={setTab} variant="underline" label="交易日志分页" />
      </div>

      <div ref={splitRef} className="flex min-h-0 flex-1">
        <div className="min-w-0 shrink-0" style={{ width: `${listWidth}px` }}>
          {tab === "decisions" && (
            <JournalDecisionTable
              rows={decisions}
              selectedId={selectedId}
              onSelect={(row) => setSelectedId(row.id)}
              loading={loading}
              error={error}
              onRetry={refresh}
              hasMore={decisionCursor != null}
              onLoadMore={loadMore}
              emptyReason={emptyReason}
            />
          )}
          {tab === "fills" && (
            <JournalFillTable
              rows={fills}
              loading={loading}
              error={error}
              onRetry={refresh}
              hasMore={fillCursor != null}
              onLoadMore={loadMore}
              onOpenDecision={openDecision}
            />
          )}
          {tab === "events" && (
            <JournalEventTable
              rows={events}
              level={eventLevel}
              onLevelChange={setEventLevel}
              loading={loading}
              error={error}
              onRetry={refresh}
              hasMore={eventCursor != null}
              onLoadMore={loadMore}
            />
          )}
        </div>

        <PanelResizeHandle
          onResize={onResize}
          onReset={() => setListWidth(DEFAULT_LIST_WIDTH)}
          ariaLabel="拖动调整日志列表宽度，双击复位"
        />

        <aside className="min-w-0 flex-1 border-l border-[var(--border)] bg-[var(--surface)]/40">
          {/* 详情栏跨页签保持：从「成交与订单」点回一条决策时，右栏立刻显示它。 */}
          <JournalDecisionDetail decisionId={selectedId} />
        </aside>
      </div>

      <JournalExportDialog
        open={exportOpen}
        onClose={() => setExportOpen(false)}
        scope={filters}
        stats={stats}
      />
      <Modal
        open={healthOpen}
        onClose={() => setHealthOpen(false)}
        labelledBy="journal-health-title"
        className="max-w-[560px]"
      >
        <div className="p-4">
          <h2 id="journal-health-title" className="sr-only">
            采集健康度
          </h2>
          <JournalHealthCard stats={stats} />
        </div>
      </Modal>
    </div>
  );
}
