import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "@/lib/api";
import { useAppStore } from "@/lib/store";
import type {
  Candle,
  CompareResult,
  ComboPreviewResult,
  Instrument,
  LiveMarketState,
  Metrics,
  Run,
  Strategy,
  Trade,
  TriggerExplain,
  WalkForwardResult,
} from "@/lib/types";
import ChartHeaderBar from "./ChartHeaderBar";
import ChartToolbar from "./ChartToolbar";
import MarketDepthPanel from "./MarketDepthPanel";
import AutoTradePanel, { type AutoTradeMemberView } from "./AutoTradePanel";
import WatchOtherPanel from "./WatchOtherPanel";
import ComboResult from "@/components/Strategy/ComboResult";
import CompareView from "@/components/Strategy/CompareView";
import MetricsCards from "@/components/Strategy/MetricsCards";
import PositionPanel from "@/components/Strategy/PositionPanel";
import RunHistory from "@/components/Strategy/RunHistory";
import TradeExplainDetail from "@/components/Strategy/TradeExplainDetail";
import TradesTable from "@/components/Strategy/TradesTable";
import WalkForwardResults from "@/components/Strategy/WalkForwardResults";

/**
 * 交易工作台「文字零折行」排版契约的组件级冻结（workstation-text-nowrap-contract §9.1）。
 *
 * 这套修法**全部由类名承载**，而类名是最容易在重构里被顺手删掉的东西；jsdom 没有布局引擎，
 * 普通渲染用例察觉不到像素回归。所以这里冻结的是**契约本身**：每一行是不是声明了不折行、
 * 压力吸收点是不是唯一、该换行的散文有没有被继承来的 nowrap 误伤。
 *
 * 四类行的定义见 `components/ui/noWrapContract.source.test.ts` 的文件头（唯一权威副本）。
 */

vi.mock("@/lib/api", () => ({
  api: { strategies: vi.fn(), compare: vi.fn(), runsList: vi.fn() },
}));

afterEach(cleanup);

const METRICS: Metrics = {
  totalReturn: 0.1234,
  winRate: 0.5,
  profitFactor: 1.4,
  maxDrawdown: -0.0812,
  sharpe: 1.2,
  tradeCount: 18,
  avgPnl: 3.2,
};

function inst(instId: string): Instrument {
  return {
    instId,
    instType: "SWAP",
    base: instId.split("-")[0],
    quote: "USDT",
    last: 100,
    open24h: 99,
    vol24h: 1,
    volCcy24h: 1,
    changePct: 0.01,
  };
}

function member(instId: string): AutoTradeMemberView {
  return { inst: inst(instId), allocationPct: 25, runtime: null };
}

function renderAutoTradePanel(over: Partial<Parameters<typeof AutoTradePanel>[0]> = {}) {
  const onOpenRecords = vi.fn();
  const view = render(
    <AutoTradePanel
      members={[member("BTC-USDT-SWAP")]}
      active="BTC-USDT-SWAP"
      totalPct={25}
      allocationMode="even"
      running={false}
      submitting={false}
      gateReason={null}
      aggregatePnl={null}
      dropError={null}
      dragOver={false}
      onOpenRecords={onOpenRecords}
      onToggle={vi.fn()}
      onAllocationModeChange={vi.fn()}
      onEvenSplit={vi.fn()}
      onSelect={vi.fn()}
      onAllocationChange={vi.fn()}
      onRemove={vi.fn()}
      onStopMember={vi.fn()}
      onDragStartMember={vi.fn()}
      onDragOver={vi.fn()}
      onDragLeave={vi.fn()}
      onDrop={vi.fn()}
      {...over}
    />,
  );
  return { ...view, onOpenRecords };
}

function renderWatchOtherPanel() {
  return render(
    <WatchOtherPanel
      rows={[]}
      active=""
      locked={false}
      dragOver={false}
      onSelect={vi.fn()}
      onToggleStar={vi.fn()}
      onAddAuto={vi.fn()}
      onDragStart={vi.fn()}
      onDragOver={vi.fn()}
      onDragLeave={vi.fn()}
      onDrop={vi.fn()}
    />,
  );
}

const CANDLE: Candle = { ts: 1_700_000_000, o: 104235, h: 104900, l: 103800, c: 104512, vol: 12.5 };

function renderChartHeaderBar() {
  return render(
    <ChartHeaderBar view="price" onView={vi.fn()} candle={CANDLE} prevClose={104000} />,
  );
}

function renderChartToolbar() {
  return render(
    <ChartToolbar
      okxUp
      chartView="live-follow"
      rangeAvailable={false}
      onChartViewChange={vi.fn()}
      onJumpDate={vi.fn()}
    />,
  );
}

