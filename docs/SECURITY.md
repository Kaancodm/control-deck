# Schutzgrenzen und sichere Konfiguration

Etappe 15 ist noch in Arbeit. Diese Beschreibung trennt pruefbaren Repo-Code von der
spaeteren Inbetriebnahme. Sie ersetzt weder das externe Review noch eine Betriebsfreigabe.

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
CSRF-Werte, nur Digests in der DB, sichere Cookies und eine gemeinsame Login-Drosselung.
Owner-Bearer-Tokens bleiben von UI-Sessions getrennt. Eine Browser-Session autorisiert keine
Bearer-geschuetzten API-Mutation. Jeder Worker verwendet seinen eigenen Lane-Token; die Lane
kommt aus der Authentifizierung und kann nicht durch den Request gewechselt werden.

Jede UI-Mutation verlangt einen ausdruecklich uebermittelten sessiongebundenen CSRF-Wert.
Chromium kann bei einem Formular unter `no-referrer` die Origin als `null` senden. Nur fuer
UI-Formulare mit `Sec-Fetch-Site: same-origin` darf dieser Fall zur anschliessenden CSRF-Pruefung
weiterlaufen. API-Requests erhalten diese Ausnahme nicht. Header allein erteilen keine Rechte.

Session-Aktivitaetsgrenze, Passwortwechsel und dauerhafte Login-Sperre werden in dieser
Etappe noch ergaenzt. Die abschliessenden Tests stehen erst nach dem Rebase auf Etappe 14 fest.

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
