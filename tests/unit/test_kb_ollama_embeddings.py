"""The Ollama embedding adapter validates every response and never leaks its inputs."""

from __future__ import annotations

import json
import logging

import pytest

from app.api.routes.knowledge import knowledge_error_status
from app.kb.contracts import MAX_DOCUMENT_BYTES, EmbeddingSpec, KnowledgeError
from app.kb.embeddings import FakeLocalEmbeddings
from app.kb.ollama_embeddings import (
    QUERY_INSTRUCTIONS,
    QWEN3_QUERY_INSTRUCTION,
    OllamaEmbeddings,
    failure_code,
    query_instruction,
)
from app.workers.lanes.ollama_http import OllamaHTTPClient, OllamaHTTPError
from app.workers.lanes.ollama_keep_alive import parse_keep_alive_value
from tests.support.ollama_stub import OllamaStub, embedding_vector

SPEC = EmbeddingSpec("ollama", "embed-fixture:0.6b", 1024)
SECRET_TEXT = "Synthetic private passage 7f3a"


def adapter(stub, *, keep_alive="server", timeout_s=2.0, spec=SPEC, **client_options):
    return OllamaEmbeddings(
        client=OllamaHTTPClient(stub.url, **client_options),
        spec=spec,
        timeout_s=timeout_s,
        keep_alive=parse_keep_alive_value(keep_alive),
    )


def show_requests(stub):
    return [request for request in stub.requests if request["path"] == "/api/show"]


def embed_requests(stub):
    return [request for request in stub.requests if request["path"] == "/api/embed"]


def test_batch_is_one_request_with_local_suffix_and_no_dimensions_field():
    with OllamaStub() as stub:
        vectors = adapter(stub).embed_documents(["one", "two", "three"])
        assert len(vectors) == 3 and all(len(vector) == 1024 for vector in vectors)
        assert vectors[0] == tuple(embedding_vector("one", 1024))
        assert [request["path"] for request in stub.requests] == ["/api/show", "/api/embed"]
        assert stub.requests[0]["body"] == {"model": "embed-fixture:0.6b:local"}
        assert stub.requests[1]["body"] == {
            "model": "embed-fixture:0.6b:local",
            "input": ["one", "two", "three"],
            "truncate": False,
        }


def test_query_is_single_input_and_keep_alive_is_forwarded():
    with OllamaStub() as stub:
        vector = adapter(stub, keep_alive="5m").embed_query("question")
        assert vector == tuple(embedding_vector("question", 1024))
        assert [request["path"] for request in stub.requests] == ["/api/show", "/api/embed"]
        assert embed_requests(stub)[0]["body"]["input"] == ["question"]
        assert embed_requests(stub)[0]["body"]["keep_alive"] == "5m"


QWEN3_SPEC = EmbeddingSpec("ollama", "qwen3-embedding:0.6b", 1024)
QWEN3_PREFIX = (
    "Instruct: Given a web search query, retrieve relevant passages that answer the query\nQuery: "
)


def test_query_instruction_is_a_fixed_constant_bound_to_the_model_tag():
    assert QWEN3_QUERY_INSTRUCTION == QWEN3_PREFIX
    assert query_instruction(QWEN3_SPEC) == QWEN3_PREFIX
    assert query_instruction(SPEC) == ""
    assert query_instruction(EmbeddingSpec("ollama", "qwen3-embedding:4b", 1024)) == ""
    assert query_instruction(EmbeddingSpec("ollama", "bge-m3:567m", 1024)) == ""
    with pytest.raises(TypeError):
        QUERY_INSTRUCTIONS["bge-m3:567m"] = "x"  # type: ignore[index]


def test_qwen3_query_carries_the_instruction():
    with OllamaStub(embed_model="qwen3-embedding:0.6b:local") as stub:
        vector = adapter(stub, spec=QWEN3_SPEC).embed_query("question")
        assert embed_requests(stub)[0]["body"]["input"] == [QWEN3_PREFIX + "question"]
        assert vector == tuple(embedding_vector(QWEN3_PREFIX + "question", 1024))


