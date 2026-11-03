# Etappe 13 - Codex-CLI als gepruefte Simulation

Status: Implementierung im gestapelten Draft-PR auf Etappe 12. Finale Pflichtchecks folgen
nach dem Rebase auf den fertigen Vorgaenger. Kein Merge durch Agenten.

## Lieferumfang

- `CodexExecutor` mit expliziter Lane-Identitaet, konfigurierten Modellkennungen und
  standardmaessig gesperrtem Live-Profil.
- Strenger JSONL-Parser fuer die gepinnte offizielle CLI 0.166.0; genau ein abgeschlossener
  Text-Turn, gepruefte Tokenzaehler, sichere Fehlercodes und keine Teil-/Tool-Ausgaben.
- Eigene Fake-CLI im gemeinsamen Reinraum-Simulator aus Etappe 12. Getrenntes Codex-Profil,
  ausdrueckliche Prozessumgebung, exakte Payload-Bytes auf stdin und unabhaengige Bereinigung.
- Host-Integration fuer Freigabe, Datenschranke, Redaktion, Heartbeats, Abbruch, Deadline,
  Rueckabbildung, Acceptance und zentrale Retry-Regeln.
- Lane-Gesundheit und ausdruecklicher Selektor `--executor codex-fake-cli`.
- Gepinntes Build-Rezept unter `docker/lane-codex/`; keine Installation und kein Image-Build.

## Verbindliche Grenzen

Der freigegebene Gesamtplan und das spaetere Owner-GO ersetzen die frueheren
`workspace-write`- und Datei-Ausfuehrungsannahmen dieses Tickets. Auftraege liefern Text;
`output.files=[]`. Erzeugter Code wird nicht ausgefuehrt. `task.v1` und `result.v1` bleiben
unveraendert; `data_class` bleibt Pflicht. Keine API-Keys, echten Logins, Provideraufrufe,
Serveraenderungen oder neuen inhaltlichen Referenzfaelle.

## Pruefungen

Unit- und Integrationstests pruefen Protokollfehler, volle Prozess-Lebenszyklen, Fake-Auth-
und Policy-Fehler, fehlende Modelle, Payload-Identitaet, local-only-Ausschluss, lokale
Rueckabbildung, Timeout, Abbruch, Lease-Verlust, Logs ohne Rohwerte und korrekte Retry-Uebergabe.
Die Pflichtchecks sind `make lint`, `uv run alembic -x target=test check`, `make test` und
`make reference`. Finale Testzahl und CI-Nachweis stehen im PR und in `docs/STAND.md`.

## Inbetriebnahme

Die echte CLI bleibt nicht verfuegbar. Der gepinnte Quellstand beweist keine vollstaendige
Tool-Sperre; eingebaute Provider-Retries lassen sich mit den naheliegenden Provider-Overrides
nicht abschalten. Rootless Isolation, Profil-/Subscription-Modus, Provider-Netzgrenzen,
Tool-Deaktivierung, fehlende interne Retries und Image-Build sind separat zu pruefen.
Simulationserfolg ist kein Live- oder Modellqualitaetsnachweis. Details und Quellen:
[LANES.md](../LANES.md).

Externes Review durch einen anderen Anbieter und Owner-Merge bleiben ausstehend.
