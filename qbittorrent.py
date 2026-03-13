import qbittorrentapi


class QBitClient:
    def __init__(self, host: str, port: int, username: str, password: str, paths: dict):
        self.client = qbittorrentapi.Client(
            host=host,
            port=port,
            username=username,
            password=password,
        )
        self.paths = paths  # {"movies": "/mnt/media/Movies", "tv": "/mnt/media/TV Shows"}

    def test_connection(self) -> str:
        """Login and return qBittorrent version. Raises on failure."""
        self.client.auth_log_in()
        return self.client.app.version

    def ensure_categories(self):
        """Create movies and tv categories with correct save paths."""
        existing = self.client.torrents_categories()
        for cat, path_key in [("movies", "movies"), ("tv", "tv")]:
            save_path = self.paths[path_key]
            if cat in existing:
                if existing[cat].savePath != save_path:
                    self.client.torrents_edit_category(name=cat, save_path=save_path)
            else:
                self.client.torrents_create_category(name=cat, save_path=save_path)

    def add_torrent(self, url: str, media_type: str) -> None:
        """Add a torrent (magnet or URL) with the appropriate category."""
        category = "tv" if media_type == "tv" else "movies"
        save_path = self.paths.get(media_type, self.paths["movies"])
        self.client.torrents_add(urls=url, category=category, save_path=save_path)

    def get_active_torrents(self) -> list[dict]:
        """Return list of active (non-completed) torrents with progress info."""
        torrents = self.client.torrents_info(status_filter="all")
        active = []
        for t in torrents:
            if t.state_enum.is_complete:
                if t.state_enum not in (
                    qbittorrentapi.TorrentStates.UPLOADING,
                    qbittorrentapi.TorrentStates.STALLED_UPLOAD,
                    qbittorrentapi.TorrentStates.QUEUED_UPLOAD,
                    qbittorrentapi.TorrentStates.FORCED_UPLOAD,
                ):
                    continue
            active.append({
                "name": t.name,
                "progress": t.progress,
                "state": t.state,
                "size": t.total_size,
                "dlspeed": t.dlspeed,
                "category": t.category,
            })
        return active

    def clear_completed(self) -> int:
        """Remove completed torrents from qBittorrent. Returns count removed."""
        torrents = self.client.torrents_info(status_filter="completed")
        if not torrents:
            return 0
        hashes = [t.hash for t in torrents]
        self.client.torrents_delete(delete_files=False, torrent_hashes=hashes)
        return len(hashes)
