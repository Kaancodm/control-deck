# Schutzgrenzen und sichere Konfiguration

Etappe 15 integriert die HTTP- und Authentifizierungsschutzgrenzen in die Owner-Oberflaeche.
Diese Beschreibung trennt pruefbaren Repo-Code von der spaeteren Inbetriebnahme. Sie ersetzt weder das externe Review noch eine Betriebsfreigabe.

## HTTP-Eingang

`python -m app.api.server_main` prueft die Konfiguration vor dem Listener-Start.
`--check-only` prueft dieselben Bedingungen ohne Listener. `DECK_BIND_HOST` ist Pflicht:
eine ausdrueckliche IP-Adresse der Loopback-Schnittstelle oder eine tatsaechliche Adresse
der lokalen Tailnet-Schnittstelle. DNS-Namen und Wildcard-Bindungen werden abgewiesen.
Der ASGI-Stack prueft diese Konfiguration ebenfalls beim Start.

`DECK_ALLOWED_HOSTS` enthaelt eine ausdrueckliche kommaseparierte Liste ohne Ports und
Wildcards. Fremde, doppelte oder ungueltige Host-Header werden vor dem Handler abgewiesen.
`DECK_BIND_PORT` liegt zwischen 1 und 65535. Die Beispielkonfiguration enthaelt keine
Betriebsadressen; der Owner traegt die Werte ausserhalb des Repos ein.

Der Launcher vertraut keinen Proxy-Headern, deaktiviert Access-Logs, begrenzt parallele
Verbindungen und aktiviert keine WebSockets. Die Middleware entfernt Forwarding-Metadaten.
Ein separat eingerichteter TLS-Proxy kann eine feste `DECK_PUBLIC_ORIGIN` erhalten. Diese
muss zur Host-Liste passen; ihre Herkunft wird niemals aus weitergereichten Headern abgeleitet.
Ohne feste Origin wird die tatsaechliche ASGI-Scheme-/Host-Kombination verwendet.

Der gesamte Request-Body wird vor jedem Anwendungs-Handler begrenzt: maximal 6 MiB und
10 Sekunden. Das gilt auch fuer Chunked-Requests und fehlende oder falsche Laengenangaben.
Doppelte beziehungsweise widerspruechliche Laengen- und Sicherheits-Header werden abgewiesen.
Es gelten zusaetzlich maximal 100 Header und 16 KiB fuer Header-Namen und -Werte.
Ein abgebrochener oder zu grosser Request kann deshalb keine Task-Mutation ausloesen.

UI und API liefern `no-store`, CSP, `nosniff`, `DENY` und eine restriktive Referrer-Policy.
Unbehandelte Fehler und Datenbankfehler der Readiness-Pruefung geben nur sichere Fehlercodes
aus. Konfiguration, SQL, Parameter und Rohtexte werden nicht in Fehlerantworten gespiegelt.

## Anmeldung und Rechte

Die Passwort-/Session-Grundlage stammt aus Etappe 11: Argon2id, zufaellige Session- und
CSRF-Werte, nur Digests in der DB, sichere Cookies und eine Login-Drosselung.
Owner-Bearer-Tokens bleiben von UI-Sessions getrennt. Eine Browser-Session autorisiert keine
Bearer-geschuetzten API-Mutation. Jeder Worker verwendet seinen eigenen Lane-Token; die Lane
kommt aus der Authentifizierung und kann nicht durch den Request gewechselt werden.

Jede UI-Mutation verlangt einen ausdruecklich uebermittelten sessiongebundenen CSRF-Wert.
Chromium kann bei einem Formular unter `no-referrer` die Origin als `null` senden. Nur fuer
UI-Formulare mit `Sec-Fetch-Site: same-origin` darf dieser Fall zur anschliessenden CSRF-Pruefung
weiterlaufen. API-Requests erhalten diese Ausnahme nicht. Header allein erteilen keine Rechte.

Migration `0014` bindet authentifizierte Sessions an einen Fingerprint des aktuellen
Passwort-Hashes. Ein Wechsel macht alte Sessions ungueltig. Nach 30 Minuten ohne Aktivitaet
endet eine Session; Aktivitaet verlaengert niemals die absolute Grenze von 12 Stunden.
Die Migration widerruft bisherige authentifizierte Sessions ohne diese Bindung.

Die Drosselung zaehlt fuenf Fehler in 15 Minuten je Client-Kennung. Die Kennung ist die
Peer-Adresse der Verbindung aus dem ASGI-Scope; `Forwarded`- und `X-Forwarded-*`-Header werden
nie herangezogen. Migration `0014` speichert je Client nur einen SHA-256-Digest der Adresse,
Zaehler und Fensterbeginn (`ui_login_clients`). Zusaetzlich sperren 20 Fehler innerhalb von
24 Stunden ueber alle Clients den Owner-Login global. Nur `make unlock-login` hebt diese
Sperre auf. Der Launcher `app.api.server_main` liest und protokolliert den Sperrstatus vor
dem Oeffnen des Listeners; Sperre, Tagesbudget und Fensterbeginn bleiben bei jedem Start
unveraendert. Ist die Datenbank nicht erreichbar, startet der Dienst nicht. Die ausdrueckliche
lokale Verwaltungsaktion setzt auch alle Client-Fenster zurueck und auditiert die Entsperrung. `make set-password` liest zweimal ohne Echo und gibt ausschliesslich einen
Argon2id-Hash aus; unsichere Terminal-Fallbacks und Passwortargumente werden abgewiesen.

