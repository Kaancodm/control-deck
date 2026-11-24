# Owner-Oberfläche und lokaler Chat

Die Oberfläche unter `/ui` verwendet Jinja2 und lokal eingebundenes HTMX. Alle Formulare
funktionieren ohne JavaScript. Farben und Abstände sind eine funktionale Basis; das
spätere Design aus den Owner-Komponenten wird separat getestet und abgenommen.

## Bedienung

| Ansicht | Verhalten |
|---------|-----------|
| `/` | 303 auf `/ui`; festes Ziel, liest weder Query noch Cookie noch Datenbank |
| `/ui` | 303 auf `/ui/overview`; ohne Datenbank |
| `/ui/overview` | Übersicht nach dem Login; nur lesend, jede Zahl aus gespeicherten oder konfigurierten Daten |
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

## Shell und Navigation

Nach dem Login landet der Owner fest auf `/ui/overview` (`HOME_PATH` in `app/ui/views.py`).
Es gibt weiterhin keinen `next`- oder `return_to`-Parameter; ein mitgeschicktes `next` wird
ignoriert. `/ui/tasks` und alle übrigen Pfade bleiben direkt erreichbar. Ein Link von einer
fremden Seite auf `/` ergibt wie jede Cross-Site-Anfrage 403; Lesezeichen funktionieren.

Ab 861 CSS-Pixeln zeigt eine Seitenleiste alle zehn Ziele in zwei Gruppen (Workspace:
Übersicht, Aufträge, Neuer Auftrag, Wissen; Steuerung: Lanes, Assistent, Meldungen, Offene
Punkte, Lokaler Chat, Audit). Bis 860 Pixel ersetzt sie eine fixe Leiste am unteren Rand mit
Übersicht, Lanes, Assistent, Meldungen und Offen; die übrigen Ziele liegen im «Menü» der
Kopfzeile. So gibt es je Breite genau einen sichtbaren Link pro Ziel. «Meldungen» und
«Offene Punkte» sind Abschnitte der Übersicht (`#meldungen`, `#offen`), keine eigenen Seiten.
Der Tab «Meldungen» zielt auf `/ui/overview?open=meldungen#meldungen`; der Server öffnet den
sonst eingeklappten Abschnitt ohne JavaScript, andere Werte von `open` werden ignoriert.
Die Kopfzeile ist ab 861 Pixeln `sticky`, darunter eine einzige deckende Zeile (Seitentitel,
«Abmelden», «Menü») ohne `sticky` und ohne Unschärfe-Filter. Sie bildet mit `z-index: 7`
einen eigenen Stapelkontext, damit das geöffnete Menü über allen Abschnitten und über der
unteren Leiste liegt. Die untere Leiste ist deckend und verschwindet, solange ein Text-,
Auswahl- oder Mehrzeilenfeld den Fokus hat (nur CSS, `body:has(...)`), damit sie nicht mit
der Bildschirmtastatur ins Bild rückt. Sie erscheint erst 0,4 Sekunden nach dem Verlassen
des Felds wieder, damit ein Klick an ihrer Stelle nicht auf der Leiste landet. Diese
Verzögerung gilt auch mit «Bewegung reduzieren»; sie ist Klickschutz, keine Animation.
Browser ohne `color-mix()` (Safari vor 16.2) erhalten feste Rückfallwerte für Linien,
Rahmen, Kopfzeilen-Hintergrund und Netzlinien. Der Runner-Zustand steht als kompakte Pill-Zeile mit
dem Link «Steuerung →» unter der Kopfzeile. Die Anmeldeseite hat keine Navigation und kein
`fixed`- oder `sticky`-Element. Der Zoom bleibt frei, Eingaben bleiben 16 Pixel gross,
Bedienelemente mindestens 44 Pixel hoch; `env(safe-area-inset-*)` hält Abstand zu Notch und
Home-Indikator.

