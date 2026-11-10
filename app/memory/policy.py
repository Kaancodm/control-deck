"""Fixed channel policy: which channels exist, which are cloud, which the Deck answers.

These constants encode owner decisions 7.1 and 7.7 (ADR 0010). They are code, not
configuration; changing them needs an ADR.
"""

from __future__ import annotations

from app.core.schemas import DataClass
from app.kb.contracts import allowed_classes


class ConversationError(Exception):
    """Expected failure with a fixed reason code. It never carries message text."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


CHANNELS = ("deck_ui", "slack", "n8n", "grok", "claude", "claude_code")
# Content on these channels leaves the host. A local_only thread never links to them.
CLOUD_CHANNELS = frozenset({"slack", "n8n", "grok", "claude"})
# The Deck itself answers only here; one reply per inbound message.
REPLY_CHANNELS = frozenset({"deck_ui", "slack"})
ROLES = ("owner", "assistant")
METHODS = ("deck_ui", "live_push", "import_file", "owner_typed")
EVIDENCE_DEFAULT = "unbewiesen"

_ALL = tuple(item.value for item in DataClass)
_CLOUD = (DataClass.CLOUD_REDACTED.value, DataClass.CLOUD_OK.value)

# Decision 7.7: the channel ceiling. The account row may narrow it, never widen it.
CHANNEL_CLASS_CEILING: dict[str, tuple[str, ...]] = {
    "deck_ui": _ALL,
    "slack": _CLOUD,
    "n8n": _CLOUD,
    "grok": _CLOUD,
    "claude": _CLOUD,
    "claude_code": (DataClass.LOCAL_ONLY.value,),
}
# Decision 7.7: claude_code imports are forced to local_only until the owner reviews them.
FORCED_CLASS: dict[str, str] = {"claude_code": DataClass.LOCAL_ONLY.value}

# Memory context targets (decision 7.3). Codex never receives memory.
TARGETS = ("ollama", "claude", "claude_code", "grok", "codex")
CLOUD_TARGETS = frozenset({"claude", "claude_code", "grok"})


def check_link_class(*, channel: str, thread_class: str) -> None:
    """A local_only thread may never be linked to a cloud channel."""
    if channel not in CHANNELS:
        raise ConversationError("unknown_channel")
    if thread_class == DataClass.LOCAL_ONLY.value and channel in CLOUD_CHANNELS:
        raise ConversationError("local_only_cloud_link")


def class_admits(container_class: str, item_class: str) -> bool:
    """True when an item of ``item_class`` may live under a ``container_class`` ceiling."""
    return DataClass(item_class) in allowed_classes(DataClass(container_class))
