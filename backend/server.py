from fastapi import FastAPI, APIRouter, Header, HTTPException, Depends, Request, WebSocket, WebSocketDisconnect, Query, Body
from fastapi.responses import HTMLResponse
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
import os
import logging
import uuid
import bcrypt
import jwt
import httpx
import json
import re
import time
import html as html_lib
import re as _re
import feedparser
import asyncio
from pathlib import Path
from pydantic import BaseModel, Field, EmailStr
from typing import List, Optional, Literal
from datetime import datetime, timezone, timedelta
from media_extractor import resolve_media as _resolve_media

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

mongo_url = os.environ['MONGO_URL']
client = AsyncIOMotorClient(mongo_url)
db = client[os.environ['DB_NAME']]

JWT_SECRET = os.environ.get('JWT_SECRET', 'dev-secret-change')
JWT_ALG = 'HS256'
JWT_TTL_DAYS = 7
EMERGENT_LLM_KEY = os.environ.get('EMERGENT_LLM_KEY')
ADMIN_TOKEN = os.environ.get('ADMIN_TOKEN', '')

app = FastAPI()
api_router = APIRouter(prefix="/api")

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _iso_utc(dt) -> str:
    """Serialize a datetime as ISO 8601 with an explicit UTC marker.

    Mongo strips tzinfo when it stores datetimes, so values read back are
    typically NAIVE even though they represent UTC instants. Calling plain
    `.isoformat()` on them yields a string that JavaScript interprets as
    LOCAL time — off by the client's timezone offset. Always append `Z`
    (or the aware offset) so clients get the correct absolute instant.
    """
    if not isinstance(dt, datetime):
        return str(dt)
    if dt.tzinfo is None:
        return dt.isoformat() + 'Z'
    return dt.isoformat()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def hash_password(pw: str) -> str:
    return bcrypt.hashpw(pw.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')


def verify_password(pw: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(pw.encode('utf-8'), hashed.encode('utf-8'))
    except Exception:
        return False


def make_jwt(user_id: str) -> str:
    payload = {
        'sub': user_id,
        'iat': int(now_utc().timestamp()),
        'exp': int((now_utc() + timedelta(days=JWT_TTL_DAYS)).timestamp()),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALG)


def decode_jwt(token: str) -> Optional[str]:
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALG])
        return payload.get('sub')
    except Exception:
        return None


async def get_current_user(authorization: Optional[str] = Header(None)) -> dict:
    if not authorization or not authorization.startswith('Bearer '):
        raise HTTPException(status_code=401, detail='Missing bearer token')
    token = authorization.split(' ', 1)[1].strip()

    user_id = decode_jwt(token)
    if user_id:
        user = await db.users.find_one({'user_id': user_id}, {'_id': 0})
        if user:
            return user

    session = await db.user_sessions.find_one({'session_token': token}, {'_id': 0})
    if session:
        expires_at = session.get('expires_at')
        if isinstance(expires_at, datetime):
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            if expires_at > now_utc():
                user = await db.users.find_one({'user_id': session['user_id']}, {'_id': 0})
                if user:
                    return user

    raise HTTPException(status_code=401, detail='Invalid or expired token')


async def get_current_user_optional(authorization: Optional[str] = Header(None)) -> Optional[dict]:
    if not authorization:
        return None
    try:
        return await get_current_user(authorization)
    except HTTPException:
        return None


async def _resolve_anon_user_from_authorization(authorization: Optional[str]) -> Optional[dict]:
    """Best-effort: if the request carries a Bearer token belonging to an
    anonymous user, return that user document. Used by signup/login/google
    endpoints to migrate anonymous data on account upgrade."""
    if not authorization or not authorization.startswith('Bearer '):
        return None
    token = authorization.split(' ', 1)[1].strip()
    uid = decode_jwt(token)
    if not uid:
        # Fallback: check user_sessions in case token is a Google session token
        try:
            session = await db.user_sessions.find_one({'session_token': token}, {'_id': 0, 'user_id': 1})
            if session:
                uid = session.get('user_id')
        except Exception:
            uid = None
    if not uid:
        return None
    u = await db.users.find_one({'user_id': uid}, {'_id': 0})
    if not u:
        return None
    if u.get('auth_provider') != 'anonymous' and not u.get('is_anonymous'):
        return None
    return u


async def _migrate_anon_data(from_uid: str, to_uid: str) -> dict:
    """Reassign anonymous user's engagement (votes/comments/replies/messages)
    to the target registered account, then delete the anonymous account.

    Vote collection has a UNIQUE index on (feud_id, user_id) — if the target
    account already voted on a feud the anon vote is dropped (registered vote
    wins, its own tallies are already reflected on the feud counters).
    """
    stats = {'votes_moved': 0, 'votes_dropped': 0, 'comments_moved': 0, 'replies_moved': 0, 'messages_moved': 0}
    if not from_uid or not to_uid or from_uid == to_uid:
        return stats
    # Votes — handle unique index (feud_id, user_id) collisions manually.
    async for v in db.votes.find({'user_id': from_uid}, {'_id': 0, 'vote_id': 1, 'feud_id': 1, 'side': 1}):
        clash = await db.votes.find_one(
            {'feud_id': v['feud_id'], 'user_id': to_uid}, {'_id': 0, 'vote_id': 1}
        )
        if clash:
            # Target already voted here — drop the anon vote AND decrement the
            # feud counter (the anon vote was already counted).
            dec_field = 'votes_a' if v.get('side') == 'A' else 'votes_b'
            try:
                await db.feuds.update_one(
                    {'feud_id': v['feud_id'], dec_field: {'$gt': 0}},
                    {'$inc': {dec_field: -1}},
                )
            except Exception:
                pass
            await db.votes.delete_one({'vote_id': v['vote_id']})
            stats['votes_dropped'] += 1
        else:
            await db.votes.update_one(
                {'vote_id': v['vote_id']}, {'$set': {'user_id': to_uid}}
            )
            stats['votes_moved'] += 1
    # Comments — bulk reassign
    r = await db.comments.update_many({'user_id': from_uid}, {'$set': {'user_id': to_uid}})
    stats['comments_moved'] = int(getattr(r, 'modified_count', 0) or 0)
    # Replies — bulk reassign
    r = await db.replies.update_many({'user_id': from_uid}, {'$set': {'user_id': to_uid}})
    stats['replies_moved'] = int(getattr(r, 'modified_count', 0) or 0)
    # Messages — anonymous accounts can't chat but reassign defensively
    r1 = await db.messages.update_many({'sender_id': from_uid}, {'$set': {'sender_id': to_uid}})
    r2 = await db.messages.update_many({'recipient_id': from_uid}, {'$set': {'recipient_id': to_uid}})
    stats['messages_moved'] = int(getattr(r1, 'modified_count', 0) or 0) + int(getattr(r2, 'modified_count', 0) or 0)
    # Nuke anon account artifacts
    try:
        await db.user_photos.delete_many({'user_id': from_uid})
        await db.user_sessions.delete_many({'user_id': from_uid})
        await db.verification_tokens.delete_many({'user_id': from_uid})
        await db.badge_notifications.delete_many({'user_id': from_uid})
    except Exception:
        pass
    await db.users.delete_one({'user_id': from_uid})
    # Refresh badges / alignment on the new owner
    try:
        await _recompute_user_alignment(to_uid)
    except Exception as e:
        logger.warning(f"post-migration alignment recompute failed for {to_uid}: {e}")
    logger.info(f"migrated anon={from_uid} -> user={to_uid} stats={stats}")
    return stats


async def _upgrade_anon_in_place(anon_uid: str, updates: dict) -> None:
    """Transform an anonymous account into a registered one WITHOUT changing
    user_id — keeps all votes/comments/replies/messages linked seamlessly.
    Callers must supply the new auth_provider / credentials in `updates`.
    """
    base = {
        'is_anonymous': False,
        'upgraded_from_anon_at': now_utc(),
    }
    base.update(updates)
    await db.users.update_one({'user_id': anon_uid}, {'$set': base})
    try:
        await _recompute_user_alignment(anon_uid)
    except Exception as e:
        logger.warning(f"post-upgrade alignment recompute failed for {anon_uid}: {e}")


async def require_admin(x_admin_key: Optional[str] = Header(None, alias='X-Admin-Key')) -> bool:
    if not ADMIN_TOKEN or not x_admin_key or x_admin_key != ADMIN_TOKEN:
        raise HTTPException(status_code=401, detail='Chiave admin non valida')
    return True


# Nickname validation — Instagram-style handle. Allowed characters are
# lowercase letters, digits, underscore and period. Nickname is stored
# lowercased (Instagram behaviour). Length is 2..24.
NICKNAME_ALLOWED_RE = _re.compile(r'^[a-z0-9._]+$')


def _normalize_and_validate_nickname(raw: Optional[str]) -> str:
    if not raw:
        raise HTTPException(status_code=400, detail='Nickname mancante')
    # Trim, strip leading '@', force lowercase — the frontend already does this
    # but the backend is the source of truth.
    n = raw.strip().lstrip('@').lower()
    if len(n) < 2:
        raise HTTPException(status_code=400, detail='Il nickname deve avere almeno 2 caratteri')
    if len(n) > 24:
        raise HTTPException(status_code=400, detail='Il nickname deve avere al massimo 24 caratteri')
    if not NICKNAME_ALLOWED_RE.match(n):
        raise HTTPException(
            status_code=400,
            detail='Il nickname può contenere solo lettere minuscole, numeri, punti e underscore (nessuno spazio)',
        )
    return n


class SignupBody(BaseModel):
    email: EmailStr
    password: str = Field(min_length=6)
    # Length + character rules are enforced by `_normalize_and_validate_nickname`
    # so we can return specific Italian 400s instead of Pydantic 422s.
    nickname: str


class LoginBody(BaseModel):
    email: EmailStr
    password: str


class AnonymousBody(BaseModel):
    nickname: str


class GoogleSessionBody(BaseModel):
    session_id: str


class ProfileBody(BaseModel):
    age: int = Field(ge=13, le=120)
    sex: Literal['F', 'M', 'other', 'na']
    region: str = Field(min_length=2, max_length=40)
    favorite_categories: List[str] = Field(min_length=1)
    # New field (optional for back-compat with previously onboarded users).
    profession: Optional[str] = Field(default=None, max_length=60)
    # Nickname override — used by external-provider signups (Google) so the
    # user can choose their own handle instead of inheriting the Google name.
    # Optional to keep backwards compatibility with existing onboarding calls.
    # Rules enforced in `_normalize_and_validate_nickname`, not by Pydantic.
    nickname: Optional[str] = None
    # Optional public "display name" shown in grey under the nickname on the
    # profile. Free-form, doesn't need to be unique.
    display_name: Optional[str] = Field(default=None, max_length=40)


class DetailsBody(BaseModel):
    bio: Optional[str] = Field(default=None, max_length=200)
    social_links: Optional[dict] = None  # {instagram, tiktok, twitter, youtube, website}


class PhotoUploadBody(BaseModel):
    data: str = Field(min_length=40)  # cropped base64 (with or without prefix)
    original_data: Optional[str] = Field(default=None, min_length=40)  # uncropped source, used to allow re-cropping (zoom-out)


class VoteBody(BaseModel):
    side: Literal['A', 'B']


class CommentBody(BaseModel):
    text: str = Field(min_length=1, max_length=500)


class ReplyBody(BaseModel):
    text: str = Field(min_length=1, max_length=500)


def compute_badge(u: dict) -> Optional[dict]:
    total = u.get('total_votes', 0)
    if total < 10:
        return {'unlocked': False, 'progress': total, 'target': 10, 'label': 'Continua a votare per sbloccare la spilla'}
    maj = u.get('majority_votes', 0)
    minr = u.get('minority_votes', 0)
    if maj >= minr:
        return {'unlocked': True, 'type': 'buon_senso', 'label': 'Utente di Buon Senso', 'majority': maj, 'minority': minr}
    return {'unlocked': True, 'type': 'bastian_contrario', 'label': 'Utente Bastian Contrario', 'majority': maj, 'minority': minr}


# Central badge registry: metadata + notification copy. Add new badge types
# here + a rule in `compute_badge` (or a dedicated evaluator) — the earn-flow
# below will pick them up automatically (push on first-ever from a group,
# in-app only on switches within the same group).
#
# `group`: badges belonging to the same group are mutually exclusive by design.
# Any transition WITHIN a group is framed as a swap ("Sei passato da X a Y")
# even if the target badge was never held before. Transitioning ACROSS groups
# (or earning your very first badge) triggers a full "NUOVA SPILLA" push.
BADGE_META: dict = {
    'buon_senso': {
        'label': 'Utente di Buon Senso',
        'emoji': '⚖️',
        'group': 'alignment',
        'first_earn_body': (
            "Hai sbloccato la spilla ⚖️ Utente di Buon Senso: voti in linea "
            "con la maggioranza. Complimenti!"
        ),
    },
    'bastian_contrario': {
        'label': 'Utente Bastian Contrario',
        'emoji': '🎭',
        'group': 'alignment',
        'first_earn_body': (
            "Hai sbloccato la spilla 🎭 Utente Bastian Contrario: voti spesso "
            "controcorrente. Solide opinioni personali!"
        ),
    },
    # Future badges go here — same shape, different `group` (or same group if
    # they form another mutually-exclusive family).
}


def _badge_group(badge_type: Optional[str]) -> Optional[str]:
    if not badge_type:
        return None
    return (BADGE_META.get(badge_type) or {}).get('group')


async def _evaluate_and_notify_badge_change(user_id: str) -> None:
    """After counters are recomputed, detect whether the user's badge changed.

    Notification rule:
    - First-ever badge from a NEW group (no previous badge in that group) →
      PUSH + in-app "NUOVA SPILLA".
    - Switch WITHIN the same group (e.g. buon_senso ↔ bastian_contrario) →
      in-app ONLY "CAMBIO SPILLA — Sei passato dalla spilla X alla spilla Y".
      This applies even if the target badge was never held before, because
      the two are mutually exclusive by design and swapping between them is
      conceptually a transition, not a discovery.
    - No change → silent.
    Idempotent: uses `current_badge` and `badges_ever_awarded` on the user doc.
    """
    u = await db.users.find_one({'user_id': user_id}, {'_id': 0})
    if not u:
        return
    badge = compute_badge(u)
    # Only unlocked badges trigger notifications
    if not badge or not badge.get('unlocked'):
        # If user previously had a badge and now doesn't (e.g. counters reset),
        # clear the current badge but keep history.
        if u.get('current_badge'):
            await db.users.update_one({'user_id': user_id}, {'$set': {'current_badge': None}})
        return
    new_type = badge['type']
    prev_type = u.get('current_badge')
    if prev_type == new_type:
        return  # no change
    history = list(u.get('badges_ever_awarded') or [])
    meta_new = BADGE_META.get(new_type, {'label': new_type, 'emoji': '🏅', 'first_earn_body': f'Hai sbloccato la spilla {new_type}.', 'group': None})
    new_group = meta_new.get('group')
    prev_group = _badge_group(prev_type)
    same_group_swap = bool(prev_type and new_group and prev_group == new_group)

    if same_group_swap:
        meta_prev = BADGE_META.get(prev_type or '', {'label': prev_type or '—', 'emoji': ''})
        title = f"{meta_new['emoji']} CAMBIO SPILLA"
        body = (
            f"Sei passato dalla spilla {meta_prev.get('emoji','')} "
            f"{meta_prev['label']} alla spilla {meta_new['emoji']} {meta_new['label']}."
        ).strip()
        send_push = False
    else:
        # First time from this group (or a genuinely new badge with no group)
        title = f"{meta_new['emoji']} NUOVA SPILLA"
        body = meta_new['first_earn_body']
        send_push = True

    # Persist BEFORE notifying so a retry of the same recompute doesn't
    # double-fire the same event.
    updates: dict = {'current_badge': new_type}
    if new_type not in history:
        history.append(new_type)
        updates['badges_ever_awarded'] = history
    await db.users.update_one({'user_id': user_id}, {'$set': updates})
    try:
        await _emit_notification(
            user_id=user_id,
            ntype='badge',
            title=title,
            body=body,
            send_push_too=send_push,
        )
    except Exception as e:
        logger.warning(f"badge notification emit failed for {user_id}: {e}")


def _public_user(u: dict) -> dict:
    return {
        'user_id': u['user_id'],
        'email': u.get('email'),
        'nickname': u.get('nickname'),
        'display_name': u.get('display_name'),
        'auth_provider': u.get('auth_provider'),
        'is_anonymous': bool(u.get('is_anonymous')) or (u.get('auth_provider') == 'anonymous'),
        'picture': u.get('picture'),
        'majority_votes': u.get('majority_votes', 0),
        'minority_votes': u.get('minority_votes', 0),
        'total_votes': u.get('total_votes', 0),
        'age': u.get('age'),
        'sex': u.get('sex'),
        'region': u.get('region'),
        'profession': u.get('profession'),
        'favorite_categories': u.get('favorite_categories', []),
        'onboarding_completed': bool(u.get('onboarding_completed', False)),
        # Populus Terms & Privacy Policy acceptance. The `TERMS_VERSION`
        # constant is bumped whenever the document materially changes; the
        # client compares this to `terms_accepted_version` and re-prompts
        # the user if they don't match (so we can force re-acceptance).
        'terms_accepted': (u.get('terms_accepted_version') == TERMS_VERSION),
        'terms_accepted_version': u.get('terms_accepted_version'),
        'terms_accepted_at': _iso_utc(u['terms_accepted_at']) if isinstance(u.get('terms_accepted_at'), datetime) else u.get('terms_accepted_at'),
        'bio': u.get('bio'),
        'social_links': u.get('social_links', {}),
        'primary_photo_id': u.get('primary_photo_id'),
        'photos_count': u.get('photos_count', 0),
        'badge': compute_badge(u),
        'push_notifications': u.get('push_notifications', True),
        # Two independent switches govern who can see the voting history on
        # the public profile: mutual-circle members and everyone else. Both
        # default to True so pre-existing accounts stay fully public.
        'history_public_generic': True if u.get('history_public_generic') is None else bool(u.get('history_public_generic')),
        'history_public_mutual': True if u.get('history_public_mutual') is None else bool(u.get('history_public_mutual')),
    }


import secrets as _secrets
import hashlib as _hashlib

FRONTEND_BASE_URL = os.environ.get('EXPO_PUBLIC_BACKEND_URL') or os.environ.get('FRONTEND_BASE_URL') or ''

async def _send_verification_email(user_id: str, email: str, pending_migration_from: Optional[str] = None) -> None:
    """Generate a fresh verification token and email the link to `email`.

    Idempotent per user: deletes any previous token before inserting a new one,
    so only ONE active verification link exists at a time (prevents the classic
    'stale token wins the query' bug). The token is opaque + URL-safe + stored
    hashed; only the raw token appears in the email link.

    If `pending_migration_from` is provided, the anon user_id is stored in the
    token doc — on successful verify-email the backend reassigns anon data to
    this account. Used when a user was anon and signed up with an email that
    already exists (verified or in-flight signup): we can't upgrade in place.
    """
    raw = _secrets.token_urlsafe(32)
    token_hash = _hashlib.sha256(raw.encode('utf-8')).hexdigest()
    expires_at = now_utc() + timedelta(hours=24)
    await db.verification_tokens.delete_many({'user_id': user_id})
    doc: dict = {
        'user_id': user_id,
        'token_hash': token_hash,
        'created_at': now_utc(),
        'expires_at': expires_at,
    }
    if pending_migration_from and pending_migration_from != user_id:
        doc['pending_migration_from'] = pending_migration_from
    await db.verification_tokens.insert_one(doc)
    base = FRONTEND_BASE_URL.rstrip('/')
    link = f"{base}/verify-email?token={raw}" if base else f"/verify-email?token={raw}"
    if not RESEND_API_KEY:
        logger.warning('RESEND_API_KEY missing — verification email not sent')
        return
    html = (
        f"<div style='font-family:system-ui,sans-serif;max-width:520px;margin:auto;padding:24px'>"
        f"<h2>Benvenuto su Populus</h2>"
        f"<p>Clicca sul pulsante qui sotto per confermare la tua email e attivare l'account.</p>"
        f"<p><a href='{link}' style='background:#e11d48;color:#fff;padding:12px 20px;text-decoration:none;border-radius:6px;display:inline-block;letter-spacing:1px;font-weight:600'>VERIFICA EMAIL</a></p>"
        f"<p style='color:#666;font-size:12px'>Se il pulsante non funziona, copia questo link nel browser:<br><span style='word-break:break-all'>{link}</span></p>"
        f"<p style='color:#999;font-size:11px'>Il link scade tra 24 ore. Se non hai richiesto tu la registrazione, ignora questa email.</p>"
        f"</div>"
    )
    try:
        async with httpx.AsyncClient(timeout=10.0) as hx:
            r = await hx.post(
                'https://api.resend.com/emails',
                headers={'Authorization': f'Bearer {RESEND_API_KEY}', 'Content-Type': 'application/json'},
                json={
                    'from': 'Populus <onboarding@resend.dev>',
                    'to': [email],
                    'subject': 'Verifica la tua email — Populus',
                    'html': html,
                },
            )
            if r.status_code >= 300:
                logger.warning(f"Resend verification email failed [{r.status_code}]: {r.text[:200]}")
    except Exception as e:
        logger.warning(f"Resend verification email exception: {e}")


class VerifyEmailBody(BaseModel):
    token: str

class ResendVerificationBody(BaseModel):
    email: str

# Simple in-memory rate limiter for resend-verification (per email + IP).
_RESEND_RATE: dict = {}  # key -> [timestamps]
def _rate_limited(key: str, max_hits: int = 3, window_sec: int = 3600) -> bool:
    now = time.time()
    hits = [t for t in _RESEND_RATE.get(key, []) if now - t < window_sec]
    if len(hits) >= max_hits:
        _RESEND_RATE[key] = hits
        return True
    hits.append(now)
    _RESEND_RATE[key] = hits
    return False


@api_router.post('/auth/verify-email')
async def verify_email(body: VerifyEmailBody):
    """Consume a verification token. Idempotent-ish: token is single-use, once
    the user is verified subsequent calls return 200 (already verified).
    """
    if not body.token:
        raise HTTPException(status_code=400, detail='Token mancante')
    token_hash = _hashlib.sha256(body.token.encode('utf-8')).hexdigest()
    doc = await db.verification_tokens.find_one({'token_hash': token_hash, 'expires_at': {'$gt': now_utc()}})
    if not doc:
        raise HTTPException(status_code=400, detail='Link non valido o scaduto. Richiedi un nuovo invio.')
    user_id = doc['user_id']
    user = await db.users.find_one({'user_id': user_id}, {'_id': 0})
    if not user:
        raise HTTPException(status_code=404, detail='Utente non trovato')
    await db.users.update_one({'user_id': user_id}, {'$set': {'email_verified': True}})
    await db.verification_tokens.delete_many({'user_id': user_id})
    # Pending anon migration: if this account was created from an anon upgrade
    # path where the target email was already taken, we deferred the actual
    # data reassignment until email verification.
    pending_from = doc.get('pending_migration_from')
    if pending_from and pending_from != user_id:
        try:
            await _migrate_anon_data(pending_from, user_id)
        except Exception as e:
            logger.warning(f"deferred anon migration failed {pending_from}->{user_id}: {e}")
    fresh = await db.users.find_one({'user_id': user_id}, {'_id': 0})
    return {'ok': True, 'token': make_jwt(user_id), 'user': _public_user(fresh)}


@api_router.post('/auth/resend-verification')
async def resend_verification(body: ResendVerificationBody, request: Request):
    email = (body.email or '').strip().lower()
    if not email:
        raise HTTPException(status_code=400, detail='Email mancante')
    client_ip = (request.client.host if request.client else 'unknown') or 'unknown'
    rl_key = f"{email}|{client_ip}"
    if _rate_limited(rl_key, max_hits=3, window_sec=3600):
        raise HTTPException(status_code=429, detail='Troppi tentativi. Riprova tra un\'ora.')
    user = await db.users.find_one({'email': email}, {'_id': 0, 'user_id': 1, 'email_verified': 1, 'auth_provider': 1})
    # Return generic success to avoid email enumeration if user doesn't exist / already verified
    if not user or user.get('auth_provider') != 'email':
        return {'ok': True, 'message': 'Se questo indirizzo corrisponde a un account non verificato, riceverai una nuova email.'}
    if user.get('email_verified'):
        return {'ok': True, 'message': 'Email già verificata. Puoi accedere.'}
    try:
        await _send_verification_email(user['user_id'], email)
    except Exception as e:
        logger.warning(f"resend verification email failed: {e}")
    return {'ok': True, 'message': 'Email di verifica inviata. Controlla la casella.'}


@api_router.post('/auth/signup')
async def signup(body: SignupBody, authorization: Optional[str] = Header(None)):
    # Enforce Instagram-style nickname rules (no spaces/emoji/punctuation).
    normalized_nick = _normalize_and_validate_nickname(body.nickname)
    email_lc = body.email.lower()
    existing = await db.users.find_one({'email': email_lc})
    anon_user = await _resolve_anon_user_from_authorization(authorization)
    anon_uid = anon_user['user_id'] if anon_user else None
    # Unverified accounts can be retried: overwrite password_hash / nickname
    # so a user who mistyped or forgot doesn't get permanently locked out of
    # their own email until manual intervention. Verified accounts remain
    # protected against being taken over by re-signup.
    if existing and existing.get('email_verified'):
        raise HTTPException(status_code=400, detail='Email già registrata')
    if existing and not existing.get('email_verified'):
        user_id = existing['user_id']
        await db.users.update_one(
            {'user_id': user_id},
            {'$set': {
                'password_hash': hash_password(body.password),
                'nickname': normalized_nick,
                'auth_provider': 'email',
            }},
        )
        try:
            # Defer anon migration to email verification: we don't want anon
            # data to move to an account that never actually gets activated.
            await _send_verification_email(
                user_id, email_lc,
                pending_migration_from=anon_uid if anon_uid and anon_uid != user_id else None,
            )
        except Exception as e:
            logger.warning(f"re-signup verification email failed for {user_id}: {e}")
        return {
            'requires_verification': True,
            'email': email_lc,
            'message': 'Abbiamo reinviato l\'email di conferma. Controlla la tua casella.',
        }
    # Anon in-place upgrade path: the current user is anonymous AND the email
    # is fresh. Preserve the anon user_id so all votes/comments/replies remain
    # linked seamlessly — the account merely graduates to an email account.
    if anon_uid:
        await _upgrade_anon_in_place(anon_uid, {
            'email': email_lc,
            'nickname': normalized_nick,
            'password_hash': hash_password(body.password),
            'auth_provider': 'email',
            'email_verified': False,
        })
        try:
            await _send_verification_email(anon_uid, email_lc)
        except Exception as e:
            logger.warning(f"anon-upgrade verification email failed for {anon_uid}: {e}")
        return {
            'requires_verification': True,
            'email': email_lc,
            'message': 'Ti abbiamo inviato una email di conferma. I tuoi voti anonimi sono stati conservati.',
        }
    user_id = new_id('user')
    user = {
        'user_id': user_id,
        'email': email_lc,
        'nickname': normalized_nick,
        'password_hash': hash_password(body.password),
        'auth_provider': 'email',
        'created_at': now_utc(),
        'majority_votes': 0, 'minority_votes': 0, 'total_votes': 0,
        # Email verification is REQUIRED before login. See _send_verification_email.
        'email_verified': False,
    }
    await db.users.insert_one(user)
    # Kick off verification email (background, non-blocking). Failures are
    # logged but don't fail the signup — user can resend later.
    try:
        await _send_verification_email(user_id, user['email'])
    except Exception as e:
        logger.warning(f"initial verification email failed for {user_id}: {e}")
    # Do NOT return a session token: user must verify email first.
    return {
        'requires_verification': True,
        'email': user['email'],
        'message': 'Ti abbiamo inviato una email di conferma. Clicca sul link per attivare il tuo account.',
    }


@api_router.post('/auth/login')
async def login(body: LoginBody, authorization: Optional[str] = Header(None)):
    user = await db.users.find_one({'email': body.email.lower()}, {'_id': 0})
    if not user or user.get('auth_provider') != 'email':
        raise HTTPException(status_code=401, detail='Credenziali non valide')
    if not verify_password(body.password, user.get('password_hash', '')):
        raise HTTPException(status_code=401, detail='Credenziali non valide')
    # Block login for unverified email accounts — 403 with structured detail
    # so the frontend can show the "resend verification" CTA.
    if not user.get('email_verified', False):
        raise HTTPException(
            status_code=403,
            detail={
                'email_not_verified': True,
                'message': 'Devi verificare la tua email prima di accedere. Controlla la tua casella.',
                'email': user['email'],
            },
        )
    # If the requester was anonymous, migrate their engagement into this
    # existing registered account before returning the session.
    try:
        anon = await _resolve_anon_user_from_authorization(authorization)
        if anon and anon['user_id'] != user['user_id']:
            await _migrate_anon_data(anon['user_id'], user['user_id'])
            user = await db.users.find_one({'user_id': user['user_id']}, {'_id': 0})
    except Exception as e:
        logger.warning(f"login-time anon migration failed: {e}")
    return {'token': make_jwt(user['user_id']), 'user': _public_user(user)}


@api_router.post('/auth/anonymous')
async def anonymous(body: AnonymousBody):
    normalized_nick = _normalize_and_validate_nickname(body.nickname)
    user_id = new_id('anon')
    user = {
        'user_id': user_id, 'email': None, 'nickname': normalized_nick,
        'auth_provider': 'anonymous', 'created_at': now_utc(),
        'majority_votes': 0, 'minority_votes': 0, 'total_votes': 0,
        # Anonymous users skip onboarding and see all categories by default
        'onboarding_completed': True,
        'favorite_categories': [],
    }
    await db.users.insert_one(user)
    return {'token': make_jwt(user_id), 'user': _public_user(user)}


