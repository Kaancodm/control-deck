# Lanes, Simulation und lokaler Live-Adapter

## Ollama in Etappe 9

Der Owner hat `ollama run` mit Fake-CLI-Tests gewählt. Der Adapter in
`app/workers/lanes/ollama.py` unterstützt deshalb ein ausdrückliches Profil `simulation`.
Es startet nur die mitgelieferte Fake-CLI mit dem aktuellen Python-Interpreter. Es gibt keine
Option für ein beliebiges Executable, keinen echten Modellaufruf und keinen Download.
Das Standardprofil `disabled` bleibt ohne Prozessstart `unavailable` mit dem Grund
`live_profile_unavailable`.

Die Modellkennung stammt aus `lanes.ollama.models.<kind>` oder `.default` in der lokalen
Routing-Konfiguration. Im Code stehen keine echten Modellnamen. Eine Lane ganz ohne
Modelle, ein konfiguriertes, aber im Inventar fehlendes Modell, eine deaktivierte Lane oder
Pause verhindern den Claim und verbrauchen keinen Versuch; die Aufgabe bleibt `queued` und
`lane_status` nennt den Grund. Die Inventarprüfung läuft vor jedem Poll ausserhalb einer
DB-Transaktion. Der Claim prüft die aktuelle Modellwahl erneut gegen die zuvor geprüften
Modelle.

Fehlt dagegen für den `kind` einer Aufgabe ein Eintrag und gibt es kein `default`, ist das
eine dauerhafte Fehlkonfiguration, die kein späterer Poll beheben kann. Die Queue setzt die
Aufgabe ohne Versuch auf `blocked` und schreibt das Ereignis `task.block` mit Grund
`routing`, Code `model_not_configured`, Lane und `kind`. Im selben Commit wird
`lane_status` auf `unavailable` / `model_not_configured` gesetzt; andere Aufgaben mit
konfiguriertem Modell werden im selben Poll weiter bearbeitet. Der nächste Poll ohne
betroffene Aufgabe meldet wieder `ok`. Diese Regel gilt nur für Lanes mit vorheriger
Inventarprüfung; Claims ohne Inventar verhalten sich wie bisher.

Der Host-Runner gibt genau die UTF-8-Bytes aus `payload.sent.md` auf stdin an
`run <modell>` weiter. Es wird kein zusätzlicher Prompt davor- oder dahintergesetzt.
Die SHA-256-Prüfung, lokale Rückabbildung, Acceptance und Ergebnis-Fencing bleiben im
Rahmen aus Etappe 8. Die CLI erhält weder den Originalauftrag noch die Redaction-Map.

```bash
uv run python -m app.workers.runner_main --worker ollama --simulate --once --executor ollama-fake-cli
```

Dieser Befehl verwendet die konfigurierte lokale Testdatenbank und eine vorhandene
Routing-Konfiguration. Er ist keine Live-Aktivierung. Der vorhandene reine Fake-Executor
bleibt mit `--executor simulation` verfügbar.

## Prozessgrenzen und Fehler

