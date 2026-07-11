"""Media extractor — pulls copyright-friendly media (og:image, og:video, YouTube)
for news items. Only URLs are stored; no content is re-hosted."""
from __future__ import annotations
import os
import re
import logging
import asyncio
from typing import Optional, Tuple
from urllib.parse import urlparse, parse_qs, urljoin

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger("media_extractor")

_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.0 Safari/605.1.15"
)

_YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be", "youtube-nocookie.com", "www.youtube-nocookie.com"}
_YOUTUBE_ID_RE = re.compile(r"[a-zA-Z0-9_-]{11}")


def _domain(url: str) -> str:
    try:
        return urlparse(url).netloc.lower()
    except Exception:
        return ""


def _extract_youtube_id(url: str) -> Optional[str]:
    """Return the 11-char YouTube video id if `url` points to a YouTube video."""
    if not url:
        return None
    try:
        u = urlparse(url)
    except Exception:
        return None
    host = u.netloc.lower().replace("www.", "")
    if host not in {h.replace("www.", "") for h in _YOUTUBE_HOSTS}:
        return None
    # /watch?v=<id>
    if u.path in ("/watch", "/watch/"):
        vid = parse_qs(u.query).get("v", [None])[0]
        if vid and _YOUTUBE_ID_RE.fullmatch(vid):
            return vid
    # /embed/<id>, /v/<id>, /shorts/<id>
    m = re.match(r"^/(embed|v|shorts)/([a-zA-Z0-9_-]{11})", u.path)
    if m:
        return m.group(2)
    # youtu.be/<id>
    if host == "youtu.be":
        pid = u.path.lstrip("/").split("/")[0]
        if _YOUTUBE_ID_RE.fullmatch(pid):
            return pid
    return None


async def _fetch_html(url: str, timeout: float = 8.0) -> Optional[str]:
    try:
        async with httpx.AsyncClient(
            timeout=timeout, follow_redirects=True,
            headers={"User-Agent": _USER_AGENT, "Accept-Language": "it,en;q=0.8"},
        ) as client:
            r = await client.get(url)
            if r.status_code >= 400:
                return None
            ct = r.headers.get("content-type", "")
            if "html" not in ct.lower():
                return None
            return r.text
    except Exception as e:
        logger.debug(f"fetch_html failed {url}: {e}")
        return None


def _find_meta(soup: BeautifulSoup, names: list) -> Optional[str]:
    for name in names:
        for attr in ("property", "name", "itemprop"):
            tag = soup.find("meta", attrs={attr: name})
            if tag and tag.get("content"):
                return tag["content"].strip()
    return None


def _extract_og(html: str, base_url: str) -> dict:
    """Return dict with keys: image, video, video_type, is_youtube, video_id."""
    result = {"image": None, "video": None, "video_type": None, "is_youtube": False, "video_id": None}
    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception:
        try:
            soup = BeautifulSoup(html, "html.parser")
        except Exception:
            return result

    # Image: og:image / og:image:secure_url / twitter:image / itemprop=image
    img = _find_meta(soup, [
        "og:image:secure_url", "og:image:url", "og:image",
        "twitter:image", "twitter:image:src", "image",
    ])
    if img:
        # If it's a relative URL make it absolute
        if img.startswith("//"):
            img = "https:" + img
        elif img.startswith("/"):
            img = urljoin(base_url, img)
        result["image"] = img

    # Video: og:video / og:video:url / og:video:secure_url / twitter:player
    vid = _find_meta(soup, [
        "og:video:secure_url", "og:video:url", "og:video",
        "twitter:player:stream", "twitter:player",
    ])
    vid_type = _find_meta(soup, ["og:video:type", "twitter:player:stream:content_type"])
    if vid:
        if vid.startswith("//"):
            vid = "https:" + vid
        result["video"] = vid
        result["video_type"] = vid_type
        yid = _extract_youtube_id(vid)
        if yid:
            result["is_youtube"] = True
            result["video_id"] = yid

    # Fallback: scan for a first embedded YouTube iframe.
    if not result["video_id"]:
        for iframe in soup.find_all("iframe", src=True):
            yid = _extract_youtube_id(iframe["src"])
            if yid:
                result["is_youtube"] = True
                result["video_id"] = yid
                result["video"] = f"https://www.youtube.com/embed/{yid}"
                break

    # Fallback: <link rel="image_src">.
    if not result["image"]:
        link = soup.find("link", rel="image_src")
        if link and link.get("href"):
            href = link["href"]
            if href.startswith("//"):
                href = "https:" + href
            elif href.startswith("/"):
                href = urljoin(base_url, href)
            result["image"] = href

    return result


