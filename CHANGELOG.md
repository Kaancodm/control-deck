# Aenderungen

## Unveroeffentlicht

Die Eintraege beschreiben den gestapelten Draft-Stand. Externes Review, Owner-Merge,
Gesamt-Abnahme und Inbetriebnahme bleiben offen. Projektversion und Tags bleiben unveraendert.

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
