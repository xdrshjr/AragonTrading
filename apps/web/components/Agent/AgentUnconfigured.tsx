"use client";

// 未配置密钥时的引导态（指向 .env / userData/agent.env 的 AGENT_API_KEY）(Agent Trading Copilot §6.2#5)。

export default function AgentUnconfigured({
  provider,
  model,
}: {
  provider: string;
  model: string;
}) {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-4 px-6 text-center">
      <div className="flex h-12 w-12 items-center justify-center rounded-2xl bg-[var(--surface-2)] text-2xl">
        🔑
      </div>
      <div>
        <p className="text-sm font-semibold text-[var(--text)]">尚未配置模型密钥</p>
        <p className="mt-2 text-xs leading-relaxed text-[var(--muted)]">
          AI 助手需要一个 LLM API Key 才能工作。请设置环境变量{" "}
          <code className="rounded bg-[var(--surface-2)] px-1 py-0.5 text-[var(--text)]">
            AGENT_API_KEY
          </code>{" "}
          后重启。
        </p>
      </div>
      <div className="w-full rounded-[10px] border border-[var(--border)] bg-[var(--surface)] px-3 py-2 text-left text-[11px] leading-relaxed text-[var(--muted)]">
        <p>开发：填写 <code className="text-[var(--text)]">services/quant/.env</code></p>
        <p>桌面端：编辑 <code className="text-[var(--text)]">userData/agent.env</code></p>
        <p className="mt-1">
          当前 provider：<span className="text-[var(--text)]">{provider}</span>，模型：
          <span className="text-[var(--text)]">{model}</span>
        </p>
      </div>
    </div>
  );
}
