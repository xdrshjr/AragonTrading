import { beforeEach, describe, expect, it } from "vitest";
import { DEFAULT_SIM_BALANCE, useAppStore } from "./store";

describe("app store — account mode & sim balance", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it("setSimBalance clamps to [100, 1e8]", () => {
    useAppStore.getState().setSimBalance(-5);
    expect(useAppStore.getState().simBalance).toBe(100);
    useAppStore.getState().setSimBalance(1e12);
    expect(useAppStore.getState().simBalance).toBe(1e8);
    useAppStore.getState().setSimBalance(50000);
    expect(useAppStore.getState().simBalance).toBe(50000);
  });

  it("setSimBalance ignores non-finite input", () => {
    useAppStore.getState().setSimBalance(50000);
    useAppStore.getState().setSimBalance(Number.NaN);
    expect(useAppStore.getState().simBalance).toBe(50000);
    useAppStore.getState().setSimBalance(Number.POSITIVE_INFINITY);
    expect(useAppStore.getState().simBalance).toBe(50000);
  });

  it("setAccountMode writes only the account mode field", () => {
    useAppStore.getState().setAccountMode("live");
    expect(useAppStore.getState().accountMode).toBe("live");
    useAppStore.getState().setAccountMode("sim");
    expect(useAppStore.getState().accountMode).toBe("sim");
  });

  it("migrates v4 persisted state to v5 with account defaults", async () => {
    localStorage.setItem(
      "aragon-ui",
      JSON.stringify({
        state: { instId: "ETH-USDT-SWAP", bar: "4H", theme: "dark" },
        version: 4,
      }),
    );
    await useAppStore.persist.rehydrate();
    const s = useAppStore.getState();
    expect(s.accountMode).toBe("sim");
    expect(s.simBalance).toBe(DEFAULT_SIM_BALANCE);
    // 既有持久化字段仍恢复
    expect(s.instId).toBe("ETH-USDT-SWAP");
    expect(s.bar).toBe("4H");
  });
});
