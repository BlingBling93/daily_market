from __future__ import annotations

import csv
import json
import statistics
import time
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .config import AShareConfig
from .models import AShareDirection, AShareIdea, AShareSnapshot


EM_QUOTE_URL = "https://push2.eastmoney.com/api/qt/stock/get"
EM_KLINE_URL = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
SINA_QUOTE_URL = "https://hq.sinajs.cn/list={symbols}"
SINA_KLINE_URL = "https://quotes.sina.cn/cn/api/jsonp_v2.php/var%20_kline=/CN_MarketDataService.getKLineData"
EM_HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Referer": "https://quote.eastmoney.com/",
    "Accept": "application/json,text/plain,*/*",
}


@dataclass
class MarketSeries:
    price: float
    previous_close: float
    day_change_pct: float
    return_5d: float
    return_20d: float
    return_60d: float
    sma_20_gap_pct: float
    volume_ratio: float
    drawdown_60d_pct: float
    volatility_20d: float
    updated_at: str


@dataclass
class ThemeCandidate:
    theme: str
    proxy_ticker: str
    proxy_name: str
    style: str
    risk_level: str
    max_theme_weight: float
    market: Optional[MarketSeries]
    score: int
    action: str
    etf_action: str
    rationale: str


@dataclass
class FundamentalSnapshot:
    roe: float
    profit_growth: float
    revenue_growth: float
    cashflow_profit_ratio: float
    asset_liability_ratio: float
    pe_percentile: float
    eps_growth: float
    analyst_buy_ratio: float
    report_date: str
    sources: List[str]


@dataclass
class FundFlowSnapshot:
    main_net_amount: Optional[float]
    main_net_pct: Optional[float]
    super_net_amount: Optional[float]
    super_net_pct: Optional[float]
    big_net_amount: Optional[float]
    big_net_pct: Optional[float]
    trade_date: str
    source: str


_AKSHARE = None
_AKSHARE_FAILED = False
_ST_TICKERS: Optional[set[str]] = None
_FORECAST_ROWS: Optional[Dict[str, Dict[str, object]]] = None
_FUNDAMENTAL_CACHE: Dict[str, FundamentalSnapshot] = {}
_FLOW_CACHE: Dict[str, FundFlowSnapshot] = {}
_LOW_FREQ_CACHE: Dict[str, object] = {}
_LOW_FREQ_CACHE_PATH: Optional[Path] = None
AUTO_HOLDINGS_PER_THEME = 12
LEADER_HOLDING_RANK = 5
RELATED_THEME_MAP = {
    "通信": {"计算机", "半导体", "芯片", "人工智能"},
    "人工智能": {"计算机", "半导体", "芯片", "通信"},
    "机器人": {"半导体", "芯片", "计算机", "军工"},
    "半导体": {"芯片", "人工智能", "计算机"},
    "芯片": {"半导体", "人工智能", "计算机"},
    "新能源车": {"光伏", "半导体", "有色金属"},
    "有色金属": {"黄金", "新能源车"},
    "医疗器械": {"创新药"},
    "消费": {"酒"},
}


def _float(row: Dict[str, str], key: str, default: float = 0.0) -> float:
    value = row.get(key, "")
    if value is None or value in {"", "null", "None"}:
        return default
    return float(value)


def _text(row: Dict[str, str], key: str) -> str:
    value = row.get(key, "")
    if value is None:
        return ""
    return str(value).strip()


def _clip(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def _safe_float(value: object, default: Optional[float] = None) -> Optional[float]:
    if value in {None, "", "nan", "None", "--"}:
        return default
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    if result != result:
        return default
    return result


def _scale_positive(value: float, good: float, excellent: float) -> float:
    if excellent == good:
        return 50.0
    return _clip((value - good) / (excellent - good) * 100)


def _scale_inverse_percentile(value: float) -> float:
    return _clip(100.0 - value)


def _scale_range(value: float, poor: float, excellent: float) -> float:
    if excellent == poor:
        return 50.0
    return _clip((value - poor) / (excellent - poor) * 100)


def _init_lowfreq_cache(path: Path) -> None:
    global _LOW_FREQ_CACHE, _LOW_FREQ_CACHE_PATH, _ST_TICKERS, _FUNDAMENTAL_CACHE
    _LOW_FREQ_CACHE_PATH = path
    today = date.today().isoformat()
    data: Dict[str, object] = {}
    if path.exists():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict) and loaded.get("cache_date") == today:
                data = loaded
        except (OSError, json.JSONDecodeError):
            data = {}

    _LOW_FREQ_CACHE = {
        "cache_date": today,
        "st_tickers": data.get("st_tickers", None),
        "fundamentals": data.get("fundamentals", {}),
        "theme_holdings": data.get("theme_holdings", {}),
    }
    cached_st = _LOW_FREQ_CACHE.get("st_tickers")
    _ST_TICKERS = set(str(item).zfill(6) for item in cached_st) if isinstance(cached_st, list) and cached_st else None
    _FUNDAMENTAL_CACHE = {}
    cached_fundamentals = _LOW_FREQ_CACHE.get("fundamentals", {})
    if isinstance(cached_fundamentals, dict):
        for symbol, raw in cached_fundamentals.items():
            snapshot = _fundamental_from_cache(raw)
            if snapshot is not None:
                _FUNDAMENTAL_CACHE[str(symbol).zfill(6)] = snapshot


