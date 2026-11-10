# Kanaele und Gedaechtnis (M0/M1)

Stand dieses Textes ist der Code unter `app/memory/`, das Schema
`schemas/channel_turn.v1.json` (dieselben Bytes in `app/schemas/channel_turn.v1.json`)
und die Migration `20261110_0064`. `parse_turn` liest das Schema aus dem Paket
`app.schemas`. Die Entscheide stehen in
[ADR 0010](DECISIONS/0010-kanaele-und-gedaechtnis.md), die Tabellen in
[DATENBANK.md](DATENBANK.md).

Dieser Text beschreibt, was der Code tut. Er ist keine Betriebsfreigabe.
Nichts in `app/memory/` sendet eine Nachricht an einen Kanal.

## Umschlag `channel_turn.v1`

`parse_turn` nimmt ein Objekt nur an, wenn das JSON Schema (Draft 2020-12) und
das Pydantic-Modell es beide annehmen. Zusaetzliche Felder sind verboten, am
Umschlag und in `source_ref`. Danach muss `occurred_at` als Zeitpunkt mit
Zeitzone parsbar sein. Ein String, der nur dem Muster genuegt, aber kein Datum
ist (zum Beispiel Monat 13), ergibt `invalid_envelope`.

`occurred_at` ist die Uhr der Quelle und nicht vertrauenswuerdig. Die
Reihenfolge einer gespeicherten Nachricht kommt aus `received_at` und `seq`.

### Pflichtfelder

Der Schluessel muss vorhanden sein. `external_message_id` darf `null` sein.

| Feld | Grenze |
| --- | --- |
| `schema_version` | genau `channel_turn.v1` |
| `channel` | `deck_ui`, `slack`, `n8n`, `grok`, `claude`, `claude_code` |
| `account` | `^[a-z][a-z0-9_]{0,31}$` (1 bis 32 Zeichen) |
| `external_thread_id` | 1 bis 200. Erstes Zeichen Buchstabe oder Ziffer, danach `A-Z` `a-z` `0-9` und `._:/#+-`. `://` ist verboten |
| `external_message_id` | `null` oder wie die Thread-Kennung, aber nicht mit Praefix `deck-reply:` |
| `role` | `owner` oder `assistant` |
| `text` | 1 bis 16000 Zeichen |
| `occurred_at` | `YYYY-MM-DDTHH:MM:SS` mit optionalem Bruchteil von 1 bis 6 Stellen und `Z` oder Offset `+HH:MM` oder `-HH:MM` |
| `data_class` | `local_only`, `cloud_redacted`, `cloud_ok` |
| `ingest_method` | `deck_ui`, `live_push`, `import_file`, `owner_typed` |
| `source_ref` | Objekt, siehe unten |

### `source_ref`

| Feld | Pflicht | Grenze |
| --- | --- | --- |
| `ref` | ja | 1 bis 300. Praefix `deck_ui`, `slack`, `n8n`, `grok`, `claude`, `claude_code`, `file` oder `backfill`, dann ein Doppelpunkt, dann 1 bis 280 Zeichen aus `A-Z`, `a-z`, `0-9` und `._/#+-`. Kein zweiter Doppelpunkt, kein `://` |
| `sha256` | nein | weggelassen, `null` oder 64 Kleinbuchstaben-Hexzeichen |

### Optionales Feld

`in_reply_to` darf fehlen oder `null` sein. Ein String hat dieselbe Grenze wie
`external_thread_id`, inklusive des Verbots von `://`. Der Praefix `deck-reply:`
ist hier erlaubt, damit ein Turn auf eine Deck-Antwort zeigen kann. Diesen
Praefix vergibt nur `record_deck_reply`. Ein Kanal darf ihn nicht als
`external_message_id` setzen.

## Kanaele und Klassendecke

Die Decke steht in `CHANNEL_CLASS_CEILING` (`app/memory/policy.py`). Eine
Kontozeile darf die Menge verengen, nicht erweitern. Die Migration legt nur
`deck_ui` / `owner` aktiv an, mit allen drei Klassen. Jedes andere Konto ist
`unknown_account`, bis eine Zeile existiert. `active = false` ergibt
`account_inactive`.

