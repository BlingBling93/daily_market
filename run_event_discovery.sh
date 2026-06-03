#!/bin/zsh
set -euo pipefail

cd "/Users/blingbili/Documents/New project 2"
python3 -B -m nasdaq_morning_brief.discovery --config config.yaml
