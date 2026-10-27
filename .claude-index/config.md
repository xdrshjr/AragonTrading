# Index Configuration

## Excluded Directories
- node_modules
- .git
- .next
- out
- dist
- build
- __pycache__
- .venv
- venv
- .pytest_cache
- .mypy_cache
- .ruff_cache
- coverage
- .turbo
- .pnpm-store
- desktop/build/
- desktop/.web-build/
- desktop/resources/
- desktop/dist/
- .agentmesh/
- .bug-diagnosis/
- tmps/

## Priority Directories
- apps/web/        (Next.js frontend)
- services/quant/  (FastAPI backend)
- desktop/src/     (Electron runtime shell)
- desktop/scripts/ (desktop build and verification)
- scripts/         (workspace development orchestration)
- docs/            (planning, diagnosis, and improvement context)

## Tech Stack
- Frontend: Next.js 14 App Router + React 18 + TypeScript + Tailwind + lightweight-charts v4 + Zustand + TanStack Query
- Backend: Python 3.11 + FastAPI + SQLAlchemy async + SQLite + httpx + websockets
- Agent Copilot: httpx-based streaming LLM clients (Anthropic + OpenAI API standards; KIMI default via .env), tool registry over backend capabilities
- Desktop: Electron + PyInstaller-bundled backend + electron-builder packaging
- Language: TypeScript/TSX, Python, JavaScript/MJS

## Index Settings
- Generated: 2026-09-09 (first generated 2026-07-28)
- Project Root: M:\takoAI\AragonTrading
- Index Version: 1.1
- Environments Configured: No
- Verified Against: master @ b982652 plus the current non-excluded working tree
- Indexed Code Files: 1513 (818 source + 695 tests/examples)
- Indexed Source Files: 818 (TypeScript/TSX 415, Python 383, JavaScript/MJS 18, PowerShell 2)
- Indexed Test Files: 695 (414 backend pytest/support + 249 web vitest + 9 desktop node tests + 23 docs examples/repros)
- Extracted Public Symbols: 4542 (1974 TypeScript/JavaScript exports + 2568 Python top-level public symbols)
- Non-Excluded Files Scanned: 1832

> Regeneration notes (2026-09-09): focused re-scan of 1,832 non-excluded files
> (large-project mode), advancing the snapshot from `d17eed6` to `b982652` across
> **twelve** commits (373 files, +62,845 / -3,964), plus the uncommitted working
> tree. New source areas: 19 top-level `ai_auto/` modules (`trend_verdict` /
> `trend_runtime` / `trend_text`, `orphan`, `gear_resolution`, `liveness` /
> `liveness_text`, `truth_wire`, `trade_economics`, `accounting_replay`,
> `trade_persistence`, `open_attempts`, `decision_memory`, `advisory_evidence`,
> `integrity_view`, `entry_quota`, `numeric_text`, `profitability_sample`,
> `backtest_funding`), six `ai_auto/intel/` modules (`session_rule`, `binding`,
> `expand`, `summary`, `arming`, `netpolicy`), three `live_auto/` modules
> (`settlement`, `settlement_identity`, `execution_receipt`), four `okx/` modules
> (`inst_family`, `positions_cache`, `call_stats`, `error_http`),
> `api/truth_sources.py`, `db/ai_profitability_repositories.py`, the 11-module
> `apps/web/components/Agent/launch/` wizard, seven new `apps/web/lib/`
> modules (`aiTradeTrend`, `aiTradeGearResolution`, `aiTradeIntegrity`,
> `truthSources`, `aiTradeLaunchSteps`, `aiTradeLaunchRisk`,
> `aiTradeLaunchFormat`), five new components (`AiTradeIntelFields`,
> `AiTradeLivenessBanner`, `AiTradeOrphanBanner`, `IntelBindingStrip`,
> `IntelReaderBanner`, plus `Settings/TruthSourcesCard` and
> `Settings/IntelArmStatusCard` and three `aiTradeStats` cards), and
> `desktop/src/lib/rotating-log.js` + `desktop/scripts/dist-process-scan.ps1`.
> **Working tree (uncommitted, indexed and marked *(in-flight)*):** the
> `ai-trade-audit-journal` feature — `ai_auto/journal/` (10 modules),
> `api/ai_trade_journal.py` + `api/schemas_journal.py`,
> `db/ai_journal_repositories.py` + `db/live_auto_repositories.py`, three
> additive tables (`ai_trade_decision_logs`, `ai_trade_event_logs`,
> `ai_trade_prompt_profiles`), `apps/web/components/Journal/` (13 components),
> `apps/web/lib/journal{,Api,Export,Types}.ts`, a fourth nav view, and 16 backend
> plus 4 web test modules. API routers 21 -> 23; `ai_auto` modules 133 -> 168
> (107 top-level + 33 `intel/` + 18 `experience/` + 10 `journal/`);
> `docs/plans` 145 -> 157; `docs/diagnoses` 47 -> 48; persist `version` 30 -> 32.
> Added seven architecture bullets (trend verdict, fill truth, evidence
> integrity, realized profitability, liveness watchdog, truth sources, guided
> launch) plus the in-flight journal bullet, three feature sections (guided
> launch inside the Agent section, Trading Journal UI, Backend Trading Journal),
> four file-index sections (`Agent/launch/`, `components/Journal/`,
> `ai_auto/journal/`, plus rows across ten existing tables), eight new Key
> Conventions, and a refreshed over-800-line debt list (still 21 files;
> `Agent/AiTradeLaunchModal.tsx` **left** it at 1,718 -> 371 and
> `ai_auto/cost_runtime.py` entered it). No test suite was rerun.

> Regeneration notes (2026-08-31): focused re-scan of 1,708 non-excluded files
> (large-project mode), advancing the snapshot from `039fa80` to `d17eed6` across
> eight commits. The range adds 192 paths (117 source + 75 tests, plus docs) and
> modifies 119 existing files. New source areas: the 27-module
> `ai_auto/intel/` package, `ai_auto/{feed_runtime,feed_watchdog,gate_veto,`
> `llm_budget,prompt_glossary,prompts_en,reasoning_protocol,reasoning_runtime}.py`,
> `api/ai_intel.py` + `api/schemas_intel_{closed_loop,evidence,actionability}.py`,
> four `db/ai_intel_*_repositories.py`, the 19-component
> `apps/web/components/Intel/` directory,
> `apps/web/lib/{intel,intelApi,intelTypes,intelActionability,intelClosedLoop,`
> `intelEvidence,aiTradeFeed,aiTradeReasoning}.ts`,
> `Settings/{IntelSourceSettings,IntelReaderSettings}.tsx`,
> `Chart/{AiTradeIntelButton,AiTradeFeedRecoveryCard,AiTradeMemberRow}.tsx`,
> `Agent/{AiTradeDirectionFields,AiTradeReasoningBlock}.tsx`,
> `Agent/aiTradeStats/DirectionBalanceCard.tsx` and
> `desktop/src/lib/external-links.js`. Eight additive `ai_intel_*` DB tables
> (`create_all`; later column additions go through the `db/session.py` ALTER
> patchers). API routers 20 -> 21; `ai_auto` modules 98 -> 133 (88 top-level + 27
> in `intel/` + 18 in `experience/`); `docs/plans` 137 -> 145; `docs/diagnoses`
> stays 47. Added five architecture bullets (market intelligence, the intelligence
> closed loop, the deep reasoning chain, directional symmetry, feed self-healing,
> plus the workstation typography contract), two feature sections
> ("Market Intelligence UI", "Backend Market Intelligence"), two file-index
> sections (`components/Intel/`, `ai_auto/intel/`), ten new Key Conventions, and a
> refreshed over-800-line debt list (21 files; `ai_auto/decider.py` and
> `ai_auto/experience/service.py` crossed it this round). No test suite was rerun.

