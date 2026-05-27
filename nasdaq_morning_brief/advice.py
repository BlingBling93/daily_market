from __future__ import annotations

from dataclasses import replace
from datetime import date

from .config import PortfolioConfig
from .models import AdviceSnapshot, PolicyEvent, PolicySnapshot, QuoteSnapshot, TemperatureSnapshot, ValuationSnapshot


MAJOR_POLICY_CATEGORIES = {"FOMC", "通胀", "就业", "财报", "监管", "地缘"}


def _trend_state(qqq: QuoteSnapshot) -> str:
    if qqq.sma_50_gap_pct > 0 and qqq.sma_200_gap_pct > 0:
        if qqq.sma_20_gap_pct > 5:
            return "健康但偏热"
        return "健康"
    if qqq.sma_50_gap_pct < 0 and qqq.sma_200_gap_pct > 0:
        return "震荡转弱"
    if qqq.sma_200_gap_pct < 0:
        return "大级别转弱"
    return "震荡"


def _risk_state(vxn: QuoteSnapshot, temperature: TemperatureSnapshot) -> str:
    if vxn.price >= 28 or temperature.score <= 32:
        return "恐慌"
    if vxn.price >= 24 or vxn.day_change_pct >= 8:
        return "升温"
    if vxn.price <= 18 and temperature.score < 72:
        return "平稳"
    return "中性"


def _valuation_state(valuation: ValuationSnapshot) -> str:
    if valuation.forward_bucket in {"偏贵", "偏高"}:
        return valuation.forward_bucket
    if valuation.trailing_bucket == "偏贵":
        return "偏高"
    if valuation.forward_bucket in {"中性", "偏低"}:
        return valuation.forward_bucket
    return valuation.trailing_bucket


def _allocation_band(
    current: float,
    target: float,
    step: float,
    min_allocation: float,
    max_allocation: float,
    action: str,
) -> str:
    if action in {"小幅加仓"}:
        lower = current
        upper = min(max_allocation, current + step)
    elif action in {"小幅降仓"}:
        lower = max(min_allocation, current - step)
        upper = current
    elif action == "防守":
        lower = max(min_allocation, target - step)
        upper = target
    elif action == "暂停加仓":
        lower = max(min_allocation, current - step)
        upper = current
    elif action == "持有观察":
        lower = current
        upper = current
    else:
        lower = max(min_allocation, current - step)
        upper = min(max_allocation, current + step)
    return f"{lower:.0%}-{upper:.0%}"


def generate_advice(
    portfolio: PortfolioConfig,
    qqq: QuoteSnapshot,
    vxn: QuoteSnapshot,
    temperature: TemperatureSnapshot,
    valuation: ValuationSnapshot,
) -> AdviceSnapshot:
    current = portfolio.current_allocation
    step = portfolio.step_allocation
    target = current
    action = "持有"
    triggers = []
    trend_state = _trend_state(qqq)
    risk_state = _risk_state(vxn, temperature)
    valuation_state = _valuation_state(valuation)

    if trend_state == "大级别转弱":
        target = max(portfolio.min_allocation, current - step * 2)
        action = "防守"
        triggers.append("QQQ 已跌破 200 日线，长期持有也需要把仓位压到下限附近。")
        triggers.append("等重新站回 200 日线或波动明显回落，再考虑恢复仓位。")
    elif trend_state == "震荡转弱" and risk_state in {"升温", "恐慌"}:
        target = max(portfolio.min_allocation, current - step)
        action = "小幅降仓"
        triggers.append("QQQ 跌破 50 日线，同时 VXN 升温，先降低一档战术仓位。")
        triggers.append("不做清仓，保留长期核心仓位。")
    elif trend_state == "健康但偏热" and valuation_state in {"偏高", "偏贵"}:
        target = current
        action = "暂停加仓"
        triggers.append("趋势仍健康，但价格明显高于 20 日线，估值也不便宜。")
        triggers.append("长期仓位继续拿，新增资金等回踩再动。")
    elif trend_state in {"健康", "健康但偏热"} and risk_state == "恐慌":
        target = min(portfolio.max_allocation, current + step)
        action = "小幅加仓"
        triggers.append("趋势没有破坏，但市场恐慌升温，适合只加一档。")
        triggers.append("如果 VXN 继续上冲，后续加仓要暂停。")
    elif trend_state == "健康" and risk_state in {"平稳", "中性"}:
        action = "持有"
        triggers.append("趋势健康，波动没有明显失控，核心仓位继续持有。")
        if valuation_state in {"偏高", "偏贵"}:
            triggers.append("估值偏高，暂时不追涨加仓。")
        else:
            triggers.append("若回踩 20 日线且 VXN 不上冲，可以考虑加一档。")
    else:
        triggers.append("趋势、风险和估值没有形成一致信号，保持当前仓位。")
        triggers.append("下一步重点看 50 日线、VXN 和 forward P/E 是否共振。")

    allocation_band = _allocation_band(
        current,
        target,
        step,
        portfolio.min_allocation,
        portfolio.max_allocation,
        action,
    )

    summary = (
        f"当前仓位 {current:.0%}；三因子判断为趋势“{trend_state}”、"
        f"风险“{risk_state}”、估值“{valuation_state}”。"
        f"明天基础动作是“{action}”，参考仓位区间 {allocation_band}。"
    )
    return AdviceSnapshot(
        action=action,
        target_allocation=target,
        summary=summary,
        triggers=triggers,
        trend_state=trend_state,
        risk_state=risk_state,
        valuation_state=valuation_state,
        allocation_band=allocation_band,
        base_action=action,
    )


