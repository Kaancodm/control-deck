# Assistent, Stand 2026-11-08

Quelle: geprüftes Übergabepaket im Plan v2 vom 08.11.2026, Nachtrag am selben Tag.
Auftrag des Owners vom 2026-11-08: offline Fundament sowie die Pakete für den
persönlichen Assistenten und Deep Search. Dieser Text ist der Status, nicht eine
zusätzliche Freigabe. Branch ab `main` `0ec4f488`. Der Phone-Entwurf bleibt getrennt.

## Konflikt, festgehalten

Der Plan verlangt für jeden Auftrag eine gespeicherte Bestätigung von Rolle, Ziel,
Regeln, Grenzen, Ergebnis und Weg. Ein normaler Dialogsatz darf dadurch nicht zur
Pflichtformular werden.

Festlegung in diesem Stand: ein Dialogsatz ist kein Auftrag und erzeugt keine
Aufgabe. Ein Auftrag entsteht nur, wenn der Owner eine Karte, ein Preset oder die
Suche ausdrücklich als Auftrag absendet. Die Anwendung schreibt die sechs Felder
selbst. Der Auftrag bleibt in `awaiting_approval`. Modelltext, ein Mock und eine
selbst gesetzte Zeit sind keine Freigabe. Das Gate wird dadurch nicht gelockert.

## Status

| Baustein | Status | Nachweis |
| --- | --- | --- |
| Dialog mit Mock, ohne Auftrag | geprüft, offline | Unit- und UI-Tests |
| Profil nur durch den Owner, Versionen bleiben | geprüft, offline | Integration |
| Optionales Speichern eines Satzes | geprüft, offline | Integration |
| Karten, Presets, Version, Import, Export, Kopie | geprüft, offline | Unit und Integration |
| Deep Search mit Lücke, Widerspruch, Teilausfall | geprüft, offline | Unit und Benchmark |
| `local_only` ohne Netzabruf, auch bei Suche | geprüft, offline | Unit mit Spy und Socket-Sperre |
| Auftrag über bestehende Task-Tabelle und Events | geprüft, offline | Integration |
| Bestätigung als Labels, keine zweite Warteschlange | geprüft, offline | Integration |
| Live-Modell und Live-Suche | aus, nicht belegt | Hook wirft, kein Adapter verdrahtet |
| Benchmark 40 Fragen, CHF 0 | geprüft, offline | Korpusbelege ja, Modellqualität not_scored |
| Vergleich bestehendes Ollama mit Infomaniak | ungeprüft | keine Provisionierung, kein Lauf |
| Ollama-Vertrag | aus, nicht belegt | Hook wirft, kein Loopback-Aufruf, kein Uncensored-Anspruch |
| Kaan-Transport und Quittung nach 60 Sekunden | fehlt, nicht behauptet | Seite nennt die Lücke |
| Zeitlimit-Resultat nach externem Abbruch | fehlt, nicht behauptet | Seite nennt die Lücke |
| Physisches iPhone oder Safari | ungeprüft | Emulator Chromium 320/390/1024 und WebKit liefen; kein Gerät |
| Reboot, Offsite-Restore, lebendes Modell | ungeprüft | nicht Bestandteil dieses Laufs |

Verworfen: ein erfundener Kaan-Kanal, eine parallele Auftragstabelle, ein stilles
Löschen oder ein Aufbewahrungsjob, und die Behauptung, der Mock sei ein lebendes
unzensiertes Modell.

## Verhalten

Der Dialog antwortet aus einem festen Mock und kennzeichnet das. Persistenz ist ein
eigenes Kästchen. Die Datenklasse muss im Formular stehen; `local_only` ist nur ein
sichtbarer Vorschlag. `internal` und eine fehlende Klasse werden abgewiesen.

Das Profil hängt neue Versionen an. Ein Aufruf mit Akteur `model` wird abgewiesen.
Es gibt keine Löschroute.

