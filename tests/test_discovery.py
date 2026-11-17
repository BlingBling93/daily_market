import unittest
from datetime import date, datetime, timezone
from unittest.mock import patch

from nasdaq_morning_brief.discovery import NewsItem, _build_event
from nasdaq_morning_brief.models import PolicyEvent
from nasdaq_morning_brief.policy import _combined_event_impacts, _fetch_event_result, _news_queries


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

    def test_rebalance_guidance_combines_realised_and_pending_events(self):
        realised = PolicyEvent(
            event_date=date(2026, 11, 16), category="美联储讲话", title="美联储发言",
            stance="偏谨慎", summary="事前观察利率路径", short_term="讲话前不要追价",
            mid_term="观察利率路径", long_term="观察政策框架", impact_days=2,
            result_conclusion="讲话偏鹰，利率预期上修。", result_market_impact="10年美债收益率上行，科技股承压。",
        )
        pending = PolicyEvent(
            event_date=date(2026, 11, 20), category="IPO", title="Anthropic IPO",
            stance="事件待确认", summary="拟在纳斯达克上市", short_term="定价前分批执行",
            mid_term="观察首日成交", long_term="观察风险偏好", impact_days=5,
        )

        short, mid, long = _combined_event_impacts([pending], [realised], date(2026, 11, 17))

        self.assertIn("讲话偏鹰", short)
        self.assertIn("重点IPO事件还有3天", short)
        self.assertIn("已落地事件", mid)
        self.assertIn("待发生事件", long)

    def test_fed_speech_queries_use_speaker_alias_instead_of_full_english_title(self):
        event = PolicyEvent(
            event_date=date(2026, 11, 16), category="美联储讲话", title="Fed讲话：Warsh, Opening Remarks",
            stance="待确认", summary="", short_term="", mid_term="", long_term="", impact_days=2,
        )

        queries = _news_queries(event)

        self.assertTrue(any("沃什" in query for query in queries))
        self.assertFalse(any("Opening Remarks" in query for query in queries))

    @patch("nasdaq_morning_brief.policy.capture_event_result_evidence")
    @patch("nasdaq_morning_brief.policy._fetch_direct_event_result")
    def test_official_result_is_used_before_media_search(self, fetch_direct, capture_media):
        event = PolicyEvent(
            event_date=date(2026, 11, 16), category="美联储讲话", title="Fed讲话：Warsh, Opening Remarks",
            stance="待确认", summary="", short_term="", mid_term="", long_term="", impact_days=2,
        )
        fetch_direct.return_value = {"summary": "美联储官网讲稿", "conclusion": "官方结论"}

        result = _fetch_event_result(event)

        self.assertEqual(result["conclusion"], "官方结论")
        capture_media.assert_not_called()


if __name__ == "__main__":
    unittest.main()
