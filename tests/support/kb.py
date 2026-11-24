"""Point a disposable ``_test`` knowledge database at a chosen embedding contract.

kb_0002 always writes the real model identity. Tests that use the deterministic
fake (or a stub model) swap the singleton contract row here, never in a migration.
"""

from __future__ import annotations

from sqlalchemy import Engine, text

from app.kb.contracts import FAKE_EMBEDDING_SPEC, EmbeddingSpec


def _require_test_database(engine: Engine) -> None:
    database = engine.url.database
    if not database or not database.endswith("_test"):
        raise RuntimeError("Contract helpers only accept disposable _test databases")


def use_contract(engine: Engine, spec: EmbeddingSpec) -> None:
    _require_test_database(engine)
    with engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE deck_kb.contract SET version = 'deck_kb.v1', "
                "embedding_model = :model, embedding_dimension = :dimension WHERE id = 1"
            ),
            {"model": spec.model, "dimension": spec.dimension},
        )


def use_fake_contract(engine: Engine) -> None:
    use_contract(engine, FAKE_EMBEDDING_SPEC)


def contract_row(engine: Engine) -> tuple[str, str, int]:
    with engine.connect() as connection:
        version, model, dimension = connection.execute(
            text("SELECT version, embedding_model, embedding_dimension FROM deck_kb.contract")
        ).one()
    return version, model, dimension
