# Etappe 06 - Pseudonymisierung und Rueckabbildung

Status: startklar, sobald VB-1, VB-3, VB-4, VB-6 erfuellt und Etappe 5 gemergt ist
Branch-Vorschlag: `stage/06-pseudonymisierung`
Abhaengig von: VB-1, VB-3, VB-4, VB-6, Etappe 5 (Vertrag `ScanHit`)
Entscheidungen: E2, E3, E8, E12 (Empfehlungen gelten als Default)
Reviewer: SuperGrok (nur Code). Plan v1.1 nannte Claude (gerade Etappe); seit dem Nachtrag zu ADR 0008 vom 2026-11-11 gilt das nicht mehr.
Abnahme laut Plan v1.1: Roundtrip verlustfrei; Map nur lokal.

## 1 Ziel

Vor jedem Cloud-Lauf mit `data_class = cloud_redacted` werden personenbezogene und interne Werte
durch stabile Platzhalter ersetzt. Die Zuordnung bleibt ausschliesslich lokal (Tabelle
`redaction_maps`). Nach dem Lauf werden die Platzhalter in der Ausgabe host-seitig zurueckgebildet.
Was den Server verlaesst, liegt revisionssicher als `payload.sent.md` im Laufverzeichnis; der
SHA-256 dieser Datei ist `result.payload_hash`. `cloud_ok` geht roh (nur ohne Schluessel-Treffer),
`local_only` erreicht nie eine Cloud-Lane.

## 2 Ausgangslage

