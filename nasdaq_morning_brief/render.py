from __future__ import annotations

import os
import re
import subprocess
from datetime import date, datetime
from html import escape
from pathlib import Path

from .config import AppConfig
from .models import AShareDirection, AShareIdea, AShareSnapshot, Brief, PolicyEvent, QuoteSnapshot


def _fmt_pct(value: float) -> str:
    return f"{value:+.1f}%"


def _fmt_price(value: float) -> str:
    return f"{value:,.2f}"


def _fmt_us10y_yield(value: float) -> str:
    normalized = value / 10.0 if value > 20 else value
    return f"{normalized:.2f}%"


def _move_class(value: float) -> str:
    if value > 0:
        return "up"
    if value < 0:
        return "down"
    return "flat"


def _move_badge(value: float) -> str:
    arrow = "▲" if value > 0 else "▼" if value < 0 else "●"
    return f'<span class="badge {_move_class(value)}">{arrow} {_fmt_pct(value)}</span>'


def _result_source_label(source_tier: str) -> str:
    labels = {
        "official": "官方",
        "official_proxy": "FRED/官方代理",
        "trusted_media_fallback": "可信媒体兜底·待官方校验",
        "media_confirmed": "媒体转述·多源待校验",
        "media_single": "媒体转述·待校验",
        "manual_confirmed": "手工确认·待行情源校验",
        "unverified": "待校验",
    }
    return labels.get(source_tier, "待校验")


def _value_badge(value: float, label: str) -> str:
    arrow = "▲" if value > 0 else "▼" if value < 0 else "●"
    return f'<span class="badge {_move_class(value)}">{arrow} {label}</span>'


def _yield_change_bp(snapshot: QuoteSnapshot) -> float:
    current = snapshot.price / 10.0 if snapshot.price > 20 else snapshot.price
    previous = snapshot.previous_close / 10.0 if snapshot.previous_close > 20 else snapshot.previous_close
    return (current - previous) * 100


def _yield_badge(snapshot: QuoteSnapshot) -> str:
    bp = _yield_change_bp(snapshot)
    return _value_badge(bp, f"{bp:+.1f}bp")


def _vol_label(value: float, thresholds: tuple[float, float, float]) -> str:
    low, mid, high = thresholds
    if value < low:
        return "平稳"
    if value < mid:
        return "中性"
    if value < high:
        return "升温"
    return "恐慌"


def _vol_range(thresholds: tuple[float, float, float]) -> str:
    low, mid, high = thresholds
    return (
        f"<span>&lt;{low:g} 平稳</span>"
        f"<span>{low:g}-{mid:g} 中性</span>"
        f"<span>{mid:g}-{high:g} 升温</span>"
        f"<span>{high:g}+ 恐慌</span>"
    )


def _temperature_class(label: str) -> str:
    if label in {"偏热", "恐慌"}:
        return "down"
    if label == "偏冷":
        return "up"
    return "flat"


def _policy_event_line(event: PolicyEvent) -> str:
    data_html = ""
    if event.result_summary:
        label = _result_source_label(event.result_source_tier)
        data_html = f"<small>数据源：{escape(label)}；{escape(event.result_summary)}</small>"
    result_html = ""
    if event.result_conclusion:
        result_html = f"<small>结论：{escape(event.result_conclusion)}</small>"
    return (
        f"<li><span>{event.event_date.isoformat()} · {escape(event.category)}</span>"
        f"<strong>{escape(event.title)}</strong>"
        f"<small>{escape(event.stance)} · 影响期{event.impact_days}天 · {escape(event.summary)}</small>"
        f"{data_html}{result_html}</li>"
    )


def _next_policy_event_line(event: PolicyEvent) -> str:
    return (
        f"<li><span>下一重要事件 · {event.event_date.isoformat()} · {escape(event.category)}</span>"
        f"<strong>{escape(event.title)}</strong>"
        f"<small>{escape(event.summary)}</small></li>"
    )


def _policy_event_display_key(event: PolicyEvent) -> tuple[str, str]:
    title = re.sub(r"[^a-z0-9]+", " ", event.title.lower()).strip()
    summary = re.sub(r"[^a-z0-9]+", " ", event.summary.lower()).strip()
    combined = f"{title} {summary}"
    if event.category == "IPO" and "spacex" in combined and any(token in combined for token in ("ipo", "listing", "nasdaq")):
        return event.category, "spacex-ipo"
    return event.category, title


def _dedupe_policy_events(events: list[PolicyEvent]) -> list[PolicyEvent]:
    selected: dict[tuple[str, str], PolicyEvent] = {}
    for event in events:
        key = _policy_event_display_key(event)
        current = selected.get(key)
        if current is None:
            selected[key] = event
            continue
        if event.event_date > current.event_date:
            selected[key] = event
        elif event.event_date == current.event_date and len(event.summary) > len(current.summary):
            selected[key] = event
    return list(selected.values())


def _policy_event_list(
    upcoming: list[PolicyEvent],
    recent: list[PolicyEvent],
    next_event: PolicyEvent | None,
) -> str:
    active_events = sorted(
        _dedupe_policy_events(upcoming + recent),
        key=lambda item: (item.event_date, item.category, item.title),
    )
    if not active_events:
        if next_event:
            return _next_policy_event_line(next_event)
        return "<li><span>未来/影响中事件</span><strong>暂无配置</strong><small>可在 policy_events.csv 中维护 FOMC、CPI、PCE、非农和重点财报。</small></li>"
    event_html = "".join(_policy_event_line(item) for item in active_events[:5])
    if next_event and _policy_event_display_key(next_event) not in {_policy_event_display_key(item) for item in active_events}:
        event_html += _next_policy_event_line(next_event)
    return event_html


