from fastapi import FastAPI, APIRouter, Header, HTTPException, Depends, Request
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


async def require_admin(x_admin_key: Optional[str] = Header(None, alias='X-Admin-Key')) -> bool:
    if not ADMIN_TOKEN or not x_admin_key or x_admin_key != ADMIN_TOKEN:
        raise HTTPException(status_code=401, detail='Chiave admin non valida')
    return True


class SignupBody(BaseModel):
    email: EmailStr
    password: str = Field(min_length=6)
    nickname: str = Field(min_length=2, max_length=24)


class LoginBody(BaseModel):
    email: EmailStr
    password: str


class AnonymousBody(BaseModel):
    nickname: str = Field(min_length=2, max_length=24)


class GoogleSessionBody(BaseModel):
    session_id: str


class ProfileBody(BaseModel):
    age: int = Field(ge=13, le=120)
    sex: Literal['F', 'M', 'other', 'na']
    region: str = Field(min_length=2, max_length=40)
    favorite_categories: List[str] = Field(min_length=1)


class DetailsBody(BaseModel):
    bio: Optional[str] = Field(default=None, max_length=200)
    social_links: Optional[dict] = None  # {instagram, tiktok, twitter, youtube, website}


class PhotoUploadBody(BaseModel):
    data: str = Field(min_length=40)  # base64 data (with or without prefix)


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
        'auth_provider': u.get('auth_provider'),
        'picture': u.get('picture'),
        'majority_votes': u.get('majority_votes', 0),
        'minority_votes': u.get('minority_votes', 0),
        'total_votes': u.get('total_votes', 0),
        'age': u.get('age'),
        'sex': u.get('sex'),
        'region': u.get('region'),
        'favorite_categories': u.get('favorite_categories', []),
        'onboarding_completed': bool(u.get('onboarding_completed', False)),
        'bio': u.get('bio'),
        'social_links': u.get('social_links', {}),
        'primary_photo_id': u.get('primary_photo_id'),
        'photos_count': u.get('photos_count', 0),
        'badge': compute_badge(u),
        'push_notifications': u.get('push_notifications', True),
    }


@api_router.post('/auth/signup')
async def signup(body: SignupBody):
    existing = await db.users.find_one({'email': body.email.lower()})
    if existing:
        raise HTTPException(status_code=400, detail='Email già registrata')
    user_id = new_id('user')
    user = {
        'user_id': user_id,
        'email': body.email.lower(),
        'nickname': body.nickname,
        'password_hash': hash_password(body.password),
        'auth_provider': 'email',
        'created_at': now_utc(),
        'majority_votes': 0, 'minority_votes': 0, 'total_votes': 0,
    }
    await db.users.insert_one(user)
    return {'token': make_jwt(user_id), 'user': _public_user(user)}


@api_router.post('/auth/login')
async def login(body: LoginBody):
    user = await db.users.find_one({'email': body.email.lower()}, {'_id': 0})
    if not user or user.get('auth_provider') != 'email':
        raise HTTPException(status_code=401, detail='Credenziali non valide')
    if not verify_password(body.password, user.get('password_hash', '')):
        raise HTTPException(status_code=401, detail='Credenziali non valide')
    return {'token': make_jwt(user['user_id']), 'user': _public_user(user)}


@api_router.post('/auth/anonymous')
async def anonymous(body: AnonymousBody):
    user_id = new_id('anon')
    user = {
        'user_id': user_id, 'email': None, 'nickname': body.nickname,
        'auth_provider': 'anonymous', 'created_at': now_utc(),
        'majority_votes': 0, 'minority_votes': 0, 'total_votes': 0,
        # Anonymous users skip onboarding and see all categories by default
        'onboarding_completed': True,
        'favorite_categories': [],
    }
    await db.users.insert_one(user)
    return {'token': make_jwt(user_id), 'user': _public_user(user)}


@api_router.post('/auth/google-session')
async def google_session(body: GoogleSessionBody):
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

    existing = await db.users.find_one({'email': email}, {'_id': 0})
    if existing:
        user_id = existing['user_id']
        user = existing
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


