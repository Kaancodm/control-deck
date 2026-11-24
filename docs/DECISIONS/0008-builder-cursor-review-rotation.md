# ADR 0008: Builder Cursor, Review durch anderen Anbieter

Aktueller Ablauf: Nachtrag vom 2026-11-12 am Ende dieser Datei. If anything conflicts, AGENTS.md wins. Der Text bis zu diesem Nachtrag ist Historie.

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

## Nachtrag (2026-11-12)

Quelle: Owner-Vorgabe vom 2026-11-12. Methode: Auftrag zu dieser Doku-Aenderung.
Status: `proven` als festgehaltener Ablauf in diesem Nachtrag. Der Entscheid oben und der
Nachtrag vom 2026-11-11 bleiben als Historie stehen. Dieser Nachtrag ersetzt davon die
Saetze «Codex und Gemini Pro sind nicht mehr im Einsatz», «SuperGrok reviewt nur den Code»
und «Andere Agenten und Bots, einschliesslich Fable, mergen nicht», soweit sie vom folgenden
Ablauf abweichen. If anything conflicts, AGENTS.md wins.

Codex ist nicht im Einsatz. Gemini Pro ist im Einsatz.

1. Der Owner gibt das Ziel in einem Satz.
2. Claude plant mit `/advisor` (Fable) und schreibt einen kurzen Bauauftrag mit einem Pruefbefehl.
3. Cursor baut in einem Branch. Der Pruefbefehl muss bestehen.
4. Claude baut nur einen komplexen Auftrag, und nur wenn der Auftrag das ausdruecklich sagt.
   Dafuer gilt Opus 5.5 mit Effort medium. Gemini Pro ist Lead Full-Stack Developer und
   Pair-Programming-Partner, gesteuert von Grok Bot, ohne Merge-Recht und nicht Security-Head.
5. Der Owner sagt einmal Ja. Danach mergt Grok Bot.

Nur Grok Bot mergt. Claude, Fable, Gemini und Cursor mergen nicht. Kein Force-Push.
Das Repository bleibt oeffentlich. Deploy, Neustart, Migration, Server-Installation und
Loeschung brauchen ein ausdrueckliches GO des Owners fuer genau diese Aktion.

Folgen dieses Nachtrags: `AGENTS.md` ist die einzige Quelle fuer Rollen, Modelle, Merge
und Gates. `CLAUDE.md` und `COORDINATION.md` verweisen darauf. Die Lane `codex` im Produkt
ist von diesem Nachtrag nicht betroffen.
