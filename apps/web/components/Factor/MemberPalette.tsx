"use client";

import { useQuery } from "@tanstack/react-query";
import clsx from "clsx";
import { useEffect, useMemo, useState } from "react";
import { Badge, Button, Input, Select, Skeleton } from "@/components/ui";
import {
  candidateIdentity,
  categoryLabel,
  directionLabel,
  factorRowToMember,
  filterLibraryItems,
  formatIc,
  isCandidateSelected,
  libraryItemToMember,
  primaryRankIc,
} from "@/lib/factor";
import { factorApi } from "@/lib/factorApi";
import type { Factor, FactorLibraryItem, FactorMember, FrozenDirection } from "@/lib/factorTypes";

interface MemberPaletteProps {
  /** 因子库目录（父层已 useQuery 拿到，避免子层重复请求）。 */
  library: FactorLibraryItem[];
  /** 当前已选成员（单一真相，来自 FactorSetBuilder.members）。 */
  members: FactorMember[];
  /** 切换某候选入选/移除（命中 candidateIdentity 即移除该因子，否则以默认方向追加；追加受上限保护，评审 R1）。 */
  onToggle: (member: FactorMember) => void;
  /** 批量并入候选（去重 + 截断到 maxMembers），用于「全选当前结果」。 */
  onAddMany: (members: FactorMember[]) => void;
  /** 清空全部已选。 */
  onClear: () => void;
  /** 按下标移除单个已选成员（沿用既有语义）。 */
  onRemove: (index: number) => void;
  /** 切换某已选成员方向（沿用既有语义）。 */
  onDirection: (index: number, direction: FrozenDirection) => void;
  /** 成员数上限（默认 12，与 validateMembers 同口径）。 */
  maxMembers?: number;
}

/**
 * 组合成员双栏多选：左「候选调色板」点击切换式多选（可搜索 / 类别过滤 / 全选当前结果），
 * 右「已选篮子」实时回显 + 方向切换 / 移除 / 清空。候选已选态按 candidateIdentity（忽略 direction）
 * 派生自单一真相 members，杜绝两份状态漂移（评审 R1）。
 */
export default function MemberPalette({
  library,
  members,
  onToggle,
  onAddMany,
  onClear,
  onRemove,
  onDirection,
  maxMembers = 12,
}: MemberPaletteProps) {
  const cn = (key: string) => library.find((f) => f.key === key)?.cn ?? key;
  return (
    <div className="card flex flex-col gap-3 p-3">
      <span className="text-sm font-semibold text-[var(--text)]">
        组合成员（已选 {members.length} / 上限 {maxMembers}）
      </span>
      <div className="flex flex-col gap-4 lg:flex-row">
        <CandidatePalette
          library={library}
          members={members}
          onToggle={onToggle}
          onAddMany={onAddMany}
          maxMembers={maxMembers}
        />
        <SelectedBasket
          members={members}
          cn={cn}
          onRemove={onRemove}
          onDirection={onDirection}
          onClear={onClear}
        />
      </div>
    </div>
  );
}

// 归一化候选描述：库目录条目与「我的因子」行统一成同一形状供 CandidateChip 消费。
interface Candidate {
  uid: string; // React key（库 key / 因子 id 各自唯一，避免 candidateIdentity 撞键）
  member: FactorMember; // 点击时切换的成员
  label: string;
  factorKey: string;
  category: string;
  direction: FrozenDirection;
  rankIc?: number | null; // 仅「我的因子」展示主 horizon RankIC（undefined=不展示）
  intradayOnly?: boolean;
  evalMissing?: boolean; // factor.eval==null → RankIC title 提示重新评估
}

