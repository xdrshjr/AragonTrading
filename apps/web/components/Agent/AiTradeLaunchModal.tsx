"use client";

import { useQuery } from "@tanstack/react-query";
import { type ReactNode, useEffect, useMemo, useState } from "react";
import { Badge, Button, Modal, Select } from "@/components/ui";
import { api } from "@/lib/api";
import {
  AI_BACKTEST_DEFAULT_DECISION_EVERY,
  AI_BACKTEST_MAX_ROUNDS,
  defaultDecisionEvery,
  planRounds,
  validateBacktestRange,
} from "@/lib/aiBacktest";
import {
  AI_TRADE_DEFAULT_CONTEXT_ROUNDS,
  AI_TRADE_DEFAULT_POSITION_STEP,
  AI_TRADE_MAX_SL_PCT,
  AI_TRADE_MAX_STRATEGIES,
  AI_TRADE_MAX_SYMBOLS,
  AI_TRADE_MAX_TP_PCT,
  AI_TRADE_POSITION_STEP_OPTIONS,
  deriveDefaultBudget,
  positionLadder,
  validateContextRounds,
  validateStrategySelection,
} from "@/lib/aiTrade";
import {
  clampGroupBudgetToCap,
  DEFAULT_AUTO_TRADE_MAX_BUDGET_PCT,
  groupBudgetCap,
  isAutoTradable,
  isGroupBudgetClamped,
  memberBudget,
  totalAllocation,
} from "@/lib/autoTrade";
import { autoTradeSettingsApi } from "@/lib/autoTradeSettingsApi";
import { fmtPrice } from "@/lib/format";
import {
  clampPct,
  LIVE_AUTO_DEFAULT_LEVERAGE,
  LIVE_MAX_ORDER_NOTIONAL_USD,
  parseOptionalLeverage,
  parseOptionalPct,
  parseOptionalUsd,
} from "@/lib/liveAuto";
import type { AutoTradeAllocationMode, AutoTradeMember } from "@/lib/liveAutoTypes";
import { tradingApi } from "@/lib/tradingApi";
import type { Bar } from "@/lib/types";

export type AiTradeLaunchMode = "live" | "backtest";

// 时间级别候选（镜像 ChartToolbar 的 BARS；弹窗「时间级别」下拉可编辑）。
const BARS: Bar[] = ["1m", "5m", "15m", "30m", "1H", "4H", "1D"];

export interface AiTradeLaunchOpts {
  mode: AiTradeLaunchMode;
  members: { instId: string; allocationPct: number }[]; // 实盘多标的（回测取 members[0] 兜底）
  allocationMode: AutoTradeAllocationMode;               // even | max_ratio
  instId: string;                                        // 回测单标的（members[0] 兜底）
  bar: string;                                           // 可编辑周期（取代对 store.bar 的直读）
  strategyIds: number[];
  capitalUsd: number | null;       // live=分组总预算；backtest=初始资金（留空＝服务端默认）
  leverage: number | null;
  takeProfitUsd: number | null;
  stopLossUsd: number | null;
  stopLossPct: number | null;      // E4：单仓 OCO 止损%（fraction，留空＝config 默认）
  takeProfitPct: number | null;    // E4：单仓 OCO 止盈%
  positionStepPct: number;         // 仓位操作档位步长（fraction；AI 每次开仓规模只能落在该档位阶梯上）
  contextRefreshRounds: number;
  // 回测专属（mode==="backtest" 时有效）
  startTs?: number;
  endTs?: number;
  decisionEveryBars?: number | null;   // UI 已解析为智能默认，永不因越界自拒（R5）
  minConsensus?: number | null;
}

const DAY = 86_400;

/**
 * 启动 AI 自动交易弹窗（ai-trade-multi-symbol-and-timeframe §4.2/§4.3）：顶部「实盘 / 回测」段控。
 * 实盘段：**多标的选择器**（默认回填左侧「自动交易」面板的 autoTradeMembers，至多 AI_TRADE_MAX_SYMBOLS 个、
 * 超出提示并禁增；每成员 allocation% + 移除 + 均分 + even/max_ratio 段控）+ **可编辑时间级别** + 多选策略 +
 * 预算/杠杆/OCO/总止盈损 + 上下文轮数 + 真实资金勾选。回测段：仍单标的（默认 members[0]），同享可编辑周期。
 */
