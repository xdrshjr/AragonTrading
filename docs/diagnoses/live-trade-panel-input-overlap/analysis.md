# 实盘下单面板输入框挤压前置控件 - 问题定位与分析

- **Bug slug**: `live-trade-panel-input-overlap`
- **诊断节点**: Subtask #0, 问题定位与分析
- **日期**: 2026-06-28
- **结论一句话**: 当前重叠不是上一轮已修的 range thumb 溢出，而是共享 `Input`
  原语默认带 `w-full`，在 flex 行内覆盖调用方的 `w-16` / `w-20` 预期固定宽度，
  使输入框以 100% 宽度参与 flex 布局，把前面的杠杆档位组和仓位滑块压到近乎
  0 宽。

## Problem description

用户反馈实盘模式下「买多 · 开多 / 买空 · 开空」下单面板仍未修好：

- 「杠杆倍率」行最前方的倍率选择被后方输入框挤压，控件叠在一起。
- 下方「仓位数量」行有同样问题，滑块/前置控制被数量输入框挤压重叠。

附件截图 `.agentmesh/attachments/paste-1782585588200.png` 可见两处现象：

- 杠杆行左侧档位选择区域被压到只剩一小段，数值输入框横向占据几乎整行。
- 仓位数量行左侧滑块只露出 thumb 附近一小段，右侧输入框占据几乎整行。

上一轮提交 `5e57e434b74789393619001fbae6d2f7de3356de` 已处理旧诊断
`live-long-short-order-panel-layout` 的展示层问题，包括给 `.range-slider`
补 thumb 几何、加分区线、把数量错误下放给 `QuantitySlider`。本轮问题发生在该提交之后，
需要在既有成果上继续定位，不回滚上一轮改动。

## Reproduction steps

### 真实界面复现路径

1. 在仓库根目录启动应用：

   ```powershell
   pnpm dev
   ```

2. 打开 `http://localhost:18761`。
3. 配置可用的默认 OKX 实盘账户，或使用已有可进入实盘模式的本地状态。
4. 顶部账户模式切到「实盘」。
5. 在图表下方左侧下单面板观察：
   - 「杠杆倍率」行，`1x/3x/5x/10x/20x` 档位组被右侧数值输入框挤压。
   - 「仓位数量」行，百分比滑块被右侧数量输入框挤压。

该路径需要实盘账户状态，成本较高。下面的最小复现只复现 CSS/flex 失效机制，不需要
后端或 OKX 账户。

### 最小复现

已创建复现文件：

- `docs/diagnoses/live-trade-panel-input-overlap/flex-overlap-repro.html`
- `docs/diagnoses/live-trade-panel-input-overlap/flex-overlap-repro.png`

运行：

```powershell
& 'C:/Program Files/Google/Chrome/Application/chrome.exe' --headless=new --disable-gpu --virtual-time-budget=1000 --dump-dom 'file:///M:/takoAI/AragonTrading/docs/diagnoses/live-trade-panel-input-overlap/flex-overlap-repro.html'
```

关键测量结果：

```json
{
  "brokenLeverageRow": 420,
  "brokenPresets": 16,
  "brokenLeverageInput": 380,
  "brokenQuantityRow": 420,
  "brokenSlider": 16,
  "brokenQuantityInput": 376,
  "fixedLeverageRow": 420,
  "fixedPresets": 332,
  "fixedWrapper": 64,
  "fixedInput": 64
}
```

解释：

- 420px 容器中，故障杠杆档位组只剩 16px，输入框占 380px。
- 故障仓位滑块只剩 16px，输入框占 376px。
- 使用固定宽度 wrapper 后，前置区域恢复到 332px，输入框保持 64px。

## Root cause analysis

### 触发范围

实盘模式才会渲染该下单面板：

- `apps/web/components/Chart/ChartWorkspace.tsx:305-307` 在 `accountMode === "live"`
  时渲染 `<LiveBottomPanel />`。
- `apps/web/components/Live/LiveBottomPanel.tsx:33` 定义实盘底部左右分栏。
- `apps/web/components/Live/LiveBottomPanel.tsx:34` 左侧下单栏宽度为
  `w-2/5 min-w-[300px] max-w-[460px] shrink-0`。
- `apps/web/components/Live/LiveBottomPanel.tsx:35` 左侧栏渲染 `<LiveTradePanel />`。

因此该问题影响的是实盘底部面板左侧下单栏，尤其在 300-460px 的受限宽度内更明显。

### 代码证据

共享 `Input` 原语默认带 `w-full`：

