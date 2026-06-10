from __future__ import annotations

import csv
import json
import re
import socket
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Sequence, Tuple
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from .models import QuoteSnapshot, ThemeHeat


YAHOO_CHART_URL = (
    "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
    "?interval=1d&range=1y&includePrePost=false"
)
NASDAQ_HISTORICAL_URL = (
    "https://api.nasdaq.com/api/quote/{symbol}/historical"
    "?assetclass={asset_class}&fromdate={from_date}&todate={to_date}&limit=260"
)
CBOE_HISTORY_URLS = {
    "^VIX": "https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv",
    "^VXN": "https://cdn.cboe.com/api/global/us_indices/daily_prices/VXN_History.csv",
}
NASDAQ_FALLBACK_SYMBOLS = {
    "QQQ": ("QQQ", "etf"),
    "^NDX": ("NDX", "index"),
    "GLD": ("GLD", "etf"),
    "USO": ("USO", "etf"),
}
FRED_DGS10_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=DGS10"
YAHOO_HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "application/json,text/plain,*/*",
}
NASDAQ_HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "application/json, text/plain, */*",
    "Origin": "https://www.nasdaq.com",
    "Referer": "https://www.nasdaq.com/market-activity",
}
VCP_NASDAQ100_VALUATION_URL = "https://vcpscanner.com/market-valuation/nasdaq-100"
STOCKANALYSIS_QQQ_URL = "https://stockanalysis.com/etf/qqq/"


