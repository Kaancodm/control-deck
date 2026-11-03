# Wissens-DB: Etappe 14

Status: Kernvertrag, Kontext-/Export-Anbindung und Oberflaeche sind umgesetzt. Der Branch
ist auf den finalen Etappe-13-Head rebased. Gesamtvalidierung und CI werden im Draft-PR #29
und in [STAND.md](STAND.md) dokumentiert. Externes Review und Inbetriebnahme bleiben offen.

Der vorgeschlagene Vertrag `deck_kb.v1` wird ausschliesslich gegen isolierte Testdatenbanken
entwickelt. Er ist kein Nachweis fuer die Kompatibilitaet einer bestehenden Wissens-DB.
Jeder Eintrag und jeder Export braucht eine ausdrueckliche Datenklasse ohne Default.

## Testvertrag und Suche

Die eigene Alembic-Kette `alembic_kb.ini` legt nur in einer ausdruecklichen `_test`-Datenbank
das Schema `deck_kb` an. Dokumente und Chunks sind unveraenderlich; ein Dokument besitzt eine
ausdrueckliche Datenklasse, die fuer alle seine Chunks gilt. SHA-256 bindet die exakten
UTF-8-Bytes. Vor jedem Zugriff werden Vertragsversion, Embedding-Modell, Dimension,
Pflichtklasse ohne DB-Default sowie erforderliche Tabellenrechte geprueft.

| Auftrag | Zulaessige Wissensklassen |
|---|---|
| `local_only` | alle drei Klassen |
| `cloud_redacted` | `cloud_redacted`, `cloud_ok` |
| `cloud_ok` | nur `cloud_ok` |

Der SQL-Filter wirkt vor der Vektorrangfolge. Explizite Referenzen sind kanonische
Dokument-UUIDs. Fehlende oder unzulaessige Referenzen werden abgewiesen, niemals still
ausgelassen. Maximal 16 Referenzen und 96 KB Kontext; Dokumente sind auf 24 KB begrenzt.
Die deterministischen achtstelligen Fake-Vektoren pruefen den Datenfluss, keine semantische
Suchqualitaet. Es gibt keine versteckten Modellaufrufe oder Downloads.

## Freigabe und Versand

Kontext wird vor dem Speichern des Auftrags aufgeloest und durch die Datenschranke geprueft.
Geheimnistreffer verhindern sowohl Auftrag als auch Snapshot. Hostseitige
`task_context_snapshots` speichern unveraenderliche Bytes, Klasse und Digest. Der Owner sieht
die ausgewaehlten Inhalte vor der Freigabe; das `task.approve`-Event bindet den vollstaendigen
Digest-Satz. Jeder Versuch prueft diesen Satz und fuehrt dieselben Bytes erneut durch Scanner,
Redaction und Versandarchiv. Ein spaeterer KB-Ausfall aendert die freigegebenen Inhalte nicht.
Unter Task-/Lease-Locks erfolgen keine externen KB-Abfragen oder Embedding-Aufrufe.

Snapshots sind lokale Task-Artefakte. Etappe 16 darf sie nur fuer alte terminale Tasks ohne
Lease entfernen; Wissensdokumente, Ergebnisse, Audit und Exportbelege bleiben erhalten.

## Expliziter Export

`POST /api/v1/tasks/{id}/export` verlangt Owner-Authentifizierung und `data_class` im Body.
Export ist ausschliesslich aus `done` moeglich; `review` genuegt nicht. Die Klasse darf
gegenueber dem Auftrag nur gleich oder strenger sein. Vor dem Export prueft die Schranke
den Ergebnistext erneut. Dateiausgaben sind bis zu einer eigenen geprueften Anbindung gesperrt.

`knowledge_exports` speichert zuerst den ausdruecklichen Owner-Entscheid. Erst nach dem
Commit schreibt der Adapter ausserhalb der Host-Transaktion in die KB. Ein stabiler
Dokumentschluessel verhindert Duplikate; anschliessend wird `kb.export` einmalig auditiert.
Bei einem Ausfall dazwischen setzt derselbe Owner-Aufruf den offenen Export fort. Eine
abweichende Klasse oder Quelle wird als Konflikt abgewiesen. Es gibt keinen automatischen
Export, auch nicht bei automatisch erreichtem `done`.

## Simulation

Aktivierung nur mit `DECK_KB_PROFILE=simulation` und `DECK_KB_TEST_DATABASE_URL` auf eine
isolierte `_test`-DB. Der Standard ist deaktiviert. `POST /api/v1/knowledge/search` ist
Owner-geschuetzt und verlangt eine ausdrueckliche Datenklasse. Der lokale Import verwendet
`scripts/kb_import.py <inbox-relativer-pfad> --title <titel> --data-class <klasse> --simulate`;
Dateien durchlaufen dieselben sicheren Inbox-Lesepruefungen und den Scanner.

Pruefungen: `uv run alembic -c alembic_kb.ini upgrade head`, danach
`uv run alembic -c alembic_kb.ini check`. CI verwendet pgvector und eine separate KB-Test-DB.
Der Python-Adapter benoetigt keine neue Dependency; pgvector ist eine Test-DB-Erweiterung.

## Inbetriebnahme

Offen bleiben bestaetigtes Zielschema, Schreibrechte, lokales Embedding-Modell und Dimension
sowie eine gepruefte echte Anbindung. Live-Zugriffe bleiben gesperrt. Es gibt keine echten
Logins, API-Keys, Anbieteraufrufe, Serveraenderungen oder automatischen Modell-Downloads.

## Oberflaeche

Unter `/ui/knowledge` sucht der eingeloggte Owner mit ausdruecklicher Datenklasse. Eine
Auswahl fuellt einen neuen Auftragsentwurf; sie gibt weder einen Auftrag frei noch exportiert
sie Wissen. Die Detailseite zeigt die unveraenderlichen Kontexte vor der Start-Freigabe.
Der Export aus `done` verlangt ein eigenes Formular mit Datenklasse, Bestaetigung und CSRF.
Die Oberflaeche verwendet die bestehenden CSS-Variablen und Partials; das spaetere Design
bleibt separat. Browserpruefungen bestehen bei 390 und 1024 Pixeln, am Desktop ohne JavaScript.
