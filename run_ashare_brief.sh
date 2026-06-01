#!/bin/zsh
set -euo pipefail

cd "/Users/blingbili/Documents/New project 2"
mkdir -p output
python3 -B -m nasdaq_morning_brief.ashare_report --config config.yaml --no-push
