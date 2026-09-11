"""AI 自动交易 · 后训练轨迹采集（ai-trade-trajectory-capture）。

用户在启动面板勾选后，把每一次 LLM 调用的完整请求与完整流式响应、每一轮的观测 / 动作 / 门禁 /
执行 / 成交写成本机 JSONL 段文件，离线编译成强化学习与 SFT 可用的数据集。

**采集层只读取、绝不参与判定**：本包的任何符号都不得出现在 `session.py` / `manager.py` /
`decider.py` 的判定表达式里（用例 G-T1 以 AST 冻结）。

【RV-P2-16】出口以 PEP 562 `__getattr__` **惰性导出**，且不导出 `compile` / `reader` / `rewards`：
`python -m app.ai_auto.trajectory.compile` 会先执行本文件，惰性导出使离线编译 CLI 只加载 `wire` 与
`accounting_replay`，不连带 import 写线程、通道与 provider 层。
"""
from __future__ import annotations

from importlib import import_module

_EXPORTS = {
    "NULL_TRAJECTORY": "channel", "EpisodeEnd": "channel", "EpisodeFacts": "channel",
    "NullTrajectoryChannel": "channel", "RoundCapture": "channel", "SessionBinding": "channel",
    "TrajectoryChannel": "channel",
    "probe_directory": "directory", "trajectory_config_wire": "directory",
    "RecordingLlmClient": "llm_tap", "wrap_llm_clients": "llm_tap",
    "GroupRecorderRequest": "recorder", "TrajectoryOpenError": "recorder",
    "TrajectoryRecorder": "recorder", "close_group_recorder_nowait": "recorder",
    "close_recorder_nowait": "recorder", "open_group_recorder": "recorder",
    "trajectory_receipt": "recorder",
    "accounting_context_of": "records",
    "TrajectoryRule": "rules", "resolve_trajectory_rule": "rules",
}

__all__ = sorted(_EXPORTS)


def __getattr__(name: str):
    """PEP 562：按需加载子模块并返回导出符号。"""
    module = _EXPORTS.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    return getattr(import_module(f".{module}", __name__), name)