| Kanal | Decke | Erzwungene Klasse | Deck-Antwort |
| --- | --- | --- | --- |
| `deck_ui` | `local_only`, `cloud_redacted`, `cloud_ok` | keine | ja |
| `slack` | `cloud_redacted`, `cloud_ok` | keine | ja |
| `n8n` | `cloud_redacted`, `cloud_ok` | keine | nein |
| `grok` | `cloud_redacted`, `cloud_ok` | keine | nein |
| `claude` | `cloud_redacted`, `cloud_ok` | keine | nein |
| `claude_code` | nur `local_only` | Umschlag wird auf `local_only` gesetzt (`class_forced`) | nein |

`CLOUD_CHANNELS` sind `slack`, `n8n`, `grok`, `claude`. Ein Thema mit
`local_only` wird daran nicht verknuepft (`local_only_cloud_link`).
`deck_ui` und `claude_code` sind keine Cloud-Kanaele.

`REPLY_CHANNELS` sind `deck_ui` und `slack`. Dort nimmt `ingest_turn` keine
Rolle `assistant` an.

Methoden:

- `ingest_method = deck_ui` nur auf Kanal `deck_ui`.
- Kanal `deck_ui` nur mit `deck_ui` oder `owner_typed`.
- Die anderen Kanaele duerfen `live_push`, `import_file` und `owner_typed`.

Sonst `invalid_method`.

Dieselbe Decke erzwingen die Checks
`ck_assistant_channel_accounts_cloud_ceiling` und
`ck_assistant_channel_accounts_claude_code_local`.

## `ingest_turn`

Der Aufrufer besitzt die Transaktion. Eine Abweisung und ein Duplikat aendern
keine Nachricht, kein Thema und keine Verknuepfung. Sie haengen eine Audit-Zeile
an. Eine neue Zeile hat `state = quarantined` und
`evidence_status = unbewiesen`.

Die Funktion liefert `IngestOutcome`: `status` (`accepted`, `duplicate`,
`rejected`), `reason`, `message_id`, `thread_id`, `link_id`.

Reihenfolge im Code:

1. **Umschlag.** `parse_turn`. Fehler `invalid_envelope`. Liegt in `account`
   oder `channel` ein Geheimnis, enthaelt das Audit nur `reason=secret_rejected`
   und, wenn `text` ein String ist, `text_sha256`. Sonst nur, wenn der Rohwert
   wohlgeformt und kein Geheimnis ist: `channel` (bekannter Kanal), `account`
   (Kontomuster) und `text_sha256`. Kein Text, keine Kennung.
2. **Geheimnis-Scan, vor jedem Detail und vor dem Insert.** `contains_secret`
   auf Konto, Kanal, beiden Aussen-Kennungen, `in_reply_to`, `source_ref.ref`,
   `text` und dem zusammengesetzten Vorschlagstitel
   `{kanal}/{konto}: ` plus hoechstens 150 Zeichen der Thread-Kennung. Treffer:
   `secret_rejected`. Gespeichert wird nichts. Das Audit hat nur `reason` und
   `text_sha256`, nie das Konto und nie den Titel.
3. **Allowlist.** Zeile `(channel, account)` mit Lesesperre. Fehlt sie:
   `unknown_account`. Ist `active` falsch: `account_inactive`.
4. **Klasse.** `claude_code` wird auf `local_only` gezwungen; das Audit setzt
   dann `class_forced`. Liegt die Klasse nicht in der Kanaldecke oder nicht in
   `allowed_data_classes` des Kontos: `data_class_block`. Noch nichts gespeichert.
5. **Methode.** Verstoss wie oben: `invalid_method`.
6. **Antwortpfad.** Rolle `assistant` auf `deck_ui` oder `slack`:
   `reply_path_required`. Speichern nur ueber `record_deck_reply`. Auf
   `n8n`, `grok`, `claude` und `claude_code` darf die Rolle `assistant` durch
   `ingest_turn`.
7. **Datenschutz, nur ausserhalb `local_only`.** Dieselbe Sperre wie
   `GateReport` (`findings_block` in `app/privacy/gate.py`), mit der Blockliste
   aus `get_privacy_settings`. `cloud_ok` sperrt jeden Befund. `cloud_redacted`
   sperrt Geheimnisse und Regeln ausserhalb der schwaerzbaren Menge; ein
   Blocklisten-Treffer allein sperrt `cloud_redacted` nicht. `local_only` liest
   die Blockliste nicht. Fehlt die Konfiguration oder weist der Normalisierer
   den Text ab, gilt die Zeile als gesperrt (`privacy_block`). Nichts gespeichert.
   Das Audit traegt den Grund und die schon gesetzten Felder, nicht den Text.
