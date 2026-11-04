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

Aktuell sind **vierzehn ausfuehrbare Faelle** vorhanden: acht normale Faelle, vier Fallen und
zwei Grenzfaelle. Die Faelle `005` bis `014` hat der Owner am 2026-11-03 bestaetigt.
Damit ist der Zielbestand von 14 erreicht. Die inhaltlichen Faelle schreibt oder bestaetigt
der Owner selbst. [OWNER_TODO.md](../tests/reference/cases/OWNER_TODO.md) dokumentiert die
urspruenglichen Kennungsreservierungen; Markdown-Platzhalter werden nicht als Tests geladen
und nicht als bestandene Faelle gezaehlt.

Die beiden zuletzt bestaetigten normalen Faelle sind:

| Kennung | Datei | Verhalten |
|---------|-------|-----------|
| `013` | `013-normal-draft-redacted-phone.yaml` | `cloud_redacted`: synthetische Telefonnummer vor dem Cloud-Lauf ersetzen; keine Nummer im Fake-Ergebnis |
| `014` | `014-normal-review-cloud-ok-codex.yaml` | `cloud_ok`: harmlosen Text ueber die Codex-Kette pruefen |

Die Scanner-Unit-Tests mit synthetischen Treffern und harmlosen Texten sind technische
Regressionstests. Sie ersetzen die vom Owner verfassten Referenzfaelle nicht.

## Regeln

- Keine echten Namen, API-Keys oder produktiven Geheimnisse in Faelle schreiben.
  Traps nutzen eindeutige Platzhalter (z. B. `sk-TEST-...`).
- `task.data_class` ist immer explizit gesetzt (kein Default).
- Referenzlaeufe verwenden standardmaessig den `FakeExecutor` (deterministisch).
  Ab Etappe 9 ist zusaetzlich die gebuendelte Ollama-Fake-CLI ausdruecklich waehlbar.
- In Etappe 5 ruft dieser die echte Datenschranke auf, mit einer ausdruecklich leeren
  Blockliste nur fuer den lokalen Testlauf. Worker-Ausfuehrung und Ergebnistext bleiben
  simuliert; der Lauf bestaetigt keine produktive Konfiguration oder Cloud-Anbindung.
- Beide simulierten Executoren geben den vorbereiteten Eingabetext mit `reference-ok`
  zurueck. Nur vorhandene Redaktionsplatzhalter werden zu `[redacted]` vereinfacht;
  unredigierte Werte bleiben sichtbar. `must_not_contain` prueft sowohl die tatsaechlichen
  Versandbytes als auch das Ergebnis. Negativtests umgehen die Redaktion bei `007`/`013`
  beziehungsweise die Datenschranke bei `010` und verlangen einen fehlgeschlagenen Fall.
  `010` bleibt eine blockierende Schluesselfalle, kein erfolgreich redigierter Auftrag.
- Die bestehenden vier YAML-Faelle blieben in Etappe 5 und 6 unveraendert. In Etappe 7
  prueft Fall `004` gemaess Owner-Vorgabe den Ollama-Override: eine vorgeschlagene Cloud-Kette
  bei `local_only` wird lokal ausgefuehrt, sofern die Policy Ollama erlaubt.
- Ein Lauf endet mit Exit-Code `0`, wenn kein Fall `fail` meldet, sonst `1`. Faelle, die
  der gewaehlte Executor auf seiner Lane nicht ausfuehren kann, zaehlen als `skip`
  (nicht als `fail`); sie erscheinen in der Zusammenfassung gesondert.

## Ausfuehrung

```bash
make reference
# oder
uv run python scripts/run_reference.py --cases-dir tests/reference/cases
```

Ausgabe pro Fall: `pass`/`fail`/`skip`, Kategorie, Dauer und bei Abweichung die Gruende.

## Ollama-Fake-CLI

```bash
uv run python scripts/run_reference.py --executor ollama-fake-cli --model fixture-model
```

`fixture-model` ist eine frei gewaehlte technische Simulationskennung, kein echtes Modell.
Der Schalter verlangt eine ausdrueckliche Kennung. Er verwendet dieselbe Datenschranke,
Routing-Entscheidung und Payload-Vorbereitung wie der reine Fake-Executor, archiviert die
exakten Versandbytes privat und uebergibt ausschliesslich den vorbereiteten Text an den
Prozessadapter. Rueckabbildung und Erwartungspruefung erfolgen lokal; die gemessene Zeit
enthaelt den Prozesslauf. Andere geroutete Lanes werden nicht durch Ollama ersetzt: Faelle,
die nach Datenschranke und Routing auf einer Cloud-Lane landen (`cloud_redacted`, `cloud_ok`),
zaehlen in diesem Lauf als `skip`. Fallen, die die Datenschranke blockiert, bleiben `blocked`
und werden normal gegen die Erwartung geprueft.

Die eingabeabhaengige Fake-Ausgabe bestaetigt den technischen Ablauf und die
Redaktionsgrenze, keine semantische Modellqualitaet. Inhaltliche neue Faelle und die
spaetere echte Modellabnahme bleiben beim Owner. Es gibt keinen Live-Executor-Schalter;
die konkreten Grenzen stehen unter [Inbetriebnahme in LANES.md](LANES.md#inbetriebnahme).
