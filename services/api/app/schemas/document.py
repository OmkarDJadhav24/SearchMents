import uuid
from datetime import datetime
from pydantic import BaseModel, Field


class DocumentUploadResponse(BaseModel):
    document_id: uuid.UUID
    job_id: uuid.UUID
    status: str
    filename: str
    created_at: datetime

    model_config = {"from_attributes": True}


class JobStatusResponse(BaseModel):
    job_id: uuid.UUID
    status: str
    progress_pct: float
    total_chunks: int
    processed_chunks: int
    error_message: str | None = None
    completed_at: datetime | None = None

    model_config = {"from_attributes": True}


class DocumentItem(BaseModel):
    document_id: uuid.UUID
    filename: str
    title: str | None
    status: str
    current_version: int
    mime_type: str
    file_size_bytes: int
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class DocumentListResponse(BaseModel):
    items: list[DocumentItem]
    total: int
    page: int
    page_size: int


class DocumentDeleteResponse(BaseModel):
    document_id: uuid.UUID
    status: str = "deleted"