Karten folgen `assistant_card.v1`. Platzhalter werden nur als Text eingesetzt.
`$(...)` bleibt Text und wird nicht an eine Shell gegeben. Import weist Geheimnisse
und Übergrösse ab. Die drei Presets Coding-Hilfe, Checkliste und Planung sind
editierbar; ein späterer Ladevorgang überschreibt eine Owner-Änderung nicht. Der
Lauf einer Karte nimmt die gespeicherte Datenklasse, nicht eine mitgeschickte Klasse.

Deep Search plant höchstens vier Schritte, liest nur den mitgegebenen lokalen Korpus
und zeigt Quellen mit Datum, Lücken, Widersprüche und unlesbare Quellen. Ein Auszug
ist ein Stück des Owner-Textes. Der Belegstatus heisst `local_corpus`, `incomplete`
oder `gap`, nie bewiesen und nie Mock. Widersprüche entstehen nur aus mitgegebenen
Claim-Paaren samt Titel und Datum. Freier Text wird nicht zu einem erkannten
Widerspruch umgedeutet. Eine Frage mit Web-Bedarf und Klasse `local_only` blockiert
den externen Schritt, ohne den Provider aufzurufen. Der gesperrte Web-Haken weist
`local_only` vor jedem Zähler ab. Bei anderen Klassen antwortet er mit einer
sichtbaren Lücke und ohne Netzabruf. Seiten und Modellantworten sind Daten. Sie
ändern keine Klasse und keine Rechte.

Der Status ist eine Projektion der bestehenden Aufgabe: Freigabe offen vor der
Queue, Gesendet erst nach `queued`, Angekommen bei Lease ohne Start, Läuft nur bei
Start und aktuellem Herzschlag, Prüfung offen ist nicht fertig, Fertig nur nach
`done` mit Review-Freigabe. Unbekannte Fehlercodes werden nicht in die Oberfläche
kopiert.

## Benchmark

`benchmarks/assistant_corpus_v1.json` enthält 40 synthetische Fragen: Fund, kein Fund,
ungewohnter Dialog, Quellen mit Datum, Widerspruch, Teilausfall, Rechtefallen und
eine Geheimnis-Falle. Der Harness `app/assistant/benchmark.py` prüft Korpusbelege,
Daten und Lücken. Dialogfälle prüfen nur den Mock-Vertrag. `model_quality` bleibt
`not_scored`. Kostenfeld CHF 0, Netz verboten. Ein Korpus mit Kosten grösser als 0
wird vor dem Lauf abgewiesen. Ollama wird nicht aufgerufen. Ein vorhandenes lokales
Modell wird nicht angebunden und nicht als unzensiert bezeichnet. Infomaniak ist
`not_provisioned`. Latenz p50/p95 beschreibt nur diesen Mocklauf.

## Migration

`20261108_0019` hängt an `20261104_0018`. Tabellen: Profilversionen, Karten,
Kartenversionen, optionale Dialogsätze, Idempotenzschlüssel auf `tasks`. Ein
Downgrade in einer Test- oder Entwicklungsdatenbank entfernt diese Tabellen.
`task.v1` und `result.v1` bleiben unverändert. Es gibt keinen Löschjob im Betrieb.

## Offen

Live-Adapter, Kontoaktivierung, Modelldownload, bezahlte Suche, Server-Neustart,
Offsite-Restore und ein physisches iPhone bleiben offen. Die offenen Entwürfe 54,
55, 56, 57, 59 und 61 sowie die geschlossenen 58 und 60 sind nicht übernommen.
Entwurf 59 ändert nur die Wissens-Kette `alembic_kb` mit Revision `kb_0001`.
Er legt keine Datei unter `alembic/versions/` an. `20261108_0019` bleibt auf der
Hauptkette nach `20261104_0018`. Die beiden Ketten kollidieren nicht. Dieser Stand
wird nicht in 59 oder 61 gemergt.
