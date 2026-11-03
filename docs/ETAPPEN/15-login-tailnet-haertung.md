# Etappe 15 - Login, Tailnet-Bindung, Haertung

Status: ABGELEITET AUS PLAN v1.1 (Drive, 03.11.2026); kompakt; nicht startklar, bis Etappe 14 gemergt ist
Branch-Vorschlag: `stage/15-login-haertung`
Abhaengig von: Etappe 11 (Session-Modul, CSRF, Header), Plan-Frage 9 (Passkeys)
Entscheidungen: neue Abhaengigkeit `argon2-cffi` (im PR begruenden)
Reviewer: Codex (ungerade Etappe)
Abnahme laut Plan v1.1: Port-Scan von aussen leer; Login-Rate-Limit greift.

## 1 Ziel

API und UI sind nur im Tailnet erreichbar und zusaetzlich durch einen Login geschuetzt (ein
Benutzer, Argon2id, Session-Cookie, CSRF, Rate-Limit). Secrets liegen in `.env` mit Rechten 600
ausserhalb des Repos. Der Zwischen-Login aus Etappe 11 wird ersetzt.

## 2 Ausgangslage

| Punkt | Stand |
|-------|-------|
| Zwischen-Login mit Owner-Token, `app/ui/session.py`, CSRF, Sicherheits-Header | Etappe 11 |
| `DECK_BIND_HOST`, `DECK_SECRET_KEY` als Platzhalter | belegt (`.env.example`) |
| Bearer-Tokens `DECK_WORKER_TOKEN`, `DECK_OWNER_TOKEN` fuer Maschinenzugriff | PR #5 (Annahme) |
| Plan Abschnitt 9: Bindung ausschliesslich auf die Tailnet-Adresse; Login als zweite Schicht | Plan v1.1 |

## 3 Scope

**In:** Passwort-Login, Hash-Werkzeug, Session-Pruefung, Rate-Limit, Bindungspruefung beim Start,
`.env`-Rechtepruefung, Sicherheitsabgleich der Header, `docs/SECURITY.md` (Grundlage fuer Etappe 18).

**Out:** Mehrbenutzer, Passkeys (Plan-Frage 9; Vorbereitung siehe 10), TLS-Terminierung (Tailnet
liefert verschluesselten Transport; HTTPS ueber Tailnet-Funktionen ist Betrieb, Etappe 16), WAF.

## 4 Default-Entscheidungen

| Nr | Entscheidung |
|----|--------------|
| D1 | Ein Benutzer. Passwort-Hash in `DECK_OWNER_PASSWORD_HASH` (Argon2id, Parameter: Speicher 64 MB, Iterationen 3, Parallelitaet 1); erzeugt mit `make set-password` (liest das Passwort ohne Echo, gibt nur den Hash aus). Kein Klartext in Dateien oder Logs. |
| D2 | Session wie Etappe 11 (HMAC mit `DECK_SECRET_KEY`), Ablauf 12 h, Verlaengerung bei Aktivitaet, Abmelden ueberall. Der Token-Login der UI entfaellt; `DECK_OWNER_TOKEN` bleibt nur fuer die API. |
| D3 | Rate-Limit: 5 Fehlversuche je 15 Minuten je Client-Kennung (Tailnet-Adresse aus der Verbindung, nie aus Headern), danach 429 mit Wartezeit; ab 20 Fehlversuchen in 24 h globale Sperre des Logins bis Neustart oder `make unlock-login`; jeder Fehlversuch und jede Sperre wird Event `auth.*` (ohne Passwort). |
| D4 | Bindung: `DECK_BIND_HOST` muss gesetzt sein und entweder Loopback oder eine Adresse einer Tailnet-Schnittstelle sein (Pruefung ueber die Schnittstellenliste des Systems); Wildcard-Bindung (alle Schnittstellen) wird verweigert; der Start bricht mit klarer Meldung ab. Kein Adress-Literal im Repo. |
| D5 | Startpruefung `.env`: Rechte 600 und Eigentuemer gleich Dienstnutzer, sonst Warnung im Log (Fehler ab Etappe 16 im Betriebsmodus). |
| D6 | Header-Abgleich: CSP, `nosniff`, `Referrer-Policy`, `X-Frame-Options: DENY`, `Cache-Control: no-store` fuer alle `/ui`- und `/api`-Antworten; Cookies `HttpOnly`, `SameSite=Strict`, `Secure`, wenn nicht Loopback. |
| D7 | Vergleich von Hashes und Tokens in konstanter Zeit; Fehlermeldungen unterscheiden nicht zwischen falschem Benutzer und falschem Passwort. |
| D8 | `docs/SECURITY.md`: Bedrohungen (Tailnet-Geraet kompromittiert, Container-Ausbruch, Payload-Leck, Secrets in Logs), Gegenmassnahmen mit Verweis auf Etappen, offene Punkte. |

