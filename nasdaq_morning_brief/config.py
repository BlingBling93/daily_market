from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional


@dataclass
class PortfolioConfig:
    instrument: str
    current_allocation: float
    min_allocation: float
    max_allocation: float
    step_allocation: float
    risk_profile: str


@dataclass
class ValuationBand:
    low: float
    high: float


@dataclass
class ValuationConfig:
    trailing_pe: Optional[float]
    forward_pe: Optional[float]
    trailing_pe_band: ValuationBand
    forward_pe_band: ValuationBand


@dataclass
class RenderConfig:
    output_dir: Path
    title: str
    node_bin: Path
    node_modules: Path
    chrome_path: Path
    viewport_width: int
    viewport_height: int


@dataclass
class PushConfig:
    wecom_webhook: Optional[str]
    feishu_webhook: Optional[str]
    feishu_secret: Optional[str]
    feishu_app_id: Optional[str]
    feishu_app_secret: Optional[str]


@dataclass
class SignalConfig:
    hot_temperature: int
    cold_temperature: int
    high_vxn: float
    low_vxn: float


@dataclass
class PolicyConfig:
    events_path: Path
    cache_path: Path
    news_cache_path: Path
    discovered_events_path: Path
    auto_fetch: bool
    lookahead_days: int
    lookback_days: int
    default_impact_days: int
    refresh_hours: int
    recalibrate_within_days: int
    result_retry_hours: int
    earnings_lookahead_days: int
    earnings_symbols: str
    discovery_lookahead_days: int
    discovery_retention_days: int
    discovery_min_importance: int
    discovery_queries: str


@dataclass
class AShareConfig:
    enabled: bool
    watchlist_path: Path
    theme_etf_path: Path
    observation_state_path: Path
    theme_heat_history_path: Path
    top_n: int
    direction_top_n: int
    current_allocation: float
    target_allocation: float
    max_single_position: float
    max_industry_allocation: float
    min_cash: float


@dataclass
class AppConfig:
    portfolio: PortfolioConfig
    valuation: ValuationConfig
    render: RenderConfig
    push: PushConfig
    signals: SignalConfig
    policy: PolicyConfig
    ashare: AShareConfig


def _load_yaml(path: Path) -> Dict[str, Any]:
    try:
        import yaml  # type: ignore
    except ModuleNotFoundError:
        return _load_simple_yaml(path)

    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def _parse_scalar(value: str) -> Any:
    if value == "null":
        return None
    if value in {"true", "false"}:
        return value == "true"
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    try:
        if "." in value:
            return float(value)
        return int(value)
    except ValueError:
        return value


