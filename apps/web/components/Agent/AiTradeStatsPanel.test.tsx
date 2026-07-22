import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import AutoTradeRoundTripRow from "@/components/Chart/AutoTradeRoundTripRow";
import type { AiTradeDecision, AiTradeGroupState, AiTradeSessionRow } from "@/lib/aiTradeTypes";
import { aiTradeHistoryApi } from "@/lib/aiTradeHistoryApi";
import type { AiTradeHistoryDetailOut, AiTradeHistoryListOut } from "@/lib/aiTradeHistoryTypes";
import { api } from "@/lib/api";
import type { RoundTrip } from "@/lib/autoTradePnl";
import { useAppStore } from "@/lib/store";
import type { PaperPositionEvent, Trade } from "@/lib/types";
import AiTradeStatsPanel from "./AiTradeStatsPanel";

vi.mock("@/lib/aiTradeHistoryApi", () => ({
  aiTradeHistoryApi: { list: vi.fn(), detail: vi.fn() },
}));

// 行内 K 线：纯图经 next/dynamic(ssr:false) 引入 → stub 掉（规避 lightweight-charts 在 jsdom 的异步加载）；
// 数据层 api.candlesWithMeta 部分 mock（保留其余 api 真实，避免 store 等依赖断裂）。
vi.mock("next/dynamic", () => ({
  default: () => () => <div data-testid="trade-candle-chart" />,
}));
vi.mock("@/lib/api", async (importActual) => {
  const actual = await importActual<typeof import("@/lib/api")>();
  return { ...actual, api: { ...actual.api, candlesWithMeta: vi.fn() } };
});

const listMock = vi.mocked(aiTradeHistoryApi.list);
const detailMock = vi.mocked(aiTradeHistoryApi.detail);
const candlesMock = vi.mocked(api.candlesWithMeta);

const NOW_SEC = 1_790_000_000;
const DAY = 86_400;
const EMPTY_LIST: AiTradeHistoryListOut = { items: [], total: 0, limit: 20, offset: 0 };

function trade(o: Partial<Trade> & Pick<Trade, "ts" | "side">): Trade {
  return {
    id: o.id, runId: 1, ts: o.ts, side: o.side, price: o.price ?? 100, qty: 1, fee: 0,
    pnl: o.pnl ?? 0, pnlPct: o.pnlPct, positionAfter: 0, reason: "", explain: null,
  };
}

// 单成员组：15 天前亏 4、10 分钟前盈 12；当前持仓浮盈由 position.unrealizedPnl 提供（可覆盖）。
function makeGroup(
  position: Partial<PaperPositionEvent> | null = { side: "long", unrealizedPnl: 7 },
): AiTradeGroupState {
  const runtimePosition: PaperPositionEvent | null = position
    ? {
        side: "long", size: 1, avgEntry: 100, markPrice: 100,
        ...position,
        unrealizedPnl: position.unrealizedPnl as number,
      }
    : null;
  return {
    groupId: "g1", bar: "1H", strategyIds: [1], strategyNames: ["动量"],
    totalBudgetUsd: 100, leverage: 3, contextRefreshRounds: 10, state: "running",
    startedTs: NOW_SEC - 30 * DAY,
    members: {
      "BTC-USDT-SWAP": {
        instId: "BTC-USDT-SWAP", sessionId: "s1", allocationPct: 100, session: null,
        position: runtimePosition, decisions: [], traces: [], summary: null,
        trades: [
          trade({ id: 1, ts: NOW_SEC - 15 * DAY - 100, side: "long", price: 100 }),
          trade({ id: 2, ts: NOW_SEC - 15 * DAY, side: "close", price: 96, pnl: -4, pnlPct: -0.12 }),
          trade({ id: 3, ts: NOW_SEC - 700, side: "long", price: 100 }),
          trade({ id: 4, ts: NOW_SEC - 600, side: "close", price: 110, pnl: 12, pnlPct: 0.36 }),
        ],
      },
    },
  };
}

