"""Read-only overview projections. Every number has a stored or configured source."""

from __future__ import annotations

import os
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from app.audit.health import LaneHealthView
from app.audit.presentation import AuditItem
from app.db.settings import RunnerSettings
from app.transport.config import TransportConfigError, wake_config_from_env, wake_enabled
from app.ui.views import STATUS_LABELS

Switch = Literal["off", "incomplete", "on"]
Tone = Literal["on", "off", "warn"]
StepState = Literal["done", "current", "todo"]

# Lanes are reached through a local model or the official CLIs only (ADR 0006).
_LANE_KIND = {"ollama": "lokal", "claude": "CLI", "codex": "CLI"}


@dataclass(frozen=True)
class FeatureFlags:
    """Configuration switches as states only. Values never leave this projection."""

    assistant_loopback: Switch
    model_named: bool
    assistant_web: Switch
    encyclopedia: bool
    web_search: bool
    routine_wake: Switch


def _set(name: str) -> bool:
    return bool(os.environ.get(name, "").strip())


def feature_flags() -> FeatureFlags:
    """Mirror ``activation_status`` and ``wake_config_from_env`` without returning values."""
    if os.environ.get("DECK_ASSISTANT_OLLAMA") == "1":
        complete_model = _set("DECK_OLLAMA_URL") and _set("DECK_ASSISTANT_OLLAMA_MODEL")
        loopback: Switch = "on" if complete_model else "incomplete"
    else:
        loopback = "off"
    web: Switch = "off"
    encyclopedia = web_search = False
    if os.environ.get("DECK_ASSISTANT_WEB") == "1":
        provider = os.environ.get("DECK_ASSISTANT_SEARCH_PROVIDER", "").strip()
        complete = _set("DECK_ASSISTANT_WEB_ALLOWLIST") and (
            (provider == "mediawiki" and _set("DECK_ASSISTANT_MEDIAWIKI_API"))
            or (provider == "searxng" and _set("DECK_ASSISTANT_SEARXNG_URL"))
        )
        web = "on" if complete else "incomplete"
        encyclopedia = complete and provider == "mediawiki"
        web_search = complete and provider == "searxng"
    wake: Switch = "off"
    if wake_enabled():
        try:
            wake = "on" if wake_config_from_env().enabled else "off"
        except TransportConfigError:
            wake = "incomplete"
    return FeatureFlags(
        assistant_loopback=loopback,
        model_named=_set("DECK_ASSISTANT_OLLAMA_MODEL"),
        assistant_web=web,
        encyclopedia=encyclopedia,
        web_search=web_search,
        routine_wake=wake,
    )


@dataclass(frozen=True)
class Step:
    key: str
    number: str
    label: str
    icon: str
    state: StepState


_STEPS: tuple[tuple[str, str, str], ...] = (
    ("planned", "Geplant", "check"),
    ("awaiting_approval", STATUS_LABELS["awaiting_approval"][1], "shield"),
    ("queued", STATUS_LABELS["queued"][1], "circle"),
    ("leased", STATUS_LABELS["leased"][1], "arrow"),
    ("running", STATUS_LABELS["running"][1], "play"),
    ("review", STATUS_LABELS["review"][1], "diamond"),
)


def workflow_steps(status: str | None) -> tuple[Step, ...]:
    """Six fixed steps. Only the focus task's own status is highlighted."""
    keys = [key for key, _, _ in _STEPS]
    if status == "done":
        current = len(keys)
    elif status in keys[1:]:
        current = keys.index(status)
    else:
        current = -1
    steps = []
    for index, (key, label, icon) in enumerate(_STEPS):
        state: StepState = "todo"
        if current >= 0 and index < current:
            state = "done"
        elif index == current:
            state = "current"
        steps.append(Step(key, f"{index + 1:02d}", label, icon, state))
    return tuple(steps)


@dataclass(frozen=True)
class Claims:
    total: int
    limit: int | None


def claims_summary(lanes: Sequence[LaneHealthView]) -> Claims | None:
    """Sum of today's local claims; the limit only when every lane has one."""
    if not lanes:
        return None
    limits = [lane.daily_attempt_limit for lane in lanes]
    total_limit = None if any(value is None for value in limits) else sum(v or 0 for v in limits)
    return Claims(sum(lane.claims_today for lane in lanes), total_limit)


@dataclass(frozen=True)
class LaneRow:
    lane: str
    kind: str
    label: str
    tone: Tone
    claims: int
    limit: int | None
    observed_at: datetime | None


