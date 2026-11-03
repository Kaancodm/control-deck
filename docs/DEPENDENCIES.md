# Abhaengigkeiten

Stand: Nachbesserung der Etappen 14 bis 17. Verbindliche direkte Anforderungen stehen in
`pyproject.toml`; `uv.lock` legt die aufgeloesten direkten und transitiven Versionen fest.
`uv sync --frozen --all-extras` installiert den geprueften Entwicklungsstand. Die beiden
vorhandenen Dev-Gruppen beschreiben dieselben Werkzeuge, keine zweite Laufzeitumgebung.

## Python-Laufzeit

| Paket | Mindestversion | Zweck |
|---|---|---|
| Python | 3.12 | Laufzeit, Typisierung und begrenzte Unterprozesse |
| FastAPI | 0.115 | HTTP-API und Request-Validierung |
| Uvicorn | 0.34 | ASGI-Prozess fuer API und lokale Browser-Tests |
| Pydantic | 2.0 | Eingefrorene Task-/Resultatvertraege und Konfigurationsvalidierung |
| SQLAlchemy | 2.0.36 | Transaktionen, Abfragen und DB-Sperren |
| psycopg mit Binary-Extra | 3.2 | PostgreSQL-Treiber |
| Alembic | 1.14 | Zentrale und getrennte Wissens-Migrationen |
| jsonschema mit format-nongpl-Extra | 4.0 | Schema- und Acceptance-Pruefung; Auswertung in begrenzten Prozessen |
| PyYAML | 6.0 | Lokale Routing- und Referenzkonfiguration |
| Jinja2 | 3.1.6 | Serverseitig gerenderte und escapte Owner-Oberflaeche |
| python-multipart | 0.0.20 | Formularverarbeitung der Owner-Oberflaeche |
| argon2-cffi | 23.1 | Lokale Passwort-Hash-Pruefung |

## Entwicklung und Build

| Werkzeug | Mindestversion | Zweck |
|---|---|---|
| pytest | 8.0 | Unit-, SQL-, HTTP- und Browser-Tests |
| httpx | 0.27 | Isolierte HTTP-Testclients |
| Ruff | 0.8 | Lint und Format |
| Mypy | 1.13 | Statische Typpruefung |
| types-jsonschema, types-PyYAML | 4.0, 6.0 | Typinformationen fuer die vorhandenen Bibliotheken |
| Playwright | 1.51 | Chromium-Tests, auch ohne JavaScript |
| Hatchling | 1.13 | Build-Backend; keine Anwendungsfunktion |
| uv | gemaess CI/Entwicklungsumgebung | Lockdatei, Installation und Befehlsausfuehrung |

Die exakten Chromium-/Playwright-Binaries sind separat zu installieren. Die CI installiert
Chromium mit dem gelockten Playwright-Paket; die Tests erlauben nur die isolierte lokale App.

## Lokale Datenbank und weitere Werkzeuge

- PostgreSQL 17 fuer Integrationsnachweise. `pg_dump` und `pg_restore` muessen zum Server
  passen; die Test-Wrapper verwenden die Clients im isolierten Testcontainer.
- pgvector ist eine PostgreSQL-Erweiterung des `deck_kb.v1`-Testvertrags. Die Wissens-DB
  ist eine eigene Datenbank mit eigener Alembic-Kette und Fake-Embeddings.
- HTMX 2.0.8 liegt lokal unter `app/ui/static/vendor/`. Herkunft, SHA-256 und BSD-2-Clause-
  Lizenz stehen in der benachbarten `VENDOR.md` und `LICENSE`; es gibt keinen CDN-Aufruf.
- systemd und Restic gehoeren zu den Betriebswerkzeugen ab Etappe 16. Repo-Tests pruefen
  Vorlagen und begrenzte Fake-Kommandos; Installation, Ziele und reale Wiederherstellung
  gehoeren zur **Inbetriebnahme**.
- Ollama-, Claude- und Codex-Kommandos werden in Repo-Tests durch Fake-CLIs simuliert.
  Kompatibilitaet echter Versionen, Anmeldung und Isolation bleiben **Inbetriebnahme**.

## Aenderungen und Nachweis

Neue direkte Abhaengigkeiten benoetigen eine konkrete Begruendung im PR sowie eine
aktualisierte Lockdatei. Vor Abschluss gelten `make lint`, Alembic-Checks, `make test`
und `make reference`. Dieser Katalog ist keine externe Lizenz- oder Sicherheitspruefung.
Die Nachbesserung fuehrt keine neue Python-Abhaengigkeit ein.