@api_router.post('/auth/google-session')
async def google_session(body: GoogleSessionBody, authorization: Optional[str] = Header(None)):
    async with httpx.AsyncClient(timeout=15.0) as hx:
        r = await hx.get(
            'https://demobackend.emergentagent.com/auth/v1/env/oauth/session-data',
            headers={'X-Session-ID': body.session_id},
        )
    if r.status_code != 200:
        raise HTTPException(status_code=401, detail='Sessione Google non valida')
    data = r.json()
    email = (data.get('email') or '').lower()
    name = data.get('name') or 'Utente'
    session_token = data.get('session_token')
    if not email or not session_token:
        raise HTTPException(status_code=401, detail='Dati sessione mancanti')

    anon = await _resolve_anon_user_from_authorization(authorization)
    anon_uid = anon['user_id'] if anon else None

    existing = await db.users.find_one({'email': email}, {'_id': 0})
    if existing:
        user_id = existing['user_id']
        user = existing
        # Migrate anon data into this existing Google account.
        if anon_uid and anon_uid != user_id:
            try:
                await _migrate_anon_data(anon_uid, user_id)
                user = await db.users.find_one({'user_id': user_id}, {'_id': 0})
            except Exception as e:
                logger.warning(f"google-login anon migration failed: {e}")
    elif anon_uid:
        # In-place upgrade of the anonymous account into a Google account:
        # preserves user_id (and therefore every vote/comment/reply/message).
        await _upgrade_anon_in_place(anon_uid, {
            'email': email,
            'nickname': name,
            'auth_provider': 'google',
            'picture': data.get('picture'),
            'email_verified': True,
        })
        user_id = anon_uid
        user = await db.users.find_one({'user_id': user_id}, {'_id': 0})
    else:
        user_id = new_id('user')
        user = {
            'user_id': user_id, 'email': email, 'nickname': name,
            'auth_provider': 'google', 'picture': data.get('picture'),
            'created_at': now_utc(),
            'majority_votes': 0, 'minority_votes': 0, 'total_votes': 0,
        }
        await db.users.insert_one(user)

    await db.user_sessions.insert_one({
        'session_token': session_token, 'user_id': user_id,
        'created_at': now_utc(), 'expires_at': now_utc() + timedelta(days=7),
    })
    return {'token': session_token, 'user': _public_user(user)}


@api_router.get('/auth/me')
async def me(user: dict = Depends(get_current_user)):
    # Throttled live recompute: if it's been more than 60s since we last checked
    # this user's alignment, recompute counters (majority/minority) from CURRENT
    # feud states and detect badge changes induced by other users' voting.
    # This guarantees the profile badge stays consistent even when the user
    # isn't the one triggering vote flips. Anonymous accounts never earn
    # badges so we can skip them entirely.
    try:
        if user.get('auth_provider') != 'anonymous':
            last = user.get('last_alignment_check')
            stale = True
            if isinstance(last, datetime):
                if last.tzinfo is None:
                    last = last.replace(tzinfo=timezone.utc)
                stale = (now_utc() - last) > timedelta(seconds=60)
            if stale:
                await _recompute_user_alignment(user['user_id'])
                # Reload after recompute so `_public_user` sees fresh counters.
                fresh = await db.users.find_one({'user_id': user['user_id']}, {'_id': 0})
                if fresh:
                    user = fresh
    except Exception as e:
        logger.warning(f"me() alignment recompute failed: {e}")
    return {'user': _public_user(user)}


VALID_CATEGORY_IDS = {'politica', 'tv', 'musica', 'sport', 'cinema', 'social', 'gossip', 'tech', 'cronaca'}
ITALIAN_REGIONS = {
    'Abruzzo', 'Basilicata', 'Calabria', 'Campania', 'Emilia-Romagna',
    'Friuli-Venezia Giulia', 'Lazio', 'Liguria', 'Lombardia', 'Marche',
    'Molise', 'Piemonte', 'Puglia', 'Sardegna', 'Sicilia', 'Toscana',
    "Trentino-Alto Adige", 'Umbria', "Valle d'Aosta", 'Veneto', 'Altro',
}

# Classic profession/occupation categories offered during onboarding.
# Broad enough to cover most working-age Italians without becoming a job title
# taxonomy — the exact string is stored on the user document as-is.
PROFESSIONS = [
    'Studente/Studentessa',
    'Impiegato/a',
    'Operaio/a',
    'Insegnante',
    'Dirigente / Manager',
    'Libero professionista',
    'Imprenditore/Imprenditrice',
    'Artigiano/a',
    'Commerciante',
    'Agricoltore/Agricoltrice',
    'Medico / Personale sanitario',
    'Avvocato / Notaio',
    'Ingegnere / Architetto',
    'Ricercatore/Ricercatrice',
    'Militare / Forze dell\'ordine',
    'Artista / Creativo',
    'Giornalista / Comunicazione',
    'Informatico / Tecnologia',
    'Trasporti / Logistica',
    'Ristorazione / Turismo',
    'Casalingo/a',
    'In cerca di occupazione',
    'Pensionato/a',
    'Altro',
    'Preferisco non dirlo',
]
VALID_PROFESSIONS = set(PROFESSIONS)


@api_router.get('/professions')
async def get_professions():
    return {'professions': PROFESSIONS}


def _reject_if_anonymous(user: dict):
    if user.get('auth_provider') == 'anonymous':
        raise HTTPException(status_code=403, detail='Personalizzazione riservata agli account registrati')


@api_router.patch('/auth/me/profile')
async def update_profile(body: ProfileBody, user: dict = Depends(get_current_user)):
    _reject_if_anonymous(user)
    invalid = [c for c in body.favorite_categories if c not in VALID_CATEGORY_IDS]
    if invalid:
        raise HTTPException(status_code=400, detail=f"Categorie non valide: {', '.join(invalid)}")
    if body.region not in ITALIAN_REGIONS:
        raise HTTPException(status_code=400, detail='Regione non valida')
    # Profession is optional but, if provided, must be one of the allowed
    # values (kept in sync with `/professions`).
    if body.profession and body.profession not in VALID_PROFESSIONS:
        raise HTTPException(status_code=400, detail='Professione non valida')
    updates: dict = {
        'age': body.age,
        'sex': body.sex,
        'region': body.region,
        'favorite_categories': body.favorite_categories,
        'onboarding_completed': True,
        'profile_updated_at': now_utc(),
    }
    if body.profession is not None:
        updates['profession'] = body.profession
    if body.nickname is not None:
        # Full validation via shared helper — same rules as signup/anon.
        nick = _normalize_and_validate_nickname(body.nickname)
        # Uniqueness (case-insensitive). We match on the lowercased form so
        # "ChatA" and "chata" collide — but the caller's original casing is
        # preserved in storage.
        clash = await db.users.find_one(
            {
                'user_id': {'$ne': user['user_id']},
                'nickname': {'$regex': f'^{_re.escape(nick)}$', '$options': 'i'},
            },
            {'_id': 0, 'user_id': 1},
        )
        if clash:
            raise HTTPException(status_code=409, detail='Questo nickname è già in uso')
        updates['nickname'] = nick
    if body.display_name is not None:
        dn = body.display_name.strip()
        # Allow explicit clearing with an empty string.
        updates['display_name'] = dn or None
    await db.users.update_one({'user_id': user['user_id']}, {'$set': updates})
    updated = await db.users.find_one({'user_id': user['user_id']}, {'_id': 0})
    return {'user': _public_user(updated)}


ALLOWED_SOCIAL_KEYS = {'instagram', 'tiktok', 'twitter', 'youtube', 'website'}
MAX_PHOTOS = 7


def _sanitize_social_links(sl: Optional[dict]) -> dict:
    if not isinstance(sl, dict):
        return {}
    platform_bases = {
        'instagram': 'https://instagram.com/',
        'tiktok': 'https://www.tiktok.com/@',
        'twitter': 'https://x.com/',
        'youtube': 'https://youtube.com/@',
    }
    out = {}
    for k, v in sl.items():
        if k not in ALLOWED_SOCIAL_KEYS:
            continue
        v = (v or '').strip()
        if not v:
            continue
        if len(v) > 200:
            v = v[:200]
        low = v.lower()
        if low.startswith('http://') or low.startswith('https://'):
            out[k] = v
            continue
        if k == 'website':
            out[k] = 'https://' + v.lstrip('@').lstrip('/')
            continue
        # Bare handle for a known social platform
        handle = v.lstrip('@').lstrip('/')
        base = platform_bases.get(k)
        if base:
            out[k] = base + handle
        else:
            out[k] = 'https://' + handle
    return out


@api_router.patch('/auth/me/details')
async def update_details(body: DetailsBody, user: dict = Depends(get_current_user)):
    _reject_if_anonymous(user)
    updates: dict = {}
    if body.bio is not None:
        updates['bio'] = body.bio.strip()[:200]
    if body.social_links is not None:
        updates['social_links'] = _sanitize_social_links(body.social_links)
    if not updates:
        raise HTTPException(status_code=400, detail='Nessun campo da aggiornare')
    updates['details_updated_at'] = now_utc()
    await db.users.update_one({'user_id': user['user_id']}, {'$set': updates})
    updated = await db.users.find_one({'user_id': user['user_id']}, {'_id': 0})
    return {'user': _public_user(updated)}


def _strip_data_url(s: str) -> str:
    if s.startswith('data:'):
        idx = s.find(',')
        if idx > 0:
            return s[idx + 1:]
    return s


@api_router.post('/auth/me/photos')
async def upload_photo(body: PhotoUploadBody, user: dict = Depends(get_current_user)):
    _reject_if_anonymous(user)
    current_count = await db.user_photos.count_documents({'user_id': user['user_id']})
    if current_count >= MAX_PHOTOS:
        raise HTTPException(status_code=400, detail=f'Massimo {MAX_PHOTOS} foto totali')
    data = _strip_data_url(body.data.strip())
    if len(data) > 3_500_000:  # ~2.5MB decoded upper bound
        raise HTTPException(status_code=400, detail='Foto troppo grande (max ~2.5MB)')
    photo_id = new_id('ph')
    doc: dict = {
        'photo_id': photo_id,
        'user_id': user['user_id'],
        'data': data,
        'position': current_count,
        'created_at': now_utc(),
    }
    # Optional uncropped source — kept ONLY when the client actually sends
    # a distinct source. This preserves accurate semantics for the
    # `has_original` flag consumed by the client: if the DB has no distinct
    # original, we can't provide a true zoom-out on re-crop (the source is
    # already the cropped rectangle). The `GET /.../original` endpoint
    # transparently falls back to `data` for these rows so the flow still
    # works — it just doesn't reveal information that was never captured.
    if body.original_data:
        original = _strip_data_url(body.original_data.strip())
        if len(original) > 3_500_000:
            raise HTTPException(status_code=400, detail='Originale foto troppo grande (max ~2.5MB)')
        doc['original_data'] = original
    await db.user_photos.insert_one(doc)
    updates: dict = {'photos_count': current_count + 1}
    if current_count == 0:
        updates['primary_photo_id'] = photo_id
    await db.users.update_one({'user_id': user['user_id']}, {'$set': updates})
    return {'photo_id': photo_id, 'primary_photo_id': updates.get('primary_photo_id', user.get('primary_photo_id'))}


@api_router.get('/auth/me/photos')
async def my_photos(user: dict = Depends(get_current_user)):
    # NOTE: We intentionally EXCLUDE `original_data` from the list response so
    # the payload does not double in size on every profile fetch. Clients that
    # need the original (re-cropping flow) call
    # `GET /auth/me/photos/{photo_id}/original` on demand.
    docs = await db.user_photos.find(
        {'user_id': user['user_id']}, {'_id': 0, 'original_data': 0}
    ).sort('position', 1).to_list(MAX_PHOTOS + 1)
    for d in docs:
        if isinstance(d.get('created_at'), datetime):
            d['created_at'] = _iso_utc(d['created_at'])
        d['is_primary'] = (d['photo_id'] == user.get('primary_photo_id'))
    return {'photos': docs, 'primary_photo_id': user.get('primary_photo_id')}


@api_router.get('/auth/me/photos/{photo_id}/original')
async def my_photo_original(photo_id: str, user: dict = Depends(get_current_user)):
    """Returns the uncropped base64 for a photo — used by the client when
    the user taps "Ricomponi" so the cropper can start from the FULL source
    and let them zoom back out. For legacy photos saved before this field
    existed, we transparently return the cropped `data` as the original.
    """
    _reject_if_anonymous(user)
    doc = await db.user_photos.find_one(
        {'photo_id': photo_id, 'user_id': user['user_id']},
        {'_id': 0, 'data': 1, 'original_data': 1, 'photo_id': 1},
    )
    if not doc:
        raise HTTPException(status_code=404, detail='Foto non trovata')
    return {
        'photo_id': doc['photo_id'],
        'original_data': doc.get('original_data') or doc.get('data'),
        'has_original': bool(doc.get('original_data')),
    }


async def _reorder_photos_primary_first(user_id: str, primary_photo_id: Optional[str]) -> None:
    """Rewrite `position` on all user photos so `primary_photo_id` sits at 0
    and every other photo follows in its current relative order.

    Idempotent: safe to call on every set-primary event. Also used by the
    one-shot startup migration for pre-existing users. Runs a single bulk
    write so it's cheap even for the max-photos ceiling.

    Callers must pass the CURRENT primary_photo_id — we don't re-read `users`
    because the write to `users` is what typically races with this call.
    """
    photos = await db.user_photos.find(
        {'user_id': user_id}, {'_id': 0, 'photo_id': 1, 'position': 1}
    ).sort('position', 1).to_list(MAX_PHOTOS)
    if not photos:
        return
    # Move the primary to the front, keep the remaining order stable.
    if primary_photo_id:
        primary_idx = next((i for i, p in enumerate(photos) if p['photo_id'] == primary_photo_id), None)
        if primary_idx is not None and primary_idx != 0:
            head = photos.pop(primary_idx)
            photos.insert(0, head)
    # Write only the positions that changed.
    for new_pos, p in enumerate(photos):
        if p.get('position') != new_pos:
            await db.user_photos.update_one(
                {'photo_id': p['photo_id']}, {'$set': {'position': new_pos}}
            )


@api_router.patch('/auth/me/photos/{photo_id}/primary')
async def set_primary_photo(photo_id: str, user: dict = Depends(get_current_user)):
    _reject_if_anonymous(user)
    photo = await db.user_photos.find_one({'photo_id': photo_id, 'user_id': user['user_id']}, {'_id': 0})
    if not photo:
        raise HTTPException(status_code=404, detail='Foto non trovata')
    await db.users.update_one({'user_id': user['user_id']}, {'$set': {'primary_photo_id': photo_id}})
    # Reorder so the new primary is at position 0 — matches the UX rule that
    # external viewers see the primary FIRST and can swipe forward through
    # the rest without paging backwards.
    await _reorder_photos_primary_first(user['user_id'], photo_id)
    return {'primary_photo_id': photo_id}


@api_router.delete('/auth/me/photos/{photo_id}')
async def delete_photo(photo_id: str, user: dict = Depends(get_current_user)):
    _reject_if_anonymous(user)
    photo = await db.user_photos.find_one({'photo_id': photo_id, 'user_id': user['user_id']}, {'_id': 0})
    if not photo:
        raise HTTPException(status_code=404, detail='Foto non trovata')
    await db.user_photos.delete_one({'photo_id': photo_id, 'user_id': user['user_id']})
    remaining = await db.user_photos.find({'user_id': user['user_id']}, {'_id': 0}).sort('position', 1).to_list(MAX_PHOTOS)
    new_primary = None
    if user.get('primary_photo_id') == photo_id:
        new_primary = remaining[0]['photo_id'] if remaining else None
    updates = {'photos_count': len(remaining)}
    if new_primary is not None or user.get('primary_photo_id') == photo_id:
        updates['primary_photo_id'] = new_primary
    await db.users.update_one({'user_id': user['user_id']}, {'$set': updates})
    return {'ok': True, 'primary_photo_id': updates.get('primary_photo_id', user.get('primary_photo_id'))}


@api_router.patch('/auth/me/photos/{photo_id}')
async def replace_photo(photo_id: str, body: PhotoUploadBody, user: dict = Depends(get_current_user)):
    """Replace the cropped base64 of an existing photo (keeps position & photo_id).

    Used by the client when the user re-crops a photo already saved to their
    profile. We keep the photo id so `primary_photo_id` remains valid without
    an additional update. **We intentionally do NOT touch `original_data`**
    so the user can keep re-cropping (including zooming back out) from the
    same pristine source indefinitely.
    """
    _reject_if_anonymous(user)
    photo = await db.user_photos.find_one({'photo_id': photo_id, 'user_id': user['user_id']}, {'_id': 0})
    if not photo:
        raise HTTPException(status_code=404, detail='Foto non trovata')
    data = _strip_data_url(body.data.strip())
    if len(data) > 3_500_000:
        raise HTTPException(status_code=400, detail='Foto troppo grande (max ~2.5MB)')
    updates: dict = {'data': data, 'updated_at': now_utc()}
    # Back-fill the original_data field on legacy photos so subsequent
    # re-crops preserve the ability to zoom back out. Client may pass its
    # cached source when it has one; otherwise we leave whatever the DB has.
    if body.original_data and not photo.get('original_data'):
        original = _strip_data_url(body.original_data.strip())
        if len(original) <= 3_500_000:
            updates['original_data'] = original
    await db.user_photos.update_one(
        {'photo_id': photo_id, 'user_id': user['user_id']},
        {'$set': updates},
    )
    return {'photo_id': photo_id, 'ok': True}


@api_router.get('/users/{user_id}')
async def public_user(user_id: str):
    u = await db.users.find_one({'user_id': user_id}, {'_id': 0})
    if not u:
        raise HTTPException(status_code=404, detail='Utente non trovato')
    is_anonymous = u.get('auth_provider') == 'anonymous'
    # Anonymous accounts only expose their opaque identifier — no photos,
    # profile, socials, badge, or voting stats.
    if is_anonymous:
        return {
            'user_id': u['user_id'],
            'nickname': u.get('nickname') or 'Anonimo',
            'auth_provider': 'anonymous',
            'is_anonymous': True,
        }
    photos = await db.user_photos.find(
        {'user_id': user_id}, {'_id': 0, 'user_id': 0, 'original_data': 0}
    ).sort('position', 1).to_list(MAX_PHOTOS + 1)
    for p in photos:
        if isinstance(p.get('created_at'), datetime):
            p['created_at'] = _iso_utc(p['created_at'])
    return {
        'user_id': u['user_id'],
        'nickname': u.get('nickname'),
        'display_name': u.get('display_name'),
        'auth_provider': u.get('auth_provider'),
        'is_anonymous': False,
        'bio': u.get('bio'),
        'social_links': u.get('social_links', {}),
        'primary_photo_id': u.get('primary_photo_id'),
        'photos': photos,
        'total_votes': u.get('total_votes', 0),
        'majority_votes': u.get('majority_votes', 0),
        'minority_votes': u.get('minority_votes', 0),
        'badge': compute_badge(u),
        'profession': u.get('profession'),
        'region': u.get('region'),
    }


@api_router.post('/auth/logout')
async def logout(authorization: Optional[str] = Header(None)):
    if authorization and authorization.startswith('Bearer '):
        token = authorization.split(' ', 1)[1]
        await db.user_sessions.delete_one({'session_token': token})
    return {'ok': True}


CATEGORIES = [
    {'id': 'politica', 'label': 'Politica'},
    {'id': 'tv', 'label': 'Programmi TV'},
    {'id': 'musica', 'label': 'Musica'},
    {'id': 'sport', 'label': 'Sport'},
    {'id': 'cinema', 'label': 'Cinema'},
    {'id': 'social', 'label': 'Social'},
    {'id': 'gossip', 'label': 'Gossip'},
    {'id': 'cronaca', 'label': 'Cronaca'},
    {'id': 'tech', 'label': 'Tech'},
]


@api_router.get('/categories')
async def get_categories():
    return {'categories': CATEGORIES}


def _attach_percentages(d: dict, revealed: bool = True):
    a = d.get('votes_a', 0)
    b = d.get('votes_b', 0)
    total = a + b
    d['total_votes'] = total
    if revealed:
        d['pct_a'] = round(100 * a / total) if total else 50
        d['pct_b'] = 100 - d['pct_a'] if total else 50
        d['revealed'] = True
    else:
        d['pct_a'] = None
        d['pct_b'] = None
        d['votes_a'] = None
        d['votes_b'] = None
        d['revealed'] = False


async def _user_voted_ids(user_id: str, feud_ids: List[str]) -> dict:
    if not feud_ids:
        return {}
    cur = db.votes.find({'user_id': user_id, 'feud_id': {'$in': feud_ids}}, {'_id': 0})
    votes = await cur.to_list(len(feud_ids))
    return {v['feud_id']: v['side'] for v in votes}


@api_router.get('/feuds/hype')
async def list_hype_feuds(user: Optional[dict] = Depends(get_current_user_optional)):
    """The always-on 'HYPE' rail.

    Returns feuds from the last 7 days ranked by an engagement score
        engagement = total_votes + 2*comments + replies
    …with the newer post winning any tie. Posts that received ZERO
    engagement (no vote, no comment, no reply) are excluded — the rail
    is supposed to surface what's actually being discussed, not fill
    space with untouched articles.

    This section is deliberately outside `/api/categories` and outside a
    user's favorite_categories filter — it always appears in every user's
    home. See the frontend chip row for the always-mounted HYPE chip.
    """
    since = now_utc() - timedelta(days=7)
    docs = await db.feuds.find(
        {'created_at': {'$gte': since}}, {'_id': 0}
    ).sort('created_at', -1).to_list(1500)
    if not docs:
        return {'feuds': [], 'personalized': False}
    feud_ids = [d['feud_id'] for d in docs]
    # Comment + reply counts in ONE aggregation per collection.
    comment_counts: dict = {}
    reply_counts: dict = {}
    try:
        async for row in db.comments.aggregate([
            {'$match': {'feud_id': {'$in': feud_ids}}},
            {'$group': {'_id': '$feud_id', 'count': {'$sum': 1}}},
        ]):
            comment_counts[row['_id']] = int(row.get('count') or 0)
    except Exception as e:
        logger.warning(f"hype: comments aggregation failed: {e}")
    # Replies live under a comment_id. Do a lookup: replies → comment → feud.
    try:
        async for row in db.replies.aggregate([
            {'$lookup': {
                'from': 'comments', 'localField': 'comment_id',
                'foreignField': 'comment_id', 'as': 'c',
            }},
            {'$unwind': '$c'},
            {'$match': {'c.feud_id': {'$in': feud_ids}}},
            {'$group': {'_id': '$c.feud_id', 'count': {'$sum': 1}}},
        ]):
            reply_counts[row['_id']] = int(row.get('count') or 0)
    except Exception as e:
        logger.warning(f"hype: replies aggregation failed: {e}")

    scored: List[dict] = []
    for d in docs:
        votes = int(d.get('votes_a', 0) or 0) + int(d.get('votes_b', 0) or 0)
        cc = comment_counts.get(d['feud_id'], 0)
        rc = reply_counts.get(d['feud_id'], 0)
        # Absolute floor: a post must have at least some interaction to be
        # eligible for HYPE. This drops the long tail of untouched articles.
        if (cc + rc) < 1 and votes < 3:
            continue
        score = votes + 2 * cc + rc
        d['_hype_score'] = score
        d['_comments'] = cc
        d['_replies'] = rc
        scored.append(d)

    # HYPE selection algorithm (per user spec):
    #   1) Rank ALL eligible posts by engagement score DESC.
    #   2) Cut the leaderboard at the top-N (HYPE_TOP_N most interacted).
    #   3) Re-sort those N chronologically (newest first) so the rail reads
    #      like a news feed: "1 h ago → 3 h ago → 1 d ago → …".
    HYPE_TOP_N = 25
    scored.sort(
        key=lambda d: (d['_hype_score'], d.get('created_at') or datetime.min.replace(tzinfo=timezone.utc)),
        reverse=True,
    )
    scored = scored[:HYPE_TOP_N]
    scored.sort(
        key=lambda d: d.get('created_at') or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )

    voted_map: dict = {}
    if user and scored:
        voted_map = await _user_voted_ids(user['user_id'], [d['feud_id'] for d in scored])
    for d in scored:
        my_vote = voted_map.get(d['feud_id']) if user else None
        _attach_percentages(d, revealed=bool(my_vote))
        d['my_vote'] = my_vote
        if isinstance(d.get('created_at'), datetime):
            d['created_at'] = _iso_utc(d['created_at'])
        d.pop('_hype_score', None)
        d.pop('_comments', None)
        d.pop('_replies', None)
    return {'feuds': scored, 'personalized': False, 'source': 'hype'}


@api_router.get('/feuds')
async def list_feuds(category: Optional[str] = None, user: Optional[dict] = Depends(get_current_user_optional)):
    q = {}
    if category and category != 'all':
        q['category'] = category
    # Only feuds from the last 24h appear in the live feed. Older ones live in the archive.
    q['created_at'] = {'$gte': now_utc() - timedelta(hours=24)}
    # Strict chronological order (newest first) across all categories. The
    # previous personalization ranking (affinity * recency) was reverted per
    # user request: the feed must feel like a news timeline where "1 min ago"
    # always precedes "20 min ago" which precedes "1 h ago", regardless of
    # which category the user engages with most.
    docs = await db.feuds.find(q, {'_id': 0}).sort('created_at', -1).to_list(200)
    voted_map: dict = {}
    if user and docs:
        voted_map = await _user_voted_ids(user['user_id'], [d['feud_id'] for d in docs])
    for d in docs:
        my_vote = voted_map.get(d['feud_id']) if user else None
        _attach_percentages(d, revealed=bool(my_vote))
        d['my_vote'] = my_vote
        if isinstance(d.get('created_at'), datetime):
            d['created_at'] = _iso_utc(d['created_at'])
    return {'feuds': docs, 'personalized': False}


async def _compute_user_affinity(user: dict) -> dict:
    """Returns {category_id: score} based on votes, comments, views and
    onboarding favorites. Higher = user is more engaged with that category."""
    uid = user['user_id']
    score: dict = {}
    # Votes → weight 4 (strongest engagement)
    async for v in db.votes.find(
        {'user_id': uid}, {'_id': 0, 'feud_id': 1, 'feud_snapshot': 1}
    ):
        cat = None
        snap = v.get('feud_snapshot') or {}
        if snap.get('category'):
            cat = snap['category']
        else:
            f = await db.feuds.find_one({'feud_id': v['feud_id']}, {'_id': 0, 'category': 1})
            if f:
                cat = f.get('category')
        if cat:
            score[cat] = score.get(cat, 0.0) + 4.0
    # Comments → weight 3
    async for c in db.comments.find({'user_id': uid}, {'_id': 0, 'feud_id': 1}):
        f = await db.feuds.find_one({'feud_id': c['feud_id']}, {'_id': 0, 'category': 1})
        if f and f.get('category'):
            score[f['category']] = score.get(f['category'], 0.0) + 3.0
    # Views → weight 1 (aggregated `count` per feud)
    async for v in db.feud_views.find(
        {'user_id': uid}, {'_id': 0, 'category': 1, 'count': 1}
    ):
        if v.get('category'):
            score[v['category']] = score.get(v['category'], 0.0) + 1.0 * float(v.get('count') or 1)
    # Onboarding favorites → flat +8 bonus (baseline)
    for fav in (user.get('favorite_categories') or []):
        score[fav] = score.get(fav, 0.0) + 8.0
    return score


@api_router.post('/feuds/{feud_id}/view')
async def record_view(feud_id: str, user: dict = Depends(get_current_user)):
    """Fire-and-forget: track that the user opened this feud's detail view.
    Used for personalization ranking on the /feuds home feed."""
    f = await db.feuds.find_one({'feud_id': feud_id}, {'_id': 0, 'category': 1})
    if not f:
        # Silently no-op on missing feud; don't punish the client.
        return {'ok': False}
    await db.feud_views.update_one(
        {'user_id': user['user_id'], 'feud_id': feud_id},
        {
            # `category` is only in $set — MongoDB refuses to update the same
            # path via both $setOnInsert and $set (write error code 40).
            '$setOnInsert': {'user_id': user['user_id'], 'feud_id': feud_id},
            '$set': {'last_viewed_at': now_utc(), 'category': f.get('category')},
            '$inc': {'count': 1},
        },
        upsert=True,
    )
    return {'ok': True}


# --- Favorites -----------------------------------------------------------------

async def _is_favorited(user_id: str, feud_id: str) -> bool:
    doc = await db.favorites.find_one(
        {'user_id': user_id, 'feud_id': feud_id}, {'_id': 1}
    )
    return doc is not None


async def _favorite_ids_for(user_id: str, feud_ids: List[str]) -> set:
    if not feud_ids:
        return set()
    cur = db.favorites.find(
        {'user_id': user_id, 'feud_id': {'$in': feud_ids}},
        {'_id': 0, 'feud_id': 1},
    )
    docs = await cur.to_list(len(feud_ids))
    return {d['feud_id'] for d in docs}


@api_router.post('/feuds/{feud_id}/favorite')
async def add_favorite(feud_id: str, user: dict = Depends(get_current_user)):
    """Add the feud to the user's favorites. Idempotent — if it already exists
    the `created_at` is REFRESHED so re-favoriting bumps it to the top of the
    favorites list (chronological order = most-recently-added first)."""
    f = await db.feuds.find_one({'feud_id': feud_id}, {'_id': 0, 'feud_id': 1})
    if not f:
        raise HTTPException(status_code=404, detail='Faida non trovata')
    await db.favorites.update_one(
        {'user_id': user['user_id'], 'feud_id': feud_id},
        {
            '$setOnInsert': {
                'user_id': user['user_id'],
                'feud_id': feud_id,
            },
            '$set': {'created_at': now_utc()},
        },
        upsert=True,
    )
    return {'ok': True, 'is_favorite': True}