> Regeneration notes (2026-08-25): focused re-scan of 1,422 non-excluded files
> (large-project mode), advancing the snapshot from `b2697d7` to `039fa80` across
> six commits. The range adds 82 code paths (45 source + 37 tests), modifies 80
> existing code files, and adds five planning specs (`agent-panel-toolbar-two-rows`,
> `ai-trade-lifecycle-truth`, `ai-trade-experience-context-graph`,
> `ai-trade-experience-compounding`, `ai-trade-exit-priority-and-commitment`) plus
> the `ai-trade-interval-wake-dead-monitor-loop` diagnosis. New source areas: the
> 18-module `ai_auto/experience/` package, `ai_auto/{lifecycle,exit_intent,`
> `exit_priority,exit_commitment,hold_horizon_profiles}.py`, `api/experience.py`,
> `db/ai_experience_repositories.py` + `db/ai_experience_accum_repositories.py`,
> `apps/web/lib/{aiTradeExit,experience,experienceApi,experienceGraph,`
> `experienceTypes}.ts`, `Settings/ExperienceSection.tsx` + eight
> `Settings/experience/*` components, `Agent/{AiTradeExitPriorityRows,`
> `AiTradeExperienceFields}.tsx`, `Agent/aiTradeStats/ExperienceCard.tsx` and
> `Chart/AiTradeExitCard.tsx`. Six additive `ai_experience_*` DB tables (no
> migration). API routers 19 -> 20; `ai_auto` modules 75 -> 98 (80 top-level + 18
> in `experience/`); `docs/plans` 132 -> 137; `docs/diagnoses` 46 -> 47. Added an
> "AI-trade exit priority & commitment" and an "AI-trade experience context graph"
> architecture bullet, a "Backend Trading Experience Graph" feature section, a
> `services/quant/app/ai_auto/experience/` file-index section, three new Key
> Conventions (derived lifecycle / the exit lane / a compounding experience
> library), and refreshed the over-800-line debt list (19 files; `db/models.py`,
> `api/ai_auto.py` and `Agent/AiTradeTraceRound.tsx` crossed it this round, while
> `hold_horizon.py` was split back under it). Existing untracked project files
> remain indexed and untouched. No test suite was rerun.

> Regeneration notes (2026-08-17, verification refresh): re-traversed 1,316
> non-excluded files in focused large-project mode, including the two managed
> `.claude-index/` artifacts. The code inventory remains 1,067 files (597 source
> + 470 tests/examples), and symbol extraction remains 3,111. The existing
> feature map, file index, dependency map, documentation counts, and current
> untracked project files were confirmed current; no source files were changed.

> Regeneration notes (2026-08-17): focused re-scan of 1,317 non-excluded files
> (large-project mode), advancing the snapshot from `7b55d16` to `b2697d7` across
> four commits. The range adds 33 code paths (11 source + 22 tests), modifies 215
> existing code files, and adds three hardening specs (`stability-hardening-audit`,
> `resilience-and-hci-hardening`, `trust-boundary-and-signal-integrity`) plus the
> `okx-public-rest-circuit-open-never-recovers` diagnosis. New source modules:
> `app/security/{origins,origin_middleware}.py`, `app/api/ws_guard.py`,
> `app/wire_caps.py`, `apps/web/lib/{httpTimeout,wireCaps}.ts`,
> `apps/web/app/global-error.tsx`, `apps/web/components/ui/CrashFallback.tsx`,
> `desktop/src/lib/{renderer-health,startup-failure}.js`. Router count stays 19;
> `ai_auto` modules stay 75; `docs/plans` 129 -> 132; `docs/diagnoses` 45 -> 46.
> Added a "Backend Trust Boundary" and a "Design System & UI Primitives" feature
> section, six new Key Conventions, and refreshed the over-800-line debt list.
> Existing untracked project files remain indexed and untouched.

> Regeneration notes (2026-08-15): focused re-scan of 1,291 non-excluded files
> (large-project mode), advancing the snapshot from `6d9a72f` to `7b55d16`
> across one commit. The range adds 19 code paths (7 source + 12 tests), modifies
> 31 existing code files, and adds the `ai-trade-pause-resume` spec: durable
> `AiTradeGroupState` contracts, hot/cold pause and atomic fail-closed resume,
> lifecycle routes/dialogs, startup hydration, continued paused-state protection,
> and a best-effort desktop quit-pause hook. API routers 18 -> 19; `ai_auto`
> modules 73 -> 75; `docs/plans` 127 -> 129. Existing untracked project files
> remain indexed and untouched.

> Regeneration notes (2026-08-13): focused re-scan of 1,244 non-excluded files
> (large-project mode), advancing the snapshot from `df3604a` to `6d9a72f`
> across seven commits. The Git range adds 83 code paths (38 source + 45 tests),
> while `desktop/scripts/config.test.mjs` was already present and indexed as an
> untracked file in the prior snapshot: resilient
> OKX REST/WS supervision and connection telemetry, strict market-frame routing,
> AI-trade discipline presets, pre-launch tradability/open-funnel diagnostics,
> pooled profitability/calibration/track-record accounting, and round-estimate
> settlement. `ai_auto` module count 61 -> 73; API routers 17 -> 18;
> `docs/plans` 123 -> 127. Existing untracked project files remain indexed and
> untouched.

> Regeneration notes (2026-08-11): focused re-scan of 1,157 non-excluded files
> (large-project mode). Git remains at `master` @ `df3604a`; the code inventory
> remains 933 files (541 source + 392 tests/examples), and no feature-map,
> exported-symbol, dependency, entry-point, or file-index changes were required.
> Existing uncommitted and untracked project files remain indexed and untouched.

> Regeneration notes (2026-08-10): incremental refresh over `359f19a` through
> `df3604a`. Added 58 code files (25 source + 33 tests): official fee-tier and
> funding ledgers, local/exchange reconciliation, holding-horizon profiles and
> G1–G3 gate, open-side G4/G5 turnover discipline, exchange-verified G6 backstop,
> reduce-only stop repair, exit attribution, five-row acceptance, matching live/
> backtest UI, and five planning specs. `ai_auto` module count 47 -> 61;
> `docs/plans` 118 -> 123. Recounted the existing untracked priority-tree files,
> refreshed module dependencies and updated the over-800-line debt inventory.

> Regeneration notes (2026-08-02): incremental refresh over `a7ed70c`
> (ai-trade-wake-aware-context) and `9f1fc4c` (ai-trade-wake-scheduler-closed-loop).
> +18 code files: eight `ai_auto` modules (`tactical_context`, `wake_reason`,
> `wake_runtime`, `wake_text`, `market_text`, `wake_backlog`, `flip_detector`,
> `signal_preview`), two web files (`lib/aiTradeWakeAttribution.ts`,
> `Agent/aiTradeStats/WakeAttributionCard.tsx`), seven backend tests and one web
> test. `ai_auto` module count 39 -> 47; `docs/plans` 113 -> 115. Added the
> AI-trade wake-loop architecture bullet, three new Key Conventions (one wake
> object / starvation ceilings + counter landing points / additive reversible
> axes), and refreshed the over-800-line debt list (`session.py` 1494 -> 1655,
> `lib/aiTrade.ts` 969 -> 1061, `lib/store.ts` 1268 -> 1275).

> Regeneration notes (2026-08-01): file inventory re-counted and unchanged.
> Corrected `FACTOR_LIBRARY` from 20 to the actual 12 factors (the 20-factor
> expansion exists only as an orphan spec/test). Added file-level coverage for
> `apps/web/lib` agent/account/trading/settings modules, `agent/llm/` +
> `agent/tools/`, and a build/config/ops-scripts section. Recorded the 11
> files already over the 800-line ceiling and the known-red test baseline.

