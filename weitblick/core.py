"""Parse explicit list fields and generate conservative questions."""

import hashlib
import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

LISTS = {"ENTSCHEIDE": "E", "SERVER": "S", "AUFTRAEGE": "A"}
ID = re.compile(r"\b[ESA]-\d+\b")
UNSAFE = re.compile(
    r"https?://|www\.|@|\b(?:\d{1,3}\.){3}\d{1,3}\b|"
    r"\b[\da-f]{0,4}:[\da-f:]{2,}|\b(?:token|password|passwort|secret|api[_ -]?key)\b|"
    r"ignoriere|ignore|<|>|\[[^\]]*\]\(",
    re.I,
)
FIELDS = {
    "nummer",
    "id",
    "thema",
    "titel",
    "projekt",
    "dienst",
    "status",
    "ablauf-status",
    "letzte pruefung",
    "datum",
    "optionen/empfehlung",
    "option 2",
    "bezug",
    "ruecklauf",
    "ersetzt",
    "ergaenzt",
    "siehe",
}


def normal(text):
    return " ".join(
        text.casefold()
        .replace("ä", "ae")
        .replace("ö", "oe")
        .replace("ü", "ue")
        .replace("ß", "ss")
        .split()
    )


def safe(text):
    return bool(text.strip()) and not UNSAFE.search(text) and not any(ord(c) < 32 for c in text)


@dataclass
class Row:
    number: str
    source: str
    path: str
    line: int
    fields: dict

    def reference(self):
        return {"datei": self.path, "zeile": self.line, "nummer": self.number}


def cells(line):
    return [
        part.strip().replace(r"\|", "|") for part in re.split(r"(?<!\\)\|", line.strip().strip("|"))
    ]


def read_sources(paths):
    if set(paths) != set(LISTS):
        raise ValueError("Exactly three source lists are required")
    rows = []
    seen = set()
    for source, prefix in LISTS.items():
        path = Path(paths[source]).resolve(strict=True)
        header = None
        current = None
        for line_no, raw in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
            line = raw.strip()
            if line.startswith("```") or line.startswith("~~~"):
                raise ValueError("Code blocks are not supported in source lists")
            if line.startswith("|"):
                current = None
                parts = cells(line)
                keys = [normal(p.strip("* ")) for p in parts]
                if "nummer" in keys or "id" in keys:
                    header = keys
                    continue
                if all(re.fullmatch(r":?-+:?", p) for p in parts):
                    continue
                if header is None:
                    continue
                if len(parts) > len(header):
                    raise ValueError("Unescaped table separator")
                fields = dict(zip(header, parts, strict=False))
                number = fields.get("nummer", fields.get("id", "")).strip("* ")
                if not re.fullmatch(prefix + r"-\d+", number):
                    continue
            else:
                header = None
                match = re.match(
                    r"^(?:#{1,6}\s+|[-*]\s+)?\*{0,2}("
                    + prefix
                    + r"-\d+)\b\*{0,2}(?:\s*[:–—-]\s*|\s+)?(.*)$",
                    line,
                )
                if match:
                    number, title = match.groups()
                    fields = {"titel": title.strip()}
                else:
                    field = re.match(r"^(?:[-*]\s+)?\*{0,2}([^:]+?)\*{0,2}:\s*\*{0,2}(.*)$", line)
                    if field and current:
                        key = normal(field[1].strip("* "))
                        if key in FIELDS:
                            current.fields[key] = field[2].strip()
                    elif line.startswith("#"):
                        current = None
                    continue
            if number in seen:
                raise ValueError("Duplicate source entry number")
            seen.add(number)
            if source == "SERVER":
                fields = {
                    k: v for k, v in fields.items() if k in {"dienst", "status", "letzte pruefung"}
                }
            else:
                fields = {k: v for k, v in fields.items() if k in FIELDS}
            current = Row(number, source, str(path), line_no, fields)
            rows.append(current)
    # Enforce the SERVER allowlist for block fields as well.
    for row in rows:
        if row.source == "SERVER":
            row.fields = {
                k: v for k, v in row.fields.items() if k in {"dienst", "status", "letzte pruefung"}
            }
    return rows


def generate(rows, day):
    result = []
    by_id = {row.number: row for row in rows}

    def emit(kind, refs, question, option=""):
        if len(question) > 200 or not all(safe(v) for r in refs for v in r.fields.values() if v):
            return
        identity = "|".join([kind, *(r.number for r in refs), option])
        result.append(
            {
                "id": "WB-" + hashlib.sha256(identity.encode()).hexdigest()[:24],
                "art": kind,
                "frage": question,
                "fundstelle": [r.reference() for r in refs],
                "datum": day,
                "bezug": refs[0].number,
                "status": "neu",
                "nicht_gewaehlte_option": option,
                "quelle_liste": sorted({r.source for r in refs}),
                "hinweis": "Frage, keine Tatsache. Quelle vor jeder Verwendung selbst pruefen.",
            }
        )

    groups = defaultdict(list)
    pairs = set()
    for row in rows:
        if row.source == "SERVER":
            continue
        text = row.fields.get("optionen/empfehlung", "")
        match = re.search(r"\bOption 2\s*[:=–-]?\s*(.+)", text, re.I)
        option = row.fields.get("option 2", "") or (match[1].strip() if match else "")
        if option and safe(option):
            question = (
                f"Waere denkbar, dass die zweite Option aus {row.number} "
                "erneut geprueft werden sollte?"
            )
            emit("nicht_gewaehlte_option", [row], question, option)
        refs = set(ID.findall(" ".join(row.fields.values()))) - {row.number}
        for number in sorted(refs):
            if number not in by_id:
                continue
            pair = tuple(sorted((row.number, number)))
            if pair in pairs:
                continue
            pairs.add(pair)
            emit(
                "verbindung",
                [row, by_id[number]],
                f"Waere denkbar, dass {number} fuer {row.number} eine Rolle spielt?",
            )
        topic = normal(row.fields.get("thema") or row.fields.get("titel", ""))
        project = normal(row.fields.get("projekt", ""))
        if not topic:
            continue
        if row.source == "ENTSCHEIDE":
            groups[(row.source, project, topic)].append(row)
        elif normal(row.fields.get("ablauf-status", "")) == "blockiert":
            feedback = normal(row.fields.get("ruecklauf", ""))
            if feedback:
                groups[(row.source, project, topic, feedback)].append(row)
    for group in groups.values():
        if len(group) >= 2:
            emit(
                "wiederholung",
                group,
                f"Waere denkbar, dass die {len(group)} Eintraege zum Thema von "
                f"{group[0].number} gemeinsam geprueft werden sollten?",
            )
    return result
