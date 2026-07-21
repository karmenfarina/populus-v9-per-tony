"""Spot-check tests for Cerchia (circle) endpoints.

Endpoints under test:
- GET  /api/users/{owner_id}/circle
- POST /api/circle/{friend_id}   (add)
- DELETE /api/circle/{friend_id} (remove)
- GET  /api/circle/me/status/{other_user_id}
"""
import os
import uuid
import pytest
import requests

BASE_URL = os.environ.get("EXPO_BACKEND_URL").rstrip("/")


USER_A = {"email": "chat_a@test.it", "password": "test123"}
USER_B = {"email": "chat_b@test.it", "password": "test123"}


def _login(creds: dict) -> dict:
    """Login as a pre-verified test user."""
    r = requests.post(f"{BASE_URL}/api/auth/login", json=creds, timeout=20)
    assert r.status_code == 200, r.text
    j = r.json()
    assert "token" in j and "user" in j
    return j


@pytest.fixture(scope="module")
def actors():
    a = _login(USER_A)
    b = _login(USER_B)
    ha = {"Authorization": f"Bearer {a['token']}"}
    hb = {"Authorization": f"Bearer {b['token']}"}
    # Best-effort cleanup: ensure b is not in a's circle at start
    requests.delete(
        f"{BASE_URL}/api/circle/{b['user']['user_id']}", headers=ha, timeout=20,
    )
    return {"a": a, "b": b, "ha": ha, "hb": hb}


def test_get_own_circle_empty(actors):
    r = requests.get(f"{BASE_URL}/api/users/{actors['a']['user']['user_id']}/circle",
                     headers=actors["ha"], timeout=20)
    assert r.status_code == 200, r.text
    j = r.json()
    assert j.get("is_owner") is True
    assert j.get("count") == 0
    assert isinstance(j.get("members"), list)
    assert j.get("max", 0) >= 1


def test_circle_status_before_add(actors):
    r = requests.get(
        f"{BASE_URL}/api/circle/me/status/{actors['b']['user']['user_id']}",
        headers=actors["ha"], timeout=20,
    )
    assert r.status_code == 200
    assert r.json().get("in_circle") is False


def test_circle_add(actors):
    r = requests.post(
        f"{BASE_URL}/api/circle/{actors['b']['user']['user_id']}",
        headers=actors["ha"], timeout=20,
    )
    assert r.status_code == 200, r.text
    j = r.json()
    assert j.get("in_circle") is True
    assert j.get("count") >= 1


def test_get_circle_after_add(actors):
    r = requests.get(f"{BASE_URL}/api/users/{actors['a']['user']['user_id']}/circle",
                     headers=actors["ha"], timeout=20)
    assert r.status_code == 200
    j = r.json()
    assert j.get("count") == 1
    ids = [m.get("user_id") for m in j.get("members", [])]
    assert actors["b"]["user"]["user_id"] in ids


def test_circle_status_after_add(actors):
    r = requests.get(
        f"{BASE_URL}/api/circle/me/status/{actors['b']['user']['user_id']}",
        headers=actors["ha"], timeout=20,
    )
    assert r.status_code == 200
    assert r.json().get("in_circle") is True


def test_circle_remove(actors):
    r = requests.delete(
        f"{BASE_URL}/api/circle/{actors['b']['user']['user_id']}",
        headers=actors["ha"], timeout=20,
    )
    assert r.status_code == 200, r.text
    j = r.json()
    assert j.get("in_circle") is False
    assert j.get("count") == 0


def test_get_circle_after_remove(actors):
    r = requests.get(f"{BASE_URL}/api/users/{actors['a']['user']['user_id']}/circle",
                     headers=actors["ha"], timeout=20)
    assert r.status_code == 200
    j = r.json()
    assert j.get("count") == 0
    ids = [m.get("user_id") for m in j.get("members", [])]
    assert actors["b"]["user"]["user_id"] not in ids
