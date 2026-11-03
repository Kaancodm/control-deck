# Owner-Oberflaeche und lokaler Chat

Die Oberflaeche unter `/ui` verwendet Jinja2 und lokal eingebundenes HTMX. Alle Formulare
funktionieren ohne JavaScript. Farben und Abstaende sind eine funktionale Basis; das
spaetere Design aus den Owner-Komponenten wird separat getestet und abgenommen.

## Bedienung

| Ansicht | Verhalten |
|---------|-----------|
| `/ui/login` | Lokales Passwort fuer genau einen Owner; kein Worker-Token-Login |
| `/ui/tasks` | Titel-, Status-, Datenklassen- und Lane-Filter; 50 Auftraege pro Seite |
| `/ui/tasks/new` | Auftrag mit Text und Dateien anlegen; `data_class` bleibt Pflicht |
| `/ui/tasks/{id}` | Unveraenderlicher Auftrag, getrennte Freigaben, Ergebnisse und Zeitlinie |
| `/ui/failed` | Schreibgeschuetzte Fehlerliste aus Etappe 10, ohne automatische Wiederholung |
| `/ui/lanes` | Beobachtete Lane-Zustaende, Pause und bestaetigter Not-Aus |
| `/ui/chat` | Lokale Antwort und optionaler validierter Auftragsentwurf |

Der sichtbare Vorschlag `local_only` ist kein Schema- oder DB-Default. Eine Cloud-Datenklasse
benoetigt eine ausdrueckliche Bestaetigung; die Datenschranke gilt weiterhin. Erlaubte und
verbotene Worker, Fallback-Reihenfolge, Versuchs-/Zeitlimit und Abnahmeregeln werden gegen die
bestehenden Schemas validiert. Die optionale Owner-Deadline muss eine zukuenftige ISO-Zeit
mit Zeitzone sein; sie liegt ausserhalb des eingefrorenen Auftrags.

Speichern erstellt einen Auftrag zur Start-Freigabe; eine erkannte Sperre fuehrt zu `blocked`.
Start- und Ergebnis-Freigabe sind getrennte, bestaetigte Aktionen. Ergebnis-Ablehnung mit
Grund schliesst als `failed` ab. Laufende Abbrueche bleiben als angefordert sichtbar, bis der
Worker sie bestaetigt oder sein Lease endet. Nur die Owner-Deadline erzeugt `expired`.
Blockierte und andere terminale Auftraege werden nicht wiederbelebt oder entsperrt.

Auftragslisten, Details, Zeitlinien und Fehlerlisten fuehren keine Deadline-Sweeps aus und
nehmen keine Task-Zeilensperren. Der Login stellt dagegen eine kurzlebige Session aus.
API und UI teilen ihre Owner-Geschaeftsfunktionen direkt; kein HTTP-Aufruf an sich selbst.
Gepruefte Versand-Payloads sind nur ueber die Owner-Session als Klartext zugaenglich.
Der Worker speichert Ergebnisse bereits lokal rueckabgebildet. Die UI zeigt diesen
autoritativen Text escaped an; sie wendet keine zweite Rueckabbildung an. Die Map gelangt
nicht ins Template oder in den Download. Positionsmarkierungen brauchen zusaetzliche
Provenienz-Metadaten und bleiben ein Folgepunkt; die eingefrorenen Result-Schemas werden
dafuer nicht veraendert.

## Dateien

Zulaessig sind UTF-8-Text, Markdown, JSON und CSV: maximal 16 Dateien, 1 MiB je Datei und
insgesamt 5 MiB Auftragsinhalt. Die gesamte UI-Anfrage ist auf 6 MiB begrenzt. Der Parser
haelt den begrenzten Upload im Speicher; erst nach Text-, Schema- und Geheimnispruefung
werden Dateien privat abgelegt. PDF, Office, Bilder, Archive und Binaerdaten werden abgewiesen.

Browser-Dateinamen sind reine Anzeigemetadaten. Speicherpfade entstehen serverseitig unter
dem Eingangsverzeichnis, mit privaten Verzeichnissen und Dateien. Descriptor-relative
Zugriffe verweigern Symlinks, Pfadflucht und ungeeignete Dateitypen. Ein fehlgeschlagener
Submit entfernt nur seinen neu erstellten Upload-Satz; bestehende Dateien werden nie ersetzt.
Die Start-Freigabe speichert weiterhin SHA-256 der exakt gelesenen Bytes. Der Runner prueft
diese vor Ausfuehrung erneut.

## Login und CSRF

`DECK_OWNER_PASSWORD_HASH` muss einen gueltigen Argon2id-Hash mit begrenzten Parametern
enthalten. Es gibt kein Standardpasswort und keine Klartext-Konfiguration. Echte
Zugangsdaten werden erst bei der spaeteren Inbetriebnahme eingerichtet.

Session- und CSRF-Tokens sind unabhaengige zufaellige Werte; nur ihre Hashes liegen in
PostgreSQL. Die Cookies sind `HttpOnly`, `SameSite=Strict`, auf `/ui` begrenzt und
standardmaessig `Secure`. `DECK_UI_COOKIE_SECURE=false` ist ein ausdruecklicher Schalter
fuer isolierte HTTP-Tests. Anmeldung rotiert beide Tokens; Abmeldung widerruft die Session.
Anonyme Login-Sessions gelten zehn Minuten, authentifizierte Sessions hoechstens zwoelf
Stunden. Ablauf und Widerruf werden serverseitig geprueft.

