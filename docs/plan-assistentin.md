# Plan: Deck-Assistentin, Multi-Agent-Split, AirLLM und Wissensaufbau

Stand: 2026-11-05. Status: **Entwurf, nichts gebaut.** Umsetzung startet erst nach GO des Owners.

Der Fortschritt pro Etappe wird später in `docs/status-assistentin.md` geführt (DONE / FAIL / HOLD).

## 1. Ziel

1. **Assistentin** (weiblich, «sie»): beantwortet Fragen aus der Wissensdatenbank (Regeln,
   Entscheide, Fehlerlösungen). Jede Antwort zeigt Quelle und Datum. Sie erkennt Wissenslücken
   und schlägt den neuesten besten Weg vor. Neue Wege bleiben Vorschläge bis zum GO des Owners.
2. **Multi-Agent-Split**: die Assistentin verteilt Anfragen aus dem Chat an spezialisierte
   Agenten. Vorbild ist ECC (Everything Claude Code), gebaut wird mit eigenen Bausteinen.
3. **AirLLM** für Batch- und Nachtjobs, **Wissensaufbau**, Ollama bleibt der interaktive Weg.

## 2. Ausgangslage im Repo (geprüft auf `main`)

| Bereich | Ist-Zustand | Folge für den Plan |
| --- | --- | --- |
| Chat | `/ui/chat` existiert (`app/ui/chat.py`). Laufzeit ist `UnavailableChat`, nur `SimulatedChat` antwortet. Kein Verlauf. | Die Assistentin erweitert diese Seite, keine zweite Chat-Seite. |
| Wissens-DB | Schema `deck_kb` mit `documents` und `chunks`, unveränderlich. Kein Eintragstyp, kein Status, keine Quelle ausser `source_task_id` und `created_at`. | Neuer Vertrag `deck_kb.v2` nötig (A1). |
| Suche | pgvector-Kosinus mit `FakeLocalEmbeddings` (8 Dimensionen, aus sha256). Keine Volltextsuche. | Die Fake-Vektoren finden nichts Sinnvolles. Volltextsuche zuerst (A2). |
| KB-Betrieb | Nur `DECK_KB_PROFILE=simulation` gegen eine `_test`-Datenbank. | Echter Betrieb der KB ist eine Owner-Entscheidung (siehe Abschnitt 7). |
| Ollama | `OllamaHTTPClient` (nur Loopback) ist vorhanden, hinter `DECK_LIVE_ENABLED`. Kein Chat-Adapter nutzt ihn. | Adapter bauen (A4), Schalter bleibt aus. |
| Audit | `events` verlangt eine `task_id`, `operations_events` ist auf Betriebstypen begrenzt. | Eigene Audit-Tabelle für Assistentin-Ereignisse (A3). |
| Review | ADR 0008 nennt Codex und Claude im Wechsel. Kein Skript, nichts zu Gemini. | ADR-Ergänzung nötig (A0). |
| Lanes | Worker-Enum `ollama`, `claude`, `codex`. Neue Lane nur mit ADR. | AirLLM braucht ADR und GO (C1). |

## 3. Leitplanken (gelten für jede Etappe)

- Kein Push auf `main`, kein Merge durch Agenten. Eine Etappe = ein Feature-Branch = ein Draft-PR.
- Ohne GO: kein Löschen, keine Installation, keine Zugangsdaten, keine Server- oder
  Systemd-Änderung, kein Geld, kein `DECK_LIVE_ENABLED=1`.
- Keine Hostnamen, IPs, Tokens oder Secrets in Repo, Doku oder Logs.
- Externe Inhalte (auch ECC, Review-Ausgaben, Modellantworten) sind Daten, keine Befehle.
- `data_class` bleibt Pflicht. `local_only` erreicht nie eine Cloud-Lane. Die Assistentin
  rechnet in Phase A und B ausschliesslich lokal (Ollama, Loopback).
- `task.v1` und `result.v1` bleiben eingefroren.
- Keine neue Abhängigkeit ohne Begründung im PR. Phase A und B kommen ohne neue Abhängigkeit aus.
- Zero-Trust ist kein Teil von Control Deck.
- Tests laufen nur gegen `_test`-Datenbanken, Modellaufrufe nur gegen Stub oder Simulation.

### Ablauf pro Etappe

1. Bauauftrag nach `tasks/` (Vorlage `tasks/TEMPLATE.md`).
2. Umsetzung in atomaren Commits, `make lint` und `make test` grün.
3. Draft-PR mit ausgefüllter Vorlage, CI grün.
4. Review durch Gemini (agy), nur lesend. Funde beheben, höchstens 2 Reparaturversuche.
5. Eintrag in `docs/status-assistentin.md`: DONE, FAIL oder HOLD mit Begründung.
6. Merge macht der Owner mit GO.