## Clean Code Settings
- Enabled: Yes
- Preset: strict
- MAX_FILE_LINES: 800
- MAX_METHOD_LINES: 50
- MAX_FUNCTION_PARAMS: 5
- MAX_CYCLOMATIC: 10
- MAX_LINE_LENGTH: 80
- MAX_NESTING_DEPTH: 4

> Strict preset intersected with language-aware minimums for TypeScript and
> Python: file 1000 -> 800, method 60 -> 50,
> line length 100 -> 80.

### 显式记录的阈值偏离（CLAUDE.md §二要求：调整必须在此登记，不得静默放宽）

| 位置 | 阈值 | 实际 | 理由 |
|---|---|---|---|
| `services/quant/app/ai_auto/prompts.py::build_ai_trade_system_prompt` | `MAX_FUNCTION_PARAMS=5` | 6（4 位置 + `texts` + `options`；**本轮已还**） | ai-trade-deep-reasoning-chain 兑现了上一版登记里那条「下一次触碰应一次性收进 `SystemPromptTexts` 聚合结构」的指示：13 → 6。九个旧关键字（六个文本段 + `position_step_pct` / `allow_limit` / `limit_band_pct`）保留为 `**legacy` 兼容垫片（仓库里有上百条冻结断言以关键字直连调用它），但**未知键抛 TypeError**——静默吞掉一个拼错的关键字等于「宪章章没进 prompt 且不报错」。等价性由 `tests/test_ai_trade_prompts.py::test_legacy_text_kwargs_equal_aggregated_texts` （sha256 相等）冻结。 |
| `services/quant/app/ai_auto/session.py::_emit_decision` | `MAX_FUNCTION_PARAMS=5` | 17（5 位置 + 12 关键字，不含 `self`） | 存量 wire 事件装配函数；新增领域数据已尽量挂在 `executed`/快照对象而非继续扩签名。下一次触碰应改为聚合事件对象并同时迁移调用点。 |
| `services/quant/app/ai_auto/session.py` | `MAX_FILE_LINES=800` | 3172（2026-08-29 复测；上次 3063） | 持仓周期、交易所兜底、修复、成本、验收与暂停判定均下沉独立模块；会话仍因编排、记账和生命周期接线增长。已是明确拆分债，下一次触碰应优先按决策编排、状态/事件、维护任务拆文件。**ai-trade-directional-symmetry（+109 行）**：本轮**没有**往这里塞任何纯核心（新判定全在 `gate_veto.py` / `signals.py` / `open_funnel.py`），增量是两个留存点、三个薄封装方法（`_gate_veto_note` / `_trace_veto_layer` / `direction_wire`）与注释。 |
| `services/quant/app/config.py` | `MAX_FILE_LINES=800` | 1029（2026-08-25 复测；2026-08-17 时为 839） | 2026-08-17 新越线，本轮因离场优先级与经验库两组设置继续增长：`Settings` 是「字段名即环境变量名」的单一真源，拆文件会让某个前缀家族的字段落到第二处声明点，而 `desktop/src/lib/config.js` 的前缀白名单与 `extra="ignore"` 的组合已为「字段没声明 / 前缀没转发」栽过两次。下一次触碰应按家族拆成 `config/` 包并保持单一 `Settings` 聚合，而不是简单切块。 |
| `services/quant/app/live_auto/executor.py` | `MAX_FILE_LINES=800` | 1042（2026-08-25 复测） | 存量债延续：本轮新增 `_record_trade` / `restore_trades` 与两个计数器（封顶的前置条件）。下一次触碰应先把 resting-order 路径整体外移到 `live_auto/resting_flow.py`。 |
| `services/quant/app/ai_auto/manager.py` | `MAX_FILE_LINES=800` | 1916（2026-08-28 复测；上次 1871） | 存量债延续：本轮新增分组生命周期接线与经验库调度接线。判定本身已全部下沉到 `ai_auto/lifecycle.py`（纯核心、零会话依赖）与 `experience/scheduler.py`，manager 里留下的只有取数与装配。下一次触碰应按「准入 / 生命周期 / 会话装配」三块拆文件。 |
| `services/quant/app/ai_auto/decider.py` | `MAX_FILE_LINES=800` | 1099（2026-08-28 新越线） | ai-trade-deep-reasoning-chain 新增：`_StreamOutcome` / `ReasoningMeta`、provider 归一的截断与 usage 回填、浅研判追问（独立 `reask` 相位）、轮级墙钟闸与尝试次数闸、三客户端路由、反思推理留存。判定与协议文本已全部下沉到纯核心（`reasoning_protocol.py` / `prompts_en.py` / `llm_budget.py`），留在本文件的是**编排**。下一次触碰应把「决策尝试 + 重试 + 追问」整体外移到 `ai_auto/decision_loop.py`，只保留 `decide/reflect/commit_round` 三个入口。 |
| `services/quant/app/ai_auto/prompts.py` | `MAX_FILE_LINES=800` | 1078（2026-08-29 复测；上次 979） | 同上特性：英文脊柱、三个英文纪律子句孪生、英文工具描述与收尾指令**已外移**到新模块 `prompts_en.py`（285 行），本文件只增了分叉判断与 `evidenceDigest` schema；越线来自既有的 ~150KB 中文证据渲染层（`_format_*` 家族）。下一次触碰应把 `_format_*` 整体外移到 `ai_auto/prompt_sections.py`，本文件只留 schema 与三个 `build_*` 入口。**ai-trade-directional-symmetry（+99 行）**：方向对称宪章、逆势通路那一行与`contrarianEvidence` 的 schema 段——三者都是本文件既有职责内的加法，但它们让上面那条外移指示更紧迫了。 |
| `services/quant/app/config.py` | `MAX_FILE_LINES=800` | 1126（2026-08-28 复测；上次 1029） | 存量债延续：本轮新增深度思维链九项设置 + `_check_reasoning_budget` 校验器。修法与上次登记一致（按家族拆成 `config/` 包并保持单一 `Settings` 聚合）。 |
| `services/quant/app/db/models.py` | `MAX_FILE_LINES=800` | 867 | 本轮（2026-08-25）新越线：六张 `ai_experience_*` 表全部是纯追加表（`create_all` 即可，无迁移）。ORM 模型集中在单文件是因为 `create_all` 与 `Base` 的注册顺序依赖它；下一次触碰应拆成 `db/models/` 包并在 `__init__` 里保持同一 `Base` 与同一导入面，而不是把某几张表挪去别处。 |
| `services/quant/app/api/ai_auto.py` | `MAX_FILE_LINES=800` | 890（2026-08-28 复测；上次 854） | 本轮（2026-08-25）新越线：新增手动离场端点与离场优先级投影。下一次触碰应把 AI-trade 回测相关的四个端点整体外移到 `api/ai_backtest.py`（它们与实盘路由共享的只有 settings）。 |
| `apps/web/components/Agent/AiTradeTraceRound.tsx` | `MAX_FILE_LINES=800` | 812（2026-08-28 复测；上次 844——深度思维链把 `AnalysisBlock` 整体换成了新组件 `AiTradeReasoningBlock.tsx`，本文件反而变短） | 本轮（2026-08-25）新越线：新增离场优先级两块轨迹呈现。呈现逻辑已按既有范式外移到 `AiTradeExitPriorityRows.tsx`，留在本文件的是三行装配。下一次触碰应继续把各 verdict 段落逐个抽成 `AiTradeTrace*Rows.tsx` 同族组件。 |
| `services/quant/app/ai_auto/experience/service.py` | `MAX_FILE_LINES=800` | 797（未越线，贴线） | 登记为预警而非偏离：经验库十四个端点的取数与装配全在此处，判定全部在纯核心。下一次新增端点前应先按「读视图 / 物化 / 导入导出」拆成三个模块，而不是等它越线后再拆。 |
| `services/quant/app/db/models.py` | `MAX_FILE_LINES=800` | **994**（2026-08-30 复测；上次 867） | ai-trade-market-intel：四张 `ai_intel_*` 全部是纯追加表（`create_all` 即可建，无迁移代码、老库首次启动自动获得）。修法与上次登记一致（拆成 `db/models/` 包并在 `__init__` 里保持同一 `Base` 与同一导入面）。 |
| `services/quant/app/config.py` | `MAX_FILE_LINES=800` | **1229**（2026-08-30 复测；上次 1151） | 存量债延续：本轮新增 34 项 `ai_trade_intel_*` + 一条 `drain_timeout < shutdown_step_timeout` 启动期校验器。前缀统一为 `AI_TRADE_` 是硬约束——`desktop/src/lib/config.js` 的透传白名单按前缀决定哪些 env 进得了打包后端。修法与上次登记一致（按家族拆 `config/` 包并保持单一 `Settings` 聚合）。 |
| `services/quant/app/ai_auto/session.py` | `MAX_FILE_LINES=800` | **3220**（2026-08-30 复测；上次 3172） | ai-trade-market-intel 只增**四处薄接线**（payload 一键、`snapshot` 第八键、`push_intel` 一方法、只读 `change_24h_pct`），**零纯核心**——情报层的全部逻辑住在 `ai_auto/intel/` 包（12 模块）。既有拆分指示不变。 |
| `services/quant/app/ai_auto/manager.py` | `MAX_FILE_LINES=800` | **2032**（2026-08-30 复测；上次 1952） | 存量债延续：本轮只增六处情报层薄转发（wire / unwire / 两个回调 / drain / drain 死线），判定全在 `intel/runtime.py` 与 `intel/scheduler.py`。既有拆分指示（按「准入 / 生命周期 / 会话装配」三块拆）不变。 |
| `services/quant/app/ai_auto/prompts.py` | `MAX_FILE_LINES=800` | **1098**（2026-08-30 复测；上次 1078） | 本轮只增 `_format_intel`（三行 + docstring）与 `build_decision_prompt` 里的一处插入；渲染全文住 `intel/disclosure.py`。既有指示「把 `_format_*` 整体外移到 `ai_auto/prompt_sections.py`」**仍未兑现且更紧迫**——本轮不兑现的理由是：那次外移会触碰上百条 sha256 冻结断言，与本特性混在一起会让 G-6 的零回归判据失去说服力。 |
| `apps/web/components/Chart/AiTradePanel.tsx` | `MAX_FILE_LINES=800` | **571**（已还；上次 799，距阈值只剩 1 行） | ai-trade-market-intel 的**前置步骤**：`MemberRow` 与五个 chip 整体外移到 `AiTradeMemberRow.tsx`（258 行，纯搬运、零签名变更），把这个贴线文件降回安全区。这不是副产品——不先还这笔债，本特性的第一行代码就会越线。 |
| `services/quant/app/ai_auto/intel/**` + `db/ai_intel_repositories.py` + `api/ai_intel.py`（15 个函数） | `MAX_FUNCTION_PARAMS=5` | 6–11 | ai-trade-market-intel：全部超出的形参**都是 keyword-only**（`*` 之后），位置参数一律 ≤2。CLAUDE.md §二对本阈值的原话是「多了请改用配置对象、**命名参数**或聚合结构体，**禁止靠位置堆参数**」——本族取的正是「命名参数」这条许可，调用点因此全部自解释且**不可能**因为顺序写错而静默传错值。已聚合的部分另走结构体（`IntelLimits` 12 个阈值 / `IntelDeps` 8 项依赖 / `CurationAction` / `MergeOp`），故这里剩下的是真正互相独立的量。行数阈值本轮**零越线**（`MAX_METHOD_LINES=50` 机械核对通过）。 |
| `services/quant/app/ai_auto/wake_guard.py::should_skip_interval_wake` | `MAX_FUNCTION_PARAMS=5` | 7（全关键字） | 存量债，reflection-hold-inertia-fix F4c 引入时未登记，本次regeneration 补录。新增的同族函数 `should_skip_wake(wake, SkipPolicy)` 已改用聚合结构体（2 个形参），旧函数保留是因为它被既有冻结断言逐字调用；两者语义不同（前者按原始读数判、后者按已判定的 `wake.kind` 判），**不可合并**，但旧函数在下次触碰时应随调用点一起收编进 `SkipPolicy`。 |
| `services/quant/app/db/models.py` | `MAX_FILE_LINES=800` | **1065**（2026-08-30 复测；上次 994） | ai-trade-intel-closed-loop：新表 `AiIntelCitation` + `ai_intel_items` 补 10 列 + `ai_intel_watch` 补 2 列。补列**必须**走 `db/session.py` 的第五个 patcher（`create_all` 不给既有表补列）。修法与上次登记一致（拆成 `db/models/` 包并在 `__init__` 里保持同一 `Base` 与同一导入面）。 |
| `services/quant/app/config.py` | `MAX_FILE_LINES=800` | **1276**（2026-08-30 复测；上次 1229） | 存量债延续：本轮新增 17 项 `ai_trade_intel_*`（34 → 51）与两条启动期校验器（`verify_max_items × verify_docs_per_item ≤ corpus_max_docs`、`corpus_max_docs ≤ 99`）。前缀统一为 `AI_TRADE_` 仍是硬约束，`desktop/scripts/config-passthrough.test.mjs` 本轮把它钉住了。修法不变。 |
| `services/quant/app/ai_auto/session.py` | `MAX_FILE_LINES=800` | **3264**（2026-08-30 复测；上次 3220） | ai-trade-intel-closed-loop 只增**七处薄接线**（构造 `IntelAttribution`、`note_decision` / `note_executed` / 开平两钩子 / `end_round` / `stop`、`tool_input["intelRefs"]`、`aiStats.intel` 合并），**零纯核心**——判定全在 `intel/citation.py` 与 `intel/attribution.py`。既有拆分指示不变。 |
| `services/quant/app/ai_auto/decider.py` | `MAX_FILE_LINES=800` | **1215**（2026-08-30 复测；上次 1099） | 本轮只增 `TradeDecision.intel_refs` 一个末位字段、`_resolve_intel_refs` 一个解析器、两条 return 带参与两个构造参。既有指示（把「决策尝试 + 重试 + 追问」外移到 `ai_auto/decision_loop.py`）不变。 |
| `services/quant/app/ai_auto/prompts.py` | `MAX_FILE_LINES=800` | **1116**（2026-08-30 复测；上次 1098） | 本轮只增 `allow_intel_refs` / `intel_max_refs` 两个形参、早退判据一项与 `intelRefs` 的 schema 段。**「把 `_format_*` 外移到 `ai_auto/prompt_sections.py`」这条指示本轮第三次未兑现**——理由仍是「那次外移会触碰上百条 sha256 冻结断言，与本特性混在一起会让 G-1 的零回归判据失去说服力」。**本轮把它升级为硬要求：下一个触碰本文件的特性必须先还这笔债**（评审留意事项 2）。 |
| `services/quant/app/ai_auto/intel/gate.py` | `MAX_FILE_LINES=800` | **710**（2026-08-30 复测；上次 611，未越线但逼近） | closed-loop 新增：`IntelItem` 10 个字段、`score_item` 的 cite 加分、`solve_confidence` 的 trust 先验、`plan_cleanup` 的存量扫除（第 5 步）与 `review_actions` 的举证责任。核查的复核表刻意放 `intel/verify.py` 而**不是**这里。**预警登记：下一次触碰前先把 `plan_cleanup` 家族外移到 `intel/cleanup.py`。** |
| `services/quant/app/ai_auto/intel/service.py` | `MAX_FILE_LINES=800` | **676**（2026-08-30 复测；上次 564，未越线） | closed-loop 新增：`verify_due`、`item_from_wire` 的 10 个新字段、`IntelDeps.verify_llm`、`INTEL_SETTING_BRIDGE` 的 8 行、整理轮的存量扫除与引用裁剪、`read_summary` 的两块新读数。核查轮的编排整体外移到 `service_verify.py`（200 行），这正是不让它越线的处置。 |
| `services/quant/app/ai_auto/intel/search.py` | `MAX_FILE_LINES=800` | **535**（2026-08-30 复测；上次 347，未越线） | closed-loop 轴 D：`BraveProvider` / `SerperProvider` 与共用的 `_HttpProvider` 重试骨架、`_parse_relative_date`。`_Breaker` / `SearchRunner` / `corpus` 一行未改。 |
| `services/quant/app/db/ai_intel_repositories.py` | `MAX_FILE_LINES=800` | **677**（2026-08-30 复测；上次 610，未越线） | closed-loop 新增：`intel_item_out` 的 10 键、`intel_watch_out` 的 2 键、三分支 `bump_intel_watch`、`list_intel_trusted_domains` / `list_intel_feedback`。引用明细 / 影响力 / 信源聚合 / 判定四组查询整体外移到新文件 `db/ai_intel_citation_repositories.py`（474 行），这正是不让它越线的处置。 |
| `services/quant/app/api/ai_intel.py` | `MAX_FILE_LINES=800` | **497**（2026-08-30 复测；上次 276，未越线） | closed-loop 新增 11 个端点。出参模型已外移到 `api/schemas_intel_closed_loop.py`（143 行）。**预警登记：下一轮再加端点前，先把信源治理族（#13-#17）整体外移到 `api/ai_intel_sources.py`。** 另有一条不可省的接线约束已写进文件注释：**字面量路径必须声明在 `/intel/{instId}` 之前**，否则 `GET /intel/feedback` 会被 `{instId}` 静默劫持。 |
| `services/quant/app/ai_auto/intel/{attribution,verify,service_verify}.py` + `db/ai_intel_citation_repositories.py`（9 个函数） | `MAX_FUNCTION_PARAMS=5` | 6–9 | ai-trade-intel-closed-loop：全部超出的形参**都是 keyword-only**（`*` 之后），位置参数一律 ≤2 —— 与上一行 `intel/**` 的同族登记同一条许可（CLAUDE.md §二明许「命名参数」）。已聚合的部分另走结构体（`VerdictPlan` / `PendingCitation` / `CitationResolution`）。 |
| `apps/web/components/Intel/IntelModal.tsx` | `MAX_FILE_LINES=800` | **492**（2026-08-30 复测；上次 426，未越线） | closed-loop：页签从 5 扩到 7。两个新页签各自独立成文件（`IntelImpactTab.tsx` 188 行 / `IntelSourcesTab.tsx` 214 行）正是为此。 |
| `services/quant/app/ai_auto/decider.py` | `MAX_FILE_LINES=800` | **1215**（2026-08-31 复测；上次 1099） | 存量债延续：本轮只增情报引用解析与方向对称的两处接线。既有指示（把「决策尝试 + 重试 + 追问」外移到 `ai_auto/decision_loop.py`，只留 `decide/reflect/commit_round` 三个入口）**本轮仍未兑现**。 |
| `services/quant/app/ai_auto/experience/service.py` | `MAX_FILE_LINES=800` | **804**（2026-08-31 新越线；上一版已以「797、贴线」登记为预警） | 预警兑现：上一版写的「下次新增端点前先拆」没有先于越线发生。修法不变：按「读视图 / 物化 / 导入导出」拆成三个模块，而不是等它继续长。 |
| `services/quant/app/db/models.py` | `MAX_FILE_LINES=800` | **1236**（2026-08-31 复测；上次 1065） | ai-trade-intel-evidence-grounding + actionability：新表 `ai_intel_documents` / `ai_intel_quotes` / `ai_intel_alerts` 均为纯追加表（`create_all` 即建）；往**已存在**的 `ai_intel_*` 补列仍必须走 `db/session.py` 的 ALTER patcher。修法与上次登记一致（拆成 `db/models/` 包并保持同一 `Base` 与同一导入面）。 |
| `services/quant/app/config.py` | `MAX_FILE_LINES=800` | **1384**（2026-08-31 复测；上次 1276） | 存量债延续：`ai_trade_intel_*` 累计约 100 项，另增深度思维链与行情自愈两族。前缀统一为 `AI_TRADE_` 仍是硬约束（`desktop/scripts/config-passthrough.test.mjs` 钉住）。修法不变。 |
| `services/quant/app/ai_auto/session.py` | `MAX_FILE_LINES=800` | **3390**（2026-08-31 复测；上次 3264） | 本轮四个特性向它只加**薄接线**：`self._feed = FeedRuntime(self)` 一行与 `_check_staleness` 一处调用、八道门禁的 `gate_veto` 注记、情报告警唤醒的一处入口、`ReasoningMeta` 的聚合转发——判定全在 `feed_watchdog` / `gate_veto` / `intel/alert` / `reasoning_runtime` 四个纯核心里。既有拆分指示（按决策编排 / 状态事件 / 维护任务拆文件）不变。 |
| `services/quant/app/ai_auto/manager.py` | `MAX_FILE_LINES=800` | **2058**（2026-08-31 复测；上次 2032） | 存量债延续：本轮只增四处情报层薄转发与一条 drain 死线。既有拆分指示不变。 |
| `services/quant/app/ai_auto/prompts.py` | `MAX_FILE_LINES=800` | **1116** | 数字与上一版相同（本轮未再增长），**但上一版把它升级为硬要求的那条仍未兑现**：把 `_format_*` 家族整体外移到 `ai_auto/prompt_sections.py`。本轮未兑现的理由仍是「那次外移会触碰上百条 sha256 冻结断言」。**下一个触碰本文件的特性必须先还这笔债（第四次登记）。** |
| `services/quant/app/ai_auto/intel/gate.py` | `MAX_FILE_LINES=800` | **778**（2026-08-31 复测；上次 710，**未越线但只剩 22 行**） | 预警登记（第二次）。本轮已把 `plan_cleanup` 家族外移到 `intel/cleanup.py`、`_review_*` 外移到 `intel/review.py`（两次都是纯搬运、零语义变化），它仍因闸⑦ 与置信度求解的新字段长回来。**下一个触碰本文件的特性必须先把置信度求解外移到 `intel/confidence.py`。** |
| `services/quant/app/ai_auto/intel/service.py` | `MAX_FILE_LINES=800` | **766**（2026-08-31 复测；上次 676，未越线但逐近） | 预警登记。本轮已向外移出 `service_curate.py`（280）/ `service_verify.py`（265）/ `evidence.py`（175）/ `settings_bridge.py`（105）四个模块，留在本文件的是采集轮编排。下一次新增轮型前先把 `IntelDeps` 与共用工具函数外移到 `intel/deps.py`。 |
| `services/quant/app/ai_auto/reasoning_protocol.py`、`intel/{taxonomy,search,runtime}.py`、`db/ai_intel_repositories.py` | `MAX_FILE_LINES=800` | 473 / 519 / 538 / 455 / 712（均未越线） | 登记为参考值：本轮新增的十三个最大模块全部在阈值内落地，拆分发生在到线时而不是事后。 |
| `services/quant/app/ai_auto/{feed_watchdog,feed_runtime,gate_veto,llm_budget,reasoning_runtime}.py` 与 `intel/{alert,conflict,efficacy,verify,citation,attribution,quotes,extract}.py`（12 个函数） | `MAX_FUNCTION_PARAMS=5` | 6–10 | 与已登记的 `intel/**` 同族同一条许可：超出的形参**全部是 keyword-only**（`*` 之后），位置参数一律 ≤2，CLAUDE.md §二 明许「命名参数」。已聚合的部分另走结构体（`FeedReadings` / `DepthProfile` / `AlertPlan` / `VerdictPlan` / `EfficacyPrior`）。`MAX_METHOD_LINES=50` 本轮机械核对通过。 |
| `apps/web/lib/aiTradeTypes.ts` | `MAX_FILE_LINES=800` | **2147**（2026-08-31 复测；上次 1923） | 存量债延续。情报层的 wire 类型**没有**往这里塞，而是单独落在 `lib/intelTypes.ts`（551 行）——下一次触碰本文件时应同样按特性家族拆出 `aiTrade*Types.ts`。 |
| `apps/web/components/Chart/AiTradePanel.tsx` | `MAX_FILE_LINES=800` | **571**（已还，保持） | 上一版登记的还债保持住了：本轮的四个新挂载点（情报按钮 / 恢复卡 / 方向展示 / 成员行）全部以独立组件文件落地，对本文件的净增被压到「一行 import + 一处挂载」。 |
| `services/quant/app/ai_auto/prompts.py` | `MAX_FILE_LINES=800` | **1116 → 1236**（2026-09-01 复测；净增 120 行） | **第五次登记（ai-trade-execution-trend-verdict，评审 RP1-5 允许的那条退出口）**：上一版把「把 `_format_*` 家族整体外移到 `ai_auto/prompt_sections.py`」升级为「下一个触碰本文件的特性必须先还」的硬要求，本特性正是那个特性，**本轮仍未兑现**。理由如实写在这里，不静默跳过：① spec §十 第 0 步要求那次外移是**单独一个提交**，而本实施节点被显式禁止 `git commit`（提交是下游节点的职责），一次「零行为变更的独立提交」在本节点物理上不可能产生；② 把纯搬运与本特性的行为改动混进同一份工作树，恰恰会让 spec §7.6 的 G-1 / S-5「总闸关 ⇒ 决策 prompt sha256 与合入前逐字节相同」失去全部说服力——那正是 RP1-5 自己写明要避免的事。**本轮对本文件的净增被刻意压到最小**：新增 `format_trend_charter`（软引导第七段宪章）、`_trend_read_order`（决策纪律 #1 读序子句，两态各一份逐字全文）、`build_decision_prompt`里一段 3 行的插入、以及 `build_reflection_prompt` 里一处条件化子句；**《本轮趋势基调》的整段渲染文案外移到新文件 `ai_auto/trend_text.py`**（经延迟导入消费，与 `market_text` / `cost_text` /`wake_text` 同款）。**下一个触碰本文件的特性仍须先还这笔债（第五次登记）。** |

