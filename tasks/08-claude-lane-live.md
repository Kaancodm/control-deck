# Bauauftrag 08-claude-lane-live

## Etappe

Ticket 8: Live-Profil fuer die Lane `claude` (Nachfolge von Etappe 12, die nur die Simulation
gebaut hat). Vier Teile A bis D, je ein Branch `stage/08x-<slug>` und ein Draft-PR, strikt
nacheinander. Builder: Cursor. Externes Review: Grok (anderer Anbieter als der Builder).
Status: nur geplant.

Herkunft: Owner-GO fuer den Claude-Live-Schalter vom 2026-11-11 (Methode: Owner-Aussage in der
Planungssitzung; Status: proven als Entscheid). Befunde zum Code: Lesepruefung des Repos am
2026-11-11 (Methode: Dateien gelesen, Stellen unten genannt; Status: proven, ausser wo
`unproven` steht). Kein Server wurde beruehrt, kein echter CLI-Aufruf ausgefuehrt.

## Ziel

Nach Teil D kann ein Runner der Lane `claude` einen freigegebenen Auftrag mit der offiziellen
CLI im Reinraum-Container wirklich ausfuehren und als Live-Ergebnis mit eigener Herkunft
ablegen. Vorher gibt es dafuer keinen Code: Die Lane kennt nur `disabled` und `simulation`.
Die Lane `codex` bleibt unveraendert gesperrt.

## Ausgangslage (belegt)

| Sperre heute | Stelle |
|--------------|--------|
| `load_live_config` wirft `live_lane_not_supported` fuer jede Lane ausser Ollama | `app/workers/live_config.py:35-36` |
| `ClaudeExecutor` akzeptiert nur `disabled` und `simulation` | `app/workers/lanes/claude.py:139`, `:146` |
| Der Reinraum startet nur die mitgelieferte Fake-CLI, nie Docker | `app/workers/sandbox.py:217-235`, `:263` |
| Der Containervertrag setzt fest `--network=none` | `app/workers/sandbox.py:148` |
| Der Executor nutzt fest das Platzhalter-Image | `app/workers/lanes/claude.py:238`, `app/workers/sandbox.py:22` |
| `--live` verlangt den Executor `ollama-live` | `app/workers/runner_main.py:94-103` |
| Der Supervisor waehlt im Live-Modus fest `ollama-live` | `app/workers/daemon.py:83-93` |
| Der Supervisor reicht nur sieben Variablen weiter | `app/workers/daemon.py:20-28` |
| Der Canary ist fest auf Ollama gelegt | `scripts/live_canary.py:128`, `:140`, `:179` |

Falle: Nur die Lane-Pruefung in `live_config.py` zu lockern ist falsch. `--live` baut heute
immer den Ollama-Runner, unabhaengig von `--worker`. Und `DECK_LIVE_ENABLED` steht in der
gemeinsamen Runner-Datei aller Instanzen; ein fuer Ollama gesetztes Flag darf Claude nie
mit einschalten.

## Rahmen

- Reihenfolge A -> B -> C -> D. Ein Teil ist gruen, wenn Abnahme, CI und das externe Review ohne
  offene Befunde vorliegen. Erst dann beginnt der naechste Teil. Merge nur nach Owner-GO.
- Tests und CI starten nie die echte CLI, bauen kein Image, melden sich nirgends an und rufen
  keinen Anbieter auf. Der Live-Pfad wird mit einem Fake-Programm an der Stelle von `docker`
  geprueft.
- Cloud nur ueber die offizielle CLI und die Abo-Anmeldung (ADR 0006). Kein API-Key, nirgends.
  `--bare` bleibt verboten (`docs/LANES.md:304-305`).
- Der CLI-Vertrag aus `claude_command` (`app/workers/sandbox.py:74-102`) bleibt unveraendert:
  ein Turn, Werkzeuge und MCP aus, keine Sitzung.
- `task.v1` und `result.v1` bleiben unveraendert. Keine neue Lane, kein neuer Enum-Wert,
  keine Migration. Braucht ein Schritt doch eine Migration: stoppen und den Owner fragen.
