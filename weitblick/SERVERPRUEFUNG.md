# Weitblick Stufe 2 · Serverpruefung fuer Atik

Diese Anleitung enthaelt nur lesende Pruefungen, mit zwei klar markierten
Ausnahmen: Schritt 5 (Bank-Einstellung `retain_extraction_mode=chunks` auf den
Test-Banks, braucht Kaans Ja) und Schritt 6 (S-K80: zwei synthetische Eintraege
in zwei isolierten Test-Banks und ein erwartbar abgewiesener Loeschversuch aus
Bank A). Keine Bereinigung, keine Loeschung aus Bank B, keine Produktivdaten,
kein Schreiben in die Bank `weitblick`, kein Cronjob, kein Timer, kein Neustart.

Keine Werte ausgeben: URL, Token, Hostnamen und Adressen erscheinen weder im
Terminal-Protokoll noch im Bericht. Fehlermeldungen von `curl` werden deshalb
verworfen; berichtet werden nur Exit-Codes, Statuscodes und Zaehler.

## Voraussetzungen

- Der Stand von `feat/weitblick-stufe2` liegt auf dem Server in einem eigenen
  Arbeitsordner. Er muss vorher von einer berechtigten Person veroeffentlicht
  worden sein; dieser Lauf hat nichts gepusht.
- Python 3.12 oder neuer. Keine Pakete noetig.
- In der Shell sind `WEITBLICK_HINDSIGHT_URL` und `WEITBLICK_HINDSIGHT_TOKEN`
  aus der vorhandenen privaten Quelle gesetzt, ohne sie anzuzeigen.
  `WEITBLICK_HINDSIGHT_ENABLED` ist dafuer nicht noetig.
- Name des Hindsight-Containers bekannt (unten `<hindsight-container>`), ebenso
  der LLM-Provider aus Schritt 4 (unten `<llm-provider>`).
- Fuer F5 ein ruhiges Zeitfenster, in dem kein anderer Dienst den LLM-Provider
  nutzt. Sonst sind Zaehler nicht zuordenbar.

## Schritt 0 · Offline-Tests auf dem Server

```sh
python3 -m unittest discover -s weitblick/tests -v
```

Bestanden: `OK`, Exit 0, Anzahl der Tests wie in `weitblick/WORKLOG.md`.
Sonst offen.

## Schritt 1 · F3 Umgebung pruefen, ohne Werte zu zeigen

```sh
for v in WEITBLICK_HINDSIGHT_URL WEITBLICK_HINDSIGHT_TOKEN; do
  if [ -n "${!v:-}" ]; then echo "$v gesetzt"; else echo "$v FEHLT"; fi
done
python3 -B -c 'import os; from weitblick.hindsight import config_from_env; config_from_env(dict(os.environ, WEITBLICK_HINDSIGHT_ENABLED="1")); print("Konfiguration gueltig")'
```

Bestanden: beide Zeilen `gesetzt` und `Konfiguration gueltig`.
Bei einem Fehler nennt die Meldung nur Variablennamen. Dann offen.

## Schritt 2 · F2 Version lesen

```sh
docker ps --filter name=<hindsight-container> --format '{{.Image}}'
```

Bestanden: Image-Tag 1.2.1. Laut den lokalen Recherchenotizen gibt es bereits
1.2.2 und 1.2.3. Laeuft eine andere Version, ist F2 offen und Schritt 3 muss
gegen genau diese Version gelesen werden.

## Schritt 3 · F2 API-Vertrag lesend pruefen

Annahme: Der Dienst liefert seine OpenAPI-Beschreibung unter `/openapi.json`.

```sh
umask 077
curl -fsS --max-time 10 -H "Authorization: Bearer $WEITBLICK_HINDSIGHT_TOKEN" \
  "$WEITBLICK_HINDSIGHT_URL/openapi.json" -o "$HOME/hindsight-openapi.json" 2>/dev/null
echo "curl Exit $?"
python3 -B -m weitblick.vertrag "$HOME/hindsight-openapi.json"
```

Die Datei bleibt privat im Home-Ordner und kommt nicht ins Repo.
Der Pruefer gibt nur Pfade, Feldnamen, Typen und Statuscodes aus.

- Bestanden: `Vertrag: passt`. Dann sind Retain-Pfad, die Request-Felder
  `items` und `async`, Lesen und Loeschen nach Dokument-ID sowie der ID-Typ
  auf Schema-Ebene belegt. Ein Feld `mode` darf fehlen; in v1.2.1 gehoert es
  nicht zum Retain-Request. Die Fehlerform belegt erst S-K80.
- Offen: `curl Exit` ungleich 0 (anderer Pfad oder nicht erreichbar) oder
  `Vertrag: Abweichung`. Die `ABWEICHUNG`-Zeilen zeigen, welche Annahme in
  `weitblick/hindsight.py` anzupassen ist. Vorher kein Betrieb.
- Zusatz: Der Pfad `GET /v1/default/chunks/{chunk_id}` ist in v1.2.1 nicht
  bankgebunden (hindsight_api/api/http.py:7561). Schritt 6 prueft die
  Banktrennung dieses Pfads.

