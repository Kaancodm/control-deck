# ADR 0005: data_class Pflicht und Schranken-Logik

## Kontext

Daten duerfen nicht ungewollt in Cloud-Worker gelangen.

## Entscheid

`data_class` ist **Pflicht** ohne Default:

| Wert | Bedeutung |
|------|-----------|
| `local_only` | Nur Ollama (lokal) |
| `cloud_redacted` | Cloud-Worker nach Pseudonymisierung |
| `cloud_ok` | Cloud-Worker mit Rohinhalt erlaubt |

Die Datenschranke vor dem Routing blockiert bei Treffern (`blocked`), unabhaengig von der Kette.

## Folgen

- Jeder Producer muss bewusst klassifizieren.
- Pseudonymisierung und Rueckabbildung werden Pflichtpfade in der Payload-Pipeline.
- Fehlklassifikation ist ein Prozessrisiko — Review bleibt wichtig (ADR 0007).
