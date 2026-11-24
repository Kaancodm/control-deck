# Bauauftrag NN-<slug>

Vorlage fuer `/plan-ticket`. Kopie als `tasks/NN-<slug>.md` ablegen. Eine Etappe = ein Auftrag =
ein Branch `stage/NN-<slug>` = ein Pull Request. Der Owner sagt einmal Ja, danach mergt nur Grok Bot. Claude, Fable, Gemini und Cursor mergen nicht. If anything conflicts, AGENTS.md wins.

## Etappe

<!-- Nummer und Name. Rollen: AGENTS.md. Nachtrag ADR 0008 vom 2026-11-12. -->

## Ziel

<!-- Ein Absatz: Was ist nach dieser Etappe moeglich, das vorher nicht moeglich war? -->

## Rahmen

- Zustandsautomat: `app/db/state_machine.py` ist die einzige Quelle; `awaiting_approval` vor `queued`.
- Datenklassen: `local_only` (Default), `cloud_redacted`, `cloud_ok`; `local_only` nie auf eine Cloud-Lane.
- Tests nur gegen `DECK_TEST_DATABASE_URL` (DB-Name endet auf `_test`).
- Keine neuen Abhaengigkeiten ohne Begruendung im PR. Keine Secrets, keine Server-Eingriffe.
- Code und Commits Englisch (Conventional Commits), `README.md` und `docs/` de-CH ohne Eszett.

## Schritte (atomar, je ein Commit)

<!-- Jeder Schritt nennt die Dateien explizit. `(neu)` fuer neue Dateien. -->

1. `<pfad/datei.py>` — <was genau geaendert wird>
2. `<pfad/test_datei.py>` (neu) — <welche Faelle getestet werden>
3. `docs/<DATEI>.md` — <welcher Abschnitt ergaenzt wird>

## Abnahme (ausfuehrbare Befehle)

<!-- Jedes Kriterium ist ein Befehl plus erwartetes Ergebnis. Keine reinen Prosa-Kriterien. -->

| Befehl | Erwartet |
|--------|----------|
| `make lint` | exit 0 |
| `make test` | exit 0, keine neuen Skips |
| `uv run pytest tests/unit/test_<x>.py -q` | `N passed` |
| `grep -rn "<verbotenes muster>" app/` | keine Treffer |

## Bewusst offen

<!-- Was diese Etappe absichtlich nicht enthaelt, mit Verweis auf die Folge-Etappe. -->

## Offene Fragen (TODO)

<!-- Unklares als TODO markieren statt raten. Der Owner beantwortet vor dem Start. -->
