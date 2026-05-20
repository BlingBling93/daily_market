from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import uuid
from pathlib import Path
from typing import Optional
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from .models import Brief
from .wechat import build_text_summary


TENANT_TOKEN_URL = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
IMAGE_UPLOAD_URL = "https://open.feishu.cn/open-apis/im/v1/images"


def _post_json(url: str, payload: dict, headers: Optional[dict] = None) -> dict:
    request_headers = {"Content-Type": "application/json"}
    if headers:
        request_headers.update(headers)
    request = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=request_headers,
        method="POST",
    )
    with urlopen(request, timeout=20) as response:
        body = response.read().decode("utf-8")
        if response.status >= 400:
            raise RuntimeError(f"Feishu request failed with status {response.status}: {body}")
        data = json.loads(body or "{}")
    if data.get("code", 0) not in (0, None):
        raise RuntimeError(f"Feishu API returned error: {data}")
    return data


def _sign(secret: str, timestamp: int) -> str:
    string_to_sign = f"{timestamp}\n{secret}".encode("utf-8")
    digest = hmac.new(string_to_sign, b"", hashlib.sha256).digest()
    return base64.b64encode(digest).decode("utf-8")


def _with_webhook_auth(payload: dict, secret: Optional[str]) -> dict:
    if not secret:
        return payload
    timestamp = int(time.time())
    return {
        **payload,
        "timestamp": str(timestamp),
        "sign": _sign(secret, timestamp),
    }


def _get_tenant_access_token(app_id: str, app_secret: str) -> str:
    data = _post_json(
        TENANT_TOKEN_URL,
        {
            "app_id": app_id,
            "app_secret": app_secret,
        },
    )
    token = data.get("tenant_access_token")
    if not token:
        raise RuntimeError(f"Feishu tenant access token missing: {data}")
    return token


def _build_multipart_image(image_path: Path) -> tuple[bytes, str]:
    boundary = f"----codex-{uuid.uuid4().hex}"
    image_bytes = image_path.read_bytes()
    parts = [
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="image_type"\r\n\r\n'
        "message\r\n",
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="image"; filename="{image_path.name}"\r\n'
        "Content-Type: image/png\r\n\r\n",
    ]
    body = "".join(parts).encode("utf-8") + image_bytes + f"\r\n--{boundary}--\r\n".encode("utf-8")
    return body, boundary


def _upload_image(image_path: Path, app_id: str, app_secret: str) -> str:
    token = _get_tenant_access_token(app_id, app_secret)
    body, boundary = _build_multipart_image(image_path)
    request = Request(
        IMAGE_UPLOAD_URL,
        data=body,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=30) as response:
            response_body = response.read().decode("utf-8")
            if response.status >= 400:
                raise RuntimeError(f"Feishu image upload failed with status {response.status}: {response_body}")
            data = json.loads(response_body or "{}")
    except HTTPError as exc:
        error_body = exc.read().decode("utf-8", "ignore")
        raise RuntimeError(f"Feishu image upload failed with status {exc.code}: {error_body}") from exc
    if data.get("code") != 0:
        raise RuntimeError(f"Feishu image upload returned error: {data}")
    image_key = data.get("data", {}).get("image_key")
    if not image_key:
        raise RuntimeError(f"Feishu image key missing: {data}")
    return image_key


def _send_webhook(webhook: str, payload: dict, secret: Optional[str]) -> None:
    _post_json(webhook, _with_webhook_auth(payload, secret))


def push_to_feishu(
    webhook: Optional[str],
    secret: Optional[str],
    brief: Brief,
    image_path: Optional[Path] = None,
    app_id: Optional[str] = None,
    app_secret: Optional[str] = None,
) -> bool:
    if not webhook:
        return False

    if image_path and image_path.exists() and app_id and app_secret:
        try:
            image_key = _upload_image(image_path, app_id, app_secret)
            _send_webhook(
                webhook,
                {
                    "msg_type": "image",
                    "content": {"image_key": image_key},
                },
                secret,
            )
            return True
        except RuntimeError as exc:
            print(f"Feishu image push skipped: {exc}")

    _send_webhook(
        webhook,
        {
            "msg_type": "text",
            "content": {"text": build_text_summary(brief)},
        },
        secret,
    )
    return True


def push_images_to_feishu(
    webhook: Optional[str],
    secret: Optional[str],
    image_paths: list[Path],
    app_id: Optional[str],
    app_secret: Optional[str],
) -> bool:
    if not webhook:
        return False
    if not image_paths:
        return False
    if not app_id or not app_secret:
        raise RuntimeError("Feishu app_id/app_secret are required for image push")
    missing = [image_path for image_path in image_paths if not image_path.exists()]
    if missing:
        raise FileNotFoundError(f"Image not found: {missing[0]}")

    sent_any = False
    for image_path in image_paths:
        image_key = _upload_image(image_path, app_id, app_secret)
        _send_webhook(
            webhook,
            {
                "msg_type": "image",
                "content": {"image_key": image_key},
            },
            secret,
        )
        sent_any = True
    return sent_any


def push_image_to_feishu(
    webhook: Optional[str],
    secret: Optional[str],
    image_path: Path,
    app_id: Optional[str],
    app_secret: Optional[str],
) -> bool:
    if not webhook:
        return False
    if not app_id or not app_secret:
        raise RuntimeError("Feishu app_id/app_secret are required for image-only push")
    if not image_path.exists():
        raise FileNotFoundError(f"Image not found: {image_path}")

    image_key = _upload_image(image_path, app_id, app_secret)
    _send_webhook(
        webhook,
        {
            "msg_type": "image",
            "content": {"image_key": image_key},
        },
        secret,
    )
    return True
