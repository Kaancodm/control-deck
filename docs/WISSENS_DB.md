# Wissens-DB: Etappe 14

Status: Kernvertrag, Kontext-/Export-Anbindung und Oberflaeche sind umgesetzt. Der Branch
ist auf den finalen Etappe-13-Head rebased. Gesamtvalidierung und CI werden im Draft-PR #29
und in [STAND.md](STAND.md) dokumentiert. Externes Review und Inbetriebnahme bleiben offen.

Der vorgeschlagene Vertrag `deck_kb.v1` wird ausschliesslich gegen isolierte Testdatenbanken
entwickelt. Er ist kein Nachweis fuer die Kompatibilitaet einer bestehenden Wissens-DB.
Jeder Eintrag und jeder Export braucht eine ausdrueckliche Datenklasse ohne Default.

## Testvertrag und Suche

Die eigene Alembic-Kette `alembic_kb.ini` legt das Schema `deck_kb` nur in der ausdruecklich
gewaehlten Datenbank an (siehe [Migrationsziel](#migrationsziel)). Entwickelt und geprueft wird
gegen `_test`-Datenbanken. Dokumente und Chunks sind unveraenderlich; ein Dokument besitzt eine
ausdrueckliche Datenklasse, die fuer alle seine Chunks gilt. SHA-256 bindet die exakten
UTF-8-Bytes. Vor jedem Zugriff werden Vertragsversion, Embedding-Modell, Dimension,
Pflichtklasse ohne DB-Default sowie erforderliche Tabellenrechte geprueft.

| Auftrag | Zulaessige Wissensklassen |
|---|---|
| `local_only` | alle drei Klassen |
| `cloud_redacted` | `cloud_redacted`, `cloud_ok` |
| `cloud_ok` | nur `cloud_ok` |

Der SQL-Filter wirkt vor der Vektorrangfolge. Explizite Referenzen sind kanonische
Dokument-UUIDs. Fehlende oder unzulaessige Referenzen werden abgewiesen, niemals still
ausgelassen. Maximal 16 Referenzen und 96 KB Kontext; Dokumente sind auf 24 KB begrenzt.
Der Standard-Embedder ist der deterministische Fake `deck-fake-embedding-v2` mit 1024
Dimensionen; er prueft den Datenfluss, keine semantische Suchqualitaet. Echte Vektoren liefert
nur ein ausdruecklich konfiguriertes lokales Ollama-Modell (siehe
[Embedding-Vertrag](#embedding-vertrag)). Es gibt keine versteckten Modellaufrufe oder Downloads.

## Freigabe und Versand

Kontext wird vor dem Speichern des Auftrags aufgeloest und durch die Datenschranke geprueft.
Geheimnistreffer verhindern sowohl Auftrag als auch Snapshot. Hostseitige
`task_context_snapshots` speichern unveraenderliche Bytes, Klasse und Digest. Der Owner sieht
die ausgewaehlten Inhalte vor der Freigabe; das `task.approve`-Event bindet den vollstaendigen
Digest-Satz. Jeder Versuch prueft diesen Satz und fuehrt dieselben Bytes erneut durch Scanner,
Redaction und Versandarchiv. Ein spaeterer KB-Ausfall aendert die freigegebenen Inhalte nicht.
Unter Task-/Lease-Locks erfolgen keine externen KB-Abfragen oder Embedding-Aufrufe.

Snapshots sind lokale Task-Artefakte. Etappe 16 darf sie nur fuer alte terminale Tasks ohne
Lease entfernen; Wissensdokumente, Ergebnisse, Audit und Exportbelege bleiben erhalten.

## Expliziter Export

`POST /api/v1/tasks/{id}/export` verlangt Owner-Authentifizierung und `data_class` im Body.
Export ist ausschliesslich aus `done` moeglich; `review` genuegt nicht. Die Klasse darf
gegenueber dem Auftrag nur gleich oder strenger sein. Vor dem Export prueft die Schranke
den Ergebnistext erneut. Dateiausgaben sind bis zu einer eigenen geprueften Anbindung gesperrt.

`knowledge_exports` speichert zuerst den ausdruecklichen Owner-Entscheid. Erst nach dem
Commit schreibt der Adapter ausserhalb der Host-Transaktion in die KB. Ein stabiler
Dokumentschluessel verhindert Duplikate; anschliessend wird `kb.export` einmalig auditiert.
Bei einem Ausfall dazwischen setzt derselbe Owner-Aufruf den offenen Export fort. Eine
abweichende Klasse oder Quelle wird als Konflikt abgewiesen. Es gibt keinen automatischen
Export, auch nicht bei automatisch erreichtem `done`.

## Profile

| `DECK_KB_PROFILE` | Datenbank | Embedding |
|---|---|---|
| `disabled` (Standard, auch leer) | keine; jede Operation endet mit `knowledge_live_profile_unavailable` | keines |
| `simulation` | nur `DECK_KB_TEST_DATABASE_URL`, Name auf `_test` | `fake` (Standard) oder `ollama` |
| `runtime` | nur `DECK_KB_DATABASE_URL` | nur `ollama` |

Andere Werte werden abgewiesen. `simulation` liest `DECK_KB_DATABASE_URL` nie, `runtime` liest
`DECK_KB_TEST_DATABASE_URL` nie. Beide Profile verweigern die zentrale DB als Wissens-DB: Namen
`controldeck` und `controldeck_test` sowie dieselbe Host-Port-DB-Kombination wie
`DECK_DATABASE_URL` oder `DECK_TEST_DATABASE_URL`. Die Pruefreihenfolge ist Profil,
Embedding-Konfiguration, Datenbank; vorher entsteht keine Verbindung.

| Code | Ursache |
|---|---|
| `knowledge_live_profile_unavailable` | Profil `disabled` oder unbekannt |
| `knowledge_embedding_configuration_invalid` | Embedding-Variablen ungueltig, Fake im Runtime-Profil |
| `knowledge_runtime_database_required` | `runtime` ohne `DECK_KB_DATABASE_URL` |
| `knowledge_runtime_database_invalid` | Runtime-URL nicht `postgresql+psycopg` mit DB-Namen |
| `knowledge_test_database_required` | `simulation` ohne gueltige `_test`-URL |
| `knowledge_database_must_be_separate` | Wissens-URL trifft die zentrale DB |
| `knowledge_central_database_invalid` | zentrale URL zum Vergleich nicht lesbar |
| `knowledge_import_simulation_only` | `scripts/kb_import.py` ausserhalb von `simulation` |

API und Oberflaeche melden diese Codes mit 503; Eingabefehler bleiben 422.

## Embedding-Vertrag

| Variable | Standard | Regel |
|---|---|---|
| `DECK_KB_EMBEDDING_PROVIDER` | `fake` | `fake` oder `ollama`; `runtime` verlangt `ollama` |
| `DECK_KB_EMBEDDING_MODEL` | leer | Pflicht bei `ollama`, verboten bei `fake`; expliziter Tag, keine Cloud-Kennung, kein `:local` |
| `DECK_KB_EMBEDDING_DIMENSION` | leer | Pflicht bei `ollama`, verboten bei `fake`; genau `1024` |
| `DECK_KB_EMBEDDING_TIMEOUT_S` | `60` | 1 bis 85 Sekunden, Gesamtbudget fuer Verdraengen, Laden und Rechnen |
| `DECK_KB_EMBEDDING_KEEP_ALIVE` | `server` | Grammatik des Lane-`keep_alive`; `server` sendet kein Feld |
| `DECK_OLLAMA_URL` | leer | Pflicht bei `ollama`; Regel aus ADR 0009 (`http://127.0.0.1:<port>` oder `http://[::1]:<port>`) |

Das vorgesehene Modell ist `qwen3-embedding:0.6b` mit 1024 Dimensionen (Quelle: Bauplan des
Owners, 2026-11-10). Im A/B-Vergleich gegen `bge-m3` lag es vorne (siehe
[Anfrage-Instruktion](#anfrage-instruktion)). Die Migration `kb_0002` setzt die Spalte auf
`vector(1024)` und die Vertragszeile auf (`deck_kb.v1`, `qwen3-embedding:0.6b`, 1024). Sie
laeuft nur auf leeren Tabellen mit exakt der alten Vertragszeile (`deck-fake-embedding-v1`, 8)
und laesst die Unveraenderlichkeits-Trigger unberuehrt. Tests setzen die Fake-Vertragszeile
ueber `tests/support/kb.py`, das nur `_test`-DBs annimmt.

Ein Dokument wird mit allen Chunks (hoechstens 16) in einem `/api/embed`-Aufruf eingebettet,
eine Suche mit einem weiteren. Ein bereits vorhandenes Dokument wird nicht erneut eingebettet.
Die Antwort muss das Modell spiegeln, darf keine Remote- oder Fehlerfelder tragen und liefert
je Eingabe genau 1024 endliche Zahlen ohne Nullvektor. Es gibt nie einen Rueckfall auf den Fake.

### Anfrage-Instruktion

Qwen3-Embedding ist asymmetrisch. Fuer `purpose=query` stellt der Ollama-Adapter der Anfrage
eine feste Instruktion voran; Dokumente bleiben ohne Praefix. Die Konstante haengt am exakten
Modell-Tag `qwen3-embedding:0.6b` (`app/kb/ollama_embeddings.py`, `QUERY_INSTRUCTIONS`); jedes
andere Modell und der Fake erhalten keinen Praefix. Wortlaut (mit Zeilenumbruch vor `Query:`):

```text
Instruct: Given a web search query, retrieve relevant passages that answer the query
Query: <Anfrage>
```

Die Groessengrenze gilt fuer den Anfragetext, nicht fuer den Praefix. Die Logzeile nennt weder
Text noch Praefix. Weil nur die Anfrageseite betroffen ist, bleiben gespeicherte Dokumentvektoren
gueltig; ein Wechsel der Instruktion verlangt kein Neu-Einbetten, veraendert aber die Rangfolge.

| Variante | R@1 | MRR |
|---|---|---|
| `qwen3-embedding:0.6b` mit Anfrage-Instruktion | 1.0 | 1.0 |
| `qwen3-embedding:0.6b` ohne Instruktion | 0.964 | 0.982 |

Quelle: Owner-A/B vom 2026-11-10 am Server, Ollama `v0.40.14`, gegen `bge-m3`; Methode:
40 oeffentliche synthetische deutsche Dokumente, 28 Anfragen, Server-CPU. Status: `proven`
nur fuer diesen kleinen oeffentlichen Satz, nicht fuer reale Bestaende.

| Code | Ursache |
|---|---|
| `embedding_busy` | 503 Warteschlange voll nach den begrenzten Wiederholungen |
| `embedding_timeout` | Zeitbudget erschoepft |
| `embedding_unavailable` | Verbindungsfehler oder anderer HTTP-Status |
| `embedding_model_missing` | 404, Modell nicht lokal vorhanden |
| `embedding_input_too_long` | 400; bei `truncate: false` meist Eingabe laenger als der Kontext (API/UI: 422) |
| `embedding_malformed` | kaputtes JSON, Uebergroesse, falsche Anzahl, Modell-Echo, Remote-Felder |
| `embedding_dimension_mismatch` | Vektor nicht 1024 lang |
| `embedding_invalid` | nicht endlich, kein Zahlwert oder Nullvektor |
| `knowledge_contract_mismatch` | Vertragszeile oder Spaltentyp passt nicht zur konfigurierten Spec |

Jeder Aufruf schreibt eine JSON-Logzeile `kb_embedding_call` mit Zweck, Anzahl, Dimension,
Dauer, Phase, Grund und Status, nie Text, Vektorwerte, URL oder Server-Fehlertext.

Index: keiner. Die Suche rangiert `min(Distanz)` je Dokument nach dem Klassenfilter und ist
exakt. Ein HNSW-Index ist als spaetere Revision vorgesehen, sobald ein Messwert es verlangt
(Vorschlag aus dem Bauplan: ueber 50'000 Chunks oder p95 ueber 300 ms; Status: `unproven`).

## Simulation

Aktivierung nur mit `DECK_KB_PROFILE=simulation` und `DECK_KB_TEST_DATABASE_URL` auf eine
isolierte `_test`-DB. Der Standard ist deaktiviert. `POST /api/v1/knowledge/search` ist
Owner-geschuetzt und verlangt eine ausdrueckliche Datenklasse. Der lokale Import verwendet
`scripts/kb_import.py <inbox-relativer-pfad> --title <titel> --data-class <klasse> --simulate`;
Dateien durchlaufen dieselben sicheren Inbox-Lesepruefungen und den Scanner.

Pruefungen: `uv run alembic -c alembic_kb.ini upgrade head`, danach
`uv run alembic -c alembic_kb.ini check`. CI verwendet pgvector und eine separate KB-Test-DB.
Der Python-Adapter benoetigt keine neue Dependency; pgvector ist eine Test-DB-Erweiterung.

## Migrationsziel

Die KB-Kette waehlt ihre Datenbank vor jedem Verbindungsaufbau:

| Aufruf | Gelesene Variable | Befehle |
|---|---|---|
| `-x target=test` oder ohne Ziel | `DECK_KB_TEST_DATABASE_URL`, Name endet auf `_test` | alle |
| `-x target=runtime` | `DECK_KB_DATABASE_URL` | nur `upgrade`, `check`, `current` |

Ist `DECK_KB_DATABASE_URL` gesetzt, muss das Ziel ausdruecklich genannt werden. Abgewiesen werden
unbekannte oder mehrfache Ziele, ungueltige URLs und die zentrale DB als KB-Ziel.
`DECK_ALEMBIC_TARGET` gilt fuer diese Kette nicht. Fehler nennen nur einen Grundcode, nie die URL.

Die URL gehoert in die private Konfiguration, nie ins Repo:

```bash
uv run alembic -c alembic_kb.ini -x target=runtime current
uv run alembic -c alembic_kb.ini -x target=runtime upgrade head
uv run alembic -c alembic_kb.ini -x target=runtime check
```

Die Revision `kb_0001` bleibt unveraendert. Ihr Schutz akzeptiert die Runtime-URL nur innerhalb
dieser Zielwahl. Die Zielwahl selbst aktiviert kein Runtime-Profil; dafuer braucht es
`DECK_KB_PROFILE=runtime` (siehe [Profile](#profile)). Eine bestehende fremde Wissens-DB wird
nicht automatisch angepasst. `make update` migriert nur die zentrale Kette; die KB-Migration
ist ein eigener Schritt (siehe [OPERATIONS.md](OPERATIONS.md#rollout-echtes-embedding-der-wissens-db)).

## Inbetriebnahme

Der Code fuer das Runtime-Profil ist vorhanden, aber nicht ausgerollt. Aktivierung, Runtime-
Migration und jeder Prod-Zugriff erfolgen nur durch den Owner mit ausdruecklichem GO je Schritt,
in der Reihenfolge aus [OPERATIONS.md](OPERATIONS.md#rollout-echtes-embedding-der-wissens-db).
Es gibt keine API-Keys, Anbieteraufrufe, Serveraenderungen oder automatischen Modell-Downloads.

Verifikation nach dem Rollout ohne Schreiben: Suche ueber `/ui/knowledge` mit synthetischer
Anfrage ergibt eine leere Trefferliste; das durchlaeuft Konfiguration, Vertragspruefung, echtes
Einbetten und SQL. Ein Testdokument wird nur in einer isolierten `_test`-Kopie eingespielt,
weil Dokumente in Prod unveraenderlich sind.

Belegt: `/api/embed` am gepinnten Ollama `v0.40.14` nimmt die Kennung mit Quellensuffix
`:local` an und liefert 1024-dimensionale Vektoren (Quelle: Owner-Messung vom 2026-11-10;
Methode: `curl` auf `/api/embed` am Server; Status: `proven` fuer diese Version). Siehe
[LANES.md](LANES.md#embedding-vertrag-apiembed). Nach einem Ollama-Update ist das erneut zu
pruefen; scheitert es, endet jede Operation mit `embedding_model_missing`, ohne Schreiben.

Offen (Status `unproven`, Pruefung im Owner-Serverlauf):

- Modellwechsel zwischen Chat- und Embedding-Modell bei einem geladenen Modell: jede Suche und
  jeder Export verdraengt das Chat-Modell; eine laufende Generierung kann zu `embedding_timeout`
  fuehren.
- Die Vertragszeile bindet Modellname und Dimension, nicht den Digest.
- Chunking (1500 Zeichen ohne Ueberlappung) und eine `BEFORE TRUNCATE`-Sperre sind vor dem
  ersten echten Import zu entscheiden. Die Anfrage-Instruktion ist entschieden (siehe oben).

## Oberflaeche

Unter `/ui/knowledge` sucht der eingeloggte Owner mit ausdruecklicher Datenklasse. Eine
Auswahl fuellt einen neuen Auftragsentwurf; sie gibt weder einen Auftrag frei noch exportiert
sie Wissen. Die Detailseite zeigt die unveraenderlichen Kontexte vor der Start-Freigabe.
Der Export aus `done` verlangt ein eigenes Formular mit Datenklasse, Bestaetigung und CSRF.
Die Oberflaeche verwendet die bestehenden CSS-Variablen und Partials; das spaetere Design
bleibt separat. Browserpruefungen bestehen bei 390 und 1024 Pixeln, am Desktop ohne JavaScript.
