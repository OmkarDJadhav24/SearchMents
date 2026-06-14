import uuid
from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit import AuditLog

logger = structlog.get_logger(__name__)


async def write_audit_log(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    action: str,
    resource_type: str,
    resource_id: uuid.UUID | None = None,
    metadata: dict[str, Any] | None = None,
    ip_address: str | None = None,
) -> None:
    """
    Persist an audit log entry.

    This is fire-and-forget from the caller's perspective — failures are
    logged but never bubble up to the user.

    Example actions:
        USER_REGISTER, USER_LOGIN, USER_LOGIN_FAILED,
        DOCUMENT_UPLOAD, DOCUMENT_DELETE, DOCUMENT_UPDATE,
        QUERY_ASK, FEEDBACK_SUBMIT
    """
    try:
        entry = AuditLog(
            user_id=user_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            metadata=metadata or {},
            ip_address=ip_address,
        )
        db.add(entry)
        await db.flush()  # write in the same transaction as the caller
    except Exception as exc:
        logger.error("Failed to write audit log", action=action, error=str(exc))