from pathlib import Path
import os
from secrets import token_urlsafe
import hmac
import hashlib
from collections import defaultdict
from decimal import Decimal, ROUND_CEILING
import html
import re
from markupsafe import Markup

from fastapi import FastAPI, Request, Depends, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session
from sqlalchemy import select, delete, func, inspect, text
from jinja2 import Environment, FileSystemLoader, select_autoescape

from .database import Base, engine, get_db
from .models import Poll, Option, Participant, Response

BASE_DIR = Path(__file__).resolve().parent
TEMPLATES = Environment(
    loader=FileSystemLoader(BASE_DIR / "templates"),
    autoescape=select_autoescape(["html", "xml"])
)

app = FastAPI(title="Group Planner")
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")

APP_VERSION = "v29"

@app.get("/health")
def health():
    return {"status": "ok", "version": APP_VERSION}

Base.metadata.create_all(bind=engine)

# Lightweight SQLite migration for databases created before v13.
def migrate_database():
    if engine.url.get_backend_name() != "sqlite":
        return
    inspector = inspect(engine)
    if "participants" not in inspector.get_table_names():
        return
    columns = {c["name"] for c in inspector.get_columns("participants")}
    with engine.begin() as conn:
        if "email" not in columns:
            conn.execute(text("ALTER TABLE participants ADD COLUMN email VARCHAR(320) DEFAULT ''"))
        if "saved_at" not in columns:
            conn.execute(text("ALTER TABLE participants ADD COLUMN saved_at DATETIME"))
            conn.execute(text(
                "UPDATE participants SET saved_at = CURRENT_TIMESTAMP "
                "WHERE saved_at IS NULL AND id IN (SELECT DISTINCT participant_id FROM responses)"
            ))
        # v19: remove the retired "maybe" answer from older availability responses.
        conn.execute(text("UPDATE responses SET answer = 'no' WHERE answer = 'maybe'"))
        poll_columns = {c["name"] for c in inspect(engine).get_columns("polls")}
        if "locked" not in poll_columns:
            conn.execute(text("ALTER TABLE polls ADD COLUMN locked BOOLEAN DEFAULT 0 NOT NULL"))
        if "booking_option_id" not in poll_columns:
            conn.execute(text("ALTER TABLE polls ADD COLUMN booking_option_id INTEGER"))

migrate_database()


def description_html(value: str | None):
    """Render a small, safe description markup subset (links, line breaks, emphasis)."""
    if not value:
        return Markup("")
    source = html.escape(value, quote=True)

    # Markdown-style links; only allow safe web URL schemes.
    link_pattern = re.compile(r"\[([^\]]+)\]\((https?://[^\s)]+)\)")
    def make_link(match):
        label, url = match.group(1), match.group(2)
        return f'<a href="{url}" target="_blank" rel="noopener noreferrer">{label}</a>'
    source = link_pattern.sub(make_link, source)

    # Plain web addresses become clickable links too.
    url_pattern = re.compile(r'(?<!["=])(https?://[^\s<]+)')
    source = url_pattern.sub(lambda m: f'<a href="{m.group(1)}" target="_blank" rel="noopener noreferrer">{m.group(1)}</a>', source)

    # Basic formatting: **bold**, *italic*, and `inline code`.
    source = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', source)
    source = re.sub(r'(?<!\*)\*([^*\n]+)\*(?!\*)', r'<em>\1</em>', source)
    source = re.sub(r'`([^`\n]+)`', r'<code>\1</code>', source)
    return Markup(source.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "<br>"))


def render(template_name: str, **context):
    context.setdefault("app_version", APP_VERSION)
    context.setdefault("format_date_sv", format_date_sv)
    context.setdefault("format_date_iso", format_date_iso)
    context.setdefault("format_weekday_sv", format_weekday_sv)
    context.setdefault("format_hotel_night_sv", format_hotel_night_sv)
    context.setdefault("format_hotel_night_weekdays_sv", format_hotel_night_weekdays_sv)
    context.setdefault("format_hotel_night_compact_sv", format_hotel_night_compact_sv)
    context.setdefault("description_html", description_html)
    template = TEMPLATES.get_template(template_name)
    return HTMLResponse(template.render(**context))


