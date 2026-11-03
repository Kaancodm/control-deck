# ADR 0002: Kern auf Host, Ausfuehrung im Reinraum-Container

## Kontext

Worker sollen untrusted Code und grosse Modelle ausfuehren, waehrend Steuerung und Datenbank nah am Host bleiben.

## Entscheid

Der **Kern** (API, Router-Funktionen, DB-Zugriff) laeuft als systemd-Dienste auf dem Host. Die **Ausfuehrung** eines Versuchs erfolgt in einem rootless Docker-Reinraum mit minimalen Rechten und ohne persistente Secrets im Container.

## Folgen

- Klare Trennung zwischen Orchestrierung und Ausfuehrung.
- Host-Patches und Container-Images koennen unabhaengig aktualisiert werden.
- Mehr Moving Parts (Image-Build, Volume-Mounts) — wird in `docker/` und Deploy-Doku verankert.
