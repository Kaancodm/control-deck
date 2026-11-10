# ADR 0011: Runtime-Profil der Wissens-DB und Ollama-Embedding im API-Prozess

## Kontext

Bis hierhin war die Wissens-DB test-only: `DECK_KB_PROFILE` kannte nur `simulation`, und
`configured_store()` erreichte ausschliesslich die `_test`-Wissens-DB. Die Vektoren stammten
aus einem deterministischen Fake mit acht Dimensionen. Fuer echte Suche braucht die KB ein
lokales Embedding-Modell und einen Weg zur separaten Runtime-Wissens-DB. ADR 0007 (Review
und Wissens-DB) und ADR 0009 (Ollama ueber Loopback-HTTP) bleiben gueltig.

Quelle: Bauplan des Owners (privat, nicht im Repo), Repo-Stand `feat/kb-real-embedding`,
2026-11-10; Methode: Code und Tests in diesem Branch; Status: im Code umgesetzt und mit Stub
geprueft (`proven` fuer das Client-Verhalten), Serverbetrieb `unproven`.

## Entscheid

- `DECK_KB_PROFILE` hat drei Werte. `disabled` (Standard) weist jede KB-Operation mit
  `knowledge_live_profile_unavailable` ab. `simulation` erreicht nur
  `DECK_KB_TEST_DATABASE_URL` mit Namen auf `_test`. `runtime` erreicht nur
  `DECK_KB_DATABASE_URL`. Es gibt keinen Rueckfall zwischen den Profilen.
- `runtime` verlangt `DECK_KB_EMBEDDING_PROVIDER=ollama`. Der Fake kann nie in einen
  Runtime-Bestand schreiben. `simulation` erlaubt Fake (Standard) oder Ollama.
- Die Runtime-URL muss eine PostgreSQL-URL mit `postgresql+psycopg` und Datenbanknamen sein.
  Sie darf weder `controldeck`/`controldeck_test` heissen noch dieselbe Host-Port-DB-Kombination
  wie `DECK_DATABASE_URL` oder `DECK_TEST_DATABASE_URL` treffen (Loopback-Namen gelten als gleich).
- Die Pruefkette ist fail-closed und verbindet nichts, bevor sie durch ist: Profil, dann
  Embedding-Konfiguration, dann DB-Auswahl. Danach folgen bei jeder Operation Secret-Gate,
  Vertragspruefung (`deck_kb.v1`, Modell, Dimension, Spaltentyp) und erst dann Einbetten.
  Fehler nennen nur Grundcodes, nie URL, Modell oder andere Werte.
- Das Embedding laeuft im API-Prozess ueber den bestehenden `OllamaHTTPClient` (`/api/embed`).
  Es erbt Loopback-only, keinen Proxy, keine Weiterleitung, `Connection: close` und die
  begrenzte 503-Wiederholung aus ADR 0009. Kein Pull, kein Warmup, kein periodischer Probe.
- Modell ist `qwen3-embedding:0.6b`; es lag im Owner-A/B gegen `bge-m3` vorne. Fuer
  `purpose=query` stellt nur der Ollama-Adapter eine feste, an diesen Modell-Tag gebundene
  Instruktion voran (`Instruct: Given a web search query, retrieve relevant passages that
  answer the query` + Zeilenumbruch + `Query: `); Dokumente bleiben ohne Praefix, der Fake
  bleibt unveraendert. Mit Instruktion R@1 1.0 und MRR 1.0, ohne 0.964 und 0.982. Quelle:
  Owner-A/B vom 2026-11-10, Ollama `v0.40.14`; Methode: 40 oeffentliche synthetische deutsche
  Dokumente, 28 Anfragen, Server-CPU; Status: `proven` nur fuer diesen kleinen Satz. Details in
  [WISSENS_DB.md](../WISSENS_DB.md#anfrage-instruktion).
- Das Schema bindet die Breite 1024 (`kb_0002`). Die Vertragszeile bindet Modellname und
  Dimension, nicht den Digest. Kein ANN-Index in `kb_0002`; die Suche bleibt exakt.
- `scripts/kb_import.py --simulate` weist das Runtime-Profil mit
  `knowledge_import_simulation_only` ab. Runtime-Schreibzugriffe gehen nur ueber den
  ausdruecklichen Owner-Export aus `done`.

## Folgen

- Mit `OLLAMA_MAX_LOADED_MODELS=1` verdraengt jede Suche und jeder Export das Chat-Modell
  (siehe offene Punkte in [WISSENS_DB.md](../WISSENS_DB.md)). Status: `unproven`, Messung
  im Owner-Serverlauf.
- Restore-Proben eines Runtime-Backups brauchen dieselben Embedding-Variablen wie der Betrieb,
  weil der Wissensvertrag gegen die konfigurierte Spec geprueft wird.
- Backups von vor `kb_0002` lassen sich nur mit altem Code zurueckspielen.
- Es kommt keine neue Abhaengigkeit hinzu. Das Modell ist eine Host-Voraussetzung.

## Freigabe

Das Aktivieren des Runtime-Profils auf dem Server, die Runtime-Migration der Wissens-DB und
jeder Zugriff auf Produktion verlangen ein ausdrueckliches Owner-GO je Schritt. Dieser ADR
erteilt keine Merge- oder Inbetriebnahmefreigabe.
