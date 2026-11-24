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
