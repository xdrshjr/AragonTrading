"use client";

import clsx from "clsx";
import { useEffect, useMemo, useState } from "react";
import { Badge, Button, Input, Modal } from "@/components/ui";
import ConfirmDialog from "@/components/ui/ConfirmDialog";
import { useAppStore } from "@/lib/store";
import type { SymbolGroup } from "@/lib/factorTypes";

// 标的组名 / 成员上限（与后端 sanitizer 常量对齐：_GROUP_NAME_MAX / _GROUP_SYMBOLS_MAX，前端先行提示更友好）。
const GROUP_NAME_MAX = 64;
const GROUP_SYMBOLS_MAX = 100;

function nowTs(): number {
  return Math.floor(Date.now() / 1000);
}

function newId(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return `g_${crypto.randomUUID()}`;
  }
  return `g_${nowTs()}_${Math.floor(Math.random() * 1e6)}`;
}

// 编辑草稿：id=null 表示新建（未落库）；id=string 表示编辑既有组。symbols 有序（[0]=焦点/方向锚点）。
interface Draft {
  id: string | null;
  name: string;
  symbols: string[];
}

/**
 * 标的组增删改查弹窗（factor-lab-symbol-groups §六）：左栏组列表 + 新建；右栏编辑器（名称 + 自选标的 chip 挑选器）。
 * 成员候选来自 `store.watchlist`（仅复用 chip-toggle 交互范式，**不**复用因子库 MemberPalette，C-4）；成员有序，
 * 可置顶选定焦点 / 方向锚点。已移出自选的既有成员灰显「已移出自选」（可移除不可再加，§3.2 决策）。
 */
export default function SymbolGroupModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const watchlist = useAppStore((s) => s.watchlist);
  const groups = useAppStore((s) => s.symbolGroups);
  const addSymbolGroup = useAppStore((s) => s.addSymbolGroup);
  const updateSymbolGroup = useAppStore((s) => s.updateSymbolGroup);
  const removeSymbolGroup = useAppStore((s) => s.removeSymbolGroup);

  const [draft, setDraft] = useState<Draft | null>(null);
  const [search, setSearch] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [pendingDelete, setPendingDelete] = useState<SymbolGroup | null>(null);

  // 关闭即重置本地编辑态（下次打开为干净状态，避免残留草稿）。
  useEffect(() => {
    if (!open) {
      setDraft(null);
      setSearch("");
      setError(null);
      setPendingDelete(null);
    }
  }, [open]);

  const startNew = () => {
    setDraft({ id: null, name: "", symbols: [] });
    setSearch("");
    setError(null);
  };

  const startEdit = (group: SymbolGroup) => {
    setDraft({ id: group.id, name: group.name, symbols: [...group.symbols] });
    setSearch("");
    setError(null);
  };

  const toggleSymbol = (inst: string) =>
    setDraft((d) => {
      if (!d) return d;
      if (d.symbols.includes(inst)) {
        return { ...d, symbols: d.symbols.filter((s) => s !== inst) };
      }
      if (d.symbols.length >= GROUP_SYMBOLS_MAX) return d;
      return { ...d, symbols: [...d.symbols, inst] };
    });

  const removeSymbol = (inst: string) =>
    setDraft((d) => (d ? { ...d, symbols: d.symbols.filter((s) => s !== inst) } : d));

  const moveToTop = (index: number) =>
    setDraft((d) => {
      if (!d || index <= 0) return d;
      const next = [...d.symbols];
      const [picked] = next.splice(index, 1);
      next.unshift(picked);
      return { ...d, symbols: next };
    });

  const save = () => {
    if (!draft) return;
    const name = draft.name.trim().slice(0, GROUP_NAME_MAX);
    const symbols = draft.symbols.map((s) => s.trim()).filter(Boolean);
    if (!name) {
      setError("请填写标的组名称");
      return;
    }
    if (symbols.length === 0) {
      setError("至少选择 1 个标的");
      return;
    }
    if (draft.id === null) {
      const ts = nowTs();
      const group: SymbolGroup = { id: newId(), name, symbols, createdTs: ts, updatedTs: ts };
      addSymbolGroup(group);
      setDraft({ id: group.id, name, symbols }); // 保持编辑态指向刚建的组
    } else {
      updateSymbolGroup(draft.id, { name, symbols, updatedTs: nowTs() });
    }
    setError(null);
  };

  const confirmDelete = () => {
    if (!pendingDelete) return;
    removeSymbolGroup(pendingDelete.id);
    if (draft?.id === pendingDelete.id) setDraft(null);
    setPendingDelete(null);
  };

  return (
    <Modal open={open} onClose={onClose} labelledBy="symbol-group-modal-title" className="max-w-3xl">
      <div className="flex items-center justify-between border-b border-[var(--border)] px-5 py-3">
        <h2 id="symbol-group-modal-title" className="text-sm font-semibold text-[var(--text)]">
          管理标的组
        </h2>
        <Button variant="ghost" size="sm" onClick={onClose} aria-label="关闭">关闭</Button>
      </div>

      {watchlist.length === 0 ? (
        <div className="flex flex-col items-center gap-2 px-5 py-12 text-center">
          <span className="text-sm font-medium text-[var(--text)]">尚无自选标的</span>
          <span className="text-[12px] text-[var(--muted)]">
            标的组的成员来自你的自选标的，请先在交易工作台添加自选标的后再创建标的组。
          </span>
        </div>
      ) : (
        <div className="flex min-h-[380px] flex-col gap-0 md:flex-row">
          <GroupList
            groups={groups}
            activeId={draft?.id ?? null}
            onNew={startNew}
            onEdit={startEdit}
            onDelete={setPendingDelete}
          />
          <div className="min-w-0 flex-1 overflow-y-auto p-4">
            {draft ? (
              <GroupEditor
                draft={draft}
                watchlist={watchlist}
                search={search}
                error={error}
                onName={(name) => setDraft((d) => (d ? { ...d, name } : d))}
                onSearch={setSearch}
                onToggle={toggleSymbol}
                onRemove={removeSymbol}
                onMoveTop={moveToTop}
                onSave={save}
                onCancel={() => setDraft(null)}
              />
            ) : (
              <div className="flex h-full flex-col items-center justify-center gap-2 text-center">
                <span className="text-[13px] font-medium text-[var(--muted)]">
                  {groups.length === 0 ? "还没有标的组" : "选择左侧标的组编辑"}
                </span>
                <Button variant="primary" size="sm" onClick={startNew}>
                  {groups.length === 0 ? "新建你的第一个标的组" : "新建标的组"}
                </Button>
              </div>
            )}
          </div>
        </div>
      )}

      <ConfirmDialog
        open={pendingDelete !== null}
        title="删除标的组"
        message={pendingDelete ? `确定删除标的组「${pendingDelete.name}」？此操作不可撤销。` : ""}
        confirmText="删除"
        onConfirm={confirmDelete}
        onCancel={() => setPendingDelete(null)}
      />
    </Modal>
  );
}

