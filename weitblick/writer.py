"""Local persistence interface and bounded, locked file implementation."""

import fcntl
import json
import os
import stat
import uuid
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

    def write(self, entries):
        for entry in entries:
            validate(entry)
        self.output.mkdir(mode=0o700, parents=True, exist_ok=True)
        directory = os.open(self.output, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            for name in ("weitblick.jsonl", "weitblick.md", ".lock"):
                try:
                    info = os.stat(name, dir_fd=directory, follow_symlinks=False)
                except FileNotFoundError:
                    continue
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                    raise ValueError("Unsafe output file")
            lock = os.open(".lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600, dir_fd=directory)
            try:
                fcntl.flock(lock, fcntl.LOCK_EX)
                try:
                    fd = os.open("weitblick.jsonl", os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory)
                except FileNotFoundError:
                    stored = []
                else:
                    with os.fdopen(fd, encoding="utf-8") as stream:
                        stored = [json.loads(line) for line in stream if line.strip()]
                    for entry in stored:
                        validate(entry)
                ids = {e["id"] for e in stored}
                added = 0
                for entry in entries:
                    if entry["id"] in ids or len(stored) >= 5:
                        continue
                    if sum(e["datum"] == entry["datum"] for e in stored) >= 3:
                        continue
                    stored.append(entry)
                    ids.add(entry["id"])
                    added += 1
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
                for name, content in [("weitblick.jsonl", jsonl), ("weitblick.md", markdown)]:
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
                os.fsync(directory)
                return added
            finally:
                os.close(lock)
        finally:
            os.close(directory)
