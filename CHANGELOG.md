# Änderungen

## Unveröffentlicht

Die Einträge beschreiben den gestapelten Draft-Stand. Externes Review, Owner-Merge,
Gesamt-Abnahme und Inbetriebnahme bleiben offen. Projektversion und Tags bleiben unverändert.

### Wissens-DB: echtes Embedding und Runtime-Profil (Draft)

- Embedding-Spec als einzige Quelle: Schema-Breite 1024, Fake `deck-fake-embedding-v2` mit
  1024 Dimensionen bleibt Standard; `deck-fake-embedding-v1` (8) bleibt historisch in `kb_0001`.
- Migration `kb_0002`: `vector(8)` auf `vector(1024)` und Vertragszeile
  (`deck_kb.v1`, `qwen3-embedding:0.6b`, 1024), nur auf leeren Tabellen mit erwarteter alter
  Vertragszeile; Trigger unverändert, kein Index.
- `OllamaHTTPClient.embed` (`/api/embed`, nicht streamend, `truncate: false`, Antwort bis 2 MiB)
  und `OllamaEmbeddings` mit strikter Antwortprüfung und festen Fehlercodes; nie Rückfall auf
  den Fake. Ein Dokument ist ein Aufruf, wiederholte Exporte betten nicht erneut ein.
- Restore prüft den Wissensvertrag gegen die konfigurierte Spec.
- Neues Profil `DECK_KB_PROFILE=runtime`: nur `DECK_KB_DATABASE_URL`, getrennt von der
  zentralen DB, nur mit `ollama`. Prüfreihenfolge Profil, Embedding, Datenbank vor jeder
  Verbindung; `simulation` bleibt auf der `_test`-DB. `scripts/kb_import.py` weist `runtime` ab.
- Doku: Embedding-Vertrag und Profile in `docs/WISSENS_DB.md`, Rollout mit eigenem
  KB-Migrationsschritt in `docs/OPERATIONS.md`, `/api/embed`-Quellenvertrag in `docs/LANES.md`,
  ADR 0011. Keine neue Abhängigkeit. Nicht ausgerollt; Aktivierung nur mit Owner-GO.

### UI-Angleichung an das Command-Center-Mockup (PR 1, Draft)

- Neue Shell: Seitenleiste ab 861 Pixeln, darunter fixe Leiste mit fünf Zielen und «Menü»
  für den Rest; Anmeldeseite ohne Navigation. Token-Namen bleiben, Werte folgen der
  Mockup-Palette; Systemschrift, weil die CSP kein `font-src` hat.
- `GET /` leitet fest auf `/ui`, `/ui` und der Login auf die neue Übersicht `/ui/overview`.
  Kein `next`-Parameter, kein neuer schreibender Endpunkt, keine neue Abhängigkeit. Dieser
  PR legt keine Alembic-Revision an; der Kopf ist nach dem Merge von M0/M1
  `20261110_0064`. Steht eine DB noch auf `20261108_0063`, braucht der Deploy das Upgrade
  aus M0/M1.
- Die Übersicht zeigt nur Daten mit Quelle (Freigaben, Claims, Lanes, letztes Backup- und
  Restore-Ereignis, Audit) und Schalter nur als Zustand. Beispielzahlen, Namen, Hoster,
  PR-Nummern, Modellname, Backup-Uhrzeit und 3D-Patch des Mockups entfallen.
- Assistent zeigt Loopback, Web und Routine-Wake als Zustandschips; Feldnamen und
  Formularreihenfolge bleiben unverändert.
- Fix-Runde 1: Telefon zuerst. Einzeilige deckende Kopfzeile mit eigenem Stapelkontext
  (Menü liegt oben), Runner-Zustand als Pill, deckende Leiste, die bei Fokus im Feld
  verschwindet. Übersicht mit Status-Leiste direkt unter dem Titel, genau einer gefüllten
  Hauptaktion, offenen Punkten nur aus Warnungen und eingeklappten Hinweisen, Assistent,
  Meldungen und Audit. Fehlende Lane-Daten heissen «nicht verfügbar», agy erscheint als
  vierte, noch nicht angebundene Lane (Owner-Entscheid), ein Datenbankfehler ergibt 503
  statt eines unbehandelten Fehlers. Assistentenseite beginnt mit dem Dialog; der Kanal
  trägt keinen Personennamen mehr. Neuer Chromium-Test für Falz, Menü und Tastatur.