Die Übersicht ist für das Telefon zuerst gebaut. Die DOM-Reihenfolge ist die Lesefolge:
Titel mit genau einer gefüllten Hauptaktion («Freigaben öffnen», wenn etwas wartet, sonst
«＋ Neuer Auftrag»), Status-Leiste (Freigaben, Claims heute, Lanes, Backup), offene Punkte,
Fokus-Auftrag, Lanes, danach eingeklappt Assistent, Meldungen und Audit, am Ende der
Projekt-Stand und der Schema-Stand. Bis 860 Pixel entfallen Hero und Netzkarte. Fokus ist
der älteste Auftrag, der auf eine Start- oder Ergebnis-Freigabe wartet; laufende Aufträge
werden nicht zum Fokus. Wartet nichts, steht «Kein Auftrag wartet auf dich.»

Die Übersicht zeigt nur Werte mit Quelle: offene Start- und Ergebnis-Freigaben, Claims des
Tages und das lokale Tageslimit (nur wenn jede Lane eines hat), die Lanes mit
Vorprüfungszeit, den Schrittweg des Fokus-Auftrags, das letzte erfasste Backup- und
Restore-Ereignis aus dem Audit, die letzten fünf Audit-Ereignisse und lesend den
Schema-Stand aus `alembic_version`. Fehlt eine Quelle, steht «nicht verfügbar» oder «noch
keines erfasst»; scheitert eine Datenbanklesung nach der Session-Prüfung (Runner-Steuerung,
Lane-Health, Aufträge, Audit), antwortet die Seite mit 503 und dem Code
`temporarily_unavailable`, behauptet keinen leeren Zustand und schreibt eine Logzeile nur
mit dem Fehlertyp. Schalter wie
`DECK_ASSISTANT_OLLAMA`, `DECK_ASSISTANT_WEB` und `DECK_ROUTINE_WAKE` erscheinen nur als
Zustand (aus, unvollständig, an); Adressen, Schlüssel und Modellkennungen werden nie
gerendert.

Lanes: Owner-Entscheid vom 07.11.2026, agy ist die vierte Lane. Die Kachel zeigt 4 und
darunter, wie viele Lanes laut Gesundheitsdaten im Deck angebunden sind. agy steht als
vierte Zeile ohne Daten mit «eigene Lane · im Deck noch nicht angebunden»; Worker-Enum,
Schema und Runner bleiben unverändert, bis ADR und Anbindung vorliegen.

Offene Punkte zählen nur Warnungen; jede Lane-Warnung nennt Lane und Grund. Reine
Konfigurationszustände (ein ausgeschalteter Schalter, keine Restore-Probe) und der feste
agy-Hinweis stehen eingeklappt unter «Hinweise» und zählen nicht. Am Seitenende steht neben
«Schema» eingeklappt der «Projekt-Stand · statisch, keine Live-Daten (Stand 10.11.2026)»;
er nennt nur, was das Repository belegt, keine PR-Nummern.

Die Assistent- und Meldungskarten nennen Fakten aus Code-Konstanten: Kontextbudget aus
`DECK_ASSISTANT_OLLAMA_NUM_CTX` und `..._NUM_PREDICT` (Standard 4096 und 400), Dialog im
Browser per Server-Sent Events, Denkphase aus, Verhalten bei Ollama-503 (bis zu vier
Versuche, dann «Ollama ausgelastet.»), Router-Golden-Set nur offline. Sie stehen unter
«Loopback-Dialog, wenn eingeschaltet»; beim Keep-alive nennt die Karte nur den Standard
(«bleibt geladen, Standard 30m») oder «eigener Wert gesetzt», weil ein eigener Wert auch
`0` sein kann. Antwortzeiten stehen
nur mit Messdatum und Quelle (`docs/ASSISTENT.md`) und ohne Zusage da. Meldungen: höchstens
drei Sendeversuche, wiederholt nur bei HTTP-Status ausser 200, ohne Antwort kein automatischer
Neuversand, nur `cloud_ok`, Rückweg über das lokale Quittungskommando.

