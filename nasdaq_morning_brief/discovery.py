from __future__ import annotations

import argparse
import html
import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import quote_plus
from urllib.request import Request, urlopen

from .config import load_config
from .policy import DEFAULT_IMPACT_DAYS_BY_CATEGORY, GOOGLE_NEWS_RSS_URL, HTTP_HEADERS, US_EASTERN


DEFAULT_DISCOVERY_QUERIES = (
    "IPO Nasdaq pricing date technology AI",
    "files to go public IPO AI technology",
    "Nasdaq listing IPO price range semiconductor AI",
    "Nasdaq 100 index inclusion rebalance AI semiconductor",
    "export controls chip restrictions AI semiconductor cloud",
    "FTC DOJ antitrust cloud AI technology",
    "tariff export license semiconductor AI technology",
    "Treasury refunding debt ceiling shutdown liquidity Nasdaq rates",
)

SOURCE_WEIGHTS = {
    "Reuters": 18,
    "Bloomberg": 18,
    "The Wall Street Journal": 16,
    "Financial Times": 16,
    "Axios": 14,
    "CNBC": 12,
    "Nasdaq": 12,
    "SEC": 16,
    "Associated Press": 10,
    "WSJ": 16,
    "The New York Times": 14,
    "NPR": 10,
    "Yahoo Finance": 8,
}

EVENT_PATTERNS = (
    {
        "category": "IPO",
        "tokens": (
            "ipo",
            "initial public offering",
            "s-1",
            "files publicly",
            "filed publicly",
            "files to go public",
            "prepares to file",
            "preliminary ipo paperwork",
            "go public",
            "nasdaq listing",
            "price range",
            "pricing date",
            "roadshow",
            "market debut",
        ),
        "channels": ("Nasdaq", "流动性", "成长股风险偏好"),
        "base_importance": 62,
        "impact_days": 21,
    },
    {
        "category": "指数调整",
        "tokens": (
            "nasdaq-100 inclusion",
            "nasdaq 100 inclusion",
            "index inclusion",
            "index rebalance",
            "special rebalance",
            "added to the nasdaq",
        ),
        "channels": ("指数权重", "被动资金", "QQQ"),
        "base_importance": 68,
        "impact_days": 14,
    },
    {
        "category": "科技监管",
        "tokens": (
            "export controls",
            "chip restrictions",
            "antitrust",
            "doj",
            "ftc",
            "eu commission",
            "tariff",
            "sanctions",
        ),
        "channels": ("监管风险", "半导体", "科技估值"),
        "base_importance": 66,
        "impact_days": 30,
    },
    {
        "category": "AI产业",
        "tokens": (
            "ai chip",
            "semiconductor",
            "data center",
            "gpu",
            "export license",
            "ai infrastructure",
        ),
        "channels": ("AI产业链", "盈利预期", "半导体"),
        "base_importance": 58,
        "impact_days": 14,
    },
    {
        "category": "流动性",
        "tokens": (
            "treasury refunding",
            "debt ceiling",
            "government shutdown",
            "liquidity drain",
            "quantitative tightening",
            "qt",
        ),
        "channels": ("流动性", "美债利率", "风险偏好"),
        "base_importance": 64,
        "impact_days": 21,
    },
)


@dataclass
class NewsItem:
    title: str
    link: str
    source: str
    published_at: datetime | None
    summary: str = ""


@dataclass
class DiscoveredEvent:
    event_date: date
    category: str
    title: str
    status: str
    importance: int
    confidence: int
    market_channels: list[str]
    summary: str
    short_term: str
    mid_term: str
    long_term: str
    impact_days: int
    first_seen: str
    last_seen: str
    sources: list[str] = field(default_factory=list)


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _fetch_text(url: str) -> str:
    request = Request(url, headers=HTTP_HEADERS)
    with urlopen(request, timeout=8) as response:
        return response.read().decode("utf-8", "ignore")


def _parse_rss_datetime(value: str) -> datetime | None:
    if not value:
        return None
    for fmt in ("%a, %d %b %Y %H:%M:%S %Z", "%a, %d %b %Y %H:%M:%S %z"):
        try:
            parsed = datetime.strptime(value, fmt)
            return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)
        except ValueError:
            continue
    return None


def _parse_google_news_items(rss_text: str) -> list[NewsItem]:
    root = ET.fromstring(rss_text)
    items: list[NewsItem] = []
    for item in root.findall("./channel/item")[:20]:
        title = html.unescape((item.findtext("title") or "").strip())
        link = (item.findtext("link") or "").strip()
        source_node = item.find("{*}source")
        source = html.unescape((source_node.text or "").strip()) if source_node is not None else ""
        published_at = _parse_rss_datetime(item.findtext("pubDate") or "")
        summary = html.unescape((item.findtext("description") or "").strip())
        if title:
            items.append(NewsItem(title=title, link=link, source=source, published_at=published_at, summary=summary))
    return items


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