8. **Thema, unter Sperre.** Die offene Verknuepfung (`state <> rejected`) wird
   mit `FOR UPDATE` geladen, danach das Thema. `thread_id` wird nur gesetzt,
   wenn die Verknuepfung `active` ist, das Thema `active` ist und `class_admits`
   die Nachrichtenklasse unter der Themenklasse zulaesst. Ist die Verknuepfung
   aktiv und das Thema aktiv, die Klasse aber nicht zulaessig:
   `thread_class_block`, vor dem Insert, nichts gespeichert. Eine Verknuepfung
   mit `proposed` mappt nicht; die Zeile bleibt unzugeordnet und der Vorschlag
   wird wiederverwendet.
9. **`in_reply_to`.** Aufloesung ueber `(channel, account, external_message_id)`.
   Fehlt das Ziel, setzt das Audit `in_reply_to_unresolved`. Die Nachricht wird
   trotzdem angenommen; `in_reply_to_message_id` bleibt dann leer.
10. **Dedup und Insert.** `INSERT ... ON CONFLICT DO NOTHING`. Mit
    Quellen-ID gilt `UNIQUE (source_channel, source_account, external_message_id)`.
    Ohne Quellen-ID gilt der partielle Unique-Index auf
    `(source_channel, source_account, external_thread_id, role, text_sha256, source_date)`
    (`source_date` ist der geparste `occurred_at`). `source_date` bleibt im
    Schluessel: derselbe Text zu einer spaeteren Quellzeit ist ohne Nachrichten-ID
    ein eigener Turn und bleibt in Quarantaene. Die Quelluhr ordnet nicht; das
    tut `seq`. Ein anderer Text mit derselben Quellen-ID ueberschreibt die erste
    Zeile nicht. Das Audit ist dann `duplicate_body_mismatch` und enthaelt nur
    `reason`, `text_sha256` und `existing_text_sha256`. `NULL` in
    `external_message_id` kollidiert auf dem ersten Schluessel nicht mit einem
    anderen `NULL`. Gleicher Text: `reason = duplicate`, Audit
    `assistant.turn_duplicate`. `message_id` ist die vorhandene Zeile, wenn die
    Suche sie findet.
11. **Vorschlag, nur nach einem neuen Insert ohne `thread_id`.** Das Audit setzt
    `thread_unmapped`. `_propose_link` legt in einem Savepoint ein Thema
    `proposed` (`title_origin = assistant_proposed`, Klasse der Nachricht, Titel
    wie oben) und eine Verknuepfung `proposed` an. Eine schon offene
    Verknuepfung (`state <> rejected`) wird wiederverwendet. Zwei gleichzeitige
    Vorschlaege behalten eine Verknuepfung; der verlorene Themen-Insert faellt
    mit dem Savepoint zurueck (`IntegrityError` wird nur weitergereicht, wenn
    danach keine Verknuepfung sichtbar ist). Ergebnis `accepted` mit
    `reason = thread_unmapped`. Mit gesetztem `thread_id`: `accepted`,
    `reason` leer. Audit `assistant.turn_accepted` mit `message_id` und, wenn
    vorhanden, `thread_id` und `link_id`.

`class_admits` folgt `allowed_classes`: ein Thema `local_only` nimmt alle drei
Klassen auf, `cloud_redacted` nimmt `cloud_redacted` und `cloud_ok`,
`cloud_ok` nur `cloud_ok`.

Der Vorschlag ruft `check_link_class` auf. Ein Cloud-Kanal mit Klasse
`local_only` wuerde `local_only_cloud_link` werfen, nicht als `IngestOutcome`.
Schritt 3 weist `local_only` auf Cloud-Kanaelen schon vorher ab.

Gespeichert wird zusaetzlich: `text_sha256` als sha256 der UTF-8-Bytes,
`source_channel`, `source_account`, `source_ref`, optionales
`source_ref_sha256`, `method = ingest_method`. `reply_for_message_id` bleibt
leer.

