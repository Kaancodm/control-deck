# ADR 0001: PostgreSQL als Queue mit SKIP LOCKED

## Kontext

Control Deck braucht zuverlaessiges, konkurrenzsicheres Ziehen von Auftraegen durch mehrere Runner-Lanes ohne externen Message-Broker.

## Entscheid

Auftraege und Zustaende liegen in PostgreSQL. Runner holen Arbeit mit `SELECT … FOR UPDATE SKIP LOCKED` und setzen Lease-Felder. Es wird **kein** separater Broker (Redis, RabbitMQ, …) eingefuehrt.

## Folgen

- Einfachere Betriebsfuehrung (eine Datenbank, Backups, Monitoring).
- Transaktionale Konsistenz zwischen Task, Lease und Events.
- Skalierung primaer vertikal und ueber wenige Runner; sehr hohe Parallelitaet ist nicht Ziel.
- Queue-Logik muss sauber indexiert und getestet werden (spaetere Etappe).