function CandidatePalette({
  library,
  members,
  onToggle,
  onAddMany,
  maxMembers,
}: {
  library: FactorLibraryItem[];
  members: FactorMember[];
  onToggle: (member: FactorMember) => void;
  onAddMany: (members: FactorMember[]) => void;
  maxMembers: number;
}) {
  const [source, setSource] = useState<"library" | "mine">("library");
  const [searchInput, setSearchInput] = useState("");
  const [q, setQ] = useState("");
  const [category, setCategory] = useState("");

  // 搜索防抖：输入停 300ms 后才下发查询键 / 库目录过滤（库与「我的因子」共用同一 q，体验一致）。
  useEffect(() => {
    const timer = setTimeout(() => setQ(searchInput.trim()), 300);
    return () => clearTimeout(timer);
  }, [searchInput]);

  // queryKey 含全部入参（评审 R2）：q/category 变化即重新拉取，不命中陈旧缓存；仅 mine 源激活时请求。
  const mine = useQuery({
    queryKey: ["factor", "list", { enabled: true, q, category, sort: "rankic" }],
    queryFn: () =>
      factorApi.list({ enabled: true, q: q || undefined, category: category || undefined, sort: "rankic" }),
    enabled: source === "mine",
  });

  const categories = useMemo(() => uniqueCategories(library), [library]);
  const intradayKeys = useMemo(
    () => new Set(library.filter((f) => f.intradayOnly).map((f) => f.key)),
    [library],
  );
  const candidates = useMemo(
    () => buildCandidates(source, library, mine.data ?? [], q, category, intradayKeys),
    [source, library, mine.data, q, category, intradayKeys],
  );

  const full = members.length >= maxMembers;
  const addable = candidates.filter((c) => !isCandidateSelected(members, c.member));

  return (
    <div className="flex min-w-0 flex-col gap-2 lg:flex-[3]">
      <div className="flex flex-wrap items-center gap-2">
        <SourceTabs source={source} onChange={setSource} />
        <Input
          value={searchInput}
          onChange={(e) => setSearchInput(e.target.value)}
          placeholder="搜索名称 / key…"
          className="h-7 flex-1 py-1 text-xs"
          style={{ minWidth: 120 }}
        />
      </div>
      <CategoryChips categories={categories} active={category} onChange={setCategory} />
      <CandidateBody
        source={source}
        loading={mine.isLoading}
        error={mine.isError}
        onRetry={() => void mine.refetch()}
        hasFilter={!!q || !!category}
        candidates={candidates}
        members={members}
        full={full}
        onToggle={onToggle}
      />
      <div className="flex items-center justify-between gap-2">
        <Button
          variant="default"
          size="sm"
          disabled={addable.length === 0 || full}
          title={full ? "已达成员上限" : "把当前筛选结果一次性并入（自动去重 + 截断到上限）"}
          onClick={() => onAddMany(addable.map((c) => c.member))}
        >
          全选当前结果（{addable.length}）
        </Button>
        <span className="text-[11px] text-[var(--muted)]">共 {candidates.length} 个候选</span>
      </div>
    </div>
  );
}

// 候选区主体：加载 / 错误 / 空激活 / 无匹配 / 候选网格五态（不静默空白，延续因子层「显式可预期」约定）。
function CandidateBody({
  source,
  loading,
  error,
  onRetry,
  hasFilter,
  candidates,
  members,
  full,
  onToggle,
}: {
  source: "library" | "mine";
  loading: boolean;
  error: boolean;
  onRetry: () => void;
  hasFilter: boolean;
  candidates: Candidate[];
  members: FactorMember[];
  full: boolean;
  onToggle: (member: FactorMember) => void;
}) {
  if (source === "mine" && loading) return <Skeleton className="h-40" />;
  if (source === "mine" && error) {
    return (
      <div className="flex flex-col items-center gap-2 rounded-[10px] border border-[var(--border)] py-6 text-center">
        <p className="text-[11px] text-[var(--short)]">加载因子失败，请重试</p>
        <Button variant="default" size="sm" onClick={onRetry}>
          重试
        </Button>
      </div>
    );
  }
  if (candidates.length === 0) {
    const empty =
      source === "mine" && !hasFilter
        ? "暂无激活因子，去「我的因子」启用或在「因子挖掘」保存结果。"
        : "无匹配候选，调整搜索 / 类别。";
    return (
      <p className="rounded-[10px] border border-dashed border-[var(--border)] px-3 py-6 text-center text-[11px] text-[var(--muted)]">
        {empty}
      </p>
    );
  }
  return (
    <div className="grid max-h-72 grid-cols-1 gap-2 overflow-y-auto pr-1 sm:grid-cols-2 xl:grid-cols-3">
      {candidates.map((c) => {
        const selected = isCandidateSelected(members, c.member);
        return (
          <CandidateChip
            key={c.uid}
            label={c.label}
            factorKey={c.factorKey}
            category={c.category}
            direction={c.direction}
            rankIc={c.rankIc}
            intradayOnly={c.intradayOnly}
            evalMissing={c.evalMissing}
            selected={selected}
            disabled={!selected && full}
            onClick={() => onToggle(c.member)}
          />
        );
      })}
    </div>
  );
}