### Grundcodes von `ingest_turn`

| `reason` | `status` | Audit |
| --- | --- | --- |
| `invalid_envelope` | `rejected` | `assistant.turn_rejected` |
| `unknown_account` | `rejected` | `assistant.turn_rejected` |
| `account_inactive` | `rejected` | `assistant.turn_rejected` |
| `data_class_block` | `rejected` | `assistant.turn_rejected` |
| `invalid_method` | `rejected` | `assistant.turn_rejected` |
| `reply_path_required` | `rejected` | `assistant.turn_rejected` |
| `secret_rejected` | `rejected` | `assistant.turn_rejected` (nur `reason` und `text_sha256`) |
| `privacy_block` | `rejected` | `assistant.turn_rejected` |
| `thread_class_block` | `rejected` | `assistant.turn_rejected` |
| `duplicate` | `duplicate` | `assistant.turn_duplicate` |
| `duplicate_body_mismatch` | `duplicate` | `assistant.turn_duplicate` (nur die zwei Hashes) |
| `thread_unmapped` | `accepted` | `assistant.turn_accepted` |
| leer | `accepted` | `assistant.turn_accepted` |

### `record_deck_reply`

Speichert die Antwort des Decks auf eine vorhandene Nachricht. Sie sendet
nichts. Zuerst wird die Verknuepfung gesperrt, dann die eingehende Zeile mit
`FOR UPDATE` (dieselbe Reihenfolge wie `decide_thread_link`). Vor jedem Audit
und vor dem Insert prueft der Code die kopierten Felder auf Geheimnisse:
Konto, Kanal, Thread-Kennung, `source_ref` der Quelle, die neue Kennung, das
neue `source_ref` und den Text. Ein Treffer schreibt nur `secret_rejected` und
den Hash.

Die Zeile ist `quarantined`, Rolle `assistant`, Klasse der Quelle.
`thread_id` kommt von der aktiven Verknuepfung, wenn das Thema die Klasse
zulaesst, sonst bleibt die `thread_id` der Quelle. Eine Deck-Antwort wird
nicht mit `thread_class_block` abgewiesen. `reply_for_message_id` und
`in_reply_to_message_id` zeigen auf die Quelle. `external_message_id` ist
`deck-reply:` plus die Id. `source_ref` ist `{kanal}:reply/{id}`.
`source_date` ist `clock_timestamp()`. Methode `deck_ui` auf `deck_ui`, sonst
`live_push`. `evidence_status` ist `unbewiesen`. Dieselbe Datenschutz-Sperre
wie bei `ingest_turn` gilt fuer die Klasse der Quelle.

`UNIQUE (reply_for_message_id)` laesst eine zweite Antwort zum Duplikat werden.
Ein anderer Text ueberschreibt den ersten nicht (`duplicate_body_mismatch`,
nur die zwei Hashes). Die Kennung `deck-reply:` kann ein Kanal nicht selbst
vergeben.

| Ausgang | Bedingung |
| --- | --- |
| wirft `reply_target_missing` | Ziel-Id existiert nicht. Keine Audit-Zeile |
| `reply_channel` | Kanal nicht in `deck_ui`/`slack` oder Rolle nicht `owner`. Audit `assistant.reply_rejected` |
| `reply_target_rejected` | Ziel hat `state = rejected`. Audit `assistant.reply_rejected` |
| `invalid_reply` | Text kein String, nach `strip` leer, oder laenger als 16000 Zeichen. Audit `assistant.reply_rejected` |
| `secret_rejected` | Geheimnis in Text oder kopiertem Feld. Nichts Neues gespeichert. Audit nur Grund und Hash |
| `privacy_block` | Datenschutz-Sperre fuer die Klasse der Quelle. Nichts Neues gespeichert |
| `duplicate` | zweite Antwort, gleicher Text. Audit `assistant.reply_duplicate` |
| `duplicate_body_mismatch` | zweite Antwort, anderer Text. Audit nur die zwei Hashes |
| `accepted`, `reason` leer | erste Antwort, bleibt `quarantined`. Audit `assistant.reply_recorded` |

