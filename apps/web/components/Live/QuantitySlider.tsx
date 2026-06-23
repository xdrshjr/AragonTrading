"use client";

import { useEffect, useState } from "react";
import clsx from "clsx";
import { Input, Slider } from "@/components/ui";
import { fmtNum, fmtPrice } from "@/lib/format";
import { sizeToSliderPct, sliderPctToSize, snapSize } from "@/lib/liveOrder";

const QUICK_PCTS = [25, 50, 75, 100];

// 仓位数量控件（§2.2）：滑块 % 与手工输入（张）双向联动，并同屏展示币量 / 名义价值 / 所需保证金。
// 单位显式标「张」（合约 sz 为张，杜绝币量误解，§2.3）；滑块上限由可开张数 maxSz 决定。
export default function QuantitySlider({
  sz,
  maxSz,
  lotSz,
  ctValCcy,
  coinQty,
  notional,
  margin,
  onChange,
  disabled,
}: {
  sz: number;
  maxSz: number;
  lotSz: number;
  ctValCcy: string;
  coinQty: number;
  notional: number;
  margin: number;
  onChange: (next: number) => void;
  disabled?: boolean;
}) {
  // 输入框保留本地文本缓冲，便于编辑；sz 外部变更（滑块 / 快捷档）时回填。
  const [text, setText] = useState(() => (sz > 0 ? String(sz) : ""));
  useEffect(() => {
    setText(sz > 0 ? String(sz) : "");
  }, [sz]);

  const pct = sizeToSliderPct(sz, maxSz);

  const onText = (raw: string) => {
    setText(raw);
    const n = Number(raw);
    if (raw.trim() !== "" && Number.isFinite(n)) {
      onChange(snapSize(Math.max(0, n), lotSz));
    }
  };

  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-center justify-between">
        <span className="text-xs font-medium text-[var(--muted)]">仓位数量（张）</span>
        <span className="text-[10px] text-[var(--muted)]">
          可开 ≈ {fmtNum(maxSz, 0)} 张
        </span>
      </div>

      <div className="flex items-center gap-2">
        <Slider
          value={pct}
          min={0}
          max={100}
          step={1}
          disabled={disabled || !(maxSz > 0)}
          ariaLabel="仓位数量百分比"
          onChange={(next) => onChange(sliderPctToSize(next, maxSz, lotSz))}
          className="flex-1"
        />
        <Input
          type="number"
          min={0}
          step={lotSz}
          value={text}
          disabled={disabled}
          aria-label="仓位数量（张）"
          onChange={(e) => onText(e.target.value)}
          className="w-20 text-right tabular"
        />
      </div>

      <div className="grid grid-cols-4 gap-1">
        {QUICK_PCTS.map((quick) => (
          <button
            key={quick}
            type="button"
            disabled={disabled || !(maxSz > 0)}
            onClick={() => onChange(sliderPctToSize(quick, maxSz, lotSz))}
            className={clsx(
              "rounded-[7px] border border-[var(--border)] px-1 py-1 text-[11px] font-medium",
              "text-[var(--muted)] transition-colors duration-150 hover:text-[var(--text)]",
              "disabled:cursor-not-allowed disabled:opacity-40",
            )}
          >
            {quick}%
          </button>
        ))}
      </div>

      <dl className="grid grid-cols-3 gap-2 rounded-[10px] bg-[var(--surface-2)] px-3 py-2 text-[11px]">
        <Metric label={`币量（${ctValCcy || "—"}）`} value={fmtNum(coinQty, 4)} />
        <Metric label="名义价值" value={`${fmtPrice(notional)} USDT`} />
        <Metric label="所需保证金" value={`${fmtPrice(margin)} USDT`} />
      </dl>
    </div>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex flex-col gap-0.5">
      <dt className="text-[10px] text-[var(--muted)]">{label}</dt>
      <dd className="tabular font-medium text-[var(--text)]">{value}</dd>
    </div>
  );
}
