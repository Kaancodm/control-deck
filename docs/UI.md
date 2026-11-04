# Owner-Oberfläche und lokaler Chat

Die Oberfläche unter `/ui` verwendet Jinja2 und lokal eingebundenes HTMX. Alle Formulare
funktionieren ohne JavaScript. Farben und Abstände sind eine funktionale Basis; das
spätere Design aus den Owner-Komponenten wird separat getestet und abgenommen.

## Bedienung

| Ansicht | Verhalten |
|---------|-----------|
| `/ui/login` | Lokales Passwort für genau einen Owner; kein Worker-Token-Login |
| `/ui/tasks` | Titel-, Status-, Datenklassen- und Lane-Filter; 50 Aufträge pro Seite |
| `/ui/tasks/new` | Auftrag mit Text und Dateien anlegen; `data_class` bleibt Pflicht |
| `/ui/tasks/{id}` | Unveränderlicher Auftrag, getrennte Freigaben, Ergebnisse und Zeitlinie |
| `/ui/failed` | Schreibgeschützte Fehlerliste aus Etappe 10, ohne automatische Wiederholung |
| `/ui/lanes` | Beobachtete Lane-Zustände, Pause und bestätigter Not-Aus |
| `/ui/audit` | Auftrags-, Betriebs- und Auth-Ereignisse mit begrenzten Filtern |
| `/ui/audit.csv` | Ausschliesslich die sichtbare Audit-Seite als geschützter CSV-Download |
| `/ui/chat` | Lokale Antwort und optionaler validierter Auftragsentwurf |

Der sichtbare Vorschlag `local_only` ist kein Schema- oder DB-Default. Eine Cloud-Datenklasse
benötigt eine ausdrückliche Bestätigung; die Datenschranke gilt weiterhin. Erlaubte und
verbotene Worker, Fallback-Reihenfolge, Versuchs-/Zeitlimit und Abnahmeregeln werden gegen die
bestehenden Schemas validiert. Die optionale Owner-Deadline muss eine zukünftige ISO-Zeit
mit Zeitzone sein; sie liegt ausserhalb des eingefrorenen Auftrags.

Speichern erstellt einen Auftrag zur Start-Freigabe; eine erkannte Sperre führt zu `blocked`.
Start- und Ergebnis-Freigabe sind getrennte, bestätigte Aktionen. Ergebnis-Ablehnung mit
Grund schliesst als `failed` ab. Laufende Abbrüche bleiben als angefordert sichtbar, bis der
Worker sie bestätigt oder sein Lease endet. Nur die Owner-Deadline erzeugt `expired`.
Blockierte Aufträge kann der Owner mit Begründung (mindestens 10 Zeichen) entsperren
(`blocked → queued`, Event `unblock` mit `actor = owner`, D8/F6); andere terminale Aufträge
werden nicht wiederbelebt.

Auftragslisten, Details, Zeitlinien und Fehlerlisten führen keine Deadline-Sweeps aus und
nehmen keine Task-Zeilensperren. Der Login stellt dagegen eine kurzlebige Session aus.
API und UI teilen ihre Owner-Geschäftsfunktionen direkt; kein HTTP-Aufruf an sich selbst.
Geprüfte Versand-Payloads sind nur über die Owner-Session als Klartext zugänglich.
Der Worker speichert Ergebnisse bereits lokal rückabgebildet. Die UI zeigt diesen
autoritativen Text escaped an; sie wendet keine zweite Rückabbildung an. Die Map gelangt
nicht ins Template oder in den Download. Positionsmarkierungen brauchen zusätzliche
Provenienz-Metadaten und bleiben ein Folgepunkt; die eingefrorenen Result-Schemas werden
dafür nicht verändert.

## Dateien

Zulässig sind UTF-8-Text, Markdown, JSON und CSV: maximal 16 Dateien, 1 MiB je Datei und
insgesamt 5 MiB Auftragsinhalt. Die gesamte UI-Anfrage ist auf 6 MiB begrenzt. Der Parser
hält den begrenzten Upload im Speicher; erst nach Text-, Schema- und Geheimnisprüfung
werden Dateien privat abgelegt. PDF, Office, Bilder, Archive und Binärdaten werden abgewiesen.

Browser-Dateinamen sind reine Anzeigemetadaten. Speicherpfade entstehen serverseitig unter
dem Eingangsverzeichnis, mit privaten Verzeichnissen und Dateien. Descriptor-relative
Zugriffe verweigern Symlinks, Pfadflucht und ungeeignete Dateitypen. Ein fehlgeschlagener
Submit entfernt nur seinen neu erstellten Upload-Satz; bestehende Dateien werden nie ersetzt.
Die Start-Freigabe speichert weiterhin SHA-256 der exakt gelesenen Bytes. Der Runner prüft
diese vor Ausführung erneut.

## Login und CSRF

