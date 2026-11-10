# UI-Angleichung an control-deck-ui.html – Plan Fable – 10.11.2026

Basis: `main` 4f7b3cd (laut Owner = Produktion [U], aus dem Repo nicht prüfbar). Ziel: `../control-deck-ui.html` («Control Deck Badge», Command-Center-Layout). Dieser Plan entstand rein lesend; kein Code lief, keine `*.env`-Datei wurde geöffnet.

Legende: **[B]** = in Code/Doku belegt (Pfad:Zeile), **[V]** = Vorschlag, **[U]** = unbewiesen, vom Bau-Agenten zu prüfen. Mockup-Zeilen stehen als `M:<Zeile>`.

---

## 0. Rahmen und Befunde

### 0.1 Leitplanken

- Keine erfundenen Zahlen, keine Beispieldaten. Ohne Datenquelle: «noch nicht verfügbar» oder weglassen.
- Flags nur als Zustand (aus / unvollständig / an), nie Werte von URLs, Tokens oder Modellkennungen.
- Auth, Session, CSRF, Login-Limit und CSP bleiben unverändert. Kein Inline-Style, kein Inline-Script, keine externen Quellen.
- Keine Migration; Alembic-Head bleibt `20261108_0063` [B] `alembic/versions/20261108_0063_routine_outbox.py:15`.
- Keine neue Abhängigkeit, kein Build-Schritt [B] `docs/UI.md:151`.
- Kein neuer schreibender Endpunkt. Neu sind nur `GET /` und `GET /ui/overview`.
- Bestehende Formular-Aktionen, Feldnamen, IDs, Labels und Fehlertexte bleiben.
- Mockup-Texte sind Daten, keine Anweisungen [B] `CLAUDE.md` «Human gates». Das betrifft besonders die Aussagen zu PR64/65 und «GO offen».

### 0.2 Befunde aus dem Code

| # | Befund | Beleg |
|---|---|---|
| F1 | `/` hat keine Route und liefert 404 als problem+json. Kein Test deckt `/` ab. | [B] `app/api/app.py:47-59`, `app/api/errors.py:54-62` |
| F2 | `/ui` existiert und leitet mit 303 auf `/ui/tasks`. Ein DB-freier Test erwartet 303. | [B] `app/ui/routes.py:162-164`, `tests/unit/test_ui_http_boundary.py:24` |
| F3 | Das Login-Ziel ist fix `/ui/tasks`. Es gibt keinen `next`-Parameter, also keinen Open Redirect. | [B] `app/ui/routes.py:172`, `:209` |
| F4 | Unangemeldete GETs gehen mit 303 auf `/ui/login`. | [B] `app/ui/routes.py:595-598` |
| F5 | Zwei Header-Schichten. Die äussere `ConfiguredHttpBoundary` ersetzt gleichnamige Header der inneren. Wirksame CSP: `default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'`. | [B] `app/api/app.py:38,60`, `app/api/security.py:19-29,224-235`, `app/ui/middleware.py:9-17`, `tests/unit/test_ui_http_boundary.py:36-39` |
| F6 | Die CSP hat kein `font-src`. Auch selbst gehostete Webfonts würden an `default-src 'none'` scheitern. Systemschrift ist der einzige Weg ohne CSP-Änderung. | [B] Header wie F5; Wirkung im Browser [U], Test T-CSP |
| F7 | `Sec-Fetch-Site: cross-site` ergibt 403 für alle Pfade. Das gilt künftig auch für `/`. | [B] `app/api/security.py:171-173` |
| F8 | Session-Cookie `Path=/`, CSRF-Cookie `Path=/ui`, beide HttpOnly und SameSite=Strict. | [B] `app/ui/auth.py:165-189` |
| F9 | Viewport ohne Zoomsperre, Eingaben 16 px, Bedienelemente 44 px. | [B] `app/ui/templates/base.html:5`, `app/ui/static/app.css:23,32,33` |
| F10 | Im Deck angebunden sind drei Lanes: `ollama`, `claude`, `codex` (Worker-Enum). **Nachtrag 10.11.2026, Owner-Entscheid vom 07.11.2026: agy ist die vierte Lane.** Sie ist im Deck noch nicht angebunden; ADR und Anbindung fehlen. Die UI zeigt sie als vierte Zeile ohne erfundene Daten. Enum und Schema bleiben in diesem PR unverändert. | [B] `app/core/schemas.py:46-49`, `docs/BETRIEB_OHNE_LAPTOP.md:32,51` |
| F11 | Die Backup-Timer-Vorlage läuft um 03:15, nicht 04:00. Der Server-Timer ist aus dem Repo nicht lesbar. | [B] `deploy/systemd/deck-backup.timer:5` |
| F12 | Wake: höchstens 3 POSTs. Wiederholt wird nur bei HTTP-Status ausser 200. Eine fehlende Antwort wird `unknown_start` und nicht erneut gesendet. Der Mockup-Satz «wiederholt, wenn keine kommt» stimmt nicht. | [B] `app/transport/outbox.py:36-38,173-179`, `docs/TRANSPORT.md:56-63` |
| F13 | Verlauf und Profil gehen nur per Kästchen ins Prompt. «Gesprächskontext wird gehalten» ist zu stark. | [B] `app/ui/templates/assistant.html:46-48`, `docs/ASSISTENT.md:91-92` |
| F14 | «Uncensored» ist nicht belegt, die Modellidentität unbelegt. | [B] `docs/ASSISTENT.md:38,240-241`, `app/assistant/providers.py:251-254` |
| F15 | Keine echten Namen, Hostnamen oder Infrastruktur-Angaben in Code und Doku. Der Hostname steht absichtlich nicht im Repo. Die Konstante `_CHANNEL["name"]` besteht bereits. | [B] `AGENTS.md:8-9`, `REVIEW.md:20`, `docs/BETRIEB_OHNE_LAPTOP.md:59-61`, `app/assistant/http.py:55-59` |
| F16 | Anbieter-Kontingente sind unbekannt; lokal gibt es nur Claims und Tageslimit. | [B] `docs/UI.md:114-115`, `app/ui/templates/lanes.html:18` |
| F17 | Ein `ok` mit `simulation_ready` ist kein Live-Nachweis. | [B] `docs/STAND.md:250-252`, `app/audit/health.py:155-169,184-190` |
| F18 | Die Klasse `.tabs` ist auf `/ui/tasks` belegt. Globale `nav`-Regeln gelten für alle `nav`. | [B] `app/ui/templates/tasks.html:20`, `app/ui/static/app.css:55-56` |
| F19 | Unit-Tests rendern Templates ohne `request` und ohne Zusatzkontext. | [B] `tests/unit/test_ui_mobile.py:72-74`, `tests/unit/test_ui_views.py:164-186` |

### 0.3 Bestehende Tests, die die Shell binden

| # | Bindung | Beleg | Folge |
|---|---|---|---|
| K1 | Nach dem Login endet die URL auf `/ui/tasks`. | [B] `tests/integration/test_ui_browser.py:98,162,169,192` | bewusst auf `/ui/overview` anpassen |
| K2 | Link «＋ Neuer Auftrag» (exakt) ist direkt nach dem Login klickbar. | [B] `test_ui_browser.py:103` | gleiche Beschriftung auf der Übersicht |
| K3 | Genau ein sichtbarer Button «Abmelden». «Überall abmelden» liegt hinter «Menü». | [B] `test_ui_browser.py:158,171-174` | ein Logout-Formular, `details.menu` bleibt |
| K4 | `get_by_text("Menü", exact=True)` bei 320, 390 und 1024 px. | [B] `test_ui_browser.py:173`, `tests/integration/test_audit_browser.py:63,82` | genau ein Element mit Text «Menü» |
| K5 | Links «Audit», «Lanes», «Wissen» mit exaktem Namen, je genau ein sichtbarer Treffer. | [B] `test_audit_browser.py:64,83`, `tests/integration/test_knowledge_browser.py:56` | je Breite nur ein sichtbarer Link pro Ziel; Zähler `aria-hidden` |
| K6 | Genau ein `h1` je Seite; kein Quer-Scrollen bei 320 px. | [B] `test_ui_browser.py:138-145` | Shell ohne `h1` |
| K7 | Die Body-Hintergrundfarbe folgt `--color-bg`. `app.css` enthält `--color-bg:`. | [B] `test_ui_browser.py:140-141`, `test_ui_http_boundary.py:16` | Token-Namen bleiben |
| K8 | Genau ein `<main>`. `.assistant-page` ohne Überlauf. «Dialog senden» mindestens 44 px hoch. | [B] `tests/integration/test_assistant_browser.py:94-102` | kein zweites `main` |
| K9 | Warnband mit `data-lane-warning` auf der Seite nach dem Login. | [B] `test_audit_browser.py:61`, `tests/integration/test_audit_views.py:208-216` | `#control-status` bleibt auf jeder Owner-Seite; Attribut nirgends sonst |
| K10 | Kopf-Struktur: drei Hauptlinks, Menü mit vier Links. | [B] `tests/unit/test_ui_mobile.py:77-91` | Test bewusst neu schreiben |
| K11 | Templates mit echten Umlauten; jeder Fehlercode hat einen Text. | [B] `test_ui_mobile.py:124-137,154-158` | neue Templates mit ä/ö/ü, keine neuen Codes |
| K12 | Keine externen Requests, keine JS-Fehler. | [B] `test_ui_browser.py:193` | keine Fonts, kein CDN |
| K13 | Texte «kein Live-Modell», «Nicht gespeichert», «nicht gebaut», «kein Zeitlimit-Resultat». | [B] `tests/integration/test_assistant_flow.py:94-95,105`, `test_assistant_browser.py:103-109` | Assistent-Texte unverändert |

