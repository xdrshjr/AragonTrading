import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import IntelBlockSourceDialog from "./IntelBlockSourceDialog";

const INST = "BTC-USDT-SWAP";
const DOMAIN = "coinmarketwatch.example";

let affected: number;
let shouldFail: boolean;
const previewMock = vi.fn(async () => {
  if (shouldFail) throw new Error("boom");
  return { affected };
});

vi.mock("@/lib/intelApi", () => ({
  intelApi: { previewFeedback: (...a: unknown[]) => previewMock(...(a as [])) },
}));

beforeEach(() => {
  affected = 3;
  shouldFail = false;
  previewMock.mockClear();
});
afterEach(cleanup);

function renderDialog(onConfirm = vi.fn()) {
  render(<IntelBlockSourceDialog open domain={DOMAIN} instId={INST}
    onCancel={vi.fn()} onConfirm={onConfirm} />);
  return onConfirm;
}

/** 同一棵树里开→关→再开，用来验证作用域**不跨调用留存**。 */
function renderReopenable(onConfirm = vi.fn()) {
  const view = render(<IntelBlockSourceDialog open domain={DOMAIN} instId={INST}
    onCancel={vi.fn()} onConfirm={onConfirm} />);
  const show = (open: boolean, domain = DOMAIN) =>
    view.rerender(<IntelBlockSourceDialog open={open} domain={domain} instId={INST}
      onCancel={vi.fn()} onConfirm={onConfirm} />);
  return { onConfirm, show };
}

describe("IntelBlockSourceDialog", () => {
  it("显示受影响条数与恢复路径（一个说不清后果的破坏性按钮不该出现在这个产品里）", async () => {
    renderDialog();
    expect(await screen.findByText(/已入库的 3 条情报/)).toBeInTheDocument();
    expect(screen.getByText(/可在「已归档」中恢复/)).toBeInTheDocument();
  });

  it("取数在飞时说「正在计算」，而不是先写一个 0 再跳变", () => {
    renderDialog();
    expect(screen.getByText(/正在计算受影响条数/)).toBeInTheDocument();
  });

  it("预览失败 ⇒ 改口说「无法预估」，但**不挡确认**", async () => {
    shouldFail = true;
    const onConfirm = renderDialog();
    expect(await screen.findByText(/暂时无法预估/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "不再采纳此源" }));
    expect(onConfirm).toHaveBeenCalledTimes(1);
  });

  it("不勾选「对所有标的生效」⇒ 回调带 instId", async () => {
    const onConfirm = renderDialog();
    await screen.findByText(/已入库的 3 条情报/);
    fireEvent.click(screen.getByRole("button", { name: "不再采纳此源" }));

    expect(onConfirm).toHaveBeenCalledWith(INST);
  });

  it("勾选之后 ⇒ 回调不带 instId（全局），且重新按全局口径算一次条数", async () => {
    affected = 9;
    const onConfirm = renderDialog();
    fireEvent.click(screen.getByRole("checkbox"));

    await waitFor(() => expect(previewMock).toHaveBeenLastCalledWith({
      key: DOMAIN, action: "block", instId: "" }));
    fireEvent.click(screen.getByRole("button", { name: "不再采纳此源" }));
    expect(onConfirm).toHaveBeenCalledWith("");
  });

  it("默认极性是**不勾选**：一次点击不该悄悄改掉别的标的的采集面", () => {
    renderDialog();
    expect(screen.getByRole("checkbox")).not.toBeChecked();
    expect(screen.getByText(new RegExp(`不勾选则只对 ${INST} 生效`))).toBeInTheDocument();
  });

  it("关掉之后作用域回到默认：上一次勾过的「全局」不跟着弹窗活到下一个域名", async () => {
    const { onConfirm, show } = renderReopenable();
    fireEvent.click(screen.getByRole("checkbox"));
    await waitFor(() => expect(screen.getByRole("checkbox")).toBeChecked());

    show(false);                       // 用户取消
    show(true, "another-source.example");   // 换一个域名再打开

    expect(screen.getByRole("checkbox")).not.toBeChecked();
    fireEvent.click(screen.getByRole("button", { name: "不再采纳此源" }));
    expect(onConfirm).toHaveBeenCalledWith(INST);
  });

  it("初始焦点在「取消」上（不可逆动作的默认答案就该是「否」）", async () => {
    renderDialog();
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "取消" })).toHaveFocus());
  });
});
