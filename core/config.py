"""Unified configuration management."""
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


def _read_env_file() -> dict:
    """Read key=value pairs from .env file."""
    env_path = Path(__file__).parent.parent / ".env"
    values = {}
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                values[k.strip()] = v.strip()
    return values

DEFAULT_CONFIG = {
    # Variational
    "var_url": "https://omni.variational.io/api/quotes/indicative",
    "var_ws_url": "wss://omni-ws-server.prod.ap-northeast-1.variational.io/portfolio",
    "var_http_proxy": "http://127.0.0.1:7897",
    "var_cookie": "",
    "var_headers": {
        "user-agent": "Mozilla/5.0",
        "origin": "https://omni.variational.io",
        "referer": "https://omni.variational.io/perpetual/ETH",
        "accept-language": "zh-CN,zh;q=0.9,en;q=0.8",
        "accept": "*/*",
    },
    # Paradex
    "paradex_ws_url": "wss://ws.api.prod.paradex.trade/v1",
    "paradex_subscribe_template": {
        "jsonrpc": "2.0",
        "method": "subscribe",
        "params": {"channel": "bbo.{market}"},
    },
    "paradex_unsubscribe_template": {
        "jsonrpc": "2.0",
        "method": "unsubscribe",
        "params": {"channel": "bbo.{market}"},
    },
    # Timing
    "poll_interval_s": 0.5,
    "ws_backoff_start": 1,
    "ws_backoff_max": 30,
    # Alerting
    "telegram_bot_token": "",
    "telegram_chat_id": "",
    "alert_cooldown_s": 30,
    "alert_threshold_net_qty": 0.01,
    "alert_threshold_single_pos_upnl": 3000.0,
    "alert_imbalance_count": 12,
    # Server
    "local_ws_port": 8789,
    "listening_server_port": 8001,
    # Trading Pairs
    "pairs": [
        {"symbol": "ETH-USD", "underlying": "ETH", "qty": "0.1", "paradex_market": "ETH-USD-PERP"},
        {"symbol": "BTC-USD", "underlying": "BTC", "qty": "0.01", "paradex_market": "BTC-USD-PERP"},
    ],
}


@dataclass
class Config:
    """Typed configuration object with all settings."""
    # Variational
    var_url: str = ""
    var_ws_url: str = ""
    var_http_proxy: str = ""
    var_cookie: str = ""
    var_headers: dict = field(default_factory=dict)
    # Paradex
    paradex_ws_url: str = ""
    paradex_subscribe_template: dict = field(default_factory=dict)
    paradex_unsubscribe_template: dict = field(default_factory=dict)
    # Timing
    poll_interval_s: float = 0.5
    ws_backoff_start: int = 1
    ws_backoff_max: int = 30
    # Alerting
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    alert_cooldown_s: int = 30
    alert_threshold_net_qty: float = 0.01
    alert_threshold_single_pos_upnl: float = 3000.0
    alert_imbalance_count: int = 12
    # Server
    local_ws_port: int = 8789
    listening_server_port: int = 8001
    # Pairs
    pairs: list = field(default_factory=list)
    # Raw dict access
    _raw: dict = field(default_factory=dict, repr=False)

    def get(self, key: str, default: Any = None) -> Any:
        return self._raw.get(key, default)

    def __getitem__(self, key: str) -> Any:
        return self._raw[key]


def load_config(config_path: Path | None = None) -> Config:
    """Load and merge user config with defaults."""
    if config_path is None:
        config_path = Path(__file__).parent.parent / "config.json"

    merged = dict(DEFAULT_CONFIG)

    if config_path.exists():
        try:
            with config_path.open("r", encoding="utf-8") as f:
                user_cfg = json.load(f)
            
            # Legacy key mapping
            if "var_http_url" in user_cfg and "var_url" not in user_cfg:
                user_cfg["var_url"] = user_cfg["var_http_url"]

            # Merge dicts
            merged.update(user_cfg)
            merged["var_headers"] = {**DEFAULT_CONFIG["var_headers"], **user_cfg.get("var_headers", {})}
            merged["pairs"] = user_cfg.get("pairs", DEFAULT_CONFIG["pairs"])
        except Exception as e:
            print(f"Config Load Error: {e}")

    # Override secrets from .env (takes priority over config.json)
    env_vals = _read_env_file()
    if env_vals.get("VAR_COOKIE"):
        merged["var_cookie"] = env_vals["VAR_COOKIE"]
    if env_vals.get("VAR_USER_AGENT"):
        merged["var_headers"]["user-agent"] = env_vals["VAR_USER_AGENT"]

    return Config(
        var_url=merged["var_url"],
        var_ws_url=merged.get("var_ws_url", ""),
        var_http_proxy=merged["var_http_proxy"],
        var_cookie=merged["var_cookie"],
        var_headers=merged["var_headers"],
        paradex_ws_url=merged["paradex_ws_url"],
        paradex_subscribe_template=merged["paradex_subscribe_template"],
        paradex_unsubscribe_template=merged["paradex_unsubscribe_template"],
        poll_interval_s=merged["poll_interval_s"],
        ws_backoff_start=merged["ws_backoff_start"],
        ws_backoff_max=merged["ws_backoff_max"],
        telegram_bot_token=merged.get("telegram_bot_token", ""),
        telegram_chat_id=merged.get("telegram_chat_id", ""),
        alert_cooldown_s=merged.get("alert_cooldown_s", 30),
        alert_threshold_net_qty=merged.get("alert_threshold_net_qty", 0.01),
        alert_threshold_single_pos_upnl=merged.get("alert_threshold_single_pos_upnl", 3000.0),
        alert_imbalance_count=merged.get("alert_imbalance_count", 12),
        local_ws_port=merged.get("local_ws_port", 8789),
        listening_server_port=merged.get("listening_server_port", 8001),
        pairs=merged["pairs"],
        _raw=merged,
    )