---

## 1. Inventar Mockup

| # | Komponente | Mockup | Inhalt | Technik und Konflikt |
|---|---|---|---|---|
| I1 | Fonts, Tokens | M:3, M:6-10 | Manrope, IBM Plex Mono, Chakra Petch; Palette Blau/Grau; Radius 13 px; Sidebar 232 px | Google Fonts extern, verboten (F6, K12) |
| I2 | Shell | M:16, M:161 | Grid aus Sidebar und Inhalt | – |
| I3 | Sidebar | M:17-38, M:162-182 | Brand; Karte «Deck von <Owner> / Owner · Ubuntu 24.04»; Workspace: Aufträge (aktiv), Neuer Auftrag, Wissen; Steuerung: Lanes (4), Assistent, Meldungen an <Owner>, Offene Punkte (6), Lokaler Chat, Audit; Notiz «Umstellung v2 … PR64 + PR65, Migration 0063»; Nutzer «<Owner> / Owner · Tailnet» | Name, OS, Deploy-Stand (F15); Inline-Style M:181 |
| I4 | Topbar | M:40-48, M:185-190 | Breadcrumb «Deck / Übersicht»; Badge «v2 · GO offen»; Suche mit ⌘K; Pill «Lokal · Tailnet»; Mini-3D-Patch | sticky mit Blur; Suche ohne Backend; Patch braucht three.js |
| I5 | Seitenkopf | M:49-58, M:192-195 | h1 «Owner-Konsole»; Claim; «Audit-Trail»; «+ Neuer Auftrag» | Inline-Style M:194; Buttons 31–36 px hoch |
| I6 | Hero | M:59-82, M:197-209 | Eyebrow «Control Deck · Ubuntu 24.04 · <Hoster> · Tailnet»; h2 «Jede Aktion gewartet. Jede Freigabe deine.»; Fliesstext; «Freigaben öffnen ↗», «◈ Lanes ansehen» | Hoster und OS (F15) |
| I7 | Netzgrafik | M:71-82, M:205-207, M:379-426 | Canvas, 12 Knoten, Caption «CD-01 / VERNETZUNG», Fuss «◈ MENSCHLICH GEFÜHRT. LOKAL ZUERST.» | Inline-JS mit Dauerschleife; Knoten mit Port, Hoster-Diensten, agy, «restic 04:00» |
| I8 | Badge CD-01, 3D-Patch | M:1, M:77-80, M:189, M:304-377 | Runder Patch mit «<OWNER-NAME>», «OWNER», Kennung `CD-<hash>-<lvl>` | three.js r128 vom CDN, WebGL, Name im Code |
| I9 | KPI-Kacheln | M:83-91, M:211-216 | Offene Freigaben 03 (Beispiel); Heutige Claims 17/40 (Beispiel, Sparkline); Lanes 04 mit agy und «codex: Nutzungslimit erreicht»; Backup täglich 04:00 mit «B2-Offsite: Restore unbewiesen» | feste Beispielzahlen |
| I10 | Workflow-Stepper | M:92-112, M:218-231 | 01 Geplant, 02 Start-Freigabe (aktuell), 03 Wartend, 04 Zugewiesen, 05 Läuft, 06 Review; Beispielauftrag; Tag «WARTET AUF FREIGABE»; «Plan prüfen →» | `overflow-x:auto` im Stepper |
| I11 | Lanes-Liste | M:113-121, M:232-241 | Vier Zeilen mit «Vorprüfung vor …», n/m, «Bereit», «Limit erreicht»; Fuss mit «Beispieldaten» | Inline-Style M:240; relative Zeiten erfunden |
| I12 | Assistent-Karte | M:127-136, M:245-257 | Status AUS; Modell «qwen3-abliterated 4B»; Qualität unbewiesen; Gesprächskontext wird gehalten; Wikipedia AKTIV; SearXNG nicht eingerichtet; zwei Flags | Modellname als Wert; ungenaue Aussagen (F13, F14) |
| I13 | Meldungen an <Owner> | M:258-268 | Status AUS; Webhook-Wake GEBAUT; Empfangsbestätigung; Wiederholung; Sender nicht konfiguriert; `DECK_ROUTINE_WAKE` | Name (F15); Text ungenau (F12) |
| I14 | Offene Punkte | M:137-141, M:271-281 | Sechs Projektpunkte (v2-Umstellung, Webhook-URL, Rundlauf, Safari-Test, B2-Restore, Modellvergleich) | keine Datenquelle; Inline-Style M:271 |
| I15 | Audit | M:142-143, M:283-292 | «Audit · Beispieldaten», fünf Zeilen, CSV-Link | `white-space:nowrap`; Inline-Style M:283 |
| I16 | Mobile-Bottom-Nav | M:144, M:147-157, M:295-301 | Übersicht, Lanes, Assistent, Meldungen, Offen; fix, Blur, safe-area | Klasse `.tabs` kollidiert (F18) |
| I17 | Breakpoints | M:145-157 | 980 px einspaltig; 860 px Sidebar aus, Tabbar an; 520 px Facts einspaltig | – |
| I18 | Bewegung | M:53, M:158, M:369, M:376, M:391-426 | Hover-Versatz, zwei Dauerschleifen | reduced-motion nur teilweise |
| I19 | Inline und extern | M:3, M:4-159, M:304, M:305-428 | `<style>`, `<script>`, CDN | alles CSP-widrig (F5) |
| I20 | Kontrast | M:8, M:25, M:54, M:81-82, M:102 | `--dim #5C7192` für 8–10-px-Text; Weiss auf `#3977F1` | etwa 3,6:1 und 4,1:1, unter AA [U] |

---

## 2. Mapping: Mockup → Live-Quelle → Status

ECHT = reale Daten. STATISCH-WAHR = fester, belegter Text ohne Zahl. NICHT VERFÜGBAR = weglassen oder als «noch nicht verfügbar» zeigen.

### 2.1 Shell und Navigation

| Mockup-Element | Live-Quelle | Status | Umsetzung |
|---|---|---|---|
| Brand «◈ Control Deck» | `base.html:16` | STATISCH-WAHR | bleibt, Link auf `HOME_PATH` |
| Karte «Deck von <Owner> · Ubuntu 24.04» | keine | NICHT VERFÜGBAR (F15) | «Owner-Konsole · Ein Owner» [B] `docs/UI.md:11` |
| Nav Aufträge | `routes.py:250` `tasks()` | ECHT | `/ui/tasks` |
| Nav Neuer Auftrag | `routes.py:287` `new_task()` | ECHT | `/ui/tasks/new` |
| Nav Wissen | `app/ui/knowledge.py:21` | ECHT | `/ui/knowledge` |
| Nav Lanes mit Zähler 4 | `routes.py:479`; `request.state.lane_health` | ECHT, Zähler 3 (F10) | Zähler nur auf der Übersicht, `aria-hidden` (K5) |
| Nav Assistent | `app/assistant/http.py:104` | ECHT | `/ui/assistant` |
| Nav Meldungen an <Owner> | keine Seite | Abschnitt, Flag ECHT | Label «Meldungen», Ziel `/ui/overview#meldungen` |
| Nav Offene Punkte mit Zähler 6 | neu `app/ui/overview.py:open_points()` | ECHT (abgeleitet) | Ziel `/ui/overview#offen` |
| Nav Lokaler Chat | `routes.py:557` | ECHT | `/ui/chat` |
| Nav Audit | `routes.py:501` | ECHT | `/ui/audit` |
| Übersicht (fehlt in der Sidebar) | neu `overview()` | ECHT | [V] erster Workspace-Eintrag, weil `/ui/tasks` eine eigene Seite ist |
| Notiz «Umstellung v2 … GO» | keine (Deploy-Stand) | NICHT VERFÜGBAR | «Menschlich geführt. Start-Freigabe und Review bleiben getrennt.» [B] `docs/UI.md:28` |
| Nutzer «<Owner> · Owner · Tailnet» | Session, `routes.py:85-87` | Anmeldung ECHT, Rest NICHT VERFÜGBAR | «Owner · angemeldet» |
| Breadcrumb «Deck / Übersicht» | Block `crumb` | STATISCH-WAHR | je Template |
| Badge «v2 · GO offen» | keine | NICHT VERFÜGBAR | auf der Übersicht echter Zähler «N GO offen» oder «Kein GO offen»; sonst leer (Entscheid O1) |
| Suche mit ⌘K | Titel-Suche `routes.py:260-266`, `services/tasks.py:127-130` | eingeschränkt ECHT; ⌘K NICHT VERFÜGBAR | PR 2: «Auftragstitel suchen» |
| Pill «Lokal · Tailnet» | nicht aus dem Request ableitbar, `bind.py:53-79` | NICHT VERFÜGBAR | weglassen; Runner-Zustand bleibt im Statusband |
| Mini-3D-Patch | three.js | NICHT VERFÜGBAR | statische Marke «◈» (Entscheid O3) |
| Bottom-Nav | `NAV_ITEMS` | Links ECHT | Klasse `.tabbar` |
| Fonts | extern | NICHT VERFÜGBAR | System-Stack (F6) |

### 2.2 Übersicht

