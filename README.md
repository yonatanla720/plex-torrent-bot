# Plex Torrent Bot

A Telegram bot that searches for torrents via Jackett and downloads them through qBittorrent, with automatic routing to Plex media folders.

## Features

- **Search** — Send any movie or TV show name to search across all your Jackett indexers
- **Smart detection** — Automatically detects TV shows (S01E05 patterns) vs movies
- **Browse results** — Paginated results with detail view (description, seeders, leechers, size, upload date)
- **Auto mode** — Optionally auto-pick the best torrent based on quality/seeder ranking
- **Download management** — Monitor progress, ETA, speed; cancel torrents; clear completed
- **Completion notifications** — Get notified when downloads finish
- **TV path organization** — Auto-organizes TV shows into `Show Name/Season XX` folders with manual override
- **Runtime settings** — Change quality, seeders, size limits, and mode directly from the bot
- **Plex integration** — Triggers library scan when clearing completed downloads

## Prerequisites

None — the setup script installs everything for you (Python will be installed if missing)

## Quick Start

1. Clone and run the setup wizard:
   ```bash
   git clone https://github.com/yonatanla720/plex-torrent-bot.git
   cd plex-torrent-bot
   ./setup.sh          # Linux / Mac
   # or
   setup.bat            # Windows (double-click or run from cmd)
   ```

   The setup wizard will:
   - Create a virtual environment and install all dependencies
   - Install Docker containers (Jackett, qBittorrent, FlareSolverr)
   - Walk you through configuring each service step by step
   - Set up download paths with an interactive directory browser

2. Run the bot:
   ```bash
   source venv/bin/activate   # Windows: venv\Scripts\activate
   python bot.py
   ```

   For development with auto-reload (Linux/Mac):
   ```bash
   ./run.sh
   ```

## Configuration

### config.yaml (secrets, read-only)

| Section | Key | Description |
|---------|-----|-------------|
| `telegram` | `bot_token` | Telegram bot token |
| `telegram` | `allowed_users` | List of authorized Telegram user IDs |
| `jackett` | `url`, `api_key` | Jackett connection |
| `qbittorrent` | `host`, `port`, `username`, `password` | qBittorrent Web UI connection |
| `paths` | `movies`, `tv` | Download directories (should match Plex library paths) |
| `proxy` | `url` | SOCKS5 proxy for Telegram API (optional) |
| `preferences` | `settings_password` | Password to protect bot settings (optional) |

### settings.yaml (runtime, editable via bot)

These settings can be changed from the bot using `/settings`:

| Key | Default | Description |
|-----|---------|-------------|
| `quality` | `[1080p, 720p, 2160p]` | Quality preferences in priority order |
| `min_seeders` | `3` | Minimum seeders to include in results |
| `max_size_gb` | `0` | Max torrent size in GB (0 = no limit) |
| `max_results` | `5` | Results per page |
| `default_mode` | `choose` | `auto` (pick best) or `choose` (browse results) |

## Bot Commands

| Command | Description |
|---------|-------------|
| `/start` | Show help |
| `/status` | Active downloads with progress, speed, ETA |
| `/done` | Remove completed torrents (triggers Plex scan) |
| `/recent` | Re-run a previous search |
| `/auto <query>` | Search and auto-download the best match |
| `/settings` | View and change runtime settings |
| `/clear` | Cancel current search flow |

Or just type a movie/show name to search.

## Architecture

```
Telegram → bot.py → jackett.py → Jackett API (Torznab XML)
                  → qbittorrent.py → qBittorrent Web API
                  → media.py (detection, ranking, filtering)
                  → config.py (YAML config + runtime settings)
```

## License

MIT
