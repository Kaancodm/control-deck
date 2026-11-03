# Etappe 18 - Abnahme-Lauf 1.0, SECURITY-Checkliste, Tag v1.0

Status: ABGELEITET AUS PLAN v1.1 (Drive, 03.11.2026); kompakt; nicht startklar, bis Etappe 17 gemergt ist
Branch-Vorschlag: `stage/18-abnahme-1-0`
Abhaengig von: allen Etappen 1 bis 17; VB-10 (Referenz-Testset)
Entscheidungen: Plan-Frage 12 (Takt), Entscheid ueber optionale Etappen 19, 20
Reviewer: Claude (gerade Etappe)
Abnahme laut Plan v1.1: volles Testset ueber alle Lanes gruen, SECURITY-Checkliste, Tag v1.0.

## 1 Ziel

Nachweis, dass Control Deck v1.0 die Leitentscheide erfuellt: Referenz-Testset ueber alle drei
Lanes, Sicherheits-Checkliste gegen AGENTS.md, ADRs und Plan Abschnitt 9, Lasttest ohne
Doppelausfuehrung, Restore-Probe, begruendete Abhaengigkeiten, CHANGELOG. Der Tag `v1.0.0` wird
nur nach GO des Owners gesetzt.

## 2 Ausgangslage

| Punkt | Stand |
|-------|-------|
| Referenz-Testset mit 12+ Faellen, 4 Traps | VB-10 |
| `make test-server` mit Lanes, Restore-Probe, `alembic check` | Etappen 9, 16 |
| `docs/SECURITY.md` als Grundlage | Etappe 15 |
| `pyproject.toml` Version `0.1.0` | belegt |

## 3 Scope

**In:** Abnahmeprotokoll, Testset-Markierung je Lane, Lasttest-Skript, SECURITY-Checkliste,
Abhaengigkeitstabelle, `CHANGELOG.md`, Version `1.0.0`, Release-Anleitung, Go/No-Go-Tabelle.

**Out:** neue Funktionen, Lanes 19 und 20, Performance-Optimierung ueber das Abnahmekriterium hinaus.

## 4 Default-Entscheidungen

| Nr | Entscheidung |
|----|--------------|
| D1 | Referenzfaelle erhalten ein Feld `lanes` im `expect` nur, wenn `reference_case.v1` dies erlaubt; sonst Zuordnung ueber `allowed_workers` (bestehend). Jeder Fall laeuft auf jeder zulaessigen Lane; Traps muessen auf allen Cloud-Lanen `blocked` sein. |
| D2 | Lasttest `scripts/loadtest.py`: 3 Runner-Prozesse der Lane `ollama` gegen den Stub, 50 Auftraege, Pruefung: jeder Auftrag genau ein `results`-Eintrag je Versuch, keine doppelten `acquire`-Events je Versuch, keine haengenden Leases nach Ende. Laeuft in `make test-server`. |
| D3 | SECURITY-Checkliste in `docs/SECURITY.md` als Tabelle mit Pruefmethode und Ergebnis: keine Secrets im Repo (`git log -p` Stichprobe, Suchmuster), keine IP-Literale und Hostnamen in `docs/` und Code, `.env` 600, Bindung geprueft, Reinraum-Tests gruen, Payload-Beweis vorhanden, Scanner-Traps blockiert, Login-Rate-Limit, Backup und Restore, Extra Usage aus (Owner bestaetigt), Abhaengigkeiten begruendet. |
| D4 | Abhaengigkeitstabelle in `docs/DEPENDENCIES.md`: Paket, Zweck, Etappe, PR mit Begruendung; `uv lock --check` in CI. |
| D5 | `CHANGELOG.md` nach Etappen gegliedert; Version in `pyproject.toml` auf `1.0.0`; Tag `v1.0.0` annotiert, setzt der Owner selbst (Agenten taggen nicht). |
| D6 | Go/No-Go-Tabelle im PR: jedes Abnahmekriterium mit Ergebnis, Datum, wer geprueft hat (Rolle). No-Go bei einem offenen Kriterium der Klasse Sicherheit. |
| D7 | Abnahmeprotokoll `docs/ABNAHME_1_0.md` ohne Modellausgaben, ohne Hostwerte; nur Zaehler, Dauern, PASS/FAIL. |

## 5 Umsetzung

| Pfad | Aenderung |
|------|-----------|
| `scripts/loadtest.py`, `Makefile` (`loadtest`) | Lasttest |
| `docs/SECURITY.md`, `docs/DEPENDENCIES.md`, `docs/ABNAHME_1_0.md`, `CHANGELOG.md` | Nachweise |
| `pyproject.toml` | Version |
| `.github/workflows/ci.yml` | `uv lock --check`, Suchmuster fuer Secrets und Adressen in `docs/` |
| `tests/reference/cases/` | Lane-Zuordnung vervollstaendigt |

## 6 Abnahmetests

| Nr | Fall | Erwartet |
|----|------|----------|
| 1 | `make test-server` komplett | alle Teile PASS; Referenz-Set gruen auf `ollama`, `claude`, `codex` |
| 2 | Traps auf Cloud-Lanen | alle `blocked`, keine Lane hat den Payload erhalten (Lane-Log zeigt keinen Aufruf) |
| 3 | Lasttest | 50 Auftraege, 0 Doppelausfuehrungen, 0 haengende Leases |
| 4 | Restore-Probe | PASS |
| 5 | Suchmuster in Repo und Historie | 0 Treffer fuer Secrets, Adressen, Hostnamen |
| 6 | Checkliste | jede Zeile mit Ergebnis und Pruefmethode |
| 7 | `uv lock --check`, `make lint`, `make test` | gruen |

## 7 Definition of Done

Protokoll und Checkliste vollstaendig; Go/No-Go im PR; Owner-GO fuer Merge; Tag durch Owner;
PR-Template mit Reviewer **Claude** und "Bewusst offen".

## 8 Blocker-Regel

Stoppen und melden, wenn: eine Etappe 1 bis 17 nicht gemergt ist; das Referenz-Set unter 12 Faellen
liegt; ein Sicherheitskriterium offen ist; der Owner die Freigabe fuer Serverlaeufe der Cloud-Lanen
nicht erteilt.

## 9 Risiken

| Risiko | Gegenmassnahme |
|--------|----------------|
| Abnahme wird zur Formsache | Pruefmethode je Zeile, No-Go-Regel D6 |
| Cloud-Lanen in der Abnahme kosten oder werden abgelehnt | Extra Usage aus, Policy-Ablehnung ist ein gueltiges Ergebnis (`policy_blocked`), wird protokolliert |

## 10 Bewusst offen

- Plan-Frage 12: Takt und Datum der Abnahme.
- Optionale Etappen 19 (Lane Cursor, n8n-Webhook) und 20 (Lane Grok) nur nach Klaerung der Kosten und Headless-Faehigkeit.
- Abgleich mit Plan v1.1 bei Start der Etappe.
