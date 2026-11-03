# Etappe 15 - Login und HTTP-Haertung

Branch: `stage/15-hardening`, Draft-PR [#30](https://github.com/Kaancodm/control-deck/pull/30),
gestapelt auf Etappe 14. Die Wiederaufnahme folgt dem Checkpoint vom 03.11.2026; ein Merge
des Vorgaengers ist fuer den autorisierten Draft-Stapel nicht erforderlich. Kein Agent-Merge.

## Ergebnis und verbindlicher Vertrag

- Die vollstaendige Owner-UI aus Etappe 14 bleibt erhalten. Die aeusserste HTTP-Middleware
  begrenzt Body, Zeitbudget, Header und Request-Ziel vor jeder Handler-Ausfuehrung.
- Bindung verlangt ausdrueckliches Loopback oder eine tatsaechliche Tailnet-Schnittstelle;
  Host-Liste und optionale feste Public-Origin werden validiert. Forwarding-Header sind
  keine Autoritaet. Launcher-Konfiguration kann mit `--check-only` ohne Listener geprueft werden.
- Passwort-Login verwendet die bestehende Argon2id-Grundlage. Migration `0014` bindet Sessions
  an den Passwort-Hash und widerruft alte authentifizierte Sessions. 30 Minuten Inaktivitaet
  und hoechstens 12 Stunden absolute Laufzeit; Aktivitaet verlaengert die absolute Grenze nie.
- Rate-Limit gemaess D3: Fuenf Fehlversuche je 15 Minuten je Client-Kennung. Die Kennung ist
  die Peer-Adresse der Verbindung (ASGI-Scope), nie ein Wert aus Headern wie `X-Forwarded-For`;
  gespeichert wird nur ihr Digest (Migration `0014`, Tabelle `ui_login_clients`). Danach
  antwortet die UI mit HTTP 429 und `Retry-After`. Ab 20 Fehlversuchen in 24 Stunden ueber
  alle Clients ist der Login global gesperrt, bis der Dienst neu startet oder die lokale
  Aktion `make unlock-login` laeuft. Der Launcher hebt die Sperre beim Prozessstart auf.
  Die UI zeigt HTTP 423 ohne Passwortformular.
- «Ueberall abmelden» verlangt Owner-Session, Origin-Pruefung und explizites CSRF. Der Dienst
  sperrt Login-State vor Sessions und widerruft alle vorhandenen Sessions atomar. UI und API
  behalten ihre getrennten Rechte; Browser-Sessions erlauben keine Bearer-API-Mutationen.
- Sichere `auth_events` enthalten nur Typ, Zeitpunkt, ID und Anzahl betroffener Sessions.
  Passwort, Hash, Cookies und Client-Adressen erscheinen nicht darin; jeder Fehlversuch, jede
  Drosselung und jede Sperre wird als `auth.*`-Event festgehalten. Wiederholte Drosselung
  wird je Client und Fenster einmal auditiert. Downgrade verweigert das Entfernen vorhandener
  Schutzdaten.

## Pruefungen

Gezielte DB-, HTTP- und Parallelitaetstests pruefen Passwortwechsel, Session-Ablauf, Drosselung
je Client ohne Einfluss von Forwarding-Headern, globale Sperre ueber Clients hinweg, Aufhebung
der Sperre im Startpfad des Launchers, lokale Entsperrung, CSRF/Origin und Sperrreihenfolge.
Chromium prueft die Owner-Ablaufe bei 390 Pixeln mit und 1024 Pixeln ohne JavaScript,
einschliesslich globaler Abmeldung zweier Browser-Sessions und erneuter Anmeldung nach Entsperrung.

Pflichtchecks sind `make lint`, `uv run alembic -x target=test check`, `make test`,
`make reference`, der Ollama-Fake-CLI-Referenzmodus und die separate KB-Migration samt
`uv run alembic -c alembic_kb.ini check`. Die finalen Testzahlen und CI-Nachweise stehen in
`docs/STAND.md` und im PR. Es werden keine inhaltlichen Owner-Referenzfaelle ergaenzt.

## Inbetriebnahme

Offen bleiben reale Tailnet-/TLS-Bindung, externe Erreichbarkeit, sichere private Konfiguration,
Betriebsrechte und echte Login-Einrichtung. Keine Netzwerk-Scans, echten Logins,
Anbieter-/Modellaufrufe, API-Keys, produktiven DB-Zugriffe oder Serveraenderungen.

Mehrbenutzer, Passkeys und Zwei-Faktor sind nicht Teil dieser Etappe. Keine neue Abhaengigkeit:
Argon2id und Uvicorn stammen aus Etappe 11. Externes Review erfolgt durch einen anderen
Anbieter; die Inbetriebnahme und die Gesamt-Abnahme bleiben gesondert offen.
