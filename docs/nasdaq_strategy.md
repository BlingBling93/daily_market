# Nasdaq Long-Term Sleeve Strategy

The Nasdaq module is designed for a long-term QQQ / Nasdaq 100 core sleeve. It first generates a base action from trend, volatility, and valuation, then uses the event / policy layer only to adjust execution.

The goal is to keep the passive index sleeve stable while avoiding one-step chasing around major events, crowded short-term moves, or unresolved rate risk.

## Base Allocation Framework

The base action uses three signal groups:

- Trend: QQQ versus the 20-day, 50-day, and 200-day moving averages.
- Risk: VXN / VIX and the composite temperature score.
- Valuation: Nasdaq 100 trailing P/E and forward P/E.

Base actions:

| Condition | Base action |
|---|---|
| QQQ breaks below the 200-day moving average | `防守` |
| QQQ breaks below the 50-day moving average and VXN heats up | `小幅降仓` |
| Trend is healthy but price is extended and valuation is high | `暂停加仓` |
| Trend is intact but market fear spikes | `小幅加仓` |
| Trend is healthy and volatility is calm or neutral | `持有` |
| Signals are mixed | `持有` |

Allocation changes are made in small `step_allocation` increments. The framework is not a full exit / re-entry timing model.

## Event Layer and Execution Constraints

The event / policy layer adjusts execution, not the core allocation framework. Its main job is to avoid chasing strength or adding in one step around market-moving events.

The policy calendar uses two windows:

- Display window: still uses `lookahead_days` / `lookback_days` so the brief can show upcoming and recent events.
- Execution window: only overrides actions when an event is near or still inside a short post-event digestion window, turning `持有` / `小幅加仓` into `持有观察`.

Routine macro events use short default impact windows:

| Category | Default impact | Pre-event execution constraint |
|---|---:|---:|
| `增长` / GDP | 2 days | 1 day |
| `通胀` / CPI, PCE | 3 days | 1 day |
| `就业` / payrolls | 3 days | 2 days |
| `美联储讲话` / Fed speeches, testimony | 2 days | 1 day |
| `FOMC` | 5 days | 3 days |
| `财报` / major index-weight earnings | 5 days | 2 days |

Structural shocks such as regulation, geopolitics, tech regulation, and liquidity keep a 14-day default impact window. Routine GDP / PCE releases should not automatically keep the sleeve in `持有观察` for more than two weeks; the override should persist only when rates, VXN, trend, or index breadth confirm sustained repricing.

## Event Data Sources

The official calendar layer only determines event date, category, and release time:

- Federal Reserve FOMC calendar
- Federal Reserve speeches RSS for Fed official speeches / testimony
- BEA release schedule for GDP / PCE
- BLS CPI / employment schedule when available
- Nasdaq public earnings calendar
- Manual fallback in `policy_events.csv`

When BLS schedule pages are blocked or temporarily unavailable, the payroll release date falls back to the monthly employment-report rule: normally the first Friday of the month, moved to the previous business day when it conflicts with a U.S. federal holiday.

The official data layer takes first-party or official-proxy results whenever possible:

- FRED / BEA series for macro results such as PCE and GDP.
- Company IR / official releases for major index-weight earnings.

The media interpretation layer is only a temporary fallback when official results are blocked:

- Prefer Reuters, WSJ, AP, CNBC, Bloomberg, MarketWatch, 财联社, and 华尔街见闻.
- Media results must be labelled as pending official verification.
- Media results are cached for only 12 hours.
- When official / FRED / company IR results are unavailable and at least two higher-quality media sources agree, the result can be promoted to `trusted_media_fallback` and temporarily participate in event stance and execution analysis.
- Single-source media remains a short-term hint only and cannot directly decide add or defense actions.

## Non-Periodic Event Discovery

Periodic and non-periodic events use different refresh cadences:

- Periodic calendars are read from `policy_calendar_cache.json`, refreshed at low frequency, and recalibrated again shortly before an event lands.
- Non-periodic events are refreshed separately into `policy_discovered_events_cache.json`; the morning brief only consumes this cache and does not fetch news on the brief generation path.
- High-conviction manual events can still be added to `policy_events.csv`, which takes precedence over automatic discovery.

The non-periodic event discovery job is not a daily news searcher. It is a low-frequency asynchronous event radar, intended to run from a separate cron two or three times per week. Its job is to catch major catalysts with a natural fermentation period that do not appear on official macro or earnings calendars, such as:

