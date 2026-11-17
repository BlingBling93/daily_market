import unittest
from datetime import date, datetime, timezone

from nasdaq_morning_brief.discovery import NewsItem, _build_event
from nasdaq_morning_brief.models import PolicyEvent
from nasdaq_morning_brief.policy import _combined_event_impacts


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


if __name__ == "__main__":
    unittest.main()