// hold-inertia-observability O5 用例夹具：最小完整决策条目 / 会话行（wakeSkips、holdStreak 可覆盖）。
function aiDecision(overrides: Partial<AiTradeDecision> = {}): AiTradeDecision {
  return { ts: NOW_SEC, round: 1, action: "hold", reason: "r", signalBoard: [], ...overrides };
}

function sessionRow(overrides: Partial<AiTradeSessionRow> = {}): AiTradeSessionRow {
  return {
    sessionId: "s1", runId: 1, state: "running", instId: "BTC-USDT-SWAP", bar: "1H",
    strategyIds: [1], strategyNames: ["动量"], budgetUsd: 100, leverage: 3,
    contextRefreshRounds: 10, roundsCompleted: 3, roundsSinceSummary: 1,
    positionSide: "flat", sessionPnlUsd: 0, startedTs: NOW_SEC - DAY,
    ...overrides,
  };
}

const HISTORY_LIST: AiTradeHistoryListOut = {
  items: [{
    groupId: "g1", strategyName: "动量", bar: "1H", instIds: ["BTC-USDT-SWAP"],
    startedTs: NOW_SEC - DAY, endedTs: NOW_SEC, status: "stopped", totalBudgetUsd: 100,
    realizedPnlUsd: 8, returnPct: 0.08, tradeCount: 2, winRate: 0.5, bestUsd: 12, worstUsd: -4,
  }],
  total: 1, limit: 20, offset: 0,
};

