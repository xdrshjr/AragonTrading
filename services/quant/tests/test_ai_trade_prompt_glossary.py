"""中→英术语对照表的漂移门禁（ai-trade-deep-reasoning-chain §7.2 用例 G-2′）。

> **判红之后的唯一正确动作是把新标签登记进 `GLOSSARY` 左列。**
> 放宽正则、加白名单、删用例三种做法一律视为缺陷。这条用例会因为**别的特性**新加一个中文证据
> 标签而判红——那正是它存在的理由（本仓库前科：`.env.example` 无扫描用例，漏登记是静默的）。
> 本用例的 owner 是 ai-trade-deep-reasoning-chain。

本特性把**指令层**切成英文，而 ~150KB 的**证据渲染层**保持中文（spec §1.2 非目标）。语言混排的
风险由两件事兜住：这份对照表，以及协议阶段 S9「用英文复述每一条已装配铁律及其阈值」。对照表如果
悄悄漂了，第二件事就没有了参照系。
"""
from __future__ import annotations

import pathlib

from app.ai_auto.prompt_glossary import (
    GLOSSARY,
    GLOSSARY_BLOCK,
    GLOSSARY_KEYS,
    SCANNED_MODULES,
    scan_chinese_labels,
)

_AI_AUTO = pathlib.Path(__file__).resolve().parents[1] / "app" / "ai_auto"


def test_every_chinese_evidence_label_is_in_the_glossary():
    """G-2′：证据渲染层里出现的每一个中文段标题 / 数据标签都必须在 `GLOSSARY` 左列。"""
    missing: dict[str, set[str]] = {}
    for name in SCANNED_MODULES:
        source = (_AI_AUTO / name).read_text(encoding="utf-8")
        unknown = scan_chinese_labels(source) - GLOSSARY_KEYS
        if unknown:
            missing[name] = unknown
    assert not missing, (
        "以下中文证据标签未登记进 prompt_glossary.GLOSSARY："
        f"{ {k: sorted(v) for k, v in missing.items()} }。"
        "唯一正确的修法是把它们登记进 GLOSSARY 左列——放宽正则 / 加白名单 / 删本用例都是缺陷。")


def test_every_scanned_module_exists():
    """扫描清单本身也会漂：模块改名后门禁会静默地少扫一个文件。"""
    for name in SCANNED_MODULES:
        assert (_AI_AUTO / name).exists(), name


def test_glossary_has_no_duplicate_keys():
    """左列是唯一真源，重复键意味着两条不同的英文译法同时生效（渲染取后者，静默）。"""
    keys = [zh for zh, _ in GLOSSARY]
    assert len(keys) == len(set(keys))


def test_glossary_block_is_english_on_the_right_hand_side():
    """块的价值在于「中文标签 = 英文术语」这一对；右列若也是中文，这个块就白占 token。"""
    assert GLOSSARY_BLOCK.startswith("# GLOSSARY")
    for zh, en in GLOSSARY:
        assert zh in GLOSSARY_BLOCK
        assert en.isascii(), (zh, en)


def test_scan_ignores_comments_and_only_reads_string_literals():
    """只扫字面量、不扫注释：注释里的中文是散文，纳入门禁只会逼着下一个人放宽正则。"""
    source = (
        "# 这里是注释：【假段标题】方向=long\n"
        "TEXT = '【真段标题】方向=long'\n")
    found = scan_chinese_labels(source)
    assert "真段标题" in found and "方向" in found
    assert "假段标题" not in found
