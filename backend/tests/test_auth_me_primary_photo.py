"""Backend tests for `/api/auth/me` primary_photo hydration (iter_68).

Ensures the fix for the "flash del cerchio vuoto" bug works:
    - Users WITHOUT a primary_photo get no `primary_photo` (or null) on `/auth/me`.
    - Users WITH a primary_photo get `primary_photo = {photo_id, data, mime}`.
    - No regression on existing fields (user_id, nickname, primary_photo_id, ...).
    - Endpoint degrades gracefully if `primary_photo_id` points to a
      photo that no longer exists in `user_photos` (no 500, primary_photo absent).

Note: signup with email requires verification (no token returned), so we use:
    - an anonymous account to cover the "no primary_photo" branch, and
    - the seeded chat_a@test.it account (which has photo upload permission)
      to exercise the upload / hydrate / delete flow deterministically.
"""
import base64
import os
import uuid
import pytest
import requests

BASE_URL = os.environ.get(
    "EXPO_PUBLIC_BACKEND_URL",
    "https://feud-governance.preview.emergentagent.com",
).rstrip("/")

USER_A = {"email": "chat_a@test.it", "password": "test123"}

# 1x1 transparent PNG (base64) used as a tiny stand-in for a real photo blob.
TINY_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="
)


def _headers(tok):
    return {"Authorization": f"Bearer {tok}", "Content-Type": "application/json"}


def _login(creds):
    r = requests.post(f"{BASE_URL}/api/auth/login", json=creds, timeout=20)
    assert r.status_code == 200, f"login failed: {r.status_code} {r.text[:200]}"
    d = r.json()
    return d["token"], d["user"]


def _new_anonymous():
    r = requests.post(
        f"{BASE_URL}/api/auth/anonymous",
        json={"nickname": f"iter68_anon_{uuid.uuid4().hex[:6]}"},
        timeout=20,
    )
    assert r.status_code == 200, f"anon signup failed: {r.status_code} {r.text[:200]}"
    d = r.json()
    return d["token"], d["user"]


class TestAuthMeAnonNoPrimaryPhoto:
    """Anonymous account never has a photo — validates the null branch."""

    def test_anon_no_primary_photo(self):
        tok, _ = _new_anonymous()
        r = requests.get(f"{BASE_URL}/api/auth/me", headers=_headers(tok), timeout=20)
        assert r.status_code == 200, r.text[:200]
        user = r.json()["user"]
        pp = user.get("primary_photo")
        assert pp is None, f"expected null primary_photo for fresh anon, got {pp!r}"
        # existing fields preserved (no regression)
        assert user.get("user_id")
        assert user.get("nickname")
        assert "primary_photo_id" in user
        assert user.get("primary_photo_id") in (None, "")


class TestAuthMeUploadHydration:
    """chat_a: upload → verify /auth/me returns primary_photo → cleanup."""

    def test_upload_then_me_hydrates_primary_photo(self):
        tok, _ = _login(USER_A)

        # Snapshot pre-existing photos so we can restore state at the end.
        r = requests.get(f"{BASE_URL}/api/auth/me/photos", headers=_headers(tok), timeout=20)
        assert r.status_code == 200
        pre_photos = r.json().get("photos") or []
        pre_primary = r.json().get("primary_photo_id")

        # Upload a fresh tiny photo
        data_url = f"data:image/png;base64,{TINY_PNG_B64}"
        up = requests.post(
            f"{BASE_URL}/api/auth/me/photos",
            headers=_headers(tok),
            json={"data": data_url},
            timeout=20,
        )
        assert up.status_code == 200, f"upload failed: {up.status_code} {up.text[:200]}"
        new_photo_id = up.json().get("photo_id")
        assert new_photo_id, f"missing photo_id in upload response: {up.json()}"

        # Make sure our new photo is set as primary (so we can predict the shape).
        primary_after_upload = up.json().get("primary_photo_id")
        if primary_after_upload != new_photo_id:
            sp = requests.patch(
                f"{BASE_URL}/api/auth/me/photos/{new_photo_id}/primary",
                headers=_headers(tok),
                timeout=20,
            )
            assert sp.status_code == 200, sp.text[:200]

        try:
            me = requests.get(f"{BASE_URL}/api/auth/me", headers=_headers(tok), timeout=20)
            assert me.status_code == 200, me.text[:200]
            user = me.json()["user"]

            pp = user.get("primary_photo")
            assert pp is not None, "primary_photo should be present after upload"
            assert isinstance(pp, dict)
            assert pp.get("photo_id") == new_photo_id
            assert isinstance(pp.get("data"), str) and len(pp["data"]) > 20
            assert pp.get("mime"), "mime should be present"
            # data must be either a data URL or raw base64
            assert pp["data"].startswith("data:") or _is_base64ish(pp["data"])
            assert user.get("primary_photo_id") == new_photo_id
            # regression: core fields preserved
            assert user.get("user_id")
            assert user.get("nickname")
        finally:
            # cleanup: delete the uploaded photo and, if possible, restore the previous primary.
            requests.delete(
                f"{BASE_URL}/api/auth/me/photos/{new_photo_id}",
                headers=_headers(tok),
                timeout=20,
            )
            if pre_primary and pre_primary != new_photo_id:
                # If the prior primary still exists in pre_photos, re-mark it as primary.
                try:
                    requests.patch(
                        f"{BASE_URL}/api/auth/me/photos/{pre_primary}/primary",
                        headers=_headers(tok),
                        timeout=20,
                    )
                except Exception:
                    pass