function market(over: Partial<LiveMarketState> = {}): LiveMarketState {
  return {
    instId: "BTC-USDT-SWAP",
    lastPrice: 104512.3,
    ticker: null,
    book: {
      asks: [{ price: 104520.1, qty: 3.5 }],
      bids: [{ price: 104510.2, qty: 2.5 }],
    } as LiveMarketState["book"],
    trades: [],
    connected: true,
    error: null,
    updatedTs: 1_700_000_000,
    ...over,
  };
}

const LONG_TRADE: Trade = {
  ts: 1_700_000_000,
  side: "long",
  price: 104235.5,
  qty: 0.5,
  fee: 0.1,
  pnl: 0,
  positionAfter: 0.5,
  reason: "entry",
};

const EXPLAIN: TriggerExplain = {
  schema: 1,
  kind: "factor_score",
  side: "long",
  headline: "复合得分越过进多阈值，且三项确认指标同向",
  summary: "这一句是散文：它必须继续正常换行，绝不能被继承来的 nowrap 截成一行加省略号。",
  metrics: [
    { label: "入场依据", value: "0.6421", hint: "复合得分" },
    { label: "进多阈值", value: "0.3000" },
    { label: "波动率", value: "1.42%" },
  ],
};

describe("workstation 排版契约 — 左侧标的展示面板（轴 B）", () => {
  it("T1 自动交易头行声明了不折行契约且行高被钉死", () => {
    renderAutoTradePanel();
    const row = screen.getByTestId("auto-trade-header-row");
    expect(row).toHaveClass("whitespace-nowrap", "min-h-[24px]");
    expect(row.getAttribute("data-nowrap")).toBe("row");
  });

  it("T2 停止态与运行态的头行高度类名完全一致，且开关两态都在", () => {
    const stopped = renderAutoTradePanel({ running: false });
    expect(screen.getByTestId("auto-trade-header-row")).toHaveClass("min-h-[24px]");
    expect(screen.getByRole("switch", { name: "自动交易分组开关" })).toBeTruthy();
    stopped.unmount();

    renderAutoTradePanel({ running: true, aggregatePnl: 1234.56 });
    expect(screen.getByTestId("auto-trade-header-row")).toHaveClass("min-h-[24px]");
    expect(screen.getByRole("switch", { name: "自动交易分组开关" })).toBeTruthy();
  });

  it("T3 收益记录按钮已移出第 1 行、落在汇总行，且点击行为逐字不变", () => {
    const { onOpenRecords } = renderAutoTradePanel();
    const header = screen.getByTestId("auto-trade-header-row");
    expect(within(header).queryByRole("button", { name: /收益记录/ })).toBeNull();

    const summary = screen.getByTestId("auto-trade-summary-row");
    const button = within(summary).getByRole("button", { name: /收益记录/ });
    fireEvent.click(button);
    expect(onOpenRecords).toHaveBeenCalledTimes(1);
  });

  it("T4 分配汇总行只有一处截断吸收点，且带完整 title", () => {
    renderAutoTradePanel({ allocationMode: "max_ratio", totalPct: 180 });
    const absorber = screen.getByTestId("auto-trade-allocation-summary");
    expect(absorber).toHaveClass("min-w-0", "truncate");
    expect(absorber.getAttribute("title")).toBeTruthy();

    const row = screen.getByTestId("auto-trade-summary-row");
    expect(row.querySelectorAll(".truncate")).toHaveLength(1);
  });

  it("T5 其他自选区标题是唯一吸收点、计数不被压缩", () => {
    const { container } = renderWatchOtherPanel();
    const header = container.querySelector("header")!;
    expect(header).toHaveClass("whitespace-nowrap");
    expect(header.getAttribute("data-nowrap")).toBe("row");

    const [title, count] = Array.from(header.children) as HTMLElement[];
    expect(title).toHaveClass("truncate");
    expect(title.getAttribute("title")).toBe("其他自选");
    expect(count).toHaveClass("shrink-0");
  });
});

describe("workstation 排版契约 — 中间 K 线面板（轴 C）", () => {
  it("T6 K 线头部行不再写死 208px", () => {
    renderChartHeaderBar();
    const bar = screen.getByTestId("chart-header-bar");
    expect(bar).toHaveClass("whitespace-nowrap");

    const first = bar.firstElementChild as HTMLElement;
    expect(first.className).not.toContain("w-[208px]");
    expect(first).toHaveClass("shrink-0");
  });

  it("T6b OHLC 组能被行宽收住，窄档整组回流而不是横向溢出", () => {
    renderChartHeaderBar();
    const bar = screen.getByTestId("chart-header-bar");
    // 第 2 个直接子元素是 OHLC 组（第 1 个是视图页签）。只写 shrink-0 会让它按 max-content
    // 定死、自身 flex-wrap 永不触发（终审实测中栏 240px 时溢出 402px），故必须与 max-w-full 同现。
    const group = bar.children[1] as HTMLElement;
    expect(group).toHaveClass("flex-wrap", "shrink-0", "max-w-full");
  });

  it("T7 K 线工具栏是可回流栏且每个直接子元素都不可压缩", () => {
    renderChartToolbar();
    const toolbar = screen.getByTestId("chart-toolbar");
    expect(toolbar).toHaveClass("flex-wrap", "whitespace-nowrap");

    const children = Array.from(toolbar.children) as HTMLElement[];
    expect(children.length).toBeGreaterThan(0);
    for (const child of children) {
      expect(child.classList.contains("shrink-0")).toBe(true);
    }
  });
});

