# Abnahme vorbereiten

Status: Checkliste vorbereitet. Der Abnahmelauf wurde nicht ausgeführt. Diese Datei ist weder eine Freigabe für Inbetriebnahme noch ein Release-Nachweis. Version und Tags bleiben unverändert.

## Verwendung

Der Owner legt den zu prüfenden Commit, Umgebung, Zeitpunkt und Rollen fest. Für jede Zeile werden Ergebnis, Nachweis und prüfende Rolle eingetragen. Ein bestandener Simulationstest ersetzt keinen Nachweis am späteren Betriebsort. Offene sicherheitsrelevante Kriterien verhindern die Freigabe.

| Angabe | Eintrag |
|---|---|
| Zu prüfender Commit | TODO Owner |
| Isolierte Testumgebung | TODO Owner; keine Zugangsdaten eintragen |
| Externer Review-Anbieter | TODO Owner; anderer Anbieter als die Implementierung |
| Abnahmedatum | TODO Owner |
| Owner-Entscheid | Offen |

## Voraussetzungen

- [ ] Alle betroffenen Etappen sind extern geprüft; offene Befunde sind entschieden und dokumentiert.
- [ ] Der Owner hat die gewünschten PRs selbst gemergt. Kein Agent-Merge.
- [ ] Die nachfolgende Prüfung ist auf einen eindeutigen Commit festgelegt.
- [ ] Die inhaltlichen Referenzfälle stammen vom Owner. Ziel: mindestens 14 Fälle, davon 8 normale, 4 Fallen und 2 Grenzfälle. TODO-Platzhalter zählen nicht als bestandene Fälle.
- [ ] Testdaten sind synthetisch; Zugangsdaten und Rohwerte erscheinen weder in Nachweisen noch in Git.
- [ ] Test-Datenbanken sind ausdrücklich isoliert und enden auf `_test`. Produktive Verbindungen sind für Repo-Prüfungen ausgeschlossen.

## Repo-Prüfungen für den späteren Abnahmelauf

Alle Ergebnisse sind offen. Die Etappen-PRs enthalten bereits separate lokale Entwicklungsnachweise; sie sind kein ausgeführter Gesamt-Abnahmelauf.

| Kriterium | Prüfmethode | Erwartung | Ergebnis / Nachweis |
|---|---|---|---|
| Pflichtchecks | `make lint`, `uv run alembic -x target=test check`, `make test`, `make reference` am gewählten Commit | Grün; Testzahl, Skips und xfail getrennt ausweisen | Offen |
| Migration und Wiederanlauf | Neue isolierte DB sowie Upgrade vom dokumentierten vorherigen Stand | Schema passend; bestehende Aufträge bleiben konsistent | Offen |
| Provenienz-Migration 0018 und Zähler-Reset | Upgrade von `20261104_0017`, Altzeile mit Fehlerserie und geschützter Downgrade | Genau ein Head `20261104_0018`; Altresultate `unknown`; alte Abschlusszahlen nach `simulated_attempts`; Live-Zähler und Live-Versuchszeitpunkte neu, Fehlerserie und letzte Fehlerdaten erhalten; keine unbelegte Live-Evidenz | Offen |
| Pflicht-Datenklasse | API, UI, Import und Wissens-Export ohne `data_class` prüfen | Keine implizite Klasse im Schema oder DB-Default; ausdrückliche Eingabe erforderlich | Offen |
| Freigabe und Abschluss | Entwurf, Owner-Freigabe, Lauf, automatische Abnahme, Owner-Review | Keine Arbeit vor Freigabe; erfolgreicher Lauf standardmässig `review`; Review-Ablehnung `failed` | Offen |
| Deadline und Lease | Owner-Deadline, Lease-Ablauf, Worker-Absturz, verspätetes Resultat | Nur Owner-Deadline erzeugt `expired`; Lease-Ablauf bleibt Retry/Verlust; kein später Status-Overwrite | Offen |
| Identität und Parallelität | Fremde Lane, fehlende Lane, getrennte Fake-Tokens, mehrere Runner | Token bestimmt Lane; `local_only` nur Ollama; keine doppelten Claims; Kapazität erholt sich nach Ablauf | Offen |
| Abbruch und Steuerung | Pause, Not-Aus, aktiver Abbruch, Dienstende | Keine neuen unzulässigen Claims; aktiver Versuch endet kontrolliert; Dienstende behauptet keinen Owner-Abbruch | Offen |
| Retry und Fallback | Acceptance, Rate-Limit, Timeout, Auth, Policy, interner Fehler, Neustart im Backoff | Persistierte Regeln; globales Versuchsbudget; keine versteckten Adapter-Retries; lokale Datenschutzsperre ohne Umgehungs-Fallback | Offen |
| Datenschranke | Synthetische Zugangsdaten, Unicode-Umgehungen, Mehrwort-Blockliste, Dateinamen und Kontext | Sperre vor Speicherung/Versand gemäss Vertrag; sichere Gründe ohne Rohwerte | Offen |
| Dateien und Artefakte | Symlinks, Pfadflucht, Hardlinks, Übergrösse, Änderung nach Freigabe, Artefaktkonflikt | Explizite Ablehnung; keine still gekürzten Daten; Konflikt blockiert betroffenen Task und lässt Queue weiterlaufen | Offen |
| Redaktion und Versand | Unicode-Roundtrip, stabile taskgebundene Platzhalter, Fremdplatzhalter, exakte Payload-Hashes | Kein Original/Map im Cloud-Payload; exakt geprüfte Bytes werden archiviert und versandt | Offen |
| CLI-Simulation | Fake-CLIs für Ollama, Claude und Codex; Fehler, Abbruch, Timeout und unvollständige Ausgabe | Begrenzte Prozesse/Streams; korrekte Fehlerklassen; kein echter Anbieteraufruf; Simulation sichtbar gekennzeichnet | Offen |
| UI und Chat | Browser bei 390 und 1024 Pixeln; Login, Freigabe, Review, Dateien, lokaler Entwurf | Funktionen nutzbar; CSS-Variablen/Partials zentral; Chat ohne Freigabe-/Exportrechte und ohne automatische Verlauf-Übernahme | Offen |
| Anmeldung und Rechte | Session-Ablauf, Abmelden, CSRF, XSS, Raten- und Request-Limits, Lane-/Owner-Trennung | Unberechtigte Zugriffe abgewiesen; keine Geheimnisse in Antworten, Events oder Logs | Offen |
| Wissen | Klassenmatrix, explizite Referenzen, Approval-Hashes, Export aus `done`, Wiederholung nach Teilfehler | Kein `local_only` im Cloud-Kontext; fehlende/unzulässige Referenzen gemeldet; Export nur ausdrücklich und idempotent | Offen |
| Backup und Restore | Manifestversion 2 mit zentraler DB und Wissens-DB samt pgvector, beiden Dumps/Prüfsummen, zwei getrennten leeren Testzielen sowie beiden Schema-/Alembic-Prüfungen und Zählungen | Konsistenter Snapshot je DB; fehlende/ungültige Archive und nichtleere Ziele vor Eingriff abgewiesen; Teilfehler bleiben fehlgeschlagen ohne Erfolgsnachweis; Timer ist nur Trockenlauf-Erinnerung | Offen |
| Aufbewahrung | 14-/90-Tage-Grenzen, aktive Aufgaben, unbestätigte Abbrüche, Teilfehler | Aktive Aufgaben geschützt; nur vorgesehene Daten entfernt; wiederaufnehmbarer und auditierter Ablauf | Offen |
| Audit und Gesundheit | Geschützte Ansichten, Auth-Fehler im selben Lauf, CSV und Filter | Sichere Metadaten; Zugriffsschutz; unbekannte Anbieter-Kontingente bleiben unbekannt | Offen |

