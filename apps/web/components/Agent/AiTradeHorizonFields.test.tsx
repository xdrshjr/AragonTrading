import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import AiTradeHorizonFields, {
  type AiTradeHorizonFieldsProps,
  type HorizonCustomFields,
} from "./AiTradeHorizonFields";
import type { AiTradeHorizonProfilesResponse } from "@/lib/aiTradeTypes";

// 交易周期模式卡片组（ai-trade-holding-horizon-modes §6.2）：单选可达性 + 契约摘要 + custom 不联动。
// 数值与中文标签一律由夹具（= 接口下发）提供，组件内不得有任何预设副本（R-3 / G-10）。

const DATA: AiTradeHorizonProfilesResponse = {
  enabled: true,
  defaultProfile: "intraday_swing",
  estimatedCostBps: 13,
  lossReleaseFrac: 0.5,
  lossReleaseReturnPctFallback: 0.1,
  backstopRepair: {
    defaultMode: "plan",
    modes: [
      { id: "plan", label: "仅提醒", detail: "系统只给出补挂方案，由你一键执行" },
      { id: "arm", label: "自动补挂", detail: "核实到止损缺失时由系统自动补挂" },
    ],
    note: "补挂只补止损腿、只补差额张数、永不平仓、永不加仓。",
    maxPerEpoch: 2,
    maxPerSession: 8,
  },
  profiles: [
    {
      id: "intraday_swing", title: "日内波段", description: "小时级别一段趋势。",
      targetLabel: "4 – 12 小时", bar: "1H", wakeIntervalMinutes: 30, minHoldSeconds: 7200,
      exitConfirmRounds: 2, turnoverHysteresisK: 1.5, targetHoldMinSeconds: 14400,
      targetHoldMaxSeconds: 43200, expectedHoldBars: 12, confirmWindowSeconds: 3600,
      leverageMaxHint: 3,
    },
    {
      id: "trend_hold", title: "趋势持仓", description: "跟随一段中线趋势。",
      targetLabel: "1 – 3 天", bar: "4H", wakeIntervalMinutes: 0, minHoldSeconds: 43200,
      exitConfirmRounds: 3, turnoverHysteresisK: 2, targetHoldMinSeconds: 86400,
      targetHoldMaxSeconds: 259200, expectedHoldBars: 18, confirmWindowSeconds: 21600,
      leverageMaxHint: 2,
    },
    {
      id: "custom", title: "自定义", description: "自行设定。", targetLabel: null, bar: null,
      wakeIntervalMinutes: null, minHoldSeconds: null, exitConfirmRounds: null,
      turnoverHysteresisK: null, targetHoldMinSeconds: null, targetHoldMaxSeconds: null,
      expectedHoldBars: null, confirmWindowSeconds: null, leverageMaxHint: null,
    },
  ],
};

const EMPTY_CUSTOM: HorizonCustomFields = {
  minHoldRaw: "", confirmRoundsRaw: "", hysteresisRaw: "",
  targetMinHoursRaw: "", targetMaxHoursRaw: "",
};

function renderFields(over: Partial<AiTradeHorizonFieldsProps> = {}) {
  const onProfileChange = vi.fn();
  const onCustomChange = vi.fn();
  const onAlign = vi.fn();
  const onRepairModeChange = vi.fn();
  render(
    <AiTradeHorizonFields
      data={DATA}
      loading={false}
      enabled
      onEnabledChange={vi.fn()}
      profileId="intraday_swing"
      onProfileChange={onProfileChange}
      custom={EMPTY_CUSTOM}
      onCustomChange={onCustomChange}
      bar="1H"
      leverage={3}
      stopLossPct={0.05}
      defaultStopLossPct={0.05}
      wakeMinutes={30}
      onAlign={onAlign}
      backstopRepairMode={null}
      onBackstopRepairModeChange={onRepairModeChange}
      disabled={false}
      {...over}
    />,
  );
  return { onProfileChange, onCustomChange, onAlign, onRepairModeChange };
}

afterEach(() => cleanup());