function CandidateChip({
  label,
  factorKey,
  category,
  direction,
  rankIc,
  intradayOnly,
  evalMissing,
  selected,
  disabled,
  onClick,
}: {
  label: string;
  factorKey: string;
  category: string;
  direction: FrozenDirection;
  rankIc?: number | null;
  intradayOnly?: boolean;
  evalMissing?: boolean;
  selected: boolean;
  disabled: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      aria-pressed={selected}
      disabled={disabled}
      onClick={onClick}
      title={disabled ? "已达成员上限，移除其他成员后再添加" : undefined}
      className={clsx(
        "relative flex flex-col gap-1 rounded-[10px] border p-2 text-left transition-all duration-150 ease-smooth",
        "disabled:cursor-not-allowed disabled:opacity-40",
        selected
          ? "border-[var(--accent)] bg-[var(--accent-soft)]"
          : "border-[var(--border)] bg-[var(--surface)] hover:border-[var(--accent)]",
      )}
    >
      {selected && (
        <span
          aria-hidden
          className="absolute right-1.5 top-1.5 flex h-4 w-4 items-center justify-center rounded-full bg-[var(--accent)] text-[10px] font-bold leading-none text-white"
        >
          ✓
        </span>
      )}
      <span className="truncate pr-5 text-xs font-medium text-[var(--text)]">{label}</span>
      <code className="truncate text-[10px] text-[var(--muted)]">{factorKey}</code>
      <div className="flex flex-wrap items-center gap-1">
        <Badge tone="neutral">{categoryLabel(category)}</Badge>
        <Badge tone={direction === "long" ? "long" : "short"}>{directionLabel(direction)}</Badge>
        {intradayOnly && <Badge tone="neutral">仅日内</Badge>}
        {rankIc !== undefined && (
          <span
            className="ml-auto text-[10px] tabular text-[var(--muted)]"
            title={evalMissing ? "参数/方向已变更，请重新评估" : undefined}
          >
            IC {formatIc(rankIc)}
          </span>
        )}
      </div>
    </button>
  );
}

