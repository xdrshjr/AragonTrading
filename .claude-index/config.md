# 本功能阈值偏离记录

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
