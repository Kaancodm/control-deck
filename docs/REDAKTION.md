# Pseudonymisierung und Rueckabbildung (Etappe 6)

`app/redaction` bereitet den vollstaendigen Versandtext lokal vor, ersetzt unterstuetzte
Angaben durch taskgebundene Platzhalter und prueft den fertigen Text erneut. Die Zuordnung
zu den Originalwerten bleibt in der lokalen Tabelle `redaction_maps`.

Etappe 6 stellt diese Bausteine bereit. Die bestehende HTTP-Worker-API liefert weiterhin den
unveraenderlichen Auftrag und verwendet deshalb unveraendert die konservative Datenschranke:
Cloud-Auftraege mit Treffern bleiben dort `blocked`. Die Anbindung an einen laufenden Versuch,
den tatsaechlichen Versand und die Ergebnisannahme folgt im Host-Runner in Etappe 8. Es gibt
hier keine echten Anbieteraufrufe und keinen neuen HTTP-Endpunkt fuer Maps.

## Datenklassen und unterstuetzte Angaben

| Datenklasse | Lokaler Payload-Builder |
|-------------|------------------------|
| `local_only` | Nur Lane `ollama`; keine Ersetzung, Geheimnisse bleiben verboten |
| `cloud_ok` | Keine Ersetzung; jeder Scanner-Treffer verhindert die Vorbereitung |
| `cloud_redacted` | Unterstuetzte Treffer ersetzen; Geheimnisse und nicht unterstuetzte Treffer sperren |

Der Builder prueft zusaetzlich die erlaubten und verbotenen Worker aus der Task-Policy.
Die Berechnung einer Fallback-Kette ist Aufgabe des Routers in Etappe 7. `data_class` bleibt
Pflicht ohne Default; `task.v1` und `result.v1` werden nicht geaendert.

| Kind | Scanner-Regel |
|------|---------------|
| `EMAIL` | `email` |
| `PHONE` | `phone` |
| `IBAN` | `iban` |
| `AHV` | `ahv` |
| `IP` | `internal_address` |
| `TERM` | `blocklist` |

Benutzerverzeichnisse und interne Hostnamen werden nicht ersetzt. Sie bleiben ebenso wie
Geheimnisse gesperrt. Namen und Organisationen werden nur ueber lokale Blocklist-Literale
erkannt; es gibt keine allgemeine Namenserkennung.

## Stabile Platzhalter und Originalwerte

Das Format lautet `[[CD_<task_id.hex>_<KIND>_<N>]]`. Die Task-Kennung ohne Bindestriche bindet
den Platzhalter an genau einen Auftrag. Die Nummer beginnt je Kind bei 1; neue Werte erhalten
die naechste freie Nummer. Derselbe exakte Originalwert behaelt ueber Wiederholungen und
Versuche hinweg denselben Platzhalter. Aendert sich spaeter seine Scanner-Kategorie, etwa
durch einen neuen Blocklist-Eintrag, bleibt die erste Zuordnung erhalten.

Nur die Erkennung arbeitet mit einer normalisierten Schattenkopie. Die Map speichert die
exakte Originalschreibweise, einschliesslich kombinierter Zeichen, Vollbreitzeichen und
unsichtbarer Formatzeichen. Verschiedene Originalschreibweisen bleiben verschiedene Werte.
Dadurch ist der Roundtrip aus Ersetzen und Rueckabbilden verlustfrei.

Bekannte kanonische Platzhalter derselben Map sind idempotent: erneutes Redigieren veraendert
sie nicht. Unbekannte, fremde oder fehlerhafte Platzhalter im reservierten Format werden
abgewiesen. Das gilt auch fuer mit ignorierten Unicode-Zeichen oder NFKC-Varianten veraenderte Schreibweisen.
Map-Originale duerfen selbst keine reservierten Platzhalter enthalten. Rueckabbildung erfolgt
in einem einzigen Durchlauf; wiederhergestellte Originalwerte werden nicht nochmals als
Platzhalter interpretiert.

## Ablauf der Vorbereitung

1. Lane und Task-Policy pruefen; bei gespeicherten Tasks den uebergebenen Auftrag mit dem
   unveraenderlichen DB-Payload vergleichen.
2. Datenschranke ausfuehren und jede Textdatei genau einmal sicher lesen. Derselbe gelesene
   Inhalt wird fuer den Versandtext verwendet.
3. Titel, Anweisung, Input-Metadaten und -Inhalte, Abnahmeregeln sowie Labels als Markdown
   rendern. Nicht aufgeloeste `context_refs` bleiben bis Etappe 14 unzulaessig.
4. Bei `cloud_redacted` den vollstaendigen Text scannen, die sechs unterstuetzten Arten
   ersetzen und die Zuordnungen atomar speichern.
5. Den gesamten redigierten Text erneut scannen. Jeder Resttreffer sperrt den Payload;
   die Begrenzung auf 128 Fundstellen darf keine weiteren Originalwerte durchlassen.
6. SHA-256 ueber die exakten UTF-8-Bytes des fertigen Textes bilden und ihn als privates
   Artefakt veroeffentlichen. Der Aufrufer kontrolliert Transaktion und spaetere Zustellung.

Die Funktionen haben getrennte Aufgaben:

