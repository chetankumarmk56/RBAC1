"""Password hashing and JWT encode/decode."""

from datetime import datetime, timedelta, timezone

import bcrypt
import jwt

from config import settings


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except ValueError:
        # Malformed hash in the database — treat as a failed login, never a 500.
        return False


def create_access_token(user_id: int, email: str) -> str:
    """Mint a token carrying only identity.

    The role is deliberately left out: it is looked up from the database on every
    request so a token can never assert a role it was not granted.
    """
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "email": email,
        "iat": now,
        "exp": now + timedelta(minutes=settings.jwt_expire_minutes),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> dict:
    """Decode and verify a token. Raises jwt.PyJWTError on any problem."""
    return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