@api_router.delete('/feuds/{feud_id}/favorite')
async def remove_favorite(feud_id: str, user: dict = Depends(get_current_user)):
    """Remove the feud from the user's favorites. No-op if not present."""
    await db.favorites.delete_one(
        {'user_id': user['user_id'], 'feud_id': feud_id}
    )
    return {'ok': True, 'is_favorite': False}


@api_router.get('/favorites')
async def list_favorites(user: dict = Depends(get_current_user)):
    """List the user's favorited feuds, most-recently-added first.

    If a favorited feud has been purged from Mongo (14-day retention) the entry
    is skipped silently — the client never sees dangling references.
    """
    fav_docs = await db.favorites.find(
        {'user_id': user['user_id']}, {'_id': 0}
    ).sort('created_at', -1).to_list(500)
    if not fav_docs:
        return {'feuds': []}
    order = {d['feud_id']: i for i, d in enumerate(fav_docs)}
    feud_ids = list(order.keys())
    feuds = await db.feuds.find(
        {'feud_id': {'$in': feud_ids}}, {'_id': 0}
    ).to_list(len(feud_ids))
    # Restore favorites order (most-recently-added first)
    feuds.sort(key=lambda f: order.get(f['feud_id'], 10**9))
    voted_map = await _user_voted_ids(user['user_id'], [f['feud_id'] for f in feuds])
    for d in feuds:
        my_vote = voted_map.get(d['feud_id'])
        _attach_percentages(d, revealed=bool(my_vote))
        d['my_vote'] = my_vote
        d['is_favorite'] = True
        if isinstance(d.get('created_at'), datetime):
            d['created_at'] = _iso_utc(d['created_at'])
    return {'feuds': feuds}


ARCHIVE_MAX_DAYS = 7


@api_router.get('/feuds/archive/dates')
async def archive_dates(category: Optional[str] = None):
    """List available archive dates (last 7 days, excluding today's live window <24h).

    Returns dates that have at least one feud in the given category (or across all).
    """
    now = now_utc()
    since = now - timedelta(days=ARCHIVE_MAX_DAYS)
    live_cutoff = now - timedelta(hours=24)
    match: dict = {'created_at': {'$gte': since, '$lt': live_cutoff}}
    if category and category != 'all':
        match['category'] = category
    pipeline = [
        {'$match': match},
        {'$group': {
            '_id': {'$dateToString': {'format': '%Y-%m-%d', 'date': '$created_at'}},
            'count': {'$sum': 1},
        }},
        {'$sort': {'_id': -1}},
    ]
    cursor = db.feuds.aggregate(pipeline)
    rows = await cursor.to_list(ARCHIVE_MAX_DAYS + 1)
    return {'dates': [{'date': r['_id'], 'count': r['count']} for r in rows]}


@api_router.get('/feuds/archive')
async def archive_feuds(
    date: str,
    category: Optional[str] = None,
    user: Optional[dict] = Depends(get_current_user_optional),
):
    """Return feuds for a specific archive day (YYYY-MM-DD) within the last 7 days."""
    try:
        day = datetime.strptime(date, '%Y-%m-%d').replace(tzinfo=timezone.utc)
    except ValueError:
        raise HTTPException(status_code=400, detail='Data non valida (usa YYYY-MM-DD)')
    now = now_utc()
    earliest = now - timedelta(days=ARCHIVE_MAX_DAYS)
    if day < earliest.replace(hour=0, minute=0, second=0, microsecond=0):
        raise HTTPException(status_code=400, detail=f'Archivio limitato a {ARCHIVE_MAX_DAYS} giorni')
    start = day
    end = day + timedelta(days=1)
    q: dict = {'created_at': {'$gte': start, '$lt': end}}
    if category and category != 'all':
        q['category'] = category
    docs = await db.feuds.find(q, {'_id': 0}).sort('created_at', -1).to_list(200)
    voted_map: dict = {}
    if user and docs:
        voted_map = await _user_voted_ids(user['user_id'], [d['feud_id'] for d in docs])
    for d in docs:
        my_vote = voted_map.get(d['feud_id']) if user else None
        # Same rule as the live feed: reveal percentages only if the user has
        # voted. Archive is read-mostly but voting is still allowed, so hiding
        # results preserves the "vote-to-see" contract.
        _attach_percentages(d, revealed=bool(my_vote))
        d['my_vote'] = my_vote
        d['archived'] = True
        if isinstance(d.get('created_at'), datetime):
            d['created_at'] = _iso_utc(d['created_at'])
    return {'feuds': docs, 'date': date}


@api_router.get('/search')
async def search_feuds(q: str, user: Optional[dict] = Depends(get_current_user_optional)):
    q = (q or '').strip()
    if not q:
        return {'feuds': []}
    # Try text search first; fallback to regex if index missing
    try:
        cursor = db.feuds.find(
            {'$text': {'$search': q}},
            {'_id': 0, 'score': {'$meta': 'textScore'}},
        ).sort([('score', {'$meta': 'textScore'})]).limit(50)
        docs = await cursor.to_list(50)
    except Exception:
        rx = re.compile(re.escape(q), re.IGNORECASE)
        docs = await db.feuds.find(
            {'$or': [{'title': rx}, {'summary': rx}, {'party_a': rx}, {'party_b': rx}]},
            {'_id': 0},
        ).limit(50).to_list(50)
    voted_map: dict = {}
    if user and docs:
        voted_map = await _user_voted_ids(user['user_id'], [d['feud_id'] for d in docs])
    for d in docs:
        d.pop('score', None)
        my_vote = voted_map.get(d['feud_id']) if user else None
        _attach_percentages(d, revealed=bool(my_vote))
        d['my_vote'] = my_vote
    return {'feuds': docs}


@api_router.get('/share/{feud_id}')
async def share_feud(feud_id: str):
    """Public share endpoint — always revealed, no auth required, no my_vote."""
    doc = await db.feuds.find_one({'feud_id': feud_id}, {'_id': 0})
    if not doc:
        raise HTTPException(status_code=404, detail='Faida non trovata')
    _attach_percentages(doc, revealed=True)
    doc['my_vote'] = None
    if isinstance(doc.get('created_at'), datetime):
        doc['created_at'] = _iso_utc(doc['created_at'])
    return {'feud': doc}


@api_router.get('/share/{feud_id}/html', response_class=HTMLResponse)
async def share_feud_html(feud_id: str, request: Request = None):
    doc = await db.feuds.find_one({'feud_id': feud_id}, {'_id': 0})
    if not doc:
        raise HTTPException(status_code=404, detail='Faida non trovata')
    _attach_percentages(doc, revealed=True)
    esc = html_lib.escape
    title = esc(doc.get('title', 'Populus'))
    summary = esc((doc.get('summary') or '')[:280])
    party_a = esc(doc.get('party_a', 'A'))
    party_b = esc(doc.get('party_b', 'B'))
    image = esc(doc.get('image_url', ''))
    pct_a = doc.get('pct_a', 50)
    pct_b = doc.get('pct_b', 50)
    total = doc.get('total_votes', 0)
    category = esc(doc.get('category_label', ''))
    canonical_url = str(request.url) if request is not None else ''
    canonical_url = esc(canonical_url)
    page = f"""<!doctype html>
<html lang=\"it\"><head>
<meta charset=\"utf-8\"/>
<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\"/>
<title>{title} · Populus</title>
<meta name=\"description\" content=\"{summary}\"/>
<meta property=\"og:type\" content=\"article\"/>
<meta property=\"og:site_name\" content=\"Populus\"/>
<meta property=\"og:title\" content=\"{title}\"/>
<meta property=\"og:description\" content=\"{summary}\"/>
<meta property=\"og:image\" content=\"{image}\"/>
<meta property=\"og:url\" content=\"{canonical_url}\"/>
<meta name=\"twitter:card\" content=\"summary_large_image\"/>
<meta name=\"twitter:title\" content=\"{title}\"/>
<meta name=\"twitter:description\" content=\"{summary}\"/>
<meta name=\"twitter:image\" content=\"{image}\"/>
<style>
  :root{{color-scheme:light}}
  body{{margin:0;background:#F4F4F0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;color:#0F0F0F}}
  .wrap{{max-width:640px;margin:0 auto;padding:16px}}
  .hdr{{background:#0F0F0F;color:#F4F4F0;padding:16px;border:2px solid #0F0F0F}}
  .brand{{font-size:28px;font-weight:700;letter-spacing:2px}}
  .kicker{{color:#FFE600;font-size:12px;letter-spacing:2px;margin-top:4px}}
  .hero{{width:100%;aspect-ratio:16/10;object-fit:cover;border:2px solid #0F0F0F;border-top:none;display:block}}
  .title{{font-size:26px;font-weight:700;line-height:1.15;margin:16px 0 8px}}
  .summary{{font-size:16px;line-height:1.5;color:#0F0F0F}}
  .split{{display:flex;margin-top:16px;border:2px solid #0F0F0F}}
  .half{{flex:1;padding:20px;text-align:center}}
  .half.a{{background:#FF3B30;color:#fff}}
  .half.b{{background:#FFE600;color:#0F0F0F}}
  .pct{{font-size:34px;font-weight:700;letter-spacing:1px}}
  .name{{font-size:13px;letter-spacing:1px;margin-top:4px}}
  .cta{{display:block;text-align:center;background:#0F0F0F;color:#F4F4F0;padding:16px;margin-top:16px;border:2px solid #0F0F0F;text-decoration:none;font-weight:700;letter-spacing:2px}}
  .meta{{margin-top:12px;font-size:12px;letter-spacing:1px;color:#6E6E6E}}
</style>
</head><body>
<div class=\"wrap\">
  <div class=\"hdr\">
    <div class=\"brand\">POPULUS</div>
    <div class=\"kicker\">{category}</div>
  </div>
  <img class=\"hero\" src=\"{image}\" alt=\"{title}\"/>
  <h1 class=\"title\">{title}</h1>
  <p class=\"summary\">{summary}</p>
  <div class=\"split\">
    <div class=\"half a\"><div class=\"pct\">{pct_a}%</div><div class=\"name\">{party_a}</div></div>
    <div class=\"half b\"><div class=\"pct\">{pct_b}%</div><div class=\"name\">{party_b}</div></div>
  </div>
  <div class=\"meta\">{total} VOTI · Con chi ti schieri?</div>
  <a class=\"cta\" href=\"/\">APRI POPULUS ›</a>
</div>
</body></html>"""
    return HTMLResponse(content=page)


@api_router.get('/feuds/{feud_id}')
async def get_feud(feud_id: str, user: Optional[dict] = Depends(get_current_user_optional)):
    doc = await db.feuds.find_one({'feud_id': feud_id}, {'_id': 0})
    if not doc:
        # Faide are purged from the DB after FEUD_RETENTION_DAYS days (see the
        # scheduler cleanup task). Return 410 Gone so the frontend can render
        # the "faida più vecchia di 2 settimane" screen.
        raise HTTPException(
            status_code=410,
            detail='Faida più vecchia di 2 settimane',
        )
    my_vote = None
    my_vote_changes = 0
    if user:
        vote = await db.votes.find_one({'feud_id': feud_id, 'user_id': user['user_id']}, {'_id': 0})
        my_vote = vote.get('side') if vote else None
        my_vote_changes = int(vote.get('change_count') or 0) if vote else 0
    _attach_percentages(doc, revealed=bool(my_vote))
    doc['my_vote'] = my_vote
    doc['my_vote_changes'] = my_vote_changes
    doc['my_vote_changes_left'] = max(0, MAX_VOTE_CHANGES - my_vote_changes)
    doc['sources'] = _filter_relevant_sources(doc)
    doc['is_favorite'] = bool(user and await _is_favorited(user['user_id'], feud_id))
    _ensure_hashtag(doc)
    if isinstance(doc.get('created_at'), datetime):
        doc['created_at'] = _iso_utc(doc['created_at'])
    return {'feud': doc}


def _ensure_hashtag(feud: dict) -> None:
    """Backfill/recompute hashtag fields on legacy feuds. In-place mutation.
    Detects single-subject mode when either party is a stance/position (or is
    too long to be a name), and in that case derives the subject from either
    the stored `subject` field or the feud title.
    """
    subject = (feud.get('subject') or '').strip() or None
    hs = feud.get('hashtag_subjects') if isinstance(feud.get('hashtag_subjects'), list) else None
    if not subject and not hs:
        # Legacy heuristic: if either party looks like a stance, extract the
        # subject from the title.
        pa, pb = feud.get('party_a', ''), feud.get('party_b', '')
        if _is_stance_party(pa) or _is_stance_party(pb):
            subject = _extract_subject_from_title(feud.get('title', '')) or None
            if subject:
                feud['subject'] = subject  # cache for the response only
    # Always recompute to reflect the current rules on legacy rows.
    feud['hashtag'] = _hashtag_key(
        feud.get('party_a', ''), feud.get('party_b', ''),
        subject=subject, hashtag_subjects=hs,
    )
    feud['hashtag_display'] = _hashtag_display(
        feud.get('party_a', ''), feud.get('party_b', ''),
        subject=subject, hashtag_subjects=hs,
    )


@api_router.get('/hashtags/{tag}')
async def feuds_by_hashtag(tag: str, user: Optional[dict] = Depends(get_current_user_optional)):
    """List all feuds matching a hashtag key (canonical form) within the retention
    window. Includes both live (24h) and archived (up to 14d) feuds."""
    key = _hashtag_norm(tag).replace('#', '')
    # Match either the stored `hashtag` key or compute-on-the-fly for legacy rows.
    docs = await db.feuds.find({}, {'_id': 0}).sort('created_at', -1).to_list(500)
    matched: List[dict] = []
    for d in docs:
        _ensure_hashtag(d)
        if d.get('hashtag') == key:
            matched.append(d)
    voted_map: dict = {}
    if user and matched:
        voted_map = await _user_voted_ids(user['user_id'], [d['feud_id'] for d in matched])
    for d in matched:
        my_vote = voted_map.get(d['feud_id']) if user else None
        _attach_percentages(d, revealed=bool(my_vote))
        d['my_vote'] = my_vote
        if isinstance(d.get('created_at'), datetime):
            d['created_at'] = _iso_utc(d['created_at'])
    display = matched[0]['hashtag_display'] if matched else f"#{tag}"
    return {'feuds': matched, 'hashtag': key, 'hashtag_display': display}


def _filter_relevant_sources(feud: dict) -> list:
    """Keep only sources actually related to the feud story.
    Rule: primary source (index 0) is always retained. Extra sources must share
    at least 2 significant tokens with title/parties OR mention a party name.
    Applied at read-time so legacy feuds get cleaned up transparently.
    """
    srcs = feud.get('sources') or []
    if len(srcs) <= 1:
        return srcs
    title_lc = (feud.get('title') or '').lower()
    party_a = (feud.get('party_a') or '').lower()
    party_b = (feud.get('party_b') or '').lower()
    key_terms = set(t for t in re.findall(r"\w{5,}", title_lc))
    for p in (party_a, party_b):
        key_terms |= set(t for t in re.findall(r"\w{4,}", p))
    kept = [srcs[0]]
    for s in srcs[1:]:
        ht = (s.get('title') or '').lower()
        if not ht:
            continue
        overlap = sum(1 for t in key_terms if t and t in ht)
        party_hit = (party_a and len(party_a) >= 4 and party_a in ht) or \
                    (party_b and len(party_b) >= 4 and party_b in ht)
        if overlap >= 2 or party_hit:
            kept.append(s)
    return kept


async def _recompute_user_alignment(user_id: str):
    votes = await db.votes.find({'user_id': user_id}, {'_id': 0}).to_list(1000)
    maj = 0
    minr = 0
    for v in votes:
        feud = await db.feuds.find_one({'feud_id': v['feud_id']}, {'_id': 0})
        if not feud:
            # Feud purged — use frozen aligned bit captured at purge time.
            if 'aligned_final' in v:
                if v['aligned_final']:
                    maj += 1
                else:
                    minr += 1
            continue
        a = feud.get('votes_a', 0)
        b = feud.get('votes_b', 0)
        if a == b:
            maj += 1
            continue
        winning_side = 'A' if a > b else 'B'
        if v['side'] == winning_side:
            maj += 1
        else:
            minr += 1
    await db.users.update_one(
        {'user_id': user_id},
        {'$set': {'majority_votes': maj, 'minority_votes': minr, 'total_votes': maj + minr,
                  'last_alignment_check': now_utc()}},
    )
    # Detect badge acquisition / transition and notify (push on first-ever,
    # in-app only on subsequent transitions).
    try:
        await _evaluate_and_notify_badge_change(user_id)
    except Exception as e:
        logger.warning(f"badge evaluate failed for {user_id}: {e}")


# ----------------------- Moderation -----------------------

BLOCKED_WORDS = {
    # Slurs + hate — keep this list conservative but non-empty for MVP
    'negro', 'frocio', 'finocchio', 'terrone', 'zingaro', 'ebreo di merda',
    'checca', 'ricchione', 'crucco', 'polentone', 'marocchino di merda',
    # Common Italian profanity/insults (strong)
    'vaffanculo', 'stronzo', 'stronza', 'coglione', 'coglioni', 'puttana', 'troia',
    'bastardo', 'bastarda', 'cazzo', 'cazzone', 'merda', 'porco dio', 'porca madonna',
    'figlio di puttana', 'figlia di puttana', 'mongoloide', 'ritardato', 'handicappato',
    'idiota di merda', 'schifoso', 'sfigato',
    # Threats
    'ti ammazzo', 'ti uccido', 'devi morire',
}


def _moderate_text(text: str) -> tuple[str, list[str]]:
    """Return (cleaned_text, flagged_words). If flagged non-empty, caller should reject."""
    original = (text or '').strip()
    if not original:
        return original, ['vuoto']
    lower = original.lower()
    hits = []
    for word in BLOCKED_WORDS:
        # match whole substring; use \b when word is single token to avoid false positives on unrelated substrings
        if ' ' in word:
            if word in lower:
                hits.append(word)
        else:
            if re.search(r'\b' + re.escape(word) + r'\b', lower):
                hits.append(word)
    return original, hits


async def _ai_moderate_comment(text: str) -> tuple[bool, Optional[str]]:
    """AI-based moderation — catches hate speech, threats and violence
    incitement that the keyword filter misses (paraphrased slurs, coded
    language, insinuations, calls to harm etc.).

    Returns (is_safe, reason). `reason` is a short Italian label for the
    audit log when the text is unsafe.

    Uses Claude Haiku 4.5 via emergentintegrations for latency (< 1s) and
    cost. Falls back to `is_safe=True` on any provider error so the app
    doesn't hard-fail moderation when the LLM is down — the keyword
    filter already ran and caught the low-hanging fruit.
    """
    if not EMERGENT_LLM_KEY:
        return True, None
    original = (text or '').strip()
    if not original or len(original) < 3:
        return True, None
    # Cap payload to keep latency low. Long rants are truncated but the
    # first 800 chars are more than enough for a hate/violence classifier.
    payload = original[:800]
    try:
        from emergentintegrations.llm.chat import LlmChat, UserMessage
    except Exception as e:
        logger.warning(f"ai-moderation: emergentintegrations import failed: {e}")
        return True, None
    try:
        chat = LlmChat(
            api_key=EMERGENT_LLM_KEY,
            session_id=f"mod-{new_id('mod')}",
            system_message=(
                "Sei un moderatore di contenuti per una community italiana. "
                "Il tuo compito: classificare un commento come SAFE o UNSAFE. "
                "UNSAFE se e solo se contiene una di queste cose: "
                "(1) hate speech verso una categoria protetta (razza, etnia, "
                "religione, orientamento sessuale, identità di genere, disabilità); "
                "(2) minaccia diretta o indiretta a una persona; "
                "(3) incitamento alla violenza o al danno fisico/psicologico; "
                "(4) molestia o doxxing (rivelazione di dati privati). "
                "Le opinioni forti, la critica politica anche aspra, la satira, "
                "il turpiloquio generico e i toni polemici sono SAFE. "
                "Rispondi ESCLUSIVAMENTE con una riga in questo formato: "
                "SAFE oppure UNSAFE|<categoria breve>. "
                "Esempi validi di risposta UNSAFE: "
                "UNSAFE|hate_speech, UNSAFE|minaccia, UNSAFE|incitamento_violenza."
            ),
        ).with_model('anthropic', 'claude-haiku-4-5-20251001')
        reply = await chat.send_message(UserMessage(text=payload))
        raw = (str(reply) if reply is not None else '').strip().upper()
        if raw.startswith('UNSAFE'):
            # Extract the short category after the pipe.
            parts = raw.split('|', 1)
            reason = parts[1].strip().lower() if len(parts) > 1 else 'unsafe'
            return False, reason
        return True, None
    except Exception as e:
        logger.warning(f"ai-moderation failed (allow-listing text): {e}")
        return True, None


async def _log_flagged(user_id: str, feud_id: str, text: str, hits: list[str]):
    try:
        await db.flagged_comments.insert_one({
            'flag_id': new_id('flag'),
            'user_id': user_id,
            'feud_id': feud_id,
            'text': text,
            'hits': hits,
            'created_at': now_utc(),
        })
    except Exception as e:
        logger.warning(f"failed to log flagged comment: {e}")


# ----------------------- RSS cache -----------------------

_RSS_CACHE: dict = {}  # key: cat_id -> (expires_ts, results)
_RSS_TTL_SECONDS = 5 * 60  # 5 minutes — match aggressive scheduler cadence


MAX_VOTE_CHANGES = 2


@api_router.post('/feuds/{feud_id}/vote')
async def vote_feud(feud_id: str, body: VoteBody, user: dict = Depends(get_current_user)):
    feud = await db.feuds.find_one({'feud_id': feud_id}, {'_id': 0})
    if not feud:
        raise HTTPException(status_code=404, detail='Faida non trovata')
    existing = await db.votes.find_one({'feud_id': feud_id, 'user_id': user['user_id']}, {'_id': 0})
    if existing:
        if existing.get('side') == body.side:
            raise HTTPException(status_code=400, detail='Hai già votato per questa parte')
        change_count = int(existing.get('change_count') or 0)
        if change_count >= MAX_VOTE_CHANGES:
            raise HTTPException(
                status_code=403,
                detail=f"Hai raggiunto il limite di {MAX_VOTE_CHANGES} cambi voto",
            )
        old_side = existing['side']
        dec_field = 'votes_a' if old_side == 'A' else 'votes_b'
        inc_field = 'votes_a' if body.side == 'A' else 'votes_b'
        pre = await db.feuds.find_one({'feud_id': feud_id}, {'_id': 0, 'votes_a': 1, 'votes_b': 1})
        pre_leader = 'A' if (pre.get('votes_a', 0) > pre.get('votes_b', 0)) else ('B' if pre.get('votes_b', 0) > pre.get('votes_a', 0) else None)
        await db.votes.update_one(
            {'vote_id': existing['vote_id']},
            {'$set': {
                'side': body.side,
                'change_count': change_count + 1,
                'updated_at': now_utc(),
            }},
        )
        await db.feuds.update_one(
            {'feud_id': feud_id},
            {'$inc': {dec_field: -1, inc_field: 1}},
        )
        # Coherence via visibility, not deletion:
        # A user's comments/replies persist in the DB but are only surfaced when
        # the user's CURRENT vote matches the comment/reply side. Switching back
        # to a previous faction re-exposes what was hidden, symmetric on both sides.
        await _recompute_user_alignment(user['user_id'])
        updated = await db.feuds.find_one({'feud_id': feud_id}, {'_id': 0})
        _attach_percentages(updated, revealed=True)
        updated['my_vote'] = body.side
        updated['my_vote_changes'] = change_count + 1
        updated['my_vote_changes_left'] = MAX_VOTE_CHANGES - (change_count + 1)
        await _notify_vote_flip(updated, pre_leader, user['user_id'])
        # Fire-and-forget alignment fanout: recomputes majority/minority (and
        # badges) for every other voter of this feud whenever the leader flips.
        asyncio.create_task(_fanout_alignment_recompute(feud_id, pre_leader, user['user_id']))
        return {'feud': updated, 'changed': True}
    await db.votes.insert_one({
        'vote_id': new_id('vote'), 'feud_id': feud_id, 'user_id': user['user_id'],
        'side': body.side, 'created_at': now_utc(), 'change_count': 0,
        # Denormalized snapshot — allows history to render feud preview even after
        # the feud is purged from `feuds` (retention: 14 days).
        'feud_snapshot': {
            'title': feud.get('title'),
            'category': feud.get('category'),
            'category_label': feud.get('category_label'),
            'party_a': feud.get('party_a'),
            'party_b': feud.get('party_b'),
            'image_url': feud.get('image_url'),
        },
    })
    inc_field = 'votes_a' if body.side == 'A' else 'votes_b'
    # Snapshot the current leader BEFORE applying the new vote so we can
    # detect a result flip caused by this vote.
    pre = await db.feuds.find_one({'feud_id': feud_id}, {'_id': 0, 'votes_a': 1, 'votes_b': 1})
    pre_leader = 'A' if (pre.get('votes_a', 0) > pre.get('votes_b', 0)) else ('B' if pre.get('votes_b', 0) > pre.get('votes_a', 0) else None)
    await db.feuds.update_one({'feud_id': feud_id}, {'$inc': {inc_field: 1}})
    await _recompute_user_alignment(user['user_id'])
    updated = await db.feuds.find_one({'feud_id': feud_id}, {'_id': 0})
    _attach_percentages(updated, revealed=True)
    updated['my_vote'] = body.side
    updated['my_vote_changes'] = 0
    updated['my_vote_changes_left'] = MAX_VOTE_CHANGES
    await _notify_vote_flip(updated, pre_leader, user['user_id'])
    # Fire-and-forget alignment fanout for other voters (badges + counters).
    asyncio.create_task(_fanout_alignment_recompute(feud_id, pre_leader, user['user_id']))
    return {'feud': updated, 'changed': False}



async def _fanout_alignment_recompute(feud_id: str, pre_leader: Optional[str], acting_user_id: str) -> None:
    """When a vote flips the winning side of a feud, every user who voted on
    that feud may now have a different majority/minority classification for
    this vote — which in turn may change their badge. Recompute alignment (and
    fire badge notifications) for all voters ASYNCHRONOUSLY so the vote
    request itself stays fast.

    The `pre_leader` param is the winning side BEFORE the acting vote; we only
    trigger fanout when the vote actually changed the leader.
    """
    try:
        f = await db.feuds.find_one({'feud_id': feud_id}, {'_id': 0, 'votes_a': 1, 'votes_b': 1})
        if not f:
            return
        a = f.get('votes_a', 0)
        b = f.get('votes_b', 0)
        if a == b:
            return  # tie doesn't count as a flip
        post_leader = 'A' if a > b else 'B'
        if post_leader == pre_leader:
            return
        cursor = db.votes.find(
            {'feud_id': feud_id, 'user_id': {'$ne': acting_user_id}},
            {'_id': 0, 'user_id': 1},
        )
        voters = await cursor.to_list(10000)
        seen: set = set()
        for v in voters:
            uid = v.get('user_id')
            if not uid or uid in seen:
                continue
            seen.add(uid)
            try:
                await _recompute_user_alignment(uid)
            except Exception as e:
                logger.warning(f"alignment recompute failed for {uid} in fanout: {e}")
        if seen:
            logger.info(f"vote flip fanout: recomputed alignment for {len(seen)} voters of {feud_id}")
    except Exception as e:
        logger.warning(f"fanout failed for {feud_id}: {e}")



async def _notify_vote_flip(feud: dict, pre_leader: Optional[str], acting_user_id: str) -> None:
    """If this vote flipped the leading side, notify the voters who now find
    themselves on the losing side. Max 1 push per user per day."""
    va = feud.get('votes_a', 0)
    vb = feud.get('votes_b', 0)
    if va == vb:
        return
    new_leader = 'A' if va > vb else 'B'
    if pre_leader is None or new_leader == pre_leader:
        return
    losing_side = pre_leader
    voters = await db.votes.find(
        {'feud_id': feud['feud_id'], 'side': losing_side, 'user_id': {'$ne': acting_user_id}},
        {'_id': 0, 'user_id': 1},
    ).to_list(200)
    if not voters:
        return
    title_short = (feud.get('title') or 'una faida')[:50]
    for v in voters:
        uid = v['user_id']
        try:
            if not await _daily_lock(uid, 'vote_flip'):
                continue
            await _emit_notification(
                uid,
                'vote_flip',
                title="Il risultato si è ribaltato!",
                body=f"«{title_short}»: ora è in vantaggio la fazione opposta alla tua.",
                feud_id=feud['feud_id'],
                send_push_too=True,
            )
        except Exception as e:
            logger.warning(f"vote-flip notify failed for {uid}: {e}")


# ----------------------- Sponsors -----------------------

