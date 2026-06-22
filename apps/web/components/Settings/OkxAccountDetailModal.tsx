"use client";

import { useQuery } from "@tanstack/react-query";
import { Badge, Button, Modal, Skeleton } from "@/components/ui";
import { accountApi } from "@/lib/accountApi";
import type { OkxAccount, OkxAccountBalanceRow, OkxAccountPosition } from "@/lib/accountTypes";
import { permissionLabel } from "@/lib/accountSettings";
import { fmtNum, fmtPrice } from "@/lib/format";

/**
 * 账户详情面板（P1-Modal）：消费 `ui` 的 `Modal` 原语，懒查 detail 接口，渲染信息 + 资产 + 持仓。
 * 加载 `Skeleton`、失败显式错误态（不假数据，C1）；资产 / 持仓抽子组件守 800/50 行阈值（P2-Size）。
 */
export default function OkxAccountDetailModal({
  account,
  onClose,
}: {
  account: OkxAccount;
  onClose: () => void;
}) {
  const titleId = `okx-detail-${account.id}`;
  const { data, isLoading, isError, error, refetch } = useQuery({
    queryKey: ["okx", "accounts", "detail", account.id],
    queryFn: () => accountApi.accounts.detail(account.id),
    staleTime: 0,
    retry: false,
  });

  return (
    <Modal open onClose={onClose} labelledBy={titleId}>
      <header className="flex items-center justify-between gap-3 border-b border-[var(--border)] px-5 py-3.5">
        <div className="flex min-w-0 items-center gap-2">
          <h2 id={titleId} className="truncate text-sm font-semibold text-[var(--text)]">
            {account.label}
          </h2>
          {account.isDefault && <Badge tone="accent">默认</Badge>}
        </div>
        <Button variant="ghost" size="sm" onClick={onClose} aria-label="关闭详情">
          关闭
        </Button>
      </header>

      <div className="flex flex-col gap-5 overflow-y-auto px-5 py-4">
        {isLoading ? (
          <DetailSkeleton />
        ) : isError || !data ? (
          <ErrorState message={(error as Error)?.message} onRetry={() => void refetch()} />
        ) : (
          <>
            <InfoSection info={data.info} />
            <BalanceSection totalEqUsd={data.balance.totalEqUsd} details={data.balance.details} />
            <PositionsSection positions={data.positions} />
          </>
        )}
      </div>
    </Modal>
  );
}

function DetailSkeleton() {
  return (
    <div className="flex flex-col gap-3">
      <Skeleton className="h-16" />
      <Skeleton className="h-24" />
      <Skeleton className="h-24" />
    </div>
  );
}

function ErrorState({ message, onRetry }: { message?: string; onRetry: () => void }) {
  return (
    <div className="flex items-center justify-between gap-3 rounded-[10px] border border-[var(--border)] p-4 text-sm text-[var(--short)]">
      <span>加载详情失败：{message || "OKX 不可达或鉴权失败"}</span>
      <Button variant="ghost" size="sm" onClick={onRetry}>
        重试
      </Button>
    </div>
  );
}

function InfoSection({ info }: { info: { uid: string; acctLevelLabel: string; permissions: string[] } }) {
  return (
    <section className="flex flex-col gap-2">
      <h3 className="text-xs font-semibold uppercase tracking-wide text-[var(--muted)]">账户信息</h3>
      <div className="flex flex-wrap items-center gap-x-6 gap-y-1.5">
        <InfoItem label="UID" value={info.uid || "—"} />
        <InfoItem label="账户层级" value={info.acctLevelLabel} />
        <div className="flex items-center gap-1.5">
          <span className="text-[11px] text-[var(--muted)]">权限</span>
          <div className="flex flex-wrap gap-1">
            {info.permissions.length > 0 ? (
              info.permissions.map((p) => (
                <Badge key={p} tone="neutral">
                  {permissionLabel(p)}
                </Badge>
              ))
            ) : (
              <span className="text-[11px] text-[var(--muted)]">—</span>
            )}
          </div>
        </div>
      </div>
    </section>
  );
}

function InfoItem({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center gap-1.5">
      <span className="text-[11px] text-[var(--muted)]">{label}</span>
      <span className="tabular text-sm font-medium text-[var(--text)]">{value}</span>
    </div>
  );
}

