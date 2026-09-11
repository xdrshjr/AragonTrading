import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type {
  AiTradeTrajectoryConfig,
  AiTradeTrajectoryDirVerdict,
  AiTradeTrajectoryRule,
} from "@/lib/trajectoryTypes";
import AiTradeTrajectoryFields from "./AiTradeTrajectoryFields";
import type { UseTrajectoryCapture } from "./launch/useTrajectoryCapture";

/**
 * 第 4 步「后训练数据采集」卡片（ai-trade-trajectory-capture §6.2 F-2）。
 *
 * 卡片是受控纯视图：`capture` 由表单层的 `useTrajectoryCapture` 算好传进来，这里直接构造它、
 * 不起 React Query——守的是**渲染契约**（生效值、禁用原因、草稿提交时机、桌面桥的有无）。
 * 取数与谓词本身在 `lib/trajectory.test.ts` 与向导用例 W-T* / F-9 里。
 */

const CONFIG: AiTradeTrajectoryConfig = {
  enabled: true,
  defaultRoot: "C:\\Users\\me\\AppData\\Roaming\\aragon-desktop\\datasets\\ai-trade",
  minFreeBytes: 1_073_741_824,
  segmentMaxBytes: 67_108_864,
  compressSegments: true,
  schemaVersion: 1,
  backtestSupported: false,
  thinkingCapturedProviders: ["anthropic"],
};

const OK_VERDICT: AiTradeTrajectoryDirVerdict = {
  ok: true,
  code: null,
  message: null,
  directory: "D:\\data\\ai-trade",
  isDefault: false,
  exists: true,
  freeBytes: 137_870_000_000,
  minFreeBytes: 1_073_741_824,
};

const BAD_VERDICT: AiTradeTrajectoryDirVerdict = {
  ...OK_VERDICT,
  ok: false,
  code: "TRAJECTORY_DIR_NOT_WRITABLE",
  message: "目录不可写：拒绝访问（13）",
};

interface QueryView<T> {
  isPending?: boolean;
  isFetching?: boolean;
  isError?: boolean;
  data?: T;
  refetch?: () => Promise<unknown>;
}

function makeCapture(
  over: {
    config?: QueryView<AiTradeTrajectoryConfig>;
    verdict?: QueryView<AiTradeTrajectoryDirVerdict>;
    effectiveEnabled?: boolean;
  } = {},
): UseTrajectoryCapture {
  return {
    configQ: { isPending: false, isError: false, data: CONFIG, ...over.config },
    verdictQ: { isFetching: false, isError: false, data: undefined, ...over.verdict },
    effectiveEnabled: over.effectiveEnabled ?? false,
    error: null,
    notice: null,
    requestRule: undefined,
    destination: null,
  } as unknown as UseTrajectoryCapture;
}

const OFF: AiTradeTrajectoryRule = { enabled: false, directory: null };
const ON: AiTradeTrajectoryRule = { enabled: true, directory: null };
const LIVE = () => makeCapture({ effectiveEnabled: true });

type CardProps = Partial<React.ComponentProps<typeof AiTradeTrajectoryFields>>;

function renderCard(props: CardProps = {}) {
  const onChange = vi.fn();
  const element = (over: CardProps = {}) => (
    <AiTradeTrajectoryFields
      value={OFF}
      onChange={onChange}
      capture={makeCapture()}
      {...props}
      {...over}
    />
  );
  const view = render(element());
  return { onChange, rerender: (over: CardProps = {}) => view.rerender(element(over)) };
}

function setBridge(bridge: Record<string, unknown> | undefined) {
  (window as unknown as { aragon?: unknown }).aragon = bridge;
}

const checkbox = () => screen.getByRole("checkbox", { name: /采集后训练数据/ });
const dirInput = () => screen.getByLabelText("后训练数据保存目录") as HTMLInputElement;

afterEach(() => {
  cleanup();
  setBridge(undefined);
});

