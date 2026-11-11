# Bauauftrag 08-claude-lane-live

## Etappe

Ticket 8: Live-Profil fuer die Lane `claude` (Nachfolge von Etappe 12, die nur die Simulation
gebaut hat). Vier Teile A bis D. Jeder Teil ist eine eigene Builder-Session, ein Branch
`stage/08x-<slug>` und ein Draft-PR gegen `main`. Teil B beginnt erst, wenn Teil A gemergt ist,
und so weiter. Builder: Cursor mit dem Modell Opus (Owner-Aussage vom 2026-11-11, im Repo
nicht belegt). Externes Review: Grok. Planer: Claude. Status: geplant; die Owner-Entscheide
E1 bis E6 liegen vor, Teil A kann beginnen.

Abweichung von der Vorlage, bewusst: ein Dokument fuer vier PRs statt vier Dateien, und Grok
statt der Rotation aus ADR 0008, weil der Planer Claude ist und das Review von einem anderen
Anbieter kommen muss.

Herkunft der Vorgaben:

| Aussage | Quelle, Datum, Methode | Status |
|---------|------------------------|--------|
| GO fuer den Claude-Live-Schalter | Owner, 2026-11-11, Owner-Aussage in der Planungssitzung | unproven by repo |
| Codex ist nicht mehr im Team | Owner, 2026-11-11, Owner-Aussage | unproven by repo |
| Benutzerkonten am Betriebsort bleiben unveraendert | Owner, 2026-11-11, Owner-Aussage | unproven by repo |
| E1 bis E6: «Ja zu allen» | Owner, 2026-11-11, Owner-Aussage in der Planungssitzung | unproven by repo |
| Codestellen unter «Ausgangslage» | Repo, 2026-11-11, Dateien gelesen, zweifach gegengeprueft | proven |
| CLI nutzt `HTTPS_PROXY`, kein SOCKS, URL braucht ein Schema | `code.claude.com/docs/en/network-config`, 2026-11-11, Herstellerdoku gelesen | proven fuer die Doku, unproven fuer CLI 2.1.317 |
| Abo-Zugangsdaten liegen im Konfigurationsverzeichnis, `CLAUDE_CONFIG_DIR` verschiebt sie | `code.claude.com/docs/en/authentication`, 2026-11-11, Herstellerdoku gelesen | proven fuer die Doku |
| Internes Docker-Netz hat keine Route nach aussen, das Gateway bleibt erreichbar | `docs.docker.com/reference/cli/docker/network/create/`, 2026-11-11, Herstellerdoku gelesen | proven fuer die Doku |
| Rootless: Host-Loopback ist aus Containern standardmaessig gesperrt | `docs.docker.com/engine/security/rootless/`, 2026-11-11, Herstellerdoku gelesen | proven fuer die Doku |

Kein Server wurde beruehrt, kein echter CLI-Aufruf und kein Docker-Aufruf ausgefuehrt.

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
| Der Supervisor prueft die Live-Konfiguration an zwei Stellen und waehlt fest `ollama-live` | `app/workers/daemon.py:81-93`, `:131-136` |
| Der Supervisor reicht nur sieben Variablen weiter | `app/workers/daemon.py:20-28` |
| Ein Test bindet `deploy/runner.env.example` an genau diese Liste | `tests/unit/test_operations_service.py:383-388` |
| `run_process` verlangt einen absoluten Programmpfad | `app/workers/process.py:71` |
| Gesundheitsgruende der Lane sind fest `simulation_*` | `app/workers/claude_lane.py:87-92` |
| Der Canary ist fest auf Ollama gelegt (Lane, Datenklasse, Nutzlastvergleich, Log-Name, Zaehler) | `scripts/live_canary.py:46-55`, `:74`, `:102-115`, `:128`, `:140`, `:179`, `:183` |
| Die Gesundheitsansicht kennt nur gelistete Gruende; nur `live_ready` ergibt den Modus `live` | `app/audit/health.py:16-46`, `:147-169` |
| Ein `cloud_ok`-Auftrag braucht eine konfigurierte Blockliste | `app/privacy/gate.py:212-213` |
| `REVIEW.md` verbietet `docker`-Aenderungen im Code und neue Netzendpunkte | `REVIEW.md:9-10`, `:13-14` |
| API und Ollama-Runner laufen unter dem Dienstkonto, die CLI-Anmeldung liegt auf einem zweiten Konto | `docs/BETRIEB_OHNE_LAPTOP.md:30-31`, `:54-55`, `:110` |

