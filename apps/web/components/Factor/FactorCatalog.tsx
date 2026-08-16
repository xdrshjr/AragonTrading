"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { Badge, Button, Skeleton } from "@/components/ui";
import { categoryLabel, directionLabel } from "@/lib/factor";
import { factorApi } from "@/lib/factorApi";
import type { FactorLibraryItem } from "@/lib/factorTypes";

/** 因子目录：库浏览（按类别分组）+「加入我的因子」（落 source=custom 行）。 */
export default function FactorCatalog() {
  const qc = useQueryClient();
  const [notice, setNotice] = useState<string | null>(null);
  const lib = useQuery({ queryKey: ["factor", "library"], queryFn: factorApi.library });

  const add = useMutation({
    mutationFn: (item: FactorLibraryItem) =>
      factorApi.create({ factorKey: item.key, name: item.cn, direction: item.directionHint }),
    onSuccess: (created) => {
      setNotice(`已加入「${created.name}」到我的因子`);
      void qc.invalidateQueries({ queryKey: ["factor", "list"] });
    },
    onError: () => setNotice("加入失败，请稍后重试"),
  });

  const groups = useMemo(() => groupByCategory(lib.data ?? []), [lib.data]);

  if (lib.isLoading) {
    return (
      <div className="flex flex-col gap-3">
        <Skeleton className="h-24" />
        <Skeleton className="h-24" />
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-5 pb-6">
      <Header total={lib.data?.length ?? 0} />
      {notice && (
        <p role="status" aria-live="polite" className="text-[11px] font-medium text-[var(--accent-ink)]">
          {notice}
        </p>
      )}
      {groups.map(({ category, items }) => (
        <section key={category} className="flex flex-col gap-2">
          <h2 className="text-sm font-semibold text-[var(--text)]">{categoryLabel(category)}</h2>
          <div className="grid grid-cols-1 gap-2 lg:grid-cols-2">
            {items.map((item) => (
              <article
                key={item.key}
                className="card flex items-start justify-between gap-3 p-3"
              >
                <div className="flex min-w-0 flex-col gap-1">
                  <div className="flex items-center gap-2">
                    <span className="truncate text-sm font-medium text-[var(--text)]">{item.cn}</span>
                    <Badge tone={item.directionHint === "long" ? "long" : "short"}>
                      {directionLabel(item.directionHint)}
                    </Badge>
                    {item.intradayOnly && <Badge tone="neutral">仅日内</Badge>}
                  </div>
                  <code className="text-[11px] text-[var(--muted)]">{item.key}</code>
                </div>
                <Button
                  variant="default"
                  size="sm"
                  disabled={add.isPending}
                  onClick={() => add.mutate(item)}
                >
                  加入
                </Button>
              </article>
            ))}
          </div>
        </section>
      ))}
    </div>
  );
}

function Header({ total }: { total: number }) {
  return (
    <div className="card flex items-center justify-between gap-3 p-4">
      <div className="flex flex-col gap-0.5">
        <span className="text-[11px] font-medium uppercase tracking-wide text-[var(--muted)]">
          因子库
        </span>
        <span className="text-sm font-semibold text-[var(--text)]">共 {total} 个内置因子</span>
      </div>
      <span className="text-[11px] text-[var(--muted)]">
        全部严格因果、相对量纲；「加入」后可在「我的因子」中编辑参数 / 方向 / 激活态。
      </span>
    </div>
  );
}

function groupByCategory(items: FactorLibraryItem[]): { category: string; items: FactorLibraryItem[] }[] {
  const order: string[] = [];
  const byCat = new Map<string, FactorLibraryItem[]>();
  for (const item of items) {
    const bucket = byCat.get(item.category);
    if (bucket) bucket.push(item);
    else {
      byCat.set(item.category, [item]);
      order.push(item.category);
    }
  }
  return order.map((category) => ({ category, items: byCat.get(category)! }));
}
