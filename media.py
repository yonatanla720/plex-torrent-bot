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

    @property
    def size_display(self) -> str:
        gb = self.size_bytes / (1024 ** 3)
        if gb >= 1:
            return f"{gb:.1f} GB"
        mb = self.size_bytes / (1024 ** 2)
        return f"{mb:.0f} MB"


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
    max_results: int,
) -> list[TorrentResult]:
    """Filter by min seeders, sort by quality preference then seeders, return top N."""
    filtered = [r for r in results if r.seeders >= min_seeders]
    filtered.sort(key=lambda r: (_quality_score(r.title, quality_prefs), -r.seeders))
    return filtered[:max_results]
