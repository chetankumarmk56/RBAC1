"""Login and current-user endpoints."""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from auth.dependencies import get_current_principal
from auth.security import create_access_token, verify_password
from db.session import get_db
from models import User
from rbac.service import Principal, load_principal
from schemas import LoginRequest, LoginResponse, UserInfo

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _user_info(principal: Principal) -> UserInfo:
    return UserInfo(
        email=principal.email,
        full_name=principal.full_name,
        role=principal.role,
        permissions=sorted(principal.permissions),
        models=list(principal.models),
        row_scope=principal.row_scope,
    )


@router.post("/login", response_model=LoginResponse)
def login(payload: LoginRequest, db: Session = Depends(get_db)) -> LoginResponse:
    user = db.scalar(select(User).where(User.email == payload.email.lower().strip()))

    if user is None or not user.is_active or not verify_password(payload.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
        )

    principal = load_principal(db, user)
    return LoginResponse(
        access_token=create_access_token(user.id, user.email),
        user=_user_info(principal),
    )


@router.get("/me", response_model=UserInfo)
def me(principal: Principal = Depends(get_current_principal)) -> UserInfo:
    return _user_info(principal)