| Punkt | Stand | Beleg |
|-------|-------|-------|
| `result.v1.redaction` = `{applied, placeholders}`, `payload_hash` SHA-256 hex, `log_ref` | belegt | `docs/SCHEMA.md`, `app/core/schemas.py` Klasse `RedactionInfo` (ab Zeile 196) |
| `ErrorCode` enthaelt `policy_blocked`, `acceptance_failed`, `internal` | belegt | `app/core/schemas.py` ab Zeile 70 |
| `DataClass` = `local_only`, `cloud_redacted`, `cloud_ok`, kein Default | belegt | `app/core/schemas.py` ab Zeile 38; ADR 0005 |
| Uebergang `running → blocked` ueber Trigger `policy_block` | belegt | `app/db/state_machine.py` (Tabelle `_TRANSITION_TABLE`) |
| Tabellen `tasks`, `results`, `events`, `leases` | belegt | `app/db/models.py`; `docs/DATENBANK.md` |
| PR #5 ergaenzt `tasks.plan`, `tasks.lane`, `cancel_requested`, Events mit `actor`, `attempt`, `lease_id` | Annahme (Diff-Auszug, nicht gemergt) | PR #5 |
| `app/core/paths.py` fuer sichere relative Pfade | Annahme (aus PR #5) | VB-6 |
| `.gitignore` enthaelt `runs/`, `config/blocklist*`, `inbox/` | belegt | `.gitignore` |
| `Executor.execute(task) -> ExecutionResult`, `FakeExecutor` deterministisch | belegt | `app/reference/runner.py` Zeilen 31 bis 43 |
| Scanner liefert `ScanHit` mit `severity` | Vertrag | Etappe 5, README |

## 3 Scope

**In:** Redaktions-Engine (Ersetzen, Rueckabbilden, Residualpruefung), Payload-Builder mit
`payload.sent.md`, Tabelle `redaction_maps` plus Migration, Repository-Funktionen, Anbindung an den
Referenz-Runner (Fake-Pfad), Unit- und Integrationstests, `docs/REDAKTION.md`.

**Out:** Scanner-Katalog und Blockliste (Etappe 5), Runner-Schleife und `AttemptContext` (Etappe 8),
echte Lanes (9, 12, 13), UI-Anzeige der Rehydrierung (11), Aufbewahrung und Loeschung (16),
Verschluesselung der Map (Alternative zu E2), NER-Modelle (Alternative zu E3), Anhang-Extraktion (5).

## 4 Default-Entscheidungen

| Nr | Entscheidung | Begruendung |
|----|--------------|-------------|
| D1 | Nur Standardbibliothek: `re`, `ipaddress`, `hashlib`, `secrets`. Keine neue Abhaengigkeit. | AGENTS.md |
| D2 | Kinds, die ersetzt werden: `EMAIL`, `PHONE`, `IBAN`, `AHV`, `IP`, `TERM` (Scanner-`severity = redact`). Kinds mit `severity = block` werden nie ersetzt, sie blockieren bei `cloud_redacted` und `cloud_ok`. | E12 |
| D3 | Platzhalter `[[KIND_N]]`; `N` je Kind ab 1 in Reihenfolge des ersten Auftretens; gleicher Originalwert → gleicher Platzhalter. | lesbar, eindeutig |
| D4 | Map ist **je Task** stabil (Plan: "deterministische Map je Task"): Versuch 2 verwendet die Platzhalter aus Versuch 1 wieder; neue Werte zaehlen weiter. | Vergleichbarkeit ueber Versuche |
| D5 | Idempotenz: bereits vorhandene Platzhalter im Text werden nicht ersetzt und nicht neu nummeriert; Nummerierung beginnt nach der hoechsten vorhandenen Nummer je Kind (Map und Text). | Retry-sicher |
| D6 | Residualpruefung: nach dem Ersetzen laeuft `scan()` erneut; jeder Treffer mit `severity` `redact` oder `block` → `blocked` (`policy_blocked`). Nie stilles Weiterleiten. | Plan Abschnitt 7 |
| D7 | Zeitpunkt: Payload-Bau nach `start` im Zustand `running`; Blockade nutzt `policy_block` aus `running`. Kein neuer Uebergang `leased → blocked`. | vorhandene Zustandstabelle |
| D8 | Fehlermeldung bei Blockade nennt nur Kinds und Anzahl, nie Werte. | Leckschutz |
| D9 | Rueckabbildung nur host-seitig; unbekannter Platzhalter in der Ausgabe → `result.status = failed`, `error.code = acceptance_failed`, `message` nennt den Platzhalter (Etappe 10 fuehrt nach `review`). | Plan Fehlertabelle |
| D10 | `payload.sent.md` atomar schreiben (Tempdatei im selben Verzeichnis, `os.replace`), Verzeichnis `0700`, Datei `0600`, UTF-8, LF. Pfad ausschliesslich ueber `app/core/paths.py`. | ADR 0003, VB-6 |
| D11 | `cloud_ok`: keine Ersetzung, Map leer, `redaction.applied = false`; `payload.sent.md` wird trotzdem geschrieben; `block`-Treffer blockieren. | Plan Abschnitt 7.1 |
| D12 | `local_only`: Builder verweigert jede Lane ausser `ollama` (`LaneNotAllowedError`, Programmfehler, nicht Policy); kein Cloud-Scan noetig, `payload.sent.md` wird geschrieben. | ADR 0005 |
| D13 | Map liegt im Klartext in `redaction_maps` (E2); nie in `result.v1`, nie in `payload.sent.md`, nie in Logs, nie in Events. | E2 |
| D14 | Testdaten synthetisch: E-Mail nur Beispiel-Domains, IP nur Dokumentationsbereiche (RFC 5737, RFC 3849), IBAN und AHV mit gueltiger Pruefziffer aus einem Generator in `tests/conftest.py`, Telefon frei erfunden. Keine echten Daten. | E8, AGENTS.md |
| D15 | Ueberlappende Treffer: laengster Treffer gewinnt, Ersetzung von hinten nach vorne. | Determinismus |

## 5 Umsetzung

### 5.1 Dateien

| Pfad | Aenderung |
|------|-----------|
| `app/core/redaction/__init__.py` | Export der oeffentlichen Funktionen |
| `app/core/redaction/placeholders.py` | Regex `\[\[(EMAIL\|PHONE\|IBAN\|AHV\|IP\|TERM)_(\d+)\]\]`, `format_placeholder`, `parse_placeholders` |
| `app/core/redaction/engine.py` | `redact`, `rehydrate`, Datenklassen `RedactionOutcome`, `RehydrateOutcome`, Fehler `RedactionBlockedError` |
| `app/workers/payload.py` | `build_payload`, `render_payload_markdown`, `write_payload_sent`, Fehler `LaneNotAllowedError` |
| `app/db/models.py` | Klasse `RedactionMap` (Tabelle `redaction_maps`) |
| `app/db/redaction_repo.py` | `load_task_map`, `store_task_map` |
| `alembic/versions/<datum>_0004_redaction_maps.py` | Tabelle, Indizes, Fremdschluessel; Downgrade loescht Tabelle |
| `app/reference/runner.py` | Hook: fuer `cloud_redacted` wird vor `execute` redigiert und danach rueckabgebildet; `FakeExecutor` sieht nur den redigierten Text |
| `tests/unit/test_redaction_engine.py`, `tests/unit/test_payload_builder.py` | Unit-Tests |
| `tests/unit/examples/redaction/*.json` | Golden-Faelle: `input`, `expected_text`, `expected_mapping` |
| `tests/integration/test_redaction_maps.py` | DB-Tests |
| `tests/reference/cases/005-normal-cloud-redacted-roundtrip.yaml`, `006-trap-cloud-redacted-secret.yaml` | Referenzfaelle |
| `docs/REDAKTION.md` (neu), `docs/DATENBANK.md`, `docs/ARCHITECTURE.md` | Doku; Datenfluss Schritt 5 verweist auf `docs/REDAKTION.md` |

### 5.2 Schnittstellen (Python, Signaturen verbindlich)

```text
# app/core/redaction/engine.py
@dataclass(frozen=True)
class RedactionOutcome:
    text: str
    mapping: dict[str, str]        # placeholder -> original (nur neue und verwendete)
    kinds: dict[str, int]          # kind -> Anzahl Platzhalter im Text
    applied: bool

def redact(text: str, *, hits: list[ScanHit], existing: Mapping[str, str]) -> RedactionOutcome: ...
def rehydrate(text: str, mapping: Mapping[str, str]) -> RehydrateOutcome: ...   # .text, .unknown: list[str]
class RedactionBlockedError(Exception): kinds: dict[str, int]                 # keine Werte

# app/workers/payload.py
@dataclass(frozen=True)
class PayloadBuild:
    text: str; sha256: str; applied: bool; placeholders: int; mapping: dict[str, str]
def build_payload(task: TaskV1, *, lane: str, context_blocks: Sequence[str], existing: Mapping[str, str]) -> PayloadBuild: ...
def write_payload_sent(run_dir: Path, text: str) -> str: ...    # gibt sha256 zurueck
```

Ablauf in `build_payload`: Markdown rendern → `scan()` (Etappe 5) → bei `block`-Treffer und
Cloud-Klasse `RedactionBlockedError` → bei `cloud_redacted` `redact()` → Residual-`scan()` →
SHA-256. Der Rahmen (Etappe 8) schreibt vorher die Map-Zeilen (`store_task_map`), ruft
`write_payload_sent`, uebergibt `text` im `AttemptContext` und bildet nach dem Lauf mit
`load_task_map` zurueck. Bis Etappe 8 existiert, nutzt der Referenz-Runner denselben Weg ohne DB
(`existing = {}`).

### 5.3 Format `payload.sent.md`

```
# Auftrag <task_id> Versuch <attempt>
## Anweisung
<instructions>
## Eingaben
### <name> (<type>)
<value oder extrahierter Text aus Etappe 5>
## Kontext
<Bloecke aus Etappe 14; bis dahin leer>
```

Die Lane haengt ihre Prompt-Vorlage separat an (Etappe 9); Vorlagen-ID und Hash stehen im Lane-Log.

### 5.4 Datenmodell `redaction_maps`

| Spalte | Typ | Regel |
|--------|-----|-------|
| `id` | uuid | Primaerschluessel |
| `task_id` | uuid | FK `tasks.id`, `ON DELETE RESTRICT` |
| `placeholder` | text | z. B. `[[EMAIL_1]]` |
| `kind` | text | CHECK in den sechs Kinds |
| `original` | text | Klartext (E2) |
| `first_attempt` | int | Versuch, in dem der Wert zuerst auftrat |
| `created_at` | timestamptz | Default `now()` |

Constraints: `UNIQUE(task_id, placeholder)`, `UNIQUE(task_id, kind, original)`, Index `task_id`.
Keine Spalte in `results` oder `tasks` wird geaendert (Schema v1 bleibt unveraendert).

## 6 Abnahmetests

| Nr | Fall | Erwartet |
|----|------|----------|
| T1 | Golden-Faelle je Kind (E-Mail, Telefon CH und DE, IBAN, AHV, IPv4 und IPv6, Blocklisten-Begriff) | `expected_text` und `expected_mapping` exakt |
| T2 | Gleicher Wert dreimal im Text | ein Platzhalter, dreimal verwendet, `placeholders == 1` |
| T3 | Idempotenz: `redact(redact(x).text)` | Text unveraendert, keine neuen Platzhalter |
| T4 | Roundtrip: `rehydrate(redact(x).text, mapping).text == x` fuer alle Golden-Faelle | verlustfrei (Plan-Abnahme) |
| T5 | Versuch 2 mit `existing` aus Versuch 1 plus neuem Wert | alte Platzhalter identisch, neuer Wert erhaelt naechste Nummer |
| T6 | Schluessel-Treffer (`severity = block`) in `cloud_redacted` und `cloud_ok` | `RedactionBlockedError`, Kinds genannt, kein Wert in der Meldung |
| T7 | Residualtreffer (Scanner-Stub meldet Treffer nach Ersetzung) | blockiert |
| T8 | `cloud_ok` mit E-Mail | Text unveraendert, `applied = false`, `payload.sent.md` geschrieben |
| T9 | `local_only` mit Lane `claude` | `LaneNotAllowedError` |
| T10 | Leck-Test: kein `original` aus der Map erscheint in `payload.sent.md`, im Log-Text oder in `str(RedactionBlockedError)` | 0 Treffer |
| T11 | SHA-256 der geschriebenen Datei == `PayloadBuild.sha256` == spaeter `result.payload_hash` | gleich |
| T12 | Unbekannter Platzhalter in der Ausgabe | `RehydrateOutcome.unknown == ["[[EMAIL_9]]"]` |
| T13 | Atomares Schreiben: Fehler vor `os.replace` hinterlaesst keine Zieldatei; Rechte `0600`/`0700` | geprueft |
| T14 | Integration: `store_task_map` zweimal fuer denselben Task | keine Duplikate, `UNIQUE` greift, Zeilen unveraendert |
| T15 | Integration: Loeschen eines Tasks mit Map | `RESTRICT` verhindert Loeschung |
| T16 | Referenzfall 005: `FakeExecutor` erhaelt nur redigierten Text (`must_not_contain` Originalwert), Ausgabe rueckabgebildet | pass |
| T17 | Referenzfall 006: Platzhalter-Schluessel mit `cloud_redacted` | `blocked` |
| T18 | Pfad-Traversal in `task_id` oder `attempt` beim Laufverzeichnis | abgewiesen durch `app/core/paths.py` |

## 7 Definition of Done

- `make lint` und `make test` gruen; Integrationstests gegen `DECK_TEST_DATABASE_URL`; `alembic upgrade head` und `alembic downgrade -1` sauber; `alembic check` leer.
- `make reference` gruen inklusive Faelle 005 und 006.
- `docs/REDAKTION.md` beschreibt Kinds, Platzhalter, Ablauf, Leckschutz; `docs/DATENBANK.md` fuehrt `redaction_maps`.
- PR-Template ausgefuellt: Etappe `stage/06-pseudonymisierung`, Reviewer **SuperGrok**, Abschnitt "Bewusst offen" aus Abschnitt 10 uebernommen, "Secrets/PII geprueft" mit Hinweis auf synthetische Fixtures.
- Kein Modellaufruf, keine Netzverbindung in Tests.

## 8 Blocker-Regel

Der Builder stoppt und meldet im PR (Draft) statt zu bauen, wenn:
- Etappe 5 nicht gemergt ist oder `ScanHit` kein Feld `severity` hat;
- der Zustandsautomat auf `main` kein `policy_block` aus `running` kennt oder `done`/`review` fehlen (VB-3);
- `app/core/paths.py` fehlt (VB-6);
- Schema v1 geaendert werden muesste;
- der Owner E2 mit "verschluesselt" beantwortet hat (dann zuerst Etappe 15).

## 9 Risiken und Gegenmassnahmen

| Risiko | Gegenmassnahme |
|--------|----------------|
| Musterbasierte Erkennung uebersieht Werte | Blockliste (E3), Residualpruefung, `review` als Standard, `payload.sent.md` als Beweis |
| Platzhalter verwirren kleine Modelle | Prompt-Vorlage erklaert Platzhalter (Etappe 9); Abnahme-Checks |
| Modell veraendert Platzhalter (z. B. `[[EMAIL 1]]`) | Rueckabbildung tolerant nur fuer Leerzeichen und Gross/Klein; sonst `acceptance_failed` |
| Map im Klartext | gleiche Schutzklasse wie `tasks.payload`; Aufbewahrung Etappe 16; E2 |
| Datei-Leck durch Logs | Log enthaelt nie Payload- oder Ausgabetext; Leck-Test T10 |

## 10 Bewusst offen

- Verschluesselung der Map (E2 Alternative) und Loeschfristen (E11, Etappe 16).
- Anhang-Extraktion und Scanner-Katalog liegen in Etappe 5; die Kinds dort muessen `severity` tragen (E12).
- Kontextbloecke aus der Wissens-DB (Etappe 14) laufen durch denselben Builder; Klassenfilter dort.
- UI-Kennzeichnung rueckabgebildeter Stellen (Etappe 11).