- Mega IPOs, S-1 filings, price ranges, pricing dates, and Nasdaq listings.
- Nasdaq 100 additions / removals, special rebalances, and index-weight events.
- AI / semiconductor export controls, antitrust actions, tariffs, and tech regulation.
- Treasury refunding, debt ceiling, government shutdown, liquidity drain, and other funding shocks.
- Fed Chair / FOMC-related speeches, testimony, and forward-guidance shifts.
- AI infrastructure, semiconductor, cloud capex, or data-center events that may alter earnings expectations.

The discovery job aggregates headlines and summaries from higher-signal sources, then extracts event patterns. It should not depend on a single entity keyword. Names such as SpaceX, OpenAI, Anthropic, and Cerebras are possible extracted subjects, not hard-coded strategy assumptions.

Candidates are scored across these dimensions:

| Dimension | Role |
|---|---|
| Source quality | Reuters, Bloomberg, WSJ, FT, Axios, CNBC, Nasdaq, SEC, AP, and similar sources receive higher weight |
| Explicit date | Pricing dates, listing dates, filing dates, and policy effective dates increase confidence |
| Nasdaq / QQQ transmission | Nasdaq listings, Nasdaq 100, QQQ, passive index flows, and mega-cap technology exposure increase importance |
| AI / semiconductor relevance | AI, GPU, semiconductor, cloud, and data-center exposure increase importance |
| Scale language | Billion / trillion language, mega valuation, large financing, or forced passive flows increase importance |
| Policy / liquidity channel | Regulation, tariffs, export licenses, Treasury rates, and dollar liquidity increase importance |

Event status tiers:

| Status | Morning brief treatment |
|---|---|
| `confirmed` | Clear date and high-quality source; included in the major event calendar |
| `probable` | Clear date or multi-source confirmation, but still needs monitoring; included in the major event calendar |
| `watch` | Fermenting event with incomplete date or transmission channel; retained only in the discovery cache |
| `expired` | Past its impact window or superseded by newer information; excluded from the brief |

Only `confirmed` / `probable` events above `discovery_min_importance` are merged into the morning brief event calendar. `watch` events are kept for later upgrade but do not directly constrain allocation, so ordinary news noise does not become a trading override.

## Result Source Tiers

Event results use `result_source_tier`:

| Tier | Use |
|---|---|
| `news_content` | Read article content from 财联社 or 华尔街见闻; eligible for event-result and short-term execution analysis |
| `unverified` | No readable article content was obtained, or the result is pending verification |

Event-result collection runs only for events already in the calendar. The news capturer uses search results to locate candidate articles, then reads article content only from 财联社 or 华尔街见闻; RSS titles and summaries never determine the event result. Content results refresh every 12 hours.

An external cron triggers the independent result capturer through `workflow_dispatch`. It merges manual, automatic, and confirmed discovered events into the calendar, de-duplicates by event ID, captures content for events in their result window, and writes `policy_event_news_cache.json`. The morning brief consumes this persisted cache rather than depending on a fresh fetch during rendering.

## Market Data Sources and Freshness

The market-data layer no longer depends on Yahoo Finance as a single point of failure. The morning brief uses these priorities:

| Data | Primary / fallback source | Date basis |
|---|---|---|
| QQQ | Nasdaq API, with Yahoo Finance as fallback | US trading-day close |
| Nasdaq 100 / NDX | Nasdaq API, with Yahoo Finance as fallback | US trading-day close |
| VIX / VXN | Cboe official daily history, with Yahoo Finance as fallback | US trading-day close |
| Gold / oil proxies | Yahoo Finance; GLD / USO can fall back to Nasdaq API | Proxy-asset trading-day close |
| 10Y Treasury | Yahoo Finance / FRED DGS10; downgrade with a warning if both fail | Daily yield |

The top-right `行情 YYYY-MM-DD` date is the QQQ / Nasdaq trading-day date, not the report generation date. `生成 YYYY-MM-DD HH:MM` is the runtime local timestamp; the configured workflow is interpreted from the Beijing-time operating schedule.

Freshness rules:

- When a non-Yahoo source is used, the brief shows a top-level data notice listing the actual source for each affected asset.
- If any market data date is earlier than the QQQ benchmark date, the brief lists the stale asset and date.
- If a data point cannot be fetched correctly, the relevant card is downgraded to `暂无` and the top-level notice calls it out. The report must not silently reuse stale data.
