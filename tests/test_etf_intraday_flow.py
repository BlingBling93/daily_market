import unittest
from unittest.mock import patch

from nasdaq_morning_brief import ashare


class ETFIntradayFlowTests(unittest.TestCase):
    @patch("nasdaq_morning_brief.ashare.time.sleep")
    @patch("nasdaq_morning_brief.ashare._fetch_json")
    def test_retries_empty_minute_flow_before_returning_data(self, fetch_json, sleep):
        today = ashare.date.today().isoformat()
        fetch_json.side_effect = [
            {"data": {"klines": []}},
            {"data": {"klines": []}},
            {"data": {"name": "测试ETF", "klines": [f"{today} 13:50,123456"]}},
        ]

        flow = ashare._fetch_etf_intraday_flow("512890")

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

        flow = ashare._fetch_etf_intraday_flow("512890")

        self.assertIsNotNone(flow)
        self.assertEqual(fetch_json.call_count, 2)
        sleep.assert_called_once_with(0.8)

    @patch("nasdaq_morning_brief.ashare.time.sleep")
    @patch("nasdaq_morning_brief.ashare._fetch_json")
    def test_rejects_stale_data_without_retrying(self, fetch_json, sleep):
        fetch_json.return_value = {
            "data": {"klines": ["2026-10-21 15:00,123456"]}
        }

        flow = ashare._fetch_etf_intraday_flow("512890")

        self.assertIsNone(flow)
        fetch_json.assert_called_once()
        sleep.assert_not_called()


if __name__ == "__main__":
    unittest.main()
