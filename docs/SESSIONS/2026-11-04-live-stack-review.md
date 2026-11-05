# Session-Protokoll 2026-11-04: Live-Stack-Review

Fachlicher Verlauf einer Review-Session ueber den Ollama-Live-Stack (Etappe 18, ADR 0009),
die Planungs-PRs und die daraus entstandenen Nacharbeiten. Kurzfassung fuer den Einstieg:
`HANDOFF.md` im Repo-Root.

## Quellenlage

Der Chat-Verlauf der Session ist nicht vollstaendig erhalten; der Kontext wurde waehrend der
Session komprimiert und der Container danach neu gebaut. Dieses Protokoll ist aus Git
(Branches, Commits, Diffs) und den PR-Beschreibungen auf GitHub rekonstruiert. Wo eine
Aussage nur aus dem Chat stammt und nicht im Repo belegt ist, steht das ausdruecklich dabei.
Keine Secrets, Namen, Adressen oder Hostnamen; Rollen statt Personen.

## Ausgangslage

| Punkt | Stand |
|-------|-------|
| `main` | `a33cc3f` (#37 gemergt, 2026-11-04) |
| Etappen | 1 bis 17 gemergt; Etappe 18 nicht gestartet |
| Version / Tag | `0.1.0`, kein Tag |
| Testumfang laut CI | 2593 Tests auf #39-Head, 2967 Tests auf #48-Head |
| Referenzfaelle | 14 (001 bis 014), 14/14 gruen laut #38 |

## Gegenstand der Session

### 1. Live-Stack Ollama (ADR 0009)

Acht gestapelte Draft-PRs, Reihenfolge und Heads nach dem Rebase auf `main@a33cc3f`:

| PR | Branch | Head | Inhalt |
|----|--------|------|--------|
| #39 | `stage/18-live-00-decision` | `590c6d1` | ADR 0009 Loopback-HTTP und Provenienz; URL- und Kontextregeln |
| #40 | `stage/18-live-01-provenance` | `0d53e8e` | Migration 0018 `result_provenance`, Health-Zaehler |
| #41 | `stage/18-live-02-flags` | `140f160` | explizite Live-Konfiguration ohne Transport |
| #42 | `stage/18-live-03-http` | `992897c` | begrenzter, abbrechbarer Loopback-HTTP-Client |
| #44 | `stage/18-live-04-executor` | `b21deb1` | lokale Textmodelle mit Live-Provenienz, Kontext und Token-Schaetzung |
| #45 | `stage/18-live-05-daemon` | `cea11e6` | Supervision der Live-Versuche, Runtime-Recovery |
| #46 | `stage/18-live-06-canary` | `97d9114` | `make canary`, lesende Verifikation, `docs/LIVE_CANARY.md` |
| #47 | `stage/18-live-07-reference` | `4335957` | Live-Nachweis getrennt von Simulation, Referenzlauf |
| #48 | `stage/18-loadtest` | `deb0a32` | Lasttest D2: 50 Auftraege, 3 Runner, 0 Doppel, 0 haengende Leases |

Gesamtdiff `main..stage/18-loadtest`: 69 Dateien, rund 6400 Zeilen neu, 800 entfernt.
`quality` und GitGuardian sind auf den Heads von #39 und #48 gruen. CodeRabbit ueberspringt
Drafts.

### 2. Zurueckgezogener Parallel-Entwurf #43

`stage/live-00-adr` (`69fc872`) enthielt eine zweite Fassung von ADR 0009 (nur PR 0 bis 2,
Streaming und Abschneiden offen). Entscheidung: #39 bleibt der gemeinsame Entwurf, #43 wurde
geschlossen, nicht gemergt. Die Unterschiede (Umfang, NDJSON-Streaming, `acceptance_failed`
bei abgeschnittener Antwort, exakte Version 0.40.14 und Modell-Tags) stehen tabellarisch in
#43 und im ADR. Branch bleibt zum Nachlesen.

### 3. Planung Inbetriebnahme #38

`plan/04-07-inbetriebnahme` (`b68dce6`): vier Bautickets in `tasks/` (A KB-Runtime-Migration,
B Ausroll-Skript, C Betriebsdoku, D Inbetriebnahme-Checkliste), Reihenfolge A -> B -> C -> D,
jeder Nachfolger erst nach gruener Abnahme und Review des Vorgaengers. Owner-Entscheide aus
dem PR: Blocker 1 Variante (b) fuer den ersten Telefon-Test und (c) als Ticket A; Blocker 2
offen (Timername und Manager fuer B2 unbekannt, nichts raten); Blocker 3 Variante A
(HTTPS ueber Tailnet, Loopback-Bindung, feste Public-Origin). Review durch Fable steht aus.

## Review-Ergebnisse und Nacharbeiten

### Befund 1: Artefakte vor Commit (P1, behoben in #49)

`HostRunner.run_once` schrieb `result.json` und das Lane-Log innerhalb der offenen
Ergebnis-Transaktion. Bei Commit-Fehler rollte die Datenbank zurueck, die Dateien blieben
liegen und behaupteten einen Ausgang, den die Datenbank nie kannte. Das Verhalten bestand
bereits auf `main`, nicht nur im Stack. Auf dem Stack liegt dazu der Entwurf
`docs/FOLGE_ISSUE_RUNNER_2026-11-04.md` (als P2 eingestuft, nicht umgesetzt).

Umsetzung in #49 `fix/runner-artifacts-after-commit` (`067176b`, Basis `main@a33cc3f`):

- Commit zuerst, Artefakte danach. Ein zurueckgerolltes Ergebnis hinterlaesst keine Dateien.
- Schreibfehler nach dem Commit: Datenbank bleibt massgebend, Rueckgabe `completed` mit
  `error_code="artifact_publication_failed"` statt irrefuehrend `result_persistence_failed`.
- `docs/RUNNER.md` beschreibt die Reihenfolge. Keine Schema-, Migrations- oder API-Aenderung.
- Drei neue Integrationstests in `tests/integration/test_host_runner.py`; der erste war gegen
  den alten Code rot. 22 Tests der Datei gruen, Lint gruen.

Bewusst offen: kein Aufraeumen alter inkonsistenter Artefakte, kein Nachholen nach
`artifact_publication_failed`. Das bleibt Gegenstand des Folge-Issues.

### Befund 2: systemd-Verifikation im falschen Scope (behoben in #50)

`test_unit_templates_have_valid_offline_syntax` rief `systemd-analyze verify` ohne `--user`
auf, prueft also gegen den System-Manager und braucht Rechte auf dem Host-Runtime-Pfad.
Installation und Dienstaktionen zielen auf den User-Manager. Die Korrektur war zunaechst
Teil von #39 und wurde auf Wunsch des externen Reviews ausgelagert.

Umsetzung in #50 `test/operations-user-unit-verify` (`0716ec1`, Basis `main@a33cc3f`):
Verifikation im User-Scope mit privatem `XDG_RUNTIME_DIR` und Locale `C`, Verifier-Meldung
als Assertion-Text, Negativkontrolle mit einer Unit ohne `ExecStart`. Nur Test, kein
Produktcode. 41 Unit-Tests gruen. #39 enthaelt die Aenderung nicht mehr.

### Weitere Review-Punkte am Stack (in die PR-Texte eingeflossen)

- Provenienz-Migration 0018 haengt an `20261104_0017_login_capacity`; Reihenfolge der Kette
  ist verbindlich, kein Squash ueber PR-Grenzen.
- Quellpruefung und Stub-Tests gelten nicht als Server-Abnahme. Echte Modelllaeufe,
  Daemon-Policy und Modellinventar bleiben Owner-Nachweise.
- Lasttest #48 misst Durchsatz und p50/p95 nur; die Spec kennt keine Grenzwerte dafuer.
  Gates sind Vollstaendigkeit, keine Doppelausfuehrung, keine haengenden Leases.
- Browser-Tests (6 Faelle) sind in CI gruen, lokal je nach Chromium-Umgebung rot. Kein Test
  wurde entfernt oder abgeschwaecht.

## Abgelehnte Ideen

Beide Punkte stammen aus dem Chat; im Repo ist nur die Regel belegt, nicht die Diskussion.

- **Feste Adresse eines Debian-Hosts in Doku oder Konfiguration.** Abgelehnt. Regel:
  keine IPs, Hostnamen oder Infrastrukturwerte in Code oder `docs/` (CLAUDE.md, Human gates;
  Etappe 18 D3 prueft das als Suchmuster). Zielsystem wird ueber Rollen, Loopback-Bindung und
  konfigurierte Public-Origin beschrieben, nicht ueber Adressen.
- **Wechsel auf MySQL.** Abgelehnt. ADR 0001 legt die Queue auf PostgreSQL mit
  `SKIP LOCKED` fest, die Wissens-DB braucht `pgvector` (Etappe 14), Migrationen und
  Testdatenbank-Guard (`_test`-Suffix) sind darauf gebaut. Ein Wechsel waere eine neue ADR
  und steht fuer v1.0 nicht zur Debatte.

## Entscheidungen dieser Session

| Nr | Entscheidung | Beleg |
|----|--------------|-------|
| E1 | #39 ist der gemeinsame ADR-Entwurf, #43 geschlossen | PR #43 Text |
| E2 | Stack auf `main@a33cc3f` rebasiert, bleibt vollstaendig Draft | PR #39 Text, Heads |
| E3 | Runner-Befund wird sofort gegen `main` behoben, nicht im Stack | PR #49 |
| E4 | systemd-Testfix verlaesst #39 und wird eigener PR | PR #50 |
| E5 | Kein Merge, kein Tag, kein Canary ohne Owner-GO | CLAUDE.md Human gates |
| E6 | Uebergabe ueber `HANDOFF.md` plus dieses Protokoll statt Session-Fortsetzung | diese Datei |

## Offene Punkte

- Owner-GO und Merge-Reihenfolge: Vorschlag #49, #50, dann #39 ff. in Kettenfolge, #48 zuletzt.
- Review von #38 (Planung A bis D) durch Fable; Blocker 2 bleibt offen.
- Folge-Issue Artefakt-Wiederaufnahme (`docs/FOLGE_ISSUE_RUNNER_2026-11-04.md` auf dem Stack).
- Owner-Nachweise: Canary, echter Ollama-Lauf, Restore-Probe, Server-Schritte.
- Setup-Script der Cloud-Umgebung: `createdb` scheitert an Passwort-Auth fuer `root` auf
  Port 5432; Integrationstests im Container sind dadurch ohne manuelle DB-Anlage nicht moeglich.

## Plan fuer die naechsten Tage

Reihenfolge verbindlich, jeder Schritt einzeln abnehmbar. Merges nur nach GO des Owners.

1. #49 lesen, lokal `make lint` und `uv run pytest tests/integration/test_host_runner.py`.
2. GO oder Aenderungswunsch zu #49; bei GO Merge durch den Owner.
3. #50 lesen, `uv run pytest tests/unit/test_operations_service.py`; GO oder Aenderungswunsch.
4. #38 reviewen (nur `tasks/`): Pfade, Abbruchkriterien, keine Hostwerte. Blocker 2 klaeren
   oder ausdruecklich offen lassen.
5. #39 reviewen: ADR 0009 gegen ADR 0002 und 0006 lesen, Widersprueche benennen.
6. #40 und #41 reviewen: Migration 0018, `alembic -x target=test check`, Live-Flags ohne Transport.
7. #42 und #44 reviewen: HTTP-Client Grenzen (Timeout, Abbruch, Groesse), Provenienz-Checks.
8. #45 bis #47 reviewen: Daemon-Supervision, Canary nur lesend, Live-Nachweis getrennt.
9. #48 reviewen: Lasttest-Gates, Test-DB-Guard, keine Runtime-URL als Ersatz.
10. Merge-Kette nach GO: #39 bis #47 in Reihenfolge, nach jedem Merge Rebase des Nachfolgers.
11. #48 nach Merge der Kette auf `main` rebasen, CI abwarten, GO, Merge.
12. Setup-Script der Cloud-Umgebung korrigieren (DB-Anlage mit passendem Benutzer oder
    weglassen; Tests skippen ohne `DECK_TEST_DATABASE_URL`).
13. Ticket A aus #38 starten: `/plan-ticket` pruefen, Branch, Umsetzung, Tests, Draft-PR.
14. Review Ticket A durch anderen Anbieter (ADR 0008), GO, Merge.
15. Ticket B (Ausroll-Skript) umsetzen; ShellCheck in CI; keine Secrets in argv oder Logs.
16. Ticket C (Betriebsdoku) und Ticket D (Inbetriebnahme-Checkliste T1 bis T17) nacheinander.
17. Owner fuehrt Server-Schritte einzeln aus; Ergebnisse in `docs/ABNAHME_CHECKLISTE_ENTWURF.md`.
18. Owner-Canary (`make canary --submit`) nach GO; Ergebnis nur PASS oder FAIL dokumentieren.
19. Etappe 18 Abnahmelauf nach `docs/ETAPPEN/18-abnahme-1-0.md`: Referenz-Set ueber alle
    Lanes, SECURITY-Checkliste, Go/No-Go-Tabelle.
20. Version `1.0.0`, `CHANGELOG.md` abschliessen, Tag `v1.0.0` setzt der Owner.

## SHAs und Referenzen

| Objekt | Wert |
|--------|------|
| `main` | `a33cc3f` |
| #37 Head (gemergt) | `8426a84` |
| #38 Head | `b68dce6` |
| #39 Head | `590c6d1` |
| #43 Head (geschlossen) | `69fc872` |
| #47 Head | `4335957` |
| #48 Head | `deb0a32` |
| #49 Head | `067176b` |
| #50 Head | `0716ec1` |
| Uebergabe-Branch | `claude/session-handoff-docs-on6j3g` |
