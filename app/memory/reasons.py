"""Fixed conversation reason codes. German labels live in the owner UI."""

from __future__ import annotations

CONVERSATION_REASONS: frozenset[str] = frozenset(
    {
        "unknown_account",
        "account_inactive",
        "invalid_envelope",
        "data_class_block",
        "secret_rejected",
        "thread_class_block",
        "duplicate_body_mismatch",
        "duplicate",
        "invalid_method",
        "reply_path_required",
        "reply_channel",
        "reply_target_rejected",
        "reply_target_missing",
        "invalid_reply",
        "source_rejected",
        "not_decided",
        "no_change",
        "already_corrected",
        "already_decided",
        "link_conflict",
        "owner_session_required",
        "shown_bytes_mismatch",
        "invalid_fact",
        "invalid_decision",
        "invalid_title",
        "invalid_data_class",
        "not_found",
        "thread_not_active",
        "thread_unmapped",
        "unknown_channel",
        "local_only_cloud_link",
        "account_missing",
    }
)
# Plan code ``unknown_source`` is shown as one of these three (issue #78, F8).
UNKNOWN_SOURCE_CODES: frozenset[str] = frozenset(
    {"unknown_account", "account_inactive", "invalid_envelope"}
)
HOLD_CODES: frozenset[str] = frozenset({"privacy_block", "blocklist_unavailable"})
CONVERSATION_EVENT_TYPES: tuple[str, ...] = (
    "assistant.turn_accepted",
    "assistant.turn_rejected",
    "assistant.turn_duplicate",
    "assistant.reply_recorded",
    "assistant.reply_rejected",
    "assistant.reply_duplicate",
    "assistant.backfill_completed",
    "memory.fact_proposed",
    "memory.fact_rejected_input",
    "memory.message_approved",
    "memory.message_rejected",
    "memory.message_corrected",
    "memory.fact_approved",
    "memory.fact_rejected",
    "memory.fact_corrected",
    "memory.topic_proposed",
    "memory.topic_created",
    "memory.topic_approved",
    "memory.topic_rejected",
    "memory.topic_corrected",
    "memory.link_approved",
    "memory.link_rejected",
    "memory.link_corrected",
)
