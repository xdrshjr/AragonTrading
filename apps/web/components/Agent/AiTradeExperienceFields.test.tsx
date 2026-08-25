import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import AiTradeExperienceFields from "./AiTradeExperienceFields";
import { AI_TRADE_EXPERIENCE_DEFAULTS } from "@/lib/experience";
import { experienceApi } from "@/lib/experienceApi";

// 启动弹窗的「经验模式」卡片（A-51 / ★ E-15 / 评审 P2-4）。
//
// 两条必须写在界面上的事实：
// ① 取消勾选 = **既不读也不写**——这张卡的形状（父复选框 + 一个「写回」子复选框）看起来像是
//    只关读，不写那一行明文，用户会以为收据还在攒，而经验库最贵的东西就是那些收据；
// ② 运维总闸关闭时整卡禁用——显示一个勾上了却毫无作用的复选框比不显示更坏。

const OVERVIEW = {
  lessons: { iron: 3, normal: 12, candidate: 40, muted: 5, archived: 0 },
  events: 2043, sessions: 37, symbols: ["BTC-USDT-SWAP"], lastUpdatedTs: 1_700_000_000,
  excludedByCostBasis: 0, rejectedNarrative: 0, harvestGapTotal: 0,
  materializeHealth: { lastTs: 1_700_000_000, lastMs: 100, events: 2043,
                       slow: false, torn: false, overflow: false },
  costRegimeCuts: {},
};

const SETTINGS = {
  enabled: true, defaultOn: true, writeEnabled: true, minSamples: 8, ironSamples: 20,
  tThreshold: 2, halfLifeDays: 30, staleDays: 90, l0Max: 3, l0MaxChars: 480,
  l1Max: 3, l1MaxChars: 660, distillEnabled: true, l0MinChars: 220, l1MinChars: 164,
};

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

beforeEach(() => {
  vi.spyOn(experienceApi, "overview").mockResolvedValue(OVERVIEW as never);
  vi.spyOn(experienceApi, "settings").mockResolvedValue(SETTINGS as never);
});

function renderCard(over: Partial<React.ComponentProps<typeof AiTradeExperienceFields>> = {}) {
  const onChange = vi.fn();
  render(
    <AiTradeExperienceFields
      value={AI_TRADE_EXPERIENCE_DEFAULTS}
      onChange={onChange}
      {...over}
    />,
  );
  return onChange;
}

describe("AiTradeExperienceFields", () => {
  it("默认勾选，并把库存说成人话", async () => {
    renderCard();
    expect(screen.getByLabelText("启用经验模式")).toBeChecked();
    await waitFor(() =>
      expect(screen.getByTestId("ai-trade-experience-fields"))
        .toHaveTextContent("37 组有成交记录的自动交易"));
    expect(screen.getByTestId("ai-trade-experience-fields")).toHaveTextContent("55 条经验");
  });

  it("取消勾选 ⇒ 提交 `{enabled:false}`（显式关闭，不是省略整键）", async () => {
    const onChange = renderCard();
    fireEvent.click(screen.getByLabelText("启用经验模式"));
    expect(onChange).toHaveBeenCalledWith({ ...AI_TRADE_EXPERIENCE_DEFAULTS, enabled: false });
  });

  it("A-51 · 取消勾选时现出「既不读取也不沉淀」那一行明文", () => {
    renderCard({ value: { ...AI_TRADE_EXPERIENCE_DEFAULTS, enabled: false } });
    const note = screen.getByTestId("ai-trade-experience-off-note");
    expect(note).toHaveTextContent("既不读取也不沉淀");
    expect(note).toHaveTextContent("写回经验库");   // 指出「只想用但不沉淀」该怎么做
  });

  it("A-51 · 运维总闸关闭 ⇒ 整卡禁用 + 一行灰字，而不是一个勾上了却没用的复选框", async () => {
    vi.spyOn(experienceApi, "settings")
      .mockResolvedValue({ ...SETTINGS, enabled: false } as never);
    renderCard();
    await waitFor(() => expect(screen.getByTestId("ai-trade-experience-ops-off")).toBeVisible());
    expect(screen.getByLabelText("启用经验模式")).toBeDisabled();
    expect(screen.getByTestId("ai-trade-experience-ops-off")).toHaveTextContent("已被运维关闭");
  });

  it("接口失败 ⇒ 只隐藏那两行数字，勾选与启动一律不受影响", async () => {
    vi.spyOn(experienceApi, "overview").mockRejectedValue(new Error("后端没起来"));
    vi.spyOn(experienceApi, "settings").mockRejectedValue(new Error("后端没起来"));
    const onChange = renderCard();
    await waitFor(() => expect(screen.getByLabelText("启用经验模式")).toBeEnabled());
    expect(screen.queryByTestId("ai-trade-experience-ops-off")).toBeNull();
    fireEvent.click(screen.getByLabelText("启用经验模式"));
    expect(onChange).toHaveBeenCalled();       // 读不到历史绝不能挡住真实资金会话的启动
  });

  it("回测 tab ⇒ 整卡只读并说明理由（回测的成本口径与实盘不同）", () => {
    renderCard({ backtest: true });
    expect(screen.getByLabelText("启用经验模式")).toBeDisabled();
    expect(screen.getByTestId("ai-trade-experience-fields"))
      .toHaveTextContent("回测不读取也不写入经验库");
  });

  it("只读模式（勾选但不写回）在卡片上有一句人话", () => {
    renderCard({ value: { ...AI_TRADE_EXPERIENCE_DEFAULTS, write: false } });
    expect(screen.getByTestId("ai-trade-experience-fields"))
      .toHaveTextContent("这次交易的收据不会进入经验库");
  });
});
