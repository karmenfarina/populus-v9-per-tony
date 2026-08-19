"""Backend tests for profile customization (bio, socials, photos, public user)."""
import os
import base64
import uuid
import pytest
import requests

BASE_URL = os.environ.get("EXPO_PUBLIC_BACKEND_URL", "https://bot-burst-fix.preview.emergentagent.com").rstrip("/")
API = f"{BASE_URL}/api"

# ~1KB base64 payload
TINY_B64 = base64.b64encode(b"A" * 1024).decode()


@pytest.fixture(scope="module")
def user_ctx():
    nick = f"pctest_{uuid.uuid4().hex[:6]}"
    r = requests.post(f"{API}/auth/anonymous", json={"nickname": nick}, timeout=15)
    assert r.status_code == 200, r.text
    data = r.json()
    return {"token": data["token"], "user": data["user"], "headers": {"Authorization": f"Bearer {data['token']}"}}


# --- PATCH /auth/me/details ---
class TestUpdateDetails:
    def test_missing_auth_returns_401(self):
        r = requests.patch(f"{API}/auth/me/details", json={"bio": "x"}, timeout=15)
        assert r.status_code == 401

    def test_update_bio_and_social_links_sanitizes(self, user_ctx):
        body = {"bio": "Ciao", "social_links": {"instagram": "user1", "website": "example.it", "invalid_key": "x"}}
        r = requests.patch(f"{API}/auth/me/details", headers=user_ctx["headers"], json=body, timeout=15)
        assert r.status_code == 200, r.text
        u = r.json()["user"]
        assert u["bio"] == "Ciao"
        sl = u["social_links"]
        assert "invalid_key" not in sl
        assert "instagram" in sl and sl["instagram"].startswith("https://")
        assert "website" in sl and sl["website"].startswith("https://")


# --- Photos flow ---
class TestPhotos:
    def test_first_upload_sets_primary(self, user_ctx):
        r = requests.post(f"{API}/auth/me/photos", headers=user_ctx["headers"], json={"data": TINY_B64}, timeout=20)
        assert r.status_code == 200, r.text
        data = r.json()
        assert "photo_id" in data
        assert data["primary_photo_id"] == data["photo_id"]
        user_ctx["first_photo_id"] = data["photo_id"]

        me = requests.get(f"{API}/auth/me", headers=user_ctx["headers"], timeout=15).json()["user"]
        assert me["primary_photo_id"] == data["photo_id"]
        assert me["photos_count"] == 1

    def test_upload_more_and_switch_primary(self, user_ctx):
        # Upload 2nd photo
        r2 = requests.post(f"{API}/auth/me/photos", headers=user_ctx["headers"], json={"data": TINY_B64}, timeout=20)
        assert r2.status_code == 200
        pid2 = r2.json()["photo_id"]
        user_ctx["second_photo_id"] = pid2

        # Switch primary
        r = requests.patch(f"{API}/auth/me/photos/{pid2}/primary", headers=user_ctx["headers"], timeout=15)
        assert r.status_code == 200
        assert r.json()["primary_photo_id"] == pid2
        me = requests.get(f"{API}/auth/me", headers=user_ctx["headers"], timeout=15).json()["user"]
        assert me["primary_photo_id"] == pid2

    def test_eighth_upload_rejected(self, user_ctx):
        # Already 2 uploaded; upload 5 more to reach 7
        for _ in range(5):
            rr = requests.post(f"{API}/auth/me/photos", headers=user_ctx["headers"], json={"data": TINY_B64}, timeout=20)
            assert rr.status_code == 200, rr.text
        # 8th
        r = requests.post(f"{API}/auth/me/photos", headers=user_ctx["headers"], json={"data": TINY_B64}, timeout=20)
        assert r.status_code == 400
        assert "Massimo 7 foto totali" in r.json().get("detail", "")

    def test_delete_primary_reassigns(self, user_ctx):
        pid2 = user_ctx["second_photo_id"]  # currently primary
        r = requests.delete(f"{API}/auth/me/photos/{pid2}", headers=user_ctx["headers"], timeout=15)
        assert r.status_code == 200
        new_primary = r.json().get("primary_photo_id")
        assert new_primary is not None and new_primary != pid2
        me = requests.get(f"{API}/auth/me", headers=user_ctx["headers"], timeout=15).json()["user"]
        assert me["primary_photo_id"] == new_primary
        assert me["photos_count"] == 6

    def test_delete_all_makes_primary_null(self):
        # Fresh user
        nick = f"pctest_{uuid.uuid4().hex[:6]}"
        auth = requests.post(f"{API}/auth/anonymous", json={"nickname": nick}, timeout=15).json()
        headers = {"Authorization": f"Bearer {auth['token']}"}
        up = requests.post(f"{API}/auth/me/photos", headers=headers, json={"data": TINY_B64}, timeout=20).json()
        pid = up["photo_id"]
        r = requests.delete(f"{API}/auth/me/photos/{pid}", headers=headers, timeout=15)
        assert r.status_code == 200
        assert r.json().get("primary_photo_id") is None
        me = requests.get(f"{API}/auth/me", headers=headers, timeout=15).json()["user"]
        assert me["primary_photo_id"] is None
        assert me["photos_count"] == 0


# --- Public user endpoint ---
class TestPublicUser:
    def test_get_public_user(self, user_ctx):
        uid = user_ctx["user"]["user_id"]
        # no auth
        r = requests.get(f"{API}/users/{uid}", timeout=15)
        assert r.status_code == 200
        data = r.json()
        assert data["nickname"] == user_ctx["user"]["nickname"]
        assert "bio" in data and "social_links" in data
        assert isinstance(data["photos"], list)
        assert "primary_photo_id" in data
        assert "badge" in data
        assert "total_votes" in data
        if data["photos"]:
            assert "data" in data["photos"][0]

    def test_unknown_user_404(self):
        r = requests.get(f"{API}/users/nonexistent_xyz_123", timeout=15)
        assert r.status_code == 404
