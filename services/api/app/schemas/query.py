import uuid
from pydantic import BaseModel, Field


class QueryFilters(BaseModel):
    document_ids: list[uuid.UUID] | None = None


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    conversation_id: uuid.UUID | None = None
    top_k: int = Field(default=5, ge=1, le=20)
    filters: QueryFilters | None = None


class SourceChunk(BaseModel):
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    filename: str
    chunk_index: int
    chunk_text: str
    relevance_score: float


class AskResponse(BaseModel):
    answer: str
    conversation_id: uuid.UUID
    sources: list[SourceChunk]
    retrieval_ms: int
    llm_ms: int
    from_cache: bool = False
    # True if LLM was unavailable and raw chunks were returned
    llm_unavailable: bool = False