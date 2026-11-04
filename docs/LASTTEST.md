# Lasttest der Queue (Etappe 18, Entscheid D2)

`scripts/loadtest.py` prueft die Parallelitaets-Invarianten der PostgreSQL-Queue mit
echten, getrennten Runner-Prozessen gegen den mitgelieferten Simulations-Stub.
Es ist ein Repo-Pruefwerkzeug, kein Live-Nachweis und keine Modellqualitaetsmessung.

## Aufruf

```bash
make loadtest                                  # 50 Auftraege, 3 Runner, Lane ollama
make loadtest ARGS="--worker claude"           # 50 Auftraege, 3 Runner, Lane claude
uv run python scripts/loadtest.py --tasks 50 --runners 3 --worker ollama
```

Grenzen: `--tasks` 1 bis 500, `--runners` 1 bis 8, `--timeout-s` 10 bis 1800.
Die Lane `codex` ist bewusst ausgeschlossen (kein aktives Konto); zulaessig sind
`ollama` und `claude`.

## Sicherheitsgrenzen

- Ausschliesslich `DECK_TEST_DATABASE_URL`; der Datenbankname muss auf `_test` enden.
  Ohne diese Variable oder mit falschem Namen bricht das Skript vor jedem Zugriff ab.
- Der Executor ist die deterministische Simulation (`SimulationExecutor`). Kein
  Anbieterprozess, kein Modell-Download, kein Netzwerkaufruf.
- Das Skript bringt eine eigene temporaere Routing-Konfiguration mit (ollama und
  claude aktiv, codex deaktiviert) sowie eine synthetische externe Blockliste fuer
  die Cloud-Datenklasse `cloud_ok`. Produktive Konfiguration bleibt unberuehrt.
- Aufgaben sind synthetisch (`requested_by: system`, Label `suite=loadtest`) und
  werden nach der Freigabe regulaer durch Queue, Datenschranke und Acceptance geleitet.

## Gepruefte Invarianten

| Pruefung | Erwartung |
|---|---|
| `tasks_finished` | Alle Auftraege erreichen `review` oder `done` |
| `single_attempt_per_task` | Jeder Auftrag braucht genau einen Versuch |
| `one_result_per_attempt` | Genau ein `results`-Eintrag je Versuch, keine Duplikate |
| `one_acquire_per_attempt` | Genau ein `task.acquire`-Ereignis je Versuch, keine doppelten Claims |
| `no_hanging_leases` | Keine Lease-Zeile bleibt nach dem Lauf bestehen |
| `runners_clean_exit` | Alle Runner-Prozesse enden sauber, kein Timeout |

Die letzte Ausgabezeile ist eine JSON-Zusammenfassung mit `status`, Zaehlern und
Dauer. Exit-Code 0 bedeutet alle Pruefungen bestanden; 1 eine fehlgeschlagene
Invariante; 2 einen Konfigurations- oder Argumentfehler.

## Einbindung

- `make loadtest` fuehrt den Volllauf gemaess Spec 18 (D2) aus.
- `tests/integration/test_loadtest.py` fuehrt das Skript mit kleinen Parametern
  (6 Auftraege, 3 Runner) auf beiden Lanes in der normalen Testsuite aus und
  verifiziert die Zaehler unabhaengig in der Datenbank. CI deckt den Lasttest damit
  bei jedem Lauf ab; ohne Test-Datenbank wird der Integrationstest uebersprungen.
- Die Einbindung in ein spaeteres `make test-server` bleibt Teil der offenen
  Etappe 18; dieser Lauf ersetzt keinen Abnahme- oder Inbetriebnahmenachweis.
