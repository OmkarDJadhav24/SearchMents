import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.audit import write_audit_log
from app.core.exceptions import AuthenticationError
from app.core.security import create_access_token, hash_password, verify_password
from app.models.user import User
from app.schemas.auth import LoginResponse, RegisterResponse

settings = get_settings()
logger = structlog.get_logger(__name__)


async def register_user(
    db: AsyncSession,
    *,
    email: str,
    password: str,
    full_name: str,
    ip_address: str | None = None,
) -> RegisterResponse:
    """
    Create a new user. Raises IntegrityError (caught by the route as 409)
    if the email already exists — handled via DB unique constraint.
    """
    user = User(
        email=email.lower().strip(),
        hashed_password=hash_password(password),
        full_name=full_name.strip(),
        role="user",
        is_active=True,
    )
    db.add(user)
    await db.flush()   # get the generated ID before audit log

    await write_audit_log(
        db,
        user_id=user.id,
        action="USER_REGISTER",
        resource_type="user",
        resource_id=user.id,
        ip_address=ip_address,
    )

    logger.info("User registered", user_id=str(user.id), email=user.email)
    return RegisterResponse(
        user_id=user.id,
        email=user.email,
        full_name=user.full_name,
        created_at=user.created_at,
    )


async def login_user(
    db: AsyncSession,
    *,
    email: str,
    password: str,
    ip_address: str | None = None,
) -> LoginResponse:
    """
    Verify credentials and return a JWT access token.
    Always raises the same AuthenticationError for wrong email OR password
    to prevent user enumeration.
    """
    result = await db.execute(select(User).where(User.email == email.lower().strip()))
    user = result.scalar_one_or_none()

    # Use constant-time comparison even when user is not found (dummy hash)
    _dummy = "$2b$12$invalidhashpaddingtomakeconsttimecheckworkproperly"
    stored_hash = user.hashed_password if user else _dummy
    password_ok = verify_password(password, stored_hash)

    if not user or not password_ok or not user.is_active:
        await write_audit_log(
            db,
            user_id=user.id if user else None,  # type: ignore[arg-type]
            action="USER_LOGIN_FAILED",
            resource_type="user",
            ip_address=ip_address,
        ) if user else None
        raise AuthenticationError("Invalid email or password")

    token = create_access_token(subject=str(user.id))

    await write_audit_log(
        db,
        user_id=user.id,
        action="USER_LOGIN",
        resource_type="user",
        resource_id=user.id,
        ip_address=ip_address,
    )

    logger.info("User logged in", user_id=str(user.id))
    return LoginResponse(
        access_token=token,
        token_type="bearer",
        expires_in=settings.jwt_expire_minutes * 60,
        user_id=user.id,
    )