Zwei Fallen:

- Wer nur die Lane-Pruefung in `live_config.py` lockert, baut mit `--live` weiter den
  Ollama-Runner, weil Runner und Supervisor im Live-Modus fest `ollama-live` waehlen.
- `DECK_LIVE_ENABLED` steht in der gemeinsamen Runner-Datei aller Instanzen
  (`deploy/systemd/deck-runner@.service:9`). Ein fuer Ollama gesetztes Flag darf Claude nie
  mit einschalten.

## Rahmen

- Tests und CI starten nie die echte CLI, nie Docker, bauen und laden kein Image, melden sich
  nirgends an und rufen keinen Anbieter auf. Der Live-Pfad wird nur mit einem Fake geprueft.
- Cloud nur ueber die offizielle CLI und die Abo-Anmeldung (ADR 0006). Kein API-Key, nirgends.
  `--bare` bleibt verboten (`docs/LANES.md:304-305`).
- Der CLI-Vertrag aus `claude_command` (`app/workers/sandbox.py:74-102`) bleibt unveraendert:
  ein Turn, Werkzeuge und MCP aus, keine Sitzung.
- `task.v1` und `result.v1` bleiben unveraendert. Keine neue Lane, kein neuer Enum-Wert,
  keine Migration. Braucht ein Schritt doch eine Migration: stoppen und den Planer fragen.
- `local_only` erreicht nie die Lane `claude`. `cloud_redacted` geht nur pseudonymisiert hinaus.
- Lane-Logs, Events, Fehlergruende und Prozessausgaben enthalten weder Prompt noch Antwort
  noch stderr noch Fehlertexte von Docker oder vom Anbieter.
- Keine Server-Eingriffe durch Agenten. Kein Schritt legt Konten an, aendert Konten oder
  kopiert Zugangsdaten zwischen Konten.
- Keine eigenen Hostnamen, IP-Literale, Namen oder Secrets im Repo. Oeffentliche Hosts des
  Anbieters stehen nur als Verweis auf die Herstellerdoku in `docs/`, nie im Code; die
  wirksame Liste liegt in der privaten Proxy-Konfiguration am Betriebsort. Die festen Namen
  `deck-claude-egress`, `deck-egress-proxy`, `deck-claude-profile` und `deck-lane-claude`
  sind Docker-Bezeichner, keine Hostnamen der Infrastruktur.
- Keine neue Python-Abhaengigkeit. Der Ausgangs-Proxy ist ein neuer Betriebsbaustein; der
  Builder richtet ihn nicht ein und waehlt keine Software dafuer (E2).
- Code und Commits Englisch (Conventional Commits), `docs/` de-CH ohne Eszett.
- Jeder Teil berichtigt die Doku-Saetze, die er falsch macht, im selben PR.
- Nach zwei gescheiterten Versuchen an derselben Stelle: stoppen und dem Planer melden.

## Schritte (atomar, je ein Commit)

### Teil A: Entscheid und Konfigurationspruefung (`stage/08a-claude-live-decision`)

Kein Transport. Fuer laufende Runner aendert sich nichts.

1. `docs/DECISIONS/0012-claude-lane-live.md` (neu) — ADR mit den Owner-Entscheiden E1 bis E6
   aus «Offene Fragen». Der ADR loest die Aussage «Claude bleibt gesperrt» aus
   `docs/DECISIONS/0009-ollama-loopback-http.md:20` fuer die Lane `claude` ab; `codex` bleibt
   gesperrt. Er haelt die Loesung der Spannung zwischen ADR 0006 (CLI-Profil), ADR 0002 (keine
   dauerhaften Secrets im Container) und `docs/LANES.md:354-355` (kein Hostprofil-Mount) fest.
2. `REVIEW.md` — enge Ausnahme gemaess E6 in Abschnitt 1: Der Reinraum-Starter in
   `app/workers/sandbox.py` darf den lokalen Docker-Client mit dem festen Vertrag aufrufen.
   Alles andere in Abschnitt 1 bleibt.
