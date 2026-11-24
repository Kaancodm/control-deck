# Weitblick Stufe 1

Auftrag: Drei lokale Markdown-Listen lesen und ausschliesslich Fragen mit
Fundstellen als JSONL und kurze Markdown-Ansicht schreiben. Kein Modell,
kein Netzwerk, keine neuen Pakete. Nur dieser Ordner wird geaendert.

Umsetzung in dieser Sitzung, Review anschliessend durch SuperGrok:
1. Erfundenes Tabellen- und Listenmaterial testen, inklusive fehlender Option 2.
2. Parser und feste Frage-Muster bauen; nummerierte Referenzen aufloesen.
3. Datei-Schreiber hinter einer kleinen Schnittstelle bauen. Quellen und
   Ausgabe trennen, Verknuepfungen ablehnen, Wiederholungsläufe begrenzen.
4. Schema, CLI und Formatannahmen dokumentieren.
5. unittest, verfuegbare Repo-Pruefungen und gitleaks ausfuehren; Draft-PR.

Prueffokus: Hash der Quellen, Schreibpfade, Verknuepfungen, unbekannte
Referenzen, doppelte Nummern, sensible Freitexte, fehlende Felder,
Wiederholung eines Laufs und ausgeschalteter Lauf.

Entscheid: Das konkrete Zehn-Felder-Schema des Auftrags ersetzt das aeltere
Papier-Schema. Datum ist das Erzeugungsdatum; Status bleibt neu. Fehlendes
wird nicht erfunden. Budget: drei neue Fragen je Kalendertag und maximal
fuenf gespeicherte Fragen pro Ausgabeordner. Keine automatische Archivierung.

# Weitblick Stufe 2

Auftrag: Kaan via Atik, 24.11.2026. Bauplan: Fable, 24.11.2026 (T1 bis T8, S-K80).
Gebaut von Claude Opus 5.5 laut ausdruecklichem Auftrag. Nur lokal, kein Push.

1. Budgetregel aus `FileWriter` unveraendert als `select_new` teilen.
2. `HindsightWriter` hinter `Writer.write(entries) -> int`, nur Bank `weitblick`,
   Modus `chunks`, Positivliste der Felder, Statusdatei fuer Reservierung.
3. `MirroredWriter` und `pipeline.run` als gemeinsamer Pfad fuer CLI und Nachtlauf.
4. S-K80-Pruefer und OpenAPI-Vertragspruefer fuer Atiks Serverlauf.
5. Offline-Tests an der urllib-Opener-Grenze, README und `SERVERPRUEFUNG.md`.

Entscheid: JSONL bleibt massgebend (Bauplan-Vorschlag, Auftrag bestaetigt).
Fundstellen-Pfade gehen nicht in die Bank, bis F4 entschieden ist.
API-Details von Hindsight 1.2.1 sind Annahmen bis zur Serverpruefung (F2).