`DECK_OWNER_PASSWORD_HASH` muss einen gültigen Argon2id-Hash mit begrenzten Parametern
enthalten. Es gibt kein Standardpasswort und keine Klartext-Konfiguration. Echte
Zugangsdaten werden erst bei der späteren Inbetriebnahme eingerichtet.

Session- und CSRF-Tokens sind unabhängige zufällige Werte; nur ihre Hashes liegen in
PostgreSQL. Die Cookies sind `HttpOnly`, `SameSite=Strict`, auf `/ui` begrenzt und
standardmässig `Secure`. `DECK_UI_COOKIE_SECURE=false` ist ein ausdrücklicher Schalter
für isolierte HTTP-Tests. Anmeldung rotiert beide Tokens; Abmeldung widerruft die Session.
Anonyme Login-Sessions gelten zehn Minuten, authentifizierte Sessions höchstens zwölf
Stunden; nach 30 Minuten ohne Aktivität endet die Session früher. Aktivität verlängert
niemals die absolute Grenze. Ein Passwortwechsel macht bestehende Sessions ungültig.
Ablauf und Widerruf werden serverseitig geprüft. «Überall abmelden» widerruft mit CSRF-Schutz
alle bestehenden Browser-Sessions und Login-Challenges.

Fünf fehlgeschlagene Anmeldungen innerhalb von 15 Minuten sperren weitere Versuche desselben
Clients für das verbleibende Fenster (HTTP 429 mit `Retry-After`). Die Client-Kennung ist die
Peer-Adresse der Verbindung, nie ein Forwarding-Header; in der Datenbank liegt nur ihr Digest.
Parallele Requests eines Clients können das Limit nicht umgehen. Anonyme Sessions sind
begrenzt; abgelaufene und widerrufene Einträge sowie abgelaufene Client-Fenster werden
bereinigt. Nach 20 Fehlern in 24 Stunden über alle Clients ist die Anmeldung global gesperrt,
bis der Dienst neu startet oder `make unlock-login` läuft. Die Anmeldeseite zeigt diese
Sperre ohne Passwortformular und verweist auf beide Wege.

Jeder POST, auch Login und Logout, benötigt ein ausdrückliches CSRF-Feld; ein Cookie
allein genügt nicht. Fremde Origins und Cross-Site-Requests werden abgewiesen. Chromium
kann mit `Referrer-Policy: no-referrer` bei normalen Formularen `Origin: null` senden;
akzeptiert wird dies nur mit `Sec-Fetch-Site: same-origin` und gültigem Session-CSRF.
Bei konfigurierter `DECK_PUBLIC_ORIGIN` gilt ausschliesslich diese validierte Origin;
Forwarding-Header ersetzen sie nicht.
Echte Browser-Smokes prüfen diesen Ablauf. UI-Sessions verleihen keine Schreib-API- oder
Worker-Rechte. Ausschliesslich die neue lesende Gesundheitsroute akzeptiert auch die Session.

## Audit und Lane-Gesundheit

Die Audit-Ansicht vereint Auftrags-, Betriebs- und Auth-Ereignisse. Filter betreffen Quelle,
Ereignistyp, Akteur, Lane, Task und ein Zeitintervall mit Zeitzone (Beginn inklusive, Ende
exklusive). Standard sind 100, maximal 200 Einträge je Seite; Offset maximal 100000.
CSV exportiert genau diese Seite, einschliesslich sicherer Zähler. Formel-Präfixe werden
neutralisiert. Unbekannte Gründe, Akteure und Ereignistypen werden nicht als Rohwerte gezeigt;
Payload, Ergebnisse, Credentials, Dateipfade und freie Detail-Dictionaries bleiben ausgeschlossen.

Die Lane-Ansicht trennt positive Vorprüfungen von tatsächlich angenommenen Ergebnissen.
Seit Migration `0018` zählen `completed_attempts` und die Live-Versuchszeitpunkte
ausschliesslich attestierte Live-Ergebnisse. Frühere Abschlusszahlen sind in
`unknown_attempts` enthalten; vorhandene Fehlerserien und Fehlerzeitpunkte bleiben
erhalten. Angenommene Fehler aller Herkünfte erhöhen die Fehlerserie. Ein Ergebnis
zählt atomar erst nach letzter Lease-/Identitäts-/Deadline-Prüfung und lokaler Acceptance.
Doppelte oder verworfene Ergebnisse zählen nicht. Nur Live-Erfolg setzt die Fehlerserie zurück;
Abbruch ist weder Erfolg noch Fehler. Der letzte Fehler bleibt als historische Metadaten sichtbar.

Ein Auth-Fehler erscheint im selben Lauf und auf der nächsten Owner-Seite im Warnband.
Ein mindestens 24 Stunden alter Claim ohne aktuell erfassten Erfolg löst einen Hinweis auf
fehlenden Erfolgsnachweis aus. Positive Vorprüfungen verdecken diesen Hinweis nicht.
Das Band wird mit der bestehenden Steuerungsansicht aktualisiert. Lokale Tagesbudgets
zählen Claims; sie sind keine Anbieter-Kontingente. Anbieterwerte bleiben unbekannt.

