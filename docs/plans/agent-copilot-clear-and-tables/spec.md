# Agent Copilot — 上下文清除按钮 + Markdown 表格渲染 设计方案

> Feature slug: `agent-copilot-clear-and-tables`
> Version: **v2**（Subtask#1 评审完成；评审记录见下「评审记录」段，评审结论见文末）
> 适用迭代：第 2 轮增量需求（在已提交的 Agent Trading Copilot 之上继续修改）
> 所属基线：`docs/plans/agent-trading-copilot/spec.md` → `agent-copilot-hardening` → `agent-copilot-resilient-streaming`

---

## 评审记录（Subtask#1，v2）

> 评审人：Anthropic 工程团队资深评审。逐节对照**源码事实**核验，维度：可行性 / 完备性 / 一致性 / 合理规模。下列问题已就地修复（P0/P1 全部 close；P2 视成本就地修或留作下游注意）。

**源码事实核验（均已对齐，方案据此成立）：**

- `apps/web/lib/markdown.ts`：现状仅 `code/heading/ul/ol/p` 五种 `Block`，无 table；`isFence`/`renderInline`/`pushBlock`/`isBlockStart` 均在模块作用域、可被新表格分支直接复用；安全铁律（字符串子节点自动转义 + `isSafeHref` http(s) 白名单 + 无 `dangerouslySetInnerHTML`）成立。✔
- `AgentComposer.tsx` 现有 props 与发送/停止按钮 JSX 与方案描述一致；`@/components/ui` 的 `Button` 支持 `variant="ghost|danger|primary"` + `size="sm"`。✔
- `AgentPanel.tsx`：连接 effect 以 `conversationId` 为依赖，变更即 `setMessages([])` + 复位 ref + 重连；`convIdRef`（line 76）存在；`newConversation` 来自 store。✔
- `agentApi.clear(id)` → `DELETE /api/py/agent/conversations/{id}`；后端 `agent_clear`（api/agent.py:37）**无条件**返回 204（含空/不存在会话）。✔
- 后端 `AgentManager.clear`（manager.py:215）只做删行 + `_cancel_grace` + `_sessions.pop`，**不取消在跑的 run_task** → 「运行中禁用清除」是硬约束成立（否则孤儿 run 会把消息回写已删除会话、复活旧行）。✔
- `detach`（manager.py:117）对 `sess is None` 早返回 → 「清除 pop 会话」与「旧 WS 断连 detach」竞态在两种次序下均不崩、不误起宽限（依赖"非运行"不变量）。✔
- `AgentMessageBubble.MessageBody`（line 45）仅在助手**完成轮**调 `renderMarkdown`，流式途中纯文本 → 表格只在落定后成形，天然无半截闪烁。✔
- `apps/web/tsconfig.json` `target: ES2020`，运行时 Electron(Chromium) / Node(vitest) 均支持 ES2018 lookbehind。✔

| # | 维度 | 严重度 | 问题 | 处置 |
|---|------|--------|------|------|
| C1 | 合理规模 / 一致性 | **P1** | `AgentComposer` 的 `clearing?: boolean` 恒为常量 `false`（§4.1 注释亦称"可保留为常量 false"）。清除是**同步**操作（`newConversation()` 即时写 store、`agentApi.clear` 为 fire-and-forget），不存在异步"清除中"态，该 prop 是死接口，并把"是否引入 state"的歧义抛给下游开发节点。 | 就地修复：**删除 `clearing`**，清除按钮仅以 `running` 决定禁用（§2.1 / §3 / §4.1 / §6 已同步更新）。 |
| C2 | 完备性 | **P1** | 清除的"消息流即时清空"完全依赖 `newConversation()` 产出**新且非空**的会话 id 来触发 `conversationId`-keyed effect；但 `makeConversationId()`（store.ts:24）在缺少 `crypto.randomUUID` 时回退为 `""`，而连接 effect 对空 id 早返回（`if (!conversationId) return`），将使清除**静默失效**（消息不清、不重连）。方案此前未暴露这一承载性依赖。 | 就地修复：新增风险 **R11** + §2.1 显式登记该依赖与运行时前提（Electron 安全上下文恒提供 `randomUUID`，与既有「新对话」同源依赖、已随第 1 轮上线验证）；补验收注记。`makeConversationId` 的非空硬化属共享既有面，列为下游跟进项（本轮 out of scope）。 |
| C3 | 完备性 / 准确性 | P2 | §7.1 称"现有 7 条 markdown 用例"，实际 `markdown.test.ts` 为 **11** 条（rendering 5 + safety 5 + parseBlocks 1）。 | 就地订正为 11，回归基线改为"11 条全绿"。 |
| C4 | 完备性 | P2 | §7.1 测试清单未覆盖本方案新增的 `takeParagraph` 截断路径（紧贴段落、无空行的表格）。 | 新增用例 #8「段落后紧邻表格（无空行）被正确拆分为独立 `table` 块」。 |
| C5 | 完备性 / 可达性 | P2 | 表头 `<th>` 缺 `scope="col"`（a11y）。 | §6 `renderRow` 片段为 `th` 补 `scope="col"`。 |
| C6 | 一致性 | P2 | §6 开头声称"行长 < 80"，但 Tailwind className 串与既有 `markdown.ts`（如 line 13/155）本就超 80；强行折行 className 反损可读性。 | 放宽表述为"逻辑行长守阈值；长 className 串与既有 markdown.ts 一致，允许超 80"。 |
| C7 | 可行性 | P2 | R9 的 lookbehind 降级回退（`split("\|")`）会使转义竖线失效，从而 §7.1 用例 #5 失败。 | R9 补注：回退为最后手段，一旦启用须同步移除/调整用例 #5；目标运行时无需回退。 |
| C8 | 合理规模 | P2（留档，不改） | 「清除」与 header「新对话」语义重叠（清除是新对话的超集：换 id + purge 旧行）。 | 用户明确要求"发送键左侧的清除按钮"，R10 已用文案区分；保留双入口，不改。 |