3. `app/workers/live_config.py`, `tests/unit/test_live_config.py` — neue Funktion
   `load_claude_live_config(environment)`. Sie verlangt in dieser Reihenfolge:
   `DECK_LIVE_ENABLED` und `DECK_CLAUDE_LIVE_ENABLED` (je exakt `1` oder `true`, gleiche
   Pruefung wie `live_enabled`), `DECK_CLAUDE_IMAGE` in der Form
   `deck-lane-claude@sha256:<64 hex>` (kein anderer Name, nicht das Platzhalter-Image) und
   `DECK_DOCKER_HOST` als absolute `unix://`-Adresse. Fehlercodes: `live_disabled`,
   `claude_live_disabled`, `claude_image_invalid`, `docker_host_invalid`.
   `load_live_config` bleibt unveraendert und weist `claude` und `codex` weiter mit
   `live_lane_not_supported` ab; der Test dazu (`tests/unit/test_live_config.py:34-37`)
   bleibt unveraendert gruen. Neue Tests: Ollama-Flag allein reicht nicht,
   `DECK_CLAUDE_LIVE_ENABLED` allein reicht nicht, falsche Werte, fremder Image-Name,
   Image ohne Digest, TCP-Socket.
4. `docs/LANES.md` — im Abschnitt «Claude in Etappe 12» die Saetze «Es gibt keinen
   Executable-Override oder Live-Schalter» (`:269`) und «Es gibt keinen automatischen Login
   oder Live-Schalter» (`:360`) berichtigen: Es gibt eine Konfigurationspruefung nach ADR 0012,
   aber noch keinen Live-Executor.

`deploy/runner.env.example` aendert sich in Teil A **nicht** (siehe Schritt 14).

### Teil B: Reinraum-Starter fuer echte Container (`stage/08b-cleanroom-live`)

5. `app/workers/sandbox.py`, `tests/unit/test_cleanroom.py` — `cleanroom_command` bekommt
   einen geschlossenen Netzmodus `none` (Standard) oder `provider`.
   - `provider` ist nur fuer `Worker.CLAUDE` erlaubt. `Worker.CODEX` mit `provider` und das
     Platzhalter-Image mit `provider` werfen `ValueError`. Die Simulation `run_in_cleanroom`
     weist `provider` ab.
   - `provider` setzt fest `--network=deck-claude-egress`,
     `--env=HTTPS_PROXY=http://deck-egress-proxy:3128`, `--env=HTTP_PROXY=` mit demselben Wert
     und `--env=NO_PROXY=` (leer). Nichts davon ist von aussen waehlbar.
   - Jeder Containerbefehl, auch mit `none`, bekommt fest `--pull=never` und
     `--log-driver=none`.
   - Tests: fuer beide Netzmodi steht genau ein `--network=`-Argument im Befehl, und es
     lautet `none` oder `deck-claude-egress`; der Codex-Befehl enthaelt nie
     `deck-claude-profile` und nie das Egress-Netz (`tests/unit/test_codex_cleanroom.py`
     bleibt als Regressionsprobe gruen); der Befehl enthaelt keines von `--network=host`,
     `--privileged`, `--cap-add`, `--device`, `--volume`, `docker.sock`.
