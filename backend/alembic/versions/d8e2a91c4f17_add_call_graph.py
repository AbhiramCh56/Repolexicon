"""add call_edges table and files.imports_json for the call graph

Revision ID: d8e2a91c4f17
Revises: f2a7c1d94b60
Create Date: 2026-11-11 10:00:00.000000

Stores the resolved call graph: one row per call site, with an honest
``resolution`` label (same_file / import / unique / unresolved) so an edge is
never mistaken for a verified link. ``files.imports_json`` persists the import
statements extracted during parsing, which the resolver uses to scope a called
name to the file that defines it.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'd8e2a91c4f17'
down_revision: Union[str, Sequence[str], None] = 'f2a7c1d94b60'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # repositories/files are still created by Base.metadata.create_all on boot
    # (see app/main.py), so guard on the parent table's presence to keep
    # `alembic upgrade head` usable on an empty database.
    conn = op.get_bind()
    has_repositories = sa.inspect(conn).has_table("repositories")
    if not has_repositories:
        return

    op.create_table(
        'call_edges',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('repository_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('caller_chunk_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('caller_symbol', sa.String(), nullable=False),
        sa.Column('caller_path', sa.String(), nullable=False),
        sa.Column('callee_chunk_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('callee_symbol', sa.String(), nullable=False),
        sa.Column('callee_path', sa.String(), nullable=True),
        sa.Column('call_line', sa.Integer(), nullable=False),
        sa.Column('resolution', sa.String(), nullable=False),
        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ['repository_id'],
            ['repositories.id'],
            ondelete='CASCADE',
        ),
        sa.ForeignKeyConstraint(
            ['caller_chunk_id'],
            ['chunks.id'],
            ondelete='CASCADE',
        ),
        sa.ForeignKeyConstraint(
            ['callee_chunk_id'],
            ['chunks.id'],
            ondelete='CASCADE',
        ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'ix_call_edges_id', 'call_edges', ['id'], unique=False
    )
    op.create_index(
        'ix_call_edges_repository_id', 'call_edges', ['repository_id'], unique=False
    )
    op.create_index(
        'ix_call_edges_resolution', 'call_edges', ['resolution'], unique=False
    )
    op.create_index(
        'ix_call_edges_callee_symbol', 'call_edges', ['callee_symbol'], unique=False
    )
    op.create_index(
        'ix_call_edges_caller_symbol', 'call_edges', ['caller_symbol'], unique=False
    )

    if sa.inspect(conn).has_table("files"):
        op.add_column('files', sa.Column('imports_json', sa.JSON(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_call_edges_caller_symbol', table_name='call_edges')
    op.drop_index('ix_call_edges_callee_symbol', table_name='call_edges')
    op.drop_index('ix_call_edges_resolution', table_name='call_edges')
    op.drop_index('ix_call_edges_repository_id', table_name='call_edges')
    op.drop_index('ix_call_edges_id', table_name='call_edges')
    op.drop_table('call_edges')

    conn = op.get_bind()
    if sa.inspect(conn).has_table("files"):
        op.drop_column('files', 'imports_json')
