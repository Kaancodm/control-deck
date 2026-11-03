# Routing und lokale Tagesbudgets

Etappe 7 berechnet die effektive Worker-Kette als reine Funktion in `app/routing`.
Die Funktion liest weder Dateien noch Datenbank oder Uhr. Konfiguration, UTC-Tag und
Verbrauch werden ausdruecklich uebergeben. `task.v1` und `result.v1` bleiben unveraendert.

## Reihenfolge der Entscheidung

1. Fuer Cloud-Datenklassen bildet `fallback_chain` den Ausgangspunkt in der angegebenen
   Reihenfolge. Eine Regel fuegt keine zusaetzliche Cloud-Lane hinzu.
2. Fuer `local_only` ersetzt der Router die Ausgangskette durch `ollama`, auch wenn der
   Auftrag nur Cloud-Worker vorgeschlagen hat. Die originale Kette bleibt im Auftrag erhalten.
3. `allowed_workers` und `forbidden_workers` schraenken die Auswahl ein. Eine leere
   `allowed_workers`-Liste erlaubt keinen Worker; ein fehlender Wert beschraenkt sie nicht.
   Auch der Ollama-Override darf diese Policy nicht umgehen.
4. Die erste passende lokale Regel schraenkt die Kette weiter ein. Deaktivierte Lanes
   fallen aus der Auswahl. Ohne grundsaetzlich zulaessige Lane lautet der Entscheid
   `blocked` mit `no_allowed_lane`.
5. Bereits ausgeschoepfte Tagesbudgets entfernen Lanes fuer die aktuelle Entscheidung.
   Sind alle ansonsten zulaessigen Lanes betroffen, lautet sie `deferred` mit
   `budget_exhausted`. Gespeicherte Auftraege bleiben wartend und werden beim naechsten
   Claim erneut geprueft; sie werden deshalb nicht terminal blockiert.
6. Die erste verbleibende Lane wird ausgewaehlt. Das Modell wird je Auftragsart gesucht,
   danach unter `default`. Ein fehlender Eintrag bleibt `null`; es wird kein Modell
   erfunden, heruntergeladen oder aufgerufen. Der spaetere Adapter verlangt seine
   tatsaechliche Modellkonfiguration vor der Ausfuehrung.

## Konfiguration

`config/router.yaml` liefert explizite Standardwerte ohne Modellnamen. Mit
`DECK_ROUTER_FILE` kann eine lokale Konfiguration gewaehlt werden. Ungueltige oder explizit
konfigurierte, aber nicht lesbare Dateien ergeben einen sicheren Konfigurationsfehler.
Die Datei ist groessenbegrenzt; unbekannte Felder, doppelte YAML-Schluessel, Aliase,
unbekannte Worker und ungueltige Zahlen werden abgewiesen.

Je Lane stehen `enabled`, `daily_attempt_limit` und `models` zur Verfuegung.
`daily_attempt_limit: null` bedeutet keine konfigurierte lokale Tagesgrenze; `0` erlaubt
keinen Claim. Es handelt sich um eigene Grenzen des Decks, nicht um ein vom Anbieter
bekanntes Restkontingent. Anbieter-Kontingente bleiben unbekannt.

## Persistenz und atomare Zaehlung

Die vorhandenen Laufzeitspalten `tasks.plan` und `tasks.lane` speichern Routing-Ergebnisse
neben dem unveraenderlichen Auftrag. Plan-Version 1 enthaelt Kette, Cursor, Regel, UTC-Tag,
Modellzuordnung und sichere Ausschlussgruende. Er wird beim Anlegen, bei Freigabe und
unmittelbar vor dem Claim erneut berechnet.

Jeder bestaetigte Claim verbraucht einen Versuch im lokalen Tagesbudget, auch wenn der
Runner danach abstuerzt oder kein Ergebnis abgibt. `task.acquire` ist das persistente
Zaehlerprotokoll: Lane und UTC-Budgettag stehen in den Metadaten. Die Event-Zeit verwendet
die aktuelle Datenbankuhr. Historische Claims ohne eindeutige Lane werden fuer ihren
UTC-Tag vorsorglich gegen jede Lane gezaehlt.

Seit Etappe 8 schuetzt ein exklusiver transaktionsgebundener PostgreSQL-Advisory-Lock
alle Claims und Steuerungs-Aenderungen gemeinsam. Er wird vor Task-Locks versucht; ist er
belegt, erhaelt der Poll keinen Claim. Die Auswahl verwendet weiterhin `SKIP LOCKED`.
Ein Rollback verwirft Versuch und Verbrauch zusammen. Tagesbudgets und globale
Parallelitaetsgrenze werden damit auch bei gleichzeitigen Runnern eingehalten.
Der Tageswechsel richtet sich ausschliesslich nach UTC und der Datenbankuhr.

## Dry-run

`POST /api/v1/tasks/dry-run` verlangt Owner-Authentifizierung und denselben vollstaendigen
Auftrag wie `POST /tasks`, einschliesslich expliziter `data_class`. Eine optionale
`deadline_at` wird getrennt validiert und muss in der Zukunft liegen.

Die Antwort zeigt Routing, Datenschranken-Status und konfigurierte lokale Budgets.
Sie enthaelt weder Originalauftrag noch Versandtext, Map oder gefundene Rohwerte.
Der Aufruf speichert keinen Task, keine Events, Maps, Leases oder Artefakte, reserviert
kein Budget und startet keinen Worker. Die Anzeige ist eine Momentaufnahme; beim Claim
wird erneut geprueft. Secrets oder ungueltige Eingaben ergeben 422, fehlende erforderliche
Konfiguration 503.

Ein moeglicher Routing-Plan hebt keine Datenschutzsperre auf. Etappe 8 erlaubt unterstuetzte
Treffer bei `cloud_redacted` nur ueber die sichere Host-Vorbereitung; Secrets und nicht
unterstuetzte Treffer bleiben blockiert.

## Referenzlauf und Grenzen

Der Fake-Worker verwendet denselben reinen Router mit einer festen Testkonfiguration.
Der bestehende Fall `004` prueft gemaess Owner-Vorgabe nun den Ollama-Override und verbietet
Cloud-Worker als Ergebnis. Neue inhaltliche Referenzfaelle bleiben Owner-Aufgabe.

Etappe 8 verbindet lane-spezifische Claims, Worker-Zuordnung, Pause/Not-Aus und den
Payload-Builder. Details: [RUNNER.md](RUNNER.md). Adapter und zentrale Fehler-/Fallback-Regeln
folgen in den weiteren Etappen. Diese Etappe fuehrt keine Anbieter- oder Modellaufrufe aus.

## Inbetriebnahme

Lokale Modellkennungen, Tagesgrenzen und Regeln muessen vor dem Live-Betrieb konfiguriert
und geprueft werden. Diese Etappe verbindet sich mit keinem Anbieter. Tatsachliche
Anbieter-Kontingente werden nicht abgefragt und bleiben als unbekannt ausgewiesen.
