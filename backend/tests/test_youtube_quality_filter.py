"""Iteration 20 — YouTube Quality Filter tests.

Covers:
1. Unit tests for _score_video (deterministic scoring rubric).
2. _youtube_search integration with mocked httpx client (topic-hit gate).
3. resolve_media end-to-end with real YOUTUBE_API_KEY (best-effort, gated).
4. POST /api/admin/backfill_media?force=true rejects music/anthem videos and
   overwrites stale media with None.
5. Regressions for /api/feuds/{id} 410, /api/users/{id}/history snapshots,
   /api/feuds/archive/dates last-7-days window.
"""
from __future__ import annotations
import os
import sys
import json
import uuid
import time
import asyncio
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import requests

# Import target module (backend path)
sys.path.insert(0, "/app/backend")
from media_extractor import (
    _score_video,
    _youtube_search,
    resolve_media,
    _keywords_from,
    MIN_RELEVANCE_SCORE,
    _NEGATIVE_TITLE_TOKENS,
)

BASE_URL = "http://localhost:8001"
ADMIN_KEY = "populus-admin-42b8f3"


# --------------------------------------------------------------------------
# 1) Unit tests for _score_video
# --------------------------------------------------------------------------

def _item(title, desc="", channel="", published=None):
    if published is None:
        published = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    return {
        "id": {"videoId": "abcdefghijk"},
        "snippet": {
            "title": title,
            "description": desc,
            "channelTitle": channel,
            "publishedAt": published,
            "thumbnails": {"high": {"url": "https://i.ytimg.com/vi/abcdefghijk/hqdefault.jpg"}},
        },
    }


class TestScoreVideo:
    """Deterministic rubric tests."""

    def test_a_anthem_only_parties_rejected(self):
        # Title = party names only, no topic terms, music channel — should score < 4.
        title = "Fratelli d'Italia - Inno di Mameli"
        signal = _keywords_from("Forza Italia Fratelli d'Italia") | _keywords_from(title)
        entity = _keywords_from("Forza Italia Fratelli d'Italia")
        topic = _keywords_from(title) - entity  # empty (all party words)
        it = _item(title, channel="Just Italian Music", published=(datetime.now(timezone.utc)-timedelta(days=5)).isoformat().replace("+00:00","Z"))
        score, detail = _score_video(it, signal, topic_keywords=topic or None)
        print(f"[A] score={score} detail={detail}")
        assert score < MIN_RELEVANCE_SCORE, f"anthem-only video should be rejected, got {score}"

    def test_b_news_topic_recent_accepted(self):
        # News channel + topic keyword hits + recent → score >= 8.
        title = "Briatore contro il gelato a 95 euro: la lite scoppia in serata"
        signal = _keywords_from("Flavio Briatore gelatieri") | _keywords_from(title)
        entity = _keywords_from("Flavio Briatore gelatieri")
        topic = _keywords_from(title) - entity  # includes gelato, euro, lite...
        it = _item(
            title,
            desc="La polemica tra Briatore e il gelataio esplode: gelato a 95 euro.",
            channel="Leggo",
            published=(datetime.now(timezone.utc)-timedelta(days=3)).isoformat().replace("+00:00","Z"),
        )
        score, detail = _score_video(it, signal, topic_keywords=topic or None)
        print(f"[B] score={score} detail={detail}")
        assert score >= 8, f"news+topic+recent should score >=8, got {score}: {detail}"

    def test_c_negative_token_penalty(self):
        # Compare a baseline (news-y) vs same title + a negative token → score drops by 6.
        base_title = "Briatore contro il gelato caro a 95 euro"
        signal = _keywords_from("Flavio Briatore gelatieri") | _keywords_from(base_title)
        entity = _keywords_from("Flavio Briatore gelatieri")
        topic = _keywords_from(base_title) - entity
        base = _item(base_title, channel="Leggo",
                     published=(datetime.now(timezone.utc)-timedelta(days=3)).isoformat().replace("+00:00","Z"))
        # Choose a token from the module's set to be safe.
        for tok in ("karaoke", "gameplay", "inno di mameli"):
            neg_title = base_title + " karaoke" if tok == "karaoke" else (
                base_title + " gameplay" if tok == "gameplay" else base_title + " inno di mameli"
            )
            neg = _item(neg_title, channel="Leggo",
                        published=base["snippet"]["publishedAt"])
            base_score, _ = _score_video(base, signal, topic_keywords=topic or None)
            neg_score, ndet = _score_video(neg, signal, topic_keywords=topic or None)
            print(f"[C tok={tok!r}] base={base_score} neg={neg_score} neg_detail={ndet}")
            assert neg_score == base_score - 6, (
                f"negative token '{tok}' should subtract 6; base={base_score} neg={neg_score}"
            )


# --------------------------------------------------------------------------
# 2) _youtube_search integration with mocked httpx
# --------------------------------------------------------------------------

