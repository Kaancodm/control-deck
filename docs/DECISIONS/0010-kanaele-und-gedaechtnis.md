# ADR 0010: Einheits-Chat, Kanaele und Gedaechtnis

## Kontext

Der Owner fuehrt Gespraeche in mehreren Kanaelen: in der Deck-Oberflaeche, in Slack,
ueber n8n, mit dem Grok-Bot, mit Claude und mit Claude Code. Bisher speichert nur der
Deck-Dialog optional einzelne Saetze (`assistant_turns`). Es gibt keine gemeinsame
Ablage, keine Herkunft pro Nachricht und keine Owner-Freigabe fuer Gedaechtnis, das
spaeter in ein Modell zurueckfliesst.

Der Owner hat am 2026-11-10 den Bau von M0 (Vertrag und Dokumente) und M1 (Ablage,
Quarantaene, Freigabe, Kontextlader) des Plans "Einheits-Chat" freigegeben, inklusive
der Migration auf `events.task_id` fuer Testdatenbanken. Dieser ADR haelt die Regeln fest.
Er ist keine Betriebsfreigabe und kein Merge-GO.

## Entscheid

Jeder Owner-Chat ist ein Kanal, der in eine Ablage schreibt:

`Adapter -> Umschlag channel_turn.v1 -> ingest_turn()`

`ingest_turn()` arbeitet in dieser Reihenfolge:

1. **Allowlist** von Kanal und Konto (`assistant_channel_accounts`). Unbekannt oder
   inaktiv wird abgewiesen und auditiert (`unknown_account`, `account_inactive`). Ein
   unbekannter Kanal scheitert schon am Schema (`invalid_envelope`). Fail-closed: die
   Migration legt nur `deck_ui/owner` aktiv an.
2. **Geheimnis-Scan** mit dem bestehenden Scanner (`app/privacy/scanner.py`). Ein Treffer
   speichert den Text nirgends, auch nicht in einer Quarantaene oder im Log. Im Audit
   stehen nur sha256 und `secret_rejected`.
3. **Dedup**: eindeutig ist `(channel, account, external_message_id)`. Ohne Quellen-ID gilt
   `(channel, account, external_thread_id, role, text_sha256, occurred_at)`. Ein Duplikat
   aendert keinen Zustand; es entsteht nur eine Audit-Zeile `assistant.turn_duplicate`.
   Zwei gleichzeitige Aufrufe mit demselben Schluessel ergeben genau eine Zeile.
4. **Quarantaene**: neue Zeilen haben `state = quarantined`. Sie kommen nie in einen
   Modellkontext und nie in eine Suche.
5. **Thread-Zuordnung**: eine angenommene Verknuepfung `(channel, account,
   external_thread_id)` fuehrt in einen Themen-Thread. Sonst bleibt die Nachricht
   `thread_unmapped`, und es entsteht ein **Vorschlag** (Thema plus Verknuepfung), den
   der Owner per Klick bestaetigt.
6. **Antworten** gibt das Deck nur in `deck_ui` und `slack`. Genau eine Antwort pro
   eingehender Nachricht erzwingt `UNIQUE (reply_for_message_id)`.

Jede Annahme, Abweisung und jedes Duplikat schreibt eine Audit-Zeile.

### Herkunft

Jede Nachricht und jedes Faktum traegt NOT NULL: `source_date` (Uhr der Quelle,
nicht vertrauenswuerdig), `source_channel`, `source_account`, `source_ref` (Dateiname
oder Link-Kennung, kein Hostname), `method` (`deck_ui`, `live_push`, `import_file`,
`owner_typed`) und `evidence_status` (`bewiesen`, `unbewiesen`, `abgelehnt`). Aus einem
Kanal kommt immer `unbewiesen`. `bewiesen` entsteht nur durch Owner-Klick; ein
DB-CHECK verlangt dafuer eine `approval_id`. Die Reihenfolge bestimmt die DB-Uhr
(`received_at`, `seq`), nicht die Quell-Uhr.

### Freigaben

