# Import all models here so SQLAlchemy's metadata is fully populated
# when Alembic runs autogenerate. The order matters for FK resolution.

from app.models.user import User
from app.models.document import Document, DocumentVersion
from app.models.chunk import ChunkMetadata
from app.models.job import EmbeddingJob
from app.models.conversation import Conversation, Feedback
from app.models.audit import AuditLog

__all__ = [
    "User",
    "Document",
    "DocumentVersion",
    "ChunkMetadata",
    "EmbeddingJob",
    "Conversation",
    "Feedback",
    "AuditLog",
]