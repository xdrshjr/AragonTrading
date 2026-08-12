import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { OkxConnectivity, OkxLinkChannel } from "@/lib/okxLinkTypes";
import ConnectionStatus from "./ConnectionStatus";

const NOW = 1_786_000_000;

function channel(over: Partial<OkxLinkChannel> = {}): OkxLinkChannel {
  return {
    key: "rest:public",
    kind: "restPublic",
    label: "行情接口(REST)",
    state: "up",
    lastOkTs: NOW - 3,
    lastErrorTs: null,
    lastErrorCode: null,
    lastErrorMessage: null,
    latencyMs: 28,
    consecutiveFailures: 0,
    reconnects1h: 0,
    resubscribes1h: 0,
    gapRepairs1h: 0,
    lastMessageTs: null,
    circuit: "closed",
    circuitOpenUntilTs: null,
    ...over,
  };
}

function snapshot(over: Partial<OkxConnectivity> = {}): OkxConnectivity {
  return {
    ts: NOW,
    overall: "up",
    accountState: "unknown",
    summary: "OKX 链路正常",
    clockOffsetMs: 0,
    clockSyncedTs: NOW - 60,
    channels: [channel()],
    ...over,
  };
}

afterEach(() => cleanup());

describe("顶栏芯片", () => {
  it("文案随 overall 变", () => {
    const { rerender } = render(<ConnectionStatus connectivity={snapshot()} okxUp={true} />);
    expect(screen.getByRole("button", { name: /OKX 已连接/ })).toBeTruthy();

    rerender(
      <ConnectionStatus connectivity={snapshot({ overall: "degraded" })} okxUp={true} />,
    );
    expect(screen.getByRole("button", { name: /连接不稳/ })).toBeTruthy();

    rerender(<ConnectionStatus connectivity={snapshot({ overall: "down" })} okxUp={false} />);
    expect(screen.getByRole("button", { name: /OKX 不可达/ })).toBeTruthy();
  });

  it("账户面异常只出现在面板行，芯片仍是「已连接」", () => {
    render(
      <ConnectionStatus
        connectivity={snapshot({ overall: "up", accountState: "down" })}
        okxUp={true}
      />,
    );
    const trigger = screen.getByRole("button", { name: /OKX 已连接/ });
    fireEvent.click(trigger);

    expect(screen.getByText("账户接口 已断开")).toBeTruthy();
    expect(screen.getByRole("button", { name: /OKX 已连接/ })).toBeTruthy();
  });
});

describe("展开面板", () => {
  it("点击展开、Esc 关闭并把焦点还给触发按钮", async () => {
    render(<ConnectionStatus connectivity={snapshot()} okxUp={true} />);
    const trigger = screen.getByRole("button", { name: /OKX 已连接/ });
    expect(trigger.getAttribute("aria-expanded")).toBe("false");
    expect(trigger.getAttribute("aria-haspopup")).toBe("dialog");

    fireEvent.click(trigger);
    expect(screen.getByRole("dialog", { name: "OKX 连接状态" })).toBeTruthy();
    expect(trigger.getAttribute("aria-expanded")).toBe("true");

    fireEvent.keyDown(document, { key: "Escape" });

    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(document.activeElement).toBe(trigger);
  });

  it("点击外部关闭", async () => {
    render(
      <div>
        <span data-testid="outside">别处</span>
        <ConnectionStatus connectivity={snapshot()} okxUp={true} />
      </div>,
    );
    fireEvent.click(screen.getByRole("button", { name: /OKX 已连接/ }));
    expect(screen.getByRole("dialog")).toBeTruthy();

    fireEvent.mouseDown(screen.getByTestId("outside"));

    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });

  it("四条通道逐行渲染，含断了多久与最近错误", () => {
    render(
      <ConnectionStatus
        okxUp={true}
        connectivity={snapshot({
          overall: "degraded",
          accountState: "idle",
          channels: [
            channel(),
            channel({
              key: "ws:business:BTC-USDT-SWAP:1H:chart",
              kind: "wsBusiness",
              label: "实时流(K 线 · BTC-USDT-SWAP 1H)",
              state: "degraded",
              latencyMs: null,
              lastMessageTs: NOW - 3600,
              lastErrorTs: NOW - 30,
              lastErrorCode: "WS_PONG_TIMEOUT",
              lastErrorMessage: "心跳应答超时，已强制重连",
              reconnects1h: 3,
              gapRepairs1h: 2,
            }),
            channel({
              key: "ws:public:BTC-USDT-SWAP:1H:chart",
              kind: "wsPublic",
              label: "实时流(盘口 · BTC-USDT-SWAP)",
            }),
            channel({ key: "rest:private", kind: "restPrivate", label: "账户接口", state: "idle" }),
          ],
        })}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /连接不稳/ }));

    const dialog = screen.getByRole("dialog");
    expect(dialog.querySelectorAll("li")).toHaveLength(4);
    expect(screen.getByText("心跳应答超时，已强制重连")).toBeTruthy();
    expect(screen.getByText(/重连 3 次/)).toBeTruthy();
    expect(screen.getByText(/补齐 K 线 2 次/)).toBeTruthy();
  });

  it("时钟偏差大到会撞 50102 时如实说出来", () => {
    render(
      <ConnectionStatus connectivity={snapshot({ clockOffsetMs: 45_000 })} okxUp={true} />,
    );
    fireEvent.click(screen.getByRole("button", { name: /OKX 已连接/ }));
    expect(screen.getByText(/本机时间比交易所慢 45 秒/)).toBeTruthy();
  });
});