---

## 1. Overview（概述）

第 1 轮已为 Aragon Trading 桌面端交付了完整的「Agent Trading Copilot」——右侧面板的多页签 AI 助手（`apps/web/components/Agent/*` + 后端 `services/quant/app/agent/*` + WS `/api/ws/agent/{convId}` + REST `/agent/*`）。第 2 轮是两个**纯增量、向后兼容**的体验修复，均聚焦前端，不触碰 Agent 运行内核（loop / manager / tools / LLM 客户端）、不改 DB schema、不引入新依赖。

**需求 1：一键清除上下文。** 当前对话区只有 header 右上角的「新对话」按钮（生成新的会话 id 后由既有 effect 重置），用户在输入框附近找不到清除入口，感知为「缺少上下文清除功能」。本轮在**底部输入条（composer）发送按钮的左侧**新增一个明确的「清除」按钮，单击即把当前会话上下文清空：既删除后端持久化的会话消息（复用已存在的 `DELETE /agent/conversations/{id}` → `AgentManager.clear`），又把前端消息流重置为空、并让 WS 重新挂载到一个全新的干净会话。语义上是「彻底清空本会话上下文、从零开始」，而非仅仅丢弃旧会话。

**需求 2：正确渲染 Markdown 表格。** 助手回复完成轮已走「无依赖安全 markdown 轻渲染器」`apps/web/lib/markdown.ts`（迭代 3 的 RS-8：转义后再格式化、绝不 `dangerouslySetInnerHTML`）。但该渲染器目前只识别代码块 / 标题 / 有序无序列表 / 段落，**没有表格块**——模型输出的 GFM 表格（`| 列 | 列 |` + `| --- | --- |` 分隔行）会落入段落分支，原样显示成一行行带竖线的纯文本，观感很差。本轮为渲染器补一个 `table` 块：用「下一行是分隔行」的前瞻判定来识别真表格（避免把含竖线的普通段落误判成表格），单元格复用既有 `renderInline`（继承转义与协议白名单的安全性），输出真正的 `<table>`，支持列对齐与窄面板横向滚动。

两项改动遵循 CLAUDE.md 的 Clean Code 阈值（单文件 < 800 行、单函数 < 50 行、圈复杂度 < 10、嵌套 < 4、参数 ≤ 5），并沿用既有命名/样式 token（`--border` / `--surface-2` / `--accent` / `--muted` 等）与组件（`@/components/ui` 的 `Button`）。

---

## 2. Technical Design（技术设计）

### 2.1 需求 1：一键清除上下文

**复用既有后端能力（零后端改动）。** 后端早已具备完整的清除链路，无需任何新增：

- REST：`DELETE /agent/conversations/{conversation_id}` → `services/quant/app/api/agent.py:agent_clear` → `AgentManager.clear`。
- `AgentManager.clear`（`services/quant/app/agent/manager.py:215`）做三件事：① `repo.clear_agent_messages` 删除该会话的全部 `AgentMessage` 行；② `_cancel_grace` 取消断连宽限计时器；③ 在 `_lock` 内 `self._sessions.pop(conversation_id, None)` 弹出内存会话。
- 前端 REST 客户端已封装：`apps/web/lib/agentApi.ts:agentApi.clear(conversationId)` → `DELETE /api/py/agent/conversations/{id}`。