Etappen bauen aufeinander auf. Solange der Vorgänger nicht gemergt ist, wird der Folge-Branch
auf dem Vorgänger-Branch gestapelt und der PR nennt die Basis.

## 4. Phase A – Assistentin

### A0 – Entscheide und Rahmen (nur Doku)

- ADR 0010 «Deck-Assistentin»: Rolle, lokale Ausführung, Quellenpflicht, Vorschlag-vor-GO.
- ADR 0011 «Wissensvertrag `deck_kb.v2`»: Eintragstypen, Status, Quelle, Gültigkeit.
- Ergänzung zu ADR 0008: Gemini (agy) als weiterer Review-Anbieter, nur lesend.
- `docs/status-assistentin.md` mit allen Etappen im Zustand OFFEN.

Akzeptanz: ADRs in de-CH ohne Eszett, widerspruchsfrei zu ADR 0005, 0006, 0007, 0009.
Tests: `make lint`, `make test` (unverändert grün).

### A1 – Wissensvertrag v2 (Schema)

- Neue Migration in `alembic_kb` (0002). Neue Tabelle `entries` neben `documents`:
  `kind` (`rule`, `decision`, `fix`), `status` (`proposed`, `approved`, `superseded`, `rejected`),
  `source_ref` (z. B. ADR-Nummer, Doku-Pfad, Task-ID), `source_date`, `approved_at`,
  `supersedes_id`, `data_class`, `title`, Verweis auf `documents`.
- Inhalt bleibt unveränderlich. Nur `status` darf wechseln, über erlaubte Übergänge
  (`proposed -> approved | rejected`, `approved -> superseded`), per Trigger abgesichert.
- `KnowledgeStore` erhält `put_entry`, `get_entry`, `set_status`.

Akzeptanz:
- Eintrag ohne `kind`, `source_ref`, `source_date` oder `data_class` wird abgelehnt.
- Verbotene Statusübergänge und Inhaltsänderungen schlagen fehl.
- `alembic -c alembic_kb.ini -x target=test upgrade head` und `check` sind grün, Downgrade auf 0001 läuft.

Tests: Integrationstests für Constraints, Trigger, Übergänge; Unit-Tests für die Store-Methoden.

### A2 – Suche mit Quelle und Datum

- PostgreSQL-Volltextsuche (`tsvector`, Konfiguration `german`, GIN-Index) über Titel und Inhalt.
  Keine neue Abhängigkeit. pgvector bleibt, wird aber erst mit echten Embeddings (C3) gewichtet.
- Filter vor der Rangfolge: `data_class`, `status = approved`, `kind`.
- Treffer liefern immer `source_ref`, `source_date`, `kind`, `entry_id`, Rang.
- `POST /api/v1/knowledge/search` erhält die neuen Felder rückwärtskompatibel.

Akzeptanz:
- Bekannte Frage findet den passenden Eintrag an erster Stelle (fester Testkorpus).
- `proposed`, `rejected`, `superseded` erscheinen nie als Antwortgrundlage.
- Kein Treffer ohne Quelle und Datum.

Tests: Integrationstests mit Testkorpus (ca. 20 Einträge), Unit-Tests für Filter und Rangfolge.

### A3 – Antwortkern und Audit

- Neues Modul `app/assistant/`: Frage -> Prüfung (`scan_text`) -> Suche -> Antwortobjekt
  `Answer(text, citations[], gap, confidence)`.
- Regel im Code, nicht im Prompt: **ohne mindestens eine Quelle keine Sachantwort.** Dann
  antwortet sie «Dazu habe ich nichts Belegtes» und markiert eine Lücke.
- Zunächst extraktiv (Auszug aus den Treffern), also ohne Modell lauffähig und prüfbar.
- Neue Tabelle `assistant_events` (Haupt-DB, Migration 0019): Frage-Hash, Treffer-IDs, Ergebnisart,
  Zeit. Der Fragetext selbst wird nicht gespeichert.

Akzeptanz:
- Jede Antwort mit Inhalt enthält mindestens eine Quelle mit Datum.
- Frage mit Secret-Muster wird abgelehnt und nicht protokolliert.
- Jede Anfrage erzeugt genau ein Audit-Ereignis.

Tests: Unit-Tests für die Quellenpflicht (auch negative Fälle), Integrationstests für das Audit.

### A4 – Lokale Formulierung mit Ollama

