"""Lane and assistant must send the same explicit num_ctx when configured."""

from __future__ import annotations

from app.assistant.ollama_chat import OllamaDialogueAdapter
from app.workers.lanes.ollama import OllamaExecutor
from tests.support.ollama_stub import OllamaStub
from tests.unit.test_ollama_executor import MODEL
from tests.unit.test_ollama_executor import context as context

SHARED_CTX = 6144


def test_lane_and_assistant_share_explicit_num_ctx(context) -> None:
    with OllamaStub(
        models=(MODEL,),
        show_extra={"model_info": {"fixture.context_length": 131072}},
    ) as stub:
        lane = OllamaExecutor(profile="live", url=stub.url, num_ctx=SHARED_CTX)
        lane.execute(context)
        lane_body = next(r for r in stub.requests if r["path"] == "/api/chat")["body"]

        assistant = OllamaDialogueAdapter(
            url=stub.url,
            model=MODEL,
            num_ctx=SHARED_CTX,
        )
        assistant.complete("ping", data_class="local_only")
        assistant_body = stub.requests[-1]["body"]

    assert lane_body["options"]["num_ctx"] == SHARED_CTX
    assert assistant_body["options"]["num_ctx"] == SHARED_CTX