function SelectedBasket({
  members,
  cn,
  onRemove,
  onDirection,
  onClear,
}: {
  members: FactorMember[];
  cn: (factorKey: string) => string;
  onRemove: (index: number) => void;
  onDirection: (index: number, direction: FrozenDirection) => void;
  onClear: () => void;
}) {
  return (
    <div className="flex min-w-0 flex-col gap-2 lg:flex-[2]">
      <div className="flex items-center justify-between gap-2">
        <span className="text-[11px] font-medium text-[var(--muted)]">已选成员（{members.length}）</span>
        <Button variant="ghost" size="sm" disabled={members.length === 0} onClick={onClear}>
          清空已选
        </Button>
      </div>
      {members.length === 0 ? (
        <p className="rounded-[10px] border border-dashed border-[var(--border)] px-3 py-6 text-center text-[11px] text-[var(--muted)]">
          从左侧点选，或在「因子评估」/「因子挖掘」中「加入组合」。
        </p>
      ) : (
        <ul className="flex max-h-72 flex-col gap-1.5 overflow-y-auto pr-1">
          {members.map((m, i) => (
            <li
              key={`${m.factorKey}-${i}`}
              className="flex items-center gap-2 rounded-[8px] bg-[var(--surface-2)] px-2.5 py-1.5"
            >
              <span className="w-5 shrink-0 text-center text-[10px] tabular text-[var(--muted)]">{i + 1}</span>
              <span className="flex-1 truncate text-xs font-medium text-[var(--text)]">{cn(m.factorKey)}</span>
              <code className="hidden text-[10px] text-[var(--muted)] sm:inline">{m.factorKey}</code>
              <Select
                value={m.direction}
                onChange={(e) => onDirection(i, e.target.value as FrozenDirection)}
                className="px-1.5 py-0.5 text-[11px]"
              >
                <option value="long">看多</option>
                <option value="short">看空</option>
              </Select>
              <Button variant="danger" size="sm" onClick={() => onRemove(i)}>
                移除
              </Button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function SourceTabs({
  source,
  onChange,
}: {
  source: "library" | "mine";
  onChange: (next: "library" | "mine") => void;
}) {
  return (
    <div className="flex gap-0.5 rounded-[8px] bg-[var(--surface-2)] p-0.5">
      {(
        [
          ["library", "库目录"],
          ["mine", "我的因子"],
        ] as const
      ).map(([val, label]) => (
        <button
          key={val}
          type="button"
          onClick={() => onChange(val)}
          className={clsx(
            "rounded-[6px] px-2 py-0.5 text-[11px] font-medium transition-all",
            source === val
              ? "bg-[var(--accent-soft)] text-[var(--accent)]"
              : "text-[var(--muted)] hover:text-[var(--text)]",
          )}
        >
          {label}
        </button>
      ))}
    </div>
  );
}

function CategoryChips({
  categories,
  active,
  onChange,
}: {
  categories: string[];
  active: string;
  onChange: (category: string) => void;
}) {
  if (categories.length === 0) return null;
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      {categories.map((cat) => (
        <button
          key={cat}
          type="button"
          aria-pressed={active === cat}
          onClick={() => onChange(active === cat ? "" : cat)}
          className={clsx(
            "rounded-full px-2.5 py-0.5 text-[11px] font-medium transition-colors",
            active === cat
              ? "bg-[var(--accent-soft)] text-[var(--accent)]"
              : "bg-[var(--surface-2)] text-[var(--muted)] hover:text-[var(--text)]",
          )}
        >
          {categoryLabel(cat)}
        </button>
      ))}
      {active && (
        <button type="button" onClick={() => onChange("")} className="text-[11px] text-[var(--muted)] underline">
          清除
        </button>
      )}
    </div>
  );
}

function uniqueCategories(items: FactorLibraryItem[]): string[] {
  return Array.from(new Set(items.map((i) => i.category)));
}

// 库目录（客户端过滤）/「我的因子」（服务端已过滤）→ 统一候选描述。
function buildCandidates(
  source: "library" | "mine",
  library: FactorLibraryItem[],
  mineFactors: Factor[],
  q: string,
  category: string,
  intradayKeys: Set<string>,
): Candidate[] {
  if (source === "library") {
    return filterLibraryItems(library, q, category).map((item) => {
      const member = libraryItemToMember(item);
      return {
        uid: `lib:${item.key}`,
        member,
        label: item.cn,
        factorKey: item.key,
        category: item.category,
        direction: member.direction,
        intradayOnly: item.intradayOnly,
      };
    });
  }
  return mineFactors.map((f) => {
    const member = factorRowToMember(f, library);
    return {
      uid: `mine:${f.id}`,
      member,
      label: f.name,
      factorKey: f.factorKey,
      category: f.category,
      direction: member.direction,
      rankIc: f.eval ? primaryRankIc(f.eval.horizons) : null,
      intradayOnly: intradayKeys.has(f.factorKey),
      evalMissing: f.eval == null,
    };
  });
}
