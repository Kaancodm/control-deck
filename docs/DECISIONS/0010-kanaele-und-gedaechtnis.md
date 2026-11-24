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
2. **Geheimnis-Scan** mit dem bestehenden Scanner (`app/privacy/scanner.py`), bevor
   ein Audit Konto, Kanal oder einen Vorschlagstitel uebernimmt. Ein Treffer
   speichert den Text nirgends. Im Audit stehen nur sha256 und `secret_rejected`.
   Dieselbe Pruefung gilt fuer kopierte Felder einer Deck-Antwort und fuer den
   zusammengesetzten Faktsatz nach `strip`.
3. **Dedup**: eindeutig ist `(channel, account, external_message_id)`. Ohne Quellen-ID gilt
   `(channel, account, external_thread_id, role, text_sha256, occurred_at)`. `occurred_at`
   bleibt im Schluessel (vom Owner am 2026-11-10 bestaetigt): derselbe Text zu einer
   spaeteren Quellzeit ist ohne Nachrichten-ID ein eigener Quarantaene-Turn. Die
   Quelluhr ordnet nicht; das tut `seq`.
   Ein anderer Text mit derselben Quellen-ID ueberschreibt nichts
   (`duplicate_body_mismatch`, nur die zwei Hashes). Ein Duplikat aendert keinen
   Zustand. Zwei gleichzeitige Aufrufe mit demselben Schluessel ergeben genau eine Zeile.
4. **Quarantaene**: neue Zeilen haben `state = quarantined`. Sie kommen nie in einen
   Modellkontext und nie in eine Suche. Ein Datenschutz-Befund weist nicht ab, sondern
   markiert die Zeile mit `privacy_hold` (siehe «Datenschutz-Markierung»).
5. **Thread-Zuordnung**: eine angenommene Verknuepfung `(channel, account,
   external_thread_id)` fuehrt in einen Themen-Thread. Sonst bleibt die Nachricht
   `thread_unmapped`, und es entsteht ein **Vorschlag** (Thema plus Verknuepfung), den
   der Owner per Klick bestaetigt.
6. **Antworten** gibt das Deck nur in `deck_ui` und `slack`. Genau eine Antwort pro
   eingehender Nachricht erzwingt `UNIQUE (reply_for_message_id)`. Die Kennung
   beginnt mit `deck-reply:`; ein Kanal darf dieses Praefix nicht vergeben.
   Aussen-Kennungen duerfen kein `://` enthalten.

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

- das rohe UI-Cookie. `get_ui_session` prueft Owner, Widerruf, absoluten Ablauf,
  Passwort-Fingerabdruck und die Leerlaufzeit, und sperrt die Zeile,
- die sha256 der exakt angezeigten Bytes. Die Bytes sind eine versionierte
  UTF-8-Nutzlast (`memory-shown.v1`) mit Klasse, Kanal, Konto, Rolle,
  Datenschutz-Markierung (`privacy_hold`), Entscheid, Nachweis-Flag und danach dem
  Text. Eine Korrektur traegt zusaetzlich `correction=1` und die Id der korrigierten
  Freigabe. Eine Abweichung scheitert (`shown_bytes_mismatch`).

`memory_approvals` speichert Art, Ziel, Entscheid, Bytes-Hash und `approver_ref`
(sha256 ueber ein festes Domaenen-Praefix und den Session-Digest, bewusst nicht
der Digest selbst und kein Fremdschluessel, Sessions werden bereinigt) sowie die
DB-Zeit. Eine Korrektur setzt zusaetzlich `corrects_approval_id`. Die Tabelle ist
append-only. Inhaltsspalten von Nachrichten, Fakten, Themen und Verknuepfungen
weisen `UPDATE` ab; `DELETE` auf diesen Tabellen und auf `memory_approvals` weist
die Datenbank ab. Erlaubt ist ein Entscheid aus dem offenen Zustand, das einmalige
Anhaengen an ein Thema und danach nur eine freigegebene Korrektur (siehe
«Unveraenderlichkeit nach dem Entscheid»). Kein API-Bearer, kein Kanal und kein
Modell kann freigeben oder korrigieren; ein Test prueft, dass nur `approval.py`
Freigabezeilen anlegt.