| `services/quant/app/ai_auto/session.py` | `MAX_FILE_LINES=800` | **3390 → 3498**（2026-09-01 复测；净增 108 行，其中约 60 行是本仓库强制的「为什么」注释） | ai-trade-execution-trend-verdict 向它只加**薄接线**：构造 `TrendRuntime` 与 `TrendVerdictRule`、payload 注入 `trendVerdict`、`_apply_trend_bias_gate`（≤12 行封装）、`_apply_consensus_gate` 两个放宽入参、两相位各一个新键、`_status_payload` 一键、以及 `note_submitted` / `note_fill` / `note_settled` 三处成交钩子——判定全在 `trend_verdict.py`（纯核心）与 `trend_runtime.py`（会话门面）两个新文件里。**净增预算据实登记为 108 行**（评审 RP2-7 已指出 v1 那个「≤30 行」的数字在补齐 RP0-5 的认领表、RP1-1 的判据与 RP1-2 的status 键之后不现实；照实登记优于为守住一个报出来的数字而把纯核心塞进会话）。既有拆分指示（按决策编排 / 状态事件 / 维护任务拆文件）不变。 |

| `services/quant/app/ai_auto/signals.py::_consensus_wire` | `MAX_FUNCTION_PARAMS=5` | **6 → 2（已还）** | 评审 RP2-1 的还债登记：本轮共识 wire 新增五键（`trendBonusVotes` / `effectiveVotes` / `trendBonusLane` / `trendOpposingAllowance` / `effectiveMaxOpposingVotes`），照旧写法会到 9 个位置形参。故同轮把它收成一个 frozen 聚合入参 `ConsensusWireFacts`，签名降为`_consensus_wire(lane, facts)`。**这条偏离本轮已还清，不再登记为偏离。** |