export default function AiTradeLaunchModal({
  open,
  onClose,
  onConfirm,
  submitting,
  acknowledged,
  onAcknowledge,
  bar: propBar,
  defaultMembers,
  defaultAllocationMode,
  defaultInstId,
  defaultStrategyIds,
  defaultContextRounds,
  defaultPositionStepPct,
  error,
  liveGateReason = null,
  defaultStartTs,
  defaultEndTs,
  defaultDecisionEveryBars,
}: {
  open: boolean;
  onClose: () => void;
  onConfirm: (opts: AiTradeLaunchOpts) => void;
  submitting: boolean;
  acknowledged: boolean;
  onAcknowledge: (value: boolean) => void;
  bar: string;                        // K 线所选周期（回填「时间级别」下拉默认值）
  defaultMembers: AutoTradeMember[];  // 来自 autoTradeMembers（可 > 上限，弹窗内截断并提示，P1-2）
  defaultAllocationMode: AutoTradeAllocationMode;   // 来自 store.autoTradeAllocationMode（P1-2）
  defaultInstId: string;              // 当前图表标的（autoTradeMembers 空时回退单成员）
  defaultStrategyIds: number[];
  defaultContextRounds: number;
  defaultPositionStepPct: number;     // 上次所选仓位操作档位（aiTradeConfig?.positionStepPct ?? 默认，回填弹窗）
  error?: string | null;
  liveGateReason?: string | null;     // 实盘不可用原因（非 null → 段控禁用实盘、默认落回测，R6）
  defaultStartTs?: number;
  defaultEndTs?: number;
  defaultDecisionEveryBars?: number;
}) {
  const liveAvailable = liveGateReason == null;
  const [mode, setMode] = useState<AiTradeLaunchMode>(liveAvailable ? "live" : "backtest");
  const [members, setMembers] = useState<AutoTradeMember[]>([]);
  const [allocationMode, setAllocationMode] = useState<AutoTradeAllocationMode>(defaultAllocationMode);
  const [bar, setBar] = useState<Bar>(propBar as Bar);
  const [instId, setInstId] = useState(defaultInstId);   // 回测单标的
  const [addSymbolRaw, setAddSymbolRaw] = useState("");
  const [addError, setAddError] = useState<string | null>(null);
  const [selectedIds, setSelectedIds] = useState<number[]>(defaultStrategyIds);
  const [capitalRaw, setCapitalRaw] = useState("");
  const [leverageRaw, setLeverageRaw] = useState("");
  const [takeProfitRaw, setTakeProfitRaw] = useState("");
  const [stopLossRaw, setStopLossRaw] = useState("");
  const [stopLossPctRaw, setStopLossPctRaw] = useState("");
  const [takeProfitPctRaw, setTakeProfitPctRaw] = useState("");
  const [roundsRaw, setRoundsRaw] = useState(String(defaultContextRounds || AI_TRADE_DEFAULT_CONTEXT_ROUNDS));
  const [positionStep, setPositionStep] = useState(defaultPositionStepPct || AI_TRADE_DEFAULT_POSITION_STEP);
  const [startInput, setStartInput] = useState("");
  const [endInput, setEndInput] = useState("");
  const [everyRaw, setEveryRaw] = useState("");
  const [minConsensusRaw, setMinConsensusRaw] = useState("");

  // 回填种子：autoTradeMembers 非空则取前 AI_TRADE_MAX_SYMBOLS 个；否则回退当前图表标的单成员 100%（P1-2）。
  const seededMembers = useMemo<AutoTradeMember[]>(() => {
    const src = defaultMembers.length > 0 ? defaultMembers : [{ instId: defaultInstId, allocationPct: 100 }];
    return src.slice(0, AI_TRADE_MAX_SYMBOLS).map((m) => ({ ...m }));
  }, [defaultMembers, defaultInstId]);
  const truncated = defaultMembers.length > AI_TRADE_MAX_SYMBOLS;
  const atCap = members.length >= AI_TRADE_MAX_SYMBOLS;

  const strategiesQ = useQuery({ queryKey: ["strategies"], queryFn: api.strategies });
  // 账户级总预算上限百分比（设置中心可调，默认 100%）：与非 AI 多标的弹窗同源，让「改总预算即联动每标的分配」。
  // 仅实盘用总预算天花板；回测走 initialCapital，故 enabled 收敛到 live 段。
  const settingsQ = useQuery({
    queryKey: ["settings", "auto-trade"],
    queryFn: autoTradeSettingsApi.get,
    enabled: open && mode === "live",
  });
  const maxBudgetPct = settingsQ.data?.effectiveMaxBudgetPct ?? DEFAULT_AUTO_TRADE_MAX_BUDGET_PCT;
  const budgetInst = mode === "live" ? (members[0]?.instId ?? "") : instId;
  const configQ = useQuery({
    queryKey: ["trading", "config", budgetInst, null],
    queryFn: () => tradingApi.config(budgetInst),
    enabled: open && !!budgetInst && mode === "live",
  });
  const availUsdt = configQ.data?.availUsdt ?? null;

  // 弹窗每次打开：回落默认段 / 成员 / 分配模式 / 周期 / 策略 / 轮数 / 区间并清空金额输入。
  useEffect(() => {
    if (!open) return;
    setMode(liveAvailable ? "live" : "backtest");
    setMembers(seededMembers);
    setAllocationMode(defaultAllocationMode);
    setBar(propBar as Bar);
    setInstId(seededMembers[0]?.instId ?? defaultInstId);
    setAddSymbolRaw("");
    setAddError(null);
    setSelectedIds(defaultStrategyIds);
    setRoundsRaw(String(defaultContextRounds || AI_TRADE_DEFAULT_CONTEXT_ROUNDS));
    setPositionStep(defaultPositionStepPct || AI_TRADE_DEFAULT_POSITION_STEP);
    setCapitalRaw("");
    setLeverageRaw("");
    setTakeProfitRaw("");
    setStopLossRaw("");
    setStopLossPctRaw("");
    setTakeProfitPctRaw("");
    const nowSec = Math.floor(Date.now() / 1000);
    setStartInput(toLocalInput(defaultStartTs ?? nowSec - 30 * DAY));
    setEndInput(toLocalInput(defaultEndTs ?? nowSec));
    setEveryRaw(defaultDecisionEveryBars ? String(defaultDecisionEveryBars) : "");
    setMinConsensusRaw("");
    // 依赖收敛为 [open]：seed 只在弹窗 open false→true 的那一次转换执行，避免父树重渲染/非稳定
    // 默认引用（seededMembers、defaultStrategyIds 等）把正在编辑的表单每秒冲回默认（inputs-revert 复发根因）。
  }, [open]); // eslint-disable-line react-hooks/exhaustive-deps

  const toggleId = (id: number) =>
    setSelectedIds((prev) => (prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]));

  // ---------- 成员编辑（实盘多标的）----------
  const updateAllocation = (target: string, raw: string) => {
    const n = Number(raw);
    setMembers((prev) =>
      prev.map((m) => (m.instId === target ? { ...m, allocationPct: Number.isFinite(n) ? n : 0 } : m)),
    );
  };
  const removeMember = (target: string) =>
    setMembers((prev) => prev.filter((m) => m.instId !== target));
  const applyEvenSplit = () =>
    setMembers((prev) => {
      if (prev.length === 0) return prev;
      const each = Math.floor((100 / prev.length) * 100) / 100;
      return prev.map((m) => ({ ...m, allocationPct: each }));
    });
  const handleAddSymbol = () => {
    const sym = addSymbolRaw.trim().toUpperCase();
    if (!sym || atCap) return;
    if (!isAutoTradable(sym)) {
      setAddError("仅支持 USDT 本位永续 / 交割合约自动交易");
      return;
    }
    if (members.some((m) => m.instId === sym)) {
      setAddError("标的已在列表中");
      return;
    }
    setAddError(null);
    const remaining = Math.max(1, Math.round(100 - totalAllocation(members)));
    const pct = allocationMode === "even" ? Math.min(100, remaining) : 100;
    setMembers((prev) => [...prev, { instId: sym, allocationPct: pct }]);
    setAddSymbolRaw("");
  };

  const capital = parseOptionalUsd(capitalRaw);
  const leverage = parseOptionalLeverage(leverageRaw);
  const takeProfit = parseOptionalUsd(takeProfitRaw);
  const stopLoss = parseOptionalUsd(stopLossRaw);
  const stopLossPct = parseOptionalPct(stopLossPctRaw);
  const takeProfitPct = parseOptionalPct(takeProfitPctRaw);
  const roundsValue = Number.parseInt(roundsRaw, 10);
  const inputError =
    capital.error || leverage.error || takeProfit.error || stopLoss.error ||
    stopLossPct.error || takeProfitPct.error || null;
  const strategyError = validateStrategySelection(selectedIds);
  const roundsError = validateContextRounds(roundsValue);

  // 成员校验（实盘）：非空、皆可自动交易、各 pct ∈ (0,100]，even 模式 Σ ≤ 100（避免提交即 422）。
  const sumPct = totalAllocation(members);
  const hasUnsupportedMember = members.some((m) => !isAutoTradable(m.instId));
  const membersError =
    members.length === 0
      ? "请至少保留一个自动交易标的"
      : hasUnsupportedMember
        ? "含不支持自动交易的标的（仅 USDT 本位永续 / 交割合约）"
        : members.some((m) => !(m.allocationPct > 0 && m.allocationPct <= 100))
          ? "每个标的百分比须在 (0, 100] 之间"
          : allocationMode === "even" && sumPct > 100 + 1e-9
            ? "各标的百分比之和不可超过 100%（或切换为「独立上限」）"
            : null;
  const oversubscribed = allocationMode === "max_ratio" && sumPct > 100 + 1e-9;

  const instSupported = isAutoTradable(instId);   // 回测单标的

  // 回测区间 + 决策间隔 + 计划轮数（R5：UI 默认永不越界自拒）。
  const startTs = fromLocalInput(startInput);
  const endTs = fromLocalInput(endInput);
  const rangeError =
    startTs == null || endTs == null ? "请选择回测区间起止时间" : validateBacktestRange(startTs, endTs);
  const smartEvery = useMemo(
    () =>
      startTs != null && endTs != null
        ? defaultDecisionEvery(startTs, endTs, bar, AI_BACKTEST_DEFAULT_DECISION_EVERY, AI_BACKTEST_MAX_ROUNDS)
        : AI_BACKTEST_DEFAULT_DECISION_EVERY,
    [startTs, endTs, bar],
  );
  const everyOverride = everyRaw.trim() ? Number.parseInt(everyRaw, 10) : null;
  const everyError = everyRaw.trim() && (!Number.isInteger(everyOverride) || (everyOverride ?? 0) < 1)
    ? "决策间隔需为 ≥1 的整数"
    : null;
  const everyValue = everyOverride && everyOverride >= 1 ? everyOverride : smartEvery;
  const minConsensusValue = minConsensusRaw.trim() ? Number.parseInt(minConsensusRaw, 10) : null;
  const minConsensusError =
    minConsensusRaw.trim() && (!Number.isInteger(minConsensusValue) || (minConsensusValue ?? 0) < 0)
      ? "共识门槛需为 ≥0 的整数"
      : null;
  const plannedRounds =
    startTs != null && endTs != null && !rangeError ? planRounds(startTs, endTs, bar, everyValue) : 0;
  const roundsCapError =
    plannedRounds > AI_BACKTEST_MAX_ROUNDS
      ? `计划决策轮数 ${plannedRounds} 超上限 ${AI_BACKTEST_MAX_ROUNDS}，请缩短区间或增大决策间隔`
      : null;

  // 生效总预算预览（分组总额；availUsdt 取 members[0] 的 config，账户级，P2-2）。留空默认仍取保守 avail×1%
  // （deriveDefaultBudget），天花板改用账户级 maxBudgetPct（avail×maxBudgetPct%，原写死 5%）——故改总预算
  // 即按 total×pct/100 联动每标的分配；口径与后端 cap_frac 逐配置严格相等（effectiveMaxBudgetPct/100）。
  const previewBudget = useMemo(() => {
    if (availUsdt == null) return null;
    const raw = capital.value ?? deriveDefaultBudget(availUsdt);
    return clampGroupBudgetToCap(raw, availUsdt, maxBudgetPct);
  }, [availUsdt, capital.value, maxBudgetPct]);
  // 填值超过硬上限时 previewBudget 被静默夹回上限——显式回显「已夹紧」，避免用户误以为输入未生效。
  const isClamped = isGroupBudgetClamped(capital.value, availUsdt, maxBudgetPct);
  const budgetCap = groupBudgetCap(availUsdt, maxBudgetPct);

  const usingDefault = capital.value == null;
  const baseValid = !inputError && !strategyError && !roundsError;
  const ready =
    mode === "live"
      ? baseValid && !membersError && acknowledged
      : baseValid && instSupported && !!instId && !rangeError && !everyError && !minConsensusError && !roundsCapError;

  const handleConfirm = () => {
    if (!ready || submitting) return;
    const base = {
      strategyIds: selectedIds,
      capitalUsd: capital.value,
      leverage: leverage.value,
      takeProfitUsd: takeProfit.value,
      stopLossUsd: stopLoss.value,
      stopLossPct: stopLossPct.value,
      takeProfitPct: takeProfitPct.value,
      positionStepPct: positionStep,                // 仓位操作档位（实盘/回测两 tab 同得）
      contextRefreshRounds: roundsValue,
      bar,                                          // 可编辑周期真正生效（不再被 store.bar 覆盖）
    };
    if (mode === "live") {
      onConfirm({ mode: "live", members, allocationMode, instId: members[0]?.instId ?? instId, ...base });
      return;
    }
    onConfirm({
      mode: "backtest",
      members,
      allocationMode,
      instId,
      ...base,
      startTs: startTs ?? undefined,
      endTs: endTs ?? undefined,
      decisionEveryBars: everyValue,          // 已解析为智能默认，永不越界自拒（R5）
      minConsensus: minConsensusValue,
    });
  };

  const firstError =
    strategyError ||
    roundsError ||
    inputError ||
    (mode === "live" ? membersError : instSupported ? null : "标的不支持自动交易（仅 USDT 本位永续 / 交割合约）") ||
    (mode === "backtest" ? rangeError || everyError || minConsensusError || roundsCapError : null) ||
    error;

  const title = mode === "backtest" ? "AI 自动交易 · 回测" : "启动 AI 自动交易";

  return (
    <Modal open={open} onClose={onClose} labelledBy="ai-trade-launch-title" className="max-w-lg">
      <div className="flex items-center justify-between border-b border-[var(--border)] px-5 py-3.5">
        <h2 id="ai-trade-launch-title" className="text-sm font-semibold text-[var(--text)]">
          {title}
        </h2>
        {mode === "backtest" ? <Badge tone="accent">模拟回测</Badge> : <Badge tone="short">真实资金</Badge>}
      </div>

      <div className="flex max-h-[62vh] flex-col gap-3 overflow-auto px-5 py-4">
        {/* 模式段控（实盘 / 回测）。 */}
        <div className="flex gap-1 rounded-md bg-[var(--surface-2)] p-0.5">
          <ModeTab
            active={mode === "live"}
            disabled={!liveAvailable || submitting}
            title={liveGateReason ?? undefined}
            onClick={() => setMode("live")}
          >
            实盘
          </ModeTab>
          <ModeTab active={mode === "backtest"} disabled={submitting} onClick={() => setMode("backtest")}>
            回测
          </ModeTab>
        </div>

        <p className="text-[11px] leading-relaxed text-[var(--muted)]">
          {mode === "backtest" ? (
            <>
              AI 将在所选历史区间上<strong className="text-[var(--text)]">逐根决策</strong>（纯模拟撮合、
              不下真单、不动真实资金），完成后展示全套回测指标与 AI 决策过程，可先评估再决定是否投入真实资金。
            </>
          ) : (
            <>
              AI 助手将作为决策者，综合研判所选<strong className="text-[var(--text)]">多个策略</strong>的实时信号，
              对下方<strong className="text-[var(--text)]">全部标的</strong>同时在每根 K 线上决定开多 / 开空 /
              平仓 / 观望，真单经既有守护执行器落地并继承全部资金护栏。
            </>
          )}
        </p>

        {/* 时间级别（默认 K 线所选周期，可修改，两模式共用）。 */}
        <label className="flex flex-col gap-1 text-[11px] text-[var(--muted)]">
          <span>时间级别（默认 K 线所选周期，可修改）</span>
          <Select
            value={bar}
            disabled={submitting}
            onChange={(e) => setBar(e.target.value as Bar)}
            className="text-xs"
            aria-label="时间级别"
          >
            {BARS.map((b) => (
              <option key={b} value={b}>
                {b}
              </option>
            ))}
          </Select>
        </label>

        {/* 多选策略清单（≥1、≤上限，两模式共用）。 */}
        <div className="flex flex-col gap-1 text-[11px] text-[var(--muted)]">
          <span>
            交易策略（可多选，AI 综合研判其信号）· 已选{" "}
            <span className="font-medium text-[var(--text)]">{selectedIds.length}</span>/{AI_TRADE_MAX_STRATEGIES}
          </span>
          <div className="max-h-40 overflow-auto rounded-md border border-[var(--border)]">
            {(strategiesQ.data ?? []).length === 0 && (
              <div className="px-3 py-2 text-xs text-[var(--muted)]">
                {strategiesQ.isError ? "策略列表读取失败" : "加载中…"}
              </div>
            )}
            {(strategiesQ.data ?? []).map((s) => (
              <label
                key={s.id}
                className="flex cursor-pointer items-center gap-2 border-t border-[var(--border)] px-3 py-1.5 text-xs first:border-t-0 hover:bg-[var(--surface-2)]"
              >
                <input
                  type="checkbox"
                  checked={selectedIds.includes(s.id)}
                  disabled={submitting}
                  onChange={() => toggleId(s.id)}
                  className="h-3.5 w-3.5 shrink-0 accent-[var(--accent)]"
                />
                <span className="truncate text-[var(--text)]">{s.name}</span>
              </label>
            ))}
          </div>
        </div>

        {mode === "live" ? (
          /* 实盘：多标的分配选择器（默认回填 autoTradeMembers）。 */
          <div className="flex flex-col gap-1.5 text-[11px] text-[var(--muted)]">
            <div className="flex items-center justify-between gap-2">
              <span>
                自动交易标的（默认取自左侧「自动交易」面板）· 已选{" "}
                <span className="font-medium text-[var(--text)]">{members.length}</span>/{AI_TRADE_MAX_SYMBOLS}
              </span>
              <div className="flex items-center gap-1">
                <AllocModeTab active={allocationMode === "even"} disabled={submitting} onClick={() => setAllocationMode("even")}>
                  均分份额
                </AllocModeTab>
                <AllocModeTab active={allocationMode === "max_ratio"} disabled={submitting} onClick={() => setAllocationMode("max_ratio")}>
                  独立上限
                </AllocModeTab>
                <button
                  type="button"
                  disabled={submitting || members.length === 0}
                  onClick={applyEvenSplit}
                  className="rounded border border-[var(--border)] px-2 py-0.5 text-[10px] text-[var(--text)] hover:bg-[var(--surface-2)] disabled:opacity-40"
                >
                  均分
                </button>
              </div>
            </div>
            {truncated && (
              <p className="rounded border border-[var(--border)] bg-[var(--surface-2)] px-2 py-1 text-[10px] leading-relaxed text-[var(--muted)]">
                左侧已配置 {defaultMembers.length} 个标的，已达 AI 自动交易标的上限 {AI_TRADE_MAX_SYMBOLS}，
                仅前 {AI_TRADE_MAX_SYMBOLS} 个入选（可在此手动增删）。
              </p>
            )}
            <div className="overflow-hidden rounded-md border border-[var(--border)]">
              <div className="flex items-center justify-between bg-[var(--surface-2)] px-3 py-1.5 text-[10px] font-semibold uppercase tracking-wide text-[var(--muted)]">
                <span>标的</span>
                <span>{allocationMode === "max_ratio" ? "上限% · 预算预览" : "百分比% · 预算预览"}</span>
              </div>
              {members.map((m) => (
                <div
                  key={m.instId}
                  className="flex items-center justify-between gap-2 border-t border-[var(--border)] px-3 py-1.5 text-xs"
                >
                  <span className="flex items-center gap-1.5 truncate text-[var(--text)]">
                    {m.instId}
                    {!isAutoTradable(m.instId) && (
                      <span className="text-[10px] text-[var(--short)]">不支持</span>
                    )}
                  </span>
                  <span className="flex items-center gap-2">
                    <input
                      type="text"
                      inputMode="decimal"
                      value={String(m.allocationPct)}
                      disabled={submitting}
                      onChange={(e) => updateAllocation(m.instId, e.target.value)}
                      aria-label={`${m.instId} 百分比`}
                      className="w-14 rounded border border-[var(--border)] bg-[var(--bg)] px-1.5 py-1 text-right text-xs text-[var(--text)] outline-none focus:border-[var(--accent)] disabled:opacity-50"
                    />
                    <span className="tabular text-[10px] text-[var(--muted)]">
                      {previewBudget != null
                        ? `${fmtPrice(memberBudget(previewBudget, m.allocationPct))} USDT`
                        : "—"}
                    </span>
                    <button
                      type="button"
                      disabled={submitting}
                      onClick={() => removeMember(m.instId)}
                      aria-label={`移除 ${m.instId}`}
                      className="rounded px-1 text-[var(--muted)] hover:text-[var(--short)] disabled:opacity-40"
                    >
                      ✕
                    </button>
                  </span>
                </div>
              ))}
              {/* 新增标的（达上限即禁用）。 */}
              <div className="flex items-center gap-2 border-t border-[var(--border)] px-3 py-1.5">
                <input
                  type="text"
                  value={addSymbolRaw}
                  placeholder={atCap ? `已达上限 ${AI_TRADE_MAX_SYMBOLS} 个` : "新增标的（USDT 永续 / 交割）"}
                  disabled={submitting || atCap}
                  onChange={(e) => setAddSymbolRaw(e.target.value.trim().toUpperCase())}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") {
                      e.preventDefault();
                      handleAddSymbol();
                    }
                  }}
                  aria-label="新增自动交易标的"
                  className="flex-1 rounded border border-[var(--border)] bg-[var(--bg)] px-2 py-1 text-xs uppercase text-[var(--text)] outline-none focus:border-[var(--accent)] disabled:opacity-50"
                />
                <button
                  type="button"
                  disabled={submitting || atCap || !addSymbolRaw.trim()}
                  onClick={handleAddSymbol}
                  className="rounded border border-[var(--border)] px-2 py-1 text-[11px] text-[var(--text)] hover:bg-[var(--surface-2)] disabled:opacity-40"
                >
                  添加
                </button>
              </div>
            </div>
            {addError && <span className="text-[10px] text-[var(--short)]">{addError}</span>}
            {oversubscribed && (
              <p className="rounded border border-[var(--border)] bg-[var(--surface-2)] px-2 py-1 text-[10px] leading-relaxed text-[var(--muted)]">
                各标的上限合计 {Math.round(sumPct * 100) / 100}%（&gt; 100%）：多标的同时触发时组合敞口可能超过总预算，
                真实敞口仍受总预算上限（可用余额 × {maxBudgetPct}%）、单笔名义上限与单仓 OCO 约束。
              </p>
            )}
          </div>
        ) : (
          /* 回测：仍单标的（默认 members[0]），同享上方可编辑周期。 */
          <label className="flex flex-col gap-1 text-[11px] text-[var(--muted)]">
            <span>标的（回测单标的，默认首个自动交易标的，仅 USDT 本位永续 / 交割合约）</span>
            <input
              type="text"
              value={instId}
              disabled={submitting}
              onChange={(e) => setInstId(e.target.value.trim().toUpperCase())}
              className="rounded-md border border-[var(--border)] bg-[var(--bg)] px-2 py-1.5 text-xs uppercase text-[var(--text)] outline-none focus:border-[var(--accent)] disabled:opacity-50"
            />
          </label>
        )}

        {mode === "backtest" && (
          <div className="grid grid-cols-2 gap-3">
            <DateField label="回测起始" value={startInput} onChange={setStartInput} disabled={submitting} />
            <DateField label="回测结束" value={endInput} onChange={setEndInput} disabled={submitting} />
          </div>
        )}

        {mode === "live" ? (
          <div className="flex flex-col gap-px overflow-hidden rounded-md bg-[var(--border)]">
            <Row label="交易账户" value="设置中心 · 默认实盘账户" />
            <Row
              label="可用余额"
              value={
                availUsdt != null
                  ? `${fmtPrice(availUsdt)} USDT`
                  : configQ.isError
                    ? "读取失败"
                    : "读取中…"
              }
            />
            <Row
              label={
                usingDefault
                  ? "默认总预算（可用余额 × 1%）"
                  : isClamped
                    ? `生效总预算（预览 · 已按 ${maxBudgetPct}% 上限夹紧）`
                    : "生效总预算（预览）"
              }
              value={previewBudget != null ? `${fmtPrice(previewBudget)} USDT` : "—"}
            />
            <Row
              label="生效杠杆"
              value={leverage.value != null ? `${leverage.value}×` : `默认 ${LIVE_AUTO_DEFAULT_LEVERAGE}×`}
            />
            <Row label="单笔名义上限" value={`≤ ${LIVE_MAX_ORDER_NOTIONAL_USD} USDT/单`} />
            <Row
              label="单仓兜底 OCO（交易所侧）"
              value={`SL ${fmtOcoPct(stopLossPct.value, AI_TRADE_MAX_SL_PCT)} / TP ${fmtOcoPct(takeProfitPct.value, AI_TRADE_MAX_TP_PCT)}`}
            />
          </div>
        ) : (
          <div className="flex flex-col gap-px overflow-hidden rounded-md bg-[var(--border)]">
            <Row
              label="计划决策轮数"
              value={
                <span className={roundsCapError ? "text-[var(--short)]" : undefined}>
                  {plannedRounds} 轮 · 间隔 {everyValue} 根/次（上限 {AI_BACKTEST_MAX_ROUNDS}）
                </span>
              }
            />
            <Row label="初始资金" value={capital.value != null ? `${fmtPrice(capital.value)} USDT` : "默认"} />
            <Row
              label="单仓 OCO（模拟撮合）"
              value={`SL ${fmtOcoPct(stopLossPct.value, AI_TRADE_MAX_SL_PCT)} / TP ${fmtOcoPct(takeProfitPct.value, AI_TRADE_MAX_TP_PCT)}`}
            />
            <Row label="手续费 / 滑点" value="taker 5bps / 2bps（同其它回测）" />
          </div>
        )}

        <div className="grid grid-cols-2 gap-3">
          <Field
            label={mode === "backtest" ? "初始资金（USDT）" : "总预算（USDT）"}
            placeholder="留空＝默认"
            value={capitalRaw}
            onChange={setCapitalRaw}
            disabled={submitting}
            hint={
              mode === "live" && isClamped && capital.value != null
                ? `输入 ${fmtPrice(capital.value)} 超过可用余额 ${maxBudgetPct}% 上限（${fmtPrice(budgetCap)} USDT），已按上限 ${fmtPrice(budgetCap)} 计算`
                : undefined
            }
          />
          <Field label="杠杆（×）" placeholder="留空＝默认" value={leverageRaw} onChange={setLeverageRaw} disabled={submitting} />
          <Field label={mode === "backtest" ? "会话止盈（USDT）" : "总止盈（USDT）"} placeholder="留空＝不触发" value={takeProfitRaw} onChange={setTakeProfitRaw} disabled={submitting} />
          <Field label={mode === "backtest" ? "会话止损（USDT）" : "总止损（USDT）"} placeholder="留空＝不触发" value={stopLossRaw} onChange={setStopLossRaw} disabled={submitting} />
          <Field label="单仓止损%（如 0.05＝5%）" placeholder="留空＝默认" value={stopLossPctRaw} onChange={setStopLossPctRaw} disabled={submitting} />
          <Field label="单仓止盈%（如 0.10＝10%）" placeholder="留空＝默认" value={takeProfitPctRaw} onChange={setTakeProfitPctRaw} disabled={submitting} />
        </div>

        {/* 仓位操作档位（两模式共用）：约束 AI 每次开仓规模只能落在离散档位阶梯上，而非任意小数金额。 */}
        <label className="flex flex-col gap-1 text-[11px] text-[var(--muted)]">
          <span>仓位操作档位（AI 每次开仓规模）</span>
          <Select
            value={String(positionStep)}
            disabled={submitting}
            onChange={(e) => setPositionStep(Number(e.target.value))}
            className="text-xs"
            aria-label="仓位操作档位"
          >
            {AI_TRADE_POSITION_STEP_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </Select>
          <span className="leading-relaxed text-[var(--muted)]">
            可用档位：{positionLadder(positionStep).map((g) => `${Math.round(g * 100)}%`).join(" / ")}；档位仅约束
            <strong className="text-[var(--text)]">开仓规模</strong>（非分批加/减仓，平仓为全平），AI 每次开仓将吸附到最近的合法档位。
          </span>
        </label>

        {mode === "backtest" && (
          <div className="grid grid-cols-2 gap-3">
            <Field
              label="决策间隔（根/次）"
              placeholder={String(smartEvery)}
              value={everyRaw}
              onChange={setEveryRaw}
              disabled={submitting}
              hint="AI 每 K 根决策一次，越大越省 LLM（盯市/风控不降频）"
            />
            <Field
              label="共识门槛（可选）"
              placeholder="留空＝默认"
              value={minConsensusRaw}
              onChange={setMinConsensusRaw}
              disabled={submitting}
              hint="开仓需同向策略数 ≥ 此值，0=关闭"
            />
          </div>
        )}

        <Field
          label="上下文刷新轮数"
          placeholder={String(AI_TRADE_DEFAULT_CONTEXT_ROUNDS)}
          value={roundsRaw}
          onChange={setRoundsRaw}
          disabled={submitting}
          hint="每 N 轮把 AI 执行过程摘要为上下文、清空历史保持纯净（保留连续记忆的同时守住 token 预算）"
        />

        {mode === "live" && (
          <label className="flex items-start gap-2 text-xs leading-relaxed text-[var(--text)]">
            <input
              type="checkbox"
              checked={acknowledged}
              disabled={submitting}
              onChange={(e) => onAcknowledge(e.target.checked)}
              aria-label="我已知晓这是真实资金 AI 自动交易"
              className="mt-0.5 h-4 w-4 shrink-0 accent-[var(--accent)]"
            />
            <span>
              我已知晓这是<strong className="text-[var(--short)]">真实资金 AI 自动交易</strong>
              ，AI 将对上述全部标的据所选策略信号自主决定开仓 / 平仓，盈亏由本人承担。
            </span>
          </label>
        )}

        {firstError && (
          <p role="alert" className="text-[11px] font-medium text-[var(--short)]">
            {firstError}
          </p>
        )}
      </div>

      <div className="flex items-center justify-end gap-2 border-t border-[var(--border)] px-5 py-3">
        <Button variant="ghost" size="sm" onClick={onClose} disabled={submitting}>
          取消
        </Button>
        <Button variant="primary" size="sm" onClick={handleConfirm} disabled={!ready || submitting}>
          {mode === "backtest"
            ? submitting
              ? "回测启动中…"
              : "开始 AI 回测"
            : submitting
              ? "启动中…"
              : "确认并开始 AI 自动交易"}
        </Button>
      </div>
    </Modal>
  );
}

