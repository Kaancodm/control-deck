# HANDOFF

Stand: 2026-11-11. Uebergabe aus der Planungssession «Claude-Lane live».

## Stand

- Branch `claude/fervent-pasteur-upzu9m`, letzter Commit vor dieser Datei `4e32a02`.
- Draft-PR #84 «docs(tasks): plan build ticket 08 for the Claude lane live profile» gegen
  `main`. CI beim Schreiben: `quality` lief noch, GitGuardian gruen, CodeRabbit ueberspringt
  Drafts.
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
- Owner-Vorgaben vom 2026-11-11 (Owner-Aussage, im Repo nicht belegt): Planer Claude,
  Builder Cursor, Review Grok. Codex und Gemini Pro sind nicht mehr im Team. Ein Betriebs-Bot
  verwaltet Router, Chat-Oberflaeche und lokale Modelle am Betriebsort. Die Benutzerkonten
  dort bleiben unveraendert. Altprojekte werden nicht eingespeist.

## Offene Punkte

- E1 bis E6 sind nicht beantwortet. Ohne sie beginnt Teil A nicht. E4 (unter welchem Konto
  der Claude-Runner laeuft) ist die heikelste Frage.
- Hermes und n8n: Der Installationszustand am Betriebsort ist unbekannt. Der Owner fuehrt
  zuerst einen lesenden Statusbefehl aus; erst danach folgen Einschaltbefehle.
- Vorschlag, nicht beauftragt: ein kleiner Bauauftrag «Anlege-Token», bevor Hermes oder n8n
  Zugriff auf die API erhalten.
- Schutzpunkte fuer den Betrieb: Das Deck verlangt exakt die gepinnte Ollama-Version
  (ADR 0009). Das Embedding-Modell der Wissens-DB (ADR 0011) und die Modelle aus der
  Router-Datei duerfen nicht geloescht werden.
- Veraltet im Repo und nicht angefasst: Statusspalte in `docs/ETAPPEN/README.md`, Kopf von
  `tasks/04-a-kb-runtime-migration.md` (mit #59 gemergt), `CLAUDE.md` nennt fuer die CI
  `postgres:17`, der Workflow nutzt ein pgvector-Image.
- ADR 0008 und `AGENTS.md` nennen Codex in der Review-Rotation. Ein Nachtrag ist ein
  Owner-Entscheid.
- Bewusst nicht gemacht: kein Server-Zugriff, kein Docker, keine echte CLI, kein Merge.
- Diese Datei liegt auf Wunsch des Owners («abspeichern») im Branch von PR #84. Ob sie im
  PR bleibt, entscheidet der Owner vor dem Merge.

## Naechster Schritt

- Der Owner beantwortet E1 bis E6 im Abschnitt «Offene Fragen» von
  `tasks/08-claude-lane-live.md` und gibt GO oder einen Aenderungswunsch zu PR #84.
  Abnahme-Befehl auf dem Branch:

  ```bash
  make lint
  ```

- Danach: `/clear`, neue Session, Teil A bauen (`stage/08a-claude-live-decision`), und
  `/plan-ticket` fuer das naechste Ticket.
