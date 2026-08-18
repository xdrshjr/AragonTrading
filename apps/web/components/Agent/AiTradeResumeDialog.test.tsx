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

// ============ 第三屏「正在武装」（ai-trade-lifecycle-truth F-8）============

const DLG_MEMBER_WIRE = {
  state: "running" as const, armed: true, reason: "armed" as const, reasonText: "",
  warmedBars: 300, feedAgeS: 3, staleAfterS: 10800, linkState: "up",
  nextWakeInS: 192, nextBarInS: 480, roundsCompleted: 4,
  pauseReason: null, pausedTs: 0, resumedTs: 0, stopReason: null,
};

function member(over: Record<string, unknown> = {}) {
  return {
    instId: "BTC-USDT-SWAP", sessionId: "s1", allocationPct: 100,
    session: { sessionId: "s1", runId: 1, state: "running", instId: "BTC-USDT-SWAP", bar: "1H",
               strategyIds: [1], strategyNames: ["S"], budgetUsd: 100, leverage: 3,
               contextRefreshRounds: 10, roundsCompleted: 0, roundsSinceSummary: 0,
               positionSide: "flat", sessionPnlUsd: 0, startedTs: 0,
               lifecycle: DLG_MEMBER_WIRE, ...over },
    trades: [], position: null, decisions: [], traces: [], summary: null,
  } as never;
}

function groupWire(over: Record<string, unknown> = {}) {
  return {
    state: "running", declaredState: "running", armed: true, membersArmed: 1, membersTotal: 1,
    asOfTs: 1_755_500_000, notArmed: [], nextWakeInS: 192, nextBarInS: 480,
    pauseReason: null, pausedTs: 0, resumedCount: 0, ...over,
  } as never;
}

describe("AiTradeResumeDialog · 武装清单三屏流转（F-8）", () => {
  it("未提交时是复核屏，`arming` 清单不出现", () => {
    renderDialog(preview());
    expect(screen.queryByTestId("ai-trade-arming-checklist")).not.toBeInTheDocument();
    expect(screen.getByText("继续 AI 自动交易")).toBeInTheDocument();
  });

  it("提交后进入第二屏：清单出现、复核内容让位、标题改为「正在恢复」", () => {
    renderDialog(preview(), {
      arming: true, armingSince: 1_000, nowTs: 1_005, submitting: true,
      lifecycle: groupWire({ armed: false, membersArmed: 0 }),
      members: [member({ lifecycle: { ...DLG_MEMBER_WIRE, armed: false,
                                      reason: "not_warmed",
                                      reasonText: "行情窗口尚未暖机完成，Agent 暂不响应。" } })],
    });
    const list = screen.getByTestId("ai-trade-arming-checklist");
    expect(screen.getByText("正在恢复 AI 自动交易")).toBeInTheDocument();
    expect(list).toHaveTextContent("正在核对持仓、重建会话、接上行情");
    expect(list).toHaveTextContent("武装中");
    // 判定性说明**一律取后端 `reasonText`**，前端不按 code 自己拼中文。
    expect(list).toHaveTextContent("行情窗口尚未暖机完成");
    // 还没超时 ⇒ 不给「重试继续」（那会诱导用户在武装途中再打一次真实资金的开仓授权）。
    expect(screen.queryByRole("button", { name: "重试继续" })).not.toBeInTheDocument();
  });

  it("全部就绪 ⇒ 第三屏说「全部就绪」，四行逐项打勾", () => {
    renderDialog(preview(), {
      arming: true, armingSince: 1_000, nowTs: 1_003,
      lifecycle: groupWire(),
      members: [member()],
    });
    const list = screen.getByTestId("ai-trade-arming-checklist");
    expect(list).toHaveTextContent("全部就绪：Agent 已恢复自动交易。");
    expect(list).toHaveTextContent("已武装");
    for (const line of ["会话已建", "行情已连", "已接管持仓", "下一次决策"]) {
      expect(list).toHaveTextContent(line);
    }
    expect(list).toHaveTextContent("03:12");     // 下一次决策的真实读数，不是占位
  });

  it("超过 45 秒仍未 armed ⇒ 原地变成如实的失败说明 + 两条**真实可走的路**（风险 R-12）", () => {
    renderDialog(preview(), {
      arming: true, armingSince: 1_000, nowTs: 1_050,      // 已过 50 秒
      lifecycle: groupWire({ armed: false, membersArmed: 0 }),
      members: [member({ lifecycle: { ...DLG_MEMBER_WIRE, armed: false, state: "paused",
                                      reason: "feed_down",
                                      reasonText: "实时链路不可达，Agent 暂不响应；交易所侧止盈止损仍生效。" } })],
    });
    const list = screen.getByTestId("ai-trade-arming-checklist");
    expect(list).toHaveTextContent("仍有标的没有武装成功");
    expect(list).toHaveTextContent("实时链路不可达");
    expect(list).toHaveTextContent("未武装");
    expect(screen.getByRole("button", { name: "重试继续" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "结束并归档" })).toBeInTheDocument();
    // 「失败」这两个字不该出现：后端可能还在暖机，说它失败是一句没被证实的话。
    expect(list.textContent).not.toContain("失败");
  });

  it("后端还没下发成员行 ⇒ 如实说「正在等待」，绝不画一张空的全绿清单", () => {
    renderDialog(preview(), {
      arming: true, armingSince: 1_000, nowTs: 1_002, lifecycle: null, members: [],
    });
    expect(screen.getByTestId("ai-trade-arming-checklist"))
      .toHaveTextContent("正在等待后端下发成员状态…");
  });
});
