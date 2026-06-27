import uuid
from typing import Annotated

import structlog
from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.exceptions import AuthenticationError, AuthorizationError
from app.core.security import decode_access_token
from app.db.postgres import get_db
from app.models.user import User

settings = get_settings()
logger = structlog.get_logger(__name__)
bearer_scheme = HTTPBearer()


async def get_current_user(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(bearer_scheme)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> User:
    """
    Decode JWT → look up user in DB → validate active.

    Attaches user_id to request.state for the TenantContextMiddleware
    to pick up and bind to the log context.
    """
    payload = decode_access_token(credentials.credentials)
    user_id_str: str | None = payload.get("sub")
    if not user_id_str:
        raise AuthenticationError("Token missing subject claim")

    try:
        user_id = uuid.UUID(user_id_str)
    except ValueError:
        raise AuthenticationError("Malformed user ID in token")

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()

    if user is None:
        raise AuthenticationError("User not found")
    if not user.is_active:
        raise AuthorizationError("Account is disabled")

    # Bind to request state for middleware and downstream use
    request.state.user_id = user.id
    return user


async def require_admin(
    current_user: Annotated[User, Depends(get_current_user)],
) -> User:
    """Dependency for admin-only endpoints."""
    if current_user.role != "admin":
        raise AuthorizationError("Admin privileges required")
    return current_user


class PaginationParams:
    def __init__(self, page: int = 1, page_size: int = 20):
        if page < 1:
            page = 1
        if page_size < 1 or page_size > 100:
            page_size = 20
        self.page = page
        self.page_size = page_size

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size