`app/workers/process.py` verwendet Argumentlisten ohne Shell, ein separates Prozessgruppen-
Signalziel und eine ausdrückliche Umgebungs-Allowlist. Geerbte Provider-Variablen,
`OLLAMA_HOST`, Proxy-Werte und das Benutzer-HOME werden nicht weitergegeben.
stdout und stderr werden gleichzeitig gelesen und getrennt begrenzt; Standardgrenzen
sind 4 MiB und 64 KiB. Timeout und Abbruch beenden und ernten die Prozessgruppe.
Die Dauer ist das Minimum aus `policy.timeout_s` (Standard 900 Sekunden, Grenze 30 bis
7200) und der verbleibenden Owner-Deadline. Die Adapter-Obergrenze ist dieselbe
Schema-Grenze 7200 und unterschreitet den Standard 900 nicht mehr. Ein explizit
kleineres Adapter-Limit gilt nur, wenn der Aufruf es setzt, zum Beispiel in einem
kurzen Test. Der Dienstsupervisor wartet standardmässig bis zu derselben Grenze
von 7200 Sekunden. Er bricht einen erlaubten Versuch nicht mehr still nach 3600
Sekunden ab. Der Versuch selbst endet bei `policy.timeout_s`. Ein von aussen
beendeter Runner, der kein Ergebnis schreibt, bekommt vom Lease-Reaper genau
ein `worker_unavailable`. Das ist kein `timeout` und keine zweite Warteschlange.
Die Erholung bleibt auf derselben Lane, innerhalb von `max_attempts`. Das ist
die Lease-Regel aus Etappe 10, nicht ein unbegrenzter gleicher Versuch. Ein
`worker_unavailable` oder `timeout`, den der laufende Runner selbst speichert,
geht weiter zur naechsten zulaessigen Lane. Der Reaper ruft diese Fallback-Regel
nicht zusaetzlich auf. Jedes neu geschriebene Verloren-Ergebnis zaehlt genau
einmal als unbekannter Lane-Versuch. Eine verspaetete Antwort der alten Lease
wird abgewiesen. Der Ollama-Transport bleibt
davon getrennt: 120 Sekunden bis zum ersten
Chunk, danach 60 Sekunden Leerlauf. Das ist keine Ergebnisfrist und keine
Kanal-Quittung. Pro Versuch gibt es höchstens einen `run`-Aufruf und keinen
versteckten Retry. Inventarprüfungen verwenden ein eigenes begrenztes `list`.

| Ergebnis | `result.v1` |
|---|---|
| Erfolgreiche nichtleere UTF-8-Ausgabe | `succeeded`, anschliessend zentrale Acceptance |
| Fehlender Prozess, fehlendes Modell, Exit-Code ungleich null | `failed`, `worker_unavailable` |
| Transport-Timeout | `failed`, `timeout` |
| Kooperativer Abbruch | `cancelled` |
| Leere Ausgabe | `failed`, `acceptance_failed` |
| Ausgabegrenze, ungültiges UTF-8, interner Prozessfehler | `failed`, `internal` |

Bei Timeout, Abbruch und Fehlern werden Teilantworten nicht als Ergebnis weitergegeben.
Das unveränderliche Lane-Log schreibt der Host-Runner; es enthält sichere Laufmetadaten,
keinen Prompt, Antworttext oder stderr. Die eingefrorenen v1-Schemas bleiben unverändert.
Backoff und Fallback folgen in Etappe 10.

## Persistierte Gesundheit

Migration `0010` ergänzt `lane_status` mit Lane, Status, sicherem Grund, `updated_at` und
`last_success_at`. Erlaubte Status sind `ok`, `unavailable`, `auth_required`, `paused`.
Zeitstempel kommen aus der DB-Uhr; Fehler erhalten die letzte positive Beobachtung.
Die Aktualisierung erfolgt in kurzen eigenen Transaktionen.

`ok` mit Grund **`simulation_ready`** bezeichnet ausschliesslich eine erfolgreiche
Fake-Inventarprüfung. Es bestätigt weder einen erfolgreichen echten Modelllauf noch eine
verfügbare Live-Lane. `last_success_at` ist der Zeitpunkt dieser Gesundheitsbeobachtung.
Das deaktivierte Profil meldet **`unavailable` / `live_profile_unavailable`**.
Der neue Live-Vertrag folgt unten; `live_ready` ist eine positive Vorprüfung,
kein Nachweis für Modellqualität. Die persistierte Anzeige beschreibt die letzte
Vorprüfung samt Zeitpunkt; ein gestoppter Runner aktualisiert sie nicht mehr.

## Inbetriebnahme

Der CLI-Live-Pfad aus Etappe 9 bleibt gesperrt. ADR 0009 erlaubt die nachfolgend
beschriebene HTTP-Anbindung. Die offizielle CLI bietet keinen
No-Pull-Schalter: `run` kann ein fehlendes Modell automatisch laden. Eine vorgängige
Inventarprüfung schliesst das Rennen bis zum Aufruf nicht. Ein `:local`-Suffix begrenzt die
Modellquelle, verhindert aber den Download nicht. `OLLAMA_NO_CLOUD` muss im Daemon gelten;
eine Variable ausschliesslich im CLI-Kindprozess konfiguriert einen laufenden Daemon nicht.
Multimodale CLI-Pfade können zudem im Prompt genannte Bild-/Audiodateien vom Host lesen.
Ein Exit-Code null allein beweist für alle CLI-Pfade keinen vollständigen Modellabschluss.

