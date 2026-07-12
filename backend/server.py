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
            d['created_at'] = d['created_at'].isoformat()
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
            p['created_at'] = p['created_at'].isoformat()
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
    docs = await db.feuds.find(q, {'_id': 0}).sort('created_at', -1).to_list(200)
    voted_map: dict = {}
    if user and docs:
        voted_map = await _user_voted_ids(user['user_id'], [d['feud_id'] for d in docs])
    for d in docs:
        my_vote = voted_map.get(d['feud_id']) if user else None
        _attach_percentages(d, revealed=bool(my_vote))
        d['my_vote'] = my_vote
    return {'feuds': docs}


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
        # Always reveal on archive — voting closed, results public.
        _attach_percentages(d, revealed=True)
        d['my_vote'] = my_vote
        d['archived'] = True
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
        doc['created_at'] = doc['created_at'].isoformat()
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
    if user:
        vote = await db.votes.find_one({'feud_id': feud_id, 'user_id': user['user_id']}, {'_id': 0})
        my_vote = vote.get('side') if vote else None
    _attach_percentages(doc, revealed=bool(my_vote))
    doc['my_vote'] = my_vote
    return {'feud': doc}


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
        {'$set': {'majority_votes': maj, 'minority_votes': minr, 'total_votes': maj + minr}},
    )


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
_RSS_TTL_SECONDS = 30 * 60  # 30 minutes


@api_router.post('/feuds/{feud_id}/vote')
async def vote_feud(feud_id: str, body: VoteBody, user: dict = Depends(get_current_user)):
    feud = await db.feuds.find_one({'feud_id': feud_id}, {'_id': 0})
    if not feud:
        raise HTTPException(status_code=404, detail='Faida non trovata')
    existing = await db.votes.find_one({'feud_id': feud_id, 'user_id': user['user_id']}, {'_id': 0})
    if existing:
        raise HTTPException(status_code=400, detail='Hai già votato')
    await db.votes.insert_one({
        'vote_id': new_id('vote'), 'feud_id': feud_id, 'user_id': user['user_id'],
        'side': body.side, 'created_at': now_utc(),
        # Denormalized snapshot — allows history to render feud preview even after
        # the feud is purged from `feuds` (retention: 14 days).
        'feud_snapshot': {
            'title': feud.get('title'),
            'category_label': feud.get('category_label'),
            'party_a': feud.get('party_a'),
            'party_b': feud.get('party_b'),
            'image_url': feud.get('image_url'),
        },
    })
    inc_field = 'votes_a' if body.side == 'A' else 'votes_b'
    await db.feuds.update_one({'feud_id': feud_id}, {'$inc': {inc_field: 1}})
    await _recompute_user_alignment(user['user_id'])
    updated = await db.feuds.find_one({'feud_id': feud_id}, {'_id': 0})
    _attach_percentages(updated, revealed=True)
    updated['my_vote'] = body.side
    return {'feud': updated}


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
        'voted_at': v['created_at'].isoformat() if isinstance(v['created_at'], datetime) else v['created_at'],
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
    for c in docs:
        c['reply_count'] = await db.replies.count_documents({'comment_id': c['comment_id']})
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
    doc['created_at'] = doc['created_at'].isoformat()
    return {'comment': doc}


@api_router.get('/comments/{comment_id}/replies')
async def list_replies(comment_id: str):
    docs = await db.replies.find({'comment_id': comment_id}, {'_id': 0}).sort('created_at', 1).to_list(500)
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
    doc['created_at'] = doc['created_at'].isoformat()
    return {'reply': doc}


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
                feud['created_at'] = feud['created_at'].isoformat()
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
    # Fetch a wider pool of real news headlines so the AI has room to pick the juiciest
    headlines = await _fetch_headlines_for_category(cat['id'], max_items=12)

    chat = LlmChat(
        api_key=EMERGENT_LLM_KEY,
        session_id=f"gen-{cat['id']}-{uuid.uuid4().hex[:6]}",
        system_message=(
            "Sei un editor italiano cinico e affilato, tipo tabloid, che trasforma notizie reali "
            "in FAIDE — controversie a due parti su cui la gente si accalora. "
            "Il tuo unico criterio è l'engagement: la notizia scelta deve provocare reazioni "
            "emotive forti (rabbia, indignazione, ironia, gossip, tifo), dividere il pubblico in due, "
            "e far venir voglia di commentare. Evita come la peste notizie tecniche, burocratiche, "
            "adempimenti, dati economici astratti, dichiarazioni istituzionali generiche, "
            "necrologi, cronaca meteo, o eventi che non hanno due parti chiaramente contrapposte. "
            "Restituisci SOLO JSON valido, in italiano, senza commenti e senza testo extra."
        ),
    ).with_model('anthropic', 'claude-sonnet-4-6')

    if headlines:
        sources_block = "\n".join([f"[{i}] {h['title']} — fonte: {h['source']}" for i, h in enumerate(headlines)])
        prompt = (
            f"Categoria: {cat['label']}.\n\n"
            f"POOL DI NOTIZIE REALI DI OGGI:\n{sources_block}\n\n"
            "COMPITO: scegli LA notizia che ha il coefficiente di engagement più alto. "
            "Criteri, in ordine di importanza:\n"
            "1. Deve avere DUE parti chiaramente contrapposte (persone, gruppi, opinioni, tifoserie).\n"
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
            "'attacca', 'smaschera', 'accusa', 'insulta', 'gela', 'demolisce'), max 90 caratteri. "
            "Ma tutti i fatti, i nomi e i dettagli DEVONO derivare dalla notizia scelta, non da tua fantasia.\n"
            "Le due parti devono essere nomi propri o gruppi riconoscibili citati nella notizia.\n"
            "La domanda finale deve essere provocatoria e schierante.\n\n"
            "Rispondi SOLO con questo JSON:\n"
            '{"title": "titolo tabloid max 90 caratteri", '
            '"party_a": "primo contendente riconoscibile citato nella notizia", '
            '"party_b": "secondo contendente riconoscibile citato nella notizia", '
            '"summary": "3-4 frasi che spiegano la faida partendo dalla notizia scelta, con dettagli concreti presi dalla notizia, senza inventare", '
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
    for i, h in enumerate(headlines[:6]):
        if i != idx and h not in sources and len(sources) < 3:
            sources.append(h)

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
        'votes_a': 0, 'votes_b': 0, 'created_at': now_utc(), 'source': 'ai',
    }