SEED_SPONSORS = [
    {'category': 'politica', 'sponsor': 'IlPost', 'headline': 'Approfondimenti quotidiani sulla politica.', 'cta': 'ABBONATI', 'image_url': 'https://images.unsplash.com/photo-1541872703-74c5e44368f6?w=800'},
    {'category': 'tv', 'sponsor': 'Infinity+', 'headline': 'Rivedi ogni puntata del reality del momento.', 'cta': 'GUARDA ORA', 'image_url': 'https://images.unsplash.com/photo-1585951237318-9ea5e175b891?w=800'},
    {'category': 'musica', 'sponsor': 'Spotify', 'headline': 'La playlist ufficiale della faida.', 'cta': 'ASCOLTA', 'image_url': 'https://images.unsplash.com/photo-1493225457124-a3eb161ffa5f?w=800'},
    {'category': 'sport', 'sponsor': 'DAZN', 'headline': 'Rivedi il derby integrale con moviola.', 'cta': 'REPLAY', 'image_url': 'https://images.unsplash.com/photo-1508098682722-e99c43a406b2?w=800'},
    {'category': 'cinema', 'sponsor': 'Netflix', 'headline': 'Il film della polemica: guardalo stasera.', 'cta': 'GUARDA', 'image_url': 'https://images.unsplash.com/photo-1489599849927-2ee91cede3ba?w=800'},
    {'category': 'social', 'sponsor': 'TrendReport', 'headline': 'Analisi virali ogni 24 ore.', 'cta': 'ISCRIVITI', 'image_url': 'https://images.unsplash.com/photo-1611162617213-7d7a39e9b1d7?w=800'},
    {'category': 'gossip', 'sponsor': 'Chi Magazine', 'headline': 'Tutti i retroscena in edicola.', 'cta': 'SFOGLIA', 'image_url': 'https://images.unsplash.com/photo-1561890244-e880c1e6d54e?w=800'},
    {'category': 'tech', 'sponsor': 'Amazon Prime Day', 'headline': 'Le offerte tech del giorno, prima di tutti.', 'cta': 'SCOPRI', 'image_url': 'https://images.unsplash.com/photo-1518770660439-4636190af475?w=800'},
    {'category': 'cronaca', 'sponsor': 'Cronache Italia', 'headline': 'Cronaca nera e casi mai risolti: inchieste in edicola.', 'cta': 'LEGGI', 'image_url': 'https://images.unsplash.com/photo-1495556650867-99590cea3657?w=800'},
]


@api_router.get('/sponsors')
async def get_sponsors(category: Optional[str] = None):
    q = {}
    if category and category != 'all':
        q['category'] = category
    docs = await db.sponsors.find(q, {'_id': 0}).to_list(50)
    return {'sponsors': docs}


async def seed_sponsors_if_empty():
    # Upsert one seed sponsor per category (idempotent, safe to run at every startup).
    for s in SEED_SPONSORS:
        existing = await db.sponsors.find_one({'category': s['category']})
        if not existing:
            await db.sponsors.insert_one({'sponsor_id': new_id('spo'), **s, 'created_at': now_utc()})
            logger.info(f"Seeded sponsor for category {s['category']}")


# ----------------------- Voting History -----------------------

FEUD_RETENTION_DAYS = 14


def _snapshot_from_vote(v: dict) -> Optional[dict]:
    """Return the denormalized feud info stored on the vote (may be missing for
    votes created before the snapshot column existed)."""
    snap = v.get('feud_snapshot') or {}
    if not snap.get('title'):
        return None
    return snap


async def _build_history_item(v: dict) -> Optional[dict]:
    """Assemble a single history entry for a vote — falls back to the
    denormalized snapshot when the underlying feud has been purged."""
    feud = await db.feuds.find_one({'feud_id': v['feud_id']}, {'_id': 0})
    feud_deleted = feud is None
    if feud_deleted:
        snap = _snapshot_from_vote(v)
        if not snap:
            return None  # legacy vote without snapshot AND feud gone → skip
        title = snap['title']
        cat_label = snap['category_label']
        party_a = snap['party_a']
        party_b = snap['party_b']
        # Winning side frozen at deletion time (if available) OR unknown.
        winning_side = v.get('winning_side_final')
        aligned = v.get('aligned_final', True)
    else:
        title = feud['title']
        cat_label = feud['category_label']
        party_a = feud['party_a']
        party_b = feud['party_b']
        a = feud.get('votes_a', 0)
        b = feud.get('votes_b', 0)
        if a == b:
            winning_side = None
            aligned = True  # tie counts as majority
        else:
            winning_side = 'A' if a > b else 'B'
            aligned = (v['side'] == winning_side)
    return {
        'feud_id': v['feud_id'],
        'title': title,
        'category_label': cat_label,
        'party_a': party_a,
        'party_b': party_b,
        'side_voted': v['side'],
        'winning_side': winning_side,
        'aligned': aligned,
        'feud_deleted': feud_deleted,
        'voted_at': _iso_utc(v['created_at']) if isinstance(v['created_at'], datetime) else v['created_at'],
    }


async def _history_for_user(user_id: str, filter: str) -> list:
    votes = await db.votes.find({'user_id': user_id}, {'_id': 0}).sort('created_at', -1).to_list(1000)
    items = []
    for v in votes:
        it = await _build_history_item(v)
        if not it:
            continue
        if filter == 'majority' and not it['aligned']:
            continue
        if filter == 'minority' and it['aligned']:
            continue
        items.append(it)
    return items


@api_router.get('/users/me/history')
async def my_history(filter: str = 'all', user: dict = Depends(get_current_user)):
    return {'history': await _history_for_user(user['user_id'], filter)}


@api_router.get('/users/{user_id}/history')
async def public_user_history(
    user_id: str,
    filter: str = 'all',
    user: Optional[dict] = Depends(get_current_user_optional),
):
    u = await db.users.find_one(
        {'user_id': user_id},
        {'_id': 0, 'user_id': 1, 'auth_provider': 1, 'history_public_generic': 1, 'history_public_mutual': 1},
    )
    if not u:
        raise HTTPException(status_code=404, detail='Utente non trovato')
    if u.get('auth_provider') == 'anonymous':
        # Anonymous voting history is hidden from other users.
        return {'history': [], 'is_anonymous': True, 'hidden': True, 'reason': 'anonymous'}

    # Owner viewing self — always visible.
    viewer_id = user['user_id'] if user else None
    if viewer_id == user_id:
        return {'history': await _history_for_user(user_id, filter), 'hidden': False}

    # Default both flags to True (backwards compatible with existing users).
    public_generic = u.get('history_public_generic')
    public_mutual = u.get('history_public_mutual')
    if public_generic is None:
        public_generic = True
    if public_mutual is None:
        public_mutual = True

    # Determine if viewer is in "cerchia bilaterale" (mutual circle) with owner.
    # Requires BOTH friendship rows: viewer→owner AND owner→viewer.
    is_mutual = False
    if viewer_id:
        a = await db.friendships.find_one({'user_id': viewer_id, 'friend_id': user_id}, {'_id': 0, 'user_id': 1})
        b = await db.friendships.find_one({'user_id': user_id, 'friend_id': viewer_id}, {'_id': 0, 'user_id': 1})
        is_mutual = bool(a and b)

    # Visibility rule: mutual-circle members follow the mutual flag; everyone
    # else follows the generic flag. If either flag applies and is False the
    # history is hidden with a reason the frontend can render.
    if is_mutual:
        if not public_mutual:
            return {'history': [], 'hidden': True, 'reason': 'mutual_private'}
    else:
        if not public_generic:
            return {'history': [], 'hidden': True, 'reason': 'private'}
    return {'history': await _history_for_user(user_id, filter), 'hidden': False}


@api_router.patch('/users/me/history-privacy')
async def update_history_privacy(body: dict, user: dict = Depends(get_current_user)):
    """Toggle the two visibility flags controlling who can see the voting
    history on the owner's public profile.

    Body accepts optional booleans:
      - `generic`: whether NON mutual-circle users can see the history.
      - `mutual`:  whether MUTUAL-circle users can see the history.

    Both default to True on brand-new accounts so behaviour is backwards
    compatible with the previous "always public" implementation.
    """
    updates: dict = {}
    if isinstance(body.get('generic'), bool):
        updates['history_public_generic'] = body['generic']
    if isinstance(body.get('mutual'), bool):
        updates['history_public_mutual'] = body['mutual']
    if not updates:
        raise HTTPException(status_code=400, detail='Nessun campo valido')
    await db.users.update_one({'user_id': user['user_id']}, {'$set': updates})
    doc = await db.users.find_one({'user_id': user['user_id']}, {'_id': 0, 'history_public_generic': 1, 'history_public_mutual': 1})
    return {
        'history_public_generic': True if doc.get('history_public_generic') is None else bool(doc.get('history_public_generic')),
        'history_public_mutual': True if doc.get('history_public_mutual') is None else bool(doc.get('history_public_mutual')),
    }



@api_router.get('/feuds/{feud_id}/comments')
async def get_comments(feud_id: str, user: Optional[dict] = Depends(get_current_user_optional)):
    docs = await db.comments.find({'feud_id': feud_id}, {'_id': 0}).sort('created_at', -1).to_list(500)
    # Visibility rule: a comment is shown only if its author is CURRENTLY voting
    # for the same side the comment was posted on. Comments where the author has
    # since switched sides are hidden — they reappear if the author switches
    # back to the original faction.
    if docs:
        uids = list({c['user_id'] for c in docs})
        current_votes = await db.votes.find(
            {'feud_id': feud_id, 'user_id': {'$in': uids}},
            {'_id': 0, 'user_id': 1, 'side': 1},
        ).to_list(len(uids))
        current = {v['user_id']: v['side'] for v in current_votes}
        docs = [c for c in docs if current.get(c['user_id'], c['side']) == c['side']]
        if docs:
            # Batch-count only *visible* replies (author's current vote matches).
            cmt_ids = [c['comment_id'] for c in docs]
            all_replies = await db.replies.find(
                {'comment_id': {'$in': cmt_ids}},
                {'_id': 0, 'comment_id': 1, 'user_id': 1, 'side': 1},
            ).to_list(10000)
            extra_uids = list({r['user_id'] for r in all_replies} - set(current.keys()))
            if extra_uids:
                extra = await db.votes.find(
                    {'feud_id': feud_id, 'user_id': {'$in': extra_uids}},
                    {'_id': 0, 'user_id': 1, 'side': 1},
                ).to_list(len(extra_uids))
                for v in extra:
                    current[v['user_id']] = v['side']
            reply_counts: dict = {}
            for r in all_replies:
                if current.get(r['user_id'], r['side']) == r['side']:
                    reply_counts[r['comment_id']] = reply_counts.get(r['comment_id'], 0) + 1
            for c in docs:
                c['reply_count'] = reply_counts.get(c['comment_id'], 0)
                c['nickname_side'] = c['side']
    for c in docs:
        if isinstance(c.get('created_at'), datetime):
            c['created_at'] = _iso_utc(c['created_at'])

    # Viewer-personalised ordering.
    # ────────────────────────────
    # For authenticated viewers we surface conversations that matter most:
    #   Bucket 0 → authors that belong to the VIEWER's Cerchia del Gossip.
    #   Bucket 1 → authors NOT in the viewer's circle but among their
    #              "preferiti" — anyone they've exchanged private messages
    #              with (a lightweight proxy for closeness that's cheap to
    #              compute and reasonably accurate).
    #   Bucket 2 → everyone else, sorted first by popularity (reply_count),
    #              then by recency, mimicking the "top comments" style of
    #              other social apps.
    # Anonymous viewers keep the plain chronological ordering that was in
    # place before this personalisation layer.
    if docs and user:
        viewer_id = user['user_id']
        author_ids = list({c['user_id'] for c in docs})
        # Circle members (viewer → author friendship).
        my_circle_rows = await db.friendships.find(
            {'user_id': viewer_id, 'friend_id': {'$in': author_ids}},
            {'_id': 0, 'friend_id': 1},
        ).to_list(len(author_ids))
        my_circle_ids: set[str] = {r['friend_id'] for r in my_circle_rows}
        # DM contacts — union of counterparties in both directions.
        dm_rows = await db.messages.find(
            {
                '$or': [
                    {'sender_id': viewer_id, 'recipient_id': {'$in': author_ids}},
                    {'recipient_id': viewer_id, 'sender_id': {'$in': author_ids}},
                ],
                'deleted': {'$ne': True},
            },
            {'_id': 0, 'sender_id': 1, 'recipient_id': 1},
        ).to_list(2000)
        dm_contacts: set[str] = set()
        for m in dm_rows:
            other = m['recipient_id'] if m['sender_id'] == viewer_id else m['sender_id']
            dm_contacts.add(other)

        def _bucket(c: dict) -> int:
            uid = c['user_id']
            if uid == viewer_id:
                return 0  # viewer's own comments alongside their circle
            if uid in my_circle_ids:
                return 0
            if uid in dm_contacts:
                return 1
            return 2

        def _ts(c: dict) -> float:
            v = c.get('created_at')
            if isinstance(v, str):
                try:
                    return datetime.fromisoformat(v.replace('Z', '+00:00')).timestamp()
                except Exception:
                    return 0.0
            if isinstance(v, datetime):
                return v.timestamp()
            return 0.0

        def _sort_key(c: dict):
            b = _bucket(c)
            # Bucket 0/1 → recency wins.
            # Bucket 2 → popularity (reply_count) first, then recency.
            if b in (0, 1):
                return (b, -_ts(c))
            return (b, -int(c.get('reply_count') or 0), -_ts(c))

        docs.sort(key=_sort_key)
    a = [c for c in docs if c['side'] == 'A']
    b = [c for c in docs if c['side'] == 'B']
    return {'side_a': a, 'side_b': b}


# ────────── AI faction summary (Sintesi del pensiero) ──────────
# On-demand AI synthesis of what each faction is arguing in the
# comments section. Rebuilt fresh on every call so that as new
# comments arrive the summary sharpens.

async def _ai_faction_summary(feud: dict, comments_a: list[dict], comments_b: list[dict]) -> Optional[dict]:
    """Ask Claude to distil the top arguments per side plus common ground.

    Returns a dict `{side_a: [str], side_b: [str], common: [str],
    generated_at: iso}` or None on any provider error. Bullets are short
    (≤ 22 words) and in Italian.
    """
    if not EMERGENT_LLM_KEY:
        return None
    try:
        from emergentintegrations.llm.chat import LlmChat, UserMessage
    except Exception as e:
        logger.warning(f"ai-summary: emergentintegrations import failed: {e}")
        return None

    def _prep(rows: list[dict], side_label: str) -> str:
        # Compose "Reply → Comment" blocks. Cap payload so a very noisy feud
        # doesn't blow the token budget: keep the 25 most-recent comments,
        # each truncated to ~250 chars. Same limit per side.
        rows = rows[:25]
        blocks: list[str] = []
        for c in rows:
            txt = (c.get('text') or '').strip().replace('\n', ' ')[:250]
            if not txt:
                continue
            blocks.append(f"- {txt}")
        return f"### {side_label}\n" + ("\n".join(blocks) if blocks else "(nessun commento)")

    a_block = _prep(comments_a, f"TEAM A — {feud.get('party_a') or 'Team A'}")
    b_block = _prep(comments_b, f"TEAM B — {feud.get('party_b') or 'Team B'}")

    prompt = (
        f"FAIDA: {feud.get('title') or ''}\n"
        f"DOMANDA: {feud.get('question') or ''}\n\n"
        f"COMMENTI DELLE DUE FAZIONI (in italiano):\n\n"
        f"{a_block}\n\n{b_block}\n\n"
        "Il tuo compito è produrre una sintesi FEDELE e non tendenziosa dei "
        "PRINCIPALI argomenti che ciascuna fazione sta portando A SOSTEGNO "
        "del proprio voto. Evita generici, entra sempre nel merito.\n\n"
        "REGOLE:\n"
        "1. 2–4 bullet per fazione (max 22 parole ciascuno).\n"
        "2. Ogni bullet deve essere una TESI CONCRETA, non un'etichetta.\n"
        "3. NON inserire ideologie mai citate nei commenti.\n"
        "4. Cerca SOVRAPPOSIZIONI: se entrambe le fazioni concordano su un "
        "punto (es. 'la situazione è vergognosa', 'servono regole più chiare'), "
        "mettilo in `common` (0–3 bullet). Se non c'è nulla di veramente comune, "
        "lascia l'array VUOTO.\n"
        "5. Se la sezione commenti è DAVVERO vuota (nessuna opinione "
        "leggibile per una fazione, o meno di 2 commenti reali complessivi), "
        'rispondi {"empty": true}. Altrimenti sforzati sempre di produrre '
        "almeno 1 bullet per fazione, anche solo distillando l'unica tesi "
        "principale rilevabile.\n\n"
        "Rispondi SOLO con questo JSON, in italiano, senza commenti né testo extra:\n"
        '{"side_a": ["bullet 1", "bullet 2", "..."], '
        '"side_b": ["bullet 1", "bullet 2", "..."], '
        '"common": ["punto in comune 1", "..."]}'
    )
    try:
        chat = LlmChat(
            api_key=EMERGENT_LLM_KEY,
            session_id=f"sum-{feud.get('feud_id') or new_id('sum')}",
            system_message=(
                "Sei un analista politico italiano imparziale. Distilli il "
                "pensiero della gente nei commenti in bullet nitidi, senza "
                "prendere posizione."
            ),
        ).with_model('anthropic', 'claude-sonnet-4-6')
        reply = await chat.send_message(UserMessage(text=prompt))
        raw = str(reply) if reply is not None else ''
        m = re.search(r'\{[\s\S]*\}', raw)
        if not m:
            return None
        data = json.loads(m.group(0))
    except Exception as e:
        logger.warning(f"ai-summary failed: {e}")
        return None

    if data.get('empty') is True:
        return {'side_a': [], 'side_b': [], 'common': [], 'empty': True, 'generated_at': _iso_utc(now_utc())}

    def _clean(arr) -> list[str]:
        if not isinstance(arr, list):
            return []
        out: list[str] = []
        for x in arr[:6]:
            if isinstance(x, str) and x.strip():
                out.append(x.strip()[:220])
        return out

    return {
        'side_a': _clean(data.get('side_a')),
        'side_b': _clean(data.get('side_b')),
        'common': _clean(data.get('common')),
        'empty': False,
        'generated_at': _iso_utc(now_utc()),
    }


@api_router.post('/feuds/{feud_id}/ai-summary')
async def get_ai_summary(feud_id: str, user: dict = Depends(get_current_user)):
    """Return a fresh AI synthesis of the faction arguments for this feud.

    Idempotent-ish: each call regenerates from the current visible
    comments so the summary keeps sharpening as new opinions land.
    Requires auth to reduce abuse of the LLM budget.
    """
    feud = await db.feuds.find_one({'feud_id': feud_id}, {'_id': 0})
    if not feud:
        raise HTTPException(status_code=404, detail='Faida non trovata')
    # Reuse the get_comments logic to obtain visible comments only. This
    # respects the "author must still be on the same side" filter so we
    # never analyse ghost opinions.
    data = await get_comments(feud_id, user)  # type: ignore
    side_a = data.get('side_a') or []
    side_b = data.get('side_b') or []
    if not side_a and not side_b:
        return {
            'side_a': [], 'side_b': [], 'common': [],
            'empty': True, 'generated_at': _iso_utc(now_utc()),
            'party_a': feud.get('party_a'),
            'party_b': feud.get('party_b'),
        }
    summary = await _ai_faction_summary(feud, side_a, side_b)
    if summary is None:
        raise HTTPException(status_code=503, detail='Sintesi AI non disponibile al momento. Riprova.')
    summary['party_a'] = feud.get('party_a')
    summary['party_b'] = feud.get('party_b')
    return summary


@api_router.post('/feuds/{feud_id}/comments')
async def add_comment(feud_id: str, body: CommentBody, user: dict = Depends(get_current_user)):
    vote = await db.votes.find_one({'feud_id': feud_id, 'user_id': user['user_id']}, {'_id': 0})
    if not vote:
        raise HTTPException(status_code=400, detail='Devi prima votare')
    clean_text, flagged = _moderate_text(body.text)
    if flagged:
        await _log_flagged(user['user_id'], feud_id, body.text, flagged)
        raise HTTPException(status_code=400, detail=f"Commento bloccato: contiene termini non consentiti ({', '.join(flagged)})")
    ai_safe, ai_reason = await _ai_moderate_comment(clean_text)
    if not ai_safe:
        await _log_flagged(user['user_id'], feud_id, clean_text, [f'ai:{ai_reason or "unsafe"}'])
        raise HTTPException(
            status_code=400,
            detail='Commento bloccato: contenuto non consentito (hate speech, minacce o incitamento alla violenza).',
        )
    doc = {
        'comment_id': new_id('cmt'), 'feud_id': feud_id, 'user_id': user['user_id'],
        'nickname': user.get('nickname'), 'side': vote['side'], 'text': clean_text,
        'created_at': now_utc(),
    }
    await db.comments.insert_one(doc)
    doc.pop('_id', None)
    doc['reply_count'] = 0
    # normalize datetime
    doc['created_at'] = _iso_utc(doc['created_at'])
    return {'comment': doc}


@api_router.delete('/comments/{comment_id}')
async def delete_comment(comment_id: str, user: dict = Depends(get_current_user)):
    """Delete a comment authored by the current user.

    Cascades the delete to the comment's replies so orphan replies never
    linger. Returns 403 if the caller is not the comment's author.
    """
    doc = await db.comments.find_one({'comment_id': comment_id}, {'_id': 0})
    if not doc:
        raise HTTPException(status_code=404, detail='Commento non trovato')
    if doc.get('user_id') != user['user_id']:
        raise HTTPException(status_code=403, detail='Puoi eliminare solo i tuoi commenti')
    await db.replies.delete_many({'comment_id': comment_id})
    await db.comments.delete_one({'comment_id': comment_id})
    return {'ok': True}


@api_router.delete('/replies/{reply_id}')
async def delete_reply(reply_id: str, user: dict = Depends(get_current_user)):
    """Delete a reply authored by the current user."""
    doc = await db.replies.find_one({'reply_id': reply_id}, {'_id': 0})
    if not doc:
        raise HTTPException(status_code=404, detail='Risposta non trovata')
    if doc.get('user_id') != user['user_id']:
        raise HTTPException(status_code=403, detail='Puoi eliminare solo le tue risposte')
    await db.replies.delete_one({'reply_id': reply_id})
    return {'ok': True}


@api_router.get('/comments/{comment_id}/replies')
async def list_replies(comment_id: str):
    docs = await db.replies.find({'comment_id': comment_id}, {'_id': 0}).sort('created_at', 1).to_list(500)
    if docs:
        # Same visibility rule as comments: a reply is shown only if its author
        # is currently voting on the side the reply was posted on.
        feud_id = docs[0].get('feud_id')
        uids = list({r['user_id'] for r in docs})
        if feud_id:
            current_votes = await db.votes.find(
                {'feud_id': feud_id, 'user_id': {'$in': uids}},
                {'_id': 0, 'user_id': 1, 'side': 1},
            ).to_list(len(uids))
            current = {v['user_id']: v['side'] for v in current_votes}
            docs = [r for r in docs if current.get(r['user_id'], r['side']) == r['side']]
            for r in docs:
                r['nickname_side'] = r['side']
    for r in docs:
        if isinstance(r.get('created_at'), datetime):
            r['created_at'] = _iso_utc(r['created_at'])
    return {'replies': docs}


@api_router.post('/comments/{comment_id}/replies')
async def add_reply(comment_id: str, body: ReplyBody, user: dict = Depends(get_current_user)):
    parent = await db.comments.find_one({'comment_id': comment_id}, {'_id': 0})
    if not parent:
        raise HTTPException(status_code=404, detail='Commento non trovato')
    vote = await db.votes.find_one({'feud_id': parent['feud_id'], 'user_id': user['user_id']}, {'_id': 0})
    side = vote['side'] if vote else parent['side']
    clean_text, flagged = _moderate_text(body.text)
    if flagged:
        await _log_flagged(user['user_id'], parent['feud_id'], body.text, flagged)
        raise HTTPException(status_code=400, detail=f"Risposta bloccata: contiene termini non consentiti ({', '.join(flagged)})")
    ai_safe, ai_reason = await _ai_moderate_comment(clean_text)
    if not ai_safe:
        await _log_flagged(user['user_id'], parent['feud_id'], clean_text, [f'ai:{ai_reason or "unsafe"}'])
        raise HTTPException(
            status_code=400,
            detail='Risposta bloccata: contenuto non consentito (hate speech, minacce o incitamento alla violenza).',
        )
    doc = {
        'reply_id': new_id('rep'), 'comment_id': comment_id, 'feud_id': parent['feud_id'],
        'user_id': user['user_id'], 'nickname': user.get('nickname'), 'side': side,
        'text': clean_text, 'created_at': now_utc(),
    }
    await db.replies.insert_one(doc)
    doc.pop('_id', None)
    doc['created_at'] = _iso_utc(doc['created_at'])
    # Emit an in-app notification to the parent-comment author (unless they
    # replied to themselves). Fire-and-forget: notification failures never
    # break the reply flow.
    try:
        if parent.get('user_id') and parent['user_id'] != user['user_id']:
            feud = await db.feuds.find_one(
                {'feud_id': parent['feud_id']}, {'_id': 0, 'title': 1}
            )
            feud_title = (feud or {}).get('title') or 'una faida'
            await _emit_notification(
                parent['user_id'],
                'reply',
                title=f"{user.get('nickname', 'Qualcuno')} ha risposto al tuo commento",
                body=f"Su «{feud_title[:60]}»: {clean_text[:80]}",
                feud_id=parent['feud_id'],
                comment_id=comment_id,
                side=parent.get('side'),
                send_push_too=True,
            )
    except Exception as e:
        logger.warning(f"notification emit (reply) failed: {e}")
    return {'reply': doc}


async def _emit_notification(user_id: str, ntype: str, *, title: str, body: str,
                              feud_id: Optional[str] = None,
                              comment_id: Optional[str] = None,
                              side: Optional[str] = None,
                              send_push_too: bool = False) -> None:
    """Write a lightweight in-app notification. Bounded auto-cleanup keeps at
    most 200 notifications per user (oldest pruned). When `send_push_too` is
    true, also fires a mobile push notification via the Emergent relay."""
    doc = {
        'notif_id': new_id('notif'),
        'user_id': user_id,
        'type': ntype,
        'title': title[:120],
        'body': body[:280],
        'feud_id': feud_id,
        'comment_id': comment_id,
        'side': side,
        'read': False,
        'created_at': now_utc(),
    }
    await db.notifications.insert_one(doc)
    # Best-effort prune: keep only the newest 200 per user.
    count = await db.notifications.count_documents({'user_id': user_id})
    if count > 200:
        overflow = count - 200
        to_del = await db.notifications.find(
            {'user_id': user_id}, {'_id': 0, 'notif_id': 1, 'created_at': 1}
        ).sort('created_at', 1).limit(overflow).to_list(overflow)
        ids = [d['notif_id'] for d in to_del]
        if ids:
            await db.notifications.delete_many({'notif_id': {'$in': ids}})
    if send_push_too:
        try:
            deeplink = f"/feud/{feud_id}" if feud_id else "/notifications"
            if comment_id:
                deeplink += f"?comment={comment_id}"
                if side:
                    deeplink += f"&side={side}"
            await send_push(
                recipients=[user_id],
                data={'title': title[:60], 'message': body[:120], 'action_url': deeplink},
            )
        except Exception as e:
            logger.warning(f"push notification failed (non-blocking): {e}")


# --- Emergent Push relay -----------------------------------------------------
PUSH_BASE_URL = 'https://integrations.emergentagent.com'
PUSH_KEY = os.environ.get('EMERGENT_PUSH_KEY', 'placeholder')
_push_client = httpx.AsyncClient(
    base_url=PUSH_BASE_URL,
    headers={'X-Push-Key': PUSH_KEY},
    timeout=10.0,
)


class RegisterPushBody(BaseModel):
    platform: str
    device_token: str


class PushToggleBody(BaseModel):
    enabled: bool


@api_router.post('/register-push', status_code=201)
async def register_push(body: RegisterPushBody, user: dict = Depends(get_current_user)):
    """Store the device push token via Emergent's relay (SuprSend). We don't
    persist tokens locally — SuprSend handles rotation and resolution."""
    # Also flip the user's push-enabled flag on if it's not explicitly off.
    await db.users.update_one(
        {'user_id': user['user_id']},
        {'$setOnInsert': {'push_notifications': True}},
        upsert=False,
    )
    try:
        resp = await _push_client.post(
            '/api/v1/push/users/register',
            json={
                'user_id': user['user_id'],
                'platform': body.platform,
                'device_token': body.device_token,
            },
        )
        if resp.status_code == 401:
            raise HTTPException(status_code=500, detail='EMERGENT_PUSH_KEY missing or invalid')
        if resp.status_code >= 500:
            raise HTTPException(status_code=502, detail='Push provider unavailable')
        resp.raise_for_status()
    except HTTPException:
        raise
    except Exception as e:
        logger.warning(f"register-push relay failed: {e}")
        raise HTTPException(status_code=502, detail='Push provider unreachable')
    return {'status': 'registered'}


@api_router.post('/settings/push')
async def toggle_push(body: PushToggleBody, user: dict = Depends(get_current_user)):
    """User-controlled ON/OFF switch (Profilo → Notifiche push). When off, the
    hot-news fanout skips them; the tap-registration endpoint still runs but
    the flag governs delivery choice."""
    await db.users.update_one(
        {'user_id': user['user_id']},
        {'$set': {'push_notifications': bool(body.enabled)}},
    )
    return {'enabled': bool(body.enabled)}


# --- Support / assistenza -----------------------------------------------------

RESEND_API_KEY = os.environ.get('RESEND_API_KEY', '')
SUPPORT_EMAIL = os.environ.get('SUPPORT_EMAIL', '')


class SupportBody(BaseModel):
    category: str = Field(min_length=1, max_length=40)
    description: str = Field(min_length=10, max_length=2000)
    frequency: str = Field(min_length=1, max_length=30)
    section: str = Field(min_length=1, max_length=30)
    contact_email: Optional[str] = None