async def _youtube_search(query: str, api_key: str, timeout: float = 8.0) -> Optional[dict]:
    """Return {video_id, title, thumbnail, channel} for the best embeddable match, or None."""
    if not api_key or not query:
        return None
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.get(
                "https://www.googleapis.com/youtube/v3/search",
                params={
                    "part": "snippet",
                    "q": query,
                    "type": "video",
                    "maxResults": 5,
                    "videoEmbeddable": "true",
                    "safeSearch": "moderate",
                    "relevanceLanguage": "it",
                    "key": api_key,
                },
            )
            if r.status_code >= 400:
                logger.warning(f"YouTube search HTTP {r.status_code}: {r.text[:200]}")
                return None
            data = r.json()
            items = data.get("items") or []
            if not items:
                return None
            top = items[0]
            vid = top.get("id", {}).get("videoId")
            if not vid:
                return None
            sn = top.get("snippet", {})
            thumbs = sn.get("thumbnails") or {}
            thumb = (thumbs.get("high") or thumbs.get("medium") or thumbs.get("default") or {}).get("url")
            return {
                "video_id": vid,
                "title": sn.get("title"),
                "thumbnail": thumb or f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg",
                "channel": sn.get("channelTitle"),
            }
    except Exception as e:
        logger.warning(f"YouTube search error: {e}")
        return None


def _is_probably_direct_video(url: str, video_type: Optional[str]) -> bool:
    if video_type and video_type.startswith("video/"):
        return True
    if not url:
        return False
    lower = url.lower().split("?")[0]
    return lower.endswith((".mp4", ".m4v", ".mov", ".webm", ".ogv", ".m3u8"))


async def resolve_media(
    title: str,
    source_url: str,
    fallback_image: Optional[str] = None,
    youtube_api_key: Optional[str] = None,
    enable_youtube_search: bool = True,
    search_query: Optional[str] = None,
) -> Tuple[Optional[str], Optional[dict]]:
    """Return (image_url, media_dict).

    Copyright-safe strategy:
      1. Read the source page's OG/meta tags (explicit share-signal from publisher).
      2. Prefer YouTube embed if og:video points to YouTube (respects uploader's
         embed setting).
      3. Otherwise use direct MP4/HLS from og:video (self-hosted by the source).
      4. Otherwise query the YouTube Data API v3 filtered by `videoEmbeddable=true`.
      5. Fallback image = fallback_image (usually the AI-generated art).

    Returned media_dict shape:
      { type: 'youtube'|'video'|'image', video_id?, embed_url?, video_url?,
        thumbnail?, source_domain, source_url? }
    """
    domain = _domain(source_url)
    og = {}
    if source_url:
        html = await _fetch_html(source_url)
        if html:
            og = _extract_og(html, source_url)

    image_url = og.get("image") or fallback_image
    media: Optional[dict] = None

    # 1) YouTube from OG
    if og.get("video_id"):
        yid = og["video_id"]
        media = {
            "type": "youtube",
            "video_id": yid,
            "embed_url": f"https://www.youtube-nocookie.com/embed/{yid}?rel=0",
            "watch_url": f"https://www.youtube.com/watch?v={yid}",
            "thumbnail": f"https://i.ytimg.com/vi/{yid}/hqdefault.jpg",
            "source_domain": domain or "youtube.com",
            "provenance": "og_meta",
        }
    # 2) Direct video from OG
    elif og.get("video") and _is_probably_direct_video(og.get("video"), og.get("video_type")):
        media = {
            "type": "video",
            "video_url": og["video"],
            "video_type": og.get("video_type") or "video/mp4",
            "thumbnail": image_url,
            "source_domain": domain,
            "provenance": "og_meta",
        }
    # 3) YouTube search fallback
    elif enable_youtube_search and youtube_api_key:
        # Try a targeted entity-based query first, then fall back to the raw title.
        queries = []
        if search_query:
            queries.append(search_query)
        queries.append(title)
        yt = None
        for q in queries:
            if not q:
                continue
            yt = await _youtube_search(q, youtube_api_key)
            if yt:
                break
        if yt:
            yid = yt["video_id"]
            media = {
                "type": "youtube",
                "video_id": yid,
                "embed_url": f"https://www.youtube-nocookie.com/embed/{yid}?rel=0",
                "watch_url": f"https://www.youtube.com/watch?v={yid}",
                "thumbnail": yt.get("thumbnail"),
                "channel": yt.get("channel"),
                "video_title": yt.get("title"),
                "source_domain": "youtube.com",
                "provenance": "youtube_search",
            }
    # 4) Otherwise no video — fall back to image (still shown in the header hero,
    #    so we keep media=None to avoid a redundant duplicate media block).
    return image_url, media
