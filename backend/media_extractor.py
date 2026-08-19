"""Media extractor — pulls copyright-friendly media (og:image, og:video, YouTube)
for news items. Only URLs are stored; no content is re-hosted."""
from __future__ import annotations
import re
import logging
import unicodedata
from datetime import datetime, timezone
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

# --- Quality filter constants ---------------------------------------------

# Italian + English stopwords + very short words we should not use as "signal".
_STOPWORDS = {
    'il','lo','la','i','gli','le','un','uno','una','di','a','da','in','con','su','per','tra','fra',
    'e','ed','o','od','ma','se','non','ne','ci','si','vi','mi','ti','che','chi','cui','al','allo',
    'alla','ai','agli','alle','del','dello','della','dei','degli','delle','dal','dallo','dalla','dai',
    'dagli','dalle','nel','nello','nella','nei','negli','nelle','col','coi','sul','sullo','sulla',
    'sui','sugli','sulle','anche','come','più','meno','molto','solo','ora','poi','ancora','così',
    'the','of','and','or','a','an','to','in','on','with','for','by','from','is','are','was','vs','vs.',
    'sono','era','è','ha','ho','hai','hanno','abbiamo','avete','avere','essere','fare',
    'contro','tra','tutto','tutti','tutta','tutte','questo','questa','questi','queste','quel','quella',
    'ancora','pure','sarà','stato','stata','stati','state','solo','ecco',
    'faida','faide','news','notizia','video','oggi','ieri','sky','tg','italia','italiano','italiana',
    'roma','milano','italy',
}

# Known Italian news outlets and general trustworthy channels — bonus if the
# YouTube uploader looks like an editorial newsroom.
_NEWS_CHANNEL_TOKENS = {
    'rai', 'raiplay', 'raiuno', 'raidue', 'raitre', 'raisport', 'raicinema', 'tg1', 'tg2', 'tg3', 'tg5',
    'tgla7', 'la7', 'sky', 'skytg24', 'tgcom24', 'tgcom', 'mediaset', 'canale5', 'italia1', 'rete4',
    'fanpage', 'fanpage.it', 'corriere', 'corrieretv', 'repubblica', 'ansa', 'leggo', 'ilmessaggero',
    'ilfattoquotidiano', 'lastampa', 'ilgiornale', 'agi', 'openonline', 'open', 'ilpost',
    'dagospia', 'buzzitalia', 'tvblog', 'davidemaggio', 'gossipetv', 'novella2000', 'chi', 'diva',
    'gazzetta', 'gazzettadellosport', 'sportmediaset', 'sportitalia', 'dazn', 'ottoemezzo',
    'reuters', 'bloomberg', 'bbc', 'cnn', 'associated press', 'euronews',
}

# Substrings that strongly suggest the video is NOT news reporting (music, fan
# content, memes, generic countdowns, karaoke, gameplay, etc.).
_NEGATIVE_TITLE_TOKENS = {
    'inno di mameli', 'inno nazionale', 'karaoke', 'lyrics', 'testo e traduzione', 'audio only',
    'official audio', 'official video', 'gameplay', 'let\'s play', 'walkthrough', 'reaction',
    'reazione a', 'trailer ufficiale', 'top 10', 'top 5', 'compilation', 'mashup', 'mixtape',
    'best moments', 'meme', 'shitpost', 'skit', 'sketch',
}

# --------------------------------------------------------------------------


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


