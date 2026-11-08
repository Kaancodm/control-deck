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
| Loopback-Dialog | Adapter geprüft, Standard aus | Lokales HTTP-Fixture, Identität unbelegt |
| Web-Abruf | Transport geprüft, Standard aus | Fixture und Policy |
| MediaWiki-Suche | Enzyklopädie-Vertrag, Standard aus | Fixture; kein allgemeines Web |
| SearXNG | Vertrag, keine Instanz | Fixture; keine öffentliche Instanz |
| Benchmark 40 Fragen, CHF 0 | geprüft, offline | Korpusbelege ja, Modellqualität not_scored |
| Vergleich bestehendes Ollama mit Infomaniak | ungeprüft | keine Provisionierung, kein Lauf |
| Installiertes Modell | nicht belegt | kein Download, kein Uncensored-Nachweis |
| Kaan-Transport und Quittung nach 60 Sekunden | fehlt, nicht behauptet | Seite nennt die Lücke |
| Zeitlimit-Resultat nach externem Abbruch | fehlt, nicht behauptet | Seite nennt die Lücke |
| Physisches iPhone oder Safari | ungeprüft | Emulator Chromium 320/390/1024 und WebKit liefen; kein Gerät |
| Reboot, Offsite-Restore, lebendes Modell | ungeprüft | nicht Bestandteil dieses Laufs |

Verworfen: ein erfundener Kaan-Kanal, eine parallele Auftragstabelle, ein stilles
Löschen oder ein Aufbewahrungsjob, und die Behauptung, der Mock sei ein lebendes
unzensiertes Modell.

## Verhalten

Der Dialog bleibt ein Mock, solange `DECK_ASSISTANT_OLLAMA` nicht exakt `1` ist.
Der Runner-Schalter `DECK_LIVE_ENABLED` schaltet den Assistenten nicht ein.
Mit dem Assistenten-Schalter, `DECK_OLLAMA_URL` als HTTP-Loopback mit Port und
`DECK_ASSISTANT_OLLAMA_MODEL` als `name:tag` geht eine Anfrage an den bestehenden
Loopback-Client. Die Antwort trägt `ollama_loopback` und den Hinweis, dass die
Identität unbelegt ist. Ein Fixture ist kein installiertes Modell und kein
Uncensored-Nachweis. Profil und gespeicherte Sätze derselben Datenklasse gehen
nur mit ins Prompt, wenn das jeweilige Kästchen gesetzt ist. Der Mock ignoriert
beide Kästchen. Es gibt keinen Modell-Download und keinen Aufruf von `/api/pull`.

Die Datenklasse muss im Formular stehen; `local_only` ist nur ein
sichtbarer Vorschlag. `internal` und eine fehlende Klasse werden abgewiesen.

Das Profil hängt neue Versionen an. Ein Aufruf mit Akteur `model` wird abgewiesen.
Es gibt keine Löschroute.

Karten folgen `assistant_card.v1`. Platzhalter werden nur als Text eingesetzt.
`$(...)` bleibt Text und wird nicht an eine Shell gegeben. Import weist Geheimnisse
und Übergrösse ab. Die drei Presets Coding-Hilfe, Checkliste und Planung sind
editierbar; ein späterer Ladevorgang überschreibt eine Owner-Änderung nicht. Der
Lauf einer Karte nimmt die gespeicherte Datenklasse, nicht eine mitgeschickte Klasse.

Deep Search plant höchstens vier Schritte, liest den mitgegebenen lokalen Korpus
und zeigt Quellen mit Datum, Lücken, Widersprüche und unlesbare Quellen. Ein Auszug
ist ein Stück des gelieferten Textes. Der Belegstatus heisst `local_corpus`,
`owner_fetch`, `encyclopedia`, `configured_search`, `incomplete` oder `gap`.
`encyclopedia` ist ein MediaWiki-Treffer und keine allgemeine Websuche.
`configured_search` ist ein Treffer einer gesetzten SearXNG-Instanz. Nie
bewiesen und nie Mock. Widersprüche
entstehen nur aus mitgegebenen Claim-Paaren samt Titel und Datum. Freier Text wird
nicht zu einem erkannten Widerspruch umgedeutet. `local_only` blockiert Suche und
Adressabruf, ohne den Provider aufzurufen. `cloud_redacted` ebenfalls, weil hier
keine Redaktion vor dem Verlassen des Hosts hängt. `cloud_ok` braucht zusätzlich
das Freigabe-Kästchen.

Ohne `DECK_ASSISTANT_WEB=1`, `DECK_ASSISTANT_WEB_ALLOWLIST` und
`DECK_ASSISTANT_SEARCH_PROVIDER` bleibt der Abruf aus und die Lücke nennt diese
Namen. Der Provider ist `mediawiki` oder `searxng`. MediaWiki braucht
`DECK_ASSISTANT_MEDIAWIKI_API` auf `https` und einem Pfad, der auf `/api.php`
endet. Der Aufruf ist `action=query`, `list=search`, `srnamespace=0`,
`srlimit=3`, `srprop=snippet|timestamp`, `format=json`, `utf8=1`. Das ist die
dokumentierte Enzyklopädie-Suche und kein allgemeines Web. SearXNG braucht
`DECK_ASSISTANT_SEARXNG_URL` auf `https` und dem Pfad `/search`. Der Aufruf ist
`GET` mit `q` und `format=json`. Eine Instanz wird hier nicht gestartet und
keine öffentliche Instanz angesprochen. Ergebnisadressen werden nicht geholt.
Die Allowlist nimmt nur öffentliche HTTPS-Hosts. Loopback, private und
link-lokale Adressen, Umleitungen dorthin und Adressen mit Zugangsdaten werden
abgewiesen. Ein Such-GET folgt keiner Umleitung. Tests sprechen ein lokales
Fixture über `for_fixture` an. Dieser Konstruktor ist nicht der Produktionslader.
Seiten und Modellantworten sind Daten. Sie ändern keine Klasse und keine Rechte.

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
wird vor dem Lauf abgewiesen. Der Benchmark selbst ruft weder Ollama noch das Web
auf. Ein vorhandenes lokales Modell wird dort nicht als unzensiert bezeichnet.
Infomaniak ist `not_provisioned`. Latenz p50/p95 beschreibt nur diesen Mocklauf.

