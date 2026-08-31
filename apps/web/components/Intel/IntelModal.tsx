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
import { quoteHitRateLabel } from "@/lib/intelEvidence";
import { conflictIds } from "@/lib/intelActionability";
import type {
  AiTradeIntelSummary,
  IntelAlert,
  IntelCategory,
  IntelConflict,
  IntelEvidence,
  IntelItem,
  IntelItemInput,
  IntelReadResponse,
  IntelRound,
  IntelSettings,
  IntelTier,
} from "@/lib/intelTypes";
import { useAppStore } from "@/lib/store";
import IntelActivityTab from "./IntelActivityTab";
import IntelEditor from "./IntelEditor";
import { IntelDegradeBanner } from "./IntelEmptyStates";
import IntelList from "./IntelList";
import type { IntelCardActions } from "./IntelCard";
import IntelBlockSourceDialog from "./IntelBlockSourceDialog";
import IntelImpactTab from "./IntelImpactTab";
import IntelSourcesTab from "./IntelSourcesTab";

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

type TabKey = IntelTier | "impact" | "sources" | "archived" | "activity";
/** 三层 + 影响力 + 信源 + 已归档 + 动态 = 7 个页签（actionability §3.4.1：**零新增页签**）。 */
const LIST_TABS: TabKey[] = ["ultra", "mid", "long", "archived"];
/** 待处理告警时限（秒）的**回落值**：真值走 `settings.alertPendingTtlS`，写死会与后端判定分叉。 */
const DEFAULT_ALERT_TTL_S = 180;

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
  const [alerts, setAlerts] = useState<IntelAlert[]>([]);
  /** 「仅看冲突（N）」筛选（轴 C）。矛盾是条目的属性，故它是一个筛选 chip 而不是第八个页签。 */
  const [onlyConflicts, setOnlyConflicts] = useState(false);
  const [settings, setSettings] = useState<IntelSettings | null>(null);
  const [blocking, setBlocking] = useState("");
  /** 每次写动作成功后 +1；影响力页签据此重取（它不在 5 秒轮询里）。 */
  const [actionToken, setActionToken] = useState(0);
  const [tab, setTab] = useState<TabKey>("ultra");
  const [category, setCategory] = useState<IntelCategory | "">("");
  const [query, setQuery] = useState("");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const [editing, setEditing] = useState<IntelItem | null>(null);
  const [editorOpen, setEditorOpen] = useState(false);
  const [pendingDelete, setPendingDelete] = useState<IntelItem | null>(null);
  const [nowMs, setNowMs] = useState(() => Date.now());
  // 证据按 `itemId` 缓存在**弹窗生命周期**内：展开时才发，收起再展开**不重发**（§3.3.2）。
  const [evidenceById, setEvidenceById] = useState<Record<number, IntelEvidence>>({});
  const [evidenceLoadingId, setEvidenceLoadingId] = useState<number | null>(null);
  const [evidenceErrors, setEvidenceErrors] = useState<Record<number, string>>({});
  const [blockedDomains, setBlockedDomains] = useState<string[]>([]);

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

  // 三把单键的**生效值**只影响空态文案的选择（closed-loop §3.7.3 的四段文案）。取一次即可：
  // 它们是设置，不是行情——每 5 秒重取一次只会给一块不会变的面板加一个请求源。
  useEffect(() => {
    if (!open) return;
    intelApi
      .settings()
      .then(setSettings)
      .catch(() => undefined);
  }, [open]);

  // 拉黑清单只取一次：它是设置，不是行情。抽屉里那几条外链据此变灰（一个仍然可点的
  // 「已屏蔽来源」是自相矛盾的）。
  useEffect(() => {
    if (!open) return;
    intelApi
      .feedback(instId)
      .then((res) =>
        setBlockedDomains(
          res.rows.filter((row) => row.action === "block").map((row) => row.key),
        ),
      )
      .catch(() => undefined);
  }, [open, instId, actionToken]);

  // 两条取数**一起发**：只取到一半的时间线会让链路断在中间。
  useEffect(() => {
    if (!open || tab !== "activity") return;
    void Promise.all([
      intelApi.rounds(instId).then((res) => setRounds(res.rounds)),
      intelApi.alerts(instId).then((res) => setAlerts(res.alerts)),
    ]).catch(() => undefined);
  }, [open, tab, instId, actionToken]);

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
      { key: "impact", label: "影响力" },
      { key: "sources", label: "信源" },
      { key: "archived", label: `已归档 ${archived}` },
      // 「整理记录」→「动态」：轮次与告警合流成一条时间线（详见 `IntelActivityTab`）。
      { key: "activity", label: "动态" },
    ],
    [summary, archived],
  );

  const conflicts: IntelConflict[] = useMemo(() => data?.conflicts ?? [], [data]);
  const conflicted = useMemo(() => conflictIds(conflicts), [conflicts]);
  /** 全部层的条目按 id 索引：矛盾对的另一端可能**不在当前这一层**。 */
  const itemsById = useMemo(() => {
    const out: Record<number, IntelItem> = {};
    for (const tier of TIER_ORDER) {
      for (const item of data?.tiers[tier]?.items ?? []) out[item.id] = item;
    }
    for (const item of inactive) out[item.id] = item;
    return out;
  }, [data, inactive]);

  const visible = useMemo(() => {
    if (!LIST_TABS.includes(tab)) return [];
    const source =
      tab === "archived" ? inactive : (data?.tiers[tab as IntelTier]?.items ?? []);
    const needle = query.trim().toLowerCase();
    return source.filter((item) => {
      if (category && item.category !== category) return false;
      if (onlyConflicts && !conflicted.has(item.id)) return false;
      if (!needle) return true;
      return (
        item.headline.toLowerCase().includes(needle) ||
        item.summary.toLowerCase().includes(needle)
      );
    });
  }, [data, inactive, tab, category, query, onlyConflicts, conflicted]);

  const guard = async (run: () => Promise<unknown>) => {
    setBusy(true);
    setNotice("");
    try {
      await run();
      await load();
      // 归档页签上的动作（恢复 / 删除）不改在库列表，只有这一次重取才能让它看到结果。
      if (tab === "archived") await loadInactive();
      // 影响力页签自己取数、且**不进 5 秒轮询**（它是聚合读数，不是行情）。少这一步，用户在
      // 榜单上点掉一个判定之后那张卡片纹丝不动 —— 一次「点了没反应」，而它其实成功了。
      setActionToken((n) => n + 1);
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
    // 「我不同意」：清判定 ⇒ 注入立刻恢复，且该条目**永久**移出自动候选池（closed-loop P0-4）。
    onClearVerdict: (item) =>
      void guard(() => intelApi.patchItem(item.id, { clearVerdict: true })),
    onVerifyItem: (item) => void guard(() => intelApi.verifyItem(item.id)),
    onBlockSource: (domain) => setBlocking(domain),
    onLoadEvidence: (item) => {
      if (evidenceById[item.id] !== undefined || evidenceLoadingId === item.id) return;
      setEvidenceLoadingId(item.id);
      // ⚠️ 错误**按 itemId 存**，不是一个弹窗级的字符串：后者会让 A 的失败在 B 展开时被清掉，
      // 而 A 那个还开着的抽屉于是从「读取失败」变成「这一条没有留存任何原文引证」——把一次
      // 请求失败说成一句关于数据的断言，是四句话里最不该出现的那一句。
      setEvidenceErrors((prev) => {
        if (prev[item.id] === undefined) return prev;
        const { [item.id]: _dropped, ...rest } = prev;
        return rest;
      });
      intelApi
        .evidence(item.id)
        .then((res) => setEvidenceById((prev) => ({ ...prev, [item.id]: res })))
        .catch((err) =>
          setEvidenceErrors((prev) => ({
            ...prev,
            [item.id]: err instanceof Error ? err.message : "读取原文引证失败",
          })),
        )
        .finally(() => setEvidenceLoadingId(null));
    },
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

  /**
   * 重新装配：`arm_failed` 的空态与降级 banner 共用的那一条出路。
   *
   * 走 `guard` 是刻意的——它已经承担了 busy 态、错误文案与那次 `load()` 重取；重装成功之后
   * summary 会立刻说新话，用户不必关掉弹窗再打开。
   */
  const rearm = () => void guard(() => intelApi.rearm());

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
            {quoteHitRateLabel(summary?.quoteHitRate ?? null) && (
              <span
                data-testid="intel-hit-rate"
                title="这是把「引文要求」调到「必须附可核对引文」的判据：连续 20 轮 ≥ 70% 再考虑收紧。"
              >
                {quoteHitRateLabel(summary?.quoteHitRate ?? null)}
              </span>
            )}
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
          onRearm={rearm}
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

        {LIST_TABS.includes(tab) && (
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
            {conflicted.size > 0 && (
              <FilterChip
                label={`仅看冲突（${conflicted.size}）`}
                active={onlyConflicts}
                onClick={() => setOnlyConflicts((v) => !v)}
              />
            )}
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
          {tab === "activity" ? (
            <IntelActivityTab
              rounds={rounds}
              alerts={alerts}
              loading={!rounds.length && !alerts.length && !data}
              nowMs={nowMs}
              pendingTtlS={settings?.alertPendingTtlS ?? DEFAULT_ALERT_TTL_S}
            />
          ) : tab === "impact" ? (
            <IntelImpactTab
              instId={instId}
              reloadToken={actionToken}
              summary={summary}
              settingsCiteEnabled={settings?.citeEnabled ?? true}
              settingsAttributionEnabled={settings?.attributionEnabled ?? true}
              onClearVerdict={actions.onClearVerdict}
              onVerifyNow={actions.onVerifyItem}
            />
          ) : tab === "sources" ? (
            <IntelSourcesTab instId={instId} onChanged={() => void load()} />
          ) : (
            <IntelList
              items={visible}
              tier={tab === "archived" ? "ultra" : (tab as IntelTier)}
              summary={summary}
              loading={data === null}
              archivedView={tab === "archived"}
              filtered={!!query.trim() || !!category || onlyConflicts}
              actions={actions}
              nowMs={nowMs}
              onOpenSettings={openSettings}
              onCollect={() => void guard(() => intelApi.collect(instId))}
              onRearm={rearm}
              onClearFilters={() => {
                setQuery("");
                setCategory("");
                setOnlyConflicts(false);
              }}
              evidenceById={evidenceById}
              evidenceLoadingId={evidenceLoadingId}
              evidenceErrors={evidenceErrors}
              blockedDomains={blockedDomains}
              conflicts={conflicts}
              itemsById={itemsById}
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
              onClick={() => void guard(() => intelApi.verify(instId))}
            >
              立即核查
            </Button>
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
      <IntelBlockSourceDialog
        open={blocking !== ""}
        domain={blocking}
        instId={instId}
        onCancel={() => setBlocking("")}
        onConfirm={(scopeInstId) => {
          const domain = blocking;
          setBlocking("");
          void guard(async () => {
            const res = await intelApi.writeFeedback({
              key: domain,
              action: "block",
              instId: scopeInstId,
            });
            setNotice(
              res.archived > 0
                ? `已不再采纳 ${domain}；${res.archived} 条情报已归档，可在「已归档」中恢复`
                : `已不再采纳 ${domain}`,
            );
          });
        }}
      />
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