| `services/quant/app/config.py` | `MAX_FILE_LINES=800` | **1384 → 1430**（2026-09-01 复测） | 存量债延续：本轮新增 10 项 `ai_trade_trend_*`。前缀统一为 `AI_TRADE_` 仍是硬约束（`desktop/scripts/config-passthrough.test.mjs` 钉住）。修法不变。 |

| `apps/web/lib/aiTradeTypes.ts` | `MAX_FILE_LINES=800` | **2147 → 2262**（2026-09-01 复测） | 存量债延续：本轮新增 `TrendVerdict` / `TrendGateWire` / `TrendStats` / `AiTradeTrendRule` 四组类型与四处 round-trace 结构的加行。中文化纯函数另落在新文件 `lib/aiTradeTrend.ts`。下一次触碰本文件时应按特性家族拆出 `aiTrade*Types.ts`。 |

| `services/quant/app/ai_auto/{trend_verdict,trend_runtime,trend_text}.py` | `MAX_FILE_LINES=800` | 692 / 266 / 161（均未越线） | 登记为参考值：ai-trade-execution-trend-verdict 的**全部纯核心与会话状态**都落在这三个新建文件里，`session.py` 只拿到薄接线。`trend_verdict.py` 的 692 行里约 300 行是模块与函数 docstring（三条不变式、三个必踩的坑、两条评审回归护栏各自的成因），这是本仓库对「判据文件」的既有密度。 |

