"use client";

import { environmentLabel, tradingTime } from "@/lib/agentTradingTypes";
import { useAppStore } from "@/lib/store";
import { useTradingContext } from "./TradingContext";
import UnsettledTrades from "./UnsettledTrades";

/** 账户选择与全局恢复独立成行，窄屏换行而不挤压会话操作。 */
export default function TradingAccountBar() {
  const context = useTradingContext();
  const mode = useAppStore((s) => s.accountMode);
  const error = useAppStore((s) => s.agentTradingBindingError);
  if (!context) return null;
  const caps = context.capabilities;
  return <section aria-label="对话交易账户"
    className="shrink-0 border-b border-[var(--border)] bg-[var(--surface)] px-3 py-2 text-[11px]">
    <div className="flex min-w-0 flex-wrap items-center gap-2">
      <label className="flex min-w-0 items-center gap-1">
        <span className="shrink-0">账户</span>
        <select aria-label="对话交易账户" value={context.binding?.accountId ?? ""}
          disabled={context.pending || !context.bindingLoaded || !caps?.enabled}
          onChange={(event) => void context.selectAccount(
            event.target.value ? Number(event.target.value) : null,
          )}
          className="min-w-0 max-w-[170px] rounded border border-[var(--border)] bg-[var(--surface)] p-1">
          <option value="">请选择账户</option>
          {caps?.accounts.map((account) => <option key={account.id}
            value={account.id} disabled={!account.configured}>
            {account.label}{account.configured ? "" : "（未配置）"}
          </option>)}
        </select>
      </label>
      {caps && <span>{environmentLabel(caps.environment)}</span>}
      <button type="button" onClick={() => void context.refresh()}
        disabled={context.pending} className="underline">刷新账户</button>
    </div>
    <div className="mt-2"><UnsettledTrades /></div>
    {context.pending && <p role="status" className="mt-1">正在更新绑定，交易提交已暂停。</p>}
    {mode === "sim" && <p className="mt-1 text-[var(--muted)]">
      当前为本地模拟，切换到账户交易后可生成订单。</p>}
    {caps && !caps.executionEnabled && <p className="mt-1 text-[var(--muted)]">
      新交易暂不可用；已提交订单继续只读核对。</p>}
    {context.binding && <p className="mt-1 text-[var(--muted)]">
      绑定更新：{tradingTime(context.binding.updatedTs)}</p>}
    {(context.error || error) && <p role="status" className="mt-1">
      {error ?? context.error}</p>}
  </section>;
}