- `local_only` erreicht nie die Lane `claude`. `cloud_redacted` geht nur pseudonymisiert hinaus.
- Lane-Logs, Events und Fehlergruende enthalten weder Prompt noch Antwort noch stderr.
- Keine Server-Eingriffe durch Agenten. Die Benutzerkonten am Betriebsort bleiben, wie sie sind;
  kein Schritt legt Konten an, aendert sie oder kopiert Zugangsdaten zwischen Konten.
- Keine Hostnamen, IP-Literale, Namen oder Secrets im Repo. Keine neue Python-Abhaengigkeit.
- Code und Commits Englisch (Conventional Commits), `docs/` de-CH ohne Eszett.
- Nach zwei gescheiterten Versuchen an derselben Stelle: stoppen und dem Planer melden.

## Schritte (atomar, je ein Commit)

### Teil A: Entscheid und eigener Schalter (`stage/08a-claude-live-switch`)

Kein Transport, kein Verhalten aendert sich fuer laufende Runner.

1. `docs/DECISIONS/0012-claude-lane-live.md` (neu) — ADR mit den Owner-Entscheiden E1 bis E5
   aus dem Abschnitt «Offene Fragen». Der ADR haelt fest, dass er die Aussage «Claude bleibt
   gesperrt» aus ADR 0009 fuer die Lane `claude` abloest und dass `codex` gesperrt bleibt. Er
   loest die Spannung zwischen ADR 0006 (CLI-Profil), ADR 0002 (keine dauerhaften Secrets im
   Container) und `docs/LANES.md:354-355` (kein Hostprofil-Mount) gemaess E3.
2. `app/workers/live_config.py`, `tests/unit/test_live_config.py` — neue Funktion
   `load_claude_live_config(environment)`. Sie verlangt `DECK_LIVE_ENABLED` **und**
   `DECK_CLAUDE_LIVE_ENABLED` (je exakt `1` oder `true`, gleiche Pruefung wie `live_enabled`)
   sowie `DECK_CLAUDE_IMAGE` als digest-gepinntes Image (Muster `_IMAGE` aus `sandbox.py`),
   das nicht das Platzhalter-Image ist. Fehlercodes: `live_disabled`, `claude_live_disabled`,
   `claude_image_invalid`. `load_live_config` bleibt unveraendert und weist `claude` und
   `codex` weiter mit `live_lane_not_supported` ab.
3. `deploy/runner.env.example` — `DECK_CLAUDE_LIVE_ENABLED=0` und ein leeres
   `DECK_CLAUDE_IMAGE=` ergaenzen, mit einer Zeile Kommentar. Keine Werte.
4. `docs/LANES.md` — im Abschnitt «Claude in Etappe 12» einen Absatz «Schalter (Teil A)»:
   beide Flags noetig, noch kein Live-Executor.

### Teil B: Reinraum-Starter fuer echte Container (`stage/08b-cleanroom-live`)

5. `app/workers/sandbox.py`, `tests/unit/test_cleanroom.py` — `cleanroom_command` bekommt
   einen geschlossenen Netzmodus: `none` (Standard, unveraendert fuer Simulation und `codex`)
   oder `provider`. `provider` setzt das im Code fest benannte Netz `deck-claude-egress` und
   die festen Proxy-Variablen gemaess E2. Aufrufer koennen weiter weder Netz, Mounts, Programme
   noch Umgebung frei waehlen.
6. `app/workers/sandbox.py`, `tests/unit/test_cleanroom_live.py` (neu) — Live-Variante von
   `run_in_cleanroom` mit derselben Abfolge wie die Simulation: Vorpruefung (Daemon rootless,
   `claude --version` gleich `CLAUDE_VERSION`), genau ein Versuch, getrennter Aufraeumaufruf
   mit zwei Sekunden Budget, der das Abbruchsignal ignoriert. Misslingt das Aufraeumen, gilt
   der Versuch nie als Erfolg. Das Programm ist fest `docker`; der Socket kommt aus
   `DECK_DOCKER_HOST` und muss eine absolute `unix://`-Adresse sein. Tests setzen ein
   Fake-Programm an die Stelle von `docker`.
7. `tests/unit/test_cleanroom_live.py` — Negativfaelle: nicht rootless, falsche CLI-Version,
   Timeout, Abbruch, Aufraeumfehler, Ausgabegrenzen, TCP-Socket abgewiesen, Image ohne Digest
   abgewiesen, keine geerbten Host-, Provider- oder Proxy-Variablen.