def test_qwen3_documents_stay_unprefixed():
    with OllamaStub(embed_model="qwen3-embedding:0.6b:local") as stub:
        adapter(stub, spec=QWEN3_SPEC).embed_documents(["one", "two"])
        assert embed_requests(stub)[0]["body"]["input"] == ["one", "two"]


def test_other_models_get_no_query_instruction():
    with OllamaStub() as stub:
        adapter(stub).embed_query("question")
        assert embed_requests(stub)[0]["body"]["input"] == ["question"]


def test_query_bound_applies_to_caller_text_not_prefix():
    limit_text = "x" * MAX_DOCUMENT_BYTES
    with OllamaStub(embed_model="qwen3-embedding:0.6b:local") as stub:
        embeddings = adapter(stub, spec=QWEN3_SPEC)
        embeddings.embed_query(limit_text)
        assert embed_requests(stub)[0]["body"]["input"] == [QWEN3_PREFIX + limit_text]
        with pytest.raises(KnowledgeError) as error:
            embeddings.embed_query(limit_text + "x")
        assert error.value.code == "embedding_input_invalid"
        assert len(embed_requests(stub)) == 1
        assert len(show_requests(stub)) == 1


@pytest.mark.parametrize(
    "options",
    [
        {"show_extra": {"remote_host": "https://remote-marker.invalid:11434"}},
        {"show_extra": {"remote_model": "cloud-secret-marker"}},
        {
            "show_extra": {
                "remote_host": "https://remote-marker.invalid",
                "remote_model": "cloud-secret-marker",
            }
        },
        {"show_extra": {"model": "embed-fixture:cloud"}},
        {"show_extra": {"model": "embed-fixture-cloud:0.6b"}},
        {"show_extra": {"modelfile": "FROM embed-fixture:cloud\n"}},
        {"show_extra": {"details": {"parent_model": "embed-fixture:cloud"}}},
        {"show_extra": {"details": {"parent_model": "embed-fixture-cloud:0.6b"}}},
        {"show_extra": {"model_info": {"general.basename": "embed-fixture:cloud"}}},
        {"show_extra": {"remote": True}},
        {"show_extra": {"cloud": True}},
        {"capabilities": ("embedding", "cloud")},
    ],
)
def test_show_remote_or_cloud_markers_abort_before_embed(caplog, options):
    caplog.set_level(logging.INFO, logger="app.kb.ollama_embeddings")
    with OllamaStub(**options) as stub:
        embeddings = adapter(stub)
        with pytest.raises(KnowledgeError) as error:
            embeddings.embed_documents([SECRET_TEXT])
        assert error.value.code == "embedding_model_remote"
        assert embed_requests(stub) == []
        assert [request["body"] for request in show_requests(stub)] == [
            {"model": "embed-fixture:0.6b:local"}
        ]
        with pytest.raises(KnowledgeError) as second_error:
            embeddings.embed_query(SECRET_TEXT)
        assert second_error.value.code == "embedding_model_remote"
        assert embed_requests(stub) == []
        assert len(show_requests(stub)) == 2
    for rendered in (caplog.text, str(error.value), repr(error.value)):
        assert SECRET_TEXT not in rendered
        assert "remote-marker.invalid" not in rendered
        assert "cloud-secret-marker" not in rendered
        assert "embed-fixture:cloud" not in rendered
    record = json.loads(caplog.records[0].getMessage())
    assert record["status"] == "embedding_model_remote"


@pytest.mark.parametrize(
    "bad_model",
    ["embed-fixture:cloud", "embed-fixture-cloud:0.6b", "embed-fixture:0.6b:local", ""],
)
def test_cloud_marker_on_spec_is_rejected_without_network(bad_model):
    with OllamaStub() as stub:
        embeddings = adapter(stub, spec=EmbeddingSpec("ollama", bad_model, 1024))
        with pytest.raises(KnowledgeError) as error:
            embeddings.embed_query(SECRET_TEXT)
        assert error.value.code == "embedding_model_remote"
        assert stub.requests == []