Ein Ziel in `quarantined` oder `approved` darf eine Antwort bekommen. Die
Antwort selbst bleibt `quarantined`. Der gespeicherte Text wird nicht
getrimmt; nur die Leere-Pruefung nutzt `strip`. `ingest_turn` trimmt `text`
nicht und nimmt ein Leerzeichen an, weil das Schema nur `minLength` 1 verlangt.

## Freigabe

Nur `app/memory/approval.py` setzt eine Zeile aus dem Wartestatus. Es gibt dort
keine Loeschfunktion. `memory_approvals` verweigert `UPDATE` und `DELETE`.
Abgelehnt bleibt `rejected` beziehungsweise `abgelehnt`.

Jede Entscheidung verlangt:

- `session_token`: das rohe UI-Cookie. `get_ui_session` bildet daraus
  `token_digest` und verlangt eine Owner-Zeile (`authenticated`), ohne
  `revoked_at`, mit `expires_at` nach der Datenbankuhr, mit passendem
  Passwort-Fingerabdruck und mit `last_active_at` innerhalb der Leerlaufzeit
  (30 Minuten, solange die Auth-Einstellungen das nicht enger setzen). Die
  Zeile wird mit `for_update` gesperrt. Sonst `owner_session_required`.
  Gespeichert wird nicht dieser Digest, sondern `approver_ref`: sha256 ueber
  das feste Praefix `deck-memory-approver`, ein NUL-Byte und den Digest.
  Das ist kein Nachschlageschluessel und kein Fremdschluessel.
- `shown_sha256`: sha256-Hex (klein) der kanonischen UTF-8-Bytes, die der
  Owner gesehen hat. Abweichung oder anderes Format: `shown_bytes_mismatch`.
  Vergleich mit `hmac.compare_digest`.

Die Bytes beginnen mit der Zeile `memory-shown.v1`, dann Kopfzeilen
`schluessel=wert`, dann eine Leerzeile, dann der Text. `mark_proven` ist `1`
oder `0`.

| Ziel | Kopfzeilen, dann Text |
| --- | --- |
| Nachricht | `kind=message`, `data_class`, `channel`, `account`, `role`, `decision`, `mark_proven`. Text ist `text` |
| Faktum | `kind=fact`, `data_class`, `channel`, `account`, `decision`, `mark_proven`. Text ist Satz, Subjekt, Praedikat, Objekt, je eine Zeile |
| Thema | `kind=thread`, `data_class`, `decision`, `mark_proven`. Text ist `title`. `create_owner_thread` hasht den Titel, bevor die Zeile existiert, mit `decision=approved` und `mark_proven=0` |
| Verknuepfung | `kind=thread_link`, `data_class` des Themas, `channel`, `account`, `thread_id`, `decision`, `mark_proven`. Text ist die Aussen-Kennung, Zeilenumbruch, Titel |

`decision` ist `approved` oder `rejected`, sonst `invalid_decision`. Ein zweiter
Entscheid auf dasselbe `(kind, target_id)` ist `already_decided`.

| Funktion | Uebergang |
| --- | --- |
| `decide_message` | `quarantined` nach `approved` oder `rejected`. `mark_proven=True` setzt bei Annahme `bewiesen`, sonst bleibt `unbewiesen`. Ablehnung setzt `abgelehnt` |
| `decide_fact` | `proposed` nach `approved` oder `rejected`, Nachweis wie bei der Nachricht. Annahme zusaetzlich nur, wenn die Quellnachricht existiert und nicht `rejected` ist (`source_rejected`) |
| `decide_thread_topic` | `proposed` nach `active` oder `rejected`. Kein Nachweis-Feld |
| `decide_thread_link` | `proposed` nach `active` oder `rejected`. Annahme nur auf ein Thema mit `status = active` (`thread_not_active`). `local_only` auf einem Cloud-Kanal: `local_only_cloud_link`. Optionales `target_thread_id` muss bei Ablehnung das Thema der Verknuepfung sein (`invalid_decision`). Die gezeigten Bytes gehoeren zum gewaehlten Thema. Bei Annahme haengt der Code ungemappte Nachrichten dieses Aussen-Threads an, deren Klasse das Thema aufnimmt, und zaehlt sie als `attached` |
| `create_owner_thread` | legt sofort `active` an (`title_origin = owner`) und eine Freigabe `approved`. Der Hash deckt den getrimmten Titel |