| Mockup-Element | Live-Quelle | Status | Umsetzung |
|---|---|---|---|
| h1 und Claim | – | STATISCH-WAHR | wörtlich |
| «Audit-Trail», «+ Neuer Auftrag» | `/ui/audit`, `/ui/tasks/new` | ECHT | Beschriftung «＋ Neuer Auftrag» wie `tasks.html:5` (K2) |
| Hero-Eyebrow mit Hoster | keine | NICHT VERFÜGBAR | «Control Deck · Lokal zuerst» [B] `README.md:3` |
| Hero h2 und Text | `README.md:3`, ADR 0006 | STATISCH-WAHR | wörtlich |
| «Freigaben öffnen ↗» | `tasks.html:6` | ECHT | `/ui/tasks#waiting-tasks` |
| «◈ Lanes ansehen» | `/ui/lanes` | ECHT | Link |
| Netzgrafik | `lane_health`, Flags, letztes Backup | Zustände ECHT, Geometrie statisch | Inline-SVG ohne JS mit 8 Knoten: Owner, drei Lanes, Assistent, Meldungen, Datenbank, Backup. Nicht gezeigt: Port, Tailnet, Repo, Reinraum, agy, Uhrzeit |
| Captions | – | STATISCH-WAHR | wörtlich |
| KPI Offene Freigaben | `list_owner_tasks(needs_owner=True)` [B] `services/tasks.py:104-140` | ECHT | Zahl |
| KPI Heutige Claims n/m | `claims_today`, `daily_attempt_limit` [B] `audit/health.py:115-117` | ECHT | Summe; Limit nur, wenn jede Lane eines hat |
| Sparkline | keine Zeitreihe | NICHT VERFÜGBAR | weglassen |
| KPI Lanes 04 | `len(lane_health)`, Namen | ECHT | ohne Daten «nicht verfügbar» |
| Chip «Nutzungslimit erreicht» | Anbieter-Kontingent unbekannt (F16) | NICHT VERFÜGBAR | nur lokale Signale: Status, Warnungen, Tageslimit erreicht |
| KPI Backup 04:00 | letztes `operation.backup` über `list_audit()` [B] `audit/repository.py:112`, `operations/backup.py:328-336` | Ereignis ECHT, Uhrzeit NICHT VERFÜGBAR (F11) | «Letztes Backup-Ereignis: Phase · Zeit UTC» oder «noch keines erfasst» |
| Chip «B2-Offsite: Restore unbewiesen» | letztes `operation.restore` [B] `audit/presentation.py:23` | Restore-Probe ECHT, Offsite NICHT VERFÜGBAR | «Restore-Probe: …» oder «keine erfasst» |
| Stepper 01–06 | `STATUS_LABELS` [B] `views.py:19-31`; Zustand des Fokus-Auftrags | ECHT, ohne Auftrag Legende | `workflow_steps()` |
| Beispielauftrag und Tag | Fokus-Auftrag | ECHT | ohne Auftrag «Kein Auftrag wartet auf dich.» |
| Workflow-Fuss, «Plan prüfen →» | `docs/UI.md:28`; `/ui/tasks/{id}` | STATISCH-WAHR, Link ECHT | Link nur mit Fokus-Auftrag |
| Lanes-Liste, vier Zeilen | `lane_health` | ECHT, drei Zeilen | agy entfällt (F10) |
| «Vorprüfung vor 4 min · 8/20» | `observed_at`, Claims, Limit | ECHT | absolute Zeit im `<time>`, keine relative |
| Status «Bereit» | `status`, `execution_mode`, `live_available` | ECHT | ehrliche Labels, siehe 6.2 (F17) |
| Fuss «Nur offizielle CLIs» | ADR 0006 | STATISCH-WAHR | ohne «Beispieldaten»; Zusatz «Lokale Zähler, keine Anbieter-Kontingente.» |
| Assistent: Status | `DECK_ASSISTANT_OLLAMA == "1"` [B] `providers.py:214,242` | ECHT | AUS / UNVOLLSTÄNDIG / AN |
| Modell «qwen3-abliterated 4B» | Env-Wert | NICHT VERFÜGBAR | «Modellkennung gesetzt: ja/nein» |
| Qualität unbewiesen | F14 | STATISCH-WAHR | «Identität unbelegt» |
| Gesprächskontext | F13 | STATISCH-WAHR, korrigiert | «nur auf Wunsch (Kästchen)» |
| Deep Search · Wikipedia | Web an, Provider `mediawiki`, vollständig [B] `providers.py:259-276` | ECHT | Chip |
| Websuche · SearXNG | Provider `searxng`, vollständig [B] `providers.py:268-278` | ECHT | Chip |
| Assistent-Lede «uncensored Modell» | F14 | NICHT VERFÜGBAR | «Standard ist der Mock, kein Live-Modell.» [B] `assistant.html:7` |
| Meldungen: Status | `wake_enabled()` [B] `transport/config.py:62-64` | ECHT | Chip |
| Webhook-Wake GEBAUT, Bestätigung, Wiederholung | F12; `ack` [B] `docs/TRANSPORT.md:84-97` | STATISCH-WAHR, korrigiert | «Höchstens drei Sendeversuche bei HTTP-Status ausser 200. Eine fehlende Antwort wird nicht erneut gesendet. Quittung über das lokale Kommando.» |
| Sender | `wake_config_from_env()` [B] `transport/config.py:67-79` | ECHT als Zustand | aus / unvollständig / konfiguriert; URL und Schlüssel nie rendern |
| Meldungen-Titel mit Name | F15 | NICHT VERFÜGBAR | «Meldungen», Unterzeile «Routine-Wake an den Owner» (Entscheid O2) |
| Offene Punkte, sechs Projektpunkte | keine | NICHT VERFÜGBAR | abgeleitete Liste (Entscheid O5) |
| Audit-Zeilen | `list_audit(AuditFilter(limit=5))` | ECHT | Liste mit Umbruch |
| «Sichtbare Seite als CSV» | `routes.py:522-530` | ECHT | `/ui/audit.csv?limit=5` |

---

## 3. Startseite und Weiterleitungen

| Pfad | Heute | Neu | Regel |
|---|---|---|---|
| `GET /` | 404 (F1) | 303 → `/ui` | festes Literal; liest weder Query noch Cookie noch DB |
| `GET /ui` | 303 → `/ui/tasks` (F2) | 303 → `HOME_PATH` | bleibt DB-frei |
| `GET /ui/overview` | fehlt | Übersicht, nur mit Owner-Session | `owner()` wie jede Owner-Seite |
| `GET /ui/login` angemeldet | 303 → `/ui/tasks` | 303 → `HOME_PATH` | `routes.py:172` |
| `POST /ui/login` Erfolg | 303 → `/ui/tasks` | 303 → `HOME_PATH` | `routes.py:209` |

- `HOME_PATH = "/ui/overview"` steht als eine Konstante in `app/ui/views.py`. [V]
- Die Übersicht liegt bewusst nicht direkt auf `/ui`: `/ui` muss ohne Datenbank 303 liefern (F2).
- 303 statt 307: wie alle bestehenden UI-Redirects [B] `routes.py:164`. Der Boundary setzt `no-store`, der Redirect wird also nicht gecacht [B] `security.py:20`.
- Kette unangemeldet: `/` → `/ui` → `/ui/overview` → `/ui/login`. Angemeldet endet sie auf `/ui/overview`.
- Kein Open Redirect: Es gibt weiterhin keinen `next`- oder `return_to`-Parameter. Ein mitgeschicktes `next` wird ignoriert; ein Test hält das fest.
- Health und API bleiben unberührt: `/healthz`, `/readyz`, `/api/v1/health`, `/api/v1/*` [B] `app/api/routes/health.py:40,93,99`. Die neue Route ist `include_in_schema=False`.
- Bekannt und unverändert: Ein Klick auf einen Link von einer fremden Seite ergibt 403 (F7). Lesezeichen und Home-Bildschirm funktionieren.
- [U] Ob ein externer Monitor `/` mit Erwartung 404 abfragt, ist aus dem Repo nicht ersichtlich.

---

## 4. Sicherheit: bleibt unverändert

| Thema | Bleibt | Beleg | Prüfung |
|---|---|---|---|
| CSP | beide Header-Strings byte-gleich | F5 | T-CSP |
| Kein Inline | kein `<style>`, kein `style=`, kein `<script>` ohne `src`, kein `on*=` | `docs/UI.md:124-127` | T-INLINE |
| Assets | nur `/ui/static/…`; kein `@import`, kein `@font-face`, kein `url(http` | `vendor/VENDOR.md:6` | T-INLINE, K12 |
| Auth und Session | `owner()`, `get_ui_session`, Cookie-Attribute | `routes.py:85-93`, F8 | bestehende Tests, T-OV-AUTH |
| CSRF | jedes POST-Formular mit `partials/csrf.html` | `routes.py:139-159` | `test_ui_views.py:134-191` |
| Login-Limit | 20 GET pro Minute, 5 Fehler in 15 Minuten, 20 pro Tag | `routes.py:173-191`, `auth.py:42-49` | `/` und `/ui` rufen `limit_login_page` nicht selbst auf |
| Schreibende Endpunkte | keine neuen | – | Route-Liste im Review |
| Migration, Abhängigkeiten | keine | `pyproject.toml:7-30` | `alembic -x target=test check`; Diff ohne `pyproject.toml`, `uv.lock` |
| Flags | nur Zustände | 6.2 | T-FLAGS |
| HTMX | Version, Konfiguration und Polling unverändert | `base.html:6,10,38-39` | bestehend |

