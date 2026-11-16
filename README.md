## v21
- Hotel polls: admin can edit title/description after creation.
- Hotel polls can be locked by admin; participant edit links disappear and locked edit URLs redirect back.
- Internationalised email addresses are accepted (for example åäö in the address).
- Participant hotel view shows the same four summary statistics as admin and current personal cost in Resultat.

v18: Mina omröstningar visar sammanfattning av egna val; hotelldetalj visar allas val; sparade svar låses med Ändra mina val; hotellnätter visar veckodag + ISO-datum.
18: Mina omröstningar visar sammanfattning av egna val; hotelldetalj visar allas val; sparade svar låses med Ändra mina val; hotellnätter visar veckodag + ISO-datum.

# Group Planner v16

Changes in v16:
- Monday-first locale for date pickers.
- ISO date format (YYYY-MM-DD) outside result tables.
- Result tables keep Swedish weekday format such as `Tor 24 sep 2026`.
- Participant result view loads all saved responses explicitly for availability polls.

# Group Planner v15

Group Planner is a small FastAPI + SQLite app for planning dinners and hotel/travel stays.

## v15 changes
- Hotel date-range picker is a Monday-first calendar.
- Admin result tables show Swedish weekday names with dates.
- Hotel admin matrix uses participants as rows and nights as columns, with horizontal scrolling for many participants.
- A participant who has saved a dinner/availability poll can view a read-only table of everybody's answers from their personal event link / Mina omröstningar.
- Participants can never edit another participant's answers from that view.
- App version is shown in the common header and `/health` reports `v15`.

## Run

```bash
docker compose build --no-cache
docker compose up -d
```

Keep `data/group_planner.db` persistent.