def new_token():
    return token_urlsafe(24)


def money(value):
    if value is None:
        return None
    return float(Decimal(str(value)).quantize(Decimal("1"), rounding=ROUND_CEILING))


def normalize_name(name: str) -> str:
    return " ".join(name.strip().split())


def normalize_email(email: str) -> str:
    return email.strip().casefold()


def valid_email(email: str) -> bool:
    # Deliberately allow internationalised addresses such as å@exempel.ä.se.
    return bool(email) and "@" in email and " " not in email and email.count("@") == 1


WEEKDAYS_SV = ["Mån", "Tis", "Ons", "Tor", "Fre", "Lör", "Sön"]
MONTHS_SV = [
    "jan", "feb", "mar", "apr", "maj", "jun",
    "jul", "aug", "sep", "okt", "nov", "dec",
]

def format_date_sv(value: str) -> str:
    from datetime import date
    d = date.fromisoformat(value)
    return f"{WEEKDAYS_SV[d.weekday()]} {d.day} {MONTHS_SV[d.month - 1]} {d.year}"


def format_date_iso(value: str) -> str:
    return value


def format_weekday_sv(value: str) -> str:
    from datetime import date
    d = date.fromisoformat(value)
    return WEEKDAYS_SV[d.weekday()]


def format_hotel_night_weekdays_sv(value: str) -> str:
    """Weekday range for a hotel night, e.g. Lör–Sön."""
    from datetime import date, timedelta
    start = date.fromisoformat(value)
    end = start + timedelta(days=1)
    return f"{WEEKDAYS_SV[start.weekday()]}–{WEEKDAYS_SV[end.weekday()]}"


def format_hotel_night_compact_sv(value: str) -> str:
    """Compact numeric check-in–check-out range, e.g. 1/5–2/5."""
    from datetime import date, timedelta
    start = date.fromisoformat(value)
    end = start + timedelta(days=1)
    return f"{start.day}/{start.month}–{end.day}/{end.month}"


def format_hotel_night_sv(value: str) -> str:
    """Combined hotel-night label for calendar cards."""
    return f"{format_hotel_night_weekdays_sv(value)} {format_hotel_night_compact_sv(value)}"


def get_participant(db: Session, token: str):
    return db.scalar(select(Participant).where(Participant.token == token))


def find_participant_by_email(db: Session, poll_id: int, email: str):
    normalized = normalize_email(email)
    if not normalized:
        return None
    return db.scalar(
        select(Participant).where(
            Participant.poll_id == poll_id,
            Participant.email == normalized,
        )
    )


def get_poll(db: Session, token: str):
    participant = get_participant(db, token)
    if participant is not None:
        poll = db.get(Poll, participant.poll_id)
        if poll is not None:
            return poll

    poll = db.scalar(select(Poll).where(Poll.participant_token == token))
    if poll is None:
        poll = db.scalar(select(Poll).where(Poll.admin_token == token))
    if not poll:
        raise HTTPException(404, "Poll not found")
    return poll


def admin_password():
    return os.getenv("ADMIN_PASSWORD", "").strip()


def make_admin_cookie_value():
    password = admin_password()
    if not password:
        return ""
    signature = hmac.new(password.encode(), b"group-planner-admin", hashlib.sha256).hexdigest()
    return signature


def is_admin_session(request: Request) -> bool:
    expected = make_admin_cookie_value()
    return bool(expected) and hmac.compare_digest(
        request.cookies.get("group_planner_admin", ""), expected
    )


def require_admin(request: Request):
    if not is_admin_session(request):
        raise HTTPException(403, "Admin login required")


def get_admin_poll(db: Session, token: str):
    poll = db.scalar(select(Poll).where(Poll.admin_token == token))
    if not poll:
        raise HTTPException(404, "Invalid admin link")
    return poll


