# AI 自动交易 · 二次反思致「永不开单」记忆惯性修复 设计方案

> Feature slug: `ai-trade-reflection-hold-inertia-fix`
> 版本：**v2**（Subtask #1 设计评审修订版；v1 = Subtask #0 方案设计初稿）
> 关联既有特性：ai-trade-pre-trade-reflection（下单前二次反思，本次事故引入源）、
> ai-auto-trading（决策脑基座）、ai-trade-wake-interval（墙钟定时唤醒）、
> ai-trade-reasoning-chain（思维链研判）、ai-auto-trading-backtest（回测复用同一决策脑）。

---

## 评审记录（Review Notes · Subtask #1 设计评审）

> 评审方法：对照仓库现状逐条核验本方案引用的代码路径与行号，再按**可行性 / 完整性 / 一致性 /
> 尺度**四维审查。**引用核验结论**：§2.0 现状梳理与全部 `file:line` 引用与源码一致——
> `reflection.py:41-42`（abort→`_hold`）、`decider.py:466-495`（commit_round）/`497`（summarize）
> /`513`（seed）、`session.py:313`（`_maybe_interval_wake`）/`403`（`_run_decision_round`）
> /`420-428`（反思编排与 commit 调用点）/`705`（payload 已含 trigger）/`959`（`_compute_ai_stats`
> 模块级纯函数）、`backtest.py:338-368`（回测反思分支）/`198-200`（既有反思计数）、
> `prompts.py:329/378-383/394-396/426-427`（首行 / 工具描述 / 不对称保守文案两处）、
> `AI_TRADE_` 前缀透传 `desktop/src/lib/config.js:72`、`extra="ignore"` `config.py:19`、
> 冻结锚点 `test_ai_trade_prompts.py:133-151` 全部属实且 F1 保留清单完整。
> **无 P0 问题**；下列 P1 已全部就地修复并升版 v2。

| # | 严重度 | 位置 | 问题 | 处置 |
|---|---|---|---|---|
| R-01 | P1 | §2.4 F4a | interval 标注原文断言「自上轮以来无新 K 线，行情数据与上一轮相同或仅实时价微动」，但与 F4c 组合后**能到达 prompt 的 interval 轮**恰恰多为：持仓/挂单轮（任意价况）、价格越阈轮（≥0.05% 已非「微动」）、candle 轮因 in-flight 被跳过后信号板已含新根的轮——向模型陈述失实的「数据未变」会在这些轮反向强化观望，与本方案目标相悖 | 已改为条件化如实措辞（见 F4a；零新增 payload 字段） |
| R-02 | P1 | §2.4 F4c | 守卫命中不刷新 `_last_decision_started_wall`（为价格越阈即恢复，设计正确）→「到点」判定此后每个 ~5s monitor tick 恒成立，`wake_skips` 每 tick +1：3 分钟周期下 1 小时静止行情老行为约 20 次唤醒、新计数却 ~720，观测口径失真（F5 恰要用它验证修复生效），debug 日志亦每 5s 刷屏 | 已增 `_last_skip_counted_wall` 计数节流：每个唤醒周期至多计数+日志一次，语义 =「被抑制的、原本会发生的唤醒次数」（见 F4c） |
| R-03 | P1 | §2.4 F4c / §六-8 / 文件计划 #12 | 守卫默认 0.0005 开启会使既有 `test_interval_wake_triggers_decision_between_candles`（默认 `Settings()`、价格恒 30000、无新根）必红——原「零回归」表述未写明哪些既有用例行为翻转、如何适配，Subtask #2 会被误导 | 已在文件计划 #12 与 §六-8 显式列出适配方式（该用例显式 `ai_trade_wake_skip_unchanged_pct=0.0` 构造、断言语义改述为「守卫关闭时行为与升级前相同」）；其余 7 个既有用例已逐个核验不受默认值影响（价差 4% / in-flight / stale / 关闭 / 无板 / 计时器合流 / 纯夹紧） |
| R-04 | P1 | §2.5 F5 / §五.4 / §六 DoD | `reflectionAborts` 按 `reflectionVerdict=="abort"` 计，而 fail-closed（timeout/llm_error/no_tool/invalid）的 verdict 同为 `"abort"`——方案明确「529 类基础设施故障是次要因素且不改」，但一次过载抖动就会抬高否决率、污染 DoD 的 `reflectionAborts/reflectionRuns` 验收指标；F2 journal 注记已区分 `failClosed`，aiStats 却不分，口径自相矛盾 | 已改：`reflectionAborts` 只计 `degradeReason=="reflection_abort"` 的真实否决，新增 `reflectionFailClosed` 计四个 fail-closed 码（反思轮进入前 degrade 恒为 None——hold/门禁降级轮根本不触发反思，故 `_decisions` 样本上二者可严格区分）；§四/§五.4/DoD 同步 |
| R-05 | P1 | §六-2 | F2.3 承诺「非否决轮 tool_result 内容逐字不变」，但测试计划无对应守门断言；本仓库已多次发生「加字段就全轮富化 / 重建即丢字段」型复发（`reflection.py` 内 F-1 教训注释即为一例） | 已在 §六-2 增补冻结断言：非否决轮 tool_result 文本 == `json.dumps(executed, ensure_ascii=False)` 且不含 `"reflection"` |
| R-06 | P2 | §2.2 / §五.1 | journal 每轮恒带 `trigger`/`reflection` 两键（含 None 值）：全轮形状稳定、F4b 折叠判定对 legacy 兼容，代价是摘要素材每轮约 +30 字符 | 维持现设计（形状稳定优先）；已核验既有测试无 journal 条目全字典相等断言（均为单键断言），无回归风险 |
| R-07 | P2 | §2.5 F5 | `wakeSkips` 只在瞬态 status 事件，会话停止后无法事后验证 F4c 效果 | 已就地改进：`_finalize_run` 富化 try 块内追加 `metrics["aiStats"]["wakeSkips"]`（加法、绝不抛；`_compute_ai_stats` 纯函数签名不动） |
| R-08 | P2 | §2.4 F4b | 折叠条目丢弃被折叠轮的 `analysis` 摘录 | 属设计意图（重复数据轮的研判本就冗余，且开/平仓、candle、反思轮永不折叠）；不改，记录备查 |
| R-09 | P2 | §2.6 | 运维注记「升级后重启 AI-trade 会话」未点名分组场景 | 已补：含 AI-trade 分组的全部成员会话 |
| R-10 | P2 | §七 R-6 | F1 文案修复效果无确定性单测出口（LLM 行为属性） | 方案已自识别（锚点断言 + F5 计数 + 回测半定量验收）；接受，无需改动 |

