from __future__ import annotations

from dataclasses import dataclass
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
    hot_themes: List[ThemeHeat]
    cooling_themes: List[ThemeHeat]
    advice: AdviceSnapshot