class TestYoutubeSearchMocked:
    """Mock the YouTube API so we control the 3-item mix and assert the picker."""

    def test_picks_only_topically_relevant_news(self):
        now = datetime.now(timezone.utc)
        recent = (now - timedelta(days=3)).isoformat().replace("+00:00", "Z")
        items = [
            # (1) News-channel + topic keyword hit — should win.
            _item(
                "Briatore contro il gelato a 95 euro: la lite",
                desc="Flavio Briatore attacca il gelataio",
                channel="Leggo",
                published=recent,
            ) | {"id": {"videoId": "NEWSITEM011"}},
            # (2) Off-topic: mentions Briatore but about F1, no topic terms
            _item(
                "Formula 1: intervista a Flavio Briatore in paddock",
                desc="Retroscena sul team, niente sul gelato.",
                channel="MotorTube",
                published=recent,
            ) | {"id": {"videoId": "OFFTOPIC012"}},
            # (3) Music/anthem
            _item(
                "Inno di Mameli - versione karaoke",
                desc="Karaoke ufficiale.",
                channel="Just Italian Music",
                published=recent,
            ) | {"id": {"videoId": "MUSICITEM03"}},
        ]

        title = "Briatore attacca il gelato da 95€"
        entity = _keywords_from("Flavio Briatore gelatieri")
        signal = entity | _keywords_from(title)
        topic = _keywords_from(title) - entity

        # Mock httpx.AsyncClient.get
        fake_resp = MagicMock()
        fake_resp.status_code = 200
        fake_resp.json.return_value = {"items": items}

        class FakeClient:
            def __init__(self, *a, **kw): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
            async def get(self, *a, **kw): return fake_resp

        with patch("media_extractor.httpx.AsyncClient", FakeClient):
            result = asyncio.run(_youtube_search(
                query=f"Flavio Briatore gelatieri {' '.join(list(topic)[:3])}",
                api_key="FAKE",
                signal_keywords=signal,
                topic_keywords=topic or None,
            ))

        print(f"[YT-search] result={result}")
        assert result is not None, "should have returned a video"
        assert result["video_id"] == "NEWSITEM011", (
            f"expected news item id NEWSITEM011, got {result['video_id']}"
        )
        assert "Leggo" in (result.get("channel") or "")


# --------------------------------------------------------------------------
# 3) resolve_media end-to-end (real YouTube API, best-effort)
# --------------------------------------------------------------------------

YT_KEY = os.environ.get("YOUTUBE_API_KEY", "") or "AIzaSyA6Jdkt1ZuetqTxpyOEwlkbUIJsy0NoKsE"


class TestResolveMediaE2E:
    """Best-effort integration tests using the real YOUTUBE_API_KEY.
    We intentionally point source_url at a dummy so the OG path fails and the
    YouTube search fallback is exercised."""

    def test_a_rejects_anthem_for_party_only_query(self):
        # Uses a plausibly non-existent URL to force the YT search path.
        img, media = asyncio.run(resolve_media(
            title="FI gela FdI: 'Ritirate la controriforma della giustizia'",
            source_url="https://www.repubblica.it/politica/2026/01/01/dummy-nonexistent-page-for-testing.html",
            fallback_image=None,
            youtube_api_key=YT_KEY,
            search_query="Forza Italia Fratelli d'Italia",
        ))
        print(f"[E2E-a] img={img} media={media}")
        # Accept: either media is None, OR media is a YouTube result from a news
        # channel (not a music channel), with no anthem/karaoke words.
        if media is not None:
            title = (media.get("video_title") or "").lower()
            channel = (media.get("channel") or "").lower()
            for neg in _NEGATIVE_TITLE_TOKENS:
                assert neg not in title, f"anthem token '{neg}' leaked through in title={title!r}"
            assert "just italian music" not in channel, f"music channel leaked: {channel}"

    def test_b_briatore_accepts_news(self):
        img, media = asyncio.run(resolve_media(
            title="Briatore attacca il gelato da 95€: 'follia in centro'",
            source_url="https://www.leggo.it/dummy-page-for-testing.html",
            fallback_image=None,
            youtube_api_key=YT_KEY,
            search_query="Flavio Briatore gelatieri",
        ))
        print(f"[E2E-b] img={img} media={media}")
        # We can't guarantee YouTube has this exact video, so skip if nothing
        # was returned — but if returned, must not be music/anthem/karaoke.
        if media is None:
            pytest.skip("no YouTube result — filter may have rejected all candidates")
        assert media.get("type") == "youtube"
        assert (media.get("embed_url") or "").startswith("https://www.youtube-nocookie.com/embed/")
        title = (media.get("video_title") or "").lower()
        for neg in _NEGATIVE_TITLE_TOKENS:
            assert neg not in title, f"neg token '{neg}' leaked in title={title!r}"