| Funktion | Zweck |
|----------|-------|
| `engine.redact` | Reine Ersetzung mit einer vorhandenen lokalen Map; liefert Text und vollstaendige Map |
| `engine.rehydrate` | Reine Rueckabbildung mit ausdruecklicher Task-Kennung und Map |
| `repository.redact_for_task` | Zuordnungen unter Task-Row-Lock laden und atomar ergaenzen |
| `repository.rehydrate_for_task` | Ausschliesslich die lokale Map des angegebenen Tasks laden |
| `payload.build_payload` | Vorbereitung im Speicher fuer Fake-/Referenzpfade, ohne DB-Persistenz |
| `payload.prepare_task_payload` | Vorbereitung eines gespeicherten Tasks mit stabiler DB-Map |
| `artifacts.write_payload_sent` | Exakte Versandbytes als unveraenderliche Datei ablegen |

`PayloadBuild` enthaelt nur `text`, `sha256`, `applied` und die Anzahl verschiedener verwendeter
`placeholders`; es besitzt kein Map-Feld. Die Map aus der reinen Engine ist ausschliesslich fuer
lokale Aufrufer bestimmt. Text und Map sind aus den entsprechenden Debug-Darstellungen
ausgeschlossen. Fehler nennen sichere Codes beziehungsweise Kategorien und Anzahlen, keine
Originalwerte oder unbekannten Platzhalter.

## Lokale Persistenz

`redaction_maps` enthaelt Originalwerte im Klartext in der lokalen Datenbank. Die Map wird weder
in `result.v1`, normale API-Antworten, Audit-Events noch in den Versandtext aufgenommen.
Die geschuetzte Owner-Ansicht des urspruenglichen Auftrags behaelt ihren bisherigen Rohinhalt.
Die Zuordnung wird durch den Task-Row-Lock auch bei parallelen Vorbereitungen serialisiert.
Die Repository-Funktionen committen nicht selbst; ein Rollback verwirft neue Zuordnungen.

Pro Task sind Platzhalter und SHA-256 des Originalwerts jeweils eindeutig. Ein Hashkonflikt
mit unterschiedlichem Originalwert fuehrt zum Abbruch. Bestehende Eintraege koennen nicht
ueberschrieben werden. Schema, Constraints und Downgrade-Schutz sind in
[DATENBANK.md](DATENBANK.md) beschrieben.

## Versand-Artefakt

Der relative Pfad lautet `<task_id>/<attempt>/payload.sent.md` unter dem konfigurierten
Laufverzeichnis. Task-Kennung und positive Versuchszahl werden geprueft. Root-, Task- und
Versuchsverzeichnis benoetigen `0700`, die Datei `0600`; der Elternpfad des Roots muss bereits
existieren. Neu angelegte Verzeichnisse erhalten diese privaten Rechte.

Alle Pfadkomponenten werden descriptorbasiert ohne Symlink-Verfolgung geoeffnet. Die Datei
wird zuerst vollstaendig in eine private Tempdatei geschrieben und synchronisiert, dann atomar
ohne Ueberschreiben veroeffentlicht. Ein vorhandenes Artefakt mit identischen Bytes ist
idempotent; andere Bytes, Symlinks, Hardlinks, Spezialdateien oder unsichere Rechte fuehren
zur Ablehnung. Es gibt keine stille Kuerzung. Die Obergrenze betraegt 5 MiB UTF-8-Bytes.

Der Writer erhaelt ausschliesslich den fertigen Text. Er veraendert weder BOM noch
Zeilenenden und liefert relativen Pfad, SHA-256 und Bytezahl zurueck. Der Hash entspricht den
tatsaechlich geschriebenen Bytes. Die Map bleibt ausserhalb dieser Datei.

## Anschluss an Etappe 8

Der Host-Runner muss die Vorbereitung an Claim, Versuch, Lease und Lane binden und die
Map-Transaktion vor dem Versand committen. Er muss die gelesenen Dateien gegen die Hashes
aus `task.approve` pruefen oder einen freigegebenen unveraenderlichen Snapshot verwenden.
Der Versand verwendet genau die vorbereiteten und archivierten Bytes.

Die Ausgabe wird lokal rueckabgebildet, bevor sie in die automatische Abnahme und
Ergebnisannahme gelangt. Unbekannte oder fremde Platzhalter duerfen keinen Rohwert aufloesen.
Die konkrete Ergebnis-/Retry-Integration ist noch nicht Bestandteil der bestehenden
Worker-Endpunkte. Auch die zwei strikten `xfail`-Tests zur offenen Worker-Identitaet bleiben
bis zur Durchsetzung in Etappe 8 bestehen.

## Pruefung und Grenzen

Technische Tests decken Unicode-Roundtrips, wiederholte Werte, stabile Maps, Platzhalter-
Kollisionen, Resttreffer, Parallelitaet, Rollback, Migration und sichere Artefakte ab. Die vier
vorhandenen fachlichen Referenzfaelle bleiben unveraendert; weitere Inhalte liefert der Owner.

NFKC gleicht keine beliebigen Homoglyphen an. Base64-, Hex- oder URL-kodierte Geheimnisse
werden nicht generell dekodiert. Verschluesselung der Maps, Aufbewahrung und spaetere Loeschung
sind hier nicht implementiert. Die Datenschranke bleibt Mustererkennung mit den in
[DATENSCHRANKE.md](DATENSCHRANKE.md) beschriebenen Grenzen.
