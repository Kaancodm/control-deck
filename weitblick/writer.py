"""Local persistence interface and bounded, locked file implementation."""

import fcntl
import json
import os
import stat
import uuid
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from typing import Protocol

FIELDS = {
    "id",
    "art",
    "frage",
    "fundstelle",
    "datum",
    "bezug",
    "status",
    "nicht_gewaehlte_option",
    "quelle_liste",
    "hinweis",
}

DAILY_LIMIT = 3
TOTAL_LIMIT = 5
JSONL_NAME = "weitblick.jsonl"
MARKDOWN_NAME = "weitblick.md"
LOCK_NAME = ".lock"
# Written only by the Hindsight writer; listed here so every writer checks it.
STATUS_NAME = "hindsight-status.json"
OUTPUT_FILES = (JSONL_NAME, MARKDOWN_NAME, LOCK_NAME, STATUS_NAME)


class Writer(Protocol):
    def write(self, entries: list[dict]) -> int: ...


def validate(entry):
    if set(entry) != FIELDS or entry["status"] != "neu":
        raise ValueError("Invalid entry fields or status")
    question = entry["frage"]
    if (
        not isinstance(question, str)
        or not question.startswith("Waere denkbar, dass")
        or not question.endswith("?")
        or len(question) > 200
    ):
        raise ValueError("Invalid question")
    if entry["art"] not in {"nicht_gewaehlte_option", "verbindung", "wiederholung"}:
        raise ValueError("Invalid kind")
    minimum = 1 if entry["art"] == "nicht_gewaehlte_option" else 2
    if len(entry["fundstelle"]) < minimum:
        raise ValueError("Missing provenance")
    for ref in entry["fundstelle"]:
        if (
            set(ref) != {"datei", "zeile", "nummer"}
            or not ref["datei"]
            or not isinstance(ref["zeile"], int)
            or ref["zeile"] < 1
        ):
            raise ValueError("Invalid provenance")
    date.fromisoformat(entry["datum"])


def select_new(stored, entries):
    """Return the entries that fit the Stage 1 budget, in input order.

    `stored` and `entries` only need `id` and `datum`. Known IDs are skipped,
    at most TOTAL_LIMIT are kept overall and at most DAILY_LIMIT per `datum`.
    This is the unchanged Stage 1 counting rule, shared by every writer.
    """
    kept = [{"id": e["id"], "datum": e["datum"]} for e in stored]
    ids = {e["id"] for e in kept}
    accepted = []
    for entry in entries:
        if entry["id"] in ids or len(kept) >= TOTAL_LIMIT:
            continue
        if sum(e["datum"] == entry["datum"] for e in kept) >= DAILY_LIMIT:
            continue
        kept.append({"id": entry["id"], "datum": entry["datum"]})
        ids.add(entry["id"])
        accepted.append(entry)
    return accepted


@contextmanager
def locked_output(output):
    """Open the output directory without following links, check files, hold `.lock`."""
    output.mkdir(mode=0o700, parents=True, exist_ok=True)
    directory = os.open(output, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for name in OUTPUT_FILES:
            try:
                info = os.stat(name, dir_fd=directory, follow_symlinks=False)
            except FileNotFoundError:
                continue
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise ValueError("Unsafe output file")
        lock = os.open(LOCK_NAME, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600, dir_fd=directory)
        try:
            fcntl.flock(lock, fcntl.LOCK_EX)
            yield directory
        finally:
            os.close(lock)
    finally:
        os.close(directory)


def read_text(directory, name):
    """Return file content or None; never follows a link."""
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory)
    except FileNotFoundError:
        return None
    with os.fdopen(fd, encoding="utf-8") as stream:
        return stream.read()


def replace_atomically(directory, name, content):
    temp = ".pending-" + uuid.uuid4().hex
    fd = os.open(
        temp,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
        0o600,
        dir_fd=directory,
    )
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temp, name, src_dir_fd=directory, dst_dir_fd=directory)


def load_jsonl(directory):
    text = read_text(directory, JSONL_NAME)
    stored = []
    if text is not None:
        stored = [json.loads(line) for line in text.splitlines() if line.strip()]
    for entry in stored:
        validate(entry)
    return stored


class FileWriter:
    """Own one private output directory; JSONL is authoritative, Markdown derived.

    Directory parents must be trusted and not concurrently renamed by another user.
    Files are replaced, never followed or modified through hard links.
    """

    def __init__(self, output, sources):
        self.output = Path(output).absolute()
        self.sources = [Path(p).resolve(strict=True) for p in sources]
        for path in (self.output, *self.output.parents):
            if path.is_symlink():
                raise ValueError("Output symlinks are forbidden")
        resolved = self.output.resolve()
        for source in self.sources:
            if (
                resolved == source.parent
                or source.parent in resolved.parents
                or resolved in source.parents
            ):
                raise ValueError("Source and output directories must be separate")

    def read_stored(self):
        """Return the validated JSONL entries under the shared lock (read only)."""
        with locked_output(self.output) as directory:
            return load_jsonl(directory)

    def write(self, entries):
        for entry in entries:
            validate(entry)
        with locked_output(self.output) as directory:
            stored = load_jsonl(directory)
            accepted = select_new(stored, entries)
            stored.extend(accepted)
            jsonl = "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in stored)
            markdown = "# Weitblick: Fragen, keine Tatsachen\n\n"
            for entry in stored:
                markdown += f"- **Frage ({entry['id']}):** {entry['frage']}\n"
                for ref in entry["fundstelle"]:
                    # JSON quoting keeps paths as data rather than Markdown links.
                    path = (
                        json.dumps(ref["datei"], ensure_ascii=True)
                        .replace("<", "&lt;")
                        .replace(">", "&gt;")
                    )
                    markdown += f"    Quelle: {path}, Zeile {ref['zeile']} ({ref['nummer']})\n"
            for name, content in [(JSONL_NAME, jsonl), (MARKDOWN_NAME, markdown)]:
                replace_atomically(directory, name, content)
            os.fsync(directory)
            return len(accepted)
