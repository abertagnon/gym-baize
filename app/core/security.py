import uuid
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
from threading import Lock
from passlib.context import CryptContext
from jose import jwt, JWTError
from cryptography.fernet import Fernet, InvalidToken
from app.config import settings

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# Token blacklist: jti → scadenza. OrderedDict per eviction FIFO a dimensione massima.
_blacklist: OrderedDict[str, datetime] = OrderedDict()
_blacklist_lock = Lock()
_MAX_BLACKLIST_SIZE = 10_000


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


def create_access_token(user_id: int, username: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.JWT_EXPIRE_MINUTES)
    payload = {"sub": str(user_id), "username": username, "exp": expire, "jti": str(uuid.uuid4())}
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def blacklist_token(jti: str, expires_at: datetime) -> None:
    now = datetime.now(timezone.utc)
    with _blacklist_lock:
        _blacklist[jti] = expires_at
        expired = [k for k, v in _blacklist.items() if v <= now]
        for k in expired:
            del _blacklist[k]
        # Eviction FIFO se si supera la dimensione massima
        while len(_blacklist) > _MAX_BLACKLIST_SIZE:
            _blacklist.popitem(last=False)


def decode_access_token(token: str) -> dict | None:
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
        jti = payload.get("jti")
        if jti:
            with _blacklist_lock:
                if jti in _blacklist:
                    return None
        return payload
    except JWTError:
        return None


def _get_fernet() -> Fernet:
    return Fernet(settings.ENCRYPTION_KEY.encode())


def encrypt_credential(plaintext: str) -> str:
    return _get_fernet().encrypt(plaintext.encode()).decode()


def decrypt_credential(ciphertext: str) -> str:
    try:
        return _get_fernet().decrypt(ciphertext.encode()).decode()
    except InvalidToken:
        raise ValueError("Impossibile decrittare. ENCRYPTION_KEY cambiata?")
