# Referenz-Fälle (Reference Cases)

Referenz-Fälle prüfen das Verhalten von Control Deck standardmässig in Simulation.
Sie liegen als YAML unter `tests/reference/cases/` und werden über `make reference`
bzw. `scripts/run_reference.py` ausgeführt.

## Dateiformat

Dateiname: `NNN-slug.yaml` ( drei Ziffern, Bindestrich, slug in Kleinbuchstaben).

Normatives JSON Schema: `schemas/reference_case.v1.json` (Entwickler-Referenz),
`app/schemas/reference_case.v1.json` (gebundelt; Laufzeit via `importlib.resources`).
Draft 2020-12, `additionalProperties: false` auf allen Objektebenen.

`expect.regex` wird beim Laden kompiliert; ungültige Syntax führt zu einem Validierungsfehler
(nicht zum Abbruch der gesamten Suite beim Einzelfall-Laden).

| Feld | Beschreibung |
|------|--------------|
| `id` | Eindeutige Kennung, gleich dem Datei-Slug (z. B. `001-normal-summarize-local`) |
| `category` | `normal`, `trap` oder `edge` |
| `description` | Kurzbeschreibung (de-CH) |
| `task` | Auftrag wie `task.v1`, **ohne** `task_id` und `created_at` |
| `expect` | Erwartetes Laufzeitverhalten (siehe unten) |

Beim Laden werden `task_id` und `created_at` erzeugt und der Auftrag zusätzlich gegen
`task.v1` validiert (JSON Schema + Pydantic).

### expect

| Feld | Typ | Bedeutung |
|------|-----|-----------|
| `status` | `succeeded` \| `blocked` \| `failed` | Erwarteter Ergebnisstatus |
| `must_contain` | string[] | Alle Teilstrings müssen in der Ausgabe vorkommen |
| `must_not_contain` | string[] | Keiner darf in der Ausgabe vorkommen |
| `regex` | string \| null | Optional: Ausgabe muss dem Muster entsprechen |
| `max_chars` | int \| null | Optional: maximale Ausgabelänge |
| `max_seconds` | number \| null | Optional: maximale Wandzeit |
| `allowed_workers` | Worker[] | Wenn nicht leer: verwendeter Worker muss enthalten sein |

## Mindestmix (Zielbestand)

Für eine vollständige Referenz-Suite sind mindestens vorgesehen:

| Kategorie | Anzahl | Zweck |
|-----------|--------|--------|
| `normal` | 8 | Typische Happy-Path-Szenarien |
| `trap` | 4 | Datenschutz, Policy, verbotene Inhalte |
| `edge` | 2 | Grenzfälle (leer, gross, Timeout, …) |

Aktuell sind **vierzehn ausführbare Fälle** vorhanden: acht normale Fälle, vier Fallen und
zwei Grenzfälle. Die Fälle `005` bis `014` hat der Owner am 2026-11-03 bestätigt.
Damit ist der Zielbestand von 14 erreicht. Die inhaltlichen Fälle schreibt oder bestätigt
der Owner selbst. [OWNER_TODO.md](../tests/reference/cases/OWNER_TODO.md) dokumentiert die
ursprünglichen Kennungsreservierungen; Markdown-Platzhalter werden nicht als Tests geladen
und nicht als bestandene Fälle gezählt.

Die beiden zuletzt bestätigten normalen Fälle sind:

| Kennung | Datei | Verhalten |
|---------|-------|-----------|
| `013` | `013-normal-draft-redacted-phone.yaml` | `cloud_redacted`: synthetische Telefonnummer vor dem Cloud-Lauf ersetzen; keine Nummer im Fake-Ergebnis |
| `014` | `014-normal-review-cloud-ok-codex.yaml` | `cloud_ok`: harmlosen Text über die Codex-Kette prüfen |

Die Scanner-Unit-Tests mit synthetischen Treffern und harmlosen Texten sind technische
Regressionstests. Sie ersetzen die vom Owner verfassten Referenzfälle nicht.

## Regeln

- Keine echten Namen, API-Keys oder produktiven Geheimnisse in Fälle schreiben.
  Traps nutzen eindeutige Platzhalter (z. B. `sk-TEST-...`).
- `task.data_class` ist immer explizit gesetzt (kein Default).
- Referenzläufe verwenden standardmässig den `FakeExecutor` (deterministisch).
  Ab Etappe 9 ist zusätzlich die gebündelte Ollama-Fake-CLI ausdrücklich wählbar.
