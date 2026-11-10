"""Immutable documents and class-filtered reads against the isolated KB contract."""

from __future__ import annotations

from uuid import UUID, uuid4

from sqlalchemy import Engine, create_engine, inspect, text
from sqlalchemy.exc import SQLAlchemyError

from app.core.schemas import DataClass
from app.kb.config import require_simulation
from app.kb.contracts import (
    CONTRACT_VERSION,
    MAX_DOCUMENT_BYTES,
    SCHEMA_EMBEDDING_DIMENSION,
    ContextBlock,
    EmbeddingSpec,
    KnowledgeError,
    allowed_classes,
    digest_text,
    validate_contexts,
)
from app.kb.embeddings import EmbeddingProvider, FakeLocalEmbeddings, vector_literal
from app.privacy.config import get_privacy_settings
from app.privacy.normalization import NormalizationError
from app.privacy.scanner import FindingKind, scan_text


def check_knowledge_text(title: str, content: str, data_class: DataClass) -> None:
    """Reject secrets before persistence or even the local embedding simulation."""
    if not isinstance(data_class, DataClass):
        raise KnowledgeError("invalid_data_class")
    try:
        size = len(content.encode("utf-8"))
        title.encode("utf-8")
    except UnicodeError:
        raise KnowledgeError("invalid_utf8") from None
    if "\x00" in title or "\x00" in content:
        raise KnowledgeError("knowledge_text_invalid")
    if not 1 <= len(title) <= 120 or not 1 <= size <= MAX_DOCUMENT_BYTES:
        raise KnowledgeError("knowledge_text_too_large")
    settings = get_privacy_settings()
    if data_class != DataClass.LOCAL_ONLY and not settings.blocklist_configured:
        raise KnowledgeError("knowledge_privacy_configuration_unavailable")
    try:
        findings = scan_text(
            title + "\n" + content, source="knowledge", blocklist=settings.blocklist
        )
    except NormalizationError:
        raise KnowledgeError("unicode_too_complex") from None
    if any(item.kind == FindingKind.SECRET for item in findings):
        raise KnowledgeError("secret_detected")
    if data_class == DataClass.CLOUD_OK and findings:
        raise KnowledgeError("knowledge_privacy_gate")


def check_contract(engine: Engine, spec: EmbeddingSpec, *, write: bool = False) -> None:
    """Compare the database with the configured spec only; no provider is contacted."""
    if spec.dimension != SCHEMA_EMBEDDING_DIMENSION:
        raise KnowledgeError("knowledge_contract_mismatch")
    try:
        with engine.connect() as connection:
            record = connection.execute(
                text(
                    "SELECT version, embedding_model, embedding_dimension "
                    "FROM deck_kb.contract WHERE id = 1"
                )
            ).one()
            if tuple(record) != (CONTRACT_VERSION, spec.model, spec.dimension):
                raise KnowledgeError("knowledge_contract_mismatch")
            metadata = inspect(connection)
            columns = {
                column["name"]: column
                for column in metadata.get_columns("documents", schema="deck_kb")
            }
            classification = columns.get("data_class")
            if (
                classification is None
                or classification["nullable"]
                or classification.get("default")
            ):
                raise KnowledgeError("knowledge_contract_mismatch")
            dimension = connection.scalar(
                text(
                    "SELECT format_type(atttypid, atttypmod) FROM pg_attribute "
                    "WHERE attrelid = 'deck_kb.chunks'::regclass AND attname = 'embedding'"
                )
            )
            if dimension != f"vector({spec.dimension})":
                raise KnowledgeError("knowledge_contract_mismatch")
            protected = connection.scalar(
                text(
                    "SELECT count(*) FROM pg_trigger "
                    "WHERE NOT tgisinternal AND tgenabled = 'O' AND "
                    "((tgrelid = 'deck_kb.documents'::regclass "
                    "AND tgname = 'immutable_documents') OR "
                    "(tgrelid = 'deck_kb.chunks'::regclass AND tgname = 'immutable_chunks'))"
                )
            )
            if protected != 2:
                raise KnowledgeError("knowledge_contract_mismatch")
            for table in ("documents", "chunks"):
                privileges = ("SELECT", "INSERT") if write else ("SELECT",)
                for privilege in privileges:
                    if not connection.scalar(
                        text("SELECT has_table_privilege(current_user, :table, :privilege)"),
                        {"table": "deck_kb." + table, "privilege": privilege},
                    ):
                        raise KnowledgeError("knowledge_permission_denied")
    except SQLAlchemyError:
        raise KnowledgeError("knowledge_contract_unavailable") from None