6. `app/workers/sandbox.py`, `tests/unit/test_cleanroom_live.py` (neu) — Live-Starter
   `run_in_cleanroom_live`. Er nimmt keine `CleanroomSimulation` an.
   - Programm: eine absolute Modulkonstante fuer den Docker-Client. Kein `PATH`-Lookup,
     keine Umgebungsvariable, kein Konstruktor- oder CLI-Argument waehlt das Programm.
   - Umgebung des Clients: genau `DOCKER_HOST` (aus der geprueften Konfiguration),
     `DOCKER_CONFIG` auf ein leeres privates Temp-Verzeichnis, `LANG`, `LC_ALL`. Kein
     `DOCKER_CONTEXT`, kein `HOME`, keine Proxy-Variablen.
   - Vorpruefung, in dieser Reihenfolge, jeweils mit festem Grund bei Verweigerung:
     a) Sweep: alle Container mit Namenspraefix `deck-attempt-` entfernen und ihre
        Abwesenheit pruefen, sonst `cleanroom_cleanup_failed`;
     b) `info` mit `--format '{{json .SecurityOptions}}'` muss den exakten Eintrag
        `name=rootless` enthalten, sonst `rootless_required`;
     c) `image inspect` des konfigurierten Images muss gelingen, sonst `image_missing`
        (kein Pull);
     d) `network inspect deck-claude-egress` muss `Internal=true` melden, sonst
        `egress_network_invalid`;
     e) Versionsprobe als eigener geschlossener Befehl: gleiches Image, `--network=none`,
        **ohne** Profil-Volume, eigener Name `deck-attempt-<uuid>`, gleiches Aufraeumen; die
        Ausgabe muss `CLAUDE_VERSION` enthalten, sonst `cli_version_unsupported`. Das
        Ausgabeformat von `claude --version` ist `unproven`; der Vergleich steht an einer
        Stelle, damit der Owner-Nachweis ihn berichtigen kann.
   - Versuch: genau ein `run` mit dem Vertrag aus Schritt 5, Bytes ueber stdin.
   - Aufraeumen: eigener Aufruf `rm --force <name>` mit zwei Sekunden Budget, der das
     Abbruchsignal ignoriert. Aufgeraeumt heisst: danach meldet `container inspect <name>`
     «nicht vorhanden». Nur das zaehlt, der Exit-Code von `rm` allein nicht. Ein bereits
     entfernter Container ist aufgeraeumt. Misslingt das Aufraeumen, gilt der Versuch nie
     als Erfolg.
   - Rueckgabe-Grund bei Erfolg: `live_completed`. Der Simulationsstarter meldet diesen
     Grund nie.
7. `tests/unit/test_cleanroom_live.py` — Tests ersetzen `sandbox.run_process` per
   monkeypatch (wie die bestehenden Tests in `tests/unit/test_cleanroom.py`); ein Test
   schlaegt fehl, wenn das echte Programm je gestartet wuerde. Faelle: nicht rootless;
   Image fehlt; Netz fehlt; Netz nicht intern; falsche CLI-Version; verwaister
   `deck-attempt-*`-Container bleibt (kein Versuch); Timeout; Abbruch; Container schon
   entfernt; Entfernen misslingt; Ausgabegrenzen; die Umgebung des Clients enthaelt nur die
   erlaubten Schluessel; ein fremdes `docker` im `PATH` aendert nichts; eine Markierung in
   stdout, stderr und in einem Fehlerobjekt des Fakes erscheint in keinem Rueckgabewert
   ausser im begrenzten Ergebnistext.
8. `docs/LANES.md` — den Satz «Das Netz bleibt `none`; dieser Vertrag kann keinen echten
   Anbieter erreichen» (`:312-313`) berichtigen: `none` bleibt Standard, `provider` gibt es
   nur fuer den Live-Starter der Lane `claude`.

### Teil C: Live-Executor und Verdrahtung (`stage/08c-claude-live-executor`)

9. `app/workers/lanes/claude.py`, `tests/unit/test_claude_executor.py` — Profil `live`, nur
   mit den geprueften Live-Einstellungen, nie zusammen mit einer Simulation. `availability`
   meldet `live_ready` oder genau einen dieser Gruende: `rootless_required`, `image_missing`,
   `egress_network_invalid`, `cleanroom_cleanup_failed`, `cli_version_unsupported`,
   `docker_unavailable`, `model_not_configured`, `model_unsupported`. Modellregel gemaess
   E5: `lanes.claude.models` enthaelt genau einen verschiedenen Wert, und dieser ist eine
   volle Modell-ID mit Versionsteil; Kurznamen ohne Versionsteil werden abgewiesen
   (`model_unsupported`). `execute` nutzt den Live-Starter mit dem konfigurierten Image und
   dem Netzmodus `provider`. Ergebnis- und Fehlerzuordnung bleiben wie in
   `docs/LANES.md:322-343`. Tests: Profil `live` ruft nie `_fake_cleanroom_cli.py` auf,
   Profil `simulation` nie den Docker-Client; zwei verschiedene Modelle und Kurznamen
   werden abgewiesen.
