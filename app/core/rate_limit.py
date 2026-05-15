"""Rate limiter in-memoria a finestra scorrevole, senza dipendenze esterne."""

from collections import defaultdict
from datetime import datetime, timedelta
from threading import Lock

_attempts: dict[str, list[datetime]] = defaultdict(list)
_lock = Lock()


def check_rate_limit(key: str, max_requests: int, window_seconds: int) -> bool:
    """Ritorna True se la richiesta è consentita, False se va limitata."""
    now = datetime.utcnow()
    cutoff = now - timedelta(seconds=window_seconds)
    with _lock:
        _attempts[key] = [t for t in _attempts[key] if t > cutoff]
        if len(_attempts[key]) >= max_requests:
            return False
        _attempts[key].append(now)
        return True
