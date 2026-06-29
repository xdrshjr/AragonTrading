import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { autoTradeSettingsApi } from "@/lib/autoTradeSettingsApi";
import AutoTradeSettingsSection from "./AutoTradeSettingsSection";

vi.mock("@/lib/autoTradeSettingsApi", () => ({
  autoTradeSettingsApi: { get: vi.fn(), update: vi.fn() },
}));

function renderSection() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <AutoTradeSettingsSection />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(autoTradeSettingsApi.get).mockResolvedValue({ maxBudgetPct: 100, effectiveMaxBudgetPct: 100 });
  vi.mocked(autoTradeSettingsApi.update).mockResolvedValue({ maxBudgetPct: 30, effectiveMaxBudgetPct: 30 });
});
afterEach(cleanup);

describe("AutoTradeSettingsSection", () => {
  it("renders the current max budget pct (default 100) and keeps save disabled when unchanged", async () => {
    renderSection();
    const input = (await screen.findByLabelText("总预算上限百分比")) as HTMLInputElement;
    expect(input.value).toBe("100");
    expect((screen.getByRole("button", { name: "保存" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("rejects out-of-range input and disables save", async () => {
    renderSection();
    const input = await screen.findByLabelText("总预算上限百分比");
    fireEvent.change(input, { target: { value: "200" } });
    expect(screen.getByText(/\(0, 100\]/)).toBeTruthy();
    expect((screen.getByRole("button", { name: "保存" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("saves a valid change via PUT", async () => {
    renderSection();
    const input = await screen.findByLabelText("总预算上限百分比");
    fireEvent.change(input, { target: { value: "30" } });
    fireEvent.click(screen.getByRole("button", { name: "保存" }));
    await waitFor(() => expect(autoTradeSettingsApi.update).toHaveBeenCalledWith(30));
  });
});
