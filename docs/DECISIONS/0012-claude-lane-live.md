# ADR 0012: Live-Profil der Lane claude

## Kontext

Etappe 12 kennt fuer die Lane `claude` nur die Profile `disabled` und `simulation`.
`load_live_config` weist `claude` und `codex` mit `live_lane_not_supported` ab.
ADR 0009 haelt dazu fest: Claude und Codex bleiben gesperrt. Diesen Satz loest dieser
ADR fuer die Lane `claude` ab. Die Lane `codex` bleibt gesperrt.

Der Owner hat am 2026-11-11 die Empfehlungen E1 bis E6 mit «Ja zu allen» beantwortet
(Quelle: Owner-Aussage in der Planungssitzung; Methode: Owner-Aussage; Status: `unproven`
im Repo). Teil A setzt davon nur den Entscheid und die Konfigurationspruefung um. Es gibt
keinen Transport, keinen Live-Executor und keinen laufenden Runner, der sich dadurch
aendert. Tests und CI starten weder die echte CLI noch Docker und rufen keinen Anbieter auf.

Drei bestehende Regeln stehen in Spannung:

- ADR 0006: Cloud-Worker nur ueber die offizielle CLI, Anmeldung im CLI-Profil, keine
  API-Keys.
- ADR 0002: keine dauerhaften Secrets im Container.
- [LANES.md](../LANES.md) (Inbetriebnahme Claude): kein Hostprofil-Mount, kein API-Key,
  kein Zugangswert in Repo, `.env`, Logs oder PR.

## Entscheid

E1. Zusaetzlich zu `DECK_LIVE_ENABLED` gibt es den eigenen Schalter
`DECK_CLAUDE_LIVE_ENABLED`. Beide Werte muessen exakt `1` oder `true` sein, mit derselben
Pruefung wie `live_enabled`. Das Ollama-Flag allein schaltet die Lane `claude` nie ein,
und der Claude-Schalter allein auch nicht. Der Standard bleibt aus.
`load_live_config` bleibt unveraendert und weist `claude` und `codex` weiter mit
`live_lane_not_supported` ab. `load_claude_live_config` prueft in dieser Reihenfolge und
nennt nur den jeweiligen Code, nie den gelesenen Wert:

1. `DECK_LIVE_ENABLED` fehlt oder ist kein exakter Schalterwert: `live_disabled`.
2. `DECK_CLAUDE_LIVE_ENABLED` fehlt oder ist kein exakter Schalterwert:
   `claude_live_disabled`.
3. `DECK_CLAUDE_IMAGE` ist nicht exakt `deck-lane-claude@sha256:` plus 64 Hex-Zeichen
   `0-9a-f`: `claude_image_invalid`. Jeder andere Name ist ungueltig, auch das
   Platzhalter-Image der Simulation.
4. `DECK_DOCKER_HOST` ist keine absolute `unix://`-Adresse: `docker_host_invalid`.
   Angenommen wird nur das Schema `unix`, ohne Hostanteil, Query oder Fragment. Der Pfad
   ist absolut, besteht aus Segmenten `[A-Za-z0-9._-]`, enthaelt weder leere Segmente noch
   `.` oder `..`, und die ganze Adresse ist hoechstens 200 Zeichen. TCP und jede andere
   Form sind ungueltig.

E2. Der spaetere Ausgangsweg ist ein internes Docker-Netz plus ein kleiner
HTTP-CONNECT-Proxy. Der Proxy lauscht nur in diesem internen Netz, laesst nur Port 443 zu
exakt gelisteten Anbieter-Hosts durch und lehnt IP-Literale sowie private Zieladressen ab.
Einrichtung und Pin liegen beim Owner oder seinem Betriebs-Bot, nicht bei diesem Auftrag.
Die Standard-Bridge ist keine Grenze. Die wirksame Hostliste steht nur in der privaten
Proxy-Konfiguration am Betriebsort. Kandidaten dafuer nennt die Herstellerdoku
[network-config](https://code.claude.com/docs/en/network-config) (gelesen 2026-11-11;
Methode: Herstellerdoku gelesen; Status: `proven` fuer die Doku, `unproven` fuer die
installierte CLI). Diese Liste steht nicht im Repo. `deck-claude-egress`,
`deck-egress-proxy`, `deck-claude-profile` und `deck-lane-claude` sind Docker-Bezeichner,
keine Hostnamen der Infrastruktur.

E3. Die Spannung aus ADR 0006, ADR 0002 und dem Verbot des Hostprofil-Mounts loest sich so:
Die Abo-Anmeldung liegt nur im Volume `deck-claude-profile`. Gemountet wird dieses Volume
nur vom Reinraum der Lane `claude` und vom einmaligen Anmeldeschritt des Owners. Es gibt
keinen Hostprofil-Mount und keinen API-Key. Das Image und das Container-Dateisystem
enthalten keine dauerhaften Secrets; die Anmeldung wird nicht ins Image und nicht zwischen
Konten kopiert. ADR 0006 bleibt bestehen: nur die offizielle CLI (`claude -p`), keine
API-Keys. `--bare` bleibt verboten.

E4. Der Claude-Runner laeuft unter dem Dienstkonto, das bereits API und Ollama-Runner
betreibt. Der Owner meldet sich dort einmal neu im Volume an. Das zweite Konto mit der
vorhandenen CLI-Anmeldung bleibt unberuehrt; nichts wird kopiert. Voraussetzung ist ein
rootless Docker-Daemon dieses Dienstkontos. Dieser ADR richtet kein Konto ein und aendert
keines.

E5. `lanes.claude.models` enthaelt genau einen verschiedenen Wert, und dieser ist eine volle
Modell-ID mit Versionsteil. Kurznamen ohne Versionsteil sind ungueltig. Die Erzwingung im
Code folgt in einem spaeteren Teil. Gewaehlt wird nicht die staerkste Klasse.
Zusatzabrechnung bleibt aus; das ist ein Owner-Nachweis und in diesem Teil nicht gefuehrt.

E6. [REVIEW.md](../../REVIEW.md) Abschnitt 1 erhaelt eine enge Ausnahme: der
Reinraum-Starter in `app/workers/sandbox.py` darf den lokalen Docker-Client mit dem festen
Vertrag aufrufen. Alles andere in Abschnitt 1 bleibt. Teil A ruft Docker nicht auf. Die
Ausnahme ist die Voraussetzung dafuer, dass ein spaeterer Starter ueberhaupt reviewbar ist.

## Folgen

- Laufende Runner verhalten sich nach Teil A wie vorher. Ohne beide Schalter gibt es keinen
  Live-Pfad fuer `claude`.
- `codex` bleibt bei `live_lane_not_supported`.
- Keine Migration, kein Schemawechsel, keine neue Abhaengigkeit, kein neuer Endpunkt.
- Fehlercodes und Logs dieser Pruefung enthalten weder Prompt noch Antwort noch den
  abgewiesenen Konfigurationswert.
- Image-Bau, Rootless-Nachweis, Netzgrenze, Abo-Anmeldung und der erste Canary bleiben
  Owner-Nachweise am Betriebsort und sind mit diesem ADR nicht erledigt.

## Status

Entschieden am 2026-11-11. Umgesetzt in Teil A ist die Konfigurationspruefung. Starter,
Executor, Verdrahtung und Canary sind eigene Teile und hier nicht freigegeben.
