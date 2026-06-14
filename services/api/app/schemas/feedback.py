import uuid
from datetime import datetime
from pydantic import BaseModel, Field


class FeedbackRequest(BaseModel):
    conversation_id: uuid.UUID
    question: str = Field(min_length=1)
    answer: str = Field(min_length=1)
    rating: int = Field(ge=1, le=5)
    comment: str | None = None
    retrieved_chunk_ids: list[uuid.UUID] = Field(default_factory=list)


class FeedbackResponse(BaseModel):
    feedback_id: uuid.UUID
    created_at: datetime

    model_config = {"from_attributes": True}