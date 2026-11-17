import unittest
from datetime import date, datetime, timezone

from nasdaq_morning_brief.discovery import NewsItem, _build_event


class DiscoveryTests(unittest.TestCase):
    def test_detects_major_ceo_departure_as_governance_event(self):
        item = NewsItem(
            title="Nvidia CEO Jensen Huang resigns and board names interim CEO",
            link="https://example.com/nvidia-ceo-transition",
            source="Reuters",
            published_at=datetime(2026, 11, 12, 12, tzinfo=timezone.utc),
        )

        event = _build_event(item, date(2026, 11, 12))

        self.assertIsNotNone(event)
        self.assertEqual(event.category, "公司治理")
        self.assertEqual(event.status, "confirmed")
        self.assertEqual(event.event_date, date(2026, 11, 12))

    def test_resolves_weekday_for_ipo_and_makes_it_confirmed(self):
        item = NewsItem(
            title="Anthropic is expected to price its U.S. IPO on Wednesday this week",
            link="https://example.com/anthropic-ipo",
            source="Axios",
            published_at=datetime(2026, 11, 16, tzinfo=timezone.utc),
        )

        event = _build_event(item, date(2026, 11, 16))

        self.assertIsNotNone(event)
        self.assertEqual(event.category, "IPO")
        self.assertEqual(event.status, "confirmed")
        self.assertEqual(event.event_date, date(2026, 11, 18))


if __name__ == "__main__":
    unittest.main()
