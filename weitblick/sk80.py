"""S-K80 bank separation check (mandatory negative test).

Offline the tests drive this module against an in-memory fake. On the server
Atik runs it once against two isolated test banks with synthetic data:

    python3 -B -m weitblick.sk80 --bank-a weitblick-sk80-a --bank-b weitblick-sk80-b

Writes: one synthetic entry Y into A and one synthetic entry X into B.
Deletes: none. The single delete call targets X in bank A and is expected
to be refused. Nothing is cleaned up. The production bank is refused.
Output: step names, HTTP status codes and verdicts only; never bodies, URL or token.

The read/delete paths and the document fields are assumptions (F2) from
weitblick.hindsight. A connection or general authentication error never
counts as proof of separation; the verdict is then "offen".
"""

import argparse
import os
import re
import sys
import uuid

from .hindsight import (
    CONFIRMED,
    ENV_TIMEOUT,
    ENV_TOKEN,
    ENV_URL,
    PRODUCTION_BANK,
    HindsightClient,
    HindsightConfigError,
    TransportError,
    classify,
    config_from_env,
)

PASSED = "bestanden"
FAILED = "nicht bestanden"
OPEN = "offen"

TEST_BANK = re.compile(r"weitblick-sk80-[a-z0-9-]{1,40}")
SEPARATION_STATUSES = {403, 404}
ID_KEYS = ("id", "document_id")
TEXT_KEYS = ("original_text", "content", "text")


def check_banks(bank_a, bank_b):
    for bank in (bank_a, bank_b):
        if bank == PRODUCTION_BANK or not TEST_BANK.fullmatch(bank):
            raise ValueError("Test-Banks muessen weitblick-sk80-<name> heissen")
    if bank_a == bank_b:
        raise ValueError("Bank A und Bank B muessen verschieden sein")


def synthetic(tag, letter):
    marker = f"SK80-{letter}-{tag}"
    return (
        f"sk80-{letter.lower()}-{tag}",
        f"Synthetischer Testeintrag {marker} fuer S-K80. Keine echten Daten.",
        marker,
    )


def extract_document(response):
    """Return (id, text) from a document response, or None if the form is unknown."""
    data = response.json()
    if not isinstance(data, dict):
        return None
    doc_id = next((data[k] for k in ID_KEYS if isinstance(data.get(k), str)), None)
    text = next((data[k] for k in TEXT_KEYS if isinstance(data.get(k), str)), None)
    if doc_id is None or text is None:
        return None
    return doc_id, text


def _call(step, function, *args):
    try:
        return function(*args), None
    except TransportError as error:
        return None, f"{step}: keine HTTP-Antwort ({error}); Verbindungsfehler beweist nichts"


def _refusal(step, response, payload_markers):
    """Judge a cross-bank call that must be refused without payload."""
    if response.status in SEPARATION_STATUSES:
        body = response.body.decode("utf-8", "replace")
        if any(marker in body for marker in payload_markers):
            return FAILED, f"{step}: HTTP {response.status}, Antwort enthaelt Nutzdaten von X"
        return PASSED, f"{step}: HTTP {response.status}, ohne Nutzdaten von X"
    if 200 <= response.status < 300:
        return FAILED, f"{step}: HTTP {response.status}, Erfolg statt Fehler"
    if response.status == 401:
        return OPEN, f"{step}: HTTP 401, allgemeiner Anmeldefehler beweist keine Banktrennung"
    return OPEN, f"{step}: HTTP {response.status}, nicht eindeutig (F2)"