def _lane_label(lane: LaneHealthView) -> tuple[str, Tone]:
    if not lane.enabled:
        return "Deaktiviert", "off"
    if lane.status == "auth_required":
        return "Anmeldung nötig", "warn"
    if lane.paused or lane.status == "paused":
        return "Pausiert", "off"
    if lane.status == "unavailable":
        return "Nicht verfügbar", "warn"
    if lane.configured_attempts_remaining == 0:
        return "Lokales Tageslimit erreicht", "warn"
    if lane.status == "ok" and lane.live_available:
        return "Bereit", "on"
    if lane.status == "ok" and lane.execution_mode == "simulation":
        return "Simulation", "off"
    if lane.status == "ok":
        return "Vorprüfung ok", "on"
    return "Unbekannt", "off"


def lane_rows(lanes: Sequence[LaneHealthView]) -> tuple[LaneRow, ...]:
    rows = []
    for lane in lanes:
        label, tone = _lane_label(lane)
        rows.append(
            LaneRow(
                lane=lane.lane.value,
                kind=_LANE_KIND.get(lane.lane.value, "CLI"),
                label=label,
                tone=tone,
                claims=lane.claims_today,
                limit=lane.daily_attempt_limit,
                observed_at=lane.observed_at,
            )
        )
    return tuple(rows)


@dataclass(frozen=True)
class Outcome:
    phase: Literal["completed", "failed"]
    at: datetime


def latest_outcome(items: Sequence[AuditItem]) -> Outcome | None:
    """Newest completed or failed phase. Audit pages arrive newest first."""
    for item in items:
        if item.phase == "completed":
            return Outcome("completed", item.created_at)
        if item.phase == "failed":
            return Outcome("failed", item.created_at)
    return None


@dataclass(frozen=True)
class OpenPoint:
    code: str
    title: str
    detail: str
    chip: str
    tone: Tone
    href: str | None


BACKUP_AUDIT_URL = "/ui/audit?source=operation&event_type=operation.backup"
RESTORE_AUDIT_URL = "/ui/audit?source=operation&event_type=operation.restore"


def open_points(
    *,
    controls: RunnerSettings | None,
    waiting_total: int,
    blocked_total: int,
    lanes: Sequence[LaneHealthView],
    health_unavailable: bool,
    flags: FeatureFlags,
    last_backup: Outcome | None,
    last_restore: Outcome | None,
) -> tuple[OpenPoint, ...]:
    """Derive open points from stored deck data in a fixed order. No project list."""
    points: list[OpenPoint] = []
    if controls is not None and controls.stop_requested:
        points.append(
            OpenPoint(
                "stop",
                "Not-Aus aktiv",
                "Kein Runner übernimmt Arbeit.",
                "aktiv",
                "warn",
                "/ui/lanes",
            )
        )
    if controls is not None and controls.paused:
        points.append(
            OpenPoint(
                "paused",
                "Neue Arbeit pausiert",
                "Laufende Versuche enden regulär.",
                "pausiert",
                "warn",
                "/ui/lanes",
            )
        )
    if waiting_total:
        points.append(
            OpenPoint(
                "approvals",
                f"{waiting_total} Freigaben offen",
                "Start- und Ergebnis-Freigaben warten auf dich.",
                "offen",
                "warn",
                "/ui/tasks#waiting-tasks",
            )
        )
    if blocked_total:
        points.append(
            OpenPoint(
                "blocked",
                f"{blocked_total} Aufträge blockiert",
                "Die Datenschranke oder eine Regel hat angehalten.",
                "blockiert",
                "warn",
                "/ui/tasks?state=blocked",
            )
        )
    for lane in lanes:
        for warning in lane.warnings:
            if warning == "auth_required":
                detail = "Anmeldung erforderlich. Pausenzustand und Anmeldeverfahren prüfen."
            else:
                detail = "Kein erfasster Erfolgsnachweis seit mindestens einem Tag."
            points.append(
                OpenPoint(
                    f"lane_{warning}",
                    f"Lane {lane.lane.value}",
                    detail,
                    "Warnung",
                    "warn",
                    "/ui/lanes",
                )
            )
    if health_unavailable:
        points.append(
            OpenPoint(
                "health_unavailable",
                "Gesundheitsdaten nicht verfügbar",
                "Router-Konfiguration auf dem Host prüfen.",
                "fehlt",
                "warn",
                "/ui/lanes",
            )
        )
    if flags.assistant_loopback != "on":
        points.append(
            OpenPoint(
                "assistant_loopback",
                "Assistent-Loopback",
                "Ohne Loopback antwortet der Mock, kein Live-Modell.",
                "aus" if flags.assistant_loopback == "off" else "unvollständig",
                "off" if flags.assistant_loopback == "off" else "warn",
                "/ui/assistant",
            )
        )
    if flags.assistant_web != "on":
        points.append(
            OpenPoint(
                "assistant_web",
                "Webabruf des Assistenten",
                "Allowlist und Suchanbieter fehlen oder sind aus.",
                "aus" if flags.assistant_web == "off" else "unvollständig",
                "off" if flags.assistant_web == "off" else "warn",
                "/ui/assistant",
            )
        )
    if flags.routine_wake != "on":
        points.append(
            OpenPoint(
                "routine_wake",
                "Routine-Wake",
                "Ohne Sender gehen keine Meldungen an den Owner.",
                "aus" if flags.routine_wake == "off" else "Sender unvollständig",
                "off" if flags.routine_wake == "off" else "warn",
                "/ui/overview#meldungen",
            )
        )
    if last_backup is None:
        points.append(
            OpenPoint(
                "backup_missing",
                "Kein abgeschlossenes Backup erfasst",
                "Das Deck zeigt nur erfasste Backup-Ereignisse.",
                "fehlt",
                "warn",
                BACKUP_AUDIT_URL,
            )
        )
    elif last_backup.phase == "failed":
        points.append(
            OpenPoint(
                "backup_failed",
                "Letztes Backup fehlgeschlagen",
                "Ereignis im Audit prüfen.",
                "fehlgeschlagen",
                "warn",
                BACKUP_AUDIT_URL,
            )
        )
    if last_restore is None:
        points.append(
            OpenPoint(
                "restore_missing",
                "Keine Restore-Probe erfasst",
                "Eine Wiederherstellung ist erst mit einer Probe belegt.",
                "unbewiesen",
                "off",
                RESTORE_AUDIT_URL,
            )
        )
    elif last_restore.phase == "failed":
        points.append(
            OpenPoint(
                "restore_failed",
                "Letzte Restore-Probe fehlgeschlagen",
                "Ereignis im Audit prüfen.",
                "fehlgeschlagen",
                "warn",
                RESTORE_AUDIT_URL,
            )
        )
    return tuple(points)


