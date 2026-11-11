import uuid
from sqlalchemy import Column, String, Integer, ForeignKey, DateTime, Index
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func
from app.db.session import Base


class CallEdge(Base):
    """
    One resolved call site: "the function ``caller_symbol`` in ``caller_path``
    calls ``callee_symbol`` (in ``callee_path`` when resolved) on ``call_line``".

    ``resolution`` records *how* the target was found, so an edge is never
    mistaken for a verified link:

    * ``same_file``  - the symbol is defined in the caller's own file.
    * ``import``     - the caller's imports pointed at the defining file.
    * ``unique``     - exactly one project-wide symbol with that name.
    * ``unresolved`` - no confident target (stdlib, third-party, dynamic
      dispatch). The call site is kept and labelled rather than guessed,
      because a wrong edge is worse than a missing one.

    One row per call *site* (not per pair): the line is what makes the graph
    navigable for a reader tracing a path through the code.
    """

    __tablename__ = "call_edges"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    repository_id = Column(
        UUID(as_uuid=True),
        ForeignKey("repositories.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # The function/method containing the call site.
    caller_chunk_id = Column(
        UUID(as_uuid=True), ForeignKey("chunks.id", ondelete="CASCADE"), nullable=False
    )
    caller_symbol = Column(String, nullable=False)
    caller_path = Column(String, nullable=False)

    # The call target. Null chunk/path when the resolution failed: the called
    # name is still recorded so callers-of-X lookups stay honest about matches
    # that could not be placed.
    callee_chunk_id = Column(
        UUID(as_uuid=True), ForeignKey("chunks.id", ondelete="CASCADE"), nullable=True
    )
    callee_symbol = Column(String, nullable=False)
    callee_path = Column(String, nullable=True)

    call_line = Column(Integer, nullable=False)
    resolution = Column(String, nullable=False, index=True)

    created_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


Index("ix_call_edges_callee_symbol", CallEdge.callee_symbol)
Index("ix_call_edges_caller_symbol", CallEdge.caller_symbol)