describe("AiTradeTrajectoryFields · 开关与禁用原因", () => {
  it("默认不勾、配置可用时可点；点它 ⇒ onChange({enabled:true})，目录区在生效前不展开", () => {
    const { onChange } = renderCard();
    expect(checkbox()).not.toBeChecked();
    expect(checkbox()).not.toBeDisabled();
    expect(screen.queryByTestId("ai-trade-trajectory-detail")).toBeNull();
    fireEvent.click(checkbox());
    expect(onChange).toHaveBeenCalledWith({ enabled: true, directory: null });
  });

  it("回测只渲染一行说明，不渲染任何控件", () => {
    renderCard({ backtest: true, value: ON, capture: LIVE() });
    expect(screen.getByTestId("ai-trade-trajectory-backtest-note")).toHaveTextContent(
      "回测不采集后训练数据（只采集实盘会话）",
    );
    expect(screen.queryByRole("checkbox")).toBeNull();
    expect(screen.queryByRole("textbox")).toBeNull();
    expect(screen.queryByTestId("ai-trade-trajectory-fields")).toBeNull();
  });

  it("【RV3】配置读取失败 ⇒ 禁用且显示为未勾（即使上次选了开），并给出原因", () => {
    renderCard({ value: ON, capture: makeCapture({ config: { isError: true, data: undefined } }) });
    expect(checkbox()).toBeDisabled();
    expect(checkbox()).not.toBeChecked();
    expect(screen.getByTestId("ai-trade-trajectory-lock-reason")).toHaveTextContent(
      "无法读取采集配置（后端尚未就绪），本次不会采集",
    );
  });

  it("配置读取失败 ⇒ 原因旁给「重试」，点它重新读取配置（不必关掉弹窗、丢掉向导里填过的一切）", () => {
    const refetch = vi.fn().mockResolvedValue(undefined);
    renderCard({
      value: ON,
      capture: makeCapture({ config: { isError: true, data: undefined, refetch } }),
    });
    fireEvent.click(screen.getByRole("button", { name: "重试读取采集配置" }));
    expect(refetch).toHaveBeenCalledTimes(1);
  });

  it("配置在途 ⇒ 禁用并说明正在读取（没勾的人也看得见为什么点不动）", () => {
    renderCard({ capture: makeCapture({ config: { isPending: true, data: undefined } }) });
    expect(checkbox()).toBeDisabled();
    expect(screen.getByTestId("ai-trade-trajectory-lock-reason")).toHaveTextContent(
      "正在读取采集配置…",
    );
    expect(screen.queryByRole("button", { name: /重试/ })).toBeNull();
  });

  it("运维关 ⇒ 禁用，原因里用 <code> 写出环境变量名（不用反引号）", () => {
    renderCard({ capture: makeCapture({ config: { data: { ...CONFIG, enabled: false } } }) });
    expect(checkbox()).toBeDisabled();
    const note = screen.getByTestId("ai-trade-trajectory-ops-off");
    expect(note).toHaveTextContent("后训练数据采集已被运维关闭");
    expect(within(note).getByText("AI_TRADE_TRAJECTORY_ENABLED").tagName).toBe("CODE");
  });

  it("提交中（submitting）⇒ 复选框与目录输入都禁用", () => {
    renderCard({ value: ON, capture: LIVE(), disabled: true });
    expect(checkbox()).toBeDisabled();
    expect(dirInput()).toBeDisabled();
  });

  it("有效开启 ⇒ 占位符是服务端下发的默认根目录；底部说明可用空间下限", () => {
    renderCard({ value: ON, capture: LIVE() });
    expect(checkbox()).toBeChecked();
    expect(dirInput().placeholder).toBe(CONFIG.defaultRoot);
    expect(screen.getByTestId("ai-trade-trajectory-detail")).toHaveTextContent(
      "可用空间低于 1.0 GB 时自动暂停采集",
    );
  });
});