**关键约束（来自源码事实，必须遵守）。** WS 处理器 `ws_agent`（`api/agent.py:43`）在**连接建立时只调用一次** `sess = await manager.attach(...)`，由 `attach`→`set_sink` 把发送闭包挂到该会话对象上。若只调用 `agentApi.clear()` 把会话从 `_sessions` 字典弹出、却不重连，则下一条 `user_message` 经 `manager.handle`→`get_or_create` 会**新建一个没有 sink 的会话**，其 run 事件只进环形缓冲、永不下发到当前 WS——前端会「发了消息却收不到回复」。因此**清除必须伴随一次 WS 重连**，让新会话在 `attach` 时正确挂上 sink。

**选定实现：清除 = 重新生成会话 id（`newConversation`）+ best-effort 清空旧会话行。** 理由：`AgentPanel` 的连接 effect 以 `conversationId` 为依赖，会话 id 一变即自动「拆旧 WS → `setMessages([])` / 复位所有 ref → 连新 WS → 收到空 history → attach 新会话（sink 正确）」。这条重置路径在迭代 1~3 已被充分使用与测试，复用它最稳、改动最小（**无需新增 store 状态、无需改 effect 依赖**）。同时对**旧** id 触发一次 `agentApi.clear(oldId)`（fire-and-forget），把被遗弃会话的持久化行也删掉，使「清除」名副其实（purge 而非仅 abandon），这也是与 header「新对话」的差异点（「新对话」只换 id、旧行残留）。

**「运行中禁用清除」是硬约束。** `manager.clear` 会在会话仍 `running` 时也弹出它，但**不**取消正在跑的 run_task；该孤儿 run 会继续把消息持久化回**旧** conversation_id，等于在 DELETE 之后又「复活」了已删除会话的行。为彻底规避此竞态，**清除按钮在 `running` 为真时禁用**（用户需先点「停止」）。非运行态 ⇒ 无在途 run ⇒ DELETE + 换 id 干净无副作用。

**不清空输入草稿。** 清除的是「会话上下文」，不应抹掉用户尚未发送的输入框草稿 `input`，故 `clearContext` 不动 `setInput`。

**无二次确认。** 需求明确「一个按钮点击，清除所有上下文」，故 v1 不加确认弹窗；按钮做成低调的 ghost 样式并在运行中禁用以降低误触代价（见 §8 风险）。

#### 清除时序（非运行态，点击「清除」）

```
用户点击「清除」(composer, 发送键左侧)
  └─ AgentPanel.clearContext()
       ├─ if (running) return            // 守卫（按钮亦 disabled）
       ├─ old = convIdRef.current
       ├─ newConversation()              // store: 生成新会话 id（zustand persist）
       │     └─(既有 effect on conversationId 变更)
       │          ├─ setMessages([]) / setRunning(false) / setError(null)
       │          ├─ 复位 lastSeqRef=0 / pendingGapReconcileRef=false / connectedRef=false
       │          ├─ 旧 ws.close()，新 agentWS(newId).connect()
       │          └─ 收 ready + 空 history → 面板回到空态（AgentEmptyState）
       └─ void agentApi.clear(old).catch(()=>{})   // best-effort：DELETE 旧行（housekeeping）
```

DELETE 失败（离线等）不影响清除效果：用户仍获得干净空会话，仅旧行残留（下次仍可清）。故清除按钮的可用性**只**与 `running` 绑定，与连接态无关（断线也能本地清空并尝试重连）。

**承载性依赖（评审 C2 / R11）。** 上述「换 id → effect 重置/重连」的清空效果，依赖 `newConversation()` 经 `makeConversationId()`（`store.ts:24`）产出一个**新且非空**的会话 id：连接 effect 对空 id 早返回（`if (!conversationId) return`），若 id 退化为 `""` 则清除会**静默失效**（消息不清、不重连）。`makeConversationId` 现状为「有 `crypto.randomUUID` 则用之，否则返回 `""`」；目标运行时为 **Electron 渲染进程（localhost/file 安全上下文）恒提供 `crypto.randomUUID`**，且既有 header「新对话」按钮已走同一函数并随第 1 轮上线验证，故本轮**复用既有已验证路径、不新增硬化**。若未来支持非安全上下文运行，须先为 `makeConversationId` 增加非空回退（如时间戳 + 随机），此项列为下游跟进（本轮 out of scope）。