describe("workstation 排版契约 — 盘口成交列与持仓卡（轴 D）", () => {
  it("T8 成交列面板一次继承覆盖全部行情文本，错误行仍换行", () => {
    render(<MarketDepthPanel market={market({ error: "行情通道断开：等待重连" })} activeCandle={null} />);
    const panel = screen.getByTestId("market-depth-panel");
    expect(panel).toHaveClass("whitespace-nowrap");
    expect(panel.getAttribute("data-nowrap")).toBe("scope");

    expect(screen.getByText("行情通道断开：等待重连")).toHaveClass("whitespace-normal");
  });

  it("T9 持仓卡片按容器宽自适应，且不含视口断点", () => {
    render(<PositionPanel trades={[LONG_TRADE]} markPrice={104512} instId="BTC-USDT-SWAP" />);
    const grid = screen.getByTestId("position-panel-grid");
    expect(grid.className).toMatch(/grid-cols-\[repeat\(auto-fit/);
    expect(grid.className).not.toContain("sm:grid-cols-");
    expect(grid.className).not.toContain("lg:grid-cols-");

    const cells = Array.from(grid.children) as HTMLElement[];
    expect(cells.length).toBeGreaterThan(0);
    for (const cell of cells) {
      expect(cell).toHaveClass("min-w-0");
      // label 一处 + 包裹 children 的那一层一处，恰好两处（评审 P1-4）。
      expect(cell.querySelectorAll(":scope > .truncate")).toHaveLength(2);
    }
  });

  it("T9b 成交触发原因的指标网格同样不含视口断点，且散文未被误伤", () => {
    render(<TradeExplainDetail explain={EXPLAIN} />);
    const grid = screen.getByTestId("trade-explain-metrics-grid");
    expect(grid.className).toMatch(/grid-cols-\[repeat\(auto-fit/);
    expect(grid.className).not.toContain("sm:grid-cols-");

    const headline = screen.getByText(EXPLAIN.headline);
    expect(headline.className).not.toContain("whitespace-nowrap");
  });

  it("T10 成交记录表的表头与数据行不折行，展开详情行仍换行", () => {
    const { container } = render(<TradesTable trades={[{ ...LONG_TRADE, explain: EXPLAIN }]} />);
    const table = container.querySelector("table")!;
    expect(table).toHaveClass("w-full");
    expect(table.className).toMatch(/min-w-\[\d+px\]/);
    expect(container.querySelector("thead")).toHaveClass("whitespace-nowrap");

    // 文案零变更：两个风险列表头仍按既有可访问名可定位。
    expect(screen.getByRole("columnheader", { name: "止损(SL)" })).toBeTruthy();
    expect(screen.getByRole("columnheader", { name: "止盈(TP)" })).toBeTruthy();

    fireEvent.click(screen.getAllByRole("button", { expanded: false })[0]);
    expect(container.querySelector("td[colspan]")).toHaveClass("whitespace-normal");
  });
});

describe("workstation 排版契约 — 右侧交易策略面板（轴 D）", () => {
  const COMBO: { metrics: Metrics; combo: ComboPreviewResult["combo"] } = {
    metrics: METRICS,
    combo: {
      annualizedReturn: 0.32,
      timeline: [],
      breakdown: [
        { regime: "trend_up", memberKey: "m1", memberName: "趋势跟随", bars: 120, contribution: 0.18 },
      ],
    },
  };

  const WALK_FORWARD: WalkForwardResult = {
    runId: 1,
    summary: {
      windowCount: 1,
      profitableWindows: 1,
      profitableRate: 1,
      medianReturn: 0.1,
      avgReturn: 0.1,
      worstReturn: 0.1,
      bestReturn: 0.1,
      avgMaxDrawdown: -0.05,
      stabilityScore: 0.8,
      uncoveredBars: 0,
      notes: [],
    },
    windows: [{ startTs: 1_700_000_000, endTs: 1_700_360_000, metrics: METRICS, tradeCount: 18 }],
  };

  const RUNS: Run[] = [
    {
      id: 1,
      strategyId: 1,
      instId: "BTC-USDT-SWAP",
      bar: "1H",
      mode: "backtest",
      status: "done",
      startTs: 1_700_000_000,
      endTs: 1_700_360_000,
      metrics: METRICS,
    },
  ];

  const STRATEGIES: Strategy[] = [
    {
      id: 1,
      templateKey: "a1",
      name: "策略一",
      description: "d1",
      kind: "atomic",
      params: {},
      paramsSchema: {},
      enabled: true,
      builtin: true,
    },
  ];

  const COMPARE_RANGE = {
    instId: "BTC-USDT-SWAP",
    bar: "1H" as const,
    backtestStartTs: 1_700_000_000,
    backtestEndTs: 1_700_360_000,
  };

  const COMPARE_RESULT: CompareResult = {
    instId: COMPARE_RANGE.instId,
    bar: COMPARE_RANGE.bar,
    items: [{ strategyId: 1, name: "策略一", runId: 101, metrics: METRICS, equity: [] }],
  };

  beforeEach(() => {
    vi.clearAllMocks();
    useAppStore.setState(COMPARE_RANGE);
    (api.strategies as ReturnType<typeof vi.fn>).mockResolvedValue(STRATEGIES);
    (api.runsList as ReturnType<typeof vi.fn>).mockResolvedValue(RUNS);
  });

  function renderWithQuery(node: React.ReactNode, seed?: (qc: QueryClient) => void) {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    seed?.(qc);
    return render(<QueryClientProvider client={qc}>{node}</QueryClientProvider>);
  }

  it("T16 右侧策略面板的四张表都有列宽地板与不折行表头", async () => {
    const combo = render(<ComboResult result={COMBO} />);
    expectTypeCTable(combo.container);
    combo.unmount();

    const wfr = render(<WalkForwardResults result={WALK_FORWARD} />);
    expectTypeCTable(wfr.container);
    wfr.unmount();

    const history = renderWithQuery(<RunHistory />);
    await screen.findByText("BTC-USDT-SWAP");
    expectTypeCTable(history.container);
    history.unmount();

    const compare = renderWithQuery(<CompareView />, (qc) =>
      qc.setQueryData(
        ["compare", COMPARE_RANGE.instId, COMPARE_RANGE.bar, COMPARE_RANGE.backtestStartTs, COMPARE_RANGE.backtestEndTs],
        COMPARE_RESULT,
      ),
    );
    await screen.findByText("多策略对比");
    expectTypeCTable(compare.container);
  });

  it("T17 ComboResult 的表被自有的横向滚动容器包住", () => {
    const { container } = render(<ComboResult result={COMBO} />);
    const table = container.querySelector("table")!;
    expect(table.parentElement).toHaveClass("overflow-x-auto");
  });

  it("T18 指标卡的标签与数值各自截断，不互相挤压", () => {
    const { container } = render(<MetricsCards metrics={{ ...METRICS, tradeCount: 1234567890 }} />);
    const grid = container.firstElementChild as HTMLElement;
    expect(grid).toHaveClass("whitespace-nowrap");
    expect(grid.getAttribute("data-nowrap")).toBe("scope");

    const cards = Array.from(grid.children) as HTMLElement[];
    expect(cards.length).toBeGreaterThan(0);
    for (const card of cards) {
      expect(card).toHaveClass("min-w-0");
      const [label, value] = Array.from(card.children) as HTMLElement[];
      expect(label).toHaveClass("truncate");
      expect(value).toHaveClass("truncate");
    }
  });
});

describe("workstation 排版契约 — 锚点不变式", () => {
  it("T19 渲染出的每个锚点取值只有 row 与 scope 两种，且都带 whitespace-nowrap", () => {
    const rendered = [
      renderAutoTradePanel().container,
      renderWatchOtherPanel().container,
      renderChartHeaderBar().container,
      renderChartToolbar().container,
      render(<MarketDepthPanel market={market()} activeCandle={null} />).container,
      render(<PositionPanel trades={[LONG_TRADE]} markPrice={104512} instId="BTC-USDT-SWAP" />)
        .container,
      render(<TradeExplainDetail explain={EXPLAIN} />).container,
      render(<MetricsCards metrics={METRICS} />).container,
    ];

    const anchors = rendered.flatMap((c) => Array.from(c.querySelectorAll("[data-nowrap]")));
    expect(anchors.length).toBeGreaterThan(0);
    for (const anchor of anchors) {
      expect(["row", "scope"]).toContain(anchor.getAttribute("data-nowrap"));
      expect(anchor.classList.contains("whitespace-nowrap")).toBe(true);
    }
  });
});

function expectTypeCTable(container: HTMLElement) {
  const table = container.querySelector("table");
  expect(table).toBeTruthy();
  expect(table!).toHaveClass("w-full");
  expect(table!.className).toMatch(/min-w-\[\d+px\]/);
  expect(container.querySelector("thead")).toHaveClass("whitespace-nowrap");
}
