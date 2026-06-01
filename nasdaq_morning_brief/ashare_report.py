from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path
from subprocess import CalledProcessError
from urllib.error import HTTPError, URLError

from .ashare import build_ashare_snapshot
from .config import load_config
from .feishu import push_image_to_feishu
from .models import AShareSnapshot
from .render import write_ashare_snapshot_html, write_png


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

    try:
        output_path = write_ashare_snapshot_html(ashare, config, date.today().isoformat())
        if output_path is None:
            raise SystemExit("A-share module is disabled in config.")
        png_path = write_png(output_path, config, "ashare.png")
    except CalledProcessError as exc:
        raise SystemExit(f"Failed to render A-share PNG screenshot: {exc}")

    if args.no_push:
        print(f"A-share HTML written to {output_path}")
        print(f"A-share PNG written to {png_path}")
        print("Push skipped: yes")
        return 0

    feishu_pushed = False
    if config.push.feishu_webhook:
        feishu_pushed = push_image_to_feishu(
            config.push.feishu_webhook,
            config.push.feishu_secret,
            png_path,
            config.push.feishu_app_id,
            config.push.feishu_app_secret,
        )
    print(f"A-share HTML written to {output_path}")
    print(f"A-share PNG written to {png_path}")
    print(f"Feishu pushed: {'yes' if feishu_pushed else 'no'}")
    if args.require_push and not feishu_pushed:
        raise SystemExit("Push required but Feishu push did not succeed. Check repository secrets.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