### 2.2 需求 2：Markdown 表格渲染（`apps/web/lib/markdown.ts` 增量）

在既有「块级扫描 → 逐块渲染」管线上**新增一个 `table` 块**，不动现有 code/heading/list/paragraph 任何分支的语义。

**识别（前瞻判定，杜绝误判）。** GFM 表格 = 表头行 + 紧随的分隔行（`---`，可带 `:` 表对齐）+ 0..N 数据行。关键：一行含竖线 `|` 不足以判定为表格（普通文字也可能含 `|`）。真正的信号是**下一行是分隔行**：

- `isTableRow(line)`：`line.includes("|")`。
- `isTableDelimiter(line)`：`splitRow` 后每个单元格都匹配 `/^\s*:?-{1,}:?\s*$/` 且至少 1 列。
- `isTableStart(lines, i)`：`i+1 < len && isTableRow(lines[i]) && isTableDelimiter(lines[i+1])`。

**解析。** `takeTable(lines, start)`：表头 = `splitRow(lines[start])`；对齐 = `splitRow(lines[start+1]).map(parseAlign)`；数据行从 `start+2` 起，循环条件 `非空 && isTableRow && !isFence`，每行 `splitRow` 后 `normalizeRow` 到表头列数（少补 `""`、多截断，对齐 GFM 容错）。`splitRow` 去掉外侧一对竖线后按**未转义**竖线切分并把 `\|` 反转义为 `|`、逐格 `trim`。`parseAlign`：`:--:`→center、`--:`→right、`:--`→left、否则 `null`。

**接入扫描。** `parseBlocks` 主循环在「ol 分支后、paragraph 兜底前」插入 `else if (isTableStart(lines, i)) { [,i]=pushBlock(blocks, takeTable(lines,i)); }`；并把 `takeParagraph` 的 while 条件追加 `&& !isTableStart(lines, i)`，使紧贴段落（无空行）的表格能正确截断上方段落。

**渲染（继承安全铁律）。** `renderTable` 用 `React.createElement`（本文件为 `.ts`、全程 createElement，非 JSX）产出 `<div class="overflow-x-auto"> <table> <thead><tr><th>… <tbody><tr><td>…`，**单元格内容一律走 `renderInline`**——即所有文本仍是 React 字符串子节点（自动 HTML 转义）、链接仍受 `isSafeHref` 协议白名单约束，绝不 `dangerouslySetInnerHTML`。列对齐通过 `text-left/center/right` class 施加到该列的 `th` 与 `td`。外层 `div` 负责窄面板横向滚动与外边距/圆角。`renderBlock` 的 `switch` 增加 `case "table"`。

**流式途中不渲染表格（保持现状）。** `AgentMessageBubble.MessageBody` 仅在助手**完成轮**（非 `streaming`）才调用 `renderMarkdown`；流式途中走 `whitespace-pre-wrap` 纯文本。因此表格只在整段回复落定后一次性成形，天然规避「半截表格闪烁」，无需改 bubble 组件。

---

## 3. File / Module Change Plan（文件 / 模块变更计划）

| # | 文件 | 创建/修改 | 一句话意图 |
|---|------|-----------|------------|
| 1 | `apps/web/lib/markdown.ts` | 修改 | 新增 `table` 块类型 + 解析（`isTableRow`/`isTableDelimiter`/`isTableStart`/`splitRow`/`parseAlign`/`takeTable`/`normalizeRow`）+ 渲染（`renderTable`/`renderRow`/`cellClass`）；接入 `parseBlocks` 与 `takeParagraph` 截断、`renderBlock` switch |
| 2 | `apps/web/lib/markdown.test.ts` | 修改 | 补表格用例：基本表格、列对齐、单元格内联格式、含竖线的非表格段落不误判、转义竖线、单元格 XSS 转义、`parseBlocks` 含 `"table"` 结构 |
| 3 | `apps/web/components/Agent/AgentComposer.tsx` | 修改 | 新增 `onClear` prop；在发送/停止按钮**左侧**渲染 ghost「清除」按钮，`running` 时禁用（清除为同步操作，不引入 `clearing` 态——见评审 C1）|
| 4 | `apps/web/components/Agent/AgentPanel.tsx` | 修改 | 新增 `clearContext` 回调（`newConversation()` + best-effort `agentApi.clear(old)`，运行中守卫）；将 `onClear` 透传给 `AgentComposer` |

**显式不改动**：所有后端文件（`services/quant/app/agent/*`、`api/agent.py`）、`agentApi.ts`（`clear` 已存在）、`store.ts`（`newConversation`/`agentConversationId` 已存在，清除用本地 state，无需新增）、`agentReducer.ts`、`agentWs.ts`、`AgentMessageBubble.tsx`、DB schema、`.env` / 桌面打包链路、依赖清单（无新增 npm 包）。

