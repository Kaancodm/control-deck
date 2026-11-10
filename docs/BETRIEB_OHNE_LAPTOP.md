# Betrieb ohne Laptop

Stand des Codes auf diesem Branch. Eine Beobachtung eines laufenden Servers vom
2026-11-07, 23:08 bis 23:10 Europe/Zurich, ist unten als Beobachtung gekennzeichnet.
Sie ist kein dauerhafter Zustand und kein Abnahmeentscheid.

## Was dieser Stand ändert

- Die Versuchsdauer folgt `policy.timeout_s`. Der Standard bleibt 900 Sekunden, die
  Grenze 30 bis 7200. Die frühere stille Adapter-Obergrenze von 600 Sekunden entfällt.
  Tests dürfen ein kürzeres Adapter-Limit setzen. Der Ollama-Leerlauf von 120 und
  danach 60 Sekunden bleibt ein Transportsignal, keine Ergebnisfrist und keine
  Kanal-Quittung.
- Die Auftragsseite zeigt auf schmalen Displays vier Zeilen: Eingang, Kanal,
  Start, Ausgang. Start ist das Deck-Ereignis `task.start` des Versuchs, den die
  Zeilen beschreiben. Kanal ist eine eigene gespeicherte Annahme in die
  Warteschlange, mit Idempotenz und einer Frist von 60 Sekunden. Ohne
  konfigurierten Kanal steht dort «Angenommen, Transport fehlt» oder nach der
  Frist «Frist ohne Zustellung». Das ist keine Zustellung und kein n8n- oder
  Kaan-Transport. Ein laufender neuer Versuch übernimmt nicht den Fehler des
  vorigen Versuchs als eigenes Ergebnis. Ein abgeschlossener späterer Erfolg
  ist als späterer Versuch beschriftet.
- Eine Simulation bleibt als Simulation beschriftet. Ein Live-Versuch wird nur so
  genannt, wenn das gespeicherte Profil `live` ist.
- iPhone Safari ab 320 CSS-Pixel: Eingabefelder mindestens 16 Pixel, kein seitliches
  Scrollen der geprüften Seiten, sichere Ränder am Displayrand.

## Kontogrenze

Beobachtung: API und Ollama-Runner liefen unter dem Dienstkonto. Claude Code und
Codex waren auf einem anderen Betriebssystemkonto installiert und dort angemeldet.
agy war auf diesem zweiten Konto installiert; die Anmeldung war nicht geprüft.
Linger und die Dienste des Dienstkontos waren eingeschaltet. Ein Neustart der
Maschine wurde nicht ausgeführt.

Der Code kopiert keine Sitzungen und ersetzt keine vorhandene Anmeldung. Der
Runner gibt nur eine Allowlist weiter (`app/workers/daemon.py`). `HOME`,
`CLAUDE_CONFIG_DIR`, `CODEX_HOME`, Proxy, API-Keys und das Owner-Token gehören
nicht dazu. Der unterstützte Cloud-Adapter in `app/workers/sandbox.py` setzt
`HOME=/tmp/home`, ein benanntes Profil-Volume, `--network=none` und den Benutzer
`10001`. Er mountet kein Home des Hosts und kennt kein sudo. Claude und Codex
haben in diesem Adapter kein Live-Profil: ohne Simulation meldet der Executor
`worker_unavailable`. Live bleibt auf Ollama und Loopback beschränkt
(`app/workers/live_config.py`).

Nicht tun:

- Zugangsdaten oder CLI-Verzeichnisse zwischen den Konten kopieren.
- pauschales sudo vergeben.
- Eine Lane einschalten, nur weil die Binary auf einem anderen Konto liegt.
- agy live schalten, solange die Anmeldung ungeprüft ist.
- Einen öffentlichen Port oder Tailscale Funnel öffnen.

Eine spätere Lane darf nur als der Betriebssystembenutzer laufen, der die
Anmeldung selbst hält, und nur über diesen Adapter. Das ist hier nicht gebaut.

## iPhone

Beobachtung: die Oberfläche war über HTTPS im Tailnet erreichbar, mit Passwort,
ohne Windows Hello und ohne Funnel. Der Hostname steht absichtlich nicht in diesem
Repo.