**四维结论**：**可行性**——全部改动落在既有模块边界内，无新依赖、零 DB 迁移、零 REST/WS 破坏，
装配点（trigger 已在 payload、`_decisions` 已带 reflectionVerdict、desktop 透传白名单、
`resting`/`projected` 取值点）均已核验存在；**完整性**——P1 五项（失实标注、计数虚高、默认值翻转
既有用例、验收指标污染、冻结缺守门）已就地修复；**一致性**——与 CLAUDE.md「修 bug 先写复现测试」
「纯函数下沉小模块（session.py 已越 800 行不再喂）」「错误显式传播 / 富化绝不抛」「配置显式声明防
`extra="ignore"` 静默丢弃」全部对齐；**尺度**——四组修复逐一对应诊断因果链四环、各带独立回退口
（F1 文案可回调 / F2 注记纯加法 / F3 摘要指令加法 / F4c 阈值 0 一键关闭），无过度设计。

---

## 一、Overview（概述）

最近合入的「下单前二次反思」特性（`ai-trade-pre-trade-reflection`）在「决策 → 真实下单」之间
插入了一次独立的风控复核 LLM 子调用，本意是拦截错误交易。上线后出现严重的行为退化：**Agent
始终不开单、长期空仓**。对历史留存样本的诊断结论为一条相互强化的因果链：

1. **反思 Prompt 过度保守**（`prompts.py:394-396`、`prompts.py:426-427`）——「若发现**任何**足以令你
   不放心的问题选择 abort（宁可错过，不可做错）；只有当你**确信**该决策稳健时才 confirm」。这一
   不对称标准使留存样本中 **7 次开仓候选全部被 abort、0 次 confirm**：任何真实交易都必然存在
   「不放心之处」，该措辞等价于把 abort 设为默认答案。
2. **否决后记忆失真**——反思否决把原开仓候选改写成普通 `hold`（`reflection.py:41-42`），再由
   `commit_round`（`decider.py:480-490`，调用点 `session.py:428`）原样写入 `_context` 与 `_journal`。
   摘要器因此完全不知道「曾经想开仓但被否决」，反而把长期空仓总结为「观望被行情验证正确、
   零回撤表现完美」，滚动摘要作为长期记忆又喂回第一阶段决策器，使其越来越不愿开仓。
3. **重复数据加速强化**——诊断样本中 ETH 最近 20 轮里 16 轮是墙钟定时唤醒、仅 4 轮携带新 K 线：
   同一份 15 分钟行情数据被重复决策约 5 次，每次都产出一条「hold」流水进入 journal 与摘要素材，
   把「继续观望」当作被反复验证的独立证据。
4. 最终形成**记忆惯性**：当前最近 50 轮全部由第一阶段直接输出 hold——既不触发反思、也不进入
   执行器，说明问题已从「当轮被否决」演变为「摘要记忆自我强化的不开单」。
5. 日志中的 HTTP 529（模型过载）安全降级 hold 只是次要放大因素（最近 50 轮无降级标记，且
   `llm_error` 已在可重试白名单内、默认重试 3 次），**本方案不为其做任何改动**。

本方案按因果链逐环拆弹，共四个修复组 + 一个观测组：**F1** 校准反思 Prompt（abort 需指名实质
缺陷、收缩仓位优先于否决、机会成本对称化）；**F2** 否决意图记忆保真（journal 与模型上下文如实
记录「提议了什么、为何被否决」）；**F3** 摘要器反自我强化（摘要指令明确「空仓零回撤≠表现完美」、
「被否决意图须与事后行情对照验证」，摘要种子声明「背景非指令」）；**F4** 重复数据轮去重（prompt
标注定时唤醒、摘要素材折叠连续重复 hold、空仓且行情未变时跳过定时唤醒）；**F5** 实盘 aiStats 补
反思计数以便验证修复生效。**全部确定性安全层逐字不变**：`apply_reflection` /`_map_reflection` 的
「只能收紧、fail-closed、abort 零重试」契约、共识门禁、置信度门禁、档位量化、预算/名义护栏均
不触碰——本方案只修「模型看到什么、记住什么、何时被唤醒」，不放松任何一道硬护栏。

---

## 二、Technical design（技术设计）

### 2.0 现状关键代码路径（已逐一核对源码）