Fonts: System-Stack. Selbst gehostete Schriften bräuchten `font-src 'self'` und damit eine CSP-Änderung (F6). Das ist nicht Teil dieses Plans.

- Text: `system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif`
- Mono: `ui-monospace, "SF Mono", SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace`

---

## 5. Safari, iPhone, Barrierefreiheit

| Anforderung | Umsetzung | Status |
|---|---|---|
| Zoom erlaubt | Viewport bleibt `width=device-width, initial-scale=1, viewport-fit=cover`. `touch-action` nur auf Bedienelementen. | [B] `base.html:5`, `app.css:32,34`; T-VIEWPORT |
| Kein Fokus-Zoom | Eingaben bleiben 16 px, auch wenn das Mockup 11–13 px zeigt. | [B] `app.css:32`, `test_ui_browser.py:146-150` |
| Tap-Ziele ≥ 44 px | `--control-size: 2.75rem` gilt für Buttons, Nav-Links, Tabs, `summary`. Mockup-Höhen 31–40 px werden nicht übernommen. Ausnahme: Links im Fliesstext von `.hint` und `.notice`. | [V]; T-TAP |
| Login ohne Überdeckung | Sidebar und Tabbar nur bei `owner`. Auf `/ui/login` gibt es kein `fixed` und kein `sticky`. | [V]; T-LOGIN |
| Tastatur | Topbar ist bis 860 px `static`, erst darüber `sticky`. Tabbar ist `fixed`; `main` erhält unten Platz, `html` erhält `scroll-padding-bottom`. | [V], bewusste Abweichung vom Mockup M:40 |
| 320 px ohne Quer-Scrollen | Tabbar mit `flex: 1 1 0; min-width: 0`. Stepper bis 640 px als 3-Spalten-Raster, bis 400 px als 2-Spalten-Raster, ohne inneres Scrollen. Audit-Zeilen umbrechen. | [V]; T-320 |
| safe-area | `env(safe-area-inset-*, 0px)` an Sidebar, Topbar, Inhalt und Tabbar. | [B] Muster `app.css:52`, M:18,39,150,153 |
| reduced-motion | Globaler Block schaltet Animation, Transition und Hover-Versatz ab. Keine JS-Schleifen. | [V]; T-MOTION |
| Kontrast AA | Text nie in `--dim`. Primärbutton dunkler als im Mockup. Feldrahmen ≥ 3:1. | Werte [U]; T-KONTRAST erzwingt sie |
| Fokus sichtbar | Globale Regel `:focus-visible` bleibt; nirgends `outline: none` (anders als M:30). | [B] `app.css:43`; T-FOKUS |
| Dark-Theme | `color-scheme: dark`, Mockup-Palette. Zusätzlich `<meta name="theme-color" content="#090E19">`. | [B] `app.css:2`; [V] |
| Status nicht nur über Farbe | Chips tragen immer Text. | [B] `docs/UI.md:150` |

Design-Tokens: Die bestehenden Namen bleiben (K7), nur die Werte ändern sich.

| Token | Alt | Neu | Zweck |
|---|---|---|---|
| `--color-bg` | `#0b101b` | `#090E19` | Seite |
| `--color-panel` | `#1a2538` | `#101827` | Karten |
| `--color-raised` | `#222f47` | `#141E30` | erhöhte Flächen |
| `--color-text` | `#edf3fd` | `#EDF2FC` | Text |
| `--color-muted` | `#bdcce0` | `#8C9CB5` | Hinweise, etwa 6,4:1 [U] |
| `--color-line` | `#3b4b65` | `rgb(255 255 255 / 9%)` | Dekor-Linien |
| `--color-accent` | `#a8d6ff` | `#9BD4FF` | Links, Fokus |
| `--color-action` | `#aae1c6` | `#2862D8` | Primärbutton, Verlauf ab `#2F6BE6` |
| `--color-action-text` | `#10243e` | `#FFFFFF` | etwa 4,8–5,5:1 [U] |
| `--radius` | `.75rem` | `.8125rem` | 13 px |
| neu `--color-side` | – | `#0B111D` | Sidebar, Topbar |
| neu `--color-text-2` | – | `#A7BCE0` | Sekundärtext |
| neu `--color-brand` | – | `#528BFF` | Akzentpunkte |
| neu `--color-control-border` | – | `#5C7192` | Feldrahmen, etwa 3,6:1 [U] |
| neu `--color-on-warning`, `--color-on-danger` | – | `#10243e` | Text auf `mark` und `.danger` |
| neu `--side-width`, `--tabbar-height` | – | `14.5rem`, `4.125rem` | Layout |

Die Tokens `--color-danger`, `--color-success`, `--color-warning` bleiben. Wichtig: `mark` und `button.danger` nutzen heute `--color-action-text` als dunkle Schrift [B] `app.css:36,48`. Sie müssen auf die neuen `on-*`-Tokens umgestellt werden, sonst steht dort weisse Schrift auf hellem Grund.

Schriftgrössen: `html` bleibt 100 %. Fliesstext 1 rem, ab 861 px .875 rem. Kleinste Schrift .6875 rem; nur Tab-Labels dürfen bis 360 px Breite auf .625 rem gehen.

---

## 6. Datei-für-Datei-Änderungen (PR 1), in Reihenfolge

Branch: `feat/ui-command-center-shell` ab `main`. Die jüngsten Branches folgen `feat/…` und `fix/…` [B] Git-Log. Code und Commits englisch, Doku de-CH.

### 6.1 `docs/plans/UI_MOCKUP_ANGLEICHUNG.md` (neu)

Dieser Plan. Commit: `docs: add UI mockup alignment plan`.

### 6.2 `app/ui/overview.py` (neu, nur Lesen, mypy strict)

Reine Projektionen ohne DB-Zugriff, ausser `feature_flags()`, das die Umgebung liest. Im Modul darf kein Literal der Form `error="…"` stehen (K11).

```python
"""Read-only overview projections. Every number has a stored or configured source."""

Switch = Literal["off", "incomplete", "on"]

@dataclass(frozen=True)
class FeatureFlags:
    assistant_loopback: Switch   # DECK_ASSISTANT_OLLAMA == "1"; needs URL and model name
    model_named: bool            # DECK_ASSISTANT_OLLAMA_MODEL is non-empty; the name is never returned
    assistant_web: Switch        # DECK_ASSISTANT_WEB == "1"; needs allowlist and provider settings
    encyclopedia: bool           # web on and provider "mediawiki" complete
    web_search: bool             # web on and provider "searxng" complete
    routine_wake: Switch         # wake_enabled(); "incomplete" on TransportConfigError

def feature_flags() -> FeatureFlags: ...
def workflow_steps(status: str | None) -> tuple[Step, ...]: ...
def claims_summary(lanes: Sequence[LaneHealthView]) -> Claims | None: ...
def lane_rows(lanes: Sequence[LaneHealthView]) -> tuple[LaneRow, ...]: ...
def latest_outcome(items: Sequence[AuditItem]) -> Outcome | None: ...
def open_points(...) -> tuple[OpenPoint, ...]: ...
def network_nodes(...) -> tuple[NetNode, ...]: ...
```

- **`feature_flags`**: Bedingungen exakt wie `activation_status()` [B] `providers.py:242-283` und `wake_config_from_env()` [B] `transport/config.py:67-79`. Die Funktion gibt nie Werte zurück und loggt nichts.
- **`workflow_steps`**: sechs feste Schritte. «Geplant» plus die Labels aus `STATUS_LABELS` für `awaiting_approval`, `queued`, `leased`, `running`, `review`. Bei `done` sind alle erledigt. Bei `None` und bei `failed`, `blocked`, `cancelled`, `expired`, `succeeded` ist nichts hervorgehoben.
- **`claims_summary`**: `total` ist die Summe von `claims_today`. `limit` ist die Summe der Tageslimits, wenn jede Lane eines hat, sonst `None`. Leere Liste ergibt `None`, die Anzeige lautet dann «nicht verfügbar».
- **`lane_rows`**, feste Labels in dieser Rangfolge:
  1. nicht aktiviert → «Deaktiviert»
  2. `auth_required` → «Anmeldung nötig»
  3. pausiert → «Pausiert»
  4. `unavailable` → «Nicht verfügbar»
  5. verbleibende Versuche 0 → «Lokales Tageslimit erreicht»
  6. `ok` und `live_available` → «Bereit»
  7. `ok` im Modus `simulation` → «Simulation»
  8. `ok` sonst → «Vorprüfung ok»
  9. sonst → «Unbekannt»
- **`latest_outcome`**: jüngstes Ereignis mit Phase `completed` oder `failed`. Die Liste kommt absteigend [B] `audit/repository.py:118-123`.
- **`open_points`**, feste Reihenfolge, je Punkt `code`, Titel, Detail, Chip-Text und interner Link:
  1. Not-Aus aktiv
  2. Neue Arbeit pausiert
  3. N Freigaben offen → `/ui/tasks#waiting-tasks`
  4. N Aufträge blockiert → `/ui/tasks?state=blocked`
  5. je Lane-Warnung (`auth_required`, `no_recent_success`) → `/ui/lanes`
  6. Gesundheitsdaten nicht verfügbar
  7. Assistent-Loopback aus oder unvollständig → `/ui/assistant`
  8. Webabruf aus oder unvollständig
  9. Routine-Wake aus oder Sender unvollständig → `#meldungen`
  10. Kein abgeschlossenes Backup erfasst, oder letztes Backup fehlgeschlagen → `/ui/audit?source=operation&event_type=operation.backup`
  11. Keine Restore-Probe erfasst
