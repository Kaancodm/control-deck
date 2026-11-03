# Etappe 12 - Claude-CLI und Reinraum-Vertrag

Branch: `stage/12-claude`, gestapelt auf Etappe 11. Der Owner hat die gestapelte
Repo-Umsetzung mit Simulationen freigegeben; ein vorgaengiger Merge ist nicht erforderlich.

## Umfang

- `ClaudeExecutor` mit ausdruecklicher Lane-Identitaet und Offline-Profil.
- Versionierter CLI-Vertrag, begrenzter Containerbefehl und gepinnte Image-Vorlage;
  keine echten Container oder Anbieterprozesse in Tests.
- Fake-Container und Fake-CLI: Rootless-/Versionsvoraussetzungen, exakte stdin-Bytes,
  JSON-Ergebnisse, Auth-/Policy-/Rate-Limit-Fehler, Timeout, Abbruch und Cleanup.
- Gemeinsamer Host-Runner fuer Claim, Owner-Freigabe, Datenschutz, lokale Rueckabbildung,
  Acceptance und erneutes Lease-/Deadline-Fencing. Fehlerreaktionen aus Etappe 10.
- Runner-Schalter `--executor claude-fake-cli`, sichere Gesundheitsgruende und Logs.
  `simulation_ready` belegt keinen echten Modelllauf.

`data_class` bleibt Pflicht; `local_only` erreicht nur Ollama. Die v1-Schemas bleiben
unveraendert. Textausgaben haben `output.files=[]`; erzeugter Code wird nicht ausgefuehrt.
Inhaltliche Referenzfaelle stammen ausschliesslich vom Owner.

## Lokale Pruefung

Gezielte Unit-/Integrationstests pruefen den simulierten Weg von der Freigabe bis `review`,
Redaction, keine offenen DB-Transaktionen bei Ausfuehrung, Abbruch/Deadline/Lease-Verlust,
Fehlerklassifikation und unabhaengiges Cleanup. Pflichtchecks: `make lint`,
`uv run alembic -x target=test check`, `make test`, `make reference`.
Zahlen und CI stehen im Draft-PR und in [STAND.md](../STAND.md).

## Inbetriebnahme

Das Live-Profil bleibt gesperrt. Die Image-Vorlage wurde nicht gebaut; kein echtes CLI
wurde gestartet oder angemeldet. Rootless-Isolation, Host-/DB-Netzsperren, technische
Werkzeugabschaltung, Konto-/Abo-Verhalten, Cleanup und reale Antworten bleiben separat
nachzuweisen. Standard-Bridge-Netz schliesst Host-Zugriff nicht aus. `--bare` ist wegen
fehlender Abo-Anmeldung kein geeigneter Ersatz fuer diese Grenzen.

Befehle, Quellen und offene Nachweise: [LANES.md](../LANES.md).
Keine API-Keys, Secrets, echten Logins, Serveraenderungen oder Self-Merges.
