"""Revoke auto-granted approvals from legacy 0002/0004 backfills.

Revision ID: 20251103_0005
Revises: 20251103_0004
Create Date: 2025-11-03

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20251103_0005"
down_revision: str | Sequence[str] | None = "20251103_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_REVOKE_AUTO_APPROVAL = """
UPDATE tasks AS t
SET requires_approval = true,
    approved_at = NULL,
    status = CASE
        WHEN t.status = 'queued' THEN 'awaiting_approval'::task_state
        ELSE t.status
    END
WHERE t.approved_at IS NOT NULL
  AND NOT EXISTS (
    SELECT 1
    FROM events AS e
    WHERE e.task_id = t.id
      AND e.event_type = 'task.approve'
  )
"""


def upgrade() -> None:
    op.execute(sa.text(_REVOKE_AUTO_APPROVAL))


def downgrade() -> None:
    pass
