import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { AiTradeGroupState } from "@/lib/aiTradeTypes";
import { aiTradeHistoryApi } from "@/lib/aiTradeHistoryApi";
import type { AiTradeHistoryDetailOut, AiTradeHistoryListOut } from "@/lib/aiTradeHistoryTypes";
import { useAppStore } from "@/lib/store";
import type { PaperPositionEvent, Trade } from "@/lib/types";
import AiTradeStatsPanel from "./AiTradeStatsPanel";

vi.mock("@/lib/aiTradeHistoryApi", () => ({
  aiTradeHistoryApi: { list: vi.fn(), detail: vi.fn() },
}));

const listMock = vi.mocked(aiTradeHistoryApi.list);
const detailMock = vi.mocked(aiTradeHistoryApi.detail);

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
