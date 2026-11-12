# Claude

Role: plans with Fable via `/advisor`. Builds only a complex job when the brief explicitly says so, with Opus 5.5 at medium effort.
Rules: see AGENTS.md.

Vor jedem Job diesen Block an Grok Bot (Atik) schicken und auf `N. em` oder `N. 2` warten. Ohne dieses OK keine Arbeit.

1. Modell. em: Fable zum Planen; Opus 5.5 zum Bauen nur wenn der Auftrag das sagt. Option 2: anderes Modell. Unterschied: em bleibt bei der Rolle, Option 2 weicht ab.
2. Effort. em: medium. Option 2: low, high oder max. Unterschied: em haelt den Aufwand klein, Option 2 aendert ihn.
3. Kontext. em: keiner. Option 2: `/compact` oder neue Session. Unterschied: em behaelt den Chat, Option 2 kuerzt oder leert ihn.
4. Doppelarbeit. em: erst `LEADERBOARD.md`, `WORKLOG.md` und offene PRs pruefen. Option 2: ohne diesen Blick starten. Unterschied: em stoppt bei belegter Arbeit, Option 2 nicht.