- **`network_nodes`**: feste Koordinaten für `viewBox="0 0 400 250"`; `active` kommt aus den echten Zuständen.

### 6.3 `app/ui/views.py`

- `HOME_PATH = "/ui/overview"`.
- `NavItem` (frozen dataclass) mit `key`, `label`, `href`, `icon`, `group`, `tab_label`. `NAV_ITEMS` in dieser Reihenfolge:

| key | label | href | group | tab_label |
|---|---|---|---|---|
| overview | Übersicht | `/ui/overview` | workspace | Übersicht |
| tasks | Aufträge | `/ui/tasks` | workspace | – |
| new | Neuer Auftrag | `/ui/tasks/new` | workspace | – |
| knowledge | Wissen | `/ui/knowledge` | workspace | – |
| lanes | Lanes | `/ui/lanes` | control | Lanes |
| assistant | Assistent | `/ui/assistant` | control | Assistent |
| messages | Meldungen | `/ui/overview#meldungen` | control | Meldungen |
| open | Offene Punkte | `/ui/overview#offen` | control | Offen |
| chat | Lokaler Chat | `/ui/chat` | control | – |
| audit | Audit | `/ui/audit` | control | – |

- `nav_key(path: str) -> str`: längster Präfix gewinnt; `/ui/failed` zählt zu `tasks`, `/ui/tasks/new` zu `new`.
- Jinja-Globals `nav_items` und `home_path` neben den bestehenden [B] `views.py:225-233`.
- `page()` ergänzt `"nav": nav_key(request.url.path)` [B] `views.py:281-294`.

Die Sichtbarkeitsregel hinter K5: Bis 860 px sind Tabbar (Einträge mit `tab_label`) und Menü (Einträge ohne) sichtbar. Ab 861 px ist nur die Sidebar sichtbar; die Menü-Navigation ist dort ausgeblendet. So gibt es je Breite genau einen sichtbaren Link pro Ziel.

### 6.4 `app/ui/routes.py`

```python
root_router = APIRouter(include_in_schema=False)


@root_router.get("/")
def root() -> Response:
    # Fixed internal target; no query, cookie or session is read here.
    return RedirectResponse("/ui", status_code=303)
```

- `index()` leitet auf `HOME_PATH`. In `login_page()` und `login()` wird `"/ui/tasks"` durch `HOME_PATH` ersetzt (Zeilen 172, 209).
- Neu `overview()`. Es ruft nur bestehende, lesende Funktionen:

```python
@router.get("/overview")
def overview(request: Request, session: DbSession) -> Response:
    owner(request, session)
    waiting, waiting_total = list_owner_tasks(session, needs_owner=True, limit=5)
    _, blocked_total = list_owner_tasks(session, state=TaskState.BLOCKED, limit=1)
    latest, _ = list_owner_tasks(session, limit=1)
    recent = list_audit(session, AuditFilter(limit=5))
    backups = list_audit(
        session, AuditFilter(source="operation", event_type="operation.backup", limit=10)
    )
    restores = list_audit(
        session, AuditFilter(source="operation", event_type="operation.restore", limit=10)
    )
    ...
    return page(request, "overview.html", ...)
```

- Fokus-Auftrag: `waiting[0]`, sonst `latest[0]`, falls dessen Status `queued`, `leased` oder `running` ist, sonst keiner.
- Kontext für das Template: `waiting_total`, `focus`, `steps`, `claims`, `lane_rows`, `flags`, `activation` (aus `activation_status()`), `open_points`, `audit`, `csv_url="/ui/audit.csv?limit=5"`, `last_backup`, `last_restore`, `net`, `nav_counts`.
- Kein Zirkelimport: `app/assistant/providers.py` importiert nur `app.assistant.errors` und `.text` [B] `providers.py:15-16`. `_CHANNEL` wird nicht importiert, weil `app/assistant/http.py` seinerseits `app.ui.routes` importiert [B] `assistant/http.py:50`.

### 6.5 `app/api/app.py`

`root_router` importieren und nach `health.router` einbinden. Sonst keine Änderung, insbesondere nicht an der Middleware-Reihenfolge [B] `app.py:38,60`.

### 6.6 `app/ui/templates/partials/icons.html` (neu)

Ein Makro `icon(name)` mit Inline-SVG: `class="icon"`, `viewBox="0 0 24 24"`, `aria-hidden="true"`, `focusable="false"`. Die Pfade stammen aus dem Mockup: grid M:167, plus M:168, layers M:169, lanes M:173, person M:174, bell M:175, list M:176, chat M:177, shield M:178, clock M:212, trend M:213, database M:215, lines M:220, check M:223, circle M:225, arrow M:226, play M:227, diamond M:228. Farbe und Strichstärke kommen aus CSS. Kein externes `<use href>`. Jedes Template, das Icons nutzt, importiert das Makro selbst.

### 6.7 `app/ui/templates/base.html`

Diese Elemente bleiben wörtlich: Viewport-Meta, HTMX-Konfiguration, CSS-Link, HTMX-Script, Skip-Link, `<main id="content">` mit Fehlerbanner, `#control-status` samt `hx-*`-Attributen, beide Logout-Formulare, `<details class="menu">` mit `<summary>Menü</summary>` [B] `base.html:5-13,18-31,37-52`.

Neue Struktur:

```jinja
{% from 'partials/icons.html' import icon %}
{% set nav = nav|default('') %}{% set nav_counts = nav_counts|default({}) %}
<body class="{{ 'is-owner' if owner else 'is-guest' }}">
<a class="skip-link" href="#content">Zum Inhalt</a>
<div class="shell">
  {% if owner %}
  <aside class="sidebar" aria-label="Bereiche">
    <a class="brand" href="{{ home_path }}">◈ Control Deck</a>
    <p class="side-card">…Owner-Konsole · Ein Owner…</p>
    {# je Gruppe: <p class="side-section"> und <nav class="side-nav" aria-label="…"> #}
    {# Link: aria-current="page" wenn item.key == nav #}
    {# Zähler: <span class="nav-count" aria-hidden="true"> #}
    <p class="side-note">…</p><p class="side-user">Owner · angemeldet</p>
  </aside>
  {% endif %}
  <div class="shell-main">
    <header class="topbar">
      {% if owner %}
      <p class="crumb"><a href="{{ home_path }}">Deck</a><span aria-hidden="true">/</span>
        <b>{% block crumb %}Control Deck{% endblock %}</b>{% block topbar_badge %}{% endblock %}</p>
      <details class="menu"><summary>Menü</summary><div class="menu-panel">
        <nav class="menu-nav" aria-label="Weitere Bereiche">{# Einträge ohne tab_label #}</nav>
        <form action="/ui/logout-all" method="post">…Überall abmelden…</form></div></details>
      <form action="/ui/logout" method="post" class="inline-form">…Abmelden…</form>
      {% else %}<a class="brand" href="/ui/login">◈ Control Deck</a>{% endif %}
    </header>
    {# #control-status unverändert #}
    <main id="content">…</main>
    <footer>…</footer>
  </div>
  {% if owner %}<nav class="tabbar" aria-label="Schnellzugriff">{# Einträge mit tab_label #}</nav>{% endif %}
</div>
```

- Ohne `request` und ohne Zusatzkontext rendern (F19): `|default`, kein Zugriff auf `request.url`.
- Kein `h1` in der Shell (K6), kein zweites `main` (K8), genau ein Button «Abmelden» (K3), der Text «Menü» genau einmal (K4).
- Im `head` zusätzlich `<meta name="color-scheme" content="dark">` und `<meta name="theme-color" content="#090E19">`.

### 6.8 `app/ui/static/app.css`

Eine Datei, kein `@import`, kein `@font-face`, kein `url()`. Abschnitte:

1. Tokens gemäss Abschnitt 5.
2. Elemente: Body mit `background-color: var(--color-bg)` plus Verlauf als `background-image` (K7); Feldrahmen mit `--color-control-border`.
3. Shell.
4. Komponenten.
5. Bestehende Seitenklassen bleiben funktionsfähig: `.task-grid`, `.filter-grid`, `.progress-band`, `.timeline`, `.tabs`, `.pagination`, `.notice`, `.status*`, `.control-band`.
6. Media-Queries in `rem` wie bisher [B] `app.css:96,101`.
7. Block für reduced-motion.

Kernregeln der Shell [V]:

```css
.shell { display: grid; grid-template-columns: var(--side-width) minmax(0, 1fr);
         min-height: 100vh; min-height: 100dvh; }
.is-guest .shell { grid-template-columns: minmax(0, 1fr); }
.sidebar { position: sticky; top: 0; height: 100vh; height: 100dvh; overflow: auto; }
.topbar { position: sticky; top: 0; z-index: 4; display: flex; flex-wrap: wrap;
          padding-top: max(.625rem, env(safe-area-inset-top, 0px)); }
.tabbar, .is-guest .topbar .crumb { display: none; }
@media (min-width: 53.8125rem) { .menu-nav { display: none; } }
@media (max-width: 53.75rem) {
  .shell { grid-template-columns: minmax(0, 1fr); }
  .sidebar { display: none; }
  .topbar { position: static; }
  .is-owner .shell-main { padding-bottom: calc(var(--tabbar-height) + 2rem + env(safe-area-inset-bottom, 0px)); }
  html { scroll-padding-bottom: calc(var(--tabbar-height) + 2rem + env(safe-area-inset-bottom, 0px)); }
  .tabbar { display: flex; position: fixed; z-index: 6;
            left: max(.75rem, env(safe-area-inset-left, 0px));
            right: max(.75rem, env(safe-area-inset-right, 0px));
            bottom: calc(.75rem + env(safe-area-inset-bottom, 0px)); }
  .tabbar a { flex: 1 1 0; min-width: 0; min-height: 3.375rem; }
}
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after { animation: none !important; transition: none !important; scroll-behavior: auto !important; }
}
```

