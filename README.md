# Nasdaq Morning Brief

Lightweight daily Nasdaq 100 brief generator for a single passive index position.

## What it does

- Pulls market data for `QQQ`, `^NDX`, `^VIX`, and `^VXN`
- Estimates short-term market temperature from price trend and volatility
- Ranks Nasdaq 100 sectors / themes by short-term heat
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
- output directory

## Notes

- Index valuation sources are uneven on free endpoints, so the first version supports manual override in config.
- Theme heat is computed from a maintained Nasdaq-oriented theme basket plus 1d / 5d / 20d returns.

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
- Schedule: every day at `08:50`
- Method: `POST`
- URL: `https://api.github.com/repos/BlingBling93/daily_market/dispatches`
- Headers:
  - `Accept: application/vnd.github+json`
  - `Authorization: Bearer <YOUR_GITHUB_PAT>`
  - `X-GitHub-Api-Version: 2022-11-28`
  - `Content-Type: application/json`
- Body:

  ```json
  {"event_type":"morning-brief"}
  ```

Create `<YOUR_GITHUB_PAT>` as a GitHub fine-grained personal access token scoped
only to this repository, with `Contents: Read and write` permission.
