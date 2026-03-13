import httpx

TMDB_BASE = "https://api.themoviedb.org/3"
IMAGE_BASE = "https://image.tmdb.org/t/p/w500"

# In-memory cache: imdb_id -> poster URL (or "" if none found)
_cache: dict[str, str] = {}


async def get_poster_url(api_key: str, imdb_id: str) -> str:
    """Look up a poster URL from TMDB using an IMDB ID. Returns URL or ""."""
    if not api_key or not imdb_id:
        return ""

    if imdb_id in _cache:
        return _cache[imdb_id]

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"{TMDB_BASE}/find/{imdb_id}",
                params={"api_key": api_key, "external_source": "imdb_id"},
            )
            resp.raise_for_status()
            data = resp.json()

        # Check movie results first, then TV
        for key in ("movie_results", "tv_results", "tv_season_results", "tv_episode_results"):
            for item in data.get(key, []):
                poster = item.get("poster_path")
                if poster:
                    url = f"{IMAGE_BASE}{poster}"
                    _cache[imdb_id] = url
                    return url

        _cache[imdb_id] = ""
        return ""
    except Exception:
        return ""
