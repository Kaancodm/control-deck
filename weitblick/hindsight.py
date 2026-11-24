"""Second writer: mirror validated Weitblick entries into the Hindsight bank.

Standard library only. The Stage 1 JSONL file stays authoritative; this
writer never opens it for writing.

API CONTRACT STATUS (F2, see README "Weitblick Stufe 2"):
Locally evidenced is only Hindsight (vectorize-io, MIT), release v1.2.1, and a
per-bank MCP path. The HTTP paths, the request field for the retain mode, the
document ID semantics and the error form below are ASSUMPTIONS until Atik's
read-only server check confirms them. They are kept in this one module on
purpose. Offline tests prove what this client sends, not what Hindsight accepts.
"""

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from http.client import HTTPException
from pathlib import Path

from .writer import (
    STATUS_NAME,
    locked_output,
    read_text,
    replace_atomically,
    select_new,
    validate,
)

PRODUCTION_BANK = "weitblick"
HINDSIGHT_VERSION = "1.2.1"
RETAIN_MODE = "chunks"

ENV_ENABLED = "WEITBLICK_HINDSIGHT_ENABLED"
ENV_URL = "WEITBLICK_HINDSIGHT_URL"
ENV_TOKEN = "WEITBLICK_HINDSIGHT_TOKEN"
ENV_TIMEOUT = "WEITBLICK_HINDSIGHT_TIMEOUT_S"
DEFAULT_TIMEOUT_S = 10.0

ON_VALUES = {"1", "true", "ja", "yes", "on"}
OFF_VALUES = {"", "0", "false", "nein", "no", "off"}

CONFIRMED = "bestaetigt"
FAILED = "fehlgeschlagen"
UNCLEAR = "unklar"

# Fields released for the bank. `fundstelle[].datei` holds a private absolute
# path and is never sent (F4 open: until Atik decides, paths stay local).
BANK_FIELDS = (
    "id",
    "art",
    "frage",
    "datum",
    "bezug",
    "status",
    "nicht_gewaehlte_option",
    "quelle_liste",
    "hinweis",
)
BANK_REFERENCE_FIELDS = ("nummer", "zeile")


class HindsightConfigError(ValueError):
    """Configuration problem; the message names variables, never their values."""


@dataclass(frozen=True)
class HindsightConfig:
    url: str
    token: str
    timeout: float = DEFAULT_TIMEOUT_S

    def __repr__(self):
        # Never reveal URL or token in tracebacks or logs.
        return "HindsightConfig(url=<gesetzt>, token=<gesetzt>)"


def is_enabled(environ):
    value = environ.get(ENV_ENABLED, "").strip().lower()
    if value in ON_VALUES:
        return True
    if value in OFF_VALUES:
        return False
    raise HindsightConfigError(f"{ENV_ENABLED} muss 1 oder 0 sein")


def config_from_env(environ):
    """Return None when Hindsight is switched off, else a checked config."""
    if not is_enabled(environ):
        return None
    missing = [name for name in (ENV_URL, ENV_TOKEN) if not environ.get(name, "").strip()]
    if missing:
        raise HindsightConfigError(
            "Hindsight ist eingeschaltet, aber es fehlen: " + ", ".join(missing)
        )
    url = environ[ENV_URL].strip().rstrip("/")
    parsed = urllib.parse.urlsplit(url)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise HindsightConfigError(
            f"{ENV_URL} muss eine http(s)-Basisadresse ohne Zugangsdaten, Query oder Fragment sein"
        )
    token = environ[ENV_TOKEN].strip()
    if any(ord(c) < 33 or ord(c) > 126 for c in token):
        raise HindsightConfigError(f"{ENV_TOKEN} enthaelt unzulaessige Zeichen")
    raw_timeout = environ.get(ENV_TIMEOUT, "").strip()
    try:
        timeout = float(raw_timeout) if raw_timeout else DEFAULT_TIMEOUT_S
    except ValueError:
        raise HindsightConfigError(f"{ENV_TIMEOUT} muss eine Zahl sein") from None
    if not 0 < timeout <= 120:
        raise HindsightConfigError(f"{ENV_TIMEOUT} muss zwischen 0 und 120 liegen")
    return HindsightConfig(url=url, token=token, timeout=timeout)


