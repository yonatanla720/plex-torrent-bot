import asyncio
import atexit
import functools
import logging
import os
import sys

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

import jackett
import tmdb
from config import load_config, load_settings, save_settings
from media import TorrentResult, detect_media_type, extract_tv_path, rank_and_filter
from qbittorrent import QBitClient

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

cfg = load_config()
runtime_settings = load_settings(cfg)
qb = QBitClient(
    host=cfg["qbittorrent"]["host"],
    port=cfg["qbittorrent"]["port"],
    username=cfg["qbittorrent"]["username"],
    password=cfg["qbittorrent"]["password"],
    paths=cfg["paths"],
)

ALLOWED_USERS: set[int] = set(cfg["telegram"]["allowed_users"])
TMDB_API_KEY: str = (cfg.get("tmdb") or {}).get("api_key", "")


# --- Auth decorator ---

def authorized(func):
    @functools.wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        if user_id not in ALLOWED_USERS:
            await update.message.reply_text("Unauthorized.")
            return
        return await func(update, context)
    return wrapper


# --- Helpers ---

def _format_size(size_bytes: int) -> str:
    gb = size_bytes / (1024 ** 3)
    if gb >= 1:
        return f"{gb:.1f} GB"
    return f"{size_bytes / (1024 ** 2):.0f} MB"


def _format_speed(speed: int) -> str:
    if speed >= 1024 * 1024:
        return f"{speed / (1024 * 1024):.1f} MB/s"
    return f"{speed / 1024:.0f} KB/s"


async def _search_and_filter(query: str, media_type: str) -> list[TorrentResult]:
    results = await jackett.search(
        base_url=cfg["jackett"]["url"],
        api_key=cfg["jackett"]["api_key"],
        query=query,
        media_type=media_type,
    )
    return rank_and_filter(
        results,
        quality_prefs=runtime_settings["quality"],
        min_seeders=runtime_settings["min_seeders"],
        max_size_gb=runtime_settings.get("max_size_gb", 0),
    )


async def _add_torrent(link: str, media_type: str, series_name: str = "") -> None:
    if link.startswith("magnet:"):
        await asyncio.to_thread(qb.add_torrent, link, media_type, series_name)
    else:
        # Download .torrent file from Jackett proxy URL, then pass bytes to qBittorrent
        import httpx
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(link)
            resp.raise_for_status()
        await asyncio.to_thread(
            qb.add_torrent, "", media_type, series_name, torrent_file=resp.content,
        )


def _format_eta(seconds: int) -> str:
    if seconds <= 0 or seconds >= 8640000:
        return "∞"
    h, remainder = divmod(seconds, 3600)
    m, s = divmod(remainder, 60)
    if h > 0:
        return f"{h}h{m:02d}m"
    if m > 0:
        return f"{m}m{s:02d}s"
    return f"{s}s"


async def _plex_scan() -> bool:
    """Trigger a Plex library scan. Returns True on success."""
    plex_cfg = cfg.get("plex") or {}
    url = plex_cfg.get("url")
    token = plex_cfg.get("token")
    if not url or not token:
        return False
    try:
        import httpx
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"{url.rstrip('/')}/library/sections/all/refresh",
                params={"X-Plex-Token": token},
            )
            return resp.status_code == 200
    except Exception:
        return False


# --- Command handlers ---

@authorized
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Remote Torrent Downloader\n\n"
        "Just type a movie or show name to search.\n\n"
        "Commands:\n"
        "/auto <query> - Auto-pick best torrent\n"
        "/top - Browse top torrents by category\n"
        "/status - Show active downloads\n"
        "/recent - Recent searches\n"
        "/settings - View/change settings\n"
        "/clear - Cancel current search"
    )


@authorized
async def cmd_clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    for key in [
        "pending_results", "pending_media_type", "pending_query",
        "pending_tv_download", "awaiting_tv_path",
        "awaiting_settings_password", "awaiting_settings_value",
    ]:
        context.user_data.pop(key, None)
    await update.message.reply_text("Cleared. Send a new search anytime.")


