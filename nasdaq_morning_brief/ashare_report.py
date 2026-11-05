from __future__ import annotations

import argparse
import csv
import json
import time
from datetime import date
from pathlib import Path
from subprocess import CalledProcessError
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .ashare import build_ashare_snapshot
from .config import load_config
from .feishu import push_image_to_feishu, push_text_to_feishu
from .models import AShareSnapshot
from .render import write_ashare_snapshot_html, write_etf_sector_flow_html, write_png


ETF_FLOW_MIN_COVERAGE = 0.80
ETF_DAILY_FLOW_URL = "https://push2his.eastmoney.com/api/qt/stock/fflow/kline/get"


def _fetch_etf_daily_history(theme_path: Path, count: int = 3) -> list[dict]:
    """Fetch recent daily main-flow values for every ETF in the configured pool."""
    with theme_path.open(encoding="utf-8-sig", newline="") as handle:
        universe = list(csv.DictReader(handle))
    results: list[dict] = []
    for index, item in enumerate(universe):
        ticker = str(item.get("proxy_ticker", "")).strip()
        row = {
            "theme": item.get("theme", ""),
            "ticker": ticker,
            "name": item.get("proxy_name", ""),
            "days": [],
        }
        if ticker:
            market = "1" if ticker.startswith(("5", "6", "9")) else "0"
            params = {
                "lmt": "10",
                "klt": "101",
                "secid": f"{market}.{ticker}",
                "fields1": "f1,f2,f3,f7",
                "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65",
                "ut": "b2884a393a59ad64002292a3e90d46a5", "_": int(time.time() * 1000),
            }
            for attempt in range(2):
                try:
                    request = Request(
                        f"{ETF_DAILY_FLOW_URL}?{urlencode(params)}",
                        headers={"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/"},
                    )
                    with urlopen(request, timeout=20) as response:
                        data = json.loads(response.read().decode("utf-8"))
                    raw_rows = (((data.get("data") or {}).get("klines")) or [])
                    parsed = []
                    for raw in raw_rows:
                        parts = str(raw).split(",")
                        if len(parts) >= 2:
                            try:
                                parsed.append({"date": parts[0][:10], "amount": float(parts[1])})
                            except ValueError:
                                pass
                    row["days"] = parsed[-count:]
                    if row["days"]:
                        row.pop("error", None)
                        break
                    row["error"] = "empty daily-flow response"
                except Exception as exc:
                    row["error"] = f"{type(exc).__name__}: {exc}"
                    if attempt == 0:
                        time.sleep(1.2)
            if index < len(universe) - 1:
                time.sleep(0.35)
        results.append(row)
    dates = sorted({day["date"] for row in results for day in row["days"]})[-count:]
    for row in results:
        by_date = {day["date"]: day["amount"] for day in row["days"]}
        row["days"] = [{"date": day, "amount": by_date.get(day)} for day in dates]
        vals = [day["amount"] for day in row["days"]]
        row["streak"] = "连续3日净流入" if len(vals) == count and all(v is not None and v > 0 for v in vals) else (
            "连续3日净流出" if len(vals) == count and all(v is not None and v < 0 for v in vals) else ""
        )
    return results
def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate A-share active research brief.")
    parser.add_argument("--config", default="config.yaml", help="Path to config yaml.")
    parser.add_argument(
        "--market-days-ago",
        type=int,
        default=0,
        help="Use a prior market session. 1 means yesterday's market session.",
    )
    parser.add_argument("--no-push", action="store_true", help="Generate HTML/PNG without pushing.")
    parser.add_argument(
        "--skip-heat-history",
        action="store_true",
        help="Do not append A-share ETF direction heat history during this run.",
    )
    parser.add_argument(
        "--require-push",
        action="store_true",
        help="Exit with an error if no image push succeeds.",
    )
    parser.add_argument(
        "--send-latest-image",
        default=None,
        help="Skip data generation and send an existing PNG image to Feishu.",
    )
    return parser.parse_args()


def build_report(
    config_path: str,
    market_days_ago: int = 0,
    write_heat_history: bool = True,
) -> AShareSnapshot:
    config = load_config(config_path)
    return build_ashare_snapshot(
        config.ashare,
        market_days_ago=market_days_ago,
        write_heat_history=write_heat_history,
    )