describe("AiTradeTrajectoryFields · 目录草稿（R13：只跟随已提交值）", () => {
  it("逐键输入不触发 onChange；失焦才提交（去首尾空白）", () => {
    const { onChange } = renderCard({ value: ON, capture: LIVE() });
    fireEvent.change(dirInput(), { target: { value: "  D:\\data" } });
    fireEvent.change(dirInput(), { target: { value: "  D:\\data\\ai  " } });
    expect(onChange).not.toHaveBeenCalled();
    fireEvent.blur(dirInput());
    expect(onChange).toHaveBeenCalledTimes(1);
    expect(onChange).toHaveBeenCalledWith({ enabled: true, directory: "D:\\data\\ai" });
  });

  it("清空后失焦 ⇒ 提交 null（回到默认目录）；与已提交值等价的草稿失焦 ⇒ 不提交", () => {
    const first = renderCard({ value: { enabled: true, directory: "D:\\x" }, capture: LIVE() });
    fireEvent.change(dirInput(), { target: { value: "   " } });
    fireEvent.blur(dirInput());
    expect(first.onChange).toHaveBeenCalledWith({ enabled: true, directory: null });
    cleanup();

    const second = renderCard({ value: { enabled: true, directory: "D:\\x" }, capture: LIVE() });
    fireEvent.change(dirInput(), { target: { value: " D:\\x " } });
    fireEvent.blur(dirInput());
    expect(second.onChange).not.toHaveBeenCalled();
    expect(dirInput().value).toBe("D:\\x");
  });

  it("Enter 提交；Esc 还原为已提交值（而不是清空）", () => {
    const { onChange } = renderCard({
      value: { enabled: true, directory: "D:\\old" },
      capture: LIVE(),
    });
    fireEvent.change(dirInput(), { target: { value: "D:\\new" } });
    fireEvent.keyDown(dirInput(), { key: "Enter" });
    expect(onChange).toHaveBeenCalledWith({ enabled: true, directory: "D:\\new" });
    fireEvent.change(dirInput(), { target: { value: "D:\\typo" } });
    fireEvent.keyDown(dirInput(), { key: "Escape" });
    expect(dirInput().value).toBe("D:\\old");
    expect(onChange).toHaveBeenCalledTimes(1);
  });

  it("父组件以新引用重渲染（已提交值不变）⇒ 正在敲的草稿不被冲掉", () => {
    const value = { enabled: true, directory: "D:\\old" };
    const { onChange, rerender } = renderCard({ value, capture: LIVE() });
    fireEvent.change(dirInput(), { target: { value: "D:\\half-typed" } });
    // 行情 1Hz flush 的形状：每次都是**新引用**的 value / capture，而已提交的目录没变。
    for (let i = 0; i < 3; i += 1) rerender({ value: { ...value }, capture: LIVE() });
    expect(dirInput().value).toBe("D:\\half-typed");
    expect(onChange).not.toHaveBeenCalled();
  });

  it("已提交值真的变了（例如父组件采纳了「恢复默认」）⇒ 草稿跟随", () => {
    const { rerender } = renderCard({
      value: { enabled: true, directory: "D:\\old" },
      capture: LIVE(),
    });
    expect(dirInput().value).toBe("D:\\old");
    rerender({ value: { enabled: true, directory: null } });
    expect(dirInput().value).toBe("");
  });

  it("「恢复默认」在已是默认目录时不可点；否则提交 directory:null", () => {
    const first = renderCard({ value: ON, capture: LIVE() });
    expect(screen.getByRole("button", { name: "恢复默认目录" })).toBeDisabled();
    cleanup();
    const second = renderCard({ value: { enabled: true, directory: "D:\\x" }, capture: LIVE() });
    fireEvent.click(screen.getByRole("button", { name: "恢复默认目录" }));
    expect(second.onChange).toHaveBeenCalledWith({ enabled: true, directory: null });
    expect(first.onChange).not.toHaveBeenCalled();
  });
});

