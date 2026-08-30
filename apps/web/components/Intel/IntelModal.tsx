"use client";

import { useCallback, useEffect, useId, useMemo, useRef, useState } from "react";
import clsx from "clsx";
import ConfirmDialog from "@/components/ui/ConfirmDialog";
import { Button, Modal, Tabs } from "@/components/ui";
import {
  CATEGORY_LABEL,
  TIER_LABEL,
  TIER_ORDER,
  absoluteTime,
  nextDueText,
  relativeTime,
} from "@/lib/intel";
import { intelApi } from "@/lib/intelApi";
import type {
  AiTradeIntelSummary,
  IntelCategory,
  IntelItem,
  IntelItemInput,
  IntelReadResponse,
  IntelRound,
  IntelTier,
} from "@/lib/intelTypes";
import { useAppStore } from "@/lib/store";
import IntelEditor from "./IntelEditor";
import { IntelDegradeBanner } from "./IntelEmptyStates";
import IntelList from "./IntelList";
import type { IntelCardActions } from "./IntelCard";
import IntelRoundsTab from "./IntelRoundsTab";

const CATEGORIES: IntelCategory[] = [
  "market",
  "sentiment",
  "macro",
  "onchain",
  "project",
  "risk",
];

// 弹窗打开期的轮询节拍。WS 的 `intel` 事件是主通道，这条轮询只兜住「后端刚跑完但 WS 恰好断了」
// 的那一小段——故意慢，5 秒一次不会让一个开着的弹窗变成一个持续的请求源。
const POLL_MS = 5_000;

type TabKey = IntelTier | "archived" | "rounds";

/**
 * 标的情报库弹窗（ai-trade-market-intel §3.8.3）。
 *
 * 复用 `ui/Modal`（对话框栈 / 焦点陷阱 / Esc / 滚动锁定），不自造壳。
 *
 * 两条 HCI 纪律：
 * - **刷新不清空**：轮询回来的数据直接替换 state，绝不先置 `null` 再重画——那会让用户每 5 秒
 *   丢一次阅读位置。骨架屏只在首次打开、尚无任何数据时出现。
 * - **打开即标记已读**：`POST /seen` 把未读徽标的真值落到服务端（换台机器也一致），而不是前端
 *   `localStorage`。
 */
