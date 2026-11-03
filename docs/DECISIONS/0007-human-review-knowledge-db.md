# ADR 0007: Mensch als letzte Instanz; Wissens-DB nach Freigabe

## Kontext

Automatisierte Ausgaben koennen falsch oder sensibel sein.

## Entscheid

`policy.require_review` default **true**. Export in die Wissens-DB erfolgt erst nach expliziter Freigabe. Neue Wissens-Eintraege default `local_only`.

## Folgen

- Zusaetzlicher UI/API-Schritt `review` im Zustandsmodell.
- Wissens-DB waechst kontrolliert.
- Durchsatz sinkt — Qualitaet und Datenschutz haben Vorrang.
