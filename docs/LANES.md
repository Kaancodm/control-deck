# Lanes, Simulation und lokaler Live-Adapter

## Ollama in Etappe 9

Der Owner hat `ollama run` mit Fake-CLI-Tests gewaehlt. Der Adapter in
`app/workers/lanes/ollama.py` unterstuetzt deshalb ein ausdrueckliches Profil `simulation`.
Es startet nur die mitgelieferte Fake-CLI mit dem aktuellen Python-Interpreter. Es gibt keine
Option fuer ein beliebiges Executable, keinen echten Modellaufruf und keinen Download.
Das Standardprofil `disabled` bleibt ohne Prozessstart `unavailable` mit dem Grund
`live_profile_unavailable`.

Die Modellkennung stammt aus `lanes.ollama.models.<kind>` oder `.default` in der lokalen
Routing-Konfiguration. Im Code stehen keine echten Modellnamen. Eine Lane ganz ohne
Modelle, ein konfiguriertes, aber im Inventar fehlendes Modell, eine deaktivierte Lane oder
Pause verhindern den Claim und verbrauchen keinen Versuch; die Aufgabe bleibt `queued` und
`lane_status` nennt den Grund. Die Inventarpruefung laeuft vor jedem Poll ausserhalb einer
DB-Transaktion. Der Claim prueft die aktuelle Modellwahl erneut gegen die zuvor geprueften
Modelle.

Fehlt dagegen fuer den `kind` einer Aufgabe ein Eintrag und gibt es kein `default`, ist das
eine dauerhafte Fehlkonfiguration, die kein spaeterer Poll beheben kann. Die Queue setzt die
Aufgabe ohne Versuch auf `blocked` und schreibt das Ereignis `task.block` mit Grund
`routing`, Code `model_not_configured`, Lane und `kind`. Im selben Commit wird
`lane_status` auf `unavailable` / `model_not_configured` gesetzt; andere Aufgaben mit
konfiguriertem Modell werden im selben Poll weiter bearbeitet. Der naechste Poll ohne
betroffene Aufgabe meldet wieder `ok`. Diese Regel gilt nur fuer Lanes mit vorheriger
Inventarpruefung; Claims ohne Inventar verhalten sich wie bisher.

Der Host-Runner gibt genau die UTF-8-Bytes aus `payload.sent.md` auf stdin an
`run <modell>` weiter. Es wird kein zusaetzlicher Prompt davor- oder dahintergesetzt.
Die SHA-256-Pruefung, lokale Rueckabbildung, Acceptance und Ergebnis-Fencing bleiben im
Rahmen aus Etappe 8. Die CLI erhaelt weder den Originalauftrag noch die Redaction-Map.

```bash
uv run python -m app.workers.runner_main --worker ollama --simulate --once --executor ollama-fake-cli
```

Dieser Befehl verwendet die konfigurierte lokale Testdatenbank und eine vorhandene
Routing-Konfiguration. Er ist keine Live-Aktivierung. Der vorhandene reine Fake-Executor
bleibt mit `--executor simulation` verfuegbar.

## Prozessgrenzen und Fehler

`app/workers/process.py` verwendet Argumentlisten ohne Shell, ein separates Prozessgruppen-
Signalziel und eine ausdrueckliche Umgebungs-Allowlist. Geerbte Provider-Variablen,
`OLLAMA_HOST`, Proxy-Werte und das Benutzer-HOME werden nicht weitergegeben.
stdout und stderr werden gleichzeitig gelesen und getrennt begrenzt; Standardgrenzen
sind 4 MiB und 64 KiB. Timeout und Abbruch beenden und ernten die Prozessgruppe.
Die Dauer ist das Minimum aus Task-Limit, Adapter-Limit (Standard 600 Sekunden) und
verbleibender Owner-Deadline. Pro Versuch gibt es hoechstens einen `run`-Aufruf und keinen
versteckten Retry. Inventarpruefungen verwenden ein eigenes begrenztes `list`.