function renderPanel() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <AiTradeStatsPanel open onClose={() => {}} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  localStorage.clear();
  vi.spyOn(Date, "now").mockReturnValue(NOW_SEC * 1000);
  useAppStore.setState({ aiTradeGroup: null });
  listMock.mockReset().mockResolvedValue(EMPTY_LIST);
  detailMock.mockReset();
  candlesMock.mockReset().mockResolvedValue({ source: "live", candles: [] });
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("AiTradeStatsPanel", () => {
  it("shows empty states on both tabs when nothing is running and no history exists", async () => {
    renderPanel();
    expect(screen.getByText(/尚未启动 AI 自动交易/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "历史" }));
    expect(await screen.findByText(/暂无历史 AI 自动交易记录/)).toBeInTheDocument();
  });

  it("aggregates the current group and decouples floating pnl from the time window", () => {
    useAppStore.setState({ aiTradeGroup: makeGroup() });
    renderPanel();
    const dialog = screen.getByRole("dialog");
    // 全部窗口 KPI total = -4 + 12 = 8（唯一，仅 KPI 出 +8.00）
    expect(within(dialog).getByText("+8.00")).toBeInTheDocument();
    expect(within(dialog).getByText("+7.00 USDT")).toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole("button", { name: "近7日" }));
    // 切窗口后 KPI 重算（仅近 10 分钟那笔在 7 日内 → total=12）：+8.00 消失；浮盈条不随窗口变
    expect(within(dialog).queryByText("+8.00")).toBeNull();
    expect(within(dialog).getByText("+7.00 USDT")).toBeInTheDocument();
  });

  it("omits the floating pnl bar when no member exposes a numeric unrealizedPnl (P1-4)", () => {
    // position 无 unrealizedPnl 数值字段 → 不可信 → 整条略去（不显示占位 0）
    useAppStore.setState({ aiTradeGroup: makeGroup({ side: "long", markPrice: 107 }) });
    renderPanel();
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).queryByText(/当前持仓浮盈/)).toBeNull();
  });

  it("drills from a history card into detail and back", async () => {
    const entry: AiTradeHistoryListOut = {
      items: [{
        groupId: "g1", strategyName: "动量", bar: "1H", instIds: ["BTC-USDT-SWAP"],
        startedTs: NOW_SEC - DAY, endedTs: NOW_SEC, status: "stopped", totalBudgetUsd: 100,
        realizedPnlUsd: 8, returnPct: 0.08, tradeCount: 2, winRate: 0.5, bestUsd: 12, worstUsd: -4,
      }],
      total: 1, limit: 20, offset: 0,
    };
    listMock.mockResolvedValue(entry);
    detailMock.mockResolvedValue(makeDetail({}));
    renderPanel();
    fireEvent.click(screen.getByRole("button", { name: "历史" }));
    const card = await screen.findByRole("button", { name: /BTC-USDT-SWAP/ });
    fireEvent.click(card);
    expect(await screen.findByText(/已实现总收益/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "返回历史列表" }));
    expect(await screen.findByRole("button", { name: /BTC-USDT-SWAP/ })).toBeInTheDocument();
  });

  it("degrades detail without an equity chart when equityDigest is missing", async () => {
    listMock.mockResolvedValue({
      items: [{
        groupId: "g1", strategyName: "动量", bar: "1H", instIds: ["BTC-USDT-SWAP"],
        startedTs: NOW_SEC - DAY, endedTs: NOW_SEC, status: "stopped", totalBudgetUsd: 100,
        realizedPnlUsd: 8, returnPct: 0.08, tradeCount: 2, winRate: 0.5, bestUsd: 12, worstUsd: -4,
      }],
      total: 1, limit: 20, offset: 0,
    });
    detailMock.mockResolvedValue(makeDetail({})); // members 无 equityDigest
    renderPanel();
    fireEvent.click(screen.getByRole("button", { name: "历史" }));
    fireEvent.click(await screen.findByRole("button", { name: /BTC-USDT-SWAP/ }));
    expect(await screen.findByText(/本次会话未落权益快照/)).toBeInTheDocument();
  });

  it("shows a loading skeleton and then an error alert on history failure", async () => {
    listMock.mockRejectedValue(new Error("boom"));
    renderPanel();
    fireEvent.click(screen.getByRole("button", { name: "历史" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/加载历史失败/);
  });

  // ---------- hold-inertia-observability §六-7：O5 live 运行观测条 / O4 历史反思复核行 ----------
  it("renders the live run-observability strip from decisions and session", () => {
    const group = makeGroup();
    const member = group.members["BTC-USDT-SWAP"];
    member.decisions = [
      aiDecision({ round: 1, reflectionVerdict: "confirm", degradeReason: null }),
      aiDecision({ round: 2, reflectionVerdict: "confirm", degradeReason: null }),
      aiDecision({
        round: 3, reflectionVerdict: "abort", degradeReason: "reflection_abort", holdStreak: 12,
      }),
    ];
    member.session = sessionRow({ wakeSkips: 5 });
    useAppStore.setState({ aiTradeGroup: group });
    renderPanel();
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByText(/运行观测/)).toBeInTheDocument();
    expect(within(dialog).getByText(/反思 3（放行 2 · 否决 1 · 故障 0）/)).toBeInTheDocument();
    expect(within(dialog).getByText(/唤醒抑制 5 · 连续观望 12 轮/)).toBeInTheDocument();
  });

  it("degrades the live observability strip to em dashes on missing fields", () => {
    // decisions=[]、session=null（老后端 / WS 未连上）→ 计数 0、唤醒抑制与连续观望呈现「—」。
    useAppStore.setState({ aiTradeGroup: makeGroup() });
    renderPanel();
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByText(/反思 0（放行 0 · 否决 0 · 故障 0）/)).toBeInTheDocument();
    expect(within(dialog).getByText(/唤醒抑制 — · 连续观望 —/)).toBeInTheDocument();
  });

  it("shows the aggregated reflection observability row for new-style aiStats", async () => {
    listMock.mockResolvedValue(HISTORY_LIST);
    detailMock.mockResolvedValue(makeDetail({
      aiStats: {
        decisionRounds: 40, actionCounts: { hold: 30 }, holdRate: 0.75, avgConfidence: 0.6,
        degradedRounds: 2, reflectionRuns: 4, reflectionConfirms: 2, reflectionAborts: 1,
        reflectionFailClosed: 1, wakeSkips: 5, maxHoldStreak: 12,
      },
    }));
    renderPanel();
    fireEvent.click(screen.getByRole("button", { name: "历史" }));
    fireEvent.click(await screen.findByRole("button", { name: /BTC-USDT-SWAP/ }));
    expect(await screen.findByText("反思轮数")).toBeInTheDocument();
    expect(screen.getByText("反思放行")).toBeInTheDocument();
    expect(screen.getByText("真实否决")).toBeInTheDocument();
    expect(screen.getByText("故障保守")).toBeInTheDocument();
    expect(screen.getByText("唤醒抑制")).toBeInTheDocument();
    expect(screen.getByText("最长连续观望")).toBeInTheDocument();
    expect(screen.getByText("12 轮")).toBeInTheDocument(); // maxHoldStreak 合计取最大
    expect(screen.getByText(/近 50 轮保留样本口径/)).toBeInTheDocument(); // 口径注脚
  });

  it("hides the observability row for legacy aiStats without new keys", async () => {
    listMock.mockResolvedValue(HISTORY_LIST);
    detailMock.mockResolvedValue(makeDetail({
      aiStats: {
        decisionRounds: 40, actionCounts: { hold: 30 }, holdRate: 0.75,
        avgConfidence: 0.6, degradedRounds: 2,
      },
    }));
    renderPanel();
    fireEvent.click(screen.getByRole("button", { name: "历史" }));
    fireEvent.click(await screen.findByRole("button", { name: /BTC-USDT-SWAP/ }));
    expect(await screen.findByText("决策轮数")).toBeInTheDocument(); // 旧五键卡照常渲染
    expect(screen.queryByText("反思轮数")).toBeNull(); // 老记录不显示一排误导性的 0
    expect(screen.queryByText("唤醒抑制")).toBeNull();
  });

  // ---------- ai-trade-stats-two-column-and-kline：60vw 双栏 / 预计年化 / 行内 K 线 ----------
  it("widens to a 60vw two-column layout with stats on the left and a trade-record column on the right", () => {
    useAppStore.setState({ aiTradeGroup: makeGroup() });
    renderPanel();
    const dialog = screen.getByRole("dialog");
    expect(dialog).toHaveClass("max-w-[60vw]");
    expect(within(dialog).getByText("总收益（USDT）")).toBeInTheDocument(); // 左栏 KPI
    expect(within(dialog).getByText(/预计年化收益/)).toBeInTheDocument(); // 左栏年化卡
    expect(within(dialog).getByText("交易记录")).toBeInTheDocument(); // 右栏标题
  });

  it("degrades the annualized projection to a sample-insufficient notice instead of a wild number (P1-2)", () => {
    // makeGroup 仅 2 笔完整交易（<3 门禁）→ 不出数字，显式降级。
    useAppStore.setState({ aiTradeGroup: makeGroup() });
    renderPanel();
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByText("样本不足以估算年化")).toBeInTheDocument();
    expect(within(dialog).queryByText(/按当前已实现收益线性外推一年/)).toBeNull();
  });

  it("projects an annualized number once the sample clears the gates (≥6h and ≥3 trades)", () => {
    const group = makeGroup();
    // 追加第三笔完整交易过 minTrades=3（elapsed 已 30 天，过 minElapsed）。
    group.members["BTC-USDT-SWAP"].trades.push(
      trade({ id: 5, ts: NOW_SEC - 500, side: "long", price: 100 }),
      trade({ id: 6, ts: NOW_SEC - 400, side: "close", price: 105, pnl: 5, pnlPct: 0.15 }),
    );
    useAppStore.setState({ aiTradeGroup: group });
    renderPanel();
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).queryByText("样本不足以估算年化")).toBeNull();
    expect(within(dialog).getByText(/按当前已实现收益线性外推一年/)).toBeInTheDocument();
  });

  it("expands a trade record into an inline K-line panel when a bar is supplied", async () => {
    candlesMock.mockResolvedValue({
      source: "live",
      candles: [
        { ts: NOW_SEC - 700, o: 100, h: 101, l: 99, c: 100, vol: 1 },
        { ts: NOW_SEC - 600, o: 100, h: 111, l: 99, c: 110, vol: 1 },
      ],
    });
    useAppStore.setState({ aiTradeGroup: makeGroup() });
    renderPanel();
    const dialog = screen.getByRole("dialog");
    // 往返行携 bar → 可展开（aria-expanded 出现于折叠态）；点最新一行（+12，窗口含 mock K 线）。
    const rows = within(dialog).getAllByRole("button", { expanded: false });
    fireEvent.click(rows[0]);
    expect(await screen.findByTestId("trade-candle-chart")).toBeInTheDocument();
    expect(candlesMock).toHaveBeenCalled();
  });
});