10. `app/workers/claude_lane.py`, `app/audit/health.py`, `tests/unit/test_audit_health.py`,
    `tests/integration/test_claude_lane.py` — die heute festen Gruende `simulation_*`
    (`app/workers/claude_lane.py:87-92`) haengen vom Profil ab; alle neuen Gruende aus
    Schritt 9 kommen in die bekannte Liste der Gesundheitsansicht, damit sie nicht als
    `unknown` erscheinen, und die Modusbestimmung (`app/audit/health.py:147-169`) ordnet
    sie dem Modus `live` zu. Weil die Live-Verfuegbarkeit jetzt Aufrufe nach aussen macht,
    uebernimmt `ClaudeLaneRunner` im Profil `live` das Muster `probe_pending` plus
    `probe_generation` der Ollama-Lane (`app/workers/ollama_lane.py:42-55`, `:72-77`); dazu
    ein Fall fuer `claude` in `tests/integration/test_lane_probe_generation.py`. Per Test
    belegen: Nur ein Executor mit Profil `live` attestiert Live-Herkunft
    (`app/workers/runner.py:72-77`).
11. `app/workers/runner_main.py`, `tests/unit/test_claude_runner_cli.py`,
    `tests/unit/test_ollama_live_executor.py` — Executor `claude-live`. Reihenfolge und
    Ergebnis je Kombination:

    | Aufruf | Ergebnis |
    |--------|----------|
    | `--live --worker ollama --executor ollama-live` | wie heute |
    | `--live --worker claude --executor claude-live`, Konfiguration gueltig | Live-Lauf |
    | `--live --worker claude`, Konfiguration ungueltig | Exit 78 mit dem Fehlercode, vor jedem DB-Zugriff |
    | `--live --worker claude --executor ollama-live` | Exit 78 (`live_lane_not_supported`), wie heute in `tests/unit/test_ollama_live_executor.py:335-357` |
    | `--live --worker codex`, jeder Executor | Exit 78 (`live_lane_not_supported`) |
    | `--live --worker ollama --executor claude-live` | Parser-Fehler |
    | `--simulate` mit `claude-live` | Parser-Fehler |

12. `app/workers/daemon.py`, `tests/unit/test_runner_daemon.py` — der Supervisor waehlt den
    Live-Executor nach Lane und endet ohne gueltige Claude-Konfiguration mit Exit 78. Beide
    Pruefstellen (`run_loop`, `:81-82`, und `main`, `:131-136`) nutzen fuer `claude` die neue
    Funktion. Der bestehende Fall in `tests/unit/test_runner_daemon.py:42` aendert sich im
    selben Commit: `claude` endet ohne `DECK_LIVE_ENABLED` mit `live_disabled` und ohne das
    Claude-Flag mit `claude_live_disabled`; `codex` endet weiter mit
    `live_lane_not_supported`. Er
    reicht `DECK_CLAUDE_LIVE_ENABLED`, `DECK_CLAUDE_IMAGE` und `DECK_DOCKER_HOST` **nur** an
    Kinder der Lane `claude` weiter. Fuer die Lane `claude` im Live-Modus ruft er den Sweep
    aus Schritt 6 beim Start, vor jedem Versuch und beim Stoppen auf; sein Zeitlimit je
    Versuch ist groesser als Versuchs-Zeitlimit plus Aufraeumbudget. Tests: Ollama- und
    Codex-Kinder erhalten die drei Variablen nie; `DECK_CLAUDE_LIVE_ENABLED=1` allein
    schaltet nichts live; ein Stopp waehrend eines Versuchs fuehrt zum Sweep.
13. `tests/integration/test_claude_lane.py` — mit `DECK_TEST_DATABASE_URL`: `local_only`
    wird von einem Live-Runner der Lane `claude` nie beansprucht; ein `cloud_redacted`-Auftrag
    verlaesst den Host nur mit Platzhaltern (empfangene Bytes gleich `payload.sent.md`); eine
    Markierung, die der Fake in stdout, stderr und Fehlerobjekt schreibt, und der Prompt
    fehlen in Lauf-Log, Events, `lane_status.reason`, `result.error.message` und in der
    Ausgabe von `runner_main`.
