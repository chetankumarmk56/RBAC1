"""FastAPI dependencies that turn a bearer token into a database-backed Principal."""

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from collections.abc import Callable

from auth.security import decode_access_token
from db.session import get_db
from models import User
from rbac.service import PermissionDenied, Principal, check_permission, load_principal

bearer_scheme = HTTPBearer(auto_error=False)

_CREDENTIALS_ERROR = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Could not validate credentials",
    headers={"WWW-Authenticate": "Bearer"},
)


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> User:
    if credentials is None:
        raise _CREDENTIALS_ERROR

    try:
        payload = decode_access_token(credentials.credentials)
        user_id = int(payload["sub"])
    except (jwt.PyJWTError, KeyError, TypeError, ValueError):
        raise _CREDENTIALS_ERROR from None

    user = db.scalar(select(User).where(User.id == user_id))
    if user is None or not user.is_active:
        raise _CREDENTIALS_ERROR
    return user


def get_current_principal(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Principal:
    """The caller's identity plus permissions, always resolved from the database."""
    return load_principal(db, user)


def require_permission(permission: str) -> Callable[..., Principal]:
    """Dependency that gates an endpoint on a permission.

    Uses the same `check_permission` the tool layer uses, so HTTP endpoints and
    agent tool calls share one authority on what a role may do.
    """

    def dependency(principal: Principal = Depends(get_current_principal)) -> Principal:
        try:
            check_permission(principal, permission)
        except PermissionDenied as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from None
        return principal

    return dependency