describe("AiTradeHorizonFields", () => {
  it("是一个真正的单选组：radiogroup + 每卡 role=radio + aria-checked 正确反映状态", () => {
    renderFields();
    const group = screen.getByRole("radiogroup", { name: "交易周期模式" });
    expect(group).toBeInTheDocument();
    // 按组取（同屏另有「交易所侧兜底自愈」那个两段式 radiogroup，全局 getAllByRole 会把它算进来）。
    expect(within(group).getAllByRole("radio")).toHaveLength(DATA.profiles.length);
    expect(screen.getByRole("radio", { name: /日内波段/ })).toBeChecked();
    expect(screen.getByRole("radio", { name: /趋势持仓/ })).not.toBeChecked();
  });

  it("方向键在卡片间循环切换（键盘可达；只有鼠标能用的单选组不算单选组）", () => {
    const { onProfileChange } = renderFields();
    fireEvent.keyDown(screen.getByRole("radiogroup", { name: "交易周期模式" }),
                      { key: "ArrowRight" });
    expect(onProfileChange).toHaveBeenCalledWith(
      expect.objectContaining({ id: "trend_hold" }));
    fireEvent.keyDown(screen.getByRole("radiogroup", { name: "交易周期模式" }),
                      { key: "ArrowLeft" });
    expect(onProfileChange).toHaveBeenLastCalledWith(
      expect.objectContaining({ id: "custom" }));   // 循环到末位
  });

  it("契约摘要默认可见（不藏在 details 里）且四行随 profile 变化", () => {
    renderFields();
    const summary = screen.getByTestId("ai-trade-horizon-contract").textContent ?? "";
    expect(summary).toContain("开仓后 2 小时内不接受 AI 主动平仓");
    expect(summary).toContain("连续 2 轮");
    expect(summary).toContain("需 ≥19.5bps");
    expect(summary).toContain("−7.50%");
    cleanup();
    renderFields({ profileId: "trend_hold", bar: "4H" });
    expect(screen.getByTestId("ai-trade-horizon-contract").textContent)
      .toContain("开仓后 12 小时内不接受 AI 主动平仓");
  });

  it("契约摘要显式点明「利润保护层不受本层限制」（[P0-1] 的同屏承诺必须是真的）", () => {
    renderFields();
    expect(screen.getByTestId("ai-trade-horizon-contract").textContent)
      .toContain("利润保护层有它自己的最短持有期");
  });

  it("custom（bar === null）⇒ 露出高级参数、契约摘要退化为一行说明", () => {
    const { onCustomChange } = renderFields({ profileId: "custom" });
    const summary = screen.getByTestId("ai-trade-horizon-contract").textContent ?? "";
    expect(summary).toContain("由你自己设定");
    fireEvent.change(screen.getByLabelText("离场连续确认轮数（1=不约束）"),
                     { target: { value: "3" } });
    expect(onCustomChange).toHaveBeenCalledWith("confirmRoundsRaw", "3");
  });

  it("一致性提示为**非阻断**提示 + 「按模式对齐」按钮（静默夹紧比不夹更危险）", () => {
    const { onAlign } = renderFields({ leverage: 10 });
    const hints = screen.getByTestId("ai-trade-horizon-hints");
    expect(hints.textContent).toContain("建议上限 3×");
    fireEvent.click(screen.getByRole("button", { name: /按模式对齐/ }));
    expect(onAlign).toHaveBeenCalledTimes(1);
  });

  it("接口未就绪 ⇒ 如实显示加载中，绝不用一份本地副本顶上（R-3）", () => {
    renderFields({ data: null, loading: true });
    expect(screen.getByTestId("ai-trade-horizon-loading").textContent).toContain("正在读取");
    expect(screen.queryByRole("radiogroup")).toBeNull();
  });

  it("读取失败 ⇒ 说是失败并说明本次仍走服务端默认模式（静默降级会被当成故障）", () => {
    renderFields({ data: null, loading: false });
    const hint = screen.getByTestId("ai-trade-horizon-loading").textContent ?? "";
    expect(hint).toContain("读取失败");
    expect(hint).toContain("服务端按默认模式装配");
  });

  it("取消勾选 ⇒ 整段折叠（卡片与摘要都不渲染）", () => {
    renderFields({ enabled: false });
    expect(screen.queryByRole("radiogroup")).toBeNull();
    expect(screen.queryByTestId("ai-trade-horizon-contract")).toBeNull();
  });
});

// ---------------- 交易所侧兜底自愈控件（backstop-repair §2.4.1 · U-20 / U-20b） ----------------
describe("AiTradeHorizonFields · 兜底自愈控件", () => {
  it("U-20 默认选中项来自接口的 `defaultMode`，**不是硬编码的 plan**", () => {
    renderFields();
    expect(screen.getByTestId("ai-trade-backstop-repair-plan")).toHaveAttribute(
      "aria-checked", "true");
    cleanup();
    renderFields({
      data: { ...DATA, backstopRepair: { ...DATA.backstopRepair!, defaultMode: "arm" } },
    });
    expect(screen.getByTestId("ai-trade-backstop-repair-arm")).toHaveAttribute(
      "aria-checked", "true");
  });

  it("整块缺席（运维总闸关）⇒ 控件不渲染（绝不给一个点了不生效的开关）", () => {
    renderFields({ data: { ...DATA, backstopRepair: null } });
    expect(screen.queryByTestId("ai-trade-backstop-repair")).toBeNull();
  });

  it("U-20b [P1-10] 不受跨日门控：非跨日模式下控件**仍然渲染**", () => {
    // `intraday_swing` 的 targetHoldMaxSeconds = 43200 < 86400 ⇒ 同屏的 BackstopNotice 不渲染，
    // 而自愈控件必须在场——随单 OCO 丢失与持仓周期长短无关，日内的仓一样会裸露。
    renderFields({ profileId: "intraday_swing" });
    expect(screen.queryByTestId("ai-trade-backstop-notice")).toBeNull();
    expect(screen.getByTestId("ai-trade-backstop-repair")).toBeInTheDocument();
  });

  it("选中「自动补挂」才出现授权确认语——这是唯一一处「系统会自己花钱」的授权点", () => {
    renderFields();
    expect(screen.queryByTestId("ai-trade-backstop-repair-consent")).toBeNull();
    cleanup();
    renderFields({ backstopRepairMode: "arm" });
    expect(screen.getByTestId("ai-trade-backstop-repair-consent").textContent).toContain(
      "你不在场时向交易所发出止损委托");
  });

  it("点击即回调（本组件是纯受控件，自己不持状态——那是 modal-revert 复发的根因）", () => {
    const { onRepairModeChange } = renderFields();
    fireEvent.click(screen.getByTestId("ai-trade-backstop-repair-arm"));
    expect(onRepairModeChange).toHaveBeenCalledWith("arm");
  });

  it("说明文字与两个上限全部来自接口，前端不硬编任何一个数", () => {
    renderFields({
      data: {
        ...DATA,
        backstopRepair: { ...DATA.backstopRepair!, maxPerEpoch: 5, maxPerSession: 30 },
      },
    });
    const text = screen.getByTestId("ai-trade-backstop-repair").textContent ?? "";
    expect(text).toContain("只补差额张数");
    expect(text).toContain("5");
    expect(text).toContain("30");
  });
});