def _fmt_optional_pct(value: float | None) -> str:
    if value is None:
        return "暂无"
    return _fmt_pct(value)


def _fmt_optional_price(value: float | None) -> str:
    if value is None:
        return "暂无"
    return _fmt_price(value)


def _fmt_optional_ratio(value: float | None) -> str:
    if value is None:
        return "暂无"
    return f"{value:.1f}x"


def _idea_class(rating: str) -> str:
    if rating in {"S", "A"}:
        return "up"
    if rating in {"D", "X"}:
        return "down"
    return "flat"


def _action_class(action: str) -> str:
    if action in {"核心候选", "可建仓"}:
        return "action-buy"
    if action in {"强关注", "轻仓试探"}:
        return "action-watch"
    if action in {"减仓/回避", "回避", "不追高"}:
        return "action-avoid"
    return "action-hold"


def _direction_class(action: str) -> str:
    if action == "重点跟踪":
        return "direction-open"
    if action == "观察":
        return "direction-watch"
    if action == "暂缓":
        return "direction-avoid"
    return "direction-track"


def _etf_action_class(action: str) -> str:
    if action == "可小幅加仓":
        return "etf-buy"
    if action in {"持有观察", "等回调"}:
        return "etf-watch"
    if action in {"减仓提醒", "减仓降温", "暂不配置"}:
        return "etf-avoid"
    return "etf-hold"


def _ashare_direction_line(direction: AShareDirection) -> str:
    return f"""
      <li class="{_direction_class(direction.action)}">
        <div class="direction-head">
          <strong>{escape(direction.name)}</strong>
          <span>{escape(direction.action)}</span>
        </div>
        <div class="direction-score">{direction.score}</div>
        <div class="etf-action {_etf_action_class(direction.etf_action)}">ETF {escape(direction.etf_action)}</div>
        <p>{escape(direction.rationale)}</p>
        <div class="direction-metrics">
          <span>20日 {_fmt_optional_pct(direction.return_20d)}</span>
          <span>成交 {_fmt_optional_ratio(direction.volume_ratio)}</span>
          <span>回撤 {_fmt_optional_pct(direction.drawdown_60d_pct)}</span>
        </div>
        <small>{escape(direction.proxy_name)} {escape(direction.proxy_ticker)} · {direction.member_count} 只 · 代表 {escape(direction.top_stock)}</small>
      </li>
    """


def _ashare_idea_line(idea: AShareIdea) -> str:
    return f"""
      <li>
        <div class="idea-main">
          <span class="rating {_idea_class(idea.rating)}">{escape(idea.rating)}</span>
          <div>
            <strong>{escape(idea.name)} <small>{escape(idea.ticker)}</small></strong>
            <em>{escape(idea.list_type)} · {escape(idea.theme)} · {escape(idea.style)}</em>
          </div>
          <b>{idea.score}</b>
        </div>
        <p>{escape(idea.thesis)}</p>
        <div class="idea-meta">
          <span class="action-pill {_action_class(idea.action)}">动作 {escape(idea.action)}</span>
          <span class="pressure-pill">{escape(idea.pressure_label)}</span>
          <span>现价 {_fmt_optional_price(idea.price)}</span>
          <span>20日 {_fmt_optional_pct(idea.return_20d)}</span>
        </div>
        <small class="idea-risk">催化：{escape(idea.catalysts)}；失效：{escape(idea.invalidation or idea.risks)}；{escape(idea.data_note)}</small>
      </li>
    """


def _ashare_table_row(idea: AShareIdea) -> str:
    return f"""
      <tr>
        <td>
          <div class="stock-name">{escape(idea.name)} <small>{escape(idea.ticker)}</small></div>
          <div class="stock-meta">{escape(idea.list_type)} · {escape(idea.theme)} · {escape(idea.style)}</div>
        </td>
        <td><span class="rating {_idea_class(idea.rating)}">{escape(idea.rating)}</span></td>
        <td class="score">{idea.score}</td>
        <td>{_fmt_optional_price(idea.price)}</td>
        <td><span class="table-action {_action_class(idea.action)}">{escape(idea.action)}</span></td>
        <td>{idea.current_weight:.1%}</td>
        <td>{idea.suggested_weight:.1%}</td>
        <td>{_fmt_optional_pct(idea.return_20d)}</td>
      </tr>
    """


def _tracking_row(idea: AShareIdea) -> str:
    return f"""
      <tr>
        <td>
          <div class="stock-name">{escape(idea.name)} <small>{escape(idea.ticker)}</small></div>
          <div class="stock-meta">{escape(idea.theme)} · {escape(idea.style)}</div>
        </td>
        <td><span class="rating {_idea_class(idea.rating)}">{escape(idea.rating)}</span></td>
        <td class="score">{idea.score}</td>
        <td>{_fmt_optional_price(idea.price)}</td>
        <td><span class="table-action {_action_class(idea.action)}">{escape(idea.action)}</span></td>
        <td>{escape(idea.pressure_label)}</td>
        <td>{_fmt_optional_pct(idea.return_20d)}</td>
      </tr>
    """


def _momentum_width(value: float) -> float:
    return min(96, max(12, 48 + value * 2))


