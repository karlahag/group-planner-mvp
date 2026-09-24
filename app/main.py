from pathlib import Path
import os
from secrets import token_urlsafe
from collections import defaultdict
from decimal import Decimal, ROUND_HALF_UP

from fastapi import FastAPI, Request, Depends, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session
from sqlalchemy import select, delete
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

APP_VERSION = "v6"

@app.get("/health")
def health():
    return {"status": "ok", "version": APP_VERSION}

Base.metadata.create_all(bind=engine)


def render(template_name: str, **context):
    template = TEMPLATES.get_template(template_name)
    return HTMLResponse(template.render(**context))


def new_token():
    return token_urlsafe(24)


def money(value):
    if value is None:
        return None
    return float(Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def get_poll(db: Session, token: str):
    poll = db.scalar(select(Poll).where(Poll.participant_token == token))
    if poll is None:
        poll = db.scalar(select(Poll).where(Poll.admin_token == token))
    if not poll:
        raise HTTPException(404, "Poll not found")
    return poll


def get_admin_poll(db: Session, token: str):
    poll = db.scalar(select(Poll).where(Poll.admin_token == token))
    if not poll:
        raise HTTPException(404, "Invalid admin link")
    return poll


def get_participant(db: Session, token: str):
    return db.scalar(select(Participant).where(Participant.token == token))


def participant_url(request: Request, token: str) -> str:
    base = os.getenv("PUBLIC_BASE_URL", "").strip().rstrip("/")
    if not base:
        base = str(request.base_url).rstrip("/")
        base = base.replace("://0.0.0.0", "://localhost", 1)
    return f"{base}/p/{token}"


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return render("index.html")


@app.get("/admin/new", response_class=HTMLResponse)
def new_poll():
    return render("new_poll.html")


@app.post("/admin/new")
def create_poll(
    title: str = Form(...),
    description: str = Form(""),
    poll_type: str = Form(...),
    dates: list[str] = Form([]),
    start_times: list[str] = Form([]),
    end_times: list[str] = Form([]),
    total_cost: str = Form(""),
    cost_mode: str = Form("per_night"),
    db: Session = Depends(get_db),
):
    if poll_type not in {"availability", "hotel"}:
        raise HTTPException(400, "Invalid poll type")

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

    for i, date in enumerate(clean_dates):
        st = start_times[i] if i < len(start_times) and start_times[i] else None
        et = end_times[i] if i < len(end_times) and end_times[i] else None
        db.add(Option(poll_id=poll.id, date=date, start_time=st, end_time=et))

    db.commit()
    return RedirectResponse(f"/admin/{poll.admin_token}", status_code=303)


@app.get("/admin/{token}", response_class=HTMLResponse)
def admin_view(request: Request, token: str, db: Session = Depends(get_db)):
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

    for r in db.scalars(
        select(Response).join(Participant).where(Participant.poll_id == poll.id)
    ):
        if r.answer in {"selected", "yes"}:
            counts[r.option_id] += 1

    per_night_rate = (
        money(poll.total_cost / selected_person_nights)
        if poll.total_cost is not None and selected_person_nights and poll.cost_mode == "per_night"
        else None
    )

    return render(
        "admin.html",
        request=request,
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


@app.get("/p/{token}", response_class=HTMLResponse)
def participant_view(request: Request, token: str, db: Session = Depends(get_db)):
    poll = get_poll(db, token)
    participant = get_participant(db, token)
    options = list(db.scalars(select(Option).where(Option.poll_id == poll.id).order_by(Option.date, Option.start_time)))

    existing = {}
    if participant:
        existing = {r.option_id: r.answer for r in participant.responses}

    return render(
        "participant.html",
        request=request,
        poll=poll,
        options=options,
        participant=participant,
        existing=existing,
        participant_token=token,
    )


@app.post("/p/{token}")
async def submit_participant(
    token: str,
    request: Request,
    name: str = Form(...),
    db: Session = Depends(get_db),
):
    poll = get_poll(db, token)

    participant = get_participant(db, token)
    if participant is None:
        participant = Participant(
            poll_id=poll.id,
            name=name.strip(),
            token=new_token(),
        )
        db.add(participant)
        db.flush()
    else:
        participant.name = name.strip()
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
    token: str,
    total_cost: str = Form(""),
    cost_mode: str = Form("per_night"),
    db: Session = Depends(get_db),
):
    poll = get_admin_poll(db, token)
    poll.total_cost = float(total_cost) if total_cost.strip() else None
    poll.cost_mode = cost_mode if cost_mode in {"per_night", "equal_person"} else "per_night"
    db.commit()
    return RedirectResponse(f"/admin/{token}", status_code=303)