@dataclass(frozen=True)
class NetNode:
    key: str
    label: str
    kind: Literal["owner", "lane", "service", "infra"]
    x: int
    y: int
    label_x: int
    label_y: int
    anchor: Literal["start", "middle", "end"]
    active: bool


# Fixed geometry for viewBox="0 0 400 250"; the deck sits at (200, 125).
_GEOMETRY: dict[str, tuple[int, int, int, int, Literal["start", "middle", "end"]]] = {
    "owner": (200, 25, 200, 12, "middle"),
    "ollama": (304, 96, 316, 92, "start"),
    "claude": (294, 164, 306, 168, "start"),
    "codex": (106, 164, 94, 168, "end"),
    "assistant": (96, 96, 84, 92, "end"),
    "messages": (200, 225, 200, 243, "middle"),
    "database": (367, 142, 392, 128, "end"),
    "backup": (33, 142, 8, 128, "start"),
}


def network_nodes(
    *,
    lanes: Sequence[LaneHealthView],
    flags: FeatureFlags,
    last_backup: Outcome | None,
) -> tuple[NetNode, ...]:
    """Eight nodes; ``active`` reflects stored or configured states only."""
    lane_on = {
        lane.lane.value: lane.enabled and not lane.paused and lane.status == "ok" for lane in lanes
    }
    entries: list[tuple[str, str, Literal["owner", "lane", "service", "infra"], bool]] = [
        ("owner", "Owner", "owner", True),
        ("ollama", "ollama", "lane", lane_on.get("ollama", False)),
        ("claude", "claude", "lane", lane_on.get("claude", False)),
        ("codex", "codex", "lane", lane_on.get("codex", False)),
        ("assistant", "Assistent", "service", flags.assistant_loopback == "on"),
        ("messages", "Meldungen", "service", flags.routine_wake == "on"),
        # The overview itself was read from the database, so this node is observed.
        ("database", "Datenbank", "infra", True),
        (
            "backup",
            "Backup",
            "infra",
            last_backup is not None and last_backup.phase == "completed",
        ),
    ]
    nodes = []
    for key, label, kind, active in entries:
        x, y, label_x, label_y, anchor = _GEOMETRY[key]
        nodes.append(
            NetNode(
                key=key,
                label=label if active else f"{label} · aus",
                kind=kind,
                x=x,
                y=y,
                label_x=label_x,
                label_y=label_y,
                anchor=anchor,
                active=active,
            )
        )
    return tuple(nodes)