- Fix-Runde 2: Der 503-Pfad der Übersicht umfasst alle Lesezugriffe nach der
  Session-Prüfung, auch Runner-Steuerung und Lane-Health, und schreibt eine Logzeile nur mit
  dem Fehlertyp. Fokus ist nur der älteste wartende Auftrag. Der agy-Punkt ist ein Hinweis
  statt Warnung, der Lane-Titel heisst nur «agy». Die Leiste erscheint nach Fokus im Feld
  erst verzögert wieder, damit kein Klick verloren geht. Der Tab «Meldungen» öffnet den
  Abschnitt serverseitig ohne JS. Der Projekt-Stand steht am Seitenende neben «Schema»,
  statisch markiert und ohne PR-Nummern. Lane-Warnungen nennen Lane und Grund, die
  Backup-Kachel zeigt am Telefon das Datum. Schalterzustände der Übersicht und der
  Aktivierungstext teilen eine Quelle; Alpha-Töne hängen per `color-mix` an den Tokens.
- Fix-Runde 3: Mit «Bewegung reduzieren» bleibt die verzögerte Rückkehr der Leiste
  erhalten (Klicktest auch mit `reduced_motion="reduce"`). Browser ohne `color-mix()`
  erhalten feste Rückfallwerte für Linien, Rahmen und Netzlinien. Die Assistentenseite
  zeigt die Fakten wie die Übersicht unter «Loopback-Dialog, wenn eingeschaltet». Die
  WebKit-Vorschau überspringt nur bei fehlendem Browser.
- Die Übersicht zeigt zusätzlich lesend den Schema-Stand aus `alembic_version`, einen
  statischen, datierten Projekt-Stand und Fakten zu Assistent und Meldungen aus
  Code-Konstanten (Kontextbudget, Server-Sent Events, Ollama-503, Wake-Sendeversuche).
- Tests für Redirects, CSP-Pin, kein Inline-Code, Viewport, Kontrast, keine erfundenen
  Zahlen, keine Konfigurationswerte im HTML, Übersicht gegen PostgreSQL, Chromium-Shell
  bei 320/390/1024 Pixeln und optionale WebKit-Vorschau.
### Einheits-Chat M0/M1 (Draft)

- Vertrag `channel_turn.v1` und Migration `20261110_0064`: Kanäle, Nachrichten in
  Quarantäne, Owner-Freigabe und taskfreie Audit-Zeilen. `events.task_id` und
  `events.to_status` sind nullable; ein CHECK trennt Task-Zeilen von
  `assistant.*`- und `memory.*`-Zeilen. Ein Downgrade verweigert, solange solche
  Zeilen existieren, und löscht nichts.
- `ingest_turn` speichert neue Nachrichten als `quarantined`. Freigabe nur mit
  authentisierter UI-Session und sha256 der gezeigten Bytes.
  `load_memory_context` liest freigegebene Nachrichten und Fakten innerhalb der
  Ziel-Klassen. Der Live-Dialog ist noch nicht angeschlossen (M1b, #77, nach dem UI-PR,
  vor G2).
- Refactor ohne Verhaltensänderung: `app/privacy/gate.py` zieht das Sperr-Prädikat
  in `findings_block`, das `inspect_task` und das Gedächtnis teilen. Die bestehenden
  Gate-Tests bleiben grün. Neu ist `non_local_texts_blocked` für den Versand an
  Cloud-Ziele: ohne konfigurierte Blockliste sind Cloud-Klassen gesperrt (wie beim
  Auftrags-Gate).
- Entschiedene Zeilen sind per Trigger eingefroren; kein hartes Löschen.
- Owner-Entscheide vom 2026-11-10 (zweite Runde):
  - Ein Datenschutz-Befund bei `cloud_ok`/`cloud_redacted` weist nicht mehr ab
    (`privacy_block` als Abweisung entfällt). Die Zeile liegt mit vollem Text in der
    Quarantäne und trägt die unveränderliche Spalte `privacy_hold`
    (`privacy_block` oder `blocklist_unavailable`). Gilt für Turns, Deck-Antworten
    und `propose_fact`; ein Faktum aus einer markierten Nachricht erbt die
    Markierung. Geheimnisse bleiben eine Abweisung ohne Speicherung.
    `load_memory_context` liefert markierte Zeilen nie an `claude`, `claude_code`
    oder `grok`, auch nach der Freigabe; `ollama` erhält sie.
  - Ohne Blockliste prüft `deck_ui` mit den eingebauten Mustern und markiert nur bei
    einem Befund; andere Kanäle und eine unlesbare Blockliste markieren
    `blocklist_unavailable`. Neue Funktion `memory_privacy_hold`; das Auftrags-Gate
    bleibt unverändert. Der Versand an Cloud-Ziele bleibt ohne Blockliste gesperrt.
  - `cloud_redacted` geht in M1 noch nicht an Cloud-Ziele: eine vorübergehende
    Abweichung von 7.3 und Plan 3.5, verfolgt in #76 (auftragsfreie Redaktion).
  - Korrekturweg: `memory_approvals.corrects_approval_id` und `correct_message`,
    `correct_fact`, `correct_thread_topic`, `correct_thread_link`. Der Inhalts-Trigger
    lässt eine eingefrorene Entscheid-Spalte nur mit einer passenden Korrekturzeile
    ändern; Inhalt, Klasse und `privacy_hold` bleiben fest.
  - Folgetickets: M1b #77, M2 #78, Umstufung von `data_class` (M4/N3) #79,
    Redaktion #76.
  - `source_date` bleibt im Ersatz-Dedup-Schlüssel (vom Owner bestätigt).
  - Der Backfill markiert Altzeilen wie `ingest_turn` auf `deck_ui`.
  - Migration `20261110_0064` ist noch nicht in Produktion und wurde an Ort und Stelle
    angepasst. Eine Testdatenbank, die schon auf dem alten Stand `20261110_0064` steht,
    muss mit der alten Datei herabgestuft und neu aufgebaut werden; `upgrade head`
    allein ergänzt die neuen Spalten nicht.