def bank_record(entry):
    """Project an entry onto the released bank fields (no file paths)."""
    record = {name: entry[name] for name in BANK_FIELDS}
    record["fundstelle"] = [
        {name: ref[name] for name in BANK_REFERENCE_FIELDS} for ref in entry["fundstelle"]
    ]
    return record


def bank_content(record):
    """Plain text chunk stored in the bank, built only from released fields."""
    refs = ", ".join(f"{r['nummer']} Zeile {r['zeile']}" for r in record["fundstelle"])
    lines = [
        f"Weitblick-Frage {record['id']}: {record['frage']}",
        f"Art: {record['art']}",
        f"Bezug: {record['bezug']}",
        f"Fundstellen: {refs}",
        f"Listen: {', '.join(record['quelle_liste'])}",
        f"Datum: {record['datum']}",
        f"Status: {record['status']}",
    ]
    if record["nicht_gewaehlte_option"]:
        lines.append(f"Nicht gewaehlte Option: {record['nicht_gewaehlte_option']}")
    lines.append(f"Hinweis: {record['hinweis']}")
    return "\n".join(lines)


def bank_metadata(record):
    # Assumption F2: metadata values are strings; the full record travels as JSON.
    return {
        "quelle": "weitblick",
        "schema": "weitblick-bank-v1",
        "weitblick_id": record["id"],
        "eintrag": json.dumps(record, ensure_ascii=False, sort_keys=True),
    }


# --- Assumed HTTP contract (F2). Change only together with the README. ---


def _bank_segment(bank):
    return "/v1/default/banks/" + urllib.parse.quote(bank, safe="")


def retain_path(bank):
    return _bank_segment(bank) + "/memories"


def document_path(bank, document_id):
    return _bank_segment(bank) + "/documents/" + urllib.parse.quote(document_id, safe="")


def recall_path(bank):
    return _bank_segment(bank) + "/memories/recall"


def retain_body(document_id, content, metadata):
    return {
        "items": [
            {
                "content": content,
                "document_id": document_id,
                "context": "weitblick",
                "metadata": metadata,
            }
        ],
        "async": False,
        "mode": RETAIN_MODE,
    }


# --- Transport boundary ---


