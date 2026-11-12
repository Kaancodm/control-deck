# Bauauftrag 07-d-inbetriebnahme-checkliste

## Etappe

Ticket 7 / D: Abschnitt Inbetriebnahme mit T1–T17 als leerer PASS/FAIL-Vorlage.
Builder: Cursor. Rollen, Modelle und Merge: `AGENTS.md` (ADR 0008, Nachtrag 2026-11-12).
Der Nachtrag 2026-11-11 (SuperGrok reviewt nur den Code; Gemini Pro nicht im Einsatz) gilt nicht mehr.
Status: nur geplant.
Umsetzungsbranch: `stage/07-d-inbetriebnahme-checkliste`; Start erst nach gruenem C.

## Ziel

`docs/ABNAHME_CHECKLISTE_ENTWURF.md` enthaelt eine ausfuellbare Inbetriebnahme-Tabelle mit
Pruefmethode, Erwartung und leeren Ergebnisfeldern. Sie trennt Repo-Tests, iPhone-Betrieb und
spaetere Live-/Restore-Abnahme. Kein Feld behauptet einen bereits ausgefuehrten Server-Test.

## Rahmen

- Reihenfolge A -> B -> C -> D; D gruen = Vorlagenpruefung, CI und Review laut `AGENTS.md` ohne offene Befunde.
- Erst danach Server-Schritte 1–15 durch den Owner, jeden einzeln und erst nach gruenem
  Vorgaenger; Eingriffe brauchen dessen jeweiliges GO. Der Owner sagt einmal Ja, danach mergt nur Grok Bot.
- Gruene D-Vorlage bedeutet keine gruene Inbetriebnahme. Blocker 2 sperrt den Ausrollpfad
  weiter, bis B2-Timername/Manager bekannt und die Kollision entschieden sind.
- Blocker 1 = (b) fuer den iPhone-Test, (c) in A; Blocker 3 = Variante A mit HTTPS/fester Origin.
- Nur `docs/ABNAHME_CHECKLISTE_ENTWURF.md` aendern; kein Code, keine neuen Tests/Abhaengigkeiten,
  keine Serverzugriffe. de-CH ohne Eszett; keine Hostnamen, IPs, Secrets oder externen Dokument-IDs.
- T1–T17 bezeichnet diese Inbetriebnahme-Pruefungen. T4–T7 der separaten Lueckenliste sind
  andere Nachweise, keine Bautickets. IDs mit ihrer Liste kennzeichnen, nicht vermischen.

## Schritte (atomar, je ein Commit)

1. `docs/ABNAHME_CHECKLISTE_ENTWURF.md`: im bestehenden Abschnitt Inbetriebnahme den festen
   Ablauf A -> B -> C -> D -> Owner-Schritte 1–15 und die drei Blocker-Entscheide eintragen.
   Bestehende offenen Live-/Isolations-/Restore-Punkte und die nicht erteilte Release-Freigabe erhalten.
2. Dieselbe Datei: genau T1–T17 gemaess untenstehender Pruefliste eintragen. Spalten exakt:
   `ID`, `Pruefung`, `Befehl/Handlung`, `Erwartet`, `Ergebnis (PASS/FAIL)`, `Nachweis`, `Datum`, `Rolle`.
   Die letzten vier Zellen jeder Zeile bleiben leer. Private Werte nur als neutrale Platzhalter.
3. Dieselbe Datei: Ausfuellregeln ergaenzen. Nicht ausgefuehrt oder blockiert bleibt leer und
   wird im offenen-Punkte-Abschnitt begruendet; kein PASS fuer Auslassen oder Dry-run.
   Bei FAIL stoppt der Ablauf. Der Owner traegt spaeter Commit, Datum, Rolle und redigierten
   Nachweis ein; kein Rohjournal, Screenshot mit Zugangsdaten oder Modellinhalt ins Repo.

### Verbindlicher Inhalt der 17 Zeilen