class TestAuthMeGracefulDegradeAfterDelete:
    """After deleting the primary photo the endpoint must still return 200
    and primary_photo must be null (server clears/re-assigns primary_photo_id)."""

    def test_delete_primary_no_500(self):
        tok, _ = _login(USER_A)

        # Snapshot state
        pre = requests.get(f"{BASE_URL}/api/auth/me/photos", headers=_headers(tok), timeout=20).json()
        pre_photos = pre.get("photos") or []
        pre_primary = pre.get("primary_photo_id")

        # Upload throwaway photo, force it as primary, then delete it.
        up = requests.post(
            f"{BASE_URL}/api/auth/me/photos",
            headers=_headers(tok),
            json={"data": f"data:image/png;base64,{TINY_PNG_B64}"},
            timeout=20,
        )
        assert up.status_code == 200
        pid = up.json()["photo_id"]
        requests.patch(
            f"{BASE_URL}/api/auth/me/photos/{pid}/primary",
            headers=_headers(tok),
            timeout=20,
        )
        try:
            requests.delete(
                f"{BASE_URL}/api/auth/me/photos/{pid}",
                headers=_headers(tok),
                timeout=20,
            )

            me = requests.get(f"{BASE_URL}/api/auth/me", headers=_headers(tok), timeout=20)
            assert me.status_code == 200, me.text[:200]
            user = me.json()["user"]
            # If there were no other photos before, primary_photo should now be null.
            # If there were photos before, server may re-assign primary — still no 500.
            pp = user.get("primary_photo")
            if not pre_photos:
                assert pp is None, f"expected null primary_photo, got {pp!r}"
            else:
                # There may be a valid primary photo hydrated. Just make sure the
                # shape is sane and no crash.
                if pp is not None:
                    assert isinstance(pp, dict)
                    assert pp.get("photo_id")
                    assert isinstance(pp.get("data"), str)
        finally:
            # Restore prior primary if it still exists
            if pre_primary:
                try:
                    requests.patch(
                        f"{BASE_URL}/api/auth/me/photos/{pre_primary}/primary",
                        headers=_headers(tok),
                        timeout=20,
                    )
                except Exception:
                    pass


class TestAuthMeRegression:
    """No regression on the existing user payload shape."""

    def test_me_returns_expected_fields(self):
        tok, _ = _login(USER_A)
        r = requests.get(f"{BASE_URL}/api/auth/me", headers=_headers(tok), timeout=20)
        assert r.status_code == 200
        user = r.json()["user"]
        # Fields the app relies on
        for k in ("user_id", "nickname", "auth_provider"):
            assert k in user, f"missing field {k!r} in /auth/me user payload"
        # primary_photo_id must still be present (may be null)
        assert "primary_photo_id" in user


def _is_base64ish(s: str) -> bool:
    try:
        base64.b64decode(s[:64] + "==")
        return True
    except Exception:
        return False
