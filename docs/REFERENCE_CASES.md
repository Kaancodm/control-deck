# Referenz-Faelle (Reference Cases)

Referenz-Faelle pruefen das Verhalten von Control Deck ohne echte Worker-Anbindung.
Sie liegen als YAML unter `tests/reference/cases/` und werden ueber `make reference`
bzw. `scripts/run_reference.py` ausgefuehrt.

## Dateiformat

Dateiname: `NNN-slug.yaml` ( drei Ziffern, Bindestrich, slug in Kleinbuchstaben).

Normatives JSON Schema: `schemas/reference_case.v1.json` (Entwickler-Referenz),
`app/schemas/reference_case.v1.json` (gebundelt; Laufzeit via `importlib.resources`).
Draft 2020-12, `additionalProperties: false` auf allen Objektebenen.

`expect.regex` wird beim Laden kompiliert; ungueltige Syntax fuehrt zu einem Validierungsfehler
(nicht zum Abbruch der gesamten Suite beim Einzelfall-Laden).

| Feld | Beschreibung |
|------|--------------|
| `id` | Eindeutige Kennung, gleich dem Datei-Slug (z. B. `001-normal-summarize-local`) |
| `category` | `normal`, `trap` oder `edge` |
| `description` | Kurzbeschreibung (de-CH) |
| `task` | Auftrag wie `task.v1`, **ohne** `task_id` und `created_at` |
| `expect` | Erwartetes Laufzeitverhalten (siehe unten) |

Beim Laden werden `task_id` und `created_at` erzeugt und der Auftrag zusaetzlich gegen
`task.v1` validiert (JSON Schema + Pydantic).

### expect

| Feld | Typ | Bedeutung |
|------|-----|-----------|
| `status` | `succeeded` \| `blocked` \| `failed` | Erwarteter Ergebnisstatus |
| `must_contain` | string[] | Alle Teilstrings muessen in der Ausgabe vorkommen |
| `must_not_contain` | string[] | Keiner darf in der Ausgabe vorkommen |
| `regex` | string \| null | Optional: Ausgabe muss dem Muster entsprechen |
| `max_chars` | int \| null | Optional: maximale Ausgabelaenge |
| `max_seconds` | number \| null | Optional: maximale Wandzeit |
| `allowed_workers` | Worker[] | Wenn nicht leer: verwendeter Worker muss enthalten sein |

## Mindestmix (Zielbestand)

Fuer eine vollstaendige Referenz-Suite sind mindestens vorgesehen:

| Kategorie | Anzahl | Zweck |
|-----------|--------|--------|
| `normal` | 8 | Typische Happy-Path-Szenarien |
| `trap` | 4 | Datenschutz, Policy, verbotene Inhalte |
| `edge` | 2 | Grenzfaelle (leer, gross, Timeout, …) |

Aktuell sind **vier ausfuehrbare Faelle** vorhanden: ein normaler Fall, zwei Fallen und ein
Grenzfall. Der Zielbestand von 14 ist noch nicht erreicht. Die inhaltlichen Faelle schreibt
der Owner selbst. [OWNER_TODO.md](../tests/reference/cases/OWNER_TODO.md) reserviert die
weiteren Kennungen ohne Auftragsinhalt oder erwartetes Verhalten; diese Platzhalter werden
nicht als Tests geladen und nicht als bestandene Faelle gezaehlt.

Die Scanner-Unit-Tests mit synthetischen Treffern und harmlosen Texten sind technische
Regressionstests. Sie ersetzen die vom Owner verfassten Referenzfaelle nicht.

## Regeln

- Keine echten Namen, API-Keys oder produktiven Geheimnisse in Faelle schreiben.
  Traps nutzen eindeutige Platzhalter (z. B. `sk-TEST-...`).
- `task.data_class` ist immer explizit gesetzt (kein Default).
- Referenzlaeufe verwenden bis zur Worker-Etappe den `FakeExecutor` (deterministisch).
- In Etappe 5 ruft dieser die echte Datenschranke auf, mit einer ausdruecklich leeren
  Blockliste nur fuer den lokalen Testlauf. Worker-Ausfuehrung und Ergebnistext bleiben
  simuliert; der Lauf bestaetigt keine produktive Konfiguration oder Cloud-Anbindung.
- Die bestehenden vier YAML-Faelle blieben in Etappe 5 und 6 unveraendert. In Etappe 7
  prueft Fall `004` gemaess Owner-Vorgabe den Ollama-Override: eine vorgeschlagene Cloud-Kette
  bei `local_only` wird lokal ausgefuehrt, sofern die Policy Ollama erlaubt.
- Ein Lauf endet mit Exit-Code `0`, wenn alle Faelle `pass` melden, sonst `1`.

## Ausfuehrung

```bash
make reference
# oder
uv run python scripts/run_reference.py --cases-dir tests/reference/cases
```

Ausgabe pro Fall: `pass`/`fail`, Kategorie, Dauer und bei Abweichung die Gruende.