14. `deploy/runner.env.example`, `app/workers/daemon.py`,
    `tests/unit/test_operations_service.py` — im selben Commit die drei Namen in
    `_ENVIRONMENT` und in der Vorlage ergaenzen (`DECK_CLAUDE_LIVE_ENABLED=0`, leeres
    `DECK_CLAUDE_IMAGE=`, leeres `DECK_DOCKER_HOST=`), weil
    `test_runner_env_example_contains_only_daemon_allowlist` beide Listen gleich verlangt.
15. `docs/OPERATIONS.md` (`:80-82`, `:93-97`), `docs/BETRIEB_OHNE_LAPTOP.md` (`:41-44`) — die
    Saetze «Live nur fuer Ollama» berichtigen: Fuer `claude` gibt es einen Live-Executor, der
    ohne die Owner-Nachweise aus Teil D nicht eingeschaltet wird. Die Tabelle der Exit-Codes
    bekommt die neuen Codes aus Schritt 3.

### Teil D: Canary, Doku und Owner-Nachweise (`stage/08d-claude-live-canary`)

16. `scripts/live_canary.py`, `tests/unit/test_live_canary.py`,
    `tests/integration/test_live_canary.py` — Option `--worker` (`ollama` als Standard,
    `claude` neu). `canary_task` nimmt die Lane als Argument; Nutzlastvergleich, Log-Name
    und Lane-Zaehler richten sich nach ihr. Der Claude-Canary nutzt einen synthetischen
    `cloud_ok`-Auftrag, erlaubt nur die Lane `claude`, prueft `claude.log` und erwartet
    `review`, Profil `live`, Lane-Status `ok` / `live_ready`. Er braucht eine konfigurierte
    Blockliste (`DECK_BLOCKLIST_PATH`), sonst weist die Datenschranke ihn ab; ist der
    Routine-Wake eingeschaltet, erzeugt er wie jeder `cloud_ok`-Auftrag eine Wake-Zeile
    (`app/transport/outbox.py:60-69`). Beides steht danach in `docs/LIVE_CANARY.md`.
    `--worker claude --submit` prueft zuerst
    `load_claude_live_config` und ist ein Owner-Schritt am Betriebsort, weil er
    Abo-Kontingent verbraucht. Tests pruefen nur `check_canary` mit Datenbankzeilen und
    rufen keinen Starter auf; der bestehende Fall «Simulation faellt durch» gilt auch fuer
    `claude`.
17. `docs/LANES.md`, `docs/LIVE_CANARY.md` (`:16-17`, `:27-29`) — Live-Vertrag der Lane
    `claude`: Netzmodus, Flags, Vorpruefung, Aufraeumen, Grenzen, Canary.
18. `docs/OPERATIONS.md` — Aktivierung als Owner-Schritte, in dieser Reihenfolge. Die drei
    Nachweise Werkzeugabschaltung, Rootless-Isolation und Netzgrenze sind erfasst, **bevor**
    ein Flag gesetzt wird.
    a) Image aus `docker/lane-claude/` bauen, pruefen, wie es digest-genau adressiert wird;
    b) Netz `deck-claude-egress` als internes Netz anlegen; den Proxy gemaess E2 mit
       `docker network connect` zusaetzlich an eine eigene normale Bridge haengen;
    c) Volume `deck-claude-profile` anlegen, beschreibbar fuer UID 10001; einmalige
       Abo-Anmeldung durch den Owner im selben Image;
    d) Flags nur in der privaten Runner-Datei setzen; genau eine Instanz; `max_parallel`
       bleibt 1 (der Wert gilt fuer alle Lanes zusammen, `app/db/queue.py:71-80`);
    e) Rueckweg: beide Flags auf 0, Instanz stoppen, danach verbliebene
       `deck-attempt-*`-Container entfernen und ihre Abwesenheit pruefen.
19. `docs/ABNAHME_CHECKLISTE_ENTWURF.md`, `docs/STAND.md`, `CHANGELOG.md`,
    `docker/lane-claude/VERSION`, `docker/lane-claude/Dockerfile` — Owner-Nachweise aus
    «Bewusst offen» als offene Punkte eintragen, Stand nachfuehren, Kommentare «nie gebaut»
    in den Docker-Vorlagen dem Stand anpassen.

## Abnahme (ausfuehrbare Befehle)