Uebergaenge `quarantined -> approved/rejected`, Faktum `proposed -> approved/rejected`,
Thema `proposed -> active/rejected` und Verknuepfung `proposed -> active/rejected` laufen
nur ueber die Funktionen in `app/memory/approval.py`. Jede verlangt:

- den Hash eines authentisierten, nicht widerrufenen, nicht abgelaufenen UI-Session-Tokens
  (zum Zeitpunkt der Freigabe in `ui_sessions` geprueft),
- die sha256 der exakt angezeigten Bytes. Eine Abweichung scheitert (`shown_bytes_mismatch`).

`memory_approvals` speichert Art, Ziel, Entscheid, Bytes-Hash, Session-Hash (bewusst
kein Fremdschluessel, Sessions werden bereinigt) und DB-Zeit. Die Tabelle ist
append-only. Kein API-Bearer, kein Kanal und kein Modell kann freigeben; ein Test prueft,
dass nur `approval.py` Freigabezeilen anlegt.

### Owner-Entscheide vom 2026-11-10

- **7.1** Kaan (der Grok-Bot) und n8n sind zwei getrennte Kanaele: `grok` (Konten wie
  `kaan_bot`, `grok1`, `grok2`) und `n8n`.
- **7.2** Die Quarantaene speichert und zeigt den vollen Text nach dem Geheimnis-Scan,
  nicht nur Hashes. Bis zur Freigabe kommt er nie in einen Modellkontext.
- **7.3** Freigegebenes Gedaechtnis darf zu Claude und Grok zurueckfliessen.
  `load_memory_context(thread, target, data_class)` liefert nur freigegebene Zeilen.
  Der Klassenfilter (`allowed_classes()` aus `app/kb/contracts.py`) laeuft in SQL **vor**
  dem Ranking. Ziel `ollama` erhaelt alle Klassen, `claude`, `claude_code` und `grok`
  nur `cloud_ok`, `codex` nie etwas (ohne DB-Zugriff). `cloud_redacted` ist fuer
  Cloud-Ziele ausgeschlossen: die Redaktion in `app/redaction/` haengt an einem Auftrag
  und seinen `redaction_maps`; Gespraechsgedaechtnis hat keinen Auftrag. Ein Turn mit
  `local_only` an ein Cloud-Ziel wird mit `data_class_block` abgewiesen. Ein
  `local_only`-Thread liefert einem Cloud-Ziel keinen Verlauf. Budget: Profil, dann
  Verlauf (aelteste zuerst gestrichen), dann Fakten; dieselbe Token-Schaetzung wie
  `compose_dialogue_messages`. Keine Embeddings, nur exakter Thread und einfache
  Stichworte. `num_ctx` bleibt 4096. Das Profil hat keine Klasse und gilt als `local_only`.
  Es gibt keinen Versandcode.
- **7.4** Slack per Socket Mode: nur Vertrag (siehe unten), kein Code.
- **7.5** Rueckweg: beide Wege mit getrenntem Zweck, HMAC-Callback (n8n) und
  SSH-Kommando ([TRANSPORT.md](../TRANSPORT.md)). Nur Vertrag, kein Code.
- **7.6** Gespraechs- und Gedaechtnis-Audit liegt in der bestehenden Tabelle `events`.
  `events.task_id` und `events.to_status` werden NULLable. Ein CHECK erlaubt entweder
  eine Task-Zeile (beide gesetzt) oder eine taskfreie Zeile ohne Status mit Typ
  `assistant.*` oder `memory.*`. Der Append-only-Trigger bleibt. Das Detail-JSON enthaelt
  keinen Nachrichtentext, nur IDs, Hashes, Grundcodes und Kanal/Konto; der Schreiber
  verweigert andere Schluessel. Task-Ansichten filtern `task_id IS NOT NULL`.
- **7.7** Klassen pro Kanal: `deck_ui` frei waehlbar; `slack`, `grok`, `claude` (und
  `n8n`) hoechstens `cloud_ok` oder `cloud_redacted`, `local_only` wird vor jedem Netz mit
  `data_class_block` abgewiesen; `claude_code`-Importe werden bis zum Review auf
  `local_only` gezwungen. Ein DB-CHECK auf `assistant_channel_accounts` erzwingt das.
