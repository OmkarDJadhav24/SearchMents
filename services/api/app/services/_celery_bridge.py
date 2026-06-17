"""
Thin bridge between the FastAPI service layer and Celery.

The Celery app lives in the worker container, but the API needs to
dispatch tasks. We solve this by importing the Celery app here with the
same broker URL. In production this pattern is standard — both processes
share the broker; neither needs to run in the same container.
"""
from celery import Celery
from app.config import get_settings

settings = get_settings()

# Re-create the Celery app (broker-only — no worker starts here)
_celery_app = Celery(
    "rag_tasks",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
)


def dispatch_ingestion_job(
    *,
    job_id: str,
    document_id: str,
    version_id: str,
    user_id: str,
    storage_path: str,
    mime_type: str,
) -> str:
    """
    Send the ingestion pipeline task to the 'ingestion' queue.
    Returns the Celery task ID.
    """
    result = _celery_app.send_task(
        "tasks.ingestion.pipeline.run_ingestion_pipeline",
        kwargs={
            "job_id": job_id,
            "document_id": document_id,
            "version_id": version_id,
            "user_id": user_id,
            "storage_path": storage_path,
            "mime_type": mime_type,
        },
        queue="ingestion",
    )
    return result.id


def dispatch_cleanup_job(*, document_id: str, user_id: str) -> str:
    """Send the soft-delete cleanup task to the 'maintenance' queue."""
    result = _celery_app.send_task(
        "tasks.maintenance.cleanup.run_document_cleanup",
        kwargs={"document_id": document_id, "user_id": user_id},
        queue="maintenance",
    )
    return result.id