# --------------------------------------------------------------------------
# 4) POST /api/admin/backfill_media?force=true
# --------------------------------------------------------------------------

@pytest.fixture(scope="module")
def api_client():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    return s


class TestBackfillForce:
    def test_backfill_force_true_completes(self, api_client):
        r = api_client.post(
            f"{BASE_URL}/api/admin/backfill_media?force=true&limit=50",
            headers={"X-Admin-Key": ADMIN_KEY},
            timeout=180,
        )
        assert r.status_code == 200, r.text
        data = r.json()
        print(f"[Backfill] scanned={data.get('scanned')} updated={data.get('updated')}")
        assert isinstance(data.get("scanned"), int)
        assert isinstance(data.get("updated"), int)

    def _all_feuds(self, api_client):
        feuds = []
        r = api_client.get(f"{BASE_URL}/api/feuds")
        if r.status_code == 200:
            feuds.extend(r.json().get("feuds", []))
        for i in range(1, 8):
            d = (datetime.now(timezone.utc) - timedelta(days=i)).strftime("%Y-%m-%d")
            r = api_client.get(f"{BASE_URL}/api/feuds/archive?date={d}")
            if r.status_code == 200:
                feuds.extend(r.json().get("feuds", []))
        return feuds

    def test_no_music_or_anthem_channel_after_backfill(self, api_client):
        feuds = self._all_feuds(api_client)
        assert feuds, "no feuds in DB"
        bad = []
        for f in feuds:
            m = f.get("media") or {}
            if not isinstance(m, dict):
                continue
            title = (m.get("video_title") or "").lower()
            channel = (m.get("channel") or "").lower()
            if channel == "just italian music":
                bad.append((f["feud_id"], "channel=Just Italian Music"))
            for neg in _NEGATIVE_TITLE_TOKENS:
                if neg in title:
                    bad.append((f["feud_id"], f"neg title token '{neg}' in {title!r}"))
                    break
        print(f"[Backfill-clean] bad={bad}")
        assert not bad, f"found {len(bad)} feuds with disallowed media: {bad}"

    def test_filter_not_too_aggressive(self, api_client):
        feuds = self._all_feuds(api_client)
        yt = [f for f in feuds if isinstance(f.get("media"), dict) and f["media"].get("type") == "youtube"]
        with_src = [f for f in feuds if (f.get("sources") or [])]
        print(f"[Backfill-cover] total={len(feuds)} with_sources={len(with_src)} yt={len(yt)}")
        # Guideline in review request: >=5 YouTube feuds. Also warn if reject rate > 70%.
        if len(with_src) >= 8:
            reject_rate = 1.0 - (len(yt) / max(1, len(with_src)))
            print(f"[Backfill-cover] reject_rate={reject_rate:.2f}")
        assert len(yt) >= 5, (
            f"filter possibly too aggressive: only {len(yt)} YouTube feuds "
            f"out of {len(with_src)} with sources"
        )


# --------------------------------------------------------------------------
# 5) Regressions
# --------------------------------------------------------------------------

class TestRegressions:
    def test_get_nonexistent_feud_returns_410(self, api_client):
        r = api_client.get(f"{BASE_URL}/api/feuds/feud_no_such_thing_9999")
        assert r.status_code == 410

    def test_archive_dates_returns_7day_window(self, api_client):
        r = api_client.get(f"{BASE_URL}/api/feuds/archive/dates")
        assert r.status_code == 200
        dates = r.json().get("dates", [])
        assert isinstance(dates, list)
        # Ensure all returned dates are within the last 8 days
        today = datetime.now(timezone.utc).date()
        for row in dates:
            d = datetime.strptime(row["date"], "%Y-%m-%d").date()
            assert (today - d).days <= 8, f"archive date too old: {row['date']}"
        print(f"[Archive-dates] dates={dates}")

    def test_public_user_history_still_works(self, api_client):
        # Create user, vote on a live feud, verify /users/{id}/history returns snapshot.
        email = f"TEST_yt_{uuid.uuid4().hex[:8]}@ex.com"
        r = api_client.post(f"{BASE_URL}/api/auth/signup", json={
            "email": email, "password": "testpass123", "nickname": f"yt{uuid.uuid4().hex[:6]}",
        })
        assert r.status_code == 200, r.text
        token = r.json()["token"]
        user_id = r.json()["user"]["user_id"]

        r = api_client.get(f"{BASE_URL}/api/feuds")
        feuds = r.json().get("feuds", [])
        if not feuds:
            pytest.skip("no live feud")
        fid = feuds[0]["feud_id"]
        r = api_client.post(
            f"{BASE_URL}/api/feuds/{fid}/vote",
            json={"side": "B"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200, r.text

        r = api_client.get(f"{BASE_URL}/api/users/{user_id}/history")
        assert r.status_code == 200
        hist = r.json().get("history", [])
        assert any(h["feud_id"] == fid for h in hist), "vote not in public history"