# ----------------------- RSS News Ingestion -----------------------

RSS_FEEDS: dict = {
    'politica': [
        ('Repubblica Politica', 'https://www.repubblica.it/rss/politica/rss2.0.xml'),
        ('ANSA Politica', 'https://www.ansa.it/sito/notizie/politica/politica_rss.xml'),
        ('Il Fatto Quotidiano', 'https://www.ilfattoquotidiano.it/politica/feed/'),
        ('Corriere Politica', 'https://xml2.corriereobjects.it/rss/politica.xml'),
    ],
    'tv': [
        ('TvBlog', 'https://www.tvblog.it/feed'),
        ('ANSA Spettacolo', 'https://www.ansa.it/sito/notizie/cultura/cinema/cinema_rss.xml'),
    ],
    'musica': [
        ('Rolling Stone Italia', 'https://www.rollingstone.it/feed/'),
        ('AllMusicItalia', 'https://www.allmusicitalia.it/feed'),
    ],
    'sport': [
        ('Gazzetta', 'https://www.gazzetta.it/rss/homepage.xml'),
        ('ANSA Sport', 'https://www.ansa.it/sito/notizie/sport/sport_rss.xml'),
        ('Tuttosport', 'https://www.tuttosport.com/rss/calcio-serie-a.xml'),
    ],
    'cinema': [
        ('BadTaste', 'https://www.badtaste.it/feed/'),
        ('ANSA Cinema', 'https://www.ansa.it/sito/notizie/cultura/cinema/cinema_rss.xml'),
    ],
    'social': [
        ('DDay', 'https://www.dday.it/rss'),
        ('GossipeTV', 'https://www.gossipetv.com/feed'),
    ],
    'gossip': [
        ('Novella 2000', 'https://www.novella2000.it/feed/'),
        ('GossipeTV', 'https://www.gossipetv.com/feed'),
    ],
    'tech': [
        ('HDblog', 'https://www.hdblog.it/rss/'),
        ('DDay', 'https://www.dday.it/rss'),
        ("Tom's Hardware Italia", 'https://www.tomshw.it/feed/'),
        ('SmartWorld', 'https://www.smartworld.it/feed'),
        ('Everyeye Tech', 'https://www.everyeye.it/rss/tech.xml'),
    ],
}


async def _fetch_headlines_for_category(cat_id: str, max_items: int = 6) -> List[dict]:
    # Cache hit
    entry = _RSS_CACHE.get(cat_id)
    if entry and entry[0] > time.time():
        return entry[1][:max_items]

    feeds = RSS_FEEDS.get(cat_id, [])
    if not feeds:
        return []
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
    """Every hour, ensure each category has at least one fresh feud from the last 24 hours.
    Skip generation for categories that already have a fresh feud. This uses the RSS+dedup
    logic so no duplicate stories will be inserted."""
    import asyncio as _asyncio
    while True:
        try:
            try:
                from emergentintegrations.llm.chat import LlmChat, UserMessage
            except Exception as e:
                logger.warning(f"scheduler LLM import failed: {e}")
                await _asyncio.sleep(3600)
                continue

            one_day_ago = now_utc() - timedelta(hours=24)
            for cat in CATEGORIES:
                try:
                    fresh = await db.feuds.find_one(
                        {'category': cat['id'], 'source': 'ai', 'created_at': {'$gte': one_day_ago}},
                        {'_id': 0, 'feud_id': 1},
                    )
                    if fresh:
                        continue
                    logger.info(f"scheduler: generating fresh feud for {cat['id']}")
                    feud = await _generate_feud_for_category(cat, LlmChat, UserMessage)
                    if feud:
                        await db.feuds.insert_one(feud)
                        logger.info(f"scheduler: inserted feud for {cat['id']}")
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
        await _asyncio.sleep(3600)  # check hourly


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
