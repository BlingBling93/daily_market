from __future__ import annotations

import csv
import html
import json
import re
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, List
from urllib.error import HTTPError, URLError
from urllib.parse import quote, quote_plus
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from .config import PolicyConfig
from .models import AdviceSnapshot, PolicyEvent, PolicySnapshot, QuoteSnapshot


DEFAULT_IMPACT_DAYS_BY_CATEGORY = {
    "FOMC": 5,
    "通胀": 3,
    "就业": 3,
    "财报": 5,
    "增长": 2,
    "监管": 14,
    "地缘": 14,
    "IPO": 5,
    "指数调整": 5,
    "科技监管": 14,
    "AI产业": 7,
    "流动性": 14,
}
LEGACY_IMPACT_DAYS_BY_CATEGORY = {
    "FOMC": 21,
    "通胀": 10,
    "就业": 10,
    "财报": 14,
    "增长": 10,
    "监管": 30,
    "地缘": 30,
    "IPO": 21,
    "指数调整": 14,
    "科技监管": 30,
    "AI产业": 14,
    "流动性": 21,
}
EXECUTION_LOOKAHEAD_DAYS_BY_CATEGORY = {
    "FOMC": 3,
    "通胀": 1,
    "就业": 2,
    "财报": 2,
    "增长": 1,
    "监管": 7,
    "地缘": 7,
    "IPO": 2,
    "指数调整": 2,
    "科技监管": 7,
    "AI产业": 3,
    "流动性": 5,
}
MAJOR_POLICY_CATEGORIES = {
    "FOMC",
    "通胀",
    "就业",
    "财报",
    "增长",
    "监管",
    "地缘",
    "IPO",
    "指数调整",
    "科技监管",
    "AI产业",
    "流动性",
}

FED_FOMC_URL = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"
BLS_RELEASE_URLS = {
    "通胀": ("CPI", "https://www.bls.gov/schedule/news_release/cpi.htm"),
    "就业": ("非农就业", "https://www.bls.gov/schedule/news_release/empsit.htm"),
    "就业-JOLTS": ("JOLTS职位空缺", "https://www.bls.gov/schedule/news_release/jolts.htm"),
}
BEA_RELEASE_URL = "https://apps.bea.gov/API/signup/release_dates.json"
NASDAQ_EARNINGS_URL = "https://api.nasdaq.com/api/calendar/earnings?date={event_date}"
GOOGLE_NEWS_RSS_URL = "https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en"
FRED_CSV_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}"
NVIDIA_NEWS_URL = "https://nvidianews.nvidia.com/news/nvidia-announces-financial-results-for-{quarter_slug}-quarter-fiscal-{fiscal_year}"
PREFERRED_MEDIA_SOURCES = {
    "Reuters",
    "The Wall Street Journal",
    "WSJ",
    "Associated Press",
    "AP News",
    "CNBC",
    "Bloomberg",
    "MarketWatch",
    "财联社",
    "华尔街见闻",
}
HTTP_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "application/json,text/html,text/plain,*/*",
    "Referer": "https://www.google.com/",
}
NASDAQ_HEADERS = {
    **HTTP_HEADERS,
    "Origin": "https://www.nasdaq.com",
    "Referer": "https://www.nasdaq.com/",
}
US_EASTERN = ZoneInfo("America/New_York")
RESULT_FETCH_GRACE_DAYS = 7
FOMC_SUMMARY = "美联储议息会议，美东日期口径；重点看政策声明、投票分歧、沃什发布会和沟通制度变化。"
FOMC_SHORT_TERM = "会前和发布会后波动可能放大，新增资金避免一次性追价。"
FOMC_MID_TERM = "中期看利率路径、2年/10年美债、资产负债表口径和沟通规则是否确认方向。"
FOMC_LONG_TERM = "长期影响取决于实际利率中枢和美联储沟通框架是否改变科技股估值约束。"


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _parse_date(value: str) -> date | None:
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        return None


def _fetch_text(url: str, headers: dict[str, str] | None = None) -> str:
    request = Request(url, headers=headers or HTTP_HEADERS)
    with urlopen(request, timeout=20) as response:
        return response.read().decode("utf-8", "ignore")


def _strip_tags(markup: str) -> str:
    without_scripts = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", markup, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", "\n", without_scripts)
    return html.unescape(text)


def _clean_lines(markup: str) -> list[str]:
    return [line.strip() for line in _strip_tags(markup).splitlines() if line.strip()]


def _month_number(name: str) -> int | None:
    months = {
        "jan": 1,
        "january": 1,
        "feb": 2,
        "february": 2,
        "mar": 3,
        "march": 3,
        "apr": 4,
        "april": 4,
        "may": 5,
        "jun": 6,
        "june": 6,
        "jul": 7,
        "july": 7,
        "aug": 8,
        "august": 8,
        "sep": 9,
        "sept": 9,
        "september": 9,
        "oct": 10,
        "october": 10,
        "nov": 11,
        "november": 11,
        "dec": 12,
        "december": 12,
    }
    return months.get(name.lower().rstrip("."))


def _parse_us_date(value: str, default_year: int | None = None) -> date | None:
    cleaned = re.sub(r"[\*,]", "", value).strip()
    match = re.search(r"([A-Za-z]+)\.?\s+(\d{1,2})(?:-\d{1,2})?\s+(\d{4})", cleaned)
    if not match:
        match = re.search(r"([A-Za-z]+)\.?\s+(\d{1,2})(?:-\d{1,2})?", cleaned)
    if not match:
        return None
    month = _month_number(match.group(1))
    if month is None:
        return None
    day_text = match.group(2)
    year = int(match.group(3)) if len(match.groups()) >= 3 and match.group(3) else default_year
    if year is None:
        return None
    try:
        return date(year, month, int(day_text))
    except ValueError:
        return None


def _event_key(event: PolicyEvent) -> tuple[date, str, str]:
    return event.event_date, event.category, event.title


def _event_id(event: PolicyEvent) -> str:
    return f"{event.event_date.isoformat()}|{event.category}|{event.title}"


def _category_priority(category: str) -> int:
    priorities = {
        "FOMC": 0,
        "通胀": 1,
        "就业": 2,
        "财报": 3,
        "增长": 4,
        "监管": 5,
        "地缘": 6,
        "IPO": 7,
        "指数调整": 8,
        "科技监管": 9,
        "AI产业": 10,
        "流动性": 11,
    }
    return priorities.get(category, 9)


def _parse_impact_days(row: dict[str, str], category: str, fallback: int) -> int:
    raw_value = (row.get("impact_days") or "").strip()
    if raw_value:
        try:
            return _normalize_impact_days(category, int(raw_value), fallback)
        except ValueError:
            pass
    return DEFAULT_IMPACT_DAYS_BY_CATEGORY.get(category, fallback)


def _normalize_impact_days(category: str, value: int | None, fallback: int) -> int:
    default = DEFAULT_IMPACT_DAYS_BY_CATEGORY.get(category, fallback)
    if value is None or value < 1:
        return default
    if value == LEGACY_IMPACT_DAYS_BY_CATEGORY.get(category):
        return default
    return value


