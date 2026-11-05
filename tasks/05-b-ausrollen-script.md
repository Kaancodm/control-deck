# Bauauftrag 05-b-ausrollen-script

## Etappe

Ticket 5 / B: `deploy/ausrollen.sh` nach Abschnitt 6 des Ausroll-Reviews, mit ShellCheck in CI.
Builder: Codex. Externes Review: Fable; noch ausstehend. Status: nur geplant.
Umsetzungsbranch: `stage/05-b-ausrollen-script`; Start erst nach gruenem A.

## Ziel

Der Owner kann die Server-Schritte 1 bis 15 einzeln, mit Vorpruefung, bewusster Freigabe und
nachvollziehbarem Fortschritt ausfuehren. Ein Aufruf erledigt hoechstens einen Schritt.
Ein Fehler oder unbewiesener Nachweis sperrt den Nachfolger. Das Skript-Konzept ist die
Vorlage; seine Platzhalter, Echo-Funktionen und unzureichenden Fehlerpfade gelten nicht als Erfolg.

## Rahmen

- Reihenfolge A -> B -> C -> D. B gruen bedeutet Abnahme + CI + Fable-Review ohne offene Befunde;
  erst dann C. Merge nur nach Owner-GO. Nur der Owner fuehrt danach Server-Schritte 1–15 aus.
- Codex testet ausschliesslich mit Fake-Befehlen und synthetischer privater Konfiguration;
  kein SSH, Dienststart, Paketinstallation, Login oder Netzaufruf am Betriebsort.
- Blocker 1(b) gilt fuer den iPhone-Test: kein Deck-Backup/Update, kein automatischer Wechsel
  nach A; Blocker 1(c) ist A. B2-Abdeckung der zentralen DB muss separat nachgewiesen sein.
- Blocker 2 bleibt offen: Timername UND Manager fehlen. Schritt 1 darf sie nur lesend ermitteln;
  ohne dokumentierte Kollisionsentscheidung kein Schritt 2 ff., insbesondere keine Installation.
- Blocker 3 = Variante A: HTTPS via Tailscale, Loopback, feste `DECK_PUBLIC_ORIGIN`, kein Funnel.
- Keine Hostnamen, IP-Literale oder Secrets im Repo/Log. Konkrete Werte nur in privaten Dateien.
- Keine neuen Python-Abhaengigkeiten; ShellCheck als begruendetes CI-Werkzeug installieren.
- ADRs/Datenschranke bleiben unveraendert; `_test`-DBs, englischer Code und de-CH-Dokumentation.

## Schritte (atomar, je ein Commit)

1. `deploy/ausrollen.sh` (neu), `tests/unit/test_rollout_script.py` (neu): Aufruf `N [--apply]`,
   N ausschliesslich 1–15, `set -euo pipefail`, `umask 077`. Ohne `--apply` nur lesende Checks
   und Vorschau; kein State-Schreiben. Kein `--force`. `check_N`, `apply_N`, `rollback_N`
   explizit je Schritt; Rollback zunaechst Vorschau, Ausfuehrung nur mit separatem Owner-GO.
2. Dieselben beiden Dateien: private `ausrollen.conf` nur als erlaubte literale Schluessel/Werte
   parsen, niemals sourcen/eval; auch die Runtime-Env nie sourcen/ausgeben. `with_env` nutzt
   `systemd-run --user --wait --pipe --collect` mit `EnvironmentFile` und `WorkingDirectory`.
   Rechte 0700/0600, Eigentuemer, Symlink/Hardlink und erlaubte Pfade pruefen; keine Secrets in argv.
3. Dieselben beiden Dateien: State mit festem Commit, Schritt, Ergebnis und Zeit atomar speichern,
   parallele Aufrufe sperren und alle Vorgaenger pruefen. Nur erfolgreicher Nachcheck markiert
   einen Schritt gruen. Commitwechsel/inkonsistenter State sperrt; Rollback entwertet Nachfolger.
   GO nur interaktiv am Terminal fuer Eingriffe; `--apply`, Env oder Pipe ersetzen das GO nicht.
4. Dieselben beiden Dateien: Checks/Apply/Ruecknahme fuer die untenstehende Folge implementieren.
   Nicht implementierte Aktionen, reine Echo-Ausgaben, fehlende Werkzeuge oder manuelle Nachweise
   duerfen keinen fertigen Schritt erzeugen. Kein automatischer Gesamtdurchlauf.
5. Dieselben beiden Dateien: Fehler-/Abbruchtests und redigiertes Logging. Nur Schritt, Zeit,
   Ergebnis und sichere Fehlercodes speichern; keine rohen Journale, Prozessausgaben, URLs,
   Hashes oder private Konfiguration. Kein `set -x`; Fehler nicht durch `|| true` verschlucken.
6. `.github/workflows/ci.yml`, `tests/unit/test_ci_workflow.py`: ShellCheck fuer das neue Skript
   verpflichtend installieren/ausfuehren; fehlendes Werkzeug oder Warnung macht CI rot.
   Vorhandene SHA-/Digest-Pins und Pflichtchecks erhalten; keine pauschalen Lint-Ausnahmen.

### Feste Server-Folge (nur der Owner)

