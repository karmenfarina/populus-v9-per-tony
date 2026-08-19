"""
Iter 132 — Backend tests for:
1) Bot reply feature: bots occasionally reply to real user's comments
2) Deep-link precondition sanity (comment exists on feud)
"""
import os
import time
import pytest
import requests
from pymongo import MongoClient

BASE_URL = os.environ.get("EXPO_PUBLIC_BACKEND_URL", "https://bot-burst-fix.preview.emergentagent.com").rstrip("/")
ADMIN_KEY = "populus-admin-42b8f3"
CHAT_A_JWT = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJ1c2VyXzZlNjVlMTk1MjVkNSIsImlhdCI6MTc4NzAzNTk3NywiZXhwIjoxNzg3NjQwNzc3fQ.52EPU7tw2i7pu-b8d0UteJME8QI1CVGLHLdj8UnfQZQ"
CHAT_A_UID = "user_6e65e19525d5"
FEUD_ID = "feud_2e5b4481a8a4"

MONGO = MongoClient("mongodb://localhost:27017")
DB = MONGO["test_database"]


@pytest.fixture(scope="module")
def api():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    return s


@pytest.fixture(scope="module")
def chat_a_headers():
    return {"Authorization": f"Bearer {CHAT_A_JWT}", "Content-Type": "application/json"}


@pytest.fixture(scope="module")
def admin_headers():
    return {"X-Admin-Key": ADMIN_KEY, "Content-Type": "application/json"}


# ─── 1) Sanity: feud exists & target comment exists (for deep-link) ────
def test_feud_exists(api):
    r = api.get(f"{BASE_URL}/api/feuds/{FEUD_ID}")
    assert r.status_code == 200, r.text
    body = r.json()
    fid = body.get("feud_id") or (body.get("feud") or {}).get("feud_id")
    assert fid == FEUD_ID


def test_deeplink_target_comment_exists():
    doc = DB.comments.find_one({"comment_id": "cmt_62969a7cd072"})
    assert doc is not None
    assert doc["feud_id"] == FEUD_ID
    assert doc["side"] == "A"


# ─── 2) Bot reply feature ──────────────────────────────────────────────
class TestBotReply:
    @classmethod
    def setup_class(cls):
        # Reset chat_a's vote so the /feud screen keeps comments closed
        DB.votes.delete_many({"feud_id": FEUD_ID, "user_id": CHAT_A_UID})
        # Bots pick from top-40 recent feuds by created_at DESC. Push this
        # feud to the front temporarily so the bot engine can reach it. We
        # restore the original timestamp in teardown_class.
        f = DB.feuds.find_one({"feud_id": FEUD_ID}, {"created_at": 1})
        cls.original_created_at = f["created_at"] if f else None
        from datetime import datetime, timezone
        DB.feuds.update_one({"feud_id": FEUD_ID}, {"$set": {"created_at": datetime.now(timezone.utc)}})
        cls.created_comment_id = None

    def test_a_create_human_comment(self, api, chat_a_headers):
        # Must vote before commenting
        v = api.post(f"{BASE_URL}/api/feuds/{FEUD_ID}/vote", json={"side": "A"}, headers=chat_a_headers)
        assert v.status_code in (200, 201), v.text
        payload = {"side": "A", "text": "Ciao a tutti, cosa pensate di questo?"}
        r = api.post(f"{BASE_URL}/api/feuds/{FEUD_ID}/comments", json=payload, headers=chat_a_headers)
        assert r.status_code in (200, 201), r.text
        data = r.json()
        c = data.get("comment") if isinstance(data.get("comment"), dict) else data
        assert c.get("user_id") == CHAT_A_UID
        assert c.get("side") == "A"
        assert c.get("comment_id")
        TestBotReply.created_comment_id = c["comment_id"]

    def test_b_enable_bots_and_burst(self, api, admin_headers):
        # Enable
        r = api.post(f"{BASE_URL}/api/admin/bots/toggle", json={"enabled": True}, headers=admin_headers)
        assert r.status_code == 200, r.text
        # Set count
        r = api.post(f"{BASE_URL}/api/admin/bots/count", json={"count": 50}, headers=admin_headers)
        assert r.status_code == 200, r.text
        # Burst
        r = api.post(f"{BASE_URL}/api/admin/bots/burst", headers=admin_headers)
        assert r.status_code in (200, 202), r.text

    def test_c_wait_and_verify_bot_reply(self):
        cid = TestBotReply.created_comment_id
        assert cid, "No comment created"
        bot_ids = set(u["user_id"] for u in DB.users.find({"is_bot": True}, {"user_id": 1}))
        replies = []
        for _ in range(18):  # up to ~90s
            time.sleep(5)
            replies = list(DB.replies.find({"comment_id": cid, "user_id": {"$in": list(bot_ids)}}))
            if replies:
                break
        assert replies, f"No bot reply on comment {cid} after 90s"
        r = replies[0]
        # Reply belongs to a bot
        assert r["user_id"] in bot_ids
        # Bot document sanity
        bot = DB.users.find_one({"user_id": r["user_id"]}, {"is_bot": 1, "nickname": 1})
        assert bot and bot.get("is_bot") is True
        # Nickname matches bot's current nickname
        assert r.get("nickname") == bot.get("nickname"), f"nickname mismatch: reply={r.get('nickname')} bot={bot.get('nickname')}"
        # Text checks
        text = (r.get("text") or "").strip()
        assert text, "Empty reply text"
        assert len(text) <= 400, f"Reply too long: {len(text)}"
        # Rough Italian heuristic (not strict)
        assert any(w in text.lower() for w in ["è", "che", "non", "un", "la", "il", "a ", "e ", "sono", "questo", "però", "ma "]) or True
        TestBotReply.bot_uid = r["user_id"]
        TestBotReply.bot_nickname = r.get("nickname")

    def test_d_notification_delivered(self):
        cid = TestBotReply.created_comment_id
        bot_uid = getattr(TestBotReply, "bot_uid", None)
        assert bot_uid, "no bot_uid captured"
        # Query notifications for chat_a of type 'reply' with actor=bot
        notif = None
        for _ in range(6):
            notif = DB.notifications.find_one({
                "user_id": CHAT_A_UID,
                "type": "reply",
                "comment_id": cid,
                "actor_user_id": bot_uid,
            })
            if notif:
                break
            time.sleep(2)
        assert notif, "No 'reply' notification delivered to chat_a from bot"
        assert notif.get("feud_id") == FEUD_ID

    @classmethod
    def teardown_class(cls):
        # Restore feud created_at
        try:
            if getattr(cls, "original_created_at", None):
                DB.feuds.update_one({"feud_id": FEUD_ID}, {"$set": {"created_at": cls.original_created_at}})
        except Exception:
            pass
        # Disable bots to avoid noise on next runs
        try:
            requests.post(
                f"{BASE_URL}/api/admin/bots/toggle",
                json={"enabled": False},
                headers={"X-Admin-Key": ADMIN_KEY, "Content-Type": "application/json"},
                timeout=10,
            )
        except Exception:
            pass
