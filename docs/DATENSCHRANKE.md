# Datenschranke (Etappen 5 und 6)

`app/privacy` prueft den unveraenderlichen Auftrag, seine Text-Metadaten und unterstuetzte
Dateien. Dieselbe Pruefung laeuft vor Speicherung, vor Owner-Freigabe und vor jeder Lease.
Es werden keine Modelle, Cloud-Dienste oder externen APIs aufgerufen.

## Datenklassen und Wirkung

`data_class` bleibt Pflicht ohne Schema- oder DB-Default. Die eingefrorenen Formate
`task.v1` und `result.v1` bleiben unveraendert.

| Trefferkategorie | `local_only` | `cloud_redacted` | `cloud_ok` |
|-----------------|--------------|------------------|------------|
| `secret` | Eingang abweisen | Eingang abweisen | Eingang abweisen |
| `redactable_pii` | Lokal zulaessig | `blocked` bis zum Redaction-Pfad | `blocked` |
| `protected_identifier` | Lokal zulaessig | `blocked` | `blocked` |
| Keine Treffer | Owner-Freigabe erforderlich | Owner-Freigabe erforderlich | Owner-Freigabe erforderlich |

Diese Tabelle beschreibt die bestehenden HTTP-/Queue-Grenzen. Der lokale Payload-Builder
aus Etappe 6 kann unterstuetzte Treffer fuer `cloud_redacted` pseudonymisieren; seine sichere
Anbindung an einen laufenden Worker-Versuch folgt in Etappe 8. Bis dahin lockert die API keine
Sperre. Die unterschiedliche Behandlung im Builder ist in [REDAKTION.md](REDAKTION.md) beschrieben.

Ein Geheimnis am Eingang ergibt **422**, bevor Task oder Event geschrieben werden.
Nicht geheime Cloud-Treffer ergeben **201** mit Status `blocked`: Nach `task.submit` wird
in derselben Transaktion `task.block` gespeichert. Der Rohauftrag bleibt lokal in der
Datenbank und in der geschuetzten Owner-Ansicht vorhanden.

Eine erneute Pruefung entdeckt beispielsweise geaenderte Dateien oder neue Blocklist-Eintraege.
Neue Sperrtreffer oder dauerhafte Dateifehler setzen einen vorhandenen Task auf `blocked`; die Queue
prueft danach den naechsten Auftrag. Der terminale Status `blocked` laesst sich nicht durch
Owner-Freigabe uebergehen. Konfigurationsfehler ergeben **503** ohne neue Freigabe oder Lease
fuer den betroffenen Task.
Ungueltige gespeicherte Alt-Auftraege werden mit dem sicheren Grund `invalid_task` blockiert,
statt die Verarbeitung der gesamten Queue abzubrechen.

Eine voruebergehend unlesbare Datei (`file_unavailable`) beendet den Task nicht. Die Freigabe
antwortet mit **409** und laesst `awaiting_approval` unveraendert. Die Queue laesst einen solchen
Task in `queued`, ueberspringt ihn fuer diesen Aufruf und prueft den naechsten Auftrag. Nach
Wiederherstellung der Datei ist ein erneuter Versuch moeglich. Es wird weder eine Lease noch
ein Versuch verbraucht; `task.gate_error` protokolliert nur Fehlercode und strukturelle Quelle.
Identische Lease-Poll-Fehler werden pro Task, Fehlercode und Quelle hoechstens einmal in
60 Sekunden gespeichert. DB-Zeit und Task-Lock verhindern auch bei konkurrierenden Pollern
doppelte Eintraege im Zeitfenster. Owner-Freigabeversuche werden weiterhin einzeln auditiert.

## Erkennung und Fundstellen

Der Scanner klassifiziert definierte Muster und lokal konfigurierte Literale. Er liefert
keine vollstaendige Erkennung beliebiger Namen, Organisationen oder verschleierter Geheimnisse.