- Die Bottom-Nav heisst `.tabbar`, nie `.tabs` (F18). `.side-nav`, `.menu-nav` und `.tabbar` überschreiben die globalen `nav`-Regeln ausdrücklich.
- Der aktive Eintrag wird über `[aria-current="page"]` gestaltet.
- Blur mit `-webkit-`-Präfix und deckendem Ersatz über `@supports not`.
- Buttons: Standard ist primär (Blau, weisse Schrift). `.quiet` entspricht dem Mockup-`.btn`. Neu `.ghost`. `.danger` erhält die Schrift `--color-on-danger`.
- Chips: `.chip`, `.chip--on`, `.chip--off`, `.chip--warn`, immer mit Text.
- [U] `position: sticky` zusammen mit `body { overflow-x: hidden }` [B] `app.css:30` im Browser prüfen; bei Problemen `overflow-x: clip` ergänzen.

### 6.9 `app/ui/templates/login.html`

Struktur, Texte, Label «Passwort», `id="password"`, `autocomplete="current-password"`, Button «Anmelden» und der Sperrblock bleiben wörtlich [B] `login.html:4-22`. Neu sind nur Klassen (`panel narrow login-card`), die Marke über der Karte und `enterkeyhint="go"`. Kein `autofocus`, keine Navigation, kein `fixed` oder `sticky`.

### 6.10 `overview.html`, `overview.css`, `partials/overview_net.html` (neu)

`overview.html` erweitert `base.html` und lädt `overview.css` über `head_extra`, wie `assistant.html:3`. Titel «Übersicht · Control Deck», Crumb «Übersicht». Der Block `topbar_badge` zeigt `{{ waiting_total }} GO offen` oder «Kein GO offen».

Abschnitte, je `<section aria-labelledby>` mit `h2` und `scroll-margin-top`:

1. Seitenkopf: `h1` «Owner-Konsole», Claim, «Audit-Trail», «＋ Neuer Auftrag».
2. Hero: Texte gemäss Mapping, zwei Buttons, daneben die Netzgrafik.
3. KPI-Raster: vier Kacheln. Jede Zahl kommt aus dem Kontext; ohne Wert steht «nicht verfügbar» oder «noch keines erfasst».
4. `#flow`: Stepper als `<ol class="stepper">`. Der aktuelle Schritt trägt `aria-current="step"`, die Nummern 01–06 sind `aria-hidden`. Darüber Titel, Datenklasse und Status des Fokus-Auftrags.
5. `#lanes`: Liste aus `lane_rows`, Zeit als `<time datetime>` im Format von `partials/timeline.html:3`.
6. `#assistent`: Chips aus `flags`, darunter die Zeilen aus `activation`.
7. `#meldungen`: Chip, korrigierter Text (F12), Sender-Zustand, Flag-Name als `<code>`.
8. `#offen`: `<ol class="open-points">`, je Eintrag `data-open-point="{{ code }}"`. Leer: «Keine offenen Punkte aus den Deck-Daten.»
9. `#audit`: `<ol class="audit-log">` mit Zeit, Ereignistyp und sicheren Feldern (`lane`, `to_status`, `phase`, `reason`), dazu der CSV-Link und «Alle Ereignisse →».

Regeln für das Template:

- Feste Ziffern nur in «01»–«06» und «CD-01».
- Kein `data-lane-warning` (K9).
- Kein Link mit exaktem Namen «Audit», «Lanes» oder «Wissen» (K5).
- Jeder Link ist ein blockiges Ziel mit mindestens 44 px Höhe.

`partials/overview_net.html`: `<svg class="net" viewBox="0 0 400 250" role="img" aria-labelledby="net-title">` mit `<title>`, zwei Ellipsen, Kanten und Knoten aus `net`. Inaktive Knoten sind gestrichelt und tragen zusätzlich « · aus» im Label. Geometrie steht in SVG-Attributen, das Aussehen in `overview.css`. Bis 420 px Breite sind die Labels der Infrastruktur-Knoten ausgeblendet, wie M:412. Darunter steht eine visuell versteckte Textliste der Knoten.

`overview.css`: Hero-Raster ab 981 px zweispaltig; KPI-Raster `repeat(auto-fit, minmax(12.5rem, 1fr))`; zweispaltige Bereiche unter 981 px einspaltig; Facts bis 520 px einspaltig.

### 6.11 Assistent: `assistant/http.py`, `assistant.html`, `assistant.css`

- `_context()` ergänzt `"flags": feature_flags()` [V] [B] `assistant/http.py:69-87`.
- `assistant.html`: Crumb «Assistent». Unter dem `h1` eine Chip-Zeile (Loopback, Web, Routine-Wake), geschützt mit `{% if flags is defined %}`.
- Unverändert bleiben alle Texte (K13), alle Formulare und Feldnamen sowie die IDs `assistant-talk-form`, `message`, `talk-live-status`, `talk-live-meta`, `talk-live-output` [B] `assistant_talk.js:2-5`, der Wrapper `.assistant-page` und die Reihenfolge der Abschnitte.
- Der Link «Export» [B] `assistant.html:105` wird `class="button quiet"`.
- `assistant.css`: neue Tokens; `pre` als Antwortfläche; die Überlauf-Regeln bleiben [B] `assistant.css:1-18`.
- `assistant_talk.js` bleibt unverändert.

### 6.12 `error.html`

Zusätzlicher Link «Zur Übersicht» auf `home_path`. «Aufträge anzeigen» und «Zum Login» bleiben.

### 6.13 Doku

- `docs/UI.md`: Tabellenzeilen für `/` und `/ui/overview`; Abschnitt «Shell und Navigation»; Token-Liste; Hinweis, dass die Systemschrift gewählt ist, weil die CSP kein `font-src` hat.
- `CHANGELOG.md`: Eintrag unter «Unveröffentlicht».
- `docs/STAND.md`: kurzer Nachtrag ohne Live-Behauptung.

Sprache de-CH ohne Eszett, keine Namen, keine Hostnamen.

### 6.14 Abschluss

`uv run ruff format .`, dann `make lint` und `make test` [B] `Makefile:9-15`. Danach Vorschau gemäss Abschnitt 9 und Draft-PR mit ausgefülltem Template.

---

## 7. Tests

### 7.1 Neue Tests