describe("AiTradeTrajectoryFields · 桌面桥", () => {
  it("无 window.aragon（浏览器）⇒ 不渲染「选择文件夹…」与「打开」", () => {
    renderCard({
      value: ON,
      capture: makeCapture({ effectiveEnabled: true, verdict: { data: OK_VERDICT } }),
    });
    expect(screen.queryByRole("button", { name: /选择文件夹/ })).toBeNull();
    expect(screen.queryByRole("button", { name: "打开后训练数据目录" })).toBeNull();
  });

  it("选择文件夹：默认路径取服务端默认根，返回值被提交；取消（null）不提交", async () => {
    const pickDirectory = vi
      .fn()
      .mockResolvedValueOnce("E:\\picked")
      .mockResolvedValueOnce(null);
    setBridge({ pickDirectory });
    const { onChange } = renderCard({ value: ON, capture: LIVE() });
    fireEvent.click(screen.getByRole("button", { name: /选择文件夹…/ }));
    await waitFor(() =>
      expect(onChange).toHaveBeenCalledWith({ enabled: true, directory: "E:\\picked" }),
    );
    expect(pickDirectory).toHaveBeenCalledWith({ defaultPath: CONFIG.defaultRoot });
    fireEvent.click(screen.getByRole("button", { name: /选择文件夹…/ }));
    await waitFor(() => expect(pickDirectory).toHaveBeenCalledTimes(2));
    expect(onChange).toHaveBeenCalledTimes(1);
  });

  it("选择文件夹：已提交过自定义目录 ⇒ 对话框从那里打开", async () => {
    const pickDirectory = vi.fn().mockResolvedValue(null);
    setBridge({ pickDirectory });
    renderCard({ value: { enabled: true, directory: "D:\\mine" }, capture: LIVE() });
    fireEvent.click(screen.getByRole("button", { name: /选择文件夹…/ }));
    await waitFor(() =>
      expect(pickDirectory).toHaveBeenCalledWith({ defaultPath: "D:\\mine" }),
    );
  });

  it("目录存在 ⇒「打开」调用 openPath(verdict.directory)；失败就地说明原因", async () => {
    const openPath = vi.fn().mockResolvedValue("not_found");
    setBridge({ openPath });
    renderCard({
      value: ON,
      capture: makeCapture({ effectiveEnabled: true, verdict: { data: OK_VERDICT } }),
    });
    fireEvent.click(screen.getByRole("button", { name: "打开后训练数据目录" }));
    await waitFor(() => expect(openPath).toHaveBeenCalledWith(OK_VERDICT.directory));
    // 主进程的拒绝码不直接给用户看：翻译成中文原因。
    expect(await screen.findByTestId("ai-trade-trajectory-action-error")).toHaveTextContent(
      "无法打开：目录不存在（可能已被移动或删除）",
    );
  });

  it("目录尚不存在（exists=false）⇒ 不给「打开」", () => {
    setBridge({ openPath: vi.fn() });
    renderCard({
      value: ON,
      capture: makeCapture({
        effectiveEnabled: true,
        verdict: { data: { ...OK_VERDICT, exists: false } },
      }),
    });
    expect(screen.queryByRole("button", { name: "打开后训练数据目录" })).toBeNull();
  });
});

