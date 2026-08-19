"""
Iter 151 (b) — In-process test of `_send_verification_email` with a
FRONTEND_BASE_URL configured. Confirms that when the module-level
`FRONTEND_BASE_URL` attribute is non-empty, the function reaches the
Resend HTTP branch (mocked) and generates an absolute link.

We deliberately mock httpx AsyncClient so no real email leaves the
sandbox. pytest-asyncio is in `auto` mode (see pytest.ini) so `async
def` tests run inside a managed event loop.
"""

import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.fixture(scope='session')
def event_loop():
    # Session-scoped loop so Motor's client (created at server import) stays
    # bound to a single, still-open loop across all tests.
    import asyncio
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()

sys.path.insert(0, '/app/backend')
os.environ.setdefault('MONGO_URL', 'mongodb://localhost:27017')
os.environ.setdefault('DB_NAME', 'test_database')


@pytest.fixture(scope='module')
def server_mod():
    import server  # noqa: F401
    return server


def test_module_reads_base_url_on_import(server_mod):
    # Sanity: current preview has no FRONTEND_BASE_URL / EXPO_PUBLIC_BACKEND_URL
    # in the backend process, so the constant should be ''.
    assert server_mod.FRONTEND_BASE_URL == '', (
        f'expected empty base URL in current preview env, got '
        f'{server_mod.FRONTEND_BASE_URL!r}'
    )


async def test_send_verification_email_missing_base_logs_warning_and_returns(server_mod, caplog):
    """When FRONTEND_BASE_URL='' the function must return WITHOUT calling
    Resend and log the missing-base warning."""
    fake_tokens = MagicMock()
    fake_tokens.delete_many = AsyncMock()
    fake_tokens.insert_one = AsyncMock()
    with caplog.at_level('WARNING'):
        with patch.object(server_mod, 'FRONTEND_BASE_URL', ''), \
             patch.dict(server_mod.db._collections if hasattr(server_mod.db, '_collections') else {}, {}, clear=False), \
             patch.object(server_mod, 'db', MagicMock(verification_tokens=fake_tokens)), \
             patch('httpx.AsyncClient') as mock_client:
            await server_mod._send_verification_email(
                user_id='user_iter151_missing',
                email='TEST_missing@example.com',
            )
            mock_client.assert_not_called()

    assert any('FRONTEND_BASE_URL missing' in rec.message for rec in caplog.records), (
        f'expected FRONTEND_BASE_URL warning; got: {[r.message for r in caplog.records]}'
    )


async def test_send_verification_email_with_base_calls_resend(server_mod):
    """When FRONTEND_BASE_URL is set AND RESEND_API_KEY present, the
    function must POST to https://api.resend.com/emails with an ABSOLUTE
    link containing the base URL and the raw token."""
    captured = {}

    class _FakeResp:
        status_code = 200
        text = 'ok'

    class _FakeClient:
        def __init__(self, *a, **kw):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            return False
        async def post(self, url, headers=None, json=None):
            captured['url'] = url
            captured['headers'] = headers
            captured['json'] = json
            return _FakeResp()

    fake_tokens = MagicMock()
    fake_tokens.delete_many = AsyncMock()
    fake_tokens.insert_one = AsyncMock()
    with patch.object(server_mod, 'FRONTEND_BASE_URL', 'https://app.example.com'), \
         patch.object(server_mod, 'RESEND_API_KEY', 'test-resend-key'), \
         patch.object(server_mod, 'db', MagicMock(verification_tokens=fake_tokens)), \
         patch('httpx.AsyncClient', _FakeClient):
        await server_mod._send_verification_email(
            user_id='user_iter151_present',
            email='TEST_present@example.com',
        )

    assert captured.get('url') == 'https://api.resend.com/emails'
    body = captured.get('json') or {}
    html_body = body.get('html', '')
    assert 'https://app.example.com/verify-email?token=' in html_body, (
        f'expected absolute verify-email link, got: {html_body[:300]}'
    )
    assert body.get('to') == ['TEST_present@example.com']
    assert 'Verifica' in (body.get('subject') or '')


async def test_send_verification_email_missing_resend_key(server_mod):
    """Sanity guard for the third branch: base set but no Resend key
    → warning, no HTTP call."""
    fake_tokens = MagicMock()
    fake_tokens.delete_many = AsyncMock()
    fake_tokens.insert_one = AsyncMock()
    with patch.object(server_mod, 'FRONTEND_BASE_URL', 'https://app.example.com'), \
         patch.object(server_mod, 'RESEND_API_KEY', ''), \
         patch.object(server_mod, 'db', MagicMock(verification_tokens=fake_tokens)), \
         patch('httpx.AsyncClient') as mock_client:
        await server_mod._send_verification_email(
            user_id='user_iter151_nokey',
            email='TEST_nokey@example.com',
        )
        mock_client.assert_not_called()
