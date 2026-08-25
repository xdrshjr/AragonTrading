import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import ExperiencePrunePrompt from "./ExperiencePrunePrompt";

// 「清理沉降经验」的二次确认（W-F / A-34）。
//
// **绝不静默批删**：先跑一次 dryRun 把条数拿回来、逐条列出跳过了什么，用户确认之后才真删。
// 它必须与 J-4 同批交付——那条修法会让一批「语境未知」的旧桶在下一次重建中不再出现 ⇒ 被判
// 为沉降。那是**正确的终局**，但不给清理入口，用户看到的就是一次「数据坏了」。

afterEach(cleanup);

describe("ExperiencePrunePrompt", () => {
  it("还没有 dryRun 结果 ⇒ 什么都不渲染（绝不先弹一个数字未知的删除框）", () => {
    const { container } = render(
      <ExperiencePrunePrompt preview={null} onCancel={vi.fn()} onConfirm={vi.fn()} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("逐条说清楚将删多少、跳过了什么，且确认按钮上带着条数", () => {
    render(<ExperiencePrunePrompt
      preview={{ matched: 12, deleted: 0, skippedPinned: 3, skippedUserMuted: 2 }}
      onCancel={vi.fn()} onConfirm={vi.fn()} />);
    expect(screen.getByRole("dialog")).toHaveTextContent("将永久删除 12 条");
    expect(screen.getByRole("dialog")).toHaveTextContent("置顶 3 条");
    expect(screen.getByRole("dialog")).toHaveTextContent("你手动静音的 2 条");
    expect(screen.getByRole("button", { name: "永久删除 12 条" })).toBeVisible();
  });

  it("ConfirmDialog 的 message 是**纯文本**：不许出现 markdown 记法（本仓库已复发两次）", () => {
    render(<ExperiencePrunePrompt
      preview={{ matched: 4, deleted: 0, skippedPinned: 0, skippedUserMuted: 0 }}
      onCancel={vi.fn()} onConfirm={vi.fn()} />);
    const text = screen.getByRole("dialog").textContent ?? "";
    expect(text).not.toMatch(/\*\*|`/);
  });

  it("一条都没有 ⇒ 把「没有可清理的」说出来，且确认键退化成「知道了」（不是一次删除）", () => {
    const onConfirm = vi.fn();
    const onCancel = vi.fn();
    render(<ExperiencePrunePrompt
      preview={{ matched: 0, deleted: 0, skippedPinned: 1, skippedUserMuted: 0 }}
      onCancel={onCancel} onConfirm={onConfirm} />);
    expect(screen.getByRole("dialog")).toHaveTextContent("没有可清理的沉降经验");
    fireEvent.click(screen.getByRole("button", { name: "知道了" }));
    expect(onConfirm).not.toHaveBeenCalled();
    expect(onCancel).toHaveBeenCalled();
  });

  it("确认之后才真的调 onConfirm（默认焦点在取消上——不可逆动作不该是默认答案）", () => {
    const onConfirm = vi.fn();
    render(<ExperiencePrunePrompt
      preview={{ matched: 7, deleted: 0, skippedPinned: 0, skippedUserMuted: 0 }}
      onCancel={vi.fn()} onConfirm={onConfirm} />);
    fireEvent.click(screen.getByRole("button", { name: "永久删除 7 条" }));
    expect(onConfirm).toHaveBeenCalledTimes(1);
  });
});