---

## 4. Interface Design（接口设计）

### 4.1 组件 props（前端）

`AgentComposer`（新增 1 个必填 prop `onClear`，其余不变）：

```ts
function AgentComposer(props: {
  value: string;
  onChange: (v: string) => void;
  onSend: () => void;
  onStop: () => void;
  onClear: () => void;      // 新增：点击「清除」回调（同步清空，无 clearing 态——评审 C1）
  running: boolean;
  disabled?: boolean;       // 既有：未连接时禁用输入与发送
}): JSX.Element
```

按钮区 JSX 顺序（textarea 之后、actions 之内）——「清除」必须在「发送/停止」**左侧**。
清除按钮**仅在 `running` 时禁用**（与连接态 `disabled` 解耦：断线也允许本地清空并重连，见 §2.1）：

```tsx
<div className="flex items-end gap-2 …">
  <textarea … />
  <Button variant="ghost" size="sm" onClick={onClear}
          disabled={running} aria-label="清除上下文" title="清除所有上下文">
    清除
  </Button>
  {running
    ? <Button variant="danger"  size="sm" onClick={onStop} aria-label="停止">停止</Button>
    : <Button variant="primary" size="sm" onClick={onSend}
              disabled={disabled || !value.trim()} aria-label="发送">发送</Button>}
</div>
```

`AgentPanel` 对外接口不变（无新 prop / 无新 store 字段）。新增内部回调：

```ts
const clearContext = useCallback(() => {
  if (running) return;                         // 运行中守卫（按钮亦 disabled）
  const old = convIdRef.current;
  newConversation();                           // 换新 id → 既有 effect 重置 + 重连空会话
  if (old) void agentApi.clear(old).catch(() => {}); // best-effort purge 旧行
}, [running, newConversation]);
// 透传：<AgentComposer … onClear={clearContext} />
// 注（评审 C2）：清空效果承载于 newConversation() 产出**新且非空** id 以触发 conversationId-keyed
// effect；目标 Electron 安全上下文恒提供 crypto.randomUUID，与既有「新对话」同源依赖（见 R11）。
```

### 4.2 REST / WS

- 复用 `DELETE /api/py/agent/conversations/{conversation_id}` → 后端 `DELETE /agent/conversations/{id}`，返回 204。**无新增端点、无协议变更**。
- WS `/api/ws/agent/{convId}` 协议不变；清除经「换 id → 重连」走既有 ready/history 流程。

---

## 5. Data Model（数据模型）

- **后端持久化无变更**：仍是既有 `AgentMessage`（conversation_id / role / blocks_json / created_ts）；清除 = 删除该会话的行（`repo.clear_agent_messages`），不改表结构。
- **前端内存形状新增一个块变体**（`apps/web/lib/markdown.ts` 的 `Block` 判别联合）：

```ts
type Align = "left" | "center" | "right";

type Block =
  | { kind: "code"; lang: string; lines: string[] }
  | { kind: "heading"; level: number; text: string }
  | { kind: "ul"; items: string[] }
  | { kind: "ol"; items: string[] }
  | { kind: "p"; lines: string[] }
  | { kind: "table"; header: string[]; align: (Align | null)[]; rows: string[][] }; // 新增
```

渲染时 `align[i] ?? null`（容忍 align 列数与表头不一致）。`rows` 内每行已由 `normalizeRow` 对齐到 `header.length`。

---

## 6. 关键实现片段（供下游逐行落地）

> 以下为 `markdown.ts` 的目标实现，沿用文件既有 `React.createElement` 风格与小函数拆分，圈复杂度均 < 10；逻辑行长守阈值，惟长 Tailwind className 串与既有 `markdown.ts`（如 line 13/155）一致，允许超 80（强行折行 className 反损可读性，评审 C6）。

