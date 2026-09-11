"""路由：AI 自动交易 · 后训练轨迹采集（ai-trade-trajectory-capture §4.1）。

两个端点，全部走 `/api` 前缀并经既有 `OriginGuardMiddleware`：

- `GET /ai-trade/trajectory/config`：启动面板卡片据它说明「为什么不能开」与默认目录。**不守 AI 总闸**
  （只读配置）。
- `POST /ai-trade/trajectory/validate`：目录判定是**数据**不是错误——`ok=false` 也回 200。副作用仅限在
  最近的已存在祖先目录里建删一个探针文件；**不创建目标目录**。

两个端点都**不挂 `response_model`**：未在模型里声明的 wire 键会被 FastAPI 静默丢弃（本仓库成文教训）。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from ..ai_auto.trajectory import probe_directory, trajectory_config_wire
from ..config import Settings, get_settings
from ..schemas import AiTradeTrajectoryValidateIn

router = APIRouter(tags=["ai-trade-trajectory"])


@router.get("/ai-trade/trajectory/config")
async def ai_trade_trajectory_config(settings: Settings = Depends(get_settings)) -> dict:
    """采集配置：运维总闸、默认根目录、空间下限、段上限、是否压缩与格式版本。"""
    return trajectory_config_wire(settings)


@router.post("/ai-trade/trajectory/validate")
async def ai_trade_trajectory_validate(body: AiTradeTrajectoryValidateIn,
                                       settings: Settings = Depends(get_settings)) -> dict:
    """目录可用性判定（线程里跑，死线 `AI_TRADE_TRAJECTORY_PROBE_TIMEOUT_S`）。"""
    verdict = await probe_directory(body.directory, settings)
    return verdict.wire()
