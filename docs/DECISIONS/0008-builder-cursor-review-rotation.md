# ADR 0008: Builder Cursor, Review durch anderen Anbieter

## Kontext

Einzel-Modell-Bias und Blindspots sollen reduziert werden.

## Entscheid

Implementierung darf primaer mit **Cursor** erfolgen. **Review** rotiert zwischen Anbietern (z. B. Codex und Claude im Wechsel). Merge nur nach explizitem GO des Repository-Owners. Agenten mergen nicht selbst.

## Folgen

- PR-Beschreibung dokumentiert Review-Anbieter und Abnahme.
- Hoeherer Koordinationsaufwand, bessere Fehlerfangquote.
- `AGENTS.md` fasst Pflichten fuer automatisierte Mitwirkende zusammen.

## Nachtrag (2026-11-11)

Quelle: Owner-Vorgabe vom 2026-11-11. Methode: Auftrag zu dieser Doku-Aenderung.
Status: `proven` als festgehaltener Ablauf in diesem Nachtrag. Der Entscheid oben bleibt
als Historie stehen. Dieser Nachtrag ersetzt die Review-Rotation (Codex und Claude im
Wechsel) und die Merge-Regel.

Codex und Gemini Pro sind nicht mehr im Einsatz.

1. Der Owner gibt das Ziel in einem Satz.
2. Claude plant und schreibt einen kurzen Bauauftrag mit einem Pruefbefehl.
3. Cursor baut in einem Branch. Der Pruefbefehl muss bestehen.
4. SuperGrok reviewt nur den Code.
5. Der Owner sagt einmal Ja. Danach mergt Grok Bot.

Nur Grok Bot mergt. Andere Agenten und Bots, einschliesslich Fable, mergen nicht.

Folgen dieses Nachtrags: `AGENTS.md` beschreibt denselben Ablauf. Die Reviewer-Spalte und
der Tagesplan in `docs/ETAPPEN/README.md` bleiben der Planstand v1.1 und sind nicht der
aktuelle Reviewer. Die Lane `codex` im Produkt ist von diesem Nachtrag nicht betroffen.
