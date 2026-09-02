import { act, fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { AiTradeGroupMemberState, AiTradeOrphan } from "@/lib/aiTradeTypes";

import AiTradeOrphanBanner from "./AiTradeOrphanBanner";

function member(instId: string, orphan: AiTradeOrphan | null): AiTradeGroupMemberState {
  return {
    instId,
    session: orphan ? ({ orphan } as AiTradeGroupMemberState["session"]) : null,
  } as AiTradeGroupMemberState;
}

const CONFIRMED: AiTradeOrphan = {
  state: "confirmed",
  qty: 2.47,
  avgPx: 100.89,
  uplUsd: -1.2,
  sinceTs: 1_788_282_202,
};

describe("AiTradeOrphanBanner", () => {
  it("renders nothing when the key is absent (gate off / old backend)", () => {
    const { container } = render(
      <AiTradeOrphanBanner members={[member("BTC-USDT-SWAP", null)]} />,
    );
    expect(container.firstChild).toBeNull();
  });

  it("stays silent while the judgement is still pending", () => {
    // `confirm_rounds` 存在的全部理由就是压掉正常成交时序上的闪动；把 `pending` 画出来会把
    // 那个闪动原样搬到界面上。
    const { container } = render(
      <AiTradeOrphanBanner
        members={[member("BTC-USDT-SWAP", { ...CONFIRMED, state: "pending" })]}
      />,
    );
    expect(container.firstChild).toBeNull();
  });

  it("shows the readings for a confirmed orphan without markdown artefacts", () => {
    render(<AiTradeOrphanBanner members={[member("SOL-USDT-SWAP", CONFIRMED)]} />);
    const row = screen.getByTestId("ai-orphan-row-SOL-USDT-SWAP");
    expect(row.textContent).toContain("SOL-USDT-SWAP");
    expect(row.textContent).toContain("2.47 张");
    expect(row.textContent).toContain("100.89");
    // 后端文案在 JSX 里是纯文本节点——`**粗体**` 会原样显示成星号（本仓库第 5 次登记）。
    expect(row.textContent).not.toContain("**");
  });

  it("never reports a healthy member as having an orphan", () => {
    // 逐标的呈现，绝不整组与门：三个标的里只有一个有孤儿仓时，把另外两个也说成有是另一种谎话。
    render(
      <AiTradeOrphanBanner
        members={[member("BTC-USDT-SWAP", null), member("SOL-USDT-SWAP", CONFIRMED)]}
      />,
    );
    expect(screen.queryByTestId("ai-orphan-row-BTC-USDT-SWAP")).toBeNull();
    expect(screen.getByTestId("ai-orphan-row-SOL-USDT-SWAP")).toBeTruthy();
  });

  it("requires a second confirmation before it will change the real position", async () => {
    // 这是本轮唯一一个会改写真实资金立场的按钮，而它接管的是一笔来路不明的持仓。
    const onAdopt = vi.fn().mockResolvedValue(undefined);
    render(<AiTradeOrphanBanner members={[member("SOL-USDT-SWAP", CONFIRMED)]} onAdopt={onAdopt} />);

    fireEvent.click(screen.getByTestId("ai-orphan-adopt-SOL-USDT-SWAP"));
    expect(onAdopt).not.toHaveBeenCalled();          // 第一次点击只展开确认，绝不下发

    // `act` 包住：接管是异步的，回执落地后组件会收起确认态——不等它就会在卸载后写 state。
    await act(async () => {
      fireEvent.click(screen.getByTestId("ai-orphan-confirm-SOL-USDT-SWAP"));
    });
    expect(onAdopt).toHaveBeenCalledWith("SOL-USDT-SWAP");
    expect(screen.queryByTestId("ai-orphan-confirm-SOL-USDT-SWAP")).toBeNull();
  });

  it("offers no button at all when the container did not wire a handler", () => {
    render(<AiTradeOrphanBanner members={[member("SOL-USDT-SWAP", CONFIRMED)]} />);
    expect(screen.queryByTestId("ai-orphan-adopt-SOL-USDT-SWAP")).toBeNull();
  });
});
