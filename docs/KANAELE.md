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
| `external_thread_id` | 1 bis 200, `^[A-Za-z0-9][A-Za-z0-9._:/#+-]{0,199}$` |
| `external_message_id` | `null` oder String wie `external_thread_id` |
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
`external_thread_id`.

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

1. **Umschlag.** `parse_turn`. Fehler `invalid_envelope`, Audit
   `assistant.turn_rejected`. Im Detail nur, wenn der Rohwert wohlgeformt ist:
   `channel` (nur ein bekannter Kanal), `account` (nur das Kontomuster) und
   `text_sha256` (sha256 der UTF-8-Bytes, wenn `text` ein String ist). Kein Text.
2. **Allowlist.** Zeile `(channel, account)` mit Lesesperre. Fehlt sie:
   `unknown_account`. Ist `active` falsch: `account_inactive`.
3. **Klasse.** `claude_code` wird auf `local_only` gezwungen; das Audit setzt
   dann `class_forced`. Liegt die Klasse nicht in der Kanaldecke oder nicht in
   `allowed_data_classes` des Kontos: `data_class_block`. Noch nichts gespeichert.
4. **Methode.** Verstoss wie oben: `invalid_method`.
5. **Antwortpfad.** Rolle `assistant` auf `deck_ui` oder `slack`:
   `reply_path_required`. Speichern nur ueber `record_deck_reply`. Auf
   `n8n`, `grok`, `claude` und `claude_code` darf die Rolle `assistant` durch
   `ingest_turn`.
6. **Geheimnis-Scan.** `scan_text` mit Quelle `channel_turn`. Nur
   `FindingKind.SECRET` zaehlt, nicht die anderen Befundarten. Geprueft werden
   `text`, `external_thread_id`, `external_message_id`, `in_reply_to` und
   `source_ref.ref`. Treffer: `secret_rejected`. Der Text wird nirgends
   gespeichert. Das Audit traegt `reason` plus die bis hier gesetzten Felder
   (`channel`, `account`, `role`, `method`, `text_sha256`, `data_class`,
   gegebenenfalls `class_forced`).
7. **Thema lesen.** Eine Verknuepfung mit `state <> rejected` wird geladen.
   `thread_id` wird nur gesetzt, wenn `state = active`, das Thema existiert,
   `status = active` hat und `class_admits` die Nachrichtenklasse unter der
   Themenklasse zulaesst. Sonst bleibt `thread_id` leer. Eine Verknuepfung mit
   `proposed` mappt nicht.
8. **`in_reply_to`.** Aufloesung ueber `(channel, account, external_message_id)`.
   Fehlt das Ziel, setzt das Audit `in_reply_to_unresolved`. Die Nachricht wird
   trotzdem angenommen; `in_reply_to_message_id` bleibt dann leer.
9. **Dedup und Insert.** `INSERT ... ON CONFLICT DO NOTHING`. Mit
   Quellen-ID gilt `UNIQUE (source_channel, source_account, external_message_id)`.
   Ohne Quellen-ID gilt der partielle Unique-Index auf
   `(source_channel, source_account, external_thread_id, role, text_sha256, source_date)`
   (`source_date` ist der geparste `occurred_at`). `NULL` in
   `external_message_id` kollidiert auf dem ersten Schluessel nicht mit einem
   anderen `NULL`. Konflikt: `status = duplicate`, `reason = duplicate`, Audit
   `assistant.turn_duplicate`, kein Zustandswechsel. `message_id` ist die
   vorhandene Zeile, wenn die Suche sie findet.
10. **Vorschlag, nur nach einem neuen Insert ohne `thread_id`.** Das Audit setzt
    `thread_unmapped`. `_propose_link` legt ein Thema `proposed`
    (`title_origin = assistant_proposed`, Klasse der Nachricht, Titel
    `{kanal}/{konto}: ` plus hoechstens 150 Zeichen der Thread-Kennung) und eine
    Verknuepfung `proposed` an. Eine schon offene Verknuepfung
    (`state <> rejected`) wird wiederverwendet, auch wenn sie `active` ist und
    die Klasse nicht passt. Zwei gleichzeitige Vorschlaege behalten eine
    Verknuepfung (Savepoint, der verlorene Themen-Insert faellt zurueck).
    Ergebnis `accepted` mit `reason = thread_unmapped`. Mit gesetztem
    `thread_id`: `accepted`, `reason` leer. Audit `assistant.turn_accepted`
    mit `message_id` und, wenn vorhanden, `thread_id` und `link_id`.

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
| `secret_rejected` | `rejected` | `assistant.turn_rejected` |
| `duplicate` | `duplicate` | `assistant.turn_duplicate` |
| `thread_unmapped` | `accepted` | `assistant.turn_accepted` |
| leer | `accepted` | `assistant.turn_accepted` |

