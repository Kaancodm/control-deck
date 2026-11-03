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
- Fuenf Fehler in 15 Minuten drosseln gemeinsam das Owner-Konto. 20 Fehler in 24 Stunden
  sperren weitere Logins dauerhaft; Neustart entsperrt nicht. Nur die ausdrueckliche lokale
  Aktion `make unlock-login` hebt die Sperre auf. Die UI zeigt HTTP 423 ohne Passwortformular.
- «Ueberall abmelden» verlangt Owner-Session, Origin-Pruefung und explizites CSRF. Der Dienst
  sperrt Login-State vor Sessions und widerruft alle vorhandenen Sessions atomar. UI und API
  behalten ihre getrennten Rechte; Browser-Sessions erlauben keine Bearer-API-Mutationen.
- Sichere `auth_events` enthalten nur Typ, Zeitpunkt, ID und Anzahl betroffener Sessions.
  Passwort, Hash, Cookies und Client-Adressen erscheinen nicht darin. Wiederholte Drosselung
  wird begrenzt auditiert. Downgrade verweigert das Entfernen vorhandener Schutzdaten.

## Pruefungen

Gezielte DB-, HTTP- und Parallelitaetstests pruefen Passwortwechsel, Session-Ablauf, globale
Sperre ueber einen App-Neustart hinweg, lokale Entsperrung, CSRF/Origin und Sperrreihenfolge.
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