```
session._run_decision_round (session.py:403)
  ├─ payload = _build_decision_payload(...)        # session.py:705（payload 已含 trigger 字段）
  ├─ decision, degrade = decider.decide(payload)   # 第一阶段（decider.py:207）
  ├─ decision, degrade = _apply_consensus_gate(...)
  ├─ decision, degrade = _reflect_before_execute(round_no, ts, payload, decision, degrade)
  │     └─ verdict = decider.reflect(...)          # 反思子调用（decider.py:369）
  │        final, new_degrade = apply_reflection(decision, degrade, verdict, step)
  │                                                # abort/fail-closed → _hold(理由)（reflection.py:41-42）
  ├─ executed = _execute_decision(final, ...)      # hold → 不进执行器
  └─ summary = decider.commit_round(payload, final, executed, analysis=…)   # session.py:428
        └─ _journal.append({round, signalBoard, decision(最终 hold), executed, …})  # decider.py:486
           → summarize() 以 _journal 为素材（decider.py:497）→ 滚动摘要喂回后续每轮 prompt
```

失真点一目了然：`commit_round` 只见「反思后的最终决策」，原提议在 `_journal`、`_context`、摘要
素材三处全部蒸发（只在瞬态 `_round_trace`/WS decision 事件留痕，而这两处不进模型记忆）。

定时唤醒路径：`_monitor_tick → _maybe_interval_wake`（session.py:313）只判
「到点 / 非 in-flight / 非 stale / 有信号板」，**不判行情是否有新数据**，导致同一份数据被反复决策。

### 2.1 F1 — 反思 Prompt 校准（根因 ①，纯文案，确定性层零改动）

改 `prompts.py` 三处文案（`build_reflection_system_prompt`、`build_reflection_prompt` 尾段、
`REFLECTION_TOOL_DESCRIPTION`），把「不对称保守」改为「有名有姓才否决」：

- **删除**「若发现任何足以令你不放心的问题，选择 abort（宁可错过，不可做错）；只有当你确信该
  决策稳健时才 confirm」及 user prompt 尾段同义句（「若确有不放心处则 abort（宁可错过不可做错），
  确信稳健才 confirm」）。
- **替换为**分级复核标准（三条均须写入 system prompt）：
  1. **默认立场**：提议决策与证据大体一致、风险敞口可控 → `confirm`；对仓位偏重有疑虑 →
     **优先** `confirm` + `adjustedSizeFraction` 收缩（收缩是你的首选保守工具，而非 abort）。
  2. **abort 门槛**：仅当你能**指名一条实质性缺陷**时才 `abort`——(a) 方向与关键证据直接矛盾；
     (b) 与更高周期背景强冲突且决策未给出对冲理由；(c) 当前持仓与会话盈亏下新增敞口不可接受；
     (d) 明显追高/杀跌/频繁反手；(e) 证据严重不足却重仓。`reason` 必须引用具体证据；
     「泛化的不放心」不构成否决理由。
  3. **机会成本对称**：错过一笔证据充分的交易与做错一笔交易同样是损失；你的职责是拦截
     **错误交易**，不是消灭交易。另注明：`close` 是风险收敛动作，否决 close 等于延长敞口，
     须有极强理由。
- **保留全部既有安全锚点文本**（`test_ai_trade_prompts.py:133-151` 断言的）：「风控复核官」
  「批判性地复核」「只能…收紧/调小仓位」「无权…放大/改变方向」「必须且只能调用一次
  `submit_trade_reflection`」、user prompt 以「也不得调用其它工具。」收尾。
- `REFLECTION_TOOL_DESCRIPTION` 同步改写 verdict 语义（abort=「有具体可指明缺陷」）。

`_map_reflection` / `apply_reflection` / fail-closed / `_RETRYABLE_REFLECTION_REASONS` / abort 零重试
**逐字节不变**——`test_ai_trade_reflection.py` 全部既有用例原样通过是硬门禁。

### 2.2 F2 — 否决意图记忆保真（根因 ②）

**不变式保持**：`_context` 中 assistant 的 `tool_use` 仍记录**最终**决策（R-P1-3：上下文绝不能
「记住一个从未开过的仓」）。保真通过**加法注记**实现：

1. 新纯函数 `reflection.build_reflection_note(original, verdict, final) -> dict | None`
   （`reflection.py`，与 `apply_reflection` 同居、session/backtest 共用）：
   `verdict is None or not verdict.ran` → `None`；否则返回
   ```python
   {"verdict": verdict.verdict,                 # "confirm" | "abort"
    "vetoed": final.action != original.action,  # 被否决（open_*/close → hold）
    "failClosed": verdict.degrade_reason is not None,  # 基础设施故障≠模型真实否决（摘要须区分）
    "reason": verdict.reason,
    "proposedAction": original.action, "proposedSizeFraction": original.size_fraction,
    "proposedConfidence": original.confidence,
    "finalAction": final.action, "finalSizeFraction": final.size_fraction}
   ```
2. `session._reflect_before_execute` 返回值由二元组扩为三元组
   `(final, degrade, reflection_note)`（直通分支返回 `(decision, degrade, None)`）；
   `_run_decision_round` 把 note 透传给 `commit_round`。
3. `decider.commit_round(payload, decision, executed, *, analysis=None, reflection=None)` 增
   keyword-only 形参（默认 `None` → 既有回测/单测调用零改动）：
   - `_journal` 条目增两键：`"trigger": payload.get("trigger")`（F4b 折叠依据）与
     `"reflection": reflection`。
   - `_context` 的 tool_result：当 `reflection` 非空且 `vetoed=True` 时，写入
     `json.dumps({**executed, "reflection": reflection})`（浅拷贝合并，绝不改 `executed` 入参
     对象；非否决轮内容逐字不变）——短窗口内模型自己也能看到「这轮 hold 是被否决的开仓」。