| `services/quant/app/ai_auto/{signals,discipline_view,backtest,manager}.py` | `MAX_FILE_LINES=800` | 662 / 433 / 1629 / 2129 | 前两个未越线（本轮各 +57 / +35）；后两个是**存量债延续**，本轮只增薄接线（回测四处 + 装配两段），既有拆分指示不变。 |

### ai-trade-silent-degradation-repair（2026-09-02，本轮登记）

> CLAUDE.md §二原文：「所有阈值都允许根据项目实际情况调整，但**调整必须在 `.claude-index/config.md`
> 中显式记录**，并在评审中说明理由，不要靠默认值偷偷放宽。」本轮往**五个已越 800 行红线的文件**
> 上继续加行，故逐个登记（评审 P1-1）。

| 位置 | 阈值 | 实际 | 理由 / 下一次触碰应先还的债 |
|---|---|---|---|
| `services/quant/app/live_auto/executor.py` | `MAX_FILE_LINES=800` | **1059 → 1142**（净增 83，其中约 50 行是本仓库强制的「为什么」注释） | 轴 C 只加**接线**：`_read_positions_fresh` 属性、`_last_private_write_wall` / `_positions_taken_wall` 两个计数、`_note_private_write` 与 `_snapshot_outranks_last_write` 两个 ≤10 行的封装、四个私有写成功点各一行。**判定全在新模块 `okx/positions_cache.py`**（单飞 / TTL / 指纹分桶）。既有指示不变：resting-order 路径整体外移到 `live_auto/resting_flow.py`。 |
| `services/quant/app/ai_auto/session.py` | `MAX_FILE_LINES=800` | **3756 → 3791**（净增 35） | 本轮只加两处：`_read_exchange_position` 换成 `_read_positions_fresh()`（轴 C，一行）与 `_truth_sources_key()`（轴 E，≤20 行的薄封装，四行判定全在 `ai_auto/truth_wire.py`）。**零纯核心。** 既有指示不变：按「决策编排 / 状态·事件 / 维护任务」拆文件。 |
| `services/quant/app/ai_auto/manager.py` | `MAX_FILE_LINES=800` | **2211 → 2302** | 本轮加三处：`_bounded_fee_rate` 带回 `FeeFetchOutcome`（轴 A-5）、`fetch_live_positions(force=True)`（轴 C）、`refresh_fee_rates()` 与模块级纯函数 `_fee_rates_receipt`（轴 A-4）。**零纯核心**（判定在 `okx/types.py` 的两个 frozen dataclass 与 `ai_auto/fee_rates.py` 里）。既有指示不变：按「准入 / 生命周期 / 会话装配」三块拆。 |
| `services/quant/app/config.py` | `MAX_FILE_LINES=800` | **1470 → 1555** | 存量债延续：本轮新增 12 个键 + 一条跨字段校验器（`_check_silent_degradation_repair`）。前缀硬约束（`OKX_` 3 个 / `AI_TRADE_` 9 个）由 `desktop/scripts/config-passthrough.test.mjs` 的**反向**探针钉住——一个无前缀的键必须穿不过去。修法不变：按家族拆 `config/` 包并保持单一 `Settings` 聚合。 |
| `services/quant/app/okx/trading.py` | `MAX_FILE_LINES=800` | **844 → 976** | 本轮加：`fetch_trade_fee_ex` / `_redact_attempt`（轴 A-5）、`fetch_live_positions` 的三个形参与 `fetch_live_positions_at`（轴 C）、`_drop_positions_cache` 与五处调用（轴 C-3）、`_normalize_trade_fee` 的 `rung` 形参（轴 A-2）。**新纯核心一律外置**（`error_http.py` / `inst_family.py` / `positions_cache.py` / `types.py`），正是 `error_text.py` 当年的同一条取舍。下一次触碰应把 `fetch_*` 家族按「读 / 写」拆成两个模块。 |

