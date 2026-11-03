# ADR 0003: Auftrag unveraenderlich, Laufzeit in runs/events

## Kontext

Revisionssicherheit und Debugging erfordern klare Trennung zwischen Auftragseingang und Laufzeitverhalten.

## Entscheid

Ein akzeptierter `task.v1`-Datensatz wird **nicht** mutiert. Versuche, Logs, Payload-Snapshots und Heartbeats landen unter `runs/` (Dateien) bzw. in Event-Tabellen.

## Folgen

- Wiederholbare Nachvollziehbarkeit pro `task_id` und `attempt`.
- Kein „stillen“ Aendern von Instructions nach Start.
- Speicherwachstum — Retention-Policy folgt spaeter.
