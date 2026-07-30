"""Refresh persisted news-content results for events already in the policy calendar."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta

from .config import load_config
from .policy import US_EASTERN, load_policy_calendar


def refresh_calendar_event_results(config_path: str) -> tuple[int, int]:
    """Capture results only for merged calendar events and persist them by event ID.

    ``load_policy_calendar`` merges the manual, automatic, and discovered
    calendars, de-duplicates their event keys, and then writes result entries
    to ``policy_event_news_cache.json``. This task deliberately does not run
    the free-form event discovery logic.
    """
    config = load_config(config_path).policy
    as_of = datetime.now(US_EASTERN).date()
    events = load_policy_calendar(config, as_of, refresh_results=True)
    eligible = [
        event
        for event in events
        if event.event_date <= as_of <= event.event_date + timedelta(days=max(event.impact_days, 7))
    ]
    captured = [event for event in eligible if event.result_summary]
    return len(eligible), len(captured)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Refresh result cache for calendar events.")
    parser.add_argument("--config", default="config.yaml", help="Path to config yaml.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    eligible, captured = refresh_calendar_event_results(args.config)
    print(f"Calendar events checked: {eligible}; content results available: {captured}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
