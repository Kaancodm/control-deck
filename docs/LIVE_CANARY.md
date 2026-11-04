# Live-Canary

`make canary` stellt ein begrenztes Pruefwerkzeug fuer den ausdruecklichen Owner-Lauf
bereit. Das Werkzeug verwendet die lokal konfigurierte Datenbank und den Laufordner;
es braucht keinen Owner-Token und gibt ausschliesslich `PASS` oder `FAIL` aus.
Es gibt keine automatische Aktivierung oder Aenderung eines Dienstes.

## Bestehenden Canary pruefen

```bash
make canary ARGS="--task-id <canary-uuid>"
```

Dieser Modus liest ausschliesslich. Jede Abfragetransaktion ist als `READ ONLY`
markiert. Ohne Aufgabenkennung und ohne `--submit` endet der Aufruf mit `FAIL`,
ohne die Datenbank anzufassen. Der Pruefer akzeptiert nur den bekannten synthetischen
Canary-Auftrag mit lokaler Datenklasse, einer einzigen Ollama-Lane, genau einem Versuch
und obligatorischem Review. Er wartet hoechstens 330 Sekunden; `--timeout` setzt ein
anderes positives Budget.

## Canary ausdruecklich anlegen und freigeben

```bash
make canary ARGS="--submit --task-id <neue-canary-uuid>"
```

`--submit` legt genau einen synthetischen `local_only`-Auftrag an und gibt ihn ueber
die bestehenden Datenschranken fuer die Queue frei. Der Schalter verlangt dieselbe
Live-Flag- und URL-Konfiguration wie der Runner. Eine vorhandene Aufgabenkennung wird
abgewiesen. Ohne `--task-id` wird eine neue UUID erzeugt; fuer spaetere lesende Pruefungen
soll der Owner eine bekannte UUID vorgeben.

Ein separat gestarteter Live-Runner fuehrt den Auftrag aus. Das Werkzeug startet keinen
Runner, akzeptiert kein Review und loescht keine Ergebnisse. Bei Zeitablauf kann der
freigegebene Auftrag weiterhin in der Queue liegen; sein Zustand ist in der UI sichtbar.

`PASS` verlangt:

- Aufgabe in `review`, genau ein Versuch, Ergebnisprofil `live` und erfolgreiche Acceptance.
- Erwartetes Canary-Wort im Ergebnis und einen passenden Live-Provenienz-Check.
- Ein `task.succeed`-Ereignis mit Live-Profil sowie Lane-Status `ok` / `live_ready`.
- Einen erfolgreichen Live-Versuchszeitpunkt; beim Anlegen einen gegenueber dem Ausgangswert
  erhoehten Live-Zaehler. Bestehende Aufgabenpruefungen verlangen mindestens einen Live-Versuch.
- Ein begrenztes JSONL-Lane-Log mit Live-Profil, ohne Canary-Wort oder Ergebnistext.

Fehlerdetails, SQL-Diagnosen, Prompt und Modellantwort werden niemals ausgegeben.
Die Aufgaben- und Ergebnisartefakte behalten ihre bestehenden lokalen Zugriffsschranken.
Die automatisierten Nachweise laufen gegen den HTTP-Stub und die isolierte Testdatenbank.
Der echte Serverlauf, unveraendertes Modellinventar und Daemon-Policy bleiben Owner-Nachweise.
