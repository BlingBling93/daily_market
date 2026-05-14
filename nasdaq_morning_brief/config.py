from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional


@dataclass
class PortfolioConfig:
    instrument: str
    current_allocation: float
    min_allocation: float
    max_allocation: float
    step_allocation: float
    risk_profile: str


@dataclass
class ValuationBand:
    low: float
    high: float


@dataclass
class ValuationConfig:
    trailing_pe: Optional[float]
    forward_pe: Optional[float]
    trailing_pe_band: ValuationBand
    forward_pe_band: ValuationBand


@dataclass
class RenderConfig:
    output_dir: Path
    title: str
    node_bin: Path
    node_modules: Path
    chrome_path: Path
    viewport_width: int
    viewport_height: int


@dataclass
class PushConfig:
    wecom_webhook: Optional[str]
    feishu_webhook: Optional[str]
    feishu_secret: Optional[str]
    feishu_app_id: Optional[str]
    feishu_app_secret: Optional[str]


@dataclass
class SignalConfig:
    hot_temperature: int
    cold_temperature: int
    high_vxn: float
    low_vxn: float


@dataclass
class AppConfig:
    portfolio: PortfolioConfig
    valuation: ValuationConfig
    render: RenderConfig
    push: PushConfig
    signals: SignalConfig


def _load_yaml(path: Path) -> Dict[str, Any]:
    try:
        import yaml  # type: ignore
    except ModuleNotFoundError:
        return _load_simple_yaml(path)

    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def _parse_scalar(value: str) -> Any:
    if value == "null":
        return None
    if value in {"true", "false"}:
        return value == "true"
    try:
        if "." in value:
            return float(value)
        return int(value)
    except ValueError:
        return value


def _load_simple_yaml(path: Path) -> Dict[str, Any]:
    root: Dict[str, Any] = {}
    stack: list[tuple[int, Dict[str, Any]]] = [(-1, root)]

    with path.open("r", encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.rstrip()
            if not line or line.lstrip().startswith("#"):
                continue
            indent = len(line) - len(line.lstrip(" "))
            key, _, raw_value = line.strip().partition(":")
            value = raw_value.strip()

            while stack and indent <= stack[-1][0]:
                stack.pop()
            parent = stack[-1][1]

            if value == "":
                node: Dict[str, Any] = {}
                parent[key] = node
                stack.append((indent, node))
            else:
                parent[key] = _parse_scalar(value)

    return root


def _env(name: str, fallback: Optional[str]) -> Optional[str]:
    value = os.environ.get(name)
    if value is None or value == "":
        return fallback
    return value


def _env_path(name: str, fallback: str) -> Path:
    value = _env(name, fallback) or fallback
    if value == "auto":
        return Path("")
    return Path(value)


def _env_int(name: str, fallback: int) -> int:
    value = os.environ.get(name)
    if value is None or value == "":
        return fallback
    return int(value)


def load_config(path: str) -> AppConfig:
    raw = _load_yaml(Path(path))

    portfolio = raw["portfolio"]
    valuation = raw["valuation"]
    render = raw["render"]
    push = raw.get("push", {})
    signals = raw["signals"]

    return AppConfig(
        portfolio=PortfolioConfig(**portfolio),
        valuation=ValuationConfig(
            trailing_pe=valuation.get("trailing_pe"),
            forward_pe=valuation.get("forward_pe"),
            trailing_pe_band=ValuationBand(**valuation["trailing_pe_band"]),
            forward_pe_band=ValuationBand(**valuation["forward_pe_band"]),
        ),
        render=RenderConfig(
            output_dir=Path(render["output_dir"]),
            title=render["title"],
            node_bin=_env_path("NODE_BIN", render["node_bin"]),
            node_modules=_env_path("NODE_MODULES", render["node_modules"]),
            chrome_path=_env_path("CHROME_PATH", render["chrome_path"]),
            viewport_width=_env_int("VIEWPORT_WIDTH", render["viewport_width"]),
            viewport_height=_env_int("VIEWPORT_HEIGHT", render["viewport_height"]),
        ),
        push=PushConfig(
            wecom_webhook=_env("WECOM_WEBHOOK", push.get("wecom_webhook")),
            feishu_webhook=_env("FEISHU_WEBHOOK", push.get("feishu_webhook")),
            feishu_secret=_env("FEISHU_SECRET", push.get("feishu_secret")),
            feishu_app_id=_env("FEISHU_APP_ID", push.get("feishu_app_id")),
            feishu_app_secret=_env("FEISHU_APP_SECRET", push.get("feishu_app_secret")),
        ),
        signals=SignalConfig(**signals),
    )