def _index_card(title: str, subtitle: str, snapshot: QuoteSnapshot, accent: str, extra: str = "") -> str:
    return f"""
      <section class="tile index {accent}">
        <div class="tile-head">
          <div>
            <h2>{title}</h2>
            <p>{subtitle}</p>
          </div>
          {_move_badge(snapshot.day_change_pct)}
        </div>
        <div class="big-number">{_fmt_price(snapshot.price)}</div>
        <div class="minor { _move_class(snapshot.day_change_pct) }">{_fmt_pct(snapshot.day_change_pct)} · 当日涨跌</div>
        {extra}
        <div class="momentum-label"><span>20日动量</span><strong>{_fmt_pct(snapshot.return_20d)}</strong></div>
        <div class="momentum-bar"><span style="width: {_momentum_width(snapshot.return_20d)}%"></span></div>
      </section>
    """


def _vol_card(
    title: str,
    subtitle: str,
    snapshot: QuoteSnapshot,
    accent: str,
    thresholds: tuple[float, float, float],
) -> str:
    label = _vol_label(snapshot.price, thresholds)
    return f"""
      <section class="tile vol {accent}">
        <div class="tile-head">
          <div>
            <h2>{title}</h2>
            <p>{subtitle}</p>
          </div>
          {_move_badge(snapshot.day_change_pct)}
        </div>
        <div class="big-number center">{snapshot.price:.2f}</div>
        <div class="pill">{label}</div>
        <div class="range">
          {_vol_range(thresholds)}
        </div>
      </section>
    """


def _asset_row(name: str, value: str, change: float) -> str:
    return f"""
      <div class="asset-row">
        <span>{name}</span>
        <strong>{value}</strong>
        {_move_badge(change)}
      </div>
    """


def _yield_asset_row(name: str, snapshot: QuoteSnapshot) -> str:
    if snapshot.source.startswith("Unavailable"):
        return f"""
          <div class="asset-row">
            <span>{name}</span>
            <strong>暂无</strong>
            <span class="badge flat">● 暂无</span>
          </div>
        """
    return f"""
      <div class="asset-row">
        <span>{name}</span>
        <strong>{_fmt_us10y_yield(snapshot.price)}</strong>
        {_yield_badge(snapshot)}
      </div>
    """


def _data_warnings_html(warnings: list[str]) -> str:
    if not warnings:
        return ""
    items = "".join(f"<li>{escape(warning)}</li>" for warning in warnings)
    return f"""
    <section class="data-alert">
      <strong>数据提示</strong>
      <ul>{items}</ul>
    </section>
    """