Der Live-Adapter verwendet deshalb ausschliesslich die lokale HTTP-API. Echte Logins,
Serveränderungen, Modellinstallationen und Modelltests wurden nicht ausgeführt.
Die semantische Qualität prüft erst das vom Owner erstellte Referenzset bei der
Inbetriebnahme. Die folgenden Nachweise betreffen Quellcode und synthetische Tests.

### HTTP-Vertrag gemäss ADR 0009

Die Aktivierung verlangt die Runner-Variablen `DECK_LIVE_ENABLED` (exakt `1` oder
`true`) und `DECK_OLLAMA_URL`. Nur HTTP, Loopback-Literale und ein expliziter Port sind
erlaubt. Ohne Freigabe bleibt der Daemon mit Exit 78 gestoppt. Laufzeitfehler eines
Ollama-Dienstes verhindern Claims, halten aber die Supervisor-Schleife am Leben.

Die Vorprüfung kontrolliert die exakte Version **0.40.14**, alle konfigurierten
Modellkennungen gegen `/api/tags` und ihre Fähigkeiten mittels `/api/show`.
Explizite Tags sind Pflicht; Cloud-Kennungen, Remote-Felder und multimodale
Fähigkeiten werden abgewiesen. Der HTTP-Modellname trägt zusätzlich `:local`.
Das Inventar und die Provenienz behalten die konfigurierte Kennung ohne Quellensuffix.
Damit wird auch ein zwischenzeitlich auf Remote geänderter Alias vor Inferenz abgewiesen.

Ein Versuch sendet eine User-Nachricht mit genau dem archivierten Text. JSON-Escaping
verändert die logischen Nachrichtenbytes nicht; `payload_hash` bezeichnet den
UTF-8-Inhalt, nicht den gesamten JSON-Request. Es gibt keine `images` oder `tools`.
`stream: true`, Temperatur null, kurze Modellhaltezeit und ein ausdrückliches
Kontextbudget gelten pro Request. `format: json` wird nur für JSON-Schema-Acceptance
verwendet. `truncate: false` und `shift: false` verhindern die standardmässige
Kontextkürzung im gepinnten Quellvertrag.

Das effektive `num_ctx` stammt zuerst aus `lanes.ollama.num_ctx`, danach aus
`/api/show.model_info` (architekturspezifisches `context_length`, sonst ein eindeutiges
Kontextfeld), sonst aus dem Fallback 4096. Explizite Lane-Werte müssen positive Integer
bis zum 32-Bit-Maximum sein; ungültige Konfiguration wird abgewiesen. Fehlende, ungültige
oder mehrdeutige Metadaten erhöhen den Fallback nicht. Prüfung und Chat verwenden denselben
Wert. Die Schätzung lautet `ceil(UTF-8-Bytes / 3) + 1024` für Vorlage und Ausgabe.
Gleichheit mit `num_ctx` ist erlaubt; ein Token darüber ergibt vor dem Chat-Request
`context_limit_exceeded`. Serverfehler bleiben Fehler ohne Teilausgabe oder Adapter-Retry.
Einzige Ausnahme ist HTTP 503 bei voller Ollama-Warteschlange (`OLLAMA_MAX_QUEUE`): Ollama
weist den Request vor der Inferenz ab. Der Client sendet ihn innerhalb desselben Zeitbudgets
höchstens viermal, mit 0,5, 1 und 2 Sekunden Abstand oder gemäss `Retry-After`, gedeckelt
auf 4 Sekunden. Der `attempt` zählt dabei nicht weiter. Bleibt der Dienst ausgelastet,
meldet der Versuch `rate_limited`; die Queue plant ihn nach den Regeln aus Etappe 10 neu ein.

