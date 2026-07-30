"""Shared news capture utilities for event discovery and event results."""

from __future__ import annotations

import html
import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import quote_plus
from urllib.request import Request, urlopen


GOOGLE_NEWS_RSS_URL = "https://news.google.com/rss/search?q={query}&hl=zh-CN&gl=CN&ceid=CN:zh-Hans"
ALLOWED_EVENT_RESULT_HOSTS = ("cls.cn", "wallstreetcn.com")
HTTP_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/124.0 Safari/537.36",
    "Accept": "application/json,text/html,text/plain,*/*",
}


@dataclass(frozen=True)
class CapturedArticle:
    source: str
    url: str
    body: str
    published_at: datetime | None


@dataclass(frozen=True)
class CapturedHeadline:
    title: str
    link: str
    source: str
    published_at: datetime | None
    summary: str


@dataclass(frozen=True)
class EventCaptureOutcome:
    articles: tuple[CapturedArticle, ...]
    diagnostics: tuple[str, ...]
    queries: tuple[str, ...]


def _fetch(url: str, timeout: int = 20) -> tuple[str, str]:
    request = Request(url, headers=HTTP_HEADERS)
    with urlopen(request, timeout=timeout) as response:
        return response.geturl(), response.read().decode("utf-8", "ignore")


def _parse_datetime(value: str) -> datetime | None:
    if not value:
        return None
    for fmt in ("%a, %d %b %Y %H:%M:%S %Z", "%a, %d %b %Y %H:%M:%S %z"):
        try:
            parsed = datetime.strptime(value, fmt)
            return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)
        except ValueError:
            continue
    return None


def _clean_text(value: str) -> str:
    value = re.sub(r"<[^>]+>", " ", value)
    return re.sub(r"\s+", " ", html.unescape(value)).strip()


def _article_body(markup: str) -> str:
    # Prefer publishers' structured article body. This is the article content,
    # rather than the search-result title or RSS description.
    for block in re.findall(r"<script[^>]+type=[\"']application/ld\+json[\"'][^>]*>(.*?)</script>", markup, re.I | re.S):
        try:
            decoded = json.loads(html.unescape(block))
        except json.JSONDecodeError:
            continue
        candidates = decoded if isinstance(decoded, list) else [decoded]
        for item in candidates:
            if isinstance(item, dict) and isinstance(item.get("articleBody"), str):
                body = _clean_text(item["articleBody"])
                if len(body) >= 80:
                    return body

    paragraphs = [_clean_text(item) for item in re.findall(r"<p[^>]*>(.*?)</p>", markup, re.I | re.S)]
    body = " ".join(item for item in paragraphs if len(item) >= 20)
    if len(body) >= 80:
        return body
    return ""


def capture_news_headlines(query: str, limit: int = 20) -> list[CapturedHeadline]:
    """Shared search-stage capturer used by discovery and result collection."""
    rss_url = GOOGLE_NEWS_RSS_URL.format(query=quote_plus(query))
    _, rss_text = _fetch(rss_url)
    root = ET.fromstring(rss_text)
    headlines: list[CapturedHeadline] = []
    for item in root.findall("./channel/item")[:limit]:
        title = html.unescape((item.findtext("title") or "").strip())
        link = (item.findtext("link") or "").strip()
        source_node = item.find("{*}source")
        source = html.unescape((source_node.text or "").strip()) if source_node is not None else ""
        if title:
            headlines.append(
                CapturedHeadline(
                    title=title,
                    link=link,
                    source=source,
                    published_at=_parse_datetime(item.findtext("pubDate") or ""),
                    summary=html.unescape((item.findtext("description") or "").strip()),
                )
            )
    return headlines


def capture_event_result_evidence(
    queries: list[str],
    limit: int = 2,
    candidates_per_query: int = 3,
) -> EventCaptureOutcome:
    """Read approved publisher articles and retain a reason for every rejected stage."""
    captured: list[CapturedArticle] = []
    seen_urls: set[str] = set()
    diagnostics: list[str] = []
    unique_queries = list(dict.fromkeys(item.strip() for item in queries if item.strip()))
    for query in unique_queries:
        try:
            headlines = capture_news_headlines(query, limit=candidates_per_query)
        except Exception as exc:
            diagnostics.append(f"候选搜索失败：{query}（{type(exc).__name__}）")
            continue
        if not headlines:
            diagnostics.append(f"无候选文章：{query}")
            continue
        for item in headlines:
            if not item.link:
                diagnostics.append(f"候选缺少链接：{query}")
                continue
            try:
                resolved_url, markup = _fetch(item.link, timeout=8)
            except Exception as exc:
                diagnostics.append(f"文章跳转失败：{item.source or query}（{type(exc).__name__}）")
                continue
            host = resolved_url.lower()
            if not any(allowed in host for allowed in ALLOWED_EVENT_RESULT_HOSTS):
                diagnostics.append(f"候选域名不在允许范围：{item.source or resolved_url}")
                continue
            body = _article_body(markup)
            if not body:
                diagnostics.append(f"正文解析失败：{resolved_url}")
                continue
            if resolved_url in seen_urls:
                diagnostics.append(f"重复文章已忽略：{resolved_url}")
                continue
            seen_urls.add(resolved_url)
            captured.append(
                CapturedArticle(
                    source=item.source or ("财联社" if "cls.cn" in host else "华尔街见闻"),
                    url=resolved_url,
                    body=body,
                    published_at=item.published_at,
                )
            )
            if len(captured) >= limit:
                return EventCaptureOutcome(tuple(captured), tuple(diagnostics), tuple(unique_queries))
    return EventCaptureOutcome(tuple(captured), tuple(diagnostics), tuple(unique_queries))