### Teil C: Live-Executor und Verdrahtung (`stage/08c-claude-live-executor`)

8. `app/workers/lanes/claude.py`, `tests/unit/test_claude_executor.py` — Profil `live`
   (nur mit Live-Einstellungen, nie zusammen mit einer Simulation). `availability` meldet
   `live_ready` oder einen festen Grund (`rootless_required`, `cli_version_unsupported`,
   `model_not_configured`, `model_unsupported`). `execute` nutzt den Live-Starter aus Teil B
   mit dem konfigurierten Image und dem Netzmodus `provider`. Ergebnis- und Fehlerzuordnung
   bleiben wie in `docs/LANES.md:322-343`.
9. `app/workers/claude_lane.py`, `tests/integration/test_claude_lane.py` — eigene
   Gesundheitsgruende fuer Live, getrennt von `simulation_*`. Nur ein Executor mit Profil
   `live` attestiert Live-Herkunft; eine Simulation zaehlt nie als Live-Erfolg.
10. `app/workers/runner_main.py`, `tests/unit/test_claude_runner_cli.py` — Executor
    `claude-live`: nur mit `--live` und `--worker claude`, laedt `load_claude_live_config`,
    endet bei Verweigerung mit Exit 78. Jede Kreuzkombination (`ollama-live` mit `claude`,
    `claude-live` mit `ollama` oder `codex`) ist ein Parser-Fehler.
11. `app/workers/daemon.py`, `tests/unit/test_runner_daemon.py` — der Supervisor waehlt den
    Live-Executor nach Lane, reicht zusaetzlich nur `DECK_CLAUDE_LIVE_ENABLED`,
    `DECK_CLAUDE_IMAGE` und `DECK_DOCKER_HOST` weiter und endet ohne gueltige
    Claude-Konfiguration mit Exit 78. `codex` endet weiter mit `live_lane_not_supported`.
12. `tests/unit/test_live_config.py`, `tests/unit/test_ollama_live_executor.py`,
    `tests/unit/test_runner_daemon.py` — die Tests, die die Sperre heute festhalten, bewusst
    anpassen: `claude` ist nur mit beiden Flags und gueltigem Image live, `codex` nie. Kein
    Test wird abgeschwaecht, der Ollama oder Codex betrifft.
13. `tests/integration/test_claude_lane.py` — `local_only` wird von einem Live-Runner der
    Lane `claude` nie beansprucht; ein `cloud_redacted`-Auftrag verlaesst den Host nur mit
    Platzhaltern (Pruefung am Fake-Programm: empfangene Bytes gleich `payload.sent.md`).

### Teil D: Canary, Doku und Owner-Nachweise (`stage/08d-claude-live-canary`)

14. `scripts/live_canary.py`, `tests/unit/test_live_canary.py` — Option `--worker`
    (`ollama` als Standard, `claude` neu). Der Claude-Canary nutzt einen synthetischen
    `cloud_ok`-Auftrag und erwartet `review`, Profil `live`, Lane-Status `ok` / `live_ready`.
15. `docs/LANES.md` — Abschnitt «Claude in Etappe 12» auf den Live-Vertrag nachfuehren:
    Netzmodus, Flags, Vorpruefung, Aufraeumen, Grenzen.
16. `docs/OPERATIONS.md` — Aktivierung als Owner-Schritte: Image aus `docker/lane-claude/`
    bauen und Digest pruefen, Netz und Ausgangsweg gemaess E2 einrichten, Anmeldung gemaess E3,
    Flags nur in der privaten Runner-Datei, genau eine Instanz mit `max_parallel=1`, Rueckweg
    (Flags auf 0, Instanz stoppen).
17. `docs/ABNAHME_CHECKLISTE_ENTWURF.md`, `docs/STAND.md`, `CHANGELOG.md` — Owner-Nachweise
    als offene Punkte eintragen (siehe «Bewusst offen»), Stand nachfuehren.

## Abnahme (ausfuehrbare Befehle)

Fuer jeden Teil gilt die ganze Tabelle; die gezielten Laeufe gelten ab dem Teil, der die
Datei anlegt.