Die Netzkarte (nur ab 861 Pixeln) ist ein statisches Inline-SVG ohne Skript; die Zustände
stehen als Text in einer Liste darunter. Fehlen Lane-Daten, heisst der Zustand «nicht
verfügbar», nie «aus». Anbieter-Kontingente, Backup-Uhrzeit, Offsite-Status, Modellname und
Commit-Stand zeigt die Übersicht bewusst nicht.

Die Assistentenseite beginnt mit dem Dialog: Nachricht, Datenklasse, «Mehr Optionen» mit den
drei Kästchen und «Dialog senden» als einzigem gefüllten Button. Erklärtexte, Kanal, Profil,
neue Karte, Import und die erweiterten Suchoptionen sind eingeklappt.

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

Die Token-Namen bleiben stabil, die Werte folgen der Command-Center-Palette:
`--color-bg`, `--color-side`, `--color-panel`, `--color-raised`, `--color-text`,
`--color-text-2`, `--color-muted`, `--color-line`, `--color-control-border`,
`--color-accent`, `--color-brand`, `--color-action` (Primärbutton, weisse Schrift),
`--color-on-warning` und `--color-on-danger` (Schrift auf `mark` und `.danger`), die
Verlaufstöne `--color-panel-*`, `--color-tile-*`, `--color-note-*`, `--color-step-*` sowie
`--color-on-brand`; ausserhalb von `:root` stehen keine festen Hex-Werte,
`--radius`, `--side-width` und `--tabbar-height`. Ein Unit-Test rechnet die
WCAG-Kontraste dieser Werte nach (Text mindestens 4,5:1, Feldrahmen mindestens 3:1).
Gewählt ist die Systemschrift: Die CSP hat kein `font-src`, `default-src 'none'` würde
also auch selbst gehostete Webfonts blockieren. Icons sind Inline-SVG aus
`partials/icons.html`. Unter `prefers-reduced-motion: reduce` entfallen Übergänge und
Hover-Versatz.

Chromium-Smokes verwenden eine isolierte temporäre App, eine ausdrückliche `_test`-DB,
ein synthetisches Passwort und Fake-Worker. Bei 390 Pixeln mit JavaScript und 1024 Pixeln
ohne JavaScript prüfen sie Login, Upload, Start-Freigabe, simulierten Lauf, Review, `done`,
Chat-Entwurf, Logout, globale Abmeldung zweier Browser-Sessions sowie globale Sperre
und lokale Entsperrung. Sie prüfen ferner fehlende externe Requests, horizontales Scrollen,
semantische Überschriften und einen CSS-Variablenwechsel ohne Logikänderung.

`tests/integration/test_ui_shell_browser.py` prüft die Shell bei 320, 390 und 1024 Pixeln:
kein Quer-Scrollen, ein `h1`, Leiste oder Seitenleiste je Breite, Tap-Ziele, sichtbarer
Fokus, Login ohne überdeckende Elemente und reduzierte Bewegung. Die optionale
WebKit-Vorschau `tests/integration/test_ui_preview_webkit.py` schreibt Bildschirmfotos von
Login, Übersicht und Assistent bei 390 × 844 nach `DECK_UI_PREVIEW_DIR` (sonst in das
pytest-Temp-Verzeichnis) und überspringt ohne installiertes WebKit.
`tests/integration/test_ui_fold_browser.py` misst bei 390 × 844 den Falz (Übersicht: `h1`
bis 150, Hauptaktion bis 260, Status-Leiste bis 430, offene Punkte ab höchstens 450,
Seitenhöhe bis 2000; Assistent: «Nachricht» ab höchstens 380, «Dialog senden» bis 740,
Seitenhöhe bis 2200), prüft mit `elementFromPoint`, dass das geöffnete Menü auf
`/ui/overview` und `/ui/lanes` oben liegt, und bei 340 Pixeln Höhe, dass die untere Leiste
deckend ist und bei Fokus im Feld verschwindet. Alles ist Emulation, kein Ergebnis auf einem
echten iPhone.

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