@api_router.post('/support/submit')
async def support_submit(body: SupportBody, user: dict = Depends(get_current_user)):
    """Multi-field support form. Fires an email to the developer via Resend.
    Reply-To is set to the user's registered email (or their optional contact
    field) so the developer can reply directly from their inbox.

    Anonymous accounts cannot submit tickets: we require a real account so we
    can actually reach the user back and to avoid spam from throwaway sessions.
    """
    is_anon = bool(user.get('is_anonymous')) or (user.get('auth_provider') == 'anonymous')
    if is_anon:
        raise HTTPException(
            status_code=403,
            detail='Devi registrarti con un account per inviare una richiesta di assistenza.',
        )

    if not RESEND_API_KEY or not SUPPORT_EMAIL:
        raise HTTPException(status_code=500, detail='Servizio email non configurato. Riprova più tardi.')

    reply_to = (user.get('email') or (body.contact_email or '').strip()) or None
    provider = user.get('auth_provider') or ('anonymous' if user.get('is_anonymous') else 'unknown')

    def esc(v: str) -> str:
        return html_lib.escape(str(v or ''))

    reply_note = ("(Reply-To impostato su " + reply_to + ")") if reply_to else (
        "— nessun contatto disponibile, l" + chr(0x2019) + " utente è anonimo senza email opzionale"
    )
    html_body = f"""
    <div style="font-family:-apple-system,sans-serif;max-width:640px;line-height:1.5">
      <h2 style="color:#F01A1A;border-bottom:2px solid #F01A1A;padding-bottom:6px">
        Populus — Nuova richiesta di assistenza
      </h2>
      <p><b>Categoria:</b> {esc(body.category)}<br>
         <b>Frequenza:</b> {esc(body.frequency)}<br>
         <b>Sezione app:</b> {esc(body.section)}</p>
      <h3>Descrizione</h3>
      <blockquote style="border-left:3px solid #ccc;padding-left:12px;color:#333;white-space:pre-wrap">{esc(body.description)}</blockquote>
      <hr>
      <h3>Identificativo utente</h3>
      <p><b>Nickname:</b> {esc(user.get('nickname', '-'))}<br>
         <b>User ID:</b> <code>{esc(user.get('user_id', '-'))}</code><br>
         <b>Auth provider:</b> {esc(provider)}<br>
         <b>Email registrata:</b> {esc(user.get('email') or '(nessuna)')}<br>
         <b>Email contatto (opzionale):</b> {esc(body.contact_email or '(non fornita)')}</p>
      <p style="font-size:12px;color:#888">
        Rispondi direttamente a questa email per contattare l&rsquo;utente
        {esc(reply_note)}.
      </p>
    </div>
    """.strip()

    payload: dict = {
        'from': 'Populus Support <onboarding@resend.dev>',
        'to': [SUPPORT_EMAIL],
        'subject': f"[Populus] {body.category} — {user.get('nickname', 'utente')}",
        'html': html_body,
    }
    if reply_to:
        payload['reply_to'] = reply_to

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.post(
                'https://api.resend.com/emails',
                headers={
                    'Authorization': f'Bearer {RESEND_API_KEY}',
                    'Content-Type': 'application/json',
                },
                json=payload,
            )
            if r.status_code >= 400:
                logger.warning(f"Resend error {r.status_code}: {r.text[:200]}")
                raise HTTPException(
                    status_code=502,
                    detail="Impossibile inviare la richiesta ora. Riprova tra qualche minuto.",
                )
    except HTTPException:
        raise
    except Exception as e:
        logger.warning(f"support email error: {e}")
        raise HTTPException(status_code=502, detail='Servizio email non raggiungibile.')

    # Also archive the ticket in Mongo for the admin to browse.
    await db.support_tickets.insert_one({
        'ticket_id': new_id('tkt'),
        'user_id': user.get('user_id'),
        'nickname': user.get('nickname'),
        'category': body.category, 'frequency': body.frequency,
        'section': body.section, 'description': body.description,
        'contact_email': body.contact_email,
        'created_at': now_utc(),
    })
    return {'sent': True}


async def send_push(recipients: List[str], data: dict, idempotency_key: Optional[str] = None) -> None:
    """Trigger a mobile push via Emergent. Caller must handle exceptions —
    push failures should NEVER block the primary operation."""
    if not recipients:
        return
    if len(recipients) > 100:
        raise ValueError('max 100 recipients per /trigger call')
    if 'title' not in data or 'message' not in data:
        raise ValueError('data must include title and message')
    payload: dict = {'recipients': recipients, 'data': data}
    if idempotency_key:
        payload['$idempotency_key'] = idempotency_key
    resp = await _push_client.post('/api/v1/push/trigger', json=payload)
    if resp.status_code == 401:
        raise HTTPException(status_code=500, detail='EMERGENT_PUSH_KEY missing or invalid')
    if resp.status_code >= 500:
        raise HTTPException(status_code=502, detail='Push provider unavailable')
    resp.raise_for_status()


async def _daily_lock(user_id: str, kind: str) -> bool:
    """Returns True if the user has NOT yet received a `kind` push today (UTC).
    Also atomically marks it as sent. Ensures max-1-per-day semantics for the
    hot-news and vote-flip triggers."""
    today = now_utc().date().isoformat()
    key = f"{user_id}:{kind}:{today}"
    res = await db.notification_locks.update_one(
        {'key': key},
        {'$setOnInsert': {'key': key, 'created_at': now_utc()}},
        upsert=True,
    )
    return res.upserted_id is not None


async def _fanout_hot_news(feud: dict) -> None:
    """When a fresh, high-engagement faida lands in an interesting category,
    push a mobile notification to users who have that category among their
    onboarding favorites. Guardrails:
      - Requires engagement_score >= 7 (Claude's own self-rating).
      - Requires push_notifications setting to be enabled for the user (default on).
      - Rate-limited to at most 1 hot-news push per user per day.
    """
    score = int(feud.get('engagement_score') or 0)
    if score < 7:
        return
    cat = feud.get('category')
    if not cat:
        return
    users = await db.users.find(
        {
            'favorite_categories': cat,
            'is_anonymous': {'$ne': True},
            '$or': [{'push_notifications': True}, {'push_notifications': {'$exists': False}}],
        },
        {'_id': 0, 'user_id': 1},
    ).to_list(500)
    if not users:
        return
    title = "Nuova faida calda per te"
    body = (feud.get('title') or '')[:120]
    fid = feud.get('feud_id')
    for u in users:
        uid = u['user_id']
        try:
            if not await _daily_lock(uid, 'hot_news'):
                continue
            await _emit_notification(
                uid, 'hot_news',
                title=title,
                body=body,
                feud_id=fid,
                send_push_too=True,
            )
        except Exception as e:
            logger.warning(f"hot-news notify failed for {uid}: {e}")


@api_router.get('/notifications')
async def list_notifications(user: dict = Depends(get_current_user)):
    """Latest 50 notifications for the current user, newest first."""
    docs = await db.notifications.find(
        {'user_id': user['user_id']}, {'_id': 0}
    ).sort('created_at', -1).to_list(50)
    for d in docs:
        if isinstance(d.get('created_at'), datetime):
            d['created_at'] = _iso_utc(d['created_at'])
    return {'notifications': docs}


@api_router.get('/notifications/unread-count')
async def unread_count(user: dict = Depends(get_current_user)):
    n = await db.notifications.count_documents(
        {'user_id': user['user_id'], 'read': False}
    )
    return {'count': n}


@api_router.post('/notifications/mark-read')
async def mark_read(user: dict = Depends(get_current_user)):
    """Mark ALL notifications for the current user as read."""
    r = await db.notifications.update_many(
        {'user_id': user['user_id'], 'read': False},
        {'$set': {'read': True, 'read_at': now_utc()}},
    )
    return {'updated': r.modified_count}


def _image_for_category(cat_id: str, seed: Optional[str] = None) -> str:
    # Verified working Unsplash IDs (Feb 2026). Two options per category, chosen by seed hash for variety.
    mapping = {
        'politica': [
            'https://images.unsplash.com/photo-1529107386315-e1a2ed48a620?w=1200',
            'https://images.unsplash.com/photo-1585155770447-2f66e2a397b5?w=1200',
        ],
        'tv': [
            'https://images.unsplash.com/photo-1522869635100-9f4c5e86aa37?w=1200',
            'https://images.unsplash.com/photo-1593359677879-a4bb92f829d1?w=1200',
        ],
        'musica': [
            'https://images.unsplash.com/photo-1470225620780-dba8ba36b745?w=1200',
            'https://images.unsplash.com/photo-1511671782779-c97d3d27a1d4?w=1200',
        ],
        'sport': [
            'https://images.unsplash.com/photo-1517649763962-0c623066013b?w=1200',
            'https://images.unsplash.com/photo-1521412644187-c49fa049e84d?w=1200',
        ],
        'cinema': [
            'https://images.unsplash.com/photo-1440404653325-ab127d49abc1?w=1200',
            'https://images.unsplash.com/photo-1478720568477-152d9b164e26?w=1200',
        ],
        'social': [
            'https://images.unsplash.com/photo-1611605698335-8b1569810432?w=1200',
            'https://images.unsplash.com/photo-1611746872915-64382b5c76da?w=1200',
        ],
        'gossip': [
            'https://images.unsplash.com/photo-1523419409543-a5e549c1faa8?w=1200',
            'https://images.unsplash.com/photo-1516307365426-bea591f05011?w=1200',
        ],
        'tech': [
            'https://images.unsplash.com/photo-1518770660439-4636190af475?w=1200',
            'https://images.unsplash.com/photo-1550751827-4bd374c3f58b?w=1200',
            'https://images.unsplash.com/photo-1517694712202-14dd9538aa97?w=1200',
        ],
        'cronaca': [
            'https://images.unsplash.com/photo-1590486803833-1c5dc8ddd4c8?w=1200',
            'https://images.unsplash.com/photo-1587653263995-422546a7a559?w=1200',
            'https://images.unsplash.com/photo-1495556650867-99590cea3657?w=1200',
        ],
    }
    options = mapping.get(cat_id, mapping['gossip'])
    if not seed:
        return options[0]
    idx = sum(ord(c) for c in seed) % len(options)
    return options[idx]


def _image_from_entry(entry) -> Optional[str]:
    """Try to extract a real image from an RSS entry (media:content, enclosures, media:thumbnail)."""
    try:
        for m in getattr(entry, 'media_content', []) or []:
            url = m.get('url')
            if url and url.startswith('http'):
                return url
    except Exception:
        pass
    try:
        for m in getattr(entry, 'media_thumbnail', []) or []:
            url = m.get('url')
            if url and url.startswith('http'):
                return url
    except Exception:
        pass
    try:
        for e in getattr(entry, 'enclosures', []) or []:
            url = e.get('href') or e.get('url')
            typ = (e.get('type') or '').lower()
            if url and ('image' in typ or url.lower().endswith(('.jpg', '.jpeg', '.png', '.webp'))):
                return url
    except Exception:
        pass
    # Some feeds embed <img> in the summary
    try:
        summary = getattr(entry, 'summary', '') or ''
        m = re.search(r'<img[^>]+src="([^"]+)"', summary)
        if m:
            return m.group(1)
    except Exception:
        pass
    return None


@api_router.post('/admin/generate-daily')
async def generate_daily(count: int = 3, _: bool = Depends(require_admin)):
    try:
        from emergentintegrations.llm.chat import LlmChat, UserMessage
    except Exception as e:
        raise HTTPException(status_code=500, detail=f'emergentintegrations non disponibile: {e}')

    created = []
    picks = CATEGORIES[:count] if count <= len(CATEGORIES) else CATEGORIES
    for cat in picks:
        try:
            feud = await _generate_feud_for_category(cat, LlmChat, UserMessage)
            if feud:
                # Fact-checker gate: strict editorial review before publish.
                chosen_headline = (feud.get('sources') or [{}])[0]
                feud = await _ai_fact_check_feud(feud, chosen_headline, LlmChat, UserMessage)
            if feud:
                await db.feuds.insert_one(feud)
                feud.pop('_id', None)
                _attach_percentages(feud, revealed=True)
                feud['created_at'] = _iso_utc(feud['created_at'])
                created.append(feud)
        except Exception as e:
            logger.warning(f"AI generation failed for {cat['id']}: {e}")
    return {'created': created}


AGE_BUCKETS = [
    ('13-17', 13, 18),
    ('18-24', 18, 25),
    ('25-34', 25, 35),
    ('35-44', 35, 45),
    ('45-54', 45, 55),
    ('55-64', 55, 65),
    ('65+', 65, 121),
]


@api_router.get('/feuds/{feud_id}/stats')
async def feud_stats(feud_id: str, user: dict = Depends(get_current_user)):
    """Aggregated real-time stats for a single feud. Requires the user to have
    already voted (percentages contract is `vote-to-reveal`, and stats are a
    stronger reveal). Anonymous users can call it too as long as they've cast
    a vote on this feud.

    Returns per-side breakdowns for age buckets, macro-region (Nord/Centro/Sud)
    and gender. Computed live from the votes+users collections — no caching.
    """
    feud = await db.feuds.find_one({'feud_id': feud_id}, {'_id': 0, 'feud_id': 1})
    if not feud:
        raise HTTPException(status_code=404, detail='Faida non trovata')
    # Gate: user must have voted (contract with the frontend UI).
    my_vote = await db.votes.find_one(
        {'feud_id': feud_id, 'user_id': user['user_id']}, {'_id': 0, 'side': 1}
    )
    if not my_vote:
        raise HTTPException(
            status_code=403,
            detail='Devi votare prima di consultare le statistiche.',
        )

    pipeline = [
        {'$match': {'feud_id': feud_id}},
        {'$lookup': {
            'from': 'users', 'localField': 'user_id', 'foreignField': 'user_id', 'as': 'u',
        }},
        {'$unwind': {'path': '$u', 'preserveNullAndEmptyArrays': True}},
        {'$project': {
            '_id': 0,
            'side': 1,
            'age': '$u.age',
            'sex': '$u.sex',
            'region': '$u.region',
        }},
    ]
    rows = await db.votes.aggregate(pipeline).to_list(50000)

    def _empty_side() -> dict:
        return {
            'total': 0,
            'age': {name: 0 for (name, _lo, _hi) in AGE_BUCKETS} | {'unknown': 0},
            'region': {'Nord': 0, 'Centro': 0, 'Sud': 0, 'unknown': 0},
            'sex': {'F': 0, 'M': 0, 'other': 0, 'unknown': 0},
        }
    sides: dict = {'A': _empty_side(), 'B': _empty_side()}
    for r in rows:
        s = r.get('side')
        if s not in sides:
            continue
        sides[s]['total'] += 1
        sides[s]['age'][_bucket_age(r.get('age'))] += 1
        sides[s]['region'][_macro_region(r.get('region'))] += 1
        sides[s]['sex'][_norm_sex(r.get('sex'))] += 1

    return {
        'feud_id': feud_id,
        'total_votes': sides['A']['total'] + sides['B']['total'],
        'sides': sides,
    }



# Italian macro-regions used for per-feud vote breakdowns. Values must match
# the region names stored on the user document (onboarding form).
REGION_MACRO = {
    # Nord
    'Piemonte': 'Nord', 'Valle d\'Aosta': 'Nord', 'Lombardia': 'Nord',
    'Trentino-Alto Adige': 'Nord', 'Veneto': 'Nord', 'Friuli-Venezia Giulia': 'Nord',
    'Liguria': 'Nord', 'Emilia-Romagna': 'Nord',
    # Centro
    'Toscana': 'Centro', 'Umbria': 'Centro', 'Marche': 'Centro', 'Lazio': 'Centro',
    'Abruzzo': 'Centro',
    # Sud e Isole
    'Molise': 'Sud', 'Campania': 'Sud', 'Puglia': 'Sud', 'Basilicata': 'Sud',
    'Calabria': 'Sud', 'Sicilia': 'Sud', 'Sardegna': 'Sud',
}


def _bucket_age(age) -> str:
    if not isinstance(age, int):
        return 'unknown'
    for name, lo, hi in AGE_BUCKETS:
        if lo <= age < hi:
            return name
    return 'unknown'


def _macro_region(region) -> str:
    if not region or not isinstance(region, str):
        return 'unknown'
    return REGION_MACRO.get(region.strip(), 'unknown')


def _norm_sex(sex) -> str:
    if not sex or not isinstance(sex, str):
        return 'unknown'
    s = sex.strip().lower()
    if s in ('f', 'm'):
        return s.upper()
    if s == 'other':
        return 'other'
    return 'unknown'


@api_router.post('/admin/backfill_media')
async def admin_backfill_media(limit: int = 200, force: bool = False, _: bool = Depends(require_admin)):
    """Re-run OG/YouTube extraction on existing feuds. By default only enriches
    feuds that don't already have a media object; pass `force=true` to redo all."""
    yt_key = os.environ.get('YOUTUBE_API_KEY')
    q: dict = {}
    if not force:
        q = {'$or': [{'media': {'$exists': False}}, {'media': None}]}
    feuds = await db.feuds.find(q, {'_id': 0}).to_list(limit)
    updated = 0
    for f in feuds:
        src = None
        srcs = f.get('sources') or []
        for s in srcs:
            if s.get('link'):
                src = s['link']
                break
        if not src:
            continue
        party_a = (f.get('party_a') or '').strip()
        party_b = (f.get('party_b') or '').strip()
        search_hint = f"{party_a} {party_b}".strip() if (party_a or party_b) else None
        try:
            img, media = await _resolve_media(
                title=f.get('title') or '',
                source_url=src,
                fallback_image=f.get('image_url'),
                youtube_api_key=yt_key,
                search_query=search_hint,
            )
        except Exception as e:
            logger.warning(f"backfill media failed for {f.get('feud_id')}: {e}")
            continue
        update: dict = {}
        if img and img != f.get('image_url'):
            update['image_url'] = img
        # When forcing, overwrite media (even with None) so obsolete entries
        # created before the quality filter get cleared.
        if force or media is not None:
            update['media'] = media
        if update:
            await db.feuds.update_one({'feud_id': f['feud_id']}, {'$set': update})
            updated += 1
    return {'scanned': len(feuds), 'updated': updated}


@api_router.post('/admin/cleanup_expired')
async def admin_cleanup(_: bool = Depends(require_admin)):
    """Manually trigger expired-feud cleanup (also runs hourly via scheduler)."""
    before = await db.feuds.count_documents({})
    await _cleanup_expired_feuds()
    after = await db.feuds.count_documents({})
    return {'purged': before - after, 'remaining': after}


@api_router.get('/admin/stats')
async def admin_stats(_: bool = Depends(require_admin)):
    total_users = await db.users.count_documents({})
    onboarded_users = await db.users.count_documents({'onboarding_completed': True})

    # Join votes with users
    pipeline = [
        {'$lookup': {'from': 'users', 'localField': 'user_id', 'foreignField': 'user_id', 'as': 'u'}},
        {'$unwind': {'path': '$u', 'preserveNullAndEmptyArrays': True}},
        {'$project': {
            '_id': 0,
            'side': 1, 'feud_id': 1,
            'region': '$u.region',
            'sex': '$u.sex',
            'age': '$u.age',
        }},
    ]
    joined = await db.votes.aggregate(pipeline).to_list(100000)
    total_votes = len(joined)

    by_region: dict = {}
    by_sex: dict = {'F': 0, 'M': 0, 'other': 0, 'na': 0, 'unknown': 0}
    by_age: dict = {name: 0 for (name, _lo, _hi) in AGE_BUCKETS}
    by_age['unknown'] = 0

    for v in joined:
        r = v.get('region') or 'unknown'
        by_region[r] = by_region.get(r, 0) + 1
        s = v.get('sex') or 'unknown'
        if s not in by_sex:
            s = 'unknown'
        by_sex[s] = by_sex.get(s, 0) + 1
        age = v.get('age')
        if not isinstance(age, int):
            by_age['unknown'] += 1
        else:
            placed = False
            for name, lo, hi in AGE_BUCKETS:
                if lo <= age < hi:
                    by_age[name] += 1
                    placed = True
                    break
            if not placed:
                by_age['unknown'] += 1

    region_list = sorted(
        [{'region': k, 'count': v} for k, v in by_region.items()],
        key=lambda x: x['count'],
        reverse=True,
    )

    # Top feuds by total votes
    top_pipe = [
        {'$group': {
            '_id': '$feud_id',
            'total': {'$sum': 1},
            'a': {'$sum': {'$cond': [{'$eq': ['$side', 'A']}, 1, 0]}},
            'b': {'$sum': {'$cond': [{'$eq': ['$side', 'B']}, 1, 0]}},
        }},
        {'$sort': {'total': -1}},
        {'$limit': 5},
        {'$lookup': {'from': 'feuds', 'localField': '_id', 'foreignField': 'feud_id', 'as': 'f'}},
        {'$unwind': {'path': '$f', 'preserveNullAndEmptyArrays': True}},
        {'$project': {
            '_id': 0,
            'feud_id': '$_id',
            'total': 1, 'a': 1, 'b': 1,
            'title': '$f.title',
            'category_label': '$f.category_label',
            'party_a': '$f.party_a',
            'party_b': '$f.party_b',
        }},
    ]
    top_feuds_raw = await db.votes.aggregate(top_pipe).to_list(5)
    top_feuds = []
    for tf in top_feuds_raw:
        total = tf.get('total', 0)
        top_feuds.append({
            'feud_id': tf.get('feud_id'),
            'title': tf.get('title') or '(cancellata)',
            'category_label': tf.get('category_label') or '',
            'party_a': tf.get('party_a') or 'A',
            'party_b': tf.get('party_b') or 'B',
            'total': total,
            'pct_a': round(100 * tf.get('a', 0) / total) if total else 50,
            'pct_b': round(100 * tf.get('b', 0) / total) if total else 50,
        })

    return {
        'total_users': total_users,
        'onboarded_users': onboarded_users,
        'total_votes': total_votes,
        'by_region': region_list,
        'by_sex': by_sex,
        'by_age': by_age,
        'top_feuds': top_feuds,
    }


