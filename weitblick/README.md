# Weitblick

Lokales Python-3.12-Skript ohne Sprachmodell und ohne Zusatzpakete.
Es liest drei Markdown-Dateien und erzeugt **Fragen, keine Tatsachen**.
Stufe 1 (Datei-Weg) arbeitet ohne Netzwerk. Stufe 2 spiegelt die Fragen
optional in die Hindsight-Bank `weitblick` (Abschnitt «Weitblick Stufe 2»);
ohne Einschalten bleibt alles wie in Stufe 1. Kein Fokus-Index, keine
Wissens-DB, kein installierter Zeitplan.

## Aufruf

Vom Repo-Hauptordner aus, mit privaten Quellen ausserhalb des Repos:

```sh
python3 -B -m weitblick --entscheide /private/listen/ENTSCHEIDE.md \
  --server /private/listen/SERVER.md --auftraege /private/listen/AUFTRAEGE.md \
  --output /private/weitblick-ausgabe
python3 -B -m weitblick --disabled
python3 -m unittest discover -s weitblick/tests -v
```

`-B` verhindert Python-Bytecode-Dateien ausserhalb der Ausgabe. Die
Quell-Dateien werden nur gelesen. Der Ausgabeordner muss getrennt von den
Quellordnern liegen; auch Unterordner und uebergeordnete Ordner sind gesperrt.
Symbolische Verknuepfungen im Ausgabepfad sowie Symlinks und Hardlinks auf
Ausgabedateien werden abgewiesen. Elternordner muessen vertrauenswuerdig
sein und duerfen waehrend des Laufs nicht von Dritten umbenannt werden.
Fuer den spaeteren Betrieb braucht der Benutzer zusaetzlich reine Leserechte
auf den Quellen. Das Skript aendert keine Betriebssystemrechte der Quellen.

Ohne `--disabled` sind alle vier Pfade Pflicht. Fehler enden mit Exit-Code 1
(bzw. 2 bei falschen Argumenten). Die Fehlermeldung gibt keine Quelldaten aus.

## Formatannahmen

- UTF-8-Markdown, eine physische Zeile je Tabellenzeile. Tabellen beginnen
  mit `|` und besitzen die Spalte `Nummer` oder `ID`. Trennzeilen sind Markdown.
  Fehlende rechte Zellen bleiben leer; zusaetzliche Zellen sind ein Fehler.
  Pipes innerhalb von Zellen muessen als `\|` geschrieben sein.
- Alternativ nummerierte Abschnitte `## E-901` oder `- **E-901: Thema**`,
  gefolgt von Feldern wie `- Thema: Suche` und `- Optionen/Empfehlung: ...`.
  Freier mehrzeiliger Fliesstext wird nicht interpretiert. Codebloecke werden
  abgewiesen. Unbekannte Felder und nicht nummerierte Notizen werden ignoriert.
- Nummern: `E-` plus Ziffern fuer ENTSCHEIDE, `S-` fuer SERVER, `A-` fuer
  AUFTRAEGE. Doppelte Nummern sind ein Fehler. Weitblick-Nummern sind keine Quellen.
- Feldnamen werden ohne Beachtung der Grossschreibung gelesen; Umlaute und
  ae/oe/ue sind gleichwertig. SERVER verwendet nur Dienst, Status und Letzte
  Pruefung, neben der Nummer und der physischen Fundstelle.
- ENTSCHEIDE/AUFTRAEGE: `Optionen/Empfehlung` mit ausdruecklichem `Option 2`
  oder Feld `Option 2`. Annahme: Diese bezeichnet die nicht gewaehlte Option.
  Das Skript ermittelt keinen tatsaechlichen Wahlentscheid aus Freitext.
  Der Rest des Felds ist der Optionstext. Fehlt er, entsteht keine Alternative.
- Verbindungen: ausdrueckliche bekannte Nummern in den erlaubten Feldern.
  Beide Fundstellen werden genannt. Keine semantische Aehnlichkeitssuche.
- Wiederholungen: mindestens zwei ENTSCHEIDE mit gleichem Thema/Titel und
  Projekt; bei AUFTRAEGE zusaetzlich Status `blockiert` und identischer,
  nicht leerer Ruecklauf. Dies ist ein Textzaehler, kein Beweis fuer Fehler.
  Fehlt Projekt, wird nur innerhalb derselben Liste gruppiert.