- Adapter `LocalAssistantChat` auf `OllamaHTTPClient`: bekommt nur die Treffer und die Frage,
  formuliert die Antwort. Aktiv nur mit `DECK_LIVE_ENABLED=1`, sonst extraktiver Weg aus A3.
- Prüfung nach dem Modell: jede zitierte Quelle muss aus den Treffern stammen, sonst fällt die
  Antwort auf den extraktiven Weg zurück. Modelltext gilt als Daten.
- Zeitlimit und Abbruch wie im bestehenden Client.

Akzeptanz:
- Mit `DECK_LIVE_ENABLED=0` findet kein Netzwerkaufruf statt (Test beweist es).
- Erfundene Quelle im Modelltext führt zum Rückfall, nie zur Anzeige.
- Kontext mit `local_only` verlässt den Host nicht (nur Loopback, bestehende Prüfung).

Tests: Unit- und Integrationstests mit `tests/support/ollama_stub.py`. Kein echtes Modell.

### A5 – Oberfläche

- `/ui/chat` zeigt Antworten mit Quellenliste (Typ, Quelle, Datum) und klarer Kennzeichnung
  «belegt», «Lücke» oder «Vorschlag». Deutsch, «sie»-Form in Texten über die Assistentin.
- Telefon-tauglich (390 px), funktioniert ohne JavaScript, HTMX als Zugabe.
- Kein dauerhafter Chatverlauf (Grenze aus `docs/UI.md` bleibt).

Akzeptanz: Owner-Login und CSRF greifen; Quelle und Datum sind bei 390 px und 1024 px sichtbar.
Tests: Integrationstests der Routen, Playwright bei 390 und 1024 px.

### A6 – Wissenslücken

- Lücke = keine oder nur schwache Treffer, veraltete Quelle (älter als konfigurierbare Frist)
  oder widersprüchliche gültige Einträge.
- Tabelle `knowledge_gaps`: Thema, Grund, Anzahl Vorkommen, erstes und letztes Auftreten.
  Gleiche Lücken werden zusammengezählt, nicht vervielfacht.
- Seite `/ui/knowledge/gaps` mit Liste nach Häufigkeit.

Akzeptanz: gleiche Frage dreimal ergibt einen Lückeneintrag mit Zähler 3; Lücke verschwindet aus
der offenen Liste, sobald ein gültiger Eintrag sie deckt.
Tests: Unit-Tests für die Erkennungsregeln, Integrationstests für Zählung und Seite.

### A7 – Vorschläge mit GO

- Die Assistentin legt zu einer Lücke einen Eintrag im Status `proposed` an: Entwurfstext,
  Begründung, verwendete Quellen, optional der ersetzte Eintrag.
- Seite `/ui/knowledge/proposals`: Owner wählt **GO** (`approved`) oder **Ablehnen** (`rejected`).
  GO auf einen Ersatz setzt den alten Eintrag auf `superseded`.
- Vorschläge erscheinen in Antworten nur getrennt und sichtbar als «Vorschlag, noch ohne GO».
- «Neuester bester Weg»: bei mehreren gültigen Einträgen gewinnt der mit dem jüngsten
  `source_date`, ältere werden als ersetzt vorgeschlagen, nicht still ersetzt.

Akzeptanz:
- Ohne GO wird ein Vorschlag nie als Antwortgrundlage verwendet.
- Nur der angemeldete Owner kann GO geben; jedes GO und jede Ablehnung ist ein Audit-Ereignis.
- Kein Weg, einen Eintrag ohne Owner-Aktion auf `approved` zu setzen (Test auf API und Store).

Tests: Integrationstests für den ganzen Ablauf Lücke -> Vorschlag -> GO -> Antwort, Negativtests.

### A8 – Startwissen einspielen

- Skript `scripts/kb_seed.py`: liest ADRs, Regeln aus `AGENTS.md` und Fehlerlösungen aus der Doku
  und erzeugt Einträge im Status `proposed` mit `source_ref` (Pfad) und `source_date`
  (Datum des letzten Commits der Datei). Läuft nur gegen die Simulation bzw. `_test`-DB.
- Der Owner gibt die Einträge gesammelt oder einzeln frei.
- Referenzfälle `015` bis `017`: belegte Antwort, Lücke, Vorschlag ohne GO.

Akzeptanz: Skript ist idempotent (zweiter Lauf erzeugt keine Dubletten); `make reference` grün.
Tests: Unit-Tests für das Parsen, Integrationstest für Idempotenz, Referenzfälle.

## 5. Phase B – Multi-Agent-Split