| Kategorie | Musterfamilien |
|-----------|----------------|
| `secret` | AWS-Zugangsschluessel, Anbieter-, GitHub-, GitLab-, Tailscale-, xAI-, Stripe-, Google-, Slack- und Hugging-Face-Token, JWT, Private-Key-Kopfzeilen einschliesslich PGP/SSH2, Zugangsdaten-Zuweisungen, Bearer-Token, Zugangsdaten in URLs |
| `redactable_pii` | ASCII-E-Mail, schweizerische/deutsche Telefonnummern, IBAN, AHV-Nummern |
| `protected_identifier` | Interne IPv4-/IPv6-Adressen, interne Hostnamen, Benutzerverzeichnisse, konfigurierte Blocklist-Literale |

IBAN wird anhand von Laenderkennung und Laenge erkannt, AHV anhand des Zahlenformats;
Pruefziffern werden bewusst nicht als Filter verwendet. Telefonnummern umfassen internationale
CH-/DE-Formate und zehnstellige nationale Nummern. Die syntaktische Erkennung kann deshalb
auch Platzhalter oder andere Zahlencodes markieren. Adressmuster umfassen private, Loopback-,
Link-Local- und CGNAT-IPv4-Bereiche sowie IPv6-ULA, Link-Local, Loopback, die nicht spezifizierte
Adresse und interne IPv4-mapped-Adressen. Beliebige Schreibweisen oder Unicode-Verschleierung
sind nicht vollstaendig abgedeckt. Harmlos wirkende oder nicht erkannte Inhalte sind keine
Garantie fuer das Fehlen persoenlicher Daten.

Zugangsdaten-Zuweisungen erfassen auch Konfigurationsnamen mit Praefixen, insbesondere die
Deck-Token, Secret-Key- und Datenbank-Passwortfelder. Kurze Werte in eindeutig benannten
Zugangsdatenfeldern bleiben gesperrt. Beim mehrdeutigen nackten Schluessel `token` vermeiden
Mindestlaenge und der Ausschluss rein numerischer Werte Treffer in gewoehnlichem Zahlentext.
URL-Zugangsdaten werden unabhaengig vom Schema erkannt, auch mit leerem Benutzernamen;
interne Hostnamen umfassen auch die Endung `.ts.net`.

Geprueft werden `title`, `instructions`, Name/Wert/Pfad/MIME der Inputs,
`acceptance[].value`, Schluessel und Werte von `labels`, Referenzkennungen und die vollstaendigen
gelesenen Dateiinhalte. Input-Name und Wert sowie Label-Schluessel und Wert werden auch zusammen
als Zuweisung geprueft, damit aufgeteilte Zugangsdaten erkannt werden. Solche Fundstellen tragen
die strukturelle Quelle `assignment`; ihre Positionen beziehen sich auf `Name=Wert`.
Die Einbindung der Wissens-DB folgt erst in Etappe 14: Nicht leere
`context_refs` werden bis dahin mit **422** (`context_unavailable`) abgewiesen statt ignoriert.

Fundstellen enthalten nur Kategorie, festen Regelnamen, strukturelle Quelle und Start-/Endposition
in Unicode-Zeichen; die Endposition ist exklusiv. Quellen verwenden Feldindizes, keine gefundenen
Rohwerte, Dateinamen oder Label-Schluessel. Datenschranken-Fehler und `task.block`-Details geben
keine Geheimnisse, persoenlichen Angaben oder lokale Konfigurationspfade aus. Pro Auftrag
werden hoechstens 128 Fundstellen gemeldet; Geheimnistreffer haben dabei Vorrang. Die Begrenzung
der Metadaten macht einen spaeteren Geheimnistreffer nicht zulaessig.

### Unicode-Schattenkopie

Vor der Musterpruefung werden Zeichen der Unicode-Kategorie `Cf` sowie die Menge
`Default_Ignorable_Code_Point` aus Unicode 15.0 entfernt und eine NFKC-Schattenkopie gebildet.
Dazu gehoeren auch CGJ, Variation Selectors, Hangul Filler und weitere unsichtbare Marken. Dabei werden auch geschuetzte und schmale geschuetzte Leerzeichen
zu gewoehnlichen Leerzeichen. Dieselbe Normalisierung gilt fuer Blocklist-Literale. So werden
unter anderem kombinierte Schreibweisen, Vollbreitzeichen und Formatzeichen innerhalb eines
Treffers erkannt.

