# Group Planner – v2

A small self-hosted group planning app for:

- Dinner/time polls: an admin creates candidate date/time slots and participants mark availability.
- Hotel/trip polls: an admin creates candidate hotel nights and participants select the nights they want.
- Hotel cost allocation: enter the total stay cost and calculate each participant's cost based on selected person-nights, or split equally per person.

## Stack

- Python 3.12
- FastAPI
- SQLite + SQLAlchemy
- Jinja2
- Vanilla HTML/CSS/JavaScript
- Docker / Docker Compose

## Run locally

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Open http://127.0.0.1:8000

## Run with Docker

```bash
docker compose up --build
```

Open http://127.0.0.1:8000

SQLite is stored in `./data/group_planner.db`.

## How it works

1. Open `/admin/new`.
2. Choose `Dinner / availability` or `Hotel / trip`.
3. Add candidate slots/nights.
4. Create the poll.
5. Copy the participant URL and send it to the group.
6. Use the admin URL to see the results.

Admin and participant URLs contain random tokens, so there is no user account system in this MVP.

## Hotel cost calculation

For a hotel poll, the admin can enter the total hotel cost.

Two calculation modes are supported:

- `per_night`: total cost / total selected person-nights
- `equal_person`: total cost / number of participants

Example:

If the hotel costs SEK 24,000 and there are 73 selected person-nights:

`24,000 / 73 = 328.77 SEK per person-night`

A participant selecting 5 nights therefore pays `1,643.84 SEK`.

## Production notes

This is deliberately a small MVP. Before exposing it publicly, consider:

- HTTPS
- authentication for admin users
- CSRF protection
- rate limiting
- backups
- PostgreSQL instead of SQLite if usage grows
- email/invitation integration
- audit logging


## v2 fixes

- Fixed the admin page `request is undefined` error.
- Fixed participant answer handling so each date/time/night has its own form field.
- Hotel checkboxes now map correctly to their dates.
- Added a small SVG favicon.
