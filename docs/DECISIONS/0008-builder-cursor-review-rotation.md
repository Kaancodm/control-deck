# ADR 0008: Builder Cursor, Review durch anderen Anbieter

## Kontext

Einzel-Modell-Bias und Blindspots sollen reduziert werden.

## Entscheid

Implementierung darf primaer mit **Cursor** erfolgen. **Review** rotiert zwischen Anbietern (z. B. Codex und Claude im Wechsel). Merge nur nach explizitem GO des Repository-Owners. Agenten mergen nicht selbst.

## Folgen

- PR-Beschreibung dokumentiert Review-Anbieter und Abnahme.
- Hoeherer Koordinationsaufwand, bessere Fehlerfangquote.
- `AGENTS.md` fasst Pflichten fuer automatisierte Mitwirkende zusammen.
- Ergaenzt durch ADR 0010: Nach Owner-GO pro PR darf ein Agent Auto-Merge aktivieren.
