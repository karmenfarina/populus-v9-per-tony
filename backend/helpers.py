"""
Small pure helpers extracted from server.py.

Everything here is stateless — no database, no logger, no globals from
the main server module. Safe to import from anywhere without risk of
circular deps. server.py re-imports each name so existing call sites
keep working unchanged.
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import List, Optional

import bcrypt


# --------------------------------------------------------------------
# Time helpers
# --------------------------------------------------------------------
def now_utc() -> datetime:
    from datetime import timezone
    return datetime.now(timezone.utc)


def iso_utc(dt) -> str:
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


# --------------------------------------------------------------------
# ID + password helpers
# --------------------------------------------------------------------
def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def hash_password(pw: str) -> str:
    return bcrypt.hashpw(pw.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')


def verify_password(pw: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(pw.encode('utf-8'), hashed.encode('utf-8'))
    except Exception:
        return False


# --------------------------------------------------------------------
# String helpers
# --------------------------------------------------------------------
def strip_data_url(s: str) -> str:
    if s.startswith('data:'):
        idx = s.find(',')
        if idx > 0:
            return s[idx + 1:]
    return s


def hashtag_norm(name: str) -> str:
    """Normalize a name to a compact alphanumeric slug."""
    return re.sub(r'[^a-zA-Z0-9]+', '', (name or '').strip().lower())


def extract_subject_from_title(title: str) -> str:
    """Fallback: derive a single-subject slug from the feud title by taking the
    leading proper-noun phrase (1-3 capitalized words)."""
    m = re.match(
        r"\s*([A-ZÀ-Ù][a-zà-ùÀ-Ù']+(?:\s+[A-ZÀ-Ù][a-zà-ùÀ-Ù']+){0,2})",
        (title or '').strip(),
    )
    return m.group(1) if m else ''


def is_stance_party(name: str) -> bool:
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
        'anti-', 'anti ', 'pro-', 'pro ', 'fan di', 'contro ',
    )
    return any(lc.startswith(p) for p in STANCE_PREFIXES)


# Italian articles/prepositions/connectives that vary between feuds. Stripping
# them lets "il Milan" and "Milan" collapse to the same hashtag bucket.
HASHTAG_STOPWORDS = {
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


def clean_subject(name: str) -> str:
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
        if w.lower() in HASHTAG_STOPWORDS:
            continue
        kept.append(w)
    if not kept:
        return ''
    # PascalCase each token
    return ''.join(w[0].upper() + w[1:].lower() for w in kept)[:40]