| Ergebnis | `result.v1` |
|---|---|
| Erfolgreiche nichtleere UTF-8-Ausgabe | `succeeded`, anschliessend zentrale Acceptance |
| Fehlender Prozess, fehlendes Modell, Exit-Code ungleich null | `failed`, `worker_unavailable` |
| Transport-Timeout | `failed`, `timeout` |
| Kooperativer Abbruch | `cancelled` |
| Leere Ausgabe | `failed`, `acceptance_failed` |
| Ausgabegrenze, ungueltiges UTF-8, interner Prozessfehler | `failed`, `internal` |

Bei Timeout, Abbruch und Fehlern werden Teilantworten nicht als Ergebnis weitergegeben.
Das unveraenderliche Lane-Log schreibt der Host-Runner; es enthaelt sichere Laufmetadaten,
keinen Prompt, Antworttext oder stderr. Die eingefrorenen v1-Schemas bleiben unveraendert.
Backoff und Fallback folgen in Etappe 10.

## Persistierte Gesundheit

Migration `0010` ergaenzt `lane_status` mit Lane, Status, sicherem Grund, `updated_at` und
`last_success_at`. Erlaubte Status sind `ok`, `unavailable`, `auth_required`, `paused`.
Zeitstempel kommen aus der DB-Uhr; Fehler erhalten die letzte positive Beobachtung.
Die Aktualisierung erfolgt in kurzen eigenen Transaktionen.

`ok` mit Grund **`simulation_ready`** bezeichnet ausschliesslich eine erfolgreiche
Fake-Inventarpruefung. Es bestaetigt weder einen erfolgreichen echten Modelllauf noch eine
verfuegbare Live-Lane. `last_success_at` ist der Zeitpunkt dieser Gesundheitsbeobachtung.
Das deaktivierte Profil meldet **`unavailable` / `live_profile_unavailable`**.
Der neue Live-Vertrag folgt unten; `live_ready` ist eine positive Vorpruefung,
kein Nachweis fuer Modellqualitaet. Die persistierte Anzeige beschreibt die letzte
Vorpruefung samt Zeitpunkt; ein gestoppter Runner aktualisiert sie nicht mehr.

## Inbetriebnahme

Der CLI-Live-Pfad aus Etappe 9 bleibt gesperrt. ADR 0009 erlaubt die nachfolgend
beschriebene HTTP-Anbindung. Die offizielle CLI bietet keinen
No-Pull-Schalter: `run` kann ein fehlendes Modell automatisch laden. Eine vorgaengige
Inventarpruefung schliesst das Rennen bis zum Aufruf nicht. Ein `:local`-Suffix begrenzt die
Modellquelle, verhindert aber den Download nicht. `OLLAMA_NO_CLOUD` muss im Daemon gelten;
eine Variable ausschliesslich im CLI-Kindprozess konfiguriert einen laufenden Daemon nicht.
Multimodale CLI-Pfade koennen zudem im Prompt genannte Bild-/Audiodateien vom Host lesen.
Ein Exit-Code null allein beweist fuer alle CLI-Pfade keinen vollstaendigen Modellabschluss.

Der Live-Adapter verwendet deshalb ausschliesslich die lokale HTTP-API. Echte Logins,
Serveraenderungen, Modellinstallationen und Modelltests wurden nicht ausgefuehrt.
Die semantische Qualitaet prueft erst das vom Owner erstellte Referenzset bei der
Inbetriebnahme. Die folgenden Nachweise betreffen Quellcode und synthetische Tests.

### HTTP-Vertrag gemaess ADR 0009

Die Aktivierung verlangt die Runner-Variablen `DECK_LIVE_ENABLED` (exakt `1` oder
`true`) und `DECK_OLLAMA_URL`. Nur HTTP, Loopback-Literale und ein expliziter Port sind
erlaubt. Ohne Freigabe bleibt der Daemon mit Exit 78 gestoppt. Laufzeitfehler eines
Ollama-Dienstes verhindern Claims, halten aber die Supervisor-Schleife am Leben.