Die Verfügbarkeit wird pro konfiguriertem Modell geprüft. Ein fehlendes oder ungeeignetes
Modell sperrt keine anderen einzeln bestätigten Modelle. Die Queue überspringt Aufgaben
für fehlende Modelle ohne Claim. Serverversion und Inventarformat bleiben globale Gates.
Live-Bereitschaft verfällt nach 30 Sekunden ab Beginn der aktuellen Probe. Ältere
Probe-Generationen dürfen neuere Beobachtungen nicht überschreiben.


Metadata-Antworten sind auf 1 MiB begrenzt, Ausgabe auf 4 MiB, jede NDJSON-Zeile auf
64 KiB und der gesamte Stream einschliesslich verworfenen Denktexten auf 8 MiB.
Bis zum ersten vollständigen Chunk gelten 120 Sekunden, danach 60 Sekunden Leerlauf;
das kleinere Task-/Adapter-/Deadline-Budget bleibt verbindlich. Abbruch unterbricht
auch blockierte Header- und Body-Leseoperationen. Provider-Fehlertexte werden verworfen.

Nur ein vollständiger letzter Chunk mit `done: true` und `done_reason: stop` erlaubt
Erfolg. `length` wird als `acceptance_failed` dem Review zugeführt. Fehlender Abschluss,
Werkzeugaufrufe oder unerwartete Protokollfelder führen zu einem sicheren Fehler.
Denktext wird nicht persistiert. Timeout und Abbruch geben keine Teilausgabe zurück.
Ein bestätigter Owner-Abbruch nutzt den vorhandenen Abbruchpfad ohne Result-Zeile.
Ein `local_only`-Prompt mit Dateipfad bleibt gemäss bestehender Datenschranke Text;
der Adapter öffnet daraus keine Datei. Eine pauschale Pfadsperre ist kein Teil dieses Vertrags.
`/api/pull` wird nie aufgerufen. Ein Modell-404 nach dem Claim bleibt
`worker_unavailable`; zentrale Retry-Regeln entscheiden über spätere Versuche.

`results.execution_profile` stammt vom Executor. Nur `live` beeinflusst Live-Zähler
und den letzten erfolgreichen Live-Versuch. Angenommene Fehler jeder Herkunft
erhöhen die Fehlerserie und aktualisieren Fehlercode und Fehlerzeitpunkt. Nur ein
Live-Erfolg setzt die Serie zurück; Simulationserfolg und Abbruch tun dies nicht.
Simulation, Remote-Ergebnisse ohne Attestierung und historische unbekannte
Ergebnisse sind kein Live-Nachweis. `simulated_attempts`, `remote_attempts` und
`unknown_attempts` trennen diese Herkunftsarten ausdrücklich.
Das eingefrorene `result.v1` trägt die Herkunft als separaten Check nach Acceptance.
Events und JSON-Zeilen im Lane-Log enthalten nur begrenzte technische Metadaten,
niemals Prompt, Antwort oder Provider-Diagnosen.

### Geprüfte Quellstellen und offene Server-Nachweise

Der Release-Tag wurde gegen den folgenden Commit geprüft. Am gepinnten Quellstand:

- `server/routes.go`, `ChatHandler`: vorhandenes Modell laden, bei Fehlen 404;
  der Pull-Handler ist getrennt. Lokale Quellen verweigern Remote-Aliase vor Inferenz.
- `internal/modelref/modelref.go`: terminaler `:local`-Suffix bindet die Modellquelle.
- `api/types.go`: Show-Antworten enthalten Fähigkeiten und Remote-Felder; Chat kennt
  `truncate`, `shift`, `thinking`, `done` und `done_reason`.
- `server/prompt.go`, `server/routes.go`, `server/sched.go`, `llm/llama_server.go`:
  keine Nachrichtenkürzung bei `truncate: false`, Context Shift nur auf Anforderung;
  HTTP-Request-Kontexte werden an die Modellverarbeitung weitergegeben.
- Der untersuchte API-Medienpfad verarbeitet explizite Medienbytes. Ohne `images`
  ist kein aus Prompttext abgeleiteter Host-Dateizugriff ersichtlich.

