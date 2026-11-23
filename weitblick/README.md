# Weitblick Stufe 1

Lokales Python-3.12-Skript ohne Sprachmodell, Netzwerk oder Zusatzpakete.
Es liest drei Markdown-Dateien und erzeugt **Fragen, keine Tatsachen**.
Kein Dienst, Zeitplan, Fokus-Index, Hindsight- oder Datenbankzugriff.

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
Nur `FileWriter` ist implementiert. Er schreibt `weitblick.jsonl` und die
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
Die Ziele Hindsight und Wissens-DB bleiben offen.
