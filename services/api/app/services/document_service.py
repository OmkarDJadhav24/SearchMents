import hashlib
import uuid
from datetime import datetime, timezone
from pathlib import Path

import structlog
from fastapi import UploadFile
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.audit import write_audit_log
from app.core.exceptions import (
    DuplicateDocumentError,
    FileTooLargeError,
    NotFoundError,
    UnsupportedFileTypeError,
)
from app.models.document import Document, DocumentVersion
from app.models.job import EmbeddingJob
from app.schemas.document import (
    DocumentDeleteResponse,
    DocumentItem,
    DocumentListResponse,
    DocumentUploadResponse,
    JobStatusResponse,
)

settings = get_settings()
logger = structlog.get_logger(__name__)


# ── Helpers ───────────────────────────────────────────────────────────

def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _storage_path(user_id: uuid.UUID, document_id: uuid.UUID, version: int, filename: str) -> Path:
    safe_name = Path(filename).name  # strip any path traversal
    return Path(settings.storage_path) / "uploads" / str(user_id) / str(document_id) / f"v{version}_{safe_name}"


# ── Upload ────────────────────────────────────────────────────────────

async def upload_document(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    file: UploadFile,
    title: str | None = None,
    ip_address: str | None = None,
) -> DocumentUploadResponse:
    # 1. Validate MIME type
    if file.content_type not in settings.allowed_mime_types:
        raise UnsupportedFileTypeError(
            f"File type '{file.content_type}' is not supported.",
            detail={"allowed": settings.allowed_mime_types},
        )

    # 2. Read file bytes and validate size
    file_bytes = await file.read()
    if len(file_bytes) > settings.max_upload_size_bytes:
        raise FileTooLargeError(
            f"File exceeds the {settings.max_upload_size_mb}MB limit.",
            detail={"size_bytes": len(file_bytes), "max_bytes": settings.max_upload_size_bytes},
        )

    # 3. Layer 1 dedup — exact file hash
    file_hash = _sha256(file_bytes)
    existing = await db.execute(
        select(Document).where(
            Document.user_id == user_id,
            Document.file_hash == file_hash,
            Document.status != "deleted",
        )
    )
    if existing.scalar_one_or_none():
        raise DuplicateDocumentError(
            "This exact file has already been uploaded.",
            detail={"file_hash": file_hash},
        )

    # 4. Create Document row (status=pending)
    document_id = uuid.uuid4()
    document = Document(
        id=document_id,
        user_id=user_id,
        filename=file.filename or "untitled",
        title=title,
        file_hash=file_hash,
        storage_path="",          # filled in after write
        mime_type=file.content_type,
        file_size_bytes=len(file_bytes),
        current_version=1,
        status="pending",
    )
    db.add(document)
    await db.flush()

    # 5. Write raw file to disk
    dest = _storage_path(user_id, document_id, 1, file.filename or "file")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(file_bytes)

    document.storage_path = str(dest)

    # 6. Create DocumentVersion row
    version = DocumentVersion(
        document_id=document_id,
        version_number=1,
        storage_path=str(dest),
        file_hash=file_hash,
        file_size_bytes=len(file_bytes),
        is_active=False,   # becomes True once indexing completes
    )
    db.add(version)
    await db.flush()

    # 7. Create EmbeddingJob row
    job = EmbeddingJob(
        document_id=document_id,
        version_id=version.id,
        user_id=user_id,
        status="queued",
    )
    db.add(job)
    await db.flush()

    # 8. Enqueue Celery task
    #    Import here to avoid circular import; worker shares the same task module
    from app.services._celery_bridge import dispatch_ingestion_job
    celery_task_id = dispatch_ingestion_job(
        job_id=str(job.id),
        document_id=str(document_id),
        version_id=str(version.id),
        user_id=str(user_id),
        storage_path=str(dest),
        mime_type=file.content_type,
    )
    job.celery_task_id = celery_task_id

    # 9. Audit
    await write_audit_log(
        db,
        user_id=user_id,
        action="DOCUMENT_UPLOAD",
        resource_type="document",
        resource_id=document_id,
        metadata={"filename": file.filename, "size_bytes": len(file_bytes)},
        ip_address=ip_address,
    )

    logger.info("Document upload queued", document_id=str(document_id), job_id=str(job.id))

    return DocumentUploadResponse(
        document_id=document_id,
        job_id=job.id,
        status="queued",
        filename=file.filename or "untitled",
        created_at=document.created_at,
    )


# ── Job status ────────────────────────────────────────────────────────

async def get_job_status(
    db: AsyncSession,
    *,
    job_id: uuid.UUID,
    user_id: uuid.UUID,
) -> JobStatusResponse:
    result = await db.execute(
        select(EmbeddingJob).where(
            EmbeddingJob.id == job_id,
            EmbeddingJob.user_id == user_id,
        )
    )
    job = result.scalar_one_or_none()
    if not job:
        raise NotFoundError("Job not found", detail={"job_id": str(job_id)})

    progress = (job.processed_chunks / job.total_chunks * 100) if job.total_chunks > 0 else 0

    return JobStatusResponse(
        job_id=job.id,
        status=job.status,
        progress_pct=round(progress, 1),
        total_chunks=job.total_chunks,
        processed_chunks=job.processed_chunks,
        error_message=job.error_message,
        completed_at=job.completed_at,
    )


# ── List ──────────────────────────────────────────────────────────────

async def list_documents(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    page: int = 1,
    page_size: int = 20,
    status: str | None = None,
) -> DocumentListResponse:
    query = select(Document).where(
        Document.user_id == user_id,
        Document.status != "deleted",
    )
    if status:
        query = query.where(Document.status == status)

    count_result = await db.execute(
        select(func.count()).select_from(query.subquery())
    )
    total = count_result.scalar_one()

    offset = (page - 1) * page_size
    result = await db.execute(
        query.order_by(Document.created_at.desc()).offset(offset).limit(page_size)
    )
    docs = result.scalars().all()

    items = [
        DocumentItem(
            document_id=d.id,
            filename=d.filename,
            title=d.title,
            status=d.status,
            current_version=d.current_version,
            mime_type=d.mime_type,
            file_size_bytes=d.file_size_bytes,
            created_at=d.created_at,
            updated_at=d.updated_at,
        )
        for d in docs
    ]
    return DocumentListResponse(items=items, total=total, page=page, page_size=page_size)


# ── Delete ────────────────────────────────────────────────────────────

async def delete_document(
    db: AsyncSession,
    *,
    document_id: uuid.UUID,
    user_id: uuid.UUID,
    ip_address: str | None = None,
) -> DocumentDeleteResponse:
    result = await db.execute(
        select(Document).where(
            Document.id == document_id,
            Document.user_id == user_id,
        )
    )
    doc = result.scalar_one_or_none()
    if not doc:
        raise NotFoundError("Document not found", detail={"document_id": str(document_id)})
    if doc.status == "deleted":
        raise NotFoundError("Document already deleted", detail={"document_id": str(document_id)})

    # Soft delete — background cleanup task handles Qdrant + filesystem
    doc.status = "deleted"
    doc.updated_at = datetime.now(timezone.utc)

    # Dispatch cleanup task
    from app.services._celery_bridge import dispatch_cleanup_job
    dispatch_cleanup_job(document_id=str(document_id), user_id=str(user_id))

    await write_audit_log(
        db,
        user_id=user_id,
        action="DOCUMENT_DELETE",
        resource_type="document",
        resource_id=document_id,
        ip_address=ip_address,
    )

    logger.info("Document soft-deleted", document_id=str(document_id))
    return DocumentDeleteResponse(document_id=document_id)