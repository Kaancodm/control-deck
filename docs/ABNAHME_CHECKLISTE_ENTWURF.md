# Abnahme vorbereiten

Status: Checkliste vorbereitet. Der Abnahmelauf wurde nicht ausgefuehrt. Diese Datei ist weder eine Freigabe fuer Inbetriebnahme noch ein Release-Nachweis. Version und Tags bleiben unveraendert.

## Verwendung

Der Owner legt den zu pruefenden Commit, Umgebung, Zeitpunkt und Rollen fest. Fuer jede Zeile werden Ergebnis, Nachweis und pruefende Rolle eingetragen. Ein bestandener Simulationstest ersetzt keinen Nachweis am spaeteren Betriebsort. Offene sicherheitsrelevante Kriterien verhindern die Freigabe.

| Angabe | Eintrag |
|---|---|
| Zu pruefender Commit | TODO Owner |
| Isolierte Testumgebung | TODO Owner; keine Zugangsdaten eintragen |
| Externer Review-Anbieter | TODO Owner; anderer Anbieter als die Implementierung |
| Abnahmedatum | TODO Owner |
| Owner-Entscheid | Offen |

## Voraussetzungen

- [ ] Alle betroffenen Etappen sind extern geprueft; offene Befunde sind entschieden und dokumentiert.
- [ ] Der Owner hat die gewuenschten PRs selbst gemergt. Kein Agent-Merge.
- [ ] Die nachfolgende Pruefung ist auf einen eindeutigen Commit festgelegt.
- [ ] Die inhaltlichen Referenzfaelle stammen vom Owner. Ziel: mindestens 14 Faelle, davon 8 normale, 4 Fallen und 2 Grenzfaelle. TODO-Platzhalter zaehlen nicht als bestandene Faelle.
- [ ] Testdaten sind synthetisch; Zugangsdaten und Rohwerte erscheinen weder in Nachweisen noch in Git.
- [ ] Test-Datenbanken sind ausdruecklich isoliert und enden auf `_test`. Produktive Verbindungen sind fuer Repo-Pruefungen ausgeschlossen.

## Repo-Pruefungen fuer den spaeteren Abnahmelauf

Alle Ergebnisse sind offen. Die Etappen-PRs enthalten bereits separate lokale Entwicklungsnachweise; sie sind kein ausgefuehrter Gesamt-Abnahmelauf.

| Kriterium | Pruefmethode | Erwartung | Ergebnis / Nachweis |
|---|---|---|---|
| Pflichtchecks | `make lint`, `uv run alembic -x target=test check`, `make test`, `make reference` am gewaehlten Commit | Gruen; Testzahl, Skips und xfail getrennt ausweisen | Offen |
| Migration und Wiederanlauf | Neue isolierte DB sowie Upgrade vom dokumentierten vorherigen Stand | Schema passend; bestehende Auftraege bleiben konsistent | Offen |
| Pflicht-Datenklasse | API, UI, Import und Wissens-Export ohne `data_class` pruefen | Keine implizite Klasse im Schema oder DB-Default; ausdrueckliche Eingabe erforderlich | Offen |
| Freigabe und Abschluss | Entwurf, Owner-Freigabe, Lauf, automatische Abnahme, Owner-Review | Keine Arbeit vor Freigabe; erfolgreicher Lauf standardmaessig `review`; Review-Ablehnung `failed` | Offen |
| Deadline und Lease | Owner-Deadline, Lease-Ablauf, Worker-Absturz, verspaetetes Resultat | Nur Owner-Deadline erzeugt `expired`; Lease-Ablauf bleibt Retry/Verlust; kein spaeter Status-Overwrite | Offen |
| Identitaet und Parallelitaet | Fremde Lane, fehlende Lane, getrennte Fake-Tokens, mehrere Runner | Token bestimmt Lane; `local_only` nur Ollama; keine doppelten Claims; Kapazitaet erholt sich nach Ablauf | Offen |
| Abbruch und Steuerung | Pause, Not-Aus, aktiver Abbruch, Dienstende | Keine neuen unzulaessigen Claims; aktiver Versuch endet kontrolliert; Dienstende behauptet keinen Owner-Abbruch | Offen |
| Retry und Fallback | Acceptance, Rate-Limit, Timeout, Auth, Policy, interner Fehler, Neustart im Backoff | Persistierte Regeln; globales Versuchsbudget; keine versteckten Adapter-Retries; lokale Datenschutzsperre ohne Umgehungs-Fallback | Offen |
| Datenschranke | Synthetische Zugangsdaten, Unicode-Umgehungen, Mehrwort-Blockliste, Dateinamen und Kontext | Sperre vor Speicherung/Versand gemaess Vertrag; sichere Gruende ohne Rohwerte | Offen |
| Dateien und Artefakte | Symlinks, Pfadflucht, Hardlinks, Uebergroesse, Aenderung nach Freigabe, Artefaktkonflikt | Explizite Ablehnung; keine still gekuerzten Daten; Konflikt blockiert betroffenen Task und laesst Queue weiterlaufen | Offen |
| Redaktion und Versand | Unicode-Roundtrip, stabile taskgebundene Platzhalter, Fremdplatzhalter, exakte Payload-Hashes | Kein Original/Map im Cloud-Payload; exakt gepruefte Bytes werden archiviert und versandt | Offen |
| CLI-Simulation | Fake-CLIs fuer Ollama, Claude und Codex; Fehler, Abbruch, Timeout und unvollstaendige Ausgabe | Begrenzte Prozesse/Streams; korrekte Fehlerklassen; kein echter Anbieteraufruf; Simulation sichtbar gekennzeichnet | Offen |
| UI und Chat | Browser bei 390 und 1024 Pixeln; Login, Freigabe, Review, Dateien, lokaler Entwurf | Funktionen nutzbar; CSS-Variablen/Partials zentral; Chat ohne Freigabe-/Exportrechte und ohne automatische Verlauf-Uebernahme | Offen |
| Anmeldung und Rechte | Session-Ablauf, Abmelden, CSRF, XSS, Raten- und Request-Limits, Lane-/Owner-Trennung | Unberechtigte Zugriffe abgewiesen; keine Geheimnisse in Antworten, Events oder Logs | Offen |
| Wissen | Klassenmatrix, explizite Referenzen, Approval-Hashes, Export aus `done`, Wiederholung nach Teilfehler | Kein `local_only` im Cloud-Kontext; fehlende/unzulaessige Referenzen gemeldet; Export nur ausdruecklich und idempotent | Offen |
| Backup und Restore | Dump mit Manifest/Pruefsumme, Restore in leere isolierte Test-DB, Schema und Zaehlungen | Konsistenter Snapshot; falsche/nichtleere Ziele vor Schreibzugriff abgewiesen | Offen |
| Aufbewahrung | 14-/90-Tage-Grenzen, aktive Aufgaben, unbestaetigte Abbrueche, Teilfehler | Aktive Aufgaben geschuetzt; nur vorgesehene Daten entfernt; wiederaufnehmbarer und auditierter Ablauf | Offen |
| Audit und Gesundheit | Geschuetzte Ansichten, Auth-Fehler im selben Lauf, CSV und Filter | Sichere Metadaten; Zugriffsschutz; unbekannte Anbieter-Kontingente bleiben unbekannt | Offen |

