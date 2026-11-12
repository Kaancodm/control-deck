# HANDOFF

Stand: 2026-11-11. Uebergabe aus der Planungssession «Claude-Lane live».

Nachtrag 2026-11-12: Rollen, Modelle, Merge und Gates stehen in `AGENTS.md`. If anything conflicts, AGENTS.md wins. Die Notizen darunter sind der Sitzungsstand vom 2026-11-11 und gelten nicht als aktuelle Rollen- oder Merge-Regel. PR #84 ist gemergt (GitHub `mergedAt` 2026-11-11T19:24:36Z, Merge-Commit `2fbb91d`; Quelle GitHub API, 2026-11-12, proven fuer diese Felder).

## Stand

- Branch `claude/fervent-pasteur-upzu9m`, letzter Commit vor dieser Datei `4e32a02`.
- Draft-PR #84 «docs(tasks): plan build ticket 08 for the Claude lane live profile» gegen
  `main`. CI auf dem Commit `41b5bcd`: `quality` und GitGuardian gruen.
- Inhalt des PR: `tasks/08-claude-lane-live.md` (Teile A bis D, 19 Schritte,
  Owner-Entscheide E1 bis E6, Risiken und Rueckweg) und diese Uebergabe. Kein Code, kein
  Schema, keine Migration.
- Gegenpruefung des Auftrags: ein Lese-Review gegen den Code und drei Pruefer (externe
  Annahmen gegen Herstellerdoku, Sicherheit, Testbarkeit). Befunde sind in den Commits
  `fdb1be6` und `4e32a02` eingearbeitet. Niemand hat Docker oder eine echte CLI gestartet.
- `make lint`: exit 0.
- `make test`: exit 2 mit `22 failed, 2744 passed, 56 skipped, 1004 errors`. Alle Fehler
  liegen in `tests/integration/`, weil in der Cloud-Umgebung kein PostgreSQL auf dem lokalen
  Port laeuft. Kein Unit-Test ist rot. Massgeblich ist der CI-Lauf am PR.
- Befunde aus dem Repo (gelesen am 2026-11-11, Stellen stehen im Auftrag):
  - Ollama live braucht nur Konfiguration am Betriebsort; fuer `claude` und `codex` gibt es
    keinen Live-Code.
  - Auftraege anlegen ueber die API verlangt das Owner-Token, das auch freigeben kann. Ein
    Token nur zum Anlegen gibt es nicht.
  - Auftraege lassen sich im Deck nicht verketten; `parent_task_id` wird nicht ausgewertet.
  - Einziger weiterer offener PR: #56 (Systemprompt, Draft).
- Owner-Vorgaben vom 2026-11-11, nur noch Sitzungsprotokoll (Owner-Aussage, im Repo nicht
  belegt; seit 2026-11-12 durch `AGENTS.md` ersetzt): damals Planer Claude, Builder Cursor
  mit dem Modell Opus, fuer kleine Auftraege Sonnet, fuer komplexe bei Bedarf Fable, Review
  Grok, Codex und Gemini Pro nicht im Team. Aktuell: Claude plant mit `/advisor` (Fable),
  Cursor baut, Claude baut nur komplexe Auftraege wenn der Auftrag das ausdruecklich sagt
  (Opus 5.5, Effort medium), Gemini Pro ist Lead Full-Stack Developer und
  Pair-Programming-Partner ohne Merge-Recht und nicht Security-Head. Ein Betriebs-Bot
  verwaltet Router, Chat-Oberflaeche und lokale Modelle am Betriebsort. Die Benutzerkonten
  dort bleiben unveraendert. Altprojekte werden nicht eingespeist.

## Offene Punkte

- E1 bis E6 sind beantwortet: «Ja zu allen» (Owner-Aussage vom 2026-11-11, im Repo nicht
  belegt). Damit laeuft der Claude-Runner unter dem Dienstkonto mit eigener Anmeldung (E4).
- Das Merge-GO fuer PR #84 lag vor (Owner-Aussage vom 2026-11-11, im Repo nicht belegt).
  Der Satz aus dieser Sitzung, der Owner merge selbst und Agenten mergten nicht, gilt nicht
  mehr. Aktuell mergt nur Grok Bot, und nur nach dem Ja des Owners (`AGENTS.md`). PR #84 ist
  gemergt (siehe Kopf).
- Hermes und n8n: Der Installationszustand am Betriebsort ist unbekannt. Der Owner fuehrt
  zuerst einen lesenden Statusbefehl aus; erst danach folgen Einschaltbefehle.
- Vorschlag, nicht beauftragt: ein kleiner Bauauftrag «Anlege-Token», bevor Hermes oder n8n
  Zugriff auf die API erhalten.
- Schutzpunkte fuer den Betrieb: Das Deck verlangt exakt die gepinnte Ollama-Version
  (ADR 0009). Das Embedding-Modell der Wissens-DB (ADR 0011) und die Modelle aus der
  Router-Datei duerfen nicht geloescht werden.
- Sitzungsstand 2026-11-11, damals nicht angefasst: Statusspalte in `docs/ETAPPEN/README.md`, Kopf von
  `tasks/04-a-kb-runtime-migration.md` (mit #59 gemergt), `CLAUDE.md` nennt fuer die CI
  `postgres:17`, der Workflow nutzt ein pgvector-Image.
- ADR 0008 hat die Review-Rotation (Codex und Claude) seit dem Nachtrag 2026-11-11 als
  Historie. Der Nachtrag 2026-11-12 ersetzt zusaetzlich die Rolle «SuperGrok reviewt nur den
  Code» und den Satz, Gemini Pro sei nicht im Einsatz. Massgebend ist `AGENTS.md`.
- Bewusst nicht gemacht: kein Server-Zugriff, kein Docker, keine echte CLI, kein Merge.
- Diese Datei liegt mit Zustimmung des Owners im Branch von PR #84.

## Naechster Schritt

- PR #84 ist gemergt. Teil A ist laut `COORDINATION.md` ebenfalls gemergt (PR #88,
  `a8bd991`). Der naechste Bau folgt dem Ticket, nicht dieser Sitzungsnotiz. Historischer
  naechster Schritt dieser Sitzung war: baut der Builder Teil A aus
  `tasks/08-claude-lane-live.md` (Schritte 1 bis 4) auf `stage/08a-claude-live-decision`.
  Abnahme-Befehl fuer Teil A:

  ```bash
  make lint && uv run pytest tests/unit/test_live_config.py -q
  ```

- Danach: `/clear`, neue Session, `/plan-ticket` fuer das naechste Ticket.
