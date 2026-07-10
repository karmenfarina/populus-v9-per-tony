from fastapi import FastAPI, APIRouter, Header, HTTPException, Depends
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
from pathlib import Path
from pydantic import BaseModel, Field, EmailStr
from typing import List, Optional, Literal
from datetime import datetime, timezone, timedelta

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

mongo_url = os.environ['MONGO_URL']
client = AsyncIOMotorClient(mongo_url)
db = client[os.environ['DB_NAME']]

JWT_SECRET = os.environ.get('JWT_SECRET', 'dev-secret-change')
JWT_ALG = 'HS256'
JWT_TTL_DAYS = 7
EMERGENT_LLM_KEY = os.environ.get('EMERGENT_LLM_KEY')

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


class VoteBody(BaseModel):
    side: Literal['A', 'B']


class CommentBody(BaseModel):
    text: str = Field(min_length=1, max_length=500)


class ReplyBody(BaseModel):
    text: str = Field(min_length=1, max_length=500)


def compute_badge(u: dict) -> Optional[dict]:
    total = u.get('total_votes', 0)
    if total < 5:
        return {'unlocked': False, 'progress': total, 'target': 5, 'label': 'Continua a votare per sbloccare la spilla'}
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
]


@api_router.get('/categories')
async def get_categories():
    return {'categories': CATEGORIES}


def _attach_percentages(d: dict):
    a = d.get('votes_a', 0)
    b = d.get('votes_b', 0)
    total = a + b
    d['total_votes'] = total
    d['pct_a'] = round(100 * a / total) if total else 50
    d['pct_b'] = 100 - d['pct_a'] if total else 50


@api_router.get('/feuds')
async def list_feuds(category: Optional[str] = None):
    q = {}
    if category and category != 'all':
        q['category'] = category
    docs = await db.feuds.find(q, {'_id': 0}).sort('created_at', -1).to_list(200)
    for d in docs:
        _attach_percentages(d)
    return {'feuds': docs}


@api_router.get('/feuds/{feud_id}')
async def get_feud(feud_id: str, user: Optional[dict] = Depends(get_current_user_optional)):
    doc = await db.feuds.find_one({'feud_id': feud_id}, {'_id': 0})
    if not doc:
        raise HTTPException(status_code=404, detail='Faida non trovata')
    _attach_percentages(doc)
    if user:
        vote = await db.votes.find_one({'feud_id': feud_id, 'user_id': user['user_id']}, {'_id': 0})
        doc['my_vote'] = vote.get('side') if vote else None
    else:
        doc['my_vote'] = None
    return {'feud': doc}


async def _recompute_user_alignment(user_id: str):
    votes = await db.votes.find({'user_id': user_id}, {'_id': 0}).to_list(1000)
    maj = 0
    minr = 0
    for v in votes:
        feud = await db.feuds.find_one({'feud_id': v['feud_id']}, {'_id': 0})
        if not feud:
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
    })
    inc_field = 'votes_a' if body.side == 'A' else 'votes_b'
    await db.feuds.update_one({'feud_id': feud_id}, {'$inc': {inc_field: 1}})
    await _recompute_user_alignment(user['user_id'])
    updated = await db.feuds.find_one({'feud_id': feud_id}, {'_id': 0})
    _attach_percentages(updated)
    updated['my_vote'] = body.side
    return {'feud': updated}


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
    doc = {
        'comment_id': new_id('cmt'), 'feud_id': feud_id, 'user_id': user['user_id'],
        'nickname': user.get('nickname'), 'side': vote['side'], 'text': body.text.strip(),
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
    doc = {
        'reply_id': new_id('rep'), 'comment_id': comment_id, 'feud_id': parent['feud_id'],
        'user_id': user['user_id'], 'nickname': user.get('nickname'), 'side': side,
        'text': body.text.strip(), 'created_at': now_utc(),
    }
    await db.replies.insert_one(doc)
    doc.pop('_id', None)
    doc['created_at'] = doc['created_at'].isoformat()
    return {'reply': doc}


def _image_for_category(cat_id: str) -> str:
    mapping = {
        'politica': 'https://images.unsplash.com/photo-1541872703-74c5e44368f6?w=1200',
        'tv': 'https://images.unsplash.com/photo-1585951237318-9ea5e175b891?w=1200',
        'musica': 'https://images.unsplash.com/photo-1493225457124-a3eb161ffa5f?w=1200',
        'sport': 'https://images.unsplash.com/photo-1508098682722-e99c43a406b2?w=1200',
        'cinema': 'https://images.unsplash.com/photo-1489599849927-2ee91cede3ba?w=1200',
        'social': 'https://images.unsplash.com/photo-1611162617213-7d7a39e9b1d7?w=1200',
        'gossip': 'https://images.unsplash.com/photo-1561890244-e880c1e6d54e?w=1200',
    }
    return mapping.get(cat_id, mapping['gossip'])


@api_router.post('/admin/generate-daily')
async def generate_daily(count: int = 3):
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
                _attach_percentages(feud)
                feud['created_at'] = feud['created_at'].isoformat()
                created.append(feud)
        except Exception as e:
            logger.warning(f"AI generation failed for {cat['id']}: {e}")
    return {'created': created}


async def _generate_feud_for_category(cat: dict, LlmChat, UserMessage) -> Optional[dict]:
    chat = LlmChat(
        api_key=EMERGENT_LLM_KEY,
        session_id=f"gen-{cat['id']}-{uuid.uuid4().hex[:6]}",
        system_message=(
            "Sei un editor italiano che crea faide/controversie dal mondo del gossip, tv, politica, musica, sport, cinema e social. "
            "Restituisci SOLO JSON valido, in italiano, senza commenti."
        ),
    ).with_model('anthropic', 'claude-sonnet-4-6')

    prompt = (
        f"Genera una faida attuale nella categoria '{cat['label']}'. "
        "Restituisci un JSON con questi campi esatti: "
        '{"title": "titolo tabloid massimo 60 caratteri", '
        '"party_a": "nome primo contendente", '
        '"party_b": "nome secondo contendente", '
        '"summary": "spiegazione della controversia in 3-4 frasi, aggiornata", '
        '"question": "domanda al lettore, con chi ti schieri?"}'
    )
    text = await chat.send_message(UserMessage(text=prompt))
    match = re.search(r'\{[\s\S]*\}', text)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except Exception:
        return None
    return {
        'feud_id': new_id('feud'),
        'category': cat['id'], 'category_label': cat['label'],
        'title': (data.get('title') or 'Faida senza titolo')[:120],
        'party_a': (data.get('party_a') or 'Team A')[:60],
        'party_b': (data.get('party_b') or 'Team B')[:60],
        'summary': data.get('summary') or '',
        'question': data.get('question') or 'Con chi ti schieri?',
        'image_url': _image_for_category(cat['id']),
        'votes_a': 0, 'votes_b': 0, 'created_at': now_utc(), 'source': 'ai',
    }


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
    await seed_if_empty()


@api_router.get('/')
async def root():
    return {'message': 'App di faide gossip API', 'ok': True}


app.include_router(api_router)

app.add_middleware(
    CORSMiddleware, allow_credentials=True, allow_origins=['*'],
    allow_methods=['*'], allow_headers=['*'],
)


@app.on_event('shutdown')
async def shutdown_db_client():
    client.close()
