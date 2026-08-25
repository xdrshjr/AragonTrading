"""会话末叙事蒸馏（§4.9）：把一次会话的执行历程压成 ≤5 条挂在闭合词表上的教训。

**时机是这个模块最重要的事实：它跑在 `stop()` 的 await 链之外**（评审 P0-1）。
`manager.stop_group` 串行遍历成员逐个 `await self._stop_session(...)`，而前端
`/ai-trade/{groupId}/stop` 命中 `WRITE_ACTION_FAMILIES + "/stop"`，超时档是 `FAST_MS = 20_000`。
一个 3 标的分组 × 45 秒蒸馏 ≥135 秒，客户端 20 秒就放弃并弹出**「本次操作结果未知，请刷新
持仓/会话状态核对后再决定是否重试」**——在真实资金的停止/平仓按钮上制造一次假警报，代价远大于
本特性的全部收益（`timeoutMessage` 的注释直接点名提交 `7b55d16`）。故本模块由 manager 在
`_stop_session` 返回**之后** spawn 成一个 tracked 后台任务，`wait_for` 有界，失败只 warning。
进程被杀 ⇒ 最多丢这一次叙事蒸馏，**确定性事件早已落盘，零数据损失**。

第二重要的事实是 R-1（自我强化的幻觉回路）：模型写一条经验 → 下次读到 → 照做 → 再写一遍，
最终把一个从未被验证的偏见铸成铁律，然后按它花真钱。四道闸：
① 输入里排除本会话已被喂过的经验 key；② schema 强制每条引用具体 `tripSeqs`；
③ 轴与取值是 `enum`，越界即**整条**丢弃并计入 `rejected`（设置中心可见）；
④ 落地恒为 `source="narrative", tier="candidate"`——**永远不能自动进 L0**（不变式 I-4）。
"""
from __future__ import annotations

import asyncio
import json
import logging

from ...agent.llm.base import LlmMessage, TextBlock, ToolSchema
from ...db import repositories as repo
from ...db.session import get_sessionmaker
from . import taxonomy as tax
from .service import sanitize_lesson

logger = logging.getLogger(__name__)

DISTILL_TOOL_NAME = "submit_trade_lessons"

_REJECTED_KEY = "experience.rejected_narrative"

DISTILL_TOOL_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "lessons": {
            "type": "array",
            "maxItems": 5,
            "items": {
                "type": "object",
                "properties": {
                    "statement": {"type": "string", "maxLength": 120,
                                  "description": "一句话教训（中文，≤120 字，须可被后续决策直接使用）"},
                    "detail": {"type": "string", "maxLength": 600,
                               "description": "展开说明：证据、适用边界、失效条件"},
                    "context": {
                        "type": "object",
                        "description": "这条教训适用的语境；轴名与取值**只能**取给定枚举，越界整条丢弃",
                        "properties": {
                            axis: {"type": "string", "enum": list(values)}
                            for axis, values in tax.AXIS_VALUES.items()
                        },
                        "additionalProperties": False,
                    },
                    "tripSeqs": {"type": "array", "items": {"type": "integer"}, "minItems": 1,
                                 "description": "支撑这条教训的往返序号（至少 1 个，取自流水）"},
                    "polarity": {"type": "string", "enum": ["positive", "negative"]},
                },
                "required": ["statement", "context", "tripSeqs", "polarity"],
            },
        }
    },
    "required": ["lessons"],
}


def build_system_prompt() -> str:
    """蒸馏系统 prompt：把「这次交易学到了什么」限定成可复用、可证伪、挂得上坐标轴的结论。"""
    return (
        "你是自动交易复盘官。给定一次自动交易会话的往返流水，请提炼**至多 5 条**可复用的交易"
        "教训。硬性要求：\n"
        "1. 每条必须挂在给定的语境坐标轴上（标的/周期/体制/波动/事件/方向/唤醒/退出/持仓周期/"
        "成本档/置信度），且取值只能从枚举里选——**不要发明新的坐标轴或取值**。\n"
        "2. 每条必须引用至少一个具体往返序号（`tripSeqs`），不能凭印象下结论。\n"
        "3. 只写这次流水**真的支持**的结论；样本不足就少写几条，宁缺毋滥。\n"
        "4. 不要复述已经被提供给交易员的经验（列在输入的「已注入经验」里）——那会把一个未经验证"
        "的偏见反复加固。\n"
        f"你的唯一任务是必须且只能调用一次 {DISTILL_TOOL_NAME} 提交结果。")


def build_user_prompt(inputs: dict) -> str:
    """蒸馏输入：本会话往返紧凑表 + 已注入经验 key（防止模型把喂进去的话原样吐回来）。"""
    trips = json.dumps(inputs.get("trips") or [], ensure_ascii=False)
    injected = json.dumps(inputs.get("injectedKeys") or [], ensure_ascii=False)
    return (f"标的={inputs.get('instId')}｜周期={inputs.get('bar')}\n"
            f"本次会话已结算往返（JSON）：\n{trips}\n\n"
            f"本次会话已注入给交易员的经验 key（不要复述）：{injected}\n\n"
            "请据此提炼教训。")


