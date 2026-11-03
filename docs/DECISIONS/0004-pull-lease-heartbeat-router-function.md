# ADR 0004: Pull mit Lease und Heartbeat; Router ist Funktion

## Kontext

Push-basierte Dispatcher erhoehen Angriffsflaeche und brauchen dauerhafte Verbindungen.

## Entscheid

Runner **ziehen** Auftraege per Lease. Heartbeats verlaengern die Lease waehrend `running`. Der **Router** ist eine reine Funktion (kein eigener Dienst), die aus Task-Feldern die effektive Worker-Reihenfolge berechnet.

## Folgen

- Kein zentraler „Dispatcher-Daemon“.
- Lease-Timeouts muessen konsistent mit `timeout_s` und Not-Aus sein.
- Idle Runner pollen die DB — Last ist bei erwarteter Groesse akzeptabel.