Der erste mobile Weg ist Safari auf dem iPhone, Breite ab 320 CSS-Pixel. Titel,
Anweisung und die drei Zeilen des Auftragswegs bleiben ohne Quer-Scrollen lesbar.
Die Anmeldung bleibt das vorhandene Passwort. Dieser Branch ändert keine
Server-Bindung und kein Zertifikat.

## Neustart und Fallback

Aufträge, Leases und Ergebnisse liegen in PostgreSQL. Endet der Runner, bleibt
die Lease bis `expires_at`. Der Reaper schreibt für diesen Versuch genau ein
Ergebnis `worker_unavailable`, wenn der Runner keines hinterlassen hat. Das ist
kein `timeout`: ein Zeitlimit schreibt nur der noch laufende Runner selbst.
Danach gilt die bisherige Lease-Erholung auf derselben Lane und innerhalb von
`max_attempts` (`app/db/queue.py`). Ein gemeldetes `worker_unavailable` bleibt
davon getrennt und darf auf die naechste zulaessige Lane wechseln. Ein Abbruch
bleibt `cancelled`. Eine Owner-Deadline wird `expired` und erfindet kein
Runner-Ergebnis. Eine verspaetete Antwort der alten Lease wird abgewiesen. Es
gibt keine zweite Warteschlange.

Dieselbe Reaper-Runde ist der begrenzte Takt: `run_once` ruft sie vor dem
naechsten Claim auf. Sie setzt faellige Kanal-Quittungen auf `deadline_elapsed`,
hoechstens das Sweep-Limit pro Runde. Eine fehlende Transportkennung wird nie
zugestellt. Die Quittung entsteht bei der Freigabe, nicht beim Eingang in
`awaiting_approval` und nicht bei `task.start`.

Ein stiller Worker, der bis zum Adapter-Limit nichts liefert, wird `timeout` und
geht zur nächsten erlaubten Lane. `local_only` bleibt bei Ollama. Der Test
`test_silent_worker_is_visible_then_falls_back_once` geht vom Formular bis zur
sichtbaren Folge und lässt den stillen Worker genau einmal laufen. Sein
Adapter-Limit ist 0,5 Sekunden, damit der Lauf kurz bleibt. Der Produktionsstandard
bleibt 900 Sekunden.

Ein Reboot des Servers ist nicht geprüft. Der vorhandene Test
`test_stop_cancels_child_without_a_database_mutation` in
`tests/integration/test_runner_service_stop.py` deckt den Prozessstopp in der
Testdatenbank ab, nicht die Maschine.

## Sicherung

Beobachtung: ein lokaler Datenbank- und Restic-Satz sowie ein verschlüsselter
Offsite-Schnappschuss waren vorhanden. Ob sich der Offsite-Satz wiederherstellen
lässt und ob der Wiederherstellungsschlüssel greifbar ist, wurde nicht geprüft.
Dieser Branch stellt nichts wieder her und löscht nichts.

## Voraussetzungen für die koordinierte Inbetriebnahme

1. Diff auf dem Server nur nach GO einspielen. Dieser Branch wurde dort nicht
   ausgerollt.
2. Die beiden Betriebssystemkonten getrennt lassen.
3. Claude, Codex und agy nicht live schalten.
4. Ollama weiter nur Loopback, ohne neues Modell aus diesem Stand.
5. Tailnet-only belassen, kein Funnel, kein zusätzlicher öffentlicher Port.
6. Einen Reboot und die Lease-Erholung danach bewusst prüfen.
7. Offsite-Restore und den Wiederherstellungsschlüssel prüfen, bevor der Satz
   als Rettung gilt.
8. Am iPhone die Zeilen Eingang, Kanal, Start und Ausgang nach einem echten
   Auftrag gegenlesen. Eine Simulation bleibt eine Simulation. Ohne Kanal darf
   die Zeile Kanal nicht «Zugestellt» sagen.

Der Code allein ist nicht produktionsreif. Die Beobachtung vom 2026-11-07 ersetzt
diese Prüfung nicht.

## Nachprüfung 2026-11-08