def set_admin_cookie(response: RedirectResponse | HTMLResponse):
    response.set_cookie(
        "group_planner_admin",
        make_admin_cookie_value(),
        httponly=True,
        samesite="lax",
        secure=False,
    )
    return response


def get_admin_session_poll(db: Session, request: Request):
    require_admin(request)
    return None


def participant_url(request: Request, token: str) -> str:
    base = os.getenv("PUBLIC_BASE_URL", "").strip().rstrip("/")
    if not base:
        base = str(request.base_url).rstrip("/")
        base = base.replace("://0.0.0.0", "://localhost", 1)
    return f"{base}/p/{token}"


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return render("index.html", request=request, is_admin=is_admin_session(request))


@app.get("/admin/login", response_class=HTMLResponse)
def admin_login(request: Request):
    if not admin_password():
        raise HTTPException(500, "ADMIN_PASSWORD is not configured")
    if is_admin_session(request):
        return RedirectResponse("/admin", status_code=303)
    return render("admin_login.html", request=request, is_admin=False)


@app.post("/admin/login")
def admin_login_post(request: Request, password: str = Form(...)):
    configured = admin_password()
    if not configured or not hmac.compare_digest(password, configured):
        return render("admin_login.html", request=request, is_admin=False, error="Fel lösenord")
    response = RedirectResponse("/admin", status_code=303)
    return set_admin_cookie(response)


@app.get("/admin/login/{token}")
def admin_login_by_token(token: str, db: Session = Depends(get_db)):
    get_admin_poll(db, token)
    response = RedirectResponse("/admin", status_code=303)
    return set_admin_cookie(response)


@app.get("/admin/new", response_class=HTMLResponse)
def new_poll(request: Request, db: Session = Depends(get_db)):
    if not is_admin_session(request):
        return RedirectResponse("/admin/login", status_code=303)
    return render("new_poll.html", request=request, is_admin=True)


@app.post("/admin/new")
def create_poll(
    request: Request,
    title: str = Form(...),
    description: str = Form(""),
    poll_type: str = Form(...),
    dates: list[str] = Form([]),
    hotel_start_date: str = Form(""),
    hotel_end_date: str = Form(""),
    start_times: list[str] = Form([]),
    end_times: list[str] = Form([]),
    total_cost: str = Form(""),
    cost_mode: str = Form("per_night"),
    db: Session = Depends(get_db),
):
    get_admin_session_poll(db, request)
    if poll_type not in {"availability", "hotel"}:
        raise HTTPException(400, "Invalid poll type")

    if poll_type == "hotel":
        if not hotel_start_date.strip() or not hotel_end_date.strip():
            raise HTTPException(400, "Start- och slutdatum krävs för hotell/resa")
        if hotel_end_date <= hotel_start_date:
            raise HTTPException(400, "Slutdatum måste vara efter startdatum")
        from datetime import date, timedelta
        start = date.fromisoformat(hotel_start_date)
        end = date.fromisoformat(hotel_end_date)
        clean_dates = [(start + timedelta(days=i)).isoformat() for i in range((end - start).days)]
    else:
        clean_dates = [d.strip() for d in dates if d.strip()]

    if not clean_dates:
        raise HTTPException(400, "At least one date/option is required")

    poll = Poll(
        title=title.strip(),
        description=description.strip(),
        poll_type=poll_type,
        admin_token=new_token(),
        participant_token=new_token(),
        total_cost=float(total_cost) if total_cost.strip() else None,
        cost_mode=cost_mode if cost_mode in {"per_night", "equal_person"} else "per_night",
    )
    db.add(poll)
    db.flush()

    for i, option_date in enumerate(clean_dates):
        st = start_times[i] if i < len(start_times) and start_times[i] else None
        et = end_times[i] if i < len(end_times) and end_times[i] else None
        db.add(Option(poll_id=poll.id, date=option_date, start_time=st, end_time=et))

    db.commit()
    return RedirectResponse(f"/admin/{poll.admin_token}", status_code=303)


