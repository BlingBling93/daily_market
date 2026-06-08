# Market Brief Scripts

Lightweight daily brief generators split by market so Nasdaq and A-share strategy work can be iterated independently.

## What it does

- Nasdaq script pulls `QQQ`, `^NDX`, `^VIX`, `^VXN`, cross-asset data, valuation, and policy events
- A-share script scores the local active watchlist and ETF direction pool
- Each script produces its own HTML card and PNG screenshot
- Each script can push its own image to Feishu when configured
- The Nasdaq script can still fall back to WeCom when configured

## Quick start

1. Optional: install extras if you want a richer local environment:

   ```bash
   pip3 install -r requirements.txt
   ```

2. Copy the sample config:

   ```bash
   cp config.example.yaml config.yaml
   ```

3. Generate today's Nasdaq brief:

   ```bash
   python3 -m nasdaq_morning_brief --config config.yaml
   ```

4. Generate today's A-share brief:

   ```bash
   python3 -m nasdaq_morning_brief.ashare_report --config config.yaml
   ```

5. Optional: run ad hoc local tests without pushing:

   ```bash
   ./run_nasdaq_brief.sh
   ./run_ashare_brief.sh
   ```

   To avoid appending A-share direction heat history during a local A-share debug run:

   ```bash
   python3 -B -m nasdaq_morning_brief.ashare_report --config config.yaml --no-push --skip-heat-history
   ```

## Config

`config.yaml` supports:

- current allocation and allocation limits
- manual valuation override for P/E metrics
- webhook for WeCom bot push
- webhook for Feishu bot push
- policy event calendar path and lookback/lookahead windows
- A-share active allocation target and watchlist path
- output directory

## Notes

- Index valuation sources are uneven on free endpoints, so the first version supports manual override in config.
- Policy events are maintained in `policy_events.csv`; use it for FOMC, CPI/PCE, employment data, Mag 7 earnings, and major regulatory/geopolitical events.
- A-share ideas are research candidates only. Edit `ashare_watchlist.csv` to update your universe, fundamentals, catalysts, and risk flags.
- The US sleeve remains passive-index oriented; the A-share module does not recommend US single stocks.

## Strategy docs

- Nasdaq long-term sleeve: `docs/nasdaq_strategy.zh.md` / `docs/nasdaq_strategy.md`
- A-share two-stage screening: `docs/ashare_two_stage_design.zh.md` / `docs/ashare_two_stage_design.md`

## Policy events

Policy events are fetched automatically into `policy_calendar_cache.json`.
Current automatic sources are:

- Federal Reserve FOMC calendar
- BEA release dates for PCE / Personal Income and Outlays and GDP
- Nasdaq public earnings calendar for the configured Mag 7 symbols
- Direct post-event result tracking where available, such as NVIDIA official
  earnings releases and FRED/BEA PCE price-index series
- Google News RSS only as a fallback when direct event data is unavailable

`policy_events.csv` remains available for manual overrides or events that the
free sources miss. It supports these columns:

```csv
date,category,title,stance,summary,short_term,mid_term,long_term,impact_days
```

Detailed event impact windows, execution constraints, and result-source tiers
are documented in the Nasdaq strategy docs.

## Asynchronous event discovery

Non-periodic major events are refreshed separately into
`policy_discovered_events_cache.json`:

```bash
./run_event_discovery.sh
```

This discovery job is intended for a low-frequency external cron, for example
two or three times per week outside the morning brief run. The morning brief only
reads the cache and merges `confirmed` / `probable` events whose importance is
above `discovery_min_importance`.

The discovery job uses broad event-pattern queries rather than single-entity
keywords. It looks for high-impact patterns such as IPO filings / pricing,
Nasdaq listings, index inclusion, export controls, antitrust actions, tariffs,
Treasury liquidity events, and AI / semiconductor infrastructure shocks. Each
candidate is scored by source quality, explicit event date, Nasdaq / QQQ
transmission channel, AI / semiconductor relevance, and scale language such as
large valuation or financing amounts.

Recommended external cron setup:

- Timezone: `Asia/Shanghai`
- Schedule: Tuesday / Thursday / Saturday at `07:30`
- Method: `POST`
- URL: `https://api.github.com/repos/BlingBling93/daily_market/actions/workflows/event-discovery.yml/dispatches`
- Headers:
  - `Accept: application/vnd.github+json`
  - `Authorization: Bearer <YOUR_GITHUB_PAT>`
  - `Content-Type: application/json`
- Body:

  ```json
  {"ref":"main"}
  ```

## A-share active module

The A-share module is intentionally local-first:

- `ashare_watchlist.csv` stores the initial candidate universe and qualitative notes
- Yahoo symbols such as `600519.SS` and `300750.SZ` are used only for price momentum
- Scores combine fundamentals, valuation percentile, catalysts, momentum, and risk/governance
- Ratings are `S/A/B/C/D/X`, where `S/A` are candidates, `B` is waitlist, and `X` is avoid

This keeps the daily card useful before connecting a paid source such as Wind,
Choice, iFinD, Tushare Pro, JQData, or Ricequant.

## GitHub Actions

The cloud workflow lives in `.github/workflows/morning-brief.yml`. One external
cron dispatch triggers the workflow once, and the workflow generates both the
Nasdaq image and the A-share image in sequence.

Set these repository secrets before enabling it:

- `FEISHU_WEBHOOK`
- `FEISHU_APP_ID`
- `FEISHU_APP_SECRET`
- `FEISHU_SECRET` if your bot uses signature verification

GitHub's native `schedule` trigger can be delayed or skipped, so this project uses
an external cron service to trigger the combined workflow through the GitHub API.

Recommended external cron setup:

- Timezone: `Asia/Shanghai`
- Schedule: every day at `13:45`
- Method: `POST`
- URL: `https://api.github.com/repos/BlingBling93/daily_market/actions/workflows/morning-brief.yml/dispatches`
- Headers:
  - `Accept: application/vnd.github+json`
  - `Authorization: Bearer <YOUR_GITHUB_PAT>`
  - `X-GitHub-Api-Version: 2022-11-28`
  - `Content-Type: application/json`
- Body:

  ```json
  {"ref":"main"}
  ```

Create `<YOUR_GITHUB_PAT>` as a GitHub fine-grained personal access token scoped
only to this repository, with `Actions: Read and write` permission.
