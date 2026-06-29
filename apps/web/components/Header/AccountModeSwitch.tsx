"use client";

import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import clsx from "clsx";
import { Badge } from "@/components/ui";
import { accountApi } from "@/lib/accountApi";
import { useAppStore } from "@/lib/store";
import SimBalanceField from "./SimBalanceField";

/** 切换「实盘」被拦截时的就地反馈：用一行可见中文原因取代旧的「静默弹设置 / 静默无反应」。 */
type SwitchNotice = { tone: "info" | "error"; text: string } | null;

/**
 * 顶部「模拟盘 / 实盘」分段开关（支柱 A / 实盘交易模式 §5.3）。
 *
 * - 「模拟盘」：选中即 setAccountMode("sim") + setMode("paper")。
 * - 「实盘」（Phase 2「guarded order placement」）：账户查询的三态被显式解耦，避免把「加载中 / 失败 /
 *   确无默认账户」折叠成单一「禁止」而造成「点了切不过去且无任何反馈」：
 *     · 加载中（!isSuccess && !isError）：给「正在校验账户…」瞬态提示，**不**乐观改 accountMode
 *       （乐观进 live 会被水合守门立刻弹回 sim，重现「闪一下又弹回」）；待查询落地后再点。
 *     · 失败（isError）：就地可见报错并触发重试（refetch），不再静默弹设置。
 *     · 成功但确无就绪默认账户（needsSetup）：引导去设置中心，并就地给出可见原因（区分「确无默认」与
 *       「凭证不全」）。
 *     · 成功且已就绪默认账户：无条件切到实盘。
 * - 水合守门（C1-5）：刷新后若持久化的 accountMode 为 "live" 但默认账户已失效 / 未配置，立即回落 "sim"；
 *   与 selectLive 共用同一 isSuccess 前置，消除加载期不对称保护造成的竞态。
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
  // 状态③：仅在查询**成功落地且确无就绪默认账户**时才视为「需去设置」（与 selectLive / 守门同口径）。
  const needsSetup = accountsQ.isSuccess && !hasReadyDefault;

  const [notice, setNotice] = useState<SwitchNotice>(null);

  // 水合守门（C1-5）：账户数据成功就绪后，若停在实盘态却无可用默认账户，回落模拟盘。
  useEffect(() => {
    if (!accountsQ.isSuccess) return;
    if (accountMode === "live" && !hasReadyDefault) setAccountMode("sim");
  }, [accountsQ.isSuccess, accountMode, hasReadyDefault, setAccountMode]);

  // 账户一旦校验为「就绪默认账户」，清掉此前因加载中 / 失败 / 缺账户给出的拦截提示。
  useEffect(() => {
    if (hasReadyDefault) setNotice(null);
  }, [hasReadyDefault]);

  const selectSim = () => {
    setNotice(null);
    setAccountMode("sim");
    setMode("paper");
  };

  const selectLive = () => {
    // 状态①/②：账户查询尚未成功落地。严禁乐观进 live —— 一旦落地为 !hasReadyDefault，
    // 水合守门会立刻把 accountMode 弹回 sim，重现用户报障的「切不动 / 闪一下又弹回」。
    if (!accountsQ.isSuccess) {
      if (accountsQ.isError) {
        setNotice({ tone: "error", text: "账户校验失败，请检查后端连接后点「实盘」重试。" });
        accountsQ.refetch();
      } else {
        setNotice({ tone: "info", text: "正在校验账户…请稍候后再切换。" });
      }
      return;
    }
    // 状态④：已就绪默认账户 → 无条件切到实盘。
    if (hasReadyDefault) {
      setNotice(null);
      setAccountMode("live");
      return;
    }
    // 状态③：查询成功但确无就绪默认账户 → 引导设置中心，并就地给出可见原因（区分两种「不就绪」）。
    setNotice({
      tone: "info",
      text: defaultAccount
        ? "默认账户凭证不全，请在设置中补全 API secret / passphrase。"
        : "请先在设置中配置默认实盘账户。",
    });
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
              : needsSetup
                ? "尚未配置默认实盘账户，点击前往设置中心配置"
                : accountsQ.isError
                  ? "账户校验失败，点击重试"
                  : "正在校验账户…"
          }
          className={clsx(
            "flex items-center gap-1 rounded-full px-3 py-1 text-xs font-medium transition-all duration-150 ease-smooth",
            accountMode === "live"
              ? "bg-[var(--surface)] text-[var(--short)] shadow-soft"
              : "text-[var(--muted)] hover:text-[var(--text)]",
          )}
        >
          实盘
          {needsSetup && <Badge tone="neutral">去设置</Badge>}
        </button>
      </div>
      {notice && (
        <span
          role="status"
          className={clsx(
            "max-w-[16rem] truncate text-[11px]",
            notice.tone === "error" ? "text-[var(--short)]" : "text-[var(--muted)]",
          )}
        >
          {notice.text}
        </span>
      )}
      {accountMode === "sim" && <SimBalanceField />}
    </div>
  );
}