- Es wird kein Status `geparkt` vorausgesetzt oder eingefuehrt.
- Frage-Muster enthalten nur Nummern und Zaehler, keinen freien Quelltext.
  Ein konservativer Filter verwirft Kandidaten mit erkennbaren URLs,
  Mail-/IP-Mustern, Schluesselbezeichnungen oder Anweisungsmustern in den
  verwendeten Feldern. Das ist keine allgemeine Erkennung aller Geheimnisse.

Die Tests verwenden ausschliesslich erfundene Listen. Echte Listen und
Lauf-Ausgaben duerfen nicht in dieses oeffentliche Repository gelangen.

## Genau zehn Felder

Das verbindliche Schema steht in `schema.json`.

| Feld | Inhalt |
| --- | --- |
| id | stabiles WB-Kuerzel aus Art, Bezugsnummern und Option |
| art | nicht_gewaehlte_option, verbindung oder wiederholung |
| frage | beginnt mit «Waere denkbar, dass», endet mit ?, maximal 200 Zeichen |
| fundstelle | Liste von Datei, physischer Zeile (ab 1) und Eintragsnummer |
| datum | lokales Erzeugungsdatum im Format YYYY-MM-DD |
| bezug | Nummer des ersten betroffenen Eintrags |
| status | neu |
| nicht_gewaehlte_option | ausdruecklicher Optionstext, sonst leer |
| quelle_liste | beteiligte Quell-Listen |
| hinweis | Fragekennzeichnung und Aufforderung zur Quellenpruefung |

Verbindungen haben mindestens zwei Fundstellen; Wiederholungen nennen alle
gezaehlten Eintraege. Die Nummer bleibt bei gleicher Art und gleichem Bezug
stabil. Ohne geaenderte Option erzeugt ein weiterer Lauf keine Dublette.

## Datei-Schreiber und Grenzen

`Writer.write(entries) -> int` ist die kleine Austausch-Schnittstelle.
`FileWriter` ist der Haupt-Schreiber; `HindsightWriter` folgt in Stufe 2. Er schreibt `weitblick.jsonl` und die
abgeleitete Ansicht `weitblick.md`, mit `.lock` als lokaler Schreibsperre.
Er begrenzt einen Ausgabeordner auf drei neue Fragen pro Kalendertag und
fuenf gespeicherte Fragen insgesamt. Prioritaet: Reihenfolge der Listen und
Zeilen, Alternativen und Verbindungen vor Wiederholungsgruppen.

Es gibt noch keine Statuspflege oder Archivierung. Bei fuenf Fragen werden
weitere Kandidaten nicht aufgenommen. Der gemeinsame Ausgabeordner muss
beibehalten werden; getrennte Ordner haben getrennte Budgets.

Jede Ausgabedatei wird atomar ersetzt. JSONL ist massgebend. Ein Abbruch
zwischen JSONL und Markdown kann eine veraltete Ansicht hinterlassen; der
naechste Lauf stellt sie wieder her. Beschädigtes JSONL wird nicht ueberschrieben.
Bei einem Schreibfehler koennen `.pending-*` liegenbleiben; keine automatische
Loeschung. Quelle und Ausgabe sind lokale private Daten, auch die absoluten
Dateipfade in den Fundstellen.

## Pruefung und Backup

Abnahme: `python3 -m unittest discover -s weitblick/tests -v`.
Hashvergleich, Pfadgrenzen, Verknuepfungen, alle Arten, Quellenpflicht,
Frageform, Budgets und fehlende Daten werden getestet.

Backup-Hinweis: Vor einem spaeter separat freizugebenden Betrieb muss der
private Ausgabeordner gesichert und die Wiederherstellung geprueft werden.
Dieser PR fuehrt keinen Betrieb, Serverzugriff oder Backup-Lauf aus.
Hindsight: siehe Stufe 2. Das Ziel Wissens-DB bleibt offen.

## Weitblick Stufe 2: Hindsight-Spiegel

