"use client";

import { useEffect, useRef, useState } from "react";
import { environmentLabel, INTENT_LABELS, tradingTime,
  type TradeIntentView } from "@/lib/agentTradingTypes";
import { useAppStore } from "@/lib/store";
import { useTradeIntent } from "./useTradeIntent";

interface Props { intent: TradeIntentView; readOnly?: boolean }

/** 冻结预览与成交回执分栏展示；只有具体提交按钮可以发交易请求。 */
export default function TradeIntentCard({ intent: snapshot, readOnly }: Props) {
  const trade = useTradeIntent(snapshot, readOnly);
  const { intent, act } = trade;
  const titleRef = useRef<HTMLHeadingElement>(null);
  const errorRef = useRef<HTMLParagraphElement>(null);
  const [now, setNow] = useState(Date.now() / 1000);
  const preview = intent.preview;
  const action = intent.action === "cancel" ? "撤单"
    : intent.action === "close" ? "平仓"
      : preview.direction === "long" ? "买多" : "卖空";
  const seconds = Math.max(0, Math.ceil(intent.expiresTs - now));
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now() / 1000), 1000);
    return () => clearInterval(timer);
  }, []);
  useEffect(() => {
    if (trade.error) errorRef.current?.focus();
  }, [trade.error]);
  const receipt = intent.result;
  return (
    <section ref={trade.elementRef} aria-label="交易意图"
      className="min-w-0 rounded-[10px] border border-[var(--border)] bg-[var(--surface)] p-3 text-xs text-[var(--text)]">
      <div className="flex flex-wrap items-center justify-between gap-1">
        <span>{intent.account.label} · {environmentLabel(intent.account.environment)}</span>
        <span role="status" aria-live="polite" className="text-[var(--muted)]">
          {trade.uncertain && intent.status === "prepared"
            ? trade.errorCode ? "未执行/已拒绝" : "结果待核实"
            : INTENT_LABELS[intent.status]}
        </span>
      </div>
      <h4 tabIndex={-1} ref={titleRef} className="my-2 break-all text-sm font-semibold">
        {action} · {preview.instId}
      </h4>
      <dl className="grid grid-cols-2 gap-x-3 gap-y-1.5">
        <Field label="仓位方向" value={preview.direction === "long" ? "多头"
          : preview.direction === "short" ? "空头" : null} />
        <Field label="委托方向" value={preview.side === "buy" ? "买入"
          : preview.side === "sell" ? "卖出" : null} />
        <Field label="数量 / 订单" value={
          `${preview.sizeContracts ?? "—"} 张 · ${preview.ordType === "market" ? "市价" : preview.ordType === "limit" ? "限价" : "撤销普通委托"}`} />
        <Field label="价格（USDT）" value={preview.price ?? "市价，成交价未定"} />
        <Field label="估计名义（USDT）" value={preview.notionalEstimateUsdt} />
        <Field label="估计保证金（USDT）" value={preview.marginEstimateUsdt} />
        <Field label="现有杠杆" value={preview.leverageObserved
          ? `${preview.leverageObserved} 倍` : null} />
        <Field label="保证金模式" value={preview.tdMode === "cross" ? "全仓"
          : preview.tdMode === "isolated" ? "逐仓" : null} />
        <Field label="止盈 / 止损（USDT）" value={
          `${preview.tpTriggerPx ?? "—"} / ${preview.slTriggerPx ?? "—"}`} />
        <Field label="保护状态" value={receipt?.protectionStatus === "observed_active"
          ? "已核实" : "未核实，附单参数不代表保护已生效"} />
      </dl>
      {preview.targetOrdId && <p className="mt-2 break-all">
        目标订单：{preview.targetOrdId}</p>}
      <p className="mt-2 text-[var(--muted)]">
        观测：{tradingTime(preview.observedTs)}
        {intent.status === "prepared" && ` · 预览剩余 ${seconds} 秒`}
      </p>
      {preview.warnings.map((warning, index) => <p key={index}
        className="mt-1 text-[var(--muted)]">{warning}</p>)}
      <p className="mt-1 text-[var(--muted)]">
        预览不预留余额；市价为估计，外部交易可能改变仓位。
      </p>
      {receipt && <div className="mt-3 border-t border-[var(--border)] pt-2">
        <p className="break-all">订单：{receipt.ordId ?? "尚未取得"}</p>
        <p className="break-all">客户订单号：{receipt.clOrdId ?? "—"}</p>
        <p>累计成交 {receipt.accFillSz ?? "—"} 张 ·
          实际均价 {receipt.avgPx ?? "—"} USDT</p>
        {intent.action === "cancel" && intent.status === "filled"
          && <p>撤销前已成交</p>}
        {intent.action === "cancel" && intent.status === "accepted"
          && <p>撤销请求已受理，等待订单终态</p>}
        <p className="text-[var(--muted)]">
          最后核对：{tradingTime(receipt.lastCheckedTs)}</p>
      </div>}
      {intent.status === "unknown" && <p className="mt-2" role="status">
        订单结果待核实，持续占用交易准入。请在交易所核对，不要重新下单。
      </p>}
      {intent.error && <p className="mt-2" role="alert">
        {intent.error.message}
        {intent.error.code === "AUTO_TRADE_OWNS_ACCOUNT" && <button
          type="button" className="ml-2 underline" onClick={() => {
            useAppStore.getState().setRightTab("agent");
            const controls = document.querySelector<HTMLElement>(
              '[data-testid="ai-trade-control-bar"]',
            );
            controls?.scrollIntoView({ block: "nearest" });
            controls?.querySelector<HTMLButtonElement>("button")?.focus();
          }}>查看自动交易面板</button>}
      </p>}
      {trade.error && <p ref={errorRef} tabIndex={-1} role="alert"
        className="mt-2 text-[var(--short-ink)]">{trade.error}</p>}
      {trade.errorCode === "AUTO_TRADE_OWNS_ACCOUNT" && <p className="mt-2">
        请在下方既有自动交易控制栏处理托管会话，再发送新消息生成预览。
      </p>}
      {!trade.verified && <p role="status" className="mt-2">
        正在核对当前状态，历史预览暂不可提交。</p>}
      <div className="mt-3 flex flex-wrap gap-2">
        {!readOnly && intent.status === "prepared" && !trade.uncertain && <>
          <button type="button" disabled={!trade.canExecute || seconds === 0}
            onClick={() => void act("execute")}
            className="rounded-md bg-[var(--text)] px-3 py-2 text-[var(--surface)] disabled:opacity-40">
            {trade.busy ? "提交中…" : `提交${action}`}
          </button>
          <button type="button" disabled={!trade.verified || trade.busy}
            onClick={async () => { await act("dismiss"); titleRef.current?.focus(); }}
            className="rounded-md border border-[var(--border)] px-3 py-2 disabled:opacity-40">
            放弃预览</button>
        </>}
        <button type="button" disabled={trade.busy}
          onClick={() => void trade.refresh()}
          className="rounded-md border border-[var(--border)] px-3 py-2">
          查询状态</button>
      </div>
    </section>
  );
}

function Field({ label, value }: { label: string; value: string | null }) {
  return <div className="min-w-0"><dt className="text-[var(--muted)]">{label}</dt>
    <dd className="break-words font-mono">{value ?? "—"}</dd></div>;
}