- **7.8** Der Grok Bot schwaerzt Musterdateien aus echten Exporten; kein Parser, bevor Muster vorliegen (ausserhalb M0/M1).
- **7.9** Alle vier Worker-Konten (zwei Grok-, zwei Claude-Konten) duerfen Dateien in die Inbox legen (Umsetzung M4).
- **7.10** Der Assistent darf Themen **vorschlagen**; der Owner bestaetigt per Klick. Es
  gibt keine automatische Annahme.
- **7.11** Kein hartes Loeschen. Abgelehnte Eintraege behalten `rejected`/`abgelehnt`.
  Es gibt keine Loeschroute und keine Loeschfunktion.
- **7.12** Reihenfolge der Phasen: M1 vor G2; M3 ist Etappe 19.

### Vertrag Slack Socket Mode (7.4, kein Code)

- Eine Slack-App mit Socket Mode baut die Verbindung vom Host aus auf; das Deck oeffnet
  keinen oeffentlichen Eingang.
- Das App-Token liegt nur in der geschuetzten Host-Konfiguration, nie im Repo.
- Der Adapter erzeugt pro Ereignis einen `channel_turn.v1` mit `channel = slack`,
  `ingest_method = live_push`, `external_thread_id` aus Kanal und Thread-Zeitstempel,
  `external_message_id` aus dem Nachrichten-Zeitstempel und `source_ref` wie
  `slack:<kanal>/<ts>`. Datenklasse hoechstens `cloud_ok` oder `cloud_redacted`.
- Ein Ereignis wird bestaetigt, sobald `ingest_turn` committet hat; Wiederholungen von
  Slack sind Duplikate.
- Antworten des Decks entstehen mit `record_deck_reply` und bleiben in Quarantaene.
  Der Versand ist eine spaetere Etappe mit eigenem GO.

### Vertrag Rueckweg (7.5, kein Code)

- **HMAC-Callback fuer n8n**: n8n ruft eine Deck-Adresse mit `POST`, Koerper
  `channel_turn.v1`, Header mit HMAC-SHA256 ueber Zeitstempel und Koerper. Schluessel
  nur in der Host-Konfiguration. Abweisung bei falscher Signatur, Zeitabweichung ueber
  fuenf Minuten oder bekannter Nonce. Zweck: eingehende Turns aus n8n-Ablaeufen.
- **SSH-Kommando**: der bestehende Rueckweg aus [TRANSPORT.md](../TRANSPORT.md) bleibt fuer
  Status und Quittungen zustaendig. Er nimmt keine Gespraechstexte an.
- Beide Wege sind getrennt; keiner kann freigeben.

## Tabellen (Migration `20261110_0064`)

`assistant_channel_accounts`, `assistant_threads`, `assistant_thread_links`,
`assistant_messages`, `memory_facts`, `memory_approvals` sowie die Aenderung an `events`.
Details in [DATENBANK.md](../DATENBANK.md), Umschlag in [KANAELE.md](../KANAELE.md).

## Folgen

- Der Live-Dialog ist in diesem Stand **nicht** umverdrahtet (`app/assistant/http.py`
  bleibt unveraendert). `load_memory_context` ist vorhanden und getestet; **M2 verdrahtet**
  ihn mit Dialog und Oberflaeche, inklusive Quarantaene-Inbox und Freigabe-Klicks.
- `backfill_assistant_turns()` kopiert alte `assistant_turns` auf Wunsch in einen
  Sammel-Thread, als `quarantined`. Sie laeuft nie automatisch; eine Wiederholung ist
  wirkungslos. `assistant_turns` bleibt bestehen.
- Ein Downgrade unter `20261110_0064` verweigert, solange taskfreie Audit-Zeilen oder
  Gespraechszeilen existieren. Nichts wird geloescht.
- `task.v1` und `result.v1` bleiben unveraendert; es gibt keine zweite Task- oder
  Lease-Tabelle.
