"""配置边界校验单测（RS-13）：新增 / 既有数值字段的 @Field 边界正反例。"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.config import Settings


def _ok(**kw) -> Settings:
    return Settings(agent_api_key="x", **kw)


def test_valid_defaults_pass():
    s = _ok()
    assert s.agent_detach_grace_seconds == 30.0
    assert s.agent_heartbeat_seconds == 20.0
    assert s.agent_event_buffer_size == 512


@pytest.mark.parametrize("kw", [
    {"agent_detach_grace_seconds": -1.0},   # 须 ≥ 0
    {"agent_heartbeat_seconds": 0.0},       # 须 > 0
    {"agent_heartbeat_seconds": -5.0},
    {"agent_event_buffer_size": 63},        # 须 ≥ 64
    {"agent_event_buffer_size": 0},
    {"agent_max_tool_rounds": 0},           # 须 ≥ 1
    {"agent_max_context_messages": 1},      # 须 ≥ 2
    {"agent_max_retries": -1},              # 须 ≥ 0
    {"agent_drain_timeout_seconds": 0.0},   # 须 > 0
])
def test_out_of_bound_values_rejected(kw):
    with pytest.raises(ValidationError):
        _ok(**kw)


@pytest.mark.parametrize("kw", [
    {"agent_detach_grace_seconds": 0.0},    # 0 合法（断连立即取消的回退开关）
    {"agent_heartbeat_seconds": 0.1},
    {"agent_event_buffer_size": 64},        # 下限合法
    {"agent_max_tool_rounds": 1},
    {"agent_max_context_messages": 2},
    {"agent_max_retries": 0},
    {"agent_drain_timeout_seconds": 0.5},
])
def test_boundary_values_accepted(kw):
    assert _ok(**kw) is not None
