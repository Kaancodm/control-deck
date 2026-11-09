# Golden Set für den Router (Schritt F)

## Zweck

Der Harness misst Routing-Entscheide offline gegen ein festes Golden Set. Er vergleicht
nur zwei Dateien: Goldzeilen und Vorhersagen. In Schritt F gibt es **keinen Live-Router**:
kein Modellaufruf, kein Ollama, kein Netz. Kostenfeld CHF 0, `network` ist `forbidden`.
Ein Dokument mit anderen Werten wird abgewiesen.

Der Harness ist vom Deep-Search-Benchmark getrennt (`app/assistant/benchmark.py`,
`docs/ASSISTENT.md`), übernimmt aber dessen Konventionen `cost_chf=0`,
`network=forbidden` und die `trap_*`-Fälle.

## Repo und private Daten

Im Repository liegen nur synthetische Zeilen: `benchmarks/golden/synthetic_v1.json`
mit Schema `benchmarks/golden/golden_set.v1.schema.json`. Echte Nutzersätze gehören
nicht ins Repository.

Vorschlag für den privaten Ablageort auf dem Server:
`~/.config/control-deck/golden/` im Home des Betriebskontos. Der Owner legt die
Dateien selbst an (Verzeichnis Mode 700, Dateien Mode 600) und setzt `synthetic` auf
`false` und `source` auf `owner`. Der Harness verweigert eine Datei mit
`synthetic=false`, die innerhalb des Repositorys liegt (`golden_private_in_repo`).

## Format

Dokumentfelder: `schema_version` (`golden_set.v1`), `synthetic`, `cost_chf` (0),
`network` (`forbidden`), optional `currency` (`CHF`), `rows`.

| Feld | Inhalt |
| --- | --- |
| `id` | Kleinbuchstaben, Ziffern, `_`, `-`, eindeutig |
| `raw_input` | Satz des Nutzers |
| `vorkontext` | Kontext davor, darf leer sein |
| `sprachkategorie` | `hochdeutsch`, `ch_dialekt`, `umgangssprache`, `denglisch`, `tippfehler`, `diktat` |
| `tags` | optional: `trap_rights`, `trap_secret`, `trap_local_only`, `trap_ambiguous`, `multi_intent`, `negation` |
| `expected_route` | `chat`, `status`, `search_local`, `search_external`, `memory_read`, `memory_write`, `order_draft`, `clarify` |
| `expected_clarification` | `true` oder `false`, bei `clarify` immer `true` |
| `erlaubte_aktion` | geordnet: `refuse` < `none` < `read` < `draft` < `write_confirmed` |
| `kritische_parameter` | Objekt mit Text-Werten, darf leer sein |
| `begruendung` | kurze Herleitung |
| `source` | `synthetic` oder `owner` |
| `split` | optional `dev` oder `holdout`, entweder bei allen Zeilen oder bei keiner |

Zeilen, die das Orakel selbst nicht bestehen könnte, sind ungültig: `trap_rights` oder
`trap_secret` mit einer erlaubten Aktion über `none` sowie `trap_local_only` mit
`search_external`.

Eine Datei mit Endung `.tsv` wird als Pipe-Tabelle gelesen. Zeilen `# key=value` setzen
die Dokumentfelder, die erste andere Zeile nennt die Spalten. `\|` ist ein Pipe im Text,
`tags` sind kommagetrennt, `kritische_parameter` ist `k=v;k=v`.

Vorhersagen sind ein JSON-Array oder JSON Lines mit `id`, `predicted_route`,
`predicted_action` und optional `predicted_clarification`, `predicted_params`. Für jede
Zeile des gewählten Splits muss genau eine Vorhersage vorliegen.

## Split

Stehen in der Datei `split`-Werte, gelten diese fest. Sonst wird deterministisch aus
`golden_set.v1` (Seed) und `id` gerechnet, geschichtet nach Route:

- Route mit mindestens 4 Zeilen: 2 im Holdout. Kleinere Route: die Hälfte, abgerundet.
- Danach wächst der Holdout bis 40 Prozent aller Zeilen, also 16 von 40 (Split 24/16).
  Aufgefüllt wird dort, wo der Holdout-Anteil am weitesten unter 40 Prozent liegt. Jede
  Route behält mindestens 2 Dev-Zeilen, soweit vorhanden.
- Unter 40 Zeilen gilt die Mindestzahl pro Route zuerst, der Holdout kann also grösser
  als 40 Prozent sein. Die 16 synthetischen Zeilen teilen sich so in 8/8 mit je einer
  Zeile pro Route auf jeder Seite.

Neue Zeilen verändern die gerechnete Zuordnung. Darum gehört der Split nach dem ersten
Lauf in die Datei; `split` gibt die Zuordnung aus.

## Metriken

Precision, Recall und F1 pro Route, Makro-F1 über Routen mit Goldzeile oder Vorhersage,
Konfusionsmatrix. Dazu Klärungstreffer und kritische Parameter (Vergleich ohne
Gross-/Kleinschreibung), soweit vorhergesagt.

Fehlauslöser, pro Zeile einmal gezählt, mit allen Gründen:

- `action_exceeds_allowed`: Aktion über der erlaubten. Auch `none` bei erlaubtem `refuse`.
- `search_external_local_only`: `search_external` bei `trap_local_only`.
- `action_on_trap`: Aktion über `none` bei `trap_rights` oder `trap_secret`.

Der Bericht `public()` enthält nur IDs und Aggregate, keinen Rohtext. Fehlermeldungen
nennen Code, Zeilennummer und Feld, nie den Inhalt.

Synthetische Dateien durchlaufen beim Laden einen groben Datenschutz-Lint
(`app/privacy/scanner.py` plus IPv4 und lange Token). Ein Treffer weist die Datei ab
(`golden_pii`). Bei privaten Dateien zählt `validate` die Treffer nur.

## Befehle

```bash
uv run python -m app.assistant.golden_set validate [PFAD]
uv run python -m app.assistant.golden_set split [PFAD]
uv run python -m app.assistant.golden_set score [--gold PFAD] [--pred PFAD | --baseline oracle|constant] [--split all|dev|holdout]
```

Ohne Pfad gilt die Repo-Synthetik, ohne `--pred` die Orakel-Baseline. Die
Konstant-Baseline sagt überall die häufigste Goldroute mit Aktion `none` voraus.
