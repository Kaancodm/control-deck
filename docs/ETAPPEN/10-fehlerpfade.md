# Etappe 10: begrenzte Fehlerpfade

Diese Umsetzung folgt dem freigegebenen Plan v1.1 und den spaeteren Owner-Ergaenzungen.
Sie ersetzt die abweichenden historischen Angaben zu Acceptance und Rate-Limit-Backoff
im Etappenindex. Auftraege und Ergebnisse behalten die eingefrorenen Schemas `task.v1`
und `result.v1`; `data_class` bleibt ein Pflichtfeld ohne Default.

## Fehlervertrag

Jeder erfolgreiche Claim erhoeht `attempt` genau einmal. Adapter bearbeiten einen Versuch
und melden dessen Ergebnis; sie starten keine versteckten Wiederholungen.

| Ereignis | Reaktion |
|----------|----------|
| `acceptance_failed` | Gleiche tatsaechlich ausfuehrende Lane bis zur globalen Versuchsgrenze |
| `rate_limited` | Gleiche Lane nach 60, danach 240 Sekunden; beim dritten Rate-Limit naechste zulaessige Lane |
| `timeout`, `worker_unavailable` | Naechste zulaessige Lane; ohne verbleibende Lane `failed` |
| `auth_required` | Aktuelle Lane pausieren, Gesundheit `auth_required`, dann Fallback |
| Anbieter-`policy_blocked` | Aktuelle Lane pausieren, Gesundheit `paused`, dann Fallback |
| `internal` | Ein weiterer Versuch auf derselben Lane, danach `failed`; die Erlaubnis gilt fuer den gesamten Task |
| Lease-Ablauf | Gleiche zuletzt geclaimte Lane innerhalb des globalen Budgets erneut einplanen |
| Lokale Datenschranke | Terminal `blocked`, kein Fallback und keine Entsperrung |
| Owner-Abbruch | `cancelled`, kein Retry |
| Explizite Owner-Deadline abgelaufen | `expired`, auch waehrend eines Backoffs |

`policy.max_attempts` begrenzt alle Pfade gemeinsam. Bei der letzten erlaubten Ausfuehrung
wird `failed` gespeichert; ein Anbieterproblem pausiert die Lane trotzdem. Die zwei
Rate-Limit-Wartezeiten sind keine zusaetzlichen Freiversuche. Fehlermeldungen von Providern
steuern keine SQL-Abfrage; nur validierte Fehlercodes gehen in den Entscheid ein.

Die lokalen Acceptance-Pruefungen laufen vor den Ergebnis-Locks. Ein vom Worker als
erfolgreich gemeldetes, lokal abgelehntes Ergebnis wird als `failed` mit
`acceptance_failed` gespeichert. Auch die Retry-Events und Fehlerliste verwenden diesen
effektiven Fehlercode. Eine misslungene lokale Rueckabbildung folgt demselben begrenzten Pfad.

## Dauerhafte Steuerung und Atomaritaet

Migration `0011` ergaenzt `tasks.retry_state` und `tasks.next_attempt_at`. Der versionierte
Zustand enthaelt Cursor, festgehaltene Lane, Rate-Limit-Zaehler je Lane und den taskweiten
internen Retry-Zaehler. Die stabile Kette folgt der unveraenderlichen Task-Policy;
`local_only` enthaelt ausschliesslich Ollama. Routing-Regeln koennen diese Menge weiter
einschraenken. Sie erweitern sie nicht.

Neue Routing-Abfragen erhalten den Fortschritt. Deaktivierte, pausierte oder im Tagesbudget
erschoepfte festgehaltene Lanes bleiben wartend; eine andere Lane darf den Retry nicht
uebernehmen. Bei einem Fallback werden nur spaetere zulaessige Lanes betrachtet. Sind diese
voruebergehend nicht verfuegbar, bleibt der Task `queued`. Eine nachtraeglich verschaerfte
Routing-Regel oder ungueltige Laufzeitmetadaten verschieben einen bereits versuchten Task,
statt fruehere Lanes erneut zuzulassen. Fehlende Metadaten historischer aktiver Tasks werden
aus der zuletzt zugewiesenen Lane konservativ initialisiert.

Die DB-Uhr bestimmt den naechsten Zeitpunkt. Sowohl die Queue-Selektion als auch das
atomare Claim-Update pruefen `next_attempt_at`; ein direkter Claim kann den Backoff nicht
umgehen. Ein partieller Queue-Index unterstuetzt die Auswahl. Ein Claim loescht den
abgelaufenen Wartezeitpunkt und belastet nur die tatsaechlich ausfuehrende Lane.

Lane-Pausen, Retry-Zustand, Ergebnis und Audit-Event werden gemeinsam committed. Die
Lock-Reihenfolge bleibt Admission vor Task vor Lease. Worker, Versuch, Lease, Versandhash,
Redaction-Metadaten und Owner-Deadline werden vor der Wirkung erneut geprueft. Falsche,
doppelte oder verspaetete Ergebnisse veraendern keine Zaehler und pausieren keine Lane.
Der Downgrade verweigert vorhandenen Retry-Zustand oder Backoff, damit Fortschritt nicht
still verloren geht.

## Fehlerliste und Owner-Entscheid

`GET /api/v1/tasks/failed` zeigt ausschliesslich terminal fehlgeschlagene Tasks. Der
Owner-Endpunkt bietet Datenklassenfilter und Pagination, fuehrt keinen Deadline-Sweep aus,
sperrt keine Task-Zeile und schreibt nichts. `last_error_code` kommt aus dem letzten lokal
gespeicherten Ergebnis; nach einer Owner-Review-Ablehnung ohne Ergebnisfehler ist er `null`.
Rohmeldungen und Ausgabeinhalte erscheinen nicht in dieser Liste.

Ein fehlgeschlagener oder blockierter Originalauftrag wird nicht wiederbelebt. Der Owner
kann nach einer Korrektur einen neuen Auftrag mit neuer ID und erneuter Freigabe erstellen.
Review-Ablehnung bleibt `review` nach `failed`; es gibt keine Entsperrung der Datenschranke.

## Pruefung und Inbetriebnahme

Unit-Tests decken die Fehlercodes, globalen Grenzen und ungueltige Metadaten ab.
PostgreSQL-Tests pruefen Neustartfestigkeit, Fencing, Reihenfolge der Locks, Lane-Pausen,
Budget-/Verfuegbarkeitsgrenzen, Owner-Abbruch, Deadline, Datenschutz und schreibfreie Listen.
Bestehende Host- und Ollama-Fake-CLI-Tests pruefen weiterhin genau einen Aufruf pro Claim.
Die bestehenden Referenzfaelle bleiben erhalten; neue inhaltliche Faelle schreibt der Owner.

**Inbetriebnahme:** echte CLI-Anmeldungen, Anbieter-Kontingente, Live-Fehler und der
Serverbetrieb bleiben ungeprueft. Es wurden keine Anbieter oder echten Modelle aufgerufen,
keine API-Keys verwendet und keine Serveraenderungen ausgefuehrt. Das Ollama-Live-Profil
bleibt gesperrt. Externes Review und Merge liegen beim Owner.