@app.get("/admin", response_class=HTMLResponse)
def admin_list(request: Request, db: Session = Depends(get_db)):
    if not is_admin_session(request):
        return RedirectResponse("/admin/login", status_code=303)
    polls = list(db.scalars(select(Poll).order_by(Poll.created_at.desc())))
    participant_counts = {}
    for poll in polls:
        participant_counts[poll.id] = db.scalar(
            select(func.count(Participant.id)).where(Participant.poll_id == poll.id)
        ) or 0
    return render(
        "admin_list.html",
        request=request,
        is_admin=True,
        polls=polls,
        participant_counts=participant_counts,
    )


@app.post("/admin/{token}/delete")
def delete_poll(request: Request, token: str, db: Session = Depends(get_db)):
    require_admin(request)
    poll = get_admin_poll(db, token)
    db.delete(poll)
    db.commit()
    return RedirectResponse("/admin", status_code=303)


@app.get("/admin/{token}", response_class=HTMLResponse)
def admin_view(request: Request, token: str, db: Session = Depends(get_db)):
    if not is_admin_session(request):
        get_admin_poll(db, token)
    poll = get_admin_poll(db, token)
    options = list(db.scalars(select(Option).where(Option.poll_id == poll.id).order_by(Option.date, Option.start_time)))
    participants = list(db.scalars(select(Participant).where(Participant.poll_id == poll.id).order_by(Participant.name)))

    response_map = {}
    for p in participants:
        for r in p.responses:
            response_map[(p.id, r.option_id)] = r.answer

    counts = defaultdict(int)
    selected_person_nights = 0
    participant_costs = {}
    for p in participants:
        selected = sum(1 for o in options if response_map.get((p.id, o.id)) in {"selected", "yes"})
        participant_costs[p.id] = {"nights": selected, "cost": None}
        selected_person_nights += selected

    if poll.total_cost is not None:
        if poll.cost_mode == "per_night" and selected_person_nights:
            rate = poll.total_cost / selected_person_nights
            for p in participants:
                participant_costs[p.id]["cost"] = money(participant_costs[p.id]["nights"] * rate)
        elif poll.cost_mode == "equal_person" and participants:
            share = poll.total_cost / len(participants)
            for p in participants:
                participant_costs[p.id]["cost"] = money(share)

    for r in db.scalars(select(Response).join(Participant).where(Participant.poll_id == poll.id)):
        if r.answer in {"selected", "yes"}:
            counts[r.option_id] += 1

    per_night_rate = (
        money(poll.total_cost / selected_person_nights)
        if poll.total_cost is not None and selected_person_nights and poll.cost_mode == "per_night"
        else None
    )

    response = render(
        "admin.html",
        request=request,
        is_admin=True,
        participant_url=participant_url(request, poll.participant_token),
        poll=poll,
        options=options,
        participants=participants,
        response_map=response_map,
        counts=counts,
        participant_costs=participant_costs,
        selected_person_nights=selected_person_nights,
        per_night_rate=per_night_rate,
    )
    set_admin_cookie(response)
    return response


@app.get("/mine", response_class=HTMLResponse)
def mine(request: Request):
    return render("mine.html", request=request, is_admin=False, error=None, participants=[], email="")