## Schritt 4 · F5 Serverkonfiguration lesen (nur Namen)

```sh
docker exec <hindsight-container> env | cut -d= -f1 | grep -iE 'llm|retain|extract|chunk' | sort
```

Nur Namen, keine Werte. Existiert eine Variable fuer den Retain- oder
Extraktionsmodus und ist sie sicher kein Schluessel, darf ihr Wert einzeln
gelesen werden: `docker exec <hindsight-container> printenv <NAME>`.

- Bestanden: Eine Server- oder Bank-Einstellung `retain_extraction_mode=chunks`
  ist belegt. In v1.2.1 ist `chunks` ein zulaessiger Wert dieser Einstellung
  und ueberspringt den LLM-Aufruf. Ein Request-Feld `mode` ist kein Beleg.
- Offen: Keine Belegstelle fuer `retain_extraction_mode=chunks`. Dann bleibt
  der Standard `concise` (mit LLM) moeglich.

## Schritt 5 · Test-Banks auf retain_extraction_mode=chunks stellen

**Schreibend. Braucht Kaans Ja. Ohne dieses Ja nicht ausfuehren.**

Dieser Schritt aendert nur die beiden Test-Banks `weitblick-sk80-a` und
`weitblick-sk80-b`. Die Bank `weitblick` bleibt unberuehrt. In Hindsight
v1.2.1 ist `retain_extraction_mode` eine Bank- oder Server-Einstellung
(hindsight_api/config.py:1900, 1901, 4290), nicht Teil des Retain-Requests.
Der Wert `chunks` speichert den Text ohne LLM-Aufruf. Schreiben geht ueber
`PATCH /v1/default/banks/{bank_id}/config` (hindsight_api/api/http.py:9149).
Der Schreibzugriff kann serverseitig ausgeschaltet sein
(`HINDSIGHT_API_ENABLE_BANK_CONFIG_API`). Existiert eine Test-Bank noch nicht,
endet der Schritt mit `offen`; das Anlegen ist eine eigene Entscheidung (F6).

Beispiel, nur nach Kaans Ja und nur fuer die beiden Test-Banks:

```sh
# SCHREIBEND, braucht Kaans Ja. Nicht ausfuehren ohne dieses Ja.
for bank in weitblick-sk80-a weitblick-sk80-b; do
  echo "PATCH $bank"
  curl -sS --max-time 10 -o /tmp/weitblick-bank-config.out -w "HTTP %{http_code}\n"     -X PATCH -H "Authorization: Bearer $WEITBLICK_HINDSIGHT_TOKEN"     -H "Content-Type: application/json"     -d '{"updates":{"retain_extraction_mode":"chunks"}}'     "$WEITBLICK_HINDSIGHT_URL/v1/default/banks/$bank/config" 2>/dev/null
  python3 -c 'import json,sys; d=json.load(open("/tmp/weitblick-bank-config.out")); c=d.get("config") or d.get("overrides") or {}; print("retain_extraction_mode", c.get("retain_extraction_mode","(fehlt)"))' 2>/dev/null || echo "Antwort nicht lesbar"
done
rm -f /tmp/weitblick-bank-config.out
```

Berichtet werden nur HTTP-Status und der Wert von `retain_extraction_mode`.
Keine URL, kein Token, keine anderen Konfigurationswerte.

- Bestanden: Beide Test-Banks antworten mit 200 und `retain_extraction_mode chunks`.
- Offen: 404 (API aus oder Bank fehlt), 401, anderer Status, oder der Wert ist
  nicht `chunks`. Dann S-K80 trotzdem nur nach gesondertem Entscheid ausfuehren,
  weil der Standardmodus `concise` ein LLM aufruft.

Hinweis zu `enable_observations` (nicht Teil des Beispiel-PATCH oben, ebenfalls
schreibend und nur nach gesondertem Ja): In v1.2.1 ist das Bank-Setting
standardmaessig eingeschaltet (hindsight_api/config.py:1968, 4309). Nach einem
Retain startet Hindsight dann, wenn auch `enable_auto_consolidation` gilt
(Standard ebenfalls wahr, config.py:1969), eine asynchrone Konsolidierung
(memory_engine.py:7490-7492), die ein LLM nutzen kann. `retain_extraction_mode=chunks`
stellt das nicht ab. Nur wenn der LLM-Provider `none` ist, werden beide
Einstellungen zusammen gezwungen (config.py:4616-4622).

## Schritt 6 · S-K80 (schreibende Ausnahme) und T5

```sh
START=$(date -u +%Y-%m-%dT%H:%M:%SZ); echo "Start $START"
python3 -B -m weitblick.sk80 --bank-a weitblick-sk80-a --bank-b weitblick-sk80-b
echo "sk80 Exit $?"
END=$(date -u +%Y-%m-%dT%H:%M:%SZ); echo "Ende $END"
```

Was passiert, in dieser Reihenfolge:

1. Synthetischer Eintrag Y in Bank A, damit A existiert.
2. Synthetischer Eintrag X in Bank B. Der Text enthaelt eine zufaellige Marke.
3. X wird ueber seine ID aus B gelesen. Ohne diesen Nachweis endet der Lauf mit
   `offen`, denn dann beweist ein spaeterer Fehler nichts.