```tsx
// apps/web/components/ui/index.tsx:62-78
export function Input({ className, invalid, ...props }) {
  return (
    <input
      className={clsx(
        "w-full rounded-[10px] border bg-[var(--surface)] px-3 py-2 text-sm text-[var(--text)]",
        ...
        className,
      )}
      {...props}
    />
  );
}
```

杠杆行希望右侧输入框只有固定宽度 `w-16`，并让左侧档位组 `flex-1` 吃掉剩余空间：

```tsx
// apps/web/components/Live/LeverageControl.tsx:32-62
<div className="flex items-center gap-1.5">
  <div className="grid flex-1 grid-cols-5 gap-1">...</div>
  <Input ... className="w-16 text-right tabular" />
</div>
```

仓位数量行同样希望左侧滑块 `flex-1`，右侧输入框固定 `w-20`：

```tsx
// apps/web/components/Live/QuantitySlider.tsx:61-82
<div className="flex items-center gap-2">
  <Slider ... className="flex-1" />
  <Input ... className="w-20 text-right tabular" />
</div>
```

但当前已编译 Tailwind CSS 中，`.w-full` 排在 `.w-16` / `.w-20` 之后：

```json
{
  "w16": 7142,
  "w20": 7216,
  "wFull": 7581,
  "flex1": 8214
}
```

来源：`apps/web/.next/static/css/480de8d1df96ec60.css:3`。

这些 utility specificity 相同，后出现的 `.w-full{width:100%}` 胜出。结果是：

- `Input` 实际主轴基准尺寸接近整行宽度，而不是 64px / 80px。
- 前面的 `flex-1` 档位组或滑块基准为 0，在负剩余空间分配后被压到极窄。
- 视觉上表现为“后方输入框挤压前方倍率选择 / 仓位数量控件并重叠”。

上一轮提交 `5e57e43` 的 `git show --stat` 证实它主要处理：

- `.range-slider` thumb 与端点内缩。
- `LiveTradePanel` 分区线。
- `QuantitySlider` 错误展示位置。
- `TpSlFields` 视觉收敛。

它没有消除 `Input` 的 `w-full` 与调用方 `w-16/w-20` 的宽度冲突，所以该问题会在上一轮修复后继续存在。

### 评审核查补充

- **执行路径成立**: `ChartWorkspace.tsx:305-307` 的 live 分支会直接进入
  `LiveBottomPanel`，`LiveBottomPanel.tsx:33-35` 再把左栏渲染为 `LiveTradePanel`。
  `LiveTradePanel.tsx:263-280` 在同一表单流内连续渲染 `LeverageControl` 和
  `QuantitySlider`，因此本报告引用的 `LeverageControl.tsx:32-62` 与
  `QuantitySlider.tsx:61-82` 确实处在用户截图对应的失败路径上。
- **备选解释已排除**: 如果只是上一轮 `.range-slider` thumb 端点溢出，现象应主要局限在
  数量滑块端点；但截图中杠杆倍率的按钮组也被右侧输入框横向覆盖。杠杆行不使用
  `Slider` / `.range-slider`，却同样复现，因此旧 thumb 几何不是本轮共同根因。
- **共同触发条件一致**: 全仓/逐仓、限价/市价和订单方向不会改变这两行的布局结构。
  失败所需条件是“同一个 flex 行内，前置控件为 `flex-1`，右侧 `Input` 同时带共享
  `w-full` 和调用方固定宽度类”。仓库内固定宽度 `Input` 搜索只命中
  `LeverageControl.tsx:61` 与 `QuantitySlider.tsx:81`，与用户反馈的两处重叠一一对应。
- **CSS 证据可复核**: 当前 `apps/web/.next/static/css/480de8d1df96ec60.css` 中
  `.w-16{`、`.w-20{`、`.w-full{`、`.flex-1{` 的字符位置分别为
  `7142`、`7216`、`7581`、`8214`。同等 specificity 下，较晚的
  `.w-full{width:100%}` 覆盖较早的固定宽度类，解释了 420px 复现容器中输入框占
  376-380px、前置控件只剩 16px 的测量结果。
- **错误路径与并发边界**: 这是纯展示层 flex 尺寸冲突；候选修复不改变 `onChange`、
  `disabled`、`invalid`、数值夹取、下单确认或网络请求路径，因此不存在并发或后端错误路径的新增风险。

## Impact surface

