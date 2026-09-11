"""分组 manifest 与数据集说明卡（ai-trade-trajectory-capture §2.7.1 / §2.7.4）。

manifest 一律「写 `.tmp` → fsync → `os.replace`」原子覆写。Windows 上杀软 / 索引器的短暂占用会让
`os.replace` 抛 `PermissionError`，故只对这一种错误有界重试（RV-P2-3）；读失败（撕裂 / 不存在）一律
视为「没有上一份」，**绝不因为读不到 manifest 而拒绝打开**。
"""
from __future__ import annotations

import json
import logging
import os
import time
from collections.abc import Callable
from pathlib import Path

logger = logging.getLogger(__name__)

MANIFEST_NAME = "manifest.json"
DATASET_CARD_NAME = "DATASET_CARD.md"
_REPLACE_RETRIES = 3
_REPLACE_BACKOFF_S = 0.05

DATASET_CARD_TEXT = """# AI 自动交易 · 后训练轨迹数据集

本目录由 Aragon Trading 的「后训练数据采集」写入（AI 自动交易启动面板第 4 步，逐会话勾选，默认关闭）。
数据只写本机、不上传；落盘前逐行剔除 OKX 与 LLM 凭证原文。它仍然包含你的交易策略、持仓与账户规模
信息——外发之前请自行审阅。

## 目录布局

    <本目录>/
      DATASET_CARD.md
      <YYYYMMDD>-<groupId>/          一次启动一个目录（冷续跑复用同一目录，新增一个 epoch）
        manifest.json                成员、段清单、epoch、计数与状态（原子覆写）
        members/<instId>/
          seg-e<epoch 序号>-<UTC>-<epoch8>-<nnnnn>.jsonl       活动段（明文，逐行追加）
          seg-e<epoch 序号>-<UTC>-<epoch8>-<nnnnn>.jsonl.gz    已封口段

    段文件名的字典序即时间序（跨冷续跑亦然：epoch 序号打头）。

## 记录格式（schemaVersion = 1）

每行一个 JSON 对象，公共信封：v / kind / epoch / seq / wallTs / groupId / instId / runId。
seq 在每个（标的, epoch）内严格 +1，被丢弃的记录照样消耗序号，缺口由 gap 记录说明。写线程生成的
segment_open / segment_close / blob / gap（scope=record）的 seq 为 null。

| kind | 含义 |
|---|---|
| episode_start / episode_end | 一个会话 epoch 的开始与结束（含会计口径 accounting 与环境 env） |
| llm_call | 一次 LLM 调用：完整请求（system / 多轮 messages / tools，以 $blob 引用）、真实发出的采样参数、完整思考与研判正文、每个工具调用的名称与入参原文、收尾码与 token 用量 |
| blob | 内容寻址的请求正文；每段自洽，任一段单独解压都能完整解析 |
| round | 一个决策轮的断面：观测、模型原始动作、门禁降级码、反思裁决、最终动作、执行回执、成交腿、本轮全部调用与被采纳的那一次 |
| round_skipped / round_failed / round_aborted | 被整轮跳过 / 抛出异常 / 在任何成交之前被暂停或停止打断的轮次 |
| fills | 决策轮之外采到的成交腿（盯市 / 暂停 / 停止 / 冷续跑离线补记） |
| lifecycle | 暂停与继续 |
| gap | 记录缺口（scope=record）或成交台账缺口（scope=ledger） |
| segment_open / segment_close | 段文件的开始与封口 |

## 编译成训练数据集

    cd services/quant
    .venv/Scripts/python -m app.ai_auto.trajectory.compile --src <分组目录或本目录> --out <输出目录> [--strict]

产出 steps.jsonl（一轮一行，强化学习）、calls.jsonl（一次调用一行，SFT / 偏好）与 report.json
（完整性报告）。编译只读输入目录，绝不改写或删除任何段文件。

## 必须知道的约定

- role=tool 的消息只承载 tool_result 块：转 Anthropic Messages 时并入 user 轮，转 OpenAI Chat 时展开为 role=tool 消息。
- 历史 assistant 轮是系统合成的：它按门禁之后的最终决策装配工具调用，并把执行回执与门禁 / 反思注记并进 tool_result，不含模型当时的研判正文与思考。模型的真实输出只在各次 llm_call.response 里。
- capabilities.thinkingCaptured=false 表示这条链路采不到扩展思考（OpenAI 兼容端）；thinkingEnabled=false 表示本次没有开启扩展思考——此时模型的思维链全部在研判正文 text 里，本数据集逐字节完整留存。
- round.completion=partial 表示成交之后该轮没走到最后一个写点：观测、动作与成交腿完整，摘要等收尾步骤可能缺席。abortReason 说明原因：cancelled（被暂停 / 取消打断）、exception（成交之后的收尾步骤抛错）、episode_closed（本轮在飞时会话停止，含本轮内部触发的会话止盈 / 止损——这一轮其实已经跑完）。
- 决策轮在飞期间发生的全部成交腿（含盯市触发的平仓）都记在该轮的 round.fills 里；fills 记录只承载轮与轮之间采到的成交。
- llm_call.status=closed_early 且 sawMessageDone=false 也可能是消费方超时（研判流在两个事件之间被取消）；响应是否完整以编译产物的 responseComplete 为准。
- 成交身份以 explain.meta.accountingEventId 为准；ledgerSeq 只在同一 epoch 内单调。采集侧不做跨 epoch 去重，由编译器去重。
- 回测车道不采集（模拟撮合会污染奖励口径）；模拟盘会话标记为 env=demo。
"""