def _fetch_json(url: str) -> Dict:
    request = Request(url, headers=YAHOO_HEADERS)
    with urlopen(request, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def _fetch_json_with_headers(url: str, headers: Dict[str, str]) -> Dict:
    request = Request(url, headers=headers)
    with urlopen(request, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def _fetch_text(url: str) -> str:
    request = Request(url, headers=YAHOO_HEADERS)
    with urlopen(request, timeout=20) as response:
        return response.read().decode("utf-8", "ignore")


def _clean_close_series(payload: Dict) -> List[Tuple[date, float]]:
    result = payload["chart"]["result"][0]
    timestamps = result["timestamp"]
    closes = result["indicators"]["quote"][0]["close"]
    gmtoffset = int(result["meta"].get("gmtoffset", 0))
    series: List[Tuple[date, float]] = []
    for timestamp, close_value in zip(timestamps, closes):
        if close_value is None:
            continue
        traded_at = datetime.fromtimestamp(timestamp, tz=timezone.utc) + timedelta(seconds=gmtoffset)
        series.append((traded_at.date(), float(close_value)))
    return series


def _parse_float(value: object) -> float:
    text = str(value).strip().replace("$", "").replace(",", "")
    if text in {"", "--", "."}:
        raise ValueError(f"Missing numeric value: {value!r}")
    return float(text)


def _calc_return(series: List[float], end_index: int, lookback: int) -> float:
    if end_index < lookback:
        return 0.0
    anchor = series[end_index - lookback]
    if anchor == 0:
        return 0.0
    return (series[end_index] / anchor - 1.0) * 100.0


def _calc_sma_gap(series: List[float], end_index: int, window: int) -> float:
    if end_index + 1 < window:
        return 0.0
    window_values = series[end_index - window + 1 : end_index + 1]
    average = sum(window_values) / window
    if average == 0:
        return 0.0
    return (series[end_index] / average - 1.0) * 100.0


def _resolve_end_index(series: List[Tuple[date, float]], market_days_ago: int) -> int:
    if not series:
        raise ValueError("Price series is empty")
    end_index = len(series) - 1 - market_days_ago
    if end_index < 1:
        raise ValueError("Not enough history for requested market day offset")
    return end_index


def _snapshot_from_series(
    symbol: str,
    dated_closes: List[Tuple[date, float]],
    market_days_ago: int,
    source: str,
) -> QuoteSnapshot:
    dated_closes = [(item_date, close) for item_date, close in dated_closes if close > 0]
    dated_closes.sort(key=lambda item: item[0])
    deduped: Dict[date, float] = {}
    for item_date, close in dated_closes:
        deduped[item_date] = close
    dated_closes = list(deduped.items())
    closes = [close for _, close in dated_closes]
    if len(closes) < 3:
        raise ValueError(f"Not enough data for {symbol}")
    end_index = _resolve_end_index(dated_closes, market_days_ago)

    price = closes[end_index]
    previous = closes[end_index - 1]
    day_change = (price / previous - 1.0) * 100.0 if previous else 0.0
    return QuoteSnapshot(
        symbol=symbol,
        as_of=dated_closes[end_index][0],
        price=price,
        previous_close=previous,
        day_change_pct=day_change,
        return_5d=_calc_return(closes, end_index, 5),
        return_20d=_calc_return(closes, end_index, 20),
        sma_20_gap_pct=_calc_sma_gap(closes, end_index, 20),
        sma_50_gap_pct=_calc_sma_gap(closes, end_index, 50),
        sma_200_gap_pct=_calc_sma_gap(closes, end_index, 200),
        source=source,
    )


def _fetch_yahoo_quote_snapshot(symbol: str, market_days_ago: int = 0) -> QuoteSnapshot:
    payload = _fetch_json(YAHOO_CHART_URL.format(symbol=quote(symbol, safe="")))
    return _snapshot_from_series(
        symbol,
        _clean_close_series(payload),
        market_days_ago,
        "Yahoo Finance",
    )


def _fetch_nasdaq_quote_snapshot(symbol: str, market_days_ago: int = 0) -> QuoteSnapshot:
    mapped = NASDAQ_FALLBACK_SYMBOLS.get(symbol)
    if mapped is None:
        raise ValueError(f"No Nasdaq fallback mapping for {symbol}")
    nasdaq_symbol, asset_class = mapped
    to_date = date.today()
    from_date = to_date - timedelta(days=370)
    url = NASDAQ_HISTORICAL_URL.format(
        symbol=quote(nasdaq_symbol, safe=""),
        asset_class=quote(asset_class, safe=""),
        from_date=from_date.isoformat(),
        to_date=to_date.isoformat(),
    )
    payload = _fetch_json_with_headers(url, NASDAQ_HEADERS)
    rows = payload.get("data", {}).get("tradesTable", {}).get("rows") or []
    series: List[Tuple[date, float]] = []
    for row in rows:
        try:
            item_date = datetime.strptime(str(row.get("date") or ""), "%m/%d/%Y").date()
            close = _parse_float(row.get("close"))
        except (TypeError, ValueError):
            continue
        series.append((item_date, close))
    return _snapshot_from_series(symbol, series, market_days_ago, "Nasdaq API")


def _fetch_cboe_quote_snapshot(symbol: str, market_days_ago: int = 0) -> QuoteSnapshot:
    url = CBOE_HISTORY_URLS.get(symbol)
    if url is None:
        raise ValueError(f"No Cboe fallback mapping for {symbol}")
    text = _fetch_text(url)
    reader = csv.DictReader(text.splitlines())
    series: List[Tuple[date, float]] = []
    for row in reader:
        try:
            item_date = datetime.strptime(str(row.get("DATE") or ""), "%m/%d/%Y").date()
            close = _parse_float(row.get("CLOSE"))
        except (TypeError, ValueError):
            continue
        series.append((item_date, close))
    return _snapshot_from_series(symbol, series, market_days_ago, "Cboe")


def _fetch_fred_tnx_snapshot(market_days_ago: int = 0) -> QuoteSnapshot:
    text = _fetch_text(FRED_DGS10_URL)
    reader = csv.DictReader(text.splitlines())
    series: List[Tuple[date, float]] = []
    for row in reader:
        try:
            item_date = date.fromisoformat(str(row.get("observation_date") or ""))
            close = _parse_float(row.get("DGS10")) * 10.0
        except (TypeError, ValueError):
            continue
        series.append((item_date, close))
    return _snapshot_from_series("^TNX", series, market_days_ago, "FRED DGS10")


def _fetch_fallback_quote_snapshot(symbol: str, market_days_ago: int = 0) -> QuoteSnapshot:
    if symbol in NASDAQ_FALLBACK_SYMBOLS:
        return _fetch_nasdaq_quote_snapshot(symbol, market_days_ago)
    if symbol in CBOE_HISTORY_URLS:
        return _fetch_cboe_quote_snapshot(symbol, market_days_ago)
    if symbol == "^TNX":
        return _fetch_fred_tnx_snapshot(market_days_ago)
    raise ValueError(f"No fallback quote source for {symbol}")


def fetch_quote_snapshot(symbol: str, market_days_ago: int = 0) -> QuoteSnapshot:
    if symbol in NASDAQ_FALLBACK_SYMBOLS or symbol in CBOE_HISTORY_URLS:
        try:
            return _fetch_fallback_quote_snapshot(symbol, market_days_ago)
        except (HTTPError, URLError, ValueError, TimeoutError, socket.timeout, json.JSONDecodeError):
            pass

    yahoo_snapshot: QuoteSnapshot | None = None
    yahoo_error: Exception | None = None
    try:
        yahoo_snapshot = _fetch_yahoo_quote_snapshot(symbol, market_days_ago)
    except (HTTPError, URLError, ValueError, TimeoutError, socket.timeout, json.JSONDecodeError) as exc:
        yahoo_error = exc

    if symbol == "^TNX":
        try:
            fallback_snapshot = _fetch_fallback_quote_snapshot(symbol, market_days_ago)
        except (HTTPError, URLError, ValueError, TimeoutError, socket.timeout, json.JSONDecodeError):
            fallback_snapshot = None
        if fallback_snapshot and (
            yahoo_snapshot is None or fallback_snapshot.as_of > yahoo_snapshot.as_of
        ):
            if yahoo_snapshot is not None:
                fallback_snapshot.source = (
                    f"{fallback_snapshot.source}; Yahoo stale {yahoo_snapshot.as_of.isoformat()}"
                )
            return fallback_snapshot

    if yahoo_snapshot is not None:
        return yahoo_snapshot
    if symbol == "^TNX":
        return QuoteSnapshot(
            symbol=symbol,
            as_of=date.today(),
            price=0.0,
            previous_close=0.0,
            day_change_pct=0.0,
            return_5d=0.0,
            return_20d=0.0,
            sma_20_gap_pct=0.0,
            sma_50_gap_pct=0.0,
            sma_200_gap_pct=0.0,
            source="Unavailable; Yahoo/FRED failed",
        )
    if yahoo_error is not None:
        raise yahoo_error
    raise ValueError(f"Unable to fetch quote snapshot for {symbol}")


def fetch_first_available_snapshot(symbols: Sequence[str], market_days_ago: int = 0) -> QuoteSnapshot:
    last_error: Exception | None = None
    for symbol in symbols:
        try:
            return fetch_quote_snapshot(symbol, market_days_ago=market_days_ago)
        except (HTTPError, URLError, ValueError, TimeoutError, socket.timeout) as exc:
            last_error = exc
            continue
    if last_error is None:
        raise ValueError("No symbols provided")
    raise ValueError(f"Unable to fetch any symbol from {list(symbols)}: {last_error}")


def fetch_vcp_nasdaq100_valuation() -> Dict[str, object]:
    html = _fetch_text(VCP_NASDAQ100_VALUATION_URL)
    marker = '\\"index_name\\":\\"nasdaq100\\"'
    marker_index = html.find(marker)
    if marker_index == -1:
        raise ValueError("Nasdaq 100 valuation row not found")

    row_start = html.rfind("{", 0, marker_index)
    row_end = html.find("}", marker_index)
    if row_start == -1 or row_end == -1:
        raise ValueError("Nasdaq 100 valuation row is malformed")

    row_text = html[row_start : row_end + 1].replace('\\"', '"')
    row = json.loads(row_text)
    return {
        "trailing_pe": float(row["trailing_pe"]),
        "forward_pe": float(row["forward_pe"]),
        "source": "VCP Scanner Nasdaq 100",
        "as_of": str(row.get("snapshot_date") or ""),
    }


def fetch_stockanalysis_qqq_trailing_pe() -> Dict[str, object]:
    html = _fetch_text(STOCKANALYSIS_QQQ_URL)
    match = re.search(r"PE Ratio</td><td[^>]*>([0-9]+(?:\.[0-9]+)?)</td>", html)
    if not match:
        raise ValueError("QQQ trailing P/E not found")
    return {
        "trailing_pe": float(match.group(1)),
        "forward_pe": None,
        "source": "StockAnalysis QQQ",
        "as_of": "",
    }


def fetch_auto_valuation() -> Dict[str, object]:
    last_error: Exception | None = None
    for fetcher in (fetch_vcp_nasdaq100_valuation, fetch_stockanalysis_qqq_trailing_pe):
        try:
            return fetcher()
        except (HTTPError, URLError, ValueError, TimeoutError, socket.timeout, json.JSONDecodeError) as exc:
            last_error = exc
            continue
    raise ValueError(f"Unable to fetch automatic valuation data: {last_error}")


def load_universe(path: Path) -> List[Dict[str, str]]:
    with path.open("r", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        return list(reader)


def fetch_theme_heat(universe_rows: Iterable[Dict[str, str]], market_days_ago: int = 0) -> List[ThemeHeat]:
    grouped: Dict[str, List[QuoteSnapshot]] = {}
    cache: Dict[str, QuoteSnapshot] = {}
    for row in universe_rows:
        ticker = row["ticker"]
        theme = row["theme"]
        try:
            snapshot = cache.get(ticker)
            if snapshot is None:
                snapshot = fetch_quote_snapshot(ticker, market_days_ago=market_days_ago)
                cache[ticker] = snapshot
        except (HTTPError, URLError, ValueError, TimeoutError, socket.timeout):
            continue
        grouped.setdefault(theme, []).append(snapshot)

    heat_list: List[ThemeHeat] = []
    for theme, snapshots in grouped.items():
        member_count = len(snapshots)
        if member_count == 0:
            continue
        winners = sum(1 for item in snapshots if item.day_change_pct > 0)
        heat_list.append(
            ThemeHeat(
                theme=theme,
                avg_day_change_pct=sum(item.day_change_pct for item in snapshots) / member_count,
                avg_return_5d=sum(item.return_5d for item in snapshots) / member_count,
                avg_return_20d=sum(item.return_20d for item in snapshots) / member_count,
                winners_ratio=winners / member_count,
                member_count=member_count,
            )
        )
    return heat_list