Ein Verbindungsende beweist noch keine vollständige Freigabe daemonseitiger
Ressourcen: Callback-Kanäle können nach einem Schreibfehler blockieren. Abbruch,
Kaltstart, Modellqualität und Ressourcenfreigabe gehören deshalb in den Owner-Serverlauf.
`OLLAMA_NO_CLOUD=1`, Loopback-Bindung und vorab geladene Modelle muss der Owner am
Ollama-Dienst einrichten; der Client konfiguriert diesen Dienst nicht.

Quellenstand: offizielles Ollama `v0.40.14`, Commit
`9a3e8177767245acc62b735eba7d5d8a553606d0`. Der Tag wurde am 2026-11-04 mit
`git ls-remote --tags` gegen das offizielle Repository aufgelöst und zeigt auf genau
diesen Commit:

- [CLI und Auto-Pull](https://github.com/ollama/ollama/blob/9a3e8177767245acc62b735eba7d5d8a553606d0/cmd/cmd.go)
- [Dateizugriffe im CLI](https://github.com/ollama/ollama/blob/9a3e8177767245acc62b735eba7d5d8a553606d0/cmd/interactive.go)
- [Daemon-Cloud-Policy](https://github.com/ollama/ollama/blob/9a3e8177767245acc62b735eba7d5d8a553606d0/internal/cloud/policy.go)
- [Client-Konfiguration](https://github.com/ollama/ollama/blob/9a3e8177767245acc62b735eba7d5d8a553606d0/api/client.go)

### Embedding-Vertrag `/api/embed`

Die Wissens-DB nutzt `OllamaHTTPClient.embed` im API-Prozess, nicht den Runner (ADR 0011).
Der Aufruf erbt Loopback-only, keinen Proxy, keine Weiterleitung, `Connection: close`, die
503-Wiederholung innerhalb desselben Budgets und den Abbruch aus dem HTTP-Vertrag oben.

Request, nicht streamend: `POST /api/embed` mit `model` als konfigurierte Kennung plus `:local`,
`input` als Liste nicht leerer Strings (hoechstens 16), `truncate: false` und optional
`keep_alive`. Ein `dimensions`-Feld wird nie gesendet; die native Breite ist der Vertrag.
Antworten sind auf 2 MiB begrenzt und werden mit dem strikten JSON-Leser geparst (doppelte
Schluessel und nicht endliche Konstanten scheitern). Erwartet werden `model` (konfigurierte
Kennung mit oder ohne `:local`) und `embeddings` mit genau einem Vektor je Eingabe; `error`,
`remote_host` oder `remote_model` fuehren zu `embedding_malformed`. Es gibt keinen Pull,
keinen Warmup und keinen periodischen Probe-Aufruf.

Quellenstand `v0.40.14`, Commit `9a3e8177767245acc62b735eba7d5d8a553606d0`, gelesen am
2026-11-10 ueber die oeffentlichen Rohdateien
[api/types.go](https://github.com/ollama/ollama/blob/9a3e8177767245acc62b735eba7d5d8a553606d0/api/types.go)
und [server/routes.go](https://github.com/ollama/ollama/blob/9a3e8177767245acc62b735eba7d5d8a553606d0/server/routes.go);
Methode: Quelltextlektuere ohne Ausfuehrung. Status: Feldnamen `proven` am Quelltext, Verhalten
am Server `unproven`.

- `EmbedRequest` kennt `model`, `runner`, `input`, `keep_alive`, `truncate` (Zeiger auf bool),
  `dimensions` und `options`. Ohne Feld oder mit `true` wird gekuerzt; nur `false` ist strikt.
- `EmbedResponse` kennt `model`, `embeddings` (`float32`), `total_duration`, `load_duration` und
  `prompt_eval_count`, aber **keine** Remote-Felder. Die Remote-Pruefung im Adapter schuetzt nur
  gegen eine kuenftige Formataenderung.
- Mit `truncate: false` ergibt eine zu lange Eingabe HTTP 400 (im Deck `embedding_unavailable`);
  der Handler laedt das Modell vorher trotzdem.
- Ein fehlendes Modell ergibt 404 (`embedding_model_missing`).
- Vektoren werden serverseitig L2-normalisiert; NaN oder Inf ergeben 400.
- `EmbedHandler` leitet nur Modelle mit Cloud-Quelle weiter; eine eigene Pruefung auf `:local`
  oder auf lokale Aliase mit Remote-Ziel ist im Handler nicht ersichtlich. Ob die gemeinsame
  Namensaufloesung den Suffix `:local` hier annimmt, ist **nicht belegt** und gehoert in den
  Preflight des Owner-Rollouts. `OLLAMA_NO_CLOUD=1` am Dienst bleibt Voraussetzung.

## Claude in Etappe 12

`ClaudeExecutor` unterstützt ausschliesslich das ausdrückliche Profil `simulation`.
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
Fallback gehören der zentralen Fehlerlogik aus Etappe 10; der Adapter wiederholt nichts.

Nach SHA-256-Prüfung gehen exakt die archivierten Versandbytes auf stdin: kein Wrapper,
Host-Dateimount, Originalauftrag oder Redaction-Map. `local_only` bleibt durch die
lanegebundene Vorbereitung bei Ollama. Textausgaben haben `output.files=[]`; erzeugter
Code wird niemals ausgeführt. Geerbte Host-/Provider-/Proxy-Variablen werden verworfen.

### Versionierter Claude-Vertrag

Referenz ist das offizielle Claude Code **2.1.317**. `docker/lane-claude/VERSION` benennt
die SHA-256-Werte des Linux-amd64-Archivs und beider Distroless-Images. Die ungebaute
Dockerfile-Vorlage kopiert nur die CLI ins finale Image, ohne Shell oder Paketmanager,
mit UID/GID 10001. Das Lesen der veröffentlichten Digests und des Archiv-Headers belegt
keine lauffähige Installation oder Bibliotheks-Kompatibilität.

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
überschreiben; die Flags ersetzen keine Isolation.

Der Containervertrag erlaubt keine beliebigen Mounts, Programme, Umgebungen oder Netzmodi.
Er verwendet `--read-only`, entfernte Capabilities, `no-new-privileges`, UID/GID 10001 und
begrenzte CPU-/RAM-/Prozesszahlen. Nur `/work` und `/tmp` sind private begrenzte tmpfs mit
`noexec,nosuid,nodev`; ein eigens benanntes Profilvolume ist vorgesehen. Das Netz bleibt
**`none`**; dieser Vertrag kann keinen echten Anbieter erreichen. Explizite CLI-Variablen
deaktivieren Wiederholungen, Updates, Zusatzverkehr und Marketplace-Autoinstallation.

Jeder gestartete Versuch erhält einen separaten Aufräumaufruf mit zwei Sekunden
Zusatzbudget, auch bei Timeout, Abbruch oder Client-Fehler. Dieser Aufruf ignoriert das
Task-Abbruchsignal. Misslingt Cleanup, wird kein Erfolg angenommen. Nur den Client zu
beenden würde einen daemonseitigen Container nicht sicher stoppen. Die echte Grenze
bleibt bei der Inbetriebnahme nachzuweisen.

### Claude-Ergebnisse und Fehler

Nur ein finales JSON-Objekt mit `type=result`, `subtype=success`, `is_error=false`, einem
Turn und `terminal_reason=completed` gilt als abgeschlossen. Werkzeug-/Berechtigungs-
befunde, doppelte JSON-Schlüssel, ungültige Tokenzahlen und unvollständige Antworten
werden verworfen. Exit null alleine belegt keinen Erfolg. Optionale Tokenzahlen werden
geprüft; Kosten und Anbieter-Kontingente werden nicht daraus geschätzt.

| Signal | Fehlercode |
|---|---|
| Strukturiertes Fehlerobjekt, Status 401 | `auth_required` |
| Strukturiertes Fehlerobjekt, Status 403 | `policy_blocked` |
| Strukturiertes Fehlerobjekt, Status 429 | `rate_limited` |
| Bekannte Server-/Kapazitätsfehler 500, 502, 503, 529 | `worker_unavailable` |
| Transport-Timeout | `timeout` |
| Leeres finales Ergebnis | `acceptance_failed` |
| Sonstige Prozess-/Protokoll-/Cleanup-Fehler | `internal` |

Unbekannte Fehler werden nicht aus Ziffern in stderr erraten. Fixtures sind synthetische
Protokollfälle, keine aufgenommenen Anbieterantworten oder semantischen Referenzfälle.
Provider-Fehlertexte, Prompt und Antwort erscheinen nicht im normalen Lauf-Log oder in
Fehlergründen. Lokale Ergebnisartefakte behalten die bestehenden Owner-Zugriffsregeln.

### Inbetriebnahme: Claude

Das Live-Profil bleibt gesperrt. Separat nachzuweisen sind:

- Gepinntes Image bauen, Digests, `claude --version`, Bibliotheken und Flags prüfen.
- Rootless-Daemon, UID, Dateisystem, Ressourcen, Werkzeug-/MCP-/Hook-/Plugin-Abschaltung
  sowie Cleanup bei Client-Absturz und Daemon-Neustart prüfen.
- Technisch begrenzten Anbieter-Netzweg ohne Host-/DB-Zugriff festlegen. Standard-Bridge
  und ein Konfigurations-Boolean belegen keine solche Grenze.
- Der Owner richtet die Abo-Anmeldung mit `claude auth login` im separaten CLI-Profil
  ein; kein API-Key, Hostprofil-Mount oder Zugangswert in Repo, `.env`, Logs oder PR.
  Kontomodell und deaktivierte Zusatzabrechnung prüfen.
- Abgelaufene Anmeldung, Anbieter-Sperre, Rate-Limit, keine versteckten Wiederholungen
  und echte JSON-Abschlussmerkmale prüfen. Inhaltliche Fälle liefert der Owner.

Nichts davon wurde ausgeführt. Es gibt keinen automatischen Login oder Live-Schalter.
Quellenstand 2026-11-03:

- [Offizieller Release 2.1.317](https://github.com/anthropics/claude-code/releases/tag/v2.1.317)
- [CLI-Referenz](https://code.claude.com/docs/en/cli-reference)
- [Headless und Bare Mode](https://code.claude.com/docs/en/headless)
- [Variablen und Wiederholungen](https://code.claude.com/docs/en/env-vars)
- [Offizielle Ergebnistypen](https://github.com/anthropics/claude-agent-sdk-python/blob/main/src/claude_agent_sdk/types.py)

## Codex in Etappe 13

`CodexExecutor` verwendet einen versionierten Textvertrag für die offizielle CLI
**0.166.0**. Ausgeführt wird ausschliesslich die mitgelieferte Python-Fake-CLI durch den
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
schreibgeschütztes Dateisystem, einen Benutzer ohne Root-Rechte, begrenzte Ressourcen und
`network=none`. Er wird nicht mit Docker ausgeführt. Die separat begrenzte Bereinigung
des generierten Container-Namens bleibt auch bei Timeout und Abbruch aktiv.

Der Aufrufvertrag nutzt `codex exec --strict-config --json --ephemeral --ignore-user-config
--ignore-rules --skip-git-repo-check --sandbox read-only --color never --model <modell> -`.
Das letzte `-` liest genau die archivierten Payload-Bytes von stdin; kein weiterer Prompt
wird hinzugefügt. Ausdrückliche Konfiguration verlangt Subscription-Anmeldung und
schaltet die dokumentierten Shell-, Code-, Plugin-, MCP-, Web- und Mehragenten-Funktionen
im Kandidatenprofil ab. Diese Konfiguration ist kein vollständiger Tool-Sperrnachweis.

Der Parser verlangt einen geordneten JSONL-Strom mit Thread-Start, Turn-Start, abgeschlossener
Agenten-Nachricht und genau einem erfolgreichen Turn-Abschluss. Tokenzähler sind begrenzte,
nichtnegative Ganzzahlen. Doppelte JSON-Schlüssel, unvollständige oder widersprüchliche
Ereignisse, Tool-Aufrufe und ungültiges UTF-8 werden abgewiesen. Ein Exit-Code null ersetzt
keinen Abschluss. Reasoning, Teilantworten und rohe Fehlermeldungen werden nicht gespeichert.
Nur die letzte vollständige Textnachricht wird zur lokalen Rückabbildung und zentralen
Acceptance weitergegeben. `output.files` bleibt leer; auch Code-Aufträge liefern nur Text.
Es gibt weder `workspace-write` noch eine Ausführung erzeugten Codes.

Synthetische, versionierte Fehlerfixtures prüfen `auth_required`, `policy_blocked`,
`rate_limited`, `worker_unavailable` und unbekannte Fehler (`internal`). Die Zuordnung ist
eng und verwendet keine Teilstrings aus einem beliebigen Modelltext. Diese Fixtures sind
technische Tests, keine aufgezeichneten echten Providerantworten und keine inhaltlichen
Referenzfälle. Backoff, Lane-Pause und Fallback entscheidet ausschliesslich Etappe 10;
der Fake-Adapter startet pro Versuch genau einen CLI-Lauf.

### Codex: Inbetriebnahme

Das Build-Rezept unter `docker/lane-codex/` pinnt Linux/amd64, die offizielle CLI-Archivversion,
deren SHA-256 sowie die Basis-Images. Archiv-Metadaten und der Pfad `bin/codex` wurden lesend
geprüft; ein Image-Build, echter CLI-Aufruf oder Login wurde nicht ausgeführt.
Vor Aktivierung sind unter anderem diese konkreten Grenzen nachzuweisen:

- **Tool-Deaktivierung:** Im gepinnten Quellstand entfernt `shell_tool=false` die Shell-Werkzeuge,
  aber `ApplyPatch` kann weiterhin anhand des Modellkatalogs registriert werden. Read-only,
  verweigerte Rückfragen und das nachträgliche Ablehnen von Tool-Ereignissen beweisen keine
  vorbeugende Tool-Sperre.
- **Interne Retries:** Der eingebaute OpenAI-Provider übernimmt aus
  `model_providers.openai` nur die Katalog-Konfiguration. Dort gesetzte Request-/Stream-Retry-
  Werte werden ignoriert. Solche wirkungslosen Schalter werden deshalb nicht mitgegeben.
  Eine Subscription-Anbindung ohne versteckte Retries ist noch nicht nachgewiesen.
- **Isolation:** Rootless Betrieb, Profiltrennung, begrenzte Provider-Netzverbindung ohne
  Host-/Repo-/DB-Zugang, Dateisystemgrenzen und zuverlässige Container-Bereinigung brauchen
  eigene Live-Nachweise. Der geschlossene Simulationsvertrag hat bewusst keinen Providerzugang.
- **Anmeldung:** Ein späterer Owner-Login mit `codex login --device-auth` gehört ausschliesslich
  ins isolierte Profil. `login status` mit Exit-Code null allein unterscheidet nicht zwischen
  Subscription- und API-Key-Anmeldung; der verwendete Modus muss zusätzlich geprüft werden.

`simulation_ready` ist auch hier ausschliesslich ein Simulationsnachweis. Echte
Anbieter-Kontingente, semantische Modellqualität und Produktionsbereitschaft bleiben offen.

Quellenstand: [offizielle Version 0.166.0](https://github.com/openai/codex/releases/tag/rust-v0.166.0),
Commit `6ff1e1ca3b30fc8b914720681fcf5fe62e95f856`:

- [JSONL-Ereignisvertrag](https://github.com/openai/codex/blob/6ff1e1ca3b30fc8b914720681fcf5fe62e95f856/codex-rs/exec/src/exec_events.rs)
- [Tool-Registrierung](https://github.com/openai/codex/blob/6ff1e1ca3b30fc8b914720681fcf5fe62e95f856/codex-rs/core/src/tools/spec_plan.rs)
- [Provider-Konfiguration und eingebaute Overrides](https://github.com/openai/codex/blob/6ff1e1ca3b30fc8b914720681fcf5fe62e95f856/codex-rs/model-provider-info/src/lib.rs)