export default function IntelModal({
  instId,
  open,
  onClose,
}: {
  instId: string;
  open: boolean;
  onClose: () => void;
}) {
  const titleId = useId();
  const searchRef = useRef<HTMLInputElement>(null);
  const setShowSettings = useAppStore((s) => s.setShowSettings);
  const setSettingsSection = useAppStore((s) => s.setSettingsSection);
  const setAiTradeIntelSummary = useAppStore((s) => s.setAiTradeIntelSummary);
  const liveSummary = useAppStore((s) => s.aiTradeIntel[instId] ?? null);

  const [data, setData] = useState<IntelReadResponse | null>(null);
  // 已归档 / 已静音的条目**另取一次**：`GET /intel/{instId}` 的 `status` 是一个单值过滤器，
  // 在库列表那一次取的是 `active`，其结果里恒不含任何非 active 行——照着它筛 `status !== "active"`
  // 得到的永远是空数组，于是「已归档 N」这个页签会一边报着 N 条、一边什么都不显示。
  const [inactive, setInactive] = useState<IntelItem[]>([]);
  const [rounds, setRounds] = useState<IntelRound[]>([]);
  const [tab, setTab] = useState<TabKey>("ultra");
  const [category, setCategory] = useState<IntelCategory | "">("");
  const [query, setQuery] = useState("");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const [editing, setEditing] = useState<IntelItem | null>(null);
  const [editorOpen, setEditorOpen] = useState(false);
  const [pendingDelete, setPendingDelete] = useState<IntelItem | null>(null);
  const [nowMs, setNowMs] = useState(() => Date.now());

  // WS 推来的 summary 优先（它带 `inFlight` 与本轮降级），REST 的那一份只在 WS 尚未推过时兜底。
  const summary: AiTradeIntelSummary | null = liveSummary ?? data?.summary ?? null;

  const load = useCallback(async () => {
    try {
      const res = await intelApi.read(instId, { status: "active", limit: 200 });
      setData(res);
      setAiTradeIntelSummary(instId, res.summary);
    } catch (err) {
      setNotice(err instanceof Error ? err.message : "读取情报库失败");
    }
  }, [instId, setAiTradeIntelSummary]);

  /** 已归档 + 已静音两次取数合并成一张列表（静音若没有回头路，用户唯一的撤销手段就成了永久删除）。 */
  const loadInactive = useCallback(async () => {
    try {
      const [archivedRes, mutedRes] = await Promise.all([
        intelApi.read(instId, { status: "archived", limit: 200 }),
        intelApi.read(instId, { status: "muted", limit: 200 }),
      ]);
      setInactive(
        [archivedRes, mutedRes].flatMap((res) =>
          TIER_ORDER.flatMap((tier) => res.tiers[tier]?.items ?? []),
        ),
      );
    } catch (err) {
      setNotice(err instanceof Error ? err.message : "读取已归档情报失败");
    }
  }, [instId]);

  useEffect(() => {
    if (!open) return;
    void load();
    // 打开即标记已读：真值落服务端（评审 P1-2），失败静默——它只影响一个徽标。
    intelApi
      .markSeen(instId)
      .then((res) => setAiTradeIntelSummary(instId, res.summary))
      .catch(() => undefined);
    const timer = setInterval(() => {
      setNowMs(Date.now());
      void load();
    }, POLL_MS);
    return () => clearInterval(timer);
  }, [open, instId, load, setAiTradeIntelSummary]);

  useEffect(() => {
    if (!open || tab !== "rounds") return;
    intelApi
      .rounds(instId)
      .then((res) => setRounds(res.rounds))
      .catch(() => undefined);
  }, [open, tab, instId]);

  useEffect(() => {
    if (!open || tab !== "archived") return;
    void loadInactive();
  }, [open, tab, loadInactive]);

  // `/` 聚焦搜索（键盘用户不必用鼠标穿过整条过滤条）。挂在弹窗容器上而不是 window：
  // 全局监听会在别的输入框里抢走斜杠。
  const onKeyDown = (e: React.KeyboardEvent<HTMLDivElement>) => {
    if (e.key !== "/" || e.target instanceof HTMLInputElement) return;
    e.preventDefault();
    searchRef.current?.focus();
  };

  // 页签计数把静音一并算进去：静音条目同样不在库、同样只能从这个页签找回来。
  const archived = useMemo(
    () => (data?.archivedTotal ?? 0) + (data?.mutedTotal ?? 0),
    [data],
  );
  const tabs = useMemo(
    () => [
      ...TIER_ORDER.map((tier) => ({
        key: tier,
        label: `${TIER_LABEL[tier]} ${summary?.counts[tier] ?? 0}`,
      })),
      { key: "archived", label: `已归档 ${archived}` },
      { key: "rounds", label: "整理记录" },
    ],
    [summary, archived],
  );

  const visible = useMemo(() => {
    if (tab === "rounds") return [];
    const source =
      tab === "archived" ? inactive : (data?.tiers[tab as IntelTier]?.items ?? []);
    const needle = query.trim().toLowerCase();
    return source.filter((item) => {
      if (category && item.category !== category) return false;
      if (!needle) return true;
      return (
        item.headline.toLowerCase().includes(needle) ||
        item.summary.toLowerCase().includes(needle)
      );
    });
  }, [data, inactive, tab, category, query]);

  const guard = async (run: () => Promise<unknown>) => {
    setBusy(true);
    setNotice("");
    try {
      await run();
      await load();
      // 归档页签上的动作（恢复 / 删除）不改在库列表，只有这一次重取才能让它看到结果。
      if (tab === "archived") await loadInactive();
    } catch (err) {
      setNotice(err instanceof Error ? err.message : "操作失败，请稍后重试");
    } finally {
      setBusy(false);
    }
  };

  const actions: IntelCardActions = {
    onTogglePin: (item) => void guard(() => intelApi.patchItem(item.id, { pinned: !item.pinned })),
    onMove: (item, tier) => void guard(() => intelApi.moveItem(item.id, tier)),
    onEdit: (item) => {
      setEditing(item);
      setEditorOpen(true);
    },
    onMute: (item) => void guard(() => intelApi.patchItem(item.id, { status: "muted" })),
    onRestore: (item) => void guard(() => intelApi.patchItem(item.id, { status: "active" })),
    onDelete: (item) => setPendingDelete(item),
  };

  const submitEditor = async (draft: IntelItemInput) => {
    if (editing) {
      await intelApi.patchItem(editing.id, {
        tier: draft.tier,
        category: draft.category,
        headline: draft.headline,
        summary: draft.summary,
        stance: draft.stance,
        impact: draft.impact,
      });
    } else {
      await intelApi.createItem(instId, draft);
    }
    await load();
  };

  const openSettings = () => {
    setSettingsSection("intel-source");
    setShowSettings(true);
    onClose();
  };

  return (
    <Modal
      open={open}
      onClose={onClose}
      labelledBy={titleId}
      className="max-w-[56vw] max-md:max-w-[94vw]"
    >
      <div
        className="flex max-h-[80vh] flex-col"
        onKeyDown={onKeyDown}
        data-testid="intel-modal"
      >
        <header className="flex items-center justify-between gap-3 border-b border-[var(--border)] px-4 py-2.5">
          <h2 id={titleId} className="truncate text-sm font-semibold text-[var(--text)]">
            {instId} · 市场情报
          </h2>
          <span className="flex shrink-0 items-center gap-2 text-[10px] text-[var(--muted)]">
            {summary?.inFlight && <span data-testid="intel-in-flight">采集中…</span>}
            {summary?.lastCollectTs != null && (
              <span title={absoluteTime(summary.lastCollectTs)}>
                最近采集 {relativeTime(summary.lastCollectTs, nowMs)}
              </span>
            )}
            <Button variant="ghost" size="sm" onClick={onClose} aria-label="关闭情报弹窗">
              ✕
            </Button>
          </span>
        </header>

        {/* 2px 顶部进度条（§3.8.3）：刷新期间的**唯一**视觉反馈——绝不把已渲染的列表清空重画，
            那会让用户每隔几秒就丢一次阅读位置。骨架屏只留给首次打开、尚无任何数据的那一刻。 */}
        <div
          aria-hidden="true"
          data-testid="intel-progress"
          className={clsx(
            "h-[2px] w-full origin-left bg-[var(--accent)] transition-opacity duration-150 motion-reduce:transition-none",
            busy || summary?.inFlight ? "opacity-100" : "opacity-0",
          )}
        />

        <IntelDegradeBanner
          degrade={summary?.degrade ?? null}
          onOpenSettings={openSettings}
          onRetry={() => void guard(() => intelApi.collect(instId))}
        />

        <div className="px-4 pt-2">
          <Tabs
            tabs={tabs}
            active={tab}
            onChange={(key) => setTab(key as TabKey)}
            variant="underline"
            label="情报层级"
          />
        </div>

        {tab !== "rounds" && (
          <div className="flex flex-wrap items-center gap-1.5 border-b border-[var(--border)] px-4 py-2">
            <FilterChip label="全部" active={category === ""} onClick={() => setCategory("")} />
            {CATEGORIES.map((c) => (
              <FilterChip
                key={c}
                label={CATEGORY_LABEL[c]}
                active={category === c}
                onClick={() => setCategory(c)}
              />
            ))}
            {/* 这里用裸 `<input>` 而不是 `ui/Input`：后者是普通函数组件、拿不到 `ref`，而
                「按 / 聚焦搜索」必须有 ref。样式与 `ui/Input` 同源，只是尺寸收窄到过滤条高度。 */}
            <input
              ref={searchRef}
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="搜索标题/摘要（按 / 聚焦）"
              aria-label="搜索情报"
              className="ml-auto h-7 w-52 rounded-[10px] border border-[var(--border)] bg-[var(--surface)] px-2 text-xs text-[var(--text)] outline-none transition-colors placeholder:text-[var(--muted)] hover:border-[var(--accent)] focus:border-[var(--accent)]"
            />
            <Button
              variant="ghost"
              size="sm"
              onClick={() => {
                setEditing(null);
                setEditorOpen(true);
              }}
            >
              ＋ 新增
            </Button>
          </div>
        )}

        <div className="min-h-0 flex-1 overflow-y-auto">
          {tab === "rounds" ? (
            <IntelRoundsTab rounds={rounds} loading={!rounds.length && !data} nowMs={nowMs} />
          ) : (
            <IntelList
              items={visible}
              tier={tab === "archived" ? "ultra" : (tab as IntelTier)}
              summary={summary}
              loading={data === null}
              archivedView={tab === "archived"}
              filtered={!!query.trim() || !!category}
              actions={actions}
              nowMs={nowMs}
              onOpenSettings={openSettings}
              onCollect={() => void guard(() => intelApi.collect(instId))}
              onClearFilters={() => {
                setQuery("");
                setCategory("");
              }}
            />
          )}
        </div>

        <footer className="flex items-center justify-between gap-2 border-t border-[var(--border)] px-4 py-2">
          <span className="min-w-0 truncate text-[10px] text-[var(--muted)]">
            {nextDueText(summary, nowMs)}
            {summary ? ` · 今日已用 ${summary.dailyUsed}/${summary.dailyCap}` : ""}
            {notice ? ` · ${notice}` : ""}
          </span>
          <span className="flex shrink-0 items-center gap-1.5">
            <Button
              variant="ghost"
              size="sm"
              disabled={busy}
              onClick={() => void guard(() => intelApi.curate(instId))}
            >
              立即整理
            </Button>
            <Button
              variant="primary"
              size="sm"
              disabled={busy}
              onClick={() => void guard(() => intelApi.collect(instId))}
            >
              立即采集
            </Button>
          </span>
        </footer>
      </div>

      {editorOpen && (
        <IntelEditor
          open
          item={editing}
          onClose={() => setEditorOpen(false)}
          onSubmit={submitEditor}
        />
      )}
      <ConfirmDialog
        open={pendingDelete !== null}
        title="删除这条情报？"
        message={`「${pendingDelete?.headline ?? ""}」将被永久删除，无法恢复。若只是想让它不再进入决策，请改用「静音」。`}
        confirmText="永久删除"
        onCancel={() => setPendingDelete(null)}
        onConfirm={() => {
          const target = pendingDelete;
          setPendingDelete(null);
          if (target) void guard(() => intelApi.deleteItem(target.id));
        }}
      />
    </Modal>
  );
}

function FilterChip({
  label,
  active,
  onClick,
}: {
  label: string;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className={clsx(
        "rounded-full px-2 py-0.5 text-[10px] transition-colors outline-none",
        "focus-visible:ring-2 focus-visible:ring-[var(--accent)]",
        active
          ? "bg-[var(--accent-soft)] text-[var(--accent-ink)]"
          : "text-[var(--muted)] hover:bg-[var(--surface-2)] hover:text-[var(--text)]",
      )}
    >
      {label}
    </button>
  );
}