def _extract_event_date(text: str, as_of: date) -> date | None:
    iso_match = re.search(r"\b(20\d{2})-(\d{1,2})-(\d{1,2})\b", text)
    if iso_match:
        try:
            return date(int(iso_match.group(1)), int(iso_match.group(2)), int(iso_match.group(3)))
        except ValueError:
            return None

    full_match = re.search(
        r"\b(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|"
        r"Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\.?\s+(\d{1,2})(?:,\s*(20\d{2}))?\b",
        text,
        re.I,
    )
    if not full_match:
        return None
    month = _month_number(full_match.group(1))
    if month is None:
        return None
    year = int(full_match.group(3)) if full_match.group(3) else as_of.year
    try:
        parsed = date(year, month, int(full_match.group(2)))
    except ValueError:
        return None
    if parsed < as_of - timedelta(days=30) and not full_match.group(3):
        try:
            parsed = date(year + 1, month, parsed.day)
        except ValueError:
            return None
    return parsed


def _category_match(text: str) -> dict[str, object] | None:
    lowered = re.sub(r"\bi\.p\.o\.?\b", "ipo", text.lower())
    matches = []
    for pattern in EVENT_PATTERNS:
        hits = sum(token in lowered for token in pattern["tokens"])
        if hits:
            matches.append((hits, int(pattern["base_importance"]), pattern))
    if not matches:
        return None
    return sorted(matches, key=lambda item: (item[0], item[1]), reverse=True)[0][2]


def _source_weight(source: str) -> int:
    for name, weight in SOURCE_WEIGHTS.items():
        if name.lower() in source.lower():
            return weight
    return 6 if source else 0


def _market_bonus(text: str) -> tuple[int, list[str]]:
    lowered = text.lower()
    bonus = 0
    channels: list[str] = []
    signals = (
        ("nasdaq", 12, "Nasdaq"),
        ("nasdaq 100", 12, "QQQ"),
        ("nasdaq-100", 12, "QQQ"),
        ("qqq", 10, "QQQ"),
        ("ai", 8, "AI"),
        ("semiconductor", 8, "半导体"),
        ("chip", 6, "半导体"),
        ("mega", 6, "超大规模"),
        ("billion", 5, "大额资金"),
        ("trillion", 10, "大额资金"),
    )
    for token, value, channel in signals:
        if token in lowered:
            bonus += value
            if channel not in channels:
                channels.append(channel)
    return bonus, channels


def _status_and_confidence(has_explicit_date: bool, importance: int, source_weight: int) -> tuple[str, int]:
    confidence = min(95, 35 + source_weight * 2 + (20 if has_explicit_date else 0) + (10 if importance >= 80 else 0))
    if has_explicit_date and source_weight >= 12 and importance >= 70:
        return "confirmed", confidence
    if has_explicit_date and importance >= 65:
        return "probable", confidence
    return "watch", confidence


def _event_key(event: DiscoveredEvent) -> str:
    normalized_title = re.sub(r"\W+", "", event.title.lower())[:80]
    return f"{event.event_date.isoformat()}|{event.category}|{normalized_title}"


def _compact_title(title: str) -> str:
    title = re.sub(r"\s+-\s+[^-]{2,40}$", "", title).strip()
    return title[:96]


def _build_event(item: NewsItem, as_of: date) -> DiscoveredEvent | None:
    text = f"{item.title} {item.summary}"
    pattern = _category_match(text)
    if pattern is None:
        return None
    event_date = _extract_event_date(text, as_of)
    has_explicit_date = event_date is not None
    if event_date is None:
        published = item.published_at.astimezone(US_EASTERN).date() if item.published_at else as_of
        event_date = published
    source_weight = _source_weight(item.source)
    market_bonus, market_channels = _market_bonus(text)
    importance = min(100, int(pattern["base_importance"]) + source_weight + market_bonus)
    status, confidence = _status_and_confidence(has_explicit_date, importance, source_weight)
    channels = list(dict.fromkeys([*pattern["channels"], *market_channels]))
    today = _now_utc().isoformat()
    title = _compact_title(item.title)
    return DiscoveredEvent(
        event_date=event_date,
        category=str(pattern["category"]),
        title=title,
        status=status,
        importance=importance,
        confidence=confidence,
        market_channels=channels,
        summary=f"{item.source or 'News'}：{title}；传导渠道：{'、'.join(channels)}。",
        short_term="非常规催化进入发酵期，短线观察QQQ、VXN、相关权重股和成交额是否确认风险偏好变化。",
        mid_term="中期看事件是否改变资金流向、盈利预期、监管约束或指数权重预期。",
        long_term="长期只有当事件改变科技股盈利中枢、监管框架或流动性环境时才调整核心配置。",
        impact_days=int(pattern["impact_days"]),
        first_seen=today,
        last_seen=today,
        sources=[item.link] if item.link else [],
    )


def _event_to_dict(event: DiscoveredEvent) -> dict[str, object]:
    return {
        "date": event.event_date.isoformat(),
        "category": event.category,
        "title": event.title,
        "status": event.status,
        "importance": event.importance,
        "confidence": event.confidence,
        "market_channels": event.market_channels,
        "summary": event.summary,
        "short_term": event.short_term,
        "mid_term": event.mid_term,
        "long_term": event.long_term,
        "impact_days": event.impact_days,
        "first_seen": event.first_seen,
        "last_seen": event.last_seen,
        "sources": event.sources,
    }