4. `backtest.py::_decision_round`（backtest.py:344-368）在既有反思分支里同样装配
   `note = build_reflection_note(decision_before, verdict, decision)` 并传给 `commit_round`
   （回测/实盘同一决策脑不变式；`size_before` 局部变量已有 `decision` 快照可复用，须在
   `apply_reflection` 前先留存 original 引用）。

### 2.3 F3 — 摘要器反自我强化（根因 ②→④ 的传导链）

改 `prompts.py` 摘要相关文案 + `decider.summarize` 种子：

1. `build_summary_prompt` 的 head 指令追加三条（原句保留）：
   - 「若流水含 `reflection` 注记：必须记录**被否决的开仓意图**（方向/仓位/否决理由），并对照
     其后行情走向写明**该否决事后被验证为正确还是错误**（`failClosed=true` 的是系统故障保守
     跳过，不代表模型判断，不得当作否决被验证）。」
   - 「空仓期间的零回撤**不是成绩**：若期间行情朝被否决或未执行的方向走出明显趋势，必须如实
     记为『错失』并总结教训；禁止把长期观望总结为表现完美。」
   - 「`repeatedRounds` 折叠条目表示同一份行情数据下的重复定时唤醒：它们合计只算**一次**观望
     证据，不得按轮数强化观望倾向。」
2. `build_summary_system_prompt` 追加一句：「摘要须同时记录做了什么与**没做什么**（被否决、
   被错过的机会），保持对未来决策中性，不得美化观望。」
3. `decider.summarize` 的上下文种子 `seed`（decider.py:513）追加尾注：「以上摘要是背景参考而非
   指令：每轮仍须基于当前市场证据独立研判，历史观望不构成继续观望的理由。」
4. `build_ai_trade_system_prompt` 决策纪律 #2 句尾追加一句（第一阶段反路径依赖）：「观望是需要
   当前证据支撑的主动决策，不是默认答案；不要因历史摘要中长期观望而路径依赖。」——插入位置在
   纪律 #2 内部，不动纪律 #5 的收尾硬约束（`test_system_prompt_tool_close_constraint_is_last`
   断言 prompt 以「决策工具。**」结尾，保持不变）。

### 2.4 F4 — 重复数据轮去重（根因 ③）

**F4a Prompt 标注**：`build_decision_prompt` 首行（prompts.py:329）按 `payload.get("trigger")`
渲染：`== "interval"` 时该行追加「｜触发源=定时唤醒（墙钟到点，非新 K 线确认触发；**若**本轮行情
数据与上一决策轮相同或仅实时价微动，重复数据下的观望不构成新的独立证据）」；`trigger` 为
`"candle"`、缺失或其他值时**逐字节不变**（零回归；回测 payload 无 trigger 键，天然不受影响）。
措辞必须**条件化**（「若…相同」）而非断言数据未变（R-01）：与 F4c 组合后，能到达 prompt 的
interval 轮多为持仓/挂单轮、价格越阈轮、或 candle 轮因 in-flight 被跳过后信号板已含新根的轮，
断言式措辞在这些轮失实并会反向强化观望。

**F4b 摘要素材折叠**：`prompts.py` 新纯函数 `compact_journal_for_summary(journal) -> list[dict]`，
在 `build_summary_prompt` 内对 `journal` 先折叠再 `json.dumps`（单点收口，实盘/回测共用；
`_journal` 本体、decision 事件、trace 一律保持逐轮原样）：

- 可折叠条件（全部满足）：`entry.get("trigger") == "interval"`、`decision.action == "hold"`、
  `not (executed or {}).get("traded")`、`entry.get("reflection") is None`。
- **连续** ≥2 条可折叠条目替换为一条：
  `{"rounds": "12-16", "repeatedRounds": 5, "trigger": "interval",
    "note": "同一份行情数据下的重复定时唤醒，均维持观望",
    "decision": <末条 decision>, "sessionPnl": <末条 sessionPnl>}`；单条保留原样。
- 非可折叠条目（开/平仓轮、candle 轮、含 reflection 注记轮、legacy 无 trigger 键条目）永不折叠。
  纯函数、不改入参。

**F4c 空仓重复唤醒跳过**（成本与惯性双收益）：

- `config.py` 新增 `ai_trade_wake_skip_unchanged_pct: float = Field(default=0.0005, ge=0.0)`
  （fraction，0.0005=0.05%；**0=完全关闭守卫**，逐字回退现行为）。env 名
  `AI_TRADE_WAKE_SKIP_UNCHANGED_PCT` 已被 desktop `config.js` 的 `AI_TRADE_` 前缀透传白名单覆盖，
  **desktop 零改动**；config.py 必须真的声明（`extra="ignore"` 会静默丢弃未声明 env）。
- 新纯模块 `ai_auto/wake_guard.py`（镜像 `reflection.py` 的「小纯函数模块」先例，避免向已越
  800 行上限的 session.py 塞逻辑）：
  ```python
  def should_skip_interval_wake(*, position_side: str, has_resting: bool,
                                bar_index: int, last_round_bar_index: int | None,
                                px: float | None, last_round_px: float | None,
                                threshold: float) -> bool
  ```
  返回 True（跳过）当且仅当：`threshold > 0` 且 `position_side == "flat"` 且 `not has_resting`
  且 `last_round_bar_index == bar_index`（自上一决策轮以来无新确认根）且 `px`/`last_round_px`
  均为有效正数且 `abs(px / last_round_px - 1) < threshold`。任一条件不满足 → False（唤醒）。
  `last_round_*` 为 None（尚无决策轮）→ False。
