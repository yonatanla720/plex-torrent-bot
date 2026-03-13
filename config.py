import sys
from pathlib import Path

import yaml


def load_config(path: str = "config.yaml") -> dict:
    config_path = Path(path)
    if not config_path.exists():
        print(f"Config file not found: {config_path}")
        sys.exit(1)

    with open(config_path) as f:
        cfg = yaml.safe_load(f)

    required = [
        ("telegram", "bot_token"),
        ("telegram", "allowed_users"),
        ("jackett", "url"),
        ("jackett", "api_key"),
        ("qbittorrent", "host"),
        ("qbittorrent", "port"),
        ("qbittorrent", "username"),
        ("qbittorrent", "password"),
        ("paths", "movies"),
        ("paths", "tv"),
    ]
    for section, key in required:
        if section not in cfg or key not in cfg[section]:
            print(f"Missing config: {section}.{key}")
            sys.exit(1)

    # Defaults for preferences (from config.yaml, read-only)
    prefs = cfg.setdefault("preferences", {})
    prefs.setdefault("quality", ["1080p", "720p", "2160p"])
    prefs.setdefault("min_seeders", 3)
    prefs.setdefault("max_results", 5)
    prefs.setdefault("default_mode", "auto")

    return cfg


SETTINGS_PATH = Path("settings.yaml")

# Keys that can be changed at runtime via the bot
SETTINGS_DEFAULTS = {
    "quality": ["1080p", "720p", "2160p"],
    "min_seeders": 3,
    "max_size_gb": 0,
    "max_results": 5,
    "default_mode": "choose",
}


def load_settings(cfg: dict) -> dict:
    """Load runtime-editable settings from settings.yaml, falling back to
    config.yaml preferences, then SETTINGS_DEFAULTS."""
    settings = dict(SETTINGS_DEFAULTS)
    # Seed from config.yaml preferences (initial values)
    prefs = cfg.get("preferences") or {}
    for key in SETTINGS_DEFAULTS:
        if key in prefs:
            settings[key] = prefs[key]
    # Override with settings.yaml (bot-modified values)
    if SETTINGS_PATH.exists():
        with open(SETTINGS_PATH) as f:
            data = yaml.safe_load(f)
        if isinstance(data, dict):
            settings.update(data)
    return settings


def save_settings(settings: dict) -> None:
    """Write runtime-editable settings to settings.yaml."""
    with open(SETTINGS_PATH, "w") as f:
        yaml.dump(settings, f, default_flow_style=False, sort_keys=False)