@authorized
async def cmd_recent(update: Update, context: ContextTypes.DEFAULT_TYPE):
    history = context.user_data.get("search_history", [])
    if not history:
        await update.message.reply_text("No recent searches.")
        return

    buttons = []
    for i, h in enumerate(history):
        label = f"{h['query']} ({h['media_type']})"
        buttons.append([InlineKeyboardButton(label, callback_data=f"recent:{i}")])

    await update.message.reply_text(
        "Recent searches:",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def callback_recent(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    if user_id not in ALLOWED_USERS:
        await query.edit_message_text("Unauthorized.")
        return

    idx = int(query.data.split(":")[1])
    history = context.user_data.get("search_history", [])
    if idx >= len(history):
        await query.edit_message_text("Entry not found.")
        return

    h = history[idx]
    await query.edit_message_text(f"Searching for: {h['query']} ({h['media_type']})...")
    await _do_search(update, context, h["query"], h["media_type"], edit_msg=query.message)


@authorized
async def cmd_auto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = " ".join(context.args).strip() if context.args else ""
    if not query:
        await update.message.reply_text("Usage: /auto <movie or show name>")
        return
    media_type = detect_media_type(query)
    msg = await update.message.reply_text(f"Searching for: {query} ({media_type})...")
    # Force auto mode by temporarily overriding
    results = await _search_and_filter(query, media_type)
    if not results:
        await msg.edit_text("No results found with enough seeders.")
        return
    best = results[0]
    tv_sub = extract_tv_path(best.title) if media_type == "tv" else ""
    try:
        await _add_torrent(best.magnet, media_type, tv_sub)
    except Exception as e:
        await msg.edit_text(f"Failed to add torrent: {e}")
        return
    save_path = cfg["paths"]["tv"] if media_type == "tv" else cfg["paths"]["movies"]
    if tv_sub:
        save_path = f"{save_path}/{tv_sub}"
    await msg.edit_text(
        f"Adding: {best.title}\n"
        f"({best.size_display}, {best.seeders} seeders)\n\n"
        f"Download started! Category: {media_type} -> {save_path}"
    )


@authorized
async def cmd_top(update: Update, context: ContextTypes.DEFAULT_TYPE):
    buttons = [
        [
            InlineKeyboardButton("Top Movies", callback_data="top:movie:top"),
            InlineKeyboardButton("Top TV Shows", callback_data="top:tv:top"),
        ],
        [
            InlineKeyboardButton("Recent Movies", callback_data="top:movie:recent"),
            InlineKeyboardButton("Recent TV Shows", callback_data="top:tv:recent"),
        ],
    ]
    await update.message.reply_text(
        "Browse torrents:",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def callback_top(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    if user_id not in ALLOWED_USERS:
        await query.edit_message_text("Unauthorized.")
        return

    parts = query.data.split(":")
    media_type = parts[1]
    sort_mode = parts[2] if len(parts) > 2 else "top"
    cat_label = "movies" if media_type == "movie" else "TV shows"
    mode_label = "top" if sort_mode == "top" else "recent"
    await query.edit_message_text(f"Fetching {mode_label} {cat_label}...")

    try:
        results = await jackett.search(
            base_url=cfg["jackett"]["url"],
            api_key=cfg["jackett"]["api_key"],
            query="",
            media_type=media_type,
            limit=20,
        )
        # Deduplicate by title (different indexers return same torrents)
        seen = set()
        unique = []
        for r in results:
            key = r.title.lower()
            if key not in seen:
                seen.add(key)
                unique.append(r)

        if sort_mode == "recent":
            # Already sorted by date from Jackett, just deduplicate
            results = unique[:20]
        else:
            # Sort by seeders for "top" view
            unique.sort(key=lambda r: -r.seeders)
            results = unique[:20]
    except Exception as e:
        await query.edit_message_text(f"Failed to fetch {mode_label} torrents: {e}")
        return

    if not results:
        await query.edit_message_text(f"No {mode_label} {cat_label} found.")
        return

    heading = f"{'Top' if sort_mode == 'top' else 'Recent'} {cat_label}"
    context.user_data["pending_results"] = results
    context.user_data["pending_media_type"] = media_type
    context.user_data["pending_query"] = heading
    page_size = runtime_settings["max_results"]
    await _show_page(query.message, results, heading, media_type, page=0, page_size=page_size)


@authorized
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.message.text.strip()
    if not query:
        return

    # Check if we're waiting for settings password
    if context.user_data.get("awaiting_settings_password"):
        setting = context.user_data.pop("awaiting_settings_password")
        password = cfg["preferences"].get("settings_password", "")
        if query != password:
            await update.message.reply_text("Wrong password.")
            return
        context.user_data["awaiting_settings_value"] = setting
        current_values = {
            "seeders": runtime_settings["min_seeders"],
            "maxsize": runtime_settings.get("max_size_gb", 0),
            "maxresults": runtime_settings["max_results"],
        }
        prompt = SETTINGS_PROMPTS[setting].format(current_values[setting])
        await update.message.reply_text(prompt)
        return

    # Check if we're waiting for a settings value
    if context.user_data.get("awaiting_settings_value"):
        setting = context.user_data.pop("awaiting_settings_value")
        if setting == "seeders":
            try:
                value = int(query)
                if value < 0:
                    raise ValueError
            except ValueError:
                await update.message.reply_text("Invalid value. Enter a positive number.")
                return
            runtime_settings["min_seeders"] = value
            save_settings(runtime_settings)
        elif setting == "maxsize":
            try:
                value = float(query)
                if value < 0:
                    raise ValueError
            except ValueError:
                await update.message.reply_text("Invalid value. Enter a number (0 = no limit).")
                return
            runtime_settings["max_size_gb"] = value
            save_settings(runtime_settings)
        elif setting == "maxresults":
            try:
                value = int(query)
                if value < 1:
                    raise ValueError
            except ValueError:
                await update.message.reply_text("Invalid value. Enter a positive number.")
                return
            runtime_settings["max_results"] = value
            save_settings(runtime_settings)
        await update.message.reply_text(_settings_text(), reply_markup=_settings_buttons())
        return

    # Check if we're waiting for a custom TV path
    if context.user_data.get("awaiting_tv_path"):
        context.user_data["awaiting_tv_path"] = False
        tv_dl = context.user_data.get("pending_tv_download")
        results = context.user_data.get("pending_results", [])

        if not tv_dl or tv_dl["idx"] >= len(results):
            await update.message.reply_text("Session expired. Send your search again.")
            return

        chosen = results[tv_dl["idx"]]
        tv_sub = query.strip("/")
        try:
            await _add_torrent(chosen.magnet, "tv", tv_sub)
        except Exception as e:
            await update.message.reply_text(f"Failed to add torrent: {e}")
            return

        save_path = cfg["paths"]["tv"]
        if tv_sub:
            save_path = f"{save_path}/{tv_sub}"
        await update.message.reply_text(
            f"Adding: {chosen.title}\n"
            f"({chosen.size_display}, {chosen.seeders} seeders)\n\n"
            f"Download started! Category: tv -> {save_path}"
        )
        context.user_data.pop("pending_results", None)
        context.user_data.pop("pending_media_type", None)
        context.user_data.pop("pending_query", None)
        context.user_data.pop("pending_tv_download", None)
        return

    media_type = detect_media_type(query)
    if media_type == "tv":
        # Clear episode pattern — skip the prompt
        await _do_search(update, context, query, "tv")
    else:
        # Ambiguous — ask the user
        context.user_data["pending_query"] = query
        buttons = [
            [
                InlineKeyboardButton("Movie", callback_data="type:movie"),
                InlineKeyboardButton("TV Show", callback_data="type:tv"),
            ]
        ]
        await update.message.reply_text(
            f"Is \"{query}\" a movie or TV show?",
            reply_markup=InlineKeyboardMarkup(buttons),
        )


async def callback_type(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    if user_id not in ALLOWED_USERS:
        await query.edit_message_text("Unauthorized.")
        return

    media_type = query.data.split(":")[1]
    pending_query = context.user_data.pop("pending_query", None)
    if not pending_query:
        await query.edit_message_text("Session expired. Send your search again.")
        return

    await query.edit_message_text(f"Searching for: {pending_query} ({media_type})...")
    await _do_search(update, context, pending_query, media_type, edit_msg=query.message)


async def _do_search(update: Update, context: ContextTypes.DEFAULT_TYPE, query: str, media_type: str, edit_msg=None):
    # Save to search history
    history = context.user_data.setdefault("search_history", [])
    entry = {"query": query, "media_type": media_type}
    if entry not in history:
        history.insert(0, entry)
        if len(history) > 10:
            history.pop()

    msg = edit_msg or await update.message.reply_text(f"Searching for: {query} ({media_type})...")

    try:
        results = await _search_and_filter(query, media_type)
    except Exception as e:
        await msg.edit_text(f"Search failed: {e}")
        return

    if not results:
        await msg.edit_text("No results found with enough seeders.")
        return

    mode = runtime_settings["default_mode"]
    if mode == "auto":
        best = results[0]
        series = extract_series_name(best.title) if media_type == "tv" else ""
        try:
            await _add_torrent(best.magnet, media_type, series)
        except Exception as e:
            await msg.edit_text(f"Failed to add torrent: {e}")
            return

        save_path = cfg["paths"]["tv"] if media_type == "tv" else cfg["paths"]["movies"]
        if series:
            save_path = f"{save_path}/{series}"
        await msg.edit_text(
            f"Adding: {best.title}\n"
            f"({best.size_display}, {best.seeders} seeders)\n\n"
            f"Download started! Category: {media_type} -> {save_path}"
        )
    else:
        context.user_data["pending_results"] = results
        context.user_data["pending_media_type"] = media_type
        context.user_data["pending_query"] = query
        page_size = runtime_settings["max_results"]
        await _show_page(msg, results, query, media_type, page=0, page_size=page_size)


async def _show_page(msg, results, query, media_type, page, page_size):
    total = len(results)
    total_pages = (total + page_size - 1) // page_size
    start = page * page_size
    end = min(start + page_size, total)
    page_results = results[start:end]

    buttons = []
    for i, r in enumerate(page_results):
        idx = start + i
        poster_icon = "🎬 " if r.imdb_id else ""
        label = f"{poster_icon}{r.title[:50]}... | {r.size_display} | {r.seeders}S" if len(r.title) > 50 else f"{poster_icon}{r.title} | {r.size_display} | {r.seeders}S"
        buttons.append([InlineKeyboardButton(label, callback_data=f"pick:{idx}")])

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("< Prev", callback_data=f"page:{page - 1}"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton("Next >", callback_data=f"page:{page + 1}"))
    if nav:
        buttons.append(nav)
    buttons.append([InlineKeyboardButton("Cancel", callback_data="pick:cancel")])

    await msg.edit_text(
        f"{query} ({media_type}) — {total} results, page {page + 1}/{total_pages}:",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def callback_page(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    if user_id not in ALLOWED_USERS:
        await query.edit_message_text("Unauthorized.")
        return

    page = int(query.data.split(":")[1])
    results = context.user_data.get("pending_results", [])
    media_type = context.user_data.get("pending_media_type", "movie")
    pending_query = context.user_data.get("pending_query", "")
    page_size = runtime_settings["max_results"]

    if not results:
        await query.edit_message_text("Session expired. Send your search again.")
        return

    # If returning from a photo detail view, delete the photo and send a new text message
    photo_msg_id = context.user_data.pop("detail_photo_msg_id", None)
    if photo_msg_id:
        try:
            await query.message.delete()
        except Exception:
            pass
        msg = await query.message.chat.send_message("Loading...")
        await _show_page(msg, results, pending_query, media_type, page, page_size)
        return

    await _show_page(query.message, results, pending_query, media_type, page, page_size)


async def callback_pick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show detail view for a selected torrent."""
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    if user_id not in ALLOWED_USERS:
        await query.edit_message_text("Unauthorized.")
        return

    data = query.data
    if data == "pick:cancel":
        await query.edit_message_text("Cancelled.")
        context.user_data.pop("pending_results", None)
        context.user_data.pop("pending_media_type", None)
        context.user_data.pop("pending_query", None)
        return

    idx = int(data.split(":")[1])
    results = context.user_data.get("pending_results", [])
    media_type = context.user_data.get("pending_media_type", "movie")

    if idx >= len(results):
        await query.edit_message_text("Selection expired. Search again.")
        return

    r = results[idx]
    page_size = runtime_settings["max_results"]
    back_page = idx // page_size

    desc = r.description.strip() if r.description else ""

    detail = (
        f"Title: {r.title}\n"
        f"Size: {r.size_display}\n"
        f"Seeders: {r.seeders} | Leechers: {r.leechers}\n"
        f"Indexer: {r.indexer or 'unknown'}\n"
        f"Uploaded: {r.pub_date or 'unknown'}"
    )
    if desc:
        detail += f"\n\nDescription:\n{desc}"

    buttons = [
        [InlineKeyboardButton("Download", callback_data=f"dl:{idx}")],
    ]
    if r.info_url and r.info_url.startswith("http"):
        buttons.append([InlineKeyboardButton("View on indexer", url=r.info_url)])
    buttons.append([InlineKeyboardButton("< Back to results", callback_data=f"page:{back_page}")])
    markup = InlineKeyboardMarkup(buttons)

    # Try to send with poster art
    poster_url = await tmdb.get_poster_url(TMDB_API_KEY, r.imdb_id)
    if poster_url:
        # Photo captions have a 1024 char limit
        if len(detail) > 1024:
            detail = detail[:1021] + "..."
        try:
            await query.message.delete()
            sent = await query.message.chat.send_photo(
                photo=poster_url,
                caption=detail,
                reply_markup=markup,
            )
            # Track the photo message so "Back to results" can delete it
            context.user_data["detail_photo_msg_id"] = sent.message_id
            return
        except Exception:
            pass  # Fall through to text-only

    # Text-only fallback
    # Restore text limit for non-photo messages
    if len(detail) > 4096:
        detail = detail[:4093] + "..."
    context.user_data.pop("detail_photo_msg_id", None)
    await query.edit_message_text(detail, reply_markup=markup)


async def _reply_or_edit(query, context, text, reply_markup=None):
    """Send text reply, handling the case where the current message is a photo."""
    photo_msg_id = context.user_data.pop("detail_photo_msg_id", None)
    if photo_msg_id:
        try:
            await query.message.delete()
        except Exception:
            pass
        await query.message.chat.send_message(text, reply_markup=reply_markup)
    else:
        await query.edit_message_text(text, reply_markup=reply_markup)


async def callback_download(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Download the selected torrent (or prompt for TV path confirmation)."""
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    if user_id not in ALLOWED_USERS:
        await _reply_or_edit(query, context, "Unauthorized.")
        return

    idx = int(query.data.split(":")[1])
    results = context.user_data.get("pending_results", [])
    media_type = context.user_data.get("pending_media_type", "movie")

    if idx >= len(results):
        await _reply_or_edit(query, context, "Selection expired. Search again.")
        return

    chosen = results[idx]

    if media_type == "tv":
        # Show path confirmation before downloading
        tv_sub = extract_tv_path(chosen.title)
        context.user_data["pending_tv_download"] = {
            "idx": idx,
            "tv_sub": tv_sub,
        }
        base = cfg["paths"]["tv"]
        full_path = f"{base}/{tv_sub}" if tv_sub else base
        buttons = [
            [InlineKeyboardButton("Confirm", callback_data="tvpath:confirm")],
            [InlineKeyboardButton("Change path", callback_data="tvpath:change")],
            [InlineKeyboardButton("< Back", callback_data=f"pick:{idx}")],
        ]
        await _reply_or_edit(
            query, context,
            f"Title: {chosen.title}\n\n"
            f"Download path:\n{full_path}\n\n"
            f"Is this correct?",
            reply_markup=InlineKeyboardMarkup(buttons),
        )
        return

    # Movies — download immediately
    try:
        await _add_torrent(chosen.magnet, media_type, "")
    except Exception as e:
        await _reply_or_edit(query, context, f"Failed to add torrent: {e}")
        return

    save_path = cfg["paths"]["movies"]
    await _reply_or_edit(
        query, context,
        f"Adding: {chosen.title}\n"
        f"({chosen.size_display}, {chosen.seeders} seeders)\n\n"
        f"Download started! Category: {media_type} -> {save_path}",
    )
    context.user_data.pop("pending_results", None)
    context.user_data.pop("pending_media_type", None)
    context.user_data.pop("pending_query", None)


async def callback_tvpath(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle TV path confirmation or change request."""
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    if user_id not in ALLOWED_USERS:
        await query.edit_message_text("Unauthorized.")
        return

    action = query.data.split(":")[1]
    tv_dl = context.user_data.get("pending_tv_download")
    results = context.user_data.get("pending_results", [])

    if not tv_dl or tv_dl["idx"] >= len(results):
        await query.edit_message_text("Session expired. Search again.")
        return

    chosen = results[tv_dl["idx"]]

    if action == "confirm":
        tv_sub = tv_dl["tv_sub"]
        try:
            await _add_torrent(chosen.magnet, "tv", tv_sub)
        except Exception as e:
            await query.edit_message_text(f"Failed to add torrent: {e}")
            return

        save_path = cfg["paths"]["tv"]
        if tv_sub:
            save_path = f"{save_path}/{tv_sub}"
        await query.edit_message_text(
            f"Adding: {chosen.title}\n"
            f"({chosen.size_display}, {chosen.seeders} seeders)\n\n"
            f"Download started! Category: tv -> {save_path}"
        )
        context.user_data.pop("pending_results", None)
        context.user_data.pop("pending_media_type", None)
        context.user_data.pop("pending_query", None)
        context.user_data.pop("pending_tv_download", None)

    elif action == "change":
        context.user_data["awaiting_tv_path"] = True
        await query.edit_message_text(
            f"Current path: {tv_dl['tv_sub']}\n\n"
            "Type the new subfolder path (e.g. Show Name/Season 01):"
        )


def _build_status_message(torrents: list[dict]) -> tuple[str, InlineKeyboardMarkup]:
    """Build status text and inline keyboard from torrent list."""
    lines = []
    buttons = []
    for i, t in enumerate(torrents[:15]):
        pct = int(t["progress"] * 100)
        size = _format_size(t["size"])
        speed = _format_speed(t["dlspeed"])
        cat = t["category"] or "none"
        eta = _format_eta(t["eta"])
        bar = "█" * (pct // 10) + "░" * (10 - pct // 10)
        lines.append(f"{i + 1}. {t['name'][:40]}\n  [{bar}] {pct}% | {size} | {speed} | ETA {eta} | {cat}")
        buttons.append([InlineKeyboardButton(
            f"Cancel #{i + 1}: {t['name'][:30]}",
            callback_data=f"cancel:{t['hash'][:20]}",
        )])

    buttons.append([InlineKeyboardButton("Refresh", callback_data="status:refresh")])
    return "\n\n".join(lines), InlineKeyboardMarkup(buttons)


@authorized
async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        torrents = await asyncio.to_thread(qb.get_active_torrents)
    except Exception as e:
        await update.message.reply_text(f"Failed to get status: {e}")
        return

    if not torrents:
        await update.message.reply_text("No active torrents.")
        return

    text, markup = _build_status_message(torrents)
    await update.message.reply_text(text, reply_markup=markup)


async def callback_status_refresh(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    if user_id not in ALLOWED_USERS:
        await query.edit_message_text("Unauthorized.")
        return

    try:
        torrents = await asyncio.to_thread(qb.get_active_torrents)
    except Exception as e:
        await query.edit_message_text(f"Failed to get status: {e}")
        return

    if not torrents:
        await query.edit_message_text("No active torrents.")
        return

    text, markup = _build_status_message(torrents)
    await query.edit_message_text(text, reply_markup=markup)


async def callback_cancel_torrent(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    if user_id not in ALLOWED_USERS:
        await query.edit_message_text("Unauthorized.")
        return

    partial_hash = query.data.split(":")[1]

    try:
        # Find the full hash by prefix match
        torrents = await asyncio.to_thread(qb.get_active_torrents)
        match = next((t for t in torrents if t["hash"].startswith(partial_hash)), None)
        if not match:
            await query.edit_message_text("Torrent not found (may have already finished).")
            return

        await asyncio.to_thread(qb.cancel_torrent, match["hash"])
    except Exception as e:
        await query.edit_message_text(f"Failed to cancel: {e}")
        return

    # Refresh the status view
    try:
        torrents = await asyncio.to_thread(qb.get_active_torrents)
    except Exception:
        torrents = []

    if not torrents:
        await query.edit_message_text(f"Cancelled: {match['name']}\n\nNo active torrents.")
        return

    text, markup = _build_status_message(torrents)
    await query.edit_message_text(f"Cancelled: {match['name']}\n\n{text}", reply_markup=markup)


@authorized
async def cmd_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        count = await asyncio.to_thread(qb.clear_completed)
    except Exception as e:
        await update.message.reply_text(f"Failed to clear: {e}")
        return

    if count == 0:
        await update.message.reply_text("No completed torrents to remove.")
    else:
        plex_ok = await _plex_scan()
        plex_note = "\nPlex library scan triggered." if plex_ok else ""
        await update.message.reply_text(f"Removed {count} completed torrent(s). Files kept on disk.{plex_note}")


# --- Settings ---

def _settings_text() -> str:
    s = runtime_settings
    max_size = s.get("max_size_gb", 0)
    size_display = f"{max_size} GB" if max_size > 0 else "No limit"
    return (
        "Settings:\n\n"
        f"Quality: {', '.join(s['quality'])}\n"
        f"Min seeders: {s['min_seeders']}\n"
        f"Max torrent size: {size_display}\n"
        f"Results per page: {s['max_results']}\n"
        f"Mode: {s['default_mode']}"
    )


def _settings_buttons() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Quality preferences", callback_data="settings:quality")],
        [InlineKeyboardButton("Change min seeders", callback_data="settings:seeders")],
        [InlineKeyboardButton("Change max size", callback_data="settings:maxsize")],
        [InlineKeyboardButton("Change results per page", callback_data="settings:maxresults")],
        [
            InlineKeyboardButton("Mode: auto", callback_data="settings:mode_auto"),
            InlineKeyboardButton("Mode: choose", callback_data="settings:mode_choose"),
        ],
        [InlineKeyboardButton("Close", callback_data="settings:close")],
    ])


def _quality_buttons() -> InlineKeyboardMarkup:
    active = set(runtime_settings["quality"])
    rows = []
    for q in QUALITY_OPTIONS:
        check = "✅" if q in active else "⬜"
        rows.append([InlineKeyboardButton(f"{check} {q}", callback_data=f"qtoggle:{q}")])
    rows.append([InlineKeyboardButton("< Back to settings", callback_data="settings:back")])
    return InlineKeyboardMarkup(rows)


QUALITY_OPTIONS = ["2160p", "1080p", "720p", "480p"]

SETTINGS_PROMPTS = {
    "seeders": "Current: {}\n\nEnter minimum seeders:",
    "maxsize": "Current: {} GB (0 = no limit)\n\nEnter max size in GB:",
    "maxresults": "Current: {}\n\nEnter results per page:",
}


@authorized
async def cmd_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(_settings_text(), reply_markup=_settings_buttons())


async def callback_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    if user_id not in ALLOWED_USERS:
        await query.edit_message_text("Unauthorized.")
        return

    action = query.data.split(":")[1]

    # Close settings
    if action == "close":
        await query.message.delete()
        return

    # Back to main settings view
    if action == "back":
        await query.edit_message_text(_settings_text(), reply_markup=_settings_buttons())
        return

    # Mode toggles don't need text input
    if action == "mode_auto":
        runtime_settings["default_mode"] = "auto"
        save_settings(runtime_settings)
        await query.edit_message_text(_settings_text(), reply_markup=_settings_buttons())
        return
    if action == "mode_choose":
        runtime_settings["default_mode"] = "choose"
        save_settings(runtime_settings)
        await query.edit_message_text(_settings_text(), reply_markup=_settings_buttons())
        return

    # Quality — show toggle sub-menu (no text input needed)
    if action == "quality":
        await query.edit_message_text(
            f"Quality preferences (tap to toggle):\n\nActive: {', '.join(runtime_settings['quality'])}",
            reply_markup=_quality_buttons(),
        )
        return

    # Text input settings — check password first
    current_values = {
        "seeders": runtime_settings["min_seeders"],
        "maxsize": runtime_settings.get("max_size_gb", 0),
        "maxresults": runtime_settings["max_results"],
    }

    password = cfg["preferences"].get("settings_password", "")
    if password:
        context.user_data["awaiting_settings_password"] = action
        await query.edit_message_text("Enter settings password:")
    else:
        context.user_data["awaiting_settings_value"] = action
        prompt = SETTINGS_PROMPTS[action].format(current_values[action])
        await query.edit_message_text(prompt)


async def callback_quality_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    if user_id not in ALLOWED_USERS:
        await query.edit_message_text("Unauthorized.")
        return

    quality = query.data.split(":")[1]
    current = list(runtime_settings["quality"])

    if quality in current:
        if len(current) <= 1:
            await query.answer("At least one quality must be selected.", show_alert=True)
            return
        current.remove(quality)
    else:
        current.append(quality)

    runtime_settings["quality"] = current
    save_settings(runtime_settings)

    await query.edit_message_text(
        f"Quality preferences (tap to toggle):\n\nActive: {', '.join(current)}",
        reply_markup=_quality_buttons(),
    )


# --- Download completion notifications ---

_known_torrents: dict[str, bool] = {}  # hash -> was_complete


async def _check_completed(context: ContextTypes.DEFAULT_TYPE):
    """Background job: notify users when torrents finish downloading."""
    global _known_torrents
    try:
        states = await asyncio.to_thread(qb.get_all_torrent_states)
    except Exception:
        return

    newly_completed = []
    for h, info in states.items():
        was_complete = _known_torrents.get(h)
        if info["is_complete"] and was_complete is False:
            newly_completed.append(info["name"])

    _known_torrents = {h: info["is_complete"] for h, info in states.items()}

    if newly_completed:
        text = "Download complete:\n" + "\n".join(f"- {name}" for name in newly_completed)
        for user_id in ALLOWED_USERS:
            try:
                await context.bot.send_message(chat_id=user_id, text=text)
            except Exception:
                pass


async def post_init(application):
    from telegram import BotCommand
    await application.bot.set_my_commands([
        BotCommand("status", "Show active downloads"),
        BotCommand("done", "Remove completed torrents"),
        BotCommand("recent", "Recent searches"),
        BotCommand("auto", "Auto-pick best torrent"),
        BotCommand("top", "Browse top torrents"),
        BotCommand("clear", "Cancel current search"),
        BotCommand("settings", "View/change settings"),
    ])


# --- Main ---

LOCK_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".bot.lock")


def _acquire_lock():
    """Ensure only one bot instance runs at a time using a PID lock file."""
    if os.path.exists(LOCK_FILE):
        with open(LOCK_FILE) as f:
            old_pid = f.read().strip()
        # Check if that process is still alive
        try:
            os.kill(int(old_pid), 0)
        except (OSError, ValueError):
            pass  # Process is dead, stale lock — we can proceed
        else:
            logger.error("Bot is already running (PID %s). Exiting.", old_pid)
            sys.exit(1)

    with open(LOCK_FILE, "w") as f:
        f.write(str(os.getpid()))

    atexit.register(_release_lock)


def _release_lock():
    """Remove the lock file on exit."""
    try:
        os.remove(LOCK_FILE)
    except OSError:
        pass


def main():
    _acquire_lock()

    # Connect to qBittorrent
    try:
        version = qb.test_connection()
        qb.ensure_categories()
        logger.info("qBittorrent connected (v%s). Categories ensured.", version)
    except Exception as e:
        logger.error("Failed to connect to qBittorrent: %s", e)
        logger.error("Make sure qBittorrent is running with Web UI enabled.")
        return

    from telegram.ext import PicklePersistence
    persistence = PicklePersistence(filepath="bot_data.pickle")
    builder = Application.builder().token(cfg["telegram"]["bot_token"]).persistence(persistence)

    proxy_url = (cfg.get("proxy") or {}).get("url")
    if proxy_url:
        from telegram.request import HTTPXRequest
        request = HTTPXRequest(proxy=proxy_url, read_timeout=30, connect_timeout=15)
        builder = builder.request(request)
        logger.info("Using proxy: %s", proxy_url)

    app = builder.post_init(post_init).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("auto", cmd_auto))
    app.add_handler(CommandHandler("top", cmd_top))
    app.add_handler(CommandHandler("clear", cmd_clear))
    app.add_handler(CommandHandler("recent", cmd_recent))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("done", cmd_done))
    app.add_handler(CommandHandler("settings", cmd_settings))
    app.add_handler(CallbackQueryHandler(callback_top, pattern=r"^top:"))
    app.add_handler(CallbackQueryHandler(callback_recent, pattern=r"^recent:"))
    app.add_handler(CallbackQueryHandler(callback_type, pattern=r"^type:"))
    app.add_handler(CallbackQueryHandler(callback_page, pattern=r"^page:"))
    app.add_handler(CallbackQueryHandler(callback_pick, pattern=r"^pick:"))
    app.add_handler(CallbackQueryHandler(callback_download, pattern=r"^dl:"))
    app.add_handler(CallbackQueryHandler(callback_tvpath, pattern=r"^tvpath:"))
    app.add_handler(CallbackQueryHandler(callback_quality_toggle, pattern=r"^qtoggle:"))
    app.add_handler(CallbackQueryHandler(callback_settings, pattern=r"^settings:"))
    app.add_handler(CallbackQueryHandler(callback_status_refresh, pattern=r"^status:refresh$"))
    app.add_handler(CallbackQueryHandler(callback_cancel_torrent, pattern=r"^cancel:"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    # Seed known torrents so existing ones don't trigger false notifications
    global _known_torrents
    try:
        states = qb.get_all_torrent_states()
        _known_torrents = {h: info["is_complete"] for h, info in states.items()}
    except Exception:
        pass

    # Poll for completed downloads every 30 seconds
    app.job_queue.run_repeating(_check_completed, interval=30, first=10)

    logger.info("Bot starting...")
    app.run_polling()


if __name__ == "__main__":
    main()
