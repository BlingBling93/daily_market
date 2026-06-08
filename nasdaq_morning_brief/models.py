from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import List, Optional


@dataclass
class QuoteSnapshot:
    symbol: str
    as_of: date
    price: float
    previous_close: float
    day_change_pct: float
    return_5d: float
    return_20d: float
    sma_20_gap_pct: float
    sma_50_gap_pct: float
    sma_200_gap_pct: float


@dataclass
class ValuationSnapshot:
    trailing_pe: Optional[float]
    forward_pe: Optional[float]
    trailing_bucket: str
    forward_bucket: str
    source: str
    as_of: Optional[str]


@dataclass
class ThemeHeat:
    theme: str
    avg_day_change_pct: float
    avg_return_5d: float
    avg_return_20d: float
    winners_ratio: float
    member_count: int


@dataclass
class TemperatureSnapshot:
    score: int
    label: str
    rationale: List[str]


@dataclass
class AdviceSnapshot:
    action: str
    target_allocation: float
    summary: str
    triggers: List[str]
    trend_state: str
    risk_state: str
    valuation_state: str
    allocation_band: str
    base_action: str = ""


@dataclass
class PolicyEvent:
    event_date: date
    category: str
    title: str
    stance: str
    summary: str
    short_term: str
    mid_term: str
    long_term: str
    impact_days: int
    result_summary: str = ""
    result_conclusion: str = ""
    result_sources: List[str] = field(default_factory=list)
    result_source_tier: str = ""


@dataclass
class PolicySnapshot:
    stance: str
    summary: str
    execution_note: str
    short_term: str
    mid_term: str
    long_term: str
    upcoming_events: List[PolicyEvent]
    recent_events: List[PolicyEvent]
    next_event: Optional[PolicyEvent] = None
    execution_upcoming_events: List[PolicyEvent] = field(default_factory=list)
    execution_recent_events: List[PolicyEvent] = field(default_factory=list)


@dataclass
class AShareIdea:
    list_type: str
    theme: str
    ticker: str
    name: str
    industry: str
    style: str
    rating: str
    action: str
    score: int
    suggested_weight: float
    current_weight: float
    price: Optional[float]
    day_change_pct: Optional[float]
    return_20d: Optional[float]
    thesis: str
    catalysts: str
    risks: str
    invalidation: str
    pressure_label: str = "资金分歧"
    pressure_score: int = 50
    data_note: str = ""


@dataclass
class AShareDirection:
    name: str
    proxy_ticker: str
    proxy_name: str
    style: str
    score: int
    action: str
    etf_action: str
    member_count: int
    top_stock: str
    rationale: str
    price: Optional[float]
    day_change_pct: Optional[float]
    return_5d: Optional[float]
    return_20d: Optional[float]
    return_60d: Optional[float]
    volume_ratio: Optional[float]
    sma_20_gap_pct: Optional[float]
    drawdown_60d_pct: Optional[float]
    volatility_20d: Optional[float]
    updated_at: Optional[str]


@dataclass
class AShareSnapshot:
    enabled: bool
    allocation_target: float
    market_note: str
    validation_summary: List[str]
    directions: List[AShareDirection]
    top_ideas: List[AShareIdea]
    long_term_ideas: List[AShareIdea]
    retained_ideas: List[AShareIdea]
    watchlist_count: int
    data_source: str
    observation_state_path: str


@dataclass
class Brief:
    as_of: date
    qqq: QuoteSnapshot
    ndx: QuoteSnapshot
    vix: QuoteSnapshot
    vxn: QuoteSnapshot
    gold: QuoteSnapshot
    us10y: QuoteSnapshot
    oil: QuoteSnapshot
    valuation: ValuationSnapshot
    temperature: TemperatureSnapshot
    cross_asset_notes: List[str]
    observation_points: List[str]
    policy: PolicySnapshot
    advice: AdviceSnapshot
    ashare: Optional[AShareSnapshot]