def _event_from_dict(row: dict[str, object]) -> DiscoveredEvent | None:
    try:
        event_date = date.fromisoformat(str(row.get("date") or ""))
    except ValueError:
        return None
    title = str(row.get("title") or "").strip()
    if not title:
        return None
    return DiscoveredEvent(
        event_date=event_date,
        category=str(row.get("category") or "事件").strip(),
        title=title,
        status=str(row.get("status") or "watch").strip(),
        importance=int(row.get("importance") or 0),
        confidence=int(row.get("confidence") or 0),
        market_channels=[str(item) for item in row.get("market_channels", [])] if isinstance(row.get("market_channels"), list) else [],
        summary=str(row.get("summary") or "").strip(),
        short_term=str(row.get("short_term") or "").strip(),
        mid_term=str(row.get("mid_term") or "").strip(),
        long_term=str(row.get("long_term") or "").strip(),
        impact_days=int(row.get("impact_days") or DEFAULT_IMPACT_DAYS_BY_CATEGORY.get(str(row.get("category") or ""), 14)),
        first_seen=str(row.get("first_seen") or ""),
        last_seen=str(row.get("last_seen") or ""),
        sources=[str(item) for item in row.get("sources", [])] if isinstance(row.get("sources"), list) else [],
    )


def read_discovered_events(path: Path) -> list[DiscoveredEvent]:
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    events = [_event_from_dict(item) for item in payload.get("events", [])]
    return [event for event in events if event is not None]


def write_discovered_events(path: Path, events: Iterable[DiscoveredEvent]) -> None:
    payload = {
        "fetched_at": _now_utc().isoformat(),
        "timezone": "America/New_York",
        "events": [_event_to_dict(event) for event in sorted(events, key=lambda item: (item.event_date, -item.importance, item.title))],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _merge_events(existing: Iterable[DiscoveredEvent], fresh: Iterable[DiscoveredEvent], as_of: date, retention_days: int) -> list[DiscoveredEvent]:
    merged = {_event_key(event): event for event in existing}
    for event in fresh:
        key = _event_key(event)
        previous = merged.get(key)
        if previous is None:
            merged[key] = event
            continue
        previous.importance = max(previous.importance, event.importance)
        previous.confidence = max(previous.confidence, event.confidence)
        previous.status = event.status if event.status in {"confirmed", "probable"} else previous.status
        previous.last_seen = event.last_seen
        previous.sources = list(dict.fromkeys([*previous.sources, *event.sources]))[:8]
        previous.market_channels = list(dict.fromkeys([*previous.market_channels, *event.market_channels]))
        previous.summary = event.summary if event.importance >= previous.importance else previous.summary

    cutoff = as_of - timedelta(days=retention_days)
    retained: list[DiscoveredEvent] = []
    for event in merged.values():
        if event.event_date < cutoff:
            continue
        if event.event_date + timedelta(days=event.impact_days) < as_of:
            event.status = "expired"
        retained.append(event)
    return retained


def _queries_from_config(raw_queries: str) -> tuple[str, ...]:
    queries = tuple(item.strip() for item in raw_queries.split("|") if item.strip())
    return queries or DEFAULT_DISCOVERY_QUERIES


def discover_events(config_path: str) -> list[DiscoveredEvent]:
    config = load_config(config_path).policy
    as_of = datetime.now(US_EASTERN).date()
    fresh: list[DiscoveredEvent] = []
    for query in _queries_from_config(config.discovery_queries):
        rss_url = GOOGLE_NEWS_RSS_URL.format(query=quote_plus(query))
        try:
            items = _parse_google_news_items(_fetch_text(rss_url))
        except (HTTPError, URLError, TimeoutError, ET.ParseError, OSError, ValueError):
            continue
        for item in items:
            event = _build_event(item, as_of)
            if event is None:
                continue
            if event.event_date > as_of + timedelta(days=config.discovery_lookahead_days):
                continue
            if event.importance < config.discovery_min_importance and event.status != "confirmed":
                continue
            fresh.append(event)

    existing = read_discovered_events(config.discovered_events_path)
    events = _merge_events(existing, fresh, as_of, config.discovery_retention_days)
    write_discovered_events(config.discovered_events_path, events)
    return events


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Refresh asynchronous market event discovery cache.")
    parser.add_argument("--config", default="config.yaml", help="Path to config yaml.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    events = discover_events(args.config)
    active = [event for event in events if event.status in {"confirmed", "probable"}]
    print(f"Discovered events cached: {len(events)} total, {len(active)} active/probable")
    for event in sorted(active, key=lambda item: (item.event_date, -item.importance))[:8]:
        print(f"{event.event_date.isoformat()} {event.status} {event.importance} {event.category} {event.title}")
    watch = [event for event in events if event.status == "watch"]
    for event in sorted(watch, key=lambda item: (-item.importance, item.event_date, item.title))[:8]:
        print(f"{event.event_date.isoformat()} watch {event.importance} {event.category} {event.title}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