def main() -> int:
    args = parse_args()
    config = load_config(args.config)

    if args.send_latest_image:
        image_path = Path(args.send_latest_image)
        pushed = push_image_to_feishu(
            config.push.feishu_webhook,
            config.push.feishu_secret,
            image_path,
            config.push.feishu_app_id,
            config.push.feishu_app_secret,
        )
        print(f"Feishu image pushed: {'yes' if pushed else 'no'}")
        if args.require_push and not pushed:
            raise SystemExit("Push required but Feishu push did not succeed. Check repository secrets.")
        return 0

    try:
        ashare = build_report(
            args.config,
            market_days_ago=args.market_days_ago,
            write_heat_history=not args.skip_heat_history,
        )
    except (HTTPError, URLError, ValueError) as exc:
        raise SystemExit(f"Failed to build A-share report from live market data: {exc}")

    as_of = date.today().isoformat()
    try:
        output_path = write_ashare_snapshot_html(ashare, config, as_of)
        if output_path is None:
            raise SystemExit("A-share module is disabled in config.")
        png_path = write_png(output_path, config, "ashare.png")
    except (CalledProcessError, OSError, RuntimeError) as exc:
        raise SystemExit(f"Failed to render A-share PNG screenshot: {exc}")

    feishu_pushed = False
    if config.push.feishu_webhook and not args.no_push:
        try:
            feishu_pushed = push_image_to_feishu(
                config.push.feishu_webhook, config.push.feishu_secret, png_path,
                config.push.feishu_app_id, config.push.feishu_app_secret,
            )
        except Exception as exc:
            print(f"A-share Feishu push failed: {type(exc).__name__}: {exc}")
    print(f"A-share HTML written to {output_path}")
    print(f"A-share PNG written to {png_path}")
    print(f"A-share Feishu pushed: {'yes' if feishu_pushed else 'no'}")

    histories: list[dict] = []
    flow_output_path = flow_png_path = None
    try:
        histories = _fetch_etf_daily_history(config.ashare.theme_etf_path)
        flow_output_path = write_etf_sector_flow_html(ashare, config, as_of, histories)
        if flow_output_path is None:
            raise RuntimeError("ETF daily-flow card is disabled")
        successful = sum(len(item.get("days", [])) == 3 and all(day.get("amount") is not None for day in item["days"]) for item in histories)
        total = len(histories)
        coverage = successful / total if total else 0.0
        print(f"ETF sector-flow HTML written to {flow_output_path}")
        print(f"ETF daily-flow coverage: {successful}/{total} ({coverage:.0%})" if total else "ETF daily-flow coverage: 0/0")
        if coverage < ETF_FLOW_MIN_COVERAGE:
            print(f"ETF flow card skipped: coverage below {ETF_FLOW_MIN_COVERAGE:.0%}")
            error_rows = [item for item in histories if item.get("error")]
            if error_rows:
                print(f"ETF daily-flow errors ({len(error_rows)} ETFs): {error_rows[0]['error']}")
            if config.push.feishu_webhook and not args.no_push:
                try:
                    detail = f"；采集错误示例：{error_rows[0]['error']}" if error_rows else ""
                    push_text_to_feishu(
                        config.push.feishu_webhook,
                        config.push.feishu_secret,
                        f"ETF日线资金流数据质量告警：最近三日完整数据 {successful}/{total}（{coverage:.0%}），低于 {ETF_FLOW_MIN_COVERAGE:.0%}，资金流图未发送{detail}。",
                    )
                except Exception as exc:
                    print(f"ETF flow quality alert failed: {type(exc).__name__}: {exc}")
        else:
            flow_png_path = write_png(flow_output_path, config, "etf_sector_flow.png")
            print(f"ETF sector-flow PNG written to {flow_png_path}")
        if flow_png_path and config.push.feishu_webhook and not args.no_push:
            try:
                flow_pushed = push_image_to_feishu(
                    config.push.feishu_webhook, config.push.feishu_secret, flow_png_path,
                    config.push.feishu_app_id, config.push.feishu_app_secret,
                )
            except Exception as exc:
                flow_pushed = False
                print(f"ETF flow Feishu push failed: {type(exc).__name__}: {exc}")
            print(f"ETF flow card pushed: {'yes' if flow_pushed else 'no'}")
    except Exception as exc:
        print(f"ETF daily-flow report failed; A-share report remains available: {type(exc).__name__}: {exc}")

    if args.no_push:
        print("Push skipped: yes")
        return 0

    if args.require_push and not feishu_pushed:
        raise SystemExit("Push required but Feishu push did not succeed. Check repository secrets.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