/** datetime-local → UTC 秒；空/非法 → null。 */
function fromLocalInput(value: string): number | null {
  if (!value) return null;
  const ms = new Date(value).getTime();
  return Number.isFinite(ms) ? Math.floor(ms / 1000) : null;
}

/** UTC 秒 → 本地 datetime-local 字符串（YYYY-MM-DDTHH:mm）。 */
function toLocalInput(ts: number): string {
  const d = new Date(ts * 1000);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

/** 单仓 OCO 比例预览：留空→「默认」；有值→夹紧到 max 后按百分比展示（如 0.9→「20%」）。 */
function fmtOcoPct(value: number | null, max: number): string {
  if (value == null) return "默认";
  return `${+(clampPct(value, max) * 100).toFixed(2)}%`;
}

function ModeTab({
  active,
  disabled,
  title,
  onClick,
  children,
}: {
  active: boolean;
  disabled?: boolean;
  title?: string;
  onClick: () => void;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      disabled={disabled}
      title={title}
      onClick={onClick}
      className={`flex-1 rounded px-3 py-1.5 text-xs font-medium transition-colors ${
        active
          ? "bg-[var(--surface)] text-[var(--text)] shadow-sm"
          : "text-[var(--muted)] hover:text-[var(--text)]"
      } ${disabled && !active ? "cursor-not-allowed opacity-40" : ""}`}
    >
      {children}
    </button>
  );
}

function AllocModeTab({
  active,
  disabled,
  onClick,
  children,
}: {
  active: boolean;
  disabled?: boolean;
  onClick: () => void;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      disabled={disabled}
      onClick={onClick}
      className={`rounded px-2 py-0.5 text-[10px] font-medium transition-colors ${
        active
          ? "bg-[var(--surface)] text-[var(--text)] shadow-sm"
          : "text-[var(--muted)] hover:text-[var(--text)]"
      } ${disabled && !active ? "cursor-not-allowed opacity-40" : ""}`}
    >
      {children}
    </button>
  );
}

function Row({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-3 bg-[var(--bg)] px-3 py-2 text-xs">
      <span className="text-[var(--muted)]">{label}</span>
      <span className="tabular text-right font-medium text-[var(--text)]">{value}</span>
    </div>
  );
}

function DateField({
  label,
  value,
  onChange,
  disabled,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  disabled?: boolean;
}) {
  return (
    <label className="flex flex-col gap-1 text-[11px] text-[var(--muted)]">
      <span>{label}</span>
      <input
        type="datetime-local"
        value={value}
        disabled={disabled}
        onChange={(e) => onChange(e.target.value)}
        className="rounded-md border border-[var(--border)] bg-[var(--bg)] px-2 py-1.5 text-xs text-[var(--text)] outline-none focus:border-[var(--accent)] disabled:opacity-50"
      />
    </label>
  );
}

function Field({
  label,
  placeholder,
  value,
  onChange,
  disabled,
  hint,
}: {
  label: string;
  placeholder: string;
  value: string;
  onChange: (v: string) => void;
  disabled?: boolean;
  hint?: string;
}) {
  return (
    <label className="flex flex-col gap-1 text-[11px] text-[var(--muted)]">
      <span>{label}</span>
      <input
        type="text"
        inputMode="decimal"
        value={value}
        placeholder={placeholder}
        disabled={disabled}
        onChange={(e) => onChange(e.target.value)}
        className="rounded-md border border-[var(--border)] bg-[var(--bg)] px-2 py-1.5 text-xs text-[var(--text)] outline-none focus:border-[var(--accent)] disabled:opacity-50"
      />
      {hint && <span className="leading-relaxed text-[var(--muted)]">{hint}</span>}
    </label>
  );
}