- `session.py` 接线：`__init__` 增 `self._last_round_bar_index: int | None = None`、
  `self._last_round_px: float | None = None`、`self.wake_skips = 0`、
  `self._last_skip_counted_wall = 0.0`；`feed_candle` 与
  `_maybe_interval_wake` 在 `create_task(_run_decision_round…)` 前记录 bar_index/px 两个值；
  `_maybe_interval_wake` 在既有条件通过、认领 `_deciding` 槽位**之前**调用守卫（纯同步函数，
  守卫与认领之间无 await、无竞态）——命中则直接 return（**不**刷新
  `_last_decision_started_wall`，下个 monitor tick（默认 5s）重新评估：价格一越过阈值或新
  K 线到来即恢复唤醒；持仓轮/挂单轮永不跳过，盯市职责不受影响）。
  **计数节流（R-02）**：命中时仅当 `now - max(self._last_decision_started_wall,
  self._last_skip_counted_wall) >= self.wake_interval_s` 才 `wake_skips += 1`、
  `logger.debug(...)` 并置 `self._last_skip_counted_wall = now`——每个唤醒周期至多计一次，
  语义 =「被抑制的、原本会发生的唤醒次数」。若按 tick 裸计数：3 分钟周期下 1 小时静止行情会
  虚计 ~720 次（老行为仅 ~20 次唤醒），F5 观测口径即失真且日志刷屏。
  `_status_payload()` 增加法键 `"wakeSkips": self.wake_skips`（前端忽略未知字段，向前兼容）。

### 2.5 F5 — 观测性（验证修复生效的量化出口）

`session.py::_compute_ai_stats`（模块级纯函数）在既有键旁增**四个**加法键（绝不抛）：
`reflectionRuns`（`reflectionVerdict` 非 None 的样本数）、`reflectionConfirms`
（`== "confirm"`）、`reflectionAborts`（**仅计模型真实否决**：`reflectionVerdict == "abort"` 且
`degradeReason == "reflection_abort"`）、`reflectionFailClosed`（`degradeReason` ∈
{`reflection_timeout`, `reflection_llm_error`, `reflection_no_tool`, `reflection_invalid`}）。
**口径依据（R-04）**：fail-closed 的 verdict 同为 `"abort"`，若混计，一次 529 类过载抖动就会抬高
否决率、污染 DoD 的 `reflectionAborts/reflectionRuns` 验收指标；而反思轮进入前 degrade 恒为
None（hold/门禁降级轮根本不触发反思），故 `_decisions` 样本上二者可严格区分，与 F2 journal 注记
的 `failClosed` 口径对齐。计数样本与既有一致（≤50 条 `_decisions` 留存样本）。落 `finalize_run`
的自由格式 `metrics_json`，**无 DB 迁移**；`_finalize_run` 富化 try 块内另追加
`metrics["aiStats"]["wakeSkips"] = self.wake_skips`（R-07：会话停止后可事后验证 F4c 生效；
`_compute_ai_stats` 纯函数签名不动）。回测侧
`reflection_runs/aborts/adjustments` 已存在（backtest.py:198-200），**零改动**——注意回测的
`reflection_aborts` 历来是 abort+fail-closed 合计口径，与实盘新四键口径的差异已在此注明，
实现时在代码注释同步说明，不得再引入第三种口径。

### 2.6 明确不做（Non-goals）

- **不**改任何确定性安全层：`apply_reflection`、`_map_reflection`、fail-closed、abort 零重试、
  共识门禁、`ai_trade_min_confidence`、档位量化、预算/名义护栏、OCO。
- **不**改 529/`llm_error` 处理（已在可重试白名单、默认重试 3 次，诊断确认为次要因素）。
- **不**动反思总闸默认值（`ai_trade_reflection_enabled=True` 保持）；**不**新增「反思置信度
  阈值」之类新门禁。
- **不**改前端（decision/WS 事件形状不变；`wakeSkips`/aiStats 新键均为加法，旧前端忽略）。
- **不**做「已污染会话的摘要自愈」：滚动摘要在内存中，修复随**新启动**的 AI-trade 会话生效；
  运维注记——升级后应重启在跑的 AI 自动交易会话（含 AI-trade 分组的全部成员会话，R-09）。

---

## 三、File / module change plan（文件 / 模块变更计划）