- Nicht in diesem Stand: Oberfläche (M2, #78), n8n- und Grok-Adapter (M3),
  Import-Parser (M4), Slack Socket Mode (M5). Beschreibung in
  [KANAELE.md](docs/KANAELE.md) und [ADR 0010](docs/DECISIONS/0010-kanaele-und-gedaechtnis.md).

### Ollama Live-Adapter – Entscheid (#39, Draft)

- ADR 0009 ersetzt für die lokale Live-Lane den CLI-Transport durch begrenztes
  Loopback-HTTP: nur `http`, die Literale `127.0.0.1` oder `::1` und ein expliziter
  Port. Ollama ist exakt auf `v0.40.14` gepinnt; der Tag wurde gegen den Upstream-Commit
  `9a3e8177767245acc62b735eba7d5d8a553606d0` aufgelöst.
- LANES.md beschreibt den HTTP-Vertrag, die geprüften Quellstellen und die offenen
  Server-Nachweise. Code, Schemas und Migrationen ändern sich in #39 nicht.
- Folgestack #40 ff., nicht Teil dieses Eintrags: Migration 0018 mit Provenienz- und
  Live-Zählern, Runner-Flags, Text-Streaming, Canary und Referenzmodus.

### Ollama Live-Adapter – Provenienz (#40, Draft)

- Migration 0018 trennt Executor-Provenienz und Live-Erfolgszähler von Simulation
  und nicht attestierten Remote-Ergebnissen. Die v1-Schemas bleiben unverändert.
- Angenommene Fehler jeder Herkunft zählen in der Fehlerserie; nur ein Live-Erfolg
  setzt sie zurück. Die Migration erhält alte Fehlerdaten, die Health-Ansicht alle
  vorgesehenen Fehlergründe. Der neue Live-Zählerzeitraum steht in der Abnahme-Checkliste.
- `safe_execution_metadata` übernimmt aus Adapter-Metadaten nur geprüfte Schlüssel
  (Transport, Modell, Versionen, Zähler, HTTP-Status, Ausgabe-Digest) in Ergebnisereignis
  und Lane-Log. Der Runner leert die Metadaten vor jedem Versuch; Rohtext wird nie gespiegelt.
- `probe_generation` zäunt externe Lane-Proben ein: ein Probenergebnis ändert nur seine
  eigene Generation, jede neuere Beobachtung verwirft ältere Proben, und ein Probenabschluss
  ohne gestartete Zeile wird abgewiesen, statt eine neue Beobachtung anzulegen.
- Folgestack #41 ff., nicht Teil dieses Eintrags: Runner-Flags, Loopback-HTTP-Transport,
  Live-Executor, Daemon-Überwachung, Canary und Referenzmodus.

### Ollama Live-Adapter – Executor (#44, Draft)

- Das Kontextbudget folgt `num_ctx` aus `/api/show`, mit Fallback `4096` bei
  unbrauchbaren Angaben. Die Token-Schätzung verwendet aufgerundete UTF-8-Bytes
  geteilt durch drei; die Reserve bleibt bei 1024 Tokens.

### Etappe 17 – Audit und lokale Lane-Gesundheit (#32)

- Geschuetzte Audit-Ansicht fuer sichere Auftrags-, Betriebs- und Auth-Metadaten;
  Filter, stabile Seiten und begrenzter CSV-Export ohne Rohdetails.
- Migration 0016 zaehlt ausschliesslich endgueltig angenommene Ergebnisse. Vorpruefungen,
  tatsaechliche Erfolge und Anbieter-Kontingente bleiben getrennt; keine historische Rueckfuellung.
- Explizite Nachweise fuer Einmalzaehlung, drei Fehler und anschliessenden Erfolg mit
  Fehlerserie null sowie unveraenderte Zaehler/Zeitpunkte bei doppelten oder spaeten Ergebnissen.
  Die vorhandene Produktionslogik erfuellt diese Faelle unveraendert.
- Lane-Ansicht, Warnband und geschuetzter lesender Gesundheitsendpunkt mit Session-Anbindung.
  Audit und CSV unterscheiden bekannte Restore-Fehler von Teilfehlern ohne Prozessdiagnosen.
- Abnahme-Checkliste fuer beide Backup-/Restore-Datenbanken praezisiert; kein Abnahmelauf.

### Etappe 16 – Betrieb und vollstaendige Wiederherstellung (#31)

- Atomarer Backup-Satz mit separaten Dumps fuer zentrale DB und Wissens-DB samt pgvector;
  Manifestversion 2 verlangt beide Archive mit Pruefsummen, Revisionen und Zeilennachweisen.
- Zwei leere Restore-Ziele und beide Archive werden vor dem ersten Eingriff validiert.
  Teilfehler bleiben fehlgeschlagen; Erfolg verlangt beide Schema- und Alembic-Pruefungen.
- CLI, Update, Restic und Aufbewahrung akzeptieren keinen zentralen Ersatz fuer eine fehlende
  Wissenssicherung. Snapshot-Konsistenz gilt je Datenbank; kein verteilter Gesamt-Snapshot.
- Append-only Betriebsnachweise, 14-/90-Tage-Retention, systemd-Vorlagen und kontrollierte
  Prozessgruppenbeendigung mit Lease-Recovery. Der Restore-Timer ist eine woechentliche
  Trockenlauf-Erinnerung ohne `--apply`, kein Restore-Nachweis.

### Etappe 15 – Anmeldung und HTTP-Haertung (#30)

- Passwortgebundene Sessions mit Inaktivitaets- und absoluter Ablaufgrenze.
- Login-Drosselung je Client-Kennung (Peer-Adresse, nur als Digest), globale Sperre nach
  20 Fehlern in 24 Stunden bis Neustart oder lokale Entsperrung, CSRF-geschuetzte globale
  Abmeldung.
- Validierte Host-/Origin-Grenzen, begrenzte Requests und sichere Auth-Metadaten.

### Etappe 14 – Wissen (#29)

- Separater versionierter Wissens-Testvertrag mit pgvector und Fake-Embeddings.
- Pflicht-Datenklassen, Klassenfilter vor Rangfolge, unveraenderliche Kontext-Snapshots,
  Freigabe-Hashes und wiederaufnehmbare ausdrueckliche Owner-Exporte.
- Geschuetzte Suche, Kontextvorschau, Inbox-Import und Export in API und UI.
- Interne Task-UUID aus dem Prompt entfernt, damit zufaellige Zahlen keine falsche
  Datenschutzsperre ausloesen; strukturierte IDs und Inhaltspruefung bleiben erhalten.
- Abhaengigkeitskatalog und dieses Aenderungsprotokoll ergaenzt.

### Etappe 13 – Codex (#28)

- Codex-Lane mit explizitem Simulationsprofil, Fake-CLI und geprueften Versandartefakten.

### Etappe 12 – Claude (#27)

- Claude-Lane mit begrenztem simuliertem Reinraum, Auth- und Fehlerpfaden.

### Etappe 11 – Oberflaeche (#26)

- Owner-Sessions, Freigabe, Review, Steuerung und lokaler simulierter Chat.
- Echte Browser-Pruefungen bei schmaler und breiter Ansicht, auch ohne JavaScript.

### Etappe 10 – Fehlerpfade (#25)

- Persistierter Retry-/Backoff-Zustand, begrenztes Fallback und sichere Owner-Fehlerliste.
- Fehlervertrag in [Spec 10](docs/ETAPPEN/10-fehlerpfade.md) ausdruecklich PR #25 zugeordnet.

### Etappe 9 – Ollama (#24)

- Lokale Fake-CLI-Lane, Modellinventar und sichere Gesundheitsbeobachtungen.
- Aufgaben ohne konfiguriertes Modell werden ohne Versuch blockiert; ein voruebergehend
  fehlendes konfiguriertes Modell laesst die Aufgabe wartend.

### Etappen 1 bis 8

- Schema-Vertraege, Referenzrunner, PostgreSQL-Zustandsautomat, API, Datenschranke,
  Pseudonymisierung, Routing und Lane-gebundener Host-Runner.
- Historische Einzelheiten stehen in der Git-Historie und `docs/STAND.md`.