```ts
// —— 表格识别 ——
const DELIM_CELL_RE = /^\s*:?-{1,}:?\s*$/;

function isTableRow(line: string): boolean {
  return line.includes("|");
}

function splitRow(line: string): string[] {
  let s = line.trim();
  if (s.startsWith("|")) s = s.slice(1);
  if (s.endsWith("|")) s = s.slice(0, -1);
  // 按未转义竖线切分，再把 \| 反转义为字面量竖线（ES2018 lookbehind，Electron/现代浏览器均支持）
  return s.split(/(?<!\\)\|/).map((c) => c.replace(/\\\|/g, "|").trim());
}

function isTableDelimiter(line: string): boolean {
  if (!line.includes("-")) return false;
  const cells = splitRow(line);
  return cells.length > 0 && cells.every((c) => DELIM_CELL_RE.test(c));
}

function isTableStart(lines: string[], i: number): boolean {
  return i + 1 < lines.length && isTableRow(lines[i]) && isTableDelimiter(lines[i + 1]);
}

function parseAlign(cell: string): Align | null {
  const l = cell.startsWith(":");
  const r = cell.endsWith(":");
  if (l && r) return "center";
  if (r) return "right";
  if (l) return "left";
  return null;
}

function normalizeRow(cells: string[], cols: number): string[] {
  const out = cells.slice(0, cols);
  while (out.length < cols) out.push("");
  return out;
}

function takeTable(lines: string[], start: number): [Block, number] {
  const header = splitRow(lines[start]);
  const align = splitRow(lines[start + 1]).map(parseAlign);
  const rows: string[][] = [];
  let i = start + 2;
  while (i < lines.length && lines[i].trim() !== "" &&
         isTableRow(lines[i]) && !isFence(lines[i])) {
    rows.push(normalizeRow(splitRow(lines[i]), header.length));
    i += 1;
  }
  return [{ kind: "table", header, align, rows }, i];
}

// —— 表格渲染（单元格复用 renderInline → 自动转义 + 协议白名单）——
const ALIGN_CLASS: Record<Align, string> = {
  left: "text-left", center: "text-center", right: "text-right",
};

function cellClass(align: Align | null, isHeader: boolean): string {
  const base = isHeader
    ? "border border-[var(--border)] bg-[var(--surface-2)] px-2.5 py-1.5 font-semibold"
    : "border border-[var(--border)] px-2.5 py-1.5";
  return align ? `${base} ${ALIGN_CLASS[align]}` : base;
}

function renderRow(cells: string[], align: (Align | null)[], key: string,
                   tag: "th" | "td"): React.ReactNode {
  const isHeader = tag === "th";
  const tds = cells.map((c, i) =>
    React.createElement(tag, {
      key: `${key}-${i}`,
      className: cellClass(align[i] ?? null, isHeader),
      scope: isHeader ? "col" : undefined,   // a11y：表头列范围（评审 C5）
    }, renderInline(c, `${key}-${i}`)));
  return React.createElement("tr", { key }, tds);
}

function renderTable(block: Extract<Block, { kind: "table" }>, key: string): React.ReactNode {
  const thead = React.createElement("thead", { key: `${key}-h` },
    renderRow(block.header, block.align, `${key}-hr`, "th"));
  const tbody = React.createElement("tbody", { key: `${key}-b` },
    block.rows.map((r, i) => renderRow(r, block.align, `${key}-r${i}`, "td")));
  const table = React.createElement("table",
    { className: "w-full border-collapse text-[12px] leading-relaxed text-[var(--text)]" },
    thead, tbody);
  return React.createElement("div",
    { key, className: "my-1.5 overflow-x-auto" }, table);
}
```

`parseBlocks` 主循环（在 `OL_RE` 分支与 `else` 段落兜底之间插入）：

```ts
} else if (OL_RE.test(line)) {
  [, i] = pushBlock(blocks, takeList(lines, i, "ol"));
} else if (isTableStart(lines, i)) {            // 新增：表格优先于段落
  [, i] = pushBlock(blocks, takeTable(lines, i));
} else {
  [, i] = pushBlock(blocks, takeParagraph(lines, i));
}
```

`takeParagraph` while 条件追加表格截断；`renderBlock` switch 增加表格分支：

```ts
// takeParagraph：
while (i < lines.length && lines[i].trim() !== "" &&
       !isBlockStart(lines[i]) && !isTableStart(lines, i)) { … }

// renderBlock：
case "table":
  return renderTable(block, key);
```

---

## 7. Testing & Acceptance（测试与验收）

### 7.1 单元测试（`apps/web/lib/markdown.test.ts`，vitest）

新增（命名描述行为，遵循 CLAUDE.md §七）：

