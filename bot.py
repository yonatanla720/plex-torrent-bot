import asyncio
import functools
import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

import jackett
from config import load_config
from media import TorrentResult, detect_media_type, rank_and_filter
from qbittorrent import QBitClient

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

cfg = load_config()
qb = QBitClient(
    host=cfg["qbittorrent"]["host"],
    port=cfg["qbittorrent"]["port"],
    username=cfg["qbittorrent"]["username"],
    password=cfg["qbittorrent"]["password"],
    paths=cfg["paths"],
)

ALLOWED_USERS: set[int] = set(cfg["telegram"]["allowed_users"])


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
    prefs = cfg["preferences"]
    return rank_and_filter(
        results,
        quality_prefs=prefs["quality"],
        min_seeders=prefs["min_seeders"],
        max_results=prefs["max_results"],
    )


async def _add_torrent(magnet: str, media_type: str) -> None:
    await asyncio.to_thread(qb.add_torrent, magnet, media_type)


# --- Command handlers ---

@authorized
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Remote Torrent Downloader\n\n"
        "Commands:\n"
        "/download <query> - Download (default mode)\n"
        "/d <query> - Alias for /download\n"
        "/auto <query> - Auto-pick best torrent\n"
        "/choose <query> - Show options to pick from\n"
        "/status - Show active downloads"
    )


@authorized
async def cmd_download(update: Update, context: ContextTypes.DEFAULT_TYPE):
    mode = cfg["preferences"]["default_mode"]
    if mode == "choose":
        await _do_choose(update, context)
    else:
        await _do_auto(update, context)


@authorized
async def cmd_auto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _do_auto(update, context)


@authorized
async def cmd_choose(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _do_choose(update, context)


async def _do_auto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = " ".join(context.args).strip() if context.args else ""
    if not query:
        await update.message.reply_text("Usage: /auto <movie or show name>")
        return

    media_type = detect_media_type(query)
    msg = await update.message.reply_text(f"Searching for: {query} ({media_type})...")

    try:
        results = await _search_and_filter(query, media_type)
    except Exception as e:
        await msg.edit_text(f"Search failed: {e}")
        return

    if not results:
        await msg.edit_text("No results found with enough seeders.")
        return

    best = results[0]
    try:
        await _add_torrent(best.magnet, media_type)
    except Exception as e:
        await msg.edit_text(f"Failed to add torrent: {e}")
        return

    save_path = cfg["paths"]["tv"] if media_type == "tv" else cfg["paths"]["movies"]
    await msg.edit_text(
        f"Adding: {best.title}\n"
        f"({best.size_display}, {best.seeders} seeders)\n\n"
        f"Download started! Category: {media_type} -> {save_path}"
    )


async def _do_choose(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = " ".join(context.args).strip() if context.args else ""
    if not query:
        await update.message.reply_text("Usage: /choose <movie or show name>")
        return

    media_type = detect_media_type(query)
    msg = await update.message.reply_text(f"Searching for: {query} ({media_type})...")

    try:
        results = await _search_and_filter(query, media_type)
    except Exception as e:
        await msg.edit_text(f"Search failed: {e}")
        return

    if not results:
        await msg.edit_text("No results found with enough seeders.")
        return

    # Store results for callback
    context.user_data["pending_results"] = results
    context.user_data["pending_media_type"] = media_type

    buttons = []
    for i, r in enumerate(results):
        label = f"{r.title[:50]}... | {r.size_display} | {r.seeders}S" if len(r.title) > 50 else f"{r.title} | {r.size_display} | {r.seeders}S"
        buttons.append([InlineKeyboardButton(label, callback_data=f"pick:{i}")])
    buttons.append([InlineKeyboardButton("Cancel", callback_data="pick:cancel")])

    await msg.edit_text(
        f"Found results for: {query} ({media_type})\nPick one:",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def callback_pick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    if user_id not in ALLOWED_USERS:
        await query.edit_message_text("Unauthorized.")
        return

    data = query.data
    if data == "pick:cancel":
        await query.edit_message_text("Cancelled.")
        return

    idx = int(data.split(":")[1])
    results = context.user_data.get("pending_results", [])
    media_type = context.user_data.get("pending_media_type", "movie")

    if idx >= len(results):
        await query.edit_message_text("Selection expired. Search again.")
        return

    chosen = results[idx]
    try:
        await _add_torrent(chosen.magnet, media_type)
    except Exception as e:
        await query.edit_message_text(f"Failed to add torrent: {e}")
        return

    save_path = cfg["paths"]["tv"] if media_type == "tv" else cfg["paths"]["movies"]
    await query.edit_message_text(
        f"Adding: {chosen.title}\n"
        f"({chosen.size_display}, {chosen.seeders} seeders)\n\n"
        f"Download started! Category: {media_type} -> {save_path}"
    )
    context.user_data.pop("pending_results", None)
    context.user_data.pop("pending_media_type", None)


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

    lines = []
    for t in torrents[:15]:
        pct = int(t["progress"] * 100)
        size = _format_size(t["size"])
        speed = _format_speed(t["dlspeed"])
        cat = t["category"] or "none"
        bar = "█" * (pct // 10) + "░" * (10 - pct // 10)
        lines.append(f"{t['name'][:40]}\n  [{bar}] {pct}% | {size} | {speed} | {cat}")

    await update.message.reply_text("\n\n".join(lines))


# --- Main ---

def main():
    # Connect to qBittorrent
    try:
        version = qb.test_connection()
        qb.ensure_categories()
        logger.info("qBittorrent connected (v%s). Categories ensured.", version)
    except Exception as e:
        logger.error("Failed to connect to qBittorrent: %s", e)
        logger.error("Make sure qBittorrent is running with Web UI enabled.")
        return

    builder = Application.builder().token(cfg["telegram"]["bot_token"])

    proxy_url = (cfg.get("proxy") or {}).get("url")
    if proxy_url:
        from telegram.request import HTTPXRequest
        request = HTTPXRequest(proxy=proxy_url, read_timeout=30, connect_timeout=15)
        builder = builder.request(request)
        logger.info("Using proxy: %s", proxy_url)

    app = builder.build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("download", cmd_download))
    app.add_handler(CommandHandler("d", cmd_download))
    app.add_handler(CommandHandler("auto", cmd_auto))
    app.add_handler(CommandHandler("choose", cmd_choose))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CallbackQueryHandler(callback_pick, pattern=r"^pick:"))

    logger.info("Bot starting...")
    app.run_polling()


if __name__ == "__main__":
    main()
