import "@testing-library/jest-dom/vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import AiTradeTradabilityFields, {
  type AiTradeTradabilityFieldsProps,
} from "./AiTradeTradabilityFields";
import type { AiTradeTradabilityResponse } from "@/lib/aiTradeTypes";

// 可交易性预检子组件（tradability-and-open-funnel 轴 A · G-4）。
// 三件必须守住的事：四档渲染 / `infeasible` 时启动**仍可点**但需二次确认（且确认态在跃迁时重置）/
// 切 bar 后 debounce 350ms 重取。**判据与文案一个字都不重写**——夹具给什么就渲染什么。

const probe = vi.fn();
vi.mock("@/lib/aiTradeHorizonApi", () => ({
  aiTradeTradabilityApi: { probe: (...args: unknown[]) => probe(...args) },
}));

function wire(over: Partial<AiTradeTradabilityResponse> = {}): AiTradeTradabilityResponse {
  return {
    instId: "BTC-USDT-SWAP",
    bar: "1m",
    preset: "balanced",
    level: "infeasible",
    samples: 200,
    source: "cache",
    asOfTs: 1786600000,
    atrBps: { p10: 2.9, p50: 5.6, p90: 11.3 },
    costBps: { market: 13.0, limit: 8.5 },
    costConfidence: { market: "degraded", limit: "degraded" },
    degradedMultApplied: 1.2,
    requiredBps: { market: 23.4, limit: 15.3 },
    vetoRate: { market: 0.986, limit: 0.926 },
    stopBps: { p50: 11.2 },
    stopInsideCostRate: { market: 0.623, limit: 0.287 },
    alternatives: [{ bar: "15m", vetoRateMarket: 0.203, costBpsMarket: 13.0, level: "marginal" }],
    headline: "在 1m 周期上……约 98.6% 的开仓意图会被否决。",
    advice: "建议改用 15m 或更长的时间级别。换预设解决不了……",
    disclaimer: "以上是确定性统计，不是收益预测。",
    ...over,
  };
}

/** 推进 fake timer 并冲刷 React 的状态更新（避免 act 警告淹没真实失败）。 */
async function advance(ms: number) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
}

// 入参按组件自己的 props 类型标注（**不是** `Record<string, unknown>` + `as never`）：
// `as never` 会让 `{...props}` 报 TS2698，而这个文件本身就是那两个受控回调的契约门禁——
// 用 never 绕开类型检查，等于把「回调签名改了没人发现」这条风险原地放行。
function renderFields(over: Partial<AiTradeTradabilityFieldsProps> = {}) {
  const onPickBar = vi.fn();
  const onConfirmedChange = vi.fn();
  const props: AiTradeTradabilityFieldsProps = {
    open: true,
    instId: "BTC-USDT-SWAP",
    bar: "1m",
    preset: "balanced",
    onPickBar,
    confirmed: false,
    onConfirmedChange,
    disabled: false,
    ...over,
  };
  const utils = render(<AiTradeTradabilityFields {...props} />);
  return { ...utils, onPickBar, onConfirmedChange, props };
}

beforeEach(() => {
  // `shouldAdvanceTime` 是必需的：`waitFor` 在纯 fake timer 下会挂死。代价是「render 到断言之间
  // 真的会过掉真实时间」，故 debounce 那条用例断言的是**行为**（连续切换只发一次请求）而不是
  // 「340ms 时还没发」那种对真实耗时敏感的写法——后者不是更严，只是不稳。
  vi.useFakeTimers({ shouldAdvanceTime: true });
  probe.mockReset();
  probe.mockResolvedValue(wire());
});

afterEach(() => {
  vi.useRealTimers();
  cleanup();
});

