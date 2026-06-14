import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean, DateTime, ForeignKey, Index,
    Integer, String, Text, text,
)
from sqlalchemy.dialects.postgresql import UUID, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.postgres import Base


class ChunkMetadata(Base):
    __tablename__ = "chunks_metadata"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True,
        default=uuid.uuid4, server_default=text("gen_random_uuid()"),
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("document_versions.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )

    # ── Identity ──────────────────────────────────────────────────────
    # MD5 of normalised chunk text — deduplication key
    chunk_hash: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    # UUID5(chunk_hash) — stable Qdrant point ID, same content = same ID
    qdrant_point_id: Mapped[str] = mapped_column(String(36), nullable=False, unique=True)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)

    # ── Content ───────────────────────────────────────────────────────
    chunk_text: Mapped[str] = mapped_column(Text, nullable=False)
    char_start: Mapped[int] = mapped_column(Integer, nullable=False)
    char_end: Mapped[int] = mapped_column(Integer, nullable=False)
    token_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # ── BM25 full-text search ─────────────────────────────────────────
    # Populated via a PostgreSQL trigger or explicit UPDATE during ingestion
    chunk_text_tsv: Mapped[str | None] = mapped_column(
        TSVECTOR, nullable=True,
        comment="tsvector for BM25 full-text search",
    )

    # ── Lifecycle ─────────────────────────────────────────────────────
    # False = soft-deleted; excluded from all queries
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    # Relationships
    document: Mapped["Document"] = relationship(back_populates="chunks")  # noqa: F821
    version: Mapped["DocumentVersion"] = relationship(back_populates="chunks")

    __table_args__ = (
        # GIN index enables fast full-text search over chunk_text_tsv
        Index("idx_chunks_fts", "chunk_text_tsv", postgresql_using="gin"),
        # Composite index for tenant-scoped active chunk lookups
        Index("idx_chunks_user_active", "user_id", "is_active"),
    )

    def __repr__(self) -> str:
        return f"<ChunkMetadata doc={self.document_id} idx={self.chunk_index} active={self.is_active}>"