| ID | Datei | Prüft |
|---|---|---|
| T-ROOT | `tests/unit/test_ui_http_boundary.py`, Parameter `("GET", "/", 303)` | 303 und alle Sicherheits-Header, ohne DB |
| T-ROOT2 | `tests/unit/test_ui_root.py` | `Location == "/ui"`, auch bei `/?next=https://example.invalid`. `/ui` → `/ui/overview`. `POST /` → 405. `/healthz` 200. `/api/v1/health` anonym `{"status":"ok"}`. OpenAPI ohne Pfad `/`. |
| T-CSP | `tests/unit/test_ui_root.py` und `tests/integration/test_ui_overview.py` | Beide CSP-Konstanten gleichen dem gepinnten Literal aus F5. Die echte Antwort von `/ui/login`, `/ui/overview`, `/ui/assistant` trägt genau einen CSP-Header mit dem Boundary-Wert. |
| T-INLINE | `tests/unit/test_ui_shell.py` | Alle Templates: kein `<style>`, kein `style`-Attribut, kein `<script>` ohne `src`, kein `on*`-Attribut, kein `javascript:`. `src` und `href` von Script und Link beginnen mit `/ui/static/`. Alle CSS- und JS-Dateien: kein `http://`, `https://`, `//`, `@import`, `@font-face`, `fonts.g`. |
| T-VIEWPORT | `test_ui_shell.py` | Viewport enthält `width=device-width` und `initial-scale=1`; ohne `maximum-scale`, `minimum-scale`, `user-scalable`. |
| T-LOGIN | `test_ui_shell.py` und Browser | `login.html` gerendert: keine `sidebar`, keine `tabbar`, kein `hx-get`. Browser bei 320 und 390 px: kein Element mit `position` `fixed` oder `sticky`; das Passwortfeld ist von nichts überdeckt. |
| T-NAV | `test_ui_shell.py` | Ersatz für K10. Sidebar zeigt alle zehn Ziele in Reihenfolge. Tabbar zeigt genau Übersicht, Lanes, Assistent, Meldungen, Offen. Menü und Tabbar sind disjunkt. Genau ein «Abmelden», «Überall abmelden» unter «Menü», «Menü» einmal. `nav_key()` für alle Pfade. Rendern ohne `request` schlägt nicht fehl. |
| T-KONTRAST | `test_ui_shell.py` | Liest die Hex-Tokens aus `app.css` und rechnet WCAG-Kontrast. Mindestens 4,5 für Text, Text-2, Muted und Accent auf Panel und Side, sowie für die Schrift auf Action, Danger und Warning. Mindestens 3,0 für den Feldrahmen auf Panel und Bg. |
| T-OV-UNIT | `tests/unit/test_ui_overview.py` | `workflow_steps` für jeden `TaskState`. `claims_summary` mit und ohne Limit und leer. `lane_rows` für jede Rangstufe. `latest_outcome`. `open_points` mit Reihenfolge und Codes. `feature_flags` über die Env-Matrix. |
| T-ZAHLEN | `test_ui_overview.py` | Mit leerem Kontext enthält der sichtbare Text von `overview.html` keine Ziffer ausser «01»–«06» und «CD-01». Mit Sentinel-Zahlen (z. B. 7411, 6203) erscheint jede genau dort. Die Quelltexte von `overview.html`, `base.html`, `login.html` enthalten keines von: `Beispiel`, `qwen`, `abliterated`, `Ubuntu`, `5433`, `PR64`, `PR65`, `agy`, `Gemini`, `04:00` (Owner-Name und Hoster-Name nicht als Literal ins Repo; AGENTS.md). |
| T-OV-AUTH | `tests/integration/test_ui_overview.py` | Anonym: `/ui/overview` → 303 `/ui/login`. Login-Erfolg → `Location == "/ui/overview"`, auch mit Formularfeld `next=https://example.invalid`. Angemeldet: `/ui/login` → 303 `/ui/overview`. Kette ab `/` endet angemeldet auf der Übersicht, anonym auf dem Login. |
| T-OV-DATEN | `test_ui_overview.py` | Zwei Aufträge anlegen: die Übersicht zeigt 2 offene Freigaben; nach einer Freigabe 1. Drei Lanes mit Namen. Die letzten Audit-Ereignisse erscheinen. Ein `OperationEvent` Backup `completed` erscheint als letztes Backup, wie in `test_audit_browser.py:27-35`. Ohne Ereignis steht «noch keines erfasst». |
| T-FLAGS | `test_ui_overview.py` | Sentinel-Werte für `DECK_ROUTINE_WAKE_URL`, `DECK_ROUTINE_WAKE_AUTHORIZATION`, `DECK_OLLAMA_URL`, `DECK_ASSISTANT_OLLAMA_MODEL`, `DECK_ASSISTANT_SEARXNG_URL`, `DECK_ASSISTANT_MEDIAWIKI_API`, `DECK_ASSISTANT_WEB_ALLOWLIST` und `DECK_OWNER_TOKEN` setzen. Keiner erscheint im HTML von `/ui/overview` oder `/ui/assistant`. Die Chips wechseln korrekt. |
| T-320, T-TAP, T-FOKUS, T-MOTION | `tests/integration/test_ui_shell_browser.py`, Chromium 320, 390, 1024 px | Auf `/ui/overview`, `/ui/assistant`, `/ui/login`: kein Quer-Scrollen; ein `h1`. Tabbar bis 860 px sichtbar, darüber die Sidebar. Tab-Labels ohne Abschnitt. Sichtbare Bedienelemente mindestens 44 px hoch. Fokussierter Tab hat einen sichtbaren Umriss. Mit `reduced_motion="reduce"` läuft keine Animation. Keine externen Requests, keine Seitenfehler. Nach Fokus auf «Nachricht» überschneidet die Tabbar das Feld nicht. |
| T-WEBKIT | `tests/integration/test_ui_preview_webkit.py` | Siehe Abschnitt 9; überspringt ohne WebKit, wie `test_assistant_browser.py:114-120`. |

### 7.2 Bewusst angepasste Tests

Keine Abschwächung; jede Änderung folgt aus der neuen Startseite oder Navigation.

| Datei | Änderung | Grund |
|---|---|---|
| `tests/unit/test_ui_mobile.py:77-91` | neu als T-NAV | K10 |
| `tests/integration/test_ui_browser.py:98,162,169,192` | `/ui/tasks` → `/ui/overview`; die Schleife bei Zeile 142 um `/ui/overview` und `/ui/assistant` erweitern | K1 |
| `tests/integration/test_knowledge_browser.py:56` | bis 860 px zuerst «Menü» öffnen | «Wissen» liegt mobil im Menü |
| `tests/unit/test_ui_http_boundary.py:21-30` | Parameter für `/` | T-ROOT |

`test_audit_browser.py` und `test_assistant_browser.py` müssen unverändert grün bleiben. Das belegt die Regeln K4, K5, K8, K9.

### 7.3 Befehle

```bash
uv sync --all-extras
uv run ruff format .
make lint
uv run alembic -x target=test upgrade head
uv run alembic -x target=test check
make test
```

Ohne `DECK_TEST_DATABASE_URL` werden die Integrationstests übersprungen [B] `CLAUDE.md`. Dann ist `make test` grün, belegt die Oberfläche aber nicht. In diesem Fall zählt die CI [B] `.github/workflows/ci.yml:30,86-90`.

---

## 8. PR-Schnitt

Empfehlung: drei PRs. PR 1 trägt die ganze Shell; ein halber Umbau ergäbe eine gemischte Oberfläche.

**PR 1 (Draft): `feat(ui): command-center shell, overview and root redirect`**

Muss enthalten:

- Plan-Datei (6.1)
- `app/ui/overview.py` mit Unit-Tests (6.2)
- Navigation und `HOME_PATH` (6.3)
- `/`-Redirect, Login-Ziele, Route `/ui/overview` (6.4, 6.5)
- Icons, neue Shell, Tokens (6.6–6.8)
- Login (6.9)
- Übersicht mit allen neun Abschnitten, weil Tabbar und Sidebar auf `#meldungen` und `#offen` zeigen (6.10)
- Assistent (6.11), Fehlerseite (6.12)
- alle Tests aus 7.1 und 7.2
- Doku (6.13)

Nicht in PR 1: Topbar-Suche, optische Feinarbeit an den übrigen Seiten, Auto-Refresh, 3D-Patch. Die übrigen Seiten laufen in PR 1 bereits in der neuen Shell mit den neuen Tokens und müssen alle bestehenden Tests bestehen.

Commits:

1. `docs: add UI mockup alignment plan`
2. `feat(ui): add overview projections and feature flags`
3. `feat(ui): redirect root to the UI and add the owner overview`
4. `feat(ui): add sidebar, top bar and tab bar shell`
5. `feat(ui): restyle login and assistant pages`
6. `test(ui): cover root redirect, shell, overview and CSP invariants`
7. `docs: describe overview, navigation and theme tokens`

PR-Text nach Template [B] `.github/pull_request_template.md`: Etappe «keine, UI-Angleichung»; Reviewer-Anbieter ein anderer als der Bau-Agent (ADR 0008); unter «Bewusst offen» die Punkte aus Abschnitt 10. Kein Merge durch Agenten.

**PR 2: `feat(ui): align remaining pages with the command-center components`**

Aufträge, Detail (Stepper aus `workflow_steps`), Formular, Lanes, Audit, Chat, Wissen, Fehlerliste. Topbar-Suche «Auftragstitel suchen» als GET auf `/ui/tasks`. Optional: Tabbar während der Texteingabe ausblenden, nur bei `pointer: coarse`, mit verzögertem Wiedererscheinen.

**PR 3 (optional): `feat(ui): overview refresh and network motion`**

Lesendes Fragment `/ui/fragments/overview` mit HTMX-Polling; CSS-Puls auf den SVG-Kanten; Zähler aus `routine_outbox` [B] `app/db/models.py:286`. Der 3D-Patch nur nach Entscheid O3.

---

## 9. Lokaler Vorschau-Nachweis

Grundlage: Die Browser-Smokes laufen gegen eine isolierte App mit `_test`-Datenbank, synthetischem Passwort und freiem Port [B] `docs/UI.md:153-158`, `tests/integration/test_ui_browser.py:29-67`. Die Fixture bindet `localhost` mit Port 0, also einen freien Loopback-Port, und setzt `DECK_UI_COOKIE_SECURE=false`.

**Variante A (empfohlen): Screenshots über pytest, ohne Produktionsvariable**

Neuer Test `tests/integration/test_ui_preview_webkit.py`:

- nutzt die Fixture `browser_app`,
- startet `playwright.webkit` mit Viewport 390 × 844, `device_scale_factor=3`, `is_mobile=True`, `has_touch=True`,
- lädt `/ui/login`, meldet sich an, lädt `/ui/overview` und `/ui/assistant`,
- schreibt `login-390x844.png`, `overview-390x844.png`, `assistant-390x844.png` nach `DECK_UI_PREVIEW_DIR` (Standard `tmp_path`),
- prüft dabei T-LOGIN und kein Quer-Scrollen,
- überspringt ohne installiertes WebKit.

```bash
# Lokale Wegwerf-DB: Name endet auf _test, Loopback, nicht Port 5433.
export DECK_TEST_DATABASE_URL='postgresql+psycopg://<nutzer>@127.0.0.1:5432/controldeck_test'
uv sync --all-extras
uv run alembic -x target=test upgrade head
uv run playwright install webkit
DECK_UI_PREVIEW_DIR=/tmp/deck-preview uv run pytest tests/integration/test_ui_preview_webkit.py -q
```

- Die Screenshots liegen ausserhalb des Repos und werden nicht committet; `.gitignore` deckt PNG nicht ab [B].
- [U] WebKit braucht Systembibliotheken (`playwright install-deps webkit` verlangt Root). Falls das fehlt, den Owner fragen und nichts umgehen.
- [U] Ob beim Bau-Agenten ein lokales PostgreSQL mit `_test`-DB bereitsteht, ist offen. Browser-Tests laufen laut Übergabe nur in der CI verlässlich [B] `HANDOFF.md:33-35`.
- Die CI installiert nur Chromium [B] `ci.yml:89`. WebKit in der CI wäre eine eigene, zu begründende Änderung.

