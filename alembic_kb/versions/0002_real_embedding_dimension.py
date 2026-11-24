"""Widen the empty knowledge vector column to the real embedding model.

The guard runs in SQL so offline scripts carry it as well. It refuses any row in
``documents`` or ``chunks``; this revision never rewrites stored knowledge, never
touches the immutability triggers, and adds no index (exact search, see WISSENS_DB).
Target selection stays in ``env.py``; runtime targets cannot run the downgrade.
"""

from __future__ import annotations

from alembic import op
from app.kb.contracts import SCHEMA_EMBEDDING_DIMENSION

revision = "kb_0002"
down_revision = "kb_0001"
branch_labels = None
depends_on = None

_LEGACY_MODEL = "deck-fake-embedding-v1"
_LEGACY_DIMENSION = 8
_REAL_MODEL = "qwen3-embedding:0.6b"
_REAL_DIMENSION = SCHEMA_EMBEDDING_DIMENSION


def _lock() -> None:
    # Hold every write out between the emptiness check and the type change.
    op.execute("SET LOCAL lock_timeout = '10s'")
    op.execute(
        "LOCK TABLE deck_kb.contract, deck_kb.documents, deck_kb.chunks IN ACCESS EXCLUSIVE MODE"
    )


def _guard(model_condition: str, dimension: int) -> None:
    op.execute(f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM deck_kb.documents)
                OR EXISTS (SELECT 1 FROM deck_kb.chunks) THEN
                RAISE EXCEPTION 'knowledge_tables_not_empty';
            END IF;
            IF (SELECT count(*) FROM deck_kb.contract) <> 1 OR NOT EXISTS (
                SELECT 1 FROM deck_kb.contract
                WHERE id = 1 AND version = 'deck_kb.v1' AND {model_condition}
                    AND embedding_dimension = {dimension}
            ) THEN
                RAISE EXCEPTION 'knowledge_contract_unexpected';
            END IF;
            IF (
                SELECT format_type(atttypid, atttypmod) FROM pg_attribute
                WHERE attrelid = 'deck_kb.chunks'::regclass AND attname = 'embedding'
                    AND NOT attisdropped
            ) IS DISTINCT FROM 'vector({dimension})' THEN
                RAISE EXCEPTION 'knowledge_vector_column_unexpected';
            END IF;
        END
        $$
    """)


def _require_triggers() -> None:
    op.execute("""
        DO $$
        BEGIN
            IF (
                SELECT count(*) FROM pg_trigger
                WHERE NOT tgisinternal AND tgenabled = 'O' AND (
                    (tgrelid = 'deck_kb.documents'::regclass AND tgname = 'immutable_documents')
                    OR (tgrelid = 'deck_kb.chunks'::regclass AND tgname = 'immutable_chunks')
                )
            ) <> 2 THEN
                RAISE EXCEPTION 'knowledge_immutability_missing';
            END IF;
        END
        $$
    """)


def upgrade() -> None:
    _lock()
    _guard(f"embedding_model = '{_LEGACY_MODEL}'", _LEGACY_DIMENSION)
    # ALTER TYPE keeps column position and NOT NULL; the empty table needs no cast.
    op.execute(f"ALTER TABLE deck_kb.chunks ALTER COLUMN embedding TYPE vector({_REAL_DIMENSION})")
    op.execute(
        f"UPDATE deck_kb.contract SET embedding_model = '{_REAL_MODEL}', "
        f"embedding_dimension = {_REAL_DIMENSION} WHERE id = 1"
    )
    _require_triggers()


def downgrade() -> None:
    _lock()
    # Test fixtures may have swapped the model name; the width must still match.
    _guard("true", _REAL_DIMENSION)
    op.execute(
        f"ALTER TABLE deck_kb.chunks ALTER COLUMN embedding TYPE vector({_LEGACY_DIMENSION})"
    )
    op.execute(
        f"UPDATE deck_kb.contract SET embedding_model = '{_LEGACY_MODEL}', "
        f"embedding_dimension = {_LEGACY_DIMENSION} WHERE id = 1"
    )
    _require_triggers()