async def _generate_feud_for_category(cat: dict, LlmChat, UserMessage) -> Optional[dict]:
    # Fetch a wide pool of real news headlines so the AI has room to pick the juiciest
    headlines = await _fetch_headlines_for_category(cat['id'], max_items=18)
    hot_topics = _load_hot_topics()

    # --- STEP 1: filter out headlines whose source link we already turned into a
    # feud in the last 3 days. Prevents the AI from repeatedly picking the same
    # top-of-feed story only to have it rejected as a duplicate later, which
    # would silently waste the whole 30-min tick.
    if headlines:
        three_days_ago = now_utc() - timedelta(days=3)
        used_links_docs = await db.feuds.find(
            {'created_at': {'$gte': three_days_ago}},
            {'_id': 0, 'sources.link': 1, 'source_url': 1},
        ).to_list(2000)
        used_links: set = set()
        for d in used_links_docs:
            for s in (d.get('sources') or []):
                lk = s.get('link') if isinstance(s, dict) else None
                if lk:
                    used_links.add(lk)
            if d.get('source_url'):
                used_links.add(d['source_url'])
        headlines = [h for h in headlines if h.get('link') not in used_links]

    # --- STEP 2: hot-topic boost — reorder the pool so headlines mentioning a
    # trending topic from hot_topics.md appear FIRST. The LLM tends to weigh
    # earlier items more heavily, so this dramatically improves the chance
    # that hot topics (e.g. "Temptation Island", "Sanremo") become feuds when
    # they surface in any category feed.
    hot_indices: set = set()
    if headlines and hot_topics:
        def _norm(s: str) -> str:
            return re.sub(r'\s+', ' ', (s or '').lower()).strip()
        topic_terms: List[str] = []
        for t in hot_topics:
            # Drop parenthetical notes and split "A / B" into separate matchers.
            cleaned = re.sub(r'\([^)]*\)', '', t).strip()
            for part in re.split(r'[/,]', cleaned):
                p = _norm(part)
                if len(p) >= 4:  # avoid noise like "TV", "AI"
                    topic_terms.append(p)
        def _score(h: dict) -> int:
            t = _norm(h.get('title', ''))
            return sum(1 for term in topic_terms if term in t)
        # Stable sort: hot-topic hits first, ties keep original RSS order.
        headlines = sorted(headlines, key=lambda h: -_score(h))
        # After sorting, hot headlines occupy the leading indices — record them
        # so the prompt can mark and REQUIRE the AI to pick from them.
        hot_indices = {i for i, h in enumerate(headlines) if _score(h) > 0}
        if hot_indices:
            top_hot = headlines[0]['title'][:80]
            logger.info(
                f"hot-topic boost for {cat['id']}: {len(hot_indices)} headline(s) match "
                f"→ top: '{top_hot}'"
            )

    chat = LlmChat(
        api_key=EMERGENT_LLM_KEY,
        session_id=f"gen-{cat['id']}-{uuid.uuid4().hex[:6]}",
        system_message=(
            "Sei un editor italiano cinico e affilato, tipo tabloid, che trasforma notizie reali "
            "in FAIDE — controversie a due parti su cui la gente si accalora. "
            "Il tuo unico criterio è l'engagement: la notizia scelta deve provocare reazioni "
            "emotive forti (rabbia, indignazione, ironia, gossip, tifo), dividere il pubblico in due, "
            "e far venir voglia di commentare. Le DUE PARTI possono essere: "
            "(A) due contendenti diversi (persone/gruppi/istituzioni) citati nella notizia, "
            "OPPURE (B) due POSIZIONI ANTITETICHE su un singolo soggetto (chi lo difende vs "
            "chi lo condanna, chi lo sostiene vs chi lo critica). Scegli la modalità che rende "
            "la faida più naturale. Evita come la peste notizie tecniche, burocratiche, "
            "adempimenti, dati economici astratti, dichiarazioni istituzionali generiche, "
            "necrologi, cronaca meteo, o eventi senza reali linee di frattura pubblica.\n\n"
            "REGOLE DI STILE PER IL SUMMARY (non negoziabili):\n"
            "1. Ogni persona citata deve avere NOME E COGNOMI completi (non solo il nome, non "
            "il soprannome soltanto). Es: 'Fabrizio Corona', non 'Corona' o 'Fabri'.\n"
            "2. Inserisci sempre i dettagli specifici della notizia: dove, quando, cifre, ruoli, "
            "titoli professionali, contesto (es. 'in diretta a La Vita in Diretta', 'durante il GF 18').\n"
            "3. Includi il dettaglio più succoso, l'aneddoto specifico, la frase incriminata o "
            "il numero-shock che rende la storia degna di essere raccontata.\n"
            "4. Vietati riassunti vaghi tipo 'polemica sui social', 'litigio pubblico', 'scoppia il caso': "
            "sostituiscili con la scena concreta ('Selvaggia Lucarelli ha pubblicato uno screenshot dove...').\n"
            "5. Se la notizia contiene una citazione forte, RIPORTALA breve tra virgolette.\n\n"
            "Restituisci SOLO JSON valido, in italiano, senza commenti e senza testo extra."
        ),
    ).with_model('anthropic', 'claude-sonnet-4-6')

    if headlines:
        def _fmt_headline(i: int, h: dict) -> str:
            tag = "[HOT] " if i in hot_indices else ""
            head = f"[{i}] {tag}TITOLO: {h['title']}\n     FONTE: {h['source']}"
            excerpt = (h.get('excerpt') or '').strip()
            if excerpt:
                head += f"\n     ESTRATTO: {excerpt}"
            return head
        sources_block = "\n\n".join([_fmt_headline(i, h) for i, h in enumerate(headlines)])
        hot_topics_block = ""
        if hot_topics:
            bullets = "\n".join(f"  • {t}" for t in hot_topics)
            hot_rule = ""
            if hot_indices:
                hot_ids = ", ".join(str(i) for i in sorted(hot_indices))
                hot_rule = (
                    f"\n\n### VINCOLO CRITICO — NON NEGOZIABILE ###\n"
                    f"Nel pool ci sono notizie marcate [HOT] agli indici: {hot_ids}. "
                    f"DEVI OBBLIGATORIAMENTE scegliere una di queste. "
                    f"NON puoi selezionare una notizia non-[HOT] finché esiste almeno "
                    f"una [HOT] nel pool. Anche se ti sembra 'meno succosa', è priorità "
                    f"assoluta perché è l'argomento del momento su cui il pubblico si "
                    f"divide di più. UNICA eccezione ammessa: se TUTTE le notizie [HOT] "
                    f"sono palesemente inadatte (comunicati istituzionali, meteo, "
                    f"necrologi, elenco cast senza conflitto), allora restituisci "
                    f'esattamente {{"skip": true, "reason": "hot topics not feud-worthy"}} '
                    f"— NON ripiegare su una notizia non-[HOT].\n"
                )
            hot_topics_block = (
                "\n\nARGOMENTI PRIORITARI DA MONITORARE (aggiornati dinamicamente):\n"
                f"{bullets}\n"
                "Le notizie del pool che toccano questi argomenti sono marcate con [HOT]."
                f"{hot_rule}"
            )
        prompt = (
            f"Categoria: {cat['label']}.\n\n"
            f"POOL DI NOTIZIE REALI DI OGGI:\n{sources_block}\n"
            f"{hot_topics_block}\n"
            "COMPITO: scegli LA notizia con il coefficiente di engagement più alto. "
            "Criteri, in ordine di importanza:\n"
            "1. Deve avere DUE parti chiaramente contrapposte OPPURE due posizioni opposte "
            "su un soggetto (es. condanna vs assoluzione, sostegno vs critica).\n"
            "2. Deve scatenare reazioni emotive forti: rabbia, indignazione, tifo, gossip, ironia.\n"
            "3. Deve toccare l'opinione popolare o essere già virale sui social.\n"
            "4. Preferisci scandali, litigi pubblici, gaffe, dichiarazioni divisive, "
            "risultati contestati, presunti tradimenti, cause legali, retroscena piccanti.\n"
            "5. Scarta senza pietà: comunicati istituzionali generici, dati statistici noiosi, "
            "adempimenti amministrativi, notizie tecniche di nicchia, retorica ovvia.\n\n"
            "REGOLA FERREA: NON INVENTARE nulla. Devi obbligatoriamente scegliere una notizia REALE "
            "dal pool fornito. Se nessuna notizia del pool è abbastanza succosa, restituisci "
            'esattamente {"skip": true, "reason": "motivo"} e nient\'altro.\n\n'
            "Il titolo deve essere DA TABLOID: incisivo, esplicito nel conflitto (usa 'contro', 'vs', "
            "'attacca', 'smaschera', 'accusa', 'insulta', 'gela', 'demolisce', 'inguaia', 'divide'), "
            "max 90 caratteri. Ma tutti i fatti, i nomi e i dettagli DEVONO derivare dalla notizia "
            "scelta (titolo + ESTRATTO fornito), non dalla tua fantasia.\n"
            "MODALITÀ PARTI ammesse:\n"
            "  A) Due contendenti reali: nomi propri/gruppi citati nella notizia (es. Milan vs Inter).\n"
            "  B) Due POSIZIONI ANTITETICHE su UN singolo soggetto quando non esiste una vera "
            "controparte (es. su Fabrizio Corona: 'Merita la condanna' vs 'La magistratura esagera'; "
            "su una decisione politica: 'Giusta' vs 'Sbagliata'; su un fenomeno: 'Utile' vs 'Dannoso'). "
            "In modalità B i nomi devono essere brevi (max 40 caratteri) e schierarsi in modo netto.\n"
            "La domanda finale deve essere provocatoria e schierante.\n\n"
            "REGOLA PER `summary` (obbligatoria): il summary NON è uno slogan, è un mini-articolo "
            "informativo che permette al lettore di capire la notizia SENZA cliccare sulla fonte. "
            "Deve avere ESATTAMENTE 3 blocchi separati da doppia interlinea (\\n\\n):\n"
            "  1) COSA È SUCCESSO — 2-3 frasi che raccontano il fatto: chi (nomi e cognomi completi), "
            "quando, dove, cosa è accaduto esattamente, ruoli/qualifiche/programma/contesto. Include "
            "la citazione più forte tra virgolette se presente nell'estratto. Se non hai il dato, "
            "scrivi 'non specificato' invece di inventarlo.\n"
            "  2) IL DETTAGLIO CHIAVE — 1-2 frasi con il retroscena/aneddoto/numero-shock/frase "
            "incriminata che rende la storia degna di essere raccontata. Deve derivare dall'estratto.\n"
            "  3) PERCHÉ LA GENTE SI DIVIDE — 1-2 frasi che chiariscono le DUE posizioni contrapposte "
            "e cosa esattamente le divide (chi la pensa come `party_a` argomenta X, chi sta con "
            "`party_b` risponde Y). Zero neutralità, ma nessuna delle due parti va delegittimata.\n"
            "In totale il summary deve essere 90-150 parole. Vietato aprire con 'polemica', 'scoppia "
            "il caso', 'si litiga': entra subito nel merito dei fatti.\n\n"
            "Rispondi SOLO con questo JSON:\n"
            '{"title": "titolo tabloid max 90 caratteri", '
            '"subject": "SOLO in modalità B (singolo soggetto con posizioni opposte): il NOME del soggetto della faida — persona, gruppo, cosa (es. \"Fabrizio Corona\", \"Samsung\", \"il nuovo film Marvel\"). In modalità A (due contendenti) lascia stringa vuota.", '
            '"party_a": "prima parte (contendente OPPURE posizione)", '
            '"party_b": "seconda parte antitetica alla prima", '
            '"hashtag_subjects": "Array di 1 o 2 NOMI PROPRI PULITI per l\'hashtag di raggruppamento. In modalità A metti [\\"NomeA\\", \\"NomeB\\"] (es. [\\"Milan\\", \\"Inter\\"] oppure [\\"Fabrizio Corona\\", \\"Selvaggia Lucarelli\\"]). In modalità B metti UN SOLO nome [\\"NomeSoggetto\\"] (es. [\\"Fabrizio Corona\\"], [\\"Sanremo 2026\\"], [\\"Temptation Island\\"]). REGOLE FERREE: SOLO nomi propri di persona/brand/prodotto/evento; MAI articoli/preposizioni (\\"il\\", \\"la\\", \\"di\\", \\"del\\"); MAI descrizioni tra parentesi; MAI frasi retoriche (\\"Chi difende…\\", \\"contrari\\"); MAI emoji; MAI titoli lunghi (\\"Il resort di Bill Gates in Puglia\\" → [\\"Bill Gates\\"] o [\\"Bill Gates\\", \\"Puglia\\"]); max 3 parole per nome; cognome incluso quando esiste (\\"Fabrizio Corona\\" non solo \\"Corona\\"). Questo hashtag deve permettere di raggruppare tutte le faide future sugli stessi protagonisti.", '
            '"summary": "mini-articolo di 90-150 parole strutturato nei 3 blocchi (COSA È SUCCESSO / IL DETTAGLIO CHIAVE / PERCHÉ LA GENTE SI DIVIDE) separati da \\n\\n. Basato ESCLUSIVAMENTE su titolo + estratto della notizia scelta.", '
            '"question": "domanda schierante e provocatoria, non neutra", '
            '"source_index": indice (0-based) della notizia scelta nel pool (obbligatorio), '
            '"engagement_score": numero da 1 a 10 che stimi per la faida che hai creato, '
            '"engagement_reason": "una frase che spiega perché scatenerà reazioni"}'
        )
    else:
        # No RSS available today: skip generation entirely rather than invent news
        logger.info(f"No RSS headlines for {cat['id']}, skipping generation")
        return None

    text = await chat.send_message(UserMessage(text=prompt))
    match = re.search(r'\{[\s\S]*\}', text)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except Exception:
        return None

    # AI chose to skip
    if data.get('skip') is True:
        logger.info(f"AI skipped {cat['id']}: {data.get('reason', 'no juicy news')}")
        return None

    # Validate that a real source was chosen
    idx = data.get('source_index')
    if not isinstance(idx, int) or idx < 0 or idx >= len(headlines):
        logger.info(f"AI returned invalid source_index for {cat['id']}: {idx}, discarding")
        return None

    # HOT-TOPIC ENFORCEMENT: if the AI ignored the [HOT] constraint, retry
    # once with a pool restricted to the [HOT] items only. This guarantees
    # trending topics get promoted to feuds when they surface in the feeds.
    if hot_indices and idx not in hot_indices:
        logger.info(
            f"AI picked non-HOT idx {idx} for {cat['id']} despite {len(hot_indices)} "
            f"HOT items available — forcing retry with HOT-only pool"
        )
        hot_headlines = [headlines[i] for i in sorted(hot_indices)]
        hot_sources = "\n".join(
            [f"[{i}] {h['title']} — fonte: {h['source']}" for i, h in enumerate(hot_headlines)]
        )
        retry_prompt = (
            f"Categoria: {cat['label']}.\n\n"
            f"POOL RISTRETTO — SOLO ARGOMENTI CALDI DI OGGI:\n{hot_sources}\n\n"
            f"Queste sono le notizie di tendenza del momento. Scegli LA più adatta "
            f"a diventare una faida a due parti (o due posizioni opposte su un "
            f"singolo soggetto). Se davvero nessuna funziona, restituisci "
            f'{{"skip": true, "reason": "..."}}.\n\n'
            f"Regole di stile e schema JSON identici a prima. Ricorda: nomi e cognomi "
            f"completi, dettagli concreti dalla notizia, niente riassunti vaghi.\n\n"
            f"Rispondi SOLO con lo stesso JSON di prima, con `source_index` in "
            f"[0..{len(hot_headlines)-1}] riferito a QUESTO pool ristretto."
        )
        try:
            retry_text = await chat.send_message(UserMessage(text=retry_prompt))
            retry_match = re.search(r'\{[\s\S]*\}', retry_text)
            if retry_match:
                retry_data = json.loads(retry_match.group(0))
                if retry_data.get('skip') is True:
                    logger.info(f"AI skipped {cat['id']} on HOT-retry: {retry_data.get('reason','')}")
                    return None
                ridx = retry_data.get('source_index')
                if isinstance(ridx, int) and 0 <= ridx < len(hot_headlines):
                    # Remap to the original headlines list index for downstream code
                    remapped = sorted(hot_indices)[ridx]
                    idx = remapped
                    data = retry_data
                    data['source_index'] = idx
                    logger.info(f"HOT-retry succeeded for {cat['id']}: idx={idx}")
        except Exception as e:
            logger.warning(f"HOT-retry failed for {cat['id']}: {e}")

    chosen = headlines[idx]

    # Deduplication: skip if we already have a feud with this source link from the last 3 days
    three_days_ago = now_utc() - timedelta(days=3)
    dup = await db.feuds.find_one({
        'sources.link': chosen['link'],
        'created_at': {'$gte': three_days_ago},
    }, {'_id': 0, 'feud_id': 1})
    if dup:
        logger.info(f"Duplicate source for {cat['id']}: {chosen['link']}, skipping generation")
        return None

    sources: List[dict] = [chosen]
    # Only include additional sources that are actually about the same story.
    # Filter by shared long words between the extra headline and the chosen
    # title / party names — avoids sprinkling unrelated news items from the
    # same category feed.
    def _tokens(s: str) -> set:
        return set(t for t in re.findall(r"\w{5,}", (s or '').lower()))
    key_terms = _tokens(chosen.get('title') or '')
    for p in (data.get('party_a') or '', data.get('party_b') or ''):
        key_terms |= set(t for t in re.findall(r"\w{4,}", (p or '').lower()))
    parties_lc = [(data.get('party_a') or '').lower(), (data.get('party_b') or '').lower()]
    for i, h in enumerate(headlines[:18]):
        if i == idx or h in sources:
            continue
        ht = (h.get('title') or '').lower()
        # Match if the extra headline shares 2+ significant tokens OR mentions
        # a party by name (min 4 chars).
        overlap = sum(1 for t in key_terms if t and t in ht)
        party_hit = any(p and len(p) >= 4 and p in ht for p in parties_lc)
        if overlap >= 2 or party_hit:
            sources.append(h)
            if len(sources) >= 3:
                break

    # Prefer the real image from the chosen headline; fallback to category image
    chosen_image = chosen.get('image')
    image_url = chosen_image if chosen_image else _image_for_category(cat['id'], seed=chosen['link'])

    # Media enrichment: OG image + YouTube/direct video for the detail page.
    yt_key = os.environ.get('YOUTUBE_API_KEY')
    party_a = (data.get('party_a') or '').strip()
    party_b = (data.get('party_b') or '').strip()
    search_hint = f"{party_a} {party_b}".strip() if (party_a or party_b) else None
    try:
        og_image, media_obj = await _resolve_media(
            title=data.get('title') or '',
            source_url=chosen['link'],
            fallback_image=image_url,
            youtube_api_key=yt_key,
            search_query=search_hint,
        )
        if og_image:
            image_url = og_image
    except Exception as e:
        logger.warning(f"media enrichment failed: {e}")
        media_obj = None

    engagement = data.get('engagement_score')
    try:
        engagement = int(engagement)
    except Exception:
        engagement = None

    subject = (data.get('subject') or '').strip() or None
    raw_subjects = data.get('hashtag_subjects')
    hashtag_subjects: List[str] = []
    if isinstance(raw_subjects, list):
        for x in raw_subjects[:2]:
            if isinstance(x, str) and x.strip():
                hashtag_subjects.append(x.strip()[:60])
    return {
        'feud_id': new_id('feud'),
        'category': cat['id'], 'category_label': cat['label'],
        'title': (data.get('title') or 'Faida senza titolo')[:140],
        'party_a': (data.get('party_a') or 'Team A')[:60],
        'party_b': (data.get('party_b') or 'Team B')[:60],
        'summary': data.get('summary') or '',
        'question': data.get('question') or 'Con chi ti schieri?',
        'image_url': image_url,
        'media': media_obj,
        'sources': sources,
        'engagement_score': engagement,
        'engagement_reason': data.get('engagement_reason') or '',
        'subject': subject,
        'hashtag_subjects': hashtag_subjects or None,
        'hashtag': _hashtag_key(
            data.get('party_a') or '', data.get('party_b') or '',
            subject=subject, hashtag_subjects=hashtag_subjects,
        ),
        'hashtag_display': _hashtag_display(
            data.get('party_a') or '', data.get('party_b') or '',
            subject=subject, hashtag_subjects=hashtag_subjects,
        ),
        'votes_a': 0, 'votes_b': 0, 'created_at': now_utc(), 'source': 'ai',
    }


async def _ai_fact_check_feud(candidate: dict, chosen_headline: dict, LlmChat, UserMessage) -> Optional[dict]:
    """Editorial gatekeeper AI.

    Sits between the "generator" AI (which turns headlines into faide) and
    the database insert. It receives the candidate feud + the source
    headline & excerpt used to generate it, then decides:

      - PUBLISH: emit the feud as-is (payload unchanged).
      - CORRECT: emit the feud with corrected `title` / `summary` /
        `party_a` / `party_b` / `question` — anything the AI deems
        inaccurate, misleading or defamatory is rewritten in place. Only
        the listed fields can be overwritten.
      - REJECT: kill the whole feud (return None). The scheduler falls
        back to the next category, no publication happens.

    The AI is instructed to be strict: unverifiable claims, defamatory
    framing, false attributions, or content unsupported by the source
    trigger REJECT/CORRECT.

    Returns the (possibly patched) feud dict on approval, `None` on
    rejection. On any provider error we fall through to approval so the
    pipeline stays resilient — the fact-checker is a safety net, not a
    hard gate.
    """
    if not EMERGENT_LLM_KEY:
        return candidate
    src_title = (chosen_headline.get('title') or '').strip()
    src_excerpt = (chosen_headline.get('excerpt') or '').strip()
    src_link = chosen_headline.get('link') or ''
    src_source = chosen_headline.get('source') or ''

    # Compact payload — the fact-checker only reasons over the candidate
    # + the actual source material we scraped from RSS. We deliberately
    # do NOT let it browse the web from within the pipeline.
    payload = {
        'candidate': {
            'title': candidate.get('title'),
            'party_a': candidate.get('party_a'),
            'party_b': candidate.get('party_b'),
            'summary': candidate.get('summary'),
            'question': candidate.get('question'),
        },
        'source': {
            'title': src_title,
            'source': src_source,
            'link': src_link,
            'excerpt': src_excerpt,
        },
    }
    prompt = (
        "Analizza il CANDIDATO FAIDA rispetto alla FONTE originale.\n\n"
        f"DATI:\n{json.dumps(payload, ensure_ascii=False, indent=2)}\n\n"
        "REGOLE (rigidissime):\n"
        "1. Ogni affermazione fattuale del CANDIDATO deve essere sostenuta "
        "dalla FONTE (titolo + estratto). Se un dettaglio non è nella "
        "fonte, va rimosso o corretto.\n"
        "2. VIETATA la diffamazione: accuse, insinuazioni, etichette "
        "penali (es. 'ladro', 'criminale', 'stupratore') vanno rimosse "
        "salvo che siano già nel testo della fonte come fatti giudiziari "
        "accertati.\n"
        "3. VIETATO attribuire dichiarazioni non presenti nell'estratto. "
        "Le virgolette devono corrispondere alla fonte.\n"
        "4. Nomi e cognomi devono essere corretti (verifica con la fonte).\n"
        "5. Il gossip pungente è consentito, la disinformazione no.\n"
        "6. Le due parti (party_a / party_b) devono essere davvero "
        "antitetiche e reali. Se sono forzate, correggi o rigetta.\n\n"
        "DECIDI:\n"
        "- Se il candidato rispetta le regole → decisione PUBLISH.\n"
        "- Se ci sono imprecisioni CORREGGIBILI riscrivendo alcuni "
        "  campi (title, party_a, party_b, summary, question) → CORRECT "
        "  e fornisci i campi corretti.\n"
        "- Se il candidato è fondamentalmente non pubblicabile "
        "  (diffamatorio, inventato, disinformazione, dettaglio scandalistico "
        "  privo di riscontro nella fonte) → REJECT con motivazione breve.\n\n"
        "Rispondi SOLO con JSON valido, in italiano:\n"
        '{"decision": "PUBLISH" | "CORRECT" | "REJECT", '
        '"reason": "motivo sintetico", '
        '"corrections": {"title": "...", "party_a": "...", "party_b": "...", '
        '  "summary": "...", "question": "..."}}\n'
        "In PUBLISH e REJECT lascia `corrections` come oggetto vuoto {}."
    )
    try:
        chat = LlmChat(
            api_key=EMERGENT_LLM_KEY,
            session_id=f"fact-{candidate.get('feud_id', '') or new_id('fact')}",
            system_message=(
                "Sei l'editor di verifica di una redazione italiana. Scrupoloso, "
                "prudente, imparziale. Il tuo compito è impedire che finiscano "
                "in pubblicazione notizie diffamatorie, inaccurate, distorte o "
                "non supportate dalla fonte. Non ti fai influenzare dallo stile "
                "tabloid: filtri i FATTI, non il tono."
            ),
        ).with_model('anthropic', 'claude-sonnet-4-6')
        reply = await chat.send_message(UserMessage(text=prompt))
        raw = str(reply) if reply is not None else ''
        m = re.search(r'\{[\s\S]*\}', raw)
        if not m:
            logger.warning(f"fact-check: no JSON in response for {candidate.get('title')!r} — approving by default")
            return candidate
        data = json.loads(m.group(0))
    except Exception as e:
        logger.warning(f"fact-check LLM error for {candidate.get('title')!r}: {e} — approving by default")
        return candidate

    decision = (data.get('decision') or 'PUBLISH').upper()
    reason = (data.get('reason') or '').strip()
    if decision == 'REJECT':
        logger.info(f"fact-check REJECTED feud '{candidate.get('title')}': {reason}")
        return None
    if decision == 'CORRECT':
        corrections = data.get('corrections') or {}
        if isinstance(corrections, dict):
            for k in ('title', 'party_a', 'party_b', 'summary', 'question'):
                v = corrections.get(k)
                if isinstance(v, str) and v.strip():
                    candidate[k] = v.strip()[:1400 if k == 'summary' else 200]
            # Also recompute hashtag fields if party names changed.
            subject_val = candidate.get('subject')
            hs_val = candidate.get('hashtag_subjects')
            candidate['hashtag'] = _hashtag_key(
                candidate.get('party_a') or '', candidate.get('party_b') or '',
                subject=subject_val, hashtag_subjects=hs_val,
            )
            candidate['hashtag_display'] = _hashtag_display(
                candidate.get('party_a') or '', candidate.get('party_b') or '',
                subject=subject_val, hashtag_subjects=hs_val,
            )
        candidate['fact_check'] = {'decision': 'CORRECT', 'reason': reason}
        logger.info(f"fact-check CORRECTED feud '{candidate.get('title')}': {reason}")
        return candidate
    # PUBLISH (default)
    candidate['fact_check'] = {'decision': 'PUBLISH', 'reason': reason}
    return candidate


HOT_TOPICS_PATH = ROOT_DIR / 'hot_topics.md'


def _load_hot_topics() -> List[str]:
    """Read the hot-topics list from `hot_topics.md`. Editable at runtime — the
    file is re-read on every generation cycle so the programmer can push new
    trends without a server restart. Non-list lines and comments are ignored.
    """
    try:
        raw = HOT_TOPICS_PATH.read_text(encoding='utf-8')
    except Exception as e:
        logger.warning(f"hot_topics.md not readable: {e}")
        return []
    topics: List[str] = []
    in_priority_section = False
    for line in raw.splitlines():
        s = line.strip()
        if s.startswith('##'):
            in_priority_section = s.lower().startswith('## argomenti prioritari')
            continue
        if not in_priority_section:
            continue
        # Only pick bullet list items in the priority section.
        m = re.match(r'^[-*]\s+(.+?)$', s)
        if m:
            item = m.group(1).strip()
            if item and not item.startswith('#'):
                topics.append(item)
    return topics


def _extract_subject_from_title(title: str) -> str:
    """Fallback: derive a single-subject slug from the feud title by taking the
    leading proper-noun phrase (1-3 capitalized words)."""
    m = re.match(
        r"\s*([A-ZÀ-Ù][a-zà-ùÀ-Ù']+(?:\s+[A-ZÀ-Ù][a-zà-ùÀ-Ù']+){0,2})",
        (title or '').strip(),
    )
    return m.group(1) if m else ''


def _is_stance_party(name: str) -> bool:
    """Detect if a party string represents a *position/stance* rather than a
    named contender (used for legacy feuds without an explicit `subject`)."""
    if not name:
        return False
    s = name.strip()
    if len(s) > 30:
        return True
    lc = s.lower()
    STANCE_PREFIXES = (
        'chi ', 'difensori', 'contrari', 'sostenitori', 'critici', 'favorevoli',
        'anti-', 'anti ', 'pro-', 'pro ', 'fan di', 'contro '
    )
    return any(lc.startswith(p) for p in STANCE_PREFIXES)


def _hashtag_norm(name: str) -> str:
    """Normalize a name to a compact alphanumeric slug."""
    return re.sub(r'[^a-zA-Z0-9]+', '', (name or '').strip().lower())


# Italian articles/prepositions/connectives that vary between feuds. Stripping
# them lets "il Milan" and "Milan" collapse to the same hashtag bucket.
_HASHTAG_STOPWORDS = {
    'il', 'lo', 'la', 'i', 'gli', 'le',
    'un', 'uno', 'una',
    'di', 'a', 'da', 'in', 'con', 'su', 'per', 'tra', 'fra', 'e', 'ed',
    'del', 'dello', 'della', 'dei', 'degli', 'delle',
    'dal', 'dallo', 'dalla', 'dai', 'dagli', 'dalle',
    'sul', 'sullo', 'sulla', 'sui', 'sugli', 'sulle',
    'nel', 'nello', 'nella', 'nei', 'negli', 'nelle',
    'al', 'allo', 'alla', 'ai', 'agli', 'alle',
    'l', 'd', 'ch', 'che', 'chi',
}


def _clean_subject(name: str) -> str:
    """Extract a canonical PascalCase form of a party/subject name.
    - Drops parenthesised segments (e.g. "Milan (rimonta col PSG)" → "Milan")
    - Removes emoji and punctuation
    - Filters Italian articles / prepositions so variants collapse
    - Capitalizes each surviving word
    Returns "" if nothing usable remains.
    """
    if not name:
        return ''
    s = str(name)
    # Drop anything inside parentheses (usually clarifying context)
    s = re.sub(r'\([^)]*\)', ' ', s)
    # Drop anything inside quotes ("…", '…', «…», “…”)
    s = re.sub(r'[«»“”"\'\']+', ' ', s)
    # Extract alphanumeric words (keep accented letters)
    words = re.findall(r"[A-Za-zÀ-ÿ0-9]+", s)
    kept: List[str] = []
    for w in words:
        if w.lower() in _HASHTAG_STOPWORDS:
            continue
        kept.append(w)
    if not kept:
        return ''
    # PascalCase each token
    return ''.join(w[0].upper() + w[1:].lower() for w in kept)[:40]


def _canonical_hashtag_subjects(
    party_a: Optional[str],
    party_b: Optional[str],
    subject: Optional[str] = None,
    hashtag_subjects: Optional[List[str]] = None,
) -> List[str]:
    """Return the ordered list of clean subject names for hashtag building.

    Order of preference:
    1. Explicit `hashtag_subjects` provided by the AI (best case)
    2. Single `subject` (stance mode B)
    3. Two-contender fallback: party_a + party_b, each cleaned
    Final output is sorted alphabetically to guarantee 'Inter/Milan' == 'Milan/Inter'.
    """
    subs: List[str] = []
    if hashtag_subjects and isinstance(hashtag_subjects, list):
        for x in hashtag_subjects:
            c = _clean_subject(x or '')
            if c:
                subs.append(c)
    if not subs and subject:
        c = _clean_subject(subject)
        if c:
            subs.append(c)
    if not subs:
        for x in (party_a, party_b):
            c = _clean_subject(x or '')
            if c:
                subs.append(c)
    # Dedup (case-insensitive) preserving order
    seen: set = set()
    unique: List[str] = []
    for s in subs:
        k = s.lower()
        if k not in seen:
            seen.add(k)
            unique.append(s)
    # Cap at 2 subjects, then alphabetical order (case-insensitive)
    unique = sorted(unique[:2], key=lambda s: s.lower())
    return unique


def _hashtag_key(
    a: str,
    b: str,
    subject: Optional[str] = None,
    hashtag_subjects: Optional[List[str]] = None,
) -> str:
    """Canonical hashtag key (alphanumeric, lowercase, alphabetically ordered).

    Used for grouping: any two feuds featuring the same subject(s) produce the
    same key regardless of A/B order.
    """
    subs = _canonical_hashtag_subjects(a, b, subject=subject, hashtag_subjects=hashtag_subjects)
    if not subs:
        return 'faida'
    return re.sub(r'[^a-z0-9]', '', ''.join(s.lower() for s in subs))[:64] or 'faida'


def _hashtag_display(
    a: str,
    b: str,
    subject: Optional[str] = None,
    hashtag_subjects: Optional[List[str]] = None,
) -> str:
    """Human-readable hashtag '#SubjectASubjectB' (or '#Subject' for stance mode).
    Names are already PascalCased and alphabetically sorted.
    """
    subs = _canonical_hashtag_subjects(a, b, subject=subject, hashtag_subjects=hashtag_subjects)
    if not subs:
        return '#Faida'
    return '#' + ''.join(subs)[:64]


# ----------------------- RSS News Ingestion -----------------------

RSS_FEEDS: dict = {
    'politica': [
        ('Repubblica Politica', 'https://www.repubblica.it/rss/politica/rss2.0.xml'),
        ('ANSA Politica', 'https://www.ansa.it/sito/notizie/politica/politica_rss.xml'),
        ('Il Fatto Quotidiano', 'https://www.ilfattoquotidiano.it/politica/feed/'),
        ('Corriere Politica', 'https://xml2.corriereobjects.it/rss/politica.xml'),
        ('Fanpage Politica', 'https://www.fanpage.it/politica/feed/'),
    ],
    'tv': [
        ('TvBlog', 'https://www.tvblog.it/feed'),
        ('BubinoBlog', 'https://www.bubinoblog.altervista.org/feed/'),
        ('Fanpage Spettacolo', 'https://www.fanpage.it/spettacolo/feed/'),
        ('IsaeChia', 'https://www.isaechia.it/feed/'),
        ('Biccy', 'https://www.biccy.it/feed/'),
        ('DavideMaggio', 'https://www.davidemaggio.it/feed'),
    ],
    'musica': [
        ('Rolling Stone Italia', 'https://www.rollingstone.it/feed/'),
        ('AllMusicItalia', 'https://www.allmusicitalia.it/feed'),
        ('Fanpage Musica', 'https://music.fanpage.it/feed/'),
    ],
    'sport': [
        ('Gazzetta', 'https://www.gazzetta.it/rss/homepage.xml'),
        ('ANSA Sport', 'https://www.ansa.it/sito/notizie/sport/sport_rss.xml'),
        ('Tuttosport', 'https://www.tuttosport.com/rss/calcio-serie-a.xml'),
        ('Corriere Sport', 'https://xml2.corriereobjects.it/rss/sport.xml'),
    ],
    'cinema': [
        ('BadTaste', 'https://www.badtaste.it/feed/'),
        ('ANSA Cinema', 'https://www.ansa.it/sito/notizie/cultura/cinema/cinema_rss.xml'),
        ('Fanpage Cinema', 'https://cinema.fanpage.it/feed/'),
    ],
    'social': [
        ('DDay', 'https://www.dday.it/rss'),
        ('GossipeTV', 'https://www.gossipetv.com/feed'),
        ('Fanpage Innovazione', 'https://www.fanpage.it/innovazione/feed/'),
    ],
    'gossip': [
        ('Novella 2000', 'https://www.novella2000.it/feed/'),
        ('GossipeTV', 'https://www.gossipetv.com/feed'),
        ('Biccy', 'https://www.biccy.it/feed/'),
        ('IsaeChia', 'https://www.isaechia.it/feed/'),
        ('BubinoBlog', 'https://www.bubinoblog.altervista.org/feed/'),
        ('DavideMaggio', 'https://www.davidemaggio.it/feed'),
    ],
    'tech': [
        ('HDblog', 'https://www.hdblog.it/rss/'),
        ('DDay', 'https://www.dday.it/rss'),
        ("Tom's Hardware Italia", 'https://www.tomshw.it/feed/'),
        ('SmartWorld', 'https://www.smartworld.it/feed'),
        ('Everyeye Tech', 'https://www.everyeye.it/rss/tech.xml'),
    ],
    'cronaca': [
        ('ANSA Cronaca', 'https://www.ansa.it/sito/notizie/cronaca/cronaca_rss.xml'),
        ('Repubblica Cronaca', 'https://www.repubblica.it/rss/cronaca/rss2.0.xml'),
        ('Corriere Cronache', 'https://xml2.corriereobjects.it/rss/cronache.xml'),
        ('Fanpage Cronaca', 'https://www.fanpage.it/cronaca/feed/'),
        ('Il Fatto Quotidiano Cronaca', 'https://www.ilfattoquotidiano.it/cronaca/feed/'),
        ('TgCom24 Cronaca', 'https://www.tgcom24.mediaset.it/rss/cronaca.xml'),
    ],
}