## Inbetriebnahme: separat und erst nach ausdruecklicher Freigabe

Diese Punkte wurden im Repo-Ausbau nicht ausgefuehrt und sind keine automatisch freigegebenen naechsten Schritte.

- [ ] Tatsachliche Bindung, HTTPS-/Tailnet-Betrieb und externe Erreichbarkeit pruefen; sichere Cookie- und Proxy-Konfiguration bestaetigen.
- [ ] Lokale Betriebsrechte, systemd-Benutzerdienste, Lingering, Speicherorte und Dateirechte am Ziel bestaetigen.
- [ ] Echte Ollama-CLI: installierte Modelle, Kontextlimit, technisch erzwungene Grenzen fuer Downloads, Cloud-Nutzung und Dateizugriff sowie Abbruch und Vollstaendigkeit pruefen. Kein Aktivieren durch ein unbelegtes Sicherheits-Flag.
- [ ] Echte Claude-/Codex-CLI: genaue Version, Subscription-Anmeldung ohne API-Keys, isoliertes Profil, rootless Reinraum, ausgeschlossene Host-/Repo-/DB-Zugaenge, technisch deaktivierte Tools, Netzwerkgrenzen und unabhaengige Container-Bereinigung nachweisen.
- [ ] Interne Anbieter-Retries, Kontingente, Auth-Ablauf und Kosten-/Nutzungsregeln kontrollieren; unbekannte Werte nicht schaetzen.
- [ ] Tatsachlichen Wissens-DB-Vertrag, Datenklassen, Schreibrechte, Schema-Version, Embedding-Modell und Dimension vor Aktivierung bestaetigen. Der isolierte Testvertrag behauptet keine Kompatibilitaet mit einem fremden Schema.
- [ ] Backup-Ziel, Verschluesselung, Off-site-Kopie, Restic-Konfiguration, Aufbewahrung und Restore am vorgesehenen Betriebsort pruefen.
- [ ] Semantische Modellqualitaet anhand der vom Owner verfassten Referenzfaelle beurteilen. Fake-CLI-Erfolg ist kein Qualitaetsnachweis eines echten Modells.
- [ ] Das spaeter gelieferte Design separat testen; die funktionale UI-Pruefung ersetzt keine Designabnahme.

## Entscheid

| Pruefung | Stand |
|---|---|
| Gesamt-Abnahmelauf | Nicht ausgefuehrt |
| Inbetriebnahme | Offen |
| Externes Review aller Etappen | Offen |
| Owner-Freigabe fuer Release | Offen |
| Versionserhoehung / Release-Tag | Nicht ausgefuehrt; gesonderter Owner-Entscheid |

Freigabe erst nach dokumentierten Ergebnissen und Owner-Entscheid. Diese Vorbereitung fuehrt keine produktive Aktion aus.
