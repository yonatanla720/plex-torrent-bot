import asyncio
import functools
import logging

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
from config import load_config
from media import TorrentResult, detect_media_type, extract_tv_path, rank_and_filter
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
    )


async def _add_torrent(magnet: str, media_type: str, series_name: str = "") -> None:
    await asyncio.to_thread(qb.add_torrent, magnet, media_type, series_name)


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
        "/status - Show active downloads\n"
        "/recent - Recent searches\n"
        "/clear - Cancel current search"
    )


@authorized
async def cmd_clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
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
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.message.text.strip()
    if not query:
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

    mode = cfg["preferences"]["default_mode"]
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
        page_size = cfg["preferences"]["max_results"]
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
        label = f"{r.title[:50]}... | {r.size_display} | {r.seeders}S" if len(r.title) > 50 else f"{r.title} | {r.size_display} | {r.seeders}S"
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
    page_size = cfg["preferences"]["max_results"]

    if not results:
        await query.edit_message_text("Session expired. Send your search again.")
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
    page_size = cfg["preferences"]["max_results"]
    back_page = idx // page_size

    detail = (
        f"Title: {r.title}\n"
        f"Size: {r.size_display}\n"
        f"Seeders: {r.seeders}\n"
        f"Indexer: {r.indexer or 'unknown'}\n"
        f"Uploaded: {r.pub_date or 'unknown'}"
    )
    buttons = [
        [InlineKeyboardButton("Download", callback_data=f"dl:{idx}")],
        [InlineKeyboardButton("< Back to results", callback_data=f"page:{back_page}")],
    ]
    await query.edit_message_text(detail, reply_markup=InlineKeyboardMarkup(buttons))


async def callback_download(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Download the selected torrent."""
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    if user_id not in ALLOWED_USERS:
        await query.edit_message_text("Unauthorized.")
        return

    idx = int(query.data.split(":")[1])
    results = context.user_data.get("pending_results", [])
    media_type = context.user_data.get("pending_media_type", "movie")

    if idx >= len(results):
        await query.edit_message_text("Selection expired. Search again.")
        return

    chosen = results[idx]
    tv_sub = extract_tv_path(chosen.title) if media_type == "tv" else ""
    try:
        await _add_torrent(chosen.magnet, media_type, tv_sub)
    except Exception as e:
        await query.edit_message_text(f"Failed to add torrent: {e}")
        return

    save_path = cfg["paths"]["tv"] if media_type == "tv" else cfg["paths"]["movies"]
    if tv_sub:
        save_path = f"{save_path}/{tv_sub}"
    await query.edit_message_text(
        f"Adding: {chosen.title}\n"
        f"({chosen.size_display}, {chosen.seeders} seeders)\n\n"
        f"Download started! Category: {media_type} -> {save_path}"
    )
    context.user_data.pop("pending_results", None)
    context.user_data.pop("pending_media_type", None)
    context.user_data.pop("pending_query", None)


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


async def post_init(application):
    from telegram import BotCommand
    await application.bot.set_my_commands([
        BotCommand("status", "Show active downloads"),
        BotCommand("done", "Remove completed torrents"),
        BotCommand("recent", "Recent searches"),
        BotCommand("auto", "Auto-pick best torrent"),
        BotCommand("clear", "Cancel current search"),
    ])


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
    app.add_handler(CommandHandler("clear", cmd_clear))
    app.add_handler(CommandHandler("recent", cmd_recent))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("done", cmd_done))
    app.add_handler(CallbackQueryHandler(callback_recent, pattern=r"^recent:"))
    app.add_handler(CallbackQueryHandler(callback_type, pattern=r"^type:"))
    app.add_handler(CallbackQueryHandler(callback_page, pattern=r"^page:"))
    app.add_handler(CallbackQueryHandler(callback_pick, pattern=r"^pick:"))
    app.add_handler(CallbackQueryHandler(callback_download, pattern=r"^dl:"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    logger.info("Bot starting...")
    app.run_polling()


if __name__ == "__main__":
    main()
