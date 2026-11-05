# Host-Runner und Worker-Vertrag

Etappe 8 verbindet Queue, Datenschranke, lokale Maps und Laufartefakte. Der Host-Runner
verwendet die gemeinsame DB-Logik direkt; laufende Arbeit benoetigt keinen HTTP-Aufruf
gegen die eigene API. Lanes erhalten ausschliesslich geprueften Versandtext und sichere
Versuchsdaten, niemals den Originalauftrag, fruehere Ergebnisse oder Redaction-Maps.

## Ablauf eines Versuchs

1. Ein begrenzter Lease-Sweep wird in einer eigenen Transaktion committed.
2. Der Claim prueft Owner-Freigabe, Lane, Policy, Owner-Deadline, Versuchs- und Tagesbudget,
   Pause/Not-Aus und globale Parallelitaet. Der Standard ist ein aktiver Versuch.
3. Die finale Payload-Vorbereitung liest und prueft jeden Anhang einmal. SHA-256 dieser
   exakten Bytes muss mit dem letzten Owner-Freigabe-Event uebereinstimmen. Derselbe
   gelesene Text wird gerendert; ein zweites Lesen fuer die Hashpruefung findet nicht statt.
4. Unterstuetzte Treffer bei `cloud_redacted` werden lokal ersetzt. Secrets, Home-Pfade
   und interne Hostnamen bleiben gesperrt. `cloud_ok` umgeht keine Datenschranke.
5. Maps, Claim und `task.prepare` werden committed, bevor vorbereiteter Text an einen
   Executor oder HTTP-Worker gelangt. Die exakten Bytes liegen unveraenderlich und privat
   in `runs/<task_id>/<attempt>/payload.sent.md`.
6. Ein eigener Startschritt prueft Identitaet, Vorbereitung und Lease erneut. Erst nach
   dessen Commit wird der Executor aufgerufen. Ein zweiter Start wird abgewiesen.
7. Der Heartbeat-Monitor prueft Kontrolle und verlaengert die Lease in kurzen eigenen
   Transaktionen. Waehrend des Executor-Aufrufs bleibt keine Ausfuehrungs-Transaktion offen.
8. Ergebnisannahme prueft Task, Versuch, Lane, Lease, Versandhash und Redaction-Metadaten.
   Rueckabbildung bleibt lokal; automatische Abnahme verwendet den rueckabgebildeten Text.
   Anschliessend wird der aktuelle Zustand erneut unter Lock geprueft.

Jeder committed Claim verbraucht einen Versuch. Geht eine Datei vor Auslieferung
voruebergehend verloren, wird der noch nicht ausgelieferte Claim zurueckgerollt und ein
gedrosseltes `task.gate_error` geschrieben. Ein geaenderter Freigabe-Hash blockiert den Task.
Ein fehlgeschlagener Commit kann ein unveraenderliches, ungesendetes Artefakt hinterlassen;
der Runner fuehrt es nicht aus und ueberschreibt es nicht. Widersprechen vorhandene Bytes
einer erneuten Vorbereitung, endet der Task als `blocked` mit Grund `artifact_conflict`
im Audit. Neue Maps werden zurueckgerollt. Derselbe Pull prueft den naechsten Task,
begrenzt auf 32 Vorbereitungsversuche; andere Artefaktfehler bleiben Fehler des Aufrufs.

Jeder Executor muss seine `lane` ausdruecklich als `Worker` deklarieren. Der Host prueft
diese Identitaet vor dem DB-Zugriff und erneut vor der Textuebergabe. Fehlende, ungueltige
oder abweichende Identitaet ergibt `executor_lane_mismatch`; es gibt keine implizite
Ollama-Lane. Nur ein als Ollama deklarierter Executor darf `local_only` erhalten.

## Steuerentscheide

| Entscheid | Wirkung |
|-----------|---------|
| `continue` | Lease und Identitaet gueltig, Ausfuehrung darf weiterlaufen |
| `cancel` | Owner-Abbruch oder Not-Aus; Adapter beendet seinen Versuch und bestaetigt Abbruch |
| `expired` | Ausschliesslich Owner-Deadline abgelaufen; die Expiry-Transition ist vor dem Stop committed |
| `lost_lease` | Lease fehlt, ist abgelaufen oder gehoert einem anderen Versuch; keine Ergebnisannahme |

Ein Lease-Timeout erzeugt niemals `expired`. Ein DB-Fehler beendet die Freigabe zur
Weiterarbeit. Die aktuelle Executor-Schnittstelle verlangt kooperativen Abbruch ueber
`cancel_requested`; spaetere CLI-Adapter muessen ihren Prozess daraufhin beenden.
Der Rahmen kann beliebigen ignorierenden Python-Code nicht zwangsweise stoppen.

## Pause, Not-Aus und Parallelitaet

Die lokale Tabelle `settings` speichert ausschliesslich typisierte Steuerwerte:

