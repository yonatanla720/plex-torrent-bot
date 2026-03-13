"""Plex Media Server API client for library browsing and management."""

import xml.etree.ElementTree as ET

import httpx


async def _get(url: str, token: str, params: dict | None = None) -> ET.Element:
    """Make an authenticated GET request to Plex and return parsed XML."""
    p = {"X-Plex-Token": token}
    if params:
        p.update(params)
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(url, params=p)
        resp.raise_for_status()
    return ET.fromstring(resp.text)


async def get_sections(base_url: str, token: str) -> list[dict]:
    """List library sections (Movies, TV Shows, etc.)."""
    root = await _get(f"{base_url}/library/sections", token)
    sections = []
    for d in root.findall(".//Directory"):
        sections.append({
            "key": d.get("key"),
            "title": d.get("title"),
            "type": d.get("type"),  # "movie" or "show"
        })
    return sections


async def get_items(base_url: str, token: str, section_key: str,
                    start: int = 0, size: int = 20) -> tuple[list[dict], int]:
    """List items in a library section. Returns (items, total_count)."""
    root = await _get(
        f"{base_url}/library/sections/{section_key}/all", token,
        {"X-Plex-Container-Start": str(start), "X-Plex-Container-Size": str(size),
         "sort": "addedAt:desc"},
    )
    total = int(root.get("totalSize", root.get("size", "0")))
    items = []
    for el in root:
        if el.tag not in ("Video", "Directory"):
            continue
        items.append({
            "ratingKey": el.get("ratingKey"),
            "title": el.get("title"),
            "year": el.get("year", ""),
            "type": el.get("type"),
            "thumb": el.get("thumb", ""),
        })
    return items, total


async def get_metadata(base_url: str, token: str, rating_key: str) -> dict:
    """Get metadata for a single item."""
    root = await _get(f"{base_url}/library/metadata/{rating_key}", token)
    el = root[0] if len(root) else None
    if el is None:
        return {}
    return {
        "ratingKey": el.get("ratingKey"),
        "title": el.get("title", ""),
        "year": el.get("year", ""),
        "type": el.get("type", ""),
        "thumb": el.get("thumb", ""),
        "summary": el.get("summary", ""),
        "rating": el.get("rating", ""),
        "contentRating": el.get("contentRating", ""),
        "duration": el.get("duration", ""),
    }


async def get_children(base_url: str, token: str, rating_key: str) -> list[dict]:
    """Get children of an item (seasons of a show, episodes of a season)."""
    root = await _get(f"{base_url}/library/metadata/{rating_key}/children", token)
    children = []
    for el in root:
        if el.tag not in ("Video", "Directory"):
            continue
        children.append({
            "ratingKey": el.get("ratingKey"),
            "title": el.get("title"),
            "index": el.get("index", ""),
            "type": el.get("type"),
            "parentIndex": el.get("parentIndex", ""),
        })
    return children


def thumb_url(base_url: str, token: str, thumb_path: str) -> str:
    """Build a full thumbnail URL."""
    if not thumb_path:
        return ""
    return f"{base_url}{thumb_path}?X-Plex-Token={token}"


async def get_thumb(base_url: str, token: str, thumb_path: str) -> bytes | None:
    """Download thumbnail image bytes. Returns None if unavailable."""
    if not thumb_path:
        return None
    try:
        url = f"{base_url}{thumb_path}"
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url, params={"X-Plex-Token": token})
            resp.raise_for_status()
        return resp.content
    except Exception:
        return None


async def delete_item(base_url: str, token: str, rating_key: str) -> bool:
    """Delete a media item from Plex. Returns True on success."""
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.delete(
            f"{base_url}/library/metadata/{rating_key}",
            params={"X-Plex-Token": token},
        )
    return resp.status_code == 200