describe("重新探测", () => {
  it("点击调用 onProbe，期间按钮禁用", async () => {
    let resolve: (value: unknown) => void = () => {};
    const onProbe = vi.fn(() => new Promise((r) => (resolve = r)));
    render(<ConnectionStatus connectivity={snapshot()} okxUp={true} onProbe={onProbe} />);
    fireEvent.click(screen.getByRole("button", { name: /OKX 已连接/ }));

    const probeButton = screen.getByRole("button", { name: "重新探测" });
    fireEvent.click(probeButton);

    expect(onProbe).toHaveBeenCalledTimes(1);
    await waitFor(() => expect(screen.getByRole("button", { name: "探测中…" })).toBeTruthy());

    resolve(snapshot());
    await waitFor(() => expect(screen.getByRole("button", { name: "重新探测" })).toBeTruthy());
  });

  it("没给 onProbe 时不渲染那个按钮（不给一个点了没反应的入口）", () => {
    render(<ConnectionStatus connectivity={snapshot()} okxUp={true} />);
    fireEvent.click(screen.getByRole("button", { name: /OKX 已连接/ }));
    expect(screen.queryByRole("button", { name: "重新探测" })).toBeNull();
  });
});

describe("端点缺失的回落", () => {
  it("connectivity=null 时回落旧展示且不崩", () => {
    render(<ConnectionStatus connectivity={null} okxUp={true} latencyMs={28} lastOkTs={NOW} />);
    const trigger = screen.getByRole("button", { name: /OKX 已连接/ });
    expect(trigger.getAttribute("title")).toContain("时延 28ms");

    fireEvent.click(trigger);

    expect(screen.getByText("连接详情暂不可用（后端未提供该端点）")).toBeTruthy();
  });

  it("okxUp=null 时显示「探测中」", () => {
    render(<ConnectionStatus connectivity={null} okxUp={null} />);
    expect(screen.getByRole("button", { name: /探测中/ })).toBeTruthy();
  });
});

describe("面板开合回调", () => {
  it("展开与收起都通知外部（页面据它调整轮询频率）", () => {
    const onOpenChange = vi.fn();
    render(
      <ConnectionStatus connectivity={snapshot()} okxUp={true} onOpenChange={onOpenChange} />,
    );
    const trigger = screen.getByRole("button", { name: /OKX 已连接/ });

    fireEvent.click(trigger);
    fireEvent.click(trigger);

    expect(onOpenChange.mock.calls.map((c) => c[0])).toEqual([true, false]);
  });
});
