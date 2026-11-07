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
- Die Auftragsseite zeigt auf schmalen Displays drei Zeilen: Eingang, Quittung,
  Ausgang. Die Quittung ist das Deck-Ereignis `task.start`. Sie ist nicht die
  60-Sekunden-Quittung eines n8n-Kanals. Dieser Kanal ist nicht gebaut.
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
die Lease bis `expires_at`. Der Reaper legt einen abgelaufenen Versuch zurück in
die Warteschlange, auf derselben Lane und innerhalb von `max_attempts`
(`app/db/queue.py`, Etappe 10). Ein gespeichertes Ergebnis bleibt je Versuch
eindeutig; eine verspätete Antwort der alten Lease wird abgewiesen.

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
8. Am iPhone die drei Zeilen Eingang, Quittung und Ausgang nach einem echten
   Auftrag gegenlesen. Eine Simulation bleibt eine Simulation.

Der Code allein ist nicht produktionsreif. Die Beobachtung vom 2026-11-07 ersetzt
diese Prüfung nicht.
