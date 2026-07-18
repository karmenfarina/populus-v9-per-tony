"""Backend tests for iteration 4:
- GET /api/share/{feud_id}/html (OpenGraph + visible preview)
- Comment/reply moderation with BLOCKED_WORDS + db.flagged_comments logging
- RSS cache (/api/admin/generate-daily fast on 2nd call)
- Regressions: /api/share/{id} JSON, clean comment/reply, search.
"""
import os
import time
import uuid
import pytest
import requests

BASE_URL = os.environ.get('EXPO_PUBLIC_BACKEND_URL', 'https://faide-poll.preview.emergentagent.com').rstrip('/')
API = f"{BASE_URL}/api"


@pytest.fixture(scope='module')
def s():
    sess = requests.Session()
    sess.headers.update({'Content-Type': 'application/json'})
    return sess


@pytest.fixture(scope='module')
def anon(s):
    r = s.post(f"{API}/auth/anonymous", json={'nickname': f'TEST_M_{uuid.uuid4().hex[:6]}'})
    assert r.status_code == 200, r.text
    tok = r.json()['token']
    uid = r.json()['user']['user_id']
    return {'token': tok, 'user_id': uid, 'hdr': {'Authorization': f'Bearer {tok}', 'Content-Type': 'application/json'}}


@pytest.fixture(scope='module')
def voted_feud(s, anon):
    r = s.get(f"{API}/feuds", headers=anon['hdr'])
    assert r.status_code == 200
    feuds = r.json()['feuds']
    unvoted = [f for f in feuds if f.get('my_vote') is None]
    assert unvoted, "expected at least one unvoted feud"
    fid = unvoted[0]['feud_id']
    rv = s.post(f"{API}/feuds/{fid}/vote", json={'side': 'A'}, headers=anon['hdr'])
    assert rv.status_code == 200, rv.text
    return {'feud_id': fid, 'feud': unvoted[0]}


# ------------------- OG HTML Share -------------------
def test_share_html_returns_200_and_html_ctype(s):
    r0 = s.get(f"{API}/feuds")
    fid = r0.json()['feuds'][0]['feud_id']
    r = s.get(f"{API}/share/{fid}/html")
    assert r.status_code == 200, r.text
    ct = r.headers.get('content-type', '').lower()
    assert 'text/html' in ct, f"expected text/html, got {ct}"


def test_share_html_has_og_and_twitter_meta_and_content(s):
    r0 = s.get(f"{API}/feuds")
    feud = r0.json()['feuds'][0]
    fid = feud['feud_id']
    # get the revealed JSON share to know pct/values expected
    rj = s.get(f"{API}/share/{fid}").json()['feud']
    r = s.get(f"{API}/share/{fid}/html")
    body = r.text
    # OG meta tags
    assert '<meta property="og:title"' in body
    assert '<meta property="og:description"' in body
    assert '<meta property="og:image"' in body
    assert '<meta property="og:type"' in body
    assert '<meta property="og:url"' in body
    # Twitter card
    assert '<meta name="twitter:card" content="summary_large_image"' in body
    assert '<meta name="twitter:title"' in body
    assert '<meta name="twitter:image"' in body
    # Visible content
    import html as html_lib
    # title/party_a/party_b appear (escaped)
    assert html_lib.escape(rj['title']) in body
    assert html_lib.escape(rj['party_a']) in body
    assert html_lib.escape(rj['party_b']) in body
    # percentages rendered
    assert f">{rj['pct_a']}%<" in body
    assert f">{rj['pct_b']}%<" in body
    # Populus header + CTA
    assert 'POPULUS' in body
    assert 'APRI POPULUS' in body


def test_share_html_404_for_unknown(s):
    r = s.get(f"{API}/share/feud_deadbeefzz/html")
    assert r.status_code == 404


# ------------------- Moderation -------------------
def test_comment_clean_ok(s, anon, voted_feud):
    fid = voted_feud['feud_id']
    r = s.post(f"{API}/feuds/{fid}/comments", json={'text': 'Bel commento'}, headers=anon['hdr'])
    assert r.status_code == 200, r.text
    c = r.json()['comment']
    assert c['text'] == 'Bel commento'
    assert c['side'] in ('A', 'B')
    assert c.get('comment_id', '').startswith('cmt_')


