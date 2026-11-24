# Koordination

Stand: 24.11.2026.

If anything conflicts, AGENTS.md wins. Diese Datei ist nur die Standliste für GitHub und den deck-server. Regeln: siehe `AGENTS.md`.

Provenance der Rollen und Gates: Quelle Owner-Auftrag vom 12.11.2026, Methode Auftrag, Status proven als festgehaltener Ablauf in `AGENTS.md`. Provenance der PR-Zeilen bis #93: Quelle GitHub API am 12.11.2026, Methode `gh pr view` (Felder `state`, `isDraft`, `mergedAt`, `closedAt`, `mergeCommit`), Status proven für diese Felder. Provenance der Zeilen #56 und #95 bis #99: Quelle GitHub API am 24.11.2026, Methode Pull-Request-Felder `state`, `draft`, `merged_at` und Commit auf `main`, Status proven für diese Felder. #56 ist gemergt (`c846725`, merged_at 2026-11-24T19:07:51Z). Die Pause vom 12.11.2026 ist damit zu Ende. Angaben zum deck-server aus dem Owner-Auftrag vom 11.11.2026 bleiben im Repository unproven, ausser den zwei Entscheiden vom 24.11.2026 unten.

## Regeln

Regeln: siehe `AGENTS.md`.

1. Diese Datei vor dem Beanspruchen einer Aufgabe lesen.
2. Eine Aufgabe mit einer Zeile beanspruchen, bevor ein Branch oder ein Serverdienst angefasst wird.
3. Nie an einem Branch oder Dienst arbeiten, den jemand anders beansprucht hat.
4. Die Zeile aktualisieren, wenn die Arbeit erledigt ist.
5. Keine Geheimnisse in diese Datei schreiben.

## GitHub

| Aufgabe | Wer | Branch | PR | Stand | Zuletzt aktualisiert |
| --- | --- | --- | --- | --- | --- |
| Konflikte PR #86 lösen | Grok Bot | `stage/refactor-ollama-chat` | #86 | geschlossen, nicht gemergt (closedAt 2026-11-11T22:19:02Z, mergedAt leer) | 12.11.2026 |
| Ticket 08 Teil A | Cursor Cloud Agent | `stage/08a-claude-live-decision` | #88 | erledigt (gemergt, a8bd991, mergedAt 2026-11-11T21:48:12Z) | 12.11.2026 |
| Badge + Codex raus in Docs | Cursor Cloud Agent | `cursor/workflow-docs-codefactor-b0cb` | #87 | erledigt (gemergt, 947bd27, mergedAt 2026-11-11T20:12:52Z) | 12.11.2026 |
| Koordinationsliste | Cursor Cloud Agent | `docs/coordination` | #89 | erledigt (gemergt, f459255, mergedAt 2026-11-11T21:29:25Z) | 12.11.2026 |
| Systemprompt-Entwurf | Grok Bot | `docs/systemprompt` | #56 | erledigt (gemergt, c846725, merged_at 2026-11-24T19:07:51Z) | 24.11.2026 |
| Weitblick Stufe 1 | — | `main` | #96 | erledigt (gemergt, 8d5f9e0, merged_at 2026-11-23T18:13:58Z) | 24.11.2026 |
| Ollama JSON-Leser | — | `main` | #97 | erledigt (gemergt, 6123488, merged_at 2026-11-24T11:59:17Z) | 24.11.2026 |
| Weitblick Stufe 2 | — | `main` | #98 | erledigt (gemergt, 979b89b, merged_at 2026-11-24T12:44:16Z) | 24.11.2026 |
| Weitblick Haertung | — | `main` | #99 | erledigt (gemergt, 75318c9, merged_at 2026-11-24T19:32:52Z) | 24.11.2026 |
| `TEAM.md` als eine Regelseite | Claude Code | `claude/wonderful-cori-d21yd2` | #95 | geschlossen, nicht gemergt (closedAt 2026-11-24T22:09:12Z). Branch bleibt auf `269b0fa`. Kommentar: TEAM.md gilt ueber DECK-REGELN v1. | 24.11.2026 |
| Ollama-HTTP aufräumen | Cursor Cloud Agent | `stage/cleanup-ollama-http` | #90 | erledigt (gemergt, 190a507, mergedAt 2026-11-11T22:13:38Z) | 12.11.2026 |
| Regeln angleichen | Cursor Cloud Agent | `cursor/align-agent-rules-10a1` | #92 | erledigt (gemergt, a105245, mergedAt 2026-11-12T22:51:01Z, mergedBy Kaancodm) | 12.11.2026 |
| GO-Liste nach Merge #92 | Cursor Cloud Agent | `cursor/clear-merged-go-10a1` | #93 | erledigt (gemergt, a123d40, mergedAt 2026-11-12T23:16:19Z, mergedBy Kaancodm) | 13.11.2026 |

## deck-server

| Dienst | Wer | Stand | Nächster Schritt |
| --- | --- | --- | --- |
| OmniRoute | Grok Bot | läuft | Owner fügt restliche Gratis-Keys hinzu |
| Open WebUI | Grok Bot | läuft, Signup aus | Owner legt Admin-Konto an |
| Ollama (Version fix, kein Update) | Grok Bot | läuft, Bestand bleibt | Owner 24.11.2026, em: nichts loeschen |
| n8n | Grok Bot | läuft | Owner legt Owner-Konto an |
| Hermes Agent | Grok Bot | läuft | Kontextlänge erhöhen nach RAM-Check |
| Egress-Proxy (E2) | Grok Bot | aus | Owner 24.11.2026, em: bleibt aus. Dieser Lauf hat ihn nicht gestartet. |
| Ollama auf PC Bruder | Grok Bot | geplant | wenn Owner am PC |

## Weitblick

Owner 24.11.2026, em. Schritte 1 bis 4 in `weitblick/SERVERPRUEFUNG.md` sind die naechste lesende Pruefung. Dieser Lauf hat sie nicht ausgefuehrt. Schritt 5 (Test-Banks schreiben) und Schritt 6 (S-K80) bleiben ohne Ja. Quelle: Antwort `alle em` auf die Bewertung vom selben Tag. Methode: festgehaltener Entscheid. Status proven als Entscheid, unproven als Serverergebnis.
