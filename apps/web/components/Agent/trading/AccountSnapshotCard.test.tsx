import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";
import type { AccountSnapshot } from "@/lib/agentTradingTypes";
import AccountSnapshotCard from "./AccountSnapshotCard";

afterEach(cleanup);
const snapshot: AccountSnapshot = {
  schemaVersion: 1, source: "okx", accountId: 1, accountLabel: "测试账户",
  environment: "demo", asOfTs: 1, coverage: { status: "complete" },
  warnings: [], data: [],
};
it("成功空仓与部分查询明确区分", () => {
  const view = render(<AccountSnapshotCard snapshot={snapshot}
    toolName="get_account_positions" />);
  expect(screen.getByText(/当前无持仓/)).toBeTruthy();
  view.rerender(<AccountSnapshotCard snapshot={{ ...snapshot,
    coverage: { status: "partial" }, data: [{ pos: "2", upl: null }],
    truncated: true, returnedCount: 1 }} />);
  expect(screen.queryByText(/当前无持仓/)).toBeNull();
  expect(screen.getByText(/仅取得部分数据/)).toBeTruthy();
  expect(screen.getByText("持仓（张）")).toBeTruthy();
  expect(screen.getByText("未知")).toBeTruthy();
  expect(screen.getByText(/结果已截断/)).toBeTruthy();
});
it("算法委托只读，明确环境及来源时间", () => {
  render(<AccountSnapshotCard snapshot={{ ...snapshot,
    data: [{ kind: "oco", algoId: "123" }] }} />);
  expect(screen.getByText("保护委托，只读")).toBeTruthy();
  expect(screen.getByText(/OKX 模拟盘/)).toBeTruthy();
  expect(screen.getByText(/上次查询.*来源：okx/)).toBeTruthy();
  expect(screen.queryByRole("button", { name: /撤/ })).toBeNull();
});

it("按每行真实币种展示现货成交和币本位保证金", () => {
  render(<AccountSnapshotCard snapshot={{ ...snapshot, data: [{
    instId: "ETH-BTC", fillSz: "2", fillPx: "0.04",
    fieldUnits: { fillSz: "ETH", fillPx: "BTC" },
  }, { instId: "BTC-USD-SWAP", margin: "0.01", upl: "0.002",
    fieldUnits: { margin: "BTC", upl: "BTC" },
  }] }} />);
  expect(screen.getByText("成交（ETH）")).toBeTruthy();
  expect(screen.getByText("成交价（BTC）")).toBeTruthy();
  expect(screen.getByText("保证金（BTC）")).toBeTruthy();
  expect(screen.queryByText("成交（张）")).toBeNull();
  expect(screen.queryByText(/USDT/)).toBeNull();
});
