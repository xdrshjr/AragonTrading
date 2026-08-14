import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { AiTradeResumableMember } from "@/lib/aiTradeTypes";
import AiTradeEndDialog from "./AiTradeEndDialog";

/**
 * G-31（[P1-12]）：「结束」是**不可逆**动作，带仓时还会造出一个「本地已归档、交易所上仍在」的
 * 孤儿仓。本文件钉死三条：带仓时**没有默认值**、选「保留持仓」必须勾选才可提交、无仓时不必确认。
 *
 * 勾选态必须**真的门控提交按钮** —— 本仓库有过「二次确认只有写点没有读点」的成文缺陷（RV-1）。
 */
const HOLDING: AiTradeResumableMember[] = [
  {
    instId: "BTC-USDT-SWAP",
    position: { side: "long", size: 0.6 },
    backstopAtPause: { covered: true, stopPx: 58172.8 },
  },
];

function renderDialog(
  members: AiTradeResumableMember[],
  onConfirm = vi.fn(),
  canFlatten = true,
) {
  render(
    <AiTradeEndDialog
      open
      members={members}
      canFlatten={canFlatten}
      submitting={false}
      onClose={() => {}}
      onConfirm={onConfirm}
    />,
  );
  return onConfirm;
}

afterEach(() => cleanup());

describe("AiTradeEndDialog", () => {
  it("带仓时不给默认值：两个单选都未选中，提交按钮 disabled", () => {
    renderDialog(HOLDING);
    const radios = screen.getAllByRole("radio");
    expect(radios.every((r) => !(r as HTMLInputElement).checked)).toBe(true);
    expect(screen.getByRole("button", { name: "结束并归档" })).toBeDisabled();
  });

  it("选「平掉这些持仓并结束」即可提交（不需要额外勾选）", () => {
    const onConfirm = renderDialog(HOLDING);
    fireEvent.click(screen.getAllByRole("radio")[0]);
    const submit = screen.getByRole("button", { name: "结束并归档" });
    expect(submit).not.toBeDisabled();
    fireEvent.click(submit);
    expect(onConfirm).toHaveBeenCalledWith({
      flatten: true,
      acknowledgeOrphanPosition: false,
    });
  });

  it("选「保留持仓」后必须勾选确认，勾选态真的门控提交（守 RV-1）", () => {
    const onConfirm = renderDialog(HOLDING);
    fireEvent.click(screen.getAllByRole("radio")[1]);
    const submit = screen.getByRole("button", { name: "结束并归档" });
    expect(submit).toBeDisabled();

    fireEvent.click(screen.getByLabelText("我知道这些持仓将不再由任何自动化管理"));
    expect(submit).not.toBeDisabled();
    fireEvent.click(submit);
    expect(onConfirm).toHaveBeenCalledWith({
      flatten: false,
      acknowledgeOrphanPosition: true,
    });
  });

  it("无仓 ⇒ 一次普通确认即可，flatten 恒 false", () => {
    const onConfirm = renderDialog([{ instId: "BTC-USDT-SWAP", position: null }]);
    expect(screen.queryAllByRole("radio")).toHaveLength(0);
    const submit = screen.getByRole("button", { name: "结束并归档" });
    expect(submit).not.toBeDisabled();
    fireEvent.click(submit);
    expect(onConfirm).toHaveBeenCalledWith({
      flatten: false,
      acknowledgeOrphanPosition: false,
    });
  });

  it("`positionUnknown` 一律按带仓处理（保守极性，与后端 S3 fail-closed 同源）", () => {
    renderDialog([{ instId: "BTC-USDT-SWAP", position: null, positionUnknown: true }]);
    expect(screen.getAllByRole("radio")).toHaveLength(2);
    expect(screen.getByRole("button", { name: "结束并归档" })).toBeDisabled();
    expect(screen.getByText("持仓未能读取")).toBeInTheDocument();
  });

  it("逐成员列出「仍将留在交易所上的持仓 + 它有没有交易所侧止损」", () => {
    renderDialog(HOLDING);
    expect(screen.getByText("BTC-USDT-SWAP")).toBeInTheDocument();
    expect(screen.getByText("关机前交易所侧止损已覆盖")).toBeInTheDocument();
  });

  // RV-1：冷续跑卡上没有会话对象，服务端也就没有任何东西能代用户下平仓单。
  it("canFlatten=false（冷续跑卡）⇒ 不提供「平仓」这一支，且必须勾选后才可提交", () => {
    const onConfirm = renderDialog(HOLDING, vi.fn(), false);
    expect(screen.queryAllByRole("radio")).toHaveLength(0);
    expect(screen.getByTestId("ai-trade-end-cold-hint")).toBeInTheDocument();

    const submit = screen.getByRole("button", { name: "结束并归档" });
    expect(submit).toBeDisabled();
    fireEvent.click(screen.getByLabelText("我知道这些持仓将不再由任何自动化管理"));
    expect(submit).not.toBeDisabled();
    fireEvent.click(submit);
    // 显式发 `false`：发 `null` 会被服务端的「带仓必须显式二选一」判成缺参数而 400，
    // 而用户面前根本没有那个选项可选。
    expect(onConfirm).toHaveBeenCalledWith({
      flatten: false,
      acknowledgeOrphanPosition: true,
    });
  });
});
