"use client";

// 空态：欢迎语 + 建议提示词（点击即填充并发送）(Agent Trading Copilot §3.2)。

const SUGGESTIONS = [
  "列出现货里成交额最高的几个标的",
  "画出 BTC 永续最近 30 天的日线",
  "用 EMA 交叉策略回测 BTC 永续最近 30 天并展示买卖点",
  "对比几个策略最近 30 天在 BTC 永续上的表现",
];

export default function AgentEmptyState({ onPick }: { onPick: (text: string) => void }) {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-4 px-5 text-center">
      <div className="flex h-12 w-12 items-center justify-center rounded-2xl bg-[var(--accent-soft)] text-2xl">
        ✨
      </div>
      <div>
        <p className="text-sm font-semibold text-[var(--text)]">AI 交易助手</p>
        <p className="mt-1 text-xs leading-relaxed text-[var(--muted)]">
          用自然语言查看行情、绘制 K 线、运行回测并解读结果。
          <br />
          所有数据均来自 OKX 真实行情。
        </p>
      </div>
      <div className="flex w-full flex-col gap-2">
        {SUGGESTIONS.map((s) => (
          <button
            key={s}
            onClick={() => onPick(s)}
            className="rounded-[10px] border border-[var(--border)] bg-[var(--surface)] px-3 py-2 text-left text-xs text-[var(--text)] transition-colors duration-150 ease-smooth hover:border-[var(--accent)] hover:bg-[var(--surface-2)]"
          >
            {s}
          </button>
        ))}
      </div>
    </div>
  );
}