Vorbild ECC liefert nur die Idee (kleine Agenten mit klarer Rolle und Übergabe). Es wird kein
Code und keine Konfiguration übernommen.

### B0 – Entscheid und Vertrag (nur Doku)

- ADR 0012 «Agenten der Assistentin»: Rollen, Rechte, Übergabeformat, Grenzen.
- Rollen (Start): **Auskunft** (Fragen beantworten), **Lücken** (Lücken erkennen und bündeln),
  **Vorschlag** (Entwürfe schreiben), **Prüfung** (Quellen und Datenklasse gegenprüfen, nur lesend).
- Agenten sind Bausteine im Prozess des Decks, keine neuen Lanes und keine Cloud-Aufrufe.

Akzeptanz: ADR benennt pro Rolle Eingabe, Ausgabe, erlaubte Schreibrechte.
Tests: `make lint`, `make test`.

### B1 – Agenten-Gerüst

- `app/assistant/agents/`: gemeinsames Protokoll `Agent`, Register, Übergabeobjekt `Handoff`
  (Anfrage-ID, Rolle, Eingabe, Ergebnis, Quellen).
- Rechte pro Rolle im Code erzwungen: nur «Vorschlag» darf `proposed` schreiben, niemand `approved`.
- Obergrenzen: Anzahl Übergaben pro Anfrage, Gesamtzeit.

Akzeptanz: Agent ohne Schreibrecht kann nicht schreiben; Übergabekette bricht an der Grenze ab.
Tests: Unit-Tests für Register, Rechte, Grenzen.

### B2 – Verteilerin

- Die Assistentin wählt die Rolle über feste Regeln (Frageart, Trefferlage). Ein Modell darf die
  Wahl nur innerhalb der erlaubten Rollen vorschlagen; unbekannte Rolle fällt auf «Auskunft» zurück.
- Verhalten aus Phase A bleibt identisch, wenn nur «Auskunft» aktiv ist (Schalter
  `DECK_ASSISTANT_AGENTS`, Standard aus).

Akzeptanz: alle Tests aus Phase A laufen mit und ohne Schalter grün.
Tests: Unit-Tests für die Verteilung, Regressionslauf der A-Tests in beiden Stellungen.

### B3 – Vier Agenten anschliessen

- A3/A4 wird «Auskunft», A6 wird «Lücken», A7 wird «Vorschlag», neu «Prüfung» als Gegenkontrolle
  vor jeder Anzeige (Quelle vorhanden, Datum vorhanden, Datenklasse erlaubt).
- Chat zeigt, welche Agentin geantwortet hat.

Akzeptanz: Antwort, die die Prüfung nicht besteht, wird nicht angezeigt; Audit nennt die Rolle.
Tests: Integrationstests je Rolle und für die Kette Auskunft -> Lücken -> Vorschlag -> Prüfung.

### B4 – Nachvollziehbarkeit

- `assistant_events` erhält Rolle und Übergabekette. Seite `/ui/audit` zeigt sie gefiltert.
- Referenzfälle `018` und `019`: Verteilung und abgewiesene Antwort.

Akzeptanz: jede Anzeige lässt sich auf Rolle, Treffer und Zeitpunkt zurückführen.
Tests: Integrationstests, `make reference`.

## 6. Phase C – AirLLM, Nachtjobs, Wissensaufbau

Aufteilung: **Ollama interaktiv** (Chat, schnelle Antwort), **AirLLM im Batch** (grosse Modelle,
langsam, nachts). Phase C enthält die einzigen Schritte mit Installation und neuer Lane; beide
brauchen ein GO vor dem Bau.

### C0 – Entscheid (nur Doku)

- ADR 0013 «AirLLM als Batch-Lane»: Zweck, Grenzen, Ressourcen, Abgrenzung zu Ollama,
  Begründung der neuen Abhängigkeit, Modellherkunft und Lizenz.
- Offene Punkte für den Owner sind dort als Fragen aufgeführt.

Akzeptanz: ADR liegt vor. **Danach HOLD bis GO** (neue Lane, Installation, Modell-Download).

### C1 – Batch-Lane (Adapter, simuliert)

- Worker-Enum um `airllm` erweitert (Migration), Adapter nach dem Lane-Vertrag, Aufruf als
  Unterprozess. AirLLM ist ein optionales Extra, nicht Teil der Standardinstallation.
- Nur `data_class` nach den bestehenden Regeln; lokal, kein Netz zur Laufzeit.
- Tests mit Fake-Prozess nach dem Muster `_fake_*_cli.py`. Kein echtes Modell in CI.

