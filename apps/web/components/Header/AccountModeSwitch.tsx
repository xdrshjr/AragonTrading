"use client";

import { useQuery } from "@tanstack/react-query";
import { useEffect } from "react";
import clsx from "clsx";
import { Badge } from "@/components/ui";
import { accountApi } from "@/lib/accountApi";
import { useAppStore } from "@/lib/store";
import SimBalanceField from "./SimBalanceField";

/**
 * 顶部「模拟盘 / 实盘」分段开关（支柱 A / 实盘交易模式 §5.3）。
 *
 * - 「模拟盘」：选中即 setAccountMode("sim") + setMode("paper")。
 * - 「实盘」（Phase 2「guarded order placement」）：仅在**已配置默认账户**（含 secret/passphrase）时可点；
 *   否则引导去设置中心配置（不静默进入无凭证可用的实盘态）。
 * - 水合守门（C1-5）：刷新后若持久化的 accountMode 为 "live" 但默认账户已失效 / 未配置，立即回落 "sim"。
 *   （`liveTradingEnabled` 在下单面板内据 `/trading/config` 显式禁用并提示，读类接口仍可用，见 LiveTradePanel。）
 */
export default function AccountModeSwitch() {
  const accountMode = useAppStore((s) => s.accountMode);
  const setAccountMode = useAppStore((s) => s.setAccountMode);
  const setMode = useAppStore((s) => s.setMode);
  const setShowSettings = useAppStore((s) => s.setShowSettings);
  const setSettingsSection = useAppStore((s) => s.setSettingsSection);

  const accountsQ = useQuery({ queryKey: ["okx", "accounts"], queryFn: accountApi.accounts.list });
  const defaultAccount =
    accountsQ.data?.accounts.find((a) => a.id === accountsQ.data?.defaultId) ?? null;
  const hasReadyDefault = !!defaultAccount && defaultAccount.hasSecret && defaultAccount.hasPassphrase;

  // 水合守门（C1-5）：账户数据就绪后，若停在实盘态却无可用默认账户，回落模拟盘。
  useEffect(() => {
    if (!accountsQ.isSuccess) return;
    if (accountMode === "live" && !hasReadyDefault) setAccountMode("sim");
  }, [accountsQ.isSuccess, accountMode, hasReadyDefault, setAccountMode]);

  const selectSim = () => {
    setAccountMode("sim");
    setMode("paper");
  };

  const selectLive = () => {
    if (hasReadyDefault) {
      setAccountMode("live");
      return;
    }
    // 未配置默认账户：引导去设置中心实盘账户页，而非静默进入实盘。
    setSettingsSection("okx-accounts");
    setShowSettings(true);
  };

  return (
    <div className="flex items-center gap-2">
      <div
        role="radiogroup"
        aria-label="账户模式"
        className="flex items-center gap-0.5 rounded-full bg-[var(--surface-2)] p-0.5"
      >
        <button
          type="button"
          role="radio"
          aria-checked={accountMode === "sim"}
          onClick={selectSim}
          className={clsx(
            "rounded-full px-3 py-1 text-xs font-medium transition-all duration-150 ease-smooth",
            accountMode === "sim"
              ? "bg-[var(--surface)] text-[var(--text)] shadow-soft"
              : "text-[var(--muted)] hover:text-[var(--text)]",
          )}
        >
          模拟盘
        </button>
        <button
          type="button"
          role="radio"
          aria-checked={accountMode === "live"}
          onClick={selectLive}
          title={
            hasReadyDefault
              ? "切换到实盘：使用默认 OKX 账户真实下单（每单二次确认）"
              : "尚未配置默认实盘账户，点击前往设置中心配置"
          }
          className={clsx(
            "flex items-center gap-1 rounded-full px-3 py-1 text-xs font-medium transition-all duration-150 ease-smooth",
            accountMode === "live"
              ? "bg-[var(--surface)] text-[var(--short)] shadow-soft"
              : "text-[var(--muted)] hover:text-[var(--text)]",
          )}
        >
          实盘
          {!hasReadyDefault && <Badge tone="neutral">去设置</Badge>}
        </button>
      </div>
      {accountMode === "sim" && <SimBalanceField />}
    </div>
  );
}
