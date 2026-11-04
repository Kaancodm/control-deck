# Aenderungen

## Unveroeffentlicht

Die Eintraege beschreiben den gestapelten Draft-Stand. Externes Review, Owner-Merge,
Gesamt-Abnahme und Inbetriebnahme bleiben offen. Projektversion und Tags bleiben unveraendert.

### Ollama Live-Adapter – Entscheid (#39, Draft)

- ADR 0009 ersetzt fuer die lokale Live-Lane den CLI-Transport durch begrenztes
  Loopback-HTTP: nur `http`, die Literale `127.0.0.1` oder `::1` und ein expliziter
  Port. Ollama ist exakt auf `v0.40.14` gepinnt; der Tag wurde gegen den Upstream-Commit
  `9a3e8177767245acc62b735eba7d5d8a553606d0` aufgeloest.
- LANES.md beschreibt den HTTP-Vertrag, die geprueften Quellstellen und die offenen
  Server-Nachweise. Code, Schemas und Migrationen aendern sich in #39 nicht.
- Folgestack #40 ff., nicht Teil dieses Eintrags: Migration 0018 mit Provenienz- und
  Live-Zaehlern, Runner-Flags, Text-Streaming, Canary und Referenzmodus.

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
