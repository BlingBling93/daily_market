from __future__ import annotations

import os
import subprocess
from datetime import datetime
from pathlib import Path

from .config import AppConfig
from .models import Brief, QuoteSnapshot, ThemeHeat


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


def _theme_line(theme: ThemeHeat) -> str:
    return (
        f"<li><span>{theme.theme}</span>"
        f"<strong class=\"{_move_class(theme.avg_return_5d)}\">{_fmt_pct(theme.avg_return_5d)}</strong>"
        f"<small>1日 {_fmt_pct(theme.avg_day_change_pct)} · 上涨占比 {theme.winners_ratio:.0%}</small></li>"
    )


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
    return f"""
      <div class="asset-row">
        <span>{name}</span>
        <strong>{_fmt_us10y_yield(snapshot.price)}</strong>
        {_yield_badge(snapshot)}
      </div>
    """


def build_html(brief: Brief, config: AppConfig) -> str:
    valuation = brief.valuation
    trailing_text = f"{valuation.trailing_pe:.1f}" if valuation.trailing_pe is not None else "暂无"
    forward_text = f"{valuation.forward_pe:.1f}" if valuation.forward_pe is not None else "暂无"
    valuation_source = valuation.source
    if valuation.as_of:
        valuation_source = f"{valuation_source} · {valuation.as_of}"

    hot_html = "".join(_theme_line(item) for item in brief.hot_themes)
    cool_html = "".join(_theme_line(item) for item in brief.cooling_themes)
    observations_html = "".join(f"<li>{item}</li>" for item in brief.observation_points)

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
    .theme-list {{
      margin: 14px 0 0;
      padding: 0;
      list-style: none;
      display: grid;
      gap: 10px;
    }}
    .theme-list li {{
      display: grid;
      grid-template-columns: 1fr auto;
      gap: 4px 12px;
      padding: 10px 12px;
      border-radius: 8px;
      background: #f3f5f1;
    }}
    .theme-list span {{
      font-size: 16px;
      font-weight: 900;
    }}
    .theme-list strong {{
      font-size: 17px;
      font-weight: 900;
    }}
    .theme-list small {{
      grid-column: 1 / 3;
      color: #68707a;
      font-size: 12px;
      font-weight: 700;
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
      grid-template-columns: 1.2fr repeat(3, 1fr);
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
    .observations {{
      margin: 16px 0 0;
      padding-left: 19px;
      color: #333b45;
      font-size: 15px;
      line-height: 1.42;
      columns: 2;
      column-gap: 28px;
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
      <div class="date-pill">{brief.as_of.isoformat()} / 生成 {datetime.now().strftime("%H:%M")}</div>
    </div>
    <h1>纳指100长期持仓观察</h1>

    <section class="tile strategy">
      <div class="tile-head">
        <div>
          <h2>今日策略</h2>
          <p>长期核心仓位优先，战术仓位小步调整</p>
        </div>
        <span class="badge {_temperature_class(brief.temperature.label)}">温度 {brief.temperature.score}</span>
      </div>
      <div class="strategy-grid">
        <div class="strategy-box"><span>明日动作</span><strong>{brief.advice.action}</strong></div>
        <div class="strategy-box"><span>参考仓位</span><strong>{brief.advice.allocation_band}</strong></div>
        <div class="strategy-box"><span>当前仓位</span><strong>{config.portfolio.current_allocation:.0%}</strong></div>
        <div class="strategy-box"><span>重点</span><strong>{brief.observation_points[0]}</strong></div>
      </div>
      <ul class="observations">{observations_html}</ul>
    </section>

    <div class="grid">
      {_index_card("QQQ", "纳指100 ETF · 当日收盘", brief.qqq, "green", qqq_extra)}
      {_index_card("NASDAQ 100", "^NDX · 指数点位", brief.ndx, "violet", ndx_extra)}
      {_vol_card("VIX", "标普500波动率 · CBOE VIX", brief.vix, "green", (15, 22, 32))}
      {_vol_card("VXN", "纳指100波动率 · CBOE VXN", brief.vxn, "violet", (18, 24, 32))}

      <section class="tile gold">
        <div class="tile-head">
          <div>
            <h2>热度集中方向</h2>
            <p>纳指风格主题 · 5日表现排序</p>
          </div>
        </div>
        <ul class="theme-list">{hot_html}</ul>
      </section>

      <section class="tile blue">
        <div class="tile-head">
          <div>
            <h2>降温方向</h2>
            <p>短线相对转弱的主题</p>
          </div>
        </div>
        <ul class="theme-list">{cool_html}</ul>
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
      <span>Data: Yahoo Finance · VCP Scanner · Nasdaq style basket</span>
      <span>仅供参考，不构成投资建议</span>
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


def write_png(html_path: Path, config: AppConfig) -> Path:
    png_path = config.render.output_dir / "brief.png"
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
