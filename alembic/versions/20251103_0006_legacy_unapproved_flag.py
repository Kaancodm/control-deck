"""Regate legacy tasks with requires_approval=false but no owner approve event.

Revision ID: 20251103_0006
Revises: 20251103_0005
Create Date: 2025-11-03

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20251103_0006"
down_revision: str | Sequence[str] | None = "20251103_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_REGATE_LEGACY_FALSE_WITHOUT_APPROVE = """
UPDATE tasks AS t
SET requires_approval = true,
    status = CASE
        WHEN t.status = 'queued' THEN 'awaiting_approval'::task_state
        ELSE t.status
    END
WHERE t.requires_approval = false
  AND t.approved_at IS NULL
  AND t.status NOT IN ('succeeded', 'failed', 'blocked', 'cancelled')
  AND NOT EXISTS (
    SELECT 1
    FROM events AS e
    WHERE e.task_id = t.id
      AND e.event_type = 'task.approve'
  )
"""


def upgrade() -> None:
    op.execute(sa.text(_REGATE_LEGACY_FALSE_WITHOUT_APPROVE))


def downgrade() -> None:
    pass
