"""Create the isolated, proposed knowledge contract and immutable document tables."""

from __future__ import annotations

from alembic import op
from app.kb.config import get_kb_test_database_url

revision = "kb_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    get_kb_test_database_url()
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE SCHEMA deck_kb")
    op.execute("""
        CREATE TABLE deck_kb.contract (
            id integer PRIMARY KEY,
            version varchar(32) NOT NULL,
            embedding_model varchar(64) NOT NULL,
            embedding_dimension integer NOT NULL,
            CONSTRAINT ck_kb_contract_singleton CHECK (id = 1)
        )
    """)
    op.execute("INSERT INTO deck_kb.contract VALUES (1, 'deck_kb.v1', 'deck-fake-embedding-v1', 8)")
    op.execute("""
        CREATE TABLE deck_kb.documents (
            id uuid PRIMARY KEY,
            source_task_id uuid UNIQUE,
            title text NOT NULL,
            data_class varchar(32) NOT NULL,
            content_sha256 varchar(64) NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT ck_kb_document_title CHECK (length(title) BETWEEN 1 AND 120),
            CONSTRAINT ck_kb_document_class
                CHECK (data_class IN ('local_only', 'cloud_redacted', 'cloud_ok')),
            CONSTRAINT ck_kb_document_hash CHECK (content_sha256 ~ '^[a-f0-9]{64}$')
        )
    """)
    op.execute("CREATE INDEX ix_kb_documents_class_id ON deck_kb.documents(data_class, id)")
    op.execute("""
        CREATE TABLE deck_kb.chunks (
            id uuid PRIMARY KEY,
            document_id uuid NOT NULL REFERENCES deck_kb.documents(id),
            position integer NOT NULL,
            content text NOT NULL,
            content_sha256 varchar(64) NOT NULL,
            embedding vector(8) NOT NULL,
            CONSTRAINT uq_kb_chunk_position UNIQUE (document_id, position),
            CONSTRAINT ck_kb_chunk_position CHECK (position >= 0),
            CONSTRAINT ck_kb_chunk_content CHECK (length(content) BETWEEN 1 AND 1500),
            CONSTRAINT ck_kb_chunk_hash
                CHECK (content_sha256 = encode(sha256(convert_to(content, 'UTF8')), 'hex'))
        )
    """)
    op.execute("""
        CREATE FUNCTION deck_kb.reject_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN RAISE EXCEPTION 'Knowledge documents are immutable'; END;
        $$
    """)
    for table in ("documents", "chunks"):
        op.execute(
            f"CREATE TRIGGER immutable_{table} BEFORE UPDATE OR DELETE ON deck_kb.{table} "
            "FOR EACH ROW EXECUTE FUNCTION deck_kb.reject_mutation()"
        )


def downgrade() -> None:
    get_kb_test_database_url()
    op.execute("DROP TABLE deck_kb.chunks")
    op.execute("DROP TABLE deck_kb.documents")
    op.execute("DROP TABLE deck_kb.contract")
    op.execute("DROP FUNCTION deck_kb.reject_mutation()")
    op.execute("DROP SCHEMA deck_kb")