### `record_deck_reply`

Speichert die Antwort des Decks auf eine vorhandene Nachricht. Sie sendet
nichts. Die Zeile ist `quarantined`, Rolle `assistant`, Klasse und Thema der
eingehenden Zeile, `reply_for_message_id` und `in_reply_to_message_id` zeigen
auf diese Zeile. `external_message_id` ist `deck-reply:` plus die Id.
`source_ref` ist `{kanal}:reply/{id}`. `source_date` ist `clock_timestamp()`.
Methode `deck_ui` auf `deck_ui`, sonst `live_push`. `evidence_status` ist
`unbewiesen`.

`UNIQUE (reply_for_message_id)` laesst eine zweite Antwort zum Duplikat werden.

| Ausgang | Bedingung |
| --- | --- |
| wirft `reply_target_missing` | Ziel-Id existiert nicht. Keine Audit-Zeile |
| `reply_channel` | Kanal nicht in `deck_ui`/`slack` oder Rolle nicht `owner`. Audit `assistant.reply_rejected` |
| `reply_target_rejected` | Ziel hat `state = rejected`. Audit `assistant.reply_rejected` |
| `invalid_reply` | Text kein String, nach `strip` leer, oder laenger als 16000 Zeichen. Audit `assistant.reply_rejected` |
| `secret_rejected` | Geheimnis im Antworttext. Nichts gespeichert. Audit `assistant.reply_rejected` |
| `duplicate` | zweite Antwort auf dieselbe Id. Audit `assistant.reply_duplicate` |
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

- `session_token_hash`: 64 Kleinbuchstaben-Hexzeichen. Die Zeile in
  `ui_sessions` muss `authenticated` sein, `revoked_at` leer haben und
  `expires_at` nach `clock_timestamp()` liegen. Die Zeile wird lesend gesperrt.
  Sonst `owner_session_required`. Der Hash ist kein Fremdschluessel.
- `shown_sha256`: sha256-Hex (klein) der Bytes, die der Owner gesehen hat.
  Abweichung oder anderes Format: `shown_bytes_mismatch`. Vergleich mit
  `hmac.compare_digest`.

Die Bytes:

| Ziel | Bytes (UTF-8) |
| --- | --- |
| Nachricht | `text` der Zeile, unveraendert |
| Faktum | `original_sentence`, `subject`, `predicate`, `object`, mit `\n` verbunden, ohne weiteres Zeichen am Ende |
| Thema | `title` der Zeile |
| Verknuepfung | `channel`, `account`, `external_thread_id`, Id des gewaehlten Themas, `title` dieses Themas, mit `\n` verbunden |

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
  Quellnachricht `rejected`: `source_rejected`. Geheimnis in Satz oder Tripel:
  Audit `memory.fact_rejected_input` mit `secret_rejected` und Hash, dann
  Fehler `secret_rejected`, nichts gespeichert.
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
`text_sha256`, `shown_sha256`, `class_forced`, `thread_unmapped`,
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
   `profile:{version}`. Cloud-Ziele sehen das Profil nicht.
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

Zuerst das Profil, dann der Verlauf, dann die Fakten. Ein Verlaufseintrag, der
nicht passt (Geheimnis oder Budget), beendet den Verlauf; aeltere Eintraege
fallen mit. Fakten werden danach einzeln weitergeprueft: ein zu grosser oder
ein Geheimnis-Fakt faellt, ein spaeterer kleinerer kann bleiben. Geheimnisse
zaehlen in `dropped` und kommen nicht in `items`. Der Verlauf im Ergebnis steht
chronologisch. `tokens` ist die Summe der behaltenen Schaetzungen.

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

- **M2, Oberflaeche.** Keine Quarantaene-Inbox und keine Freigabe-Klicks.
  `load_memory_context` ist nicht an den Dialog gehaengt.
  `app/assistant/http.py` ruft weder `ingest_turn` noch `load_memory_context`.
- **M3, Adapter n8n und Kanal `grok`.** Kein Eingang, kein HMAC, kein Versand.
- **M4, Import-Parser.** `import_file` ist ein Wert im Umschlag. Es gibt keinen
  Parser, der eine Exportdatei in `channel_turn.v1` schreibt. Die bestehende
  Aufgaben-Inbox fuer Anhaenge ist ein anderer Pfad.
- **M5, Slack Socket Mode.** Kein Socket und kein Slack-Adapter. Der Vertrag
  dafuer und der Rueckweg stehen nur in ADR 0010.
