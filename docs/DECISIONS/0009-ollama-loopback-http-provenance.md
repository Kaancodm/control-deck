# ADR 0009: Ollama live ueber Loopback-HTTP, Flag und Provenienz

## Kontext

Etappe 9 verwendete auf Owner-Entscheid `ollama run` ausschliesslich als Fake-CLI.
Ein echter CLI-Aufruf kann fehlende Modelle automatisch laden und bei multimodalen
Eingaben lokale Dateien lesen. Eine Inventarpruefung vor dem Aufruf schliesst diese
Wege nicht. Die vorhandene Simulation ist kein Nachweis fuer einen echten Modelllauf.

## Entscheid

Der Owner hat am 04.11.2026 den Wechsel des geplanten lokalen Live-Transports zu
HTTP ueber Loopback freigegeben. Dieser Entscheid ersetzt die Transportwahl aus
Etappe 9 fuer Ollama. [ADR 0006](0006-official-clis-only.md) bleibt fuer die
Cloud-Lanes unveraendert: dort weiterhin nur offizielle CLIs, keine API-Keys.

- Ein spaeterer Live-Adapter verwendet die lokale Ollama-HTTP-API, vorzugsweise
  `http.client` aus der Standardbibliothek. Kein `ollama run`, kein beliebiger
  Executable-Override, keine neue HTTP-Abhaengigkeit und kein Proxy aus der Umgebung.
- Die Ziel-URL kommt ausschliesslich aus `DECK_OLLAMA_URL`: Schema `http`, IP-Literal
  `127.0.0.1` oder `[::1]`, expliziter Port 1 bis 65535. Hostnamen, Userinfo, Pfad,
  Query, Fragment und Weiterleitungen sind nicht erlaubt. Kein DNS-Fallback.
- `DECK_LIVE_ENABLED` ist standardmaessig aus. Nur exakt `1` oder `true` aktiviert
  die Freigabe; kein Trimmen und keine Gross-/Kleinschreibungskorrektur. Beide
  Variablen gehoeren ausschliesslich in die getrennte Runner-Konfiguration.
- `--simulate` bleibt Simulation, auch bei gesetztem Live-Flag. Bis zur gesonderten
  Transport-Anbindung verweigert jeder produktive Daemon-Aufruf weiterhin mit Exit 78.
  Eine Flag-Freigabe allein ist weder Live-Verfuegbarkeit noch ein Modellnachweis.
- Die Provenienz stammt vom ausgefuehrten Executor, nie allein vom Flag. Sie wird
  neben dem unveraenderten `result.v1` als `execution_profile` gespeichert; erlaubt
  sind `simulation`, `live`, `remote` und `unknown`. Neue interne Aufrufe fallen auf
  `simulation` zurueck, Altzeilen bleiben `unknown`, HTTP-Worker erhalten `remote`.
- Nur nachweislich `live` ausgefuehrte Versuche veraendern die echten Abschluss-,
  Erfolgs- und Fehlerserienzaehler. Andere Profile werden getrennt gezaehlt; sie
  loeschen keine echte Fehlerserie. Die Review-Ansicht kennzeichnet Simulationen.
  Ein nach der Acceptance angehaengter Provenienz-Check nutzt das vorhandene offene
  `checks[]`; die eingefrorenen v1-Schemas bleiben unveraendert.

## Folgen und Reihenfolge

PR 0 dokumentiert ausschliesslich diesen Entscheid. PR 1 fuehrt Provenienz,
getrennte Zaehler und einen Health-Abgleich gegen den Alembic-Head ein. Die bereits
in PR #37 belegte Login-Migration 0017 darf weder ueberschrieben noch als zweiter
Head uebergangen werden; die Provenienz braucht eine eindeutig gekettete Revision.
PR 2 verdrahtet Flag und URL, ohne Live-Transport oder Aenderung der Unit-Vorlage.
Alle drei PRs bleiben Draft; Agenten mergen nicht. Spaetere Adapter-, Canary- und
Server-Schritte sind durch diese drei PRs nicht freigegeben oder erledigt.

Ein spaeterer Adapter muss Modelle ausschliesslich aus der bestehenden Allowlist
verwenden, automatische Downloads und Cloud-Aliase verweigern und nur Text ohne
`images` oder `tools` senden. Fehler, Timeout und Abbruch duerfen keine Teilausgabe
als Erfolg behandeln. Logs enthalten sichere Metadaten, keine Prompts, Antworten
oder ungefilterten Anbieterdiagnosen. CI verwendet ausschliesslich Simulationen
und lokale Stubs; sie benoetigt kein echtes Ollama und keinen Login.

## Inbetriebnahme und offene Entscheidungen

Der gepinnte Ollama-Quellenstand in `docs/LANES.md` ist noch kein Nachweis fuer das
Verhalten der HTTP-API. Vor Live-Freigabe sind fehlendes Modell ohne Download,
Cloud-/Dateizugriffsgrenzen, Abschluss- und Abbruchprotokoll sowie die tatsaechliche
Daemon-Konfiguration nachzuweisen. Loopback-Bindung, `OLLAMA_NO_CLOUD=1` und das
vorherige Laden der Modelle sind gesonderte Owner-Schritte ausserhalb des Repos.

Streaming, Behandlung abgeschnittener Antworten, Versionsfenster und die genaue
Modell-Tag-Regel sind spaetere Entscheidungen. HTTP-Worker erhalten bis zu einem
gesonderten Attestierungsentscheid keinen Live-Nachweis. Dieser ADR aktiviert
keinen Transport und behauptet keine erfolgreiche Inbetriebnahme.