**Variante B (optional): Server zum Durchklicken**

Der validierte Starter verlangt `DECK_BIND_HOST`, `DECK_ALLOWED_HOSTS`, einen Port und beim Start eine lesbare Datenbank über `DECK_DATABASE_URL` [B] `app/core/bind.py:150-184`, `app/api/server_main.py:24-27,49-71`. Unsichere Cookies sind nur auf Loopback erlaubt [B] `bind.py:175-176`.

```bash
# Nur für diesen einen Prozess; DECK_DATABASE_URL zeigt hier auf die lokale _test-DB.
env -u DECK_ENV_FILE -u DECK_PUBLIC_ORIGIN \
  DECK_DATABASE_URL="$DECK_TEST_DATABASE_URL" \
  DECK_BIND_HOST=127.0.0.1 DECK_BIND_PORT=<freier Port> \
  DECK_ALLOWED_HOSTS=127.0.0.1,localhost DECK_UI_COOKIE_SECURE=false \
  DECK_OWNER_PASSWORD_HASH='<Argon2id-Hash eines synthetischen Passworts>' \
  uv run python -m app.api.server_main
```

Schutzregeln:

- Vor dem Start prüfen: DB-Name endet auf `_test`, Host ist Loopback, Port ist nicht 5433.
- Keine Variable aus einer `*.env`-Datei laden; die URL nie ausgeben.
- Den Hash mit `make set-password` oder wie in den Fixtures erzeugen, nur mit synthetischem Passwort [B] `Makefile:3-4`, `test_ui_browser.py:31-32`.
- Keine Runner, kein Ollama, kein Wake.

Der Nachweis für den PR ist Variante A. Variante B ist nur Komfort.

---

## 10. Risiken, bewusst offen, Live-Deploy

### 10.1 Risiken

| # | Risiko | Gegenmassnahme |
|---|---|---|
| R1 | Strikte Playwright-Locator brechen durch doppelte Links. | Sichtbarkeitsregel aus 6.3; T-NAV; `test_audit_browser.py` unverändert grün |
| R2 | Die Tabbar überdeckt Bedienelemente oder fängt Klicks ab. | Platz unten, `scroll-padding-bottom`, T-320; kein Ausblenden in PR 1 |
| R3 | Die Kontrastwerte sind von Hand gerechnet. [U] | T-KONTRAST erzwingt sie; Tokens bis grün anpassen |
| R4 | Die Systemschrift weicht vom Mockup ab. | bewusst (F6); Entscheid O6 |
| R5 | Sechs zusätzliche Abfragen je Aufruf der Übersicht, darunter drei Audit-Unions. [U] Laufzeit | nur bestehende Lesefunktionen; bei Bedarf in PR 3 bündeln |
| R6 | Geänderte Token-Werte treffen alle Seiten, etwa den vormals grünen Button, `mark`, `.danger`. | on-Tokens; Chromium-Smokes über alle Seiten |
| R7 | Sticky Topbar und `overflow-x: hidden` vertragen sich in Safari nicht. [U] | Desktop-Prüfung; Ersatz `overflow-x: clip` |
| R8 | Die abgeleiteten «Offenen Punkte» wirken wie eine Projektliste. | Titelzusatz «aus Deck-Daten»; Entscheid O5 |
| R9 | Neues Ziel nach dem Login überrascht Lesezeichen auf `/ui/tasks`. | `/ui/tasks` bleibt erreichbar; Entscheid O4 |
| R10 | Jemand fragt `/` mit Erwartung 404 ab. [U] | Health-Pfade unverändert; im PR nennen |
| R11 | Emulator ist kein iPhone. | physischer Safari-Test bleibt offen [B] `docs/ASSISTENT.md:42` |

### 10.2 Bewusst offen

- 3D-Patch und three.js; ⌘K; globale Suche über Lanes und Ereignisse.
- Anzeige des Deploy- oder GO-Stands «v2», von PR-Nummern und Hoster.
- Backup-Uhrzeit und Offsite-Restore-Status: Das Deck zeigt nur erfasste Ereignisse.
- Modellname und Modellqualität als Anzeige.
- Eigene Seiten für «Meldungen» und «Offene Punkte»; Zähler der Wake-Zeilen.
- Webfonts; PWA-Manifest (bräuchte `manifest-src`).
- Rücksprung auf die ursprünglich angefragte Seite nach dem Login.

### 10.3 Owner-Entscheide

| # | Frage | Vorschlag |
|---|---|---|
| O1 | Badge «v2 · GO offen» durch echten Freigaben-Zähler ersetzen? | ja |
| O2 | Name in der Oberfläche («Meldungen an <Owner>», «Deck von <Owner>»)? | nein, neutral «Meldungen» (F15) |
| O3 | 3D-Patch mit lokal abgelegtem three.js? | vorerst nein; statische Marke |
| O4 | Übersicht als Ziel nach dem Login? | ja |
| O5 | «Offene Punkte» aus Deck-Daten statt Projektliste? | ja |
| O6 | Systemschrift statt Manrope und Plex? | ja; Webfonts nur mit CSP-Entscheid |

### 10.4 Live-Deploy (nur mit Owner-GO, nicht durch Agenten)

Voraussetzungen:

1. Externes Review durch einen anderen Anbieter.
2. CI grün.
3. Vorschau-Screenshots im PR.
4. Ausdrückliches GO des Owners; der Owner mergt selbst.

Durchführung durch den Owner:

- Update nach Betriebshandbuch [B] `docs/OPERATIONS.md:18-21`, `Makefile:34-35`.
- Der API-Dienst muss neu starten, weil Python-Code und Templates im Prozess geladen sind. [U] genaues Verhalten des Template-Caches.
- Statische Dateien kommen direkt aus dem Checkout [B] `app/api/app.py:54-58`. Wegen `no-store` gibt es kein veraltetes CSS im Browser.
- Keine Migration, keine neue Umgebungsvariable, keine geänderte Unit.
- Rückweg: voriger Commit und Neustart; kein Datenbank-Schritt.

Abnahme durch den Owner am iPhone in Safari über das Tailnet:

- Login mit Tastatur und Passwort-Manager.
- Pinch-Zoom funktioniert.
- Übersicht mit echten Zahlen gegen `/ui/tasks`, `/ui/lanes`, `/ui/audit` gegenlesen.
- Tabbar und Home-Indikator überdecken sich nicht.
- Auf dem Assistenten liegt bei offener Tastatur nichts über dem Feld.
- Querformat mit Notch.
- «Menü», dann «Überall abmelden».

---

## Kurzfassung für Plan-Dokument

1. Ziel: Live-UI im Command-Center-Layout des Mockups, ohne erfundene Daten und ohne Änderung an Auth, CSRF, Login-Limit, CSP oder Schema (Alembic bleibt `20261108_0063`).
2. Wirksame CSP ist `default-src 'none'` ohne `font-src`: Systemschrift statt Google Fonts; kein Inline-Style oder -Script; die Netzgrafik wird Inline-SVG ohne JS.
3. Neu sind nur `GET /` (303 → `/ui`, ohne DB) und `GET /ui/overview` (lesend, nur mit Session); `/ui` und der Login zielen fix auf die Übersicht, ohne `next`-Parameter.
4. Shell: Sidebar ab 861 px, darunter fixe Tabbar Übersicht/Lanes/Assistent/Meldungen/Offen; Topbar mobil nicht sticky; Login ohne Navigation.
5. Echt: offene Freigaben, Claims und Tageslimit, 3 Lanes, letztes Backup- und Restore-Ereignis, Stepper des Fokus-Auftrags, Flags als Zustand, die letzten 5 Audit-Ereignisse.
6. Entfällt oder wird ersetzt: «v2 · GO offen», PR-Nummern, Hoster, Name, agy als vierte Lane, 04:00 (Repo-Vorlage 03:15), Modellname, Sparkline, 3D-Patch, ⌘K.
7. Mockup-Texte korrigiert: Wake wiederholt nur bei HTTP-Status ausser 200; Gesprächskontext nur per Kästchen; «uncensored» ist nicht belegt.
8. Bestehende Browser-Tests binden die Shell (ein `h1`, ein `main`, ein «Abmelden», «Menü», exakte Linknamen); vier Tests werden bewusst angepasst, keiner abgeschwächt.
9. Neue Tests: Redirect, CSP-Pin, kein Inline, Viewport ohne Zoomsperre, Login ohne fixe Navigation, Navigations-Matrix, Kontrast, Zahlen nur mit Quelle, kein Flag-Wert im HTML, 320/390/1024 px.
10. Drei PRs; PR 1 (Draft) umfasst Plan, Shell, Tokens, Login, Übersicht, Assistent, `/`-Redirect, Tests und Doku.
11. Vorschau: pytest mit `browser_app`, `_test`-DB und Playwright-WebKit 390 × 844; Screenshots ausserhalb des Repos. WebKit-Installation und die Kontrastwerte sind unbewiesen.
12. Offen für den Owner: O1–O6 (Badge, Name, 3D-Patch, Startseite, Offene Punkte, Schrift); Merge und Deploy nur mit GO, Safari-Test am echten iPhone bleibt Abnahme.