def test_comment_blocked_returns_400_with_words(s, anon, voted_feud):
    fid = voted_feud['feud_id']
    txt = 'sei un coglione vaffanculo'
    r = s.post(f"{API}/feuds/{fid}/comments", json={'text': txt}, headers=anon['hdr'])
    assert r.status_code == 400, r.text
    detail = r.json().get('detail', '')
    assert 'Commento bloccato' in detail
    assert 'coglione' in detail
    assert 'vaffanculo' in detail


def test_reply_blocked_returns_400(s, anon, voted_feud):
    fid = voted_feud['feud_id']
    # create a parent clean comment first
    rc = s.post(f"{API}/feuds/{fid}/comments", json={'text': 'commento base per replies'}, headers=anon['hdr'])
    assert rc.status_code == 200, rc.text
    cid = rc.json()['comment']['comment_id']

    r = s.post(f"{API}/comments/{cid}/replies",
               json={'text': 'stronzo, sei un idiota di merda'}, headers=anon['hdr'])
    assert r.status_code == 400, r.text
    detail = r.json().get('detail', '')
    assert 'Risposta bloccata' in detail or 'Commento bloccato' in detail
    assert 'stronzo' in detail


def test_reply_clean_ok(s, anon, voted_feud):
    fid = voted_feud['feud_id']
    rc = s.post(f"{API}/feuds/{fid}/comments", json={'text': 'altro commento pulito'}, headers=anon['hdr'])
    cid = rc.json()['comment']['comment_id']
    r = s.post(f"{API}/comments/{cid}/replies",
               json={'text': 'sono daccordo con te'}, headers=anon['hdr'])
    assert r.status_code == 200, r.text
    rep = r.json()['reply']
    assert rep['text'] == 'sono daccordo con te'
    assert rep.get('reply_id', '').startswith('rep_')


def test_flagged_comment_logged_in_db(s, anon, voted_feud):
    """Trigger a fresh block and verify db.flagged_comments has a record for this user."""
    fid = voted_feud['feud_id']
    marker = f"puttana troia TEST_{uuid.uuid4().hex[:6]}"
    r = s.post(f"{API}/feuds/{fid}/comments", json={'text': marker}, headers=anon['hdr'])
    assert r.status_code == 400
    # Verify via direct MongoDB read
    from motor.motor_asyncio import AsyncIOMotorClient
    import asyncio
    mongo_url = os.environ.get('MONGO_URL', 'mongodb://localhost:27017')
    db_name = os.environ.get('DB_NAME', 'test_database')

    async def _check():
        cli = AsyncIOMotorClient(mongo_url)
        try:
            db = cli[db_name]
            doc = await db.flagged_comments.find_one(
                {'user_id': anon['user_id'], 'text': marker}, {'_id': 0}
            )
            return doc
        finally:
            cli.close()

    doc = asyncio.get_event_loop().run_until_complete(_check())
    assert doc is not None, "expected flagged_comments doc for this user/text"
    assert doc.get('feud_id') == fid
    assert 'puttana' in doc.get('hits', []) or 'troia' in doc.get('hits', [])
    assert doc.get('flag_id', '').startswith('flag_')


# ------------------- RSS cache -------------------
def test_generate_daily_second_call_fast(s):
    """Second call to /api/admin/generate-daily?count=1 should benefit from RSS cache.
    We time it and require second call < first call + a generous cap (<= 60s)."""
    t0 = time.time()
    r1 = s.post(f"{API}/admin/generate-daily", params={'count': 1}, timeout=120)
    t1 = time.time() - t0
    assert r1.status_code == 200, r1.text

    t0 = time.time()
    r2 = s.post(f"{API}/admin/generate-daily", params={'count': 1}, timeout=120)
    t2 = time.time() - t0
    assert r2.status_code == 200, r2.text
    # sanity: second call finishes within reasonable time
    assert t2 <= 60, f"second call took {t2:.1f}s (>60s), cache may not be effective"
    print(f"generate-daily timings: first={t1:.1f}s second={t2:.1f}s")


# ------------------- Regressions -------------------
def test_regression_share_json_still_works(s):
    r0 = s.get(f"{API}/feuds")
    fid = r0.json()['feuds'][0]['feud_id']
    r = s.get(f"{API}/share/{fid}")
    assert r.status_code == 200
    body = r.json()
    assert 'feud' in body
    feud = body['feud']
    assert feud['revealed'] is True
    assert feud['my_vote'] is None
    assert feud.get('pct_a') is not None and feud.get('pct_b') is not None


def test_regression_search_still_works(s):
    r = s.get(f"{API}/search", params={'q': 'Ferragni'})
    assert r.status_code == 200
    assert len(r.json()['feuds']) >= 1