Vorbedingung fuer die Datenbank-Befehle: `DECK_TEST_DATABASE_URL` zeigt auf eine erreichbare
`_test`-Datenbank. Ohne sie werden die Integrationstests uebersprungen; dann gilt der Teil
erst mit dem gruenen CI-Lauf als geprueft.

Fuer jeden Teil:

```bash
make lint
make test
uv run alembic -x target=test upgrade head
uv run alembic -x target=test check
uv run alembic -c alembic_kb.ini upgrade head
uv run alembic -c alembic_kb.ini check
make reference
make validate
grep -rnF -e "--network=host" -e "--privileged" -e "--cap-add" -e "--device" -e "docker.sock" app/
git fetch origin
git diff --stat origin/main...HEAD -- schemas/ app/schemas/ alembic/ alembic_kb/
```

Erwartet: `make lint`, `make test`, die vier Alembic-Befehle, `make reference` und
`make validate` enden mit exit 0, ohne neue Revision; mit Test-Datenbank gibt es keine Skips
in `tests/integration/test_claude_lane.py` und `tests/integration/test_live_canary.py`;
`grep` findet nichts; `git diff --stat` ist leer.

Zusaetzlich je Teil:

```bash
# Teil A
uv run pytest tests/unit/test_live_config.py -q
# Teil B
uv run pytest tests/unit/test_cleanroom.py tests/unit/test_cleanroom_live.py -q
# Teil C
uv run pytest tests/unit/test_claude_executor.py tests/unit/test_claude_runner_cli.py tests/unit/test_runner_daemon.py tests/unit/test_ollama_live_executor.py tests/unit/test_operations_service.py tests/unit/test_audit_health.py -q
uv run pytest tests/integration/test_claude_lane.py -q
# Teil D
uv run pytest tests/unit/test_live_canary.py tests/integration/test_live_canary.py -q
```

Erwartet: alle bestanden. Der Builder meldet je Befehl den Befehl und das Ende der Ausgabe.
Nie eine Pruefung melden, die nicht gelaufen ist.

## Risiken und Rueckweg

| Risiko | Gegenmassnahme im Auftrag | Rueckweg |
|--------|---------------------------|----------|
| Erster echter Datenabfluss an einen Anbieter | `local_only` bleibt gesperrt, Pseudonymisierung unveraendert, Schritt 13 | beide Flags auf 0 |
| Ein Container ueberlebt einen Stopp und behaelt Anmeldung und Netz | Sweep in Schritt 6 und 12, Aufraeumdefinition | Schritt 18 e |
| Das Ollama-Flag schaltet Claude mit ein | zweites Flag, Schritt 3, Tests in Schritt 12 | `DECK_CLAUDE_LIVE_ENABLED=0` |
| Das Netz ist keine Grenze | Vorpruefung `Internal=true`, E2, Owner-Nachweis | Flag auf 0, Netz entfernen |
| Unbemerkte Kosten | E5, genau ein Modell, Zusatzabrechnung aus | Flag auf 0 |
| Ein Teil wird halb gemergt | jeder Teil ist allein mergebar und ohne Flags wirkungslos | PR des Teils zuruecknehmen |

Kein Teil enthaelt eine Migration; ein Zuruecknehmen braucht deshalb kein Downgrade.

## Bewusst offen

Diese Punkte sind Owner-Nachweise am Betriebsort und gehoeren nicht in einen PR
(`docs/LANES.md:347-358`, `docs/ABNAHME_CHECKLISTE_ENTWURF.md:76-77`):

- Image wirklich bauen; Digests, `claude --version`, Bibliotheken und Flags pruefen; klaeren,
  ob ein lokal gebautes Image in der Form `name@sha256:...` adressierbar ist (`unproven`).
- Die gepinnte CLI nimmt `--setting-sources ""` an (`unproven`).
- Werkzeuge, MCP, Hooks und Plugins sind wirklich abgeschaltet.
- Rootless-Daemon, UID, Dateisystem, Ressourcen; Aufraeumen bei Client-Absturz und
  Daemon-Neustart; die Meldung von `rm --force` bei schon entferntem Container; ob das
  Aufraeumbudget von zwei Sekunden am Betriebsort reicht.
- Der Docker-Client arbeitet mit genau der Umgebung aus Schritt 6 gegen den rootless
  Daemon (`unproven`); das Image startet `claude --version` ohne Netz und ohne Anmeldung
  (`unproven`).