| # | 文件 | 操作 | 一句话意图 |
|---|---|---|---|
| 1 | `services/quant/app/ai_auto/prompts.py` | 修改 | F1 反思三处文案校准；F3 摘要 head/system 指令 + 决策纪律 #2 反路径依赖句；F4a trigger=interval 标注行；F4b 新纯函数 `compact_journal_for_summary` 并在 `build_summary_prompt` 内调用 |
| 2 | `services/quant/app/ai_auto/reflection.py` | 修改 | F2 新纯函数 `build_reflection_note(original, verdict, final)`（`apply_reflection` 逐字不变） |
| 3 | `services/quant/app/ai_auto/decider.py` | 修改 | F2 `commit_round` 增 keyword-only `reflection=None`、journal 条目增 `trigger`/`reflection` 键、否决轮 tool_result 合并注记；F3 `summarize` 种子尾注 |
| 4 | `services/quant/app/ai_auto/wake_guard.py` | **新建** | F4c 纯守卫 `should_skip_interval_wake`（无副作用、可独立单测） |
| 5 | `services/quant/app/ai_auto/session.py` | 修改 | F2 `_reflect_before_execute` 返回三元组 + note 透传 `commit_round`；F4c 守卫接线 + `_last_round_bar_index/_last_round_px/wake_skips/_last_skip_counted_wall`（计数节流 R-02）+ status 增 `wakeSkips`；F5 `_compute_ai_stats` 增反思四计数（aborts/failClosed 分流 R-04）+ `_finalize_run` 富化追加 `aiStats.wakeSkips`（R-07） |
| 6 | `services/quant/app/ai_auto/backtest.py` | 修改 | F2 反思分支装配 note 并传 `commit_round`（保留 original 决策引用） |
| 7 | `services/quant/app/config.py` | 修改 | F4c 声明 `ai_trade_wake_skip_unchanged_pct: float = Field(default=0.0005, ge=0.0)` |
| 8 | `services/quant/tests/test_ai_trade_prompts.py` | 修改 | F1 新锚点断言 +「宁可错过」缺席断言；F4a interval 行出现/candle 与 legacy 缺席（零回归钉）；F3 摘要指令断言；F4b 折叠纯函数用例 |
| 9 | `services/quant/tests/test_ai_trade_reflection.py` | 修改 | F2 `build_reflection_note` 全分支用例（否决/收缩确认/直通 None/failClosed）；既有 `apply_reflection`/`reflect` 用例**一字不改**全绿（安全层冻结门禁） |
| 10 | `services/quant/tests/test_ai_trade_decider.py` | 修改 | F2 `commit_round(reflection=…)` journal/tool_result 断言 + 缺省 None 零回归；F3 种子尾注断言 |
| 11 | `services/quant/tests/test_ai_trade_session.py` | 修改 | F2 集成：反思否决轮 journal 带 `reflection.vetoed=True` 且 tool_result 含注记 + **非否决轮 tool_result 逐字不变冻结断言（R-05）**；F5 `_compute_ai_stats` 反思四计数（含 aborts/failClosed 分流用例，R-04） |
| 12 | `services/quant/tests/test_ai_trade_wake_interval.py` | 修改 | F4c 新用例：空仓+无新根+微动 → 跳过且计数节流（同窗口只 +1）；持仓/挂单/价格越阈/新根/阈值 0 → 不跳过；跳过不刷新计时器（下个 tick 重评估）。**既有用例适配（R-03）**：`test_interval_wake_triggers_decision_between_candles` 价格恒 30000、默认阈值下守卫必命中 → 改为显式 `Settings(ai_trade_wake_skip_unchanged_pct=0.0)` 构造（断言语义 =「守卫关闭时行为与升级前相同」，断言本身不得删除或放松）；其余 7 个既有用例（价差 4% 复用 / in-flight / stale / 关闭 / 无板 / 计时器合流 / 夹紧解析）已逐个核验不受默认值影响、一字不改 |
| 13 | `services/quant/tests/test_ai_trade_wake_guard.py` | **新建** | `should_skip_interval_wake` 纯函数全分支表驱动用例 |
| 14 | `services/quant/tests/test_ai_backtest_runner.py` | 修改 | 回测反思轮 journal 带 note 断言 + 既有回测用例零回归 |

**明确不改**：`desktop/src/lib/config.js`（`AI_TRADE_` 前缀透传已覆盖新 env）、`api/ai_auto.py`
（WS 已透传 session 事件）、全部前端文件、DB models（零迁移）。

---

## 四、Interface design（接口设计）

**REST / WebSocket：零变更。** `decision` 事件已携带 `reflectionVerdict/reflectionReason`；
`status` 事件仅加法新增 `wakeSkips: number`；`metrics_json.aiStats` 加法新增
`reflectionRuns/reflectionConfirms/reflectionAborts/reflectionFailClosed/wakeSkips`
（R-04/R-07）。旧前端全部忽略未知字段。

**内部 Python 签名变更**（全部向后兼容）：

```python
# reflection.py（新增）
def build_reflection_note(original: TradeDecision, verdict: ReflectionVerdict | None,
                          final: TradeDecision) -> dict | None: ...

# decider.py（扩参，默认值保证既有调用零改动）
async def commit_round(self, payload: dict, decision: TradeDecision, executed: dict, *,
                       analysis: str | None = None,
                       reflection: dict | None = None) -> str | None: ...

# session.py（内部编排，返回值 2 → 3 元组；唯一调用点 _run_decision_round 同步改）
async def _reflect_before_execute(...) -> tuple[TradeDecision, str | None, dict | None]: ...

# prompts.py（新增纯函数；build_summary_prompt 内部调用）
def compact_journal_for_summary(journal: list[dict]) -> list[dict]: ...

# wake_guard.py（新增纯函数）
def should_skip_interval_wake(*, position_side: str, has_resting: bool,
                              bar_index: int, last_round_bar_index: int | None,
                              px: float | None, last_round_px: float | None,
                              threshold: float) -> bool: ...
```

**配置 / 环境变量**：`AI_TRADE_WAKE_SKIP_UNCHANGED_PCT`（fraction；默认 `0.0005`；`0` 关闭）。

---

## 五、Data model（数据模型）

**零 DB 迁移。** 仅内存/自由格式 JSON 形状变化：

1. `_journal` 条目（decider 内存，摘要素材）：
   ```python
   {"round": int, "signalBoard": [...], "decision": {...}, "executed": {...},
    "positionAfter": str, "sessionPnl": float, "analysis": str | None,
    "trigger": "candle" | "interval" | None,        # 新增（回测/legacy 为 None）
    "reflection": {                                  # 新增（未触发反思轮为 None）
        "verdict": "confirm" | "abort", "vetoed": bool, "failClosed": bool,
        "reason": str, "proposedAction": str, "proposedSizeFraction": float | None,
        "proposedConfidence": float | None,
        "finalAction": str, "finalSizeFraction": float | None}}
   ```