def read_manifest(group_dir: Path) -> dict | None:
    """读上一份 manifest；撕裂 / 不存在 / 非对象一律 `None`（绝不抛）。"""
    try:
        value = json.loads((Path(group_dir) / MANIFEST_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def write_manifest_atomic(group_dir: Path, manifest: dict, *,
                          redact: Callable[[str], str] | None = None) -> bool:
    """原子覆写 manifest（写线程内调用）。

    Args:
        group_dir: 分组目录。
        manifest: 待写对象（`TrajectoryRecorder.manifest_snapshot` 的产物）。
        redact: 写入前对序列化文本做凭证剔除（`launch` 是启动请求原文，纵深防御再过一遍）。

    Returns:
        是否写成。失败时旧 `manifest.json` 保持完整可读，调用方只计 `manifestWriteErrors`。
    """
    text = json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True, default=str)
    if redact is not None:
        text = redact(text)
    target = Path(group_dir) / MANIFEST_NAME
    temp = target.with_name(MANIFEST_NAME + ".tmp")
    try:
        with open(temp, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
    except OSError as exc:
        logger.warning("ai-trade trajectory manifest temp write failed (%s): %s", target, exc)
        return False
    return _replace_with_retry(temp, target)


def ensure_dataset_card(root: Path) -> None:
    """首次写入数据集说明卡；已存在则不覆盖（用户可能改过它）。

    Raises:
        OSError: 根目录不可写（打开 recorder 的调用方据此判 `OPEN_FAILED`）。
    """
    try:
        with open(Path(root) / DATASET_CARD_NAME, "x", encoding="utf-8", newline="\n") as handle:
            handle.write(DATASET_CARD_TEXT)
    except FileExistsError:
        return


def _replace_with_retry(temp: Path, target: Path) -> bool:
    last: OSError | None = None
    for attempt in range(_REPLACE_RETRIES):
        try:
            os.replace(temp, target)
            return True
        except PermissionError as exc:      # 杀软 / 索引器短暂占用：有界重试
            last = exc
            if attempt + 1 < _REPLACE_RETRIES:
                time.sleep(_REPLACE_BACKOFF_S)
        except OSError as exc:
            last = exc
            break
    logger.warning("ai-trade trajectory manifest replace failed (%s): %s", target, last)
    return False
