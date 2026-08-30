"""brave / serper 两个适配器的解析（ai-trade-intel-closed-loop §7.1 · test_ai_intel_search_providers）。

**离线**：只喂各家 API 的真实响应形状，不发任何网络请求。
"""
from __future__ import annotations

from app.ai_auto.intel.search import (
    PLANNED_SLUGS,
    PROVIDER_SLUGS,
    BraveProvider,
    IntelSearchError,
    SerperProvider,
    _freshness_token,
    _parse_relative_date,
    resolve_search_provider,
)

NOW = 1_800_000_000


def test_provider_slugs_now_include_brave_and_serper():
    assert PROVIDER_SLUGS == ("none", "tavily", "brave", "serper")
    assert PLANNED_SLUGS == (), "v1 登记的那条待办已由本轮兑现"


def test_resolve_returns_the_right_adapter():
    assert resolve_search_provider(slug="brave", api_key="k").slug == "brave"
    assert resolve_search_provider(slug="serper", api_key="k").slug == "serper"
    assert resolve_search_provider(slug="brave", api_key="") is None
    assert resolve_search_provider(slug="typo", api_key="k") is None


def test_freshness_days_maps_to_provider_tokens():
    """两家同一映射：`<=1 → 日`、`<=7 → 周`、其余 → 月。"""
    for days, brave, serper in ((1, "pd", "qdr:d"), (7, "pw", "qdr:w"), (30, "pm", "qdr:m")):
        assert _freshness_token(days, tokens=("pd", "pw", "pm")) == brave
        assert _freshness_token(days, tokens=("qdr:d", "qdr:w", "qdr:m")) == serper


def test_brave_parses_page_age_rfc3339():
    docs = BraveProvider._parse({"web": {"results": [
        {"url": "https://a.com/x", "title": "T", "description": "D",
         "page_age": "2026-08-20T10:00:00Z", "profile": {"name": "A News"}},
        {"url": "", "title": "skipped"},
    ]}}, now=NOW)

    assert len(docs) == 1
    assert docs[0].snippet == "D"
    assert docs[0].publisher == "A News"
    assert docs[0].published_ts == 1787220000


def test_brave_missing_page_age_is_zero_not_now():
    docs = BraveProvider._parse({"web": {"results": [
        {"url": "https://a.com/x", "title": "T", "description": "D"}]}}, now=NOW)

    assert docs[0].published_ts == 0


def test_brave_bad_shape_raises_instead_of_returning_empty():
    """「一条都没搜到」与「检索源坏了」在界面上是两段不同的文案，混为一谈就是用降级掩盖故障。"""
    for payload in ({}, {"web": {}}, {"web": {"results": "nope"}}):
        try:
            BraveProvider._parse(payload, now=NOW)
        except IntelSearchError as exc:
            assert exc.code == "bad_response"
        else:
            raise AssertionError(f"应当抛 IntelSearchError：{payload}")


def test_serper_parses_relative_days_ago():
    docs = SerperProvider._parse({"organic": [
        {"link": "https://b.com/y", "title": "T2", "snippet": "S2", "date": "3 days ago",
         "source": "B"}]}, now=NOW)

    assert docs[0].published_ts == NOW - 3 * 86400
    assert docs[0].publisher == "B"


def test_serper_parses_absolute_month_day_year():
    docs = SerperProvider._parse({"organic": [
        {"link": "https://b.com/y", "title": "T", "snippet": "S", "date": "Jan 5, 2026"}]},
        now=NOW)

    assert docs[0].published_ts == 1767571200


def test_serper_unparseable_date_is_zero_not_now():
    """G-14：把「不知道什么时候发的」写成「刚刚发的」会让时效闸与升级门槛同时失守。"""
    for raw in ("前天", "yesterday", "", None, "3 fortnights ago", "2026/13/45"):
        assert _parse_relative_date(raw, now=NOW) == 0, raw


def test_serper_relative_units_cover_the_documented_set():
    for unit, span in (("minute", 60), ("hour", 3600), ("day", 86400), ("week", 7 * 86400),
                       ("month", 30 * 86400), ("year", 365 * 86400)):
        assert _parse_relative_date(f"2 {unit}s ago", now=NOW) == NOW - 2 * span
        assert _parse_relative_date(f"1 {unit} ago", now=NOW) == NOW - span
    assert _parse_relative_date("2 DAYS AGO", now=NOW) == NOW - 2 * 86400


def test_serper_accepts_url_alias_and_skips_rows_without_a_link():
    docs = SerperProvider._parse({"organic": [
        {"url": "https://b.com/z", "title": "T"}, {"title": "no link"}, "garbage"]}, now=NOW)

    assert [d.url for d in docs] == ["https://b.com/z"]