Grundlage: Fables Bauplan vom 24.11.2026 und Kaans Auftrag vom selben Tag.
JSONL bleibt die Hauptquelle. `HindsightWriter` ist ein zweiter Schreiber
hinter `Writer.write(entries) -> int` und spiegelt bereits gesicherte
Eintraege in die Bank `weitblick`, Version 1.2.1. Der Extraktionsmodus
`chunks` (ohne LLM-Aufruf) ist eine Bank- oder Server-Einstellung, kein
Feld im Retain-Request. Nur Python-Standardbibliothek.

### Datenfluss

1. Quellen lesen und Fragen erzeugen, unveraendert aus Stufe 1.
2. `FileWriter` schreibt `weitblick.jsonl` und `weitblick.md` atomar (fsync).
3. Erst danach erhaelt `HindsightWriter` den Inhalt der JSONL-Datei.
   Er oeffnet die JSONL-Datei nie zum Schreiben. Ein Hindsight-Ausfall kann
   gesicherte Eintraege daher weder veraendern noch verlieren.
4. `MirroredWriter` verbindet beide. Sein Rueckgabewert ist wie in Stufe 1 die
   Zahl neuer JSONL-Eintraege. Das Hindsight-Ergebnis steht getrennt in
   `mirror_result` und in der Ausgabe.

Beide Einstiegspunkte nutzen denselben Pfad (`weitblick/pipeline.py:run`).

### Aufruf

```sh
# Wie Stufe 1; Hindsight bleibt aus, solange es nicht eingeschaltet ist:
python3 -B -m weitblick --entscheide /private/listen/ENTSCHEIDE.md \
  --server /private/listen/SERVER.md --auftraege /private/listen/AUFTRAEGE.md \
  --output /private/weitblick-ausgabe
# Hindsight trotz gesetzter Umgebung fuer diesen Lauf auslassen:
python3 -B -m weitblick ... --no-hindsight
# Nachtlauf, nur ueber Umgebungsvariablen konfiguriert:
python3 -B -m weitblick.nachtlauf
```

### Umgebungsvariablen (F3)

| Variable | Pflicht | Bedeutung |
| --- | --- | --- |
| `WEITBLICK_HINDSIGHT_ENABLED` | nein | `1` schaltet die Spiegelung ein. Leer oder `0`: aus. Andere Werte: Konfigurationsfehler. |
| `WEITBLICK_HINDSIGHT_URL` | wenn eingeschaltet | http(s)-Basisadresse von Hindsight, ohne Zugangsdaten, Query oder Fragment |
| `WEITBLICK_HINDSIGHT_TOKEN` | wenn eingeschaltet | Bearer-Token, nur aus der Umgebung |
| `WEITBLICK_HINDSIGHT_TIMEOUT_S` | nein | Zeitlimit je Anfrage in Sekunden, Standard 10, hoechstens 120 |
| `WEITBLICK_ENTSCHEIDE`, `WEITBLICK_SERVER`, `WEITBLICK_AUFTRAEGE` | Nachtlauf | Pfade der drei Quell-Listen |
| `WEITBLICK_OUTPUT` | Nachtlauf | privater Ausgabeordner, immer derselbe (Budget und Status liegen dort) |
| `WEITBLICK_DISABLED` | nein | `1` schaltet den Nachtlauf ganz aus: nichts lesen, nichts schreiben, Exit 0 |

Namenswahl: Im Repo gibt es keine Hindsight-Variablen. Die `DECK_`-Namen
gehoeren zur Konfiguration der Deck-Anwendung in `app/`. Weitblick ist ein
eigenstaendiges Skript ohne diese Konfiguration und bekommt deshalb das Praefix
`WEITBLICK_`. Die Endungen `_URL`, `_ENABLED` und `_TIMEOUT_S` folgen den
bestehenden `DECK_`-Mustern. Die Bank ist absichtlich nicht konfigurierbar.

Fehlende Pflichtwerte im eingeschalteten Modus ergeben Exit 2 und die Meldung
`Weitblick: Konfigurationsfehler: Hindsight ist eingeschaltet, aber es fehlen:`
mit den Variablennamen. Werte werden nie ausgegeben, auch nicht in `repr`.
Vor dieser Pruefung wird nichts gelesen oder geschrieben.
Pruefung mit gesetzter Umgebung ohne Werte: siehe `SERVERPRUEFUNG.md`, Schritt 1.

### Aus-Schalter

- `--disabled` (CLI) und `WEITBLICK_DISABLED=1` (Nachtlauf): nichts lesen,
  nichts schreiben, kein Hindsight-Aufruf, Exit 0.
