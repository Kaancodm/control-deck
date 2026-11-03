# ADR 0006: Nur offizielle CLIs, keine API-Keys

## Kontext

Kosten, Compliance und Geheimnisverwaltung sollen zur Laufzeit minimal sein.

## Entscheid

Cloud-Worker werden ausschliesslich ueber offizielle CLIs angesprochen (`claude -p`, `codex exec`). Keine API-Keys im Repository oder in Umgebungsvariablen der Runner. Extra Usage und On-Demand-Optionen bleiben ausgeschaltet.

## Folgen

- Authentifizierung liegt beim interaktiv eingerichteten CLI-Profil des Hosts.
- Weniger automatisierte Skalierung in der Cloud — bewusst akzeptiert.
- Tests mocken CLI-Aufrufe oder nutzen lokales Ollama.