def _has_major_event(events: list[PolicyEvent]) -> bool:
    return any(event.category in MAJOR_POLICY_CATEGORIES for event in events)


def _policy_adjusted_action(base_action: str, policy: PolicySnapshot) -> str:
    if base_action == "防守":
        return base_action

    has_upcoming_major = _has_major_event(policy.upcoming_events)
    has_active_major = _has_major_event(policy.recent_events)

    if has_upcoming_major:
        if base_action in {"小幅加仓", "持有"}:
            return "持有观察"
        return base_action

    if has_active_major:
        if base_action in {"小幅加仓", "持有"}:
            return "持有观察"
        return base_action

    if policy.stance == "偏谨慎":
        if base_action in {"小幅加仓", "持有"}:
            return "持有观察"
        return base_action

    if policy.stance == "偏友好" and base_action == "小幅降仓":
        return "持有观察"

    return base_action


def _adjusted_target(
    portfolio: PortfolioConfig,
    base_target: float,
    final_action: str,
) -> float:
    if final_action == "持有观察":
        return portfolio.current_allocation
    return base_target


def apply_policy_adjustment(
    portfolio: PortfolioConfig,
    advice: AdviceSnapshot,
    policy: PolicySnapshot,
    as_of: date,
) -> AdviceSnapshot:
    base_action = advice.base_action or advice.action
    final_action = _policy_adjusted_action(base_action, policy)
    final_target = _adjusted_target(portfolio, advice.target_allocation, final_action)
    allocation_band = _allocation_band(
        portfolio.current_allocation,
        final_target,
        portfolio.step_allocation,
        portfolio.min_allocation,
        portfolio.max_allocation,
        final_action,
    )

    if final_action == base_action:
        focus = (
            f"三因子基础动作“{base_action}”；基本面“{policy.stance}”，"
            f"{policy.execution_note}"
        )
    else:
        focus = (
            f"三因子基础动作“{base_action}”，基本面修正为“{final_action}”；"
            f"{policy.execution_note}"
        )

    triggers = [focus]
    for item in advice.triggers:
        if item not in triggers:
            triggers.append(item)
    if policy.short_term not in triggers:
        triggers.append(policy.short_term)

    summary = (
        f"当前仓位 {portfolio.current_allocation:.0%}；三因子判断为趋势“{advice.trend_state}”、"
        f"风险“{advice.risk_state}”、估值“{advice.valuation_state}”。"
        f"基础动作“{base_action}”，结合基本面后明日动作是“{final_action}”，"
        f"参考仓位区间 {allocation_band}。"
    )

    return replace(
        advice,
        action=final_action,
        target_allocation=final_target,
        summary=summary,
        triggers=triggers,
        allocation_band=allocation_band,
        base_action=base_action,
    )