class KnowledgeStore:
    """Independent transactions; callers must not hold task/lease transactions."""

    def __init__(self, engine: Engine, *, embeddings: EmbeddingProvider | None = None) -> None:
        self.engine = engine
        self.embeddings: EmbeddingProvider = embeddings or FakeLocalEmbeddings()

    @property
    def spec(self) -> EmbeddingSpec:
        return self.embeddings.spec

    def check_contract(self, *, write: bool = False) -> None:
        check_contract(self.engine, self.spec, write=write)

    def _vectors(self, pieces: tuple[str, ...]) -> tuple[str, ...]:
        vectors = self.embeddings.embed_documents(pieces)
        if len(vectors) != len(pieces):
            raise KnowledgeError("embedding_malformed")
        return tuple(vector_literal(item, dimension=self.spec.dimension) for item in vectors)

    def put_document(
        self,
        *,
        document_id: UUID,
        title: str,
        content: str,
        data_class: DataClass,
        source_task_id: UUID | None = None,
    ) -> UUID:
        check_knowledge_text(title, content, data_class)
        self.check_contract(write=True)
        pieces = tuple(content[start : start + 1500] for start in range(0, len(content), 1500))
        vectors = self._vectors(pieces)
        digest = digest_text(content)
        try:
            with self.engine.begin() as connection:
                inserted = connection.scalar(
                    text(
                        "INSERT INTO deck_kb.documents "
                        "(id, source_task_id, title, data_class, content_sha256) "
                        "VALUES (:id, :source, :title, :classification, :digest) "
                        "ON CONFLICT DO NOTHING RETURNING id"
                    ),
                    {
                        "id": document_id,
                        "source": source_task_id,
                        "title": title,
                        "classification": data_class.value,
                        "digest": digest,
                    },
                )
                if inserted is None:
                    existing = connection.execute(
                        text(
                            "SELECT source_task_id, title, data_class, content_sha256 "
                            "FROM deck_kb.documents WHERE id = :id"
                        ),
                        {"id": document_id},
                    ).one_or_none()
                    if existing is None or tuple(existing) != (
                        source_task_id,
                        title,
                        data_class.value,
                        digest,
                    ):
                        raise KnowledgeError("knowledge_export_conflict")
                    return document_id
                for position, (piece, vector) in enumerate(zip(pieces, vectors, strict=True)):
                    connection.execute(
                        text(
                            "INSERT INTO deck_kb.chunks "
                            "(id, document_id, position, content, content_sha256, embedding) "
                            "VALUES (:id, :document, :position, :content, :digest, "
                            "CAST(:vector AS vector))"
                        ),
                        {
                            "id": uuid4(),
                            "document": document_id,
                            "position": position,
                            "content": piece,
                            "digest": digest_text(piece),
                            "vector": vector,
                        },
                    )
            return document_id
        except SQLAlchemyError:
            raise KnowledgeError("knowledge_write_unavailable") from None

    def resolve(
        self, references: tuple[str, ...], *, task_class: DataClass
    ) -> tuple[ContextBlock, ...]:
        if not references:
            return ()
        # Validate count, duplication and canonical IDs before any database request.
        if len(references) > 16 or len(set(references)) != len(references):
            raise KnowledgeError("invalid_context_references")
        try:
            identifiers = tuple(UUID(reference) for reference in references)
        except ValueError:
            raise KnowledgeError("invalid_context_reference") from None
        if tuple(map(str, identifiers)) != references:
            raise KnowledgeError("invalid_context_reference")
        self.check_contract()
        blocks: list[ContextBlock] = []
        try:
            with self.engine.connect() as connection:
                for identifier in identifiers:
                    record = connection.execute(
                        text(
                            "SELECT id, title, data_class, content_sha256 FROM deck_kb.documents "
                            "WHERE id = :id AND data_class = ANY(:classes)"
                        ),
                        {
                            "id": identifier,
                            "classes": [item.value for item in allowed_classes(task_class)],
                        },
                    ).one_or_none()
                    if record is None:
                        raise KnowledgeError("context_reference_unavailable")
                    rows = connection.execute(
                        text(
                            "SELECT position, content, content_sha256 FROM deck_kb.chunks "
                            "WHERE document_id = :id ORDER BY position LIMIT 25"
                        ),
                        {"id": identifier},
                    ).all()
                    if not rows or tuple(row.position for row in rows) != tuple(range(len(rows))):
                        raise KnowledgeError("knowledge_integrity_error")
                    if any(digest_text(row.content) != row.content_sha256 for row in rows):
                        raise KnowledgeError("knowledge_integrity_error")
                    content = "".join(row.content for row in rows)
                    if digest_text(content) != record.content_sha256:
                        raise KnowledgeError("knowledge_integrity_error")
                    classification = DataClass(record.data_class)
                    check_knowledge_text(record.title, content, classification)
                    rendered = f"### Knowledge {identifier}\n{record.title}\n{content}"
                    blocks.append(
                        ContextBlock(
                            str(identifier), classification, rendered, digest_text(rendered)
                        )
                    )
        except SQLAlchemyError:
            raise KnowledgeError("knowledge_read_unavailable") from None
        resolved = tuple(blocks)
        validate_contexts(resolved, references=references, task_class=task_class)
        return resolved

    def search(
        self, query: str, *, task_class: DataClass, limit: int = 5
    ) -> tuple[ContextBlock, ...]:
        if isinstance(limit, bool) or not 1 <= limit <= 16:
            raise KnowledgeError("invalid_search_limit")
        check_knowledge_text("Search", query, DataClass.LOCAL_ONLY)
        self.check_contract()
        vector = vector_literal(self.embeddings.embed_query(query), dimension=self.spec.dimension)
        try:
            with self.engine.connect() as connection:
                identifiers = connection.scalars(
                    text(
                        "SELECT d.id FROM deck_kb.documents d "
                        "JOIN deck_kb.chunks c ON c.document_id = d.id "
                        "WHERE d.data_class = ANY(:classes) GROUP BY d.id "
                        "ORDER BY min(c.embedding <=> CAST(:vector AS vector)), d.id LIMIT :limit"
                    ),
                    {
                        "classes": [item.value for item in allowed_classes(task_class)],
                        "vector": vector,
                        "limit": limit,
                    },
                ).all()
        except SQLAlchemyError:
            raise KnowledgeError("knowledge_read_unavailable") from None
        return self.resolve(
            tuple(str(identifier) for identifier in identifiers), task_class=task_class
        )


def configured_store() -> KnowledgeStore:
    """Use only the explicit simulation profile and an isolated test database."""
    return KnowledgeStore(
        create_engine(require_simulation(), pool_pre_ping=True, hide_parameters=True)
    )