async def _fetch_headlines_for_category(cat_id: str, max_items: int = 18) -> List[dict]:
    # Cache hit
    entry = _RSS_CACHE.get(cat_id)
    if entry and entry[0] > time.time():
        return entry[1][:max_items]

    feeds = RSS_FEEDS.get(cat_id, [])
    if not feeds:
        return []

    def _clean_rss_text(raw: str) -> str:
        """Strip HTML tags & entities from RSS summary/content and squash whitespace."""
        if not raw:
            return ''
        try:
            txt = html_lib.unescape(raw)
        except Exception:
            txt = raw
        # Remove tags including <script> / <style> content
        txt = re.sub(r'<script[\s\S]*?</script>', ' ', txt, flags=re.IGNORECASE)
        txt = re.sub(r'<style[\s\S]*?</style>', ' ', txt, flags=re.IGNORECASE)
        txt = re.sub(r'<[^>]+>', ' ', txt)
        txt = re.sub(r'\s+', ' ', txt).strip()
        return txt

    def _extract_entry_text(entry) -> str:
        # Prefer full content, then summary/description, in this order.
        for key in ('content', 'summary_detail'):
            val = entry.get(key)
            if isinstance(val, list) and val:
                v = val[0]
                if isinstance(v, dict) and v.get('value'):
                    t = _clean_rss_text(v['value'])
                    if t:
                        return t
            elif isinstance(val, dict) and val.get('value'):
                t = _clean_rss_text(val['value'])
                if t:
                    return t
        for key in ('summary', 'description'):
            val = entry.get(key)
            if isinstance(val, str) and val.strip():
                t = _clean_rss_text(val)
                if t:
                    return t
        return ''

    results: List[dict] = []
    async with httpx.AsyncClient(timeout=10.0, follow_redirects=True, headers={'User-Agent': 'PopulusBot/1.0'}) as hx:
        for source_name, url in feeds:
            try:
                r = await hx.get(url)
                if r.status_code != 200:
                    continue
                parsed = feedparser.parse(r.content)
                for entry in parsed.entries[:max_items]:
                    title = (entry.get('title') or '').strip()
                    link = entry.get('link') or ''
                    if title and link:
                        results.append({
                            'title': title[:200],
                            'link': link,
                            'source': source_name,
                            'image': _image_from_entry(entry),
                            # Article excerpt (max ~800 chars) — gives the AI real
                            # context to write informative summaries instead of
                            # improvising from just the headline.
                            'excerpt': _extract_entry_text(entry)[:800],
                        })
                    if len(results) >= max_items * 2:
                        break
            except Exception as e:
                logger.warning(f"RSS fetch failed for {url}: {e}")
    # Dedup by title, cap to max_items
    seen = set()
    out = []
    for r in results:
        key = r['title'].lower()[:80]
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
        if len(out) >= max_items:
            break
    _RSS_CACHE[cat_id] = (time.time() + _RSS_TTL_SECONDS, out)
    return out


SEED_FEUDS = [
    {'category': 'gossip', 'category_label': 'Gossip',
     'title': 'Ferragni vs Fedez: la resa dei conti social',
     'party_a': 'Chiara Ferragni', 'party_b': 'Fedez',
     'summary': 'Dopo mesi di indiscrezioni e stoccate a distanza, i due ex hanno ricominciato a scambiarsi frecciate pubbliche tramite storie e interviste. Fedez accusa, Chiara risponde con eleganza glaciale.',
     'question': 'Chi ha ragione nella nuova faida?'},
    {'category': 'tv', 'category_label': 'Programmi TV',
     'title': 'Grande Fratello: eliminazione contestata',
     'party_a': 'Il concorrente eliminato', 'party_b': 'La produzione',
     'summary': 'Un televoto flash ha estromesso uno dei favoriti scatenando la protesta del pubblico. La produzione difende la regolarità, i fan gridano al complotto.',
     'question': 'Il televoto è stato truccato?'},
    {'category': 'sport', 'category_label': 'Sport',
     'title': 'Derby infuocato: rigore o non rigore?',
     'party_a': 'I tifosi pro-rigore', 'party_b': 'I tifosi contrari',
     'summary': "Al 92° minuto di un derby già rovente il VAR richiama l'arbitro, che assegna un penalty decisivo. La polemica dilaga sui social e in tv per tutto il weekend.",
     'question': 'Era davvero calcio di rigore?'},
    {'category': 'politica', 'category_label': 'Politica',
     'title': 'Legge di bilancio: scontro in aula',
     'party_a': 'Maggioranza', 'party_b': 'Opposizione',
     'summary': "Il ddl bilancio approda in aula tra emendamenti bocciati e cortei fuori dal Parlamento. La maggioranza rivendica le coperture, l'opposizione denuncia tagli su sanità e scuola.",
     'question': 'Chi ha ragione nel merito?'},
    {'category': 'musica', 'category_label': 'Musica',
     'title': 'Nuovo singolo o plagio? Il caso della settimana',
     'party_a': "L'artista accusato", 'party_b': "L'artista che accusa",
     'summary': 'Un tormentone appena uscito è finito nel mirino per una presunta somiglianza con un brano indie del 2019. Le due parti si sfidano a colpi di dichiarazioni e ipotesi di causa.',
     'question': 'È un plagio o pura coincidenza?'},
    {'category': 'cinema', 'category_label': 'Cinema',
     'title': 'Il regista contro la sua stessa attrice',
     'party_a': 'Il regista', 'party_b': 'La protagonista',
     'summary': "A pochi giorni dall'uscita del film, il regista rilascia dichiarazioni al vetriolo sulla protagonista. Lei risponde con un lungo post che ribalta la narrazione.",
     'question': 'Chi sta dicendo la verità?'},
    {'category': 'social', 'category_label': 'Social',
     'title': 'Influencer e sponsor: pubblicità occulta?',
     'party_a': "L'influencer", 'party_b': 'Gli utenti indignati',
     'summary': "Un post che pubblicizzava un integratore senza dichiararlo come inserzione ha scatenato una tempesta di segnalazioni. L'influencer difende la buona fede, gli utenti chiedono trasparenza.",
     'question': 'Serve una multa esemplare?'},
    {'category': 'cronaca', 'category_label': 'Cronaca',
     'title': 'Caso di cronaca al centro del dibattito',
     'party_a': "Chi chiede pene certe", 'party_b': 'Chi difende le garanzie',
     'summary': "Un fatto di cronaca nera divide l'opinione pubblica tra chi invoca inasprimento delle pene e chi sottolinea la necessità di rispettare il giusto processo. I talk show dedicano ore al caso.",
     'question': 'Servono pene più severe o più garanzie processuali?'},
]


async def seed_if_empty():
    count = await db.feuds.count_documents({})
    if count > 0:
        logger.info(f"Feuds already present: {count}, skipping seed")
        return
    for base in SEED_FEUDS:
        doc = {
            'feud_id': new_id('feud'), **base,
            'image_url': _image_for_category(base['category']),
            'votes_a': 0, 'votes_b': 0, 'created_at': now_utc(), 'source': 'seed',
        }
        await db.feuds.insert_one(doc)
    logger.info(f"Seeded {len(SEED_FEUDS)} feuds")


@app.on_event('startup')
async def on_startup():
    await db.users.create_index('email', unique=False, sparse=True)
    await db.users.create_index('user_id', unique=True)
    await db.user_sessions.create_index('session_token', unique=True)
    await db.user_sessions.create_index('user_id')
    await db.user_sessions.create_index('expires_at', expireAfterSeconds=0)
    await db.feuds.create_index('feud_id', unique=True)
    await db.feuds.create_index('category')
    await db.votes.create_index([('feud_id', 1), ('user_id', 1)], unique=True)
    await db.comments.create_index('feud_id')
    await db.replies.create_index('comment_id')
    await db.sponsors.create_index('category')
    await db.user_photos.create_index('user_id')
    await db.user_photos.create_index([('user_id', 1), ('position', 1)])
    await db.favorites.create_index([('user_id', 1), ('feud_id', 1)], unique=True)
    await db.favorites.create_index([('user_id', 1), ('created_at', -1)])
    # TTL index: expired verification tokens are removed automatically.
    await db.verification_tokens.create_index([('token_hash', 1)], unique=True)
    await db.verification_tokens.create_index('expires_at', expireAfterSeconds=0)
    # Grandfather: existing email users without the new flag are marked as
    # verified so the new login-block doesn't lock them out.
    try:
        await db.users.update_many(
            {'auth_provider': 'email', 'email_verified': {'$exists': False}},
            {'$set': {'email_verified': True}},
        )
    except Exception as e:
        logger.warning(f"grandfather email_verified migration failed: {e}")
    # One-shot backfill: users who onboarded BEFORE `cronaca` was introduced
    # can't possibly have it in their favorites (it didn't exist), so the
    # "favorites-only" home filter effectively hides it from them until they
    # manually opt in. Add it to every user whose favorites are set and don't
    # yet contain it — a one-time nudge, not a lock-in (they can still remove
    # it via the profile prefs editor).
    try:
        await db.users.update_many(
            {
                'favorite_categories.0': {'$exists': True},
                'favorite_categories': {'$nin': ['cronaca']},
            },
            {'$addToSet': {'favorite_categories': 'cronaca'}},
        )
    except Exception as e:
        logger.warning(f"cronaca favorite backfill failed: {e}")
    # One-shot: reorder every user's photos so their primary sits at position
    # 0 — pre-existing accounts had position frozen at upload time, which
    # made external viewers land on a random photo when opening the gallery.
    try:
        cur = db.users.find(
            {'primary_photo_id': {'$exists': True, '$ne': None}},
            {'_id': 0, 'user_id': 1, 'primary_photo_id': 1},
        )
        migrated = 0
        async for u in cur:
            try:
                await _reorder_photos_primary_first(u['user_id'], u['primary_photo_id'])
                migrated += 1
            except Exception as inner:
                logger.warning(f"reorder photos failed for {u.get('user_id')}: {inner}")
        if migrated:
            logger.info(f"photo position backfill: reordered {migrated} users")
    except Exception as e:
        logger.warning(f"photo position backfill failed: {e}")
    # Full-text index for search
    try:
        await db.feuds.create_index([('title', 'text'), ('summary', 'text'), ('party_a', 'text'), ('party_b', 'text')])
    except Exception as e:
        logger.warning(f"text index creation failed: {e}")
    await seed_if_empty()
    await seed_sponsors_if_empty()
    # Start background daily generation task
    import asyncio as _asyncio
    _asyncio.create_task(_daily_generation_loop())


async def _cleanup_expired_feuds() -> None:
    """Delete feuds (and related comments/replies) older than FEUD_RETENTION_DAYS.
    Before removing each feud, freeze final `aligned_final`/`winning_side_final`
    onto every vote so the voting history keeps rendering the preview + badge."""
    cutoff = now_utc() - timedelta(days=FEUD_RETENTION_DAYS)
    expired = await db.feuds.find({'created_at': {'$lt': cutoff}}, {'_id': 0}).to_list(500)
    for f in expired:
        fid = f['feud_id']
        a = f.get('votes_a', 0)
        b = f.get('votes_b', 0)
        if a == b:
            winning_side = None
            tie = True
        else:
            winning_side = 'A' if a > b else 'B'
            tie = False
        # Freeze snapshot + alignment on each vote
        votes_cur = db.votes.find({'feud_id': fid}, {'_id': 0, 'vote_id': 1, 'side': 1, 'feud_snapshot': 1})
        async for v in votes_cur:
            aligned = True if tie else (v['side'] == winning_side)
            update: dict = {
                'aligned_final': aligned,
                'winning_side_final': winning_side,
            }
            if not v.get('feud_snapshot'):
                update['feud_snapshot'] = {
                    'title': f.get('title'),
                    'category_label': f.get('category_label'),
                    'party_a': f.get('party_a'),
                    'party_b': f.get('party_b'),
                    'image_url': f.get('image_url'),
                }
            await db.votes.update_one({'vote_id': v['vote_id']}, {'$set': update})
        # Delete replies for comments of this feud, then comments, then the feud.
        comments = await db.comments.find({'feud_id': fid}, {'_id': 0, 'comment_id': 1}).to_list(2000)
        if comments:
            comment_ids = [c['comment_id'] for c in comments]
            await db.replies.delete_many({'comment_id': {'$in': comment_ids}})
        await db.comments.delete_many({'feud_id': fid})
        await db.feuds.delete_one({'feud_id': fid})
    if expired:
        logger.info(f"cleanup: purged {len(expired)} feuds older than {FEUD_RETENTION_DAYS} days")


async def _daily_generation_loop():
    """Continuous feud generator. Tries every category every SCHEDULER_TICK_MIN
    minutes. A category is skipped only if it already produced a feud in the
    last CATEGORY_COOLDOWN_MIN minutes — otherwise we attempt a fresh one.

    Design goals:
    - Users opening the app should never see feuds older than ~15-20 min in
      *some* category. Bursts are avoided because the tick is small.
    - Categories with sparse RSS (e.g. `social` overnight) fail gracefully
      via AI-skip without consuming a cooldown slot.
    - Combined with the used-links filter + hot-topic boost, this yields
      distributed generation day and night.
    """
    import asyncio as _asyncio
    SCHEDULER_TICK_MIN = 10        # try each category every 10 min
    CATEGORY_COOLDOWN_MIN = 20     # min gap between successful feuds for the same category
    while True:
        try:
            try:
                from emergentintegrations.llm.chat import LlmChat, UserMessage
            except Exception as e:
                logger.warning(f"scheduler LLM import failed: {e}")
                await _asyncio.sleep(SCHEDULER_TICK_MIN * 60)
                continue

            cooldown_ago = now_utc() - timedelta(minutes=CATEGORY_COOLDOWN_MIN)
            for cat in CATEGORIES:
                try:
                    recent_count = await db.feuds.count_documents(
                        {'category': cat['id'], 'source': 'ai', 'created_at': {'$gte': cooldown_ago}}
                    )
                    if recent_count >= 1:
                        # In cooldown — skip silently to keep logs clean.
                        continue
                    logger.info(
                        f"scheduler: attempting fresh feud for {cat['id']} "
                        f"(cooldown {CATEGORY_COOLDOWN_MIN}min cleared)"
                    )
                    feud = await _generate_feud_for_category(cat, LlmChat, UserMessage)
                    if feud:
                        # Fact-checker gate: strict editorial review before publish.
                        chosen_headline = (feud.get('sources') or [{}])[0]
                        feud = await _ai_fact_check_feud(feud, chosen_headline, LlmChat, UserMessage)
                    if feud:
                        await db.feuds.insert_one(feud)
                        logger.info(f"scheduler: inserted feud for {cat['id']}")
                        # Hot-news trigger: high-engagement faide fan out a push
                        # to users who have this category among their favorites.
                        # Rate-limited to 1 push per user per day (across all
                        # categories), so even multiple hot faide won't spam.
                        try:
                            await _fanout_hot_news(feud)
                        except Exception as e:
                            logger.warning(f"hot-news fanout failed: {e}")
                except Exception as e:
                    logger.warning(f"scheduler gen failed for {cat['id']}: {e}")
            await db.system_meta.update_one(
                {'key': 'last_scheduler_run'},
                {'$set': {'key': 'last_scheduler_run', 'at': now_utc()}},
                upsert=True,
            )
            # Purge feuds older than the retention window (2 weeks).
            try:
                await _cleanup_expired_feuds()
            except Exception as e:
                logger.warning(f"cleanup error: {e}")
        except Exception as e:
            logger.warning(f"scheduler loop error: {e}")
        await _asyncio.sleep(SCHEDULER_TICK_MIN * 60)


@api_router.get('/')
async def root():
    return {'message': 'Populus API', 'ok': True}


# =============================================================================
# MESSAGING SYSTEM (Direct Messages between registered users)
# =============================================================================
# - 1-to-1 conversations only. Anonymous users cannot send or receive.
# - Blocks are bi-directional filters (either side blocking hides the other).
# - Real-time delivery via WebSocket; polling fallback via REST.
# - Push notifications fire when recipient is not currently connected via WS.
# =============================================================================

MAX_MSG_TEXT = 2000
MAX_MSG_IMAGE_BYTES = 3_000_000  # ~3MB base64 payload
COMMON_REACTIONS = {'❤️', '😂', '😮', '😢', '😡', '👍', '👎', '🔥'}


class SendMessageBody(BaseModel):
    recipient_id: str = Field(min_length=1)
    text: Optional[str] = Field(default=None, max_length=MAX_MSG_TEXT)
    image_data: Optional[str] = Field(default=None, max_length=MAX_MSG_IMAGE_BYTES)
    # Instagram-style "share a post to a friend" — attaches a snapshot of the
    # feud so the recipient sees a preview inline in chat that they can tap
    # to open. Only feud_id is trusted from the client; the snapshot fields
    # are built server-side from the current feud document.
    shared_feud_id: Optional[str] = Field(default=None, min_length=1, max_length=120)


class ShareToUsersBody(BaseModel):
    """Payload for /feuds/{id}/share — the fan-out share-sheet endpoint.

    `recipient_ids` is the list of users the sender wants to share the feud
    with in a single tap (Instagram-style multi-select). `text` is optional
    and attached identically to every generated message.
    """
    recipient_ids: List[str] = Field(min_length=1, max_length=25)
    text: Optional[str] = Field(default=None, max_length=MAX_MSG_TEXT)


class ReactMessageBody(BaseModel):
    emoji: str = Field(min_length=1, max_length=8)


class ReportUserBody(BaseModel):
    reason: str = Field(min_length=2, max_length=500)
    message_id: Optional[str] = None


def _conv_key(a: str, b: str) -> str:
    """Deterministic conversation id from two user ids (sorted)."""
    lo, hi = sorted([a, b])
    return f"conv_{lo}_{hi}"


async def _both_registered(uid_a: str, uid_b: str) -> tuple[dict, dict]:
    a = await db.users.find_one({'user_id': uid_a}, {'_id': 0})
    b = await db.users.find_one({'user_id': uid_b}, {'_id': 0})
    if not a or not b:
        raise HTTPException(status_code=404, detail='Utente non trovato')
    if a.get('auth_provider') == 'anonymous' or a.get('is_anonymous'):
        raise HTTPException(status_code=403, detail="Gli utenti anonimi non possono usare la chat")
    if b.get('auth_provider') == 'anonymous' or b.get('is_anonymous'):
        raise HTTPException(status_code=403, detail="L'utente destinatario è anonimo e non può ricevere messaggi")
    return a, b


async def _is_blocked_pair(a: str, b: str) -> bool:
    """True if either user has blocked the other."""
    n = await db.user_blocks.count_documents({
        '$or': [
            {'blocker_id': a, 'blocked_id': b},
            {'blocker_id': b, 'blocked_id': a},
        ],
    })
    return n > 0


async def _ensure_conversation(uid_a: str, uid_b: str) -> dict:
    cid = _conv_key(uid_a, uid_b)
    existing = await db.conversations.find_one({'conversation_id': cid}, {'_id': 0})
    if existing:
        return existing
    doc = {
        'conversation_id': cid,
        'participants': sorted([uid_a, uid_b]),
        'last_message_at': None,
        'last_message_preview': '',
        'last_sender_id': None,
        'created_at': now_utc(),
    }
    await db.conversations.insert_one(doc)
    return doc


# --- WebSocket registry -------------------------------------------------------
WS_CLIENTS: dict[str, set[WebSocket]] = {}


async def _ws_send(user_id: str, event: dict) -> None:
    sockets = WS_CLIENTS.get(user_id, set())
    if not sockets:
        return
    dead: list[WebSocket] = []
    for ws in list(sockets):
        try:
            await ws.send_json(event)
        except Exception:
            dead.append(ws)
    for ws in dead:
        sockets.discard(ws)


def _user_is_online(user_id: str) -> bool:
    return bool(WS_CLIENTS.get(user_id))


def _serialize_message(m: dict) -> dict:
    out = dict(m)
    if isinstance(out.get('created_at'), datetime):
        out['created_at'] = _iso_utc(out['created_at'])
    if isinstance(out.get('read_at'), datetime):
        out['read_at'] = _iso_utc(out['read_at'])
    out.pop('_id', None)
    return out


def _preview_text(text: Optional[str], has_image: bool, shared_feud: Optional[dict] = None) -> str:
    if shared_feud:
        title = (shared_feud.get('title') or '').strip()
        base = '📎 Post condiviso' + (f' · {title}' if title else '')
        if text and text.strip():
            return (text.strip() + ' · ' + base)[:120]
        return base[:120]
    if text and text.strip():
        return text.strip()[:120]
    if has_image:
        return '📷 Foto'
    return ''


async def _mini_user(uid: str) -> dict:
    """Compact user info for conversation list / chat header."""
    u = await db.users.find_one({'user_id': uid}, {'_id': 0, 'user_id': 1, 'nickname': 1, 'primary_photo_id': 1, 'auth_provider': 1})
    if not u:
        return {'user_id': uid, 'nickname': 'Utente', 'primary_photo_id': None, 'photo_data': None}
    photo_data = None
    if u.get('primary_photo_id'):
        p = await db.user_photos.find_one({'user_id': uid, 'photo_id': u['primary_photo_id']}, {'_id': 0, 'data': 1})
        if p:
            photo_data = p.get('data')
    return {
        'user_id': uid,
        'nickname': u.get('nickname') or 'Utente',
        'primary_photo_id': u.get('primary_photo_id'),
        'photo_data': photo_data,
    }


# --- REST endpoints -----------------------------------------------------------
@api_router.get('/messages/unread-count')
async def messages_unread_count(user: dict = Depends(get_current_user)):
    if user.get('is_anonymous') or user.get('auth_provider') == 'anonymous':
        return {'count': 0}
    n = await db.messages.count_documents({
        'recipient_id': user['user_id'],
        'read_at': None,
        'deleted': {'$ne': True},
    })
    return {'count': n}


@api_router.get('/messages/share-suggestions')
async def share_suggestions(limit: int = 21, user: dict = Depends(get_current_user)):
    """Instagram-style "share to" suggestions.

    Ranking heuristic (weighted, most→least):
    1. Existing conversation partners in the last 90 days, ordered by
       cumulative message count (both directions).
    2. Users the caller has recently REPLIED to on public comments.
    3. Users who have recently replied to the caller's public comments.

    Anonymous users get an empty list (they can't send messages anyway).
    We de-duplicate, exclude self + blocked users, and return a compact
    payload including the primary photo (base64 preview) so the client can
    render the grid without a follow-up per-user fetch.
    """
    if user.get('is_anonymous') or user.get('auth_provider') == 'anonymous':
        return {'users': []}
    me = user['user_id']
    limit = max(1, min(int(limit or 21), 60))
    scores: dict[str, float] = {}
    since = now_utc() - timedelta(days=90)

    # 1) Chat partners
    try:
        cursor = db.messages.find(
            {
                '$or': [{'sender_id': me}, {'recipient_id': me}],
                'deleted': {'$ne': True},
                'created_at': {'$gte': since},
            },
            {'_id': 0, 'sender_id': 1, 'recipient_id': 1},
        ).limit(5000)
        async for m in cursor:
            other = m['recipient_id'] if m['sender_id'] == me else m['sender_id']
            if other == me:
                continue
            scores[other] = scores.get(other, 0.0) + 1.0
    except Exception as e:
        logger.warning(f"share suggestions (messages) failed: {e}")

    # 2) Users I've replied to (public comments)
    try:
        my_replies = db.replies.find(
            {'user_id': me, 'created_at': {'$gte': since}},
            {'_id': 0, 'comment_id': 1},
        ).limit(300)
        async for r in my_replies:
            parent = await db.comments.find_one(
                {'comment_id': r['comment_id']}, {'_id': 0, 'user_id': 1}
            )
            if parent and parent.get('user_id') and parent['user_id'] != me:
                scores[parent['user_id']] = scores.get(parent['user_id'], 0.0) + 0.4
    except Exception as e:
        logger.warning(f"share suggestions (replies) failed: {e}")

    # 3) Users who replied to my comments
    try:
        my_comments = db.comments.find(
            {'user_id': me, 'created_at': {'$gte': since}},
            {'_id': 0, 'comment_id': 1},
        ).limit(300)
        my_comment_ids: list[str] = []
        async for c in my_comments:
            my_comment_ids.append(c['comment_id'])
        if my_comment_ids:
            replies_in = db.replies.find(
                {'comment_id': {'$in': my_comment_ids}, 'user_id': {'$ne': me}},
                {'_id': 0, 'user_id': 1},
            ).limit(400)
            async for r in replies_in:
                uid = r.get('user_id')
                if uid and uid != me:
                    scores[uid] = scores.get(uid, 0.0) + 0.3
    except Exception as e:
        logger.warning(f"share suggestions (replies_in) failed: {e}")

    # Exclude blocked pairs (both directions) so users we can't message
    # never appear as "share suggestions" — otherwise tapping them would
    # fail with 403 and the sheet would show a misleading error.
    # The user_blocks collection stores {blocker_id, blocked_id}.
    try:
        blocks = db.user_blocks.find(
            {'$or': [{'blocker_id': me}, {'blocked_id': me}]},
            {'_id': 0, 'blocker_id': 1, 'blocked_id': 1},
        )
        async for b in blocks:
            other = b.get('blocked_id') if b.get('blocker_id') == me else b.get('blocker_id')
            if other:
                scores.pop(other, None)
    except Exception as e:
        logger.warning(f"share suggestions (blocks) failed: {e}")

    # Rank and hydrate
    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)[:limit]
    if not ranked:
        return {'users': []}
    uids = [u for u, _ in ranked]
    users_map: dict[str, dict] = {}
    async for u in db.users.find(
        {'user_id': {'$in': uids}, 'auth_provider': {'$ne': 'anonymous'}, 'is_anonymous': {'$ne': True}},
        {'_id': 0, 'user_id': 1, 'nickname': 1, 'primary_photo_id': 1},
    ):
        users_map[u['user_id']] = u
    out: List[dict] = []
    for uid, score in ranked:
        u = users_map.get(uid)
        if not u:
            continue
        photo_data = None
        if u.get('primary_photo_id'):
            ph = await db.user_photos.find_one(
                {'user_id': uid, 'photo_id': u['primary_photo_id']},
                {'_id': 0, 'data': 1},
            )
            photo_data = (ph or {}).get('data')
        out.append({
            'user_id': uid,
            'nickname': u.get('nickname') or 'Utente',
            'primary_photo_id': u.get('primary_photo_id'),
            'photo_data': photo_data,
            'score': round(score, 2),
        })
    return {'users': out}


@api_router.get('/search/users')
async def search_users(q: str, limit: int = 20, user: dict = Depends(get_current_user)):
    """Nickname substring search — case-insensitive, excludes self and
    anonymous accounts. Used by the share-sheet's search bar."""
    if user.get('is_anonymous') or user.get('auth_provider') == 'anonymous':
        return {'users': []}
    q = (q or '').strip()
    if len(q) < 1:
        return {'users': []}
    # Escape regex special chars in the user's raw query so a stray '.' or
    # '+' doesn't broaden the match.
    safe = _re.escape(q)
    limit = max(1, min(int(limit or 20), 40))
    me = user['user_id']

    # Pre-compute the set of user ids we can't message (either we blocked
    # them or they blocked us) so search results don't surface unshareable
    # accounts.
    blocked_ids: set[str] = set()
    try:
        async for b in db.user_blocks.find(
            {'$or': [{'blocker_id': me}, {'blocked_id': me}]},
            {'_id': 0, 'blocker_id': 1, 'blocked_id': 1},
        ):
            other = b.get('blocked_id') if b.get('blocker_id') == me else b.get('blocker_id')
            if other:
                blocked_ids.add(other)
    except Exception as e:
        logger.warning(f"search_users blocks lookup failed: {e}")

    excluded = list(blocked_ids | {me})
    # Match against nickname OR display_name so users can find friends
    # by either the @handle or the real/visible name they set on their
    # profile.
    cursor = db.users.find(
        {
            '$or': [
                {'nickname': {'$regex': safe, '$options': 'i'}},
                {'display_name': {'$regex': safe, '$options': 'i'}},
            ],
            'user_id': {'$nin': excluded},
            'auth_provider': {'$ne': 'anonymous'},
            'is_anonymous': {'$ne': True},
        },
        {'_id': 0, 'user_id': 1, 'nickname': 1, 'primary_photo_id': 1, 'display_name': 1},
    ).limit(limit)
    results: List[dict] = []
    async for u in cursor:
        photo_data = None
        if u.get('primary_photo_id'):
            ph = await db.user_photos.find_one(
                {'user_id': u['user_id'], 'photo_id': u['primary_photo_id']},
                {'_id': 0, 'data': 1},
            )
            photo_data = (ph or {}).get('data')
        results.append({
            'user_id': u['user_id'],
            'nickname': u.get('nickname') or 'Utente',
            'display_name': u.get('display_name'),
            'primary_photo_id': u.get('primary_photo_id'),
            'photo_data': photo_data,
        })
    return {'users': results}