## Inbetriebnahme: separat und erst nach ausdrücklicher Freigabe

Diese Punkte wurden im Repo-Ausbau nicht ausgeführt und sind keine automatisch freigegebenen nächsten Schritte.

- [ ] Tatsachliche Bindung, HTTPS-/Tailnet-Betrieb und externe Erreichbarkeit prüfen; sichere Cookie- und Proxy-Konfiguration bestätigen.
- [ ] Lokale Betriebsrechte, systemd-Benutzerdienste, Lingering, Speicherorte und Dateirechte am Ziel bestätigen.
- [ ] Echte Ollama-CLI: installierte Modelle, Kontextlimit, technisch erzwungene Grenzen für Downloads, Cloud-Nutzung und Dateizugriff sowie Abbruch und Vollständigkeit prüfen. Kein Aktivieren durch ein unbelegtes Sicherheits-Flag.
- [ ] Echte Claude-/Codex-CLI: genaue Version, Subscription-Anmeldung ohne API-Keys, isoliertes Profil, rootless Reinraum, ausgeschlossene Host-/Repo-/DB-Zugänge, technisch deaktivierte Tools, Netzwerkgrenzen und unabhängige Container-Bereinigung nachweisen.
- [ ] Interne Anbieter-Retries, Kontingente, Auth-Ablauf und Kosten-/Nutzungsregeln kontrollieren; unbekannte Werte nicht schätzen.
- [ ] Tatsachlichen Wissens-DB-Vertrag, Datenklassen, Schreibrechte, Schema-Version, Embedding-Modell und Dimension vor Aktivierung bestätigen. Der isolierte Testvertrag behauptet keine Kompatibilität mit einem fremden Schema.
- [ ] Backup-Ziel, Verschlüsselung, Off-site-Kopie, Restic-Konfiguration, Aufbewahrung und Restore am vorgesehenen Betriebsort prüfen.
- [ ] Semantische Modellqualität anhand der vom Owner verfassten Referenzfälle beurteilen. Fake-CLI-Erfolg ist kein Qualitätsnachweis eines echten Modells.
- [ ] Das später gelieferte Design separat testen; die funktionale UI-Prüfung ersetzt keine Designabnahme.

## Entscheid

| Prüfung | Stand |
|---|---|
| Gesamt-Abnahmelauf | Nicht ausgeführt |
| Inbetriebnahme | Offen |
| Externes Review aller Etappen | Offen |
| Owner-Freigabe für Release | Offen |
| Versionserhöhung / Release-Tag | Nicht ausgeführt; gesonderter Owner-Entscheid |

Freigabe erst nach dokumentierten Ergebnissen und Owner-Entscheid. Diese Vorbereitung führt keine produktive Aktion aus.