VALID_CATEGORY_IDS = {'politica', 'tv', 'musica', 'sport', 'cinema', 'social', 'gossip', 'tech'}
ITALIAN_REGIONS = {
    'Abruzzo', 'Basilicata', 'Calabria', 'Campania', 'Emilia-Romagna',
    'Friuli-Venezia Giulia', 'Lazio', 'Liguria', 'Lombardia', 'Marche',
    'Molise', 'Piemonte', 'Puglia', 'Sardegna', 'Sicilia', 'Toscana',
    "Trentino-Alto Adige", 'Umbria', "Valle d'Aosta", 'Veneto', 'Altro',
}


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
    await db.users.update_one(
        {'user_id': user['user_id']},
        {'$set': {
            'age': body.age,
            'sex': body.sex,
            'region': body.region,
            'favorite_categories': body.favorite_categories,
            'onboarding_completed': True,
            'profile_updated_at': now_utc(),
        }},
    )
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
    doc = {
        'photo_id': photo_id,
        'user_id': user['user_id'],
        'data': data,
        'position': current_count,
        'created_at': now_utc(),
    }
    await db.user_photos.insert_one(doc)
    updates: dict = {'photos_count': current_count + 1}
    if current_count == 0:
        updates['primary_photo_id'] = photo_id
    await db.users.update_one({'user_id': user['user_id']}, {'$set': updates})
    return {'photo_id': photo_id, 'primary_photo_id': updates.get('primary_photo_id', user.get('primary_photo_id'))}


@api_router.get('/auth/me/photos')
async def my_photos(user: dict = Depends(get_current_user)):
    docs = await db.user_photos.find(
        {'user_id': user['user_id']}, {'_id': 0}
    ).sort('position', 1).to_list(MAX_PHOTOS + 1)
    for d in docs:
        if isinstance(d.get('created_at'), datetime):
            d['created_at'] = _iso_utc(d['created_at'])
        d['is_primary'] = (d['photo_id'] == user.get('primary_photo_id'))
    return {'photos': docs, 'primary_photo_id': user.get('primary_photo_id')}


@api_router.patch('/auth/me/photos/{photo_id}/primary')
async def set_primary_photo(photo_id: str, user: dict = Depends(get_current_user)):
    _reject_if_anonymous(user)
    photo = await db.user_photos.find_one({'photo_id': photo_id, 'user_id': user['user_id']}, {'_id': 0})
    if not photo:
        raise HTTPException(status_code=404, detail='Foto non trovata')
    await db.users.update_one({'user_id': user['user_id']}, {'$set': {'primary_photo_id': photo_id}})
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
        {'user_id': user_id}, {'_id': 0, 'user_id': 0}
    ).sort('position', 1).to_list(MAX_PHOTOS + 1)
    for p in photos:
        if isinstance(p.get('created_at'), datetime):
            p['created_at'] = _iso_utc(p['created_at'])
    return {
        'user_id': u['user_id'],
        'nickname': u.get('nickname'),
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
async def public_user_history(user_id: str, filter: str = 'all'):
    u = await db.users.find_one({'user_id': user_id}, {'_id': 0, 'user_id': 1, 'auth_provider': 1})
    if not u:
        raise HTTPException(status_code=404, detail='Utente non trovato')
    if u.get('auth_provider') == 'anonymous':
        # Anonymous voting history is hidden from other users.
        return {'history': [], 'is_anonymous': True}
    return {'history': await _history_for_user(user_id, filter)}



@api_router.get('/feuds/{feud_id}/comments')
async def get_comments(feud_id: str):
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
    a = [c for c in docs if c['side'] == 'A']
    b = [c for c in docs if c['side'] == 'B']
    return {'side_a': a, 'side_b': b}


@api_router.post('/feuds/{feud_id}/comments')
async def add_comment(feud_id: str, body: CommentBody, user: dict = Depends(get_current_user)):
    vote = await db.votes.find_one({'feud_id': feud_id, 'user_id': user['user_id']}, {'_id': 0})
    if not vote:
        raise HTTPException(status_code=400, detail='Devi prima votare')
    clean_text, flagged = _moderate_text(body.text)
    if flagged:
        await _log_flagged(user['user_id'], feud_id, body.text, flagged)
        raise HTTPException(status_code=400, detail=f"Commento bloccato: contiene termini non consentiti ({', '.join(flagged)})")
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


app.include_router(api_router)

app.add_middleware(
    CORSMiddleware, allow_credentials=True, allow_origins=['*'],
    allow_methods=['*'], allow_headers=['*'],
)


@app.on_event('shutdown')
async def shutdown_db_client():
    client.close()