1. `renders a GFM table with thead/tbody` —— `| A | B |\n| --- | --- |\n| a1 | b1 |` → 含 `<table`、`<thead`、`<th`、`<td`、`A`、`b1`。
2. `applies column alignment classes` —— `:--` / `:--:` / `--:` 分隔行 → 输出含 `text-left` / `text-center` / `text-right`。
3. `renders inline formatting inside cells` —— 单元格 `**粗**` → `<strong>粗</strong>`。
4. `does not treat a pipe paragraph without a delimiter row as a table` —— `a | b\nc | d` → **不含** `<table`（仍是段落）。
5. `unescapes escaped pipes in cells` —— `| a\\|b | c |` → 单元格文本含字面量 `a|b`。
6. `escapes html inside table cells (R6)` —— 单元格含 `<script>` → 含 `&lt;script&gt;`、不含 `<script>`。
7. `parseBlocks separates a table block` —— `parseBlocks(...)` 的 `kind` 序列含 `"table"`。
8. `separates a table that immediately follows a paragraph` —— `段落文字\n| A | B |\n| --- | --- |\n| a1 | b1 |`（段落与表格间无空行）→ `parseBlocks` 的 `kind` 序列为 `["p","table"]`（验证 `takeParagraph` 的 `!isTableStart` 截断路径，评审 C4）。

回归基线：现有 **11** 条 markdown 用例（rendering 5 + safety 5 + parseBlocks 1）须全绿（表格改动不得回归 code/list/heading/link/safety）。

### 7.2 验收标准（手动 / 集成）

- A1 **位置**：「清除」按钮出现在底部输入条、发送（或停止）按钮的**左侧**；ghost 样式、有 `aria-label="清除上下文"`。
- A2 **行为**：非运行态单击「清除」→ 消息流即时清空、回到空态（AgentEmptyState）；输入框草稿不被清空。核查 `agentConversationId` 确实由非空旧值变为另一非空新值（前置条件：运行时提供 `crypto.randomUUID`，评审 C2 / R11）。
- A3 **后端 purge**：清除后调用 `GET /api/py/agent/conversations/{old}/messages` 返回 `[]`（旧行已删）。
- A4 **真清上下文**：清除后再发一条消息，模型回复不引用此前对话（新会话、零历史）。
- A5 **运行中禁用**：助手回复进行中，「清除」按钮 disabled，无法触发。
- A6 **表格渲染**：让模型输出含 GFM 表格的回复 → 完成后渲染为带边框/对齐/可横向滚动的真实 `<table>`，而非带竖线的纯文本。
- A7 **不误判**：含竖线但无分隔行的普通文本仍按段落显示。
- A8 **安全**：表格单元格内的 `<script>`/`javascript:` 链接被转义/降级为纯文本（无可执行标记）。
- A9 **构建/质检**：`apps/web` 下 `npm run lint`、`npm run test`、`npm run build` 全绿；桌面打包链路无需改动即可继续工作。

---

## 8. Risks & Mitigations（风险与缓解）

| # | 风险 | 缓解 |
|---|------|------|
| R1 | 仅 DELETE 不重连 → 新会话无 sink，发消息收不到回复 | 清除走「换 id + 既有 effect 重连」，确保 `attach` 重新挂 sink（§2.1 关键约束）|
| R2 | 运行中清除 → 孤儿 run 把消息回写已删除会话（复活旧行） | 清除按钮 `running` 时禁用 + `clearContext` 内 `if (running) return` 双重守卫 |
| R3 | `agentApi.clear(old)` 因离线/网络失败 → 旧行残留 | best-effort（`.catch(()=>{})`），不阻塞清除效果；下次仍可清；用户已获干净空会话 |
| R4 | 误触清除丢失对话（无确认弹窗） | 需求明确「一键清除」故不加弹窗；ghost 低调样式 + 运行中禁用降低代价；后续可加撤销/快照（v2 备选）|
| R5 | 表格解析把含竖线的普通段落误判为表格 | 「下一行须为分隔行」前瞻判定（`isTableStart`），单竖线不触发 |
| R6 | 转义竖线 / 列数参差的脏表格导致错列或抛错 | `splitRow` 处理 `\|`、`normalizeRow` 截断+补空到表头列数；解析全程不抛 |
| R7 | 窄右面板下宽表撑破布局 | 外层 `div overflow-x-auto` 横向滚动，`table w-full border-collapse` |
| R8 | 表格单元格成为 XSS 向量 | 单元格统一走 `renderInline`，继承「字符串子节点自动转义 + http(s) 协议白名单 + 无 dangerouslySetInnerHTML」铁律 |
| R9 | `(?<!\\)` lookbehind 兼容性 | 目标运行时为 Electron(Chromium)/现代浏览器 + Node(vitest)，均支持 ES2018 lookbehind（tsconfig `target: ES2020`）；**仅作最后手段**才退化为不识别 `\|` 的简单 `split("\|")`（功能降级、不报错），且一旦启用须同步移除/调整 §7.1 用例 #5（转义竖线）——目标运行时无需回退 |
| R10 | 「清除」与 header「新对话」语义重叠引困惑 | 文档与按钮文案区分：「清除」= purge 当前会话并从零开始（删旧行）；「新对话」= 仅切新会话（旧行留存）|
| R11 | `makeConversationId()` 在缺 `crypto.randomUUID` 时回退 `""`，使连接 effect 早返回、清除静默失效（评审 C2） | 目标 Electron 安全上下文恒提供 `randomUUID`，与既有「新对话」同源依赖、已随第 1 轮上线验证；§2.1 显式登记该前提，验收 A2 增「id 确实变更」核查；非安全上下文支持为下游跟进（本轮 out of scope）|