| Schritt | Inhalt und gruener Nachcheck |
|---|---|
| 1 | Ist-Zustand nur lesen: Versionen, DB-Erreichbarkeit, Speicher, Linger, Sandbox, HTTPS, Timeridentitaet; keine Werte ins Repo |
| 2 | Nur fehlende Werkzeuge nach GO; Installer vorher pruefen, kein Download-Pipe-in-Shell; Versionen passend |
| 3 | Zentrale Rolle/DB nach GO; Passwort interaktiv, kein argv; effektive Rechte pruefen, inklusive indirekter PUBLIC-/Gruppenrechte |
| 4 | Exakten freigegebenen Commit auschecken, `uv sync --frozen`; vorhandene fremde/unsaubere Verzeichnisse nicht ueberschreiben |
| 5 | Konfigurations-/Unit-Verzeichnisse 0700, private Env-/Runner-Datei 0600; getrennte Konfiguration, keine Test-/Restore-Ziele im Betrieb |
| 6 | Owner-Hash interaktiv erzeugen/eintragen; weder Passwort noch Hash im Log; Anwesenheit/Format ohne Wertausgabe pruefen |
| 7 | `server_main --check-only` mit identischer Env-Ladung; keine DB, kein Listener; exit 0 |
| 8 | Zentrale Migration und `check` nach GO; KB-Runtime-Migration nur nach separatem, ausdruecklichem Wechsel von 1(b) auf 1(c) |
| 9 | Linger/User-Manager und vorhandene Units pruefen, nur fehlendes Linger nach GO; ungepruefte Kollision sperrt |
| 10 | Units nach GO installieren/verifizieren; install aktiviert Timer: Deck-Timer gemass Entscheid kontrollieren, B2 niemals mitverwalten |
| 11 | Unter 1(b) nur bestaetigte B2-Abdeckung und expliziter zentraler Erstdump; sonst vollstaendiger Zwei-DB-Satz; keine Backup-Gleichsetzung |
| 12 | Nur API explizit starten und pruefen; nicht pauschal `make start`, das Runner UND Timer starten wuerde; DB-Nachweis separat von Liveness |
| 13 | HTTPS im Tailnet nach GO, feste Origin/Host-Allowlist pruefen; kein Funnel und keine Firewall-Aenderung |
| 14 | Owner fuehrt iPhone-Pruefungen aus; manuelle Ergebnisse ausdruecklich erfassen, keine automatischen PASS-Werte |
| 15 | Nachweise fuer Checkliste zusammenstellen; Skript editiert/pusht/mergt nichts im Repo; keine vorgetaeuschte Gesamtabnahme |

Der zentrale Erstdump unter 1(b) muss dieselbe explizite DB-Konfiguration wie die API verwenden;
keine stillen pg_dump-Defaults. Private PG-Umgebung statt Passwort/DSN in argv, Ziel privat.
Timer unter 1(b) nur nach eindeutiger Zuordnung deaktivieren; Schritt 12 darf sie nicht reaktivieren.
Ruecknahmen betreffen nur nachweislich selbst angelegte/geaenderte Ressourcen, nie B2 oder fremde
Units/DBs. Kein automatischer Schema-Downgrade, kein pauschales Loeschen und kein Abschalten der Sandbox.

## Abnahme (ausfuehrbare Befehle)

| Befehl | Erwartet |
|---|---|
| `bash -n deploy/ausrollen.sh && shellcheck --severity=style deploy/ausrollen.sh` | exit 0; keine Diagnose |
| `uv run pytest tests/unit/test_rollout_script.py -q` | Fake-Harness prueft alle 15 Schritte, GO/TTY, State, Rollback, Parallelstart, falschen Commit und sichere Logs; kein echter Betriebsbefehl |
| `uv run pytest tests/unit/test_rollout_script.py -q -k 'blocker or timer or dry_run or secret or failure'` | Unbekannter B2-Timer sperrt; keine B2-Aenderung; Dry-run ohne Mutation; jeder injizierte Fehler sperrt Folgeschritt |
| `uv run pytest tests/unit/test_ci_workflow.py -q` | ShellCheck verpflichtend, Pins/Pflichtchecks erhalten |
| `make lint && make test && make reference` | alle gruen, keine neuen Skips; Testzahlen ausweisen |
| `git diff --check` | exit 0 |

## Abbruchkriterium

Abbruch bei unklarem Ziel/Timer, fehlendem GO/Nachweis, ungeschuetzter Konfiguration, ausgegebenem
Geheimnis, fehlgeschlagenem Check oder rotem Review. Kein Fortschrittseintrag, kein Folge-Schritt.
Ein gruener Fake-Lauf ersetzt keinen Test des echten User-Managers oder des iPhones.

## Bewusst offen

C dokumentiert den tatsaechlich implementierten Ablauf; D liefert die leere Nachweistabelle.
Alle realen Server-Schritte und T4–T7-Nachweise bleiben beim Owner, getrennt von diesem Repo-Bau.

## Offene Fragen (TODO)

TODO Blocker 2 bleibt offen: exakter B2-Timername, Manager und Kollisionsentscheidung durch Owner.
TODO Inbetriebnahme: private Pfade, Versionen, Rechte und B2-Abdeckung pruefen; keine Werte ins Repo.
