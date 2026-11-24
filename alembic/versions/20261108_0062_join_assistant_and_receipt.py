"""Join the assistant chain and the receipt chain into one main head.

Both branches revise 20261104_0018. This revision adds no tables. Downgrade
removes only the join and leaves the two parents. The knowledge chain in
alembic_kb is a separate script and is not a parent of this revision.

Revision ID: 20261108_0062
Revises: 20261108_0020, 20261108_0061
Create Date: 2026-11-08
"""

from __future__ import annotations

revision = "20261108_0062"
down_revision = ("20261108_0020", "20261108_0061")
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Record the join. The parent revisions already created their tables."""


def downgrade() -> None:
    """Leave both parents in place. Their own downgrades keep their guards."""
