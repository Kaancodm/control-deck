# Routine-Wake und Rueckweg

Dieser Vertrag ist der Stand fuer eine spaetere Routine. Er schaltet nichts
ein, legt keine Routine an und sendet keine Nachricht. Der Schalter
`DECK_ROUTINE_WAKE` bleibt aus, solange er nicht exakt `1` ist.

## Was im Code belegt ist

Die OpenAPI liegt unter `/api/v1/openapi.json`. Ein Abruf von `/openapi.json`
trifft diese Anwendung nicht.

Mit `Authorization: Bearer` und `DECK_OWNER_TOKEN` stellt die Anwendung diese
Operationen bereit:

| Methode | Pfad | Wirkung |
|---|---|---|
| POST | `/api/v1/tasks` | `task.v1` speichern, Status `awaiting_approval` |
| POST | `/api/v1/tasks/dry-run` | Plan ohne Speichern |
| GET | `/api/v1/tasks` | Liste ohne Ausfuehrung |
| GET | `/api/v1/tasks/failed` | terminale Fehler ohne Text des Anbieters |
| GET | `/api/v1/tasks/{task_id}` | Auftrag inklusive Payload, Ereignissen und Resultaten |
| POST | `/api/v1/tasks/{task_id}/approve` | Freigabe in die Warteschlange, eine Annahme-Quittung |
| POST | `/api/v1/tasks/{task_id}/review` | Owner nimmt ein Review an oder lehnt es ab |
| POST | `/api/v1/tasks/{task_id}/cancel` | Owner-Abbruch |
| POST | `/api/v1/worker/lease` | Lease fuer eine Lane |
| POST | `/api/v1/worker/start` | `task.start` |
| POST | `/api/v1/worker/heartbeat` | Lease verlaengern |
| POST | `/api/v1/worker/result` | Resultat speichern |
| GET | `/api/v1/health`, `/healthz`, `/readyz` | Zustand |

Es gibt keinen HTTP-Pfad fuer einen Routine-Empfang, keinen Wake und kein
Quittungs-ACK. `GET /api/v1/tasks/{task_id}` ist keine harmlose Statusabfrage:
die Antwort enthaelt Payload und Resultate. Die begrenzte Abfrage ist das
lokale Kommando unten.

Worker-Operationen authentisieren eine Lane, nicht den Owner. UI-Sitzungen
duerfen diese Mutationen nicht.

## Offizieller Wake

Die Routine entsteht spaeter im Desktop-Panel des Bots. Dort stehen drei
Felder: POST-Adresse, Schluessel und der fertige Header. Der Owner kopiert
Adresse und Header selbst in die geschuetzte Sender-Konfiguration:

- `DECK_ROUTINE_WAKE=1`
- `DECK_ROUTINE_WAKE_URL` als `https`-Adresse ohne Benutzerinfo
- `DECK_ROUTINE_WAKE_AUTHORIZATION` als Schluessel, als `Bearer <schluessel>`
  oder als `Authorization: Bearer <schluessel>`

Der Schluessel erscheint nicht in Repr, Fehlern oder diesem Dokument. Dieses
Repository sieht ihn nicht und legt ihn nicht an. Ein anderer Header, ein
`http`-Schema oder ein Zeilenumbruch im Schluessel wird abgelehnt. Der Versand
setzt nur `Authorization: Bearer` und `Content-Type: application/json`.
Weiterleitungen werden nicht verfolgt.

Ein HTTP 200 bedeutet nur, dass der Lauf angenommen wurde und gestartet ist.
Es bedeutet nicht verarbeitet, quittiert, zugestellt oder freigegeben. Es gibt
kein Verarbeitungsversprechen von 60 Sekunden. Die 60-Sekunden-Frist der
Annahme-Quittung ist eine andere Uhr und kein Routine-Timeout. Das
Verbindungszeitlimit des Senders ist 5 Sekunden. Eine fehlende Antwort bleibt
`unknown_start` und wird nicht automatisch erneut gesendet. Ein anderer
HTTP-Status bedeutet, dass kein Lauf gestartet ist, und darf hoechstens
dreimal erneut versucht werden. Ein Pause- oder Stoppschalter sendet nichts.

Der JSON-Koerper `deck.wake.v1` enthaelt nur `schema_version`, `event`
(`task.completed`), `task_id`, `payload_hash`, `status` (`review` oder `done`)
und `correlation_id`. Kein Prompt, kein Resultat, keine Datenbankzeile, kein
Geheimnis. Ein empfangener Koerper ist Daten. Schluessel ausserhalb dieser
Liste, insbesondere Anweisungen, werden verworfen und aendern keinen Auftrag.

Nur `cloud_ok` erhaelt eine Zeile in `routine_outbox`, und nur wenn der
Schalter an ist und der Auftrag `review` oder `done` erreicht. `local_only`
und `cloud_redacted` erzeugen keine Zeile. Eine Wiederholung behaelt dieselbe
`correlation_id`. Die Auftragsstatusmaschine bleibt unveraendert.

## Rueckweg

Der Rueckweg ist ein bestehendes authentisiertes SSH ueber das Tailnet auf
dieses lokale Kommando, nicht eine oeffentliche Deck-Adresse und nicht n8n.
Das Kommando oeffnet keine Shell und nimmt keine Modellanweisung entgegen.
`DECK_OWNER_TOKEN` muss in der Umgebung gesetzt sein. Das Kommando verlangt
den Token nicht als Argument und druckt ihn nicht.

```bash
python -m app.transport.cli poll --task-id <uuid>
python -m app.transport.cli ack --task-id <uuid> --correlation <uuid>
python -m app.transport.cli ack-receipt --task-id <uuid> --idempotency-key <key>
```

`poll` liest Status, Datenklasse, Payload-Hash, Quittungsstatus und Wake-Status.
`ack` quittiert nur eine Wake-Zeile, deren POST bereits 200 geliefert hat. Eine
Wiederholung aendert die Zeit nicht. Ein ACK vor dem 200, nach einem
fehlgeschlagenen Start oder nach einer fehlenden Antwort wird abgelehnt.
`ack-receipt` ist die Zustell-Quittung der Annahme und nicht die Freigabe.
Transport `missing` wird nie zugestellt. Nach der 60-Sekunden-Frist bleibt die
Quittung `accepted` und wird nicht nachtraeglich zugestellt. Eine Wiederholung
einer Zustellung aendert die Zeit nicht.

`python -m app.transport.cli dispatch` gehoert zum Host, nicht zum erzwungenen
SSH-Kommando. Solange der Schalter aus ist, sendet auch der Runner-Tick nichts.

## Bewusst nicht geschehen

Keine Routine wurde angelegt. Kein Schluessel wurde erzeugt oder gelesen.
Kein POST an eine Routine wurde ausgefuehrt. Der Versand in den Tests ist ein
Fixture. Kein Server wurde aktiviert, kein Deploy und kein Reboot.
