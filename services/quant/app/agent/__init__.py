"""Agent Trading Copilot —— 对话式 AI 助手后端包 (Agent Trading Copilot spec §2)。

把既有后端能力（OKX 行情、策略引擎、回测器、run 仓储）包装成确定性、可审计的工具，
让 LLM 在受控边界内驱动工作台。密钥只在后端，流式事件经 WS 推前端。
"""
from __future__ import annotations
