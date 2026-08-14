import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { AiTradeResumePreview } from "@/lib/aiTradeTypes";
import AiTradeResumeDialog from "./AiTradeResumeDialog";

/**
 * G-24：`verdict === "needs_confirm"` 且未勾选时提交按钮必须 `disabled`（守 RV-1 那类「二次确认
 * 只有写点没有读点」的成文缺陷）；`blocked` 时无论如何都不能提交。
 *
 * 另一条同样要紧：**判定性文案只渲染后端下发的 `message`**，前端不自己拼一句解释——两处各写
 * 一遍就是两份口径，而用户正拿着真钱读那一句。
 */
function preview(over: Partial<AiTradeResumePreview> = {}): AiTradeResumePreview {
  return {
    groupId: "g",
    pausedTs: 1_700_000_000,
    asOfTs: 1_700_003_600,
    offlineSeconds: 3600,
    offlineText: "1 小时",
    ageExceeded: false,
    resumedCount: 0,
    pauseReason: "user",
    pauseReasonText: "你暂停了它",
    contract: {
      bar: "15m",
      instIds: ["BTC-USDT-SWAP"],
      totalBudgetUsd: 300,
      leverage: 3,
      stopLossPct: 0.01,
      takeProfitPct: 0.03,
      disciplinePreset: "balanced",
      strategyNames: ["趋势带波段"],
    },
    drift: [],
    members: [
      { instId: "BTC-USDT-SWAP", verdict: "matched", message: "与暂停时一致（多头 0.6 张）。" },
    ],
    verdict: "ready",
    blockedReason: null,
    ...over,
  };
}

function renderDialog(data: AiTradeResumePreview | null, extra: Record<string, unknown> = {}) {
  const onConfirm = vi.fn();
  const onEnd = vi.fn();
  render(
    <AiTradeResumeDialog
      open
      preview={data}
      loading={false}
      submitting={false}
      onClose={() => {}}
      onConfirm={onConfirm}
      onEnd={onEnd}
      {...extra}
    />,
  );
  return { onConfirm, onEnd };
}

afterEach(() => cleanup());

describe("AiTradeResumeDialog", () => {
  it("ready ⇒ 无需勾选即可继续，且下发的是 acknowledge=false", () => {
    const { onConfirm } = renderDialog(preview());
    const submit = screen.getByRole("button", { name: "继续" });
    expect(submit).not.toBeDisabled();
    fireEvent.click(submit);
    expect(onConfirm).toHaveBeenCalledWith({ acknowledge: false });
  });

  it("needs_confirm ⇒ 未勾选时提交按钮 disabled；勾选后才放行（G-24）", () => {
    const { onConfirm } = renderDialog(
      preview({
        verdict: "needs_confirm",
        members: [
          {
            instId: "BTC-USDT-SWAP",
            verdict: "closed_offline",
            message: "离线期间该仓已被平掉（暂停时为多头 0.6 张，现在无持仓）。续跑将以空仓接管。",
          },
        ],
      }),
    );
    const submit = screen.getByRole("button", { name: "继续" });
    expect(submit).toBeDisabled();

    fireEvent.click(screen.getByLabelText("我已了解离线期间的持仓变化"));
    expect(submit).not.toBeDisabled();
    fireEvent.click(submit);
    expect(onConfirm).toHaveBeenCalledWith({ acknowledge: true });
  });

  it("blocked ⇒ 提交按钮恒 disabled，且给出「结束并归档」这条出路", () => {
    const { onEnd } = renderDialog(
      preview({
        verdict: "blocked",
        blockedReason: null,
        members: [
          {
            instId: "BTC-USDT-SWAP",
            verdict: "foreign_position",
            message: "当前持仓不是暂停时那一笔。",
          },
        ],
      }),
    );
    expect(screen.getByRole("button", { name: "继续" })).toBeDisabled();
    expect(screen.getByText("不是本会话的持仓")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "结束并归档" }));
    expect(onEnd).toHaveBeenCalledTimes(1);
  });

  it("判定性说明只渲染后端下发的 message（前端绝不自己拼一句解释）", () => {
    const message = "离线期间该仓被部分平掉（0.6 → 0.4 张）。续跑将按交易所真值 0.4 张接管。";
    renderDialog(
      preview({
        verdict: "needs_confirm",
        members: [{ instId: "BTC-USDT-SWAP", verdict: "partially_closed", message }],
      }),
    );
    expect(screen.getByText(message)).toBeInTheDocument();
  });

  it("契约与漂移清单都渲染出来（续跑后风控参数失真是本轮点名的风险 3）", () => {
    renderDialog(
      preview({
        drift: [
          {
            field: "discipline.preset",
            from: "balanced",
            to: "strict",
            reason: "服务端默认预设已变更",
          },
        ],
      }),
    );
    expect(screen.getByText(/单仓止损/)).toBeInTheDocument();
    expect(screen.getByText(/discipline\.preset：balanced → strict/)).toBeInTheDocument();
  });

  it("离线时长直接渲染后端的 offlineText（不在前端按本地时钟重算一遍，避免两个数打架）", () => {
    renderDialog(preview({ offlineText: "8 小时 40 分" }));
    expect(screen.getByText(/已暂停 8 小时 40 分/)).toBeInTheDocument();
  });

  it("正在核对持仓时给出等待反馈，而不是一个空壳弹窗", () => {
    renderDialog(null, { loading: true });
    expect(screen.getByText("正在核对持仓…")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "继续" })).toBeDisabled();
  });
});
