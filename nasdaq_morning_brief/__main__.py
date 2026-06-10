from __future__ import annotations

import argparse
from pathlib import Path
from subprocess import CalledProcessError
from urllib.error import HTTPError, URLError

from .advice import apply_policy_adjustment, generate_advice
from .config import load_config
from .data import (
    fetch_auto_valuation,
    fetch_first_available_snapshot,
    fetch_quote_snapshot,
)
from .indicators import (
    build_cross_asset_notes,
    build_observation_points,
    compute_temperature,
    summarize_valuation,
)
from .feishu import push_image_to_feishu, push_images_to_feishu
from .models import Brief
from .policy import build_policy_snapshot
from .render import write_html, write_png
from .wechat import push_to_wecom


def _build_data_warnings(brief: Brief) -> list[str]:
    market_snapshots = [
        ("QQQ", brief.qqq),
        ("NDX", brief.ndx),
        ("VIX", brief.vix),
        ("VXN", brief.vxn),
        ("Gold", brief.gold),
        ("10Y", brief.us10y),
        ("Oil", brief.oil),
    ]
    warnings: list[str] = []
    unavailable_items = [
        label
        for label, snapshot in market_snapshots
        if snapshot.source.startswith("Unavailable")
    ]
    if unavailable_items:
        warnings.append("以下行情未能正确获取，报告已降级显示：" + "；".join(unavailable_items))

    fallback_items = [
        f"{label}={snapshot.source}"
        for label, snapshot in market_snapshots
        if snapshot.source != "Yahoo Finance" and not snapshot.source.startswith("Unavailable")
    ]
    if fallback_items:
        warnings.append("备用行情源已启用：" + "；".join(fallback_items))

    benchmark_as_of = brief.qqq.as_of
    stale_items = [
        f"{label} {snapshot.as_of.isoformat()}"
        for label, snapshot in market_snapshots[1:]
        if not snapshot.source.startswith("Unavailable") and snapshot.as_of < benchmark_as_of
    ]
    if stale_items:
        warnings.append(
            f"部分行情日期落后于 QQQ 基准日 {benchmark_as_of.isoformat()}："
            + "；".join(stale_items)
        )
    return warnings


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate Nasdaq morning brief.")
    parser.add_argument("--config", default="config.yaml", help="Path to config yaml.")
    parser.add_argument(
        "--market-days-ago",
        type=int,
        default=0,
        help="Use a prior market session. 1 means yesterday's market session.",
    )
    parser.add_argument("--no-push", action="store_true", help="Generate HTML/PNG without pushing.")
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


def build_brief(
    config_path: str,
    market_days_ago: int = 0,
) -> Brief:
    config = load_config(config_path)

    qqq = fetch_quote_snapshot("QQQ", market_days_ago=market_days_ago)
    ndx = fetch_quote_snapshot("^NDX", market_days_ago=market_days_ago)
    vix = fetch_quote_snapshot("^VIX", market_days_ago=market_days_ago)
    vxn = fetch_quote_snapshot("^VXN", market_days_ago=market_days_ago)
    gold = fetch_first_available_snapshot(["XAUUSD=X", "GC=F", "GLD"], market_days_ago=market_days_ago)
    us10y = fetch_first_available_snapshot(["^TNX"], market_days_ago=market_days_ago)
    oil = fetch_first_available_snapshot(["CL=F", "BZ=F", "USO"], market_days_ago=market_days_ago)
    try:
        auto_valuation = fetch_auto_valuation()
    except ValueError:
        auto_valuation = None
    valuation = summarize_valuation(config.valuation, auto_valuation)
    temperature = compute_temperature(qqq, vxn, vix, valuation, config.signals)
    cross_asset_notes = build_cross_asset_notes(gold, us10y, oil)
    advice = generate_advice(config.portfolio, qqq, vxn, temperature, valuation)
    policy = build_policy_snapshot(config.policy, qqq.as_of, advice, us10y, oil)
    advice = apply_policy_adjustment(config.portfolio, advice, policy, qqq.as_of)
    observation_points = build_observation_points(
        advice.triggers,
        temperature.rationale,
        cross_asset_notes,
    )

    brief = Brief(
        as_of=qqq.as_of,
        qqq=qqq,
        ndx=ndx,
        vix=vix,
        vxn=vxn,
        gold=gold,
        us10y=us10y,
        oil=oil,
        valuation=valuation,
        temperature=temperature,
        cross_asset_notes=cross_asset_notes,
        observation_points=observation_points,
        policy=policy,
        advice=advice,
        ashare=None,
    )
    brief.data_warnings = _build_data_warnings(brief)
    return brief


def main() -> int:
    args = parse_args()
    config = load_config(args.config)

    if args.send_latest_image:
        image_paths = [Path(item.strip()) for item in args.send_latest_image.split(",") if item.strip()]
        if len(image_paths) > 1:
            pushed = push_images_to_feishu(
                config.push.feishu_webhook,
                config.push.feishu_secret,
                image_paths,
                config.push.feishu_app_id,
                config.push.feishu_app_secret,
            )
        else:
            pushed = push_image_to_feishu(
                config.push.feishu_webhook,
                config.push.feishu_secret,
                image_paths[0],
                config.push.feishu_app_id,
                config.push.feishu_app_secret,
            )
        print(f"Feishu image pushed: {'yes' if pushed else 'no'}")
        return 0

    try:
        brief = build_brief(
            args.config,
            market_days_ago=args.market_days_ago,
        )
    except (HTTPError, URLError, ValueError) as exc:
        raise SystemExit(f"Failed to build brief from live market data: {exc}")
    output_path = write_html(brief, config)
    try:
        png_path = write_png(output_path, config, "brief.png")
    except CalledProcessError as exc:
        raise SystemExit(f"Failed to render PNG screenshot: {exc}")

    if args.no_push:
        print(f"HTML brief written to {output_path}")
        print(f"PNG brief written to {png_path}")
        print("Push skipped: yes")
        return 0

    feishu_pushed = False
    if config.push.feishu_webhook:
        feishu_pushed = push_images_to_feishu(
            config.push.feishu_webhook,
            config.push.feishu_secret,
            [png_path],
            config.push.feishu_app_id,
            config.push.feishu_app_secret,
        )
    wecom_pushed = False
    if not feishu_pushed:
        wecom_pushed = push_to_wecom(config.push.wecom_webhook, brief, image_path=png_path)
    print(f"HTML brief written to {output_path}")
    print(f"PNG brief written to {png_path}")
    print(f"Feishu pushed: {'yes' if feishu_pushed else 'no'}")
    print(f"WeCom pushed: {'yes' if wecom_pushed else 'no'}")
    if args.require_push and not (feishu_pushed or wecom_pushed):
        raise SystemExit("Push required but no Feishu/WeCom push succeeded. Check repository secrets.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