def _normalize_discovered_category(category: str, title: str, summary: str) -> str:
    text = f"{title} {summary}".lower()
    if category == "流动性":
        liquidity_tokens = (
            "treasury",
            "debt ceiling",
            "shutdown",
            "liquidity",
            "quantitative tightening",
            " qt",
        )
        ai_tokens = ("ai", "nvidia", "semiconductor", "chip", "data center", "infrastructure")
        if not any(token in text for token in liquidity_tokens) and any(token in text for token in ai_tokens):
            return "AI产业"
    return category


def load_policy_events(path: Path, default_impact_days: int) -> List[PolicyEvent]:
    if not path.exists():
        return []

    events: List[PolicyEvent] = []
    with path.open("r", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            event_date = _parse_date(row.get("date", ""))
            title = (row.get("title") or "").strip()
            if event_date is None or not title:
                continue
            category = (row.get("category") or "政策").strip()
            events.append(
                PolicyEvent(
                    event_date=event_date,
                    category=category,
                    title=title,
                    stance=(row.get("stance") or "待确认").strip(),
                    summary=(row.get("summary") or "等待事件落地。").strip(),
                    short_term=(row.get("short_term") or "事件前后波动可能放大。").strip(),
                    mid_term=(row.get("mid_term") or "连续数据确认后再调整仓位中枢。").strip(),
                    long_term=(row.get("long_term") or "暂不改变长期核心配置逻辑。").strip(),
                    impact_days=_parse_impact_days(row, category, default_impact_days),
                )
            )
    return sorted(events, key=lambda item: item.event_date)


def _stance_from_discovered_category(category: str) -> str:
    if category in {"科技监管", "流动性"}:
        return "偏谨慎"
    if category in {"IPO", "指数调整", "AI产业"}:
        return "事件待确认"
    return "待确认"


def load_discovered_policy_events(config: PolicyConfig, as_of: date) -> List[PolicyEvent]:
    path = config.discovered_events_path
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []

    events: List[PolicyEvent] = []
    cutoff = as_of - timedelta(days=config.discovery_retention_days)
    max_future = as_of + timedelta(days=config.discovery_lookahead_days)
    for row in payload.get("events", []):
        if not isinstance(row, dict):
            continue
        status = str(row.get("status") or "").strip()
        if status not in {"confirmed", "probable"}:
            continue
        try:
            importance = int(row.get("importance") or 0)
        except (TypeError, ValueError):
            importance = 0
        if importance < config.discovery_min_importance:
            continue
        event_date = _parse_date(str(row.get("date") or ""))
        title = str(row.get("title") or "").strip()
        if event_date is None or not title:
            continue
        if event_date < cutoff or event_date > max_future:
            continue
        raw_category = str(row.get("category") or "事件").strip()
        summary = str(row.get("summary") or "非常规事件雷达发现的候选重大事件。").strip()
        category = _normalize_discovered_category(raw_category, title, summary)
        impact_days = DEFAULT_IMPACT_DAYS_BY_CATEGORY.get(category, config.default_impact_days)
        try:
            impact_days = _normalize_impact_days(category, int(row.get("impact_days") or impact_days), config.default_impact_days)
        except (TypeError, ValueError):
            impact_days = DEFAULT_IMPACT_DAYS_BY_CATEGORY.get(category, config.default_impact_days)
        if event_date + timedelta(days=impact_days) < as_of and event_date < as_of:
            continue
        channels = row.get("market_channels") if isinstance(row.get("market_channels"), list) else []
        channel_text = "、".join(str(item) for item in channels if str(item).strip())
        if channel_text and "传导渠道" not in summary:
            summary = f"{summary} 传导渠道：{channel_text}。"
        sources = row.get("sources") if isinstance(row.get("sources"), list) else []
        events.append(
            PolicyEvent(
                event_date=event_date,
                category=category,
                title=title,
                stance=_stance_from_discovered_category(category),
                summary=summary,
                short_term=str(row.get("short_term") or "非常规催化发酵期内，短线观察QQQ、VXN和相关权重股确认。").strip(),
                mid_term=str(row.get("mid_term") or "中期看事件是否改变资金流向、盈利预期或监管约束。").strip(),
                long_term=str(row.get("long_term") or "长期只有当事件改变盈利、监管或流动性中枢时才调整核心配置。").strip(),
                impact_days=impact_days,
                result_sources=[str(item) for item in sources],
            )
        )
    return sorted(events, key=lambda item: (item.event_date, _category_priority(item.category), item.title))


def _event_to_dict(event: PolicyEvent) -> dict[str, object]:
    return {
        "date": event.event_date.isoformat(),
        "category": event.category,
        "title": event.title,
        "stance": event.stance,
        "summary": event.summary,
        "short_term": event.short_term,
        "mid_term": event.mid_term,
        "long_term": event.long_term,
        "impact_days": event.impact_days,
        "result_summary": event.result_summary,
        "result_conclusion": event.result_conclusion,
        "result_sources": event.result_sources,
        "result_source_tier": event.result_source_tier,
    }


def _event_from_dict(row: dict[str, object], default_impact_days: int) -> PolicyEvent | None:
    event_date = _parse_date(str(row.get("date") or ""))
    title = str(row.get("title") or "").strip()
    if event_date is None or not title:
        return None
    category = str(row.get("category") or "政策").strip()
    raw_impact = row.get("impact_days")
    try:
        impact_days = (
            _normalize_impact_days(category, int(raw_impact), default_impact_days)
            if raw_impact not in (None, "")
            else DEFAULT_IMPACT_DAYS_BY_CATEGORY.get(category, default_impact_days)
        )
    except (TypeError, ValueError):
        impact_days = DEFAULT_IMPACT_DAYS_BY_CATEGORY.get(category, default_impact_days)
    summary = str(row.get("summary") or "等待事件落地。").strip()
    short_term = str(row.get("short_term") or "事件前后波动可能放大。").strip()
    mid_term = str(row.get("mid_term") or "连续数据确认后再调整仓位中枢。").strip()
    long_term = str(row.get("long_term") or "暂不改变长期核心配置逻辑。").strip()
    if category == "FOMC":
        if "鲍威尔" in summary or "点阵图" in summary or "经济预测" in summary:
            summary = FOMC_SUMMARY
        if "点阵图" in mid_term or "降息路径" in mid_term:
            mid_term = FOMC_MID_TERM
        if "利率中枢" in long_term or "估值框架" in long_term:
            long_term = FOMC_LONG_TERM
    return PolicyEvent(
        event_date=event_date,
        category=category,
        title=title,
        stance=str(row.get("stance") or "待确认").strip(),
        summary=summary,
        short_term=short_term,
        mid_term=mid_term,
        long_term=long_term,
        impact_days=impact_days,
        result_summary=str(row.get("result_summary") or "").strip(),
        result_conclusion=str(row.get("result_conclusion") or "").strip(),
        result_sources=row.get("result_sources") if isinstance(row.get("result_sources"), list) else [],
        result_source_tier=str(row.get("result_source_tier") or "").strip(),
    )


def _read_cached_events(path: Path, default_impact_days: int) -> tuple[List[PolicyEvent], datetime | None]:
    if not path.exists():
        return [], None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return [], None
    fetched_at = None
    fetched_raw = payload.get("fetched_at")
    if fetched_raw:
        try:
            fetched_at = datetime.fromisoformat(str(fetched_raw))
        except ValueError:
            fetched_at = None
    events = [
        event
        for event in (_event_from_dict(item, default_impact_days) for item in payload.get("events", []))
        if event is not None
    ]
    return sorted(events, key=lambda item: item.event_date), fetched_at


def _write_cached_events(path: Path, events: List[PolicyEvent]) -> None:
    payload = {
        "fetched_at": _now_utc().isoformat(),
        "timezone": "America/New_York",
        "events": [_event_to_dict(event) for event in sorted(events, key=lambda item: item.event_date)],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _cache_needs_refresh(
    events: List[PolicyEvent],
    fetched_at: datetime | None,
    as_of: date,
    refresh_hours: int,
    recalibrate_within_days: int,
) -> bool:
    if fetched_at is None:
        return True
    if fetched_at.tzinfo is None:
        fetched_at = fetched_at.replace(tzinfo=timezone.utc)
    if datetime.now(timezone.utc) - fetched_at >= timedelta(hours=refresh_hours):
        return True
    recalibrate_end = as_of + timedelta(days=recalibrate_within_days)
    return any(as_of <= item.event_date <= recalibrate_end for item in events)


def _merge_events(*event_groups: Iterable[PolicyEvent]) -> List[PolicyEvent]:
    merged: dict[tuple[date, str, str], PolicyEvent] = {}
    for group in event_groups:
        for event in group:
            merged[_event_key(event)] = event
    return sorted(merged.values(), key=lambda item: (item.event_date, _category_priority(item.category), item.title))


def _read_news_cache(path: Path) -> dict[str, dict[str, object]]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    entries = payload.get("entries", {})
    return entries if isinstance(entries, dict) else {}


def _write_news_cache(path: Path, entries: dict[str, dict[str, object]]) -> None:
    payload = {
        "fetched_at": _now_utc().isoformat(),
        "timezone": "America/New_York",
        "entries": entries,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _news_cache_entry_fresh(entry: dict[str, object], refresh_hours: int) -> bool:
    fetched_raw = entry.get("fetched_at")
    if not fetched_raw:
        return False
    try:
        fetched_at = datetime.fromisoformat(str(fetched_raw))
    except ValueError:
        return False
    if fetched_at.tzinfo is None:
        fetched_at = fetched_at.replace(tzinfo=timezone.utc)
    return _now_utc() - fetched_at < timedelta(hours=refresh_hours)


def _news_cache_entry_has_result(entry: dict[str, object]) -> bool:
    return bool(str(entry.get("summary") or "").strip() and str(entry.get("conclusion") or "").strip())


def _result_source_tier(entry: dict[str, object]) -> str:
    source_tier = str(entry.get("source_tier") or "").strip()
    if source_tier:
        return source_tier
    method = str(entry.get("method") or "")
    if method.startswith("direct_fred"):
        return "official_proxy"
    if method.startswith("direct_"):
        return "official"
    if method:
        return "media_single"
    return "unverified"


def _result_is_policy_grade(entry: dict[str, object]) -> bool:
    return _result_source_tier(entry) in {"official", "official_proxy"}


def _media_cache_entry_fresh(entry: dict[str, object]) -> bool:
    fetched_raw = entry.get("fetched_at")
    if not fetched_raw:
        return False
    try:
        fetched_at = datetime.fromisoformat(str(fetched_raw))
    except ValueError:
        return False
    if fetched_at.tzinfo is None:
        fetched_at = fetched_at.replace(tzinfo=timezone.utc)
    return _now_utc() - fetched_at < timedelta(hours=12)


def _event_active_on(event: PolicyEvent, event_date: date) -> bool:
    return event.event_date <= event_date <= event.event_date + timedelta(days=event.impact_days)


def _event_needs_result(event: PolicyEvent, event_date: date) -> bool:
    if event.category not in MAJOR_POLICY_CATEGORIES:
        return False
    result_end = event.event_date + timedelta(days=max(event.impact_days, RESULT_FETCH_GRACE_DAYS))
    return event.event_date <= event_date <= result_end


def _direct_result_supported(event: PolicyEvent) -> bool:
    return (
        (event.category == "通胀" and ("CPI" in event.title or "PCE" in event.title))
        or (event.category == "增长" and "GDP" in event.title)
        or (event.category == "财报" and event.title.startswith("NVDA"))
    )


def _news_query(event: PolicyEvent) -> str:
    if event.category == "FOMC":
        return f'Federal Reserve FOMC decision {event.event_date.isoformat()} Kevin Warsh communication rates'
    if event.category == "通胀":
        if "PCE" in event.title:
            return f'US PCE inflation personal income outlays {event.event_date.isoformat()}'
        return f'US CPI inflation data {event.event_date.isoformat()}'
    if event.category == "就业":
        return f'US jobs report unemployment nonfarm payrolls {event.event_date.isoformat()}'
    if event.category == "财报":
        symbol = event.title.replace("财报", "").strip()
        return f'{symbol} earnings results guidance {event.event_date.isoformat()}'
    if event.category == "IPO":
        return f'{event.title} IPO debut shares close Nasdaq {event.event_date.isoformat()}'
    return f'{event.title} {event.event_date.isoformat()}'


def _plain_text(markup: str) -> str:
    return re.sub(r"\s+", " ", _strip_tags(markup)).strip()


def _latest_fred_values(series_id: str) -> list[tuple[date, float]]:
    csv_text = _fetch_text(FRED_CSV_URL.format(series_id=quote(series_id)), HTTP_HEADERS)
    values: list[tuple[date, float]] = []
    for line in csv_text.splitlines()[1:]:
        raw_date, _, raw_value = line.partition(",")
        event_date = _parse_date(raw_date)
        if event_date is None or raw_value.strip() in {"", "."}:
            continue
        try:
            values.append((event_date, float(raw_value)))
        except ValueError:
            continue
    return values[-13:]


def _pct_change(current: float, previous: float) -> float:
    if previous == 0:
        return 0.0
    return (current / previous - 1.0) * 100.0


def _fetch_pce_direct_result(event: PolicyEvent) -> dict[str, object] | None:
    headline = _latest_fred_values("PCEPI")
    core = _latest_fred_values("PCEPILFE")
    if len(headline) < 13 or len(core) < 13:
        return None
    latest_date, latest_headline = headline[-1]
    previous_headline = headline[-2][1]
    year_ago_headline = headline[-13][1]
    latest_core = core[-1][1]
    previous_core = core[-2][1]
    year_ago_core = core[-13][1]

    headline_mom = _pct_change(latest_headline, previous_headline)
    headline_yoy = _pct_change(latest_headline, year_ago_headline)
    core_mom = _pct_change(latest_core, previous_core)
    core_yoy = _pct_change(latest_core, year_ago_core)

    if core_mom >= 0.30 or headline_mom >= 0.35:
        stance = "偏谨慎"
        conclusion = "PCE月度涨幅偏高，利率预期可能承压，事件影响期内降低追价和加仓冲动。"
    elif core_mom <= 0.20 and headline_mom <= 0.25:
        stance = "偏友好"
        conclusion = "PCE月度涨幅温和，利率压力阶段性缓和，可维持基础动作但仍分批执行。"
    else:
        stance = "待确认"
        conclusion = "PCE数据处于中性区间，继续观察美债收益率、美元和成长股反应。"

    summary = (
        f"FRED/BEA序列 {latest_date.isoformat()}：PCE物价指数环比{headline_mom:+.2f}%、同比{headline_yoy:+.2f}%；"
        f"核心PCE环比{core_mom:+.2f}%、同比{core_yoy:+.2f}%。"
    )
    return {
        "fetched_at": _now_utc().isoformat(),
        "summary": summary,
        "conclusion": conclusion,
        "stance": stance,
        "sources": [
            FRED_CSV_URL.format(series_id="PCEPI"),
            FRED_CSV_URL.format(series_id="PCEPILFE"),
        ],
        "method": "direct_fred_pce",
        "source_tier": "official_proxy",
    }


def _fetch_cpi_direct_result(event: PolicyEvent) -> dict[str, object] | None:
    headline = _latest_fred_values("CPIAUCSL")
    core = _latest_fred_values("CPILFESL")
    if len(headline) < 13 or len(core) < 13:
        return None
    latest_date, latest_headline = headline[-1]
    previous_headline = headline[-2][1]
    year_ago_headline = headline[-13][1]
    latest_core = core[-1][1]
    previous_core = core[-2][1]
    year_ago_core = core[-13][1]

    headline_mom = _pct_change(latest_headline, previous_headline)
    headline_yoy = _pct_change(latest_headline, year_ago_headline)
    core_mom = _pct_change(latest_core, previous_core)
    core_yoy = _pct_change(latest_core, year_ago_core)

    if core_mom >= 0.30 or headline_mom >= 0.35:
        stance = "偏谨慎"
        conclusion = "CPI月度涨幅偏高，利率预期可能承压，事件影响期内降低追价和加仓冲动。"
    elif core_mom <= 0.20 and headline_mom <= 0.25:
        stance = "偏友好"
        conclusion = "CPI月度涨幅温和，利率压力阶段性缓和，可维持基础动作但仍分批执行。"
    else:
        stance = "待确认"
        conclusion = "CPI数据处于中性区间，继续观察美债收益率、美元和成长股反应。"

    summary = (
        f"FRED/BLS序列 {latest_date.isoformat()}：CPI环比{headline_mom:+.2f}%、同比{headline_yoy:+.2f}%；"
        f"核心CPI环比{core_mom:+.2f}%、同比{core_yoy:+.2f}%。"
    )
    return {
        "fetched_at": _now_utc().isoformat(),
        "summary": summary,
        "conclusion": conclusion,
        "stance": stance,
        "sources": [
            FRED_CSV_URL.format(series_id="CPIAUCSL"),
            FRED_CSV_URL.format(series_id="CPILFESL"),
        ],
        "method": "direct_fred_cpi",
        "source_tier": "official_proxy",
    }


def _quarter_label(value_date: date) -> str:
    return f"{value_date.year}Q{((value_date.month - 1) // 3) + 1}"


def _fetch_gdp_direct_result(event: PolicyEvent) -> dict[str, object] | None:
    growth = _latest_fred_values("A191RL1Q225SBEA")
    real_gdp = _latest_fred_values("GDPC1")
    if not growth:
        return None
    latest_date, latest_growth = growth[-1]
    previous_growth = growth[-2][1] if len(growth) >= 2 else None
    real_level = real_gdp[-1][1] if real_gdp else None
    real_level_text = f"；实际GDP折年水平{real_level:,.1f}十亿美元" if real_level is not None else ""

    if latest_growth < 0:
        stance = "偏谨慎"
        conclusion = "GDP环比折年转负，增长韧性承压，事件影响期内降低追价和加仓冲动。"
    elif latest_growth >= 2.5:
        stance = "偏友好"
        conclusion = "GDP增长韧性较强，盈利基本面支撑仍在，但需同步观察利率是否上行。"
    else:
        stance = "待确认"
        conclusion = "GDP增长处于温和区间，继续观察利率、盈利预期和指数广度的二次确认。"

    previous_text = f"，前值{previous_growth:+.1f}%" if previous_growth is not None else ""
    summary = (
        f"FRED/BEA序列 {_quarter_label(latest_date)}：实际GDP环比折年{latest_growth:+.1f}%"
        f"{previous_text}{real_level_text}。"
    )
    return {
        "fetched_at": _now_utc().isoformat(),
        "summary": summary,
        "conclusion": conclusion,
        "stance": stance,
        "sources": [
            FRED_CSV_URL.format(series_id="A191RL1Q225SBEA"),
            FRED_CSV_URL.format(series_id="GDPC1"),
        ],
        "method": "direct_fred_gdp",
        "source_tier": "official_proxy",
    }


def _quarter_slug_from_event(event: PolicyEvent) -> str | None:
    match = re.search(r"(Jan|Apr|Jul|Oct)/20\d{2}", event.summary)
    if not match:
        return None
    return {
        "Jan": "fourth",
        "Apr": "first",
        "Jul": "second",
        "Oct": "third",
    }.get(match.group(1))


def _fiscal_year_from_event(event: PolicyEvent) -> int:
    match = re.search(r"/(20\d{2})", event.summary)
    if match:
        month = event.summary[match.start() - 3 : match.start()]
        year = int(match.group(1))
        return year + 1 if month in {"Apr", "Jul", "Oct"} else year
    return event.event_date.year + 1


def _money_value(pattern: str, text: str) -> str:
    match = re.search(pattern, text, re.I)
    return match.group(1) if match else "暂无"


def _fetch_nvda_direct_result(event: PolicyEvent) -> dict[str, object] | None:
    if not event.title.startswith("NVDA"):
        return None
    quarter_slug = _quarter_slug_from_event(event)
    if not quarter_slug:
        return None
    fiscal_year = _fiscal_year_from_event(event)
    url = NVIDIA_NEWS_URL.format(quarter_slug=quarter_slug, fiscal_year=fiscal_year)
    markup = _fetch_text(url, HTTP_HEADERS)
    text = _plain_text(markup)

    revenue = _money_value(r"reported record revenue .*? of \$(\d+(?:\.\d+)?) billion", text)
    if revenue == "暂无":
        revenue = _money_value(r"reported revenue .*? of \$(\d+(?:\.\d+)?) billion", text)
    data_center = _money_value(r"Data Center revenue of \$(\d+(?:\.\d+)?) billion", text)
    revenue_yoy = _money_value(r"record revenue of \$\d+(?:\.\d+)? billion, up .*? and up (\d+)% from a year ago", text)
    if revenue_yoy == "暂无":
        revenue_yoy = _money_value(r"reported revenue .*? of \$\d+(?:\.\d+)? billion, up .*? and up (\d+)% from a year ago", text)
    gross_margin = _money_value(r"non-GAAP gross margins were .*? and (\d+(?:\.\d+)?)%, respectively", text)
    eps = _money_value(r"non-GAAP earnings per diluted share were \$\d+(?:\.\d+)? and \$(\d+(?:\.\d+)?), respectively", text)
    next_revenue = _money_value(r"Revenue is expected to be \$(\d+(?:\.\d+)?) billion", text)
    ceo_quote = ""
    quote_match = re.search(r"“([^”]{80,360})” said Jensen Huang", text)
    if quote_match:
        ceo_quote = quote_match.group(1)

    if revenue == "暂无" and data_center == "暂无" and next_revenue == "暂无":
        return None

    friendly_signals = sum(value != "暂无" for value in (revenue, data_center, next_revenue))
    stance = "偏友好" if friendly_signals >= 2 else "待确认"
    conclusion = (
        "NVIDIA官方财报显示收入、数据中心业务和下季收入指引仍强，事件影响期内盈利基本面对纳指偏友好，但估值高位仍需分批执行。"
        if stance == "偏友好"
        else "NVIDIA官方财报关键数字不完整，事件影响期内继续观察权重股和VXN反应。"
    )
    summary = (
        f"NVIDIA官方财报：收入${revenue}B，同比+{revenue_yoy}%；数据中心收入${data_center}B；"
        f"Non-GAAP毛利率{gross_margin}%；Non-GAAP EPS ${eps}；下季收入指引${next_revenue}B。"
    )
    if ceo_quote:
        summary = f"{summary} 电话会/管理层要点：{ceo_quote[:180]}。"
    return {
        "fetched_at": _now_utc().isoformat(),
        "summary": summary,
        "conclusion": conclusion,
        "stance": stance,
        "sources": [url],
        "method": "direct_nvidia_release",
        "source_tier": "official",
    }


def _fetch_direct_event_result(event: PolicyEvent) -> dict[str, object] | None:
    if event.category == "通胀" and "CPI" in event.title:
        return _fetch_cpi_direct_result(event)
    if event.category == "通胀" and "PCE" in event.title:
        return _fetch_pce_direct_result(event)
    if event.category == "增长" and "GDP" in event.title:
        return _fetch_gdp_direct_result(event)
    if event.category == "财报" and event.title.startswith("NVDA"):
        return _fetch_nvda_direct_result(event)
    return None


def _parse_google_news_items(rss_text: str) -> list[dict[str, str]]:
    root = ET.fromstring(rss_text)
    items: list[dict[str, str]] = []
    for item in root.findall("./channel/item")[:8]:
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        source_node = item.find("{*}source")
        source = (source_node.text or "").strip() if source_node is not None else ""
        if title:
            items.append({"title": html.unescape(title), "link": link, "source": source})
    return items


def _is_preferred_media(source: str) -> bool:
    normalized = source.strip().lower()
    return any(item.lower() in normalized for item in PREFERRED_MEDIA_SOURCES)


def _select_media_items(items: list[dict[str, str]]) -> tuple[list[dict[str, str]], str]:
    preferred = [item for item in items if _is_preferred_media(item.get("source", ""))]
    selected = preferred[:3] if preferred else items[:3]
    distinct_sources = {item.get("source", "").strip().lower() for item in selected if item.get("source", "").strip()}
    source_tier = "media_confirmed" if len(distinct_sources) >= 2 and preferred else "media_single"
    return selected, source_tier


def _infer_result_conclusion(event: PolicyEvent, titles: list[str]) -> tuple[str, str]:
    joined = " ".join(titles).lower()
    hawkish_hits = sum(
        token in joined
        for token in (
            "hotter",
            "accelerates",
            "sticky",
            "above forecast",
            "higher than expected",
            "hawkish",
            "yields rise",
            "rates higher",
            "misses estimates",
            "cuts forecast",
            "weak guidance",
            "selloff",
        )
    )
    dovish_hits = sum(
        token in joined
        for token in (
            "cooler",
            "slows",
            "below forecast",
            "lower than expected",
            "dovish",
            "yields fall",
            "rate cuts",
            "beats estimates",
            "beat",
            "top estimates",
            "raises forecast",
            "strong guidance",
            "strong q2 revenue guidance",
            "sales and earnings beat",
            "share buyback",
            "rally",
            "jumps",
            "surges",
            "shares rise",
            "shares rose",
            "closes up",
            "closed up",
        )
    )
    if hawkish_hits > dovish_hits:
        return "偏谨慎", "新闻标题偏向利率/盈利压力，事件影响期内降低追价和加仓冲动。"
    if dovish_hits > hawkish_hits:
        return "偏友好", "新闻标题偏向利率缓和或盈利支撑，事件影响期内可维持基础动作但仍分批执行。"
    return "待确认", "相关新闻尚未形成明确方向，事件影响期内继续观察利率、VXN和权重股反应。"


def _fetch_event_result(event: PolicyEvent) -> dict[str, object] | None:
    direct_result = _fetch_direct_event_result(event)
    if direct_result:
        return direct_result

    query = quote_plus(_news_query(event))
    rss_text = _fetch_text(GOOGLE_NEWS_RSS_URL.format(query=query), HTTP_HEADERS)
    items = _parse_google_news_items(rss_text)
    if not items:
        return None
    selected_items, source_tier = _select_media_items(items)
    titles = [item["title"] for item in selected_items]
    stance, conclusion = _infer_result_conclusion(event, titles)
    source_text = "；".join(
        f"{item['source'] or 'News'}: {item['title']}" for item in selected_items
    )
    summary = (
        f"媒体转述（待官方校验）：{source_text}"
    )
    media_conclusion = (
        f"媒体标题初步指向“{stance}”，仅作为临时解读；仓位动作等待官方/FRED/公司IR数据确认。"
    )
    return {
        "fetched_at": _now_utc().isoformat(),
        "summary": summary,
        "conclusion": media_conclusion if conclusion else "媒体结果待官方校验，暂不改变基础动作。",
        "stance": "待官方确认",
        "sources": [item["link"] for item in selected_items if item.get("link")],
        "method": "media_google_news",
        "source_tier": source_tier,
    }


def _attach_event_results(config: PolicyConfig, events: List[PolicyEvent]) -> List[PolicyEvent]:
    current_et_date = datetime.now(US_EASTERN).date()
    entries = _read_news_cache(config.news_cache_path)
    changed = False
    enriched: list[PolicyEvent] = []

    for event in events:
        event_id = _event_id(event)
        entry = entries.get(event_id, {})
        entry_tier = _result_source_tier(entry) if entry else ""
        has_cached_result = _news_cache_entry_has_result(entry)
        if has_cached_result and entry_tier.startswith("media"):
            should_refresh = not _media_cache_entry_fresh(entry)
        elif has_cached_result:
            should_refresh = False
        elif event.event_date <= current_et_date:
            should_refresh = True
        else:
            should_refresh = not _news_cache_entry_fresh(entry, config.result_retry_hours)
            if _direct_result_supported(event) and not _result_is_policy_grade(entry):
                should_refresh = True
        if config.auto_fetch and _event_needs_result(event, current_et_date) and should_refresh:
            try:
                fetched = _fetch_event_result(event)
            except (HTTPError, URLError, TimeoutError, ET.ParseError, OSError, ValueError):
                fetched = None
            if fetched:
                entry = fetched
                entries[event_id] = entry
                changed = True
            elif not entry:
                entry = {"fetched_at": _now_utc().isoformat()}
                entries[event_id] = entry
                changed = True

        if entry:
            event.result_summary = str(entry.get("summary") or "")
            event.result_conclusion = str(entry.get("conclusion") or "")
            event.result_sources = list(entry.get("sources") or [])
            event.result_source_tier = _result_source_tier(entry)
            stance = str(entry.get("stance") or "")
            if _result_is_policy_grade(entry) and stance and stance != "待确认":
                event.stance = stance
        enriched.append(event)

    if changed:
        _write_news_cache(config.news_cache_path, entries)
    return enriched


def _fetch_fomc_events(as_of: date) -> List[PolicyEvent]:
    lines = _clean_lines(_fetch_text(FED_FOMC_URL))
    events: List[PolicyEvent] = []
    year = None
    month = None
    for line in lines:
        year_match = re.fullmatch(r"(20\d{2})\s+FOMC Meetings", line)
        if year_match:
            year = int(year_match.group(1))
            month = None
            continue
        if year is None or year < as_of.year - 1 or year > as_of.year + 1:
            continue
        month_value = _month_number(line)
        if month_value:
            month = month_value
            continue
        if month is None:
            continue
        date_match = re.fullmatch(r"(\d{1,2})(?:-(\d{1,2}))?\*?", line)
        if not date_match:
            continue
        day = int(date_match.group(2) or date_match.group(1))
        try:
            event_date = date(year, month, day)
        except ValueError:
            continue
        events.append(
            PolicyEvent(
                event_date=event_date,
                category="FOMC",
                title="FOMC利率决议",
                stance="待确认",
                summary=FOMC_SUMMARY,
                short_term=FOMC_SHORT_TERM,
                mid_term=FOMC_MID_TERM,
                long_term=FOMC_LONG_TERM,
                impact_days=DEFAULT_IMPACT_DAYS_BY_CATEGORY["FOMC"],
            )
        )
    return events


def _fetch_bls_release_events(as_of: date) -> List[PolicyEvent]:
    events: List[PolicyEvent] = []
    for category, (title_prefix, url) in BLS_RELEASE_URLS.items():
        category_name = "就业" if category.startswith("就业") else category
        text = " ".join(_clean_lines(_fetch_text(url)))

        def add_event(reference_month: str, release_date_text: str, release_time: str) -> None:
            event_date = _parse_us_date(release_date_text)
            if event_date is None or event_date.year < as_of.year - 1 or event_date.year > as_of.year + 1:
                return
            event = PolicyEvent(
                event_date=event_date,
                category=category_name,
                title=f"{title_prefix}发布",
                stance="待确认",
                summary=f"{reference_month}数据，{release_time} ET发布；美东日期口径。",
                short_term="数据落地前后利率和纳指期货波动可能放大。",
                mid_term="连续两到三次数据同向后，再调整仓位中枢。",
                long_term="只有通胀/就业趋势改变利率中枢时，才影响长期核心配置。",
                impact_days=DEFAULT_IMPACT_DAYS_BY_CATEGORY.get(category_name, 10),
            )
            if _event_key(event) not in {_event_key(item) for item in events}:
                events.append(event)

        for match in re.finditer(r"([A-Za-z]+ \d{4})\s+([A-Z][a-z]{2}\.?\s+\d{1,2},\s+20\d{2})\s+(\d{1,2}:\d{2}\s+[AP]M)", text):
            reference_month, release_date_text, release_time = match.groups()
            add_event(reference_month, release_date_text, release_time)
        for match in re.finditer(
            r"for\s+([A-Za-z]+\s+20\d{2})\s+is\s+scheduled\s+to\s+be\s+released\s+on\s+"
            r"([A-Za-z]+\.?\s+\d{1,2},\s+20\d{2}),\s+at\s+"
            r"(\d{1,2}:\d{2})\s+([AP])\.?M\.?",
            text,
            flags=re.I,
        ):
            reference_month, release_date_text, release_time, meridiem = match.groups()
            add_event(reference_month, release_date_text, f"{release_time} {meridiem.upper()}M")
    return events


def _fetch_bea_release_events(as_of: date) -> List[PolicyEvent]:
    payload = json.loads(_fetch_text(BEA_RELEASE_URL))
    events: List[PolicyEvent] = []
    for title, release_info in payload.items():
        if not isinstance(release_info, dict):
            continue
        if title not in {"Personal Income and Outlays", "Gross Domestic Product"}:
            continue
        is_pce = "Personal Income" in title or "Outlays" in title
        category = "通胀" if is_pce else "增长"
        event_title = "PCE/个人收入支出发布" if is_pce else "GDP发布"
        for raw_date in release_info.get("release_dates", []):
            try:
                released_at = datetime.fromisoformat(str(raw_date).replace("Z", "+00:00"))
            except ValueError:
                continue
            eastern_time = released_at.astimezone(US_EASTERN)
            event_date = eastern_time.date()
            if event_date.year < as_of.year - 1 or event_date.year > as_of.year + 1:
                continue
            events.append(
                PolicyEvent(
                    event_date=event_date,
                    category=category,
                    title=event_title,
                    stance="待确认",
                    summary=f"{title}，{eastern_time.strftime('%H:%M')} ET发布；美东日期口径。",
                    short_term="数据可能改变降息预期和成长股估值定价。",
                    mid_term="中期看PCE/GDP是否与CPI、就业数据形成同向确认。",
                    long_term="长期影响取决于通胀回落和增长韧性能否同时维持。",
                    impact_days=DEFAULT_IMPACT_DAYS_BY_CATEGORY.get(category, 10),
                )
            )
    return events


def _fetch_nasdaq_earnings_events(as_of: date, config: PolicyConfig) -> List[PolicyEvent]:
    symbols = {symbol.strip().upper() for symbol in config.earnings_symbols.split(",") if symbol.strip()}
    if not symbols:
        return []
    start_date = as_of - timedelta(days=DEFAULT_IMPACT_DAYS_BY_CATEGORY["财报"])
    end_date = as_of + timedelta(days=config.earnings_lookahead_days)
    events: List[PolicyEvent] = []
    current = start_date
    while current <= end_date:
        url = NASDAQ_EARNINGS_URL.format(event_date=quote(current.isoformat()))
        try:
            payload = json.loads(_fetch_text(url, NASDAQ_HEADERS))
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError):
            current += timedelta(days=1)
            continue
        for row in payload.get("data", {}).get("rows") or []:
            symbol = str(row.get("symbol") or "").upper()
            if symbol not in symbols:
                continue
            company = str(row.get("name") or symbol)
            report_time = str(row.get("time") or "time-not-supplied").replace("time-", "")
            quarter = str(row.get("fiscalQuarterEnding") or "待确认")
            eps = str(row.get("epsForecast") or "暂无EPS一致预期")
            events.append(
                PolicyEvent(
                    event_date=current,
                    category="财报",
                    title=f"{symbol}财报",
                    stance="待确认",
                    summary=f"{company}，{quarter}，{report_time}；EPS预期 {eps}。日期为美股日历口径。",
                    short_term="重点权重股财报前后可能直接影响QQQ缺口和盘后风险。",
                    mid_term="中期看AI、云、广告、硬件或电动车指引是否带动盈利预期上修/下修。",
                    long_term="长期影响取决于盈利增长能否支撑高估值。",
                    impact_days=DEFAULT_IMPACT_DAYS_BY_CATEGORY["财报"],
                )
            )
        current += timedelta(days=1)
    return events


def _fetch_automatic_events(config: PolicyConfig, as_of: date) -> List[PolicyEvent]:
    fetched: List[PolicyEvent] = []
    for fetcher in (
        _fetch_fomc_events,
        _fetch_bls_release_events,
        _fetch_bea_release_events,
    ):
        try:
            fetched.extend(fetcher(as_of))
        except (HTTPError, URLError, TimeoutError, ValueError, json.JSONDecodeError, OSError):
            continue
    try:
        fetched.extend(_fetch_nasdaq_earnings_events(as_of, config))
    except (HTTPError, URLError, TimeoutError, ValueError, json.JSONDecodeError, OSError):
        pass
    return sorted(fetched, key=lambda item: (item.event_date, item.category, item.title))


def load_policy_calendar(config: PolicyConfig, as_of: date) -> List[PolicyEvent]:
    manual_events = load_policy_events(config.events_path, config.default_impact_days)
    discovered_events = load_discovered_policy_events(config, as_of)
    cached_events, fetched_at = _read_cached_events(config.cache_path, config.default_impact_days)
    automatic_events = cached_events

    if config.auto_fetch and _cache_needs_refresh(
        cached_events,
        fetched_at,
        as_of,
        config.refresh_hours,
        config.recalibrate_within_days,
    ):
        fresh_events = _fetch_automatic_events(config, as_of)
        if fresh_events:
            automatic_events = fresh_events
            _write_cached_events(config.cache_path, automatic_events)

    return _attach_event_results(config, _merge_events(automatic_events, discovered_events, manual_events))


def _event_window(
    events: Iterable[PolicyEvent],
    as_of: date,
    lookahead_days: int,
    lookback_days: int,
) -> tuple[List[PolicyEvent], List[PolicyEvent]]:
    upcoming_end = as_of + timedelta(days=lookahead_days)
    recent_start = as_of - timedelta(days=lookback_days)
    upcoming = [item for item in events if as_of <= item.event_date <= upcoming_end]
    recent = [
        item
        for item in events
        if recent_start <= item.event_date < as_of
        and as_of <= item.event_date + timedelta(days=item.impact_days)
    ]
    upcoming = sorted(upcoming, key=lambda item: (item.event_date, _category_priority(item.category), item.title))
    recent = sorted(recent, key=lambda item: (item.event_date, _category_priority(item.category), item.title))
    return upcoming[:5], recent[-3:]


def _execution_event_window(
    upcoming: List[PolicyEvent],
    recent: List[PolicyEvent],
    as_of: date,
) -> tuple[List[PolicyEvent], List[PolicyEvent]]:
    execution_upcoming = [
        item
        for item in upcoming
        if 0 <= (item.event_date - as_of).days <= EXECUTION_LOOKAHEAD_DAYS_BY_CATEGORY.get(item.category, 1)
    ]
    execution_recent = [
        item
        for item in recent
        if as_of <= item.event_date + timedelta(days=item.impact_days)
    ]
    return execution_upcoming, execution_recent


def _next_major_event(events: Iterable[PolicyEvent], as_of: date) -> PolicyEvent | None:
    future_events = [
        item
        for item in events
        if item.event_date > as_of and item.category in MAJOR_POLICY_CATEGORIES
    ]
    if not future_events:
        return None
    return sorted(future_events, key=lambda item: (item.event_date, _category_priority(item.category), item.title))[0]


def _rate_note(us10y: QuoteSnapshot, oil: QuoteSnapshot) -> str:
    if us10y.day_change_pct >= 1.0:
        return "10年美债收益率上行，政策面更偏估值约束。"
    if us10y.day_change_pct <= -1.0:
        return "10年美债收益率回落，政策面短线更偏估值支撑。"
    if oil.day_change_pct >= 2.0:
        return "原油快速上行，通胀预期需要重新纳入风险预算。"
    if oil.day_change_pct <= -2.0:
        return "原油明显回落，通胀压力阶段性缓和。"
    return "利率和通胀代理变量没有给出强方向，政策面保持观察。"


def _stance_from_events(upcoming: List[PolicyEvent], recent: List[PolicyEvent], rate_note: str) -> str:
    joined = " ".join(item.stance for item in upcoming + recent)
    result_joined = " ".join(
        item.result_conclusion
        for item in upcoming + recent
        if item.result_source_tier in {"official", "official_proxy"}
    )
    if "鹰" in joined or "风险" in joined or "10年美债收益率上行" in rate_note:
        return "偏谨慎"
    if "鸽" in joined or "利好" in joined or "收益率回落" in rate_note:
        return "偏友好"
    if "降低追价" in result_joined or "压力" in result_joined:
        return "偏谨慎"
    if "盈利支撑" in result_joined or "利率缓和" in result_joined:
        return "偏友好"
    if upcoming:
        return "事件待确认"
    return "中性观察"


def _execution_note(
    advice: AdviceSnapshot,
    upcoming: List[PolicyEvent],
    recent: List[PolicyEvent],
    stance: str,
) -> str:
    execution_categories = {
        "FOMC",
        "通胀",
        "就业",
        "财报",
        "增长",
        "IPO",
        "指数调整",
        "科技监管",
        "AI产业",
        "流动性",
    }
    near_events = [item for item in upcoming if item.category in execution_categories]
    active_aftershocks = [item for item in recent if item.category in execution_categories]
    if near_events and advice.action in {"小幅加仓", "持有"}:
        return "重大事件落地前不追价，新增资金分批或延后执行。"
    if near_events and advice.action in {"小幅降仓", "防守", "暂停加仓"}:
        return "事件风险与防守倾向一致，先控制波动暴露，等数据和市场反应确认。"
    if active_aftershocks and advice.action in {"小幅加仓", "持有"}:
        return "近期重大事件影响仍在消化，新增资金继续分批，观察利率和指数广度是否确认。"
    if active_aftershocks and advice.action in {"小幅降仓", "防守", "暂停加仓"}:
        return "近期重大事件仍在影响定价，先降低波动暴露。"
    if stance == "偏谨慎":
        return "政策面偏谨慎，明日动作只执行半档，避免在利率上行日追高。"
    if stance == "偏友好" and advice.action == "小幅降仓":
        return "政策面短线偏友好，降仓信号可先降级为持有观察。"
    return "政策面不覆盖明日动作，按基础仓位框架小步执行。"


def _event_has_result(event: PolicyEvent) -> bool:
    return bool(event.result_conclusion or event.result_summary)


def _event_label(event: PolicyEvent) -> str:
    text = f"{event.title} {event.summary}".lower()
    if event.category == "FOMC":
        return "FOMC利率决议"
    if event.category == "通胀":
        if "pce" in text:
            return "PCE通胀数据"
        if "cpi" in text:
            return "CPI通胀数据"
        return "通胀数据"
    if event.category == "就业":
        return "就业数据"
    if event.category == "增长":
        return "增长数据"
    if event.category == "财报":
        symbol = event.title.replace("财报", "").strip()
        return f"{symbol or '权重股'}财报"
    if event.category == "IPO":
        if "spacex" in text:
            return "SpaceX IPO/纳斯达克上市"
        return "重点IPO事件"
    if event.category == "指数调整":
        return "指数调整事件"
    if event.category == "AI产业":
        return "AI产业催化"
    if event.category == "科技监管":
        return "科技监管事件"
    if event.category == "流动性":
        return "流动性事件"
    return event.category or "重点事件"


def _confirmation_focus(event: PolicyEvent) -> str:
    if event.category == "通胀":
        return "确认项是核心环比、10年美债收益率、美元和VXN是否同向缓和或再度上行。"
    if event.category == "FOMC":
        return "确认项是政策声明、投票分歧、沃什发布会、2年/10年美债和成长股估值反应是否一致。"
    if event.category == "就业":
        return "确认项是新增就业、失业率、薪资增速和美债利率是否给出同向信号。"
    if event.category == "财报":
        return "确认项是收入指引、毛利率、AI/云业务增速以及权重股盘后缺口是否被次日成交确认。"
    if event.category == "IPO":
        return "确认项是首日收盘表现、成交额、同赛道科技股联动、VXN和被动资金压力是否同时改善。"
    if event.category == "指数调整":
        return "确认项是被动资金买卖方向、权重股成交额和QQQ跟踪误差是否放大。"
    if event.category == "AI产业":
        return "确认项是半导体、云资本开支、盈利预期和纳指广度是否形成扩散。"
    if event.category in {"科技监管", "监管"}:
        return "确认项是监管约束是否转化为收入、供应链或估值折价，而不是只停留在新闻标题。"
    if event.category == "流动性":
        return "确认项是美债收益率、美元、融资条件和VXN是否同步收紧。"
    return "确认项是利率、波动率、成交额和指数广度是否给出同向信号。"


def _no_result_reasoning(event: PolicyEvent) -> tuple[str, str, str]:
    focus = _confirmation_focus(event)
    if event.category == "IPO":
        return (
            "暂无可靠落地数据时，短线不把传闻当成买入理由；先看首日定价、收盘涨跌、成交额和VXN是否确认风险偏好。",
            f"中期按资金再平衡事件处理，{focus}若高开回落或带动波动率上行，新增仓位继续延后；若放量收涨且科技股广度扩散，事件约束可降级。",
            "长期只有当IPO改变纳指权重结构、被动资金需求或科技股风险偏好中枢时，才调整核心仓位框架。",
        )
    if event.category == "通胀":
        return (
            "暂无官方通胀结果时，短线不预判方向；新增资金等待核心CPI/PCE和美债反应落地后再执行。",
            f"中期按利率定价链条推演，{focus}若核心通胀偏热且收益率上行，仓位约束延续；若通胀降温且收益率回落，恢复基础动作。",
            "长期只有当连续通胀数据改变实际利率中枢时，才改变纳指核心配置比例。",
        )
    if event.category == "FOMC":
        return (
            "暂无议息结果时，短线按事件前约束处理，避免在政策声明和沃什发布会前一次性加仓。",
            f"中期看政策路径是否被重新定价，{focus}偏鹰则降低追价，偏鸽且收益率回落才解除约束。",
            "长期只有当实际利率中枢或美联储沟通框架发生持续变化时，才影响长期仓位中枢。",
        )
    return (
        f"暂无可用落地数据时，短线先按观察处理；{focus}",
        f"中期看事件是否真正传导到盈利预期、资金流和指数广度，{focus}",
        "长期不因单条新闻调整核心仓位，只有基本面、监管或流动性中枢发生持续变化才改变配置框架。",
    )


def _impact_event(upcoming: List[PolicyEvent], recent: List[PolicyEvent]) -> PolicyEvent | None:
    if upcoming:
        return upcoming[0]
    resulted_recent = [item for item in recent if _event_has_result(item)]
    if resulted_recent:
        return sorted(resulted_recent, key=lambda item: (item.event_date, _category_priority(item.category), item.title))[-1]
    if recent:
        return recent[-1]
    return None


def _short_term_impact(event: PolicyEvent, as_of: date) -> str:
    label = _event_label(event)
    if event.event_date > as_of:
        days = (event.event_date - as_of).days
        return f"{label}还有{days}天落地，短线新增资金先按事件前约束执行，避免一次性追价。"
    if event.result_conclusion:
        return f"{label}已有落地信息：{event.result_conclusion}"
    return _no_result_reasoning(event)[0]


def _mid_term_impact(event: PolicyEvent, as_of: date) -> str:
    label = _event_label(event)
    if _event_has_result(event) and event.event_date <= as_of:
        return f"中期以{label}的落地信息为锚，{_confirmation_focus(event)}如果确认项与左侧结论一致，再恢复或收紧基础仓位动作。"
    return _no_result_reasoning(event)[1]


def _long_term_impact(event: PolicyEvent, as_of: date) -> str:
    label = _event_label(event)
    if _event_has_result(event) and event.event_date <= as_of:
        return f"长期不因{label}的单次结果改变核心配置；只有该结果继续改变盈利预期、实际利率或监管/流动性中枢，才调整长期仓位框架。"
    return _no_result_reasoning(event)[2]


def build_policy_snapshot(
    config: PolicyConfig,
    as_of: date,
    advice: AdviceSnapshot,
    us10y: QuoteSnapshot,
    oil: QuoteSnapshot,
) -> PolicySnapshot:
    events = load_policy_calendar(config, as_of)
    upcoming, recent = _event_window(events, as_of, config.lookahead_days, config.lookback_days)
    execution_upcoming, execution_recent = _execution_event_window(upcoming, recent, as_of)
    next_event = _next_major_event(events, as_of)
    if next_event and any(_event_key(next_event) == _event_key(item) for item in upcoming):
        next_event = None
    rate_note = _rate_note(us10y, oil)
    stance = _stance_from_events(upcoming, recent, rate_note)
    execution_note = _execution_note(advice, execution_upcoming, execution_recent, stance)
    impact_event = _impact_event(upcoming, recent)

    if upcoming:
        first = upcoming[0]
        detail = first.result_conclusion or first.summary
        summary = f"未来{config.lookahead_days}天重点：{first.title}；{detail}"
    elif recent:
        first = impact_event or recent[-1]
        elapsed_days = (as_of - first.event_date).days
        remaining_days = max(0, first.impact_days - elapsed_days)
        detail = first.result_conclusion or first.summary
        summary = f"仍在影响期：{first.title}；已过{elapsed_days}天，预计还需观察{remaining_days}天。{detail}"
    else:
        summary = f"未配置未来{config.lookahead_days}天重大事件；{rate_note}"

    if impact_event:
        short_term = _short_term_impact(impact_event, as_of)
        mid_term = _mid_term_impact(impact_event, as_of)
        long_term = _long_term_impact(impact_event, as_of)
    else:
        short_term = "没有明确事件催化时，短线仍以价格趋势、VXN和美债变化为主。"
        mid_term = "中期等待通胀、就业和财报数据形成连续方向后，再调整仓位中枢。"
        long_term = "长期核心逻辑仍取决于盈利增长、实际利率中枢和监管环境。"

    return PolicySnapshot(
        stance=stance,
        summary=summary,
        execution_note=execution_note,
        short_term=short_term,
        mid_term=mid_term,
        long_term=long_term,
        upcoming_events=upcoming,
        recent_events=recent,
        next_event=next_event,
        execution_upcoming_events=execution_upcoming,
        execution_recent_events=execution_recent,
    )
