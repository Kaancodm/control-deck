# Etappen-Spezifikationen (Control Deck)

Stand: 03.11.2026. Vorab-Spezifikationen fuer Builder-Agents (Cursor), damit die aufwaendigen
Etappen ohne Rueckfragen gebaut werden koennen. Quelle der Etappenliste ist **Plan v1.1**
(Drive-Dokument "VON: Claude | Control Deck Plan v1.1 Architektur und 18 Etappen | 2026-11-03").
Die Architektur v0 ist veraltet und wird nicht verwendet.

Reviewer-Rotation laut Plan v1.1: ungerade Etappen **Codex**, gerade Etappen **Claude** (ADR 0008).
Builder ist immer Cursor. Merge nur nach GO des Owners.

## Stand der Etappen

| Etappe | Titel (Plan v1.1) | Stand | Reviewer | Spec |
|--------|-------------------|-------|----------|------|
| 1 | Grundgeruest, Schema v1, Validator | gemergt (PR #1, #3) | Codex | – |
| 2 | Referenz-Testset-Runner | gemergt (PR #2) | Claude | – |
| 3 | DB-Schema, Migrationen, Zustandsautomat, Events | gemergt (PR #4) | Codex | – |
| 4 | API: create, get, list, cancel, approve, dry-run, health | **PR #5 offen** | Claude | – |
| 5 | Datenschranke: Scanner, Blockliste, Anhang-Extraktion | offen | Codex | Vertrag unten |
| 6 | Pseudonymisierung und Rueckabbildung | offen | Claude | [06](06-pseudonymisierung.md) |
| 7 | Router, Regeln, Tagesbudget, Dry-run | offen | Codex | Vertrag unten |
| 8 | Runner-Rahmen: Lease, Heartbeat, Sweep, Not-Aus, runs/-Ablage | offen | Claude | Vertrag unten |
| 9 | Lane Ollama und erster E2E | offen | Codex | [09](09-ollama-lane-e2e.md) |
| 10 | Fehlerpfad: Retry, Backoff, Fallback, Dead-Letter, Abbruch | umgesetzt, externes Review offen | extern, Owner bestimmt | [10-fehlerpfade.md](10-fehlerpfade.md) |
| 11 | Themebare Oberflaeche und lokaler Chat | umgesetzt, externes Review offen | extern, Owner bestimmt | [11](11-ui.md) |
| 12 | Reinraum-Image und Lane Claude | offen | Claude | [12](12-reinraum-lane-claude.md) |
| 13 | Lane Codex | offen | Codex | [13](13-lane-codex.md) |
| 14 | Wissens-DB-Anbindung | offen | Claude | [14](14-wissens-db-anbindung.md) |
| 15 | Login, Tailnet-Bindung, Haertung | offen | Codex | [15](15-login-tailnet-haertung.md) |
| 16 | Betrieb: systemd, Backup, Aufbewahrung, Restore-Probe | offen | Claude | [16](16-betrieb.md) |
| 17 | Audit-Ansicht, Worker-Gesundheit, Login-Ablauf-Warnung | offen | Codex | [17](17-audit-worker-gesundheit.md) |
| 18 | Abnahme-Lauf 1.0, SECURITY-Checkliste, Tag v1.0 | offen | Claude | [18](18-abnahme-1-0.md) |
| 19, 20 | optional: Lane Cursor, n8n-Webhook, Lane Grok | nicht geplant | – | – |

Die Specs 06, 09 und 11 sind detailliert. Die Specs 12 bis 18 sind kompakter und aus Plan v1.1
abgeleitet; sie werden vor dem jeweiligen Start gegen den dann gemergten Stand nachgeschaerft.
Die Etappen 5, 7 und 8 sind hier als Schnittstellen-Vertraege festgehalten, weil 6, 9 und 11
davon abhaengen. Fuer Etappe 10 gilt die nachgetragene [Spec zu PR #25](10-fehlerpfade.md);
ihr implementierter Fehlervertrag ersetzt abweichende historische Entwurfsangaben unten.

## Verbindliche Begriffe

| Begriff | Festlegung |
|---------|------------|
| Zustaende | `awaiting_approval`, `queued`, `blocked`, `leased`, `running`, `review`, `done`, `failed`, `cancelled`, `expired` (Plan v1.1 plus `awaiting_approval`; Owner-Entscheid). Code auf `main` kennt noch `succeeded` statt `done` und weder `review` noch `expired` (VB-3). |
| Datenklassen | `local_only` (nur Lane ollama), `cloud_redacted` (Cloud nach Pseudonymisierung), `cloud_ok` (Cloud roh). Pflichtfeld ohne Default (ADR 0005). |
| Lanes | `ollama`, `claude`, `codex` (`Worker`-Enum in `app/core/schemas.py`). |
| Tabellen | `tasks`, `results` (Plan nennt sie `runs`), `events`, `leases`; neu `redaction_maps` (Etappe 6), `settings` (Etappe 8), `lane_status` (Etappe 9), `kb_documents`, `kb_chunks` (Etappe 14). |
| Laufverzeichnis | `runs/<task_id>/<attempt>/` (ADR 0003) mit `payload.sent.md`, `<lane>.log`, `result.json`, `out/`. Pfade nur ueber `app/core/paths.py`. |
| Platzhalter | `[[EMAIL_1]]`, `[[PHONE_1]]`, `[[IBAN_1]]`, `[[AHV_1]]`, `[[IP_1]]`, `[[TERM_1]]`; Nummer je Kind ab 1, stabil je Task. |
| Praefixe | API `/api/v1`, UI `/ui`. |
| Make-Ziele | `make lint`, `make test`, `make reference`; neu `make test-server` (Etappe 9), `make install start stop update backup restore-probe` (Etappe 16). |

### Umgebungsvariablen

| Variable | Seit | Bedeutung |
|----------|------|-----------|
| `DECK_DATABASE_URL`, `DECK_TEST_DATABASE_URL` | Etappe 3 | Laufzeit- und Test-DB (Testname endet auf `_test`) |
| `DECK_OWNER_TOKEN` | PR #5 | Bearer-Token fuer Owner-Endpunkte |
| `DECK_WORKER_TOKEN_OLLAMA`, `DECK_WORKER_TOKEN_CLAUDE`, `DECK_WORKER_TOKEN_CODEX` | Etappe 8 | Verschiedene Bearer-Tokens je authentifizierter Lane; kein gemeinsames Worker-Token |
| `DECK_LEASE_TTL_SECONDS`, `DECK_HEARTBEAT_EXTEND_SECONDS` | PR #5 | Lease-Dauer und Verlaengerung |
| `DECK_BIND_HOST`, `DECK_SECRET_KEY`, `OLLAMA_URL`, `KB_DB_URL` | Etappe 1 | Bindung, Signaturschluessel, Ollama-Endpunkt, Wissens-DB |
| `DECK_BLOCKLIST_FILE` | Etappe 5 | Lokale Blockliste, Default `config/blocklist.txt` (gitignored) |
| `DECK_RUNS_DIR` | Etappe 6 | Wurzel der Laufverzeichnisse, Default `runs/` |
| `DECK_OLLAMA_TIMEOUT_S`, `DECK_OLLAMA_NUM_CTX`, `DECK_OLLAMA_ALLOW_REMOTE`, `DECK_E2E_OLLAMA` | Etappe 9 | Lane Ollama und Serverlauf |
| `DECK_E2E_CLAUDE`, `DECK_E2E_CODEX` | Etappen 12, 13 | reale Lane-Laeufe nur auf dem Server |
| `DECK_OWNER_PASSWORD_HASH` | Etappe 15 | Argon2id-Hash, nie Klartext |
| `DECK_BACKUP_DIR` | Etappe 16 | Ziel der DB-Dumps (`--target runtime`), Default `backups/` |
| `DECK_TEST_BACKUP_DIR` | Etappe 16 | Getrenntes Ziel fuer `--target test`, Default `backups_test/`; nie `DECK_BACKUP_DIR` |

Kein Modellname im Repo: Modelle je `kind` stehen in `config/router.yaml` (Etappe 7, E7).

## Schnittstellen-Vertraege zwischen Etappen

Die Etappe links liefert, die Etappe rechts verlaesst sich darauf. Abweichungen sind ein Blocker
(Abschnitt 8 jeder Spec), kein Grund fuer stille Anpassung.

| Vertrag | Von → Nach | Inhalt |
|---------|-----------|--------|
| `ScanHit` | 5 → 6 | `app/core/scanner.py`: `scan(text) -> list[ScanHit]`; `ScanHit(kind, severity, start, end, value)`; `severity` ist `block` (Kinds `SECRET_AWS`, `SECRET_GITHUB`, `SECRET_OPENAI`, `SECRET_ANTHROPIC`, `JWT`, `PRIVATE_KEY`, `HOME_PATH`, `HOSTNAME`) oder `redact` (Kinds `EMAIL`, `PHONE`, `IBAN`, `AHV`, `IP`, `TERM`). Blockliste aus `DECK_BLOCKLIST_FILE`, eine Zeile je Begriff, Kind `TERM`. |
| `plan` und `lane` | 7 → 8, 9 | `tasks.plan` (jsonb): `{"version": 1, "chain": [...], "cursor": 0, "rule": "<id>", "budget_date": "YYYY-MM-DD"}`; `tasks.lane = plan.chain[plan.cursor]`. Runner pullen nur `queued` mit eigener `lane`. Harte Regel: `local_only` → `chain == ["ollama"]`. `config/router.yaml` enthaelt zusaetzlich `lanes.<lane>.models.<kind>` und `lanes.<lane>.models.default`. |
| `AttemptContext`, `LaneExecutor` | 8 → 9, 12, 13 | `app/workers/context.py`: frozen dataclass `AttemptContext(task: TaskV1, attempt: int, lease_id: UUID, lane: str, run_dir: Path, payload_text: str, payload_sha256: str, deadline: datetime, heartbeat: Callable[[], None], cancel_requested: Callable[[], bool])`. Protocol `LaneExecutor.execute(ctx) -> ResultV1` (validiert; `status` in `succeeded`, `failed`, `blocked`, `cancelled`). Der Rahmen (8) macht Pull, Start, Payload (6), Executor, Rueckabbildung (6), Validierung, Acceptance (10), Uebergang. |
| `settings` | 8 → 9, 11 | Tabelle `settings(key text primary key, value jsonb, updated_at)`; Schluessel `paused` (globaler Not-Aus) und `lanes.<lane>.paused`. Pull liest beide; `true` stoppt den Pull. |
| `lane_status` | 9 → 12, 13, 17 | Tabelle `lane_status(lane text primary key, status text, reason text, updated_at, last_success_at)`; `status` in `ok`, `unavailable`, `auth_required`, `paused`. |
| Service-Schicht | 4 → 11 | UI ruft dieselben Funktionen wie `app/api/routes/*` (Anlegen, Freigeben, Abbrechen, Lesen), nie HTTP gegen sich selbst, keine Geschaeftslogik in Templates. |
| Fehlerreaktion | 10 → 9, 12, 13 | Lanes melden einen Versuch; zentrale Reaktion gemaess freigegebenem Plan: `rate_limited` gleiche Lane nach 1/4 min, dann Fallback; `timeout`/`worker_unavailable` naechste Lane; `auth_required`/Anbieter-`policy_blocked` Lane pausieren und Fallback; `acceptance_failed` nach `review`, kein Retry (Owner-Entscheid); `internal` ein taskweiter Retry, dann `failed`. Lokale Datenschranke bleibt `blocked`. [Verbindlicher Vertrag](10-fehlerpfade.md). |

## Vorbedingungen (VB)

| ID | Vorbedingung | Pruefung |
|----|--------------|----------|
| VB-1 | PR #5 (Etappe 4) ist reviewt und gemergt | `git log main` enthaelt den Merge; `make test` gruen |
| VB-2 | Plan v1.1 liegt als `docs/PLAN.md` im Repo (Kopie ohne IP-Literale und Hostnamen; E1) | Datei vorhanden, 18 Etappen identisch mit dieser Liste |
| VB-3 | Zustandsmodell final (Issue #10): `done` statt `succeeded`, neu `review`, `expired`, Uebergaenge `running → review` (bei `require_review`), `review → done` (approve), `review → queued oder failed` (reject), `blocked → queued` (unblock, nur Owner mit Begruendung), `leased oder running → expired`; Events mit `actor`, `reason`; `state_machine.py`, Migration, `ARCHITECTURE.md`, `DATENBANK.md` identisch | Unit-Tests der State-Machine; `alembic check` |
| VB-4 | Datenintegritaet (Issue #9): kein DB-Default fuer `data_class`, CHECKs, Payload unveraenderlich, Events append-only | Migration `0003` aus PR #5 im Diff bestaetigt |
| VB-5 | Fencing und Abbruch (Issues #6, #7, #8): `cancel_requested`, kein `skip_lease_check`, Lease-Ablauf respektiert `max_attempts` | Integrationstests aus PR #5 gruen |
| VB-6 | Pfadregeln `app/core/paths.py` (Issue #12) fuer Eingaben und `runs/` | Unit-Tests Traversal |
| VB-7 | Referenz-Runner beruecksichtigt `data_class` (Issue #13); xfail Lane-Filter aus PR #5 in Etappe 7 aufgeloest | `make reference` gruen ohne xfail |
| VB-8 | Vorstufen gemergt: 5 vor 6; 6, 7, 8 vor 9; 9, 10 vor 11; 11 vor 12 bis 18 | Merge-Liste |
| VB-9 | `httpx` ist seit PR #5 Abhaengigkeit; Begruendung steht im PR (AGENTS.md) | `pyproject.toml` |
| VB-10 | Referenz-Testset mit mindestens 12 Faellen, davon 4 Traps (Owner liefert, Format `docs/REFERENCE_CASES.md`) | `tests/reference/cases/` zaehlt 12+ |

## Entscheidungen fuer den Owner (E)

Empfehlung zuerst. Ohne Entscheid baut der Builder die Empfehlung.

| ID | Frage | Empfehlung | Folge |
|----|-------|------------|-------|
| E1 | Plan v1.1 ins Repo | ja, `docs/PLAN.md`, ohne IP-Literale und Hostnamen | VB-2 erfuellt; Specs verweisen auf Abschnitte |
| E2 | `redaction_maps` im Klartext in der DB | ja (Schutzklasse wie `tasks.payload`, Aufbewahrung wie `runs/`) | keine Schluesselverwaltung; Alternative verschluesselt braucht Etappe 15 |
| E3 | Personennamen nur ueber lokale Blockliste | ja | kein NER-Modell, kein RAM; Owner pflegt `config/blocklist.txt` |
| E4 | Ollama-Client | `httpx` (bereits Abhaengigkeit) | Alternative `urllib`, ohne Abhaengigkeit |
| E5 | UI-Technik | Jinja2 und HTMX vendored (Plan-Leitentscheid 2) | nur HTMX-Version festlegen |
| E6 | Netz der Reinraum-Container | ausgehendes Internet ja (CLI erreicht den Anbieter), kein Host-Netz, kein DB-Zugang; Ollama host-seitig ohne Container | rootless Docker, Standard-Bridge |
| E7 | Modellwahl | je `kind` in `config/router.yaml`, kein Modellname im Code | Lane verweigert Start ohne Eintrag |
| E8 | Testfixtures | IP-Werte nur aus Dokumentationsbereichen (RFC 5737, RFC 3849), E-Mail nur Beispiel-Domains (RFC 2606) | Auslegung von AGENTS.md fuer Tests bestaetigt |
| E9 | Zustandsmodell | Plan v1.1 plus `awaiting_approval` (entschieden); Nachzug-PR direkt nach PR #5 | VB-3 |
| E10 | Revisionsnamen `20251103_*` (Jahr 2025, Issue #10) | umbenennen auf `20261103_*` im Nachzug-PR, solange keine produktive DB migriert ist | danach nur neue Revisionen |
| E11 | Aufbewahrung | Plan: `runs/` 90 Tage, Dumps 14 Tage; `redaction_maps` wie `runs/` | Etappe 16 |
| E12 | Scanner-Schweregrad | `block` fuer Schluessel, PEM, JWT, Home-Pfade, Hostnamen; `redact` fuer E-Mail, Telefon, IBAN, AHV, IP, Blocklisten-Begriffe | Etappe 5 baut den Katalog mit `severity`; Etappe 6 ersetzt nur `redact` |
| Plan-Fragen 3, 4, 5, 9, 12 | Backup-Pfad, Wissens-DB-Schema, Policy-Restrisiko Claude, Passkeys, Takt | siehe Specs 16, 14, 12, 15, 18 | je Spec unter "Bewusst offen" |

## Arbeitsregeln fuer Builder-Agents

1. Ein Branch `stage/NN-<slug>` und ein Draft-PR je Etappe; PR-Template vollstaendig, Reviewer-Anbieter nach Rotation eintragen.
2. Erst bauen, wenn alle VB der Spec erfuellt sind. Sonst stoppen und im PR oder Issue melden (Abschnitt 8 der Spec).
3. Keine Rueckfragen im Verlauf: Default-Entscheidungen der Spec gelten. Abweichung nur nach Owner-Entscheid.
4. `make lint` und `make test` vor jeder Meldung "fertig"; neue Abhaengigkeiten nur mit Begruendung im PR.
5. Nie mergen, nie auf `main` pushen, keine Server-Zugriffe, keine Secrets, keine Namen, keine IP-Literale, keine Hostnamen.
6. Doku unter `docs/` auf Deutsch (de-CH, kein Eszett); Code, Kommentare, Commits Englisch (Conventional Commits).
7. Jede Spec endet mit "Bewusst offen"; der PR uebernimmt diese Liste.

## Tagesplan (17 Schritte, Schaetzung, eine Etappe pro Tag)

| Nr | Datum | Schritt | Verantwortlich | Gate |
|----|-------|---------|----------------|------|
| 1 | Di 03.11. | PR #5 Diff-Review (Beschreibung reicht nicht), Owner-GO, Merge; Issues #6 bis #14 pruefen und schliessen | Reviewer Claude, Owner | VB-1 |
| 2 | Mi 04.11. | E1, E2, E3, E8, E12 entscheiden; `config/blocklist.txt` lokal anlegen; `docs/PLAN.md` ablegen | Owner | VB-2 |
| 3 | Mi 04.11. | Nachzug Zustandsmodell (Issue #10, E9, E10): Code, Migration, Doku identisch | Cursor, Review Codex | VB-3 |
| 4 | Do 05.11. | Etappe 5 Datenschranke mit `severity` | Cursor, Review Codex | Traps blockiert, keine Falschpositive |
| 5 | Fr 06.11. | Etappe 6 nach Spec 06 | Cursor, Review Claude | Roundtrip, Map lokal |
| 6 | Mo 09.11. | Etappe 7 Router (`router.yaml`, `plan`, Budget, Dry-run); Owner bestaetigt E7 | Cursor, Review Codex | `local_only` nie Cloud |
| 7 | Di 10.11. | Etappe 8 Runner-Rahmen (`AttemptContext`, `settings`, Sweep) | Cursor, Review Claude | Runner-Kill → Wiederaufnahme |
| 8 | Mi 11.11. | Etappe 9 nach Spec 09; `make test-server` auf dem Server | Cursor, Owner, Review Codex | Referenz-Set gruen auf Ollama |
| 9 | Do 12.11. | Etappe 10 Fehlerpfad | Cursor, Review Claude | jeder Fehlercode simuliert |
| 10 | Fr 13.11. | Etappe 11 nach Spec 11; Smoke-Test ohne CLI | Cursor, Owner, Review Codex | alles in der UI bedienbar |
| 11 | Mo 16.11. | Etappe 12 Reinraum und Lane Claude; Owner: CLI-Login im Credentials-Volume, Extra Usage aus | Cursor, Owner, Review Claude | `policy_blocked` sauber |
| 12 | Di 17.11. | Etappe 13 Lane Codex; Owner: Device-Login | Cursor, Owner, Review Codex | wie 12 |
| 13 | Mi 18.11. | Etappe 14 Wissens-DB; Plan-Frage 4 vorher beantwortet | Cursor, Owner, Review Claude | `local_only`-Chunk nie in Cloud-Payload |
| 14 | Do 19.11. | Etappe 15 Login, Tailnet-Bindung; Plan-Frage 9 | Cursor, Owner, Review Codex | Port-Scan leer, Rate-Limit greift |
| 15 | Fr 20.11. | Etappe 16 Betrieb, Restore-Probe; Plan-Frage 3 | Cursor, Owner, Review Claude | Restore in leere DB |
| 16 | Mo 23.11. | Etappe 17 Audit, Worker-Gesundheit, Login-Ablauf | Cursor, Review Codex | Ablauf innert eines Laufs sichtbar |
| 17 | Di 24.11. | Etappe 18 Abnahme 1.0, SECURITY-Checkliste, Tag v1.0 nur nach GO | Cursor, Owner, Review Claude | Testset ueber alle Lanes gruen |

## Bekannte Widersprueche (zu klaeren, nicht still aufloesen)

| Thema | Code oder Doku | Plan v1.1 | Umgang |
|-------|----------------|-----------|--------|
| Ergebnis-Tabelle | `results` | `runs` | Codename `results` behalten, Plan-Begriff in Doku erwaehnen |
| Zustandsnamen | `succeeded`; kein `review`, `expired` | `review`, `done`, `expired` | VB-3 Nachzug |
| `data_class`-Default | `docs/DATENBANK.md` nennt DB-Default `local_only` | Pflicht ohne Default (ADR 0005) | PR #5 Migration `0003` pruefen (VB-4) |
| CI-Umfang | CI faehrt Postgres und Integrationstests | nur Lint, Unit, Schema | CI so lassen; E2E mit Ollama nur `make test-server` |
| `ARCHITECTURE.md` | Zustandstabelle mit `review`, `done`, `expired` | identisch | nach VB-3 stimmt Doku mit Code |
| Revisionsjahr | `20251103_*` | – | E10 |