Die Positionskarte rechnet Fundstellen auf den unveraenderten Originaltext zurueck. Sie
beruecksichtigt Zusammensetzung, Expansion, Umordnung kombinierender Zeichen und Hangul.
Gespeicherter Auftrag, Datei-Hashes und spaetere Rueckabbildung behalten die Originalbytes.
Reiner ASCII-Text verwendet eine Identitaetsabbildung ohne Positionsarrays.

Original und Schattenkopie sind auf jeweils 5 Mi Unicode-Zeichen begrenzt, eine kombinierende
Zeichenfolge auf 1024 zerlegte Zeichen. Uebermaessig komplexer Text wird ohne Kuerzung mit
`unicode_too_complex` abgewiesen. Die bestehenden Byte-Limits fuer Auftraege und Dateien
gelten zusaetzlich. NFKC loest keine beliebigen Homoglyphen auf und dekodiert keine Base64-,
Hex- oder URL-kodierten Geheimnisse.

## Lokale Blockliste

| Einstellung | Bedeutung |
|-------------|-----------|
| `DECK_BLOCKLIST_PATH` | Pfad einer UTF-8-Textdatei ausserhalb des Repositories |
| `DECK_INBOX_DIR` | Eingangsverzeichnis fuer Dateien; ohne Einstellung `inbox` |

Die Blockliste enthaelt ein Literal pro Zeile, keine regulaeren Ausdruecke. Der Vergleich
ignoriert Gross-/Kleinschreibung und verwendet Wortgrenzen. Zwischen den Woertern eines
Mehrwort-Literals sind beliebige nichtleere Leerraumfolgen erlaubt, einschliesslich
Zeilenumbruechen und Tabs; Metazeichen bleiben woertlich. Leere Zeilen und
Zeilen mit fuehrendem `#` werden ignoriert, aeussere Leerzeichen entfernt und identische
normalisierte Eintraege zusammengefasst. Nach der Normalisierung leere oder mehr als 256
Zeichen lange Eintraege sind ungueltig. Namen und Organisationen traegt der Owner ausschliesslich lokal
ein; die Datei gehoert weder ins Repository noch in normale Logs.

Fuer Cloud-Auftraege muss `DECK_BLOCKLIST_PATH` ausdruecklich gesetzt sein. Eine vorhandene,
leere Datei ist eine bewusst leere Liste und zulaessig. Ohne Einstellung sind nur lokale
Auftraege moeglich. Eine konfigurierte, aber unlesbare oder ungueltige Liste verhindert die
Pruefung fuer jede Datenklasse mit **503**. Die Liste wird an jeder Pruefgrenze erneut geladen.
Ein Symlink als Blocklist-Datei wird abgewiesen.

Grenzen: 64 KiB UTF-8-Datei, hoechstens 1000 nicht leere Eintraege, 256 Zeichen pro Literal,
keine Steuerzeichen. Unlesbare Dateien, ungueltiges UTF-8 und Aenderungen waehrend des Lesens
gelten als Konfigurationsfehler. `.env.example` enthaelt nur die leeren Einstellungen und das
lokale Standardverzeichnis.

## Sichere Textdateien

`inputs[].path` muss relativ zum Eingangsverzeichnis sein. Absolute Pfade, Pfadflucht,
Punktsegmente, Symlinks in jeder Pfadkomponente sowie Dateien mit mehreren Hardlinks werden
abgewiesen. Verzeichnisse und Dateien werden ueber gebundene Dateideskriptoren geoeffnet;
ein Symlinkwechsel lenkt das Lesen nicht auf einen anderen Pfad um. Auch die Komponenten des
konfigurierten Eingangsverzeichnisses duerfen keine Symlinks sein.