function BalanceSection({
  totalEqUsd,
  details,
}: {
  totalEqUsd: number | null;
  details: OkxAccountBalanceRow[];
}) {
  const rows = details.filter((d) => (d.eq ?? 0) !== 0 || (d.eqUsd ?? 0) !== 0);
  return (
    <section className="flex flex-col gap-2">
      <h3 className="text-xs font-semibold uppercase tracking-wide text-[var(--muted)]">资产</h3>
      <div className="flex items-baseline gap-2">
        <span className="tabular text-2xl font-semibold text-[var(--text)]">
          {totalEqUsd === null ? "—" : `$${fmtNum(totalEqUsd)}`}
        </span>
        <span className="text-[11px] text-[var(--muted)]">总权益（USD）</span>
      </div>
      {rows.length > 0 ? <BalanceTable rows={rows} /> : (
        <p className="text-[11px] text-[var(--muted)]">暂无分币种余额</p>
      )}
    </section>
  );
}

function BalanceTable({ rows }: { rows: OkxAccountBalanceRow[] }) {
  return (
    <table className="w-full text-left text-xs">
      <thead>
        <tr className="text-[11px] text-[var(--muted)]">
          <Th>币种</Th>
          <Th align="right">权益</Th>
          <Th align="right">可用</Th>
          <Th align="right">冻结</Th>
          <Th align="right">估值(USD)</Th>
        </tr>
      </thead>
      <tbody>
        {rows.map((r) => (
          <tr key={r.ccy} className="border-t border-[var(--border)]">
            <Td className="font-medium text-[var(--text)]">{r.ccy}</Td>
            <Td align="right">{fmtNum(r.eq, 4)}</Td>
            <Td align="right">{fmtNum(r.availBal, 4)}</Td>
            <Td align="right">{fmtNum(r.frozenBal, 4)}</Td>
            <Td align="right">{r.eqUsd === null ? "—" : `$${fmtNum(r.eqUsd)}`}</Td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function PositionsSection({ positions }: { positions: OkxAccountPosition[] }) {
  return (
    <section className="flex flex-col gap-2">
      <h3 className="text-xs font-semibold uppercase tracking-wide text-[var(--muted)]">当前持仓</h3>
      {positions.length > 0 ? (
        <PositionsTable positions={positions} />
      ) : (
        <p className="text-[11px] text-[var(--muted)]">当前无持仓</p>
      )}
    </section>
  );
}

function PositionsTable({ positions }: { positions: OkxAccountPosition[] }) {
  return (
    <table className="w-full text-left text-xs">
      <thead>
        <tr className="text-[11px] text-[var(--muted)]">
          <Th>标的</Th>
          <Th>方向</Th>
          <Th align="right">数量</Th>
          <Th align="right">均价</Th>
          <Th align="right">最新价</Th>
          <Th align="right">未实现盈亏</Th>
        </tr>
      </thead>
      <tbody>
        {positions.map((p, i) => (
          <tr key={`${p.instId}-${p.posSide}-${i}`} className="border-t border-[var(--border)]">
            <Td className="font-medium text-[var(--text)]">{p.instId}</Td>
            <Td>
              <PosSideBadge posSide={p.posSide} />
            </Td>
            <Td align="right">{fmtNum(p.pos, 4)}</Td>
            <Td align="right">{fmtPrice(p.avgPx)}</Td>
            <Td align="right">{fmtPrice(p.last)}</Td>
            <Td align="right">
              <PnlCell upl={p.upl} />
            </Td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function PosSideBadge({ posSide }: { posSide: string }) {
  if (posSide === "long") return <Badge tone="long">多</Badge>;
  if (posSide === "short") return <Badge tone="short">空</Badge>;
  return <Badge tone="neutral">{posSide || "—"}</Badge>;
}

function PnlCell({ upl }: { upl: number | null }) {
  if (upl === null) return <span className="text-[var(--muted)]">—</span>;
  const tone = upl > 0 ? "var(--long)" : upl < 0 ? "var(--short)" : "var(--muted)";
  const sign = upl > 0 ? "+" : "";
  return (
    <span className="tabular font-medium" style={{ color: tone }}>
      {sign}
      {fmtNum(upl)}
    </span>
  );
}

function Th({ children, align = "left" }: { children: React.ReactNode; align?: "left" | "right" }) {
  return (
    <th className={`py-1.5 font-medium ${align === "right" ? "text-right" : "text-left"}`}>
      {children}
    </th>
  );
}

function Td({
  children,
  align = "left",
  className,
}: {
  children: React.ReactNode;
  align?: "left" | "right";
  className?: string;
}) {
  return (
    <td
      className={`py-1.5 tabular ${align === "right" ? "text-right" : "text-left"} text-[var(--muted)] ${className || ""}`}
    >
      {children}
    </td>
  );
}