Die Vorpruefung kontrolliert die exakte Version **0.40.14**, alle konfigurierten
Modellkennungen gegen `/api/tags` und ihre Faehigkeiten mittels `/api/show`.
Explizite Tags sind Pflicht; Cloud-Kennungen, Remote-Felder und multimodale
Faehigkeiten werden abgewiesen. Der HTTP-Modellname traegt zusaetzlich `:local`.
Das Inventar und die Provenienz behalten die konfigurierte Kennung ohne Quellensuffix.
Damit wird auch ein zwischenzeitlich auf Remote geaenderter Alias vor Inferenz abgewiesen.

Ein Versuch sendet eine User-Nachricht mit genau dem archivierten Text. JSON-Escaping
veraendert die logischen Nachrichtenbytes nicht; `payload_hash` bezeichnet den
UTF-8-Inhalt, nicht den gesamten JSON-Request. Es gibt keine `images` oder `tools`.
`stream: true`, Temperatur null, kurze Modellhaltezeit und ein ausdrueckliches
Kontextbudget gelten pro Request. `format: json` wird nur fuer JSON-Schema-Acceptance
verwendet. `truncate: false` und `shift: false` verhindern die standardmaessige
Kontextkuerzung im gepinnten Quellvertrag. Eine konservative Groessenpruefung weist
zu grosse Payloads bereits lokal ab; sie ist kein exakter Tokenizer.

Metadata-Antworten sind auf 1 MiB begrenzt, Ausgabe auf 4 MiB, jede NDJSON-Zeile auf
64 KiB und der gesamte Stream einschliesslich verworfenen Denktexten auf 8 MiB.
Bis zum ersten vollstaendigen Chunk gelten 120 Sekunden, danach 60 Sekunden Leerlauf;
das kleinere Task-/Adapter-/Deadline-Budget bleibt verbindlich. Abbruch unterbricht
auch blockierte Header- und Body-Leseoperationen. Provider-Fehlertexte werden verworfen.

Nur ein vollstaendiger letzter Chunk mit `done: true` und `done_reason: stop` erlaubt
Erfolg. `length` wird als `acceptance_failed` dem Review zugefuehrt. Fehlender Abschluss,
Werkzeugaufrufe oder unerwartete Protokollfelder fuehren zu einem sicheren Fehler.
Denktext wird nicht persistiert. Timeout und Abbruch geben keine Teilausgabe zurueck.
Ein bestaetigter Owner-Abbruch nutzt den vorhandenen Abbruchpfad ohne Result-Zeile.
Ein `local_only`-Prompt mit Dateipfad bleibt gemaess bestehender Datenschranke Text;
der Adapter oeffnet daraus keine Datei. Eine pauschale Pfadsperre ist kein Teil dieses Vertrags.
`/api/pull` wird nie aufgerufen. Ein Modell-404 nach dem Claim bleibt
`worker_unavailable`; zentrale Retry-Regeln entscheiden ueber spaetere Versuche.

`results.execution_profile` stammt vom Executor. Nur `live` beeinflusst Live-Zaehler,
Fehlerserie und letzten erfolgreichen Versuch. Simulation, Remote-Ergebnisse ohne
Attestierung und historische unbekannte Ergebnisse sind kein Live-Nachweis.
Das eingefrorene `result.v1` traegt die Herkunft als separaten Check nach Acceptance.
Events und JSON-Zeilen im Lane-Log enthalten nur begrenzte technische Metadaten,
niemals Prompt, Antwort oder Provider-Diagnosen.

### Gepruefte Quellstellen und offene Server-Nachweise

Der Release-Tag wurde gegen den folgenden Commit geprueft. Am gepinnten Quellstand:

- `server/routes.go`, `ChatHandler`: vorhandenes Modell laden, bei Fehlen 404;
  der Pull-Handler ist getrennt. Lokale Quellen verweigern Remote-Aliase vor Inferenz.
- `internal/modelref/modelref.go`: terminaler `:local`-Suffix bindet die Modellquelle.
- `api/types.go`: Show-Antworten enthalten Faehigkeiten und Remote-Felder; Chat kennt
  `truncate`, `shift`, `thinking`, `done` und `done_reason`.