| Befehl | Erwartet |
|--------|----------|
| `make lint` | exit 0 |
| `make test` | exit 0, keine neuen Skips |
| `uv run alembic -x target=test check` | keine neue Revision noetig |
| `make reference` | exit 0 |
| `make validate` | exit 0 |
| `uv run pytest tests/unit/test_live_config.py -q` | alle bestanden (ab A) |
| `uv run pytest tests/unit/test_cleanroom.py tests/unit/test_cleanroom_live.py -q` | alle bestanden (ab B) |
| `uv run pytest tests/unit/test_claude_executor.py tests/unit/test_claude_runner_cli.py tests/unit/test_runner_daemon.py -q` | alle bestanden (ab C) |
| `uv run pytest tests/integration/test_claude_lane.py -q` | alle bestanden mit `DECK_TEST_DATABASE_URL` (ab C) |
| `grep -rnE -- "--network=host\|--privileged" app/` | keine Treffer |
| `git diff --stat origin/main -- schemas/ app/schemas/ alembic/` | leer |

Der Builder meldet je Befehl den Befehl und das Ende der Ausgabe. Nie eine Pruefung melden,
die nicht gelaufen ist.

## Bewusst offen

Diese Punkte sind Owner-Nachweise am Betriebsort und gehoeren nicht in einen PR
(`docs/LANES.md:347-358`):

- Image wirklich bauen; Digests, `claude --version`, Bibliotheken und Flags pruefen.
- Rootless-Daemon, UID, Dateisystem, Ressourcen und Aufraeumen bei Client-Absturz und
  Daemon-Neustart nachweisen.
- Den Ausgangsweg gemaess E2 nachweisen: Anbieter erreichbar, Host, Datenbanken und andere
  Dienste nicht.
- Abo-Anmeldung im getrennten Profil, Kontomodell und ausgeschaltete Zusatzabrechnung.
- Abgelaufene Anmeldung, Anbieter-Sperre, Rate-Limit und echte JSON-Abschlussmerkmale.
- Erster echter Canary-Lauf und inhaltliche Faelle des Owners.

Ebenfalls nicht in diesem Auftrag: Lane `codex`, Werkzeuge oder MCP in der Lane, mehrere
Turns, neue Lanes, Verkettung von Auftraegen, ein Nachzug von ADR 0008.

## Offene Fragen (TODO)

Der Owner beantwortet E1 bis E5 vor dem Start von Teil A mit Ja oder Nein. Die Empfehlung steht
jeweils dabei. Der Builder entscheidet keine dieser Fragen.

| ID | Frage | Empfehlung |
|----|-------|------------|
| E1 | Eigener Schalter `DECK_CLAUDE_LIVE_ENABLED` zusaetzlich zu `DECK_LIVE_ENABLED`? | Ja. Sonst schaltet das Ollama-Flag Claude mit ein. |
| E2 | Ausgangsweg: internes Docker-Netz ohne Route nach aussen plus ein Ausgangs-Proxy, der nur die Anbieter-Hosts durchlaesst? | Ja. Die Standard-Bridge ist laut `docs/LANES.md:352-353` keine Grenze. Status des Entwurfs: `unproven`, bis der Owner-Nachweis vorliegt. Der Proxy ist ein neuer kleiner Dienst und braucht den Betriebsplan aus Schritt 16. Dass die CLI einen Proxy ueber Umgebungsvariablen nutzt, ist vor Teil B an der offiziellen Doku zu belegen (`unproven`). |
| E3 | Anmeldung nur im benannten Volume `deck-claude-profile`, kein Hostprofil-Mount? | Ja. Der Owner meldet sich einmal selbst an; kein Zugangswert verlaesst das Volume. |
| E4 | Der Claude-Runner laeuft unter dem bestehenden Konto, das die Anmeldung haelt; keine Aenderung an Konten? | Ja (Owner-Vorgabe vom 2026-11-11). |
| E5 | Genau ein ausdruecklich gepinntes Modell in `lanes.claude.models`, kein Modell der staerksten Klasse, Zusatzabrechnung aus? | Ja. `CLAUDE.md` warnt vor Abrechnung ohne Rueckfrage in `claude -p`. |

TODO Planer: ADR 0008 nennt Codex in der Review-Rotation. Der Owner hat Codex am 2026-11-11
aus dem Team genommen. Ob ein Nachtrag zu ADR 0008 folgt, entscheidet der Owner; dieser Auftrag
aendert ADR 0008 nicht.
