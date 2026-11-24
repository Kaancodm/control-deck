# Koordination

Stand: 24.11.2026.

If anything conflicts, AGENTS.md wins. Diese Datei ist nur die Standliste für GitHub. Rollen, Merge und Entscheide: siehe `TEAM.md`. Regeln: siehe `AGENTS.md`.

Provenance der Gates: Quelle Owner-Auftrag vom 12.11.2026, Methode Auftrag, Status proven als festgehaltener Ablauf in `AGENTS.md`. Provenance der PR-Zeilen bis #93: Quelle GitHub API am 12.11.2026, Methode `gh pr view` (Felder `state`, `isDraft`, `mergedAt`, `closedAt`, `mergeCommit`), Status proven für diese Felder. #56 ist gemergt (`c846725`, merged_at 2026-11-24T19:07:51Z, Quelle GitHub API am 24.11.2026). Die Pause vom 12.11.2026 ist damit zu Ende. `main` `75318c9` ist per Merge in diesen Entwurf uebernommen.

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
| Ollama-HTTP aufräumen | Cursor Cloud Agent | `stage/cleanup-ollama-http` | #90 | erledigt (gemergt, 190a507, mergedAt 2026-11-11T22:13:38Z) | 12.11.2026 |
| Regeln angleichen | Cursor Cloud Agent | `cursor/align-agent-rules-10a1` | #92 | erledigt (gemergt, a105245, mergedAt 2026-11-12T22:51:01Z) | 12.11.2026 |
| GO-Liste nach Merge #92 | Cursor Cloud Agent | `cursor/clear-merged-go-10a1` | #93 | erledigt (gemergt, a123d40, mergedAt 2026-11-12T23:16:19Z) | 13.11.2026 |
| `TEAM.md` als eine Regelseite | Claude Code | `claude/wonderful-cori-d21yd2` | #95 | offen, Entwurf, auf `75318c9` per Merge. 16 Fragen in `TEAM.md` Abschnitt 12 bleiben offen. | 24.11.2026 |

## Entscheide 24.11.2026

Owner `alle em`. Egress-Proxy bleibt aus. Ollama-Bestand bleibt. Weitblick-Schritte 1 bis 4 sind die lesende Pruefung und wurden hier nicht ausgefuehrt. Schritt 5 und S-K80 bleiben ohne Ja. Quelle: Antwort auf die Bewertung vom selben Tag. Methode: festgehaltener Entscheid. Status proven als Entscheid, unproven als Serverergebnis.
