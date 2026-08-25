"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import ConfirmDialog from "@/components/ui/ConfirmDialog";
import { Button, Card, Field, Input, Tabs } from "@/components/ui";
import { HARVEST_REASON_ZH, honestyNotes, relativeTime } from "@/lib/experience";
import { experienceApi } from "@/lib/experienceApi";
import type {
  ExperienceFilters,
  ExperienceGraphView,
  ExperienceLessonPage,
  ExperienceNode,
  ExperienceNodeDetail,
  ExperienceOverview,
  ExperiencePruneResult,
  ExperienceSettings,
  ExperienceUtilityPage,
} from "@/lib/experienceTypes";
import ExperienceDetailDrawer from "./experience/ExperienceDetailDrawer";
import ExperienceEditor from "./experience/ExperienceEditor";
import ExperienceGraphCanvas from "./experience/ExperienceGraphCanvas";
import ExperienceList from "./experience/ExperienceList";
import ExperiencePrunePrompt from "./experience/ExperiencePrunePrompt";
import ExperienceRetrievalPreview from "./experience/ExperienceRetrievalPreview";
import ExperienceToolbar from "./experience/ExperienceToolbar";
import ExperienceUtilityPanel from "./experience/ExperienceUtilityPanel";

const BARS = ["1m", "5m", "15m", "30m", "1H", "4H", "12H", "1D"];
const TABS = [
  { key: "list", label: "列表" },
  { key: "graph", label: "图谱" },
  { key: "utility", label: "注入后表现" },
  { key: "preview", label: "检索预演" },
];

/**
 * 设置中心 →「交易经验库」。
 *
 * 布局自上而下：概览条（含三个诚实性注记）→ 工具栏 → Tabs（列表 / 图谱 / 检索预演）。
 *
 * **列表页签排在图谱之前**：图谱是加分项，列表才是无障碍等价形态；把默认页签给列表，读屏与
 * 键盘用户不必先穿过一张对他们不可用的图。
 */