def build_html(brief: Brief, config: AppConfig) -> str:
    valuation = brief.valuation
    trailing_text = f"{valuation.trailing_pe:.1f}" if valuation.trailing_pe is not None else "暂无"
    forward_text = f"{valuation.forward_pe:.1f}" if valuation.forward_pe is not None else "暂无"
    valuation_source = valuation.source
    if valuation.as_of:
        valuation_source = f"{valuation_source} · {valuation.as_of}"

    policy_events_html = _policy_event_list(
        brief.policy.upcoming_events,
        brief.policy.recent_events,
        brief.policy.next_event,
    )
    observations_html = "".join(f"<li>{item}</li>" for item in brief.observation_points[1:])
    data_warnings_html = _data_warnings_html(brief.data_warnings)
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M")

    qqq_extra = f"""
      <div class="kv">
        <span>5日 {_fmt_pct(brief.qqq.return_5d)}</span>
        <span>20日 {_fmt_pct(brief.qqq.return_20d)}</span>
        <span>20日线 {_fmt_pct(brief.qqq.sma_20_gap_pct)}</span>
      </div>
      <div class="pe-line">
        <span>纳指P/E <strong>{trailing_text}</strong></span>
        <span>Fwd <strong>{forward_text}</strong></span>
        <em>{brief.valuation.forward_bucket}</em>
      </div>
    """

    ndx_extra = f"""
      <div class="kv">
        <span>5日 {_fmt_pct(brief.ndx.return_5d)}</span>
        <span>20日 {_fmt_pct(brief.ndx.return_20d)}</span>
        <span>50日线 {_fmt_pct(brief.ndx.sma_50_gap_pct)}</span>
      </div>
      <div class="pe-line muted">
        <span>估值来源</span><strong>{valuation_source}</strong>
      </div>
    """

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <title>{config.render.title}</title>
  <style>
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: #f2f3f0;
      color: #11151c;
      font-family: "Avenir Next", "PingFang SC", "Hiragino Sans GB", sans-serif;
    }}
    .card {{
      width: 960px;
      margin: 0 auto;
      padding: 34px 38px 30px;
      background:
        linear-gradient(135deg, rgba(31, 138, 112, 0.08), transparent 30%),
        linear-gradient(180deg, #fbfbf7 0%, #eff2eb 100%);
    }}
    .topbar {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 18px;
    }}
    .label {{
      display: inline-flex;
      align-items: center;
      gap: 8px;
      padding: 9px 20px;
      border-radius: 999px;
      background: #11151c;
      color: #fff;
      font-size: 16px;
      font-weight: 800;
      letter-spacing: 0.02em;
    }}
    .date-pill {{
      padding: 9px 22px;
      border: 1px solid #c9cec8;
      border-radius: 999px;
      background: rgba(255, 255, 255, 0.72);
      color: #49505a;
      font-size: 16px;
    }}
    h1 {{
      margin: 8px 0 22px;
      font-size: 48px;
      line-height: 1.08;
      letter-spacing: 0;
    }}
    .grid {{
      display: grid;
      grid-template-columns: repeat(2, 1fr);
      gap: 18px;
    }}
    .tile {{
      min-height: 182px;
      padding: 18px 20px;
      border: 1px solid #d6d9d2;
      border-radius: 8px;
      background: rgba(255, 255, 255, 0.78);
      box-shadow: 0 5px 20px rgba(18, 24, 31, 0.06);
      overflow: hidden;
    }}
    .tile-head {{
      display: flex;
      justify-content: space-between;
      gap: 14px;
      align-items: flex-start;
    }}
    h2 {{
      margin: 0;
      font-size: 18px;
      line-height: 1.2;
      letter-spacing: 0;
    }}
    p {{
      margin: 4px 0 0;
      color: #68707a;
      font-size: 13px;
      line-height: 1.35;
    }}
    .tile h2::before {{
      content: "";
      display: block;
      width: 38px;
      height: 6px;
      border-radius: 999px;
      margin-bottom: 7px;
      background: var(--accent);
    }}
    .green {{ --accent: #159a73; }}
    .violet {{ --accent: #6843c4; }}
    .blue {{ --accent: #327bb4; }}
    .gold {{ --accent: #d6aa2a; }}
    .big-number {{
      margin-top: 14px;
      font-size: 42px;
      font-weight: 900;
      letter-spacing: 0;
      line-height: 1;
    }}
    .big-number.center {{
      text-align: center;
      font-size: 58px;
    }}
    .minor {{
      margin-top: 8px;
      font-size: 14px;
      font-weight: 800;
    }}
    .up {{ color: #16835f; }}
    .down {{ color: #b63d5b; }}
    .flat {{ color: #6d7480; }}
    .badge {{
      display: inline-flex;
      align-items: center;
      min-width: 112px;
      justify-content: center;
      padding: 7px 12px;
      border-radius: 999px;
      font-size: 14px;
      font-weight: 900;
      white-space: nowrap;
      border: 2px solid currentColor;
      background: rgba(255, 255, 255, 0.8);
    }}
    .kv {{
      display: grid;
      grid-template-columns: repeat(3, 1fr);
      gap: 8px;
      margin-top: 14px;
      color: #4c5560;
      font-size: 13px;
      font-weight: 700;
    }}
    .kv span {{
      padding: 7px 8px;
      border-radius: 7px;
      background: #f1f3ef;
      text-align: center;
    }}
    .pe-line {{
      display: flex;
      gap: 12px;
      align-items: center;
      margin-top: 12px;
      color: #29313b;
      font-size: 15px;
      font-weight: 800;
    }}
    .pe-line em {{
      padding: 4px 9px;
      border-radius: 999px;
      color: #16835f;
      background: rgba(22, 131, 95, 0.12);
      font-style: normal;
    }}
    .pe-line.muted {{
      color: #68707a;
      font-size: 12px;
    }}
    .pe-line.muted strong {{
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }}
    .momentum-label {{
      display: flex;
      justify-content: space-between;
      margin-top: 14px;
      color: #68707a;
      font-size: 12px;
      font-weight: 800;
    }}
    .momentum-bar {{
      height: 7px;
      margin-top: 6px;
      border-radius: 999px;
      background: #e3e7e0;
      overflow: hidden;
    }}
    .momentum-bar span {{
      display: block;
      height: 100%;
      border-radius: inherit;
      background: var(--accent);
    }}
    .pill {{
      display: flex;
      justify-content: center;
      width: 180px;
      margin: 10px auto 14px;
      padding: 7px 12px;
      border: 2px solid var(--accent);
      border-radius: 999px;
      color: var(--accent);
      font-weight: 900;
    }}
    .range {{
      display: grid;
      grid-template-columns: repeat(4, 1fr);
      gap: 8px;
      color: #68707a;
      font-size: 12px;
      font-weight: 700;
    }}
    .range span {{
      padding: 7px 6px;
      border-radius: 7px;
      background: #f1f3ef;
      text-align: center;
    }}
    .event-list {{
      margin: 14px 0 0;
      padding: 0;
      list-style: none;
      display: grid;
      gap: 10px;
    }}
    .event-list li {{
      display: grid;
      gap: 4px 12px;
      padding: 10px 12px;
      border-radius: 8px;
      background: #f3f5f1;
    }}
    .event-list span {{
      color: #68707a;
      font-size: 12px;
      font-weight: 800;
    }}
    .event-list strong {{
      font-size: 16px;
      font-weight: 900;
    }}
    .event-list small {{
      color: #68707a;
      font-size: 12px;
      font-weight: 700;
      line-height: 1.35;
    }}
    .policy-impact {{
      display: grid;
      gap: 8px;
      margin-top: 12px;
    }}
    .policy-impact div {{
      padding: 10px 12px;
      border-radius: 8px;
      background: #f3f5f1;
    }}
    .policy-impact span {{
      display: block;
      color: #68707a;
      font-size: 12px;
      font-weight: 800;
    }}
    .policy-impact strong {{
      display: block;
      margin-top: 3px;
      font-size: 13px;
      line-height: 1.35;
    }}
    .asset-row {{
      display: grid;
      grid-template-columns: 1fr auto auto;
      gap: 12px;
      align-items: center;
      padding: 11px 0;
      border-top: 1px solid #e0e3dc;
      font-size: 16px;
    }}
    .asset-row:first-of-type {{ border-top: none; }}
    .asset-row strong {{
      font-size: 23px;
      font-weight: 900;
    }}
    .policy-row {{
      display: grid;
      gap: 5px;
      padding: 11px 0;
      border-top: 1px solid #e0e3dc;
    }}
    .policy-row span {{
      color: #68707a;
      font-size: 13px;
      font-weight: 800;
    }}
    .policy-row strong {{
      font-size: 14px;
      line-height: 1.4;
    }}
    .strategy {{
      margin: 0 0 18px;
      border: 2px solid #1b9a74;
      background: rgba(255, 255, 255, 0.82);
    }}
    .strategy h2 {{
      color: #138464;
    }}
    .strategy-grid {{
      display: grid;
      grid-template-columns: 1.2fr 1.8fr;
      gap: 12px;
      margin-top: 12px;
    }}
    .strategy-box {{
      min-height: 84px;
      padding: 13px;
      border-radius: 8px;
      border: 1px solid #d7dbd4;
      background: #fbfcf8;
    }}
    .strategy-box span {{
      display: block;
      color: #68707a;
      font-size: 13px;
      font-weight: 800;
    }}
    .strategy-box strong {{
      display: block;
      margin-top: 6px;
      font-size: 19px;
      line-height: 1.22;
    }}
    .strategy-action strong {{
      font-size: 27px;
      line-height: 1;
    }}
    .strategy-position {{
      display: grid;
      grid-template-columns: repeat(2, 1fr);
      gap: 8px;
      margin-top: 11px;
    }}
    .strategy-position em {{
      display: block;
      padding: 7px 8px;
      border-radius: 7px;
      background: #f1f3ef;
      color: #4c5560;
      font-size: 13px;
      font-style: normal;
      font-weight: 900;
      text-align: center;
    }}
    .strategy-notes {{
      margin: 10px 0 0;
      padding-left: 19px;
      color: #333b45;
      font-size: 13px;
      line-height: 1.42;
    }}
    .strategy-notes li {{
      margin-top: 4px;
    }}
    .data-alert {{
      margin: 0 0 13px;
      padding: 10px 12px;
      border: 1px solid #e1b45b;
      border-radius: 7px;
      background: #fff8e8;
      color: #6d4a07;
      font-size: 12px;
      line-height: 1.35;
    }}
    .data-alert strong {{
      display: block;
      margin-bottom: 4px;
      font-size: 13px;
      color: #5d3f04;
    }}
    .data-alert ul {{
      margin: 0;
      padding-left: 17px;
    }}
    .data-alert li {{
      margin-top: 3px;
    }}
    .footer {{
      margin-top: 20px;
      display: flex;
      justify-content: space-between;
      color: #7c838b;
      font-size: 12px;
    }}
  </style>
</head>
<body>
  <div class="card">
    <div class="topbar">
      <div class="label">◆ DAILY MARKET PULSE</div>
      <div class="date-pill">行情 {brief.as_of.isoformat()} / 生成 {generated_at}</div>
    </div>
    <h1>纳指100长期持仓观察</h1>
    {data_warnings_html}

    <section class="tile strategy">
      <div class="tile-head">
        <div>
          <h2>今日策略</h2>
          <p>长期核心仓位优先，战术仓位小步调整</p>
        </div>
        <span class="badge {_temperature_class(brief.temperature.label)}">温度 {brief.temperature.score}</span>
      </div>
      <div class="strategy-grid">
        <div class="strategy-box strategy-action">
          <span>明日动作</span>
          <strong>{brief.advice.action}</strong>
          <div class="strategy-position">
            <em>参考 {brief.advice.allocation_band}</em>
            <em>当前 {config.portfolio.current_allocation:.0%}</em>
          </div>
        </div>
        <div class="strategy-box">
          <span>重点</span>
          <strong>{brief.observation_points[0]}</strong>
          <ul class="strategy-notes">{observations_html}</ul>
        </div>
      </div>
    </section>

    <div class="grid">
      {_index_card("QQQ", "纳指100 ETF · 当日收盘", brief.qqq, "green", qqq_extra)}
      {_index_card("NASDAQ 100", "^NDX · 指数点位", brief.ndx, "violet", ndx_extra)}
      {_vol_card("VIX", "标普500波动率 · CBOE VIX", brief.vix, "green", (15, 22, 32))}
      {_vol_card("VXN", "纳指100波动率 · CBOE VXN", brief.vxn, "violet", (18, 24, 32))}

      <section class="tile gold">
        <div class="tile-head">
          <div>
            <h2>事件与政策基本面</h2>
            <p>{escape(brief.policy.stance)} · 执行约束优先</p>
          </div>
        </div>
        <ul class="event-list">{policy_events_html}</ul>
      </section>

      <section class="tile blue">
        <div class="tile-head">
          <div>
            <h2>调仓影响</h2>
            <p>短期执行 · 中期仓位 · 长期逻辑</p>
          </div>
        </div>
        <div class="policy-impact">
          <div><span>执行</span><strong>{escape(brief.policy.execution_note)}</strong></div>
          <div><span>短期</span><strong>{escape(brief.policy.short_term)}</strong></div>
          <div><span>中期</span><strong>{escape(brief.policy.mid_term)}</strong></div>
          <div><span>长期</span><strong>{escape(brief.policy.long_term)}</strong></div>
        </div>
      </section>

      <section class="tile gold">
        <div class="tile-head">
          <div>
            <h2>跨资产</h2>
            <p>黄金 · 美债 · 原油</p>
          </div>
        </div>
        {_asset_row("黄金", _fmt_price(brief.gold.price), brief.gold.day_change_pct)}
        {_yield_asset_row("10Y 美债", brief.us10y)}
        {_asset_row("原油", _fmt_price(brief.oil.price), brief.oil.day_change_pct)}
      </section>

      <section class="tile blue">
        <div class="tile-head">
          <div>
            <h2>三因子</h2>
            <p>长持仓位调节框架</p>
          </div>
        </div>
        <div class="asset-row"><span>趋势</span><strong>{brief.advice.trend_state}</strong></div>
        <div class="asset-row"><span>风险</span><strong>{brief.advice.risk_state}</strong></div>
        <div class="asset-row"><span>估值</span><strong>{brief.advice.valuation_state}</strong></div>
      </section>
    </div>

    <div class="footer">
      <span>Data: Yahoo Finance · VCP Scanner · Fed/BEA/Nasdaq calendar cache</span>
      <span>仅供参考，不构成投资建议</span>
    </div>
  </div>
</body>
</html>
"""


def build_ashare_html(ashare: AShareSnapshot, config: AppConfig, as_of: str) -> str:
    ideas = ashare.top_ideas if ashare.enabled else []
    directions = ashare.directions[:3] if ashare.enabled else []
    direction_html = "".join(_ashare_direction_line(item) for item in directions)
    idea_html = "".join(_ashare_idea_line(item) for item in ideas)
    retained_html = "".join(_tracking_row(item) for item in ashare.retained_ideas)
    if not retained_html:
        retained_html = '<tr><td colspan="7" class="empty-row">暂无保留观察标的</td></tr>'
    long_term_html = "".join(_tracking_row(item) for item in ashare.long_term_ideas)
    if not long_term_html:
        long_term_html = '<tr><td colspan="7" class="empty-row">暂无长期追踪标的</td></tr>'
    validation_items = ashare.validation_summary or ["预测验证：暂无可回填样本，明日收盘后开始统计。"]
    validation_html = "".join(f"<li>{escape(item)}</li>" for item in validation_items[:3])
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <title>A股主动候选</title>
  <style>
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: #f4f2ed;
      color: #11151c;
      font-family: "Avenir Next", "PingFang SC", "Hiragino Sans GB", sans-serif;
    }}
    .card {{
      width: 960px;
      margin: 0 auto;
      padding: 34px 38px 30px;
      background:
        linear-gradient(135deg, rgba(194, 70, 62, 0.09), transparent 34%),
        linear-gradient(180deg, #fffdf8 0%, #f1f2eb 100%);
    }}
    .topbar {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 18px;
    }}
    .label {{
      display: inline-flex;
      padding: 9px 20px;
      border-radius: 999px;
      background: #11151c;
      color: #fff;
      font-size: 16px;
      font-weight: 900;
      letter-spacing: 0.02em;
    }}
    .date-pill {{
      padding: 9px 22px;
      border: 1px solid #d2d0c9;
      border-radius: 999px;
      background: rgba(255, 255, 255, 0.76);
      color: #555d68;
      font-size: 16px;
    }}
    h1 {{
      margin: 8px 0 10px;
      font-size: 48px;
      line-height: 1.08;
      letter-spacing: 0;
    }}
    .subtitle {{
      margin: 0 0 22px;
      color: #68707a;
      font-size: 16px;
      line-height: 1.42;
      font-weight: 700;
    }}
    .summary {{
      display: grid;
      grid-template-columns: 2fr 1fr;
      gap: 12px;
      margin-bottom: 18px;
    }}
    .summary-box {{
      min-height: 96px;
      padding: 14px;
      border: 1px solid #ddd9cf;
      border-radius: 8px;
      background: rgba(255, 255, 255, 0.78);
    }}
    .summary-box span {{
      display: block;
      color: #68707a;
      font-size: 13px;
      font-weight: 800;
    }}
    .summary-box strong {{
      display: block;
      margin-top: 7px;
      font-size: 20px;
      line-height: 1.22;
    }}
    .summary-box.note strong {{
      font-size: 18px;
    }}
    .validation-list {{
      margin: -4px 0 18px;
      padding: 12px 14px;
      list-style: none;
      border: 1px solid #ddd9cf;
      border-radius: 8px;
      background: rgba(255, 255, 255, 0.62);
      color: #333b45;
      font-size: 14px;
      line-height: 1.45;
      font-weight: 800;
    }}
    .validation-list li + li {{
      margin-top: 4px;
      color: #68707a;
    }}
    .direction-list {{
      margin: 0 0 18px;
      padding: 0;
      list-style: none;
      display: grid;
      grid-template-columns: repeat(3, 1fr);
      gap: 14px;
    }}
    .direction-list li {{
      min-height: 166px;
      padding: 14px;
      border-radius: 8px;
      border: 1px solid #ddd9cf;
      background: rgba(255, 255, 255, 0.82);
      box-shadow: 0 5px 18px rgba(24, 28, 34, 0.04);
    }}
    .direction-head {{
      display: flex;
      justify-content: space-between;
      gap: 10px;
      align-items: center;
    }}
    .direction-head strong {{
      font-size: 19px;
      line-height: 1.15;
    }}
    .direction-head span {{
      display: inline-flex;
      justify-content: center;
      min-width: 54px;
      padding: 5px 9px;
      border-radius: 999px;
      font-size: 12px;
      font-weight: 900;
    }}
    .direction-score {{
      margin-top: 8px;
      font-size: 34px;
      line-height: 1;
      font-weight: 900;
    }}
    .etf-action {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      margin-top: 9px;
      padding: 5px 10px;
      border-radius: 999px;
      font-size: 11px;
      font-weight: 900;
    }}
    .etf-buy {{
      color: #0f7a58;
      background: rgba(22, 131, 95, 0.15);
      border: 1px solid rgba(22, 131, 95, 0.34);
    }}
    .etf-watch {{
      color: #9b6b00;
      background: rgba(214, 170, 42, 0.20);
      border: 1px solid rgba(214, 170, 42, 0.42);
    }}
    .etf-hold {{
      color: #415064;
      background: rgba(104, 112, 122, 0.15);
      border: 1px solid rgba(104, 112, 122, 0.30);
    }}
    .etf-avoid {{
      color: #a8324f;
      background: rgba(182, 61, 91, 0.15);
      border: 1px solid rgba(182, 61, 91, 0.34);
    }}
    .direction-list p {{
      margin: 8px 0 0;
      color: #29313b;
      font-size: 13px;
      line-height: 1.34;
      min-height: 36px;
    }}
    .direction-metrics {{
      display: grid;
      grid-template-columns: repeat(3, 1fr);
      gap: 6px;
      margin-top: 9px;
    }}
    .direction-metrics span {{
      padding: 6px 4px;
      border-radius: 7px;
      background: #f2f1ec;
      color: #4c5560;
      text-align: center;
      font-size: 11px;
      font-weight: 900;
      white-space: nowrap;
    }}
    .direction-list small {{
      display: block;
      margin-top: 8px;
      color: #68707a;
      font-size: 12px;
      font-weight: 800;
    }}
    .direction-open .direction-head span {{
      color: #0f7a58;
      background: rgba(22, 131, 95, 0.15);
      border: 1px solid rgba(22, 131, 95, 0.34);
    }}
    .direction-watch .direction-head span {{
      color: #9b6b00;
      background: rgba(214, 170, 42, 0.20);
      border: 1px solid rgba(214, 170, 42, 0.42);
    }}
    .direction-track .direction-head span {{
      color: #415064;
      background: rgba(104, 112, 122, 0.15);
      border: 1px solid rgba(104, 112, 122, 0.30);
    }}
    .direction-avoid .direction-head span {{
      color: #a8324f;
      background: rgba(182, 61, 91, 0.15);
      border: 1px solid rgba(182, 61, 91, 0.34);
    }}
    .idea-list {{
      margin: 0;
      padding: 0;
      list-style: none;
      display: grid;
      grid-template-columns: repeat(2, 1fr);
      gap: 14px;
    }}
    .section-title {{
      margin: 0 0 12px;
      font-size: 20px;
      letter-spacing: 0;
    }}
    .idea-list li {{
      min-height: 174px;
      padding: 15px;
      border: 1px solid #ddd9cf;
      border-radius: 8px;
      background: rgba(255, 255, 255, 0.82);
      box-shadow: 0 5px 18px rgba(24, 28, 34, 0.05);
    }}
    .idea-main {{
      display: grid;
      grid-template-columns: auto 1fr auto;
      gap: 10px;
      align-items: center;
    }}
    .rating {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      width: 34px;
      height: 34px;
      border-radius: 50%;
      border: 2px solid currentColor;
      font-weight: 900;
      background: rgba(255, 255, 255, 0.78);
    }}
    .rating.up {{
      color: #0f7a58;
      background: rgba(22, 131, 95, 0.14);
    }}
    .rating.down {{
      color: #a8324f;
      background: rgba(182, 61, 91, 0.14);
    }}
    .rating.flat {{
      color: #48515d;
      background: rgba(104, 112, 122, 0.14);
    }}
    .up {{ color: #16835f; }}
    .down {{ color: #b63d5b; }}
    .flat {{ color: #6d7480; }}
    .idea-main strong {{
      display: block;
      font-size: 18px;
      line-height: 1.1;
    }}
    .idea-main small, .stock-name small {{
      color: #68707a;
      font-size: 12px;
    }}
    .idea-main em {{
      display: block;
      margin-top: 4px;
      color: #68707a;
      font-size: 12px;
      font-style: normal;
      font-weight: 800;
    }}
    .idea-main b {{
      font-size: 25px;
    }}
    .idea-list p {{
      margin: 10px 0 0;
      color: #29313b;
      font-size: 13px;
      line-height: 1.38;
      min-height: 38px;
    }}
    .idea-meta {{
      display: grid;
      grid-template-columns: repeat(4, 1fr);
      gap: 6px;
      margin-top: 10px;
    }}
    .idea-meta span {{
      padding: 6px 5px;
      border-radius: 7px;
      background: #f2f1ec;
      color: #4c5560;
      text-align: center;
      font-size: 11px;
      font-weight: 800;
      white-space: nowrap;
    }}
    .idea-meta .action-pill,
    .idea-meta .pressure-pill,
    .table-action {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      min-width: 72px;
      border-radius: 999px;
      font-weight: 900;
    }}
    .idea-meta .pressure-pill {{
      color: #174c86;
      background: rgba(42, 117, 196, 0.14);
      border: 1px solid rgba(42, 117, 196, 0.30);
    }}
    .idea-meta .action-buy,
    .table-action.action-buy {{
      color: #0f7a58;
      background: rgba(22, 131, 95, 0.15);
      border: 1px solid rgba(22, 131, 95, 0.32);
    }}
    .idea-meta .action-watch,
    .table-action.action-watch {{
      color: #9b6b00;
      background: rgba(214, 170, 42, 0.20);
      border: 1px solid rgba(214, 170, 42, 0.42);
    }}
    .idea-meta .action-hold,
    .table-action.action-hold {{
      color: #415064;
      background: rgba(104, 112, 122, 0.15);
      border: 1px solid rgba(104, 112, 122, 0.30);
    }}
    .idea-meta .action-avoid,
    .table-action.action-avoid {{
      color: #a8324f;
      background: rgba(182, 61, 91, 0.15);
      border: 1px solid rgba(182, 61, 91, 0.34);
    }}
    .idea-risk {{
      display: block;
      margin-top: 9px;
      color: #68707a;
      font-size: 12px;
      line-height: 1.35;
    }}
    .table-card {{
      margin-top: 18px;
      padding: 17px 18px 16px;
      border: 1px solid #ddd9cf;
      border-radius: 8px;
      background: rgba(255, 255, 255, 0.82);
    }}
    h2 {{
      margin: 0 0 12px;
      font-size: 20px;
      letter-spacing: 0;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      font-size: 13px;
    }}
    th {{
      padding: 8px 9px;
      color: #68707a;
      text-align: left;
      border-bottom: 2px solid #dedbd2;
      font-weight: 900;
    }}
    td {{
      padding: 9px;
      border-bottom: 1px solid #e6e3da;
      font-weight: 800;
      vertical-align: middle;
    }}
    tr:last-child td {{ border-bottom: none; }}
    .stock-name {{
      font-size: 15px;
      font-weight: 900;
    }}
    .stock-meta {{
      margin-top: 2px;
      color: #68707a;
      font-size: 12px;
    }}
    .score {{
      font-size: 20px;
      font-weight: 900;
    }}
    .empty-row {{
      padding: 18px 9px;
      color: #68707a;
      text-align: center;
      font-weight: 900;
    }}
    .footer {{
      margin-top: 18px;
      display: flex;
      justify-content: space-between;
      color: #7c838b;
      font-size: 12px;
    }}
  </style>
</head>
<body>
  <div class="card">
    <div class="topbar">
      <div class="label">A-SHARE ACTIVE IDEAS</div>
      <div class="date-pill">{escape(as_of)} / 生成 {datetime.now().strftime("%H:%M")}</div>
    </div>
    <h1>A股主动候选观察</h1>
    <p class="subtitle">强方向自动召回候选并深筛，只做研究排序和仓位提示；美股仍保持被动指数基金框架。</p>

    <div class="summary">
      <div class="summary-box note"><span>今日主线</span><strong>{escape(ashare.market_note)}</strong></div>
      <div class="summary-box"><span>候选池</span><strong>{ashare.watchlist_count} 只</strong></div>
    </div>

    <ul class="validation-list">{validation_html}</ul>

    <ul class="direction-list">{direction_html}</ul>

    <h2 class="section-title">今日推荐</h2>
    <ul class="idea-list">{idea_html}</ul>

    <section class="table-card">
      <h2>保留观察</h2>
      <table>
        <thead>
          <tr>
            <th>股票</th>
            <th>评级</th>
            <th>分数</th>
            <th>现价</th>
            <th>动作</th>
            <th>资金</th>
            <th>20日</th>
          </tr>
        </thead>
        <tbody>{retained_html}</tbody>
      </table>
    </section>

    <section class="table-card">
      <h2>长期追踪</h2>
      <table>
        <thead>
          <tr>
            <th>股票</th>
            <th>评级</th>
            <th>分数</th>
            <th>现价</th>
            <th>动作</th>
            <th>资金</th>
            <th>20日</th>
          </tr>
        </thead>
        <tbody>{long_term_html}</tbody>
      </table>
    </section>

    <div class="footer">
      <span>Data: 东方财富 / 新浪 quote & kline · AKShare 财务/估值/预测 · 东方财富资金流</span>
      <span>仅供研究参考，不构成投资建议</span>
    </div>
  </div>
</body>
</html>
"""


def write_html(brief: Brief, config: AppConfig) -> Path:
    config.render.output_dir.mkdir(parents=True, exist_ok=True)
    output_path = config.render.output_dir / "brief.html"
    output_path.write_text(build_html(brief, config), encoding="utf-8")
    return output_path


def write_ashare_html(brief: Brief, config: AppConfig) -> Path | None:
    if brief.ashare is None or not brief.ashare.enabled:
        return None
    return write_ashare_snapshot_html(brief.ashare, config)


def write_ashare_snapshot_html(
    ashare: AShareSnapshot,
    config: AppConfig,
    as_of: str | None = None,
) -> Path | None:
    if not ashare.enabled:
        return None
    config.render.output_dir.mkdir(parents=True, exist_ok=True)
    output_path = config.render.output_dir / "ashare.html"
    output_path.write_text(
        build_ashare_html(ashare, config, as_of or date.today().isoformat()),
        encoding="utf-8",
    )
    return output_path


def write_png(html_path: Path, config: AppConfig, filename: str = "brief.png") -> Path:
    png_path = config.render.output_dir / filename
    script_path = Path(__file__).with_name("screenshot.js")
    env = os.environ.copy()
    env["NODE_PATH"] = str(config.render.node_modules)
    chrome_path = str(config.render.chrome_path)
    if chrome_path in {"", "."}:
        chrome_path = ""
    subprocess.run(
        [
            str(config.render.node_bin),
            str(script_path),
            str(html_path),
            str(png_path),
            str(config.render.viewport_width),
            str(config.render.viewport_height),
            chrome_path,
        ],
        check=True,
        env=env,
    )
    return png_path