@pytest.mark.parametrize(
    "stub_options,client_options,code",
    [
        ({"status_by_path": {"/api/show": 404}}, {}, "embedding_model_missing"),
        ({"status_by_path": {"/api/show": 500}}, {}, "embedding_unavailable"),
        ({"status_by_path": {"/api/show": 400}}, {}, "embedding_unavailable"),
        (
            {"status_by_path": {"/api/show": 503}},
            {"busy_attempts": 2, "busy_backoff_s": 0.01},
            "embedding_busy",
        ),
        ({"raw_by_path": {"/api/show": b"not JSON"}}, {}, "embedding_malformed"),
        ({"show_extra": {"error": "synthetic show failure"}}, {}, "embedding_malformed"),
    ],
)
def test_show_failures_fail_closed_without_embed(caplog, stub_options, client_options, code):
    caplog.set_level(logging.INFO, logger="app.kb.ollama_embeddings")
    with OllamaStub(**stub_options) as stub:
        embeddings = adapter(stub, timeout_s=1.0, **client_options)
        with pytest.raises(KnowledgeError) as error:
            embeddings.embed_documents([SECRET_TEXT])
        assert error.value.code == code
        assert embed_requests(stub) == []
        assert len(show_requests(stub)) >= 1
    assert SECRET_TEXT not in caplog.text
    assert "synthetic show failure" not in caplog.text
    assert "synthetic provider error marker" not in caplog.text


def test_show_precheck_is_cached_per_adapter_and_rechecked_on_model_change():
    remote_spec = EmbeddingSpec("ollama", "remote-alias:1b", 1024)
    with OllamaStub(
        show_by_model={"remote-alias:1b:local": {"remote_model": "cloud-target:1b"}}
    ) as stub:
        embeddings = adapter(stub)
        embeddings.embed_documents(["first"])
        embeddings.embed_query("second")
        embeddings.embed_documents(["third"])
        assert [request["path"] for request in stub.requests] == [
            "/api/show",
            "/api/embed",
            "/api/embed",
            "/api/embed",
        ]
        # A second adapter instance maintains its own cache.
        adapter(stub).embed_query("fresh")
        assert len(show_requests(stub)) == 2
        assert len(embed_requests(stub)) == 4

        # Changing the model on the instance forces a new /api/show pre-check.
        embeddings._spec = QWEN3_SPEC
        embeddings.embed_query("switched")
        embeddings.embed_query("switched-cached")
        assert [request["body"]["model"] for request in show_requests(stub)] == [
            "embed-fixture:0.6b:local",
            "embed-fixture:0.6b:local",
            "qwen3-embedding:0.6b:local",
        ]
        assert len(embed_requests(stub)) == 6

        # Switching to a remote alias fails closed without calling /api/embed.
        embeddings._spec = remote_spec
        with pytest.raises(KnowledgeError) as error:
            embeddings.embed_documents([SECRET_TEXT])
        assert error.value.code == "embedding_model_remote"
        assert len(show_requests(stub)) == 4
        assert len(embed_requests(stub)) == 6


@pytest.mark.parametrize("options", [{}, {"status_by_path": {"/api/embed": 500}}])
def test_query_logs_carry_neither_prefix_nor_text(caplog, options):
    caplog.set_level(logging.INFO, logger="app.kb.ollama_embeddings")
    with OllamaStub(embed_model="qwen3-embedding:0.6b:local", **options) as stub:
        embeddings = adapter(stub, spec=QWEN3_SPEC)
        try:
            embeddings.embed_query(SECRET_TEXT)
        except KnowledgeError as exc:
            assert SECRET_TEXT not in repr(exc) and "Instruct" not in repr(exc)
    assert len(caplog.records) == 1
    record = json.loads(caplog.records[0].getMessage())
    assert record["purpose"] == "query" and record["count"] == 1
    for value in (caplog.text, repr(embeddings)):
        assert SECRET_TEXT not in value
        assert "Instruct" not in value and "web search query" not in value


def test_fake_query_has_no_instruction():
    fake = FakeLocalEmbeddings()
    assert fake.embed_query("question") == fake.embed_documents(["question"])[0]