Akzeptanz: Lane besteht den bestehenden Vertragstest; ohne installiertes Extra meldet sie sauber
«nicht verfügbar».
Tests: Unit- und Integrationstests mit Fake-Prozess, Fehlerpfade (Zeitlimit, Abbruch, leere Ausgabe).

### C2 – Nachtjobs

- Batch-Aufträge als normale Tasks mit Fenster «nachts» (Start- und Endzeit konfigurierbar),
  nacheinander, mit Obergrenze pro Nacht. Start über ein Skript/CLI.
- Die Einbindung in einen Timer ist eine Systemänderung und bleibt ausserhalb des Repos: nur
  Beschreibung in `docs/OPERATIONS.md`, Aktivierung erst mit GO.

Akzeptanz: ausserhalb des Fensters startet kein Batch-Auftrag; Abbruch am Fensterende hinterlässt
keine hängenden Leases.
Tests: Unit-Tests mit gestellter Uhr, Integrationstest für Abbruch und Wiederaufnahme.

### C3 – Wissensaufbau

- Nachtjob-Arten: (1) Lücken aus A6 zu Entwürfen ausarbeiten, (2) erledigte Tasks und
  Fehlerpfade zu Fehlerlösungen verdichten, (3) widersprüchliche oder alte Einträge melden,
  (4) echte Embeddings berechnen und pgvector in die Rangfolge aufnehmen (Vertrag v3,
  lokales Embedding-Modell).
- Jedes Ergebnis ist ein Eintrag im Status `proposed`. Der Owner sieht morgens die Liste.

Akzeptanz: kein Nachtjob schreibt `approved`; jeder Entwurf nennt seine Quellen; Embedding-Wechsel
ändert die Antworten des Testkorpus nicht zum Schlechteren.
Tests: Integrationstests je Jobart mit Fake-Lane, Vergleichslauf der Suche vor und nach Embeddings.

### C4 – Abnahme

- Referenzfälle für Batch und Wissensaufbau, Lasttest nach dem Muster der Etappe 18 (simuliert),
  Doku (`docs/ASSISTENTIN.md`, `docs/LANES.md`, `docs/WISSENS_DB.md`, `docs/STAND.md`).

Akzeptanz: `make lint`, `make test`, `make reference` grün; Statusdatei vollständig.

## 7. Offene Entscheide des Owners

Diese Punkte blockieren den Bau nicht vor der genannten Etappe, müssen dort aber geklärt sein.

| Nr. | Frage | Blockiert | Vorschlag |
| --- | --- | --- | --- |
| 1 | Wie wird das Gemini-Review (agy) aufgerufen? Im Repo gibt es dazu nichts, die Verfügbarkeit ist ungeprüft. | Review ab A0 | Falls nicht verfügbar: PR bleibt Draft, Status HOLD «Review ausstehend», Bau geht auf gestapeltem Branch weiter. |
| 2 | Echte KB-Datenbank statt Simulation (Profil, Rolle, Zugang)? Das ist eine Server- und Zugangsänderung. | Nutzen im laufenden Deck | Bis zum GO läuft alles in Simulation und Tests. |
| 3 | `DECK_LIVE_ENABLED=1` für formulierte Antworten? | Live-Nutzen von A4 | Aus lassen; extraktive Antworten funktionieren ohne Modell. |
| 4 | AirLLM: Installation, Modellwahl, Download, Speicherplatz. | C1 | Erst nach ADR 0013 entscheiden. |
| 5 | Timer für Nachtjobs (Systemänderung). | Betrieb von C2 | Erst nach C2, eigenes GO. |
| 6 | Frist, ab der eine Quelle als veraltet gilt. | A6 | 180 Tage, konfigurierbar. |

## 8. Risiken

- **Erfundene Quellen**: abgefangen durch Quellenpflicht im Code (A3) und Nachprüfung (A4, B3).
- **Simulation täuscht Qualität vor**: fester Testkorpus und Referenzfälle statt Fake-Vektoren.
- **Gestapelte PRs**: viele offene Drafts gleichzeitig. Empfehlung: Merge in Reihenfolge A0 bis A8.
- **Batch-Last auf dem Host**: Fenster, Obergrenze und Reihenfolge in C2, kein paralleler Batch.
- **Umfang**: 19 Etappen. Phase A ist allein nutzbar; B und C können danach neu bewertet werden.

## 9. Reihenfolge

A0 -> A1 -> A2 -> A3 -> A4 -> A5 -> A6 -> A7 -> A8 -> B0 -> B1 -> B2 -> B3 -> B4 -> C0 (HOLD bis GO)
-> C1 -> C2 -> C3 -> C4.
