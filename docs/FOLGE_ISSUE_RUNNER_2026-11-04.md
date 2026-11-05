# Folge-Issue-Entwurf: Unvollständig veröffentlichte Ergebnisdateien wiederaufnehmen

Status: **Entwurf, nicht umgesetzt**. Priorität: P2.
Quelle: Prüfung von `app/workers/runner.py` am Stack-Endstand `f7f67233` und
`main` bei `a33cc3f`.

## Stand

Die Ursache ist auf `main` behoben: Der Host-Runner committet zuerst die
Ergebnis-Transaktion und veröffentlicht `result.json` und das Lane-Log erst danach.
Ein zurückgerolltes Ergebnis hinterlässt keine Dateien mehr. Schlägt das Schreiben
nach dem Commit fehl, bleibt die Datenbank massgebend und der Versuch endet als
`completed` mit `artifact_publication_failed`. Details: [RUNNER.md](RUNNER.md).

## Verbleibendes Problem

Nach `artifact_publication_failed` fehlen die lokalen Dateien ganz oder teilweise,
zum Beispiel `result.json` ohne Lane-Log. Es gibt kein Nachholen und kein Aufräumen.
Artefakte, die vor der Korrektur inkonsistent entstanden sind, bleiben ebenfalls liegen.

## Spätere Akzeptanzkriterien

- Fehler nach Veröffentlichung von `result.json`, vor dem Lane-Log reproduzieren.
- Wiederaufnahme ordnet Dateien eindeutig Task, Attempt und Lease zu.
- Nachholen schreibt ausschliesslich aus dem gespeicherten Datenbankergebnis.
- Kein zusätzlich gezähltes Ergebnis und kein Überschreiben unveränderlicher Evidenz.
- Behandlung unvollständig veröffentlichter Artefakte dokumentieren und testen.

Dieser Entwurf ist keine Freigabe für Umsetzung, Servereingriffe oder Merge.
