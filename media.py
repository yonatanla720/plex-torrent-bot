import re
from dataclasses import dataclass

TV_PATTERNS = [
    re.compile(r"S\d{1,2}E\d{1,2}", re.IGNORECASE),          # S01E05, s1e5
    re.compile(r"\d{1,2}x\d{2}", re.IGNORECASE),              # 1x05
    re.compile(r"season\s*\d+\s*episode\s*\d+", re.IGNORECASE),  # season 1 episode 5
    re.compile(r"S\d{1,2}", re.IGNORECASE),                    # S01 (full season)
]


@dataclass
class TorrentResult:
    title: str
    magnet: str
    seeders: int
    size_bytes: int
    indexer: str = ""
    pub_date: str = ""
    description: str = ""

    @property
    def size_display(self) -> str:
        gb = self.size_bytes / (1024 ** 3)
        if gb >= 1:
            return f"{gb:.1f} GB"
        mb = self.size_bytes / (1024 ** 2)
        return f"{mb:.0f} MB"


SEASON_PATTERNS = [
    re.compile(r"S(\d{1,2})E\d{1,2}", re.IGNORECASE),         # S01E05
    re.compile(r"(\d{1,2})x\d{2}", re.IGNORECASE),             # 1x05
    re.compile(r"season\s*(\d+)", re.IGNORECASE),               # season 1
    re.compile(r"S(\d{1,2})(?!\d)", re.IGNORECASE),             # S01 (full season)
]


def extract_series_name(title: str) -> str:
    """Extract series name from a torrent title by stripping episode patterns and quality info."""
    for pattern in TV_PATTERNS:
        match = pattern.search(title)
        if match:
            name = title[:match.start()].strip()
            name = re.sub(r"[._]", " ", name).strip(" -")
            return name
    return re.sub(r"[._]", " ", title).strip()


def extract_season(title: str) -> int | None:
    """Extract season number from a torrent title. Returns None if not found."""
    for pattern in SEASON_PATTERNS:
        match = pattern.search(title)
        if match:
            return int(match.group(1))
    return None


def extract_tv_path(title: str) -> str:
    """Extract full TV subfolder path: 'Series Name/Season 01'. Returns '' if not TV."""
    series = extract_series_name(title)
    if not series:
        return ""
    season = extract_season(title)
    if season is not None:
        return f"{series}/Season {season:02d}"
    return series


def detect_media_type(query: str) -> str:
    """Return 'tv' if query matches TV patterns, else 'movie'."""
    for pattern in TV_PATTERNS:
        if pattern.search(query):
            return "tv"
    return "movie"


def _quality_score(title: str, quality_prefs: list[str]) -> int:
    """Lower score = better match. Returns len(quality_prefs) if no match."""
    title_upper = title.upper()
    for i, q in enumerate(quality_prefs):
        if q.upper() in title_upper:
            return i
    return len(quality_prefs)


def rank_and_filter(
    results: list[TorrentResult],
    quality_prefs: list[str],
    min_seeders: int,
) -> list[TorrentResult]:
    """Filter by min seeders, sort by quality preference then seeders."""
    filtered = [r for r in results if r.seeders >= min_seeders]
    filtered.sort(key=lambda r: (_quality_score(r.title, quality_prefs), -r.seeders))
    return filtered