- `server/prompt.go`, `server/routes.go`, `server/sched.go`, `llm/llama_server.go`:
  keine Nachrichtenkuerzung bei `truncate: false`, Context Shift nur auf Anforderung;
  HTTP-Request-Kontexte werden an die Modellverarbeitung weitergegeben.
- Der untersuchte API-Medienpfad verarbeitet explizite Medienbytes. Ohne `images`
  ist kein aus Prompttext abgeleiteter Host-Dateizugriff ersichtlich.

Ein Verbindungsende beweist noch keine vollstaendige Freigabe daemonseitiger
Ressourcen: Callback-Kanaele koennen nach einem Schreibfehler blockieren. Abbruch,
Kaltstart, Modellqualität und Ressourcenfreigabe gehoeren deshalb in den Owner-Serverlauf.
`OLLAMA_NO_CLOUD=1`, Loopback-Bindung und vorab geladene Modelle muss der Owner am
Ollama-Dienst einrichten; der Client konfiguriert diesen Dienst nicht.

Quellenstand: offizielles Ollama `v0.40.14`, Commit
`9a3e8177767245acc62b735eba7d5d8a553606d0`:

- [CLI und Auto-Pull](https://github.com/ollama/ollama/blob/9a3e8177767245acc62b735eba7d5d8a553606d0/cmd/cmd.go)
- [Dateizugriffe im CLI](https://github.com/ollama/ollama/blob/9a3e8177767245acc62b735eba7d5d8a553606d0/cmd/interactive.go)
- [Daemon-Cloud-Policy](https://github.com/ollama/ollama/blob/9a3e8177767245acc62b735eba7d5d8a553606d0/internal/cloud/policy.go)
- [Client-Konfiguration](https://github.com/ollama/ollama/blob/9a3e8177767245acc62b735eba7d5d8a553606d0/api/client.go)

## Claude in Etappe 12

`ClaudeExecutor` unterstuetzt ausschliesslich das ausdrueckliche Profil `simulation`.
Der Standard `disabled` meldet ohne Prozessstart `unavailable` / `live_profile_unavailable`.
Die mitgelieferte Fake-Container-CLI startet nur die Fake-Claude-CLI. Sie simuliert einen
Lebenszyklus und ist keine Sandbox. Es gibt keinen Executable-Override oder Live-Schalter.

```bash
uv run python -m app.workers.runner_main --worker claude --simulate --once --executor claude-fake-cli
```

Modelle stammen aus `lanes.claude.models.<kind>` oder `.default`. Fehlende Modelle,
Pause, deaktivierte Lanes, Versionsabweichung oder fehlendes simuliertes Rootless verhindern
den Claim. `simulation_ready` belegt nur die Simulationskonfiguration. Auth-Fehler werden
als `auth_required`, Anbieter-Sperren als `paused` beobachtet. Lane-Pause, Backoff und
Fallback gehoeren der zentralen Fehlerlogik aus Etappe 10; der Adapter wiederholt nichts.

Nach SHA-256-Pruefung gehen exakt die archivierten Versandbytes auf stdin: kein Wrapper,
Host-Dateimount, Originalauftrag oder Redaction-Map. `local_only` bleibt durch die
lanegebundene Vorbereitung bei Ollama. Textausgaben haben `output.files=[]`; erzeugter
Code wird niemals ausgefuehrt. Geerbte Host-/Provider-/Proxy-Variablen werden verworfen.

### Versionierter Claude-Vertrag

Referenz ist das offizielle Claude Code **2.1.317**. `docker/lane-claude/VERSION` benennt
die SHA-256-Werte des Linux-amd64-Archivs und beider Distroless-Images. Die ungebaute
Dockerfile-Vorlage kopiert nur die CLI ins finale Image, ohne Shell oder Paketmanager,
mit UID/GID 10001. Das Lesen der veroeffentlichten Digests und des Archiv-Headers belegt
keine lauffaehige Installation oder Bibliotheks-Kompatibilitaet.

| Argument | Zweck |
|---|---|
| `-p --output-format json --max-turns 1` | Ein Auftrag mit finalem JSON-Ergebnis |
| `--tools "" --disallowedTools "*"` | Eingebaute Werkzeuge abschalten, Werkzeuge verweigern |
| `--strict-mcp-config --mcp-config '{"mcpServers":{}}'` | Keine fremde MCP-Konfiguration |
| `--restricted --safe-mode --setting-sources ""` | Benutzer-/Projekt-Anpassungen begrenzen |
| `--disable-slash-commands --permission-mode dontAsk` | Keine Skills oder Freigabedialoge |
| `--no-session-persistence` | Keine fortsetzbare Sitzung |
| `--model <konfiguriert>` | Modell aus der lokalen Konfiguration |

`--bare` wird nicht verwendet: Dieser Modus liest laut offizieller Dokumentation keine
OAuth-/Abo-Anmeldung und verlangt API-Key- oder andere Provider-Zugangsdaten. `--tools ""`
alleine deaktiviert keine MCP-Werkzeuge. Safe Mode kann verwaltete Vorgaben nicht beliebig
ueberschreiben; die Flags ersetzen keine Isolation.

Der Containervertrag erlaubt keine beliebigen Mounts, Programme, Umgebungen oder Netzmodi.
Er verwendet `--read-only`, entfernte Capabilities, `no-new-privileges`, UID/GID 10001 und
begrenzte CPU-/RAM-/Prozesszahlen. Nur `/work` und `/tmp` sind private begrenzte tmpfs mit
`noexec,nosuid,nodev`; ein eigens benanntes Profilvolume ist vorgesehen. Das Netz bleibt
**`none`**; dieser Vertrag kann keinen echten Anbieter erreichen. Explizite CLI-Variablen
deaktivieren Wiederholungen, Updates, Zusatzverkehr und Marketplace-Autoinstallation.

Jeder gestartete Versuch erhaelt einen separaten Aufraeumaufruf mit zwei Sekunden
Zusatzbudget, auch bei Timeout, Abbruch oder Client-Fehler. Dieser Aufruf ignoriert das
Task-Abbruchsignal. Misslingt Cleanup, wird kein Erfolg angenommen. Nur den Client zu
beenden wuerde einen daemonseitigen Container nicht sicher stoppen. Die echte Grenze
bleibt bei der Inbetriebnahme nachzuweisen.

### Claude-Ergebnisse und Fehler

Nur ein finales JSON-Objekt mit `type=result`, `subtype=success`, `is_error=false`, einem
Turn und `terminal_reason=completed` gilt als abgeschlossen. Werkzeug-/Berechtigungs-
befunde, doppelte JSON-Schluessel, ungueltige Tokenzahlen und unvollstaendige Antworten
werden verworfen. Exit null alleine belegt keinen Erfolg. Optionale Tokenzahlen werden
geprueft; Kosten und Anbieter-Kontingente werden nicht daraus geschaetzt.

| Signal | Fehlercode |
|---|---|
| Strukturiertes Fehlerobjekt, Status 401 | `auth_required` |
| Strukturiertes Fehlerobjekt, Status 403 | `policy_blocked` |
| Strukturiertes Fehlerobjekt, Status 429 | `rate_limited` |
| Bekannte Server-/Kapazitaetsfehler 500, 502, 503, 529 | `worker_unavailable` |
| Transport-Timeout | `timeout` |
| Leeres finales Ergebnis | `acceptance_failed` |
| Sonstige Prozess-/Protokoll-/Cleanup-Fehler | `internal` |

Unbekannte Fehler werden nicht aus Ziffern in stderr erraten. Fixtures sind synthetische
Protokollfaelle, keine aufgenommenen Anbieterantworten oder semantischen Referenzfaelle.
Provider-Fehlertexte, Prompt und Antwort erscheinen nicht im normalen Lauf-Log oder in
Fehlergruenden. Lokale Ergebnisartefakte behalten die bestehenden Owner-Zugriffsregeln.

### Inbetriebnahme: Claude

Das Live-Profil bleibt gesperrt. Separat nachzuweisen sind:

- Gepinntes Image bauen, Digests, `claude --version`, Bibliotheken und Flags pruefen.
- Rootless-Daemon, UID, Dateisystem, Ressourcen, Werkzeug-/MCP-/Hook-/Plugin-Abschaltung
  sowie Cleanup bei Client-Absturz und Daemon-Neustart pruefen.
- Technisch begrenzten Anbieter-Netzweg ohne Host-/DB-Zugriff festlegen. Standard-Bridge
  und ein Konfigurations-Boolean belegen keine solche Grenze.
- Der Owner richtet die Abo-Anmeldung mit `claude auth login` im separaten CLI-Profil
  ein; kein API-Key, Hostprofil-Mount oder Zugangswert in Repo, `.env`, Logs oder PR.
  Kontomodell und deaktivierte Zusatzabrechnung pruefen.
- Abgelaufene Anmeldung, Anbieter-Sperre, Rate-Limit, keine versteckten Wiederholungen
  und echte JSON-Abschlussmerkmale pruefen. Inhaltliche Faelle liefert der Owner.

Nichts davon wurde ausgefuehrt. Es gibt keinen automatischen Login oder Live-Schalter.
Quellenstand 2026-11-03:

- [Offizieller Release 2.1.317](https://github.com/anthropics/claude-code/releases/tag/v2.1.317)
- [CLI-Referenz](https://code.claude.com/docs/en/cli-reference)
- [Headless und Bare Mode](https://code.claude.com/docs/en/headless)
- [Variablen und Wiederholungen](https://code.claude.com/docs/en/env-vars)
- [Offizielle Ergebnistypen](https://github.com/anthropics/claude-agent-sdk-python/blob/main/src/claude_agent_sdk/types.py)

## Codex in Etappe 13

`CodexExecutor` verwendet einen versionierten Textvertrag fuer die offizielle CLI
**0.166.0**. Ausgefuehrt wird ausschliesslich die mitgelieferte Python-Fake-CLI durch den
Reinraum-Simulator. Es gibt keine Auswahl eines echten Executables und keinen Schalter,
der das Live-Profil aktiviert. Das Standardprofil bleibt
`unavailable` / `live_profile_unavailable` ohne Prozessstart.

```bash
uv run python -m app.workers.runner_main --worker codex --simulate --once --executor codex-fake-cli
```

Modelle stammen aus `lanes.codex.models`. Eine falsche Worker-Lane wird vor dem DB-Zugriff
abgewiesen. Die Queue und der Host-Rahmen geben `local_only` niemals an Codex weiter.
Der Simulator verwendet ein eigenes Codex-Profil; es werden keine Host-Umgebungsvariablen,
DB-Verbindungen oder CLI-Anmeldedaten vererbt. Der beschriebene Container-Befehl hat ein
schreibgeschuetztes Dateisystem, einen Benutzer ohne Root-Rechte, begrenzte Ressourcen und
`network=none`. Er wird nicht mit Docker ausgefuehrt. Die separat begrenzte Bereinigung
des generierten Container-Namens bleibt auch bei Timeout und Abbruch aktiv.

Der Aufrufvertrag nutzt `codex exec --strict-config --json --ephemeral --ignore-user-config
--ignore-rules --skip-git-repo-check --sandbox read-only --color never --model <modell> -`.
Das letzte `-` liest genau die archivierten Payload-Bytes von stdin; kein weiterer Prompt
wird hinzugefuegt. Ausdrueckliche Konfiguration verlangt Subscription-Anmeldung und
schaltet die dokumentierten Shell-, Code-, Plugin-, MCP-, Web- und Mehragenten-Funktionen
im Kandidatenprofil ab. Diese Konfiguration ist kein vollstaendiger Tool-Sperrnachweis.

Der Parser verlangt einen geordneten JSONL-Strom mit Thread-Start, Turn-Start, abgeschlossener
Agenten-Nachricht und genau einem erfolgreichen Turn-Abschluss. Tokenzaehler sind begrenzte,
nichtnegative Ganzzahlen. Doppelte JSON-Schluessel, unvollstaendige oder widerspruechliche
Ereignisse, Tool-Aufrufe und ungueltiges UTF-8 werden abgewiesen. Ein Exit-Code null ersetzt
keinen Abschluss. Reasoning, Teilantworten und rohe Fehlermeldungen werden nicht gespeichert.
Nur die letzte vollstaendige Textnachricht wird zur lokalen Rueckabbildung und zentralen
Acceptance weitergegeben. `output.files` bleibt leer; auch Code-Auftraege liefern nur Text.
Es gibt weder `workspace-write` noch eine Ausfuehrung erzeugten Codes.

Synthetische, versionierte Fehlerfixtures pruefen `auth_required`, `policy_blocked`,
`rate_limited`, `worker_unavailable` und unbekannte Fehler (`internal`). Die Zuordnung ist
eng und verwendet keine Teilstrings aus einem beliebigen Modelltext. Diese Fixtures sind
technische Tests, keine aufgezeichneten echten Providerantworten und keine inhaltlichen
Referenzfaelle. Backoff, Lane-Pause und Fallback entscheidet ausschliesslich Etappe 10;
der Fake-Adapter startet pro Versuch genau einen CLI-Lauf.

### Codex: Inbetriebnahme

Das Build-Rezept unter `docker/lane-codex/` pinnt Linux/amd64, die offizielle CLI-Archivversion,
deren SHA-256 sowie die Basis-Images. Archiv-Metadaten und der Pfad `bin/codex` wurden lesend
geprueft; ein Image-Build, echter CLI-Aufruf oder Login wurde nicht ausgefuehrt.
Vor Aktivierung sind unter anderem diese konkreten Grenzen nachzuweisen:

- **Tool-Deaktivierung:** Im gepinnten Quellstand entfernt `shell_tool=false` die Shell-Werkzeuge,
  aber `ApplyPatch` kann weiterhin anhand des Modellkatalogs registriert werden. Read-only,
  verweigerte Rueckfragen und das nachtraegliche Ablehnen von Tool-Ereignissen beweisen keine
  vorbeugende Tool-Sperre.
- **Interne Retries:** Der eingebaute OpenAI-Provider uebernimmt aus
  `model_providers.openai` nur die Katalog-Konfiguration. Dort gesetzte Request-/Stream-Retry-
  Werte werden ignoriert. Solche wirkungslosen Schalter werden deshalb nicht mitgegeben.
  Eine Subscription-Anbindung ohne versteckte Retries ist noch nicht nachgewiesen.
- **Isolation:** Rootless Betrieb, Profiltrennung, begrenzte Provider-Netzverbindung ohne
  Host-/Repo-/DB-Zugang, Dateisystemgrenzen und zuverlaessige Container-Bereinigung brauchen
  eigene Live-Nachweise. Der geschlossene Simulationsvertrag hat bewusst keinen Providerzugang.
- **Anmeldung:** Ein spaeterer Owner-Login mit `codex login --device-auth` gehoert ausschliesslich
  ins isolierte Profil. `login status` mit Exit-Code null allein unterscheidet nicht zwischen
  Subscription- und API-Key-Anmeldung; der verwendete Modus muss zusaetzlich geprueft werden.

`simulation_ready` ist auch hier ausschliesslich ein Simulationsnachweis. Echte
Anbieter-Kontingente, semantische Modellqualitaet und Produktionsbereitschaft bleiben offen.

Quellenstand: [offizielle Version 0.166.0](https://github.com/openai/codex/releases/tag/rust-v0.166.0),
Commit `6ff1e1ca3b30fc8b914720681fcf5fe62e95f856`:

- [JSONL-Ereignisvertrag](https://github.com/openai/codex/blob/6ff1e1ca3b30fc8b914720681fcf5fe62e95f856/codex-rs/exec/src/exec_events.rs)
- [Tool-Registrierung](https://github.com/openai/codex/blob/6ff1e1ca3b30fc8b914720681fcf5fe62e95f856/codex-rs/core/src/tools/spec_plan.rs)
- [Provider-Konfiguration und eingebaute Overrides](https://github.com/openai/codex/blob/6ff1e1ca3b30fc8b914720681fcf5fe62e95f856/codex-rs/model-provider-info/src/lib.rs)
