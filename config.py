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

    # Defaults for preferences
    prefs = cfg.setdefault("preferences", {})
    prefs.setdefault("quality", ["1080p", "720p", "2160p"])
    prefs.setdefault("min_seeders", 3)
    prefs.setdefault("max_results", 5)
    prefs.setdefault("default_mode", "auto")

    return cfg