export default function ExperienceSection() {
  const [overview, setOverview] = useState<ExperienceOverview | null>(null);
  const [settings, setSettings] = useState<ExperienceSettings | null>(null);
  const [graph, setGraph] = useState<ExperienceGraphView | null>(null);
  const [lessons, setLessons] = useState<ExperienceLessonPage | null>(null);
  const [utility, setUtility] = useState<ExperienceUtilityPage | null>(null);
  const [filters, setFilters] = useState<ExperienceFilters>({});
  // 「效用」筛选是**纯前端**的：读数已经随列表行下发，再打一次接口只会让两处条数有机会漂移。
  const [utilityFilter, setUtilityFilter] = useState("");
  const [prunePreview, setPrunePreview] = useState<ExperiencePruneResult | null>(null);
  const [tab, setTab] = useState("list");
  const [limit, setLimit] = useState(120);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [detail, setDetail] = useState<ExperienceNodeDetail | null>(null);
  const [editing, setEditing] = useState<ExperienceNode | null>(null);
  const [editorOpen, setEditorOpen] = useState(false);
  const [editorError, setEditorError] = useState<string | null>(null);
  const [pendingDelete, setPendingDelete] = useState<ExperienceNode | null>(null);
  const [pendingImport, setPendingImport] = useState<{ nodes: unknown[]; mode: "merge" | "replace" } | null>(null);
  // 选文件之前先定模式：`<input type="file">` 的 change 事件拿不到「是哪个按钮打开我的」。
  const importMode = useRef<"merge" | "replace">("merge");
  const [showThresholds, setShowThresholds] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  const loadAll = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      // 图谱与列表**分别向各自的端点取数**，而两个端点在后端共用同一个 `_match` 判据——
      // 「同数据源同筛选」因此是服务端保证的，不是前端从一份数据里切两次切出来的（A-22）。
      const [head, view, page, panel, scores] = await Promise.all([
        experienceApi.overview(),
        experienceApi.graph(filters, limit),
        experienceApi.lessons(filters, 0, 200),
        experienceApi.settings(),
        experienceApi.utility({ instId: filters.instId, bar: filters.bar, limit: 300 }),
      ]);
      setOverview(head);
      setGraph(view);
      setLessons(page);
      setSettings(panel);
      setUtility(scores);
    } catch (err) {
      setError((err as Error).message || "读取经验库失败");
    } finally {
      setLoading(false);
    }
  }, [filters, limit]);

  useEffect(() => { void loadAll(); }, [loadAll]);

  const symbols = overview?.symbols ?? [];

  const act = async (label: string, fn: () => Promise<unknown>) => {
    setBusy(label);
    setError(null);
    setNotice(null);
    try {
      await fn();
      await loadAll();
    } catch (err) {
      setError((err as Error).message || `${label}失败`);
    } finally {
      setBusy(null);
    }
  };

  // 收 id 而不是整个节点：列表 / 图谱 / 效用面板三处的行形状各不相同，但它们通向的是**同一个**
  // 抽屉。收整个节点会逼着效用面板去伪造一个 `ExperienceNode`，那是一份迟早会漂移的假数据。
  const openDetail = async (nodeId: number) => {
    try {
      setDetail(await experienceApi.detail(nodeId));
    } catch (err) {
      setError((err as Error).message || "读取详情失败");
    }
  };

  const refreshDetail = async (nodeId: number) => {
    try {
      setDetail(await experienceApi.detail(nodeId));
    } catch {
      setDetail(null);      // 详情读不回来就关掉抽屉，绝不留一份已经不对的旧数据在屏幕上
    }
  };

  const onPin = (node: ExperienceNode) => act("置顶", async () => {
    await experienceApi.patch(node.id, { pinned: !node.pinned });
    if (detail?.node.id === node.id) await refreshDetail(node.id);
  });

  const onMute = (node: ExperienceNode) => act("静音", async () => {
    await experienceApi.patch(node.id, { muted: !node.muted });
    if (detail?.node.id === node.id) await refreshDetail(node.id);
  });

  const onExport = () => act("导出", async () => {
    const text = await experienceApi.exportNdjson({ instId: filters.instId });
    const url = URL.createObjectURL(new Blob([text], { type: "application/x-ndjson" }));
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = "aragon-experience.ndjson";
    anchor.click();
    URL.revokeObjectURL(url);
    setNotice("已导出经验库（NDJSON）。");
  });

  const onPickFile = async (file: File | null, mode: "merge" | "replace") => {
    if (!file) return;
    try {
      const parsed = JSON.parse(await file.text());
      const nodes = Array.isArray(parsed) ? parsed : (parsed?.nodes ?? []);
      if (!Array.isArray(nodes) || nodes.length === 0) {
        setError("这个文件里没有可导入的经验节点。");
        return;
      }
      setPendingImport({ nodes, mode });
    } catch {
      setError("文件不是合法的 JSON。");
    }
  };

  /**
   * 编辑器的提交路径**自带 try/catch**，绝不复用 `act`。
   *
   * `act` 把失败写进页面顶部那条横幅并**吞掉异常**（那是它对其余按钮的正确行为）。走它的话
   * `editorError` 这个 prop 就有读点没有写点——表单里那块红字**永远不会出现**，用户在一个已经
   * 关掉的弹窗背后才看到错误。这正是本特性反复在守的「算了却看不见」的镜像形态。
   */
  const submitEditor = async (payload: {
    statement: string; detail: string; context: Record<string, string>;
    polarity: "positive" | "negative";
  }) => {
    setBusy("保存");
    setEditorError(null);
    try {
      if (editing) {
        await experienceApi.patch(editing.id, {
          statement: payload.statement, detail: payload.detail,
        });
      } else {
        await experienceApi.create(payload);
      }
      setEditorOpen(false);
      setNotice(editing
        ? "已保存。"
        : "已新增一条候选经验——它还不会进入 Agent 的纲要，置顶它才会。");
      await loadAll();
    } catch (err) {
      setEditorError((err as Error).message || "保存失败");
    } finally {
      setBusy(null);
    }
  };

  const notes = honestyNotes({
    health: overview?.materializeHealth,
    excludedByCostBasis: overview?.excludedByCostBasis ?? 0,
    harvestGapTotal: overview?.harvest?.gapTotal ?? overview?.harvestGapTotal ?? 0,
    adoptedTotal: overview?.harvest?.adoptedTotal ?? 0,
    utilityMuted: overview?.utility?.ineffective ?? 0,
  });
  // 采集缺口的逐原因明细：**零值的键也列**——列出来才知道「这条路今天没走过」与「这条路
  // 不存在」的区别（后端的键集是闭合的六个，多一个就是一个永远显示 0 的假分类）。
  const harvestReasons = Object.entries(overview?.harvest?.reasons ?? {});
  // 「效用」筛选（前端）：全部 / 有注入样本 / 已判失效。
  const visibleLessons = (lessons?.items ?? []).filter((row) => {
    if (utilityFilter === "scored") return (row.utility?.injected ?? 0) > 0;
    if (utilityFilter === "ineffective") return row.utility?.ineffective === true;
    return true;
  });

  const onPrune = () => act("清理沉降经验", async () => {
    // **绝不静默批删**：先跑一次 dryRun 把条数拿回来，二次确认之后才真删（A-34）。
    setPrunePreview(await experienceApi.prune(true));
  });

  return (
    <div className="flex flex-col gap-4" data-testid="experience-section">
      <header className="flex flex-col gap-1">
        <h2 className="text-sm font-semibold text-[var(--text)]">交易经验库</h2>
        <p className="text-[11px] leading-relaxed text-[var(--muted)]">
          AI 自动交易每平掉一笔仓位就会留下一条「已结算往返 + 开仓那一刻的语境」的收据。
          这里是那些收据被聚合成的经验与教训——它们只会进入 Agent 的 prompt，
          <strong className="text-[var(--text)]">绝不参与任何风控、仓位或下单门禁</strong>。
        </p>
      </header>

      <Card className="flex flex-col gap-3 p-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex flex-wrap items-center gap-4">
            <Stat label="铁律" value={overview?.lessons.iron ?? 0} />
            <Stat label="常规" value={overview?.lessons.normal ?? 0} />
            <Stat label="候选" value={overview?.lessons.candidate ?? 0} />
            <Stat label="已沉降" value={overview?.lessons.muted ?? 0} />
            <Stat label="事件样本" value={overview?.events ?? 0} />
            <Stat label="有成交的分组" value={overview?.sessions ?? 0} />
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <Button
              variant="ghost"
              size="sm"
              onClick={() => act("重建", () => experienceApi.rebuild("stat"))}
              disabled={busy !== null}
              title="智能：能走增量就走增量（成本与库大小近似无关），判据不满足时自动回全量"
            >
              {busy === "重建" ? "重建中…" : "重建图谱"}
            </Button>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => act("强制全量重建", () => experienceApi.rebuild("full"))}
              disabled={busy !== null}
              title="从事件表整表重算并重写增量底座。它是增量算错时的兜底出口，大库上会明显更慢。"
            >
              {busy === "强制全量重建" ? "重建中…" : "强制全量"}
            </Button>
            <Button variant="ghost" size="sm" onClick={onExport} disabled={busy !== null}>
              导出
            </Button>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => { importMode.current = "merge"; fileRef.current?.click(); }}
              disabled={busy !== null}
            >
              导入（合并）
            </Button>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => { importMode.current = "replace"; fileRef.current?.click(); }}
              disabled={busy !== null}
              title="先删除既有的手工与导入经验，再写入文件里的那些（统计经验不受影响）"
            >
              替换式导入
            </Button>
            <input
              ref={fileRef}
              type="file"
              accept="application/json,.json"
              className="hidden"
              aria-label="选择要导入的经验 JSON 文件"
              onChange={(e) => {
                void onPickFile(e.target.files?.[0] ?? null, importMode.current);
                e.target.value = "";
              }}
            />
            <Button
              variant="ghost"
              size="sm"
              onClick={() => setShowThresholds((v) => !v)}
              aria-expanded={showThresholds}
            >
              阈值设置
            </Button>
            <Button variant="primary" size="sm" onClick={() => { setEditing(null); setEditorError(null); setEditorOpen(true); }}>
              新增经验
            </Button>
          </div>
        </div>

        {symbols.length > 0 && (
          <div className="flex flex-wrap items-center gap-1.5">
            {symbols.map((symbol) => (
              <span
                key={symbol}
                className="rounded-full bg-[var(--surface-2)] px-2 py-0.5 text-[10px] text-[var(--muted)]"
              >
                {symbol}
              </span>
            ))}
            <span className="text-[10px] text-[var(--muted)]">
              · 最近更新 {relativeTime(overview?.lastUpdatedTs)}
            </span>
          </div>
        )}

        {notes.map((note) => (
          <p
            key={note.text}
            className="text-[10px] leading-relaxed"
            style={{ color: note.warn ? "var(--warn)" : "var(--muted)" }}
            data-testid={note.warn ? "experience-honesty-warn" : "experience-honesty-note"}
          >
            {note.text}
          </p>
        ))}
        {harvestReasons.length > 0 && (overview?.harvest?.gapTotal ?? 0) > 0 && (
          <p
            className="text-[10px] leading-relaxed text-[var(--muted)]"
            data-testid="experience-harvest-reasons"
          >
            采集缺口明细：
            {harvestReasons
              .map(([key, count]) => `${HARVEST_REASON_ZH[key] ?? key} ${count}`)
              .join(" · ")}
          </p>
        )}
        {(overview?.rejectedNarrative ?? 0) > 0 && (
          <p className="text-[10px] leading-relaxed text-[var(--muted)]">
            另有 {overview?.rejectedNarrative} 条 AI 蒸馏输出因语境越界被整条丢弃（闭合词表之外的
            坐标轴在图上永远匹配不到任何东西）。
          </p>
        )}

        {showThresholds && settings && (
          <ThresholdPanel
            settings={settings}
            onSaved={(next) => { setSettings(next); setNotice("阈值已保存。点「重建图谱」让它对既有经验生效。"); }}
            onError={setError}
          />
        )}
      </Card>

      {error && (
        <p className="rounded-md border border-[var(--short)] px-3 py-2 text-[11px] text-[var(--short-ink)]">
          {error}
        </p>
      )}
      {notice && (
        <p className="rounded-md border border-[var(--border)] px-3 py-2 text-[11px] text-[var(--muted)]">
          {notice}
        </p>
      )}

      <ExperienceToolbar
        filters={filters}
        symbols={symbols}
        bars={BARS}
        utility={utilityFilter}
        onChange={setFilters}
        onUtilityChange={setUtilityFilter}
        onPrune={onPrune}
      />

      <Tabs tabs={TABS} active={tab} onChange={setTab} variant="underline" label="经验库视图" />

      {tab === "list" && (
        <ExperienceList
          items={visibleLessons}
          total={utilityFilter ? visibleLessons.length : (lessons?.total ?? 0)}
          loading={loading}
          onOpen={(node) => void openDetail(node.id)}
          onPin={onPin}
          onMute={onMute}
          onDelete={setPendingDelete}
        />
      )}
      {tab === "graph" && (
        <ExperienceGraphCanvas
          nodes={graph?.nodes ?? []}
          edges={graph?.edges ?? []}
          total={graph?.total ?? 0}
          truncated={graph?.truncated ?? false}
          limit={limit}
          onRaiseLimit={() => setLimit((prev) => Math.min(2000, prev * 2))}
          onOpen={(node) => void openDetail(node.id)}
          onFilterContext={(node) =>
            setFilters((prev) => ({ ...prev, axis: node.axis ?? undefined }))}
        />
      )}
      {tab === "utility" && (
        <ExperienceUtilityPanel
          page={utility}
          loading={loading}
          onOpen={(row) => void openDetail(row.id)}
          onRestore={(row) => act("恢复启用", async () => {
            // 走 `PATCH {muted:false}`：那条路径把 `mute_reason` 清成 null，此后自动化可以
            // 再次接管它。用户显式静音（`user`）与自动静音（`ineffective`）严格分离。
            await experienceApi.patch(row.id, { muted: false });
          })}
        />
      )}
      {tab === "preview" && <ExperienceRetrievalPreview symbols={symbols} bars={BARS} />}

      <ExperienceDetailDrawer
        detail={detail}
        open={detail !== null}
        onClose={() => setDetail(null)}
        onPin={() => detail && onPin(detail.node)}
        onMute={() => detail && onMute(detail.node)}
        onEdit={() => {
          if (!detail) return;
          setEditing(detail.node);
          setEditorError(null);
          setEditorOpen(true);
        }}
        onDelete={() => detail && setPendingDelete(detail.node)}
      />

      {editorOpen && (
        <ExperienceEditor
          open={editorOpen}
          editing={editing}
          symbols={symbols}
          bars={BARS}
          submitting={busy === "保存"}
          error={editorError}
          onClose={() => setEditorOpen(false)}
          onSubmit={(payload) => { void submitEditor(payload); }}
        />
      )}

      <ConfirmDialog
        open={pendingDelete !== null}
        title="删除这条经验？"
        message={`「${pendingDelete?.label ?? ""}」将被永久删除，它的边也会一并移除。此操作不可撤销。`}
        confirmText="永久删除"
        onCancel={() => setPendingDelete(null)}
        onConfirm={() => {
          const node = pendingDelete;
          setPendingDelete(null);
          if (!node) return;
          void act("删除", async () => {
            await experienceApi.remove(node.id);
            setDetail(null);
          });
        }}
      />

      <ExperiencePrunePrompt
        preview={prunePreview}
        onCancel={() => setPrunePreview(null)}
        onConfirm={() => {
          setPrunePreview(null);
          void act("清理沉降经验", async () => {
            const result = await experienceApi.prune(false);
            setNotice(`已删除 ${result.deleted} 条沉降经验`
              + `（跳过置顶 ${result.skippedPinned} 条 / 你手动静音的 ${result.skippedUserMuted} 条）。`);
          });
        }}
      />

      <ConfirmDialog
        open={pendingImport !== null}
        title="导入这些经验？"
        message={`即将导入 ${pendingImport?.nodes.length ?? 0} 条经验。`
          + (pendingImport?.mode === "replace"
            // ConfirmDialog 的 message 是纯文本渲染：写 `**粗体**` 只会让用户看到两颗星号
            // （本仓库已复发两次）。要强调就用词，不用 markdown。
            ? "注意：替换式导入会先删除你现有的全部手工与导入经验（统计经验不受影响），"
              + "删与写在同一个事务里，中途失败会整体回滚。"
            : "")
          + "导入的经验会进入 Agent 的上下文（起始档位恒为「候选」，不会自动成为铁律）；"
          + "文件里的边与事件一律不导入——边是本地派生物，事件带着别人的成本口径。"}
        danger={pendingImport?.mode === "replace"}
        confirmText={pendingImport?.mode === "replace" ? "替换并导入" : "导入"}
        onCancel={() => setPendingImport(null)}
        onConfirm={() => {
          const payload = pendingImport;
          setPendingImport(null);
          if (!payload) return;
          void act("导入", async () => {
            const result = await experienceApi.importNodes(payload);
            setNotice(`已导入 ${result.imported.nodes} 条；`
              + `被拒 ${result.rejected.nodes} 条经验 / ${result.rejected.edges} 条边 / `
              + `${result.rejected.events} 条事件。`
              // 合并掉的重复必须说出来：文件里 12 条、库里多了 10 条，用户有权知道差额去哪了。
              + (result.deduped > 0
                ? `另有 ${result.deduped} 条与文件内其它条目重复（同一句话 + 同一语境）已合并。`
                : ""));
          });
        }}
      />
    </div>
  );
}