`auth_events` speichert nur Typ, Zeitpunkt, ID und Anzahl betroffener Sessions. Keine
Passwoerter, Cookies, CSRF-Werte oder Client-Adressen. Wiederholte abgewiesene Aufrufe im selben
Drosselungsfenster eines Clients erzeugen nicht immer neue Ereignisse. Ein Downgrade darf
bestehende Auth-Ereignisse, aktive Schutzdaten oder laufende Client-Fenster nicht entfernen.

Die Aktion «Ueberall abmelden» widerruft alle bestehenden Sessions einschliesslich anonymer
Login-Challenges. Sie verlangt eine gueltige Owner-Session und ausdruecklichen CSRF-Wert.
Login und globale Abmeldung sperren immer zuerst den Login-State und erst danach Sessions;
die HTTP-Anbindung nimmt vorher keine Aktivitaetssperre. Der Widerruf wird vor der Antwort
committed und genau einmal auditiert.

Die globale Login-Sperre erscheint mit HTTP 423, ohne Zeitversprechen und ohne
Passwortformular. Eine vorhandene gueltige Owner-Session bleibt nutzbar. Erst die lokale
Entsperrung erlaubt weitere Anmeldungen; ein Neustart aendert daran nichts. Die UI-CSRF-Pruefung
verwendet dieselbe validierte feste Proxy-Origin wie die aeusserste HTTP-Schutzmiddleware.

HTTP-Regressionen pruefen Passwortwechsel, Session-Ablauf, Drosselung je Client ohne Einfluss
gefaelschter Forwarding-Header, globale Sperre, Erhaltung im Startpfad und lokale Entsperrung,
zwei Browser-Sessions, gefaelschte CSRF-/Origin-Werte und die Sperrreihenfolge waehrend einer
konkurrierenden Transaktion. Chromium prueft globale Abmeldung und Sperranzeige bei
390 und 1024 Pixeln. Konkrete Gesamtnachweise stehen in `STAND.md` und im Draft-PR.

Migration `0017` begrenzt aktive anonyme Sessions auf fuenf je Client-Digest, insgesamt
weiterhin auf 1000. GET `/ui/login` erlaubt je Client 20 Aufrufe pro Minute, auch bei
wiederverwendetem Cookie. Kapazitaets- und Ratenlimits antworten mit HTTP 429 und `Retry-After`.
Eine Flut desselben Clients belegt dadurch nicht das ganze anonyme Sitzungsbudget. Clients
hinter derselben Verbindung teilen diese Grenzen; dies ist kein Schutz vor beliebig vielen
verteilten Absendern. Nur Digests, Zaehler und Fensterzeiten werden gespeichert.

Argon2 laeuft ohne offene DB-Transaktion und ohne gehaltene Zeilensperren. Eine kurze
Vorpruefung schuetzt den Einstieg; nach der Passwortpruefung werden Session, CSRF, Ablauf,
globale Sperre sowie beide Fehlerbudgets unter denselben Locks erneut geprueft. Erst dann
wird ein Ergebnis gezaehlt oder eine Owner-Session ausgestellt. UI-Handler mit synchronem
DB-Zugriff laufen im Threadpool; Formularlesen und Upload-Bereinigung bleiben asynchron.

## Private Konfiguration

Wird `DECK_ENV_FILE` angegeben, muss die geoeffnete Datei regulaer, im Besitz des Dienstnutzers,
mit genau Modus 600 und ohne weitere Hardlinks sein. Symlinks werden abgewiesen. Die Pruefung
liest keine Dateiinhalte und laedt keine Zugangsdaten nach. Fehler enthalten keine Pfade oder
konfigurierten Werte. Reale Tokens und Passwort-Hashes gehoeren nicht ins Repo.

Unsichere UI-Cookies sind nur bei ausdruecklichem Loopback-Betrieb erlaubt. Die sichere
Voreinstellung bleibt aktiv. HTTPS-Konfiguration und Cookie-Verhalten am Ziel muessen
separat geprueft werden.

## Inbetriebnahme

Nicht ausgefuehrt: externe Port-Scans, Tailnet-/TLS-Konfiguration, echte Logins, Anbieteraufrufe,
API-Keys, Serverinstallation und produktive Datenbankverbindungen. Tests verwenden lokale
Konfiguration, simulierte Schnittstellen und synthetische Anmeldedaten. Ein beliebig anders
konfigurierter externer ASGI-Launcher ist kein Nachweis fuer die sichere Bindung; die
Betriebsvorlagen verwenden den validierten Einstiegspunkt.

Vor der Aktivierung prueft der Owner die tatsaechliche Erreichbarkeit, TLS-Terminierung,
privaten Dateirechte und Betriebsidentitaet. CLI-Profile bleiben bis zu ihren getrennt
nachgewiesenen Isolations- und Tool-Grenzen nicht verfuegbar. Die Abnahme in Etappe 18 wird
nur als Checkliste vorbereitet.
