import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { useState } from "react";
import { afterEach, describe, expect, it } from "vitest";
import type { FactorMember } from "@/lib/factorTypes";
import ManualComboBuilder from "./ManualComboBuilder";

const M = (key: string): FactorMember => ({ factorKey: key, params: {}, weight: 0, direction: "long" });

// 受控壳：托管 pool + value，暴露「删除首个池因子」以驱动 P1-2 池编辑失真防护。
function Harness({ initialPool }: { initialPool: FactorMember[] }) {
  const [pool, setPool] = useState(initialPool);
  const [value, setValue] = useState<number[][]>([]);
  return (
    <div>
      <button onClick={() => setPool((p) => p.filter((_, i) => i !== 0))}>del-first-pool</button>
      <ManualComboBuilder pool={pool} labelOf={(m) => m.factorKey} value={value} onChange={setValue} />
      <div data-testid="value">{JSON.stringify(value)}</div>
    </div>
  );
}

const val = () => screen.getByTestId("value").textContent;

describe("ManualComboBuilder", () => {
  afterEach(cleanup);

  it("新增组合行、勾选筹码输出下标数组，删除行还原", () => {
    render(<Harness initialPool={[M("a"), M("b"), M("c")]} />);
    fireEvent.click(screen.getByRole("button", { name: "+ 新增组合" }));
    fireEvent.click(screen.getByRole("button", { name: "a" }));
    fireEvent.click(screen.getByRole("button", { name: "b" }));
    expect(val()).toBe("[[0,1]]");
    fireEvent.click(screen.getByRole("button", { name: "删除本行" }));
    expect(val()).toBe("[]");
  });

  it("空行不进入 onChange 产出", () => {
    render(<Harness initialPool={[M("a"), M("b")]} />);
    fireEvent.click(screen.getByRole("button", { name: "+ 新增组合" }));
    expect(screen.getByTestId("combo-row")).toBeTruthy();
    expect(val()).toBe("[]"); // 空行不产出
  });

  it("成员集合重复的行给出提示", () => {
    render(<Harness initialPool={[M("a"), M("b")]} />);
    fireEvent.click(screen.getByRole("button", { name: "+ 新增组合" }));
    fireEvent.click(screen.getByRole("button", { name: "+ 新增组合" }));
    const rows = screen.getAllByTestId("combo-row");
    for (const row of rows) {
      fireEvent.click(within(row).getByRole("button", { name: "a" }));
      fireEvent.click(within(row).getByRole("button", { name: "b" }));
    }
    expect(val()).toBe("[[0,1],[0,1]]");
    expect(screen.getAllByText("与另一组合成员相同，将被自动去重。").length).toBeGreaterThan(0);
  });

  it("删除被组合引用的低位池因子后按 memberIdentity 重解、剔除失真项并提示（P1-2）", () => {
    render(<Harness initialPool={[M("a"), M("b"), M("c")]} />);
    fireEvent.click(screen.getByRole("button", { name: "+ 新增组合" }));
    fireEvent.click(screen.getByRole("button", { name: "a" })); // 下标 0
    fireEvent.click(screen.getByRole("button", { name: "c" })); // 下标 2
    expect(val()).toBe("[[0,2]]");

    fireEvent.click(screen.getByRole("button", { name: "del-first-pool" })); // 删 a → 池变 [b,c]
    // c 重解为下标 1（非静默保留 [0,2] 而指向 b / 越界）；a 消失被显式剔除。
    expect(val()).toBe("[[1]]");
    expect(screen.getByText(/已因因子池变更移除 1 项/)).toBeTruthy();
  });
});