## Migration

`20261108_0019` hängt an `20261104_0018`. Tabellen: Profilversionen, Karten,
Kartenversionen, optionale Dialogsätze, Idempotenzschlüssel auf `tasks`.
`20261108_0020` erlaubt bei Dialogsätzen zusätzlich die Herkunft `ollama_loopback`.
Ein Downgrade auf `0019` bricht ab, wenn eine solche Zeile existiert, und löscht
sie nicht. Ein Downgrade in einer Test- oder Entwicklungsdatenbank auf `0018`
entfernt die Assistenten-Tabellen. `task.v1` und `result.v1` bleiben unverändert.
Es gibt keinen Löschjob im Betrieb.

## Isolierte Loopback-Probe

Der Einstieg ist `scripts/loopback_probe_isolated.py`. Er ruft dieselbe Klasse
`OllamaDialogueAdapter` und denselben `OllamaHTTPClient` auf. Abhängigkeiten:
Python 3.12, nur die Standardbibliothek. Kein Drittpaket. `uv run` und
`uv --offline --frozen` lösen das ganze Lockfile auf. Darin hängt FastAPI an
`opentelemetry-api`, und genau daran ist der Serverlauf vom 2026-11-08 um 05:32
stehen geblieben, bevor die Probe ausgeführt wurde. Diesen Befehl nicht wiederholen.

Im Checkout, ohne Datenbankvariable und ohne `DECK_LIVE_ENABLED`:

```bash
env -u PYTHONPATH -u DECK_DATABASE_URL -u DECK_LIVE_ENABLED \
  DECK_OLLAMA_URL='http://127.0.0.1:PORT' \
  DECK_ASSISTANT_OLLAMA_MODEL='name:tag' \
  python3 -S scripts/loopback_probe_isolated.py
```

`PORT` ist der Port, den readyz für das lokale Ollama schon gezeigt hat.
`name:tag` ist ein bereits installiertes Modell. `python3 -S` lädt keine
site-packages. Stehen site-packages auf dem Pfad, endet das Skript mit Code 4
und öffnet keinen Socket. Code 2 heisst, eine der beiden Variablen fehlt.
Code 3 schreibt den Adapter-Code auf die Fehlerausgabe, zum Beispiel
`assistant_model_unconfigured`, und enthält keinen Antworttext. Code 0 schreibt
ein JSON-Objekt. `runtime` ist `python-stdlib`, `third_party_modules` ist
eine leere Liste, und `implementation` enthält die sha256 der geladenen
App-Dateien. Der Satz ist fest: `Reply with the single token LOOPBACK_PROBE_7`.
Die Identität bleibt `unverified`. Ein Treffer belegt kein unzensiertes Modell
und keine Ollama-Version. Eine Adresse ausserhalb von Loopback bricht vor dem
Socket ab. Vom Cloud-Lauf aus wird der Server nicht kontaktiert.

## Offen

Der Modellaufruf dieser Probe auf dem Server bleibt offen. Der Lauf um 05:32
hat die Probe nicht erreicht. Die Prüfung der Ollama-Version 0.40.14 für
diesen Dialog und eine allgemeine Websuche bleiben offen. MediaWiki ist nur die
Enzyklopädie. Ein einzelner Cloud-Lauf mit dem öffentlichen Wort `octet` und
`srlimit=1` lieferte einen API-Treffer, kein Fixture und kein `FIXTURE_REPLY_7`.
SearXNG hat einen Vertrag, aber keine gesetzte Instanz und wurde nicht
angesprochen. Firecrawl und Parallel sind keine hinterlegten Zugangsdaten. Für den Serverlauf der Probe
reichen die beiden Variablen oben und ein bereits installiertes Modell mit Tag.
Für die Enzyklopädie auf dem Server zusätzlich `DECK_ASSISTANT_WEB=1`,
`DECK_ASSISTANT_SEARCH_PROVIDER=mediawiki`, die öffentliche Allowlist und
`DECK_ASSISTANT_MEDIAWIKI_API`. Modelldownload, bezahlte Suche, Server-Neustart,
Offsite-Restore und ein physisches iPhone bleiben offen. Die offenen Entwürfe 54,
55, 56, 57, 59 und 61 sowie die geschlossenen 58 und 60 sind nicht übernommen.
Entwurf 59 ändert nur die Wissens-Kette `alembic_kb` mit Revision `kb_0001`.
Er legt keine Datei unter `alembic/versions/` an. `20261108_0019` bleibt auf der
Hauptkette nach `20261104_0018`. Die beiden Ketten kollidieren nicht. Dieser Stand
wird nicht in 59 oder 61 gemergt.
