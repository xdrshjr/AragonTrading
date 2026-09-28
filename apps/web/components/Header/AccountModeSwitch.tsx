"use client";

import { useQuery } from "@tanstack/react-query";
import { useEffect, useId, useRef, useState } from "react";
import clsx from "clsx";
import RailIconButton from "@/components/Nav/RailIconButton";
import { Badge, Button, Modal } from "@/components/ui";
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
export default function AccountModeSwitch({ placement = "inline" }: {
  placement?: "inline" | "rail";
} = {}) {
  const [open, setOpen] = useState(false);
  const titleId = useId();
  const bodyRef = useRef<HTMLDivElement>(null);
  const simRef = useRef<HTMLButtonElement>(null);
  const liveRef = useRef<HTMLButtonElement>(null);
  const accountMode = useAppStore((s) => s.accountMode);
  const setAccountMode = useAppStore((s) => s.setAccountMode);
  const setMode = useAppStore((s) => s.setMode);
  const bindingPending = useAppStore((s) => s.agentTradingBindingPending);
  const bindingError = useAppStore((s) => s.agentTradingBindingError);
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
    if (!useAppStore.getState().agentConversationId) setMode("paper");
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
    if (placement === "inline") {
      setSettingsSection("okx-accounts");
      setShowSettings(true);
    }
  };

  const selectByKey = (event: React.KeyboardEvent<HTMLDivElement>) => {
    if (placement !== "rail") return;
    if (!event.target || !(event.target as HTMLElement).matches('[role="radio"]')) return;
    const keys = ["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown", "Home", "End"];
    if (!keys.includes(event.key)) return;
    event.preventDefault();
    const nextLive = event.key === "End" || (event.key !== "Home"
      && event.target === simRef.current);
    (nextLive ? selectLive : selectSim)();
    (nextLive ? liveRef : simRef).current?.focus();
  };
  const contents = (
    <div ref={bodyRef} className={placement === "rail"
      ? "flex min-h-0 flex-col gap-4 overflow-y-auto p-4"
      : "flex items-center gap-2"}>
      <div
        onKeyDown={selectByKey}
        role="radiogroup"
        aria-label="账户模式"
        className="flex items-center gap-0.5 rounded-full bg-[var(--surface-2)] p-0.5"
      >
        <button
          type="button"
          role="radio"
          ref={simRef}
          disabled={bindingPending}
          tabIndex={placement === "rail" && accountMode !== "sim" ? -1 : 0}
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
          ref={liveRef}
          disabled={bindingPending}
          tabIndex={placement === "rail" && accountMode !== "live" ? -1 : 0}
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
              ? "bg-[var(--surface)] text-[var(--short-ink)] shadow-soft"
              : "text-[var(--muted)] hover:text-[var(--text)]",
          )}
        >
          实盘
          {needsSetup && <Badge tone="neutral">去设置</Badge>}
        </button>
      </div>
      {bindingPending && <p role="status">正在更新交易模式…</p>}
      {bindingError && <p role="alert">{bindingError}</p>}
      {notice && (
        <span
          role="status"
          className={clsx(
            placement === "rail" ? "whitespace-normal text-xs"
              : "max-w-[16rem] truncate text-[11px]",
            notice.tone === "error" ? "text-[var(--short-ink)]" : "text-[var(--muted)]",
          )}
        >
          {notice.text}
        </span>
      )}
      {placement === "rail" && notice && needsSetup && (
        <Button variant="ghost" size="sm" onClick={() => {
          setOpen(false);
          setSettingsSection("okx-accounts");
          setShowSettings(true);
        }}>前往账户设置</Button>
      )}
      {accountMode === "sim" && <SimBalanceField />}
    </div>
  );
  if (placement === "inline") return contents;
  return <>
    <RailIconButton label={`账户模式：${accountMode === "sim" ? "模拟盘" : "实盘"}`}
      aria-haspopup="dialog" aria-expanded={open} onClick={() => setOpen(true)}>
      <span className={clsx("flex flex-col items-center gap-0.5 text-[10px]",
        accountMode === "live" ? "text-[var(--short-ink)]" : "text-[var(--text)]")}>
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none"
          stroke="currentColor" strokeWidth="1.6" aria-hidden="true">
          <path d="M4 6h15v14H4a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h13v2M19 10h-5v6h5" />
        </svg>
        {accountMode === "sim" ? "模拟" : "实盘"}
      </span>
    </RailIconButton>
    <Modal open={open} onClose={() => setOpen(false)} labelledBy={titleId}
      className="!max-w-sm" initialFocusRef={accountMode === "sim" ? simRef : liveRef}
      onEscapeKeyDown={(event) => {
        const target = event.target;
        if (target instanceof HTMLInputElement
          && target.getAttribute("aria-label") === "模拟资金"
          && bodyRef.current?.contains(target)) event.preventDefault();
      }}>
      <h2 id={titleId} className="shrink-0 border-b border-[var(--border)] p-4 text-sm font-semibold">
        账户模式
      </h2>
      {contents}
      <div className="flex shrink-0 justify-end border-t border-[var(--border)] p-4">
        <Button variant="ghost" size="sm" onClick={() => setOpen(false)}>关闭</Button>
      </div>
    </Modal>
  </>;
}
