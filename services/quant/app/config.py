"""应用配置 —— 从环境变量 / .env 读取 (spec §12)。

所有密钥仅存于环境变量，绝不下发前端、不入库、不进日志。
"""
from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """全局设置。字段名即环境变量名（大小写不敏感）。"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ---- OKX 端点 ----
    okx_rest_base: str = "https://www.okx.com"
    # ⚠️ OKX V5 把公共行情拆到两个 WS 端点：trades/tickers/books5 仅在 **public**
    # 端点；candle{bar} K 线仅在 **business** 端点（在 public 订阅 candle 会回 60018
    # "channel doesn't exist"，反之亦然）。故两端点都要配：盘口/成交/ticker 走
    # public，K 线走 business。
    okx_ws_public: str = "wss://ws.okx.com:8443/ws/v5/public"
    okx_ws_business: str = "wss://ws.okx.com:8443/ws/v5/business"

    # ---- OKX 凭据（本期仅占位，公共行情不需要）----
    okx_api_key: str = ""
    okx_secret_key: str = ""
    okx_passphrase: str = ""

    # ---- 实盘账户（设置中心 · 实盘账户私有签名请求，spec §12）----
    # 字段名即环境变量名（OKX_ACCOUNT_*，大小写不敏感）；凭证真源为库内多账户，env 不参与。
    okx_account_demo: bool = False             # True 时私有请求附 x-simulated-trading:1（模拟盘）
    okx_account_timeout_seconds: float = 15.0  # 私有请求超时上限

    # ---- 实盘交易（Phase 2「guarded order placement」，实盘交易模式 spec §3）----
    # 字段名即环境变量名（LIVE_*，大小写不敏感）；本轮需求把实盘下单默认翻为开启。
    # 桌面端经 desktop/src/lib/config.js 白名单透传（C1-6），dev 经 services/quant/.env 读取。
    # 安全阀仍在：设 LIVE_TRADING_ENABLED=false 即一键回退「real trading disabled」安全姿态；
    # 逐单二次确认、名义上限、标的 / net_mode 反向单本地拦截等护栏均未削弱。
    live_trading_enabled: bool = True            # 总开关：False 时 /trading/order|leverage 一律 403
    # 单笔下单名义价值本地上限（USDT），越界本地拦截。C1-7：默认收紧至 1000，避免高默认值架空
    # §8 风险①护栏③；需放大须显式 env（LIVE_MAX_ORDER_NOTIONAL_USD）覆写。
    live_max_order_notional_usd: float = 1000.0
    live_default_td_mode: str = "cross"          # 合约默认保证金模式（cross | isolated）
    live_default_leverage: float = 3.0           # 下单面板默认杠杆

    # ---- 实时自动交易（live-auto-trading spec §三/config，策略驱动无人值守下单）----
    # 字段名即环境变量名（LIVE_AUTO_*，大小写不敏感）；桌面端经 desktop/src/lib/config.js
    # 白名单 key.startsWith('LIVE_') 天然透传。安全：自动化总闸默认关闭（R1）——自动下单真正的
    # 默认关闭闸是 live_auto_trading_enabled（live_trading_enabled 现默认 True），启动须 API 层
    # 显式同时校验两闸皆为 True 方可放行。
    live_auto_trading_enabled: bool = False      # 自动化专用总闸：False 时 /live-auto 写类一律 403
    # 容量语义已由「单活跃分组（1..N 会话）+ live_auto_max_group_symbols」取代（watchlist-auto-trade-group
    # §2.4 / P2-1）：一次至多一个活跃分组，分组内成员数受 live_auto_max_group_symbols 封顶。本字段现无
    # 引用，仅作历史兼容保留（容量判定改由 manager 直接判 self._group），避免文档漂移。
    live_auto_max_sessions: int = 1              # （已弃用）旧「单会话独占」语义，现由单活跃分组取代
    # 单个活跃自动交易分组内的标的数硬上限（真实资金多标的并发的封顶护栏，R1/R6；前端拖入超限即拒绝）。
    live_auto_max_group_symbols: int = 8
    # 分组启动时签名私有请求（account/config + account/balance）的并发上限：每 N 个标的一组排队，
    # 避免启动瞬间把 N×2 发私有请求一次性打爆 OKX 私有限频触发 429（见 docs/diagnoses/
    # live-auto-multi-symbol-rate-limit-429）。默认 2，可经 env LIVE_AUTO_START_CONCURRENCY 调参。
    live_auto_start_concurrency: int = 2
    # 单标的旧路径留空默认（取可用 USDT 的 1%，保守）；分组路径不引用它（分组留空默认 = 上限本身）。
    live_auto_default_capital_frac: float = 0.01
    # 单标的旧路径绝对天花板（前置门禁强制夹紧，R4）。**保持 0.05 不变**——分组天花板见下一字段。
    live_auto_max_capital_frac: float = 0.05
    # 分组路径运营天花板（auto-trade-budget-and-allocation-settings RN-1）：默认 1.0 = 不额外限制；
    # 运营可经 env LIVE_AUTO_GROUP_MAX_CAPITAL_FRAC 收紧（如生产设 0.2 强制分组 ≤ 20%，无视前端配置）。
    # 与「自动交易设置 · 总预算上限百分比」取 min 得分组有效上限分数；服务端权威，前端不可越过。
    live_auto_group_max_capital_frac: float = 1.0
    # max_ratio（最大比例）分配模式下新增成员的保守默认上限百分比（前端 store 同名常量与之对齐）。
    live_auto_default_max_ratio_pct: float = 20.0
    live_auto_poll_interval_s: int = 5           # 持仓 reconcile / 会话盯市轮询基准间隔（秒，hardening H1 接线）
    # ---- 加固增强（live-auto-trading-hardening）：独立盯市循环 + 容错 + 看门狗（H1/H2/H3/H4）----
    # 行情停滞看门狗阈值 = bar_seconds × 该系数；超阈推降级 status（默认仅告警不平仓）。
    live_auto_staleness_factor: float = 3.0
    # 行情停滞是否自动平仓（默认 False：瞬时抖动平仓危害更大；仅运维显式开启且停滞超长才触发）。
    live_auto_flatten_on_stale: bool = False
    # reconcile 连续失败达此次数即推 degraded（默认 6 ≈ 30s @ poll_interval_s=5），恢复后清零。
    live_auto_reconcile_max_failures: int = 6
    # 停止会话平仓失败的有界重试次数（默认 2；仍失败则诚实返回 residual 并强提示人工平仓，H4）。
    live_auto_flatten_max_retries: int = 2

    # ---- AI 自动交易（ai-auto-trading spec §三/config：AI Agent 作为多策略信号的决策者）----
    # 字段名即环境变量名（AI_TRADE_*，大小写不敏感）。**必须真的声明这些字段**：Settings 为
    # extra="ignore"，未声明的 AI_TRADE_* env 会被静默丢弃（P1-6）。桌面打包须在
    # desktop/src/lib/config.js prod 白名单补 key.startsWith('AI_TRADE_') 方能透传（P1-6）。
    # 安全：自动化专用总闸 ai_trade_enabled 默认关闭（AI 下单默认关闭闸）；启动须 API 层显式同时
    # 校验 ai_trade_enabled 与 live_trading_enabled 皆为 True 方可放行（双闸）。
    ai_trade_enabled: bool = False               # 自动化专用总闸：False 时 /ai-trade 写类一律 403
    ai_trade_default_capital_frac: float = 0.01  # 留空默认预算 = availUsdt × 该值（保守 1%）
    # AI 分组预算的**可选**绝对天花板分数（默认 1.0=不约束，实际天花板委托给账户级 maxBudgetPct 与分组运营
    # 天花板 live_auto_group_max_capital_frac，与非 AI 多标的分组口径统一）；运维如需 AI 专属更紧的绝对上限
    # （如恢复历史 5% 红线），可经 env AI_TRADE_MAX_CAPITAL_FRAC 下调本值（min 只能收紧、绝不放大天花板）。
    ai_trade_max_capital_frac: float = 1.0
    ai_trade_default_sl_pct: float = 0.05        # 会话默认单仓止损 pct（交易所侧 OCO 兜底；0 即不挂）
    ai_trade_default_tp_pct: float = 0.10        # 会话默认单仓止盈 pct（交易所侧 OCO 兜底；0 即不挂）
    ai_trade_default_max_hold_bars: int = 0      # 默认强制时间平仓根数（0=禁用，退出交由 AI close + 会话止盈止损）
    ai_trade_max_strategies: int = 8             # 单会话可喂给 AI 的策略数上限（超出即 400 守门）
    # AI 自动交易「分组」标的数上限（ai-trade-multi-symbol-and-timeframe §4.1）：一个 AI 分组内至多 N 个
    # 标的，每标的各是一个独立 AiTradeSession（各自一次/轮 LLM 决策）。刻意 < live_auto_max_group_symbols(8)：
    # N 个 decider 每轮各一次 LLM 调用，成本随标的数线性上升。分组总预算按 allocationPct **切分**（非倍增），
    # 故总账户敞口仍受分组总预算天花板约束——该天花板 = min(maxBudgetPct/100, ai_trade_max_capital_frac(默认
    # 1.0=不约束), live_auto_group_max_capital_frac)，由账户级 maxBudgetPct 与运营天花板共同主导（与非 AI 分组
    # 口径统一，ai-trade-total-budget-allocation 有意放开历史 5% 绝对红线）。
    ai_trade_max_symbols: int = Field(default=5, ge=1)
    ai_trade_default_context_rounds: int = 10    # 上下文刷新轮数默认值
    ai_trade_min_context_rounds: int = Field(default=3, ge=1)    # 上下文刷新轮数下限
    ai_trade_max_context_rounds: int = Field(default=200, ge=1)  # 上下文刷新轮数上限（与下方 messages 相互独立，P1-3）
    # 喂给决策模型的滚动窗口硬上限（与 ai_trade_max_context_rounds 解耦：摘要真源是 _journal 而非
    # 模型窗口 _context，P1-3）。仅为「回喂 LLM 的近 N 条」护栏，防极端参数下越界。
    ai_trade_max_context_messages: int = Field(default=40, ge=2)
    ai_trade_decision_timeout_s: float = 60.0    # 单轮 LLM 决策超时（超时一律回落 hold）
    # 单轮决策重试护栏（round-2）：仅对「瞬时 LLM 失败 / 未产出有效决策」类回落重试，穷尽后才降级 hold。
    # 语义：首次尝试之外的最多重试次数——默认 3（即最多 4 次 LLM 调用）；0=沿用旧行为「首次失败即降级」。
    # genuine 决策（合法动作 / 刻意 hold / low_confidence 风险门禁）不计入、不重试。
    ai_trade_decision_max_retries: int = Field(default=3, ge=0)
    ai_trade_retry_base_ms: int = 500     # 指数退避基数（复用 agent.llm.retry.backoff_delay_ms 语义）
    ai_trade_retry_max_ms: int = 8000     # 单次退避封顶
    ai_trade_min_confidence: float = 0.0         # 置信度门槛（<该值降级 hold）；默认 0=不启用
    # ---- 综合研判增强包（ai-auto-trading-enhancements v4 · E1/E2/E4）----
    # E1 市场体制上下文：默认开（只读输入增强，向决策 prompt 注入一行市场体制；数据不足/异常自动省略）。
    ai_trade_regime_context: bool = True         # E1：每轮注入市场体制上下文（只读；False=不注入）
    # E2 共识门禁：默认 0=关闭（等价 v2）。>0 时开仓方向同向策略数不足即硬降级 hold（独立于 LLM，仅更保守）。
    # 与既有 ai_trade_min_confidence 独立叠加：开仓须同时过「置信度 ≥ min_confidence」与「同向共识 ≥ min_consensus」。
    ai_trade_min_consensus: int = Field(default=0, ge=0)   # E2：开仓最小同向策略数门禁（0=关闭）
    # E4 单仓 OCO pct 夹紧上限：入参 stopLossPct/takeProfitPct 越界即夹到这里（留空则回落上方 default_sl/tp_pct）。
    ai_trade_max_sl_pct: float = 0.20            # E4：单仓止损 pct 硬上限（入参越界夹紧）
    ai_trade_max_tp_pct: float = 0.50            # E4：单仓止盈 pct 硬上限（入参越界夹紧）
    # ---- 用户可配置仓位操作档位（ai-trade-position-sizing-steps §2.1/§五）----
    # 请求 positionStepPct 留空时的默认档位步长（fraction，0.25=每次 25% 一格 → 档位 {25,50,75,100}%）。
    # 决策器把 LLM 给出的连续 sizeFraction 吸附到 {k·step} 档位（唯一真源 signals.position_ladder）；
    # 0=连续（旧行为，仅后端零回归默认 / API 退出口，弹窗不露出）。env 名 AI_TRADE_DEFAULT_POSITION_STEP_PCT
    # 已在 desktop config.js prod 透传白名单内（AI_TRADE_ 前缀），此处声明后方不被 extra="ignore" 静默丢弃。
    ai_trade_default_position_step_pct: float = 0.25
    # ---- 思维链研判增强（ai-trade-reasoning-chain §2.8）----
    # 向决策 prompt 注入「价格走势 / 成交量 / 技术因子」三段紧凑证据摘要，配合分步思维链研判纪律：策略/因子
    # 信号只作参考与触发线索、可被 Agent 推翻。默认开（只读输入增强；禁用 / 数据不足 / 异常自动省略该段，
    # 确定性行为零回归）。指标周期（RSI14/EMA9,21/MACD12,26,9/BB20,2/ATR14）与 _MIN_BARS=35 作为模块级常量
    # 写在 market_digest.py（规避「AI_TRADE_ env 三处联动」既知易踩点，与 trace.py 阈值常量同风格）。
    ai_trade_market_digest: bool = True          # 总开关：注入价格走势/成交量/因子证据摘要（False=不注入）
    ai_trade_multi_timeframe_context: bool = True  # 多周期中线背景总开关（只读；缺数时显式降级）
    ai_trade_digest_trend_window: int = Field(default=20, ge=2)  # 价格走势/区间位置回看根数
    ai_trade_digest_vol_window: int = Field(default=20, ge=2)    # 成交量均值/zscore 回看根数
    # ---- Agent 墙钟定时唤醒（ai-auto-trading-wake-interval §2.5）----
    # 在既有「确认 K 线 → 决策」触发之外叠加第三个唤醒源：一个独立于 K 线周期的墙钟定时唤醒，默认每 2
    # 分钟让 Agent 用「当前实时价 + 最近信号板 + 持仓」重新研判（两根 K 线之间也能及时止盈损/调仓）。
    # 复用同一决策管线与全部资金/并发护栏，仅多一种「何时唤醒」的来源。env 名 AI_TRADE_WAKE_INTERVAL_*
    # 已在 desktop config.js prod 透传白名单内（AI_TRADE_ 前缀），此处声明后方不被 extra="ignore" 静默丢弃。
    ai_trade_wake_interval_default_min: float = 2.0    # 未显式传值时的默认唤醒周期（分钟）
    ai_trade_wake_interval_floor_s: float = 30.0       # 唤醒周期硬下限（秒），防高频 LLM 烧钱 / 抖动占比过大
    ai_trade_wake_interval_ceil_min: float = 240.0     # 唤醒周期硬上限（分钟，= 4h）
    # ---- AI 自动交易 · 回测模式（ai-auto-trading-backtest §2.5，三重决策成本护栏）----
    # 回测每决策根调一次 LLM，成本/时长敏感；三重护栏：决策间隔（降频）+ 决策轮数硬上限（越界启动前
    # 拒绝，绝不静默截断）+ 并发闸（限同时消耗 LLM 的回测数）。字段名即环境变量名（AI_BACKTEST_*，
    # 大小写不敏感）；须真的声明（Settings extra="ignore"，未声明的 env 会被静默丢弃）。
    ai_backtest_max_rounds: int = Field(default=400, ge=1)          # 单次回测的决策轮数硬上限（越界 400 拒绝）
    ai_backtest_default_decision_every_bars: int = Field(default=1, ge=1)  # AI 每 K 根决策一次的默认间隔
    ai_backtest_max_jobs: int = Field(default=2, ge=1, le=8)        # 同时进行的 AI 回测作业上限（超出 429）

    # ---- 代理（透传给 httpx / websockets）----
    http_proxy: str = ""
    https_proxy: str = ""
    all_proxy: str = ""

    # ---- 持久化 ----
    db_path: str = "aragon.db"

    # ---- 默认值 ----
    default_inst: str = "BTC-USDT-SWAP"
    default_bar: str = "1H"
    initial_capital: float = 10000.0

    # ---- 行为开关 ----
    allow_cached_fallback: bool = False
    max_paper_sessions: int = 12

    # ---- 撮合默认参数 (spec §6.1) ----
    default_taker_fee_bps: float = 5.0       # OKX 永续 taker ≈ 5 bps
    default_slippage_bps: float = 2.0
    default_leverage: float = 1.0

    # ---- 回测取数安全闸（高级回测：滑动窗口 / 全区间分页 spec §6.3）----
    # 字段名即环境变量名（BACKTEST_MAX_BARS / WALK_FORWARD_MAX_WINDOWS，大小写不敏感）。
    backtest_max_bars: int = Field(default=20000, ge=1000)   # 单次回测取数安全上限（防超长区间拖垮）
    walk_forward_max_windows: int = Field(default=200, ge=1)  # 高级回测窗口数硬上限
    # 同步权重训练（/api/factor-sets/train）的有效根数预算：超出即取数前 413 FACTOR_TRAIN_TOO_LARGE 快速
    # 失败，而非让无界长请求被传输层窗口切断成 socket hang up（docs/diagnoses/
    # factor-lab-train-long-range-socket-hang-up）。须严格 < backtest_max_bars，否则因 target 早被夹到
    # 20000 而沦为 no-op；默认 8000（≈80 次 OKX 串行请求、≥10s 限频地板，落在多数链路超时窗口内），可经
    # 环境变量 FACTOR_TRAIN_MAX_BARS 调参。
    factor_train_max_bars: int = Field(default=8000, ge=1000)
    # ---- 因子实验室「标的组」多标的扇出上限（factor-lab-symbol-groups §五，取数前判定，绝不无界扇出）----
    # 字段名即环境变量名（MAX_TRAIN_SYMBOLS / MAX_EVAL_SYMBOLS，大小写不敏感）。多标的评估 / 训练逐标的
    # 串行取真实 OKX K 线，标的数越界即取数前 400 快速失败（评估 FACTOR_EVAL_TOO_MANY_SYMBOLS / 训练
    # FACTOR_TRAIN_TOO_MANY_SYMBOLS），把逐标的 OKX 请求压回 _candle_cache 量级附近，重演 429 / socket 压力
    # 的风险受控（C-1）。挖掘扇出由既有 413 工作量护栏（计入标的数）在取数前拦截，无需独立标的数上限。
    max_train_symbols: int = Field(default=20, ge=1)   # 单次权重训练的标的组成员数上限
    max_eval_symbols: int = Field(default=30, ge=1)    # 单次因子评估的标的组成员数上限
    # ---- 权重模型工作区（weight-model-workspace §四/config，流式训练 + 模型管理）----
    # 字段名即环境变量名（MAX_WEIGHT_TRAIN_JOBS 等，大小写不敏感）。流式训练作业与 socket 解耦，
    # 故并发上限独立于同步训练护栏：超出即 429 WEIGHT_TRAIN_BUSY（守门优先，绝不先落库再拒，R-01）。
    # G-B9：补 le 上限（宽松、远高于合理用值），仅拦截病态超大值——过高 curve_steps 膨胀缓冲 + WS 事件数，
    # 过高 max_weight_train_jobs 抹掉保护性并发上限；默认值不变，既有部署不受影响。
    max_weight_train_jobs: int = Field(default=4, ge=1, le=16)      # 同时在跑的流式训练作业上限（R-01 429 守门）
    weight_train_curve_steps: int = Field(default=6, ge=2, le=20)   # 扩样本学习曲线的 fraction 网格步数（末点恒 1.0）
    weight_model_run_cap: int = Field(default=50, ge=1, le=500)     # 单模型训练记录上限；超出按时间剪枝且显式 log（R-05）
    # 多模型训练竞技场（weight-multi-model-training §3.2/config）：单个批次可勾选的候选方法 / 超参变体数上限，
    # 界定单批资源上界（候选超可用名额时进服务端 FIFO 队列，名额释放即泵起，绝不突破 max_weight_train_jobs）。
    max_weight_batch_candidates: int = Field(default=8, ge=2, le=16)  # 单批候选数上限（超出即 400 守门，绝不无界扇出）
    # 因子组合探索训练（factor-combo-exploration §2.3/§4.1/§7）：单次探索展开的「组合 × 方法」候选数上限。
    # 探索走自有端点（不受 WeightBatchStartReq.candidates 的 max_length=16 约束），故上限独立在此界定。默认 12
    # （约为 arena max_weight_batch_candidates=8 的 1.5×），并发仍受 max_weight_train_jobs=4 限、墙钟随候选数
    # 线性增长；超限一律显式 400 WEIGHT_EXPLORE_TOO_MANY_CANDIDATES 阻断，绝不静默截断（P1-3）。
    max_weight_explore_candidates: int = Field(default=12, ge=2, le=24)

    # ---- Agent（对话式 AI 助手）LLM 接入 (Agent Trading Copilot spec §3.1) ----
    # 字段名即环境变量名（AGENT_*，大小写不敏感）；密钥仅存后端，绝不下发前端 / 不入库 / 不进日志。
    agent_provider: str = "anthropic"                       # anthropic | openai（双标准适配）
    agent_base_url: str = "https://api.kimi.com/coding"     # KIMI Coding 端点
    agent_api_key: str = ""                                 # 缺省为空 → 面板优雅引导而非崩溃
    # 默认采用任务显式指定值 kimi-k2.6；kimi-k2.5 为 docs/examples 已实测可用的回退值（R1/P1-A）。
    agent_model: str = "kimi-k2.6"
    agent_anthropic_version: str = "2023-06-01"
    agent_user_agent: str = "claude-code/1.0"               # KIMI 端点校验 UA（R2）
    agent_max_tokens: int = 2048
    agent_temperature: float = 1.0
    agent_top_p: float = 0.95
    # 失控护栏 / 上下文裁剪（RS-13 边界校验：轮数 ≥1、回喂条数 ≥2，杜绝 .env 误填 0/负值）。
    agent_max_tool_rounds: int = Field(default=12, ge=1)     # 失控护栏（spec §2.3）
    agent_max_context_messages: int = Field(default=40, ge=2)  # 回喂模型的近 N 条裁剪（P2-2）
    agent_timeout_seconds: float = 120.0
    # Agent LLM 独立代理：留空回落 settings.proxy_url；显式 "none" 强制直连（R5）。
    agent_proxy: str = ""
    # ---- 加固增强（iteration 2）：LLM 传输层有界重试 + 断连 drain 超时 (hardening H1/H4) ----
    # 字段名即 AGENT_* 环境变量名（大小写不敏感），可经 .env 覆盖。
    agent_max_retries: int = Field(default=2, ge=0)  # pre-stream 重试上限（最多 1+max 次，0 即不重试）
    agent_retry_base_ms: int = 500      # 指数退避基数（第 i 次退避 base*2**i，封顶 max）；RN-6：不新增校验
    agent_retry_max_ms: int = 8000      # 单次退避封顶（亦为 Retry-After 整数秒解析上限）；RN-6：不新增校验
    agent_drain_timeout_seconds: float = Field(default=8.0, gt=0)  # 断连后兜底 drain 超时
    # ---- 韧性流式（iteration 3）：run/socket 解耦续流 + 心跳保活（RS-1/RS-3/RS-7/RS-13）----
    # 字段名即 AGENT_* 环境变量名；缺省回落以下默认值，桌面端经白名单按需覆盖。
    agent_detach_grace_seconds: float = Field(default=30.0, ge=0)   # 断连存活宽限；0 即断连立即取消（回退迭代2）
    agent_heartbeat_seconds: float = Field(default=20.0, gt=0)      # run 期心跳间隔（须 < 中间层 idle 超时）
    agent_event_buffer_size: int = Field(default=512, ge=64)        # 会话事件缓冲上限（重放窗口）

    @property
    def proxy_url(self) -> str | None:
        """优先 HTTPS_PROXY，其次 HTTP_PROXY / ALL_PROXY；都没有则 None。"""
        return self.https_proxy or self.http_proxy or self.all_proxy or None

    @property
    def agent_proxy_url(self) -> str | None:
        """Agent LLM 调用专用代理（R5）：'none' → None（强制直连）；空 → 回落 proxy_url。"""
        if self.agent_proxy.strip().lower() == "none":
            return None
        return self.agent_proxy or self.proxy_url


@lru_cache
def get_settings() -> Settings:
    """单例配置。"""
    return Settings()