- In Etappe 5 ruft dieser die echte Datenschranke auf, mit einer ausdrücklich leeren
  Blockliste nur für den lokalen Testlauf. Worker-Ausführung und Ergebnistext bleiben
  simuliert; der Lauf bestätigt keine produktive Konfiguration oder Cloud-Anbindung.
- Beide simulierten Executoren geben den vorbereiteten Eingabetext mit `reference-ok`
  zurück. Nur vorhandene Redaktionsplatzhalter werden zu `[redacted]` vereinfacht;
  unredigierte Werte bleiben sichtbar. `must_not_contain` prüft sowohl die tatsächlichen
  Versandbytes als auch das Ergebnis. Negativtests umgehen die Redaktion bei `007`/`013`
  beziehungsweise die Datenschranke bei `010` und verlangen einen fehlgeschlagenen Fall.
  `010` bleibt eine blockierende Schlüsselfalle, kein erfolgreich redigierter Auftrag.
- Die bestehenden vier YAML-Fälle blieben in Etappe 5 und 6 unverändert. In Etappe 7
  prüft Fall `004` gemäss Owner-Vorgabe den Ollama-Override: eine vorgeschlagene Cloud-Kette
  bei `local_only` wird lokal ausgeführt, sofern die Policy Ollama erlaubt.
- Ein Lauf endet mit Exit-Code `0`, wenn kein Fall `fail` meldet, sonst `1`. Fälle, die
  der gewählte Executor auf seiner Lane nicht ausführen kann, zählen als `skip`
  (nicht als `fail`); sie erscheinen in der Zusammenfassung gesondert.

## Ausführung

```bash
make reference
# oder
uv run python scripts/run_reference.py --cases-dir tests/reference/cases
```

Ausgabe pro Fall: `pass`/`fail`/`skip`, Kategorie, Dauer und bei Abweichung die Gründe.

## Ollama-Fake-CLI

```bash
uv run python scripts/run_reference.py --executor ollama-fake-cli --model fixture-model
```

`fixture-model` ist eine frei gewählte technische Simulationskennung, kein echtes Modell.
Der Schalter verlangt eine ausdrückliche Kennung. Er verwendet dieselbe Datenschranke,
Routing-Entscheidung und Payload-Vorbereitung wie der reine Fake-Executor, archiviert die
exakten Versandbytes privat und übergibt ausschliesslich den vorbereiteten Text an den
Prozessadapter. Rückabbildung und Erwartungsprüfung erfolgen lokal; die gemessene Zeit
enthält den Prozesslauf. Andere geroutete Lanes werden nicht durch Ollama ersetzt: Fälle,
die nach Datenschranke und Routing auf einer Cloud-Lane landen (`cloud_redacted`, `cloud_ok`),
zählen in diesem Lauf als `skip`. Fallen, die die Datenschranke blockiert, bleiben `blocked`
und werden normal gegen die Erwartung geprüft.

Die eingabeabhängige Fake-Ausgabe bestätigt den technischen Ablauf und die
Redaktionsgrenze, keine semantische Modellqualität. Inhaltliche neue Fälle und die
spätere echte Modellabnahme bleiben beim Owner. Die Transportprüfungen aus #37
bleiben auch bei separat erfasster Live-Provenienz erhalten.

## Ollama live

Nach den Owner-Prüfungen unter [Inbetriebnahme in LANES.md](LANES.md#inbetriebnahme)
kann derselbe Runner mit `--executor ollama-live --model <konfiguriertes-modell>` laufen.
Er verlangt die Runner-Umgebung mit `DECK_LIVE_ENABLED=1` oder `true` und einer gültigen
`DECK_OLLAMA_URL`. Das Modell muss mit explizitem Tag in `lanes.ollama.models` stehen.
Routing und Datenschranke verwenden dann die echte lokale Konfiguration einschliesslich
der externen Blockliste. Vor der Ausführung muss die Vorprüfung alle konfigurierten
Modelle bestätigen. Die Transportgrenzen sind dieselben wie beim Host-Runner.

Die Zusammenfassung nennt unter `Live evidence` ausschliesslich bestandene Fälle mit
erfolgreicher Live-Modellausführung. Simulationen und schon vor dem Modell blockierte
Fallen erhöhen diesen Zähler nicht. Die bisherigen `pass`/`fail`/`skip`-Zähler bleiben
als technische Testauswertung erhalten; ein grünes Simulationsresultat ist kein
Live-Nachweis. `reference_case.v1` bleibt unverändert.

Die automatisierten Tests verwenden ausschliesslich einen lokalen HTTP-Stub. Ein
echter Referenzlauf und die Beurteilung der semantischen Modellqualität bleiben offen.