@api_router.get('/circle/suggestions')
async def suggested_users(limit: int = 20, user: dict = Depends(get_current_user)):
    """Suggest users the viewer might want to add to their Cerchia.

    Ranking pool (union, each candidate deduped and self+block-filtered):
      1. DM contacts (users the viewer has exchanged messages with).
      2. Friends-of-friends (members of the viewer's circle's circles).
      3. Co-commenters (users who recently commented on the same feuds
         the viewer commented on).

    Each candidate gets a score composed of the three signals; the final
    list is truncated to `limit`. Users already in the viewer's circle,
    or who blocked (or were blocked by) the viewer, are excluded.

    Anonymous users get an empty list — Populus' social graph is opt-in
    and requires a real identity anyway.
    """
    if user.get('is_anonymous') or user.get('auth_provider') == 'anonymous':
        return {'users': []}
    me = user['user_id']
    limit = max(1, min(int(limit or 20), 40))

    # Exclusions: my current circle + people blocked in either direction.
    my_circle = set()
    async for row in db.friendships.find({'user_id': me}, {'_id': 0, 'friend_id': 1}):
        my_circle.add(row['friend_id'])
    blocked: set[str] = set()
    async for b in db.user_blocks.find(
        {'$or': [{'blocker_id': me}, {'blocked_id': me}]},
        {'_id': 0, 'blocker_id': 1, 'blocked_id': 1},
    ):
        other = b.get('blocked_id') if b.get('blocker_id') == me else b.get('blocker_id')
        if other:
            blocked.add(other)
    excluded = my_circle | blocked | {me}

    # Weights per signal — tuneable. DM contacts are strongest because
    # they represent explicit interaction; friends-of-friends is second
    # because it's a graph proxy; co-commenters is the softest signal.
    W_DM = 5.0
    W_FOF = 3.0
    W_COC = 1.0

    scores: dict[str, float] = {}
    reasons: dict[str, list[str]] = {}

    # 1. DM contacts (union of sender/recipient counterparties). Count
    # message exchanges (capped) so power-contacts float higher.
    dm_counts: dict[str, int] = {}
    async for m in db.messages.find(
        {
            '$or': [{'sender_id': me}, {'recipient_id': me}],
            'deleted': {'$ne': True},
        },
        {'_id': 0, 'sender_id': 1, 'recipient_id': 1},
    ):
        other = m['recipient_id'] if m['sender_id'] == me else m['sender_id']
        if other and other not in excluded:
            dm_counts[other] = dm_counts.get(other, 0) + 1
    for uid, cnt in dm_counts.items():
        scores[uid] = scores.get(uid, 0) + W_DM * min(cnt, 20) / 20
        reasons.setdefault(uid, []).append('chat')

    # 2. Friends-of-friends. Fetch the circles of everyone in MY circle,
    # then count how many mutual paths lead to each candidate.
    if my_circle:
        fof_counts: dict[str, int] = {}
        async for row in db.friendships.find(
            {'user_id': {'$in': list(my_circle)}},
            {'_id': 0, 'friend_id': 1},
        ):
            fid = row.get('friend_id')
            if fid and fid not in excluded:
                fof_counts[fid] = fof_counts.get(fid, 0) + 1
        for uid, cnt in fof_counts.items():
            scores[uid] = scores.get(uid, 0) + W_FOF * min(cnt, 10) / 10
            reasons.setdefault(uid, []).append('amici_di_amici')

    # 3. Co-commenters on the same feuds. Look at my recent comments to
    # find the feud ids I've engaged with, then any OTHER commenter on
    # those feuds is a plausible ideological neighbour.
    my_feuds: set[str] = set()
    async for c in db.comments.find(
        {'user_id': me}, {'_id': 0, 'feud_id': 1},
    ).sort('created_at', -1).limit(60):
        fid = c.get('feud_id')
        if fid:
            my_feuds.add(fid)
    if my_feuds:
        co_counts: dict[str, int] = {}
        async for c in db.comments.find(
            {'feud_id': {'$in': list(my_feuds)}, 'user_id': {'$ne': me}},
            {'_id': 0, 'user_id': 1},
        ):
            uid = c.get('user_id')
            if uid and uid not in excluded:
                co_counts[uid] = co_counts.get(uid, 0) + 1
        for uid, cnt in co_counts.items():
            scores[uid] = scores.get(uid, 0) + W_COC * min(cnt, 20) / 20
            reasons.setdefault(uid, []).append('commenti_in_comune')

    if not scores:
        return {'users': []}

    # Sort by score desc, then hydrate mini_user info. Stop once we have
    # `limit` non-anonymous users (we may lose some to the anonymity or
    # existence filter).
    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    hydrated: list[dict] = []
    for uid, _score in ranked:
        if len(hydrated) >= limit:
            break
        u = await db.users.find_one(
            {'user_id': uid, 'auth_provider': {'$ne': 'anonymous'}, 'is_anonymous': {'$ne': True}},
            {'_id': 0, 'user_id': 1, 'nickname': 1, 'display_name': 1, 'primary_photo_id': 1},
        )
        if not u:
            continue
        photo_data = None
        if u.get('primary_photo_id'):
            ph = await db.user_photos.find_one(
                {'user_id': uid, 'photo_id': u['primary_photo_id']},
                {'_id': 0, 'data': 1},
            )
            photo_data = (ph or {}).get('data')
        hydrated.append({
            'user_id': uid,
            'nickname': u.get('nickname') or 'Utente',
            'display_name': u.get('display_name'),
            'photo_data': photo_data,
            'reasons': reasons.get(uid, []),
        })
    return {'users': hydrated}


@api_router.post('/feuds/{feud_id}/share')
async def share_feud_to_users(feud_id: str, body: ShareToUsersBody, user: dict = Depends(get_current_user)):
    """Fan-out multi-recipient share — one DM per selected recipient, all
    carrying the same optional text + a snapshot of the feud."""
    if user.get('is_anonymous') or user.get('auth_provider') == 'anonymous':
        raise HTTPException(status_code=403, detail="Gli utenti anonimi non possono condividere post")
    feud = await db.feuds.find_one(
        {'feud_id': feud_id},
        {'_id': 0, 'feud_id': 1, 'title': 1, 'image_url': 1, 'category': 1, 'category_label': 1},
    )
    if not feud:
        raise HTTPException(status_code=404, detail='Post non trovato')
    text = (body.text or '').strip() or None
    ok: List[str] = []
    failed: List[dict] = []
    for rid in body.recipient_ids:
        if rid == user['user_id']:
            failed.append({'user_id': rid, 'error': 'self'})
            continue
        try:
            # Delegate to the standard send-message path so all the usual
            # invariants (blocked pair check, WS delivery, push, unread
            # counter) apply uniformly.
            await send_message(
                SendMessageBody(recipient_id=rid, text=text, shared_feud_id=feud_id),
                user=user,
            )
            ok.append(rid)
        except HTTPException as e:
            failed.append({'user_id': rid, 'error': str(e.detail)})
        except Exception as e:
            failed.append({'user_id': rid, 'error': str(e)})
    return {'sent': ok, 'failed': failed}


@api_router.get('/messages/conversations')
async def list_conversations(user: dict = Depends(get_current_user)):
    if user.get('is_anonymous') or user.get('auth_provider') == 'anonymous':
        raise HTTPException(status_code=403, detail="Gli utenti anonimi non possono usare la chat")
    uid = user['user_id']
    convs = await db.conversations.find(
        {'participants': uid},
        {'_id': 0},
    ).sort('last_message_at', -1).to_list(200)
    # Filter out conversations where the other side has been blocked by/is-blocking us.
    blocked_ids = set()
    async for b in db.user_blocks.find({'$or': [{'blocker_id': uid}, {'blocked_id': uid}]}, {'_id': 0}):
        blocked_ids.add(b.get('blocker_id') if b.get('blocked_id') == uid else b.get('blocked_id'))
    out = []
    for c in convs:
        parts = c.get('participants', [])
        other_id = parts[0] if parts[1] == uid else parts[1]
        if other_id in blocked_ids:
            continue
        if not c.get('last_message_at'):
            # Skip empty ghost conversations.
            continue
        unread = await db.messages.count_documents({
            'conversation_id': c['conversation_id'],
            'recipient_id': uid,
            'read_at': None,
            'deleted': {'$ne': True},
        })
        mini = await _mini_user(other_id)
        out.append({
            'conversation_id': c['conversation_id'],
            'other_user': mini,
            'last_message_at': _iso_utc(c['last_message_at']) if c.get('last_message_at') else None,
            'last_message_preview': c.get('last_message_preview', ''),
            'last_sender_id': c.get('last_sender_id'),
            'unread': unread,
        })
    return {'conversations': out}


@api_router.get('/messages/with/{other_user_id}')
async def messages_with(other_user_id: str, before: Optional[str] = None,
                        limit: int = 50,
                        user: dict = Depends(get_current_user)):
    if other_user_id == user['user_id']:
        raise HTTPException(status_code=400, detail='Non puoi chattare con te stesso')
    _, other = await _both_registered(user['user_id'], other_user_id)
    conv = await _ensure_conversation(user['user_id'], other_user_id)
    q: dict = {'conversation_id': conv['conversation_id'], 'deleted': {'$ne': True}}
    if before:
        try:
            before_dt = datetime.fromisoformat(before.replace('Z', '+00:00'))
            q['created_at'] = {'$lt': before_dt}
        except Exception:
            pass
    limit = max(1, min(limit, 100))
    msgs = await db.messages.find(q, {'_id': 0}).sort('created_at', -1).to_list(limit)
    msgs.reverse()
    # Determine block status for UI.
    i_blocked = await db.user_blocks.count_documents({'blocker_id': user['user_id'], 'blocked_id': other_user_id}) > 0
    they_blocked = await db.user_blocks.count_documents({'blocker_id': other_user_id, 'blocked_id': user['user_id']}) > 0
    return {
        'conversation_id': conv['conversation_id'],
        'other_user': await _mini_user(other_user_id),
        'messages': [_serialize_message(m) for m in msgs],
        'i_blocked': i_blocked,
        'they_blocked': they_blocked,
    }


@api_router.post('/messages/send')
async def send_message(body: SendMessageBody, user: dict = Depends(get_current_user)):
    if user.get('is_anonymous') or user.get('auth_provider') == 'anonymous':
        raise HTTPException(status_code=403, detail="Gli utenti anonimi non possono inviare messaggi")
    if body.recipient_id == user['user_id']:
        raise HTTPException(status_code=400, detail='Non puoi inviare messaggi a te stesso')
    _, other = await _both_registered(user['user_id'], body.recipient_id)
    if await _is_blocked_pair(user['user_id'], body.recipient_id):
        raise HTTPException(status_code=403, detail="Non puoi contattare questo utente")
    text = (body.text or '').strip() or None
    img = body.image_data
    if img:
        # strip prefix if present
        if img.startswith('data:'):
            img = img.split(',', 1)[-1]
    # Optionally attach a shared feud snapshot. We fetch the feud server-side
    # so a client can't spoof title/image and so a stale local snapshot never
    # ends up in someone's inbox.
    shared_feud: Optional[dict] = None
    if body.shared_feud_id:
        f = await db.feuds.find_one(
            {'feud_id': body.shared_feud_id},
            {'_id': 0, 'feud_id': 1, 'title': 1, 'image_url': 1, 'category': 1, 'category_label': 1},
        )
        if f:
            shared_feud = {
                'feud_id': f['feud_id'],
                'title': f.get('title'),
                'image_url': f.get('image_url'),
                'category': f.get('category'),
                'category_label': f.get('category_label'),
            }
    if not text and not img and not shared_feud:
        raise HTTPException(status_code=400, detail='Messaggio vuoto')
    conv = await _ensure_conversation(user['user_id'], body.recipient_id)
    now = now_utc()
    if shared_feud:
        kind = 'shared_feud'
    elif img and not text:
        kind = 'image'
    elif text and not img:
        kind = 'text'
    else:
        kind = 'mixed'
    doc = {
        'message_id': new_id('msg'),
        'conversation_id': conv['conversation_id'],
        'sender_id': user['user_id'],
        'recipient_id': body.recipient_id,
        'text': text,
        'image_data': img,
        'shared_feud': shared_feud,
        'kind': kind,
        'reactions': {},
        'created_at': now,
        'read_at': None,
        'deleted': False,
    }
    await db.messages.insert_one(doc)
    preview = _preview_text(text, bool(img), shared_feud)
    await db.conversations.update_one(
        {'conversation_id': conv['conversation_id']},
        {'$set': {
            'last_message_at': now,
            'last_message_preview': preview,
            'last_sender_id': user['user_id'],
        }},
    )
    payload = _serialize_message(doc)
    # Real-time delivery
    await _ws_send(body.recipient_id, {'type': 'message.new', 'message': payload})
    await _ws_send(user['user_id'], {'type': 'message.sent', 'message': payload})
    # Push notification if recipient is offline and has push enabled.
    try:
        if not _user_is_online(body.recipient_id):
            recip = await db.users.find_one(
                {'user_id': body.recipient_id, '$or': [{'push_notifications': True}, {'push_notifications': {'$exists': False}}]},
                {'_id': 0, 'user_id': 1, 'nickname': 1},
            )
            if recip:
                sender_nick = user.get('nickname') or 'Utente'
                await send_push(
                    recipients=[body.recipient_id],
                    data={
                        'title': f'Nuovo messaggio da @{sender_nick}',
                        'message': preview or 'Ti ha inviato un messaggio',
                        'action_url': f"/messages/{user['user_id']}",
                    },
                )
    except Exception as e:
        logger.warning(f"push (message) failed: {e}")
    return {'message': payload}


@api_router.post('/messages/mark-all-read')
async def mark_all_messages_read(user: dict = Depends(get_current_user)):
    """Mark every unread message addressed to the current user as read.

    Used as a self-healing sweep when the messages list screen finds it has
    no conversations but the tab-badge counter still reports unread messages
    (orphans from deleted/blocked conversations).
    """
    if user.get('is_anonymous') or user.get('auth_provider') == 'anonymous':
        return {'updated': 0}
    now = now_utc()
    r = await db.messages.update_many(
        {
            'recipient_id': user['user_id'],
            'read_at': None,
            'deleted': {'$ne': True},
        },
        {'$set': {'read_at': now}},
    )
    return {'updated': r.modified_count}



@api_router.post('/messages/with/{other_user_id}/read')
async def mark_conversation_read(other_user_id: str, user: dict = Depends(get_current_user)):
    if user.get('is_anonymous') or user.get('auth_provider') == 'anonymous':
        return {'updated': 0}
    conv_id = _conv_key(user['user_id'], other_user_id)
    now = now_utc()
    # Collect ids we're about to mark so we can notify sender.
    to_mark = await db.messages.find(
        {'conversation_id': conv_id, 'recipient_id': user['user_id'], 'read_at': None, 'deleted': {'$ne': True}},
        {'_id': 0, 'message_id': 1},
    ).to_list(1000)
    if not to_mark:
        return {'updated': 0}
    ids = [m['message_id'] for m in to_mark]
    r = await db.messages.update_many(
        {'message_id': {'$in': ids}},
        {'$set': {'read_at': now}},
    )
    # Notify sender in real-time that these were read.
    await _ws_send(other_user_id, {
        'type': 'message.read',
        'conversation_id': conv_id,
        'message_ids': ids,
        'read_at': _iso_utc(now),
    })
    return {'updated': r.modified_count}


@api_router.post('/messages/{message_id}/react')
async def react_message(message_id: str, body: ReactMessageBody, user: dict = Depends(get_current_user)):
    if user.get('is_anonymous') or user.get('auth_provider') == 'anonymous':
        raise HTTPException(status_code=403, detail="Non disponibile per utenti anonimi")
    m = await db.messages.find_one({'message_id': message_id}, {'_id': 0})
    if not m:
        raise HTTPException(status_code=404, detail='Messaggio non trovato')
    if user['user_id'] not in (m.get('sender_id'), m.get('recipient_id')):
        raise HTTPException(status_code=403, detail='Non autorizzato')
    reactions = dict(m.get('reactions') or {})
    if reactions.get(user['user_id']) == body.emoji:
        reactions.pop(user['user_id'], None)  # Toggle off if same emoji.
    else:
        reactions[user['user_id']] = body.emoji
    await db.messages.update_one({'message_id': message_id}, {'$set': {'reactions': reactions}})
    m['reactions'] = reactions
    payload = _serialize_message(m)
    # Notify both sides
    await _ws_send(m['sender_id'], {'type': 'message.reaction', 'message': payload})
    if m['recipient_id'] != m['sender_id']:
        await _ws_send(m['recipient_id'], {'type': 'message.reaction', 'message': payload})
    return {'message': payload}


@api_router.delete('/messages/{message_id}')
async def delete_message(message_id: str, user: dict = Depends(get_current_user)):
    m = await db.messages.find_one({'message_id': message_id}, {'_id': 0})
    if not m:
        raise HTTPException(status_code=404, detail='Messaggio non trovato')
    if m.get('sender_id') != user['user_id']:
        raise HTTPException(status_code=403, detail='Puoi cancellare solo i tuoi messaggi')
    await db.messages.update_one(
        {'message_id': message_id},
        {'$set': {'deleted': True, 'text': None, 'image_data': None, 'reactions': {}}},
    )
    m['deleted'] = True
    m['text'] = None
    m['image_data'] = None
    m['reactions'] = {}
    payload = _serialize_message(m)
    await _ws_send(m['sender_id'], {'type': 'message.deleted', 'message': payload})
    if m['recipient_id'] != m['sender_id']:
        await _ws_send(m['recipient_id'], {'type': 'message.deleted', 'message': payload})
    return {'ok': True}


# --- Block / Report -----------------------------------------------------------
@api_router.post('/users/{user_id}/block')
async def block_user(user_id: str, user: dict = Depends(get_current_user)):
    if user_id == user['user_id']:
        raise HTTPException(status_code=400, detail='Non puoi bloccare te stesso')
    target = await db.users.find_one({'user_id': user_id}, {'_id': 0, 'user_id': 1})
    if not target:
        raise HTTPException(status_code=404, detail='Utente non trovato')
    await db.user_blocks.update_one(
        {'blocker_id': user['user_id'], 'blocked_id': user_id},
        {'$setOnInsert': {'blocker_id': user['user_id'], 'blocked_id': user_id, 'created_at': now_utc()}},
        upsert=True,
    )
    return {'ok': True, 'blocked': True}


@api_router.delete('/users/{user_id}/block')
async def unblock_user(user_id: str, user: dict = Depends(get_current_user)):
    await db.user_blocks.delete_one({'blocker_id': user['user_id'], 'blocked_id': user_id})
    return {'ok': True, 'blocked': False}


@api_router.get('/users/me/blocks')
async def my_blocks(user: dict = Depends(get_current_user)):
    docs = await db.user_blocks.find({'blocker_id': user['user_id']}, {'_id': 0}).to_list(500)
    users = []
    for d in docs:
        mini = await _mini_user(d['blocked_id'])
        users.append({**mini, 'blocked_at': _iso_utc(d['created_at']) if d.get('created_at') else None})
    return {'blocked_users': users}


@api_router.post('/users/{user_id}/report')
async def report_user(user_id: str, body: ReportUserBody, user: dict = Depends(get_current_user)):
    if user_id == user['user_id']:
        raise HTTPException(status_code=400, detail='Non puoi segnalare te stesso')
    target = await db.users.find_one({'user_id': user_id}, {'_id': 0, 'user_id': 1})
    if not target:
        raise HTTPException(status_code=404, detail='Utente non trovato')
    await db.user_reports.insert_one({
        'report_id': new_id('rep'),
        'reporter_id': user['user_id'],
        'reported_id': user_id,
        'reason': body.reason[:500],
        'message_id': body.message_id,
        'created_at': now_utc(),
    })
    return {'ok': True}


# --- WebSocket endpoint -------------------------------------------------------
@app.websocket("/api/ws/messages")
async def ws_messages(ws: WebSocket, token: str = Query(default="")):
    await ws.accept()
    # Authenticate: first try JWT, then session token.
    uid = decode_jwt(token) if token else None
    if not uid:
        if token:
            sess = await db.user_sessions.find_one({'session_token': token}, {'_id': 0})
            if sess:
                uid = sess.get('user_id')
    if not uid:
        await ws.close(code=4401)
        return
    user = await db.users.find_one({'user_id': uid}, {'_id': 0, 'user_id': 1, 'auth_provider': 1, 'is_anonymous': 1})
    if not user or user.get('auth_provider') == 'anonymous' or user.get('is_anonymous'):
        await ws.close(code=4403)
        return
    WS_CLIENTS.setdefault(uid, set()).add(ws)
    try:
        await ws.send_json({'type': 'hello', 'user_id': uid})
        while True:
            # We use the socket unidirectionally (server → client), but read
            # to detect close/keepalive pings from the client.
            msg = await ws.receive_text()
            if msg == 'ping':
                try:
                    await ws.send_text('pong')
                except Exception:
                    break
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.warning(f"ws disconnect ({uid}): {e}")
    finally:
        WS_CLIENTS.get(uid, set()).discard(ws)


# ────────── Cerchia del Gossip (friend circle) ──────────
#
# One-way "circle" à la Google+ / Instagram-following. When user A adds
# user B to their circle, that pairing is stored as a single directional
# `friendships` doc. Users can toggle the whole circle to private so
# visitors see a "cerchia privata" state instead of the member list.
# Limit: 45 members per user (raises 409 with a friendly message).

MAX_CIRCLE_MEMBERS = 45

# ────────── Terms & Privacy Policy ──────────
# Bump `TERMS_VERSION` whenever the document materially changes; users
# whose stored acceptance version doesn't match will be prompted to
# re-accept the new copy.
TERMS_VERSION = 'v1'
_TERMS_PATH = ROOT_DIR / 'legal' / f'terms_{TERMS_VERSION}.md'
_TERMS_CACHE: dict = {'text': None}


def _load_terms_text() -> str:
    """Read and memoise the current terms markdown from disk.

    A miss cache re-reads the file on next call so a fresh deployment
    picks up new content without a server restart if the version key
    was left unchanged.
    """
    cached = _TERMS_CACHE.get('text')
    if cached:
        return cached
    try:
        text = _TERMS_PATH.read_text(encoding='utf-8')
    except Exception as e:
        logger.warning(f"terms file not readable at {_TERMS_PATH}: {e}")
        text = "Termini di Servizio non disponibili al momento. Riprova più tardi."
    _TERMS_CACHE['text'] = text
    return text


@api_router.get('/legal/terms')
async def get_legal_terms():
    """Return the current Terms of Service + Privacy Policy in markdown.

    Public endpoint: usable both during onboarding (the mandatory
    acceptance screen) and later from Settings.
    """
    return {
        'version': TERMS_VERSION,
        'text': _load_terms_text(),
        'updated_at': '2026-06-01',
    }


@api_router.post('/users/me/accept-terms')
async def accept_legal_terms(body: dict = Body(default_factory=dict), user: dict = Depends(get_current_user)):
    """Record that the current user has accepted the specified terms
    version. Client sends `{version: "v1"}`; we accept exact match and
    stamp the acceptance timestamp on the user document.
    """
    version = (body or {}).get('version') if isinstance(body, dict) else None
    if version != TERMS_VERSION:
        raise HTTPException(status_code=400, detail=f'Versione termini non valida (attesa {TERMS_VERSION})')
    now = now_utc()
    await db.users.update_one(
        {'user_id': user['user_id']},
        {'$set': {
            'terms_accepted_version': TERMS_VERSION,
            'terms_accepted_at': now,
        }},
    )
    return {
        'terms_accepted': True,
        'terms_accepted_version': TERMS_VERSION,
        'terms_accepted_at': _iso_utc(now),
    }


async def _circle_count(uid: str) -> int:
    return await db.friendships.count_documents({'user_id': uid})


async def _circle_is_private(uid: str) -> bool:
    u = await db.users.find_one({'user_id': uid}, {'_id': 0, 'circle_private': 1})
    return bool(u and u.get('circle_private'))


async def _last_interaction_ts(a: str, b: str) -> datetime:
    """Best-effort recency signal for ordering circle members.

    Uses the most-recent private message exchanged in either direction.
    Falls back to the friendship creation timestamp when there is no
    message history yet.
    """
    m = await db.messages.find_one(
        {
            '$or': [
                {'sender_id': a, 'recipient_id': b},
                {'sender_id': b, 'recipient_id': a},
            ],
            'deleted': {'$ne': True},
        },
        sort=[('created_at', -1)],
        projection={'_id': 0, 'created_at': 1},
    )
    if m and m.get('created_at'):
        return m['created_at']
    fr = await db.friendships.find_one(
        {'user_id': a, 'friend_id': b},
        {'_id': 0, 'created_at': 1},
    )
    return (fr or {}).get('created_at') or datetime.min.replace(tzinfo=timezone.utc)


async def _hydrate_circle(uids: list[str], q: str = '') -> list[dict]:
    """Turn a list of user_ids into circle rows sorted by recency.

    Applies the optional case-insensitive nickname filter `q` BEFORE the
    recency sort so ordering stays deterministic within a search result.
    """
    if not uids:
        return []
    users = await db.users.find(
        {'user_id': {'$in': uids}},
        {'_id': 0, 'user_id': 1, 'nickname': 1, 'display_name': 1, 'primary_photo_id': 1, 'auth_provider': 1, 'is_anonymous': 1},
    ).to_list(len(uids))
    if q:
        needle = q.lower()
        users = [u for u in users if needle in (u.get('nickname') or '').lower() or needle in (u.get('display_name') or '').lower()]
    return users


@api_router.post('/circle/{friend_id}')
async def circle_add(friend_id: str, user: dict = Depends(get_current_user)):
    if user.get('is_anonymous') or user.get('auth_provider') == 'anonymous':
        raise HTTPException(status_code=403, detail='Gli account anonimi non possono usare la cerchia')
    me = user['user_id']
    if friend_id == me:
        raise HTTPException(status_code=400, detail='Non puoi aggiungere te stesso')
    target = await db.users.find_one({'user_id': friend_id}, {'_id': 0, 'user_id': 1, 'is_anonymous': 1, 'auth_provider': 1})
    if not target:
        raise HTTPException(status_code=404, detail='Utente non trovato')
    if target.get('is_anonymous') or target.get('auth_provider') == 'anonymous':
        raise HTTPException(status_code=400, detail='Non puoi aggiungere un utente anonimo')
    # Block guard both directions.
    b1 = await db.user_blocks.count_documents({'blocker_id': me, 'blocked_id': friend_id})
    b2 = await db.user_blocks.count_documents({'blocker_id': friend_id, 'blocked_id': me})
    if b1 or b2:
        raise HTTPException(status_code=403, detail='Impossibile aggiungere questo utente')
    existing = await db.friendships.find_one({'user_id': me, 'friend_id': friend_id})
    if existing:
        return {'ok': True, 'in_circle': True, 'already': True, 'count': await _circle_count(me)}
    current = await _circle_count(me)
    if current >= MAX_CIRCLE_MEMBERS:
        raise HTTPException(
            status_code=409,
            detail=f'La tua cerchia è piena ({MAX_CIRCLE_MEMBERS} amici). Rimuovi qualcuno prima di aggiungerne altri.',
        )
    await db.friendships.insert_one({
        'friendship_id': new_id('fri'),
        'user_id': me,
        'friend_id': friend_id,
        'created_at': now_utc(),
    })
    return {'ok': True, 'in_circle': True, 'count': current + 1}


@api_router.delete('/circle/{friend_id}')
async def circle_remove(friend_id: str, user: dict = Depends(get_current_user)):
    me = user['user_id']
    r = await db.friendships.delete_one({'user_id': me, 'friend_id': friend_id})
    return {'ok': True, 'in_circle': False, 'removed': r.deleted_count, 'count': await _circle_count(me)}


@api_router.patch('/circle/me/privacy')
async def circle_set_privacy(body: dict, user: dict = Depends(get_current_user)):
    if user.get('is_anonymous') or user.get('auth_provider') == 'anonymous':
        raise HTTPException(status_code=403, detail='Non disponibile per account anonimi')
    private = bool(body.get('private', False))
    await db.users.update_one({'user_id': user['user_id']}, {'$set': {'circle_private': private}})
    return {'ok': True, 'private': private}


@api_router.get('/circle/me/status/{other_user_id}')
async def circle_status(other_user_id: str, user: dict = Depends(get_current_user)):
    """Returns whether the target user is already in my circle. Cheap check
    used by the "Aggiungi/Rimuovi" toggle on the external-profile screen."""
    me = user['user_id']
    in_circle = await db.friendships.count_documents({'user_id': me, 'friend_id': other_user_id}) > 0
    return {'in_circle': in_circle, 'count': await _circle_count(me), 'max': MAX_CIRCLE_MEMBERS}


@api_router.get('/users/{owner_id}/circle')
async def get_circle(owner_id: str, q: str = '', user: dict = Depends(get_current_user)):
    """Return the circle of `owner_id`. Respects privacy: if the owner
    made their circle private, only the owner themselves can read the
    member list.

    Sort order (from the viewer's point of view):
      1. The viewer themselves, if they are a member (own row surfaced
         first when browsing someone else's circle).
      2. Members that are ALSO in the viewer's own circle, ordered by
         the viewer's most-recent interaction with them.
      3. Everyone else, ordered by the viewer's most-recent interaction.
    """
    me = user['user_id']
    is_owner = owner_id == me
    if not is_owner:
        # Non-owner: check privacy + basic user exists.
        target = await db.users.find_one({'user_id': owner_id}, {'_id': 0, 'user_id': 1, 'circle_private': 1})
        if not target:
            raise HTTPException(status_code=404, detail='Utente non trovato')
        if target.get('circle_private'):
            return {'private': True, 'count': await _circle_count(owner_id), 'max': MAX_CIRCLE_MEMBERS, 'members': [], 'is_owner': False}
    rows = await db.friendships.find({'user_id': owner_id}, {'_id': 0, 'friend_id': 1, 'created_at': 1}).to_list(200)
    uids = [r['friend_id'] for r in rows]
    users = await _hydrate_circle(uids, q.strip())

    # Which of those members are also inside the VIEWER's own circle?
    # Single indexed lookup keeps this O(1) roundtrip regardless of size.
    my_circle_ids: set[str] = set()
    if uids:
        my_rows = await db.friendships.find(
            {'user_id': me, 'friend_id': {'$in': [u['user_id'] for u in users]}},
            {'_id': 0, 'friend_id': 1},
        ).to_list(len(users))
        my_circle_ids = {r['friend_id'] for r in my_rows}

    # Compute (bucket, -recency) sort keys. Bucket 0 = viewer themselves,
    # 1 = mutual (in viewer's circle), 2 = everyone else. Recency is the
    # last interaction between VIEWER and that member so the ordering
    # reflects the viewer's own connections regardless of whose circle
    # we're looking at.
    triples = []
    for u in users:
        uid = u['user_id']
        if uid == me:
            bucket = 0
        elif uid in my_circle_ids:
            bucket = 1
        else:
            bucket = 2
        ts = await _last_interaction_ts(me, uid)
        triples.append((bucket, -ts.timestamp() if ts else 0, u))
    triples.sort(key=lambda x: (x[0], x[1]))

    # Attach mini_user photo_data + viewer-relative flags so the UI can
    # render "AGGIUNGI/NELLA CERCHIA" without a per-row status call.
    hydrated = []
    for _, _, u in triples:
        uid = u['user_id']
        mini = await _mini_user(uid)
        hydrated.append({
            **mini,
            'display_name': u.get('display_name'),
            'is_me': uid == me,
            'in_my_circle': uid in my_circle_ids,
        })

    return {
        'private': False,
        'is_owner': is_owner,
        'count': len(rows),
        'max': MAX_CIRCLE_MEMBERS,
        'members': hydrated,
    }




app.include_router(api_router)

app.add_middleware(
    CORSMiddleware, allow_credentials=True, allow_origins=['*'],
    allow_methods=['*'], allow_headers=['*'],
)


@app.on_event('shutdown')
async def shutdown_db_client():
    client.close()