`bewiesen` entsteht hier nur mit `mark_proven=True`. Ein Klick ohne das Flag
laesst `unbewiesen` stehen. Die Datenbank verlangt fuer `bewiesen` eine
`approval_id`.

Vorschlaege ohne Session, jeder Aufrufer:

- `propose_fact` speichert `proposed` / `unbewiesen`, Klasse und Herkunft der
  Quellnachricht. Der Satz ist nach Trim nicht leer, die Rohlaenge hoechstens
  4000; gespeichert wird der getrimmte Satz. `subject` und `predicate` bis 200,
  `object` bis 1000, keines nur Weissraum, gespeichert getrimmt. Zeitpunkte mit
  Zeitzone, `valid_to` nicht vor `valid_from`.
  Die Quellnachricht wird mit `FOR UPDATE` gelesen. `rejected` ergibt
  `source_rejected`. Nach dem Trim prueft der Scan Satz, Subjekt, Praedikat,
  Objekt, die vier Felder mit Zeilenumbruch verbunden und die Form
  `{subject} | {predicate} | {object} ({sentence})`. Geheimnis:
  Audit `memory.fact_rejected_input` mit `secret_rejected` und Hash, dann
  Fehler `secret_rejected`, nichts gespeichert. `decide_fact` liest die Quelle
  auf dem Annahme-Pfad erneut mit `FOR UPDATE` und verweigert `rejected` mit
  demselben Code. Eine Ablehnung des Faktums bleibt moeglich.
- `propose_thread_topic` speichert `proposed`, `title_origin = assistant_proposed`.
  Titel nach Trim 1 bis 200. Geheimnis im Titel: `secret_rejected`, ohne
  Audit-Zeile, nichts gespeichert.

Weitere Fehler: `not_found`, `invalid_fact`, `invalid_title`,
`invalid_data_class`.

Akteur der Entscheidungs-Audits ist `operator`. `system` schreibt nur
`memory.fact_proposed`, `memory.topic_proposed` und `memory.fact_rejected_input`.
Geschrieben werden:

- `memory.message_approved`, `memory.message_rejected`
- `memory.fact_proposed`, `memory.fact_rejected_input`, `memory.fact_approved`, `memory.fact_rejected`
- `memory.topic_proposed`, `memory.topic_created`, `memory.topic_approved`,
  `memory.topic_rejected`
- `memory.link_approved`, `memory.link_rejected`

### Audit-Schreiber

`record_conversation_event` schreibt in `events` mit `task_id`, `from_status`
und `to_status` leer. Akteur nur `system` oder `operator`. Der Typ matcht
`^(assistant|memory)\.[a-z][a-z0-9_]{0,48}$`. Ein anderer Typ oder Akteur
ergibt `invalid_audit_event`. Ein anderer Schluessel oder ein freier Text
ergibt `invalid_audit_detail`. Beides schreibt keine Zeile. Erlaubte Schluessel:
`reason`, `channel`, `account`, `role`, `method`, `data_class`, `kind`,
`decision`, `evidence_status`, `message_id`, `existing_message_id`,
`reply_for_message_id`, `thread_id`, `link_id`, `fact_id`, `approval_id`,
`text_sha256`, `existing_text_sha256`, `shown_sha256`, `class_forced`, `thread_unmapped`,
`in_reply_to_unresolved`, `inserted`, `duplicates`, `secret_rejected`,
`attached`. Werte: `null`, Boolean, Ganzzahl von 0 bis 2^31, oder ein Token
`^[A-Za-z0-9][A-Za-z0-9._:-]{0,79}$`. Nachrichtentext ist keiner dieser Werte.
Der CHECK auf `events` steht in [DATENBANK.md](DATENBANK.md).

## `load_memory_context`

Lesend, sendet nichts. Signatur: `thread_id` (darf leer sein), `target`,
`data_class`, optionales `query`, `budget_tokens`. Ziele in `TARGETS`:
`ollama`, `claude`, `claude_code`, `grok`, `codex`. Unbekannt:
`unknown_target`. Ungueltige Klasse: `invalid_data_class`.

`target_classes`:

