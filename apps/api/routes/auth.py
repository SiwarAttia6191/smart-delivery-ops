"""Authentication endpoints (Phase 5, #28): OAuth2 password flow.

POST /auth/token takes the standard username/password FORM (that's what
makes the Swagger UI's Authorize button work end-to-end) and returns a
short-lived HS256 JWT whose claims (sub, role) the mutating endpoints
enforce. Users live in the `users` table — create them with
scripts/seed_auth.py (demo) or scripts/create_user.py (real).
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import select
from sqlalchemy.orm import Session

from apps.api.deps import get_db
from core.security import create_access_token, verify_password
from domain.models import User

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/token")
def issue_token(
    form: Annotated[OAuth2PasswordRequestForm, Depends()],
    db: Session = Depends(get_db),
) -> dict:
    user = db.scalar(select(User).where(User.username == form.username))
    # Same error for unknown user and wrong password — no account oracle.
    if user is None or not verify_password(form.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return {
        "access_token": create_access_token(user.username, user.role),
        "token_type": "bearer",
    }
