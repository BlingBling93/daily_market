from __future__ import annotations

from typing import Any, Dict, List, Optional

from .config import SignalConfig, ValuationConfig
from .models import QuoteSnapshot, TemperatureSnapshot, ThemeHeat, ValuationSnapshot


def _bucket(value: Optional[float], low: float, high: float) -> str:
    if value is None:
        return "未知"
    if value <= low:
        return "偏低"
    if value >= high:
        return "偏贵"
    midpoint = (low + high) / 2
    if value < midpoint:
        return "中性"
    return "偏高"


def summarize_valuation(config: ValuationConfig, auto_valuation: Optional[Dict[str, Any]] = None) -> ValuationSnapshot:
    auto_valuation = auto_valuation or {}
    trailing_pe = auto_valuation.get("trailing_pe")
    forward_pe = auto_valuation.get("forward_pe")
    if trailing_pe is None:
        trailing_pe = config.trailing_pe
    if forward_pe is None:
        forward_pe = config.forward_pe

    trailing_bucket = _bucket(
        trailing_pe,
        config.trailing_pe_band.low,
        config.trailing_pe_band.high,
    )
    forward_bucket = _bucket(
        forward_pe,
        config.forward_pe_band.low,
        config.forward_pe_band.high,
    )
    return ValuationSnapshot(
        trailing_pe=trailing_pe,
        forward_pe=forward_pe,
        trailing_bucket=trailing_bucket,
        forward_bucket=forward_bucket,
        source=str(auto_valuation.get("source") or "手动配置"),
        as_of=str(auto_valuation.get("as_of") or "") or None,
    )


def compute_temperature(
    qqq: QuoteSnapshot,
    vxn: QuoteSnapshot,
    vix: QuoteSnapshot,
    valuation: ValuationSnapshot,
    signals: SignalConfig,
) -> TemperatureSnapshot:
    score = 50
    rationale: List[str] = []

    if qqq.return_5d > 3:
        score += 8
        rationale.append("QQQ 近 5 个交易日动能偏强。")
    elif qqq.return_5d < -3:
        score -= 8
        rationale.append("QQQ 近一周回撤明显。")

    if qqq.sma_20_gap_pct > 4:
        score += 10
        rationale.append("QQQ 明显运行在 20 日线之上，短线有些偏热。")
    elif qqq.sma_20_gap_pct < -4:
        score -= 10
        rationale.append("QQQ 已明显跌到 20 日线下方。")

    if vxn.price >= signals.high_vxn:
        score -= 12
        rationale.append("VXN 处于高位，科技股波动压力偏大。")
    elif vxn.price <= signals.low_vxn:
        score += 6
        rationale.append("VXN 处于低位，风险偏好暂时稳定。")

    if vix.day_change_pct > 8:
        score -= 8
        rationale.append("VIX 单日明显拉升，市场避险情绪在升温。")

    if valuation.forward_bucket == "偏贵":
        score += 7
        rationale.append("远期估值已经来到偏贵区间。")
    elif valuation.forward_bucket == "偏低":
        score -= 5
        rationale.append("远期估值处于相对偏低区间。")

    score = max(0, min(100, score))
    if score >= signals.hot_temperature:
        label = "偏热"
    elif score <= signals.cold_temperature:
        label = "偏冷"
    else:
        label = "中性"

    return TemperatureSnapshot(score=score, label=label, rationale=rationale)


def build_cross_asset_notes(gold: QuoteSnapshot, us10y: QuoteSnapshot, oil: QuoteSnapshot) -> List[str]:
    notes: List[str] = []

    if gold.day_change_pct >= 1 and us10y.day_change_pct <= -0.8:
        notes.append("黄金走强、10年美债回落，市场偏向避险定价。")
    elif gold.day_change_pct <= -1 and us10y.day_change_pct >= 0.8:
        notes.append("黄金走弱、10年美债上行，风险资产面临估值压缩压力。")
    elif gold.day_change_pct >= 0.8:
        notes.append("黄金明显走强，说明资金在增加防守仓位。")
    elif gold.day_change_pct <= -0.8:
        notes.append("黄金回落，避险需求阶段性降温。")

    if us10y.day_change_pct >= 1.0:
        notes.append("10年美债收益率上行，对高估值科技股不太友好。")
    elif us10y.day_change_pct <= -1.0:
        notes.append("10年美债收益率回落，对久期资产和成长风格相对友好。")

    if oil.day_change_pct >= 2.0:
        notes.append("原油快速上行，通胀预期有抬头迹象，需要留意利率压力。")
    elif oil.day_change_pct <= -2.0:
        notes.append("原油明显回落，通胀压力阶段性缓和。")

    if not notes:
        notes.append("黄金、美债和原油都没有出现特别强的跨资产警报。")

    return notes[:4]


def build_observation_points(
    advice_triggers: List[str],
    temperature_rationale: List[str],
    cross_asset_notes: List[str],
) -> List[str]:
    observations: List[str] = []

    for item in advice_triggers[:2]:
        if item not in observations:
            observations.append(item)

    priority_temperature = temperature_rationale[:2]
    priority_cross_asset = cross_asset_notes[:2]

    for item in priority_temperature + priority_cross_asset:
        if item not in observations:
            observations.append(item)

    if len(observations) < 6:
        for item in temperature_rationale[2:] + cross_asset_notes[2:]:
            if item not in observations:
                observations.append(item)
            if len(observations) >= 6:
                break

    return observations[:6]


def rank_theme_heat(theme_heat: List[ThemeHeat]) -> tuple[List[ThemeHeat], List[ThemeHeat]]:
    hottest = sorted(
        theme_heat,
        key=lambda item: (item.avg_return_5d, item.avg_day_change_pct, item.winners_ratio),
        reverse=True,
    )[:3]
    coolest = sorted(
        theme_heat,
        key=lambda item: (item.avg_return_5d, item.avg_day_change_pct, item.winners_ratio),
    )[:3]
    return hottest, coolest
