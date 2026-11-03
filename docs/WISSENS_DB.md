# Wissens-DB: Etappe 14

Status: Umsetzung gestartet, noch nicht reviewbereit. Der Branch ist auf Etappe 13 gestapelt.

Der vorgeschlagene Vertrag `deck_kb.v1` wird ausschliesslich gegen isolierte Testdatenbanken
entwickelt. Er ist kein Nachweis fuer die Kompatibilitaet einer bestehenden Wissens-DB.
Jeder Eintrag und jeder Export braucht eine ausdrueckliche Datenklasse ohne Default.

Geplant sind klassengefiltertes Lesen und Suchen, ein separater Owner-Export aus `done`,
idempotente Wiederaufnahme sowie lokale Fake-Embeddings. Unveraenderliche Kontext-Snapshots
binden die Freigabe an exakt die spaeter geprueften und versandten UTF-8-Bytes.

## Inbetriebnahme

Offen bleiben bestaetigtes Zielschema, Schreibrechte, lokales Embedding-Modell und Dimension
sowie eine gepruefte echte Anbindung. Live-Zugriffe bleiben gesperrt. Es gibt keine echten
Logins, API-Keys, Anbieteraufrufe, Serveraenderungen oder automatischen Modell-Downloads.
