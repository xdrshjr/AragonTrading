import "@testing-library/jest-dom/vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { AiTradeGroupState, AiTradeRoundTrace, AiTradeSessionRow } from "@/lib/aiTradeTypes";
import { useAppStore } from "@/lib/store";
import AgentPanel from "./AgentPanel";
import AiTradeAgentTrace from "./AiTradeAgentTrace";

// AgentPanel 依赖：WS 与 REST 全桩化，避免真实连接 / 网络。
vi.mock("@/lib/agentWs", () => ({
  agentWS: vi.fn(() => ({ connect: vi.fn(), close: vi.fn(), send: vi.fn() })),
}));
vi.mock("@/lib/agentApi", () => ({
  agentApi: {
    messages: vi.fn().mockResolvedValue([]),
    clear: vi.fn().mockResolvedValue({}),
    config: vi.fn(),
  },
}));
// AiTradeToggle 头部控件（含自身守门查询）：桩为占位，聚焦分段视图行为。
vi.mock("./AiTradeToggle", () => ({ default: () => <div data-testid="ai-trade-toggle" /> }));

// jsdom 未实现 scrollIntoView（时间线贴底跟随会调用），补一个无副作用桩。
Element.prototype.scrollIntoView = vi.fn();

const SESSION: AiTradeSessionRow = {
  sessionId: "s1",
  runId: 1,
  state: "running",
  instId: "BTC-USDT-SWAP",
  bar: "1H",
  strategyIds: [1],
  strategyNames: ["S"],
  budgetUsd: 100,
  leverage: 3,
  contextRefreshRounds: 10,
  roundsCompleted: 2,
  roundsSinceSummary: 1,
  positionSide: "flat",
  sessionPnlUsd: 12.5,
  startedTs: 0,
};

// 分组封装：把单会话视图包成 1 成员分组（AiTradeAgentTrace 现按分组 + 聚焦标的渲染）。
function groupWith(session: AiTradeSessionRow, traces: AiTradeRoundTrace[] = []): AiTradeGroupState {
  return {
    groupId: "g1",
    bar: session.bar,
    strategyIds: session.strategyIds,
    strategyNames: session.strategyNames,
    totalBudgetUsd: session.budgetUsd,
    leverage: session.leverage,
    contextRefreshRounds: session.contextRefreshRounds,
    state: session.state === "stopped" ? "stopped" : "running",
    startedTs: session.startedTs,
    members: {
      [session.instId]: {
        instId: session.instId,
        sessionId: session.sessionId,
        allocationPct: 100,
        session,
        trades: [],
        position: null,
        decisions: [],
        traces,
        summary: null,
      },
    },
  };
}

function round(overrides: Partial<AiTradeRoundTrace>): AiTradeRoundTrace {
  return {
    round: 1,
    ts: 1_700_000_000,
    lastPx: 63000,
    signalBoard: [],
    thinking: "",
    analysis: "",
    toolCallPending: false,
    toolCall: null,
    executed: null,
    degradeReason: null,
    summary: null,
    errorMessage: null,
    state: "deciding",
    ...overrides,
  };
}

afterEach(() => cleanup());