| Ziel | Klassen |
| --- | --- |
| `ollama` | `local_only`, `cloud_redacted`, `cloud_ok` |
| `claude`, `claude_code`, `grok` | nur `cloud_ok` |
| `codex` | keine |

`cloud_redacted` ist fuer die drei Cloud-Ziele ausgeschlossen: die Redaktion
in `app/redaction/` haengt an einem Auftrag und seinen `redaction_maps`.
Gespraechsgedaechtnis hat keinen Auftrag.

`context_classes` schneidet `allowed_classes(data_class)` mit dieser Decke.
`local_only` am Cloud-Ziel wirft `data_class_block`, bevor gelesen wird.

| Ziel | `data_class` des Aufrufs | Ergebnis |
| --- | --- | --- |
| `ollama` | `local_only` | alle drei |
| `ollama` | `cloud_redacted` | `cloud_redacted`, `cloud_ok` |
| `ollama` | `cloud_ok` | `cloud_ok` |
| `claude`, `claude_code`, `grok` | `cloud_redacted` oder `cloud_ok` | nur `cloud_ok` |
| `claude`, `claude_code`, `grok` | `local_only` | Fehler `data_class_block` |
| `codex` | jede gueltige Klasse | leeres Tupel, kein Datenbankzugriff |

Leere Klassenschnittmenge: Rueckgabe ohne Lesen (`codex`).

Ist die Menge nicht leer, in dieser Reihenfolge, der Klassenfilter in SQL vor
Rang und Text:

1. **Profil**, nur wenn `local_only` in der Menge liegt. `current_profile`,
   Notizen getrimmt, Klasse im Ergebnis `local_only`, Referenz
   `profile:{version}`, `evidence_status` ist `owner-typed` (kein Nachweis-Enum).
   Cloud-Ziele sehen das Profil nicht.
2. **Verlauf**, nur bei gesetzter `thread_id`, Thema vorhanden, `status = active`
   und Themenklasse in der Menge. Nachrichten mit `state = approved` und
   Klasse in der Menge, `seq` absteigend, hoechstens 200.
3. **Fakten**, nur wenn `thread_id` gesetzt ist oder die Anfrage Stichworte hat.
   `status = approved`, `superseded_by` leer, Klasse in der Menge,
   `valid_to` leer oder nach `clock_timestamp()`. `valid_from` filtert nicht.
   Quellnachricht `state <> rejected` (eine noch `quarantined` Quellzeile
   schliesst den Fakt nicht aus). Hoechstens 500 Kandidaten, neueste zuerst.
   Behalten werden Fakten im selben Thema oder mit Stichworttreffer. Rang:
   Thema zuerst, dann mehr Treffer, dann die urspruengliche Reihenfolge.
   Stichworte sind `\w` ab drei Zeichen, klein, als Teilstring in Satz und
   Tripel. Text im Ergebnis:
   `{subject} | {predicate} | {object} ({original_sentence})`.
   Keine Embeddings.

Budget standardmaessig `(4096 - 400 - 64) // 2` = 1816. Das ist die Haelfte
des Dialog-Rests (`num_ctx` 4096, `num_predict` 400, Reserve 64). Die Funktion
aendert `num_ctx` nicht und liest die Dialog-Umgebung nicht.
`budget_tokens < 1` ergibt `invalid_budget`. Schaetzung wie
`estimate_dialogue_tokens`: mindestens 1, aufgerundet `Laenge / 3.5`.

Jedes `MemoryItem` traegt `evidence_status`: beim Verlauf und bei Fakten der
Wert der Zeile (`bewiesen`, `unbewiesen`, `abgelehnt`), beim Profil
`owner-typed`. Ein Aufrufer kann `unbewiesen` damit beschriften.

Zuerst das Profil, dann der Verlauf, dann die Fakten. Ein Verlaufseintrag, der
nicht passt, beendet den Verlauf; aeltere Eintraege fallen mit. Geprueft wird
ein Geheimnis im Text, dann fuer jede Klasse ausser `local_only` dieselbe
Datenschutz-Sperre wie bei der Annahme (Blockliste; fehlende Konfiguration
sperrt), dann das Budget. Fakten werden danach einzeln weitergeprueft: ein zu
grosser, ein gesperrter oder ein Geheimnis-Fakt faellt, ein spaeterer kleinerer
kann bleiben. Verworfene Eintraege zaehlen in `dropped` und kommen nicht in
`items`. Der Verlauf im Ergebnis steht chronologisch. `tokens` ist die Summe
der behaltenen Schaetzungen.

