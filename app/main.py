from pathlib import Path
import os
from secrets import token_urlsafe
import hmac
import hashlib
from collections import defaultdict
from decimal import Decimal, ROUND_HALF_UP

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
@app.middleware("http")
async def add_version_to_request(request: Request, call_next):
    request.state.app_version = APP_VERSION
    return await call_next(request)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")

APP_VERSION = "v14"

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
    if "email" not in columns:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE participants ADD COLUMN email VARCHAR(320) DEFAULT ''"))

migrate_database()


def render(template_name: str, **context):
    context["app_version"] = APP_VERSION
    template = TEMPLATES.get_template(template_name)
    return HTMLResponse(template.render(**context))


def new_token():
    return token_urlsafe(24)


def money(value):
    if value is None:
        return None
    return float(Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def normalize_name(name: str) -> str:
    return " ".join(name.strip().split())


def normalize_email(email: str) -> str:
    return email.strip().casefold()


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
        app_version=APP_VERSION,
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
    if not normalized or "@" not in normalized:
        return render("mine.html", request=request, is_admin=False, error="Ange en giltig e-postadress.", participants=[], email=email)

    participants = list(
        db.scalars(
            select(Participant)
            .where(Participant.email == normalized)
            .order_by(Participant.id.desc())
        )
    )
    return render("mine.html", request=request, is_admin=False, error=None, participants=participants, email=normalized)


@app.get("/p/{token}", response_class=HTMLResponse)
def participant_view(request: Request, token: str, db: Session = Depends(get_db)):
    participant = get_participant(db, token)
    if participant is not None:
        poll = db.get(Poll, participant.poll_id)
    else:
        poll = db.scalar(select(Poll).where(Poll.participant_token == token))

    if poll is None:
        raise HTTPException(404, "Poll not found")

    options = list(db.scalars(select(Option).where(Option.poll_id == poll.id).order_by(Option.date, Option.start_time)))
    existing = {r.option_id: r.answer for r in participant.responses} if participant else {}

    return render(
        "participant.html",
        request=request,
        is_admin=False,
        poll=poll,
        options=options,
        participant=participant,
        existing=existing,
        participant_token=participant.token if participant else token,
    )


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
    if not submitted_email or "@" not in submitted_email:
        raise HTTPException(400, "A valid email address is required")

    participant = get_participant(db, token)

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
        if value in {"yes", "no", "maybe", "selected"}:
            db.add(Response(
                participant_id=participant.id,
                option_id=option.id,
                answer=value,
            ))

    db.commit()
    return RedirectResponse(f"/p/{participant.token}?saved=1", status_code=303)


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