2. 否决轮 `_context` tool_result 内容：`json.dumps({**executed, "reflection": <note>})`
   （有界小 dict，仅否决轮出现；其余轮逐字不变）。
3. 摘要素材折叠条目（仅存在于 `build_summary_prompt` 的瞬态视图，不落任何存储）：
   `{"rounds": "a-b", "repeatedRounds": n, "trigger": "interval", "note": str,
     "decision": {...}, "sessionPnl": float}`。
4. `metrics_json.aiStats` 加法键：`reflectionRuns/reflectionConfirms/reflectionAborts/
   reflectionFailClosed/wakeSkips`（int；`reflectionAborts` 只计模型真实否决，fail-closed 另计，
   R-04/R-07）。

---

## 六、Testing & acceptance criteria（测试与验收标准）

**先红后绿复现用例（修 bug 必须先能稳定复现，CLAUDE.md §七）：**

1. `test_reflection_prompt_no_blanket_abort`：`build_reflection_system_prompt()` 与
   `build_reflection_prompt(...)` 均不含「宁可错过」「任何足以令你不放心」；含新锚点
   （「实质性缺陷」「具体证据」「机会成本」/「错过…同样是损失」、「优先」收缩）。
2. `test_journal_preserves_vetoed_intent`（session 集成，桩 LLM：决策 open_long + 反思 abort）：
   `decider._journal[-1]["reflection"]["vetoed"] is True` 且 `proposedAction == "open_long"`；
   `_context` 末条 tool_result 文本含 `"reflection"`；同时保持既有断言
   `journal[-1]["decision"]["action"] == "hold"`（R-P1-3 不变式仍成立）。
   **冻结守门（R-05）**：同用例（或伴生用例）再走一轮 confirm 直通 / 未触发反思的决策，断言该轮
   `_context` tool_result 文本 == `json.dumps(executed, ensure_ascii=False)` 且**不含**
   `"reflection"`——钉死「非否决轮内容逐字不变」，防「加字段就全轮富化」型复发。
3. `test_summary_material_folds_repeated_interval_holds`：构造 5 条连续
   interval-hold journal → `build_summary_prompt` 素材中出现 `"repeatedRounds": 5` 且不再出现
   5 条独立 hold；夹在中间的开仓轮 / candle 轮 / 含 reflection 轮不被折叠。
4. `test_interval_wake_skips_when_flat_and_unchanged`：空仓+无挂单+无新确认根+价格微动
   （<0.05%）→ `_maybe_interval_wake` 不创建决策任务、`wake_skips` 恰 +1、计时器未刷新；
   同一唤醒周期窗口内紧接着再调 `_maybe_interval_wake` → 仍跳过且 `wake_skips` **不再递增**
   （R-02 计数节流）。

**零回归冻结门禁：**

5. `test_ai_trade_reflection.py` 既有全部用例**一字不改**通过（`apply_reflection`/`_map_reflection`/
   fail-closed/重试/取消/直通——确定性安全层逐字冻结）。
6. `trigger="candle"` 或缺失 trigger 的 `build_decision_prompt` 输出不含「定时唤醒」（candle 路径
   文案零漂移）；既有 prompts 用例（含收尾硬约束「决策工具。**」与「也不得调用其它工具。」）
   全绿。
7. `commit_round` 缺省调用（无 `reflection` 实参，回测/既有单测路径）行为不变；
   `reflection_enabled=False` 直接构造的决策器全链路无新副作用。
8. 守卫关闭（`ai_trade_wake_skip_unchanged_pct=0`）时定时唤醒行为与升级前**逐字相同**（含
   R-03 适配后的 `test_interval_wake_triggers_decision_between_candles`——该用例以显式阈值 0 构造、
   断言原样保留；`test_ai_trade_wake_interval.py` 其余既有用例一字不改全绿）；持仓轮与挂单轮在
   任何阈值下都不跳过。

**验收 DoD：**

- 后端 `services/quant` 全量 pytest 零回归（工作树内已知的 factor-expansion 孤儿测试红为
  pre-existing，与本特性无关，须在报告中列明而非静默）。
- 反思裁决观测：回测同一区间同一配置下否决率显著低于 1.0（不再是历史样本的 7/7=100% 否决率），
  且 `confirm+收缩` 路径可被脚本化用例触达（脚本化桩 LLM 下无 fail-closed 干扰；真实 LLM 场景以
  实盘 aiStats 四键口径为准，R-04）。
- 实盘 aiStats 出现 `reflectionRuns/Confirms/Aborts/FailClosed` 四键与 `wakeSkips`；status 出现
  `wakeSkips`。`reflectionAborts` 只计模型真实否决（fail-closed 另计），否则验收比率会被
  基础设施故障污染（R-04）。

---

## 七、Risks & mitigations（风险与缓解）