## Altbestand

`backfill_assistant_turns` laeuft nur auf Aufruf. Sie kopiert
`assistant_turns` nach `assistant_messages` im Sammel-Thread
`5a3c1d0e-6b7f-4e21-9c4d-0000000c0064`, Titel `Sammel-Thread (Altbestand)`,
`title_origin = assistant_proposed`, Klasse `local_only`, `status = proposed`.
Jede Zeile ist `quarantined`, Kanal `deck_ui`, Konto `owner`, Methode
`deck_ui`, `external_thread_id = backfill`. Die Quellen-ID ist
`assistant_turn:{id}:owner` oder `assistant_turn:{id}:reply`, `source_ref`
`backfill:assistant_turns/{id}/owner` oder `.../reply`. Ist der Owner-Text ein
Geheimnis, entfaellt diese Zeile; die Antwort wird dann ohne
`reply_for_message_id` gespeichert, sofern sie selbst kein Geheimnis ist.
Ein zweiter Lauf trifft den Unique-Schluessel und fuegt nichts ein.
`assistant_turns` bleibt.
`secret_rejected` im Bericht zaehlt Geheimnisse und zusaetzlich eine
`data_class` ausserhalb der drei Werte. Audit
`assistant.backfill_completed` nur, wenn etwas eingefuegt wurde oder der
Sammel-Thread neu ist.

## Nicht in diesem Stand

- **M1b, offen.** «Dialog nutzt Thread» ist nicht erledigt. Der Live-Dialog
  liest weiter die letzten Saetze aus `assistant_turns`. M1b haengt den Dialog
  an den Themen-Thread, nach dem Merge des UI-PR und vor G2.
  `app/assistant/http.py` bleibt in M1 unveraendert und ruft weder
  `ingest_turn` noch `load_memory_context`.
- **M2, Oberflaeche.** Keine Quarantaene-Inbox und keine Freigabe-Klicks.
  Deck-Antworten bleiben `quarantined`, bis M2 entscheidet, ob `persist` sie
  mit abdeckt oder ein zweiter Klick gilt. Der Plan-Code `unknown_source`
  erscheint in der UI als `unknown_account`, `account_inactive` oder
  `invalid_envelope`. Weitere M1-Codes fuer dieselben Labels:
  `privacy_block`, `thread_class_block`, `duplicate_body_mismatch`,
  `secret_rejected`, `data_class_block`, `invalid_method`,
  `reply_path_required`, `reply_channel`, `reply_target_rejected`,
  `invalid_reply`, `source_rejected`. Die Audit-Auswahl blendet taskfreie
  Zeilen aus; M2 braucht einen Leser fuer `assistant.*` und `memory.*`.
  Der Verlauf sortiert nach `seq`, nicht nach `occurred_at`.
- **M3, Adapter n8n und Kanal `grok`.** Kein Eingang, kein HMAC, kein Versand.
- **M4, Import-Parser.** `import_file` ist ein Wert im Umschlag. Es gibt keinen
  Parser, der eine Exportdatei in `channel_turn.v1` schreibt. Konten legt der
  Owner an. Der Seed ist nur `deck_ui/owner`. Die vier Worker-Konten
  (zwei Grok-, zwei Claude-Konten) entstehen inaktiv als Standard. `memory_approvals.kind` kennt
  keinen Import-Stapel; M4 braucht ihn fuer den Manifest-Hash. `claude_code`
  bleibt per CHECK `local_only`, bis ein Umstufungsweg existiert.
- **M5, Slack Socket Mode.** Kein Socket und kein Slack-Adapter. Der Vertrag
  dafuer und der Rueckweg stehen nur in ADR 0010.
- **`cloud_redacted` an Cloud-Ziele.** Der Lader liefert an `claude`,
  `claude_code` und `grok` nur `cloud_ok`. Das ist strenger als der Plan, weil
  die Redaktion an einem Auftrag haengt. Der Owner bestaetigt diese Grenze,
  oder ein spaeteres Ticket baut eine auftragsfreie Redaktion. Der Code bleibt
  bei `cloud_ok`.
