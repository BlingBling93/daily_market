import unittest
from unittest.mock import patch

from nasdaq_morning_brief import ashare


class ETFIntradayFlowTests(unittest.TestCase):
    @staticmethod
    def _theme(theme, ticker):
        return ashare.ThemeCandidate(
            theme=theme,
            proxy_ticker=ticker,
            proxy_name=f"{theme}ETF",
            style="test",
            risk_level="low",
            max_theme_weight=0.1,
            market=None,
            score=50,
            action="观察",
            etf_action="持有观察",
            rationale="test",
        )

    @patch("nasdaq_morning_brief.ashare.time.sleep")
    @patch("nasdaq_morning_brief.ashare._fetch_json")
    def test_retries_empty_minute_flow_before_returning_data(self, fetch_json, sleep):
        today = ashare.date.today().isoformat()
        fetch_json.side_effect = [
            {"data": {"klines": []}},
            {"data": {"klines": []}},
            {"data": {"name": "测试ETF", "klines": [f"{today} 13:50,123456"]}},
        ]

        flow = ashare._fetch_eastmoney_etf_intraday_flow("512890")

        self.assertIsNotNone(flow)
        self.assertEqual(flow.main_net_amount, 123456.0)
        self.assertEqual(fetch_json.call_count, 3)
        self.assertEqual(sleep.call_count, 2)

    @patch("nasdaq_morning_brief.ashare.time.sleep")
    @patch("nasdaq_morning_brief.ashare._fetch_json")
    def test_retries_request_error_before_returning_data(self, fetch_json, sleep):
        today = ashare.date.today().isoformat()
        fetch_json.side_effect = [
            ValueError("Eastmoney request failed: timed out"),
            {"data": {"name": "测试ETF", "klines": [f"{today} 13:50,123456"]}},
        ]

        flow = ashare._fetch_eastmoney_etf_intraday_flow("512890")

        self.assertIsNotNone(flow)
        self.assertEqual(fetch_json.call_count, 2)
        sleep.assert_called_once_with(0.8)

    @patch("nasdaq_morning_brief.ashare.time.sleep")
    @patch("nasdaq_morning_brief.ashare._fetch_json")
    def test_rejects_stale_data_without_retrying(self, fetch_json, sleep):
        fetch_json.return_value = {
            "data": {"klines": ["2026-10-21 15:00,123456"]}
        }

        flow = ashare._fetch_eastmoney_etf_intraday_flow("512890")

        self.assertIsNone(flow)
        fetch_json.assert_called_once()
        sleep.assert_not_called()

    @patch("nasdaq_morning_brief.ashare._fetch_text")
    def test_uses_sina_main_net_flow_as_primary_data(self, fetch_text):
        fetch_text.return_value = (
            '[{"opendate":"2026-10-23","r0_net":"12345678.9","r0_ratio":"0.12"}]'
        )

        flow = ashare._fetch_sina_etf_flow("512890")

        self.assertIsNotNone(flow)
        self.assertEqual(flow.main_net_amount, 12345678.9)
        self.assertEqual(flow.updated_at, "2026-10-23")
        self.assertEqual(flow.source, "新浪财经资金流向")

    @patch("nasdaq_morning_brief.ashare._fetch_eastmoney_etf_intraday_flow")
    @patch("nasdaq_morning_brief.ashare._fetch_sina_etf_flow")
    def test_uses_eastmoney_only_when_sina_is_unavailable(self, fetch_sina, fetch_eastmoney):
        sina_flow = ashare.ETFThemeFlow("", "512890", "", 1.0, None, "2026-10-23", "新浪财经资金流向")
        fetch_sina.return_value = sina_flow

        flow = ashare._fetch_etf_intraday_flow("512890")

        self.assertIs(flow, sina_flow)
        fetch_eastmoney.assert_not_called()

    @patch("nasdaq_morning_brief.ashare.time.sleep")
    @patch("nasdaq_morning_brief.ashare._fetch_etf_intraday_flow")
    def test_paces_live_flow_requests_across_the_batch(self, fetch_flow, sleep):
        fetch_flow.side_effect = [
            ashare.ETFThemeFlow("", "512890", "", 1.0, None, "", ""),
            ashare.ETFThemeFlow("", "512800", "", 2.0, None, "", ""),
        ]

        flows = ashare._build_sector_flows(
            [self._theme("红利低波", "512890"), self._theme("银行", "512800")],
            market_days_ago=0,
        )

        self.assertEqual([flow.main_net_amount for flow in flows], [1.0, 2.0])
        self.assertEqual(fetch_flow.call_args_list[0].args, ("512890",))
        self.assertEqual(fetch_flow.call_args_list[1].args, ("512800",))
        sleep.assert_called_once_with(ashare.ETF_INTRADAY_FLOW_REQUEST_INTERVAL_SECONDS)


if __name__ == "__main__":
    unittest.main()
