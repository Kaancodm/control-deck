# Lanes und CLI-Simulation

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
Das Live-Profil meldet stets **`unavailable` / `live_profile_unavailable`**. Oberflaeche und
spaetere Health-Endpunkte muessen diesen Unterschied anzeigen.

## Inbetriebnahme

Die Live-Lane ist in dieser Etappe **nicht aktivierbar**. Die offizielle CLI bietet keinen
No-Pull-Schalter: `run` kann ein fehlendes Modell automatisch laden. Eine vorgaengige
Inventarpruefung schliesst das Rennen bis zum Aufruf nicht. Ein `:local`-Suffix begrenzt die
Modellquelle, verhindert aber den Download nicht. `OLLAMA_NO_CLOUD` muss im Daemon gelten;
eine Variable ausschliesslich im CLI-Kindprozess konfiguriert einen laufenden Daemon nicht.
Multimodale CLI-Pfade koennen zudem im Prompt genannte Bild-/Audiodateien vom Host lesen.
Ein Exit-Code null allein beweist fuer alle CLI-Pfade keinen vollstaendigen Modellabschluss.

Vor einer spaeteren Live-Anbindung braucht es nachweisbare Grenzen fuer automatische
Downloads und Cloud-Wege, Dateisystem-Isolation gegen promptgesteuerte Dateizugriffe sowie
ein eindeutiges Abschlussprotokoll. Diese Grenzen muessen auch bei Rennen, Abbruch und
Daemon-Neustart gelten und separat getestet werden. Ein Konfigurations-Boolean allein
genuegt nicht. Echte Logins, Serveraenderungen, Modellinstallationen und Modelltests wurden
nicht ausgefuehrt. Die semantische Qualitaet prueft erst das vom Owner erstellte Referenzset
bei der gesonderten Inbetriebnahme.

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
