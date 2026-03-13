# Plex Torrent Bot

A Telegram bot that searches for torrents via Jackett and downloads them through qBittorrent, with automatic routing to Plex media folders.

## Features

- **Search** — Send any movie or TV show name to search across all your Jackett indexers
- **Smart detection** — Automatically detects TV shows (S01E05 patterns) vs movies
- **Browse results** — Paginated results with detail view, poster art from TMDB, and torrent metadata
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
| `tmdb` | `api_key` | TMDB API key for poster art (optional, [get one free](https://www.themoviedb.org/settings/api)) |
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

## FAQ / Troubleshooting

**The bot can't reach the Telegram API (WSL2)**

WSL2 sometimes can't resolve `api.telegram.org`. Add an entry to `/etc/hosts`:
```
149.154.167.220  api.telegram.org
```

**qBittorrent says "Unauthorized" or won't connect**

The default credentials are `admin` / `adminadmin`, but newer versions generate a random temporary password on first start. Check the logs:
```bash
docker logs qbittorrent 2>&1 | grep "temporary password"
```
Then log in at http://localhost:8080 and change the password. Update `config.yaml` to match.

**Jackett indexers fail with Cloudflare errors**

Some indexers (1337x, EZTV) are behind Cloudflare. Install FlareSolverr (the setup wizard offers this) and configure its URL in Jackett's settings as `http://flaresolverr:8191`.

**Downloads go to the wrong folder / Plex doesn't see them**

Make sure `paths.movies` and `paths.tv` in `config.yaml` match your Plex library paths exactly. If running qBittorrent in Docker, the container needs a volume mount for those paths — the setup wizard configures this automatically, but if you changed paths after setup you may need to recreate the container.

**"Another instance is already running" but the bot isn't running**

The bot uses a PID lock file (`bot.pid`). If it wasn't shut down cleanly, the stale lock file may remain:
```bash
rm bot.pid
```

**No poster art on torrent details**

Poster art requires a free TMDB API key. To set it up:
1. Create an account at [themoviedb.org](https://www.themoviedb.org/signup)
2. Go to [Settings > API](https://www.themoviedb.org/settings/api) and request an API key (choose "Developer" and fill in basic details — approval is instant)
3. Copy the **API Key** (not the "Read Access Token") and add it to `config.yaml`:
   ```yaml
   tmdb:
     api_key: "your_api_key_here"
   ```
Results with available poster art are marked with a 🎬 icon in the search results list. Without a TMDB key, the bot works normally but shows text-only detail views.

**Port conflicts when creating Docker containers**

If ports 9117 (Jackett), 8080 (qBittorrent), or 8191 (FlareSolverr) are already in use, stop the conflicting service or change the port mapping. For example, to use port 9118 for Jackett, recreate the container with `-p 9118:9117` and update `jackett.url` in `config.yaml`.

## Architecture

```
Telegram → bot.py → jackett.py → Jackett API (Torznab XML)
                  → qbittorrent.py → qBittorrent Web API
                  → tmdb.py → TMDB API (poster art)
                  → media.py (detection, ranking, filtering)
                  → config.py (YAML config + runtime settings)
```

## License

MIT
