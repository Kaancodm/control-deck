# Sicherer Checkpoint vom 03.11.2026

Die Arbeit ist auf ausdruecklichen Owner-Wunsch pausiert. Alle unten genannten Umsetzungsstaende sind committed und auf GitHub gepusht. Die Arbeitsverzeichnisse sind sauber; es laufen keine lokalen Implementierungs- oder Testprozesse mehr. GitHub-CI kann unabhaengig fertiglaufen. Erst nach neuem GO weiterarbeiten.

Dieser Branch speichert die Uebergabe und den bereits vorbereiteten Abnahme-Entwurf. Die Implementierungen liegen auf den jeweiligen Stage-Branches. Kein Merge, Release, Deployment oder echter Anbieteraufruf wurde durch diese Arbeit ausgefuehrt.

## Ausgangspunkt und Branch-Staende

`main`: `14e7771a23691d17f2a387f1e3163dc38517a576` nach dem extern erfolgten Squash-Merge von PR #23. Die Korrekturen des Etappe-8-Reviews sind enthalten. Etappen 9 und 10 wurden danach ohne Inhaltsaenderung rebased; ihre neue CI ist gruen.

| Etappe | PR / Branch | Gesicherter Head | Nachweis und Stand |
|---|---|---|---|
| 9 | [#24](https://github.com/Kaancodm/control-deck/pull/24), `stage/09-ollama` | `9ec72d636f34250d1b3c4c115125019f697f6b91` | 1264 Tests, Pflichtchecks und finale CI gruen; reviewbereit |
| 10 | [#25](https://github.com/Kaancodm/control-deck/pull/25), `stage/10-error-paths` | `26d3d5ddf1514d8905aa782e2ea754f209aa66eb` | 1455 Tests, Pflichtchecks und finale CI gruen; reviewbereit |
| 11 | [#26](https://github.com/Kaancodm/control-deck/pull/26), `stage/11-ui-chat` | `ea73073609e3884104368d9c795b32d695a799f7` | 1578 Tests, Pflichtchecks und finale CI gruen; reviewbereit |
| 12 | [#27](https://github.com/Kaancodm/control-deck/pull/27), `stage/12-claude` | `4f9a428d3693997ebc650f19bec4065d1ba2335f` | 1714 Tests, Pflichtchecks und finale CI gruen; reviewbereit |
| 13 | [#28](https://github.com/Kaancodm/control-deck/pull/28), `stage/13-codex` | `aa95b47c4b07f46b41adbdfeb79a046cc2766f15` | 1896 Tests und lokale Pflichtchecks gruen; finale CI beim Sichern noch laufend |
| 14 | [#29](https://github.com/Kaancodm/control-deck/pull/29), `stage/14-knowledge` | `e32ef7de61158742adac6b560f22bb55d2bb3487` | 88 gezielte Tests; Lint gruen; finaler Eltern-Rebase und Gesamtchecks offen |
| 15 | [#30](https://github.com/Kaancodm/control-deck/pull/30), `stage/15-hardening` | `d91d6b2b337fee107156f14b878026d109b59b82` | 137 gezielte Tests, Lint und beide Alembic-Checks gruen; UI-Integration und Gesamtchecks offen |
| 16 | [#31](https://github.com/Kaancodm/control-deck/pull/31), `stage/16-operations` | `54798c9e22afaad78f8220516532baa5feabb7e0` | 66 neue Unit-Tests, gezieltes Ruff/Mypy und Offline-systemd-Pruefung gruen; DB-Integration und Gesamtchecks offen |
| 17 | [#32](https://github.com/Kaancodm/control-deck/pull/32), `stage/17-audit-health` | `92a776935c0e9e92b1ec32d4f78843a37d98a48d` | 66 Unit- und 11 SQL-Integrationstests; Zaehler-, UI- und API-Integration sowie Gesamtchecks offen |

Die Gesamtsuiten der Etappen 9 bis 13 hatten keine Skips oder xfail. Gezielte Testzahlen der Etappen 14 bis 17 ersetzen keine Gesamtpruefung. Die Zahlen ueberlappen zwischen Branches und duerfen nicht addiert werden. Alle offenen PRs bleiben Draft; Etappen 14 bis 17 sind nicht reviewbereit.

Aktuelle CI-Links: [Etappe 9](https://github.com/Kaancodm/control-deck/actions/runs/41611957898), [10](https://github.com/Kaancodm/control-deck/actions/runs/41611993392), [11](https://github.com/Kaancodm/control-deck/actions/runs/41613588418), [12](https://github.com/Kaancodm/control-deck/actions/runs/41613793598), [13](https://github.com/Kaancodm/control-deck/actions/runs/41613958483).

## Wiederaufnahme nach neuem GO

Zuerst aktuelle PR-/Merge-Staende und Remote-Heads lesen. Die nachstehenden Grenzen gehoeren zu genau diesem Checkpoint. Bei inzwischen veraenderten Branches erst deren neue Abstammung feststellen. Wegen der bereits umgeschriebenen Elternhistorie nicht blind `merge-base` als Replay-Grenze verwenden.

| Branch | Beim Checkpoint enthaltene alte Eltern-Grenze | Vorgesehene neue Basis |
|---|---|---|
| `stage/14-knowledge` | `fef8c6ee9906a618c62a18dc8b3aeaecfc5df82c` | finaler `stage/13-codex` |
| `stage/15-hardening` | `56998cc199f5f9ef0c03e60add2b93717727d1db` | finaler `stage/14-knowledge` |
| `stage/16-operations` | `7c0157096e574a2fe8fd34fe32cb206303276239` | finaler `stage/15-hardening` |
| `stage/17-audit-health` | `bfd6f6d6a78e3fe3087988ca3cedaaaba6c423c3` | finaler `stage/16-operations` |

Rebase-Muster: `git rebase --onto <neue-Basis> <alte-Eltern-Grenze> <Stage-Branch>`. Nur saubere, zugeordnete Worktrees verwenden und eigene Branches mit explizitem Lease pushen.

1. Finale CI von PR #28 feststellen und PR-Status abgleichen. Etappen 11 bis 13 sind implementiert; es steht kein weiterer Funktionsausbau an. Bei den letzten Eltern-Rebases blieben Anwendung, Konfiguration und Schemas identisch; zwei geaenderte historische Migrationstests wurden separat erneut bestanden.
2. Etappe 14 auf den finalen Etappe-13-Head rebasen. Implementiert sind der ausdrueckliche Testvertrag `deck_kb.v1`, pgvector, lokale Fake-Embeddings, unveraenderliche Kontext-Snapshots, Freigabe-Hashes, idempotenter expliziter Export und UI. Vollstaendige Checks, finale Dokumentation und CI abschliessen.
3. Etappe 15 auf die fertige Etappe 14 rebasen. HTTP-/Bind-/Konfigurationsschutz und Migration `0014` sind vorhanden. UI-Anbindung fuer Passwortbindung, Sitzungsablauf, globale Login-Sperre, Entsperren und Auth-Audit fertigstellen; Lock-Reihenfolge pruefen und Browser-/Gesamtchecks ausfuehren.
4. Etappe 16 auf die fertige Etappe 15 rebasen. Migration `0015`, Betriebsereignisse, Backup-/Restore-/Retention-Code, systemd-Vorlagen und begrenzter Supervisor sind gesichert. Die geschriebenen DB-Integrationstests einschliesslich echter Wegwerf-DB-Restore-Probe ausfuehren, allfaellige Fehler beheben und Gesamtchecks abschliessen.
5. Etappe 17 auf die fertige Etappe 16 rebasen. Sichere Audit-Abfragen und CSV sind vorbereitet. Migration `0016`, Zaehler bei endgueltig angenommenen Ergebnissen, UI-/Health-/Cookie-Anbindung und Gesamtchecks fertigstellen. Provider-Kontingente bleiben unbekannt, wenn kein Nachweis vorliegt.
6. Etappe 18 ausschliesslich als Abnahme-Checkliste vorbereiten. Der gesicherte [Entwurf](ABNAHME_CHECKLISTE_ENTWURF.md) enthaelt offene Pruefpunkte. Keinen Abnahmelauf, keine Versionserhoehung, keinen Tag und keinen Release ausfuehren.

Je fertigem PR Link, Head und eindeutige Testzahl melden. Externes Review erfolgt durch einen anderen Anbieter; keine Agent-Merges. Nach GO darf der Stapel ohne Warten auf Review weiterbearbeitet werden, solange die freigegebenen Grenzen eingehalten werden.

## Tests und unveraenderte Grenzen

- Pflichtchecks: `make lint`, `uv run alembic -x target=test check`, `make test`, `make reference`. Bestehende Fake-CLI-Referenzmodi ebenfalls pruefen.
- Ab Etappe 14 zusaetzlich `uv run alembic -c alembic_kb.ini upgrade head` und `uv run alembic -c alembic_kb.ini check`.
- Zentrale und KB-Tests verwenden getrennte, ausdruecklich konfigurierte Datenbanken mit Namen auf `_test`. Je Worktree eigene Datenbanken, keine gemeinsamen Truncate-Laeufe. `DECK_DATABASE_URL` fuer Tests entfernen; `DECK_TEST_DATABASE_URL` und `DECK_KB_TEST_DATABASE_URL` setzen. Fehlende Integrationstests nicht als bestanden zaehlen.
- PostgreSQL-17-Clients muessen zum Testserver passen. Die Betriebs-CI verwendet dafuer Test-Wrapper zum isolierten Service-Container; lokale Client-Pfade bleiben ausserhalb des Repos.
- `task.v1` und `result.v1` bleiben eingefroren. `data_class` ist Pflicht ohne impliziten Schema-/DB-Default. UI-Vorschlaege ersetzen keine ausdrueckliche Eingabe.
- `local_only` ausschliesslich an die authentifizierte Ollama-Lane. Worker-Identitaet stammt aus getrennten Lane-Tokens; Executor-Lane ist Pflicht. Nur eine Owner-Deadline erzeugt `expired`.
- Ergebnisse sind vor Speicherung bereits lokal wiederhergestellt. UI zeigt den gespeicherten Text escaped; keine zweite Rueckabbildung und keine erfundenen Positionsmarkierungen.
- Nur Repo-Code, Simulationen und Fake-CLIs. Keine echten Logins, Anbieter-/Modellaufrufe, API-Keys, produktiven DB-Verbindungen oder Serveraenderungen. Die Live-Profile bleiben gesperrt; fehlende Tool-, Retry-, Netzwerk- und Reinraum-Nachweise stehen unter **Inbetriebnahme**.
- KB-Aktivierung verlangt ausdrueckliche Simulation und eine Test-DB. Ein fremdes Zielschema, Schreibrechte und echte Embeddings wurden nicht bestaetigt. Keine behauptete Live-Kompatibilitaet.
- Inhaltliche Referenzfaelle schreibt der Owner. Die bestehende Suite bleibt bei vier Faellen; vorbereitete TODOs zaehlen nicht als bestanden. Das spaetere Design wird separat geprueft; zentrale CSS-Variablen und Partials bleiben erhalten.
- Code und Conventional Commits auf Englisch; README und docs auf de-CH ohne Eszett. Bereits dokumentierte Folgepunkte in PRs und `docs/STAND.md` weiterfuehren.