def run_sk80(client, bank_a, bank_b, tag=None):
    """Run S-K80. Returns {"sk80": verdict, "t5": verdict, "schritte": [...]}."""
    check_banks(bank_a, bank_b)
    tag = tag or uuid.uuid4().hex[:12]
    if not re.fullmatch(r"[a-z0-9]{4,32}", tag):
        raise ValueError("Tag muss 4 bis 32 Kleinbuchstaben oder Ziffern haben")
    x_id, x_text, x_marker = synthetic(tag, "X")
    y_id, y_text, _ = synthetic(tag, "Y")
    steps = []
    report = {"sk80": OPEN, "t5": OPEN, "schritte": steps}

    def stop(note):
        steps.append(note)
        return report

    # Preparation: Y makes bank A exist, X is the probe in bank B.
    for step, bank, doc_id, text in (
        ("Vorbereitung Y in A", bank_a, y_id, y_text),
        ("Vorbereitung X in B", bank_b, x_id, x_text),
    ):
        response, error = _call(step, client.retain, bank, doc_id, text, {"quelle": "sk80"})
        if error:
            return stop(error)
        if classify(response) != CONFIRMED:
            return stop(f"{step}: HTTP {response.status}, nicht bestaetigt")
        steps.append(f"{step}: HTTP {response.status}, bestaetigt")

    response, error = _call("Lesen X aus B", client.get_document, bank_b, x_id)
    if error:
        return stop(error)
    baseline = extract_document(response) if 200 <= response.status < 300 else None
    if baseline is None or baseline[0] != x_id or x_marker not in baseline[1]:
        return stop(
            f"Lesen X aus B: HTTP {response.status}, X nicht nachgewiesen; ohne Nachweis beweist "
            "ein Fehler nichts"
        )
    steps.append(f"Lesen X aus B: HTTP {response.status}, X gelesen")

    markers = (x_marker, x_text)
    verdicts = []
    for step, function in (
        ("Lesen X aus A", client.get_document),
        ("Loeschen X aus A", client.delete_document),
    ):
        response, error = _call(step, function, bank_a, x_id)
        if error:
            verdicts.append(OPEN)
            steps.append(error)
            continue
        verdict, note = _refusal(step, response, markers)
        verdicts.append(verdict)
        steps.append(note)

    response, error = _call("Nachpruefung X in B", client.get_document, bank_b, x_id)
    if error:
        verdicts.append(OPEN)
        steps.append(error)
    else:
        after = extract_document(response) if 200 <= response.status < 300 else None
        if after == baseline:
            verdicts.append(PASSED)
            steps.append(f"Nachpruefung X in B: HTTP {response.status}, ID und Inhalt unveraendert")
        else:
            verdicts.append(FAILED)
            steps.append(f"Nachpruefung X in B: HTTP {response.status}, X fehlt oder veraendert")

    if FAILED in verdicts:
        report["sk80"] = FAILED
    elif all(v == PASSED for v in verdicts):
        report["sk80"] = PASSED

    # T5: searching bank A for the wording of X must not find X.
    response, error = _call("T5 Suche in A", client.recall, bank_a, x_text)
    if error:
        steps.append(error)
    elif 200 <= response.status < 300 and response.json() is not None:
        body = response.body.decode("utf-8", "replace")
        found = x_marker in body or x_id in body
        report["t5"] = FAILED if found else PASSED
        word = "gefunden" if found else "nicht gefunden"
        steps.append(f"T5 Suche in A: HTTP {response.status}, X {word}")
    else:
        steps.append(f"T5 Suche in A: HTTP {response.status}, nicht auswertbar (F2)")
    return report


def main(argv=None, environ=None, transport=None):
    parser = argparse.ArgumentParser(description="S-K80 Banktrennung mit Test-Banks")
    parser.add_argument("--bank-a", required=True)
    parser.add_argument("--bank-b", required=True)
    parser.add_argument("--tag", help="optional, sonst zufaellig")
    args = parser.parse_args(argv)
    environ = dict(os.environ if environ is None else environ)
    environ["WEITBLICK_HINDSIGHT_ENABLED"] = "1"
    try:
        check_banks(args.bank_a, args.bank_b)
        config = config_from_env(environ)
    except (HindsightConfigError, ValueError) as error:
        print(f"S-K80: Konfigurationsfehler: {error}", file=sys.stderr)
        print(f"Benoetigt: {ENV_URL}, {ENV_TOKEN} (optional {ENV_TIMEOUT})", file=sys.stderr)
        return 2
    report = run_sk80(HindsightClient(config, transport), args.bank_a, args.bank_b, args.tag)
    for line in report["schritte"]:
        print("- " + line)
    print(f"S-K80: {report['sk80']}")
    print(f"T5: {report['t5']}")
    return {PASSED: 0, FAILED: 1}.get(report["sk80"], 3)


if __name__ == "__main__":
    raise SystemExit(main())