Geprüft wurde dieser Branch gegen `origin/main`
`0ec4f488bf480627dc685efbc1c605e677092fbe`. Der Stand davor war `5a1b252`.
Die Prüfung lief in einer wegwerfbaren Testdatenbank. Sie ist Offline-Vorbereitung
und kein Live-Nachweis.

Testlauf am 2026-11-08, nur gegen eine Datenbank mit Namen auf `_test`:

- `make lint`: bestanden (ruff check, ruff format --check, mypy app/).
- `make test`: 2887 bestanden, 94 übersprungen, 1 Warnung. Die Sprünge sind die
  bestehenden Fälle ohne Wissensdatenbank-URL und ohne Restore-Probe.
- `bash tests/hooks/test_guard.sh`: 41 Fälle bestanden.
- `test_silent_worker_is_visible_then_falls_back_once` und
  `tests/integration/test_ui_browser.py` sind in diesem Lauf enthalten. Der
  Browserlauf ist headless Chromium, kein physisches iPhone.

In diesem Lauf korrigiert:

- Die drei Zeilen haben einen älteren Fehlversuch als Ergebnis gezeigt, sobald
  irgendein `task.start` vorhanden war. Quittung und Ausgang gelten jetzt für den
  offenen Versuch. Der ältere Fehler bleibt als «Vorheriger Versuch» sichtbar.
  Ein Erfolg ab Versuch 2 trägt «Späterer Versuch». Die Herkunftsbeschriftung
  gehört zum gezeigten Ergebnis, nicht zu einem anderen Versuch.
- `app/workers/daemon.py` hat den Runner-Prozess standardmässig nach 3600 Sekunden
  beendet. `policy.timeout_s` darf bis 7200 Sekunden gehen. Der Supervisor wartet
  jetzt bis zu dieser Schema-Grenze. Der Versuch endet weiter bei
  `policy.timeout_s`. Ein unvollständiger Ollama-Strom wird als Fehler verworfen
  und nicht als gekürzter Erfolg gespeichert; das zeigen die bestehenden
  Stub-Tests `test_incomplete_or_malformed_stream_discards_partial_output` und
  `test_first_chunk_and_following_idle_budgets`. Das ist kein Live-Modelllauf.

Belegt, nur offline oder simuliert:

- Formular, stille Simulation, sichtbares Zeitlimit, genau ein Ollama-Lauf,
  danach beschriftete Simulation bis `review`.
- Chromium headless bei 320 und 1024 CSS-Pixel: kein Quer-Scrollen, Eingabe
  mindestens 16 Pixel, Auftragsweg sichtbar. Das ist nicht Safari auf einem
  physischen iPhone.
- Die Runner-Allowlist lässt `HOME`, CLI-Verzeichnisse, sudo und SSH fallen.
  Der Cleanroom-Befehl setzt `HOME=/tmp/home` und enthält kein sudo. Beides sind
  Tests des Befehls und der Umgebungsauswahl, kein laufender Container und keine
  Prüfung einer vorhandenen Anmeldung.

Nicht belegt und nicht als erledigt zu werten:

- Kein Ausrollen, kein Merge, kein Zugriff auf den Produktionsserver.
- Keine Live-CLI, kein Live-Ollama, kein agy. Vorhandene Anmeldungen bleiben
  unangetastet.
- Reboot der Maschine nicht geprüft. Der Prozessstopp in der Testdatenbank bleibt
  `test_stop_cancels_child_without_a_database_mutation`.
- Offsite-Restore und Wiederherstellungsschlüssel nicht geprüft.
- Das HARPA-Fragenpaket ist nicht begonnen. Die Kanalzeile speichert die
  Annahme und die 60-Sekunden-Frist. Fehlender Transport bleibt ausdrücklich
  fehlend. Der Vertragsentwurf fuer einen spaeteren Routine-Wake steht in
  [TRANSPORT.md](TRANSPORT.md). Er ist standardmaessig aus, sendet nichts und
  legt keine Routine an. HTTP 200 ist dort nur ein gestarteter Lauf.
- Ein von aussen beendeter Runner bekommt ein Ergebnis `worker_unavailable`
  für genau diesen Versuch. Das ist kein Zeitlimit. Die Lease-Erholung bleibt
  auf derselben Lane. Ein physisches iPhone und ein Reboot der Maschine sind
  damit nicht geprüft.
