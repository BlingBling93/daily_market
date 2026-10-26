#!/bin/zsh
set -euo pipefail

cd "/Users/blingbili/Documents/New project 2"
mkdir -p output
# Run from the local Chinese-network host so Eastmoney's current-session
# minute flow remains the primary source. The report enforces its own
# same-day and 80%-coverage gate before sending any ETF flow card.
export ETF_FLOW_PRIMARY=eastmoney
python3 -B -m nasdaq_morning_brief.ashare_report --config config.yaml --require-push