- **受影响功能**: 仅实盘账户模式下的底部左侧下单面板。
- **受影响用户**: 能进入实盘模式、使用买多/买空下单面板的用户。
- **风险等级**: 高于普通展示问题。该界面是实盘下单入口，布局重叠会降低参数可读性与可操作性，增加误判杠杆/数量输入的风险。
- **不受影响路径**: 回测、模拟盘、策略面板、实盘右侧持仓/成交表不直接使用该横向输入布局。
- **共享组件风险**: 根因位于共享 `Input` 的默认 `w-full`，但本轮实际故障只由
  `LeverageControl` 和 `QuantitySlider` 中的“固定宽度输入 + 前置 flex 控件”组合触发。

## Candidate fixes

| 方案 | 做法 | 侵入性 | 风险 | 工作量 | 评价 |
|---|---|---:|---:|---:|---|
| F1: 固定宽度 wrapper | 在 `LeverageControl` / `QuantitySlider` 中用 `div.w-16.shrink-0`、`div.w-20.shrink-0` 包住 `Input`，让 `Input` 的 `w-full` 只填满 wrapper；前置 `flex-1` 控件补 `min-w-0` | 低 | 低 | 小 | 最小修复，保持共享 `Input` “默认铺满父容器”的既有语义 |
| F2: 给 `Input` 增加宽度模式 | 为 `Input` 增加 `fullWidth?: boolean` 或类似 prop，默认 true；这两处传 false 后使用 `w-16/w-20 shrink-0` | 中 | 中 | 中 | API 更清晰，但会触碰共享原语接口，需要确认所有调用点语义 |
| F3: 重要级工具类覆盖 | 在两处改成 `className="!w-16 shrink-0 ..."` / `!w-20` | 低 | 中 | 小 | 快速但偏脆，依赖 `!important` 压制共享原语，不利于后续维护 |

## Recommended fix

推荐 **F1: 固定宽度 wrapper**。

理由：

1. **直接命中根因**: 问题不是 `Input` 作为表单原语默认 `w-full` 不合理，而是它被放进了需要固定主轴宽度的 flex 行。wrapper 把“外部固定宽度”和“内部输入框铺满”两个语义分开。
2. **改动面最小**: 只需改 `LeverageControl.tsx` 和 `QuantitySlider.tsx` 的展示结构，不碰 `Input` 公共 API、不碰下单逻辑、不碰 `liveOrder.ts`。
3. **已由最小复现验证**: `flex-overlap-repro.html` 中同样的 wrapper 方案让 420px 行内前置区域恢复到 332px，输入框稳定为 64px。
4. **兼容上一轮成果**: 保留 `.range-slider` thumb 修复、分区线、错误归位等已完成工作，不回滚也不重写上一轮展示层改动。

建议落地边界：

- `LeverageControl.tsx`: 将右侧 `Input` 包在 `div className="w-16 shrink-0"` 内，`Input` 去掉局部 `w-16`，保留 `text-right tabular`。
- `QuantitySlider.tsx`: 将右侧 `Input` 包在 `div className="w-20 shrink-0"` 内，`Input` 去掉局部 `w-20`，保留 `text-right tabular`。
- 前置控件容器补 `min-w-0`，避免极窄场景下内容最小宽度再次顶破布局。
- 不修改 `lib/liveOrder.ts`、`tradingApi`、确认弹窗或实盘下单安全护栏。

## 评审结论

结论：原分析的根因成立，最终推荐 **F1: 固定宽度 wrapper**，并限定为只修改
`apps/web/components/Live/LeverageControl.tsx` 与
`apps/web/components/Live/QuantitySlider.tsx` 两个展示组件。

推荐落地方式：

- 杠杆行：把右侧 `Input` 包入 `div className="w-16 shrink-0"`，`Input` 自身只保留
  `text-right tabular` 等非宽度类；左侧档位容器由 `grid flex-1 grid-cols-5 gap-1`
  调整为 `grid min-w-0 flex-1 grid-cols-5 gap-1`。
- 仓位数量行：把右侧 `Input` 包入 `div className="w-20 shrink-0"`，`Input` 自身只保留
  `text-right tabular`；`Slider` 调整为 `className="min-w-0 flex-1"`。

理由：该方案直接切断 `Input` 默认 `w-full` 与调用方固定宽度类在同一 DOM 节点上竞争的问题，
同时保留共享 `Input` “铺满父容器”的既有语义。它不触碰下单数据流、校验逻辑、确认弹窗、
API 调用或上一轮 `.range-slider` 修复，回归面最小。F2 需要变更共享组件 API 并重新审计所有调用点，
超出本轮 bug 的最小修复范围；F3 依赖 `!important` 压制工具类，虽然可快速止血，但会把
样式优先级债务留在两个真实下单控件上，不应作为最终修复。