describe("AiTradeTrajectoryFields · 校验行", () => {
  it("ok=false ⇒ role=alert + ⚠ 原因 +「改用默认目录」提交 directory:null", () => {
    const { onChange } = renderCard({
      value: { enabled: true, directory: "D:\\locked" },
      capture: makeCapture({ effectiveEnabled: true, verdict: { data: BAD_VERDICT } }),
    });
    const row = screen.getByTestId("ai-trade-trajectory-verdict");
    expect(row).toHaveAttribute("aria-live", "polite");
    const alert = within(row).getByRole("alert");
    expect(alert).toHaveTextContent("⚠ 目录不可写：拒绝访问（13）");
    fireEvent.click(within(alert).getByRole("button", { name: "改用默认目录" }));
    expect(onChange).toHaveBeenCalledWith({ enabled: true, directory: null });
  });

  it("默认目录本身不可用 ⇒ 只说原因，不给「改用默认目录」（点了还是它）", () => {
    renderCard({
      value: ON,
      capture: makeCapture({
        effectiveEnabled: true,
        verdict: { data: { ...BAD_VERDICT, isDefault: true } },
      }),
    });
    expect(screen.getByRole("alert")).toHaveTextContent("目录不可写");
    expect(screen.queryByRole("button", { name: "改用默认目录" })).toBeNull();
  });

  it("ok ⇒ ✓ 可写 · 可用 128.4 GB；可用空间读不到 ⇒ 省略，绝不显示 0 B", () => {
    renderCard({
      value: ON,
      capture: makeCapture({ effectiveEnabled: true, verdict: { data: OK_VERDICT } }),
    });
    const row = screen.getByTestId("ai-trade-trajectory-verdict");
    expect(row).toHaveTextContent("✓ 可写 · 可用 128.4 GB");
    cleanup();
    renderCard({
      value: ON,
      capture: makeCapture({
        effectiveEnabled: true,
        verdict: { data: { ...OK_VERDICT, freeBytes: null } },
      }),
    });
    const again = screen.getByTestId("ai-trade-trajectory-verdict");
    expect(again).toHaveTextContent("✓ 可写");
    expect(again.textContent).not.toContain("0 B");
  });

  it("校验在途 / 校验请求失败 ⇒ 一行说明，不出现 role=alert（不确定不翻译成否决）", () => {
    renderCard({ value: ON, capture: makeCapture({ effectiveEnabled: true, verdict: { isFetching: true } }) });
    expect(screen.getByTestId("ai-trade-trajectory-verdict")).toHaveTextContent("正在检查目录…");
    expect(screen.queryByRole("alert")).toBeNull();
    cleanup();
    renderCard({ value: ON, capture: makeCapture({ effectiveEnabled: true, verdict: { isError: true } }) });
    expect(screen.getByTestId("ai-trade-trajectory-verdict")).toHaveTextContent(
      "暂时无法检查该目录，启动时会再检查一次",
    );
    expect(screen.queryByRole("alert")).toBeNull();
  });
});

describe("AiTradeTrajectoryFields · 文案与可达性", () => {
  it("渲染文本不含 Markdown 强调符与反引号（含后端下发的原因）", () => {
    renderCard({
      value: { enabled: true, directory: "D:\\locked" },
      capture: makeCapture({
        effectiveEnabled: true,
        verdict: { data: { ...BAD_VERDICT, message: "**目录不可写**：`EACCES`" } },
      }),
    });
    const text = document.body.textContent ?? "";
    expect(text).not.toContain("**");
    expect(text).not.toContain("`");
    expect(text).toContain("目录不可写：EACCES");
  });

  it("每个按钮的可访问名都包含它的可见文字（WCAG 2.5.3 Label in Name）", () => {
    setBridge({ pickDirectory: vi.fn(), openPath: vi.fn() });
    renderCard({
      value: { enabled: true, directory: "D:\\locked" },
      capture: makeCapture({ effectiveEnabled: true, verdict: { data: BAD_VERDICT } }),
    });
    const buttons = screen.getAllByRole("button");
    // 选择文件夹… / 恢复默认 / 打开 / 改用默认目录：少一个说明夹具没把分支走全，这条就是空转。
    expect(buttons).toHaveLength(4);
    for (const button of buttons) {
      const visible = (button.textContent ?? "").trim();
      const name = button.getAttribute("aria-label") ?? visible;
      expect(name, `按钮「${visible}」`).toContain(visible);
    }
  });
});
