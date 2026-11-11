import uuid
from sqlalchemy import Column, String, Integer, ForeignKey, DateTime, JSON
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
from app.db.session import Base

class File(Base):
    __tablename__ = "files"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    repository_id = Column(UUID(as_uuid=True), ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False)
    
    file_path = Column(String, nullable=False, index=True) # e.g., "backend/app/main.py"
    extension = Column(String, nullable=True)              # e.g., ".py"
    language = Column(String, nullable=True)               # e.g., "Python"
    size_bytes = Column(Integer, nullable=False)

    # How this file was understood: "structural" when a grammar extracted real
    # symbols, otherwise one of the raw_* states. A null value predates this
    # column and is treated as unknown rather than as a successful parse.
    parse_state = Column(String, nullable=True, index=True)
    
    # We will use this later for deduplication and update tracking
    checksum = Column(String, nullable=True) 

    # Raw import statements extracted during parsing (e.g.
    # "from app.services import file_filter"). Consumed by the call-graph
    # resolver to scope a called name to the file that defines it.
    imports_json = Column(JSON, nullable=True)
    
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    # Relationships
    repository = relationship("Repository", back_populates="files")
    # Chunks
    chunks = relationship("Chunk", back_populates="file", cascade="all, delete-orphan")