| # | 风险 | 缓解 |
|---|---|---|
| R-1 | F1 放宽反思标准 → 过度交易反向风险 | 只改 prompt 文案；全部确定性护栏（置信度/共识门禁/档位/预算/OCO/会话止损）逐字不变；abort 仍可用且标准明确；F5 计数持续观测否决率，可随时回调文案 |
| R-2 | F2 注记增大上下文/journal | 注记为有界小 dict 且仅反思轮携带；tool_result 富化仅否决轮；`_trim_context` 硬上限兜底不变 |
| R-3 | F4b 折叠丢信息 | 仅折叠「连续、interval、hold、未成交、无反思」轮；保留条数与末条决策；开/平仓、candle、反思轮永不折叠；`_journal` 本体不折叠（decision 事件/trace/历史不受影响） |
| R-4 | F4c 跳过唤醒错过急速行情 | 阈值极小（0.05%）且仅作用于**空仓+无挂单+无新 K 线**；monitor 每 ~5s 重评估，价格越阈立即恢复唤醒；新 K 线走独立触发源不受守卫影响；`0` 一键关闭 |
| R-5 | session.py 已超 800 行（pre-existing 越限），继续膨胀 | 纯逻辑全部下沉新/既有小模块（`wake_guard.py`、`reflection.py`、`prompts.py`），session 净增控制在 ~30 行编排代码内 |
| R-6 | prompt 修复效果无法确定性单测 | 文案锚点断言 + F5 观测计数 + 回测 `reflectionAborts` 比率作半定量验收出口；后续可用回测 A/B 验证 |
| R-7 | 在跑会话的内存摘要已被污染，修复不自愈 | 文档明确运维注记：升级后重启 AI-trade 会话；新会话从干净摘要起步 |
| R-8 | `_reflect_before_execute` 返回元组扩为三元 → 潜在调用点遗漏 | 全仓唯一调用点在 `_run_decision_round`（已核对）；若有直测该私有方法的用例按新签名同步修 |

---

## 八、实施过程发现的方案缺陷（预留给 Subtask #2 回填）

Subtask #2（代码开发）核验结论：**方案与仓库现状一致，无 P0/P1 级缺陷**；全部 14 个文件按
§三计划落地，五项放行条件（安全层冻结 / R-03 适配 / R-04 分流 / R-05 守门 / pre-existing 红列明）
逐条满足。以下为三条实施备注（非方案缺陷，供 Subtask #3 提交节点与后续维护参考）：

1. **I-1（提交节点必读）`config.py` 是混合文件**：工作树内该文件已含**他特性在制** hunk
   （`ai_trade_enabled` 默认值 False→True 翻转 + 注释改写，约 92-98 行），与本特性的
   `ai_trade_wake_skip_unchanged_pct` hunk（约 184-191 行）同文件共存。提交本特性时须按 hunk
   级精确暂存（`git apply --cached` 或 `git add -p`），勿把总闸翻转一并带入。其余本特性触碰的
   6 个源文件 + 7 个测试文件在实施前均为干净态，可整文件暂存。
2. **I-2 `session.py` 守卫计数节流抽为命名私有方法 `_count_wake_skip`**：§2.4 F4c 把 R-02 节流
   描述为 `_maybe_interval_wake` 内联逻辑；实施时抽为同类私有方法（语义逐字一致），以守
   CLAUDE.md 单方法 ≤50 行与嵌套深度约束——`_maybe_interval_wake` 加守卫后已近上限。守卫与
   `_deciding` 认领之间仍无 await，无竞态（§2.4 承诺保持）。
3. **I-3 守卫阈值边界单测用二进制可精确表示值**：`should_skip_interval_wake` 的「恰等于阈值 →
   不跳过」边界断言若用 30015/30000 与 0.0005 构造会踩浮点表示毛刺（商为 1.0004999…），改用
   45000/30000 与 0.5（1.5/0.5 均可精确表示）钉死严格 `<` 语义；生产语义不受影响（真实行情价
   恰落在阈值精确边界的概率为测度零，且守卫方向保守——毛刺只会多唤醒一次，不会漏唤醒）。

---

## 评审结论（Review Verdict）

**有条件通过（approved with conditions）**——v2 已就地修复全部 P0/P1（P0 = 0；P1 = 5：R-01 失实
标注、R-02 计数节流、R-03 既有用例适配、R-04 验收指标口径、R-05 冻结守门断言），无未决 P0/P1。
Subtask #2 落地时须满足以下放行条件：

1. **确定性安全层逐字冻结门禁**（§六-5）：`apply_reflection` / `_map_reflection` / fail-closed /
   abort 零重试 / 共识与置信度门禁 / 档位量化 / 预算护栏相关代码零改动，
   `test_ai_trade_reflection.py` 既有用例**一字不改**全绿；F1 仅改 prompt 文案且保留 §2.1 列出的
   全部既有锚点（含 user prompt 以「也不得调用其它工具。」收尾、system prompt 以「决策工具。**」
   收尾两条既有硬断言）。
2. **R-03 适配方式不得走样**：`test_interval_wake_triggers_decision_between_candles` 只允许以显式
   `ai_trade_wake_skip_unchanged_pct=0.0` 的 Settings 保留原行为断言，不得删除或放松断言；新增
   守卫用例须覆盖 §六-4 的计数节流分支（同窗口第二次调用不再递增）。
3. **R-04 口径落地**：实盘 aiStats 四键按「aborts = 真实否决（`degradeReason=="reflection_abort"`）、
   failClosed = 四个基础设施码」分流；回测侧 `reflection_aborts`（abort+fail-closed 合计）维持
   零改动，两侧口径差以代码注释注明，不得引入第三种口径。
4. **R-05 守门断言落地**：非否决轮 tool_result 逐字不变（== `json.dumps(executed,
   ensure_ascii=False)`、不含 `"reflection"`）的断言必须进 `test_ai_trade_session.py`。
5. 全量 pytest 验收时，工作树内已知的 factor-expansion 孤儿测试红为 pre-existing，须在报告中
   列明而非静默（§六 DoD 既有要求）；实施中发现的偏差按惯例回填 §八。