@app.post("/mine", response_class=HTMLResponse)
def mine_post(request: Request, email: str = Form(...), db: Session = Depends(get_db)):
    normalized = normalize_email(email)
    if not valid_email(normalized):
        return render("mine.html", request=request, is_admin=False, error="Ange en giltig e-postadress.", participants=[], mine_items=[], email=email)

    participants = list(db.scalars(
        select(Participant).where(Participant.email == normalized).order_by(Participant.id.desc())
    ))

    mine_items = []
    for participant in participants:
        poll = participant.poll
        options = list(db.scalars(
            select(Option).where(Option.poll_id == poll.id).order_by(Option.date, Option.start_time)
        ))
        response_map = {r.option_id: r.answer for r in participant.responses}
        result_participants = []
        result_response_map = {}
        if poll.poll_type == "availability":
            result_participants = list(db.scalars(
                select(Participant).where(Participant.poll_id == poll.id).order_by(Participant.name, Participant.id)
            ))
            for r in db.scalars(
                select(Response).join(Participant).where(Participant.poll_id == poll.id)
            ):
                result_response_map[(r.participant_id, r.option_id)] = r.answer
        mine_items.append({
            "participant": participant,
            "poll": poll,
            "options": options,
            "response_map": response_map,
            "result_participants": result_participants,
            "result_response_map": result_response_map,
        })

    return render("mine.html", request=request, is_admin=False, error=None,
                  participants=participants, mine_items=mine_items, email=normalized)


@app.get("/p/{token}", response_class=HTMLResponse)
def participant_view(request: Request, token: str, db: Session = Depends(get_db)):
    participant = get_participant(db, token)
    if participant is not None:
        poll = db.get(Poll, participant.poll_id)
    else:
        poll = db.scalar(select(Poll).where(Poll.participant_token == token))
    if poll is None:
        raise HTTPException(404, "Poll not found")
    if poll.locked and request.query_params.get("edit") == "1":
        return RedirectResponse(f"/p/{token}", status_code=303)

    options = list(db.scalars(select(Option).where(Option.poll_id == poll.id).order_by(Option.date, Option.start_time)))
    existing = {r.option_id: r.answer for r in participant.responses} if participant else {}
    has_saved = participant is not None and participant.saved_at is not None
    editing = (not poll.locked) and (participant is None or request.query_params.get("edit") == "1" or not has_saved)

    all_participants = []
    all_response_map = {}
    hotel_counts = {}
    if participant is not None:
        all_participants = list(db.scalars(
            select(Participant).where(Participant.poll_id == poll.id).order_by(Participant.name, Participant.id)
        ))
        for r in db.scalars(select(Response).join(Participant).where(Participant.poll_id == poll.id)):
            all_response_map[(r.participant_id, r.option_id)] = r.answer
        if poll.poll_type == "hotel":
            for o in options:
                hotel_counts[o.id] = sum(
                    1 for p in all_participants
                    if all_response_map.get((p.id, o.id)) in {"selected", "yes"}
                )

    participant_costs = {}
    selected_person_nights = sum(hotel_counts.values()) if poll.poll_type == "hotel" else 0
    per_night_rate = (
        money(poll.total_cost / selected_person_nights)
        if poll.poll_type == "hotel" and poll.total_cost is not None and selected_person_nights
        else None
    )
    if poll.poll_type == "hotel":
        for p in all_participants:
            nights = sum(1 for o in options if all_response_map.get((p.id, o.id)) in {"selected", "yes"})
            cost = None
            if poll.total_cost is not None:
                if poll.cost_mode == "per_night" and selected_person_nights:
                    cost = money(nights * poll.total_cost / selected_person_nights)
                elif poll.cost_mode == "equal_person" and all_participants:
                    cost = money(poll.total_cost / len(all_participants))
            participant_costs[p.id] = {"nights": nights, "cost": cost}

    my_cost = participant_costs.get(participant.id, {}).get("cost") if participant else None
    my_nights = participant_costs.get(participant.id, {}).get("nights", 0) if participant else 0

    return render("participant.html", request=request, is_admin=False, poll=poll, options=options,
                  participant=participant, existing=existing,
                  participant_token=participant.token if participant else token,
                  all_participants=all_participants, all_response_map=all_response_map,
                  hotel_counts=hotel_counts, has_saved=has_saved, editing=editing,
                  selected_person_nights=selected_person_nights, per_night_rate=per_night_rate,
                  participant_costs=participant_costs, my_cost=my_cost, my_nights=my_nights)


