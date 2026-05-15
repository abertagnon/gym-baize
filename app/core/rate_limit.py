"""Rate limiter in-memoria a finestra scorrevole, senza dipendenze esterne."""

from collections import defaultdict
from datetime import datetime, timedelta
from threading import Lock

_attempts: dict[str, list[datetime]] = defaultdict(list)
_lock = Lock()
_MAX_KEYS = 5_000


def check_rate_limit(key: str, max_requests: int, window_seconds: int) -> bool:
    """Ritorna True se la richiesta è consentita, False se va limitata."""
    now = datetime.utcnow()
    cutoff = now - timedelta(seconds=window_seconds)
    with _lock:
        _attempts[key] = [t for t in _attempts[key] if t > cutoff]
        if len(_attempts[key]) >= max_requests:
            return False
        _attempts[key].append(now)
        if len(_attempts) > _MAX_KEYS:
            _evict_oldest()
        return True


def cleanup_old_attempts(max_age_seconds: int = 3600) -> int:
    """Rimuove gli IP senza tentativi recenti. Ritorna il numero di entry eliminate."""
    cutoff = datetime.utcnow() - timedelta(seconds=max_age_seconds)
    with _lock:
        stale = [k for k, ts_list in _attempts.items() if not any(t > cutoff for t in ts_list)]
        for k in stale:
            del _attempts[k]
    return len(stale)


def _evict_oldest():
    """Rimuove il 20% delle entry con tentativi più vecchi (lock già acquisito)."""
    n = max(1, len(_attempts) // 5)
    oldest = sorted(_attempts, key=lambda k: max(_attempts[k]) if _attempts[k] else datetime.min)[:n]
    for k in oldest:
        del _attempts[k]