def test_unsuffixed_model_echo_is_accepted():
    with OllamaStub(embed_model="embed-fixture:0.6b") as stub:
        assert len(adapter(stub).embed_query("question")) == 1024


@pytest.mark.parametrize(
    "options,code",
    [
        ({"embed_count_delta": -1}, "embedding_malformed"),
        ({"embed_count_delta": 1}, "embedding_malformed"),
        ({"embed_model": "other-model:1b"}, "embedding_malformed"),
        ({"embed_extra": {"remote_host": "https://remote.invalid"}}, "embedding_malformed"),
        ({"embed_extra": {"remote_model": "cloud-model"}}, "embedding_malformed"),
        ({"embed_extra": {"error": "synthetic"}}, "embedding_malformed"),
        ({"embed_dimension": 8}, "embedding_dimension_mismatch"),
        ({"embed_dimension": 1023}, "embedding_dimension_mismatch"),
        ({"embed_dimension": 1025}, "embedding_dimension_mismatch"),
    ],
)
def test_wrong_responses_are_rejected(options, code):
    with OllamaStub(**options) as stub:
        with pytest.raises(KnowledgeError) as error:
            adapter(stub).embed_documents(["one", "two"])
        assert error.value.code == code


def _raw(vectors, model="embed-fixture:0.6b:local"):
    return json.dumps({"model": model, "embeddings": vectors}).encode()


@pytest.mark.parametrize(
    "raw,code",
    [
        (_raw([[True] * 1024]), "embedding_invalid"),
        (_raw([[0.0] * 1024]), "embedding_invalid"),
        (_raw([["0.5"] * 1024]), "embedding_invalid"),
        (_raw([[None] * 1024]), "embedding_invalid"),
        (
            b'{"model":"embed-fixture:0.6b:local","embeddings":[[1e400' + b",1" * 1023 + b"]]}",
            "embedding_invalid",
        ),
        (_raw([{"values": [0.5] * 1024}]), "embedding_malformed"),
        (_raw("not a list"), "embedding_malformed"),
        (b'{"model":"embed-fixture:0.6b:local","embeddings":[[NaN]]}', "embedding_malformed"),
        (b"not JSON", "embedding_malformed"),
    ],
)
def test_invalid_vectors_are_rejected(raw, code):
    with OllamaStub(raw_by_path={"/api/embed": raw}) as stub:
        with pytest.raises(KnowledgeError) as error:
            adapter(stub).embed_query("question")
        assert error.value.code == code


@pytest.mark.parametrize(
    "stub_options,client_options,code",
    [
        (
            {"status_by_path": {"/api/embed": 503}},
            {"busy_attempts": 2, "busy_backoff_s": 0.01},
            "embedding_busy",
        ),
        ({"status_by_path": {"/api/embed": 404}}, {}, "embedding_model_missing"),
        ({"status_by_path": {"/api/embed": 500}}, {}, "embedding_unavailable"),
        ({"status_by_path": {"/api/embed": 400}}, {}, "embedding_input_too_long"),
        ({"header_delay_s": 5}, {}, "embedding_timeout"),
    ],
)
def test_transport_failures_map_to_codes(stub_options, client_options, code):
    with OllamaStub(**stub_options) as stub:
        with pytest.raises(KnowledgeError) as error:
            adapter(stub, timeout_s=1.0, **client_options).embed_query("question")
        assert error.value.code == code


def test_closed_port_is_unavailable():
    with OllamaStub() as stub:
        url = stub.url
    embeddings = OllamaEmbeddings(
        client=OllamaHTTPClient(url),
        spec=SPEC,
        timeout_s=1.0,
        keep_alive=parse_keep_alive_value("server"),
    )
    with pytest.raises(KnowledgeError) as error:
        embeddings.embed_query("question")
    assert error.value.code == "embedding_unavailable"