describe("AiTradeAgentTrace", () => {
  it("running session with no traces shows the waiting empty state", () => {
    render(<AiTradeAgentTrace group={groupWith(SESSION)} activeInstId="BTC-USDT-SWAP" />);
    expect(screen.getByText(/正在等待首个确认 K 线/)).toBeInTheDocument();
    expect(screen.getByText("运行中")).toBeInTheDocument();
  });

  it("streaming round renders analysis text under a 研判中 chip", () => {
    render(
      <AiTradeAgentTrace
        group={groupWith(SESSION, [round({ analysis: "多数策略维持多头立场", state: "deciding" })])}
        activeInstId="BTC-USDT-SWAP"
      />,
    );
    expect(screen.getByText("多数策略维持多头立场")).toBeInTheDocument();
    expect(screen.getByText("研判中…")).toBeInTheDocument();
  });

  it("executed round shows the decision tool card and long-toned result", () => {
    render(
      <AiTradeAgentTrace
        group={groupWith(SESSION, [
          round({
            state: "done",
            analysis: "开多",
            toolCall: { action: "open_long", sizeFraction: 0.4, confidence: 0.72, reason: "顺势" },
            executed: { ok: true, traded: true, positionAfter: "long", action: "open_long" },
          }),
        ])}
        activeInstId="BTC-USDT-SWAP"
      />,
    );
    expect(screen.getByText("决策工具 · 开多")).toBeInTheDocument();
    const result = screen.getByText("已成交");
    expect(result).toBeInTheDocument();
    expect(result.parentElement?.getAttribute("style") ?? "").toContain("--long");
  });

  it("degraded round shows the downgrade result", () => {
    render(
      <AiTradeAgentTrace
        group={groupWith(SESSION, [
          round({
            state: "done",
            toolCall: { action: "hold", reason: "共识不足" },
            executed: { ok: true, traded: false, positionAfter: "flat", action: "hold" },
            degradeReason: "low_consensus(0/1)",
          }),
        ])}
        activeInstId="BTC-USDT-SWAP"
      />,
    );
    expect(screen.getByText(/已降级观望/)).toBeInTheDocument();
  });

  it("trade-intent round renders the 二次反思 segment with an abort verdict chip", () => {
    render(
      <AiTradeAgentTrace
        group={groupWith(SESSION, [
          round({
            state: "done",
            analysis: "开多",
            toolCall: { action: "hold", reason: "追多风险高" },
            executed: { ok: true, traded: false, positionAfter: "flat", action: "hold" },
            degradeReason: "reflection_abort",
            reflection: "高周期顶背离，追多风险高",
            reflectionVerdict: "abort",
            reflectionReason: "追多风险高",
          }),
        ])}
        activeInstId="BTC-USDT-SWAP"
      />,
    );
    expect(screen.getByText(/二次反思（ReAct 校验）/)).toBeInTheDocument();
    expect(screen.getByText("二次反思否决 · 观望")).toBeInTheDocument();
    expect(screen.getByText("高周期顶背离，追多风险高")).toBeInTheDocument();
  });

  it("hold round without reflection does not render the 二次反思 segment", () => {
    render(
      <AiTradeAgentTrace
        group={groupWith(SESSION, [
          round({
            state: "done",
            toolCall: { action: "hold", reason: "观望" },
            executed: { ok: true, traded: false, positionAfter: "flat", action: "hold" },
          }),
        ])}
        activeInstId="BTC-USDT-SWAP"
      />,
    );
    expect(screen.queryByText(/二次反思（ReAct 校验）/)).not.toBeInTheDocument();
  });

  it("confirm-with-shrink round shows the reflection confirm chip and shrunk size", () => {
    render(
      <AiTradeAgentTrace
        group={groupWith(SESSION, [
          round({
            state: "done",
            toolCall: { action: "open_long", sizeFraction: 0.2, confidence: 0.7, reason: "顺势" },
            executed: { ok: true, traded: true, positionAfter: "long", action: "open_long" },
            reflection: "方向可，仓位略重",
            reflectionVerdict: "confirm",
            reflectionReason: "减仓更稳",
            reflectionAdjustedSize: 0.2,
          }),
        ])}
        activeInstId="BTC-USDT-SWAP"
      />,
    );
    expect(screen.getByText("二次反思确认执行")).toBeInTheDocument();
    expect(screen.getByText(/收缩后仓位\s*20%/)).toBeInTheDocument();
  });

  it("summary round renders an expandable context-refresh node", () => {
    render(
      <AiTradeAgentTrace
        group={groupWith(SESSION, [round({ state: "done", summary: "截至第1轮：偏多" })])}
        activeInstId="BTC-USDT-SWAP"
      />,
    );
    expect(screen.getByText(/上下文已刷新/)).toBeInTheDocument();
    expect(screen.getByText("截至第1轮：偏多")).toBeInTheDocument();
  });

  it("stopped session with no traces reads as a stopped empty state", () => {
    render(
      <AiTradeAgentTrace group={groupWith({ ...SESSION, state: "stopped" })} activeInstId="BTC-USDT-SWAP" />,
    );
    expect(screen.getByText(/会话已停止/)).toBeInTheDocument();
    expect(screen.getByText("已停止")).toBeInTheDocument();
  });

  it("shows a symbol switcher for a multi-symbol group and switches focus", () => {
    const eth: AiTradeSessionRow = { ...SESSION, sessionId: "s2", instId: "ETH-USDT-SWAP" };
    const group = groupWith(SESSION, [round({ analysis: "BTC 分析", state: "deciding" })]);
    group.members["ETH-USDT-SWAP"] = {
      instId: "ETH-USDT-SWAP",
      sessionId: "s2",
      allocationPct: 50,
      session: eth,
      trades: [],
      position: null,
      decisions: [],
      traces: [round({ analysis: "ETH 分析", state: "deciding" })],
      summary: null,
    };
    render(<AiTradeAgentTrace group={group} activeInstId="BTC-USDT-SWAP" />);
    expect(screen.getByText("BTC 分析")).toBeInTheDocument();
    // 切到 ETH 标的段控 → 展示该成员轨迹（切换不丢历史）。
    fireEvent.click(screen.getByRole("tab", { name: "ETH-USDT-SWAP" }));
    expect(screen.getByText("ETH 分析")).toBeInTheDocument();
  });

  it("round header shows the trigger reason and time without a round number (R1)", () => {
    render(
      <AiTradeAgentTrace
        group={groupWith(SESSION, [
          round({
            state: "done",
            analysis: "顺势维持多头",
            signalBoard: [
              { strategyId: 1, name: "均线突破", stance: "long", latestSignal: "open_long", strength: 0.8, reason: "" },
            ],
          }),
        ])}
        activeInstId="BTC-USDT-SWAP"
      />,
    );
    expect(screen.getByText(/开多信号/)).toBeInTheDocument();
    expect(screen.getByText(/\d{2}:\d{2}/)).toBeInTheDocument();
    expect(screen.queryByText(/第\s*\d+\s*轮/)).not.toBeInTheDocument();
  });

  it("overview omits the round-number badge and reads as a refresh countdown (R1)", () => {
    render(
      <AiTradeAgentTrace
        group={groupWith(SESSION, [round({ state: "done", analysis: "常规复盘" })])}
        activeInstId="BTC-USDT-SWAP"
      />,
    );
    expect(screen.getByText(/距上下文刷新/)).toBeInTheDocument();
    expect(screen.queryByText(/第\s*\d+\s*轮/)).not.toBeInTheDocument();
  });

  it("shows multi-timeframe evidence long-to-short with stale and missing states", () => {
    const frame = (bar: string, stale: boolean) => ({
      bar, sampleCount: 80, closeTs: 1, ageSeconds: stale ? 999999 : 0, stale,
      regime: { primary: "trend_up", volatility: "normal", event: "none", confidence: 0.8, labels: [] },
      digest: { priceAction: null, volume: null, factors: null },
    });
    render(
      <AiTradeAgentTrace
        group={groupWith(SESSION, [round({
          state: "done",
          multiTimeframeContext: {
            executionBar: "1H", asOfTs: 1,
            frames: [frame("1H", false), frame("1D", true)],
            trendAlignment: { status: "bullish", bullishBars: ["1D", "1H"], bearishBars: [], rangeBars: [] },
            rsiOverboughtBars: [], rsiOversoldBars: [],
            nearestSupport: { price: 62000, distancePct: 1.59, bars: ["1D"] },
            nearestResistance: null, missingBars: ["12H", "4H"], staleBars: ["1D"],
          },
        })])}
        activeInstId="BTC-USDT-SWAP"
      />,
    );
    const frames = screen.getAllByTestId("mtf-frame");
    expect(frames[0]).toHaveTextContent("1D");
    expect(frames[1]).toHaveTextContent("1H");
    expect(frames[0]).toHaveTextContent("数据陈旧");
    expect(screen.getByText(/缺失周期.*12H.*4H/)).toBeInTheDocument();
    expect(screen.getByText(/候选支撑/)).toBeInTheDocument();
  });

  // ---------- 唤醒感知上下文（wake-aware-context §6.1 T-18）----------
  const tacticalFrame = (bar: string, closedBars: number | null) => ({
    bar,
    sampleCount: 120,
    closeTs: 1,
    ageSeconds: 42,
    stale: false,
    closedBars,
    regime: { primary: "range", volatility: "normal", event: "none", confidence: 0.6, labels: [] },
    digest: {
      priceAction: null,
      volume: null,
      factors: {
        rsi14: { value: 49, label: "neutral" },
        macdHist: { value: 0, label: "flat" },
        bollPctB: { value: 0.5, label: "mid" },
        atrPct: { value: 0.3, label: "low" },
        rocPct: { value: 0, label: "flat" },
      },
    },
  });

  const TACTICAL = {
    executionBar: "1H",
    wakeIntervalS: 180,
    asOfTs: 1,
    executionPrimary: "trend_up",
    alignment: "diverged" as const,
    changeSinceLastRound: { pct: 0.42, atrMult: 0.91, atrBar: "1H" },
    frames: [tacticalFrame("5m", 1), tacticalFrame("1m", 3)],
    missingBars: [],
    staleBars: [],
  };

  it("renders the tactical block coarse-to-fine with its shared change line", () => {
    render(
      <AiTradeAgentTrace
        group={groupWith(SESSION, [round({ state: "done", tacticalContext: TACTICAL })])}
        activeInstId="BTC-USDT-SWAP"
      />,
    );
    const frames = screen.getAllByTestId("tactical-frame");
    expect(frames).toHaveLength(2);
    expect(frames[0]).toHaveTextContent("5m");
    expect(frames[1]).toHaveTextContent("1m");
    expect(frames[1]).toHaveTextContent("自上轮收 3 根");
    expect(screen.getByText(/战术周期 · 与执行周期背离/)).toBeInTheDocument();
    expect(screen.getByText(/自上一决策轮：价 \+0.42%（≈0.91×1H ATR）/)).toBeInTheDocument();
  });

  it("shows the wake reason in the round header (七态中文标签)", () => {
    render(
      <AiTradeAgentTrace
        group={groupWith(SESSION, [
          round({
            state: "done",
            signalBoard: [],
            trigger: "interval",
            wake: { kind: "price_breakout", trigger: "interval" },
          }),
        ])}
        activeInstId="BTC-USDT-SWAP"
      />,
    );
    expect(screen.getByText("价格异动 · 越过唤醒阈值")).toBeInTheDocument();
    expect(screen.queryByText("周期唤醒 · 常规复盘")).not.toBeInTheDocument();
  });

  it("spells out the wake criterion with its actual numbers (why it woke / why it did not)", () => {
    render(
      <AiTradeAgentTrace
        group={groupWith(SESSION, [
          round({
            state: "done",
            wake: {
              kind: "price_breakout",
              trigger: "interval",
              executionBar: "1H",
              sinceLastRoundS: 182,
              priceMovePct: 0.42,
              priceMoveAtr: 0.91,
              moveThresholdAtr: 0.5,
              horizonScale: 0.316,
              moveThresholdAtrEff: 0.158,
            },
          }),
        ])}
        activeInstId="BTC-USDT-SWAP"
      />,
    );
    const line = screen.getByTestId("wake-reason");
    expect(line).toHaveTextContent("0.91×1H ATR");
    // 门槛必须是**折算后**的实际判据，不是配置里的 0.5（说的和做的必须一致）
    expect(line).toHaveTextContent("阈值 0.16×1H ATR");
  });

  it("keeps the whole 输入信号板 section alive on a tactical-only round (early-return gate)", () => {
    // 板空 + 无 regime/consensus/digest/MTF、**仅有** tacticalContext：早退门控漏了 tactical 时，
    // 整段会静默消失（跑不出错、永远空白）。这正是本仓库 RV-2 的原型复发形态。
    render(
      <AiTradeAgentTrace
        group={groupWith(SESSION, [
          round({
            state: "done",
            signalBoard: [],
            marketContext: null,
            consensus: null,
            marketDigest: null,
            multiTimeframeContext: null,
            tacticalContext: TACTICAL,
          }),
        ])}
        activeInstId="BTC-USDT-SWAP"
      />,
    );
    expect(screen.getByText("输入信号板")).toBeInTheDocument();
    expect(screen.getAllByTestId("tactical-frame")).toHaveLength(2);
  });

  it("keeps the section alive on a trend-verdict-only round (F-2 早退门控回归护栏)", () => {
    // 板空 + 其余输入全空、**仅有** trendVerdict：早退门控漏了它，整段「输入信号板」会静默
    // 消失（跑不出错、永远空白）。这是同一处门控在本轮的第三次复发形态，故必须有专属用例。
    render(
      <AiTradeAgentTrace
        group={groupWith(SESSION, [
          round({
            state: "done",
            signalBoard: [],
            marketContext: null,
            consensus: null,
            marketDigest: null,
            multiTimeframeContext: null,
            tacticalContext: null,
            trendVerdict: {
              bar: "15m",
              asOfTs: 1,
              score: -0.32,
              execScore: -0.61,
              direction: "down",
              bias: "short",
              strength: "strong",
              verdictConfidence: 0.59,
              components: [
                { key: "regime", label: "体制结构", score: -0.72, weight: 0.26, detail: "…" },
              ],
              missing: [],
              availableWeight: 0.86,
              execAvailableWeight: 0.84,
              higherTimeframe: {
                status: "bullish",
                agree: false,
                bullishBars: ["1D"],
                bearishBars: [],
              },
              tacticalAlignment: null,
              conflict: "htf_conflict",
              bonusWithheld: true,
              counterGate: { armed: true, minConfidence: 0.65, sizeMult: 0.5 },
              degraded: false,
              degradeReason: null,
            },
          }),
        ])}
        activeInstId="BTC-USDT-SWAP"
      />,
    );
    expect(screen.getByText("输入信号板")).toBeInTheDocument();
    const evidence = screen.getByTestId("trend-verdict-evidence");
    expect(evidence).toHaveTextContent("偏空");
    expect(evidence).toHaveTextContent("只由 15m 证据算出");
    // 高周期的硬边界必须写出来（它是本需求「判在我所选级别」的可执行译文）。
    expect(evidence).toHaveTextContent("不能反转本级别的方向");
  });

  it("renders the trend-bias gate row on both of its lanes (write point needs a read point)", () => {
    // `trendGate` 是 `tool_call` 相位的兄弟键、走 store 的逐键手抄轨。少了这一行渲染，它就是
    // 一份「后端算了、类型补了、没有渲染落点」的死数据——尤其是**逆势放行并被强制降档**那一档，
    // 它在别处（降级码文案 / 层名）完全不可见，因为那一轮压根没有降级码。
    const { unmount } = render(
      <AiTradeAgentTrace
        group={groupWith(SESSION, [
          round({
            state: "done",
            toolCall: { action: "open_short", sizeFraction: 0.25, confidence: 0.8, reason: "逆势" },
            trendGate: {
              lane: "counter",
              alignment: "counter",
              strength: "strong",
              score: -0.32,
              execScore: -0.61,
              minConfidence: 0.65,
              modelConfidence: 0.8,
              verdictConfidence: 0.59,
              sizeMult: 0.5,
              sizeBefore: 0.5,
              sizeAfter: 0.25,
              reason: null,
            },
          }),
        ])}
        activeInstId="BTC-USDT-SWAP"
      />,
    );
    const passed = screen.getByTestId("trend-gate-row");
    expect(passed).toHaveTextContent("逆势");
    expect(passed).toHaveTextContent("50% → 25%");
    unmount();

    render(
      <AiTradeAgentTrace
        group={groupWith(SESSION, [
          round({
            state: "done",
            toolCall: { action: "hold", reason: "逆势置信度不足" },
            trendGate: {
              lane: "veto",
              alignment: "counter",
              strength: "strong",
              score: -0.32,
              execScore: -0.61,
              minConfidence: 0.65,
              // ★ 判据取模型自报值；`verdictConfidence` 再高也不放行（取错方向正好相反）。
              modelConfidence: 0.4,
              verdictConfidence: 0.99,
              sizeMult: 0.5,
              sizeBefore: 0.5,
              sizeAfter: null,
              reason: "counter_trend_confidence(score=-0.61,conf=0.40/0.65)",
            },
          }),
        ])}
        activeInstId="BTC-USDT-SWAP"
      />,
    );
    const vetoed = screen.getByTestId("trend-gate-row");
    expect(vetoed).toHaveTextContent("模型自报置信 40%");
    expect(vetoed).toHaveTextContent("门槛 65%");
    expect(vetoed).not.toHaveTextContent("99%");
  });

  it("keeps the section alive on a backlog-only round and renders the catch-up evidence", () => {
    // 只有补跑信息、其余输入全空的轮次**仍要渲染**（镜像点 12：早退门控漏了 backlog 就整段空白）。
    // 这里正是上一轮 RV-2 的原址——新增边缘态字段必须验证渲染门控没把该态排除掉。
    render(
      <AiTradeAgentTrace
        group={groupWith(SESSION, [
          round({
            state: "done",
            signalBoard: [],
            marketContext: null,
            consensus: null,
            marketDigest: null,
            multiTimeframeContext: null,
            tacticalContext: null,
            wake: {
              kind: "bar_advanced",
              trigger: "interval",
              backlog: { droppedBars: 1, droppedIntervals: 3, waitedS: 259, chain: 1 },
            },
          }),
        ])}
        activeInstId="BTC-USDT-SWAP"
      />,
    );
    expect(screen.getByText("输入信号板")).toBeInTheDocument();
    const backlog = screen.getByTestId("wake-backlog");
    expect(backlog).toHaveTextContent("1 根 K 线收线未被研判");
    expect(backlog).toHaveTextContent("3 次定时唤醒被丢弃");
    expect(backlog).toHaveTextContent("最早的一次已等待 259s");
    // 轮头徽标同步带上补跑标记
    expect(screen.getByText("补看新 K 线 · 首次研判 · 补跑")).toBeInTheDocument();
  });

  it("keeps the section alive on a flip-only round and always states it is provisional", () => {
    // 翻转是**未收线**的预演结论。UI 把它呈现成既成事实，就等于用界面撤销了后端那条安全隔离。
    render(
      <AiTradeAgentTrace
        group={groupWith(SESSION, [
          round({
            state: "done",
            signalBoard: [],
            marketContext: null,
            consensus: null,
            marketDigest: null,
            multiTimeframeContext: null,
            tacticalContext: null,
            wake: {
              kind: "signal_flip",
              trigger: "interval",
              flip: {
                netStanceFrom: "flat",
                netStanceTo: "long",
                strategies: [{ strategyId: 7, name: "EMA 交叉短线", from: "flat", to: "long" }],
                provisional: true,
                heldS: 47.3,
                samples: 4,
                barTs: 1785600000,
              },
            },
          }),
        ])}
        activeInstId="BTC-USDT-SWAP"
      />,
    );
    expect(screen.getByText("输入信号板")).toBeInTheDocument();
    const flip = screen.getByTestId("wake-flip");
    expect(flip).toHaveTextContent("EMA 交叉短线");
    expect(flip).toHaveTextContent("已保持 47s");
    expect(flip).toHaveTextContent("4 次有效观测");
    expect(flip).toHaveTextContent("repaint");
    expect(flip).toHaveTextContent("仍以最近确认根为准");
    expect(screen.getByText("策略立场翻转 · 未收线预演")).toBeInTheDocument();
  });

  it("auto-resumes bottom-follow after 2 minutes idle and re-scrolls to latest (R2)", () => {
    vi.useFakeTimers();
    try {
      render(
        <AiTradeAgentTrace
          group={groupWith(SESSION, [round({ state: "done", analysis: "复盘" })])}
          activeInstId="BTC-USDT-SWAP"
        />,
      );
      const el = screen.getByTestId("ai-trade-trace-scroll");
      Object.defineProperty(el, "scrollHeight", { value: 1000, configurable: true });
      Object.defineProperty(el, "clientHeight", { value: 200, configurable: true });
      Object.defineProperty(el, "scrollTop", { value: 0, configurable: true });
      fireEvent.scroll(el);
      expect(screen.getByRole("button", { name: "回到最新" })).toBeInTheDocument();
      (Element.prototype.scrollIntoView as unknown as ReturnType<typeof vi.fn>).mockClear();
      act(() => {
        vi.advanceTimersByTime(120_000);
      });
      expect(screen.queryByRole("button", { name: "回到最新" })).not.toBeInTheDocument();
      expect(Element.prototype.scrollIntoView).toHaveBeenCalled();
    } finally {
      vi.useRealTimers();
    }
  });

  it("resets the 2-minute idle timer on each further scroll (R2)", () => {
    vi.useFakeTimers();
    try {
      render(
        <AiTradeAgentTrace
          group={groupWith(SESSION, [round({ state: "done", analysis: "复盘" })])}
          activeInstId="BTC-USDT-SWAP"
        />,
      );
      const el = screen.getByTestId("ai-trade-trace-scroll");
      Object.defineProperty(el, "scrollHeight", { value: 1000, configurable: true });
      Object.defineProperty(el, "clientHeight", { value: 200, configurable: true });
      Object.defineProperty(el, "scrollTop", { value: 0, configurable: true });
      fireEvent.scroll(el);
      expect(screen.getByRole("button", { name: "回到最新" })).toBeInTheDocument();
      act(() => {
        vi.advanceTimersByTime(60_000);
      });
      fireEvent.scroll(el); // 仍离底 → 重置 120s 倒计时
      act(() => {
        vi.advanceTimersByTime(90_000); // 距上次滚动仅 90s < 120s
      });
      expect(screen.getByRole("button", { name: "回到最新" })).toBeInTheDocument();
      act(() => {
        vi.advanceTimersByTime(30_000); // 距上次滚动累计 120s
      });
      expect(screen.queryByRole("button", { name: "回到最新" })).not.toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });
});

// ---------------- 利润保护段（ai-trade-profit-protection §2.7 / §六.4）----------------
describe("AiTradeAgentTrace · 利润保护段", () => {
  it("armed 快照渲染峰值 / 已回吐 / 有效退出线", () => {
    render(
      <AiTradeAgentTrace
        group={groupWith(SESSION, [
          round({
            profitGuard: {
              ruleId: "profit_trailing",
              status: "armed",
              peakUnrealizedPnlUsd: 0.302,
              givebackRatio: 0.2,
              effectiveExitPx: 0.0729,
            },
          }),
        ])}
        activeInstId="BTC-USDT-SWAP"
      />,
    );
    expect(screen.getByText(/已回吐 20\.0%/)).toBeInTheDocument();
    expect(screen.getByText("利润保护 · 移动止盈已武装")).toBeInTheDocument();
  });

  it("承诺退出线（commitment C4）在保护段可见 + 「当前由它定住」（只读 effectiveExitSource）", () => {
    render(
      <AiTradeAgentTrace
        group={groupWith(SESSION, [
          round({
            profitGuard: {
              ruleId: "profit_trailing",
              status: "armed",
              peakUnrealizedPnlUsd: 0.302,
              givebackRatio: 0.2,
              effectiveExitPx: 0.07298,
              committedExitPx: 0.07298,
              committedExitBasis: "adopted",
              effectiveExitSource: "profit_guard_committed_exit",
            },
          }),
        ])}
        activeInstId="BTC-USDT-SWAP"
      />,
    );
    const committed = screen.getByTestId("trace-profit-committed");
    expect(committed).toHaveTextContent("承诺退出线 0.07298");
    expect(committed).toHaveTextContent("当前由它定住");
  });

  it("无承诺线 → 承诺行不渲染（缺省逐字节回退）", () => {
    render(
      <AiTradeAgentTrace
        group={groupWith(SESSION, [
          round({
            profitGuard: {
              ruleId: "profit_trailing",
              status: "armed",
              peakUnrealizedPnlUsd: 0.302,
              givebackRatio: 0.2,
              effectiveExitPx: 0.0729,
            },
          }),
        ])}
        activeInstId="BTC-USDT-SWAP"
      />,
    );
    expect(screen.queryByTestId("trace-profit-committed")).not.toBeInTheDocument();
  });

  it("平仓后的 flat 空壳快照仍渲染 lastExit 回执（P1-7 的唯一展示点）", () => {
    // 回执只在保护平仓后的那一轮出现，彼时仓位已平 ⇒ status 恒为 flat。若整段按状态徽标早退，
    // 「上一笔仓位为何消失」就会在 UI 上被悄悄吞掉——本用例是该回归的护栏。
    render(
      <AiTradeAgentTrace
        group={groupWith(SESSION, [
          round({
            profitGuard: {
              ruleId: "profit_trailing",
              status: "flat",
              lastExit: { label: "移动止盈", exitPx: 0.0729, pnlUsd: 0.181 },
            },
          }),
        ])}
        activeInstId="BTC-USDT-SWAP"
      />,
    );
    expect(screen.getByText(/上一笔持仓已由利润保护层强制平仓（移动止盈）/)).toBeInTheDocument();
  });

  it("交易所侧未挂 OCO 时，不可用文案不得宣称「仍生效」", () => {
    render(
      <AiTradeAgentTrace
        group={groupWith(SESSION, [
          round({
            profitGuard: {
              ruleId: "profit_trailing",
              status: "unavailable",
              unavailableReason: "stale_feed",
            },
          }),
        ])}
        activeInstId="BTC-USDT-SWAP"
      />,
    );
    expect(screen.getByText(/没有任何自动兜底/)).toBeInTheDocument();
    expect(screen.queryByText(/仅交易所侧止盈止损仍生效/)).not.toBeInTheDocument();
  });

  it("快照缺失（规则未装配 / 老轨迹）→ 整段不渲染", () => {
    render(
      <AiTradeAgentTrace group={groupWith(SESSION, [round({})])} activeInstId="BTC-USDT-SWAP" />,
    );
    expect(screen.queryByText(/利润保护（移动止盈 \/ 保本）/)).not.toBeInTheDocument();
  });

  // 冷却降级轮里持仓已平（快照多为 flat 空壳甚至整键缺席），而「本轮为什么被降级为观望」恰恰
  // 要在轨迹上说清楚——按快照早退就会让用户只看到一个没有理由的 hold（adaptive §4.2 载体二）。
  it("冷却轮即便没有保护快照也渲染冷却原因与剩余时间", () => {
    render(
      <AiTradeAgentTrace
        group={groupWith(SESSION, [
          round({
            profitGuardCooldown: {
              activeUntilTs: 1_700_000_180,
              remainingSeconds: 120,
              scope: "same_side",
              blockedSide: "long",
              reasonCode: "profit_guard_trailing",
              label: "移动止盈",
            },
          }),
        ])}
        activeInstId="BTC-USDT-SWAP"
      />,
    );
    const chip = screen.getByTestId("trace-profit-cooldown");
    expect(chip).toHaveTextContent("120s");
    expect(chip).toHaveTextContent("暂停做多开仓（反向不受影响）");
  });
});

// ---------------- 仓位与风险段（vol-sizing-and-drawdown-brake §6.3 第 7a/7b 编辑点）----------------
// 本组用例守的是**挂载**而不只是定义：本仓库有过「组件写好了但不在渲染树里——数据到了、代码全绿、
// 界面上什么都没有」的同类事故，`tsc` 与逐函数单测都不会替你发现少挂了一个。
describe("AiTradeAgentTrace · 仓位与风险段", () => {
  it("三条规则全未装配（默认）⇒ 整段不渲染", () => {
    render(
      <AiTradeAgentTrace group={groupWith(SESSION, [round({})])} activeInstId="BTC-USDT-SWAP" />,
    );
    expect(screen.queryByText(/仓位与风险/)).not.toBeInTheDocument();
  });

  it("装配后渲染：吸附前/后两个数同时可见（差值即被档位地板吃掉的量）", () => {
    render(
      <AiTradeAgentTrace
        group={groupWith(SESSION, [
          round({
            brakeLevel: "slow",
            stopSource: "atr",
            modelFraction: 0.75,
            finalFraction: 0.25,
            fFinalRaw: 0.135,
            fVol: 0.28,
            fKelly: 0.21,
            scaleErased: true,
            scaleSource: "brake",
            stopPct: 0.021,
            takePct: 0.042,
            drawdownPct: 0.092,
          }),
        ])}
        activeInstId="BTC-USDT-SWAP"
      />,
    );
    expect(screen.getByText(/仓位与风险/)).toBeInTheDocument();
    expect(screen.getByText("仓位层缩减 · 0.75→0.25")).toBeInTheDocument();
    expect(screen.getByText(/吸附前 0\.135/)).toBeInTheDocument();
    expect(screen.getByText(/实际下单占比 0\.25/)).toBeInTheDocument();
    expect(screen.getByText(/止损 ±2\.10% \/ 止盈 4\.20%/)).toBeInTheDocument();
  });

  it("第二档「只平不开」把降级码中文化，并写清平仓不受影响", () => {
    render(
      <AiTradeAgentTrace
        group={groupWith(SESSION, [
          round({
            brakeLevel: "halt",
            drawdownPct: 0.162,
            brakeReason: "drawdown_brake_halt(dd=16.2%)",
            stopSource: "fixed",
          }),
        ])}
        activeInstId="BTC-USDT-SWAP"
      />,
    );
    expect(screen.getByText(/回撤刹车 · 只平不开/)).toBeInTheDocument();
    expect(screen.getByText(/暂停开新仓（平仓不受影响）/)).toBeInTheDocument();
  });
});

describe("AgentPanel segmented view", () => {
  beforeEach(() => {
    localStorage.clear();
    useAppStore.setState({ agentConversationId: "conv-test", aiTradeGroup: null });
  });

  it("hides the 执行轨迹 tab and renders chat when there is no AI trade group", () => {
    render(<AgentPanel />);
    expect(screen.queryByRole("tab", { name: /执行轨迹/ })).not.toBeInTheDocument();
    expect(screen.getByText("AI 交易助手")).toBeInTheDocument(); // 聊天空态逐字不变
  });

  it("auto-switches to the trace view when a group appears", () => {
    render(<AgentPanel />);
    act(() => {
      useAppStore.setState({ aiTradeGroup: groupWith(SESSION) });
    });
    expect(screen.getByRole("tab", { name: /执行轨迹/ })).toBeInTheDocument();
    expect(screen.getByText("运行中")).toBeInTheDocument(); // 轨迹概览条已呈现
  });

  it("shows no round-number badge on the 执行轨迹 tab (R1, P0-1)", () => {
    render(<AgentPanel />);
    act(() => {
      useAppStore.setState({ aiTradeGroup: groupWith(SESSION) });
    });
    expect(screen.getByRole("tab", { name: /执行轨迹/ })).toBeInTheDocument();
    expect(screen.queryByText(/第\s*\d+\s*轮/)).not.toBeInTheDocument();
  });

  it("respects a manual switch back to 对话 and does not auto-steal", () => {
    render(<AgentPanel />);
    act(() => {
      useAppStore.setState({ aiTradeGroup: groupWith(SESSION) });
    });
    fireEvent.click(screen.getByRole("tab", { name: "对话" }));
    expect(screen.queryByText("运行中")).not.toBeInTheDocument();
    expect(screen.getByText("AI 交易助手")).toBeInTheDocument();
  });

  it("falls back to 对话 when the group clears", () => {
    render(<AgentPanel />);
    act(() => {
      useAppStore.setState({ aiTradeGroup: groupWith(SESSION) });
    });
    act(() => {
      useAppStore.setState({ aiTradeGroup: null });
    });
    expect(screen.queryByRole("tab", { name: /执行轨迹/ })).not.toBeInTheDocument();
    expect(screen.getByText("AI 交易助手")).toBeInTheDocument();
  });
});

// ---------------- 交易周期段（ai-trade-holding-horizon-modes §2.11 展示落点之二）----------------
// 与上一组同款理由：守的是**挂载**而不只是定义——组件写好了却没挂进渲染树，tsc 与逐函数单测都不会
// 替你发现（本仓库有过同类事故）。
function horizonWire(over: Record<string, unknown> = {}) {
  return {
    profileId: "intraday_swing", title: "日内波段", targetLabel: "4 – 12 小时",
    status: "holding", holdSeconds: 5400, barsInPosition: 2, targetHoldMinSeconds: 14400,
    targetHoldMaxSeconds: 43200, minHoldSeconds: 7200, minHoldRemainingSeconds: 1800,
    exitConfirmRounds: 2, exitConfirmStreak: 1, confirmWindowSeconds: 3600,
    confirmWindowRemainingSeconds: 1200, confirmWindowElapsed: false,
    turnoverHysteresisK: 1.5, moveBps: 8.2, costBps: 13, requiredMoveBps: 19.5,
    returnPct: 0.02, lossReleaseReturnPct: 0.075, released: false, releaseReason: null,
    releasedGates: [], blockedStreak: 0, expectedHoldBars: 12, openedWallEstimated: false,
    gateReverse: true, asOfWall: 1_700_000_000, ...over,
  } as NonNullable<AiTradeRoundTrace["horizonWire"]>;
}

describe("AiTradeAgentTrace · 交易周期段", () => {
  it("契约未装配（默认）⇒ 整段不渲染", () => {
    render(
      <AiTradeAgentTrace group={groupWith(SESSION, [round({})])} activeInstId="BTC-USDT-SWAP" />,
    );
    expect(screen.queryByText(/交易周期（持仓时长铁律）/)).not.toBeInTheDocument();
  });

  it("被否轮：三道闸读数与中文理由同时可见（只说「被拦了」用户无从判断该等还是该关）", () => {
    render(
      <AiTradeAgentTrace
        group={groupWith(SESSION, [round({
          horizonStatus: "veto",
          horizonReason: "horizon_min_hold(held=90m,min=120m)",
          horizonWire: horizonWire(),
        })])}
        activeInstId="BTC-USDT-SWAP"
      />,
    );
    expect(screen.getByText(/交易周期（持仓时长铁律）/)).toBeInTheDocument();
    const rows = screen.getByTestId("ai-horizon-rows").textContent ?? "";
    expect(rows).toContain("还差 30 分钟");
    expect(rows).toContain("离场确认 1/2");
    expect(rows).toContain("需 ≥19.5bps");
    expect(screen.getByText(/未满最短持仓窗口/)).toBeInTheDocument();
  });

  it("解除态：徽标点名原因，且文案讲清此刻是**放行**（本层 fail-open，用红色会让人以为被拦住）", () => {
    render(
      <AiTradeAgentTrace
        group={groupWith(SESSION, [round({
          horizonStatus: "released",
          horizonWire: horizonWire({ released: true, releaseReason: "stale_px",
                                     releasedGates: ["G1", "G2", "G3"] }),
        })])}
        activeInstId="BTC-USDT-SWAP"
      />,
    );
    expect(screen.getByText(/限制已解除（判定价不可信）/)).toBeInTheDocument();
  });

  it("空仓轮：如实说「本层不约束开仓」（新增边缘态必须验渲染门控没把它整段吞掉）", () => {
    render(
      <AiTradeAgentTrace
        group={groupWith(SESSION, [round({
          horizonStatus: "idle",
          horizonWire: horizonWire({ status: "flat", holdSeconds: 0 }),
        })])}
        activeInstId="BTC-USDT-SWAP"
      />,
    );
    // 徽标与正文两处都要说清楚（用户先看徽标、再看正文，缺一处都会让空仓态读起来像被拦住）。
    expect(screen.getAllByText(/不约束开仓/).length).toBeGreaterThanOrEqual(2);
  });
});