### Owner-Entscheide vom 2026-11-10

- **7.1** Kaan (der Grok-Bot) und n8n sind zwei getrennte Kanaele: `grok` (Konten wie
  `kaan_bot`, `grok1`, `grok2`) und `n8n`.
- **7.2** Die Quarantaene speichert und zeigt den vollen Text nach dem Geheimnis-Scan,
  nicht nur Hashes. Bis zur Freigabe kommt er nie in einen Modellkontext.
- **7.3** Freigegebenes Gedaechtnis darf zu Claude und Grok zurueckfliessen.
  `load_memory_context(thread, target, data_class)` liefert nur freigegebene Zeilen.
  Der Klassenfilter (`allowed_classes()` aus `app/kb/contracts.py`) laeuft in SQL **vor**
  dem Ranking. Ziel `ollama` erhaelt alle Klassen, `claude`, `claude_code` und `grok`
  im Stand M1 nur `cloud_ok`, `codex` nie etwas (ohne DB-Zugriff). Dass M1
  `cloud_redacted` noch nicht an Cloud-Ziele liefert, ist eine voruebergehende
  Abweichung von 7.3 und Plan 3.5: die Redaktion in `app/redaction/` haengt an einem
  Auftrag und seinen `redaction_maps`, Gespraechsgedaechtnis hat keinen Auftrag. Die
  auftragsfreie Redaktion ist Issue
  [#76](https://github.com/Kaancodm/control-deck/issues/76). Zeilen mit
  `privacy_hold` gehen nie an ein Cloud-Ziel, auch nach der Freigabe. Ein Turn mit
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
- **7.12** Reihenfolge der Phasen: M1 vor G2; M3 ist Etappe 19. «Dialog nutzt
  Thread» ist das offene Ticket **M1b** (Issue #77, nach dem Merge des UI-PR, vor G2),
  nicht Bestandteil dieses Stands.

### Weitere Planregeln (Vertrag, kein Code in M1)

- **Fremde KIs (Plan 3.6).** Fremde KIs duerfen hoechstens ueber die bestehende
  API einen Auftrag anlegen (`requested_by=api`, Status `awaiting_approval`).
  Solche Auftraege tragen `memory_eligible=false`: kein Gedaechtnis-Kontext
  hinein, kein Ergebnis-Export ins Gedaechtnis. Kein fremdes Modell schreibt ins
  Gedaechtnis und keines gibt frei. M1 aendert die Auftragstabelle nicht.
- **Kanal gegen Worker (Plan 5.5).** Kanal-Adapter und Worker nutzen getrennte
  Tokens. Ein Kanal-Token arbeitet nicht, ein Worker-Token nimmt keine Turns an.
- **Inhalte sind Daten (Plan 5.9).** Nachrichtentext, Titel und Fakten sind
  Daten, nie Anweisungen an das Deck. Sie loesen keine Aktion aus.
- **Kein Transport-Retry (Plan 2.5).** Ein fehlgeschlagener Versand wird nicht
  automatisch wiederholt. Der Owner stoesst ihn neu an.
- **Kanal-Aktionen nur als Entwurf (Plan 5.2).** Eine Aktion aus einem Kanal
  wird hoechstens ein Auftrag im Status `awaiting_approval`. M1 versendet nichts.

### Owner-Entscheide vom 2026-11-10 (zweite Runde)

1. **Datenschutz-Treffer markiert statt abgewiesen.** Ein Befund ausserhalb von
   `local_only` speichert die Zeile in der Quarantaene mit `privacy_hold`. Geheimnisse
   bleiben eine Abweisung ohne Speicherung. Details unten.
2. **`deck_ui` ohne Blockliste.** Ohne konfigurierte Blockliste nimmt `deck_ui` auch
   Cloud-Klassen an und markiert nur bei einem Befund der eingebauten Muster. Andere
   Kanaele und eine unlesbare Blockliste markieren `blocklist_unavailable`. Der
   Versand an Cloud-Ziele bleibt ohne Blockliste gesperrt.
3. **`cloud_redacted`.** M1 liefert an Cloud-Ziele nur `cloud_ok`. Das ist eine
   voruebergehende Abweichung von 7.3 und Plan 3.5, verfolgt in Issue #76
   (auftragsfreie Redaktion). Das Verhalten des Codes aendert sich nicht.
4. **Entscheide sind nicht endgueltig.** Es gibt einen freigegebenen Korrekturweg
   (`corrects_approval_id`, `correct_*`). Siehe «Unveraenderlichkeit nach dem Entscheid».
5. **Aufteilung angenommen.** Tickets: M1b #77, M2 #78, Umstufung von `data_class`
   (M4/N3) #79, Redaktion #76.
6. **`source_date` bleibt im Ersatz-Dedup-Schluessel.** Vom Owner bestaetigt, kein
   Restrisiko des Bau-Agenten mehr.

### Datenschutz-Markierung (vom Owner am 2026-11-10 bestaetigt)

- Fuer `cloud_ok` und `cloud_redacted` gilt bei der Annahme dasselbe Sperr-Praedikat
  wie beim Auftrags-Gate (`findings_block`, Blockliste plus eingebaute Muster).
  `cloud_ok` sperrt jeden Befund; `cloud_redacted` sperrt Geheimnisse und Befunde,
  die die Redaktion nicht abdeckt (ein Blocklisten-Treffer allein sperrt dort nicht).
  `local_only` liest die Blockliste nicht.
- Ein Treffer weist **nicht** ab. Die Zeile wird wie jede andere mit vollem Text in
  der Quarantaene gespeichert (7.2) und traegt die unveraenderliche Spalte
  `privacy_hold`. Codes: `privacy_block` (Befund oder Text, den der Normalisierer
  abweist) und `blocklist_unavailable` (Blockliste fehlt dort, wo sie verlangt ist,
  oder ist konfiguriert, aber unlesbar). Das Audit der angenommenen Zeile traegt den
  Code, nie Text. Die Pruefung liegt in `memory_privacy_hold` (`app/privacy/gate.py`);
  das Auftrags-Gate (`inspect_task`, `findings_block`) bleibt unveraendert.
- Ohne konfigurierte Blockliste: Kanal `deck_ui` (und Deck-Antworten auf eine
  `deck_ui`-Nachricht) prueft mit den eingebauten Mustern allein und markiert nur
  bei einem echten Befund. Alle anderen Kanaele markieren `blocklist_unavailable`.
  Die Kanalliste steht in `BLOCKLIST_OPTIONAL_CHANNELS` (`app/memory/policy.py`).
- Dasselbe gilt fuer `propose_fact` (Klasse und Kanal der Quellnachricht). Ein
  Faktum aus einer markierten Nachricht erbt deren Markierung. Eine Deck-Antwort
  auf eine markierte Nachricht erbt sie ebenfalls. Treffen eigene und geerbte
  Markierung zusammen, gilt `privacy_block` vor `blocklist_unavailable`.
- Der Backfill (`backfill_assistant_turns`) markiert Altzeilen genauso wie
  `ingest_turn` auf `deck_ui`; die Antwort erbt die Markierung der Owner-Zeile.
- Die Markierung ist dauerhaft. Eine spaeter konfigurierte Blockliste hebt
  `blocklist_unavailable` nicht auf; solche Zeilen gehen nie an Cloud-Ziele. Ein
  Umweg ueber eine neue, saubere Zeile bleibt dem Owner ueberlassen.
- Geheimnisse bleiben eine Abweisung vor jeder Speicherung (`secret_rejected`, nur
  der Hash). Abweisungen bleiben ausserdem `unknown_account`, `account_inactive`,
  `data_class_block`, `invalid_method`, `reply_path_required`,
  `thread_class_block` und `invalid_envelope`.
- Der Owner sieht die Markierung in den gezeigten Bytes (`privacy_hold=...`) und
  darf eine markierte Zeile freigeben. `load_memory_context` schliesst markierte
  Nachrichten und Fakten (und Fakten aus markierten Nachrichten) fuer `claude`,
  `claude_code` und `grok` in SQL vor dem Ranking aus. `ollama` erhaelt sie; der
  Geheimnis-Scan gilt dort weiter.
- Die erneute Pruefung beim Lesen fuer Cloud-Ziele bleibt und ist ohne Blockliste
  weiterhin gesperrt (fail-closed).

### Unveraenderlichkeit nach dem Entscheid

- Inhaltsspalten, `data_class` und `privacy_hold` aendern sich nie. Bei Nachrichten
  darf `thread_id` nur von leer auf ein Thema wechseln, bei Verknuepfungen ist
  `thread_id` nach dem ersten Entscheid fest.
- Entscheid-Spalten sind nach dem ersten Entscheid eingefroren: Nachrichten
  `state`, `evidence_status`, `approval_id`; Fakten zusaetzlich `valid_from`,
  `valid_to`, `superseded_by` (statt `state` heisst es `status`); Themen `status`,
  `approval_id`; Verknuepfungen `state`, `approval_id`.
- Einzige Ausnahme ist die **Korrektur** durch den Owner (`correct_message`,
  `correct_fact`, `correct_thread_topic`, `correct_thread_link` in
  `app/memory/approval.py`). Jede verlangt das rohe UI-Cookie und die sha256 der
  gezeigten Bytes (mit `correction=1` und `corrects_approval_id`), sperrt dieselben
  Zeilen in derselben Reihenfolge wie der erste Entscheid, schreibt genau eine neue
  Freigabezeile mit `corrects_approval_id` = bisherige `approval_id`, setzt die
  `approval_id` des Ziels darauf und auditiert `memory.*_corrected` ohne Text.
- Die Datenbank laesst die Aenderung nur zu, wenn die neue `approval_id` auf eine
  Zeile in `memory_approvals` zeigt, deren `corrects_approval_id` die alte
  `approval_id` ist, deren Art und Ziel zur Zeile passen und deren Entscheid zum
  neuen Zustand passt; der neue Zustand ist ein entschiedener Zustand (nie zurueck
  nach `quarantined` oder `proposed`). `valid_from` bleibt auch bei einer Korrektur fest.
  Ein zusammengesetzter Fremdschluessel verlangt, dass eine Korrektur dieselbe Art
  und dasselbe Ziel wie die korrigierte Freigabe hat; jede Freigabe wird hoechstens
  einmal korrigiert, Korrekturen bilden eine Kette.
- Korrekturen: Nachricht `approved` <-> `rejected` oder Nachweis
  (`bewiesen`/`unbewiesen`/`abgelehnt`); Faktum widerrufen, wieder annehmen,
  `valid_to` setzen oder `superseded_by` auf ein freigegebenes Faktum setzen; Thema
  `active` <-> `rejected`; Verknuepfung loesen (`active` -> `rejected`) und wieder
  aktivieren, wenn das Thema aktiv ist, die Klassenregel passt und keine andere
  offene Verknuepfung fuer denselben Aussen-Thread besteht (`link_conflict`). Ein
  Entscheid ohne Aenderung ist `no_change`, eine offene Zeile `not_decided`.
- Kein hartes Loeschen (7.11). Angehaengte Nachrichten behalten ihr Thema, auch wenn
  das Thema oder die Verknuepfung korrigiert wird.
- Vom Owner am 2026-11-10 bestaetigt: Der Ersatz-Dedup-Schluessel ohne Nachrichten-ID
  enthaelt `source_date`. Ein Sender mit neuem Zeitstempel legt eine weitere Kopie in
  die Quarantaene; ohne `source_date` wuerden echte Wiederholungen (z. B. «ok»)
  verschluckt.
- Betriebshinweis: `TRUNCATE` umgeht die Zeilen-Trigger. Das Recht dafuer soll der
  Anwendungsrolle beim Prod-Rollout entzogen werden (Rollen liegen ausserhalb des Repos).

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

Zuordnung frueherer Review-Findings zu Tickets:

| Finding | Thema | Ticket |
| --- | --- | --- |
| F1 | Dialog nutzt Thread | M1b #77 |
| F4 | `cloud_redacted` an Cloud-Ziele | #76 |
| F5 | Deck-Antwort: `persist` oder zweiter Klick | M2 #78 |
| F8 | Grundcodes und feste UI-Labels | M2 #78 |
| F10 | Leser fuer taskfreie Audit-Zeilen | M2 #78 |
| N5 | Korrekturweg (gebaut), UI dafuer | M2 #78 |
| F6 | Konten der Worker anlegen | M4 #79 |
| F9 | Import-Stapel in `memory_approvals.kind`, Sortierung nach `occurred_at` | M4 #79 |
| N3 | `data_class` umstufen | M4 #79 |

Ohne Ticket, als Betriebsschritt beim Prod-Rollout: `TRUNCATE`-Recht der
Anwendungsrolle entziehen.

Bekannte Grenzen des Korrekturwegs: `correct_fact` kann `valid_to` und
`superseded_by` setzen, aber nicht leeren (bewusst nicht geplant; bei Bedarf eigenes
Ticket). Ein Nachfolger muss beim Korrigieren selbst aktuell sein; Faktum und
Nachfolger werden zusammen in Id-Reihenfolge gesperrt. Das schliesst Kreise aus,
Ketten (A -> B, spaeter B -> C) bleiben moeglich. Wird ein Nachfolger widerrufen, bleibt das alte
Faktum ausgeblendet, bis der Owner ein neues Faktum freigibt. `thread_id` einer
angehaengten Nachricht bleibt nach dem Loesen einer Verknuepfung bestehen.

- Der Live-Dialog ist in diesem Stand **nicht** umverdrahtet (`app/assistant/http.py`
  bleibt unveraendert). `load_memory_context` ist vorhanden und getestet und traegt
  `evidence_status`, damit ein Aufrufer `unbewiesen` beschriften kann. **M1b**
  (Issue #77, nach dem Merge des UI-PR, vor G2) haengt den Dialog an den
  Themen-Thread. Bis dahin gilt «Dialog nutzt Thread» nicht als erledigt. **M2**
  (Issue #78) liefert die Oberflaeche: Quarantaene-Inbox, Freigabe- und
  Korrektur-Klicks, Anzeige von `privacy_hold`, Labels fuer die Grundcodes und
  einen Leser fuer taskfreie `assistant.*`- und `memory.*`-Zeilen. Deck-Antworten
  bleiben bis zu diesem M2-Entscheid in Quarantaene.
- `cloud_redacted` geht im Stand M1 noch nicht an Cloud-Ziele. Das ist eine
  voruebergehende Abweichung von 7.3 und Plan 3.5, verfolgt in Issue #76
  (auftragsfreie Redaktion). Der Code bleibt bis dahin bei `cloud_ok`.
- Ein Weg zur Umstufung von `data_class` (u. a. fuer `claude_code`-Importe, M4/N3)
  ist Issue #79.
- `backfill_assistant_turns()` kopiert alte `assistant_turns` auf Wunsch in einen
  Sammel-Thread, als `quarantined`. Sie laeuft nie automatisch; eine Wiederholung ist
  wirkungslos. `assistant_turns` bleibt bestehen.
- Ein Downgrade unter `20261110_0064` verweigert, solange taskfreie Audit-Zeilen oder
  Gespraechszeilen existieren. Nichts wird geloescht.
- `task.v1` und `result.v1` bleiben unveraendert; es gibt keine zweite Task- oder
  Lease-Tabelle.