## 5 Umsetzung

| Pfad | Aenderung |
|------|-----------|
| `app/ui/auth.py`, `app/ui/session.py`, `app/ui/ratelimit.py` | Login, Session, Rate-Limit |
| `app/core/bind.py` | Bindungspruefung, Aufruf in `app/api/app.py` |
| `scripts/set_password.py`, `Makefile` (`set-password`, `unlock-login`) | Werkzeuge |
| `pyproject.toml` | `argon2-cffi` mit Begruendung |
| `tests/unit/test_auth.py`, `tests/unit/test_bind.py`, `tests/integration/test_login_flow.py` | Tests |
| `docs/SECURITY.md` (neu), `docs/UI.md`, `.env.example` | Doku, `DECK_OWNER_PASSWORD_HASH` |

## 6 Abnahmetests

| Nr | Fall | Erwartet |
|----|------|----------|
| 1 | Richtiges Passwort | Session gesetzt, Weiterleitung auf `/ui/tasks` |
| 2 | Falsches Passwort fuenfmal | 429 ab dem sechsten Versuch, Events vorhanden, Wartezeit sichtbar |
| 3 | Zwanzig Fehlversuche | globale Sperre, `make unlock-login` hebt auf |
| 4 | Wildcard-Bindung in `DECK_BIND_HOST` | Start verweigert |
| 5 | Loopback und Tailnet-Adresse (Stub der Schnittstellenliste) | Start erlaubt |
| 6 | Cookie-Attribute und Header auf allen Routen | vollstaendig |
| 7 | Passwort in Log oder Event | 0 Treffer (Leck-Test) |
| 8 | Owner: Port-Scan von einem Geraet ausserhalb des Tailnets | keine offenen Ports des Decks (Protokoll im PR) |

## 7 Definition of Done

`make lint`, `make test` gruen; Owner-Protokoll zu Test 8; `docs/SECURITY.md` vorhanden;
PR-Template mit Reviewer **Codex**, Abhaengigkeit begruendet, "Bewusst offen".

## 8 Blocker-Regel

Stoppen und melden, wenn: Etappe 11 keinen austauschbaren Pruefschritt in `session.py` liefert;
der Dienst auf dem Zielsystem keine Tailnet-Schnittstelle sieht; Plan-Frage 9 mit "nur Passkeys"
beantwortet wird (dann anderer Umfang).

## 9 Risiken

| Risiko | Gegenmassnahme |
|--------|----------------|
| Tailnet-Geraet kompromittiert | Login als zweite Schicht, Rate-Limit, Sessions begrenzt |
| Fehlkonfigurierte Bindung | Startpruefung D4 |
| Schwaches Passwort | Mindestlaenge 12 im Hash-Werkzeug, Hinweis in Doku |

## 10 Bewusst offen

- Plan-Frage 9: Passkeys zusaetzlich oder statt Passwort; Session-Modul laesst einen zweiten Faktor zu.
- Zwei-Faktor, Mehrbenutzer: nicht in v1.
- Abgleich mit Plan v1.1 bei Start der Etappe.
