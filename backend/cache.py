"""
In-process TTL cache for high-frequency read endpoints.
=======================================================

Volutamente semplice: dict + lock async per invalidazione atomica.
Non serve Redis su un singolo pod — se in futuro si passa a
multi-replica, sostituire l'implementazione mantenendo l'interfaccia:

    await cache.get_or_set(key, ttl_seconds, coroutine_factory)
    cache.invalidate(key)
    cache.invalidate_prefix(prefix)

Note:
- Ogni entry ha `(value, expires_at)`. Espirazione lazy (nessun
  garbage collector background: la cache si autoripulisce quando la
  key viene riletta).
- `stampede protection`: se due request chiedono la stessa key in
  contemporanea (miss), la seconda aspetta la prima invece di
  duplicare la query DB/LLM.
- Statistiche esposte via `stats()` per debugging/admin.
"""
from __future__ import annotations
import asyncio
import time
from typing import Any, Awaitable, Callable, Dict, Optional, Tuple

_store: Dict[str, Tuple[float, Any]] = {}
_inflight: Dict[str, asyncio.Future] = {}
_lock = asyncio.Lock()

_hits = 0
_misses = 0


def _now() -> float:
    return time.monotonic()


async def get_or_set(
    key: str,
    ttl_seconds: float,
    factory: Callable[[], Awaitable[Any]],
) -> Any:
    """Ritorna il valore in cache oppure lo calcola con `factory()` e lo memorizza per `ttl_seconds`.

    Protetto contro thundering herd: se una richiesta è già in volo per la
    stessa key, le altre attendono lo stesso Future invece di rieseguire
    `factory`.
    """
    global _hits, _misses
    entry = _store.get(key)
    if entry is not None:
        expires_at, value = entry
        if expires_at > _now():
            _hits += 1
            return value
        # Scaduto: rimuoviamo pigramente
        _store.pop(key, None)

    # Verifica se una request è già in corso
    async with _lock:
        entry = _store.get(key)
        if entry is not None and entry[0] > _now():
            _hits += 1
            return entry[1]
        inflight = _inflight.get(key)
        if inflight is None:
            loop = asyncio.get_event_loop()
            inflight = loop.create_future()
            _inflight[key] = inflight
            owner = True
        else:
            owner = False

    if not owner:
        # Sub-waiter: aspetta il risultato del proprietario
        try:
            return await inflight
        except Exception:
            # Se il proprietario ha fallito, rilancia
            raise

    # Owner: calcola valore
    _misses += 1
    try:
        value = await factory()
        _store[key] = (_now() + max(0.1, float(ttl_seconds)), value)
        if not inflight.done():
            inflight.set_result(value)
        return value
    except Exception as e:
        if not inflight.done():
            inflight.set_exception(e)
        raise
    finally:
        _inflight.pop(key, None)


def invalidate(key: str) -> None:
    _store.pop(key, None)


def invalidate_prefix(prefix: str) -> int:
    """Rimuove tutte le entry con key che comincia per `prefix`. Ritorna il conteggio."""
    keys = [k for k in _store.keys() if k.startswith(prefix)]
    for k in keys:
        _store.pop(k, None)
    return len(keys)


def clear() -> None:
    _store.clear()


def stats() -> dict:
    return {
        'entries': len(_store),
        'inflight': len(_inflight),
        'hits': _hits,
        'misses': _misses,
        'hit_rate': (_hits / (_hits + _misses)) if (_hits + _misses) else 0.0,
    }
