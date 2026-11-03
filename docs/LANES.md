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
