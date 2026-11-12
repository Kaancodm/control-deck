# Koordination

Stand: 12.11.2026.

If anything conflicts, AGENTS.md wins. Diese Datei ist die gemeinsame Standliste für GitHub und den deck-server. Sie gilt für Claude, Cursor, Gemini Pro und Grok Bot. Sie setzt keine Rolle, kein Modell und kein Merge-Recht. Das steht in `AGENTS.md`.

Provenance der Rollen und Gates: Quelle Owner-Auftrag vom 12.11.2026, Methode Auftrag, Status proven als festgehaltener Ablauf in `AGENTS.md`. Provenance der PR-Zeilen: Quelle GitHub API am 12.11.2026, Methode `gh pr view` (Felder `state`, `isDraft`, `mergedAt`, `closedAt`, `mergeCommit`), Status proven für diese Felder. Die Pause von PR #56 ist die Owner-Vorgabe vom 12.11.2026; GitHub zeigt den PR weiter als offenen Entwurf. Angaben zum deck-server bleiben der Owner-Auftrag vom 11.11.2026, Methode Angabe des Owners, Status im Repository unproven.

## Regeln

1. Diese Datei vor jeder Arbeit lesen. Wenn eine Regel hier von `AGENTS.md` abweicht, gilt `AGENTS.md`.
2. Eine Aufgabe mit einer Zeile beanspruchen, bevor ein Branch oder ein Serverdienst angefasst wird.
3. Nie an einem Branch oder Dienst arbeiten, den jemand anders beansprucht hat.
4. Die Zeile aktualisieren, wenn die Arbeit erledigt ist.
5. Nur Grok Bot mergt, und nur nach dem einmaligen Ja des Owners. Claude, Fable, Gemini und Cursor mergen nicht. Kein Auto-Merge. Kein Force-Push. Das Repository bleibt öffentlich.
6. Deploy, Neustart, Migration, Server-Installation und Löschung nur mit ausdrücklichem GO des Owners für genau diese Aktion. Danach führt sie Grok Bot aus. Vorbereitung im Repository ist ohne dieses GO erlaubt.
7. Keine Geheimnisse in dieser Datei. Geheimnisse nie ausgeben und nie committen.
8. Offene Punkte in Berichten: nummeriert, je Punkt `em` (Empfehlung), `option 2`, und der grösste Unterschied in einem Satz. Der Owner antwortet `N. em` oder `N. 2`.
9. `Wartet auf dein GO` steht oben in `CLAUDE.md`. Jeder Agent und jeder Lead-Bot schreibt diese Liste am Ende des Jobs neu. Lead-Bots lesen vor jedem Lauf `AGENTS.md`, `CLAUDE.md` und `COORDINATION.md`.

## GitHub

| Aufgabe | Wer | Branch | PR | Stand | Zuletzt aktualisiert |
| --- | --- | --- | --- | --- | --- |
| Konflikte PR #86 lösen | Grok Bot | `stage/refactor-ollama-chat` | #86 | geschlossen, nicht gemergt (closedAt 2026-11-11T22:19:02Z, mergedAt leer) | 12.11.2026 |
| Ticket 08 Teil A | Cursor Cloud Agent | `stage/08a-claude-live-decision` | #88 | erledigt (gemergt, a8bd991, mergedAt 2026-11-11T21:48:12Z) | 12.11.2026 |
| Badge + Codex raus in Docs | Cursor Cloud Agent | `cursor/workflow-docs-codefactor-b0cb` | #87 | erledigt (gemergt, 947bd27, mergedAt 2026-11-11T20:12:52Z) | 12.11.2026 |
| Koordinationsliste | Cursor Cloud Agent | `docs/coordination` | #89 | erledigt (gemergt, f459255, mergedAt 2026-11-11T21:29:25Z) | 12.11.2026 |
| Systemprompt-Entwurf | Grok Bot | `docs/systemprompt` | #56 | pausiert (Owner Grok Bot). GitHub: offen, Entwurf, mergedAt leer | 12.11.2026 |
| Ollama-HTTP aufräumen | Cursor Cloud Agent | `stage/cleanup-ollama-http` | #90 | erledigt (gemergt, 190a507, mergedAt 2026-11-11T22:13:38Z) | 12.11.2026 |
| Regeln angleichen | Cursor Cloud Agent | `cursor/align-agent-rules-10a1` | #92 | Entwurf offen | 12.11.2026 |

## deck-server

| Dienst | Wer | Stand | Nächster Schritt |
| --- | --- | --- | --- |
| OmniRoute | Grok Bot | läuft | Owner fügt restliche Gratis-Keys hinzu |
| Open WebUI | Grok Bot | läuft, Signup aus | Owner legt Admin-Konto an |
| Ollama (Version fix, kein Update) | Grok Bot | läuft | Modell-Aufräumen wartet auf Owner-Ja |
| n8n | Grok Bot | läuft | Owner legt Owner-Konto an |
| Hermes Agent | Grok Bot | läuft | Kontextlänge erhöhen nach RAM-Check |
| Egress-Proxy (E2) | Grok Bot | nicht gestartet | braucht eigenes GO |
| Ollama auf PC Bruder | Grok Bot | geplant | wenn Owner am PC |
