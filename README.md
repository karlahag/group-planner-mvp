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


## v3 UI improvements

- Responsive mobile-first interface.
- Calendar-style selection for hotel nights.
- Cleaner admin dashboard with summary cards.
- Clearer cost allocation presentation.
- Improved participant form.


## v4
Explicitly passes FastAPI Request into admin and participant Jinja templates.
Use `/health` to verify the running container is v4.


## v5 fixes

- Participant poll-token lookup is explicit and robust.
- Participant URL is constructed once on the server and passed to the admin template.
- Copy-link works on normal HTTP/LAN setups where `navigator.clipboard` is unavailable, using a fallback.
- The copy button gives visible feedback.
- `/health` returns version `v5`.


## v6

Participant links use PUBLIC_BASE_URL so Docker/Uvicorn never publishes 0.0.0.0.

Set it in `.env`, for example `PUBLIC_BASE_URL=http://192.168.1.100:8000`, then run `docker compose up --build`.

`/health` should report version `v6`.


## v7 fix

Fixed the participant-token redirect bug.

After the first save, the app creates a personal `Participant.token` and redirects to it. The GET `/p/<token>` handler now recognizes both the shared poll participant token and the personal participant token, so saved answers remain accessible.

Verify with `/health` and expect version `v7`.


## v9

- Adminsida `/admin` visar alla omröstningar med admin-/deltagarlänkar och möjlighet att radera.
- Deltagarnamn är unika inom en omröstning (case-insensitive och normaliserade).
- Personligt deltagartoken kan användas för att ändra tidigare svar utan att skapa en ny deltagare.
- Om samma namn används från den gemensamma deltagarlänken uppdateras den befintliga deltagaren istället för att skapa en dubblett.


## v9 changes
- Hotel/resa uses startdatum + stoppdatum (utcheckning) instead of individual date rows.
- All nights between the dates are generated automatically; the end date itself is not a hotel night.
- Creation timestamps in the admin list are displayed in the browser's local timezone.

## Admin login (v14)

Admin access is now protected by one global password configured with `ADMIN_PASSWORD` in `.env`.

Example `.env`:

```env
PUBLIC_BASE_URL=http://localhost:8000
ADMIN_PASSWORD=choose-a-password
```

Open `http://localhost:8000/admin` and log in with that password. Existing per-poll admin links continue to work and bootstrap the admin session.

Participants do not see the admin navigation or the list of polls.


## v14 – participant identity
- Participant email is required.
- Email is normalized and used as the unique participant identifier within each poll.
- Names are display names only; duplicate names are allowed.
- `/mine` lets a participant enter their email and see only their own participation records.
- No email messages, magic links, or passwords are used for participants.
- Personal participant tokens remain the actual edit links.
- Existing SQLite databases are migrated automatically by adding the `participants.email` column.


## v14
- Admin hotellmatris: hover over participant name to see email address.
- Email remains hidden from the main table unless the admin hovers over a participant name.


## v14 – hotel admin matrix
- Hotel results use nights as rows and participants as columns.
- The matrix scrolls horizontally when there are many participants.
- Participant names remain visible in the header while scrolling vertically.
- Admins can hover participant names to see the email address.
