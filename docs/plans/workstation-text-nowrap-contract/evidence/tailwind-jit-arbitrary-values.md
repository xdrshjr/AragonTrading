# 证据 · Tailwind JIT 真的生成了本轮的全部任意值类（K10 / T9 / T9b / G4 / G6）

> 实施节点（Subtask #2）产出。**这不是 R1–R14 的人工验收截图**——那一轮是 §9.3 定义的终审门，
> 需要 dev server + 真实拖拽（R4 / R14 的判据是「跟手 / 不抖动 / 不把中栏挤破 240px 地板」，
> 无头截图证不了），留给终审节点。本文件只回答 §10 风险表 **K10** 那一条：
> 「Tailwind 任意值写法出错 / JIT 未生成」——这是 jsdom 用例**无法**察觉的一类失效
> （T9 / T9b 断言的是**编译前**的类名字符串，类名写对了但 CSS 没生成，用例照样全绿而页面照样折行）。

## 复现命令

```bash
cd apps/web
npx tailwindcss -c tailwind.config.ts -i app/globals.css -o /tmp/tw.css
```

`Done in 1806ms.`（tailwindcss ^3.4.13，`tailwind.config.ts` 的 `plugins: []`，未装任何容器查询插件——N2 的前提成立。）

## 结论：9 个任意值 + 2 个容器自适应网格，全部生成

容器自适应网格（§4.5.4 `PositionPanel` / §4.5.6 `TradeExplainDetail`，**替代被删掉的 `sm:` / `lg:` 视口断点**）：

```css
.grid-cols-\[repeat\(auto-fit\2c minmax\(120px\2c 1fr\)\)\] {
  grid-template-columns: repeat(auto-fit,minmax(120px,1fr));
}
.grid-cols-\[repeat\(auto-fit\2c minmax\(140px\2c 1fr\)\)\] {
  grid-template-columns: repeat(auto-fit,minmax(140px,1fr));
}
```

⇒ K10 的回退方案（把它写进 `globals.css` 的具名 utility）**不需要启用**。

九张表的列宽地板与自动交易头行的最小行高，逐个 `grep -cF` 均为 1：

| 类名 | 出处 | 生成 |
|---|---|---|
| `min-w-[620px]` | `Strategy/TradesTable.tsx` | ✓ |
| `min-w-[560px]` | `Strategy/PaperPositionsTable.tsx` / `WalkForwardResults.tsx` / `CompareView.tsx` | ✓ |
| `min-w-[520px]` | `Live/LiveFillsTable.tsx` | ✓ |
| `min-w-[640px]` | `Live/LivePositionsTable.tsx` | ✓ |
| `min-w-[760px]` | `Live/LiveOpenOrdersTable.tsx` | ✓ |
| `min-w-[420px]` | `Strategy/RunHistory.tsx` | ✓ |
| `min-w-[360px]` | `Strategy/ComboResult.tsx` | ✓ |
| `min-w-[240px]` | `Chart/ChartWorkspace.tsx` 中栏地板（轴 E-2 / G6） | ✓ |
| `min-h-[24px]` | `Chart/AutoTradePanel.tsx` 头行（T1 / T2） | ✓ |

`whitespace-nowrap` 与 `whitespace-normal` 两条规则也都在产物里（各 1 处定义，被 19 个锚点与
14 处 Type D 共用——这正是「一次继承覆盖一棵子树」的成本模型）。

## 仍待终审节点执行的

`docs/plans/workstation-text-nowrap-contract/spec.md` §9.3 的 **R1–R14**，截图归档回本目录。
其中 **R14**（过约束档位的拖拽死区）与 **C4** 绑定：若看到的不是「不跟手但稳定」而是
「抖动 / 闪烁 / 中栏被挤破 240px 地板」，按 C4 单独回滚轴 E 的三行 + G6，其余四轴照常合入。