function Stat({ label, value }: { label: string; value: number }) {
  return (
    <div className="flex flex-col">
      <span className="tabular text-lg font-semibold text-[var(--text)]">{value}</span>
      <span className="text-[10px] text-[var(--muted)]">{label}</span>
    </div>
  );
}

/**
 * 阈值面板。
 *
 * 保存后**立刻回读生效值**：「保存成功」与「真的生效了」不是一回事，而这块面板此前连一条读
 * 路径都没有——用户改得动、界面有反应、系统行为毫无变化，且不报任何错。
 */
function ThresholdPanel({
  settings,
  onSaved,
  onError,
}: {
  settings: ExperienceSettings;
  onSaved: (next: ExperienceSettings) => void;
  onError: (message: string) => void;
}) {
  const [draft, setDraft] = useState(settings);
  const [saving, setSaving] = useState(false);
  const rows: { key: keyof ExperienceSettings; label: string; hint?: string }[] = [
    { key: "minSamples", label: "常规样本门槛 n" },
    { key: "ironSamples", label: "铁律样本门槛 n" },
    { key: "tThreshold", label: "常规 |t| 门槛" },
    { key: "halfLifeDays", label: "时间衰减半衰期（天）" },
    { key: "staleDays", label: "沉降天数" },
    { key: "l0Max", label: "L0 条数上限" },
    { key: "l0MaxChars", label: "L0 字符上限", hint: `下限 ${settings.l0MinChars}（含框架句）` },
    { key: "l1Max", label: "L1 条数上限" },
    { key: "l1MaxChars", label: "L1 字符上限", hint: `下限 ${settings.l1MinChars}（含框架句）` },
    // ★ W-C · 失效判据的两道可调门槛。第三道（符号系统性相反）不可调——它是判据本身。
    { key: "utilityMinSamples", label: "失效判定的最少注入次数" },
    { key: "utilityTThreshold", label: "失效判定的 |t| 门槛" },
  ];
  return (
    <div className="flex flex-col gap-3 rounded-md border border-[var(--border)] p-3">
      <p className="text-[11px] leading-relaxed text-[var(--muted)]">
        这些阈值决定「一条统计结论要多少证据才算数」。改完请点上面的
        <strong className="text-[var(--text)]">「重建图谱」</strong>——阈值只在物化时被读取，
        既有经验的档位不会自己变。
        {!settings.enabled && "（经验模式已被运维总闸关闭，这些阈值当前不生效。）"}
      </p>
      <div className="grid grid-cols-2 gap-3 md:grid-cols-3">
        {rows.map((row) => (
          <Field key={String(row.key)} label={row.label} htmlFor={`thr-${String(row.key)}`} hint={row.hint}>
            <Input
              id={`thr-${String(row.key)}`}
              type="number"
              value={String(draft[row.key] ?? "")}
              onChange={(e) =>
                setDraft((prev) => ({ ...prev, [row.key]: Number(e.target.value) }))}
            />
          </Field>
        ))}
      </div>
      <div className="flex items-center justify-end gap-2">
        <Button variant="ghost" size="sm" onClick={() => setDraft(settings)} disabled={saving}>
          还原
        </Button>
        <Button
          variant="primary"
          size="sm"
          disabled={saving}
          onClick={async () => {
            setSaving(true);
            try {
              onSaved(await experienceApi.updateSettings({
                minSamples: draft.minSamples, ironSamples: draft.ironSamples,
                tThreshold: draft.tThreshold, halfLifeDays: draft.halfLifeDays,
                staleDays: draft.staleDays, l0Max: draft.l0Max, l0MaxChars: draft.l0MaxChars,
                l1Max: draft.l1Max, l1MaxChars: draft.l1MaxChars,
                utilityMinSamples: draft.utilityMinSamples,
                utilityTThreshold: draft.utilityTThreshold,
              }));
            } catch (err) {
              onError((err as Error).message || "保存阈值失败");
            } finally {
              setSaving(false);
            }
          }}
        >
          {saving ? "保存中…" : "保存阈值"}
        </Button>
      </div>
    </div>
  );
}
