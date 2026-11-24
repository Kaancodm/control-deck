"""Compare a saved Hindsight OpenAPI document with the assumed contract (F2).

Offline only: the file is fetched separately by a read-only GET. Usage:

    python3 -B -m weitblick.vertrag /pfad/zur/hindsight-openapi.json

Prints only paths, field names, types and status codes from the file.
Exit 0 when every assumption holds, 1 on a deviation, 2 on an unreadable file.
"""

import json
import re
import sys

from .hindsight import HINDSIGHT_VERSION, RETAIN_ALLOWED_TOP_FIELDS, RETAIN_TOP_FIELDS

PARAM = re.compile(r"\{[^}]+\}")
RETAIN = ("post", "/v1/default/banks/{}/memories")
DOCUMENT_GET = ("get", "/v1/default/banks/{}/documents/{}")
DOCUMENT_DELETE = ("delete", "/v1/default/banks/{}/documents/{}")
RECALL = ("post", "/v1/default/banks/{}/memories/recall")
RETAIN_ITEM_FIELDS = ("content", "document_id", "metadata", "context")


def normalise(path):
    return PARAM.sub("{}", path)


def resolve(spec, schema, depth=0):
    """Follow local $ref and merge allOf/anyOf/oneOf into one property view."""
    if not isinstance(schema, dict) or depth > 8:
        return {}
    if "$ref" in schema:
        node = spec
        for part in schema["$ref"].lstrip("#/").split("/"):
            node = node.get(part, {}) if isinstance(node, dict) else {}
        return resolve(spec, node, depth + 1)
    merged = dict(schema)
    properties = dict(schema.get("properties", {}))
    for key in ("allOf", "anyOf", "oneOf"):
        for part in schema.get(key, []):
            resolved = resolve(spec, part, depth + 1)
            properties.update(resolved.get("properties", {}))
            if "type" in resolved and "type" not in merged:
                merged["type"] = resolved["type"]
            if "enum" in resolved:
                merged.setdefault("enum", resolved["enum"])
            if "items" in resolved:
                merged.setdefault("items", resolved["items"])
    merged["properties"] = properties
    return merged


def operations(spec):
    found = {}
    for path, item in spec.get("paths", {}).items():
        if not isinstance(item, dict):
            continue
        for method, operation in item.items():
            if isinstance(operation, dict):
                found[(method.lower(), normalise(path))] = (path, operation)
    return found


def request_schema(spec, operation):
    content = operation.get("requestBody", {}).get("content", {})
    media = content.get("application/json", {})
    return resolve(spec, media.get("schema", {}))


def check(spec):
    """Return (ok, lines)."""
    lines = []
    ok = True

    def bad(text):
        nonlocal ok
        ok = False
        lines.append("ABWEICHUNG: " + text)

    version = str(spec.get("info", {}).get("version", ""))
    if version == HINDSIGHT_VERSION:
        lines.append(f"OK: Version {version}")
    else:
        bad(f"Version {version or '(fehlt)'} statt {HINDSIGHT_VERSION}")

    ops = operations(spec)
    for name, key in (
        ("Retain", RETAIN),
        ("Lesen nach ID", DOCUMENT_GET),
        ("Loeschen nach ID", DOCUMENT_DELETE),
        ("Suche (T5)", RECALL),
    ):
        if key in ops:
            lines.append(f"OK: {name} {key[0].upper()} {ops[key][0]}")
        else:
            bad(f"{name} {key[0].upper()} {key[1]} nicht gefunden")

    if RETAIN in ops:
        schema = request_schema(spec, ops[RETAIN][1])
        top = schema.get("properties", {})
        for field in RETAIN_TOP_FIELDS:
            if field not in top:
                bad(f"Retain-Feld '{field}' fehlt im Request-Schema")
        if "mode" in top:
            lines.append(
                "INFO: Retain-Feld 'mode' steht im Schema, ist in v1.2.1 aber kein Request-Feld"
            )
        unknown = sorted(
            name for name in top if name not in RETAIN_ALLOWED_TOP_FIELDS and name != "mode"
        )
        if unknown:
            lines.append("INFO: zusaetzliche Retain-Felder: " + ", ".join(unknown))
        present = [field for field in RETAIN_TOP_FIELDS if field in top]
        if present:
            lines.append("OK: Retain-Felder " + ", ".join(present))
        item = resolve(spec, resolve(spec, top.get("items", {})).get("items", {}))
        item_fields = item.get("properties", {})
        for field in RETAIN_ITEM_FIELDS:
            if field not in item_fields:
                bad(f"Retain-Item-Feld '{field}' fehlt")
        doc_type = resolve(spec, item_fields.get("document_id", {})).get("type")
        lines.append(f"INFO: document_id im Retain-Item hat Typ {doc_type or 'unbekannt'}")

    for name, key in (("Lesen", DOCUMENT_GET), ("Loeschen", DOCUMENT_DELETE)):
        if key not in ops:
            continue
        path, operation = ops[key]
        params = [p for p in operation.get("parameters", []) if isinstance(p, dict)]
        id_type = None
        for param in params:
            if param.get("in") == "path" and "document" in param.get("name", ""):
                id_type = resolve(spec, param.get("schema", {})).get("type")
        lines.append(f"INFO: {name} ID-Parameter Typ {id_type or 'unbekannt'}")
        codes = sorted(str(code) for code in operation.get("responses", {}))
        lines.append(f"INFO: {name} dokumentierte Status {', '.join(codes) or 'keine'}")
        if not {"403", "404"} & set(codes):
            lines.append(f"HINWEIS: {name} dokumentiert weder 403 noch 404; S-K80 entscheidet")

    chunk_paths = sorted({path for (_m, _n), (path, _o) in ops.items() if "chunk" in path.lower()})
    for path in chunk_paths:
        scope = "bankgebunden" if "{bank" in path or "/banks/" in path else "NICHT bankgebunden"
        lines.append(f"INFO: Chunk-Pfad {path} ({scope})")
    return ok, lines


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1:
        print("Aufruf: python3 -B -m weitblick.vertrag <openapi.json>", file=sys.stderr)
        return 2
    try:
        with open(argv[0], encoding="utf-8") as stream:
            spec = json.load(stream)
        if not isinstance(spec, dict):
            raise ValueError
    except (OSError, ValueError):
        print("Vertrag: Datei nicht lesbar oder kein JSON-Objekt", file=sys.stderr)
        return 2
    ok, lines = check(spec)
    for line in lines:
        print(line)
    print("Vertrag: passt" if ok else "Vertrag: Abweichung, Adapter vor Betrieb anpassen")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
