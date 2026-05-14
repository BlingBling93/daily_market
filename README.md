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

The schedule runs at `00:55 UTC`, which is `08:55 Asia/Shanghai`.
