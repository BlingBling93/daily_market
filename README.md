# Nasdaq Morning Brief

Lightweight daily Nasdaq 100 brief generator for a single passive index position.

## What it does

- Pulls market data for `QQQ`, `^NDX`, `^VIX`, and `^VXN`
- Estimates short-term market temperature from price trend and volatility
- Tracks event and policy fundamentals from a local event calendar
- Scores a local A-share watchlist for active stock research candidates
- Produces an HTML card ready for browser screenshot or automation capture
- Sends the card summary to WeCom webhook when configured
- Sends the card summary to Feishu webhook when configured

## Quick start

1. Optional: install extras if you want a richer local environment:

   ```bash
   pip3 install -r requirements.txt
   ```

2. Copy the sample config:

   ```bash
   cp config.example.yaml config.yaml
   ```

3. Generate today's brief:

   ```bash
   python3 -m nasdaq_morning_brief --config config.yaml
   ```

4. Optional: schedule it on macOS `launchd` at 8:00 every day:

   ```bash
   launchctl load ~/Documents/New\ project\ 2/launchd.com.blingbili.nasdaq-morning-brief.plist
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

Use `category` values such as `FOMC`, `通胀`, `就业`, `财报`, `监管`, or `地缘`.
`impact_days` is optional in spirit but recommended: it keeps an event active
after the event date while the market digests its rate, earnings, or risk
premium impact. If it is blank, the app uses category defaults such as 21 days
for `FOMC`, 10 days for `通胀` / `就业`, and 14 days for `财报`.
The morning brief treats these as execution constraints on top of the base
`明日动作`, not as a second conflicting action engine.

Post-event result snippets are cached in `policy_event_news_cache.json`. The
cache is refreshed on the event's ET date and during its impact window, then the
result conclusion remains visible until the impact window expires.

## A-share active module

The A-share module is intentionally local-first:

- `ashare_watchlist.csv` stores the initial candidate universe and qualitative notes
- Yahoo symbols such as `600519.SS` and `300750.SZ` are used only for price momentum
- Scores combine fundamentals, valuation percentile, catalysts, momentum, and risk/governance
- Ratings are `S/A/B/C/D/X`, where `S/A` are candidates, `B` is waitlist, and `X` is avoid

This keeps the daily card useful before connecting a paid source such as Wind,
Choice, iFinD, Tushare Pro, JQData, or Ricequant.

## GitHub Actions

The cloud workflow lives in `.github/workflows/morning-brief.yml`.

Set these repository secrets before enabling it:

- `FEISHU_WEBHOOK`
- `FEISHU_APP_ID`
- `FEISHU_APP_SECRET`
- `FEISHU_SECRET` if your bot uses signature verification

GitHub's native `schedule` trigger can be delayed or skipped, so this project uses
an external cron service to trigger the workflow through the GitHub API.

Recommended external cron setup:

- Timezone: `Asia/Shanghai`
- Schedule: every day at `14:15`
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