@app.post("/p/{token}")
async def submit_participant(
    token: str,
    request: Request,
    name: str = Form(...),
    email: str = Form(...),
    db: Session = Depends(get_db),
):
    poll = get_poll(db, token)
    submitted_name = normalize_name(name)
    submitted_email = normalize_email(email)
    if not submitted_name:
        raise HTTPException(400, "Name is required")
    if not valid_email(submitted_email):
        raise HTTPException(400, "A valid email address is required")

    participant = get_participant(db, token)
    if poll.locked:
        raise HTTPException(403, "Omröstningen är låst för ändringar.")

    if participant is None:
        # Shared invitation: email identifies an existing participant in this poll.
        participant = find_participant_by_email(db, poll.id, submitted_email)
        if participant is None:
            participant = Participant(
                poll_id=poll.id,
                name=submitted_name,
                email=submitted_email,
                token=new_token(),
            )
            db.add(participant)
            db.flush()
        else:
            participant.name = submitted_name
    else:
        # Personal token: only that participant can be edited. Keep email as identity.
        other = find_participant_by_email(db, poll.id, submitted_email)
        if other is not None and other.id != participant.id:
            raise HTTPException(409, "E-postadressen används redan i den här omröstningen.")
        participant.name = submitted_name
        participant.email = submitted_email
        db.execute(delete(Response).where(Response.participant_id == participant.id))

    # If an existing participant was found through the shared link, replace their answers.
    if participant.id is not None:
        db.execute(delete(Response).where(Response.participant_id == participant.id))

    parsed = await request.form()
    for option in poll.options:
        value = parsed.get(f"answer_{option.id}")
        if value in {"yes", "no", "selected"}:
            db.add(Response(
                participant_id=participant.id,
                option_id=option.id,
                answer=value,
            ))

    from datetime import datetime
    participant.saved_at = datetime.utcnow()
    db.commit()
    return RedirectResponse(f"/p/{participant.token}?saved=1", status_code=303)


@app.post("/admin/{token}/details")
def update_poll_details(
    request: Request,
    token: str,
    title: str = Form(...),
    description: str = Form(""),
    db: Session = Depends(get_db),
):
    require_admin(request)
    poll = get_admin_poll(db, token)
    title = title.strip()
    if not title:
        raise HTTPException(400, "Titel krävs")
    poll.title = title
    poll.description = description.strip()
    db.commit()
    return RedirectResponse(f"/admin/{token}", status_code=303)


@app.post("/admin/{token}/lock")
def toggle_poll_lock(
    request: Request,
    token: str,
    locked: str = Form("1"),
    booking_option_id: int | None = Form(None),
    db: Session = Depends(get_db),
):
    require_admin(request)
    poll = get_admin_poll(db, token)

    should_lock = locked == "1"
    if should_lock and poll.poll_type == "availability":
        if booking_option_id is None:
            raise HTTPException(400, "Välj vilken tid som ska bokas innan du låser omröstningen.")
        option = db.scalar(
            select(Option).where(Option.id == booking_option_id, Option.poll_id == poll.id)
        )
        if option is None:
            raise HTTPException(400, "Ogiltigt bokningsalternativ.")
        poll.booking_option_id = option.id
    elif not should_lock:
        # Keep the selected booking option when unlocking, so the admin can lock again
        # without having to make the same selection again.
        pass

    poll.locked = should_lock
    db.commit()
    return RedirectResponse(f"/admin/{token}", status_code=303)


@app.post("/admin/{token}/cost")
def update_cost(
    request: Request,
    token: str,
    total_cost: str = Form(""),
    cost_mode: str = Form("per_night"),
    db: Session = Depends(get_db),
):
    require_admin(request)
    poll = get_admin_poll(db, token)
    poll.total_cost = float(total_cost) if total_cost.strip() else None
    poll.cost_mode = cost_mode if cost_mode in {"per_night", "equal_person"} else "per_night"
    db.commit()
    return RedirectResponse(f"/admin/{token}", status_code=303)