| `apps/web/lib/aiTradeTypes.ts` | `MAX_FILE_LINES=800` | **2347 → 2378** | 存量债延续：本轮新增 `AiTradeTruthDigest` 与三处加行（`feeRates` 两处、`truthSources` 两处）。轴 A-4 / 轴 E 的**纯函数**另落在新文件 `lib/truthSources.ts`（98 行），费率类型扩展落在既有的 `lib/aiTradeCost.ts`。既有指示不变：按特性家族拆出 `aiTrade*Types.ts`。 |
| `services/quant/app/api/ai_auto.py` | `MAX_FILE_LINES=800` | **1066 → 1090** | 存量债延续：本轮只增一个端点（`POST /ai-trade/fee-rates/refresh`，≤20 行 + docstring），判定全在 `manager.refresh_fee_rates`。既有指示不变：把 AI-trade 回测的四个端点整体外移到 `api/ai_backtest.py`。 |
| `services/quant/app/schemas.py` | `MAX_FILE_LINES=800` | **2991 → 3002** | 存量债延续：本轮只增 `IntelSettingsIn.readerResolver` 与 `IntelSettingsOut` 的三个字段。 |

**未越线但需预警**：`app/ai_auto/intel/reader.py` 326 → **504**（轴 B-1 的四值判定 + 对端复核 +
拒因计数器；终审复测 508）。纯核心（`classify_addr` / `classify_resolution` / `normalize_peer` / `parse_cidrs`）
已按 F-5 外置到 `intel/netpolicy.py`，此处只留接线与两个 ≤25 行的谓词。

**登记为参考值（本轮 7 个新建后端模块全部在阈值内落地）**：
`okx/error_http.py` 116 / `okx/inst_family.py` 36 / `okx/positions_cache.py` 159 /
`okx/call_stats.py` 235 / `ai_auto/intel/netpolicy.py` 189 / `ai_auto/truth_wire.py` 206 /
`api/truth_sources.py` 85。前端 3 个 + 桌面 1 个同样全部在阈值内。
（读数为**终审后**的复测值：终审删掉了 `trading.fetch_trade_fee` 这个零调用点的薄封装与
`CallStats._first_slot` 僵尸状态，`positions_cache.py` 则因作废代次与领飞者取消两条语义各加了
一段「为什么」——见 spec §I-19。）

**共用车道的偏离（评审须确认，本轮显式登记）**：`live_auto/executor.py` 与
`live_auto/manager.py` 被 AI-trade 与 live-auto **两条车道共用**。轴 C 的改动因此同时作用于
live-auto——这是**有意的**（同样的 5× 压降对 live-auto 一样成立），并由
`AI_TRADE_POSITIONS_SNAPSHOT_TTL_S=0` 一键回退两条车道。


### ai-trade-fill-truth-and-effective-controls（成交真值化与「真正会生效」的风控层）

| 位置 | 阈值 | 实际（`wc -l` 实测） | 理由 |
|---|---|---|---|
| `services/quant/app/live_auto/executor.py` | `MAX_FILE_LINES=800` | **1546**（2026-09-02 复测；上次 1142） | 轴 A 的 `settling` 状态机（侦测 / 轮询 / 死线 / 幂等落账 / 停手兜底）+ 轴 B 的孤儿对称分支 + 轴 E 的 TTL 时钟修复与三计数。判定全部在三个新纯核心（`live_auto/settlement.py` / `ai_auto/orphan.py` / `ai_auto/gear_resolution.py`），本文件只留取数与装配。**既有指示「先把 resting-order 路径整体外移到 `live_auto/resting_flow.py`」本轮第二次未兑现**——理由是那次外移会把轴 A/E 的零回归判据搅浑（`_prepare_resting_slot` 与 `_on_reconcile_ok` 在同一条 `reconcile` 调用序上）。**升级为硬要求：下一个触碰本文件的特性必须先还这笔债。** |
| `services/quant/app/ai_auto/session.py` | `MAX_FILE_LINES=800` | **3959**（2026-09-02 复测；上次 3791） | 只增薄接线：`_status_payload` 四键（`settling` / `orphan` / `sampleScope` / `contrarianBlocked`）+ `_inject_explain_meta` 消费轮级槽 + 开仓分支一处孤儿硬拒 + 停手收尾一步 drain + `adopt_orphan` 一个用户入口。**零纯核心**。既有拆分指示（按决策编排 / 状态事件 / 维护任务拆文件）不变。 |
| `services/quant/app/config.py` | `MAX_FILE_LINES=800` | **1642**（2026-09-02 复测；上次 1555） | 本轮 +13 项 `ai_trade_*` + 一条跨字段校验器（`settlement_poll_interval_s < settlement_deadline_s`）。前缀统一为 `AI_TRADE_` 仍是硬约束。修法与既有登记一致（按家族拆 `config/` 包并保持单一 `Settings` 聚合）。 |
| `apps/web/lib/aiTradeTypes.ts` / `apps/web/lib/aiTrade.ts` | `MAX_FILE_LINES=800` | **2489 / 1626**（2026-09-02 复测；上次 2378 / 1613） | 只增类型与两处常量表（5% 档位、孤儿降级码标签）。档位分辨率的判定**刻意新建** `aiTradeGearResolution.ts`（123 行）而不是塞进 `aiTrade.ts`——它是一份与后端逐格冻结的孪生，混进通用工具文件会让那道冻结门禁的作用面变得不可界定。 |
| `services/quant/app/ai_auto/trend_verdict.py` | `MAX_FILE_LINES=800` | **736**（2026-09-02 实测，**未越线**） | 轴 D 只增 `TrendVerdictRule.gear_downgrade` 一个字段 + 把三行降档改成一次 `downgrade_one_gear` 调用（**净行数基本持平**）+ wire 一个 `downgradeLane` 键（终审删掉了 spec 原列的两个格号键，见 spec I-23）。判定下沉到 `gear_resolution.py`，本文件不承接任何新逻辑。登记它是因为 spec §十二 点名要求复测。 |

**四个函数的形参数量**（本轮**不申请** `MAX_FUNCTION_PARAMS` 偏离）：`match_settlement` 2+2、
`plan_resting_action`（**本轮推迟，见 spec I-1**）、`describe_gear_resolution` 1+2、
`downgrade_one_gear` 1+3 —— 位置参数均 ≤2，其余 keyword-only。

