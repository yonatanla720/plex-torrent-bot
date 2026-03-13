import xml.etree.ElementTree as ET

import httpx

from media import TorrentResult

TORZNAB_NS = {"torznab": "http://torznab.com/schemas/2015/feed"}

# Jackett categories: 2000 = movies, 5000 = TV
CAT_MOVIE = "2000"
CAT_TV = "5000"


async def search(
    base_url: str,
    api_key: str,
    query: str,
    media_type: str,
) -> list[TorrentResult]:
    """Search Jackett via Torznab API. Returns list of TorrentResult."""
    cat = CAT_TV if media_type == "tv" else CAT_MOVIE
    url = f"{base_url.rstrip('/')}/api/v2.0/indexers/all/results/torznab/api"
    params = {
        "apikey": api_key,
        "t": "search",
        "q": query,
        "cat": cat,
    }

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(url, params=params)
        resp.raise_for_status()

    return _parse_torznab(resp.text)


def _parse_torznab(xml_text: str) -> list[TorrentResult]:
    """Parse Torznab XML response into TorrentResult list."""
    root = ET.fromstring(xml_text)
    channel = root.find("channel")
    if channel is None:
        return []

    results = []
    for item in channel.findall("item"):
        title = item.findtext("title", "")
        magnet = _extract_magnet(item)
        if not magnet:
            continue

        seeders = _get_torznab_attr(item, "seeders")
        leechers = _get_torznab_attr(item, "peers")
        size = _get_torznab_attr(item, "size") or item.findtext("size", "0")
        indexer = item.findtext("jackettindexer", "")
        pub_date = item.findtext("pubDate", "")
        description = item.findtext("description", "")
        info_url = item.findtext("comments", "") or item.findtext("guid", "")
        imdb_id = _get_torznab_attr(item, "imdbid")

        results.append(TorrentResult(
            title=title,
            magnet=magnet,
            seeders=int(seeders) if seeders else 0,
            size_bytes=int(size) if size else 0,
            indexer=indexer,
            pub_date=pub_date,
            description=description,
            leechers=int(leechers) if leechers else 0,
            info_url=info_url,
            imdb_id=imdb_id,
        ))

    return results


def _extract_magnet(item: ET.Element) -> str:
    """Extract magnet link from item, checking multiple locations."""
    # Check <link> first
    link = item.findtext("link", "")
    if link.startswith("magnet:"):
        return link

    # Check torznab magneturl attribute
    magnet_attr = _get_torznab_attr(item, "magneturl")
    if magnet_attr:
        return magnet_attr

    # Check enclosure
    enclosure = item.find("enclosure")
    if enclosure is not None:
        enc_url = enclosure.get("url", "")
        if enc_url.startswith("magnet:"):
            return enc_url

    # Fall back to any link — Jackett often puts download URLs in <link>
    # that qBittorrent can handle (torrent file URLs)
    if link:
        return link

    return ""


def _get_torznab_attr(item: ET.Element, name: str) -> str:
    """Get a torznab:attr value by name."""
    for attr in item.findall("torznab:attr", TORZNAB_NS):
        if attr.get("name") == name:
            return attr.get("value", "")
    return ""