function GroupList({
  groups,
  activeId,
  onNew,
  onEdit,
  onDelete,
}: {
  groups: SymbolGroup[];
  activeId: string | null;
  onNew: () => void;
  onEdit: (group: SymbolGroup) => void;
  onDelete: (group: SymbolGroup) => void;
}) {
  return (
    <div className="flex w-full shrink-0 flex-col gap-2 border-b border-[var(--border)] p-4 md:w-56 md:border-b-0 md:border-r">
      <Button variant="primary" size="sm" onClick={onNew}>+ 新建标的组</Button>
      {groups.length === 0 ? (
        <p className="rounded-[10px] border border-dashed border-[var(--border)] px-3 py-6 text-center text-[11px] text-[var(--muted)]">
          还没有标的组，点击上方新建。
        </p>
      ) : (
        <ul className="flex max-h-[300px] flex-col gap-1 overflow-y-auto pr-0.5">
          {groups.map((g) => (
            <li
              key={g.id}
              className={clsx(
                "flex items-center gap-1 rounded-[10px] px-2 py-1.5 transition-colors",
                activeId === g.id ? "bg-[var(--accent-soft)]" : "hover:bg-[var(--surface-2)]",
              )}
            >
              <button
                type="button"
                onClick={() => onEdit(g)}
                aria-current={activeId === g.id ? "true" : undefined}
                className="flex min-w-0 flex-1 flex-col text-left"
              >
                <span className="truncate text-xs font-medium text-[var(--text)]">{g.name}</span>
                <span className="text-[10px] text-[var(--muted)]">{g.symbols.length} 标的</span>
              </button>
              <Button variant="ghost" size="sm" onClick={() => onDelete(g)} aria-label={`删除 ${g.name}`}>
                删除
              </Button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function GroupEditor({
  draft,
  watchlist,
  search,
  error,
  onName,
  onSearch,
  onToggle,
  onRemove,
  onMoveTop,
  onSave,
  onCancel,
}: {
  draft: Draft;
  watchlist: string[];
  search: string;
  error: string | null;
  onName: (name: string) => void;
  onSearch: (q: string) => void;
  onToggle: (inst: string) => void;
  onRemove: (inst: string) => void;
  onMoveTop: (index: number) => void;
  onSave: () => void;
  onCancel: () => void;
}) {
  const needle = search.trim().toLowerCase();
  const candidates = useMemo(
    () => watchlist.filter((id) => !needle || id.toLowerCase().includes(needle)),
    [watchlist, needle],
  );
  const selectedSet = useMemo(() => new Set(draft.symbols), [draft.symbols]);
  const watchlistSet = useMemo(() => new Set(watchlist), [watchlist]);
  const full = draft.symbols.length >= GROUP_SYMBOLS_MAX;

  return (
    <div className="flex flex-col gap-3">
      <label className="flex flex-col gap-1">
        <span className="text-[11px] text-[var(--muted)]">标的组名称</span>
        <Input
          value={draft.name}
          onChange={(e) => onName(e.target.value)}
          placeholder="例如：主流币 / 山寨轮动"
          maxLength={GROUP_NAME_MAX}
          className="h-8 py-1 text-xs"
        />
      </label>

      <div className="flex flex-col gap-2">
        <span className="text-[11px] font-medium text-[var(--muted)]">
          从自选标的挑选成员（已选 {draft.symbols.length} / 上限 {GROUP_SYMBOLS_MAX}）
        </span>
        <Input
          value={search}
          onChange={(e) => onSearch(e.target.value)}
          placeholder="搜索自选标的…"
          className="h-7 py-1 text-xs"
        />
        <div className="grid max-h-40 grid-cols-2 gap-1.5 overflow-y-auto pr-1 sm:grid-cols-3">
          {candidates.map((id) => {
            const selected = selectedSet.has(id);
            return (
              <button
                key={id}
                type="button"
                aria-pressed={selected}
                disabled={!selected && full}
                onClick={() => onToggle(id)}
                title={!selected && full ? "已达成员上限" : undefined}
                className={clsx(
                  "relative truncate rounded-[8px] border px-2 py-1.5 text-left text-[11px] font-medium transition-all",
                  "disabled:cursor-not-allowed disabled:opacity-40",
                  selected
                    ? "border-[var(--accent)] bg-[var(--accent-soft)] text-[var(--accent)]"
                    : "border-[var(--border)] bg-[var(--surface)] text-[var(--text)] hover:border-[var(--accent)]",
                )}
              >
                {selected && (
                  <span
                    aria-hidden
                    className="absolute right-1 top-1 flex h-3.5 w-3.5 items-center justify-center rounded-full bg-[var(--accent)] text-[9px] font-bold leading-none text-white"
                  >
                    ✓
                  </span>
                )}
                <span className="truncate pr-3">{id}</span>
              </button>
            );
          })}
          {candidates.length === 0 && (
            <p className="col-span-full rounded-[8px] border border-dashed border-[var(--border)] px-2 py-4 text-center text-[11px] text-[var(--muted)]">
              无匹配的自选标的。
            </p>
          )}
        </div>
      </div>

      <div className="flex flex-col gap-1.5">
        <span className="text-[11px] font-medium text-[var(--muted)]">
          组内成员（有序，首个为焦点标的 / 方向锚点）
        </span>
        {draft.symbols.length === 0 ? (
          <p className="rounded-[10px] border border-dashed border-[var(--border)] px-3 py-4 text-center text-[11px] text-[var(--muted)]">
            从上方自选标的点选加入。
          </p>
        ) : (
          <ul className="flex max-h-40 flex-col gap-1 overflow-y-auto pr-1">
            {draft.symbols.map((inst, i) => {
              const removedFromWatchlist = !watchlistSet.has(inst);
              return (
                <li
                  key={inst}
                  className={clsx(
                    "flex items-center gap-2 rounded-[8px] px-2.5 py-1.5",
                    removedFromWatchlist ? "bg-[var(--surface)] opacity-70" : "bg-[var(--surface-2)]",
                  )}
                >
                  <span className="w-5 shrink-0 text-center text-[10px] tabular text-[var(--muted)]">{i + 1}</span>
                  <span className="flex-1 truncate text-xs font-medium text-[var(--text)]">{inst}</span>
                  {i === 0 && <Badge tone="accent">焦点</Badge>}
                  {removedFromWatchlist && <Badge tone="short">已移出自选</Badge>}
                  {i > 0 && (
                    <Button variant="ghost" size="sm" onClick={() => onMoveTop(i)} title="置顶为焦点">
                      置顶
                    </Button>
                  )}
                  <Button variant="danger" size="sm" onClick={() => onRemove(inst)}>移除</Button>
                </li>
              );
            })}
          </ul>
        )}
      </div>

      {error && <p className="text-[11px] text-[var(--short)]">{error}</p>}

      <div className="flex items-center justify-end gap-2 pt-1">
        <Button variant="ghost" size="sm" onClick={onCancel}>取消</Button>
        <Button variant="primary" size="sm" onClick={onSave}>保存</Button>
      </div>
    </div>
  );
}