- Hindsight aus (Standard, `WEITBLICK_HINDSIGHT_ENABLED` leer oder `0`, oder
  `--no-hindsight`): JSONL-Verarbeitung wie in Stufe 1, kein Hindsight-Aufruf,
  Meldung `Hindsight ausgeschaltet; kein Hindsight-Aufruf.`

### Freigegebene Felder und Datenschutz

Die Nutzlast entsteht aus einer Positivliste (`BANK_FIELDS` in `hindsight.py`):
`id`, `art`, `frage`, `datum`, `bezug`, `status`, `nicht_gewaehlte_option`,
`quelle_liste`, `hinweis` und `fundstelle` nur mit `nummer` und `zeile`.
Der private Dateipfad `fundstelle[].datei` wird nie gesendet (Fable F4 offen).
Zusaetzlich prueft der Writer den vollstaendig serialisierten Request auf jeden
Pfad aus den Fundstellen (roh, JSON-maskiert, URL-kodiert). Bei einem Treffer
wird nichts gesendet und der Eintrag gilt als `fehlgeschlagen`.
Der Transport nutzt keine Umgebungs-Proxys und folgt keinen Weiterleitungen,
damit das Token nur an die konfigurierte Adresse geht.

### Grenzen und Wiederholung

Zaehlweise, Tagesgrenze und Behandlung bekannter IDs stammen unveraendert aus
Stufe 1 (`select_new` in `writer.py`, von beiden Writern genutzt): hoechstens
drei Eintraege je `datum` und fuenf insgesamt; bekannte IDs werden uebersprungen.
`HindsightWriter` fuehrt in `hindsight-status.json` im Ausgabeordner die
reservierten IDs mit Datum und Ergebnis, ohne Inhalt und ohne Pfade. Die
Reservierung wird vor dem Senden gespeichert. Dadurch gelten die Grenzen auch
bei erneutem Nachtlauf, nach Abbruch und nach Teilfehlern.

| Ergebnis | Wann | Erneuter Versuch |
| --- | --- | --- |
| `bestaetigt` | HTTP 2xx mit JSON-Objekt, nicht `success: false` | nie |
| `fehlgeschlagen` | HTTP 3xx (Weiterleitung wird nicht verfolgt), 4xx ausser 408, 425, 429, oder lokale Pfadsperre | ja, im naechsten Lauf, gleiche Dokument-ID |
| `unklar` | keine HTTP-Antwort, Zeitlimit, 408, 425, 429, 5xx, 2xx ohne JSON oder `success: false` | ja, im naechsten Lauf, gleiche Dokument-ID |

Ein erneuter Versuch betrifft nur bereits reservierte IDs. Neue Kandidaten
duerfen deren Platz nicht einnehmen. Die Wiederholung ist nur dann ohne Dublette,
wenn Hindsight ein Retain mit gleicher `document_id` ersetzt statt anhaengt
(Annahme F2-c, Serverpruefung Schritt 3 und S-K80). Geht die Statusdatei
verloren, sendet der naechste Lauf die hoechstens fuenf JSONL-Eintraege erneut
mit ihren Dokument-IDs. Eine beschaedigte Statusdatei wird nicht ueberschrieben.

### Fehlerverhalten und Exit-Codes

| Exit | Bedeutung |
| --- | --- |
| 0 | Erfolgreich; Hindsight aus oder alle offenen Eintraege bestaetigt |
| 1 | Eingabe- oder Ausgabefehler (wie Stufe 1), oder Hindsight-Spiegelung unvollstaendig. JSONL ist dann bereits gesichert; Meldung nennt nur Zaehler. |
| 2 | Falsche Argumente oder Konfigurationsfehler (nur Variablennamen in der Meldung) |

Meldungen enthalten keine Quelldaten, keine Pfade, keine URL und kein Token.

### Spaeterer Installationsschritt (nicht ausgefuehrt)

Erst nach bestandener Serverpruefung und Owner-GO. Beispiel fuer einen
Cron-Eintrag des Betriebsbenutzers, Platzhalter in spitzen Klammern:

```sh
# 02:15 taeglich; Umgebungsdatei nur fuer den Benutzer lesbar (chmod 600)
15 2 * * * cd <repo> && set -a && . <private-umgebungsdatei> && set +a && python3 -B -m weitblick.nachtlauf >> <privates-log> 2>&1
```

