"use client";

import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { fmtPrice, fmtTime } from "@/lib/format";
import { useAppStore } from "@/lib/store";
import type { Bar, Instrument, Mode } from "@/lib/types";
import { Badge, Select } from "@/components/ui";

const BARS: Bar[] = ["1m", "5m", "15m", "30m", "1H", "4H", "1D"];
const FALLBACK_INSTRUMENTS = ["BTC-USDT-SWAP", "ETH-USDT-SWAP", "SOL-USDT-SWAP"];
const MODES: { key: Mode; label: string }[] = [
  { key: "backtest", label: "回测" },
  { key: "compare", label: "对比" },
  { key: "paper", label: "实时" },
];

export default function ChartToolbar({
  okxUp,
  latencyMs,
  lastOkTs,
  liveWsOpen,
  lastPrice,
  onJumpDate,
}: {
  okxUp: boolean | null;
  latencyMs?: number | null;
  lastOkTs?: number | null;
  liveWsOpen?: boolean; // F1：市场实时流连接态（仅非 paper 模式传入）
  lastPrice?: number | null; // F1：实时最新价
  onJumpDate: (tsSeconds: number) => void;
}) {
  const { instId, bar, mode, setInst, setBar, setMode } = useAppStore();

  const { data: instruments } = useQuery({
    queryKey: ["instruments"],
    queryFn: () => api.instruments("SWAP"),
    staleTime: 5 * 60_000,
  });

  // 仅展示 USDT 永续，避免下拉过长；保证默认三标的可选
  const instOptions = buildInstOptions(instruments);

  return (
    <div className="flex flex-wrap items-center gap-2">
      <Select value={instId} onChange={(e) => setInst(e.target.value)} aria-label="标的">
        {instOptions.map((id) => (
          <option key={id} value={id}>
            {id}
          </option>
        ))}
      </Select>

      <div className="flex gap-0.5 rounded-[10px] bg-[var(--surface-2)] p-0.5">
        {BARS.map((b) => (
          <button
            key={b}
            onClick={() => setBar(b)}
            className={
              "rounded-[7px] px-2 py-1 text-xs font-medium transition-all " +
              (bar === b
                ? "bg-[var(--surface)] text-[var(--text)] shadow-soft"
                : "text-[var(--muted)] hover:text-[var(--text)]")
            }
          >
            {b}
          </button>
        ))}
      </div>

      <input
        type="date"
        onChange={(e) => {
          if (!e.target.value) return;
          const ts = Math.floor(new Date(`${e.target.value}T00:00:00Z`).getTime() / 1000);
          onJumpDate(ts);
        }}
        className="rounded-[10px] border border-[var(--border)] bg-[var(--surface)] px-2 py-1.5 text-xs text-[var(--muted)] hover:border-[var(--accent)]"
        aria-label="跳转日期"
      />

      <div className="ml-auto flex items-center gap-2">
        <div className="flex gap-0.5 rounded-[10px] bg-[var(--surface-2)] p-0.5">
          {MODES.map((m) => (
            <button
              key={m.key}
              onClick={() => setMode(m.key)}
              className={
                "rounded-[7px] px-2.5 py-1 text-xs font-medium transition-all " +
                (mode === m.key
                  ? "bg-[var(--surface)] text-[var(--text)] shadow-soft"
                  : "text-[var(--muted)] hover:text-[var(--text)]")
              }
            >
              {m.label}
            </button>
          ))}
        </div>

        <LivePrice liveWsOpen={liveWsOpen} lastPrice={lastPrice} />
        <ConnDot okxUp={okxUp} latencyMs={latencyMs} lastOkTs={lastOkTs} />
      </div>
    </div>
  );
}

// F1：实时价 chip + 实时流状态脉冲点（仅在传入 lastPrice / liveWsOpen 时渲染）
function LivePrice({
  liveWsOpen,
  lastPrice,
}: {
  liveWsOpen?: boolean;
  lastPrice?: number | null;
}) {
  if (lastPrice == null && liveWsOpen === undefined) return null;
  const open = !!liveWsOpen;
  return (
    <span
      className="flex items-center gap-1.5 rounded-[10px] bg-[var(--surface-2)] px-2 py-1 text-xs"
      title={open ? "实时行情流已连接（最右一根随真实成交跳动）" : "实时行情流连接中…"}
    >
      <span
        className="inline-block h-1.5 w-1.5 rounded-full"
        style={{
          background: open ? "var(--long)" : "var(--muted)",
          animation: open ? "shimmer 1.4s ease-in-out infinite" : undefined,
        }}
      />
      <span className="tabular font-medium text-[var(--text)]">{fmtPrice(lastPrice)}</span>
    </span>
  );
}

function ConnDot({
  okxUp,
  latencyMs,
  lastOkTs,
}: {
  okxUp: boolean | null;
  latencyMs?: number | null;
  lastOkTs?: number | null;
}) {
  const tone = okxUp === null ? "neutral" : okxUp ? "long" : "short";
  const label = okxUp === null ? "探测中" : okxUp ? "OKX 已连接" : "OKX 不可达";
  const title = okxUp
    ? `OKX 已连接${latencyMs != null ? ` · 时延 ${latencyMs}ms` : ""}${
        lastOkTs ? ` · 最近取数 ${fmtTime(lastOkTs)}` : ""
      }`
    : okxUp === null
      ? "正在探测 OKX 可达性…"
      : "OKX 不可达：请检查代理 / 网络（不会以假数据冒充真实行情）";
  return (
    <span title={title}>
      <Badge tone={tone}>
        <span
          className="mr-1 inline-block h-1.5 w-1.5 rounded-full"
          style={{ background: "currentColor" }}
        />
        {label}
      </Badge>
    </span>
  );
}

function buildInstOptions(instruments: Instrument[] | undefined): string[] {
  const set = new Set<string>(FALLBACK_INSTRUMENTS);
  if (instruments) {
    instruments
      .filter((i) => i.quote === "USDT")
      .slice(0, 40)
      .forEach((i) => set.add(i.instId));
  }
  return Array.from(set);
}
