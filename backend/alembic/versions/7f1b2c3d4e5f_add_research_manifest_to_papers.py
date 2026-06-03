"""add research manifest to papers

Revision ID: 7f1b2c3d4e5f
Revises: 2b5d0e8f9a12
Create Date: 2026-05-30 23:45:00.000000
"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "7f1b2c3d4e5f"
down_revision: Union[str, None] = "2b5d0e8f9a12"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE papers ADD COLUMN IF NOT EXISTS research_manifest JSONB")
    op.execute(
        """
        COMMENT ON COLUMN papers.research_manifest IS
        'Structured ResearchManifest JSON generated from selected paper chunks. Contains KG-oriented fields such as core_concepts, methodology_summary, key_findings, and tech_stack_mentions.'
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE papers DROP COLUMN IF EXISTS research_manifest")