Ein systemd-Timer mit `EnvironmentFile=` ist gleichwertig. Vor dem Betrieb den
Ausgabeordner sichern und die Wiederherstellung pruefen (T8).

### Annahmen (F2, F5) am Adapter

Belegt ist lokal nur: Hindsight von vectorize-io, MIT-Lizenz, Release v1.2.1
vom 10.11.2026 und ein bankgebundener MCP-Pfad `/mcp/{bank_id}/` (Quelle:
oeffentliches Repository `vectorize-io/hindsight` auf GitHub, laut lokalen
Recherchenotizen vom 15. bis 23.11.2026). Ein Quellstand von 1.2.1 lag in
dieser Arbeitsumgebung nicht vor, und Netzwerkzugriff war ausgeschlossen.
Deshalb sind folgende Punkte Annahmen. Sie stehen gesammelt in `hindsight.py`
und werden in den Tests als solche benannt:

- F2-a Pfade: Retain `POST /v1/default/banks/{bank}/memories`, Lesen
  `GET` und Loeschen `DELETE /v1/default/banks/{bank}/documents/{id}`,
  Suche `POST /v1/default/banks/{bank}/memories/recall`.
- F2-b Modus: Im Retain-Request von Hindsight v1.2.1 gibt es kein Feld
  `mode`. Die Klasse `RetainRequest` (hindsight_api/api/http.py, Zeile 1354
  im Paket hindsight-api-slim 1.2.1) kennt nur `items`, `async`,
  `document_tags` (veraltet) und `operation_id`. Unbekannte Felder werden
  still verworfen (Pydantic v2, Standard `extra=ignore`; `RetainRequest`
  setzt `extra` nicht auf `forbid`). Der Extraktionsmodus ist die Bank-
  oder Server-Einstellung `retain_extraction_mode`
  (hindsight_api/config.py:1900 Standard `"concise"`, 1901 erlaubte Werte
  inklusive `"chunks"`, 4290 je Bank konfigurierbar). `"chunks"` speichert
  den Text ohne LLM-Aufruf (hindsight_api/engine/retain/fact_extraction.py:3481
  und 3580). Weitblick sendet deshalb `"async": false` ohne `mode` und
  erwartet, dass die Bank auf `retain_extraction_mode=chunks` steht
  (siehe `SERVERPRUEFUNG.md`, Schritt 5, braucht Kaans Ja).
- F2-c ID: Weitblick vergibt die `document_id` selbst (`WB-` plus 24
  Hex-Zeichen, Typ String). Ein Retain mit gleicher ID ersetzt das Dokument.
- F2-d Fehler: fremde oder fehlende ID in einer Bank ergibt 404 (oder 403)
  mit JSON-Fehlerobjekt.

Ein Mock bestaetigt keinen API-Vertrag. Entscheidend sind
`python3 -B -m weitblick.vertrag` gegen die OpenAPI-Datei des Servers
(Schritt 3) und S-K80 (Schritt 5) in `SERVERPRUEFUNG.md`.

F5: Offline zeigt `test_t4_only_retain_without_mode_field`, dass der Client je
Eintrag genau ein `POST .../banks/weitblick/memories` sendet, dessen Rumpf nur
die erlaubten Felder `items` und `async=false` enthaelt und kein `mode`. Der
Fake-Opener lehnt unbekannte Retain-Felder und jeden anderen Aufruf mit
`AssertionError` ab (`test_t4_mock_rejects_unknown_retain_fields`,
`test_t4_unexpected_call_fails_loudly`). Jeder Socket-Zugriff laesst den Test
scheitern. Ob Hindsight serverseitig dennoch ein LLM aufruft, zeigen nur
`retain_extraction_mode`, die Serverkonfiguration und die Logs im
Pruef-Fenster (Schritte 4, 5 und 7 in `SERVERPRUEFUNG.md`).

### Abnahme: Zuordnung T1 bis T8 und S-K80

Testdatei `tests/test_stufe2.py`, Stufe-1-Tests in `tests/test_weitblick.py`.

