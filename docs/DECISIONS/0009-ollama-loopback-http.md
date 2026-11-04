# ADR 0009: Ollama live über Loopback-HTTP und explizite Provenienz

## Kontext

Der Live-Adapter folgt auf den Simulationsstand `11c9617`. Der Owner hat den Bauplan
und die empfohlene HTTP-Variante zur Umsetzung freigegeben. Der CLI-Entscheid aus
Etappe 9 kann automatische Downloads und promptgesteuerte Dateizugriffe nicht sicher
begrenzen. ADR 0006 für die offiziellen Cloud-CLIs bleibt unverändert.

## Entscheid

- Ollama verwendet die lokale HTTP-API mit `http.client` aus der Standardbibliothek.
  Erlaubt sind ausschliesslich HTTP und die Loopback-Literale `127.0.0.1` und `::1`
  mit explizitem Port. Proxies und Weiterleitungen werden nicht verwendet.
- `DECK_LIVE_ENABLED` aktiviert nur mit exakt `1` oder `true`. Es gehört zusammen
  mit `DECK_OLLAMA_URL` in die Runner-Env-Datei. Simulation bleibt ausdrücklich
  wählbar und ignoriert das Live-Flag. Claude und Codex bleiben gesperrt.
- Der erste Vertrag pinnt **exakt Ollama 0.40.14**. Ein grösseres Versionsfenster
  braucht neue Quellprüfung und Vertragsnachweise. Abweichungen verhindern Claims.
- Nur lokal konfigurierte Modelle mit explizitem Tag sind erlaubt. Cloud-Kennungen,
  Remote-Metadaten und multimodale Fähigkeiten werden abgewiesen. HTTP-Anfragen
  verwenden zusätzlich den Quellen-Suffix `:local`, um den Wechsel eines Alias auf
  ein Remote-Modell zwischen Vorprüfung und Ausführung abzuweisen.
- `/api/chat` streamt NDJSON. Es erhält eine User-Nachricht mit dem unveränderten
  archivierten Text, keine Bilder oder Werkzeuge. `truncate: false` und
  `shift: false` ergänzen die konservative Kontextvorprüfung. Das JSON-Encoding
  ist ein Transportumschlag: `payload_hash` bezeichnet weiterhin die UTF-8-Bytes
  des Nachrichteninhalts, nicht den gesamten HTTP-Body.
- Nur `done: true` mit `done_reason: stop` erlaubt Erfolg. `length` verlangt Review
  mit `acceptance_failed`; fehlender Abschluss ist ein Protokollfehler. Timeout und
  Abbruch verwerfen Teilantworten. Es gibt keinen Adapter-Retry und keinen Pull.
- `result.v1` bleibt eingefroren. Das Executor-Profil wird ausserhalb des Schemas
  persistiert und als Check mitgegeben. Nur `live` beeinflusst Live-Erfolgszähler.
  HTTP-Worker erhalten serverseitig `remote`; alte Ergebnisse bleiben `unknown`.
- Exit 78 ist ausschliesslich Konfigurationsverweigerung. Ein unerreichbarer
  Ollama-Dienst bleibt eine wiederholt geprüfte nicht verfügbare Lane.

## Nachweise und Grenzen

Quellreferenz ist `v0.40.14`, Commit
`9a3e8177767245acc62b735eba7d5d8a553606d0`; siehe den Quellenvertrag in
[LANES.md](../LANES.md). Tests verwenden ausschliesslich einen lokalen HTTP-Stub.
Sie beweisen Client-Verhalten, keine installierte Ollama-Instanz oder Modellqualität.

Der Owner richtet Loopback-Bindung, `OLLAMA_NO_CLOUD=1` und vorab geladene Modelle
im Ollama-Dienst ein. Ein Verbindungsabbruch propagiert im geprüften Quellstand
über den Request-Kontext; die tatsächliche Freigabe von Modellressourcen bleibt
Teil der Server-Abnahme. Referenzläufe und Canary werden lokal vorbereitet; ihre
Ausführung auf dem Server erfolgt gesondert durch den Owner. Draft-PRs werden
nicht automatisch gemergt oder deployt.

## Abgleich der Entwürfe #39 und #43

Dieser ADR ist der gemeinsame Entwurf. Der parallele Draft #43 wird zugunsten
von #39 zurückgezogen. Folgende Abweichungen wurden aufgelöst:

| Punkt | Entwurf #39 | Entwurf #43 | Gemeinsamer Stand |
|---|---|---|---|
| Umfang | Gesamter vorbereiteter Live-Stack | Entscheid auf PR 0 bis 2 begrenzt | Alle Teile bleiben Draft und folgen der Merge-Reihenfolge und Owner-Abnahme |
| Streaming | NDJSON-Streaming festgelegt | Spätere Entscheidung offen | `stream: true` gemäss implementiertem Draftvertrag |
| Abgeschnittene Antwort | `length` ergibt `acceptance_failed` mit Review | Behandlung offen | Review mit `acceptance_failed`, kein Erfolg |
| Version und Modell-Tags | Exakt 0.40.14 und explizite Tags | Regeln offen | Exakter Versions-Pin und explizite Tags gemäss implementiertem Draftvertrag |
| Nachweisstand | Quellstellen und Stub-Tests geprüft | HTTP-Verhalten vor Live-Freigabe nachzuweisen | Kein sachlicher Widerspruch: Quellprüfung und Client-Tests liegen vor; echte Server-Abnahme bleibt offen |

Die genauere URL-Regel aus #43 gilt ebenfalls: expliziter Port 1 bis 65535,
keine Hostnamen, Userinfo, Pfade, Query, Fragmente oder Weiterleitungen.
Das Flag ist standardmässig aus. Neue interne Ergebnisse fallen auf
`simulation` zurück; Altresultate bleiben `unknown`, HTTP-Worker erhalten
`remote`. Nur `live` verändert Live-Abschluss- und Erfolgszähler. Angenommene
Fehler jeder Herkunft erhöhen die Fehlerserie und aktualisieren den letzten
Fehler. Ausschliesslich ein Live-Erfolg setzt die Fehlerserie zurück.

Simulation, Remote und unbekannte Herkunft haben jeweils einen eigenen Zähler.

Die Kontextpriorität lautet Lane-Konfiguration → `model_info` → Fallback 4096.
`ceil(UTF-8-Bytes / 3) + 1024` darf das effektive `num_ctx` nicht überschreiten;
der Chat erhält denselben Wert. Fehlende Einzelmodelle sperren keine anderen
verifizierten Modelle. Live-Bereitschaft gilt höchstens 30 Sekunden ab Probe-Beginn;
persistente Probe-Generationen verhindern das Überschreiben durch alte Ergebnisse.

## Merge-Reihenfolge

Der Stack baut auf `main` mit dem bereits übernommenen #37 auf. Dessen
Login-Migration `20261104_0017` bleibt unverändert. Die Provenienz-Migration aus
#40 trägt Nummer 0018 und `down_revision = "20261104_0017"`; damit folgt sie
eindeutig auf `20261104_0017_login_capacity.py`. Die Draft-Reihenfolge ist
#39 → #40 → #41 → #42 → #44 → #45 → #46 → #47.

Das Vorbereiten oder Rebasen der Drafts erteilt keine Merge- oder
Inbetriebnahmefreigabe. Ohne Owner-GO erfolgt kein Merge.