@dataclass(frozen=True)
class Response:
    status: int
    body: bytes

    def json(self):
        try:
            return json.loads(self.body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            return None


class TransportError(Exception):
    """No HTTP status was received; the outcome on the server is unknown."""


class _RefuseRedirect(urllib.request.HTTPRedirectHandler):
    # A redirect would forward the Authorization header; treat it as a response.
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def default_opener():
    # Empty ProxyHandler: environment proxies never see the token.
    return urllib.request.build_opener(urllib.request.ProxyHandler({}), _RefuseRedirect())


class UrllibTransport:
    def __init__(self, timeout=DEFAULT_TIMEOUT_S, opener=None):
        self.timeout = timeout
        self.opener = opener or default_opener()

    def request(self, method, url, headers, body=None):
        request = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with self.opener.open(request, timeout=self.timeout) as reply:
                return Response(reply.status, reply.read())
        except urllib.error.HTTPError as error:
            try:
                payload = error.read()
            finally:
                error.close()
            return Response(error.code, payload or b"")
        except (OSError, HTTPException) as error:
            # URLError, timeouts, resets. Keep the type only, never the URL.
            raise TransportError(type(error).__name__) from None


class HindsightClient:
    """Bank-scoped calls. `bank` is explicit so S-K80 can address test banks."""

    def __init__(self, config, transport=None):
        self.config = config
        self.transport = transport or UrllibTransport(config.timeout)

    def _call(self, method, path, payload=None):
        body = None
        headers = {
            "Authorization": "Bearer " + self.config.token,
            "Accept": "application/json",
        }
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
            headers["Content-Type"] = "application/json"
        return self.transport.request(method, self.config.url + path, headers, body)

    def retain(self, bank, document_id, content, metadata):
        return self._call("POST", retain_path(bank), retain_body(document_id, content, metadata))

    def get_document(self, bank, document_id):
        return self._call("GET", document_path(bank, document_id))

    def delete_document(self, bank, document_id):
        return self._call("DELETE", document_path(bank, document_id))

    def recall(self, bank, query):
        return self._call("POST", recall_path(bank), {"query": query, "max_tokens": 512})


def classify(response):
    """Map a retain response to bestaetigt, fehlgeschlagen or unklar."""
    if 200 <= response.status < 300:
        data = response.json()
        if not isinstance(data, dict) or data.get("success") is False:
            return UNCLEAR
        return CONFIRMED
    if response.status in (408, 425, 429) or response.status >= 500:
        return UNCLEAR
    return FAILED


def private_values(entry):
    values = set()
    for ref in entry["fundstelle"]:
        path = ref["datei"]
        values.add(path)
        values.add(json.dumps(path, ensure_ascii=False)[1:-1])
        values.add(json.dumps(path, ensure_ascii=True)[1:-1])
        values.add(urllib.parse.quote(path, safe=""))
    return {v for v in values if v}


def contains_private_value(entry, serialized):
    return any(value in serialized for value in private_values(entry))


class HindsightWriter:
    """Writer.write(entries) -> int: number of entries newly confirmed by Hindsight.

    Budget: the Stage 1 rule (`select_new`) runs over the IDs already reserved
    in `hindsight-status.json`. A reserved ID keeps its slot until confirmed,
    so retries never let more than five entries in total or three per day
    reach the bank, also across repeated nightly runs.

    Retry rule: entries recorded as `fehlgeschlagen` or `unklar` are sent again
    on the next run with the same document ID. Confirmed entries are never resent.
    The JSONL file is neither read for writing nor changed here.
    """

    def __init__(self, output, client):
        self.output = Path(output).absolute()
        if any(p.is_symlink() for p in (self.output, *self.output.parents)):
            raise ValueError("Output symlinks are forbidden")
        self.client = client
        self.bank = PRODUCTION_BANK
        self.last_result = {CONFIRMED: 0, FAILED: 0, UNCLEAR: 0, "bereits": 0}

    def _load_status(self, directory):
        text = read_text(directory, STATUS_NAME)
        if text is None:
            return {}
        data = json.loads(text)
        if (
            not isinstance(data, dict)
            or data.get("bank") != self.bank
            or not isinstance(data.get("eintraege"), dict)
        ):
            raise ValueError("Invalid Hindsight status file")
        for value in data["eintraege"].values():
            if value.get("status") not in {CONFIRMED, FAILED, UNCLEAR} or not value.get("datum"):
                raise ValueError("Invalid Hindsight status file")
        return data["eintraege"]

    def _save_status(self, directory, status):
        content = json.dumps(
            {"bank": self.bank, "version": 1, "eintraege": status},
            ensure_ascii=True,
            indent=1,
            sort_keys=True,
        )
        replace_atomically(directory, STATUS_NAME, content + "\n")
        os.fsync(directory)

    def _send(self, entry):
        record = bank_record(entry)
        content = bank_content(record)
        metadata = bank_metadata(record)
        probe = json.dumps(retain_body(entry["id"], content, metadata), ensure_ascii=False)
        if contains_private_value(entry, probe) or contains_private_value(entry, entry["id"]):
            # Defence in depth: refuse locally, nothing leaves the machine.
            return FAILED
        try:
            response = self.client.retain(self.bank, entry["id"], content, metadata)
        except TransportError:
            return UNCLEAR
        return classify(response)

    def write(self, entries):
        for entry in entries:
            validate(entry)
        result = {CONFIRMED: 0, FAILED: 0, UNCLEAR: 0, "bereits": 0}
        self.last_result = result
        with locked_output(self.output) as directory:
            status = self._load_status(directory)
            reserved = [{"id": k, "datum": v["datum"]} for k, v in status.items()]
            by_id = {}
            for entry in entries:
                by_id.setdefault(entry["id"], entry)
            retry = [
                by_id[k]
                for k, v in sorted(status.items())
                if v["status"] != CONFIRMED and k in by_id
            ]
            result["bereits"] = sum(
                1 for k in by_id if k in status and status[k]["status"] == CONFIRMED
            )
            fresh = select_new(reserved, list(by_id.values()))
            for entry in fresh:
                # Reserve before sending: an interrupted run still keeps the budget.
                status[entry["id"]] = {"datum": entry["datum"], "status": UNCLEAR}
            if fresh:
                self._save_status(directory, status)
            for entry in retry + fresh:
                outcome = self._send(entry)
                status[entry["id"]] = {
                    "datum": entry["datum"],
                    "status": outcome,
                    "zeit": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                }
                self._save_status(directory, status)
                result[outcome] += 1
        return result[CONFIRMED]