---

## 9. Out of Scope（本轮不做）

- 不改 Agent 运行内核（loop / manager / tools / LLM 客户端 / prompts）。
- 不加表格的复杂特性（合并单元格、嵌套块、多行单元格、脚注）。
- 不加清除的二次确认 / 撤销 / 历史会话浏览器。
- 不动桌面打包、`.env`、CI、DB 迁移、第三方依赖。

---

## 10. 评审记录（Subtask#1 填写）

> 已完成。完整问题清单（C1~C8）、源码事实核验与就地修复说明见**文首「评审记录（Subtask#1，v2）」段**；评审结论见文末「评审结论」段。文档版本已升至 **v2**。

---

## 11. 实施过程发现的方案缺陷（Subtask#2 回填）

> 占位：代码开发节点若发现本方案与实际不符（命名、签名、样式 token、tsconfig target 等），在此登记偏差与最终落地决策，保证 spec 与代码一致。

**落地结论（Subtask#2）：方案与源码事实一致，四个文件均按 §3 变更计划与 §6 实现片段逐项落地，无接口/命名/样式 token 偏差，无需修正方案。** 实测：`npm run test` 162 条全绿（其中 `markdown.test.ts` 由 11 → 19 条，含本轮新增 8 条表格用例）；`npm run build`（含 TypeScript 类型检查）`✓ Compiled successfully`，4 个静态页面正常生成。

**唯一与验收清单的出入（A9 `npm run lint`）：** 本项目 `apps/web` 未配置 ESLint（无 `.eslintrc*`），`next lint` 会进入交互式初始化向导，无法在非交互环境跑出"绿/红"结论——这是**既有工程缺口**，非本轮改动引入。本轮以 `npm run build` 内置的 TS 类型检查 + `vitest` 作为等价质检门禁（均全绿）。`lint` 的配置化属共享工程面，列为下游跟进（本轮 out of scope，未顺手新增 eslint 配置以保持改动聚焦）。

---

## 评审结论（Subtask#1）

**有条件通过（Approved with conditions）。**

方案逐节核验与源码事实一致，两项改动均为**纯增量、向后兼容**，复用既有已上线路径（换 id 重连 / 安全 markdown 管线），**无 P0**；两项 **P1 已就地修复**——C1（删除死接口 `clearing`）、C2（显式登记 `makeConversationId` 承载性依赖并增 R11/验收核查）；P2（C3~C7）亦就地修复，C8 留档不改。文档版本已升 **v2**。

下游代码节点（Subtask#2）须满足以下条件，方可视为合规落地：

1. **严格按 v2 接口落地**：`AgentComposer` 不引入 `clearing`，清除按钮仅 `running` 时禁用、与连接态 `disabled` 解耦；「清除」渲染在发送/停止按钮**左侧**（`variant="ghost" size="sm"`、`aria-label="清除上下文"`）。
2. **运行中禁用清除是硬约束**：`clearContext` 内 `if (running) return` 与按钮 `disabled={running}` 双守卫缺一不可（防孤儿 run 把消息回写已删除会话、复活旧行）。
3. **表格解析须前瞻判定**：仅当"下一行为分隔行"才识别表格（`isTableStart`）；单元格一律走 `renderInline`（继承自动转义 + http(s) 协议白名单），**绝不** `dangerouslySetInnerHTML`；表头 `<th>` 带 `scope="col"`。
4. **测试须含 11 条回归基线全绿 + §7.1 新增 8 条表格用例**（含段落紧邻表格、单元格 XSS 转义、转义竖线、含竖线非表格不误判、列对齐）；`npm run lint / test / build` 全绿。
5. **承载性依赖留意（C2/R11）**：清除依赖 `makeConversationId()` 产新非空 id；目标 Electron 安全上下文恒满足，若未来支持非安全上下文须先硬化该函数（本轮 out of scope，不得在本轮顺手改 store 行为）。

满足上述条件即可进入 Subtask#2 开发。