@pytest.mark.parametrize(
    "error,code",
    [
        (OllamaHTTPError("busy", 503), "embedding_busy"),
        (OllamaHTTPError("timeout"), "embedding_timeout"),
        (OllamaHTTPError("internal"), "embedding_malformed"),
        (OllamaHTTPError("unavailable", 404), "embedding_model_missing"),
        (OllamaHTTPError("unavailable", 400), "embedding_input_too_long"),
        (OllamaHTTPError("unavailable", 500), "embedding_unavailable"),
        (OllamaHTTPError("unavailable"), "embedding_unavailable"),
        (OllamaHTTPError("cancelled"), "embedding_unavailable"),
    ],
)
def test_failure_code_table(error, code):
    assert failure_code(error) == code


@pytest.mark.parametrize("texts", [[], [""], ["x"] * 17, "plain"])
def test_unbounded_batches_never_reach_the_server(texts):
    with OllamaStub() as stub:
        with pytest.raises(KnowledgeError) as error:
            adapter(stub).embed_documents(texts)
        assert error.value.code == "embedding_input_invalid"
        assert stub.requests == []


def test_fake_spec_is_refused():
    with OllamaStub() as stub:
        with pytest.raises(KnowledgeError) as error:
            adapter(stub, spec=EmbeddingSpec("fake", "deck-fake-embedding-v2", 1024))
        assert error.value.code == "knowledge_embedding_configuration_invalid"


@pytest.mark.parametrize(
    "options", [{}, {"embed_dimension": 8}, {"status_by_path": {"/api/embed": 500}}]
)
def test_logs_and_errors_never_contain_text_vectors_or_url(caplog, options):
    caplog.set_level(logging.INFO, logger="app.kb.ollama_embeddings")
    with OllamaStub(**options) as stub:
        embeddings = adapter(stub)
        try:
            embeddings.embed_documents([SECRET_TEXT])
            failure = None
        except KnowledgeError as exc:
            failure = exc
        vector_value = repr(embedding_vector(SECRET_TEXT, 8)[0])
        rendered = [caplog.text, repr(embeddings), str(embeddings)]
        if failure is not None:
            rendered += [str(failure), repr(failure)]
        for value in rendered:
            assert SECRET_TEXT not in value
            assert stub.url not in value
            assert str(stub.server.server_port) not in value
            assert vector_value not in value
            assert "synthetic provider error marker" not in value
    records = [json.loads(record.getMessage()) for record in caplog.records]
    assert len(records) == 1
    assert records[0]["event"] == "kb_embedding_call"
    assert records[0]["count"] == 1 and records[0]["dimension"] == 1024
    assert set(records[0]) == {
        "event",
        "purpose",
        "count",
        "dimension",
        "elapsed_ms",
        "status",
        "phase",
        "reason",
        "http_status",
    }


@pytest.mark.parametrize(
    "code",
    [
        "embedding_busy",
        "embedding_timeout",
        "embedding_unavailable",
        "embedding_model_missing",
        "embedding_model_remote",
        "embedding_malformed",
        "embedding_dimension_mismatch",
        "embedding_invalid",
        "knowledge_embedding_configuration_invalid",
        "knowledge_contract_mismatch",
    ],
)
def test_embedding_and_contract_faults_are_service_unavailable(code):
    assert knowledge_error_status(code) == 503


def test_input_faults_stay_unprocessable():
    assert knowledge_error_status("secret_detected") == 422
    assert knowledge_error_status("embedding_input_too_long") == 422


def test_too_long_input_has_its_own_code_and_safe_log(caplog):
    caplog.set_level(logging.INFO, logger="app.kb.ollama_embeddings")
    with OllamaStub(status_by_path={"/api/embed": 400}) as stub:
        with pytest.raises(KnowledgeError) as error:
            adapter(stub).embed_documents([SECRET_TEXT])
        assert error.value.code == "embedding_input_too_long"
        assert [request["path"] for request in stub.requests] == ["/api/show", "/api/embed"]
    record = json.loads(caplog.records[0].getMessage())
    assert record["status"] == "embedding_input_too_long"
    assert record["http_status"] == 400 and record["phase"] == "status"
    assert SECRET_TEXT not in caplog.text