describe("AiTradeTradabilityFields", () => {
  it("连续切换在 debounce 窗口内合并成一次请求（切 bar 期间不打爆 REST）", async () => {
    const { rerender, props } = renderFields();
    rerender(<AiTradeTradabilityFields {...{ ...props, bar: "5m" }} />);
    rerender(<AiTradeTradabilityFields {...{ ...props, bar: "15m" }} />);
    await advance(400);
    expect(probe).toHaveBeenCalledTimes(1);
    expect(probe).toHaveBeenCalledWith("BTC-USDT-SWAP", "15m", "balanced");
  });

  it("切换 bar 之后重新取一次（G-4）", async () => {
    const { rerender, props } = renderFields();
    await advance(400);
    expect(probe).toHaveBeenCalledTimes(1);
    probe.mockResolvedValue(wire({ bar: "1H", level: "ok", advice: null }));
    rerender(<AiTradeTradabilityFields {...{ ...props, bar: "1H" }} />);
    await advance(400);
    expect(probe).toHaveBeenCalledTimes(2);
    expect(probe).toHaveBeenLastCalledWith("BTC-USDT-SWAP", "1H", "balanced");
  });

  it("infeasible ⇒ 红色结论 + 后端原文建议 + 二次确认；启动按钮不由本组件禁用", async () => {
    const { onConfirmedChange } = renderFields();
    await advance(400);
    await waitFor(() => expect(screen.getByTestId("ai-tradability-level")).toBeInTheDocument());
    expect(screen.getByTestId("ai-tradability-level")).toHaveTextContent("不可行");
    expect(screen.getByTestId("ai-tradability-headline")).toHaveTextContent("98.6%");
    // **文案一个字都不重写**：建议原样来自后端。
    expect(screen.getByTestId("ai-tradability-advice")).toHaveTextContent("换预设解决不了");
    const confirm = screen.getByTestId("ai-tradability-confirm");
    fireEvent.click(confirm.querySelector("input")!);
    expect(onConfirmedChange).toHaveBeenCalledWith(true);
    // 本组件内没有任何 disabled 的启动按钮——它只提示、不阻断。
    expect(screen.queryByRole("button", { name: /启动/ })).toBeNull();
  });

  it("一键换周期只改本弹窗的时间级别，不发任何请求", async () => {
    const { onPickBar } = renderFields();
    await advance(400);
    await waitFor(() => expect(screen.getByTestId("ai-tradability-pick-bar")).toBeInTheDocument());
    const before = probe.mock.calls.length;
    fireEvent.click(screen.getByTestId("ai-tradability-pick-bar"));
    expect(onPickBar).toHaveBeenCalledWith("15m");
    expect(probe.mock.calls.length).toBe(before);
  });

  it("ok / marginal / unknown 三档都不要求确认，且 unknown 不出现任何阻碍性文案", async () => {
    for (const level of ["ok", "marginal", "unknown"] as const) {
      probe.mockResolvedValue(wire({ level, advice: level === "ok" ? null : "建议" }));
      renderFields();
      await advance(400);
      await waitFor(() => expect(screen.getByTestId("ai-tradability-level")).toBeInTheDocument());
      expect(screen.queryByTestId("ai-tradability-confirm")).toBeNull();
      expect(screen.queryByTestId("ai-tradability-pick-bar")).toBeNull();
      cleanup();
    }
  });

  it("[P2-9] 由非 infeasible 跃迁到 infeasible 时确认态必须重置", async () => {
    probe.mockResolvedValue(wire({ level: "ok", advice: null }));
    const { rerender, onConfirmedChange, props } = renderFields({ confirmed: true });
    await advance(400);
    expect(onConfirmedChange).not.toHaveBeenCalled();     // ok → ok，不动用户已勾的确认
    probe.mockResolvedValue(wire({ level: "infeasible" }));
    rerender(<AiTradeTradabilityFields {...{ ...props, confirmed: true, bar: "5m" }} />);
    await advance(400);
    await waitFor(() => expect(onConfirmedChange).toHaveBeenCalledWith(false));
  });

  it("取数失败只说「不影响启动」，绝不把用户挡在门外", async () => {
    probe.mockRejectedValue(new Error("boom"));
    renderFields();
    await advance(400);
    await waitFor(() => expect(screen.getByTestId("ai-trade-tradability")).toHaveTextContent(
      "这不影响启动",
    ));
  });

  it("缓存来源必须说出来（探针无条件读缓存是一次有意偏离）", async () => {
    renderFields();
    await advance(400);
    await waitFor(() => expect(screen.getByTestId("ai-tradability-note")).toHaveTextContent(
      "本地缓存",
    ));
    expect(screen.getByTestId("ai-tradability-note")).toHaveTextContent("不是收益预测");
  });
});