async def run(llm, inputs: dict, settings) -> dict:
    """跑一次蒸馏并落库；任何失败 ⇒ `{"lessons": 0, "rejected": 0, "error": ...}`（**绝不抛**）。

    Args:
        llm: 决策器持有的 LLM 客户端（经 `AiTradeDecider.llm` 只读属性取得）。
        inputs: `ExperienceRuntime.distill_inputs()` 产物。
        settings: 运行配置（读 `distill_max_lessons`）。

    Returns:
        `{"lessons": n, "rejected": m}`；调用方只用于日志与 WS 通知。
    """
    if llm is None or not inputs:
        return {"lessons": 0, "rejected": 0, "error": "not_configured"}
    try:
        raw = await _stream(llm, inputs)
    except Exception as exc:  # noqa: BLE001 —— 蒸馏是加法，失败只放弃
        logger.warning("ai-trade experience distill failed: %s", exc)
        return {"lessons": 0, "rejected": 0, "error": str(exc)}
    lessons, rejected = validate(raw, int(settings.ai_trade_experience_distill_max_lessons))
    if not lessons and not rejected:
        return {"lessons": 0, "rejected": 0}
    try:
        async with get_sessionmaker()() as session:
            for lesson in lessons:
                await repo.create_experience_node(session, lesson)
            if rejected:
                prior = int((await repo.get_app_setting(session, _REJECTED_KEY, 0)) or 0)
                await repo.set_app_setting(session, _REJECTED_KEY, prior + rejected)
    except Exception as exc:  # noqa: BLE001 —— 同上
        logger.warning("ai-trade experience distill not persisted: %s", exc)
        return {"lessons": 0, "rejected": rejected, "error": str(exc)}
    return {"lessons": len(lessons), "rejected": rejected}


async def _stream(llm, inputs: dict) -> dict | None:
    """强制单工具调用并累积 `partial_json`（逐字镜像 `decider._stream_decision` 的取参范式）。"""
    tool = ToolSchema(name=DISTILL_TOOL_NAME,
                      description="提交本次会话提炼出的交易教训（至多 5 条）",
                      input_schema=DISTILL_TOOL_SCHEMA)
    parts: list[str] = []
    matched = False
    async for event in llm.stream(
            system=build_system_prompt(),
            messages=[LlmMessage("user", [TextBlock(build_user_prompt(inputs))])],
            tools=[tool]):
        if event.kind == "tool_use_start":
            matched = (event.name or "") == DISTILL_TOOL_NAME
        elif event.kind == "tool_use_args_delta" and matched:
            parts.append(event.partial_json or "")
        elif event.kind == "error":
            raise RuntimeError(event.message or event.code or "LLM_ERROR")
        elif event.kind == "message_done":
            break
    if not parts:
        return None
    try:
        return json.loads("".join(parts).strip() or "{}")
    except json.JSONDecodeError:
        return None


def validate(raw: dict | None, max_lessons: int) -> tuple[list[dict], int]:
    """把模型输出夹紧成可入库的经验行；越界轴 / 无引用 / 空陈述 ⇒ **整条丢弃**并计数。

    Returns:
        `(lessons, rejected)`——`lessons` 已是数据层的行字典（`source="narrative"`）。
    """
    items = (raw or {}).get("lessons")
    if not isinstance(items, list):
        return [], 0
    lessons: list[dict] = []
    rejected = 0
    for item in items[:max(1, max_lessons) * 2]:
        if not isinstance(item, dict) or not (item.get("tripSeqs") or []):
            rejected += 1
            continue
        context = item.get("context")
        if not isinstance(context, dict) or not context:
            rejected += 1
            continue
        if any(axis not in tax.AXES or not tax.is_valid(axis, value)
               for axis, value in context.items()):
            rejected += 1                     # 越界轴 / 越界取值 ⇒ 整条丢弃（不变式 I-5）
            continue
        clean = sanitize_lesson({"statement": item.get("statement"),
                                 "detail": item.get("detail"), "context": context},
                                source="narrative")
        if clean is None:
            rejected += 1
            continue
        clean["stats"] = {"n": 0, "narrative": True,
                          "tripSeqs": [int(s) for s in item["tripSeqs"][:20]
                                       if isinstance(s, (int, float))],
                          "polarity": item.get("polarity") or "negative"}
        lessons.append(clean)
        if len(lessons) >= max_lessons:
            break
    return lessons, rejected


async def run_bounded(llm, inputs: dict, settings) -> dict:
    """`run` 的有界包装（manager 侧后台任务的唯一入口）。超时即放弃，**绝不抛**。"""
    timeout = float(settings.ai_trade_experience_distill_timeout_s)
    try:
        return await asyncio.wait_for(run(llm, inputs, settings), timeout=timeout)
    except (TimeoutError, asyncio.TimeoutError):
        logger.warning("ai-trade experience distill timed out after %.1fs", timeout)
        return {"lessons": 0, "rejected": 0, "error": "timeout"}
    except Exception as exc:  # noqa: BLE001 —— 后台任务绝不把异常带出去
        logger.warning("ai-trade experience distill aborted: %s", exc)
        return {"lessons": 0, "rejected": 0, "error": str(exc)}
