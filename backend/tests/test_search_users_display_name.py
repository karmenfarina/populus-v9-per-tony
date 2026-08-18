"""Backend spot-check for GET /api/search/users after the addition
of the `display_name` field (used by the new Cerca Amici screen)."""

import os
import pytest
import requests

BASE_URL = os.environ.get('EXPO_PUBLIC_BACKEND_URL', 'https://feud-admin-panel.preview.emergentagent.com').rstrip('/')


@pytest.fixture(scope='module')
def token_a():
    r = requests.post(f"{BASE_URL}/api/auth/login",
                      json={"email": "chat_a@test.it", "password": "test123"},
                      timeout=15)
    assert r.status_code == 200, r.text
    return r.json()['token']


@pytest.fixture
def api(token_a):
    s = requests.Session()
    s.headers.update({"Authorization": f"Bearer {token_a}", "Content-Type": "application/json"})
    return s


# --- /api/search/users --------------------------------------------------
class TestSearchUsers:
    """Ensure the search payload exposes the fields consumed by the
    Cerca Amici screen: user_id, nickname, display_name, photo_data."""

    def test_search_returns_display_name_field(self, api):
        r = api.get(f"{BASE_URL}/api/search/users", params={"q": "chat", "limit": 20})
        assert r.status_code == 200
        data = r.json()
        assert 'users' in data
        users = data['users']
        assert isinstance(users, list)
        assert len(users) >= 1, "expected at least chatUserB in results for 'chat'"
        for u in users:
            assert 'user_id' in u, u
            assert 'nickname' in u, u
            assert 'display_name' in u, u  # new required key
            assert 'photo_data' in u, u
            # display_name can be null but the key MUST be present
            assert u['display_name'] is None or isinstance(u['display_name'], str)

    def test_search_excludes_self(self, api):
        """chat_a searching for 'chat' should NOT get their own row back."""
        r = api.get(f"{BASE_URL}/api/search/users", params={"q": "chatUserA", "limit": 20})
        assert r.status_code == 200
        for u in r.json()['users']:
            assert u['user_id'] != 'user_6e65e19525d5'

    def test_search_no_results(self, api):
        r = api.get(f"{BASE_URL}/api/search/users", params={"q": "zzzxxxqqq123nomatch", "limit": 20})
        assert r.status_code == 200
        assert r.json() == {'users': []}

    def test_search_requires_auth(self):
        r = requests.get(f"{BASE_URL}/api/search/users", params={"q": "chat"}, timeout=15)
        assert r.status_code in (401, 403)

    def test_search_finds_userB_for_partial(self, api):
        r = api.get(f"{BASE_URL}/api/search/users", params={"q": "user", "limit": 20})
        assert r.status_code == 200
        nicknames = [u['nickname'] for u in r.json()['users']]
        assert 'chatUserB' in nicknames