def _load_simple_yaml(path: Path) -> Dict[str, Any]:
    root: Dict[str, Any] = {}
    stack: list[tuple[int, Dict[str, Any]]] = [(-1, root)]

    with path.open("r", encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.rstrip()
            if not line or line.lstrip().startswith("#"):
                continue
            indent = len(line) - len(line.lstrip(" "))
            key, _, raw_value = line.strip().partition(":")
            value = raw_value.strip()

            while stack and indent <= stack[-1][0]:
                stack.pop()
            parent = stack[-1][1]

            if value == "":
                node: Dict[str, Any] = {}
                parent[key] = node
                stack.append((indent, node))
            else:
                parent[key] = _parse_scalar(value)

    return root


def _env(name: str, fallback: Optional[str]) -> Optional[str]:
    value = os.environ.get(name)
    if value is None or value == "":
        return fallback
    return value


def _env_path(name: str, fallback: str) -> Path:
    value = _env(name, fallback) or fallback
    if value == "auto":
        return Path("")
    return Path(value)


def _env_int(name: str, fallback: int) -> int:
    value = os.environ.get(name)
    if value is None or value == "":
        return fallback
    return int(value)


def load_config(path: str) -> AppConfig:
    raw = _load_yaml(Path(path))

    portfolio = raw["portfolio"]
    valuation = raw["valuation"]
    render = raw["render"]
    push = raw.get("push", {})
    signals = raw["signals"]
    policy = raw.get("policy", {})
    ashare = raw.get("ashare", {})

    return AppConfig(
        portfolio=PortfolioConfig(**portfolio),
        valuation=ValuationConfig(
            trailing_pe=valuation.get("trailing_pe"),
            forward_pe=valuation.get("forward_pe"),
            trailing_pe_band=ValuationBand(**valuation["trailing_pe_band"]),
            forward_pe_band=ValuationBand(**valuation["forward_pe_band"]),
        ),
        render=RenderConfig(
            output_dir=Path(render["output_dir"]),
            title=render["title"],
            node_bin=_env_path("NODE_BIN", render["node_bin"]),
            node_modules=_env_path("NODE_MODULES", render["node_modules"]),
            chrome_path=_env_path("CHROME_PATH", render["chrome_path"]),
            viewport_width=_env_int("VIEWPORT_WIDTH", render["viewport_width"]),
            viewport_height=_env_int("VIEWPORT_HEIGHT", render["viewport_height"]),
        ),
        push=PushConfig(
            wecom_webhook=_env("WECOM_WEBHOOK", push.get("wecom_webhook")),
            feishu_webhook=_env("FEISHU_WEBHOOK", push.get("feishu_webhook")),
            feishu_secret=_env("FEISHU_SECRET", push.get("feishu_secret")),
            feishu_app_id=_env("FEISHU_APP_ID", push.get("feishu_app_id")),
            feishu_app_secret=_env("FEISHU_APP_SECRET", push.get("feishu_app_secret")),
        ),
        signals=SignalConfig(**signals),
        policy=PolicyConfig(
            events_path=Path(policy.get("events_path", "policy_events.csv")),
            cache_path=Path(policy.get("cache_path", "policy_calendar_cache.json")),
            news_cache_path=Path(policy.get("news_cache_path", "policy_event_news_cache.json")),
            discovered_events_path=Path(policy.get("discovered_events_path", "policy_discovered_events_cache.json")),
            auto_fetch=bool(policy.get("auto_fetch", True)),
            lookahead_days=int(policy.get("lookahead_days", 7)),
            lookback_days=int(policy.get("lookback_days", 21)),
            default_impact_days=int(policy.get("default_impact_days", 3)),
            refresh_hours=int(policy.get("refresh_hours", 72)),
            recalibrate_within_days=int(policy.get("recalibrate_within_days", 3)),
            result_retry_hours=int(policy.get("result_retry_hours", policy.get("news_refresh_hours", 6))),
            earnings_lookahead_days=int(policy.get("earnings_lookahead_days", 45)),
            earnings_symbols=str(policy.get("earnings_symbols", "AAPL,MSFT,NVDA,AMZN,GOOGL,META,TSLA")),
            discovery_lookahead_days=int(policy.get("discovery_lookahead_days", 120)),
            discovery_retention_days=int(policy.get("discovery_retention_days", 45)),
            discovery_min_importance=int(policy.get("discovery_min_importance", 70)),
            discovery_queries=str(policy.get("discovery_queries", "")),
        ),
        ashare=AShareConfig(
            enabled=bool(ashare.get("enabled", False)),
            watchlist_path=Path(ashare.get("watchlist_path", "ashare_watchlist.csv")),
            theme_etf_path=Path(ashare.get("theme_etf_path", "ashare_theme_etf.csv")),
            observation_state_path=Path(ashare.get("observation_state_path", "ashare_observation_state.json")),
            theme_heat_history_path=Path(ashare.get("theme_heat_history_path", "ashare_theme_heat_history.csv")),
            top_n=int(ashare.get("top_n", 5)),
            direction_top_n=int(ashare.get("direction_top_n", 3)),
            current_allocation=float(ashare.get("current_allocation", 0.0)),
            target_allocation=float(ashare.get("target_allocation", 0.0)),
            max_single_position=float(ashare.get("max_single_position", 0.05)),
            max_industry_allocation=float(ashare.get("max_industry_allocation", 0.25)),
            min_cash=float(ashare.get("min_cash", 0.10)),
        ),
    )