Fuenf fehlgeschlagene Anmeldungen innerhalb von 15 Minuten sperren weitere Versuche fuer
das verbleibende Fenster. Das Limit gilt gemeinsam fuer das eine Konto, auch bei parallelen
Requests. Anonyme Sessions sind begrenzt; abgelaufene und widerrufene Eintraege werden
bereinigt. Etappe 15 ergaenzt weitere Session-, Request- und Bind-Haertung.

Jeder POST, auch Login und Logout, benoetigt ein ausdrueckliches CSRF-Feld; ein Cookie
allein genuegt nicht. Fremde Origins und Cross-Site-Requests werden abgewiesen. Chromium
kann mit `Referrer-Policy: no-referrer` bei normalen Formularen `Origin: null` senden;
akzeptiert wird dies nur mit `Sec-Fetch-Site: same-origin` und gueltigem Session-CSRF.
Echte Browser-Smokes pruefen diesen Ablauf. UI-Sessions verleihen keine API-/Worker-Rechte.

Jinja escaped Inhalte. CSP erlaubt nur lokale Ressourcen, keine Inline-Skripte, Objekte
oder fremde Frames. `nosniff`, `no-referrer` und `no-store` schuetzen auch Fehlerantworten.
HTMX 2.0.8 ist lokal versioniert, ohne Eval, Script-Ausfuehrung aus Antworten oder
Browser-History-Cache; Hash und Lizenz liegen unter `app/ui/static/vendor/`.

## Chat-Grenze

Der Adapter erhaelt nur die begrenzte aktuelle Nachricht und hoechstens vier ausdruecklich
gewaehlte erledigte Auftraege als Kontext. Er bekommt keine DB-Session, Dateipfade,
Freigabe-, Export- oder Steuerungsfunktionen. Modellarbeit laeuft ausserhalb einer offenen
DB-Transaktion. Chatverlauf, Prompt und Antwort werden nicht als eigene DB-Daten gespeichert.

Ein Entwurf muss `task.v1` erfuellen, explizit `local_only` waehlen und darf weder Dateien
noch Wissens-Referenzen bestimmen. Der Owner oeffnet ihn ausdruecklich im Formular, prueft
ihn und speichert einen neuen Auftrag mit neuer Identitaet. Erst eine separate Start-Freigabe
erlaubt Arbeit. Chatverlauf und ausgewaehlter Kontext werden nicht automatisch in neue
Cloud-Auftraege uebernommen. Geheimnisse blockieren auch den lokalen Chat.

Das normale Laufzeitprofil bleibt unverfuegbar. Tests injizieren ausschliesslich einen
Fake-CLI-Adapter mit sichtbarer Simulation-Kennzeichnung. Simulationserfolg wird nicht als
nachgewiesene Live-Bereitschaft dargestellt.

## Theme-Schnittstellen und Tests

Farben, Abstaende, Rundungen, Schriften und Kontrollgroesse sind in den CSS-Variablen am
Anfang von `app/ui/static/app.css` zentralisiert. Layout und wiederkehrende Komponenten
liegen in `base.html` und `templates/partials/`. Status erscheinen immer mit Text und Symbol.
Die Oberflaeche benoetigt keinen Build-Schritt, kein CDN und keine externen Schriften.

Chromium-Smokes verwenden eine isolierte temporaere App, eine ausdrueckliche `_test`-DB,
ein synthetisches Passwort und Fake-Worker. Bei 390 Pixeln mit JavaScript und 1024 Pixeln
ohne JavaScript pruefen sie Login, Upload, Start-Freigabe, simulierten Lauf, Review, `done`,
Chat-Entwurf und Logout. Sie pruefen ferner fehlende externe Requests, horizontales Scrollen,
semantische Ueberschriften und einen CSS-Variablenwechsel ohne Logikaenderung.

Browser-Tests brauchen Chromium: installiertes `chromium` oder `uv run playwright install
chromium`. Die CI richtet den Browser nur in ihrem temporaeren Test-Runner ein. Pflichtchecks:
`make lint`, `uv run alembic -x target=test check`, `make test` und `make reference`.

Abhaengigkeitsgruende: Jinja2 fuer Auto-Escaping und serverseitige Templates; argon2-cffi
fuer gepruefte Argon2id-Verifikation; python-multipart fuer begrenzte Formular-Uploads;
Uvicorn als ASGI-Laufzeit fuer lokale App und Browser-Tests; Playwright ausschliesslich als
Entwicklungsabhaengigkeit fuer echte Browserablauf-Tests.

## Inbetriebnahme

Offen bleiben echte Owner-Zugangsdaten, sichere Bindung/TLS und Betriebskonfiguration,
Live-Ollama mit nachgewiesener Isolation und gemeinsamer Modell-Zulassung, echte Geraete
und das spaetere 3D-Komponenten-Design. Keine echten Logins, Anbieter-/Modellaufrufe,
API-Keys oder Serveraenderungen wurden verwendet. Simulationserfolg ist kein Live-Nachweis.