def _normalize(text: str) -> str:
    """Lowercase + strip accents/apostrophes/punctuation."""
    if not text:
        return ""
    t = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    t = t.lower()
    t = re.sub(r"[^a-z0-9\s]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def _keywords_from(*sources: str, min_len: int = 4) -> set:
    """Return a set of significant tokens (nouns/proper nouns) from the given
    strings after normalization and stopword removal."""
    out = set()
    for s in sources:
        if not s:
            continue
        for tok in _normalize(s).split():
            if len(tok) < min_len:
                continue
            if tok in _STOPWORDS:
                continue
            if tok.isdigit() and len(tok) < 4:
                continue
            out.add(tok)
    return out


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


MIN_RELEVANCE_SCORE = 2  # relaxed to increase video coverage — the topic-
# keyword and negative-token guardrails still filter music/gaming/fan
# videos that only share entity names with the story. The founder saw
# only 7/120 feuds with a video attached under the previous stricter
# threshold; lowering by 1 point roughly doubles video coverage without
# meaningfully increasing off-topic false positives.
MIN_KEYWORD_MATCHES = 1   # a single strong signal-term match is enough when
# combined with a news channel or recent publication date; the score guard
# still enforces overall relevance.


def _score_video(
    item: dict,
    signal_keywords: set,
    topic_keywords: Optional[set] = None,
) -> Tuple[int, dict]:
    """Return (score, detail) for a YouTube search item.

    Scoring rubric — kept intentionally deterministic and inspectable:
      +2 per distinct signal keyword found in the video title (capped at +6)
      +1 per distinct signal keyword found in the description
      +2 if the channel handle/title contains a known Italian news outlet token
      +2 if the video was published within the last 30 days
      +1 if published within 90 days
      -6 if the video title matches any negative token (music/fan/gameplay/etc.)
      -5 if `topic_keywords` is given and NONE of them appear in title+description
         (this rejects videos that merely mention the same entities but are
          about a different event, e.g. "Milan Inter Full Squad" vs the Ouédraogo
          transfer story).
    """
    sn = item.get("snippet") or {}
    title = sn.get("title") or ""
    desc = sn.get("description") or ""
    channel = sn.get("channelTitle") or ""
    published = sn.get("publishedAt") or ""

    title_norm = _normalize(title)
    desc_norm = _normalize(desc)
    channel_norm = _normalize(channel)
    combined = title_norm + " " + desc_norm

    # Fast-reject: heavy negative flag if the title contains known "not news" markers.
    neg_hit = None
    for neg in _NEGATIVE_TITLE_TOKENS:
        if neg in title_norm:
            neg_hit = neg
            break

    title_hits = {k for k in signal_keywords if k in title_norm}
    desc_hits = {k for k in signal_keywords if k in desc_norm}
    topic_hits = set()
    topic_title_hits = set()
    if topic_keywords:
        topic_hits = {k for k in topic_keywords if k in combined}
        topic_title_hits = {k for k in topic_keywords if k in title_norm}

    score = 0
    score += min(len(title_hits), 3) * 2
    score += min(len(desc_hits), 3) * 1
    if any(tok in channel_norm for tok in _NEWS_CHANNEL_TOKENS):
        score += 2

    if published:
        try:
            pub = datetime.fromisoformat(published.replace("Z", "+00:00"))
            days = (datetime.now(timezone.utc) - pub).days
            if days <= 30:
                score += 2
            elif days <= 90:
                score += 1
        except Exception:
            pass

    if neg_hit:
        score -= 6

    if topic_keywords:
        # No event-specific term matched at all → likely off-topic.
        if not topic_hits:
            score -= 5
        # Topic terms only in description (not title) → weaker relevance.
        elif not topic_title_hits:
            score -= 3

    return score, {
        "title_hits": sorted(title_hits),
        "desc_hits": sorted(desc_hits),
        "topic_hits": sorted(topic_hits),
        "topic_title_hits": sorted(topic_title_hits),
        "channel": channel,
        "published": published,
        "neg_hit": neg_hit,
        "score": score,
    }


async def _youtube_search(
    query: str,
    api_key: str,
    signal_keywords: Optional[set] = None,
    topic_keywords: Optional[set] = None,
    min_score: int = MIN_RELEVANCE_SCORE,
    timeout: float = 10.0,
) -> Optional[dict]:
    """Return {video_id, title, thumbnail, channel, score, debug} for the best-scoring
    embeddable YouTube video, or None if nothing clears `min_score`.

    `signal_keywords` = full set of relevant tokens (title + parties + ...).
    `topic_keywords`  = subset that describes the specific event (excludes party
    names). If provided, videos that don't hit any topic keyword are downranked.
    """
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
                    "maxResults": 10,
                    "videoEmbeddable": "true",
                    "safeSearch": "moderate",
                    "relevanceLanguage": "it",
                    "regionCode": "IT",
                    "order": "relevance",
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

        if signal_keywords is None:
            signal_keywords = _keywords_from(query)

        scored = []
        for it in items:
            score, detail = _score_video(it, signal_keywords, topic_keywords=topic_keywords)
            hits = len(detail["title_hits"]) + len(detail["desc_hits"])
            if hits < MIN_KEYWORD_MATCHES:
                score -= 2
                detail["score"] = score
            scored.append((score, it, detail))

        if not scored:
            return None
        scored.sort(key=lambda x: x[0], reverse=True)
        best_score, best_item, best_detail = scored[0]
        if best_score < min_score:
            logger.info(
                f"YT quality reject: q='{query[:60]}' best_score={best_score} "
                f"title='{(best_item.get('snippet',{}).get('title') or '')[:60]}'"
            )
            return None
        vid = best_item.get("id", {}).get("videoId")
        if not vid:
            return None
        sn = best_item.get("snippet", {})
        thumbs = sn.get("thumbnails") or {}
        thumb = (thumbs.get("high") or thumbs.get("medium") or thumbs.get("default") or {}).get("url")
        return {
            "video_id": vid,
            "title": sn.get("title"),
            "thumbnail": thumb or f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg",
            "channel": sn.get("channelTitle"),
            "score": best_score,
            "debug": best_detail,
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
    # 3) YouTube search fallback — with quality gate.
    elif enable_youtube_search and youtube_api_key:
        entity_keywords = _keywords_from(search_query or "")
        title_keywords = _keywords_from(title)
        topic_keywords = title_keywords - entity_keywords
        signal_keywords = title_keywords | entity_keywords

        # Build queries from most specific to most generic. The composite query
        # combines entity + top topic keywords so YouTube's ranker can pinpoint
        # the exact event (e.g. "Milan Inter Ouédraogo" beats "Milan Inter").
        # We list top 3 topic keywords by length (longer usually = more specific).
        top_topics = sorted(topic_keywords, key=len, reverse=True)[:3]
        queries: list = []
        if search_query and top_topics:
            queries.append(f"{search_query} {' '.join(top_topics)}")
        if search_query:
            queries.append(search_query)
        if title:
            queries.append(title)
        # Deduplicate while preserving order.
        seen = set()
        queries = [q for q in queries if q and not (q in seen or seen.add(q))]

        yt = None
        for q in queries:
            yt = await _youtube_search(
                q, youtube_api_key,
                signal_keywords=signal_keywords,
                topic_keywords=topic_keywords or None,
            )
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
