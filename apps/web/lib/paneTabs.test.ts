import { describe, expect, it } from "vitest";
import { selectActivePane } from "./paneTabs";

describe("selectActivePane", () => {
  it("returns_null_when_none_enabled", () => {
    expect(selectActivePane([], "macd")).toBeNull();
    expect(selectActivePane([], null)).toBeNull();
  });

  it("returns_paneTab_when_still_enabled", () => {
    expect(selectActivePane(["macd", "rsi"], "rsi")).toBe("rsi");
  });

  it("falls_back_to_first_when_paneTab_disabled", () => {
    expect(selectActivePane(["rsi", "kdj"], "macd")).toBe("rsi");
  });

  it("falls_back_to_first_when_paneTab_null", () => {
    expect(selectActivePane(["macd", "vol"], null)).toBe("macd");
  });

  it("falls_back_to_first_when_paneTab_non_pane", () => {
    // 持久化里残留的非 pane 键（如主图叠加 ma）不在启用集合中，回落首个启用项（健壮性）。
    expect(selectActivePane(["macd", "vol"], "ma")).toBe("macd");
  });
});