| ID | Pruefung und Erwartung fuer die spaetere Vorlage |
|---|---|
| T1 | Safari im Tailnet erreicht Login ueber HTTPS mit gueltigem Zertifikat |
| T2 | Ausserhalb des Tailnets keine Erreichbarkeit, Funnel aus |
| T3 | API lauscht ausschliesslich auf vorgesehener Loopback-Bindung |
| T4 | Sicherheitsheader: CSP, no-store, DENY, nosniff und Referrer-Policy |
| T5 | Login erfolgreich; Cookie Secure, HttpOnly, SameSite=Strict |
| T6 | Mobile UI: Navigation, Formular, Entwurf, Freigabeansicht; kein horizontales Scrollen |
| T7 | Formular mit gueltigem CSRF/fester HTTPS-Origin erfolgreich; fremde Origin abgewiesen |
| T8 | Nach fuenf falschen Anmeldungen wird der naechste Versuch mit 429/Retry-After abgewiesen; keine globale Sperre |
| T9 | Ueberall abmelden widerruft beide Sitzungen und erzeugt Auth-Audit |
| T10 | API-only-Pfad startet keine Runner; ungeprueftes Live-Profil bleibt gesperrt. Falls separat geprueft: Exit 78 ohne Neustartschleife |
| T11 | Anonyme Health-Antwort nur ok; ausdruecklich kein DB- oder Lane-Nachweis |
| T12 | Neustart erhaelt Login-Sperre und Tagesbudget; nur explizites lokales Entsperren hebt die Sperre auf |
| T13 | Vollstaendiger Zwei-DB-Backup-Satz mit Manifest/Rechten erst nach nachgewiesenem KB-Vertrag; unter 1(b) bleibt dieser Nachweis offen |
| T14 | Restore ohne apply ist nur Trockenlauf ohne Aenderung; bestandener Dry-run beweist keinen echten Restore |
| T15 | Zentraler Schema-Check passend; KB-Check nur nach angewandtem A, sonst ausdruecklich offen |
| T16 | Redigierte Log-/Konfigurationskontrolle ohne Geheimnisse; Repo sauber |
| T17 | Timerzustand entspricht dokumentierter Entscheidung; B2 unveraendert, keine ungeklaerte Namenskollision |

Bei T12 nicht die ueberholte Aussage des externen Reviews uebernehmen, ein Neustart entsperre.
Den zur Pruefung gewaehlten Main-Stand kontrollieren; fehlt die Korrektur, ist dies ein offener
Befund und kein Anlass, die Erwartung abzuschwaechen. T10 an den API-only-Pfad aus B/C anpassen.
Unter 1(b) separaten Nachweis fuer zentralen Erstdump/B2-Abdeckung fuehren; er ersetzt T13 nicht.
Echter Restore beider DBs und Artefakte sowie Live-Adapter/Isolation bleiben weitere Abnahme-Nachweise.

## Abnahme (ausfuehrbare Befehle)

| Befehl | Erwartet |
|---|---|
| `rg -n '^## Inbetriebnahme' docs/ABNAHME_CHECKLISTE_ENTWURF.md` | bestehender Abschnitt vorhanden, keine zweite konkurrierende Checkliste |
| `uv run python -c 'import re; from pathlib import Path; p=Path("docs/ABNAHME_CHECKLISTE_ENTWURF.md"); rows=[[c.strip() for c in s.split(chr(124))[1:-1]] for s in p.read_text().splitlines() if re.match(r"^\x7c\s*T\d+\s*\x7c", s)]; assert [r[0] for r in rows] == [f"T{i}" for i in range(1,18)]; assert all(len(r)==8 and not any(r[4:]) for r in rows)'` | genau T1–T17, acht Spalten, Ergebnis/Nachweis/Datum/Rolle leer; exit 0 |
| `rg -n -e 'Blocker 1' -e 'Blocker 2' -e 'Blocker 3' docs/ABNAHME_CHECKLISTE_ENTWURF.md` | 1(b)/1(c), 2 offen und 3 Variante A korrekt eingetragen |
| `make lint && make test && make reference` | alle gruen; keine Aussage ueber bereits bestandene Inbetriebnahme ableiten |
| `git diff --check && git diff --name-only "${TICKET_BASE:?}...HEAD"` | exit 0; nur `docs/ABNAHME_CHECKLISTE_ENTWURF.md` gegen dokumentierte D-Basis geaendert |

## Abbruchkriterium

Abbruch bei fehlenden/doppelten T-IDs, vorbefuelltem PASS/Datum/Nachweis, unredigierten Werten,
Ruecknahme offener Sicherheitskriterien, rotem Check oder offenem Review. Keine Server-Freigabe.

## Bewusst offen

Die Tabelle wird erst waehrend der Owner-Pruefung ausgefuellt. `docs/ABNAHME_1_0.md` (neu, spaeter),
Lasttest, Live-Adapter, echte Isolations-/Restore-Nachweise und Release-Tag gehoeren nicht zu D.
Das Review laut `AGENTS.md` wird erst nach tatsaechlicher Rueckmeldung als bestanden eingetragen.

## Offene Fragen (TODO)

TODO Blocker 2 bleibt offen bis der B2-Timername/Manager bekannt ist; keine Schaetzung.
TODO Pruef-Commit und konkrete Nachweise legt der Owner nach gruenem A–D und geloesten Blockern fest.