def _save_lowfreq_cache() -> None:
    if _LOW_FREQ_CACHE_PATH is None:
        return
    try:
        _LOW_FREQ_CACHE_PATH.write_text(
            json.dumps(_LOW_FREQ_CACHE, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
    except OSError:
        pass


def _fundamental_to_cache(snapshot: FundamentalSnapshot) -> Dict[str, object]:
    return {
        "roe": snapshot.roe,
        "profit_growth": snapshot.profit_growth,
        "revenue_growth": snapshot.revenue_growth,
        "cashflow_profit_ratio": snapshot.cashflow_profit_ratio,
        "asset_liability_ratio": snapshot.asset_liability_ratio,
        "pe_percentile": snapshot.pe_percentile,
        "eps_growth": snapshot.eps_growth,
        "analyst_buy_ratio": snapshot.analyst_buy_ratio,
        "report_date": snapshot.report_date,
        "sources": snapshot.sources,
    }


def _fundamental_from_cache(raw: object) -> Optional[FundamentalSnapshot]:
    if not isinstance(raw, dict):
        return None
    sources = raw.get("sources", [])
    if not isinstance(sources, list):
        sources = ["lowfreq_cache"]
    return FundamentalSnapshot(
        roe=_safe_float(raw.get("roe"), 0.0) or 0.0,
        profit_growth=_safe_float(raw.get("profit_growth"), 0.0) or 0.0,
        revenue_growth=_safe_float(raw.get("revenue_growth"), 0.0) or 0.0,
        cashflow_profit_ratio=_safe_float(raw.get("cashflow_profit_ratio"), 80.0) or 80.0,
        asset_liability_ratio=_safe_float(raw.get("asset_liability_ratio"), 50.0) or 50.0,
        pe_percentile=_safe_float(raw.get("pe_percentile"), 50.0) or 50.0,
        eps_growth=_safe_float(raw.get("eps_growth"), 0.0) or 0.0,
        analyst_buy_ratio=_safe_float(raw.get("analyst_buy_ratio"), 50.0) or 50.0,
        report_date=str(raw.get("report_date", "低频缓存")),
        sources=[str(item) for item in sources],
    )


def _secid(symbol: str) -> str:
    market = "1" if symbol.startswith(("5", "6", "9")) else "0"
    return f"{market}.{symbol}"


def _sina_symbol(symbol: str) -> str:
    prefix = "sh" if symbol.startswith(("5", "6", "9")) else "sz"
    return f"{prefix}{symbol}"


def _scale_price(raw_value: object, symbol: str) -> float:
    divisor = 1000.0 if symbol.startswith(("1", "5")) else 100.0
    return float(raw_value) / divisor


def _fetch_json(url: str, params: Dict[str, object]) -> Dict[str, object]:
    request = Request(f"{url}?{urlencode(params)}", headers=EM_HEADERS)
    last_error: Exception | None = None
    for attempt in range(1):
        try:
            with urlopen(request, timeout=15) as response:
                return json.loads(response.read().decode("utf-8"))
        except (OSError, TimeoutError, json.JSONDecodeError) as exc:
            last_error = exc
            time.sleep(0.8 * (attempt + 1))
    raise ValueError(f"Eastmoney request failed: {last_error}")


def _fetch_text(url: str, params: Dict[str, object] | None = None) -> str:
    full_url = url if params is None else f"{url}?{urlencode(params)}"
    request = Request(full_url, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn"})
    with urlopen(request, timeout=15) as response:
        return response.read().decode("utf-8", "ignore")


def _calc_return(closes: List[float], lookback: int) -> float:
    if len(closes) <= lookback:
        return 0.0
    anchor = closes[-1 - lookback]
    if anchor == 0:
        return 0.0
    return (closes[-1] / anchor - 1.0) * 100.0


def _calc_sma_gap(closes: List[float], window: int) -> float:
    if len(closes) < window:
        return 0.0
    average = sum(closes[-window:]) / window
    if average == 0:
        return 0.0
    return (closes[-1] / average - 1.0) * 100.0


def _calc_volume_ratio(amounts: List[float]) -> float:
    if len(amounts) < 20:
        return 1.0
    short = sum(amounts[-5:]) / 5
    base = sum(amounts[-20:]) / 20
    if base == 0:
        return 1.0
    return short / base


def _calc_drawdown(closes: List[float], window: int = 60) -> float:
    if not closes:
        return 0.0
    values = closes[-window:]
    high = max(values)
    if high == 0:
        return 0.0
    return (closes[-1] / high - 1.0) * 100.0


def _calc_volatility(closes: List[float], window: int = 20) -> float:
    if len(closes) <= window:
        return 0.0
    returns = [
        (closes[index] / closes[index - 1] - 1.0) * 100.0
        for index in range(len(closes) - window, len(closes))
        if closes[index - 1] != 0
    ]
    if len(returns) < 2:
        return 0.0
    return statistics.pstdev(returns)


def _fetch_market_series(symbol: str, market_days_ago: int = 0) -> Optional[MarketSeries]:
    try:
        quote = _fetch_json(
            EM_QUOTE_URL,
            {
                "secid": _secid(symbol),
                "fields": "f43,f57,f58,f60,f86,f170",
            },
        ).get("data") or {}
        kline_payload = _fetch_json(
            EM_KLINE_URL,
            {
                "secid": _secid(symbol),
                "fields1": "f1,f2,f3,f4,f5,f6",
                "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
                "klt": "101",
                "fqt": "1",
                "end": "20500101",
                "lmt": "90",
            },
        ).get("data") or {}
        klines = list(kline_payload.get("klines") or [])
    except (OSError, TimeoutError, ValueError, json.JSONDecodeError):
        return None

    closes: List[float] = []
    amounts: List[float] = []
    for line in klines:
        parts = str(line).split(",")
        if len(parts) < 7:
            continue
        closes.append(float(parts[2]))
        amounts.append(float(parts[6]))
    if len(closes) < 22:
        return None

    if market_days_ago > 0 and len(closes) > market_days_ago:
        closes = closes[: -market_days_ago]
        amounts = amounts[: -market_days_ago]

    price = _scale_price(quote.get("f43", closes[-1]), symbol)
    previous = _scale_price(quote.get("f60", closes[-2]), symbol)
    day_change_pct = float(quote.get("f170") or 0.0) / 100.0
    raw_updated_at = quote.get("f86")
    updated_at = date.today().isoformat()
    if raw_updated_at:
        updated_at = datetime.fromtimestamp(int(raw_updated_at)).strftime("%Y-%m-%d %H:%M")

    return MarketSeries(
        price=price,
        previous_close=previous,
        day_change_pct=day_change_pct,
        return_5d=_calc_return(closes, 5),
        return_20d=_calc_return(closes, 20),
        return_60d=_calc_return(closes, 60),
        sma_20_gap_pct=_calc_sma_gap(closes, 20),
        volume_ratio=_calc_volume_ratio(amounts),
        drawdown_60d_pct=_calc_drawdown(closes, 60),
        volatility_20d=_calc_volatility(closes, 20),
        updated_at=updated_at,
    )


def _fetch_sina_market_series(symbol: str, market_days_ago: int = 0) -> Optional[MarketSeries]:
    sina_symbol = _sina_symbol(symbol)
    try:
        quote_text = _fetch_text(SINA_QUOTE_URL.format(symbols=sina_symbol))
        data = quote_text.split('"', 1)[1].rsplit('"', 1)[0]
        parts = data.split(",")
        if len(parts) < 32:
            return None
        kline_text = _fetch_text(
            SINA_KLINE_URL,
            {
                "symbol": sina_symbol,
                "scale": "240",
                "ma": "no",
                "datalen": "90",
            },
        )
        json_text = kline_text.split("=(", 1)[1].rsplit(")", 1)[0]
        rows = json.loads(json_text)
    except (OSError, TimeoutError, ValueError, IndexError, json.JSONDecodeError):
        return None

    closes = [float(item["close"]) for item in rows]
    volumes = [float(item["volume"]) for item in rows]
    if len(closes) < 22:
        return None
    if market_days_ago > 0 and len(closes) > market_days_ago:
        closes = closes[: -market_days_ago]
        volumes = volumes[: -market_days_ago]

    previous = float(parts[2])
    price = float(parts[3])
    day_change_pct = (price / previous - 1.0) * 100.0 if previous else 0.0
    updated_at = f"{parts[30]} {parts[31][:5]}" if len(parts) > 31 else date.today().isoformat()

    return MarketSeries(
        price=price,
        previous_close=previous,
        day_change_pct=day_change_pct,
        return_5d=_calc_return(closes, 5),
        return_20d=_calc_return(closes, 20),
        return_60d=_calc_return(closes, 60),
        sma_20_gap_pct=_calc_sma_gap(closes, 20),
        volume_ratio=_calc_volume_ratio(volumes),
        drawdown_60d_pct=_calc_drawdown(closes, 60),
        volatility_20d=_calc_volatility(closes, 20),
        updated_at=updated_at,
    )


def _fetch_best_market_series(symbol: str, market_days_ago: int = 0) -> Optional[MarketSeries]:
    return _fetch_market_series(symbol, market_days_ago=market_days_ago) or _fetch_sina_market_series(
        symbol,
        market_days_ago=market_days_ago,
    )


def _akshare():
    global _AKSHARE, _AKSHARE_FAILED
    if _AKSHARE_FAILED:
        return None
    if _AKSHARE is not None:
        return _AKSHARE
    try:
        import akshare as ak  # type: ignore
    except Exception:
        _AKSHARE_FAILED = True
        return None
    _AKSHARE = ak
    return _AKSHARE


def _stock_market(symbol: str) -> str:
    if symbol.startswith(("5", "6", "9")):
        return "sh"
    if symbol.startswith(("8", "4")):
        return "bj"
    return "sz"


def _is_etf_row(row: Dict[str, str]) -> bool:
    asset_type = _text(row, "asset_type").lower()
    if asset_type == "etf":
        return True
    industry = _text(row, "industry").upper()
    style = _text(row, "style").upper()
    name = _text(row, "name").upper()
    return industry == "ETF" or style.endswith("ETF") or name.endswith("ETF")


def _is_st_stock(symbol: str) -> bool:
    global _ST_TICKERS
    if _ST_TICKERS is None:
        _ST_TICKERS = set()
        ak = _akshare()
        fetched = False
        if ak is not None:
            try:
                df = ak.stock_zh_a_st_em()
                if "代码" in df.columns:
                    _ST_TICKERS = {str(item).zfill(6) for item in df["代码"].tolist()}
                    fetched = bool(_ST_TICKERS)
            except Exception:
                _ST_TICKERS = set()
        if fetched:
            _LOW_FREQ_CACHE["st_tickers"] = sorted(_ST_TICKERS)
            _save_lowfreq_cache()
    return symbol in _ST_TICKERS


def _latest_row(df, date_columns: tuple[str, ...]):
    if df is None or getattr(df, "empty", True):
        return None
    for column in date_columns:
        if column in df.columns:
            try:
                return df.sort_values(column).iloc[-1]
            except Exception:
                return df.iloc[-1]
    return df.iloc[-1]


def _first_number(row: object, names: tuple[str, ...]) -> Optional[float]:
    for name in names:
        try:
            if name in row.index:
                value = _safe_float(row[name])
                if value is not None:
                    return value
        except Exception:
            continue
    return None


def _forecast_rows() -> Dict[str, Dict[str, object]]:
    global _FORECAST_ROWS
    if _FORECAST_ROWS is not None:
        return _FORECAST_ROWS
    _FORECAST_ROWS = {}
    ak = _akshare()
    if ak is None:
        return _FORECAST_ROWS
    try:
        df = ak.stock_profit_forecast_em()
    except Exception:
        return _FORECAST_ROWS
    for _, row in df.iterrows():
        code = str(row.get("代码", "")).zfill(6)
        if code:
            _FORECAST_ROWS[code] = row.to_dict()
    return _FORECAST_ROWS


def _pe_percentile(symbol: str, fallback: float) -> tuple[float, bool]:
    ak = _akshare()
    if ak is None:
        return fallback, False
    try:
        df = ak.stock_zh_valuation_baidu(symbol=symbol, indicator="市盈率(TTM)", period="近一年")
    except Exception:
        return fallback, False
    if df is None or getattr(df, "empty", True):
        return fallback, False
    value_column = "value" if "value" in df.columns else df.columns[-1]
    values = [_safe_float(item) for item in df[value_column].tolist()]
    values = [item for item in values if item is not None and item > 0]
    if len(values) < 30:
        return fallback, False
    latest = values[-1]
    rank = sum(1 for item in values if item <= latest) / len(values) * 100.0
    return _clip(rank), True


def _fetch_fundamentals(row: Dict[str, str]) -> FundamentalSnapshot:
    symbol = _text(row, "ticker")
    if symbol in _FUNDAMENTAL_CACHE:
        return _FUNDAMENTAL_CACHE[symbol]

    if _is_etf_row(row):
        snapshot = FundamentalSnapshot(
            roe=0.0,
            profit_growth=0.0,
            revenue_growth=0.0,
            cashflow_profit_ratio=80.0,
            asset_liability_ratio=_float(row, "risk_score", 60.0),
            pe_percentile=_float(row, "pe_percentile", 50.0),
            eps_growth=0.0,
            analyst_buy_ratio=_float(row, "catalyst_score", 50.0),
            report_date="ETF持仓型观察",
            sources=["watchlist:etf"],
        )
        _FUNDAMENTAL_CACHE[symbol] = snapshot
        return snapshot

    snapshot = FundamentalSnapshot(
        roe=_float(row, "roe"),
        profit_growth=_float(row, "profit_growth"),
        revenue_growth=_float(row, "revenue_growth"),
        cashflow_profit_ratio=80.0,
        asset_liability_ratio=_float(row, "risk_score", 50.0),
        pe_percentile=_float(row, "pe_percentile", 50.0),
        eps_growth=0.0,
        analyst_buy_ratio=50.0,
        report_date="本地字段",
        sources=["watchlist"],
    )
    ak = _akshare()
    if ak is not None:
        try:
            df = ak.stock_financial_analysis_indicator(symbol, start_year=str(date.today().year - 3))
            latest = _latest_row(df, ("日期", "报告期", "报告日期"))
            if latest is not None:
                snapshot.roe = _first_number(latest, ("净资产收益率(%)", "加权净资产收益率(%)")) or snapshot.roe
                snapshot.profit_growth = _first_number(latest, ("净利润增长率(%)", "归属母公司股东的净利润同比增长率(%)")) or snapshot.profit_growth
                snapshot.revenue_growth = _first_number(latest, ("主营业务收入增长率(%)", "营业收入增长率(%)")) or snapshot.revenue_growth
                snapshot.cashflow_profit_ratio = (
                    _first_number(latest, ("经营现金净流量与净利润的比率(%)", "经营现金净流量对净利润比率(%)"))
                    or snapshot.cashflow_profit_ratio
                )
                snapshot.asset_liability_ratio = _first_number(latest, ("资产负债率(%)",)) or snapshot.asset_liability_ratio
                snapshot.report_date = str(latest.get("日期", latest.get("报告期", latest.get("报告日期", "")))) or snapshot.report_date
                snapshot.sources.append("ak:financial_analysis")
        except Exception:
            pass

    pe_percentile, pe_ok = _pe_percentile(symbol, snapshot.pe_percentile)
    snapshot.pe_percentile = pe_percentile
    if pe_ok:
        snapshot.sources.append("ak:baidu_pe")

    forecast = _forecast_rows().get(symbol, {})
    eps_values = [
        _safe_float(value)
        for key, value in forecast.items()
        if "预测每股收益" in str(key)
    ]
    eps_values = [item for item in eps_values if item is not None and item > 0]
    if len(eps_values) >= 2:
        snapshot.eps_growth = (eps_values[1] / eps_values[0] - 1.0) * 100.0
    buy = _safe_float(forecast.get("机构投资评级(近六个月)-买入"), 0.0) or 0.0
    add = _safe_float(forecast.get("机构投资评级(近六个月)-增持"), 0.0) or 0.0
    neutral = _safe_float(forecast.get("机构投资评级(近六个月)-中性"), 0.0) or 0.0
    reduce = _safe_float(forecast.get("机构投资评级(近六个月)-减持"), 0.0) or 0.0
    sell = _safe_float(forecast.get("机构投资评级(近六个月)-卖出"), 0.0) or 0.0
    total_rating = buy + add + neutral + reduce + sell
    if total_rating > 0:
        snapshot.analyst_buy_ratio = (buy + add * 0.7) / total_rating * 100.0
        snapshot.sources.append("ak:profit_forecast")

    _FUNDAMENTAL_CACHE[symbol] = snapshot
    fundamentals = _LOW_FREQ_CACHE.setdefault("fundamentals", {})
    if isinstance(fundamentals, dict):
        fundamentals[symbol] = _fundamental_to_cache(snapshot)
        _save_lowfreq_cache()
    return snapshot


def _fetch_fund_flow(symbol: str) -> Optional[FundFlowSnapshot]:
    if symbol in _FLOW_CACHE:
        return _FLOW_CACHE[symbol]
    params = {
        "lmt": "0",
        "klt": "101",
        "secid": f"{1 if _stock_market(symbol) == 'sh' else 0}.{symbol}",
        "fields1": "f1,f2,f3,f7",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65",
        "ut": "b2884a393a59ad64002292a3e90d46a5",
        "_": int(time.time() * 1000),
    }
    try:
        data = _fetch_json("https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get", params)
        rows = list(((data.get("data") or {}).get("klines") or []))
        if not rows:
            return None
        parts = str(rows[-1]).split(",")
        if len(parts) < 11:
            return None
        snapshot = FundFlowSnapshot(
            trade_date=parts[0],
            main_net_amount=_safe_float(parts[1]),
            super_net_amount=_safe_float(parts[5]),
            big_net_amount=_safe_float(parts[4]),
            main_net_pct=_safe_float(parts[6]),
            super_net_pct=_safe_float(parts[10]),
            big_net_pct=_safe_float(parts[9]),
            source="eastmoney_fund_flow",
        )
        _FLOW_CACHE[symbol] = snapshot
        return snapshot
    except Exception:
        return None


def _fetch_theme_holdings(theme: ThemeCandidate, limit: int = AUTO_HOLDINGS_PER_THEME) -> List[Dict[str, object]]:
    holdings_cache = _LOW_FREQ_CACHE.setdefault("theme_holdings", {})
    if isinstance(holdings_cache, dict):
        cached = holdings_cache.get(theme.theme)
        if isinstance(cached, list) and len(cached) >= limit:
            return [item for item in cached if isinstance(item, dict)][:limit]

    ak = _akshare()
    rows: List[Dict[str, object]] = []
    if ak is not None:
        for year in (str(date.today().year), str(date.today().year - 1)):
            try:
                df = ak.fund_portfolio_hold_em(symbol=theme.proxy_ticker, date=year)
            except Exception:
                continue
            if df is None or getattr(df, "empty", True):
                continue
            try:
                latest_quarter = df["季度"].iloc[0] if "季度" in df.columns else None
                latest = df[df["季度"] == latest_quarter] if latest_quarter is not None else df
                latest = latest.sort_values("占净值比例", ascending=False).head(limit)
                for index, item in latest.iterrows():
                    symbol = str(item.get("股票代码", "")).zfill(6)
                    if not symbol or symbol == "000000":
                        continue
                    rows.append(
                        {
                            "ticker": symbol,
                            "name": str(item.get("股票名称", "")).strip(),
                            "holding_rank": len(rows) + 1,
                            "holding_pct": _safe_float(item.get("占净值比例"), 0.0) or 0.0,
                            "holding_value": _safe_float(item.get("持仓市值"), 0.0) or 0.0,
                            "quarter": str(item.get("季度", "")),
                        }
                    )
                if rows:
                    break
            except Exception:
                continue

    if isinstance(holdings_cache, dict) and rows:
        holdings_cache[theme.theme] = rows
        _save_lowfreq_cache()
    return rows[:limit]


def _default_risk_score(risk_level: str) -> float:
    return {"low": 35.0, "medium": 50.0, "high": 62.0}.get(risk_level, 55.0)


def _merge_auto_row(
    theme: ThemeCandidate,
    holding: Dict[str, object],
    local_rows_by_ticker: Dict[str, Dict[str, str]],
    selection_bucket: str | None = None,
) -> Dict[str, str]:
    ticker = str(holding.get("ticker", "")).zfill(6)
    local = local_rows_by_ticker.get(ticker, {})
    holding_pct = _safe_float(holding.get("holding_pct"), 0.0) or 0.0
    holding_rank = int(_safe_float(holding.get("holding_rank"), 99.0) or 99)
    bucket = selection_bucket or ("主线龙头" if holding_rank <= LEADER_HOLDING_RANK or holding_pct >= 8 else "右侧初期")
    row = {
        "initial_list": "auto_candidate",
        "ticker": ticker,
        "yahoo_symbol": f"{ticker}.SS" if ticker.startswith(("5", "6", "9")) else f"{ticker}.SZ",
        "theme": theme.theme,
        "name": str(holding.get("name", "")).strip() or local.get("name", ticker),
        "industry": local.get("industry", theme.theme),
        "style": bucket,
        "current_weight": local.get("current_weight", "0"),
        "roe": local.get("roe", "0"),
        "profit_growth": local.get("profit_growth", "0"),
        "revenue_growth": local.get("revenue_growth", "0"),
        "fcf_yield": local.get("fcf_yield", "0"),
        "dividend_yield": local.get("dividend_yield", "0"),
        "pe_percentile": local.get("pe_percentile", "50"),
        "pb_percentile": local.get("pb_percentile", "50"),
        "catalyst_score": local.get("catalyst_score", str(round(_clip(52 + holding_pct * 2.2)))),
        "risk_score": local.get("risk_score", str(_default_risk_score(theme.risk_level))),
        "governance_score": local.get("governance_score", "70"),
        "thesis": local.get(
            "thesis",
            f"{theme.theme}方向ETF核心成分，来自{theme.proxy_name}权重靠前持仓，进入当日自动深筛。",
        ),
        "catalysts": local.get("catalysts", f"{theme.theme}方向走强、ETF成分权重靠前、量价和资金确认"),
        "risks": local.get("risks", "方向回落、短线拥挤、基本面或订单验证不及预期"),
        "invalidation": local.get("invalidation", "跌破趋势支撑或资金流连续转弱"),
        "selection_bucket": bucket,
        "holding_pct": f"{holding_pct:.4f}",
        "holding_rank": str(holding_rank),
        "auto_source": f"{theme.proxy_name} {theme.proxy_ticker} {holding.get('quarter', '')} 权重{holding_pct:.2f}%",
    }
    return row


def _merge_related_row(
    theme: ThemeCandidate,
    local: Dict[str, str],
) -> Dict[str, str]:
    row = dict(local)
    local_theme = _text(local, "theme") or _text(local, "industry")
    row["initial_list"] = "auto_related"
    row["theme"] = theme.theme
    row["industry"] = local.get("industry", local_theme)
    row["style"] = "扩散补涨"
    row["selection_bucket"] = "扩散补涨"
    row["holding_pct"] = "0"
    row["holding_rank"] = "99"
    row["auto_source"] = f"{theme.theme}强方向关联主题：{local_theme}"
    row["thesis"] = row.get("thesis") or f"{theme.theme}主线外溢到{local_theme}方向，进入扩散补涨深筛。"
    row["catalysts"] = row.get("catalysts") or f"{theme.theme}方向扩散、相关主题量价修复"
    row["risks"] = row.get("risks") or "主线扩散失败、资金回流龙头或方向整体降温"
    row["invalidation"] = row.get("invalidation") or "相关主题无法跟随主线走强"
    return row


def _build_auto_candidate_rows(
    themes: List[ThemeCandidate],
    local_rows: List[Dict[str, str]],
    needed: int,
) -> List[Dict[str, str]]:
    local_rows_by_ticker = {_text(row, "ticker"): row for row in local_rows}
    candidates: Dict[str, Dict[str, str]] = {}
    for theme in themes:
        if theme.action == "暂缓":
            continue
        for holding in _fetch_theme_holdings(theme):
            row = _merge_auto_row(theme, holding, local_rows_by_ticker)
            ticker = _text(row, "ticker")
            if not ticker or _is_st_stock(ticker):
                continue
            existing = candidates.get(ticker)
            if existing is None or _float(row, "catalyst_score", 50.0) > _float(existing, "catalyst_score", 50.0):
                candidates[ticker] = row
        related_themes = RELATED_THEME_MAP.get(theme.theme, set())
        for local in local_rows:
            local_theme = _text(local, "theme") or _text(local, "industry")
            ticker = _text(local, "ticker")
            if local_theme not in related_themes or not ticker or _is_st_stock(ticker):
                continue
            candidates.setdefault(ticker, _merge_related_row(theme, local))
    return list(candidates.values())


def _theme_score(market: Optional[MarketSeries], risk_level: str) -> int:
    if market is None:
        return 45

    trend = _clip(50 + market.return_20d * 2.0 + market.return_60d * 0.8)
    reversal = 50.0
    if market.return_60d > 5 and -8 <= market.drawdown_60d_pct <= -3:
        reversal += 18
    if market.return_20d > 0 and market.return_60d < 0 and market.return_5d > 2:
        reversal += 15
    if market.return_5d > 8 or market.sma_20_gap_pct > 10:
        reversal -= 22
    liquidity = _clip(50 + (market.volume_ratio - 1.0) * 80)
    crowding = 70.0
    crowding -= max(0.0, market.sma_20_gap_pct - 8.0) * 4
    crowding -= max(0.0, market.volatility_20d - 2.5) * 7
    crowding += max(-8.0, market.drawdown_60d_pct) * -1.2
    macro_fit = {"low": 62.0, "medium": 55.0, "high": 50.0}.get(risk_level, 55.0)

    total = trend * 0.35 + reversal * 0.15 + liquidity * 0.20 + _clip(crowding) * 0.15 + macro_fit * 0.15
    return round(_clip(total))


def _theme_action(score: int, market: Optional[MarketSeries]) -> str:
    if market and (market.return_5d > 8 or market.sma_20_gap_pct > 10):
        return "暂缓"
    if score >= 68:
        return "重点跟踪"
    if score >= 58:
        return "观察"
    if score >= 50:
        return "轻跟踪"
    return "暂缓"


def _etf_position_action(score: int, market: Optional[MarketSeries]) -> str:
    if market is None:
        return "暂不配置"
    if market.return_5d > 12 and (market.day_change_pct < 0 or market.volume_ratio < 1.0):
        return "减仓降温"
    if market.return_5d > 12 or market.sma_20_gap_pct > 14:
        return "减仓提醒"
    if market.return_5d > 8 or market.sma_20_gap_pct > 10:
        return "等回调"
    if score >= 72 and market.return_5d <= 6 and market.sma_20_gap_pct <= 8 and market.volume_ratio >= 1.1:
        return "可小幅加仓"
    if score >= 68:
        return "持有观察"
    if score < 58:
        return "暂不配置"
    return "持有观察"


def _theme_rationale(theme: str, action: str, market: Optional[MarketSeries]) -> str:
    if market is None:
        return f"{theme} 方向实时数据缺失，暂时只保留观察。"
    if action == "重点跟踪":
        return f"{theme} 方向趋势和成交配合较好，优先深筛核心股。"
    if action == "观察":
        return f"{theme} 方向信号进入观察区，等待趋势或成交进一步确认。"
    if action == "轻跟踪":
        return f"{theme} 方向没有形成强共振，只做轻量跟踪。"
    return f"{theme} 方向短线过热或趋势不足，暂缓深筛个股。"


def _load_theme_candidates(path: Path, market_days_ago: int) -> List[ThemeCandidate]:
    with path.open("r", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))

    candidates: List[ThemeCandidate] = []
    for row in rows:
        market = _fetch_best_market_series(_text(row, "proxy_ticker"), market_days_ago=market_days_ago)
        score = _theme_score(market, _text(row, "risk_level"))
        action = _theme_action(score, market)
        etf_action = _etf_position_action(score, market)
        candidates.append(
            ThemeCandidate(
                theme=_text(row, "theme"),
                proxy_ticker=_text(row, "proxy_ticker"),
                proxy_name=_text(row, "proxy_name"),
                style=_text(row, "style"),
                risk_level=_text(row, "risk_level"),
                max_theme_weight=_float(row, "max_theme_weight", 0.10),
                market=market,
                score=score,
                action=action,
                etf_action=etf_action,
                rationale=_theme_rationale(_text(row, "theme"), action, market),
            )
        )
    return sorted(candidates, key=lambda item: item.score, reverse=True)


def _save_theme_heat_history(path: Path, themes: List[ThemeCandidate], keep_batches: int = 2) -> None:
    fieldnames = [
        "pulled_at",
        "theme",
        "proxy_ticker",
        "proxy_name",
        "style",
        "risk_level",
        "max_theme_weight",
        "score",
        "direction_action",
        "etf_action",
        "price",
        "day_change_pct",
        "return_5d",
        "return_20d",
        "return_60d",
        "volume_ratio",
        "sma_20_gap_pct",
        "drawdown_60d_pct",
        "volatility_20d",
        "market_updated_at",
    ]
    pulled_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    existing: List[Dict[str, str]] = []
    if path.exists():
        with path.open("r", encoding="utf-8") as handle:
            existing = list(csv.DictReader(handle))

    rows: List[Dict[str, str]] = []
    for theme in themes:
        market = theme.market
        rows.append(
            {
                "pulled_at": pulled_at,
                "theme": theme.theme,
                "proxy_ticker": theme.proxy_ticker,
                "proxy_name": theme.proxy_name,
                "style": theme.style,
                "risk_level": theme.risk_level,
                "max_theme_weight": f"{theme.max_theme_weight:.4f}",
                "score": str(theme.score),
                "direction_action": theme.action,
                "etf_action": theme.etf_action,
                "price": f"{market.price:.4f}" if market else "",
                "day_change_pct": f"{market.day_change_pct:.4f}" if market else "",
                "return_5d": f"{market.return_5d:.4f}" if market else "",
                "return_20d": f"{market.return_20d:.4f}" if market else "",
                "return_60d": f"{market.return_60d:.4f}" if market else "",
                "volume_ratio": f"{market.volume_ratio:.4f}" if market else "",
                "sma_20_gap_pct": f"{market.sma_20_gap_pct:.4f}" if market else "",
                "drawdown_60d_pct": f"{market.drawdown_60d_pct:.4f}" if market else "",
                "volatility_20d": f"{market.volatility_20d:.4f}" if market else "",
                "market_updated_at": market.updated_at if market else "",
            }
        )

    combined = [
        {field: str(row.get(field, "")) for field in fieldnames}
        for row in existing
        if row.get("pulled_at")
    ] + rows
    batches = sorted({row["pulled_at"] for row in combined}, reverse=True)[:keep_batches]
    kept = [row for row in combined if row["pulled_at"] in set(batches)]

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(kept)


def _buy_sell_pressure(
    quote: Optional[MarketSeries],
    flow: Optional[FundFlowSnapshot],
) -> tuple[str, int]:
    if quote is None:
        return "资金分歧", 50

    amount_heat = _scale_range(quote.volume_ratio, 0.7, 2.0)
    price_strength = _scale_range(quote.day_change_pct, -3.0, 3.0)
    trend_strength = _scale_range(quote.return_20d * 0.7 + quote.return_60d * 0.3, -8.0, 12.0)
    flow_score = 50.0
    main_pct = None
    super_pct = None
    if flow is not None:
        main_pct = flow.main_net_pct
        super_pct = flow.super_net_pct
        flow_score = _scale_range((main_pct or 0.0) * 0.7 + (super_pct or 0.0) * 0.3, -8.0, 8.0)

    score = round(_clip(amount_heat * 0.25 + price_strength * 0.25 + trend_strength * 0.20 + flow_score * 0.30))
    heavy_volume = quote.volume_ratio >= 1.8
    strong_flow = flow is not None and (main_pct or 0.0) >= 5 and (super_pct or 0.0) >= 0
    weak_flow = flow is not None and (main_pct or 0.0) <= -5 and (super_pct or 0.0) <= 0

    if quote.day_change_pct >= 2.0 and (heavy_volume or strong_flow) and score >= 68:
        return "大量买入", score
    if quote.day_change_pct >= 0.5 and score >= 58:
        return "温和买入", score
    if quote.day_change_pct <= -2.0 and (heavy_volume or weak_flow) and score <= 36:
        return "大量卖出", score
    if quote.day_change_pct <= -0.5 and score <= 44:
        return "温和卖出", score
    return "资金分歧", score


def _early_right_score(row: Dict[str, str], quote: Optional[MarketSeries], pressure_score: int) -> float:
    if quote is None:
        return 45.0
    trend_repair = _scale_range(quote.return_20d, -2.0, 10.0) * 0.55 + _scale_range(quote.return_60d, -12.0, 15.0) * 0.45
    ma_confirmation = 100.0 if 0 <= quote.sma_20_gap_pct <= 8 else _scale_range(quote.sma_20_gap_pct, -5.0, 12.0)
    volume_confirmation = 100.0 - abs(quote.volume_ratio - 1.45) / 1.45 * 100.0
    volume_confirmation = _clip(volume_confirmation)
    low_crowding = _clip(100.0 - max(0.0, quote.sma_20_gap_pct - 8.0) * 8.0 - max(0.0, quote.return_20d - 18.0) * 3.0)
    source_bonus = {"右侧初期": 8.0, "扩散补涨": 5.0, "主线龙头": -4.0}.get(_text(row, "selection_bucket"), 0.0)
    return _clip(
        trend_repair * 0.30
        + ma_confirmation * 0.20
        + volume_confirmation * 0.20
        + pressure_score * 0.20
        + low_crowding * 0.10
        + source_bonus
    )


def _crowding_penalty(row: Dict[str, str], quote: Optional[MarketSeries]) -> float:
    if quote is None:
        return 0.0
    penalty = 0.0
    holding_pct = _float(row, "holding_pct", 0.0)
    if holding_pct > 10:
        penalty += min(8.0, (holding_pct - 10.0) * 0.8)
    if quote.return_20d > 25:
        penalty += min(12.0, (quote.return_20d - 25.0) * 0.8)
    if quote.sma_20_gap_pct > 12:
        penalty += min(14.0, (quote.sma_20_gap_pct - 12.0) * 1.4)
    if quote.return_5d > 12:
        penalty += min(8.0, (quote.return_5d - 12.0) * 1.0)
    return penalty


def _score(
    row: Dict[str, str],
    quote: Optional[MarketSeries],
    theme: Optional[ThemeCandidate],
    fundamentals: FundamentalSnapshot,
    pressure_score: int,
) -> int:
    roe = fundamentals.roe
    profit_growth = fundamentals.profit_growth
    revenue_growth = fundamentals.revenue_growth
    dividend_yield = _float(row, "dividend_yield")
    pe_percentile = fundamentals.pe_percentile
    catalyst_score = _float(row, "catalyst_score", 50.0)
    risk_score = _float(row, "risk_score", 50.0)
    governance_score = _float(row, "governance_score", 70.0)
    theme_score = theme.score if theme else 50
    early_right = _early_right_score(row, quote, pressure_score)

    if _is_etf_row(row):
        momentum = 50.0
        if quote:
            momentum = _clip(50 + quote.return_20d * 2.0 + quote.return_60d * 0.8 + quote.volume_ratio * 5.0)
        risk_control = _clip((100.0 - risk_score) * 0.55 + governance_score * 0.45)
        total = (
            theme_score * 0.30
            + momentum * 0.25
            + pressure_score * 0.15
            + early_right * 0.10
            + catalyst_score * 0.10
            + risk_control * 0.10
        )
        total -= _crowding_penalty(row, quote) * 0.60
        return round(_clip(total))

    quality = (
        _scale_positive(roe, 5, 25) * 0.40
        + _scale_positive(profit_growth, -10, 40) * 0.25
        + _scale_positive(revenue_growth, -5, 35) * 0.20
        + _scale_positive(fundamentals.cashflow_profit_ratio, 50, 150) * 0.10
        + _scale_positive(dividend_yield, 0, 6) * 0.05
    )
    valuation = _scale_inverse_percentile(pe_percentile)
    momentum = 50.0
    if quote:
        momentum = _clip(50 + quote.return_20d * 2.0 + quote.sma_20_gap_pct * 1.2)
    leverage_control = _scale_range(80.0 - fundamentals.asset_liability_ratio, 0.0, 60.0)
    risk_control = _clip((100.0 - risk_score) * 0.45 + governance_score * 0.30 + leverage_control * 0.25)
    analyst_expectation = _clip(fundamentals.analyst_buy_ratio * 0.65 + _scale_positive(fundamentals.eps_growth, -5, 15) * 0.35)
    catalyst_blend = catalyst_score * 0.45 + risk_control * 0.25 + analyst_expectation * 0.30
    total = (
        theme_score * 0.20
        + quality * 0.22
        + valuation * 0.13
        + momentum * 0.12
        + early_right * 0.13
        + pressure_score * 0.10
        + catalyst_blend * 0.10
    )
    total -= _crowding_penalty(row, quote)
    return round(_clip(total))


def _rating(score: int, row: Dict[str, str], theme: Optional[ThemeCandidate], pressure_label: str) -> tuple[str, str]:
    if _is_st_stock(_text(row, "ticker")):
        return "X", "回避"
    risk_score = _float(row, "risk_score", 50.0)
    governance_score = _float(row, "governance_score", 70.0)
    if risk_score >= 80 or governance_score < 45:
        return "X", "回避"
    if pressure_label == "大量卖出" and score < 68:
        return "D", "减仓/回避"
    if theme and theme.action == "暂缓":
        if score >= 68:
            return "B", "不追高"
        return "C", "持有观察"
    if pressure_label == "大量买入" and score >= 70:
        return "A", "可建仓"
    if theme and theme.action == "观察" and score >= 68:
        return "B", "强关注"
    if theme and theme.action == "轻跟踪" and score >= 68:
        return "B", "轻仓试探"
    if score >= 82:
        return "S", "核心候选"
    if score >= 72:
        return "A", "可建仓"
    if score >= 62:
        return "B", "强关注"
    if score >= 52:
        return "C", "持有观察"
    return "D", "减仓/回避"


def _suggested_weight(config: AShareConfig, rating: str, current_weight: float) -> float:
    cap = config.max_single_position
    if rating == "S":
        return min(cap, max(current_weight, cap * 0.80))
    if rating == "A":
        return min(cap, max(current_weight, cap * 0.60))
    if rating in {"B"}:
        return min(cap * 0.50, current_weight)
    if rating == "C":
        return current_weight
    return 0.0


def _build_ideas(
    rows: Iterable[Dict[str, str]],
    config: AShareConfig,
    market_days_ago: int,
    selected_themes: Dict[str, ThemeCandidate],
    list_type: str = "今日推荐",
) -> List[AShareIdea]:
    ideas: List[AShareIdea] = []
    for row in rows:
        if _is_st_stock(_text(row, "ticker")):
            continue
        theme_name = _text(row, "theme") or _text(row, "industry")
        theme = selected_themes.get(theme_name)
        if theme_name not in selected_themes:
            continue
        quote = _fetch_best_market_series(_text(row, "ticker"), market_days_ago=market_days_ago)
        fundamentals = _fetch_fundamentals(row)
        flow = _fetch_fund_flow(_text(row, "ticker"))
        pressure_label, pressure_score = _buy_sell_pressure(quote, flow)
        score = _score(row, quote, theme, fundamentals, pressure_score)
        rating, action = _rating(score, row, theme, pressure_label)
        current_weight = _float(row, "current_weight")
        data_note = (
            f"资金:{pressure_label}({pressure_score})；"
            f"财务:{fundamentals.report_date}；"
            f"来源:{','.join(fundamentals.sources[-3:])}"
        )
        if _text(row, "auto_source"):
            data_note = f"{data_note}；层级:{_text(row, 'selection_bucket') or '自动候选'}；召回:{_text(row, 'auto_source')}"
        ideas.append(
            AShareIdea(
                list_type=list_type,
                theme=theme_name,
                ticker=_text(row, "ticker"),
                name=_text(row, "name"),
                industry=_text(row, "industry"),
                style=_text(row, "style"),
                rating=rating,
                action=action,
                score=score,
                suggested_weight=_suggested_weight(config, rating, current_weight),
                current_weight=current_weight,
                price=quote.price if quote else None,
                day_change_pct=quote.day_change_pct if quote else None,
                return_20d=quote.return_20d if quote else None,
                thesis=_text(row, "thesis"),
                catalysts=_text(row, "catalysts"),
                risks=_text(row, "risks"),
                invalidation=_text(row, "invalidation"),
                pressure_label=pressure_label,
                pressure_score=pressure_score,
                data_note=data_note,
            )
        )
    return sorted(ideas, key=lambda item: (item.score, item.suggested_weight), reverse=True)


def _select_today_ideas(ideas: List[AShareIdea], top_n: int) -> List[AShareIdea]:
    if top_n <= 0:
        return []
    eligible = [idea for idea in ideas if not _is_avoid_signal(idea)]
    selected: List[AShareIdea] = []
    selected_tickers: set[str] = set()

    def take(predicate, count: int) -> None:
        for idea in eligible:
            if len(selected) >= top_n or count <= 0:
                return
            if idea.ticker in selected_tickers or not predicate(idea):
                continue
            selected.append(idea)
            selected_tickers.add(idea.ticker)
            count -= 1

    take(lambda item: item.style == "主线龙头", 1)
    take(lambda item: item.style == "右侧初期", 2)
    take(lambda item: item.style == "扩散补涨", 1)
    take(lambda item: True, top_n - len(selected))
    return selected[:top_n]


def _load_observation_state(path: Path, rows: List[Dict[str, str]]) -> Dict[str, object]:
    if path.exists():
        with path.open("r", encoding="utf-8") as handle:
            state = json.load(handle)
    else:
        state = {}

    long_term = [
        _text(row, "ticker")
        for row in rows
        if _text(row, "initial_list") == "long_term"
    ]
    state.setdefault("long_term_tracking", long_term)
    state["long_term_tracking"] = long_term
    state.setdefault("today_recommendations", [])
    state.setdefault("retained_observations", [])
    state.setdefault("tickers", {})
    return state


def _is_avoid_signal(idea: AShareIdea) -> bool:
    return idea.rating in {"D", "X"} or idea.action in {"回避", "减仓/回避"}


def _update_observation_state(
    state: Dict[str, object],
    all_ideas: List[AShareIdea],
    today_ideas: List[AShareIdea],
    evaluation_date: str,
) -> Dict[str, object]:
    tickers = state.setdefault("tickers", {})
    assert isinstance(tickers, dict)
    today_tickers = {idea.ticker for idea in today_ideas}
    avoid_tickers = {idea.ticker for idea in all_ideas if _is_avoid_signal(idea)}
    all_tickers = {idea.ticker for idea in all_ideas} | today_tickers | avoid_tickers

    for ticker in sorted(all_tickers):
        item = tickers.setdefault(ticker, {})
        if not isinstance(item, dict):
            item = {}
            tickers[ticker] = item
        if item.get("last_evaluation_date") == evaluation_date:
            continue

        if ticker in today_tickers:
            item["recommend_streak"] = int(item.get("recommend_streak", 0)) + 1
            item["last_recommended_date"] = evaluation_date
        else:
            item["recommend_streak"] = 0

        if ticker in avoid_tickers:
            item["avoid_streak"] = int(item.get("avoid_streak", 0)) + 1
            item["last_avoid_date"] = evaluation_date
        else:
            item["avoid_streak"] = 0

        item["last_evaluation_date"] = evaluation_date

    retained = set(str(item) for item in state.get("retained_observations", []))
    for ticker, item in tickers.items():
        if not isinstance(item, dict):
            continue
        if int(item.get("recommend_streak", 0)) >= 3:
            retained.add(str(ticker))
        if int(item.get("avoid_streak", 0)) >= 3:
            retained.discard(str(ticker))

    state["today_recommendations"] = [idea.ticker for idea in today_ideas]
    state["retained_observations"] = sorted(retained)
    state["last_run_date"] = evaluation_date
    return state


def _save_observation_state(path: Path, state: Dict[str, object]) -> None:
    path.write_text(
        json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def _ideas_by_ticker(ideas: List[AShareIdea]) -> Dict[str, AShareIdea]:
    return {idea.ticker: idea for idea in ideas}


def _copy_with_list_type(idea: AShareIdea, list_type: str) -> AShareIdea:
    return AShareIdea(
        list_type=list_type,
        theme=idea.theme,
        ticker=idea.ticker,
        name=idea.name,
        industry=idea.industry,
        style=idea.style,
        rating=idea.rating,
        action=idea.action,
        score=idea.score,
        suggested_weight=idea.suggested_weight,
        current_weight=idea.current_weight,
        price=idea.price,
        day_change_pct=idea.day_change_pct,
        return_20d=idea.return_20d,
        thesis=idea.thesis,
        catalysts=idea.catalysts,
        risks=idea.risks,
        invalidation=idea.invalidation,
        pressure_label=idea.pressure_label,
        pressure_score=idea.pressure_score,
        data_note=idea.data_note,
    )


def _market_note(
    directions: List[ThemeCandidate],
    rows: List[Dict[str, str]],
    ideas: List[AShareIdea],
    config: AShareConfig,
) -> str:
    if not directions:
        return "A股方向池暂未产生有效评分。"

    direction_names = [item.theme for item in directions]
    hot_names = direction_names[:2]
    paused_names = [item.theme for item in directions if item.action == "暂缓"]

    tech_terms = {"人工智能", "通信", "机器人", "半导体", "芯片", "计算机", "国产替代"}
    resource_terms = {"有色金属", "黄金", "煤炭", "石油", "稀土"}
    defensive_terms = {"红利低波", "银行", "电力", "公用事业"}
    consumer_terms = {"消费", "白酒", "医药", "医疗"}
    new_energy_terms = {"新能源", "新能源车", "光伏", "储能", "电池"}

    name_set = set(direction_names)
    if name_set & tech_terms:
        mainline = f"AI算力链占优，{'、'.join(hot_names)}共振"
    elif name_set & resource_terms:
        mainline = f"资源周期修复，{'、'.join(hot_names)}领涨"
    elif name_set & defensive_terms:
        mainline = f"红利防守占优，{'、'.join(hot_names)}保持强势"
    elif name_set & consumer_terms:
        mainline = f"消费修复占优，{'、'.join(hot_names)}改善"
    elif name_set & new_energy_terms:
        mainline = f"新能源反弹占优，{'、'.join(hot_names)}修复"
    else:
        mainline = f"{'、'.join(hot_names)}占优"

    if paused_names:
        mainline = f"{mainline}；{paused_names[0]}短线暂缓"

    theme_weights: Dict[str, float] = {}
    for item in ideas:
        theme_weights[item.theme] = theme_weights.get(item.theme, 0.0) + item.suggested_weight
    crowded = [
        theme
        for theme, weight in theme_weights.items()
        if weight > config.max_industry_allocation
    ]

    hot_markets = [item.market for item in directions if item.market]
    overheated = any(
        market.return_5d > 8 or market.sma_20_gap_pct > 10
        for market in hot_markets
    )
    weak_turnover = any(
        market.volume_ratio < 1.05
        for market in hot_markets
    )

    if crowded:
        risk = f"{crowded[0]}建议仓位接近主题上限，新增要分批。"
    elif overheated:
        risk = "强方向短线涨幅偏热，若龙头冲高回落，今日推荐降为观察。"
    elif weak_turnover:
        risk = "强方向成交放大不足，确认信号偏弱，今日推荐不追高。"
    else:
        risk = "若强方向成交不能继续放大或龙头冲高回落，今日推荐降为观察。"
    if paused_names:
        risk = f"{risk.rstrip('。')}，不追暂缓方向。"

    return f"主线判断：{mainline}。风险提醒：{risk}"


def _build_directions(
    themes: List[ThemeCandidate],
    rows: List[Dict[str, str]],
    limit: int,
) -> List[AShareDirection]:
    stock_names_by_theme: Dict[str, List[str]] = {}
    for row in rows:
        stock_names_by_theme.setdefault(_text(row, "theme"), []).append(_text(row, "name"))

    directions: List[AShareDirection] = []
    for theme in themes[:limit]:
        market = theme.market
        names = stock_names_by_theme.get(theme.theme, [])
        rationale = theme.rationale
        if not names:
            rationale = f"{theme.theme} 方向 ETF 信号靠前，但自动候选池暂无覆盖个股。"
        directions.append(
            AShareDirection(
                name=theme.theme,
                proxy_ticker=theme.proxy_ticker,
                proxy_name=theme.proxy_name,
                style=theme.style,
                score=theme.score,
                action=theme.action,
                etf_action=theme.etf_action,
                member_count=len(names),
                top_stock=names[0] if names else "本地暂无",
                rationale=rationale,
                price=market.price if market else None,
                day_change_pct=market.day_change_pct if market else None,
                return_5d=market.return_5d if market else None,
                return_20d=market.return_20d if market else None,
                return_60d=market.return_60d if market else None,
                volume_ratio=market.volume_ratio if market else None,
                sma_20_gap_pct=market.sma_20_gap_pct if market else None,
                drawdown_60d_pct=market.drawdown_60d_pct if market else None,
                volatility_20d=market.volatility_20d if market else None,
                updated_at=market.updated_at if market else None,
            )
        )
    return directions


def build_ashare_snapshot(
    config: AShareConfig,
    market_days_ago: int = 0,
    write_heat_history: bool = True,
) -> AShareSnapshot:
    if not config.enabled:
        return AShareSnapshot(
            enabled=False,
            allocation_target=config.target_allocation,
            market_note="A股主动推荐模块未启用。",
            directions=[],
            top_ideas=[],
            long_term_ideas=[],
            retained_ideas=[],
            watchlist_count=0,
            data_source=str(config.watchlist_path),
            observation_state_path=str(config.observation_state_path),
        )

    path = config.watchlist_path
    if not path.is_absolute():
        path = Path.cwd() / path
    theme_path = config.theme_etf_path
    if not theme_path.is_absolute():
        theme_path = Path.cwd() / theme_path
    state_path = config.observation_state_path
    if not state_path.is_absolute():
        state_path = Path.cwd() / state_path
    theme_heat_history_path = config.theme_heat_history_path
    if not theme_heat_history_path.is_absolute():
        theme_heat_history_path = Path.cwd() / theme_heat_history_path
    cache_path = state_path.with_name("ashare_lowfreq_cache.json")
    _init_lowfreq_cache(cache_path)

    with path.open("r", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))

    themes = _load_theme_candidates(theme_path, market_days_ago)
    if write_heat_history:
        _save_theme_heat_history(theme_heat_history_path, themes)
    all_theme_map = {item.theme: item for item in themes}
    selected = themes[: config.direction_top_n]
    selected_map = {item.theme: item for item in selected}

    auto_rows = _build_auto_candidate_rows(
        selected,
        rows,
        needed=max(config.top_n * 3, config.top_n),
    )
    ideas = _build_ideas(auto_rows, config, market_days_ago, selected_map)
    if len(ideas) < config.top_n:
        auto_rows = _build_auto_candidate_rows(
            themes,
            rows,
            needed=max(config.top_n * 5, config.top_n),
        )
        ideas = _build_ideas(auto_rows, config, market_days_ago, all_theme_map)
    today_ideas = _select_today_ideas(ideas, config.top_n)

    auto_all_ideas = _build_ideas(auto_rows, config, market_days_ago, all_theme_map, list_type="自动候选池")
    local_all_ideas = _build_ideas(rows, config, market_days_ago, all_theme_map, list_type="核心观察池")
    all_ideas = auto_all_ideas + local_all_ideas
    all_by_ticker = _ideas_by_ticker(all_ideas)
    state = _load_observation_state(state_path, rows)
    state = _update_observation_state(
        state,
        all_ideas=all_ideas,
        today_ideas=today_ideas,
        evaluation_date=date.today().isoformat(),
    )
    _save_observation_state(state_path, state)

    long_term_tickers = [str(item) for item in state.get("long_term_tracking", [])]
    retained_tickers = [str(item) for item in state.get("retained_observations", [])]
    long_term_ideas = [
        _copy_with_list_type(all_by_ticker[ticker], "长期追踪")
        for ticker in long_term_tickers
        if ticker in all_by_ticker
    ]
    retained_ideas = [
        _copy_with_list_type(all_by_ticker[ticker], "保留观察")
        for ticker in retained_tickers
        if ticker in all_by_ticker and ticker not in set(long_term_tickers)
    ]
    today_ideas = [_copy_with_list_type(idea, "今日推荐") for idea in today_ideas]

    return AShareSnapshot(
        enabled=True,
        allocation_target=config.target_allocation,
        market_note=_market_note(selected, auto_rows, today_ideas, config),
        directions=_build_directions(selected, auto_rows, limit=config.direction_top_n),
        top_ideas=today_ideas,
        long_term_ideas=long_term_ideas,
        retained_ideas=retained_ideas,
        watchlist_count=len(auto_rows),
        data_source=f"{path} + {theme_path} + {cache_path}",
        observation_state_path=str(state_path),
    )
