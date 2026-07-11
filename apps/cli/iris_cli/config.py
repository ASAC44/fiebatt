"""Manages persistent CLI configuration at ~/.iris/config.json."""

import json
import uuid
from pathlib import Path
from typing import Any

CONFIG_DIR = Path.home() / ".iris"
CONFIG_PATH = CONFIG_DIR / "config.json"

DEFAULTS: dict[str, Any] = {
    "base_url": "http://localhost:8000",
    "session_id": None,  # generated on first load
    "token": None,
}


def load_config() -> dict[str, Any]:
    """Load config from disk, creating defaults if missing."""
    if CONFIG_PATH.exists():
        with CONFIG_PATH.open() as f:
            cfg = json.load(f)
    else:
        cfg = {}

    changed = False
    for key, default in DEFAULTS.items():
        if key not in cfg:
            cfg[key] = default
            changed = True

    if cfg["session_id"] is None:
        cfg["session_id"] = str(uuid.uuid4())
        changed = True

    if changed:
        save_config(cfg)

    return cfg


def save_config(cfg: dict[str, Any]) -> None:
    """Persist config to disk."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with CONFIG_PATH.open("w") as f:
        json.dump(cfg, f, indent=2)
        f.write("\n")


def get_client_kwargs() -> dict[str, Any]:
    """Return kwargs suitable for constructing an IrisClient."""
    cfg = load_config()
    return {
        "base_url": cfg["base_url"],
        "session_id": cfg["session_id"],
        "token": cfg.get("token"),
    }
