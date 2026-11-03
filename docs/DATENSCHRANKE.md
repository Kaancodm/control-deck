# Datenschranke (Etappe 5)

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

Ein Geheimnis am Eingang ergibt **422**, bevor Task oder Event geschrieben werden.
Nicht geheime Cloud-Treffer ergeben **201** mit Status `blocked`: Nach `task.submit` wird
in derselben Transaktion `task.block` gespeichert. Der Rohauftrag bleibt lokal in der
Datenbank und in der geschuetzten Owner-Ansicht vorhanden.

Eine erneute Pruefung entdeckt beispielsweise geaenderte Dateien oder neue Blocklist-Eintraege.
Neue Sperrtreffer oder Dateifehler setzen einen vorhandenen Task auf `blocked`; die Queue
prueft danach den naechsten Auftrag. Der terminale Status `blocked` laesst sich nicht durch
Owner-Freigabe uebergehen. Konfigurationsfehler ergeben **503** ohne neue Freigabe oder Lease
fuer den betroffenen Task.
Ungueltige gespeicherte Alt-Auftraege werden mit dem sicheren Grund `invalid_task` blockiert,
statt die Verarbeitung der gesamten Queue abzubrechen.

## Erkennung und Fundstellen

Der Scanner klassifiziert definierte Muster und lokal konfigurierte Literale. Er liefert
keine vollstaendige Erkennung beliebiger Namen, Organisationen oder verschleierter Geheimnisse.

| Kategorie | Musterfamilien |
|-----------|----------------|
| `secret` | AWS-Zugangsschluessel, Anbieter- und GitHub-Token, JWT, Private-Key-Kopfzeilen, Zugangsdaten-Zuweisungen, Bearer-Token, Zugangsdaten in URLs |
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

## Lokale Blockliste

| Einstellung | Bedeutung |
|-------------|-----------|
| `DECK_BLOCKLIST_PATH` | Pfad einer UTF-8-Textdatei ausserhalb des Repositories |
| `DECK_INBOX_DIR` | Eingangsverzeichnis fuer Dateien; ohne Einstellung `inbox` |

Die Blockliste enthaelt ein Literal pro Zeile, keine regulaeren Ausdruecke. Der Vergleich
ignoriert Gross-/Kleinschreibung und verwendet Wortgrenzen. Leere Zeilen und
Zeilen mit fuehrendem `#` werden ignoriert, aeussere Leerzeichen entfernt und identische
Eintraege zusammengefasst. Namen und Organisationen traegt der Owner ausschliesslich lokal
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

## Anschluss an die folgenden Etappen

- Etappe 6 verwendet die Trefferkategorien fuer Pseudonymisierung und lokale `redaction_maps`.
  `cloud_redacted` besitzt in Etappe 5 noch keinen Transformationspfad.
- Etappe 7 berechnet die erlaubten Lanes und den Ollama-Override fuer `local_only`.
- Etappe 8 bindet Claims und Ergebnisse an eine Worker-Identitaet. Die vorhandene Worker-API
  garantiert diese Zuordnung noch nicht; die Datenschranke ersetzt sie nicht.
- Der spaetere Payload-Pfad muss die tatsaechlich versendeten Bytes nach Kontextaufloesung und
  Redaction erneut pruefen und archivieren. Die aktuelle Dateipruefung reserviert keinen
  unveraenderlichen Versand-Snapshot fuer eine spaetere Worker-Ausfuehrung.

Unit-Tests pruefen Muster, harmlose Texte, Limits und sichere Dateioperationen. Integrationstests
pruefen API, Persistenz, Freigabe und Queue gegen eine isolierte Testdatenbank. Inhaltliche
Referenzfaelle bleiben Aufgabe des Owners; Bestand und TODO-Platzhalter sind in
[REFERENCE_CASES.md](REFERENCE_CASES.md) beschrieben.
