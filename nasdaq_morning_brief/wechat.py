from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
from typing import Optional
from urllib.request import Request, urlopen

from .models import Brief


def build_text_summary(brief: Brief) -> str:
    hot = ", ".join(item.theme for item in brief.hot_themes)
    us10y_value = brief.us10y.price / 10.0 if brief.us10y.price > 20 else brief.us10y.price
    cross_asset_summary = "；".join(brief.cross_asset_notes[:2])
    return (
        f"Nasdaq 100晨报 {brief.as_of.isoformat()}\n"
        f"市场温度：{brief.temperature.score}（{brief.temperature.label}）\n"
        f"QQQ：{brief.qqq.price:.2f}（{brief.qqq.day_change_pct:+.2f}%）\n"
        f"黄金：{brief.gold.price:.2f}（{brief.gold.day_change_pct:+.2f}%）\n"
        f"美债10年：{us10y_value:.2f}%（{brief.us10y.day_change_pct:+.2f}%）\n"
        f"原油：{brief.oil.price:.2f}（{brief.oil.day_change_pct:+.2f}%）\n"
        f"VXN：{brief.vxn.price:.2f}\n"
        f"热度方向：{hot}\n"
        f"跨资产结论：{cross_asset_summary}\n"
        f"建议：{brief.advice.summary}"
    )


def _post_json(webhook: str, payload: dict) -> None:
    request = Request(
        webhook,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=15) as response:
        if response.status >= 400:
            raise RuntimeError(f"WeCom push failed with status {response.status}")


def _build_image_payload(image_path: Path) -> dict:
    image_bytes = image_path.read_bytes()
    image_base64 = base64.b64encode(image_bytes).decode("utf-8")
    image_md5 = hashlib.md5(image_bytes).hexdigest()
    return {
        "msgtype": "image",
        "image": {
            "base64": image_base64,
            "md5": image_md5,
        },
    }


def push_to_wecom(webhook: Optional[str], brief: Brief, image_path: Optional[Path] = None) -> bool:
    if not webhook:
        return False
    if image_path and image_path.exists():
        _post_json(webhook, _build_image_payload(image_path))

    payload = {
        "msgtype": "text",
        "text": {"content": build_text_summary(brief)},
    }
    _post_json(webhook, payload)
    return True
