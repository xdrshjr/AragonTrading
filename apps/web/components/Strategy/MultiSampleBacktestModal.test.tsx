import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import MultiSampleBacktestModal from "./MultiSampleBacktestModal";

const WATCHLIST = ["BTC-USDT-SWAP", "ETH-USDT-SWAP", "SOL-USDT-SWAP"];

afterEach(cleanup);

function renderModal(
  props: Partial<React.ComponentProps<typeof MultiSampleBacktestModal>> = {},
) {
  const onClose = vi.fn();
  const onConfirm = vi.fn();
  render(
    <MultiSampleBacktestModal
      open
      onClose={onClose}
      watchlist={WATCHLIST}
      currentInstId="BTC-USDT-SWAP"
      bar="1H"
      startTs={1_700_000_000}
      endTs={1_700_086_400}
      strategyName="Alpha Trend"
      onConfirm={onConfirm}
      {...props}
    />,
  );
  return { onClose, onConfirm };
}

function checkedBoxes() {
  return screen.getAllByRole("checkbox").filter((b) => (b as HTMLInputElement).checked);
}

describe("MultiSampleBacktestModal", () => {
  it("renders one row per watchlist instrument", () => {
    renderModal();
    expect(screen.getAllByRole("checkbox")).toHaveLength(WATCHLIST.length);
  });

  it("default-checks the current instrument when it is in the watchlist", () => {
    renderModal();
    expect(checkedBoxes()).toHaveLength(1);
    expect(screen.getByText("当前")).toBeInTheDocument();
  });

  it("does not pre-select anything when the current instrument is not in the watchlist", () => {
    renderModal({ currentInstId: "DOGE-USDT-SWAP" });
    expect(checkedBoxes()).toHaveLength(0);
  });

  it("select-all and clear change the selection count", () => {
    renderModal();
    fireEvent.click(screen.getByRole("button", { name: "全选" }));
    expect(checkedBoxes()).toHaveLength(WATCHLIST.length);

    fireEvent.click(screen.getByRole("button", { name: "清空" }));
    expect(checkedBoxes()).toHaveLength(0);
  });

  it("disables confirm when nothing is selected", () => {
    renderModal();
    fireEvent.click(screen.getByRole("button", { name: "清空" }));
    expect(screen.getByRole("button", { name: /确定/ })).toBeDisabled();
  });

  it("shows an empty state and no rows when the watchlist is empty", () => {
    renderModal({ watchlist: [] });
    expect(screen.queryAllByRole("checkbox")).toHaveLength(0);
    expect(screen.getByText("自选列表为空")).toBeInTheDocument();
  });

  it("confirms with the deduped selected instIds", () => {
    const { onConfirm } = renderModal();
    // 默认含 BTC；再勾选 ETH。
    fireEvent.click(screen.getByRole("checkbox", { name: /ETH/ }));
    fireEvent.click(screen.getByRole("button", { name: /确定/ }));
    expect(onConfirm).toHaveBeenCalledWith(["BTC-USDT-SWAP", "ETH-USDT-SWAP"]);
  });

  it("filters rows by the search query", () => {
    renderModal();
    fireEvent.change(screen.getByPlaceholderText("搜索标的"), { target: { value: "eth" } });
    expect(screen.getAllByRole("checkbox")).toHaveLength(1);
  });

  it("calls onClose on Escape and on overlay click", () => {
    const { onClose } = renderModal();
    fireEvent.keyDown(window, { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(1);

    const overlay = screen.getByRole("dialog").parentElement as HTMLElement;
    fireEvent.click(overlay);
    expect(onClose).toHaveBeenCalledTimes(2);
  });
});