| Kennung | Testmethoden | Beobachtbare Erwartung | Zuordnung |
| --- | --- | --- | --- |
| T1 Bestand | alle 18 in `test_weitblick.py`, `test_t1_file_path_unchanged_without_hindsight` | 18 alte Tests gruen; ohne Einschalten 3 JSONL-Eintraege, 0 Hindsight-Aufrufe, keine Statusdatei | nach Bauplan |
| T2 Schreiben | `test_t2_write_mirrors_released_fields`, `test_t2_return_value_meaning_preserved` | Bank `weitblick` enthaelt genau die JSONL-IDs; Abruf liefert alle freigegebenen Felder unveraendert; Rueckgabe = neue JSONL-Eintraege | angepasst: `fundstelle[].datei` wird bewusst nicht gespeichert |
| T3 Budget | `test_t3_daily_limit_twenty_candidates`, `test_t3_total_limit_across_days`, `test_t3_repeated_nightly_run`, `test_t3_retry_does_not_widen_budget` | 20 Kandidaten ergeben 3, zweiter Lauf 0; ueber Tage hoechstens 5; zweiter Nachtlauf sendet nichts; Wiederholung erweitert das Budget nicht | nach Bauplan, erweitert |
| T4 Ohne Modell | `test_t4_only_retain_without_mode_field`, `test_t4_mock_rejects_unknown_retain_fields`, `test_t4_unexpected_call_fails_loudly` | nur Retain, Rumpf nur `items` und `async=false`, kein `mode`; unbekannte Felder und unerwartete Aufrufe lassen den Test scheitern | offline gegen Vertrag v1.2.1; Servernachweis F5 offen |
| T5 Trennung | `test_t5_production_bank_only`, `test_sk80_separated_banks_pass` (T5-Teil), `test_sk80_cross_bank_chunk_read_fails` | alle Produktionsaufrufe nur an Bank `weitblick`; Suche in A findet X nicht; unscoped `GET /v1/default/chunks/{chunk_id}` gibt X nicht unter Bank A frei | offline vorlaeufig; Server: Schritt 6 |
| T6 Aus-Schalter | `test_t6_disabled_cli_makes_no_call`, `test_t6_hindsight_switch_keeps_jsonl`, `test_t6_nightly_disabled_reads_and_writes_nothing`, Stufe 1 `test_disabled_cli_creates_nothing` | `--disabled`: klare Meldung, Exit 0, nichts geschrieben, 0 Aufrufe; Hindsight aus: JSONL nutzbar, 0 Aufrufe | nach Bauplan |
| T7 Nicht erreichbar | `test_t7_unreachable_keeps_jsonl_and_exits_1`, `test_t7_timeout_is_unclear`, `test_t7_response_classification_and_retry`, `test_t7_hindsight_failure_never_touches_jsonl`, `test_t7_corrupt_status_file_is_preserved` | Exit 1, Meldung ohne Quelldaten, URL oder Token; JSONL byte-gleich; Status `unklar`; naechster Lauf bestaetigt | nach Bauplan |
| T8 Wiederherstellung | `test_t8_rebuild_from_jsonl_after_status_loss` | ohne Statusdatei und mit leerer Bank stellt ein Lauf alle JSONL-Eintraege wieder her | vorlaeufig: nur Offline-Teil; Restore aus Nachtsicherung braucht eigenes GO |
| S-K80 | `test_sk80_separated_banks_pass`, `test_sk80_cross_bank_read_fails`, `test_sk80_cross_bank_chunk_read_fails`, `test_sk80_cross_bank_delete_fails`, `test_sk80_empty_success_is_not_a_pass`, `test_sk80_connection_error_is_open`, `test_sk80_general_auth_error_is_open`, `test_sk80_refuses_production_and_identical_banks`, `test_sk80_cli_output_hides_url_and_token`, `test_sk80_cli_missing_config` | Pruefer urteilt `bestanden` nur bei Nachweis von X in B, Abweisung des Dokument- und des Chunk-Pfads aus A ohne Nutzdaten, abgewiesenem Loeschen und unveraendertem X; Verbindungsfehler und 401 ergeben `offen` | offline: Pruefer-Logik; Server: Schritt 6 |

Weitere Tests: Pfadfilter (`test_privacy_*`), Konfiguration F3 (`test_f3_*`),
Vertragspruefer F2 (`test_f2_*`, gegen eine synthetische OpenAPI-Datei).
