# Bauauftrag 06-c-betriebsdoku

## Etappe

Ticket 6 / C: Betriebsdoku an den geprueften Code und die Owner-Entscheide anpassen.
Builder: Cursor. Externes Review: SuperGrok (nur Code); noch ausstehend. Status: nur geplant.
Nachtrag 2026-11-11: Codex und Gemini Pro sind nicht mehr im Einsatz. Ablauf laut ADR 0008
(Nachtrag): Cursor baut, SuperGrok reviewt nur den Code, der Owner sagt einmal Ja, nur
Grok Bot mergt. Die aeltere Zuweisung an Codex und Fable gilt nicht mehr.
Umsetzungsbranch: `stage/06-c-betriebsdoku`; Start erst nach gruenem B, damit A und B belegbar sind.

## Ziel

`docs/OPERATIONS.md` beschreibt korrekt, was die Betriebsbefehle leisten und was sie nicht
pruefen. Der iPhone-Pfad ist eindeutig: Variante A mit HTTPS, fester Public-Origin und
bewusst gesperrten Runnern. Die Doku behauptet keine erfolgreiche Inbetriebnahme.

## Rahmen

- Reihenfolge A -> B -> C -> D; C gruen = Abnahme, CI und Review durch SuperGrok ohne offene Befunde.
- Danach erst D; Server-Schritte 1–15 fuehrt anschliessend der Owner einzeln aus, nicht der Builder.
- Der Owner sagt einmal Ja, danach mergt nur Grok Bot. Dieser Bauauftrag aendert ausschliesslich `docs/OPERATIONS.md`.
- Blocker 1 = (b) fuer den iPhone-Test; (c) ist Ticket A. Ohne bestaetigten Runtime-KB-Vertrag
  kein Deck-Backup/Update; vorhandene B2-Abdeckung der zentralen DB muss nachgewiesen werden.
- Blocker 2 bleibt offen bis B2-Timername, Manager und Kollisionsentscheidung vorliegen.
  Niemals einen vermuteten bestehenden Timer deaktivieren/umbenennen; keine Server-Anleitung
  als startklar bezeichnen, solange die Entscheidung fehlt.
- Blocker 3 = Variante A: Loopback-Bindung, HTTPS ueber Tailscale, kein Funnel, sichere Cookies.
- Keine konkreten Hostnamen, IPs, Secrets oder externen Dokument-IDs; nur private Platzhalter.
- de-CH ohne Eszett; keine neuen Abhaengigkeiten, Runtime-Aenderungen oder Serverzugriffe.

## Schritte (atomar, je ein Commit)

1. `docs/OPERATIONS.md`: falsche Secret-Key-Anforderung entfernen. Die Anwendung verwendet
   keinen `DECK_SECRET_KEY`. Bearer-API (`DECK_OWNER_TOKEN`), UI (`DECK_OWNER_PASSWORD_HASH`)
   und getrennte Runner-Datei erklaeren, ohne Werte auszugeben. Nur tatsaechlich verwendete
   Variablen nennen; private Env-Dateien nie per Shell-source laden.
2. `docs/OPERATIONS.md`: bestehende Make-Dry-runs von B unterscheiden. Ohne `--apply` liefern
   die Make-Einstiegspunkte nur `dry_run`, keinen gerenderten Installationsplan und keine
   Konfigurationspruefung. B prueft dagegen lesend und zeigt eine Vorschau; keine erfundenen Checks.
3. `docs/OPERATIONS.md`: private Konfigurations- UND Unit-Verzeichnisse mit Modus `0700`,
   Env-/Runner-Dateien mit `0600`, Eigentuemer sowie Symlink-/Hardlink-Regeln festhalten.
   `install --apply` aktiviert Timer; `make start` startet API, Runner und Timer gemeinsam.
   Fuer 1(b) deshalb B mit explizitem API-Start verwenden; keine Reaktivierung deaktivierter
   Deck-Timer und kein Eingriff in den bestehenden B2-Lauf.
4. `docs/OPERATIONS.md`: nur Variante A als iPhone-Zugang beschreiben. `DECK_PUBLIC_ORIGIN`
   ist fuer diesen HTTPS-Proxy-Pfad Pflicht; `DECK_ALLOWED_HOSTS` passt zum privaten externen
   Namen, Cookies bleiben Secure. Keine direkte Tailnet-HTTP-Variante als nutzbare Alternative.
   Origin-/Host-Fehler, Zertifikate, User-Manager-Sandbox und Funnel-Ausschluss als Checks benennen.
5. `docs/OPERATIONS.md`: A und B samt festen Gates referenzieren. 1(b) und 1(c) sauber trennen:
   Code fuer Runtime-Migration ist noch kein angewandter KB-Vertrag. Anonyme Health-Antwort
   prueft keine DB; Restore-Timer ist nur Trockenlauf, kein Restore-Nachweis. Kein automatischer
   Schema-Rollback. Befehle und Rechte gegen den nach A/B tatsaechlichen Code abgleichen.

## Abnahme (ausfuehrbare Befehle)

| Befehl | Erwartet |
|---|---|
| `! rg -n -e 'DECK_SECRET_KEY' -e 'Secret Key' -e 'Variante B' docs/OPERATIONS.md` | keine Treffer fuer die gestrichenen Anforderungen/Alternative |
| `rg -q 'DECK_PUBLIC_ORIGIN' docs/OPERATIONS.md && rg -q '0700' docs/OPERATIONS.md && rg -q 'dry_run' docs/OPERATIONS.md && rg -q 'Inbetriebnahme' docs/OPERATIONS.md` | alle vier Themen mit korrekter Bedeutung vorhanden |
| `uv run python -m app.operations.service install` | nur JSON mit `action=install`, `status=dry_run`; kein systemctl/Schreiben |
| `uv run python -m app.operations.main restore-probe` | JSON `status=dry_run`; keine DB-Verbindung oder Wiederherstellung |
| `uv run pytest tests/unit/test_operations_service.py tests/unit/test_deployment_config.py tests/unit/test_server_main.py -q` | vorhandene Vertraege gruen; keine echten Dienste/Listener |
| `make lint && make test && make reference` | alle gruen; Testzahlen/Skips offen ausweisen |
| `git diff --check && git diff --name-only "${TICKET_BASE:?}...HEAD"` | exit 0; gegen den dokumentierten C-Basis-Commit nur `docs/OPERATIONS.md` geaendert |

## Abbruchkriterium

Abbruch bei Widerspruch zwischen Doku und implementiertem A/B, impliziter Freigabe eines
Server-Schritts, behauptetem DB-/Restore-Erfolg ohne Nachweis, rotem Check oder offenem Review.
Nicht nebenbei Code reparieren: Befund im betroffenen Vorgaenger schliessen; D bleibt gesperrt.

## Bewusst offen

D liefert die leere PASS/FAIL-Vorlage. Reale Netz-, Sandbox-, iPhone-, DB- und Restore-Nachweise
bleiben Inbetriebnahme. Live-Adapter oder fehlende Abnahme-Werkzeuge werden hier nicht gebaut.

## Offene Fragen (TODO)

TODO Blocker 2: B2-Timername/Manager und Kollisionsentscheidung; bis dahin keine Installationsfreigabe.
TODO Inbetriebnahme: echte Zertifikats-/Origin- und Rechtepruefung durch Owner, ohne Hostwerte im Repo.