4. Der Chunk von X wird ueber `GET /v1/default/chunks/{chunk_id}` gelesen
   (Pfad ist nicht bankgebunden, die ID kodiert die Bank). Ohne Nachweis
   bleibt dieser Pruefpunkt `offen`.
5. Dieselbe Dokument-ID wird im Kontext von A gelesen. Erwartet: 403 oder 404
   ohne Nutzdaten von X.
6. Eine gefaelschte Chunk-ID mit Bank A und der Dokument-ID von X wird ueber
   denselben Chunk-Pfad gelesen. In v1.2.1 ist das eine unbekannte ID und
   die Antwort ist typischerweise 404; das belegt keine Banktrennung und
   bleibt `offen`. Enthaelt die Antwort Nutzdaten von X, ist S-K80
   `nicht bestanden`.
7. Loeschversuch der Dokument-ID im Kontext von A. Erwartet: 403 oder 404.
8. X wird erneut aus B gelesen, ID und Inhalt werden mit Schritt 3 verglichen.
9. T5: Suche nach dem Wortlaut von X in Bank A. Erwartet: X nicht gefunden.

Die Ausgabe enthaelt nur Schrittnamen, Statuscodes und Urteile.
Die Test-Banks bleiben bestehen. Eine Bereinigung braucht ein eigenes GO.
Banks mit dem Namen `weitblick` oder ohne das Praefix `weitblick-sk80-` weist
das Werkzeug ab.

| Ergebnis | Bedeutung |
| --- | --- |
| `S-K80: bestanden`, Exit 0 | Banktrennung fuer Lesen und Loeschen nach Dokument-ID belegt (Schritte 3, 5, 7, 8). Der Chunk-Pfad bleibt `offen`; eine 404 auf eine unbekannte Chunk-ID belegt keine Trennung. |
| `S-K80: nicht bestanden`, Exit 1 | Lesen oder Loeschen aus A war erfolgreich, eine leere Erfolgsantwort kam, die Antwort (Dokument oder Chunk) enthielt Nutzdaten von X, oder X fehlt danach in B. Sofort melden, nicht umdeuten. |
| `S-K80: offen`, Exit 3 | Verbindungsfehler, 401, 5xx, andere Statuscodes oder Vorbereitung nicht bestaetigt. Ein Verbindungs- oder allgemeiner Anmeldefehler belegt keine Banktrennung. |
| `T5: bestanden` | Die Suche in A findet X nicht. |
| `T5: offen` | Suche nicht auswertbar (Pfad oder Antwortform anders, F2). |

Muessen Banks ausdruecklich angelegt werden, endet die Vorbereitung mit `nicht
bestaetigt` und das Ergebnis ist `offen`. Das Anlegen ist eine eigene
Entscheidung (Fable F6).

## Schritt 7 · F5 LLM-Aufrufe im S-K80-Fenster ausschliessen

```sh
docker logs --since "$START" --until "$END" <hindsight-container> 2>&1 \
  | grep -ciE 'llm|completion|extract|reflect'
docker logs --since "$START" --until "$END" <llm-provider> 2>&1 \
  | grep -cE '/api/chat|/api/generate|/v1/chat/completions|/v1/completions'
```

Laeuft der Provider nicht in Docker, liest Atik dessen Journal fuer dasselbe
Fenster, zum Beispiel `journalctl -u <dienst> --since ... --until ...`.
Die Zeilen selbst liest nur Atik. Berichtet werden nur die Zaehler.

- Bestanden: Im Fenster liegen die S-K80-Retains. Der Provider-Zaehler fuer
  Chat- und Generate-Aufrufe ist 0, und kein anderer Dienst hat den Provider im
  Fenster genutzt. Embedding-Aufrufe (`/api/embed`) sind keine LLM-Erzeugung,
  werden aber mit Anzahl notiert.
- Offen: Der Provider protokolliert keine einzelnen Anfragen, er ist ein
  externer Dienst ohne Zaehler fuer genau dieses Fenster, oder andere Dienste
  nutzen ihn gleichzeitig. Dann bleibt der serverseitige Nachweis fuer F5 offen.

## Nicht Teil dieser Pruefung

- T8 Wiederherstellung aus der Nachtsicherung in eine Test-Datenbank: Das ist
  ein Schreibvorgang und braucht ein eigenes Owner-GO. Bleibt offen.
- Aktivieren des Nachtlaufs (`WEITBLICK_HINDSIGHT_ENABLED=1` gegen die Bank
  `weitblick`) und die Cron-Installation: erst nach bestandenen Schritten 0 bis 7
  und Owner-GO.

## Bericht an Kaan

Getrennt ausweisen:

- Offline: Anzahl der Tests aus Schritt 0 und deren Ergebnis.
- Server: Ergebnis je Schritt 1 bis 7 (`bestanden` oder `offen`, bei S-K80
  auch `nicht bestanden`), mit Exit-Codes und Zaehlern, ohne Werte.