`manager._build_executor` 今天已是 **6 个形参**（既有偏离）。轴 A 的 `settlement_fn` **在函数体内
用已在作用域的 `creds` / `inst_id` 闭包构造，不新增形参**——不把一条既有偏离推得更远，是本轮对
这条阈值的全部承诺。

**共用车道的偏离（本轮显式登记）**：`live_auto/executor.py` 被 AI-trade 与 live-auto 两条车道共用。
轴 A / 轴 B 的两条新支路都由**注入缝**门控（`settlement_fn` / `settings.ai_trade_orphan_*`），而
`live_auto` 从不传那条缝、也从不装配 `FeeLedger` ⇒ 两轴对 live-auto **构造性地不存在**。唯一真的
同时作用于两条车道的是**轴 E 的 TTL 时钟修复**（`ai_trade_resting_ttl_wall_clock`）——但 live_auto
走的是市价路径、`self.resting` 恒为 `None`，故它在那条车道上同样是空转。


### ai-trade-intel-session-binding（会话级情报绑定与渐进式披露）

| 位置 | 阈值 | 实际（`wc -l` 实测） | 理由 |
|---|---|---|---|
| `services/quant/app/ai_auto/session.py` | `MAX_FILE_LINES=800` | **4162**（2026-09-03 终审复测；实施期 4134，上次 3959） | 本轮只增薄接线：`_intel_payload` / `_note_intel_round` / `_commit_intel_seen` / `_reset_intel_seen` / `_note_intel_expand` / `_intel_mode` 六个短方法 + 四块会话内状态 + 三处调用行。**零纯核心**——横向档、披露计划、展开段的判定全在 `intel/disclosure.py` 与 `intel/expand.py`。既有拆分指示（按决策编排 / 状态事件 / 维护任务拆文件）不变。本轮的自我约束是「新增的长文本一律住在新模块里」，已兑现。 |
| `services/quant/app/config.py` | `MAX_FILE_LINES=800` | **1696**（2026-09-02 复测；上次 1642） | 本轮 +9 项 `AI_TRADE_INTEL_*` + 一条跨字段校验器（`repeat_max_chars ≤ min(index_max_chars, prompt_max_chars)`）。前缀统一为 `AI_TRADE_` 仍是硬约束（桌面壳按前缀透传，prod 后端只读进程 env）。修法与既有登记一致（按家族拆 `config/` 包并保持单一 `Settings` 聚合）。 |
| `services/quant/app/ai_auto/manager.py` | `MAX_FILE_LINES=800` | **2389**（2026-09-02 复测；上次 2388） | 只增两个 dataclass 字段 + `_intel_receipt` 一个 6 行静态方法 + `_on_intel_alert` 的一处短路。判定全在 `intel/` 包内。 |
| `services/quant/app/schemas.py` | `MAX_FILE_LINES=800` | **3044**（2026-09-02 复测） | 只增 `AiTradeIntelIn`（2 个字段）+ `AiTradeGroupStartIn.intel` + `IntelSettingsIn/Out` 各五个键。既有登记的修法（按家族拆 `schemas/` 包）不变。 |
| `apps/web/lib/aiTradeTypes.ts` | `MAX_FILE_LINES=800` | **2534**（2026-09-02 复测；上次 2489） | 只增三个接口与一个默认值常量。 |
| `apps/web/components/Agent/AiTradeLaunchModal.tsx` | `MAX_FILE_LINES=800` | **1938**（2026-09-02 复测；上次 1907） | 本轮 **+31 行全是接线**（一个 state、一个 prop、一个提交键、一处挂载）。新卡片按既有约定住在独立文件 `AiTradeIntelFields.tsx`（208 行，阈值内）。既有拆分指示不变。 |

**本轮 4 个新建后端模块 + 2 个新建前端组件全部在阈值内落地**：
`ai_auto/intel/session_rule.py` 85 / `ai_auto/intel/binding.py` 266 / `ai_auto/intel/expand.py` 150 /
`ai_auto/intel/summary.py` 157（纯搬运）；`components/Agent/AiTradeIntelFields.tsx` 217 /
`components/Intel/IntelBindingStrip.tsx` 87。终审在 `components/Chart/AiTradePanel.tsx`
（697 行，**未越线**）上补了 R-3 的那个读点。

**本轮**为守住阈值做了三次「再下沉一层」（**没有放宽任何阈值**，spec 放行条件 C-8）：

1. `intel/service.py` 783 → 771（终审 R-5 +4 行）：`read_summary` 整段搬进新建的 `intel/summary.py`，`service` 末尾原样重新导出，三个既有 `intel_service.read_summary(...)` 调用点零改动；
2. `intel/runtime.py` 774 → 799：`_static_summary` 搬进 `summary.py`（与 `read_summary` 同为 `AiTradeIntelSummary` 的装配点）、置顶的两个纯函数（`set_pin` / `live_pin`）与四个模块级会话侧入口（`note_disclosure` / `intel_limits_for` / `binding_for` / `pin_item_id`）住进 `intel/binding.py`、`ARM_NOT_RUNNING` 与 `arm_result` / `arm_result_of` 搬进 `intel/arming.py`（那里本来就是「装配状态」的家）；
3. `intel/disclosure.py` 275 → 549：纵横两个维度的判定全部落在这里而不是散进 `session.py`（**未越线**）。

**形参数量**（本轮**不申请** `MAX_FUNCTION_PARAMS` 偏离）：`resolve_session_rule` 2+1、
`plan_disclosure` 1+3、`plan_expand` 2+2、`build_binding` 2、`last_round_wire` 0+4、
`set_pin` 3+2 —— 位置参数均 ≤3，其余 keyword-only。

**一处既有偏离被推远了一格并显式登记**：`intel_runtime.build_group_runtime` 从 10 个
keyword-only 形参变成 11 个（新增 `session_rule`）。**不合并进任何聚合对象**是刻意的——
会话规则必须在 `resolve_intel_settings` **之后**才与运维总闸合取，把它编码进 `Settings`
会被 `INTEL_SETTING_BRIDGE` 里那一行库内行静默覆盖（spec §3.1 A-2 的陷阱）；而 `IntelDeps`
是这条链的**产物**、不是入参。签名冻结用例 `G-19` 已同步更新为 11 个并写明理由。

**共用车道**：本轮不触碰 `live_auto/*`，情报层从来就只在 AI-trade 实盘车道装配
（`ai_auto/backtest.py` / `backtest_manager.py` 全文 0 次 `intel`，由用例 `G-5` 冻结）。

### agent-trading-capabilities（对话账户工具与显式交易确认）

本轮沿用现有大文件的接线豁免，不做与功能无关的拆分。2026-09-28
按 UTF-8 文本行数复测如下；新交易逻辑均放入独立模块。

| 位置 | 阈值 | 实际 | 本轮必要接线与后续边界 |
|---|---|---|---|
| `services/quant/app/config.py` | 800 | 1782 | 仅增加账户工具及执行两项开关；未来按配置家族拆包 |
| `services/quant/app/ai_auto/manager.py` | 800 | 2607 | start/resume 共用未结交易门禁；增加只读身份一致的运行上下文投影；交易状态机留在 `agent_trading/` |
| `services/quant/app/okx/trading.py` | 800 | 1113 | 注入冻结订单发送守卫及仅聊天启用的新鲜合约规则读取；新平仓意图构建下沉 `okx/intent_close.py`，旧 API 行为保留 |
| `apps/web/lib/store.ts` | 800 | 1669 | 绑定 CAS、模式切换异步结果与会话失效守卫；类型与 HTTP 客户端留在独立模块 |

新建交易后端模块的函数均不超过 50 行，不申请函数长度或参数阈值放宽。
本轮共用自动交易准入锁，仅在 start/resume 入口接入恢复与未结链门禁，
不改变两条自动交易车道内部的策略决策或下单算法。