| Endung | Erlaubter MIME-Typ, falls angegeben |
|--------|------------------------------------|
| `.txt` | `text/plain` |
| `.md`, `.markdown` | `text/markdown` |
| `.json` | `application/json` |
| `.csv` | `text/csv` |

Ein optionaler `charset`-Parameter muss UTF-8 bezeichnen. Dateien werden als striktes UTF-8
gelesen, ohne Normalisierung oder Kuerzung. Tabulator, Zeilenumbruch und Wagenruecklauf sind
erlaubt; andere Steuerzeichen werden abgewiesen. JSON und CSV werden in dieser Etappe als
Text gescannt, nicht fachlich geparst. PDF, Office-Dateien, Bilder, Archive, Binaerdateien,
Geraete und FIFOs sind nicht unterstuetzt. Aenderungen von Dateigroesse oder Metadaten waehrend
des Lesens fuehren zur Ablehnung.

| Grenze | Wert |
|--------|------|
| Dateiinhalt | 1 MiB je Datei |
| Gescannte Texte und Dateiinhalte zusammen | 5 MiB je Auftrag |
| Inputs | 64 |
| Dateien | 16 |
| Gepruefte Textteile einschliesslich zusammengesetzter Zuweisungen | 1024 |
| Dateipfad | 64 Komponenten, 4096 UTF-8-Bytes |

Ueberschrittene Grenzen fuehren zu **422** am Eingang oder `blocked` bei einer erneuten
Pruefung. Kein Inhalt wird still gekuerzt.

Bei erfolgreicher Freigabe enthaelt `task.approve.detail.file_hashes` fuer jede gelesene Datei
`input_index` und `sha256`. Der Hash stammt aus denselben vollstaendigen Bytes, die in diesem
Freigabelauf geprueft wurden; UTF-8-BOM und Zeilenenden bleiben erhalten. Es gibt keinen zweiten
Dateizugriff fuer den Hash und keine Dateipfade oder Rohinhalte im Hash-Eintrag. Diese Nachweise
erlauben der spaeteren Worker-Etappe einen Inhaltsvergleich, ersetzen ihn aber noch nicht.

## Anschluss an die folgenden Etappen

- Etappe 6 verwendet die Trefferregeln fuer lokale Pseudonymisierung und `redaction_maps`.
  Der Payload-Builder ersetzt E-Mail, Telefon, IBAN, AHV, interne Adressen und Blocklist-Literale;
  Geheimnisse, Benutzerverzeichnisse und interne Hostnamen bleiben gesperrt. Die bestehenden
  HTTP-/Queue-Grenzen bleiben bis zur sicheren Runner-Anbindung konservativ.
- Etappe 7 berechnet die erlaubten Lanes und den Ollama-Override fuer `local_only`.
- Etappe 8 bindet Claims und Ergebnisse an eine Worker-Identitaet. Die vorhandene Worker-API
  garantiert diese Zuordnung noch nicht; die Datenschranke ersetzt sie nicht. Zwei strikte
  `xfail`-Integrationstests halten die offene Dispatch-Luecke fuer Cloud-Worker sichtbar.
- Der Payload-Builder prueft bereits den vollstaendig gerenderten Text nach Redaction erneut;
  der Artefakt-Writer legt exakt diese Bytes unveraenderlich ab. Etappe 8 muss diesen Pfad an
  Claim und Versuch binden und die tatsaechlich gelesenen Dateien gegen die Freigabe-Hashes
  pruefen oder einen freigegebenen unveraenderlichen Snapshot verwenden.
- Homoglyphen und zusaetzliche Dekodierungen erfordern gesonderte Regeln und bleiben offen.

Unit-Tests pruefen Muster, harmlose Texte, Limits und sichere Dateioperationen. Integrationstests
pruefen API, Persistenz, Freigabe und Queue gegen eine isolierte Testdatenbank. Inhaltliche
Referenzfaelle bleiben Aufgabe des Owners; Bestand und TODO-Platzhalter sind in
[REFERENCE_CASES.md](REFERENCE_CASES.md) beschrieben.