`GET /api/v1/health` akzeptiert Owner-Token oder gültige Session und zeigt DB-Schemastand,
lokale Lane-Beobachtungen, Steuerung und Zähler. Es prüft keinen Anbieter. Ohne Credentials
liefert es nur `{"status":"ok"}` ohne DB-Zugriff. Ungültige Credentials liefern keine Details.
Gesundheitsabfragen verlängern die Session-Aktivität nicht. Das Session-Cookie gilt dafür
unter `/`; das CSRF-Cookie bleibt unter `/ui`. Beim Setzen und Abmelden wird auch das alte
Session-Cookie unter `/ui` entfernt. Cookie-Schutz und CSRF-Prüfungen bleiben erhalten.

Jinja escaped Inhalte. CSP erlaubt nur lokale Ressourcen, keine Inline-Skripte, Objekte
oder fremde Frames. `nosniff`, `no-referrer` und `no-store` schützen auch Fehlerantworten.
HTMX 2.0.8 ist lokal versioniert, ohne Eval, Script-Ausführung aus Antworten oder
Browser-History-Cache; Hash und Lizenz liegen unter `app/ui/static/vendor/`.

## Chat-Grenze

Der Adapter erhält nur die begrenzte aktuelle Nachricht und höchstens vier ausdrücklich
gewählte erledigte Aufträge als Kontext. Er bekommt keine DB-Session, Dateipfade,
Freigabe-, Export- oder Steuerungsfunktionen. Modellarbeit läuft ausserhalb einer offenen
DB-Transaktion. Chatverlauf, Prompt und Antwort werden nicht als eigene DB-Daten gespeichert.

Ein Entwurf muss `task.v1` erfüllen, explizit `local_only` wählen und darf weder Dateien
noch Wissens-Referenzen bestimmen. Der Owner öffnet ihn ausdrücklich im Formular, prüft
ihn und speichert einen neuen Auftrag mit neuer Identität. Erst eine separate Start-Freigabe
erlaubt Arbeit. Chatverlauf und ausgewählter Kontext werden nicht automatisch in neue
Cloud-Aufträge übernommen. Geheimnisse blockieren auch den lokalen Chat.

Das normale Laufzeitprofil bleibt unverfügbar. Tests injizieren ausschliesslich einen
Fake-CLI-Adapter mit sichtbarer Simulation-Kennzeichnung. Simulationserfolg wird nicht als
nachgewiesene Live-Bereitschaft dargestellt.

## Theme-Schnittstellen und Tests

Farben, Abstände, Rundungen, Schriften und Kontrollgrösse sind in den CSS-Variablen am
Anfang von `app/ui/static/app.css` zentralisiert. Layout und wiederkehrende Komponenten
liegen in `base.html` und `templates/partials/`. Status erscheinen immer mit Text und Symbol.
Die Oberfläche benötigt keinen Build-Schritt, kein CDN und keine externen Schriften.

Chromium-Smokes verwenden eine isolierte temporäre App, eine ausdrückliche `_test`-DB,
ein synthetisches Passwort und Fake-Worker. Bei 390 Pixeln mit JavaScript und 1024 Pixeln
ohne JavaScript prüfen sie Login, Upload, Start-Freigabe, simulierten Lauf, Review, `done`,
Chat-Entwurf, Logout, globale Abmeldung zweier Browser-Sessions sowie globale Sperre
und lokale Entsperrung. Sie prüfen ferner fehlende externe Requests, horizontales Scrollen,
semantische Überschriften und einen CSS-Variablenwechsel ohne Logikänderung.

Browser-Tests brauchen Chromium: installiertes `chromium` oder `uv run playwright install
chromium`. Die CI richtet den Browser nur in ihrem temporären Test-Runner ein. Pflichtchecks:
`make lint`, `uv run alembic -x target=test check`, `make test` und `make reference`.

Abhängigkeitsgründe: Jinja2 für Auto-Escaping und serverseitige Templates; argon2-cffi
für geprüfte Argon2id-Verifikation; python-multipart für begrenzte Formular-Uploads;
Uvicorn als ASGI-Laufzeit für lokale App und Browser-Tests; Playwright ausschliesslich als
Entwicklungsabhängigkeit für echte Browserablauf-Tests.

## Inbetriebnahme

Offen bleiben echte Owner-Zugangsdaten, sichere Bindung/TLS und Betriebskonfiguration,
Live-Ollama mit nachgewiesener Isolation und gemeinsamer Modell-Zulassung, echte Geräte
und das spätere 3D-Komponenten-Design. Keine echten Logins, Anbieter-/Modellaufrufe,
API-Keys oder Serveränderungen wurden verwendet. Simulationserfolg ist kein Live-Nachweis.
