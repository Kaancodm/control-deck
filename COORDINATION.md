# Koordination

Stand: 11.11.2026. Angaben zum Betrieb: Quelle Owner-Auftrag vom 11.11.2026, Methode Angabe des Owners, Status im Repository unproven.

Diese Datei ist die eine gemeinsame Standliste für die gesamte Arbeit auf GitHub und auf dem deck-server. Sie gilt für alle Werkzeuge: Claude 1, Claude 2, Grok 2, Grok 3, Cursor und Grok Bot. Der Owner spricht nur mit Grok Bot. Grok Bot koordiniert und hält diese Datei aktuell.

## Regeln

1. Diese Datei vor jeder Arbeit lesen.
2. Eine Aufgabe mit einer Zeile beanspruchen, bevor ein Branch oder ein Serverdienst angefasst wird.
3. Nie an einem Branch oder Dienst arbeiten, den jemand anders beansprucht hat.
4. Die Zeile aktualisieren, wenn die Arbeit erledigt ist.
5. Nur Grok Bot führt den Merge aus, und nur nach dem einmaligen Ja des Owners. Kein Auto-Merge.
6. Änderungen am Server (Installationen, Neustarts, Konfiguration) nur durch Grok Bot oder mit ausdrücklichem GO des Owners.
7. Keine Geheimnisse in dieser Datei.

## GitHub

| Aufgabe | Wer | Branch | PR | Stand | Zuletzt aktualisiert |
| --- | --- | --- | --- | --- | --- |
| Konflikte PR #86 lösen | Grok Bot | `stage/refactor-ollama-chat` | #86 | zu schliessen, wartet auf Owner | 11.11.2026 |
| Ticket 08 Teil A | Cursor Cloud Agent | `stage/08a-claude-live-decision` | #88 | erledigt (gemergt, a8bd991) | 11.11.2026 |
| Badge + Codex raus in Docs | Cursor Cloud Agent | `cursor/workflow-docs-codefactor-b0cb` | #87 | erledigt (gemergt) | 11.11.2026 |
| Koordinationsliste | Cursor Cloud Agent | `docs/coordination` | #89 | erledigt (gemergt, f459255) | 11.11.2026 |
| Systemprompt-Entwurf | Grok Bot | `docs/systemprompt` | #56 | pausiert bis 17.11.2026 | 12.11.2026 |
| Ollama-HTTP aufräumen | Cursor Cloud Agent | `stage/cleanup-ollama-http` | #90 | erledigt (gemergt, 190a507) | 12.11.2026 |
| Quota-Regeln, Schlussbericht, Builder-Ausnahme | Claude (Fable plant, Opus baut) | `docs/quota-report-rules` | #91 | erledigt mit Merge von #91 | 12.11.2026 |

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
