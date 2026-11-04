# Folge-Issue-Entwurf: Ergebnisdateien und DB-Commit konsistent wiederaufnehmen

Status: **Entwurf, nicht umgesetzt**. Priorität: P2.
Quelle: Prüfung von `app/workers/runner.py` am Stack-Endstand `f7f67233` und
`main` bei `a33cc3f`. Das Verhalten besteht bereits auf main.

## Problem

Der Host-Runner veröffentlicht `result.json` und das Lane-Log vor dem Commit
der Ergebnis-Transaktion. Schlägt der zweite Schreibvorgang oder der Commit fehl,
rollt die Datenbank zurück, bereits veröffentlichte Dateien bleiben jedoch liegen.
Die Rückgabe `result_persistence_failed` beschreibt den Fehler, stellt die
Konsistenz zwischen autoritativer Datenbank und Dateinachweis aber nicht wieder her.

## Spätere Akzeptanzkriterien

- Fehler nach Veröffentlichung von `result.json`, vor dem Lane-Log reproduzieren.
- Commit-Fehler nach Veröffentlichung beider Dateien reproduzieren.
- Wiederaufnahme ordnet Dateien eindeutig Task, Attempt und Lease zu.
- Kein zusätzlich gezähltes Ergebnis und kein Überschreiben unveränderlicher Evidenz.
- Behandlung unvollständig veröffentlichter Artefakte dokumentieren und testen.

Der aktuelle Fixauftrag ändert `runner.py` nicht. Dieser Entwurf ist keine
Freigabe für Umsetzung, Servereingriffe oder Merge.