- Ausgangsweg gemaess E2: Der Anbieter ist erreichbar. Nicht erreichbar sind der Host ueber
  das Gateway des internen Netzes, die Datenbanken, andere Dienste und private Adressen. Eine
  DNS-Abfrage aus dem Reinraum nach aussen scheitert. Welche der dokumentierten Hosts ein
  Lauf wirklich braucht, zeigt das Proxy-Log.
- Kein anderer Container mountet `deck-claude-profile`.
- Abo-Anmeldung im Volume, Erneuerung der Anmeldung, Kontomodell, ausgeschaltete
  Zusatzabrechnung.
- Keine versteckten Wiederholungen (`CLAUDE_CODE_MAX_RETRIES=0` wirkt, `unproven`; ob
  weitere Wiederholungs-Variablen der CLI gesetzt werden muessen, klaert der Owner-Nachweis);
  abgelaufene Anmeldung, Anbieter-Sperre, Rate-Limit, echte JSON-Abschlussmerkmale.
- Erster echter Canary-Lauf und inhaltliche Faelle des Owners.

Ebenfalls nicht in diesem Auftrag: Lane `codex`, Werkzeuge oder MCP in der Lane, mehrere
Turns, neue Lanes, Verkettung von Auftraegen, ein zweites Runner-Setup unter einem anderen
Konto, ein Nachzug von ADR 0008.

## Offene Fragen (TODO)

Stand 2026-11-11: Der Owner hat E1 bis E6 mit «Ja zu allen» beantwortet. Es gilt jeweils die
Empfehlung in der rechten Spalte; ADR 0012 (Schritt 1) haelt sie fest. Der Builder entscheidet
keine dieser Fragen neu.

| ID | Frage | Empfehlung |
|----|-------|------------|
| E1 | Eigener Schalter `DECK_CLAUDE_LIVE_ENABLED` zusaetzlich zu `DECK_LIVE_ENABLED`? | Ja. Sonst schaltet das Ollama-Flag Claude mit ein. |
| E2 | Ausgangsweg: internes Docker-Netz plus ein kleiner HTTP-CONNECT-Proxy, der nur im internen Netz lauscht, nur Port 443 zu exakt gelisteten Anbieter-Hosts durchlaesst und IP-Literale sowie private Zieladressen ablehnt? Wer richtet ihn ein und pinnt ihn? | Ja; Einrichtung und Pin beim Owner oder seinem Betriebs-Bot. Die Standard-Bridge ist laut `docs/LANES.md:352-353` keine Grenze. Liste der Hosts: Herstellerdoku `code.claude.com/docs/en/network-config`. |
| E3 | Anmeldung nur im Volume `deck-claude-profile`, das nur der Reinraum der Lane `claude` und der Owner-Anmeldeschritt mounten; kein Hostprofil-Mount? | Ja. |
| E4 | Der Claude-Runner laeuft unter dem **Dienstkonto**, das schon API und Ollama-Runner betreibt. Der Owner meldet sich dort einmal neu im Volume an. Das zweite Konto mit der vorhandenen CLI-Anmeldung bleibt unberuehrt, nichts wird kopiert. Voraussetzung: Das Dienstkonto hat einen rootless Docker-Daemon. | Ja. Bei Nein stoppt dieser Auftrag; ein Runner unter dem zweiten Konto braucht ein eigenes Setup (Unit, Runner-Datei, Zugriff auf `runs/`) und einen eigenen Auftrag. |
| E5 | Genau ein Modell in `lanes.claude.models`, als volle Modell-ID mit Versionsteil (im Code erzwungen); nicht die staerkste Klasse und Zusatzabrechnung aus (Owner-Nachweis)? | Ja. `CLAUDE.md` warnt vor Abrechnung ohne Rueckfrage in `claude -p`. |
| E6 | `REVIEW.md` bekommt die enge Ausnahme aus Schritt 2? | Ja. Ohne sie kann kein Review von Teil B ohne Befund enden. |

TODO Planer: ADR 0008 und `AGENTS.md:27` nennen Codex in der Review-Rotation. Ob ein Nachtrag
folgt, entscheidet der Owner; dieser Auftrag aendert beide nicht.