- `paused`: verhindert neue Claims, laufende Versuche bleiben aktiv.
- `lanes.<worker>.paused`: verhindert neue Claims fuer diese Lane.
- `stop_requested`: verhindert neue Claims und setzt Abbruchanforderungen fuer aktive Versuche.
- `max_parallel`: maximale Zahl `leased` plus `running`, Standard 1, Bereich 1 bis 16.

Das Zuruecksetzen von Not-Aus hebt bestehende Abbruchanforderungen nicht auf. Aenderungen
und Claims verwenden denselben transaktionsgebundenen Admission-Lock vor Task-/Lease-Locks.
Ungueltige gespeicherte Steuerwerte verhindern Ausfuehrung. Migration `0009` verweigert den
Downgrade, solange Einstellungen vorhanden sind.

Owner-Endpunkte: `GET /api/v1/control/settings` und `PATCH /api/v1/control/settings` mit
`{"changes": {"paused": true}}`. Worker-Tokens berechtigen nicht zu diesen Aenderungen.

## Worker-API

`POST /api/v1/worker/lease` verlangt das ausdrueckliche Feld `worker` (`ollama`, `claude`,
`codex`). Es zieht ausschliesslich Auftraege der durch das Token authentifizierten Lane;
die Angabe im Body muss dazu passen. Die Antwort enthaelt
sichere Task-Metadaten, Lease, vorbereiteten Text, SHA-256 und Redaction-Zaehler. Die lokale
Map und der originale `task.v1`-Auftrag werden nicht ausgeliefert.

`POST /worker/start` verlangt `task_id`, `attempt`, `lease_id` und `worker`.
`POST /worker/heartbeat` verlangt dieselbe Identitaet und optional `extend_seconds`.
Beide liefern einen Steuerentscheid; die API committed vorher ausdruecklich und verlaesst
sich nicht auf spaete Dependency-Bereinigung. `POST /worker/result` behaelt den Body mit
`lease_id` und `result.v1`; ein Ergebnis vor explizitem Start wird abgewiesen.

Jede Lane verwendet ihr eigenes Token: `DECK_WORKER_TOKEN_OLLAMA`,
`DECK_WORKER_TOKEN_CLAUDE` oder `DECK_WORKER_TOKEN_CODEX`. Ein gemeinsames Token wird nicht
akzeptiert. Doppelte Lane-Tokens oder eine Uebereinstimmung mit dem Owner-Token sperren
beide Authentifizierungspfade. Anbieter-Container erhalten diese Host-Zugangsdaten nicht.
Alle vier Worker-Endpunkte weisen abweichende Lane-Angaben vor dem Task-Zugriff ab.

Auch ein HTTP-Lease-Pull fuehrt den begrenzten Lease-Sweep vor dem Claim aus und committed
ihn separat. Ein spaeterer Vorbereitungsfehler kann die Wiederherstellung nicht zurueckrollen.
Unbestaetigte Abbrueche belegen Kapazitaet bis zur Bestaetigung oder zum Lease-Ablauf;
eine noch aktive Lease wird durch Polling nicht vorzeitig freigegeben.

## Artefakte und Simulation

Laufverzeichnisse und `out/` sind privat (`0700`), Artefakte `0600`. Symlinks, Hardlinks,
abweichende vorhandene Inhalte und Pfadflucht werden abgewiesen. `result.json` bleibt lokal;
das Lane-Log enthaelt nur sichere Laufmetadaten, keine Auftragstexte oder Provider-Ausgaben.
Beide werden erst geschrieben, nachdem das Ergebnis in der Datenbank committed ist; ein
zurueckgerolltes Ergebnis hinterlaesst keine Artefakte. Scheitert das Schreiben danach, bleibt
die Datenbank massgebend und der Lauf meldet `completed` mit `artifact_publication_failed`.
Nichtleere `output.files` werden im Rahmen bis zu einer verifizierten Adapter-Dateianbindung
abgewiesen. Es wird kein erzeugter Code ausgefuehrt.

Der CLI-Einstieg `python -m app.workers.runner_main` bietet ausschliesslich eine ausdrueckliche
Simulation. `--executor ollama-fake-cli` nutzt ab Etappe 9 die gebuendelte Fake-CLI mit
Inventarpruefung und begrenzten Prozessen; das Standardprofil bleibt `simulation`.
Das echte Ollama-Profil ist gesperrt. Details und Inbetriebnahme-Grenzen:
[LANES.md](LANES.md). Adapter fuer `claude -p` und `codex exec` folgen mit Fake-CLI-Tests
in den dafuer vorgesehenen Etappen. Inhaltliche Referenzfaelle erstellt der Owner.

## Inbetriebnahme

Nicht ausgefuehrt: echte Logins, echte Modell-/Anbieteraufrufe, produktive DB-Verbindungen,
Serverinstallation, Deployment oder Freigabe eines Release. Diese Arbeiten bleiben separat.
Die Abnahme dieser Etappe gilt fuer Repo-Code, isolierte Testdatenbanken und Fake-Executors.