// P1-3 零回归门控：`AutoTradeRoundTripRow` 的行内 K 线**仅**由可选 `bar` 开启；不传 `bar` 的既有调用点
// （live-auto / paper 面板）行为逐字节不变——`expandable` 仍 = `Boolean(rt.exit.explain)`、绝不挂 K 线。
describe("AutoTradeRoundTripRow bar-gated inline K-line (zero regression)", () => {
  function roundTrip(): RoundTrip {
    return {
      instId: "BTC-USDT-SWAP",
      direction: "long",
      entry: trade({ id: 1, ts: NOW_SEC - 700, side: "long", price: 100 }),
      exit: trade({ id: 2, ts: NOW_SEC - 600, side: "close", price: 110, pnl: 12 }),
      pnlUsd: 12,
      pnlPct: 0.36,
      openedTs: NOW_SEC - 700,
      closedTs: NOW_SEC - 600,
      isWin: true,
    };
  }
  function renderRow(bar?: string) {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    return render(
      <QueryClientProvider client={qc}>
        <AutoTradeRoundTripRow rt={roundTrip()} open onToggle={() => {}} bar={bar} />
      </QueryClientProvider>,
    );
  }

  it("stays non-expandable and mounts no K-line without a bar (unchanged legacy behavior)", () => {
    renderRow(undefined); // 夹具 exit.explain=null 且无 bar
    expect(screen.getByRole("button")).not.toHaveAttribute("aria-expanded");
    expect(screen.queryByTestId("trade-candle-chart")).toBeNull();
  });

  it("mounts the inline K-line only once a bar is supplied", async () => {
    candlesMock.mockResolvedValue({
      source: "live",
      candles: [{ ts: NOW_SEC - 700, o: 100, h: 101, l: 99, c: 100, vol: 1 }],
    });
    renderRow("1H");
    expect(screen.getByRole("button")).toHaveAttribute("aria-expanded", "true");
    expect(await screen.findByTestId("trade-candle-chart")).toBeInTheDocument();
  });
});

function makeDetail(metrics: AiTradeHistoryDetailOut["members"][number]["metrics"]): AiTradeHistoryDetailOut {
  return {
    groupId: "g1", strategyName: "动量", bar: "1H",
    startedTs: NOW_SEC - DAY, endedTs: NOW_SEC, status: "stopped", totalBudgetUsd: 100,
    members: [{
      runId: 1, instId: "BTC-USDT-SWAP", leverage: 3, budgetUsd: 100, allocationPct: 100,
      strategyNames: ["动量"], status: "stopped", metrics,
      trades: [
        trade({ id: 1, ts: NOW_SEC - 700, side: "long", price: 100 }),
        trade({ id: 2, ts: NOW_SEC - 600, side: "close", price: 110, pnl: 12 }),
      ],
    }],
    summary: { realizedPnlUsd: 12, returnPct: 0.12, tradeCount: 1, winRate: 1, bestUsd: 12, worstUsd: 0 },
  };
}
